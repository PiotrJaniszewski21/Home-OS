import Foundation

@MainActor
final class MusicLibraryStore: ObservableObject {
    @Published private(set) var playlists: [Playlist] = []
    @Published private(set) var likedTracks: [Track] = []
    @Published private(set) var recentTracks: [Track] = []
    @Published private(set) var savedAlbums: [SavedAlbum] = []
    @Published private(set) var playlistSuggestions: [Int: [Track]] = [:]
    @Published var message: String?
    @Published var isLoading = false
    private var snapshotClient: APIClient?
    private var loadedSnapshotNamespaces = Set<String>()

    func load(using client: APIClient?) async {
        guard let client else { return }
        snapshotClient = client
        let namespace = HomeFeedStore.namespace(for: client)
        if loadedSnapshotNamespaces.insert(namespace).inserted,
           let snapshot = LibrarySnapshotStore.load(for: client) {
            apply(snapshot)
        }
        isLoading = playlists.isEmpty
            && likedTracks.isEmpty
            && recentTracks.isEmpty
            && savedAlbums.isEmpty
        defer { isLoading = false }
        async let playlistRequest = client.playlists()
        async let libraryRequest = client.library()
        async let historyRequest = client.history()
        async let albumRequest = client.savedAlbums()
        do {
            playlists = try await playlistRequest
            likedTracks = try await libraryRequest
            recentTracks = try await historyRequest
            savedAlbums = try await albumRequest
            persistSnapshot()
            message = nil
        } catch {
            message = error.localizedDescription
        }
    }

    func isSaved(_ albumID: String) -> Bool {
        savedAlbums.contains { $0.id == albumID }
    }

    func toggleSaved(_ album: AlbumDetail, using client: APIClient?) async {
        guard let client else { return }
        do {
            if let existing = savedAlbums.first(where: { $0.id == album.id }) {
                try await client.removeSavedAlbum(existing)
                savedAlbums.removeAll { $0.id == album.id }
                message = "Removed from Library"
            } else {
                let saved = try await client.save(album)
                savedAlbums.removeAll { $0.id == saved.id }
                savedAlbums.insert(saved, at: 0)
                message = "Added to Library"
            }
            persistSnapshot()
        } catch {
            message = error.localizedDescription
        }
    }

    func createPlaylist(name: String, description: String, using client: APIClient?) async -> Playlist? {
        guard let client else { return nil }
        do {
            let playlist = try await client.createPlaylist(name: name, description: description)
            playlists.insert(playlist, at: 0)
            message = "Playlist created"
            persistSnapshot()
            return playlist
        } catch {
            message = error.localizedDescription
            return nil
        }
    }

    func delete(_ playlist: Playlist, using client: APIClient?) async {
        guard let client else { return }
        do {
            try await client.deletePlaylist(playlist)
            playlists.removeAll { $0.id == playlist.id }
            persistSnapshot()
        } catch {
            message = error.localizedDescription
        }
    }

    func rename(_ playlist: Playlist, to name: String, using client: APIClient?) async -> Bool {
        guard let client else { return false }
        let cleanedName = name.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !cleanedName.isEmpty else { return false }
        do {
            replace(try await client.renamePlaylist(playlist, name: cleanedName))
            message = "Playlist renamed"
            persistSnapshot()
            return true
        } catch {
            message = error.localizedDescription
            return false
        }
    }

    func add(_ track: Track, to playlist: Playlist, using client: APIClient?) async {
        guard let client else { return }
        do {
            let updated = try await client.add(track, to: playlist)
            replace(updated)
            message = "Added to \(playlist.name)"
            persistSnapshot()
        } catch {
            message = error.localizedDescription
        }
    }

    func remove(_ track: Track, from playlist: Playlist, using client: APIClient?) async {
        guard let client else { return }
        do {
            let updated = try await client.remove(track, from: playlist)
            replace(updated)
            persistSnapshot()
        } catch {
            message = error.localizedDescription
        }
    }

    func loadSuggestions(for playlist: Playlist, using client: APIClient?) async {
        guard let client else { return }
        do {
            playlistSuggestions[playlist.id] = try await client.playlistSuggestions(playlist)
        } catch {
            message = error.localizedDescription
        }
    }

    func suggestions(for playlist: Playlist) -> [Track] {
        playlistSuggestions[playlist.id] ?? []
    }

    func toggleLike(_ track: Track, using client: APIClient?) async {
        guard let client else { return }
        let isLiked = likedTracks.contains { $0.id == track.id }
        do {
            let updated = try await client.setLiked(!isLiked, track: track)
            if updated.liked == true {
                likedTracks.removeAll { $0.id == updated.id }
                likedTracks.insert(updated, at: 0)
                message = "Added to Loved Songs"
            } else {
                likedTracks.removeAll { $0.id == updated.id }
                message = "Removed from Loved Songs"
            }
            persistSnapshot()
        } catch {
            message = error.localizedDescription
        }
    }

    func playlist(id: Int) -> Playlist? {
        playlists.first { $0.id == id }
    }

    func restoreOfflinePlaylists(_ offlinePlaylists: [Playlist]) {
        for playlist in offlinePlaylists where !playlists.contains(where: { $0.id == playlist.id }) {
            playlists.append(playlist)
        }
    }

    private func apply(_ snapshot: MusicLibrarySnapshot) {
        let offlineOnly = playlists.filter { playlist in
            !snapshot.playlists.contains { $0.id == playlist.id }
        }
        playlists = snapshot.playlists + offlineOnly
        likedTracks = snapshot.likedTracks
        recentTracks = snapshot.recentTracks
        savedAlbums = snapshot.savedAlbums
    }

    private func persistSnapshot() {
        guard let snapshotClient else { return }
        try? LibrarySnapshotStore.save(
            MusicLibrarySnapshot(
                playlists: playlists,
                likedTracks: likedTracks,
                recentTracks: recentTracks,
                savedAlbums: savedAlbums,
                savedAt: Date()
            ),
            for: snapshotClient
        )
    }

    private func replace(_ playlist: Playlist) {
        guard let index = playlists.firstIndex(where: { $0.id == playlist.id }) else {
            playlists.insert(playlist, at: 0)
            return
        }
        playlists[index] = playlist
        playlistSuggestions[playlist.id]?.removeAll { suggested in
            playlist.tracks.contains { $0.id == suggested.id }
        }
    }
}
