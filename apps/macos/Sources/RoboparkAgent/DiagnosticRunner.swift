import Foundation
import AgentCore

@MainActor final class DiagnosticRunner {
    private let journal: DiagnosticJournal
    private let bridge: Bridge
    private let session: WorkspaceSession
    private let coordinator = DiagnosticRunCoordinator()
    var running: Bool { coordinator.running }

    init(journal: DiagnosticJournal, bridge: Bridge, session: WorkspaceSession) {
        self.journal = journal; self.bridge = bridge; self.session = session
    }

    func snapshot() -> [String: Any] {
        let events = journal.snapshot().map { event -> [String: Any] in
            var value: [String: Any] = ["id": event.id, "time": event.time,
                "service": event.service, "stage": event.stage, "status": event.status]
            if let code = event.code { value["code"] = code }
            if let elapsed = event.elapsedMs { value["elapsedMs"] = elapsed }
            return value
        }
        let results = coordinator.results.map { result -> [String: Any] in
            var value: [String: Any] = ["service": result.service, "route": result.route,
                "status": result.status, "elapsedMs": result.elapsedMs]
            if let robot = result.robot { value["robot"] = robot }
            if let status = result.httpStatus { value["httpStatus"] = status }
            if let code = result.code { value["code"] = code }
            return value
        }
        return ["events": events, "running": coordinator.running, "results": results]
    }

    func start(body: [String: Any]) -> Bool {
        guard let request = try? DiagnosticRequest(body) else { return false }
        return coordinator.start { [weak self] in
            guard let self else {
                return DiagnosticResult(service: request.service, route: request.route,
                    robot: request.robot, status: "failed", httpStatus: nil,
                    code: "stopped", elapsedMs: 0)
            }
            return await execute(service: request.service, route: request.route, robot: request.robot)
        }
    }

    private func execute(service: String, route: String, robot: String?) async -> DiagnosticResult {
        let started = Date()
        journal.append(service: service, stage: "start", status: "running")
        var httpStatus: Int?
        var code: String?
        do {
            if service == "server" {
                let url = URL(string: "https://\(TestOriginTransport.testHost)/api/health")!
                let (_, response) = try await Network.request(url, timeout: 12)
                httpStatus = response.statusCode
                guard response.statusCode == 200 else { throw NetworkFailure.network }
                journal.append(service: service, stage: "http", status: "ok",
                    elapsedMs: elapsed(started))
                try await websocketChallenge()
                journal.append(service: service, stage: "websocket", status: "ok",
                    elapsedMs: elapsed(started))
            } else if route == "server" {
                let target: String
                switch service {
                case "emergency": target = "/api/emergency/\(try EmergencyTarget.normalizeRobot(robot!))/snapshot"
                case "inventory": target = "/api/sdc-inventory/robots/\(robot!)"
                default: target = "/api/tracker/robots/\(robot!)/tickets"
                }
                let (_, response) = try await session.call(path: target, timeout: 15)
                httpStatus = response.statusCode
                guard (200...299).contains(response.statusCode) else { throw NetworkFailure.network }
            } else {
                try await direct(service: service, robot: robot)
                httpStatus = 200
            }
            journal.append(service: service, stage: "complete", status: "ok",
                elapsedMs: elapsed(started))
        } catch let failure as NetworkFailure {
            code = failure.wireCode
        } catch let failure as CorporateReadError {
            code = failure.wireCode
        } catch is AsyncDeadlineError {
            code = "timeout"
        } catch {
            code = "unavailable"
        }
        let status = code == nil ? "ok" : "failed"
        if let code { journal.append(service: service, stage: "complete", status: status,
            code: code, elapsedMs: elapsed(started)) }
        return DiagnosticResult(service: service, route: route, robot: robot, status: status,
            httpStatus: httpStatus, code: code, elapsedMs: elapsed(started))
    }

    private func direct(service: String, robot: String?) async throws {
        guard let registration = bridge.registration,
              registration.ownerUserID != nil,
              registration.ownerUserID == session.profile?.accountID,
              CorporateAuthPolicy.normalizedLogin(registration.corporateLogin)
                == CorporateAuthPolicy.normalizedLogin(registration.ownerTrackerLogin) else {
            throw NetworkFailure.auth
        }
        if service == "emergency" {
            guard !registration.cookie.isEmpty,
                  registration.corporateCookieVerifiedAt != nil,
                  registration.corporateIdentityStatus == CorporateIdentityStatus.ready.rawValue else {
                throw NetworkFailure.auth
            }
            _ = try await Network.emergency(vin: try EmergencyTarget.normalizeRobot(robot!),
                                            cookie: registration.cookie)
            return
        }
        guard bridge.hasVerifiedCorporateOAuth,
              let token = registration.trackerOAuthToken, !token.isEmpty else { throw NetworkFailure.auth }
        if service == "inventory" {
            let status = await Network.corporateInventoryReadiness(
                serverAuthorizedVIN: robot!, oauthToken: token)
            guard status == .ready else {
                throw status == .denied ? CorporateReadError.auth : CorporateReadError.network
            }
            return
        }
        guard let owner = registration.ownerUserID,
              let ownerLogin = CorporateAuthPolicy.normalizedLogin(registration.ownerTrackerLogin) else {
            throw NetworkFailure.auth
        }
        let encoded = try JSONSerialization.data(withJSONObject: ["service": "tracker",
            "action": "issues_search", "resource": "search",
            "params": ["query": "(Summary: \"\(robot!)\" OR rover: \"a\(robot!)\") AND Resolution: empty()",
                       "per_page": 1, "order": ["createdAt"]],
            "owner_user_id": owner, "owner_tracker_login": ownerLogin,
            "requester_user_id": owner])
        let request = try CorporateReadPolicy.decode(encoded, expectedOwnerUserID: owner,
                                                     expectedOwnerTrackerLogin: ownerLogin)
        _ = try await Network.corporateRead(plan: try CorporateReadPolicy.plan(request),
                                            oauthToken: token, request: request)
    }

    private func websocketChallenge() async throws {
        let url = URL(string: "wss://\(TestOriginTransport.testHost)/api/emergency/bridge/ws")!
        let socket = try Network.serverTransport().webSocketTask(with: url)
        socket.resume()
        defer { socket.cancel(with: .goingAway, reason: nil) }
        let message = try await AsyncDeadline.run(nanoseconds: 8_000_000_000) { try await socket.receive() }
        let data: Data
        switch message {
        case .data(let value): data = value
        case .string(let value): data = Data(value.utf8)
        @unknown default: throw NetworkFailure.invalid
        }
        let challenge = try JSONDecoder().decode(Challenge.self, from: data)
        guard challenge.type == "challenge", challenge.protocolVersion == 1,
              !challenge.nonce.isEmpty, challenge.nonce.utf8.count <= 256,
              challenge.environment == session.profile?.environment,
              challenge.instance_id == session.profile?.instanceID else { throw NetworkFailure.invalid }
    }

    private func elapsed(_ start: Date) -> Int {
        min(300_000, max(0, Int(Date().timeIntervalSince(start) * 1000)))
    }
}
