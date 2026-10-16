#!/usr/bin/env python3
"""Hermetic checks for the exact-host macOS TEST-origin CONNECT transport."""

from __future__ import annotations

import os
import shutil
import socket
import subprocess
import tempfile
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "apps/macos/Sources/AgentCore/TestOriginTransport.swift"
PACKAGE = """// swift-tools-version: 5.9
import PackageDescription
let package = Package(
    name: "TestOriginTransportChecks",
    platforms: [.macOS(.v13)],
    targets: [.executableTarget(name: "TestOriginTransportChecks")]
)
"""

MAIN = r'''import Foundation
import Network
import Security
import Darwin

private var checks = 0
private func check(_ condition: @autoclosure () -> Bool, _ message: String) {
    guard condition() else { fatalError("FAIL: \(message)") }
    checks += 1
}

private func pinnedTLS(certificatePath: String, serverName: String) throws -> NWProtocolTLS.Options {
    let bytes = try Data(contentsOf: URL(fileURLWithPath: certificatePath))
    guard let certificate = SecCertificateCreateWithData(nil, bytes as CFData) else {
        throw NSError(domain: "fixture", code: 1)
    }
    let options = NWProtocolTLS.Options()
    let queue = DispatchQueue(label: "ru.robopark.test.tls-verify")
    sec_protocol_options_set_verify_block(options.securityProtocolOptions, { _, trust, complete in
        let evaluated = sec_trust_copy_ref(trust).takeRetainedValue()
        SecTrustSetAnchorCertificates(evaluated, [certificate] as CFArray)
        SecTrustSetAnchorCertificatesOnly(evaluated, true)
        SecTrustSetPolicies(evaluated, SecPolicyCreateSSL(true, serverName as CFString))
        var trustError: CFError?
        let trusted = SecTrustEvaluateWithError(evaluated, &trustError)
        complete(trusted)
    }, queue)
    return options
}

private final class FixtureTrustDelegate: NSObject, URLSessionDelegate, @unchecked Sendable {
    private let certificate: SecCertificate
    init(path: String) throws {
        let bytes = try Data(contentsOf: URL(fileURLWithPath: path))
        guard let certificate = SecCertificateCreateWithData(nil, bytes as CFData) else {
            throw NSError(domain: "fixture", code: 2)
        }
        self.certificate = certificate
    }
    func urlSession(_ session: URLSession, didReceive challenge: URLAuthenticationChallenge,
                    completionHandler: @escaping (URLSession.AuthChallengeDisposition, URLCredential?) -> Void) {
        guard challenge.protectionSpace.authenticationMethod == NSURLAuthenticationMethodServerTrust,
              challenge.protectionSpace.host == TestOriginTransport.testHost,
              let trust = challenge.protectionSpace.serverTrust else {
            completionHandler(.performDefaultHandling, nil); return
        }
        SecTrustSetAnchorCertificates(trust, [certificate] as CFArray)
        SecTrustSetAnchorCertificatesOnly(trust, true)
        SecTrustSetPolicies(trust, SecPolicyCreateSSL(true, TestOriginTransport.testHost as CFString))
        guard SecTrustEvaluateWithError(trust, nil) else {
            completionHandler(.cancelAuthenticationChallenge, nil); return
        }
        completionHandler(.useCredential, URLCredential(trust: trust))
    }
}

private func rawProxyRequest(port: UInt16, request: Data, readLimit: Int = 8192) throws -> Data {
    let descriptor = socket(AF_INET, SOCK_STREAM, 0)
    guard descriptor >= 0 else { throw POSIXError(.ECONNREFUSED) }
    defer { Darwin.close(descriptor) }
    var timeout = timeval(tv_sec: 5, tv_usec: 0)
    setsockopt(descriptor, SOL_SOCKET, SO_RCVTIMEO, &timeout, socklen_t(MemoryLayout.size(ofValue: timeout)))
    var address = sockaddr_in()
    address.sin_len = UInt8(MemoryLayout<sockaddr_in>.size)
    address.sin_family = sa_family_t(AF_INET)
    address.sin_port = port.bigEndian
    inet_pton(AF_INET, "127.0.0.1", &address.sin_addr)
    let result = withUnsafePointer(to: &address) {
        $0.withMemoryRebound(to: sockaddr.self, capacity: 1) {
            Darwin.connect(descriptor, $0, socklen_t(MemoryLayout<sockaddr_in>.size))
        }
    }
    guard result == 0 else { throw POSIXError(POSIXErrorCode(rawValue: errno) ?? .ECONNREFUSED) }
    try request.withUnsafeBytes { bytes in
        var offset = 0
        while offset < bytes.count {
            let count = Darwin.write(descriptor, bytes.baseAddress!.advanced(by: offset), bytes.count - offset)
            guard count > 0 else { throw POSIXError(.EIO) }
            offset += count
        }
    }
    var output = Data()
    var bytes = [UInt8](repeating: 0, count: min(4096, readLimit))
    while output.count < readLimit {
        let count = Darwin.read(descriptor, &bytes, min(bytes.count, readLimit - output.count))
        if count <= 0 { break }
        output.append(bytes, count: count)
        if output.range(of: Data("\r\n\r\n".utf8)) != nil { break }
    }
    return output
}

private final class StallServer: @unchecked Sendable {
    let port: UInt16
    private let descriptor: Int32
    private let queue = DispatchQueue(label: "ru.robopark.test.stall-server")
    private let lock = NSLock()
    private var accepted: [Int32] = []
    private var source: DispatchSourceRead?

    init() throws {
        let descriptor = socket(AF_INET, SOCK_STREAM, 0)
        guard descriptor >= 0 else { throw POSIXError(.EIO) }
        var address = sockaddr_in()
        address.sin_len = UInt8(MemoryLayout<sockaddr_in>.size)
        address.sin_family = sa_family_t(AF_INET)
        address.sin_port = 0
        inet_pton(AF_INET, "127.0.0.1", &address.sin_addr)
        let bound = withUnsafePointer(to: &address) {
            $0.withMemoryRebound(to: sockaddr.self, capacity: 1) {
                Darwin.bind(descriptor, $0, socklen_t(MemoryLayout<sockaddr_in>.size))
            }
        }
        guard bound == 0, Darwin.listen(descriptor, 8) == 0 else {
            Darwin.close(descriptor); throw POSIXError(.EADDRINUSE)
        }
        var actual = sockaddr_in()
        var length = socklen_t(MemoryLayout<sockaddr_in>.size)
        guard withUnsafeMutablePointer(to: &actual, { pointer in
            pointer.withMemoryRebound(to: sockaddr.self, capacity: 1) {
                getsockname(descriptor, $0, &length)
            }
        }) == 0 else {
            Darwin.close(descriptor); throw POSIXError(.EIO)
        }
        self.descriptor = descriptor
        self.port = UInt16(bigEndian: actual.sin_port)
        let source = DispatchSource.makeReadSource(fileDescriptor: descriptor, queue: queue)
        source.setEventHandler { [weak self] in
            guard let self else { return }
            let connection = Darwin.accept(self.descriptor, nil, nil)
            if connection >= 0 { self.lock.withLock { self.accepted.append(connection) } }
        }
        source.setCancelHandler { [descriptor] in Darwin.close(descriptor) }
        self.source = source
        source.resume()
    }

    func stop() {
        source?.cancel(); source = nil
        let sockets = lock.withLock { let value = accepted; accepted.removeAll(); return value }
        for socket in sockets { Darwin.shutdown(socket, SHUT_RDWR); Darwin.close(socket) }
    }

    deinit { stop() }
}

@main struct Run {
    static func main() async throws {
        guard CommandLine.arguments.count == 5,
              let exactPort = UInt16(CommandLine.arguments[1]),
              let wrongPort = UInt16(CommandLine.arguments[2]) else {
            fatalError("expected exact-port wrong-port exact-cert wrong-cert")
        }
        let exactCertificate = CommandLine.arguments[3]
        let wrongCertificate = CommandLine.arguments[4]

        check(TestOriginCandidate.publicAddress("127.0.0.1") == nil, "loopback DNS target is rejected")
        check(TestOriginCandidate.publicAddress("10.2.3.4") == nil, "private IPv4 target is rejected")
        check(TestOriginCandidate.publicAddress("::1") == nil, "IPv6 loopback target is rejected")
        check(TestOriginCandidate.publicAddress("2001:db8::1") == nil, "documentation IPv6 target is rejected")
        check(TestOriginCandidate.publicAddress("8.8.8.8") != nil, "public IPv4 target is accepted")
        check(TestOriginCandidate.publicAddress("2606:4700:4700::1111") != nil, "public IPv6 target is accepted")
        check(TestOriginTransport.proxyIsEnabled(["HTTPEnable": 1]),
              "an enabled corporate HTTP proxy is detected")
        check(TestOriginTransport.proxyIsEnabled(["ProxyAutoConfigEnable": true]),
              "an enabled corporate PAC is detected")
        check(!TestOriginTransport.proxyIsEnabled(["HTTPEnable": 0]),
              "disabled system proxy settings do not suppress the TEST transport")

        let first = TestOriginCandidate.testing("192.0.2.1")
        let second = TestOriginCandidate.testing("192.0.2.2")
        let counter = LockedCounter()
        let selector = TestOriginAddressSelector(cacheTTL: 20, resolver: {
            counter.incrementResolver(); return [first, second]
        }, preflight: { candidate in
            counter.incrementPreflight()
            if candidate == first { try? await Task.sleep(nanoseconds: 80_000_000); return false }
            try? await Task.sleep(nanoseconds: 10_000_000); return true
        })
        async let selectedA = selector.select()
        async let selectedB = selector.select()
        let (a, b) = try await (selectedA, selectedB)
        check(a == second && b == second, "first verified TLS candidate wins")
        check(counter.resolverCalls == 1, "concurrent selection shares one DNS flight")
        _ = try await selector.select()
        check(counter.resolverCalls == 1, "healthy candidate cache avoids DNS for twenty seconds")
        await selector.invalidate(second)
        _ = try await selector.select()
        check(counter.resolverCalls == 2, "invalidated candidate is selected through a new flight")

        let skippedFallback = LockedCounter()
        let systemHealthy = TestOriginAddressSelector(cacheTTL: 20, resolver: { [first] },
            fallbackResolver: {
                skippedFallback.incrementResolver(); return [second]
            }, preflight: { $0 == first })
        let healthySystemResult = try await systemHealthy.select()
        check(healthySystemResult == first,
              "a healthy system DNS candidate remains preferred")
        check(skippedFallback.resolverCalls == 0,
              "DoH fallback is not queried while system DNS has a verified candidate")

        let usedFallback = LockedCounter()
        let systemFailed = TestOriginAddressSelector(cacheTTL: 20, resolver: { [first] },
            fallbackResolver: {
                usedFallback.incrementResolver(); return [second]
            }, preflight: { $0 == second })
        let fallbackResult = try await systemFailed.select()
        check(fallbackResult == second,
              "DoH candidate is selected after all system DNS candidates fail TLS")
        check(usedFallback.resolverCalls == 1,
              "DoH fallback is queried once after system candidates fail")

        let boundedFallback = LockedCounter()
        let coldStarted = Date()
        let coldSelector = TestOriginAddressSelector(cacheTTL: 20, resolver: {
            try await Task.sleep(nanoseconds: 5_000_000_000)
            return [first]
        }, fallbackResolver: {
            boundedFallback.incrementResolver(); return [second]
        }, preflight: { $0 == second }, resolverTimeout: 0.08,
           fallbackResolverTimeout: 0.08, maximumCandidates: 4)
        let coldResult = try await coldSelector.select()
        check(coldResult == second && boundedFallback.resolverCalls == 1,
              "a stalled system resolver advances to the bounded fallback")
        check(Date().timeIntervalSince(coldStarted) < 0.5,
              "cold fallback selection completes before the CONNECT relay deadline")

        let fifth = TestOriginCandidate.testing("192.0.2.5")
        let cappedSystem = [first, second, TestOriginCandidate.testing("192.0.2.3"),
                            TestOriginCandidate.testing("192.0.2.4"), fifth]
        let fifthViaDoH = TestOriginAddressSelector(cacheTTL: 20,
            resolver: { cappedSystem }, fallbackResolver: { [fifth] },
            preflight: { $0 == fifth }, maximumCandidates: 4)
        let fifthResult = try await fifthViaDoH.select()
        check(fifthResult == fifth,
              "an untested fifth system address remains eligible when DoH returns it")

        let validDoH = Data(#"{"Status":0,"Question":[{"name":"roboparktest.ru.tuna.am.","type":1}],"Answer":[{"name":"roboparktest.ru.tuna.am.","type":1,"TTL":20,"data":"8.8.8.8"}]}"#.utf8)
        check(TestOriginDoHResolver.parse(validDoH, expectedHost: TestOriginTransport.testHost)
                == [TestOriginCandidate.testing("8.8.8.8")],
              "DoH parser accepts an exact question and public A answer")
        let privateDoH = Data(#"{"Status":0,"Question":[{"name":"roboparktest.ru.tuna.am","type":1}],"Answer":[{"name":"roboparktest.ru.tuna.am","type":1,"data":"10.0.0.7"}]}"#.utf8)
        check(TestOriginDoHResolver.parse(privateDoH, expectedHost: TestOriginTransport.testHost).isEmpty,
              "DoH parser rejects private fallback addresses")
        let wrongQuestionDoH = Data(#"{"Status":0,"Question":[{"name":"example.com","type":1}],"Answer":[{"name":"example.com","type":1,"data":"8.8.8.8"}]}"#.utf8)
        check(TestOriginDoHResolver.parse(wrongQuestionDoH,
                                           expectedHost: TestOriginTransport.testHost).isEmpty,
              "DoH parser rejects a response for a different question")
        check(TestOriginDoHResolver.parse(Data("not-json".utf8),
                                           expectedHost: TestOriginTransport.testHost).isEmpty,
              "DoH parser rejects malformed responses")

        let loopback = TestOriginCandidate.testing("127.0.0.1")
        let exactTLS = try pinnedTLS(certificatePath: exactCertificate, serverName: TestOriginTransport.testHost)
        let wrongTLS = try pinnedTLS(certificatePath: wrongCertificate, serverName: TestOriginTransport.testHost)
        let exactTrusted = await TestOriginTLSPreflight.check(
            loopback, port: exactPort, serverName: TestOriginTransport.testHost,
            timeout: 2, options: exactTLS)
        check(exactTrusted, "preflight uses exact SNI and accepts an explicitly trusted matching SAN")
        let wrongSAN = await TestOriginTLSPreflight.check(
            loopback, port: wrongPort, serverName: TestOriginTransport.testHost,
            timeout: 2, options: wrongTLS)
        check(!wrongSAN, "preflight rejects an explicitly trusted certificate with the wrong SAN")
        let untrusted = await TestOriginTLSPreflight.check(
            loopback, port: exactPort, serverName: TestOriginTransport.testHost,
            timeout: 2, options: nil)
        check(!untrusted, "production default trust rejects an untrusted certificate")

        let stall = try StallServer()
        defer { stall.stop() }
        let cancellationStarted = Date()
        let cancelledPreflight = Task {
            await TestOriginTLSPreflight.check(
                loopback, port: stall.port, serverName: TestOriginTransport.testHost,
                timeout: 3, options: try? pinnedTLS(
                    certificatePath: exactCertificate, serverName: TestOriginTransport.testHost))
        }
        try await Task.sleep(nanoseconds: 80_000_000)
        cancelledPreflight.cancel()
        let cancelledResult = await cancelledPreflight.value
        check(!cancelledResult, "cancelled TLS preflight returns failure")
        check(Date().timeIntervalSince(cancellationStarted) < 0.75,
              "cancelled TLS preflight closes its NWConnection without waiting for timeout")

        let racingFastTLS = try pinnedTLS(
            certificatePath: exactCertificate, serverName: TestOriginTransport.testHost)
        let racingSlowTLS = try pinnedTLS(
            certificatePath: exactCertificate, serverName: TestOriginTransport.testHost)
        let racingSelector = TestOriginAddressSelector(cacheTTL: 20, resolver: { [first, second] },
            preflight: { candidate in
                await TestOriginTLSPreflight.check(loopback,
                    port: candidate == first ? exactPort : stall.port,
                    serverName: TestOriginTransport.testHost, timeout: 3,
                    options: candidate == first ? racingFastTLS : racingSlowTLS)
            })
        let racingStarted = Date()
        let racingResult = try await racingSelector.select()
        check(racingResult == first, "selector returns the verified TLS address")
        check(Date().timeIntervalSince(racingStarted) < 0.75,
              "selector cancels a stalled sibling after the first verified TLS address")

        let transport = TestOriginTransport(
            resolver: { [loopback] }, preflight: { _ in true }, targetPort: exactPort)
        let preservedConfiguration = URLSessionConfiguration.ephemeral
        preservedConfiguration.connectionProxyDictionary = ["HTTPEnable": 1, "HTTPProxy": "corp.invalid"]
        let preservedProxy = preservedConfiguration.connectionProxyDictionary
        try transport.configure(preservedConfiguration)
        check(NSDictionary(dictionary: preservedConfiguration.connectionProxyDictionary ?? [:])
                .isEqual(to: preservedProxy ?? [:]),
              "an existing session proxy configuration is preserved")
        let configuration = URLSessionConfiguration.ephemeral
        try transport.configure(configuration)
        let proxyPort = try transport.testingPort()
        let unauthorized = try rawProxyRequest(port: proxyPort, request: Data(
            "CONNECT \(TestOriginTransport.testHost):443 HTTP/1.1\r\nHost: \(TestOriginTransport.testHost):443\r\n\r\n".utf8))
        check(String(decoding: unauthorized, as: UTF8.self).hasPrefix("HTTP/1.1 407"),
              "CONNECT without the nonce is rejected")
        let authorization = try transport.testingAuthorizationHeader()
        let wrongHost = try rawProxyRequest(port: proxyPort, request: Data(
            "CONNECT example.com:443 HTTP/1.1\r\nHost: example.com:443\r\nProxy-Authorization: \(authorization)\r\n\r\n".utf8))
        check(String(decoding: wrongHost, as: UTF8.self).hasPrefix("HTTP/1.1 403"),
              "authorized CONNECT cannot target another host")
        let oversized = Data(("CONNECT \(TestOriginTransport.testHost):443 HTTP/1.1\r\nX-Fill: "
            + String(repeating: "x", count: 5000)).utf8)
        let oversizedReply = try rawProxyRequest(port: proxyPort, request: oversized)
        check(String(decoding: oversizedReply, as: UTF8.self).hasPrefix("HTTP/1.1 431"),
              "CONNECT headers are bounded to four KiB")

        let delegate = try FixtureTrustDelegate(path: exactCertificate)
        let session = URLSession(configuration: configuration, delegate: delegate, delegateQueue: nil)
        defer { session.invalidateAndCancel() }
        var request = URLRequest(url: URL(string: "https://\(TestOriginTransport.testHost)/")!)
        request.timeoutInterval = 8
        let (body, response) = try await session.data(for: request)
        check((response as? HTTPURLResponse)?.statusCode == 200 && !body.isEmpty,
              "URLSession authenticates to the proxy and relays opaque TLS bytes")

        let eofSocket = socket(AF_INET, SOCK_STREAM, 0)
        guard eofSocket >= 0 else { throw POSIXError(.ECONNREFUSED) }
        var eofAddress = sockaddr_in()
        eofAddress.sin_len = UInt8(MemoryLayout<sockaddr_in>.size)
        eofAddress.sin_family = sa_family_t(AF_INET); eofAddress.sin_port = proxyPort.bigEndian
        inet_pton(AF_INET, "127.0.0.1", &eofAddress.sin_addr)
        let eofConnected = withUnsafePointer(to: &eofAddress) {
            $0.withMemoryRebound(to: sockaddr.self, capacity: 1) {
                Darwin.connect(eofSocket, $0, socklen_t(MemoryLayout<sockaddr_in>.size))
            }
        }
        check(eofConnected == 0, "authorized EOF fixture connects")
        let eofRequest = Data("CONNECT \(TestOriginTransport.testHost):443 HTTP/1.1\r\nHost: \(TestOriginTransport.testHost):443\r\nProxy-Authorization: \(authorization)\r\n\r\n".utf8)
        _ = eofRequest.withUnsafeBytes { Darwin.write(eofSocket, $0.baseAddress!, $0.count) }
        Darwin.shutdown(eofSocket, SHUT_RDWR); Darwin.close(eofSocket)
        for _ in 0..<40 {
            if transport.testingActiveConnections() == 0 { break }
            try await Task.sleep(nanoseconds: 25_000_000)
        }
        check(transport.testingActiveConnections() == 0, "client EOF closes both relay sides")
        transport.stop()
        print("PASS: \(checks) TEST-origin DNS, TLS preflight, authenticated CONNECT and byte-relay checks")
    }
}

final class LockedCounter: @unchecked Sendable {
    private let lock = NSLock()
    private var resolver = 0
    private var preflight = 0
    var resolverCalls: Int { lock.withLock { resolver } }
    var preflightCalls: Int { lock.withLock { preflight } }
    func incrementResolver() { lock.withLock { resolver += 1 } }
    func incrementPreflight() { lock.withLock { preflight += 1 } }
}
'''


def free_ports(count: int) -> list[int]:
    listeners: list[socket.socket] = []
    try:
        for _ in range(count):
            listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            listener.bind(("127.0.0.1", 0))
            listeners.append(listener)
        return [int(listener.getsockname()[1]) for listener in listeners]
    finally:
        for listener in listeners:
            listener.close()


def make_certificate(folder: Path, name: str, san: str) -> tuple[Path, Path]:
    key = folder / f"{name}.key.pem"
    certificate = folder / f"{name}.cert.pem"
    der = folder / f"{name}.cert.der"
    subprocess.run(
        [
            "openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "1",
            "-sha256", "-subj", f"/CN={san}",
            "-addext", f"subjectAltName=DNS:{san}",
            "-addext", "basicConstraints=critical,CA:FALSE",
            "-addext", "keyUsage=critical,digitalSignature,keyEncipherment",
            "-addext", "extendedKeyUsage=serverAuth",
            "-keyout", str(key), "-out", str(certificate),
        ],
        check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    subprocess.run(
        ["openssl", "x509", "-in", str(certificate), "-outform", "DER", "-out", str(der)],
        check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    return key, certificate


def main() -> int:
    if not SOURCE.is_file():
        print(f"FAIL: missing production source {SOURCE}")
        return 1
    with tempfile.TemporaryDirectory(prefix="robopark-test-origin-") as raw:
        folder = Path(raw)
        source_dir = folder / "Sources/TestOriginTransportChecks"
        source_dir.mkdir(parents=True)
        shutil.copy2(SOURCE, source_dir / SOURCE.name)
        (source_dir / "main.swift").write_text(MAIN)
        (folder / "Package.swift").write_text(PACKAGE)
        exact_key, exact_cert = make_certificate(folder, "exact", "roboparktest.ru.tuna.am")
        wrong_key, wrong_cert = make_certificate(folder, "wrong", "wrong.test.invalid")
        exact_der = folder / "exact.cert.der"
        wrong_der = folder / "wrong.cert.der"
        exact_port, wrong_port = free_ports(2)
        servers = [
            subprocess.Popen(
                ["openssl", "s_server", "-quiet", "-www", "-accept", str(exact_port),
                 "-cert", str(exact_cert), "-key", str(exact_key)],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            ),
            subprocess.Popen(
                ["openssl", "s_server", "-quiet", "-www", "-accept", str(wrong_port),
                 "-cert", str(wrong_cert), "-key", str(wrong_key)],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            ),
        ]
        try:
            time.sleep(0.2)
            if any(server.poll() is not None for server in servers):
                raise RuntimeError("fixture TLS server failed to start")
            cache = Path("/private/tmp/robopark-test-origin-transport-checks")
            cache.mkdir(parents=True, exist_ok=True)
            command = [
                "swift", "run", "--package-path", str(folder), "--scratch-path", str(cache),
                "--cache-path", str(cache / "cache"), "--config-path", str(cache / "config"),
                "--security-path", str(cache / "security"), "--disable-sandbox",
                "TestOriginTransportChecks", str(exact_port), str(wrong_port),
                str(exact_der), str(wrong_der),
            ]
            return subprocess.run(
                command, check=False,
                env={
                    **os.environ,
                    "CLANG_MODULE_CACHE_PATH": str(cache / "clang"),
                    "SWIFTPM_MODULECACHE_OVERRIDE": str(cache / "modules"),
                },
            ).returncode
        finally:
            for server in servers:
                server.terminate()
            for server in servers:
                try:
                    server.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    server.kill()


if __name__ == "__main__":
    raise SystemExit(main())
