"""Integration health check: compare the store's orders with what the platform received.

For every order it asks the questions an Integration Engineer would ask
during onboarding: did partner traffic keep its click ID, did the sale reach
the platform, does the amount match, is the currency right, was a refund
reported, did the tag fire twice, was it a test order, and was the request
rejected? Each finding is tied to an entry in playbook.json, which explains
the cause and the fix.

Money is shown as commission impact in US dollars: a positive number means
partners would be overpaid, a negative number means they'd be underpaid.
"""
from __future__ import annotations

import csv
import json
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
PLAYBOOK = json.loads((HERE / "playbook.json").read_text())["issues"]

COMMISSION_RATE = 0.08            # program setting: partners earn 8% of the sale
TO_USD = {"USD": 1.0, "CAD": 0.73, "EUR": 1.08, "GBP": 1.27, "AUD": 0.66}  # fixed rates for the demo
AUTH_CODES = {"missing_api_key", "invalid_api_key"}
# Findings that need no fix: duplicates are already dropped by the platform, and
# late sales are rejected by design.
INFORMATIONAL = {
    "duplicate_fire": "Already handled: the platform dropped the extra copies. Optional cleanup: fire the tag once per order.",
    "late_conversion": "Working as designed: these sales fell outside the 30-day lookback window.",
}


def read_csv(path: Path) -> list[dict]:
    with Path(path).open(newline="") as f:
        return list(csv.DictReader(f))


def _f(v) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def check(orders: list[dict], conversions: list[dict], api_errors: list[dict]) -> list[dict]:
    """One row per (order, issue) found."""
    conv = {c["order_id"]: c for c in conversions}
    errs = defaultdict(list)
    for e in api_errors:
        errs[e["order_id"]].append(e["code"])

    findings = []

    def add(o, issue, impact, detail):
        findings.append({"order_id": o["order_id"], "issue": issue, "title": PLAYBOOK[issue]["title"],
                         "commission_impact_usd": round(impact, 2), "detail": detail})

    for o in orders:
        amount, fx = _f(o["amount"]), TO_USD.get(o["currency"], 1.0)
        fair = amount * fx * COMMISSION_RATE
        c = conv.get(o["order_id"])
        if o["partner_traffic"] == "yes" and not o["click_id"]:
            add(o, "click_id_lost", -fair, "partner traffic, but the order has no click ID")
            continue
        if not o["click_id"]:
            continue
        if c is None:
            codes = errs.get(o["order_id"], [])
            if not codes:
                add(o, "conversion_missing", -fair, "order has a click ID but never reached the platform")
            elif "click_expired" in codes:
                add(o, "late_conversion", 0.0, "rejected: click older than the lookback window")
            elif AUTH_CODES & set(codes):
                add(o, "auth_error", -fair, f"rejected: {codes[0]}")
            else:
                add(o, "validation_error", -fair, f"rejected: {codes[0]}")
            continue

        sent = _f(c["amount"])
        if amount > 0 and 95 <= sent / amount <= 105:
            add(o, "amount_in_cents", (sent - amount) * fx * COMMISSION_RATE,
                f"store {amount:.2f}, platform {sent:.2f}")
        if c["currency"] != o["currency"]:
            over = amount * (TO_USD.get(c["currency"], 1.0) - fx) * COMMISSION_RATE
            add(o, "currency_mismatch", over, f"store {o['currency']}, platform {c['currency']}")
        refunded = _f(o["refunded_amount"])
        if refunded > 0 and _f(c["refunded"]) < refunded - 0.01:
            add(o, "refund_not_reversed", (refunded - _f(c["refunded"])) * fx * COMMISSION_RATE,
                f"refunded {refunded:.2f} in the store, {_f(c['refunded']):.2f} reported")
        if int(_f(c.get("duplicates", 0))) > 0:
            add(o, "duplicate_fire", 0.0, f"reported {int(_f(c['duplicates'])) + 1} times; extra copies were dropped")
        if o["is_test"] == "yes" and c.get("environment") == "production":
            add(o, "test_order", sent * fx * COMMISSION_RATE, "store marks this as a test order")
    return findings


def summarize(orders: list[dict], findings: list[dict]) -> list[dict]:
    amount = {o["order_id"]: _f(o["amount"]) * TO_USD.get(o["currency"], 1.0) for o in orders}
    g = defaultdict(lambda: {"orders": 0, "sales_usd": 0.0, "impact": 0.0})
    for f in findings:
        s = g[f["issue"]]
        s["orders"] += 1
        s["sales_usd"] += amount.get(f["order_id"], 0.0)
        s["impact"] += f["commission_impact_usd"]
    rows = [{"issue": k, "title": PLAYBOOK[k]["title"], "orders": v["orders"], "sales_usd": round(v["sales_usd"], 2),
             "commission_impact_usd": round(v["impact"], 2), "needs_fix": k not in INFORMATIONAL,
             "fix": PLAYBOOK[k]["fix"]} for k, v in g.items()]
    return sorted(rows, key=lambda r: (not r["needs_fix"], -abs(r["commission_impact_usd"]), -r["orders"]))


def score(findings: list[dict], planted: list[dict]) -> list[dict]:
    found = defaultdict(set)
    truth = defaultdict(set)
    for f in findings:
        found[f["issue"]].add(f["order_id"])
    for p in planted:
        truth[p["issue"]].add(p["order_id"])
    rows = []
    for issue in sorted(set(found) | set(truth)):
        hit = len(found[issue] & truth[issue])
        rows.append({"issue": issue, "title": PLAYBOOK[issue]["title"], "planted": len(truth[issue]),
                     "flagged": len(found[issue]), "correct": hit,
                     "precision": hit / len(found[issue]) if found[issue] else None,
                     "recall": hit / len(truth[issue]) if truth[issue] else None})
    return rows


def money_signed(v: float) -> str:
    if abs(v) < 0.5:
        return "$0"
    return ("+" if v > 0 else "-") + f"${abs(v):,.0f}"


def client_summary(orders, summary, store_name="Juniper Supply Co.") -> str:
    partner = [o for o in orders if o["partner_traffic"] == "yes"]
    over = sum(r["commission_impact_usd"] for r in summary if r["commission_impact_usd"] > 0)
    under = -sum(r["commission_impact_usd"] for r in summary if r["commission_impact_usd"] < 0)
    fix = [r for r in summary if r["needs_fix"]]
    info = [r for r in summary if not r["needs_fix"]]
    lines = [
        f"# Integration health check: {store_name}",
        "",
        f"I compared {len(orders):,} store orders ({len(partner):,} from partner traffic) with what the platform received.",
        f"{len(fix)} issues need a fix. Left as is, partners would be overpaid by about ${over:,.0f} and underpaid by "
        f"about ${under:,.0f} in commission for the month."
        + (f" {len(info)} more are informational and need no change." if info else ""),
        "",
        "| Issue | Orders | Commission impact | Fix |",
        "| --- | ---: | ---: | --- |",
    ]
    for r in summary:
        fix_text = r["fix"] if r["needs_fix"] else INFORMATIONAL[r["issue"]]
        lines.append(f"| {r['title']} | {r['orders']} | {money_signed(r['commission_impact_usd'])} | {fix_text} |")
    lines += ["", "A positive impact means partners would be overpaid; negative means underpaid. "
              f"Commission is figured at {COMMISSION_RATE:.0%} of the sale, converted to US dollars."]
    return "\n".join(lines) + "\n"
