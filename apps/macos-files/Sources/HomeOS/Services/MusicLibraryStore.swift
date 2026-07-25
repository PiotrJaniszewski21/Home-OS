import Foundation

@MainActor
final class MusicLibraryStore: ObservableObject {
    @Published private(set) var playlists: [MusicPlaylist] = []
    @Published private(set) var likedTracks: [MusicTrack] = []
    @Published private(set) var recentTracks: [MusicTrack] = []
    @Published private(set) var savedAlbums: [MusicSavedAlbum] = []
    @Published private(set) var playlistSuggestions: [Int: [MusicTrack]] = [:]
    @Published var message: String?
    @Published var isLoading = false

    func load(using client: APIClient?) async {
        guard let client else { return }
        isLoading = true
        defer { isLoading = false }
        async let playlists = client.musicPlaylists()
        async let likedTracks = client.musicLibrary()
        async let recentTracks = client.musicHistory()
        async let savedAlbums = client.musicSavedAlbums()
        do {
            self.playlists = try await playlists
            self.likedTracks = try await likedTracks
            self.recentTracks = try await recentTracks
            self.savedAlbums = try await savedAlbums
            message = nil
        } catch {
            message = error.localizedDescription
        }
    }

    func isSaved(_ albumID: String) -> Bool {
        savedAlbums.contains { $0.id == albumID }
    }

    func isLiked(_ track: MusicTrack) -> Bool {
        likedTracks.contains { $0.id == track.id }
    }

    func toggleSaved(_ album: MusicAlbumDetail, using client: APIClient?) async {
        guard let client else { return }
        do {
            if let existing = savedAlbums.first(where: { $0.id == album.id }) {
                try await client.musicRemoveSavedAlbum(existing)
                savedAlbums.removeAll { $0.id == album.id }
                message = "Removed from Library"
            } else {
                let saved = try await client.musicSaveAlbum(album)
                savedAlbums.removeAll { $0.id == saved.id }
                savedAlbums.insert(saved, at: 0)
                message = "Added to Library"
            }
        } catch {
            message = error.localizedDescription
        }
    }

    func createPlaylist(
        name: String,
        description: String,
        using client: APIClient?
    ) async -> MusicPlaylist? {
        guard let client else { return nil }
        do {
            let playlist = try await client.musicCreatePlaylist(name: name, description: description)
            playlists.insert(playlist, at: 0)
            message = "Playlist created"
            return playlist
        } catch {
            message = error.localizedDescription
            return nil
        }
    }

    func delete(_ playlist: MusicPlaylist, using client: APIClient?) async {
        guard let client else { return }
        do {
            try await client.musicDeletePlaylist(playlist)
            playlists.removeAll { $0.id == playlist.id }
            playlistSuggestions.removeValue(forKey: playlist.id)
        } catch {
            message = error.localizedDescription
        }
    }

    func rename(_ playlist: MusicPlaylist, to name: String, using client: APIClient?) async -> Bool {
        guard let client else { return false }
        let cleanedName = name.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !cleanedName.isEmpty else { return false }
        do {
            replace(try await client.musicRenamePlaylist(playlist, name: cleanedName))
            message = "Playlist renamed"
            return true
        } catch {
            message = error.localizedDescription
            return false
        }
    }

    func add(_ track: MusicTrack, to playlist: MusicPlaylist, using client: APIClient?) async {
        guard let client else { return }
        do {
            replace(try await client.musicAdd(track, to: playlist))
            message = "Added to \(playlist.name)"
        } catch {
            message = error.localizedDescription
        }
    }

    func remove(_ track: MusicTrack, from playlist: MusicPlaylist, using client: APIClient?) async {
        guard let client else { return }
        do {
            replace(try await client.musicRemove(track, from: playlist))
        } catch {
            message = error.localizedDescription
        }
    }

    func loadSuggestions(for playlist: MusicPlaylist, using client: APIClient?) async {
        guard let client else { return }
        do {
            playlistSuggestions[playlist.id] = try await client.musicPlaylistSuggestions(playlist)
        } catch {
            message = error.localizedDescription
        }
    }

    func suggestions(for playlist: MusicPlaylist) -> [MusicTrack] {
        playlistSuggestions[playlist.id] ?? []
    }

    @discardableResult
    func toggleLike(_ track: MusicTrack, using client: APIClient?) async -> MusicTrack? {
        guard let client else { return nil }
        do {
            let updated = try await client.musicSetLiked(!isLiked(track), track: track)
            likedTracks.removeAll { $0.id == updated.id }
            if updated.liked == true {
                likedTracks.insert(updated, at: 0)
                message = "Added to Loved Songs"
            } else {
                message = "Removed from Loved Songs"
            }
            return updated
        } catch {
            message = error.localizedDescription
            return nil
        }
    }

    func playlist(id: Int) -> MusicPlaylist? {
        playlists.first { $0.id == id }
    }

    private func replace(_ playlist: MusicPlaylist) {
        if let index = playlists.firstIndex(where: { $0.id == playlist.id }) {
            playlists[index] = playlist
        } else {
            playlists.insert(playlist, at: 0)
        }
        playlistSuggestions[playlist.id]?.removeAll { suggested in
            playlist.tracks.contains { $0.id == suggested.id }
        }
    }
}
