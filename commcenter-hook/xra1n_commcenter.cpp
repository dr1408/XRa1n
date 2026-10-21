/* Narrow IMS request adaptation for XR 22H374 CommCenter. */
#include <atomic>
#include <cstddef>
#include <cstdint>
#include <cstring>
#include <fcntl.h>
#include <syslog.h>
#include <unistd.h>

#define DYLD_INTERPOSE(replacement, replacee)                                      \
    __attribute__((used)) static struct {                                          \
        const void *replacement;                                                   \
        const void *replacee;                                                      \
    } interpose_##replacement __attribute__((section("__DATA,__interpose"))) = {  \
        (const void *)(uintptr_t)&replacement,                                     \
        (const void *)(uintptr_t)&replacee                                         \
    }

extern "C" void ari_start_data_call_ctor(void *)
    __asm__("__ZN6AriSdk33ARI_IBICallPsStartDataCallReq_SDKC1Ev");
extern "C" void send_proxy_ctor(void *, void *, void *)
    __asm__("__ZN3ice16SendMsgBaseProxyC1ENSt3__110shared_ptrINS_6ClientEEERN6AriSdk7MsgBaseE");

static std::atomic<uintptr_t> g_start_request_vtable{0};
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
    ari_start_data_call_ctor(self);
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
    send_proxy_ctor(self, client, message);
}

DYLD_INTERPOSE(replacement_start_data_call_ctor, ari_start_data_call_ctor);
DYLD_INTERPOSE(replacement_send_proxy_ctor, send_proxy_ctor);

__attribute__((constructor)) static void init(void)
{
    marker("XRA1N_COMMCENTER hook loaded\n");
}
