import FileProvider
import Foundation
import UniformTypeIdentifiers

final class HomeOSFileProviderItem: NSObject, NSFileProviderItem {
    let itemIdentifier: NSFileProviderItemIdentifier
    let parentItemIdentifier: NSFileProviderItemIdentifier
    let filename: String
    let documentSize: NSNumber?
    let childItemCount: NSNumber?
    let creationDate: Date?
    let contentModificationDate: Date?
    let itemVersion: NSFileProviderItemVersion
    let capabilities: NSFileProviderItemCapabilities
    let contentType: UTType
    let contentPolicy: NSFileProviderContentPolicy
    let isUploaded: Bool
    let isUploading: Bool
    let uploadingError: Error?
    let userInfo: [AnyHashable: Any]?

    init(
        itemIdentifier: NSFileProviderItemIdentifier,
        parentItemIdentifier: NSFileProviderItemIdentifier,
        filename: String,
        contentType: UTType,
        documentSize: Int64?,
        modifiedDate: Date?,
        isDirectory: Bool,
        childItemCount: NSNumber? = nil,
        capabilities: NSFileProviderItemCapabilities? = nil,
        isKeptDownloaded: Bool = false,
        isUploaded: Bool = true,
        isUploading: Bool = false,
        uploadingError: Error? = nil
    ) {
        self.itemIdentifier = itemIdentifier
        self.parentItemIdentifier = parentItemIdentifier
        self.filename = filename
        self.contentType = contentType
        self.documentSize = documentSize.map(NSNumber.init(value:))
        self.childItemCount = isDirectory ? childItemCount : nil
        self.creationDate = nil
        self.contentModificationDate = modifiedDate
        self.itemVersion = NSFileProviderItemVersion(
            contentVersion: Data("\(itemIdentifier.rawValue)|\(documentSize ?? 0)|\(modifiedDate?.timeIntervalSince1970 ?? 0)".utf8),
            metadataVersion: Data("\(filename)|\(contentType.identifier)|kept:\(isKeptDownloaded)".utf8)
        )
        self.contentPolicy = isKeptDownloaded
            ? .downloadEagerlyAndKeepDownloaded
            : .downloadLazily
        self.isUploaded = isUploaded
        self.isUploading = isUploading
        self.uploadingError = uploadingError
        var resolvedCapabilities = capabilities ?? (isDirectory
            ? [.allowsReading, .allowsContentEnumerating, .allowsAddingSubItems, .allowsRenaming, .allowsReparenting, .allowsDeleting, .allowsTrashing, .allowsEvicting]
            : [.allowsReading, .allowsWriting, .allowsRenaming, .allowsReparenting, .allowsDeleting, .allowsTrashing, .allowsEvicting])
        if isKeptDownloaded {
            resolvedCapabilities.remove(.allowsEvicting)
        }
        self.capabilities = resolvedCapabilities
        self.userInfo = [Self.keptOnDiskUserInfoKey: isKeptDownloaded]
        super.init()
    }

    static let keptOnDiskUserInfoKey = "homeosKeptOnDisk"

    static func root() -> HomeOSFileProviderItem {
        HomeOSFileProviderItem(
            itemIdentifier: .rootContainer,
            parentItemIdentifier: .rootContainer,
            filename: "Home OS",
            contentType: .folder,
            documentSize: nil,
            modifiedDate: nil,
            isDirectory: true,
            childItemCount: nil,
            capabilities: [.allowsReading, .allowsContentEnumerating, .allowsAddingSubItems]
        )
    }

    static func from(
        entry: FileEntry,
        keepDownloadedStore: HomeOSFileProviderKeepDownloadedStore = .shared
    ) -> HomeOSFileProviderItem {
        let remotePath = HomeOSFileProviderPath.normalize(entry.path)
        let identifier = HomeOSFileProviderPath.identifier(for: remotePath)
        return HomeOSFileProviderItem(
            itemIdentifier: identifier,
            parentItemIdentifier: HomeOSFileProviderPath.parentIdentifier(for: remotePath),
            filename: entry.name,
            contentType: entry.isDirectory ? .folder : HomeOSFileProviderPath.contentType(for: entry.name),
            documentSize: entry.isDirectory ? nil : entry.size,
            modifiedDate: HomeOSFileProviderPath.date(from: entry.modified),
            isDirectory: entry.isDirectory,
            isKeptDownloaded: keepDownloadedStore.isKept(identifier)
        )
    }
}
