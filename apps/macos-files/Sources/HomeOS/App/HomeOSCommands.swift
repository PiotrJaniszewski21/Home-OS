import SwiftUI

struct HomeOSCommands: Commands {
    @ObservedObject var appState: AppState
    @ObservedObject var connection: ConnectionManager
    @ObservedObject var fileProviderDomain: FileProviderDomainService

    @Environment(\.openSettings) private var openSettings
    @Environment(\.openWindow) private var openWindow

    var body: some Commands {
        CommandGroup(replacing: .newItem) {
            Button("Open Home OS") {
                openDashboard()
            }
            .keyboardShortcut("0", modifiers: [.command])
        }

        CommandMenu("Home OS") {
            Button("Dashboard") {
                openDashboard()
            }
            .keyboardShortcut("1", modifiers: [.command])

            Button("Files in Finder") {
                Task { await fileProviderDomain.openInFinder(using: appState) }
            }
            .keyboardShortcut("2", modifiers: [.command])
            .disabled(!appState.isConfigured || fileProviderDomain.state == .installing)

            Button("Refresh Status") {
                Task { await connection.refreshDashboard(appState: appState) }
            }
            .keyboardShortcut("r", modifiers: [.command])
            .disabled(!appState.isConfigured || connection.isConnecting)

            Divider()

            Button("Settings…") {
                openSettings()
            }
            .keyboardShortcut(",", modifiers: [.command])
        }
    }

    private func openDashboard() {
        AppWindow.openMainWindow(openWindow: openWindow)
    }
}
