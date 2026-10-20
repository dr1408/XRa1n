#define _POSIX_C_SOURCE 200809L

/*
 * xra1n-jbboot - recreate Dropbear's rootless PATH links every normal boot.
 *
 * The stock Dropbear sets remote commands' PATH to include /tmp/bin and
 * /tmp/usr/bin.  Since /tmp is ephemeral, map those two slots to Procursus
 * after Data is available.  Refuse to trust links owned by another uid and
 * preserve any unexpected object by moving it aside for inspection.
 */

#include <errno.h>
#include <limits.h>
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
#define WAIT_SECONDS 180

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
    return 0;
}
