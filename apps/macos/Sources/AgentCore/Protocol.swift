import Foundation
import CryptoKit

public enum AgentError: Error, LocalizedError {
    case invalidOrigin, invalidJob, wrongServer, invalidCatalog, expiredCatalog, rollback, missingChannel, unsupportedRelease
    public var errorDescription: String? {
        switch self {
        case .invalidOrigin: return "Укажите HTTPS-адрес сервера без пути, пароля и параметров."
        case .invalidJob: return "Недопустимое задание."
        case .wrongServer: return "Сервер не подтверждён подписью администратора или не совпадает с регистрацией."
        case .invalidCatalog: return "Не удалось проверить подпись каталога обновлений."
        case .expiredCatalog: return "Каталог обновлений устарел. Установленная версия сохранена."
        case .rollback: return "Отклонён устаревший каталог обновлений."
        case .missingChannel: return "Назначенный канал отсутствует в каталоге."
        case .unsupportedRelease: return "Обновление несовместимо с этим агентом."
        }
    }
}

public struct ServerOrigin: Equatable {
    public let url: URL
    public init(_ value: String) throws {
        guard var parts = URLComponents(string: value.trimmingCharacters(in: .whitespacesAndNewlines)),
              parts.scheme == "https", let host = parts.host, !host.isEmpty,
              parts.user == nil, parts.password == nil, parts.query == nil, parts.fragment == nil,
              parts.path.isEmpty || parts.path == "/" else { throw AgentError.invalidOrigin }
        parts.host = host.lowercased(); parts.path = ""
        if parts.port == 443 { parts.port = nil }
        guard let url = parts.url else { throw AgentError.invalidOrigin }
        self.url = url
    }
    public func api(_ path: String) -> URL { url.appendingPathComponent("api").appendingPathComponent(path) }
    public var webSocket: URL {
        var parts = URLComponents(url: api("emergency/bridge/ws"), resolvingAgainstBaseURL: false)!
        parts.scheme = "wss"
        return parts.url!
    }
    public var computeWebSocket: URL {
        var parts = URLComponents(url: api("emergency/bridge/compute/ws"), resolvingAgainstBaseURL: false)!
        parts.scheme = "wss"
        return parts.url!
    }
}

public struct Challenge: Codable {
    public let type: String
    public let nonce: String
    public let environment: String
    public let instance_id: String
    public let protocolVersion: Int
    enum CodingKeys: String, CodingKey { case type, nonce, environment, instance_id; case protocolVersion = "protocol" }
    public init(type: String, nonce: String, environment: String, instance_id: String, protocolVersion: Int) {
        self.type = type; self.nonce = nonce; self.environment = environment
        self.instance_id = instance_id; self.protocolVersion = protocolVersion
    }
    public func sign(key: P256.Signing.PrivateKey, environment: String, instanceID: String) throws -> Data {
        guard type == "challenge", protocolVersion == 1, self.environment == environment,
              instance_id == instanceID, !nonce.isEmpty, nonce.count <= 256,
              !nonce.contains("\n") else { throw AgentError.wrongServer }
        return try key.signature(for: Data("robopark-bridge-v1\n\(instanceID)\n\(environment)\n\(nonce)".utf8)).derRepresentation
    }
    public func signCompute(key: P256.Signing.PrivateKey, environment: String, instanceID: String) throws -> Data {
        guard type == "challenge", protocolVersion == 1, self.environment == environment,
              instance_id == instanceID, !nonce.isEmpty, nonce.count <= 256,
              !nonce.contains("\n") else { throw AgentError.wrongServer }
        return try key.signature(for: Data("robopark-compute-v1\n\(instanceID)\n\(environment)\n\(nonce)".utf8)).derRepresentation
    }
}

public enum EmergencyTarget {
    public static let host = "emergency.sdc.yandex-team.ru"
    public static func url(vin: String) throws -> URL {
        guard vin.range(of: "^YASADR[0-9]{11}$", options: .regularExpression) != nil else { throw AgentError.invalidJob }
        return URL(string: "https://\(host)/api/v1/\(vin)/")!
    }
    public static func normalizeRobot(_ value: String) throws -> String {
        let text = value.trimmingCharacters(in: .whitespacesAndNewlines).uppercased()
        if text.hasPrefix("YASADR") { _ = try url(vin: text); return text }
        let digits = text.hasPrefix("A") || text.hasPrefix("А") ? String(text.dropFirst()) : text
        guard digits.range(of: "^[0-9]{1,11}$", options: .regularExpression) != nil else { throw AgentError.invalidJob }
        return "YASADR" + String(repeating: "0", count: 11 - digits.count) + digits
    }
}

public struct ServerInfo: Codable {
    public let signed_profile: SignedCatalog?
    public let environment: String
    public let instance_id: String
    public let protocolVersion: Int
    enum CodingKeys: String, CodingKey { case environment, instance_id, signed_profile; case protocolVersion = "protocol" }
}

public struct Registration: Codable {
    public var signedProfile: SignedCatalog?
    public var origin: String
    public var deviceID: String
    public var instanceID: String
    public var environment: String
    public var label: String
    public var privateKey: Data
    public var cookie: String = ""
    public var probeVIN: String = ""
    public var channel: String = "rc"
    public var catalogURL: String = ""
    public var minimumGeneration: Int64 = 0
    public var deferredVersion: String = ""
    public var paused: Bool = false
    public init(origin: String, deviceID: String, instanceID: String, environment: String, label: String, privateKey: Data) {
        self.origin = origin; self.deviceID = deviceID; self.instanceID = instanceID
        self.environment = environment; self.label = label; self.privateKey = privateKey
    }
}


public enum LoginPolicy {
    public static let hosts: Set<String> = [EmergencyTarget.host, "passport.yandex-team.ru"]
    public static func allows(_ url: URL) -> Bool {
        guard url.scheme == "https", let host = url.host?.lowercased(), hosts.contains(host),
              url.user == nil, url.password == nil, url.port == nil || url.port == 443 else { return false }
        return true
    }
}
