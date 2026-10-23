#include <fcntl.h>
#include <stdio.h>
#include <string.h>
#include <unistd.h>

static const char *const log_path = "/private/var/tmp/xra1n-admission.log";

int main(int argc, char **argv) {
    const char *name = argc > 0 && argv[0] ? argv[0] : "(unknown)";
    int fd = open(log_path, O_WRONLY | O_CREAT | O_APPEND, 0644);
    if (fd < 0) return 70;
    dprintf(fd, "MAIN %s\n", name);
    fsync(fd);
    close(fd);
    write(STDOUT_FILENO, "ADMISSION_PROBE_MAIN_OK\n", 24);
    return 0;
}
