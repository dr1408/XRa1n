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
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <unistd.h>

static const char *const kMarker = "/private/var/jb/.xra1n-icloud-diag";
static const char *const kTarget = "AAUIFollowUpExtension";

static BOOL armed(void) {
    struct stat st;
    return lstat(kMarker, &st) == 0 && S_ISREG(st.st_mode) && st.st_uid == 0 &&
           (st.st_mode & (S_IWGRP | S_IWOTH)) == 0;
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
    os_log_error(OS_LOG_DEFAULT,
                 "xra1n-iclouddiag stage=%{public}s source=%{public}s present=%d "
                 "has-unique=%d type=%ld uid=%ld gid=%ld",
                 stage, source, persona != nil, hasUnique,
                 numeric(property(persona, @"userPersonaType")),
                 numeric(property(persona, @"uid")),
                 numeric(property(persona, @"gid")));
}

static void reportSandbox(const char *stage, const char *service) {
    typedef int (*sandbox_check_fn)(pid_t, const char *, int, ...);
    sandbox_check_fn check = (sandbox_check_fn)dlsym(RTLD_DEFAULT, "sandbox_check");
    const int *noReport = (const int *)dlsym(RTLD_DEFAULT, "SANDBOX_CHECK_NO_REPORT");
    if (!check || !noReport) {
        os_log_error(OS_LOG_DEFAULT,
                     "xra1n-iclouddiag stage=%{public}s mach=%{public}s query-unavailable",
                     stage, service);
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
}

static void reportAttributes(const char *stage, NSUInteger type) {
    Class cls = objc_lookUpClass("UMUserPersonaAttributes");
    SEL sel = sel_registerName("personaAttributesForPersonaType:withError:");
    if (!cls || ![cls respondsToSelector:sel]) {
        os_log_error(OS_LOG_DEFAULT,
                     "xra1n-iclouddiag stage=%{public}s attributes-type=%lu unavailable",
                     stage, (unsigned long)type);
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
        return;
    }
    id unique = property(attrs, @"userPersonaUniqueString");
    BOOL hasUnique = [unique isKindOfClass:[NSString class]] && [unique length] != 0;
    os_log_error(OS_LOG_DEFAULT,
                 "xra1n-iclouddiag stage=%{public}s attributes-type=%lu "
                 "present=%d has-unique=%d error=%ld",
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
    if (!name || strcmp(name, kTarget) != 0 || !armed()) return;
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
