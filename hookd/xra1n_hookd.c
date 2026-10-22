#include "xra1n_hookd_protocol.h"
#include <mach/mach.h>
#include <stddef.h>
#include <stdlib.h>
#include <string.h>

static pid_t token_pid(audit_token_t token) { return (pid_t)token.val[5]; }

static kern_return_t patch_process(const struct xra1n_hookd_request *request)
{
    if (request->client_pid <= 1 || request->data_size == 0 ||
        request->data_size > XRA1N_HOOKD_MAX_DATA ||
        request->address > UINT64_MAX - request->data_size)
        return KERN_INVALID_ARGUMENT;
    task_port_t task = MACH_PORT_NULL;
    kern_return_t kr = task_for_pid(mach_task_self(), request->client_pid, &task);
    if (kr != KERN_SUCCESS || !MACH_PORT_VALID(task))
        return kr == KERN_SUCCESS ? KERN_FAILURE : kr;
    kr = vm_protect(task, (vm_address_t)request->address, request->data_size,
                    false, VM_PROT_READ | VM_PROT_WRITE | VM_PROT_COPY);
    if (kr == KERN_SUCCESS)
        kr = vm_write(task, (vm_address_t)request->address,
                      (vm_offset_t)(uintptr_t)request->data,
                      request->data_size);
    kern_return_t restore = vm_protect(task, (vm_address_t)request->address,
                                       request->data_size, false,
                                       VM_PROT_READ | VM_PROT_EXECUTE);
    mach_port_deallocate(mach_task_self(), task);
    return kr == KERN_SUCCESS ? restore : kr;
}

static void send_reply(struct xra1n_hookd_request *request, kern_return_t status)
{
    struct xra1n_hookd_reply reply = {0};
    mach_msg_type_name_t bits = MACH_MSGH_BITS_REMOTE(request->hdr.msgh_bits);
    if (bits == MACH_MSG_TYPE_COPY_SEND) bits = MACH_MSG_TYPE_MOVE_SEND;
    reply.hdr.msgh_bits = MACH_MSGH_BITS(bits, 0);
    reply.hdr.msgh_size = sizeof(reply);
    reply.hdr.msgh_remote_port = request->hdr.msgh_remote_port;
    reply.hdr.msgh_id = request->hdr.msgh_id + 100;
    reply.magic = XRA1N_HOOKD_MAGIC;
    reply.action = request->action;
    reply.status = status;
    if (mach_msg_send(&reply.hdr) == KERN_SUCCESS) {
        request->hdr.msgh_remote_port = MACH_PORT_NULL;
        request->hdr.msgh_bits &= ~MACH_MSGH_BITS_REMOTE_MASK;
    }
}

int main(void)
{
    mach_port_t server = MACH_PORT_NULL;
    if (mach_port_allocate(mach_task_self(), MACH_PORT_RIGHT_RECEIVE, &server) != KERN_SUCCESS) return 1;
    if (mach_port_insert_right(mach_task_self(), server, server, MACH_MSG_TYPE_MAKE_SEND) != KERN_SUCCESS) return 2;
    mach_port_t *ports = NULL;
    mach_msg_type_number_t count = 0;
    if (mach_ports_lookup(mach_task_self(), &ports, &count) != KERN_SUCCESS ||
        count < 3 || !MACH_PORT_VALID(ports[2])) return 3;
    mach_msg_header_t checkin = {0};
    checkin.msgh_bits = MACH_MSGH_BITS(MACH_MSG_TYPE_COPY_SEND, MACH_MSG_TYPE_MAKE_SEND);
    checkin.msgh_size = sizeof(checkin);
    checkin.msgh_remote_port = ports[2];
    checkin.msgh_local_port = server;
    checkin.msgh_id = XRA1N_HOOKD_MSG_ID;
    if (mach_msg_send(&checkin) != KERN_SUCCESS) return 4;
    vm_deallocate(mach_task_self(), (vm_address_t)ports, count * sizeof(mach_port_t));

    const mach_msg_size_t receive_size = sizeof(struct xra1n_hookd_request) + MAX_TRAILER_SIZE;
    uint8_t *buffer = calloc(1, receive_size);
    if (!buffer) return 5;
    for (;;) {
        memset(buffer, 0, receive_size);
        struct xra1n_hookd_request *request = (struct xra1n_hookd_request *)buffer;
        kern_return_t kr = mach_msg(&request->hdr,
            MACH_RCV_MSG | MACH_RCV_TRAILER_TYPE(MACH_MSG_TRAILER_FORMAT_0) |
            MACH_RCV_TRAILER_ELEMENTS(MACH_RCV_TRAILER_AUDIT),
            0, receive_size, server, MACH_MSG_TIMEOUT_NONE, MACH_PORT_NULL);
        if (kr != KERN_SUCCESS) continue;
        kern_return_t status = KERN_NO_ACCESS;
        mach_msg_audit_trailer_t *trailer = (mach_msg_audit_trailer_t *)
            (buffer + ((request->hdr.msgh_size + 3u) & ~3u));
        if (request->hdr.msgh_size == sizeof(*request) &&
            trailer->msgh_trailer_type == MACH_MSG_TRAILER_FORMAT_0 &&
            trailer->msgh_trailer_size >= sizeof(*trailer) &&
            token_pid(trailer->msgh_audit) == 1 &&
            request->magic == XRA1N_HOOKD_MAGIC &&
            request->action == XRA1N_HOOKD_ACTION_PATCH)
            status = patch_process(request);
        send_reply(request, status);
        mach_msg_destroy(&request->hdr);
    }
}
