import AppKit
import WebKit
import CryptoKit
import Security
import AgentCore

@MainActor final class AgentApplication: NSObject, NSApplicationDelegate, NSWindowDelegate, WKScriptMessageHandler, WKNavigationDelegate, WKUIDelegate {
    private let testOrigin = TestDeploymentAssignment.origin
    let bridge = Bridge()
    lazy var compute = ComputeBridge(bridge: bridge)
    lazy var updater = Updater(bridge: bridge)
    lazy var notifications = NotificationController(openWorkspace: { [weak self] in self?.showWorkspace() },
        openNotificationCenter: { [weak self] in self?.showNotificationCenter() })
    let autostart = AutostartController()
    var statusItem: NSStatusItem?
    var window: NSWindow?
    var panel: WKWebView?
    var loginWindow: NSWindow?
    var loginView: WKWebView?
    private var loginPopups: [(view: WKWebView, window: NSWindow)] = []
    var legacyWorkspaceWindow: NSWindow?
    var legacyWorkspaceView: WKWebView?
    var legacyMenuItem: NSMenuItem?
    var updateTimer: Timer?
    var updateStatusTimer: Timer?
    var probeTimer: Timer?
    var workspace: WorkspaceServer?
    private let diagnosticJournal = DiagnosticJournal(capacity: 100)
    private var diagnosticRunner: DiagnosticRunner?
    private var enrollmentTask: Task<Void, Never>?
    private struct EnrollmentIdentity: Equatable {
        let userID: Int
        let origin: String
        let instanceID: String
        let role: String
        let canUseEmergency: Bool
    }
    private var enrollmentIdentity: EnrollmentIdentity?
    private var probeRefreshTask: Task<Void, Never>?
    private var probeRefreshRun = UUID()
    private var identityRestoreTask: Task<Void, Never>?
    private var workspaceSetupTask: Task<Void, Error>?
    private var enrollmentRun = UUID()
    private var corporateLoginRun = UUID()
    private var corporateOAuthState: String?
    private var corporateLoginMessage: String?
    private var corporateLoginLabel: NSTextField?
    private var corporateNavigationTimer: Timer?
    private var corporateNavigationEvents: [[String: Any]] = []
    private var accountActive = false
    private var emergencyAuthorized = false
    private var activeAccountID: Int?
    private var accountRole: String?
    private var legacyWorkspaceAvailable: Bool?
    private let accountPresentKey = "RoboparkServiceAccountPresent"
    var lastError = ""
    var accountLabel = "Не выполнен вход"
    var working = false
    var panelURL: URL? { Bundle.main.url(forResource: "panel", withExtension: "html") }

    func applicationDidFinishLaunching(_ notification: Notification) {
        NSApp.setActivationPolicy(.accessory)
        statusItem = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)
        statusItem?.button?.title = "RP"
        do { try initializeWorkspace() }
        catch {
            NSLog("Robopark workspace initialization failed (keychain=%d, code=%ld)",
                (error as NSError).domain == "RoboparkKeychain" ? 1 : 0, (error as NSError).code)
            lastError = SecureStore.failureMessage(error)
        }
        if UserDefaults.standard.bool(forKey: accountPresentKey) { accountLabel = "Проверка аккаунта…" }
        let menu = NSMenu()
        menu.addItem(withTitle: "Открыть Robopark", action: #selector(showWorkspace), keyEquivalent: "")
        menu.addItem(withTitle: "Служба и подключение", action: #selector(showDevice), keyEquivalent: "")
        let legacyItem = menu.addItem(withTitle: "Черновики предыдущей версии…", action: #selector(showLegacyWorkspace), keyEquivalent: "")
        legacyItem.isEnabled = false; legacyMenuItem = legacyItem
        menu.addItem(withTitle: "Перенести данные предыдущей версии…", action: #selector(connectKeychain), keyEquivalent: "")
        menu.addItem(withTitle: "Проверить обновления", action: #selector(checkUpdate), keyEquivalent: "")
        menu.addItem(withTitle: "Удалить Robopark…", action: #selector(uninstall), keyEquivalent: "")
        menu.addItem(.separator())
        menu.addItem(withTitle: "Завершить до следующего входа", action: #selector(quit), keyEquivalent: "q")
        menu.items.forEach { $0.target = self }; statusItem?.menu = menu
        bridge.onChange = { [weak self] in
            guard let self else { return }
            if let registration = self.bridge.registration { try? self.workspace?.session.useRegistration(registration) }
            self.compute.emergencyPriorityChanged()
            self.render()
        }
        bridge.onLifecycle = { [weak self] stage, status, code, elapsedMs in
            self?.diagnosticJournal.append(service: "emergency", stage: stage,
                                           status: status, code: code, elapsedMs: elapsedMs)
        }
        compute.onChange = { [weak self] in self?.render() }
        compute.onLifecycle = { [weak self] stage, status, code, elapsedMs in
            self?.diagnosticJournal.append(service: "compute", stage: stage,
                                           status: status, code: code, elapsedMs: elapsedMs)
        }
        updater.onChange = { [weak self] in self?.render() }
        notifications.onChange = { [weak self] in self?.render() }
        autostart.onChange = { [weak self] in self?.render() }
        autostart.refresh()
        NSWorkspace.shared.notificationCenter.addObserver(self, selector: #selector(wake), name: NSWorkspace.didWakeNotification, object: nil)
        NSWorkspace.shared.notificationCenter.addObserver(self, selector: #selector(sleep), name: NSWorkspace.willSleepNotification, object: nil)
        DistributedNotificationCenter.default().addObserver(self, selector: #selector(showWorkspace), name: Notification.Name("ru.robopark.agent.open"), object: nil)
        startWorkspaceService()
        detectLegacyWorkspace()
        if !CommandLine.arguments.contains("--background") { showWorkspace() }
        restoreAccountSession()
        Task { await updater.check() }
        updateTimer = Timer.scheduledTimer(withTimeInterval: 4 * 60 * 60, repeats: true) { [weak self] _ in
            Task { @MainActor in await self?.updater.check() }
        }
        updateStatusTimer = Timer.scheduledTimer(withTimeInterval: 60, repeats: true) { [weak self] _ in
            Task { @MainActor in self?.updater.refreshStatus() }
        }
        probeTimer = Timer.scheduledTimer(withTimeInterval: 60, repeats: true) { [weak self] _ in
            Task { @MainActor in
                guard let self else { return }
                if self.workspace?.session.profile == nil { self.startWorkspaceService() }
                else if self.accountActive { self.refreshProbeTarget() }
                else { self.restoreAccountSession() }
            }
        }
    }
    private func initializeWorkspace() throws {
        guard workspace == nil else { return }
        let server = try WorkspaceServer()
        if let registration = bridge.registration { try server.session.useRegistration(registration) }
        server.session.onIdentity = { [weak self] user in await self?.accountVerified(user) }
        server.session.onLogout = { [weak self] in await self?.accountLogout() }
        server.agentStatus = { [weak self] in self?.agentStatusResponse() ?? .json(["detail": "agent_starting"], status: 503) }
        server.agentAction = { [weak self] body in
            guard let self else { return .json(["detail": "agent_stopped"], status: 503) }
            return await self.handleAgentAction(body)
        }
        let diagnostics = DiagnosticRunner(journal: diagnosticJournal, bridge: bridge,
                                           session: server.session)
        diagnosticRunner = diagnostics
        server.agentDiagnostics = { [weak self, weak diagnostics] method, body in
            guard let self, let diagnostics else {
                return .json(["detail": "agent_stopped"], status: 503)
            }
            guard self.accountActive, self.accountRole == "royal",
                  let owner = self.bridge.registration?.ownerUserID,
                  owner == self.activeAccountID,
                  owner == self.workspace?.session.profile?.accountID else {
                return .json(["detail": "royal_owner_required"], status: 403)
            }
            if method == "POST" {
                guard let body else { return .json(["detail": "invalid_diagnostic_request"], status: 422) }
                let wasRunning = diagnostics.running
                guard diagnostics.start(body: body) else {
                    return .json(["detail": wasRunning ? "diagnostic_running" : "invalid_diagnostic_request"],
                                 status: wasRunning ? 409 : 422)
                }
            }
            return .json(diagnostics.snapshot())
        }
        workspace = server
    }
    func applicationShouldHandleReopen(_ sender: NSApplication, hasVisibleWindows flag: Bool) -> Bool { showWorkspace(); return true }
    @objc func showWorkspace() { presentWorkspace(route: nil) }
    @objc func showDevice() { presentWorkspace(route: "/device") }
    @objc func showLegacyWorkspace() {
        Task {
            do { try await openLegacyWorkspace() }
            catch { lastError = "Не удалось открыть прежнее рабочее окно. Повторите после запуска локальной службы."; render() }
        }
    }
    func showNotificationCenter() { presentWorkspace(route: "/schedule#notifications") }
    private func presentWorkspace(route: String?) {
        guard let workspace else { showPanel(); return }
        Task {
            do {
                try await workspace.start()
                do { try await ensureWorkspaceConfiguration() }
                catch { lastError = "Тестовый сервер пока недоступен. Локальный интерфейс открыт; служба повторит настройку автоматически." }
                let target = workspace.launchURL(route: route)
                guard NSWorkspace.shared.open(target) else { throw NetworkFailure.invalid }
            } catch {
                lastError = "Не удалось запустить локальный интерфейс. Порт 18444 должен быть свободен; повторите запуск службы."
                showPanel()
            }
        }
    }
    private func startWorkspaceService() {
        guard let workspace else { return }
        Task {
            do {
                try await workspace.start()
                try await ensureWorkspaceConfiguration()
                if lastError.contains("Тестовый сервер пока недоступен") { lastError = "" }
            } catch {
                lastError = workspace.session.profile == nil
                    ? "Тестовый сервер пока недоступен. Локальная служба повторит настройку автоматически."
                    : "Не удалось запустить локальный интерфейс на 127.0.0.1:18444."
                render()
            }
        }
    }
    private func ensureWorkspaceConfiguration() async throws {
        guard let workspace, workspace.session.profile == nil else { return }
        if let workspaceSetupTask { try await workspaceSetupTask.value; return }
        let task = Task { @MainActor [bridge, testOrigin] in
            let origin = try ServerOrigin(testOrigin)
            let (info, _) = try await bridge.verifiedInfo(origin)
            try workspace.session.configure(origin: origin, info: info)
        }
        workspaceSetupTask = task
        defer { workspaceSetupTask = nil }
        try await task.value
    }
    private func detectLegacyWorkspace() {
        WKWebsiteDataStore.default().fetchDataRecords(ofTypes: [WKWebsiteDataTypeIndexedDBDatabases]) { [weak self] records in
            Task { @MainActor in
                guard let self else { return }
                let found = records.contains { $0.displayName == "127.0.0.1" }
                self.legacyWorkspaceAvailable = found
                self.legacyMenuItem?.isEnabled = found
                self.render()
            }
        }
    }
    private func openLegacyWorkspace() async throws {
        guard legacyWorkspaceAvailable == true, let workspace else { throw NetworkFailure.invalid }
        try await workspace.start()
        try await ensureWorkspaceConfiguration()
        let target = workspace.launchURL(route: nil)
        if legacyWorkspaceView == nil {
            let config = WKWebViewConfiguration()
            config.websiteDataStore = .default()
            let view = WKWebView(frame: .zero, configuration: config)
            view.navigationDelegate = self; view.uiDelegate = self
            let win = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 1240, height: 850),
                styleMask: [.titled, .closable, .miniaturizable, .resizable], backing: .buffered, defer: false)
            win.title = "Robopark — черновики предыдущей версии"
            win.contentView = view; win.isReleasedWhenClosed = false; win.center()
            legacyWorkspaceView = view; legacyWorkspaceWindow = win
        }
        legacyWorkspaceView?.load(URLRequest(url: target))
        legacyWorkspaceWindow?.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)
    }
    private func restoreAccountSession() {
        guard identityRestoreTask == nil, let session = workspace?.session else { return }
        identityRestoreTask = Task { [weak self] in
            _ = await session.restoreIdentity()
            self?.identityRestoreTask = nil
        }
    }
    private func accountVerified(_ user: [String: Any]) async {
        guard let session = workspace?.session,
              let profile = session.profile, let userID = user["id"] as? Int,
              TestDeploymentAssignment.matches(origin: profile.origin, instanceID: profile.instanceID,
                                               environment: profile.environment) else { return }
        accountLabel = (user["display_name"] as? String) ?? (user["username"] as? String) ?? "Аккаунт Robopark"
        accountRole = user["role"] as? String
        guard user["access_status"] as? String == "approved", user["must_change_password"] as? Bool != true else {
            enrollmentRun = UUID(); enrollmentTask?.cancel(); enrollmentTask = nil
            accountActive = false
            emergencyAuthorized = false
            activeAccountID = nil
            accountRole = nil
            UserDefaults.standard.set(false, forKey: accountPresentKey)
            cancelProbeRefresh()
            bridge.stop()
            compute.stop()
            notifications.stop(removeDelivered: true)
            return
        }
        // The server has already verified the workspace identity. Registering
        // the worker channel must not delay /auth/me and the browser shell.
        let canUseEmergency = (user["permissions"] as? [String] ?? []).contains("nav.emergency")
        let identity = EnrollmentIdentity(userID: userID, origin: profile.origin,
            instanceID: profile.instanceID, role: user["role"] as? String ?? "",
            canUseEmergency: canUseEmergency)
        if enrollmentTask != nil {
            guard enrollmentIdentity != identity else { return }
            enrollmentRun = UUID(); enrollmentTask?.cancel(); enrollmentTask = nil
            bridge.stop(); compute.stop(); cancelProbeRefresh()
        }
        if accountActive, activeAccountID == userID,
           bridge.registration?.origin == profile.origin,
           bridge.registration?.instanceID == profile.instanceID {
            if emergencyAuthorized && canUseEmergency { bridge.startIfNeeded() }
            if emergencyAuthorized != canUseEmergency {
                emergencyAuthorized = canUseEmergency
                if canUseEmergency { bridge.start(); refreshProbeTarget() }
                else { cancelProbeRefresh(); bridge.stop(); try? bridge.clearProbe() }
            }
            notifications.start(accountID: userID, role: user["role"] as? String ?? "", session: session)
            render()
            return
        }
        cancelProbeRefresh()
        let run = UUID(); enrollmentRun = run; enrollmentIdentity = identity
        let task = Task { [weak self] in
            guard let self else { return }
            defer { if self.enrollmentRun == run { self.enrollmentTask = nil; self.render() } }
            do {
                if !canUseEmergency { self.bridge.stop() }
                if let existing = self.bridge.registration,
                   existing.instanceID != profile.instanceID || existing.origin != profile.origin {
                    throw AgentError.ownerMismatch
                }
                if !self.accountActive || self.activeAccountID != userID { try self.bridge.clearProbe() }
                let label = String((Host.current().localizedName ?? "Mac").prefix(80))
                try await self.bridge.claimAuthenticated(origin: profile.origin, label: label,
                                                          expectedOwnerUserID: userID,
                                                          startEmergency: false) { object in
                    try await session.call(path: "/api/agent-devices/claim-auth", method: "POST", object: object)
                }
                guard !Task.isCancelled, self.enrollmentRun == run,
                      self.workspace?.session === session,
                      session.profile?.accountID == userID,
                      session.profile?.ownerUserID == userID,
                      !SecureStore.isSignedOut("workspace") else { return }
                self.accountActive = true
                self.emergencyAuthorized = canUseEmergency
                self.activeAccountID = userID
                UserDefaults.standard.set(true, forKey: self.accountPresentKey)
                if canUseEmergency { self.bridge.start(); self.refreshProbeTarget() }
                self.compute.start()
                self.notifications.start(accountID: userID, role: user["role"] as? String ?? "", session: session)
                Task { @MainActor [weak self] in await self?.updater.check() }
            } catch is CancellationError { }
            catch {
                if self.enrollmentRun == run {
                    self.accountActive = false
                    self.accountRole = nil
                    self.bridge.stop()
                    self.compute.stop()
                    self.notifications.stop(removeDelivered: false)
                    self.lastError = "Рабочий интерфейс доступен. Этот ключ Mac не закреплён за текущим аккаунтом или сервер недоступен."
                }
            }
        }
        enrollmentTask = task
    }
    private func accountLogout() async {
        accountLabel = "Не выполнен вход"
        accountActive = false
        emergencyAuthorized = false
        activeAccountID = nil
        accountRole = nil
        UserDefaults.standard.set(false, forKey: accountPresentKey)
        cancelProbeRefresh()
        identityRestoreTask?.cancel(); identityRestoreTask = nil
        enrollmentRun = UUID(); enrollmentTask?.cancel(); enrollmentTask = nil
        // Signing out stops work but preserves the device key and owner binding.
        // A different account cannot claim that key; the original owner can
        // resume without creating a second Mac entry.
        bridge.stop()
        compute.stop()
        notifications.stop(removeDelivered: true)
        updater.reset()
        corporateLoginRun = UUID(); corporateOAuthState = nil
        corporateNavigationEvents.removeAll()
        closeCorporatePopups()
        loginWindow?.close(); loginWindow = nil; loginView = nil
        // Close the legacy surface on logout but preserve its default WebKit
        // data store so the owner can reopen and synchronize old drafts later.
        legacyWorkspaceWindow?.close(); legacyWorkspaceWindow = nil; legacyWorkspaceView = nil
    }
    private func refreshProbeTarget() {
        guard accountActive, emergencyAuthorized, probeRefreshTask == nil, let session = workspace?.session,
              let accountID = activeAccountID,
              bridge.registration != nil else { return }
        let enrollment = enrollmentRun
        let run = UUID()
        probeRefreshRun = run
        probeRefreshTask = Task { [weak self] in
            guard let self else { return }
            defer { if self.probeRefreshRun == run { self.probeRefreshTask = nil } }
            guard let (data, response) = try? await session.call(path: "/api/agent-devices/probe-target", timeout: 5),
                  response.statusCode == 200,
                  let object = try? JSONSerialization.jsonObject(with: data) as? [String: Any] else { return }
            guard !Task.isCancelled, self.accountActive, self.emergencyAuthorized,
                  self.activeAccountID == accountID, self.enrollmentRun == enrollment,
                  self.workspace?.session === session else { return }
            if let vin = object["vin"] as? String, !vin.isEmpty {
                _ = try? await self.bridge.refreshInventoryReadiness(forProbe: vin)
            } else { try? self.bridge.clearProbe() }
            guard !Task.isCancelled, self.accountActive, self.emergencyAuthorized,
                  self.activeAccountID == accountID, self.enrollmentRun == enrollment,
                  self.workspace?.session === session else { return }
            self.render()
        }
    }
    private func cancelProbeRefresh() {
        probeRefreshRun = UUID()
        probeRefreshTask?.cancel()
        probeRefreshTask = nil
    }
    @objc func showPanel() {
        if window == nil {
            let controller = WKUserContentController()
            controller.add(self, name: "agent")
            let config = WKWebViewConfiguration()
            config.userContentController = controller
            config.websiteDataStore = .nonPersistent()
            let view = WKWebView(frame: .zero, configuration: config)
            view.navigationDelegate = self
            panel = view
            let win = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 680, height: 780),
                               styleMask: [.titled, .closable, .miniaturizable, .resizable], backing: .buffered, defer: false)
            win.title = "Robopark — служба Mac"; win.contentView = view
            win.isReleasedWhenClosed = false; win.center(); window = win
            if let url = panelURL { view.loadFileURL(url, allowingReadAccessTo: url.deletingLastPathComponent()) }
        }
        window?.makeKeyAndOrderFront(nil); NSApp.activate(ignoringOtherApps: true)
        render()
    }
    @objc func checkUpdate() { Task { await updater.check(manual: true) } }
    @objc func wake() {
        if accountActive {
            if emergencyAuthorized { bridge.start(); refreshProbeTarget() }
            compute.start()
            notifications.resume()
        } else { restoreAccountSession() }
        Task { await updater.check() }
    }
    @objc func sleep() { bridge.stop(); compute.stop(); notifications.suspend() }
    @objc func quit() { enrollmentTask?.cancel(); workspace?.stop(); bridge.stop(); compute.stop(); notifications.suspend(); NSApp.terminate(nil) }
    private func restoreAfterFailedUninstall() {
        if accountActive {
            if emergencyAuthorized { bridge.start(); refreshProbeTarget() }
            compute.start()
            notifications.resume()
        } else {
            restoreAccountSession()
        }
        startWorkspaceService()
    }
    @objc func uninstall() {
        let alert = NSAlert()
        alert.messageText = "Удалить Robopark?"
        alert.informativeText = "Можно сохранить аккаунт и офлайн-данные для повторной установки. Полное удаление удалит локальные сессии и данные прежнего встроенного окна. Данные сайта 127.0.0.1 в системном браузере сохранятся."
        alert.addButton(withTitle: "Отмена")
        alert.addButton(withTitle: "Удалить, сохранив данные")
        alert.addButton(withTitle: "Удалить все данные")
        let choice = alert.runModal()
        guard choice == .alertSecondButtonReturn || choice == .alertThirdButtonReturn else { return }
        let removeData = choice == .alertThirdButtonReturn
        if removeData {
            let confirm = NSAlert()
            confirm.messageText = "Удалить аккаунт и офлайн-данные?"
            confirm.informativeText = "Будут удалены локальный вход и данные прежнего встроенного окна. Данные сайта 127.0.0.1 в системном браузере можно удалить отдельно в настройках данных сайтов браузера."
            confirm.addButton(withTitle: "Отмена"); confirm.addButton(withTitle: "Удалить всё")
            guard confirm.runModal() == .alertSecondButtonReturn else { return }
        }
        guard let script = Bundle.main.url(forResource: "uninstall", withExtension: "command"),
              script.lastPathComponent == "uninstall.command",
              let values = try? script.resourceValues(forKeys: [.isRegularFileKey, .isSymbolicLinkKey, .fileSizeKey]),
              values.isRegularFile == true, values.isSymbolicLink != true,
              let size = values.fileSize, size > 0, size <= 128 * 1024,
              let attributes = try? FileManager.default.attributesOfItem(atPath: script.path),
              (attributes[.ownerAccountID] as? NSNumber)?.intValue == 0,
              ((attributes[.posixPermissions] as? NSNumber)?.intValue ?? 0) & 0o022 == 0 else {
            lastError = "Встроенный деинсталлятор повреждён. Переустановите пакет и повторите удаление."
            render(); return
        }
        enrollmentTask?.cancel(); workspace?.stop(); bridge.stop(); compute.stop(); notifications.suspend()
        let quotedPath = "'" + script.path.replacingOccurrences(of: "'", with: "'\\''") + "'"
        let fixedCommand = quotedPath + (removeData ? " --remove-data" : "")
        let appleCommand = fixedCommand.replacingOccurrences(of: "\\", with: "\\\\").replacingOccurrences(of: "\"", with: "\\\"")
        Task {
            let process = Process(); process.executableURL = URL(fileURLWithPath: "/usr/bin/osascript")
            process.arguments = ["-e", "do shell script \"\(appleCommand)\" with administrator privileges"]
            process.standardOutput = FileHandle.nullDevice; process.standardError = FileHandle.nullDevice
            do {
                try process.run()
                await Task.detached(priority: .utility) { process.waitUntilExit() }.value
                if process.terminationStatus == 0 { NSApp.terminate(nil) }
                else {
                    lastError = "Удаление отменено или не завершилось."
                    restoreAfterFailedUninstall(); render()
                }
            } catch {
                lastError = "Не удалось открыть системный запрос удаления."
                restoreAfterFailedUninstall(); render()
            }
        }
    }
    private func corporateServiceStates() -> (tracker: String, inventory: String, emergency: String) {
        guard let profile = bridge.registration else {
            return ("login_required", "unverified", "login_required")
        }
        let owner = CorporateAuthPolicy.normalizedLogin(profile.ownerTrackerLogin)
        let login = CorporateAuthPolicy.normalizedLogin(profile.corporateLogin)
        let tracker: String
        if owner == nil || profile.trackerOAuthToken?.isEmpty != false {
            tracker = "login_required"
        } else if bridge.hasVerifiedCorporateOAuth, login == owner {
            tracker = "ready"
        } else if profile.trackerOAuthExpiresAt.map({ $0.timeIntervalSinceNow <= 60 }) ?? false {
            tracker = "login_required"
        } else {
            tracker = "unverified"
        }
        let inventory: String
        switch bridge.inventoryReadiness {
        case .ready where tracker == "ready" && !profile.probeVIN.isEmpty: inventory = "ready"
        case .denied: inventory = "denied"
        case .unreachable: inventory = "unavailable"
        default: inventory = "unverified"
        }
        let emergency: String
        if owner == nil || profile.cookie.isEmpty {
            emergency = "login_required"
        } else if login != owner {
            emergency = "denied"
        } else {
            switch CorporateIdentityStatus(rawValue: profile.corporateIdentityStatus ?? "") {
            case .ready where profile.corporateCookieVerifiedAt != nil: emergency = "ready"
            case .reauthRequired: emergency = "login_required"
            case .accessDenied, .ownerMismatch: emergency = "denied"
            case .networkUnavailable: emergency = "unavailable"
            default: emergency = "unverified"
            }
        }
        return (tracker, inventory, emergency)
    }
    private func agentStatusResponse() -> LocalHTTPResponse {
        let profile = bridge.registration
        let session = workspace?.session
        let storedAccountOffline = !accountActive && session?.hasStoredIdentity == true
            && ["offline", "error"].contains(session?.connectionState ?? "")
        let accountState: String
        if accountActive { accountState = "signed_in" }
        else if identityRestoreTask != nil || enrollmentTask != nil { accountState = "checking" }
        else if storedAccountOffline || (UserDefaults.standard.bool(forKey: accountPresentKey) && !lastError.isEmpty) { accountState = "error" }
        else { accountState = "signed_out" }
        let ownerLoginConfigured = CorporateAuthPolicy.normalizedLogin(profile?.ownerTrackerLogin) != nil
        let ownerBound = accountActive && activeAccountID != nil && activeAccountID == profile?.ownerUserID
        let services = corporateServiceStates()
        let serverState = session?.profile == nil && workspaceSetupTask == nil &&
            lastError.contains("Тестовый сервер пока недоступен") ? "offline" : (session?.connectionState ?? "offline")
        let message: String?
        if storedAccountOffline { message = "Аккаунт сохранён. Проверим подключение автоматически, когда сервер станет доступен." }
        else if !accountActive { message = "Войдите в аккаунт Robopark на этом Mac." }
        else if !ownerLoginConfigured { message = "Для владельца Mac не назначен корпоративный логин." }
        else if let corporateLoginMessage { message = corporateLoginMessage }
        else if DesktopAgentStatus.requiredCorporateServicesReady(
            tracker: services.tracker,
            emergency: services.emergency
        ) { message = nil }
        else { message = "Часть корпоративных сервисов требует входа или повторной проверки." }
        let status = DesktopAgentStatus(snapshot: AgentStatusSnapshot(
            accountState: accountState,
            accountLabel: storedAccountOffline ? "Аккаунт сохранён — нет связи с сервером" : accountLabel,
            accountRole: accountRole,
            registered: profile != nil,
            serverState: serverState,
            serverLastSuccessAt: session?.lastSuccessAt,
            ownerLoginConfigured: ownerLoginConfigured,
            probeConfigured: profile?.probeVIN.isEmpty == false,
            tracker: services.tracker,
            inventory: services.inventory,
            emergency: services.emergency,
            corporateChecking: loginWindow != nil,
            corporateMessage: message,
            corporateCanLogin: ownerBound && ownerLoginConfigured,
            autostartSupported: true,
            autostartEnabled: autostart.enabled,
            paused: profile?.paused ?? false,
            legacyWorkspaceAvailable: legacyWorkspaceAvailable,
            updateState: updater.statusState,
            currentVersion: updater.version,
            availableVersion: updater.availableVersion,
            updateMessage: updater.message
        ))
        var object = status.jsonObject
        if accountActive && accountRole == "royal" {
            object["corporateNavigation"] = corporateNavigationEvents
            object["channels"] = ["emergency": bridge.state, "upstream": bridge.upstreamState,
                                  "compute": compute.state, "emergencyAuthorized": emergencyAuthorized]
        }
        return .json(object)
    }
    private func handleAgentAction(_ body: [String: Any]) async -> LocalHTTPResponse {
        let action: DesktopAgentAction
        do { action = try DesktopAgentAction(body: body) }
        catch { return .json(["detail": "invalid_agent_action"], status: 422) }
        do {
            switch action {
            case .connect:
                guard let workspace else { throw NetworkFailure.invalid }
                try await workspace.start()
                try await ensureWorkspaceConfiguration()
                if accountActive {
                    if emergencyAuthorized { bridge.start() }
                    compute.start(); notifications.resume()
                } else {
                    _ = await workspace.session.restoreIdentity()
                }
            case .corporateLogin:
                let profile = bridge.registration
                guard accountActive, activeAccountID == profile?.ownerUserID,
                      CorporateAuthPolicy.normalizedLogin(profile?.ownerTrackerLogin) != nil else {
                    return .json(["detail": "corporate_login_unavailable"], status: 409)
                }
                openEmergencyLogin()
            case .autostart(let enabled):
                try await autostart.setEnabled(enabled)
            case .pause(let enabled):
                guard bridge.registration != nil else { return .json(["detail": "device_not_registered"], status: 409) }
                try bridge.pause(enabled)
            case .checkUpdate:
                await updater.check(manual: true)
            case .reloadKeychain:
                try recoverKeychain()
            case .openLegacyWorkspace:
                try await openLegacyWorkspace()
            }
            return agentStatusResponse()
        } catch {
            lastError = SecureStore.failureMessage(error)
            render()
            return .json(["detail": "agent_action_failed"], status: 409)
        }
    }
    @objc private func connectKeychain() {
        showPanel()
        // Give the foreground panel a run-loop turn before asking SecurityAgent.
        DispatchQueue.main.async { [weak self] in
            do { try self?.recoverKeychain() }
            catch { self?.lastError = SecureStore.failureMessage(error); self?.render() }
        }
    }
    private func recoverKeychain() throws {
        NSApp.activate(ignoringOtherApps: true)
        try SecureStore.withUserApproval {
            try bridge.reloadRegistration()
            try initializeWorkspace()
            if let registration = bridge.registration { try workspace?.session.useRegistration(registration) }
            // Migrate the offline key in the same explicit foreground operation.
            _ = try SecureStore.readData("workspace-storage-key-v1")
            try SecureStore.migrateCorporateWriteReceipts()
        }
        lastError = ""; startWorkspaceService(); restoreAccountSession(); render()
    }
    func render() {
        let profile = bridge.registration
        let object: [String: Any] = [
            "registered": profile != nil, "environment": profile?.environment ?? "",
            "legacyDataPending": SecureStore.hasDeferredLegacyData,
            "label": profile?.label ?? "", "state": bridge.state, "upstream": bridge.upstreamState,
            "needsCorporateLogin": bridge.needsCorporateLogin,
            "version": updater.version, "updates": updater.message,
            "paused": profile?.paused ?? false, "completed": bridge.completed,
            "emergencyAuthorized": emergencyAuthorized,
            "computeState": compute.state, "computeActive": compute.active,
            "computeCompleted": compute.completed, "computeInputBytes": compute.inputBytes,
            "computeOutputBytes": compute.outputBytes,
            "notificationPermission": notifications.permission, "notificationCount": notifications.unreadCount,
            "account": accountLabel, "autostart": autostart.enabled,
            "error": lastError, "busy": working || updater.busy
        ]
        guard let bytes = try? JSONSerialization.data(withJSONObject: object), let json = String(data: bytes, encoding: .utf8) else { return }
        panel?.evaluateJavaScript("window.render(\(json))", completionHandler: nil)
    }
    func userContentController(_ userContentController: WKUserContentController, didReceive message: WKScriptMessage) {
        if message.name == "corporateOAuthCompletion" {
            receiveCorporateCompletion(message); return
        }
        guard message.webView === panel, message.frameInfo.isMainFrame,
              message.frameInfo.request.url?.standardizedFileURL == panelURL?.standardizedFileURL,
              let body = message.body as? [String: Any], let action = body["action"] as? String, !working else { return }
        working = true; lastError = ""; render()
        Task {
            defer { working = false; render() }
            do {
                switch action {
                case "workspaceSetup":
                    let origin = try ServerOrigin(testOrigin)
                    let (info, _) = try await bridge.verifiedInfo(origin)
                    guard let workspace else { throw NetworkFailure.invalid }
                    if let old = workspace.session.profile, old.origin != origin.url.absoluteString || old.instanceID != info.instance_id {
                        throw AgentError.ownerMismatch
                    }
                    try workspace.session.configure(origin: origin, info: info)
                    showWorkspace()
                case "emergencyLogin": try bridge.requireTrust(); openEmergencyLogin()
                case "pause": try bridge.pause(!(bridge.registration?.paused ?? false))
                case "autostart":
                    try await autostart.setEnabled(body["enabled"] as? Bool ?? true)
                case "open":
                    showWorkspace()
                case "openNotifications":
                    showNotificationCenter()
                case "notificationPermission":
                    await notifications.requestPermission()
                case "reloadKeychain":
                    try recoverKeychain()
                case "check": await updater.check(manual: true)
                case "uninstall": uninstall()
                case "ready": break
                default: throw AgentError.invalidJob
                }
            } catch {
                lastError = SecureStore.failureMessage(error)
            }
        }
    }
    private func freshOAuthState() throws -> String {
        var bytes = [UInt8](repeating: 0, count: 32)
        guard SecRandomCopyBytes(kSecRandomDefault, bytes.count, &bytes) == errSecSuccess else {
            throw NetworkFailure.invalid
        }
        return Data(bytes).base64EncodedString().replacingOccurrences(of: "+", with: "-")
            .replacingOccurrences(of: "/", with: "_").replacingOccurrences(of: "=", with: "")
    }
    func openEmergencyLogin() {
        guard accountActive,
              let ownerUserID = bridge.registration?.ownerUserID,
              activeAccountID == ownerUserID,
              CorporateAuthPolicy.normalizedLogin(bridge.registration?.ownerTrackerLogin) != nil else {
            lastError = "Для владельца Mac не задан корпоративный логин. Обновите профиль на сервере."
            render(); return
        }
        let state: String
        do { state = try freshOAuthState() }
        catch { lastError = "Не удалось безопасно начать корпоративный вход."; render(); return }
        corporateNavigationTimer?.invalidate()
        closeCorporatePopups()
        corporateLoginRun = UUID(); corporateOAuthState = state
        corporateNavigationEvents.removeAll()
        setCorporateLoginMessage("Открываю корпоративный вход…")
        // Retrying keeps the in-memory SSO cookies, so a completed password/MFA
        // step need not be repeated after a network timeout.
        if let loginView, let loginWindow {
            loginView.stopLoading()
            guard installCorporateCompletion(in: loginView.configuration.userContentController) else { return }
            if let url = try? CorporateAuthPolicy.authorizationURL(state: state) {
                loginView.load(URLRequest(url: url, timeoutInterval: 40))
            }
            loginWindow.makeKeyAndOrderFront(nil)
            render(); return
        }
        let config = WKWebViewConfiguration()
        config.websiteDataStore = .nonPersistent()
        // Passport can finish an asynchronous login step by opening a child.
        // The UI delegate still enforces the trusted opener, destination and cap.
        config.preferences.javaScriptCanOpenWindowsAutomatically = true
        guard installCorporateCompletion(in: config.userContentController) else { return }
        let view = WKWebView(frame: .zero, configuration: config)
        view.navigationDelegate = self; view.uiDelegate = self
        let win = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 1000, height: 740),
                           styleMask: [.titled, .closable, .resizable], backing: .buffered, defer: false)
        win.title = "Подключение корпоративных сервисов"
        win.delegate = self
        let label = NSTextField(wrappingLabelWithString: corporateLoginMessage ?? "")
        corporateLoginLabel = label
        label.setContentCompressionResistancePriority(.defaultLow, for: .horizontal)
        let retry = NSButton(title: "Повторить", target: self, action: #selector(retryCorporateLogin))
        let status = NSStackView(views: [label, retry]); status.orientation = .horizontal
        status.spacing = 12; status.edgeInsets = NSEdgeInsets(top: 12, left: 16, bottom: 12, right: 16)
        let container = NSView()
        for child in [status, view] { child.translatesAutoresizingMaskIntoConstraints = false; container.addSubview(child) }
        NSLayoutConstraint.activate([
            status.topAnchor.constraint(equalTo: container.topAnchor), status.leadingAnchor.constraint(equalTo: container.leadingAnchor),
            status.trailingAnchor.constraint(equalTo: container.trailingAnchor), status.heightAnchor.constraint(greaterThanOrEqualToConstant: 56),
            view.topAnchor.constraint(equalTo: status.bottomAnchor), view.leadingAnchor.constraint(equalTo: container.leadingAnchor),
            view.trailingAnchor.constraint(equalTo: container.trailingAnchor), view.bottomAnchor.constraint(equalTo: container.bottomAnchor),
        ])
        win.contentView = container; win.isReleasedWhenClosed = false; win.center()
        loginWindow = win; loginView = view
        guard let url = try? CorporateAuthPolicy.authorizationURL(state: state) else { return }
        view.load(URLRequest(url: url, timeoutInterval: 40))
        win.makeKeyAndOrderFront(nil)
        render()
    }
    private func isCorporateView(_ view: WKWebView) -> Bool {
        view === loginView || loginPopups.contains { $0.view === view }
    }
    private func installCorporateCompletion(in controller: WKUserContentController) -> Bool {
        guard let sourceURL = Bundle.main.url(forResource: "corporate-oauth", withExtension: "js"),
              let source = try? String(contentsOf: sourceURL, encoding: .utf8) else {
            failCorporateNavigation("Не удалось загрузить завершение входа. Переустановите обновление Robopark.")
            return false
        }
        controller.removeAllUserScripts()
        controller.removeScriptMessageHandler(forName: "corporateOAuthCompletion", contentWorld: .defaultClient)
        controller.add(self, contentWorld: .defaultClient, name: "corporateOAuthCompletion")
        controller.addUserScript(WKUserScript(source: source.replacingOccurrences(
            of: "__ROBOPARK_LOGIN_RUN__", with: corporateLoginRun.uuidString),
            injectionTime: .atDocumentEnd, forMainFrameOnly: true, in: .defaultClient))
        return true
    }
    private func receiveCorporateCompletion(_ message: WKScriptMessage) {
        guard let view = message.webView, isCorporateView(view), message.frameInfo.isMainFrame,
              message.frameInfo.securityOrigin.protocol == "https",
              message.frameInfo.securityOrigin.host.lowercased() == CorporateAuthPolicy.oauthHost,
              [0, 443].contains(message.frameInfo.securityOrigin.port),
              let page = view.url, CorporateAuthPolicy.isOAuthCompletionPage(page),
              let body = message.body as? [String: Any], body["run"] as? String == corporateLoginRun.uuidString,
              let source = body["url"] as? String, source.utf8.count <= 20 * 1024,
              let sourcePage = URL(string: source), CorporateAuthPolicy.isOAuthCompletionPage(sourcePage),
              let state = corporateOAuthState else { return }
        do {
            let credential: CorporateOAuthCredential
            if body["kind"] as? String == "callback" {
                credential = try CorporateAuthPolicy.oauthCredential(from: sourcePage, expectedState: state)
            } else if body["kind"] as? String == "displayed", let token = body["token"] as? String {
                credential = try CorporateAuthPolicy.displayedOAuthCredential(token: token, pageURL: sourcePage, expectedState: state)
            } else { return }
            finishCorporateOAuth(credential, in: view)
        } catch { showCorporateFailure(.reauthRequired) }
    }
    private func finishCorporateOAuth(_ credential: CorporateOAuthCredential, in view: WKWebView) {
        guard corporateOAuthState != nil else { return }
        corporateOAuthState = nil // Exactly one candidate per login attempt, including child windows.
        corporateNavigationTimer?.invalidate(); corporateNavigationTimer = nil
        recordCorporateNavigation(.callback, url: view.url)
        setCorporateLoginMessage("Подключаю Tracker, Inventory и Emergency…")
        let run = corporateLoginRun
        Task { @MainActor [weak self, weak view] in
            guard let self, let view else { return }
            let (status, login) = await self.verifyCorporateIdentity(authorization: "OAuth \(credential.token)", run: run)
            guard self.corporateLoginRun == run, self.isCorporateView(view) else { return }
            guard status == .ready, let login else { self.showCorporateFailure(status); return }
            do {
                try self.bridge.storeVerifiedOAuth(credential, login: login)
                // The same in-memory Passport session supplies Emergency's
                // cookie. OAuth is sent only to the OAuth-enabled API hosts.
                view.load(URLRequest(url: URL(string: "https://\(EmergencyTarget.host)/")!))
                _ = await self.bridge.verifyInventoryReadiness(force: true)
                self.render()
            } catch { self.showCorporateFailure(.ownerMismatch) }
        }
    }
    private func recordCorporateNavigation(_ phase: CorporateLoginDiagnostic.Phase, url: URL?,
                                            mainFrame: Bool = true, code: Int? = nil) {
        corporateNavigationEvents.append(CorporateLoginDiagnostic.event(phase, url: url, mainFrame: mainFrame, code: code))
        if corporateNavigationEvents.count > 48 { corporateNavigationEvents.removeFirst(corporateNavigationEvents.count - 48) }
    }
    private func closeCorporatePopups() {
        let windows = loginPopups; loginPopups.removeAll()
        for popup in windows { popup.view.stopLoading(); popup.window.close() }
    }
    func webViewDidClose(_ webView: WKWebView) {
        if let popup = loginPopups.first(where: { $0.view === webView }) { popup.window.close() }
    }
    @objc private func retryCorporateLogin() { openEmergencyLogin() }
    private func setCorporateLoginMessage(_ message: String) {
        corporateLoginMessage = message; corporateLoginLabel?.stringValue = message
        render()
    }
    private func failCorporateNavigation(_ message: String) {
        corporateNavigationTimer?.invalidate(); corporateNavigationTimer = nil
        lastError = message; setCorporateLoginMessage(message)
    }
    func windowWillClose(_ notification: Notification) {
        guard let closing = notification.object as? NSWindow else { return }
        if let index = loginPopups.firstIndex(where: { $0.window === closing }) {
            loginPopups.remove(at: index).view.stopLoading(); return
        }
        guard closing === loginWindow else { return }
        closeCorporatePopups()
        corporateLoginRun = UUID(); corporateOAuthState = nil
        loginView?.configuration.userContentController.removeScriptMessageHandler(
            forName: "corporateOAuthCompletion", contentWorld: .defaultClient)
        corporateNavigationTimer?.invalidate(); corporateNavigationTimer = nil
        corporateLoginLabel = nil
        loginWindow = nil; loginView = nil
        corporateLoginMessage = nil
        render()
    }
    func webView(_ webView: WKWebView, didStartProvisionalNavigation navigation: WKNavigation!) {
        guard isCorporateView(webView) else { return }
        corporateNavigationTimer?.invalidate()
        let run = corporateLoginRun
        setCorporateLoginMessage("Загружаю страницу корпоративного входа…")
        corporateNavigationTimer = Timer.scheduledTimer(withTimeInterval: 45, repeats: false) { [weak self, weak webView] _ in
            Task { @MainActor in
                guard let self, let webView, self.corporateLoginRun == run, self.isCorporateView(webView) else { return }
                webView.stopLoading()
                self.failCorporateNavigation(CorporateLoginPresentation.failureMessage(
                    NSError(domain: NSURLErrorDomain, code: NSURLErrorTimedOut))!)
            }
        }
    }
    func webView(_ webView: WKWebView, didFailProvisionalNavigation navigation: WKNavigation!, withError error: Error) {
        if isCorporateView(webView) {
            recordCorporateNavigation(.failed, url: webView.url, code: (error as NSError).code)
        }
        guard isCorporateView(webView), let message = CorporateLoginPresentation.failureMessage(error as NSError) else { return }
        failCorporateNavigation(message)
    }
    func webView(_ webView: WKWebView, didFail navigation: WKNavigation!, withError error: Error) {
        self.webView(webView, didFailProvisionalNavigation: navigation, withError: error)
    }
    func webViewWebContentProcessDidTerminate(_ webView: WKWebView) {
        guard isCorporateView(webView) else { return }
        failCorporateNavigation("Окно корпоративного входа было остановлено macOS. Нажмите «Повторить».")
    }
    private func verifyCorporateIdentity(authorization: String, run: UUID) async -> (CorporateIdentityStatus, String?) {
        guard corporateLoginRun == run, accountActive,
              activeAccountID == bridge.registration?.ownerUserID,
              let owner = CorporateAuthPolicy.normalizedLogin(bridge.registration?.ownerTrackerLogin) else {
            return (.ownerMismatch, nil)
        }
        do {
            let (body, response) = try await Network.corporateIdentity(authorization: authorization)
            return (CorporateAuthPolicy.identityStatus(httpStatus: response.statusCode, body: body, expectedLogin: owner),
                    CorporateAuthPolicy.identityLogin(from: body))
        } catch { return (.networkUnavailable, nil) }
    }
    private func showCorporateFailure(_ status: CorporateIdentityStatus) {
        switch status {
        case .reauthRequired: lastError = "Корпоративная сессия истекла. Выполните вход повторно."
        case .accessDenied: lastError = "Корпоративный аккаунт не имеет доступа к сервисам."
        case .networkUnavailable: lastError = "Не удалось проверить корпоративный аккаунт. Проверьте сеть."
        case .ownerMismatch: lastError = "Этот корпоративный аккаунт не принадлежит владельцу Mac."
        case .ready: break
        }
        failCorporateNavigation(lastError)
    }
    func webView(_ webView: WKWebView, decidePolicyFor navigationResponse: WKNavigationResponse,
                 decisionHandler: @escaping (WKNavigationResponsePolicy) -> Void) {
        if isCorporateView(webView), let response = navigationResponse.response as? HTTPURLResponse {
            recordCorporateNavigation(.response, url: response.url,
                mainFrame: navigationResponse.isForMainFrame, code: response.statusCode)
        }
        if isCorporateView(webView), navigationResponse.isForMainFrame,
           let response = navigationResponse.response as? HTTPURLResponse, response.statusCode >= 400 {
            decisionHandler(.cancel)
            failCorporateNavigation("Корпоративный сервер входа вернул ошибку HTTP \(response.statusCode). Нажмите «Повторить».")
            return
        }
        decisionHandler(.allow)
    }
    func webView(_ webView: WKWebView, didFinish navigation: WKNavigation!) {
        if webView === panel { render(); return }
        if webView === legacyWorkspaceView { return }
        if isCorporateView(webView) {
            recordCorporateNavigation(.finished, url: webView.url)
            corporateNavigationTimer?.invalidate(); corporateNavigationTimer = nil
            setCorporateLoginMessage("Завершите вход на корпоративной странице. Если она пуста, нажмите «Повторить».")
        }
        guard isCorporateView(webView), webView.url?.host?.lowercased() == EmergencyTarget.host,
              accountActive,
              let ownerUserID = bridge.registration?.ownerUserID,
              activeAccountID == ownerUserID else { return }
        let run = corporateLoginRun
        webView.configuration.websiteDataStore.httpCookieStore.getAllCookies { [weak self] cookies in
            guard let self, self.corporateLoginRun == run, self.isCorporateView(webView), self.accountActive,
                  let ownerUserID = self.bridge.registration?.ownerUserID,
                  self.activeAccountID == ownerUserID else { return }
            let candidates = cookies.map { cookie in
                CorporateCookieCandidate(name: cookie.name, value: cookie.value, domain: cookie.domain,
                    path: cookie.path, secure: cookie.isSecure, expires: cookie.expiresDate)
            }
            guard let header = try? CorporateAuthPolicy.sessionCookie(from: candidates) else {
                self.failCorporateNavigation("Корпоративная сессия не подтверждена. Нажмите «Повторить».")
                return
            }
            Task { @MainActor [weak self] in
                guard let self else { return }
                let (status, login) = await self.verifyCorporateIdentity(authorization: header, run: run)
                guard self.corporateLoginRun == run, self.isCorporateView(webView) else { return }
                guard status == .ready, let login else { self.showCorporateFailure(status); return }
                do {
                    try self.bridge.storeVerifiedSession(header, login: login)
                    self.closeCorporatePopups()
                    self.loginWindow?.close(); self.loginWindow = nil; self.loginView = nil
                    self.corporateOAuthState = nil
                    self.corporateLoginMessage = nil; self.lastError = ""
                    if self.emergencyAuthorized { self.bridge.start() }
                    self.render()
                } catch { self.showCorporateFailure(.ownerMismatch) }
            }
        }
    }
    func webView(_ webView: WKWebView, didReceive challenge: URLAuthenticationChallenge,
                 completionHandler: @escaping (URLSession.AuthChallengeDisposition, URLCredential?) -> Void) {
        guard isCorporateView(webView),
              challenge.protectionSpace.authenticationMethod == NSURLAuthenticationMethodServerTrust,
              CorporateAuthPolicy.isCorporateBrowserHost(challenge.protectionSpace.host),
              let trust = challenge.protectionSpace.serverTrust,
              let url = Bundle.main.url(forResource: "YandexInternalRootCA", withExtension: "der"),
              let bytes = try? Data(contentsOf: url),
              let certificate = SecCertificateCreateWithData(nil, bytes as CFData) else {
            completionHandler(.performDefaultHandling, nil); return
        }
        SecTrustSetAnchorCertificates(trust, [certificate] as CFArray)
        SecTrustSetAnchorCertificatesOnly(trust, false)
        if SecTrustEvaluateWithError(trust, nil) { completionHandler(.useCredential, URLCredential(trust: trust)) }
        else { completionHandler(.cancelAuthenticationChallenge, nil) }
    }
    func webView(_ webView: WKWebView, decidePolicyFor navigationAction: WKNavigationAction,
                 decisionHandler: @escaping (WKNavigationActionPolicy) -> Void) {
        guard let url = navigationAction.request.url else { decisionHandler(.cancel); return }
        if webView === legacyWorkspaceView {
            if url.scheme == "http", url.host == "127.0.0.1", url.port == 18444,
               url.user == nil, url.password == nil { decisionHandler(.allow) }
            else {
                if navigationAction.navigationType == .linkActivated, url.scheme == "https",
                   url.user == nil, url.password == nil { NSWorkspace.shared.open(url) }
                decisionHandler(.cancel)
            }
        }
        else if webView === panel { decisionHandler(url.standardizedFileURL == panelURL?.standardizedFileURL ? .allow : .cancel) }
        else {
            if isCorporateView(webView) {
                recordCorporateNavigation(.requested, url: url, mainFrame: navigationAction.targetFrame?.isMainFrame != false)
            }
            if isCorporateView(webView), navigationAction.targetFrame?.isMainFrame != false, url.scheme == "https",
               url.host?.lowercased() == CorporateAuthPolicy.oauthHost,
               CorporateAuthPolicy.isOAuthCompletionPage(url),
               url.fragment?.contains("access_token=") == true {
                decisionHandler(.cancel)
                guard let state = corporateOAuthState else { showCorporateFailure(.reauthRequired); return }
                guard let credential = try? CorporateAuthPolicy.oauthCredential(from: url, expectedState: state) else {
                    showCorporateFailure(.reauthRequired); return
                }
                finishCorporateOAuth(credential, in: webView)
                return
            }
            let origin = navigationAction.sourceFrame.securityOrigin
            if isCorporateView(webView), let target = navigationAction.targetFrame,
               CorporateLoginPresentation.allowsBlankSubframe(url, isMainFrame: target.isMainFrame && !loginPopups.contains(where: { $0.view === webView }),
                    sourceProtocol: origin.protocol, sourceHost: origin.host, sourcePort: origin.port) {
                decisionHandler(.allow); return
            }
            let allowed = LoginPolicy.allows(url)
            if isCorporateView(webView) {
                recordCorporateNavigation(allowed ? .allowed : .blocked, url: url,
                    mainFrame: navigationAction.targetFrame?.isMainFrame != false)
            }
            if allowed, navigationAction.targetFrame?.isMainFrame != false {
                loginWindow?.title = "Подключение корпоративных сервисов — " + (url.host ?? "")
            }
            else {
                if !allowed, navigationAction.targetFrame?.isMainFrame != false {
                    failCorporateNavigation("Переход на непроверенный сервер входа заблокирован. Обратитесь к администратору.")
                }
            }
            decisionHandler(allowed ? .allow : .cancel)
        }
    }
    func webView(_ webView: WKWebView, createWebViewWith configuration: WKWebViewConfiguration,
                 for navigationAction: WKNavigationAction, windowFeatures: WKWindowFeatures) -> WKWebView? {
        let source = navigationAction.sourceFrame.securityOrigin
        guard isCorporateView(webView), navigationAction.targetFrame == nil,
              loginPopups.count < 3, let url = navigationAction.request.url,
              CorporateLoginPresentation.allowsPopup(url, sourceProtocol: source.protocol,
                  sourceHost: source.host, sourcePort: source.port) else { return nil }
        // Return a real child window: window.open() must retain its WindowProxy
        // so Passport can navigate it after the initial about:blank document.
        // WebKit supplies the parent process pool and ephemeral cookie store.
        let popup = WKWebView(frame: .zero, configuration: configuration)
        popup.navigationDelegate = self; popup.uiDelegate = self
        let win = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 900, height: 700),
            styleMask: [.titled, .closable, .resizable], backing: .buffered, defer: false)
        win.title = "Корпоративный вход"; win.contentView = popup
        win.delegate = self; win.isReleasedWhenClosed = false; win.center()
        loginPopups.append((popup, win)); win.makeKeyAndOrderFront(nil)
        return popup
    }
    func webView(_ webView: WKWebView, runOpenPanelWith parameters: WKOpenPanelParameters,
                 initiatedByFrame frame: WKFrameInfo, completionHandler: @escaping ([URL]?) -> Void) {
        guard webView === legacyWorkspaceView, frame.securityOrigin.host == "127.0.0.1",
              frame.securityOrigin.port == 18444 else { completionHandler(nil); return }
        let chooser = NSOpenPanel()
        chooser.canChooseDirectories = false; chooser.canChooseFiles = true
        chooser.allowsMultipleSelection = parameters.allowsMultipleSelection
        chooser.begin { response in completionHandler(response == .OK ? chooser.urls : nil) }
    }
    func webView(_ webView: WKWebView, requestMediaCapturePermissionFor origin: WKSecurityOrigin,
                 initiatedByFrame frame: WKFrameInfo, type: WKMediaCaptureType,
                 decisionHandler: @escaping (WKPermissionDecision) -> Void) {
        guard webView === legacyWorkspaceView, origin.host == "127.0.0.1", origin.port == 18444,
              frame.securityOrigin.host == origin.host, frame.securityOrigin.port == origin.port else {
            decisionHandler(.deny); return
        }
        decisionHandler(.prompt)
    }
}
