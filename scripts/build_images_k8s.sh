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

KANIKO_IMAGE="${PILOT_KANIKO_IMAGE:-gcr.io/kaniko-project/executor:v1.23.2}"
REGISTRY_SECRET="${PILOT_REGISTRY_SECRET:-}"
REGISTRY_INSECURE="${PILOT_REGISTRY_INSECURE:-false}"
PROBE_IMAGE="$PILOT_REGISTRY/hami-tail-bert:mlperf-v5.1.1-hami-v2.9.0"
VANILLA_IMAGE="$PILOT_REGISTRY/hami-tail-bert:mlperf-v5.1.1-hami-v2.9.0-vanilla"

if [[ "${1:-}" == "--print-tags" ]]; then
  printf '{"probe":"%s","vanilla":"%s"}\n' "$PROBE_IMAGE" "$VANILLA_IMAGE"
  exit 0
fi

if [[ $# -ne 0 ]]; then
  printf '사용법: scripts/build_images_k8s.sh [--print-tags]\n' >&2
  exit 2
fi

case "$REGISTRY_INSECURE" in
  true)
    KANIKO_REGISTRY_ARGS=$'            - --insecure\n            - --skip-tls-verify'
    ;;
  false)
    KANIKO_REGISTRY_ARGS=""
    ;;
  *)
    printf 'PILOT_REGISTRY_INSECURE는 true 또는 false여야 합니다.\n' >&2
    exit 1
    ;;
esac

if [[ "$PILOT_REGISTRY" == ghcr.io/* && -z "$REGISTRY_SECRET" ]]; then
  printf 'GitHub 이미지 업로드에 PILOT_REGISTRY_SECRET 값이 필요합니다.\n' >&2
  exit 1
fi

REGISTRY_VOLUME_MOUNT=""
REGISTRY_VOLUME=""
if [[ -n "$REGISTRY_SECRET" ]]; then
  REGISTRY_VOLUME_MOUNT=$(cat <<YAML
            - name: registry-auth
              mountPath: /kaniko/.docker
              readOnly: true
YAML
)
  REGISTRY_VOLUME=$(cat <<YAML
        - name: registry-auth
          secret:
            secretName: $REGISTRY_SECRET
            items:
              - key: .dockerconfigjson
                path: config.json
YAML
)
fi

SOURCE_MANIFEST="$PILOT_ROOT/artifacts/source_manifest.json"

[[ -f "$SOURCE_MANIFEST" ]] || {
  printf '고정 소스 정보가 없습니다. scripts/bootstrap_sources.sh를 먼저 실행하세요.\n' >&2
  exit 1
}

case "$PILOT_ROOT" in
  "$PILOT_NFS_ROOT"/*) CONTEXT_RELATIVE="${PILOT_ROOT#"$PILOT_NFS_ROOT"/}" ;;
  *)
    printf '저장소가 공용 저장공간 아래에 있어야 합니다: %s\n' \
      "$PILOT_NFS_ROOT" >&2
    exit 1
    ;;
esac

POD_CONTEXT="/workspace/$CONTEXT_RELATIVE"

build_image() {
  local job_name="$1"
  local hami_source="$2"
  local destination="$3"

  kubectl -n "$PILOT_K8S_NAMESPACE" delete job "$job_name" --ignore-not-found=true
  kubectl apply -f - <<YAML
apiVersion: batch/v1
kind: Job
metadata:
  name: $job_name
  namespace: $PILOT_K8S_NAMESPACE
spec:
  backoffLimit: 0
  activeDeadlineSeconds: 3600
  template:
    spec:
      restartPolicy: Never
      nodeSelector:
        kubernetes.io/hostname: $PILOT_K8S_BUILD_NODE
      containers:
        - name: kaniko
          image: $KANIKO_IMAGE
          args:
            - --context=dir://$POD_CONTEXT
            - --dockerfile=$POD_CONTEXT/docker/Dockerfile.bert
            - --build-arg=HAMI_CORE_SOURCE=$hami_source
            - --destination=$destination
$KANIKO_REGISTRY_ARGS
          volumeMounts:
            - name: shared-storage
              mountPath: /workspace
              readOnly: true
$REGISTRY_VOLUME_MOUNT
      volumes:
        - name: shared-storage
          nfs:
            server: $PILOT_NFS_SERVER
            path: $PILOT_NFS_ROOT
$REGISTRY_VOLUME
YAML

  if ! kubectl -n "$PILOT_K8S_NAMESPACE" wait \
    --for=condition=complete "job/$job_name" --timeout=60m; then
    kubectl -n "$PILOT_K8S_NAMESPACE" logs \
      "job/$job_name" --all-containers=true || true
    kubectl -n "$PILOT_K8S_NAMESPACE" describe "job/$job_name" || true
    return 1
  fi
  kubectl -n "$PILOT_K8S_NAMESPACE" logs \
    "job/$job_name" --all-containers=true
}

printf '[1/2] 실행 대기 측정 코드가 포함된 이미지 제작\n'
build_image \
  hami-pilot-build-probe \
  .cache/sources/HAMi-core-5091a2f \
  "$PROBE_IMAGE"

printf '\n[2/2] 측정 코드가 없는 비교용 이미지 제작\n'
build_image \
  hami-pilot-build-vanilla \
  .cache/sources/HAMi-core-5091a2f-vanilla \
  "$VANILLA_IMAGE"

printf '\n두 이미지가 설정한 저장소에 등록됐습니다.\n'
printf '측정용: %s\n비교용: %s\n' "$PROBE_IMAGE" "$VANILLA_IMAGE"
