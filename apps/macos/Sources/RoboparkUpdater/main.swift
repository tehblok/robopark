import Foundation
import CryptoKit
import AgentCore

private let support = URL(fileURLWithPath: "/Library/Application Support/RoboparkUpdater", isDirectory: true)
private let appInfo = URL(fileURLWithPath: "/Applications/Robopark.app/Contents/Info.plist")
private let storage = SecureUpdateStorage(root: support, expectedOwner: 0, trustedAncestors: [
    URL(fileURLWithPath: "/Library", isDirectory: true),
    URL(fileURLWithPath: "/Library/Application Support", isDirectory: true),
])

private struct FixedConfig: Codable {
    let schema: Int
    let origin: String
    let instance_id: String
    let environment: String
    let channel: String
}

private struct HelperState: Codable {
    var minimum_generation: Int64 = 0
    var status: String = "idle"
    var message: String = "Обновления ещё не проверялись"
    var installed_version: String = ""
    var target_version: String = ""
    var checked_at: Int64 = 0
}

private final class RedirectDeny: NSObject, URLSessionTaskDelegate {
    func urlSession(_ session: URLSession, task: URLSessionTask,
                    willPerformHTTPRedirection response: HTTPURLResponse,
                    newRequest request: URLRequest,
                    completionHandler: @escaping (URLRequest?) -> Void) {
        completionHandler(nil)
    }
}

private func requireSecureDirectory(_ url: URL) throws {
    let values = try url.resourceValues(forKeys: [.isDirectoryKey, .isSymbolicLinkKey])
    let attributes = try FileManager.default.attributesOfItem(atPath: url.path)
    guard values.isDirectory == true, values.isSymbolicLink != true,
          (attributes[.ownerAccountID] as? NSNumber)?.intValue == 0,
          ((attributes[.posixPermissions] as? NSNumber)?.intValue ?? 0) & 0o022 == 0 else {
        throw AgentError.invalidCatalog
    }
}

private func requireSecureSupportPath() throws {
    try storage.validate()
}

private func regularRootFile(_ name: String, maximum: Int) throws -> Data {
    try requireSecureSupportPath()
    let url = support.appendingPathComponent(name)
    let values = try url.resourceValues(forKeys: [.isRegularFileKey, .isSymbolicLinkKey, .fileSizeKey])
    guard values.isRegularFile == true, values.isSymbolicLink != true,
          let size = values.fileSize, size > 0, size <= maximum else { throw AgentError.invalidCatalog }
    let attributes = try FileManager.default.attributesOfItem(atPath: url.path)
    guard (attributes[.ownerAccountID] as? NSNumber)?.intValue == 0,
          ((attributes[.posixPermissions] as? NSNumber)?.intValue ?? 0) & 0o022 == 0 else { throw AgentError.invalidCatalog }
    let data = try Data(contentsOf: url, options: [.mappedIfSafe])
    guard data.count == size else { throw AgentError.invalidCatalog }
    return data
}

private func updateRoot() throws -> Data {
    let object = try JSONSerialization.jsonObject(with: regularRootFile("update-root.json", maximum: 16 * 1024)) as? [String: String]
    guard let encoded = object?["ed25519"], object?.count == 1,
          let key = Data(base64Encoded: encoded), key.count == 32 else { throw AgentError.invalidCatalog }
    return key
}

private func loadState() throws -> HelperState {
    guard let data = try storage.loadState() else { return HelperState() }
    return try JSONDecoder().decode(HelperState.self, from: data)
}

private func writeState(_ state: HelperState) throws {
    let data = try JSONEncoder().encode(state)
    try storage.writeState(data)
    let publicURL = support.appendingPathComponent("status.json")
    let publicStatus: [String: Any] = [
        "status": state.status, "message": state.message,
        "installed_version": state.installed_version, "target_version": state.target_version,
        "checked_at": state.checked_at,
    ]
    let publicData = try JSONSerialization.data(withJSONObject: publicStatus, options: [.sortedKeys])
    try publicData.write(to: publicURL, options: [.atomic])
    try FileManager.default.setAttributes([.posixPermissions: 0o644, .ownerAccountID: 0, .groupOwnerAccountID: 0], ofItemAtPath: publicURL.path)
}

private func installedVersion() throws -> String {
    try requireSecureDirectory(URL(fileURLWithPath: "/Applications", isDirectory: true))
    try requireSecureDirectory(URL(fileURLWithPath: "/Applications/Robopark.app", isDirectory: true))
    try requireSecureDirectory(URL(fileURLWithPath: "/Applications/Robopark.app/Contents", isDirectory: true))
    let values = try appInfo.resourceValues(forKeys: [.isRegularFileKey, .isSymbolicLinkKey, .fileSizeKey])
    let attributes = try FileManager.default.attributesOfItem(atPath: appInfo.path)
    guard values.isRegularFile == true, values.isSymbolicLink != true,
          let size = values.fileSize, size > 0, size <= 64 * 1024,
          (attributes[.ownerAccountID] as? NSNumber)?.intValue == 0,
          ((attributes[.posixPermissions] as? NSNumber)?.intValue ?? 0) & 0o022 == 0,
          let info = NSDictionary(contentsOf: appInfo), let version = info["RoboparkReleaseVersion"] as? String else {
        throw AgentError.unsupportedRelease
    }
    _ = try ReleaseVersion(version)
    return version
}

private func boundedGET(_ url: URL, origin: ServerOrigin, limit: Int, timeout: Double) async throws -> (Data, HTTPURLResponse) {
    guard url.scheme == "https", url.host?.lowercased() == origin.url.host?.lowercased(),
          url.port == origin.url.port, limit > 0, limit <= 512 * 1024 * 1024 else { throw AgentError.invalidCatalog }
    var request = URLRequest(url: url, cachePolicy: .reloadIgnoringLocalCacheData, timeoutInterval: timeout)
    request.setValue("application/json", forHTTPHeaderField: "Accept")
    request.setValue("RoboparkUpdateHelper/1", forHTTPHeaderField: "User-Agent")
    let configuration = URLSessionConfiguration.ephemeral
    configuration.httpCookieStorage = nil
    configuration.urlCache = nil
    configuration.timeoutIntervalForResource = timeout
    configuration.httpMaximumConnectionsPerHost = 1
    let session = URLSession(configuration: configuration, delegate: RedirectDeny(), delegateQueue: nil)
    defer { session.invalidateAndCancel() }
    let (stream, rawResponse) = try await session.bytes(for: request)
    guard let response = rawResponse as? HTTPURLResponse,
          response.expectedContentLength <= Int64(limit) else { throw AgentError.invalidCatalog }
    var data = Data()
    data.reserveCapacity(min(limit, max(0, Int(response.expectedContentLength))))
    for try await byte in stream {
        guard data.count < limit else { throw AgentError.invalidCatalog }
        data.append(byte)
    }
    return (data, response)
}

private func runInstaller(_ package: URL) throws {
    let command = try VerifiedInstaller.command(package: package, supportRoot: support)
    let process = Process()
    process.executableURL = URL(fileURLWithPath: command.executable)
    process.arguments = command.arguments
    process.standardOutput = FileHandle.nullDevice
    process.standardError = FileHandle.nullDevice
    try process.run()
    process.waitUntilExit()
    guard process.terminationReason == .exit, process.terminationStatus == 0 else { throw AgentError.unsupportedRelease }
}

@main private struct RoboparkUpdater {
    static func main() async {
        guard getuid() == 0 else { return }
        let lock: FileHandle
        do { lock = try storage.acquireLock() }
        catch { return }
        defer { try? lock.close() }
        let loaded: HelperState
        do { loaded = try loadState() }
        catch { return }
        var state = loaded
        do {
            let config = try JSONDecoder().decode(FixedConfig.self, from: regularRootFile("config.json", maximum: 16 * 1024))
            guard config.schema == 1, config.origin == TestDeploymentAssignment.origin,
                  config.environment == TestDeploymentAssignment.environment,
                  config.instance_id == TestDeploymentAssignment.instanceID,
                  config.channel == "rc" else { throw AgentError.wrongServer }
            let origin = try ServerOrigin(config.origin)
            let key = try updateRoot()
            let (infoBytes, infoResponse) = try await boundedGET(origin.api("agent-devices/info"), origin: origin, limit: 128 * 1024, timeout: 20)
            guard infoResponse.statusCode == 200 else { throw AgentError.wrongServer }
            let info = try JSONDecoder().decode(ServerInfo.self, from: infoBytes)
            guard info.protocolVersion == 1, info.instance_id == config.instance_id,
                  info.environment == config.environment, let signed = info.signed_profile else { throw AgentError.wrongServer }
            let assignment = try DeploymentTrust.verify(signed, publicKey: key, origin: origin,
                instanceID: config.instance_id, environment: config.environment,
                now: Int64(Date().timeIntervalSince1970))
            guard assignment.channel == config.channel else { throw AgentError.wrongServer }
            let (catalog, catalogResponse) = try await boundedGET(origin.api("distribution/v1/macos/catalog.json"), origin: origin, limit: 256 * 1024, timeout: 30)
            guard catalogResponse.statusCode == 200 else { throw AgentError.invalidCatalog }
            let verified = try UpdateVerifier(publicKey: key).verify(catalog,
                now: Int64(Date().timeIntervalSince1970), minimumGeneration: state.minimum_generation,
                channel: config.channel)
            let current = try installedVersion()
            state.minimum_generation = max(state.minimum_generation, verified.generation)
            state.installed_version = current
            state.target_version = verified.target.version
            state.checked_at = Int64(Date().timeIntervalSince1970)
            if try ReleaseVersion(current) >= ReleaseVersion(verified.target.version) {
                state.status = "current"; state.message = "Установлена актуальная версия"
                try writeState(state); return
            }
            state.status = "downloading"
            state.message = "Загружается проверенное обновление \(verified.target.version)"
            try writeState(state)
            let packageURL = try verified.target.fixedArtifactURL(origin: origin)
            let (package, packageResponse) = try await boundedGET(packageURL, origin: origin,
                limit: verified.target.length, timeout: 300)
            guard packageResponse.statusCode == 200 else { throw AgentError.invalidCatalog }
            try UpdateVerifier.checkArtifact(package, target: verified.target)
            let staged = support.appendingPathComponent("verified-update.pkg")
            try storage.stagePackage(package)
            defer { try? FileManager.default.removeItem(at: staged) }
            state.status = "installing"; state.message = "Устанавливается \(verified.target.version)"
            try writeState(state)
            try runInstaller(staged)
            let installed = try installedVersion()
            guard installed == verified.target.version else { throw AgentError.unsupportedRelease }
            state.status = "installed"; state.message = "Установлена версия \(installed)"
            state.installed_version = installed
            try writeState(state)
        } catch {
            state.status = "failed"
            state.message = "Автообновление не установлено: проверка сервера, подписи или пакета не пройдена."
            state.checked_at = Int64(Date().timeIntervalSince1970)
            try? writeState(state)
        }
    }
}
