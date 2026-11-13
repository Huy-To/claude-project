"""HTTP routes for the Trade panel: /api/trade/*.

These can move real money, so beyond the safety rails they only answer requests that
come from the btcliq page itself:
- the Host header must be localhost or 127.0.0.1 (stops DNS-rebinding sites),
- a browser Origin, when sent, must be the same local address,
- every call except /status needs the per-run token from /status in an X-Btcliq-Trade
  header. A custom header forces a CORS preflight, which this server never approves,
  so no other website can send one.
"""

from __future__ import annotations

import hmac
import logging
import secrets
from urllib.parse import urlsplit

from aiohttp import web

from .okx_private import OKXError, OKXNotConfigured
from .trader import NeedsApproval, OrderInputError, RailsBlocked, Trader

log = logging.getLogger("btcliq")
LOCAL = ("localhost", "127.0.0.1", "[::1]", "::1")


def _host_ok(host: str | None) -> bool:
    if not host:
        return False
    h = host.rsplit(":", 1)[0] if not host.startswith("[") else host.split("]")[0] + "]"
    return h in LOCAL


@web.middleware
async def trade_guard(req: web.Request, handler):
    if not req.path.startswith("/api/trade"):
        return await handler(req)
    if not _host_ok(req.headers.get("Host")):
        return web.json_response({"error": "Trading only answers on localhost"}, status=403)
    origin = req.headers.get("Origin")
    if origin and (urlsplit(origin).hostname or "") not in LOCAL:
        return web.json_response({"error": "Cross-site request refused"}, status=403)
    if req.path != "/api/trade/status":
        token = req.headers.get("X-Btcliq-Trade", "")
        if not hmac.compare_digest(token, req.app["trade_token"]):
            return web.json_response({"error": "Missing or stale trade token: reload the page"}, status=403)
    try:
        return await handler(req)
    except NeedsApproval as e:
        return web.json_response({"error": str(e), "needs_approval": e.missing, "preview": e.preview}, status=409)
    except RailsBlocked as e:
        return web.json_response({"error": str(e), "blocked": True, "preview": e.preview}, status=409)
    except OrderInputError as e:
        return web.json_response({"error": str(e)}, status=400)
    except OKXNotConfigured as e:
        return web.json_response({"error": str(e)}, status=503)
    except OKXError as e:
        return web.json_response({"error": str(e), "code": e.code}, status=502)


def _trader(req) -> Trader:
    tr = req.app.get("trader")
    if tr is None:
        raise web.HTTPServiceUnavailable(text="Trading is starting up")
    return tr


async def _body(req) -> dict:
    try:
        b = await req.json()
    except Exception:
        raise OrderInputError("Expected a JSON body")
    if not isinstance(b, dict):
        raise OrderInputError("Expected a JSON object")
    return b


async def status(req):
    tr = _trader(req)
    return web.json_response({**tr.status(), "token": req.app["trade_token"]})


async def account(req):
    return web.json_response(await _trader(req).account())


async def quote(req):
    tr = _trader(req)
    inst = req.query.get("instId", "")
    meta = await tr.instrument(inst)
    from .trader import contract_math, describe_instrument
    cm = contract_math(meta)
    return web.json_response({"instId": meta["instId"], "type": meta["instType"], "about": describe_instrument(meta),
                              "tick": meta["tickSz"], "lot": meta["lotSz"], "min": meta["minSz"],
                              "price_ccy": cm["price_ccy"], "size_unit": cm["unit"],
                              "btc_per": cm["btc_per"], "usd_per": cm["usd_per"], **(await tr.quote(inst))})


async def instruments(req):
    return web.json_response(await _trader(req).instrument_list())


def _flag(v) -> bool:
    return str(v or "").lower() in ("1", "true", "yes", "on")


async def check(req):
    """Read-only: the preview's numbers and rail checks without storing a preview (the ticket's live summary)."""
    q = req.query
    body = {"instId": q.get("instId", ""), "side": q.get("side", ""), "ordType": q.get("ordType") or None,
            "sz": q.get("sz", ""), "px": q.get("px") or None, "reduce": _flag(q.get("reduce"))}
    return web.json_response(await _trader(req).check(body))


async def sizing(req):
    q = req.query
    return web.json_response(await _trader(req).sizing(q.get("instId", ""), q.get("side", ""), q.get("px") or None,
                                                       _flag(q.get("reduce"))))


async def history(req):
    q = req.query
    return web.json_response(await _trader(req).history(q.get("kind", ""), q.get("days", "90")))


async def preview(req):
    return web.json_response(await _trader(req).preview(await _body(req), source="ui"))


async def place(req):
    b = await _body(req)
    accept = b.get("accept") or []
    if not isinstance(accept, list):
        raise OrderInputError("accept must be a list of rule names")
    tr = _trader(req)
    tr.forget_history()
    return web.json_response(await tr.place(str(b.get("id", "")), source="ui", accept=[str(a) for a in accept]))


async def cancel(req):
    b = await _body(req)
    tr = _trader(req)
    tr.forget_history()
    return web.json_response(await tr.cancel(str(b.get("instId", "")), str(b.get("ordId", "")), source="ui"))


async def amend(req):
    b = await _body(req)
    accept = b.get("accept") or []
    if not isinstance(accept, list):
        raise OrderInputError("accept must be a list of rule names")
    tr = _trader(req)
    tr.forget_history()
    return web.json_response(await tr.amend(str(b.get("instId", "")), str(b.get("ordId", "")),
                                            b.get("px"), b.get("sz"), source="ui", accept=[str(a) for a in accept]))


async def cancel_all(req):
    tr = _trader(req)
    tr.forget_history()
    return web.json_response(await tr.cancel_all(source="ui"))


KILL_WORD = "KILL"


async def kill(req):
    b = await _body(req)
    # The page sends the word you typed in its confirmation dialog. Refusing anything else means
    # a stray click, a stale cached page or a script can't trip the kill switch without that step.
    if str(b.get("confirm", "")).strip().upper() != KILL_WORD:
        raise OrderInputError(f"Kill switch not confirmed: type {KILL_WORD} in the dialog to confirm")
    tr = _trader(req)
    tr.forget_history()
    return web.json_response(await tr.kill(str(b.get("reason", "")), source="ui"))


async def resume(req):
    return web.json_response(_trader(req).resume(source="ui"))


def setup(app: web.Application) -> None:
    app["trade_token"] = secrets.token_urlsafe(24)
    app.middlewares.append(trade_guard)
    r = app.router
    r.add_get("/api/trade/status", status)
    r.add_get("/api/trade/account", account)
    r.add_get("/api/trade/quote", quote)
    r.add_get("/api/trade/instruments", instruments)
    r.add_get("/api/trade/check", check)
    r.add_get("/api/trade/sizing", sizing)
    r.add_get("/api/trade/history", history)
    r.add_post("/api/trade/preview", preview)
    r.add_post("/api/trade/place", place)
    r.add_post("/api/trade/cancel", cancel)
    r.add_post("/api/trade/amend", amend)
    r.add_post("/api/trade/cancel-all", cancel_all)
    r.add_post("/api/trade/kill", kill)
    r.add_post("/api/trade/resume", resume)


def start(app: web.Application, session, data_dir) -> None:
    tr = app["trader"] = Trader(session, data_dir)
    st = tr.status()
    if not st["configured"]:
        log.info("Trading is off: add OKX_API_KEY, OKX_API_SECRET and OKX_API_PASSPHRASE to .env")
    elif st["rails"]["error"]:
        log.warning("Trading is off: %s", st["rails"]["error"])
    else:
        log.info("Trading is ON (%s keys, limits from %s)%s", st["env"].upper(), st["rails"]["file"],
                 "" if st["rails"]["enabled"] else " but enabled = false in the limits file")
    if st["halted"]:
        log.warning("Trading is HALTED: %s", st["halted"].get("reason"))
