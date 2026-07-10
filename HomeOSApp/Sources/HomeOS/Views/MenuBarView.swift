import AppKit
import SwiftUI

struct MenuBarView: View {
    @EnvironmentObject private var appState: AppState
    @EnvironmentObject private var connection: ConnectionManager
    @EnvironmentObject private var fileProviderDomain: FileProviderDomainService
    @Environment(\.openSettings) private var openSettings
    @Environment(\.openWindow) private var openWindow

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            HStack {
                Circle()
                    .fill(connection.isConnecting ? .orange : (connection.isConnected ? .green : .red))
                    .frame(width: 9, height: 9)
                Text(connection.isConnecting ? "Checking…" : (connection.isConnected ? "Connected" : "Disconnected"))
                    .font(.headline)
                Spacer()
            }

            if let error = connection.lastError, !error.isEmpty {
                Text(error)
                    .font(.caption)
                    .foregroundStyle(.orange)
                    .lineLimit(3)
                    .textSelection(.enabled)
            }

            if appState.isConfigured {
                VStack(alignment: .leading, spacing: 5) {
                    Text(appState.username.isEmpty ? "Home OS" : appState.username)
                        .font(.subheadline.weight(.semibold))
                    Text(appState.activeConnectionDescription)
                        .font(.caption)
                        .foregroundStyle(.secondary)
                        .lineLimit(1)
                }

                if let storage = appState.storage?.main {
                    VStack(alignment: .leading, spacing: 5) {
                        HStack {
                            Text("Storage")
                            Spacer()
                            Text(Formatters.percent(storage.percentUsed))
                        }
                        .font(.caption)
                        ProgressView(value: storage.percentUsed / 100)
                    }
                }
            } else {
                Text("Open Settings to connect your server.")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }

            Divider()

            Button("Open Home OS", systemImage: "macwindow") {
                AppWindow.openMainWindow(openWindow: openWindow)
            }
            .disabled(!appState.isConfigured)

            Button("Refresh Status", systemImage: "arrow.clockwise") {
                Task { await connection.refreshDashboard(appState: appState) }
            }
            .disabled(!appState.isConfigured)

            Button("Enable Finder Folder", systemImage: "externaldrive.badge.plus") {
                Task { await fileProviderDomain.install(using: appState, requestDesktopShortcutAccess: true) }
            }
            .disabled(!appState.isConfigured || fileProviderDomain.state == .installing)

            Button("Open Finder Folder", systemImage: "folder") {
                Task { await fileProviderDomain.openInFinder(using: appState) }
            }
            .disabled(!appState.isConfigured || fileProviderDomain.state == .installing)

            Button("Create Desktop Shortcut", systemImage: "desktopcomputer") {
                Task { await fileProviderDomain.createDesktopShortcut(using: appState) }
            }
            .disabled(!appState.isConfigured || fileProviderDomain.state == .installing)

            Button("Open File Provider Settings", systemImage: "gearshape") {
                fileProviderDomain.openFileProviderSettings()
            }
            .disabled(fileProviderDomain.state == .installing)

            Button("Refresh Finder Folder", systemImage: "arrow.triangle.2.circlepath") {
                Task {
                    appState.saveSharedSettings()
                    try? await fileProviderDomain.signalRootChanged()
                }
            }
            .disabled(!appState.isConfigured || fileProviderDomain.state == .installing)

            Divider()

            if let detail = fileProviderDomain.state.detail {
                Text(detail)
                    .font(.caption)
                    .foregroundStyle(.secondary)
                    .lineLimit(3)
                    .textSelection(.enabled)
            }

            HStack {
                Button("Settings…") { openSettings() }
                Spacer()
                Button("Quit") { NSApplication.shared.terminate(nil) }
            }
        }
        .padding(14)
        .frame(width: 280)
        .task {
            connection.restoreSession(from: appState)
            await fileProviderDomain.ensureInstalled(using: appState)
            await connection.refreshDashboard(appState: appState)
        }
    }

}
