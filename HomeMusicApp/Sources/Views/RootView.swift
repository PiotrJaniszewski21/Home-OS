import SwiftUI

struct RootView: View {
    @EnvironmentObject private var session: AppSession
    @EnvironmentObject private var player: PlayerManager
    @State private var showNowPlaying = false

    var body: some View {
        TabView {
            ListenNowView()
                .tabItem { Label("Listen Now", systemImage: "play.circle.fill") }
            SearchView()
                .tabItem { Label("Search", systemImage: "magnifyingglass") }
            LibraryView()
                .tabItem { Label("Library", systemImage: "music.note.list") }
        }
        .safeAreaInset(edge: .bottom, spacing: 0) {
            if player.currentTrack != nil {
                MiniPlayerView { showNowPlaying = true }
            }
        }
        .sheet(isPresented: $showNowPlaying) { NowPlayingView() }
        .task { player.connect(session: session) }
    }
}
