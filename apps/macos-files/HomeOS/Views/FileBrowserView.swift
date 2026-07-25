import SwiftUI

struct FileBrowserView: View {
    @EnvironmentObject var connection: ConnectionManager
    @State private var currentPath: String = "/"
    @State private var entries: [FileEntry] = []
    @State private var isLoading: Bool = false
    @State private var pathHistory: [String] = ["/"]

    var body: some View {
        VStack(spacing: 0) {
            // Toolbar
            HStack {
                Button(action: goBack) {
                    Image(systemName: "chevron.left")
                }
                .disabled(pathHistory.count <= 1)

                Text(currentPath)
                    .font(.caption)
                    .foregroundStyle(.secondary)
                    .lineLimit(1)

                Spacer()

                Button(action: refresh) {
                    Image(systemName: "arrow.clockwise")
                }
            }
            .padding(8)
            .background(.bar)

            Divider()

            // File list
            if isLoading {
                Spacer()
                ProgressView()
                Spacer()
            } else if entries.isEmpty {
                Spacer()
                Text("Empty folder")
                    .foregroundStyle(.secondary)
                Spacer()
            } else {
                List(entries) { entry in
                    HStack {
                        Image(systemName: entry.is_dir ? "folder.fill" : fileIcon(for: entry))
                            .foregroundStyle(entry.is_dir ? .blue : .secondary)
                            .frame(width: 20)

                        Text(entry.name)
                            .lineLimit(1)

                        Spacer()

                        if let size = entry.size, !entry.is_dir {
                            Text(formatSize(size))
                                .font(.caption)
                                .foregroundStyle(.tertiary)
                        }
                    }
                    .contentShape(Rectangle())
                    .onTapGesture(count: 2) {
                        if entry.is_dir {
                            navigateTo(entry.path)
                        } else {
                            downloadFile(entry)
                        }
                    }
                    .contextMenu {
                        if entry.is_dir {
                            Button("Open") { navigateTo(entry.path) }
                        } else {
                            Button("Download") { downloadFile(entry) }
                        }
                    }
                }
                .listStyle(.inset)
            }
        }
        .onAppear(perform: loadDirectory)
    }

    private func loadDirectory() {
        guard let client = connection.client else { return }
        isLoading = true
        Task {
            do {
                let response = try await client.listDirectory(path: currentPath)
                await MainActor.run {
                    entries = response.data?.entries ?? []
                    isLoading = false
                }
            } catch {
                await MainActor.run { isLoading = false }
            }
        }
    }

    private func navigateTo(_ path: String) {
        pathHistory.append(path)
        currentPath = path
        loadDirectory()
    }

    private func goBack() {
        guard pathHistory.count > 1 else { return }
        pathHistory.removeLast()
        currentPath = pathHistory.last ?? "/"
        loadDirectory()
    }

    private func refresh() {
        loadDirectory()
    }

    private func downloadFile(_ entry: FileEntry) {
        guard let client = connection.client else { return }
        Task {
            if let data = try? await client.downloadFile(path: entry.path) {
                await MainActor.run {
                    let panel = NSSavePanel()
                    panel.nameFieldStringValue = entry.name
                    if panel.runModal() == .OK, let url = panel.url {
                        try? data.write(to: url)
                    }
                }
            }
        }
    }

    private func fileIcon(for entry: FileEntry) -> String {
        switch entry.extension_type {
        case "jpg", "jpeg", "png", "gif", "webp", "svg": return "photo"
        case "pdf": return "doc.richtext"
        case "mp4", "mov", "avi", "mkv": return "film"
        case "mp3", "wav", "flac": return "music.note"
        case "zip", "tar", "gz", "rar": return "archivebox"
        case "txt", "md", "log": return "doc.text"
        case "py", "js", "html", "css", "json": return "doc.plaintext"
        default: return "doc"
        }
    }

    private func formatSize(_ bytes: Int) -> String {
        if bytes > 1_073_741_824 { return String(format: "%.1f GB", Double(bytes) / 1_073_741_824) }
        if bytes > 1_048_576 { return String(format: "%.1f MB", Double(bytes) / 1_048_576) }
        if bytes > 1024 { return String(format: "%.1f KB", Double(bytes) / 1024) }
        return "\(bytes) B"
    }
}
