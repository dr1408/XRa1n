/* Liter8's launchd bootstrap and process-local tweak loader.
 * Only this System-volume image is inserted by dyld. Its constructor consumes
 * /private/var/jb sandbox extensions (read, and map-executable) before
 * dlopening ElleKit.
 * PID 1 and xpcproxy propagate launch state but never load tweaks.
 */
#include <dlfcn.h>
#include <errno.h>
#include <stdint.h>
#include <os/log.h>
#include <fcntl.h>
#include <pthread.h>
#include <spawn.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <unistd.h>
#include "lhook_session.h"

#define DYLD_INTERPOSE(_replacement, _replacee)                                        \
    __attribute__((used)) static struct {                                              \
        const void *replacement;                                                       \
        const void *replacee;                                                          \
    } _interpose_##_replacee __attribute__((section("__DATA,__interpose"))) = {        \
        (const void *)(unsigned long)&_replacement,                                    \
        (const void *)(unsigned long)&_replacee                                        \
    };

/* On the System volume, so it resolves before the Data volume is mounted.
 *
 * Overridable at build time only so the interpose logic can be exercised from /var/jb on
 * a running device. Testing this code by deploying it to PID 1 first would mean a DFU
 * trip per iteration, and the whole point of the guards is that they are verified before
 * they ever run in launchd. The production build takes the default. */
#ifndef LHOOK_SELF_PATH
#define LHOOK_SELF_PATH "/usr/lib/lhook"
#endif
static const char *kSelf       = LHOOK_SELF_PATH;

#ifndef LHOOK_PAYLOAD_C
#define LHOOK_PAYLOAD_C "/var/jb/usr/lib/TweakLoader.dylib"
#endif
static const char *kLoader = LHOOK_PAYLOAD_C;

/* The rootless-icon payload (systemhook_icon.c), on the System volume for the
 * same reason as kSelf. It is inserted directly rather than loaded through
 * TweakLoader because iconservicesagent needs its read token before the icon
 * service renders anything, and because it is already provisioned and verified
 * by sshrd_provision.sh. It acts only in iconservicesagent and returns after
 * one name comparison everywhere else, so inserting it costs one strcmp per
 * injected spawn. */
#ifndef LHOOK_PAYLOAD_A
#define LHOOK_PAYLOAD_A "/usr/lib/systemhook.dylib"
#endif
static const char *kIcons = LHOOK_PAYLOAD_A;
/* Sandbox grants the parent mints for an injected child, one environment
 * variable each. Read lets a sandboxed process open /private/var/jb; the
 * container profile checks mapping a dylib executable separately
 * (file-map-executable), so dlopen of TweakLoader and the tweaks also needs
 * the executable class, as other rootless loaders issue for their root. */
static const char *kGrantRoot = "/private/var/jb";
static const struct grant { const char *key; const char *cls; } kGrants[] = {
    { "LITER8_SANDBOX_READ_TOKEN=", "com.apple.app-sandbox.read" },
    { "LITER8_SANDBOX_EXEC_TOKEN=", "com.apple.sandbox.executable" },
};
#define GRANT_COUNT (sizeof(kGrants) / sizeof(kGrants[0]))
typedef char *(*issue_file_fn)(const char *, const char *, uint32_t);
typedef int64_t (*consume_fn)(const char *);

static const char *kDebugFile  = "/var/jb/.lhook_debug";
static const char *kLogFile    = "/var/jb/tmp/lhook.log";

/* The tunable denylist, on the Data volume so it can be edited over SSH. This exists
 * because the first version compiled the list into this dylib, which lives on the sealed
 * System volume, making a one-word policy change cost a DFU trip. */
static const char *kDenyFile   = "/var/jb/etc/lhook.deny";

static const char *kMarkerPaths[] = {
    "/var/jb/tmp/lhook.log",
    "/var/tmp/lhook.log",
    "/tmp/lhook.log",
};
static const int kMarkerPathCount = 3;

/* The Data volume appears well within a minute of PID 1 starting, and a thread that
 * lives forever inside launchd is not something to leave behind. */
static const int kMaxAttempts = 120;
static const unsigned kRetryMicroseconds = 500000;

/* Compiled in and NOT overridable. Injecting any of these does not produce a crashed
 * process, it produces a device that does not come back, or one that cannot be reached
 * to fix it. dropbear and sshd are here for that second reason: they are the only way in.
 *
 * Nothing tunable belongs in this list. If an entry here turns out to be wrong it costs a
 * DFU trip to change, which is exactly the problem kDenyFile exists to solve. */
static const char *kHardDeny[] = {
    "launchd", "amfid", "trustd", "securityd", "configd",
    "notifyd", "logd", "opendirectoryd", "keybagd", "watchdogd",
    "dropbear", "sshd",
    NULL
};

/* Conservative fallback for services unrelated to tweak loading. */
static const char *kDefaultDeny[] = {
    "diskarbitrationd", "syslogd", "usbmuxd",
    "mobile_obliterator", "restored_external", "aslmanager",
    NULL
};

static int file_exists(const char *path) {
    struct stat st;
    return stat(path, &st) == 0;
}

/* Guard #3. With no payload present there is nothing to inject, and injecting kSelf alone
 * would spread the interposer with no effect.
 *
 * Both payloads count, not just the loader. The icon payload is inserted directly and
 * works on its own, so a device with it installed and no ElleKit still has a reason to
 * inject: measured on n104, where provisioning installs systemhook.dylib but nothing
 * installs /var/jb/usr/lib/TweakLoader.dylib, so gating on the loader alone left
 * iconservicesagent unpatched and every rootless icon blank. */
static int any_payload_exists(void) {
    return file_exists(kLoader) || file_exists(kIcons);
}

static int name_in(const char **list, const char *name) {
    for (int i = 0; list[i]; i++)
        if (strcmp(name, list[i]) == 0) return 1;
    return 0;
}

/* Read the tunable denylist and test one name against it.
 *
 * The file is re-read per spawn rather than cached. That is deliberate: a cache needs
 * invalidation and a lock, launchd is multithreaded, and a locking bug in the spawn path
 * of PID 1 is far more expensive than a few microseconds of file read. It is also only
 * ever reached once injection is enabled, which cannot happen before /var/jb is mounted,
 * so it never runs during early boot.
 *
 * Format: one process name per line. Blank lines and lines starting with # are ignored,
 * so the file can document itself.
 *
 * Returns 1 deny, 0 allow, -1 if the file is absent and the caller should use the default.
 */
static int denied_by_file(const char *name) {
    FILE *file = fopen(kDenyFile, "r");
    if (!file) return -1;

    char line[256];
    int result = 0;
    int continuation = 0;
    while (fgets(line, sizeof line, file)) {
        /* A line longer than the buffer comes back in fragments, and fgets gives no
         * indication that it did. Without tracking that, the tail of a long comment
         * would be treated as a line of its own: a comment ending in a process name
         * would silently deny that process. Skip fragments that continue a previous
         * incomplete line. */
        size_t length = strlen(line);
        int incomplete = (length > 0 && line[length - 1] != '\n');
        int is_continuation = continuation;
        continuation = incomplete;
        if (is_continuation) continue;

        char *start = line;
        while (*start == ' ' || *start == '\t') start++;
        if (*start == '#' || *start == '\n' || *start == '\r' || *start == '\0') continue;

        char *end = start + strlen(start);
        while (end > start && (end[-1] == '\n' || end[-1] == '\r' ||
                               end[-1] == ' '  || end[-1] == '\t')) end--;
        *end = '\0';

        if (strcmp(start, name) == 0) { result = 1; break; }
    }
    fclose(file);
    return result;
}

static int denied(const char *path) {
    if (!path) return 1;
    const char *base = strrchr(path, '/');
    base = base ? base + 1 : path;

    /* Checked first and unconditionally, so no edit to the file can reach these. */
    if (name_in(kHardDeny, base)) return 1;

    int from_file = denied_by_file(base);
    if (from_file >= 0) return from_file;
    return name_in(kDefaultDeny, base);
}

/* Off unless kDebugFile exists. Checked per call rather than cached so it can be turned
 * on over SSH without a reboot, which matters because a reboot here is a DFU trip. */
static void trace(const char *fmt, const char *arg) {
    if (!file_exists(kDebugFile)) return;
    FILE *log = fopen(kLogFile, "a");
    if (!log) return;
    fprintf(log, fmt, arg);
    fclose(log);
}

/* ---------------------------------------------------------------- proof of load */

static int append_pid(char *out, int at, int value) {
    char digits[16];
    int n = 0;
    if (value == 0) digits[n++] = '0';
    while (value > 0 && n < (int)sizeof(digits)) {
        digits[n++] = (char)('0' + (value % 10));
        value /= 10;
    }
    while (n > 0) out[at++] = digits[--n];
    return at;
}

static int build_message(char *out, int cap) {
    static const char prefix[] = "[lhook] loaded into pid ";
    int at = 0;
    for (const char *p = prefix; *p && at < cap - 24; p++) out[at++] = *p;
    at = append_pid(out, at, (int)getpid());
    out[at++] = '\n';
    return at;
}

static void write_console(const char *message, int length) {
    int fd = open("/dev/console", O_WRONLY | O_NONBLOCK);
    if (fd < 0) return;
    (void)write(fd, message, (size_t)length);
    close(fd);
}

static int write_to(const char *path, const char *message, int length) {
    int fd = open(path, O_WRONLY | O_CREAT | O_APPEND, 0644);
    if (fd < 0) return 0;
    (void)write(fd, message, (size_t)length);
    close(fd);
    return 1;
}

/* TMPDIR is the only path an app-sandboxed process can write. Every fixed path in
 * kMarkerPaths is outside the container and is refused regardless of file mode, which is
 * why injection into Safari and Geekbench looked like a total failure: the variable was
 * delivered correctly and there was simply nowhere to say so.
 *
 * Tried FIRST, and the shared path is still attempted afterwards, so a daemon keeps
 * logging where the daemon log has always been while an app finally gets a voice. */
static int try_write_marker(const char *message, int length) {
    int wrote = 0;

    const char *tmpdir = getenv("TMPDIR");
    if (tmpdir && *tmpdir) {
        char local[1024];
        size_t n = strlen(tmpdir);
        if (n + sizeof("/lhook.log") < sizeof local) {
            memcpy(local, tmpdir, n);
            memcpy(local + n, "/lhook.log", sizeof("/lhook.log"));
            wrote |= write_to(local, message, length);
        }
    }

    for (int i = 0; i < kMarkerPathCount; i++)
        if (write_to(kMarkerPaths[i], message, length)) { wrote = 1; break; }

    return wrote;
}

/* Only reached when nothing was writable at constructor time, which is the PID 1 case:
 * the Data volume is not mounted yet. Ordinary processes never get here. */
static void *marker_thread(void *unused) {
    (void)unused;
    char message[64];
    int length = build_message(message, (int)sizeof(message));

    for (int attempt = 0; attempt < kMaxAttempts; attempt++) {
        if (try_write_marker(message, length)) return NULL;
        usleep(kRetryMicroseconds);
    }
    return NULL;
}

/* ------------------------------------------------------------ spawn interposition */

/* Insert only System-volume images. Data-volume libraries must wait until the
 * new process has consumed its sandbox extension, which is why TweakLoader is
 * dlopened by our constructor instead of being inserted here. */
static const char kInsertKey[] = "DYLD_INSERT_LIBRARIES=";
#define INSERT_KEY_LEN (sizeof(kInsertKey) - 1)

/* Ownership is explicit: only the array, insert value and token values are
 * ours. Other environment entries remain borrowed from the caller. */
struct child_env { char **values; char *insert; char *tokens[GRANT_COUNT]; };

/* Index of the grant whose key prefixes this entry, or -1. */
static int grant_key(const char *entry) {
    for (size_t g = 0; g < GRANT_COUNT; g++)
        if (!strncmp(entry, kGrants[g].key, strlen(kGrants[g].key))) return (int)g;
    return -1;
}

static int own_library(const char *path) {
    return !strcmp(path, kSelf) || !strcmp(path, kLoader) ||
        !strcmp(path, kIcons) || !strcmp(path, "/usr/lib/lhook");
}

/* True if the caller's environment already carries one of our keys, so a
 * not-injecting spawn still has something to strip. Used only to decide
 * whether the allocating rebuild path is needed at all. */
static int env_has_our_keys(char *const envp[]) {
    for (int i = 0; envp && envp[i]; i++) {
        if (!strncmp(envp[i], kInsertKey, INSERT_KEY_LEN)) return 1;
        if (grant_key(envp[i]) >= 0) return 1;
    }
    return 0;
}

static void free_child_env(struct child_env *env) {
    free(env->insert);
    for (size_t g = 0; g < GRANT_COUNT; g++) free(env->tokens[g]);
    free(env->values);
}

static int make_child_env(char *const input[], int inject, struct child_env *out) {
    size_t count = 0;
    const char *existing = NULL, *inherited[GRANT_COUNT] = {0};
    while (input && input[count]) {
        const char *v = input[count++];
        if (!strncmp(v, kInsertKey, INSERT_KEY_LEN) && !existing)
            existing = v + INSERT_KEY_LEN;
        int g = grant_key(v);
        if (g >= 0 && !inherited[g]) inherited[g] = v + strlen(kGrants[g].key);
    }
    size_t capacity = strlen(kSelf) + strlen(kIcons)
        + (existing ? strlen(existing) : 0) + 3;
    char *libraries = calloc(1, capacity);
    char *copy = existing ? strdup(existing) : NULL;
    out->values = calloc(count + 2 + GRANT_COUNT, sizeof(char *));
    if (!libraries || (existing && !copy) || !out->values) {
        free(libraries); free(copy); return ENOMEM;
    }
    if (inject) {
        /* kSelf first so its constructor consumes the sandbox extension before
         * any other inserted image runs. Only insert the icon payload when it
         * is actually present: handing dyld a path that does not resolve is the
         * one mistake here that breaks every spawn on the system. */
        strcpy(libraries, kSelf);
        if (file_exists(kIcons)) { strcat(libraries, ":"); strcat(libraries, kIcons); }
    }
    char *cursor = NULL;
    for (char *part = copy ? strtok_r(copy, ":", &cursor) : NULL;
         part; part = strtok_r(NULL, ":", &cursor)) {
        if (own_library(part)) continue;
        if (*libraries) strcat(libraries, ":");
        strcat(libraries, part);
    }
    free(copy);
    if (*libraries && asprintf(&out->insert, "%s%s", kInsertKey, libraries) < 0) {
        out->insert = NULL; free(libraries); return ENOMEM;
    }
    free(libraries);
    /* Only where there is a loader to dlopen. The icon payload needs none of
     * this, and the strip below drops any inherited tokens rather than passing
     * on a grant this spawn would not have minted. */
    if (inject && file_exists(kLoader)) {
        /* A sandboxed descendant may be unable to issue a new extension. The
         * inherited bearer remains valid for this boot and this directory. */
        issue_file_fn issue = (issue_file_fn)dlsym(RTLD_DEFAULT, "sandbox_extension_issue_file");
        for (size_t g = 0; g < GRANT_COUNT; g++) {
            char *fresh = issue ? issue(kGrants[g].cls, kGrantRoot, 0) : NULL;
            const char *token = fresh ? fresh : inherited[g];
            if (token && *token &&
                asprintf(&out->tokens[g], "%s%s", kGrants[g].key, token) < 0) {
                out->tokens[g] = NULL; free(fresh); return ENOMEM;
            }
            free(fresh);
        }
    }
    size_t used = 0;
    for (size_t i = 0; i < count; i++) {
        if (!strncmp(input[i], kInsertKey, INSERT_KEY_LEN) || grant_key(input[i]) >= 0)
            continue;
        out->values[used++] = input[i];
    }
    if (out->insert) out->values[used++] = out->insert;
    for (size_t g = 0; g < GRANT_COUNT; g++)
        if (out->tokens[g]) out->values[used++] = out->tokens[g];
    return 0;
}

static int spawn_common(int (*real)(pid_t *, const char *,
                                    const posix_spawn_file_actions_t *,
                                    const posix_spawnattr_t *, char *const *, char *const *),
                        pid_t *pid, const char *path,
                        const posix_spawn_file_actions_t *actions,
                        const posix_spawnattr_t *attr,
                        char *const argv[], char *const envp[]) {
    int inject = lhook_session_enabled() && !denied(path) && any_payload_exists();

    /* Early-boot fast path. During launchd's own startup /var/jb is not mounted,
     * so inject is false and there is nothing of ours to strip. Do not allocate
     * and do not rebuild the environment: just spawn. This path runs on every
     * spawn out of PID 1 before the system is up, where any failure is an
     * unbootable device. */
    if (!inject && !env_has_our_keys(envp))
        return real(pid, path, actions, attr, argv, envp);

    struct child_env child = {0};
    if (make_child_env(envp, inject, &child) != 0) {
        /* Allocation failed. Spawn with the caller's unmodified environment
         * rather than failing the spawn: a failed spawn in PID 1 is far worse
         * than a missed injection. */
        free_child_env(&child);
        return real(pid, path, actions, attr, argv, envp);
    }
    if (inject) trace("[lhook] injecting %s\n", path ? path : "(null)");
    int rc = real(pid, path, actions, attr, argv, child.values);
    free_child_env(&child);
    return rc;
}

static int my_posix_spawn(pid_t *pid, const char *path,
                          const posix_spawn_file_actions_t *actions,
                          const posix_spawnattr_t *attr,
                          char *const argv[], char *const envp[]) {
    return spawn_common(posix_spawn, pid, path, actions, attr, argv, envp);
}

static int my_posix_spawnp(pid_t *pid, const char *path,
                           const posix_spawn_file_actions_t *actions,
                           const posix_spawnattr_t *attr,
                           char *const argv[], char *const envp[]) {
    return spawn_common(posix_spawnp, pid, path, actions, attr, argv, envp);
}

DYLD_INTERPOSE(my_posix_spawn,  posix_spawn)
DYLD_INTERPOSE(my_posix_spawnp, posix_spawnp)

/* This entry point is also used by the standalone sandbox regression test.
 * A failure leaves the host running without tweaks; it never aborts launch. */
/* Consume one of the minted extensions. 0 if it was consumed or was never
 * offered, -1 if it was offered and refused. */
static int consume_grant(size_t g) {
    char name[64];
    size_t length = strlen(kGrants[g].key) - 1; /* without '=' */
    if (length >= sizeof name) return 0;
    memcpy(name, kGrants[g].key, length);
    name[length] = '\0';
    const char *token = getenv(name);
    if (!token || !*token) return 0;
    consume_fn consume = (consume_fn)dlsym(RTLD_DEFAULT, "sandbox_extension_consume");
    if (consume && consume(token) >= 0) return 0;
    os_log_error(OS_LOG_DEFAULT, "Liter8: sandbox grant %{public}s failed pid=%d",
                 kGrants[g].cls, getpid());
    return -1;
}

int liter8_load_tweaks(void) {
    if (getpid() == 1) return 0;
    /* Read first: the checks below are themselves reads of /var/jb. Deciding
     * before consuming it cost injection in every profile that denies the
     * rootless tree until the extension lands; measured on 24B5099f,
     * iconservicesagent logged "deny(1) file-read-data /var/jb/.lhook_enabled"
     * and loaded nothing. The executable grant stays withheld until there is
     * something to dlopen, which was the point of gating at all. */
    if (consume_grant(0) != 0) return -1;
    if (!lhook_session_enabled() || !strcmp(getprogname(), "xpcproxy") || denied(getprogname())) return 0;
    if (!file_exists(kLoader)) return 0;
    /* Only matters to profiles that check file-map-executable, so a refusal is
     * logged and the load continues. */
    (void)consume_grant(1);
    /* Loading the loader here, from the constructor, rather than inserting it.
     *
     * The fork this port comes from records the opposite, at its own lhook.c:97:
     * "Loading ElleKit by hand from a constructor is what killed SpringBoard
     * during testing; inserted directly it returns 0 and the host survives,
     * which is measured, not assumed." That is a real measurement, but on
     * t8020 / iPadOS 26.7.1, and it is about an insert this path cannot perform:
     * a Data-volume library has no sandbox extension at DYLD_INSERT_LIBRARIES
     * time, which is the whole reason the grants above are consumed first (see
     * the spawn interposition comment).
     *
     * Measured here instead, on n104 / 24B5099f, 2026-10-12, with injection
     * armed and `launchctl kickstart -k user/501/com.apple.SpringBoard`:
     *
     *   enabled  -> SpringBoard respawned as pid 4521, logged "ElleKit loaded
     *               pid=4521 executable=SpringBoard" from this dlopen, then held
     *               the same pid across repeated checks. Reproduced once more as
     *               pid 4635. No SpringBoard crash report was written.
     *   disabled -> respawned as pid 4605, zero ElleKit loads device-wide, also
     *               stable. That half is what shows the enabled result is not
     *               just injection having stayed off.
     *
     * So this ordering survives on this platform. If that ever stops being
     * true, the fix is not to move the load into the insert list: it is to make
     * the extension available before dyld runs, or to defer the dlopen off the
     * constructor. The insert list is also third-party-owned, and rewriting it
     * is what the spawn path deliberately avoids. */
    if (!dlopen(kLoader, RTLD_NOW | RTLD_GLOBAL)) {
        os_log_error(OS_LOG_DEFAULT, "Liter8: ElleKit load failed pid=%d %{public}s", getpid(), dlerror());
        return -2;
    }
    os_log_error(OS_LOG_DEFAULT, "Liter8: ElleKit loaded pid=%d executable=%{public}s", getpid(), getprogname());
    return 1;
}

#ifndef LHOOK_NO_CONSTRUCTOR
__attribute__((constructor))
static void lhook_init(void) {
    (void)liter8_load_tweaks();
    char message[64];
    int length = build_message(message, (int)sizeof(message));
    if (file_exists(kDebugFile)) write_console(message, length);
    if (try_write_marker(message, length) || getpid() != 1) return;
    pthread_t thread;
    pthread_attr_t attr;
    if (pthread_attr_init(&attr) != 0) return;
    pthread_attr_setdetachstate(&attr, PTHREAD_CREATE_DETACHED);
    (void)pthread_create(&thread, &attr, marker_thread, NULL);
    pthread_attr_destroy(&attr);
}
#endif
