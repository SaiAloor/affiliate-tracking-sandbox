from datetime import datetime, timedelta, timezone

import pytest

from sandbox.platform import LIVE_KEY, TEST_KEY, Platform, validate

T0 = datetime(2026, 10, 1, tzinfo=timezone.utc)


@pytest.fixture
def p():
    return Platform(seed=1)


def sale(click, order="A-1", amount="84.50", currency="USD"):
    return {"click_id": click, "order_id": order, "amount": amount, "currency": currency}


def test_valid_sale_is_approved_and_credited(p):
    c = p.create_click("trail-blog", at=T0)
    status, body = p.record_conversion(sale(c), api_key=LIVE_KEY, at=T0 + timedelta(hours=2))
    assert status == 201 and body["partner_id"] == "trail-blog"


def test_duplicate_order_is_not_counted_twice(p):
    c = p.create_click("x", at=T0)
    p.record_conversion(sale(c), api_key=LIVE_KEY, at=T0)
    status, body = p.record_conversion(sale(c), api_key=LIVE_KEY, at=T0)
    assert status == 200 and body["status"] == "duplicate"
    assert len(p.conversions()) == 1 and p.conversions()[0]["duplicates"] == 1


@pytest.mark.parametrize("key,code", [(None, "missing_api_key"), ("wrong", "invalid_api_key")])
def test_server_calls_need_a_valid_key(p, key, code):
    c = p.create_click("x", at=T0)
    status, body = p.record_conversion(sale(c), api_key=key, at=T0)
    assert status == 401 and body["error"]["code"] == code


def test_browser_tag_needs_no_key(p):
    c = p.create_click("x", at=T0)
    assert p.record_conversion(sale(c), method="tag", at=T0)[0] == 201


def test_test_key_is_marked_as_test(p):
    c = p.create_click("x", at=T0)
    assert p.record_conversion(sale(c), api_key=TEST_KEY, at=T0)[1]["environment"] == "test"


@pytest.mark.parametrize("payload,code", [
    ({"order_id": "A", "amount": 1, "currency": "USD"}, "missing_field"),
    ({"click_id": "abc", "order_id": "A", "amount": 1, "currency": "USD"}, "invalid_click_id"),
    ({"click_id": "clk_abcdefghij", "order_id": "A", "amount": 0, "currency": "USD"}, "invalid_amount"),
    ({"click_id": "clk_abcdefghij", "order_id": "A", "amount": 845000, "currency": "USD"}, "amount_too_large"),
    ({"click_id": "clk_abcdefghij", "order_id": "A", "amount": 10, "currency": "dollars"}, "invalid_currency"),
])
def test_validation_names_the_problem(payload, code):
    assert validate(payload)[0] == code


def test_unknown_and_expired_clicks(p):
    assert p.record_conversion(sale("clk_zzzzzzzzzz"), api_key=LIVE_KEY, at=T0)[1]["error"]["code"] == "unknown_click"
    c = p.create_click("x", at=T0)
    assert p.record_conversion(sale(c), api_key=LIVE_KEY, at=T0 + timedelta(days=31))[1]["error"]["code"] == "click_expired"


def test_rejected_requests_are_logged(p):
    p.record_conversion({"order_id": "A"}, api_key=LIVE_KEY, at=T0)
    assert p.api_errors()[0]["code"] == "missing_field"


def test_partial_then_full_refund(p):
    c = p.create_click("x", at=T0)
    p.record_conversion(sale(c, amount="100"), api_key=LIVE_KEY, at=T0)
    assert p.reverse("A-1", 40)[1]["status"] == "partly_reversed"
    assert p.reverse("A-1")[1]["status"] == "reversed"
    assert p.reverse("A-1", 1)[1]["error"]["code"] == "reversal_too_large"
    assert p.reverse("nope")[1]["error"]["code"] == "order_not_found"
