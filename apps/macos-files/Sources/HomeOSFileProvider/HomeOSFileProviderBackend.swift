import FileProvider
import Foundation
import os
import UniformTypeIdentifiers

final class HomeOSFileProviderBackend: @unchecked Sendable {
    private let logger = Logger(subsystem: "uk.co.petershomenet.homeos", category: "FileProvider")
    private let remoteSession: URLSession
    private let localSession: URLSession
    private let localCertificateTrustDelegate: LocalCertificateTrustDelegate
    private let keepDownloadedStore = HomeOSFileProviderKeepDownloadedStore.shared
    private let identityStore = HomeOSFileProviderIdentityStore.shared
    private let snapshotStore = HomeOSFileProviderSnapshotStore.shared
    private let fileManager = FileManager.default

    init() {
        let remoteConfiguration = URLSessionConfiguration.default
        remoteConfiguration.timeoutIntervalForRequest = 12
        remoteConfiguration.timeoutIntervalForResource = 300
        remoteConfiguration.waitsForConnectivity = false

        let localConfiguration = URLSessionConfiguration.default
        localConfiguration.timeoutIntervalForRequest = 3
        localConfiguration.timeoutIntervalForResource = 120
        localConfiguration.waitsForConnectivity = false

        let localCertificateTrustDelegate = LocalCertificateTrustDelegate()
        self.localCertificateTrustDelegate = localCertificateTrustDelegate
        self.remoteSession = URLSession(configuration: remoteConfiguration, delegate: localCertificateTrustDelegate, delegateQueue: nil)
        self.localSession = URLSession(configuration: localConfiguration, delegate: localCertificateTrustDelegate, delegateQueue: nil)
    }

    func item(for identifier: NSFileProviderItemIdentifier) async throws -> HomeOSFileProviderItem {
        if identifier == .rootContainer || identifier == .workingSet {
            return .root()
        }

        let remotePath = try HomeOSFileProviderPath.remotePath(for: identifier, identityStore: identityStore)
        let parentPath = HomeOSFileProviderPath.parentPath(for: remotePath)
        let entries = try await listEntries(path: parentPath)
        guard let entry = entries.first(where: { HomeOSFileProviderPath.normalize($0.path) == remotePath }) else {
            throw NSFileProviderError(.noSuchItem)
        }
        return .from(entry: entry, keepDownloadedStore: keepDownloadedStore, identityStore: identityStore)
    }

    func listItems(in containerIdentifier: NSFileProviderItemIdentifier) async throws -> [HomeOSFileProviderItem] {
        if containerIdentifier == .trashContainer {
            return []
        }
        if containerIdentifier == .workingSet {
            return try await listWorkingSetItems()
        }
        let remotePath = try HomeOSFileProviderPath.remotePath(for: containerIdentifier, identityStore: identityStore)
        return try await listEntries(path: remotePath)
            .sorted { lhs, rhs in
                if lhs.isDirectory != rhs.isDirectory { return lhs.isDirectory && !rhs.isDirectory }
                return lhs.name.localizedStandardCompare(rhs.name) == .orderedAscending
            }
            .map { HomeOSFileProviderItem.from(entry: $0, keepDownloadedStore: keepDownloadedStore, identityStore: identityStore) }
    }

    private func listWorkingSetItems() async throws -> [HomeOSFileProviderItem] {
        let username = HomeOSSharedSettings.load().username.trimmingCharacters(in: .whitespacesAndNewlines)
        var paths = ["/", "/users"]
        if !username.isEmpty {
            paths.append("/users/\(username)")
        }

        var entriesByPath: [String: FileEntry] = [:]
        for path in paths {
            for entry in try await listEntries(path: path) {
                entriesByPath[HomeOSFileProviderPath.normalize(entry.path)] = entry
            }
        }
        return entriesByPath.values
            .sorted { lhs, rhs in
                if lhs.isDirectory != rhs.isDirectory { return lhs.isDirectory && !rhs.isDirectory }
                return lhs.path.localizedStandardCompare(rhs.path) == .orderedAscending
            }
            .map {
                HomeOSFileProviderItem.from(
                    entry: $0,
                    keepDownloadedStore: keepDownloadedStore,
                    identityStore: identityStore
                )
            }
    }

    func fetchContents(
        for identifier: NSFileProviderItemIdentifier,
        requestedVersion: NSFileProviderItemVersion?,
        onProgress: (@Sendable (Double?) async -> Void)? = nil
    ) async throws -> (URL, HomeOSFileProviderItem) {
        let remotePath = try HomeOSFileProviderPath.remotePath(for: identifier, identityStore: identityStore)
        let item = try await item(for: identifier)
        if let requestedVersion, !versionsMatch(item.itemVersion, requestedVersion) {
            throw NSFileProviderError(.versionNoLongerAvailable)
        }
        let temporaryURL = try await withReadClient { client in
            try await client.downloadFileToTemporaryURL(path: remotePath, onProgress: onProgress)
        }
        return (temporaryURL, item)
    }

    func createItem(
        from template: NSFileProviderItem,
        contents: URL?,
        onProgress: (@Sendable (Double?) async -> Void)? = nil
    ) async throws -> HomeOSFileProviderItem {
        let parentPath = try HomeOSFileProviderPath.remotePath(for: template.parentItemIdentifier, identityStore: identityStore)
        let targetPath = HomeOSFileProviderPath.join(parentPath, template.filename)
        var createdPath = targetPath

        if isDirectory(template, contents: contents) {
            try await withMutationClient { client in
                let response = try await client.createDirectory(path: targetPath)
                if !response.ok {
                    throw APIError.requestFailed(response.error ?? "Could not create \(template.filename).")
                }
            }
        } else if let contents {
            let stagedContents = try stageUploadContents(contents, preferredFilename: template.filename)
            defer { try? fileManager.removeItem(at: stagedContents) }
            try await withMutationClient { client in
                let uploaded = try await client.upload(
                    fileURL: stagedContents,
                    to: parentPath,
                    filename: template.filename,
                    onProgress: onProgress
                )
                createdPath = uploaded.map { HomeOSFileProviderPath.normalize($0.path) } ?? targetPath
            }
        } else {
            let emptyFile = fileManager.temporaryDirectory
                .appendingPathComponent("homeos-empty-\(UUID().uuidString)-\(template.filename)")
            fileManager.createFile(atPath: emptyFile.path, contents: Data())
            defer { try? fileManager.removeItem(at: emptyFile) }
            try await withMutationClient { client in
                let uploaded = try await client.upload(
                    fileURL: emptyFile,
                    to: parentPath,
                    filename: template.filename,
                    onProgress: onProgress
                )
                createdPath = uploaded.map { HomeOSFileProviderPath.normalize($0.path) } ?? targetPath
            }
        }

        return try await item(for: HomeOSFileProviderPath.identifier(for: createdPath, identityStore: identityStore))
    }

    func modifyItem(
        _ template: NSFileProviderItem,
        baseVersion: NSFileProviderItemVersion,
        changedFields: NSFileProviderItemFields,
        contents: URL?,
        onProgress: (@Sendable (Double?) async -> Void)? = nil
    ) async throws -> HomeOSFileProviderItem {
        let originalIdentifier = template.itemIdentifier
        try await ensureCurrentVersion(for: originalIdentifier, matches: baseVersion)
        var currentPath = try HomeOSFileProviderPath.remotePath(for: template.itemIdentifier, identityStore: identityStore)

        if changedFields.contains(.parentItemIdentifier),
           template.parentItemIdentifier == .trashContainer {
            let contentType = template.contentType ?? .data
            let isDirectory = contentType.conforms(to: .folder)
            try await deleteItem(identifier: originalIdentifier, baseVersion: baseVersion)
            return HomeOSFileProviderItem(
                itemIdentifier: originalIdentifier,
                parentItemIdentifier: .trashContainer,
                filename: template.filename,
                contentType: contentType,
                documentSize: (template.documentSize ?? nil)?.int64Value,
                modifiedDate: template.contentModificationDate ?? nil,
                isDirectory: isDirectory,
                capabilities: [.allowsDeleting]
            )
        }

        let requestedParentPath = try HomeOSFileProviderPath.remotePath(for: template.parentItemIdentifier, identityStore: identityStore)
        let currentParentPath = HomeOSFileProviderPath.parentPath(for: currentPath)
        let currentFilename = HomeOSFileProviderPath.filename(for: currentPath)

        if changedFields.contains(.parentItemIdentifier), requestedParentPath != currentParentPath {
            let previousPath = currentPath
            try await withMutationClient { client in
                let response = try await client.move(sourcePath: currentPath, destinationPath: requestedParentPath)
                if !response.ok {
                    throw APIError.requestFailed(response.error ?? "Could not move \(currentFilename).")
                }
            }
            currentPath = HomeOSFileProviderPath.join(requestedParentPath, currentFilename)
            identityStore.moveTree(
                fromNormalizedPath: previousPath,
                toNormalizedPath: currentPath,
                rootIdentifier: originalIdentifier
            )
        }

        if changedFields.contains(.filename), template.filename != HomeOSFileProviderPath.filename(for: currentPath) {
            let previousPath = currentPath
            try await withMutationClient { client in
                let response = try await client.rename(path: currentPath, newName: template.filename)
                if !response.ok {
                    throw APIError.requestFailed(response.error ?? "Could not rename \(currentFilename).")
                }
            }
            currentPath = HomeOSFileProviderPath.join(HomeOSFileProviderPath.parentPath(for: currentPath), template.filename)
            identityStore.moveTree(
                fromNormalizedPath: previousPath,
                toNormalizedPath: currentPath,
                rootIdentifier: originalIdentifier
            )
        }

        if let contents, !isDirectory(template, contents: contents) {
            let uploadParentPath = HomeOSFileProviderPath.parentPath(for: currentPath)
            let stagedContents = try stageUploadContents(contents, preferredFilename: template.filename)
            defer { try? fileManager.removeItem(at: stagedContents) }
            try await withMutationClient { client in
                try await safelyReplaceFile(
                    at: currentPath,
                    with: stagedContents,
                    filename: HomeOSFileProviderPath.filename(for: currentPath),
                    parentPath: uploadParentPath,
                    client: client,
                    onProgress: onProgress
                )
            }
        }

        return try await item(for: originalIdentifier)
    }

    func deleteItem(identifier: NSFileProviderItemIdentifier, baseVersion: NSFileProviderItemVersion) async throws {
        try await ensureCurrentVersion(for: identifier, matches: baseVersion)
        let remotePath = try HomeOSFileProviderPath.remotePath(for: identifier, identityStore: identityStore)
        try await withMutationClient { client in
            let response = try await client.delete(path: remotePath)
            if !response.ok {
                throw APIError.requestFailed(response.error ?? "Could not delete \(HomeOSFileProviderPath.filename(for: remotePath)).")
            }
        }
        keepDownloadedStore.remove(identifier)
        identityStore.remove(identifier: identifier)
        snapshotStore.remove(identifier: identifier.rawValue)
    }

    func parentIdentifier(for identifier: NSFileProviderItemIdentifier) throws -> NSFileProviderItemIdentifier {
        let path = try HomeOSFileProviderPath.remotePath(for: identifier, identityStore: identityStore)
        return HomeOSFileProviderPath.parentIdentifier(for: path, identityStore: identityStore)
    }

    private func listEntries(path: String) async throws -> [FileEntry] {
        try await withReadClient { client in
            let response = try await client.listDirectory(path: path)
            guard response.ok, let data = response.data else {
                throw APIError.requestFailed(response.error ?? "Could not list \(path).")
            }
            var normalizedPaths = Set<String>()
            for entry in data.entries {
                let normalizedPath = HomeOSFileProviderPath.normalize(entry.path)
                guard normalizedPaths.insert(normalizedPath).inserted else {
                    throw APIError.requestFailed("Server returned duplicate file metadata.")
                }
            }
            return data.entries
        }
    }

    private func withReadClient<T>(_ operation: (APIClient) async throws -> T) async throws -> T {
        let (settings, candidates) = try configuredCandidates()
        var failures: [String] = []
        for endpoint in candidates {
            do {
                let client = try makeClient(endpoint: endpoint, settings: settings)
                let result = try await operation(client)
                logger.info("File Provider read succeeded via \(endpoint.kind.rawValue, privacy: .public) endpoint \(endpoint.displayName, privacy: .public)")
                return result
            } catch {
                logger.error("File Provider read failed via \(endpoint.kind.rawValue, privacy: .public) endpoint \(endpoint.displayName, privacy: .public): \(error.localizedDescription, privacy: .public)")
                failures.append("\(endpoint.displayName): \(error.localizedDescription)")
            }
        }
        throw APIError.requestFailed(failures.joined(separator: "\n"))
    }

    private func withMutationClient<T>(_ operation: (APIClient) async throws -> T) async throws -> T {
        let (settings, candidates) = try configuredCandidates()
        var failures: [String] = []
        for endpoint in candidates {
            let client: APIClient
            do {
                client = try makeClient(endpoint: endpoint, settings: settings)
                let health = try await client.getHealth()
                guard health.status.localizedCaseInsensitiveCompare("healthy") == .orderedSame else {
                    throw APIError.requestFailed("Server did not report healthy.")
                }
            } catch {
                failures.append("\(endpoint.displayName): \(error.localizedDescription)")
                continue
            }

            do {
                let result = try await operation(client)
                logger.info("File Provider mutation succeeded via \(endpoint.kind.rawValue, privacy: .public) endpoint \(endpoint.displayName, privacy: .public)")
                return result
            } catch {
                logger.error("File Provider mutation failed via selected \(endpoint.kind.rawValue, privacy: .public) endpoint \(endpoint.displayName, privacy: .public): \(error.localizedDescription, privacy: .public)")
                throw error
            }
        }
        throw APIError.requestFailed(failures.joined(separator: "\n"))
    }

    private func configuredCandidates() throws -> (HomeOSSharedSettings, [ConnectionEndpoint]) {
        let settings = HomeOSSharedSettings.load()
        guard settings.isConfigured else {
            logger.error("File Provider missing shared settings")
            throw NSFileProviderError(.notAuthenticated)
        }

        let candidates = ConnectionEndpointResolver.candidates(
            domainURL: settings.serverURL,
            localURL: settings.localServerURL,
            preferLocal: settings.preferLocalServer
        )
        guard !candidates.isEmpty else {
            logger.error("File Provider has no valid endpoint candidates")
            throw APIError.invalidURL
        }

        return (settings, candidates)
    }

    private func makeClient(endpoint: ConnectionEndpoint, settings: HomeOSSharedSettings) throws -> APIClient {
        try APIClient(
            baseURL: endpoint.url,
            authToken: settings.authToken,
            session: endpoint.kind == .local ? localSession : remoteSession,
            trustsLocalSelfSignedCertificates: endpoint.kind == .local
        )
    }

    private func isDirectory(_ item: NSFileProviderItem, contents: URL?) -> Bool {
        if item.contentType?.conforms(to: .folder) == true {
            return true
        }
        return false
    }

    private func ensureCurrentVersion(
        for identifier: NSFileProviderItemIdentifier,
        matches baseVersion: NSFileProviderItemVersion
    ) async throws {
        let current = try await item(for: identifier)
        guard versionsMatch(current.itemVersion, baseVersion) else {
            throw NSFileProviderError(.cannotSynchronize)
        }
    }

    private func versionsMatch(_ lhs: NSFileProviderItemVersion, _ rhs: NSFileProviderItemVersion) -> Bool {
        lhs.contentVersion == rhs.contentVersion && lhs.metadataVersion == rhs.metadataVersion
    }

    private func safelyReplaceFile(
        at currentPath: String,
        with stagedContents: URL,
        filename: String,
        parentPath: String,
        client: APIClient,
        onProgress: (@Sendable (Double?) async -> Void)?
    ) async throws {
        let nonce = UUID().uuidString.lowercased()
        let temporaryName = "homeos-upload-\(nonce)-\(filename)"
        let backupName = "homeos-backup-\(nonce)-\(filename)"
        let temporaryPath = HomeOSFileProviderPath.join(parentPath, temporaryName)
        let backupPath = HomeOSFileProviderPath.join(parentPath, backupName)

        let uploaded = try await client.upload(
            fileURL: stagedContents,
            to: parentPath,
            filename: temporaryName,
            onProgress: onProgress
        )
        let resolvedTemporaryPath = uploaded.map { HomeOSFileProviderPath.normalize($0.path) } ?? temporaryPath

        do {
            let backupResponse = try await client.rename(path: currentPath, newName: backupName)
            guard backupResponse.ok else {
                throw APIError.requestFailed(backupResponse.error ?? "Could not preserve the existing file.")
            }
        } catch {
            _ = try? await client.delete(path: resolvedTemporaryPath)
            throw error
        }

        do {
            let replaceResponse = try await client.rename(path: resolvedTemporaryPath, newName: filename)
            guard replaceResponse.ok else {
                throw APIError.requestFailed(replaceResponse.error ?? "Could not activate the uploaded file.")
            }
        } catch {
            _ = try? await client.rename(path: backupPath, newName: filename)
            _ = try? await client.delete(path: resolvedTemporaryPath)
            throw error
        }

        let cleanup = try? await client.delete(path: backupPath)
        if cleanup?.ok != true {
            logger.warning("Replacement succeeded but the backup could not be moved to trash: \(backupPath, privacy: .private)")
        }
    }

    private func stageUploadContents(_ contents: URL, preferredFilename: String) throws -> URL {
        let resourceValues = try contents.resourceValues(forKeys: [.isSymbolicLinkKey, .isRegularFileKey])
        guard resourceValues.isSymbolicLink != true, resourceValues.isRegularFile == true else {
            throw APIError.requestFailed("Symbolic links and non-regular files cannot be uploaded.")
        }
        let filename = sanitizedUploadFilename(preferredFilename, fallback: contents.lastPathComponent)
        let stagedURL = fileManager.temporaryDirectory
            .appendingPathComponent("homeos-upload-\(UUID().uuidString)-\(filename)")

        let startedSecurityScope = contents.startAccessingSecurityScopedResource()
        defer {
            if startedSecurityScope {
                contents.stopAccessingSecurityScopedResource()
            }
        }

        var coordinationError: NSError?
        var copyError: Error?
        let coordinator = NSFileCoordinator(filePresenter: nil)
        coordinator.coordinate(readingItemAt: contents, options: [], error: &coordinationError) { readableURL in
            do {
                if fileManager.fileExists(atPath: stagedURL.path) {
                    try fileManager.removeItem(at: stagedURL)
                }
                try fileManager.copyItem(at: readableURL, to: stagedURL)
            } catch {
                copyError = error
            }
        }

        if let coordinationError {
            throw coordinationError
        }
        if let copyError {
            throw copyError
        }
        guard fileManager.fileExists(atPath: stagedURL.path) else {
            throw APIError.requestFailed("Could not prepare \(filename) for upload.")
        }
        return stagedURL
    }

    private func sanitizedUploadFilename(_ preferredFilename: String, fallback: String) -> String {
        let preferred = preferredFilename.split(separator: "/").last.map(String.init)
        let fallback = fallback.split(separator: "/").last.map(String.init)
        return [preferred, fallback, "upload"]
            .compactMap { $0?.trimmingCharacters(in: .whitespacesAndNewlines) }
            .first { !$0.isEmpty } ?? "upload"
    }
}

private extension String {
    var pathExtension: String {
        (self as NSString).pathExtension
    }
}
