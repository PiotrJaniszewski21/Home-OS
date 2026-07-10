import SwiftUI

struct DashboardView: View {
    @EnvironmentObject private var appState: AppState
    @EnvironmentObject private var connection: ConnectionManager

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 20) {
                header
                if let error = connection.lastError, !error.isEmpty {
                    Label(error, systemImage: "exclamationmark.triangle.fill")
                        .font(.callout)
                        .foregroundStyle(.orange)
                        .padding(12)
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .background(.thinMaterial, in: RoundedRectangle(cornerRadius: 12, style: .continuous))
                        .textSelection(.enabled)
                }
                if let metrics = appState.metrics {
                    metricGrid(metrics: metrics, storage: appState.storage)
                    if let storage = appState.storage {
                        storageSection(storage: storage)
                    } else if let error = connection.lastError {
                        ContentUnavailableView("Storage status unavailable", systemImage: "internaldrive.badge.exclamationmark", description: Text(error))
                    }
                } else if connection.isConnected {
                    ProgressView("Loading server status…")
                        .frame(maxWidth: .infinity, minHeight: 240)
                } else {
                    ContentUnavailableView("Server unavailable", systemImage: "wifi.slash", description: Text(connection.lastError ?? "Open Settings to reconnect."))
                }
            }
            .padding(24)
        }
        .navigationTitle("Dashboard")
        .toolbar {
            Button("Refresh", systemImage: "arrow.clockwise") {
                Task { await connection.refreshDashboard(appState: appState) }
            }
        }
        .task { await connection.refreshDashboard(appState: appState) }
    }

    private var header: some View {
        VStack(alignment: .leading, spacing: 6) {
            Text("Welcome, \(appState.username.isEmpty ? "Home OS user" : appState.username)")
                .font(.largeTitle.bold())
            Text(appState.activeConnectionDescription)
                .foregroundStyle(.secondary)
                .textSelection(.enabled)
            if !appState.localServerURL.isEmpty, appState.activeConnectionKind == ConnectionEndpoint.Kind.local.rawValue {
                Text("Domain fallback: \(appState.serverURL)")
                    .font(.caption)
                    .foregroundStyle(.tertiary)
                    .textSelection(.enabled)
            }
        }
    }

    private func metricGrid(metrics: MetricsData, storage: StorageData?) -> some View {
        LazyVGrid(columns: [GridItem(.adaptive(minimum: 190), spacing: 14)], spacing: 14) {
            StatCard(title: "CPU", value: Formatters.percent(metrics.cpuPercent), subtitle: "\(metrics.cpuCount) cores", systemImage: "cpu")
            StatCard(title: "Memory", value: Formatters.percent(metrics.memory.percent), subtitle: "\(String(format: "%.1f", metrics.memory.usedGB)) / \(String(format: "%.1f", metrics.memory.totalGB)) GB", systemImage: "memorychip")
            StatCard(title: "Storage", value: storage.map { Formatters.percent($0.main.percentUsed) } ?? "—", subtitle: storage.map { "\(Formatters.byteString($0.main.freeBytes)) free" } ?? "Status unavailable", systemImage: "internaldrive")
            StatCard(title: "Uptime", value: metrics.uptime, subtitle: metrics.hostname, systemImage: "clock")
            StatCard(title: "Network", value: Formatters.byteString(Int64(metrics.network.recvGB * 1_073_741_824)), subtitle: "received total", systemImage: "network")
        }
    }

    private func storageSection(storage: StorageData) -> some View {
        VStack(alignment: .leading, spacing: 12) {
            Text("Storage")
                .font(.title2.bold())
            StorageRow(name: "Main Storage", used: storage.main.usedBytes, total: storage.main.totalBytes, percent: storage.main.percentUsed)
            ForEach(storage.drives) { drive in
                StorageRow(name: drive.name, used: drive.usedBytes, total: drive.totalBytes, percent: drive.percentUsed)
            }
        }
    }
}

private struct StatCard: View {
    let title: String
    let value: String
    let subtitle: String
    let systemImage: String

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            Label(title, systemImage: systemImage)
                .font(.headline)
                .foregroundStyle(.secondary)
            Text(value)
                .font(.system(size: 30, weight: .semibold, design: .rounded))
            Text(subtitle)
                .font(.caption)
                .foregroundStyle(.secondary)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(16)
        .background(.regularMaterial, in: RoundedRectangle(cornerRadius: 16, style: .continuous))
    }
}

private struct StorageRow: View {
    let name: String
    let used: Int64
    let total: Int64
    let percent: Double

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack {
                Text(name).font(.headline)
                Spacer()
                Text("\(Formatters.byteString(used)) of \(Formatters.byteString(total))")
                    .foregroundStyle(.secondary)
            }
            ProgressView(value: percent / 100)
        }
        .padding(14)
        .background(.thinMaterial, in: RoundedRectangle(cornerRadius: 14, style: .continuous))
    }
}
