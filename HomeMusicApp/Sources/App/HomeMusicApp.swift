import SwiftUI

@main
struct HomeMusicApp: App {
    @StateObject private var session = AppSession()
    @StateObject private var player = PlayerManager()

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
            .tint(.homeMusicRed)
            .preferredColorScheme(nil)
        }
    }
}

extension Color {
    static let homeMusicRed = Color(red: 0.98, green: 0.16, blue: 0.29)
}
