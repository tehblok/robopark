import Foundation
import CoreFoundation
import CryptoKit

public enum CorporateWriteError: Error, Equatable {
    case invalid, auth, accessDenied, notFound, rateLimited, network
    case outcomeUnknown, conflict, receiptStorage, busy

    public var wireCode: String {
        switch self {
        case .invalid: return "invalid"
        case .auth: return "auth"
        case .accessDenied: return "access_denied"
        case .notFound: return "not_found"
        case .rateLimited: return "rate_limited"
        case .network: return "network"
        case .outcomeUnknown: return "outcome_unknown"
        case .conflict: return "conflict"
        case .receiptStorage: return "receipt_storage"
        case .busy: return "busy"
        }
    }
}

public struct CorporateWriteRequest {
    public let action: String
    public let resource: String
    public let params: [String: Any]
    public let ownerUserID: Int
    public let ownerTrackerLogin: String
    public let requesterUserID: Int
    public let commandID: String
    public let stepID: String
}

public struct CorporateWritePlan {
    public let method: String
    public let url: URL
    public let body: Data
    public let contentType: String
    public let ifMatch: String?
}

public enum CorporateWriteReceiptState: String, Codable { case prepared, executing, unknown, completed }
public enum CorporateWriteReceiptDisposition: Equatable {
    case execute, replay(Data), outcomeUnknown
}

public struct CorporateWriteReceiptIndex: Codable, Equatable {
    public static let capacity = 256
    public private(set) var accounts: [String]
    public init(accounts: [String] = []) { self.accounts = accounts }
    public mutating func reserve(_ account: String) throws {
        guard !account.isEmpty, account.utf8.count <= 128 else { throw CorporateWriteError.receiptStorage }
        if accounts.contains(account) { return }
        guard accounts.count < Self.capacity else { throw CorporateWriteError.busy }
        accounts.append(account)
    }
    public mutating func releaseAcknowledged(_ account: String) {
        accounts.removeAll { $0 == account }
    }
}

public struct CorporateWriteAdmission: Equatable {
    public private(set) var attemptID: String?
    public init() {}
    public mutating func acquire(_ attemptID: String) throws {
        guard self.attemptID == nil else { throw CorporateWriteError.busy }
        self.attemptID = attemptID
    }
    public mutating func release(_ attemptID: String) {
        if self.attemptID == attemptID { self.attemptID = nil }
    }
}

public struct CorporateWriteReceipt: Codable, Equatable {
    public let ownerUserID: Int
    public let deviceID: String
    public let commandID: String
    public let stepID: String
    public let digest: String
    public var state: CorporateWriteReceiptState
    public var wireResult: Data?

    public init(ownerUserID: Int, deviceID: String, commandID: String, stepID: String,
                digest: String, state: CorporateWriteReceiptState = .prepared, wireResult: Data? = nil) {
        self.ownerUserID = ownerUserID; self.deviceID = deviceID; self.commandID = commandID
        self.stepID = stepID; self.digest = digest; self.state = state; self.wireResult = wireResult
    }

    public func validate(ownerUserID: Int, deviceID: String, digest: String) throws {
        guard self.ownerUserID == ownerUserID, self.deviceID == deviceID, self.digest == digest else {
            throw CorporateWriteError.conflict
        }
    }
    public func permitsAcknowledgement(ownerUserID: Int, deviceID: String,
                                       commandID: String, stepID: String) -> Bool {
        state == .completed && self.ownerUserID == ownerUserID && self.deviceID == deviceID
            && self.commandID == commandID && self.stepID == stepID
    }
    public func disposition(ownerUserID: Int, deviceID: String, digest: String) throws -> CorporateWriteReceiptDisposition {
        try validate(ownerUserID: ownerUserID, deviceID: deviceID, digest: digest)
        switch state {
        case .prepared: return .execute
        case .executing, .unknown: return .outcomeUnknown
        case .completed:
            guard let wireResult else { throw CorporateWriteError.receiptStorage }
            return .replay(wireResult)
        }
    }
}

public enum CorporateWritePolicy {
    public static let operation = "corporate_write_v1"
    public static let maxRequestBytes = 6 * 1024 * 1024
    public static let maxResponseBytes = 16 * 1024
    public static let capability = "corporate_tracker_write_v1"
    private static let issuePattern = "^[A-Z][A-Z0-9_]{0,63}-[1-9][0-9]{0,11}$"
    private static let stepPattern = "^[a-z0-9_:-]{1,64}$"
    private static let transitionPattern = "^[0-9A-Za-z_-]{1,128}$"
    private static let patchFields = Set(["assignee", "tags", "components",
        "60df26695151a36df681d67b--theDefectCode", "solutionMethod"])
    private static let attachmentTypes = Set(["image/jpeg", "image/jpg", "image/png", "image/webp", "image/heic", "image/heif"])

    public static func decode(_ data: Data, expectedOwnerUserID: Int,
                              expectedOwnerTrackerLogin: String) throws -> CorporateWriteRequest {
        guard data.count <= maxRequestBytes,
              let object = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
              Set(object.keys) == Set(["service", "action", "resource", "params", "owner_user_id",
                "owner_tracker_login", "requester_user_id", "command_id", "step_id"]),
              object["service"] as? String == "tracker",
              let action = object["action"] as? String,
              let resource = object["resource"] as? String,
              resource.range(of: issuePattern, options: .regularExpression) != nil,
              let params = object["params"] as? [String: Any],
              let owner = object["owner_user_id"] as? Int, owner == expectedOwnerUserID,
              let login = CorporateAuthPolicy.normalizedLogin(object["owner_tracker_login"] as? String),
              login == CorporateAuthPolicy.normalizedLogin(expectedOwnerTrackerLogin),
              let requester = object["requester_user_id"] as? Int, requester > 0,
              let command = object["command_id"] as? String, UUID(uuidString: command) != nil,
              let step = object["step_id"] as? String,
              step.range(of: stepPattern, options: .regularExpression) != nil else { throw CorporateWriteError.invalid }
        let request = CorporateWriteRequest(action: action, resource: resource, params: params,
            ownerUserID: owner, ownerTrackerLogin: login, requesterUserID: requester,
            commandID: command.lowercased(), stepID: step)
        _ = try plan(request)
        return request
    }

    public static func plan(_ request: CorporateWriteRequest) throws -> CorporateWritePlan {
        let base = "https://\(CorporateAuthPolicy.trackerHost)/v2/issues/\(request.resource)"
        switch request.action {
        case "comment_create":
            guard Set(request.params.keys).isSubset(of: ["text", "attachment_ids"]),
                  let text = request.params["text"] as? String, !text.isEmpty,
                  text.utf8.count <= 64 * 1024 else { throw CorporateWriteError.invalid }
            var body: [String: Any] = ["text": text]
            if let raw = request.params["attachment_ids"] {
                guard let ids = raw as? [String], ids.count <= 8,
                      ids.allSatisfy({ !$0.isEmpty && $0.utf8.count <= 128 }) else { throw CorporateWriteError.invalid }
                body["attachmentIds"] = ids
            }
            return try jsonPlan(method: "POST", url: URL(string: base + "/comments")!, body: body)
        case "issue_patch":
            guard Set(request.params.keys).isSubset(of: ["fields", "version"]),
                  let fields = request.params["fields"] as? [String: Any], !fields.isEmpty,
                  Set(fields.keys).isSubset(of: patchFields) else { throw CorporateWriteError.invalid }
            try validatePatch(fields)
            let url = URL(string: base)!
            var ifMatch: String?
            if let version = request.params["version"] {
                guard let value = version as? String,
                      value.range(of: "^[0-9]{1,20}$", options: .regularExpression) != nil
                else { throw CorporateWriteError.invalid }
                ifMatch = value
            }
            return try jsonPlan(method: "PATCH", url: url, body: fields, ifMatch: ifMatch)
        case "transition_execute":
            guard Set(request.params.keys).isSubset(of: ["transition", "resolution"]),
                  let transition = request.params["transition"] as? String,
                  transition.range(of: transitionPattern, options: .regularExpression) != nil else { throw CorporateWriteError.invalid }
            var body: [String: Any] = [:]
            if let resolution = request.params["resolution"] {
                guard let value = resolution as? String, !value.isEmpty, value.utf8.count <= 128 else { throw CorporateWriteError.invalid }
                body["resolution"] = value
            }
            return try jsonPlan(method: "POST", url: URL(string: base + "/transitions/\(transition)/_execute")!, body: body)
        case "attachment_upload":
            guard Set(request.params.keys) == Set(["filename", "content_type", "data_base64"]),
                  let filename = request.params["filename"] as? String,
                  !filename.isEmpty, filename.utf8.count <= 255,
                  filename == URL(fileURLWithPath: filename).lastPathComponent,
                  filename != ".", filename != "..",
                  !filename.contains("/") && !filename.contains("\\") && !filename.utf8.contains(13) && !filename.utf8.contains(10),
                  let contentType = request.params["content_type"] as? String, attachmentTypes.contains(contentType),
                  let encoded = request.params["data_base64"] as? String,
                  let content = Data(base64Encoded: encoded), !content.isEmpty, content.count <= 4 * 1024 * 1024 else {
                throw CorporateWriteError.invalid
            }
            let boundary = "robopark-" + request.commandID.replacingOccurrences(of: "-", with: "")
            var body = Data("--\(boundary)\r\nContent-Disposition: form-data; name=\"file\"; filename=\"\(filename)\"\r\nContent-Type: \(contentType)\r\n\r\n".utf8)
            body.append(content); body.append(Data("\r\n--\(boundary)--\r\n".utf8))
            guard body.count <= maxRequestBytes else { throw CorporateWriteError.invalid }
            return CorporateWritePlan(method: "POST",
                                      url: URL(string: "https://\(CorporateAuthPolicy.trackerHost)/v2/attachments/")!,
                                      body: body, contentType: "multipart/form-data; boundary=\(boundary)",
                                      ifMatch: nil)
        default: throw CorporateWriteError.invalid
        }
    }

    public static func result(status: Int, data: Data, request: CorporateWriteRequest) throws -> [String: Any] {
        switch status {
        case 401: throw CorporateWriteError.auth
        case 403: throw CorporateWriteError.accessDenied
        case 404: throw CorporateWriteError.notFound
        case 409: throw CorporateWriteError.conflict
        case 429: throw CorporateWriteError.rateLimited
        case 200...299: break
        default: throw CorporateWriteError.network
        }
        guard data.count <= maxResponseBytes else { throw CorporateWriteError.network }
        let raw: [String: Any]
        if data.isEmpty { raw = [:] }
        else {
            guard let object = try? JSONSerialization.jsonObject(with: data) as? [String: Any] else {
                throw CorporateWriteError.invalid
            }
            raw = object
        }
        let payload: [String: Any]
        if request.action == "comment_create" || request.action == "attachment_upload" {
            let id: String
            let rawID = raw["id"] ?? raw["longId"]
            if let text = rawID as? String { id = text }
            else if let number = rawID as? NSNumber,
                    CFGetTypeID(number) != CFBooleanGetTypeID(),
                    number.doubleValue.isFinite,
                    number.doubleValue.rounded() == number.doubleValue {
                id = number.stringValue
            } else { throw CorporateWriteError.invalid }
            guard !id.isEmpty, id.utf8.count <= 256 else { throw CorporateWriteError.invalid }
            payload = ["id": id]
        } else { payload = [:] }
        return ["service": "tracker", "action": request.action, "resource": request.resource,
            "owner_user_id": request.ownerUserID, "identity_login": request.ownerTrackerLogin,
            "status_code": status, "payload": payload,
            "command_id": request.commandID, "step_id": request.stepID]
    }

    public static func receiptAccount(ownerUserID: Int, deviceID: String, commandID: String, stepID: String) -> String {
        let raw = "\(ownerUserID)\n\(deviceID)\n\(commandID.lowercased())\n\(stepID)"
        return "corporate-write-receipt-v1-" + SHA256Digest.hex(Data(raw.utf8))
    }

    private static func validatePatch(_ fields: [String: Any]) throws {
        for (key, value) in fields {
            switch key {
            case "assignee":
                guard value is NSNull || ((value as? String).map { !$0.isEmpty && $0.utf8.count <= 128 } ?? false)
                else { throw CorporateWriteError.invalid }
            case "tags", "components":
                guard let list = value as? [String], list.count <= 64,
                      list.allSatisfy({ !$0.isEmpty && $0.utf8.count <= 256 }) else { throw CorporateWriteError.invalid }
            default:
                guard let text = value as? String, !text.isEmpty, text.utf8.count <= 1024 else {
                    throw CorporateWriteError.invalid
                }
            }
        }
    }

    private static func jsonPlan(method: String, url: URL, body: [String: Any],
                                 ifMatch: String? = nil) throws -> CorporateWritePlan {
        let data = try JSONSerialization.data(withJSONObject: body, options: [.sortedKeys])
        guard data.count <= maxRequestBytes else { throw CorporateWriteError.invalid }
        return CorporateWritePlan(method: method, url: url, body: data,
                                  contentType: "application/json", ifMatch: ifMatch)
    }

    private static func fixedURL(path: String, query: [URLQueryItem]) throws -> URL {
        var parts = URLComponents(); parts.scheme = "https"; parts.host = CorporateAuthPolicy.trackerHost
        parts.path = path; parts.queryItems = query
        guard let url = parts.url else { throw CorporateWriteError.invalid }; return url
    }
}

private enum SHA256Digest {
    static func hex(_ data: Data) -> String {
        // Kept dependency-free in AgentCore; CryptoKit is already linked by Protocol.swift.
        SHA256.hash(data: data).map { String(format: "%02x", $0) }.joined()
    }
}
