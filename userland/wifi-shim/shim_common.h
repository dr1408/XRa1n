#ifndef XRA1N_WIFI_SHIM_COMMON_H
#define XRA1N_WIFI_SHIM_COMMON_H

#include <CoreFoundation/CoreFoundation.h>
#include <stdbool.h>
#include <stdint.h>
#include <sys/types.h>

bool xrw_cf_equal(CFTypeRef left, CFTypeRef right);
bool xrw_dictionary_bool(CFDictionaryRef dictionary, CFTypeRef key);
bool xrw_read_file(const char *path, CFDataRef *data_out);
bool xrw_atomic_write_data(const char *path, CFDataRef data, mode_t mode);
void xrw_log(const char *format, ...) __attribute__((format(printf, 1, 2)));

#define XRW_INTERPOSE(replacement, replacee)                                  \
  __attribute__((used)) static struct {                                       \
    const void *replacement;                                                  \
    const void *replacee;                                                     \
  } _xrw_interpose_##replacee                                                 \
      __attribute__((section("__DATA,__interpose"))) = {                      \
          (const void *)(uintptr_t)&replacement,                              \
          (const void *)(uintptr_t)&replacee}

#endif
