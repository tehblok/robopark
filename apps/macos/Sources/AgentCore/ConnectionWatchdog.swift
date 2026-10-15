import Foundation

public enum AsyncDeadlineError: Error {
    case timedOut
}

private final class AsyncDeadlineRace<Value: Sendable>: @unchecked Sendable {
    private let lock = NSLock()
    private var continuation: CheckedContinuation<Value, Error>?
    private var result: Result<Value, Error>?
    private var tasks: [Task<Void, Never>] = []

    func install(_ continuation: CheckedContinuation<Value, Error>) {
        lock.lock()
        if let result {
            lock.unlock()
            continuation.resume(with: result)
            return
        }
        self.continuation = continuation
        lock.unlock()
    }

    func install(_ tasks: [Task<Void, Never>]) {
        lock.lock()
        if result != nil {
            lock.unlock()
            tasks.forEach { $0.cancel() }
            return
        }
        self.tasks = tasks
        lock.unlock()
    }

    func resolve(_ result: Result<Value, Error>) {
        lock.lock()
        guard self.result == nil else { lock.unlock(); return }
        self.result = result
        let continuation = self.continuation
        self.continuation = nil
        let tasks = self.tasks
        self.tasks.removeAll()
        lock.unlock()
        tasks.forEach { $0.cancel() }
        continuation?.resume(with: result)
    }
}

public enum AsyncDeadline {
    public static func run<Value: Sendable>(
        nanoseconds: UInt64,
        operation: @escaping @Sendable () async throws -> Value
    ) async throws -> Value {
        let race = AsyncDeadlineRace<Value>()
        return try await withTaskCancellationHandler {
            try await withCheckedThrowingContinuation { continuation in
                race.install(continuation)
                let operationTask = Task {
                    do { race.resolve(.success(try await operation())) }
                    catch { race.resolve(.failure(error)) }
                }
                let timeoutTask = Task {
                    do { try await Task.sleep(nanoseconds: nanoseconds) }
                    catch { return }
                    race.resolve(.failure(AsyncDeadlineError.timedOut))
                }
                race.install([operationTask, timeoutTask])
            }
        } onCancel: {
            race.resolve(.failure(CancellationError()))
        }
    }
}

public struct ConnectionHeartbeat: Equatable, Sendable {
    fileprivate let leaseID: UUID
    fileprivate let pulseID: UUID
}

public struct ConnectionWatchdog: Sendable {
    private struct Lease: Sendable {
        let id: UUID
        let run: UUID
        let generation: String
    }

    private var lease: Lease?
    private var pending: ConnectionHeartbeat?

    public init() {}

    public mutating func begin(run: UUID, generation: String) {
        lease = Lease(id: UUID(), run: run, generation: generation)
        pending = nil
    }

    public mutating func heartbeatSent(run: UUID, generation: String) -> ConnectionHeartbeat? {
        guard let lease, lease.run == run, lease.generation == generation, pending == nil else { return nil }
        let heartbeat = ConnectionHeartbeat(leaseID: lease.id, pulseID: UUID())
        pending = heartbeat
        return heartbeat
    }

    @discardableResult public mutating func acknowledge(run: UUID, generation: String) -> Bool {
        guard let lease, lease.run == run, lease.generation == generation, pending != nil else { return false }
        pending = nil
        return true
    }

    @discardableResult public mutating func expire(_ heartbeat: ConnectionHeartbeat) -> Bool {
        guard pending == heartbeat, lease?.id == heartbeat.leaseID else { return false }
        pending = nil
        return true
    }

    @discardableResult public mutating func end(run: UUID, generation: String) -> Bool {
        guard let lease, lease.run == run, lease.generation == generation else { return false }
        self.lease = nil
        pending = nil
        return true
    }

    public mutating func cancel() {
        lease = nil
        pending = nil
    }
}
