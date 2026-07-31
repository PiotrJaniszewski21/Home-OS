#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import sys
import time
import urllib.parse
import urllib.request
from copy import deepcopy
from pathlib import Path
from xml.etree import ElementTree

PROWLARR_API = "http://127.0.0.1:9696/svc/prowlarr/api/v1"
BRIDGE_API = "http://127.0.0.1:8787/torrentgalaxy/api"
INDEXER_NAME = "TorrentGalaxy Browser Bridge"
ARR_APPS = (
    ("Sonarr", Path("/opt/Sonarr/data/config.xml"), 8989),
    ("Radarr", Path("/opt/Radarr/data/config.xml"), 7878),
)


def _bridge_key() -> str:
    for line in Path("/etc/home-os-1337x-bridge.env").read_text().splitlines():
        name, separator, value = line.partition("=")
        if separator and name == "BRIDGE_API_KEY":
            return value
    raise RuntimeError("BRIDGE_API_KEY is missing")


def _prowlarr_key() -> str:
    root = ElementTree.parse("/opt/Prowlarr/data/config.xml").getroot()
    value = root.findtext("ApiKey", "")
    if not value:
        raise RuntimeError("Prowlarr API key is missing")
    return value


def _request(
    url: str,
    *,
    method: str = "GET",
    api_key: str = "",
    payload: object | None = None,
    timeout: int = 180,
) -> bytes:
    data = None
    headers = {}
    if api_key:
        headers["X-Api-Key"] = api_key
    if payload is not None:
        data = json.dumps(payload).encode()
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(
        url,
        data=data,
        headers=headers,
        method=method,
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read()


def _prowlarr(
    path: str,
    *,
    api_key: str,
    method: str = "GET",
    payload: object | None = None,
) -> object:
    body = _request(
        f"{PROWLARR_API}{path}",
        method=method,
        api_key=api_key,
        payload=payload,
    )
    return json.loads(body) if body else {}


def _set_field(indexer: dict[str, object], name: str, value: object) -> None:
    fields = indexer.get("fields", [])
    if not isinstance(fields, list):
        raise RuntimeError("Prowlarr indexer fields are invalid")
    for field in fields:
        if isinstance(field, dict) and field.get("name") == name:
            field["value"] = value
            return
    raise RuntimeError(f"Prowlarr indexer field is missing: {name}")


def _test_bridge(bridge_key: str) -> int:
    query = urllib.parse.urlencode(
        {
            "t": "search",
            "q": "ubuntu",
            "limit": 5,
            "apikey": bridge_key,
        }
    )
    root = ElementTree.fromstring(_request(f"{BRIDGE_API}?{query}"))
    items = root.findall("./channel/item")
    if not items:
        raise RuntimeError("TorrentGalaxy bridge returned no Ubuntu results")
    link = items[0].findtext("link", "")
    payload = _request(link)
    if not payload.startswith(b"d"):
        raise RuntimeError("TorrentGalaxy bridge returned an invalid torrent")
    return len(items)


def _prepare_indexer(
    source: dict[str, object],
    *,
    bridge_key: str,
) -> dict[str, object]:
    indexer = deepcopy(source)
    indexer["name"] = INDEXER_NAME
    indexer["enable"] = True
    _set_field(indexer, "baseUrl", "http://127.0.0.1:8787/torrentgalaxy")
    _set_field(indexer, "apiPath", "/api")
    _set_field(indexer, "apiKey", bridge_key)
    return indexer


def _wait_for_command(command_id: int, *, api_key: str) -> str:
    for _ in range(180):
        command = _prowlarr(f"/command/{command_id}", api_key=api_key)
        if not isinstance(command, dict):
            raise RuntimeError("Prowlarr returned an invalid command response")
        status = str(command.get("status", "")).casefold()
        if status in {"completed", "failed", "aborted", "cancelled"}:
            return status
        time.sleep(1)
    raise RuntimeError("Prowlarr application sync timed out")


def _verify_arr_indexer(
    name: str,
    config_path: Path,
    default_port: int,
) -> None:
    root = ElementTree.parse(config_path).getroot()
    api_key = root.findtext("ApiKey", "")
    port = int(root.findtext("Port", str(default_port)))
    url_base = root.findtext("UrlBase", "").strip("/")
    prefix = f"/{url_base}" if url_base else ""
    body = _request(
        f"http://127.0.0.1:{port}{prefix}/api/v3/indexer",
        api_key=api_key,
    )
    indexers = json.loads(body)
    if not isinstance(indexers, list) or not any(
        INDEXER_NAME in str(indexer.get("name", ""))
        for indexer in indexers
        if isinstance(indexer, dict)
    ):
        raise RuntimeError(f"{name} does not contain the TorrentGalaxy indexer")
    print(f"{name} indexer sync verified")


def _verify_arr_indexers() -> None:
    for name, config_path, default_port in ARR_APPS:
        _verify_arr_indexer(name, config_path, default_port)


def main() -> None:
    if os.geteuid() != 0:
        raise PermissionError("Run this configurator as root")
    if len(sys.argv) > 1:
        if sys.argv[1:] != ["--verify-downstream-only"]:
            raise ValueError("Usage: configure_prowlarr.py [--verify-downstream-only]")
        _verify_arr_indexers()
        return

    bridge_key = _bridge_key()
    prowlarr_key = _prowlarr_key()
    result_count = _test_bridge(bridge_key)

    indexers = _prowlarr("/indexer", api_key=prowlarr_key)
    if not isinstance(indexers, list):
        raise RuntimeError("Prowlarr returned an invalid indexer list")
    existing = next(
        (
            item
            for item in indexers
            if isinstance(item, dict) and item.get("name") == INDEXER_NAME
        ),
        None,
    )
    template = existing or next(
        (
            item
            for item in indexers
            if isinstance(item, dict)
            and item.get("name") in {"Uindex Browser Bridge", "1337x Browser Bridge"}
        ),
        None,
    )
    if not isinstance(template, dict):
        raise RuntimeError("No Generic Torznab bridge template exists in Prowlarr")

    indexer = _prepare_indexer(template, bridge_key=bridge_key)
    _prowlarr(
        "/indexer/test",
        api_key=prowlarr_key,
        method="POST",
        payload=indexer,
    )

    if existing:
        indexer_id = int(existing["id"])
        saved = _prowlarr(
            f"/indexer/{indexer_id}",
            api_key=prowlarr_key,
            method="PUT",
            payload=indexer,
        )
        action = "updated"
    else:
        indexer.pop("id", None)
        saved = _prowlarr(
            "/indexer",
            api_key=prowlarr_key,
            method="POST",
            payload=indexer,
        )
        action = "created"
    if not isinstance(saved, dict):
        raise RuntimeError("Prowlarr returned an invalid saved indexer")
    indexer_id = int(saved["id"])

    command = _prowlarr(
        "/command",
        api_key=prowlarr_key,
        method="POST",
        payload={"name": "ApplicationIndexerSync"},
    )
    if not isinstance(command, dict):
        raise RuntimeError("Prowlarr returned an invalid sync command")
    sync_status = _wait_for_command(int(command["id"]), api_key=prowlarr_key)
    if sync_status != "completed":
        raise RuntimeError(f"Prowlarr application sync {sync_status}")

    print(f"TorrentGalaxy indexer {action}: id={indexer_id}")
    print(f"Bridge search and torrent download passed: results={result_count}")
    print("Application indexer sync completed")
    _verify_arr_indexers()


if __name__ == "__main__":
    main()
