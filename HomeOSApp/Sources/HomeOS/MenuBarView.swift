import SwiftUI

struct MenuBarView: View {
    @EnvironmentObject var connection: ConnectionManager
    @EnvironmentObject var appState: AppState
    @Environment(\.openSettings) private var openSettings
    @Environment(\.openWindow) private var openWindow

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            // Header
            HStack {
                Circle()
                    .fill(connection.isConnected ? .green : .red)
                    .frame(width: 8, height: 8)
                Text(connection.isConnected ? "Connected" : "Disconnected")
                    .font(.caption)
                    .foregroundStyle(.secondary)
                Spacer()
                if let metrics = appState.systemMetrics {
                    Text("CPU \(Int(metrics.cpuPercent))%")
                        .font(.caption2)
                        .foregroundStyle(.secondary)
                }
            }

            if !appState.isConfigured {
                VStack(spacing: 8) {
                    Text("Not configured")
                        .font(.headline)
                    Text("Open Settings to connect")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
                .frame(maxWidth: .infinity)
                .padding(.vertical, 8)
            } else if connection.isConnected {
                if let storage = appState.storageInfo {
                    VStack(alignment: .leading, spacing: 4) {
                        Text("Storage")
                            .font(.caption)
                            .foregroundStyle(.secondary)
                        ProgressView(value: storage.percentUsed / 100)
                            .tint(.blue)
                        HStack {
                            Text("\(String(format: "%.1f", storage.usedGB)) GB used")
                            Spacer()
                            Text("\(String(format: "%.1f", storage.freeGB)) GB free")
                        }
                        .font(.caption2)
                        .foregroundStyle(.secondary)
                    }
                }

                Divider()

                Button(action: openMainWindow) {
                    Label("Open Home OS", systemImage: "macwindow")
                }
                .buttonStyle(.plain)
            }

            Divider()

            HStack {
                Button("Settings...") {
                    openSettings()
                }
                .buttonStyle(.plain)
                .font(.caption)

                Spacer()

                Button("Quit") {
                    NSApplication.shared.terminate(nil)
                }
                .buttonStyle(.plain)
                .font(.caption)
                .foregroundStyle(.red)
            }
        }
        .padding(12)
        .frame(width: 240)
        .onAppear(perform: connectIfConfigured)
    }

    private func connectIfConfigured() {
        guard appState.isConfigured, !appState.authToken.isEmpty else { return }
        connection.connect(serverURL: appState.serverURL, token: appState.authToken)
        refreshData()
    }

    private func refreshData() {
        guard let client = connection.client else { return }
        Task {
            if let metrics = try? await client.getMetrics(), let data = metrics.data {
                appState.systemMetrics = SystemMetrics(
                    cpuPercent: data.cpu_percent,
                    memoryPercent: data.memory.percent,
                    memoryUsedGB: data.memory.used_gb,
                    memoryTotalGB: data.memory.total_gb,
                    uptime: data.uptime
                )
            }
            if let storage = try? await client.getStorageInfo(), let data = storage.data {
                appState.storageInfo = StorageInfo(
                    totalGB: Double(data.main.total_bytes) / 1_073_741_824,
                    usedGB: Double(data.main.used_bytes) / 1_073_741_824,
                    freeGB: Double(data.main.free_bytes) / 1_073_741_824
                )
            }
        }
    }

    private func openMainWindow() {
        NSApplication.shared.setActivationPolicy(.regular)
        NSApplication.shared.activate(ignoringOtherApps: true)
        openWindow(id: "main")
    }
}
