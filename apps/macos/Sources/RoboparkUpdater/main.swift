import Foundation
import CryptoKit
import AgentCore
import Darwin

private let support = URL(fileURLWithPath: "/Library/Application Support/RoboparkUpdater", isDirectory: true)
private let appInfo = URL(fileURLWithPath: "/Applications/Robopark.app/Contents/Info.plist")
private let retirement = support.appendingPathComponent("retiring")
private let lifecycleLock = support.appendingPathComponent("lifecycle.lock", isDirectory: true)
private let installLock = support.appendingPathComponent("install.lock", isDirectory: true)
private let updaterPlist = URL(fileURLWithPath: "/Library/LaunchDaemons/ru.robopark.update-helper.plist")
private let updaterService = "system/ru.robopark.update-helper"
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

private struct LifecycleState: Codable {
    let helper_pid: Int32
    let installer_pid: Int32?
    let installer_started_at: Int64?
}

private final class RedirectDeny: NSObject, URLSessionTaskDelegate {
    func urlSession(_ session: URLSession, task: URLSessionTask,
                    willPerformHTTPRedirection response: HTTPURLResponse,
                    newRequest request: URLRequest,
                    completionHandler: @escaping (URLRequest?) -> Void) {
        completionHandler(nil)
    }
}

private func requireSecureDirectory(_ url: URL, applicationsParent: Bool = false) throws {
    let values = try url.resourceValues(forKeys: [.isDirectoryKey, .isSymbolicLinkKey])
    let attributes = try FileManager.default.attributesOfItem(atPath: url.path)
    let owner = (attributes[.ownerAccountID] as? NSNumber)?.uint32Value ?? UInt32.max
    let group = (attributes[.groupOwnerAccountID] as? NSNumber)?.uint32Value ?? UInt32.max
    let permissions = (attributes[.posixPermissions] as? NSNumber)?.intValue ?? 0
    guard values.isDirectory == true, values.isSymbolicLink != true,
          applicationsParent
            ? ApplicationInstallPathPolicy.allowsApplicationsDirectory(owner: owner, group: group, permissions: permissions)
            : owner == 0 && permissions & 0o022 == 0 else {
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

private func isRetiring() -> Bool {
    FileManager.default.fileExists(atPath: retirement.path)
}

private var lifecycleStateURL: URL { lifecycleLock.appendingPathComponent("state.json") }

private func processPath(_ pid: Int32) -> String? {
    guard pid > 1 else { return nil }
    var buffer = [CChar](repeating: 0, count: 4096)
    let length = proc_pidpath(pid, &buffer, UInt32(buffer.count))
    guard length > 0 else { return nil }
    return String(cString: buffer)
}

private func readLifecycleState() throws -> LifecycleState {
    try requireSecureDirectory(lifecycleLock)
    let values = try lifecycleStateURL.resourceValues(forKeys: [.isRegularFileKey, .isSymbolicLinkKey, .fileSizeKey])
    let attributes = try FileManager.default.attributesOfItem(atPath: lifecycleStateURL.path)
    guard values.isRegularFile == true, values.isSymbolicLink != true,
          let size = values.fileSize, size > 0, size <= 4096,
          (attributes[.ownerAccountID] as? NSNumber)?.intValue == 0,
          ((attributes[.posixPermissions] as? NSNumber)?.intValue ?? 0) & 0o077 == 0 else {
        throw AgentError.invalidCatalog
    }
    return try JSONDecoder().decode(LifecycleState.self, from: Data(contentsOf: lifecycleStateURL))
}

private func writeLifecycleState(installerPID: Int32? = nil, installerStartedAt: Int64? = nil) throws {
    let bytes = try JSONEncoder().encode(LifecycleState(helper_pid: getpid(), installer_pid: installerPID,
                                                        installer_started_at: installerStartedAt))
    try bytes.write(to: lifecycleStateURL, options: [.atomic])
    try FileManager.default.setAttributes([.posixPermissions: 0o600, .ownerAccountID: 0, .groupOwnerAccountID: 0],
                                          ofItemAtPath: lifecycleStateURL.path)
}

private func anyInstallerRunning() -> Bool {
    var pids = [pid_t](repeating: 0, count: 4096)
    let listed = proc_listallpids(&pids, Int32(pids.count * MemoryLayout<pid_t>.size))
    guard listed > 0 else { return true }
    let count = min(Int(listed), pids.count)
    return pids.prefix(count).contains { processPath($0) == "/usr/sbin/installer" }
}

private func prepareLifecycleLock() throws {
    if FileManager.default.fileExists(atPath: lifecycleLock.path) {
        try requireSecureDirectory(lifecycleLock)
        let children = try FileManager.default.contentsOfDirectory(atPath: lifecycleLock.path)
        guard children.isEmpty || children == ["state.json"] else { throw AgentError.invalidCatalog }
        if children == ["state.json"] {
            let stale = try readLifecycleState()
            if let installer = stale.installer_pid, installer > 1,
               processPath(installer) == "/usr/sbin/installer" {
                throw AgentError.invalidCatalog
            }
            let now = Int64(Date().timeIntervalSince1970)
            if stale.installer_pid == -1,
               now - (stale.installer_started_at ?? now) < 600 {
                throw AgentError.invalidCatalog
            }
            if anyInstallerRunning() {
                throw AgentError.invalidCatalog
            }
            try FileManager.default.removeItem(at: lifecycleStateURL)
        }
        // The kernel update lock is already held, so no earlier helper remains.
        // A recorded installer is the only child allowed to outlive it.
        guard rmdir(lifecycleLock.path) == 0 else { throw AgentError.invalidCatalog }
    }
    try FileManager.default.createDirectory(at: lifecycleLock, withIntermediateDirectories: false,
                                            attributes: [.posixPermissions: 0o700, .ownerAccountID: 0, .groupOwnerAccountID: 0])
    try writeLifecycleState()
}

private func removeLifecycleLock() {
    try? FileManager.default.removeItem(at: lifecycleStateURL)
    _ = rmdir(lifecycleLock.path)
}

private func prepareInstallLock(package: URL, startedAt: Int64) throws {
    guard package.standardizedFileURL == support.appendingPathComponent("verified-update.pkg").standardizedFileURL else {
        throw AgentError.invalidCatalog
    }
    if FileManager.default.fileExists(atPath: installLock.path) {
        try requireSecureDirectory(installLock)
        let state = installLock.appendingPathComponent("state")
        let values = try state.resourceValues(forKeys: [.isRegularFileKey, .isSymbolicLinkKey, .fileSizeKey,
                                                        .contentModificationDateKey])
        let attributes = try FileManager.default.attributesOfItem(atPath: state.path)
        guard values.isRegularFile == true, values.isSymbolicLink != true,
              let size = values.fileSize, (1...4096).contains(size),
              (attributes[.ownerAccountID] as? NSNumber)?.intValue == 0,
              ((attributes[.posixPermissions] as? NSNumber)?.intValue ?? 0) & 0o077 == 0,
              Date().timeIntervalSince(values.contentModificationDate ?? Date()) >= 600,
              !anyInstallerRunning(),
              let text = String(data: try Data(contentsOf: state), encoding: .utf8),
              text.split(separator: "\n").contains(where: { $0 == "mode=auto" || $0 == "mode=package" }) else {
            throw AgentError.invalidCatalog
        }
        try FileManager.default.removeItem(at: state)
        guard rmdir(installLock.path) == 0 else { throw AgentError.invalidCatalog }
    }
    try FileManager.default.createDirectory(at: installLock, withIntermediateDirectories: false,
                                            attributes: [.posixPermissions: 0o700, .ownerAccountID: 0, .groupOwnerAccountID: 0])
    let state = installLock.appendingPathComponent("state")
    let value = "mode=auto\nhelper_pid=\(getpid())\nstarted_at=\(startedAt)\n"
    do {
        try Data(value.utf8).write(to: state, options: [.atomic])
        try FileManager.default.setAttributes([.posixPermissions: 0o600, .ownerAccountID: 0, .groupOwnerAccountID: 0],
                                              ofItemAtPath: state.path)
    } catch {
        try? FileManager.default.removeItem(at: state)
        _ = rmdir(installLock.path)
        throw error
    }
}

private func installedVersion() throws -> String {
    try requireSecureDirectory(URL(fileURLWithPath: "/Applications", isDirectory: true), applicationsParent: true)
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
    let startedAt = Int64(Date().timeIntervalSince1970)
    try prepareInstallLock(package: package, startedAt: startedAt)
    try writeLifecycleState(installerPID: -1, installerStartedAt: startedAt)
    try process.run()
    do { try writeLifecycleState(installerPID: process.processIdentifier, installerStartedAt: startedAt) }
    catch {
        process.terminate(); process.waitUntilExit(); throw error
    }
    process.waitUntilExit()
    guard process.terminationReason == .exit, process.terminationStatus == 0 else { throw AgentError.unsupportedRelease }
}

private struct LaunchctlResult {
    let status: Int32
    let output: String
}

private func launchctl(_ arguments: [String], captureOutput: Bool = false) throws -> LaunchctlResult {
    let process = Process()
    process.executableURL = URL(fileURLWithPath: "/bin/launchctl")
    process.arguments = arguments
    let output = Pipe()
    process.standardOutput = captureOutput ? output : FileHandle.nullDevice
    process.standardError = FileHandle.nullDevice
    try process.run()
    let bytes = captureOutput ? output.fileHandleForReading.readDataToEndOfFile() : Data()
    process.waitUntilExit()
    guard process.terminationReason == .exit else { throw AgentError.invalidCatalog }
    return LaunchctlResult(status: process.terminationStatus,
                           output: String(data: bytes, encoding: .utf8) ?? "")
}

private func pathExistsNoFollow(_ url: URL) throws -> Bool {
    var status = stat()
    if lstat(url.path, &status) == 0 { return true }
    guard errno == ENOENT else { throw AgentError.invalidCatalog }
    return false
}

private func registrationIsBusy() throws -> Bool {
    if anyInstallerRunning() { return true }
    for lock in [installLock, lifecycleLock] {
        if try pathExistsNoFollow(lock) {
            try requireSecureDirectory(lock)
            return true
        }
    }
    return false
}

private func requireSecureUpdaterPlist() throws {
    let values = try updaterPlist.resourceValues(forKeys: [.isRegularFileKey, .isSymbolicLinkKey, .fileSizeKey])
    let attributes = try FileManager.default.attributesOfItem(atPath: updaterPlist.path)
    guard values.isRegularFile == true, values.isSymbolicLink != true,
          let size = values.fileSize, (1...64 * 1024).contains(size),
          (attributes[.ownerAccountID] as? NSNumber)?.intValue == 0,
          ((attributes[.posixPermissions] as? NSNumber)?.intValue ?? 0) & 0o022 == 0 else {
        throw AgentError.invalidCatalog
    }
}

private func writeRegistrationStatus(_ status: String) throws {
    try requireSecureSupportPath()
    let url = support.appendingPathComponent("registration.status")
    let data = try JSONSerialization.data(withJSONObject: [
        "status": status,
        "checked_at": Int64(Date().timeIntervalSince1970),
    ], options: [.sortedKeys])
    try data.write(to: url, options: [.atomic])
    try FileManager.default.setAttributes(
        [.posixPermissions: 0o600, .ownerAccountID: 0, .groupOwnerAccountID: 0],
        ofItemAtPath: url.path
    )
}

private func registerUpdater() -> Bool {
    do {
        let outcome = try UpdaterRegistrationCoordinator.run(
            maxAttempts: 600,
            isRetiring: isRetiring,
            acquireLock: { try storage.tryAcquireLock() },
            releaseLock: { try $0.close() },
            isBusy: registrationIsBusy,
            isDisabled: {
                try requireSecureUpdaterPlist()
                let result = try launchctl(["print-disabled", "system"], captureOutput: true)
                guard result.status == 0 else { throw AgentError.invalidCatalog }
                return UpdaterRegistrationPolicy.isDisabled(
                    result.output, label: "ru.robopark.update-helper"
                )
            },
            isLoaded: { try launchctl(["print", updaterService]).status == 0 },
            bootout: {
                guard try launchctl(["bootout", updaterService]).status == 0 else {
                    throw AgentError.invalidCatalog
                }
            },
            bootstrap: {
                guard try launchctl(["bootstrap", "system", updaterPlist.path]).status == 0 else {
                    throw AgentError.invalidCatalog
                }
            },
            pause: { sleep(1) }
        )
        try writeRegistrationStatus(outcome == .ready ? "ready" : "retired")
        return true
    } catch {
        try? writeRegistrationStatus("failed")
        return false
    }
}

@main private struct RoboparkUpdater {
    static func main() async {
        guard getuid() == 0 else { return }
        let arguments = Array(CommandLine.arguments.dropFirst())
        if arguments == ["--register-updater"] {
            Darwin.exit(registerUpdater() ? 0 : 75)
        }
        guard arguments.isEmpty else { Darwin.exit(64) }
        let lock: FileHandle
        do { lock = try storage.acquireLock() }
        catch { return }
        defer { try? lock.close() }
        guard !isRetiring() else { return }
        do { try prepareLifecycleLock() }
        catch { return }
        defer { removeLifecycleLock() }
        guard !isRetiring() else { return }
        let loaded: HelperState
        do { loaded = try loadState() }
        catch { return }
        var state = loaded
        do {
            let app = URL(fileURLWithPath: "/Applications/Robopark.app", isDirectory: true)
            let appValues = try? app.resourceValues(forKeys: [.isDirectoryKey, .isSymbolicLinkKey])
            guard appValues?.isDirectory == true, appValues?.isSymbolicLink != true else {
                state.status = "removed"
                state.message = "Robopark удалён; автоматическая переустановка отключена."
                state.installed_version = ""
                state.checked_at = Int64(Date().timeIntervalSince1970)
                try? writeState(state)
                return
            }
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
            guard !isRetiring() else { return }
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
            guard !isRetiring() else { return }
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
