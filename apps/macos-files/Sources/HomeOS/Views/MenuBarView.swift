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
                    .fill(connectionStatusColor)
                    .frame(width: 9, height: 9)
                Text(connectionStatusTitle)
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
                Grid(alignment: .leading, horizontalSpacing: 10, verticalSpacing: 7) {
                    GridRow {
                        Label("Route", systemImage: connectionRouteIcon)
                        Text(connectionRouteTitle)
                            .foregroundStyle(.secondary)
                    }
                    if let lastHealthCheck = connection.lastHealthCheck {
                        GridRow {
                            Label("Checked", systemImage: "clock")
                            Text(lastHealthCheck, style: .relative)
                                .foregroundStyle(.secondary)
                        }
                    }
                }
                .font(.caption)

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

            HStack(spacing: 8) {
                quickAction("Dashboard", systemImage: "gauge.with.dots.needle.67percent") {
                    openDashboard()
                }
                quickAction("Files", systemImage: "folder") {
                    Task { await fileProviderDomain.openInFinder(using: appState) }
                }
            }

            Button("Refresh Status", systemImage: "arrow.clockwise") {
                Task { await connection.refreshDashboard(appState: appState) }
            }
            .disabled(!appState.isConfigured)

            Divider()

            HStack {
                Button("Settings…") { openSettings() }
                Spacer()
                Button("Quit") { NSApplication.shared.terminate(nil) }
            }
        }
        .padding(14)
        .frame(width: 310)
        .task {
            connection.restoreSession(from: appState)
            await connection.refreshDashboard(appState: appState)
        }
    }

    private var connectionStatusTitle: String {
        if connection.isConnecting { return "Checking…" }
        guard connection.isConnected else { return "Disconnected" }
        return appState.activeConnectionKind == ConnectionEndpoint.Kind.local.rawValue
            ? "Connected locally"
            : "Connected via domain"
    }

    private var connectionStatusColor: Color {
        guard connection.isConnected else { return .red }
        return appState.activeConnectionKind == ConnectionEndpoint.Kind.local.rawValue ? .green : .blue
    }

    private var connectionRouteTitle: String {
        if connection.isConnecting { return "Checking…" }
        guard connection.isConnected else { return "Unavailable" }
        if appState.activeConnectionKind == ConnectionEndpoint.Kind.local.rawValue { return "Local network" }
        if appState.activeConnectionKind == ConnectionEndpoint.Kind.remote.rawValue { return "Domain" }
        return "Connected"
    }

    private var connectionRouteIcon: String {
        if connection.isConnecting { return "arrow.triangle.2.circlepath" }
        guard connection.isConnected else { return "wifi.slash" }
        return appState.activeConnectionKind == ConnectionEndpoint.Kind.local.rawValue ? "house.and.flag.fill" : "globe"
    }

    private func quickAction(_ title: String, systemImage: String, action: @escaping () -> Void) -> some View {
        Button(action: action) {
            VStack(spacing: 5) {
                Image(systemName: systemImage)
                Text(title)
                    .font(.caption2)
            }
            .frame(maxWidth: .infinity)
        }
        .buttonStyle(.bordered)
        .disabled(!appState.isConfigured)
    }

    private func openDashboard() {
        AppWindow.openMainWindow(openWindow: openWindow)
    }
}
