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
WARMUP_SECONDS=60
MEASUREMENT_SECONDS=120
NEIGHBOR_MEASUREMENT_SECONDS=240
HAMI_LOG_LEVEL=2
VICTIM_SM_LIMIT=50
VICTIM_WAIT_EXPECTED=true
NEIGHBOR_SM_LIMIT=""
NEIGHBOR_WAIT_EXPECTED=""
TARGET_QPS=""
MODE=""
PRINT_PLAN=false

usage() {
  printf '%s\n' \
    '사용법:' \
    '  scripts/check_bert_neighbor_k8s.sh --victim-limited <초당 요청 수> [--print-plan]' \
    '  scripts/check_bert_neighbor_k8s.sh --both-limited <초당 요청 수> [--print-plan]' >&2
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --victim-limited)
      [[ -z "$MODE" && $# -ge 2 ]] || { usage; exit 2; }
      MODE="victim-limited"
      TARGET_QPS="$2"
      NEIGHBOR_SM_LIMIT=100
      NEIGHBOR_WAIT_EXPECTED=false
      shift 2
      ;;
    --both-limited)
      [[ -z "$MODE" && $# -ge 2 ]] || { usage; exit 2; }
      MODE="both-limited"
      TARGET_QPS="$2"
      NEIGHBOR_SM_LIMIT=50
      NEIGHBOR_WAIT_EXPECTED=true
      shift 2
      ;;
    --print-plan)
      PRINT_PLAN=true
      shift
      ;;
    *)
      usage
      exit 2
      ;;
  esac
done

[[ -n "$MODE" && -n "$TARGET_QPS" ]] || { usage; exit 2; }

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
  printf '%s\n' \
    '{' \
    "  \"hami_log_level\": $HAMI_LOG_LEVEL," \
    "  \"image\": \"$PROBE_IMAGE\"," \
    "  \"measurement_seconds\": $MEASUREMENT_SECONDS," \
    "  \"neighbor_measurement_seconds\": $NEIGHBOR_MEASUREMENT_SECONDS," \
    "  \"neighbor_sm_limit\": $NEIGHBOR_SM_LIMIT," \
    "  \"neighbor_wait_expected\": $NEIGHBOR_WAIT_EXPECTED," \
    "  \"node\": \"$PILOT_K8S_BUILD_NODE\"," \
    "  \"target_qps\": $TARGET_QPS," \
    "  \"victim_sm_limit\": $VICTIM_SM_LIMIT," \
    "  \"victim_wait_expected\": $VICTIM_WAIT_EXPECTED," \
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

FEATURE_CACHE_DIR="$BERT_INPUT_DIR/cache"
FEATURE_CACHE_RELATIVE="$INPUT_RELATIVE/cache"
FEATURE_CACHE_FILE="$FEATURE_CACHE_DIR/eval_features.pickle"
[[ -s "$FEATURE_CACHE_FILE" ]] || {
  printf '%s\n' \
    'BERT 자료 변환 결과가 없습니다. 먼저 단독 BERT 확인을 실행하세요:' \
    '  bash scripts/check_bert_k8s.sh --full 1' >&2
  exit 1
}

JOB_NAME="hami-pilot-check-bert-neighbor"
ATTEMPT_ID="$(date -u +%Y%m%dT%H%M%SZ)"
OUTPUT_RELATIVE="$CONTEXT_RELATIVE/artifacts/k8s-bert-neighbor-check/$ATTEMPT_ID"
OUTPUT_DIR="$PILOT_NFS_ROOT/$OUTPUT_RELATIVE"

for role in victim neighbor; do
  mkdir -p "$OUTPUT_DIR/$role"
done
cat > "$OUTPUT_DIR/victim/user.conf" <<EOF
*.Server.target_qps = $TARGET_QPS
*.Server.min_duration = $((MEASUREMENT_SECONDS * 1000))
*.Server.target_duration = $((MEASUREMENT_SECONDS * 1000))
*.Server.min_query_count = 20
EOF
cat > "$OUTPUT_DIR/neighbor/user.conf" <<EOF
*.Server.target_qps = $TARGET_QPS
*.Server.min_duration = $((NEIGHBOR_MEASUREMENT_SECONDS * 1000))
*.Server.target_duration = $((NEIGHBOR_MEASUREMENT_SECONDS * 1000))
*.Server.min_query_count = 20
EOF

cat > "$OUTPUT_DIR/run_both.sh" <<'POD_SCRIPT'
#!/usr/bin/env bash
set -euo pipefail

BERT_DIR=/opt/mlperf/inference/language/bert
NEIGHBOR_PID=""
VICTIM_PID=""

cleanup() {
  for pid in "$VICTIM_PID" "$NEIGHBOR_PID"; do
    if [[ -n "$pid" ]] && kill -0 "$pid" 2>/dev/null; then
      kill "$pid" 2>/dev/null || true
    fi
  done
}
trap cleanup EXIT INT TERM

run_role() {
  local role="$1"
  local sm_limit="$2"
  local ready_file="${3:-}"
  local output="/output/$role"
  local -a role_env=(
    "LIBCUDA_LOG_LEVEL=$HAMI_LOG_LEVEL"
    "LD_PRELOAD=/opt/hami/libvgpu.so"
    "CUDA_DEVICE_SM_LIMIT=$sm_limit"
    "CUDA_DEVICE_MEMORY_SHARED_CACHE=/hami-cache/$role.cache"
    "HAMI_PROBE_OUTPUT=$output/hami_probe.jsonl"
    "HAMI_WARMUP_SECONDS=$WARMUP_SECONDS"
    "LOG_PATH=$output"
    "ML_MODEL_FILE_WITH_PATH=/inputs/model.pytorch"
    "DATASET_FILE=/inputs/dev-v1.1.json"
    "VOCAB_FILE=/inputs/vocab.txt"
  )
  if [[ "$sm_limit" -lt 100 ]]; then
    role_env+=("GPU_CORE_UTILIZATION_POLICY=force")
  fi
  if [[ -n "$ready_file" ]]; then
    role_env+=("HAMI_READY_FILE=$ready_file")
  fi

  (
    cd "$BERT_DIR"
    exec env "${role_env[@]}" python3 run.py \
      --backend=pytorch \
      --scenario=Server \
      --user_conf="$output/user.conf"
  ) >"$output/stdout.log" 2>"$output/stderr.log" &
  ROLE_PID=$!
}

ln -sfn /feature-cache/eval_features.pickle \
  "$BERT_DIR/eval_features.pickle"
rm -f /output/neighbor/ready

run_role neighbor "$NEIGHBOR_SM_LIMIT" /output/neighbor/ready
NEIGHBOR_PID="$ROLE_PID"
printf '이웃 작업 준비를 기다립니다.\n'

ready_deadline=$((SECONDS + 300))
while [[ ! -f /output/neighbor/ready ]]; do
  if ! kill -0 "$NEIGHBOR_PID" 2>/dev/null; then
    wait "$NEIGHBOR_PID" || true
    printf '이웃 작업이 준비되기 전에 종료됐습니다.\n' >&2
    exit 1
  fi
  if (( SECONDS >= ready_deadline )); then
    printf '이웃 작업 준비 시간이 300초를 넘었습니다.\n' >&2
    exit 1
  fi
  sleep 1
done

printf '이웃 작업이 준비되어 측정 대상 작업을 시작합니다.\n'
run_role victim "$VICTIM_SM_LIMIT"
VICTIM_PID="$ROLE_PID"

if ! wait "$VICTIM_PID"; then
  printf '측정 대상 작업이 실패했습니다.\n' >&2
  exit 1
fi
VICTIM_PID=""

if ! kill -0 "$NEIGHBOR_PID" 2>/dev/null; then
  wait "$NEIGHBOR_PID" || true
  printf '측정 대상 작업이 끝나기 전에 이웃 작업이 종료됐습니다.\n' >&2
  exit 1
fi

if ! wait "$NEIGHBOR_PID"; then
  printf '이웃 작업이 실패했습니다.\n' >&2
  exit 1
fi
NEIGHBOR_PID=""
trap - EXIT INT TERM
printf '두 BERT 작업이 모두 완료됐습니다.\n'
POD_SCRIPT
chmod 0755 "$OUTPUT_DIR/run_both.sh"

IMAGE_PULL_SECRETS=""
if [[ -n "$REGISTRY_SECRET" ]]; then
  IMAGE_PULL_SECRETS=$(cat <<YAML
      imagePullSecrets:
        - name: $REGISTRY_SECRET
YAML
)
fi

if [[ "$MODE" == victim-limited ]]; then
  printf '%s\n' \
    '이웃은 100%로 두고 측정 대상만 50%로 제한합니다.' \
    "두 작업에 각각 초당 $TARGET_QPS건을 보냅니다."
else
  printf '%s\n' \
    '측정 대상과 이웃을 모두 50%로 제한합니다.' \
    "두 작업에 각각 초당 $TARGET_QPS건을 보냅니다."
fi

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
        - name: bert-neighbor-check
          image: $PROBE_IMAGE
          imagePullPolicy: Always
          command: ["/bin/bash", "/output/run_both.sh"]
          env:
            - name: HAMI_LOG_LEVEL
              value: "$HAMI_LOG_LEVEL"
            - name: WARMUP_SECONDS
              value: "$WARMUP_SECONDS"
            - name: VICTIM_SM_LIMIT
              value: "$VICTIM_SM_LIMIT"
            - name: NEIGHBOR_SM_LIMIT
              value: "$NEIGHBOR_SM_LIMIT"
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
              readOnly: true
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
  for role in neighbor victim; do
    printf '%s 작업의 마지막 기록:\n' "$role"
    tail -n 80 "$OUTPUT_DIR/$role/stderr.log" 2>/dev/null || true
  done
  kubectl -n "$PILOT_K8S_NAMESPACE" describe "job/$JOB_NAME" || true
  exit 1
fi

kubectl -n "$PILOT_K8S_NAMESPACE" logs \
  "job/$JOB_NAME" --all-containers=true

role_arguments=()
for role in victim neighbor; do
  for name in mlperf_log_summary.txt mlperf_log_detail.txt hami_probe.jsonl; do
    [[ -s "$OUTPUT_DIR/$role/$name" ]] || {
      printf '%s 작업의 결과 파일이 없거나 비어 있습니다: %s\n' \
        "$role" "$OUTPUT_DIR/$role/$name" >&2
      exit 1
    }
  done
  role_arguments+=(
    "$OUTPUT_DIR/$role/mlperf_log_summary.txt"
    "$OUTPUT_DIR/$role/mlperf_log_detail.txt"
    "$OUTPUT_DIR/$role/hami_probe.jsonl"
  )
done

PYTHONPATH="$PILOT_ROOT/src${PYTHONPATH:+:$PYTHONPATH}" \
python3 - "${role_arguments[@]}" "$TARGET_QPS" \
  "$VICTIM_WAIT_EXPECTED" "$NEIGHBOR_WAIT_EXPECTED" <<'PY'
from pathlib import Path
import sys

from hami_tail_pilot.mlperf import MLPerfLogError, parse_mlperf_candidate_summary
from hami_tail_pilot.probe import ProbeLogError, parse_probe_jsonl

roles = ("측정 대상", "이웃")
role_files = {
    role: tuple(Path(value) for value in sys.argv[1 + index * 3 : 4 + index * 3])
    for index, role in enumerate(roles)
}
target_qps = float(sys.argv[7])
wait_expected = {
    "측정 대상": sys.argv[8] == "true",
    "이웃": sys.argv[9] == "true",
}

for role in roles:
    summary, detail, probe_path = role_files[role]
    try:
        metrics = parse_mlperf_candidate_summary(summary, detail_path=detail)
        probe = parse_probe_jsonl(probe_path)
    except (MLPerfLogError, ProbeLogError) as exc:
        raise SystemExit(f"{role} 작업 기록을 읽지 못했습니다: {exc}") from exc

    scheduled = metrics.scheduled_samples_per_second
    completed = metrics.completed_samples_per_second
    if scheduled is None:
        raise SystemExit(f"{role} 작업에 실제 요청 전송 속도 기록이 없습니다")
    minimum_rate = target_qps * 0.98
    if scheduled < minimum_rate or completed < minimum_rate:
        raise SystemExit(
            f"{role} 작업이 목표 요청량의 98%를 처리하지 못했습니다: "
            f"목표={target_qps:.3f}, 보냄={scheduled:.3f}, 완료={completed:.3f}"
        )
    if completed < scheduled * 0.98:
        raise SystemExit(
            f"{role} 작업에서 보낸 요청에 비해 완료 속도가 낮아 "
            f"요청이 쌓일 가능성이 있습니다: 보냄={scheduled:.3f}, "
            f"완료={completed:.3f}"
        )
    if probe.limiter_calls <= 0:
        raise SystemExit(f"HAMi가 {role} 작업을 확인한 기록이 없습니다")

    if wait_expected[role]:
        if probe.waited_calls <= 0 or probe.wait_ns <= 0:
            raise SystemExit(f"{role} 작업에서 예상한 실행 대기가 기록되지 않았습니다")
    elif probe.waited_calls != 0 or probe.wait_ns != 0:
        raise SystemExit(f"100%인 {role} 작업에서 제한으로 인한 대기가 발생했습니다")

    print(f"[{role} 작업]")
    print("실제로 요청을 보낸 속도:", round(scheduled, 3))
    print("초당 완료 요청 수:", round(completed, 3))
    print("가운데 응답시간(ms):", round(metrics.p50_ms, 3))
    print(
        "전체 요청의 99%가 완료되는 응답시간 경계(ms):",
        round(metrics.p99_ms, 3),
    )
    print("HAMi 사용량 제한으로 인한 실행 대기 횟수:", probe.waited_calls)
    print("실행 대기 누적시간(초):", round(probe.wait_ns / 1_000_000_000, 6))

print("두 작업 모두 요청 적체와 실행 대기 조건 확인을 통과했습니다.")
PY

printf '확인 결과 위치: %s\n' "$OUTPUT_DIR"
