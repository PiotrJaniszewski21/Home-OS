import AppKit
import SwiftUI

extension Color {
    static let homeOSMusicAccent = Color(red: 0.95, green: 0.18, blue: 0.36)
}

struct MusicRemoteArtworkView: View {
    let url: String
    var placeholderSymbol = "music.note"
    var placeholderColors: [Color] = [.pink, .purple]

    var body: some View {
        GeometryReader { proxy in
            ZStack {
                LinearGradient(
                    colors: placeholderColors,
                    startPoint: .topLeading,
                    endPoint: .bottomTrailing
                )
                AsyncImage(url: url.highResolutionMusicArtworkURL) { phase in
                    if let image = phase.image {
                        ZStack {
                            image
                                .resizable()
                                .scaledToFill()
                                .frame(width: proxy.size.width, height: proxy.size.height)
                                .blur(radius: 14)
                                .opacity(0.45)
                            image
                                .resizable()
                                .scaledToFit()
                                .frame(width: proxy.size.width, height: proxy.size.height)
                        }
                    } else if phase.error != nil || url.highResolutionMusicArtworkURL == nil {
                        Image(systemName: placeholderSymbol)
                            .font(.largeTitle)
                            .foregroundStyle(.white.opacity(0.9))
                    } else {
                        ProgressView().controlSize(.small)
                    }
                }
            }
            .frame(width: proxy.size.width, height: proxy.size.height)
            .clipped()
        }
    }
}

struct MusicArtworkView: View {
    let track: MusicTrack

    var body: some View {
        MusicRemoteArtworkView(url: track.thumbnail)
            .clipShape(RoundedRectangle(cornerRadius: 8, style: .continuous))
            .overlay {
                RoundedRectangle(cornerRadius: 8, style: .continuous)
                    .stroke(.white.opacity(0.12), lineWidth: 0.5)
            }
    }
}

struct MusicPlayerArtworkView: View {
    let image: NSImage?

    var body: some View {
        GeometryReader { proxy in
            ZStack {
                if let image {
                    Image(nsImage: image)
                        .resizable()
                        .scaledToFill()
                        .blur(radius: 14)
                        .opacity(0.42)
                        .frame(width: proxy.size.width, height: proxy.size.height)
                    Image(nsImage: image)
                        .resizable()
                        .scaledToFit()
                        .frame(width: proxy.size.width, height: proxy.size.height)
                } else {
                    LinearGradient(
                        colors: [.pink, .purple],
                        startPoint: .topLeading,
                        endPoint: .bottomTrailing
                    )
                    Image(systemName: "music.note")
                        .font(.largeTitle)
                        .foregroundStyle(.white.opacity(0.9))
                }
            }
            .frame(width: proxy.size.width, height: proxy.size.height)
            .clipped()
        }
        .clipShape(RoundedRectangle(cornerRadius: 12, style: .continuous))
    }
}

struct MusicPlaylistArtworkView: View {
    let playlist: MusicPlaylist

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
                                MusicRemoteArtworkView(url: playlist.artwork[index])
                                    .frame(width: cellWidth, height: cellHeight)
                            } else {
                                ZStack {
                                    Color.secondary.opacity(0.12)
                                    Image(systemName: "music.note")
                                        .foregroundStyle(.secondary)
                                }
                                .frame(width: cellWidth, height: cellHeight)
                            }
                        }
                    }
                }
            }
        }
        .clipShape(RoundedRectangle(cornerRadius: 10, style: .continuous))
    }
}

struct MusicTrackRow: View {
    @EnvironmentObject private var connection: HomeOSMusicSession
    @EnvironmentObject private var library: MusicLibraryStore
    @EnvironmentObject private var player: MusicPlayerManager
    @State private var showingNewPlaylist = false
    @State private var newPlaylistName = ""

    let track: MusicTrack
    let context: [MusicTrack]
    var removeAction: (() -> Void)?

    var body: some View {
        HStack(spacing: 12) {
            Button {
                Task { await player.play(track, from: context) }
            } label: {
                HStack(spacing: 12) {
                    MusicArtworkView(track: track)
                        .frame(width: 46, height: 46)
                    VStack(alignment: .leading, spacing: 3) {
                        HStack(spacing: 5) {
                            Text(track.title)
                                .lineLimit(1)
                            if track.explicit == true {
                                Text("E")
                                    .font(.caption2.bold())
                                    .foregroundStyle(.secondary)
                            }
                        }
                        Text(track.artist)
                            .font(.subheadline)
                            .foregroundStyle(.secondary)
                            .lineLimit(1)
                    }
                    Spacer(minLength: 12)
                    if library.isLiked(track) {
                        Image(systemName: "heart.fill")
                            .font(.caption)
                            .foregroundStyle(Color.homeOSMusicAccent)
                    }
                    if let duration = track.duration, !duration.isEmpty {
                        Text(duration)
                            .font(.caption.monospacedDigit())
                            .foregroundStyle(.tertiary)
                    }
                }
                .contentShape(Rectangle())
            }
            .buttonStyle(.plain)

            Menu {
                trackActions
            } label: {
                Image(systemName: "ellipsis")
                    .frame(width: 26, height: 30)
                    .contentShape(Rectangle())
            }
            .menuStyle(.borderlessButton)
            .fixedSize()
        }
        .padding(.vertical, 3)
        .contextMenu {
            trackActions
        }
        .sheet(isPresented: $showingNewPlaylist) {
            MusicNewPlaylistSheet(
                initialTrack: track,
                isPresented: $showingNewPlaylist
            )
            .environmentObject(connection)
            .environmentObject(library)
        }
    }

    @ViewBuilder
    private var trackActions: some View {
        Button {
            Task {
                if let updated = await library.toggleLike(track, using: connection.client) {
                    player.updateCurrentTrack(updated)
                }
            }
        } label: {
            Label(
                library.isLiked(track) ? "Remove Love" : "Love",
                systemImage: library.isLiked(track) ? "heart.slash" : "heart"
            )
        }
        Button {
            player.playNext(track)
        } label: {
            Label("Play Next", systemImage: "text.insert")
        }
        Button {
            player.playLater(track)
        } label: {
            Label("Play Last", systemImage: "text.append")
        }
        Menu("Add to Playlist") {
            ForEach(library.playlists) { playlist in
                Button(playlist.name) {
                    Task {
                        await library.add(track, to: playlist, using: connection.client)
                    }
                }
            }
            if !library.playlists.isEmpty {
                Divider()
            }
            Button {
                showingNewPlaylist = true
            } label: {
                Label("New Playlist…", systemImage: "plus")
            }
        }
        if let removeAction {
            Divider()
            Button(role: .destructive, action: removeAction) {
                Label("Remove from Playlist", systemImage: "minus.circle")
            }
        }
    }
}

struct MusicPlaybackButtons: View {
    @EnvironmentObject private var player: MusicPlayerManager
    let tracks: [MusicTrack]

    var body: some View {
        HStack(spacing: 10) {
            Button {
                guard let first = tracks.first else { return }
                Task { await player.play(first, from: tracks) }
            } label: {
                Label("Play", systemImage: "play.fill")
                    .frame(minWidth: 88)
            }
            .buttonStyle(.borderedProminent)
            .tint(Color.homeOSMusicAccent)

            Button {
                Task { await player.playShuffled(tracks) }
            } label: {
                Label("Shuffle", systemImage: "shuffle")
                    .frame(minWidth: 88)
            }
            .buttonStyle(.bordered)
        }
        .disabled(tracks.isEmpty)
    }
}

struct MusicSectionHeader: View {
    let title: String
    var subtitle: String?

    var body: some View {
        VStack(alignment: .leading, spacing: 3) {
            Text(title)
                .font(.title2.bold())
            if let subtitle {
                Text(subtitle)
                    .font(.subheadline)
                    .foregroundStyle(.secondary)
            }
        }
    }
}

struct MusicLoadFailureView: View {
    let message: String
    let retry: () -> Void

    var body: some View {
        ContentUnavailableView {
            Label("Couldn’t Load Music", systemImage: "exclamationmark.triangle")
        } description: {
            Text(message)
        } actions: {
            Button("Try Again", action: retry)
                .buttonStyle(.borderedProminent)
        }
    }
}

struct MusicReleaseCard: View {
    let title: String
    let subtitle: String
    let artwork: String

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            MusicRemoteArtworkView(url: artwork)
                .aspectRatio(1, contentMode: .fit)
                .clipShape(RoundedRectangle(cornerRadius: 12, style: .continuous))
                .shadow(color: .black.opacity(0.12), radius: 8, y: 4)
            Text(title)
                .font(.headline)
                .lineLimit(1)
            Text(subtitle)
                .font(.subheadline)
                .foregroundStyle(.secondary)
                .lineLimit(1)
        }
    }
}

struct MusicNewPlaylistSheet: View {
    @EnvironmentObject private var connection: HomeOSMusicSession
    @EnvironmentObject private var library: MusicLibraryStore
    @Binding var isPresented: Bool
    @State private var name = ""
    @State private var description = ""

    let initialTrack: MusicTrack?

    init(initialTrack: MusicTrack? = nil, isPresented: Binding<Bool>) {
        self.initialTrack = initialTrack
        _isPresented = isPresented
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 18) {
            Text("New Playlist")
                .font(.title2.bold())
            Form {
                TextField("Name", text: $name)
                TextField("Description", text: $description)
            }
            HStack {
                Spacer()
                Button("Cancel") {
                    isPresented = false
                }
                Button("Create") {
                    Task {
                        guard let playlist = await library.createPlaylist(
                            name: name,
                            description: description,
                            using: connection.client
                        ) else {
                            return
                        }
                        if let initialTrack {
                            await library.add(initialTrack, to: playlist, using: connection.client)
                        }
                        isPresented = false
                    }
                }
                .buttonStyle(.borderedProminent)
                .disabled(name.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
            }
        }
        .padding(24)
        .frame(width: 430)
    }
}

struct MusicRenamePlaylistSheet: View {
    @EnvironmentObject private var connection: HomeOSMusicSession
    @EnvironmentObject private var library: MusicLibraryStore
    @Binding var isPresented: Bool
    @State private var name: String

    let playlist: MusicPlaylist

    init(playlist: MusicPlaylist, isPresented: Binding<Bool>) {
        self.playlist = playlist
        _isPresented = isPresented
        _name = State(initialValue: playlist.name)
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 18) {
            Text("Rename Playlist")
                .font(.title2.bold())
            TextField("Playlist Name", text: $name)
            HStack {
                Spacer()
                Button("Cancel") {
                    isPresented = false
                }
                Button("Save") {
                    Task {
                        if await library.rename(playlist, to: name, using: connection.client) {
                            isPresented = false
                        }
                    }
                }
                .buttonStyle(.borderedProminent)
                .disabled(name.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
            }
        }
        .padding(24)
        .frame(width: 400)
    }
}

func musicFormatTime(_ seconds: Double) -> String {
    guard seconds.isFinite, seconds >= 0 else { return "0:00" }
    return "\(Int(seconds) / 60):\(String(format: "%02d", Int(seconds) % 60))"
}
