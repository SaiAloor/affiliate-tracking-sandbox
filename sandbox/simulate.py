"""Simulate one month of affiliate traffic for a fictional store, Juniper Supply Co.

Every order goes through the real platform logic in platform.py, the same
code the REST API uses. The store's integration has the mistakes new clients
often make, and each one is recorded in data/planted_issues.csv so the health
check can be scored on how many it finds.

Planted problems
    click_id_lost        a mobile redirect drops the clickid parameter
    conversion_missing   browser-only tag, blocked for some shoppers
    amount_in_cents      a store release on Oct 27 starts sending cents
    currency_mismatch    the Canadian store's orders are sent as USD
    refund_not_reversed  refunds are never reported
    duplicate_fire       the tag fires again when the confirmation page reloads
    test_order           internal test orders use the live tag
    validation_error     free orders (100% coupon) are sent with amount 0
    late_conversion      a few shoppers buy more than 30 days after clicking

Outputs in data/: store_orders.csv, platform_conversions.csv, platform_api_errors.csv,
planted_issues.csv.
"""
from __future__ import annotations

import csv
import random
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .platform import Platform

SEED = 7
DATA = Path(__file__).resolve().parent.parent / "data"
START = datetime(2026, 10, 1, tzinfo=timezone.utc)
DAYS = 31
CENTS_RELEASE = datetime(2026, 10, 27, tzinfo=timezone.utc)
PARTNERS = ["coupon-hub", "trail-blog", "gearlist", "deals-daily", "hike-and-seek", "the-outdoor-edit",
            "cashback-club", "summit-stories", "campfire-reviews", "packlight", "ridge-runner", "wild-weekend"]


def write_csv(path: Path, rows: list[dict], fields: list[str]):
    with path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


def simulate(out: Path = DATA, seed: int = SEED) -> dict:
    rng = random.Random(seed)
    platform = Platform(":memory:", seed=seed)
    orders, planted = [], []
    n = 0

    def plant(order_id, issue):
        planted.append({"order_id": order_id, "issue": issue})

    for day in range(DAYS):
        for _ in range(rng.randint(40, 56)):
            n += 1
            order_id = f"JS-{10000 + n}"
            at = START + timedelta(days=day, minutes=rng.randint(0, 1439))
            store = "CA" if rng.random() < 0.15 else "US"
            currency = "CAD" if store == "CA" else "USD"
            amount = round(rng.lognormvariate(4.3, 0.55), 2)
            device = "mobile" if rng.random() < 0.45 else "desktop"
            partner_traffic = rng.random() < 0.8
            is_test = rng.random() < 0.004
            free = partner_traffic and rng.random() < 0.004
            if free:
                amount = 0.0
            refunded = round(amount * rng.choice([1, 1, 0.5]), 2) if (amount and rng.random() < 0.06) else 0.0

            click_id = ""
            sent = None
            if partner_traffic:
                late = rng.random() < 0.006
                clicked_at = at - (timedelta(days=rng.randint(31, 45)) if late else timedelta(hours=rng.randint(0, 240)))
                click = platform.create_click(rng.choice(PARTNERS), at=clicked_at)
                # Mobile shoppers are redirected to /m/ pages; the redirect drops clickid for some landing pages.
                lost = device == "mobile" and rng.random() < 0.14
                click_id = "" if lost else click
                if lost:
                    plant(order_id, "click_id_lost")

                if click_id:
                    blocked = rng.random() < 0.08  # ad blocker or closed tab: the browser tag never fires
                    if blocked:
                        plant(order_id, "conversion_missing")
                    else:
                        sent_amount = amount * 100 if at >= CENTS_RELEASE else amount
                        sent = {"click_id": click_id, "order_id": order_id,
                                "amount": f"{sent_amount:.0f}" if at >= CENTS_RELEASE else f"{sent_amount:.2f}",
                                "currency": "USD"}  # hard-coded in the tag
                        status, _ = platform.record_conversion(sent, method="tag", at=at + timedelta(seconds=3))
                        if free:
                            plant(order_id, "validation_error")
                        elif status == 422:
                            plant(order_id, "late_conversion")
                        if status == 201:
                            if at >= CENTS_RELEASE:
                                plant(order_id, "amount_in_cents")
                            if currency != "USD":
                                plant(order_id, "currency_mismatch")
                            if is_test:
                                plant(order_id, "test_order")
                        if rng.random() < 0.04:  # shopper reloads the confirmation page
                            platform.record_conversion(sent, method="tag", at=at + timedelta(minutes=rng.randint(1, 20)))
                            if status == 201:
                                plant(order_id, "duplicate_fire")
                        if refunded and status == 201:
                            plant(order_id, "refund_not_reversed")  # the store never calls the reversal endpoint

            orders.append({
                "order_id": order_id, "order_time": at.isoformat(), "store": store, "currency": currency,
                "amount": f"{amount:.2f}", "partner_traffic": "yes" if partner_traffic else "no",
                "click_id": click_id, "device": device, "refunded_amount": f"{refunded:.2f}",
                "is_test": "yes" if is_test else "no",
            })

    out.mkdir(exist_ok=True)
    conversions, errors = platform.conversions(), platform.api_errors()
    write_csv(out / "store_orders.csv", orders, list(orders[0]))
    write_csv(out / "platform_conversions.csv", conversions,
              ["order_id", "click_id", "partner_id", "amount", "currency", "method", "environment", "status",
               "refunded", "received_at", "duplicates"])
    write_csv(out / "platform_api_errors.csv", errors, ["received_at", "method", "order_id", "click_id", "code", "field"])
    write_csv(out / "planted_issues.csv", planted, ["order_id", "issue"])
    return {"orders": orders, "conversions": conversions, "errors": errors, "planted": planted}


if __name__ == "__main__":
    r = simulate()
    from collections import Counter
    print(f"{len(r['orders'])} orders, {len(r['conversions'])} conversions, {len(r['errors'])} rejected requests")
    print(Counter(p["issue"] for p in r["planted"]))
