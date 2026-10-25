// XR 22H374 adaptation of Liter8-iPad8's LocalStorage persona compatibility
// hook. Process-local only: no kernel persona, registration, or keybag edits.
#import <Foundation/Foundation.h>
#import <objc/message.h>
#import <objc/runtime.h>
#import <os/log.h>
#include <dlfcn.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <sys/sysctl.h>
#include <unistd.h>

static NSString *const kIdentity = @"58524653-5045-5253-4F4E-414C00000501";
static const char *const kMarker = "/var/jb/.xra1n-files";

static BOOL enabled(void) {
    struct stat st;
    return lstat(kMarker, &st) == 0 && S_ISREG(st.st_mode) && st.st_uid == 0 &&
           !(st.st_mode & 022);
}

static void setObject(id object, const char *name, id value) {
    SEL selector = sel_registerName(name);
    if ([object respondsToSelector:selector])
        ((void (*)(id, SEL, id))objc_msgSend)(object, selector, value);
}

static void setWord(id object, const char *name, NSUInteger value) {
    SEL selector = sel_registerName(name);
    if ([object respondsToSelector:selector])
        ((void (*)(id, SEL, NSUInteger))objc_msgSend)(object, selector, value);
}

static void setUInt32(id object, const char *name, uint32_t value) {
    SEL selector = sel_registerName(name);
    if ([object respondsToSelector:selector])
        ((void (*)(id, SEL, uint32_t))objc_msgSend)(object, selector, value);
}

static void setBool(id object, const char *name, BOOL value) {
    SEL selector = sel_registerName(name);
    if ([object respondsToSelector:selector])
        ((void (*)(id, SEL, BOOL))objc_msgSend)(object, selector, value);
}

static id attributes(NSUInteger type) {
    Class cls = objc_lookUpClass("UMUserPersonaAttributes");
    if (!cls) return nil;
    id object = [cls new];
    setObject(object, "setUserPersonaUniqueString:", kIdentity);
    setObject(object, "setPersonaLayoutPathURL:",
              [NSURL fileURLWithPath:@"/private/var/mobile" isDirectory:YES]);
    setWord(object, "setUserPersonaType:", type);
    setUInt32(object, "setUserPersona_id:", 501);
    setBool(object, "setIsPersonalPersona:", type == 0);
    setBool(object, "setIsSystemPersona:", type == 2);
    setBool(object, "setIsDefaultPersona:", type == 5);
    return object;
}

static id localPersona(void) {
    static id persona;
    static dispatch_once_t once;
    dispatch_once(&once, ^{
        Class cls = objc_lookUpClass("UMUserPersona");
        if (!cls) return;
        persona = class_createInstance(cls, 0);
        setObject(persona, "setUserPersonaUniqueString:", kIdentity);
        setWord(persona, "setUserPersonaType:", 0);
        setUInt32(persona, "setUid:", 501);
        setUInt32(persona, "setGid:", 501);
        setBool(persona, "setIsPersonalPersona:", YES);
    });
    return persona;
}

static id (*oldCurrent)(id, SEL);
static id (*oldClassCurrent)(id, SEL);
static id (*oldType)(id, SEL, NSUInteger, NSError **);
static id (*oldUnique)(id, SEL, id, NSError **);
static BOOL (*oldLegit)(id, SEL);
static void (*oldEXEncode)(id, SEL, id);
static id (*oldEXString)(id, SEL);
static id (*oldLaunch)(id, SEL);
static id (*oldHostLaunch)(id, SEL);

static BOOL lacksIdentity(id persona) {
    SEL selector = sel_registerName("userPersonaUniqueString");
    if (!persona || ![persona respondsToSelector:selector]) return YES;
    return ((id (*)(id, SEL))objc_msgSend)(persona, selector) == nil;
}

static id current(id self, SEL command) {
    id persona = oldCurrent(self, command);
    return enabled() && lacksIdentity(persona) ? localPersona() : persona;
}

static id classCurrent(id self, SEL command) {
    id persona = oldClassCurrent(self, command);
    return enabled() && lacksIdentity(persona) ? localPersona() : persona;
}

static id byType(id self, SEL command, NSUInteger type, NSError **error) {
    id value = oldType(self, command, type, error);
    if (value || !enabled() || (type != 0 && type != 2 && type != 5)) return value;
    if (error) *error = nil;
    os_log_error(OS_LOG_DEFAULT, "xra1n-files: local attributes role %{public}lu", (unsigned long)type);
    return attributes(type);
}

static id byUnique(id self, SEL command, id unique, NSError **error) {
    id value = oldUnique(self, command, unique, error);
    if (value || !enabled() || ![unique isEqual:kIdentity]) return value;
    if (error) *error = nil;
    return attributes(0);
}

static void exEncode(id self, SEL command, id coder) {
    Class cls = objc_lookUpClass("_EXPersona");
    Ivar ivar = cls ? class_getInstanceVariable(cls, "_personaUniqueString") : NULL;
    id unique = ivar ? object_getIvar(self, ivar) : nil;
    if (enabled() && [unique isEqual:kIdentity]) {
        id empty = class_createInstance(cls, 0);
        os_log_error(OS_LOG_DEFAULT, "xra1n-files: omit synthetic persona from extension encoding");
        oldEXEncode(empty, command, coder);
        return;
    }
    oldEXEncode(self, command, coder);
}

static id exString(id self, SEL command) {
    id unique = oldEXString(self, command);
    return enabled() && [unique isEqual:kIdentity] ? nil : unique;
}

static id withoutSyntheticLaunch(id persona) {
    SEL selector = sel_registerName("personaUniqueString");
    if (enabled() && [persona respondsToSelector:selector] &&
        [((id (*)(id, SEL))objc_msgSend)(persona, selector) isEqual:kIdentity]) {
        os_log_error(OS_LOG_DEFAULT, "xra1n-files: launch local extension without synthetic kernel persona");
        return nil;
    }
    return persona;
}

static id launch(id self, SEL command) {
    return withoutSyntheticLaunch(oldLaunch(self, command));
}

static id hostLaunch(id self, SEL command) {
    return withoutSyntheticLaunch(oldHostLaunch(self, command));
}

static BOOL legit(id self, SEL command) {
    if (!enabled()) return oldLegit(self, command);
    SEL identifier = sel_registerName("identifier");
    if (![self respondsToSelector:identifier]) return NO;
    id value = ((id (*)(id, SEL))objc_msgSend)(self, identifier);
    return [value isEqual:@"com.apple.FileProvider.LocalStorage"];
}

static BOOL hook(Class cls, const char *name, IMP replacement, void *saved) {
    if (!cls) return NO;
    Method method = class_getInstanceMethod(cls, sel_registerName(name));
    if (!method) return NO;
    *(IMP *)saved = method_getImplementation(method);
    method_setImplementation(method, replacement);
    return YES;
}

__attribute__((constructor)) static void install(void) {
    @autoreleasepool {
        if (!enabled() || geteuid() != 501) return;
        const char *program = getprogname();
        if (!program || (strcmp(program, "fileproviderd") &&
                         strcmp(program, "LocalStorageFileProvider"))) return;
        char machine[64] = {0}, build[64] = {0};
        size_t size = sizeof(machine);
        if (sysctlbyname("hw.machine", machine, &size, NULL, 0) ||
            strcmp(machine, "iPhone11,8")) return;
        size = sizeof(build);
        if (sysctlbyname("kern.osversion", build, &size, NULL, 0) ||
            strcmp(build, "22H374")) return;

        dlopen("/System/Library/PrivateFrameworks/UserManagement.framework/UserManagement", RTLD_NOW);
        dlopen("/System/Library/Frameworks/ExtensionFoundation.framework/ExtensionFoundation", RTLD_NOW);
        BOOL enc = hook(objc_lookUpClass("_EXPersona"), "encodeWithCoder:",
                        (IMP)exEncode, &oldEXEncode);
        BOOL exs = hook(objc_lookUpClass("_EXPersona"), "personaUniqueString",
                        (IMP)exString, &oldEXString);
        BOOL exl = hook(objc_lookUpClass("_EXLaunchConfiguration"), "launchPersona",
                        (IMP)launch, &oldLaunch);
        BOOL exh = hook(objc_lookUpClass("_EXHostConfiguration"), "launchPersona",
                        (IMP)hostLaunch, &oldHostLaunch);
        BOOL cur = hook(objc_lookUpClass("UMUserManager"), "currentPersona",
                        (IMP)current, &oldCurrent);
        BOOL typ = hook(object_getClass(objc_lookUpClass("UMUserPersonaAttributes")),
                        "personaAttributesForPersonaType:withError:", (IMP)byType, &oldType);
        BOOL uni = hook(object_getClass(objc_lookUpClass("UMUserPersonaAttributes")),
                        "personaAttributesForPersonaUniqueString:withError:", (IMP)byUnique, &oldUnique);
        BOOL lgt = hook(objc_lookUpClass("FPDProviderDescriptor"), "isPersonaLegit",
                        (IMP)legit, &oldLegit);
        BOOL cls = hook(object_getClass(objc_lookUpClass("UMUserPersona")),
                        "currentPersona", (IMP)classCurrent, &oldClassCurrent);
        os_log_error(OS_LOG_DEFAULT,
                     "xra1n-files: hooks EX=%{public}d/%{public}d/%{public}d/%{public}d UM=%{public}d/%{public}d/%{public}d/%{public}d FPD=%{public}d pid=%{public}d",
                     enc, exs, exl, exh, cur, typ, uni, cls, lgt, getpid());
    }
}
