/*
 * File-backed AirPort credential store for XR 22H374 wifid.
 *
 * This follows 0xjohnnydev's Wi-Fi-only SecItem interposition design, adapted
 * to the query contract recovered from this exact wifid: data and attribute
 * reads, wildcard attribute enumeration, synchronizable state, system-
 * keychain selection, updates and deletion.  Unrelated keychain operations
 * always pass through to Security.framework.
 */
#include "shim_common.h"

#include <Security/Security.h>
#include <pthread.h>
#include <stdlib.h>

#define STORE_PATH \
  "/private/var/preferences/SystemConfiguration/com.xra1n.wifi-passwords.plist"

static pthread_mutex_t store_mutex = PTHREAD_MUTEX_INITIALIZER;
static CFStringRef kData = CFSTR("data");
static CFStringRef kAccount = CFSTR("account");
static CFStringRef kSync = CFSTR("synchronizable");
static CFStringRef kSystem = CFSTR("system-keychain");
static CFStringRef kModified = CFSTR("modified");

static bool query_matches(CFDictionaryRef query) {
  if (query == NULL || CFGetTypeID(query) != CFDictionaryGetTypeID()) return false;
  CFTypeRef service = CFDictionaryGetValue(query, kSecAttrService);
  CFTypeRef item_class = CFDictionaryGetValue(query, kSecClass);
  return xrw_cf_equal(service, CFSTR("AirPort")) &&
         (item_class == NULL || xrw_cf_equal(item_class, kSecClassGenericPassword));
}

static CFStringRef query_account(CFDictionaryRef query) {
  CFTypeRef value = query ? CFDictionaryGetValue(query, kSecAttrAccount) : NULL;
  return value && CFGetTypeID(value) == CFStringGetTypeID() ? value : NULL;
}

static bool query_system(CFDictionaryRef query) {
  return xrw_dictionary_bool(query, kSecUseSystemKeychain);
}

static CFStringRef entry_key(CFStringRef account, bool system) {
  return CFStringCreateWithFormat(kCFAllocatorDefault, NULL, CFSTR("%@\x1f%d"),
                                  account, system ? 1 : 0);
}

static CFMutableDictionaryRef load_store(void) {
  CFDataRef bytes = NULL;
  if (!xrw_read_file(STORE_PATH, &bytes))
    return CFDictionaryCreateMutable(kCFAllocatorDefault, 0,
                                     &kCFTypeDictionaryKeyCallBacks,
                                     &kCFTypeDictionaryValueCallBacks);
  CFErrorRef error = NULL;
  CFPropertyListRef plist = CFPropertyListCreateWithData(
      kCFAllocatorDefault, bytes, kCFPropertyListMutableContainersAndLeaves,
      NULL, &error);
  CFRelease(bytes);
  if (error) CFRelease(error);
  if (!plist || CFGetTypeID(plist) != CFDictionaryGetTypeID()) {
    if (plist) CFRelease(plist);
    xrw_log("credential store is invalid");
    return NULL;
  }
  return (CFMutableDictionaryRef)plist;
}

static bool write_store(CFDictionaryRef store) {
  CFErrorRef error = NULL;
  CFDataRef bytes = CFPropertyListCreateData(kCFAllocatorDefault, store,
      kCFPropertyListBinaryFormat_v1_0, 0, &error);
  if (error) CFRelease(error);
  if (!bytes) return false;
  bool ok = xrw_atomic_write_data(STORE_PATH, bytes, 0600);
  CFRelease(bytes);
  return ok;
}

static CFMutableDictionaryRef new_entry(CFStringRef account, CFDataRef data,
                                        bool sync, bool system) {
  CFMutableDictionaryRef entry = CFDictionaryCreateMutable(
      kCFAllocatorDefault, 0, &kCFTypeDictionaryKeyCallBacks,
      &kCFTypeDictionaryValueCallBacks);
  if (!entry) return NULL;
  CFDateRef now = CFDateCreate(kCFAllocatorDefault, CFAbsoluteTimeGetCurrent());
  CFDictionarySetValue(entry, kAccount, account);
  CFDictionarySetValue(entry, kData, data);
  CFDictionarySetValue(entry, kSync, sync ? kCFBooleanTrue : kCFBooleanFalse);
  CFDictionarySetValue(entry, kSystem, system ? kCFBooleanTrue : kCFBooleanFalse);
  if (now) { CFDictionarySetValue(entry, kModified, now); CFRelease(now); }
  return entry;
}

static CFDictionaryRef find_entry(CFDictionaryRef store, CFStringRef account,
                                  bool system) {
  CFStringRef key = entry_key(account, system);
  CFTypeRef value = key ? CFDictionaryGetValue(store, key) : NULL;
  if (key) CFRelease(key);
  return value && CFGetTypeID(value) == CFDictionaryGetTypeID() ? value : NULL;
}

static CFMutableDictionaryRef result_attributes(CFDictionaryRef entry,
                                                bool wants_data) {
  CFMutableDictionaryRef result = CFDictionaryCreateMutable(
      kCFAllocatorDefault, 0, &kCFTypeDictionaryKeyCallBacks,
      &kCFTypeDictionaryValueCallBacks);
  if (!result) return NULL;
  CFTypeRef account = CFDictionaryGetValue(entry, kAccount);
  CFTypeRef data = CFDictionaryGetValue(entry, kData);
  CFTypeRef sync = CFDictionaryGetValue(entry, kSync);
  CFTypeRef modified = CFDictionaryGetValue(entry, kModified);
  CFDictionarySetValue(result, kSecClass, kSecClassGenericPassword);
  CFDictionarySetValue(result, kSecAttrService, CFSTR("AirPort"));
  if (account) CFDictionarySetValue(result, kSecAttrAccount, account);
  if (sync) CFDictionarySetValue(result, kSecAttrSynchronizable, sync);
  if (modified) CFDictionarySetValue(result, kSecAttrModificationDate, modified);
  if (wants_data && data) CFDictionarySetValue(result, kSecValueData, data);
  return result;
}

OSStatus xrw_SecItemCopyMatching(CFDictionaryRef query, CFTypeRef *result) {
  if (!query_matches(query)) return SecItemCopyMatching(query, result);
  if (result) *result = NULL;
  bool wants_data = xrw_dictionary_bool(query, kSecReturnData);
  bool wants_attrs = xrw_dictionary_bool(query, kSecReturnAttributes);
  bool system = query_system(query);
  CFStringRef account = query_account(query);

  pthread_mutex_lock(&store_mutex);
  CFMutableDictionaryRef store = load_store();
  CFTypeRef produced = NULL;
  if (store && account) {
    CFDictionaryRef entry = find_entry(store, account, system);
    if (entry) {
      if (wants_attrs) produced = result_attributes(entry, wants_data);
      else if (wants_data) {
        CFTypeRef data = CFDictionaryGetValue(entry, kData);
        if (data && CFGetTypeID(data) == CFDataGetTypeID()) produced = CFRetain(data);
      }
    }
  } else if (store && !account && wants_attrs &&
             xrw_cf_equal(CFDictionaryGetValue(query, kSecMatchLimit),
                          kSecMatchLimitAll)) {
    CFIndex count = CFDictionaryGetCount(store);
    const void **values = count ? calloc((size_t)count, sizeof(*values)) : NULL;
    CFMutableArrayRef array = CFArrayCreateMutable(kCFAllocatorDefault, 0,
                                                   &kCFTypeArrayCallBacks);
    if (values && array) {
      CFDictionaryGetKeysAndValues(store, NULL, values);
      for (CFIndex i = 0; i < count; i++) {
        if (CFGetTypeID(values[i]) != CFDictionaryGetTypeID()) continue;
        if (system && !xrw_dictionary_bool(values[i], kSystem)) continue;
        CFDictionaryRef attrs = result_attributes(values[i], wants_data);
        if (attrs) { CFArrayAppendValue(array, attrs); CFRelease(attrs); }
      }
      if (CFArrayGetCount(array)) produced = array;
      else CFRelease(array);
    } else if (array) CFRelease(array);
    free(values);
  }
  if (store) CFRelease(store);
  pthread_mutex_unlock(&store_mutex);

  if (produced) {
    if (result) *result = produced; else CFRelease(produced);
    return errSecSuccess;
  }
  return SecItemCopyMatching(query, result);
}

OSStatus xrw_SecItemAdd(CFDictionaryRef attributes, CFTypeRef *result) {
  if (!query_matches(attributes) || !query_account(attributes))
    return SecItemAdd(attributes, result);
  if (result) *result = NULL;
  CFStringRef account = query_account(attributes);
  CFTypeRef data = CFDictionaryGetValue(attributes, kSecValueData);
  if (!data || CFGetTypeID(data) != CFDataGetTypeID()) return errSecParam;
  bool system = query_system(attributes);
  bool sync = xrw_dictionary_bool(attributes, kSecAttrSynchronizable);
  pthread_mutex_lock(&store_mutex);
  CFMutableDictionaryRef store = load_store();
  CFStringRef key = entry_key(account, system);
  OSStatus status = errSecNotAvailable;
  if (store && key && CFDictionaryContainsKey(store, key)) status = errSecDuplicateItem;
  else if (store && key) {
    CFMutableDictionaryRef entry = new_entry(account, data, sync, system);
    if (entry) {
      CFDictionarySetValue(store, key, entry);
      status = write_store(store) ? errSecSuccess : errSecNotAvailable;
      CFRelease(entry);
    }
  }
  if (key) CFRelease(key);
  if (store) CFRelease(store);
  pthread_mutex_unlock(&store_mutex);
  if (status == errSecSuccess) xrw_log("stored one AirPort credential");
  return status;
}

OSStatus xrw_SecItemUpdate(CFDictionaryRef query, CFDictionaryRef changes) {
  if (!query_matches(query) || !query_account(query))
    return SecItemUpdate(query, changes);
  bool system = query_system(query);
  CFStringRef account = query_account(query);
  pthread_mutex_lock(&store_mutex);
  CFMutableDictionaryRef store = load_store();
  CFStringRef key = entry_key(account, system);
  CFDictionaryRef old = store && key ? CFDictionaryGetValue(store, key) : NULL;
  OSStatus status = errSecItemNotFound;
  if (old && CFGetTypeID(old) == CFDictionaryGetTypeID()) {
    CFMutableDictionaryRef entry = CFDictionaryCreateMutableCopy(kCFAllocatorDefault, 0, old);
    CFTypeRef data = CFDictionaryGetValue(changes, kSecValueData);
    CFTypeRef sync = CFDictionaryGetValue(changes, kSecAttrSynchronizable);
    CFDateRef now = CFDateCreate(kCFAllocatorDefault, CFAbsoluteTimeGetCurrent());
    if (data && CFGetTypeID(data) == CFDataGetTypeID()) CFDictionarySetValue(entry, kData, data);
    if (sync && CFGetTypeID(sync) == CFBooleanGetTypeID()) CFDictionarySetValue(entry, kSync, sync);
    if (now) { CFDictionarySetValue(entry, kModified, now); CFRelease(now); }
    CFDictionarySetValue(store, key, entry);
    status = write_store(store) ? errSecSuccess : errSecNotAvailable;
    CFRelease(entry);
  }
  if (key) CFRelease(key);
  if (store) CFRelease(store);
  pthread_mutex_unlock(&store_mutex);
  return status == errSecItemNotFound ? SecItemUpdate(query, changes) : status;
}

OSStatus xrw_SecItemDelete(CFDictionaryRef query) {
  if (!query_matches(query)) return SecItemDelete(query);
  CFStringRef account = query_account(query);
  bool system = query_system(query), removed = false, written = false;
  pthread_mutex_lock(&store_mutex);
  CFMutableDictionaryRef store = load_store();
  if (store && account) {
    CFStringRef key = entry_key(account, system);
    if (key && CFDictionaryContainsKey(store, key)) {
      CFDictionaryRemoveValue(store, key); removed = true;
      written = write_store(store);
    }
    if (key) CFRelease(key);
  } else if (store && !account) {
    removed = CFDictionaryGetCount(store) != 0;
    CFDictionaryRemoveAllValues(store);
    written = !removed || write_store(store);
  }
  if (store) CFRelease(store);
  pthread_mutex_unlock(&store_mutex);
  OSStatus real = SecItemDelete(query);
  if (!removed) return real;
  return written ? errSecSuccess : errSecNotAvailable;
}

__attribute__((constructor)) static void loaded(void) {
  xrw_log("XR 22H374 AirPort credential shim loaded");
}

XRW_INTERPOSE(xrw_SecItemCopyMatching, SecItemCopyMatching);
XRW_INTERPOSE(xrw_SecItemAdd, SecItemAdd);
XRW_INTERPOSE(xrw_SecItemUpdate, SecItemUpdate);
XRW_INTERPOSE(xrw_SecItemDelete, SecItemDelete);
