import SwiftUI

private struct CachedSearchPage: Codable {
    let result: MusicSearchResult
    let artists: [ArtistSummary]
    let albums: [MusicRelease]
}

@MainActor
final class SearchModel: ObservableObject {
    @Published var query = ""
    @Published var results: [Track] = []
    @Published var artists: [ArtistSummary] = []
    @Published var albums: [MusicRelease] = []
    @Published var genre: String?
    @Published var recentReleases: [MusicRelease] = []
    @Published var classics: [Track] = []
    @Published var hotArtists: [ArtistSummary] = []
    @Published private(set) var recentSearches: [String] = []
    @Published var isSearching = false
    @Published var error: String?

    private let historyKey = "HomeMusicSearchHistory"
    private var latestSearchID = UUID()

    init() {
        recentSearches = UserDefaults.standard.stringArray(forKey: historyKey) ?? []
    }

    var hasResults: Bool {
        !results.isEmpty
            || !artists.isEmpty
            || !albums.isEmpty
            || !recentReleases.isEmpty
            || !classics.isEmpty
            || !hotArtists.isEmpty
    }

    func search(_ requestedTerm: String? = nil, using client: APIClient?) async {
        if let requestedTerm { query = requestedTerm }
        let term = query.trimmingCharacters(in: .whitespacesAndNewlines)
        guard let client, !term.isEmpty else { return }
        let searchID = UUID()
        latestSearchID = searchID
        record(term)
        isSearching = true
        error = nil
        let cacheKey = "search:\(term.lowercased())"
        if let cached = await CatalogCacheStore.shared.load(
            CachedSearchPage.self,
            key: cacheKey,
            client: client,
            maximumAge: 7 * 24 * 60 * 60
        ), latestSearchID == searchID {
            apply(cached.result, artists: cached.artists, albums: cached.albums)
        }
        defer {
            if latestSearchID == searchID {
                isSearching = false
            }
        }

        let unifiedResult = try? await client.unifiedSearch(term)
        guard latestSearchID == searchID else { return }
        guard let unifiedResult else {
            if !hasResults { error = "Home OS couldn’t complete this search." }
            return
        }
        results = unifiedResult.tracks
        genre = unifiedResult.genre
        recentReleases = unifiedResult.recentReleases
        classics = unifiedResult.classics
        hotArtists = unifiedResult.hotArtists
        artists = unifiedResult.artists
        albums = unifiedResult.albums
        
        let musicResult = MusicSearchResult(
            tracks: unifiedResult.tracks,
            genre: unifiedResult.genre,
            recentReleases: unifiedResult.recentReleases,
            classics: unifiedResult.classics,
            hotArtists: unifiedResult.hotArtists
        )
        await CatalogCacheStore.shared.save(
            CachedSearchPage(
                result: musicResult,
                artists: artists,
                albums: albums
            ),
            key: cacheKey,
            client: client
        )
    }

    func removeHistory(_ term: String) {
        recentSearches.removeAll { $0 == term }
        UserDefaults.standard.set(recentSearches, forKey: historyKey)
    }

    func clearHistory() {
        recentSearches = []
        UserDefaults.standard.removeObject(forKey: historyKey)
    }

    private func record(_ term: String) {
        recentSearches.removeAll { $0.localizedCaseInsensitiveCompare(term) == .orderedSame }
        recentSearches.insert(term, at: 0)
        recentSearches = Array(recentSearches.prefix(12))
        UserDefaults.standard.set(recentSearches, forKey: historyKey)
    }

    private func apply(
        _ result: MusicSearchResult,
        artists: [ArtistSummary],
        albums: [MusicRelease]
    ) {
        results = result.tracks
        genre = result.genre
        recentReleases = result.recentReleases
        classics = result.classics
        hotArtists = result.hotArtists
        self.artists = result.genre == nil ? artists : []
        self.albums = result.genre == nil ? albums : []
    }
}

struct SearchView: View {
    @EnvironmentObject private var session: AppSession
    @EnvironmentObject private var player: PlayerManager
    @StateObject private var model = SearchModel()

    private var likelyTracks: [Track] {
        model.results + model.classics
    }

    var body: some View {
        NavigationStack {
            Group {
                if model.hasResults {
                    searchResults
                } else if model.isSearching {
                    ProgressView("Searching HomeMusic…")
                } else if model.query.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
                    searchLanding
                } else {
                    ContentUnavailableView.search(text: model.query)
                }
            }
            .navigationTitle("Search")
            .searchable(
                text: $model.query,
                placement: .navigationBarDrawer(displayMode: .always),
                prompt: "Artists, albums, songs and genres"
            )
            .onSubmit(of: .search) {
                Task { await model.search(using: session.client) }
            }
            .task(id: likelyTracks.map(\.id)) {
                player.prepareForLikelyPlayback(likelyTracks)
            }
            .alert("Search Failed", isPresented: Binding(
                get: { model.error != nil },
                set: { if !$0 { model.error = nil } }
            )) {
                Button("OK") { model.error = nil }
            } message: {
                Text(model.error ?? "")
            }
        }
    }

    private var searchLanding: some View {
        List {
            if !model.recentSearches.isEmpty {
                Section {
                    ForEach(model.recentSearches, id: \.self) { term in
                        Button {
                            Task { await model.search(term, using: session.client) }
                        } label: {
                            HStack(spacing: 13) {
                                Image(systemName: "clock.arrow.circlepath")
                                    .foregroundStyle(.secondary)
                                Text(term).foregroundStyle(.primary)
                                Spacer()
                                Image(systemName: "arrow.up.left")
                                    .font(.caption)
                                    .foregroundStyle(.tertiary)
                            }
                        }
                        .swipeActions {
                            Button(role: .destructive) {
                                model.removeHistory(term)
                            } label: {
                                Label("Remove", systemImage: "trash")
                            }
                        }
                    }
                } header: {
                    HStack {
                        Text("Recently Searched")
                        Spacer()
                        Button("Clear") { model.clearHistory() }
                            .font(.subheadline)
                            .textCase(nil)
                    }
                }
            } else {
                ContentUnavailableView(
                    "Search HomeMusic",
                    systemImage: "music.note.magnifyingglass",
                    description: Text("Find songs, albums and artists.")
                )
                .listRowBackground(Color.clear)
            }
        }
        .listStyle(.plain)
    }

    private var searchResults: some View {
        Group {
            if let genre = model.genre {
                GenrePageContent(
                    genre: genre,
                    popular: model.results,
                    recentReleases: model.recentReleases,
                    classics: model.classics,
                    hotArtists: model.hotArtists,
                    showsTitle: true
                )
            } else {
                ScrollView {
                    LazyVStack(alignment: .leading, spacing: 26) {
                        if !model.artists.isEmpty {
                    SearchSectionHeader(title: "Artists")
                    ScrollView(.horizontal, showsIndicators: false) {
                        LazyHStack(spacing: 18) {
                            ForEach(model.artists) { artist in
                                NavigationLink {
                                    ArtistView(artistID: artist.id)
                                } label: {
                                    VStack(spacing: 9) {
                                        ArtistArtwork(url: artist.thumbnail)
                                            .frame(width: 132, height: 132)
                                        Text(artist.name)
                                            .font(.subheadline.weight(.semibold))
                                            .foregroundStyle(.primary)
                                            .lineLimit(1)
                                    }
                                    .frame(width: 132)
                                }
                                .buttonStyle(.plain)
                            }
                        }
                        .padding(.horizontal, 16)
                    }
                }

                        if !model.albums.isEmpty {
                    SearchSectionHeader(title: "Albums")
                    ScrollView(.horizontal, showsIndicators: false) {
                        LazyHStack(spacing: 16) {
                            ForEach(model.albums) { album in
                                NavigationLink {
                                    AlbumView(albumID: album.id)
                                } label: {
                                    VStack(alignment: .leading, spacing: 8) {
                                        ReleaseArtwork(url: album.thumbnail)
                                            .frame(width: 164, height: 164)
                                        Text(album.title)
                                            .font(.subheadline.weight(.semibold))
                                            .foregroundStyle(.primary)
                                            .lineLimit(1)
                                        Text([album.type, album.year].filter { !$0.isEmpty }.joined(separator: " · "))
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

                        if !model.results.isEmpty {
                            trackSection(title: "Songs", tracks: model.results)
                        }
                    }
                    .padding(.vertical, 12)
                }
            }
        }
        .overlay(alignment: .top) {
            if model.isSearching {
                ProgressView().padding(8).background(.regularMaterial, in: Capsule())
            }
        }
    }

    @ViewBuilder
    private func trackSection(title: String, tracks: [Track]) -> some View {
        SearchSectionHeader(title: title)
        LazyVStack(spacing: 0) {
            ForEach(tracks) { track in
                TrackRow(track: track, context: tracks, isSearch: true)
                    .padding(.horizontal, 16)
                    .padding(.vertical, 5)
                if track.id != tracks.last?.id {
                    Divider().padding(.leading, 84)
                }
            }
        }
    }
}

struct SearchSectionHeader: View {
    let title: String

    var body: some View {
        Text(title)
            .font(.title2.bold())
            .padding(.horizontal, 16)
    }
}

struct ArtistArtwork: View {
    let url: String

    var body: some View {
        RemoteArtworkView(
            url: url,
            placeholderSymbol: "person.crop.circle.fill"
        )
        .clipShape(Circle())
    }
}
