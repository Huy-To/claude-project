"""Public market data from exchanges reachable without an account.

Binance and Bybit block US IPs, so this uses OKX, Hyperliquid, Deribit, Kraken
Futures and Coinbase. All endpoints are public; no keys needed.
"""

from __future__ import annotations

import asyncio
import logging
import time

import aiohttp

log = logging.getLogger("btcliq")

OKX = "https://www.okx.com"
OKX_SWAP = "BTC-USDT-SWAP"
OKX_CT_VAL = 0.01  # BTC per BTC-USDT-SWAP contract
# Chart timeframes: our name -> (OKX bar name, length in ms). 1D uses UTC days.
TIMEFRAMES = {
    "1m": ("1m", 60_000),
    "5m": ("5m", 300_000),
    "15m": ("15m", 900_000),
    "1H": ("1H", 3_600_000),
    "4H": ("4H", 14_400_000),
    "1D": ("1Dutc", 86_400_000),
}


def _f(x) -> float:
    return float(x)


class Exchanges:
    def __init__(self, session: aiohttp.ClientSession):
        self.s = session

    async def _get(self, url: str, **params):
        async with self.s.get(url, params=params or None) as r:
            r.raise_for_status()
            return await r.json(content_type=None)

    async def _okx(self, path: str, **params) -> list:
        body = await self._get(OKX + path, **params)
        if body.get("code") != "0":
            raise RuntimeError(f"OKX {path}: {body.get('msg')}")
        return body["data"]

    # ---------- OKX: candles ----------
    @staticmethod
    def _candle(row: list) -> dict:
        return {"time": int(row[0]), "open": _f(row[1]), "high": _f(row[2]),
                "low": _f(row[3]), "close": _f(row[4]), "volume": _f(row[6])}

    async def okx_candles_history(self, bar: str, start_ms: int) -> list[dict]:
        """Candles from start_ms to now, oldest first."""
        out: dict[int, dict] = {}
        for row in await self._okx("/api/v5/market/candles", instId=OKX_SWAP, bar=bar, limit=300):
            c = self._candle(row)
            out[c["time"]] = c
        after = min(out) if out else None
        while after and after > start_ms:
            rows = await self._okx("/api/v5/market/history-candles",
                                   instId=OKX_SWAP, bar=bar, limit=100, after=after)
            if not rows:
                break
            for row in rows:
                c = self._candle(row)
                out[c["time"]] = c
            after = min(int(r[0]) for r in rows)
            await asyncio.sleep(0.12)
        return [out[t] for t in sorted(out) if t >= start_ms]

    async def okx_latest_candles(self, bar: str) -> list[dict]:
        rows = await self._okx("/api/v5/market/candles", instId=OKX_SWAP, bar=bar, limit=2)
        return sorted((self._candle(r) for r in rows), key=lambda c: c["time"])

    # ---------- OKX: open interest history ----------
    async def okx_oi_history(self, period: str, start_ms: int) -> dict[int, float]:
        """{timestamp_ms: open interest in USD} for BTC-USDT-SWAP."""
        out: dict[int, float] = {}
        end = None
        while True:
            params = {"instId": OKX_SWAP, "period": period, "limit": 100}
            if end:
                params["end"] = end
            rows = await self._okx("/api/v5/rubik/stat/contracts/open-interest-history", **params)
            new = [r for r in rows if int(r[0]) not in out]
            for r in new:
                out[int(r[0])] = _f(r[3])
            if not new:
                break
            end = min(int(r[0]) for r in rows)
            if end <= start_ms:
                break
            await asyncio.sleep(0.45)  # this endpoint allows 5 requests / 2s
        return out

    # ---------- OKX: liquidations ----------
    async def okx_liquidations(self) -> list[dict]:
        rows = await self._okx("/api/v5/public/liquidation-orders",
                               instType="SWAP", uly="BTC-USDT", state="filled")
        out = []
        for group in rows:
            for d in group.get("details", []):
                px = _f(d["bkPx"])
                out.append({
                    "id": f"{d['ts']}-{d['sz']}-{d['bkPx']}-{d['posSide']}",
                    "time": int(d["ts"]),
                    "side": d["posSide"],  # "long" = a long got liquidated
                    "price": px,
                    "usd": _f(d["sz"]) * OKX_CT_VAL * px,
                })
        return out

    # ---------- open interest + price, all venues ----------
    async def _okx_oi(self) -> dict[str, tuple[float, float]]:
        out = {}
        for inst, name in ((OKX_SWAP, "OKX USDT"), ("BTC-USD-SWAP", "OKX USD")):
            d = (await self._okx("/api/v5/public/open-interest", instType="SWAP", instId=inst))[0]
            out[name] = (_f(d["oiUsd"]), 0.0)
        t = (await self._okx("/api/v5/market/ticker", instId=OKX_SWAP))[0]
        out["OKX USDT"] = (out["OKX USDT"][0], _f(t["last"]))
        return out

    async def _hyperliquid(self) -> dict[str, tuple[float, float]]:
        async with self.s.post("https://api.hyperliquid.xyz/info",
                               json={"type": "metaAndAssetCtxs"}) as r:
            r.raise_for_status()
            meta, ctxs = await r.json(content_type=None)
        i = [u["name"] for u in meta["universe"]].index("BTC")
        px = _f(ctxs[i]["markPx"])
        return {"Hyperliquid": (_f(ctxs[i]["openInterest"]) * px, px)}

    async def _deribit(self) -> dict[str, tuple[float, float]]:
        body = await self._get("https://www.deribit.com/api/v2/public/get_book_summary_by_instrument",
                               instrument_name="BTC-PERPETUAL")
        d = body["result"][0]
        return {"Deribit": (_f(d["open_interest"]), _f(d["mark_price"]))}  # OI already in USD

    async def _kraken(self) -> dict[str, tuple[float, float]]:
        t = (await self._get("https://futures.kraken.com/derivatives/api/v3/tickers/PF_XBTUSD"))["ticker"]
        px = _f(t["markPrice"])
        return {"Kraken": (_f(t["openInterest"]) * px, px)}

    async def _coinbase(self) -> dict[str, tuple[float, float]]:
        t = await self._get("https://api.exchange.coinbase.com/products/BTC-USD/ticker")
        return {"Coinbase spot": (0.0, _f(t["price"]))}

    # ---------- delayed venues via CoinGecko (Binance and Bybit block US connections) ----------
    COINGECKO = {"Binance": "binance_futures", "Bybit": "bybit"}
    COINGECKO_TTL = 120  # seconds between refreshes; CoinGecko itself updates every few minutes

    async def _coingecko_one(self, name: str, ex_id: str) -> tuple[float, float, int]:
        cache = getattr(self, "_cg_cache", {})
        self._cg_cache = cache
        hit = cache.get(name)
        if hit and time.time() - hit[0] < self.COINGECKO_TTL:
            return hit[1]
        body = await self._get(f"https://api.coingecko.com/api/v3/derivatives/exchanges/{ex_id}",
                               include_tickers="unexpired")
        perps = [t for t in body.get("tickers", [])
                 if t.get("base") == "BTC" and t.get("contract_type") == "perpetual"]
        if not perps:
            raise RuntimeError(f"no BTC perpetuals for {ex_id}")
        oi = sum(_f(t.get("open_interest_usd") or 0) for t in perps)
        main = max(perps, key=lambda t: _f(t.get("open_interest_usd") or 0))
        updated = max(int(t.get("last_traded") or 0) for t in perps) * 1000
        result = (oi, _f(main.get("last") or 0), updated)
        cache[name] = (time.time(), result)
        return result

    async def delayed(self) -> dict[str, tuple[float, float, int]]:
        """{venue: (open_interest_usd, price, data_time_ms)} from CoinGecko. Failures are left out."""
        names = list(self.COINGECKO)
        results = await asyncio.gather(*(self._coingecko_one(n, self.COINGECKO[n]) for n in names),
                                       return_exceptions=True)
        out = {}
        for name, r in zip(names, results):
            if isinstance(r, Exception):
                log.warning("CoinGecko %s unavailable: %s", name, r)
            else:
                out[name] = r
        return out

    async def snapshot(self) -> dict[str, tuple[float, float]]:
        """{venue: (open_interest_usd, price)}. Venues that fail are left out."""
        results = await asyncio.gather(
            self._okx_oi(), self._hyperliquid(), self._deribit(), self._kraken(), self._coinbase(),
            return_exceptions=True,
        )
        out: dict[str, tuple[float, float]] = {}
        for name, r in zip(("OKX", "Hyperliquid", "Deribit", "Kraken", "Coinbase"), results):
            if isinstance(r, Exception):
                log.warning("%s unavailable: %s", name, r)
            else:
                out.update(r)
        return out
