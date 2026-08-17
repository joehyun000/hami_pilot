#!/usr/bin/env bash
set -euo pipefail

HAMI_RELEASE="v2.9.0"
HAMI_COMMIT="3a006c6ae2f077a2683df7805c43656c07f6dc15"
HAMI_CORE_COMMIT="5091a2fbe1816df1265490f771346730f29e2c8d"
MLPERF_RELEASE="v5.1.1"
MLPERF_COMMIT="6776245e99dce0600cfc9a6fb61efd310f87de3d"
DEEP_LEARNING_EXAMPLES_COMMIT="b03375bd6c2c5233130e61a3be49e26d1a20ac7c"

verify_bert_source_tree() {
  local mlperf_dir="$1"
  local bert_source="$mlperf_dir/language/bert/DeepLearningExamples/PyTorch/LanguageModeling/BERT"
  local required_file
  for required_file in tokenization.py file_utils.py; do
    [[ -s "$bert_source/$required_file" ]] || {
      printf 'BERT 연결 소스가 없거나 비어 있습니다: %s\n' \
        "$bert_source/$required_file" >&2
      return 1
    }
  done
  printf 'BERT 연결 소스 확인 완료: %s\n' "$bert_source"
}

stage_bert_runtime_files() {
  local mlperf_dir="$1"
  local destination="$2"
  local bert_source="$mlperf_dir/language/bert/DeepLearningExamples/PyTorch/LanguageModeling/BERT"
  verify_bert_source_tree "$mlperf_dir"
  mkdir -p "$destination"
  install -m 0644 "$bert_source/tokenization.py" "$destination/tokenization.py"
  install -m 0644 "$bert_source/file_utils.py" "$destination/file_utils.py"
  printf 'BERT 실행 파일 준비 완료: %s\n' "$destination"
}

if [[ "${1:-}" == "--print-pins" ]]; then
  printf '{"hami_release":"%s","hami_commit":"%s","hami_core_commit":"%s","deep_learning_examples_commit":"%s","mlperf_release":"%s","mlperf_commit":"%s"}\n' \
    "$HAMI_RELEASE" "$HAMI_COMMIT" "$HAMI_CORE_COMMIT" \
    "$DEEP_LEARNING_EXAMPLES_COMMIT" "$MLPERF_RELEASE" "$MLPERF_COMMIT"
  exit 0
fi
if [[ "${1:-}" == "--verify-bert-source-tree" ]]; then
  [[ $# -eq 2 ]] || {
    printf '사용법: scripts/bootstrap_sources.sh --verify-bert-source-tree <MLPerf 소스 경로>\n' >&2
    exit 2
  }
  verify_bert_source_tree "$2"
  exit 0
fi
if [[ "${1:-}" == "--stage-bert-runtime-files" ]]; then
  [[ $# -eq 3 ]] || {
    printf '사용법: scripts/bootstrap_sources.sh --stage-bert-runtime-files <MLPerf 소스 경로> <준비 경로>\n' >&2
    exit 2
  }
  stage_bert_runtime_files "$2" "$3"
  exit 0
fi
if [[ $# -ne 0 ]]; then
  printf '사용법: scripts/bootstrap_sources.sh [--print-pins | --verify-bert-source-tree <MLPerf 소스 경로> | --stage-bert-runtime-files <MLPerf 소스 경로> <준비 경로>]\n' >&2
  exit 2
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PILOT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
SOURCE_ROOT="$PILOT_ROOT/.cache/sources"
HAMI_CORE_DIR="$SOURCE_ROOT/HAMi-core-5091a2f"
HAMI_CORE_VANILLA_DIR="$SOURCE_ROOT/HAMi-core-5091a2f-vanilla"
MLPERF_DIR="$SOURCE_ROOT/inference-v5.1.1"
BERT_RUNTIME_DIR="$SOURCE_ROOT/bert-runtime-b03375bd"

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
ensure_checkout "https://github.com/Project-HAMi/HAMi-core.git" "$HAMI_CORE_COMMIT" "$HAMI_CORE_VANILLA_DIR"

ensure_checkout "https://github.com/mlcommons/inference.git" "$MLPERF_COMMIT" "$MLPERF_DIR"
git -C "$MLPERF_DIR" submodule sync -- language/bert/DeepLearningExamples
git -C "$MLPERF_DIR" submodule update \
  --init --depth 1 language/bert/DeepLearningExamples
actual_deep_learning_examples_commit="$(
  git -C "$MLPERF_DIR/language/bert/DeepLearningExamples" rev-parse HEAD
)"
if [[ "$actual_deep_learning_examples_commit" != "$DEEP_LEARNING_EXAMPLES_COMMIT" ]]; then
  printf 'NVIDIA BERT 연결 소스 번호가 다릅니다: %s\n' \
    "$actual_deep_learning_examples_commit" >&2
  exit 1
fi
verify_bert_source_tree "$MLPERF_DIR"
stage_bert_runtime_files "$MLPERF_DIR" "$BERT_RUNTIME_DIR"
apply_once "$MLPERF_DIR" "$PILOT_ROOT/patches/mlperf-v5.1.1-bert-pilot.patch"

MANIFEST="$PILOT_ROOT/artifacts/source_manifest.json"
python3 -c 'import json,pathlib,sys; pathlib.Path(sys.argv[1]).write_text(json.dumps({"hami_release":sys.argv[2],"hami_commit":sys.argv[3],"hami_core_commit":sys.argv[4],"deep_learning_examples_commit":sys.argv[5],"mlperf_release":sys.argv[6],"mlperf_commit":sys.argv[7]},indent=2,sort_keys=True)+"\n",encoding="utf-8")' \
  "$MANIFEST" "$HAMI_RELEASE" "$HAMI_COMMIT" "$HAMI_CORE_COMMIT" \
  "$DEEP_LEARNING_EXAMPLES_COMMIT" "$MLPERF_RELEASE" "$MLPERF_COMMIT"

printf 'Prepared pinned sources under %s\n' "$SOURCE_ROOT"
