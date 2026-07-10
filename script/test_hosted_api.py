#!/usr/bin/env python3
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request


USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) AppleWebKit/605.1.15 (KHTML, like Gecko) HomeOSMac/1.0 Safari/605.1.15"


def fail(message):
    print(f"FAIL: {message}", file=sys.stderr)
    raise SystemExit(1)


def request(base_url, method, path, token=None, payload=None, accept="application/json"):
    data = None
    headers = {
        "Accept": accept,
        "User-Agent": USER_AGENT,
        "X-Requested-With": "XMLHttpRequest",
    }
    if payload is not None:
        data = json.dumps(payload).encode()
        headers["Content-Type"] = "application/json"
    if token:
        headers["Authorization"] = f"Bearer {token}"

    req = urllib.request.Request(base_url + path, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            return resp.status, resp.headers.get("content-type", ""), resp.read()
    except urllib.error.HTTPError as err:
        return err.code, err.headers.get("content-type", ""), err.read()


def decode_json(path, status, content_type, body):
    if status < 200 or status > 299:
        snippet = body[:240].decode(errors="replace").replace("\n", " ")
        fail(f"{path} returned HTTP {status}: {snippet}")
    if "json" not in content_type.lower():
        fail(f"{path} returned non-JSON content-type {content_type!r}")
    try:
        return json.loads(body)
    except json.JSONDecodeError as err:
        fail(f"{path} returned invalid JSON: {err}")


def main():
    base_url = os.environ.get("HOMEOS_TEST_URL", "").rstrip("/")
    username = os.environ.get("HOMEOS_TEST_USERNAME", "")
    password = os.environ.get("HOMEOS_TEST_PASSWORD", "")

    if not base_url or not username or not password:
        fail("set HOMEOS_TEST_URL, HOMEOS_TEST_USERNAME, and HOMEOS_TEST_PASSWORD")

    parsed = urllib.parse.urlparse(base_url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        fail("HOMEOS_TEST_URL must be a full http(s) URL")

    checks = []

    status, content_type, body = request(
        base_url,
        "POST",
        "/api/login",
        payload={"username": username, "password": password},
    )
    login = decode_json("POST /api/login", status, content_type, body)
    if not login.get("ok") or not login.get("data", {}).get("token"):
        fail("POST /api/login did not return an API token")
    token = login["data"]["token"]
    checks.append("POST /api/login")

    for method, path in [
        ("GET", "/api/monitor/metrics"),
        ("GET", "/storage"),
        ("GET", "/files"),
        ("GET", "/api/files/search?q=test"),
    ]:
        status, content_type, body = request(base_url, method, path, token=token)
        response = decode_json(f"{method} {path}", status, content_type, body)
        if response.get("ok") is not True:
            fail(f"{method} {path} returned ok=false")
        checks.append(f"{method} {path}")

    print("Hosted API checks passed:")
    for check in checks:
        print(f"- {check}")


if __name__ == "__main__":
    main()
