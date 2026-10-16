import Darwin
import Foundation

public enum PrivateStoredState: Equatable, Sendable {
    case absent
    case value(Data)
    case deleted
}

public enum PrivateSessionStoreError: Error, Equatable, LocalizedError {
    case invalidAccount
    case valueTooLarge
    case unsafeDirectory
    case unsafeEntry
    case corruptRecord
    case recordTooLarge
    case system(operation: String, code: Int32)

    public var errorDescription: String? {
        switch self {
        case .invalidAccount: return "The private store account is not allowed."
        case .valueTooLarge: return "The private store value is too large."
        case .unsafeDirectory: return "The private store directory is not safe."
        case .unsafeEntry: return "A private store entry is not safe."
        case .corruptRecord: return "A private store record is corrupt."
        case .recordTooLarge: return "A private store record is too large."
        case let .system(operation, code): return "Private store \(operation) failed (\(code))."
        }
    }
}

public final class PrivateSessionStore: @unchecked Sendable {
    public static let maximumValueBytes = 1_048_576

    private static let processLock = NSLock()
    private static let magic = Array("RPSTORE1".utf8)
    private static let headerBytes = magic.count + 1 + MemoryLayout<UInt64>.size
    private static let fixedAccounts: Set<String> = [
        "registration",
        "workspace-profile-v1",
        "workspace-browser-session-v1",
        "workspace-storage-key-v1",
        "corporate-write-receipt-index-v1",
        "internal-receipt-migration-sealed-v1",
    ]
    private static let digestPrefixes = [
        "enrollment-key-v1-",
        "account-claim-key-v1-",
        "corporate-write-receipt-v1-",
    ]

    private let directoryDescriptor: Int32
    private let lockDescriptor: Int32
    private let owner: uid_t

    public init(directory: URL) throws {
        owner = geteuid()
        let path = directory.standardizedFileURL.path
        var before = stat()
        if lstat(path, &before) != 0 {
            guard errno == ENOENT else { throw Self.system("inspect-directory") }
            if mkdir(path, 0o700) != 0, errno != EEXIST { throw Self.system("create-directory") }
            guard lstat(path, &before) == 0 else { throw Self.system("inspect-directory") }
        }
        guard before.st_mode & S_IFMT == S_IFDIR, before.st_uid == owner else {
            throw PrivateSessionStoreError.unsafeDirectory
        }

        let directoryDescriptor = open(path, O_RDONLY | O_DIRECTORY | O_CLOEXEC | O_NOFOLLOW)
        guard directoryDescriptor >= 0 else {
            if errno == ELOOP { throw PrivateSessionStoreError.unsafeDirectory }
            throw Self.system("open-directory")
        }
        var directoryStatus = stat()
        guard fstat(directoryDescriptor, &directoryStatus) == 0 else {
            let error = Self.system("inspect-directory")
            Darwin.close(directoryDescriptor)
            throw error
        }
        guard directoryStatus.st_mode & S_IFMT == S_IFDIR,
              directoryStatus.st_uid == owner,
              directoryStatus.st_dev == before.st_dev,
              directoryStatus.st_ino == before.st_ino else {
            Darwin.close(directoryDescriptor)
            throw PrivateSessionStoreError.unsafeDirectory
        }
        guard fchmod(directoryDescriptor, 0o700) == 0 else {
            let error = Self.system("secure-directory")
            Darwin.close(directoryDescriptor)
            throw error
        }

        let lockDescriptor: Int32
        do {
            lockDescriptor = try Self.openLock(in: directoryDescriptor)
        } catch {
            Darwin.close(directoryDescriptor)
            throw error
        }
        var lockStatus = stat()
        guard fstat(lockDescriptor, &lockStatus) == 0 else {
            let error = Self.system("inspect-lock")
            Darwin.close(lockDescriptor)
            Darwin.close(directoryDescriptor)
            throw error
        }
        guard lockStatus.st_mode & S_IFMT == S_IFREG,
              lockStatus.st_uid == owner,
              lockStatus.st_nlink == 1 else {
            Darwin.close(lockDescriptor)
            Darwin.close(directoryDescriptor)
            throw PrivateSessionStoreError.unsafeEntry
        }
        guard fchmod(lockDescriptor, 0o600) == 0 else {
            let error = Self.system("secure-lock")
            Darwin.close(lockDescriptor)
            Darwin.close(directoryDescriptor)
            throw error
        }
        self.directoryDescriptor = directoryDescriptor
        self.lockDescriptor = lockDescriptor
    }

    deinit {
        Darwin.close(lockDescriptor)
        Darwin.close(directoryDescriptor)
    }

    public func readState(_ account: String) throws -> PrivateStoredState {
        let name = try recordName(account)
        return try withLock(exclusive: false) { try readLocked(name) }
    }

    public func write(_ data: Data, account: String) throws {
        guard data.count <= Self.maximumValueBytes else {
            throw PrivateSessionStoreError.valueTooLarge
        }
        let name = try recordName(account)
        try withLock(exclusive: true) {
            _ = try readLocked(name)
            try writeLocked(.value(data), name: name)
        }
    }

    public func migrateIfAbsent(_ data: Data, account: String) throws -> PrivateStoredState {
        guard data.count <= Self.maximumValueBytes else {
            throw PrivateSessionStoreError.valueTooLarge
        }
        let name = try recordName(account)
        return try withLock(exclusive: true) {
            let current = try readLocked(name)
            guard current == .absent else { return current }
            try writeLocked(.value(data), name: name)
            return .value(data)
        }
    }

    public func migrateDeletionIfAbsent(_ account: String) throws -> PrivateStoredState {
        let name = try recordName(account)
        return try withLock(exclusive: true) {
            let current = try readLocked(name)
            guard current == .absent else { return current }
            try writeLocked(.deleted, name: name)
            return .deleted
        }
    }

    public func delete(_ account: String, tombstone: Bool = true) throws {
        let name = try recordName(account)
        try withLock(exclusive: true) {
            let current = try readLocked(name)
            if tombstone {
                if current != .deleted { try writeLocked(.deleted, name: name) }
            } else if current != .absent {
                guard unlinkat(directoryDescriptor, name, 0) == 0 else {
                    throw Self.system("delete-record")
                }
                guard fsync(directoryDescriptor) == 0 else {
                    throw Self.system("sync-directory")
                }
            }
        }
    }

    private func recordName(_ account: String) throws -> String {
        guard Self.accountAllowed(account) else { throw PrivateSessionStoreError.invalidAccount }
        return account + ".rps"
    }

    private static func accountAllowed(_ account: String) -> Bool {
        if fixedAccounts.contains(account) { return true }
        for prefix in digestPrefixes where account.hasPrefix(prefix) {
            let digest = account.dropFirst(prefix.count)
            return digest.utf8.count == 64 && digest.utf8.allSatisfy {
                (48...57).contains($0) || (97...102).contains($0)
            }
        }
        return false
    }

    private static func openLock(in directoryDescriptor: Int32) throws -> Int32 {
        while true {
            let existing = openat(directoryDescriptor, "store.lock", O_RDWR | O_CLOEXEC | O_NOFOLLOW)
            if existing >= 0 { return existing }
            if errno == EINTR { continue }
            if errno == ELOOP { throw PrivateSessionStoreError.unsafeEntry }
            guard errno == ENOENT else { throw system("open-lock") }

            let created = openat(directoryDescriptor, "store.lock",
                                 O_RDWR | O_CREAT | O_EXCL | O_CLOEXEC | O_NOFOLLOW, 0o600)
            if created >= 0 { return created }
            if errno == EINTR || errno == EEXIST { continue }
            if errno == ELOOP { throw PrivateSessionStoreError.unsafeEntry }
            throw system("create-lock")
        }
    }

    private func withLock<T>(exclusive: Bool, _ body: () throws -> T) throws -> T {
        Self.processLock.lock()
        defer { Self.processLock.unlock() }
        let operation = exclusive ? LOCK_EX : LOCK_SH
        while flock(lockDescriptor, operation) != 0 {
            if errno == EINTR { continue }
            throw Self.system("lock")
        }
        defer { _ = flock(lockDescriptor, LOCK_UN) }
        return try body()
    }

    private func readLocked(_ name: String) throws -> PrivateStoredState {
        let descriptor = openat(directoryDescriptor, name, O_RDONLY | O_CLOEXEC | O_NOFOLLOW)
        if descriptor < 0 {
            if errno == ENOENT { return .absent }
            if errno == ELOOP { throw PrivateSessionStoreError.unsafeEntry }
            throw Self.system("open-record")
        }
        defer { Darwin.close(descriptor) }

        var status = stat()
        guard fstat(descriptor, &status) == 0 else { throw Self.system("inspect-record") }
        guard status.st_mode & S_IFMT == S_IFREG,
              status.st_uid == owner,
              status.st_nlink == 1,
              status.st_mode & 0o777 == 0o600 else {
            throw PrivateSessionStoreError.unsafeEntry
        }
        guard status.st_size >= 0 else { throw PrivateSessionStoreError.corruptRecord }
        let maximumRecordBytes = Self.headerBytes + Self.maximumValueBytes
        guard status.st_size <= maximumRecordBytes else { throw PrivateSessionStoreError.recordTooLarge }
        let size = Int(status.st_size)
        var bytes = Data(count: size)
        var offset = 0
        try bytes.withUnsafeMutableBytes { rawBuffer in
            guard let base = rawBuffer.baseAddress else {
                if size != 0 { throw PrivateSessionStoreError.corruptRecord }
                return
            }
            while offset < size {
                let count = Darwin.read(descriptor, base.advanced(by: offset), size - offset)
                if count > 0 { offset += count; continue }
                if count < 0, errno == EINTR { continue }
                if count < 0 { throw Self.system("read-record") }
                throw PrivateSessionStoreError.corruptRecord
            }
        }
        return try Self.decode(bytes)
    }

    private func writeLocked(_ state: PrivateStoredState, name: String) throws {
        let bytes = try Self.encode(state)
        let temporary = ".tmp-\(getpid())-\(UUID().uuidString)"
        let descriptor = openat(directoryDescriptor, temporary,
                                O_WRONLY | O_CREAT | O_EXCL | O_CLOEXEC | O_NOFOLLOW, 0o600)
        guard descriptor >= 0 else { throw Self.system("create-temporary-record") }
        var renamed = false
        defer {
            Darwin.close(descriptor)
            if !renamed { _ = unlinkat(directoryDescriptor, temporary, 0) }
        }
        guard fchmod(descriptor, 0o600) == 0 else { throw Self.system("secure-record") }
        var offset = 0
        try bytes.withUnsafeBytes { rawBuffer in
            guard let base = rawBuffer.baseAddress else { return }
            while offset < bytes.count {
                let count = Darwin.write(descriptor, base.advanced(by: offset), bytes.count - offset)
                if count > 0 { offset += count; continue }
                if count < 0, errno == EINTR { continue }
                throw Self.system("write-record")
            }
        }
        guard fsync(descriptor) == 0 else { throw Self.system("sync-record") }
        guard renameat(directoryDescriptor, temporary, directoryDescriptor, name) == 0 else {
            throw Self.system("replace-record")
        }
        renamed = true
        guard fsync(directoryDescriptor) == 0 else { throw Self.system("sync-directory") }
    }

    private static func encode(_ state: PrivateStoredState) throws -> Data {
        let marker: UInt8
        let payload: Data
        switch state {
        case let .value(data):
            guard data.count <= maximumValueBytes else { throw PrivateSessionStoreError.valueTooLarge }
            marker = 1
            payload = data
        case .deleted:
            marker = 2
            payload = Data()
        case .absent:
            throw PrivateSessionStoreError.corruptRecord
        }
        var result = Data(magic)
        result.append(marker)
        var length = UInt64(payload.count).bigEndian
        withUnsafeBytes(of: &length) { result.append(contentsOf: $0) }
        result.append(payload)
        return result
    }

    private static func decode(_ data: Data) throws -> PrivateStoredState {
        guard data.count >= headerBytes,
              Array(data.prefix(magic.count)) == magic else {
            throw PrivateSessionStoreError.corruptRecord
        }
        let marker = data[magic.count]
        var length: UInt64 = 0
        for byte in data[(magic.count + 1)..<headerBytes] { length = (length << 8) | UInt64(byte) }
        guard length <= UInt64(maximumValueBytes),
              length == UInt64(data.count - headerBytes) else {
            throw PrivateSessionStoreError.corruptRecord
        }
        let payload = data.suffix(from: headerBytes)
        if marker == 1 { return .value(Data(payload)) }
        if marker == 2, payload.isEmpty { return .deleted }
        throw PrivateSessionStoreError.corruptRecord
    }

    private static func system(_ operation: String) -> PrivateSessionStoreError {
        .system(operation: operation, code: errno)
    }
}
