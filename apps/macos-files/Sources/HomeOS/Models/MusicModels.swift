import Foundation

struct MusicEnvelope<Value: Decodable>: Decodable {
    let ok: Bool
    let data: Value
    let message: String?
}

struct MusicTrack: Codable, Identifiable, Hashable {
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

    func usingFallbackArtwork(_ artwork: String) -> MusicTrack {
        guard thumbnail.isEmpty, !artwork.isEmpty else { return self }
        return MusicTrack(
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

    func settingLiked(_ liked: Bool) -> MusicTrack {
        MusicTrack(
            id: id,
            title: title,
            artist: artist,
            artistID: artistID,
            thumbnail: thumbnail,
            duration: duration,
            durationSeconds: durationSeconds,
            explicit: explicit,
            playCount: playCount,
            liked: liked,
            lastPlayedAt: lastPlayedAt
        )
    }
}

struct MusicPlaybackPayload: Decodable {
    let path: String
    let directURL: String?
    let expiresIn: Int
    let durationSeconds: Double?

    enum CodingKeys: String, CodingKey {
        case path
        case directURL = "direct_url"
        case expiresIn = "expires_in"
        case durationSeconds = "duration_seconds"
    }
}

struct MusicPlaybackSource {
    let url: URL
    let durationSeconds: Double?
    let fallbackURL: URL?
}

struct MusicPlaylist: Codable, Identifiable, Hashable {
    let id: Int
    var name: String
    var description: String
    var trackCount: Int
    var artwork: [String]
    var tracks: [MusicTrack]
    let createdAt: String
    var updatedAt: String

    enum CodingKeys: String, CodingKey {
        case id, name, description, artwork, tracks
        case trackCount = "track_count"
        case createdAt = "created_at"
        case updatedAt = "updated_at"
    }
}

struct MusicDeletePayload: Decodable {
    let deleted: Bool
}

struct MusicArtistSummary: Codable, Identifiable, Hashable {
    let id: String
    let name: String
    let thumbnail: String
    let subscribers: String?
}

struct MusicRelease: Codable, Identifiable, Hashable {
    let id: String
    let title: String
    let thumbnail: String
    let year: String
    let type: String
}

struct MusicSavedAlbum: Codable, Identifiable, Hashable {
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

struct MusicHomeRelease: Codable, Identifiable, Hashable {
    let id: String
    let title: String
    let artist: String
    let thumbnail: String
    let year: String
    let type: String
}

struct MusicHomeFeed: Codable {
    let suggestedSongs: [MusicTrack]
    let suggestedAlbums: [MusicHomeRelease]
    let newReleases: [MusicHomeRelease]

    static let empty = MusicHomeFeed(
        suggestedSongs: [],
        suggestedAlbums: [],
        newReleases: []
    )

    enum CodingKeys: String, CodingKey {
        case suggestedSongs = "suggested_songs"
        case suggestedAlbums = "suggested_albums"
        case newReleases = "new_releases"
    }
}

struct MusicArtistDetail: Codable, Identifiable {
    let id: String
    let name: String
    let description: String
    let subscribers: String?
    let monthlyListeners: String?
    let thumbnail: String
    let essentials: [MusicTrack]
    let albums: [MusicRelease]
    let singles: [MusicRelease]
    let related: [MusicArtistSummary]

    enum CodingKeys: String, CodingKey {
        case id, name, description, subscribers, thumbnail, essentials, albums, singles, related
        case monthlyListeners = "monthly_listeners"
    }
}

struct MusicAlbumDetail: Codable, Identifiable {
    let id: String
    let title: String
    let artist: String
    let year: String
    let type: String
    let thumbnail: String
    let tracks: [MusicTrack]
}

struct MusicRadioStation: Codable, Identifiable, Hashable {
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
        if !tags.isEmpty {
            return tags.prefix(2).map(\.capitalized).joined(separator: " · ")
        }
        return country.isEmpty ? "Live Radio" : country
    }

    var playerTrack: MusicTrack {
        MusicTrack(
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

extension String {
    var highResolutionMusicArtworkURL: URL? {
        let pattern = #"=w(\d+)-h(\d+)([^?]*)$"#
        guard let expression = try? NSRegularExpression(pattern: pattern),
              let match = expression.firstMatch(in: self, range: NSRange(startIndex..., in: self)),
              let widthRange = Range(match.range(at: 1), in: self),
              let heightRange = Range(match.range(at: 2), in: self),
              let suffixRange = Range(match.range(at: 3), in: self),
              let width = Int(self[widthRange]),
              let height = Int(self[heightRange]),
              max(width, height) > 0 else {
            return URL(string: self)
        }
        let scale = 1200.0 / Double(max(width, height))
        guard scale > 1 else { return URL(string: self) }
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
}
