import Foundation
import CryptoKit
import ImageIO
import UniformTypeIdentifiers

private let maxFrameBytes = 9 * 1024 * 1024
private let maxPayloadBytes = 6 * 1024 * 1024
private let maxOutputBytes = 8 * 1024 * 1024

private enum WorkerFailure: String, Error {
    case invalidFrame = "invalid_frame"
    case invalidPayload = "invalid_payload"
    case payloadHash = "payload_hash"
    case unsupportedOperation = "unsupported_operation"
    case imageDecode = "image_decode"
    case outputTooLarge = "output_too_large"
    case outputNotSmaller = "output_not_smaller"
}

private func sha256(_ data: Data) -> String {
    SHA256.hash(data: data).map { String(format: "%02x", $0) }.joined()
}

private func csvField(_ value: String) -> String {
    guard value.contains(",") || value.contains("\"") || value.contains("\r") || value.contains("\n") else { return value }
    return "\"" + value.replacingOccurrences(of: "\"", with: "\"\"") + "\""
}

private func stripJPEGMetadata(_ encoded: Data) throws -> Data {
    guard encoded.count >= 4, encoded[0] == 0xFF, encoded[1] == 0xD8 else { throw WorkerFailure.imageDecode }
    var output = Data(encoded.prefix(2)); var offset = 2
    while offset + 4 <= encoded.count {
        guard encoded[offset] == 0xFF else { throw WorkerFailure.imageDecode }
        let marker = encoded[offset + 1]
        if marker == 0xDA { output.append(encoded[offset...]); return output }
        if marker == 0xD9 { output.append(contentsOf: [0xFF, 0xD9]); return output }
        let length = Int(encoded[offset + 2]) << 8 | Int(encoded[offset + 3])
        guard length >= 2, offset + 2 + length <= encoded.count else { throw WorkerFailure.imageDecode }
        if !(0xE1...0xEF).contains(marker), marker != 0xFE {
            output.append(encoded[offset..<(offset + 2 + length)])
        }
        offset += 2 + length
    }
    throw WorkerFailure.imageDecode
}

private func renderCSV(_ object: [String: Any]) throws -> (Data, String, Int?, Int?) {
    guard let headers = object["headers"] as? [String], (1...20).contains(headers.count),
          let rows = object["rows"] as? [[String]], rows.count <= 10_000,
          rows.allSatisfy({ $0.count == headers.count }) else { throw WorkerFailure.invalidPayload }
    var data = Data([0xEF, 0xBB, 0xBF])
    for row in [headers] + rows {
        data.append(Data((row.map(csvField).joined(separator: ",") + "\r\n").utf8))
        guard data.count <= maxOutputBytes else { throw WorkerFailure.outputTooLarge }
    }
    return (data, "text/csv; charset=utf-8", nil, nil)
}

private func renderImage(_ object: [String: Any]) throws -> (Data, String, Int?, Int?) {
    guard let encoded = object["data_base64"] as? String,
          let input = Data(base64Encoded: encoded, options: []), input.count <= 4 * 1024 * 1024,
          let mime = object["mime_type"] as? String, ["image/jpeg", "image/png", "image/webp"].contains(mime),
          let maxEdge = object["max_edge"] as? Int, (64...4096).contains(maxEdge),
          let quality = object["quality"] as? Int, (40...95).contains(quality),
          let source = CGImageSourceCreateWithData(input as CFData, nil),
          CGImageSourceGetCount(source) == 1,
          let properties = CGImageSourceCopyPropertiesAtIndex(source, 0, nil) as? [CFString: Any],
          let width = properties[kCGImagePropertyPixelWidth] as? Int,
          let height = properties[kCGImagePropertyPixelHeight] as? Int,
          width > 0, height > 0, width <= 40_000_000 / height else { throw WorkerFailure.invalidPayload }
    let options: [CFString: Any] = [
        kCGImageSourceCreateThumbnailFromImageAlways: true,
        kCGImageSourceCreateThumbnailWithTransform: true,
        kCGImageSourceThumbnailMaxPixelSize: min(maxEdge, max(width, height)),
        kCGImageSourceShouldCacheImmediately: true,
    ]
    guard let image = CGImageSourceCreateThumbnailAtIndex(source, 0, options as CFDictionary) else {
        throw WorkerFailure.imageDecode
    }
    let output = NSMutableData()
    guard let destination = CGImageDestinationCreateWithData(output, UTType.jpeg.identifier as CFString, 1, nil) else {
        throw WorkerFailure.imageDecode
    }
    CGImageDestinationAddImage(destination, image, [kCGImageDestinationLossyCompressionQuality: Double(quality) / 100] as CFDictionary)
    guard CGImageDestinationFinalize(destination) else { throw WorkerFailure.imageDecode }
    let data = try stripJPEGMetadata(output as Data)
    guard data.count <= 4 * 1024 * 1024 else { throw WorkerFailure.outputTooLarge }
    guard data.count < input.count else { throw WorkerFailure.outputNotSmaller }
    return (data, "image/jpeg", image.width, image.height)
}

private func failure(_ frame: [String: Any], _ code: WorkerFailure) -> [String: Any] {
    ["type": "compute_result", "generation": frame["generation"] as? String ?? "",
     "job_id": frame["job_id"] as? String ?? "", "attempt_id": frame["attempt_id"] as? String ?? "",
     "payload_sha256": frame["payload_sha256"] as? String ?? "", "ok": false, "error": code.rawValue]
}

private func execute(_ frame: [String: Any]) throws -> [String: Any] {
    guard frame["type"] as? String == "compute_job",
          let generation = frame["generation"] as? String, !generation.isEmpty, generation.utf8.count <= 256,
          let jobID = frame["job_id"] as? String, UUID(uuidString: jobID) != nil,
          let attemptID = frame["attempt_id"] as? String, UUID(uuidString: attemptID) != nil,
          let operation = frame["operation"] as? String,
          let payload64 = frame["payload_base64"] as? String,
          let claimedHash = frame["payload_sha256"] as? String,
          claimedHash.range(of: "^[0-9a-f]{64}$", options: .regularExpression) != nil,
          let timeout = frame["timeout_ms"] as? Int, (1...30_000).contains(timeout) else { throw WorkerFailure.invalidFrame }
    guard let payload = Data(base64Encoded: payload64, options: []), payload.count <= maxPayloadBytes else { throw WorkerFailure.invalidPayload }
    guard sha256(payload) == claimedHash else { throw WorkerFailure.payloadHash }
    guard let object = try JSONSerialization.jsonObject(with: payload) as? [String: Any] else { throw WorkerFailure.invalidPayload }
    let transformed: (Data, String, Int?, Int?)
    switch operation {
    case "image_resize_v1": transformed = try renderImage(object)
    case "csv_render_v1": transformed = try renderCSV(object)
    default: throw WorkerFailure.unsupportedOperation
    }
    var result: [String: Any] = ["data_base64": transformed.0.base64EncodedString(),
                                 "sha256": sha256(transformed.0), "mime_type": transformed.1]
    if let width = transformed.2, let height = transformed.3 { result["width"] = width; result["height"] = height }
    return ["type": "compute_result", "generation": generation, "job_id": jobID, "attempt_id": attemptID,
            "payload_sha256": claimedHash, "ok": true, "result": result]
}

var input = Data()
while input.count <= maxFrameBytes {
    guard let chunk = try FileHandle.standardInput.read(upToCount: min(64 * 1024, maxFrameBytes + 1 - input.count)),
          !chunk.isEmpty else { break }
    input.append(chunk)
}
var frame: [String: Any] = [:]
var response: [String: Any]
if input.count > maxFrameBytes {
    response = failure(frame, .invalidFrame)
} else {
    do {
        guard let value = try JSONSerialization.jsonObject(with: input) as? [String: Any] else { throw WorkerFailure.invalidFrame }
        frame = value
        response = try execute(value)
    } catch let error as WorkerFailure {
        response = failure(frame, error)
    } catch {
        response = failure(frame, .invalidPayload)
    }
}
let output = try JSONSerialization.data(withJSONObject: response, options: [.sortedKeys])
FileHandle.standardOutput.write(output)
FileHandle.standardOutput.write(Data([0x0A]))
