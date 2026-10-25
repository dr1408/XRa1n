// Read-only XR Files/LocalStorage diagnostic. No registration or persona edits.
#import <Foundation/Foundation.h>
#import <objc/message.h>
#import <objc/runtime.h>
#import <dlfcn.h>
#import <stdio.h>
#import <unistd.h>

static id property(id object, NSString *name) {
    if (!object || ![object respondsToSelector:NSSelectorFromString(name)]) return nil;
    @try { return [object valueForKey:name]; }
    @catch (NSException *exception) {
        printf("PROPERTY %s exception=%s\n", name.UTF8String, exception.name.UTF8String);
        return nil;
    }
}

static void printPersona(const char *source, id persona) {
    printf("PERSONA %s class=%s unique=%s type=%s uid=%s gid=%s\n", source,
           persona ? object_getClassName(persona) : "nil",
           [property(persona, @"userPersonaUniqueString") description].UTF8String ?: "nil",
           [property(persona, @"userPersonaType") description].UTF8String ?: "nil",
           [property(persona, @"uid") description].UTF8String ?: "nil",
           [property(persona, @"gid") description].UTF8String ?: "nil");
}

static void printAttributes(const char *source, id attributes, NSError *error) {
    printf("ATTR %s class=%s unique=%s type=%s id=%s layout=%s error=%s:%ld\n", source,
           attributes ? object_getClassName(attributes) : "nil",
           [property(attributes, @"userPersonaUniqueString") description].UTF8String ?: "nil",
           [property(attributes, @"userPersonaType") description].UTF8String ?: "nil",
           [property(attributes, @"userPersona_id") description].UTF8String ?: "nil",
           [property(attributes, @"personaLayoutPathURL") description].UTF8String ?: "nil",
           error.domain.UTF8String ?: "nil", (long)error.code);
}

static void inspectPersonaValues(void) {
    printf("IDENTITY uid=%u euid=%u\n", getuid(), geteuid());
    Class managerClass = objc_lookUpClass("UMUserManager");
    SEL shared = sel_registerName("sharedManager");
    SEL current = sel_registerName("currentPersona");
    id manager = managerClass && [managerClass respondsToSelector:shared]
        ? ((id (*)(id, SEL))objc_msgSend)(managerClass, shared) : nil;
    id managerPersona = manager && [manager respondsToSelector:current]
        ? ((id (*)(id, SEL))objc_msgSend)(manager, current) : nil;
    printPersona("UMUserManager.currentPersona", managerPersona);

    Class personaClass = objc_lookUpClass("UMUserPersona");
    id classPersona = personaClass && [personaClass respondsToSelector:current]
        ? ((id (*)(id, SEL))objc_msgSend)(personaClass, current) : nil;
    printPersona("UMUserPersona.currentPersona", classPersona);

    Class attrClass = objc_lookUpClass("UMUserPersonaAttributes");
    SEL byType = sel_registerName("personaAttributesForPersonaType:withError:");
    for (NSUInteger type = 0; type <= 5; type += type == 0 ? 2 : 3) {
        NSError *error = nil;
        id attrs = attrClass && [attrClass respondsToSelector:byType]
            ? ((id (*)(id, SEL, NSUInteger, NSError **))objc_msgSend)(attrClass, byType, type, &error)
            : nil;
        char label[48]; snprintf(label, sizeof(label), "type=%lu", (unsigned long)type);
        printAttributes(label, attrs, error);
    }
    NSString *unique = property(classPersona ?: managerPersona, @"userPersonaUniqueString");
    if (unique.length) {
        SEL byUnique = sel_registerName("personaAttributesForPersonaUniqueString:withError:");
        NSError *error = nil;
        id attrs = attrClass && [attrClass respondsToSelector:byUnique]
            ? ((id (*)(id, SEL, id, NSError **))objc_msgSend)(attrClass, byUnique, unique, &error)
            : nil;
        printAttributes("current-unique", attrs, error);
    }
}

static void inspectMethod(const char *className, const char *selectorName, BOOL classMethod) {
    Class cls = objc_lookUpClass(className);
    SEL selector = sel_registerName(selectorName);
    Method method = cls ? (classMethod ? class_getClassMethod(cls, selector)
                                       : class_getInstanceMethod(cls, selector)) : NULL;
    printf("ABI %c[%s %s] %s\n", classMethod ? '+' : '-', className, selectorName,
           method ? method_getTypeEncoding(method) : "MISSING");
}

static void inspectABI(void) {
    const char *frameworks[] = {
        "/System/Library/PrivateFrameworks/UserManagement.framework/UserManagement",
        "/System/Library/Frameworks/ExtensionFoundation.framework/ExtensionFoundation",
        "/System/Library/PrivateFrameworks/FileProviderDaemon.framework/FileProviderDaemon",
    };
    for (unsigned i = 0; i < sizeof(frameworks) / sizeof(frameworks[0]); i++) {
        void *handle = dlopen(frameworks[i], RTLD_LAZY | RTLD_LOCAL);
        printf("FRAMEWORK %s %s\n", frameworks[i], handle ? "loaded" : dlerror());
    }

    inspectMethod("UMUserManager", "currentPersona", NO);
    inspectMethod("UMUserManager", "sharedManager", YES);
    inspectMethod("UMUserPersona", "currentPersona", YES);
    inspectMethod("UMUserPersonaAttributes", "personaAttributesForPersonaType:withError:", YES);
    inspectMethod("UMUserPersonaAttributes", "personaAttributesForPersonaUniqueString:withError:", YES);
    inspectMethod("UMUserPersonaAttributes", "setUserPersonaUniqueString:", NO);
    inspectMethod("UMUserPersonaAttributes", "setPersonaLayoutPathURL:", NO);
    inspectMethod("UMUserPersonaAttributes", "setUserPersonaType:", NO);
    inspectMethod("UMUserPersonaAttributes", "setUserPersona_id:", NO);
    inspectMethod("UMUserPersona", "setUid:", NO);
    inspectMethod("UMUserPersona", "setGid:", NO);
    inspectMethod("_EXPersona", "encodeWithCoder:", NO);
    inspectMethod("_EXPersona", "personaUniqueString", NO);
    inspectMethod("_EXLaunchConfiguration", "launchPersona", NO);
    inspectMethod("_EXHostConfiguration", "launchPersona", NO);
    inspectMethod("FPDProviderDescriptor", "isPersonaLegit", NO);

    Class personaClass = objc_lookUpClass("_EXPersona");
    Ivar ivar = personaClass ? class_getInstanceVariable(personaClass, "_personaUniqueString") : NULL;
    printf("IVAR _EXPersona._personaUniqueString %s offset=%td\n",
           ivar ? ivar_getTypeEncoding(ivar) : "MISSING",
           ivar ? ivar_getOffset(ivar) : (ptrdiff_t)-1);
}

static int listLocalStorage(void) {
    Class cls = objc_lookUpClass("LSApplicationWorkspace");
    SEL shared = sel_registerName("defaultWorkspace");
    SEL installed = sel_registerName("installedPlugins");
    if (!cls || ![cls respondsToSelector:shared]) {
        fprintf(stderr, "LSApplicationWorkspace unavailable\n");
        return 1;
    }
    id workspace = ((id (*)(id, SEL))objc_msgSend)(cls, shared);
    if (![workspace respondsToSelector:installed]) {
        fprintf(stderr, "installedPlugins unavailable\n");
        return 1;
    }
    NSArray *plugins = ((id (*)(id, SEL))objc_msgSend)(workspace, installed);
    unsigned matches = 0;
    for (id plugin in plugins) {
        NSString *identifier = [plugin respondsToSelector:@selector(pluginIdentifier)]
            ? [plugin valueForKey:@"pluginIdentifier"] : nil;
        NSURL *url = [plugin respondsToSelector:@selector(bundleURL)]
            ? [plugin valueForKey:@"bundleURL"] : nil;
        if ([identifier containsString:@"LocalStorage"] ||
            [url.path containsString:@"LocalStorageFileProvider"]) {
            NSString *point = [plugin respondsToSelector:@selector(protocol)]
                ? [plugin valueForKey:@"protocol"] : nil;
            printf("PLUGIN id=%s point=%s path=%s\n",
                   identifier.UTF8String ?: "(nil)", point.UTF8String ?: "(nil)",
                   url.path.UTF8String ?: "(nil)");
            matches++;
        }
    }
    printf("PLUGIN_SUMMARY total=%lu localstorage=%u\n", (unsigned long)plugins.count, matches);
    return 0;
}

int main(void) {
    @autoreleasepool {
        inspectABI();
        inspectPersonaValues();
        return listLocalStorage();
    }
}
