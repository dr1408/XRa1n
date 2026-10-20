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
#include <stdint.h>
#include <stdlib.h>
#include <stdio.h>
#include <string.h>
#include <sys/stat.h>
#include <sys/types.h>
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

typedef char *(*sandbox_extension_issue_file_t)(const char *, const char *,
                                                 uint32_t);

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
    if (!refresh_sandbox_extension()) {
        return 4;
    }
    return 0;
}
