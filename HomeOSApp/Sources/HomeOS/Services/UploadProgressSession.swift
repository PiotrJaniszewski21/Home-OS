import Foundation

enum UploadProgressSession {
    static func upload(
        request: URLRequest,
        body: Data,
        trustsLocalSelfSignedCertificates: Bool = false,
        onProgress: @escaping @Sendable (Double?) async -> Void
    ) async throws -> (Data, URLResponse) {
        let delegate = UploadDelegate(
            trustsLocalSelfSignedCertificates: trustsLocalSelfSignedCertificates,
            onProgress: onProgress
        )
        let configuration = URLSessionConfiguration.default
        let session = URLSession(configuration: configuration, delegate: delegate, delegateQueue: nil)
        defer { session.finishTasksAndInvalidate() }

        return try await withTaskCancellationHandler {
            try await withCheckedThrowingContinuation { continuation in
                delegate.continuation = continuation
                Task { await onProgress(0) }
                session.uploadTask(with: request, from: body).resume()
            }
        } onCancel: {
            session.invalidateAndCancel()
        }
    }
}

private final class UploadDelegate: NSObject, URLSessionDataDelegate, URLSessionTaskDelegate, @unchecked Sendable {
    var continuation: CheckedContinuation<(Data, URLResponse), Error>?

    private let trustsLocalSelfSignedCertificates: Bool
    private let onProgress: @Sendable (Double?) async -> Void
    private let lock = NSLock()
    private var responseData = Data()
    private var response: URLResponse?
    private var didResume = false

    init(
        trustsLocalSelfSignedCertificates: Bool,
        onProgress: @escaping @Sendable (Double?) async -> Void
    ) {
        self.trustsLocalSelfSignedCertificates = trustsLocalSelfSignedCertificates
        self.onProgress = onProgress
    }

    func urlSession(
        _ session: URLSession,
        didReceive challenge: URLAuthenticationChallenge,
        completionHandler: @escaping (URLSession.AuthChallengeDisposition, URLCredential?) -> Void
    ) {
        guard
            trustsLocalSelfSignedCertificates,
            LocalCertificateTrustPolicy.shouldTrust(challenge: challenge),
            let trust = challenge.protectionSpace.serverTrust
        else {
            completionHandler(.performDefaultHandling, nil)
            return
        }

        completionHandler(.useCredential, URLCredential(trust: trust))
    }

    func urlSession(
        _ session: URLSession,
        task: URLSessionTask,
        didSendBodyData bytesSent: Int64,
        totalBytesSent: Int64,
        totalBytesExpectedToSend: Int64
    ) {
        let progress = totalBytesExpectedToSend > 0 ? min(Double(totalBytesSent) / Double(totalBytesExpectedToSend), 1) : nil
        Task { await onProgress(progress) }
    }

    func urlSession(
        _ session: URLSession,
        dataTask: URLSessionDataTask,
        didReceive response: URLResponse,
        completionHandler: @escaping (URLSession.ResponseDisposition) -> Void
    ) {
        lock.withLock {
            self.response = response
        }
        completionHandler(.allow)
    }

    func urlSession(_ session: URLSession, dataTask: URLSessionDataTask, didReceive data: Data) {
        lock.withLock {
            responseData.append(data)
        }
    }

    func urlSession(_ session: URLSession, task: URLSessionTask, didCompleteWithError error: Error?) {
        let result: Result<(Data, URLResponse), Error> = lock.withLock {
            if didResume {
                return .failure(URLError(.cancelled))
            }
            didResume = true
            if let error {
                return .failure(error)
            }
            guard let response else {
                return .failure(URLError(.badServerResponse))
            }
            return .success((responseData, response))
        }

        switch result {
        case .success(let value):
            Task { await onProgress(1) }
            continuation?.resume(returning: value)
        case .failure(let error):
            continuation?.resume(throwing: error)
        }
    }
}

private extension NSLock {
    func withLock<T>(_ body: () -> T) -> T {
        lock()
        defer { unlock() }
        return body()
    }
}
