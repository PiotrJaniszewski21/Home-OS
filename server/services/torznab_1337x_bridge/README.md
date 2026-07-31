# Home OS 1337x Torznab Bridge

This localhost-only service uses a persistent Invisible Playwright browser to
fetch and parse 1337x pages. It exposes a Torznab API to Prowlarr and resolves
torrent downloads lazily through the detail page.

The same browser process also exposes a Uindex Torznab endpoint. Uindex results
already contain magnet links, so the feed returns those directly.

TorrentGalaxy is exposed through the same service. Searches use its reachable
mirror, while downloads resolve the magnet lazily from the detail page.

Install on the Home OS server:

```bash
sudo bash install.sh
```

For an existing installation, update the bridge in place with automatic
rollback:

```bash
sudo bash update.sh
```

Endpoints:

- `GET /health`
- `GET /api?t=caps&apikey=...`
- `GET /api?t=search&q=...&apikey=...`
- `GET /download?path=/torrent/.../&apikey=...`
- `GET /uindex/api?t=caps&apikey=...`
- `GET /uindex/api?t=search&q=...&apikey=...`
- `GET /torrentgalaxy/api?t=caps&apikey=...`
- `GET /torrentgalaxy/api?t=search&q=...&apikey=...`
- `GET /torrentgalaxy/download?path=/post-detail/.../&apikey=...`

The API key is generated in `/etc/home-os-1337x-bridge.env`. Configure
Prowlarr's Generic Torznab URL as `http://127.0.0.1:8787` with API path `/api`.
For Uindex, use URL `http://127.0.0.1:8787/uindex` with API path `/api`.
For TorrentGalaxy, use URL `http://127.0.0.1:8787/torrentgalaxy` with API path
`/api`.

On a Home OS server with Prowlarr already configured for either bridge, add or
update the TorrentGalaxy indexer and synchronize applications with:

```bash
sudo python3 configure_prowlarr.py
```
