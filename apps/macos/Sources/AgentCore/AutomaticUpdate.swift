import Foundation
import Darwin

public enum ApplicationInstallPathPolicy {
    /// `/Applications` is normally root:admin 0775 on macOS. The app bundle
    /// below it remains root-owned and non-writable by group or others.
    public static func allowsApplicationsDirectory(owner: UInt32, group: UInt32, permissions: Int) -> Bool {
        owner == 0 && permissions & 0o002 == 0 &&
            (permissions & 0o020 == 0 || group == 80)
    }
}

public struct SecureUpdateStorage {
    public let root: URL
    public let expectedOwner: uid_t
    public let trustedAncestors: [URL]

    public init(root: URL, expectedOwner: uid_t, trustedAncestors: [URL]) {
        self.root = root.standardizedFileURL
        self.expectedOwner = expectedOwner
        self.trustedAncestors = trustedAncestors.map(\.standardizedFileURL)
    }

    public func validate() throws {
        for directory in trustedAncestors + [root] {
            let values = try directory.resourceValues(forKeys: [.isDirectoryKey, .isSymbolicLinkKey])
            let attributes = try FileManager.default.attributesOfItem(atPath: directory.path)
            guard values.isDirectory == true, values.isSymbolicLink != true,
                  (attributes[.ownerAccountID] as? NSNumber)?.uint32Value == expectedOwner,
                  ((attributes[.posixPermissions] as? NSNumber)?.intValue ?? 0) & 0o022 == 0 else {
                throw AgentError.invalidCatalog
            }
        }
    }

    public func loadState() throws -> Data? {
        try validate()
        let url = root.appendingPathComponent("state.json")
        if access(url.path, F_OK) != 0 {
            guard errno == ENOENT else { throw AgentError.invalidCatalog }
            return nil
        }
        return try regularFile(url, maximum: 64 * 1024)
    }

    public func writeState(_ data: Data) throws {
        try validate()
        guard !data.isEmpty, data.count <= 64 * 1024 else { throw AgentError.invalidCatalog }
        let url = root.appendingPathComponent("state.json")
        try data.write(to: url, options: [.atomic])
        try FileManager.default.setAttributes([.posixPermissions: 0o600], ofItemAtPath: url.path)
    }

    public func stagePackage(_ data: Data) throws {
        try validate()
        guard !data.isEmpty, data.count <= 512 * 1024 * 1024 else { throw AgentError.invalidCatalog }
        let url = root.appendingPathComponent("verified-update.pkg")
        try removeRecoverableStaging(at: url)
        let descriptor = open(url.path, O_WRONLY | O_CREAT | O_EXCL | O_NOFOLLOW, 0o600)
        guard descriptor >= 0 else { throw AgentError.invalidCatalog }
        let handle = FileHandle(fileDescriptor: descriptor, closeOnDealloc: true)
        do {
            try handle.write(contentsOf: data)
            try handle.synchronize()
            try handle.close()
        } catch {
            try? FileManager.default.removeItem(at: url)
            throw error
        }
    }

    public func acquireLock() throws -> FileHandle {
        try validate()
        let url = root.appendingPathComponent("update.lock")
        let descriptor = open(url.path, O_RDWR | O_CREAT | O_NOFOLLOW, 0o600)
        guard descriptor >= 0 else { throw AgentError.invalidCatalog }
        var status = stat()
        guard fstat(descriptor, &status) == 0,
              status.st_mode & S_IFMT == S_IFREG,
              status.st_uid == expectedOwner,
              status.st_mode & 0o077 == 0,
              flock(descriptor, LOCK_EX | LOCK_NB) == 0 else {
            close(descriptor)
            throw AgentError.invalidCatalog
        }
        return FileHandle(fileDescriptor: descriptor, closeOnDealloc: true)
    }

    private func removeRecoverableStaging(at url: URL) throws {
        var status = stat()
        if lstat(url.path, &status) != 0 {
            guard errno == ENOENT else { throw AgentError.invalidCatalog }
            return
        }
        guard status.st_mode & S_IFMT == S_IFREG,
              status.st_uid == expectedOwner,
              status.st_mode & 0o077 == 0,
              status.st_size >= 0,
              status.st_size <= 512 * 1024 * 1024,
              unlink(url.path) == 0 else { throw AgentError.invalidCatalog }
    }

    private func regularFile(_ url: URL, maximum: Int) throws -> Data {
        let values = try url.resourceValues(forKeys: [.isRegularFileKey, .isSymbolicLinkKey, .fileSizeKey])
        let attributes = try FileManager.default.attributesOfItem(atPath: url.path)
        guard values.isRegularFile == true, values.isSymbolicLink != true,
              let size = values.fileSize, size > 0, size <= maximum,
              (attributes[.ownerAccountID] as? NSNumber)?.uint32Value == expectedOwner,
              ((attributes[.posixPermissions] as? NSNumber)?.intValue ?? 0) & 0o077 == 0 else {
            throw AgentError.invalidCatalog
        }
        let data = try Data(contentsOf: url, options: [.mappedIfSafe])
        guard data.count == size else { throw AgentError.invalidCatalog }
        return data
    }
}

public enum VerifiedInstaller {
    public struct Command: Equatable {
        public let executable: String
        public let arguments: [String]
    }

    public static func command(package: URL, supportRoot: URL) throws -> Command {
        let expected = supportRoot.standardizedFileURL.appendingPathComponent("verified-update.pkg")
        guard package.standardizedFileURL == expected,
              package.lastPathComponent == "verified-update.pkg" else { throw AgentError.invalidCatalog }
        return Command(executable: "/usr/sbin/installer", arguments: ["-pkg", expected.path, "-target", "/"])
    }
}
