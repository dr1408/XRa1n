/* XR 22H374 CommCenter data-family adaptation. */
#include <atomic>
#include <cstddef>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <dlfcn.h>
#include <fcntl.h>
#include <libkern/OSCacheControl.h>
#include <mach/mach.h>
#include <mach-o/dyld.h>
#include <syslog.h>
#include <unistd.h>
#include "../hookd/xra1n_hookd_protocol.h"

static std::atomic<bool> g_patched{false};
static const char kLogPath[] = "/var/jb/tmp/xra1n-commcenter-hook.log";
static constexpr uintptr_t kImageBase = 0x22A89D000ULL;
static constexpr uintptr_t kFamilyIntersection = 0x22A8BCB1CULL;
static constexpr uintptr_t kFamilyIntersectionOffset = kFamilyIntersection - kImageBase;

static kern_return_t hookd_patch(void *address, const uint8_t *data, uint32_t size)
{
    if (address == nullptr || data == nullptr || size == 0 ||
        size > XRA1N_HOOKD_MAX_DATA)
        return KERN_INVALID_ARGUMENT;
    mach_port_t launchd = MACH_PORT_NULL;
    kern_return_t kr = task_get_bootstrap_port(mach_task_self(), &launchd);
    if (kr != KERN_SUCCESS || !MACH_PORT_VALID(launchd))
        return kr == KERN_SUCCESS ? KERN_FAILURE : kr;
    mach_port_t reply_port = mig_get_reply_port();
    if (!MACH_PORT_VALID(reply_port)) {
        mach_port_deallocate(mach_task_self(), launchd);
        return KERN_FAILURE;
    }
    xra1n_hookd_request request{};
    request.hdr.msgh_bits = MACH_MSGH_BITS(MACH_MSG_TYPE_COPY_SEND,
                                            MACH_MSG_TYPE_MAKE_SEND_ONCE);
    request.hdr.msgh_size = sizeof(request);
    request.hdr.msgh_remote_port = launchd;
    request.hdr.msgh_local_port = reply_port;
    request.hdr.msgh_id = XRA1N_HOOKD_MSG_ID;
    request.magic = XRA1N_HOOKD_MAGIC;
    request.action = XRA1N_HOOKD_ACTION_PATCH;
    request.data_size = size;
    request.address = reinterpret_cast<mach_vm_address_t>(address);
    memcpy(request.data, data, size);
    kr = mach_msg(&request.hdr, MACH_SEND_MSG, request.hdr.msgh_size, 0,
                  MACH_PORT_NULL, MACH_MSG_TIMEOUT_NONE, MACH_PORT_NULL);
    mach_port_deallocate(mach_task_self(), launchd);
    if (kr != KERN_SUCCESS) return kr;
    struct {
        xra1n_hookd_reply reply;
        uint8_t trailer[MAX_TRAILER_SIZE];
    } response{};
    response.reply.hdr.msgh_size = sizeof(response.reply);
    kr = mach_msg(&response.reply.hdr, MACH_RCV_MSG | MACH_RCV_TIMEOUT, 0,
                  sizeof(response), reply_port, 5000, MACH_PORT_NULL);
    if (kr != KERN_SUCCESS) return kr;
    const xra1n_hookd_reply &reply = response.reply;
    if (reply.magic != XRA1N_HOOKD_MAGIC ||
        reply.action != XRA1N_HOOKD_ACTION_PATCH)
        return KERN_INVALID_VALUE;
    if (reply.status == KERN_SUCCESS) sys_icache_invalidate(address, size);
    return reply.status;
}

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
    kern_return_t kr = hookd_patch(site, replacement, sizeof(replacement));
    if (kr != KERN_SUCCESS) {
        char line[96];
        snprintf(line, sizeof(line),
                 "XRA1N_COMMCENTER hookd patch failed kr=%d\n", kr);
        marker(line);
        return;
    }
    if (memcmp(site, replacement, sizeof(replacement)) == 0)
        marker("XRA1N_COMMCENTER family translation AND -> ORR active\n");
    else
        marker("XRA1N_COMMCENTER family patch write failed\n");
}

__attribute__((constructor)) static void init(void)
{
    marker("XRA1N_COMMCENTER hook loaded\n");
    _dyld_register_func_for_add_image(image_added);
    marker("XRA1N_COMMCENTER waiting for KCommandDrivers\n");
}
