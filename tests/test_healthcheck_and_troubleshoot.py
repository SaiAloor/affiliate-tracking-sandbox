import io
import json
from pathlib import Path

import pytest

from sandbox import healthcheck as hc
from sandbox import troubleshoot as ts
from sandbox.simulate import simulate

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(scope="module")
def month(tmp_path_factory):
    d = tmp_path_factory.mktemp("data")
    simulate(out=d)
    rd = lambda n: hc.read_csv(d / n)  # noqa: E731
    return rd("store_orders.csv"), rd("platform_conversions.csv"), rd("platform_api_errors.csv"), rd("planted_issues.csv")


def test_health_check_finds_every_planted_problem(month):
    orders, conv, errs, planted = month
    for row in hc.score(hc.check(orders, conv, errs), planted):
        assert row["recall"] >= 0.95 and row["precision"] >= 0.95, row


def test_cents_overpayment_is_the_biggest_issue(month):
    orders, conv, errs, _ = month
    top = hc.summarize(orders, hc.check(orders, conv, errs))[0]
    assert top["issue"] == "amount_in_cents" and top["commission_impact_usd"] > 0


def test_informational_issues_need_no_fix(month):
    orders, conv, errs, _ = month
    rows = {r["issue"]: r for r in hc.summarize(orders, hc.check(orders, conv, errs))}
    assert rows["duplicate_fire"]["needs_fix"] is False and rows["amount_in_cents"]["needs_fix"] is True


def load(name):
    return json.loads((ROOT / "tests" / name).read_text())


def test_rules_on_tuning_messages():
    assert all(ts.diagnose(c["text"])[0]["issue"] == c["expect"] for c in load("troubleshoot_cases.json"))


def test_rules_on_unseen_structured_input():
    held = [c for c in load("troubleshoot_holdout.json") if ts.is_structured(c["text"])]
    assert held and all(ts.diagnose(c["text"])[0]["issue"] == c["expect"] for c in held)


class FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def fake_claude(answer):
    def urlopen(req, timeout=60):
        body = json.loads(req.data)
        assert "known integration issue" in body["messages"][0]["content"] or "Diagnosis" in body["messages"][0]["content"]
        return FakeResponse(json.dumps({"content": [{"text": answer}]}).encode())
    return urlopen


def test_ai_mode_uses_claude_for_plain_text(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    monkeypatch.setattr(ts.urllib.request, "urlopen", fake_claude('{"issue": "refund_not_reversed", "evidence": "sends something back"}'))
    out = ts.diagnose_hybrid("If a customer sends something back, what do we call?", use_ai=True)
    assert out[0]["issue"] == "refund_not_reversed"


def test_ai_mode_cannot_invent_a_cause(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    monkeypatch.setattr(ts.urllib.request, "urlopen", fake_claude('{"issue": "server_on_fire"}'))
    assert ts.ai_classify("anything") is None


def test_structured_input_never_goes_to_ai(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("structured input should use the rules")
    monkeypatch.setattr(ts.urllib.request, "urlopen", boom)
    out = ts.diagnose_hybrid('{"error": {"code": "click_expired"}}', use_ai=True)
    assert out[0]["issue"] == "late_conversion"
