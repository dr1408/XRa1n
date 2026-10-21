#include "lhook_session.h"
#include <stdio.h>
#include <stdlib.h>
#include <errno.h>
#include <sys/stat.h>

int main(int argc, char **argv) {
    if (argc != 2) goto usage;
    if (!strcmp(argv[1], "status")) {
        puts(lhook_session_enabled() ? "enabled for this boot" : "disabled for this boot");
        return 0;
    }
    if (!strcmp(argv[1], "disable")) {
        if (unlink(LHOOK_ENABLE_PATH) && errno != ENOENT) { perror("disable"); return 1; }
        puts("disabled for future spawns; already loaded tweaks remain until their process exits");
        return 0;
    }
    if (!strcmp(argv[1], "enable")) {
        char boot[64] = {0};
        if (!lhook_boot_id(boot)) { fputs("boot-session UUID unavailable; injection stays disabled\n", stderr); return 1; }
        char temporary[1024];
        if (snprintf(temporary, sizeof temporary, "%s.XXXXXX", LHOOK_ENABLE_PATH) >= (int)sizeof temporary) return 1;
        int fd = mkstemp(temporary);
        if (fd < 0) { perror("enable"); return 1; }
        int ok = fchmod(fd, 0644) == 0 && write(fd, boot, 36) == 36 && fsync(fd) == 0;
        if (close(fd)) ok = 0;
        if (ok && rename(temporary, LHOOK_ENABLE_PATH) == 0) {
            puts("enabled for this boot only; reboot will disable injection");
            return 0;
        }
        unlink(temporary);
        fputs("could not publish boot-session flag\n", stderr);
        return 1;
    }
usage:
    fputs("usage: lhookctl enable|disable|status\n", stderr);
    return 2;
}
