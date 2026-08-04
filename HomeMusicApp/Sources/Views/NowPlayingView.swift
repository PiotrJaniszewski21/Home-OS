import SwiftUI

struct NowPlayingView: View {
    @Environment(\.dismiss) private var dismiss
    @EnvironmentObject private var player: PlayerManager

    var body: some View {
        NavigationStack {
            VStack(spacing: 28) {
                Spacer()
                if let track = player.currentTrack {
                    ArtworkView(track: track)
                        .aspectRatio(1, contentMode: .fit)
                        .shadow(color: .black.opacity(0.25), radius: 28, y: 16)
                    VStack(alignment: .leading, spacing: 6) {
                        HStack {
                            VStack(alignment: .leading, spacing: 6) {
                                Text(track.title).font(.title2.bold()).lineLimit(1)
                                Text(track.artist).font(.title3).foregroundStyle(.secondary).lineLimit(1)
                            }
                            Spacer()
                            Button { Task { await player.toggleLike() } } label: {
                                Image(systemName: track.liked == true ? "heart.fill" : "heart")
                                    .font(.title2)
                            }
                        }
                    }.frame(maxWidth: .infinity, alignment: .leading)
                    Slider(
                        value: Binding(get: { player.elapsed }, set: player.seek),
                        in: 0...max(player.duration, 1)
                    )
                    HStack(spacing: 56) {
                        Button(action: player.playPrevious) { Image(systemName: "backward.fill") }
                        Button(action: player.togglePlayback) {
                            Image(systemName: player.isPlaying ? "pause.circle.fill" : "play.circle.fill")
                                .font(.system(size: 68))
                        }
                        Button(action: player.playNext) { Image(systemName: "forward.fill") }
                    }.font(.title)
                }
                Spacer()
            }
            .padding(28)
            .background(.regularMaterial)
            .toolbar {
                ToolbarItem(placement: .topBarLeading) {
                    Button(action: { dismiss() }) { Image(systemName: "chevron.down") }
                }
                ToolbarItem(placement: .principal) { Text("Playing Now").font(.subheadline.weight(.semibold)) }
            }
        }
        .presentationDragIndicator(.visible)
    }
}
