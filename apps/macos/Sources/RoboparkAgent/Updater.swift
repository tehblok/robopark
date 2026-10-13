import Foundation
import AppKit
import AgentCore

@MainActor final class Updater {
    let bridge: Bridge
    private(set) var message = "Обновления ещё не проверялись"
    private(set) var available: VerifiedUpdate?
    private(set) var busy = false
    var onOffer: ((ReleaseTarget) -> Void)?
    var onChange: (() -> Void)?
    init(bridge: Bridge) { self.bridge = bridge }
    var version: String { Bundle.main.object(forInfoDictionaryKey: "RoboparkReleaseVersion") as? String ?? "development" }
    func check(manual: Bool = false) async {
        guard !busy, let profile = bridge.registration,
              let url = URL(string: profile.catalogURL), url.scheme == "https" else { return }
        busy = true
        defer { busy = false; onChange?() }
        do {
            let (info, assignment) = try await bridge.verifiedInfo(ServerOrigin(profile.origin))
            guard info.instance_id == profile.instanceID, info.environment == profile.environment,
                  bridge.registration?.deviceID == profile.deviceID else { throw AgentError.wrongServer }
            bridge.registration?.signedProfile = info.signed_profile
            bridge.registration?.channel = assignment.channel
            try bridge.persist()
            let key = try EmbeddedTrust.publicKey()
            let (bytes, response) = try await Network.request(url, limit: 256 * 1024)
            guard response.statusCode == 200 else { throw NetworkFailure.network }
            let update = try UpdateVerifier(publicKey: key).verify(bytes, now: Int64(Date().timeIntervalSince1970),
                                     minimumGeneration: profile.minimumGeneration, channel: assignment.channel)
            bridge.registration?.minimumGeneration = update.generation
            try bridge.persist()
            if update.target.version == version { available = nil; message = "Установлена актуальная версия"; return }
            guard try ReleaseVersion(version) < ReleaseVersion(update.target.version) else { throw AgentError.rollback }
            available = update
            message = "Доступна версия \(update.target.version)"
            if manual || profile.deferredVersion != update.target.version { onOffer?(update.target) }
        } catch {
            available = nil
            message = "Проверка обновлений недоступна или не прошла проверку подписи. Установленная версия сохранена."
        }
    }
    func reset() { available = nil; message = "Обновления ещё не проверялись"; onChange?() }
    func deferUpdate() {
        bridge.registration?.deferredVersion = available?.target.version ?? ""
        try? bridge.persist()
    }
    func install() async {
        guard let offered = available, !busy, let profile = bridge.registration,
              let catalogURL = URL(string: profile.catalogURL), let origin = try? ServerOrigin(profile.origin) else { return }
        busy = true; message = "Загрузка и проверка пакета…"; onChange?()
        defer { busy = false; onChange?() }
        do {
            let assignment = try EmbeddedTrust.verify(profile.signedProfile, origin: origin,
                                                      instanceID: profile.instanceID, environment: profile.environment)
            let (catalog, catalogResponse) = try await Network.request(catalogURL, limit: 256 * 1024)
            guard catalogResponse.statusCode == 200 else { throw NetworkFailure.network }
            let fresh = try UpdateVerifier(publicKey: EmbeddedTrust.publicKey()).verify(catalog,
                now: Int64(Date().timeIntervalSince1970), minimumGeneration: profile.minimumGeneration, channel: assignment.channel)
            let release = fresh.target
            guard fresh.generation == offered.generation, release.sha256 == offered.target.sha256,
                  release.version == offered.target.version, try ReleaseVersion(version) < ReleaseVersion(release.version),
                  bridge.registration?.deviceID == profile.deviceID else { throw AgentError.rollback }
            let url = try release.artifactURL(origin: origin)
            // Bounded before allocation. The catalog is authenticated by a root in the installed app.
            let (data, response) = try await Network.request(url, limit: release.length, timeout: 120)
            guard response.statusCode == 200 else { throw NetworkFailure.network }
            try UpdateVerifier.checkArtifact(data, target: release)
            let directory = FileManager.default.temporaryDirectory.appendingPathComponent("robopark-update-" + UUID().uuidString)
            try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: false,
                                                   attributes: [.posixPermissions: 0o700])
            let target = directory.appendingPathComponent("Robopark.pkg")
            try data.write(to: target, options: .atomic)
            try FileManager.default.setAttributes([.posixPermissions: 0o600], ofItemAtPath: target.path)
            guard NSWorkspace.shared.open(target) else { throw NetworkFailure.invalid }
            message = "Пакет проверен. Завершите установку в Installer macOS."
        } catch { available = nil; message = "Обновление не установлено: загрузка или проверка пакета не завершилась." }
    }
}
