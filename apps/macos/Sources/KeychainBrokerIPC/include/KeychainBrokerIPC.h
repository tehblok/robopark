#ifndef KEYCHAIN_BROKER_IPC_H
#define KEYCHAIN_BROKER_IPC_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include <sys/types.h>
#include <xpc/xpc.h>

bool rp_keychain_broker_account_allowed(const char *account);
bool rp_keychain_broker_operation_allowed(const char *operation);
bool rp_keychain_broker_payload_size_allowed(size_t size);
int32_t rp_keychain_broker_validate_protocol(int64_t version);
int32_t rp_keychain_broker_validate_message_sender(
    xpc_object_t message,
    const char *identifier,
    const char *certificate_sha1
);
bool rp_keychain_broker_peer_euid_matches(xpc_connection_t peer, uid_t expected_euid);
int32_t rp_keychain_broker_disable_keychain_ui(void);

#endif
