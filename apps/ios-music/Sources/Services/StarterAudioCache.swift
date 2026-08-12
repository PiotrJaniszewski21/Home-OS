import AVFoundation
import CryptoKit
import Foundation
import UniformTypeIdentifiers

private struct StarterAudioCacheMetadata: Codable {
    let sourceFingerprint: String
    let contentLength: Int64
    let contentType: String
    let supportsByteRanges: Bool
    var lastAccessedAt: Date
}

private struct StarterAudioCacheEntry {
    let data: Data
    let metadata: StarterAudioCacheMetadata
}

final class StarterAudioCache: @unchecked Sendable {
    static let shared = StarterAudioCache()
    static let prefixByteLimit = 256 * 1_024

    private static let maximumBytes: Int64 = 512 * 1_024 * 1_024
    private static let targetBytes: Int64 = 384 * 1_024 * 1_024
    private static let maximumAge: TimeInterval = 90 * 24 * 60 * 60
    private static let minimumUsefulBytes = 64 * 1_024

    private let lock = NSLock()
    private var rootURL: URL?
    private var activeTasks = [String: Task<Void, Never>]()

    private init() {}

    func cancelAll() {
        lock.lock()
        let tasks = Array(activeTasks.values)
        activeTasks.removeAll()
        warmingKeys.removeAll()
        lock.unlock()
        for task in tasks {
            task.cancel()
        }
    }

    func connect(client: APIClient?) {
        lock.lock()
        defer { lock.unlock() }
        guard let client else {
            rootURL = nil
            warmingKeys.removeAll()
            activeTasks.values.forEach { $0.cancel() }
            activeTasks.removeAll()
            return
        }
        let base = FileManager.default.urls(
            for: .cachesDirectory,
            in: .userDomainMask
        )[0]
        rootURL = base
            .appending(path: "HomeMusic/StarterAudio", directoryHint: .isDirectory)
            .appending(path: HomeFeedStore.namespace(for: client), directoryHint: .isDirectory)
        try? ensureDirectoryLocked()
        pruneLocked()
    }

    func warm(
        trackID: String,
        sourceURL: URL,
        authorization: String?
    ) {
        let sourceFingerprint = Self.fingerprint(for: sourceURL)
        if entry(trackID: trackID, sourceFingerprint: sourceFingerprint) != nil {
            return
        }

        let warmingKey = "\(trackID)|\(sourceFingerprint)"
        lock.lock()
        guard rootURL != nil, warmingKeys.insert(warmingKey).inserted else {
            lock.unlock()
            return
        }

        let task = Task.detached(priority: .utility) { [weak self] in
            defer {
                self?.finishWarming(warmingKey)
            }
            guard !Task.isCancelled else { return }
            var request = URLRequest(url: sourceURL)
            request.timeoutInterval = 30
            request.setValue("audio/*,*/*;q=0.8", forHTTPHeaderField: "Accept")
            request.setValue("identity", forHTTPHeaderField: "Accept-Encoding")
            request.setValue(
                "bytes=0-\(Self.prefixByteLimit - 1)",
                forHTTPHeaderField: "Range"
            )
            if let authorization {
                request.setValue(authorization, forHTTPHeaderField: "Authorization")
            }

            guard let (data, response) = try? await URLSession.shared.data(for: request),
                  !Task.isCancelled,
                  let http = response as? HTTPURLResponse,
                  http.statusCode == 200 || http.statusCode == 206 else {
                return
            }
            let contentLength = Self.totalLength(from: http)
            let mimeType = http.mimeType ?? "audio/mp4"
            let contentType = UTType(mimeType: mimeType)?.identifier
                ?? UTType.mpeg4Audio.identifier
            let supportsByteRanges = http.statusCode == 206
                || http.value(forHTTPHeaderField: "Accept-Ranges")?
                    .lowercased() == "bytes"
            self?.store(
                data,
                trackID: trackID,
                sourceFingerprint: sourceFingerprint,
                contentLength: contentLength,
                contentType: contentType,
                supportsByteRanges: supportsByteRanges
            )
        }
        activeTasks[warmingKey] = task
        lock.unlock()
    }

    private func finishWarming(_ warmingKey: String) {
        lock.lock()
        warmingKeys.remove(warmingKey)
        activeTasks.removeValue(forKey: warmingKey)
        lock.unlock()
    }

    fileprivate func hasEntry(trackID: String, sourceURL: URL) -> Bool {
        entry(
            trackID: trackID,
            sourceFingerprint: Self.fingerprint(for: sourceURL)
        ) != nil
    }

    fileprivate func entry(
        trackID: String,
        sourceFingerprint: String
    ) -> StarterAudioCacheEntry? {
        lock.lock()
        defer { lock.unlock() }
        guard rootURL != nil else { return nil }
        let metadataURL = metadataURLLocked(trackID: trackID)
        let dataURL = dataURLLocked(trackID: trackID)
        guard let metadataData = try? Data(contentsOf: metadataURL),
              var metadata = try? JSONDecoder().decode(
                StarterAudioCacheMetadata.self,
                from: metadataData
              ),
              metadata.sourceFingerprint == sourceFingerprint,
              Date().timeIntervalSince(metadata.lastAccessedAt) <= Self.maximumAge,
              let data = try? Data(contentsOf: dataURL),
              data.count >= Self.minimumUsefulBytes,
              data.count <= Self.prefixByteLimit else {
            removeLocked(trackID: trackID)
            return nil
        }
        metadata.lastAccessedAt = Date()
        try? writeMetadataLocked(metadata, to: metadataURL)
        return StarterAudioCacheEntry(data: data, metadata: metadata)
    }

    fileprivate func store(
        _ data: Data,
        trackID: String,
        sourceFingerprint: String,
        contentLength: Int64,
        contentType: String,
        supportsByteRanges: Bool
    ) {
        let prefix = Data(data.prefix(Self.prefixByteLimit))
        guard prefix.count >= Self.minimumUsefulBytes else { return }
        lock.lock()
        defer { lock.unlock() }
        guard rootURL != nil else { return }
        do {
            try ensureDirectoryLocked()
            if let existing = existingMetadataLocked(trackID: trackID),
               existing.sourceFingerprint == sourceFingerprint,
               let size = try? dataURLLocked(trackID: trackID)
                    .resourceValues(forKeys: [.fileSizeKey]).fileSize,
               size >= prefix.count {
                return
            }
            try prefix.write(
                to: dataURLLocked(trackID: trackID),
                options: [.atomic, .completeFileProtectionUntilFirstUserAuthentication]
            )
            try writeMetadataLocked(
                StarterAudioCacheMetadata(
                    sourceFingerprint: sourceFingerprint,
                    contentLength: contentLength,
                    contentType: contentType,
                    supportsByteRanges: supportsByteRanges,
                    lastAccessedAt: Date()
                ),
                to: metadataURLLocked(trackID: trackID)
            )
            pruneLocked()
        } catch {}
    }

    private func ensureDirectoryLocked() throws {
        guard let rootURL else { return }
        try FileManager.default.createDirectory(
            at: rootURL,
            withIntermediateDirectories: true
        )
    }

    private func digest(_ trackID: String) -> String {
        SHA256.hash(data: Data(trackID.utf8))
            .map { String(format: "%02x", $0) }
            .joined()
    }

    private func dataURLLocked(trackID: String) -> URL {
        rootURL!.appending(path: "\(digest(trackID)).prefix")
    }

    private func metadataURLLocked(trackID: String) -> URL {
        rootURL!.appending(path: "\(digest(trackID)).json")
    }

    private func existingMetadataLocked(trackID: String) -> StarterAudioCacheMetadata? {
        guard let data = try? Data(contentsOf: metadataURLLocked(trackID: trackID)) else {
            return nil
        }
        return try? JSONDecoder().decode(StarterAudioCacheMetadata.self, from: data)
    }

    private func writeMetadataLocked(
        _ metadata: StarterAudioCacheMetadata,
        to url: URL
    ) throws {
        let data = try JSONEncoder().encode(metadata)
        try data.write(
            to: url,
            options: [.atomic, .completeFileProtectionUntilFirstUserAuthentication]
        )
    }

    private func removeLocked(trackID: String) {
        guard rootURL != nil else { return }
        try? FileManager.default.removeItem(at: dataURLLocked(trackID: trackID))
        try? FileManager.default.removeItem(at: metadataURLLocked(trackID: trackID))
    }

    private func finishWarming(_ key: String) {
        lock.lock()
        warmingKeys.remove(key)
        lock.unlock()
    }

    fileprivate static func fingerprint(for url: URL) -> String {
        guard let components = URLComponents(
            url: url,
            resolvingAgainstBaseURL: false
        ) else { return url.path }
        let stableNames = Set(["id", "itag", "clen", "mime"])
        let stableQuery = (components.queryItems ?? [])
            .filter { stableNames.contains($0.name) }
            .sorted { $0.name < $1.name }
            .map { "\($0.name)=\($0.value ?? "")" }
            .joined(separator: "&")
        return "\(components.path)?\(stableQuery)"
    }

    private static func totalLength(from response: HTTPURLResponse) -> Int64 {
        if let contentRange = response.value(forHTTPHeaderField: "Content-Range"),
           let total = contentRange.split(separator: "/").last,
           let value = Int64(total) {
            return value
        }
        return max(0, response.expectedContentLength)
    }

    private func pruneLocked() {
        guard let rootURL,
              let files = try? FileManager.default.contentsOfDirectory(
                at: rootURL,
                includingPropertiesForKeys: [
                    .contentModificationDateKey,
                    .fileSizeKey,
                ],
                options: [.skipsHiddenFiles]
              ) else { return }

        let cutoff = Date().addingTimeInterval(-Self.maximumAge)
        var prefixes: [(URL, URL, Int64, Date)] = []
        for file in files where file.pathExtension == "prefix" {
            let metadataURL = file.deletingPathExtension().appendingPathExtension("json")
            guard let values = try? file.resourceValues(
                forKeys: [.contentModificationDateKey, .fileSizeKey]
            ) else { continue }
            let modified = values.contentModificationDate ?? .distantPast
            if modified < cutoff {
                try? FileManager.default.removeItem(at: file)
                try? FileManager.default.removeItem(at: metadataURL)
                continue
            }
            prefixes.append((
                file,
                metadataURL,
                Int64(values.fileSize ?? 0),
                modified
            ))
        }

        var totalBytes = prefixes.reduce(Int64(0)) { $0 + $1.2 }
        guard totalBytes > Self.maximumBytes else { return }
        for (dataURL, metadataURL, size, _) in prefixes.sorted(by: { $0.3 < $1.3 })
        where totalBytes > Self.targetBytes {
            try? FileManager.default.removeItem(at: dataURL)
            try? FileManager.default.removeItem(at: metadataURL)
            totalBytes -= size
        }
    }
}

final class CachedAudioAsset {
    let asset: AVURLAsset
    private let loader: StarterAudioResourceLoader

    init?(
        trackID: String,
        sourceURL: URL,
        authorization: String?
    ) {
        guard StarterAudioCache.shared.hasEntry(
            trackID: trackID,
            sourceURL: sourceURL
        ) else { return nil }
        guard var components = URLComponents(
            url: sourceURL,
            resolvingAgainstBaseURL: false
        ) else { return nil }
        components.scheme = "homemusic-cache"
        guard let assetURL = components.url else { return nil }
        loader = StarterAudioResourceLoader(
            trackID: trackID,
            sourceURL: sourceURL,
            authorization: authorization
        )
        asset = AVURLAsset(url: assetURL)
        asset.resourceLoader.setDelegate(
            loader,
            queue: DispatchQueue(
                label: "HomeMusic.StarterAudio.ResourceLoader.\(trackID)"
            )
        )
    }
}

private final class StarterAudioResourceLoader: NSObject, AVAssetResourceLoaderDelegate {
    private let trackID: String
    private let sourceURL: URL
    private let sourceFingerprint: String
    private let authorization: String?
    private let lock = NSLock()
    private var requests: [ObjectIdentifier: StarterAudioRangeRequest] = [:]

    init(trackID: String, sourceURL: URL, authorization: String?) {
        self.trackID = trackID
        self.sourceURL = sourceURL
        self.authorization = authorization
        sourceFingerprint = StarterAudioCache.fingerprint(for: sourceURL)
    }

    func resourceLoader(
        _ resourceLoader: AVAssetResourceLoader,
        shouldWaitForLoadingOfRequestedResource loadingRequest: AVAssetResourceLoadingRequest
    ) -> Bool {
        let identifier = ObjectIdentifier(loadingRequest)
        let request = StarterAudioRangeRequest(
            loadingRequest: loadingRequest,
            trackID: trackID,
            sourceURL: sourceURL,
            sourceFingerprint: sourceFingerprint,
            authorization: authorization
        ) { [weak self] in
            self?.removeRequest(identifier)
        }
        lock.lock()
        requests[identifier] = request
        lock.unlock()
        request.start()
        return true
    }

    func resourceLoader(
        _ resourceLoader: AVAssetResourceLoader,
        didCancel loadingRequest: AVAssetResourceLoadingRequest
    ) {
        let identifier = ObjectIdentifier(loadingRequest)
        lock.lock()
        let request = requests.removeValue(forKey: identifier)
        lock.unlock()
        request?.cancel()
    }

    private func removeRequest(_ identifier: ObjectIdentifier) {
        lock.lock()
        requests.removeValue(forKey: identifier)
        lock.unlock()
    }
}

private final class StarterAudioRangeRequest: NSObject, URLSessionDataDelegate {
    private let loadingRequest: AVAssetResourceLoadingRequest
    private let trackID: String
    private let sourceURL: URL
    private let sourceFingerprint: String
    private let authorization: String?
    private let completion: () -> Void
    private let stateLock = NSLock()
    private var session: URLSession?
    private var task: URLSessionDataTask?
    private var isFinished = false
    private var networkOffset: Int64 = 0
    private var prefixData = Data()
    private var contentLength: Int64 = 0
    private var contentType = UTType.mpeg4Audio.identifier
    private var supportsByteRanges = true

    init(
        loadingRequest: AVAssetResourceLoadingRequest,
        trackID: String,
        sourceURL: URL,
        sourceFingerprint: String,
        authorization: String?,
        completion: @escaping () -> Void
    ) {
        self.loadingRequest = loadingRequest
        self.trackID = trackID
        self.sourceURL = sourceURL
        self.sourceFingerprint = sourceFingerprint
        self.authorization = authorization
        self.completion = completion
    }

    func start() {
        if let entry = StarterAudioCache.shared.entry(
            trackID: trackID,
            sourceFingerprint: sourceFingerprint
        ) {
            prefixData = entry.data
            contentLength = entry.metadata.contentLength
            contentType = entry.metadata.contentType
            supportsByteRanges = entry.metadata.supportsByteRanges
            applyContentInformation()
            if respondFromPrefix(), requestIsSatisfied() {
                finish()
                return
            }
            if loadingRequest.dataRequest == nil {
                finish()
                return
            }
        }
        beginNetworkRequest()
    }

    func cancel() {
        stateLock.lock()
        guard !isFinished else {
            stateLock.unlock()
            return
        }
        isFinished = true
        stateLock.unlock()
        task?.cancel()
        session?.invalidateAndCancel()
        persistPrefix()
        completion()
    }

    private func beginNetworkRequest() {
        let offset = requestedCurrentOffset
        var request = URLRequest(url: sourceURL)
        request.timeoutInterval = 90
        request.setValue("audio/*,*/*;q=0.8", forHTTPHeaderField: "Accept")
        request.setValue("identity", forHTTPHeaderField: "Accept-Encoding")
        if let authorization {
            request.setValue(authorization, forHTTPHeaderField: "Authorization")
        }
        if let end = requestedEndOffset {
            request.setValue("bytes=\(offset)-\(max(offset, end - 1))", forHTTPHeaderField: "Range")
        } else {
            request.setValue("bytes=\(offset)-", forHTTPHeaderField: "Range")
        }

        let configuration = URLSessionConfiguration.ephemeral
        configuration.waitsForConnectivity = true
        configuration.timeoutIntervalForRequest = 45
        configuration.timeoutIntervalForResource = 15 * 60
        configuration.requestCachePolicy = .reloadIgnoringLocalAndRemoteCacheData
        let session = URLSession(
            configuration: configuration,
            delegate: self,
            delegateQueue: nil
        )
        self.session = session
        let task = session.dataTask(with: request)
        self.task = task
        task.resume()
    }

    func urlSession(
        _ session: URLSession,
        dataTask: URLSessionDataTask,
        didReceive response: URLResponse,
        completionHandler: @escaping (URLSession.ResponseDisposition) -> Void
    ) {
        guard let http = response as? HTTPURLResponse,
              http.statusCode == 200 || http.statusCode == 206 else {
            completionHandler(.cancel)
            finish(error: URLError(.badServerResponse))
            return
        }

        networkOffset = Self.rangeStart(from: http) ?? 0
        contentLength = Self.totalLength(from: http)
        let mimeType = http.mimeType ?? "audio/mp4"
        contentType = UTType(mimeType: mimeType)?.identifier
            ?? UTType.mpeg4Audio.identifier
        supportsByteRanges = http.statusCode == 206
            || http.value(forHTTPHeaderField: "Accept-Ranges")?
                .lowercased() == "bytes"
        applyContentInformation()
        if loadingRequest.dataRequest == nil {
            completionHandler(.cancel)
            finish()
            return
        }
        completionHandler(.allow)
    }

    func urlSession(
        _ session: URLSession,
        dataTask: URLSessionDataTask,
        didReceive data: Data
    ) {
        guard !finished else { return }
        capturePrefix(data, startingAt: networkOffset)
        respond(data, startingAt: networkOffset)
        networkOffset += Int64(data.count)
        if requestIsSatisfied() {
            task?.cancel()
            finish()
        }
    }

    func urlSession(
        _ session: URLSession,
        task: URLSessionTask,
        didCompleteWithError error: Error?
    ) {
        if let error, !finished {
            finish(error: error)
        } else if !finished {
            finish()
        }
    }

    private var requestedCurrentOffset: Int64 {
        guard let dataRequest = loadingRequest.dataRequest else { return 0 }
        return max(dataRequest.requestedOffset, dataRequest.currentOffset)
    }

    private var requestedEndOffset: Int64? {
        guard let dataRequest = loadingRequest.dataRequest,
              !dataRequest.requestsAllDataToEndOfResource else { return nil }
        return dataRequest.requestedOffset + Int64(dataRequest.requestedLength)
    }

    private func requestIsSatisfied() -> Bool {
        guard let end = requestedEndOffset else { return false }
        return requestedCurrentOffset >= end
    }

    private func respondFromPrefix() -> Bool {
        guard let dataRequest = loadingRequest.dataRequest else { return false }
        let offset = requestedCurrentOffset
        guard offset >= 0, offset < Int64(prefixData.count) else { return false }
        let availableEnd = min(
            Int64(prefixData.count),
            requestedEndOffset ?? Int64(prefixData.count)
        )
        guard availableEnd > offset else { return false }
        dataRequest.respond(
            with: prefixData.subdata(
                in: Int(offset)..<Int(availableEnd)
            )
        )
        return true
    }

    private func respond(_ data: Data, startingAt start: Int64) {
        guard let dataRequest = loadingRequest.dataRequest else { return }
        let current = requestedCurrentOffset
        let chunkEnd = start + Int64(data.count)
        guard current < chunkEnd else { return }
        let responseStart = max(current, start)
        let responseEnd = min(
            chunkEnd,
            requestedEndOffset ?? chunkEnd
        )
        guard responseEnd > responseStart else { return }
        let lowerBound = Int(responseStart - start)
        let upperBound = Int(responseEnd - start)
        dataRequest.respond(with: data.subdata(in: lowerBound..<upperBound))
    }

    private func capturePrefix(_ data: Data, startingAt start: Int64) {
        guard start <= Int64(prefixData.count),
              prefixData.count < StarterAudioCache.prefixByteLimit else { return }
        let overlap = max(0, Int64(prefixData.count) - start)
        guard overlap < Int64(data.count) else { return }
        let available = min(
            data.count - Int(overlap),
            StarterAudioCache.prefixByteLimit - prefixData.count
        )
        guard available > 0 else { return }
        let lowerBound = Int(overlap)
        prefixData.append(data.subdata(in: lowerBound..<(lowerBound + available)))
        if prefixData.count == StarterAudioCache.prefixByteLimit {
            persistPrefix()
        }
    }

    private func applyContentInformation() {
        guard let information = loadingRequest.contentInformationRequest else { return }
        information.contentType = contentType
        if contentLength > 0 {
            information.contentLength = contentLength
        }
        information.isByteRangeAccessSupported = supportsByteRanges
    }

    private func persistPrefix() {
        StarterAudioCache.shared.store(
            prefixData,
            trackID: trackID,
            sourceFingerprint: sourceFingerprint,
            contentLength: contentLength,
            contentType: contentType,
            supportsByteRanges: supportsByteRanges
        )
    }

    private var finished: Bool {
        stateLock.lock()
        defer { stateLock.unlock() }
        return isFinished
    }

    private func finish(error: Error? = nil) {
        stateLock.lock()
        guard !isFinished else {
            stateLock.unlock()
            return
        }
        isFinished = true
        stateLock.unlock()
        persistPrefix()
        if let error {
            loadingRequest.finishLoading(with: error)
        } else {
            loadingRequest.finishLoading()
        }
        session?.finishTasksAndInvalidate()
        completion()
    }

    private static func rangeStart(from response: HTTPURLResponse) -> Int64? {
        guard let contentRange = response.value(forHTTPHeaderField: "Content-Range"),
              let range = contentRange.split(separator: " ").last?
                .split(separator: "/").first,
              let start = range.split(separator: "-").first else {
            return nil
        }
        return Int64(start)
    }

    private static func totalLength(from response: HTTPURLResponse) -> Int64 {
        if let contentRange = response.value(forHTTPHeaderField: "Content-Range"),
           let total = contentRange.split(separator: "/").last,
           let value = Int64(total) {
            return value
        }
        return max(0, response.expectedContentLength)
    }
}
