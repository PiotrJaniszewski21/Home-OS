import SwiftUI

@main
struct HomeOSApp: App {
    @NSApplicationDelegateAdaptor(AppDelegate.self) var appDelegate
    @StateObject private var connectionManager = ConnectionManager()
    @StateObject private var appState = AppState()

    var body: some Scene {
        MenuBarExtra {
            MenuBarView()
                .environmentObject(connectionManager)
                .environmentObject(appState)
        } label: {
            Image(systemName: connectionManager.isConnected ? "externaldrive.fill.badge.checkmark" : "externaldrive.fill.badge.xmark")
        }
        .menuBarExtraStyle(.window)

        Settings {
            SettingsView()
                .environmentObject(connectionManager)
                .environmentObject(appState)
        }

        Window("Home OS", id: "main") {
            MainWindowView()
                .environmentObject(connectionManager)
                .environmentObject(appState)
        }
    }
}

class AppDelegate: NSObject, NSApplicationDelegate {
    func applicationDidFinishLaunching(_ notification: Notification) {
        // Allow app to become active for text input in settings
    }
}

