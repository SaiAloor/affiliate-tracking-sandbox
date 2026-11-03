"""Score the troubleshooter on labeled client messages.

    python -m sandbox.evaluate          # rules only, works offline
    python -m sandbox.evaluate --ai     # rules and Claude (needs ANTHROPIC_API_KEY)

Two sets: tests/troubleshoot_cases.json was used while writing the rules, so
its score is optimistic. tests/troubleshoot_holdout.json was written after the
rules were finished and never used to change them, so its score is the honest one.
"""
from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

from .troubleshoot import diagnose_hybrid, is_structured

ROOT = Path(__file__).resolve().parent.parent
SETS = {"tuning": ROOT / "tests" / "troubleshoot_cases.json", "holdout": ROOT / "tests" / "troubleshoot_holdout.json"}


def run(use_ai: bool) -> list[dict]:
    rows = []
    for name, path in SETS.items():
        for case in json.loads(path.read_text()):
            got = diagnose_hybrid(case["text"], use_ai=use_ai)
            rows.append({"set": name, "kind": "structured" if is_structured(case["text"]) else "plain text",
                         "mode": "rules + Claude" if use_ai else "rules", "expected": case["expect"],
                         "got": got[0]["issue"] if got else "", "correct": bool(got) and got[0]["issue"] == case["expect"]})
    return rows


def table(rows: list[dict]) -> list[dict]:
    out = []
    for mode in sorted({r["mode"] for r in rows}):
        for s in SETS:
            for kind in ("structured", "plain text"):
                sel = [r for r in rows if r["mode"] == mode and r["set"] == s and r["kind"] == kind]
                if sel:
                    out.append({"mode": mode, "set": s, "kind": kind, "cases": len(sel),
                                "correct": sum(r["correct"] for r in sel)})
    return out


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--ai", action="store_true")
    args = ap.parse_args(argv)
    rows = run(False) + (run(True) if args.ai else [])
    summary = table(rows)
    out = ROOT / "outputs"
    out.mkdir(exist_ok=True)
    name = "troubleshoot_scorecard_ai.csv" if args.ai else "troubleshoot_scorecard.csv"
    with (out / name).open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(summary[0]))
        w.writeheader()
        w.writerows(summary)
    for s in summary:
        print(f"{s['mode']:14} {s['set']:8} {s['kind']:11} {s['correct']:>3}/{s['cases']}")


if __name__ == "__main__":
    main()
