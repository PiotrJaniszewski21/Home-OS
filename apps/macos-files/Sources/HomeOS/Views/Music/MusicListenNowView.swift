import SwiftUI

@MainActor
final class MusicListenNowModel: ObservableObject {
    @Published var feed = MusicHomeFeed.empty
    @Published var isLoading = false
    @Published var error: String?

    var isEmpty: Bool {
        feed.suggestedSongs.isEmpty
            && feed.suggestedAlbums.isEmpty
            && feed.newReleases.isEmpty
    }

    func load(using client: APIClient?) async {
        guard let client else { return }
        isLoading = true
        defer { isLoading = false }
        do {
            feed = try await client.musicHomeFeed()
            error = nil
        } catch {
            self.error = error.localizedDescription
        }
    }
}

struct MusicListenNowView: View {
    @EnvironmentObject private var connection: HomeOSMusicSession
    @EnvironmentObject private var player: MusicPlayerManager
    @StateObject private var model = MusicListenNowModel()

    var body: some View {
        NavigationStack {
            ScrollView {
                LazyVStack(alignment: .leading, spacing: 32) {
                    if let first = model.feed.suggestedSongs.first {
                        personalStation(first: first)
                    }
                    releaseShelf(
                        title: "Albums for You",
                        subtitle: "Picked from artists and songs you return to",
                        releases: model.feed.suggestedAlbums
                    )
                    releaseShelf(
                        title: "New Releases",
                        subtitle: "The latest from artists you listen to",
                        releases: model.feed.newReleases
                    )
                    if !model.feed.suggestedSongs.isEmpty {
                        songsForYou
                    }
                    if model.isEmpty, !model.isLoading, model.error == nil {
                        ContentUnavailableView(
                            "Listen Now is learning your taste",
                            systemImage: "sparkles",
                            description: Text(
                                "Play and love a few songs, then return for personalised albums, releases and queues."
                            )
                        )
                        .frame(maxWidth: .infinity, minHeight: 380)
                    }
                }
                .padding(28)
            }
            .navigationTitle("Listen Now")
            .toolbar {
                Button {
                    Task { await model.load(using: connection.client) }
                } label: {
                    Label("Refresh", systemImage: "arrow.clockwise")
                }
            }
            .task {
                if model.isEmpty {
                    await model.load(using: connection.client)
                }
            }
            .overlay {
                if model.isLoading, model.isEmpty {
                    ProgressView("Personalising Listen Now…")
                } else if let error = model.error, model.isEmpty {
                    MusicLoadFailureView(message: error) {
                        Task { await model.load(using: connection.client) }
                    }
                }
            }
        }
    }

    private func personalStation(first: MusicTrack) -> some View {
        Button {
            Task { await player.play(first, from: model.feed.suggestedSongs) }
        } label: {
            ZStack(alignment: .bottomLeading) {
                MusicRemoteArtworkView(url: first.thumbnail)
                LinearGradient(
                    colors: [.clear, .black.opacity(0.2), .black.opacity(0.92)],
                    startPoint: .top,
                    endPoint: .bottom
                )
                VStack(alignment: .leading, spacing: 8) {
                    Text("MADE FOR \(connection.activeEndpoint?.displayName.uppercased() ?? "YOU")")
                        .font(.caption.bold())
                        .tracking(1.3)
                        .foregroundStyle(.white.opacity(0.76))
                    Text("Your Personal Station")
                        .font(.largeTitle.bold())
                        .foregroundStyle(.white)
                    Text("Starting with \(first.title) · \(first.artist)")
                        .font(.headline)
                        .foregroundStyle(.white.opacity(0.8))
                        .lineLimit(1)
                    Label("Play", systemImage: "play.fill")
                        .font(.headline)
                        .foregroundStyle(.black)
                        .padding(.horizontal, 18)
                        .frame(height: 38)
                        .background(.white, in: Capsule())
                        .padding(.top, 4)
                }
                .padding(24)
            }
            .frame(maxWidth: .infinity)
            .frame(height: 310)
            .clipShape(RoundedRectangle(cornerRadius: 22, style: .continuous))
            .shadow(color: .black.opacity(0.18), radius: 18, y: 10)
        }
        .buttonStyle(.plain)
    }

    @ViewBuilder
    private func releaseShelf(
        title: String,
        subtitle: String,
        releases: [MusicHomeRelease]
    ) -> some View {
        if !releases.isEmpty {
            VStack(alignment: .leading, spacing: 14) {
                MusicSectionHeader(title: title, subtitle: subtitle)
                ScrollView(.horizontal, showsIndicators: false) {
                    LazyHStack(spacing: 16) {
                        ForEach(releases) { release in
                            NavigationLink {
                                MusicAlbumView(albumID: release.id)
                            } label: {
                                MusicReleaseCard(
                                    title: release.title,
                                    subtitle: release.artist.isEmpty ? release.type : release.artist,
                                    artwork: release.thumbnail
                                )
                                .frame(width: 178)
                            }
                            .buttonStyle(.plain)
                        }
                    }
                    .padding(.vertical, 4)
                }
            }
        }
    }

    private var songsForYou: some View {
        VStack(alignment: .leading, spacing: 14) {
            HStack(alignment: .bottom) {
                MusicSectionHeader(
                    title: "Songs for You",
                    subtitle: "A fresh queue shaped by your listening"
                )
                Spacer()
                Button {
                    Task { await player.playShuffled(model.feed.suggestedSongs) }
                } label: {
                    Label("Shuffle", systemImage: "shuffle")
                }
            }
            LazyVStack(spacing: 0) {
                ForEach(Array(model.feed.suggestedSongs.prefix(12))) { track in
                    MusicTrackRow(track: track, context: model.feed.suggestedSongs)
                        .padding(.horizontal, 12)
                    if track.id != model.feed.suggestedSongs.prefix(12).last?.id {
                        Divider().padding(.leading, 70)
                    }
                }
            }
            .padding(.vertical, 6)
            .background(.regularMaterial, in: RoundedRectangle(cornerRadius: 16, style: .continuous))
        }
    }
}
