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
            .onChange(of: session.isSignedIn) { _, isSignedIn in
                if !isSignedIn {
                    player.stop()
                    offlineMusic.disconnect()
                }
            }
        }
    }
}

extension Color {
    static let homeMusicRed = Color(red: 0.98, green: 0.16, blue: 0.29)
}
