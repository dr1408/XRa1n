/*
 * l8localauth.dylib - make passcode confirmations succeed on the SEP-less boot.
 *
 * Every "enter your passcode to confirm" prompt (Trust this computer, install a
 * marketplace / alternative app store, authorise a setting) ends in
 * LocalAuthentication asking the Secure Enclave, via ACM, to verify the
 * passcode. With no SEP, ACM returns -3 before any sheet appears, and the app
 * sees com.apple.LocalAuthentication/-1000, which looks like the user cancelled.
 *
 * This is the general form of the per-marketplace workaround from the lhook
 * branch (marketplace-confirmation.m) and of upstream's lockdownd-only
 * l8pair_auth.m. It hooks LAContext in whatever process loads it and, only for
 * the exact SEP-less ACM failure, reports LAErrorPasscodeNotSet instead. That
 * is truthful on this boot (there is no SEP-backed passcode) and sends the UI
 * down its existing no-passcode branch, so the action proceeds. It is not real
 * passcode authorisation; the no-SEP boot cannot produce that.
 *
 * Hooked, each only if present with the expected arity (selectors from the
 * 23H30 LocalAuthentication):
 *   evaluatePolicy:options:reply:         async policy path (public calls it)
 *   evaluatePolicy:options:error:         synchronous policy path (daemons)
 *   canEvaluatePolicy:error:
 *   evaluateAccessControl:operation:localizedReason:reply:   public, keychain
 *   evaluateAccessControl:operation:options:reply:           private async
 *   evaluateAccessControl:operation:options:error:           private sync
 * A public method that calls a hooked private one is translated once: the
 * translated error no longer matches the SEP-less signature.
 *
 * Built without ARC on purpose: the reply wrappers forward the first block
 * argument untouched, so its exact type (object or BOOL) cannot be
 * mis-retained.
 *
 * Scope and safety:
 *   - Dormant unless the root-owned marker /var/jb/.liter8-localauth exists, so
 *     it is a runtime kill switch (rm it to restore stock behaviour).
 *   - Translates ONLY the exact ACM -3 / LA -1000 signature. Every success,
 *     every other policy outcome and every other error pass through unchanged.
 *   - Changes no executable pages and needs no task port, so it works whether
 *     or not the code-signing-invalid kernel patches are present.
 *   - Does not link LocalAuthentication. The filter reaches every Objective-C
 *     process, but most never use LAContext, so the framework is not pulled
 *     into them. The hook is installed when LAContext appears: immediately if
 *     the host already loaded the framework, otherwise from an objc image-load
 *     callback, which runs after objc has registered the new image's classes.
 *
 * Install it as an ElleKit tweak: l8localauth.dylib and l8localauth.plist in
 * /var/jb/usr/lib/TweakInject. lhook's TweakLoader then loads it into every
 * Objective-C process (the filter is Foundation). It is a method swizzle, so it
 * needs no code-signing patch. Build: device/localauthfix/build.sh.
 *
 * Self-test (no device, no injection):
 *   clang -DLITER8_LOCALAUTH_TEST -framework Foundation \
 *       -framework LocalAuthentication l8localauth.m -o t && ./t
 */

#import <Foundation/Foundation.h>
#import <objc/runtime.h>

#include <mach-o/dyld.h>
#include <mach-o/loader.h>
#include <os/log.h>
#include <stdatomic.h>
#include <sys/stat.h>
#include <string.h>

/* LocalAuthentication's public values, spelled out so nothing links against
 * the framework (LAErrorDomain is a symbol, not a macro). */
static NSString *const kL8LADomain = @"com.apple.LocalAuthentication";
static const NSInteger kL8PasscodeNotSet = -5;
/* ACM's status when there is no credential to verify against, which is what a
 * SEP-less boot produces. Named so the matcher reads as a status comparison
 * rather than a string that happens to appear in the message. */
static const NSInteger kL8ACMNoCredential = -3;

typedef void (^PolicyReply)(id result, NSError *error);
typedef void (^AccessReply)(BOOL success, NSError *error);
typedef void (*EvaluateIMP)(id, SEL, NSInteger, NSDictionary *, PolicyReply);
typedef id (*EvaluateSyncIMP)(id, SEL, NSInteger, NSDictionary *, NSError **);
typedef BOOL (*CanEvaluateIMP)(id, SEL, NSInteger, NSError **);
typedef void (*AccessIMP)(id, SEL, CFTypeRef, NSInteger, NSString *, AccessReply);
typedef void (*AccessOptionsIMP)(id, SEL, CFTypeRef, NSInteger, NSDictionary *, PolicyReply);
typedef id (*AccessSyncIMP)(id, SEL, CFTypeRef, NSInteger, NSDictionary *, NSError **);

static EvaluateIMP gOriginalEvaluate;
static EvaluateSyncIMP gOriginalEvaluateSync;
static CanEvaluateIMP gOriginalCanEvaluate;
static AccessIMP gOriginalAccess;
static AccessOptionsIMP gOriginalAccessOptions;
static AccessSyncIMP gOriginalAccessSync;

static const char *kMarker = "/private/var/jb/.liter8-localauth";

static BOOL marker_enabled(void) {
    struct stat st;
    if (lstat(kMarker, &st) != 0) return NO;
    return S_ISREG(st.st_mode) && st.st_uid == 0 &&
           (st.st_mode & (S_IWGRP | S_IWOTH)) == 0;
}

/* The SEP-less condition, not a specific prompt: ACM could not verify the
 * credential because there is no SEP to verify it against. Matching the
 * signature rather than a policy number is what makes this general across every
 * passcode prompt, while staying narrow enough to leave real failures alone. */
static BOOL is_sep_less_acm_failure(NSError *error) {
    if (error == nil || error.code != -1000 || ![error.domain isEqualToString:kL8LADomain]) {
        return NO;
    }
    id detail = error.userInfo[NSDebugDescriptionErrorKey];
    if (![detail isKindOfClass:[NSString class]]) return NO;
    NSString *text = (NSString *)detail;
    if (![text containsString:@"ACM verification"]) return NO;
    // "ACM verification of <policy> on ACMContext N failed: -3"
    //
    // Parse the status rather than matching its text: "failed: -3" is a prefix
    // of "failed: -30", so a substring test also claims unrelated ACM failures
    // and rewrites them into the bypass. scanInteger consumes the whole run, so
    // -30 scans as -30 and is rejected by the comparison.
    NSRange marker = [text rangeOfString:@"failed: "];
    if (marker.location == NSNotFound) return NO;
    NSScanner *scanner =
        [NSScanner scannerWithString:[text substringFromIndex:NSMaxRange(marker)]];
    NSInteger status = 0;
    return [scanner scanInteger:&status] && status == kL8ACMNoCredential;
}

static NSError *translated(NSError *error) {
    if (!is_sep_less_acm_failure(error)) return error;
    os_log_error(OS_LOG_DEFAULT,
                 "l8localauth: SEP-less ACM -3 -> passcode-not-set (pid %d)", getpid());
    return [NSError errorWithDomain:kL8LADomain code:kL8PasscodeNotSet userInfo:nil];
}

static void l8_evaluate(id self, SEL sel, NSInteger policy,
                        NSDictionary *options, PolicyReply reply) {
    if (reply == nil || !marker_enabled()) {
        gOriginalEvaluate(self, sel, policy, options, reply);
        return;
    }
    gOriginalEvaluate(self, sel, policy, options, ^(id result, NSError *error) {
        reply(result, result != nil ? error : translated(error));
    });
}

/* The three synchronous wrappers check the marker before calling through, so a
 * disabled shim forwards the caller's own error pointer, NULL included.
 * Substituting &native regardless meant a caller that asked for no error
 * reporting had the original invoked as though it had asked for it, which is a
 * difference the kill switch exists to remove. The async wrappers already
 * short-circuit the same way. The extra lstat is immaterial here: these are
 * user-initiated authentication prompts, not a hot path. */
static id l8_evaluateSync(id self, SEL sel, NSInteger policy,
                          NSDictionary *options, NSError **error) {
    if (!marker_enabled()) return gOriginalEvaluateSync(self, sel, policy, options, error);
    NSError *native = nil;
    id result = gOriginalEvaluateSync(self, sel, policy, options, error ? &native : NULL);
    if (error) *error = result != nil ? native : translated(native);
    return result;
}

static BOOL l8_canEvaluate(id self, SEL sel, NSInteger policy, NSError **error) {
    if (!marker_enabled()) return gOriginalCanEvaluate(self, sel, policy, error);
    NSError *native = nil;
    BOOL ok = gOriginalCanEvaluate(self, sel, policy, error ? &native : NULL);
    if (error) *error = ok ? native : translated(native);
    return ok;
}

static void l8_access(id self, SEL sel, CFTypeRef accessControl, NSInteger operation,
                      NSString *reason, AccessReply reply) {
    if (reply == nil || !marker_enabled()) {
        gOriginalAccess(self, sel, accessControl, operation, reason, reply);
        return;
    }
    gOriginalAccess(self, sel, accessControl, operation, reason, ^(BOOL success, NSError *error) {
        reply(success, success ? error : translated(error));
    });
}

static void l8_accessOptions(id self, SEL sel, CFTypeRef accessControl, NSInteger operation,
                             NSDictionary *options, PolicyReply reply) {
    if (reply == nil || !marker_enabled()) {
        gOriginalAccessOptions(self, sel, accessControl, operation, options, reply);
        return;
    }
    gOriginalAccessOptions(self, sel, accessControl, operation, options, ^(id result, NSError *error) {
        reply(result, result != nil ? error : translated(error));
    });
}

static id l8_accessSync(id self, SEL sel, CFTypeRef accessControl, NSInteger operation,
                        NSDictionary *options, NSError **error) {
    if (!marker_enabled()) {
        return gOriginalAccessSync(self, sel, accessControl, operation, options, error);
    }
    NSError *native = nil;
    id result = gOriginalAccessSync(self, sel, accessControl, operation, options,
                                    error ? &native : NULL);
    if (error) *error = result != nil ? native : translated(native);
    return result;
}

/* A method's type encoding with the frame offsets stripped, leaving only the
 * type letters. The offsets are frame-layout detail and say nothing about
 * whether the casts above are valid; the letters are exactly what they depend
 * on, and dropping the numbers keeps the expectation readable. */
static void encoding_letters(const char *encoding, char *out, size_t size) {
    size_t n = 0;
    for (const char *p = encoding; p != NULL && *p != '\0' && n + 1 < size; p++) {
        if (*p < '0' || *p > '9') out[n++] = *p;
    }
    out[n] = '\0';
}

/* Swizzle one method if it exists with the expected argument count and the
 * expected type encoding. The original is published before the replacement is
 * installed, so a thread that enters the hook at once never sees a NULL
 * original. Each hook is independent: a selector missing on some build leaves
 * the others working.
 *
 * Arity alone was not enough. These are mostly private selectors, and a private
 * selector can keep its argument count across builds while changing a return,
 * a scalar or a block ABI. Forwarding such a method through the fixed typedefs
 * above corrupts arguments or kills the host process, and the marker does not
 * prevent it because the swizzle happens either way. An unrecognised encoding
 * now leaves the method alone and says so, which costs that one fallback and
 * keeps the process alive. */
static BOOL swap(Class cls, const char *name, unsigned arguments, const char *types,
                 IMP replacement, IMP *original) {
    Method method = class_getInstanceMethod(cls, sel_registerName(name));
    if (method == NULL || method_getNumberOfArguments(method) != arguments) return NO;
    char letters[160];
    encoding_letters(method_getTypeEncoding(method), letters, sizeof letters);
    if (strcmp(letters, types) != 0) {
        os_log_error(OS_LOG_DEFAULT,
                     "l8localauth: %{public}s encoding is %{public}s, expected %{public}s; "
                     "not hooked", name, letters, types);
        return NO;
    }
    *original = method_getImplementation(method);
    method_setImplementation(method, replacement);
    return YES;
}

static atomic_bool gInstalled;

static void install_if_ready(void) {
    if (atomic_load(&gInstalled)) return;
    Class cls = objc_lookUpClass("LAContext");
    if (cls == Nil) return;
    bool expected = false;
    if (!atomic_compare_exchange_strong(&gInstalled, &expected, true)) return;

    /* Encodings read from the live runtime on n104/24B5099f, offsets stripped.
     * CFTypeRef comes back concretely typed as the SecAccessControl struct
     * pointer, and the two reply: variants are indistinguishable by encoding
     * because NSString* and NSDictionary* are both '@'; the selector name is
     * what separates those two, which is why both are still listed. */
    int hooked = 0;
    hooked |= swap(cls, "evaluatePolicy:options:reply:", 5, "v@:q@@?",
                   (IMP)l8_evaluate, (IMP *)&gOriginalEvaluate) << 0;
    hooked |= swap(cls, "evaluatePolicy:options:error:", 5, "@@:q@^@",
                   (IMP)l8_evaluateSync, (IMP *)&gOriginalEvaluateSync) << 1;
    hooked |= swap(cls, "canEvaluatePolicy:error:", 4, "B@:q^@",
                   (IMP)l8_canEvaluate, (IMP *)&gOriginalCanEvaluate) << 2;
    hooked |= swap(cls, "evaluateAccessControl:operation:localizedReason:reply:", 6,
                   "v@:^{__SecAccessControl=}q@@?",
                   (IMP)l8_access, (IMP *)&gOriginalAccess) << 3;
    hooked |= swap(cls, "evaluateAccessControl:operation:options:reply:", 6,
                   "v@:^{__SecAccessControl=}q@@?",
                   (IMP)l8_accessOptions, (IMP *)&gOriginalAccessOptions) << 4;
    hooked |= swap(cls, "evaluateAccessControl:operation:options:error:", 6,
                   "@@:^{__SecAccessControl=}q@^@",
                   (IMP)l8_accessSync, (IMP *)&gOriginalAccessSync) << 5;
    os_log_error(OS_LOG_DEFAULT, "l8localauth: installed in pid %d (hook mask 0x%x of 0x3f)",
                 getpid(), hooked);
}

/* Whether a loaded image is LocalAuthentication, read from its own
 * LC_ID_DYLIB. Pure memory parsing: the objc image-load callback runs with the
 * runtime lock held, so it must not call into the objc runtime (that
 * deadlocks, measured) or dyld. */
static BOOL is_localauth_image(const struct mach_header *header) {
    if (header == NULL || header->magic != MH_MAGIC_64) return NO;
    const struct load_command *command =
        (const struct load_command *)((const char *)header + sizeof(struct mach_header_64));
    for (uint32_t i = 0; i < header->ncmds; i++) {
        if (command->cmd == LC_ID_DYLIB) {
            const struct dylib_command *dylib = (const struct dylib_command *)command;
            const char *name = (const char *)command + dylib->dylib.name.offset;
            return strstr(name, "/LocalAuthentication.framework/") != NULL;
        }
        command = (const struct load_command *)((const char *)command + command->cmdsize);
    }
    return NO;
}

#ifndef LITER8_LOCALAUTH_TEST
static void deferred_install(void *context) {
    (void)context;
    @autoreleasepool { install_if_ready(); }
}

/* Called for every image already loaded at registration and for each later
 * one, after objc has registered its classes, with the runtime lock held. The
 * swizzle needs that lock, so it is queued and runs as soon as the load ends.
 * A call made in the window between the load and the queued install is not
 * translated; processes that link LocalAuthentication are hooked directly by
 * the constructor and never take this path. */
static void image_loaded(const struct mach_header *header) {
    if (atomic_load(&gInstalled) || !is_localauth_image(header)) return;
    dispatch_async_f(dispatch_get_global_queue(QOS_CLASS_USER_INITIATED, 0), NULL,
                     deferred_install);
}

__attribute__((constructor))
static void install(void) {
    @autoreleasepool {
        install_if_ready();
        if (!atomic_load(&gInstalled)) objc_addLoadImageFunc(image_loaded);
    }
}
#else
#import <LocalAuthentication/LocalAuthentication.h>

static const NSInteger kL8UserCancel = -2;

int main(void) {
    @autoreleasepool {
        // The spelled-out values must equal the framework's.
        if (![kL8LADomain isEqualToString:LAErrorDomain]) return 10;
        if (kL8PasscodeNotSet != LAErrorPasscodeNotSet) return 11;
        if (kL8UserCancel != LAErrorUserCancel) return 12;

        NSError *acm = [NSError errorWithDomain:kL8LADomain code:-1000
            userInfo:@{NSDebugDescriptionErrorKey:
                @"ACM verification of Oslo on ACMContext 0 failed: -3"}];
        NSError *cancel = [NSError errorWithDomain:kL8LADomain code:kL8UserCancel userInfo:nil];
        NSError *otherMinus1000 = [NSError errorWithDomain:kL8LADomain code:-1000 userInfo:nil];
        NSError *wrongDomain = [NSError errorWithDomain:@"other" code:-1000
            userInfo:@{NSDebugDescriptionErrorKey: @"ACM verification failed: -3"}];

        if (translated(acm).code != kL8PasscodeNotSet) return 1;
        if (translated(cancel) != cancel) return 2;
        if (translated(otherMinus1000) != otherMinus1000) return 3;
        if (translated(wrongDomain) != wrongDomain) return 4;
        if (translated(nil) != nil) return 5;

        /* "failed: -3" is a prefix of "failed: -30". An unrelated ACM status
         * must keep stock behaviour instead of being rewritten into the bypass. */
        for (NSString *other in @[@"-30", @"-31", @"-399", @"-3000"]) {
            NSError *near = [NSError errorWithDomain:kL8LADomain code:-1000
                userInfo:@{NSDebugDescriptionErrorKey: [NSString stringWithFormat:
                    @"ACM verification of Oslo on ACMContext 0 failed: %@", other]}];
            if (translated(near) != near) return 13;
        }
        /* The real one still matches, with and without trailing text. */
        NSError *trailing = [NSError errorWithDomain:kL8LADomain code:-1000
            userInfo:@{NSDebugDescriptionErrorKey:
                @"ACM verification of Oslo on ACMContext 0 failed: -3 (ignored)"}];
        if (translated(trailing).code != kL8PasscodeNotSet) return 14;
        /* A message with no status at all is not a match. */
        NSError *noStatus = [NSError errorWithDomain:kL8LADomain code:-1000
            userInfo:@{NSDebugDescriptionErrorKey: @"ACM verification of Oslo gave up"}];
        if (translated(noStatus) != noStatus) return 15;
        // Translation is idempotent, so a hooked public method calling a
        // hooked private one cannot translate twice into something else.
        if (translated(translated(acm)).code != kL8PasscodeNotSet) return 6;

        // The image-load callback recognises LocalAuthentication by header.
        BOOL found = NO, wrong = NO;
        for (uint32_t i = 0; i < _dyld_image_count(); i++) {
            BOOL match = is_localauth_image(_dyld_get_image_header(i));
            BOOL named = strstr(_dyld_get_image_name(i), "/LocalAuthentication.framework/") != NULL;
            found |= match && named;
            wrong |= match != named;
        }
        if (!found || wrong) return 8;

        // LAContext is linked into this test binary, so the path the
        // constructor takes must find and hook all three.
        install_if_ready();
        if (!gOriginalEvaluate || !gOriginalCanEvaluate || !gOriginalAccess) return 7;
        printf("PASS: SEP-less ACM -3 translated; cancellation, other errors and success "
               "preserved; public LAContext methods hooked (private: sync=%d "
               "access-options=%d access-sync=%d)\n", gOriginalEvaluateSync != NULL,
               gOriginalAccessOptions != NULL, gOriginalAccessSync != NULL);
        return 0;
    }
}
#endif
