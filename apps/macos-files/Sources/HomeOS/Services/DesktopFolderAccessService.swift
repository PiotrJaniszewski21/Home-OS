import AppKit
import Foundation

enum DesktopFolderAccessError: LocalizedError {
    case permissionRequired
    case cancelled
    case invalidSelection

    var errorDescription: String? {
        switch self {
        case .permissionRequired:
            "Desktop access is not granted yet. Choose your Desktop folder so Home OS can create the Home OS Drive shortcut."
        case .cancelled:
            "Desktop access was cancelled."
        case .invalidSelection:
            "Choose a folder, such as your Desktop folder."
        }
    }
}

final class DesktopFolderAccessService {
    private static let bookmarkKey = "desktopFolderBaseBookmark"
    private let defaults: UserDefaults

    init(defaults: UserDefaults = .standard) {
        self.defaults = defaults
    }

    static func defaultDesktopURL() -> URL {
        FileManager.default.urls(for: .desktopDirectory, in: .userDomainMask).first
            ?? FileManager.default.homeDirectoryForCurrentUser.appendingPathComponent("Desktop", isDirectory: true)
    }

    @MainActor
    func requestAccess() throws -> DesktopFolderAuthorization {
        let panel = NSOpenPanel()
        panel.title = "Allow Home OS Desktop Shortcut"
        panel.message = "Choose your Desktop folder. Home OS will add a “Home OS Drive” shortcut to the Finder drive."
        panel.prompt = "Allow Desktop Shortcut"
        panel.directoryURL = Self.defaultDesktopURL()
        panel.canChooseFiles = false
        panel.canChooseDirectories = true
        panel.allowsMultipleSelection = false
        panel.canCreateDirectories = true
        panel.resolvesAliases = true

        guard panel.runModal() == .OK else {
            throw DesktopFolderAccessError.cancelled
        }
        guard let selectedURL = panel.url else {
            throw DesktopFolderAccessError.invalidSelection
        }

        let values = try selectedURL.resourceValues(forKeys: [.isDirectoryKey])
        guard values.isDirectory == true else {
            throw DesktopFolderAccessError.invalidSelection
        }

        let bookmark = try selectedURL.bookmarkData(
            options: [.withSecurityScope],
            includingResourceValuesForKeys: nil,
            relativeTo: nil
        )
        defaults.set(bookmark, forKey: Self.bookmarkKey)
        guard let authorization = try resolvedAuthorization() else {
            throw DesktopFolderAccessError.permissionRequired
        }
        return authorization
    }

    func resolvedAuthorization() throws -> DesktopFolderAuthorization? {
        guard let bookmark = defaults.data(forKey: Self.bookmarkKey) else {
            return nil
        }

        var isStale = false
        let baseURL = try URL(
            resolvingBookmarkData: bookmark,
            options: [.withSecurityScope],
            relativeTo: nil,
            bookmarkDataIsStale: &isStale
        )

        if isStale {
            let refreshedBookmark = try baseURL.bookmarkData(
                options: [.withSecurityScope],
                includingResourceValuesForKeys: nil,
                relativeTo: nil
            )
            defaults.set(refreshedBookmark, forKey: Self.bookmarkKey)
        }

        return DesktopFolderAuthorization(baseURL: baseURL)
    }
}

struct DesktopFolderAuthorization {
    let baseURL: URL
    let securityScope: DesktopFolderSecurityScope

    init(baseURL: URL) {
        self.baseURL = baseURL
        self.securityScope = DesktopFolderSecurityScope(url: baseURL)
    }
}

final class DesktopFolderSecurityScope {
    private let url: URL
    private let didStartAccessing: Bool

    init(url: URL) {
        self.url = url
        self.didStartAccessing = url.startAccessingSecurityScopedResource()
    }

    deinit {
        if didStartAccessing {
            url.stopAccessingSecurityScopedResource()
        }
    }
}
