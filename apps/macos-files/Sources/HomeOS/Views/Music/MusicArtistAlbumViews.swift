import SwiftUI

@MainActor
final class MusicArtistModel: ObservableObject {
    @Published var artist: MusicArtistDetail?
    @Published var isLoading = false
    @Published var error: String?

    func load(id: String, using client: APIClient?) async {
        guard let client else { return }
        isLoading = true
        defer { isLoading = false }
        do {
            artist = try await client.musicArtist(id)
            error = nil
        } catch {
            self.error = error.localizedDescription
        }
    }
}

struct MusicArtistView: View {
    @EnvironmentObject private var connection: HomeOSMusicSession
    @StateObject private var model = MusicArtistModel()
    let artistID: String

    var body: some View {
        ScrollView {
            if let artist = model.artist {
                LazyVStack(alignment: .leading, spacing: 28) {
                    artistHero(artist)

                    if !artist.essentials.isEmpty {
                        MusicSectionHeader(title: "\(artist.name) Essentials")
                        LazyVStack(spacing: 0) {
                            ForEach(Array(artist.essentials.prefix(8))) { track in
                                MusicTrackRow(track: track, context: artist.essentials)
                                if track.id != artist.essentials.prefix(8).last?.id {
                                    Divider().padding(.leading, 64)
                                }
                            }
                        }
                    }

                    releaseSection(title: "Albums", releases: artist.albums)
                    releaseSection(title: "Singles & EPs", releases: artist.singles)

                    if !artist.related.isEmpty {
                        MusicSectionHeader(title: "Similar Artists")
                        ScrollView(.horizontal, showsIndicators: false) {
                            LazyHStack(spacing: 18) {
                                ForEach(artist.related) { related in
                                    NavigationLink {
                                        MusicArtistView(artistID: related.id)
                                    } label: {
                                        VStack(spacing: 9) {
                                            MusicRemoteArtworkView(
                                                url: related.thumbnail,
                                                placeholderSymbol: "person.crop.circle.fill"
                                            )
                                            .frame(width: 128, height: 128)
                                            .clipShape(Circle())
                                            Text(related.name)
                                                .font(.headline)
                                                .lineLimit(1)
                                        }
                                        .frame(width: 128)
                                    }
                                    .buttonStyle(.plain)
                                }
                            }
                            .padding(.vertical, 3)
                        }
                    }

                    if !artist.description.isEmpty {
                        VStack(alignment: .leading, spacing: 10) {
                            MusicSectionHeader(title: "About")
                            Text(artist.description)
                                .font(.body)
                                .foregroundStyle(.secondary)
                                .textSelection(.enabled)
                        }
                    }
                }
                .padding(28)
            }
        }
        .navigationTitle(model.artist?.name ?? "Artist")
        .overlay {
            if model.isLoading {
                ProgressView()
            } else if let error = model.error {
                MusicLoadFailureView(message: error) {
                    Task {
                        await model.load(id: artistID, using: connection.client)
                    }
                }
            }
        }
        .task(id: artistID) {
            await model.load(id: artistID, using: connection.client)
        }
    }

    private func artistHero(_ artist: MusicArtistDetail) -> some View {
        HStack(alignment: .bottom, spacing: 28) {
            MusicRemoteArtworkView(
                url: artist.thumbnail,
                placeholderSymbol: "person.crop.circle.fill"
            )
            .frame(width: 220, height: 220)
            .clipShape(Circle())
            .shadow(color: .black.opacity(0.22), radius: 22, y: 10)

            VStack(alignment: .leading, spacing: 10) {
                Text("ARTIST")
                    .font(.caption.bold())
                    .tracking(1.2)
                    .foregroundStyle(.secondary)
                Text(artist.name)
                    .font(.system(size: 42, weight: .bold))
                if let listeners = artist.monthlyListeners {
                    Text("\(listeners) monthly listeners")
                        .foregroundStyle(.secondary)
                } else if let subscribers = artist.subscribers {
                    Text("\(subscribers) subscribers")
                        .foregroundStyle(.secondary)
                }
                MusicPlaybackButtons(tracks: artist.essentials)
                    .padding(.top, 6)
            }
            Spacer()
        }
    }

    @ViewBuilder
    private func releaseSection(title: String, releases: [MusicRelease]) -> some View {
        if !releases.isEmpty {
            VStack(alignment: .leading, spacing: 14) {
                MusicSectionHeader(title: title)
                ScrollView(.horizontal, showsIndicators: false) {
                    LazyHStack(spacing: 16) {
                        ForEach(releases) { release in
                            NavigationLink {
                                MusicAlbumView(albumID: release.id)
                            } label: {
                                MusicReleaseCard(
                                    title: release.title,
                                    subtitle: [release.type, release.year]
                                        .filter { !$0.isEmpty }
                                        .joined(separator: " · "),
                                    artwork: release.thumbnail
                                )
                                .frame(width: 174)
                            }
                            .buttonStyle(.plain)
                        }
                    }
                    .padding(.vertical, 3)
                }
            }
        }
    }
}

@MainActor
final class MusicAlbumModel: ObservableObject {
    @Published var album: MusicAlbumDetail?
    @Published var isLoading = false
    @Published var error: String?

    func load(id: String, using client: APIClient?) async {
        guard let client else { return }
        isLoading = true
        defer { isLoading = false }
        do {
            let album = try await client.musicAlbum(id)
            self.album = MusicAlbumDetail(
                id: album.id,
                title: album.title,
                artist: album.artist,
                year: album.year,
                type: album.type,
                thumbnail: album.thumbnail,
                tracks: album.tracks.map { $0.usingFallbackArtwork(album.thumbnail) }
            )
            error = nil
        } catch {
            self.error = error.localizedDescription
        }
    }
}

struct MusicAlbumView: View {
    @EnvironmentObject private var connection: HomeOSMusicSession
    @EnvironmentObject private var library: MusicLibraryStore
    @StateObject private var model = MusicAlbumModel()
    let albumID: String

    var body: some View {
        List {
            if let album = model.album {
                albumHeader(album)
                    .listRowSeparator(.hidden)
                    .listRowBackground(Color.clear)

                ForEach(album.tracks) { track in
                    MusicTrackRow(track: track, context: album.tracks)
                }
            }
        }
        .listStyle(.plain)
        .navigationTitle(model.album?.title ?? "Album")
        .toolbar {
            if let album = model.album {
                Button {
                    Task {
                        await library.toggleSaved(album, using: connection.client)
                    }
                } label: {
                    Label(
                        library.isSaved(album.id) ? "Remove from Library" : "Add to Library",
                        systemImage: library.isSaved(album.id)
                            ? "checkmark.circle.fill"
                            : "plus.circle"
                    )
                }
            }
        }
        .overlay {
            if model.isLoading {
                ProgressView()
            } else if let error = model.error {
                MusicLoadFailureView(message: error) {
                    Task {
                        await model.load(id: albumID, using: connection.client)
                    }
                }
            }
        }
        .task(id: albumID) {
            await model.load(id: albumID, using: connection.client)
        }
    }

    private func albumHeader(_ album: MusicAlbumDetail) -> some View {
        HStack(alignment: .bottom, spacing: 28) {
            MusicRemoteArtworkView(url: album.thumbnail)
                .frame(width: 220, height: 220)
                .clipShape(RoundedRectangle(cornerRadius: 16, style: .continuous))
                .shadow(color: .black.opacity(0.2), radius: 20, y: 10)
            VStack(alignment: .leading, spacing: 8) {
                Text(album.type.uppercased())
                    .font(.caption.bold())
                    .tracking(1.2)
                    .foregroundStyle(.secondary)
                Text(album.title)
                    .font(.system(size: 36, weight: .bold))
                    .lineLimit(2)
                Text(album.artist)
                    .font(.title3)
                    .foregroundStyle(Color.homeOSMusicAccent)
                Text([album.type, album.year].filter { !$0.isEmpty }.joined(separator: " · "))
                    .foregroundStyle(.secondary)
                MusicPlaybackButtons(tracks: album.tracks)
                    .padding(.top, 8)
            }
            Spacer()
        }
        .padding(28)
    }
}
