import Foundation

struct Track: Codable, Identifiable, Hashable {
    let id: String
    let title: String
    let artist: String
    let artistID: String?
    let thumbnail: String
    let duration: String?
    let durationSeconds: Int?
    let explicit: Bool?
    let playCount: Int?
    let liked: Bool?
    let lastPlayedAt: String?

    enum CodingKeys: String, CodingKey {
        case id, title, artist, thumbnail, duration, explicit, liked
        case artistID = "artist_id"
        case durationSeconds = "duration_seconds"
        case playCount = "play_count"
        case lastPlayedAt = "last_played_at"
    }
}

extension String {
    func musicArtworkURL(maximumDimension: Int) -> URL? {
        let pattern = #"=w(\d+)-h(\d+)([^?]*)$"#
        guard let expression = try? NSRegularExpression(pattern: pattern),
              let match = expression.firstMatch(
                in: self,
                range: NSRange(startIndex..., in: self)
              ),
              let widthRange = Range(match.range(at: 1), in: self),
              let heightRange = Range(match.range(at: 2), in: self),
              let suffixRange = Range(match.range(at: 3), in: self),
              let width = Int(self[widthRange]),
              let height = Int(self[heightRange]),
              max(width, height) > 0 else {
            return URL(string: self)
        }
        let requestedDimension = Double(max(64, maximumDimension))
        let scale = requestedDimension / Double(max(width, height))
        guard abs(scale - 1) > 0.01 else { return URL(string: self) }
        let upgradedWidth = max(1, Int((Double(width) * scale).rounded()))
        let upgradedHeight = max(1, Int((Double(height) * scale).rounded()))
        let replacement = "=w\(upgradedWidth)-h\(upgradedHeight)\(self[suffixRange])"
        let upgraded = expression.stringByReplacingMatches(
            in: self,
            range: NSRange(startIndex..., in: self),
            withTemplate: replacement
        )
        return URL(string: upgraded)
    }

    var highResolutionMusicArtworkURL: URL? {
        musicArtworkURL(maximumDimension: 1200)
    }
}

struct APIEnvelope<Value: Decodable>: Decodable {
    let ok: Bool
    let data: Value
    let message: String?
}

struct MusicSearchResult: Codable {
    let tracks: [Track]
    let genre: String?
    let recentReleases: [MusicRelease]
    let classics: [Track]
    let hotArtists: [ArtistSummary]

    enum CodingKeys: String, CodingKey {
        case tracks, genre, classics
        case recentReleases = "recent_releases"
        case hotArtists = "hot_artists"
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        tracks = try container.decode([Track].self, forKey: .tracks)
        genre = try container.decodeIfPresent(String.self, forKey: .genre)
        recentReleases = try container.decodeIfPresent(
            [MusicRelease].self,
            forKey: .recentReleases
        ) ?? []
        classics = try container.decodeIfPresent(
            [Track].self,
            forKey: .classics
        ) ?? []
        hotArtists = try container.decodeIfPresent(
            [ArtistSummary].self,
            forKey: .hotArtists
        ) ?? []
    }
}

struct LoginPayload: Decodable {
    let token: String
}

struct PlaybackPayload: Decodable {
    let path: String
    let directURL: String?
    let expiresIn: Int
    let durationSeconds: Double?
    let sourceExpiresAt: TimeInterval?
    let cacheHit: Bool

    enum CodingKeys: String, CodingKey {
        case path
        case directURL = "direct_url"
        case expiresIn = "expires_in"
        case durationSeconds = "duration_seconds"
        case sourceExpiresAt = "source_expires_at"
        case cacheHit = "cache_hit"
    }
}

struct PlaybackSource {
    let url: URL
    let durationSeconds: Double?
    let fallbackURL: URL?
    let expiresAt: Date?
    let cacheHit: Bool

    init(
        url: URL,
        durationSeconds: Double?,
        fallbackURL: URL? = nil,
        expiresAt: Date? = nil,
        cacheHit: Bool = false
    ) {
        self.url = url
        self.durationSeconds = durationSeconds
        self.fallbackURL = fallbackURL
        self.expiresAt = expiresAt
        self.cacheHit = cacheHit
    }

    func isUsable(for interval: TimeInterval = 60) -> Bool {
        guard let expiresAt else { return true }
        return expiresAt.timeIntervalSinceNow > interval
    }
}

struct Playlist: Codable, Identifiable, Hashable {
    let id: Int
    var name: String
    var description: String
    var trackCount: Int
    var artwork: [String]
    var tracks: [Track]
    let createdAt: String
    var updatedAt: String

    enum CodingKeys: String, CodingKey {
        case id, name, description, artwork, tracks
        case trackCount = "track_count"
        case createdAt = "created_at"
        case updatedAt = "updated_at"
    }
}

struct DeletePayload: Decodable {
    let deleted: Bool
}

struct ArtistSummary: Codable, Identifiable, Hashable {
    let id: String
    let name: String
    let thumbnail: String
    let subscribers: String?
}

struct MusicRelease: Codable, Identifiable, Hashable {
    let id: String
    let title: String
    let artist: String?
    let thumbnail: String
    let year: String
    let type: String
}

struct SavedAlbum: Codable, Identifiable, Hashable {
    let id: String
    let title: String
    let artist: String
    let thumbnail: String
    let year: String
    let type: String
    let savedAt: String?

    enum CodingKeys: String, CodingKey {
        case id, title, artist, thumbnail, year, type
        case savedAt = "saved_at"
    }
}

struct HomeMusicRelease: Codable, Identifiable, Hashable {
    let id: String
    let title: String
    let artist: String
    let thumbnail: String
    let year: String
    let type: String
}

struct HomeMusicFeed: Codable {
    let suggestedSongs: [Track]
    let suggestedAlbums: [HomeMusicRelease]
    let newReleases: [HomeMusicRelease]

    enum CodingKeys: String, CodingKey {
        case suggestedSongs = "suggested_songs"
        case suggestedAlbums = "suggested_albums"
        case newReleases = "new_releases"
    }
}

struct ArtistDetail: Codable, Identifiable {
    let id: String
    let name: String
    let description: String
    let subscribers: String?
    let monthlyListeners: String?
    let thumbnail: String
    let essentials: [Track]
    let albums: [MusicRelease]
    let singles: [MusicRelease]
    let related: [ArtistSummary]

    enum CodingKeys: String, CodingKey {
        case id, name, description, subscribers, thumbnail, essentials, albums, singles, related
        case monthlyListeners = "monthly_listeners"
    }
}

struct AlbumDetail: Codable, Identifiable {
    let id: String
    let title: String
    let artist: String
    let year: String
    let type: String
    let thumbnail: String
    let tracks: [Track]
}

extension Track {
    func usingFallbackArtwork(_ artwork: String) -> Track {
        guard thumbnail.isEmpty, !artwork.isEmpty else { return self }
        return Track(
            id: id,
            title: title,
            artist: artist,
            artistID: artistID,
            thumbnail: artwork,
            duration: duration,
            durationSeconds: durationSeconds,
            explicit: explicit,
            playCount: playCount,
            liked: liked,
            lastPlayedAt: lastPlayedAt
        )
    }
}

struct RadioStation: Codable, Identifiable, Hashable {
    let id: String
    let name: String
    let streamURL: String
    let artwork: String
    let country: String
    let countryCode: String
    let language: String
    let tags: [String]
    let codec: String
    let bitrate: Int
    let isHLS: Bool

    enum CodingKeys: String, CodingKey {
        case id, name, artwork, country, language, tags, codec, bitrate
        case streamURL = "stream_url"
        case countryCode = "country_code"
        case isHLS = "is_hls"
    }

    var subtitle: String {
        if !tags.isEmpty { return tags.prefix(2).map(\.capitalized).joined(separator: " · ") }
        if !country.isEmpty { return country }
        return "Live Radio"
    }

    var playerTrack: Track {
        Track(
            id: id,
            title: name,
            artist: subtitle,
            artistID: nil,
            thumbnail: artwork.hasPrefix("https://") ? artwork : "",
            duration: nil,
            durationSeconds: nil,
            explicit: false,
            playCount: nil,
            liked: nil,
            lastPlayedAt: nil
        )
    }
}
