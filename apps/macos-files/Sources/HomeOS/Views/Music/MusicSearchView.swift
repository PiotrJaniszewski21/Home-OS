import SwiftUI

@MainActor
final class MusicSearchModel: ObservableObject {
    @Published var query = ""
    @Published var tracks: [MusicTrack] = []
    @Published var artists: [MusicArtistSummary] = []
    @Published var albums: [MusicRelease] = []
    @Published private(set) var recentSearches: [String] = []
    @Published var isSearching = false
    @Published var error: String?

    private let historyKey = "HomeOSMusicSearchHistory"

    init() {
        recentSearches = UserDefaults.standard.stringArray(forKey: historyKey) ?? []
    }

    var hasResults: Bool {
        !tracks.isEmpty || !artists.isEmpty || !albums.isEmpty
    }

    func search(_ requestedTerm: String? = nil, using client: APIClient?) async {
        if let requestedTerm {
            query = requestedTerm
        }
        let term = query.trimmingCharacters(in: .whitespacesAndNewlines)
        guard let client, !term.isEmpty else { return }
        record(term)
        isSearching = true
        error = nil
        defer { isSearching = false }

        async let trackRequest = try? client.musicSearch(term)
        async let artistRequest = try? client.musicSearchArtists(term)
        async let albumRequest = try? client.musicSearchAlbums(term)
        let (tracks, artists, albums) = await (
            trackRequest,
            artistRequest,
            albumRequest
        )
        self.tracks = tracks ?? []
        self.artists = artists ?? []
        self.albums = albums ?? []
        if tracks == nil, artists == nil, albums == nil {
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
        recentSearches.removeAll {
            $0.localizedCaseInsensitiveCompare(term) == .orderedSame
        }
        recentSearches.insert(term, at: 0)
        recentSearches = Array(recentSearches.prefix(12))
        UserDefaults.standard.set(recentSearches, forKey: historyKey)
    }
}

struct MusicSearchView: View {
    @EnvironmentObject private var connection: HomeOSMusicSession
    @StateObject private var model = MusicSearchModel()

    var body: some View {
        NavigationStack {
            Group {
                if model.hasResults {
                    results
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
                Task { await model.search(using: connection.client) }
            }
            .alert(
                "Search Failed",
                isPresented: Binding(
                    get: { model.error != nil },
                    set: { if !$0 { model.error = nil } }
                )
            ) {
                Button("OK") {
                    model.error = nil
                }
            } message: {
                Text(model.error ?? "")
            }
        }
    }

    private var searchLanding: some View {
        List {
            if model.recentSearches.isEmpty {
                ContentUnavailableView(
                    "Search HomeMusic",
                    systemImage: "music.note.magnifyingglass",
                    description: Text("Find songs, albums and artists from YouTube Music.")
                )
            } else {
                Section {
                    ForEach(model.recentSearches, id: \.self) { term in
                        Button {
                            Task { await model.search(term, using: connection.client) }
                        } label: {
                            HStack {
                                Image(systemName: "clock.arrow.circlepath")
                                    .foregroundStyle(.secondary)
                                Text(term)
                                    .foregroundStyle(.primary)
                                Spacer()
                                Button {
                                    model.removeHistory(term)
                                } label: {
                                    Image(systemName: "xmark")
                                }
                                .buttonStyle(.borderless)
                            }
                        }
                        .buttonStyle(.plain)
                    }
                } header: {
                    HStack {
                        Text("Recently Searched")
                        Spacer()
                        Button("Clear") {
                            model.clearHistory()
                        }
                        .buttonStyle(.link)
                    }
                }
            }
        }
    }

    private var results: some View {
        ScrollView {
            LazyVStack(alignment: .leading, spacing: 28) {
                if !model.artists.isEmpty {
                    MusicSectionHeader(title: "Artists")
                    ScrollView(.horizontal, showsIndicators: false) {
                        LazyHStack(spacing: 18) {
                            ForEach(model.artists) { artist in
                                NavigationLink {
                                    MusicArtistView(artistID: artist.id)
                                } label: {
                                    VStack(spacing: 9) {
                                        MusicRemoteArtworkView(
                                            url: artist.thumbnail,
                                            placeholderSymbol: "person.crop.circle.fill"
                                        )
                                        .frame(width: 132, height: 132)
                                        .clipShape(Circle())
                                        Text(artist.name)
                                            .font(.headline)
                                            .lineLimit(1)
                                    }
                                    .frame(width: 132)
                                }
                                .buttonStyle(.plain)
                            }
                        }
                        .padding(.vertical, 3)
                    }
                }

                if !model.albums.isEmpty {
                    MusicSectionHeader(title: "Albums")
                    ScrollView(.horizontal, showsIndicators: false) {
                        LazyHStack(spacing: 16) {
                            ForEach(model.albums) { album in
                                NavigationLink {
                                    MusicAlbumView(albumID: album.id)
                                } label: {
                                    MusicReleaseCard(
                                        title: album.title,
                                        subtitle: [album.type, album.year]
                                            .filter { !$0.isEmpty }
                                            .joined(separator: " · "),
                                        artwork: album.thumbnail
                                    )
                                    .frame(width: 164)
                                }
                                .buttonStyle(.plain)
                            }
                        }
                        .padding(.vertical, 3)
                    }
                }

                if !model.tracks.isEmpty {
                    MusicSectionHeader(title: "Songs")
                    LazyVStack(spacing: 0) {
                        ForEach(model.tracks) { track in
                            MusicTrackRow(track: track, context: [track])
                            if track.id != model.tracks.last?.id {
                                Divider().padding(.leading, 64)
                            }
                        }
                    }
                }
            }
            .padding(28)
        }
        .overlay(alignment: .top) {
            if model.isSearching {
                ProgressView()
                    .padding(8)
                    .background(.regularMaterial, in: Capsule())
            }
        }
    }
}
