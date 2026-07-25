import SwiftUI

@MainActor
final class SearchModel: ObservableObject {
    @Published var query = ""
    @Published var results: [Track] = []
    @Published var artists: [ArtistSummary] = []
    @Published var albums: [MusicRelease] = []
    @Published private(set) var recentSearches: [String] = []
    @Published var isSearching = false
    @Published var error: String?

    private let historyKey = "HomeMusicSearchHistory"

    init() {
        recentSearches = UserDefaults.standard.stringArray(forKey: historyKey) ?? []
    }

    var hasResults: Bool {
        !results.isEmpty || !artists.isEmpty || !albums.isEmpty
    }

    func search(_ requestedTerm: String? = nil, using client: APIClient?) async {
        if let requestedTerm { query = requestedTerm }
        let term = query.trimmingCharacters(in: .whitespacesAndNewlines)
        guard let client, !term.isEmpty else { return }
        record(term)
        isSearching = true
        error = nil
        defer { isSearching = false }

        async let trackRequest = try? client.search(term)
        async let artistRequest = try? client.searchArtists(term)
        async let albumRequest = try? client.searchAlbums(term)
        let (tracks, foundArtists, foundAlbums) = await (
            trackRequest,
            artistRequest,
            albumRequest
        )
        results = tracks ?? []
        artists = foundArtists ?? []
        albums = foundAlbums ?? []
        if tracks == nil, foundArtists == nil, foundAlbums == nil {
            error = "Home OS couldn’t complete this search."
        }
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
}

struct SearchView: View {
    @EnvironmentObject private var session: AppSession
    @StateObject private var model = SearchModel()

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
            .searchable(text: $model.query, prompt: "Artists, albums and songs")
            .onSubmit(of: .search) {
                Task { await model.search(using: session.client) }
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
                    description: Text("Find songs, albums and artists from YouTube Music.")
                )
                .listRowBackground(Color.clear)
            }
        }
        .listStyle(.plain)
    }

    private var searchResults: some View {
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
                    SearchSectionHeader(title: "Songs")
                    LazyVStack(spacing: 0) {
                        ForEach(model.results) { track in
                            TrackRow(track: track, context: [track])
                                .padding(.horizontal, 16)
                                .padding(.vertical, 5)
                            if track.id != model.results.last?.id {
                                Divider().padding(.leading, 84)
                            }
                        }
                    }
                }
            }
            .padding(.vertical, 12)
        }
        .overlay(alignment: .top) {
            if model.isSearching {
                ProgressView().padding(8).background(.regularMaterial, in: Capsule())
            }
        }
    }
}

private struct SearchSectionHeader: View {
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
