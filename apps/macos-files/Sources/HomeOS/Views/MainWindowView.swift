import SwiftUI

struct MainWindowView: View {
    @EnvironmentObject private var appState: AppState
    @EnvironmentObject private var connection: ConnectionManager
    @EnvironmentObject private var fileProviderDomain: FileProviderDomainService
    @Environment(\.openSettings) private var openSettings

    var body: some View {
        Group {
            if appState.isConfigured {
                DashboardView()
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
            guard !Self.isRunningTests else { return }
            connection.restoreSession(from: appState)
            await fileProviderDomain.ensureInstalled(using: appState)
            await connection.refreshDashboard(appState: appState)
        }
    }

    private static var isRunningTests: Bool {
        ProcessInfo.processInfo.environment["XCTestConfigurationFilePath"] != nil
    }
}

private struct EmptyConnectionView: View {
    @Environment(\.openSettings) private var openSettings

    var body: some View {
        ContentUnavailableView {
            Label("Connect to Home OS", systemImage: "externaldrive.badge.wifi")
        } description: {
            Text("Add your server URL and log in before using HomeOS.")
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
        Label(
            connection.isConnected ? "Connected" : "Disconnected",
            systemImage: connection.isConnected
                ? "checkmark.circle.fill"
                : "exclamationmark.triangle.fill"
        )
        .foregroundStyle(connection.isConnected ? .green : .orange)
        .labelStyle(.titleAndIcon)
    }
}
