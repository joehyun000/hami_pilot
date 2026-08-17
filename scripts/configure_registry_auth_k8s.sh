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
  PILOT_REGISTRY
  PILOT_REGISTRY_USERNAME
  PILOT_REGISTRY_SECRET
)
for variable_name in "${required_variables[@]}"; do
  [[ -n "${!variable_name:-}" ]] || {
    printf '쿠버네티스 설정에 %s 값이 필요합니다: %s\n' \
      "$variable_name" "$ENV_FILE" >&2
    exit 1
  }
done

REGISTRY_HOST="${PILOT_REGISTRY%%/*}"

printf 'GitHub 이미지 업로드 토큰을 입력하세요(화면에 표시되지 않음): ' >&2
IFS= read -r -s REGISTRY_TOKEN
printf '\n' >&2

[[ -n "$REGISTRY_TOKEN" ]] || {
  printf '토큰이 비어 있습니다.\n' >&2
  exit 1
}

AUTH_VALUE="$(printf '%s' "$PILOT_REGISTRY_USERNAME:$REGISTRY_TOKEN" | base64 | tr -d '\n')"
DOCKER_CONFIG="$(printf \
  '{"auths":{"%s":{"username":"%s","password":"%s","auth":"%s"}}}' \
  "$REGISTRY_HOST" "$PILOT_REGISTRY_USERNAME" "$REGISTRY_TOKEN" "$AUTH_VALUE")"
SECRET_VALUE="$(printf '%s' "$DOCKER_CONFIG" | base64 | tr -d '\n')"

unset REGISTRY_TOKEN AUTH_VALUE DOCKER_CONFIG

kubectl apply -f - <<YAML
apiVersion: v1
kind: Secret
metadata:
  name: $PILOT_REGISTRY_SECRET
  namespace: $PILOT_K8S_NAMESPACE
type: kubernetes.io/dockerconfigjson
data:
  .dockerconfigjson: $SECRET_VALUE
YAML

unset SECRET_VALUE

SECRET_TYPE="$(kubectl -n "$PILOT_K8S_NAMESPACE" get secret \
  "$PILOT_REGISTRY_SECRET" -o jsonpath='{.type}')"
[[ "$SECRET_TYPE" == "kubernetes.io/dockerconfigjson" ]] || {
  printf '이미지 저장소 인증 정보의 형식이 잘못됐습니다.\n' >&2
  exit 1
}

printf '이미지 저장소 인증 정보를 쿠버네티스에 등록했습니다: %s/%s\n' \
  "$PILOT_K8S_NAMESPACE" "$PILOT_REGISTRY_SECRET"
