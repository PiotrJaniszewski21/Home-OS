import Foundation

struct Track: Codable, Identifiable, Hashable {
    let id: String
    let title: String
    let artist: String
    let thumbnail: String
    let duration: String?
    let durationSeconds: Int?
    let explicit: Bool?
    let playCount: Int?
    let liked: Bool?
    let lastPlayedAt: String?

    enum CodingKeys: String, CodingKey {
        case id, title, artist, thumbnail, duration, explicit, liked
        case durationSeconds = "duration_seconds"
        case playCount = "play_count"
        case lastPlayedAt = "last_played_at"
    }
}

struct APIEnvelope<Value: Decodable>: Decodable {
    let ok: Bool
    let data: Value
    let message: String?
}

struct LoginPayload: Decodable {
    let token: String
}

struct PlaybackPayload: Decodable {
    let path: String
    let expiresIn: Int

    enum CodingKeys: String, CodingKey {
        case path
        case expiresIn = "expires_in"
    }
}
