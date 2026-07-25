import SwiftUI

struct DashboardHeader: View {
    let username: String
    let hostname: String?
    let connectionTitle: String
    let connectionColor: Color
    let uptime: String?
    let route: String
    let lastUpdated: Date?

    var body: some View {
        HStack(alignment: .center, spacing: 24) {
            VStack(alignment: .leading, spacing: 9) {
                Text("Welcome back, \(username.isEmpty ? "Home OS user" : username)")
                    .font(.system(size: 29, weight: .bold, design: .rounded))
                HStack(spacing: 8) {
                    Image(systemName: "server.rack")
                    Text(hostname ?? "Home OS server")
                }
                .font(.subheadline.weight(.medium))
                .foregroundStyle(.secondary)
            }

            Spacer(minLength: 16)

            HStack(spacing: 26) {
                statusItem(title: "Connection") {
                    HStack(spacing: 6) {
                        Circle()
                            .fill(connectionColor)
                            .frame(width: 8, height: 8)
                        Text(connectionTitle)
                            .foregroundStyle(.primary)
                    }
                }

                if let uptime, !uptime.isEmpty {
                    statusItem(title: "Uptime") {
                        Text(uptime)
                            .foregroundStyle(.primary)
                            .lineLimit(1)
                    }
                }

                statusItem(title: "Last updated") {
                    if let lastUpdated {
                        Text(lastUpdated, style: .relative)
                            .foregroundStyle(.primary)
                    } else {
                        Text("Not yet")
                            .foregroundStyle(.secondary)
                    }
                }
            }
            .font(.subheadline.weight(.semibold))
        }
        .padding(22)
        .background {
            ZStack {
                RoundedRectangle(cornerRadius: 20, style: .continuous)
                    .fill(.regularMaterial)
                LinearGradient(
                    colors: [connectionColor.opacity(0.12), .clear],
                    startPoint: .topLeading,
                    endPoint: .bottomTrailing
                )
                .clipShape(RoundedRectangle(cornerRadius: 20, style: .continuous))
            }
        }
        .overlay {
            RoundedRectangle(cornerRadius: 20, style: .continuous)
                .strokeBorder(.primary.opacity(0.08))
        }
        .accessibilityElement(children: .combine)
        .help(route)
    }

    private func statusItem<Content: View>(title: String, @ViewBuilder content: () -> Content) -> some View {
        VStack(alignment: .leading, spacing: 5) {
            Text(title.uppercased())
                .font(.caption2.weight(.semibold))
                .foregroundStyle(.tertiary)
            content()
        }
    }
}

struct DashboardWarning: View {
    let message: String

    var body: some View {
        Label(message, systemImage: "exclamationmark.triangle.fill")
            .font(.callout)
            .foregroundStyle(.orange)
            .padding(12)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(.orange.opacity(0.08), in: RoundedRectangle(cornerRadius: 12, style: .continuous))
            .overlay {
                RoundedRectangle(cornerRadius: 12, style: .continuous)
                    .strokeBorder(.orange.opacity(0.2))
            }
            .textSelection(.enabled)
    }
}

struct DashboardMetricCard: View {
    let title: String
    let value: String
    let subtitle: String
    let systemImage: String
    let progress: Double?
    let tint: Color

    var body: some View {
        VStack(alignment: .leading, spacing: 15) {
            HStack {
                Image(systemName: systemImage)
                    .font(.system(size: 15, weight: .semibold))
                    .foregroundStyle(tint)
                    .frame(width: 30, height: 30)
                    .background(tint.opacity(0.1), in: RoundedRectangle(cornerRadius: 8, style: .continuous))
                Spacer()
                Text(title.uppercased())
                    .font(.caption2.weight(.bold))
                    .foregroundStyle(.tertiary)
            }

            VStack(alignment: .leading, spacing: 5) {
                Text(value)
                    .font(.system(size: 26, weight: .semibold, design: .rounded))
                    .monospacedDigit()
                    .lineLimit(1)
                    .minimumScaleFactor(0.7)
                Text(subtitle)
                    .font(.caption)
                    .foregroundStyle(.secondary)
                    .lineLimit(1)
            }

            if let progress {
                ProgressView(value: min(max(progress, 0), 1))
                    .tint(tint)
                    .controlSize(.small)
            }
        }
        .frame(maxWidth: .infinity, minHeight: 122, alignment: .leading)
        .padding(16)
        .dashboardPanel()
    }
}

struct NetworkActivityCard: View {
    let downloadBytesPerSecond: Double?
    let uploadBytesPerSecond: Double?
    let totalReceivedGB: Double
    let totalSentGB: Double

    var body: some View {
        DashboardPanel(title: "Network Activity", systemImage: "network") {
            HStack(spacing: 12) {
                rate(
                    title: "Download",
                    value: downloadBytesPerSecond,
                    total: "\(String(format: "%.2f", totalReceivedGB)) GB received",
                    systemImage: "arrow.down",
                    tint: .green
                )
                Divider()
                rate(
                    title: "Upload",
                    value: uploadBytesPerSecond,
                    total: "\(String(format: "%.2f", totalSentGB)) GB sent",
                    systemImage: "arrow.up",
                    tint: .blue
                )
            }
        }
    }

    private func rate(title: String, value: Double?, total: String, systemImage: String, tint: Color) -> some View {
        HStack(spacing: 11) {
            Image(systemName: systemImage)
                .font(.headline)
                .foregroundStyle(tint)
                .frame(width: 34, height: 34)
                .background(tint.opacity(0.1), in: Circle())
            VStack(alignment: .leading, spacing: 3) {
                Text(title)
                    .font(.caption)
                    .foregroundStyle(.secondary)
                Text(Self.speedString(value))
                    .font(.title3.weight(.semibold))
                    .monospacedDigit()
                Text(total)
                    .font(.caption2)
                    .foregroundStyle(.tertiary)
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    static func speedString(_ bytesPerSecond: Double?) -> String {
        guard let bytesPerSecond else { return "—" }
        let bitsPerSecond = bytesPerSecond * 8
        if bitsPerSecond >= 1_000_000_000 { return String(format: "%.1f Gbps", bitsPerSecond / 1_000_000_000) }
        if bitsPerSecond >= 1_000_000 { return String(format: "%.1f Mbps", bitsPerSecond / 1_000_000) }
        if bitsPerSecond >= 1_000 { return String(format: "%.0f Kbps", bitsPerSecond / 1_000) }
        return String(format: "%.0f bps", bitsPerSecond)
    }
}

struct StorageOverview: View {
    let storage: StorageData

    var body: some View {
        DashboardPanel(title: "Storage", systemImage: "internaldrive") {
            VStack(spacing: 16) {
                StorageUsageRow(
                    name: "Main Storage",
                    used: storage.main.usedBytes,
                    total: storage.main.totalBytes,
                    percent: storage.main.percentUsed
                )
                ForEach(storage.drives) { drive in
                    Divider()
                    StorageUsageRow(
                        name: drive.name,
                        used: drive.usedBytes,
                        total: drive.totalBytes,
                        percent: drive.percentUsed
                    )
                }
            }
        }
    }
}

private struct StorageUsageRow: View {
    let name: String
    let used: Int64
    let total: Int64
    let percent: Double

    private var tint: Color {
        if percent >= 90 { return .red }
        if percent >= 70 { return .orange }
        return .blue
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 9) {
            HStack(alignment: .firstTextBaseline) {
                Text(name)
                    .font(.subheadline.weight(.semibold))
                    .lineLimit(1)
                Spacer()
                Text("\(Formatters.percent(percent)) used")
                    .font(.caption.weight(.medium))
                    .foregroundStyle(tint)
            }
            ProgressView(value: min(max(percent / 100, 0), 1))
                .tint(tint)
                .controlSize(.small)
            HStack {
                Text("\(Formatters.byteString(used)) used")
                Spacer()
                Text("\(Formatters.byteString(max(total - used, 0))) available")
            }
            .font(.caption2)
            .foregroundStyle(.tertiary)
        }
    }
}

struct SystemInformationCard: View {
    let hostname: String
    let platform: String?
    let pythonVersion: String?
    let uptime: String
    let route: String
    let role: String

    var body: some View {
        DashboardPanel(title: "System Details", systemImage: "server.rack") {
            Grid(alignment: .leading, horizontalSpacing: 18, verticalSpacing: 12) {
                informationRow("Hostname", hostname)
                Divider().gridCellColumns(2)
                informationRow("Platform", platform ?? "Unknown")
                Divider().gridCellColumns(2)
                informationRow("Runtime", pythonVersion.map { "Python \($0)" } ?? "Unknown")
                Divider().gridCellColumns(2)
                informationRow("Uptime", uptime)
                Divider().gridCellColumns(2)
                informationRow("Route", route)
                Divider().gridCellColumns(2)
                informationRow("Account", role.isEmpty ? "Unknown" : role.capitalized)
            }
        }
    }

    private func informationRow(_ label: String, _ value: String) -> some View {
        GridRow {
            Text(label)
                .font(.caption)
                .foregroundStyle(.secondary)
                .frame(width: 72, alignment: .leading)
            Text(value)
                .font(.callout.weight(.medium))
                .lineLimit(2)
                .textSelection(.enabled)
        }
    }
}

private struct DashboardPanel<Content: View>: View {
    let title: String
    let systemImage: String
    @ViewBuilder let content: Content

    init(title: String, systemImage: String, @ViewBuilder content: () -> Content) {
        self.title = title
        self.systemImage = systemImage
        self.content = content()
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            Label(title, systemImage: systemImage)
                .font(.headline)
            content
        }
        .frame(maxWidth: .infinity, alignment: .topLeading)
        .padding(18)
        .dashboardPanel()
    }
}

private extension View {
    func dashboardPanel() -> some View {
        background(Color(nsColor: .controlBackgroundColor), in: RoundedRectangle(cornerRadius: 16, style: .continuous))
            .overlay {
                RoundedRectangle(cornerRadius: 16, style: .continuous)
                    .strokeBorder(.primary.opacity(0.07))
            }
            .shadow(color: .black.opacity(0.035), radius: 7, y: 2)
    }
}
