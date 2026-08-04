import SwiftUI

@MainActor
final class ListenNowModel: ObservableObject {
    @Published var feed = HomeMusicFeed(
        suggestedSongs: [],
        suggestedAlbums: [],
        newReleases: []
    )
    @Published var isLoading = false
    @Published var error: String?
    private var loadedCacheIdentity: String?

    var isEmpty: Bool {
        feed.suggestedSongs.isEmpty
            && feed.suggestedAlbums.isEmpty
            && feed.newReleases.isEmpty
    }

    func load(using client: APIClient?, forceRefresh: Bool = false) async {
        guard let client, !isLoading else { return }
        let cacheIdentity = HomeFeedStore.namespace(for: client)
        if loadedCacheIdentity != cacheIdentity {
            if let cached = HomeFeedStore.load(for: client) {
                feed = cached
            }
            loadedCacheIdentity = cacheIdentity
        }
        isLoading = true
        defer { isLoading = false }
        do {
            let refreshedFeed = try await client.homeFeed(forceRefresh: forceRefresh)
            feed = refreshedFeed
            try? HomeFeedStore.save(refreshedFeed, for: client)
            error = nil
        } catch {
            self.error = error.localizedDescription
        }
    }
}

struct ListenNowView: View {
    @EnvironmentObject private var session: AppSession
    @EnvironmentObject private var player: PlayerManager
    @StateObject private var model = ListenNowModel()

    var body: some View {
        NavigationStack {
            ScrollView {
                LazyVStack(alignment: .leading, spacing: 34) {
                    if let first = model.feed.suggestedSongs.first {
                        PersonalStationHero(
                            track: first,
                            tracks: model.feed.suggestedSongs
                        )
                    }

                    if !model.feed.suggestedAlbums.isEmpty {
                        ReleaseShelf(
                            title: "Albums for You",
                            subtitle: "Picked from artists and songs you return to",
                            releases: model.feed.suggestedAlbums
                        )
                    }

                    if !model.feed.newReleases.isEmpty {
                        ReleaseShelf(
                            title: "New Releases",
                            subtitle: "The latest from artists you listen to",
                            releases: model.feed.newReleases
                        )
                    }

                    if !model.feed.suggestedSongs.isEmpty {
                        SuggestedSongsSection(tracks: model.feed.suggestedSongs)
                    }

                    if model.isEmpty && !model.isLoading && model.error == nil {
                        ContentUnavailableView(
                            "Listen Now is learning your taste",
                            systemImage: "sparkles",
                            description: Text("Play and love a few songs, then return for albums, releases and music chosen for you.")
                        )
                        .frame(maxWidth: .infinity, minHeight: 420)
                    }
                }
                .padding(.horizontal, 18)
                .padding(.bottom, 34)
            }
            .navigationTitle("Listen Now")
            .toolbar {
                ToolbarItemGroup(placement: .topBarTrailing) {
                    Button {
                        Task {
                            await model.load(
                                using: session.client,
                                forceRefresh: true
                            )
                        }
                    } label: {
                        Image(systemName: "arrow.clockwise")
                    }
                    .disabled(model.isLoading)
                    Menu {
                        Button("Sign Out", role: .destructive, action: session.signOut)
                    } label: {
                        Image(systemName: "person.crop.circle")
                    }
                }
            }
            .refreshable {
                await model.load(using: session.client, forceRefresh: true)
            }
            .task { await model.load(using: session.client) }
            .task(id: model.feed.suggestedSongs.map(\.id)) {
                player.prepareForLikelyPlayback(model.feed.suggestedSongs)
            }
            .overlay {
                if model.isLoading && model.isEmpty {
                    ProgressView("Personalising Listen Now…")
                } else if let error = model.error, model.isEmpty {
                    LoadFailureView(message: error) {
                        Task { await model.load(using: session.client) }
                    }
                }
            }
        }
    }
}

private struct PersonalStationHero: View {
    @EnvironmentObject private var player: PlayerManager
    let track: Track
    let tracks: [Track]

    var body: some View {
        Button {
            Task { await player.play(track, from: tracks) }
        } label: {
            ZStack(alignment: .bottomLeading) {
                RemoteArtworkView(url: track.thumbnail)
                LinearGradient(
                    colors: [.clear, .black.opacity(0.2), .black.opacity(0.92)],
                    startPoint: .top,
                    endPoint: .bottom
                )
                VStack(alignment: .leading, spacing: 7) {
                    Text("MADE FOR YOU")
                        .font(.caption2.bold())
                        .tracking(1.4)
                        .foregroundStyle(.white.opacity(0.76))
                    Text("Your Personal Station")
                        .font(.title.bold())
                        .foregroundStyle(.white)
                    Text("Starting with \(track.title) · \(track.artist)")
                        .font(.subheadline)
                        .foregroundStyle(.white.opacity(0.78))
                        .lineLimit(1)
                    Label("Play", systemImage: "play.fill")
                        .font(.headline)
                        .foregroundStyle(.black)
                        .padding(.horizontal, 18)
                        .frame(height: 42)
                        .background(.white)
                        .clipShape(Capsule())
                        .padding(.top, 5)
                }
                .padding(22)
            }
            .frame(height: 300)
            .clipShape(RoundedRectangle(cornerRadius: 24, style: .continuous))
            .shadow(color: .black.opacity(0.18), radius: 18, y: 10)
        }
        .buttonStyle(.plain)
    }
}

private struct ReleaseShelf: View {
    let title: String
    let subtitle: String
    let releases: [HomeMusicRelease]

    var body: some View {
        VStack(alignment: .leading, spacing: 15) {
            SectionHeading(title: title, subtitle: subtitle)
            ScrollView(.horizontal, showsIndicators: false) {
                LazyHStack(spacing: 16) {
                    ForEach(releases) { release in
                        NavigationLink {
                            AlbumView(albumID: release.id)
                        } label: {
                            VStack(alignment: .leading, spacing: 8) {
                                RemoteArtworkView(url: release.thumbnail)
                                    .frame(width: 178, height: 178)
                                    .clipShape(RoundedRectangle(cornerRadius: 14, style: .continuous))
                                    .shadow(color: .black.opacity(0.13), radius: 10, y: 6)
                                Text(release.title)
                                    .font(.headline)
                                    .lineLimit(1)
                                Text(release.artist.isEmpty ? release.type : release.artist)
                                    .font(.subheadline)
                                    .foregroundStyle(.secondary)
                                    .lineLimit(1)
                            }
                            .frame(width: 178, alignment: .leading)
                        }
                        .buttonStyle(.plain)
                    }
                }
                .padding(.vertical, 3)
            }
            .contentMargins(.horizontal, 1, for: .scrollContent)
        }
    }
}

private struct SuggestedSongsSection: View {
    @EnvironmentObject private var player: PlayerManager
    let tracks: [Track]

    var body: some View {
        VStack(alignment: .leading, spacing: 15) {
            HStack(alignment: .bottom) {
                SectionHeading(
                    title: "Songs for You",
                    subtitle: "A fresh queue shaped by your listening"
                )
                Spacer()
                Button {
                    let shuffled = tracks.shuffled()
                    guard let first = shuffled.first else { return }
                    Task { await player.play(first, from: shuffled) }
                } label: {
                    Label("Shuffle", systemImage: "shuffle")
                }
                .buttonStyle(.bordered)
            }

            LazyVStack(spacing: 0) {
                ForEach(tracks.prefix(12)) { track in
                    TrackRow(track: track, context: tracks)
                        .padding(.vertical, 6)
                    if track.id != tracks.prefix(12).last?.id {
                        Divider().padding(.leading, 66)
                    }
                }
            }
            .padding(.horizontal, 14)
            .background(Color(uiColor: .secondarySystemGroupedBackground))
            .clipShape(RoundedRectangle(cornerRadius: 18, style: .continuous))
        }
    }
}

private struct SectionHeading: View {
    let title: String
    let subtitle: String

    var body: some View {
        VStack(alignment: .leading, spacing: 3) {
            Text(title).font(.title2.bold())
            Text(subtitle)
                .font(.subheadline)
                .foregroundStyle(.secondary)
        }
    }
}
