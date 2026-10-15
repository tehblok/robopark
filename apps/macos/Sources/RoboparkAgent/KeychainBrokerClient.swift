import Foundation
import Security
import XPC
import KeychainBrokerIPC

enum KeychainBrokerStoredState: Equatable {
    case absent
    case value(Data)
    case deleted
}

enum KeychainBrokerClient {
    private static let service = "ru.robopark.keychain-broker.v1"
    private static let brokerIdentifier = "ru.robopark.keychain-broker.v1"
    private static let certificateSHA1 = "5d22f72e3757592561ff874b90268fcc541c767f"
    private static let brokerRequirement = "identifier \"\(brokerIdentifier)\" and certificate leaf = H\"\(certificateSHA1)\""
    private static let protocolVersion: Int64 = 1
    private static let replyQueue = DispatchQueue(label: "ru.robopark.keychain-broker.reply")

    private final class ReplyBox: @unchecked Sendable {
        let semaphore = DispatchSemaphore(value: 0)
        private let lock = NSLock()
        private var response: xpc_object_t?

        func receive(_ value: xpc_object_t) {
            lock.lock()
            if response == nil { response = value }
            lock.unlock()
            semaphore.signal()
        }

        func take() -> xpc_object_t? {
            lock.lock(); defer { lock.unlock() }
            return response
        }
    }

    private static func failure(_ code: Int32 = errSecAuthFailed) -> NSError {
        NSError(domain: "RoboparkKeychain", code: Int(code))
    }

    private static func validateAccount(_ account: String) throws {
        guard account.utf8.count <= 128,
              account.withCString({ rp_keychain_broker_account_allowed($0) }) else {
            throw failure()
        }
    }

    private static func requiredValue(_ dictionary: xpc_object_t, key: String,
                                      type: xpc_type_t) throws -> xpc_object_t {
        guard let value = xpc_dictionary_get_value(dictionary, key), xpc_get_type(value) == type else {
            throw failure()
        }
        return value
    }

    private static func validateReply(_ response: xpc_object_t, nonce: String) throws {
        guard xpc_get_type(response) == XPC_TYPE_DICTIONARY,
              brokerIdentifier.withCString({ identifier in
                  certificateSHA1.withCString { digest in
                      rp_keychain_broker_validate_message_sender(response, identifier, digest)
                  }
              }) == errSecSuccess else { throw failure() }
        _ = try requiredValue(response, key: "protocol", type: XPC_TYPE_INT64)
        _ = try requiredValue(response, key: "nonce", type: XPC_TYPE_STRING)
        _ = try requiredValue(response, key: "status", type: XPC_TYPE_INT64)
        guard xpc_dictionary_get_int64(response, "protocol") == protocolVersion,
              xpc_dictionary_get_string(response, "nonce").map({ String(cString: $0) }) == nonce else {
            throw failure()
        }
        let status = xpc_dictionary_get_int64(response, "status")
        guard status == errSecSuccess, status >= Int64(Int32.min), status <= Int64(Int32.max) else {
            throw failure(Int32(clamping: status))
        }
    }

    private static func send(_ message: xpc_object_t, over connection: xpc_connection_t,
                             nonce: String, timeout: TimeInterval) throws -> xpc_object_t {
        let box = ReplyBox()
        xpc_connection_send_message_with_reply(connection, message, replyQueue) { response in
            box.receive(response)
        }
        guard box.semaphore.wait(timeout: .now() + timeout) == .success,
              let response = box.take() else { throw failure() }
        try validateReply(response, nonce: nonce)
        return response
    }

    private static func message(operation: String, account: String? = nil,
                                data: Data? = nil) -> (xpc_object_t, String) {
        let nonce = UUID().uuidString.lowercased()
        let request = xpc_dictionary_create(nil, nil, 0)
        xpc_dictionary_set_int64(request, "protocol", protocolVersion)
        xpc_dictionary_set_string(request, "operation", operation)
        xpc_dictionary_set_string(request, "nonce", nonce)
        if let account { xpc_dictionary_set_string(request, "account", account) }
        if let data {
            data.withUnsafeBytes { bytes in
                xpc_dictionary_set_data(request, "data", bytes.baseAddress, bytes.count)
            }
        }
        return (request, nonce)
    }

    private static func privateConnection(timeout: TimeInterval) throws -> xpc_connection_t {
        let named = xpc_connection_create_mach_service(service, nil, 0)
        guard xpc_connection_set_peer_code_signing_requirement(named, brokerRequirement) == 0 else {
            xpc_connection_cancel(named)
            throw failure()
        }
        xpc_connection_set_event_handler(named) { _ in }
        xpc_connection_activate(named)
        let (hello, nonce) = message(operation: "hello")
        let response: xpc_object_t
        do { response = try send(hello, over: named, nonce: nonce, timeout: timeout) }
        catch { xpc_connection_cancel(named); throw error }
        xpc_connection_cancel(named)
        let endpoint = try requiredValue(response, key: "endpoint", type: XPC_TYPE_ENDPOINT)
        let connection = xpc_connection_create_from_endpoint(endpoint)
        guard xpc_connection_set_peer_code_signing_requirement(connection, brokerRequirement) == 0 else {
            xpc_connection_cancel(connection)
            throw failure()
        }
        xpc_connection_set_event_handler(connection) { _ in }
        xpc_connection_activate(connection)
        return connection
    }

    private static func request(operation: String, account: String, data: Data?,
                                timeout: TimeInterval) throws -> xpc_object_t {
        try validateAccount(account)
        guard timeout > 0, timeout <= 10,
              operation.withCString({ rp_keychain_broker_operation_allowed($0) }),
              data.map({ rp_keychain_broker_payload_size_allowed($0.count) }) ?? true else {
            throw failure()
        }
        let connection = try privateConnection(timeout: timeout)
        let (request, nonce) = message(operation: operation, account: account, data: data)
        defer { xpc_connection_cancel(connection) }
        return try send(request, over: connection, nonce: nonce, timeout: timeout)
    }

    private static func internalRequest(operation: String, timeout: TimeInterval) throws -> xpc_object_t {
        guard timeout > 0, timeout <= 10,
              operation.withCString({ rp_keychain_broker_operation_allowed($0) }) else { throw failure() }
        let connection = try privateConnection(timeout: timeout)
        let (request, nonce) = message(operation: operation)
        defer { xpc_connection_cancel(connection) }
        return try send(request, over: connection, nonce: nonce, timeout: timeout)
    }

    static func readState(_ account: String, timeout: TimeInterval = 2) throws -> KeychainBrokerStoredState {
        let response = try request(operation: "read", account: account, data: nil, timeout: timeout)
        _ = try requiredValue(response, key: "state", type: XPC_TYPE_INT64)
        switch xpc_dictionary_get_int64(response, "state") {
        case 0:
            guard xpc_dictionary_get_value(response, "data") == nil else { throw failure() }
            return .absent
        case 1:
            let value = try requiredValue(response, key: "data", type: XPC_TYPE_DATA)
            let length = xpc_data_get_length(value)
            guard rp_keychain_broker_payload_size_allowed(length) else { throw failure() }
            if length == 0 { return .value(Data()) }
            guard let bytes = xpc_data_get_bytes_ptr(value) else { throw failure() }
            return .value(Data(bytes: bytes, count: length))
        case 2:
            guard xpc_dictionary_get_value(response, "data") == nil else { throw failure() }
            return .deleted
        default:
            throw failure()
        }
    }

    static func read(_ account: String, timeout: TimeInterval = 2) throws -> Data? {
        guard case let .value(data) = try readState(account, timeout: timeout) else { return nil }
        return data
    }

    static func write(_ data: Data, account: String, timeout: TimeInterval = 2) throws {
        _ = try request(operation: "write", account: account, data: data, timeout: timeout)
    }

    static func migrateIfAbsent(_ data: Data, account: String,
                                timeout: TimeInterval = 2) throws -> KeychainBrokerStoredState {
        let response = try request(operation: "migrate", account: account, data: data, timeout: timeout)
        _ = try requiredValue(response, key: "state", type: XPC_TYPE_INT64)
        switch xpc_dictionary_get_int64(response, "state") {
        case 0:
            guard xpc_dictionary_get_value(response, "data") == nil else { throw failure() }
            return .absent
        case 1:
            let value = try requiredValue(response, key: "data", type: XPC_TYPE_DATA)
            let length = xpc_data_get_length(value)
            guard rp_keychain_broker_payload_size_allowed(length) else { throw failure() }
            if length == 0 { return .value(Data()) }
            guard let bytes = xpc_data_get_bytes_ptr(value) else { throw failure() }
            return .value(Data(bytes: bytes, count: length))
        case 2:
            guard xpc_dictionary_get_value(response, "data") == nil else { throw failure() }
            return .deleted
        default:
            throw failure()
        }
    }

    static func delete(_ account: String, timeout: TimeInterval = 2) throws {
        _ = try request(operation: "delete", account: account, data: nil, timeout: timeout)
    }

    static func receiptMigrationSealed(timeout: TimeInterval = 2) throws -> Bool {
        let response = try internalRequest(operation: "receipt-seal-status", timeout: timeout)
        _ = try requiredValue(response, key: "sealed", type: XPC_TYPE_BOOL)
        return xpc_dictionary_get_bool(response, "sealed")
    }

    static func sealReceiptMigration(timeout: TimeInterval = 2) throws {
        let response = try internalRequest(operation: "receipt-seal", timeout: timeout)
        _ = try requiredValue(response, key: "sealed", type: XPC_TYPE_BOOL)
        guard xpc_dictionary_get_bool(response, "sealed") else { throw failure() }
    }
}
