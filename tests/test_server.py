"""Start the real HTTP server on a free port and call it like a client would."""
import json
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

import pytest

from sandbox import server
from sandbox.platform import LIVE_KEY, Platform


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None


@pytest.fixture(scope="module")
def base():
    server.platform = Platform(seed=3)
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.shutdown()


def call(url, data=None, key=None, method=None):
    headers = {"Content-Type": "application/json"}
    if key:
        headers["Authorization"] = f"Bearer {key}"
    req = urllib.request.Request(url, data=data if data is None or isinstance(data, bytes) else json.dumps(data).encode(),
                                 headers=headers, method=method)
    try:
        with urllib.request.urlopen(req) as r:
            return r.status, r.headers, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.headers, e.read()


def click(base):
    opener = urllib.request.build_opener(NoRedirect)
    try:
        opener.open(f"{base}/r/trail-blog?to=https://store.example/daypack?color=green")
    except urllib.error.HTTPError as e:
        assert e.code == 302
        return e.headers["Location"]


def test_partner_link_redirects_with_click_id(base):
    loc = click(base)
    assert loc.startswith("https://store.example/daypack?") and "color=green" in loc and "clickid=clk_" in loc


def test_full_sale_duplicate_and_refund(base):
    cid = click(base).split("clickid=")[1]
    sale = {"click_id": cid, "order_id": "S-1", "amount": 84.5, "currency": "USD"}
    assert call(f"{base}/v1/conversions", sale, LIVE_KEY)[0] == 201
    status, _, body = call(f"{base}/v1/conversions", sale, LIVE_KEY)
    assert status == 200 and json.loads(body)["status"] == "duplicate"
    status, _, body = call(f"{base}/v1/conversions/S-1/reversal", {"amount": 84.5}, LIVE_KEY)
    assert status == 200 and json.loads(body)["status"] == "reversed"


def test_errors_are_clear(base):
    assert json.loads(call(f"{base}/v1/conversions", {"order_id": "x"})[2])["error"]["code"] == "missing_api_key"
    status, _, body = call(f"{base}/v1/conversions", b"{not json", LIVE_KEY)
    assert status == 400 and json.loads(body)["error"]["code"] == "invalid_json"


def test_tag_always_returns_an_image(base):
    status, headers, body = call(f"{base}/v1/tag.gif?click_id=bad&order_id=T-1&amount=10&currency=USD")
    assert status == 200 and headers["Content-Type"] == "image/gif" and body[:3] == b"GIF"
    assert json.loads(headers["X-Conversion-Result"])["error"]["code"] == "invalid_click_id"
