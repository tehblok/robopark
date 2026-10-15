#include "KeychainBrokerIPC.h"

#include <Security/Security.h>
#include <string.h>

enum {
    RP_ERR_SEC_AUTH_FAILED = -25293,
    RP_MAX_VALUE_SIZE = 1024 * 1024,
};

static bool rp_has_digest_suffix(const char *account, const char *prefix) {
    size_t prefix_length = strlen(prefix);
    if (strncmp(account, prefix, prefix_length) != 0) {
        return false;
    }
    const char *digest = account + prefix_length;
    if (strlen(digest) != 64) {
        return false;
    }
    for (size_t index = 0; index < 64; index++) {
        char character = digest[index];
        if (!((character >= '0' && character <= '9') ||
              (character >= 'a' && character <= 'f'))) {
            return false;
        }
    }
    return true;
}

bool rp_keychain_broker_account_allowed(const char *account) {
    if (account == NULL) {
        return false;
    }
    static const char *exact_accounts[] = {
        "registration",
        "workspace-profile-v1",
        "workspace-browser-session-v1",
        "workspace-storage-key-v1",
        "corporate-write-receipt-index-v1",
    };
    for (size_t index = 0; index < sizeof(exact_accounts) / sizeof(exact_accounts[0]); index++) {
        if (strcmp(account, exact_accounts[index]) == 0) {
            return true;
        }
    }
    return rp_has_digest_suffix(account, "enrollment-key-v1-") ||
        rp_has_digest_suffix(account, "account-claim-key-v1-") ||
        rp_has_digest_suffix(account, "corporate-write-receipt-v1-");
}

bool rp_keychain_broker_operation_allowed(const char *operation) {
    return operation != NULL &&
        (strcmp(operation, "read") == 0 ||
         strcmp(operation, "write") == 0 ||
         strcmp(operation, "delete") == 0 ||
         strcmp(operation, "migrate") == 0 ||
         strcmp(operation, "receipt-seal-status") == 0 ||
         strcmp(operation, "receipt-seal") == 0);
}

bool rp_keychain_broker_payload_size_allowed(size_t size) {
    return size <= RP_MAX_VALUE_SIZE;
}

int32_t rp_keychain_broker_validate_protocol(int64_t version) {
    return version == 1 ? errSecSuccess : RP_ERR_SEC_AUTH_FAILED;
}

static bool rp_valid_identity_parameter(const char *value, size_t maximum_length) {
    if (value == NULL) {
        return false;
    }
    size_t length = strlen(value);
    if (length == 0 || length > maximum_length) {
        return false;
    }
    for (size_t index = 0; index < length; index++) {
        char character = value[index];
        if (!((character >= 'a' && character <= 'z') ||
              (character >= 'A' && character <= 'Z') ||
              (character >= '0' && character <= '9') ||
              character == '.' || character == '-')) {
            return false;
        }
    }
    return true;
}

int32_t rp_keychain_broker_validate_message_sender(
    xpc_object_t message,
    const char *identifier,
    const char *certificate_sha1
) {
    if (message == NULL ||
        !rp_valid_identity_parameter(identifier, 128) ||
        !rp_valid_identity_parameter(certificate_sha1, 40) ||
        strlen(certificate_sha1) != 40) {
        return RP_ERR_SEC_AUTH_FAILED;
    }
    SecCodeRef code = NULL;
    OSStatus status = SecCodeCreateWithXPCMessage(message, kSecCSDefaultFlags, &code);
    if (status != errSecSuccess || code == NULL) {
        if (code != NULL) {
            CFRelease(code);
        }
        return RP_ERR_SEC_AUTH_FAILED;
    }
    CFStringRef identifier_string = CFStringCreateWithCString(
        kCFAllocatorDefault, identifier, kCFStringEncodingUTF8);
    CFStringRef digest_string = CFStringCreateWithCString(
        kCFAllocatorDefault, certificate_sha1, kCFStringEncodingASCII);
    if (identifier_string == NULL || digest_string == NULL) {
        if (identifier_string != NULL) CFRelease(identifier_string);
        if (digest_string != NULL) CFRelease(digest_string);
        CFRelease(code);
        return RP_ERR_SEC_AUTH_FAILED;
    }
    CFStringRef text = CFStringCreateWithFormat(
        kCFAllocatorDefault, NULL,
        CFSTR("identifier \"%@\" and certificate leaf = H\"%@\""),
        identifier_string, digest_string);
    SecRequirementRef requirement = NULL;
    status = text == NULL ? errSecParam :
        SecRequirementCreateWithString(text, kSecCSDefaultFlags, &requirement);
    if (status == errSecSuccess && requirement != NULL) {
        status = SecCodeCheckValidity(code, kSecCSDefaultFlags, requirement);
    }
    if (requirement != NULL) CFRelease(requirement);
    if (text != NULL) CFRelease(text);
    CFRelease(digest_string);
    CFRelease(identifier_string);
    CFRelease(code);
    return status == errSecSuccess ? errSecSuccess : RP_ERR_SEC_AUTH_FAILED;
}

bool rp_keychain_broker_peer_euid_matches(xpc_connection_t peer, uid_t expected_euid) {
    return peer != NULL && xpc_connection_get_euid(peer) == expected_euid;
}

int32_t rp_keychain_broker_disable_keychain_ui(void) {
#pragma clang diagnostic push
#pragma clang diagnostic ignored "-Wdeprecated-declarations"
    OSStatus status = SecKeychainSetUserInteractionAllowed(false);
#pragma clang diagnostic pop
    return status;
}
