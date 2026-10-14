import Foundation
import AppKit
import WebKit
import CryptoKit
import AgentCore

@MainActor public final class ComputePeerHost: NSObject, WKScriptMessageHandler, WKNavigationDelegate {
    private struct Lease {
        let generation: String
        let jobID: String
        let attemptID: String
        let operation: String
        let digest: String
        let payloadBytes: Int
        let timeoutMilliseconds: Int
        let token: String
        let started: Date
    }

    private let workerURL: URL
    private let peerDocument: Data?
    private let outbound: ([String: Any]) async -> Void
    private let capability = UUID().uuidString.replacingOccurrences(of: "-", with: "") + UUID().uuidString.replacingOccurrences(of: "-", with: "")
    private var server: LoopbackHTTPServer?
    private var window: NSWindow?
    private var webView: WKWebView?
    private var ready = false
    private var pageServed = false
    private var loadFailed = false
    private var loadFailure = ""
    private var lease: Lease?
    private var runner: ComputeWorkerProcess?
    private var executingAttemptID: String?
    private var pendingResult: (attemptID: String, input: Int, output: Int, ok: Bool)?
    private var leaseTimeout: Task<Void, Never>?

    private func tokenBytes(_ value: String) -> Data? {
        var normalized = value.replacingOccurrences(of: "-", with: "+").replacingOccurrences(of: "_", with: "/")
        normalized += String(repeating: "=", count: (4 - normalized.count % 4) % 4)
        return Data(base64Encoded: normalized)
    }

    public init(workerURL: URL, peerDocument: Data? = nil,
                outbound: @escaping ([String: Any]) async -> Void) {
        self.workerURL = workerURL; self.peerDocument = peerDocument; self.outbound = outbound
    }

    public func startIfNeeded() async throws {
        if server == nil {
            let local = LoopbackHTTPServer(port: 0) { [weak self] request in
                guard let self else { return .json(["detail": "stopped"], status: 503) }
                return await self.handle(request)
            }
            let port = try await local.start(); server = local
            let controller = WKUserContentController(); controller.add(self, name: "peer")
            let configuration = WKWebViewConfiguration(); configuration.userContentController = controller
            configuration.websiteDataStore = .nonPersistent(); configuration.mediaTypesRequiringUserActionForPlayback = .all
            let view = WKWebView(frame: .zero, configuration: configuration); view.navigationDelegate = self; webView = view
            let hidden = NSWindow(contentRect: NSRect(x: -10_000, y: -10_000, width: 1, height: 1),
                                  styleMask: [.borderless], backing: .buffered, defer: false)
            hidden.contentView = view; hidden.orderOut(nil); window = hidden
            let peerURL = URL(string: "http://127.0.0.1:\(port)/\(capability)/peer")!
            // Overrides are served by the same HTTP route as the bundled page,
            // preserving its real origin and WebRTC/network policy in checks.
            view.load(URLRequest(url: peerURL))
        }
        for _ in 0..<100 {
            if ready { return }
            if loadFailed {
                let failure = loadFailure
                resetTransport()
                fputs("Compute peer page failed: \(failure)\n", stderr)
                throw ComputeWorkerRunError.unavailable
            }
            try await Task.sleep(nanoseconds: 50_000_000)
        }
        let served = pageServed
        resetTransport()
        fputs("Compute peer page readiness timed out (served=\(served))\n", stderr)
        throw ComputeWorkerRunError.timeout
    }

    private func resetTransport() {
        cancelAll()
        webView?.configuration.userContentController.removeScriptMessageHandler(forName: "peer")
        webView?.stopLoading(); webView = nil
        window?.close(); window = nil
        server?.stop(); server = nil
        ready = false; pageServed = false; loadFailed = false; loadFailure = ""
    }

    public func accept(_ message: [String: Any], generation: String) throws {
        guard ready, lease == nil, message["generation"] as? String == generation,
              let jobID = message["job_id"] as? String, UUID(uuidString: jobID) != nil,
              let attemptID = message["attempt_id"] as? String, UUID(uuidString: attemptID) != nil,
              message["operation"] as? String == "image_resize_v1",
              let digest = (message["digest"] ?? message["payload_sha256"]) as? String,
              digest.range(of: "^[0-9a-f]{64}$", options: .regularExpression) != nil,
              let payloadBytes = message["payload_bytes"] as? Int, (1...6 * 1024 * 1024).contains(payloadBytes),
              let timeout = message["timeout_ms"] as? Int, (1...30_000).contains(timeout),
              let token = message["token"] as? String, let tokenData = tokenBytes(token), tokenData.count == 32,
              let offer = message["offer"] as? [String: Any], offer["type"] as? String == "offer",
              let sdp = offer["sdp"] as? String, !sdp.isEmpty, sdp.utf8.count <= 64 * 1024 else { throw AgentError.invalidJob }
        lease = Lease(generation: generation, jobID: jobID, attemptID: attemptID, operation: "image_resize_v1",
                      digest: digest, payloadBytes: payloadBytes, timeoutMilliseconds: timeout,
                      token: token, started: Date())
        executingAttemptID = nil; pendingResult = nil
        leaseTimeout?.cancel()
        leaseTimeout = Task { [weak self] in
            try? await Task.sleep(nanoseconds: UInt64(timeout) * 1_000_000)
            guard !Task.isCancelled, let self, self.lease?.attemptID == attemptID else { return }
            self.runner?.cancel()
            _ = try? await self.webView?.evaluateJavaScript("window.roboparkCancel(); null")
            self.finish(attemptID: attemptID, ok: false, input: 0, output: 0)
        }
        let payload: [String: Any] = ["job_id": jobID, "attempt_id": attemptID, "payload_sha256": digest,
                                      "offer": ["type": "offer", "sdp": sdp]]
        guard let bytes = try? JSONSerialization.data(withJSONObject: payload), let json = String(data: bytes, encoding: .utf8) else { throw AgentError.invalidJob }
        webView?.evaluateJavaScript("window.roboparkAcceptOffer(\(json)); null") { [weak self] _, error in
            if let error {
                fputs("Compute peer offer injection failed: \(error)\n", stderr)
                self?.finish(attemptID: attemptID, ok: false, input: 0, output: 0)
            }
        }
    }

    public func cancel(_ message: [String: Any]) {
        guard let lease, message["generation"] as? String == lease.generation,
              message["job_id"] as? String == lease.jobID,
              message["attempt_id"] as? String == lease.attemptID else { return }
        cancelAll()
    }

    public func cancelAll() {
        runner?.cancel(); runner = nil; leaseTimeout?.cancel(); leaseTimeout = nil
        lease = nil; executingAttemptID = nil; pendingResult = nil
        webView?.evaluateJavaScript("window.roboparkCancel()", completionHandler: nil)
    }

    private func handle(_ request: LocalHTTPRequest) async -> LocalHTTPResponse {
        let peerPath = "/\(capability)/peer"
        let executePath = "/\(capability)/execute"
        if request.method == "GET", request.path == peerPath {
            pageServed = true
            let data = peerDocument ?? Bundle.main.url(forResource: "compute-peer", withExtension: "html").flatMap { try? Data(contentsOf: $0) }
            guard let data, data.count <= 128 * 1024 else { return .json(["detail": "asset_missing"], status: 503) }
            return LocalHTTPResponse(headers: ["Content-Type": "text/html; charset=utf-8",
                "Content-Security-Policy": "default-src 'none'; script-src 'unsafe-inline'; connect-src 'self' stun:; style-src 'none'; img-src 'none'; media-src 'none'"], body: data)
        }
        guard request.method == "POST", request.path == executePath,
              request.headers["origin"] == request.headers["host"].map({ "http://" + $0 }),
              let active = lease, Date().timeIntervalSince(active.started) * 1000 < Double(active.timeoutMilliseconds),
              let envelope = try? JSONSerialization.jsonObject(with: request.body) as? [String: Any],
              let suppliedToken = envelope["token"] as? String, LocalHTTPPolicy.equalSecret(suppliedToken, active.token),
              let frame = envelope["frame"] as? [String: Any], frame["type"] as? String == "compute_job",
              frame["generation"] as? String == active.generation, frame["job_id"] as? String == active.jobID,
              frame["attempt_id"] as? String == active.attemptID, frame["operation"] as? String == active.operation,
              frame["payload_sha256"] as? String == active.digest,
              let payload64 = frame["payload_base64"] as? String,
              let payload = Data(base64Encoded: payload64), payload.count == active.payloadBytes,
              SHA256.hash(data: payload).map({ String(format: "%02x", $0) }).joined() == active.digest,
              let frameBytes = try? JSONSerialization.data(withJSONObject: frame, options: [.sortedKeys]), frameBytes.count <= 9 * 1024 * 1024 else {
            return .json(["detail": "invalid_peer_lease"], status: 403)
        }
        guard executingAttemptID == nil, pendingResult == nil else {
            return .json(["detail": "peer_lease_already_used"], status: 409)
        }
        executingAttemptID = active.attemptID
        let worker = ComputeWorkerProcess(); runner = worker
        do {
            let elapsed = Int(Date().timeIntervalSince(active.started) * 1000)
            let result = try await worker.run(frame: frameBytes, timeoutMilliseconds: max(1, active.timeoutMilliseconds - elapsed), executable: workerURL)
            guard lease?.attemptID == active.attemptID,
                  let object = try JSONSerialization.jsonObject(with: result) as? [String: Any],
                  object["generation"] as? String == active.generation, object["job_id"] as? String == active.jobID,
                  object["attempt_id"] as? String == active.attemptID, object["payload_sha256"] as? String == active.digest else {
                throw AgentError.invalidJob
            }
            var output = 0
            if let row = object["result"] as? [String: Any], let data64 = row["data_base64"] as? String,
               let data = Data(base64Encoded: data64) { output = data.count }
            guard lease?.attemptID == active.attemptID else { throw CancellationError() }
            runner = nil; executingAttemptID = nil
            pendingResult = (active.attemptID, payload.count, output, object["ok"] as? Bool == true)
            return LocalHTTPResponse(headers: ["Content-Type": "application/json", "Cache-Control": "no-store"], body: result)
        } catch {
            if lease?.attemptID == active.attemptID {
                runner = nil; executingAttemptID = nil
                finish(attemptID: active.attemptID, ok: false, input: payload.count, output: 0)
            }
            return .json(["detail": "peer_worker_failed"], status: 502)
        }
    }

    private func finish(attemptID: String, ok: Bool, input: Int, output: Int) {
        guard let active = lease, active.attemptID == attemptID else { return }
        leaseTimeout?.cancel(); leaseTimeout = nil
        lease = nil; runner = nil; executingAttemptID = nil; pendingResult = nil
        let duration = max(0, min(30_000, Int(Date().timeIntervalSince(active.started) * 1000)))
        Task { await outbound(["type": "compute_peer_done", "generation": active.generation,
            "job_id": active.jobID, "attempt_id": active.attemptID, "payload_sha256": active.digest,
            "ok": ok, "input_bytes": input, "output_bytes": output, "duration_ms": duration]) }
    }

    public func userContentController(_ userContentController: WKUserContentController, didReceive message: WKScriptMessage) {
        guard message.name == "peer", let body = message.body as? [String: Any] else { return }
        if body["type"] as? String == "ready" { ready = true; return }
        guard let active = lease,
              body["job_id"] as? String == active.jobID, body["attempt_id"] as? String == active.attemptID else { return }
        if body["type"] as? String == "answer", let answer = body["answer"] as? [String: Any],
           answer["type"] as? String == "answer", let sdp = answer["sdp"] as? String,
           !sdp.isEmpty, sdp.utf8.count <= 64 * 1024 {
            Task { await outbound(["type": "compute_peer_answer", "generation": active.generation,
                "job_id": active.jobID, "attempt_id": active.attemptID, "operation": active.operation,
                "payload_sha256": active.digest, "payload_bytes": active.payloadBytes,
                "answer": ["type": "answer", "sdp": sdp]]) }
        } else if body["type"] as? String == "complete", let result = pendingResult,
                  result.attemptID == active.attemptID {
            finish(attemptID: active.attemptID, ok: result.ok, input: result.input, output: result.output)
        } else if body["type"] as? String == "failed" {
            fputs("Compute peer failed: \(body["detail"] as? String ?? "unknown")\n", stderr)
            runner?.cancel()
            finish(attemptID: active.attemptID, ok: false, input: 0, output: 0)
        }
    }

    public func webView(_ webView: WKWebView, decidePolicyFor navigationAction: WKNavigationAction,
                 decisionHandler: @escaping (WKNavigationActionPolicy) -> Void) {
        let url = navigationAction.request.url
        decisionHandler(url?.scheme == "http" && url?.host == "127.0.0.1" ? .allow : .cancel)
    }

    public func webView(_ webView: WKWebView, didFinish navigation: WKNavigation!) { ready = true }
    public func webView(_ webView: WKWebView, didFail navigation: WKNavigation!, withError error: Error) {
        loadFailure = String(describing: error); loadFailed = true
    }
    public func webView(_ webView: WKWebView, didFailProvisionalNavigation navigation: WKNavigation!, withError error: Error) {
        loadFailure = String(describing: error); loadFailed = true
    }
}
