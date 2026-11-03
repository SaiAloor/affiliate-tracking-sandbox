"""The browser demo (docs/engine.js) must give the same answers as the Python code."""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from sandbox import healthcheck as hc
from sandbox.simulate import simulate
from sandbox.troubleshoot import diagnose

ROOT = Path(__file__).resolve().parent.parent
NODE = shutil.which("node")


@pytest.fixture(scope="module")
def both(tmp_path_factory):
    if not NODE:
        pytest.skip("Node.js is not installed")
    d = tmp_path_factory.mktemp("data")
    simulate(out=d)
    js = json.loads(subprocess.run([NODE, str(ROOT / "tests" / "parity.js"), str(d)],
                                   capture_output=True, text=True, check=True).stdout)
    orders = hc.read_csv(d / "store_orders.csv")
    findings = hc.check(orders, hc.read_csv(d / "platform_conversions.csv"), hc.read_csv(d / "platform_api_errors.csv"))
    return js, orders, findings


def test_same_findings(both):
    js, _, findings = both
    py = [[f["order_id"], f["issue"], f["commission_impact_usd"]] for f in findings]
    assert len(js["findings"]) == len(py)
    for a, b in zip(js["findings"], py):
        assert a[:2] == b[:2]
        assert a[2] == pytest.approx(b[2], abs=0.011)


def test_same_summary(both):
    js, orders, findings = both
    py = [[s["issue"], s["orders"], s["commission_impact_usd"]] for s in hc.summarize(orders, findings)]
    assert [s[:2] for s in js["summary"]] == [s[:2] for s in py]


def test_same_diagnoses(both):
    js, _, _ = both
    cases = json.loads((ROOT / "tests/troubleshoot_cases.json").read_text()) + \
        json.loads((ROOT / "tests/troubleshoot_holdout.json").read_text())
    py = [(diagnose(c["text"]) or [{"issue": None}])[0]["issue"] for c in cases]
    assert js["diagnoses"] == py
