import Foundation

/// Only fixed messages are exposed: NSError descriptions and URLs may contain credentials.
public enum CorporateLoginPresentation {
    public static func failureMessage(_ error: NSError) -> String? {
        if error.domain == NSURLErrorDomain {
            switch error.code {
            case NSURLErrorCancelled: return nil
            case NSURLErrorTimedOut: return "Истекло время загрузки корпоративного входа. Проверьте сеть и нажмите «Повторить»."
            case NSURLErrorNotConnectedToInternet, NSURLErrorCannotFindHost, NSURLErrorCannotConnectToHost,
                 NSURLErrorNetworkConnectionLost:
                return "Нет связи с корпоративным сервером входа. Проверьте корпоративную сеть и нажмите «Повторить»."
            case NSURLErrorServerCertificateUntrusted, NSURLErrorServerCertificateHasBadDate,
                 NSURLErrorSecureConnectionFailed, NSURLErrorServerCertificateHasUnknownRoot:
                return "Не удалось проверить защищённое соединение с корпоративным сервером входа."
            default: break
            }
        }
        return "Страница корпоративного входа не загрузилась. Нажмите «Повторить»."
    }

    public static func allowsPopup(_ url: URL, sourceProtocol: String,
                                    sourceHost: String, sourcePort: Int) -> Bool {
        guard sourceProtocol == "https", CorporateAuthPolicy.browserHosts.contains(sourceHost.lowercased()),
              [0, 443].contains(sourcePort) else { return false }
        return url.absoluteString == "about:blank" || LoginPolicy.allows(url)
    }

    public static func allowsBlankSubframe(_ url: URL, isMainFrame: Bool,
                                           sourceProtocol: String, sourceHost: String, sourcePort: Int) -> Bool {
        url.absoluteString == "about:blank" && !isMainFrame && sourceProtocol == "https"
            && CorporateAuthPolicy.browserHosts.contains(sourceHost.lowercased())
            && [0, 443].contains(sourcePort)
    }
}

public enum CorporateIdentityStatus: String, Codable {
    case ready
    case reauthRequired = "reauth_required"
    case accessDenied = "access_denied"
    case networkUnavailable = "network_unavailable"
    case ownerMismatch = "owner_mismatch"
}

public enum CorporateInventoryStatus: String, Codable {
    case unverifiedHostACL = "unverified_host_acl"
    case ready
    case denied
    case unreachable
}

public struct CorporateOAuthCredential: Equatable {
    public let token: String
    public let expiresAt: Date

    public func isUsable(now: Date = Date()) -> Bool {
        expiresAt.timeIntervalSince(now) > 60
    }
}

public struct CorporateCookieCandidate: Equatable {
    public let name: String
    public let value: String
    public let domain: String
    public let path: String
    public let secure: Bool
    public let expires: Date?

    public init(name: String, value: String, domain: String, path: String,
                secure: Bool, expires: Date?) {
        self.name = name; self.value = value; self.domain = domain
        self.path = path; self.secure = secure; self.expires = expires
    }
}

public enum CorporateAuthPolicy {
    public static let trackerHost = "st-api.yandex-team.ru"
    public static let oauthHost = "oauth.yandex-team.ru"
    public static let passportHost = "passport.yandex-team.ru"
    public static let oauthClientID = "5f671d781aca402ab7460fde4050267b"
    public static let callbackURL = URL(string: "https://oauth.yandex-team.ru/verification_code")!
    public static let trackerIdentityURL = URL(string: "https://st-api.yandex-team.ru/v3/myself")!
    public static let browserHosts: Set<String> = [oauthHost, passportHost, EmergencyTarget.host]

    public static func normalizedLogin(_ value: String?) -> String? {
        guard let value else { return nil }
        let normalized = value.trimmingCharacters(in: .whitespacesAndNewlines).lowercased()
        guard normalized.range(of: "^[a-z0-9._-]{1,128}$", options: .regularExpression) != nil else { return nil }
        return normalized
    }

    public static func authorizationURL(state: String) throws -> URL {
        guard state.range(of: "^[A-Za-z0-9_-]{32,128}$", options: .regularExpression) != nil else {
            throw AgentError.invalidJob
        }
        var parts = URLComponents(string: "https://oauth.yandex-team.ru/authorize")!
        parts.queryItems = [
            URLQueryItem(name: "response_type", value: "token"),
            URLQueryItem(name: "client_id", value: oauthClientID),
            URLQueryItem(name: "state", value: state),
        ]
        guard let url = parts.url else { throw AgentError.invalidJob }
        return url
    }

    public static func oauthCredential(from url: URL, expectedState: String,
                                       now: Date = Date()) throws -> CorporateOAuthCredential {
        guard url.scheme == "https", url.host?.lowercased() == oauthHost,
              url.port == nil || url.port == 443, url.user == nil, url.password == nil,
              url.path == callbackURL.path, url.query == nil,
              let fragment = url.fragment, fragment.utf8.count <= 16 * 1024 else {
            throw AgentError.invalidJob
        }
        var values: [String: String] = [:]
        for item in fragment.split(separator: "&", omittingEmptySubsequences: false) {
            let pair = item.split(separator: "=", maxSplits: 1, omittingEmptySubsequences: false)
            guard pair.count == 2,
                  let key = String(pair[0]).removingPercentEncoding,
                  let value = String(pair[1]).removingPercentEncoding,
                  !key.isEmpty, values[key] == nil else { throw AgentError.invalidJob }
            values[key] = value
        }
        guard values["state"] == expectedState,
              let token = values["access_token"], !token.isEmpty, token.utf8.count <= 8192,
              !token.utf8.contains(13), !token.utf8.contains(10),
              let lifetimeText = values["expires_in"], let lifetime = TimeInterval(lifetimeText),
              lifetime > 60, lifetime <= 31_536_000 else { throw AgentError.invalidJob }
        return CorporateOAuthCredential(token: token, expiresAt: now.addingTimeInterval(lifetime))
    }

    public static func sessionCookie(from cookies: [CorporateCookieCandidate],
                                     now: Date = Date()) throws -> String {
        let named = cookies.filter { $0.name == "Session_id" }
        guard named.count == 1, let cookie = named.first,
              cookie.secure, cookie.domain.lowercased() == ".yandex-team.ru",
              cookie.path == "/", cookie.expires == nil || cookie.expires! > now,
              !cookie.value.isEmpty, cookie.value.utf8.count <= 8192,
              !cookie.value.contains(";"), !cookie.value.utf8.contains(13),
              !cookie.value.utf8.contains(10) else { throw AgentError.invalidJob }
        return "Session_id=\(cookie.value)"
    }

    public static func identityStatus(httpStatus: Int, body: Data,
                                      expectedLogin: String) -> CorporateIdentityStatus {
        switch httpStatus {
        case 401: return .reauthRequired
        case 403: return .accessDenied
        case 200: break
        default: return .networkUnavailable
        }
        guard body.count <= 64 * 1024,
              let object = try? JSONSerialization.jsonObject(with: body) as? [String: Any],
              object["dismissed"] as? Bool != true,
              let actual = normalizedLogin(object["login"] as? String),
              let expected = normalizedLogin(expectedLogin) else { return .accessDenied }
        return actual == expected ? .ready : .ownerMismatch
    }

    public static func identityLogin(from body: Data) -> String? {
        guard body.count <= 64 * 1024,
              let object = try? JSONSerialization.jsonObject(with: body) as? [String: Any],
              object["dismissed"] as? Bool != true else { return nil }
        return normalizedLogin(object["login"] as? String)
    }
}
