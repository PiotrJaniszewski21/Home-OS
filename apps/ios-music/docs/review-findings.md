# HomeMusic Review Findings

Date: 14 July 2026

## Scope

This report captures the findings from the initial review of the HomeMusic iOS app and its Home OS Flask API. No feature implementation is represented as complete here.

## Active architecture

- The active music client is the iOS application at `apps/ios-music/HomeMusic.xcodeproj`.
- Its source lives under `apps/ios-music/Sources` and targets iOS 17.
- The active server is the Flask application under `server/home_os/`.
- Music API routes are implemented in `server/home_os/modules/music/routes.py`.
- Music persistence models are implemented in `server/home_os/models/music.py`.
- `apps/macos-files/HomeOS.xcodeproj` is the separate macOS Home OS companion.

## Repository state warning

The root worktree already contains many modified and untracked files. Changes should remain tightly scoped to the HomeMusic client, its music API/models, and their tests so unrelated work is preserved.

## Confirmed existing behavior

### User isolation

- Listen history and loved-track state are stored in `MusicListen` with a required `user_id`.
- The database has a unique constraint on `(user_id, track_id)`.
- Playlists have a required `user_id` and playlist tracks belong to a specific playlist.
- Playlist lookup uses the current user.
- `tests/test_home_music.py` already contains a playlist-isolation test between users.
- Further tests are still required for loved tracks, albums, and any new server-side library records.

### Current offline/download design

- `OfflineMusicStore` only models downloaded playlists.
- Offline files are separated by a SHA-256 digest of the server URL and bearer token, providing per-account local storage.
- Track files are named from a SHA-256 digest of the track ID.
- A shared track file can be reused by multiple playlist records.
- Existing files are skipped during playlist download, which is the start of download deduplication.
- The current manifest only stores playlist records; it cannot represent standalone downloaded tracks cleanly.

## Confirmed gaps and likely defects

### 1. Albums cannot be added to the library

- Album detail exists as a remote `AlbumDetail` model.
- There is no saved-album database model, API, client store, or Albums library destination.
- Album download actions are also absent.

### 2. Standalone track downloads are missing

- Downloading is exposed only at playlist level.
- A track outside a playlist cannot be downloaded.
- Loved Songs and individual playlist rows do not currently have a complete one-track download workflow.
- A permanent Downloads library destination cannot be built reliably from the current playlist-only manifest.

### 3. Playlist download failure handling is weak

- Each track requests a playback source and then downloads either the direct URL or proxy fallback.
- Failures are collected only as track titles and reduced to one generic message.
- The underlying HTTP status, response content type, URL expiry, move failure, or storage error is not surfaced.
- There are no focused client tests for successful download, direct-source fallback, partial playlist resume, duplicate suppression, or manifest recovery.
- A playlist is marked as having a download after any single track succeeds, which can make partial state look more complete than it is.

### 4. Download persistence needs a track-first model

The manifest should become track-first rather than playlist-only:

- one durable record per downloaded track;
- optional playlist and album membership references;
- metadata for title, artist, artwork, album, duration, file type, and download date;
- atomic manifest updates;
- validation that the local file exists and is non-empty;
- one in-flight task per track ID;
- no second network transfer when a valid file already exists;
- reference-aware deletion so removing a playlist does not delete a track still retained individually, by an album, or by another playlist.

### 5. Artwork fallback is not represented in the track model

- `Track` currently has only a single `thumbnail` value.
- It has no album ID, album title, or album artwork field.
- The UI therefore cannot reliably distinguish missing track artwork from album artwork.
- The API and models should provide album metadata and a resolved artwork rule: use track artwork when present, otherwise use album artwork.
- Persisted playlist, loved-track, album, and offline records should preserve the resolved album artwork.

### 6. The Library is incomplete

The requested permanent structure should include at least:

- Albums
- Playlists
- Loved Songs
- Recently Played
- Downloads

Downloads should be a permanent destination even when empty, and should list every locally available track regardless of how it was downloaded.

### 7. Track action menus need to be consistent

The same three-dot menu should be reusable across search results, album tracks, playlist tracks, Loved Songs, Downloads, and other track lists. Relevant actions include:

- Play Next
- Play Later
- Add to Playlist
- Love/Unlove
- Download or Remove Download
- Remove from Playlist when shown inside a playlist

Download progress and failure state should be visible at row level.

### 8. Search needs a product-level redesign

- The current implementation is a basic remote query with a small history store.
- It does not yet resemble Apple Music's search flow.
- The redesign should include an Apple Music-like search field, recent searches, clear/cancel behavior, debounced requests, loading and empty states, separated result sections, and navigation to artists/albums.
- Results should support songs, artists, and albums rather than presenting a song-only experience.
- Request cancellation or generation checks are needed so stale responses cannot replace newer search results.

### 9. Now Playing needs refinement

- The requested scrubber should use a slim filled track without a permanently visible knob, similar to Apple Music.
- A knob can appear while actively dragging if necessary for accessibility and precision.
- Above the slider, artwork and metadata should be given clearer hierarchy.
- Playback, queue, AirPlay, love, and more actions should use consistent spacing and hit targets.
- Accessibility labels and adjustable actions must remain available even if the visible slider thumb is removed.

## Recommended implementation phases

### Phase 1: Persistence and reliable downloads

- Replace the playlist-only offline manifest with a track-first manifest.
- Add single-track download and removal APIs in `OfflineMusicStore`.
- Add in-flight deduplication and valid-file reuse.
- Repair playlist download and resume behavior on top of the single-track primitive.
- Preserve shared files until no retained collection references them.
- Add focused tests for success, failure, fallback, partial resume, persistence, and deduplication.

### Phase 2: Albums and library structure

- Add per-user saved-album models and API endpoints.
- Add album metadata to track payloads.
- Implement track-artwork-to-album-artwork fallback.
- Add Albums and permanent Downloads destinations to Library.
- Add album download/remove controls.
- Add server tests proving albums and loved tracks are isolated by user.

### Phase 3: Unified track menus

- Build one reusable track menu.
- Add download actions to search, album, playlist, Loved Songs, and other track rows.
- Show downloaded, downloading, failed, and retry states consistently.

### Phase 4: Search redesign

- Add song, artist, and album search results.
- Add debouncing and stale-request cancellation.
- Rework recent-search, loading, empty, and error states to closely follow Apple Music conventions.

### Phase 5: Now Playing polish

- Redesign visual hierarchy and spacing.
- Replace the current slider presentation with a slim Apple Music-like scrubber.
- Verify seeking, accessibility, compact layouts, queue controls, AirPlay, and interruption behavior.

### Phase 6: End-to-end verification

- Run focused Flask music tests.
- Build the HomeMusic iOS target with `xcodebuild`.
- Exercise single-track, album, playlist, Loved Songs, Downloads, restart persistence, resume, and duplicate-download flows.
- Verify two Home OS users cannot see or mutate each other's playlists, albums, loved tracks, or history.

## Completion criteria

The work should not be considered complete until:

- one track is never transferred twice when a valid local copy already exists;
- interrupted playlist downloads resume and report precise failures;
- single tracks can be downloaded from every requested three-dot menu;
- Albums and Downloads are permanent Library destinations;
- tracks without artwork display their album cover;
- downloaded tracks survive application restart and account reconnection;
- search returns correctly grouped, current results;
- Now Playing seeking is reliable and accessible;
- automated user-isolation tests cover playlists, loved tracks, albums, and history;
- server tests pass and the iOS app builds successfully.
