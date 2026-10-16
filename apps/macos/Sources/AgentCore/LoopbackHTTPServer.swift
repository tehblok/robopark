import Foundation
import Network

/// A local asset/API transport. The caller owns authentication and route policy.
/// Listener, buffers, deadlines and task cancellation are confined to one queue.
public final class LoopbackHTTPServer: @unchecked Sendable {
    public typealias Handler = @Sendable (LocalHTTPRequest) async -> LocalHTTPResponse
    public typealias HeadroomPolicy = @Sendable (LocalHTTPRequest) -> Bool
    private let port: UInt16
    private let preservesHeadroom: HeadroomPolicy
    private let requestDeadlineSeconds: Double
    private let handler: Handler
    private let queue = DispatchQueue(label: "ru.robopark.workspace.http", qos: .utility)
    private var listener: NWListener?
    private var contexts: [UUID: Connection] = [:]
    private var activeConstrainedRequests = 0
    private var bufferedBytes = 0
    // Browsers load local bundles in bursts. Keep slow handler work bounded
    // separately so it cannot consume every local transport slot.
    private static let maximumConnections = 32
    private static let maximumConstrainedRequests = 8
    // Mutable members are accessed only on queue; tasks pass the identity back.
    private final class Connection: @unchecked Sendable {
        let id = UUID()
        let socket: NWConnection
        var bytes = Data()
        var task: Task<Void, Never>?
        var timer: DispatchSourceTimer?
        var occupiesConstrainedSlot = false
        init(_ socket: NWConnection) { self.socket = socket }
    }
    public init(port: UInt16 = 18444, preservesHeadroom: @escaping HeadroomPolicy = { _ in false },
                requestDeadlineSeconds: Double = 100,
                handler: @escaping Handler) {
        self.port = port; self.preservesHeadroom = preservesHeadroom
        self.requestDeadlineSeconds = requestDeadlineSeconds; self.handler = handler
    }

    public func start() async throws -> UInt16 {
        try await withCheckedThrowingContinuation { continuation in
            queue.async { [self] in
                guard self.listener == nil else { continuation.resume(throwing: LocalHTTPError.malformed); return }
                do {
                    let parameters = NWParameters.tcp
                    parameters.requiredLocalEndpoint = .hostPort(host: .ipv4(.loopback), port: NWEndpoint.Port(rawValue: self.port)!)
                    parameters.allowLocalEndpointReuse = false
                    let listener = try NWListener(using: parameters)
                    self.listener = listener
                    var resumed = false
                    listener.stateUpdateHandler = { [weak listener] state in
                        switch state {
                        case .ready:
                            guard !resumed, let port = listener?.port else { return }
                            resumed = true; continuation.resume(returning: port.rawValue)
                        case .failed(let error):
                            if !resumed { resumed = true; continuation.resume(throwing: error) }
                            self.closeAll()
                        case .cancelled:
                            if !resumed { resumed = true; continuation.resume(throwing: CancellationError()) }
                        default: break
                        }
                    }
                    listener.newConnectionHandler = { [weak self] connection in self?.accept(connection) }
                    listener.start(queue: self.queue)
                } catch { continuation.resume(throwing: error) }
            }
        }
    }
    public func stop() { queue.async { self.closeAll() } }
    private func closeAll() {
        listener?.cancel(); listener = nil
        for context in Array(contexts.values) { close(context) }
    }
    private func close(_ context: Connection) {
        guard contexts.removeValue(forKey: context.id) != nil else { return }
        bufferedBytes -= context.bytes.count; context.bytes.removeAll()
        context.timer?.cancel(); context.timer = nil
        context.task?.cancel(); context.task = nil
        context.socket.cancel()
    }
    private func releaseConstrainedSlot(_ context: Connection) {
        guard context.occupiesConstrainedSlot else { return }
        activeConstrainedRequests -= 1
        context.occupiesConstrainedSlot = false
    }
    private func deadline(_ context: Connection, seconds: Double) {
        context.timer?.cancel()
        let timer = DispatchSource.makeTimerSource(queue: queue)
        timer.schedule(deadline: .now() + seconds)
        timer.setEventHandler { [weak self, weak context] in
            if let self, let context { self.close(context) }
        }
        context.timer = timer; timer.resume()
    }
    private func accept(_ socket: NWConnection) {
        guard contexts.count < Self.maximumConnections else { socket.cancel(); return }
        let context = Connection(socket)
        contexts[context.id] = context
        socket.stateUpdateHandler = { [weak self, weak context] state in
            guard let self, let context else { return }
            if case .failed = state { self.close(context) }
            if case .cancelled = state { self.close(context) }
        }
        deadline(context, seconds: 20)
        socket.start(queue: queue)
        receive(context)
    }
    private func receive(_ context: Connection) {
        context.socket.receive(minimumIncompleteLength: 1, maximumLength: 64 * 1024) { [weak self, weak context] data, _, complete, error in
            guard let self, let context, self.contexts[context.id] != nil else { return }
            if let data {
                guard self.bufferedBytes + data.count <= 32 * 1024 * 1024 else {
                    self.respond(.json(["detail": "local_request_too_large"], status: 413), to: context); return
                }
                self.bufferedBytes += data.count; context.bytes.append(data)
            }
            do {
                if let request = try LocalHTTPRequest.parse(context.bytes) {
                    self.bufferedBytes -= context.bytes.count; context.bytes.removeAll()
                    if !self.preservesHeadroom(request) {
                        guard self.activeConstrainedRequests < Self.maximumConstrainedRequests else {
                            self.respond(.json(["detail": "local_transport_busy"], status: 503), to: context)
                            return
                        }
                        self.activeConstrainedRequests += 1
                        context.occupiesConstrainedSlot = true
                    }
                    self.deadline(context, seconds: self.requestDeadlineSeconds)
                    let handler = self.handler
                    context.task = Task { [weak self] in
                        let response = await handler(request)
                        let cancelled = Task.isCancelled
                        guard let self else { return }
                        self.queue.async {
                            self.releaseConstrainedSlot(context)
                            guard !cancelled, self.contexts[context.id] != nil else { return }
                            self.respond(response, to: context, head: request.method == "HEAD")
                        }
                    }
                    return
                }
            } catch {
                self.respond(.json(["detail": "invalid_local_request"], status: error as? LocalHTTPError == .tooLarge ? 413 : 400), to: context)
                return
            }
            if complete || error != nil { self.close(context) }
            else { self.receive(context) }
        }
    }
    private func respond(_ response: LocalHTTPResponse, to context: Connection, head: Bool = false) {
        let bounded = response.body.count <= 32 * 1024 * 1024 ? response : .json(["detail": "local_response_too_large"], status: 502)
        deadline(context, seconds: 20)
        context.socket.send(content: bounded.wire(head: head), completion: .contentProcessed { [weak self, weak context] _ in
            if let self, let context { self.close(context) }
        })
    }

}
