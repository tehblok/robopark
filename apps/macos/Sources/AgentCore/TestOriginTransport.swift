import Foundation
import Network
import Security
import SystemConfiguration
import Darwin

public enum TestOriginTransportError: Error, Equatable {
    case unsupportedSystem
    case listenerUnavailable
    case noVerifiedAddress
}

/// A per-process, exact-host HTTP CONNECT transport for the TEST deployment.
/// It changes only the supplied URLSessionConfiguration. TLS remains end-to-end:
/// the relay selects a publicly resolved address after a default-trust TLS
/// preflight, then forwards the caller's own TLS bytes over a plain TCP socket.
public final class TestOriginTransport: @unchecked Sendable {
    public static let testHost = "roboparktest.ru.tuna.am"
    public static let shared = TestOriginTransport()

    private static let maximumConnections = 16
    private static let maximumHeaderBytes = 4 * 1024
    private static let listenerReadyTimeout: TimeInterval = 3
    private static let relayIdleTimeout: TimeInterval = 120
    private static let upstreamConnectTimeout: TimeInterval = 4
    private static let addressSelectionTimeout: TimeInterval = 10

    private let queue = DispatchQueue(label: "ru.robopark.test-origin-connect", qos: .utility)
    private let selector: TestOriginAddressSelector
    private let targetPort: UInt16
    private let honorSystemProxy: Bool
    private let allowDirectFailover: Bool
    private let username = "robopark"
    private let password: String
    private var listener: NWListener?
    private var listenerPort: UInt16?
    private var listenerWaiters: [(Result<UInt16, Error>) -> Void] = []
    private var connections: [UUID: Relay] = [:]

    private enum Phase { case headers, selecting, relay }
    private final class Relay: @unchecked Sendable {
        let id = UUID()
        let client: NWConnection
        var upstream: NWConnection?
        var phase: Phase = .headers
        var header = Data()
        var pending = Data()
        var timer: DispatchSourceTimer?
        init(client: NWConnection) { self.client = client }
    }

    private convenience init() {
        let resolver: TestOriginAddressSelector.Resolver = {
            try await TestOriginDNSResolver.resolve(host: TestOriginTransport.testHost)
        }
        let fallbackResolver: TestOriginAddressSelector.Resolver = {
            try await TestOriginDoHResolver.resolve(host: TestOriginTransport.testHost)
        }
        let preflight: TestOriginAddressSelector.Preflight = { candidate in
            await TestOriginTLSPreflight.check(candidate, port: 443,
                serverName: TestOriginTransport.testHost, timeout: 2, options: nil)
        }
        self.init(resolver: resolver, fallbackResolver: fallbackResolver,
                  preflight: preflight, targetPort: 443,
                  honorSystemProxy: true, allowDirectFailover: true)
    }

    internal init(resolver: @escaping TestOriginAddressSelector.Resolver,
                  fallbackResolver: TestOriginAddressSelector.Resolver? = nil,
                  preflight: @escaping TestOriginAddressSelector.Preflight,
                  targetPort: UInt16 = 443, honorSystemProxy: Bool = false,
                  allowDirectFailover: Bool = false) {
        self.selector = TestOriginAddressSelector(cacheTTL: 20, resolver: resolver,
                                                  fallbackResolver: fallbackResolver,
                                                  preflight: preflight)
        self.targetPort = targetPort
        self.honorSystemProxy = honorSystemProxy
        self.allowDirectFailover = allowDirectFailover
        var nonce = [UInt8](repeating: 0, count: 32)
        if SecRandomCopyBytes(kSecRandomDefault, nonce.count, &nonce) == errSecSuccess {
            self.password = Data(nonce).base64EncodedString()
        } else {
            self.password = UUID().uuidString + UUID().uuidString
        }
    }

    /// Installs an authenticated exact-domain proxy when the local listener is
    /// available. The transport is an optimization: listener startup failure
    /// leaves the configuration on URLSession's direct, default-trust path.
    public func configure(_ configuration: URLSessionConfiguration) throws {
        if #available(macOS 14.0, *) {
            guard configuration.proxyConfigurations.isEmpty,
                  configuration.connectionProxyDictionary?.isEmpty != false,
                  !honorSystemProxy || !Self.systemProxyIsEnabled() else { return }
            guard let port = try? ensureListener() else { return }
            try configureModern(configuration, port: port)
        }
        // macOS 13 keeps URLSession's direct, default-trust transport. Its
        // legacy proxy dictionary cannot scope and authenticate CONNECT with
        // the same guarantees as Network.framework's ProxyConfiguration.
    }

    @available(macOS 14.0, *)
    private func configureModern(_ configuration: URLSessionConfiguration, port: UInt16) throws {
        guard let endpointPort = NWEndpoint.Port(rawValue: port) else {
            throw TestOriginTransportError.listenerUnavailable
        }
        var proxy = ProxyConfiguration(httpCONNECTProxy: .hostPort(
            host: .ipv4(.loopback), port: endpointPort))
        proxy.applyCredential(username: username, password: password)
        proxy.matchDomains = [Self.testHost]
        proxy.excludedDomains = []
        proxy.allowFailover = allowDirectFailover
        configuration.connectionProxyDictionary = nil
        configuration.proxyConfigurations = [proxy]
    }

    private static func systemProxyIsEnabled() -> Bool {
        guard let settings = SCDynamicStoreCopyProxies(nil) as? [String: Any] else { return false }
        return proxyIsEnabled(settings)
    }

    internal static func proxyIsEnabled(_ settings: [String: Any]) -> Bool {
        let keys = [
            kSCPropNetProxiesHTTPEnable,
            kSCPropNetProxiesHTTPSEnable,
            kSCPropNetProxiesSOCKSEnable,
            kSCPropNetProxiesProxyAutoConfigEnable,
            kSCPropNetProxiesProxyAutoDiscoveryEnable,
        ] as [CFString]
        return keys.contains { key in
            if let value = settings[key as String] as? NSNumber { return value.boolValue }
            if let value = settings[key as String] as? Bool { return value }
            return false
        }
    }

    public func stop() {
        queue.async { [self] in
            listener?.cancel(); listener = nil; listenerPort = nil
            let active = Array(connections.values)
            for relay in active { close(relay) }
            let waiters = listenerWaiters; listenerWaiters.removeAll()
            waiters.forEach { $0(.failure(TestOriginTransportError.listenerUnavailable)) }
        }
    }

    private func ensureListener() throws -> UInt16 {
        let semaphore = DispatchSemaphore(value: 0)
        let resultLock = NSLock()
        var result: Result<UInt16, Error>?
        queue.async { [self] in
            if let port = listenerPort {
                resultLock.lock(); result = .success(port); resultLock.unlock()
                semaphore.signal(); return
            }
            listenerWaiters.append { value in
                resultLock.lock(); result = value; resultLock.unlock(); semaphore.signal()
            }
            guard listener == nil else { return }
            do {
                let parameters = NWParameters.tcp
                parameters.requiredLocalEndpoint = .hostPort(host: .ipv4(.loopback), port: .any)
                parameters.allowLocalEndpointReuse = false
                let listener = try NWListener(using: parameters)
                self.listener = listener
                listener.stateUpdateHandler = { [weak self, weak listener] state in
                    guard let self else { return }
                    switch state {
                    case .ready:
                        guard let port = listener?.port?.rawValue else {
                            self.finishListener(.failure(TestOriginTransportError.listenerUnavailable)); return
                        }
                        self.listenerPort = port
                        self.finishListener(.success(port))
                    case .failed:
                        self.finishListener(.failure(TestOriginTransportError.listenerUnavailable))
                        self.listener?.cancel(); self.listener = nil; self.listenerPort = nil
                    case .cancelled:
                        if self.listenerPort == nil {
                            self.finishListener(.failure(TestOriginTransportError.listenerUnavailable))
                        }
                    default: break
                    }
                }
                listener.newConnectionHandler = { [weak self] connection in self?.accept(connection) }
                listener.start(queue: queue)
            } catch {
                listener = nil
                finishListener(.failure(error))
            }
        }
        guard semaphore.wait(timeout: .now() + Self.listenerReadyTimeout) == .success else {
            throw TestOriginTransportError.listenerUnavailable
        }
        resultLock.lock(); let completed = result; resultLock.unlock()
        return try completed?.get() ?? { throw TestOriginTransportError.listenerUnavailable }()
    }

    private func finishListener(_ result: Result<UInt16, Error>) {
        let waiters = listenerWaiters; listenerWaiters.removeAll()
        waiters.forEach { $0(result) }
    }

    private func accept(_ client: NWConnection) {
        guard connections.count < Self.maximumConnections else { client.cancel(); return }
        let relay = Relay(client: client)
        connections[relay.id] = relay
        client.stateUpdateHandler = { [weak self, weak relay] state in
            guard let self, let relay else { return }
            if case .failed = state { self.close(relay) }
            if case .cancelled = state { self.close(relay) }
        }
        armDeadline(relay, seconds: 10)
        client.start(queue: queue)
        receiveHeaders(relay)
    }

    private func receiveHeaders(_ relay: Relay) {
        relay.client.receive(minimumIncompleteLength: 1, maximumLength: Self.maximumHeaderBytes + 1) {
            [weak self, weak relay] data, _, complete, error in
            guard let self, let relay, self.connections[relay.id] != nil, relay.phase == .headers else { return }
            if let data { relay.header.append(data) }
            guard relay.header.count <= Self.maximumHeaderBytes else {
                self.respondAndClose(relay, status: "431 Request Header Fields Too Large"); return
            }
            if let boundary = relay.header.range(of: Data("\r\n\r\n".utf8)) {
                relay.pending = Data(relay.header[boundary.upperBound...])
                let header = Data(relay.header[..<boundary.lowerBound])
                relay.header.removeAll(keepingCapacity: false)
                self.authorize(header, relay: relay)
                return
            }
            if complete || error != nil { self.close(relay) }
            else { self.receiveHeaders(relay) }
        }
    }

    private func authorize(_ data: Data, relay: Relay) {
        guard let text = String(data: data, encoding: .utf8), !text.contains("\0") else {
            respondAndClose(relay, status: "400 Bad Request"); return
        }
        let lines = text.components(separatedBy: "\r\n")
        guard let first = lines.first else { respondAndClose(relay, status: "400 Bad Request"); return }
        var headers: [String: String] = [:]
        for line in lines.dropFirst() {
            guard let separator = line.firstIndex(of: ":") else {
                respondAndClose(relay, status: "400 Bad Request"); return
            }
            let name = line[..<separator].trimmingCharacters(in: .whitespaces).lowercased()
            let value = line[line.index(after: separator)...].trimmingCharacters(in: .whitespaces)
            guard !name.isEmpty, headers[name] == nil else {
                respondAndClose(relay, status: "400 Bad Request"); return
            }
            headers[name] = value
        }
        let expected = "Basic " + Data("\(username):\(password)".utf8).base64EncodedString()
        guard constantTimeEqual(headers["proxy-authorization"], expected) else {
            respondAndClose(relay, status: "407 Proxy Authentication Required",
                extra: "Proxy-Authenticate: Basic realm=\"Robopark Test Transport\"\r\n")
            return
        }
        let request = first.split(separator: " ", omittingEmptySubsequences: false)
        let authority = "\(Self.testHost):443"
        let hostHeaderAllowed = headers["host"].map {
            $0.lowercased() == Self.testHost || $0.lowercased() == authority
        } ?? true
        guard request.count == 3, request[0] == "CONNECT", request[2] == "HTTP/1.1",
              request[1].lowercased() == authority, hostHeaderAllowed else {
            respondAndClose(relay, status: "403 Forbidden"); return
        }
        relay.phase = .selecting
        armDeadline(relay, seconds: Self.addressSelectionTimeout)
        Task { [weak self, weak relay] in
            guard let self, let relay else { return }
            do {
                let candidate = try await selector.select()
                queue.async { [weak self, weak relay] in
                    guard let self, let relay, self.connections[relay.id] != nil else { return }
                    self.connectUpstream(relay, candidate: candidate)
                }
            } catch {
                queue.async { [weak self, weak relay] in
                    if let self, let relay { self.respondAndClose(relay, status: "502 Bad Gateway") }
                }
            }
        }
    }

    private func connectUpstream(_ relay: Relay, candidate: TestOriginCandidate) {
        guard let port = NWEndpoint.Port(rawValue: targetPort) else {
            respondAndClose(relay, status: "502 Bad Gateway"); return
        }
        let upstream = NWConnection(host: NWEndpoint.Host(candidate.address), port: port, using: .tcp)
        relay.upstream = upstream
        var completed = false
        upstream.stateUpdateHandler = { [weak self, weak relay, weak upstream] state in
            guard let self, let relay, let upstream, self.connections[relay.id] != nil else { return }
            switch state {
            case .ready where !completed:
                completed = true
                self.beginRelay(relay, upstream: upstream)
            case .failed where !completed:
                completed = true
                Task { await self.selector.invalidate(candidate) }
                self.respondAndClose(relay, status: "502 Bad Gateway")
            case .cancelled where !completed:
                completed = true; self.close(relay)
            default: break
            }
        }
        armDeadline(relay, seconds: Self.upstreamConnectTimeout)
        upstream.start(queue: queue)
    }

    private func beginRelay(_ relay: Relay, upstream: NWConnection) {
        let response = Data("HTTP/1.1 200 Connection Established\r\n\r\n".utf8)
        relay.client.send(content: response, completion: .contentProcessed { [weak self, weak relay] error in
            guard let self, let relay, self.connections[relay.id] != nil else { return }
            guard error == nil else { self.close(relay); return }
            relay.phase = .relay
            self.armDeadline(relay, seconds: Self.relayIdleTimeout)
            let pending = relay.pending; relay.pending.removeAll(keepingCapacity: false)
            if pending.isEmpty {
                self.pump(from: relay.client, to: upstream, relay: relay)
            } else {
                upstream.send(content: pending, completion: .contentProcessed { [weak self, weak relay] error in
                    guard let self, let relay else { return }
                    guard error == nil else { self.close(relay); return }
                    self.pump(from: relay.client, to: upstream, relay: relay)
                })
            }
            self.pump(from: upstream, to: relay.client, relay: relay)
        })
    }

    private func pump(from source: NWConnection, to target: NWConnection, relay: Relay) {
        source.receive(minimumIncompleteLength: 1, maximumLength: 64 * 1024) {
            [weak self, weak relay] data, _, complete, error in
            guard let self, let relay, self.connections[relay.id] != nil, relay.phase == .relay else { return }
            guard error == nil else { self.close(relay); return }
            guard let data, !data.isEmpty else {
                if complete { self.close(relay) }
                else { self.pump(from: source, to: target, relay: relay) }
                return
            }
            self.armDeadline(relay, seconds: Self.relayIdleTimeout)
            target.send(content: data, completion: .contentProcessed { [weak self, weak relay] sendError in
                guard let self, let relay else { return }
                guard sendError == nil else { self.close(relay); return }
                if complete { self.close(relay) }
                else { self.pump(from: source, to: target, relay: relay) }
            })
        }
    }

    private func armDeadline(_ relay: Relay, seconds: TimeInterval) {
        relay.timer?.cancel()
        let timer = DispatchSource.makeTimerSource(queue: queue)
        timer.schedule(deadline: .now() + seconds)
        timer.setEventHandler { [weak self, weak relay] in
            if let self, let relay { self.close(relay) }
        }
        relay.timer = timer; timer.resume()
    }

    private func respondAndClose(_ relay: Relay, status: String, extra: String = "") {
        guard connections[relay.id] != nil else { return }
        let response = Data("HTTP/1.1 \(status)\r\n\(extra)Connection: close\r\nContent-Length: 0\r\n\r\n".utf8)
        relay.client.send(content: response, completion: .contentProcessed { [weak self, weak relay] _ in
            if let self, let relay { self.close(relay) }
        })
    }

    private func close(_ relay: Relay) {
        guard connections.removeValue(forKey: relay.id) != nil else { return }
        relay.timer?.cancel(); relay.timer = nil
        relay.client.stateUpdateHandler = nil; relay.client.cancel()
        relay.upstream?.stateUpdateHandler = nil; relay.upstream?.cancel(); relay.upstream = nil
        relay.header.removeAll(); relay.pending.removeAll()
    }

    private func constantTimeEqual(_ left: String?, _ right: String) -> Bool {
        guard let left else { return false }
        let a = Array(left.utf8), b = Array(right.utf8)
        var difference = UInt8(truncatingIfNeeded: a.count ^ b.count)
        for index in 0..<max(a.count, b.count) {
            difference |= (index < a.count ? a[index] : 0) ^ (index < b.count ? b[index] : 0)
        }
        return difference == 0
    }

    internal func testingPort() throws -> UInt16 { try ensureListener() }
    internal func testingAuthorizationHeader() throws -> String {
        "Basic " + Data("\(username):\(password)".utf8).base64EncodedString()
    }
    internal func testingActiveConnections() -> Int { queue.sync { connections.count } }
}

internal struct TestOriginCandidate: Hashable, Sendable {
    let address: String
    private init(_ address: String) { self.address = address }

    static func testing(_ address: String) -> Self { Self(address) }

    static func publicAddress(_ address: String) -> Self? {
        var ipv4 = in_addr()
        if inet_pton(AF_INET, address, &ipv4) == 1 {
            let bytes = withUnsafeBytes(of: &ipv4) { Array($0) }
            guard bytes.count == 4, isPublicIPv4(bytes) else { return nil }
            return Self(address)
        }
        var ipv6 = in6_addr()
        if inet_pton(AF_INET6, address, &ipv6) == 1 {
            let bytes = withUnsafeBytes(of: &ipv6) { Array($0) }
            guard bytes.count == 16, isPublicIPv6(bytes) else { return nil }
            return Self(address)
        }
        return nil
    }

    private static func isPublicIPv4(_ b: [UInt8]) -> Bool {
        if b[0] == 0 || b[0] == 10 || b[0] == 127 || b[0] >= 224 { return false }
        if b[0] == 100 && (64...127).contains(b[1]) { return false }
        if b[0] == 169 && b[1] == 254 { return false }
        if b[0] == 172 && (16...31).contains(b[1]) { return false }
        if b[0] == 192 && b[1] == 168 { return false }
        if b[0] == 192 && b[1] == 0 { return false }
        if b[0] == 192 && b[1] == 0 && b[2] == 2 { return false }
        if b[0] == 198 && (b[1] == 18 || b[1] == 19 || b[1] == 51 && b[2] == 100) { return false }
        if b[0] == 203 && b[1] == 0 && b[2] == 113 { return false }
        return true
    }

    private static func isPublicIPv6(_ b: [UInt8]) -> Bool {
        if b.allSatisfy({ $0 == 0 }) || b.dropLast().allSatisfy({ $0 == 0 }) && b.last == 1 { return false }
        if b[0] & 0xfe == 0xfc || b[0] == 0xfe && b[1] & 0xc0 == 0x80 || b[0] == 0xff { return false }
        if b[0] == 0x20 && b[1] == 0x01 && b[2] == 0x0d && b[3] == 0xb8 { return false }
        guard b[0] & 0xe0 == 0x20 else { return false }
        return true
    }
}

internal actor TestOriginAddressSelector {
    typealias Resolver = @Sendable () async throws -> [TestOriginCandidate]
    typealias Preflight = @Sendable (TestOriginCandidate) async -> Bool
    private let cacheTTL: TimeInterval
    private let resolver: Resolver
    private let fallbackResolver: Resolver?
    private let preflight: Preflight
    private let resolverTimeout: TimeInterval
    private let fallbackResolverTimeout: TimeInterval
    private let maximumCandidates: Int
    private var cached: (candidate: TestOriginCandidate, expires: Date)?
    private var flight: (id: UUID, task: Task<TestOriginCandidate, Error>)?

    init(cacheTTL: TimeInterval, resolver: @escaping Resolver,
         fallbackResolver: Resolver? = nil, preflight: @escaping Preflight,
         resolverTimeout: TimeInterval = 0.75,
         fallbackResolverTimeout: TimeInterval = 3,
         maximumCandidates: Int = 4) {
        self.cacheTTL = cacheTTL; self.resolver = resolver
        self.fallbackResolver = fallbackResolver; self.preflight = preflight
        self.resolverTimeout = resolverTimeout
        self.fallbackResolverTimeout = fallbackResolverTimeout
        self.maximumCandidates = max(1, maximumCandidates)
    }

    func select(now: Date = Date()) async throws -> TestOriginCandidate {
        if let cached, now < cached.expires { return cached.candidate }
        if let flight { return try await flight.task.value }
        let resolver = self.resolver, fallbackResolver = self.fallbackResolver
        let preflight = self.preflight
        let resolverTimeout = self.resolverTimeout
        let fallbackResolverTimeout = self.fallbackResolverTimeout
        let maximumCandidates = self.maximumCandidates
        let id = UUID()
        let task = Task { () throws -> TestOriginCandidate in
            func firstVerified(_ candidates: [TestOriginCandidate]) async -> TestOriginCandidate? {
                let batch = Array(candidates.prefix(maximumCandidates))
                return await withTaskGroup(of: TestOriginCandidate?.self,
                                           returning: TestOriginCandidate?.self, body: { group in
                    for candidate in batch {
                        group.addTask { await preflight(candidate) ? candidate : nil }
                    }
                    for await result in group {
                        if let result { group.cancelAll(); return result }
                    }
                    return nil
                })
            }
            let system = await TestOriginResolverDeadline.run(resolver, timeout: resolverTimeout)
            let testedSystem = Array(system.prefix(maximumCandidates))
            if let selected = await firstVerified(testedSystem) { return selected }
            if let fallbackResolver {
                let fallback = await TestOriginResolverDeadline.run(
                    fallbackResolver, timeout: fallbackResolverTimeout)
                let unique = fallback.filter { !testedSystem.contains($0) }
                if let selected = await firstVerified(unique) { return selected }
            }
            throw TestOriginTransportError.noVerifiedAddress
        }
        self.flight = (id, task)
        do {
            let selected = try await task.value
            if self.flight?.id == id { self.flight = nil }
            cached = (selected, now.addingTimeInterval(cacheTTL))
            return selected
        } catch {
            if self.flight?.id == id { self.flight = nil }
            throw error
        }
    }

    func invalidate(_ candidate: TestOriginCandidate) {
        if cached?.candidate == candidate { cached = nil }
    }
}

private enum TestOriginResolverDeadline {
    static func run(_ resolver: @escaping TestOriginAddressSelector.Resolver,
                    timeout: TimeInterval) async -> [TestOriginCandidate] {
        let completion = TestOriginResolverCompletion()
        return await withTaskCancellationHandler(operation: {
            await withCheckedContinuation { continuation in
                completion.install(continuation)
                let worker = Task {
                    let value = (try? await resolver()) ?? []
                    completion.finish(value)
                }
                completion.setWorker(worker)
                DispatchQueue.global(qos: .utility).asyncAfter(deadline: .now() + timeout) {
                    completion.finish([])
                }
            }
        }, onCancel: { completion.finish([]) })
    }
}

private final class TestOriginResolverCompletion: @unchecked Sendable {
    private let lock = NSLock()
    private var continuation: CheckedContinuation<[TestOriginCandidate], Never>?
    private var worker: Task<Void, Never>?
    private var result: [TestOriginCandidate]?

    func install(_ continuation: CheckedContinuation<[TestOriginCandidate], Never>) {
        lock.lock()
        if let result {
            lock.unlock(); continuation.resume(returning: result); return
        }
        self.continuation = continuation
        lock.unlock()
    }

    func setWorker(_ worker: Task<Void, Never>) {
        lock.lock()
        if result != nil { lock.unlock(); worker.cancel(); return }
        self.worker = worker
        lock.unlock()
    }

    func finish(_ value: [TestOriginCandidate]) {
        lock.lock()
        guard result == nil else { lock.unlock(); return }
        result = value
        let continuation = self.continuation
        let worker = self.worker
        self.continuation = nil
        self.worker = nil
        lock.unlock()
        worker?.cancel()
        continuation?.resume(returning: value)
    }
}

internal enum TestOriginDNSResolver {
    static func resolve(host: String) async throws -> [TestOriginCandidate] {
        try await Task.detached(priority: .utility) {
            var hints = addrinfo(ai_flags: AI_ADDRCONFIG, ai_family: AF_UNSPEC,
                                 ai_socktype: SOCK_STREAM, ai_protocol: IPPROTO_TCP,
                                 ai_addrlen: 0, ai_canonname: nil, ai_addr: nil, ai_next: nil)
            var result: UnsafeMutablePointer<addrinfo>?
            guard getaddrinfo(host, "443", &hints, &result) == 0, let first = result else {
                throw TestOriginTransportError.noVerifiedAddress
            }
            defer { freeaddrinfo(first) }
            var addresses: [TestOriginCandidate] = []
            var seen = Set<TestOriginCandidate>()
            var cursor: UnsafeMutablePointer<addrinfo>? = first
            while let info = cursor?.pointee {
                defer { cursor = info.ai_next }
                guard info.ai_family == AF_INET || info.ai_family == AF_INET6,
                      let socketAddress = info.ai_addr else { continue }
                var buffer = [CChar](repeating: 0, count: Int(NI_MAXHOST))
                guard getnameinfo(socketAddress, info.ai_addrlen, &buffer, socklen_t(buffer.count),
                                  nil, 0, NI_NUMERICHOST) == 0 else { continue }
                let address = String(cString: buffer)
                if let candidate = TestOriginCandidate.publicAddress(address), seen.insert(candidate).inserted {
                    addresses.append(candidate)
                }
            }
            guard !addresses.isEmpty else { throw TestOriginTransportError.noVerifiedAddress }
            return addresses
        }.value
    }
}

internal enum TestOriginDoHResolver {
    private static let maximumBytes = 64 * 1024

    static func resolve(host: String) async throws -> [TestOriginCandidate] {
        guard normalized(host) == normalized(TestOriginTransport.testHost) else {
            throw TestOriginTransportError.noVerifiedAddress
        }
        var parts = URLComponents(string: "https://cloudflare-dns.com/dns-query")!
        parts.queryItems = [URLQueryItem(name: "name", value: host),
                            URLQueryItem(name: "type", value: "A")]
        guard let url = parts.url else { throw TestOriginTransportError.noVerifiedAddress }
        var request = URLRequest(url: url, cachePolicy: .reloadIgnoringLocalCacheData,
                                 timeoutInterval: 2.5)
        request.setValue("application/dns-json", forHTTPHeaderField: "Accept")
        request.setValue("RoboparkMacAgent/1", forHTTPHeaderField: "User-Agent")
        let receiver = TestOriginDoHReceiver(limit: maximumBytes)
        let configuration = URLSessionConfiguration.ephemeral
        configuration.urlCache = nil
        configuration.timeoutIntervalForRequest = 2.5
        configuration.timeoutIntervalForResource = 3
        let session = URLSession(configuration: configuration, delegate: receiver, delegateQueue: nil)
        defer { session.finishTasksAndInvalidate() }
        let (data, response) = try await receiver.read(request, using: session)
        let mimeType = response.mimeType?.lowercased()
        guard response.statusCode == 200,
              (mimeType == "application/dns-json" || mimeType == "application/json") else {
            throw TestOriginTransportError.noVerifiedAddress
        }
        let candidates = parse(data, expectedHost: host)
        guard !candidates.isEmpty else { throw TestOriginTransportError.noVerifiedAddress }
        return candidates
    }

    internal static func parse(_ data: Data, expectedHost: String) -> [TestOriginCandidate] {
        guard data.count <= maximumBytes,
              let object = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
              (object["Status"] as? Int) == 0,
              let questions = object["Question"] as? [[String: Any]], questions.count == 1,
              let name = questions[0]["name"] as? String,
              normalized(name) == normalized(expectedHost),
              (questions[0]["type"] as? Int) == 1,
              let answers = object["Answer"] as? [[String: Any]] else { return [] }
        var seen = Set<TestOriginCandidate>()
        return answers.compactMap { answer in
            guard (answer["type"] as? Int) == 1, let literal = answer["data"] as? String,
                  let candidate = TestOriginCandidate.publicAddress(literal),
                  seen.insert(candidate).inserted else { return nil }
            return candidate
        }
    }

    private static func normalized(_ name: String) -> String {
        name.lowercased().trimmingCharacters(in: CharacterSet(charactersIn: "."))
    }
}

private final class TestOriginDoHReceiver: NSObject, URLSessionDataDelegate, @unchecked Sendable {
    private let lock = NSLock()
    private let limit: Int
    private var data = Data()
    private var response: HTTPURLResponse?
    private var completion: CheckedContinuation<(Data, HTTPURLResponse), Error>?
    private var task: URLSessionDataTask?
    private var finished = false

    init(limit: Int) { self.limit = limit }

    func read(_ request: URLRequest, using session: URLSession) async throws -> (Data, HTTPURLResponse) {
        try await withTaskCancellationHandler(operation: {
            try await withCheckedThrowingContinuation { continuation in
                let dataTask = session.dataTask(with: request)
                lock.lock()
                guard !finished else {
                    lock.unlock()
                    dataTask.cancel()
                    continuation.resume(throwing: CancellationError())
                    return
                }
                completion = continuation
                task = dataTask
                lock.unlock()
                dataTask.resume()
            }
        }, onCancel: { [weak self] in self?.finish(.failure(CancellationError())) })
    }

    func urlSession(_ session: URLSession, dataTask: URLSessionDataTask,
                    didReceive response: URLResponse,
                    completionHandler: @escaping (URLSession.ResponseDisposition) -> Void) {
        guard let response = response as? HTTPURLResponse,
              response.expectedContentLength <= Int64(limit) else {
            finish(.failure(TestOriginTransportError.noVerifiedAddress))
            completionHandler(.cancel); return
        }
        lock.lock(); self.response = response; let active = !finished; lock.unlock()
        completionHandler(active ? .allow : .cancel)
    }

    func urlSession(_ session: URLSession, dataTask: URLSessionDataTask, didReceive bytes: Data) {
        lock.lock()
        guard !finished, bytes.count <= limit - data.count else {
            lock.unlock(); finish(.failure(TestOriginTransportError.noVerifiedAddress)); dataTask.cancel(); return
        }
        data.append(bytes); lock.unlock()
    }

    func urlSession(_ session: URLSession, task: URLSessionTask,
                    didCompleteWithError error: Error?) {
        if let error { finish(.failure(error)); return }
        lock.lock(); let result = response.map { (data, $0) }; lock.unlock()
        if let result { finish(.success(result)) }
        else { finish(.failure(TestOriginTransportError.noVerifiedAddress)) }
    }

    func urlSession(_ session: URLSession, task: URLSessionTask,
                    willPerformHTTPRedirection response: HTTPURLResponse,
                    newRequest request: URLRequest,
                    completionHandler: @escaping (URLRequest?) -> Void) {
        completionHandler(nil)
    }

    private func finish(_ result: Result<(Data, HTTPURLResponse), Error>) {
        lock.lock()
        guard !finished else { lock.unlock(); return }
        finished = true
        let completion = self.completion
        let task = self.task
        self.completion = nil
        self.task = nil
        lock.unlock()
        task?.cancel()
        completion?.resume(with: result)
    }
}

internal enum TestOriginTLSPreflight {
    static func check(_ candidate: TestOriginCandidate, port: UInt16, serverName: String,
                      timeout: TimeInterval, options supplied: NWProtocolTLS.Options?) async -> Bool {
        guard let endpointPort = NWEndpoint.Port(rawValue: port) else { return false }
        let options = supplied ?? NWProtocolTLS.Options()
        sec_protocol_options_set_tls_server_name(options.securityProtocolOptions, serverName)
        let parameters = NWParameters(tls: options, tcp: NWProtocolTCP.Options())
        let connection = NWConnection(host: NWEndpoint.Host(candidate.address), port: endpointPort,
                                      using: parameters)
        let state = TestOriginCompletion(connection: connection)
        return await withTaskCancellationHandler(operation: {
            await withCheckedContinuation { continuation in
                state.install(continuation)
                guard !Task.isCancelled else { state.finish(false); return }
            connection.stateUpdateHandler = { status in
                switch status {
                case .ready: state.finish(true)
                case .failed, .cancelled: state.finish(false)
                default: break
                }
            }
            connection.start(queue: DispatchQueue(label: "ru.robopark.test-origin-preflight", qos: .utility))
            DispatchQueue.global(qos: .utility).asyncAfter(deadline: .now() + timeout) { state.finish(false) }
            }
        }, onCancel: { state.finish(false) })
    }
}

private final class TestOriginCompletion: @unchecked Sendable {
    private let lock = NSLock()
    private var continuation: CheckedContinuation<Bool, Never>?
    private let connection: NWConnection
    private var result: Bool?
    init(connection: NWConnection) {
        self.connection = connection
    }
    func install(_ continuation: CheckedContinuation<Bool, Never>) {
        lock.lock()
        if let result {
            lock.unlock()
            continuation.resume(returning: result)
            return
        }
        self.continuation = continuation
        lock.unlock()
    }
    func finish(_ value: Bool) {
        lock.lock()
        guard result == nil else { lock.unlock(); return }
        result = value
        let continuation = self.continuation
        self.continuation = nil
        lock.unlock()
        connection.stateUpdateHandler = nil
        connection.cancel()
        continuation?.resume(returning: value)
    }
}
