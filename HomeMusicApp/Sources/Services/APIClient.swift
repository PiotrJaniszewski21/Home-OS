import Foundation

struct APIClient {
    let baseURL: URL
    let token: String

    func search(_ query: String) async throws -> [Track] {
        try await get("/api/music/search", query: [URLQueryItem(name: "q", value: query)])
    }

    func recommendations() async throws -> [Track] {
        try await get("/api/music/recommendations")
    }

    func history() async throws -> [Track] {
        try await get("/api/music/history")
    }

    func playbackURL(for track: Track) async throws -> URL {
        let payload: PlaybackPayload = try await get(
            "/api/music/playback-url",
            query: [URLQueryItem(name: "id", value: track.id)]
        )
        guard let url = URL(string: payload.path, relativeTo: baseURL)?.absoluteURL else {
            throw APIError.invalidResponse
        }
        return url
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

    private func execute<Value: Decodable>(_ request: URLRequest) async throws -> Value {
        let (data, response) = try await URLSession.shared.data(for: request)
        guard let http = response as? HTTPURLResponse else { throw APIError.invalidResponse }
        guard (200..<300).contains(http.statusCode) else {
            throw APIError.http(http.statusCode)
        }
        return try JSONDecoder().decode(APIEnvelope<Value>.self, from: data).data
    }
}

enum APIError: LocalizedError {
    case invalidURL
    case invalidResponse
    case http(Int)

    var errorDescription: String? {
        switch self {
        case .invalidURL: "The server address is invalid."
        case .invalidResponse: "Home OS returned an invalid response."
        case .http(let status): "Home OS returned error \(status)."
        }
    }
}
