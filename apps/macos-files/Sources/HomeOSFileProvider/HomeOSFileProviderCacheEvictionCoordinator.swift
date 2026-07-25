import Darwin
import FileProvider
import Foundation
import OSLog

final class HomeOSFileProviderCacheEvictionCoordinator {
    static let shared = HomeOSFileProviderCacheEvictionCoordinator()

    private let evictionDelay: TimeInterval
    private let retryDelay: TimeInterval
    private let maximumRetryCount: Int
    private let defaults: UserDefaults?
    private let keepDownloadedStore: HomeOSFileProviderKeepDownloadedStore
    private let queue = DispatchQueue(label: "uk.co.petershomenet.homeos.fileprovider.cache-eviction")
    private let logger = Logger(subsystem: "uk.co.petershomenet.homeos", category: "FileProviderCache")
    private let defaultsKey = "HomeOSFileProviderScheduledEvictions"
    private var workItems: [String: DispatchWorkItem] = [:]

    init(
        evictionDelay: TimeInterval = 10 * 60,
        retryDelay: TimeInterval = 60,
        maximumRetryCount: Int = 60,
        defaults: UserDefaults? = .standard,
        keepDownloadedStore: HomeOSFileProviderKeepDownloadedStore = .shared
    ) {
        self.evictionDelay = evictionDelay
        self.retryDelay = retryDelay
        self.maximumRetryCount = maximumRetryCount
        self.defaults = defaults
        self.keepDownloadedStore = keepDownloadedStore
    }

    func scheduleEviction(of identifier: NSFileProviderItemIdentifier, in domain: NSFileProviderDomain) {
        guard shouldSchedule(identifier) else { return }

        let deadline = Date().addingTimeInterval(evictionDelay)
        queue.async { [weak self] in
            guard let self else { return }
            if keepDownloadedStore.isKept(identifier) {
                cancelEvictionLocked(of: identifier)
                return
            }
            storeDeadline(deadline, for: identifier.rawValue)
            scheduleEvictionLocked(of: identifier, in: domain, at: deadline, retryCount: 0)
        }
    }

    func cancelEviction(of identifier: NSFileProviderItemIdentifier) {
        queue.async { [weak self] in
            self?.cancelEvictionLocked(of: identifier)
        }
    }

    func evictNow(_ identifier: NSFileProviderItemIdentifier, in domain: NSFileProviderDomain) {
        guard shouldSchedule(identifier) else { return }

        queue.async { [weak self] in
            guard let self else { return }
            let rawIdentifier = identifier.rawValue
            workItems[rawIdentifier]?.cancel()
            workItems[rawIdentifier] = nil
            removeDeadline(for: rawIdentifier)
            evict(identifier, in: domain, retryCount: 0)
        }
    }

    func resumePendingEvictions(in domain: NSFileProviderDomain) {
        queue.async { [weak self] in
            guard let self else { return }

            let deadlines = loadDeadlines()
            for (rawIdentifier, deadlineInterval) in deadlines {
                let identifier = NSFileProviderItemIdentifier(rawIdentifier)
                guard shouldSchedule(identifier) else {
                    removeDeadline(for: rawIdentifier)
                    continue
                }
                guard !keepDownloadedStore.isKept(identifier) else {
                    cancelEvictionLocked(of: identifier)
                    continue
                }

                scheduleEvictionLocked(
                    of: identifier,
                    in: domain,
                    at: Date(timeIntervalSince1970: deadlineInterval),
                    retryCount: 0
                )
            }
        }
    }

    func cancelInMemoryWork() {
        queue.async { [weak self] in
            guard let self else { return }

            for workItem in workItems.values {
                workItem.cancel()
            }
            workItems.removeAll()
        }
    }

    private func scheduleEvictionLocked(
        of identifier: NSFileProviderItemIdentifier,
        in domain: NSFileProviderDomain,
        at deadline: Date,
        retryCount: Int
    ) {
        let rawIdentifier = identifier.rawValue
        workItems[rawIdentifier]?.cancel()

        let delay = max(0, deadline.timeIntervalSinceNow)
        let workItem = DispatchWorkItem { [weak self] in
            self?.evict(identifier, in: domain, retryCount: retryCount)
        }

        workItems[rawIdentifier] = workItem
        queue.asyncAfter(deadline: .now() + delay, execute: workItem)
        logger.info("Scheduled File Provider cache eviction for item \(rawIdentifier, privacy: .private) in \(Int(delay), privacy: .public) seconds")
    }

    private func scheduleRetryLocked(
        of identifier: NSFileProviderItemIdentifier,
        in domain: NSFileProviderDomain,
        retryCount: Int
    ) {
        let deadline = Date().addingTimeInterval(retryDelay)
        scheduleEvictionLocked(of: identifier, in: domain, at: deadline, retryCount: retryCount)
    }

    private func evict(
        _ identifier: NSFileProviderItemIdentifier,
        in domain: NSFileProviderDomain,
        retryCount: Int
    ) {
        let rawIdentifier = identifier.rawValue
        workItems[rawIdentifier] = nil

        guard !keepDownloadedStore.isKept(identifier) else {
            removeDeadline(for: rawIdentifier)
            logger.info("Skipped File Provider cache eviction for kept item \(rawIdentifier, privacy: .private)")
            return
        }

        guard let manager = NSFileProviderManager(for: domain) else {
            logger.error("Unable to evict cached File Provider item because no manager exists for domain \(domain.identifier.rawValue, privacy: .private)")
            if retryCount < maximumRetryCount {
                scheduleRetryLocked(of: identifier, in: domain, retryCount: retryCount + 1)
            }
            return
        }

        manager.evictItem(identifier: identifier) { [weak self] error in
            self?.queue.async {
                self?.handleEvictionResult(
                    error,
                    identifier: identifier,
                    domain: domain,
                    retryCount: retryCount
                )
            }
        }
    }

    private func handleEvictionResult(
        _ error: Error?,
        identifier: NSFileProviderItemIdentifier,
        domain: NSFileProviderDomain,
        retryCount: Int
    ) {
        let rawIdentifier = identifier.rawValue

        if let error {
            let nsError = error as NSError
            if isRetryableBusyError(nsError), retryCount < maximumRetryCount {
                logger.info("Cached File Provider item is busy; retrying eviction for item \(rawIdentifier, privacy: .private)")
                scheduleRetryLocked(of: identifier, in: domain, retryCount: retryCount + 1)
                return
            }

            removeDeadline(for: rawIdentifier)
            logger.info("File Provider cache eviction skipped for item \(rawIdentifier, privacy: .private): \(nsError.domain, privacy: .public) \(nsError.code, privacy: .public)")
            return
        }

        removeDeadline(for: rawIdentifier)
        logger.info("Evicted cached File Provider item \(rawIdentifier, privacy: .private)")
    }

    private func isRetryableBusyError(_ error: NSError) -> Bool {
        error.domain == NSPOSIXErrorDomain && error.code == Int(EBUSY)
    }

    private func shouldSchedule(_ identifier: NSFileProviderItemIdentifier) -> Bool {
        identifier != .rootContainer
            && identifier != .workingSet
            && identifier != .trashContainer
    }

    private func storeDeadline(_ deadline: Date, for rawIdentifier: String) {
        var deadlines = loadDeadlines()
        deadlines[rawIdentifier] = deadline.timeIntervalSince1970
        defaults?.set(deadlines, forKey: defaultsKey)
        defaults?.synchronize()
    }

    private func removeDeadline(for rawIdentifier: String) {
        var deadlines = loadDeadlines()
        deadlines.removeValue(forKey: rawIdentifier)
        defaults?.set(deadlines, forKey: defaultsKey)
        defaults?.synchronize()
    }

    private func cancelEvictionLocked(of identifier: NSFileProviderItemIdentifier) {
        let rawIdentifier = identifier.rawValue
        workItems[rawIdentifier]?.cancel()
        workItems[rawIdentifier] = nil
        removeDeadline(for: rawIdentifier)
        logger.info("Cancelled File Provider cache eviction for item \(rawIdentifier, privacy: .private)")
    }

    private func loadDeadlines() -> [String: TimeInterval] {
        defaults?.dictionary(forKey: defaultsKey) as? [String: TimeInterval] ?? [:]
    }
}
