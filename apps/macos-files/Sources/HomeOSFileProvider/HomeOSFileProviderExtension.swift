import FileProvider
import Foundation
import OSLog

@objc(HomeOSFileProviderExtension)
final class HomeOSFileProviderExtension: NSObject, NSFileProviderReplicatedExtension, NSFileProviderEnumerating, NSFileProviderCustomAction {
    private let transferLogger = Logger(subsystem: "uk.co.petershomenet.homeos", category: "transfer-entry")
    private let domain: NSFileProviderDomain
    private let backend = HomeOSFileProviderBackend()
    private let cacheEvictionCoordinator = HomeOSFileProviderCacheEvictionCoordinator.shared
    private let keepDownloadedStore = HomeOSFileProviderKeepDownloadedStore.shared

    required init(domain: NSFileProviderDomain) {
        self.domain = domain
        super.init()
        HomeOSTransferProgressBridge.reset()
        cacheEvictionCoordinator.resumePendingEvictions(in: domain)
    }

    func invalidate() {
        cacheEvictionCoordinator.cancelInMemoryWork()
    }

    func item(
        for identifier: NSFileProviderItemIdentifier,
        request: NSFileProviderRequest,
        completionHandler: @escaping (NSFileProviderItem?, Error?) -> Void
    ) -> Progress {
        progressTask { [backend] _ in
            completionHandler(try await backend.item(for: identifier), nil)
        } onError: { error in
            completionHandler(nil, HomeOSFileProviderErrorMapper.map(error))
        }
    }

    @objc(fetchContentsForItemWithIdentifier:version:request:completionHandler:)
    func fetchContents(
        for itemIdentifier: NSFileProviderItemIdentifier,
        version requestedVersion: NSFileProviderItemVersion?,
        request: NSFileProviderRequest,
        completionHandler: @escaping (URL?, NSFileProviderItem?, Error?) -> Void
    ) -> Progress {
        transferLogger.debug("File Provider download entry invoked")
        let filename = (try? HomeOSFileProviderPath.remotePath(for: itemIdentifier))
            .map(HomeOSFileProviderPath.filename(for:)) ?? "Download"
        return progressTask(fileOperationKind: .downloading, filename: filename) { [backend, cacheEvictionCoordinator, domain] progress in
            let (url, item) = try await backend.fetchContents(
                for: itemIdentifier,
                requestedVersion: requestedVersion,
                onProgress: { value in Self.update(progress, with: value) }
            )
            completionHandler(url, item, nil)
            cacheEvictionCoordinator.scheduleEviction(of: itemIdentifier, in: domain)
        } onError: { error in
            completionHandler(nil, nil, HomeOSFileProviderErrorMapper.map(error))
        }
    }

    func createItem(
        basedOn itemTemplate: NSFileProviderItem,
        fields: NSFileProviderItemFields,
        contents url: URL?,
        options: NSFileProviderCreateItemOptions = [],
        request: NSFileProviderRequest,
        completionHandler: @escaping (NSFileProviderItem?, NSFileProviderItemFields, Bool, Error?) -> Void
    ) -> Progress {
        transferLogger.debug("File Provider create upload entry invoked")
        return progressTask(fileOperationKind: .uploading, filename: itemTemplate.filename) { [backend] progress in
            let item = try await backend.createItem(
                from: itemTemplate,
                contents: url,
                onProgress: { value in Self.update(progress, with: value) }
            )
            completionHandler(item, [], false, nil)
        } onError: { error in
            completionHandler(nil, fields, false, HomeOSFileProviderErrorMapper.map(error))
        }
    }

    func modifyItem(
        _ item: NSFileProviderItem,
        baseVersion version: NSFileProviderItemVersion,
        changedFields: NSFileProviderItemFields,
        contents newContents: URL?,
        options: NSFileProviderModifyItemOptions = [],
        request: NSFileProviderRequest,
        completionHandler: @escaping (NSFileProviderItem?, NSFileProviderItemFields, Bool, Error?) -> Void
    ) -> Progress {
        transferLogger.debug("File Provider modify entry invoked, has contents: \(newContents != nil, privacy: .public)")
        return progressTask(
            fileOperationKind: newContents == nil ? .copying : .uploading,
            filename: item.filename
        ) { [backend] progress in
            let item = try await backend.modifyItem(
                item,
                baseVersion: version,
                changedFields: changedFields,
                contents: newContents,
                onProgress: { value in Self.update(progress, with: value) }
            )
            completionHandler(item, [], false, nil)
        } onError: { error in
            completionHandler(nil, changedFields, false, HomeOSFileProviderErrorMapper.map(error))
        }
    }

    func deleteItem(
        identifier: NSFileProviderItemIdentifier,
        baseVersion version: NSFileProviderItemVersion,
        options: NSFileProviderDeleteItemOptions = [],
        request: NSFileProviderRequest,
        completionHandler: @escaping (Error?) -> Void
    ) -> Progress {
        progressTask { [backend] _ in
            try await backend.deleteItem(identifier: identifier, baseVersion: version)
            completionHandler(nil)
        } onError: { error in
            completionHandler(HomeOSFileProviderErrorMapper.map(error))
        }
    }

    func performAction(
        identifier actionIdentifier: NSFileProviderExtensionActionIdentifier,
        onItemsWithIdentifiers itemIdentifiers: [NSFileProviderItemIdentifier],
        completionHandler: @escaping (Error?) -> Void
    ) -> Progress {
        progressTask { [backend, cacheEvictionCoordinator, domain, keepDownloadedStore] _ in
            let itemIdentifiers = itemIdentifiers.filter(Self.isActionable)
            guard !itemIdentifiers.isEmpty else { return }

            switch actionIdentifier {
            case HomeOSFileProviderAction.keepOnDisk:
                for itemIdentifier in itemIdentifiers {
                    keepDownloadedStore.setKept(true, for: itemIdentifier)
                    cacheEvictionCoordinator.cancelEviction(of: itemIdentifier)
                }
                try await Self.signalMetadataChanged(for: itemIdentifiers, backend: backend, in: domain)
                try await Self.requestDownloads(for: itemIdentifiers, in: domain)

            case HomeOSFileProviderAction.stopKeepingOnDisk:
                for itemIdentifier in itemIdentifiers {
                    keepDownloadedStore.setKept(false, for: itemIdentifier)
                    cacheEvictionCoordinator.evictNow(itemIdentifier, in: domain)
                }
                try await Self.signalMetadataChanged(for: itemIdentifiers, backend: backend, in: domain)

            default:
                throw NSFileProviderError(.serverUnreachable)
            }
            completionHandler(nil)
        } onError: { error in
            completionHandler(HomeOSFileProviderErrorMapper.map(error))
        }
    }

    @objc(materializedItemsDidChangeWithCompletionHandler:)
    func materializedItemsDidChange(completionHandler: @escaping () -> Void) {
        cacheEvictionCoordinator.resumePendingEvictions(in: domain)
        completionHandler()
    }

    @objc(pendingItemsDidChangeWithCompletionHandler:)
    func pendingItemsDidChange(completionHandler: @escaping () -> Void) {
        completionHandler()
    }

    func enumerator(
        for containerItemIdentifier: NSFileProviderItemIdentifier,
        request: NSFileProviderRequest
    ) throws -> NSFileProviderEnumerator {
        HomeOSFileProviderEnumerator(containerIdentifier: containerItemIdentifier, backend: backend)
    }

    private func progressTask(
        fileOperationKind: Progress.FileOperationKind? = nil,
        filename: String? = nil,
        _ operation: @escaping (Progress) async throws -> Void,
        onError: @escaping (Error) -> Void
    ) -> Progress {
        let progress = Progress(totalUnitCount: 1_000)
        if let fileOperationKind {
            progress.kind = .file
            progress.fileOperationKind = fileOperationKind
            progress.setUserInfoObject(1, forKey: .fileTotalCountKey)
            progress.setUserInfoObject(0, forKey: .fileCompletedCountKey)
            if let filename {
                let identifier = UUID()
                progress.fileURL = URL(fileURLWithPath: filename)
                progress.setUserInfoObject(identifier.uuidString, forKey: HomeOSTransferProgressBridge.progressIdentifierKey)
                if let kind = Self.transferKind(fileOperationKind) {
                    HomeOSTransferProgressBridge.postStarted(identifier: identifier, kind: kind, filename: filename)
                }
            }
        }
        let task = Task {
            defer { HomeOSTransferProgressBridge.postFinished(progress: progress) }
            do {
                try await operation(progress)
                if fileOperationKind != nil {
                    progress.setUserInfoObject(1, forKey: .fileCompletedCountKey)
                }
                progress.completedUnitCount = progress.totalUnitCount
            } catch {
                onError(error)
                progress.cancel()
            }
        }
        progress.cancellationHandler = { task.cancel() }
        return progress
    }

    private static func update(_ progress: Progress, with value: Double?) {
        guard let value else { return }
        let fractionCompleted = min(max(value, 0), 1)
        let completedUnitCount = Int64((fractionCompleted * Double(progress.totalUnitCount)).rounded())
        guard completedUnitCount != progress.completedUnitCount else { return }
        guard abs(completedUnitCount - progress.completedUnitCount) >= 20 || completedUnitCount == progress.totalUnitCount else { return }
        progress.completedUnitCount = completedUnitCount
        HomeOSTransferProgressBridge.postUpdated(progress: progress, fractionCompleted: fractionCompleted)
    }

    private static func transferKind(_ operation: Progress.FileOperationKind) -> FileTransferActivity.Kind? {
        switch operation {
        case .uploading:
            .upload
        case .downloading, .receiving, .decompressingAfterDownloading:
            .download
        default:
            nil
        }
    }

    private static func isActionable(_ identifier: NSFileProviderItemIdentifier) -> Bool {
        identifier != .rootContainer
            && identifier != .workingSet
            && identifier != .trashContainer
            && identifier.rawValue.hasPrefix("/")
    }

    private static func requestDownloads(
        for itemIdentifiers: [NSFileProviderItemIdentifier],
        in domain: NSFileProviderDomain
    ) async throws {
        guard let manager = NSFileProviderManager(for: domain) else {
            throw NSFileProviderError(.serverUnreachable)
        }

        for itemIdentifier in itemIdentifiers {
            try await withCheckedThrowingContinuation { (continuation: CheckedContinuation<Void, Error>) in
                manager.requestDownloadForItem(
                    withIdentifier: itemIdentifier,
                    requestedRange: NSRange(location: NSNotFound, length: 0)
                ) { error in
                    if let error {
                        continuation.resume(throwing: error)
                    } else {
                        continuation.resume()
                    }
                }
            }
        }
    }

    private static func signalMetadataChanged(
        for itemIdentifiers: [NSFileProviderItemIdentifier],
        backend: HomeOSFileProviderBackend,
        in domain: NSFileProviderDomain
    ) async throws {
        guard let manager = NSFileProviderManager(for: domain) else {
            throw NSFileProviderError(.serverUnreachable)
        }

        var containers = Set<NSFileProviderItemIdentifier>()
        containers.insert(.workingSet)
        for itemIdentifier in itemIdentifiers {
            if let parent = try? backend.parentIdentifier(for: itemIdentifier) {
                containers.insert(parent)
            }
        }
        HomeOSFileProviderSnapshotStore.shared.markUpdated(
            identifiers: itemIdentifiers.map(\.rawValue),
            in: Set(containers.map(\.rawValue))
        )

        for container in containers {
            try await withCheckedThrowingContinuation { (continuation: CheckedContinuation<Void, Error>) in
                manager.signalEnumerator(for: container) { error in
                    if let error {
                        continuation.resume(throwing: error)
                    } else {
                        continuation.resume()
                    }
                }
            }
        }
    }
}

private enum HomeOSFileProviderAction {
    static let keepOnDisk = NSFileProviderExtensionActionIdentifier("uk.co.petershomenet.homeos.keep-on-disk")
    static let stopKeepingOnDisk = NSFileProviderExtensionActionIdentifier("uk.co.petershomenet.homeos.stop-keeping-on-disk")
}
