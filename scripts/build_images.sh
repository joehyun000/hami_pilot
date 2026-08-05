#!/usr/bin/env bash
set -euo pipefail

PROBE_IMAGE_TAG="hami-tail-bert:mlperf-v5.1.1-hami-v2.9.0"
VANILLA_IMAGE_TAG="hami-tail-bert:mlperf-v5.1.1-hami-v2.9.0-vanilla"

if [[ "${1:-}" == "--print-tag" ]]; then
  printf '%s\n' "$PROBE_IMAGE_TAG"
  exit 0
fi

if [[ "${1:-}" == "--print-tags" ]]; then
  printf '{"probe":"%s","vanilla":"%s"}\n' "$PROBE_IMAGE_TAG" "$VANILLA_IMAGE_TAG"
  exit 0
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PILOT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

if [[ ! -f "$PILOT_ROOT/artifacts/source_manifest.json" ]]; then
  printf 'source manifest is missing; run scripts/bootstrap_sources.sh first\n' >&2
  exit 1
fi

docker build \
  --file "$PILOT_ROOT/docker/Dockerfile.bert" \
  --build-arg "HAMI_CORE_SOURCE=.cache/sources/HAMi-core-5091a2f" \
  --tag "$PROBE_IMAGE_TAG" \
  "$PILOT_ROOT"

docker build \
  --file "$PILOT_ROOT/docker/Dockerfile.bert" \
  --build-arg "HAMI_CORE_SOURCE=.cache/sources/HAMi-core-5091a2f-vanilla" \
  --tag "$VANILLA_IMAGE_TAG" \
  "$PILOT_ROOT"

docker image inspect "$PROBE_IMAGE_TAG" --format '{{.Id}}'
docker image inspect "$VANILLA_IMAGE_TAG" --format '{{.Id}}'
