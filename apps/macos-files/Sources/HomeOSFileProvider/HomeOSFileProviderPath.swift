import FileProvider
import Foundation
import UniformTypeIdentifiers

enum HomeOSFileProviderPath {
    static func normalize(_ path: String) -> String {
        let trimmed = path.trimmingCharacters(in: .whitespacesAndNewlines)
        let prefixed = trimmed.hasPrefix("/") ? trimmed : "/\(trimmed)"
        let components = prefixed
            .split(separator: "/")
            .map(String.init)
            .filter { !$0.isEmpty && $0 != "." && $0 != ".." }
        return components.isEmpty ? "/" : "/\(components.joined(separator: "/"))"
    }

    static func identifier(
        for path: String,
        identityStore: HomeOSFileProviderIdentityStore = .shared
    ) -> NSFileProviderItemIdentifier {
        let normalized = normalize(path)
        return identityStore.identifier(forNormalizedPath: normalized)
    }

    static func remotePath(
        for identifier: NSFileProviderItemIdentifier,
        identityStore: HomeOSFileProviderIdentityStore = .shared
    ) throws -> String {
        if identifier == .rootContainer || identifier == .workingSet {
            return "/"
        }
        guard let path = identityStore.path(for: identifier) else {
            throw NSFileProviderError(.noSuchItem)
        }
        return normalize(path)
    }

    static func parentPath(for path: String) -> String {
        let normalized = normalize(path)
        guard normalized != "/" else { return "/" }
        let components = normalized.split(separator: "/").dropLast().map(String.init)
        return components.isEmpty ? "/" : "/\(components.joined(separator: "/"))"
    }

    static func parentIdentifier(
        for path: String,
        identityStore: HomeOSFileProviderIdentityStore = .shared
    ) -> NSFileProviderItemIdentifier {
        identifier(for: parentPath(for: path), identityStore: identityStore)
    }

    static func filename(for path: String) -> String {
        let normalized = normalize(path)
        guard normalized != "/" else { return "Home OS" }
        return normalized.split(separator: "/").last.map(String.init) ?? "Home OS"
    }

    static func join(_ parentPath: String, _ filename: String) -> String {
        let safeName = filename
            .split(separator: "/")
            .map(String.init)
            .filter { !$0.isEmpty && $0 != "." && $0 != ".." }
            .joined(separator: "-")
        let parent = normalize(parentPath)
        guard !safeName.isEmpty else { return parent }
        return parent == "/" ? "/\(safeName)" : "\(parent)/\(safeName)"
    }

    static func contentType(for filename: String) -> UTType {
        guard let ext = filename.split(separator: ".").last.map(String.init), ext != filename else {
            return .data
        }
        return UTType(filenameExtension: ext) ?? .data
    }

    static func date(from value: String?) -> Date? {
        guard let value, !value.isEmpty else { return nil }
        if let date = ISO8601DateFormatter().date(from: value) {
            return date
        }
        let formatter = DateFormatter()
        formatter.locale = Locale(identifier: "en_US_POSIX")
        formatter.dateFormat = "yyyy-MM-dd HH:mm:ss"
        return formatter.date(from: value)
    }
}
