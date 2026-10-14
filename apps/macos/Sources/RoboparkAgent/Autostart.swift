import Foundation

@MainActor final class AutostartController {
    private let label = "ru.robopark.agent"
    private let plist = "/Library/LaunchAgents/ru.robopark.agent.plist"

    func isEnabled() -> Bool {
        guard let output = try? run(["print-disabled", "gui/\(getuid())"]) else { return true }
        return !output.contains("\"\(label)\" => true")
    }

    func setEnabled(_ enabled: Bool) throws {
        _ = try run([enabled ? "enable" : "disable", "gui/\(getuid())/\(label)"])
        if enabled {
            _ = try? run(["bootstrap", "gui/\(getuid())", plist])
        }
        // Disabling applies to the next login. The current workspace remains
        // alive so an update never destroys unsaved operator work.
    }

    @discardableResult private func run(_ arguments: [String]) throws -> String {
        let process = Process()
        let pipe = Pipe()
        process.executableURL = URL(fileURLWithPath: "/bin/launchctl")
        process.arguments = arguments
        process.standardOutput = pipe
        process.standardError = FileHandle.nullDevice
        try process.run(); process.waitUntilExit()
        guard process.terminationReason == .exit, process.terminationStatus == 0 else {
            throw NSError(domain: "RoboparkAutostart", code: Int(process.terminationStatus))
        }
        return String(decoding: pipe.fileHandleForReading.readDataToEndOfFile(), as: UTF8.self)
    }
}
