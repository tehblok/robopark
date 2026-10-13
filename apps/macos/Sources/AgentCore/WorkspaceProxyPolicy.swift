import Foundation

public struct WorkspaceCookie: Codable, Equatable, Sendable {
    public let name: String
    public let value: String
    public let path: String
    public let expires: Date?
    public init(name: String, value: String, path: String, expires: Date?) {
        self.name = name; self.value = value; self.path = path; self.expires = expires
    }
}

public enum WorkspaceProxyPolicy {
    public static func request(origin: ServerOrigin, target: String, method: String,
                               headers: [String: String], body: Data, cookies: [WorkspaceCookie]) throws -> URLRequest {
        guard ["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"].contains(method),
              body.count <= LocalHTTPRequest.maxBodyBytes else { throw LocalHTTPError.malformed }
        let url = try LocalHTTPPolicy.proxyURL(origin, target: target)
        var request = URLRequest(url: url, cachePolicy: .reloadIgnoringLocalCacheData, timeoutInterval: 30)
        request.httpMethod = method
        if !body.isEmpty { request.httpBody = body }
        let allowed: Set<String> = ["accept", "content-type", "if-match", "if-none-match", "range", "if-range", "idempotency-key", "x-chunk-sha256", "x-expected-account-id"]
        for (key, value) in headers where allowed.contains(key) {
            guard !value.contains("\r"), !value.contains("\n") else { throw LocalHTTPError.malformed }
            request.setValue(value, forHTTPHeaderField: key)
        }
        request.setValue("RoboparkDesktop/2", forHTTPHeaderField: "User-Agent")
        request.setValue(origin.url.absoluteString, forHTTPHeaderField: "Origin")
        let selected = cookies.filter {
            let matches = url.path == $0.path || (url.path.hasPrefix($0.path) && ($0.path.hasSuffix("/") || url.path.dropFirst($0.path.count).hasPrefix("/")))
            return ($0.expires == nil || $0.expires! > Date()) && matches
                && $0.name.range(of: "^[A-Za-z0-9_!#$%&'*+.^`|~-]{1,128}$", options: .regularExpression) != nil
                && !$0.value.contains(";") && !$0.value.contains("\r") && !$0.value.contains("\n") && $0.value.utf8.count <= 8192
        }
        if !selected.isEmpty { request.setValue(selected.map { "\($0.name)=\($0.value)" }.joined(separator: "; "), forHTTPHeaderField: "Cookie") }
        return request
    }
    public static func responseHeaders(_ original: [String: String]) -> [String: String] {
        var headers: [String: String] = ["Cache-Control": "no-store", "Content-Security-Policy": "sandbox; default-src 'none'"]
        for (key, value) in original {
            if ["content-type", "content-disposition", "etag", "last-modified", "retry-after", "content-range", "accept-ranges", "x-request-id", "x-schedule-has-more", "x-schedule-next-start-at", "x-schedule-next-id"].contains(key.lowercased()),
               !value.contains("\r"), !value.contains("\n") { headers[key] = value }
        }
        return headers
    }
}
