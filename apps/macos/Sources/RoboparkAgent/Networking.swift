import Foundation
import Security
import AgentCore

enum NetworkFailure: Error {
    case auth, network, invalid
    var wireCode: String { switch self { case .auth: return "auth"; case .network: return "network"; case .invalid: return "invalid" } }
}

final class NetworkPolicy: NSObject, URLSessionTaskDelegate {
    // Never forward a credential-bearing request to a redirect target.
    func urlSession(_ session: URLSession, task: URLSessionTask, willPerformHTTPRedirection response: HTTPURLResponse,
                    newRequest request: URLRequest, completionHandler: @escaping (URLRequest?) -> Void) {
        completionHandler(nil)
    }
    func urlSession(_ session: URLSession, task: URLSessionTask, didReceive challenge: URLAuthenticationChallenge,
                    completionHandler: @escaping (URLSession.AuthChallengeDisposition, URLCredential?) -> Void) {
        guard challenge.protectionSpace.authenticationMethod == NSURLAuthenticationMethodServerTrust,
              challenge.protectionSpace.host == EmergencyTarget.host,
              let trust = challenge.protectionSpace.serverTrust,
              let caURL = Bundle.main.url(forResource: "YandexInternalRootCA", withExtension: "der"),
              let caData = try? Data(contentsOf: caURL),
              let cert = SecCertificateCreateWithData(nil, caData as CFData) else {
            completionHandler(.performDefaultHandling, nil); return
        }
        SecTrustSetAnchorCertificates(trust, [cert] as CFArray)
        SecTrustSetAnchorCertificatesOnly(trust, false)
        if SecTrustEvaluateWithError(trust, nil) { completionHandler(.useCredential, URLCredential(trust: trust)) }
        else { completionHandler(.cancelAuthenticationChallenge, nil) }
    }
}

enum Network {
    static let policy = NetworkPolicy()
    static let session: URLSession = {
        let config = URLSessionConfiguration.ephemeral
        config.httpCookieStorage = nil
        config.urlCache = nil
        config.timeoutIntervalForRequest = 12
        config.timeoutIntervalForResource = 120
        config.httpMaximumConnectionsPerHost = 4
        return URLSession(configuration: config, delegate: policy, delegateQueue: nil)
    }()
    static func request(_ url: URL, method: String = "GET", object: [String: Any]? = nil,
                        cookie: String? = nil, limit: Int = 128 * 1024, timeout: Double = 12) async throws -> (Data, HTTPURLResponse) {
        guard url.scheme == "https" else { throw AgentError.invalidOrigin }
        var request = URLRequest(url: url, cachePolicy: .reloadIgnoringLocalCacheData, timeoutInterval: timeout)
        request.httpMethod = method
        request.setValue("application/json", forHTTPHeaderField: "Accept")
        request.setValue("RoboparkMacAgent/1", forHTTPHeaderField: "User-Agent")
        if let object {
            request.httpBody = try JSONSerialization.data(withJSONObject: object)
            request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        }
        if let cookie {
            guard url.host == EmergencyTarget.host else { throw AgentError.invalidJob }
            request.setValue(cookie, forHTTPHeaderField: "Cookie")
            request.setValue("XMLHttpRequest", forHTTPHeaderField: "X-Requested-With")
        }
        let configuration = session.configuration
        configuration.timeoutIntervalForResource = timeout
        let boundedSession = URLSession(configuration: configuration, delegate: policy, delegateQueue: nil)
        defer { boundedSession.invalidateAndCancel() }
        let (stream, rawResponse) = try await boundedSession.bytes(for: request)
        guard let response = rawResponse as? HTTPURLResponse else { throw NetworkFailure.network }
        guard response.expectedContentLength <= Int64(limit) else { throw NetworkFailure.invalid }
        var data = Data()
        for try await byte in stream {
            guard data.count < limit else { throw NetworkFailure.invalid }
            data.append(byte)
        }
        return (data, response)
    }
    static func emergency(vin: String, cookie: String, timeout: Double = 10) async throws -> [String: Any] {
        guard !cookie.isEmpty else { throw NetworkFailure.auth }
        let (data, response) = try await request(EmergencyTarget.url(vin: vin), cookie: cookie,
                                                 limit: 1024 * 1024, timeout: timeout)
        if [301, 302, 303, 307, 308, 401, 403].contains(response.statusCode) { throw NetworkFailure.auth }
        guard response.statusCode == 200 else { throw NetworkFailure.network }
        guard response.mimeType?.lowercased() == "application/json",
              let object = try? JSONSerialization.jsonObject(with: data) as? [String: Any] else { throw NetworkFailure.invalid }
        return object
    }
}
