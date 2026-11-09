"""OKX BTC/USD options: the full chain plus lookups of single contracts by name.

Two families:
- BTC-USD     coin-margined (inverse): quoted and settled in BTC, 1 contract = 0.01 BTC
- BTC-USD_UM  USD-margined: quoted and settled in USD, 1 contract = 0.01 BTC

Data comes from five public OKX endpoints (instruments, tickers, opt-summary for IV and
greeks, mark price, open interest), merged per contract and cached for a few seconds.
Polling only happens while a browser is looking at options.
"""

from __future__ import annotations

import asyncio
import calendar
import logging
import re
import time
from datetime import datetime, timezone

import aiohttp

log = logging.getLogger("btcliq")
OKX = "https://www.okx.com"
FAMILIES = {"BTC-USD": "Coin-margined (BTC)", "BTC-USD_UM": "USD-margined"}
CHAIN_TTL = 8          # seconds a merged chain snapshot stays fresh
INSTRUMENTS_TTL = 300  # contract list changes rarely
MONTHS = {m.upper(): i for i, m in enumerate(calendar.month_abbr) if m}


def _f(x) -> float | None:
    try:
        return float(x) if x not in (None, "") else None
    except (TypeError, ValueError):
        return None


def parse_query(q: str) -> dict:
    """Pull family, expiry, strike and type out of a loosely written contract name.

    Accepts e.g. 'BTC-USD-261127-75000-C', 'btc-usd_um-261127-75000-p',
    '27NOV26 75000 C', '27nov 75k call', '261127 75000 put', 'BTC-27NOV26-75000-C' (Deribit style).
    """
    s = q.strip().upper()
    # Written-out dates -> 27NOV26: "27 nov 2026", "27th November", "Nov 27", "November 27th, 2026".
    mon = r"(JAN|FEB|MAR|APR|MAY|JUN|JUL|AUG|SEP|OCT|NOV|DEC)[A-Z]*\.?"
    yr = lambda y: (y or "")[-2:]
    s = re.sub(rf"\b(\d{{1,2}})(?:ST|ND|RD|TH)?\s+{mon}(?:,?\s+(\d{{4}}|\d{{2}})\b)?",
               lambda m: f" {m.group(1)}{m.group(2)}{yr(m.group(3))} ", s)
    s = re.sub(rf"\b{mon}\s+(\d{{1,2}})(?:ST|ND|RD|TH)?\b(?:,?\s+(\d{{4}}|\d{{2}})\b)?",
               lambda m: f" {m.group(2)}{m.group(1)}{yr(m.group(3))} ", s)
    out: dict = {"family": "BTC-USD_UM" if ("_UM" in s or " UM" in s or "USD-MARGIN" in s) else None}
    s = s.replace("BTC-USD_UM", " ").replace("BTCUSD", " ").replace("BTC-USD", " ").replace("BTC", " ")
    tokens = [t for t in re.split(r"[\s\-_/]+", s) if t]
    for t in tokens:
        if t in ("C", "CALL", "CALLS"):
            out["type"] = "C"
        elif t in ("P", "PUT", "PUTS"):
            out["type"] = "P"
        elif re.fullmatch(r"\d{6}", t) and t[2:4] <= "12":                 # YYMMDD
            out["expiry"] = t
        elif m := re.fullmatch(r"(\d{1,2})([A-Z]{3})(\d{2})?", t):           # 27NOV or 27NOV26
            day, mon, yy = int(m.group(1)), MONTHS.get(m.group(2)), m.group(3)
            if mon:
                out["expiry_dm"] = (day, mon, int(yy) if yy else None)
        elif m := re.fullmatch(r"(\d+(?:\.\d+)?)(K?)([CP]?)", t):           # 75000, 75K, 75000C
            val = float(m.group(1)) * (1000 if m.group(2) else 1)
            if val >= 100 and not (1900 <= val <= 2100) and "strike" not in out:  # skip years
                out["strike"] = val
            if m.group(3):
                out["type"] = m.group(3)
    return out


class OptionsFeed:
    def __init__(self, session: aiohttp.ClientSession):
        self.s = session
        self._instruments: dict[str, tuple[float, list[dict]]] = {}
        self._chain: dict[str, tuple[float, dict]] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self.last_request = 0.0

    async def _okx(self, path: str, **params) -> list:
        async with self.s.get(OKX + path, params=params) as r:
            r.raise_for_status()
            body = await r.json(content_type=None)
        if body.get("code") != "0":
            raise RuntimeError(f"OKX {path}: {body.get('msg')}")
        return body["data"]

    async def _instrument_list(self, family: str) -> list[dict]:
        hit = self._instruments.get(family)
        if hit and time.time() - hit[0] < INSTRUMENTS_TTL:
            return hit[1]
        rows = await self._okx("/api/v5/public/instruments", instType="OPTION", instFamily=family)
        rows = [r for r in rows if r.get("state") == "live"]
        self._instruments[family] = (time.time(), rows)
        return rows

    async def chain(self, family: str = "BTC-USD") -> dict:
        """Every live contract in a family, merged from all endpoints."""
        if family not in FAMILIES:
            raise ValueError(f"family must be one of {', '.join(FAMILIES)}")
        self.last_request = time.time()
        async with self._locks.setdefault(family, asyncio.Lock()):
            hit = self._chain.get(family)
            if hit and time.time() - hit[0] < CHAIN_TTL:
                return hit[1]
            inst, tickers, summary, marks, ois, index = await asyncio.gather(
                self._instrument_list(family),
                self._okx("/api/v5/market/tickers", instType="OPTION", instFamily=family),
                self._okx("/api/v5/public/opt-summary", instFamily=family),
                self._okx("/api/v5/public/mark-price", instType="OPTION", instFamily=family),
                self._okx("/api/v5/public/open-interest", instType="OPTION", instFamily=family),
                self._okx("/api/v5/market/index-tickers", instId="BTC-USD"),
            )
            idx = _f(index[0]["idxPx"]) if index else None
            tk = {t["instId"]: t for t in tickers}
            sm = {t["instId"]: t for t in summary}
            mk = {t["instId"]: t for t in marks}
            oi = {t["instId"]: t for t in ois}
            coin = family == "BTC-USD"
            contracts = []
            for i in inst:
                n = i["instId"]
                t, s, m, o = tk.get(n, {}), sm.get(n, {}), mk.get(n, {}), oi.get(n, {})
                contracts.append({
                    "name": n,
                    "family": family,
                    "expiry": n.split("-")[-3],
                    "exp_ms": int(i["expTime"]),
                    "strike": float(i["stk"]),
                    "type": i["optType"],
                    "unit": "BTC" if coin else "USD",
                    "bid": _f(t.get("bidPx")), "bid_sz": _f(t.get("bidSz")),
                    "ask": _f(t.get("askPx")), "ask_sz": _f(t.get("askSz")),
                    "last": _f(t.get("last")), "vol24h": _f(t.get("vol24h")),
                    "mark": _f(m.get("markPx")),
                    "iv": _f(s.get("markVol")), "iv_bid": _f(s.get("bidVol")), "iv_ask": _f(s.get("askVol")),
                    "delta": _f(s.get("deltaBS")), "gamma": _f(s.get("gammaBS")),
                    "theta": _f(s.get("thetaBS")), "vega": _f(s.get("vegaBS")),
                    "fwd": _f(s.get("fwdPx")),
                    "oi": _f(o.get("oi")), "oi_btc": _f(o.get("oiCcy")), "oi_usd": _f(o.get("oiUsd")),
                    "ct_mult": _f(i.get("ctMult")), "tick": _f(i.get("tickSz")),
                })
            contracts.sort(key=lambda c: (c["exp_ms"], c["strike"], c["type"]))
            expiries = []
            for e in sorted({c["expiry"] for c in contracts}, key=lambda e: e):
                cs = [c for c in contracts if c["expiry"] == e]
                expiries.append({
                    "code": e, "exp_ms": cs[0]["exp_ms"], "count": len(cs),
                    "oi_btc": sum(c["oi_btc"] or 0 for c in cs),
                })
            snap = {"family": family, "label": FAMILIES[family], "index": idx, "ts": int(time.time() * 1000),
                    "expiries": expiries, "contracts": contracts}
            self._chain[family] = (time.time(), snap)
            return snap

    # ---------- single contract ----------
    async def find(self, query: str, family_hint: str | None = None) -> tuple[dict | None, list[str], dict]:
        """Resolve a typed name to a contract. Returns (contract, suggestions, parsed)."""
        q = query.strip()
        parsed = parse_query(q)
        families = [parsed["family"]] if parsed["family"] else (
            [family_hint] + [f for f in FAMILIES if f != family_hint] if family_hint in FAMILIES else list(FAMILIES))
        chains = [await self.chain(f) for f in families]
        allc = [c for ch in chains for c in ch["contracts"]]
        exact = next((c for c in allc if c["name"].upper() == q.upper()), None)
        if exact:
            return exact, [], parsed

        cands = [c for c in allc if c["family"] == families[0]]
        if "expiry" in parsed:
            cands = [c for c in cands if c["expiry"] == parsed["expiry"]]
        elif "expiry_dm" in parsed:
            day, mon, yy = parsed["expiry_dm"]
            def match(c):
                d = datetime.strptime(c["expiry"], "%y%m%d")
                return d.day == day and d.month == mon and (yy is None or d.year % 100 == yy)
            cands = [c for c in cands if match(c)]
            if yy is None and cands:  # no year given: nearest upcoming one
                first = min(c["exp_ms"] for c in cands)
                cands = [c for c in cands if c["exp_ms"] == first]
        if "type" in parsed:
            cands = [c for c in cands if c["type"] == parsed["type"]]
        if "strike" in parsed and cands:
            exact_k = [c for c in cands if c["strike"] == parsed["strike"]]
            if exact_k and ("expiry" in parsed or "expiry_dm" in parsed) and "type" in parsed:
                return exact_k[0], [], parsed
            cands.sort(key=lambda c: (abs(c["strike"] - parsed["strike"]), c["exp_ms"]))
        elif not ({"expiry", "expiry_dm"} & parsed.keys()):
            # Free text: fall back to substring match on the name.
            needle = re.sub(r"\s+", "-", q.upper())
            cands = [c for c in allc if needle in c["name"]]
        return None, [c["name"] for c in cands[:12]], parsed

    async def detail(self, name: str) -> dict:
        """Order book and recent candles for one contract (fetched live)."""
        book, candles = await asyncio.gather(
            self._okx("/api/v5/market/books", instId=name, sz=10),
            self._okx("/api/v5/market/candles", instId=name, bar="1H", limit=168),
            return_exceptions=True,
        )
        out: dict = {"book": None, "candles": []}
        if not isinstance(book, Exception) and book:
            b = book[0]
            out["book"] = {"bids": [[float(p), float(s)] for p, s, *_ in b["bids"]],
                           "asks": [[float(p), float(s)] for p, s, *_ in b["asks"]]}
        if not isinstance(candles, Exception):
            out["candles"] = [{"time": int(c[0]), "open": float(c[1]), "high": float(c[2]),
                               "low": float(c[3]), "close": float(c[4]), "volume": float(c[5])}
                              for c in reversed(candles)]
        return out

    # ---------- context for the chat ----------
    @staticmethod
    def max_pain(contracts: list[dict]) -> float | None:
        """Strike where option holders' total payoff at expiry would be smallest."""
        strikes = sorted({c["strike"] for c in contracts})
        best, best_val = None, None
        for k in strikes:
            val = sum((c["oi_btc"] or 0) * (max(0.0, k - c["strike"]) if c["type"] == "C" else max(0.0, c["strike"] - k))
                      for c in contracts)
            if best_val is None or val < best_val:
                best, best_val = k, val
        return best

    @staticmethod
    def describe(c: dict, idx: float) -> list[str]:
        """Every number we have about one contract, for the chat."""
        def px(v):
            if v is None:
                return "n/a"
            if c["unit"] == "BTC":
                return f"{v:.4f} BTC (≈ ${v * idx:,.0f})"
            return f"${v:,.2f}"
        mark_usd = (c["mark"] or 0) * (idx if c["unit"] == "BTC" else 1)
        be = c["strike"] + mark_usd if c["type"] == "C" else c["strike"] - mark_usd
        days = (c["exp_ms"] - time.time() * 1000) / 86_400_000
        itm = (idx > c["strike"]) if c["type"] == "C" else (idx < c["strike"])
        g = lambda v, f: "n/a" if v is None else f.format(v)
        return [
            f"#### {c['name']} ({'call' if c['type'] == 'C' else 'put'}, "
            f"{'coin-margined, priced in BTC' if c['unit'] == 'BTC' else 'USD-margined, priced in USD'})",
            f"- Strike ${c['strike']:,.0f} ({(c['strike'] - idx) / idx * 100:+.2f}% vs index), "
            f"{'in' if itm else 'out of'} the money; expires "
            f"{datetime.fromtimestamp(c['exp_ms'] / 1000, timezone.utc):%Y-%m-%d %H:%M} UTC ({days:.2f} days)",
            f"- Mark {px(c['mark'])}; bid {px(c['bid'])} x {g(c['bid_sz'], '{:,.0f}')}; "
            f"ask {px(c['ask'])} x {g(c['ask_sz'], '{:,.0f}')}; last {px(c['last'])}; 24h volume {g(c['vol24h'], '{:,.0f}')} contracts",
            f"- IV mark {g(c['iv'] and c['iv'] * 100, '{:.1f}%')} (bid {g(c['iv_bid'] and c['iv_bid'] * 100, '{:.1f}%')}, "
            f"ask {g(c['iv_ask'] and c['iv_ask'] * 100, '{:.1f}%')})",
            f"- Delta {g(c['delta'], '{:.3f}')}, gamma {g(c['gamma'], '{:.2e}')} per $1, "
            f"theta {g(c['theta'], '${:,.2f}')}/day, vega {g(c['vega'], '${:,.2f}')} per vol point",
            f"- Open interest {g(c['oi'], '{:,.0f}')} contracts = {g(c['oi_btc'], '{:,.2f}')} BTC (${g(c['oi_usd'], '{:,.0f}')}); "
            f"1 contract = {c['ct_mult']} BTC; breakeven at expiry ${be:,.0f}; forward {g(c['fwd'], '${:,.1f}')}",
        ]

    async def context(self, message: str = "", selected: str | None = None) -> list[str]:
        """Options section for the chat: market summary, near-the-money table, and full
        details for any contract named in the message or open in the options panel."""
        snap = await self.chain("BTC-USD")
        idx = snap["index"] or 0
        cs = snap["contracts"]
        out = ["", f"### OKX BTC options (live; index ${idx:,.1f})",
               "Coin-margined BTC-USD family unless noted: prices in BTC (USD ≈ BTC price × index); "
               "1 contract = 0.01 BTC. IV is annualized."]
        calls = sum(c["oi_btc"] or 0 for c in cs if c["type"] == "C")
        puts = sum(c["oi_btc"] or 0 for c in cs if c["type"] == "P")
        if calls:
            out.append(f"Total open interest: calls {calls:,.0f} BTC, puts {puts:,.0f} BTC (put/call {puts / calls:.2f})")
        out += ["", "Expiries (ATM IV, open interest, max pain, biggest OI strikes):"]
        for e in snap["expiries"]:
            ec = [c for c in cs if c["expiry"] == e["code"]]
            atm = min(ec, key=lambda c: abs(c["strike"] - idx))
            top = sorted(ec, key=lambda c: c["oi_btc"] or 0, reverse=True)[:3]
            days = (e["exp_ms"] - time.time() * 1000) / 86_400_000
            iv = f"{atm['iv'] * 100:.1f}%" if atm["iv"] else "n/a"
            out.append(f"- {e['code']} ({expiry_label(e['code'])}, {days:.1f}d): ATM IV {iv}; OI {e['oi_btc']:,.0f} BTC; "
                       f"max pain ${self.max_pain(ec) or 0:,.0f}; top OI "
                       + ", ".join(f"{c['strike']:,.0f}{c['type']} {c['oi_btc'] or 0:,.0f}" for c in top))

        out += ["", "Near-the-money chain, 4 nearest expiries (strike: call mark/IV/delta/OI BTC | put mark/IV/delta/OI BTC):"]
        for e in snap["expiries"][:4]:
            ec = {(c["strike"], c["type"]): c for c in cs if c["expiry"] == e["code"]}
            strikes = sorted({k for k, _ in ec}, key=lambda k: abs(k - idx))[:9]
            out.append(f"{e['code']}:")
            for k in sorted(strikes):
                cell = lambda c: ("n/a" if not c else
                                  f"{c['mark'] or 0:.4f}/{(c['iv'] or 0) * 100:.0f}%/{c['delta'] or 0:+.2f}/{c['oi_btc'] or 0:,.0f}")
                out.append(f"  {k:,.0f}: C {cell(ec.get((k, 'C')))} | P {cell(ec.get((k, 'P')))}")

        # Contracts the user is asking about.
        wanted: list[str] = []
        if selected:
            wanted.append(selected)
        text = (message or "").upper()
        exact_re = r"BTC-USD(?:_UM)?-\d{6}-\d+(?:\.\d+)?-[CP]"
        wanted += re.findall(exact_re, text)
        # Loose mentions ("27nov 75k call"): parse what's left after removing exact names.
        loose = re.sub(r"[^A-Z0-9._\-\s]", " ", re.sub(exact_re, " ", text))
        pq = parse_query(loose)
        if "strike" in pq and "type" in pq and ({"expiry", "expiry_dm"} & pq.keys()):
            wanted.append(loose)
        seen, details = set(), []
        for q in wanted[:5]:
            try:
                c, _, _ = await self.find(q)
            except Exception:
                c = None
            if c and c["name"] not in seen:
                seen.add(c["name"])
                cidx = (await self.chain(c["family"]))["index"] or idx
                desc = self.describe(c, cidx)
                if c["name"] == selected:
                    desc[0] += " (open in the user's options panel)"
                details += [""] + desc
        if details:
            out += ["", "### Contracts the user is looking at or asked about"] + details
        return out


def expiry_label(code: str) -> str:
    d = datetime.strptime(code, "%y%m%d").replace(tzinfo=timezone.utc)
    return d.strftime("%a %d %b %Y")
