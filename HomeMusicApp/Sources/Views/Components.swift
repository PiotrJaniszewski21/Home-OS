import SwiftUI

struct ArtworkView: View {
    let track: Track

    var body: some View {
        AsyncImage(url: URL(string: track.thumbnail)) { phase in
            if let image = phase.image {
                image.resizable().scaledToFill()
            } else {
                ZStack {
                    LinearGradient(colors: [.pink, .purple], startPoint: .topLeading, endPoint: .bottomTrailing)
                    Image(systemName: "music.note").font(.largeTitle).foregroundStyle(.white.opacity(0.9))
                }
            }
        }
        .clipShape(RoundedRectangle(cornerRadius: 12, style: .continuous))
    }
}

struct TrackRow: View {
    @EnvironmentObject private var player: PlayerManager
    let track: Track
    let context: [Track]

    var body: some View {
        Button { Task { await player.play(track, from: context) } } label: {
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
                Image(systemName: "ellipsis").foregroundStyle(.secondary)
            }
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
    }
}

struct MiniPlayerView: View {
    @EnvironmentObject private var player: PlayerManager
    let open: () -> Void

    var body: some View {
        VStack(spacing: 0) {
            Divider()
            HStack(spacing: 12) {
                if let track = player.currentTrack {
                    ArtworkView(track: track).frame(width: 46, height: 46)
                    Text(track.title).font(.subheadline.weight(.medium)).lineLimit(1)
                    Spacer()
                    Button(action: player.togglePlayback) {
                        Image(systemName: player.isPlaying ? "pause.fill" : "play.fill").font(.title3)
                    }
                    Button(action: player.playNext) { Image(systemName: "forward.fill").font(.title3) }
                }
            }
            .padding(.horizontal, 14).padding(.vertical, 8)
            .background(.ultraThinMaterial)
            .contentShape(Rectangle()).onTapGesture(perform: open)
        }
    }
}
