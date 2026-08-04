import CryptoKit
import Foundation
import Network

private struct AutomaticTrackRecord: Codable {
    var track: Track
    var cachedAt: Date
    var lastAccessedAt: Date
}

private struct AutomaticCacheManifest: Codable {
    var records: [String: AutomaticTrackRecord]
    var lastMaintenanceAt: Date?
}

private final class AutomaticCacheNetworkMonitor: @unchecked Sendable {
    static let shared = AutomaticCacheNetworkMonitor()

    private let monitor = NWPathMonitor()
    private let queue = DispatchQueue(label: "HomeMusic.AutomaticCache.Network")
    private let lock = NSLock()
    private var wifiAvailable = false

    private init() {
        monitor.pathUpdateHandler = { [weak self] path in
            self?.lock.lock()
            self?.wifiAvailable = path.status == .satisfied
                && path.usesInterfaceType(.wifi)
            self?.lock.unlock()
        }
        monitor.start(queue: queue)
    }

    var canDownload: Bool {
#if targetEnvironment(macCatalyst)
        true
#else
        lock.lock()
        defer { lock.unlock() }
        return wifiAvailable
#endif
    }
}

@MainActor
final class AutomaticMusicCache {
    private static let maximumBytes: Int64 = 8 * 1_024 * 1_024 * 1_024
    private static let targetBytes: Int64 = 7 * 1_024 * 1_024 * 1_024
    private static let maximumIdleAge: TimeInterval = 90 * 24 * 60 * 60
    private static let maintenanceInterval: TimeInterval = 24 * 60 * 60
    private static let warmLimit = 24

    private var client: APIClient?
    private var rootURL: URL?
    private var records: [String: AutomaticTrackRecord] = [:]
    private var lastMaintenanceAt: Date?
    private var downloadsInProgress = Set<String>()
    private var maintenanceInProgress = false

    var maintenanceDue: Bool {
        guard client != nil else { return false }
        guard let lastMaintenanceAt else { return true }
        return Date().timeIntervalSince(lastMaintenanceAt) >= Self.maintenanceInterval
    }

    func connect(client: APIClient) {
        self.client = client
        let base = FileManager.default.urls(
            for: .cachesDirectory,
            in: .userDomainMask
        )[0]
        rootURL = base
            .appending(path: "HomeMusic/Automatic", directoryHint: .isDirectory)
            .appending(path: HomeFeedStore.namespace(for: client), directoryHint: .isDirectory)
        try? FileManager.default.createDirectory(
            at: tracksURL,
            withIntermediateDirectories: true
        )
        loadManifest()
    }

    func disconnect() {
        client = nil
        rootURL = nil
        records = [:]
        lastMaintenanceAt = nil
        downloadsInProgress = []
        maintenanceInProgress = false
    }

    func localURL(for track: Track) -> URL? {
        guard fileExists(trackID: track.id) else {
            if records.removeValue(forKey: track.id) != nil {
                try? saveManifest()
            }
            return nil
        }
        let now = Date()
        var shouldPersist = false
        if records[track.id] == nil {
            records[track.id] = AutomaticTrackRecord(
                track: track,
                cachedAt: now,
                lastAccessedAt: now
            )
            shouldPersist = true
        } else {
            shouldPersist = now.timeIntervalSince(
                records[track.id]?.lastAccessedAt ?? .distantPast
            ) >= 15 * 60
            records[track.id]?.track = track
            records[track.id]?.lastAccessedAt = now
        }
        if shouldPersist { try? saveManifest() }
        return fileURL(trackID: track.id)
    }

    func promote(_ track: Track, to destination: URL) -> Bool {
        guard fileExists(trackID: track.id) else { return false }
        let source = fileURL(trackID: track.id)
        do {
            try? FileManager.default.removeItem(at: destination)
            do {
                try FileManager.default.moveItem(at: source, to: destination)
            } catch {
                try FileManager.default.copyItem(at: source, to: destination)
                try FileManager.default.removeItem(at: source)
            }
            records.removeValue(forKey: track.id)
            try saveManifest()
            return true
        } catch {
            return false
        }
    }

    func cache(_ tracks: [Track]) async {
        guard AutomaticCacheNetworkMonitor.shared.canDownload else { return }
        for (index, track) in Array(tracks.prefix(3)).enumerated() {
            guard !Task.isCancelled else { return }
            guard client != nil else { return }
            if fileExists(trackID: track.id) { continue }
            if index > 0 {
                try? await Task.sleep(for: .seconds(2))
            }
            try? await download(track)
        }
        prune(retaining: Set(tracks.map(\.id)))
    }

    func maintain(candidates: [Track], force: Bool = false) async {
        guard !maintenanceInProgress, force || maintenanceDue else { return }
        maintenanceInProgress = true
        defer { maintenanceInProgress = false }
        let retained = Set(candidates.map(\.id))
        prune(retaining: retained)
        guard AutomaticCacheNetworkMonitor.shared.canDownload else { return }
        for (index, track) in candidates.prefix(Self.warmLimit).enumerated() {
            guard !Task.isCancelled else { return }
            guard client != nil else { return }
            if fileExists(trackID: track.id) { continue }
            if index > 0 {
                try? await Task.sleep(for: .seconds(5))
            }
            try? await download(track)
        }
        prune(retaining: retained)
        lastMaintenanceAt = Date()
        try? saveManifest()
    }

    private func download(_ track: Track) async throws {
        try Task.checkCancellation()
        guard !downloadsInProgress.contains(track.id) else { return }
        guard let client else { throw APIError.invalidResponse }
        downloadsInProgress.insert(track.id)
        defer { downloadsInProgress.remove(track.id) }

        let source = try await client.downloadSource(for: track)
        var request = URLRequest(url: source.url)
        request.timeoutInterval = 240
        request.setValue("audio/*,*/*;q=0.8", forHTTPHeaderField: "Accept")
        if source.url.host == client.baseURL.host {
            request.setValue("Bearer \(client.token)", forHTTPHeaderField: "Authorization")
        }
        let (temporaryURL, response) = try await NetworkSession.automaticDownloads.download(
            for: request
        )
        try Task.checkCancellation()
        guard let http = response as? HTTPURLResponse,
              (200..<300).contains(http.statusCode) else {
            throw APIError.invalidResponse
        }
        let attributes = try FileManager.default.attributesOfItem(
            atPath: temporaryURL.path
        )
        let fileSize = (attributes[.size] as? NSNumber)?.int64Value ?? 0
        guard fileSize >= 16 * 1024,
              response.expectedContentLength <= 0
                || fileSize == response.expectedContentLength else {
            throw APIError.invalidResponse
        }
        let contentType = http.value(forHTTPHeaderField: "Content-Type")?
            .lowercased() ?? ""
        guard !contentType.contains("json"), !contentType.contains("html") else {
            throw APIError.invalidResponse
        }

        let destination = fileURL(trackID: track.id)
        try? FileManager.default.removeItem(at: destination)
        try FileManager.default.moveItem(at: temporaryURL, to: destination)
        try FileManager.default.setAttributes(
            [.protectionKey: FileProtectionType.completeUntilFirstUserAuthentication],
            ofItemAtPath: destination.path
        )
        records[track.id] = AutomaticTrackRecord(
            track: track,
            cachedAt: Date(),
            lastAccessedAt: Date()
        )
        try saveManifest()
    }

    private func prune(retaining retained: Set<String>) {
        let now = Date()
        for trackID in Array(records.keys) {
            guard fileExists(trackID: trackID) else {
                records.removeValue(forKey: trackID)
                continue
            }
            guard !retained.contains(trackID),
                  let record = records[trackID],
                  now.timeIntervalSince(record.lastAccessedAt) > Self.maximumIdleAge else {
                continue
            }
            remove(trackID)
        }

        var files = records.compactMap { trackID, record -> (String, Int64, Date)? in
            let path = fileURL(trackID: trackID)
            guard let attributes = try? FileManager.default.attributesOfItem(
                atPath: path.path
            ), let size = attributes[.size] as? NSNumber else {
                return nil
            }
            return (trackID, size.int64Value, record.lastAccessedAt)
        }
        var totalBytes = files.reduce(Int64(0)) { $0 + $1.1 }
        if totalBytes > Self.maximumBytes {
            files.sort {
                let firstRetained = retained.contains($0.0)
                let secondRetained = retained.contains($1.0)
                if firstRetained != secondRetained {
                    return !firstRetained
                }
                return $0.2 < $1.2
            }
            for (trackID, size, _) in files where totalBytes > Self.targetBytes {
                remove(trackID)
                totalBytes -= size
            }
        }
        try? saveManifest()
    }

    private func remove(_ trackID: String) {
        records.removeValue(forKey: trackID)
        try? FileManager.default.removeItem(at: fileURL(trackID: trackID))
    }

    private var tracksURL: URL {
        rootURL?.appending(path: "Tracks", directoryHint: .isDirectory)
            ?? FileManager.default.temporaryDirectory
    }

    private var manifestURL: URL? {
        rootURL?.appending(path: "manifest.json")
    }

    private func fileURL(trackID: String) -> URL {
        let digest = SHA256.hash(data: Data(trackID.utf8))
            .map { String(format: "%02x", $0) }
            .joined()
        return tracksURL.appending(path: "\(digest).m4a")
    }

    private func fileExists(trackID: String) -> Bool {
        let path = fileURL(trackID: trackID)
        guard let attributes = try? FileManager.default.attributesOfItem(
            atPath: path.path
        ), let size = attributes[.size] as? NSNumber else {
            return false
        }
        return size.int64Value >= 16 * 1024
    }

    private func loadManifest() {
        guard let manifestURL,
              let data = try? Data(contentsOf: manifestURL),
              let manifest = try? JSONDecoder().decode(
                AutomaticCacheManifest.self,
                from: data
              ) else {
            records = [:]
            lastMaintenanceAt = nil
            return
        }
        records = manifest.records
        lastMaintenanceAt = manifest.lastMaintenanceAt
        for trackID in Array(records.keys) where !fileExists(trackID: trackID) {
            records.removeValue(forKey: trackID)
        }
        try? saveManifest()
    }

    private func saveManifest() throws {
        guard let manifestURL else { return }
        let manifest = AutomaticCacheManifest(
            records: records,
            lastMaintenanceAt: lastMaintenanceAt
        )
        let data = try JSONEncoder().encode(manifest)
        try data.write(
            to: manifestURL,
            options: [.atomic, .completeFileProtectionUntilFirstUserAuthentication]
        )
    }
}
