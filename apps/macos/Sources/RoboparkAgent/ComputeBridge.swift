import Foundation
import CryptoKit
import AgentCore
import ComputePeerCore
import IOKit.ps

@MainActor final class ComputeBridge {
    private struct DirectLease {
        let run: UUID
        let generation: String
        let attemptID: String
    }

    let bridge: Bridge
    var onChange: (() -> Void)?
    private(set) var state = "Ожидание аккаунта"
    private(set) var active = 0
    private(set) var completed = 0
    private(set) var inputBytes = 0
    private(set) var outputBytes = 0
    private var loop: Task<Void, Never>?
    private var heartbeat: Task<Void, Never>?
    private var socket: URLSessionWebSocketTask?
    private var jobs: [String: Task<Void, Never>] = [:]
    private var jobTokens: [String: UUID] = [:]
    private var directLease: DirectLease?
    private var generation = ""
    private var peerAvailable = false
    private var runID = UUID()
    private var previousCPUTicks: [UInt64]?
    private lazy var peer = ComputePeerHost(workerURL: workerURL) { [weak self] message in
        guard let self else { return }
        guard let lease = self.directLease,
              self.runID == lease.run, self.generation == lease.generation,
              message["generation"] as? String == lease.generation,
              message["attempt_id"] as? String == lease.attemptID else { return }
        if message["type"] as? String == "compute_peer_done" {
            self.directLease = nil
            self.active = 0
            if message["ok"] as? Bool == true { self.completed += 1 }
            self.inputBytes += message["input_bytes"] as? Int ?? 0
            self.outputBytes += message["output_bytes"] as? Int ?? 0
            self.onChange?()
        }
        try? await self.send(message)
    }

    init(bridge: Bridge) { self.bridge = bridge }

    private var workerURL: URL {
        (Bundle.main.executableURL ?? URL(fileURLWithPath: CommandLine.arguments[0]))
            .deletingLastPathComponent().appendingPathComponent("RoboparkComputeWorker")
    }

    func start() {
        guard bridge.registration != nil else { return }
        stop(); let run = runID
        loop = Task { [weak self] in
            guard let self else { return }
            var delay = 1.0
            while !Task.isCancelled, self.runID == run {
                do { try await self.connect(run: run); delay = 1 }
                catch { if self.runID == run { self.state = "Вычислительный канал переподключается…"; self.onChange?() } }
                if self.runID == run { self.cleanupConnection() }
                if Task.isCancelled || self.runID != run { return }
                try? await Task.sleep(nanoseconds: UInt64((delay + Double.random(in: 0...1)) * 1_000_000_000))
                delay = min(60, delay * 2)
            }
        }
    }

    func stop() {
        runID = UUID(); loop?.cancel(); loop = nil; cleanupConnection()
        state = bridge.registration == nil ? "Ожидание аккаунта" : "Вычисления остановлены"
        onChange?()
    }

    func emergencyPriorityChanged() {
        guard active > 0, bridge.hasActiveEmergencyJobs else { return }
        // Closing this dedicated channel makes the broker requeue its lease,
        // while cleanup terminates the local worker and direct peer promptly.
        socket?.cancel(with: .goingAway, reason: nil)
    }

    private func cleanupConnection() {
        heartbeat?.cancel(); heartbeat = nil
        jobs.values.forEach { $0.cancel() }; jobs.removeAll(); jobTokens.removeAll(); directLease = nil; active = 0
        peer.cancelAll()
        socket?.cancel(with: .goingAway, reason: nil); socket = nil; generation = ""; peerAvailable = false
    }

    private func send(_ object: [String: Any], through channel: URLSessionWebSocketTask? = nil) async throws {
        guard let channel = channel ?? socket else { throw NetworkFailure.network }
        let data = try JSONSerialization.data(withJSONObject: object)
        guard data.count <= 12 * 1024 * 1024 else { throw NetworkFailure.invalid }
        try await channel.send(.string(String(decoding: data, as: UTF8.self)))
    }

    private func receive(_ channel: URLSessionWebSocketTask) async throws -> Data {
        switch try await channel.receive() {
        case .data(let data): return data
        case .string(let text): return Data(text.utf8)
        @unknown default: throw NetworkFailure.invalid
        }
    }

    private func connect(run: UUID) async throws {
        guard let profile = bridge.registration else { return }
        try bridge.requireTrust()
        let server = try ServerOrigin(profile.origin)
        let channel = Network.session.webSocketTask(with: server.computeWebSocket)
        channel.maximumMessageSize = 12 * 1024 * 1024
        socket = channel; channel.resume()
        let challenge = try JSONDecoder().decode(Challenge.self, from: await receive(channel))
        let key = try P256.Signing.PrivateKey(rawRepresentation: profile.privateKey)
        let signature = try challenge.signCompute(key: key, environment: profile.environment, instanceID: profile.instanceID)
        try await send(["type": "auth", "device_id": profile.deviceID, "signature": signature.base64EncodedString()], through: channel)
        guard let ready = try JSONSerialization.jsonObject(with: await receive(channel)) as? [String: Any],
              ready["type"] as? String == "ready", let nextGeneration = ready["generation"] as? String,
              !nextGeneration.isEmpty else { throw AgentError.wrongServer }
        guard runID == run, !Task.isCancelled else { throw CancellationError() }
        generation = nextGeneration; state = "Готов к вычислениям"; onChange?()
        do { try await peer.startIfNeeded(); peerAvailable = true }
        catch { peerAvailable = false }
        heartbeat = Task { [weak self] in
            while !Task.isCancelled {
                guard let self, self.runID == run else { return }
                do { try await self.sendHeartbeat() } catch { channel.cancel(with: .goingAway, reason: nil); return }
                try? await Task.sleep(nanoseconds: 10_000_000_000)
            }
        }
        while !Task.isCancelled {
            guard let message = try JSONSerialization.jsonObject(with: await receive(channel)) as? [String: Any] else { throw NetworkFailure.invalid }
            guard runID == run else { throw CancellationError() }
            switch message["type"] as? String {
            case "compute_job": try acceptJob(message)
            case "compute_cancel", "cancel": cancel(message)
            case "compute_peer_offer":
                guard peerAvailable else {
                    guard message["generation"] as? String == generation,
                          let jobID = message["job_id"] as? String, UUID(uuidString: jobID) != nil,
                          let attemptID = message["attempt_id"] as? String, UUID(uuidString: attemptID) != nil,
                          let digest = message["payload_sha256"] as? String,
                          digest.range(of: "^[0-9a-f]{64}$", options: .regularExpression) != nil else {
                        throw AgentError.invalidJob
                    }
                    try await send(["type": "compute_peer_done",
                                    "generation": generation, "job_id": jobID,
                                    "attempt_id": attemptID, "payload_sha256": digest,
                                    "ok": false, "input_bytes": 0, "output_bytes": 0, "duration_ms": 0])
                    continue
                }
                let resources = telemetry()
                guard resources.computeCapacity(activeJobs: active, ready: true,
                        paused: bridge.registration?.paused == true || bridge.hasActiveEmergencyJobs,
                        lowPower: ProcessInfo.processInfo.isLowPowerModeEnabled) == 1 else { throw AgentError.invalidJob }
                guard let attempt = message["attempt_id"] as? String, UUID(uuidString: attempt) != nil else {
                    throw AgentError.invalidJob
                }
                try peer.accept(message, generation: generation)
                directLease = DirectLease(run: run, generation: generation, attemptID: attempt)
                active = 1; onChange?()
            default: continue
            }
        }
    }

    private func sendHeartbeat() async throws {
        let resources = telemetry()
        let paused = bridge.registration?.paused == true
        let lowPower = ProcessInfo.processInfo.isLowPowerModeEnabled
        let capacity = resources.computeCapacity(activeJobs: active, ready: true, paused: paused || bridge.hasActiveEmergencyJobs,
                                                 lowPower: lowPower)
        var supports = ["image_resize_v1", "csv_render_v1"]
        if peerAvailable { supports.append("webrtc_datachannel_v1") }
        try await send(["type": "heartbeat", "generation": generation, "capacity": capacity, "active_jobs": active,
                        "cpu_percent": resources.cpuPercent, "memory_percent": resources.memoryPercent,
                        "thermal_state": resources.thermalState, "power": lowPower ? "low_power" : powerState,
                        "supports": supports,
                        "version": Bundle.main.object(forInfoDictionaryKey: "RoboparkReleaseVersion") as? String ?? "development"])
    }

    private func acceptJob(_ message: [String: Any]) throws {
        let resources = telemetry()
        guard message["generation"] as? String == generation,
              let attempt = message["attempt_id"] as? String, UUID(uuidString: attempt) != nil,
              let job = message["job_id"] as? String, UUID(uuidString: job) != nil,
              let timeout = message["timeout_ms"] as? Int, (1...30_000).contains(timeout),
              jobs[attempt] == nil, active == 0, bridge.registration?.paused == false,
              !bridge.hasActiveEmergencyJobs,
              resources.computeCapacity(activeJobs: active, ready: true, paused: false,
                                          lowPower: ProcessInfo.processInfo.isLowPowerModeEnabled) == 1 else {
            throw AgentError.invalidJob
        }
        let bytes = try JSONSerialization.data(withJSONObject: message, options: [.sortedKeys])
        guard bytes.count <= 9 * 1024 * 1024 else { throw AgentError.invalidJob }
        if let encoded = message["payload_base64"] as? String, let payload = Data(base64Encoded: encoded) {
            inputBytes += payload.count
        }
        let leasedGeneration = generation
        let leasedRun = runID
        let jobToken = UUID()
        active = 1; onChange?()
        jobTokens[attempt] = jobToken
        jobs[attempt] = Task { [weak self] in
            guard let self else { return }
            defer {
                if self.runID == leasedRun, self.generation == leasedGeneration,
                   self.jobTokens[attempt] == jobToken {
                    self.jobTokens.removeValue(forKey: attempt)
                    self.jobs.removeValue(forKey: attempt)
                    self.active = self.directLease == nil && self.jobs.isEmpty ? 0 : 1
                    self.onChange?()
                }
            }
            let runner = ComputeWorkerProcess()
            do {
                let result = try await runner.run(frame: bytes, timeoutMilliseconds: timeout, executable: self.workerURL)
                guard !Task.isCancelled, self.runID == leasedRun, self.generation == leasedGeneration,
                      self.jobTokens[attempt] == jobToken else { return }
                guard let object = try JSONSerialization.jsonObject(with: result) as? [String: Any],
                      object["generation"] as? String == leasedGeneration,
                      object["job_id"] as? String == job, object["attempt_id"] as? String == attempt else { throw AgentError.invalidJob }
                self.completed += object["ok"] as? Bool == true ? 1 : 0
                self.outputBytes += result.count
                var wire = object
                if object["ok"] as? Bool != true { wire["error"] = "invalid" }
                try await self.send(wire)
            } catch is CancellationError { }
            catch {
                guard self.runID == leasedRun, self.generation == leasedGeneration,
                      self.jobTokens[attempt] == jobToken else { return }
                try? await self.send(["type": "compute_result", "generation": leasedGeneration, "job_id": job,
                                      "attempt_id": attempt, "payload_sha256": message["payload_sha256"] as? String ?? "",
                                      "ok": false, "error": error is ComputeWorkerRunError ? "worker_failed" : "invalid_job"])
            }
        }
    }

    private func cancel(_ message: [String: Any]) {
        guard message["generation"] as? String == generation, let attempt = message["attempt_id"] as? String else { return }
        jobTokens.removeValue(forKey: attempt)
        jobs.removeValue(forKey: attempt)?.cancel()
        if directLease?.attemptID == attempt { directLease = nil }
        peer.cancel(message)
        active = directLease == nil && jobs.isEmpty ? 0 : 1
        onChange?()
    }

    private func telemetry() -> ResourceTelemetry {
        let thermal: String
        switch ProcessInfo.processInfo.thermalState {
        case .nominal: thermal = "normal"; case .fair: thermal = "fair"
        case .serious: thermal = "serious"; case .critical: thermal = "critical"
        @unknown default: thermal = "serious"
        }
        var cpuInfo = host_cpu_load_info()
        var count = mach_msg_type_number_t(MemoryLayout<host_cpu_load_info_data_t>.size / MemoryLayout<integer_t>.size)
        let result = withUnsafeMutablePointer(to: &cpuInfo) { $0.withMemoryRebound(to: integer_t.self, capacity: Int(count)) { host_statistics(mach_host_self(), HOST_CPU_LOAD_INFO, $0, &count) } }
        let ticks = [UInt64(cpuInfo.cpu_ticks.0), UInt64(cpuInfo.cpu_ticks.1), UInt64(cpuInfo.cpu_ticks.2), UInt64(cpuInfo.cpu_ticks.3)]
        var cpu = 100.0
        if result == KERN_SUCCESS, let previous = previousCPUTicks {
            let delta = zip(ticks, previous).map { $0 >= $1 ? $0 - $1 : 0 }; let total = delta.reduce(0, +)
            if total > 0 { cpu = Double(delta[0] + delta[1] + delta[3]) * 100 / Double(total) }
        }
        if result == KERN_SUCCESS { previousCPUTicks = ticks }
        var vm = vm_statistics64(); var vmCount = mach_msg_type_number_t(MemoryLayout<vm_statistics64_data_t>.size / MemoryLayout<integer_t>.size)
        let vmResult = withUnsafeMutablePointer(to: &vm) { $0.withMemoryRebound(to: integer_t.self, capacity: Int(vmCount)) { host_statistics64(mach_host_self(), HOST_VM_INFO64, $0, &vmCount) } }
        let used = UInt64(vm.active_count) + UInt64(vm.wire_count) + UInt64(vm.compressor_page_count)
        let memory = vmResult == KERN_SUCCESS ? Double(used) * Double(vm_kernel_page_size) * 100 / Double(ProcessInfo.processInfo.physicalMemory) : 100
        return ResourceTelemetry(cpuPercent: cpu, memoryPercent: memory, thermalState: thermal)
    }

    private var powerState: String {
        guard let info = IOPSCopyPowerSourcesInfo()?.takeRetainedValue(), let source = IOPSGetProvidingPowerSourceType(info)?.takeUnretainedValue() else { return "unknown" }
        return source as String == kIOPSACPowerValue ? "ac" : "battery"
    }
}
