#ifndef HAMI_PROBE_COUNTER_H
#define HAMI_PROBE_COUNTER_H

#include <fcntl.h>
#include <stdatomic.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

static _Atomic uint64_t hami_probe_limiter_calls = 0;
static _Atomic uint64_t hami_probe_waited_calls = 0;
static _Atomic uint64_t hami_probe_sleep_calls = 0;
static _Atomic uint64_t hami_probe_wait_ns = 0;

static inline void hami_probe_record_limiter_call(void) {
  atomic_fetch_add_explicit(&hami_probe_limiter_calls, 1, memory_order_relaxed);
}

static inline void hami_probe_record_wait(uint64_t sleep_calls, uint64_t wait_ns) {
  atomic_fetch_add_explicit(&hami_probe_waited_calls, 1, memory_order_relaxed);
  atomic_fetch_add_explicit(&hami_probe_sleep_calls, sleep_calls, memory_order_relaxed);
  atomic_fetch_add_explicit(&hami_probe_wait_ns, wait_ns, memory_order_relaxed);
}

static inline int hami_probe_flush(void) {
  const char *path = getenv("HAMI_PROBE_OUTPUT");
  if (path == NULL || path[0] == '\0') {
    return 0;
  }

  char line[512];
  int length = snprintf(
      line, sizeof(line),
      "{\"schema_version\":1,\"pid\":%ld,\"limiter_calls\":%llu,"
      "\"waited_calls\":%llu,\"sleep_calls\":%llu,\"wait_ns\":%llu}\n",
      (long)getpid(),
      (unsigned long long)atomic_load_explicit(&hami_probe_limiter_calls, memory_order_relaxed),
      (unsigned long long)atomic_load_explicit(&hami_probe_waited_calls, memory_order_relaxed),
      (unsigned long long)atomic_load_explicit(&hami_probe_sleep_calls, memory_order_relaxed),
      (unsigned long long)atomic_load_explicit(&hami_probe_wait_ns, memory_order_relaxed));
  if (length <= 0 || (size_t)length >= sizeof(line)) {
    return -1;
  }

  int fd = open(path, O_WRONLY | O_APPEND | O_CREAT, 0644);
  if (fd < 0) {
    return -1;
  }
  ssize_t written = write(fd, line, (size_t)length);
  int close_result = close(fd);
  return written == length && close_result == 0 ? 0 : -1;
}

#endif
