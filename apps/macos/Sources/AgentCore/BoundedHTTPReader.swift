import Foundation

/// A per-task deadline preserves the shared connection pool and cancels only
/// the stalled request. The byte limit applies while receiving the body.
public enum BoundedHTTPReader {
    public static func read(_ request: URLRequest, using session: URLSession,
                            timeout: TimeInterval, limit: Int) async throws -> (Data, HTTPURLResponse) {
        guard timeout.isFinite, timeout > 0, timeout <= 300, limit > 0 else {
            throw URLError(.badURL)
        }
        let receiver = Receiver(limit: limit)
        let task = session.dataTask(with: request)
        task.delegate = receiver
        return try await CallbackReceiveGate.run(
            nanoseconds: UInt64(timeout * 1_000_000_000),
            onCancel: { receiver.cancel(); task.cancel() },
            start: { completion in
                receiver.start(completion)
                task.resume()
            }
        )
    }

    private final class Receiver: NSObject, URLSessionDataDelegate, @unchecked Sendable {
        private let lock = NSLock()
        private let limit: Int
        private var data = Data()
        private var response: HTTPURLResponse?
        private var completion: (@Sendable (Result<(Data, HTTPURLResponse), Error>) -> Void)?
        private var finished = false

        init(limit: Int) { self.limit = limit }

        func start(_ completion: @escaping @Sendable (Result<(Data, HTTPURLResponse), Error>) -> Void) {
            lock.lock()
            guard !finished else { lock.unlock(); completion(.failure(CancellationError())); return }
            self.completion = completion
            lock.unlock()
        }

        func cancel() {
            lock.lock()
            finished = true; completion = nil; data = Data(); response = nil
            lock.unlock()
        }

        private func finish(_ result: Result<(Data, HTTPURLResponse), Error>) {
            lock.lock()
            guard !finished else { lock.unlock(); return }
            finished = true
            let callback = completion
            completion = nil; data = Data(); response = nil
            lock.unlock()
            callback?(result)
        }

        func urlSession(_ session: URLSession, dataTask: URLSessionDataTask,
                        didReceive response: URLResponse,
                        completionHandler: @escaping (URLSession.ResponseDisposition) -> Void) {
            guard let response = response as? HTTPURLResponse,
                  response.expectedContentLength <= Int64(limit) else {
                finish(.failure(URLError(.dataLengthExceedsMaximum)))
                completionHandler(.cancel); return
            }
            lock.lock()
            let active = !finished
            if active { self.response = response }
            lock.unlock()
            completionHandler(active ? .allow : .cancel)
        }

        func urlSession(_ session: URLSession, dataTask: URLSessionDataTask, didReceive bytes: Data) {
            lock.lock()
            guard !finished else { lock.unlock(); return }
            guard bytes.count <= limit - data.count else {
                lock.unlock()
                finish(.failure(URLError(.dataLengthExceedsMaximum)))
                dataTask.cancel(); return
            }
            data.append(bytes)
            lock.unlock()
        }

        func urlSession(_ session: URLSession, task: URLSessionTask, didCompleteWithError error: Error?) {
            if let error { finish(.failure(error)); return }
            lock.lock()
            let result = response.map { (data, $0) }
            lock.unlock()
            if let result { finish(.success(result)) }
            else { finish(.failure(URLError(.badServerResponse))) }
        }

        func urlSession(_ session: URLSession, task: URLSessionTask,
                        willPerformHTTPRedirection response: HTTPURLResponse,
                        newRequest request: URLRequest,
                        completionHandler: @escaping (URLRequest?) -> Void) {
            completionHandler(nil)
        }
    }
}
