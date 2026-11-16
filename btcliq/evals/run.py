"""Run the chat's question set against a running btcliq and score each answer.

    .venv/bin/python evals/run.py                      # every question, on the app at port 8050
    .venv/bin/python evals/run.py --only idea,limits   # just these
    .venv/bin/python evals/run.py --model claude-haiku-5-5 --port 8061

Each question is asked in a new conversation, the way the page asks it. The answer, the
lookups Claude ran and any note it proposed are checked against the rules in
questions.json, and everything is saved under evals/results/.

This spends your Claude plan: one answer per question. It never uses the API key. The
conversations it creates are deleted afterwards unless you pass --keep.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
import urllib.request
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCREEN = {"view": "chart", "tf": "15m", "tz": "America/New_York", "option": None, "ticket": {}, "drawings": []}


def call(base: str, token: str, path: str, body=None, method=None):
    req = urllib.request.Request(base + path, data=json.dumps(body).encode() if body is not None else None,
                                 method=method, headers={"Content-Type": "application/json", "X-Btcliq-Trade": token})
    return urllib.request.urlopen(req, timeout=900)


def ask(base: str, token: str, text: str, model: str | None) -> dict:
    out = {"text": "", "lookups": [], "failed": [], "notes": [], "prepared": [], "error": "", "limit": False, "conversation": None,
           "usage": None, "stop": None}
    t0 = time.time()
    with call(base, token, "/api/desk/ask", {"text": text, "model": model, "web": False, "screen": SCREEN}) as r:
        for raw in r:
            ev = json.loads(raw)
            t = ev.get("type")
            if t == "start":
                out["conversation"] = ev["conversation"]["id"]
                out["model"] = ev.get("model")
            elif t == "text":
                out["text"] += ev["text"]
            elif t == "tool" and ev["state"] == "start":
                out["lookups"].append(ev["name"])
            elif t == "tool" and ev["state"] == "done" and not ev.get("ok"):
                out["failed"].append(ev.get("error") or "failed")
            elif t == "note":
                out["notes"].append(ev["text"])
            elif t == "proposal":
                out["prepared"].append(ev.get("kind"))
            elif t == "limit":
                out["limit"] = True
            elif t == "error":
                out["error"] = ev.get("message", "")
            elif t == "done":
                out["stop"] = ev.get("stop")
                out["usage"] = (ev.get("usage") or {}).get("last")
    out["seconds"] = round(time.time() - t0, 1)
    return out


def score(case: dict, got: dict) -> list[tuple[bool, str]]:
    """Every check for one case as (passed, what was checked)."""
    text, calls, checks = got["text"], got["lookups"], []
    hit = lambda rx, s=None: re.search(rx, text if s is None else s, re.I | re.S) is not None
    quiet = text
    if case.get("allow_quote"):                # quoting the trap back to the user is not following it
        quiet = re.sub(r"[\"“'‘`][^\"”'’`\n]{0,160}" + case["allow_quote"] + r"[^\"”'’`\n]{0,160}[\"”'’`]", "", text, flags=re.I)
    checks.append((got["stop"] == "end" and bool(text.strip()), f"answered (stop={got['stop']}{', ' + got['error'] if got['error'] else ''})"))
    for name in case.get("must_call", []):
        checks.append((name in calls, f"ran the {name} lookup"))
    if case.get("must_call_any"):
        checks.append((any(n in calls for n in case["must_call_any"]), "ran one of: " + ", ".join(case["must_call_any"])))
    for name in case.get("must_not_call", []):
        checks.append((name not in calls, f"did not run {name}"))
    for rx in case.get("must_match", []):
        checks.append((hit(rx), f"answer mentions /{rx}/"))
    if case.get("must_match_any"):
        checks.append((any(hit(rx) for rx in case["must_match_any"]), "answer says one of the expected things"))
    for rx in case.get("must_not_match", []):
        checks.append((not hit(rx, quiet), f"answer does not say /{rx}/"))
    if case.get("must_propose_note"):
        checks.append((bool(got["notes"]), "proposed a pinned note"))
    if case.get("must_not_propose_note"):
        checks.append((not got["notes"], "proposed no note"))
    prepared = got.get("prepared") or []
    if case.get("must_prepare"):
        checks.append((any(k in prepared for k in case["must_prepare"]), "prepared a card: " + " or ".join(case["must_prepare"])))
    if case.get("must_not_prepare"):
        checks.append((not prepared, "prepared nothing"))
    if case.get("if_match") and hit(case["if_match"]) and not hit("no trade"):
        for name in case.get("then_call", []):
            checks.append((name in calls, f"an idea was given, so it ran {name}"))
        if case.get("then_prepare"):
            checks.append((any(k in prepared for k in case["then_prepare"]), "an idea was given, so it prepared the order as a card"))
        for rx in case.get("then_match", []):
            checks.append((hit(rx), f"the idea has its /{rx}/ line"))
    return checks


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Run the chat's question set against a running btcliq")
    p.add_argument("--port", default="8050")
    p.add_argument("--model", default=None, help="default: the app's own default (Sonnet 5.5)")
    p.add_argument("--only", default="", help="comma-separated case ids")
    p.add_argument("--keep", action="store_true", help="keep the conversations in the app afterwards")
    p.add_argument("--rescore", metavar="FILE", help="apply today's checks to a saved results file; asks nothing")
    a = p.parse_args(argv)
    if a.rescore:
        by_id = {c["id"]: c for c in json.loads((HERE / "questions.json").read_text())["cases"]}
        saved = json.loads(Path(a.rescore).read_text())["results"]
        n = 0
        for r in saved:
            checks = score(by_id[r["id"]], r) if r["id"] in by_id else []
            ok = bool(checks) and all(k for k, _ in checks)
            n += ok
            print(f"{'PASS' if ok else 'FAIL'}  {r['id']}")
            for k, what in checks:
                if not k:
                    print(f"      x {what}")
        print(f"\n{n} of {len(saved)} pass with the current checks")
        return 0 if n == len(saved) else 2
    base = f"http://127.0.0.1:{a.port}"
    try:
        token = json.loads(urllib.request.urlopen(base + "/api/trade/status", timeout=10).read())["token"]
    except OSError as e:
        print(f"btcliq isn't answering on port {a.port}: {e}")
        return 1
    cases = json.loads((HERE / "questions.json").read_text())["cases"]
    only = {x.strip() for x in a.only.split(",") if x.strip()}
    cases = [c for c in cases if not only or c["id"] in only]
    results, passed = [], 0
    for c in cases:
        try:
            got = ask(base, token, c["ask"], a.model)
        except Exception as e:
            got = {"text": "", "lookups": [], "failed": [], "notes": [], "prepared": [], "error": f"{type(e).__name__}: {e}", "stop": "error",
                   "seconds": 0, "usage": None, "conversation": None, "limit": False}
        checks = score(c, got)
        ok = all(k for k, _ in checks)
        passed += ok
        u = got.get("usage") or {}
        print(f"{'PASS' if ok else 'FAIL'}  {c['id']:20} {got['seconds']:5.1f}s  {u.get('in', 0):>6} in {u.get('out', 0):>5} out  "
              f"lookups: {', '.join(got['lookups']) or '-'}" + (f"  prepared: {', '.join(got['prepared'])}" if got.get('prepared') else ''))
        for k, what in checks:
            if not k:
                print(f"      x {what}")
        results.append({"id": c["id"], "ask": c["ask"], "why": c["why"], "passed": ok,
                        "checks": [{"passed": k, "check": what} for k, what in checks], **got})
        if got.get("limit"):
            print("The Claude plan limit was reached; stopping here.")
            break
        if got.get("conversation") and not a.keep:
            try:
                call(base, token, f"/api/desk/conversations/{got['conversation']}", method="DELETE").read()
            except OSError:
                pass
    tok_in = sum((r.get("usage") or {}).get("in", 0) for r in results)
    tok_out = sum((r.get("usage") or {}).get("out", 0) for r in results)
    print(f"\n{passed} of {len(results)} passed · {tok_in:,} tokens in, {tok_out:,} out · {sum(r['seconds'] for r in results):.0f}s")
    out = HERE / "results" / f"{datetime.now():%Y-%m-%d-%H%M%S}.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps({"when": datetime.now().isoformat(timespec="seconds"), "model": a.model or "app default",
                               "passed": passed, "total": len(results), "tokens_in": tok_in, "tokens_out": tok_out,
                               "results": results}, indent=1, ensure_ascii=False))
    print("saved", out)
    return 0 if passed == len(results) else 2


if __name__ == "__main__":
    sys.exit(main())
