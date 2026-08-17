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

case "$PILOT_ROOT" in
  "$PILOT_NFS_ROOT"/*) CONTEXT_RELATIVE="${PILOT_ROOT#"$PILOT_NFS_ROOT"/}" ;;
  *)
    printf '저장소가 공용 저장공간 아래에 있어야 합니다: %s\n' \
      "$PILOT_NFS_ROOT" >&2
    exit 1
    ;;
esac

PROBE_IMAGE="$PILOT_REGISTRY/hami-tail-bert:mlperf-v5.1.1-hami-v2.9.0"
REGISTRY_SECRET="${PILOT_REGISTRY_SECRET:-}"
JOB_NAME="hami-pilot-check-probe"
ATTEMPT_ID="$(date -u +%Y%m%dT%H%M%SZ)"
OUTPUT_RELATIVE="$CONTEXT_RELATIVE/artifacts/k8s-probe-check/$ATTEMPT_ID"
OUTPUT_DIR="$PILOT_NFS_ROOT/$OUTPUT_RELATIVE"
PROBE_FILE="$OUTPUT_DIR/hami_probe.jsonl"

mkdir -p "$OUTPUT_DIR"

IMAGE_PULL_SECRETS=""
if [[ -n "$REGISTRY_SECRET" ]]; then
  IMAGE_PULL_SECRETS=$(cat <<YAML
      imagePullSecrets:
        - name: $REGISTRY_SECRET
YAML
)
fi

printf '측정용 이미지로 짧은 GPU 연산을 시작합니다.\n'
printf '이 검사는 BERT 실험 결과가 아니라 HAMi 연결 확인용입니다.\n'

kubectl -n "$PILOT_K8S_NAMESPACE" delete job "$JOB_NAME" --ignore-not-found=true
kubectl apply -f - <<YAML
apiVersion: batch/v1
kind: Job
metadata:
  name: $JOB_NAME
  namespace: $PILOT_K8S_NAMESPACE
spec:
  backoffLimit: 0
  activeDeadlineSeconds: 600
  template:
    spec:
      restartPolicy: Never
$IMAGE_PULL_SECRETS
      nodeSelector:
        kubernetes.io/hostname: $PILOT_K8S_BUILD_NODE
      containers:
        - name: probe-check
          image: $PROBE_IMAGE
          imagePullPolicy: Always
          command: ["python3", "-c"]
          args:
            - |
              import time
              import torch

              if not torch.cuda.is_available():
                  raise RuntimeError("GPU를 사용할 수 없습니다")

              left = torch.randn((4096, 4096), device="cuda")
              right = torch.randn((4096, 4096), device="cuda")
              torch.cuda.synchronize()
              check_seconds = 10
              operations = 0
              started = time.perf_counter()
              while time.perf_counter() - started < check_seconds:
                  result = torch.mm(left, right)
                  torch.cuda.synchronize()
                  operations += 1
              print("GPU:", torch.cuda.get_device_name(0), flush=True)
              print("짧은 GPU 연산 시간(초):", round(time.perf_counter() - started, 3), flush=True)
              print("GPU 연산 횟수:", operations, flush=True)
              print("연산 결과 크기:", tuple(result.shape), flush=True)
          env:
            - name: LIBCUDA_LOG_LEVEL
              value: "3"
            - name: LD_PRELOAD
              value: /opt/hami/libvgpu.so
            - name: CUDA_DEVICE_SM_LIMIT
              value: "50"
            - name: GPU_CORE_UTILIZATION_POLICY
              value: force
            - name: CUDA_DEVICE_MEMORY_SHARED_CACHE
              value: /output/victim.cache
            - name: HAMI_PROBE_OUTPUT
              value: /output/hami_probe.jsonl
          resources:
            limits:
              nvidia.com/gpu: 1
          volumeMounts:
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
  --for=condition=complete "job/$JOB_NAME" --timeout=11m; then
  kubectl -n "$PILOT_K8S_NAMESPACE" logs \
    "job/$JOB_NAME" --all-containers=true || true
  kubectl -n "$PILOT_K8S_NAMESPACE" describe "job/$JOB_NAME" || true
  exit 1
fi

kubectl -n "$PILOT_K8S_NAMESPACE" logs \
  "job/$JOB_NAME" --all-containers=true

[[ -s "$PROBE_FILE" ]] || {
  printf 'HAMi 실행 대기 기록 파일이 만들어지지 않았습니다: %s\n' \
    "$PROBE_FILE" >&2
  exit 1
}

python3 - "$PROBE_FILE" <<'PY'
import json
from pathlib import Path
import sys

path = Path(sys.argv[1])
records = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]
totals = {
    key: sum(int(record[key]) for record in records)
    for key in ("limiter_calls", "waited_calls", "sleep_calls", "wait_ns")
}

print("HAMi 제한 기능 확인 횟수:", totals["limiter_calls"])
print("실제로 실행을 기다린 횟수:", totals["waited_calls"])
print("실행 대기의 누적 시간(초):", round(totals["wait_ns"] / 1_000_000_000, 6))

if totals["limiter_calls"] <= 0:
    raise SystemExit("HAMi 제한 기능이 GPU 작업을 확인한 기록이 없습니다")
if totals["waited_calls"] <= 0 or totals["wait_ns"] <= 0:
    raise SystemExit("50% 사용 한도에서 실제 실행 대기가 기록되지 않았습니다")

print("HAMi 실행 대기 기록 확인 완료")
PY

printf '확인 결과 위치: %s\n' "$OUTPUT_DIR"
