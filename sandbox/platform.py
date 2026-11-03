"""The fictional Sandbox Partner Platform: clicks, conversions and refunds.

This is the logic behind the REST API in server.py, kept separate so tests
and the simulator can call it directly. Data lives in SQLite.

A conversion goes through the same steps a real affiliate platform uses:
check the API key, validate the fields, find the click, check the lookback
window, drop duplicates by order_id, then record the sale.
"""
from __future__ import annotations

import json
import random
import re
import sqlite3
import string
from datetime import datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
CONTRACT = json.loads((HERE / "contract.json").read_text())
CLICK_RE = re.compile(CONTRACT["click_id_pattern"])

LIVE_KEY = "live_demo_9f2c41"
TEST_KEY = "test_demo_51ab07"


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def error(code: str, field: str | None = None) -> tuple[int, dict]:
    spec = CONTRACT["errors"][code]
    message = spec["message"].replace("{field}", field or "")
    body = {"error": {"code": code, "message": message, "docs": "guide.html" + spec["docs"]}}
    if field:
        body["error"]["field"] = field
    return spec["status"], body


def validate(payload: dict) -> tuple[str, str | None] | None:
    """Return (error_code, field) for the first problem, or None if the payload is valid."""
    for field in CONTRACT["required"]:
        if payload.get(field) in (None, ""):
            return "missing_field", field
    if not CLICK_RE.match(str(payload["click_id"])):
        return "invalid_click_id", "click_id"
    try:
        amount = float(payload["amount"])
    except (TypeError, ValueError):
        return "invalid_amount", "amount"
    if amount <= 0:
        return "invalid_amount", "amount"
    if amount > CONTRACT["max_amount"]:
        return "amount_too_large", "amount"
    if str(payload["currency"]).upper() not in CONTRACT["currencies"]:
        return "invalid_currency", "currency"
    return None


class Platform:
    def __init__(self, db: str | Path = ":memory:", seed: int | None = None):
        self.db = sqlite3.connect(str(db), check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.rng = random.Random(seed)
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS clicks (
                click_id TEXT PRIMARY KEY, partner_id TEXT NOT NULL, clicked_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS conversions (
                order_id TEXT PRIMARY KEY, click_id TEXT, partner_id TEXT, amount REAL, currency TEXT,
                method TEXT, environment TEXT, status TEXT, refunded REAL DEFAULT 0,
                received_at TEXT, duplicates INTEGER DEFAULT 0);
            CREATE TABLE IF NOT EXISTS api_errors (
                received_at TEXT, method TEXT, order_id TEXT, click_id TEXT, code TEXT, field TEXT, body TEXT);
        """)

    # ---- clicks -------------------------------------------------------------
    def create_click(self, partner_id: str, at: datetime | None = None) -> str:
        click_id = "clk_" + "".join(self.rng.choices(string.ascii_lowercase + string.digits, k=10))
        self.db.execute("INSERT INTO clicks VALUES (?, ?, ?)", (click_id, partner_id, (at or now_utc()).isoformat()))
        self.db.commit()
        return click_id

    # ---- conversions --------------------------------------------------------
    def _log_error(self, at, method, payload, code, field):
        self.db.execute("INSERT INTO api_errors VALUES (?, ?, ?, ?, ?, ?, ?)",
                        (at.isoformat(), method, str(payload.get("order_id") or ""), str(payload.get("click_id") or ""),
                         code, field or "", json.dumps(payload)))
        self.db.commit()

    def record_conversion(self, payload: dict, method: str = "s2s", api_key: str | None = None,
                          at: datetime | None = None) -> tuple[int, dict]:
        """Validate and store one conversion. Returns (HTTP status, JSON body)."""
        at = at or now_utc()
        if method == "s2s":
            if not api_key:
                self._log_error(at, method, payload, "missing_api_key", None)
                return error("missing_api_key")
            if api_key not in (LIVE_KEY, TEST_KEY):
                self._log_error(at, method, payload, "invalid_api_key", None)
                return error("invalid_api_key")
        environment = "test" if api_key == TEST_KEY else "production"

        problem = validate(payload)
        if problem:
            self._log_error(at, method, payload, *problem)
            return error(*problem)

        click = self.db.execute("SELECT * FROM clicks WHERE click_id = ?", (payload["click_id"],)).fetchone()
        if click is None:
            self._log_error(at, method, payload, "unknown_click", "click_id")
            return error("unknown_click", "click_id")
        clicked = datetime.fromisoformat(click["clicked_at"])
        if at - clicked > timedelta(days=CONTRACT["lookback_days"]):
            self._log_error(at, method, payload, "click_expired", "click_id")
            return error("click_expired", "click_id")

        order_id = str(payload["order_id"])
        existing = self.db.execute("SELECT * FROM conversions WHERE order_id = ?", (order_id,)).fetchone()
        if existing:
            self.db.execute("UPDATE conversions SET duplicates = duplicates + 1 WHERE order_id = ?", (order_id,))
            self.db.commit()
            return 200, {"status": "duplicate", "order_id": order_id,
                         "message": "This order_id was already recorded, so it was not counted again."}

        self.db.execute(
            "INSERT INTO conversions (order_id, click_id, partner_id, amount, currency, method, environment, status,"
            " received_at) VALUES (?, ?, ?, ?, ?, ?, ?, 'approved', ?)",
            (order_id, payload["click_id"], click["partner_id"], round(float(payload["amount"]), 2),
             str(payload["currency"]).upper(), method, environment, at.isoformat()))
        self.db.commit()
        return 201, {"status": "approved", "order_id": order_id, "partner_id": click["partner_id"],
                     "environment": environment}

    def reverse(self, order_id: str, amount: float | None = None, at: datetime | None = None) -> tuple[int, dict]:
        row = self.db.execute("SELECT * FROM conversions WHERE order_id = ?", (order_id,)).fetchone()
        if row is None:
            return error("order_not_found", "order_id")
        amount = row["amount"] - row["refunded"] if amount is None else float(amount)
        if row["refunded"] + amount > row["amount"] + 0.005:
            return error("reversal_too_large", "amount")
        refunded = round(row["refunded"] + amount, 2)
        status = "reversed" if refunded >= row["amount"] - 0.005 else "partly_reversed"
        self.db.execute("UPDATE conversions SET refunded = ?, status = ? WHERE order_id = ?", (refunded, status, order_id))
        self.db.commit()
        return 200, {"status": status, "order_id": order_id, "refunded": refunded}

    # ---- exports ------------------------------------------------------------
    def conversions(self) -> list[dict]:
        return [dict(r) for r in self.db.execute("SELECT * FROM conversions ORDER BY received_at")]

    def api_errors(self) -> list[dict]:
        return [dict(r) for r in self.db.execute("SELECT * FROM api_errors ORDER BY received_at")]
