import SwiftUI

struct DashboardView: View {
    @EnvironmentObject var connection: ConnectionManager
    @State private var metrics: MetricsData?
    @State private var timer: Timer?

    var body: some View {
        ScrollView {
            if let m = metrics {
                VStack(spacing: 16) {
                    // Stats grid
                    LazyVGrid(columns: [GridItem(.flexible()), GridItem(.flexible())], spacing: 12) {
                        StatCard(title: "CPU", value: "\(Int(m.cpu_percent))%", progress: m.cpu_percent / 100, color: .blue)
                        StatCard(title: "Memory", value: "\(String(format: "%.1f", m.memory.used_gb)) / \(String(format: "%.1f", m.memory.total_gb)) GB", progress: m.memory.percent / 100, color: .green)
                        StatCard(title: "Disk", value: "\(String(format: "%.1f", m.disk.used_gb)) / \(String(format: "%.1f", m.disk.total_gb)) GB", progress: m.disk.percent / 100, color: m.disk.percent > 90 ? .red : .orange)
                        StatCard(title: "Uptime", value: m.uptime, progress: nil, color: .purple)
                    }

                    // System info
                    GroupBox("System") {
                        VStack(alignment: .leading, spacing: 6) {
                            InfoRow(label: "Hostname", value: m.hostname)
                            InfoRow(label: "CPU Cores", value: "\(m.cpu_count)")
                            InfoRow(label: "Network Sent", value: "\(String(format: "%.2f", m.network.sent_gb)) GB")
                            InfoRow(label: "Network Received", value: "\(String(format: "%.2f", m.network.recv_gb)) GB")
                        }
                        .padding(4)
                    }
                }
                .padding()
            } else {
                VStack {
                    ProgressView()
                    Text("Loading metrics...")
                        .foregroundStyle(.secondary)
                }
                .frame(maxWidth: .infinity, maxHeight: .infinity)
                .padding(.top, 60)
            }
        }
        .onAppear(perform: startPolling)
        .onDisappear { timer?.invalidate() }
    }

    private func startPolling() {
        fetchMetrics()
        timer = Timer.scheduledTimer(withTimeInterval: 5, repeats: true) { _ in
            fetchMetrics()
        }
    }

    private func fetchMetrics() {
        guard let client = connection.client else { return }
        Task {
            if let response = try? await client.getMetrics(), let data = response.data {
                await MainActor.run { metrics = data }
            }
        }
    }
}

struct StatCard: View {
    let title: String
    let value: String
    let progress: Double?
    let color: Color

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text(title)
                .font(.caption)
                .foregroundStyle(.secondary)
            Text(value)
                .font(.title2)
                .fontWeight(.semibold)
            if let progress {
                ProgressView(value: progress)
                    .tint(color)
            }
        }
        .padding(12)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(.background.secondary)
        .cornerRadius(10)
    }
}

struct InfoRow: View {
    let label: String
    let value: String

    var body: some View {
        HStack {
            Text(label)
                .foregroundStyle(.secondary)
            Spacer()
            Text(value)
        }
        .font(.callout)
    }
}
