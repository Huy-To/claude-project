"""HTTP routes for the Claude view: /api/desk/* for the page, /mcp for Claude's lookups.

The chat can read your OKX account, so these routes get the same protection as trading:
- the Host header must be localhost or 127.0.0.1 (stops DNS-rebinding sites),
- a browser Origin, when sent, must be the same local address,
- /api/desk/* needs the page's per-run token in an X-Btcliq-Trade header. A custom header
  forces a CORS preflight, which this server never approves, so no other website can call it,
- /mcp needs a desk token: one that lives only while a question is answered, or the
  long-lived read-only one that other Claude Code sessions use.
"""

from __future__ import annotations

import hmac
import json
import logging
from urllib.parse import urlsplit

from aiohttp import web

from ..trade_routes import LOCAL, _host_ok
from .mcp import mcp_handler
from .notebook import Conflict, NoteError

log = logging.getLogger("btcliq")


@web.middleware
async def desk_guard(req: web.Request, handler):
    path = req.path
    if not (path.startswith("/api/desk") or path == "/mcp"):
        return await handler(req)
    if not _host_ok(req.headers.get("Host")):
        return web.json_response({"error": "The chat only answers on localhost"}, status=403)
    origin = req.headers.get("Origin")
    if origin and (urlsplit(origin).hostname or "") not in LOCAL:
        return web.json_response({"error": "Cross-site request refused"}, status=403)
    if path.startswith("/api/desk"):
        token = req.headers.get("X-Btcliq-Trade", "")
        if not hmac.compare_digest(token, req.app["trade_token"]):
            return web.json_response({"error": "Missing or stale page token: reload the page"}, status=403)
    return await handler(req)


def _desk(req):
    return req.app["desk"]


async def _json(req) -> dict:
    try:
        b = await req.json()
    except Exception:
        raise web.HTTPBadRequest(text="Expected a JSON body")
    if not isinstance(b, dict):
        raise web.HTTPBadRequest(text="Expected a JSON object")
    return b


async def state(req):
    return web.json_response(_desk(req).state())


async def ask(req):
    """One question. The answer streams back as one JSON object per line."""
    desk = _desk(req)
    body = await _json(req)
    resp = web.StreamResponse(headers={"Content-Type": "application/x-ndjson; charset=utf-8",
                                       "Cache-Control": "no-store", "X-Accel-Buffering": "no"})
    await resp.prepare(req)
    gen = desk.ask(body)
    try:
        async for ev in gen:
            await resp.write((json.dumps(ev, ensure_ascii=False, default=str) + "\n").encode())
        await resp.write_eof()
    except (ConnectionResetError, ConnectionError):
        pass                                  # the page pressed Stop or closed
    finally:
        await gen.aclose()                    # stops Claude and saves what was answered so far
    return resp


async def conversations(req):
    desk = _desk(req)
    q = req.query.get("q", "").strip()
    if q:
        return web.json_response({"query": q, "matches": desk.conversations.search(q, 30)})
    return web.json_response({"conversations": desk.conversations.list()})


async def conversation(req):
    desk = _desk(req)
    cid = req.match_info["id"]
    if req.method == "DELETE":
        return web.json_response({"deleted": desk.conversations.delete(cid)})
    if req.method == "POST":
        head = desk.conversations.rename(cid, str((await _json(req)).get("title") or ""))
        if not head:
            raise web.HTTPNotFound(text="No such conversation")
        return web.json_response(head)
    c = desk.conversations.get(cid)
    if not c:
        raise web.HTTPNotFound(text="No such conversation")
    return web.json_response({**c, "usage": desk.usage_view(c)})


async def notes(req):
    """Pinned notes: list, add (this is how a proposed note gets approved), edit, remove."""
    desk = _desk(req)
    v = desk.vault
    try:
        if req.method == "POST":
            b = await _json(req)
            if b.get("id"):
                v.edit_pinned(str(b["id"]), str(b.get("text") or ""))
            else:
                v.add_pinned(str(b.get("text") or ""))
        elif req.method == "DELETE":
            v.remove_pinned(req.query.get("id", ""))
    except KeyError:
        raise web.HTTPNotFound(text="No such note")
    except (ValueError, RuntimeError) as e:
        return web.json_response({"error": str(e), "pinned": v.pinned()}, status=400)
    return web.json_response({"pinned": v.pinned(), "vault": v.status()})


async def settings(req):
    desk = _desk(req)
    b = await _json(req)
    if "key_cap_usd" in b:
        try:
            desk.usage.set_cap(float(b["key_cap_usd"]))
        except (TypeError, ValueError):
            raise web.HTTPBadRequest(text="key_cap_usd must be a number")
    return web.json_response({"usage": desk.usage_view()})


# ---------- the Notes view: the vault's own Markdown files ----------

def _nb(req):
    return _desk(req).notebook


def _note_errors(fn):
    async def wrap(req):
        try:
            return await fn(req)
        except NoteError as e:
            return web.json_response({"error": str(e)}, status=400)
        except FileNotFoundError as e:
            return web.json_response({"error": f"No note at {e}"}, status=404)
        except Conflict as e:
            return web.json_response({"error": str(e), "conflict": True, "current": e.current}, status=409)
        except OSError as e:
            log.warning("Notes: %s", e)
            return web.json_response({"error": f"Could not reach the file: {e.strerror or e}"}, status=500)
    return wrap


def _touched(req):
    _desk(req).vault._scan = None          # the chat's knowledge index re-reads on the next question


@_note_errors
async def vault_tree(req):
    return web.json_response(_nb(req).tree())


@_note_errors
async def vault_note(req):
    nb = _nb(req)
    if req.method == "GET":
        return web.json_response(nb.read(req.query.get("path", "")))
    if req.method == "DELETE":
        out = nb.trash(req.query.get("path", ""))
        _touched(req)
        return web.json_response(out)
    if req.method in ("PUT", "POST"):
        b = await _json(req)
        text = b.get("text")
        if not isinstance(text, str):
            raise web.HTTPBadRequest(text="text must be a string")
        base = b.get("base")
        out = nb.write(str(b.get("path") or ""), text, str(base) if base else None, create=bool(b.get("create")))
        _touched(req)
        return web.json_response(out)
    raise web.HTTPMethodNotAllowed(req.method, ["GET", "PUT", "POST", "DELETE"])


@_note_errors
async def vault_rename(req):
    b = await _json(req)
    out = _nb(req).rename(str(b.get("from") or ""), str(b.get("to") or ""))
    _touched(req)
    return web.json_response(out)


@_note_errors
async def vault_search(req):
    return web.json_response({"q": req.query.get("q", ""), "hits": _nb(req).search(req.query.get("q", ""))})


@_note_errors
async def vault_backlinks(req):
    return web.json_response({"links": _nb(req).backlinks(req.query.get("path", ""))})


@_note_errors
async def vault_file(req):
    """An image or PDF from the vault. The page fetches it with its token and shows a blob URL."""
    f, ctype = _nb(req).file(req.query.get("path", ""))
    headers = {"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff", "Content-Type": ctype}
    if ctype == "image/svg+xml":            # never run scripts inside an SVG someone dropped in the vault
        headers["Content-Security-Policy"] = "default-src 'none'; style-src 'unsafe-inline'; sandbox"
    return web.FileResponse(f, headers=headers)


@_note_errors
async def vault_attach(req):
    """A pasted or dropped image: the raw bytes, with ?ext=png and optionally &name=."""
    data = await req.read()
    return web.json_response(_nb(req).attach(data, req.query.get("ext", ""), req.query.get("name")))


def setup(app: web.Application) -> None:
    app.middlewares.append(desk_guard)
    r = app.router
    r.add_get("/api/desk/state", state)
    r.add_post("/api/desk/ask", ask)
    r.add_get("/api/desk/conversations", conversations)
    r.add_route("*", "/api/desk/conversations/{id}", conversation)
    r.add_route("*", "/api/desk/notes", notes)
    r.add_post("/api/desk/settings", settings)
    r.add_get("/api/desk/vault/tree", vault_tree)
    r.add_route("*", "/api/desk/vault/note", vault_note)
    r.add_post("/api/desk/vault/rename", vault_rename)
    r.add_get("/api/desk/vault/search", vault_search)
    r.add_get("/api/desk/vault/backlinks", vault_backlinks)
    r.add_get("/api/desk/vault/file", vault_file)
    r.add_post("/api/desk/vault/attach", vault_attach)
    r.add_route("*", "/mcp", mcp_handler)
