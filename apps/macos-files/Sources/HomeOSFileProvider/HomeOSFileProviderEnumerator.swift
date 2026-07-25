import FileProvider
import Foundation
import os

final class HomeOSFileProviderEnumerator: NSObject, NSFileProviderEnumerator {
    private static let logger = Logger(subsystem: "uk.co.petershomenet.homeos", category: "FileProvider")

    private let containerIdentifier: NSFileProviderItemIdentifier
    private let backend: HomeOSFileProviderBackend
    private let snapshotStore = HomeOSFileProviderSnapshotStore.shared
    private var isInvalidated = false
    private var enumerationTask: Task<Void, Never>?
    private var cachedItems: [HomeOSFileProviderItem]?
    private static let pageSize = 200

    init(containerIdentifier: NSFileProviderItemIdentifier, backend: HomeOSFileProviderBackend) {
        self.containerIdentifier = containerIdentifier
        self.backend = backend
        super.init()
    }

    func invalidate() {
        isInvalidated = true
        enumerationTask?.cancel()
        enumerationTask = nil
        cachedItems = nil
        Self.logger.debug("Invalidated enumerator for \(self.containerIdentifier.rawValue, privacy: .public)")
    }

    func enumerateItems(
        for observer: NSFileProviderEnumerationObserver,
        startingAt page: NSFileProviderPage
    ) {
        enumerationTask?.cancel()
        enumerationTask = Task {
            guard !isInvalidated else { return }
            do {
                Self.logger.info("Enumerating items for \(self.containerIdentifier.rawValue, privacy: .public)")
                let offset = Self.offset(from: page)
                let items: [HomeOSFileProviderItem]
                if offset == 0 || cachedItems == nil {
                    items = try await backend.listItems(in: containerIdentifier)
                    cachedItems = items
                    _ = snapshotStore.recordFullEnumeration(
                        containerIdentifier: containerIdentifier.rawValue,
                        snapshots: items.map(\.snapshot)
                    )
                } else {
                    items = cachedItems ?? []
                }
                guard !Task.isCancelled, !isInvalidated else { return }
                let end = min(offset + Self.pageSize, items.count)
                let pageItems = offset < end ? Array(items[offset..<end]) : []
                Self.logger.info("Enumerated \(pageItems.count, privacy: .public) item(s) for \(self.containerIdentifier.rawValue, privacy: .public)")
                observer.didEnumerate(pageItems)
                if end < items.count {
                    observer.finishEnumerating(upTo: NSFileProviderPage(Data(String(end).utf8)))
                } else {
                    cachedItems = nil
                    observer.finishEnumerating(upTo: nil)
                }
            } catch {
                guard !Task.isCancelled, !isInvalidated else { return }
                Self.logger.error("Enumeration failed for \(self.containerIdentifier.rawValue, privacy: .public): \(error.localizedDescription, privacy: .public)")
                observer.finishEnumeratingWithError(HomeOSFileProviderErrorMapper.map(error))
            }
        }
    }

    func enumerateChanges(
        for observer: NSFileProviderChangeObserver,
        from anchor: NSFileProviderSyncAnchor
    ) {
        enumerationTask?.cancel()
        enumerationTask = Task {
            guard !isInvalidated else { return }
            do {
                Self.logger.info("Enumerating changes for \(self.containerIdentifier.rawValue, privacy: .public)")
                var items = try await backend.listItems(in: containerIdentifier)
                let pendingIdentifiers = snapshotStore.pendingUpdatedIdentifiers(
                    in: containerIdentifier.rawValue
                )
                var knownIdentifiers = Set(items.map { $0.itemIdentifier.rawValue })
                for rawIdentifier in pendingIdentifiers where knownIdentifiers.insert(rawIdentifier).inserted {
                    if let item = try? await backend.item(
                        for: NSFileProviderItemIdentifier(rawIdentifier)
                    ) {
                        items.append(item)
                    }
                }
                guard !Task.isCancelled, !isInvalidated else { return }
                let changes = try snapshotStore.changes(
                    containerIdentifier: containerIdentifier.rawValue,
                    from: anchor.rawValue,
                    current: items.map(\.snapshot),
                    preserveMissingItems: containerIdentifier == .workingSet
                )
                let updatedItems = items.filter { changes.updatedIdentifiers.contains($0.itemIdentifier.rawValue) }
                let deletedIdentifiers = changes.deletedIdentifiers.map { rawValue in
                    NSFileProviderItemIdentifier(rawValue)
                }
                Self.logger.info("Enumerated \(updatedItems.count, privacy: .public) update(s) and \(deletedIdentifiers.count, privacy: .public) deletion(s) for \(self.containerIdentifier.rawValue, privacy: .public)")
                if !updatedItems.isEmpty {
                    observer.didUpdate(updatedItems)
                }
                if !deletedIdentifiers.isEmpty {
                    observer.didDeleteItems(withIdentifiers: deletedIdentifiers)
                }
                observer.finishEnumeratingChanges(
                    upTo: NSFileProviderSyncAnchor(changes.anchor),
                    moreComing: false
                )
            } catch {
                guard !Task.isCancelled, !isInvalidated else { return }
                Self.logger.error("Change enumeration failed for \(self.containerIdentifier.rawValue, privacy: .public): \(error.localizedDescription, privacy: .public)")
                observer.finishEnumeratingWithError(HomeOSFileProviderErrorMapper.map(error))
            }
        }
    }

    func currentSyncAnchor(completionHandler: @escaping (NSFileProviderSyncAnchor?) -> Void) {
        completionHandler(currentAnchor())
    }

    private func currentAnchor() -> NSFileProviderSyncAnchor {
        NSFileProviderSyncAnchor(snapshotStore.currentAnchor(containerIdentifier: containerIdentifier.rawValue))
    }

    private static func offset(from page: NSFileProviderPage) -> Int {
        guard let value = String(data: page.rawValue, encoding: .utf8), let offset = Int(value) else {
            return 0
        }
        return max(0, offset)
    }
}
