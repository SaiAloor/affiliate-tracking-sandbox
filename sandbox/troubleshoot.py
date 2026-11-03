"""Troubleshooter: paste what a client sent and get the likely cause and a reply.

It accepts three kinds of input:
1. An error response from the API, like {"error": {"code": "invalid_click_id", ...}}
2. The request the client sent, like {"click_id": "...", "amount": 8450, ...}
3. A plain description, like "our refunds still show as approved"

Error responses and requests are diagnosed by rules that use the same contract
as the platform, so the answer is exact and can be traced to a rule. Plain-English
messages are where rules break down: clients describe one problem in many ways.
With --ai and an ANTHROPIC_API_KEY, Claude sorts those messages into the
playbook's known issues (it can't pick anything outside the list) and then
tailors the reply. Without a key, everything falls back to the rules.

    python -m sandbox.troubleshoot "our refunds still show as approved"
    python -m sandbox.troubleshoot --ai < client_message.txt
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.request
from pathlib import Path

from .healthcheck import PLAYBOOK
from .platform import validate

RULES = json.loads((Path(__file__).resolve().parent / "rules.json").read_text())
ERROR_TO_ISSUE = RULES["error_to_issue"]
CLUES = [(c["issue"], c["weight"], c["pattern"]) for c in RULES["clues"]]


def _json(text: str):
    try:
        return json.loads(text)
    except ValueError:
        m = re.search(r"\{.*\}", text, re.S)  # JSON pasted inside an email or log line
        if m:
            try:
                return json.loads(m.group(0))
            except ValueError:
                return None
    return None


def diagnose(text: str) -> list[dict]:
    """Ranked list of {issue, score, reason}."""
    scores: dict[str, float] = {}
    reasons: dict[str, str] = {}

    def add(issue, w, why):
        scores[issue] = scores.get(issue, 0) + w
        reasons.setdefault(issue, why)

    data = _json(text)
    if isinstance(data, dict) and isinstance(data.get("error"), dict):
        code = data["error"].get("code", "")
        if code in ERROR_TO_ISSUE:
            add(ERROR_TO_ISSUE[code], 10, f"the API returned error code {code}")
    elif isinstance(data, dict) and {"amount", "order_id"} & set(data):
        problem = validate(data)
        if problem:
            code, field = problem
            issue = "click_id_lost" if field == "click_id" else ERROR_TO_ISSUE.get(code, "validation_error")
            add(issue, 10, f"this request would be rejected: {code} on {field}")
        else:
            amt = str(data.get("amount", ""))
            if re.fullmatch(r"\d{4,}", amt):
                add("amount_in_cents", 6, f"amount {amt} has no decimal point and looks like cents")
            if not data.get("click_id"):
                add("click_id_lost", 6, "no click_id in the request")
    if scores:  # an error code or a request decides it; field names in JSON aren't clues
        lower = ""
    else:
        lower = text.lower()
    for issue, w, pattern in CLUES:
        m = re.search(pattern, lower) if lower else None
        if m:
            add(issue, w, f'the message mentions "{m.group(0)}"')
    ranked = sorted(scores, key=lambda k: -scores[k])
    return [{"issue": k, "score": scores[k], "reason": reasons[k], **{f: PLAYBOOK[k][f] for f in
             ("title", "cause", "confirm", "fix", "reply")}} for k in ranked]


def _claude(prompt: str, max_tokens: int = 400, model: str | None = None) -> str:
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        raise RuntimeError("Set ANTHROPIC_API_KEY to use --ai.")
    body = json.dumps({"model": model or os.environ.get("CLAUDE_MODEL", "claude-sonnet-4-5"), "max_tokens": max_tokens,
                       "messages": [{"role": "user", "content": prompt}]}).encode()
    req = urllib.request.Request("https://api.anthropic.com/v1/messages", data=body, headers={
        "x-api-key": key, "anthropic-version": "2023-06-01", "content-type": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read())["content"][0]["text"]


def ai_classify(text: str) -> dict | None:
    """Ask Claude which known issue a free-text message describes.

    The model may only pick from the playbook's issue list (or "unknown"), and
    its answer is checked against that list, so it can't invent a cause.
    """
    catalog = "\n".join(f"- {k}: {v['title']}. {v['symptom']}" for k, v in PLAYBOOK.items())
    prompt = (
        "A client of an affiliate tracking platform sent the message below. Decide which known integration issue "
        "it most likely describes.\n\n"
        f"Known issues:\n{catalog}\n- unknown: none of the above fits\n\n"
        f"<message>\n{text}\n</message>\n\n"
        'Answer with JSON only: {"issue": "<one id from the list>", "evidence": "<the words in the message that point to it>"}'
    )
    raw = _claude(prompt, max_tokens=200)
    data = _json(raw)
    if not isinstance(data, dict) or data.get("issue") not in PLAYBOOK:
        return None
    return {"issue": data["issue"], "score": 5, "reason": f'Claude matched "{data.get("evidence", "")}"',
            **{f: PLAYBOOK[data["issue"]][f] for f in ("title", "cause", "confirm", "fix", "reply")}}


def is_structured(text: str) -> bool:
    data = _json(text)
    return isinstance(data, dict) and (isinstance(data.get("error"), dict) or bool({"amount", "order_id"} & set(data)))


def diagnose_hybrid(text: str, use_ai: bool = False) -> list[dict]:
    """Rules for error responses and requests (exact and testable); Claude for plain-English messages."""
    if use_ai and not is_structured(text):
        found = ai_classify(text)
        if found:
            return [found] + [r for r in diagnose(text) if r["issue"] != found["issue"]]
    return diagnose(text)


def ai_reply(client_text: str, diagnosis: dict) -> str:
    """Ask Claude to tailor the reply to the client's exact message. The diagnosis is fixed before this call."""
    prompt = (
        "You are an integration engineer at an affiliate tracking platform, replying to a client.\n"
        f"Client's message:\n<message>\n{client_text}\n</message>\n\n"
        f"Diagnosis (already decided; do not change it): {diagnosis['title']}.\n"
        f"Cause: {diagnosis['cause']}\nHow to confirm: {diagnosis['confirm']}\nFix: {diagnosis['fix']}\n\n"
        "Write a short, calm, friendly reply (under 120 words) in plain English. Acknowledge their message, "
        "explain the cause in one or two sentences, give the fix as clear steps, and offer to help test. "
        "Do not invent features, numbers or causes."
    )
    return _claude(prompt)


def main(argv=None):
    ap = argparse.ArgumentParser(description="Diagnose an integration problem from a client's message.")
    ap.add_argument("text", nargs="?", help="the client's message, error or request (or pipe it in)")
    ap.add_argument("--ai", action="store_true", help="tailor the reply with Claude (needs ANTHROPIC_API_KEY)")
    args = ap.parse_args(argv)
    text = args.text if args.text else sys.stdin.read()
    results = diagnose_hybrid(text, use_ai=args.ai)
    if not results:
        print("No known pattern matched. Ask the client for the request they sent and the response they got.")
        return 1
    top = results[0]
    print(f"Likely cause: {top['title']}  (because {top['reason']})")
    print(f"How to confirm: {top['confirm']}")
    print(f"Fix: {top['fix']}")
    if len(results) > 1:
        print("Also possible: " + ", ".join(r["title"] for r in results[1:3]))
    print("\nSuggested reply:\n" + (ai_reply(text, top) if args.ai else top["reply"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
