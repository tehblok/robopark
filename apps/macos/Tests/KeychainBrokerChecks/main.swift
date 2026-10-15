import Foundation
import KeychainBrokerIPC
import XPC

private var checks = 0

private func check(_ condition: @autoclosure () -> Bool, _ message: String) {
    checks += 1
    guard condition() else {
        fputs("FAIL: \(message)\n", stderr)
        exit(1)
    }
}

private func allowedAccount(_ account: String) -> Bool {
    account.withCString { rp_keychain_broker_account_allowed($0) }
}

private func allowedOperation(_ operation: String) -> Bool {
    operation.withCString { rp_keychain_broker_operation_allowed($0) }
}

let digest = String(repeating: "a", count: 64)
for account in [
    "registration",
    "workspace-profile-v1",
    "workspace-browser-session-v1",
    "workspace-storage-key-v1",
    "corporate-write-receipt-index-v1",
    "enrollment-key-v1-\(digest)",
    "account-claim-key-v1-\(digest)",
    "corporate-write-receipt-v1-\(digest)",
] {
    check(allowedAccount(account), "broker admits required account \(account)")
}

for account in [
    "", "Registration", "workspace-profile-v2", "registration-extra",
    "enrollment-key-v1-" + String(repeating: "a", count: 63),
    "account-claim-key-v1-" + String(repeating: "g", count: 64),
    "corporate-write-receipt-v1-" + digest + "-extra",
    "../../registration",
] {
    check(!allowedAccount(account), "broker rejects unapproved account \(account)")
}

for operation in ["read", "write", "delete", "migrate", "receipt-seal-status", "receipt-seal"] {
    check(allowedOperation(operation), "broker admits operation \(operation)")
}
for operation in ["", "list", "read-all", "READ", "write "] {
    check(!allowedOperation(operation), "broker rejects operation \(operation)")
}

check(rp_keychain_broker_payload_size_allowed(0), "empty values are representable")
check(rp_keychain_broker_payload_size_allowed(1_048_576), "one MiB value is admitted")
check(!rp_keychain_broker_payload_size_allowed(1_048_577), "oversized value is rejected")
check(rp_keychain_broker_validate_protocol(1) == 0, "protocol one is admitted")
check(rp_keychain_broker_validate_protocol(0) == -25293, "missing protocol fails authenticated")
check(rp_keychain_broker_validate_protocol(2) == -25293, "unknown protocol fails authenticated")

let unauthenticatedMessage = xpc_dictionary_create(nil, nil, 0)
check(rp_keychain_broker_validate_message_sender(unauthenticatedMessage,
    "ru.robopark.agent", "5d22f72e3757592561ff874b90268fcc541c767f") == -25293,
    "dictionary without an XPC audit token is rejected")
check(rp_keychain_broker_validate_message_sender(unauthenticatedMessage,
    "ru.robopark.agent", "short") == -25293,
    "malformed certificate pin is rejected before code lookup")
check(!rp_keychain_broker_peer_euid_matches(nil, geteuid()),
    "missing peer cannot satisfy same-user authorization")

print("KeychainBrokerChecks: \(checks) passed")
