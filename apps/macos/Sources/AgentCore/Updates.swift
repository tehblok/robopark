import Foundation
import CryptoKit

public struct ReleaseTarget: Codable {
    public let version: String
    public let url: String
    public let sha256: String
    public let length: Int
    public let architecture: String
    public let protocolVersion: Int
    public let notes: String
    enum CodingKeys: String, CodingKey { case version, url, sha256, length, architecture, notes; case protocolVersion = "protocol" }
    public func artifactURL(origin: ServerOrigin) throws -> URL {
        if url.hasPrefix("/") {
            guard url.range(of: "^/api/distribution/v1/macos/Robopark-[A-Za-z0-9._+-]+-arm64\\.pkg$", options: .regularExpression) != nil else { throw AgentError.invalidCatalog }
            return URL(string: url, relativeTo: origin.url)!.absoluteURL
        }
        guard let parts = URLComponents(string: url), parts.scheme == "https", parts.host != nil,
              parts.user == nil, parts.password == nil, parts.fragment == nil, let result = parts.url else { throw AgentError.invalidCatalog }
        return result
    }
}

public struct UpdateCatalog: Codable {
    public let schema: Int
    public let generation: Int64
    public let expires: Int64
    public let channels: [String: ReleaseTarget]
}

public struct SignedCatalog: Codable {
    public let payload: String
    public let signature: String
}

public struct VerifiedUpdate {
    public let generation: Int64
    public let target: ReleaseTarget
}

public struct UpdateVerifier {
    public let publicKey: Data
    public init(publicKey: Data) { self.publicKey = publicKey }
    public func verify(_ data: Data, now: Int64, minimumGeneration: Int64, channel: String) throws -> VerifiedUpdate {
        guard data.count <= 256 * 1024,
              let envelope = try? JSONDecoder().decode(SignedCatalog.self, from: data),
              let payload = Data(base64Encoded: envelope.payload),
              let signature = Data(base64Encoded: envelope.signature),
              let key = try? Curve25519.Signing.PublicKey(rawRepresentation: publicKey),
              key.isValidSignature(signature, for: payload),
              let catalog = try? JSONDecoder().decode(UpdateCatalog.self, from: payload),
              catalog.schema == 1, catalog.generation > 0 else { throw AgentError.invalidCatalog }
        guard catalog.expires > now else { throw AgentError.expiredCatalog }
        guard catalog.generation >= minimumGeneration else { throw AgentError.rollback }
        guard let target = catalog.channels[channel] else { throw AgentError.missingChannel }
        _ = try target.artifactURL(origin: ServerOrigin("https://distribution.invalid"))
        guard target.architecture == "arm64", target.protocolVersion == 1,
              target.length > 0, target.length <= 512 * 1024 * 1024,
              target.sha256.range(of: "^[a-f0-9]{64}$", options: .regularExpression) != nil,
              !target.version.isEmpty, target.version.count <= 150 else { throw AgentError.unsupportedRelease }
        return VerifiedUpdate(generation: catalog.generation, target: target)
    }
    public static func checkArtifact(_ data: Data, target: ReleaseTarget) throws {
        guard data.count == target.length,
              SHA256.hash(data: data).map({ String(format: "%02x", $0) }).joined() == target.sha256 else { throw AgentError.invalidCatalog }
    }
}
