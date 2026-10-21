/* Narrow IMS request adaptation for XR 22H374 CommCenter. */
#include <atomic>
#include <cstddef>
#include <cstdint>
#include <cstring>
#include <dlfcn.h>
#include <fcntl.h>
#include <mach-o/dyld.h>
#include <syslog.h>
#include <unistd.h>

/* dyld-interposing.h is private and absent from the public iPhoneOS SDK, but
 * this stable dyld SPI is exported on the target build. */
struct dyld_interpose_tuple {
    const void *replacement;
    const void *replacee;
};
extern "C" void dyld_dynamic_interpose(const mach_header *,
                                        const dyld_interpose_tuple *, size_t);

#define DYLD_INTERPOSE(replacement, replacee)                                      \
    __attribute__((used)) static struct {                                          \
        const void *replacement;                                                   \
        const void *replacee;                                                      \
    } interpose_##replacement __attribute__((section("__DATA,__interpose"))) = {  \
        (const void *)(uintptr_t)&replacement,                                     \
        (const void *)(uintptr_t)&replacee                                         \
    }

using start_data_call_ctor_t = void (*)(void *);
using send_proxy_ctor_t = void (*)(void *, void *, void *);

static start_data_call_ctor_t g_start_data_call_ctor;
static send_proxy_ctor_t g_send_proxy_ctor;

static std::atomic<uintptr_t> g_start_request_vtable{0};
static std::atomic<bool> g_rebound{false};
static std::atomic<bool> g_start_seen{false};
static std::atomic<bool> g_send_seen{false};
static const mach_header *g_interposer_image;
static const char kLogPath[] = "/var/jb/tmp/xra1n-commcenter-hook.log";

static void marker(const char *line)
{
    int fd = open(kLogPath, O_WRONLY | O_CREAT | O_APPEND | O_CLOEXEC, 0644);
    if (fd >= 0) {
        (void)write(fd, line, strlen(line));
        close(fd);
    }
    syslog(LOG_NOTICE, "%s", line);
}

static void replacement_start_data_call_ctor(void *self)
{
    if (!g_start_seen.exchange(true))
        marker("XRA1N_COMMCENTER start constructor intercepted\n");
    g_start_data_call_ctor(self);
    if (self != nullptr) {
        g_start_request_vtable.store(*(uintptr_t *)self, std::memory_order_release);
    }
}

static bool exact_ims_apn(uint8_t *request)
{
    /* ARI array<char,101>: begin/end/capacity at request + 0x48. */
    const uint8_t *begin = *(const uint8_t **)(request + 0x48);
    const uint8_t *end = *(const uint8_t **)(request + 0x50);
    if (begin == nullptr || end == nullptr || end < begin || end - begin != 4)
        return false;
    return begin[0] == 'i' && begin[1] == 'm' && begin[2] == 's' && begin[3] == 0;
}

static void replacement_send_proxy_ctor(void *self, void *client, void *message)
{
    if (!g_send_seen.exchange(true))
        marker("XRA1N_COMMCENTER send constructor intercepted\n");
    if (message != nullptr) {
        uint8_t *request = (uint8_t *)message;
        uintptr_t expected = g_start_request_vtable.load(std::memory_order_acquire);
        if (expected != 0 && *(uintptr_t *)request == expected && exact_ims_apn(request)) {
            int *ip_type = *(int **)(request + 0x60);
            if (ip_type != nullptr && *ip_type == 2) {
                *ip_type = 3;
                marker("XRA1N_COMMCENTER ims ip_type 2 -> 3\n");
            } else {
                marker("XRA1N_COMMCENTER ims request seen; ip_type unchanged\n");
            }
        }
    }
    g_send_proxy_ctor(self, client, message);
}

static void image_added(const mach_header *image, intptr_t)
{
    Dl_info info{};
    if (image == nullptr || dladdr(image, &info) == 0 || info.dli_fname == nullptr ||
        strstr(info.dli_fname, "/libCommCenterKCommandDrivers.dylib") == nullptr)
        return;

    void *start = dlsym(RTLD_DEFAULT,
        "_ZN6AriSdk33ARI_IBICallPsStartDataCallReq_SDKC1Ev");
    void *send = dlsym(RTLD_DEFAULT,
        "_ZN3ice16SendMsgBaseProxyC1ENSt3__110shared_ptrINS_6ClientEEERN6AriSdk7MsgBaseE");
    if (start == nullptr || send == nullptr) {
        marker("XRA1N_COMMCENTER dyld interpose symbols unavailable\n");
        return;
    }
    bool expected = false;
    if (!g_rebound.compare_exchange_strong(expected, true))
        return;

    g_start_data_call_ctor = reinterpret_cast<start_data_call_ctor_t>(start);
    g_send_proxy_ctor = reinterpret_cast<send_proxy_ctor_t>(send);
    const dyld_interpose_tuple tuples[] = {
        { reinterpret_cast<const void *>(&replacement_start_data_call_ctor), start },
        { reinterpret_cast<const void *>(&replacement_send_proxy_ctor), send },
    };
    dyld_dynamic_interpose(g_interposer_image, tuples,
                           sizeof(tuples) / sizeof(tuples[0]));
    marker("XRA1N_COMMCENTER dyld imports rebound\n");
}

__attribute__((constructor)) static void init(void)
{
    marker("XRA1N_COMMCENTER hook loaded\n");

    Dl_info self_info{};
    if (dladdr(reinterpret_cast<const void *>(&init), &self_info) == 0 ||
        self_info.dli_fbase == nullptr) {
        marker("XRA1N_COMMCENTER interposer image unavailable\n");
        return;
    }
    g_interposer_image = reinterpret_cast<const mach_header *>(self_info.dli_fbase);

    /* libinjector loads this tweak after KCommandDrivers has already bound its
     * imports.  Ask dyld to rebind that image's C1 constructor imports rather
     * than trying to trampoline the tiny C1/C2 aliases in libARI/libICEClient. */
    _dyld_register_func_for_add_image(image_added);
    marker("XRA1N_COMMCENTER waiting for KCommandDrivers\n");
}
