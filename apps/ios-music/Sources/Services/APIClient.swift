import Foundation

struct APIClient {
    let baseURL: URL
    let token: String

    func search(_ query: String) async throws -> MusicSearchResult {
        try await get(
            "/api/music/search/smart",
            query: [URLQueryItem(name: "q", value: query)]
        )
    }

    func searchArtists(_ query: String) async throws -> [ArtistSummary] {
        try await get(
            "/api/music/search/artists",
            query: [URLQueryItem(name: "q", value: query)]
        )
    }

    func searchAlbums(_ query: String) async throws -> [MusicRelease] {
        try await get(
            "/api/music/search/albums",
            query: [URLQueryItem(name: "q", value: query)]
        )
    }

    func genres() async throws -> [String] {
        try await get("/api/music/genres")
    }

    func artist(_ id: String) async throws -> ArtistDetail {
        try await get("/api/music/artists/\(id)")
    }

    func album(_ id: String) async throws -> AlbumDetail {
        try await get("/api/music/albums/\(id)")
    }

    func savedAlbums() async throws -> [SavedAlbum] {
        try await get("/api/music/albums/library")
    }

    func save(_ album: AlbumDetail) async throws -> SavedAlbum {
        struct Body: Encodable {
            let title: String
            let artist: String
            let thumbnail: String
            let year: String
            let type: String
        }
        return try await send(
            "/api/music/albums/library/\(album.id)",
            method: "PUT",
            body: Body(
                title: album.title,
                artist: album.artist,
                thumbnail: album.thumbnail,
                year: album.year,
                type: album.type
            )
        )
    }

    func removeSavedAlbum(_ album: SavedAlbum) async throws {
        let _: DeletePayload = try await request(
            "/api/music/albums/library/\(album.id)",
            method: "DELETE"
        )
    }

    func recommendations() async throws -> [Track] {
        try await get("/api/music/recommendations")
    }

    func homeFeed(forceRefresh: Bool = false) async throws -> HomeMusicFeed {
        let query = forceRefresh
            ? [URLQueryItem(name: "refresh", value: "1")]
            : []
        return try await get("/api/music/home", query: query)
    }

    func recommendations(seedIDs: [String], excluding excludeIDs: [String], limit: Int = 15) async throws -> [Track] {
        struct Body: Encodable {
            let seedIDs: [String]
            let excludeIDs: [String]
            let limit: Int

            enum CodingKeys: String, CodingKey {
                case seedIDs = "seed_ids"
                case excludeIDs = "exclude_ids"
                case limit
            }
        }
        return try await send(
            "/api/music/recommendations/context",
            method: "POST",
            body: Body(seedIDs: seedIDs, excludeIDs: excludeIDs, limit: limit)
        )
    }

    func playlistSuggestions(_ playlist: Playlist) async throws -> [Track] {
        try await get("/api/music/playlists/\(playlist.id)/suggestions")
    }

    func radioStations(query: String? = nil) async throws -> [RadioStation] {
        let queryItems = query.map { [URLQueryItem(name: "q", value: $0)] } ?? []
        return try await get("/api/music/radio/stations", query: queryItems)
    }

    func history() async throws -> [Track] {
        try await get("/api/music/history")
    }

    func automaticCacheCandidates() async throws -> [Track] {
        try await get("/api/music/cache/candidates")
    }

    func library() async throws -> [Track] {
        try await get("/api/music/library")
    }

    func playlists() async throws -> [Playlist] {
        try await get("/api/music/playlists")
    }

    func createPlaylist(name: String, description: String = "") async throws -> Playlist {
        struct Body: Encodable { let name: String; let description: String }
        return try await send(
            "/api/music/playlists",
            method: "POST",
            body: Body(name: name, description: description)
        )
    }

    func renamePlaylist(_ playlist: Playlist, name: String) async throws -> Playlist {
        struct Body: Encodable { let name: String }
        return try await send(
            "/api/music/playlists/\(playlist.id)",
            method: "PATCH",
            body: Body(name: name)
        )
    }

    func deletePlaylist(_ playlist: Playlist) async throws {
        let _: DeletePayload = try await request(
            "/api/music/playlists/\(playlist.id)",
            method: "DELETE"
        )
    }

    func add(_ track: Track, to playlist: Playlist) async throws -> Playlist {
        struct Body: Encodable {
            let id: String
            let title: String
            let artist: String
            let thumbnail: String
            let durationSeconds: Int?

            enum CodingKeys: String, CodingKey {
                case id, title, artist, thumbnail
                case durationSeconds = "duration_seconds"
            }
        }
        return try await send(
            "/api/music/playlists/\(playlist.id)/tracks",
            method: "POST",
            body: Body(
                id: track.id,
                title: track.title,
                artist: track.artist,
                thumbnail: track.thumbnail,
                durationSeconds: track.durationSeconds
            )
        )
    }

    func remove(_ track: Track, from playlist: Playlist) async throws -> Playlist {
        try await request(
            "/api/music/playlists/\(playlist.id)/tracks/\(track.id)",
            method: "DELETE"
        )
    }

    func playback(
        for track: Track,
        prefetch: Bool = false
    ) async throws -> PlaybackSource {
        var query = [URLQueryItem(name: "id", value: track.id)]
        if prefetch {
            query.append(URLQueryItem(name: "prefetch", value: "1"))
        }
        let payload: PlaybackPayload = try await get(
            "/api/music/playback-url",
            query: query
        )
        guard let proxyURL = URL(string: payload.path, relativeTo: baseURL)?.absoluteURL else {
            throw APIError.invalidResponse
        }
        let directURL = payload.directURL.flatMap(URL.init(string:))
        let expiresAt: Date
        if directURL == nil {
            expiresAt = Date().addingTimeInterval(TimeInterval(payload.expiresIn))
        } else if let sourceExpiresAt = payload.sourceExpiresAt {
            expiresAt = Date(timeIntervalSince1970: sourceExpiresAt)
        } else {
            expiresAt = Date().addingTimeInterval(180)
        }
        return PlaybackSource(
            url: directURL ?? proxyURL,
            durationSeconds: payload.durationSeconds,
            fallbackURL: directURL == nil ? nil : proxyURL,
            expiresAt: expiresAt,
            cacheHit: payload.cacheHit
        )
    }

    func prepareServerCache(for tracks: [Track]) async throws {
        struct Body: Encodable {
            let trackIDs: [String]

            enum CodingKeys: String, CodingKey {
                case trackIDs = "track_ids"
            }
        }
        struct Result: Decodable {
            let requested: Int
            let cached: Int
            let queued: Int
        }
        let _: Result = try await send(
            "/api/music/cache/prepare",
            method: "POST",
            body: Body(trackIDs: Array(tracks.map(\.id).prefix(20)))
        )
    }

    func downloadSource(for track: Track) async throws -> PlaybackSource {
        var components = URLComponents(
            url: baseURL.appending(path: "/api/music/download"),
            resolvingAgainstBaseURL: false
        )
        components?.queryItems = [URLQueryItem(name: "id", value: track.id)]
        guard let streamURL = components?.url else {
            throw APIError.invalidResponse
        }
        return PlaybackSource(url: streamURL, durationSeconds: nil)
    }

    func record(_ track: Track, playedSeconds: Int, completed: Bool) async throws {
        struct Body: Encodable {
            let id: String
            let title: String
            let artist: String
            let thumbnail: String
            let durationSeconds: Int?
            let playedSeconds: Int
            let completed: Bool

            enum CodingKeys: String, CodingKey {
                case id, title, artist, thumbnail, completed
                case durationSeconds = "duration_seconds"
                case playedSeconds = "played_seconds"
            }
        }
        let _: Track = try await send(
            "/api/music/history",
            method: "POST",
            body: Body(
                id: track.id,
                title: track.title,
                artist: track.artist,
                thumbnail: track.thumbnail,
                durationSeconds: track.durationSeconds,
                playedSeconds: playedSeconds,
                completed: completed
            )
        )
    }

    func recordPlaybackMetric(
        eventID: UUID,
        trackID: String,
        scenario: String,
        sourceKind: String,
        sourceReadyMilliseconds: Int?,
        audibleMilliseconds: Int?,
        success: Bool,
        fallbackUsed: Bool,
        appVersion: String,
        osVersion: String
    ) async throws {
        struct Body: Encodable {
            let eventID: UUID
            let trackID: String
            let scenario: String
            let sourceKind: String
            let sourceReadyMilliseconds: Int?
            let audibleMilliseconds: Int?
            let success: Bool
            let fallbackUsed: Bool
            let appVersion: String
            let osVersion: String

            enum CodingKeys: String, CodingKey {
                case eventID = "event_id"
                case trackID = "track_id"
                case scenario
                case sourceKind = "source_kind"
                case sourceReadyMilliseconds = "source_ready_ms"
                case audibleMilliseconds = "audible_ms"
                case success
                case fallbackUsed = "fallback_used"
                case appVersion = "app_version"
                case osVersion = "os_version"
            }
        }
        struct Result: Decodable {
            let recorded: Bool
        }
        let _: Result = try await send(
            "/api/music/playback-metrics",
            method: "POST",
            body: Body(
                eventID: eventID,
                trackID: trackID,
                scenario: scenario,
                sourceKind: sourceKind,
                sourceReadyMilliseconds: sourceReadyMilliseconds,
                audibleMilliseconds: audibleMilliseconds,
                success: success,
                fallbackUsed: fallbackUsed,
                appVersion: appVersion,
                osVersion: osVersion
            )
        )
    }

    func setLiked(_ liked: Bool, track: Track) async throws -> Track {
        struct Body: Encodable {
            let liked: Bool
            let title: String
            let artist: String
            let thumbnail: String
            let durationSeconds: Int?

            enum CodingKeys: String, CodingKey {
                case liked, title, artist, thumbnail
                case durationSeconds = "duration_seconds"
            }
        }
        return try await send(
            "/api/music/library/\(track.id)",
            method: "PUT",
            body: Body(
                liked: liked,
                title: track.title,
                artist: track.artist,
                thumbnail: track.thumbnail,
                durationSeconds: track.durationSeconds
            )
        )
    }

    private func get<Value: Decodable>(_ path: String, query: [URLQueryItem] = []) async throws -> Value {
        var components = URLComponents(url: baseURL.appending(path: path), resolvingAgainstBaseURL: false)
        components?.queryItems = query.isEmpty ? nil : query
        guard let url = components?.url else { throw APIError.invalidURL }
        var request = URLRequest(url: url)
        request.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
        return try await execute(request)
    }

    private func send<Value: Decodable, Body: Encodable>(
        _ path: String,
        method: String,
        body: Body
    ) async throws -> Value {
        var request = URLRequest(url: baseURL.appending(path: path))
        request.httpMethod = method
        request.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.httpBody = try JSONEncoder().encode(body)
        return try await execute(request)
    }

    private func request<Value: Decodable>(_ path: String, method: String) async throws -> Value {
        var request = URLRequest(url: baseURL.appending(path: path))
        request.httpMethod = method
        request.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
        return try await execute(request)
    }

    private func execute<Value: Decodable>(_ request: URLRequest) async throws -> Value {
        let (data, response): (Data, URLResponse)
        do {
            (data, response) = try await NetworkSession.shared.data(for: request)
        } catch let error as URLError {
            throw APIError.network(error)
        }
        guard let http = response as? HTTPURLResponse else { throw APIError.invalidResponse }
        guard (200..<300).contains(http.statusCode) else {
            throw APIError.response(status: http.statusCode, data: data)
        }
        do {
            return try JSONDecoder().decode(APIEnvelope<Value>.self, from: data).data
        } catch {
            throw APIError.invalidResponse
        }
    }
}

enum APIError: LocalizedError {
    case invalidURL
    case invalidResponse
    case http(Int, String?)
    case network(URLError)

    static func response(status: Int, data: Data) -> APIError {
        struct ErrorEnvelope: Decodable {
            let error: String?
            let message: String?
        }
        let payload = try? JSONDecoder().decode(ErrorEnvelope.self, from: data)
        return .http(status, payload?.error ?? payload?.message)
    }

    var errorDescription: String? {
        switch self {
        case .invalidURL:
            return "The server address is invalid."
        case .invalidResponse:
            return "Home OS returned an invalid response."
        case .http(let status, let message):
            return message ?? "Home OS returned error \(status)."
        case .network(let error):
            let detail = "(URL error \(error.errorCode))"
            return switch error.code {
            case .notConnectedToInternet:
                "Your iPhone is not connected to the internet. \(detail)"
            case .cannotFindHost, .dnsLookupFailed:
                "The Home OS domain could not be found. \(detail)"
            case .cannotConnectToHost, .timedOut:
                "Home OS could not be reached. Check the server address and connection. \(detail)"
            case .serverCertificateUntrusted, .serverCertificateHasBadDate,
                 .serverCertificateHasUnknownRoot, .serverCertificateNotYetValid:
                "The Home OS certificate is not trusted by this iPhone. \(detail)"
            default:
                "\(error.localizedDescription) \(detail)"
            }
        }
    }
}
