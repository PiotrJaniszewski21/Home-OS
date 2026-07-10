import FileProvider
import Foundation
import Combine
import AppKit
import OSLog

private let fileProviderLogger = Logger(subsystem: "uk.co.petershomenet.homeos", category: "file-provider")

private struct SendableFileProviderDomain: @unchecked Sendable {
    let domain: NSFileProviderDomain
}

@MainActor
final class FileProviderDomainService: ObservableObject {
    enum State: Equatable {
        case unavailable(String)
        case disabled
        case installing
        case enabled
        case failed(String)

        var title: String {
            switch self {
            case .unavailable:
                "Unavailable"
            case .disabled:
                "Not enabled"
            case .installing:
                "Setting up…"
            case .enabled:
                "Enabled in Finder"
            case .failed:
                "Failed"
            }
        }

        var detail: String? {
            switch self {
            case .unavailable(let message), .failed(let message):
                message
            case .disabled:
                "macOS has the Home OS Finder location turned off. Turn on HomeOS under System Settings → General → Login Items & Extensions → File Providers, then enable it again here."
            case .installing:
                "Registering the Home OS File Provider domain."
            case .enabled:
                "Home OS appears in Finder Locations. The Desktop shortcut points to this same Finder drive."
            }
        }
    }

    static let domainIdentifierValue = "home-os"
    static let domainIdentifier = NSFileProviderDomainIdentifier(domainIdentifierValue)
    private static let cloudStorageFolderName = "HomeOS-HomeOS"
    private static let desktopShortcutName = "Home OS Drive"
    private let desktopFolderAccess = DesktopFolderAccessService()

    @Published private(set) var state: State = .disabled

    var domain: NSFileProviderDomain {
        NSFileProviderDomain(identifier: Self.domainIdentifier, displayName: "Home OS")
    }

    func ensureInstalled(using appState: AppState) async {
        appState.restoreSharedSessionIfNeeded()
        guard appState.isConfigured else {
            await refreshStatus()
            return
        }

        guard state != .installing else { return }
        appState.saveSharedSettings()

        do {
            if let registeredDomain = try await registeredDomain(), registeredDomain.userEnabled {
                await verifyFinderLocationReady(
                    action: "verify existing File Provider folder",
                    appState: appState,
                    repairIfNeeded: true
                )
            } else {
                await install(using: appState)
            }
        } catch {
            state = .failed(describe(error, action: "ensure File Provider domain"))
        }
    }

    func install(using appState: AppState, requestDesktopShortcutAccess: Bool = false) async {
        appState.restoreSharedSessionIfNeeded()
        guard appState.isConfigured else {
            state = .failed("Connect to your Home OS server first.")
            return
        }

        state = .installing
        appState.saveSharedSettings()

        do {
            if let registeredDomain = try await registeredDomain() {
                guard registeredDomain.userEnabled else {
                    fileProviderLogger.info("Home OS File Provider domain is disabled; removing before reinstall")
                    try await NSFileProviderManager.remove(registeredDomain)
                    fileProviderLogger.info("Disabled Home OS File Provider domain removed")
                    try await addDomainAndVerify(
                        action: "re-add File Provider domain",
                        desktopShortcutAccess: requestDesktopShortcutAccess ? .requestIfNeeded : .savedOnly
                    )
                    return
                }

                fileProviderLogger.info("Home OS File Provider domain is already registered")
                await verifyFinderLocationReady(
                    action: "verify existing File Provider folder",
                    appState: appState,
                    repairIfNeeded: true,
                    desktopShortcutAccess: requestDesktopShortcutAccess ? .requestIfNeeded : .savedOnly
                )
                return
            }

            fileProviderLogger.info("Adding Home OS File Provider domain")
            try await addDomainAndVerify(
                action: "add File Provider domain",
                desktopShortcutAccess: requestDesktopShortcutAccess ? .requestIfNeeded : .savedOnly
            )
        } catch {
            state = .failed(describe(error, action: "add File Provider domain"))
        }
    }

    func reset(using appState: AppState, requestDesktopShortcutAccess: Bool = false) async {
        appState.restoreSharedSessionIfNeeded()
        guard appState.isConfigured else {
            state = .failed("Connect to your Home OS server first.")
            return
        }

        state = .installing
        appState.saveSharedSettings()

        do {
            fileProviderLogger.info("Resetting Home OS File Provider domain")
            try await removeRegisteredDomainIfPresent()
            try? await Task.sleep(for: .seconds(1))
            try await addDomainAndVerify(
                action: "reset File Provider domain",
                attempts: 16,
                desktopShortcutAccess: requestDesktopShortcutAccess ? .requestIfNeeded : .savedOnly
            )
            fileProviderLogger.info("Home OS File Provider domain reset complete")
        } catch {
            state = .failed("Could not reset the Home OS Finder folder: \(describe(error, action: "reset File Provider domain"))")
        }
    }

    func openInFinder(using appState: AppState) async {
        appState.restoreSharedSessionIfNeeded()
        guard appState.isConfigured else {
            state = .failed("Connect to your Home OS server first.")
            return
        }

        if state != .enabled {
            await ensureInstalled(using: appState)
        }

        do {
            guard let registeredDomain = try await registeredDomain(), registeredDomain.userEnabled else {
                state = .disabled
                openFileProviderSettings()
                return
            }

            try await openRootInFinder()
        } catch {
            await repairDomain(
                using: appState,
                reason: error,
                action: "open File Provider folder"
            )

            do {
                try await openRootInFinder()
            } catch {
                state = .failed("Could not open the Home OS Finder folder: \(describe(error, action: "open File Provider folder"))")
            }
        }
    }

    func createDesktopShortcut(using appState: AppState) async {
        appState.restoreSharedSessionIfNeeded()
        guard appState.isConfigured else {
            state = .failed("Connect to your Home OS server first.")
            return
        }

        if state != .enabled {
            await ensureInstalled(using: appState)
        }

        do {
            guard let registeredDomain = try await registeredDomain(), registeredDomain.userEnabled else {
                state = .disabled
                openFileProviderSettings()
                return
            }

            let rootURL = try await waitForCloudStorageRootURL(attempts: 16)
            ensureDesktopFinderShortcut(to: rootURL, access: .requestIfNeeded)
            state = .enabled
        } catch {
            state = .failed("Could not create the Home OS Desktop shortcut: \(describe(error, action: "create Desktop shortcut"))")
        }
    }

    func refreshStatus() async {
        do {
            if let registeredDomain = try await registeredDomain() {
                if registeredDomain.userEnabled {
                    await verifyFinderLocationReady(action: "refresh File Provider folder")
                } else {
                    state = .disabled
                }
            } else {
                state = .disabled
            }
        } catch {
            state = .failed(describe(error, action: "read File Provider domains"))
        }
    }

    func remove() async {
        state = .installing
        do {
            fileProviderLogger.info("Removing Home OS File Provider domain")
            try await NSFileProviderManager.remove(domain)
            fileProviderLogger.info("Home OS File Provider domain removed")
            state = .disabled
        } catch {
            state = .failed(describe(error, action: "remove File Provider domain"))
        }
    }

    func openFileProviderSettings() {
        let preferredSettingsURLs = [
            "x-apple.systempreferences:com.apple.LoginItems-Settings.extension",
            "x-apple.systempreferences:com.apple.ExtensionsPreferences",
        ]

        for rawURL in preferredSettingsURLs {
            guard let url = URL(string: rawURL), NSWorkspace.shared.open(url) else { continue }
            return
        }

        NSWorkspace.shared.open(URL(fileURLWithPath: "/System/Applications/System Settings.app"))
    }

    func signalRootChanged() async throws {
        guard let manager = NSFileProviderManager(for: domain) else { return }
        try await manager.signalEnumerator(for: .rootContainer)
        try await manager.signalEnumerator(for: .workingSet)
    }

    private func verifyFinderLocationReady(
        action: String,
        appState: AppState? = nil,
        repairIfNeeded: Bool = false,
        desktopShortcutAccess: DesktopShortcutAccess = .savedOnly
    ) async {
        do {
            let rootURL = try await waitForCloudStorageRootURL(attempts: repairIfNeeded ? 24 : 12)
            state = .enabled
            ensureDesktopFinderShortcut(to: rootURL, access: desktopShortcutAccess)
            try? await signalRootChanged()
        } catch {
            if repairIfNeeded, let appState {
                await repairDomain(using: appState, reason: error, action: action)
            } else {
                state = .failed(describe(error, action: action))
            }
        }
    }

    private func repairDomain(using appState: AppState, reason: Error, action: String) async {
        let nsError = reason as NSError
        fileProviderLogger.warning("Repairing Home OS File Provider domain after \(action, privacy: .public) failed: \(nsError.domain, privacy: .public) \(nsError.code)")

        state = .installing
        appState.restoreSharedSessionIfNeeded()
        appState.saveSharedSettings()

        do {
            try await removeRegisteredDomainIfPresent()
            try? await Task.sleep(for: .milliseconds(750))
            try await addDomainAndVerify(action: "repair File Provider domain", attempts: 24)
        } catch {
            state = .failed("Could not repair the Home OS Finder folder: \(describe(error, action: "repair File Provider domain"))")
        }
    }

    private func addDomainAndVerify(
        action: String,
        attempts: Int = 24,
        desktopShortcutAccess: DesktopShortcutAccess = .savedOnly
    ) async throws {
        try await NSFileProviderManager.add(domain)
        fileProviderLogger.info("Home OS File Provider domain added for \(action, privacy: .public)")
        try? await Task.sleep(for: .milliseconds(750))

        guard let registeredDomain = try await registeredDomain() else {
            state = .disabled
            throw FileProviderDomainServiceError.missingRegisteredDomain
        }

        guard registeredDomain.userEnabled else {
            fileProviderLogger.error("Home OS File Provider domain remains user-disabled after \(action, privacy: .public)")
            state = .disabled
            throw FileProviderDomainServiceError.domainUserDisabled
        }

        let rootURL = try await waitForCloudStorageRootURL(attempts: attempts)
        state = .enabled
        ensureDesktopFinderShortcut(to: rootURL, access: desktopShortcutAccess)
        try? await signalRootChanged()
    }

    private func removeRegisteredDomainIfPresent() async throws {
        do {
            if let registeredDomain = try await registeredDomain() {
                try await NSFileProviderManager.remove(registeredDomain)
                fileProviderLogger.info("Existing Home OS File Provider domain removed for repair")
                return
            }
        } catch {
            fileProviderLogger.warning("Could not list registered File Provider domains before repair; trying direct removal")
        }

        do {
            try await NSFileProviderManager.remove(domain)
            fileProviderLogger.info("Home OS File Provider domain removed directly for repair")
        } catch {
            let nsError = error as NSError
            if nsError.domain == NSFileProviderErrorDomain,
               nsError.code == NSFileProviderError.noSuchItem.rawValue {
                return
            }

            fileProviderLogger.warning("Direct Home OS File Provider domain removal failed during repair: \(nsError.domain, privacy: .public) \(nsError.code)")
            throw error
        }
    }

    private func waitForCloudStorageRootURL(attempts: Int = 20) async throws -> URL {
        for attempt in 1...attempts {
            if let url = knownCloudStorageRootURL() {
                return url
            }

            if attempt < attempts {
                try? await Task.sleep(for: .milliseconds(750))
            }
        }

        throw FileProviderDomainServiceError.missingCloudStorageRootURL
    }

    private func openRootInFinder() async throws {
        let url = try await waitForCloudStorageRootURL(attempts: 16)
        if NSWorkspace.shared.open(url) {
            return
        }

        if NSWorkspace.shared.selectFile(url.path, inFileViewerRootedAtPath: url.deletingLastPathComponent().path) {
            return
        }

        guard NSWorkspace.shared.open(url.deletingLastPathComponent()) else {
            throw FileProviderDomainServiceError.cannotOpenURL(url)
        }
    }

    private func knownCloudStorageRootURL() -> URL? {
        let url = FileManager.default.homeDirectoryForCurrentUser
            .appendingPathComponent("Library", isDirectory: true)
            .appendingPathComponent("CloudStorage", isDirectory: true)
            .appendingPathComponent(Self.cloudStorageFolderName, isDirectory: true)
        return FileManager.default.fileExists(atPath: url.path) ? url : nil
    }

    private func ensureDesktopFinderShortcut(to cloudStorageURL: URL, access: DesktopShortcutAccess) {
        let authorization: DesktopFolderAuthorization
        do {
            if let savedAuthorization = try desktopFolderAccess.resolvedAuthorization() {
                authorization = savedAuthorization
            } else if access == .requestIfNeeded {
                authorization = try desktopFolderAccess.requestAccess()
            } else {
                fileProviderLogger.info("Skipping Home OS Finder desktop shortcut until Desktop access is granted")
                return
            }
        } catch DesktopFolderAccessError.cancelled {
            fileProviderLogger.info("Home OS Finder desktop shortcut skipped because Desktop access was cancelled")
            return
        } catch {
            let nsError = error as NSError
            fileProviderLogger.warning("Could not resolve Desktop access for Home OS Finder shortcut: \(nsError.domain, privacy: .public) \(nsError.code)")
            return
        }

        let desktopURL = authorization.baseURL
        let shortcutURL = desktopURL.appendingPathComponent(Self.desktopShortcutName)

        do {
            if FileManager.default.fileExists(atPath: shortcutURL.path) {
                let destination = try? FileManager.default.destinationOfSymbolicLink(atPath: shortcutURL.path)
                if destination == cloudStorageURL.path { return }
                if destination != nil {
                    try FileManager.default.removeItem(at: shortcutURL)
                } else {
                    fileProviderLogger.info("Home OS Finder desktop shortcut path already exists and is not a symlink")
                    return
                }
            }

            try withExtendedLifetime(authorization.securityScope) {
                try FileManager.default.createSymbolicLink(at: shortcutURL, withDestinationURL: cloudStorageURL)
            }
            fileProviderLogger.info("Created Home OS Finder desktop shortcut")
        } catch {
            let nsError = error as NSError
            fileProviderLogger.warning("Could not create Home OS Finder desktop shortcut: \(nsError.domain, privacy: .public) \(nsError.code)")
        }
    }

    private enum DesktopShortcutAccess {
        case savedOnly
        case requestIfNeeded
    }

    private func registeredDomain() async throws -> NSFileProviderDomain? {
        let domains = try await registeredDomains()
        return domains.first { $0.identifier.rawValue == Self.domainIdentifierValue }
    }

    private func registeredDomains() async throws -> [NSFileProviderDomain] {
        let domains: [SendableFileProviderDomain] = try await withCheckedThrowingContinuation { continuation in
            NSFileProviderManager.getDomainsWithCompletionHandler { domains, error in
                if let error {
                    continuation.resume(throwing: error)
                } else {
                    continuation.resume(returning: domains.map { SendableFileProviderDomain(domain: $0) })
                }
            }
        }
        return domains.map(\.domain)
    }

    private func describe(_ error: Error, action: String) -> String {
        let nsError = error as NSError
        fileProviderLogger.error("\(action, privacy: .public) failed: \(nsError.domain, privacy: .public) \(nsError.code) \(nsError.localizedDescription, privacy: .public)")
        return "\(nsError.localizedDescription) (\(nsError.domain) \(nsError.code))"
    }
}

private enum FileProviderDomainServiceError: LocalizedError {
    case missingCloudStorageRootURL
    case missingRegisteredDomain
    case domainUserDisabled
    case cannotOpenURL(URL)

    var errorDescription: String? {
        switch self {
        case .missingCloudStorageRootURL:
            "Finder has not created the Home OS CloudStorage folder yet."
        case .missingRegisteredDomain:
            "macOS did not register the Home OS File Provider domain."
        case .domainUserDisabled:
            "macOS has the Home OS File Provider domain turned off."
        case .cannotOpenURL(let url):
            "Finder could not open \(url.path)."
        }
    }
}
