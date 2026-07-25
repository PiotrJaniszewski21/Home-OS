import FileProvider
import Foundation

final class HomeOSFileProviderKeepDownloadedStore {
    static let shared = HomeOSFileProviderKeepDownloadedStore()

    private let defaults: UserDefaults?
    private let defaultsKey = "HomeOSFileProviderKeptDownloadedItemIdentifiers"
    private let queue = DispatchQueue(label: "uk.co.petershomenet.homeos.fileprovider.keep-downloaded")

    init(defaults: UserDefaults? = .standard) {
        self.defaults = defaults
    }

    func isKept(_ identifier: NSFileProviderItemIdentifier) -> Bool {
        guard isKeepable(identifier) else { return false }
        return queue.sync {
            loadIdentifiersLocked().contains(identifier.rawValue)
        }
    }

    func setKept(_ isKept: Bool, for identifier: NSFileProviderItemIdentifier) {
        guard isKeepable(identifier) else { return }
        queue.sync {
            var identifiers = loadIdentifiersLocked()
            if isKept {
                identifiers.insert(identifier.rawValue)
            } else {
                identifiers.remove(identifier.rawValue)
            }
            storeIdentifiersLocked(identifiers)
        }
    }

    func remove(_ identifier: NSFileProviderItemIdentifier) {
        setKept(false, for: identifier)
    }

    func moveKeptState(from oldIdentifier: NSFileProviderItemIdentifier, to newIdentifier: NSFileProviderItemIdentifier) {
        guard oldIdentifier != newIdentifier, isKeepable(oldIdentifier), isKeepable(newIdentifier) else { return }
        queue.sync {
            var identifiers = loadIdentifiersLocked()
            guard identifiers.remove(oldIdentifier.rawValue) != nil else { return }
            identifiers.insert(newIdentifier.rawValue)
            storeIdentifiersLocked(identifiers)
        }
    }

    private func isKeepable(_ identifier: NSFileProviderItemIdentifier) -> Bool {
        identifier != .rootContainer
            && identifier != .workingSet
            && identifier != .trashContainer
            && identifier.rawValue.hasPrefix("/")
    }

    private func loadIdentifiersLocked() -> Set<String> {
        Set(defaults?.stringArray(forKey: defaultsKey) ?? [])
    }

    private func storeIdentifiersLocked(_ identifiers: Set<String>) {
        defaults?.set(Array(identifiers).sorted(), forKey: defaultsKey)
        defaults?.synchronize()
    }
}
