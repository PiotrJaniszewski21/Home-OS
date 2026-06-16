# Instructions for Claude on Mac

You are building a macOS app called "Home OS" that makes a remote Linux server's file storage appear as a native Finder folder. Read the two companion documents in this directory first:
- `ARCHITECTURE.md` — high-level design and concepts
- `IMPLEMENTATION.md` — complete source code and Xcode setup steps

## What you're building

A macOS app with two components:
1. **Main App** — Menu bar app with login/settings. Already partially written (in `../Sources/HomeOS/`).
2. **File Provider Extension** — Makes the server appear as a volume in Finder. This is the new part.

## The Server

Home OS runs on a Debian Linux box at `https://<ip>:4443` with a self-signed TLS cert. It has a REST API for file operations. All endpoints require `Authorization: Bearer <token>`. The token is obtained via `POST /api/login` with username/password.

The server's API is documented in `IMPLEMENTATION.md` under "API Endpoints Used". The file listing endpoint returns JSON with entries containing `name`, `path`, `is_dir`, `size`, and `modified` fields.

## Your task

1. Create a new Xcode project (macOS App, SwiftUI, Swift, called "HomeOS")
2. Add a File Provider Extension target called "HomeOSFileProvider"
3. Configure App Groups (`group.com.homeos.app`) on both targets
4. Enable "Outgoing Connections (Client)" in sandbox for both targets
5. Add all the source files from `IMPLEMENTATION.md`
6. Wire up the main app to use `SharedConfig` and `DomainManager` for login/logout
7. Build and test

## Key constraints

- **macOS 14+ only** (uses modern NSFileProviderReplicatedExtension)
- **Self-signed TLS cert** — the `TrustAllDelegate` class handles this
- **No local file storage needed** — files download on demand, are cached by the OS, and evicted when space is needed
- **App Group shared state** — the extension reads credentials from `group.com.homeos.app` UserDefaults
- **The extension is a separate process** — it cannot import SwiftUI or access the main app's objects

## Existing code you can reference

The `../Sources/HomeOS/` directory has the existing main app code:
- `APIClient.swift` — Old API client (replace with `HomeOSAPIClient` from the shared code)
- `APIModels.swift` — Old models (the shared code has its own)
- `AppState.swift` — App state management
- `ConnectionManager.swift` — Connection health checks
- `MenuBarView.swift` — Menu bar UI
- `SettingsView.swift` — Settings/login UI (needs updating to use SharedConfig + DomainManager)
- `MainWindowView.swift` — WebKit wrapper for the web UI
- `HomeOSApp.swift` — App entry point

You can either refactor the existing app or start fresh — the key new work is the File Provider extension.

## What success looks like

After building and running:
1. User launches HomeOS.app
2. A menu bar icon appears
3. User opens Settings, enters `https://192.168.0.8:4443`, username `Peter`, and password
4. Login succeeds → "Home OS" appears in Finder's sidebar under Locations
5. Clicking it shows the server's storage folder contents
6. Double-clicking a file downloads it and opens it
7. Dragging a file in uploads it
8. Right-click → Delete sends it to the server's trash
9. New Folder creates a directory on the server
10. Rename works
11. Move (drag between folders) works

## Common pitfalls to avoid

- File Provider extensions CANNOT use `@main`, SwiftUI, or any UI frameworks
- The extension MUST be embedded inside the app bundle (Xcode does this automatically when you add the target)
- `NSFileProviderItemIdentifier.rootContainer` is special — never create items with this as their own identifier
- Item identifiers must be stable across enumerations (we use the server path)
- The `item(for:)` method must work for any valid identifier, not just currently-enumerated ones
- Always handle the self-signed cert — without `TrustAllDelegate`, all network calls fail silently
- The extension's principal class in Info.plist must match: `$(PRODUCT_MODULE_NAME).FileProviderExtension`
- Don't forget `import FileProvider` in extension files and `import UniformTypeIdentifiers` where UTType is used
