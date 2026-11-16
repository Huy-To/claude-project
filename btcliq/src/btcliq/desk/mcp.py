"""The lookup endpoint: btcliq's lookups offered over MCP (Model Context Protocol).

MCP is the standard way a Claude client discovers and calls tools. This is the smallest
useful server: tools only, one JSON-RPC message per HTTP POST, answered as plain JSON.
Two kinds of caller reach it, told apart by the token they send:
- the in-app chat: a token that exists only while one question is being answered, and
  unlocks every lookup plus the events that draw the lookup cards;
- other Claude Code sessions on this PC (through bridge.py): a long-lived token that
  unlocks the read-only lookups and nothing chat-specific.
"""

from __future__ import annotations

import json
import logging

from aiohttp import web

log = logging.getLogger("btcliq")

# Protocol revisions this server speaks. A client asking for another one is offered the newest here.
VERSIONS = ("2025-11-25", "2025-06-18", "2025-03-26", "2024-11-05")
SERVER_INFO = {"name": "btcliq", "title": "btcliq live data", "version": "1.0"}
INSTRUCTIONS = ("Live BTC market data, option chain, account (read-only), calendar, knowledge notes and "
                "calculators from the user's running btcliq app. Every tool only reads.")


def _error(mid, code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": mid, "error": {"code": code, "message": message}}


async def handle_message(desk, msg, caller) -> dict | None:
    """One JSON-RPC message in, one response out (None for notifications). `caller` is
    ("chat", ChatContext) or ("session", None)."""
    if not isinstance(msg, dict) or msg.get("jsonrpc") != "2.0" or not isinstance(msg.get("method"), str):
        return _error(msg.get("id") if isinstance(msg, dict) else None, -32600, "Invalid request")
    mid, method, params = msg.get("id"), msg["method"], msg.get("params") or {}
    if mid is None:                       # a notification, e.g. notifications/initialized: nothing to answer
        return None
    kind, chat = caller
    if method == "initialize":
        want = params.get("protocolVersion") if isinstance(params, dict) else None
        result = {"protocolVersion": want if want in VERSIONS else VERSIONS[0],
                  "capabilities": {"tools": {"listChanged": False}},
                  "serverInfo": SERVER_INFO, "instructions": INSTRUCTIONS}
    elif method == "ping":
        result = {}
    elif method == "tools/list":
        result = {"tools": desk.registry.mcp_tools(chat=kind == "chat")}
    elif method == "tools/call":
        if not isinstance(params, dict) or not isinstance(params.get("name"), str):
            return _error(mid, -32602, "tools/call needs a tool name")
        res = await desk.run_lookup(chat, params["name"], params.get("arguments") or {})
        result = {"content": [{"type": "text", "text": res.text}], "isError": not res.ok}
    else:
        return _error(mid, -32601, f"Method not found: {method}")
    return {"jsonrpc": "2.0", "id": mid, "result": result}


async def mcp_handler(req: web.Request) -> web.StreamResponse:
    """POST /mcp. The route guard has already checked that the request is local."""
    desk = req.app["desk"]
    token = req.headers.get("X-Btcliq-Desk") or ""
    if not token and req.headers.get("Authorization", "").lower().startswith("bearer "):
        token = req.headers["Authorization"][7:].strip()
    caller = desk.caller_for(token)
    if caller is None:
        return web.json_response(_error(None, -32001, "Unknown or expired token"), status=403)
    if req.method != "POST":              # no server-initiated stream to open, no session to end
        return web.Response(status=405, headers={"Allow": "POST"})
    try:
        body = json.loads(await req.read())
    except ValueError:
        return web.json_response(_error(None, -32700, "Parse error"), status=400)
    if isinstance(body, list):            # batches were allowed by older protocol revisions
        out = [r for r in [await handle_message(desk, m, caller) for m in body[:50]] if r is not None]
        return web.json_response(out) if out else web.Response(status=202)
    out = await handle_message(desk, body, caller)
    return web.json_response(out) if out is not None else web.Response(status=202)
