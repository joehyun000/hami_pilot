#define _POSIX_C_SOURCE 200809L

#include <assert.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

#include "../probe/hami_probe_counter.h"

int main(void) {
  char path[] = "/tmp/hami-probe-counter-XXXXXX";
  int fd = mkstemp(path);
  assert(fd >= 0);
  close(fd);
  assert(setenv("HAMI_PROBE_OUTPUT", path, 1) == 0);

  hami_probe_record_limiter_call();
  hami_probe_record_limiter_call();
  hami_probe_record_wait(2, 12000000);
  assert(hami_probe_flush() == 0);

  FILE *file = fopen(path, "r");
  assert(file != NULL);
  char line[512] = {0};
  assert(fgets(line, sizeof(line), file) != NULL);
  fclose(file);
  unlink(path);

  assert(strstr(line, "\"schema_version\":1") != NULL);
  assert(strstr(line, "\"limiter_calls\":2") != NULL);
  assert(strstr(line, "\"waited_calls\":1") != NULL);
  assert(strstr(line, "\"sleep_calls\":2") != NULL);
  assert(strstr(line, "\"wait_ns\":12000000") != NULL);
  return 0;
}
