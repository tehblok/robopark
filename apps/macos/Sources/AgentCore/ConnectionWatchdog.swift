import Foundation

public enum AsyncDeadlineError: Error {
    case timedOut
}

public enum ConnectionLivenessError: Error {
    case invalidated
}

public final class ConnectionLivenessSignal: @unchecked Sendable {
    private let lock = NSLock()
    private var invalidated = false
    private var waiters: [UUID: CheckedContinuation<Void, Never>] = [:]

    public init() {}

    public func wait() async {
        let id = UUID()
        await withTaskCancellationHandler {
            await withCheckedContinuation { continuation in
                lock.lock()
                if invalidated || Task.isCancelled {
                    lock.unlock()
                    continuation.resume()
                } else {
                    waiters[id] = continuation
                    lock.unlock()
                }
            }
        } onCancel: {
            self.cancelWaiter(id)
        }
    }

    public func invalidate() {
        lock.lock()
        guard !invalidated else { lock.unlock(); return }
        invalidated = true
        let continuations = Array(waiters.values)
        waiters.removeAll()
        lock.unlock()
        continuations.forEach { $0.resume() }
    }

    private func cancelWaiter(_ id: UUID) {
        lock.lock()
        let continuation = waiters.removeValue(forKey: id)
        lock.unlock()
        continuation?.resume()
    }
}

private final class AsyncDeadlineRace<Value: Sendable>: @unchecked Sendable {
    private let lock = NSLock()
    private let onResolve: (@Sendable () -> Void)?
    private var continuation: CheckedContinuation<Value, Error>?
    private var result: Result<Value, Error>?
    private var tasks: [Task<Void, Never>] = []

    init(onResolve: (@Sendable () -> Void)? = nil) {
        self.onResolve = onResolve
    }

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

    var isResolved: Bool {
        lock.lock()
        defer { lock.unlock() }
        return result != nil
    }

    @discardableResult func resolve(
        _ result: Result<Value, Error>,
        beforeResume: (@Sendable () -> Void)? = nil
    ) -> Bool {
        lock.lock()
        guard self.result == nil else { lock.unlock(); return false }
        self.result = result
        let continuation = self.continuation
        self.continuation = nil
        let tasks = self.tasks
        self.tasks.removeAll()
        lock.unlock()
        beforeResume?()
        tasks.forEach { $0.cancel() }
        onResolve?()
        continuation?.resume(with: result)
        return true
    }
}

public final class CallbackReceiveOwnership: @unchecked Sendable {
    private let lock = NSLock()
    private var active = 0

    public init() {}

    public var activeCount: Int {
        lock.lock()
        defer { lock.unlock() }
        return active
    }

    fileprivate func acquire() {
        lock.lock()
        active += 1
        lock.unlock()
    }

    fileprivate func release() {
        lock.lock()
        precondition(active > 0)
        active -= 1
        lock.unlock()
    }
}

public enum CallbackReceiveGate {
    public static func run<Value: Sendable>(
        interruptedBy signal: ConnectionLivenessSignal,
        onCancel: @escaping @Sendable () -> Void,
        ownership: CallbackReceiveOwnership? = nil,
        start: @escaping @Sendable (@escaping @Sendable (Result<Value, Error>) -> Void) -> Void
    ) async throws -> Value {
        ownership?.acquire()
        let race = AsyncDeadlineRace<Value>(onResolve: { ownership?.release() })
        return try await withTaskCancellationHandler {
            try await withCheckedThrowingContinuation { continuation in
                race.install(continuation)
                let interruptionTask = Task {
                    await signal.wait()
                    guard !Task.isCancelled else { return }
                    race.resolve(.failure(ConnectionLivenessError.invalidated), beforeResume: onCancel)
                }
                race.install([interruptionTask])
                guard !race.isResolved else { return }
                start { [weak race] result in
                    race?.resolve(result)
                }
            }
        } onCancel: {
            race.resolve(.failure(CancellationError()), beforeResume: onCancel)
        }
    }

    public static func run<Value: Sendable>(
        nanoseconds: UInt64,
        onCancel: @escaping @Sendable () -> Void,
        ownership: CallbackReceiveOwnership? = nil,
        start: @escaping @Sendable (@escaping @Sendable (Result<Value, Error>) -> Void) -> Void
    ) async throws -> Value {
        ownership?.acquire()
        let race = AsyncDeadlineRace<Value>(onResolve: { ownership?.release() })
        return try await withTaskCancellationHandler {
            try await withCheckedThrowingContinuation { continuation in
                race.install(continuation)
                let timeoutTask = Task {
                    do { try await Task.sleep(nanoseconds: nanoseconds) }
                    catch { return }
                    race.resolve(.failure(AsyncDeadlineError.timedOut), beforeResume: onCancel)
                }
                race.install([timeoutTask])
                guard !race.isResolved else { return }
                start { [weak race] result in
                    race?.resolve(result)
                }
            }
        } onCancel: {
            race.resolve(.failure(CancellationError()), beforeResume: onCancel)
        }
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

    @discardableResult public mutating func acknowledge(
        _ heartbeat: ConnectionHeartbeat, run: UUID, generation: String
    ) -> Bool {
        guard let lease, lease.run == run, lease.generation == generation,
              lease.id == heartbeat.leaseID, pending == heartbeat else { return false }
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
