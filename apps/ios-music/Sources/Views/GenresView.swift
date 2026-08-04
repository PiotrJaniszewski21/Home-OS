import SwiftUI

@MainActor
final class GenresModel: ObservableObject {
    @Published var genres: [String] = []
    @Published var isLoading = false
    @Published var error: String?

    func load(using client: APIClient?) async {
        guard genres.isEmpty, let client else { return }
        if let cached = await CatalogCacheStore.shared.load(
            [String].self,
            key: "genres",
            client: client,
            maximumAge: 30 * 24 * 60 * 60
        ) {
            genres = cached
        }
        isLoading = genres.isEmpty
        defer { isLoading = false }
        do {
            genres = try await client.genres()
            await CatalogCacheStore.shared.save(
                genres,
                key: "genres",
                client: client
            )
        } catch {
            self.error = error.localizedDescription
        }
    }
}

struct GenresView: View {
    @EnvironmentObject private var session: AppSession
    @StateObject private var model = GenresModel()
    @State private var query = ""

    private var visibleGenres: [String] {
        let value = query.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !value.isEmpty else { return model.genres }
        return model.genres.filter {
            $0.localizedCaseInsensitiveContains(value)
        }
    }

    var body: some View {
        NavigationStack {
            Group {
                if model.isLoading, model.genres.isEmpty {
                    ProgressView("Loading Genres…")
                } else if model.genres.isEmpty {
                    ContentUnavailableView(
                        "Genres Unavailable",
                        systemImage: "music.note.list",
                        description: Text(model.error ?? "Home OS could not load genres.")
                    )
                } else if visibleGenres.isEmpty {
                    ContentUnavailableView.search(text: query)
                } else {
                    ScrollView {
                        LazyVGrid(
                            columns: [
                                GridItem(.flexible(), spacing: 12),
                                GridItem(.flexible(), spacing: 12),
                            ],
                            spacing: 12
                        ) {
                            ForEach(visibleGenres, id: \.self) { genre in
                                NavigationLink {
                                    GenreDetailView(genre: genre)
                                } label: {
                                    GenreTile(genre: genre)
                                }
                                .buttonStyle(.plain)
                            }
                        }
                        .padding(.horizontal, 16)
                        .padding(.vertical, 12)
                    }
                }
            }
            .navigationTitle("Genres")
            .searchable(
                text: $query,
                placement: .navigationBarDrawer(displayMode: .always),
                prompt: "Find a genre"
            )
            .task { await model.load(using: session.client) }
        }
    }
}

private struct GenreTile: View {
    let genre: String

    private static let colors: [Color] = [
        Color(red: 0.88, green: 0.18, blue: 0.24),
        Color(red: 0.08, green: 0.52, blue: 0.66),
        Color(red: 0.12, green: 0.56, blue: 0.36),
        Color(red: 0.93, green: 0.42, blue: 0.12),
        Color(red: 0.76, green: 0.22, blue: 0.52),
        Color(red: 0.23, green: 0.38, blue: 0.72),
        Color(red: 0.55, green: 0.34, blue: 0.68),
        Color(red: 0.64, green: 0.39, blue: 0.16),
    ]

    private var color: Color {
        let value = genre.unicodeScalars.reduce(0) {
            $0 + Int($1.value)
        }
        return Self.colors[value % Self.colors.count]
    }

    var body: some View {
        ZStack(alignment: .bottomLeading) {
            color
            Image(systemName: "music.note")
                .font(.system(size: 54, weight: .bold))
                .foregroundStyle(.white.opacity(0.18))
                .rotationEffect(.degrees(-8))
                .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topTrailing)
                .padding(14)
            Text(genre)
                .font(.headline.weight(.bold))
                .foregroundStyle(.white)
                .multilineTextAlignment(.leading)
                .lineLimit(3)
                .padding(14)
        }
        .aspectRatio(1, contentMode: .fit)
        .clipShape(RoundedRectangle(cornerRadius: 8, style: .continuous))
        .contentShape(RoundedRectangle(cornerRadius: 8, style: .continuous))
        .accessibilityLabel(genre)
    }
}

@MainActor
final class GenreDetailModel: ObservableObject {
    @Published var result: MusicSearchResult?
    @Published var isLoading = false
    @Published var error: String?

    func load(genre: String, using client: APIClient?) async {
        guard result == nil, let client else { return }
        let key = "genre:\(genre.lowercased())"
        if let cached = await CatalogCacheStore.shared.load(
            MusicSearchResult.self,
            key: key,
            client: client,
            maximumAge: 7 * 24 * 60 * 60
        ) {
            result = cached
        }
        isLoading = result == nil
        defer { isLoading = false }
        do {
            result = try await client.search(genre)
            if let result {
                await CatalogCacheStore.shared.save(
                    result,
                    key: key,
                    client: client
                )
            }
        } catch {
            self.error = error.localizedDescription
        }
    }
}

struct GenreDetailView: View {
    @EnvironmentObject private var session: AppSession
    @StateObject private var model = GenreDetailModel()
    let genre: String

    var body: some View {
        Group {
            if let result = model.result {
                GenrePageContent(
                    genre: result.genre ?? genre,
                    popular: result.tracks,
                    recentReleases: result.recentReleases,
                    classics: result.classics,
                    hotArtists: result.hotArtists,
                    showsTitle: false
                )
            } else if model.isLoading {
                ProgressView("Loading \(genre)…")
            } else {
                ContentUnavailableView(
                    "Genre Unavailable",
                    systemImage: "music.note",
                    description: Text(model.error ?? "Home OS could not load this genre.")
                )
            }
        }
        .navigationTitle(genre)
        .navigationBarTitleDisplayMode(.large)
        .task { await model.load(genre: genre, using: session.client) }
    }
}

struct GenrePageContent: View {
    @EnvironmentObject private var player: PlayerManager
    let genre: String
    let popular: [Track]
    let recentReleases: [MusicRelease]
    let classics: [Track]
    let hotArtists: [ArtistSummary]
    let showsTitle: Bool

    private var likelyTracks: [Track] {
        popular + classics
    }

    var body: some View {
        ScrollView {
            LazyVStack(alignment: .leading, spacing: 26) {
                if showsTitle {
                    Text(genre)
                        .font(.largeTitle.bold())
                        .padding(.horizontal, 16)
                }

                if !recentReleases.isEmpty {
                    SearchSectionHeader(title: "New Releases")
                    releaseShelf
                }

                if !hotArtists.isEmpty {
                    SearchSectionHeader(title: "Hot Artists")
                    artistShelf
                }

                if !popular.isEmpty {
                    trackSection(title: "Popular Right Now", tracks: popular)
                }

                if !classics.isEmpty {
                    trackSection(title: "Classics", tracks: classics)
                }
            }
            .padding(.vertical, 12)
        }
        .task(id: likelyTracks.map(\.id)) {
            player.prepareForLikelyPlayback(likelyTracks)
        }
    }

    private var artistShelf: some View {
        ScrollView(.horizontal, showsIndicators: false) {
            LazyHStack(spacing: 18) {
                ForEach(hotArtists) { artist in
                    NavigationLink {
                        ArtistView(artistID: artist.id)
                    } label: {
                        VStack(spacing: 9) {
                            ArtistArtwork(url: artist.thumbnail)
                                .frame(width: 132, height: 132)
                            Text(artist.name)
                                .font(.subheadline.weight(.semibold))
                                .foregroundStyle(.primary)
                                .lineLimit(2)
                                .multilineTextAlignment(.center)
                        }
                        .frame(width: 132, alignment: .top)
                    }
                    .buttonStyle(.plain)
                }
            }
            .padding(.horizontal, 16)
        }
    }

    private var releaseShelf: some View {
        ScrollView(.horizontal, showsIndicators: false) {
            LazyHStack(spacing: 16) {
                ForEach(recentReleases) { release in
                    NavigationLink {
                        AlbumView(albumID: release.id)
                    } label: {
                        VStack(alignment: .leading, spacing: 8) {
                            ReleaseArtwork(url: release.thumbnail)
                                .frame(width: 164, height: 164)
                            Text(release.title)
                                .font(.subheadline.weight(.semibold))
                                .foregroundStyle(.primary)
                                .lineLimit(1)
                            Text(
                                [release.artist, release.year]
                                    .compactMap { $0 }
                                    .filter { !$0.isEmpty }
                                    .joined(separator: " · ")
                            )
                            .font(.caption)
                            .foregroundStyle(.secondary)
                            .lineLimit(1)
                        }
                        .frame(width: 164, alignment: .leading)
                    }
                    .buttonStyle(.plain)
                }
            }
            .padding(.horizontal, 16)
        }
    }

    @ViewBuilder
    private func trackSection(title: String, tracks: [Track]) -> some View {
        SearchSectionHeader(title: title)
        LazyVStack(spacing: 0) {
            ForEach(tracks) { track in
                TrackRow(track: track, context: tracks)
                    .padding(.horizontal, 16)
                    .padding(.vertical, 5)
                if track.id != tracks.last?.id {
                    Divider().padding(.leading, 84)
                }
            }
        }
    }
}
