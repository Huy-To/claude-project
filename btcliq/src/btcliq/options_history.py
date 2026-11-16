"""Price and volatility history of one option contract, for the drawer chart.

GET /api/options/history?instId=...&range=1H|1D|1W|All answers with

    {"bars":   [{"t": ms, "mark", "bid", "ask", "iv"}, ...],     oldest first
     "trades": [{"time": ms, "px", "sz", "side"}, ...]}          newest first; left out when unknown

Where each number comes from:
- bid, ask, IV and mark: the recorder (options_store.py), for the time it has run.
- mark before that: OKX mark-price candles.
- IV before that: rebuilt from the mark with Black-76 the way OKX does it (zero rate, 365-day
  year, coin-margined prices converted with the forward). The forward at each bar comes from
  put-call parity on the paired contract's mark candle; if the pair is missing, from the index
  candle times today's forward/index ratio. Those bars carry "iv_src": "rebuilt": candle prices
  are rounded, so the rebuilt IV is approximate, and it is left null where rounding alone
  could move it by more than MAX_IV_ERR.
"""

from __future__ import annotations

import asyncio
import math
import re
import time

MARK_CANDLES = "/api/v5/market/history-mark-price-candles"
INDEX_CANDLES = "/api/v5/market/history-index-candles"
TRADES = "/api/v5/market/trades"

BAR_MS = {"1m": 60_000, "5m": 300_000, "15m": 900_000, "1H": 3_600_000, "4H": 14_400_000, "1Dutc": 86_400_000}
RANGES = {"1H": (3_600_000, "1m"), "1D": (86_400_000, "5m"), "1W": (7 * 86_400_000, "1H")}
ALL_MAX_BARS = 600       # "All" picks the smallest bar that covers the contract's life in this many bars
PAGE = 300               # candles asked per request (OKX may return fewer)
MAX_PAGES = 8
OKX_TTL = 45             # seconds an OKX answer is reused (the page asks again every 10 s)
STORE_TTL = 8            # seconds a recorder read is reused
TRADES_MAX = 500
YEAR_MS = 365 * 86_400_000
MAX_IV_ERR = 0.05        # a rebuilt IV is dropped when price rounding alone could move it this much
NAME = re.compile(r"BTC-USD(_UM)?-\d{6}-\d+(\.\d+)?-[CP]")


def plan(rng: str, now_ms: int, list_ms: int | None = None) -> tuple[int, str]:
    """(since_ms, OKX bar) for a range. Unknown ranges are treated as 1D."""
    if rng == "All":
        since = list_ms or 0
        age = now_ms - since if since else float("inf")
        bar = next((b for b, ms in BAR_MS.items() if age / ms <= ALL_MAX_BARS), "1Dutc")
        return since, bar
    span, bar = RANGES.get(rng, RANGES["1D"])
    return now_ms - span, bar


# ---------- Black-76 ----------
def _cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def black76(fwd: float, strike: float, t: float, vol: float, cp: str) -> float:
    """Undiscounted Black-76 price in USD. t in years."""
    if t <= 0 or vol <= 0:
        return max(0.0, fwd - strike if cp == "C" else strike - fwd)
    sd = vol * math.sqrt(t)
    d1 = (math.log(fwd / strike) + 0.5 * sd * sd) / sd
    d2 = d1 - sd
    return fwd * _cdf(d1) - strike * _cdf(d2) if cp == "C" else strike * _cdf(-d2) - fwd * _cdf(-d1)


def vega(fwd: float, strike: float, t: float, vol: float) -> float:
    """USD price change per 1.00 of volatility (i.e. per 100 vol points)."""
    if t <= 0 or vol <= 0:
        return 0.0
    sd = vol * math.sqrt(t)
    d1 = (math.log(fwd / strike) + 0.5 * sd * sd) / sd
    return fwd * math.exp(-0.5 * d1 * d1) / math.sqrt(2 * math.pi) * math.sqrt(t)


def implied_vol(px: float, fwd: float, strike: float, t: float, cp: str) -> float | None:
    """Volatility (fraction) that makes Black-76 give this USD price, or None if none does."""
    if not (px and fwd and strike) or px <= 0 or fwd <= 0 or strike <= 0 or t <= 0:
        return None
    lo, hi = 1e-4, 10.0
    if px <= black76(fwd, strike, t, lo, cp) or px >= black76(fwd, strike, t, hi, cp):
        return None
    for _ in range(60):
        mid = (lo + hi) / 2
        if black76(fwd, strike, t, mid, cp) < px:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


def parity_forward(call: float, put: float, strike: float, coin: bool) -> float | None:
    """Forward implied by a call and a put of the same strike and expiry (marks obey parity).
    Coin-margined prices are in BTC per 1 BTC: (call - put) * F = F - K."""
    if coin:
        d = 1.0 - (call - put)
        return strike / d if d > 0 else None
    f = strike + call - put
    return f if f > 0 else None


def rebuilt_iv(c: dict, t_ms: int, mark: float | None, pair: float | None = None,
               index: float | None = None, basis: float | None = None, step: float = 0.0) -> float | None:
    """IV of contract c at time t_ms from its mark. `pair` is the mark of the same strike's
    other type at that time; without it the forward is index * basis. `step` is the rounding
    of the marks (in the contract's own price unit)."""
    if mark is None or mark <= 0:
        return None
    coin = c["family"] == "BTC-USD"
    t = (c["exp_ms"] - t_ms) / YEAR_MS
    fwd = None
    if pair is not None:
        fwd = parity_forward(*((mark, pair) if c["type"] == "C" else (pair, mark)), c["strike"], coin)
    elif index and basis:
        fwd = index * basis
    if not fwd or t <= 0:
        return None
    iv = implied_vol(mark * fwd if coin else mark, fwd, c["strike"], t, c["type"])
    if iv is None:
        return None
    if step:
        v = vega(fwd, c["strike"], t, iv)
        err = step * (fwd if coin else 1.0)     # both marks are rounded: about one step in total
        if v <= 0 or err / v > MAX_IV_ERR:
            return None
    return round(iv, 4)


# ---------- merging ----------
def _step(raw: list[str]) -> float:
    """Rounding step of a list of price strings: 10^-(most decimals seen)."""
    return 10.0 ** -max((len(s.partition(".")[2]) for s in raw), default=0)


def build_bars(c: dict, *, marks: dict[int, float], pair: dict[int, float] | None, index: dict[int, float] | None,
               basis: float | None, recorded: list[dict], bar_ms: int, since_ms: int, now_ms: int,
               step: float = 0.0) -> list[dict]:
    """Recorded samples where there are any, mark candles (with rebuilt IV) for the other bars.
    `marks`, `pair` and `index` map a candle's opening time to its close; `recorded` holds the
    recorder's rows oldest first (already one per bar, or raw for the 1H range)."""
    by_bar: dict[int, list[dict]] = {}
    for r in recorded:
        by_bar.setdefault(r["t"] // bar_ms * bar_ms, []).append(r)
    bars: list[dict] = []
    for ts in sorted(set(marks) | set(by_bar)):
        rows = by_bar.get(ts)
        if rows:
            for r in rows:
                bars.append({"t": r["t"], "mark": r["mark"] if r["mark"] is not None else marks.get(ts),
                             "bid": r["bid"], "ask": r["ask"], "iv": r["iv"]})
            continue
        t = min(ts + bar_ms, now_ms)          # a candle's close belongs to the end of its bar
        if t < since_ms:
            continue
        bar = {"t": t, "mark": marks[ts], "bid": None, "ask": None, "iv": None}
        has_pair = pair is not None and ts in pair
        iv = rebuilt_iv(c, t, marks[ts], pair=pair[ts] if has_pair else None,
                        index=None if has_pair or not index else index.get(ts), basis=basis, step=step)
        if iv is not None:
            bar["iv"], bar["iv_src"] = iv, "rebuilt"
        bars.append(bar)
    bars.sort(key=lambda b: b["t"])
    return bars


class OptionHistory:
    def __init__(self, feed, store=None):
        self.feed = feed            # OptionsFeed: live chain and OKX requests
        self.store = store          # OptionStore, or None when recording is off
        self._cache: dict[tuple, tuple[float, int, object]] = {}   # key -> (fetched at, since_ms, value)

    def _hit(self, key: tuple, ttl: float, since_ms: int):
        hit = self._cache.get(key)
        return hit[2] if hit and time.time() - hit[0] < ttl and hit[1] <= since_ms else None

    def _put(self, key: tuple, since_ms: int, value) -> None:
        if len(self._cache) > 256:
            cut = time.time() - OKX_TTL
            self._cache = {k: v for k, v in self._cache.items() if v[0] >= cut}
        self._cache[key] = (time.time(), since_ms, value)

    async def _candles(self, path: str, inst_id: str, bar: str, since_ms: int) -> tuple[dict[int, float], float]:
        """Closes by opening time back to since_ms, plus the prices' rounding step. Cached."""
        key = (path, inst_id, bar)
        hit = self._hit(key, OKX_TTL, since_ms)
        if hit is not None:
            return hit
        closes: dict[int, float] = {}
        raw: list[str] = []
        after = None
        for _ in range(MAX_PAGES):
            params = {"instId": inst_id, "bar": bar, "limit": PAGE}
            if after is not None:
                params["after"] = after
            rows = await self.feed._okx(path, **params)
            if not rows:
                break
            for r in rows:
                closes[int(r[0])] = float(r[4])
                raw.append(r[4])
            after = int(rows[-1][0])                 # newest first: the last row is the oldest
            if after - BAR_MS[bar] <= since_ms:
                break
        out = ({ts: v for ts, v in closes.items() if ts + BAR_MS[bar] >= since_ms}, _step(raw))
        self._put(key, since_ms, out)
        return out

    async def _trades(self, inst_id: str, since_ms: int) -> list[dict] | None:
        """Single trades back to since_ms, newest first; None when they can't be had in full."""
        key = (TRADES, inst_id)
        rows = self._hit(key, OKX_TTL, 0)
        if rows is None:
            try:
                rows = await self.feed._okx(TRADES, instId=inst_id, limit=TRADES_MAX)
            except Exception:
                return None
            self._put(key, 0, rows)
        trades = [{"time": int(t["ts"]), "px": float(t["px"]), "sz": float(t["sz"]), "side": t["side"]} for t in rows]
        if len(trades) >= TRADES_MAX and trades[-1]["time"] > since_ms:
            return None                              # older trades exist that this request didn't reach
        return [t for t in trades if t["time"] >= since_ms]

    async def _recorded(self, inst_id: str, since_ms: int, bucket_ms: int) -> list[dict]:
        if not self.store:
            return []
        key = ("store", inst_id, bucket_ms)
        rows = self._hit(key, STORE_TTL, since_ms)
        if rows is None:
            rows = await asyncio.to_thread(self.store.read, inst_id, since_ms, None, bucket_ms)
            self._put(key, since_ms, rows)
        return [r for r in rows if r["t"] >= since_ms]

    async def history(self, inst_id: str, rng: str = "1D") -> dict:
        inst_id = inst_id.strip().upper()
        if not NAME.fullmatch(inst_id):
            return {"bars": [], "trades": []}
        now = int(time.time() * 1000)
        family = "BTC-USD_UM" if inst_id.startswith("BTC-USD_UM-") else "BTC-USD"
        snap = await self.feed.chain(family)
        c = next((x for x in snap["contracts"] if x["name"] == inst_id), None)
        if c is None:
            # Not trading now. If it used to and the recorder saw it, show what was recorded.
            known = await asyncio.to_thread(self.store.contract, inst_id) if self.store else None
            if not known:
                return {"bars": [], "trades": []}
            since, bar = plan(rng, min(now, known["exp_ms"]), None)   # ranges count back from its expiry
            if rng == "All":
                bar = "4H"                           # its listing time is no longer known
            raw = rng == "1H"
            rec = await self._recorded(inst_id, since, 0 if raw else BAR_MS[bar])
            return {"bars": [{"t": r["t"], "mark": r["mark"], "bid": r["bid"], "ask": r["ask"], "iv": r["iv"]}
                             for r in rec], "trades": [], "bar": bar}

        since, bar = plan(rng, now, c.get("list_ms"))
        raw = rng == "1H"                            # the shortest range shows every recorded sample
        pair_id = inst_id[:-1] + ("P" if inst_id.endswith("C") else "C")
        marks, pair, trades, rec = await asyncio.gather(
            self._candles(MARK_CANDLES, inst_id, bar, since),
            self._candles(MARK_CANDLES, pair_id, bar, since),
            self._trades(inst_id, since),
            self._recorded(inst_id, since, 0 if raw else BAR_MS[bar]),
            return_exceptions=True,
        )
        if isinstance(rec, Exception):
            rec = []
        if isinstance(marks, Exception):
            if not rec:
                raise marks
            marks = ({}, 0.0)
        closes, step = marks
        pair_closes = None if isinstance(pair, Exception) else pair[0]
        index, basis = None, None
        if any(ts not in (pair_closes or {}) for ts in closes) and c.get("fwd") and snap.get("index"):
            try:                                     # no paired candle for some bars: fall back to the index
                index = (await self._candles(INDEX_CANDLES, "BTC-USD", bar, since))[0]
                basis = c["fwd"] / snap["index"]
            except Exception:
                index = None
        out = {"bars": build_bars(c, marks=closes, pair=pair_closes, index=index, basis=basis, recorded=rec,
                                  bar_ms=BAR_MS[bar], since_ms=since, now_ms=now, step=step),
               "bar": bar}
        if not isinstance(trades, Exception) and trades is not None:
            out["trades"] = trades
        return out
