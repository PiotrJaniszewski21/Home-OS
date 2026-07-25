# Server Connection Details

## Network
- **Local IP**: 192.168.0.8
- **Port**: 4443 (HTTPS, self-signed certificate)
- **URL**: `https://192.168.0.8:4443`
- **Remote URL** (when Cloudflare tunnel is active): The quick tunnel URL changes each time. For permanent access, set up a named tunnel with a domain.

## Authentication
- **Login endpoint**: `POST /api/login`
- **Request body**: `{"username": "Peter", "password": "<password>"}`
- **Response**: `{"ok": true, "data": {"token": "...", "user": {"username": "Peter", "role": "admin"}}}`
- **Token usage**: `Authorization: Bearer <token>` header on all subsequent requests

## File API Behavior

### Listing directories
- `GET /files/` with `Accept: application/json` → lists root of user storage
- `GET /files/Documents` with `Accept: application/json` → lists /Documents
- Response contains an `entries` array with objects having: `name`, `path`, `is_dir`, `size`, `modified`
- `modified` is ISO-8601 format: `2026-06-15T10:30:00`
- `size` is in bytes, `null` for directories

### Downloading files
- `GET /files/Documents/report.pdf?download` → returns the file with Content-Disposition: attachment
- No JSON wrapper — raw file bytes

### Uploading files
- `POST /api/files/upload` with multipart/form-data
- Fields: `path` (destination directory), `file` (file data)
- Returns redirect on success (302), so handle accordingly

### Creating directories
- `POST /api/files/mkdir` with JSON `{"path": "/Documents/NewFolder"}`
- Returns `{"ok": true, "data": {...}}`

### Renaming
- `POST /api/files/rename` with JSON `{"path": "/Documents/old.txt", "new_name": "new.txt"}`

### Moving
- `POST /api/files/move` with JSON `{"src": "/Documents/file.txt", "dest": "/Archive/file.txt"}`

### Deleting
- `POST /api/files/delete` with JSON `{"path": "/Documents/file.txt"}`
- Moves to trash (not permanent delete)

## TLS Certificate
The server uses a self-signed certificate. The app must either:
- Implement a custom URLSessionDelegate that trusts all server certs (included in the implementation)
- Or pin the specific certificate

The `TrustAllDelegate` class in the implementation handles this.

## Storage Layout
- User storage root: `/opt/home-os/storage/` on the server
- The API treats this as `/` — all paths are relative to the storage root
- External drives: available at `/files/drive/<name>/` but the File Provider should focus on the main storage (`/files/`)
