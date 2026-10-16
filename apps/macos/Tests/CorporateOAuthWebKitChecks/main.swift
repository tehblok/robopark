import AppKit
import WebKit
import AgentCore

// A hermetic provider-dialog fixture. No corporate requests, live credentials,
// user browser sessions or credential stores are accessed by this executable.
final class Harness: NSObject, WKScriptMessageHandler {
    var view: WKWebView!
    let token = "y1__" + String(repeating: "fixture", count: 10)
    let state = String(repeating: "fixture", count: 8)

    func start() throws {
        guard CommandLine.arguments.count == 2 else { fatalError("Expected corporate-oauth.js path") }
        let source = try String(contentsOfFile: CommandLine.arguments[1], encoding: .utf8)
            .replacingOccurrences(of: "__ROBOPARK_LOGIN_RUN__", with: "fixture-run")
        let config = WKWebViewConfiguration()
        config.websiteDataStore = .nonPersistent()
        config.userContentController.add(self, contentWorld: .defaultClient, name: "corporateOAuthCompletion")
        config.userContentController.addUserScript(WKUserScript(source: source,
            injectionTime: .atDocumentEnd, forMainFrameOnly: true, in: .defaultClient))
        // Simulate the provider clearing the query immediately after completion.
        // The native receiver must use the URL captured with the credential.
        config.userContentController.addUserScript(WKUserScript(source: "history.replaceState(null, '', '/authorize');",
            injectionTime: .atDocumentEnd, forMainFrameOnly: true, in: .defaultClient))
        view = WKWebView(frame: CGRect(x: 0, y: 0, width: 800, height: 600), configuration: config)
        let html = "<html><body><dialog open><input readonly value='\(token)'><button>Скопировать</button></dialog></body></html>"
        view.loadHTMLString(html, baseURL: try CorporateAuthPolicy.authorizationURL(state: state))
        DispatchQueue.main.asyncAfter(deadline: .now() + 8) {
            print("FAIL: no isolated-world completion on authorize page"); exit(1)
        }
    }

    func userContentController(_ controller: WKUserContentController, didReceive message: WKScriptMessage) {
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.1) { self.verify(message) }
    }

    func verify(_ message: WKScriptMessage) {
        guard message.webView === view, message.frameInfo.isMainFrame,
              message.frameInfo.securityOrigin.protocol == "https",
              message.frameInfo.securityOrigin.host == CorporateAuthPolicy.oauthHost,
              [0, 443].contains(message.frameInfo.securityOrigin.port),
              let current = view.url, CorporateAuthPolicy.isOAuthCompletionPage(current), current.query == nil,
              let body = message.body as? [String: Any], body["run"] as? String == "fixture-run",
              body["kind"] as? String == "displayed", let candidate = body["token"] as? String,
              let source = body["url"] as? String, let page = URL(string: source),
              let credential = try? CorporateAuthPolicy.displayedOAuthCredential(
                token: candidate, pageURL: page, expectedState: state),
              credential.token == token, credential.expiresAt == nil else {
            print("FAIL: native completion metadata or credential policy"); exit(1)
        }
        print("PASS: WebKit authorize dialog → isolated client world → native policy across history.replaceState")
        exit(0)
    }
}

let app = NSApplication.shared
app.setActivationPolicy(.prohibited)
let harness = Harness()
try harness.start()
app.run()
