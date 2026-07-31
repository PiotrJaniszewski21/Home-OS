import FileProvider
import Foundation
import OSLog

private let fileProviderStateLogger = Logger(subsystem: "uk.co.petershomenet.homeos", category: "FileProviderState")

final class HomeOSFileProviderIdentityStore: @unchecked Sendable {
    static let shared = HomeOSFileProviderIdentityStore()

    private let defaults: UserDefaults?
    private let defaultsKey = "HomeOSFileProviderPathIdentifiers"
    private let lock = NSLock()

    init(defaults: UserDefaults? = .standard) {
        self.defaults = defaults
    }

    func identifier(forNormalizedPath path: String) -> NSFileProviderItemIdentifier {
        guard path != "/" else { return .rootContainer }
        return lock.withLock {
            var paths = loadLocked()
            if let rawIdentifier = paths[path] {
                return NSFileProviderItemIdentifier(rawIdentifier)
            }
            let rawIdentifier = paths.values.contains(path)
                ? uniqueIdentifier(excluding: Set(paths.values))
                : path
            let identifier = NSFileProviderItemIdentifier(rawIdentifier)
            paths[path] = identifier.rawValue
            saveLocked(paths)
            return identifier
        }
    }

    func path(for identifier: NSFileProviderItemIdentifier) -> String? {
        if identifier == .rootContainer || identifier == .workingSet {
            return "/"
        }
        return lock.withLock {
            var paths = loadLocked()
            if let path = paths.first(where: { $0.value == identifier.rawValue })?.key {
                return path
            }
            guard identifier.rawValue.hasPrefix("/") else { return nil }
            paths[identifier.rawValue] = identifier.rawValue
            saveLocked(paths)
            return identifier.rawValue
        }
    }

    func move(identifier: NSFileProviderItemIdentifier, toNormalizedPath path: String) {
        guard identifier != .rootContainer, path != "/" else { return }
        lock.withLock {
            var paths = loadLocked()
            paths = paths.filter { $0.value != identifier.rawValue && $0.key != path }
            paths[path] = identifier.rawValue
            saveLocked(paths)
        }
    }

    func moveTree(
        fromNormalizedPath oldPath: String,
        toNormalizedPath newPath: String,
        rootIdentifier: NSFileProviderItemIdentifier
    ) {
        guard oldPath != newPath, oldPath != "/", newPath != "/" else { return }
        lock.withLock {
            var paths = loadLocked()
            let moved = paths.filter { path, _ in
                path == oldPath || path.hasPrefix(oldPath + "/")
            }
            for path in moved.keys {
                paths.removeValue(forKey: path)
            }
            paths = paths.filter { $0.value != rootIdentifier.rawValue }
            for (path, identifier) in moved {
                let suffix = String(path.dropFirst(oldPath.count))
                paths[newPath + suffix] = identifier
            }
            paths[newPath] = rootIdentifier.rawValue
            saveLocked(paths)
        }
    }

    func remove(identifier: NSFileProviderItemIdentifier) {
        lock.withLock {
            let filtered = loadLocked().filter { $0.value != identifier.rawValue }
            saveLocked(filtered)
        }
    }

    func clear() {
        lock.withLock {
            defaults?.removeObject(forKey: defaultsKey)
        }
    }

    private func loadLocked() -> [String: String] {
        let stored = defaults?.dictionary(forKey: defaultsKey) as? [String: String] ?? [:]
        let repaired = repairDuplicateIdentifiers(in: stored)
        if repaired != stored {
            saveLocked(repaired)
            fileProviderStateLogger.warning("Repaired duplicate File Provider path identifiers")
        }
        return repaired
    }

    private func saveLocked(_ paths: [String: String]) {
        defaults?.set(paths, forKey: defaultsKey)
    }

    private func repairDuplicateIdentifiers(in paths: [String: String]) -> [String: String] {
        let groupedPaths = Dictionary(grouping: paths.keys) { paths[$0] ?? "" }
        var repaired = paths
        var occupiedIdentifiers = Set(paths.values)

        for identifier in groupedPaths.keys.sorted() {
            guard let matchingPaths = groupedPaths[identifier], matchingPaths.count > 1 else {
                continue
            }
            let orderedPaths = matchingPaths.sorted { lhs, rhs in
                if lhs == identifier { return true }
                if rhs == identifier { return false }
                return lhs.localizedStandardCompare(rhs) == .orderedAscending
            }
            for path in orderedPaths.dropFirst() {
                let replacement = uniqueIdentifier(excluding: occupiedIdentifiers)
                repaired[path] = replacement
                occupiedIdentifiers.insert(replacement)
            }
        }
        return repaired
    }

    private func uniqueIdentifier(excluding occupied: Set<String>) -> String {
        while true {
            let candidate = "homeos-\(UUID().uuidString.lowercased())"
            if !occupied.contains(candidate) {
                return candidate
            }
        }
    }
}

struct HomeOSFileProviderSnapshot: Codable, Equatable, Sendable {
    let itemIdentifier: String
    let parentIdentifier: String
    let fingerprint: String
}

struct HomeOSFileProviderChangeSet: Equatable, Sendable {
    let updatedIdentifiers: Set<String>
    let deletedIdentifiers: Set<String>
    let anchor: Data
}

final class HomeOSFileProviderSnapshotStore: @unchecked Sendable {
    static let shared = HomeOSFileProviderSnapshotStore()

    private struct ContainerHistory: Codable {
        var currentAnchor: String
        var states: [String: [String: HomeOSFileProviderSnapshot]]
        var order: [String]
    }

    private let defaults: UserDefaults?
    private let defaultsKey = "HomeOSFileProviderContainerSnapshots"
    private let forcedUpdatesDefaultsKey = "HomeOSFileProviderForcedMetadataUpdates"
    private let lock = NSLock()

    init(defaults: UserDefaults? = .standard) {
        self.defaults = defaults
    }

    func recordFullEnumeration(containerIdentifier: String, snapshots: [HomeOSFileProviderSnapshot]) -> Data {
        lock.withLock {
            var histories = loadLocked()
            let anchor = UUID().uuidString
            let items = Dictionary(uniqueKeysWithValues: snapshots.map { ($0.itemIdentifier, $0) })
            var history = histories[containerIdentifier]
                ?? ContainerHistory(currentAnchor: anchor, states: [:], order: [])
            history.currentAnchor = anchor
            history.states[anchor] = items
            history.order.removeAll { $0 == anchor }
            history.order.append(anchor)
            trim(&history)
            histories[containerIdentifier] = history
            saveLocked(histories)
            return Data(anchor.utf8)
        }
    }

    func changes(
        containerIdentifier: String,
        from anchor: Data,
        current snapshots: [HomeOSFileProviderSnapshot],
        authoritativeParentIdentifiers: Set<String>? = nil
    ) throws -> HomeOSFileProviderChangeSet {
        try lock.withLock {
            var histories = loadLocked()
            let requestedAnchor = String(data: anchor, encoding: .utf8) ?? ""
            let history = histories[containerIdentifier]
            let previousItems: [String: HomeOSFileProviderSnapshot]
            if requestedAnchor == "uninitialized", history == nil {
                previousItems = [:]
            } else if let items = history?.states[requestedAnchor] {
                previousItems = items
            } else {
                throw NSFileProviderError(.syncAnchorExpired)
            }

            var current = Dictionary(uniqueKeysWithValues: snapshots.map { ($0.itemIdentifier, $0) })
            if let authoritativeParentIdentifiers {
                for (identifier, snapshot) in previousItems
                where current[identifier] == nil
                    && !authoritativeParentIdentifiers.contains(snapshot.parentIdentifier) {
                    current[identifier] = snapshot
                }
            }
            var updated = Set(current.compactMap { identifier, snapshot in
                previousItems[identifier] == snapshot ? nil : identifier
            })
            var forcedUpdates = loadForcedUpdatesLocked()
            let forcedForContainer = forcedUpdates[containerIdentifier, default: []]
                .intersection(current.keys)
            fileProviderStateLogger.info("Applying \(forcedForContainer.count, privacy: .public) of \(forcedUpdates[containerIdentifier, default: []].count, privacy: .public) queued metadata update(s) for container \(containerIdentifier, privacy: .public)")
            updated.formUnion(forcedForContainer)
            forcedUpdates[containerIdentifier]?.subtract(forcedForContainer)
            saveForcedUpdatesLocked(forcedUpdates)
            let deleted = Set(previousItems.keys).subtracting(current.keys)
            let newAnchor = UUID().uuidString
            var updatedHistory = history
                ?? ContainerHistory(currentAnchor: newAnchor, states: [:], order: [])
            updatedHistory.currentAnchor = newAnchor
            updatedHistory.states[newAnchor] = current
            updatedHistory.order.removeAll { $0 == newAnchor }
            updatedHistory.order.append(newAnchor)
            trim(&updatedHistory)
            histories[containerIdentifier] = updatedHistory
            saveLocked(histories)
            return HomeOSFileProviderChangeSet(
                updatedIdentifiers: updated,
                deletedIdentifiers: deleted,
                anchor: Data(newAnchor.utf8)
            )
        }
    }

    func currentAnchor(containerIdentifier: String) -> Data {
        lock.withLock {
            guard let anchor = loadLocked()[containerIdentifier]?.currentAnchor else {
                return Data("uninitialized".utf8)
            }
            return Data(anchor.utf8)
        }
    }

    func remove(identifier: String) {
        lock.withLock {
            var histories = loadLocked()
            for (container, var history) in histories {
                var changed = false
                for anchor in Array(history.states.keys) {
                    if history.states[anchor]?.removeValue(forKey: identifier) != nil {
                        changed = true
                    }
                }
                if changed {
                    histories[container] = history
                }
            }
            saveLocked(histories)
            var forcedUpdates = loadForcedUpdatesLocked()
            for container in Array(forcedUpdates.keys) {
                forcedUpdates[container]?.remove(identifier)
            }
            saveForcedUpdatesLocked(forcedUpdates)
        }
    }

    func clear() {
        lock.withLock {
            defaults?.removeObject(forKey: defaultsKey)
            defaults?.removeObject(forKey: forcedUpdatesDefaultsKey)
        }
    }

    func markUpdated(identifiers: [String], in containers: Set<String>) {
        guard !identifiers.isEmpty, !containers.isEmpty else { return }
        lock.withLock {
            var forcedUpdates = loadForcedUpdatesLocked()
            for container in containers {
                forcedUpdates[container, default: []].formUnion(identifiers)
            }
            saveForcedUpdatesLocked(forcedUpdates)
            fileProviderStateLogger.info("Queued \(identifiers.count, privacy: .public) metadata update(s) for \(containers.count, privacy: .public) container(s)")
        }
    }

    func pendingUpdatedIdentifiers(in container: String) -> Set<String> {
        lock.withLock {
            loadForcedUpdatesLocked()[container, default: []]
        }
    }

    private func trim(_ history: inout ContainerHistory) {
        while history.order.count > 4 {
            let expired = history.order.removeFirst()
            if expired != history.currentAnchor {
                history.states.removeValue(forKey: expired)
            }
        }
    }

    private func loadLocked() -> [String: ContainerHistory] {
        guard let data = defaults?.data(forKey: defaultsKey) else { return [:] }
        return (try? JSONDecoder().decode([String: ContainerHistory].self, from: data)) ?? [:]
    }

    private func saveLocked(_ states: [String: ContainerHistory]) {
        defaults?.set(try? JSONEncoder().encode(states), forKey: defaultsKey)
    }

    private func loadForcedUpdatesLocked() -> [String: Set<String>] {
        guard let data = defaults?.data(forKey: forcedUpdatesDefaultsKey) else { return [:] }
        return (try? JSONDecoder().decode([String: Set<String>].self, from: data)) ?? [:]
    }

    private func saveForcedUpdatesLocked(_ updates: [String: Set<String>]) {
        let nonEmptyUpdates = updates.filter { !$0.value.isEmpty }
        defaults?.set(try? JSONEncoder().encode(nonEmptyUpdates), forKey: forcedUpdatesDefaultsKey)
    }
}

private extension NSLock {
    func withLock<T>(_ body: () throws -> T) rethrows -> T {
        lock()
        defer { unlock() }
        return try body()
    }
}
