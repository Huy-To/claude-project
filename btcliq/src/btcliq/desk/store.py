"""What the chat keeps on disk: every conversation word for word, and a usage ledger.

Conversations are the lossless layer of memory. They cost no tokens until Claude searches
them. Each lives in its own JSON file under data/desk/conversations/.
"""

from __future__ import annotations

import json
import logging
import re
import secrets
import time
from datetime import datetime
from pathlib import Path

log = logging.getLogger("btcliq")

ID_RE = re.compile(r"^\d{8}-\d{6}-[0-9a-f]{4}$")
MAX_CARD_CHARS = 20_000         # a lookup card bigger than this is saved without its data
TITLE_CHARS = 60

# USD per million tokens: (input, output, cache read). Cache writes cost 1.25x input.
# From Anthropic's price list as of 2026-11-03; only used to total what the key fallback spends.
PRICES = {
    "claude-haiku-5-5": (0.10, 0.50, 0.01),
    "claude-sonnet-5-5": (2.00, 10.00, 0.10),
    "claude-opus-5-5": (4.00, 20.00, 0.20),
    "claude-fable-5-2": (8.00, 40.00, 0.20),
}
DEAREST = (8.00, 40.00, 0.20)   # an unknown model is priced as the dearest, so the cap errs on the safe side
WEB_SEARCH_USD = 0.01           # per search


def cost_usd(model: str, usage: dict) -> float:
    pin, pout, pread = PRICES.get(model, DEAREST)
    return (usage.get("in", 0) * pin + usage.get("cache_write", 0) * pin * 1.25
            + usage.get("cache_read", 0) * pread + usage.get("out", 0) * pout) / 1e6 \
        + usage.get("web_searches", 0) * WEB_SEARCH_USD


def _write_json(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    tmp.replace(path)      # atomic: a crash mid-write can't corrupt a conversation


def _read_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def title_from(text: str) -> str:
    t = " ".join(str(text or "").split())
    t = re.sub(r"^(please|can you|could you|hey|hi|claude)[,:]?\s+", "", t, flags=re.I)
    if len(t) > TITLE_CHARS:
        t = t[:TITLE_CHARS].rsplit(" ", 1)[0] + "…"
    return (t[:1].upper() + t[1:]) if t else "New chat"


class Conversations:
    def __init__(self, data_dir: Path):
        self.dir = Path(data_dir) / "desk" / "conversations"
        self._heads: dict[str, tuple[float, dict]] = {}

    def _path(self, cid: str) -> Path:
        if not ID_RE.match(str(cid or "")):
            raise KeyError(cid)
        return self.dir / f"{cid}.json"

    def new(self) -> dict:
        now = int(time.time() * 1000)
        cid = f"{datetime.now():%Y%m%d-%H%M%S}-{secrets.token_hex(2)}"
        return {"id": cid, "title": "", "created": now, "updated": now, "messages": [],
                "summary": None, "tokens": {"in": 0, "out": 0}}

    def get(self, cid: str) -> dict | None:
        try:
            p = self._path(cid)
        except KeyError:
            return None
        c = _read_json(p, None)
        return c if isinstance(c, dict) and c.get("id") == cid else None

    def save(self, conv: dict, touch: bool = True) -> None:
        """Write the conversation. touch=False keeps its place in the list (a summary isn't activity)."""
        if touch:
            conv["updated"] = int(time.time() * 1000)
        for m in conv.get("messages", []):
            for b in m.get("blocks") or []:
                if b.get("t") == "card" and b.get("data") is not None:
                    if len(json.dumps(b["data"], ensure_ascii=False, default=str)) > MAX_CARD_CHARS:
                        b["data"] = None
                        b["dropped"] = "The data behind this lookup was too large to keep. Ask again to see it."
        _write_json(self._path(conv["id"]), conv)

    def delete(self, cid: str) -> bool:
        try:
            self._path(cid).unlink()
            self._heads.pop(cid, None)
            return True
        except (KeyError, OSError):
            return False

    def rename(self, cid: str, title: str) -> dict | None:
        c = self.get(cid)
        if not c:
            return None
        c["title"] = " ".join(str(title or "").split())[:120] or c["title"]
        updated = c["updated"]
        _write_json(self._path(cid), c)      # renaming doesn't move it up the list
        c["updated"] = updated
        return self._head(c)

    @staticmethod
    def _head(c: dict) -> dict:
        msgs = c.get("messages") or []
        tok = c.get("tokens") or {}
        return {"id": c["id"], "title": c.get("title") or "New chat", "created": c.get("created"),
                "updated": c.get("updated"), "messages": len(msgs),
                "tokens": (tok.get("in") or 0) + (tok.get("out") or 0)}

    def _all(self) -> list[tuple[Path, dict]]:
        out = []
        if not self.dir.is_dir():
            return out
        for p in self.dir.glob("*.json"):
            if ID_RE.match(p.stem):
                c = _read_json(p, None)
                if isinstance(c, dict) and c.get("id") == p.stem:
                    out.append((p, c))
        return out

    def list(self, limit: int = 300) -> list[dict]:
        heads = []
        if self.dir.is_dir():
            for p in self.dir.glob("*.json"):
                if not ID_RE.match(p.stem):
                    continue
                try:
                    mt = p.stat().st_mtime
                except OSError:
                    continue
                hit = self._heads.get(p.stem)
                if not hit or hit[0] != mt:
                    c = _read_json(p, None)
                    if not isinstance(c, dict) or c.get("id") != p.stem:
                        continue
                    hit = (mt, self._head(c))
                    self._heads[p.stem] = hit
                heads.append(hit[1])
        heads.sort(key=lambda h: h.get("updated") or 0, reverse=True)
        return heads[:limit]

    def search(self, query: str, limit: int = 12, skip: str | None = None) -> list[dict]:
        """Conversations whose messages contain every word of the query, best first, with snippets."""
        terms = [t for t in re.findall(r"[a-z0-9$%.\-]+", str(query or "").lower()) if len(t) > 1]
        if not terms:
            return []
        out = []
        for _, c in self._all():
            if c["id"] == skip:
                continue
            title = (c.get("title") or "").lower()
            hits, score = [], 0
            for i, m in enumerate(c.get("messages") or []):
                text = m.get("text") or ""
                low = text.lower()
                n = sum(low.count(t) for t in terms)
                if n and all(t in low or t in title for t in terms):
                    at = min(low.find(t) for t in terms if t in low)
                    a = max(0, at - 90)
                    hits.append({"i": i, "role": m.get("role"),
                                 "text": ("…" if a else "") + " ".join(text[a:a + 260].split())})
                    score += n
            score += 3 * sum(t in title for t in terms)
            if hits or all(t in title for t in terms):
                out.append({"score": score, "id": c["id"], "title": c.get("title") or "New chat",
                            "date": datetime.fromtimestamp((c.get("updated") or 0) / 1000).strftime("%Y-%m-%d %H:%M"),
                            "updated": c.get("updated"), "hits": hits[:3]})
        out.sort(key=lambda r: (-r["score"], -(r["updated"] or 0)))
        for r in out:
            r.pop("score")
        return out[:limit]

    def read(self, cid: str, start: int = 0, count: int = 8) -> dict | None:
        """A stretch of one conversation, as plain text turns."""
        c = self.get(cid)
        if not c:
            return None
        msgs = c.get("messages") or []
        start = max(0, min(int(start), max(0, len(msgs) - 1)))
        part = msgs[start:start + max(1, min(int(count), 20))]
        return {"id": cid, "title": c.get("title"), "messages": len(msgs), "from": start,
                "date": datetime.fromtimestamp((c.get("created") or 0) / 1000).strftime("%Y-%m-%d %H:%M"),
                "turns": [{"i": start + k, "role": m.get("role"),
                           "when": datetime.fromtimestamp((m.get("ts") or 0) / 1000).strftime("%m-%d %H:%M"),
                           "text": (m.get("text") or "")[:4000]} for k, m in enumerate(part)]}


class Usage:
    """Plan usage as Claude Code reports it, tokens per day, and what the key fallback has cost."""

    def __init__(self, data_dir: Path, default_cap: float = 2.0):
        self.path = Path(data_dir) / "desk" / "usage.json"
        self.settings_path = Path(data_dir) / "desk" / "settings.json"
        d = _read_json(self.path, {})
        self.days: dict = d.get("days") or {}
        self.plan: dict = d.get("plan") or {}
        self.last: dict = d.get("last") or {}
        s = _read_json(self.settings_path, {})
        try:
            self.cap = max(0.0, float(s.get("key_cap_usd", default_cap)))
        except (TypeError, ValueError):
            self.cap = default_cap

    @staticmethod
    def _today() -> str:
        return datetime.now().strftime("%Y-%m-%d")

    def _save(self) -> None:
        keep = sorted(self.days)[-60:]
        self.days = {k: self.days[k] for k in keep}
        try:
            _write_json(self.path, {"days": self.days, "plan": self.plan, "last": self.last})
        except OSError as e:
            log.warning("couldn't save chat usage: %s", e)

    def today(self) -> dict:
        return self.days.setdefault(self._today(), {"answers": 0, "in": 0, "out": 0, "key_usd": 0.0, "key_answers": 0})

    def set_cap(self, usd: float) -> float:
        self.cap = max(0.0, min(float(usd), 500.0))
        _write_json(self.settings_path, {"key_cap_usd": self.cap})
        return self.cap

    def key_left(self) -> float:
        return max(0.0, self.cap - self.today().get("key_usd", 0.0))

    def note_plan(self, info: dict) -> None:
        """Remember the plan windows from a Claude Code rate_limit_event."""
        if not isinstance(info, dict):
            return
        win = info.get("unifiedWindows") or {}
        plan = {"status": info.get("status"), "at": int(time.time() * 1000), "type": info.get("rateLimitType")}
        for key, name in (("five_hour", "five_hour"), ("seven_day", "seven_day")):
            w = win.get(key)
            if isinstance(w, dict) and w.get("utilization") is not None:
                plan[name] = {"pct": round(float(w["utilization"]) * 100, 1),
                              "resets": int(w["resetsAt"]) * 1000 if w.get("resetsAt") else None,
                              "status": w.get("status")}
        if "five_hour" not in plan and info.get("resetsAt"):
            plan["resets"] = int(info["resetsAt"]) * 1000
        self.plan = {**self.plan, **plan}
        self._save()

    def add(self, model: str, engine: str, usage: dict) -> dict:
        """Record one answer. Returns the entry stored on the message."""
        tin = (usage.get("in", 0) or 0) + (usage.get("cache_read", 0) or 0) + (usage.get("cache_write", 0) or 0)
        tout = usage.get("out", 0) or 0
        entry = {"in": tin, "out": tout, "cache_read": usage.get("cache_read", 0) or 0, "model": model,
                 "engine": engine, "at": int(time.time() * 1000)}
        day = self.today()
        day["answers"] += 1
        day["in"] += tin
        day["out"] += tout
        if engine == "api":
            entry["usd"] = round(cost_usd(model, usage), 5)
            day["key_usd"] = round(day.get("key_usd", 0.0) + entry["usd"], 5)
            day["key_answers"] = day.get("key_answers", 0) + 1
        self.last = entry
        self._save()
        return entry

    def snapshot(self) -> dict:
        day = self.today()
        plan = dict(self.plan)
        now = time.time() * 1000
        for k in ("five_hour", "seven_day"):       # a window that has reset since we last heard starts again at 0
            w = plan.get(k)
            if isinstance(w, dict) and w.get("resets") and w["resets"] < now:
                plan[k] = {**w, "pct": 0.0, "stale": True}
        return {"plan": plan, "today": {"tokens": day["in"] + day["out"], "in": day["in"], "out": day["out"],
                                        "answers": day["answers"]},
                "key": {"usd": round(day.get("key_usd", 0.0), 4), "cap": self.cap,
                        "answers": day.get("key_answers", 0)},
                "last": self.last}
