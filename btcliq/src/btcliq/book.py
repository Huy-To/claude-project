"""Live order books from the venues reachable without an account, merged into one ladder.

Sizes are converted to BTC. Books are refreshed by REST polling (each venue at its own
pace) and only while a browser is actually looking at them.

Spot (Coinbase, Bitfinex) and perpetuals trade a little apart (the basis, often $20-60),
so a naive merge shows bids above asks. With align=True each venue's book is shifted so
its mid lines up with OKX's mid, which is the price the chart shows.
"""

from __future__ import annotations

import asyncio
import logging
import math
import time
from dataclasses import dataclass, field

import aiohttp

log = logging.getLogger("btcliq")

# (label, kind, seconds between refreshes)
VENUES = {
    "OKX": ("perp", 1.5),
    "Hyperliquid": ("perp", 1.0),
    "Deribit": ("perp", 1.0),
    "Kraken": ("perp", 2.0),
    "Coinbase": ("spot", 3.0),
    "Bitfinex": ("spot", 2.0),
}
ACTIVE_SECONDS = 20  # keep polling this long after the last browser request


@dataclass
class Book:
    bids: list[tuple[float, float]] = field(default_factory=list)  # (price, btc), best first
    asks: list[tuple[float, float]] = field(default_factory=list)
    ts: float = 0.0       # when fetched (unix seconds)
    error: str = ""

    @property
    def mid(self) -> float | None:
        if self.bids and self.asks:
            return (self.bids[0][0] + self.asks[0][0]) / 2
        return None


def _sorted(bids, asks) -> tuple[list, list]:
    bids = sorted(((p, s) for p, s in bids if s > 0 and p > 0), key=lambda x: -x[0])
    asks = sorted(((p, s) for p, s in asks if s > 0 and p > 0), key=lambda x: x[0])
    return bids, asks


class BookFeed:
    def __init__(self, session: aiohttp.ClientSession):
        self.s = session
        self.books: dict[str, Book] = {v: Book() for v in VENUES}
        self.last_request = 0.0
        self._task: asyncio.Task | None = None

    # ---------- fetchers: each returns (bids, asks) as [(price, btc)] ----------
    async def _json(self, url, **params):
        async with self.s.get(url, params=params or None) as r:
            r.raise_for_status()
            return await r.json(content_type=None)

    async def _okx(self):
        d = (await self._json("https://www.okx.com/api/v5/market/books-full",
                              instId="BTC-USDT-SWAP", sz=5000))["data"][0]
        conv = lambda rows: [(float(r[0]), float(r[1]) * 0.01) for r in rows]  # 0.01 BTC/contract
        return conv(d["bids"]), conv(d["asks"])

    async def _hyperliquid(self):
        # Hyperliquid returns only 20 levels per side. Ask at three precisions (≈$1, $10,
        # $100 buckets) and use the finest data available at each distance from the mid.
        async def l2(sig):
            async with self.s.post("https://api.hyperliquid.xyz/info",
                                   json={"type": "l2Book", "coin": "BTC", "nSigFigs": sig}) as r:
                r.raise_for_status()
                b, a = (await r.json(content_type=None))["levels"]
                return ([(float(x["px"]), float(x["sz"])) for x in b],
                        [(float(x["px"]), float(x["sz"])) for x in a])
        fine, mid, coarse = await asyncio.gather(l2(5), l2(4), l2(3))
        bids, asks = list(fine[0]), list(fine[1])
        for layer in (mid, coarse):
            low = min(p for p, _ in bids) if bids else math.inf
            high = max(p for p, _ in asks) if asks else 0
            bids += [(p, s) for p, s in layer[0] if p < low]
            asks += [(p, s) for p, s in layer[1] if p > high]
        return bids, asks

    async def _deribit(self):
        d = (await self._json("https://www.deribit.com/api/v2/public/get_order_book",
                              instrument_name="BTC-PERPETUAL", depth=1000))["result"]
        conv = lambda rows: [(float(p), float(usd) / float(p)) for p, usd in rows]  # USD contracts
        return conv(d["bids"]), conv(d["asks"])

    async def _kraken(self):
        d = (await self._json("https://futures.kraken.com/derivatives/api/v3/orderbook",
                              symbol="PF_XBTUSD"))["orderBook"]
        return [(float(p), float(q)) for p, q in d["bids"]], [(float(p), float(q)) for p, q in d["asks"]]

    async def _coinbase(self):
        d = await self._json("https://api.exchange.coinbase.com/products/BTC-USD/book", level=2)
        return [(float(p), float(q)) for p, q, *_ in d["bids"]], [(float(p), float(q)) for p, q, *_ in d["asks"]]

    async def _bitfinex(self):
        rows = await self._json("https://api-pub.bitfinex.com/v2/book/tBTCUSD/P0", len=250)
        bids = [(float(p), float(a)) for p, _n, a in rows if a > 0]
        asks = [(float(p), -float(a)) for p, _n, a in rows if a < 0]
        return bids, asks

    # ---------- polling ----------
    def touch(self) -> None:
        """A browser asked for the book: make sure polling is running."""
        self.last_request = time.time()
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._run())

    async def _poll_venue(self, name: str) -> None:
        fetch = getattr(self, "_" + name.lower())
        interval = VENUES[name][1]
        while time.time() - self.last_request < ACTIVE_SECONDS:
            t0 = time.time()
            try:
                bids, asks = await fetch()
                bids, asks = _sorted(bids, asks)
                self.books[name] = Book(bids, asks, time.time())
            except Exception as e:
                self.books[name].error = f"{type(e).__name__}: {e}"[:200]
                log.debug("book %s failed: %s", name, e)
            await asyncio.sleep(max(0.2, interval - (time.time() - t0)))

    async def _run(self) -> None:
        log.info("Order book feed started")
        await asyncio.gather(*(self._poll_venue(v) for v in VENUES))
        log.info("Order book feed paused (no viewers)")

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()

    # ---------- merge ----------
    def aggregate(self, step: float, ref_mid: float, rng: float = 0.02,
                  venues: list[str] | None = None, align: bool = True) -> dict:
        """Group all chosen venues into `step`-sized price buckets within ±rng of ref_mid."""
        venues = [v for v in (venues or list(VENUES)) if v in self.books]
        now = time.time()
        lo, hi = ref_mid * (1 - rng), ref_mid * (1 + rng)
        bids: dict[float, dict[str, float]] = {}
        asks: dict[float, dict[str, float]] = {}
        status = {}
        for v in venues:
            b = self.books[v]
            age = now - b.ts if b.ts else None
            status[v] = {"age": age, "error": b.error, "kind": VENUES[v][0],
                         "mid": b.mid, "levels": len(b.bids) + len(b.asks)}
            if not b.ts or age > 15 or b.mid is None:
                continue  # missing or stale: leave it out rather than show old orders
            shift = (ref_mid - b.mid) if align else 0.0
            status[v]["shift"] = shift
            for p, sz in b.bids:
                p += shift
                if p < lo:
                    break
                k = math.floor(p / step) * step
                bids.setdefault(k, {})[v] = bids.get(k, {}).get(v, 0) + sz
            for p, sz in b.asks:
                p += shift
                if p > hi:
                    break
                k = math.ceil(p / step) * step
                asks.setdefault(k, {})[v] = asks.get(k, {}).get(v, 0) + sz

        def rows(book: dict, reverse: bool) -> list:
            out = []
            for k in sorted(book, reverse=reverse):
                by = {v: round(s, 4) for v, s in book[k].items()}
                out.append([round(k, 2), round(sum(by.values()), 4), by])
            return out

        bid_rows, ask_rows = rows(bids, True), rows(asks, False)
        return {
            "mid": ref_mid,
            "step": step,
            "bids": bid_rows,
            "asks": ask_rows,
            "best_bid": bid_rows[0][0] if bid_rows else None,
            "best_ask": ask_rows[0][0] if ask_rows else None,
            "venues": status,
            "align": align,
        }
