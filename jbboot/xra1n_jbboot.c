#define _POSIX_C_SOURCE 200809L
#define _DARWIN_C_SOURCE

/*
 * xra1n-jbboot - prepare the rootless bootstrap every normal boot.
 *
 * The stock Dropbear sets remote commands' PATH to include /tmp/bin and
 * /tmp/usr/bin.  Since /tmp is ephemeral, map those two slots to Procursus
 * after Data is available. It also issues the bearer sandbox extension that
 * systemhook consumes inside iconservicesagent, allowing that process to read
 * rootless application bundles below /var/jb.
 */

#include <dlfcn.h>
#include <errno.h>
#include <fcntl.h>
#include <limits.h>
#include <spawn.h>
#include <stdint.h>
#include <stdlib.h>
#include <stdio.h>
#include <string.h>
#include <sys/stat.h>
#include <sys/types.h>
#include <sys/wait.h>
#include <unistd.h>

#ifndef PATH_MAX
#define PATH_MAX 1024
#endif

#define TMP_ROOT "/private/var/tmp"
#define BOOTSTRAP_BIN "/var/jb/usr/bin"
#define BOOTSTRAP_SBIN "/var/jb/usr/sbin"
#define SANDBOX_EXTENSION_CLASS "com.apple.app-sandbox.read"
#define SANDBOX_EXTENSION_PATH "/private/var/jb"
#define SANDBOX_EXTENSION_MAILBOX TMP_ROOT "/sbext.token"
#define WAIT_SECONDS 180
#define SILEO_PERSONA_ID 99
#define UICACHE_PATH "/var/jb/usr/bin/uicache"
#define APP_REGISTRATION_MARKER "/var/jb/.xra1n-system-apps-registered"
#define PFWATCH_PATH "/usr/local/bin/pfwatch"

extern char **environ;

/* ABI from xnu/bsd/sys/persona.h.  Keep this local: the public iPhoneOS SDK
 * does not install the private persona declarations used by libsystem. */
struct xra1n_persona_info {
    unsigned int persona_info_version;
    uid_t persona_id;
    int persona_type;
    gid_t persona_gid;
    unsigned int persona_ngroups;
    gid_t persona_groups[16];
    uid_t persona_gmuid;
    char persona_name[257];
};

enum {
    XRA1N_PERSONA_GUEST = 1,
    XRA1N_PERSONA_MANAGED = 2,
    XRA1N_PERSONA_PRIV = 3,
    XRA1N_PERSONA_SYSTEM = 4,
};

typedef char *(*sandbox_extension_issue_file_t)(const char *, const char *,
                                                 uint32_t);
typedef int (*kpersona_alloc_t)(struct xra1n_persona_info *, uid_t *);
typedef int (*kpersona_info_t)(uid_t, struct xra1n_persona_info *);

static int write_all(int fd, const char *buffer, size_t size)
{
    while (size != 0) {
        ssize_t amount = write(fd, buffer, size);
        if (amount < 0 && errno == EINTR) continue;
        if (amount <= 0) return 0;
        buffer += amount;
        size -= (size_t)amount;
    }
    return 1;
}

static int is_secure_directory(const char *path)
{
    struct stat status;
    return lstat(path, &status) == 0 && S_ISDIR(status.st_mode) &&
           status.st_uid == 0 && (status.st_mode & (S_IWGRP | S_IWOTH)) == 0;
}

static int wait_for_bootstrap(void)
{
    for (int elapsed = 0; elapsed < WAIT_SECONDS; elapsed++) {
        if (is_secure_directory(BOOTSTRAP_BIN) &&
            is_secure_directory(BOOTSTRAP_SBIN)) {
            return 1;
        }
        sleep(1);
    }
    fprintf(stderr, "xra1n-jbboot: Procursus directories were not ready\n");
    return 0;
}

static int move_aside(const char *path)
{
    char destination[PATH_MAX];
    int amount = snprintf(destination, sizeof(destination),
                          "%s.xra1n-displaced.%d", path, (int)getpid());
    if (amount < 0 || (size_t)amount >= sizeof(destination)) {
        fprintf(stderr, "xra1n-jbboot: displaced path is too long: %s\n", path);
        return 0;
    }
    if (rename(path, destination) != 0) {
        fprintf(stderr, "xra1n-jbboot: cannot move %s aside: %s\n",
                path, strerror(errno));
        return 0;
    }
    printf("xra1n-jbboot: moved %s to %s\n", path, destination);
    return 1;
}

static int is_expected_link(const char *path, const char *target)
{
    struct stat status;
    char destination[PATH_MAX];
    if (lstat(path, &status) != 0 || !S_ISLNK(status.st_mode) ||
        status.st_uid != 0) {
        return 0;
    }
    ssize_t amount = readlink(path, destination, sizeof(destination) - 1);
    if (amount < 0) {
        return 0;
    }
    destination[amount] = '\0';
    return strcmp(destination, target) == 0;
}

static int ensure_private_directory(const char *path)
{
    struct stat status;
    if (lstat(path, &status) == 0) {
        if (S_ISDIR(status.st_mode) && status.st_uid == 0 &&
            (status.st_mode & (S_IWGRP | S_IWOTH)) == 0) {
            return 1;
        }
        if (!move_aside(path)) {
            return 0;
        }
    } else if (errno != ENOENT) {
        fprintf(stderr, "xra1n-jbboot: cannot inspect %s: %s\n",
                path, strerror(errno));
        return 0;
    }

    if (mkdir(path, 0755) != 0 && errno != EEXIST) {
        fprintf(stderr, "xra1n-jbboot: cannot create %s: %s\n",
                path, strerror(errno));
        return 0;
    }
    return lstat(path, &status) == 0 && S_ISDIR(status.st_mode) &&
           status.st_uid == 0 && (status.st_mode & (S_IWGRP | S_IWOTH)) == 0;
}

static int ensure_link(const char *path, const char *target)
{
    for (int attempt = 0; attempt < 3; attempt++) {
        struct stat status;
        if (is_expected_link(path, target)) {
            return 1;
        }
        if (lstat(path, &status) == 0) {
            if (!move_aside(path)) {
                return 0;
            }
        } else if (errno != ENOENT) {
            fprintf(stderr, "xra1n-jbboot: cannot inspect %s: %s\n",
                    path, strerror(errno));
            return 0;
        }
        if (symlink(target, path) == 0) {
            printf("xra1n-jbboot: linked %s -> %s\n", path, target);
            return 1;
        }
        if (errno != EEXIST) {
            fprintf(stderr, "xra1n-jbboot: cannot link %s: %s\n",
                    path, strerror(errno));
            return 0;
        }
    }
    fprintf(stderr, "xra1n-jbboot: %s kept changing; giving up\n", path);
    return 0;
}

static int refresh_sandbox_extension(void)
{
    sandbox_extension_issue_file_t issue =
        (sandbox_extension_issue_file_t)dlsym(
            RTLD_DEFAULT, "sandbox_extension_issue_file");
    if (issue == NULL) {
        unlink(SANDBOX_EXTENSION_MAILBOX);
        fprintf(stderr,
                "xra1n-jbboot: sandbox_extension_issue_file unavailable\n");
        return 0;
    }

    char *token = issue(SANDBOX_EXTENSION_CLASS, SANDBOX_EXTENSION_PATH, 0);
    if (token == NULL || strchr(token, ';') == NULL) {
        unlink(SANDBOX_EXTENSION_MAILBOX);
        fprintf(stderr, "xra1n-jbboot: could not issue %s for %s\n",
                SANDBOX_EXTENSION_CLASS, SANDBOX_EXTENSION_PATH);
        free(token);
        return 0;
    }

    char temporary[PATH_MAX];
    int amount = snprintf(temporary, sizeof(temporary), "%s.%d.tmp",
                          SANDBOX_EXTENSION_MAILBOX, (int)getpid());
    if (amount < 0 || (size_t)amount >= sizeof(temporary)) {
        fprintf(stderr, "xra1n-jbboot: token mailbox path is too long\n");
        free(token);
        return 0;
    }

    unlink(temporary);
    int fd = open(temporary,
                  O_WRONLY | O_CREAT | O_EXCL | O_NOFOLLOW | O_CLOEXEC,
                  0644);
    if (fd < 0) {
        fprintf(stderr, "xra1n-jbboot: open %s: %s\n",
                temporary, strerror(errno));
        free(token);
        return 0;
    }

    int success = fchown(fd, 0, 0) == 0 && fchmod(fd, 0644) == 0 &&
                  write_all(fd, token, strlen(token)) && fsync(fd) == 0;
    int saved = errno;
    if (close(fd) != 0 && success) {
        success = 0;
        saved = errno;
    }
    if (success && rename(temporary, SANDBOX_EXTENSION_MAILBOX) != 0) {
        success = 0;
        saved = errno;
    }
    free(token);

    if (!success) {
        unlink(temporary);
        unlink(SANDBOX_EXTENSION_MAILBOX);
        fprintf(stderr, "xra1n-jbboot: publish %s: %s\n",
                SANDBOX_EXTENSION_MAILBOX, strerror(saved));
        return 0;
    }

    puts("xra1n-jbboot: sandbox read token refreshed");
    return 1;
}

static int persona_exists(kpersona_info_t get_info)
{
    struct xra1n_persona_info info;
    memset(&info, 0, sizeof(info));
    info.persona_info_version = 1;
    if (get_info(SILEO_PERSONA_ID, &info) != 0) {
        return 0;
    }
    if (info.persona_id != SILEO_PERSONA_ID) {
        fprintf(stderr,
                "xra1n-jbboot: persona query returned unexpected id %u\n",
                info.persona_id);
        return 0;
    }
    printf("xra1n-jbboot: persona 99 ready (type=%d name=%s)\n",
           info.persona_type, info.persona_name);
    return 1;
}

static int ensure_sileo_persona(void)
{
    kpersona_alloc_t allocate =
        (kpersona_alloc_t)dlsym(RTLD_DEFAULT, "kpersona_alloc");
    kpersona_info_t get_info =
        (kpersona_info_t)dlsym(RTLD_DEFAULT, "kpersona_info");
    if (allocate == NULL || get_info == NULL) {
        fprintf(stderr, "xra1n-jbboot: persona API unavailable\n");
        return 0;
    }
    if (persona_exists(get_info)) {
        return 1;
    }

    const int types[] = {
        XRA1N_PERSONA_PRIV,
        XRA1N_PERSONA_SYSTEM,
        XRA1N_PERSONA_MANAGED,
        XRA1N_PERSONA_GUEST,
    };
    for (size_t index = 0; index < sizeof(types) / sizeof(types[0]); index++) {
        struct xra1n_persona_info info;
        memset(&info, 0, sizeof(info));
        info.persona_info_version = 1;
        info.persona_id = SILEO_PERSONA_ID;
        info.persona_type = types[index];
        info.persona_gid = 0;
        info.persona_gmuid = 0;
        (void)snprintf(info.persona_name, sizeof(info.persona_name), "sileo");

        uid_t allocated_id = 0;
        errno = 0;
        if (allocate(&info, &allocated_id) == 0) {
            if (allocated_id != SILEO_PERSONA_ID) {
                fprintf(stderr,
                        "xra1n-jbboot: allocated unexpected persona id %u\n",
                        allocated_id);
                return 0;
            }
            if (!persona_exists(get_info)) {
                fprintf(stderr,
                        "xra1n-jbboot: persona 99 absent after allocation\n");
                return 0;
            }
            puts("xra1n-jbboot: persona 99 created");
            return 1;
        }
        fprintf(stderr,
                "xra1n-jbboot: persona 99 type %d allocation failed: %s\n",
                types[index], strerror(errno));
    }

    fprintf(stderr, "xra1n-jbboot: could not create persona 99\n");
    return 0;
}

static int register_system_apps_once(void)
{
    struct stat status;
    if (lstat(APP_REGISTRATION_MARKER, &status) == 0) {
        if (S_ISREG(status.st_mode) && status.st_uid == 0) {
            puts("xra1n-jbboot: system apps already registered");
            return 1;
        }
        fprintf(stderr, "xra1n-jbboot: unsafe registration marker\n");
        return 0;
    }
    if (errno != ENOENT) {
        fprintf(stderr, "xra1n-jbboot: cannot inspect registration marker: %s\n",
                strerror(errno));
        return 0;
    }

    char *arguments[] = { (char *)UICACHE_PATH, (char *)"-a", NULL };
    pid_t child = 0;
    int result = posix_spawn(&child, UICACHE_PATH, NULL, NULL, arguments, environ);
    if (result != 0) {
        fprintf(stderr, "xra1n-jbboot: cannot spawn uicache: %s\n",
                strerror(result));
        return 0;
    }
    int child_status = 0;
    while (waitpid(child, &child_status, 0) < 0) {
        if (errno == EINTR) continue;
        fprintf(stderr, "xra1n-jbboot: waitpid uicache: %s\n", strerror(errno));
        return 0;
    }
    if (!WIFEXITED(child_status) || WEXITSTATUS(child_status) != 0) {
        fprintf(stderr, "xra1n-jbboot: uicache -a failed (status=%d)\n",
                child_status);
        return 0;
    }

    char temporary[PATH_MAX];
    int amount = snprintf(temporary, sizeof(temporary), "%s.%d.tmp",
                          APP_REGISTRATION_MARKER, (int)getpid());
    if (amount < 0 || (size_t)amount >= sizeof(temporary)) return 0;
    unlink(temporary);
    int fd = open(temporary,
                  O_WRONLY | O_CREAT | O_EXCL | O_NOFOLLOW | O_CLOEXEC,
                  0644);
    static const char contents[] = "22H374-system-apps-v1\n";
    int success = fd >= 0 && fchown(fd, 0, 0) == 0 &&
                  fchmod(fd, 0644) == 0 &&
                  write_all(fd, contents, sizeof(contents) - 1) &&
                  fsync(fd) == 0;
    int saved = errno;
    if (fd >= 0 && close(fd) != 0 && success) {
        success = 0;
        saved = errno;
    }
    if (success && rename(temporary, APP_REGISTRATION_MARKER) != 0) {
        success = 0;
        saved = errno;
    }
    if (!success) {
        unlink(temporary);
        fprintf(stderr, "xra1n-jbboot: publish registration marker: %s\n",
                strerror(saved));
        return 0;
    }
    puts("xra1n-jbboot: system app registration complete");
    return 1;
}

int main(void)
{
    if (getuid() != 0) {
        fprintf(stderr, "xra1n-jbboot: must run as root\n");
        return 1;
    }
    if (!wait_for_bootstrap()) {
        return 2;
    }
    if (!ensure_link(TMP_ROOT "/bin", BOOTSTRAP_BIN) ||
        !ensure_private_directory(TMP_ROOT "/usr") ||
        !ensure_link(TMP_ROOT "/usr/bin", BOOTSTRAP_SBIN)) {
        return 3;
    }
    puts("xra1n-jbboot: PATH links ready");
    if (!ensure_sileo_persona()) {
        return 4;
    }
    if (!refresh_sandbox_extension()) {
        return 5;
    }
    if (!register_system_apps_once()) {
        return 6;
    }

    /* Match Liter8's launch lifecycle: after the one-time boot preparation,
     * replace this process with the native PosterBoard watcher.  exec keeps
     * the watcher in the same launchd job instead of leaving an unsupervised
     * child behind. */
    if (access(PFWATCH_PATH, X_OK) != 0) {
        fprintf(stderr, "xra1n-jbboot: pfwatch unavailable at %s: %s\n",
                PFWATCH_PATH, strerror(errno));
        return 0;
    }
    puts("xra1n-jbboot: entering pfwatch");
    fflush(NULL);
    execl(PFWATCH_PATH, "pfwatch", (char *)NULL);
    fprintf(stderr, "xra1n-jbboot: exec pfwatch failed: %s\n",
            strerror(errno));
    return 7;
}
