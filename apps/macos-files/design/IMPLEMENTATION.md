# Home OS Mac App — Implementation Guide

This document contains the complete implementation instructions for Claude on the Mac. It includes every file that needs to be written, the exact Xcode project setup steps, and the full source code for the File Provider extension.

## Step 1: Create the Xcode Project

1. Open Xcode → File → New → Project
2. Choose **macOS → App**
3. Product Name: `HomeOS`
4. Team: Personal Team (your Apple ID)
5. Organization Identifier: `com.homeos`
6. Interface: SwiftUI
7. Language: Swift
8. Uncheck "Include Tests"
9. Save to a working directory

## Step 2: Add the File Provider Extension Target

1. File → New → Target
2. Choose **macOS → File Provider Extension**
3. Product Name: `HomeOSFileProvider`
4. When asked to activate the scheme, click "Activate"

## Step 3: Create an App Group

1. Select the `HomeOS` target → Signing & Capabilities → + Capability → App Groups
2. Add group: `group.com.homeos.app`
3. Select the `HomeOSFileProvider` target → Signing & Capabilities → + Capability → App Groups
4. Add the same group: `group.com.homeos.app`

## Step 4: Ensure Network Entitlement

Both targets need "Outgoing Connections (Client)" under App Sandbox:
1. Select each target → Signing & Capabilities → App Sandbox
2. Check "Outgoing Connections (Client)"

## Step 5: Create Shared Framework (Optional but Recommended)

To share code between the app and extension, add files to BOTH targets:
1. Select each shared file → File Inspector → Target Membership → check both `HomeOS` and `HomeOSFileProvider`

Alternatively, create a "Shared" group in the project navigator and add files to both targets.

## Step 6: Configure the Extension Info.plist

The File Provider extension's Info.plist should already have the NSExtension key from the template. Verify it contains:
```xml
<key>NSExtension</key>
<dict>
    <key>NSExtensionPointIdentifier</key>
    <string>com.apple.fileprovider-nonui</string>
    <key>NSExtensionPrincipalClass</key>
    <string>$(PRODUCT_MODULE_NAME).FileProviderExtension</string>
</dict>
```

## Step 7: Set Deployment Target

Both targets: set minimum deployment to **macOS 14.0**.

---

## Source Files

Below is every source file needed. Copy these into the Xcode project.

---

### Shared/SharedConfig.swift
**Add to both targets.**

```swift
import Foundation

/// Shared configuration between the main app and File Provider extension.
/// Uses App Group UserDefaults so both processes can read/write.
struct SharedConfig {
    private static let suiteName = "group.com.homeos.app"
    
    private static var defaults: UserDefaults {
        UserDefaults(suiteName: suiteName)!
    }
    
    static var serverURL: String {
        get { defaults.string(forKey: "serverURL") ?? "" }
        set { defaults.set(newValue, forKey: "serverURL") }
    }
    
    static var authToken: String {
        get { defaults.string(forKey: "authToken") ?? "" }
        set { defaults.set(newValue, forKey: "authToken") }
    }
    
    static var username: String {
        get { defaults.string(forKey: "username") ?? "" }
        set { defaults.set(newValue, forKey: "username") }
    }
    
    static var isConfigured: Bool {
        !serverURL.isEmpty && !authToken.isEmpty
    }
}
```

---

### Shared/HomeOSAPIClient.swift
**Add to both targets.** This is a standalone API client (no SwiftUI dependencies) that both the app and extension use.

```swift
import Foundation

/// API client for Home OS server. Used by both the main app and the File Provider extension.
final class HomeOSAPIClient: @unchecked Sendable {
    let baseURL: String
    let token: String
    private let session: URLSession
    
    init(baseURL: String, token: String) {
        self.baseURL = baseURL.trimmingCharacters(in: CharacterSet(charactersIn: "/"))
        self.token = token
        
        let config = URLSessionConfiguration.default
        config.timeoutIntervalForRequest = 30
        config.timeoutIntervalForResource = 300
        // Trust self-signed certs for local server
        self.session = URLSession(configuration: config, delegate: TrustAllDelegate(), delegateQueue: nil)
    }
    
    // MARK: - File Operations
    
    /// List directory contents at the given path
    func listDirectory(path: String) async throws -> [FileItem] {
        let cleanPath = path == "/" ? "" : path
        let url = URL(string: "\(baseURL)/files\(cleanPath)")!
        var request = URLRequest(url: url)
        request.setValue("application/json", forHTTPHeaderField: "Accept")
        authorize(&request)
        
        let (data, response) = try await session.data(for: request)
        try checkResponse(response)
        
        let decoded = try JSONDecoder().decode(FileListAPIResponse.self, from: data)
        guard decoded.ok, let listData = decoded.data else {
            throw HomeOSError.serverError("Failed to list directory")
        }
        return listData.entries
    }
    
    /// Download file contents
    func downloadFile(path: String) async throws -> URL {
        let url = URL(string: "\(baseURL)/files\(path)?download")!
        var request = URLRequest(url: url)
        authorize(&request)
        
        let (tempURL, response) = try await session.download(for: request)
        try checkResponse(response)
        
        // Move to a stable temporary location (the download temp file may be deleted)
        let dest = FileManager.default.temporaryDirectory
            .appendingPathComponent(UUID().uuidString)
            .appendingPathExtension(URL(string: path)?.pathExtension ?? "")
        try FileManager.default.moveItem(at: tempURL, to: dest)
        return dest
    }
    
    /// Upload a file to the server
    func uploadFile(localURL: URL, toDirectory remotePath: String, fileName: String) async throws {
        let url = URL(string: "\(baseURL)/api/files/upload")!
        var request = URLRequest(url: url)
        request.httpMethod = "POST"
        authorize(&request)
        
        let boundary = UUID().uuidString
        request.setValue("multipart/form-data; boundary=\(boundary)", forHTTPHeaderField: "Content-Type")
        
        var body = Data()
        // Path field
        body.append("--\(boundary)\r\n".data(using: .utf8)!)
        body.append("Content-Disposition: form-data; name=\"path\"\r\n\r\n".data(using: .utf8)!)
        body.append("\(remotePath)\r\n".data(using: .utf8)!)
        // File field
        body.append("--\(boundary)\r\n".data(using: .utf8)!)
        body.append("Content-Disposition: form-data; name=\"file\"; filename=\"\(fileName)\"\r\n".data(using: .utf8)!)
        body.append("Content-Type: application/octet-stream\r\n\r\n".data(using: .utf8)!)
        body.append(try Data(contentsOf: localURL))
        body.append("\r\n--\(boundary)--\r\n".data(using: .utf8)!)
        
        request.httpBody = body
        
        let (_, response) = try await session.data(for: request)
        try checkResponse(response)
    }
    
    /// Create a directory
    func createDirectory(path: String) async throws {
        let url = URL(string: "\(baseURL)/api/files/mkdir")!
        var request = URLRequest(url: url)
        request.httpMethod = "POST"
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        authorize(&request)
        
        let body = ["path": path]
        request.httpBody = try JSONSerialization.data(withJSONObject: body)
        
        let (_, response) = try await session.data(for: request)
        try checkResponse(response)
    }
    
    /// Rename a file or directory
    func rename(path: String, newName: String) async throws {
        let url = URL(string: "\(baseURL)/api/files/rename")!
        var request = URLRequest(url: url)
        request.httpMethod = "POST"
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        authorize(&request)
        
        let body: [String: String] = ["path": path, "new_name": newName]
        request.httpBody = try JSONSerialization.data(withJSONObject: body)
        
        let (_, response) = try await session.data(for: request)
        try checkResponse(response)
    }
    
    /// Move a file or directory
    func move(src: String, dest: String) async throws {
        let url = URL(string: "\(baseURL)/api/files/move")!
        var request = URLRequest(url: url)
        request.httpMethod = "POST"
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        authorize(&request)
        
        let body: [String: String] = ["src": src, "dest": dest]
        request.httpBody = try JSONSerialization.data(withJSONObject: body)
        
        let (_, response) = try await session.data(for: request)
        try checkResponse(response)
    }
    
    /// Delete a file or directory (moves to trash on server)
    func delete(path: String) async throws {
        let url = URL(string: "\(baseURL)/api/files/delete")!
        var request = URLRequest(url: url)
        request.httpMethod = "POST"
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        authorize(&request)
        
        let body = ["path": path]
        request.httpBody = try JSONSerialization.data(withJSONObject: body)
        
        let (_, response) = try await session.data(for: request)
        try checkResponse(response)
    }
    
    /// Login and return a Bearer token
    func login(username: String, password: String) async throws -> String {
        let url = URL(string: "\(baseURL)/api/login")!
        var request = URLRequest(url: url)
        request.httpMethod = "POST"
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        
        let body: [String: String] = ["username": username, "password": password]
        request.httpBody = try JSONSerialization.data(withJSONObject: body)
        
        let (data, response) = try await session.data(for: request)
        try checkResponse(response)
        
        let decoded = try JSONDecoder().decode(LoginAPIResponse.self, from: data)
        guard decoded.ok, let loginData = decoded.data else {
            throw HomeOSError.unauthorized
        }
        return loginData.token
    }
    
    // MARK: - Helpers
    
    private func authorize(_ request: inout URLRequest) {
        if !token.isEmpty {
            request.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
        }
    }
    
    private func checkResponse(_ response: URLResponse) throws {
        guard let http = response as? HTTPURLResponse else {
            throw HomeOSError.serverError("Invalid response")
        }
        switch http.statusCode {
        case 200...299: return
        case 401, 403: throw HomeOSError.unauthorized
        case 404: throw HomeOSError.notFound
        default: throw HomeOSError.serverError("HTTP \(http.statusCode)")
        }
    }
}

// MARK: - Models

struct FileItem: Decodable {
    let name: String
    let path: String
    let is_dir: Bool
    let size: Int?
    let modified: String?
    
    var modifiedDate: Date? {
        guard let modified else { return nil }
        let formatter = ISO8601DateFormatter()
        formatter.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        if let date = formatter.date(from: modified) { return date }
        // Try without fractional seconds
        formatter.formatOptions = [.withInternetDateTime]
        if let date = formatter.date(from: modified) { return date }
        // Try basic datetime format
        let df = DateFormatter()
        df.dateFormat = "yyyy-MM-dd'T'HH:mm:ss"
        return df.date(from: modified)
    }
}

struct FileListAPIResponse: Decodable {
    let ok: Bool
    let data: FileListAPIData?
}

struct FileListAPIData: Decodable {
    let path: String
    let entries: [FileItem]
}

struct LoginAPIResponse: Decodable {
    let ok: Bool
    let data: LoginAPIData?
}

struct LoginAPIData: Decodable {
    let token: String
}

enum HomeOSError: Error {
    case unauthorized
    case notFound
    case serverError(String)
    case offline
}

// MARK: - SSL Trust (for self-signed certs)

/// Allows connecting to the Home OS server with a self-signed TLS certificate.
/// This is necessary because the server uses a self-signed cert on port 4443.
final class TrustAllDelegate: NSObject, URLSessionDelegate {
    func urlSession(
        _ session: URLSession,
        didReceive challenge: URLAuthenticationChallenge,
        completionHandler: @escaping (URLSession.AuthChallengeDisposition, URLCredential?) -> Void
    ) {
        if challenge.protectionSpace.authenticationMethod == NSURLAuthenticationMethodServerTrust,
           let trust = challenge.protectionSpace.serverTrust {
            completionHandler(.useCredential, URLCredential(trust: trust))
        } else {
            completionHandler(.performDefaultHandling, nil)
        }
    }
}
```

---

### Shared/DomainManager.swift
**Add to both targets.**

```swift
import FileProvider
import Foundation

/// Manages the File Provider domain (the "Home OS" volume in Finder).
enum DomainManager {
    static let domainIdentifier = NSFileProviderDomainIdentifier(rawValue: "homeos-storage")
    static let domainDisplayName = "Home OS"
    
    /// Register the domain so it appears in Finder
    static func addDomain() async throws {
        let domain = NSFileProviderDomain(identifier: domainIdentifier, displayName: domainDisplayName)
        try await NSFileProviderManager.add(domain)
    }
    
    /// Remove the domain from Finder
    static func removeDomain() async throws {
        let domain = NSFileProviderDomain(identifier: domainIdentifier, displayName: domainDisplayName)
        try await NSFileProviderManager.remove(domain)
    }
    
    /// Signal Finder to re-enumerate a path (refresh contents)
    static func signalEnumerator(for itemIdentifier: NSFileProviderItemIdentifier = .rootContainer) {
        let domain = NSFileProviderDomain(identifier: domainIdentifier, displayName: domainDisplayName)
        guard let manager = NSFileProviderManager(for: domain) else { return }
        manager.signalEnumerator(for: itemIdentifier) { _ in }
    }
}
```

---

### HomeOSFileProvider/FileProviderExtension.swift
**Extension target only.**

```swift
import FileProvider
import UniformTypeIdentifiers

/// The main File Provider extension class. macOS instantiates this to handle
/// all file operations for the "Home OS" domain.
final class FileProviderExtension: NSObject, NSFileProviderReplicatedExtension {
    let domain: NSFileProviderDomain
    
    required init(domain: NSFileProviderDomain) {
        self.domain = domain
        super.init()
    }
    
    // MARK: - API Client
    
    private var _client: HomeOSAPIClient?
    
    private var client: HomeOSAPIClient {
        if let existing = _client { return existing }
        let c = HomeOSAPIClient(baseURL: SharedConfig.serverURL, token: SharedConfig.authToken)
        _client = c
        return c
    }
    
    // MARK: - Item Lookup
    
    func item(for identifier: NSFileProviderItemIdentifier, request: NSFileProviderRequest,
              completionHandler: @escaping (NSFileProviderItem?, Error?) -> Void) -> Progress {
        
        // Root container is always available
        if identifier == .rootContainer {
            completionHandler(FileProviderItem.rootItem(), nil)
            return Progress()
        }
        
        let path = serverPath(from: identifier)
        
        Task {
            do {
                let parentPath = (path as NSString).deletingLastPathComponent
                let items = try await client.listDirectory(path: parentPath.isEmpty ? "/" : parentPath)
                if let match = items.first(where: { $0.path == path }) {
                    completionHandler(FileProviderItem(fileItem: match), nil)
                } else {
                    completionHandler(nil, NSFileProviderError(.noSuchItem))
                }
            } catch HomeOSError.unauthorized {
                completionHandler(nil, NSFileProviderError(.notAuthenticated))
            } catch HomeOSError.notFound {
                completionHandler(nil, NSFileProviderError(.noSuchItem))
            } catch {
                completionHandler(nil, NSFileProviderError(.serverUnreachable))
            }
        }
        
        return Progress()
    }
    
    // MARK: - Fetch Contents (Download)
    
    func fetchContents(for itemIdentifier: NSFileProviderItemIdentifier,
                       version requestedVersion: NSFileProviderItemVersion?,
                       request: NSFileProviderRequest,
                       completionHandler: @escaping (URL?, NSFileProviderItem?, Error?) -> Void) -> Progress {
        
        let path = serverPath(from: itemIdentifier)
        let progress = Progress(totalUnitCount: 100)
        
        Task {
            do {
                let localURL = try await client.downloadFile(path: path)
                // Fetch the item metadata
                let parentPath = (path as NSString).deletingLastPathComponent
                let items = try await client.listDirectory(path: parentPath.isEmpty ? "/" : parentPath)
                let item = items.first(where: { $0.path == path })
                    .map { FileProviderItem(fileItem: $0) } ?? FileProviderItem.placeholder(path: path)
                progress.completedUnitCount = 100
                completionHandler(localURL, item, nil)
            } catch HomeOSError.unauthorized {
                completionHandler(nil, nil, NSFileProviderError(.notAuthenticated))
            } catch HomeOSError.notFound {
                completionHandler(nil, nil, NSFileProviderError(.noSuchItem))
            } catch {
                completionHandler(nil, nil, NSFileProviderError(.serverUnreachable))
            }
        }
        
        return progress
    }
    
    // MARK: - Create Item (Upload new file or create directory)
    
    func createItem(basedOn itemTemplate: NSFileProviderItem, fields: NSFileProviderItemFields,
                    contents url: URL?, options: NSFileProviderCreateItemOptions = [],
                    request: NSFileProviderRequest,
                    completionHandler: @escaping (NSFileProviderItem?, NSFileProviderItemFields, Bool, Error?) -> Void) -> Progress {
        
        let parentPath = serverPath(from: itemTemplate.parentItemIdentifier)
        let itemName = itemTemplate.filename
        let newPath = parentPath == "/" ? "/\(itemName)" : "\(parentPath)/\(itemName)"
        
        Task {
            do {
                if itemTemplate.contentType == .folder || itemTemplate.contentType == .directory {
                    try await client.createDirectory(path: newPath)
                } else if let url {
                    try await client.uploadFile(localURL: url, toDirectory: parentPath, fileName: itemName)
                }
                
                let item = FileProviderItem(
                    path: newPath,
                    name: itemName,
                    isDir: itemTemplate.contentType == .folder || itemTemplate.contentType == .directory,
                    size: url.flatMap { try? FileManager.default.attributesOfItem(atPath: $0.path)[.size] as? Int },
                    modified: Date()
                )
                completionHandler(item, [], false, nil)
            } catch HomeOSError.unauthorized {
                completionHandler(nil, [], false, NSFileProviderError(.notAuthenticated))
            } catch {
                completionHandler(nil, [], false, NSFileProviderError(.serverUnreachable))
            }
        }
        
        return Progress()
    }
    
    // MARK: - Modify Item (Re-upload changed file, rename, move)
    
    func modifyItem(_ item: NSFileProviderItem, baseVersion: NSFileProviderItemVersion,
                    changedFields: NSFileProviderItemFields, contents newContents: URL?,
                    options: NSFileProviderModifyItemOptions = [], request: NSFileProviderRequest,
                    completionHandler: @escaping (NSFileProviderItem?, NSFileProviderItemFields, Bool, Error?) -> Void) -> Progress {
        
        let currentPath = serverPath(from: item.itemIdentifier)
        
        Task {
            do {
                var finalPath = currentPath
                
                // Handle rename
                if changedFields.contains(.filename) {
                    try await client.rename(path: currentPath, newName: item.filename)
                    let parent = (currentPath as NSString).deletingLastPathComponent
                    finalPath = parent == "/" ? "/\(item.filename)" : "\(parent)/\(item.filename)"
                }
                
                // Handle move (parent changed)
                if changedFields.contains(.parentItemIdentifier) {
                    let newParent = serverPath(from: item.parentItemIdentifier)
                    let name = (finalPath as NSString).lastPathComponent
                    let dest = newParent == "/" ? "/\(name)" : "\(newParent)/\(name)"
                    try await client.move(src: finalPath, dest: dest)
                    finalPath = dest
                }
                
                // Handle content change (re-upload)
                if changedFields.contains(.contents), let newContents {
                    let parentPath = (finalPath as NSString).deletingLastPathComponent
                    let fileName = (finalPath as NSString).lastPathComponent
                    // Delete old, upload new
                    try? await client.delete(path: finalPath)
                    try await client.uploadFile(localURL: newContents, toDirectory: parentPath, fileName: fileName)
                }
                
                let updatedItem = FileProviderItem(
                    path: finalPath,
                    name: (finalPath as NSString).lastPathComponent,
                    isDir: item.contentType == .folder || item.contentType == .directory,
                    size: newContents.flatMap { try? FileManager.default.attributesOfItem(atPath: $0.path)[.size] as? Int },
                    modified: Date()
                )
                completionHandler(updatedItem, [], false, nil)
            } catch HomeOSError.unauthorized {
                completionHandler(nil, [], false, NSFileProviderError(.notAuthenticated))
            } catch {
                completionHandler(nil, [], false, NSFileProviderError(.serverUnreachable))
            }
        }
        
        return Progress()
    }
    
    // MARK: - Delete Item
    
    func deleteItem(identifier: NSFileProviderItemIdentifier, baseVersion: NSFileProviderItemVersion,
                    options: NSFileProviderDeleteItemOptions = [], request: NSFileProviderRequest,
                    completionHandler: @escaping (Error?) -> Void) -> Progress {
        
        let path = serverPath(from: identifier)
        
        Task {
            do {
                try await client.delete(path: path)
                completionHandler(nil)
            } catch HomeOSError.unauthorized {
                completionHandler(NSFileProviderError(.notAuthenticated))
            } catch HomeOSError.notFound {
                // Already gone, that's fine
                completionHandler(nil)
            } catch {
                completionHandler(NSFileProviderError(.serverUnreachable))
            }
        }
        
        return Progress()
    }
    
    // MARK: - Enumerator
    
    func enumerator(for containerItemIdentifier: NSFileProviderItemIdentifier,
                    request: NSFileProviderRequest) throws -> NSFileProviderEnumerator {
        
        guard SharedConfig.isConfigured else {
            throw NSFileProviderError(.notAuthenticated)
        }
        
        let path = serverPath(from: containerItemIdentifier)
        return FileProviderEnumerator(path: path, client: client)
    }
    
    // MARK: - Invalidate
    
    func invalidate() {
        _client = nil
    }
    
    // MARK: - Helpers
    
    /// Convert an NSFileProviderItemIdentifier to a server path.
    /// Root container → "/", otherwise the rawValue IS the path.
    private func serverPath(from identifier: NSFileProviderItemIdentifier) -> String {
        if identifier == .rootContainer { return "/" }
        return identifier.rawValue
    }
}
```

---

### HomeOSFileProvider/FileProviderEnumerator.swift
**Extension target only.**

```swift
import FileProvider

/// Enumerates the contents of a directory on the Home OS server.
final class FileProviderEnumerator: NSObject, NSFileProviderEnumerator {
    private let path: String
    private let client: HomeOSAPIClient
    
    init(path: String, client: HomeOSAPIClient) {
        self.path = path
        self.client = client
        super.init()
    }
    
    func invalidate() {}
    
    func enumerateItems(for observer: NSFileProviderEnumerationObserver, startingAt page: NSFileProviderPage) {
        Task {
            do {
                let items = try await client.listDirectory(path: path)
                let providerItems = items.map { FileProviderItem(fileItem: $0) }
                observer.didEnumerate(providerItems)
                observer.finishEnumerating(upTo: nil)
            } catch HomeOSError.unauthorized {
                observer.finishEnumeratingWithError(NSFileProviderError(.notAuthenticated))
            } catch {
                observer.finishEnumeratingWithError(NSFileProviderError(.serverUnreachable))
            }
        }
    }
    
    func enumerateChanges(for observer: NSFileProviderChangeObserver, from anchor: NSFileProviderSyncAnchor) {
        // We don't track incremental changes — just tell Finder to re-enumerate
        observer.finishEnumeratingChanges(upTo: anchor, moreComing: false)
    }
    
    func currentSyncAnchor(completionHandler: @escaping (NSFileProviderSyncAnchor?) -> Void) {
        // Use current timestamp as sync anchor
        let data = "\(Date().timeIntervalSince1970)".data(using: .utf8)!
        completionHandler(NSFileProviderSyncAnchor(data))
    }
}
```

---

### HomeOSFileProvider/FileProviderItem.swift
**Extension target only.**

```swift
import FileProvider
import UniformTypeIdentifiers

/// Represents a single file or folder from the Home OS server as a File Provider item.
final class FileProviderItem: NSObject, NSFileProviderItem {
    
    private let path: String
    private let name: String
    private let isDir: Bool
    private let fileSize: Int?
    private let modifiedDate: Date?
    
    init(path: String, name: String, isDir: Bool, size: Int?, modified: Date?) {
        self.path = path
        self.name = name
        self.isDir = isDir
        self.fileSize = size
        self.modifiedDate = modified
        super.init()
    }
    
    convenience init(fileItem: FileItem) {
        self.init(
            path: fileItem.path,
            name: fileItem.name,
            isDir: fileItem.is_dir,
            size: fileItem.size,
            modified: fileItem.modifiedDate
        )
    }
    
    // MARK: - NSFileProviderItem Protocol
    
    var itemIdentifier: NSFileProviderItemIdentifier {
        NSFileProviderItemIdentifier(path)
    }
    
    var parentItemIdentifier: NSFileProviderItemIdentifier {
        let parent = (path as NSString).deletingLastPathComponent
        if parent == "/" || parent.isEmpty {
            return .rootContainer
        }
        return NSFileProviderItemIdentifier(parent)
    }
    
    var filename: String { name }
    
    var contentType: UTType {
        if isDir { return .folder }
        let ext = (name as NSString).pathExtension.lowercased()
        return UTType(filenameExtension: ext) ?? .data
    }
    
    var capabilities: NSFileProviderItemCapabilities {
        if isDir {
            return [.allowsReading, .allowsWriting, .allowsRenaming, .allowsDeleting, .allowsAddingSubItems]
        }
        return [.allowsReading, .allowsWriting, .allowsRenaming, .allowsDeleting, .allowsReparenting]
    }
    
    var documentSize: NSNumber? {
        fileSize.map { NSNumber(value: $0) }
    }
    
    var contentModificationDate: Date? {
        modifiedDate
    }
    
    var itemVersion: NSFileProviderItemVersion {
        let content = "\(modifiedDate?.timeIntervalSince1970 ?? 0)_\(fileSize ?? 0)".data(using: .utf8)!
        return NSFileProviderItemVersion(contentVersion: content, metadataVersion: content)
    }
    
    // MARK: - Factory Methods
    
    static func rootItem() -> FileProviderItem {
        FileProviderItem(path: "/", name: "Home OS", isDir: true, size: nil, modified: nil)
    }
    
    static func placeholder(path: String) -> FileProviderItem {
        FileProviderItem(
            path: path,
            name: (path as NSString).lastPathComponent,
            isDir: false,
            size: nil,
            modified: Date()
        )
    }
}
```

---

### Main App Changes

The main app needs two small additions:

#### 1. Update SettingsView to use SharedConfig and register the domain

In the login success handler, after storing the token:

```swift
// After successful login:
SharedConfig.serverURL = serverURL
SharedConfig.authToken = token
SharedConfig.username = username

// Register File Provider domain
Task {
    try? await DomainManager.addDomain()
}
```

In the disconnect handler:

```swift
// On disconnect:
SharedConfig.authToken = ""

// Remove File Provider domain
Task {
    try? await DomainManager.removeDomain()
}
```

#### 2. On app launch, ensure domain is registered if already authenticated

In the App's init or onAppear:

```swift
if SharedConfig.isConfigured {
    Task {
        try? await DomainManager.addDomain()
    }
}
```

---

## Testing

1. Build and run the main app
2. Log in via Settings (enter server URL like `https://192.168.0.8:4443`, username, password)
3. Open Finder → sidebar should show "Home OS" under "Locations"
4. Click it — your server's storage directory contents appear
5. Open a file — it downloads on demand
6. Drag a file in — it uploads
7. Delete — moves to trash on server
8. Create New Folder — works

## Troubleshooting

- **Domain doesn't appear**: Run `fileproviderctl list` in Terminal to see registered domains
- **Extension crashes**: Check Console.app, filter by "HomeOSFileProvider"
- **"Not authenticated" errors**: The token may have expired. Re-login in the main app.
- **Self-signed cert issues**: The TrustAllDelegate handles this, but make sure both targets include it
- **Files show as unavailable**: Server may be unreachable. Check network.

## Important Notes

- The File Provider extension runs in its OWN process — it cannot access the main app's memory
- All shared state goes through the App Group UserDefaults (`SharedConfig`)
- The extension is automatically launched by macOS when Finder needs it
- You don't need to manually start or manage the extension
- Changes made on the server (e.g. via the web UI) won't auto-refresh in Finder — call `DomainManager.signalEnumerator()` to trigger a refresh, or just close/reopen the folder
