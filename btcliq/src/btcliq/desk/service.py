"""The desk itself: one question in, a stream of events out.

It owns everything a question needs (the lookups, the vault, the saved conversations, the
usage ledger, the two engines) and decides which engine answers:
- your Claude login through Claude Code, when it is installed;
- the API key only when you chose it (the page offers it once the plan limit is hit, or
  btcliq was started with --chat api), and never past the daily cap.
"""

from __future__ import annotations

import asyncio
import hmac
import json
import logging
import os
import secrets
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import AsyncIterator

from . import prompt
from .calc import Quant
from .engine_api import ApiEngine
from .engine_cli import CliEngine, Job, RunResult
from .lookups import Ctx, R
from .notebook import Notebook
from .registry import LookupResult
from .store import Conversations, Usage, title_from
from .vault import Vault

log = logging.getLogger("btcliq")

MODELS = [
    {"id": "claude-haiku-5-5", "label": "Haiku 5.5", "hint": "Fastest. Quick checks."},
    {"id": "claude-sonnet-5-5", "label": "Sonnet 5.5", "hint": "The everyday default."},
    {"id": "claude-opus-5-5", "label": "Opus 5.5", "hint": "Deeper analysis, slower."},
    {"id": "claude-fable-5-2", "label": "Fable 5.2", "hint": "The most capable. Heaviest on your plan."},
]
DEFAULT_MODEL = "claude-sonnet-5-5"
SUMMARY_MODEL = "claude-haiku-5-5"
MAX_QUESTION_CHARS = 12_000
MAX_SCREEN_CHARS = 60_000


@dataclass
class ChatContext:
    """One question being answered. Its token is what the lookup endpoint accepts while it runs."""
    token: str
    conversation_id: str
    screen: dict
    web: bool
    queue: asyncio.Queue = field(default_factory=asyncio.Queue)
    sources: set = field(default_factory=set)      # unverified text this answer read: telegram, web
    n: int = 0

    def emit(self, ev: dict) -> None:
        self.queue.put_nowait(ev)


class Desk:
    def __init__(self, st, args, data_dir: Path, project_dir: Path, candles_fn=None):
        self.st = st
        self.args = args
        self.data_dir = Path(data_dir)
        self.project_dir = Path(project_dir)
        self.trader = None
        self.ex = None
        self.quant: Quant | None = None
        self._candles_fn = candles_fn
        self.registry = R
        self.vault = Vault.find(getattr(args, "vault", None), self.project_dir, self.data_dir / "desk")
        self.notebook = Notebook(self.vault.path)       # the Notes view: every .md file in the vault
        self.conversations = Conversations(self.data_dir)
        self.usage = Usage(self.data_dir, default_cap=float(os.environ.get("BTCLIQ_KEY_CAP_USD") or 2.0))
        self.contexts: dict[str, ChatContext] = {}
        self._acct: tuple[float, dict | None] = (0.0, None)
        self._tasks: set[asyncio.Task] = set()

        self.requested = getattr(args, "chat", "auto")
        self.model = getattr(args, "model", None) or DEFAULT_MODEL
        self.effort = getattr(args, "effort", None) or "medium"
        key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
        claude_bin = shutil.which("claude")
        self.cli = CliEngine(claude_bin) if claude_bin and self.requested in ("auto", "claude-code") else None
        self.api = ApiEngine(key) if key and self.requested in ("auto", "api") else None
        self.primary = "claude-code" if self.cli else "api" if self.api else "off"
        self.session_token = self._load_session_token()

    # ---------- wiring ----------

    def start(self, session, ex, trader) -> None:
        """Called once the app's network session and feeds exist."""
        self.ex, self.trader = ex, trader
        self.quant = Quant(session, self.data_dir, getattr(self.args, "garch_dir", None))
        self._write_endpoint()

    @property
    def enabled(self) -> bool:
        return self.primary != "off"

    @property
    def label(self) -> str:
        return {"claude-code": "Claude Code", "api": "Anthropic API"}.get(self.primary, "")

    @property
    def mcp_url(self) -> str:
        return f"http://127.0.0.1:{getattr(self.args, 'port', 8050)}/mcp"

    def _load_session_token(self) -> str:
        """The long-lived token for other Claude Code sessions (read-only lookups)."""
        p = self.data_dir / "desk" / "session-token"
        try:
            tok = p.read_text().strip()
            if len(tok) >= 24:
                return tok
        except OSError:
            pass
        tok = secrets.token_urlsafe(32)
        try:
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(tok)
            p.chmod(0o600)
        except OSError as e:
            log.warning("couldn't save the sessions' lookup token: %s", e)
        return tok

    def _write_endpoint(self) -> None:
        p = self.data_dir / "desk" / "endpoint.json"
        try:
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(json.dumps({"url": self.mcp_url, "token": self.session_token, "pid": os.getpid(),
                                     "started": int(time.time() * 1000)}))
            p.chmod(0o600)
        except OSError as e:
            log.warning("couldn't write the lookup endpoint file: %s", e)

    def caller_for(self, token: str):
        """Who is calling the lookup endpoint: a question in progress, another session, or nobody."""
        if not token:
            return None
        ctx = self.contexts.get(token)
        if ctx is not None:
            return ("chat", ctx)
        if hmac.compare_digest(token, self.session_token):
            return ("session", None)
        return None

    # ---------- what lookups use ----------

    async def candles(self, tf: str) -> list[dict]:
        if self._candles_fn is None:
            raise RuntimeError("candles aren't available")
        return await self._candles_fn(tf)

    async def account_summary(self) -> dict | None:
        """The account, for the snapshot's summary lines. Cached briefly; None if OKX isn't connected."""
        tr = self.trader
        if tr is None or not tr.okx.configured:
            return None
        if time.time() - self._acct[0] < 10:
            return self._acct[1]
        try:
            a = await asyncio.wait_for(tr.account(), 8)
        except Exception as e:
            log.info("account summary unavailable for the chat: %s", e)
            a = None
        self._acct = (time.time(), a)
        return a

    async def snapshot_text(self, chart_tf: str | None, option: str | None, question: str) -> str:
        return await prompt.snapshot_text(self.st, chart_tf, option, question, await self.account_summary())

    async def run_lookup(self, chat: ChatContext | None, name: str, args) -> LookupResult:
        """Run one lookup and, for an in-app question, send the events that draw its card."""
        l = self.registry.get(name)
        show = chat is not None and (l is None or l.card)
        cid = ""
        if show:
            chat.n += 1
            cid = f"t{chat.n}"
            chat.emit({"type": "tool", "id": cid, "state": "start", "name": name, "title": l.title if l else name,
                       "group": l.group if l else "", "args": args if isinstance(args, dict) else {}})
        res = await self.registry.call(name, args, Ctx(self, chat))
        if chat is not None and res.ok and res.untrusted:
            chat.sources.add("telegram")
        if show:
            chat.emit({"type": "tool", "id": cid, "state": "done", "ok": res.ok, "ms": res.ms, "args": res.args,
                       "data": res.data if res.ok else None, "error": res.error, "untrusted": res.untrusted})
        return res

    # ---------- state for the page ----------

    def usage_view(self, conv: dict | None = None) -> dict:
        out = self.usage.snapshot()
        tok = (conv or {}).get("tokens") or {}
        out["conversation"] = {"tokens": (tok.get("in") or 0) + (tok.get("out") or 0),
                               "in": tok.get("in") or 0, "out": tok.get("out") or 0}
        return out

    def state(self) -> dict:
        return {"enabled": self.enabled, "primary": self.primary, "label": self.label,
                "disabled_by_flag": self.requested == "off",
                "engines": {"claude_code": self.cli is not None, "api": self.api is not None},
                "model": self.model if any(m["id"] == self.model for m in MODELS) else DEFAULT_MODEL,
                "models": MODELS + ([] if any(m["id"] == self.model for m in MODELS)
                                    else [{"id": self.model, "label": self.model, "hint": "Set with --model."}]),
                "briefs": prompt.BRIEFS, "vault": self.vault.status(), "pinned": self.vault.pinned(),
                "usage": self.usage_view(), "lookups": len(self.registry.visible(chat=True))}

    # ---------- answering ----------

    def _pick_engine(self, want: str):
        """(engine, error). `want` is "api" when you chose to continue on the key."""
        if want == "api":
            if not self.api:
                return None, "There is no API key to fall back to. Add ANTHROPIC_API_KEY to .env and restart btcliq."
            return self.api, ""
        if self.primary == "claude-code":
            return self.cli, ""
        if self.primary == "api":
            return self.api, ""
        return None, "Chat is off: install Claude Code and log in, or add ANTHROPIC_API_KEY to .env, then restart btcliq."

    async def ask(self, body: dict) -> AsyncIterator[dict]:
        """Answer one question. Yields events for the page; the last one is always type "done"."""
        text = " ".join(str(body.get("text") or "").split("\r")).strip()[:MAX_QUESTION_CHARS]
        retry = bool(body.get("retry"))
        conv = self.conversations.get(str(body.get("conversation") or "")) or self.conversations.new()
        msgs = conv["messages"]
        if retry:                                    # answer the last question again, e.g. on the API key
            while msgs and msgs[-1].get("role") == "assistant" and msgs[-1].get("stop") not in (None, "end"):
                msgs.pop()                           # drop the attempt that failed or was cut short
            if not msgs or msgs[-1].get("role") != "user":
                yield {"type": "error", "message": "There is no unanswered question to retry."}
                yield {"type": "done", "stop": "error"}
                return
            text = msgs[-1]["text"]
        else:
            if not text:
                yield {"type": "error", "message": "Nothing to answer."}
                yield {"type": "done", "stop": "error"}
                return
            msgs.append({"role": "user", "text": text, "ts": int(time.time() * 1000)})
            if not conv.get("title"):
                conv["title"] = title_from(text)
        self.conversations.save(conv)

        model = str(body.get("model") or self.model)
        if not any(m["id"] == model for m in MODELS) and model != self.model:
            model = self.model
        engine, err = self._pick_engine(str(body.get("engine") or "auto"))
        head = self.conversations._head(conv)
        yield {"type": "start", "conversation": head, "engine": engine.name if engine else "off", "model": model}
        if engine is None:
            yield {"type": "error", "message": err}
            yield {"type": "done", "stop": "error", "conversation": head, "usage": self.usage_view(conv)}
            return
        if engine.name == "api" and self.usage.key_left() <= 0:
            yield {"type": "error", "message": f"Today's API key cap (${self.usage.cap:,.2f}) is used up. "
                                               "Raise it in the usage panel, or wait until tomorrow."}
            yield {"type": "done", "stop": "cap", "conversation": head, "usage": self.usage_view(conv)}
            return

        screen = body.get("screen") if isinstance(body.get("screen"), dict) else {}
        if len(json.dumps(screen, default=str)) > MAX_SCREEN_CHARS:
            screen = {k: v for k, v in screen.items() if k != "drawings"}
        web = bool(body.get("web"))
        ctx = ChatContext(secrets.token_urlsafe(24), conv["id"], screen, web)
        self.contexts[ctx.token] = ctx
        blocks: list[dict] = []
        result: RunResult | None = None
        saved = False

        def record(ev: dict) -> None:
            """Keep what the page shows, so the conversation can be reopened exactly as it was."""
            t = ev.get("type")
            if t == "text":
                if blocks and blocks[-1]["t"] == "text":
                    blocks[-1]["text"] += ev["text"]
                else:
                    blocks.append({"t": "text", "text": ev["text"]})
            elif t == "tool":
                if ev["state"] == "start":
                    blocks.append({"t": "card", "id": ev["id"], "name": ev["name"], "title": ev["title"],
                                   "group": ev.get("group", ""), "args": ev.get("args") or {}, "state": "run"})
                else:
                    b = next((b for b in blocks if b["t"] == "card" and b.get("id") == ev["id"]), None)
                    if b:
                        b.update(state="done", ok=ev.get("ok"), ms=ev.get("ms"), data=ev.get("data"),
                                 error=ev.get("error") or "", untrusted=bool(ev.get("untrusted")))
                        if ev.get("args"):
                            b["args"] = ev["args"]
                        if ev.get("untrusted") and ev.get("name", b["name"]) == "web_search":
                            ctx.sources.add("web")
            elif t == "note":
                blocks.append({"t": "note", "text": ev["text"], "why": ev.get("why", "")})
            elif t == "proposal":
                blocks.append({"t": "proposal", **{k: v for k, v in ev.items() if k != "type"}})
            elif t == "notice":
                blocks.append({"t": "notice", "text": ev["text"]})

        def save(stop: str, error: str = "") -> None:
            nonlocal saved
            if saved:
                return
            saved = True
            answer = "".join(b["text"] for b in blocks if b["t"] == "text").strip()
            usage = result.usage if result else {}
            entry = self.usage.add(model, engine.name, usage) if result else None
            if entry:
                conv["tokens"]["in"] = (conv["tokens"].get("in") or 0) + entry["in"]
                conv["tokens"]["out"] = (conv["tokens"].get("out") or 0) + entry["out"]
            for b in blocks:                       # a card still running when we stopped never finished
                if b["t"] == "card" and b.get("state") == "run":
                    b.update(state="done", ok=False, error="stopped before it finished")
            if answer or any(b["t"] in ("card", "note", "proposal") for b in blocks):
                conv["messages"].append({"role": "assistant", "text": answer, "ts": int(time.time() * 1000),
                                         "model": model, "engine": engine.name, "blocks": blocks, "usage": entry,
                                         "stop": stop, "error": error, "web": web, "sources": sorted(ctx.sources)})
            self.conversations.save(conv)

        async def run() -> None:
            nonlocal result
            try:
                snapshot = await self.snapshot_text(screen.get("tf"), screen.get("option"), text)
                if "### Telegram news" in snapshot and "no posts" not in snapshot.split("### Telegram news", 1)[1][:200]:
                    ctx.sources.add("telegram")
                summary, turns = prompt.history_for(conv)
                context = prompt.context_block(snapshot, screen, web)
                system = prompt.system_prompt(self.vault.index(), self.vault.pinned())
                job = Job(system=system, model=model, effort=self.effort, web=web, mcp_url=self.mcp_url, token=ctx.token)
                if engine.name == "claude-code":
                    job.prompt = prompt.cli_prompt(summary, turns, context, text)
                    result = await engine.run(job, ctx.emit, on_plan=self.usage.note_plan)
                else:
                    job.messages = prompt.api_messages(summary, turns, context, text)
                    job.budget_usd = self.usage.key_left()

                    async def call(name, args):
                        r = await self.run_lookup(ctx, name, args)
                        return r.text, r.ok

                    result = await engine.run(job, ctx.emit, tools=self.registry.api_tools(chat=True), call_tool=call)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                log.exception("chat question failed")
                result = RunResult(stop="error", error=f"{type(e).__name__}: {e}")
            finally:
                ctx.emit({"type": "_end"})

        task = asyncio.create_task(run())
        try:
            while True:
                ev = await ctx.queue.get()
                if ev.get("type") == "_end":
                    break
                record(ev)
                yield ev
            await task
            res = result or RunResult(stop="error", error="no answer")
            if res.stop == "limit":
                plan = self.usage.snapshot()["plan"]
                resets = (plan.get("five_hour") or {}).get("resets") or plan.get("resets")
                save("limit", res.error)
                yield {"type": "limit", "message": "Your Claude plan's usage limit has been reached.",
                       "resets": resets, "can_use_key": self.api is not None and self.usage.key_left() > 0,
                       "has_key": self.api is not None,
                       "key": {"usd": self.usage.today().get("key_usd", 0.0), "cap": self.usage.cap}}
            elif res.stop in ("error", "refusal", "cut", "cap"):
                save(res.stop, res.error)
                yield {"type": "error", "message": res.error or "Something went wrong.", "kind": res.stop}
            else:
                save("end")
            head = self.conversations._head(conv)
            yield {"type": "done", "stop": res.stop, "conversation": head, "usage": self.usage_view(conv),
                   "sources": sorted(ctx.sources)}
            upto = prompt.needs_summary(conv)
            if upto and res.stop == "end":
                t = asyncio.create_task(self._summarize(conv["id"], upto, engine))
                self._tasks.add(t)
                t.add_done_callback(self._tasks.discard)
        finally:
            self.contexts.pop(ctx.token, None)
            if not task.done():                    # the page pressed Stop or went away
                task.cancel()
                try:
                    await task
                except (asyncio.CancelledError, Exception):
                    pass
            if not saved:
                save("stopped")

    async def _summarize(self, cid: str, upto: int, engine) -> None:
        """Fold the older part of a long conversation into a short summary. The full text stays on disk."""
        conv = self.conversations.get(cid)
        if not conv:
            return
        try:
            if engine.name == "api" and self.usage.key_left() <= 0:
                return
            res = await engine.quick(prompt.SUMMARY_SYSTEM, prompt.summary_request(conv, upto), SUMMARY_MODEL)
            self.usage.add(SUMMARY_MODEL, engine.name, res.usage)
            if res.stop != "end" or not res.text.strip():
                return
            fresh = self.conversations.get(cid)        # it may have grown while we summarized
            if not fresh:
                return
            fresh["summary"] = {"upto": upto, "text": res.text.strip(), "at": int(time.time() * 1000)}
            self.conversations.save(fresh, touch=False)
            log.info("Chat: summarized the first %d messages of %s", upto, cid)
        except Exception as e:
            log.warning("couldn't summarize conversation %s: %s", cid, e)

    async def stop(self) -> None:
        for t in list(self._tasks):
            t.cancel()
        if self.cli:
            shutil.rmtree(self.cli.workdir, ignore_errors=True)
