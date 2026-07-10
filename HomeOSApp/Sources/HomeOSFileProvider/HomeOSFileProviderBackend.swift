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
        self.remoteSession = URLSession(configuration: remoteConfiguration)
        self.localSession = URLSession(configuration: localConfiguration, delegate: localCertificateTrustDelegate, delegateQueue: nil)
    }

    func item(for identifier: NSFileProviderItemIdentifier) async throws -> HomeOSFileProviderItem {
        if identifier == .rootContainer || identifier == .workingSet {
            return .root()
        }

        let remotePath = try HomeOSFileProviderPath.remotePath(for: identifier)
        let parentPath = HomeOSFileProviderPath.parentPath(for: remotePath)
        let entries = try await listEntries(path: parentPath)
        guard let entry = entries.first(where: { HomeOSFileProviderPath.normalize($0.path) == remotePath }) else {
            throw NSFileProviderError(.noSuchItem)
        }
        return .from(entry: entry, keepDownloadedStore: keepDownloadedStore)
    }

    func listItems(in containerIdentifier: NSFileProviderItemIdentifier) async throws -> [HomeOSFileProviderItem] {
        if containerIdentifier == .trashContainer {
            return []
        }
        let remotePath = try HomeOSFileProviderPath.remotePath(for: containerIdentifier)
        return try await listEntries(path: remotePath)
            .sorted { lhs, rhs in
                if lhs.isDirectory != rhs.isDirectory { return lhs.isDirectory && !rhs.isDirectory }
                return lhs.name.localizedStandardCompare(rhs.name) == .orderedAscending
            }
            .map { HomeOSFileProviderItem.from(entry: $0, keepDownloadedStore: keepDownloadedStore) }
    }

    func fetchContents(for identifier: NSFileProviderItemIdentifier) async throws -> (URL, HomeOSFileProviderItem) {
        let remotePath = try HomeOSFileProviderPath.remotePath(for: identifier)
        let item = try await item(for: identifier)
        let temporaryURL = try await withClient { client in
            try await client.downloadFileToTemporaryURL(path: remotePath)
        }
        return (temporaryURL, item)
    }

    func createItem(from template: NSFileProviderItem, contents: URL?) async throws -> HomeOSFileProviderItem {
        let parentPath = try HomeOSFileProviderPath.remotePath(for: template.parentItemIdentifier)
        let targetPath = HomeOSFileProviderPath.join(parentPath, template.filename)

        if isDirectory(template, contents: contents) {
            try await withClient { client in
                let response = try await client.createDirectory(path: targetPath)
                if !response.ok {
                    throw APIError.requestFailed(response.error ?? "Could not create \(template.filename).")
                }
            }
        } else if let contents {
            let stagedContents = try stageUploadContents(contents, preferredFilename: template.filename)
            defer { try? fileManager.removeItem(at: stagedContents) }
            try await withClient { client in
                try await client.upload(fileURL: stagedContents, to: parentPath, filename: template.filename)
            }
        } else {
            let emptyFile = fileManager.temporaryDirectory
                .appendingPathComponent("homeos-empty-\(UUID().uuidString)-\(template.filename)")
            fileManager.createFile(atPath: emptyFile.path, contents: Data())
            defer { try? fileManager.removeItem(at: emptyFile) }
            try await withClient { client in
                try await client.upload(fileURL: emptyFile, to: parentPath, filename: template.filename)
            }
        }

        return try await item(for: HomeOSFileProviderPath.identifier(for: targetPath))
    }

    func modifyItem(_ template: NSFileProviderItem, contents: URL?) async throws -> HomeOSFileProviderItem {
        let originalIdentifier = template.itemIdentifier
        var currentPath = try HomeOSFileProviderPath.remotePath(for: template.itemIdentifier)
        let requestedParentPath = try HomeOSFileProviderPath.remotePath(for: template.parentItemIdentifier)
        let currentParentPath = HomeOSFileProviderPath.parentPath(for: currentPath)
        let currentFilename = HomeOSFileProviderPath.filename(for: currentPath)

        if requestedParentPath != currentParentPath {
            try await withClient { client in
                let response = try await client.move(sourcePath: currentPath, destinationPath: requestedParentPath)
                if !response.ok {
                    throw APIError.requestFailed(response.error ?? "Could not move \(currentFilename).")
                }
            }
            currentPath = HomeOSFileProviderPath.join(requestedParentPath, currentFilename)
        }

        if template.filename != HomeOSFileProviderPath.filename(for: currentPath) {
            try await withClient { client in
                let response = try await client.rename(path: currentPath, newName: template.filename)
                if !response.ok {
                    throw APIError.requestFailed(response.error ?? "Could not rename \(currentFilename).")
                }
            }
            currentPath = HomeOSFileProviderPath.join(HomeOSFileProviderPath.parentPath(for: currentPath), template.filename)
        }

        if let contents, !isDirectory(template, contents: contents) {
            let uploadParentPath = HomeOSFileProviderPath.parentPath(for: currentPath)
            let stagedContents = try stageUploadContents(contents, preferredFilename: template.filename)
            defer { try? fileManager.removeItem(at: stagedContents) }
            try await withClient { client in
                _ = try? await client.delete(path: currentPath)
                try await client.upload(fileURL: stagedContents, to: uploadParentPath, filename: template.filename)
            }
        }

        let newIdentifier = HomeOSFileProviderPath.identifier(for: currentPath)
        keepDownloadedStore.moveKeptState(from: originalIdentifier, to: newIdentifier)
        return try await item(for: newIdentifier)
    }

    func deleteItem(identifier: NSFileProviderItemIdentifier) async throws {
        let remotePath = try HomeOSFileProviderPath.remotePath(for: identifier)
        try await withClient { client in
            let response = try await client.delete(path: remotePath)
            if !response.ok {
                throw APIError.requestFailed(response.error ?? "Could not delete \(HomeOSFileProviderPath.filename(for: remotePath)).")
            }
        }
        keepDownloadedStore.remove(identifier)
    }

    private func listEntries(path: String) async throws -> [FileEntry] {
        try await withClient { client in
            let response = try await client.listDirectory(path: path)
            guard response.ok, let data = response.data else {
                throw APIError.requestFailed(response.error ?? "Could not list \(path).")
            }
            return data.entries
        }
    }

    private func withClient<T>(_ operation: (APIClient) async throws -> T) async throws -> T {
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

        var failures: [String] = []
        for endpoint in candidates {
            do {
                let client = try APIClient(
                    baseURL: endpoint.url,
                    authToken: settings.authToken,
                    session: endpoint.kind == .local ? localSession : remoteSession,
                    trustsLocalSelfSignedCertificates: endpoint.kind == .local
                )
                let result = try await operation(client)
                logger.info("File Provider request succeeded via \(endpoint.kind.rawValue, privacy: .public) endpoint \(endpoint.displayName, privacy: .public)")
                return result
            } catch {
                logger.error("File Provider request failed via \(endpoint.kind.rawValue, privacy: .public) endpoint \(endpoint.displayName, privacy: .public): \(error.localizedDescription, privacy: .public)")
                failures.append("\(endpoint.displayName): \(error.localizedDescription)")
            }
        }

        throw APIError.requestFailed(failures.joined(separator: "\n"))
    }

    private func isDirectory(_ item: NSFileProviderItem, contents: URL?) -> Bool {
        if item.contentType?.conforms(to: .folder) == true {
            return true
        }
        if contents == nil, item.filename.pathExtension.isEmpty {
            return true
        }
        return false
    }

    private func stageUploadContents(_ contents: URL, preferredFilename: String) throws -> URL {
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
