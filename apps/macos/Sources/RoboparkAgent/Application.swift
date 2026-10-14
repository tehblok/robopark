import AppKit
import WebKit
import CryptoKit
import Security
import AgentCore

@MainActor final class AgentApplication: NSObject, NSApplicationDelegate, WKScriptMessageHandler, WKNavigationDelegate, WKUIDelegate {
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
    var updateTimer: Timer?
    var updateStatusTimer: Timer?
    var probeTimer: Timer?
    var workspace: WorkspaceServer?
    var workspaceWindow: NSWindow?
    var workspaceView: WKWebView?
    private var enrollmentTask: Task<Void, Never>?
    private var probeRefreshTask: Task<Void, Never>?
    private var identityRestoreTask: Task<Void, Never>?
    private var enrollmentRun = UUID()
    private var accountActive = false
    private var emergencyAuthorized = false
    private var activeAccountID: Int?
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
        catch { lastError = "Не удалось открыть защищённое хранилище рабочего интерфейса. Разблокируйте Keychain." }
        if UserDefaults.standard.bool(forKey: accountPresentKey) { accountLabel = "Проверка аккаунта…" }
        let menu = NSMenu()
        menu.addItem(withTitle: "Открыть Robopark", action: #selector(showWorkspace), keyEquivalent: "")
        menu.addItem(withTitle: "Служба и подключение", action: #selector(showPanel), keyEquivalent: "")
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
        compute.onChange = { [weak self] in self?.render() }
        updater.onChange = { [weak self] in self?.render() }
        notifications.onChange = { [weak self] in self?.render() }
        NSWorkspace.shared.notificationCenter.addObserver(self, selector: #selector(wake), name: NSWorkspace.didWakeNotification, object: nil)
        NSWorkspace.shared.notificationCenter.addObserver(self, selector: #selector(sleep), name: NSWorkspace.willSleepNotification, object: nil)
        DistributedNotificationCenter.default().addObserver(self, selector: #selector(showPanel), name: Notification.Name("ru.robopark.agent.open"), object: nil)
        if !CommandLine.arguments.contains("--background") || bridge.registration == nil {
            if workspace?.session.profile != nil { showWorkspace() } else { showPanel() }
        }
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
                if self.accountActive { self.refreshProbeTarget() }
                else { self.restoreAccountSession() }
            }
        }
    }
    private func initializeWorkspace() throws {
        guard workspace == nil else { return }
        let server = try WorkspaceServer()
        if let registration = bridge.registration { try server.session.useRegistration(registration) }
        server.session.onIdentity = { [weak self] user in self?.accountVerified(user) }
        server.session.onLogout = { [weak self] in await self?.accountLogout() }
        workspace = server
    }
    func applicationShouldHandleReopen(_ sender: NSApplication, hasVisibleWindows flag: Bool) -> Bool { showWorkspace(); return true }
    @objc func showWorkspace() { presentWorkspace(route: nil) }
    func showNotificationCenter() { presentWorkspace(route: "/schedule#notifications") }
    private func presentWorkspace(route: String?) {
        guard let workspace, workspace.session.profile != nil else { showPanel(); return }
        Task {
            do {
                try await workspace.start()
                let target = route.flatMap { URL(string: $0, relativeTo: workspace.initialURL)?.absoluteURL } ?? workspace.initialURL
                if workspaceWindow == nil {
                    let config = WKWebViewConfiguration()
                    config.websiteDataStore = .default()
                    let view = WKWebView(frame: .zero, configuration: config)
                    view.navigationDelegate = self
                    view.uiDelegate = self
                    let win = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 1240, height: 850),
                        styleMask: [.titled, .closable, .miniaturizable, .resizable], backing: .buffered, defer: false)
                    win.title = "Robopark"; win.contentView = view; win.isReleasedWhenClosed = false; win.center()
                    workspaceView = view; workspaceWindow = win
                    await withCheckedContinuation { continuation in
                        view.configuration.websiteDataStore.httpCookieStore.setCookie(workspace.sessionCookie) { continuation.resume() }
                    }
                    if workspaceView === view { view.load(URLRequest(url: target)) }
                } else if route != nil {
                    workspaceView?.load(URLRequest(url: target))
                }
                workspaceWindow?.makeKeyAndOrderFront(nil); NSApp.activate(ignoringOtherApps: true)
            } catch {
                lastError = "Не удалось запустить локальный интерфейс. Порт 18444 должен быть свободен; повторите запуск службы."
                showPanel()
            }
        }
    }
    private func clearWorkspaceData() async {
        workspaceWindow?.close(); workspaceWindow = nil; workspaceView = nil
        await withCheckedContinuation { continuation in
            WKWebsiteDataStore.default().removeData(ofTypes: WKWebsiteDataStore.allWebsiteDataTypes(), modifiedSince: .distantPast) { continuation.resume() }
        }
    }
    private func restoreAccountSession() {
        guard identityRestoreTask == nil, let session = workspace?.session else { return }
        identityRestoreTask = Task { [weak self] in
            _ = await session.restoreIdentity()
            self?.identityRestoreTask = nil
        }
    }
    private func accountVerified(_ user: [String: Any]) {
        guard enrollmentTask == nil, let session = workspace?.session,
              let profile = session.profile, let userID = user["id"] as? Int,
              TestDeploymentAssignment.matches(origin: profile.origin, instanceID: profile.instanceID,
                                               environment: profile.environment) else { return }
        accountLabel = (user["display_name"] as? String) ?? (user["username"] as? String) ?? "Аккаунт Robopark"
        guard user["access_status"] as? String == "approved", user["must_change_password"] as? Bool != true else {
            accountActive = false
            emergencyAuthorized = false
            activeAccountID = nil
            UserDefaults.standard.set(false, forKey: accountPresentKey)
            bridge.stop()
            compute.stop()
            notifications.stop(removeDelivered: true)
            return
        }
        let canUseEmergency = (user["permissions"] as? [String] ?? []).contains("nav.emergency")
        if accountActive, activeAccountID == userID,
           bridge.registration?.origin == profile.origin,
           bridge.registration?.instanceID == profile.instanceID {
            if emergencyAuthorized != canUseEmergency {
                emergencyAuthorized = canUseEmergency
                if canUseEmergency { bridge.start(); refreshProbeTarget() }
                else { bridge.stop(); try? bridge.clearProbe() }
            }
            notifications.start(accountID: userID, role: user["role"] as? String ?? "", session: session)
            render()
            return
        }
        let run = UUID(); enrollmentRun = run
        enrollmentTask = Task { [weak self] in
            guard let self else { return }
            defer { if self.enrollmentRun == run { self.enrollmentTask = nil; self.render() } }
            do {
                if !canUseEmergency { self.bridge.stop() }
                if let existing = self.bridge.registration,
                   existing.instanceID != profile.instanceID || existing.origin != profile.origin {
                    try self.bridge.disconnect()
                }
                if !self.accountActive || self.activeAccountID != userID { try self.bridge.clearProbe() }
                let label = String((Host.current().localizedName ?? "Mac").prefix(80))
                try await self.bridge.claimAuthenticated(origin: profile.origin, label: label, startEmergency: canUseEmergency) { object in
                    try await session.call(path: "/api/agent-devices/claim-auth", method: "POST", object: object)
                }
                self.accountActive = true
                self.emergencyAuthorized = canUseEmergency
                self.activeAccountID = userID
                UserDefaults.standard.set(true, forKey: self.accountPresentKey)
                if canUseEmergency { self.refreshProbeTarget() }
                self.compute.start()
                self.notifications.start(accountID: userID, role: user["role"] as? String ?? "", session: session)
                await self.updater.check()
            } catch is CancellationError { }
            catch {
                if self.enrollmentRun == run {
                    self.accountActive = false
                    self.bridge.stop()
                    self.compute.stop()
                    self.notifications.stop(removeDelivered: false)
                    self.lastError = "Рабочий интерфейс доступен. Этот ключ Mac не закреплён за текущим аккаунтом или сервер недоступен."
                }
            }
        }
    }
    private func accountLogout() async {
        accountLabel = "Не выполнен вход"
        accountActive = false
        emergencyAuthorized = false
        activeAccountID = nil
        UserDefaults.standard.set(false, forKey: accountPresentKey)
        probeRefreshTask?.cancel(); probeRefreshTask = nil
        identityRestoreTask?.cancel(); identityRestoreTask = nil
        enrollmentRun = UUID(); enrollmentTask?.cancel(); enrollmentTask = nil
        // Signing out stops work but preserves the device key and owner binding.
        // A different account cannot claim that key; the original owner can
        // resume without creating a second Mac entry.
        bridge.stop()
        compute.stop()
        notifications.stop(removeDelivered: true)
        updater.reset()
    }
    private func refreshProbeTarget() {
        guard accountActive, emergencyAuthorized, probeRefreshTask == nil, let session = workspace?.session,
              bridge.registration != nil else { return }
        probeRefreshTask = Task { [weak self] in
            guard let self else { return }
            defer { self.probeRefreshTask = nil }
            guard let (data, response) = try? await session.call(path: "/api/agent-devices/probe-target", timeout: 5),
                  response.statusCode == 200,
                  let object = try? JSONSerialization.jsonObject(with: data) as? [String: Any] else { return }
            guard !Task.isCancelled, self.accountActive else { return }
            if let vin = object["vin"] as? String, !vin.isEmpty { try? self.bridge.setProbe(vin) }
            else { try? self.bridge.clearProbe() }
        }
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
    private func restoreAfterFailedUninstall(workspaceWasVisible: Bool) {
        if accountActive {
            if emergencyAuthorized { bridge.start(); refreshProbeTarget() }
            compute.start()
            notifications.resume()
        } else {
            restoreAccountSession()
        }
        guard workspaceWasVisible, let workspace else { return }
        Task {
            do {
                try await workspace.start()
                workspaceView?.load(URLRequest(url: workspace.initialURL))
                workspaceWindow?.makeKeyAndOrderFront(nil)
            } catch {
                lastError = "Удаление отменено; не удалось перезапустить локальный интерфейс. Перезапустите Robopark."
                render()
            }
        }
    }
    @objc func uninstall() {
        let alert = NSAlert()
        alert.messageText = "Удалить Robopark?"
        alert.informativeText = "Можно сохранить аккаунт и офлайн-данные для повторной установки. Полное удаление также удалит незавершённые офлайн-черновики."
        alert.addButton(withTitle: "Отмена")
        alert.addButton(withTitle: "Удалить, сохранив данные")
        alert.addButton(withTitle: "Удалить все данные")
        let choice = alert.runModal()
        guard choice == .alertSecondButtonReturn || choice == .alertThirdButtonReturn else { return }
        let removeData = choice == .alertThirdButtonReturn
        if removeData {
            let confirm = NSAlert()
            confirm.messageText = "Удалить аккаунт и офлайн-данные?"
            confirm.informativeText = "Это действие удалит локальный вход, очередь синхронизации и незавершённые офлайн-черновики."
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
        let workspaceWasVisible = workspaceWindow?.isVisible == true
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
                    restoreAfterFailedUninstall(workspaceWasVisible: workspaceWasVisible); render()
                }
            } catch {
                lastError = "Не удалось открыть системный запрос удаления."
                restoreAfterFailedUninstall(workspaceWasVisible: workspaceWasVisible); render()
            }
        }
    }
    func render() {
        let profile = bridge.registration
        let object: [String: Any] = [
            "registered": profile != nil, "environment": profile?.environment ?? "",
            "label": profile?.label ?? "", "state": bridge.state, "upstream": bridge.upstreamState,
            "needsCorporateLogin": bridge.needsCorporateLogin,
            "version": updater.version, "updates": updater.message,
            "paused": profile?.paused ?? false, "completed": bridge.completed,
            "emergencyAuthorized": emergencyAuthorized,
            "computeState": compute.state, "computeActive": compute.active,
            "computeCompleted": compute.completed, "computeInputBytes": compute.inputBytes,
            "computeOutputBytes": compute.outputBytes,
            "notificationPermission": notifications.permission, "notificationCount": notifications.unreadCount,
            "account": accountLabel, "autostart": autostart.isEnabled(),
            "error": lastError, "busy": working || updater.busy
        ]
        guard let bytes = try? JSONSerialization.data(withJSONObject: object), let json = String(data: bytes, encoding: .utf8) else { return }
        panel?.evaluateJavaScript("window.render(\(json))", completionHandler: nil)
    }
    func userContentController(_ userContentController: WKUserContentController, didReceive message: WKScriptMessage) {
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
                        await accountLogout()
                        await clearWorkspaceData()
                        try workspace.session.clear()
                    }
                    try workspace.session.configure(origin: origin, info: info)
                    showWorkspace()
                case "emergencyLogin": try bridge.requireTrust(); openEmergencyLogin()
                case "disconnect":
                    let alert = NSAlert()
                    alert.messageText = "Отключить этот Mac?"
                    alert.informativeText = "Ключ устройства и корпоративная сессия будут удалены с Mac. В браузере отзовите устройство в списке. После отключения можно подключить другой сервер."
                    alert.addButton(withTitle: "Отмена"); alert.addButton(withTitle: "Отключить")
                    if alert.runModal() == .alertSecondButtonReturn {
                        if let origin = bridge.registration?.origin { NSWorkspace.shared.open(try ServerOrigin(origin).api("agent-devices/setup")) }
                        await accountLogout()
                        try workspace?.session.clear()
                        await clearWorkspaceData()
                        try bridge.disconnect(); loginWindow?.close(); loginWindow = nil; loginView = nil
                        updater.reset()
                    }
                case "pause": try bridge.pause(!(bridge.registration?.paused ?? false))
                case "autostart":
                    try autostart.setEnabled(body["enabled"] as? Bool ?? true)
                case "open":
                    showWorkspace()
                case "openNotifications":
                    showNotificationCenter()
                case "notificationPermission":
                    await notifications.requestPermission()
                case "reloadKeychain":
                    try bridge.reloadRegistration()
                    try initializeWorkspace()
                    if accountActive {
                        if emergencyAuthorized { bridge.start() }
                        compute.start()
                    }
                case "check": await updater.check(manual: true)
                case "uninstall": uninstall()
                case "ready": break
                default: throw AgentError.invalidJob
                }
            } catch {
                lastError = (error as? AgentError)?.localizedDescription ?? "Операция не завершилась. Проверьте адрес, код регистрации, сеть и Keychain."
            }
        }
    }
    func openEmergencyLogin() {
        guard bridge.registration != nil else { return }
        let config = WKWebViewConfiguration()
        config.websiteDataStore = .nonPersistent()
        let view = WKWebView(frame: .zero, configuration: config)
        view.navigationDelegate = self
        let win = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 1000, height: 740),
                           styleMask: [.titled, .closable, .resizable], backing: .buffered, defer: false)
        win.title = "Вход в Emergency — корпоративная сессия этого Mac"
        win.contentView = view; win.isReleasedWhenClosed = false; win.center()
        loginWindow = win; loginView = view
        view.load(URLRequest(url: URL(string: "https://\(EmergencyTarget.host)/")!))
        win.makeKeyAndOrderFront(nil)
    }
    func webView(_ webView: WKWebView, didFinish navigation: WKNavigation!) {
        if webView === panel { render(); return }
        if webView === workspaceView { return }
        guard webView === loginView, webView.url?.host == EmergencyTarget.host else { return }
        webView.configuration.websiteDataStore.httpCookieStore.getAllCookies { [weak self] cookies in
            let selected = cookies.filter { cookie in
                let domain = cookie.domain.hasPrefix(".") ? String(cookie.domain.dropFirst()) : cookie.domain
                return cookie.isSecure && (EmergencyTarget.host == domain || EmergencyTarget.host.hasSuffix("." + domain))
                    && (cookie.expiresDate == nil || cookie.expiresDate! > Date())
            }
            guard !selected.isEmpty else { return }
            let header = HTTPCookie.requestHeaderFields(with: selected)["Cookie"] ?? ""
            do { try self?.bridge.updateCookie(header) }
            catch { self?.lastError = "Не удалось сохранить корпоративную сессию в Keychain." }
            self?.render()
        }
    }
    func webView(_ webView: WKWebView, didReceive challenge: URLAuthenticationChallenge,
                 completionHandler: @escaping (URLSession.AuthChallengeDisposition, URLCredential?) -> Void) {
        guard webView === loginView,
              challenge.protectionSpace.authenticationMethod == NSURLAuthenticationMethodServerTrust,
              challenge.protectionSpace.host == EmergencyTarget.host,
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
        if webView === workspaceView {
            if url.scheme == "http", url.host == "127.0.0.1", url.port == 18444, url.user == nil, url.password == nil { decisionHandler(.allow) }
            else {
                if navigationAction.navigationType == .linkActivated, url.scheme == "https", url.user == nil, url.password == nil { NSWorkspace.shared.open(url) }
                decisionHandler(.cancel)
            }
        }
        else if webView === panel { decisionHandler(url.standardizedFileURL == panelURL?.standardizedFileURL ? .allow : .cancel) }
        else {
            let allowed = LoginPolicy.allows(url)
            if allowed { loginWindow?.title = "Вход в Emergency — " + (url.host ?? "") }
            else {
                lastError = "Переход на непроверенный сервер входа заблокирован: " + (url.host ?? "неизвестный адрес")
                render()
            }
            decisionHandler(allowed ? .allow : .cancel)
        }
    }
    func webView(_ webView: WKWebView, runOpenPanelWith parameters: WKOpenPanelParameters,
                 initiatedByFrame frame: WKFrameInfo, completionHandler: @escaping ([URL]?) -> Void) {
        guard webView === workspaceView, frame.securityOrigin.host == "127.0.0.1", frame.securityOrigin.port == 18444 else { completionHandler(nil); return }
        let chooser = NSOpenPanel()
        chooser.canChooseDirectories = false; chooser.canChooseFiles = true
        chooser.allowsMultipleSelection = parameters.allowsMultipleSelection
        chooser.begin { response in completionHandler(response == .OK ? chooser.urls : nil) }
    }
    func webView(_ webView: WKWebView, requestMediaCapturePermissionFor origin: WKSecurityOrigin,
                 initiatedByFrame frame: WKFrameInfo, type: WKMediaCaptureType,
                 decisionHandler: @escaping (WKPermissionDecision) -> Void) {
        guard webView === workspaceView, origin.host == "127.0.0.1", origin.port == 18444,
              frame.securityOrigin.host == origin.host, frame.securityOrigin.port == origin.port else { decisionHandler(.deny); return }
        decisionHandler(.prompt)
    }
}
