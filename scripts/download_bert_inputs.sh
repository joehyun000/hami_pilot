#!/usr/bin/env bash
set -euo pipefail

DEFAULT_OUTPUT_DIR="/mnt/nfs_share/johyeon/hami-tail-pilot/inputs/bert"
OUTPUT_DIR="${PILOT_BERT_INPUT_DIR:-$DEFAULT_OUTPUT_DIR}"

MODEL_URL="https://zenodo.org/records/3733896/files/model.pytorch?download=1"
MODEL_MD5="00fbcbfaebfa20d87ac9885120a6e9b4"
VOCAB_URL="https://zenodo.org/records/3733896/files/vocab.txt?download=1"
VOCAB_MD5="64800d5d8528ce344256daf115d4965e"
DATASET_URL="https://rajpurkar.github.io/SQuAD-explorer/dataset/dev-v1.1.json"
DATASET_MD5="3e85deb501d4e538b6bc56f786231552"

usage() {
  cat <<'EOF'
사용법:
  scripts/download_bert_inputs.sh [저장-디렉토리]
  scripts/download_bert_inputs.sh --print-manifest

저장 디렉토리를 생략하면 다음 위치를 사용합니다.
  /mnt/nfs_share/johyeon/hami-tail-pilot/inputs/bert
EOF
}

print_manifest() {
  printf '%s\n' \
    '{' \
    '  "dev-v1.1.json": {' \
    "    \"md5\": \"$DATASET_MD5\"," \
    "    \"url\": \"$DATASET_URL\"" \
    '  },' \
    '  "model.pytorch": {' \
    "    \"md5\": \"$MODEL_MD5\"," \
    "    \"url\": \"$MODEL_URL\"" \
    '  },' \
    '  "vocab.txt": {' \
    "    \"md5\": \"$VOCAB_MD5\"," \
    "    \"url\": \"$VOCAB_URL\"" \
    '  }' \
    '}'
}

if [[ $# -gt 1 ]]; then
  usage >&2
  exit 2
fi
if [[ "${1:-}" == "--print-manifest" ]]; then
  print_manifest
  exit 0
fi
if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  usage
  exit 0
fi
if [[ $# -eq 1 ]]; then
  OUTPUT_DIR="$1"
fi
if [[ "$OUTPUT_DIR" != /* ]]; then
  printf '저장 디렉토리는 절대 경로로 적어야 합니다: %s\n' "$OUTPUT_DIR" >&2
  exit 2
fi

command -v md5sum >/dev/null || {
  printf '파일 검사에 필요한 md5sum 명령을 찾지 못했습니다.\n' >&2
  exit 1
}
if ! command -v curl >/dev/null && ! command -v wget >/dev/null; then
  printf '파일을 받을 curl 또는 wget 명령이 필요합니다.\n' >&2
  exit 1
fi

mkdir -p "$OUTPUT_DIR"

actual_md5() {
  md5sum "$1" | awk '{print $1}'
}

verify_file() {
  local path="$1"
  local expected="$2"
  [[ -f "$path" ]] || return 1
  [[ "$(actual_md5 "$path")" == "$expected" ]]
}

download_file() {
  local name="$1"
  local url="$2"
  local expected="$3"
  local destination="$OUTPUT_DIR/$name"
  local partial="$destination.part"

  if [[ -e "$destination" ]]; then
    if verify_file "$destination" "$expected"; then
      printf '이미 검사를 통과한 파일입니다: %s\n' "$destination"
      return
    fi
    printf '기존 파일의 검사값이 다릅니다. 자동으로 덮어쓰지 않습니다: %s\n' \
      "$destination" >&2
    exit 1
  fi

  printf '다운로드합니다: %s\n' "$name"
  if command -v curl >/dev/null; then
    curl_args=(--fail --location --retry 4 --retry-delay 2 --output "$partial")
    if [[ -s "$partial" ]]; then
      curl_args+=(--continue-at -)
    fi
    curl "${curl_args[@]}" "$url"
  else
    wget --continue --output-document="$partial" "$url"
  fi

  if ! verify_file "$partial" "$expected"; then
    printf '다운로드한 파일의 검사값이 다릅니다: %s\n' "$partial" >&2
    exit 1
  fi
  mv "$partial" "$destination"
  printf '검사를 통과했습니다: %s\n' "$destination"
}

download_file "model.pytorch" "$MODEL_URL" "$MODEL_MD5"
download_file "vocab.txt" "$VOCAB_URL" "$VOCAB_MD5"
download_file "dev-v1.1.json" "$DATASET_URL" "$DATASET_MD5"

python3 - "$OUTPUT_DIR/dev-v1.1.json" <<'PY'
import json
import pathlib
import sys

path = pathlib.Path(sys.argv[1])
payload = json.loads(path.read_text(encoding="utf-8"))
if payload.get("version") != "1.1" or not isinstance(payload.get("data"), list):
    raise SystemExit("SQuAD v1.1 자료 형식이 아닙니다")
print("SQuAD v1.1 자료 형식을 확인했습니다.")
PY

printf '\nBERT 입력 파일 준비가 끝났습니다: %s\n' "$OUTPUT_DIR"
