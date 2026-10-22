#ifndef XRA1N_HOOKD_PROTOCOL_H
#define XRA1N_HOOKD_PROTOCOL_H
#include <mach/mach.h>
#include <stdint.h>
#include <sys/types.h>
#define XRA1N_HOOKD_MAGIC UINT64_C(0x58524131484f4f4b)
#define XRA1N_HOOKD_ACTION_PATCH UINT64_C(1)
#define XRA1N_HOOKD_MAX_DATA 64
#define XRA1N_HOOKD_MSG_ID (0x40000000 | 206)
struct xra1n_hookd_request {
    mach_msg_header_t hdr;
    uint64_t magic;
    uint64_t action;
    pid_t client_pid;
    uint32_t data_size;
    mach_vm_address_t address;
    uint8_t data[XRA1N_HOOKD_MAX_DATA];
};
struct xra1n_hookd_reply {
    mach_msg_header_t hdr;
    uint64_t magic;
    uint64_t action;
    kern_return_t status;
};
#endif
