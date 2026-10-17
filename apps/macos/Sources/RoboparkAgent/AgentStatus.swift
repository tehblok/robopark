import Foundation

struct AgentStatusSnapshot {
    let accountState: String
    let accountLabel: String
    let accountRole: String?
    let registered: Bool
    let serverState: String
    let serverLastSuccessAt: Date?
    let ownerLoginConfigured: Bool
    let probeConfigured: Bool
    let tracker: String
    let inventory: String
    let emergency: String
    let corporateChecking: Bool
    let corporateMessage: String?
    let corporateCanLogin: Bool
    let autostartSupported: Bool
    let autostartEnabled: Bool
    let paused: Bool
    let legacyWorkspaceAvailable: Bool?
    let updateState: String
    let currentVersion: String
    let availableVersion: String?
    let updateMessage: String?
}

struct DesktopAgentStatus {
    let snapshot: AgentStatusSnapshot

    var jsonObject: [String: Any] {
        let corporateState: String
        let services = [snapshot.tracker, snapshot.emergency]
        if services.contains("denied") {
            corporateState = "denied"
        } else if services.contains("unavailable") {
            corporateState = "unavailable"
        } else if services.contains("login_required") {
            corporateState = "login_required"
        } else if snapshot.corporateChecking || services.contains("checking") || services.contains("unverified") {
            corporateState = "checking"
        } else {
            corporateState = Self.requiredCorporateServicesReady(
                tracker: snapshot.tracker,
                emergency: snapshot.emergency
            ) ? "ready" : "unavailable"
        }
        return [
            "version": 1,
            "account": [
                "state": snapshot.accountState,
                "label": snapshot.accountLabel,
                "role": Self.nullable(snapshot.accountRole),
                "registered": snapshot.registered,
            ],
            "service": ["state": "running"],
            "server": [
                "state": snapshot.serverState,
                "lastSuccessAt": Self.nullable(snapshot.serverLastSuccessAt.map(Self.timestamp)),
            ],
            // Offline queue counters live in browser IndexedDB. The native
            // process must not present made-up zeroes as a successful sync.
            "sync": NSNull(),
            "corporate": [
                "state": corporateState,
                "canLogin": snapshot.corporateCanLogin,
                "ownerLoginConfigured": snapshot.ownerLoginConfigured,
                "probeConfigured": snapshot.probeConfigured,
                "tracker": snapshot.tracker,
                "inventory": snapshot.inventory,
                "emergency": snapshot.emergency,
                "message": Self.nullable(snapshot.corporateMessage),
            ],
            "autostart": [
                "supported": snapshot.autostartSupported,
                "enabled": snapshot.autostartEnabled,
            ],
            "work": ["paused": snapshot.paused],
            "legacyWorkspaceAvailable": Self.nullable(snapshot.legacyWorkspaceAvailable),
            "update": [
                "state": snapshot.updateState,
                "currentVersion": snapshot.currentVersion,
                "availableVersion": Self.nullable(snapshot.availableVersion),
                "message": Self.nullable(snapshot.updateMessage),
            ],
        ]
    }

    static func requiredCorporateServicesReady(tracker: String, emergency: String) -> Bool {
        [tracker, emergency].allSatisfy { $0 == "ready" }
    }

    private static func timestamp(_ value: Date) -> String {
        let formatter = ISO8601DateFormatter()
        formatter.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        return formatter.string(from: value)
    }

    private static func nullable<T>(_ value: T?) -> Any {
        if let value { return value }
        return NSNull()
    }
}

enum DesktopAgentAction: Equatable {
    case connect
    case corporateLogin
    case autostart(Bool)
    case pause(Bool)
    case checkUpdate
    case reloadKeychain
    case openLegacyWorkspace

    init(body: [String: Any]) throws {
        guard let name = body["action"] as? String else { throw AgentActionError.invalid }
        switch name {
        case "connect":
            guard body.keys.count == 1 else { throw AgentActionError.invalid }
            self = .connect
        case "corporateLogin":
            guard body.keys.count == 1 else { throw AgentActionError.invalid }
            self = .corporateLogin
        case "checkUpdate":
            guard body.keys.count == 1 else { throw AgentActionError.invalid }
            self = .checkUpdate
        case "reloadKeychain":
            guard body.keys.count == 1 else { throw AgentActionError.invalid }
            self = .reloadKeychain
        case "openLegacyWorkspace":
            guard body.keys.count == 1 else { throw AgentActionError.invalid }
            self = .openLegacyWorkspace
        case "autostart":
            guard body.keys.count == 2, let enabled = body["enabled"] as? Bool else {
                throw AgentActionError.invalid
            }
            self = .autostart(enabled)
        case "pause":
            guard body.keys.count == 2, let enabled = body["enabled"] as? Bool else {
                throw AgentActionError.invalid
            }
            self = .pause(enabled)
        default:
            throw AgentActionError.invalid
        }
    }
}

enum AgentActionError: Error {
    case invalid
}
