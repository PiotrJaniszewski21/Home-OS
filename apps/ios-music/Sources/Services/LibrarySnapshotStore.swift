import Foundation

struct MusicLibrarySnapshot: Codable {
    let playlists: [Playlist]
    let likedTracks: [Track]
    let recentTracks: [Track]
    let savedAlbums: [SavedAlbum]
    let savedAt: Date
}

enum LibrarySnapshotStore {
    static func load(for client: APIClient) -> MusicLibrarySnapshot? {
        guard let data = try? Data(contentsOf: fileURL(for: client)) else {
            return nil
        }
        return try? JSONDecoder().decode(MusicLibrarySnapshot.self, from: data)
    }

    static func save(
        _ snapshot: MusicLibrarySnapshot,
        for client: APIClient
    ) throws {
        let directory = try cacheDirectory()
        let data = try JSONEncoder().encode(snapshot)
        try data.write(
            to: directory.appending(path: filename(for: client)),
            options: [.atomic, .completeFileProtectionUntilFirstUserAuthentication]
        )
    }

    private static func fileURL(for client: APIClient) -> URL {
        let directory = (
            try? cacheDirectory()
        ) ?? FileManager.default.temporaryDirectory
        return directory.appending(path: filename(for: client))
    }

    private static func cacheDirectory() throws -> URL {
        let base = try FileManager.default.url(
            for: .applicationSupportDirectory,
            in: .userDomainMask,
            appropriateFor: nil,
            create: true
        )
        let directory = base.appending(
            path: "HomeMusic/Library",
            directoryHint: .isDirectory
        )
        try FileManager.default.createDirectory(
            at: directory,
            withIntermediateDirectories: true
        )
        return directory
    }

    private static func filename(for client: APIClient) -> String {
        "\(HomeFeedStore.namespace(for: client)).json"
    }
}
