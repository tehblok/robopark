import Foundation
import CryptoKit
import AgentCore

@MainActor final class WorkspaceServer {
    let port: UInt16 = 18444
    let session: WorkspaceSession
    private let token = SymmetricKey(size: .bits256).withUnsafeBytes { Data($0).map { String(format: "%02x", $0) }.joined() }
    private var server: LoopbackHTTPServer?
    private var starting: Task<UInt16, Error>?
    var initialURL: URL { URL(string: "http://127.0.0.1:\(port)/")! }
    var sessionCookie: HTTPCookie {
        HTTPCookie.cookies(withResponseHeaderFields: ["Set-Cookie": "rp_local=\(token); Path=/; HttpOnly; SameSite=Strict"], for: initialURL)[0]
    }
    init() throws { session = try WorkspaceSession() }
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
    func stop() { starting?.cancel(); starting = nil; server?.stop(); server = nil }
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
        do { try LocalHTTPPolicy.authorize(request, port: port, token: token) }
        catch { return .json(["detail": "local_session_required"], status: 403) }
        if request.path.hasPrefix("/api/") { return await session.proxy(request) }
        guard ["GET", "HEAD"].contains(request.method) else { return .json(["detail": "method_not_allowed"], status: 405) }
        if request.path == "/__robopark/storage-key" {
            guard session.profile != nil else { return .json(["detail": "workspace_not_configured"], status: 503) }
            do { return .json(["key": try storageKey().base64EncodedString()]) }
            catch { return .json(["detail": "keychain_locked"], status: 503) }
        }
        if request.path == "/__robopark/config.js" {
            guard let profile = session.profile else { return .json(["detail": "workspace_not_configured"], status: 503) }
            let data = (try? JSONSerialization.data(withJSONObject: ["version": 1, "instanceId": profile.instanceID])) ?? Data("{}".utf8)
            return LocalHTTPResponse(headers: ["Content-Type": "application/javascript", "Cache-Control": "no-store"],
                body: Data("window.__ROBOPARK_DESKTOP__=".utf8) + data + Data(";".utf8))
        }
        do {
            guard let root = Bundle.main.resourceURL?.appendingPathComponent("workspace", isDirectory: true) else { throw NetworkFailure.invalid }
            let decoded = try LocalHTTPPolicy.decodedPath(request.target)
            let path = root.appendingPathComponent(String(decoded.dropFirst())).standardizedFileURL
            guard path.path.hasPrefix(root.standardizedFileURL.path + "/") || path == root else { throw NetworkFailure.invalid }
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
