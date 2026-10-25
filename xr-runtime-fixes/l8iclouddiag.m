// Read-only, opt-in sign-in diagnostic for iPhone11,8 / 22H374.
// This image has no interposes or swizzles. It reports the current process's
// persona view and two sandbox lookup decisions; it never issues an extension,
// changes a profile, or contacts the named Mach services.
#import <Foundation/Foundation.h>
#import <objc/message.h>
#import <objc/runtime.h>
#import <dispatch/dispatch.h>
#import <os/log.h>

#include <dlfcn.h>
#include <errno.h>
#include <fcntl.h>
#include <limits.h>
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <time.h>
#include <unistd.h>

static const char *const kMarker = "/private/var/jb/.xra1n-icloud-diag";
static const char *const kTargets[] = {
    "AAUIFollowUpExtension", "Preferences", "MobileSafari",
};

static BOOL armed(void) {
    struct stat st;
    return lstat(kMarker, &st) == 0 && S_ISREG(st.st_mode) && st.st_uid == 0 &&
           (st.st_mode & (S_IWGRP | S_IWOTH)) == 0;
}

// A small, private fallback when USB syslog is unavailable. Each sandboxed
// process writes only to its own temporary directory; no account identifiers
// or persona UUIDs are recorded. Root can retrieve these files over Wi-Fi SSH.
static void fileRecord(const char *format, ...) {
    NSString *temp = NSTemporaryDirectory();
    if (!temp.length) return;
    char path[PATH_MAX];
    int pathLength = snprintf(path, sizeof(path), "%s/xra1n-iclouddiag-%d.log",
                              temp.fileSystemRepresentation, getpid());
    if (pathLength < 0 || (size_t)pathLength >= sizeof(path)) return;
    int fd = open(path, O_WRONLY | O_CREAT | O_APPEND | O_CLOEXEC | O_NOFOLLOW, 0600);
    if (fd < 0) return;

    char line[768];
    int prefix = snprintf(line, sizeof(line), "%ld %s pid=%d ",
                          (long)time(NULL), getprogname(), getpid());
    if (prefix < 0 || (size_t)prefix >= sizeof(line)) { close(fd); return; }
    va_list args;
    va_start(args, format);
    (void)vsnprintf(line + prefix, sizeof(line) - (size_t)prefix, format, args);
    va_end(args);
    size_t length = strnlen(line, sizeof(line));
    if (length == sizeof(line)) length--;
    line[length++] = '\n';
    (void)write(fd, line, length);
    close(fd);
}

// KVC boxes the scalar uid/gid/type accessors. The private classes are only
// queried if already loaded in the target; this probe does not dlopen them.
static id property(id object, NSString *key) {
    if (!object || ![object respondsToSelector:NSSelectorFromString(key)]) return nil;
    @try { return [object valueForKey:key]; }
    @catch (NSException *exception) { (void)exception; return nil; }
}

static long numeric(id value) {
    return [value respondsToSelector:@selector(longValue)] ? [value longValue] : -1;
}

static void reportPersona(const char *stage, const char *source, id persona) {
    id unique = property(persona, @"userPersonaUniqueString");
    BOOL hasUnique = [unique isKindOfClass:[NSString class]] && [unique length] != 0;
    long type = numeric(property(persona, @"userPersonaType"));
    long uid = numeric(property(persona, @"uid"));
    long gid = numeric(property(persona, @"gid"));
    os_log_error(OS_LOG_DEFAULT,
                 "xra1n-iclouddiag stage=%{public}s source=%{public}s present=%d "
                 "has-unique=%d type=%ld uid=%ld gid=%ld",
                 stage, source, persona != nil, hasUnique, type, uid, gid);
    fileRecord("stage=%s source=%s present=%d has-unique=%d type=%ld uid=%ld gid=%ld",
               stage, source, persona != nil, hasUnique, type, uid, gid);
}

static void reportSandbox(const char *stage, const char *service) {
    typedef int (*sandbox_check_fn)(pid_t, const char *, int, ...);
    sandbox_check_fn check = (sandbox_check_fn)dlsym(RTLD_DEFAULT, "sandbox_check");
    const int *noReport = (const int *)dlsym(RTLD_DEFAULT, "SANDBOX_CHECK_NO_REPORT");
    if (!check || !noReport) {
        os_log_error(OS_LOG_DEFAULT,
                     "xra1n-iclouddiag stage=%{public}s mach=%{public}s query-unavailable",
                     stage, service);
        fileRecord("stage=%s mach=%s query-unavailable", stage, service);
        return;
    }
    // SANDBOX_FILTER_GLOBAL_NAME is 2. NO_REPORT avoids creating a second
    // violation merely by asking what the profile would do.
    errno = 0;
    int result = check(getpid(), "mach-lookup", 2 | *noReport, service);
    int savedErrno = errno;
    os_log_error(OS_LOG_DEFAULT,
                 "xra1n-iclouddiag stage=%{public}s mach=%{public}s "
                 "sandbox-result=%d errno=%d",
                 stage, service, result, savedErrno);
    fileRecord("stage=%s mach=%s sandbox-result=%d errno=%d",
               stage, service, result, savedErrno);
}

static void reportAttributes(const char *stage, NSUInteger type) {
    Class cls = objc_lookUpClass("UMUserPersonaAttributes");
    SEL sel = sel_registerName("personaAttributesForPersonaType:withError:");
    if (!cls || ![cls respondsToSelector:sel]) {
        os_log_error(OS_LOG_DEFAULT,
                     "xra1n-iclouddiag stage=%{public}s attributes-type=%lu unavailable",
                     stage, (unsigned long)type);
        fileRecord("stage=%s attributes-type=%lu unavailable", stage, (unsigned long)type);
        return;
    }
    NSError *error = nil;
    id attrs = nil;
    @try {
        attrs = ((id (*)(id, SEL, NSUInteger, NSError **))objc_msgSend)(
            cls, sel, type, &error);
    } @catch (NSException *exception) {
        (void)exception;
        os_log_error(OS_LOG_DEFAULT,
                     "xra1n-iclouddiag stage=%{public}s attributes-type=%lu exception",
                     stage, (unsigned long)type);
        fileRecord("stage=%s attributes-type=%lu exception", stage, (unsigned long)type);
        return;
    }
    id unique = property(attrs, @"userPersonaUniqueString");
    BOOL hasUnique = [unique isKindOfClass:[NSString class]] && [unique length] != 0;
    os_log_error(OS_LOG_DEFAULT,
                 "xra1n-iclouddiag stage=%{public}s attributes-type=%lu "
                 "present=%d has-unique=%d error=%ld",
                 stage, (unsigned long)type, attrs != nil, hasUnique,
                 error ? (long)error.code : 0L);
    fileRecord("stage=%s attributes-type=%lu present=%d has-unique=%d error=%ld",
               stage, (unsigned long)type, attrs != nil, hasUnique,
               error ? (long)error.code : 0L);
}

static void snapshot(const char *stage) {
    if (!armed()) return; // Removing the marker stops future samples.
    @autoreleasepool {
        Class personaClass = objc_lookUpClass("UMUserPersona");
        SEL current = sel_registerName("currentPersona");
        id currentPersona = personaClass && [personaClass respondsToSelector:current]
            ? ((id (*)(id, SEL))objc_msgSend)(personaClass, current) : nil;

        Class managerClass = objc_lookUpClass("UMUserManager");
        SEL shared = sel_registerName("sharedManager");
        id manager = managerClass && [managerClass respondsToSelector:shared]
            ? ((id (*)(id, SEL))objc_msgSend)(managerClass, shared) : nil;
        id managerPersona = manager && [manager respondsToSelector:current]
            ? ((id (*)(id, SEL))objc_msgSend)(manager, current) : nil;

        os_log_error(OS_LOG_DEFAULT,
                     "xra1n-iclouddiag stage=%{public}s pid=%d uid=%u euid=%u "
                     "persona-class=%d manager-class=%d",
                     stage, getpid(), getuid(), geteuid(),
                     personaClass != Nil, managerClass != Nil);
        fileRecord("stage=%s uid=%u euid=%u persona-class=%d manager-class=%d",
                   stage, getuid(), geteuid(), personaClass != Nil, managerClass != Nil);
        reportPersona(stage, "UMUserPersona.currentPersona", currentPersona);
        reportPersona(stage, "UMUserManager.currentPersona", managerPersona);
        reportAttributes(stage, 0);
        reportAttributes(stage, 2);
        reportAttributes(stage, 5);
        reportSandbox(stage, "com.apple.adid");
        reportSandbox(stage, "com.apple.ak.auth.xpc"); // signed-allow control
    }
}

__attribute__((constructor))
static void beginDiagnostic(void) {
    const char *name = getprogname();
    BOOL selected = NO;
    if (name) {
        for (size_t index = 0; index < sizeof(kTargets) / sizeof(kTargets[0]); index++) {
            if (strcmp(name, kTargets[index]) == 0) { selected = YES; break; }
        }
    }
    if (!selected || !armed()) return;
    dispatch_after(dispatch_time(DISPATCH_TIME_NOW, NSEC_PER_SEC),
                   dispatch_get_global_queue(QOS_CLASS_UTILITY, 0), ^{ snapshot("start"); });
    // The sign-in request can happen long after extension launch. Sampling
    // again distinguishes a missing initial persona from a later transition.
    dispatch_after(dispatch_time(DISPATCH_TIME_NOW, 5 * NSEC_PER_SEC),
                   dispatch_get_global_queue(QOS_CLASS_UTILITY, 0), ^{ snapshot("5s"); });
    dispatch_after(dispatch_time(DISPATCH_TIME_NOW, 45 * NSEC_PER_SEC),
                   dispatch_get_global_queue(QOS_CLASS_UTILITY, 0), ^{ snapshot("45s"); });
    dispatch_after(dispatch_time(DISPATCH_TIME_NOW, 75 * NSEC_PER_SEC),
                   dispatch_get_global_queue(QOS_CLASS_UTILITY, 0), ^{ snapshot("75s"); });
}
