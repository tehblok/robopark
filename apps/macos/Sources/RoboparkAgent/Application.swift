import AppKit
import WebKit
import CryptoKit
import Security
import AgentCore

@MainActor final class AgentApplication: NSObject, NSApplicationDelegate, WKScriptMessageHandler, WKNavigationDelegate, WKUIDelegate {
    let bridge = Bridge()
    lazy var updater = Updater(bridge: bridge)
    var statusItem: NSStatusItem?
    var window: NSWindow?
    var panel: WKWebView?
    var loginWindow: NSWindow?
    var loginView: WKWebView?
    var updateTimer: Timer?
    var workspace: WorkspaceServer?
    var workspaceWindow: NSWindow?
    var workspaceView: WKWebView?
    private var enrollmentTask: Task<Void, Never>?
    private var enrollmentRun = UUID()
    var lastError = ""
    var working = false
    var panelURL: URL? { Bundle.main.url(forResource: "panel", withExtension: "html") }

    func applicationDidFinishLaunching(_ notification: Notification) {
        NSApp.setActivationPolicy(.accessory)
        statusItem = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)
        statusItem?.button?.title = "RP"
        do { try initializeWorkspace() }
        catch { lastError = "Не удалось открыть защищённое хранилище рабочего интерфейса. Разблокируйте Keychain." }
        let menu = NSMenu()
        menu.addItem(withTitle: "Открыть Robopark", action: #selector(showWorkspace), keyEquivalent: "")
        menu.addItem(withTitle: "Служба и подключение", action: #selector(showPanel), keyEquivalent: "")
        menu.addItem(withTitle: "Проверить обновления", action: #selector(checkUpdate), keyEquivalent: "")
        menu.addItem(.separator())
        menu.addItem(withTitle: "Завершить до следующего входа", action: #selector(quit), keyEquivalent: "q")
        menu.items.forEach { $0.target = self }; statusItem?.menu = menu
        bridge.onChange = { [weak self] in
            guard let self else { return }
            if let registration = self.bridge.registration { try? self.workspace?.session.useRegistration(registration) }
            self.render()
        }
        updater.onChange = { [weak self] in self?.render() }
        updater.onOffer = { [weak self] release in self?.offer(release) }
        NSWorkspace.shared.notificationCenter.addObserver(self, selector: #selector(wake), name: NSWorkspace.didWakeNotification, object: nil)
        DistributedNotificationCenter.default().addObserver(self, selector: #selector(showPanel), name: Notification.Name("ru.robopark.agent.open"), object: nil)
        bridge.start()
        if !CommandLine.arguments.contains("--background") || bridge.registration == nil {
            if workspace?.session.profile != nil { showWorkspace() } else { showPanel() }
        }
        Task { await updater.check() }
        updateTimer = Timer.scheduledTimer(withTimeInterval: 4 * 60 * 60, repeats: true) { [weak self] _ in
            Task { @MainActor in await self?.updater.check() }
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
    @objc func showWorkspace() {
        guard let workspace, workspace.session.profile != nil else { showPanel(); return }
        Task {
            do {
                try await workspace.start()
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
                    if workspaceView === view { view.load(URLRequest(url: workspace.initialURL)) }
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
    private func accountVerified(_ user: [String: Any]) {
        guard enrollmentTask == nil, let session = workspace?.session,
              let profile = session.profile, let userID = user["id"] as? Int else { return }
        guard user["access_status"] as? String == "approved", user["must_change_password"] as? Bool != true,
              (user["permissions"] as? [String] ?? []).contains("nav.emergency") else {
            bridge.stop()
            return
        }
        let run = UUID(); enrollmentRun = run
        enrollmentTask = Task { [weak self] in
            guard let self else { return }
            defer { if self.enrollmentRun == run { self.enrollmentTask = nil; self.render() } }
            do {
                let (data, response) = try await session.call(path: "/api/agent-devices")
                try Task.checkCancellation()
                guard response.statusCode == 200 else { throw NetworkFailure.auth }
                let raw = try JSONSerialization.jsonObject(with: data)
                let rows = (raw as? [[String: Any]]) ?? ((raw as? [String: Any])?["devices"] as? [[String: Any]]) ?? []
                let ownIDs = Set(rows.filter { ($0["owner_user_id"] as? Int ?? userID) == userID }
                    .compactMap { $0["device_id"] as? String ?? $0["id"] as? String })
                // Offline logout removes the local key immediately. Finish the
                // server revocation with the next authenticated owner session
                // before spending another enrollment slot.
                for id in SecureStore.pendingRevocations(origin: profile.origin, instanceID: profile.instanceID).intersection(ownIDs) {
                    let (_, revoked) = try await session.call(path: "/api/agent-devices/\(id)", method: "DELETE", timeout: 5)
                    try Task.checkCancellation()
                    guard [204, 404].contains(revoked.statusCode) else { throw NetworkFailure.auth }
                    SecureStore.forgetRevocation(id, origin: profile.origin, instanceID: profile.instanceID)
                }
                if let existing = self.bridge.registration {
                    let own = rows.contains { ($0["device_id"] as? String ?? $0["id"] as? String) == existing.deviceID && ($0["owner_user_id"] as? Int ?? userID) == userID && $0["revoked_at"] is NSNull }
                    if existing.instanceID == profile.instanceID && existing.origin == profile.origin && own {
                        self.bridge.startIfNeeded()
                        return
                    }
                    try self.bridge.disconnect()
                }
                let label = String((Host.current().localizedName ?? "Mac").prefix(80))
                let (enrollmentData, enrollmentResponse) = try await session.call(path: "/api/agent-devices/enrollments", method: "POST", object: ["label": label])
                guard enrollmentResponse.statusCode == 201,
                      let value = try JSONSerialization.jsonObject(with: enrollmentData) as? [String: Any],
                      let code = value["code"] as? String else { throw NetworkFailure.auth }
                try Task.checkCancellation()
                guard self.enrollmentRun == run, session.profile?.instanceID == profile.instanceID else { throw CancellationError() }
                try await self.bridge.enroll(origin: profile.origin, code: code, label: label)
                await self.updater.check()
            } catch is CancellationError { }
            catch { if self.enrollmentRun == run { self.lastError = "Рабочий интерфейс доступен. Автоподключение к пулу не завершилось — проверьте состояние службы или повторите вход." } }
        }
    }
    private func accountLogout() async {
        enrollmentRun = UUID(); enrollmentTask?.cancel(); enrollmentTask = nil
        let profile = bridge.registration
        if let profile { SecureStore.queueRevocation(profile) }
        try? bridge.disconnect()
        if let profile, let session = workspace?.session {
            if let (_, response) = try? await session.call(path: "/api/agent-devices/\(profile.deviceID)", method: "DELETE", timeout: 5), [204, 404].contains(response.statusCode) {
                SecureStore.forgetRevocation(profile.deviceID, origin: profile.origin, instanceID: profile.instanceID)
            }
        }
        updater.reset()
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
    @objc func wake() { bridge.start(); Task { await updater.check() } }
    @objc func quit() { enrollmentTask?.cancel(); workspace?.stop(); bridge.stop(); NSApp.terminate(nil) }
    func offer(_ target: ReleaseTarget) {
        let alert = NSAlert()
        let size = ByteCountFormatter.string(fromByteCount: Int64(target.length), countStyle: .file)
        alert.messageText = "Доступно обновление Robopark"
        alert.informativeText = "\(updater.version) → \(target.version)\n\(target.notes)\nРазмер: \(size)"
        alert.addButton(withTitle: "Обновить"); alert.addButton(withTitle: "Позже")
        if alert.runModal() == .alertFirstButtonReturn { Task { await updater.install() } }
        else { updater.deferUpdate() }
    }
    func render() {
        let profile = bridge.registration
        let object: [String: Any] = [
            "registered": profile != nil, "environment": profile?.environment ?? "",
            "label": profile?.label ?? "", "state": bridge.state, "upstream": bridge.upstreamState,
            "version": updater.version, "updates": updater.message, "canUpdate": updater.available != nil,
            "paused": profile?.paused ?? false, "completed": bridge.completed,
            "probe": profile?.probeVIN ?? "", "error": lastError, "busy": working || updater.busy
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
                    let origin = try ServerOrigin(body["origin"] as? String ?? "")
                    let (info, _) = try await bridge.verifiedInfo(origin)
                    guard let workspace else { throw NetworkFailure.invalid }
                    if let old = workspace.session.profile, old.origin != origin.url.absoluteString || old.instanceID != info.instance_id {
                        await accountLogout()
                        await clearWorkspaceData()
                        try workspace.session.clear()
                    }
                    try workspace.session.configure(origin: origin, info: info)
                    showWorkspace()
                case "enroll":
                    try await bridge.enroll(origin: body["origin"] as? String ?? "", code: body["code"] as? String ?? "",
                                            label: body["label"] as? String ?? "Mac")
                    await updater.check()
                case "setup":
                    let origin = try ServerOrigin(body["origin"] as? String ?? "")
                    _ = try await bridge.verifiedInfo(origin)
                    NSWorkspace.shared.open(origin.api("agent-devices/setup"))
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
                case "saveProbe": try bridge.setProbe(body["probe"] as? String ?? "")
                case "pause": try bridge.pause(!(bridge.registration?.paused ?? false))
                case "open":
                    showWorkspace()
                case "reloadKeychain":
                    try bridge.reloadRegistration()
                    try initializeWorkspace()
                case "check": await updater.check(manual: true)
                case "update": await updater.install()
                case "later": updater.deferUpdate()
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
