import SwiftUI

struct PerformanceLogsView: View {
    @ObservedObject private var logger = PerformanceLogger.shared
    @Environment(\.dismiss) private var dismiss
    @State private var filterCategory: String = "All"
    @State private var copiedToClipboard = false

    private var categories: [String] {
        let allCategories = Set(logger.logs.map(\.category))
        return ["All"] + Array(allCategories).sorted()
    }

    private var filteredLogs: [PerformanceLogEntry] {
        if filterCategory == "All" {
            return logger.logs.reversed()
        }
        return logger.logs.filter { $0.category == filterCategory }.reversed()
    }

    var body: some View {
        NavigationStack {
            VStack(spacing: 0) {
                // Category Filter Pills
                ScrollView(.horizontal, showsIndicators: false) {
                    HStack(spacing: 8) {
                        ForEach(categories, id: \.self) { cat in
                            Button {
                                filterCategory = cat
                            } label: {
                                Text(cat)
                                    .font(.caption.bold())
                                    .padding(.horizontal, 12)
                                    .padding(.vertical, 6)
                                    .background(filterCategory == cat ? Color.accentColor : Color(.secondarySystemBackground))
                                    .foregroundStyle(filterCategory == cat ? Color.white : Color.primary)
                                    .clipShape(Capsule())
                            }
                        }
                    }
                    .padding(.horizontal, 16)
                    .padding(.vertical, 10)
                }
                .background(Color(.systemGroupedBackground))

                Divider()

                if filteredLogs.isEmpty {
                    ContentUnavailableView("No Performance Logs", systemImage: "gauge.with.dots.needle.bottom.50percent", description: Text("Start playing songs to collect performance timing logs."))
                } else {
                    List {
                        ForEach(filteredLogs) { entry in
                            VStack(alignment: .leading, spacing: 4) {
                                HStack {
                                    Text(entry.category)
                                        .font(.caption2.bold())
                                        .foregroundStyle(.secondary)
                                    Spacer()
                                    Text(entry.formattedTimestamp)
                                        .font(.caption2.monospaced())
                                        .foregroundStyle(.tertiary)
                                }
                                Text(entry.message)
                                    .font(.system(.footnote, design: .monospaced))
                                    .foregroundStyle(.primary)

                                if let durationMs = entry.durationMs {
                                    HStack {
                                        Spacer()
                                        Text(String(format: "%.1f ms", durationMs))
                                            .font(.caption.bold().monospaced())
                                            .foregroundStyle(durationMs > 1000 ? .red : (durationMs > 300 ? .orange : .green))
                                    }
                                }
                            }
                            .padding(.vertical, 2)
                        }
                    }
                    .listStyle(.plain)
                }
            }
            .navigationTitle("Performance Logs (\(logger.logs.count))")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .topBarLeading) {
                    Button("Clear") {
                        logger.clear()
                    }
                    .foregroundStyle(.red)
                }
                ToolbarItem(placement: .topBarTrailing) {
                    HStack(spacing: 12) {
                        Button {
                            let text = logger.logs.map(\.displayText).joined(separator: "\n")
                            UIPasteboard.general.string = text
                            copiedToClipboard = true
                            Task {
                                try? await Task.sleep(for: .seconds(2))
                                copiedToClipboard = false
                            }
                        } label: {
                            Image(systemName: copiedToClipboard ? "checkmark" : "doc.on.doc")
                        }
                        Button("Done") {
                            dismiss()
                        }
                    }
                }
            }
        }
    }
}
