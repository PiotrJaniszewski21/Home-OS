import SwiftUI

struct DashboardView: View {
    @EnvironmentObject private var appState: AppState
    @EnvironmentObject private var connection: ConnectionManager

    @AppStorage("dashboardLiveUpdates") private var liveUpdates = true
    @State private var previousNetworkSample: NetworkSpeedSample?
    @State private var downloadBytesPerSecond: Double?
    @State private var uploadBytesPerSecond: Double?
    @State private var isRefreshing = false

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 20) {
                DashboardHeader(
                    username: appState.username,
                    hostname: appState.metrics?.hostname,
                    connectionTitle: connectionTitle,
                    connectionColor: connectionColor,
                    uptime: appState.metrics?.uptime,
                    route: appState.activeConnectionDescription,
                    lastUpdated: connection.lastHealthCheck
                )

                if let error = connection.lastError, !error.isEmpty {
                    DashboardWarning(message: error)
                }

                if let metrics = appState.metrics {
                    metricGrid(metrics: metrics)
                    HStack(alignment: .top, spacing: 14) {
                        NetworkActivityCard(
                            downloadBytesPerSecond: downloadBytesPerSecond,
                            uploadBytesPerSecond: uploadBytesPerSecond,
                            totalReceivedGB: metrics.network.recvGB,
                            totalSentGB: metrics.network.sentGB
                        )
                        .frame(maxWidth: .infinity)

                        SystemInformationCard(
                            hostname: metrics.hostname,
                            platform: metrics.platform,
                            pythonVersion: metrics.pythonVersion,
                            uptime: metrics.uptime,
                            route: appState.activeConnectionDescription,
                            role: appState.userRole
                        )
                        .frame(maxWidth: .infinity)
                    }

                    if let storage = appState.storage {
                        StorageOverview(storage: storage)
                    }
                } else if connection.isConnected {
                    ProgressView("Loading server status…")
                        .frame(maxWidth: .infinity, minHeight: 240)
                } else {
                    ContentUnavailableView(
                        "Server unavailable",
                        systemImage: "wifi.slash",
                        description: Text(connection.lastError ?? "Open Settings to reconnect.")
                    )
                }
            }
            .padding(26)
        }
        .navigationTitle("Dashboard")
        .toolbar {
            Toggle("Live Updates", systemImage: "waveform.path.ecg", isOn: $liveUpdates)
            Button("Refresh", systemImage: "arrow.clockwise") {
                Task { await refreshAll() }
            }
            .disabled(isRefreshing)
        }
        .task { await monitorDashboard() }
    }

    private var connectionTitle: String {
        guard connection.isConnected else { return "Offline" }
        return appState.activeConnectionKind == ConnectionEndpoint.Kind.local.rawValue ? "Local" : "Domain"
    }

    private var connectionColor: Color {
        guard connection.isConnected else { return .red }
        return appState.activeConnectionKind == ConnectionEndpoint.Kind.local.rawValue ? .green : .blue
    }

    private func metricGrid(metrics: MetricsData) -> some View {
        LazyVGrid(columns: Array(repeating: GridItem(.flexible(), spacing: 14), count: 4), spacing: 14) {
            DashboardMetricCard(
                title: "CPU",
                value: Formatters.percent(metrics.cpuPercent),
                subtitle: "\(metrics.cpuCount) cores",
                systemImage: "cpu",
                progress: metrics.cpuPercent / 100,
                tint: metricColor(metrics.cpuPercent)
            )
            DashboardMetricCard(
                title: "Memory",
                value: "\(String(format: "%.1f", metrics.memory.usedGB)) GB",
                subtitle: "of \(String(format: "%.1f", metrics.memory.totalGB)) GB · \(Formatters.percent(metrics.memory.percent))",
                systemImage: "memorychip",
                progress: metrics.memory.percent / 100,
                tint: metricColor(metrics.memory.percent)
            )
            DashboardMetricCard(
                title: "Disk",
                value: "\(String(format: "%.1f", metrics.disk.usedGB)) GB",
                subtitle: "of \(String(format: "%.1f", metrics.disk.totalGB)) GB · \(Formatters.percent(metrics.disk.percent))",
                systemImage: "internaldrive",
                progress: metrics.disk.percent / 100,
                tint: metricColor(metrics.disk.percent)
            )
            DashboardMetricCard(
                title: "Network",
                value: NetworkActivityCard.speedString(downloadBytesPerSecond),
                subtitle: "↓ download · ↑ \(NetworkActivityCard.speedString(uploadBytesPerSecond))",
                systemImage: "network",
                progress: nil,
                tint: .green
            )
        }
    }

    private func monitorDashboard() async {
        await refreshAll()
        if downloadBytesPerSecond == nil || uploadBytesPerSecond == nil {
            do {
                try await Task.sleep(for: .seconds(1))
            } catch {
                return
            }
            await refreshNetworkSpeed()
        }
        while !Task.isCancelled {
            do {
                try await Task.sleep(for: .seconds(3))
            } catch {
                return
            }
            guard liveUpdates else { continue }
            await connection.refreshLiveMetrics(appState: appState)
            await refreshNetworkSpeed()
        }
    }

    private func refreshAll() async {
        isRefreshing = true
        await connection.refreshDashboard(appState: appState)
        await refreshNetworkSpeed()
        isRefreshing = false
    }

    private func refreshNetworkSpeed() async {
        guard let client = connection.client else { return }
        do {
            let response = try await client.getNetworkSpeed()
            guard response.ok, let sample = response.data else { return }
            if let previousNetworkSample {
                let elapsed = sample.timestamp - previousNetworkSample.timestamp
                let countersDidNotReset = sample.bytesReceived >= previousNetworkSample.bytesReceived
                    && sample.bytesSent >= previousNetworkSample.bytesSent
                if elapsed > 0, countersDidNotReset {
                    downloadBytesPerSecond = max(0, Double(sample.bytesReceived - previousNetworkSample.bytesReceived) / elapsed)
                    uploadBytesPerSecond = max(0, Double(sample.bytesSent - previousNetworkSample.bytesSent) / elapsed)
                }
            }
            previousNetworkSample = sample
        } catch {
            downloadBytesPerSecond = nil
            uploadBytesPerSecond = nil
        }
    }

    private func metricColor(_ percent: Double) -> Color {
        if percent >= 90 { return .red }
        if percent >= 70 { return .orange }
        return .blue
    }
}
