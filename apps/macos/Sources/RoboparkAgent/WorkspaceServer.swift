import Foundation
import CryptoKit
import AgentCore

@MainActor final class WorkspaceServer {
    let port: UInt16 = 18444
    let session: WorkspaceSession
    private static let capabilityAccount = "workspace-browser-session-v1"
    private var token: String
    private var launches: [String: (deadline: Date, fragment: String?)] = [:]
    private let now: () -> Date
    var sessionToken: String { token }
    var agentStatus: (() -> LocalHTTPResponse)?
    var agentAction: (([String: Any]) async -> LocalHTTPResponse)?
    private var server: LoopbackHTTPServer?
    private var starting: Task<UInt16, Error>?
    var initialURL: URL { URL(string: "http://127.0.0.1:\(port)/")! }
    private static func newToken() -> String {
        SymmetricKey(size: .bits256).withUnsafeBytes { Data($0).map { String(format: "%02x", $0) }.joined() }
    }
    init(now: @escaping () -> Date = Date.init) throws {
        self.now = now
        let stored = SecureStore.isSignedOut(Self.capabilityAccount) ? nil : try SecureStore.readData(Self.capabilityAccount)
        if let stored, let value = String(data: stored, encoding: .utf8),
           value.range(of: "^[a-f0-9]{64}$", options: .regularExpression) != nil { token = value }
        else {
            token = Self.newToken()
            try SecureStore.writeData(Data(token.utf8), account: Self.capabilityAccount)
            SecureStore.markSignedOut(Self.capabilityAccount, false)
        }
        session = try WorkspaceSession()
        session.onSessionInvalidated = { [weak self] in self?.revokeBrowserSession() }
    }
    private func revokeBrowserSession() {
        // Fail closed across a crash even if Keychain is locked during logout.
        SecureStore.markSignedOut(Self.capabilityAccount, true)
        token = Self.newToken()
        launches.removeAll()
        do {
            try SecureStore.writeData(Data(token.utf8), account: Self.capabilityAccount)
            SecureStore.markSignedOut(Self.capabilityAccount, false)
        } catch { /* Next launch must replace the old durable capability. */ }
    }
    func launchURL(route: String? = nil) -> URL {
        launches = launches.filter { $0.value.deadline > now() }
        if launches.count >= 8 { launches.removeAll() }
        let nonce = Self.newToken()
        let proposed = route.flatMap { URL(string: $0, relativeTo: initialURL)?.absoluteURL }
        let target = proposed.flatMap { url in
            url.scheme == initialURL.scheme && url.host == initialURL.host && url.port == initialURL.port
                && url.user == nil && url.password == nil ? url : nil
        } ?? initialURL
        var parts = URLComponents(url: target, resolvingAgainstBaseURL: false)!
        launches[nonce] = (now().addingTimeInterval(30), parts.fragment)
        parts.fragment = "rp-launch=" + nonce
        return parts.url!
    }
    func start() async throws {
        if server != nil { return }
        if let starting { _ = try await starting.value; return }
        let http = LoopbackHTTPServer(port: port) { [weak self] request in
            guard let self else { return .json(["detail": "workspace_closed"], status: 503) }
            return await self.handle(request)
        }
        let task = Task { try await http.start() }
        starting = task
        do { _ = try await task.value; server = http; starting = nil }
        catch { http.stop(); starting = nil; throw error }
    }
    func stop() { starting?.cancel(); starting = nil; server?.stop(); server = nil; launches.removeAll() }
    private func storageKey() throws -> Data {
        if let bytes = try SecureStore.readData("workspace-storage-key-v1") {
            guard bytes.count == 32 else { throw NetworkFailure.invalid }
            return bytes
        }
        let bytes = SymmetricKey(size: .bits256).withUnsafeBytes { Data($0) }
        try SecureStore.writeData(bytes, account: "workspace-storage-key-v1")
        return bytes
    }
    func handle(_ request: LocalHTTPRequest) async -> LocalHTTPResponse {
        do { try LocalHTTPPolicy.authorizeContext(request, port: port) }
        catch { return .json(["detail": "local_origin_required"], status: 403) }
        if request.path == "/__robopark/bootstrap", request.method == "POST" {
            guard let nonce = request.headers["x-robopark-launch"],
                  let launch = launches.removeValue(forKey: nonce), launch.deadline > now() else {
                return .json(["detail": "launch_expired"], status: 401)
            }
            return .json(["session": token, "fragment": launch.fragment as Any? ?? NSNull()])
        }
        if request.path == "/api/auth/register", request.method == "POST" {
            return await session.proxy(request)
        }
        if request.path == "/api/auth/login", request.method == "POST" {
            var response = await session.proxy(request)
            if (200...299).contains(response.status) {
                // Login status alone is not identity proof. The native session
                // validates /me and the immutable owner before issuing a browser capability.
                guard let identity = try? LocalHTTPRequest.parse(Data("GET /api/auth/me HTTP/1.1\r\nHost: 127.0.0.1:\(port)\r\n\r\n".utf8)) else {
                    return .json(["detail": "identity_request_invalid"], status: 503)
                }
                let verified = await session.proxy(identity)
                guard verified.status == 200, session.profile?.accountID != nil else {
                    let denied = verified.status == 200 ? LocalHTTPResponse.json(["detail": "device_owner_verification_required"], status: 403) : verified
                    return denied
                }
                response.headers["X-Robopark-Session"] = token
            }
            return response
        }
        let privatePath = request.path.hasPrefix("/api/") ||
            (request.path.hasPrefix("/__robopark/") && request.path != "/__robopark/config.js")
        if privatePath {
            do {
                try LocalHTTPPolicy.authorize(request, port: port, token: token)
                guard session.profile?.accountID != nil else { throw NetworkFailure.invalid }
            } catch {
                var response = LocalHTTPResponse.json(["detail": "local_session_required"], status: 401)
                // HEAD has no body; clients must distinguish a stale local
                // capability from revocation of the upstream account.
                response.headers["X-Robopark-Local-Session"] = "required"
                return response
            }
        }
        if request.path.hasPrefix("/api/") {
            return await session.proxy(request)
        }
        if request.path == "/__robopark/agent/status" {
            guard request.method == "GET" else { return .json(["detail": "method_not_allowed"], status: 405) }
            return agentStatus?() ?? .json(["detail": "agent_starting"], status: 503)
        }
        if request.path == "/__robopark/agent/action" {
            guard request.method == "POST" else { return .json(["detail": "method_not_allowed"], status: 405) }
            guard request.body.count <= 2048,
                  let object = try? JSONSerialization.jsonObject(with: request.body) as? [String: Any] else {
                return .json(["detail": "invalid_agent_action"], status: 422)
            }
            return await agentAction?(object) ?? .json(["detail": "agent_starting"], status: 503)
        }
        guard ["GET", "HEAD"].contains(request.method) else { return .json(["detail": "method_not_allowed"], status: 405) }
        if request.path == "/__robopark/storage-key" {
            guard session.profile != nil else { return .json(["detail": "workspace_not_configured"], status: 503) }
            do { return .json(["key": try storageKey().base64EncodedString()]) }
            catch { return .json(["detail": "offline_key_unavailable"], status: 503) }
        }
        if request.path == "/__robopark/config.js" {
            guard let profile = session.profile else { return .json(["detail": "workspace_not_configured"], status: 503) }
            let data = (try? JSONSerialization.data(withJSONObject: ["version": 1, "instanceId": profile.instanceID])) ?? Data("{}".utf8)
            return LocalHTTPResponse(headers: ["Content-Type": "application/javascript", "Cache-Control": "no-store"],
                body: Data("window.__ROBOPARK_DESKTOP__=".utf8) + data + Data(";".utf8))
        }
        if request.path.hasPrefix("/__robopark/") { return .json(["detail": "not_found"], status: 404) }
        do {
            guard let resources = Bundle.main.resourceURL else { throw NetworkFailure.invalid }
            // Resolve the existing directory before adding a possibly absent SPA
            // route: /private/tmp and /tmp must use the same spelling on both sides.
            let root = resources.appendingPathComponent("workspace", isDirectory: true)
                .resolvingSymlinksInPath().standardizedFileURL
            let decoded = try LocalHTTPPolicy.decodedPath(request.target)
            let path = root.appendingPathComponent(String(decoded.dropFirst())).standardizedFileURL
            // Bundle resource URLs can retain a base URL. Compare normalized
            // filesystem paths: URL equality rejects the same root at `/`.
            let rootPath = root.standardizedFileURL.path
            guard path.path.hasPrefix(rootPath + "/") || path.path == rootPath else { throw NetworkFailure.invalid }
            var file = path
            var isDirectory: ObjCBool = false
            if decoded == "/" || !FileManager.default.fileExists(atPath: file.path, isDirectory: &isDirectory) || isDirectory.boolValue {
                guard decoded == "/" || URL(fileURLWithPath: decoded).pathExtension.isEmpty else { return LocalHTTPResponse(status: 404) }
                file = root.appendingPathComponent("index.html")
            }
            guard file.resolvingSymlinksInPath().path.hasPrefix(root.resolvingSymlinksInPath().path + "/"),
                  let size = try file.resourceValues(forKeys: [.fileSizeKey]).fileSize, size <= 16 * 1024 * 1024 else { throw NetworkFailure.invalid }
            var bytes = try Data(contentsOf: file)
            let types = ["html": "text/html; charset=utf-8", "js": "application/javascript", "css": "text/css", "json": "application/json", "svg": "image/svg+xml", "png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg", "webp": "image/webp", "ico": "image/x-icon", "woff2": "font/woff2", "woff": "font/woff", "webmanifest": "application/manifest+json", "wasm": "application/wasm"]
            if file.lastPathComponent == "index.html", let text = String(data: bytes, encoding: .utf8) {
                bytes = Data(text.replacingOccurrences(of: "<head>", with: "<head><script src=\"/__robopark/config.js\"></script>").utf8)
            }
            return LocalHTTPResponse(headers: ["Content-Type": types[file.pathExtension] ?? "application/octet-stream",
                "Cache-Control": "no-store", "Content-Security-Policy": "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob: https:; font-src 'self'; connect-src 'self'; worker-src 'self' blob:; media-src 'self' blob:; object-src 'none'; base-uri 'self'; form-action 'self'; frame-ancestors 'none'"], body: bytes)
        } catch { return .json(["detail": "bundled_workspace_unavailable"], status: 503) }
    }
}
