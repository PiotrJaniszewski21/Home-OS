import ipaddress
import re
import socket
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit


DEFAULT_HOST = "HomeOS.local"
LOCAL_HOSTS = {DEFAULT_HOST.casefold()}


def redirect_host(host_header):
    candidate = (host_header or "").strip()
    try:
        parsed = urlsplit(f"//{candidate}")
        hostname = parsed.hostname if parsed.username is None and parsed.password is None else None
    except ValueError:
        hostname = None

    if not hostname:
        return DEFAULT_HOST

    try:
        address = ipaddress.ip_address(hostname)
        if not (address.is_private or address.is_loopback or address.is_link_local):
            return DEFAULT_HOST
        return f"[{address.compressed}]" if address.version == 6 else address.compressed
    except ValueError:
        pass

    normalized = hostname.casefold().rstrip(".")
    return normalized if normalized in LOCAL_HOSTS else DEFAULT_HOST


def redirect_location(host_header, path):
    safe_path = path if path.startswith("/") else "/"
    return f"https://{redirect_host(host_header)}{safe_path}"


class RedirectHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def redirect(self):
        self.send_response(308)
        self.send_header("Location", redirect_location(self.headers.get("Host"), self.path))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", "0")
        self.end_headers()

    do_CONNECT = redirect
    do_DELETE = redirect
    do_GET = redirect
    do_HEAD = redirect
    do_OPTIONS = redirect
    do_PATCH = redirect
    do_POST = redirect
    do_PUT = redirect


class DualStackHTTPServer(ThreadingHTTPServer):
    address_family = socket.AF_INET6

    def server_bind(self):
        self.socket.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 0)
        super().server_bind()


def main():
    server = DualStackHTTPServer(("::", 80), RedirectHandler)
    server.serve_forever()


if __name__ == "__main__":
    main()
