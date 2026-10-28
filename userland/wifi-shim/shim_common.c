#include "shim_common.h"

#include <fcntl.h>
#include <limits.h>
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <syslog.h>
#include <unistd.h>

#define XRW_MAX_FILE_SIZE (1024U * 1024U)

bool xrw_cf_equal(CFTypeRef left, CFTypeRef right) {
  return left != NULL && right != NULL &&
         (left == right || CFEqual(left, right));
}

bool xrw_dictionary_bool(CFDictionaryRef dictionary, CFTypeRef key) {
  return dictionary != NULL && key != NULL &&
         xrw_cf_equal(CFDictionaryGetValue(dictionary, key), kCFBooleanTrue);
}

bool xrw_read_file(const char *path, CFDataRef *data_out) {
  int fd = -1;
  struct stat st;
  UInt8 *bytes = NULL;
  bool ok = false;
  if (path == NULL || data_out == NULL) return false;
  *data_out = NULL;
  fd = open(path, O_RDONLY | O_CLOEXEC | O_NOFOLLOW);
  if (fd < 0 || fstat(fd, &st) != 0 || !S_ISREG(st.st_mode) || st.st_size <= 0 ||
      (uint64_t)st.st_size > XRW_MAX_FILE_SIZE) goto out;
  bytes = malloc((size_t)st.st_size);
  if (bytes == NULL) goto out;
  size_t done = 0;
  while (done < (size_t)st.st_size) {
    ssize_t count = read(fd, bytes + done, (size_t)st.st_size - done);
    if (count <= 0) goto out;
    done += (size_t)count;
  }
  *data_out = CFDataCreate(kCFAllocatorDefault, bytes, st.st_size);
  ok = *data_out != NULL;
out:
  free(bytes);
  if (fd >= 0) close(fd);
  return ok;
}

bool xrw_atomic_write_data(const char *path, CFDataRef data, mode_t mode) {
  char temporary[PATH_MAX];
  int fd = -1;
  bool ok = false;
  if (path == NULL || data == NULL || CFGetTypeID(data) != CFDataGetTypeID())
    return false;
  if (snprintf(temporary, sizeof(temporary), "%s.tmp.%d", path, getpid()) >=
      (int)sizeof(temporary)) return false;
  unlink(temporary);
  fd = open(temporary, O_WRONLY | O_CREAT | O_EXCL | O_CLOEXEC | O_NOFOLLOW, mode);
  if (fd < 0 || fchmod(fd, mode) != 0) goto out;
  const UInt8 *bytes = CFDataGetBytePtr(data);
  size_t length = (size_t)CFDataGetLength(data), done = 0;
  while (done < length) {
    ssize_t count = write(fd, bytes + done, length - done);
    if (count <= 0) goto out;
    done += (size_t)count;
  }
  if (fsync(fd) != 0 || close(fd) != 0) { fd = -1; goto out; }
  fd = -1;
  if (rename(temporary, path) != 0 || chmod(path, mode) != 0) goto out;
  ok = true;
out:
  if (fd >= 0) close(fd);
  if (!ok) unlink(temporary);
  return ok;
}

void xrw_log(const char *format, ...) {
  char message[512];
  va_list args;
  va_start(args, format);
  vsnprintf(message, sizeof(message), format, args);
  va_end(args);
  syslog(LOG_NOTICE, "xra1n[wifi]: %s", message);
}
