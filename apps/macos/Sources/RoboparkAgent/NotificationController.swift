import Foundation
import CryptoKit
import Network
@preconcurrency import UserNotifications
import AgentCore

@MainActor final class NotificationController: NSObject, UNUserNotificationCenterDelegate {
    private enum InboxFailure: Error { case authorization, connectivity }
    private struct SeenEntry: Codable {
        let accountID: Int
        let eventID: String
        let seenAt: TimeInterval
    }

    private let center = UNUserNotificationCenter.current()
    private let pathMonitor = NWPathMonitor()
    private let pathQueue = DispatchQueue(label: "ru.robopark.notifications.network", qos: .utility)
    private let seenKey = "RoboparkNotificationSeenV1"
    private let openWorkspace: () -> Void
    private let openNotificationCenter: () -> Void
    private var loop: Task<Void, Never>?
    private var runID = UUID()
    private weak var session: WorkspaceSession?
    private var accountID: Int?
    private var role = ""
    private var inboxETag: String?
    private var notificationSessionID = UUID().uuidString.lowercased()
    private var maintenanceEpisode = NativeMaintenanceEpisodePolicy()
    private var suspended = false
    private var networkAvailable = false
    private var incident = NativeIncidentPolicy()
    private(set) var unreadCount = 0
    private(set) var permission = "Не настроены"
    var onChange: (() -> Void)?

    init(openWorkspace: @escaping () -> Void, openNotificationCenter: @escaping () -> Void) {
        self.openWorkspace = openWorkspace; self.openNotificationCenter = openNotificationCenter
        super.init()
        center.delegate = self
        let open = UNNotificationAction(identifier: "OPEN_NOTIFICATIONS", title: "Открыть уведомления")
        let workspace = UNNotificationAction(identifier: "OPEN_WORKSPACE", title: "Открыть Robopark")
        center.setNotificationCategories([UNNotificationCategory(identifier: "ROBOPARK_INBOX",
            actions: [open, workspace], intentIdentifiers: [], options: [])])
        pathMonitor.pathUpdateHandler = { [weak self] path in
            Task { @MainActor in self?.networkAvailable = path.status == .satisfied }
        }
        pathMonitor.start(queue: pathQueue)
        refreshPermission()
    }

    deinit { pathMonitor.cancel() }

    func start(accountID: Int, role: String, session: WorkspaceSession) {
        if self.accountID != accountID { stop(removeDelivered: self.accountID != nil) }
        if self.accountID == nil { notificationSessionID = UUID().uuidString.lowercased() }
        self.accountID = accountID; self.role = role; self.session = session; suspended = false
        unreadCount = UserDefaults.standard.integer(forKey: unreadKey(accountID))
        schedule()
    }

    func suspend() { suspended = true; runID = UUID(); loop?.cancel(); loop = nil }

    func resume() { suspended = false; schedule() }

    func stop(removeDelivered: Bool) {
        let previous = accountID
        let previousSession = notificationSessionID
        runID = UUID(); loop?.cancel(); loop = nil; session = nil; accountID = nil; role = ""; inboxETag = nil
        unreadCount = 0; incident = NativeIncidentPolicy(); maintenanceEpisode = NativeMaintenanceEpisodePolicy(); suspended = false
        if removeDelivered, let previous {
            UserDefaults.standard.removeObject(forKey: unreadKey(previous))
            saveSeen(loadSeen().filter { $0.accountID != previous })
            removeNotifications(accountID: previous, sessionID: previousSession)
        }
        onChange?()
    }

    func requestPermission() async {
        do {
            _ = try await center.requestAuthorization(options: [.alert, .badge, .sound])
            await updatePermission()
            schedule(immediate: true)
        } catch { await updatePermission() }
    }

    func refreshPermission() { Task { await updatePermission() } }

    private func updatePermission() async {
        let settings = await center.notificationSettings()
        switch settings.authorizationStatus {
        case .authorized, .provisional: permission = "Разрешены"
        case .denied: permission = "Запрещены в настройках macOS"
        case .notDetermined: permission = "Не настроены"
        @unknown default: permission = "Состояние неизвестно"
        }
        onChange?()
    }

    private func schedule(immediate: Bool = false) {
        guard !suspended, loop == nil, accountID != nil, session != nil else { return }
        let run = UUID(); runID = run
        loop = Task { [weak self] in
            guard let self else { return }
            var delay: UInt64 = immediate ? 0 : 1
            while !Task.isCancelled, self.runID == run {
                if delay > 0 { try? await Task.sleep(nanoseconds: delay * 1_000_000_000) }
                guard !Task.isCancelled, self.runID == run else { return }
                let succeeded = await self.poll(run: run)
                delay = succeeded ? 60 : min(300, max(60, delay * 2))
            }
        }
    }

    private func poll(run: UUID) async -> Bool {
        guard let session, let accountID, runID == run else { return false }
        do {
            let headers = inboxETag.map { ["if-none-match": $0] } ?? [:]
            let (data, response) = try await session.call(path: "/api/push/inbox", headers: headers, timeout: 15)
            if response.statusCode == 304 {
                guard !Task.isCancelled, runID == run, self.accountID == accountID else { return false }
                if let signal = incident.succeeded(isRoyal: role == "royal") {
                    await deliverIncident(signal, accountID: accountID, run: run)
                }
                onChange?(); return true
            }
            guard response.statusCode == 200 else {
                if [401, 403].contains(response.statusCode) { throw InboxFailure.authorization }
                throw InboxFailure.connectivity
            }
            let events = try NativeNotificationPolicy.parseInbox(data)
            guard !Task.isCancelled, runID == run, self.accountID == accountID else { return false }
            inboxETag = response.value(forHTTPHeaderField: "ETag")
            unreadCount = events.filter(\.unread).count
            UserDefaults.standard.set(unreadCount, forKey: unreadKey(accountID))
            if let signal = incident.succeeded(isRoyal: role == "royal") {
                await deliverIncident(signal, accountID: accountID, run: run)
            }
            await deliver(events: events, accountID: accountID, run: run)
            onChange?(); return true
        } catch is CancellationError { return false }
        catch InboxFailure.authorization { return false }
        catch {
            guard runID == run, self.accountID == accountID else { return false }
            if await pollMaintenanceFallback(session: session, accountID: accountID, run: run) { return true }
            if let signal = incident.failed(isRoyal: role == "royal", networkAvailable: networkAvailable) {
                await deliverIncident(signal, accountID: accountID, run: run)
            }
            return false
        }
    }

    private func pollMaintenanceFallback(session: WorkspaceSession, accountID: Int, run: UUID) async -> Bool {
        guard let (data, response) = try? await session.call(path: "/api/ops/maintenance", timeout: 8),
              response.statusCode == 200,
              let status = try? NativeNotificationPolicy.parseMaintenance(data),
              !Task.isCancelled, runID == run, self.accountID == accountID else { return false }
        if let signal = incident.succeeded(isRoyal: role == "royal") {
            await deliverIncident(signal, accountID: accountID, run: run)
        }
        let source = "\(status.kind):\(status.operatorName)"
        let fingerprint = SHA256.hash(data: Data(source.utf8)).map { String(format: "%02x", $0) }.joined()
        switch maintenanceEpisode.update(active: status.active, fingerprint: fingerprint) {
        case .started(let episode):
            await deliver(events: [NativeInboxEvent(id: "maintenance-\(episode)", eventType: "maintenance",
                protectedText: "В Robopark идут технические работы.", unread: true)], accountID: accountID, run: run)
        case .recovered(let episode):
            await deliver(events: [NativeInboxEvent(id: "maintenance-recovered-\(episode)", eventType: "system_recovered",
                protectedText: "Технические работы завершены.", unread: true)], accountID: accountID, run: run)
        case .unchanged: break
        }
        onChange?(); return true
    }

    private func deliver(events: [NativeInboxEvent], accountID: Int, run: UUID) async {
        let settings = await center.notificationSettings()
        guard [.authorized, .provisional].contains(settings.authorizationStatus) else { return }
        var seen = loadSeen()
        let existing = Set(seen.filter { $0.accountID == accountID }.map(\.eventID))
        for event in events where event.unread && !existing.contains(event.id) {
            guard runID == run, self.accountID == accountID else { return }
            let notificationID = identifier(accountID: accountID, sessionID: notificationSessionID, eventID: event.id)
            let content = UNMutableNotificationContent()
            content.title = NativeNotificationPolicy.title(for: event.eventType)
            content.body = event.protectedText
            content.categoryIdentifier = "ROBOPARK_INBOX"
            content.userInfo = ["account_id": accountID, "session_id": notificationSessionID, "destination": "notifications"]
            content.sound = .default
            do {
                try await center.add(UNNotificationRequest(identifier: notificationID,
                                                           content: content, trigger: nil))
                guard runID == run, self.accountID == accountID else {
                    center.removePendingNotificationRequests(withIdentifiers: [notificationID])
                    center.removeDeliveredNotifications(withIdentifiers: [notificationID])
                    return
                }
                seen.append(SeenEntry(accountID: accountID, eventID: event.id, seenAt: Date().timeIntervalSince1970))
            } catch { }
        }
        if runID == run, self.accountID == accountID { saveSeen(seen) }
    }

    private func deliverIncident(_ signal: NativeIncidentSignal, accountID: Int, run: UUID) async {
        guard runID == run, self.accountID == accountID else { return }
        let content = UNMutableNotificationContent()
        switch signal {
        case .serverIssue:
            content.title = "Нет связи с сервером Robopark"; content.body = "Mac не получил ответ после нескольких попыток."
        case .networkIssue:
            content.title = "Нет сети"; content.body = "Mac сейчас офлайн; состояние сервера неизвестно."
        case .recovered:
            content.title = "Связь восстановлена"; content.body = "Mac снова получает уведомления Robopark."
        }
        content.categoryIdentifier = "ROBOPARK_INBOX"
        content.userInfo = ["account_id": accountID, "session_id": notificationSessionID, "destination": "notifications"]
        let notificationID = identifier(accountID: accountID, sessionID: notificationSessionID,
                                        eventID: "local-\(String(describing: signal))")
        try? await center.add(UNNotificationRequest(identifier: notificationID, content: content, trigger: nil))
        guard runID == run, self.accountID == accountID else {
            center.removePendingNotificationRequests(withIdentifiers: [notificationID])
            center.removeDeliveredNotifications(withIdentifiers: [notificationID])
            return
        }
    }

    private func identifier(accountID: Int, sessionID: String, eventID: String) -> String {
        let hash = SHA256.hash(data: Data("\(accountID):\(sessionID):\(eventID)".utf8)).map { String(format: "%02x", $0) }.joined()
        return "robopark.\(accountID).\(sessionID).\(hash)"
    }

    private func unreadKey(_ accountID: Int) -> String { "RoboparkNotificationUnreadV1.\(accountID)" }

    private func loadSeen() -> [SeenEntry] {
        guard let data = UserDefaults.standard.data(forKey: seenKey),
              let rows = try? JSONDecoder().decode([SeenEntry].self, from: data) else { return [] }
        let cutoff = Date().timeIntervalSince1970 - 24 * 60 * 60
        return Array(rows.filter { $0.seenAt >= cutoff }.suffix(512))
    }

    private func saveSeen(_ rows: [SeenEntry]) {
        let cutoff = Date().timeIntervalSince1970 - 24 * 60 * 60
        let bounded = Array(rows.filter { $0.seenAt >= cutoff }.suffix(512))
        if let data = try? JSONEncoder().encode(bounded) { UserDefaults.standard.set(data, forKey: seenKey) }
    }

    private func removeNotifications(accountID: Int, sessionID: String) {
        let prefix = "robopark.\(accountID).\(sessionID)."
        center.getDeliveredNotifications { [center] rows in
            center.removeDeliveredNotifications(withIdentifiers: rows.map(\.request.identifier).filter { $0.hasPrefix(prefix) })
        }
        center.getPendingNotificationRequests { [center] rows in
            center.removePendingNotificationRequests(withIdentifiers: rows.map(\.identifier).filter { $0.hasPrefix(prefix) })
        }
    }

    nonisolated func userNotificationCenter(_ center: UNUserNotificationCenter, didReceive response: UNNotificationResponse,
                                             withCompletionHandler completionHandler: @escaping () -> Void) {
        let action = response.actionIdentifier
        let notificationAccount = response.notification.request.content.userInfo["account_id"] as? Int
        let notificationSession = response.notification.request.content.userInfo["session_id"] as? String
        Task { @MainActor [weak self] in
            guard let self, notificationAccount == self.accountID,
                  notificationSession == self.notificationSessionID else { completionHandler(); return }
            if action == "OPEN_WORKSPACE" { self.openWorkspace() }
            else { self.openNotificationCenter() }
            completionHandler()
        }
    }

    nonisolated func userNotificationCenter(_ center: UNUserNotificationCenter,
                                             willPresent notification: UNNotification,
                                             withCompletionHandler completionHandler: @escaping (UNNotificationPresentationOptions) -> Void) {
        let notificationAccount = notification.request.content.userInfo["account_id"] as? Int
        let notificationSession = notification.request.content.userInfo["session_id"] as? String
        Task { @MainActor [weak self] in
            guard let self, notificationAccount == self.accountID,
                  notificationSession == self.notificationSessionID else { completionHandler([]); return }
            completionHandler([.banner, .sound, .badge])
        }
    }
}
