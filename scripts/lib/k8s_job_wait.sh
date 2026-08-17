#!/usr/bin/env bash

wait_for_k8s_job() {
  local namespace="$1"
  local job_name="$2"
  local timeout_seconds="$3"
  local poll_seconds="${4:-5}"
  local deadline=$((SECONDS + timeout_seconds))
  local status
  local succeeded
  local failed

  while ((SECONDS <= deadline)); do
    if ! status="$(
      kubectl -n "$namespace" get job "$job_name" \
        -o jsonpath='{.status.succeeded}{"|"}{.status.failed}'
    )"; then
      printf '작업 상태를 확인하지 못했습니다: %s/%s\n' \
        "$namespace" "$job_name" >&2
      return 2
    fi

    IFS='|' read -r succeeded failed <<<"$status"
    succeeded="${succeeded:-0}"
    failed="${failed:-0}"
    if [[ ! "$succeeded" =~ ^[0-9]+$ || ! "$failed" =~ ^[0-9]+$ ]]; then
      printf '작업 상태 값을 이해하지 못했습니다: %s\n' \
        "$status" >&2
      return 2
    fi
    if ((succeeded >= 1)); then
      printf '작업이 완료됐습니다: %s/%s\n' "$namespace" "$job_name"
      return 0
    fi
    if ((failed >= 1)); then
      printf '작업이 실패했습니다: %s/%s\n' \
        "$namespace" "$job_name" >&2
      return 1
    fi
    sleep "$poll_seconds"
  done

  printf '작업 완료 대기 시간을 넘겼습니다: %s/%s\n' \
    "$namespace" "$job_name" >&2
  return 124
}
