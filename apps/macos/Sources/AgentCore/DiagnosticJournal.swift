import Foundation

public struct DiagnosticEvent: Codable, Sendable {
    public let id: Int
    public let time: String
    public let service: String
    public let stage: String
    public let status: String
    public let code: String?
    public let elapsedMs: Int?
}

public struct DiagnosticRequest: Sendable, Equatable {
    public let service: String
    public let route: String
    public let robot: String?

    public init(_ object: [String: Any]) throws {
        guard Set(object.keys).isSubset(of: ["service", "route", "robot"]),
              let service = object["service"] as? String,
              ["server", "emergency", "inventory", "tracker"].contains(service),
              let requestedRoute = object["route"] as? String,
              ["direct", "server"].contains(requestedRoute) else {
            throw DiagnosticRequestError.invalid
        }
        self.service = service
        route = service == "server" ? "direct" : requestedRoute
        if service == "server" {
            if let value = object["robot"] {
                guard let value = value as? String, let number = Int(value),
                      (1000...3000).contains(number) else { throw DiagnosticRequestError.invalid }
            }
            robot = nil
        } else {
            guard let value = object["robot"] as? String, let number = Int(value),
                  (1000...3000).contains(number) else { throw DiagnosticRequestError.invalid }
            robot = String(number)
        }
    }
}

public enum DiagnosticRequestError: Error { case invalid }

public struct DiagnosticResult: Codable, Sendable {
    public let service: String
    public let route: String
    public let robot: String?
    public let status: String
    public let httpStatus: Int?
    public let code: String?
    public let elapsedMs: Int

    public init(service: String, route: String, robot: String?, status: String,
                httpStatus: Int?, code: String?, elapsedMs: Int) {
        self.service = service; self.route = route; self.robot = robot; self.status = status
        self.httpStatus = httpStatus; self.code = code; self.elapsedMs = elapsedMs
    }
}

@MainActor public final class DiagnosticRunCoordinator {
    public private(set) var running = false
    public private(set) var results: [DiagnosticResult] = []
    private var task: Task<Void, Never>?

    public init() {}

    public func start(_ operation: @escaping @MainActor () async -> DiagnosticResult) -> Bool {
        guard !running else { return false }
        running = true; results = []
        task = Task { @MainActor [weak self] in
            guard let self else { return }
            let result = await operation()
            guard !Task.isCancelled else { return }
            results = [result]; running = false; task = nil
        }
        return true
    }
}

/// Memory-only, bounded diagnostics. Callers provide enumerated values; URLs,
/// credentials, headers and response payloads are deliberately not accepted.
public final class DiagnosticJournal: @unchecked Sendable {
    private let lock = NSLock()
    private let capacity: Int
    private var nextID = 1
    private var events: [DiagnosticEvent] = []

    public init(capacity: Int = 100) {
        self.capacity = min(100, max(1, capacity))
    }

    public func append(service: String, stage: String, status: String,
                       code: String? = nil, elapsedMs: Int? = nil, at: Date = Date()) {
        guard Self.safe(service), Self.safe(stage), Self.safe(status),
              code.map(Self.safe) ?? true else { return }
        let formatter = ISO8601DateFormatter()
        formatter.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        lock.lock()
        events.append(DiagnosticEvent(id: nextID, time: formatter.string(from: at),
            service: service, stage: stage, status: status, code: code,
            elapsedMs: elapsedMs.map { min(300_000, max(0, $0)) }))
        nextID += 1
        if events.count > capacity { events.removeFirst(events.count - capacity) }
        lock.unlock()
    }

    public func snapshot() -> [DiagnosticEvent] {
        lock.lock(); defer { lock.unlock() }
        return events
    }

    private static func safe(_ value: String) -> Bool {
        !value.isEmpty && value.utf8.count <= 64 && value.range(
            of: "^[a-z0-9_.-]+$", options: .regularExpression) != nil
    }
}
