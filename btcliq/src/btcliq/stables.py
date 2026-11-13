"""Global stablecoin supply, from DefiLlama's public stablecoin API (no key).

- /stablecoins?includePrices=true  every stablecoin: supply now / 1d / 7d / 30d ago, price, chains
- /stablecoincharts/all            daily total supply since 2017 (all pegs, converted to USD)
- /stablecoincharts/all?stablecoin=ID  daily supply of one coin
- /stablecoinchains                supply per blockchain

DefiLlama updates these roughly hourly, so this refreshes every 5 minutes. Supply doesn't
move tick by tick like a price.
"""

from __future__ import annotations

import asyncio
import logging
import time

import aiohttp

log = logging.getLogger("btcliq")
API = "https://stablecoins.llama.fi"
REFRESH_SECONDS = 300
HISTORY_TTL = 3600
DEPEG_PCT = 0.5          # flag USD stablecoins trading more than 0.5% off $1
DEPEG_MIN_SUPPLY = 50e6  # ...if they have at least $50M in circulation
BTC_SUPPLY_FALLBACK = 19.95e6


def _usd(asset: dict, key: str) -> float | None:
    """Supply in USD. Non-USD pegs are converted with the coin's USD price."""
    d = asset.get(key) or {}
    peg = asset.get("pegType", "peggedUSD")
    v = d.get(peg)
    if v is None:
        return None
    if peg == "peggedUSD":
        return float(v)
    price = asset.get("price")
    return float(v) * float(price) if isinstance(price, (int, float)) and price > 0 else None


class Stablecoins:
    def __init__(self, session: aiohttp.ClientSession):
        self.s = session
        self.assets: list[dict] = []
        self.chains: list[dict] = []
        self.history: list[list] = []      # [[ms, total_usd], ...]
        self.summary: dict | None = None
        self.btc: dict = {}
        self._hist_at = 0.0
        self._coin_hist: dict[str, tuple[float, list]] = {}
        self._task: asyncio.Task | None = None
        self.on_update = None
        self.error = ""

    async def _get(self, path: str, **params):
        async with self.s.get(API + path, params=params or None,
                              timeout=aiohttp.ClientTimeout(total=40)) as r:
            r.raise_for_status()
            return await r.json(content_type=None)

    # ---------- refresh ----------
    async def refresh(self, btc_price: float | None = None) -> None:
        jobs = [self._get("/stablecoins", includePrices="true"), self._get("/stablecoinchains")]
        need_hist = not self.history or time.time() - self._hist_at > HISTORY_TTL
        if need_hist:
            jobs.append(self._get("/stablecoincharts/all"))
        res = await asyncio.gather(*jobs)
        raw_assets, raw_chains = res[0]["peggedAssets"], res[1]
        if need_hist:
            self.history = [[int(p["date"]) * 1000,
                             sum(float(v) for v in (p.get("totalCirculatingUSD") or {}).values())]
                            for p in res[2]]
            self._hist_at = time.time()

        assets = []
        for a in raw_assets:
            now = _usd(a, "circulating")
            if not now or now < 1:
                continue
            assets.append({
                "id": a["id"], "name": a["name"], "symbol": a["symbol"],
                "mechanism": (a.get("pegMechanism") or "").replace("crytpo", "crypto"),  # fix a source typo
                "peg": (a.get("pegType") or "").replace("pegged", ""),
                "price": a.get("price") if isinstance(a.get("price"), (int, float)) else None,
                "supply": now,
                "d1": _usd(a, "circulatingPrevDay"), "d7": _usd(a, "circulatingPrevWeek"),
                "d30": _usd(a, "circulatingPrevMonth"),
                "chains": len(a.get("chains") or []),
                "top_chains": sorted(
                    ((c, float((v.get("current") or {}).get(a.get("pegType", "peggedUSD"), 0) or 0))
                     for c, v in (a.get("chainCirculating") or {}).items()),
                    key=lambda x: -x[1])[:3],
            })
        assets.sort(key=lambda x: -x["supply"])
        self.assets = assets
        self.chains = sorted(
            ({"name": c["name"], "supply": sum(float(v) for v in (c.get("totalCirculatingUSD") or {}).values())}
             for c in raw_chains), key=lambda x: -x["supply"])
        self.btc = await self._btc(btc_price)
        self.summary = self._summarize()
        self.error = ""

    async def _btc(self, price: float | None) -> dict:
        """BTC market cap for the stablecoin supply ratio."""
        try:
            async with self.s.get("https://api.coingecko.com/api/v3/simple/price",
                                  params={"ids": "bitcoin", "vs_currencies": "usd", "include_market_cap": "true"}) as r:
                d = (await r.json(content_type=None))["bitcoin"]
            return {"price": d["usd"], "mcap": d["usd_market_cap"], "source": "CoinGecko"}
        except Exception:
            if price:
                return {"price": price, "mcap": price * BTC_SUPPLY_FALLBACK, "source": "estimate (19.95M BTC)"}
            return {}

    def _summarize(self) -> dict:
        total = sum(a["supply"] for a in self.assets)
        tot = lambda k: sum(a[k] for a in self.assets if a[k] is not None)
        # Compare like with like: only coins that report a value for that lookback.
        def change(k):
            now = sum(a["supply"] for a in self.assets if a[k] is not None)
            then = tot(k)
            return {"abs": now - then, "pct": (now - then) / then * 100 if then else None}
        hist = self.history
        def hist_change(days):
            if len(hist) <= days:
                return None
            then = hist[-1 - days][1]
            return {"abs": total - then, "pct": (total - then) / then * 100 if then else None}
        ath = max(hist, key=lambda p: p[1]) if hist else None
        usd_coins = [a for a in self.assets if a["peg"] == "USD"]
        depegs = [{"symbol": a["symbol"], "name": a["name"], "price": a["price"], "supply": a["supply"],
                   "dev_pct": (a["price"] - 1) * 100}
                  for a in usd_coins
                  if a["price"] and a["supply"] >= DEPEG_MIN_SUPPLY and abs(a["price"] - 1) * 100 > DEPEG_PCT]
        mech = {}
        for a in self.assets:
            mech[a["mechanism"] or "other"] = mech.get(a["mechanism"] or "other", 0) + a["supply"]
        usdt = next((a for a in self.assets if a["symbol"] == "USDT"), None)
        usdc = next((a for a in self.assets if a["symbol"] == "USDC"), None)
        mcap = self.btc.get("mcap")
        return {
            "total": total, "count": len(self.assets),
            "change_1d": change("d1"), "change_7d": change("d7"), "change_30d": change("d30"),
            "change_90d": hist_change(90), "change_365d": hist_change(365),
            "ath": {"date_ms": ath[0], "value": ath[1]} if ath else None,
            "at_ath": bool(ath and total >= ath[1] * 0.999),
            "usdt_dominance": usdt["supply"] / total * 100 if usdt and total else None,
            "usdc_dominance": usdc["supply"] / total * 100 if usdc and total else None,
            "by_mechanism": sorted(([k, v] for k, v in mech.items()), key=lambda x: -x[1]),
            "ssr": mcap / total if mcap and total else None,
            "btc": self.btc,
            "depegs": depegs,
            "updated": int(time.time() * 1000),
        }

    async def run(self, get_price) -> None:
        while True:
            try:
                await self.refresh(get_price())
                if self.on_update:
                    await self.on_update(self.snapshot(compact=True))
            except Exception as e:
                self.error = f"{type(e).__name__}: {e}"
                log.warning("stablecoin refresh failed: %s", self.error)
            await asyncio.sleep(REFRESH_SECONDS)

    def start(self, get_price) -> None:
        self._task = asyncio.create_task(self.run(get_price))

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()

    # ---------- outputs ----------
    def snapshot(self, compact: bool = False) -> dict:
        if not self.summary:
            return {"ready": False, "error": self.error}
        out = {"ready": True, "summary": self.summary, "error": self.error,
               "assets": self.assets[:60], "chains": self.chains[:20]}
        if not compact:
            out["history"] = self.history
        return out

    async def coin_history(self, coin_id: str) -> list[list]:
        hit = self._coin_hist.get(coin_id)
        if hit and time.time() - hit[0] < HISTORY_TTL:
            return hit[1]
        rows = await self._get("/stablecoincharts/all", stablecoin=coin_id)
        hist = [[int(p["date"]) * 1000, sum(float(v) for v in (p.get("totalCirculatingUSD") or {}).values())]
                for p in rows]
        self._coin_hist[coin_id] = (time.time(), hist)
        return hist

    def context(self) -> list[str]:
        s = self.summary
        if not s:
            return []
        b = lambda v: f"${v / 1e9:,.2f}B"
        sg = lambda c: "n/a" if not c or c.get("pct") is None else f"{'+' if c['abs'] >= 0 else '-'}{b(abs(c['abs']))} ({c['pct']:+.2f}%)"
        out = ["", "### Global stablecoin supply (DefiLlama, updates ~hourly)",
               f"Total {b(s['total'])} across {s['count']} stablecoins"
               + (" (at all-time high)" if s["at_ath"] else
                  f"; ATH {b(s['ath']['value'])}" if s["ath"] else ""),
               f"Change: 1d {sg(s['change_1d'])}, 7d {sg(s['change_7d'])}, 30d {sg(s['change_30d'])}, "
               f"90d {sg(s['change_90d'])}, 1y {sg(s['change_365d'])}",
               f"USDT dominance {s['usdt_dominance']:.1f}%, USDC {s['usdc_dominance']:.1f}%"
               if s["usdt_dominance"] and s["usdc_dominance"] else "",
               f"Stablecoin supply ratio (BTC market cap / stablecoin supply): {s['ssr']:.2f} "
               "(lower = more stablecoin buying power relative to BTC)" if s["ssr"] else "",
               "Top coins (supply, 7d change, price):"]
        for a in self.assets[:10]:
            ch = (a["supply"] - a["d7"]) / a["d7"] * 100 if a["d7"] else None
            out.append(f"- {a['symbol']} ({a['mechanism']}): {b(a['supply'])}, 7d "
                       f"{'n/a' if ch is None else f'{ch:+.2f}%'}, price {a['price'] if a['price'] else 'n/a'}")
        out.append("Top chains: " + ", ".join(f"{c['name']} {b(c['supply'])}" for c in self.chains[:6]))
        if s["depegs"]:
            out.append("PEG ALERTS: " + "; ".join(f"{d['symbol']} at ${d['price']:.4f} ({d['dev_pct']:+.2f}%), supply {b(d['supply'])}"
                                                   for d in s["depegs"]))
        else:
            out.append("No USD stablecoin above $50M is more than 0.5% off its peg.")
        last = self.history[-31:]
        if len(last) > 1:
            net = [(last[i][1] - last[i - 1][1]) for i in range(1, len(last))]
            out.append(f"Daily net mint/burn, last 30 days: avg {b(sum(net) / len(net))}/day, "
                       f"biggest mint {b(max(net))}, biggest burn {b(min(net))}")
        return [l for l in out if l]
