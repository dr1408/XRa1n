/*
 * Narrow LocalAuthentication fallback for lockdownd's Trust flow.
 *
 * The user must still accept lockdownd's Trust notification. Only after that
 * explicit acceptance does lockdownd evaluate private policy 1028
 * (LocationBasedTrustComputer) to request the device passcode. On the
 * SEP-less Liter8 boot profile, ACM returns -3 before presenting that UI.
 *
 * Preserve the real evaluation first. If, and only if, policy 1028 returns the
 * exact observed LocalAuthentication -1000 / ACM -3 failure while the existing
 * pairing-fallback marker is active, report an empty successful result to
 * lockdownd. Every successful evaluation, other policy, and other failure is
 * passed through unchanged.
 */

#import <Foundation/Foundation.h>
#import <objc/runtime.h>

#include <stdbool.h>
#include <fcntl.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <syslog.h>
#include <unistd.h>

enum { kLocationBasedTrustComputerPolicy = 1028 };

static const char *kPairingFallbackMarker =
    "/private/var/root/Library/Lockdown/.xra1n-pairing-fallback";
static const char *kPairingDiagnosticLog =
    "/private/var/root/Library/Lockdown/xra1n-pair-auth.log";

typedef void (^L8PolicyReply)(id result, NSError *error);
typedef void (*EvaluatePolicyIMP)(id, SEL, NSInteger, NSDictionary *,
                                  L8PolicyReply);

static EvaluatePolicyIMP gOriginalEvaluatePolicy;

static void append_diagnostic(NSInteger policy, id result, NSError *error,
                              const char *event)
{
    int descriptor = open(kPairingDiagnosticLog,
                          O_WRONLY | O_CREAT | O_APPEND | O_CLOEXEC | O_NOFOLLOW,
                          S_IRUSR | S_IWUSR);
    if (descriptor < 0) {
        return;
    }
    (void)fchmod(descriptor, S_IRUSR | S_IWUSR);
    NSString *description = error != nil ? error.description : @"<nil>";
    const char *domain = error != nil ? error.domain.UTF8String : "<nil>";
    const char *detail = description.UTF8String;
    dprintf(descriptor,
            "event=%s pid=%d policy=%ld result=%s domain=%s code=%ld error=%s\n",
            event, getpid(), (long)policy, result != nil ? "yes" : "no",
            domain != NULL ? domain : "<null>",
            error != nil ? (long)error.code : 0,
            detail != NULL ? detail : "<null>");
    close(descriptor);
}

static bool pairing_fallback_enabled(void)
{
    struct stat status;
    if (lstat(kPairingFallbackMarker, &status) != 0) {
        return false;
    }
    return S_ISREG(status.st_mode) && status.st_uid == 0 &&
           (status.st_mode & (S_IWGRP | S_IWOTH)) == 0;
}

static bool is_expected_sep_less_failure(NSError *error)
{
    if (error == nil ||
        ![error.domain isEqualToString:@"com.apple.LocalAuthentication"]) {
        return false;
    }

    id debug_value = error.userInfo[@"NSDebugDescription"];
    if (![debug_value isKindOfClass:[NSString class]]) {
        return false;
    }
    NSString *debug_description = (NSString *)debug_value;
    if (error.code == -1000) {
        return [debug_description containsString:@"LocationBasedTrustComputer"] &&
               [debug_description containsString:@"failed: -3"];
    }
    /* Measured on XR 22H374 before first unlock. */
    return error.code == -1008 &&
           [debug_description containsString:@"DTO policies are not available"] &&
           [debug_description containsString:@"before first unlock"];
}

static void l8_evaluatePolicy(id self, SEL command, NSInteger policy,
                              NSDictionary *options, L8PolicyReply reply)
{
    if (reply == nil || !pairing_fallback_enabled()) {
        gOriginalEvaluatePolicy(self, command, policy, options, reply);
        return;
    }

    gOriginalEvaluatePolicy(
        self, command, policy, options, ^(id result, NSError *error) {
            append_diagnostic(policy, result, error, "reply");
            if (policy == kLocationBasedTrustComputerPolicy && result == nil &&
                is_expected_sep_less_failure(error)) {
                syslog(LOG_NOTICE,
                       "xra1n-pair: accepted explicit Trust after expected "
                       "policy 1028 measured LA failure");
                reply(@{}, nil);
                return;
            }
            reply(result, error);
        });
}

__attribute__((constructor))
static void install_trust_computer_policy_guard(void)
{
    const char *program = getprogname();
    if (program == NULL || strcmp(program, "lockdownd") != 0) {
        return;
    }

    Class context = objc_lookUpClass("LAContext");
    SEL selector = sel_registerName("evaluatePolicy:options:reply:");
    Method method = context != Nil ? class_getInstanceMethod(context, selector)
                                   : NULL;
    if (method == NULL) {
        syslog(LOG_ERR,
               "xra1n-pair: private Trust-computer policy method is unavailable");
        return;
    }

    gOriginalEvaluatePolicy =
        (EvaluatePolicyIMP)method_setImplementation(method,
                                                     (IMP)l8_evaluatePolicy);
    if (gOriginalEvaluatePolicy == NULL) {
        syslog(LOG_ERR,
               "xra1n-pair: could not install Trust-computer policy guard");
        return;
    }
    syslog(LOG_NOTICE,
           "xra1n-pair: installed marker-gated Trust-computer policy guard");
    append_diagnostic(-1, @{}, nil, "constructor-installed");
}
