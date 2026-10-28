#!/usr/bin/env python3
"""Private v2rayN subscription endpoint for verified IP-project nodes.

Never commit nodes.json or the access token. Supply both at runtime.
"""
from __future__ import annotations

import base64
import hmac
import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

ROLES = ("tcp", "udp", "home")
SUPPORTED = {
    "tcp": ("vless://", "trojan://"),
    "udp": ("hysteria2://", "hy2://", "hysteria2+realm://"),
    "home": ("vless://", "trojan://"),
}
MAX_BYTES = 32768


def load_subscription(path: Path, allow_partial: bool = False) -> bytes:
    """Publish verified roles only; optionally allow an incomplete verified set."""
    if not path.is_file() or path.stat().st_size > MAX_BYTES:
        raise ValueError("Missing or oversized private node manifest")
    manifest = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(manifest, dict):
        raise ValueError("Invalid private node manifest")
    links = []
    for role in ROLES:
        row = manifest.get(role)
        if not isinstance(row, dict) or row.get("verified") is not True:
            if allow_partial:
                continue
            raise ValueError("Role %s is not verified" % role)
        link = row.get("uri")
        if (
            not isinstance(link, str)
            or len(link) > 4096
            or not link.startswith(SUPPORTED[role])
            or "\n" in link
            or "\r" in link
        ):
            raise ValueError("Role %s has an invalid URI" % role)
        links.append(link)
    if not links:
        raise ValueError("No verified nodes")
    if len(set(links)) != len(links):
        raise ValueError("Duplicated node links")
    return base64.b64encode(("\n".join(links) + "\n").encode("utf-8"))


def serve() -> None:
    token = os.environ.get("IP_SUBSCRIPTION_TOKEN", "")
    source = os.environ.get("IP_PRIVATE_NODES_FILE", "")
    host = os.environ.get("IP_SUBSCRIPTION_BIND", "127.0.0.1")
    port = int(os.environ.get("IP_SUBSCRIPTION_PORT", "18090"))
    allow_partial = os.environ.get("IP_SUBSCRIPTION_ALLOW_PARTIAL") == "1"
    if len(token) < 32 or not source:
        raise SystemExit("Required private token and manifest path are missing")

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):
            # Never leak the bearer token from subscription paths to logs.
            pass

        def do_GET(self):
            path = urlsplit(self.path).path
            if not hmac.compare_digest(path, "/s/" + token):
                self.send_error(404)
                return
            try:
                data = load_subscription(Path(source), allow_partial=allow_partial)
            except (ValueError, OSError, json.JSONDecodeError):
                self.send_response(503)
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(b"Subscription not ready: verified nodes required")
                return
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

    server = ThreadingHTTPServer((host, port), Handler)
    print("Subscription service listening on %s:%d" % (host, port))
    server.serve_forever()


if __name__ == "__main__":
    serve()
