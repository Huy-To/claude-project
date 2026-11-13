"""Estimated liquidation levels from open-interest changes.

Idea (the same family of model public "liquidation heatmaps" use):
- When open interest rises during a bar, new positions were opened near that bar's
  typical price. Every contract has a long and a short side, so half the new notional
  is long and half is short.
- Those positions use a spread of leverage. A long at entry E with leverage L is
  liquidated near E * (1 - 1/L + mmr); a short near E * (1 + 1/L - mmr).
- When open interest falls, positions were closed: every tracked level shrinks
  in proportion.
- When price trades through a level, those positions are gone (liquidated), so the
  level is removed.

This is an estimate. Exchanges don't publish where positions actually sit.
"""

from __future__ import annotations

from dataclasses import dataclass

# (leverage, share of new notional). Tunable; roughly what retail perp traders use.
DEFAULT_LEVERAGE: list[tuple[float, float]] = [
    (5, 0.10), (10, 0.25), (20, 0.25), (25, 0.15), (50, 0.15), (100, 0.10),
]
DEFAULT_MMR = 0.005  # maintenance margin rate
MIN_USD = 1.0        # drop dust


@dataclass
class Level:
    usd: float
    start: int  # ms timestamp when the oldest still-open positions in this bin were opened


class LiqModel:
    def __init__(self, bin_size: float = 50.0,
                 leverage: list[tuple[float, float]] | None = None,
                 mmr: float = DEFAULT_MMR):
        self.bin_size = bin_size
        self.leverage = leverage or DEFAULT_LEVERAGE
        total = sum(w for _, w in self.leverage)
        self.leverage = [(lev, w / total) for lev, w in self.leverage]
        self.mmr = mmr
        self.long: dict[int, Level] = {}   # liquidated when price falls to it
        self.short: dict[int, Level] = {}  # liquidated when price rises to it
        self.liquidated_usd = {"long": 0.0, "short": 0.0}

    # ---- bins ----
    def _bin(self, price: float) -> int:
        return int(price // self.bin_size)

    def _mid(self, b: int) -> float:
        return (b + 0.5) * self.bin_size

    @staticmethod
    def _put(book: dict[int, Level], b: int, usd: float, ts: int) -> None:
        lvl = book.get(b)
        if lvl is None:
            book[b] = Level(usd, ts)
        else:
            lvl.usd += usd
            lvl.start = min(lvl.start, ts)

    # ---- updates ----
    def open_positions(self, ts: int, entry: float, usd: float) -> None:
        half = usd / 2
        for lev, w in self.leverage:
            self._put(self.long, self._bin(entry * (1 - 1 / lev + self.mmr)), half * w, ts)
            self._put(self.short, self._bin(entry * (1 + 1 / lev - self.mmr)), half * w, ts)

    def close_fraction(self, frac: float) -> None:
        keep = max(0.0, 1.0 - frac)
        for book in (self.long, self.short):
            for b in list(book):
                book[b].usd *= keep
                if book[b].usd < MIN_USD:
                    del book[b]

    def sweep(self, low: float, high: float) -> dict[str, float]:
        """Remove levels price traded through. Returns liquidated USD per side."""
        out = {"long": 0.0, "short": 0.0}
        for b in [b for b in self.long if self._mid(b) >= low]:
            out["long"] += self.long.pop(b).usd
        for b in [b for b in self.short if self._mid(b) <= high]:
            out["short"] += self.short.pop(b).usd
        for k in out:
            self.liquidated_usd[k] += out[k]
        return out

    def step(self, ts: int, high: float, low: float, close: float,
             d_oi_usd: float, oi_before_usd: float) -> dict[str, float]:
        """Apply one bar: sweep levels hit by its range, then apply the OI change."""
        hit = self.sweep(low, high)
        if d_oi_usd > 0:
            self.open_positions(ts, (high + low + close) / 3, d_oi_usd)
        elif d_oi_usd < 0 and oi_before_usd > 0:
            self.close_fraction(min(1.0, -d_oi_usd / oi_before_usd))
        return hit

    # ---- output ----
    def levels(self, price: float, window: float = 0.25, limit: int = 400) -> list[dict]:
        """Levels within +/- window of price, biggest first, capped at `limit`."""
        lo, hi = price * (1 - window), price * (1 + window)
        rows = []
        for side, book in (("long", self.long), ("short", self.short)):
            for b, lvl in book.items():
                p = self._mid(b)
                if lo <= p <= hi:
                    rows.append({
                        "side": side,
                        "lo": b * self.bin_size,
                        "hi": (b + 1) * self.bin_size,
                        "usd": round(lvl.usd),
                        "start": lvl.start,
                    })
        rows.sort(key=lambda r: r["usd"], reverse=True)
        return rows[:limit]

    def nearby(self, price: float, pct: float) -> dict[str, float]:
        """Total estimated liquidations within pct below (longs) and above (shorts)."""
        longs = sum(l.usd for b, l in self.long.items() if price * (1 - pct) <= self._mid(b) < price)
        shorts = sum(l.usd for b, l in self.short.items() if price < self._mid(b) <= price * (1 + pct))
        return {"long": longs, "short": shorts}
