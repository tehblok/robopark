import Foundation
import Compression

public enum PayloadCompression {
    public static let limit = 2 * 1024 * 1024
    public static func compress(_ data: Data) throws -> Data {
        guard !data.isEmpty, data.count <= limit else { throw AgentError.invalidJob }
        var buffer = Data(count: data.count + 4096)
        let capacity = buffer.count
        let count = buffer.withUnsafeMutableBytes { output in
            data.withUnsafeBytes { input in
                compression_encode_buffer(output.bindMemory(to: UInt8.self).baseAddress!, capacity,
                    input.bindMemory(to: UInt8.self).baseAddress!, data.count, nil, COMPRESSION_ZLIB)
            }
        }
        guard count > 0 else { throw AgentError.invalidJob }
        return Data(buffer.prefix(count))
    }
    public static func decompress(_ data: Data, expectedBytes: Int) throws -> Data {
        guard !data.isEmpty, data.count <= limit, expectedBytes > 0, expectedBytes <= limit else { throw AgentError.invalidJob }
        var output = Data(count: expectedBytes + 1)
        let count = output.withUnsafeMutableBytes { dst in
            data.withUnsafeBytes { src in
                compression_decode_buffer(dst.bindMemory(to: UInt8.self).baseAddress!, expectedBytes + 1,
                    src.bindMemory(to: UInt8.self).baseAddress!, data.count, nil, COMPRESSION_ZLIB)
            }
        }
        guard count == expectedBytes else { throw AgentError.invalidJob }
        return Data(output.prefix(count))
    }
}
