import SwiftUI

struct RootView: View {
    @Environment(\.scenePhase) private var scenePhase
    @EnvironmentObject private var session: AppSession
    @EnvironmentObject private var player: PlayerManager
    @EnvironmentObject private var offlineMusic: OfflineMusicStore
    @StateObject private var library = MusicLibraryStore()
    @StateObject private var radio = RadioStore()
    @State private var showNowPlaying = false

    @ViewBuilder
    var body: some View {
        if #available(iOS 26.1, *) {
            tabView
                .tabViewBottomAccessory(isEnabled: player.currentTrack != nil) {
                    TabBarPlayerAccessory { showNowPlaying = true }
                }
                .sheet(isPresented: $showNowPlaying) {
                    NowPlayingView()
                        .environmentObject(session)
                        .environmentObject(player)
                        .environmentObject(offlineMusic)
                        .environmentObject(library)
                        .environmentObject(radio)
                        .presentationDetents([.large])
                        .presentationDragIndicator(.visible)
                        .presentationBackground {
                            PlayerBackground(url: player.currentTrack?.thumbnail)
                        }
                        .interactiveDismissDisabled(false)
                }
                .environmentObject(library)
                .environmentObject(radio)
                .task { await prepare() }
                .onChange(of: scenePhase) { _, phase in
                    guard phase == .active else { return }
                    Task { await maintainAutomaticCache() }
                }
        } else {
            tabView
                .safeAreaInset(edge: .bottom, spacing: 0) {
                    if player.currentTrack != nil {
                        MiniPlayerView { showNowPlaying = true }
                    }
                }
                .sheet(isPresented: $showNowPlaying) {
                    NowPlayingView()
                        .environmentObject(session)
                        .environmentObject(player)
                        .environmentObject(offlineMusic)
                        .environmentObject(library)
                        .environmentObject(radio)
                        .presentationDetents([.large])
                        .presentationDragIndicator(.visible)
                        .presentationBackground {
                            PlayerBackground(url: player.currentTrack?.thumbnail)
                        }
                        .interactiveDismissDisabled(false)
                }
                .environmentObject(library)
                .environmentObject(radio)
                .task { await prepare() }
                .onChange(of: scenePhase) { _, phase in
                    guard phase == .active else { return }
                    Task { await maintainAutomaticCache() }
                }
        }
    }

    private var tabView: some View {
        TabView {
            ListenNowView()
                .tabItem { Label("Listen Now", systemImage: "play.circle.fill") }
            SearchView()
                .tabItem { Label("Search", systemImage: "magnifyingglass") }
            GenresView()
                .tabItem { Label("Genres", systemImage: "square.grid.2x2.fill") }
            RadioView()
                .tabItem { Label("Radio", systemImage: "radio.fill") }
            LibraryView()
                .tabItem { Label("Library", systemImage: "music.note.list") }
        }
    }

    private func prepare() async {
        offlineMusic.connect(client: session.client)
        player.connect(session: session, offlineMusic: offlineMusic)
        library.restoreOfflinePlaylists(offlineMusic.downloadedPlaylists)
        async let libraryLoad: Void = library.load(using: session.client)
        async let radioLoad: Void = radio.load(using: session.client)
        _ = await (libraryLoad, radioLoad)
        offlineMusic.reconcile(playlists: library.playlists)
        await offlineMusic.resumeIncompleteDownloads(playlists: library.playlists)
        await maintainAutomaticCache()
    }

    private func maintainAutomaticCache(force: Bool = false) async {
        guard force || offlineMusic.automaticMaintenanceDue,
              let client = session.client,
              let candidates = try? await client.automaticCacheCandidates() else {
            return
        }
        await offlineMusic.maintainAutomaticCache(
            candidates: candidates,
            force: force
        )
    }
}

@available(iOS 26.1, *)
private struct TabBarPlayerAccessory: View {
    @Environment(\.tabViewBottomAccessoryPlacement) private var placement
    @EnvironmentObject private var player: PlayerManager
    let open: () -> Void

    var body: some View {
        HStack(spacing: placement == .inline ? 8 : 12) {
            Button(action: open) {
                HStack(spacing: placement == .inline ? 8 : 12) {
                    if let track = player.currentTrack {
                        PlayerArtworkView(image: player.artworkImage)
                            .frame(
                                width: placement == .inline ? 30 : 44,
                                height: placement == .inline ? 30 : 44
                            )
                        VStack(alignment: .leading, spacing: 1) {
                            Text(track.title)
                                .font(placement == .inline ? .caption.weight(.semibold) : .subheadline.weight(.semibold))
                                .lineLimit(1)
                            if placement != .inline {
                                Text(track.artist)
                                    .font(.caption)
                                    .foregroundStyle(.secondary)
                                    .lineLimit(1)
                            }
                        }
                    }
                }
                .frame(maxWidth: .infinity, alignment: .leading)
                .contentShape(Rectangle())
            }
            .buttonStyle(.plain)
            .frame(maxWidth: .infinity, alignment: .leading)

            Button(action: player.togglePlayback) {
                if player.isBuffering {
                    ProgressView().controlSize(.small)
                } else {
                    Image(systemName: player.isPlaying ? "pause.fill" : "play.fill")
                }
            }
            .frame(width: 36, height: 36)

            if placement != .inline {
                Button(action: player.playNext) {
                    Image(systemName: "forward.fill")
                }
                .frame(width: 36, height: 36)
            }
        }
        .padding(.horizontal, placement == .inline ? 4 : 10)
    }
}

struct GlobalAmbientBackgroundView: View {
    @EnvironmentObject private var player: PlayerManager
    @AppStorage("enableGlobalAmbientLights") private var enableGlobalAmbientLights = true

    var body: some View {
        if enableGlobalAmbientLights {
            ZStack {
                Color.black

                if let artwork = player.artworkImage {
                    Image(uiImage: artwork)
                        .resizable()
                        .scaledToFill()
                        .saturation(1.4)
                        .blur(radius: 60)
                        .opacity(0.35)
                }

                RadialGradient(
                    colors: [.clear, Color.black.opacity(0.60)],
                    center: .center,
                    startRadius: 120,
                    endRadius: 750
                )
            }
            .ignoresSafeArea()
            .allowsHitTesting(false)
        }
    }
}
