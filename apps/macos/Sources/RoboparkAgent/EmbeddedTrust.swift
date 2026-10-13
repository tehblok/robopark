import Foundation
import AgentCore

enum EmbeddedTrust {
    static func publicKey() throws -> Data {
        guard let url = Bundle.main.url(forResource: "update-root", withExtension: "json"),
              let root = try JSONSerialization.jsonObject(with: Data(contentsOf: url)) as? [String: String],
              let value = root["ed25519"], let key = Data(base64Encoded: value), key.count == 32
        else { throw AgentError.invalidCatalog }
        return key
    }
    static func verify(_ envelope: SignedCatalog?, origin: ServerOrigin, instanceID: String,
                       environment: String) throws -> DeploymentProfile {
        guard let envelope else { throw AgentError.wrongServer }
        return try DeploymentTrust.verify(envelope, publicKey: publicKey(), origin: origin,
                                          instanceID: instanceID, environment: environment,
                                          now: Int64(Date().timeIntervalSince1970))
    }
}
