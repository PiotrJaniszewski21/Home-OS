import SwiftUI

enum HomeOSMusicDestination: Hashable {
    case listenNow
    case search
    case radio
    case loved
    case recent
    case albums
    case playlist(Int)
}

struct HomeOSMusicRootView: View {
    @EnvironmentObject private var session: HomeOSMusicSession
    @EnvironmentObject private var library: MusicLibraryStore
    @EnvironmentObject private var player: MusicPlayerManager
    @EnvironmentObject private var radio: MusicRadioStore
    @Environment(\.openSettings) private var openSettings
    @State private var destination: HomeOSMusicDestination = .listenNow
    @State private var showingNowPlaying = false
    @State private var showingNewPlaylist = false
    @State private var didRestore = false

    var body: some View {
        Group {
            if session.isConfigured {
                NavigationSplitView {
                    sidebar
                } detail: {
                    destinationView
                }
                .safeAreaInset(edge: .bottom, spacing: 0) {
                    if player.currentTrack != nil {
                        MusicMiniPlayerView {
                            showingNowPlaying = true
                        }
                    }
                }
            } else {
                HomeOSMusicLoginView()
            }
        }
        .toolbar {
            if session.isConfigured {
                ToolbarItemGroup(placement: .primaryAction) {
                    connectionBadge
                    Button("Settings", systemImage: "gear") {
                        openSettings()
                    }
                }
            }
        }
        .sheet(isPresented: $showingNowPlaying) {
            MusicNowPlayingView()
                .environmentObject(session)
                .environmentObject(library)
                .environmentObject(player)
                .environmentObject(radio)
        }
        .sheet(isPresented: $showingNewPlaylist) {
            MusicNewPlaylistSheet(isPresented: $showingNewPlaylist)
                .environmentObject(session)
                .environmentObject(library)
        }
        .task {
            guard !didRestore else { return }
            didRestore = true
            await session.restoreAndConnect()
            await prepareMusic()
        }
        .task(id: session.activeEndpoint?.url) {
            guard session.client != nil else { return }
            await prepareMusic()
        }
        .alert(
            "HomeOS-Music",
            isPresented: Binding(
                get: { library.message != nil },
                set: { if !$0 { library.message = nil } }
            )
        ) {
            Button("OK") {
                library.message = nil
            }
        } message: {
            Text(library.message ?? "")
        }
    }

    private var sidebar: some View {
        List(selection: $destination) {
            Section("HomeOS-Music") {
                Label("Listen Now", systemImage: "play.circle.fill")
                    .tag(HomeOSMusicDestination.listenNow)
                Label("Search", systemImage: "magnifyingglass")
                    .tag(HomeOSMusicDestination.search)
                Label("Radio", systemImage: "radio.fill")
                    .tag(HomeOSMusicDestination.radio)
            }

            Section("Library") {
                Label("Loved Songs", systemImage: "heart.fill")
                    .tag(HomeOSMusicDestination.loved)
                Label("Recently Played", systemImage: "clock.fill")
                    .tag(HomeOSMusicDestination.recent)
                Label("Albums", systemImage: "square.stack.fill")
                    .tag(HomeOSMusicDestination.albums)
            }

            Section {
                ForEach(library.playlists) { playlist in
                    Label {
                        VStack(alignment: .leading, spacing: 1) {
                            Text(playlist.name).lineLimit(1)
                            Text("\(playlist.trackCount) songs")
                                .font(.caption)
                                .foregroundStyle(.secondary)
                        }
                    } icon: {
                        Image(systemName: "music.note.list")
                    }
                    .tag(HomeOSMusicDestination.playlist(playlist.id))
                }
            } header: {
                HStack {
                    Text("Playlists")
                    Spacer()
                    Button {
                        showingNewPlaylist = true
                    } label: {
                        Image(systemName: "plus")
                    }
                    .buttonStyle(.borderless)
                    .help("New Playlist")
                }
            }
        }
        .listStyle(.sidebar)
        .navigationSplitViewColumnWidth(min: 190, ideal: 220, max: 270)
    }

    @ViewBuilder
    private var destinationView: some View {
        switch destination {
        case .listenNow:
            MusicListenNowView()
        case .search:
            MusicSearchView()
        case .radio:
            MusicRadioView()
        case .loved:
            MusicTrackCollectionView(
                title: "Loved Songs",
                subtitle: "Songs you’ve marked as favourites",
                tracks: library.likedTracks,
                symbol: "heart.fill"
            )
        case .recent:
            MusicTrackCollectionView(
                title: "Recently Played",
                subtitle: "Your listening history",
                tracks: library.recentTracks,
                symbol: "clock.fill"
            )
        case .albums:
            MusicAlbumsLibraryView()
        case .playlist(let id):
            MusicPlaylistDetailView(playlistID: id) {
                destination = .listenNow
            }
        }
    }

    private var connectionBadge: some View {
        Label(
            session.connectionDescription,
            systemImage: session.isConnected
                ? "checkmark.circle.fill"
                : "exclamationmark.triangle.fill"
        )
        .foregroundStyle(session.isConnected ? .green : .orange)
    }

    private func prepareMusic() async {
        player.connect(client: session.client)
        async let libraryLoad: Void = library.load(using: session.client)
        async let radioLoad: Void = radio.load(using: session.client)
        _ = await (libraryLoad, radioLoad)
    }
}
