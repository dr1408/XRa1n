/* XR 22H374 CommCenter data-family adaptation. */
#include <atomic>
#include <cstddef>
#include <cstdint>
#include <cstring>
#include <dlfcn.h>
#include <fcntl.h>
#include <mach-o/dyld.h>
#include <syslog.h>
#include <unistd.h>

using mshook_memory_t = void (*)(void *, const uint8_t *, uint64_t);
static mshook_memory_t g_hook_memory;

static std::atomic<bool> g_patched{false};
static const char kLogPath[] = "/var/jb/tmp/xra1n-commcenter-hook.log";
static constexpr uintptr_t kImageBase = 0x22A89D000ULL;
static constexpr uintptr_t kFamilyIntersection = 0x22A8BCB1CULL;
static constexpr uintptr_t kFamilyIntersectionOffset = kFamilyIntersection - kImageBase;

static void marker(const char *line)
{
    int fd = open(kLogPath, O_WRONLY | O_CREAT | O_APPEND | O_CLOEXEC, 0644);
    if (fd >= 0) {
        (void)write(fd, line, strlen(line));
        close(fd);
    }
    syslog(LOG_NOTICE, "%s", line);
}

static void image_added(const mach_header *image, intptr_t)
{
    Dl_info info{};
    if (image == nullptr || dladdr(image, &info) == 0 || info.dli_fname == nullptr ||
        strstr(info.dli_fname, "/libCommCenterKCommandDrivers.dylib") == nullptr)
        return;

    bool expected = false;
    if (!g_patched.compare_exchange_strong(expected, true))
        return;

    uint8_t *site = reinterpret_cast<uint8_t *>(
        reinterpret_cast<uintptr_t>(image) + kFamilyIntersectionOffset);

    /* Pristine: AND W20,W23,W8. This computes translated=requested&allowed,
     * making the VM's IMS request 3&2 become 2. This diagnostic uses the union
     * so requested dual stack is retained while we validate baseband behavior. */
    static const uint8_t pristine[] = {0xf4, 0x02, 0x08, 0x0a};
    static const uint8_t replacement[] = {0xf4, 0x02, 0x08, 0x2a};
    if (memcmp(site, pristine, sizeof(pristine)) != 0) {
        marker("XRA1N_COMMCENTER family patch preimage mismatch\n");
        return;
    }
    if (g_hook_memory == nullptr) {
        marker("XRA1N_COMMCENTER MSHookMemory unavailable\n");
        return;
    }
    g_hook_memory(site, replacement, sizeof(replacement));
    if (memcmp(site, replacement, sizeof(replacement)) == 0)
        marker("XRA1N_COMMCENTER family translation AND -> ORR active\n");
    else
        marker("XRA1N_COMMCENTER family patch write failed\n");
}

__attribute__((constructor)) static void init(void)
{
    marker("XRA1N_COMMCENTER hook loaded\n");
    void *ellekit = dlopen("/var/jb/usr/lib/libellekit.dylib", RTLD_NOW | RTLD_GLOBAL);
    if (ellekit != nullptr)
        g_hook_memory = reinterpret_cast<mshook_memory_t>(dlsym(ellekit, "MSHookMemory"));
    _dyld_register_func_for_add_image(image_added);
    marker("XRA1N_COMMCENTER waiting for KCommandDrivers\n");
}
