# Home OS Mac App Development Plan

## Goal

Build the SwiftUI Mac app into a reliable native companion for Home OS, with a clean project layout, tested API contracts, visible runtime diagnostics, and a phased path toward Finder integration.

## Current Structure

- `HomeOSApp/Sources/HomeOS/App` — app entry point and scene definitions.
- `HomeOSApp/Sources/HomeOS/Models` — API DTOs, app state, and selection models.
- `HomeOSApp/Sources/HomeOS/Services` — hosted API client, connection/session logic, transfers, and Desktop drop-folder offload.
- `HomeOSApp/Sources/HomeOS/Views` — dashboard, files, AI chat, menu bar, settings, and main window views.
- `HomeOSApp/Sources/HomeOS/Support` — formatters, logging, window helpers, and small extensions.
- `HomeOSApp/Tests/HomeOSTests` — Swift unit tests for API decoding and client behavior.
- `script/build_and_run.sh` — app bundle build/run/verify/log entry point.
- `script/test_hosted_api.py` — hosted API contract test for the Flask server.

## Phases

### Phase 1 — Stabilise Core Companion

- Make login/session state reliable against the hosted domain.
- Keep dashboard health independent from optional storage failures.
- Show clear server errors in Settings, dashboard, and menu bar.
- Add browser-like headers required by the hosted Cloudflare policy.
- Add baseline API response decoding tests.

Status: in progress, core pieces implemented.

### Phase 2 — Files Experience

- Improve file browser layout and action discoverability.
- Add download progress, completion, and failure states.
- Add upload status and queued upload states.
- Add rename, move/copy, safer delete confirmation, and refresh behavior.
- Add preview/open behavior for common file types.
- Add a Finder-visible Desktop drop folder that uploads local additions to Home OS and removes local copies after success.

Status: in progress. Download state, efficient temp-file downloads, upload progress, rename, copy, move, delete confirmation, Quick Look preview, Desktop drop-folder offload, and file-operation API tests are implemented.

### Phase 3 — AI Assistant

- Polish native chat flow and loading states.
- Show provider/config warnings when AI is not configured server-side.
- Render tool-call results in a readable native format.
- Add chat error recovery and retry actions.

### Phase 4 — Desktop Polish

- Add keyboard shortcuts and command menus for key actions.
- Persist window/sidebar state where useful.
- Improve menu-bar summary and quick actions.
- Add lightweight notifications for completed transfers and server warnings.

### Phase 5 — Test Harness

- Expand unit tests for URL building, file operations, and error handling.
- Add mock API flows for login, files, downloads, upload failures, and AI errors.
- Keep hosted API contract tests separate from unit tests because they require credentials.

### Phase 6 — Runtime Diagnostics

- Keep structured `Logger` telemetry for connection and transfer milestones.
- Use `./script/build_and_run.sh --logs` for process logs.
- Use `./script/build_and_run.sh --telemetry` for subsystem/category-focused logs.
- Do not log tokens, passwords, raw file names, or message contents.

### Phase 7 — Finder/File Provider

- Keep the current Desktop drop folder as a safe one-way bridge from Mac to Home OS.
- Upload files moved or copied into `~/Desktop/Home OS`, then remove the local drop-folder copy after the server upload succeeds.
- Do not represent server-only files as normal local files until File Provider is implemented.
- Implement move-from-server-to-client and copy-from-server-to-client semantics through a File Provider extension, because Finder must be able to distinguish move and copy operations for server-only placeholders.
- Treat Finder integration as its own milestone.
- Define server-side file-provider endpoints and sync/change-token behavior first.
- Implement the File Provider extension separately from the menu-bar companion.

### Phase 8 — Packaging

- Prepare app bundle metadata, icons, signing settings, and entitlement review.
- Add packaging and notarization workflow once the app behavior stabilises.

## Verification Commands

Run from the repository root:

```bash
swift test --package-path HomeOSApp
```

```bash
./script/build_and_run.sh --verify
```

```bash
HOMEOS_TEST_URL="https://example.com" \
HOMEOS_TEST_USERNAME="<username>" \
HOMEOS_TEST_PASSWORD="<password>" \
./script/test_hosted_api.py
```

```bash
./script/build_and_run.sh --logs
```

```bash
./script/build_and_run.sh --telemetry
```
