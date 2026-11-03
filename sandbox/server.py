"""REST API for the Sandbox Partner Platform, using only the Python standard library.

    python -m sandbox.server            # http://localhost:8008

Endpoints
    GET  /r/{partner_id}?to=<url>          partner link: records a click, redirects with ?clickid=
    POST /v1/conversions                   server-to-server conversion (needs Authorization: Bearer <key>)
    GET  /v1/tag.gif?click_id=&order_id=&amount=&currency=   browser tag, returns a 1x1 image
    POST /v1/conversions/{order_id}/reversal   report a full or partial refund
    GET  /v1/conversions                   list recorded conversions
    GET  /v1/errors                        list rejected requests
    GET  /health                           liveness check
"""
from __future__ import annotations

import base64
import json
import re
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlencode, urlparse, urlsplit, urlunsplit

from .platform import LIVE_KEY, TEST_KEY, Platform

PIXEL = base64.b64decode("R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7")
platform: Platform | None = None  # set in main(), or by tests


def add_param(url: str, key: str, value: str) -> str:
    parts = urlsplit(url)
    query = parse_qs(parts.query)
    query[key] = [value]
    return urlunsplit(parts._replace(query=urlencode(query, doseq=True)))


class Handler(BaseHTTPRequestHandler):
    server_version = "SandboxPartnerPlatform/1.0"

    def _send(self, status: int, body: dict | bytes, content_type: str = "application/json", headers=None):
        data = body if isinstance(body, bytes) else json.dumps(body, indent=2).encode()
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Access-Control-Allow-Origin", "*")
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(data)

    def _key(self) -> str | None:
        auth = self.headers.get("Authorization", "")
        return auth[7:].strip() if auth.lower().startswith("bearer ") else None

    def do_OPTIONS(self):  # browser preflight for the demo store
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Authorization, Content-Type")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.end_headers()

    def do_GET(self):
        url = urlparse(self.path)
        q = {k: v[0] for k, v in parse_qs(url.query).items()}
        if url.path == "/health":
            return self._send(200, {"status": "ok"})
        m = re.fullmatch(r"/r/([\w-]+)", url.path)
        if m:
            click_id = platform.create_click(m.group(1))
            target = add_param(q.get("to", "http://localhost:8000/store.html"), "clickid", click_id)
            return self._send(302, {"redirect": target}, headers={"Location": target})
        if url.path == "/v1/tag.gif":
            status, body = platform.record_conversion(q, method="tag")
            # A tag must always return an image, even on error; the result is in a header for debugging.
            return self._send(200, PIXEL, "image/gif", {"X-Conversion-Result": json.dumps(body)})
        if url.path == "/v1/conversions":
            return self._send(200, {"conversions": platform.conversions()})
        if url.path == "/v1/errors":
            return self._send(200, {"errors": platform.api_errors()})
        return self._send(404, {"error": {"code": "not_found", "message": f"No endpoint at {url.path}"}})

    def do_POST(self):
        url = urlparse(self.path)
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b""
        try:
            payload = json.loads(raw or b"{}")
            if not isinstance(payload, dict):
                raise ValueError
        except ValueError:
            return self._send(400, {"error": {"code": "invalid_json",
                                              "message": "The request body isn't valid JSON. Check for a missing quote, comma or bracket.",
                                              "docs": "guide.html#server-to-server"}})
        if url.path == "/v1/conversions":
            return self._send(*platform.record_conversion(payload, method="s2s", api_key=self._key()))
        m = re.fullmatch(r"/v1/conversions/([\w.-]+)/reversal", url.path)
        if m:
            if self._key() not in (LIVE_KEY, TEST_KEY):
                return self._send(401, {"error": {"code": "invalid_api_key", "message": "A valid API key is required."}})
            return self._send(*platform.reverse(m.group(1), payload.get("amount")))
        return self._send(404, {"error": {"code": "not_found", "message": f"No endpoint at {url.path}"}})

    def log_message(self, fmt, *args):
        sys.stderr.write("%s %s\n" % (self.command, fmt % args))


def main(port: int = 8008, db: str = "sandbox.db"):
    global platform
    platform = Platform(db)
    print(f"Sandbox Partner Platform API on http://localhost:{port}  (live key: {LIVE_KEY}, test key: {TEST_KEY})")
    ThreadingHTTPServer(("", port), Handler).serve_forever()


if __name__ == "__main__":
    main(int(sys.argv[1]) if len(sys.argv) > 1 else 8008)
