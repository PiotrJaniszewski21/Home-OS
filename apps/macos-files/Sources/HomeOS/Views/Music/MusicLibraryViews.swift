import SwiftUI

struct MusicTrackCollectionView: View {
    @EnvironmentObject private var player: MusicPlayerManager
    let title: String
    let subtitle: String
    let tracks: [MusicTrack]
    let symbol: String

    var body: some View {
        NavigationStack {
            List {
                collectionHeader
                    .listRowSeparator(.hidden)
                    .listRowBackground(Color.clear)
                ForEach(tracks) { track in
                    MusicTrackRow(track: track, context: tracks)
                }
            }
            .listStyle(.plain)
            .navigationTitle(title)
            .overlay {
                if tracks.isEmpty {
                    ContentUnavailableView(
                        "No Songs Yet",
                        systemImage: symbol,
                        description: Text(subtitle)
                    )
                }
            }
        }
    }

    private var collectionHeader: some View {
        HStack(alignment: .bottom, spacing: 28) {
            ZStack {
                LinearGradient(
                    colors: [.pink, .purple],
                    startPoint: .topLeading,
                    endPoint: .bottomTrailing
                )
                Image(systemName: symbol)
                    .font(.system(size: 64))
                    .foregroundStyle(.white)
            }
            .frame(width: 210, height: 210)
            .clipShape(RoundedRectangle(cornerRadius: 18, style: .continuous))
            .shadow(color: .black.opacity(0.18), radius: 20, y: 10)

            VStack(alignment: .leading, spacing: 10) {
                Text(title)
                    .font(.system(size: 38, weight: .bold))
                Text(subtitle)
                    .font(.title3)
                    .foregroundStyle(.secondary)
                Text("\(tracks.count) songs")
                    .foregroundStyle(.secondary)
                MusicPlaybackButtons(tracks: tracks)
                    .padding(.top, 8)
            }
            Spacer()
        }
        .padding(28)
    }
}

struct MusicAlbumsLibraryView: View {
    @EnvironmentObject private var library: MusicLibraryStore

    private let columns = [
        GridItem(.adaptive(minimum: 160, maximum: 210), spacing: 20)
    ]

    var body: some View {
        NavigationStack {
            ScrollView {
                if library.savedAlbums.isEmpty {
                    ContentUnavailableView(
                        "No Saved Albums",
                        systemImage: "square.stack",
                        description: Text("Albums you add to your library appear here.")
                    )
                    .frame(maxWidth: .infinity, minHeight: 440)
                } else {
                    LazyVGrid(columns: columns, alignment: .leading, spacing: 24) {
                        ForEach(library.savedAlbums) { album in
                            NavigationLink {
                                MusicAlbumView(albumID: album.id)
                            } label: {
                                MusicReleaseCard(
                                    title: album.title,
                                    subtitle: [album.artist, album.year]
                                        .filter { !$0.isEmpty }
                                        .joined(separator: " · "),
                                    artwork: album.thumbnail
                                )
                            }
                            .buttonStyle(.plain)
                        }
                    }
                    .padding(28)
                }
            }
            .navigationTitle("Albums")
        }
    }
}

struct MusicPlaylistDetailView: View {
    @EnvironmentObject private var connection: HomeOSMusicSession
    @EnvironmentObject private var library: MusicLibraryStore
    @EnvironmentObject private var player: MusicPlayerManager
    @State private var showingRename = false
    @State private var showingDeleteConfirmation = false

    let playlistID: Int
    let onDeleted: () -> Void

    private var playlist: MusicPlaylist? {
        library.playlist(id: playlistID)
    }

    var body: some View {
        NavigationStack {
            List {
                if let playlist {
                    playlistHeader(playlist)
                        .listRowSeparator(.hidden)
                        .listRowBackground(Color.clear)

                    ForEach(playlist.tracks) { track in
                        MusicTrackRow(
                            track: track,
                            context: playlist.tracks,
                            removeAction: {
                                Task {
                                    await library.remove(
                                        track,
                                        from: playlist,
                                        using: connection.client
                                    )
                                }
                            }
                        )
                    }

                    let suggestions = library.suggestions(for: playlist)
                    if !suggestions.isEmpty {
                        Section {
                            ForEach(suggestions) { track in
                                suggestionRow(track, playlist: playlist)
                            }
                        } header: {
                            HStack {
                                Label("Suggested Songs", systemImage: "sparkles")
                                Spacer()
                                Button("Refresh") {
                                    Task {
                                        await library.loadSuggestions(
                                            for: playlist,
                                            using: connection.client
                                        )
                                    }
                                }
                                .buttonStyle(.link)
                            }
                        } footer: {
                            Text("Preview starts one minute in and plays for 30 seconds.")
                        }
                    }
                }
            }
            .listStyle(.plain)
            .navigationTitle(playlist?.name ?? "Playlist")
            .toolbar {
                if playlist != nil {
                    Menu {
                        Button {
                            showingRename = true
                        } label: {
                            Label("Rename Playlist", systemImage: "pencil")
                        }
                        Button {
                            if let playlist {
                                Task {
                                    await library.loadSuggestions(
                                        for: playlist,
                                        using: connection.client
                                    )
                                }
                            }
                        } label: {
                            Label("Refresh Suggestions", systemImage: "sparkles")
                        }
                        Divider()
                        Button(role: .destructive) {
                            showingDeleteConfirmation = true
                        } label: {
                            Label("Delete Playlist", systemImage: "trash")
                        }
                    } label: {
                        Label("Playlist Actions", systemImage: "ellipsis.circle")
                    }
                }
            }
            .task(id: playlist?.tracks.map(\.id)) {
                guard let playlist, !playlist.tracks.isEmpty else { return }
                await library.loadSuggestions(for: playlist, using: connection.client)
            }
            .sheet(isPresented: $showingRename) {
                if let playlist {
                    MusicRenamePlaylistSheet(
                        playlist: playlist,
                        isPresented: $showingRename
                    )
                    .environmentObject(connection)
                    .environmentObject(library)
                }
            }
            .confirmationDialog(
                "Delete this playlist?",
                isPresented: $showingDeleteConfirmation
            ) {
                Button("Delete Playlist", role: .destructive) {
                    guard let playlist else { return }
                    Task {
                        await library.delete(playlist, using: connection.client)
                        onDeleted()
                    }
                }
                Button("Cancel", role: .cancel) {}
            } message: {
                Text("This removes the playlist from your Home OS account.")
            }
        }
    }

    private func playlistHeader(_ playlist: MusicPlaylist) -> some View {
        HStack(alignment: .bottom, spacing: 28) {
            MusicPlaylistArtworkView(playlist: playlist)
                .frame(width: 210, height: 210)
                .shadow(color: .black.opacity(0.18), radius: 20, y: 10)

            VStack(alignment: .leading, spacing: 9) {
                Text("PLAYLIST")
                    .font(.caption.bold())
                    .tracking(1.2)
                    .foregroundStyle(.secondary)
                Text(playlist.name)
                    .font(.system(size: 38, weight: .bold))
                    .lineLimit(2)
                if !playlist.description.isEmpty {
                    Text(playlist.description)
                        .font(.title3)
                        .foregroundStyle(.secondary)
                }
                Text("\(playlist.trackCount) songs")
                    .foregroundStyle(.secondary)
                MusicPlaybackButtons(tracks: playlist.tracks)
                    .padding(.top, 8)
            }
            Spacer()
        }
        .padding(28)
    }

    private func suggestionRow(
        _ track: MusicTrack,
        playlist: MusicPlaylist
    ) -> some View {
        HStack(spacing: 12) {
            MusicArtworkView(track: track)
                .frame(width: 48, height: 48)
            VStack(alignment: .leading, spacing: 3) {
                Text(track.title)
                    .font(.headline)
                    .lineLimit(1)
                Text(track.artist)
                    .font(.subheadline)
                    .foregroundStyle(.secondary)
                    .lineLimit(1)
            }
            Spacer()
            Button {
                Task { await player.togglePreview(track) }
            } label: {
                ZStack {
                    Circle()
                        .stroke(Color.secondary.opacity(0.25), lineWidth: 2)
                    if player.previewTrackID == track.id {
                        Circle()
                            .trim(from: 0, to: player.previewProgress)
                            .stroke(
                                Color.homeOSMusicAccent,
                                style: StrokeStyle(lineWidth: 2.5, lineCap: .round)
                            )
                            .rotationEffect(.degrees(-90))
                    }
                    Image(
                        systemName: player.previewTrackID == track.id
                            ? "stop.fill"
                            : "play.fill"
                    )
                    .font(.caption.bold())
                }
                .frame(width: 32, height: 32)
            }
            .buttonStyle(.borderless)
            .help(player.previewTrackID == track.id ? "Stop Preview" : "Play 30-Second Preview")

            Button {
                player.stopPreview()
                Task {
                    await library.add(track, to: playlist, using: connection.client)
                }
            } label: {
                Image(systemName: "plus.circle.fill")
                    .font(.title2)
                    .foregroundStyle(Color.homeOSMusicAccent)
            }
            .buttonStyle(.borderless)
            .help("Add to \(playlist.name)")
        }
        .padding(.vertical, 2)
    }
}
