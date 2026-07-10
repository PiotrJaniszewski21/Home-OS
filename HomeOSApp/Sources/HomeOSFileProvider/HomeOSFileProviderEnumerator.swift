import FileProvider
import Foundation
import os

final class HomeOSFileProviderEnumerator: NSObject, NSFileProviderEnumerator {
    private static let logger = Logger(subsystem: "uk.co.petershomenet.homeos", category: "FileProvider")

    private let containerIdentifier: NSFileProviderItemIdentifier
    private let backend: HomeOSFileProviderBackend
    private var isInvalidated = false
    private var enumerationTask: Task<Void, Never>?

    init(containerIdentifier: NSFileProviderItemIdentifier, backend: HomeOSFileProviderBackend) {
        self.containerIdentifier = containerIdentifier
        self.backend = backend
        super.init()
    }

    func invalidate() {
        isInvalidated = true
        enumerationTask?.cancel()
        enumerationTask = nil
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
                let items = try await backend.listItems(in: containerIdentifier)
                guard !Task.isCancelled, !isInvalidated else { return }
                Self.logger.info("Enumerated \(items.count, privacy: .public) item(s) for \(self.containerIdentifier.rawValue, privacy: .public)")
                observer.didEnumerate(items)
                observer.finishEnumerating(upTo: nil)
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
                let items = try await backend.listItems(in: containerIdentifier)
                guard !Task.isCancelled, !isInvalidated else { return }
                Self.logger.info("Enumerated \(items.count, privacy: .public) change item(s) for \(self.containerIdentifier.rawValue, privacy: .public)")
                observer.didUpdate(items)
                observer.finishEnumeratingChanges(upTo: currentAnchor(), moreComing: false)
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
        NSFileProviderSyncAnchor(Data("\(Date().timeIntervalSince1970)".utf8))
    }
}
