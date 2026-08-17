#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PILOT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
ENV_FILE="${PILOT_K8S_ENV_FILE:-$PILOT_ROOT/configs/k8s.env}"

if [[ -f "$ENV_FILE" ]]; then
  set -a
  # shellcheck disable=SC1090
  source "$ENV_FILE"
  set +a
fi

required_variables=(
  PILOT_K8S_NAMESPACE
  PILOT_K8S_BUILD_NODE
  PILOT_NFS_SERVER
  PILOT_NFS_ROOT
  PILOT_REGISTRY
)
for variable_name in "${required_variables[@]}"; do
  [[ -n "${!variable_name:-}" ]] || {
    printf '쿠버네티스 설정에 %s 값이 필요합니다: %s\n' \
      "$variable_name" "$ENV_FILE" >&2
    exit 1
  }
done

PROBE_IMAGE="$PILOT_REGISTRY/hami-tail-bert:mlperf-v5.1.1-hami-v2.9.0"
REGISTRY_SECRET="${PILOT_REGISTRY_SECRET:-}"
BERT_INPUT_DIR="${PILOT_BERT_INPUT_DIR:-$PILOT_NFS_ROOT/johyeon/hami-tail-pilot/inputs/bert}"
TARGET_QPS=1
WARMUP_SECONDS=60
MEASUREMENT_SECONDS=30
SM_LIMIT=100

print_plan() {
  printf '%s\n' \
    '{' \
    "  \"image\": \"$PROBE_IMAGE\"," \
    "  \"measurement_seconds\": $MEASUREMENT_SECONDS," \
    "  \"node\": \"$PILOT_K8S_BUILD_NODE\"," \
    "  \"sm_limit\": $SM_LIMIT," \
    "  \"target_qps\": $TARGET_QPS," \
    "  \"warmup_seconds\": $WARMUP_SECONDS" \
    '}'
}

if [[ "${1:-}" == "--print-plan" ]]; then
  print_plan
  exit 0
fi
if [[ $# -ne 0 ]]; then
  printf '사용법: scripts/check_bert_k8s.sh [--print-plan]\n' >&2
  exit 2
fi

case "$PILOT_ROOT" in
  "$PILOT_NFS_ROOT"/*) CONTEXT_RELATIVE="${PILOT_ROOT#"$PILOT_NFS_ROOT"/}" ;;
  *)
    printf '저장소가 공용 저장공간 아래에 있어야 합니다: %s\n' \
      "$PILOT_NFS_ROOT" >&2
    exit 1
    ;;
esac
case "$BERT_INPUT_DIR" in
  "$PILOT_NFS_ROOT"/*) INPUT_RELATIVE="${BERT_INPUT_DIR#"$PILOT_NFS_ROOT"/}" ;;
  *)
    printf 'BERT 입력 경로가 공용 저장공간 아래에 있어야 합니다: %s\n' \
      "$BERT_INPUT_DIR" >&2
    exit 1
    ;;
esac

for name in model.pytorch dev-v1.1.json vocab.txt; do
  [[ -s "$BERT_INPUT_DIR/$name" ]] || {
    printf 'BERT 입력 파일이 없거나 비어 있습니다: %s\n' \
      "$BERT_INPUT_DIR/$name" >&2
    exit 1
  }
done

JOB_NAME="hami-pilot-check-bert"
ATTEMPT_ID="$(date -u +%Y%m%dT%H%M%SZ)"
OUTPUT_RELATIVE="$CONTEXT_RELATIVE/artifacts/k8s-bert-check/$ATTEMPT_ID"
OUTPUT_DIR="$PILOT_NFS_ROOT/$OUTPUT_RELATIVE"
FEATURE_CACHE_DIR="$BERT_INPUT_DIR/cache"
FEATURE_CACHE_RELATIVE="$INPUT_RELATIVE/cache"
FEATURE_CACHE_FILE="$FEATURE_CACHE_DIR/eval_features.pickle"
SUMMARY_FILE="$OUTPUT_DIR/mlperf_log_summary.txt"
PROBE_FILE="$OUTPUT_DIR/hami_probe.jsonl"

mkdir -p "$OUTPUT_DIR" "$FEATURE_CACHE_DIR"
cat > "$OUTPUT_DIR/user.conf" <<EOF
*.Server.target_qps = $TARGET_QPS
*.Server.min_duration = $((MEASUREMENT_SECONDS * 1000))
*.Server.target_duration = $((MEASUREMENT_SECONDS * 1000))
*.Server.min_query_count = 20
EOF

if [[ -s "$FEATURE_CACHE_FILE" ]]; then
  CACHE_IN_POD="/feature-cache/eval_features.pickle"
else
  CACHE_IN_POD="/output/eval_features.pickle"
fi

IMAGE_PULL_SECRETS=""
if [[ -n "$REGISTRY_SECRET" ]]; then
  IMAGE_PULL_SECRETS=$(cat <<YAML
      imagePullSecrets:
        - name: $REGISTRY_SECRET
YAML
)
fi

printf 'BERT 한 작업을 100%% 조건으로 실행합니다.\n'
printf '요청량은 초당 %s건이며, 이번 검사는 성능 비교가 아닙니다.\n' "$TARGET_QPS"

kubectl -n "$PILOT_K8S_NAMESPACE" delete job "$JOB_NAME" --ignore-not-found=true
kubectl apply -f - <<YAML
apiVersion: batch/v1
kind: Job
metadata:
  name: $JOB_NAME
  namespace: $PILOT_K8S_NAMESPACE
spec:
  backoffLimit: 0
  activeDeadlineSeconds: 1200
  template:
    spec:
      restartPolicy: Never
$IMAGE_PULL_SECRETS
      nodeSelector:
        kubernetes.io/hostname: $PILOT_K8S_BUILD_NODE
      containers:
        - name: bert-check
          image: $PROBE_IMAGE
          imagePullPolicy: Always
          command: ["/bin/bash", "-lc"]
          args:
            - |
              ln -sfn "$CACHE_IN_POD" eval_features.pickle
              exec python3 run.py \
                --backend=pytorch \
                --scenario=Server \
                --user_conf=/output/user.conf
          env:
            - name: LD_PRELOAD
              value: /opt/hami/libvgpu.so
            - name: CUDA_DEVICE_SM_LIMIT
              value: "$SM_LIMIT"
            - name: CUDA_DEVICE_MEMORY_SHARED_CACHE
              value: /output/victim.cache
            - name: HAMI_PROBE_OUTPUT
              value: /output/hami_probe.jsonl
            - name: HAMI_WARMUP_SECONDS
              value: "$WARMUP_SECONDS"
            - name: LOG_PATH
              value: /output
            - name: ML_MODEL_FILE_WITH_PATH
              value: /inputs/model.pytorch
            - name: DATASET_FILE
              value: /inputs/dev-v1.1.json
            - name: VOCAB_FILE
              value: /inputs/vocab.txt
          resources:
            limits:
              nvidia.com/gpu: 1
          volumeMounts:
            - name: shared-storage
              mountPath: /inputs
              subPath: $INPUT_RELATIVE
              readOnly: true
            - name: shared-storage
              mountPath: /feature-cache
              subPath: $FEATURE_CACHE_RELATIVE
            - name: shared-storage
              mountPath: /output
              subPath: $OUTPUT_RELATIVE
      volumes:
        - name: shared-storage
          nfs:
            server: $PILOT_NFS_SERVER
            path: $PILOT_NFS_ROOT
YAML

if ! kubectl -n "$PILOT_K8S_NAMESPACE" wait \
  --for=condition=complete "job/$JOB_NAME" --timeout=21m; then
  kubectl -n "$PILOT_K8S_NAMESPACE" logs \
    "job/$JOB_NAME" --all-containers=true || true
  kubectl -n "$PILOT_K8S_NAMESPACE" describe "job/$JOB_NAME" || true
  exit 1
fi

kubectl -n "$PILOT_K8S_NAMESPACE" logs \
  "job/$JOB_NAME" --all-containers=true

[[ -s "$SUMMARY_FILE" ]] || {
  printf 'MLPerf 응답시간 요약 파일이 없습니다: %s\n' \
    "$SUMMARY_FILE" >&2
  exit 1
}
[[ -s "$PROBE_FILE" ]] || {
  printf 'HAMi 확인 파일이 없습니다: %s\n' "$PROBE_FILE" >&2
  exit 1
}

python3 - "$SUMMARY_FILE" "$PROBE_FILE" <<'PY'
import json
from pathlib import Path
import re
import sys

summary_path = Path(sys.argv[1])
probe_path = Path(sys.argv[2])
summary = summary_path.read_text(encoding="utf-8")

def extract(pattern, label, cast=float):
    match = re.search(pattern, summary, flags=re.MULTILINE)
    if match is None:
        raise SystemExit(f"MLPerf 기록에 {label}이(가) 없습니다")
    return cast(match.group(1))

validity = extract(r"^Result is\s*:\s*(\S+)\s*$", "유효성", str)
completed = extract(r"^Completed samples\s*:\s*(\d+)\s*$", "완료 요청 수", int)
throughput = extract(
    r"^Completed samples per second\s*:\s*([0-9.eE+-]+)\s*$",
    "초당 완료 요청 수",
)
p50_ns = extract(
    r"^50\.00 percentile latency \(ns\)\s*:\s*([0-9.eE+-]+)\s*$",
    "가운데 응답시간",
)
p99_ns = extract(
    r"^99\.00 percentile latency \(ns\)\s*:\s*([0-9.eE+-]+)\s*$",
    "99% 완료 응답시간 경계",
)

if validity != "VALID":
    raise SystemExit(f"MLPerf 결과가 유효하지 않습니다: {validity}")
if completed <= 0 or throughput <= 0:
    raise SystemExit("완료된 BERT 요청이 없습니다")

records = [
    json.loads(line)
    for line in probe_path.read_text(encoding="utf-8").splitlines()
    if line.strip()
]
if not records:
    raise SystemExit("HAMi 확인 기록이 비어 있습니다")
limiter_calls = sum(int(record["limiter_calls"]) for record in records)
waited_calls = sum(int(record["waited_calls"]) for record in records)
wait_ns = sum(int(record["wait_ns"]) for record in records)
if limiter_calls <= 0:
    raise SystemExit("HAMi가 BERT GPU 작업을 확인한 기록이 없습니다")
if waited_calls != 0 or wait_ns != 0:
    raise SystemExit("100% 작업에서 HAMi 사용량 제한으로 인한 대기가 발생했습니다")

print("완료된 BERT 요청 수:", completed)
print("초당 완료 요청 수:", round(throughput, 3))
print("가운데 응답시간(ms):", round(p50_ns / 1_000_000, 3))
print("전체 요청의 99%가 완료되는 응답시간 경계(ms):", round(p99_ns / 1_000_000, 3))
print("HAMi 제한 기능 확인 횟수:", limiter_calls)
print("HAMi 사용량 제한으로 인한 실행 대기 횟수:", waited_calls)
print("BERT 한 작업 실행 확인 완료")
PY

if [[ ! -s "$FEATURE_CACHE_FILE" && -s "$OUTPUT_DIR/eval_features.pickle" ]]; then
  mv "$OUTPUT_DIR/eval_features.pickle" "$FEATURE_CACHE_FILE"
  printf 'BERT 자료 변환 결과를 다음 실행용으로 보존했습니다.\n'
fi

printf '확인 결과 위치: %s\n' "$OUTPUT_DIR"
