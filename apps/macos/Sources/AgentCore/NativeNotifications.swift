import Foundation

public struct NativeInboxEvent: Equatable, Sendable {
    public let id: String
    public let eventType: String
    public let protectedText: String
    public let unread: Bool
    public let createdAt: Date?

    public init(id: String, eventType: String, protectedText: String, unread: Bool, createdAt: Date? = nil) {
        self.id = id; self.eventType = eventType; self.protectedText = protectedText
        self.unread = unread; self.createdAt = createdAt
    }
}

public struct NativeNotificationDeliveryPlan: Equatable, Sendable {
    public let deliver: [NativeInboxEvent]
    public let suppress: [NativeInboxEvent]
}

public struct NativeNotificationReceiptLedger: Sendable {
    private enum State: String, Codable, Sendable { case reserved, complete, suppressed }
    private struct Entry: Codable, Sendable {
        let accountID: Int
        let eventID: String
        var state: State
        var reservationID: String?
        var updatedAt: TimeInterval
    }

    private static let retention: TimeInterval = 25 * 60 * 60
    private static let capacity = 512
    private var entries: [Entry]

    public init(data: Data?, now: Date) {
        let decoded = data.flatMap { try? JSONDecoder().decode([Entry].self, from: $0) } ?? []
        let cutoff = now.timeIntervalSince1970 - Self.retention
        entries = Array(decoded.filter { $0.updatedAt >= cutoff }.suffix(Self.capacity))
    }

    public mutating func reserve(accountID: Int, eventID: String, reservationID: String, now: Date) -> Bool {
        if let index = entries.lastIndex(where: { $0.accountID == accountID && $0.eventID == eventID }) {
            switch entries[index].state {
            case .complete, .suppressed: return false
            case .reserved:
                guard entries[index].reservationID != reservationID else { return false }
                entries[index].reservationID = reservationID
                entries[index].updatedAt = now.timeIntervalSince1970
                return true
            }
        }
        entries.append(Entry(accountID: accountID, eventID: eventID, state: .reserved,
                             reservationID: reservationID, updatedAt: now.timeIntervalSince1970))
        trim(now: now)
        return true
    }

    public mutating func complete(accountID: Int, eventID: String, reservationID: String, now: Date) {
        guard let index = entries.lastIndex(where: {
            $0.accountID == accountID && $0.eventID == eventID &&
            $0.state == .reserved && $0.reservationID == reservationID
        }) else { return }
        entries[index].state = .complete
        entries[index].reservationID = nil
        entries[index].updatedAt = now.timeIntervalSince1970
        trim(now: now)
    }

    public mutating func suppress(accountID: Int, eventID: String, now: Date) {
        entries.removeAll { $0.accountID == accountID && $0.eventID == eventID }
        entries.append(Entry(accountID: accountID, eventID: eventID, state: .suppressed,
                             reservationID: nil, updatedAt: now.timeIntervalSince1970))
        trim(now: now)
    }

    public mutating func release(accountID: Int, eventID: String, reservationID: String) {
        entries.removeAll {
            $0.accountID == accountID && $0.eventID == eventID &&
            $0.state == .reserved && $0.reservationID == reservationID
        }
    }

    public func ownsReservation(accountID: Int, eventID: String, reservationID: String) -> Bool {
        entries.last(where: { $0.accountID == accountID && $0.eventID == eventID })
            .map { $0.state == .reserved && $0.reservationID == reservationID } ?? false
    }

    public func recentDelivery(accountID: Int, eventIDPrefixes: [String],
                               since: Date) -> (eventID: String, completed: Bool)? {
        let cutoff = since.timeIntervalSince1970
        guard let entry = entries.reversed().first(where: {
            $0.accountID == accountID && eventIDPrefixes.contains(where: $0.eventID.hasPrefix) &&
                $0.state != .suppressed && $0.updatedAt >= cutoff
        }) else { return nil }
        return (entry.eventID, entry.state == .complete)
    }

    public mutating func remove(accountID: Int) {
        entries.removeAll { $0.accountID == accountID }
    }

    public func encoded() throws -> Data { try JSONEncoder().encode(entries) }

    private mutating func trim(now: Date) {
        let cutoff = now.timeIntervalSince1970 - Self.retention
        entries = Array(entries.filter { $0.updatedAt >= cutoff }.suffix(Self.capacity))
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
    private static let freshWindow: TimeInterval = 10 * 60
    private static let historicalWorkLimit = 3
    private static let workEvents: Set<String> = [
        "new_task", "review_task", "return", "operator_comment", "report",
        "announcement", "action_required", "call", "prize", "problem",
    ]

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
            let createdAt: Date?
            if let raw = row["created_at"] as? String {
                createdAt = ISO8601DateFormatter.roboparkDate(from: raw)
                guard createdAt != nil else { throw AgentError.invalidJob }
            } else { createdAt = nil }
            return NativeInboxEvent(id: id, eventType: eventType, protectedText: text,
                                    unread: row["read_at"] is NSNull || row["read_at"] == nil,
                                    createdAt: createdAt)
        }
    }

    public static func deliveryPlan(events: [NativeInboxEvent], accountID: Int? = nil,
                                    ledger: NativeNotificationReceiptLedger? = nil,
                                    now: Date = Date()) -> NativeNotificationDeliveryPlan {
        let unread = events.filter(\.unread).sorted {
            ($0.createdAt ?? .distantPast) > ($1.createdAt ?? .distantPast)
        }
        let cutoff = now.addingTimeInterval(-freshWindow)
        var deliveredIDs = Set<String>()
        var deliver: [NativeInboxEvent] = []

        for event in unread where (workEvents.contains(event.eventType) || event.eventType == "maintenance")
            && (event.createdAt.map { $0 >= cutoff } ?? false) {
            if deliveredIDs.insert(event.id).inserted { deliver.append(event) }
        }
        let health = unread.filter {
            !workEvents.contains($0.eventType) && $0.eventType != "maintenance"
                && ($0.createdAt.map { $0 >= cutoff } ?? false)
        }
        if let latest = health.first {
            if let accountID, let ledger {
                let phase = latest.eventType == "system_recovered" ? "recovery" : "issue"
                let prefix = "health-window-\(phase)-"
                let previous = ledger.recentDelivery(accountID: accountID,
                    eventIDPrefixes: ["health-window-issue-", "health-window-recovery-"], since: cutoff)
                let sameEpisodePhase = previous?.eventID.hasPrefix(prefix) == true
                if !sameEpisodePhase || previous?.completed != true {
                    let eventType = health.count == 1 || phase == "recovery" ? latest.eventType : "health_summary"
                    let text = health.count == 1 ? latest.protectedText
                        : "Технических событий за последние 10 минут: \(health.count). Подробности — в уведомлениях Robopark."
                    let eventID = sameEpisodePhase ? previous!.eventID : prefix + latest.id
                    deliver.append(NativeInboxEvent(id: eventID, eventType: eventType,
                        protectedText: text, unread: true, createdAt: latest.createdAt))
                }
            } else if health.count == 1 {
                deliveredIDs.insert(latest.id); deliver.append(latest)
            } else {
                deliver.append(NativeInboxEvent(id: "health-summary-" + latest.id, eventType: "health_summary",
                    protectedText: "Технических событий за последние 10 минут: \(health.count). Подробности — в уведомлениях Robopark.",
                    unread: true, createdAt: latest.createdAt))
            }
        }
        for event in unread where workEvents.contains(event.eventType) && (event.createdAt.map { $0 < cutoff } ?? true) {
            guard deliver.filter({ ($0.createdAt.map { $0 < cutoff } ?? true) && workEvents.contains($0.eventType) }).count
                    < historicalWorkLimit else { break }
            if deliveredIDs.insert(event.id).inserted { deliver.append(event) }
        }
        return NativeNotificationDeliveryPlan(
            deliver: deliver,
            suppress: unread.filter { !deliveredIDs.contains($0.id) }
        )
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
        case "health_summary": return "Состояние системы Robopark"
        default: return "Новое уведомление Robopark"
        }
    }
}

private extension ISO8601DateFormatter {
    static let roboparkFractional: ISO8601DateFormatter = {
        let formatter = ISO8601DateFormatter()
        formatter.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        return formatter
    }()
    static let roboparkWholeSeconds = ISO8601DateFormatter()
    static func roboparkDate(from value: String) -> Date? {
        roboparkFractional.date(from: value) ?? roboparkWholeSeconds.date(from: value)
    }
}
