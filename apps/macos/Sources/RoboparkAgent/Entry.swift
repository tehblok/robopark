import Foundation
import AgentCore
import AppKit
import Darwin

@main struct Entry {
@MainActor static func main() throws {
if CommandLine.arguments.contains("--version") {
    print(Bundle.main.object(forInfoDictionaryKey: "RoboparkReleaseVersion") as? String ?? "development")
} else {
    let directory = FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask)[0].appendingPathComponent("RoboparkAgent")
    try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true, attributes: [.posixPermissions: 0o700])
    let descriptor = open(directory.appendingPathComponent("agent.lock").path, O_CREAT | O_RDWR | O_NOFOLLOW, 0o600)
    guard descriptor >= 0, flock(descriptor, LOCK_EX | LOCK_NB) == 0 else {
        DistributedNotificationCenter.default().postNotificationName(Notification.Name("ru.robopark.agent.open"), object: nil, deliverImmediately: true)
        exit(0)
    }
    let app = NSApplication.shared
    let delegate = AgentApplication()
    app.delegate = delegate
    app.run()
    close(descriptor)
}
}
}
