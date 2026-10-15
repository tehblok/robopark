import Foundation
import CryptoKit

public struct DeviceSessionChallenge: Decodable {
    public let challenge_id: String
    public let nonce: String
    public let expires_at: String
    public let instance_id: String
    public let environment: String

    public func signature(for registration: Registration, now: Date = Date()) throws -> String {
        let formatter = ISO8601DateFormatter()
        formatter.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        guard instance_id == registration.instanceID, environment == registration.environment,
              UUID(uuidString: challenge_id) != nil, UUID(uuidString: registration.deviceID) != nil,
              nonce.range(of: "^[A-Za-z0-9_-]{43}$", options: .regularExpression) != nil,
              expires_at.range(of: "^\\d{4}-\\d{2}-\\d{2}T\\d{2}:\\d{2}:\\d{2}\\.\\d{6}Z$", options: .regularExpression) != nil,
              let expiry = formatter.date(from: expires_at), expiry > now,
              expiry.timeIntervalSince(now) <= 90 else { throw AgentError.invalidJob }
        let message = ["robopark-device-auth-v1", instance_id, environment, registration.deviceID,
                       challenge_id, nonce, expires_at].joined(separator: "\n")
        let key = try P256.Signing.PrivateKey(rawRepresentation: registration.privateKey)
        return try key.signature(for: Data(message.utf8)).derRepresentation.base64EncodedString()
    }
}
