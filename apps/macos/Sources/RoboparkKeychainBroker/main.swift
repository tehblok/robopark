import Foundation
import LocalAuthentication
import Security
import XPC
import KeychainBrokerIPC

private enum StoredState {
    case absent
    case value(Data)
    case deleted
}

private enum BrokerStore {
    private static let service = "ru.robopark.keychain-broker-v1"
    private static let valueMarker = Data("value-v1".utf8)
    private static let deletedMarker = Data("deleted-v1".utf8)
    private static let receiptPrefix = "corporate-write-receipt-v1-"
    private static let receiptSealAccount = "internal-receipt-migration-sealed-v1"
    private static let receiptSealValue = Data("sealed-v1".utf8)
    private static let authenticationContext: LAContext = {
        let context = LAContext()
        context.interactionNotAllowed = true
        return context
    }()

    private static func query(_ account: String) -> [String: Any] {
        [kSecClass as String: kSecClassGenericPassword,
         kSecAttrService as String: service,
         kSecAttrAccount as String: account,
         kSecUseAuthenticationContext as String: authenticationContext]
    }

    static func read(_ account: String) throws -> StoredState {
        var request = query(account)
        request[kSecReturnAttributes as String] = true
        request[kSecReturnData as String] = true
        request[kSecMatchLimit as String] = kSecMatchLimitOne
        var item: CFTypeRef?
        let status = SecItemCopyMatching(request as CFDictionary, &item)
        if status == errSecItemNotFound { return .absent }
        guard status == errSecSuccess, let fields = item as? [String: Any],
              let marker = fields[kSecAttrGeneric as String] as? Data,
              let data = fields[kSecValueData as String] as? Data else {
            throw NSError(domain: NSOSStatusErrorDomain,
                          code: Int(status == errSecSuccess ? errSecDecode : status))
        }
        if marker == deletedMarker { return .deleted }
        guard marker == valueMarker, rp_keychain_broker_payload_size_allowed(data.count) else {
            throw NSError(domain: NSOSStatusErrorDomain, code: Int(errSecDecode))
        }
        return .value(data)
    }

    private static func store(_ state: StoredState, account: String) throws {
        let data: Data
        let marker: Data
        switch state {
        case let .value(value): data = value; marker = valueMarker
        case .deleted: data = Data(); marker = deletedMarker
        case .absent: throw NSError(domain: NSOSStatusErrorDomain, code: Int(errSecParam))
        }
        guard rp_keychain_broker_payload_size_allowed(data.count) else {
            throw NSError(domain: NSOSStatusErrorDomain, code: Int(errSecParam))
        }
        let attributes: [String: Any] = [
            kSecValueData as String: data,
            kSecAttrGeneric as String: marker,
            kSecAttrAccessible as String: kSecAttrAccessibleWhenUnlockedThisDeviceOnly,
        ]
        var status = SecItemUpdate(query(account) as CFDictionary, attributes as CFDictionary)
        if status == errSecItemNotFound {
            status = SecItemAdd(query(account).merging(attributes) { _, value in value } as CFDictionary, nil)
        }
        guard status == errSecSuccess else {
            throw NSError(domain: NSOSStatusErrorDomain, code: Int(status))
        }
    }

    static func write(_ data: Data, account: String) throws {
        if account.hasPrefix(receiptPrefix), try !receiptMigrationSealed() {
            throw NSError(domain: NSOSStatusErrorDomain, code: Int(errSecAuthFailed))
        }
        try store(.value(data), account: account)
    }

    static func delete(_ account: String) throws {
        if account.hasPrefix(receiptPrefix) {
            guard try receiptMigrationSealed() else {
                throw NSError(domain: NSOSStatusErrorDomain, code: Int(errSecAuthFailed))
            }
            let status = SecItemDelete(query(account) as CFDictionary)
            guard status == errSecSuccess || status == errSecItemNotFound else {
                throw NSError(domain: NSOSStatusErrorDomain, code: Int(status))
            }
            return
        }
        try store(.deleted, account: account)
    }

    static func migrateIfAbsent(_ data: Data, account: String) throws -> StoredState {
        let current = try read(account)
        guard case .absent = current else { return current }
        if account.hasPrefix(receiptPrefix), try receiptMigrationSealed() { return .absent }
        try store(.value(data), account: account)
        return .value(data)
    }

    static func receiptMigrationSealed() throws -> Bool {
        switch try read(receiptSealAccount) {
        case .absent: return false
        case let .value(data):
            guard data == receiptSealValue else {
                throw NSError(domain: NSOSStatusErrorDomain, code: Int(errSecDecode))
            }
            return true
        case .deleted:
            throw NSError(domain: NSOSStatusErrorDomain, code: Int(errSecDecode))
        }
    }

    static func sealReceiptMigration() throws {
        if try receiptMigrationSealed() { return }
        let request: [String: Any] = [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: service,
            kSecReturnAttributes as String: true,
            kSecMatchLimit as String: kSecMatchLimitAll,
            kSecUseAuthenticationContext as String: authenticationContext,
        ]
        var result: CFTypeRef?
        let status = SecItemCopyMatching(request as CFDictionary, &result)
        if status != errSecItemNotFound {
            guard status == errSecSuccess else {
                throw NSError(domain: NSOSStatusErrorDomain, code: Int(status))
            }
            let rows: [[String: Any]]
            if let values = result as? [[String: Any]] { rows = values }
            else if let value = result as? [String: Any] { rows = [value] }
            else { throw NSError(domain: NSOSStatusErrorDomain, code: Int(errSecDecode)) }
            for row in rows {
                guard let account = row[kSecAttrAccount as String] as? String,
                      account.hasPrefix(receiptPrefix),
                      account.withCString({ rp_keychain_broker_account_allowed($0) }) else { continue }
                guard let marker = row[kSecAttrGeneric as String] as? Data else {
                    throw NSError(domain: NSOSStatusErrorDomain, code: Int(errSecDecode))
                }
                if marker == deletedMarker {
                    let deleteStatus = SecItemDelete(query(account) as CFDictionary)
                    guard deleteStatus == errSecSuccess || deleteStatus == errSecItemNotFound else {
                        throw NSError(domain: NSOSStatusErrorDomain, code: Int(deleteStatus))
                    }
                } else if marker != valueMarker {
                    throw NSError(domain: NSOSStatusErrorDomain, code: Int(errSecDecode))
                }
            }
        }
        try store(.value(receiptSealValue), account: receiptSealAccount)
    }
}

private final class BrokerServer {
    private final class Session {
        let listener: xpc_connection_t
        var peer: xpc_connection_t?
        init(listener: xpc_connection_t) { self.listener = listener }
    }

    private let queue = DispatchQueue(label: "ru.robopark.keychain-broker.serial")
    private let service = "ru.robopark.keychain-broker.v1"
    private let agentIdentifier = "ru.robopark.agent"
    private let certificateSHA1 = "5d22f72e3757592561ff874b90268fcc541c767f"
    private lazy var agentRequirement = "identifier \"\(agentIdentifier)\" and certificate leaf = H\"\(certificateSHA1)\""
    private let protocolVersion: Int64 = 1
    private var sessions: [String: Session] = [:]

    private func errorCode(_ error: Error) -> Int32 {
        let code = (error as NSError).code
        return code >= Int(Int32.min) && code <= Int(Int32.max) ? Int32(code) : errSecAuthFailed
    }

    private func required(_ dictionary: xpc_object_t, _ key: String,
                          _ type: xpc_type_t) -> xpc_object_t? {
        guard let value = xpc_dictionary_get_value(dictionary, key), xpc_get_type(value) == type else {
            return nil
        }
        return value
    }

    private func requestHeader(_ message: xpc_object_t) -> (operation: String, nonce: String)? {
        guard xpc_get_type(message) == XPC_TYPE_DICTIONARY,
              required(message, "protocol", XPC_TYPE_INT64) != nil,
              xpc_dictionary_get_int64(message, "protocol") == protocolVersion,
              required(message, "operation", XPC_TYPE_STRING) != nil,
              required(message, "nonce", XPC_TYPE_STRING) != nil,
              let operationPointer = xpc_dictionary_get_string(message, "operation"),
              let noncePointer = xpc_dictionary_get_string(message, "nonce") else { return nil }
        let operation = String(cString: operationPointer)
        let nonce = String(cString: noncePointer)
        guard UUID(uuidString: nonce) != nil, nonce.utf8.count == 36 else { return nil }
        return (operation, nonce)
    }

    private func authenticated(_ message: xpc_object_t, peer: xpc_connection_t) -> Bool {
        rp_keychain_broker_peer_euid_matches(peer, geteuid()) &&
            agentIdentifier.withCString { identifier in
                certificateSHA1.withCString { digest in
                    rp_keychain_broker_validate_message_sender(message, identifier, digest) == errSecSuccess
                }
            }
    }

    private func reply(to message: xpc_object_t, peer: xpc_connection_t,
                       nonce: String, status: Int32,
                       state: StoredState? = nil, endpoint: xpc_endpoint_t? = nil,
                       sealed: Bool? = nil) {
        guard let response = xpc_dictionary_create_reply(message) else { return }
        xpc_dictionary_set_int64(response, "protocol", protocolVersion)
        xpc_dictionary_set_string(response, "nonce", nonce)
        xpc_dictionary_set_int64(response, "status", Int64(status))
        if let endpoint { xpc_dictionary_set_value(response, "endpoint", endpoint) }
        if let sealed { xpc_dictionary_set_bool(response, "sealed", sealed) }
        if let state {
            switch state {
            case .absent: xpc_dictionary_set_int64(response, "state", 0)
            case let .value(data):
                xpc_dictionary_set_int64(response, "state", 1)
                data.withUnsafeBytes { bytes in
                    xpc_dictionary_set_data(response, "data", bytes.baseAddress, bytes.count)
                }
            case .deleted: xpc_dictionary_set_int64(response, "state", 2)
            }
        }
        xpc_connection_send_message(peer, response)
    }

    private func openSession(for message: xpc_object_t, peer: xpc_connection_t, nonce: String) {
        guard sessions.count < 32,
              xpc_dictionary_get_value(message, "account") == nil,
              xpc_dictionary_get_value(message, "data") == nil else {
            reply(to: message, peer: peer, nonce: nonce, status: errSecAuthFailed)
            return
        }
        let identifier = UUID().uuidString.lowercased()
        let listener = xpc_connection_create(nil, queue)
        guard xpc_connection_set_peer_code_signing_requirement(listener, agentRequirement) == 0 else {
            xpc_connection_cancel(listener)
            reply(to: message, peer: peer, nonce: nonce, status: errSecAuthFailed)
            return
        }
        xpc_connection_set_event_handler(listener) { [weak self] privatePeer in
            guard xpc_get_type(privatePeer) == XPC_TYPE_CONNECTION else { return }
            self?.accept(privatePeer, session: identifier)
        }
        sessions[identifier] = Session(listener: listener)
        xpc_connection_activate(listener)
        let endpoint = xpc_endpoint_create(listener)
        reply(to: message, peer: peer, nonce: nonce, status: errSecSuccess, endpoint: endpoint)
        queue.asyncAfter(deadline: .now() + 5) { [weak self] in self?.expire(identifier) }
    }

    private func expire(_ identifier: String) {
        guard let session = sessions.removeValue(forKey: identifier) else { return }
        xpc_connection_cancel(session.listener)
        if let peer = session.peer { xpc_connection_cancel(peer) }
    }

    private func accept(_ peer: xpc_connection_t, session identifier: String) {
        guard let session = sessions[identifier], session.peer == nil else {
            xpc_connection_cancel(peer)
            return
        }
        session.peer = peer
        xpc_connection_set_target_queue(peer, queue)
        var consumed = false
        xpc_connection_set_event_handler(peer) { [weak self] message in
            guard let self else { xpc_connection_cancel(peer); return }
            guard !consumed, xpc_get_type(message) == XPC_TYPE_DICTIONARY else {
                xpc_connection_cancel(peer)
                return
            }
            consumed = true
            self.handleOperation(message, peer: peer)
            xpc_connection_send_barrier(peer) { [weak self] in self?.expire(identifier) }
        }
        xpc_connection_activate(peer)
    }

    private func handleOperation(_ message: xpc_object_t, peer: xpc_connection_t) {
        guard let header = requestHeader(message),
              authenticated(message, peer: peer),
              header.operation.withCString({ rp_keychain_broker_operation_allowed($0) }) else {
            return
        }
        if header.operation == "receipt-seal-status" || header.operation == "receipt-seal" {
            guard xpc_dictionary_get_value(message, "account") == nil,
                  xpc_dictionary_get_value(message, "data") == nil else {
                reply(to: message, peer: peer, nonce: header.nonce, status: errSecAuthFailed)
                return
            }
            do {
                if header.operation == "receipt-seal" { try BrokerStore.sealReceiptMigration() }
                reply(to: message, peer: peer, nonce: header.nonce, status: errSecSuccess,
                      sealed: try BrokerStore.receiptMigrationSealed())
            } catch {
                reply(to: message, peer: peer, nonce: header.nonce, status: errorCode(error))
            }
            return
        }
        guard
              required(message, "account", XPC_TYPE_STRING) != nil,
              let accountPointer = xpc_dictionary_get_string(message, "account") else {
            return
        }
        let account = String(cString: accountPointer)
        guard account.utf8.count <= 128,
              account.withCString({ rp_keychain_broker_account_allowed($0) }) else {
            reply(to: message, peer: peer, nonce: header.nonce, status: errSecAuthFailed)
            return
        }
        do {
            switch header.operation {
            case "read":
                guard xpc_dictionary_get_value(message, "data") == nil else { throw NSError(domain: NSOSStatusErrorDomain, code: Int(errSecAuthFailed)) }
                reply(to: message, peer: peer, nonce: header.nonce, status: errSecSuccess,
                      state: try BrokerStore.read(account))
            case "write", "migrate":
                guard let value = required(message, "data", XPC_TYPE_DATA) else { throw NSError(domain: NSOSStatusErrorDomain, code: Int(errSecAuthFailed)) }
                let length = xpc_data_get_length(value)
                guard rp_keychain_broker_payload_size_allowed(length) else { throw NSError(domain: NSOSStatusErrorDomain, code: Int(errSecAuthFailed)) }
                let data: Data
                if length == 0 { data = Data() }
                else {
                    guard let bytes = xpc_data_get_bytes_ptr(value) else { throw NSError(domain: NSOSStatusErrorDomain, code: Int(errSecAuthFailed)) }
                    data = Data(bytes: bytes, count: length)
                }
                if header.operation == "write" {
                    try BrokerStore.write(data, account: account)
                    reply(to: message, peer: peer, nonce: header.nonce, status: errSecSuccess)
                } else {
                    let state = try BrokerStore.migrateIfAbsent(data, account: account)
                    reply(to: message, peer: peer, nonce: header.nonce, status: errSecSuccess, state: state)
                }
            case "delete":
                guard xpc_dictionary_get_value(message, "data") == nil else { throw NSError(domain: NSOSStatusErrorDomain, code: Int(errSecAuthFailed)) }
                try BrokerStore.delete(account)
                reply(to: message, peer: peer, nonce: header.nonce, status: errSecSuccess)
            default:
                reply(to: message, peer: peer, nonce: header.nonce, status: errSecAuthFailed)
            }
        } catch {
            reply(to: message, peer: peer, nonce: header.nonce, status: errorCode(error))
        }
    }

    private func acceptNamed(_ peer: xpc_connection_t) {
        xpc_connection_set_target_queue(peer, queue)
        var consumed = false
        xpc_connection_set_event_handler(peer) { [weak self] message in
            guard let self, !consumed else { xpc_connection_cancel(peer); return }
            guard let header = self.requestHeader(message), header.operation == "hello",
                  self.authenticated(message, peer: peer) else { xpc_connection_cancel(peer); return }
            consumed = true
            self.openSession(for: message, peer: peer, nonce: header.nonce)
            xpc_connection_send_barrier(peer) { xpc_connection_cancel(peer) }
        }
        xpc_connection_activate(peer)
    }

    func run() -> Never {
        guard rp_keychain_broker_disable_keychain_ui() == errSecSuccess else { exit(78) }
        let listener = xpc_connection_create_mach_service(
            service, queue, UInt64(XPC_CONNECTION_MACH_SERVICE_LISTENER))
        guard xpc_connection_set_peer_code_signing_requirement(listener, agentRequirement) == 0 else { exit(78) }
        xpc_connection_set_event_handler(listener) { [weak self] peer in
            guard xpc_get_type(peer) == XPC_TYPE_CONNECTION else { return }
            self?.acceptNamed(peer)
        }
        xpc_connection_activate(listener)
        dispatchMain()
    }
}

@main
struct RoboparkKeychainBrokerMain {
    static func main() {
        let server = BrokerServer()
        server.run()
    }
}
