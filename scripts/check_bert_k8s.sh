#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PILOT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
ENV_FILE="${PILOT_K8S_ENV_FILE:-$PILOT_ROOT/configs/k8s.env}"

# shellcheck source=lib/k8s_job_wait.sh
source "$SCRIPT_DIR/lib/k8s_job_wait.sh"

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
MEASUREMENT_SECONDS=120
HAMI_LOG_LEVEL=2
SM_LIMIT=100
EXPECT_WAIT=false
PRINT_PLAN=false

while [[ $# -gt 0 ]]; do
  case "$1" in
    --limited)
      [[ $# -ge 2 ]] || {
        printf '사용법: scripts/check_bert_k8s.sh [--limited <초당 요청 수>] [--print-plan]\n' >&2
        exit 2
      }
      TARGET_QPS="$2"
      SM_LIMIT=50
      EXPECT_WAIT=true
      shift 2
      ;;
    --full)
      [[ $# -ge 2 ]] || { printf '사용법 오류\n' >&2; exit 2; }
      TARGET_QPS="$2"
      SM_LIMIT=100
      EXPECT_WAIT=false
      shift 2
      ;;
    --print-plan)
      PRINT_PLAN=true
      shift
      ;;
    *)
      printf '사용법: scripts/check_bert_k8s.sh [--limited <초당 요청 수>] [--print-plan]\n' >&2
      exit 2
      ;;
  esac
done

python3 - "$TARGET_QPS" <<'PY'
import math
import sys

try:
    target = float(sys.argv[1])
except ValueError as exc:
    raise SystemExit("초당 요청 수는 숫자여야 합니다") from exc
if not math.isfinite(target) or target <= 0:
    raise SystemExit("초당 요청 수는 0보다 큰 유한한 값이어야 합니다")
PY

print_plan() {
  printf '%s\n' '{'
  if [[ "$EXPECT_WAIT" == true ]]; then
    printf '  "expect_wait": true,\n'
  fi
  printf '%s\n' \
    "  \"hami_log_level\": $HAMI_LOG_LEVEL," \
    "  \"image\": \"$PROBE_IMAGE\"," \
    "  \"measurement_seconds\": $MEASUREMENT_SECONDS," \
    "  \"node\": \"$PILOT_K8S_BUILD_NODE\"," \
    "  \"sm_limit\": $SM_LIMIT," \
    "  \"target_qps\": $TARGET_QPS," \
    "  \"warmup_seconds\": $WARMUP_SECONDS" \
    '}'
}

if [[ "$PRINT_PLAN" == true ]]; then
  print_plan
  exit 0
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
DETAIL_FILE="$OUTPUT_DIR/mlperf_log_detail.txt"
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

UTILIZATION_POLICY=""
if [[ "$EXPECT_WAIT" == true ]]; then
  UTILIZATION_POLICY=$(cat <<'YAML'
            - name: GPU_CORE_UTILIZATION_POLICY
              value: force
YAML
)
fi

printf 'BERT 한 작업을 %s%% 조건으로 실행합니다.\n' "$SM_LIMIT"
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
            - name: LIBCUDA_LOG_LEVEL
              value: "$HAMI_LOG_LEVEL"
            - name: LD_PRELOAD
              value: /opt/hami/libvgpu.so
            - name: CUDA_DEVICE_SM_LIMIT
              value: "$SM_LIMIT"
$UTILIZATION_POLICY
            - name: CUDA_DEVICE_MEMORY_SHARED_CACHE
              value: /hami-cache/victim.cache
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
            - name: hami-cache
              mountPath: /hami-cache
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
        - name: hami-cache
          emptyDir: {}
        - name: shared-storage
          nfs:
            server: $PILOT_NFS_SERVER
            path: $PILOT_NFS_ROOT
YAML

if ! wait_for_k8s_job \
  "$PILOT_K8S_NAMESPACE" "$JOB_NAME" 1260 5; then
  kubectl -n "$PILOT_K8S_NAMESPACE" logs \
    "job/$JOB_NAME" --all-containers=true || true
  kubectl -n "$PILOT_K8S_NAMESPACE" describe "job/$JOB_NAME" || true
  exit 1
fi

kubectl -n "$PILOT_K8S_NAMESPACE" logs \
  "job/$JOB_NAME" --all-containers=true

if [[ ! -s "$FEATURE_CACHE_FILE" && -s "$OUTPUT_DIR/eval_features.pickle" ]]; then
  mv "$OUTPUT_DIR/eval_features.pickle" "$FEATURE_CACHE_FILE"
  printf 'BERT 자료 변환 결과를 다음 실행용으로 보존했습니다.\n'
fi

[[ -s "$SUMMARY_FILE" ]] || {
  printf 'MLPerf 응답시간 요약 파일이 없습니다: %s\n' \
    "$SUMMARY_FILE" >&2
  exit 1
}
[[ -s "$DETAIL_FILE" ]] || {
  printf 'MLPerf 상세 기록 파일이 없습니다: %s\n' "$DETAIL_FILE" >&2
  exit 1
}
[[ -s "$PROBE_FILE" ]] || {
  printf 'HAMi 확인 파일이 없습니다: %s\n' "$PROBE_FILE" >&2
  exit 1
}

PYTHONPATH="$PILOT_ROOT/src${PYTHONPATH:+:$PYTHONPATH}" \
python3 - "$SUMMARY_FILE" "$DETAIL_FILE" "$PROBE_FILE" \
  "$TARGET_QPS" "$EXPECT_WAIT" <<'PY'
from pathlib import Path
import sys

from hami_tail_pilot.mlperf import MLPerfLogError, parse_mlperf_candidate_summary
from hami_tail_pilot.probe import ProbeLogError, parse_probe_jsonl

try:
    metrics = parse_mlperf_candidate_summary(
        Path(sys.argv[1]),
        detail_path=Path(sys.argv[2]),
    )
    probe = parse_probe_jsonl(Path(sys.argv[3]))
except (MLPerfLogError, ProbeLogError) as exc:
    raise SystemExit(str(exc)) from exc

if probe.limiter_calls <= 0:
    raise SystemExit("HAMi가 BERT GPU 작업을 확인한 기록이 없습니다")

target_qps = float(sys.argv[4])
expect_wait = sys.argv[5] == "true"
if metrics.scheduled_samples_per_second is None:
    raise SystemExit("실제로 요청을 보낸 속도 기록이 없습니다")
if (
    metrics.completed_samples_per_second
    < metrics.scheduled_samples_per_second * 0.98
):
    raise SystemExit(
        "이 요청량은 처리 능력보다 높아 요청이 쌓일 가능성이 있습니다: "
        f"보냄={metrics.scheduled_samples_per_second:.3f}, "
        f"완료={metrics.completed_samples_per_second:.3f}"
    )
if expect_wait:
    if probe.waited_calls <= 0 or probe.wait_ns <= 0:
        raise SystemExit(
            "이 요청량에서는 50% 사용 한도에 의한 실행 대기가 기록되지 않았습니다"
        )
elif probe.waited_calls != 0 or probe.wait_ns != 0:
    raise SystemExit("100% 작업에서 HAMi 사용량 제한으로 인한 대기가 발생했습니다")

if metrics.result_validity == "INVALID":
    print("짧은 확인이라 공식 MLPerf 조기 종료 필요 요청 수는 충족하지 않았습니다.")
if metrics.performance_constraints_satisfied is False:
    print("미리 정한 응답시간 목표를 충족하지 못했습니다.")
print("완료된 BERT 요청 수:", metrics.completed_samples)
print("실제로 요청을 보낸 속도:", round(metrics.scheduled_samples_per_second, 3))
print("초당 완료 요청 수:", round(metrics.completed_samples_per_second, 3))
print("가운데 응답시간(ms):", round(metrics.p50_ms, 3))
print("전체 요청의 99%가 완료되는 응답시간 경계(ms):", round(metrics.p99_ms, 3))
print("HAMi 제한 기능 확인 횟수:", probe.limiter_calls)
print("HAMi 사용량 제한으로 인한 실행 대기 횟수:", probe.waited_calls)
print("실행 대기 누적시간(초):", round(probe.wait_ns / 1_000_000_000, 6))
if expect_wait:
    print("50% 작업의 후보 요청량 확인 완료")
else:
    print("BERT 한 작업 실행 확인 완료")
PY

printf '확인 결과 위치: %s\n' "$OUTPUT_DIR"
