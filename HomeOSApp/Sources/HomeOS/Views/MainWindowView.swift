import SwiftUI

struct MainWindowView: View {
    @EnvironmentObject private var appState: AppState
    @EnvironmentObject private var connection: ConnectionManager
    @EnvironmentObject private var fileProviderDomain: FileProviderDomainService
    @Environment(\.openSettings) private var openSettings

    var body: some View {
        Group {
            if appState.isConfigured {
                NavigationSplitView {
                    SidebarView(selection: $appState.selectedSection)
                } detail: {
                    DetailView(section: appState.selectedSection ?? .dashboard)
                }
            } else {
                EmptyConnectionView()
            }
        }
        .toolbar {
            ToolbarItemGroup(placement: .primaryAction) {
                ConnectionBadge()
                Button("Settings", systemImage: "gear") {
                    openSettings()
                }
            }
        }
        .task {
            connection.restoreSession(from: appState)
            await fileProviderDomain.ensureInstalled(using: appState)
            await connection.refreshDashboard(appState: appState)
        }
    }
}

private struct SidebarView: View {
    @Binding var selection: AppSection?

    var body: some View {
        List(AppSection.allCases, selection: $selection) { section in
            Label(section.title, systemImage: section.systemImage)
                .tag(section)
        }
        .navigationTitle("Home OS")
        .frame(minWidth: 190)
    }
}

private struct DetailView: View {
    let section: AppSection

    var body: some View {
        switch section {
        case .dashboard:
            DashboardView()
        case .files:
            FileBrowserView()
        case .ai:
            AIChatView()
        }
    }
}

private struct EmptyConnectionView: View {
    @Environment(\.openSettings) private var openSettings

    var body: some View {
        ContentUnavailableView {
            Label("Connect to Home OS", systemImage: "externaldrive.badge.wifi")
        } description: {
            Text("Add your server URL and log in before using the native app.")
        } actions: {
            Button("Open Settings") {
                openSettings()
            }
            .buttonStyle(.borderedProminent)
        }
    }
}

private struct ConnectionBadge: View {
    @EnvironmentObject private var connection: ConnectionManager

    var body: some View {
        Label(connection.isConnected ? "Connected" : "Disconnected", systemImage: connection.isConnected ? "checkmark.circle.fill" : "exclamationmark.triangle.fill")
            .foregroundStyle(connection.isConnected ? .green : .orange)
            .labelStyle(.titleAndIcon)
    }
}
