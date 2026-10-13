import AppKit
import WebKit
import CryptoKit
import Security
import AgentCore

@MainActor final class AgentApplication: NSObject, NSApplicationDelegate, WKScriptMessageHandler, WKNavigationDelegate {
    let bridge = Bridge()
    lazy var updater = Updater(bridge: bridge)
    var statusItem: NSStatusItem?
    var window: NSWindow?
    var panel: WKWebView?
    var loginWindow: NSWindow?
    var loginView: WKWebView?
    var updateTimer: Timer?
    var lastError = ""
    var working = false
    var panelURL: URL? { Bundle.main.url(forResource: "panel", withExtension: "html") }

    func applicationDidFinishLaunching(_ notification: Notification) {
        NSApp.setActivationPolicy(.accessory)
        statusItem = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)
        statusItem?.button?.title = "RP"
        let menu = NSMenu()
        menu.addItem(withTitle: "Открыть Robopark", action: #selector(showPanel), keyEquivalent: "")
        menu.addItem(withTitle: "Проверить обновления", action: #selector(checkUpdate), keyEquivalent: "")
        menu.addItem(.separator())
        menu.addItem(withTitle: "Завершить до следующего входа", action: #selector(quit), keyEquivalent: "q")
        menu.items.forEach { $0.target = self }; statusItem?.menu = menu
        bridge.onChange = { [weak self] in self?.render() }
        updater.onChange = { [weak self] in self?.render() }
        updater.onOffer = { [weak self] release in self?.offer(release) }
        NSWorkspace.shared.notificationCenter.addObserver(self, selector: #selector(wake), name: NSWorkspace.didWakeNotification, object: nil)
        DistributedNotificationCenter.default().addObserver(self, selector: #selector(showPanel), name: Notification.Name("ru.robopark.agent.open"), object: nil)
        bridge.start()
        if !CommandLine.arguments.contains("--background") || bridge.registration == nil { showPanel() }
        Task { await updater.check() }
        updateTimer = Timer.scheduledTimer(withTimeInterval: 4 * 60 * 60, repeats: true) { [weak self] _ in
            Task { @MainActor in await self?.updater.check() }
        }
    }
    func applicationShouldHandleReopen(_ sender: NSApplication, hasVisibleWindows flag: Bool) -> Bool { showPanel(); return true }
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
    @objc func quit() { bridge.stop(); NSApp.terminate(nil) }
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
                        try bridge.disconnect(); loginWindow?.close(); loginWindow = nil; loginView = nil
                        updater.reset()
                    }
                case "saveProbe": try bridge.setProbe(body["probe"] as? String ?? "")
                case "pause": try bridge.pause(!(bridge.registration?.paused ?? false))
                case "open":
                    if let origin = bridge.registration?.origin { NSWorkspace.shared.open(try ServerOrigin(origin).url) }
                case "reloadKeychain": try bridge.reloadRegistration()
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
        if webView === panel { decisionHandler(url.standardizedFileURL == panelURL?.standardizedFileURL ? .allow : .cancel) }
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
}
