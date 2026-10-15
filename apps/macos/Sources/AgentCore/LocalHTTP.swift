import Foundation
import CryptoKit

public enum LocalHTTPError: Error { case malformed, forbidden, tooLarge }

/// One bounded HTTP/1.1 request per connection; no pipelining or transfer coding.
public struct LocalHTTPRequest: Equatable, Sendable {
    public let method: String
    public let target: String
    public let headers: [String: String]
    public let body: Data
    /// Validated, percent-decoded routing path. Lifecycle checks must use the
    /// same path that the remote ASGI router sees (e.g. auth/%6cogout).
    public let path: String
    public static let maxBodyBytes = 16 * 1024 * 1024
    public static let maxHeaderBytes = 16 * 1024

    public static func parse(_ data: Data) throws -> LocalHTTPRequest? {
        guard data.count <= maxBodyBytes + maxHeaderBytes else { throw LocalHTTPError.tooLarge }
        guard let marker = data.range(of: Data("\r\n\r\n".utf8)) else {
            if data.count > maxHeaderBytes { throw LocalHTTPError.tooLarge }
            return nil
        }
        guard marker.upperBound <= maxHeaderBytes,
              let text = String(data: data[..<marker.lowerBound], encoding: .utf8),
              text.utf8.allSatisfy({ $0 == 9 || $0 == 10 || $0 == 13 || (32...126).contains($0) }) else { throw LocalHTTPError.malformed }
        let lines = text.components(separatedBy: "\r\n")
        let start = lines[0].split(separator: " ", omittingEmptySubsequences: false)
        guard start.count == 3, start[2] == "HTTP/1.1",
              ["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"].contains(String(start[0])) else { throw LocalHTTPError.malformed }
        let target = String(start[1])
        let path = try LocalHTTPPolicy.decodedPath(target)
        var headers: [String: String] = [:]
        for line in lines.dropFirst() {
            guard let colon = line.firstIndex(of: ":"), !line.hasPrefix(" "), !line.hasPrefix("\t") else { throw LocalHTTPError.malformed }
            let name = String(line[..<colon]).lowercased()
            let value = String(line[line.index(after: colon)...]).trimmingCharacters(in: .whitespaces)
            guard name.range(of: "^[a-z0-9!#$%&'*+.^_`|~-]+$", options: .regularExpression) != nil,
                  headers[name] == nil, !value.utf8.contains(13), !value.utf8.contains(10) else { throw LocalHTTPError.malformed }
            headers[name] = value
        }
        guard headers["host"] != nil, headers["transfer-encoding"] == nil,
              headers["expect"] == nil, headers["upgrade"] == nil else { throw LocalHTTPError.malformed }
        let lengthText = headers["content-length"] ?? "0"
        guard lengthText.range(of: "^[0-9]{1,9}$", options: .regularExpression) != nil,
              let length = Int(lengthText), length <= maxBodyBytes else { throw LocalHTTPError.tooLarge }
        let actual = data.count - marker.upperBound
        if actual < length { return nil }
        guard actual == length else { throw LocalHTTPError.malformed }
        return LocalHTTPRequest(method: String(start[0]), target: target, headers: headers, body: Data(data[marker.upperBound...]), path: path)
    }
}

public enum LocalHTTPPolicy {
    public static func decodedPath(_ target: String) throws -> String {
        guard target.utf8.count <= 8192, target.hasPrefix("/"), !target.hasPrefix("//"),
              !target.contains("#"), !target.contains("\\"),
              !target.unicodeScalars.contains(where: { $0.value < 33 || $0.value == 127 }) else { throw LocalHTTPError.malformed }
        let rawPath = String(target.split(separator: "?", maxSplits: 1, omittingEmptySubsequences: false)[0])
        let lower = rawPath.lowercased()
        guard !lower.contains("%2f"), !lower.contains("%5c"),
              let path = rawPath.removingPercentEncoding, !path.contains("%"), !path.contains("\\"),
              !path.unicodeScalars.contains(where: { $0.value < 32 || $0.value == 127 }),
              !path.split(separator: "/", omittingEmptySubsequences: false).contains(where: { $0 == "." || $0 == ".." }) else { throw LocalHTTPError.malformed }
        return path
    }
    public static func proxyURL(_ origin: ServerOrigin, target: String) throws -> URL {
        let path = try decodedPath(target)
        guard path.hasPrefix("/api/"), !path.hasPrefix("/api//"),
              let url = URL(string: origin.url.absoluteString + target),
              url.scheme == "https", url.host == origin.url.host, url.port == origin.url.port,
              url.user == nil, url.password == nil else { throw LocalHTTPError.forbidden }
        return url
    }
    public static func equalSecret(_ lhs: String, _ rhs: String) -> Bool {
        let a = Array(SHA256.hash(data: Data(lhs.utf8)))
        let b = Array(SHA256.hash(data: Data(rhs.utf8)))
        return zip(a, b).reduce(UInt8(0)) { $0 | ($1.0 ^ $1.1) } == 0
    }
    /// Also applies to the credential-free browser shell and login endpoint.
    public static func authorizeContext(_ request: LocalHTTPRequest, port: UInt16) throws {
        let host = "127.0.0.1:\(port)"
        let origin = "http://" + host
        guard request.headers["host"] == host,
              request.headers["origin"] == nil || request.headers["origin"] == origin,
              !["cross-site", "same-site"].contains(request.headers["sec-fetch-site"] ?? "") else { throw LocalHTTPError.forbidden }
        if !["GET", "HEAD"].contains(request.method), request.headers["origin"] != origin { throw LocalHTTPError.forbidden }
    }
    public static func authorize(_ request: LocalHTTPRequest, port: UInt16, token: String) throws {
        try authorizeContext(request, port: port)
        guard let capability = request.headers["x-robopark-session"],
              !capability.isEmpty, equalSecret(capability, token) else { throw LocalHTTPError.forbidden }
    }
}

public struct LocalHTTPResponse: Sendable {
    public var status: Int
    public var headers: [String: String]
    public var body: Data
    public init(status: Int = 200, headers: [String: String] = [:], body: Data = Data()) {
        self.status = status; self.headers = headers; self.body = body
    }
    public static func json(_ value: [String: Any], status: Int = 200) -> LocalHTTPResponse {
        LocalHTTPResponse(status: status, headers: ["Content-Type": "application/json", "Cache-Control": "no-store"],
                          body: (try? JSONSerialization.data(withJSONObject: value)) ?? Data("{}".utf8))
    }
    public func wire(head: Bool = false) -> Data {
        var fields = headers
        fields["Content-Length"] = String(body.count)
        fields["Connection"] = "close"
        fields["X-Content-Type-Options"] = "nosniff"
        fields["Referrer-Policy"] = "no-referrer"
        fields["Cross-Origin-Resource-Policy"] = "same-origin"
        fields["X-Frame-Options"] = "DENY"
        let safe = fields.filter { !$0.key.utf8.contains(13) && !$0.key.utf8.contains(10) && !$0.value.utf8.contains(13) && !$0.value.utf8.contains(10) }
        var bytes = Data(("HTTP/1.1 \(status) Response\r\n" + safe.sorted(by: { $0.key < $1.key }).map { "\($0.key): \($0.value)\r\n" }.joined() + "\r\n").utf8)
        if !head { bytes.append(body) }
        return bytes
    }
}
