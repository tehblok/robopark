import Foundation
import CryptoKit
import AgentCore
import IOKit.ps

@MainActor final class Bridge {
    var registration: Registration?
    var onChange: (() -> Void)?
    private(set) var state = "Не зарегистрирован"
    private(set) var upstreamState = "Ожидание проверочного робота"
    var needsCorporateLogin: Bool {
        guard let value = registration else { return false }
        return value.cookie.isEmpty || value.corporateCookieVerifiedAt == nil
            || CorporateAuthPolicy.normalizedLogin(value.corporateLogin) != CorporateAuthPolicy.normalizedLogin(value.ownerTrackerLogin)
            || value.corporateIdentityStatus != CorporateIdentityStatus.ready.rawValue
            || value.trackerOAuthToken?.isEmpty != false
            || (value.trackerOAuthExpiresAt?.timeIntervalSinceNow ?? 0) <= 60
            || value.trackerOAuthVerifiedAt == nil
    }
    private(set) var completed = 0
    private(set) var payloadBytes = 0
    private(set) var transmittedBytes = 0
    private var loop: Task<Void, Never>?
    private var heartbeat: Task<Void, Never>?
    private var livenessTimeout: Task<Void, Never>?
    private var socket: URLSessionWebSocketTask?
    private var jobs: [String: Task<Void, Never>] = [:]
    private var generation = ""
    private var lastProbe = Date.distantPast
    private var readyUntil = Date.distantPast
    private var latency = 1000.0
    private var probing = false
    private var probeTask: Task<Void, Never>?
    private var managedProbes = false
    private var compressedResults = false
    private var heartbeatAcknowledgements = false
    private var connectionWatchdog = ConnectionWatchdog()
    private var runID = UUID()
    private var previousCPUTicks: [UInt64]?
    var hasActiveEmergencyJobs: Bool { !jobs.isEmpty || probeTask != nil }

    init() {
        do {
            if let stored = try SecureStore.load(), TestDeploymentAssignment.matches(
                origin: stored.origin, instanceID: stored.instanceID, environment: stored.environment
            ) {
                var value = stored
                if Self.sanitizeCorporateCredentials(&value) { try SecureStore.save(value) }
                registration = value
            }
        }
        catch { state = SecureStore.failureMessage(error) }
    }
    func reloadRegistration() throws {
        guard registration == nil else { return }
        if let stored = try SecureStore.load(), TestDeploymentAssignment.matches(
            origin: stored.origin, instanceID: stored.instanceID, environment: stored.environment
        ) {
            var value = stored
            if Self.sanitizeCorporateCredentials(&value) { try SecureStore.save(value) }
            registration = value
        }
        onChange?()
    }
    func persist() throws {
        if let registration { try SecureStore.save(registration) }
        onChange?()
    }
    @discardableResult private static func sanitizeCorporateCredentials(_ value: inout Registration,
                                                                         now: Date = Date()) -> Bool {
        var changed = false
        let owner = CorporateAuthPolicy.normalizedLogin(value.ownerTrackerLogin)
        let credentialOwner = CorporateAuthPolicy.normalizedLogin(value.corporateLogin)
        if value.trackerOAuthExpiresAt.map({ $0.timeIntervalSince(now) <= 60 }) ?? false {
            value.trackerOAuthToken = nil; value.trackerOAuthExpiresAt = nil
            value.trackerOAuthVerifiedAt = nil; changed = true
            value.inventoryOAuthVerifiedAt = nil; value.inventoryOAuthStatus = CorporateInventoryStatus.unverifiedHostACL.rawValue
        }
        if owner == nil || credentialOwner != owner {
            if !value.cookie.isEmpty || value.corporateCookieVerifiedAt != nil || value.corporateLogin != nil {
                value.cookie = ""; value.corporateCookieVerifiedAt = nil; value.corporateLogin = nil; changed = true
            }
            if value.corporateIdentityStatus != CorporateIdentityStatus.reauthRequired.rawValue {
                value.corporateIdentityStatus = CorporateIdentityStatus.reauthRequired.rawValue; changed = true
            }
            if value.trackerOAuthToken != nil || value.trackerOAuthExpiresAt != nil || value.trackerOAuthVerifiedAt != nil {
                value.trackerOAuthToken = nil; value.trackerOAuthExpiresAt = nil
                value.trackerOAuthVerifiedAt = nil; changed = true
            }
            if value.inventoryOAuthVerifiedAt != nil
                || value.inventoryOAuthStatus != CorporateInventoryStatus.unverifiedHostACL.rawValue {
                value.inventoryOAuthVerifiedAt = nil
                value.inventoryOAuthStatus = CorporateInventoryStatus.unverifiedHostACL.rawValue
                changed = true
            }
        } else if value.corporateCookieVerifiedAt == nil, !value.cookie.isEmpty {
            value.cookie = ""; value.corporateIdentityStatus = CorporateIdentityStatus.reauthRequired.rawValue
            changed = true
        }
        return changed
    }
    private func bindTrackerLogin(_ rawLogin: String?) throws {
        guard var value = registration else { throw AgentError.ownerMismatch }
        let incoming = CorporateAuthPolicy.normalizedLogin(rawLogin)
        let previous = CorporateAuthPolicy.normalizedLogin(value.ownerTrackerLogin)
        if previous != incoming {
            value.cookie = ""; value.corporateCookieVerifiedAt = nil
            value.trackerOAuthToken = nil; value.trackerOAuthExpiresAt = nil
            value.trackerOAuthVerifiedAt = nil
            value.inventoryOAuthVerifiedAt = nil; value.inventoryOAuthStatus = CorporateInventoryStatus.unverifiedHostACL.rawValue
            value.corporateLogin = nil
            value.corporateIdentityStatus = CorporateIdentityStatus.reauthRequired.rawValue
            readyUntil = .distantPast
        }
        value.ownerTrackerLogin = incoming
        registration = value
    }
    func stop() {
        runID = UUID()
        loop?.cancel(); loop = nil; heartbeat?.cancel(); heartbeat = nil
        livenessTimeout?.cancel(); livenessTimeout = nil; connectionWatchdog.cancel()
        probeTask?.cancel(); probeTask = nil; probing = false
        socket?.cancel(with: .goingAway, reason: nil); socket = nil
        jobs.values.forEach { $0.cancel() }; jobs.removeAll()
        generation = ""; readyUntil = .distantPast
    }
    func start() {
        // Wake may arrive while the first claim is awaiting its response.
        // With no registration there is no connection to restart; preserve
        // that enrollment's generation instead of cancelling it implicitly.
        guard registration != nil else { return }
        stop()
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
                self.livenessTimeout?.cancel(); self.livenessTimeout = nil
                self.connectionWatchdog.cancel()
                self.jobs.values.forEach { $0.cancel() }; self.jobs.removeAll()
                self.socket?.cancel(with: .goingAway, reason: nil); self.socket = nil
                self.readyUntil = .distantPast
                if Task.isCancelled { break }
                try? await Task.sleep(nanoseconds: UInt64((delay + Double.random(in: 0...1)) * 1_000_000_000))
                delay = min(60, delay * 2)
            }
        }
    }
    func startIfNeeded() { if loop == nil { start() } }
    func verifiedInfo(_ server: ServerOrigin) async throws -> (ServerInfo, DeploymentProfile) {
        guard server.url.absoluteString == TestDeploymentAssignment.origin else { throw AgentError.wrongServer }
        let (bytes, response) = try await Network.request(server.api("agent-devices/info"))
        guard response.statusCode == 200 else { throw NetworkFailure.network }
        let info = try JSONDecoder().decode(ServerInfo.self, from: bytes)
        guard info.protocolVersion == 1, TestDeploymentAssignment.matches(
            origin: server.url.absoluteString, instanceID: info.instance_id, environment: info.environment
        ) else { throw AgentError.wrongServer }
        let trusted = try EmbeddedTrust.verify(info.signed_profile, origin: server,
                                              instanceID: info.instance_id, environment: info.environment)
        return (info, trusted)
    }
    func requireTrust() throws {
        guard let profile = registration, TestDeploymentAssignment.matches(
            origin: profile.origin, instanceID: profile.instanceID, environment: profile.environment
        ) else { throw AgentError.wrongServer }
        _ = try EmbeddedTrust.verify(profile.signedProfile, origin: ServerOrigin(profile.origin),
                                     instanceID: profile.instanceID, environment: profile.environment)
    }
    func disconnect() throws {
        stop()
        state = "Отключён"; upstreamState = "Ожидание проверочного робота"
        onChange?()
    }
    func enroll(origin: String, code: String, label: String) async throws {
        guard registration == nil else { throw AgentError.wrongServer }
        let run = runID
        let server = try ServerOrigin(origin)
        let (info, trusted) = try await verifiedInfo(server)
        guard run == runID, !Task.isCancelled else { throw CancellationError() }
        // Keep an unfinished enrollment key through network loss or logout.
        // A fresh one-use code can recover the same active server device; a
        // lost claim response must not consume another device slot each retry.
        let pendingAccount = "enrollment-key-v1-" + SHA256.hash(data: Data((server.url.absoluteString + info.instance_id).utf8)).map { String(format: "%02x", $0) }.joined()
        let key: P256.Signing.PrivateKey
        if let data = try SecureStore.readData(pendingAccount) {
            key = try P256.Signing.PrivateKey(rawRepresentation: data)
        } else {
            key = P256.Signing.PrivateKey()
            try SecureStore.writeData(key.rawRepresentation, account: pendingAccount)
        }
        let (data, claimed) = try await Network.request(server.api("agent-devices/claim"), method: "POST", object: [
            "code": code, "label": String(label.prefix(80)), "environment": info.environment,
            "instance_id": info.instance_id, "public_key": key.publicKey.x963Representation.base64EncodedString()
        ])
        guard run == runID, !Task.isCancelled else { throw CancellationError() }
        guard claimed.statusCode == 201,
              let object = try JSONSerialization.jsonObject(with: data) as? [String: Any],
              let id = object["device_id"] as? String, UUID(uuidString: id) != nil,
              let ownerUserID = object["owner_user_id"] as? Int, ownerUserID > 0,
              object["instance_id"] as? String == info.instance_id,
              object["environment"] as? String == info.environment else { throw AgentError.wrongServer }
        var value = Registration(origin: server.url.absoluteString, deviceID: id, instanceID: info.instance_id,
                                 environment: info.environment, label: label, privateKey: key.rawRepresentation)
        value.ownerUserID = ownerUserID
        value.ownerTrackerLogin = CorporateAuthPolicy.normalizedLogin(object["owner_tracker_login"] as? String)
        value.signedProfile = info.signed_profile
        value.channel = trusted.channel
        value.catalogURL = server.api("distribution/v1/macos/catalog.json").absoluteString
        registration = value
        do { try persist() }
        catch { registration = nil; throw error }
        try? SecureStore.deleteData(pendingAccount)
        start()
    }
    func claimAuthenticated(origin: String, label: String, expectedOwnerUserID: Int,
                            startEmergency: Bool = true,
                            claim: ([String: Any]) async throws -> (Data, HTTPURLResponse)) async throws {
        let run = runID
        let server = try ServerOrigin(origin)
        let (info, trusted) = try await verifiedInfo(server)
        guard run == runID, !Task.isCancelled else { throw CancellationError() }
        let pendingAccount = "account-claim-key-v1-" + SHA256.hash(data: Data((server.url.absoluteString + info.instance_id).utf8)).map { String(format: "%02x", $0) }.joined()
        let key: P256.Signing.PrivateKey
        let existing = registration
        if let existing, existing.origin == server.url.absoluteString, existing.instanceID == info.instance_id {
            key = try P256.Signing.PrivateKey(rawRepresentation: existing.privateKey)
        } else if let data = try SecureStore.readData(pendingAccount) {
            key = try P256.Signing.PrivateKey(rawRepresentation: data)
        } else {
            key = P256.Signing.PrivateKey()
            try SecureStore.writeData(key.rawRepresentation, account: pendingAccount)
        }
        let (data, claimed) = try await claim([
            "label": String(label.prefix(80)), "environment": info.environment,
            "instance_id": info.instance_id, "public_key": key.publicKey.x963Representation.base64EncodedString()
        ])
        guard run == runID, !Task.isCancelled else { throw CancellationError() }
        guard [200, 201].contains(claimed.statusCode) else { throw AgentError.ownerMismatch }
        let authoritative = try JSONDecoder().decode(AuthenticatedDeviceClaim.self, from: data)
        try authoritative.validate(expectedOwnerUserID: expectedOwnerUserID,
                                   existingDeviceID: existing?.deviceID)
        guard authoritative.instanceID == info.instance_id,
              authoritative.environment == info.environment else { throw AgentError.wrongServer }
        var value = Registration(origin: server.url.absoluteString, deviceID: authoritative.deviceID,
                                 instanceID: info.instance_id,
                                 environment: info.environment, label: label, privateKey: key.rawRepresentation)
        value.ownerUserID = authoritative.ownerUserID
        value.ownerTrackerLogin = CorporateAuthPolicy.normalizedLogin(authoritative.ownerTrackerLogin)
        value.signedProfile = info.signed_profile
        value.channel = trusted.channel
        value.catalogURL = server.api("distribution/v1/macos/catalog.json").absoluteString
        if let existing {
            guard existing.ownerUserID == nil || existing.ownerUserID == authoritative.ownerUserID else {
                throw AgentError.ownerMismatch
            }
            if CorporateAuthPolicy.normalizedLogin(existing.ownerTrackerLogin) == value.ownerTrackerLogin {
                value.cookie = existing.cookie
                value.corporateLogin = existing.corporateLogin
                value.corporateCookieVerifiedAt = existing.corporateCookieVerifiedAt
                value.trackerOAuthToken = existing.trackerOAuthToken
                value.trackerOAuthExpiresAt = existing.trackerOAuthExpiresAt
                value.trackerOAuthVerifiedAt = existing.trackerOAuthVerifiedAt
                value.inventoryOAuthVerifiedAt = existing.inventoryOAuthVerifiedAt
                value.inventoryOAuthStatus = existing.inventoryOAuthStatus
                value.corporateIdentityStatus = existing.corporateIdentityStatus
            }
            value.probeVIN = existing.probeVIN
            value.minimumGeneration = existing.minimumGeneration
            value.paused = existing.paused
        }
        registration = value
        do { try persist() }
        catch { registration = nil; throw error }
        try? SecureStore.deleteData(pendingAccount)
        if startEmergency { start() }
    }
    func storeVerifiedOAuth(_ credential: CorporateOAuthCredential, login: String) throws {
        guard credential.isUsable(), let owner = CorporateAuthPolicy.normalizedLogin(registration?.ownerTrackerLogin),
              CorporateAuthPolicy.normalizedLogin(login) == owner else { throw AgentError.ownerMismatch }
        try requireTrust()
        registration?.trackerOAuthToken = credential.token
        registration?.trackerOAuthExpiresAt = credential.expiresAt
        registration?.trackerOAuthVerifiedAt = Date()
        registration?.corporateLogin = owner
        registration?.inventoryOAuthVerifiedAt = nil
        registration?.inventoryOAuthStatus = CorporateInventoryStatus.unverifiedHostACL.rawValue
        try persist()
    }
    var hasVerifiedCorporateOAuth: Bool {
        guard let value = registration, let token = value.trackerOAuthToken, !token.isEmpty,
              let expiry = value.trackerOAuthExpiresAt, expiry.timeIntervalSinceNow > 60,
              let checked = value.trackerOAuthVerifiedAt, Date().timeIntervalSince(checked) < 15 * 60,
              let owner = CorporateAuthPolicy.normalizedLogin(value.ownerTrackerLogin),
              CorporateAuthPolicy.normalizedLogin(value.corporateLogin) == owner else { return false }
        return true
    }
    func verifiedCorporateOAuth(force: Bool = false, now: Date = Date()) async -> (token: String, login: String)? {
        guard let value = registration, let token = value.trackerOAuthToken,
              let expiry = value.trackerOAuthExpiresAt, expiry.timeIntervalSince(now) > 60,
              let owner = CorporateAuthPolicy.normalizedLogin(value.ownerTrackerLogin),
              CorporateAuthPolicy.normalizedLogin(value.corporateLogin) == owner else { return nil }
        if !force, let checked = value.trackerOAuthVerifiedAt, now.timeIntervalSince(checked) < 15 * 60 {
            return (token, owner)
        }
        do {
            let (body, response) = try await Network.corporateIdentity(authorization: "OAuth \(token)")
            guard CorporateAuthPolicy.identityStatus(httpStatus: response.statusCode, body: body,
                                                      expectedLogin: owner) == .ready else {
                registration?.trackerOAuthToken = nil; registration?.trackerOAuthExpiresAt = nil
                registration?.trackerOAuthVerifiedAt = nil; readyUntil = .distantPast
                registration?.inventoryOAuthVerifiedAt = nil
                registration?.inventoryOAuthStatus = CorporateInventoryStatus.unverifiedHostACL.rawValue
                try? persist(); return nil
            }
            registration?.trackerOAuthVerifiedAt = now; try? persist()
            return (token, owner)
        } catch { return nil }
    }
    var inventoryReadiness: CorporateInventoryStatus {
        CorporateInventoryStatus(rawValue: registration?.inventoryOAuthStatus ?? "") ?? .unverifiedHostACL
    }
    func verifyInventoryReadiness(force: Bool = false, now: Date = Date()) async -> CorporateInventoryStatus {
        guard let credential = await verifiedCorporateOAuth(), let vin = registration?.probeVIN, !vin.isEmpty else {
            if registration?.inventoryOAuthVerifiedAt != nil || inventoryReadiness != .unverifiedHostACL {
                registration?.inventoryOAuthVerifiedAt = nil
                registration?.inventoryOAuthStatus = CorporateInventoryStatus.unverifiedHostACL.rawValue
                try? persist()
            }
            return .unverifiedHostACL
        }
        let ttl: TimeInterval = inventoryReadiness == .unreachable ? 60 : 15 * 60
        if !force, let checked = registration?.inventoryOAuthVerifiedAt,
           now.timeIntervalSince(checked) < ttl { return inventoryReadiness }
        let status = await Network.corporateInventoryReadiness(serverAuthorizedVIN: vin,
                                                               oauthToken: credential.token)
        guard registration?.trackerOAuthToken == credential.token,
              registration?.probeVIN == vin else { return .unverifiedHostACL }
        registration?.inventoryOAuthStatus = status.rawValue
        registration?.inventoryOAuthVerifiedAt = now
        try? persist()
        return status
    }
    func acceptServerOwner(ownerUserID: Int, trackerLogin: String?) throws {
        guard ownerUserID == registration?.ownerUserID else { throw AgentError.ownerMismatch }
        try bindTrackerLogin(trackerLogin)
        try persist()
    }
    func invalidateCorporateOAuth() {
        registration?.trackerOAuthToken = nil
        registration?.trackerOAuthExpiresAt = nil
        registration?.trackerOAuthVerifiedAt = nil
        registration?.inventoryOAuthVerifiedAt = nil
        registration?.inventoryOAuthStatus = CorporateInventoryStatus.unverifiedHostACL.rawValue
        try? persist()
    }
    func storeVerifiedSession(_ cookie: String, login: String, verifiedAt: Date = Date()) throws {
        guard registration?.ownerUserID != nil,
              let owner = CorporateAuthPolicy.normalizedLogin(registration?.ownerTrackerLogin),
              CorporateAuthPolicy.normalizedLogin(login) == owner,
              cookie.hasPrefix("Session_id="), cookie.utf8.count > "Session_id=".utf8.count,
              cookie.utf8.count <= 8192,
              !cookie.contains(";"), !cookie.contains("\r"), !cookie.contains("\n") else {
            throw AgentError.ownerMismatch
        }
        try requireTrust()
        registration?.cookie = cookie
        registration?.corporateLogin = owner
        registration?.corporateCookieVerifiedAt = verifiedAt
        registration?.corporateIdentityStatus = CorporateIdentityStatus.ready.rawValue
        readyUntil = .distantPast; lastProbe = .distantPast
        try persist()
    }
    @discardableResult func verifyCorporateSession(force: Bool = false, now: Date = Date()) async -> CorporateIdentityStatus {
        guard let value = registration,
              let owner = CorporateAuthPolicy.normalizedLogin(value.ownerTrackerLogin),
              CorporateAuthPolicy.normalizedLogin(value.corporateLogin) == owner,
              !value.cookie.isEmpty else { return .reauthRequired }
        if !force, let checked = value.corporateCookieVerifiedAt, now.timeIntervalSince(checked) < 15 * 60,
           value.corporateIdentityStatus == CorporateIdentityStatus.ready.rawValue { return .ready }
        let status: CorporateIdentityStatus
        do {
            let (body, response) = try await Network.corporateIdentity(authorization: value.cookie)
            status = CorporateAuthPolicy.identityStatus(httpStatus: response.statusCode, body: body, expectedLogin: owner)
        } catch { status = .networkUnavailable }
        guard registration?.cookie == value.cookie,
              CorporateAuthPolicy.normalizedLogin(registration?.ownerTrackerLogin) == owner else { return .ownerMismatch }
        registration?.corporateIdentityStatus = status.rawValue
        if status == .ready { registration?.corporateCookieVerifiedAt = now }
        else {
            readyUntil = .distantPast
            if status == .reauthRequired || status == .ownerMismatch {
                registration?.cookie = ""; registration?.corporateCookieVerifiedAt = nil
            }
        }
        try? persist()
        return status
    }
    func setProbe(_ value: String) throws {
        let normalized = try EmergencyTarget.normalizeRobot(value)
        guard registration?.probeVIN != normalized else { return }
        registration?.probeVIN = normalized
        registration?.inventoryOAuthVerifiedAt = nil
        registration?.inventoryOAuthStatus = CorporateInventoryStatus.unverifiedHostACL.rawValue
        readyUntil = .distantPast; lastProbe = .distantPast
        try persist()
    }
    func clearProbe() throws {
        registration?.probeVIN = ""
        registration?.inventoryOAuthVerifiedAt = nil
        registration?.inventoryOAuthStatus = CorporateInventoryStatus.unverifiedHostACL.rawValue
        readyUntil = .distantPast; lastProbe = .distantPast
        upstreamState = "Ожидание проверочного робота"
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
    private func receive(_ socket: URLSessionWebSocketTask, timeoutNanoseconds: UInt64? = nil) async throws -> Data {
        let operation: @Sendable () async throws -> Data = {
            switch try await socket.receive() {
            case .data(let data): return data
            case .string(let string): return Data(string.utf8)
            @unknown default: throw NetworkFailure.invalid
            }
        }
        guard let timeoutNanoseconds else { return try await operation() }
        return try await AsyncDeadline.run(nanoseconds: timeoutNanoseconds, operation: operation)
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
        let challenge = try JSONDecoder().decode(
            Challenge.self, from: await receive(channel, timeoutNanoseconds: 20_000_000_000)
        )
        guard runID == run, !Task.isCancelled else { throw CancellationError() }
        let key = try P256.Signing.PrivateKey(rawRepresentation: profile.privateKey)
        let signature = try challenge.sign(key: key, environment: profile.environment, instanceID: profile.instanceID)
        try await send(["type": "auth", "device_id": profile.deviceID, "signature": signature.base64EncodedString()], through: channel)
        guard let accepted = try JSONSerialization.jsonObject(
            with: await receive(channel, timeoutNanoseconds: 20_000_000_000)
        ) as? [String: Any],
              accepted["type"] as? String == "ready", let nextGeneration = accepted["generation"] as? String,
              let readyOwnerID = accepted["owner_user_id"] as? Int,
              readyOwnerID == profile.ownerUserID else { throw AgentError.ownerMismatch }
        guard runID == run, !Task.isCancelled else { throw CancellationError() }
        try bindTrackerLogin(accepted["owner_tracker_login"] as? String)
        try persist()
        _ = await verifyCorporateSession(force: true)
        generation = nextGeneration
        let capabilities = accepted["supports"] as? [String] ?? []
        managedProbes = capabilities.contains("managed_probes")
        compressedResults = capabilities.contains("raw_deflate")
        heartbeatAcknowledgements = capabilities.contains("heartbeat_ack")
        connectionWatchdog.begin(run: run, generation: nextGeneration)
        state = "Соединение с сервером установлено"; onChange?()
        heartbeat = Task { [weak self] in
            while !Task.isCancelled {
                guard let self else { return }
                guard self.runID == run else { return }
                if !self.managedProbes { await self.probeIfNeeded() }
                guard self.runID == run, !Task.isCancelled else { return }
                guard let pulse = self.armLivenessTimeout(run: run, generation: nextGeneration, channel: channel) else {
                    // A slow ACK still has its own deadline. Keep the sender alive.
                    try? await Task.sleep(nanoseconds: 1_000_000_000)
                    continue
                }
                do {
                    try await self.sendHeartbeat()
                    if !self.heartbeatAcknowledgements {
                        channel.sendPing { [weak self] error in
                            Task { @MainActor in
                                guard let self else { return }
                                self.finishLegacyPing(error: error, pulse: pulse, run: run,
                                                      generation: nextGeneration, channel: channel)
                            }
                        }
                    }
                }
                catch { channel.cancel(with: .goingAway, reason: nil); return }
                try? await Task.sleep(nanoseconds: UInt64(Double.random(in: 8.5...10.5) * 1_000_000_000))
            }
        }
        while !Task.isCancelled {
            guard let message = try JSONSerialization.jsonObject(with: await receive(channel)) as? [String: Any] else { throw NetworkFailure.invalid }
            guard runID == run, !Task.isCancelled else { throw CancellationError() }
            switch message["type"] as? String {
            case "heartbeat_ack":
                if message["generation"] as? String == nextGeneration {
                    acknowledgeLiveness(run: run, generation: nextGeneration)
                }
            case "job": try acceptJob(message, channel: channel)
            case "probe": try acceptProbe(message, channel: channel)
            default: continue
            }
        }
    }
    private func armLivenessTimeout(run: UUID, generation: String,
                                    channel: URLSessionWebSocketTask) -> ConnectionHeartbeat? {
        guard let pulse = connectionWatchdog.heartbeatSent(run: run, generation: generation) else { return nil }
        livenessTimeout?.cancel()
        livenessTimeout = Task { [weak self] in
            do { try await Task.sleep(nanoseconds: 25_000_000_000) }
            catch { return }
            guard let self, self.runID == run, self.generation == generation,
                  self.connectionWatchdog.expire(pulse) else { return }
            channel.cancel(with: .goingAway, reason: nil)
        }
        return pulse
    }
    private func acknowledgeLiveness(run: UUID, generation: String) {
        guard connectionWatchdog.acknowledge(run: run, generation: generation) else { return }
        livenessTimeout?.cancel(); livenessTimeout = nil
    }
    private func finishLegacyPing(error: Error?, pulse: ConnectionHeartbeat, run: UUID,
                                  generation: String, channel: URLSessionWebSocketTask) {
        guard runID == run, self.generation == generation else { return }
        if error == nil {
            acknowledgeLiveness(run: run, generation: generation)
        } else if connectionWatchdog.expire(pulse) {
            livenessTimeout?.cancel(); livenessTimeout = nil
            channel.cancel(with: .goingAway, reason: nil)
        }
    }
    private func sendHeartbeat() async throws {
        let power = powerState
        let resources = resourceTelemetry()
        let ready = registration?.paused == false && !thermallyPaused && Date() < readyUntil
        let baseCapacity = power == "ac" ? 2 : 1
        try await send(["type": "heartbeat", "generation": generation,
                        "ready": ready,
                        "latency_ms": latency, "inflight": jobs.count, "active_jobs": jobs.count,
                        "capacity": resources.capacity(base: baseCapacity, activeJobs: jobs.count, ready: ready,
                                                       paused: registration?.paused == true),
                        "load": localLoad, "paused": registration?.paused == true || thermallyPaused,
                        "cpu_percent": resources.cpuPercent, "memory_percent": resources.memoryPercent,
                        "thermal_state": resources.thermalState,
                        "power": power, "supports": ["raw_deflate", "managed_probes", "heartbeat_ack"],
                        "probe_available": registration?.paused == false && !thermallyPaused
                            && registration?.corporateIdentityStatus == CorporateIdentityStatus.ready.rawValue
                            && !(registration?.cookie.isEmpty ?? true) && !(registration?.probeVIN.isEmpty ?? true)])
    }
    private var thermallyPaused: Bool { ProcessInfo.processInfo.thermalState == .critical }
    private var localLoad: Double {
        var load = [Double](repeating: 0, count: 3)
        guard getloadavg(&load, 3) == 3 else { return 0 }
        return min(1, max(0, load[0] / Double(max(1, ProcessInfo.processInfo.activeProcessorCount))))
    }
    private func resourceTelemetry() -> ResourceTelemetry {
        let thermal: String
        switch ProcessInfo.processInfo.thermalState {
        case .nominal: thermal = "normal"
        case .fair: thermal = "fair"
        case .serious: thermal = "serious"
        case .critical: thermal = "critical"
        @unknown default: thermal = "serious"
        }
        var cpuInfo = host_cpu_load_info()
        var cpuCount = mach_msg_type_number_t(MemoryLayout<host_cpu_load_info_data_t>.size / MemoryLayout<integer_t>.size)
        let cpuResult = withUnsafeMutablePointer(to: &cpuInfo) {
            $0.withMemoryRebound(to: integer_t.self, capacity: Int(cpuCount)) {
                host_statistics(mach_host_self(), HOST_CPU_LOAD_INFO, $0, &cpuCount)
            }
        }
        let ticks = [UInt64(cpuInfo.cpu_ticks.0), UInt64(cpuInfo.cpu_ticks.1),
                     UInt64(cpuInfo.cpu_ticks.2), UInt64(cpuInfo.cpu_ticks.3)]
        var cpu = 0.0
        if cpuResult == KERN_SUCCESS, let previous = previousCPUTicks, previous.count == ticks.count {
            let delta = zip(ticks, previous).map { $0 >= $1 ? $0 - $1 : 0 }
            let total = delta.reduce(0, +)
            if total > 0 { cpu = Double(delta[0] + delta[1] + delta[3]) * 100 / Double(total) }
        }
        if cpuResult == KERN_SUCCESS { previousCPUTicks = ticks }

        var vmInfo = vm_statistics64()
        var vmCount = mach_msg_type_number_t(MemoryLayout<vm_statistics64_data_t>.size / MemoryLayout<integer_t>.size)
        let vmResult = withUnsafeMutablePointer(to: &vmInfo) {
            $0.withMemoryRebound(to: integer_t.self, capacity: Int(vmCount)) {
                host_statistics64(mach_host_self(), HOST_VM_INFO64, $0, &vmCount)
            }
        }
        let usedPages = UInt64(vmInfo.active_count) + UInt64(vmInfo.wire_count) + UInt64(vmInfo.compressor_page_count)
        let memory = vmResult == KERN_SUCCESS && ProcessInfo.processInfo.physicalMemory > 0
            ? Double(usedPages) * Double(vm_kernel_page_size) * 100 / Double(ProcessInfo.processInfo.physicalMemory) : 100
        return ResourceTelemetry(cpuPercent: cpu, memoryPercent: memory, thermalState: thermal)
    }
    private var powerState: String {
        if ProcessInfo.processInfo.isLowPowerModeEnabled { return "low_power" }
        guard let info = IOPSCopyPowerSourcesInfo()?.takeRetainedValue(), let value = IOPSGetProvidingPowerSourceType(info)?.takeUnretainedValue() else { return "unknown" }
        return value as String == kIOPSACPowerValue ? "ac" : "battery"
    }
    private func acceptProbe(_ message: [String: Any], channel: URLSessionWebSocketTask) throws {
        guard managedProbes, message["generation"] as? String == generation,
              message["operation"] as? String == "emergency_readiness",
              let id = message["probe_id"] as? String, UUID(uuidString: id) != nil,
              let timeout = message["timeout_ms"] as? Int, timeout > 0, timeout <= 10_000 else { throw AgentError.invalidJob }
        guard probeTask == nil else { return }
        let currentGeneration = generation
        probeTask = Task { [weak self] in
            guard let self else { return }
            defer { if self.generation == currentGeneration { self.probeTask = nil } }
            await self.probeIfNeeded(granted: true, timeout: Double(timeout) / 1000)
            guard !Task.isCancelled, self.generation == currentGeneration else { return }
            try? await self.send(["type": "probe_result", "probe_id": id, "generation": currentGeneration,
                "ready": Date() < self.readyUntil && self.registration?.paused == false && !self.thermallyPaused,
                "latency_ms": self.latency], through: channel)
        }
        // Managed Emergency work preempts compute as soon as the probe lease is accepted.
        onChange?()
    }
    private func probeIfNeeded(granted: Bool = false, timeout: Double = 8) async {
        guard !probing, let profile = registration, !profile.paused, !thermallyPaused,
              !profile.cookie.isEmpty,
              granted || Date().timeIntervalSince(lastProbe) >= 20 else { return }
        let currentRun = runID
        probing = true; lastProbe = Date()
        defer { if runID == currentRun { probing = false; onChange?() } }
        let started = Date()
        do {
            try requireTrust()
            guard await verifyCorporateSession() == .ready else {
                readyUntil = .distantPast
                upstreamState = registration?.corporateIdentityStatus == CorporateIdentityStatus.accessDenied.rawValue
                    ? "Корпоративный доступ запрещён" : "Требуется корпоративный вход"
                return
            }
            guard !profile.probeVIN.isEmpty else {
                readyUntil = .distantPast
                upstreamState = "Ожидание проверочного робота"
                return
            }
            _ = try await Network.emergency(vin: profile.probeVIN, cookie: profile.cookie, timeout: timeout)
            guard !Task.isCancelled, runID == currentRun, registration?.cookie == profile.cookie, registration?.probeVIN == profile.probeVIN, registration?.paused == false else { return }
            latency = latency * 0.7 + max(1, Date().timeIntervalSince(started) * 1000) * 0.3
            readyUntil = Date().addingTimeInterval(managedProbes ? 120 : 30)
            upstreamState = "Корпоративный доступ проверен"
        } catch {
            guard !Task.isCancelled, runID == currentRun else { return }
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
                guard let profile = self.registration, !profile.paused, !self.thermallyPaused, Date() < self.readyUntil else { throw NetworkFailure.network }
                let started = Date()
                let payload = try await Network.emergency(vin: vin, cookie: profile.cookie, timeout: min(10, Double(timeout) / 1000))
                guard !Task.isCancelled, self.registration?.cookie == profile.cookie, self.generation == jobGeneration else { throw CancellationError() }
                self.readyUntil = Date().addingTimeInterval(self.managedProbes ? 120 : 30)
                self.lastProbe = Date()
                self.latency = self.latency * 0.7 + max(1, Date().timeIntervalSince(started) * 1000) * 0.3
                self.upstreamState = "Корпоративный доступ проверен"
                let raw = try JSONSerialization.data(withJSONObject: payload, options: [.sortedKeys])
                answer["ok"] = true
                if self.compressedResults {
                    let packed = try await Task.detached(priority: .utility) { try PayloadCompression.compress(raw) }.value
                    if packed.count * 4 / 3 + 128 < raw.count {
                        answer["encoding"] = "deflate-raw"; answer["payload_base64"] = packed.base64EncodedString()
                        answer["payload_bytes"] = raw.count; answer["encoded_bytes"] = packed.count
                    } else { answer["payload"] = payload }
                } else { answer["payload"] = payload }
                self.payloadBytes += raw.count
                self.transmittedBytes += (try? JSONSerialization.data(withJSONObject: answer).count) ?? raw.count
                self.completed += 1
            } catch {
                self.readyUntil = .distantPast
                answer["ok"] = false; answer["error"] = (error as? NetworkFailure)?.wireCode ?? "network"
                self.upstreamState = "Доступ требует повторной проверки"
            }
            guard !Task.isCancelled, self.generation == jobGeneration else { return }
            try? await self.send(answer, through: channel)
        }
        onChange?()
    }
}
