"""Run the whole sandbox: simulate a month, run the health check, score it, write the reports.

    python run.py              # uses data/ (simulates it if missing)
    python run.py --simulate   # regenerate the simulated month first
"""
from __future__ import annotations

import argparse
import csv
import shutil
from pathlib import Path

from sandbox import evaluate, healthcheck as hc
from sandbox.simulate import DATA, simulate

ROOT = Path(__file__).resolve().parent
OUT, DOCS, README = ROOT / "outputs", ROOT / "docs", ROOT / "README.md"
START, END = "<!-- RESULTS:START -->", "<!-- RESULTS:END -->"


def write(path: Path, rows: list[dict]):
    with path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)


def pct(v):
    return "n/a" if v is None else f"{v:.0%}"


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--simulate", action="store_true")
    args = ap.parse_args(argv)
    if args.simulate or not (DATA / "store_orders.csv").exists():
        simulate()

    orders = hc.read_csv(DATA / "store_orders.csv")
    conversions = hc.read_csv(DATA / "platform_conversions.csv")
    errors = hc.read_csv(DATA / "platform_api_errors.csv")
    planted = hc.read_csv(DATA / "planted_issues.csv")

    findings = hc.check(orders, conversions, errors)
    summary = hc.summarize(orders, findings)
    score = hc.score(findings, planted)
    ts = evaluate.table(evaluate.run(False))

    OUT.mkdir(exist_ok=True)
    write(OUT / "health_findings.csv", findings)
    write(OUT / "health_summary.csv", summary)
    write(OUT / "detection_scorecard.csv", score)
    write(OUT / "troubleshoot_scorecard.csv", ts)
    report = hc.client_summary(orders, summary)
    (OUT / "health_report.md").write_text(report)

    # The browser demo reads the same rules and the same sample month.
    sample = DOCS / "sample"
    sample.mkdir(parents=True, exist_ok=True)
    for name in ("store_orders.csv", "platform_conversions.csv", "platform_api_errors.csv"):
        shutil.copy(DATA / name, sample / name)
    for name in ("contract.json", "playbook.json", "rules.json"):
        shutil.copy(ROOT / "sandbox" / name, DOCS / name)

    hold = {r["kind"]: r for r in ts if r["set"] == "holdout"}
    block = "\n".join([
        report.split("\n", 2)[2].strip(),
        "",
        "**How well the health check finds the planted problems**",
        "",
        "| Problem | Planted | Flagged | Correct | Precision | Recall |",
        "| --- | ---: | ---: | ---: | ---: | ---: |",
        *[f"| {r['title']} | {r['planted']} | {r['flagged']} | {r['correct']} | {pct(r['precision'])} | {pct(r['recall'])} |"
          for r in score],
        "",
        "**How well the troubleshooter's rules diagnose messages they were never tuned on**",
        "",
        "| Input | Messages | Correct |",
        "| --- | ---: | ---: |",
        *[f"| {k.capitalize()} | {v['cases']} | {v['correct']} |" for k, v in hold.items()],
    ])
    text = README.read_text()
    if START in text and END in text:
        head, rest = text.split(START, 1)
        README.write_text(f"{head}{START}\n{block}\n{END}{rest.split(END, 1)[1]}")
    over = sum(r["commission_impact_usd"] for r in summary if r["commission_impact_usd"] > 0)
    print(f"{len(orders):,} orders checked, {len(findings):,} findings in {len(summary)} issue types, "
          f"${over:,.0f} commission overpaid if left unfixed")


if __name__ == "__main__":
    main()
