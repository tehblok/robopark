import Foundation
import Security
import AgentCore

enum SecureStore {
    private static let service = "ru.robopark.agent"
    private static func query(_ account: String) -> [String: Any] {
        [kSecClass as String: kSecClassGenericPassword,
         kSecAttrService as String: service, kSecAttrAccount as String: account]
    }
    static func load() throws -> Registration? {
        var request = query("registration")
        request[kSecReturnData as String] = true
        request[kSecMatchLimit as String] = kSecMatchLimitOne
        var item: CFTypeRef?
        let status = SecItemCopyMatching(request as CFDictionary, &item)
        if status == errSecItemNotFound { return nil }
        guard status == errSecSuccess, let data = item as? Data else {
            throw NSError(domain: "RoboparkKeychain", code: Int(status), userInfo: [NSLocalizedDescriptionKey: "Хранилище Keychain недоступно. Разблокируйте сеанс macOS."])
        }
        return try JSONDecoder().decode(Registration.self, from: data)
    }
    static func save(_ value: Registration) throws {
        let data = try JSONEncoder().encode(value)
        let attributes: [String: Any] = [kSecValueData as String: data,
            kSecAttrAccessible as String: kSecAttrAccessibleWhenUnlockedThisDeviceOnly]
        var status = SecItemUpdate(query("registration") as CFDictionary, attributes as CFDictionary)
        if status == errSecItemNotFound {
            status = SecItemAdd(query("registration").merging(attributes) { _, new in new } as CFDictionary, nil)
        }
        guard status == errSecSuccess else {
            throw NSError(domain: "RoboparkKeychain", code: Int(status), userInfo: [NSLocalizedDescriptionKey: "Не удалось сохранить регистрацию в Keychain."])
        }
    }
    static func remove() throws {
        let status = SecItemDelete(query("registration") as CFDictionary)
        guard status == errSecSuccess || status == errSecItemNotFound else {
            throw NSError(domain: "RoboparkKeychain", code: Int(status))
        }
    }
}
