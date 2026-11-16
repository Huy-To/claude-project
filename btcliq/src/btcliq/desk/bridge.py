"""Lets other Claude Code sessions on this PC use btcliq's read-only lookups.

Claude Code starts this as a small helper program (an MCP "stdio server") and talks to it
over stdin and stdout. The helper passes each lookup on to the running app and hands back
the answer. It holds no data of its own: when btcliq isn't running, it says so.

    python -m btcliq.desk.bridge [--data-dir DIR]

The app writes its address and the sessions' token to data/desk/endpoint.json when it starts.
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

from .mcp import INSTRUCTIONS, SERVER_INFO, VERSIONS

DEFAULT_DATA = Path(__file__).resolve().parents[3] / "data"
NOT_RUNNING = ("btcliq isn't running, so there is no live data to read. Ask the user to start it "
               "(`btcliq --open` in the btcliq folder), then try again.")


def endpoint(data_dir: Path) -> dict | None:
    try:
        d = json.loads((data_dir / "desk" / "endpoint.json").read_text())
        return d if d.get("url") and d.get("token") else None
    except (OSError, ValueError):
        return None


def forward(data_dir: Path, msg: dict, timeout: float = 240) -> dict | None:
    """Send one message to the app. Returns its reply, or None if the app can't be reached."""
    ep = endpoint(data_dir)
    if not ep:
        return None
    req = urllib.request.Request(ep["url"], data=json.dumps(msg).encode(), method="POST",
                                 headers={"Content-Type": "application/json", "X-Btcliq-Desk": ep["token"]})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            body = r.read()
            return json.loads(body) if body else None
    except (urllib.error.URLError, OSError, ValueError):
        return None


def offline_tools() -> list[dict]:
    """The lookup list without the app, so a session can still see what would be available."""
    try:
        from .lookups import R
        return R.mcp_tools(chat=False)
    except Exception:
        return []


def answer(data_dir: Path, msg: dict) -> dict | None:
    mid, method = msg.get("id"), msg.get("method")
    if mid is None:                         # a notification
        return None
    ok = lambda result: {"jsonrpc": "2.0", "id": mid, "result": result}
    if method == "initialize":
        want = (msg.get("params") or {}).get("protocolVersion")
        return ok({"protocolVersion": want if want in VERSIONS else VERSIONS[0],
                   "capabilities": {"tools": {"listChanged": False}}, "serverInfo": SERVER_INFO,
                   "instructions": INSTRUCTIONS})
    if method == "ping":
        return ok({})
    if method == "tools/list":
        return forward(data_dir, msg, timeout=10) or ok({"tools": offline_tools()})
    if method == "tools/call":
        return forward(data_dir, msg) or ok({"content": [{"type": "text", "text": NOT_RUNNING}], "isError": True})
    return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": f"Method not found: {method}"}}


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(prog="btcliq.desk.bridge", description="Bridge Claude Code sessions to a running btcliq")
    p.add_argument("--data-dir", default=str(DEFAULT_DATA), help="btcliq's data folder (default: the project's data/)")
    data_dir = Path(p.parse_args(argv).data_dir)
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except ValueError:
            continue
        for m in (msg if isinstance(msg, list) else [msg]):
            if isinstance(m, dict):
                out = answer(data_dir, m)
                if out is not None:
                    sys.stdout.write(json.dumps(out) + "\n")
                    sys.stdout.flush()


if __name__ == "__main__":
    main()
