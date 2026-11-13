"""Bitcoin Power Law (Santostasi) inside btcliq.

Wraps the brief's calculator (powerlaw_calc.py, an unchanged copy of
btc-powerlaw/powerlaw.py) so the chart can show the model's state, check the
invalidation rules (brief section 8), and evaluate live option contracts in the
three worlds (risk-neutral / NvPL / mix) at each contract's own IV.

Use it as the brief says: a long-horizon bias, strike anchor and regime filter,
never a short-term timing signal.
"""

from __future__ import annotations

import asyncio
import logging
import math
import shutil
import time
from datetime import date, datetime, timezone
from pathlib import Path

import aiohttp

from . import powerlaw_calc as pl

log = logging.getLogger("btcliq")
# The btc-powerlaw project sits next to this one (../btc-powerlaw); its cache seeds ours if present.
SEED_CACHE = Path(__file__).resolve().parents[3] / "btc-powerlaw" / "data" / "btc_daily.csv"
REFRESH_SECONDS = 60          # recompute state with the live index
DATA_REFRESH_SECONDS = 3600   # top up daily closes from Coin Metrics

# Brief §7.2: what each zone calls for.
ZONE_MENU = {
    "BELOW_FLOOR": ("Long-term bullish but unconfirmed. Small 9-15m bull call spreads, staged. "
                    "Sell puts only after a reclaim (two daily closes back above the floor).",
                    "Short puts, leverage, large size."),
    "DEEP_VALUE": ("Strong at 6-24 months, neutral under 3 months. 6-14m bull call spreads; 2-5m short "
                   "put spreads at or below the floor or cycle low; put-spread-financed call spreads; "
                   "diagonals (long 9-12m call, short 1-2m OTM call).",
                   "Short-dated puts as directional bets; short calls near spot; expecting a quick move to trend."),
    "VALUE": ("Bullish. 3-9m call spreads; roll long calls up and out; put spreads near the floor.",
              "Naked short calls."),
    "FAIR": ("Neutral; take profits. Scale out of long calls at 0.8x/1.0x; collars; call spreads sold at "
             ">= 1.25x trend; condors (short put >= 0.6x, short call >= 1.3x).",
             "New aggressive long-dated calls."),
    "EXTENDED": ("Bearish tilt. Bear call spreads at 1.5-2x; 3-9m put spreads; protect spot.",
                 "Long OTM calls."),
    "BUBBLE": ("Bearish. 6-12m put spreads; sell call spreads; cut long delta.", "Any long calls."),
}

# Brief §0.3, condensed for the chat.
RULES = """Power-law operating rules (from the user's brief, sections 0.3 and 8):
1. Horizon gate: the power law is the primary thesis only for >= 6 months to expiry; a secondary
   tilt at 3-6 months; under 3 months only a filter, never the reason for a short-dated directional trade.
2. Baseline expectation is NvPL (naive-power-law blend). The AR forecast is an optimistic in-sample bound; never size on it.
3. The zone sets the menu of structures (see zone menu below).
4. Strike anchors: puts at the floor at expiry and the cycle low; call-spread short strikes at 0.55x / 0.80x
   trend at expiry or the NvPL forecast; call-selling zone 1.25x trend. Never use the old 3-4x "resistance" bands.
5. Test every trade in three worlds: risk_neutral (model wrong, zero drift at market IV), nvpl (model holds),
   mix (50/50). Take it only if attractive under nvpl AND survivable under risk_neutral.
6. Defined risk only (spreads, long options; no naked shorts). Max 2-3% of the account at risk per trade,
   15-20% total across power-law trades. Ladder expiries and entries. Size by the risk-neutral loss, never the NvPL gain.
7. Expect path pain: put strikes and sizes must survive a -25% excursion.
8. Use today's vol regime (~40% realized), not old cycles; last peak was only 1.16x trend.
9. Invalidation: two consecutive weekly closes below the floor, or any daily close below 0.35x trend, or
   30+ days below the floor -> drop the bullish bias, close power-law short puts; resume after two weekly closes back above.
10. Refresh the numbers before every decision (the chart does this live)."""


def _today() -> date:
    return datetime.now(timezone.utc).date()


class PowerLaw:
    def __init__(self, session: aiohttp.ClientSession, data_dir: Path):
        self.s = session
        data_dir.mkdir(parents=True, exist_ok=True)
        cache = data_dir / "btc_daily.csv"
        if not cache.exists() and SEED_CACHE.exists():
            shutil.copy(SEED_CACHE, cache)  # start from the btc-powerlaw project's cache
        pl.CACHE = str(cache)
        self.rows: list[tuple[date, float]] = []
        self.src = ""
        self.base: dict | None = None
        self.inval: dict | None = None
        self.price: float | None = None
        self.price_src = ""
        self.updated = 0.0
        self._data_at = 0.0
        self._task: asyncio.Task | None = None
        self.on_update = None  # async callback(state)

    # ---------- data ----------
    async def _index(self, fallback: float | None) -> tuple[float | None, str]:
        try:
            async with self.s.get("https://www.okx.com/api/v5/market/index-tickers",
                                  params={"instId": "BTC-USD"}) as r:
                d = await r.json(content_type=None)
            return float(d["data"][0]["idxPx"]), "OKX BTC-USD index"
        except Exception:
            return fallback, "OKX perp (index unavailable)"

    async def refresh(self, fallback_price: float | None = None) -> None:
        loop = asyncio.get_running_loop()
        if not self.rows or time.time() - self._data_at > DATA_REFRESH_SECONDS:
            rows, src = await loop.run_in_executor(None, pl.update_data, False)
            if rows:
                self.rows, self.src, self._data_at = rows, src, time.time()
        if not self.rows:
            raise RuntimeError(f"no price history ({self.src})")
        price, psrc = await self._index(fallback_price)
        if not price:
            price, psrc = self.rows[-1][1], f"daily close {self.rows[-1][0]}"
        self.price, self.price_src = price, psrc
        self.base = await loop.run_in_executor(
            None, pl.analyse, self.rows, _today(), price, psrc, None, None)
        self.inval = self._invalidation(price)
        self.updated = time.time()

    async def run(self, get_price) -> None:
        while True:
            try:
                await self.refresh(get_price())
                if self.on_update:
                    await self.on_update(self.state())
            except Exception as e:
                log.warning("power law refresh failed: %s", e)
            await asyncio.sleep(REFRESH_SECONDS)

    def start(self, get_price) -> None:
        self._task = asyncio.create_task(self.run(get_price))

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()

    # ---------- invalidation (brief §8) ----------
    def _invalidation(self, live_price: float) -> dict:
        a, b, _ = pl.FIXED_MODELS[pl.PRIMARY]
        floor = lambda d: pl.FLOOR_MULT * pl.trend(a, b, pl.tdays(d))
        state, reason, since = "OK", "", None
        consec_below = 0
        weekly: list[bool] = []          # Sunday closes: True if below the floor
        above_weeks_since_trigger = 0
        for d, px in self.rows:
            f = floor(d)
            below = px < f
            consec_below = consec_below + 1 if below else 0
            trig = None
            if px < 0.35 / pl.FLOOR_MULT * f:
                trig = f"daily close {d} at {px / (f / pl.FLOOR_MULT):.3f}x trend (< 0.35x)"
            elif consec_below >= 30:
                trig = f"30+ consecutive days below the floor (to {d})"
            if d.weekday() == 6:  # Sunday close = weekly close
                weekly.append(below)
                if len(weekly) >= 2 and weekly[-1] and weekly[-2]:
                    trig = trig or f"two consecutive weekly closes below the floor (week of {d})"
                if state == "INVALIDATED":
                    above_weeks_since_trigger = above_weeks_since_trigger + 1 if not below else 0
                    if above_weeks_since_trigger >= 2:
                        state, reason, since = "OK", f"re-enabled after two weekly closes above the floor ({d})", d
            if trig and state != "INVALIDATED":
                state, reason, since, above_weeks_since_trigger = "INVALIDATED", trig, d, 0
        last_d, last_px = self.rows[-1]
        live_floor = floor(_today())
        warnings = []
        if state == "OK":
            if last_px < floor(last_d):
                warnings.append(f"daily close {last_d} below the floor: no new short puts, tighten stops")
            if live_price < live_floor:
                warnings.append(f"live price below today's floor (${live_floor:,.0f})")
            if live_price < 0.35 / pl.FLOOR_MULT * live_floor:
                warnings.append("live price below 0.35x trend: invalidates on a daily close here")
        status = "INVALIDATED" if state == "INVALIDATED" else ("WARNING" if warnings else "OK")
        last_trigger = reason if state == "INVALIDATED" else None
        return {
            "status": status,
            "detail": (reason if state == "INVALIDATED" else "; ".join(warnings) or
                       "no trigger: price above the floor, no weekly closes below it, above 0.35x trend"),
            "since": since.isoformat() if since and state == "INVALIDATED" else None,
            "days_below_floor_now": consec_below,
            "last_close": {"date": last_d.isoformat(), "price": round(last_px), "floor": round(floor(last_d))},
            "last_trigger": last_trigger,
        }

    # ---------- outputs ----------
    def state(self) -> dict:
        if not self.base:
            return {"ready": False}
        o = self.base
        p = o["primary"]
        sp = o["models"]["SP26"]
        rf = o["models"]["REFIT"]
        zone = p["zone"]
        good, avoid = ZONE_MENU.get(zone, ("", ""))
        return {
            "ready": True,
            "asof": o["asof"], "price": round(self.price, 1), "price_source": self.price_src,
            "data_last_date": o["data_last_date"], "data_source": self.src,
            "zone": zone, "zone_menu": good, "zone_avoid": avoid,
            "ratio": p["ratio"], "resid_dex": p["resid_dex"], "trend": p["trend"],
            "floor": p["floor"], "floor_cushion_pct": p["floor_cushion_pct"],
            "floor_qr025": p["floor_qr025"], "floor_refit": p["floor_refit"],
            "flat_price_meets_floor_on": p["flat_price_meets_floor_on"],
            "days_until_flat_price_meets_floor": p["days_until_flat_price_meets_floor"],
            "pct_rank_since_2018": p["pct_rank_since_2018"], "pct_rank_all": p["pct_rank_all"],
            "trend_growth_pct_per_year": p["trend_growth_pct_per_year"],
            "realized_vol": o["realized_vol"], "cycle": o["cycle"],
            "refit_b": rf["b"],
            "params": {"genesis": "2009-01-03", "floor_mult": pl.FLOOR_MULT,
                       "sp26": [sp["a"], sp["b"]], "refit": [rf["a"], rf["b"]],
                       "nvpl": [pl.NVPL_ALPHA, pl.NVPL_CENTER]},
            "forward": o["forward"],
            "invalidation": self.inval,
        }

    def evaluate(self, c: dict, index: float, trust: float = 0.5) -> dict:
        """Three-world check of one live contract at its own IV (brief rule 5)."""
        if not self.rows:
            return {"error": "power-law data not loaded yet"}
        today = _today()
        exp = datetime.fromtimestamp(c["exp_ms"] / 1000, timezone.utc).date()
        h = (exp - today).days
        if h < 1:
            return {"error": "expires today: no power-law evaluation"}
        iv = c.get("iv") or self.base["realized_vol"]["rv90"]
        # Rate implied by OKX's forward for this expiry, so the risk-neutral world matches the
        # market's pricing (the brief's calculator defaults to r = 0).
        T = h / 365.0
        rate = math.log(c["fwd"] / index) / T if c.get("fwd") and index else 0.0
        o = pl.analyse(self.rows, today, index, "OKX index", [exp],
                       {"expiry": exp, "iv": iv, "rate": rate, "trust": trust, "strikes": [c["strike"]]})
        ob, s = o["options"], o["options"]["strikes"][0]
        fwd = next((f for f in o["forward"] if f["date"] == exp.isoformat()), {})
        kind = "call" if c["type"] == "C" else "put"
        to_usd = (lambda v: None if v is None else v * index) if c["unit"] == "BTC" else (lambda v: v)
        per_btc = lambda v: None if v is None else v  # OKX prices are per 1 BTC of underlying
        bid, ask, mark = (per_btc(to_usd(c[k])) for k in ("bid", "ask", "mark"))
        worlds = ("risk_neutral", "nvpl", "mix")
        fair = {w: s[kind][w] for w in worlds}
        p_itm = {w: (s["p_above"][w] if kind == "call" else round(1 - s["p_above"][w], 4)) for w in worlds}
        gate = "primary" if h >= 182 else "secondary" if h >= 91 else "filter"
        K = c["strike"]
        checks = []
        checks.append({
            "primary": ("ok", f"{h} days to expiry: the power law can be the primary thesis (rule 1)."),
            "secondary": ("warn", f"{h} days to expiry: power law is only a secondary tilt at 3-6 months (rule 1)."),
            "filter": ("bad", f"{h} days to expiry: under 3 months the power law is only a filter, "
                              f"never the reason for a directional trade (rule 1)."),
        }[gate])
        if self.inval and self.inval["status"] == "INVALIDATED":
            checks.append(("bad", "Invalidation is active (section 8): power-law bullish bias is off."))
        elif self.inval and self.inval["status"] == "WARNING":
            checks.append(("warn", "Floor warning (section 8): no new short puts."))
        floor_T = fwd.get("floor")
        if kind == "put":
            if floor_T:
                checks.append(("ok" if K <= floor_T else "warn",
                               f"As a short put: strike ${K:,.0f} is {'at/below' if K <= floor_T else 'above'} the floor at "
                               f"expiry (${floor_T:,.0f}); rule 4 anchors short puts at or below the floor or the cycle low."))
            checks.append(("ok" if K <= 0.75 * index else "warn",
                           f"-25% excursion test (rule 7): a 25% drop takes BTC to ${0.75 * index:,.0f}; "
                           f"strike is {'below' if K <= 0.75 * index else 'above'} that."))
        else:
            anchors = {"0.55x": fwd.get("x0_55"), "0.80x": fwd.get("x0_80"), "NvPL": fwd.get("nvpl_forecast")}
            near = min(((k, v) for k, v in anchors.items() if v), key=lambda kv: abs(kv[1] - K), default=None)
            if near:
                checks.append(("info", f"Nearest call-spread short-strike anchor at expiry: {near[0]} = ${near[1]:,.0f} (rule 4)."))
            if fwd.get("x1_25") and K >= fwd["x1_25"]:
                checks.append(("info", f"Strike is in the call-selling zone (>= 1.25x trend = ${fwd['x1_25']:,.0f})."))
        checks.append(("info", "Defined risk only (rule 6): use this as a leg of a spread, max 2-3% of the account at risk."))
        return {
            "expiry": exp.isoformat(), "days": h, "gate": gate, "iv_used": round(iv, 4), "trust": trust,
            "rate": round(rate, 5), "forward": c.get("fwd"),
            "index": index, "zone": o["primary"]["zone"], "ratio": o["primary"]["ratio"],
            "floor_now": ob["floor_now"], "floor_at_expiry": floor_T,
            "x0_55": fwd.get("x0_55"), "x0_80": fwd.get("x0_80"), "x1_25": fwd.get("x1_25"),
            "trend_at_expiry": fwd.get("trend"), "nvpl_forecast": fwd.get("nvpl_forecast"),
            "nvpl_weight": fwd.get("nvpl_weight"),
            "median_price": {w: ob["median_price"].get(w) for w in ("risk_neutral", "nvpl", "ar")},
            "p_touch_floor": ob["p_touch_floor_before_expiry"], "p_below_floor": ob["p_below_floor_at_expiry"],
            "p_itm": p_itm, "fair_usd": fair,
            "market_usd": {"bid": bid, "ask": ask, "mark": mark},
            "edge_buy_at_ask": {w: None if ask is None else round(fair[w] - ask, 2) for w in worlds},
            "edge_sell_at_bid": {w: None if bid is None else round(bid - fair[w], 2) for w in worlds},
            "checks": [{"level": l, "text": t} for l, t in checks],
        }

    # ---------- chat ----------
    def context(self) -> list[str]:
        st = self.state()
        if not st.get("ready"):
            return ["", "### Bitcoin power law", "not loaded yet"]
        inv = st["invalidation"]
        out = ["", "### Bitcoin power law (Santostasi; SP26 primary; live)", RULES, "",
               f"State: price ${st['price']:,.0f} ({st['price_source']}) = {st['ratio']:.3f}x SP26 trend "
               f"(${st['trend']:,.0f}), residual {st['resid_dex']:+.3f} dex, zone {st['zone']}.",
               f"Floor (0.42x) ${st['floor']:,.0f}, cushion {st['floor_cushion_pct']:.1f}%; alt floors: REFIT ${st['floor_refit']:,.0f}, "
               f"QR2.5% ${st['floor_qr025']:,.0f}. Flat price meets the floor on {st['flat_price_meets_floor_on']}. "
               f"Cheaper than {100 - st['pct_rank_since_2018']:.1f}% of days since 2018. REFIT exponent {st['refit_b']:.3f} "
               f"(downgrade targets if < 5.5). Realized vol 30/90/365d: "
               f"{st['realized_vol']['rv30']:.0%}/{st['realized_vol']['rv90']:.0%}/{st['realized_vol']['rv365']:.0%}.",
               f"Invalidation status: {inv['status']}: {inv['detail']}.",
               f"Zone menu ({st['zone']}): {st['zone_menu']} Avoid: {st['zone_avoid']}",
               "Forward levels (floor | 0.55x | 0.80x | 1.25x | NvPL forecast (weight)):"]
        for f in st["forward"][:9]:
            out.append(f"- {f['date']} ({f['days']}d): {f['floor']:,.0f} | {f['x0_55']:,.0f} | {f['x0_80']:,.0f} | "
                       f"{f['x1_25']:,.0f} | {f['nvpl_forecast']:,.0f} ({f['nvpl_weight']:.2f})")
        return out

    @staticmethod
    def describe_eval(name: str, e: dict) -> list[str]:
        if "error" in e:
            return [f"- Power-law check for {name}: {e['error']}"]
        f = lambda d: " / ".join(f"{w} {d[w]:,.0f}" if isinstance(d.get(w), (int, float)) else f"{w} n/a"
                                 for w in ("risk_neutral", "nvpl", "mix"))
        pc = lambda d: " / ".join(f"{w} {d[w] * 100:.0f}%" for w in ("risk_neutral", "nvpl", "mix"))
        m = e["market_usd"]
        return [
            f"- Power-law check ({e['days']}d, gate={e['gate']}, IV {e['iv_used']:.1%}): floor at expiry ${e['floor_at_expiry']:,.0f}, "
            f"0.55x ${e['x0_55']:,.0f}, NvPL forecast ${e['nvpl_forecast']:,.0f} (w {e['nvpl_weight']:.2f})",
            f"  P(finish ITM): {pc(e['p_itm'])}; fair value per 1 BTC (USD): {f(e['fair_usd'])}",
            f"  Market per 1 BTC: bid {m['bid'] or 0:,.0f}, ask {m['ask'] or 0:,.0f}; edge if bought at ask: {f(e['edge_buy_at_ask'])}; "
            f"edge if sold at bid: {f(e['edge_sell_at_bid'])}",
            f"  P(touch floor before expiry): {pc(e['p_touch_floor'])}",
            "  Checks: " + " ".join(c["text"] for c in e["checks"]),
        ]
