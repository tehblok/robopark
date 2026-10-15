import Foundation
import AgentCore

@MainActor final class Updater {
    private struct PublicStatus: Codable {
        let status: String
        let message: String
        let installed_version: String
        let target_version: String
        let checked_at: Int64
    }

    let bridge: Bridge
    private(set) var message = "Автообновление ещё не проверялось"
    private(set) var busy = false
    private(set) var catalogTarget = ""
    private var lastCheckFailed = false
    var onChange: (() -> Void)?

    init(bridge: Bridge) { self.bridge = bridge }
    var version: String { Bundle.main.object(forInfoDictionaryKey: "RoboparkReleaseVersion") as? String ?? "development" }
    var statusState: String {
        if busy { return "checking" }
        if lastCheckFailed { return "error" }
        if message.contains("ещё не проверялось") || message.contains("Ожидается первая") { return "checking" }
        if message.hasPrefix("Доступна подписанная версия") { return "available" }
        if message.contains("недоступна") || message.contains("не может понизить") { return "error" }
        return "current"
    }
    var availableVersion: String? { statusState == "available" && !catalogTarget.isEmpty ? catalogTarget : nil }

    func check(manual: Bool = false) async {
        guard !busy else { return }
        busy = true
        defer { busy = false; onChange?() }
        refreshStatus(notify: false)
        do {
            let origin = try ServerOrigin("https://roboparktest.ru.tuna.am")
            let (info, assignment) = try await bridge.verifiedInfo(origin)
            guard info.environment == "test" else { throw AgentError.wrongServer }
            let catalogURL = origin.api("distribution/v1/macos/catalog.json")
            let (bytes, response) = try await Network.request(catalogURL, limit: 256 * 1024)
            guard response.statusCode == 200 else { throw NetworkFailure.network }
            let minimum = bridge.registration?.minimumGeneration ?? 0
            let verified = try UpdateVerifier(publicKey: EmbeddedTrust.publicKey()).verify(bytes,
                now: Int64(Date().timeIntervalSince1970), minimumGeneration: minimum,
                channel: assignment.channel)
            lastCheckFailed = false
            catalogTarget = verified.target.version
            if let registration = bridge.registration,
               registration.origin == origin.url.absoluteString,
               registration.instanceID == info.instance_id {
                bridge.registration?.signedProfile = info.signed_profile
                bridge.registration?.channel = assignment.channel
                bridge.registration?.minimumGeneration = max(minimum, verified.generation)
                try bridge.persist()
            }
            if try ReleaseVersion(version) < ReleaseVersion(verified.target.version) {
                message = "Доступна подписанная версия \(verified.target.version). Системная служба установит её автоматически в течение 15 минут."
            } else if try ReleaseVersion(version) == ReleaseVersion(verified.target.version) {
                message = "Установлена актуальная версия"
            } else {
                message = "Каталог не может понизить установленную версию"
            }
        } catch {
            lastCheckFailed = true
            message = "Проверка тестового сервера недоступна. Автообновление повторит попытку без изменения установленной версии."
        }
    }

    func refreshStatus() { refreshStatus(notify: true) }

    private func refreshStatus(notify: Bool) {
        let url = URL(fileURLWithPath: "/Library/Application Support/RoboparkUpdater/status.json")
        do {
            let values = try url.resourceValues(forKeys: [.isRegularFileKey, .isSymbolicLinkKey, .fileSizeKey])
            guard values.isRegularFile == true, values.isSymbolicLink != true,
                  let size = values.fileSize, size > 0, size <= 64 * 1024 else { throw AgentError.invalidCatalog }
            let status = try JSONDecoder().decode(PublicStatus.self, from: Data(contentsOf: url))
            if status.installed_version != version, !status.installed_version.isEmpty {
                message = "Установлена версия \(status.installed_version). Перезапустите Robopark, сейчас запущена \(version)."
            } else if !catalogTarget.isEmpty, status.target_version != catalogTarget,
               status.installed_version != catalogTarget {
                message = "Доступна подписанная версия \(catalogTarget). Системная служба установит её автоматически в течение 15 минут."
            } else {
                message = status.message
            }
        } catch {
            message = "Ожидается первая автоматическая проверка обновлений."
        }
        if notify { onChange?() }
    }

    func reset() { message = "Автообновление ещё не проверялось"; lastCheckFailed = false; onChange?() }
}
