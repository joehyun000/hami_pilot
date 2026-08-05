#!/usr/bin/env bash
set -euo pipefail

HAMI_RELEASE="v2.9.0"
HAMI_COMMIT="3a006c6ae2f077a2683df7805c43656c07f6dc15"
HAMI_CORE_COMMIT="5091a2fbe1816df1265490f771346730f29e2c8d"
MLPERF_RELEASE="v5.1.1"
MLPERF_COMMIT="6776245e99dce0600cfc9a6fb61efd310f87de3d"

if [[ "${1:-}" == "--print-pins" ]]; then
  printf '{"hami_release":"%s","hami_commit":"%s","hami_core_commit":"%s","mlperf_release":"%s","mlperf_commit":"%s"}\n' \
    "$HAMI_RELEASE" "$HAMI_COMMIT" "$HAMI_CORE_COMMIT" "$MLPERF_RELEASE" "$MLPERF_COMMIT"
  exit 0
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PILOT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
SOURCE_ROOT="$PILOT_ROOT/.cache/sources"
HAMI_CORE_DIR="$SOURCE_ROOT/HAMi-core-5091a2f"
MLPERF_DIR="$SOURCE_ROOT/inference-v5.1.1"

mkdir -p "$SOURCE_ROOT" "$PILOT_ROOT/artifacts"

ensure_checkout() {
  local repository_url="$1"
  local commit="$2"
  local destination="$3"

  if [[ ! -d "$destination/.git" ]]; then
    git clone "$repository_url" "$destination"
  fi
  local remote
  remote="$(git -C "$destination" config --get remote.origin.url)"
  if [[ "$remote" != "$repository_url" ]]; then
    printf 'unexpected remote for %s: %s\n' "$destination" "$remote" >&2
    exit 1
  fi
  git -C "$destination" cat-file -e "${commit}^{commit}"
  git -C "$destination" checkout --detach "$commit"
  local actual
  actual="$(git -C "$destination" rev-parse HEAD)"
  if [[ "$actual" != "$commit" ]]; then
    printf 'checkout mismatch for %s: %s\n' "$destination" "$actual" >&2
    exit 1
  fi
}

apply_once() {
  local destination="$1"
  local patch="$2"
  if git -C "$destination" apply --check "$patch"; then
    git -C "$destination" apply "$patch"
    return
  fi
  if git -C "$destination" apply --reverse --check "$patch"; then
    return
  fi
  printf 'patch is neither cleanly applicable nor already applied: %s\n' "$patch" >&2
  exit 1
}

ensure_checkout "https://github.com/Project-HAMi/HAMi-core.git" "$HAMI_CORE_COMMIT" "$HAMI_CORE_DIR"
install -m 0644 "$PILOT_ROOT/probe/hami_probe_counter.h" \
  "$HAMI_CORE_DIR/src/multiprocess/hami_probe_counter.h"
apply_once "$HAMI_CORE_DIR" "$PILOT_ROOT/patches/hami-core-5091a2f-probe.patch"

ensure_checkout "https://github.com/mlcommons/inference.git" "$MLPERF_COMMIT" "$MLPERF_DIR"
apply_once "$MLPERF_DIR" "$PILOT_ROOT/patches/mlperf-v5.1.1-bert-pilot.patch"

MANIFEST="$PILOT_ROOT/artifacts/source_manifest.json"
python3 -c 'import json,pathlib,sys; pathlib.Path(sys.argv[1]).write_text(json.dumps({"hami_release":sys.argv[2],"hami_commit":sys.argv[3],"hami_core_commit":sys.argv[4],"mlperf_release":sys.argv[5],"mlperf_commit":sys.argv[6]},indent=2,sort_keys=True)+"\n",encoding="utf-8")' \
  "$MANIFEST" "$HAMI_RELEASE" "$HAMI_COMMIT" "$HAMI_CORE_COMMIT" "$MLPERF_RELEASE" "$MLPERF_COMMIT"

printf 'Prepared pinned sources under %s\n' "$SOURCE_ROOT"

