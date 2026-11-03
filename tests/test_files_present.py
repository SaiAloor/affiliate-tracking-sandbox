"""Check that every file the project needs was uploaded, and say exactly which ones are missing.

GitHub's web uploader sometimes skips folders. If this test fails, upload the files it lists
into the folders shown, then run the workflow again.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

REQUIRED = [
    "README.md", "LICENSE", "requirements.txt", "run.py", ".github/workflows/tests.yml",
    "sandbox/__init__.py", "sandbox/platform.py", "sandbox/server.py", "sandbox/simulate.py",
    "sandbox/healthcheck.py", "sandbox/troubleshoot.py", "sandbox/evaluate.py",
    "sandbox/contract.json", "sandbox/playbook.json", "sandbox/rules.json",
    "data/store_orders.csv", "data/platform_conversions.csv", "data/platform_api_errors.csv", "data/planted_issues.csv",
    "docs/index.html", "docs/guide.html", "docs/engine.js",
    "docs/contract.json", "docs/playbook.json", "docs/rules.json",
    "docs/sample/store_orders.csv", "docs/sample/platform_conversions.csv", "docs/sample/platform_api_errors.csv",
    "tests/__init__.py", "tests/parity.js", "tests/troubleshoot_cases.json", "tests/troubleshoot_holdout.json",
]


def test_every_file_was_uploaded():
    missing = [p for p in REQUIRED if not (ROOT / p).is_file()]
    assert not missing, "These files are missing from the repository. Upload them to the folders shown:\n  " + "\n  ".join(missing)


def test_demo_uses_the_same_rules_as_python():
    for name in ("contract.json", "playbook.json", "rules.json"):
        a, b = ROOT / "sandbox" / name, ROOT / "docs" / name
        if a.is_file() and b.is_file():
            assert a.read_text() == b.read_text(), f"docs/{name} differs from sandbox/{name}. Run python run.py and upload docs/{name} again."
