import Foundation
import CoreFoundation

public enum CorporateReadError: Error, Equatable {
    case invalid, auth, accessDenied, notFound, rateLimited, network, tooLarge

    public var wireCode: String {
        switch self {
        case .auth: return "auth"
        case .accessDenied: return "access_denied"
        case .notFound: return "not_found"
        case .rateLimited: return "rate_limited"
        case .network, .tooLarge: return "network"
        case .invalid: return "invalid"
        }
    }
}

public struct CorporateReadRequest {
    public let service: String
    public let action: String
    public let resource: String
    public let params: [String: Any]
    public let ownerUserID: Int
    public let ownerTrackerLogin: String
    public let requesterUserID: Int
}

public struct CorporateReadPlan {
    public let method: String
    public let url: URL
    public let body: Data?
}

public enum CorporateReadPolicy {
    public static let inventoryHost = "inventory.sdc.yandex-team.ru"
    public static let trackerHost = CorporateAuthPolicy.trackerHost
    public static let maxRequestBytes = 8 * 1024
    public static let maxResponseBytes = 1024 * 1024
    public static let capabilities = ["corporate_read_v1", "corporate_inventory_read_v1", "corporate_tracker_read_v1"]
    public static func inventoryRover(serverAuthorizedVIN: String) throws -> String {
        let vin = try EmergencyTarget.normalizeRobot(serverAuthorizedVIN)
        let digits = String(vin.dropFirst("YASADR".count)).drop { $0 == "0" }
        return "a" + (digits.isEmpty ? "0" : digits)
    }
    public static func admits(inflight: Int) -> Bool { (0..<2).contains(inflight) }
    private static let issuePattern = "^[A-Z][A-Z0-9_]{0,63}-[1-9][0-9]{0,11}$"
    private static let queuePattern = "^[A-Z][A-Z0-9_]{0,63}$"
    private static let userPattern = "^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$"
    private static let roverPattern = "^a[0-9]{1,11}$"
    private static let issueActions = Set(["issue_read", "comments_read", "transitions_read", "history_read"])
    private static let queueActions = Set(["queue_read", "queue_components_read"])
    private static let paginationHeaders = Set(["x-total-count", "x-total-pages", "x-current-page", "x-next-page", "link"])

    public static func decode(_ data: Data, expectedOwnerUserID: Int,
                              expectedOwnerTrackerLogin: String) throws -> CorporateReadRequest {
        guard data.count <= maxRequestBytes,
              let object = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
              Set(object.keys) == Set(["service", "action", "resource", "params", "owner_user_id",
                                       "owner_tracker_login", "requester_user_id"]),
              let service = object["service"] as? String,
              let action = object["action"] as? String,
              let resource = object["resource"] as? String,
              let params = object["params"] as? [String: Any],
              let owner = object["owner_user_id"] as? Int, owner == expectedOwnerUserID,
              let login = CorporateAuthPolicy.normalizedLogin(object["owner_tracker_login"] as? String),
              login == CorporateAuthPolicy.normalizedLogin(expectedOwnerTrackerLogin),
              let requester = object["requester_user_id"] as? Int, requester > 0 else {
            throw CorporateReadError.invalid
        }
        let value = CorporateReadRequest(service: service, action: action, resource: resource,
                                         params: params, ownerUserID: owner,
                                         ownerTrackerLogin: login, requesterUserID: requester)
        _ = try plan(value)
        return value
    }

    public static func plan(_ request: CorporateReadRequest) throws -> CorporateReadPlan {
        if request.service == "inventory" { return try inventoryPlan(request) }
        if request.service == "tracker" { return try trackerPlan(request) }
        throw CorporateReadError.invalid
    }

    private static func inventoryPlan(_ request: CorporateReadRequest) throws -> CorporateReadPlan {
        guard request.resource.range(of: roverPattern, options: .regularExpression) != nil else { throw CorporateReadError.invalid }
        if request.action == "rover_card" {
            guard request.params.isEmpty else { throw CorporateReadError.invalid }
            return CorporateReadPlan(method: "GET", url: URL(string: "https://\(inventoryHost)/api/v1/rovers/\(request.resource)/")!, body: nil)
        }
        guard request.action == "rover_installations",
              Set(request.params.keys).isSubset(of: ["offset", "limit", "slot"]),
              integer(request.params["offset"], default: 0, range: 0...100_000) != nil,
              integer(request.params["limit"], default: 25, range: 1...50) != nil else { throw CorporateReadError.invalid }
        var query = [URLQueryItem(name: "rover", value: request.resource)]
        if let value = request.params["offset"] as? Int { query.append(URLQueryItem(name: "offset", value: String(value))) }
        if let value = request.params["limit"] as? Int { query.append(URLQueryItem(name: "limit", value: String(value))) }
        if let slot = request.params["slot"] {
            guard let value = slot as? String,
                  value.range(of: "^/[A-Za-z0-9_/-]{1,200}$", options: .regularExpression) != nil else { throw CorporateReadError.invalid }
            query.append(URLQueryItem(name: "slot", value: value))
        }
        return CorporateReadPlan(method: "GET", url: try fixedURL(host: inventoryHost,
            path: "/api/v1/installations/", query: query), body: nil)
    }

    private static func trackerPlan(_ request: CorporateReadRequest) throws -> CorporateReadPlan {
        let allowed: Set<String>
        if request.action == "issues_search" { allowed = ["page", "per_page", "expand", "fields", "query", "order", "stale_ok"] }
        else if request.action == "issues_count" { allowed = ["query"] }
        else if ["issue_read", "queue_read"].contains(request.action) { allowed = ["expand", "fields"] }
        else if request.action == "user_read" { allowed = [] }
        else if issueActions.contains(request.action) || request.action == "queue_components_read" {
            allowed = ["page", "per_page", "expand", "fields"]
        } else { throw CorporateReadError.invalid }
        guard Set(request.params.keys).isSubset(of: allowed) else { throw CorporateReadError.invalid }
        if let raw = request.params["stale_ok"] {
            guard let value = raw as? NSNumber, CFGetTypeID(value) == CFBooleanGetTypeID() else { throw CorporateReadError.invalid }
        }
        for key in ["page", "per_page"] where request.params[key] != nil {
            let ceiling = key == "page" ? 10_000 : 100
            guard integer(request.params[key], default: 1, range: 1...ceiling) != nil else { throw CorporateReadError.invalid }
        }
        for key in ["expand", "fields"] where request.params[key] != nil {
            guard let value = request.params[key] as? String, (1...1024).contains(value.count) else { throw CorporateReadError.invalid }
        }
        if let raw = request.params["order"] {
            guard let values = raw as? [String], values.count <= 8,
                  values.allSatisfy({ $0.range(of: "^[-+]?[A-Za-z][A-Za-z0-9_]{0,63}$", options: .regularExpression) != nil })
            else { throw CorporateReadError.invalid }
        }
        if ["issues_search", "issues_count"].contains(request.action) {
            guard request.resource == (request.action == "issues_search" ? "search" : "count"),
                  let query = request.params["query"] as? String, (1...4096).contains(query.count) else { throw CorporateReadError.invalid }
            var body: [String: Any] = ["query": query]
            if let order = request.params["order"] { body["order"] = order }
            let bytes = try JSONSerialization.data(withJSONObject: body, options: [.sortedKeys])
            guard bytes.count <= maxRequestBytes else { throw CorporateReadError.tooLarge }
            let path = request.action == "issues_search" ? "/v2/issues/_search" : "/v2/issues/_count"
            let urlQuery: [URLQueryItem] = request.action == "issues_search" ? request.params
                .filter { ["page", "per_page", "expand", "fields", "stale_ok"].contains($0.key) }
                .sorted(by: { $0.key < $1.key })
                .map { item in
                    if item.key == "stale_ok" {
                        return URLQueryItem(name: "staleOk", value: (item.value as? Bool) == true ? "true" : "false")
                    }
                    return URLQueryItem(name: item.key == "per_page" ? "perPage" : item.key,
                                        value: String(describing: item.value))
                } : []
            return CorporateReadPlan(method: "POST", url: try fixedURL(host: trackerHost, path: path,
                                                                         query: urlQuery), body: bytes)
        }
        let path: String
        if request.action == "user_read" {
            guard request.resource.range(of: userPattern, options: .regularExpression) != nil else { throw CorporateReadError.invalid }
            path = "/v2/users/\(request.resource)"
        } else if issueActions.contains(request.action) {
            guard request.resource.range(of: issuePattern, options: .regularExpression) != nil else { throw CorporateReadError.invalid }
            let suffix = ["issue_read": "", "comments_read": "/comments", "transitions_read": "/transitions", "history_read": "/changelog"][request.action]!
            path = "/v2/issues/\(request.resource)\(suffix)"
        } else {
            guard queueActions.contains(request.action),
                  request.resource.range(of: queuePattern, options: .regularExpression) != nil else { throw CorporateReadError.invalid }
            path = "/v2/queues/\(request.resource)" + (request.action == "queue_components_read" ? "/components" : "")
        }
        let query = request.params.sorted(by: { $0.key < $1.key }).map {
            URLQueryItem(name: $0.key == "per_page" ? "perPage" : $0.key, value: String(describing: $0.value))
        }
        return CorporateReadPlan(method: "GET", url: try fixedURL(host: trackerHost, path: path, query: query), body: nil)
    }

    public static func result(status: Int, data: Data, headers rawHeaders: [AnyHashable: Any],
                              request: CorporateReadRequest) throws -> [String: Any] {
        switch status {
        case 401: throw CorporateReadError.auth
        case 403: throw CorporateReadError.accessDenied
        case 404: throw CorporateReadError.notFound
        case 429: throw CorporateReadError.rateLimited
        case 200...299: break
        default: throw CorporateReadError.network
        }
        guard data.count <= maxResponseBytes else { throw CorporateReadError.tooLarge }
        let decoded = try JSONSerialization.jsonObject(with: data, options: [.fragmentsAllowed])
        let payload: Any
        if request.action == "issues_count", let count = decoded as? Int, count >= 0 { payload = ["count": count] }
        else if decoded is [String: Any] || decoded is [Any] { payload = decoded }
        else { throw CorporateReadError.invalid }
        var headers: [String: String] = [:]
        for (key, value) in rawHeaders {
            let normalized = String(describing: key).lowercased()
            if paginationHeaders.contains(normalized) {
                let text = String(describing: value)
                guard text.utf8.count <= 4096 else { throw CorporateReadError.invalid }
                headers[normalized] = text
            }
        }
        return ["service": request.service, "action": request.action, "resource": request.resource,
                "owner_user_id": request.ownerUserID, "identity_login": request.ownerTrackerLogin,
                "status_code": status, "headers": headers, "payload": payload]
    }

    private static func integer(_ value: Any?, default defaultValue: Int, range: ClosedRange<Int>) -> Int? {
        if value == nil { return range.contains(defaultValue) ? defaultValue : nil }
        guard let raw = value as? NSNumber, CFGetTypeID(raw) != CFBooleanGetTypeID(),
              raw.doubleValue == Double(raw.intValue), range.contains(raw.intValue) else { return nil }
        return raw.intValue
    }

    private static func fixedURL(host: String, path: String, query: [URLQueryItem] = []) throws -> URL {
        var parts = URLComponents(); parts.scheme = "https"; parts.host = host; parts.path = path
        parts.queryItems = query.isEmpty ? nil : query
        guard let url = parts.url, url.host == host, url.port == nil, url.user == nil, url.password == nil else {
            throw CorporateReadError.invalid
        }
        return url
    }
}
