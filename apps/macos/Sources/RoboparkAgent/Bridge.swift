import Foundation
import CryptoKit
import AgentCore

@MainActor final class Bridge {
    var registration: Registration?
    var onChange: (() -> Void)?
    private(set) var state = "Не зарегистрирован"
    private(set) var upstreamState = "Войдите в Emergency и укажите проверочный робот"
    private(set) var completed = 0
    private var loop: Task<Void, Never>?
    private var heartbeat: Task<Void, Never>?
    private var socket: URLSessionWebSocketTask?
    private var jobs: [String: Task<Void, Never>] = [:]
    private var generation = ""
    private var lastProbe = Date.distantPast
    private var readyUntil = Date.distantPast
    private var latency = 1000.0
    private var probing = false
    private var runID = UUID()

    init() {
        do { registration = try SecureStore.load() }
        catch { state = "Разблокируйте Keychain и откройте панель повторно" }
    }
    func reloadRegistration() throws {
        guard registration == nil else { return }
        registration = try SecureStore.load()
        if registration != nil { start() }
        onChange?()
    }
    func persist() throws {
        if let registration { try SecureStore.save(registration) }
        onChange?()
    }
    func stop() {
        runID = UUID()
        loop?.cancel(); loop = nil; heartbeat?.cancel(); heartbeat = nil
        socket?.cancel(with: .goingAway, reason: nil); socket = nil
        jobs.values.forEach { $0.cancel() }; jobs.removeAll()
        generation = ""; readyUntil = .distantPast
    }
    func start() {
        stop()
        guard registration != nil else { return }
        let currentRun = runID
        loop = Task { [weak self] in
            guard let self else { return }
            var delay = 1.0
            while !Task.isCancelled {
                do { try await self.connect(run: currentRun); delay = 1 }
                catch {
                    guard self.runID == currentRun else { return }
                    self.state = "Связь с сервером потеряна. Повторное подключение…"; self.onChange?()
                }
                guard self.runID == currentRun else { return }
                self.heartbeat?.cancel(); self.heartbeat = nil
                self.jobs.values.forEach { $0.cancel() }; self.jobs.removeAll()
                self.socket?.cancel(with: .goingAway, reason: nil); self.socket = nil
                self.readyUntil = .distantPast
                if Task.isCancelled { break }
                try? await Task.sleep(nanoseconds: UInt64((delay + Double.random(in: 0...1)) * 1_000_000_000))
                delay = min(60, delay * 2)
            }
        }
    }
    func verifiedInfo(_ server: ServerOrigin) async throws -> (ServerInfo, DeploymentProfile) {
        let (bytes, response) = try await Network.request(server.api("agent-devices/info"))
        guard response.statusCode == 200 else { throw NetworkFailure.network }
        let info = try JSONDecoder().decode(ServerInfo.self, from: bytes)
        guard info.protocolVersion == 1 else { throw AgentError.wrongServer }
        let trusted = try EmbeddedTrust.verify(info.signed_profile, origin: server,
                                              instanceID: info.instance_id, environment: info.environment)
        return (info, trusted)
    }
    func requireTrust() throws {
        guard let profile = registration else { throw AgentError.wrongServer }
        _ = try EmbeddedTrust.verify(profile.signedProfile, origin: ServerOrigin(profile.origin),
                                     instanceID: profile.instanceID, environment: profile.environment)
    }
    func disconnect() throws {
        stop()
        try SecureStore.remove()
        registration = nil
        state = "Не зарегистрирован"; upstreamState = "Войдите в Emergency и укажите проверочный робот"
        onChange?()
    }
    func enroll(origin: String, code: String, label: String) async throws {
        guard registration == nil else { throw AgentError.wrongServer }
        let server = try ServerOrigin(origin)
        let (info, trusted) = try await verifiedInfo(server)
        let key = P256.Signing.PrivateKey()
        let (data, claimed) = try await Network.request(server.api("agent-devices/claim"), method: "POST", object: [
            "code": code, "label": String(label.prefix(80)), "environment": info.environment,
            "instance_id": info.instance_id, "public_key": key.publicKey.x963Representation.base64EncodedString()
        ])
        guard claimed.statusCode == 201,
              let object = try JSONSerialization.jsonObject(with: data) as? [String: Any],
              let id = object["device_id"] as? String, UUID(uuidString: id) != nil,
              object["instance_id"] as? String == info.instance_id,
              object["environment"] as? String == info.environment else { throw AgentError.wrongServer }
        var value = Registration(origin: server.url.absoluteString, deviceID: id, instanceID: info.instance_id,
                                 environment: info.environment, label: label, privateKey: key.rawRepresentation)
        value.signedProfile = info.signed_profile
        value.channel = trusted.channel
        value.catalogURL = server.api("distribution/v1/macos/catalog.json").absoluteString
        registration = value
        try persist()
        start()
    }
    func updateCookie(_ value: String) throws {
        guard registration != nil, value.utf8.count <= 32 * 1024,
              !value.contains("\r"), !value.contains("\n") else { throw NetworkFailure.invalid }
        try requireTrust()
        registration?.cookie = value
        readyUntil = .distantPast; lastProbe = .distantPast
        try persist()
    }
    func setProbe(_ value: String) throws {
        registration?.probeVIN = try EmergencyTarget.normalizeRobot(value)
        readyUntil = .distantPast; lastProbe = .distantPast
        try persist()
    }
    func pause(_ value: Bool) throws {
        registration?.paused = value
        if value { readyUntil = .distantPast }
        try persist()
        Task { try? await sendHeartbeat() }
    }
    private func send(_ object: [String: Any], through channel: URLSessionWebSocketTask? = nil) async throws {
        guard let socket = channel ?? socket else { throw NetworkFailure.network }
        let data = try JSONSerialization.data(withJSONObject: object)
        guard data.count <= 2 * 1024 * 1024 else { throw NetworkFailure.invalid }
        try await socket.send(.string(String(decoding: data, as: UTF8.self)))
    }
    private func receive(_ socket: URLSessionWebSocketTask) async throws -> Data {
        switch try await socket.receive() {
        case .data(let data): return data
        case .string(let string): return Data(string.utf8)
        @unknown default: throw NetworkFailure.invalid
        }
    }
    private func connect(run: UUID) async throws {
        guard let profile = registration else { return }
        let server = try ServerOrigin(profile.origin)
        let (info, trusted) = try await verifiedInfo(server)
        guard runID == run, info.instance_id == profile.instanceID, info.environment == profile.environment else { throw AgentError.wrongServer }
        registration?.signedProfile = info.signed_profile; registration?.channel = trusted.channel
        try persist()
        state = "Подключение…"; onChange?()
        let channel = Network.session.webSocketTask(with: server.webSocket)
        channel.maximumMessageSize = 2 * 1024 * 1024
        socket = channel; channel.resume()
        let challenge = try JSONDecoder().decode(Challenge.self, from: await receive(channel))
        guard runID == run, !Task.isCancelled else { throw CancellationError() }
        let key = try P256.Signing.PrivateKey(rawRepresentation: profile.privateKey)
        let signature = try challenge.sign(key: key, environment: profile.environment, instanceID: profile.instanceID)
        try await send(["type": "auth", "device_id": profile.deviceID, "signature": signature.base64EncodedString()], through: channel)
        guard let accepted = try JSONSerialization.jsonObject(with: await receive(channel)) as? [String: Any],
              accepted["type"] as? String == "ready", let nextGeneration = accepted["generation"] as? String else { throw AgentError.wrongServer }
        guard runID == run, !Task.isCancelled else { throw CancellationError() }
        generation = nextGeneration
        state = "Соединение с сервером установлено"; onChange?()
        heartbeat = Task { [weak self] in
            while !Task.isCancelled {
                guard let self else { return }
                guard self.runID == run else { return }
                await self.probeIfNeeded()
                guard self.runID == run, !Task.isCancelled else { return }
                do { try await self.sendHeartbeat() }
                catch { channel.cancel(with: .goingAway, reason: nil); return }
                try? await Task.sleep(nanoseconds: 10_000_000_000)
            }
        }
        while !Task.isCancelled {
            guard let message = try JSONSerialization.jsonObject(with: await receive(channel)) as? [String: Any] else { throw NetworkFailure.invalid }
            guard runID == run, !Task.isCancelled else { throw CancellationError() }
            guard message["type"] as? String == "job" else { continue }
            try acceptJob(message, channel: channel)
        }
    }
    private func sendHeartbeat() async throws {
        try await send(["type": "heartbeat", "generation": generation,
                        "ready": registration?.paused == false && Date() < readyUntil,
                        "latency_ms": latency, "inflight": jobs.count])
    }
    private func probeIfNeeded() async {
        guard !probing, let profile = registration, !profile.paused,
              !profile.probeVIN.isEmpty, !profile.cookie.isEmpty,
              Date().timeIntervalSince(lastProbe) >= 20 else { return }
        probing = true; lastProbe = Date()
        defer { probing = false; onChange?() }
        let started = Date()
        do {
            _ = try await Network.emergency(vin: profile.probeVIN, cookie: profile.cookie, timeout: 8)
            guard !Task.isCancelled, registration?.cookie == profile.cookie, registration?.probeVIN == profile.probeVIN, registration?.paused == false else { return }
            latency = max(1, Date().timeIntervalSince(started) * 1000)
            readyUntil = Date().addingTimeInterval(30)
            upstreamState = "Корпоративный доступ проверен"
        } catch {
            readyUntil = .distantPast
            upstreamState = (error as? NetworkFailure) == .auth ? "Требуется вход в Emergency" : "Корпоративный доступ недоступен"
        }
    }
    private func acceptJob(_ message: [String: Any], channel: URLSessionWebSocketTask) throws {
        guard message["operation"] as? String == "robot_payload", message["generation"] as? String == generation,
              let id = message["job_id"] as? String, UUID(uuidString: id) != nil,
              let attempt = message["attempt_id"] as? String, UUID(uuidString: attempt) != nil,
              let vin = message["vin"] as? String, let timeout = message["timeout_ms"] as? Int,
              timeout > 0, timeout <= 20_000, jobs[attempt] == nil, jobs.count < 2 else { throw AgentError.invalidJob }
        _ = try EmergencyTarget.url(vin: vin)
        try requireTrust()
        let jobGeneration = generation
        jobs[attempt] = Task { [weak self] in
            guard let self else { return }
            defer { self.jobs.removeValue(forKey: attempt); self.onChange?() }
            var answer: [String: Any] = ["type": "result", "job_id": id, "attempt_id": attempt, "generation": jobGeneration]
            do {
                guard let profile = self.registration, !profile.paused, Date() < self.readyUntil else { throw NetworkFailure.network }
                let payload = try await Network.emergency(vin: vin, cookie: profile.cookie, timeout: min(10, Double(timeout) / 1000))
                answer["ok"] = true; answer["payload"] = payload
                self.completed += 1
            } catch {
                self.readyUntil = .distantPast
                answer["ok"] = false; answer["error"] = (error as? NetworkFailure)?.wireCode ?? "network"
                self.upstreamState = "Доступ требует повторной проверки"
            }
            guard !Task.isCancelled, self.generation == jobGeneration else { return }
            try? await self.send(answer, through: channel)
        }
    }
}
