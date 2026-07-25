# Home OS Mac App — File Provider Architecture

## Goal

Make the Home OS server's storage appear as a native Finder volume on macOS. Files are NOT stored locally — they live on the server and download on demand when opened. Saving/creating files uploads them to the server. The user sees a folder called "Home OS" in Finder's sidebar, just like iCloud Drive.

## How It Works

Apple's **File Provider** framework lets apps expose remote files in Finder without syncing everything locally. The architecture has two parts:

1. **Main App** (`HomeOS.app`) — The existing menu bar app. Handles login, stores credentials, shows status.
2. **File Provider Extension** (`HomeOSFileProvider.appex`) — An embedded app extension that implements Apple's `NSFileProviderReplicatedExtension` protocol. This is what Finder talks to.

The extension runs in its own sandboxed process. It communicates with the main app only through:
- **App Group** shared container (for credentials/config)
- **NSFileProviderManager** signals (to notify Finder of changes)

## Xcode Project Structure

```
HomeOS/
├── HomeOS.xcodeproj
├── HomeOS/                          (Main app target)
│   ├── HomeOSApp.swift
│   ├── AppState.swift
│   ├── ConnectionManager.swift
│   ├── MenuBarView.swift
│   ├── SettingsView.swift
│   ├── MainWindowView.swift
│   ├── Info.plist
│   └── HomeOS.entitlements
├── HomeOSFileProvider/              (File Provider extension target)
│   ├── FileProviderExtension.swift  (Main extension class)
│   ├── FileProviderEnumerator.swift (Lists directory contents)
│   ├── FileProviderItem.swift       (Represents a single file/folder)
│   ├── Info.plist
│   └── HomeOSFileProvider.entitlements
├── Shared/                          (Shared between app and extension)
│   ├── APIClient.swift              (HTTP client for Home OS server)
│   ├── APIModels.swift              (Response types)
│   ├── SharedConfig.swift           (Read/write credentials via App Group)
│   └── DomainManager.swift          (Register/unregister the file provider domain)
```

## Key Concepts

### File Provider Domain
A "domain" is the named volume that appears in Finder. We register one called "Home OS" when the user logs in, and remove it when they disconnect.

### Item Identifiers
Every file/folder needs a stable, unique identifier. We use the server path:
- Root: `NSFileProviderItemIdentifier.rootContainer` maps to `/` on the server
- Files: The identifier IS the server path, e.g. `/Documents/report.pdf`

### Enumeration
When Finder opens a folder, the extension's enumerator is asked to list its contents. The enumerator calls `GET /files/<path>` on the Home OS API and returns `NSFileProviderItem` objects for each entry.

### Fetching (Download)
When a file is opened, the extension's `fetchContents(for:)` is called. It downloads from `GET /files/<path>?download` and provides the local temporary file to Finder.

### Creating/Modifying (Upload)
When a file is saved or dragged in, `createItem()` or `modifyItem()` is called with the local file. The extension uploads it via `POST /api/files/upload`.

### Deleting
When deleted in Finder, `deleteItem()` calls `POST /api/files/delete`.

## API Endpoints Used

The extension needs these Home OS API endpoints (all exist already):

| Operation | Endpoint | Method |
|-----------|----------|--------|
| List directory | `/files/<path>` (Accept: application/json) | GET |
| Download file | `/files/<path>?download` | GET |
| Upload file | `/api/files/upload` | POST (multipart) |
| Create directory | `/api/files/mkdir` | POST |
| Rename | `/api/files/rename` | POST |
| Move | `/api/files/move` | POST |
| Copy | `/api/files/copy` | POST |
| Delete | `/api/files/delete` | POST |
| Storage info | `/storage` (Accept: application/json) | GET |

All authenticated via `Authorization: Bearer <token>` header.

## API Response Formats

### List Directory (`GET /files/Documents` with `Accept: application/json`)
```json
{
  "ok": true,
  "data": {
    "path": "/Documents",
    "entries": [
      {
        "name": "report.pdf",
        "path": "/Documents/report.pdf",
        "is_dir": false,
        "size": 245760,
        "modified": "2026-06-15T10:30:00",
        "extension": "pdf"
      },
      {
        "name": "Photos",
        "path": "/Documents/Photos",
        "is_dir": true,
        "size": null,
        "modified": "2026-06-14T08:00:00",
        "extension": null
      }
    ]
  }
}
```

### Upload (`POST /api/files/upload`)
Multipart form data with fields:
- `path`: destination directory (e.g. `/Documents`)
- `file`: the file data

### Create Directory (`POST /api/files/mkdir`)
```json
{"path": "/Documents/NewFolder"}
```

### Rename (`POST /api/files/rename`)
```json
{"path": "/Documents/old-name.txt", "new_name": "new-name.txt"}
```

### Move (`POST /api/files/move`)
```json
{"src": "/Documents/file.txt", "dest": "/Archive/file.txt"}
```

### Delete (`POST /api/files/delete`)
```json
{"path": "/Documents/file.txt"}
```

## Authentication Flow

1. User enters server URL + credentials in the main app's Settings
2. Main app calls `POST /api/login` → receives Bearer token
3. Token is stored in the App Group shared container (UserDefaults suite)
4. File Provider extension reads the token from the same shared container
5. All API calls use `Authorization: Bearer <token>`

## Entitlements Required

### Main App (`HomeOS.entitlements`)
```xml
<key>com.apple.security.app-sandbox</key><true/>
<key>com.apple.security.network.client</key><true/>
<key>com.apple.security.application-groups</key>
<array><string>group.com.homeos.app</string></array>
```

### File Provider Extension (`HomeOSFileProvider.entitlements`)
```xml
<key>com.apple.security.app-sandbox</key><true/>
<key>com.apple.security.network.client</key><true/>
<key>com.apple.security.application-groups</key>
<array><string>group.com.homeos.app</string></array>
```

### Info.plist for Extension
```xml
<key>NSExtension</key>
<dict>
    <key>NSExtensionPointIdentifier</key>
    <string>com.apple.fileprovider-nonui</string>
    <key>NSExtensionPrincipalClass</key>
    <string>$(PRODUCT_MODULE_NAME).FileProviderExtension</string>
</dict>
```

## Domain Registration

When the user logs in successfully:
```swift
let domain = NSFileProviderDomain(identifier: .init(rawValue: "homeos"), displayName: "Home OS")
NSFileProviderManager.add(domain) { error in ... }
```

When they disconnect:
```swift
NSFileProviderManager.remove(domain) { error in ... }
```

## Materialization Behavior

Files in the Finder show as "in cloud" (download icon) until opened. When opened:
1. Finder asks the extension for the file contents
2. Extension downloads from the server
3. File is cached locally temporarily
4. macOS manages the cache (evicts when space is needed)

This means the Mac's disk only stores files that are actively in use.

## Error Handling

- **Offline**: Return `.serverUnreachable` — Finder shows items as unavailable
- **Auth expired**: Return `.notAuthenticated` — triggers re-auth flow
- **File not found**: Return `.noSuchItem`
- **Conflict**: Return `.contentConflict` with both versions

## Deployment Notes

- Minimum macOS 14.0 (Sonoma) — for modern File Provider APIs
- The extension is embedded inside the app bundle at `HomeOS.app/Contents/PlugIns/HomeOSFileProvider.appex`
- Signing: Both app and extension must be signed (development signing is fine for personal use)
- No App Store required — can be distributed as a .app for personal use
- macFUSE is NOT needed — this is 100% native Apple API
