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
              [EmergencyTarget.host, CorporateAuthPolicy.trackerHost, CorporateReadPolicy.inventoryHost]
                .contains(challenge.protectionSpace.host),
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

    static func corporateIdentity(authorization: String, timeout: Double = 10) async throws -> (Data, HTTPURLResponse) {
        guard authorization.utf8.count <= 16 * 1024,
              (authorization.hasPrefix("OAuth ") || authorization.hasPrefix("Session_id=")),
              !authorization.contains("\r"), !authorization.contains("\n") else {
            throw NetworkFailure.invalid
        }
        var request = URLRequest(url: CorporateAuthPolicy.trackerIdentityURL,
                                 cachePolicy: .reloadIgnoringLocalCacheData,
                                 timeoutInterval: min(20, max(1, timeout)))
        request.httpMethod = "GET"
        request.setValue("application/json", forHTTPHeaderField: "Accept")
        request.setValue("RoboparkMacAgent/1", forHTTPHeaderField: "User-Agent")
        if authorization.hasPrefix("OAuth ") {
            request.setValue(authorization, forHTTPHeaderField: "Authorization")
        } else {
            request.setValue(authorization, forHTTPHeaderField: "Cookie")
        }
        let config = URLSessionConfiguration.ephemeral
        config.httpCookieStorage = nil; config.httpShouldSetCookies = false; config.urlCache = nil
        config.timeoutIntervalForRequest = request.timeoutInterval
        config.timeoutIntervalForResource = request.timeoutInterval
        let bounded = URLSession(configuration: config, delegate: policy, delegateQueue: nil)
        defer { bounded.invalidateAndCancel() }
        let (stream, raw) = try await bounded.bytes(for: request)
        guard let response = raw as? HTTPURLResponse,
              response.expectedContentLength <= 64 * 1024 else { throw NetworkFailure.invalid }
        var data = Data()
        for try await byte in stream {
            guard data.count < 64 * 1024 else { throw NetworkFailure.invalid }
            data.append(byte)
        }
        return (data, response)
    }

    static func corporateRead(plan: CorporateReadPlan, oauthToken: String,
                              request corporateRequest: CorporateReadRequest,
                              timeout: Double = 15, sessionOverride: URLSession? = nil) async throws -> [String: Any] {
        guard plan.url.scheme == "https", plan.url.port == nil,
              [CorporateReadPolicy.inventoryHost, CorporateReadPolicy.trackerHost].contains(plan.url.host ?? ""),
              plan.url.user == nil, plan.url.password == nil,
              ["GET", "POST"].contains(plan.method),
              oauthToken.utf8.count > 0, oauthToken.utf8.count <= 8192,
              !oauthToken.contains("\r"), !oauthToken.contains("\n") else { throw CorporateReadError.invalid }
        var request = URLRequest(url: plan.url, cachePolicy: .reloadIgnoringLocalCacheData,
                                 timeoutInterval: min(20, max(1, timeout)))
        request.httpMethod = plan.method
        request.httpBody = plan.body
        request.setValue("OAuth \(oauthToken)", forHTTPHeaderField: "Authorization")
        request.setValue("application/json", forHTTPHeaderField: "Accept")
        request.setValue("RoboparkMacAgent/1", forHTTPHeaderField: "User-Agent")
        if plan.body != nil { request.setValue("application/json", forHTTPHeaderField: "Content-Type") }
        let ownedSession: URLSession?
        let transport: URLSession
        if let sessionOverride { ownedSession = nil; transport = sessionOverride }
        else {
            let config = URLSessionConfiguration.ephemeral
            config.httpCookieStorage = nil; config.httpShouldSetCookies = false; config.urlCache = nil
            config.timeoutIntervalForRequest = request.timeoutInterval
            config.timeoutIntervalForResource = request.timeoutInterval
            let created = URLSession(configuration: config, delegate: policy, delegateQueue: nil)
            ownedSession = created; transport = created
        }
        defer { ownedSession?.invalidateAndCancel() }
        do {
            let (stream, raw) = try await transport.bytes(for: request)
            guard let response = raw as? HTTPURLResponse,
                  response.expectedContentLength <= Int64(CorporateReadPolicy.maxResponseBytes),
                  response.mimeType?.lowercased() == "application/json" else {
                throw CorporateReadError.tooLarge
            }
            var data = Data()
            for try await byte in stream {
                guard data.count < CorporateReadPolicy.maxResponseBytes else { throw CorporateReadError.tooLarge }
                data.append(byte)
            }
            return try CorporateReadPolicy.result(status: response.statusCode, data: data,
                                                  headers: response.allHeaderFields, request: corporateRequest)
        } catch let error as CorporateReadError { throw error }
        catch is CancellationError { throw CancellationError() }
        catch { throw CorporateReadError.network }
    }

    static func corporateInventoryReadiness(serverAuthorizedVIN: String, oauthToken: String,
                                            timeout: Double = 10,
                                            sessionOverride: URLSession? = nil) async -> CorporateInventoryStatus {
        guard let rover = try? CorporateReadPolicy.inventoryRover(serverAuthorizedVIN: serverAuthorizedVIN),
              oauthToken.utf8.count > 0, oauthToken.utf8.count <= 8192,
              !oauthToken.contains("\r"), !oauthToken.contains("\n"),
              let url = URL(string: "https://\(CorporateReadPolicy.inventoryHost)/api/v1/rovers/\(rover)/") else {
            return .unverifiedHostACL
        }
        var request = URLRequest(url: url, cachePolicy: .reloadIgnoringLocalCacheData,
                                 timeoutInterval: min(20, max(1, timeout)))
        request.httpMethod = "GET"
        request.setValue("OAuth \(oauthToken)", forHTTPHeaderField: "Authorization")
        request.setValue("application/json", forHTTPHeaderField: "Accept")
        request.setValue("RoboparkMacAgent/1", forHTTPHeaderField: "User-Agent")
        let ownedSession: URLSession?
        let transport: URLSession
        if let sessionOverride { ownedSession = nil; transport = sessionOverride }
        else {
            let config = URLSessionConfiguration.ephemeral
            config.httpCookieStorage = nil; config.httpShouldSetCookies = false; config.urlCache = nil
            config.timeoutIntervalForRequest = request.timeoutInterval
            config.timeoutIntervalForResource = request.timeoutInterval
            let created = URLSession(configuration: config, delegate: policy, delegateQueue: nil)
            ownedSession = created; transport = created
        }
        defer { ownedSession?.invalidateAndCancel() }
        do {
            let (stream, raw) = try await transport.bytes(for: request)
            guard let response = raw as? HTTPURLResponse else { return .unreachable }
            if response.statusCode == 401 || response.statusCode == 403 { return .denied }
            guard response.statusCode == 200,
                  response.expectedContentLength <= Int64(CorporateReadPolicy.maxResponseBytes),
                  response.mimeType?.lowercased() == "application/json" else { return .unreachable }
            var data = Data()
            for try await byte in stream {
                guard data.count < CorporateReadPolicy.maxResponseBytes else { return .unreachable }
                data.append(byte)
            }
            guard (try? JSONSerialization.jsonObject(with: data)) is [String: Any] else { return .unreachable }
            return .ready
        } catch { return .unreachable }
    }

    static func corporateWrite(plan: CorporateWritePlan, oauthToken: String,
                               request corporateRequest: CorporateWriteRequest,
                               timeout: Double = 20, sessionOverride: URLSession? = nil) async throws -> [String: Any] {
        guard plan.url.scheme == "https", plan.url.host == CorporateAuthPolicy.trackerHost,
              plan.url.port == nil, plan.url.user == nil, plan.url.password == nil,
              ["POST", "PATCH"].contains(plan.method), plan.body.count <= CorporateWritePolicy.maxRequestBytes,
              oauthToken.utf8.count > 0, oauthToken.utf8.count <= 8192,
              !oauthToken.contains("\r"), !oauthToken.contains("\n") else { throw CorporateWriteError.invalid }
        var request = URLRequest(url: plan.url, cachePolicy: .reloadIgnoringLocalCacheData,
                                 timeoutInterval: min(20, max(1, timeout)))
        request.httpMethod = plan.method; request.httpBody = plan.body
        request.setValue("OAuth \(oauthToken)", forHTTPHeaderField: "Authorization")
        request.setValue("application/json", forHTTPHeaderField: "Accept")
        request.setValue(plan.contentType, forHTTPHeaderField: "Content-Type")
        if let version = plan.ifMatch {
            request.setValue("\"\(version)\"", forHTTPHeaderField: "If-Match")
        }
        request.setValue("RoboparkMacAgent/1", forHTTPHeaderField: "User-Agent")
        let ownedSession: URLSession?
        let transport: URLSession
        if let sessionOverride { ownedSession = nil; transport = sessionOverride }
        else {
            let config = URLSessionConfiguration.ephemeral
            config.httpCookieStorage = nil; config.httpShouldSetCookies = false; config.urlCache = nil
            config.timeoutIntervalForRequest = request.timeoutInterval
            config.timeoutIntervalForResource = request.timeoutInterval
            let created = URLSession(configuration: config, delegate: policy, delegateQueue: nil)
            ownedSession = created; transport = created
        }
        defer { ownedSession?.invalidateAndCancel() }
        do {
            let (stream, raw) = try await transport.bytes(for: request)
            guard let response = raw as? HTTPURLResponse,
                  response.expectedContentLength <= Int64(CorporateWritePolicy.maxResponseBytes) else {
                throw CorporateWriteError.network
            }
            var data = Data()
            for try await byte in stream {
                guard data.count < CorporateWritePolicy.maxResponseBytes else { throw CorporateWriteError.network }
                data.append(byte)
            }
            if !data.isEmpty, response.mimeType?.lowercased() != "application/json" {
                throw CorporateWriteError.network
            }
            return try CorporateWritePolicy.result(status: response.statusCode, data: data, request: corporateRequest)
        } catch let error as CorporateWriteError { throw error }
        catch is CancellationError { throw CorporateWriteError.outcomeUnknown }
        catch { throw CorporateWriteError.outcomeUnknown }
    }
}
