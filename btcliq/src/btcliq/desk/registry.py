"""Lookups: the named, typed questions Claude can ask the running app.

A lookup is a small async function plus a description of its arguments. The same list is
offered to Claude two ways: as MCP tools (Claude Code, other sessions) and as API tools
(the key fallback). Arguments are checked here, because neither path can be trusted to
send exactly what the description asks for.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

log = logging.getLogger("btcliq")

MAX_RESULT_CHARS = 24_000   # about 6-8k tokens: one lookup can't flood the conversation
DEFAULT_TIMEOUT = 30.0


class LookupFailed(Exception):
    """A lookup couldn't answer. The message goes back to Claude, so it says what to change."""


@dataclass(frozen=True)
class Param:
    type: str                       # string | number | integer | boolean | array | object
    description: str = ""
    enum: tuple = ()
    default: Any = None
    minimum: float | None = None
    maximum: float | None = None
    items: dict | None = None       # JSON schema of one array item

    def schema(self) -> dict:
        s: dict = {"type": self.type}
        if self.description:
            s["description"] = self.description
        if self.enum:
            s["enum"] = list(self.enum)
        if self.type == "array":
            s["items"] = self.items or {"type": "string"}
        return s


@dataclass
class Lookup:
    name: str
    title: str                      # shown on the lookup's card
    description: str                # what Claude reads when deciding whether to call it
    fn: Callable[..., Awaitable[Any]]
    params: dict[str, Param] = field(default_factory=dict)
    required: tuple[str, ...] = ()
    scope: str = "read"             # "read": any client; "chat": only the in-app chat
    group: str = "market"
    untrusted: bool = False         # the result carries text written by strangers
    timeout: float = DEFAULT_TIMEOUT
    card: bool = True               # False: the page shows something of its own instead of a lookup card

    def schema(self) -> dict:
        return {"type": "object", "properties": {k: p.schema() for k, p in self.params.items()},
                "required": list(self.required), "additionalProperties": False}


@dataclass
class LookupResult:
    name: str
    title: str
    args: dict
    ok: bool
    data: Any = None                # what the card shows
    text: str = ""                  # what Claude reads
    ms: int = 0
    error: str = ""
    untrusted: bool = False
    group: str = ""


def tidy(x: Any) -> Any:
    """Round floats to 6 significant digits, all the way down: fewer tokens, same meaning."""
    if isinstance(x, float):
        if x != x or x in (float("inf"), float("-inf")):
            return None
        return float(f"{x:.6g}")
    if isinstance(x, dict):
        return {str(k): tidy(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [tidy(v) for v in x]
    return x


def _dumps(x: Any) -> str:
    return json.dumps(x, separators=(",", ":"), ensure_ascii=False, default=str)


def pack(data: Any, limit: int = MAX_RESULT_CHARS) -> tuple[Any, str]:
    """(data for the card, text for Claude), cut down if it would be too long to read."""
    if isinstance(data, str):
        if len(data) <= limit:
            return data, data
        cut = data[:limit].rsplit("\n", 1)[0]
        return cut, cut + f"\n[cut: {len(data) - len(cut):,} more characters. Ask for a narrower slice.]"
    data = tidy(data)
    text = _dumps(data)
    if len(text) <= limit:
        return data, text
    # A table: keep as many rows as fit and say how many were dropped.
    if isinstance(data, dict) and isinstance(data.get("rows"), list) and data["rows"]:
        rows, lo, hi = data["rows"], 0, len(data["rows"])
        while lo < hi:
            mid = (lo + hi + 1) // 2
            trial = {**data, "rows": rows[:mid], "truncated": "x" * 100}
            if len(_dumps(trial)) <= limit:
                lo = mid
            else:
                hi = mid - 1
        if lo:
            out = {**data, "rows": rows[:lo],
                   "truncated": f"showing {lo} of {len(rows)} rows; narrow the request (limit, filters) for the rest"}
            return out, _dumps(out)
    cut = text[:limit]
    return ({"truncated": "result too large to show in full", "text": cut},
            cut + f"\n[cut: {len(text) - limit:,} more characters. Ask for a narrower slice.]")


def _coerce(name: str, p: Param, v: Any) -> Any:
    t = p.type
    try:
        if t == "integer":
            if isinstance(v, bool):
                raise ValueError
            v = int(float(v))
        elif t == "number":
            if isinstance(v, bool):
                raise ValueError
            v = float(v)
        elif t == "boolean":
            if isinstance(v, str):
                if v.strip().lower() not in ("true", "false", "1", "0", "yes", "no"):
                    raise ValueError
                v = v.strip().lower() in ("true", "1", "yes")
            v = bool(v)
        elif t == "string":
            if isinstance(v, (dict, list)):
                raise ValueError
            v = str(v).strip()
        elif t == "array":
            if isinstance(v, str):
                v = [x.strip() for x in v.split(",") if x.strip()]
            if not isinstance(v, list):
                raise ValueError
        elif t == "object":
            if not isinstance(v, dict):
                raise ValueError
    except (TypeError, ValueError):
        raise LookupFailed(f"'{name}' must be {'an' if t[0] in 'aeio' else 'a'} {t}") from None
    if p.enum:
        match = next((e for e in p.enum if str(e).lower() == str(v).lower()), None)
        if match is None:
            raise LookupFailed(f"'{name}' must be one of: {', '.join(map(str, p.enum))}")
        v = match
    if t in ("integer", "number"):
        if p.minimum is not None and v < p.minimum:
            v = type(v)(p.minimum)
        if p.maximum is not None and v > p.maximum:
            v = type(v)(p.maximum)
    return v


def clean_args(lookup: Lookup, raw: Any) -> dict:
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        raise LookupFailed("arguments must be a JSON object")
    unknown = [k for k in raw if k not in lookup.params]
    if unknown:
        takes = ", ".join(lookup.params) or "no arguments"
        raise LookupFailed(f"unknown argument {', '.join(map(repr, unknown))}; {lookup.name} takes: {takes}")
    out = {}
    for k, p in lookup.params.items():
        v = raw.get(k)
        if v is None or v == "":
            if k in lookup.required:
                raise LookupFailed(f"'{k}' is required")
            out[k] = p.default
        else:
            out[k] = _coerce(k, p, v)
    return out


class Registry:
    def __init__(self):
        self._items: dict[str, Lookup] = {}

    def lookup(self, name: str, *, title: str, description: str, params: dict[str, Param] | None = None,
               required: tuple[str, ...] = (), scope: str = "read", group: str = "market",
               untrusted: bool = False, timeout: float = DEFAULT_TIMEOUT, card: bool = True):
        def wrap(fn):
            self._items[name] = Lookup(name, title, " ".join(description.split()), fn, params or {}, required,
                                       scope, group, untrusted, timeout, card)
            return fn
        return wrap

    def get(self, name: str) -> Lookup | None:
        return self._items.get(name)

    def visible(self, chat: bool) -> list[Lookup]:
        """Sorted by name, so the list Claude sees is identical from one question to the next."""
        return [l for _, l in sorted(self._items.items()) if chat or l.scope == "read"]

    def mcp_tools(self, chat: bool) -> list[dict]:
        return [{"name": l.name, "title": l.title, "description": l.description, "inputSchema": l.schema()}
                for l in self.visible(chat)]

    def api_tools(self, chat: bool = True) -> list[dict]:
        return [{"name": l.name, "description": l.description, "input_schema": l.schema(),
                 "eager_input_streaming": True} for l in self.visible(chat)]

    async def call(self, name: str, raw_args: Any, ctx) -> LookupResult:
        l = self._items.get(name)
        chat = getattr(ctx, "chat", None) is not None
        if l is None or (l.scope == "chat" and not chat):
            return LookupResult(name, name, {}, False, error=f"no lookup named {name!r}",
                                text=f"Error: no lookup named {name!r}.")
        t0 = time.monotonic()
        args: dict = {}
        try:
            args = clean_args(l, raw_args)
            data = await asyncio.wait_for(l.fn(ctx, **args), l.timeout)
            shown, text = pack(data)
            return LookupResult(l.name, l.title, _shown_args(args), True, shown, text,
                                int((time.monotonic() - t0) * 1000), untrusted=l.untrusted, group=l.group)
        except LookupFailed as e:
            err = str(e)
        except asyncio.TimeoutError:
            err = f"timed out after {l.timeout:.0f}s"
        except asyncio.CancelledError:
            raise
        except Exception as e:  # a lookup must never take the conversation down with it
            log.warning("lookup %s failed: %s: %s", name, type(e).__name__, e)
            err = f"{type(e).__name__}: {e}"[:400]
        shown = args or (raw_args if isinstance(raw_args, dict) else {})
        return LookupResult(l.name, l.title, _shown_args(shown), False, error=err, text=f"Error: {err}",
                            ms=int((time.monotonic() - t0) * 1000), group=l.group)


def _shown_args(args: dict) -> dict:
    """Arguments worth showing on the card: the ones that were set."""
    return {k: v for k, v in args.items() if v not in (None, "", [], {})}
