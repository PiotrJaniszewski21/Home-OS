import AppKit
import QuickLook
import SwiftUI
import UniformTypeIdentifiers

struct FileBrowserView: View {
    @EnvironmentObject private var appState: AppState
    @EnvironmentObject private var connection: ConnectionManager
    @EnvironmentObject private var fileProviderDomain: FileProviderDomainService

    @State private var currentPath = "/"
    @State private var entries: [FileEntry] = []
    @State private var searchText = ""
    @State private var isLoading = false
    @State private var errorMessage: String?
    @State private var showingNewFolder = false
    @State private var newFolderName = ""
    @State private var isDropTarget = false
    @State private var downloadStates: [String: DownloadState] = [:]
    @State private var uploadState: UploadState?
    @State private var pendingAction: FileAction?
    @State private var actionText = ""
    @State private var deleteCandidate: FileEntry?
    @State private var previewURL: URL?

    var body: some View {
        VStack(spacing: 0) {
            pathBar
            Divider()
            content
        }
        .navigationTitle("Files")
        .searchable(text: $searchText, prompt: "Search files")
        .onSubmit(of: .search, runSearch)
        .toolbar {
            Button("Upload", systemImage: "square.and.arrow.up") { chooseFilesForUpload() }
            Button("New Folder", systemImage: "folder.badge.plus") { showingNewFolder = true }
            Button("Open Finder Folder", systemImage: "externaldrive") {
                Task { await fileProviderDomain.openInFinder(using: appState) }
            }
            .disabled(!appState.isConfigured || fileProviderDomain.state == .installing)
            Button("Refresh", systemImage: "arrow.clockwise") { Task { await loadDirectory() } }
        }
        .alert("New Folder", isPresented: $showingNewFolder) {
            TextField("Folder name", text: $newFolderName)
            Button("Create") { Task { await createFolder() } }
            Button("Cancel", role: .cancel) { newFolderName = "" }
        } message: {
            Text("Create a folder in \(currentPath)")
        }
        .alert(actionAlertTitle, isPresented: actionBinding) {
            TextField(actionPrompt, text: $actionText)
            Button(actionButtonTitle) { Task { await commitPendingAction() } }
            Button("Cancel", role: .cancel) { clearPendingAction() }
        } message: {
            Text(actionMessage)
        }
        .alert("Move to Trash?", isPresented: deleteBinding) {
            Button("Move to Trash", role: .destructive) {
                guard let entry = deleteCandidate else { return }
                Task { await delete(entry) }
                deleteCandidate = nil
            }
            Button("Cancel", role: .cancel) { deleteCandidate = nil }
        } message: {
            Text(deleteCandidate.map { "This moves \($0.name) to Home OS Trash. You can restore it from the Trash page." } ?? "")
        }
        .quickLookPreview($previewURL)
        .task { await loadDirectory() }
    }

    private var deleteBinding: Binding<Bool> {
        Binding(
            get: { deleteCandidate != nil },
            set: { if !$0 { deleteCandidate = nil } }
        )
    }

    private var actionBinding: Binding<Bool> {
        Binding(
            get: { pendingAction != nil },
            set: { if !$0 { clearPendingAction() } }
        )
    }

    private var actionAlertTitle: String {
        switch pendingAction {
        case .rename: "Rename Item"
        case .copy: "Copy Item"
        case .move: "Move Item"
        case nil: "File Action"
        }
    }

    private var actionPrompt: String {
        switch pendingAction {
        case .rename: "New name"
        case .copy, .move: "Destination folder path"
        case nil: "Value"
        }
    }

    private var actionButtonTitle: String {
        switch pendingAction {
        case .rename: "Rename"
        case .copy: "Copy"
        case .move: "Move"
        case nil: "Apply"
        }
    }

    private var actionMessage: String {
        switch pendingAction {
        case .rename(let entry): "Enter a new name for \(entry.name)."
        case .copy(let entry): "Enter the destination folder for a copy of \(entry.name)."
        case .move(let entry): "Enter the destination folder for \(entry.name)."
        case nil: ""
        }
    }

    private var pathBar: some View {
        HStack(spacing: 8) {
            Button(action: goUp) {
                Image(systemName: "chevron.up")
            }
            .disabled(currentPath == "/")

            Label(currentPath, systemImage: "folder")
                .font(.headline)
                .lineLimit(1)
                .truncationMode(.middle)
            Spacer()
            if activeDownloadCount > 0 {
                HStack(spacing: 6) {
                    ProgressView()
                        .controlSize(.small)
                    Text(activeDownloadCount == 1 ? "Downloading…" : "\(activeDownloadCount) downloads…")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
            }
            if let uploadState {
                Label(uploadState.label, systemImage: uploadState.systemImage)
                    .font(.caption)
                    .foregroundStyle(uploadState.tint)
            }
            if isLoading { ProgressView().controlSize(.small) }
        }
        .padding(.horizontal, 18)
        .padding(.vertical, 10)
    }

    @ViewBuilder
    private var content: some View {
        if let errorMessage {
            ContentUnavailableView("Could not load files", systemImage: "exclamationmark.triangle", description: Text(errorMessage))
        } else if isLoading && entries.isEmpty {
            ProgressView("Loading files…")
                .frame(maxWidth: .infinity, maxHeight: .infinity)
        } else if entries.isEmpty {
            dropZone {
                ContentUnavailableView("No files", systemImage: "folder", description: Text("Upload files or create a folder to get started."))
            }
        } else {
            dropZone {
                List(entries) { entry in
                    FileRow(entry: entry, downloadState: downloadStates[entry.id]) {
                        open(entry)
                    } onDownload: {
                        Task { await download(entry) }
                    } onPreview: {
                        Task { await preview(entry) }
                    } onReveal: {
                        revealDownload(entry)
                    } onRename: {
                        beginAction(.rename(entry))
                    } onCopy: {
                        beginAction(.copy(entry))
                    } onMove: {
                        beginAction(.move(entry))
                    } onDelete: {
                        deleteCandidate = entry
                    }
                }
                .listStyle(.inset)
            }
        }
    }

    private func dropZone<Content: View>(@ViewBuilder content: () -> Content) -> some View {
        content()
            .overlay {
                if isDropTarget {
                    RoundedRectangle(cornerRadius: 18)
                        .stroke(.blue, style: StrokeStyle(lineWidth: 3, dash: [8]))
                        .padding(10)
                }
            }
            .onDrop(of: [UTType.fileURL.identifier], isTargeted: $isDropTarget) { providers in
                handleDrop(providers)
            }
    }

    private func loadDirectory() async {
        guard let client = connection.client else { return }
        isLoading = true
        errorMessage = nil
        do {
            let response = try await client.listDirectory(path: currentPath)
            entries = response.data?.entries ?? []
        } catch {
            errorMessage = error.localizedDescription
        }
        isLoading = false
    }

    private func runSearch() {
        let query = searchText.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !query.isEmpty, let client = connection.client else {
            Task { await loadDirectory() }
            return
        }
        Task {
            isLoading = true
            errorMessage = nil
            do {
                let response = try await client.searchFiles(query: query)
                entries = response.data ?? []
            } catch {
                errorMessage = error.localizedDescription
            }
            isLoading = false
        }
    }

    private func open(_ entry: FileEntry) {
        if entry.isDirectory {
            currentPath = entry.path.hasPrefix("/") ? entry.path : "/\(entry.path)"
            searchText = ""
            Task { await loadDirectory() }
        } else {
            Task { await download(entry, openAfterDownload: true) }
        }
    }

    private func goUp() {
        guard currentPath != "/" else { return }
        let parent = URL(fileURLWithPath: currentPath).deletingLastPathComponent().path
        currentPath = parent == "/" ? "/" : parent
        Task { await loadDirectory() }
    }

    private func createFolder() async {
        guard let client = connection.client else { return }
        let name = newFolderName.trimmingCharacters(in: .whitespacesAndNewlines)
        newFolderName = ""
        guard !name.isEmpty else { return }
        let path = currentPath == "/" ? "/\(name)" : "\(currentPath)/\(name)"
        do {
            _ = try await client.createDirectory(path: path)
            await loadDirectory()
        } catch {
            errorMessage = error.localizedDescription
        }
    }

    private func download(_ entry: FileEntry, openAfterDownload: Bool = false) async {
        guard let client = connection.client else { return }
        downloadStates[entry.id] = .downloading(progress: nil)
        errorMessage = nil
        AppLog.files.info("Download started, size bytes: \(entry.size ?? -1, privacy: .public)")

        do {
            let temporaryURL = try await client.downloadFileToTemporaryURL(path: entry.path) { progress in
                await MainActor.run {
                    downloadStates[entry.id] = .downloading(progress: progress)
                }
            }
            let destination = uniqueDownloadDestination(named: entry.name)
            try FileManager.default.moveItem(at: temporaryURL, to: destination)
            downloadStates[entry.id] = .completed(destination)
            AppLog.files.info("Download completed")
            if openAfterDownload { NSWorkspace.shared.open(destination) }
        } catch {
            downloadStates[entry.id] = .failed(error.localizedDescription)
            errorMessage = error.localizedDescription
            AppLog.files.error("Download failed: \(error.localizedDescription, privacy: .public)")
        }
    }

    private func preview(_ entry: FileEntry) async {
        guard let client = connection.client else { return }
        downloadStates[entry.id] = .downloading(progress: nil)
        errorMessage = nil
        AppLog.files.info("Preview download started")

        do {
            let temporaryURL = try await client.downloadFileToTemporaryURL(path: entry.path) { progress in
                await MainActor.run {
                    downloadStates[entry.id] = .downloading(progress: progress)
                }
            }
            let destination = uniquePreviewDestination(named: entry.name)
            try? FileManager.default.removeItem(at: destination)
            try FileManager.default.moveItem(at: temporaryURL, to: destination)
            previewURL = destination
            downloadStates[entry.id] = nil
            AppLog.files.info("Preview ready")
        } catch {
            downloadStates[entry.id] = .failed(error.localizedDescription)
            errorMessage = error.localizedDescription
            AppLog.files.error("Preview failed: \(error.localizedDescription, privacy: .public)")
        }
    }

    private var activeDownloadCount: Int {
        downloadStates.values.filter { state in
            if case .downloading = state { return true }
            return false
        }.count
    }

    private func revealDownload(_ entry: FileEntry) {
        guard case .completed(let url) = downloadStates[entry.id] else { return }
        NSWorkspace.shared.activateFileViewerSelecting([url])
    }

    private func uniqueDownloadDestination(named filename: String) -> URL {
        let downloads = FileManager.default.urls(for: .downloadsDirectory, in: .userDomainMask)[0]
        let baseURL = downloads.appendingPathComponent(filename)
        guard FileManager.default.fileExists(atPath: baseURL.path) else { return baseURL }

        let ext = baseURL.pathExtension
        let stem = baseURL.deletingPathExtension().lastPathComponent
        for index in 1...999 {
            let candidateName = ext.isEmpty ? "\(stem) \(index)" : "\(stem) \(index).\(ext)"
            let candidate = downloads.appendingPathComponent(candidateName)
            if !FileManager.default.fileExists(atPath: candidate.path) {
                return candidate
            }
        }
        return downloads.appendingPathComponent("\(UUID().uuidString)-\(filename)")
    }

    private func uniquePreviewDestination(named filename: String) -> URL {
        let directory = FileManager.default.temporaryDirectory.appendingPathComponent("HomeOSPreview", isDirectory: true)
        try? FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
        return directory.appendingPathComponent("\(UUID().uuidString)-\(filename)")
    }

    private func delete(_ entry: FileEntry) async {
        guard let client = connection.client else { return }
        do {
            AppLog.files.info("Delete requested")
            _ = try await client.delete(path: entry.path)
            await loadDirectory()
        } catch {
            errorMessage = error.localizedDescription
        }
    }

    private func chooseFilesForUpload() {
        let panel = NSOpenPanel()
        panel.allowsMultipleSelection = true
        panel.canChooseDirectories = false
        panel.canChooseFiles = true
        guard panel.runModal() == .OK else { return }
        Task { await upload(panel.urls) }
    }

    private func handleDrop(_ providers: [NSItemProvider]) -> Bool {
        for provider in providers where provider.hasItemConformingToTypeIdentifier(UTType.fileURL.identifier) {
            provider.loadItem(forTypeIdentifier: UTType.fileURL.identifier, options: nil) { item, _ in
                let url: URL?
                if let data = item as? Data {
                    url = URL(dataRepresentation: data, relativeTo: nil)
                } else {
                    url = item as? URL
                }
                guard let url else { return }
                Task { await upload([url]) }
            }
        }
        return true
    }

    private func upload(_ urls: [URL]) async {
        guard let client = connection.client else { return }
        isLoading = true
        uploadState = .uploading(completed: 0, total: urls.count, progress: nil)
        for (index, url) in urls.enumerated() {
            do {
                AppLog.files.info("Upload started")
                uploadState = .uploading(completed: index, total: urls.count, progress: 0)
                try await client.upload(fileURL: url, to: currentPath) { progress in
                    await MainActor.run {
                        uploadState = .uploading(completed: index, total: urls.count, progress: progress)
                    }
                }
                uploadState = .uploading(completed: index + 1, total: urls.count, progress: 1)
                AppLog.files.info("Upload completed")
            } catch {
                errorMessage = error.localizedDescription
                uploadState = .failed(error.localizedDescription)
                AppLog.files.error("Upload failed: \(error.localizedDescription, privacy: .public)")
            }
        }
        await loadDirectory()
        isLoading = false
        if case .failed = uploadState {
            return
        }
        uploadState = .completed(urls.count)
    }

    private func beginAction(_ action: FileAction) {
        pendingAction = action
        switch action {
        case .rename(let entry):
            actionText = entry.name
        case .copy, .move:
            actionText = currentPath
        }
    }

    private func clearPendingAction() {
        pendingAction = nil
        actionText = ""
    }

    private func commitPendingAction() async {
        guard let client = connection.client, let pendingAction else { return }
        let value = actionText.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !value.isEmpty else { return }

        do {
            switch pendingAction {
            case .rename(let entry):
                AppLog.files.info("Rename requested")
                _ = try await client.rename(path: entry.path, newName: value)
            case .copy(let entry):
                AppLog.files.info("Copy requested")
                _ = try await client.copy(sourcePath: entry.path, destinationPath: value)
            case .move(let entry):
                AppLog.files.info("Move requested")
                _ = try await client.move(sourcePath: entry.path, destinationPath: value)
            }
            clearPendingAction()
            await loadDirectory()
        } catch {
            errorMessage = error.localizedDescription
        }
    }
}

private enum DownloadState: Equatable {
    case downloading(progress: Double?)
    case completed(URL)
    case failed(String)
}

private enum UploadState: Equatable {
    case uploading(completed: Int, total: Int, progress: Double?)
    case completed(Int)
    case failed(String)

    var label: String {
        switch self {
        case .uploading(let completed, let total, let progress):
            if let progress {
                "Uploading \(completed + 1)/\(total) · \(Int((progress * 100).rounded()))%"
            } else {
                "Uploading \(completed)/\(total)…"
            }
        case .completed(let count): "Uploaded \(count)"
        case .failed: "Upload failed"
        }
    }

    var systemImage: String {
        switch self {
        case .uploading: "arrow.up.circle"
        case .completed: "checkmark.circle.fill"
        case .failed: "exclamationmark.triangle.fill"
        }
    }

    var tint: Color {
        switch self {
        case .uploading: .secondary
        case .completed: .green
        case .failed: .red
        }
    }
}

private enum FileAction: Identifiable {
    case rename(FileEntry)
    case copy(FileEntry)
    case move(FileEntry)

    var id: String {
        switch self {
        case .rename(let entry): "rename-\(entry.id)"
        case .copy(let entry): "copy-\(entry.id)"
        case .move(let entry): "move-\(entry.id)"
        }
    }
}

private struct FileRow: View {
    let entry: FileEntry
    let downloadState: DownloadState?
    let onOpen: () -> Void
    let onDownload: () -> Void
    let onPreview: () -> Void
    let onReveal: () -> Void
    let onRename: () -> Void
    let onCopy: () -> Void
    let onMove: () -> Void
    let onDelete: () -> Void

    var body: some View {
        HStack(spacing: 12) {
            Image(systemName: entry.isDirectory ? "folder.fill" : iconName)
                .foregroundStyle(entry.isDirectory ? .blue : .secondary)
                .frame(width: 24)
            VStack(alignment: .leading, spacing: 3) {
                Text(entry.name)
                    .font(.headline)
                Text(entry.path)
                    .font(.caption)
                    .foregroundStyle(.secondary)
                    .lineLimit(1)
            }
            Spacer()
            Text(Formatters.byteString(entry.size))
                .font(.caption)
                .foregroundStyle(.secondary)
            downloadStatus
            Button("Open", action: onOpen)
            if !entry.isDirectory {
                Button("Preview", systemImage: "eye", action: onPreview)
                Button("Download", action: onDownload)
                    .disabled(isDownloading)
            }
            Menu("More") {
                Button("Rename", systemImage: "pencil", action: onRename)
                Button("Copy…", systemImage: "doc.on.doc", action: onCopy)
                Button("Move…", systemImage: "folder", action: onMove)
                Divider()
                Button("Move to Trash", systemImage: "trash", role: .destructive, action: onDelete)
            }
            .menuStyle(.borderlessButton)
        }
        .padding(.vertical, 5)
        .contentShape(Rectangle())
        .onTapGesture(perform: onOpen)
    }

    @ViewBuilder
    private var downloadStatus: some View {
        switch downloadState {
        case .downloading(let progress):
            HStack(spacing: 6) {
                if let progress {
                    ProgressView(value: progress)
                        .frame(width: 72)
                    Text("\(Int((progress * 100).rounded()))%")
                        .font(.caption2)
                        .monospacedDigit()
                } else {
                    ProgressView()
                        .controlSize(.small)
                    Text("Downloading")
                        .font(.caption2)
                }
            }
            .foregroundStyle(.secondary)
        case .completed:
            Button("Show in Finder", systemImage: "magnifyingglass") {
                onReveal()
            }
            .font(.caption)
        case .failed(let message):
            Label(message, systemImage: "exclamationmark.triangle.fill")
                .font(.caption2)
                .foregroundStyle(.red)
                .lineLimit(1)
        case nil:
            EmptyView()
        }
    }

    private var isDownloading: Bool {
        if case .downloading = downloadState { return true }
        return false
    }

    private var iconName: String {
        switch entry.extensionType?.lowercased() {
        case "jpg", "jpeg", "png", "gif", "heic", "webp": "photo"
        case "pdf": "doc.richtext"
        case "zip", "rar", "7z": "archivebox"
        case "mp4", "mov", "mkv": "film"
        case "mp3", "wav", "flac": "music.note"
        default: "doc"
        }
    }
}
