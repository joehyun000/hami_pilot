#!/usr/bin/env bash
set -euo pipefail

IMAGE_TAG="hami-tail-bert:mlperf-v5.1.1-hami-v2.9.0"

if [[ "${1:-}" == "--print-tag" ]]; then
  printf '%s\n' "$IMAGE_TAG"
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
  --tag "$IMAGE_TAG" \
  "$PILOT_ROOT"

docker image inspect "$IMAGE_TAG" --format '{{.Id}}'

