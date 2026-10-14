import Foundation

public struct NativeInboxEvent: Equatable, Sendable {
    public let id: String
    public let eventType: String
    public let protectedText: String
    public let unread: Bool

    public init(id: String, eventType: String, protectedText: String, unread: Bool) {
        self.id = id; self.eventType = eventType; self.protectedText = protectedText; self.unread = unread
    }
}

public struct NativeMaintenanceStatus: Equatable, Sendable {
    public let active: Bool
    public let kind: String
    public let operatorName: String
}

public enum NativeMaintenanceTransition: Equatable {
    case started(String), recovered(String), unchanged
}

public struct NativeMaintenanceEpisodePolicy {
    private var fingerprint: String?
    private var episodeID: String?
    public init() {}

    public mutating func update(active: Bool, fingerprint next: String,
                                makeEpisodeID: () -> String = { UUID().uuidString.lowercased() }) -> NativeMaintenanceTransition {
        if active {
            if fingerprint == next, episodeID != nil { return .unchanged }
            let episode = makeEpisodeID(); fingerprint = next; episodeID = episode
            return .started(episode)
        }
        guard let episode = episodeID else { fingerprint = nil; return .unchanged }
        fingerprint = nil; episodeID = nil
        return .recovered(episode)
    }
}

public enum NativeIncidentSignal: Equatable { case serverIssue, networkIssue, recovered }

public struct NativeIncidentPolicy {
    private var failures = 0
    private var active: NativeIncidentSignal?
    public init() {}

    public mutating func failed(isRoyal: Bool, networkAvailable: Bool) -> NativeIncidentSignal? {
        guard isRoyal else { failures = 0; active = nil; return nil }
        failures += 1
        guard failures >= 3, active == nil else { return nil }
        let signal: NativeIncidentSignal = networkAvailable ? .serverIssue : .networkIssue
        active = signal
        return signal
    }

    public mutating func succeeded(isRoyal: Bool) -> NativeIncidentSignal? {
        failures = 0
        guard isRoyal, active != nil else { active = nil; return nil }
        active = nil
        return .recovered
    }
}

public enum NativeNotificationPolicy {
    public static func parseMaintenance(_ data: Data) throws -> NativeMaintenanceStatus {
        guard data.count <= 64 * 1024,
              let row = try JSONSerialization.jsonObject(with: data) as? [String: Any],
              let active = row["active"] as? Bool else { throw AgentError.invalidJob }
        let kind = row["kind"] as? String ?? "maintenance"
        let operatorName = row["operator"] as? String ?? ""
        guard kind.range(of: "^[a-z][a-z0-9_]{0,63}$", options: .regularExpression) != nil,
              operatorName.utf8.count <= 256 else { throw AgentError.invalidJob }
        return NativeMaintenanceStatus(active: active, kind: kind, operatorName: operatorName)
    }

    public static func parseInbox(_ data: Data) throws -> [NativeInboxEvent] {
        guard data.count <= 2 * 1024 * 1024,
              let rows = try JSONSerialization.jsonObject(with: data) as? [[String: Any]], rows.count <= 500 else {
            throw AgentError.invalidJob
        }
        return try rows.map { row in
            let id: String
            if let value = row["id"] as? String { id = value }
            else if let value = row["id"] as? NSNumber { id = value.stringValue }
            else { throw AgentError.invalidJob }
            guard id.range(of: "^[A-Za-z0-9._:-]{1,128}$", options: .regularExpression) != nil,
                  let eventType = row["event_type"] as? String,
                  eventType.range(of: "^[a-z][a-z0-9_]{0,63}$", options: .regularExpression) != nil,
                  let text = row["protected_text"] as? String,
                  !text.isEmpty, text.utf8.count <= 4000 else { throw AgentError.invalidJob }
            return NativeInboxEvent(id: id, eventType: eventType, protectedText: text,
                                    unread: row["read_at"] is NSNull || row["read_at"] == nil)
        }
    }

    public static func title(for eventType: String) -> String {
        switch eventType {
        case "new_task", "review_task": return "Новая задача"
        case "return": return "Возврат"
        case "operator_comment": return "Комментарий оператора"
        case "report": return "Новый отчёт"
        case "problem", "anomaly": return "Проблема требует внимания"
        case "system_problem": return "Проблема системы"
        case "network_problem": return "Проблема сети"
        case "server_problem": return "Проблема сервера"
        case "integration_down": return "Интеграция недоступна"
        case "disk_low": return "Заканчивается место на диске"
        case "update_failure": return "Ошибка обновления"
        case "maintenance": return "Технические работы"
        case "announcement": return "Объявление"
        case "action_required": return "Требуется действие"
        case "system_recovered": return "Система восстановлена"
        default: return "Новое уведомление Robopark"
        }
    }
}
