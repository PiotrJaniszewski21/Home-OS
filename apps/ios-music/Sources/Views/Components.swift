import SwiftUI
import UIKit

struct PlayerArtworkView: View {
    let image: UIImage?

    var body: some View {
        GeometryReader { proxy in
            ZStack {
                if let image {
                    Image(uiImage: image)
                        .resizable()
                        .scaledToFill()
                        .blur(radius: 12)
                        .opacity(0.45)
                        .frame(width: proxy.size.width, height: proxy.size.height)
                    Image(uiImage: image)
                        .resizable()
                        .scaledToFit()
                        .frame(width: proxy.size.width, height: proxy.size.height)
                } else {
                    LinearGradient(colors: [.pink, .purple], startPoint: .topLeading, endPoint: .bottomTrailing)
                    Image(systemName: "music.note")
                        .font(.largeTitle)
                        .foregroundStyle(.white.opacity(0.9))
                }
            }
            .frame(width: proxy.size.width, height: proxy.size.height)
            .clipped()
        }
        .clipShape(RoundedRectangle(cornerRadius: 10, style: .continuous))
    }
}

struct RemoteArtworkView: View {
    let url: String
    var placeholderSymbol = "music.note"
    var placeholderColors: [Color] = [.pink, .purple]
    var maximumPixelDimension = 512
    @State private var image: UIImage?
    @State private var failed = false

    var body: some View {
        GeometryReader { proxy in
            ZStack {
                LinearGradient(
                    colors: placeholderColors,
                    startPoint: .topLeading,
                    endPoint: .bottomTrailing
                )
                if let image {
                    ZStack {
                        Image(uiImage: image)
                            .resizable()
                            .scaledToFill()
                            .frame(width: proxy.size.width, height: proxy.size.height)
                            .blur(radius: 14)
                            .opacity(0.48)
                        Image(uiImage: image)
                            .resizable()
                            .scaledToFit()
                            .frame(width: proxy.size.width, height: proxy.size.height)
                    }
                } else if failed || url.musicArtworkURL(maximumDimension: maximumPixelDimension) == nil {
                    Image(systemName: placeholderSymbol)
                        .font(.largeTitle)
                        .foregroundStyle(.white.opacity(0.9))
                } else {
                    ProgressView()
                        .tint(.white)
                }
            }
            .frame(width: proxy.size.width, height: proxy.size.height)
            .clipped()
        }
        .task(id: url) {
            image = nil
            failed = false
            guard let artworkURL = url.musicArtworkURL(
                maximumDimension: maximumPixelDimension
            ) else {
                failed = true
                return
            }
            image = await ArtworkCacheStore.shared.image(for: artworkURL)
            failed = image == nil
        }
    }
}

struct PlaybackButtons: View {
    @EnvironmentObject private var player: PlayerManager
    let tracks: [Track]

    var body: some View {
        HStack(spacing: 12) {
            playbackButton("Play", systemImage: "play.fill") {
                guard let first = tracks.first else { return }
                Task { await player.play(first, from: tracks) }
            }
            playbackButton("Shuffle", systemImage: "shuffle") {
                let shuffled = tracks.shuffled()
                guard let first = shuffled.first else { return }
                Task { await player.play(first, from: shuffled) }
            }
        }
        .padding(.horizontal, 16)
        .disabled(tracks.isEmpty)
        .opacity(tracks.isEmpty ? 0.45 : 1)
    }

    private func playbackButton(
        _ title: String,
        systemImage: String,
        action: @escaping () -> Void
    ) -> some View {
        Button(action: action) {
            Label(title, systemImage: systemImage)
                .font(.headline)
                .foregroundStyle(Color.homeMusicRed)
                .frame(maxWidth: .infinity)
                .frame(height: 48)
                .background(Color(uiColor: .secondarySystemGroupedBackground))
                .clipShape(RoundedRectangle(cornerRadius: 12, style: .continuous))
        }
        .buttonStyle(.plain)
    }
}

struct LoadFailureView: View {
    let message: String
    let retry: () -> Void

    var body: some View {
        ContentUnavailableView {
            Label("Couldn’t Load Content", systemImage: "exclamationmark.triangle")
        } description: {
            Text(message)
        } actions: {
            Button("Try Again", action: retry).buttonStyle(.borderedProminent)
        }
    }
}

struct ArtworkView: View {
    let track: Track

    var body: some View {
        RemoteArtworkView(url: track.thumbnail)
        .clipShape(RoundedRectangle(cornerRadius: 10, style: .continuous))
        .overlay {
            RoundedRectangle(cornerRadius: 10, style: .continuous)
                .stroke(.white.opacity(0.12), lineWidth: 0.5)
        }
    }
}

struct PlaylistArtworkView: View {
    let playlist: Playlist

    var body: some View {
        GeometryReader { proxy in
            let cellWidth = max((proxy.size.width - 1) / 2, 0)
            let cellHeight = max((proxy.size.height - 1) / 2, 0)
            VStack(spacing: 1) {
                ForEach(0..<2, id: \.self) { row in
                    HStack(spacing: 1) {
                        ForEach(0..<2, id: \.self) { column in
                            let index = row * 2 + column
                            if playlist.artwork.indices.contains(index) {
                                RemoteArtworkView(url: playlist.artwork[index])
                                    .frame(width: cellWidth, height: cellHeight)
                            } else {
                                ZStack {
                                    Color.secondary.opacity(0.1)
                                    Image(systemName: "music.note").foregroundStyle(.secondary)
                                }
                                .frame(width: cellWidth, height: cellHeight)
                            }
                        }
                    }
                }
            }
            .frame(width: proxy.size.width, height: proxy.size.height)
        }
        .clipShape(RoundedRectangle(cornerRadius: 12, style: .continuous))
    }
}

struct TrackRow: View {
    @EnvironmentObject private var player: PlayerManager
    @EnvironmentObject private var session: AppSession
    @EnvironmentObject private var library: MusicLibraryStore
    @EnvironmentObject private var offlineMusic: OfflineMusicStore
    @State private var showingNewPlaylist = false
    @State private var newPlaylistName = ""
    let track: Track
    let context: [Track]
    var isSearch: Bool = false

    var body: some View {
        HStack(spacing: 12) {
            Button {
                Task {
                    if isSearch {
                        await player.playFromSearch(track)
                    } else {
                        await player.play(track, from: context)
                    }
                }
            } label: {
                HStack(spacing: 12) {
                ArtworkView(track: track).frame(width: 54, height: 54)
                VStack(alignment: .leading, spacing: 3) {
                    HStack(spacing: 5) {
                        Text(track.title).font(.body).lineLimit(1)
                        if track.explicit == true { Text("E").font(.caption2.bold()).foregroundStyle(.secondary) }
                    }
                    Text(track.artist).font(.subheadline).foregroundStyle(.secondary).lineLimit(1)
                }
                Spacer()
                if offlineMusic.activeTrackIDs.contains(track.id) {
                    ProgressView()
                        .controlSize(.small)
                        .accessibilityLabel("Downloading")
                } else if offlineMusic.isDownloaded(track) {
                    Image(systemName: "arrow.down.circle.fill")
                        .foregroundStyle(Color.homeMusicRed)
                        .accessibilityLabel("Downloaded")
                }
                }
                .contentShape(Rectangle())
            }
            .buttonStyle(.plain)
            Menu {
                if offlineMusic.activeTrackIDs.contains(track.id) {
                    Label("Downloading…", systemImage: "arrow.down.circle")
                } else if offlineMusic.isDownloaded(track) {
                    Button(role: .destructive) {
                        offlineMusic.removeDownload(track)
                    } label: {
                        Label("Remove Download", systemImage: "trash")
                    }
                } else {
                    Button {
                        Task { await offlineMusic.download(track) }
                    } label: {
                        Label("Download", systemImage: "arrow.down.circle")
                    }
                }
                Divider()
                Button {
                    Task { await library.toggleLike(track, using: session.client) }
                } label: {
                    Label("Love", systemImage: "heart")
                }
                Button { player.playNext(track) } label: {
                    Label("Play Next", systemImage: "text.insert")
                }
                Button { player.playLater(track) } label: {
                    Label("Play Last", systemImage: "text.append")
                }
                Menu("Add to Playlist") {
                    ForEach(library.playlists) { playlist in
                        Button(playlist.name) {
                            Task { await library.add(track, to: playlist, using: session.client) }
                        }
                    }
                    if !library.playlists.isEmpty { Divider() }
                    Button {
                        newPlaylistName = ""
                        showingNewPlaylist = true
                    } label: {
                        Label("New Playlist…", systemImage: "plus")
                    }
                }
            } label: {
                Image(systemName: "ellipsis")
                    .frame(width: 34, height: 44)
                    .foregroundStyle(.secondary)
            }
        }
        .alert("New Playlist", isPresented: $showingNewPlaylist) {
            TextField("Playlist Name", text: $newPlaylistName)
            Button("Cancel", role: .cancel) {}
            Button("Create") {
                let name = newPlaylistName.trimmingCharacters(in: .whitespacesAndNewlines)
                guard !name.isEmpty else { return }
                Task {
                    if let playlist = await library.createPlaylist(
                        name: name,
                        description: "",
                        using: session.client
                    ) {
                        await library.add(track, to: playlist, using: session.client)
                    }
                }
            }
        } message: {
            Text("Create a playlist and add \"\(track.title)\" to it.")
        }
    }
}

struct MiniPlayerView: View {
    @EnvironmentObject private var player: PlayerManager
    let open: () -> Void

    var body: some View {
        VStack(spacing: 0) {
            GeometryReader { proxy in
                Rectangle()
                    .fill(Color.homeMusicRed)
                    .frame(width: proxy.size.width * min(player.elapsed / max(player.duration, 1), 1))
            }
            .frame(height: 2)
            HStack(spacing: 12) {
                if let track = player.currentTrack {
                    Button(action: open) {
                        HStack(spacing: 12) {
                            ArtworkView(track: track).frame(width: 46, height: 46)
                            VStack(alignment: .leading, spacing: 2) {
                                Text(track.title).font(.subheadline.weight(.medium)).lineLimit(1)
                                Text(track.artist).font(.caption).foregroundStyle(.secondary).lineLimit(1)
                            }
                            Spacer(minLength: 0)
                        }
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .contentShape(Rectangle())
                    }
                    .buttonStyle(.plain)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    Button(action: player.togglePlayback) {
                        if player.isBuffering {
                            ProgressView().controlSize(.small)
                        } else {
                            Image(systemName: player.isPlaying ? "pause.fill" : "play.fill").font(.title3)
                        }
                    }
                    Button(action: player.playNext) { Image(systemName: "forward.fill").font(.title3) }
                }
            }
            .padding(.horizontal, 14).padding(.vertical, 8)
            .background(.ultraThinMaterial)
        }
        .clipShape(RoundedRectangle(cornerRadius: 16, style: .continuous))
        .overlay {
            RoundedRectangle(cornerRadius: 16, style: .continuous)
                .stroke(.white.opacity(0.15), lineWidth: 0.5)
        }
        .padding(.horizontal, 8)
        .padding(.bottom, 4)
    }
}
