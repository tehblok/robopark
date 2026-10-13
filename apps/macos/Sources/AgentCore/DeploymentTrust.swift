import Foundation
import CryptoKit

public struct DeploymentProfile: Codable {
    public let purpose: String
    public let origin: String
    public let instance_id: String
    public let environment: String
    public let channel: String
    public let expires: Int64
}

public enum DeploymentTrust {
    public static func verify(_ envelope: SignedCatalog, publicKey: Data, origin: ServerOrigin,
                              instanceID: String, environment: String, now: Int64) throws -> DeploymentProfile {
        guard envelope.payload.utf8.count <= 16 * 1024,
              let bytes = Data(base64Encoded: envelope.payload),
              let signature = Data(base64Encoded: envelope.signature),
              let key = try? Curve25519.Signing.PublicKey(rawRepresentation: publicKey),
              key.isValidSignature(signature, for: bytes),
              let profile = try? JSONDecoder().decode(DeploymentProfile.self, from: bytes),
              profile.purpose == "robopark-deployment-v1",
              try ServerOrigin(profile.origin) == origin,
              profile.instance_id == instanceID, UUID(uuidString: instanceID) != nil,
              instanceID != "00000000-0000-0000-0000-000000000000",
              profile.environment == environment, ["test", "production"].contains(environment),
              profile.expires > now,
              profile.channel.range(of: "^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$", options: .regularExpression) != nil
        else { throw AgentError.wrongServer }
        return profile
    }
}
