import Foundation

extension APIClient {
    func musicSearch(_ query: String) async throws -> [MusicTrack] {
        let response: MusicEnvelope<[MusicTrack]> = try await authenticatedGet(
            "/api/music/search",
            queryItems: [URLQueryItem(name: "q", value: query)]
        )
        return response.data
    }

    func musicSearchArtists(_ query: String) async throws -> [MusicArtistSummary] {
        let response: MusicEnvelope<[MusicArtistSummary]> = try await authenticatedGet(
            "/api/music/search/artists",
            queryItems: [URLQueryItem(name: "q", value: query)]
        )
        return response.data
    }

    func musicSearchAlbums(_ query: String) async throws -> [MusicRelease] {
        let response: MusicEnvelope<[MusicRelease]> = try await authenticatedGet(
            "/api/music/search/albums",
            queryItems: [URLQueryItem(name: "q", value: query)]
        )
        return response.data
    }

    func musicArtist(_ id: String) async throws -> MusicArtistDetail {
        let response: MusicEnvelope<MusicArtistDetail> = try await authenticatedGet("/api/music/artists/\(id)")
        return response.data
    }

    func musicAlbum(_ id: String) async throws -> MusicAlbumDetail {
        let response: MusicEnvelope<MusicAlbumDetail> = try await authenticatedGet("/api/music/albums/\(id)")
        return response.data
    }

    func musicSavedAlbums() async throws -> [MusicSavedAlbum] {
        let response: MusicEnvelope<[MusicSavedAlbum]> = try await authenticatedGet("/api/music/albums/library")
        return response.data
    }

    func musicSaveAlbum(_ album: MusicAlbumDetail) async throws -> MusicSavedAlbum {
        struct Body: Encodable {
            let title: String
            let artist: String
            let thumbnail: String
            let year: String
            let type: String
        }
        let response: MusicEnvelope<MusicSavedAlbum> = try await authenticatedSend(
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
        return response.data
    }

    func musicRemoveSavedAlbum(_ album: MusicSavedAlbum) async throws {
        let _: MusicEnvelope<MusicDeletePayload> = try await authenticatedRequest(
            "/api/music/albums/library/\(album.id)",
            method: "DELETE"
        )
    }

    func musicHomeFeed() async throws -> MusicHomeFeed {
        let response: MusicEnvelope<MusicHomeFeed> = try await authenticatedGet("/api/music/home")
        return response.data
    }

    func musicRecommendations(
        seedIDs: [String],
        excluding excludeIDs: [String],
        limit: Int = 15
    ) async throws -> [MusicTrack] {
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
        let response: MusicEnvelope<[MusicTrack]> = try await authenticatedSend(
            "/api/music/recommendations/context",
            method: "POST",
            body: Body(seedIDs: seedIDs, excludeIDs: excludeIDs, limit: limit)
        )
        return response.data
    }

    func musicPlaylistSuggestions(_ playlist: MusicPlaylist) async throws -> [MusicTrack] {
        let response: MusicEnvelope<[MusicTrack]> = try await authenticatedGet(
            "/api/music/playlists/\(playlist.id)/suggestions"
        )
        return response.data
    }

    func musicRadioStations(query: String? = nil) async throws -> [MusicRadioStation] {
        let queryItems = query.map { [URLQueryItem(name: "q", value: $0)] } ?? []
        let response: MusicEnvelope<[MusicRadioStation]> = try await authenticatedGet(
            "/api/music/radio/stations",
            queryItems: queryItems
        )
        return response.data
    }

    func musicHistory() async throws -> [MusicTrack] {
        let response: MusicEnvelope<[MusicTrack]> = try await authenticatedGet("/api/music/history")
        return response.data
    }

    func musicLibrary() async throws -> [MusicTrack] {
        let response: MusicEnvelope<[MusicTrack]> = try await authenticatedGet("/api/music/library")
        return response.data
    }

    func musicPlaylists() async throws -> [MusicPlaylist] {
        let response: MusicEnvelope<[MusicPlaylist]> = try await authenticatedGet("/api/music/playlists")
        return response.data
    }

    func musicCreatePlaylist(name: String, description: String) async throws -> MusicPlaylist {
        struct Body: Encodable {
            let name: String
            let description: String
        }
        let response: MusicEnvelope<MusicPlaylist> = try await authenticatedSend(
            "/api/music/playlists",
            method: "POST",
            body: Body(name: name, description: description)
        )
        return response.data
    }

    func musicRenamePlaylist(_ playlist: MusicPlaylist, name: String) async throws -> MusicPlaylist {
        struct Body: Encodable {
            let name: String
        }
        let response: MusicEnvelope<MusicPlaylist> = try await authenticatedSend(
            "/api/music/playlists/\(playlist.id)",
            method: "PATCH",
            body: Body(name: name)
        )
        return response.data
    }

    func musicDeletePlaylist(_ playlist: MusicPlaylist) async throws {
        let _: MusicEnvelope<MusicDeletePayload> = try await authenticatedRequest(
            "/api/music/playlists/\(playlist.id)",
            method: "DELETE"
        )
    }

    func musicAdd(_ track: MusicTrack, to playlist: MusicPlaylist) async throws -> MusicPlaylist {
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
        let response: MusicEnvelope<MusicPlaylist> = try await authenticatedSend(
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
        return response.data
    }

    func musicRemove(_ track: MusicTrack, from playlist: MusicPlaylist) async throws -> MusicPlaylist {
        let response: MusicEnvelope<MusicPlaylist> = try await authenticatedRequest(
            "/api/music/playlists/\(playlist.id)/tracks/\(track.id)",
            method: "DELETE"
        )
        return response.data
    }

    func musicPlayback(for track: MusicTrack) async throws -> MusicPlaybackSource {
        let response: MusicEnvelope<MusicPlaybackPayload> = try await authenticatedGet(
            "/api/music/playback-url",
            queryItems: [
                URLQueryItem(name: "id", value: track.id),
                URLQueryItem(name: "direct", value: "1"),
            ]
        )
        let payload = response.data
        guard let proxyURL = resolvedAuthenticatedURL(payload.path) else {
            throw APIError.invalidURL
        }
        let directURL = payload.directURL.flatMap(URL.init(string:))
        return MusicPlaybackSource(
            url: directURL ?? proxyURL,
            durationSeconds: payload.durationSeconds,
            fallbackURL: directURL == nil ? nil : proxyURL
        )
    }

    func musicRecord(_ track: MusicTrack, playedSeconds: Int, completed: Bool) async throws {
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
        let _: MusicEnvelope<MusicTrack> = try await authenticatedSend(
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

    func musicSetLiked(_ liked: Bool, track: MusicTrack) async throws -> MusicTrack {
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
        let response: MusicEnvelope<MusicTrack> = try await authenticatedSend(
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
        return response.data
    }
}
