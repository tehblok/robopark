import Foundation
import AgentCore

var checks = 0
func check(_ value: @autoclosure () throws -> Bool, _ name: String) throws {
    guard try value() else { fatalError("FAIL: \(name)") }; checks += 1
}
func rejects(_ name: String, _ body: () throws -> Void) {
    do { try body() } catch { checks += 1; return }; fatalError("FAIL: \(name)")
}
let token = String(repeating: "a", count: 64)
let proxyOrigin = try ServerOrigin("https://example.com")
let nativeCookie = WorkspaceCookie(name: "session", value: "native-only", path: "/", expires: Date().addingTimeInterval(600))
let proxyRequest = try WorkspaceProxyPolicy.request(origin: proxyOrigin, target: "/api/sync/batch", method: "POST",
    headers: ["cookie": "session=attacker", "authorization": "Bearer attacker", "host": "evil", "origin": "https://evil", "x-chunk-sha256": "hash"],
    body: Data("{}".utf8), cookies: [nativeCookie])
try check(proxyRequest.value(forHTTPHeaderField: "Cookie") == "session=native-only", "browser cannot replace native session")
try check(proxyRequest.value(forHTTPHeaderField: "Authorization") == nil, "arbitrary authorization not forwarded")
try check(proxyRequest.value(forHTTPHeaderField: "Origin") == "https://example.com", "native CSRF origin")
try check(proxyRequest.value(forHTTPHeaderField: "X-Chunk-SHA256") == "hash", "upload integrity header preserved")
let computeBody = Data(repeating: 0x61, count: 6 * 1024 * 1024)
let computeImage = try WorkspaceProxyPolicy.request(origin: proxyOrigin, target: "/api/compute/image", method: "POST",
    headers: ["content-type": "application/json"], body: computeBody, cookies: [nativeCookie])
try check(computeImage.url?.path == "/api/compute/image" && computeImage.httpBody?.count == computeBody.count,
          "bounded desktop proxy carries compute image requests")
let computePool = try WorkspaceProxyPolicy.request(origin: proxyOrigin, target: "/api/compute/pool", method: "GET",
    headers: [:], body: Data(), cookies: [nativeCookie])
try check(computePool.url?.path == "/api/compute/pool", "desktop proxy carries compute pool status")
let expiredCookie = WorkspaceCookie(name: "old", value: "expired", path: "/", expires: .distantPast)
let narrowCookie = WorkspaceCookie(name: "narrow", value: "value", path: "/api/admin", expires: nil)
let scopedRequest = try WorkspaceProxyPolicy.request(origin: proxyOrigin, target: "/api/administrator", method: "GET", headers: [:], body: Data(), cookies: [expiredCookie, narrowCookie])
try check(scopedRequest.value(forHTTPHeaderField: "Cookie") == nil, "cookie expiry and path boundary")
let safeResponse = WorkspaceProxyPolicy.responseHeaders(["Set-Cookie": "secret", "Location": "https://evil", "Content-Length": "999", "Content-Encoding": "gzip", "Content-Type": "application/json", "X-Request-ID": "trace"])
try check(safeResponse["Set-Cookie"] == nil && safeResponse["Location"] == nil && safeResponse["Content-Encoding"] == nil, "no credential or redirect leak")
try check(safeResponse["Content-Type"] == "application/json" && safeResponse["X-Request-ID"] == "trace", "safe response metadata")
func request(_ target: String = "/api/auth/me", method: String = "GET", headers: String = "", body: String = "") throws -> LocalHTTPRequest {
    let wire = "\(method) \(target) HTTP/1.1\r\nHost: 127.0.0.1:18444\r\nCookie: rp_local=\(token)\r\n\(headers)Content-Length: \(body.utf8.count)\r\n\r\n\(body)"
    return try LocalHTTPRequest.parse(Data(wire.utf8))!
}
let valid = try request()
try LocalHTTPPolicy.authorize(valid, port: 18444, token: token)
try check(valid.path == "/api/auth/me", "origin-form path")
try check(try request("/api/auth/%6cogout?reason=user", method: "POST").path == "/api/auth/logout", "encoded logout follows native account lifecycle")
try check(try request("/%61pi/auth/log%69n", method: "POST").path == "/api/auth/login", "encoded login follows native account lifecycle")
let mutation = try request("/api/auth/login", method: "POST", headers: "Origin: http://127.0.0.1:18444\r\n", body: "{}")
try LocalHTTPPolicy.authorize(mutation, port: 18444, token: token)
rejects("CSRF cross origin") { try LocalHTTPPolicy.authorize(request(headers: "Origin: https://evil.example\r\n"), port: 18444, token: token) }
rejects("CSRF no origin on write") { try LocalHTTPPolicy.authorize(request(method: "POST"), port: 18444, token: token) }
rejects("incorrect session") { try LocalHTTPPolicy.authorize(valid, port: 18444, token: "wrong") }
for head in ["Host: attacker.example", "Host: 127.0.0.1:18444\r\nHost: evil", "Host: 127.0.0.1:18444\r\nTransfer-Encoding: chunked", "Host: 127.0.0.1:18444\r\nContent-Length: -1", "Host: 127.0.0.1:18444\r\nContent-Length: 1\r\nContent-Length: 1"] {
    rejects("host/smuggling rejected") {
        let parsed = try LocalHTTPRequest.parse(Data("GET / HTTP/1.1\r\n\(head)\r\n\r\n".utf8))!
        try LocalHTTPPolicy.authorize(parsed, port: 18444, token: token)
    }
}
try check(try LocalHTTPRequest.parse(Data("POST / HTTP/1.1\r\nHost: 127.0.0.1:18444\r\nContent-Length: 4\r\n\r\n12".utf8)) == nil, "partial body waits")
rejects("pipelined requests rejected") { _ = try LocalHTTPRequest.parse(Data("GET / HTTP/1.1\r\nHost: 127.0.0.1:18444\r\n\r\nGET /secret HTTP/1.1\r\n\r\n".utf8)) }
for target in ["//evil/api/x", "https://evil/api/x", "/api/../secret", "/api/%2e%2e/secret", "/api/%252e%252e/secret", "/api/x%2fy", "/api/x%5cy", "/api/x#secret", "/api/x%00"] {
    rejects("path escape") { _ = try LocalHTTPPolicy.proxyURL(try ServerOrigin("https://example.com"), target: target) }
}
try check(try LocalHTTPPolicy.proxyURL(ServerOrigin("https://example.com"), target: "/api/tasks?park_id=3").absoluteString == "https://example.com/api/tasks?park_id=3", "query preserved")
let plain = Data(String(repeating: "{\"robot\":\"YASADR00000000664\"}", count: 1000).utf8)
let encoded = try PayloadCompression.compress(plain)
try check(encoded.count < plain.count / 4, "compression reduces repeated telemetry")
try check(try PayloadCompression.decompress(encoded, expectedBytes: plain.count) == plain, "raw DEFLATE roundtrip")
rejects("decoded limit") { _ = try PayloadCompression.decompress(encoded, expectedBytes: 8) }
rejects("compression input bound") { _ = try PayloadCompression.compress(Data(repeating: 1, count: 2 * 1024 * 1024 + 1)) }
let server = LoopbackHTTPServer(port: 0) { request in
    LocalHTTPResponse.json(["path": request.path])
}
let testPort = try await server.start()
let testSession = URLSession(configuration: .ephemeral)
let (reply, rawResponse) = try await testSession.data(from: URL(string: "http://127.0.0.1:\(testPort)/hello")!)
try check((rawResponse as? HTTPURLResponse)?.statusCode == 200, "real loopback response")
try check((try JSONSerialization.jsonObject(with: reply) as? [String: String])?["path"] == "/hello", "loopback dispatch")
server.stop()
testSession.invalidateAndCancel()
if let flag = CommandLine.arguments.firstIndex(of: "--compression-fixture"), flag + 1 < CommandLine.arguments.count {
    let payload: [String: Any] = ["vin": "YASADR00000000664", "timestamp": Int64(Date().timeIntervalSince1970 * 1000),
        "diagnostics": Array(repeating: ["state": "ok", "sensor": "synthetic-test-sensor"], count: 200)]
    let bytes = try JSONSerialization.data(withJSONObject: payload, options: [.sortedKeys])
    let packed = try PayloadCompression.compress(bytes)
    let frame: [String: Any] = ["encoding": "deflate-raw", "payload_base64": packed.base64EncodedString(), "encoded_bytes": packed.count, "payload_bytes": bytes.count]
    try JSONSerialization.data(withJSONObject: frame).write(to: URL(fileURLWithPath: CommandLine.arguments[flag + 1]))
}
print("PASS: \(checks) desktop HTTP isolation, compression and real loopback checks")
