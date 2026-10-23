/*
 * Constructor-only probe for the future XR lockdownd pairing fallback.
 *
 * This deliberately does not interpose Security.framework yet.  Its only job
 * is to prove that an LC_LOAD_WEAK_DYLIB added to lockdownd is loaded during a
 * normal boot.  The Data-volume marker survives reboot and can be inspected
 * later from SSHRD when pre-pair idevicesyslog is unavailable.
 */

#include <errno.h>
#include <fcntl.h>
#include <limits.h>
#include <mach-o/dyld.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <sys/stat.h>
#include <sys/types.h>
#include <syslog.h>
#include <unistd.h>

static const char kProbeLog[] =
    "/private/var/root/Library/Lockdown/xra1n-l8pair-probe.log";

__attribute__((constructor))
static void l8pair_probe_init(void)
{
    char executable[PATH_MAX] = {0};
    uint32_t executable_size = sizeof(executable);
    int path_result = _NSGetExecutablePath(executable, &executable_size);
    pid_t pid = getpid();

    if (path_result != 0) {
        strlcpy(executable, "<path-too-long>", sizeof(executable));
    }

    syslog(LOG_NOTICE, "XRA1N_L8PAIR_PROBE loaded pid=%d executable=%s",
           pid, executable);

    int fd = open(kProbeLog, O_WRONLY | O_CREAT | O_APPEND | O_CLOEXEC, 0600);
    if (fd < 0) {
        syslog(LOG_ERR, "XRA1N_L8PAIR_PROBE marker open failed errno=%d", errno);
        return;
    }

    (void)fchmod(fd, 0600);
    dprintf(fd, "XRA1N_L8PAIR_PROBE loaded pid=%d executable=%s\n",
            pid, executable);
    close(fd);
}
