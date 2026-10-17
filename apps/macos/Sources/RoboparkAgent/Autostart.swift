import Foundation

@MainActor final class AutostartController {
    typealias Runner = ([String], TimeInterval) async throws -> String

    private let label = "ru.robopark.agent"
    private let plist = "/Library/LaunchAgents/ru.robopark.agent.plist"
    private let commandTimeout: TimeInterval
    private let runner: Runner
    private var refreshID: UUID?
    private var revision = 0
    private(set) var enabled = true
    var onChange: (() -> Void)?

    init(commandTimeout: TimeInterval = 3, runner: @escaping Runner = AutostartController.runLaunchctl) {
        self.commandTimeout = commandTimeout
        self.runner = runner
    }

    func refresh() {
        guard refreshID == nil else { return }
        let id = UUID()
        let expectedRevision = revision
        refreshID = id
        Task { [weak self, runner, commandTimeout] in
            let output = try? await runner(["print-disabled", "gui/\(getuid())"], commandTimeout)
            guard let self, self.refreshID == id else { return }
            self.refreshID = nil
            guard self.revision == expectedRevision, let output else { return }
            self.updateCachedValue(!output.contains("\"\(self.label)\" => true"))
        }
    }

    func setEnabled(_ enabled: Bool) async throws {
        _ = try await runner([enabled ? "enable" : "disable", "gui/\(getuid())/\(label)"], commandTimeout)
        if enabled {
            _ = try? await runner(["bootstrap", "gui/\(getuid())", plist], commandTimeout)
        }
        // Disabling applies to the next login. The current workspace remains
        // alive so an update never destroys unsaved operator work.
        revision += 1
        refreshID = nil
        updateCachedValue(enabled)
        refresh()
    }

    private func updateCachedValue(_ value: Bool) {
        guard enabled != value else { return }
        enabled = value
        onChange?()
    }

    nonisolated private static func runLaunchctl(_ arguments: [String], timeout: TimeInterval) async throws -> String {
        try await runCommand(
            executableURL: URL(fileURLWithPath: "/bin/launchctl"), arguments: arguments, timeout: timeout
        )
    }

    nonisolated static func runCommand(
        executableURL: URL, arguments: [String], timeout: TimeInterval
    ) async throws -> String {
        try await withCheckedThrowingContinuation { continuation in
            DispatchQueue.global(qos: .utility).async {
                let process = Process()
                let pipe = Pipe()
                let finished = DispatchSemaphore(value: 0)
                process.executableURL = executableURL
                process.arguments = arguments
                process.standardOutput = pipe
                process.standardError = FileHandle.nullDevice
                process.terminationHandler = { _ in finished.signal() }
                do {
                    try process.run()
                    guard finished.wait(timeout: .now() + timeout) == .success else {
                        process.terminate()
                        throw NSError(domain: "RoboparkAutostart", code: 110)
                    }
                    guard process.terminationReason == .exit, process.terminationStatus == 0 else {
                        throw NSError(domain: "RoboparkAutostart", code: Int(process.terminationStatus))
                    }
                    let data = pipe.fileHandleForReading.readDataToEndOfFile()
                    continuation.resume(returning: String(decoding: data, as: UTF8.self))
                } catch {
                    continuation.resume(throwing: error)
                }
            }
        }
    }
}
