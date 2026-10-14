import AppKit
import CryptoKit
import Foundation
import ImageIO
import UniformTypeIdentifiers
import WebKit
import AgentCore
import ComputePeerCore

private func sha256(_ data: Data) -> String {
    SHA256.hash(data: data).map { String(format: "%02x", $0) }.joined()
}

// Explicit deterministic protocol check independent of network ICE reachability.
// Never used by the application; the normal harness preserves every candidate.
private func checkedDescription(_ description: [String: Any]) -> [String: Any] {
    guard CommandLine.arguments.contains("--loopback-candidates"),
          let sdp = description["sdp"] as? String else { return description }
    var result = description
    result["sdp"] = sdp.components(separatedBy: "\r\n").map { line in
        guard line.hasPrefix("a=candidate:") else { return line }
        var fields = line.components(separatedBy: " ")
        if fields.count >= 8 { fields[4] = "127.0.0.1" }
        return fields.joined(separator: " ")
    }.joined(separator: "\r\n")
    return result
}

private func makeNoisyPNG(width: Int = 256, height: Int = 192) throws -> Data {
    var state: UInt32 = 0x5eed1234
    var pixels = Data(count: width * height * 4)
    pixels.withUnsafeMutableBytes { raw in
        let bytes = raw.bindMemory(to: UInt8.self)
        for offset in stride(from: 0, to: bytes.count, by: 4) {
            state = 1664525 &* state &+ 1013904223
            bytes[offset] = UInt8(truncatingIfNeeded: state >> 16)
            state = 1664525 &* state &+ 1013904223
            bytes[offset + 1] = UInt8(truncatingIfNeeded: state >> 16)
            state = 1664525 &* state &+ 1013904223
            bytes[offset + 2] = UInt8(truncatingIfNeeded: state >> 16)
            bytes[offset + 3] = 255
        }
    }
    guard let provider = CGDataProvider(data: pixels as CFData),
          let image = CGImage(width: width, height: height, bitsPerComponent: 8, bitsPerPixel: 32,
                              bytesPerRow: width * 4, space: CGColorSpaceCreateDeviceRGB(),
                              bitmapInfo: CGBitmapInfo(rawValue: CGImageAlphaInfo.last.rawValue),
                              provider: provider, decode: nil, shouldInterpolate: false,
                              intent: .defaultIntent) else { throw ComputeWorkerRunError.failed }
    let output = NSMutableData()
    guard let destination = CGImageDestinationCreateWithData(output, UTType.png.identifier as CFString, 1, nil) else {
        throw ComputeWorkerRunError.failed
    }
    CGImageDestinationAddImage(destination, image, nil)
    guard CGImageDestinationFinalize(destination) else { throw ComputeWorkerRunError.failed }
    return output as Data
}

private let requesterDocument = #"""
<!doctype html><meta charset="utf-8"><script>
(()=>{'use strict';let pc,channel,expected=-1,total=0,chunks=[],complete=false;
const post=x=>webkit.messageHandlers.test.postMessage(x);
const waitIce=p=>new Promise(resolve=>{if(p.iceGatheringState==='complete')return resolve();const done=()=>{if(p.iceGatheringState==='complete'){p.removeEventListener('icegatheringstatechange',done);resolve()}};p.addEventListener('icegatheringstatechange',done);setTimeout(resolve,3000)});
const bytes=Uint8Array.from(atob('REQUEST_BYTES'),c=>c.charCodeAt(0));
async function send(){channel.send(JSON.stringify({type:'begin',bytes:bytes.byteLength}));for(let i=0;i<bytes.byteLength;i+=16384){while(channel.bufferedAmount>262144)await new Promise(r=>setTimeout(r,10));channel.send(bytes.slice(i,i+16384))}channel.send(JSON.stringify({type:'end'}))}
function receive(event){try{if(typeof event.data==='string'){const h=JSON.parse(event.data);if(h.type==='begin'&&Number.isInteger(h.bytes)&&h.bytes>0&&h.bytes<=8388608&&expected<0){expected=h.bytes;return}if(h.type==='end'&&expected===total){const all=new Uint8Array(total);let at=0;for(const x of chunks){all.set(new Uint8Array(x),at);at+=x.byteLength}complete=true;post({type:'result',json:new TextDecoder().decode(all)});return}throw Error('sequence')}if(!(event.data instanceof ArrayBuffer)||event.data.byteLength<1||event.data.byteLength>16384||expected<0||total+event.data.byteLength>expected)throw Error('chunk');chunks.push(event.data);total+=event.data.byteLength}catch(e){post({type:'failed',detail:String(e)})}}
window.applyAnswer=async answer=>{try{await pc.setRemoteDescription(answer)}catch(e){post({type:'failed',detail:String(e)})}};
window.ack=ack=>channel.send(JSON.stringify(ack));
(async()=>{try{pc=new RTCPeerConnection({iceServers:[]});pc.oniceconnectionstatechange=()=>post({type:'diagnostic',detail:'ice='+pc.iceConnectionState});pc.onconnectionstatechange=()=>post({type:'diagnostic',detail:'connection='+pc.connectionState});setInterval(async()=>{const stats=await pc.getStats();const brief=[];stats.forEach(r=>{if(r.type==='candidate-pair')brief.push({type:r.type,state:r.state,nominated:r.nominated,requests:r.requestsSent,responses:r.responsesReceived});if(r.type==='local-candidate'||r.type==='remote-candidate')brief.push({type:r.type,kind:r.candidateType,protocol:r.protocol,mdns:String(r.address||'').endsWith('.local')})});post({type:'diagnostic',detail:JSON.stringify(brief)})},5000);channel=pc.createDataChannel('robopark-compute-v1',{ordered:true});channel.binaryType='arraybuffer';channel.onmessage=receive;channel.onerror=()=>{if(!complete)post({type:'failed',detail:'channel_error'})};channel.onopen=()=>send().catch(e=>post({type:'failed',detail:String(e)}));await pc.setLocalDescription(await pc.createOffer());await waitIce(pc);post({type:'offer',offer:{type:'offer',sdp:pc.localDescription.sdp}})}catch(e){post({type:'failed',detail:String(e)})}})();
})();</script>
"""#

@MainActor
private final class PeerCheck: NSObject, WKScriptMessageHandler, WKNavigationDelegate {
    let workerURL: URL
    let peerDocument: Data
    let generation = UUID().uuidString.lowercased()
    var jobID = ""
    var attemptID = ""
    var token = ""
    let payload: Data
    let digest: String
    var envelope = Data()
    var host: ComputePeerHost!
    var requester: WKWebView?
    var requesterWindow: NSWindow?
    var retainedRequesters: [WKWebView] = []
    var retainedWindows: [NSWindow] = []
    var requesterServers: [LoopbackHTTPServer] = []
    var failure: String?
    var resultSeen = false
    var doneSeen = false
    var completedJobs = 0

    var finished: Bool { failure != nil || completedJobs == 2 }

    init(workerURL: URL, peerDocument: Data) throws {
        self.workerURL = workerURL
        self.peerDocument = peerDocument
        let image = try makeNoisyPNG()
        let payloadObject: [String: Any] = ["data_base64": image.base64EncodedString(), "mime_type": "image/png",
                                            "max_edge": 128, "quality": 72]
        self.payload = try JSONSerialization.data(withJSONObject: payloadObject, options: [.sortedKeys])
        self.digest = sha256(payload)
        super.init()
        try prepareJob()
    }

    func prepareJob() throws {
        jobID = UUID().uuidString.lowercased(); attemptID = UUID().uuidString.lowercased()
        let tokenBytes = Data((0..<32).map { UInt8($0 &+ 11 &+ completedJobs) })
        token = tokenBytes.base64EncodedString().replacingOccurrences(of: "+", with: "-")
            .replacingOccurrences(of: "/", with: "_").replacingOccurrences(of: "=", with: "")
        let frame: [String: Any] = ["type": "compute_job", "generation": generation, "job_id": jobID,
                                    "attempt_id": attemptID, "operation": "image_resize_v1",
                                    "payload_base64": payload.base64EncodedString(), "payload_sha256": digest,
                                    "timeout_ms": 25_000]
        envelope = try JSONSerialization.data(withJSONObject: ["token": token, "frame": frame], options: [.sortedKeys])
    }

    func start() async {
        host = ComputePeerHost(workerURL: workerURL, peerDocument: peerDocument) { [weak self] message in
            self?.receiveHost(message)
        }
        do {
            try await host.startIfNeeded()
            try await launchRequester()
        } catch { failure = "host_start: \(error)" }
    }

    func launchRequester() async throws {
        let controller = WKUserContentController(); controller.add(self, name: "test")
        let configuration = WKWebViewConfiguration(); configuration.userContentController = controller
        configuration.websiteDataStore = .nonPersistent()
        let view = WKWebView(frame: .zero, configuration: configuration); view.navigationDelegate = self; requester = view
        let hidden = NSWindow(contentRect: NSRect(x: -10_000, y: -10_000, width: 1, height: 1),
                              styleMask: [.borderless], backing: .buffered, defer: false)
        hidden.contentView = view; hidden.orderFront(nil); requesterWindow = hidden
        retainedRequesters.append(view); retainedWindows.append(hidden)
        var html = requesterDocument.replacingOccurrences(of: "REQUEST_BYTES", with: envelope.base64EncodedString())
        if CommandLine.arguments.contains("--stun") {
            html = html.replacingOccurrences(of: "iceServers:[]", with: "iceServers:[{urls:'stun:stun.cloudflare.com:3478'}]")
        }
        let document = Data(html.utf8)
        let local = LoopbackHTTPServer(port: 0) { request in
            guard request.method == "GET", request.path == "/requester" else {
                return .json(["detail": "not_found"], status: 404)
            }
            return LocalHTTPResponse(headers: ["Content-Type": "text/html; charset=utf-8"], body: document)
        }
        let port = try await local.start(); requesterServers.append(local)
        view.load(URLRequest(url: URL(string: "http://127.0.0.1:\(port)/requester")!))
    }

    func advanceIfReady() {
        guard resultSeen, doneSeen, failure == nil else { return }
        completedJobs += 1
        guard completedJobs < 2 else { return }
        resultSeen = false; doneSeen = false
        Task { @MainActor in
            do { try prepareJob(); try await launchRequester() }
            catch { failure = "prepare_second_job: \(error)" }
        }
    }

    func receiveHost(_ message: [String: Any]) {
        guard message["generation"] as? String == generation,
              message["job_id"] as? String == jobID,
              message["attempt_id"] as? String == attemptID,
              message["payload_sha256"] as? String == digest else { failure = "host_ids"; return }
        if message["type"] as? String == "compute_peer_answer",
           let answer = message["answer"] as? [String: Any],
           let bytes = try? JSONSerialization.data(withJSONObject: checkedDescription(answer)),
           let json = String(data: bytes, encoding: .utf8) {
            requester?.evaluateJavaScript("window.applyAnswer(\(json)); null") { [weak self] _, error in
                if let error { self?.failure = "apply_answer: \(error)" }
            }
        } else if message["type"] as? String == "compute_peer_done" {
            guard message["ok"] as? Bool == true,
                  message["input_bytes"] as? Int == payload.count,
                  (message["output_bytes"] as? Int ?? 0) > 0,
                  (0...30_000).contains(message["duration_ms"] as? Int ?? -1) else {
                failure = "done_metrics: \(message) expected_input=\(payload.count)"; return
            }
            doneSeen = true
            advanceIfReady()
        } else { failure = "host_message" }
    }

    func userContentController(_ userContentController: WKUserContentController, didReceive message: WKScriptMessage) {
        guard let body = message.body as? [String: Any], let type = body["type"] as? String else { failure = "requester_message"; return }
        if type == "diagnostic" {
            fputs("Peer check: \(body["detail"] as? String ?? "unknown")\n", stderr)
            return
        }
        if type == "offer", let offer = body["offer"] as? [String: Any] {
            do {
                try host.accept(["generation": generation, "job_id": jobID, "attempt_id": attemptID,
                                 "operation": "image_resize_v1", "payload_sha256": digest,
                                 "payload_bytes": payload.count, "timeout_ms": 25_000,
                                 "token": token, "offer": checkedDescription(offer)], generation: generation)
            } catch { failure = "accept_offer: \(error)" }
        } else if type == "result", let json = body["json"] as? String, let bytes = json.data(using: .utf8),
                  let result = try? JSONSerialization.jsonObject(with: bytes) as? [String: Any] {
            guard result["type"] as? String == "compute_result", result["ok"] as? Bool == true,
                  result["generation"] as? String == generation, result["job_id"] as? String == jobID,
                  result["attempt_id"] as? String == attemptID, result["payload_sha256"] as? String == digest,
                  let row = result["result"] as? [String: Any], row["mime_type"] as? String == "image/jpeg",
                  row["width"] as? Int == 128, row["height"] as? Int == 96,
                  let output64 = row["data_base64"] as? String, let output = Data(base64Encoded: output64),
                  row["sha256"] as? String == sha256(output), output.count < payload.count else { failure = "worker_result"; return }
            resultSeen = true
            let ack: [String: Any] = ["type": "ack", "job_id": jobID, "attempt_id": attemptID,
                                      "payload_sha256": digest, "outcome": "completed"]
            if let ackBytes = try? JSONSerialization.data(withJSONObject: ack),
               let ackJSON = String(data: ackBytes, encoding: .utf8) {
                requester?.evaluateJavaScript("window.ack(\(ackJSON)); null", completionHandler: nil)
            } else { failure = "ack_encode"; return }
            advanceIfReady()
        } else { failure = "requester_\(type): \(body["detail"] as? String ?? "invalid")" }
    }

    func webView(_ webView: WKWebView, didFail navigation: WKNavigation!, withError error: Error) { failure = "requester_load: \(error)" }
    func webView(_ webView: WKWebView, didFailProvisionalNavigation navigation: WKNavigation!, withError error: Error) { failure = "requester_load: \(error)" }
}

@MainActor private func runCheck() async throws {
    guard (3...4).contains(CommandLine.arguments.count) else { fatalError("usage: ComputePeerChecks WORKER PEER_HTML [--loopback-candidates|--stun]") }
    let workerURL = URL(fileURLWithPath: CommandLine.arguments[1])
    guard FileManager.default.isExecutableFile(atPath: workerURL.path) else { fatalError("worker is not executable") }
    var peerBytes = try Data(contentsOf: URL(fileURLWithPath: CommandLine.arguments[2]))
    guard var peerHTML = String(data: peerBytes, encoding: .utf8) else { fatalError("invalid peer resource") }
    if !CommandLine.arguments.contains("--stun") {
        peerHTML = peerHTML.replacingOccurrences(of: "iceServers:[{urls:'stun:stun.cloudflare.com:3478'}]", with: "iceServers:[]")
    }
    peerBytes = Data(peerHTML.utf8)

    let application = NSApplication.shared
    application.setActivationPolicy(.accessory)
    application.finishLaunching()
    let check = try PeerCheck(workerURL: workerURL, peerDocument: peerBytes)
    await check.start()
    let deadline = Date().addingTimeInterval(60)
    while !check.finished, Date() < deadline {
        try await Task.sleep(nanoseconds: 50_000_000)
    }
    if !check.finished { check.failure = "peer exchange timed out" }
    check.host?.cancelAll()
    if let failure = check.failure { fputs("FAIL: \(failure)\n", stderr); exit(EXIT_FAILURE) }
    guard check.finished else { fatalError("FAIL: peer exchange stopped") }
    print("PASS: two consecutive WKWebView DataChannels -> loopback leases -> real Swift worker (candidate_mode=\(CommandLine.arguments.contains("--loopback-candidates") ? "forced-loopback" : "network"))")
}

Task { @MainActor in
    do {
        try await runCheck()
        exit(EXIT_SUCCESS)
    } catch {
        fputs("FAIL: \(error)\n", stderr)
        exit(EXIT_FAILURE)
    }
}
NSApplication.shared.run()
