"""Calculators: the numbers Claude should compute with code, not in its head.

- realized volatility from daily closes, Deribit's DVOL index, OKX at-the-money IV
- the GARCH forecast from the garch-method plugin (the fork QuantClaude fixed), fed with
  OKX daily closes so its data-quality check passes
- option payoff at expiry for one leg or a spread
- risk totals and what-if moves across open positions

Everything here is arithmetic on data the app already has. Nothing places an order.
"""

from __future__ import annotations

import asyncio
import csv
import json
import logging
import math
import os
import statistics
import time
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger("btcliq")

DAY_MS = 86_400_000
SQRT_YEAR = math.sqrt(365)          # crypto trades every day
DVOL_URL = "https://www.deribit.com/api/v2/public/get_volatility_index_data"
GARCH_HISTORY_DAYS = 1500           # about four years of daily closes for the fit
GARCH_NOTE = ("GARCH forecasts the size of moves, never the direction. QuantClaude's audit: it is a rough "
              "one-day gauge for sizing spot or perp exposure; for 7 to 30 day option horizons DVOL "
              "predicted realized volatility better (R2 0.37 against 0.17). Do not act on it when "
              "data_quality.ok is false.")


# ---------- small maths ----------

def norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def black76(forward: float, strike: float, years: float, iv: float, kind: str) -> float:
    """Undiscounted option value in USD per 1 BTC of underlying. kind is 'C' or 'P'."""
    call = kind.upper().startswith("C")
    if years <= 0 or iv <= 0 or forward <= 0 or strike <= 0:
        return max(0.0, forward - strike) if call else max(0.0, strike - forward)
    s = iv * math.sqrt(years)
    d1 = (math.log(forward / strike) + 0.5 * s * s) / s
    d2 = d1 - s
    if call:
        return forward * norm_cdf(d1) - strike * norm_cdf(d2)
    return strike * norm_cdf(-d2) - forward * norm_cdf(-d1)


def pct_rank(values: list[float], x: float) -> float | None:
    """Share of `values` at or below x, 0-100."""
    vals = [v for v in values if v is not None]
    if not vals:
        return None
    return 100.0 * sum(1 for v in vals if v <= x) / len(vals)


def complete_days(candles: list[dict], now_ms: int | None = None) -> list[dict]:
    """Daily candles whose day has finished (the last one from OKX is still forming)."""
    now_ms = now_ms or int(time.time() * 1000)
    return [c for c in candles if c["time"] + DAY_MS <= now_ms]


def realized_vol(closes: list[float], windows=(7, 14, 30, 60, 90)) -> dict[str, float | None]:
    """Annualized close-to-close volatility (as a fraction, 0.45 = 45%) over the last n days."""
    rets = [math.log(b / a) for a, b in zip(closes, closes[1:]) if a > 0 and b > 0]
    out: dict[str, float | None] = {}
    for w in windows:
        out[f"{w}d"] = statistics.stdev(rets[-w:]) * SQRT_YEAR if len(rets) >= w and w > 1 else None
    return out


def rv_history(closes: list[float], window: int = 30) -> list[float]:
    """Rolling realized vol, one value per day, for ranking today's reading against the past."""
    rets = [math.log(b / a) for a, b in zip(closes, closes[1:]) if a > 0 and b > 0]
    return [statistics.stdev(rets[i - window:i]) * SQRT_YEAR for i in range(window, len(rets) + 1)]


# ---------- option payoff ----------

def _leg_price(c: dict, side: str, given: float | None) -> tuple[float | None, str]:
    """The price a leg would trade at: yours if given, else the ask to buy / bid to sell, else the mark."""
    if given is not None:
        return given, "given"
    px = c.get("ask") if side == "buy" else c.get("bid")
    if px:
        return px, "ask" if side == "buy" else "bid"
    return c.get("mark"), "mark"


def payoff(legs: list[dict], index: float, spots: list[float] | None = None, now_ms: int | None = None) -> dict:
    """Profit and loss at the first expiry for a set of option legs.

    Each leg: {"c": chain contract, "side": "buy"|"sell", "contracts": n, "price": optional}.
    Prices are in the contract's own unit (BTC for coin-margined, USD for USD-margined) per
    1 BTC of underlying; USD figures convert BTC premiums at today's index.
    """
    if not legs:
        raise ValueError("at least one leg is needed")
    now_ms = now_ms or int(time.time() * 1000)
    first_exp = min(l["c"]["exp_ms"] for l in legs)
    notes: list[str] = []
    rows = []
    for l in legs:
        c, side, n = l["c"], l["side"], float(l["contracts"])
        px, src = _leg_price(c, side, l.get("price"))
        if px is None:
            raise ValueError(f"no price available for {c['name']}")
        mult = c.get("ct_mult") or 0.01
        usd_px = px * index if c.get("unit") == "BTC" else px
        sign = 1.0 if side == "buy" else -1.0
        rows.append({"c": c, "side": side, "n": n, "px": px, "src": src, "mult": mult, "usd_px": usd_px,
                     "sign": sign, "cash": -sign * n * mult * usd_px})
    if any(r["c"]["exp_ms"] != first_exp for r in rows):
        notes.append("Legs expire on different dates: the table is at the first expiry, with later legs valued "
                     "by Black-76 at today's IV.")
    if any(r["c"].get("unit") == "BTC" for r in rows):
        notes.append("Coin-margined legs are paid for and settled in BTC; USD figures convert the premium at "
                     "today's index, so the BTC result differs if BTC moves.")
    if any(r["src"] == "mark" for r in rows):
        notes.append("A leg had no live bid or ask, so its mark price was used.")
    net_cash = sum(r["cash"] for r in rows)          # negative = you pay (debit)

    def value_at(S: float) -> float:
        total = 0.0
        for r in rows:
            c = r["c"]
            if c["exp_ms"] == first_exp:
                v = max(0.0, S - c["strike"]) if c["type"] == "C" else max(0.0, c["strike"] - S)
            else:
                v = black76(S, c["strike"], (c["exp_ms"] - first_exp) / DAY_MS / 365.0, c.get("iv") or 0.0, c["type"])
            total += r["sign"] * r["n"] * r["mult"] * v
        return total

    pnl = lambda S: value_at(S) + net_cash
    strikes = sorted({r["c"]["strike"] for r in rows})
    lo, hi = min(0.5 * index, 0.8 * strikes[0]), max(1.6 * index, 1.25 * strikes[-1])
    fine = [lo + (hi - lo) * i / 600 for i in range(601)]
    breakevens = []
    for a, b in zip(fine, fine[1:]):
        pa, pb = pnl(a), pnl(b)
        if pa == 0:
            breakevens.append(a)
        elif pa * pb < 0:
            breakevens.append(a + (b - a) * pa / (pa - pb))
    net_calls = sum(r["sign"] * r["n"] * r["mult"] for r in rows if r["c"]["type"] == "C")
    candidates = [pnl(s) for s in fine + strikes + [0.0001]]
    max_gain: float | str = max(candidates)
    max_loss: float | str = min(candidates)
    if net_calls > 1e-12:
        max_gain = "unlimited"
    elif net_calls < -1e-12:
        max_loss = "unlimited"
    grid = spots or sorted({round(index * (1 + m), 0) for m in
                            (-0.3, -0.2, -0.15, -0.1, -0.05, -0.02, 0, 0.02, 0.05, 0.1, 0.15, 0.2, 0.3)}
                           | set(strikes) | {round(b, 0) for b in breakevens})
    greeks = {}
    for g in ("delta", "gamma", "theta", "vega"):
        vals = [r["c"].get(g) for r in rows]
        greeks[g] = None if any(v is None for v in vals) else sum(
            r["sign"] * r["n"] * r["mult"] * r["c"][g] for r in rows)
    return {
        "index": index,
        "expiry": datetime.fromtimestamp(first_exp / 1000, timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "days_to_expiry": (first_exp - now_ms) / DAY_MS,
        "legs": [{"contract": r["c"]["name"], "side": r["side"], "contracts": r["n"], "price": r["px"],
                  "price_from": r["src"], "price_usd_per_btc": r["usd_px"], "premium_usd": r["cash"],
                  "iv": r["c"].get("iv")} for r in rows],
        "net_premium_usd": net_cash,
        "net_premium_reads": "you pay this (debit)" if net_cash < 0 else "you receive this (credit)",
        "max_loss_usd": max_loss, "max_gain_usd": max_gain,
        "breakevens": [round(b, 0) for b in breakevens],
        "greeks_now": {"delta_btc": greeks["delta"], "gamma": greeks["gamma"],
                       "theta_usd_per_day": greeks["theta"], "vega_usd_per_vol_point": greeks["vega"]},
        "at_expiry": {"columns": ["spot", "move_pct", "pnl_usd"],
                      "rows": [[s, (s / index - 1) * 100, pnl(s)] for s in grid]},
        "notes": notes,
    }


# ---------- risk across positions ----------

def signed_size(p: dict) -> float:
    pos = p.get("pos") or 0.0
    return -abs(pos) if p.get("posSide") == "short" else pos


def position_risk(positions: list[dict], contracts: dict[str, dict], index: float,
                  moves=(-0.10, -0.05, -0.02, 0.02, 0.05, 0.10), now_ms: int | None = None) -> dict:
    """Totals and what-if spot moves for the open positions.

    `contracts` maps an option's name to its chain row (for IV, strike and expiry). Options
    are repriced with Black-76 at today's IV; perps, futures and spot move one for one.
    """
    now_ms = now_ms or int(time.time() * 1000)
    rows, notes = [], []
    tot = {"delta_btc": 0.0, "delta_usd": 0.0, "theta_usd_per_day": 0.0, "upl_usd": 0.0, "notional_usd": 0.0}
    scen = {m: 0.0 for m in moves}
    missing = []
    for p in positions:
        inst, typ, qty = p.get("instId") or "", p.get("type") or "", signed_size(p)
        if not qty:
            continue
        row = {"instId": inst, "type": typ, "size": qty, "mgnMode": p.get("mgnMode"),
               "upl_usd": p.get("upl_usd"), "notional_usd": p.get("notionalUsd")}
        if typ == "OPTION":
            c = contracts.get(inst)
            # OKX reports a position's Black-Scholes delta in BTC (deltaBS) and its theta in USD a day (thetaBS).
            d_btc = p.get("deltaBS") if p.get("deltaBS") is not None else p.get("delta")
            d_usd = d_btc * index if d_btc is not None else None
            if c:
                mult = c.get("ct_mult") or 0.01
                years = max(0.0, (c["exp_ms"] - now_ms) / DAY_MS / 365.0)
                fwd = c.get("fwd") or index
                iv = c.get("iv") or 0.0
                base = black76(fwd, c["strike"], years, iv, c["type"])
                for m in moves:
                    scen[m] += qty * mult * (black76(fwd * (1 + m), c["strike"], years, iv, c["type"]) - base)
                row.update(expiry=datetime.fromtimestamp(c["exp_ms"] / 1000, timezone.utc).strftime("%Y-%m-%d"),
                           days_left=(c["exp_ms"] - now_ms) / DAY_MS, strike=c["strike"], iv=iv,
                           moneyness_pct=(c["strike"] / index - 1) * 100)
                if d_btc is None and c.get("delta") is not None:
                    d_btc = qty * mult * c["delta"]
                    d_usd = d_btc * index
            else:
                missing.append(inst)
                for m in moves:      # no chain row: fall back to delta, good for small moves only
                    scen[m] += (d_usd or 0.0) * m
            theta = p.get("thetaBS")
        else:
            notional = abs(p.get("notionalUsd") or 0.0)
            d_usd = math.copysign(notional, qty)
            d_btc = d_usd / index if index else None
            theta = 0.0
            for m in moves:
                scen[m] += d_usd * m
            row.update(lever=p.get("lever"), liq_price=p.get("liqPx"))
        row.update(delta_btc=d_btc, delta_usd=d_usd, theta_usd_per_day=theta)
        rows.append(row)
        tot["delta_btc"] += d_btc or 0.0
        tot["delta_usd"] += d_usd or 0.0
        tot["theta_usd_per_day"] += theta or 0.0
        tot["upl_usd"] += p.get("upl_usd") or 0.0
        tot["notional_usd"] += abs(p.get("notionalUsd") or 0.0)
    if missing:
        notes.append("No chain data for " + ", ".join(missing) + ": its what-if uses delta only.")
    notes.append("What-ifs move spot only and keep today's IV and time to expiry. Options values are in USD; "
                 "coin-margined positions settle in BTC.")
    return {
        "index": index, "positions": len(rows), "totals": tot,
        "reads": {"delta_btc": "BTC you are effectively long (+) or short (-)",
                  "delta_usd": "the same exposure in dollars: what a 1% move in BTC is worth, times 100",
                  "theta_usd_per_day": "what one day of time decay costs (-) or earns (+), at today's rate"},
        "what_if": {"columns": ["spot_move_pct", "spot", "pnl_usd"],
                    "rows": [[m * 100, index * (1 + m), scen[m]] for m in moves]},
        "rows": rows, "notes": notes,
    }


# ---------- data that needs the network or a subprocess ----------

class Quant:
    """DVOL, long daily history and the GARCH runner, each cached."""

    def __init__(self, session, data_dir: Path, garch_dir: Path | None = None):
        self.s = session
        self.data_dir = Path(data_dir)
        self.garch_dir = garch_dir
        self._dvol: tuple[float, dict] | None = None
        self._daily: tuple[str, list[dict]] | None = None
        self._garch: tuple[str, dict] | None = None
        self._lock = asyncio.Lock()

    # Deribit's 30-day implied volatility index for BTC, in vol points (40 = 40%).
    async def dvol(self) -> dict:
        if self._dvol and time.time() - self._dvol[0] < 300:
            return self._dvol[1]
        now = int(time.time() * 1000)
        params = {"currency": "BTC", "start_timestamp": str(now - 366 * DAY_MS),
                  "end_timestamp": str(now), "resolution": "1D"}
        async with self.s.get(DVOL_URL, params=params) as r:
            r.raise_for_status()
            body = await r.json(content_type=None)
        data = (body.get("result") or {}).get("data") or []
        if not data:
            raise RuntimeError("Deribit returned no DVOL data")
        closes = [float(d[4]) for d in data]
        last = closes[-1]
        ago = lambda n: closes[-1 - n] if len(closes) > n else None
        out = {"dvol": last, "as_of": datetime.fromtimestamp(data[-1][0] / 1000, timezone.utc).strftime("%Y-%m-%d"),
               "change_7d": None if ago(7) is None else last - ago(7),
               "change_30d": None if ago(30) is None else last - ago(30),
               "low_1y": min(closes), "high_1y": max(closes), "percentile_1y": pct_rank(closes, last),
               "implied_daily_move_pct": last / SQRT_YEAR,
               "reads": "30-day implied volatility for BTC from Deribit options, in vol points (40 = 40% a year)"}
        self._dvol = (time.time(), out)
        return out

    async def daily_history(self, ex) -> list[dict]:
        """About four years of OKX daily candles, refetched once per UTC day."""
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        if self._daily and self._daily[0] == today:
            return self._daily[1]
        async with self._lock:
            if self._daily and self._daily[0] == today:
                return self._daily[1]
            start = int(time.time() * 1000) - GARCH_HISTORY_DAYS * DAY_MS
            candles = await ex.okx_candles_history("1Dutc", start)
            self._daily = (today, candles)
            return candles

    def garch_script(self) -> Path | None:
        for d in (self.garch_dir, os.environ.get("BTCLIQ_GARCH_DIR"),
                  Path(__file__).resolve().parents[4] / "garch-method-fixed"):
            if d and (Path(d) / "scripts" / "garch").is_file():
                return Path(d) / "scripts" / "garch"
        return None

    async def garch(self, candles: list[dict]) -> dict:
        """Run the plugin's forecast and size commands on these daily candles."""
        script = self.garch_script()
        if not script:
            raise FileNotFoundError("the garch-method plugin isn't next to btcliq (expected ../garch-method-fixed); "
                                    "set BTCLIQ_GARCH_DIR to its folder")
        done = complete_days(candles)
        if len(done) < 400:
            raise ValueError("not enough daily history for a GARCH fit yet")
        key = f"{done[-1]['time']}:{len(done)}"
        if self._garch and self._garch[0] == key:
            return self._garch[1]
        path = self.data_dir / "desk" / "okx_daily.csv"
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["date", "close"])
            for c in candles:  # the still-forming bar goes in too: the plugin drops it and says so
                w.writerow([datetime.fromtimestamp(c["time"] / 1000, timezone.utc).strftime("%Y-%m-%d"), c["close"]])

        async def run(cmd: str) -> dict:
            proc = await asyncio.create_subprocess_exec(
                "bash", str(script), cmd, "--csv", str(path), "--json",
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
            try:
                out, err = await asyncio.wait_for(proc.communicate(), 150)
            except asyncio.TimeoutError:
                proc.kill()
                raise RuntimeError(f"garch {cmd} timed out") from None
            if proc.returncode:
                raise RuntimeError(f"garch {cmd} failed: {err.decode(errors='replace').strip()[-300:]}")
            return json.loads(out.decode())

        fc, sz = await asyncio.gather(run("forecast"), run("size"))
        out = {
            "source": f"OKX BTC-USDT perpetual, {len(done)} daily closes",
            "as_of": fc.get("as_of"), "forecast_for": fc.get("forecast_for"),
            "forecast_vol_daily_pct": fc.get("forecast_vol_daily_pct"),
            "forecast_vol_annualized_pct": fc.get("forecast_vol_annualized_pct"),
            "vol_percentile_1y": fc.get("vol_percentile_1y"), "regime": fc.get("regime"),
            "regime_reads": "calm / normal / storm = bottom / middle / top third of BTC's own past year",
            "target_vol_pct": sz.get("target_vol_pct"), "target_mode": sz.get("target_mode"),
            "position_size_multiplier": sz.get("position_size_multiplier"),
            "multiplier_reads": "for spot or perp size only, not options; never size up in a storm",
            "data_quality": fc.get("data_quality"),
            "note": GARCH_NOTE,
        }
        self._garch = (key, out)
        return out
