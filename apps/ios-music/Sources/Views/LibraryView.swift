import SwiftUI

struct LibraryView: View {
    @EnvironmentObject private var session: AppSession
    @EnvironmentObject private var library: MusicLibraryStore
    @EnvironmentObject private var offlineMusic: OfflineMusicStore
    @State private var showingNewPlaylist = false
    @State private var renamingPlaylist: Playlist?
    @AppStorage("enableGlobalAmbientLights") private var enableGlobalAmbientLights = true

    var body: some View {
        NavigationStack {
            List {
                Section {
                    NavigationLink {
                        TrackCollectionView(
                            title: "Downloads",
                            subtitle: "Available offline on this iPhone",
                            tracks: offlineMusic.downloadedTracks,
                            symbol: "arrow.down.circle.fill"
                        )
                    } label: {
                        LibraryDestinationLabel(
                            title: "Downloads",
                            detail: "\(offlineMusic.downloadedTracks.count) songs",
                            symbol: "arrow.down.circle.fill",
                            colors: [.indigo, .blue]
                        )
                    }
                    NavigationLink {
                        TrackCollectionView(
                            title: "Loved Songs",
                            subtitle: "Songs you’ve marked as favourites",
                            tracks: library.likedTracks,
                            symbol: "heart.fill"
                        )
                    } label: {
                        LibraryDestinationLabel(
                            title: "Loved Songs",
                            detail: "\(library.likedTracks.count) songs",
                            symbol: "heart.fill",
                            colors: [.pink, .red]
                        )
                    }
                    NavigationLink {
                        TrackCollectionView(
                            title: "Recently Played",
                            subtitle: "Your listening history",
                            tracks: library.recentTracks,
                            symbol: "clock.fill"
                        )
                    } label: {
                        LibraryDestinationLabel(
                            title: "Recently Played",
                            detail: "\(library.recentTracks.count) songs",
                            symbol: "clock.fill",
                            colors: [.blue, .purple]
                        )
                    }
                }

                Section("Albums") {
                    if library.savedAlbums.isEmpty {
                        Text("Albums you add to your library appear here.")
                            .font(.subheadline)
                            .foregroundStyle(.secondary)
                    } else {
                        ForEach(library.savedAlbums) { album in
                            NavigationLink {
                                AlbumView(albumID: album.id)
                            } label: {
                                HStack(spacing: 14) {
                                    ReleaseArtwork(url: album.thumbnail)
                                        .frame(width: 64, height: 64)
                                    VStack(alignment: .leading, spacing: 4) {
                                        Text(album.title).font(.headline).lineLimit(1)
                                        Text([album.artist, album.year].filter { !$0.isEmpty }.joined(separator: " · "))
                                            .font(.subheadline)
                                            .foregroundStyle(.secondary)
                                            .lineLimit(1)
                                    }
                                }
                            }
                        }
                    }
                }

                Section("Playlists") {
                    if library.playlists.isEmpty {
                        Button {
                            showingNewPlaylist = true
                        } label: {
                            Label("Create Your First Playlist", systemImage: "plus.circle.fill")
                        }
                    } else {
                        ForEach(library.playlists) { playlist in
                            NavigationLink {
                                PlaylistDetailView(playlistID: playlist.id)
                            } label: {
                                HStack(spacing: 14) {
                                    PlaylistArtworkView(playlist: playlist)
                                        .frame(width: 64, height: 64)
                                    VStack(alignment: .leading, spacing: 4) {
                                        Text(playlist.name).font(.headline).lineLimit(1)
                                        HStack(spacing: 5) {
                                            Text("Playlist · \(playlist.trackCount) songs")
                                            if offlineMusic.isDownloaded(playlist) {
                                                Image(systemName: "arrow.down.circle.fill")
                                                    .foregroundStyle(Color.homeMusicRed)
                                            }
                                        }
                                            .font(.subheadline)
                                            .foregroundStyle(.secondary)
                                    }
                                }
                            }
                            .swipeActions {
                                Button(role: .destructive) {
                                    Task {
                                        offlineMusic.removeDownload(playlist)
                                        await library.delete(playlist, using: session.client)
                                    }
                                } label: {
                                    Label("Delete", systemImage: "trash")
                                }
                                Button {
                                    renamingPlaylist = playlist
                                } label: {
                                    Label("Rename", systemImage: "pencil")
                                }
                                .tint(.blue)
                            }
                        }
                    }
                }
            }
            .listStyle(.insetGrouped)
            .navigationTitle("Library")
            .toolbar {
                ToolbarItem(placement: .topBarTrailing) {
                    Button { showingNewPlaylist = true } label: {
                        Image(systemName: "plus")
                    }
                }
            }
            .overlay { if library.isLoading { ProgressView() } }
            .refreshable { await library.load(using: session.client) }
            .sheet(isPresented: $showingNewPlaylist) { NewPlaylistView() }
            .sheet(item: $renamingPlaylist) { playlist in
                RenamePlaylistView(playlist: playlist)
            }
        }
    }
}

private struct LibraryDestinationLabel: View {
    let title: String
    let detail: String
    let symbol: String
    let colors: [Color]

    var body: some View {
        HStack(spacing: 14) {
            ZStack {
                LinearGradient(colors: colors, startPoint: .topLeading, endPoint: .bottomTrailing)
                Image(systemName: symbol).font(.title2).foregroundStyle(.white)
            }
            .frame(width: 58, height: 58)
            .clipShape(RoundedRectangle(cornerRadius: 12, style: .continuous))
            VStack(alignment: .leading, spacing: 4) {
                Text(title).font(.headline)
                Text(detail).font(.subheadline).foregroundStyle(.secondary)
            }
        }
    }
}

struct TrackCollectionView: View {
    @EnvironmentObject private var player: PlayerManager
    let title: String
    let subtitle: String
    let tracks: [Track]
    let symbol: String

    var body: some View {
        List {
            Section {
                CollectionHeader(title: title, subtitle: subtitle, symbol: symbol, tracks: tracks)
                    .listRowInsets(EdgeInsets())
                    .listRowBackground(Color.clear)
            }
            ForEach(tracks) { track in
                TrackRow(track: track, context: tracks)
            }
        }
        .listStyle(.plain)
        .navigationTitle(title)
        .navigationBarTitleDisplayMode(.inline)
        .overlay {
            if tracks.isEmpty {
                ContentUnavailableView("No Songs Yet", systemImage: symbol)
            }
        }
        .task(id: tracks.map(\.id)) {
            player.prepareForLikelyPlayback(tracks)
        }
    }
}

struct PlaylistDetailView: View {
    @EnvironmentObject private var session: AppSession
    @EnvironmentObject private var player: PlayerManager
    @EnvironmentObject private var library: MusicLibraryStore
    @EnvironmentObject private var offlineMusic: OfflineMusicStore
    let playlistID: Int
    @State private var showingRename = false

    private var playlist: Playlist? { library.playlist(id: playlistID) }

    var body: some View {
        List {
            if let playlist {
                Section {
                    VStack(spacing: 18) {
                        PlaylistArtworkView(playlist: playlist)
                            .frame(width: 220, height: 220)
                            .shadow(color: .black.opacity(0.18), radius: 22, y: 12)
                        VStack(spacing: 5) {
                            Text(playlist.name).font(.title2.bold())
                            if !playlist.description.isEmpty {
                                Text(playlist.description)
                                    .font(.subheadline)
                                    .foregroundStyle(.secondary)
                                    .multilineTextAlignment(.center)
                            }
                        }
                        PlaybackButtons(tracks: playlist.tracks)
                        OfflinePlaylistStatus(playlist: playlist)
                    }
                    .frame(maxWidth: .infinity)
                    .padding(.vertical, 20)
                    .listRowInsets(EdgeInsets())
                    .listRowBackground(Color.clear)
                }
                ForEach(playlist.tracks) { track in
                    TrackRow(track: track, context: playlist.tracks)
                        .swipeActions {
                            Button(role: .destructive) {
                                Task { await library.remove(track, from: playlist, using: session.client) }
                            } label: {
                                Label("Remove", systemImage: "minus.circle")
                            }
                        }
                }
                let suggestions = library.suggestions(for: playlist)
                if !suggestions.isEmpty {
                    Section {
                        ForEach(suggestions) { track in
                            PlaylistSuggestionRow(track: track, playlist: playlist)
                        }
                    } header: {
                        HStack {
                            Label("Suggested Songs", systemImage: "sparkles")
                            Spacer()
                            Button("Refresh") {
                                Task { await library.loadSuggestions(for: playlist, using: session.client) }
                            }
                            .textCase(nil)
                        }
                    } footer: {
                        Text("Tap the play button for a 30-second preview before adding.")
                    }
                }
            }
        }
        .listStyle(.plain)
        .navigationTitle(playlist?.name ?? "Playlist")
        .navigationBarTitleDisplayMode(.inline)
        .toolbar {
            if let playlist {
                ToolbarItem(placement: .topBarTrailing) {
                    Menu {
                        Button {
                            showingRename = true
                        } label: {
                            Label("Rename Playlist", systemImage: "pencil")
                        }
                        if offlineMusic.activePlaylistIDs.contains(playlist.id) {
                            Label("Downloading…", systemImage: "arrow.down.circle")
                        } else if offlineMusic.hasDownload(playlist) {
                            Button(role: .destructive) {
                                offlineMusic.removeDownload(playlist)
                            } label: {
                                Label("Remove Download", systemImage: "trash")
                            }
                            if !offlineMusic.isDownloaded(playlist) {
                                Button {
                                    Task { await offlineMusic.download(playlist) }
                                } label: {
                                    Label("Update Download", systemImage: "arrow.clockwise")
                                }
                            }
                        } else {
                            Button {
                                Task { await offlineMusic.download(playlist) }
                            } label: {
                                Label("Download for Offline", systemImage: "arrow.down.circle")
                            }
                        }
                        Divider()
                        Button {
                            Task { await library.loadSuggestions(for: playlist, using: session.client) }
                        } label: {
                            Label("Refresh Suggestions", systemImage: "sparkles")
                        }
                    } label: {
                        Image(systemName: "ellipsis.circle")
                    }
                }
            }
        }
        .task(id: playlist?.tracks.map(\.id)) {
            guard let playlist, !playlist.tracks.isEmpty else { return }
            player.prepareForLikelyPlayback(playlist.tracks)
            player.prewarmTracks(playlist.tracks)
            await library.loadSuggestions(for: playlist, using: session.client)
        }
        .sheet(isPresented: $showingRename) {
            if let playlist { RenamePlaylistView(playlist: playlist) }
        }
    }
}

private struct OfflinePlaylistStatus: View {
    @EnvironmentObject private var offlineMusic: OfflineMusicStore
    let playlist: Playlist

    var body: some View {
        Group {
            if offlineMusic.activePlaylistIDs.contains(playlist.id) {
                VStack(spacing: 7) {
                    ProgressView(value: offlineMusic.playlistProgress[playlist.id] ?? 0)
                    Text("Downloading for offline playback…")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
            } else if offlineMusic.isDownloaded(playlist) {
                Label("Downloaded", systemImage: "arrow.down.circle.fill")
                    .font(.subheadline.weight(.medium))
                    .foregroundStyle(Color.homeMusicRed)
            } else {
                Button {
                    Task { await offlineMusic.download(playlist) }
                } label: {
                    let count = offlineMusic.downloadedTrackCount(for: playlist)
                    Label(
                        count > 0 ? "Resume Download (\(count) of \(playlist.trackCount))" : "Download",
                        systemImage: count > 0 ? "arrow.clockwise.circle" : "arrow.down.circle"
                    )
                }
                .buttonStyle(.bordered)
                .disabled(playlist.tracks.isEmpty)
            }
            if let error = offlineMusic.errorMessage {
                Text(error)
                    .font(.caption)
                    .foregroundStyle(.red)
                    .multilineTextAlignment(.center)
            }
        }
    }
}

private struct PlaylistSuggestionRow: View {
    @EnvironmentObject private var session: AppSession
    @EnvironmentObject private var library: MusicLibraryStore
    @EnvironmentObject private var player: PlayerManager
    let track: Track
    let playlist: Playlist

    private var isPreviewing: Bool { player.previewTrackID == track.id }

    var body: some View {
        HStack(spacing: 12) {
            ArtworkView(track: track)
                .frame(width: 56, height: 56)

            VStack(alignment: .leading, spacing: 4) {
                Text(track.title)
                    .font(.body.weight(.medium))
                    .lineLimit(1)
                Text(track.artist)
                    .font(.subheadline)
                    .foregroundStyle(.secondary)
                    .lineLimit(1)
            }

            Spacer(minLength: 8)

            Button {
                Task { await player.togglePreview(track) }
            } label: {
                ZStack {
                    Circle()
                        .stroke(Color.secondary.opacity(0.22), lineWidth: 2)
                    if isPreviewing {
                        Circle()
                            .trim(from: 0, to: player.previewProgress)
                            .stroke(Color.homeMusicRed, style: StrokeStyle(lineWidth: 2.5, lineCap: .round))
                            .rotationEffect(.degrees(-90))
                    }
                    Image(systemName: isPreviewing ? "stop.fill" : "play.fill")
                        .font(.caption.bold())
                        .foregroundStyle(isPreviewing ? Color.homeMusicRed : .primary)
                        .offset(x: isPreviewing ? 0 : 1)
                }
                .frame(width: 36, height: 36)
            }
            .buttonStyle(.plain)
            .accessibilityLabel(isPreviewing ? "Stop preview" : "Preview \(track.title)")

            Button {
                player.stopPreview()
                Task { await library.add(track, to: playlist, using: session.client) }
            } label: {
                Image(systemName: "plus.circle.fill")
                    .font(.title2)
                    .foregroundStyle(Color.homeMusicRed)
                    .frame(width: 36, height: 36)
            }
            .buttonStyle(.plain)
            .accessibilityLabel("Add \(track.title) to playlist")
        }
        .padding(.vertical, 4)
    }
}

private struct CollectionHeader: View {
    let title: String
    let subtitle: String
    let symbol: String
    let tracks: [Track]

    var body: some View {
        VStack(spacing: 18) {
            ZStack {
                LinearGradient(colors: [.pink, .purple], startPoint: .topLeading, endPoint: .bottomTrailing)
                Image(systemName: symbol).font(.system(size: 70)).foregroundStyle(.white)
            }
            .frame(width: 220, height: 220)
            .clipShape(RoundedRectangle(cornerRadius: 18, style: .continuous))
            .shadow(color: .black.opacity(0.18), radius: 22, y: 12)
            VStack(spacing: 4) {
                Text(title).font(.title2.bold())
                Text(subtitle).font(.subheadline).foregroundStyle(.secondary)
            }
            PlaybackButtons(tracks: tracks)
        }
        .frame(maxWidth: .infinity)
        .padding(.vertical, 20)
    }
}

private struct NewPlaylistView: View {
    @Environment(\.dismiss) private var dismiss
    @EnvironmentObject private var session: AppSession
    @EnvironmentObject private var library: MusicLibraryStore
    @State private var name = ""
    @State private var description = ""

    var body: some View {
        NavigationStack {
            Form {
                TextField("Playlist Name", text: $name)
                TextField("Description", text: $description, axis: .vertical)
                    .lineLimit(2...4)
            }
            .navigationTitle("New Playlist")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("Cancel") { dismiss() }
                }
                ToolbarItem(placement: .confirmationAction) {
                    Button("Create") {
                        Task {
                            if await library.createPlaylist(
                                name: name,
                                description: description,
                                using: session.client
                            ) != nil {
                                dismiss()
                            }
                        }
                    }
                    .disabled(name.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
                }
            }
        }
        .presentationDetents([.medium])
    }
}

private struct RenamePlaylistView: View {
    @Environment(\.dismiss) private var dismiss
    @EnvironmentObject private var session: AppSession
    @EnvironmentObject private var library: MusicLibraryStore
    @EnvironmentObject private var offlineMusic: OfflineMusicStore
    let playlist: Playlist
    @State private var name: String

    init(playlist: Playlist) {
        self.playlist = playlist
        _name = State(initialValue: playlist.name)
    }

    var body: some View {
        NavigationStack {
            Form {
                TextField("Playlist Name", text: $name)
                    .textInputAutocapitalization(.words)
            }
            .navigationTitle("Rename Playlist")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("Cancel") { dismiss() }
                }
                ToolbarItem(placement: .confirmationAction) {
                    Button("Save") {
                        Task {
                            if await library.rename(playlist, to: name, using: session.client) {
                                if let updated = library.playlist(id: playlist.id) {
                                    offlineMusic.updatePlaylistMetadata(updated)
                                }
                                dismiss()
                            }
                        }
                    }
                    .disabled(name.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
                }
            }
        }
        .presentationDetents([.medium])
    }
}
