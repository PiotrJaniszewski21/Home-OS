# Home OS Mac App Development Plan

## Goal

Build the SwiftUI Mac app into a reliable native companion for Home OS, with a clean project layout, tested API contracts, visible runtime diagnostics, and a phased path toward Finder integration.

## Current Structure

- `apps/macos-files/Sources/HomeOS/App` — app entry point and scene definitions.
- `apps/macos-files/Sources/HomeOS/Models` — API DTOs and app state.
- `apps/macos-files/Sources/HomeOS/Services` — API, session, and transfer logic.
- `apps/macos-files/Sources/HomeOS/Views` — dashboard, menu bar, and settings views.
- `apps/macos-files/Sources/HomeOS/Support` — logging, formatting, and window helpers.
- `apps/macos-files/Tests/HomeOSTests` — Swift unit tests.
- `apps/macos-files/scripts/build_and_run.sh` — macOS build/run/verify helper.
- `server/scripts/test_hosted_api.py` — hosted API contract test.

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
- Use `./apps/macos-files/scripts/build_and_run.sh --logs` for process logs.
- Use `./apps/macos-files/scripts/build_and_run.sh --telemetry` for subsystem/category-focused logs.
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
swift test --package-path apps/macos-files
```

```bash
./apps/macos-files/scripts/build_and_run.sh --verify
```

```bash
HOMEOS_TEST_URL="https://example.com" \
HOMEOS_TEST_USERNAME="<username>" \
HOMEOS_TEST_PASSWORD="<password>" \
./server/scripts/test_hosted_api.py
```

```bash
./apps/macos-files/scripts/build_and_run.sh --logs
```

```bash
./apps/macos-files/scripts/build_and_run.sh --telemetry
```
