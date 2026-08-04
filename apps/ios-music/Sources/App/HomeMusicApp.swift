import SwiftUI

@main
struct HomeMusicApp: App {
    @StateObject private var session = AppSession()
    @StateObject private var player = PlayerManager()
    @StateObject private var offlineMusic = OfflineMusicStore()

    var body: some Scene {
        WindowGroup {
            Group {
                if session.isSignedIn {
                    RootView()
                } else {
                    LoginView()
                }
            }
            .environmentObject(session)
            .environmentObject(player)
            .environmentObject(offlineMusic)
            .tint(.homeMusicRed)
            .preferredColorScheme(nil)
            .onAppear {
                AutomaticCacheRefresh.schedule()
            }
            .onChange(of: session.isSignedIn) { _, isSignedIn in
                if !isSignedIn {
                    player.stop()
                    offlineMusic.disconnect()
                }
            }
        }
        .backgroundTask(.appRefresh(AutomaticCacheRefresh.identifier)) {
            await refreshAutomaticCache()
            AutomaticCacheRefresh.schedule()
        }
    }

    private func refreshAutomaticCache() async {
        guard let client = session.client,
              let candidates = try? await client.automaticCacheCandidates() else {
            return
        }
        offlineMusic.connect(client: client)
        await offlineMusic.maintainAutomaticCache(
            candidates: candidates,
            force: true
        )
    }
}

extension Color {
    static let homeMusicRed = Color(red: 0.98, green: 0.16, blue: 0.29)
}
