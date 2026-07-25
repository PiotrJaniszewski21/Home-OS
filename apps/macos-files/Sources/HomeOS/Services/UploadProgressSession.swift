import Foundation

enum UploadProgressSession {
    static func upload(
        request: URLRequest,
        bodyFile: URL,
        configuration: URLSessionConfiguration,
        trustsLocalSelfSignedCertificates: Bool = false,
        onProgress: @escaping @Sendable (Double?) async -> Void
    ) async throws -> (Data, URLResponse) {
        let delegate = UploadDelegate(
            trustsLocalSelfSignedCertificates: trustsLocalSelfSignedCertificates,
            onProgress: onProgress
        )
        let session = URLSession(configuration: configuration, delegate: delegate, delegateQueue: nil)
        defer { session.finishTasksAndInvalidate() }

        return try await withTaskCancellationHandler {
            try await withCheckedThrowingContinuation { continuation in
                delegate.continuation = continuation
                Task { await onProgress(0) }
                session.uploadTask(with: request, fromFile: bodyFile).resume()
            }
        } onCancel: {
            session.invalidateAndCancel()
        }
    }
}

enum DownloadProgressSession {
    static func download(
        request: URLRequest,
        configuration: URLSessionConfiguration,
        trustsLocalSelfSignedCertificates: Bool = false,
        onProgress: @escaping @Sendable (Double?) async -> Void
    ) async throws -> (URL, URLResponse) {
        let delegate = DownloadDelegate(
            trustsLocalSelfSignedCertificates: trustsLocalSelfSignedCertificates,
            onProgress: onProgress
        )
        let session = URLSession(configuration: configuration, delegate: delegate, delegateQueue: nil)
        defer { session.finishTasksAndInvalidate() }

        return try await withTaskCancellationHandler {
            try await withCheckedThrowingContinuation { continuation in
                delegate.continuation = continuation
                Task { await onProgress(nil) }
                session.downloadTask(with: request).resume()
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

    func urlSession(
        _ session: URLSession,
        task: URLSessionTask,
        willPerformHTTPRedirection response: HTTPURLResponse,
        newRequest request: URLRequest,
        completionHandler: @escaping (URLRequest?) -> Void
    ) {
        completionHandler(Self.safeRedirect(from: task.originalRequest?.url, to: request.url) ? request : nil)
    }

    private static func safeRedirect(from source: URL?, to destination: URL?) -> Bool {
        guard let source, let destination else { return false }
        return source.scheme?.lowercased() == "https"
            && destination.scheme?.lowercased() == "https"
            && source.host?.lowercased() == destination.host?.lowercased()
            && source.port == destination.port
    }
}

private final class DownloadDelegate: NSObject, URLSessionDownloadDelegate, @unchecked Sendable {
    var continuation: CheckedContinuation<(URL, URLResponse), Error>?

    private let trustsLocalSelfSignedCertificates: Bool
    private let onProgress: @Sendable (Double?) async -> Void
    private let lock = NSLock()
    private var downloadedURL: URL?
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
        downloadTask: URLSessionDownloadTask,
        didWriteData bytesWritten: Int64,
        totalBytesWritten: Int64,
        totalBytesExpectedToWrite: Int64
    ) {
        let progress = totalBytesExpectedToWrite > 0
            ? min(Double(totalBytesWritten) / Double(totalBytesExpectedToWrite), 1)
            : nil
        Task { await onProgress(progress) }
    }

    func urlSession(
        _ session: URLSession,
        downloadTask: URLSessionDownloadTask,
        didFinishDownloadingTo location: URL
    ) {
        let destination = FileManager.default.temporaryDirectory
            .appendingPathComponent("homeos-download-\(UUID().uuidString)")
        do {
            try FileManager.default.moveItem(at: location, to: destination)
            lock.withLock { downloadedURL = destination }
        } catch {
            resume(.failure(error))
        }
    }

    func urlSession(_ session: URLSession, task: URLSessionTask, didCompleteWithError error: Error?) {
        if let error {
            resume(.failure(error))
            return
        }
        guard let response = task.response, let downloadedURL = lock.withLock({ downloadedURL }) else {
            resume(.failure(URLError(.badServerResponse)))
            return
        }
        Task { await onProgress(1) }
        resume(.success((downloadedURL, response)))
    }

    private func resume(_ result: Result<(URL, URLResponse), Error>) {
        let continuation: CheckedContinuation<(URL, URLResponse), Error>? = lock.withLock {
            guard !didResume else { return nil }
            didResume = true
            return self.continuation
        }
        switch result {
        case .success(let value): continuation?.resume(returning: value)
        case .failure(let error): continuation?.resume(throwing: error)
        }
    }

    func urlSession(
        _ session: URLSession,
        task: URLSessionTask,
        willPerformHTTPRedirection response: HTTPURLResponse,
        newRequest request: URLRequest,
        completionHandler: @escaping (URLRequest?) -> Void
    ) {
        guard let source = task.originalRequest?.url, let destination = request.url else {
            completionHandler(nil)
            return
        }
        let safe = source.scheme?.lowercased() == "https"
            && destination.scheme?.lowercased() == "https"
            && source.host?.lowercased() == destination.host?.lowercased()
            && source.port == destination.port
        completionHandler(safe ? request : nil)
    }
}

private extension NSLock {
    func withLock<T>(_ body: () -> T) -> T {
        lock()
        defer { unlock() }
        return body()
    }
}
