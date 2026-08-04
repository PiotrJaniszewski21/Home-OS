import SwiftUI

@MainActor
final class ArtistModel: ObservableObject {
    @Published var artist: ArtistDetail?
    @Published var isLoading = false
    @Published var error: String?

    func load(id: String, using client: APIClient?) async {
        guard let client else { return }
        let key = "artist:\(id)"
        if let cached = await CatalogCacheStore.shared.load(
            ArtistDetail.self,
            key: key,
            client: client,
            maximumAge: 7 * 24 * 60 * 60
        ) {
            artist = cached
        }
        isLoading = artist == nil
        defer { isLoading = false }
        do {
            artist = try await client.artist(id)
            if let artist {
                await CatalogCacheStore.shared.save(
                    artist,
                    key: key,
                    client: client
                )
            }
            error = nil
        } catch {
            self.error = error.localizedDescription
        }
    }
}

struct ArtistView: View {
    @EnvironmentObject private var session: AppSession
    @EnvironmentObject private var player: PlayerManager
    @StateObject private var model = ArtistModel()
    let artistID: String

    var body: some View {
        ScrollView {
            if let artist = model.artist {
                LazyVStack(alignment: .leading, spacing: 30) {
                    ArtistHero(artist: artist)

                    if !artist.essentials.isEmpty {
                        ArtistSectionHeader(title: "\(artist.name) Essentials")
                        VStack(spacing: 0) {
                            ForEach(artist.essentials.prefix(8)) { track in
                                TrackRow(track: track, context: artist.essentials)
                                    .padding(.vertical, 5)
                                Divider().padding(.leading, 66)
                            }
                        }
                    }

                    ReleaseSection(title: "Albums", releases: artist.albums)
                    ReleaseSection(title: "Singles & EPs", releases: artist.singles)

                    if !artist.related.isEmpty {
                        ArtistSectionHeader(title: "Similar Artists")
                        ScrollView(.horizontal, showsIndicators: false) {
                            LazyHStack(spacing: 18) {
                                ForEach(artist.related) { related in
                                    NavigationLink {
                                        ArtistView(artistID: related.id)
                                    } label: {
                                        VStack(spacing: 9) {
                                            ArtistArtwork(url: related.thumbnail)
                                                .frame(width: 132, height: 132)
                                            Text(related.name)
                                                .font(.subheadline.weight(.medium))
                                                .lineLimit(1)
                                        }
                                        .frame(width: 132)
                                    }
                                    .buttonStyle(.plain)
                                }
                            }
                        }
                    }

                    if !artist.description.isEmpty {
                        VStack(alignment: .leading, spacing: 10) {
                            ArtistSectionHeader(title: "About")
                            Text(artist.description)
                                .font(.subheadline)
                                .foregroundStyle(.secondary)
                        }
                    }
                }
                .padding(.horizontal)
                .padding(.bottom, 40)
            }
        }
        .navigationTitle(model.artist?.name ?? "Artist")
        .navigationBarTitleDisplayMode(.inline)
        .overlay { if model.isLoading { ProgressView() } }
        .overlay {
            if let error = model.error, !model.isLoading {
                LoadFailureView(message: error) {
                    Task { await model.load(id: artistID, using: session.client) }
                }
            }
        }
        .task(id: artistID) { await model.load(id: artistID, using: session.client) }
        .task(id: model.artist?.essentials.map(\.id) ?? []) {
            player.prepareForLikelyPlayback(model.artist?.essentials ?? [])
        }
    }
}

private struct ArtistHero: View {
    let artist: ArtistDetail

    var body: some View {
        VStack(spacing: 16) {
            ArtistArtwork(url: artist.thumbnail)
                .frame(width: 240, height: 240)
                .shadow(color: .black.opacity(0.22), radius: 24, y: 12)
            VStack(spacing: 5) {
                Text(artist.name).font(.largeTitle.bold()).multilineTextAlignment(.center)
                if let listeners = artist.monthlyListeners {
                    Text("\(listeners) monthly listeners")
                        .font(.subheadline)
                        .foregroundStyle(.secondary)
                }
            }
            PlaybackButtons(tracks: artist.essentials)
        }
        .frame(maxWidth: .infinity)
        .padding(.top, 12)
    }
}

private struct ArtistSectionHeader: View {
    let title: String
    var body: some View { Text(title).font(.title2.bold()) }
}

private struct ReleaseSection: View {
    let title: String
    let releases: [MusicRelease]

    var body: some View {
        if !releases.isEmpty {
            VStack(alignment: .leading, spacing: 14) {
                ArtistSectionHeader(title: title)
                ScrollView(.horizontal, showsIndicators: false) {
                    LazyHStack(spacing: 16) {
                        ForEach(releases) { release in
                            NavigationLink {
                                AlbumView(albumID: release.id)
                            } label: {
                                VStack(alignment: .leading, spacing: 8) {
                                    ReleaseArtwork(url: release.thumbnail)
                                        .frame(width: 174, height: 174)
                                    Text(release.title).font(.headline).lineLimit(1)
                                    Text([release.type, release.year].filter { !$0.isEmpty }.joined(separator: " · "))
                                        .font(.subheadline)
                                        .foregroundStyle(.secondary)
                                }
                                .frame(width: 174, alignment: .leading)
                            }
                            .buttonStyle(.plain)
                        }
                    }
                }
            }
        }
    }
}

struct ReleaseArtwork: View {
    let url: String
    var body: some View {
        RemoteArtworkView(url: url)
        .clipShape(RoundedRectangle(cornerRadius: 12, style: .continuous))
    }
}

@MainActor
final class AlbumModel: ObservableObject {
    @Published var album: AlbumDetail?
    @Published var isLoading = false
    @Published var error: String?

    func load(id: String, using client: APIClient?) async {
        guard let client else { return }
        let key = "album:\(id)"
        if let cached = await CatalogCacheStore.shared.load(
            AlbumDetail.self,
            key: key,
            client: client,
            maximumAge: 7 * 24 * 60 * 60
        ) {
            album = cached
        }
        isLoading = album == nil
        defer { isLoading = false }
        do {
            album = try await client.album(id)
            if let album {
                await CatalogCacheStore.shared.save(
                    album,
                    key: key,
                    client: client
                )
            }
            error = nil
        } catch {
            self.error = error.localizedDescription
        }
    }
}

struct AlbumView: View {
    @EnvironmentObject private var session: AppSession
    @EnvironmentObject private var player: PlayerManager
    @EnvironmentObject private var library: MusicLibraryStore
    @EnvironmentObject private var offlineMusic: OfflineMusicStore
    @StateObject private var model = AlbumModel()
    let albumID: String

    var body: some View {
        List {
            if let album = model.album {
                Section {
                    VStack(spacing: 15) {
                        ReleaseArtwork(url: album.thumbnail)
                            .frame(width: 230, height: 230)
                            .shadow(color: .black.opacity(0.2), radius: 22, y: 12)
                        VStack(spacing: 4) {
                            Text(album.title).font(.title2.bold()).multilineTextAlignment(.center)
                            Text(album.artist).foregroundStyle(Color.homeMusicRed)
                            Text([album.type, album.year].filter { !$0.isEmpty }.joined(separator: " · "))
                                .font(.subheadline).foregroundStyle(.secondary)
                        }
                        PlaybackButtons(tracks: album.tracks)
                        AlbumOfflineStatus(album: album)
                    }
                    .frame(maxWidth: .infinity)
                    .padding(.vertical, 18)
                    .listRowInsets(EdgeInsets())
                    .listRowBackground(Color.clear)
                }
                ForEach(album.tracks) { track in TrackRow(track: track, context: album.tracks) }
            }
        }
        .listStyle(.plain)
        .navigationTitle(model.album?.title ?? "Album")
        .navigationBarTitleDisplayMode(.inline)
        .toolbar {
            if let album = model.album {
                ToolbarItem(placement: .topBarTrailing) {
                    Menu {
                        Button {
                            Task { await library.toggleSaved(album, using: session.client) }
                        } label: {
                            Label(
                                library.isSaved(album.id) ? "Remove from Library" : "Add to Library",
                                systemImage: library.isSaved(album.id) ? "minus.circle" : "plus.circle"
                            )
                        }
                        if offlineMusic.activeAlbumIDs.contains(album.id) {
                            Label("Downloading…", systemImage: "arrow.down.circle")
                        } else if offlineMusic.hasDownload(album) {
                            Button(role: .destructive) {
                                offlineMusic.removeDownload(album)
                            } label: {
                                Label("Remove Download", systemImage: "trash")
                            }
                            if !offlineMusic.isDownloaded(album) {
                                Button {
                                    Task { await offlineMusic.download(album) }
                                } label: {
                                    Label("Resume Download", systemImage: "arrow.clockwise.circle")
                                }
                            }
                        } else {
                            Button {
                                Task { await offlineMusic.download(album) }
                            } label: {
                                Label("Download Album", systemImage: "arrow.down.circle")
                            }
                        }
                    } label: {
                        Image(systemName: "ellipsis.circle")
                    }
                }
            }
        }
        .overlay { if model.isLoading { ProgressView() } }
        .overlay {
            if let error = model.error, !model.isLoading {
                LoadFailureView(message: error) {
                    Task { await model.load(id: albumID, using: session.client) }
                }
            }
        }
        .task(id: albumID) { await model.load(id: albumID, using: session.client) }
        .task(id: model.album?.tracks.map(\.id) ?? []) {
            player.prepareForLikelyPlayback(model.album?.tracks ?? [])
        }
    }
}

private struct AlbumOfflineStatus: View {
    @EnvironmentObject private var offlineMusic: OfflineMusicStore
    let album: AlbumDetail

    var body: some View {
        Group {
            if offlineMusic.activeAlbumIDs.contains(album.id) {
                VStack(spacing: 7) {
                    ProgressView(value: offlineMusic.albumProgress[album.id] ?? 0)
                    Text("Downloading album for offline playback…")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
            } else if offlineMusic.isDownloaded(album) {
                Label("Downloaded", systemImage: "arrow.down.circle.fill")
                    .font(.subheadline.weight(.medium))
                    .foregroundStyle(Color.homeMusicRed)
            } else {
                Button {
                    Task { await offlineMusic.download(album) }
                } label: {
                    let count = offlineMusic.downloadedTrackCount(for: album)
                    Label(
                        count > 0 ? "Resume Download (\(count) of \(album.tracks.count))" : "Download",
                        systemImage: count > 0 ? "arrow.clockwise.circle" : "arrow.down.circle"
                    )
                }
                .buttonStyle(.bordered)
            }
        }
    }
}
