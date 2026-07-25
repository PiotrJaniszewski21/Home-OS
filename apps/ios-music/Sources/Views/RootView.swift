import SwiftUI

struct RootView: View {
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
                        .presentationDetents([.large])
                        .presentationDragIndicator(.visible)
                        .interactiveDismissDisabled(false)
                }
                .environmentObject(library)
                .environmentObject(radio)
                .task { await prepare() }
        } else {
            tabView
                .safeAreaInset(edge: .bottom, spacing: 0) {
                    if player.currentTrack != nil {
                        MiniPlayerView { showNowPlaying = true }
                    }
                }
                .sheet(isPresented: $showNowPlaying) {
                    NowPlayingView()
                        .presentationDetents([.large])
                        .presentationDragIndicator(.visible)
                        .interactiveDismissDisabled(false)
                }
                .environmentObject(library)
                .environmentObject(radio)
                .task { await prepare() }
        }
    }

    private var tabView: some View {
        TabView {
            ListenNowView()
                .tabItem { Label("Listen Now", systemImage: "play.circle.fill") }
            SearchView()
                .tabItem { Label("Search", systemImage: "magnifyingglass") }
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
