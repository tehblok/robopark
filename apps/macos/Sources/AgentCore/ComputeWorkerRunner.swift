import Foundation
import Darwin

public enum ComputeWorkerRunError: Error { case unavailable, failed, outputTooLarge, timeout }

public final class ComputeWorkerProcess: @unchecked Sendable {
    private let lock = NSLock()
    private var process: Process?

    public init() {}

    public func cancel() {
        let current = lock.withLock { process }
        guard let current, current.isRunning else { return }
        current.terminate()
        let pid = current.processIdentifier
        DispatchQueue.global(qos: .utility).asyncAfter(deadline: .now() + 0.5) {
            if current.isRunning { Darwin.kill(pid, SIGKILL) }
        }
    }

    public func run(frame: Data, timeoutMilliseconds: Int, executable: URL,
                    arguments: [String] = []) async throws -> Data {
        guard frame.count <= 9 * 1024 * 1024, (1...30_000).contains(timeoutMilliseconds),
              FileManager.default.isExecutableFile(atPath: executable.path) else { throw ComputeWorkerRunError.unavailable }
        let directory = FileManager.default.temporaryDirectory.appendingPathComponent("robopark-compute-\(UUID().uuidString)", isDirectory: true)
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: false, attributes: [.posixPermissions: 0o700])
        defer { try? FileManager.default.removeItem(at: directory) }
        let input = directory.appendingPathComponent("input.json")
        let output = directory.appendingPathComponent("output.json")
        try frame.write(to: input, options: .atomic)
        FileManager.default.createFile(atPath: output.path, contents: nil, attributes: [.posixPermissions: 0o600])
        let inputHandle = try FileHandle(forReadingFrom: input)
        let outputHandle = try FileHandle(forWritingTo: output)
        let child = Process()
        child.executableURL = executable; child.arguments = arguments
        child.standardInput = inputHandle; child.standardOutput = outputHandle; child.standardError = FileHandle.nullDevice
        lock.withLock { process = child }
        defer { lock.withLock { process = nil }; try? inputHandle.close(); try? outputHandle.close() }
        return try await withTaskCancellationHandler(operation: {
            try child.run()
            _ = setpriority(PRIO_PROCESS, UInt32(child.processIdentifier), 10)
            return try await withThrowingTaskGroup(of: Data.self) { group in
                group.addTask(priority: .utility) {
                    child.waitUntilExit(); try? outputHandle.synchronize()
                    guard child.terminationStatus == 0 else { throw ComputeWorkerRunError.failed }
                    let values = try output.resourceValues(forKeys: [.fileSizeKey])
                    guard let size = values.fileSize, size <= 12 * 1024 * 1024 else { throw ComputeWorkerRunError.outputTooLarge }
                    return try Data(contentsOf: output, options: [.mappedIfSafe])
                }
                group.addTask {
                    try await Task.sleep(nanoseconds: UInt64(timeoutMilliseconds) * 1_000_000)
                    throw ComputeWorkerRunError.timeout
                }
                do {
                    guard let first = try await group.next() else { throw ComputeWorkerRunError.failed }
                    group.cancelAll(); return first
                } catch {
                    self.cancel(); group.cancelAll(); throw error
                }
            }
        }, onCancel: { self.cancel() })
    }
}
