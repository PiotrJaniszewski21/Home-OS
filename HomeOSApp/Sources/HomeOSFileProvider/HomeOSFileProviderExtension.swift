import FileProvider
import Foundation

@objc(HomeOSFileProviderExtension)
final class HomeOSFileProviderExtension: NSObject, NSFileProviderReplicatedExtension, NSFileProviderEnumerating, NSFileProviderCustomAction {
    private let domain: NSFileProviderDomain
    private let backend = HomeOSFileProviderBackend()
    private let cacheEvictionCoordinator = HomeOSFileProviderCacheEvictionCoordinator.shared
    private let keepDownloadedStore = HomeOSFileProviderKeepDownloadedStore.shared

    required init(domain: NSFileProviderDomain) {
        self.domain = domain
        super.init()
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
        progressTask { [backend] in
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
        progressTask { [backend, cacheEvictionCoordinator, domain] in
            let (url, item) = try await backend.fetchContents(for: itemIdentifier)
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
        progressTask { [backend] in
            let item = try await backend.createItem(from: itemTemplate, contents: url)
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
        progressTask { [backend] in
            let item = try await backend.modifyItem(item, contents: newContents)
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
        progressTask { [backend] in
            try await backend.deleteItem(identifier: identifier)
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
        progressTask { [cacheEvictionCoordinator, domain, keepDownloadedStore] in
            let itemIdentifiers = itemIdentifiers.filter(Self.isActionable)
            guard !itemIdentifiers.isEmpty else { return }

            switch actionIdentifier {
            case HomeOSFileProviderAction.keepOnDisk:
                for itemIdentifier in itemIdentifiers {
                    keepDownloadedStore.setKept(true, for: itemIdentifier)
                    cacheEvictionCoordinator.cancelEviction(of: itemIdentifier)
                }
                try await Self.signalMetadataChanged(for: itemIdentifiers, in: domain)
                try await Self.requestDownloads(for: itemIdentifiers, in: domain)

            case HomeOSFileProviderAction.stopKeepingOnDisk:
                for itemIdentifier in itemIdentifiers {
                    keepDownloadedStore.setKept(false, for: itemIdentifier)
                    cacheEvictionCoordinator.evictNow(itemIdentifier, in: domain)
                }
                try await Self.signalMetadataChanged(for: itemIdentifiers, in: domain)

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
        _ operation: @escaping () async throws -> Void,
        onError: @escaping (Error) -> Void
    ) -> Progress {
        let progress = Progress(totalUnitCount: 1)
        Task {
            do {
                try await operation()
                progress.completedUnitCount = 1
            } catch {
                onError(error)
                progress.cancel()
            }
        }
        return progress
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
        in domain: NSFileProviderDomain
    ) async throws {
        guard let manager = NSFileProviderManager(for: domain) else {
            throw NSFileProviderError(.serverUnreachable)
        }

        var containers = Set<NSFileProviderItemIdentifier>()
        containers.insert(.workingSet)
        for itemIdentifier in itemIdentifiers {
            containers.insert(HomeOSFileProviderPath.parentIdentifier(for: itemIdentifier.rawValue))
        }

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
