#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PILOT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
DEFAULT_ENV_FILE="$PILOT_ROOT/configs/server.env"
ENV_FILE="$DEFAULT_ENV_FILE"
DRY_RUN=false
RERUN_FAILED=false
CLOCK_LOCKED=false

usage() {
  cat <<'EOF'
사용법:
  scripts/run_gpu_pilot.sh prepare [--env-file 경로] [--dry-run]
  scripts/run_gpu_pilot.sh run [--env-file 경로] [--dry-run] [--rerun-failed]

prepare: 실행 환경을 만들고, 공통 요청량 선정과 짧은 조건 확인까지 실행한다.
run:     짧은 조건 확인을 통과한 경우에만 30회 본 실험과 결과 분석을 실행한다.
EOF
}

die() {
  printf '%s\n' "$*" >&2
  exit 1
}

if [[ $# -lt 1 ]]; then
  usage
  exit 2
fi

MODE="$1"
shift
case "$MODE" in
  prepare|run) ;;
  -h|--help)
    usage
    exit 0
    ;;
  *)
    usage >&2
    die "알 수 없는 실행 단계입니다: $MODE"
    ;;
esac

while [[ $# -gt 0 ]]; do
  case "$1" in
    --env-file)
      [[ $# -ge 2 ]] || die "--env-file 뒤에 경로가 필요합니다."
      ENV_FILE="$2"
      shift 2
      ;;
    --dry-run)
      DRY_RUN=true
      shift
      ;;
    --rerun-failed)
      RERUN_FAILED=true
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *) die "알 수 없는 옵션입니다: $1" ;;
  esac
done

if [[ "$MODE" != "run" && "$RERUN_FAILED" == true ]]; then
  die "--rerun-failed는 run 단계에서만 사용할 수 있습니다."
fi

[[ -f "$ENV_FILE" ]] || die "서버 설정 파일이 없습니다: $ENV_FILE"

set -a
# shellcheck disable=SC1090
source "$ENV_FILE"
set +a

required_variables=(
  PILOT_MODEL_FILE
  PILOT_DATASET_FILE
  PILOT_VOCAB_FILE
  PILOT_CANDIDATE_QPS
  PILOT_GPU_INDEX
  PILOT_OUTPUT_DIR
  PILOT_GPU_CLOCK_MHZ
  PILOT_MAX_START_TEMP_C
)
for variable_name in "${required_variables[@]}"; do
  [[ -n "${!variable_name:-}" ]] || die "서버 설정에 $variable_name 값이 필요합니다."
done

PILOT_TEMPERATURE_WAIT_SECONDS="${PILOT_TEMPERATURE_WAIT_SECONDS:-900}"

for input_path in "$PILOT_MODEL_FILE" "$PILOT_DATASET_FILE" "$PILOT_VOCAB_FILE"; do
  [[ "$input_path" = /* ]] || die "입력 파일은 절대 경로로 적어야 합니다: $input_path"
  [[ -f "$input_path" ]] || die "입력 파일이 없습니다: $input_path"
done

[[ "$PILOT_GPU_CLOCK_MHZ" =~ ^[0-9]+$ ]] || die "PILOT_GPU_CLOCK_MHZ는 양의 정수여야 합니다."
[[ "$PILOT_GPU_CLOCK_MHZ" -gt 0 ]] || die "PILOT_GPU_CLOCK_MHZ는 0보다 커야 합니다."
[[ "$PILOT_MAX_START_TEMP_C" =~ ^[0-9]+$ ]] || die "PILOT_MAX_START_TEMP_C는 양의 정수여야 합니다."
[[ "$PILOT_TEMPERATURE_WAIT_SECONDS" =~ ^[0-9]+$ ]] || die "PILOT_TEMPERATURE_WAIT_SECONDS는 양의 정수여야 합니다."

read -r -a CANDIDATE_QPS <<<"$PILOT_CANDIDATE_QPS"
[[ ${#CANDIDATE_QPS[@]} -gt 0 ]] || die "시험할 초당 요청 수가 하나 이상 필요합니다."
for qps in "${CANDIDATE_QPS[@]}"; do
  [[ "$qps" =~ ^[0-9]+([.][0-9]+)?$ ]] || die "잘못된 초당 요청 수입니다: $qps"
done

if [[ "$PILOT_OUTPUT_DIR" = /* ]]; then
  OUTPUT_DIR="$PILOT_OUTPUT_DIR"
else
  OUTPUT_DIR="$PILOT_ROOT/$PILOT_OUTPUT_DIR"
fi
BASE_CONFIG="$PILOT_ROOT/configs/pilot.yaml"
RESOLVED_CONFIG="$OUTPUT_DIR/pilot.resolved.yaml"
PYTHON="$PILOT_ROOT/.venv/bin/python"
PIP="$PILOT_ROOT/.venv/bin/pip"

run_command() {
  if [[ "$DRY_RUN" == true ]]; then
    printf '$'
    printf ' %q' "$@"
    printf '\n'
  else
    "$@"
  fi
}

restore_clock() {
  if [[ "$CLOCK_LOCKED" == true ]]; then
    if ! nvidia-smi -i "$PILOT_GPU_INDEX" -rgc >/dev/null; then
      printf '경고: GPU 동작 속도를 원래 설정으로 되돌리지 못했습니다.\n' >&2
    fi
  fi
}
trap restore_clock EXIT

lock_clock() {
  printf 'GPU %s의 동작 속도를 %s MHz로 고정합니다.\n' \
    "$PILOT_GPU_INDEX" "$PILOT_GPU_CLOCK_MHZ"
  run_command nvidia-smi -i "$PILOT_GPU_INDEX" \
    -lgc "$PILOT_GPU_CLOCK_MHZ,$PILOT_GPU_CLOCK_MHZ"
  if [[ "$DRY_RUN" == false ]]; then
    CLOCK_LOCKED=true
  fi
}

wait_for_temperature() {
  if [[ "$DRY_RUN" == true ]]; then
    printf '$ nvidia-smi -i %q --query-gpu=temperature.gpu --format=csv,noheader,nounits\n' \
      "$PILOT_GPU_INDEX"
    printf 'GPU 온도가 %s°C 이하인지 확인합니다.\n' "$PILOT_MAX_START_TEMP_C"
    return
  fi

  local deadline=$((SECONDS + PILOT_TEMPERATURE_WAIT_SECONDS))
  local temperature
  while true; do
    temperature="$(
      nvidia-smi -i "$PILOT_GPU_INDEX" \
        --query-gpu=temperature.gpu --format=csv,noheader,nounits | tr -d '[:space:]'
    )"
    [[ "$temperature" =~ ^[0-9]+$ ]] || die "GPU 온도를 숫자로 읽지 못했습니다: $temperature"
    if [[ "$temperature" -le "$PILOT_MAX_START_TEMP_C" ]]; then
      printf 'GPU 온도 %s°C: 시작 기준 %s°C 이하입니다.\n' \
        "$temperature" "$PILOT_MAX_START_TEMP_C"
      return
    fi
    if [[ "$SECONDS" -ge "$deadline" ]]; then
      die "GPU 온도가 ${PILOT_TEMPERATURE_WAIT_SECONDS}초 안에 ${PILOT_MAX_START_TEMP_C}°C 이하로 내려가지 않았습니다."
    fi
    printf 'GPU 온도 %s°C: %s°C 이하가 될 때까지 기다립니다.\n' \
      "$temperature" "$PILOT_MAX_START_TEMP_C"
    sleep 5
  done
}

smoke_passed() {
  [[ -f "$OUTPUT_DIR/smoke.json" ]] || return 1
  "$PYTHON" -c \
    'import json,sys; raise SystemExit(0 if json.load(open(sys.argv[1], encoding="utf-8")).get("passed") is True else 1)' \
    "$OUTPUT_DIR/smoke.json"
}

prepare() {
  printf '[1/5] 입력 파일과 서버 설정 확인\n'
  printf '설정: %s\n결과 저장 위치: %s\n' "$ENV_FILE" "$OUTPUT_DIR"

  printf '\n[2/5] 파이썬 실행 환경과 컨테이너 이미지 준비\n'
  if [[ ! -x "$PYTHON" ]]; then
    run_command python3 -m venv "$PILOT_ROOT/.venv"
  fi
  run_command "$PIP" install -e "$PILOT_ROOT" --no-build-isolation
  run_command bash "$PILOT_ROOT/scripts/bootstrap_sources.sh"
  run_command bash "$PILOT_ROOT/scripts/build_images.sh"
  run_command "$PYTHON" -m hami_tail_pilot.cli validate --config "$BASE_CONFIG"

  printf '\n[3/5] GPU 설정과 실행 전 환경 확인\n'
  lock_clock
  wait_for_temperature
  run_command "$PYTHON" -m hami_tail_pilot.cli run \
    --preflight-only \
    --config "$BASE_CONFIG" \
    --output "$OUTPUT_DIR" \
    --model-file "$PILOT_MODEL_FILE" \
    --dataset-file "$PILOT_DATASET_FILE" \
    --vocab-file "$PILOT_VOCAB_FILE" \
    --gpu-index "$PILOT_GPU_INDEX"

  printf '\n[4/5] 공통 요청량 찾기\n'
  wait_for_temperature
  run_command "$PYTHON" -m hami_tail_pilot.cli calibrate \
    --config "$BASE_CONFIG" \
    --output "$OUTPUT_DIR" \
    --candidate-qps "${CANDIDATE_QPS[@]}" \
    --model-file "$PILOT_MODEL_FILE" \
    --dataset-file "$PILOT_DATASET_FILE" \
    --vocab-file "$PILOT_VOCAB_FILE" \
    --gpu-index "$PILOT_GPU_INDEX"

  printf '\n[5/5] 짧은 조건 확인\n'
  wait_for_temperature
  run_command "$PYTHON" -m hami_tail_pilot.cli smoke \
    --config "$RESOLVED_CONFIG" \
    --output "$OUTPUT_DIR" \
    --model-file "$PILOT_MODEL_FILE" \
    --dataset-file "$PILOT_DATASET_FILE" \
    --vocab-file "$PILOT_VOCAB_FILE" \
    --gpu-index "$PILOT_GPU_INDEX"

  if [[ "$DRY_RUN" == false ]] && ! smoke_passed; then
    die "짧은 조건 확인 결과를 통과한 것으로 읽지 못했습니다."
  fi

  printf '\n준비 단계가 끝났습니다. 30회 본 실험은 시작하지 않았습니다.\n'
  printf '짧은 확인 결과를 본 뒤 다음 명령을 별도로 실행하세요:\n'
  printf '  scripts/run_gpu_pilot.sh run --env-file %q\n' "$ENV_FILE"
}

run_experiment() {
  [[ -f "$RESOLVED_CONFIG" ]] || die "공통 요청량이 정해진 설정 파일이 없습니다: $RESOLVED_CONFIG"
  if ! smoke_passed; then
    die "짧은 조건 확인을 통과하지 않았습니다. prepare 단계부터 확인하세요."
  fi

  printf '[1/2] 30회 본 실험 시작\n'
  lock_clock
  wait_for_temperature
  run_arguments=(
    "$PYTHON" -m hami_tail_pilot.cli run
    --config "$RESOLVED_CONFIG"
    --output "$OUTPUT_DIR"
    --model-file "$PILOT_MODEL_FILE"
    --dataset-file "$PILOT_DATASET_FILE"
    --vocab-file "$PILOT_VOCAB_FILE"
    --gpu-index "$PILOT_GPU_INDEX"
  )
  if [[ "$RERUN_FAILED" == true ]]; then
    run_arguments+=(--rerun-failed)
  fi
  run_command "${run_arguments[@]}"

  printf '\n[2/2] 결과 비교와 판정\n'
  run_command "$PYTHON" -m hami_tail_pilot.cli analyze --input "$OUTPUT_DIR"
  printf '\n본 실험과 분석이 끝났습니다: %s\n' "$OUTPUT_DIR"
}

cd "$PILOT_ROOT"
case "$MODE" in
  prepare) prepare ;;
  run) run_experiment ;;
esac
