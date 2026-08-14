import AVFoundation
import CryptoKit
import Foundation

private struct OfflineTrackRecord: Codable {
    var track: Track
    var playlistIDs: Set<Int>
    var albumIDs: Set<String>
    var individuallyDownloaded: Bool
    var downloadedAt: Date

    var isReferenced: Bool {
        individuallyDownloaded || !playlistIDs.isEmpty || !albumIDs.isEmpty
    }
}

private struct OfflineAlbumRecord: Codable {
    var album: SavedAlbum
    var trackIDs: [String]
}

private struct LegacyOfflinePlaylistRecord: Codable {
    let playlist: Playlist
    var downloadedTrackIDs: [String]?

    var trackIDs: [String] {
        downloadedTrackIDs ?? playlist.tracks.map(\.id)
    }
}

@MainActor
final class OfflineMusicStore: ObservableObject {
    @Published private(set) var playlistProgress: [Int: Double] = [:]
    @Published private(set) var albumProgress: [String: Double] = [:]
    @Published private(set) var activePlaylistIDs = Set<Int>()
    @Published private(set) var activeAlbumIDs = Set<String>()
    @Published private(set) var activeTrackIDs = Set<String>()
    @Published private(set) var errorMessage: String?
    @Published private(set) var downloadedPlaylistIDs = Set<Int>()
    @Published private(set) var downloadedTrackCounts: [Int: Int] = [:]

    private var client: APIClient?
    @Published private var trackRecords: [String: OfflineTrackRecord] = [:]
    private var playlistRecords: [Int: Playlist] = [:]
    private var albumRecords: [String: OfflineAlbumRecord] = [:]
    private var audioDownloadsInProgress = Set<String>()
    private var rootURL: URL?
    private let automaticCache = AutomaticMusicCache()

    var automaticMaintenanceDue: Bool {
        automaticCache.maintenanceDue
    }

    var downloadedTracks: [Track] {
        trackRecords.values
            .filter { fileExists(trackID: $0.track.id) }
            .sorted { $0.downloadedAt > $1.downloadedAt }
            .map(\.track)
    }

    var downloadedPlaylists: [Playlist] {
        playlistRecords.values.sorted {
            $0.name.localizedCaseInsensitiveCompare($1.name) == .orderedAscending
        }
    }

    func connect(client: APIClient?) {
        guard let client else {
            disconnect()
            return
        }
        self.client = client
        let identity = "\(client.baseURL.absoluteString)|\(client.token)"
        let digest = SHA256.hash(data: Data(identity.utf8))
            .map { String(format: "%02x", $0) }
            .joined()
        let support = FileManager.default.urls(
            for: .applicationSupportDirectory,
            in: .userDomainMask
        )[0]
        rootURL = support.appending(path: "OfflineMusic/\(digest)", directoryHint: .isDirectory)
        try? FileManager.default.createDirectory(at: tracksURL, withIntermediateDirectories: true)
        loadManifests()
        automaticCache.connect(client: client)
    }

    func disconnect() {
        client = nil
        rootURL = nil
        trackRecords = [:]
        playlistRecords = [:]
        albumRecords = [:]
        downloadedPlaylistIDs = []
        downloadedTrackCounts = [:]
        playlistProgress = [:]
        albumProgress = [:]
        activePlaylistIDs = []
        activeAlbumIDs = []
        activeTrackIDs = []
        audioDownloadsInProgress = []
        errorMessage = nil
        automaticCache.disconnect()
    }

    func isDownloaded(_ track: Track) -> Bool {
        trackRecords[track.id] != nil && fileExists(trackID: track.id)
    }

    func isDownloaded(_ playlist: Playlist) -> Bool {
        guard playlistRecords[playlist.id] != nil, !playlist.tracks.isEmpty else { return false }
        return playlist.tracks.allSatisfy { track in
            trackRecords[track.id]?.playlistIDs.contains(playlist.id) == true
                && fileExists(trackID: track.id)
        }
    }

    func isDownloaded(_ album: AlbumDetail) -> Bool {
        guard albumRecords[album.id] != nil, !album.tracks.isEmpty else { return false }
        return album.tracks.allSatisfy { track in
            trackRecords[track.id]?.albumIDs.contains(album.id) == true
                && fileExists(trackID: track.id)
        }
    }

    func hasDownload(_ playlist: Playlist) -> Bool {
        downloadedTrackCount(for: playlist) > 0
    }

    func hasDownload(_ album: AlbumDetail) -> Bool {
        downloadedTrackCount(for: album) > 0
    }

    func downloadedTrackCount(for playlist: Playlist) -> Int {
        playlist.tracks.reduce(into: 0) { count, track in
            if trackRecords[track.id]?.playlistIDs.contains(playlist.id) == true,
               fileExists(trackID: track.id) {
                count += 1
            }
        }
    }

    func downloadedTrackCount(for album: AlbumDetail) -> Int {
        album.tracks.reduce(into: 0) { count, track in
            if trackRecords[track.id]?.albumIDs.contains(album.id) == true,
               fileExists(trackID: track.id) {
                count += 1
            }
        }
    }

    func reconcile(playlists: [Playlist]) {
        for playlist in playlists where playlistRecords[playlist.id] != nil {
            playlistRecords[playlist.id] = playlist
        }
        refreshPublishedState()
        try? saveManifests()
    }

    func resumeIncompleteDownloads(playlists: [Playlist]) async {
        for playlist in playlists
        where playlistRecords[playlist.id] != nil && !isDownloaded(playlist) {
            await download(playlist)
        }

        guard let client else { return }
        for albumID in Array(albumRecords.keys) where !activeAlbumIDs.contains(albumID) {
            guard let album = try? await client.album(albumID), !isDownloaded(album) else {
                continue
            }
            await download(album)
        }
    }

    func localURL(for track: Track) -> URL? {
        let url = fileURL(trackID: track.id)
        if fileExists(trackID: track.id) {
            return url
        }
        return automaticCache.localURL(for: track)
    }

    func cacheAutomatically(_ tracks: [Track]) async {
        await automaticCache.cache(tracks)
    }

    func maintainAutomaticCache(
        candidates: [Track],
        force: Bool = false
    ) async {
        await automaticCache.maintain(candidates: candidates, force: force)
    }

    func download(_ track: Track) async {
        guard !activeTrackIDs.contains(track.id) else { return }
        activeTrackIDs.insert(track.id)
        errorMessage = nil
        defer { activeTrackIDs.remove(track.id) }
        do {
            try await ensureAudioFile(for: track)
            var record = trackRecords[track.id] ?? newRecord(for: track)
            record.track = track
            record.individuallyDownloaded = true
            trackRecords[track.id] = record
            try saveManifests()
        } catch {
            errorMessage = "Couldn’t download \(track.title): \(error.localizedDescription)"
        }
    }

    func download(_ playlist: Playlist) async {
        guard !activePlaylistIDs.contains(playlist.id) else { return }
        guard !playlist.tracks.isEmpty else {
            errorMessage = "Add songs before downloading this playlist."
            return
        }
        activePlaylistIDs.insert(playlist.id)
        playlistRecords[playlist.id] = playlist
        playlistProgress[playlist.id] = 0
        errorMessage = nil
        defer { activePlaylistIDs.remove(playlist.id) }

        var failedCount = 0
        for (index, track) in playlist.tracks.enumerated() {
            do {
                try Task.checkCancellation()
                try await ensureAudioFile(for: track)
                var record = trackRecords[track.id] ?? newRecord(for: track)
                record.track = track
                record.playlistIDs.insert(playlist.id)
                trackRecords[track.id] = record
                try saveManifests()
            } catch is CancellationError {
                refreshPublishedState()
                errorMessage = "Playlist download paused. Tap Resume Download to continue."
                return
            } catch {
                failedCount += 1
            }
            playlistProgress[playlist.id] = Double(index + 1) / Double(playlist.tracks.count)
            refreshPublishedState()
        }
        refreshPublishedState()
        if failedCount > 0 {
            errorMessage = "Downloaded \(downloadedTrackCount(for: playlist)) of \(playlist.tracks.count) songs. Tap Resume Download to retry the rest."
        }
    }

    func download(_ album: AlbumDetail) async {
        guard !activeAlbumIDs.contains(album.id) else { return }
        guard !album.tracks.isEmpty else {
            errorMessage = "This album has no downloadable songs."
            return
        }
        activeAlbumIDs.insert(album.id)
        albumRecords[album.id] = OfflineAlbumRecord(
            album: SavedAlbum(
                id: album.id,
                title: album.title,
                artist: album.artist,
                thumbnail: album.thumbnail,
                year: album.year,
                type: album.type,
                savedAt: nil
            ),
            trackIDs: album.tracks.map(\.id)
        )
        albumProgress[album.id] = 0
        errorMessage = nil
        defer { activeAlbumIDs.remove(album.id) }

        var failedCount = 0
        for (index, originalTrack) in album.tracks.enumerated() {
            let track = originalTrack.usingFallbackArtwork(album.thumbnail)
            do {
                try Task.checkCancellation()
                try await ensureAudioFile(for: track)
                var record = trackRecords[track.id] ?? newRecord(for: track)
                record.track = track
                record.albumIDs.insert(album.id)
                trackRecords[track.id] = record
                try saveManifests()
            } catch is CancellationError {
                errorMessage = "Album download paused. Tap Resume Download to continue."
                return
            } catch {
                failedCount += 1
            }
            albumProgress[album.id] = Double(index + 1) / Double(album.tracks.count)
        }
        if failedCount > 0 {
            errorMessage = "Downloaded \(downloadedTrackCount(for: album)) of \(album.tracks.count) songs. Tap Resume Download to retry the rest."
        }
    }

    func removeDownload(_ track: Track) {
        trackRecords.removeValue(forKey: track.id)
        try? FileManager.default.removeItem(at: fileURL(trackID: track.id))
        refreshPublishedState()
        try? saveManifests()
    }

    func removeDownload(_ playlist: Playlist) {
        playlistRecords.removeValue(forKey: playlist.id)
        for trackID in Array(trackRecords.keys) {
            trackRecords[trackID]?.playlistIDs.remove(playlist.id)
            removeUnreferencedTrack(trackID)
        }
        playlistProgress.removeValue(forKey: playlist.id)
        refreshPublishedState()
        try? saveManifests()
    }

    func removeDownload(_ album: AlbumDetail) {
        albumRecords.removeValue(forKey: album.id)
        for trackID in Array(trackRecords.keys) {
            trackRecords[trackID]?.albumIDs.remove(album.id)
            removeUnreferencedTrack(trackID)
        }
        albumProgress.removeValue(forKey: album.id)
        refreshPublishedState()
        try? saveManifests()
    }

    func updatePlaylistMetadata(_ playlist: Playlist) {
        guard playlistRecords[playlist.id] != nil else { return }
        playlistRecords[playlist.id] = playlist
        try? saveManifests()
    }

    private func ensureAudioFile(for track: Track) async throws {
        if fileExists(trackID: track.id) { return }
        let destination = fileURL(trackID: track.id)
        if automaticCache.promote(track, to: destination) {
            return
        }
        while audioDownloadsInProgress.contains(track.id) {
            try await Task.sleep(for: .milliseconds(150))
            if fileExists(trackID: track.id) { return }
        }
        if fileExists(trackID: track.id) { return }
        audioDownloadsInProgress.insert(track.id)
        defer { audioDownloadsInProgress.remove(track.id) }
        guard let client else { throw APIError.invalidResponse }
        var lastError: Error = APIError.invalidResponse
        for attempt in 0..<3 {
            do {
                let source = try await client.downloadSource(for: track)
                try await downloadFile(from: source, to: destination)
                return
            } catch is CancellationError {
                throw CancellationError()
            } catch {
                lastError = error
                if attempt < 2 {
                    try await Task.sleep(for: .milliseconds(500 * (1 << attempt)))
                }
            }
        }
        throw lastError
    }

    private func downloadFile(from source: PlaybackSource, to destination: URL) async throws {
        let candidateURLs = [source.url, source.fallbackURL].compactMap { $0 }
        var lastError: Error = APIError.invalidResponse
        for url in candidateURLs {
            do {
                var request = URLRequest(url: url)
                request.timeoutInterval = 240
                request.setValue("audio/*,*/*;q=0.8", forHTTPHeaderField: "Accept")
                if let client, url.host == client.baseURL.host {
                    request.setValue("Bearer \(client.token)", forHTTPHeaderField: "Authorization")
                }
                let (temporaryURL, response) = try await NetworkSession.downloads.download(for: request)
                guard let http = response as? HTTPURLResponse,
                      (200..<300).contains(http.statusCode) else {
                    throw APIError.invalidResponse
                }
                let attributes = try FileManager.default.attributesOfItem(atPath: temporaryURL.path)
                let fileSize = (attributes[.size] as? NSNumber)?.int64Value ?? 0
                guard fileSize >= 16 * 1024 else { throw APIError.invalidResponse }
                if response.expectedContentLength > 0 {
                    guard fileSize == response.expectedContentLength else {
                        throw APIError.invalidResponse
                    }
                }
                let contentType = http.value(forHTTPHeaderField: "Content-Type")?.lowercased() ?? ""
                guard !contentType.contains("json"), !contentType.contains("html") else {
                    throw APIError.invalidResponse
                }
                try? FileManager.default.removeItem(at: destination)
                try FileManager.default.moveItem(at: temporaryURL, to: destination)
                return
            } catch is CancellationError {
                throw CancellationError()
            } catch {
                lastError = error
            }
        }
        throw lastError
    }

    private func newRecord(for track: Track) -> OfflineTrackRecord {
        OfflineTrackRecord(
            track: track,
            playlistIDs: [],
            albumIDs: [],
            individuallyDownloaded: false,
            downloadedAt: Date()
        )
    }

    private func removeUnreferencedTrack(_ trackID: String) {
        guard let record = trackRecords[trackID], !record.isReferenced else { return }
        trackRecords.removeValue(forKey: trackID)
        try? FileManager.default.removeItem(at: fileURL(trackID: trackID))
    }

    private func refreshPublishedState() {
        downloadedPlaylistIDs = Set(playlistRecords.keys)
        downloadedTrackCounts = playlistRecords.mapValues { playlist in
            downloadedTrackCount(for: playlist)
        }
    }

    private var tracksURL: URL {
        rootURL?.appending(path: "Tracks", directoryHint: .isDirectory)
            ?? FileManager.default.temporaryDirectory
    }

    private var trackManifestURL: URL? { rootURL?.appending(path: "tracks.json") }
    private var playlistManifestURL: URL? { rootURL?.appending(path: "playlists-v2.json") }
    private var albumManifestURL: URL? { rootURL?.appending(path: "albums.json") }
    private var legacyManifestURL: URL? { rootURL?.appending(path: "playlists.json") }

    private func fileURL(trackID: String) -> URL {
        let digest = SHA256.hash(data: Data(trackID.utf8))
            .map { String(format: "%02x", $0) }
            .joined()
        return tracksURL.appending(path: "\(digest).m4a")
    }

    private func fileExists(trackID: String) -> Bool {
        let url = fileURL(trackID: trackID)
        guard FileManager.default.fileExists(atPath: url.path),
              let attributes = try? FileManager.default.attributesOfItem(atPath: url.path),
              let size = attributes[.size] as? NSNumber else {
            return false
        }
        return size.int64Value >= 16 * 1024
    }

    private func loadManifests() {
        trackRecords = decode([String: OfflineTrackRecord].self, from: trackManifestURL) ?? [:]
        playlistRecords = decode([Int: Playlist].self, from: playlistManifestURL) ?? [:]
        albumRecords = decode([String: OfflineAlbumRecord].self, from: albumManifestURL) ?? [:]
        if trackRecords.isEmpty, playlistRecords.isEmpty {
            migrateLegacyManifest()
        }
        for trackID in Array(trackRecords.keys) where !fileExists(trackID: trackID) {
            trackRecords.removeValue(forKey: trackID)
        }
        repairMissingTrackDurations()
        refreshPublishedState()
        try? saveManifests()
    }

    func repairMissingTrackDurations() {
        var didModify = false
        for (trackID, var record) in trackRecords {
            let fileURL = fileURL(trackID: trackID)
            if FileManager.default.fileExists(atPath: fileURL.path) {
                let asset = AVURLAsset(url: fileURL)
                let seconds = asset.duration.seconds
                if seconds.isFinite, seconds > 0 {
                    let durSec = Int(seconds.rounded())
                    if record.track.parsedDurationSeconds != Double(durSec) {
                        let min = durSec / 60
                        let sec = durSec % 60
                        let formattedDuration = String(format: "%d:%02d", min, sec)
                        let updatedTrack = Track(
                            id: record.track.id,
                            title: record.track.title,
                            artist: record.track.artist,
                            artistID: record.track.artistID,
                            thumbnail: record.track.thumbnail,
                            duration: formattedDuration,
                            durationSeconds: durSec,
                            explicit: record.track.explicit,
                            playCount: record.track.playCount,
                            liked: record.track.liked,
                            lastPlayedAt: record.track.lastPlayedAt
                        )
                        record.track = updatedTrack
                        trackRecords[trackID] = record
                        didModify = true
                    }
                }
            }
        }
        if didModify {
            try? saveManifests()
        }
    }

    private func migrateLegacyManifest() {
        guard let legacy: [Int: LegacyOfflinePlaylistRecord] = decode(
            [Int: LegacyOfflinePlaylistRecord].self,
            from: legacyManifestURL
        ) else { return }
        for (playlistID, record) in legacy {
            playlistRecords[playlistID] = record.playlist
            let downloadedIDs = Set(record.trackIDs)
            for track in record.playlist.tracks
            where downloadedIDs.contains(track.id) && fileExists(trackID: track.id) {
                var trackRecord = trackRecords[track.id] ?? newRecord(for: track)
                trackRecord.playlistIDs.insert(playlistID)
                trackRecords[track.id] = trackRecord
            }
        }
    }

    private func decode<Value: Decodable>(_ type: Value.Type, from url: URL?) -> Value? {
        guard let url, let data = try? Data(contentsOf: url) else { return nil }
        return try? JSONDecoder().decode(type, from: data)
    }

    private func saveManifests() throws {
        try encode(trackRecords, to: trackManifestURL)
        try encode(playlistRecords, to: playlistManifestURL)
        try encode(albumRecords, to: albumManifestURL)
    }

    private func encode<Value: Encodable>(_ value: Value, to url: URL?) throws {
        guard let url else { return }
        let data = try JSONEncoder().encode(value)
        try data.write(to: url, options: .atomic)
    }
}
