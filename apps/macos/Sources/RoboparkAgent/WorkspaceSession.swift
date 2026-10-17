import Foundation
import AgentCore

struct WorkspaceProfile: Codable {
    let origin: String
    let instanceID: String
    let environment: String
    var signedProfile: SignedCatalog?
    var cookies: [WorkspaceCookie] = []
    var ownerUserID: Int? = nil
    var accountID: Int?
}

/// Native cookie jar: server credentials are never forwarded to browser storage.
@MainActor final class WorkspaceSession {
    private(set) var profile: WorkspaceProfile?
    private var generation = UUID() {
        didSet { renewalTask?.cancel(); renewalTask = nil; identityCheckedAt = nil }
    }
    private var deviceAuthMaterial: Registration?
    private var renewalTask: Task<Bool, Error>?
    private var identityCheckedAt: Date?
    private var transitioning = false
    var onIdentity: (([String: Any]) async -> Void)?
    var onLogout: (() async -> Void)?
    var onSessionInvalidated: (() -> Void)?
    private(set) var lastSuccessAt: Date?
    private(set) var connectionState = "connecting"
    private let transport: URLSession

    // The injected session is used by hermetic lifecycle checks. Production
    // always takes the nil path below, preserving the isolated cookie/cache
    // policy and redirect-denying delegate.
    init(transport injectedTransport: URLSession? = nil) throws {
        if let data = try SecureStore.readData("workspace-profile-v1") {
            let stored = try JSONDecoder().decode(WorkspaceProfile.self, from: data)
            if TestDeploymentAssignment.matches(origin: stored.origin, instanceID: stored.instanceID,
                                                environment: stored.environment) { profile = stored }
            if SecureStore.isSignedOut("workspace") { profile?.cookies = []; profile?.accountID = nil }
        }
        if let injectedTransport {
            transport = injectedTransport
        } else {
            let config = URLSessionConfiguration.ephemeral
            config.httpCookieStorage = nil; config.httpShouldSetCookies = false; config.urlCache = nil
            config.timeoutIntervalForRequest = 30; config.timeoutIntervalForResource = 90
            config.httpMaximumConnectionsPerHost = 6
            try TestOriginTransport.shared.configure(config)
            transport = URLSession(configuration: config, delegate: Network.policy, delegateQueue: nil)
        }
    }
    func configure(origin: ServerOrigin, info: ServerInfo) throws {
        guard TestDeploymentAssignment.matches(origin: origin.url.absoluteString,
                                               instanceID: info.instance_id,
                                               environment: info.environment) else { throw AgentError.wrongServer }
        if profile?.origin == origin.url.absoluteString, profile?.instanceID == info.instance_id {
            profile?.signedProfile = info.signed_profile
        } else {
            generation = UUID()
            profile = WorkspaceProfile(origin: origin.url.absoluteString, instanceID: info.instance_id,
                                       environment: info.environment, signedProfile: info.signed_profile)
        }
        try persist()
    }
    func useRegistration(_ registration: Registration) throws {
        guard TestDeploymentAssignment.matches(origin: registration.origin,
                                               instanceID: registration.instanceID,
                                               environment: registration.environment) else { throw AgentError.wrongServer }
        if profile?.origin == registration.origin, profile?.instanceID == registration.instanceID {
            var changed = false
            if let ownerUserID = registration.ownerUserID {
                let binding = try DeviceOwnerBinding(ownerUserID: profile?.ownerUserID,
                                                     activeUserID: profile?.accountID)
                    .bindingAuthoritativeOwner(ownerUserID)
                changed = profile?.ownerUserID != binding.ownerUserID || profile?.accountID != binding.activeUserID
                profile?.ownerUserID = binding.ownerUserID
                profile?.accountID = binding.activeUserID
            }
            if profile?.signedProfile?.payload != registration.signedProfile?.payload {
                profile?.signedProfile = registration.signedProfile
                changed = true
            }
            if changed { try persist() }
            deviceAuthMaterial = registration
            return
        }
        guard profile == nil else { return }
        profile = WorkspaceProfile(origin: registration.origin, instanceID: registration.instanceID,
                                   environment: registration.environment, signedProfile: registration.signedProfile,
                                   ownerUserID: registration.ownerUserID)
        try persist()
        deviceAuthMaterial = registration
    }
    func clear() throws {
        SecureStore.markSignedOut("workspace", true)
        generation = UUID(); profile = nil
        onSessionInvalidated?()
        try SecureStore.deleteData("workspace-profile-v1")
    }
    private func persist() throws {
        if let profile { try SecureStore.writeData(JSONEncoder().encode(profile), account: "workspace-profile-v1") }
    }
    private func acceptIdentity(_ user: [String: Any], previousAccountID: Int? = nil) throws -> Int {
        guard let userID = user["id"] as? Int else { throw NetworkFailure.invalid }
        if let previousAccountID, previousAccountID != userID { throw AgentError.ownerMismatch }
        let binding = try DeviceOwnerBinding(ownerUserID: profile?.ownerUserID,
                                             activeUserID: profile?.accountID)
            .acceptingAuthenticatedUser(userID)
        profile?.ownerUserID = binding.ownerUserID
        profile?.accountID = binding.activeUserID
        try persist()
        identityCheckedAt = Date()
        return userID
    }
    private func rejectIdentity() async {
        SecureStore.markSignedOut("workspace", true)
        generation = UUID()
        profile?.cookies = []
        profile?.accountID = nil
        onSessionInvalidated?()
        try? persist()
        await onLogout?()
    }
    func call(path: String, method: String = "GET", object: [String: Any]? = nil,
              headers additionalHeaders: [String: String] = [:], timeout: TimeInterval = 30) async throws -> (Data, HTTPURLResponse) {
        var headers: [String: String] = ["accept": "application/json"]
        for (name, value) in additionalHeaders { headers[name] = value }
        let body = try object.map { try JSONSerialization.data(withJSONObject: $0) } ?? Data()
        if object != nil { headers["content-type"] = "application/json" }
        return try await request(target: path, method: method, headers: headers, body: body, timeout: timeout)
    }
    var hasStoredIdentity: Bool {
        guard let current = profile, !SecureStore.isSignedOut("workspace") else { return false }
        return !current.cookies.isEmpty || (current.accountID != nil && current.accountID == deviceAuthMaterial?.ownerUserID)
    }
    func restoreIdentity() async -> Bool {
        guard hasStoredIdentity, let current = profile else { return false }
        do {
            let (bytes, response) = try await call(path: "/api/auth/me", timeout: 10)
            guard response.statusCode == 200,
                  let user = try JSONSerialization.jsonObject(with: bytes) as? [String: Any] else {
                if [401, 403].contains(response.statusCode) || response.statusCode == 200 {
                    await rejectIdentity()
                }
                return false
            }
            do { _ = try acceptIdentity(user, previousAccountID: current.accountID) }
            catch { await rejectIdentity(); return false }
            await onIdentity?(user)
            return true
        } catch is CancellationError {
            return false
        } catch {
            return false
        }
    }
    func request(target: String, method: String, headers: [String: String], body: Data, timeout: TimeInterval = 30) async throws -> (Data, HTTPURLResponse) {
        let path = String(target.split(separator: "?", maxSplits: 1).first ?? "")
        let accountRequest = path.hasPrefix("/api/auth/") || path.hasPrefix("/api/privileged-auth/")
        let safeRead = ["GET", "HEAD"].contains(method)
        // Check identity before a write after inactivity, so the write itself
        // is sent once. Never replay an arbitrary mutation after a 401.
        if !safeRead, !accountRequest, profile?.accountID != nil,
           identityCheckedAt.map({ Date().timeIntervalSince($0) > 300 }) ?? true {
            let (identityData, identity) = try await request(target: "/api/auth/me", method: "GET", headers: [:], body: Data(), timeout: 10)
            guard identity.statusCode == 200 else { return (Data(), identity) }
            guard let user = try JSONSerialization.jsonObject(with: identityData) as? [String: Any] else { throw NetworkFailure.invalid }
            do { _ = try acceptIdentity(user, previousAccountID: profile?.accountID) }
            catch { await rejectIdentity(); throw error }
        }
        let run = generation
        let result = try await performRequest(target: target, method: method, headers: headers, body: body, timeout: timeout)
        guard result.1.statusCode == 401, path == "/api/auth/me" || !accountRequest else { return result }
        if try await renewDeviceSession(identityAlreadyUnauthorized: path == "/api/auth/me") {
            guard generation == run else { throw CancellationError() }
            if safeRead {
                return try await performRequest(target: target, method: method, headers: headers, body: body, timeout: timeout)
            }
            // Session is restored; the caller can retry explicitly/idempotently.
            throw NetworkFailure.network
        }
        return result
    }
    private func renewDeviceSession(identityAlreadyUnauthorized: Bool) async throws -> Bool {
        guard !SecureStore.isSignedOut("workspace"), !transitioning,
              let registration = deviceAuthMaterial,
              let owner = registration.ownerUserID, owner == profile?.ownerUserID,
              owner == profile?.accountID else { return false }
        if let renewalTask { return try await renewalTask.value }
        let run = generation
        let task = Task { @MainActor [weak self] () throws -> Bool in
            guard let self else { return false }
            if !identityAlreadyUnauthorized {
                let (identityData, response) = try await self.performRequest(target: "/api/auth/me", method: "GET", headers: [:], body: Data(), timeout: 10)
                if response.statusCode == 200 {
                    guard let user = try JSONSerialization.jsonObject(with: identityData) as? [String: Any] else { throw NetworkFailure.invalid }
                    do { _ = try self.acceptIdentity(user, previousAccountID: owner) }
                    catch { await self.rejectIdentity(); return false }
                    return true // Another in-flight request may already have renewed the jar.
                }
                if response.statusCode >= 500 || response.statusCode == 429 { throw NetworkFailure.network }
                guard response.statusCode == 401 else { return false }
            }
            let jsonHeaders = ["content-type": "application/json", "accept": "application/json"]
            let challengeBody = try JSONSerialization.data(withJSONObject: ["device_id": registration.deviceID])
            let (data, response) = try await self.performRequest(target: "/api/auth/device-auth/challenge", method: "POST",
                headers: jsonHeaders, body: challengeBody, timeout: 10)
            if response.statusCode >= 500 || response.statusCode == 429 { throw NetworkFailure.network }
            guard response.statusCode == 200 else { return false }
            guard data.count <= 4096, self.generation == run, !Task.isCancelled,
                  !SecureStore.isSignedOut("workspace") else { throw CancellationError() }
            let challenge = try JSONDecoder().decode(DeviceSessionChallenge.self, from: data)
            let signature = try challenge.signature(for: registration)
            let renewBody = try JSONSerialization.data(withJSONObject: ["device_id": registration.deviceID,
                "challenge_id": challenge.challenge_id, "nonce": challenge.nonce, "signature": signature])
            let (_, renewed) = try await self.performRequest(target: "/api/auth/device-auth/renew", method: "POST",
                headers: jsonHeaders, body: renewBody, timeout: 10)
            if renewed.statusCode >= 500 || renewed.statusCode == 429 { throw NetworkFailure.network }
            guard renewed.statusCode == 204 else { return false }
            guard self.generation == run, !Task.isCancelled, !SecureStore.isSignedOut("workspace") else { throw CancellationError() }
            let (identityData, identity) = try await self.performRequest(target: "/api/auth/me", method: "GET", headers: [:], body: Data(), timeout: 10)
            if identity.statusCode >= 500 || identity.statusCode == 429 { throw NetworkFailure.network }
            guard identity.statusCode == 200,
                  let user = try JSONSerialization.jsonObject(with: identityData) as? [String: Any] else { return false }
            do { _ = try self.acceptIdentity(user, previousAccountID: owner) }
            catch { await self.rejectIdentity(); return false }
            return true
        }
        renewalTask = task
        defer { if generation == run { renewalTask = nil } }
        return try await task.value
    }
    private func performRequest(target: String, method: String, headers: [String: String], body: Data, timeout: TimeInterval = 30) async throws -> (Data, HTTPURLResponse) {
        guard let current = profile else { throw AgentError.wrongServer }
        guard TestDeploymentAssignment.matches(origin: current.origin, instanceID: current.instanceID,
                                               environment: current.environment) else { throw AgentError.wrongServer }
        let origin = try ServerOrigin(current.origin)
        _ = try EmbeddedTrust.verify(current.signedProfile, origin: origin, instanceID: current.instanceID, environment: current.environment)
        let run = generation
        var request = try WorkspaceProxyPolicy.request(origin: origin, target: target, method: method, headers: headers, body: body, cookies: current.cookies)
        request.timeoutInterval = min(30, max(1, timeout))
        let url = request.url!
        let data: Data
        let response: HTTPURLResponse
        do {
            (data, response) = try await BoundedHTTPReader.read(request, using: transport,
                timeout: request.timeoutInterval, limit: 32 * 1024 * 1024)
        } catch { if generation == run { connectionState = "offline" }; throw error }
        guard !Task.isCancelled, generation == run else { throw CancellationError() }
        let mediaType = response.value(forHTTPHeaderField: "Content-Type")?
            .split(separator: ";", maxSplits: 1).first?
            .trimmingCharacters(in: .whitespacesAndNewlines).lowercased()
        // The API's not-found response is JSON. Tuna returns an HTML 404 when
        // the tunnel itself is absent, which must remain retryable and offline.
        if response.statusCode == 404, mediaType == "text/html" {
            connectionState = "offline"
            throw NetworkFailure.network
        }
        // Redirects are blocked by NetworkPolicy. Never convert one into a local
        // navigation or forward the native cookie to its Location.
        if (300...399).contains(response.statusCode), response.statusCode != 304 { throw NetworkFailure.invalid }
        let fields = response.allHeaderFields.reduce(into: [String: String]()) { if let key = $1.key as? String { $0[key] = String(describing: $1.value) } }
        let incoming = HTTPCookie.cookies(withResponseHeaderFields: fields, for: url)
        for cookie in incoming {
            let domain = cookie.domain.trimmingCharacters(in: CharacterSet(charactersIn: ".")).lowercased()
            guard domain == url.host?.lowercased(), cookie.isSecure,
                  cookie.name.utf8.count <= 128, cookie.value.utf8.count <= 8192,
                  !cookie.name.contains(";"), !cookie.value.contains(";"),
                  !cookie.value.utf8.contains(13), !cookie.value.utf8.contains(10) else { continue }
            profile?.cookies.removeAll { $0.name == cookie.name && $0.path == cookie.path }
            if cookie.expiresDate == nil || cookie.expiresDate! > Date() {
                profile?.cookies.append(WorkspaceCookie(name: cookie.name, value: cookie.value, path: cookie.path, expires: cookie.expiresDate))
            }
        }
        guard (profile?.cookies.count ?? 0) <= 16 else { profile?.cookies = []; try persist(); throw NetworkFailure.invalid }
        if !incoming.isEmpty { try persist() }
        let transientFailure = response.statusCode >= 500 || [408, 425, 429].contains(response.statusCode)
        if !transientFailure { lastSuccessAt = Date() }
        connectionState = transientFailure ? "error" : "connected"
        return (data, response)
    }
    func proxy(_ local: LocalHTTPRequest) async -> LocalHTTPResponse {
        guard !transitioning else { return .json(["detail": "account_transition_in_progress"], status: 503) }
        let login = local.path == "/api/auth/login" && local.method == "POST"
        let logout = local.path == "/api/auth/logout" && local.method == "POST"
        let identity = local.path == "/api/auth/me" && local.method == "GET"
        let ownerVerified = profile?.ownerUserID != nil && profile?.ownerUserID == profile?.accountID
        let hasPendingIdentity = profile?.cookies.isEmpty == false || profile?.accountID != nil
        if hasPendingIdentity, !ownerVerified, !login, !logout, !identity {
            return .json(["detail": "device_owner_verification_required"], status: 403)
        }
        if login || logout {
            transitioning = true; generation = UUID()
            SecureStore.markSignedOut("workspace", true)
            onSessionInvalidated?()
        }
        defer { if login || logout { transitioning = false } }
        do {
            if login || logout {
                await onLogout?()
                if login { profile?.cookies = []; profile?.accountID = nil; try persist() }
            }
            let (bytes, response) = try await request(target: local.target, method: local.method, headers: local.headers, body: local.body, timeout: login || identity ? 10 : 30)
            if login, (200...299).contains(response.statusCode) {
                try persist()
                SecureStore.markSignedOut("workspace", false)
            }
            if local.path == "/api/auth/logout", local.method == "POST" {
                generation = UUID(); profile?.cookies = []; profile?.accountID = nil; try persist()
            }
            if identity, response.statusCode == 200,
               let user = try JSONSerialization.jsonObject(with: bytes) as? [String: Any] {
                do { _ = try acceptIdentity(user) }
                catch {
                    await rejectIdentity()
                    return .json(["detail": "device_owner_mismatch"], status: 403)
                }
                await onIdentity?(user)
            }
            if identity, [401, 403].contains(response.statusCode) {
                await rejectIdentity()
            }
            var original: [String: String] = [:]
            for (key, value) in response.allHeaderFields {
                guard let key = key as? String else { continue }
                original[key] = String(describing: value)
            }
            return LocalHTTPResponse(status: response.statusCode, headers: WorkspaceProxyPolicy.responseHeaders(original), body: bytes)
        } catch {
            if local.path == "/api/auth/logout", local.method == "POST" {
                generation = UUID(); profile?.cookies = []; profile?.accountID = nil; try? persist()
                return LocalHTTPResponse(status: 204)
            }
            return .json(["detail": "desktop_server_unavailable"], status: 503)
        }
    }
}
