"""Polymarket's BTC prediction markets, from the public Gamma API (no key, read-only).

Polymarket files its recurring BTC markets under series. The panels use these:

- hit     "What price will Bitcoin hit ...?"   odds BTC touches each price before the event ends
          (daily, weekly, monthly; the yearly event is filed under the monthly series)
- above   "Bitcoin above ___ on <date>?"       odds BTC is above each strike at noon ET that day
          (Binance BTC/USDT 1-minute close), one ladder per day for the coming week
- range   "Bitcoin price on <date>?"           odds for each $2,000 bucket at the same noon ET close
- updown  "Bitcoin Up or Down"                 the live 5m, 15m, 1h, 4h and daily windows

Odds are shown the way Polymarket shows them: the midpoint of the order book, or the last trade
when the spread is wider than 10 cents. Once a hit price is touched Polymarket resolves that
market and lists a new one for the same price ("from November 5"); the open one wins, and a price
with only resolved markets is reported as touched.

Nothing runs in the background: the page asks while a panel is open, and answers are cached
(ladders 30 s, up/down 5 s) so several tabs don't multiply the requests.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from datetime import datetime, timezone

import aiohttp

log = logging.getLogger("btcliq")
GAMMA = "https://gamma-api.polymarket.com"
EVENT_URL = "https://polymarket.com/event/"
LADDER_TTL = 30.0
UPDOWN_TTL = 5.0
WIDE_SPREAD = 0.10          # Polymarket shows the last trade instead of the midpoint above this

LADDER_SERIES = {
    "hit": ["bitcoin-hit-price-daily", "bitcoin-hit-price-weekly", "bitcoin-hit-price-monthly"],
    "above": ["btc-multi-strikes-weekly"],
    "range": ["bitcoin-neg-risk-weekly"],
}
UPDOWN_SERIES = [("5m", "btc-up-or-down-5m"), ("15m", "btc-up-or-down-15m"), ("1h", "btc-up-or-down-hourly"),
                 ("4h", "btc-up-or-down-4h"), ("1d", "btc-up-or-down-daily")]
HORIZON_TAGS = [("daily", "day"), ("weekly", "week"), ("monthly", "month"), ("yearly", "year")]
HORIZON_ORDER = {"day": 0, "week": 1, "month": 2, "year": 3}
_NUM = re.compile(r"\d[\d,]*(?:\.\d+)?")


# ---------- parsing (pure functions, tested without the network) ----------

def _f(v) -> float | None:
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return x if x == x else None   # drop NaN


def _list(v) -> list:
    """Gamma sends some arrays as JSON strings ("[\"Yes\", \"No\"]")."""
    if isinstance(v, list):
        return v
    try:
        out = json.loads(v or "[]")
    except (TypeError, ValueError):
        return []
    return out if isinstance(out, list) else []


def _ms(iso: str | None) -> int | None:
    if not iso:
        return None
    try:
        return int(datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp() * 1000)
    except ValueError:
        return None


def _nums(s: str) -> list[float]:
    return [float(x.replace(",", "")) for x in _NUM.findall(s or "")]


def outcome_prob(m: dict, outcome: str = "Yes") -> float | None:
    """Probability of one outcome, the way Polymarket displays it."""
    names = [str(x).lower() for x in _list(m.get("outcomes"))]
    i = names.index(outcome.lower()) if outcome.lower() in names else 0
    prices = [_f(x) for x in _list(m.get("outcomePrices"))]
    if m.get("closed"):
        return prices[i] if i < len(prices) else None
    bid, ask, last = _f(m.get("bestBid")), _f(m.get("bestAsk")), _f(m.get("lastTradePrice"))
    # bestBid / bestAsk / lastTradePrice quote the first outcome; outcomePrices has one per outcome.
    if bid is not None and ask is not None and ask - bid <= WIDE_SPREAD + 1e-9:
        p = (bid + ask) / 2
    elif last:
        p = last
    elif i < len(prices) and prices[i] is not None:
        return min(1.0, max(0.0, prices[i]))
    else:
        return None
    if i == 1:
        p = 1 - p
    return min(1.0, max(0.0, p))


def _horizon(ev: dict) -> str:
    tags = {t.get("slug") for t in ev.get("tags") or []}
    for tag, h in HORIZON_TAGS[::-1]:          # "yearly" wins over the series' "monthly"
        if tag in tags:
            return h
    return "week"


def _label(title: str) -> str:
    """The part of the title that differs between events: "in November", "on November 13"."""
    t = title or ""
    m = next((m for pat in (r"\bhit\s+(.*?)\??$", r"\babove\s+_+\s+(.*?)\??$", r"\bprice\s+(.*?)\??$")
              if (m := re.search(pat, t, re.I))), None)
    s = (m.group(1) if m else t).strip()
    return s[:1].upper() + s[1:]


def _market(m: dict, family: str) -> dict | None:
    label = (m.get("groupItemTitle") or "").strip()
    q = m.get("question") or ""
    out = {
        "label": label or q, "p": outcome_prob(m), "bid": _f(m.get("bestBid")), "ask": _f(m.get("bestAsk")),
        "chg24": _f(m.get("oneDayPriceChange")), "vol24": _f(m.get("volume24hr")) or 0.0,
        "volume": _f(m.get("volumeNum") or m.get("volume")) or 0.0, "closed": bool(m.get("closed")),
        "slug": m.get("slug"),
    }
    if family == "hit":
        n = _nums(label) or _nums(q)
        if not n:
            return None
        up = "↑" in label or "reach" in q.lower()
        down = "↓" in label or "dip" in q.lower()
        if up == down:
            return None
        out.update(strike=n[0], dir="up" if up else "down")
    elif family == "above":
        n = _nums(label) or _nums(q)
        if not n:
            return None
        out.update(strike=n[0])
    else:   # range: "<66,000", "66,000-68,000", ">84,000"
        n = _nums(label)
        if not n:
            return None
        if label.startswith("<"):
            lo, hi = None, n[0]
        elif label.startswith(">"):
            lo, hi = n[0], None
        elif len(n) >= 2:
            lo, hi = n[0], n[1]
        else:
            return None
        out.update(lo=lo, hi=hi, strike=lo if lo is not None else hi)
    return out


def _dedupe_hits(rows: list[dict]) -> list[dict]:
    """One row per (direction, price): the open market if there is one, else the resolved one."""
    best: dict[tuple, dict] = {}
    for r in rows:
        k = (r["dir"], r["strike"])
        cur = best.get(k)
        rank = (not r["closed"], r["vol24"])
        if cur is None or rank > (not cur["closed"], cur["vol24"]):
            best[k] = r
    out = []
    for r in best.values():
        if r["closed"]:
            if (r["p"] or 0) < 0.5:     # resolved No: nothing to show before the event ends
                continue
            r["touched"] = True
        out.append(r)
    return out


def _interp(points: list[tuple[float, float]], target: float) -> float | None:
    """Price where a curve of (price, probability) crosses `target`, linear between strikes."""
    for (x0, y0), (x1, y1) in zip(points, points[1:]):
        if y0 is None or y1 is None:
            continue
        if (y0 - target) * (y1 - target) <= 0 and y0 != y1:
            return x0 + (target - y0) * (x1 - x0) / (y1 - y0)
    return None


def summarize(family: str, markets: list[dict]) -> dict:
    """The few numbers worth reading first."""
    live = [m for m in markets if not m.get("touched") and m["p"] is not None]
    if family == "above":
        pts = sorted((m["strike"], m["p"]) for m in live)
        return {"median": _interp(pts, 0.5), "lo": _interp(pts, 0.84), "hi": _interp(pts, 0.16)}
    if family == "range":
        total = sum(m["p"] for m in live) or 0
        if not total:
            return {}
        top = max(live, key=lambda m: m["p"])
        cum, median = 0.0, None
        for m in sorted(live, key=lambda m: m["lo"] if m["lo"] is not None else float("-inf")):
            if m["lo"] is not None and m["hi"] is not None and cum + m["p"] / total >= 0.5 > cum:
                median = m["lo"] + (0.5 - cum) / (m["p"] / total) * (m["hi"] - m["lo"])
            cum += m["p"] / total
        return {"top": {"label": top["label"], "p": top["p"] / total}, "median": median, "sum": total}
    up = sorted((m["strike"], m["p"]) for m in live if m["dir"] == "up")
    down = sorted(((m["strike"], m["p"]) for m in live if m["dir"] == "down"), reverse=True)
    near = lambda pts: {"strike": pts[0][0], "p": pts[0][1]} if pts else None   # closest untouched price
    return {"up50": _interp(up, 0.5), "down50": _interp(down, 0.5), "near_up": near(up), "near_down": near(down)}


def parse_event(ev: dict, family: str) -> dict | None:
    rows = [r for r in (_market(m, family) for m in ev.get("markets") or []) if r]
    if family == "hit":
        rows = _dedupe_hits(rows)
    else:
        rows = [r for r in rows if not r["closed"]]
    if not rows:
        return None
    # Highest price first; an open-ended "<66,000" range sorts below "66,000-68,000".
    # At the same hit price, ▲ sorts above ▼.
    rows.sort(key=lambda r: (-(r["lo"] if r.get("lo") is not None else float("-inf")),) if family == "range"
              else (-r["strike"], r.get("dir") != "up"))
    return {
        "family": family, "slug": ev.get("slug"), "title": ev.get("title"), "label": _label(ev.get("title", "")),
        "horizon": _horizon(ev) if family == "hit" else "day",
        "end": _ms(ev.get("endDate")), "url": EVENT_URL + str(ev.get("slug", "")),
        "vol24": _f(ev.get("volume24hr")) or 0.0, "volume": _f(ev.get("volume")) or 0.0,
        "liquidity": _f(ev.get("liquidity")) or 0.0,
        "markets": rows, "summary": summarize(family, rows),
    }


def parse_updown(ev: dict, key: str, now_ms: int) -> dict | None:
    ms = ev.get("markets") or []
    if not ms:
        return None
    m = ms[0]
    start = _ms(ev.get("startTime") or m.get("eventStartTime"))
    end = _ms(ev.get("endDate") or m.get("endDate"))
    if end is None or end <= now_ms:
        return None
    return {"key": key, "slug": ev.get("slug"), "title": ev.get("title"), "url": EVENT_URL + str(ev.get("slug", "")),
            "start": start, "end": end, "live": start is None or start <= now_ms,
            "up": outcome_prob(m, "Up"), "bid": _f(m.get("bestBid")), "ask": _f(m.get("bestAsk")),
            "vol": _f(m.get("volumeNum") or m.get("volume")) or 0.0}


# ---------- fetching ----------

class Polymarket:
    def __init__(self, session: aiohttp.ClientSession):
        self.s = session
        self._cache: dict[str, tuple[float, dict]] = {}
        self._locks = {"ladders": asyncio.Lock(), "updown": asyncio.Lock()}

    async def _events(self, series: str, limit: int, ascending: bool = False) -> list[dict]:
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        params = {"series_slug": series, "closed": "false", "end_date_min": now, "limit": str(limit)}
        if ascending:
            params.update(order="endDate", ascending="true")
        async with self.s.get(GAMMA + "/events", params=params, timeout=aiohttp.ClientTimeout(total=15)) as r:
            r.raise_for_status()
            d = await r.json(content_type=None)
        return d if isinstance(d, list) else []

    async def _cached(self, key: str, ttl: float, build) -> dict:
        hit = self._cache.get(key)
        if hit and time.time() - hit[0] < ttl:
            return hit[1]
        async with self._locks[key]:
            hit = self._cache.get(key)
            if hit and time.time() - hit[0] < ttl:
                return hit[1]
            try:
                out = {**await build(), "ready": True, "error": "", "updated": int(time.time() * 1000)}
            except Exception as e:   # keep showing the last good answer, marked stale
                err = f"Polymarket didn't answer ({type(e).__name__}{': ' + str(e) if str(e) else ''})"
                log.warning("polymarket %s: %s", key, err)
                if hit:
                    out = {**hit[1], "error": err, "stale": True}
                else:
                    return {"ready": False, "error": err}
            self._cache[key] = (time.time(), out)
            return out

    async def ladders(self) -> dict:
        async def build():
            jobs = [(fam, s) for fam, ss in LADDER_SERIES.items() for s in ss]
            res = await asyncio.gather(*(self._events(s, 20) for _, s in jobs), return_exceptions=True)
            out: dict[str, list] = {fam: [] for fam in LADDER_SERIES}
            failed = [r for r in res if isinstance(r, Exception)]
            if len(failed) == len(res):
                raise failed[0]
            seen = set()
            for (fam, _), evs in zip(jobs, res):
                if isinstance(evs, Exception):
                    continue
                for ev in evs:
                    if not str(ev.get("title", "")).lower().startswith(("what price will bitcoin", "bitcoin")):
                        continue
                    p = parse_event(ev, fam)
                    if p and p["slug"] not in seen:
                        seen.add(p["slug"])
                        out[fam].append(p)
            out["hit"].sort(key=lambda e: (HORIZON_ORDER.get(e["horizon"], 9), e["end"] or 0))
            for fam in ("above", "range"):
                out[fam].sort(key=lambda e: e["end"] or 0)
            return {"ladders": out, "partial": bool(failed)}
        return await self._cached("ladders", LADDER_TTL, build)

    async def updown(self) -> dict:
        async def build():
            res = await asyncio.gather(*(self._events(s, 2, ascending=True) for _, s in UPDOWN_SERIES),
                                       return_exceptions=True)
            if all(isinstance(r, Exception) for r in res):
                raise res[0]
            now = int(time.time() * 1000)
            rows = []
            for (key, _), evs in zip(UPDOWN_SERIES, res):
                if isinstance(evs, Exception):
                    continue
                cands = [w for w in (parse_updown(ev, key, now) for ev in evs) if w]
                live = [w for w in cands if w["live"]] or cands
                if live:
                    rows.append(min(live, key=lambda w: w["end"]))
            return {"windows": rows}
        return await self._cached("updown", UPDOWN_TTL, build)
