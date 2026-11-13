"""Trading core shared by the browser ticket and the btcliq-trade command line.

An order always takes two steps:

1. preview(request) builds the exact OKX order, prices it (value, premium, fees, margin,
   worst case), runs every safety rail and stores it under a short id for a minute or so.
2. place(id) runs the rails again with fresh prices and only then sends it to OKX.

Requests look like:
    {"instId": "BTC-USD-261127-75000-C", "side": "buy", "sz": 2, "ordType": "limit",
     "px": 0.0125, "reduce": false}
ordType is limit, post_only, ioc, fok or market. Sizes are in contracts (spot: BTC).
Prices are in the instrument's own unit (coin-margined options: BTC; everything else: USD/USDT).
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import secrets
import time
from decimal import Decimal, InvalidOperation
from pathlib import Path

import aiohttp

from . import rails as R
from .okx_private import OKXClient, OKXError, OKXNotConfigured

log = logging.getLogger("btcliq")
ORD_TYPES = ("limit", "post_only", "ioc", "fok", "market")
ACCT_LEVELS = {"1": "Spot mode", "2": "Futures mode", "3": "Multi-currency margin", "4": "Portfolio margin"}
OPTION_RE = re.compile(r"^BTC-USD(?:_UM)?-\d{6}-\d+(?:\.\d+)?-[CP]$")
FUTURES_RE = re.compile(r"^BTC-USDT?-\d{6}$")
SPOT_RE = re.compile(r"^BTC-(USDT|USDC)$")


class RailsBlocked(Exception):
    def __init__(self, preview: dict):
        self.preview = preview
        failed = [c["msg"] for c in preview["checks"] if not c["ok"]]
        super().__init__("Blocked by the safety rails: " + "; ".join(failed))


class OrderInputError(ValueError):
    pass


def inst_type_of(inst_id: str) -> str:
    if inst_id in ("BTC-USDT-SWAP", "BTC-USD-SWAP", "BTC-USDC-SWAP"):
        return "SWAP"
    if OPTION_RE.match(inst_id):
        return "OPTION"
    if FUTURES_RE.match(inst_id):
        return "FUTURES"
    if SPOT_RE.match(inst_id):
        return "SPOT"
    raise OrderInputError(f"{inst_id!r} isn't a BTC instrument btcliq trades. Examples: BTC-USDT-SWAP, "
                          "BTC-USD-SWAP, BTC-USD-261127, BTC-USDT, BTC-USD-261127-75000-C")


def family_of(inst_id: str) -> str:
    """BTC-USD-261127-75000-C -> BTC-USD; BTC-USD_UM-... -> BTC-USD_UM; BTC-USDT-SWAP -> BTC-USDT."""
    return "-".join(inst_id.split("-")[:2])


def _f(x) -> float | None:
    try:
        return float(x) if x not in (None, "") else None
    except (TypeError, ValueError):
        return None


def _dec(x, what: str) -> Decimal:
    try:
        d = Decimal(str(x).strip())
    except (InvalidOperation, AttributeError):
        raise OrderInputError(f"{what} must be a number, not {x!r}")
    if not d.is_finite() or d <= 0:
        raise OrderInputError(f"{what} must be greater than zero")
    return d


def _multiple(v: Decimal, step: str) -> bool:
    s = Decimal(step)
    return s <= 0 or (v / s) % 1 == 0


def _snap(v: Decimal, step: str) -> Decimal:
    s = Decimal(step)
    return (v / s).to_integral_value() * s


def _plain(d: Decimal) -> str:
    """Decimal to a plain string OKX accepts (no exponent, no trailing zeros)."""
    s = format(d.normalize(), "f")
    return s


def contract_math(meta: dict) -> dict:
    """What one unit of size is worth, from OKX's instrument spec."""
    t = meta["instType"]
    if t == "SPOT":
        return {"unit": "BTC", "btc_per": 1.0, "usd_per": None, "price_ccy": meta.get("quoteCcy") or "USDT"}
    per = (_f(meta.get("ctVal")) or 0) * (_f(meta.get("ctMult")) or 1)
    if meta.get("ctValCcy") == "USD":          # inverse: each contract is a fixed dollar amount
        return {"unit": "contracts", "btc_per": None, "usd_per": per, "price_ccy": "USD"}
    price_ccy = "BTC" if (t == "OPTION" and meta.get("settleCcy") == "BTC") else "USD"
    return {"unit": "contracts", "btc_per": per, "usd_per": None, "price_ccy": price_ccy}


def describe_instrument(meta: dict) -> str:
    t, cm = meta["instType"], contract_math(meta)
    kind = {"SWAP": "Perpetual", "FUTURES": "Dated future", "OPTION": "Option", "SPOT": "Spot"}[t]
    if t == "SPOT":
        size = "size in BTC"
    elif cm["usd_per"]:
        size = f"1 contract = ${cm['usd_per']:,.0f} (coin-margined, settles in {meta.get('settleCcy')})"
    else:
        size = f"1 contract = {cm['btc_per']:g} BTC (settles in {meta.get('settleCcy')})"
    if t == "OPTION":
        size += f"; priced in {cm['price_ccy']} per 1 BTC"
    return f"{kind}: {size}"


class PreviewStore:
    """Pending previews on disk, so a preview made by one process can be confirmed by another."""

    def __init__(self, data_dir: Path):
        self.path = Path(data_dir) / "trade_previews.json"

    def _load(self) -> dict:
        try:
            d = json.loads(self.path.read_text())
        except (FileNotFoundError, ValueError):
            return {}
        now = time.time()
        return {k: v for k, v in d.items() if v.get("expires", 0) > now}

    def _save(self, d: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(d))
        tmp.replace(self.path)

    def put(self, pid: str, entry: dict) -> None:
        d = self._load()
        d[pid] = entry
        self._save(d)

    def take(self, pid: str) -> dict | None:
        """Single use: a preview can only be confirmed once."""
        d = self._load()
        entry = d.pop(pid, None)
        self._save(d)
        return entry


class Trader:
    def __init__(self, session: aiohttp.ClientSession, data_dir: Path | str,
                 client: OKXClient | None = None, rails: R.Rails | None = None):
        self.data_dir = Path(data_dir)
        self.okx = client or OKXClient.from_env(session)
        self.rails = rails or R.Rails()
        self.halt = R.Halt(self.data_dir)
        self.journal = R.Journal(self.data_dir)
        self.previews = PreviewStore(self.data_dir)
        self._meta: dict[str, tuple[float, dict]] = {}
        self._cache: dict[str, tuple[float, object]] = {}
        self._lock = asyncio.Lock()      # one order at a time per process

    # ---------- small cached reads ----------

    async def _cached(self, key: str, ttl: float, fn):
        hit = self._cache.get(key)
        if hit and time.time() - hit[0] < ttl:
            return hit[1]
        v = await fn()
        self._cache[key] = (time.time(), v)
        return v

    async def instrument(self, inst_id: str) -> dict:
        inst_id = inst_id.strip().upper()
        t = inst_type_of(inst_id)
        hit = self._meta.get(inst_id)
        if hit and time.time() - hit[0] < 300:
            return hit[1]
        try:
            d = await self.okx.public("/api/v5/public/instruments", instType=t, instId=inst_id,
                                      instFamily=family_of(inst_id) if t in ("OPTION", "FUTURES") else None)
        except OKXError as e:
            if e.code != "51001":
                raise
            d = []
        if not d:
            raise OrderInputError(f"OKX doesn't list {inst_id} (expired or mistyped?)")
        meta = d[0]
        if meta.get("state") not in (None, "", "live"):
            raise OrderInputError(f"{inst_id} isn't trading right now (state: {meta.get('state')})")
        self._meta[inst_id] = (time.time(), meta)
        return meta

    async def instrument_list(self) -> list[dict]:
        """BTC perps, spot pairs and live dated futures, for the ticket's picker."""
        async def fetch():
            out = [{"instId": "BTC-USDT-SWAP", "label": "Perpetual, USDT-margined"},
                   {"instId": "BTC-USD-SWAP", "label": "Perpetual, coin-margined"},
                   {"instId": "BTC-USDT", "label": "Spot"}, {"instId": "BTC-USDC", "label": "Spot"}]
            for fam in ("BTC-USD", "BTC-USDT"):
                try:
                    d = await self.okx.public("/api/v5/public/instruments", instType="FUTURES", instFamily=fam)
                except Exception:
                    continue
                for i in sorted(d, key=lambda i: int(i.get("expTime") or 0)):
                    out.append({"instId": i["instId"], "label": f"Future, {'coin' if fam == 'BTC-USD' else 'USDT'}-margined"})
            return out
        return await self._cached("instruments", 600, fetch)

    async def quote(self, inst_id: str) -> dict:
        meta = await self.instrument(inst_id)
        t = meta["instType"]
        jobs = [self.okx.public("/api/v5/market/ticker", instId=inst_id),
                self.okx.public("/api/v5/market/index-tickers", instId="BTC-USD" if t != "SPOT" else inst_id)]
        if t != "SPOT":
            jobs.append(self.okx.public("/api/v5/public/mark-price", instType=t, instId=inst_id,
                                        instFamily=family_of(inst_id) if t == "OPTION" else None))
        res = await asyncio.gather(*jobs, return_exceptions=True)
        tk = res[0][0] if not isinstance(res[0], Exception) and res[0] else {}
        ix = res[1][0] if not isinstance(res[1], Exception) and res[1] else {}
        mk = res[2][0] if len(res) > 2 and not isinstance(res[2], Exception) and res[2] else {}
        q = {"bid": _f(tk.get("bidPx")), "ask": _f(tk.get("askPx")), "last": _f(tk.get("last")),
             "bid_sz": _f(tk.get("bidSz")), "ask_sz": _f(tk.get("askSz")),
             "mark": _f(mk.get("markPx")), "index": _f(ix.get("idxPx"))}
        if q["mark"] is None and q["bid"] and q["ask"]:
            q["mark"] = (q["bid"] + q["ask"]) / 2
        if q["mark"] is None:
            q["mark"] = q["last"]
        return q

    async def account_config(self) -> dict:
        async def fetch():
            d = await self.okx.get("/api/v5/account/config")
            return d[0] if d else {}
        return await self._cached("config", 60, fetch)

    async def _fee_rate(self, meta: dict) -> float | None:
        t = meta["instType"]
        key = f"fee:{t}:{meta.get('instFamily') or meta['instId']}"

        async def fetch():
            params = {"instType": t}
            if t == "SPOT":
                params["instId"] = meta["instId"]
            else:
                params["instFamily"] = meta.get("instFamily")
            d = await self.okx.get("/api/v5/account/trade-fee", **params)
            return d[0] if d else {}
        try:
            fee = await self._cached(key, 3600, fetch)
        except Exception:
            return None
        settle = meta.get("settleCcy")
        for k in (("takerU",) if settle == "USDT" else ("takerUSDC",) if settle == "USDC" else ()) + ("taker",):
            v = _f(fee.get(k))
            if v is not None:
                return abs(v)
        return None

    async def _realized_today(self, index: float | None) -> float | None:
        """Realized P&L plus fees since 00:00 UTC, in USD, from OKX's fill history."""
        async def fetch():
            begin, total, after = R.utc_midnight_ms(), 0.0, None
            for _ in range(5):          # up to 500 fills
                d = await self.okx.get("/api/v5/trade/fills", begin=str(begin), limit="100", after=after)
                for f in d:
                    pnl, fee = _f(f.get("fillPnl")) or 0.0, _f(f.get("fee")) or 0.0
                    for amt, ccy in ((pnl, f.get("ccy") or f.get("feeCcy")), (fee, f.get("feeCcy"))):
                        if not amt:
                            continue
                        if ccy == "BTC":
                            amt *= index or 0
                        elif ccy not in ("USDT", "USDC", "USD", None, ""):
                            continue
                        total += amt
                if len(d) < 100:
                    break
                after = d[-1].get("billId")
            return total
        try:
            return await self._cached("realized", 20, fetch)
        except Exception as e:
            log.warning("Couldn't read today's fills: %s", e)
            return None

    async def _positions(self, inst_id: str) -> list[dict]:
        return await self.okx.get("/api/v5/account/positions", instId=inst_id)

    async def _leverage(self, inst_id: str, mgn_mode: str) -> float | None:
        d = await self.okx.get("/api/v5/account/leverage-info", instId=inst_id, mgnMode=mgn_mode)
        levs = [_f(x.get("lever")) for x in d if _f(x.get("lever"))]
        return max(levs) if levs else None

    async def _spot_balance(self, ccy: str) -> float:
        d = await self.okx.get("/api/v5/account/balance", ccy=ccy)
        for det in (d[0].get("details", []) if d else []):
            if det.get("ccy") == ccy:
                return _f(det.get("availBal")) or 0.0
        return 0.0

    def usage(self, realized: float | None) -> dict:
        u = self.journal.today()
        u["halted"] = self.halt.state()
        u["realized_usd"] = realized
        return u

    # ---------- preview ----------

    def _parse_request(self, req: dict) -> dict:
        inst_id = str(req.get("instId") or "").strip().upper()
        if not inst_id:
            raise OrderInputError("instId is required, e.g. BTC-USDT-SWAP")
        side = str(req.get("side") or "").lower()
        if side not in ("buy", "sell"):
            raise OrderInputError("side must be buy or sell")
        ord_type = str(req.get("ordType") or ("market" if req.get("px") in (None, "") else "limit")).lower()
        if ord_type not in ORD_TYPES:
            raise OrderInputError(f"ordType must be one of {', '.join(ORD_TYPES)}")
        sz = _dec(req.get("sz"), "Size")
        px = None if ord_type == "market" else _dec(req.get("px"), "Price")
        return {"instId": inst_id, "side": side, "ordType": ord_type, "sz": sz, "px": px,
                "reduce": bool(req.get("reduce"))}

    async def build(self, req: dict) -> dict:
        """Price and check an order without storing or sending it."""
        r = self._parse_request(req)
        inst_id, side, sz, px = r["instId"], r["side"], r["sz"], r["px"]
        self.rails.reload()
        if self.rails.error:
            raise OrderInputError(self.rails.error)
        meta = await self.instrument(inst_id)
        t, cm = meta["instType"], contract_math(meta)
        cfg = self.rails.cfg

        # Input checks against OKX's own rules (sizes and ticks), before anything else.
        if not _multiple(sz, meta["lotSz"]):
            raise OrderInputError(f"Size must be a multiple of {meta['lotSz']} (try {_plain(_snap(sz, meta['lotSz']))})")
        if sz < Decimal(meta["minSz"]):
            raise OrderInputError(f"Minimum size is {meta['minSz']}")
        if px is not None and not _multiple(px, meta["tickSz"]):
            raise OrderInputError(f"Price must be a multiple of {meta['tickSz']} (try {_plain(_snap(px, meta['tickSz']))})")
        maxsz = meta.get("maxMktSz" if r["ordType"] == "market" else "maxLmtSz")
        if maxsz and sz > Decimal(maxsz):
            raise OrderInputError(f"OKX's maximum size for this order type is {maxsz}")

        acct, q, pend = await asyncio.gather(self.account_config(), self.quote(inst_id),
                                             self.okx.get("/api/v5/trade/orders-pending"))
        acct_lv, pos_mode = str(acct.get("acctLv", "")), acct.get("posMode", "net_mode")
        if t != "SPOT" and acct_lv == "1":
            raise OrderInputError("Your OKX account is in Spot mode, which can't trade derivatives. "
                                  "Switch it to Futures mode in OKX (Trade → settings → account mode).")
        mgn = cfg["account"]["derivatives_margin"]
        if t == "SPOT":
            spot_mode = cfg["account"]["spot_mode"]
            td_mode = ("cross" if acct_lv in ("3", "4") else "cash") if spot_mode == "auto" else spot_mode
        else:
            td_mode = mgn

        index = q["index"] or (q["mark"] if t == "SPOT" else None)
        ref = q["mark"] if q["mark"] else q["last"]
        if ref is None:
            raise OrderInputError(f"No price for {inst_id} right now, so the order can't be checked")
        fill_px = float(px) if px is not None else (q["ask"] if side == "buy" else q["bid"]) or ref
        szf = float(sz)

        # Value of the order in USD (the underlying, not the premium).
        if t == "SPOT":
            notional = szf * fill_px
            btc = szf
        elif cm["usd_per"]:
            notional = szf * cm["usd_per"]
            btc = notional / index if index else None
        else:
            btc = szf * cm["btc_per"]
            notional = btc * (index if t == "OPTION" else fill_px) if (index or t != "OPTION") else 0.0
        premium = premium_native = None
        if t == "OPTION":
            premium_native = szf * cm["btc_per"] * fill_px
            premium = premium_native * (index or 0) if cm["price_ccy"] == "BTC" else premium_native

        # The current position and what it becomes.
        closing, opens_short, post_ct, post_usd, pos_side, reduce_only = False, False, None, None, None, None
        before = None
        if t == "SPOT":
            base_avail = await self._spot_balance("BTC")
            before = {"btc_available": base_avail}
            closing = side == "sell"
            if side == "sell" and szf > base_avail + 1e-12:
                raise OrderInputError(f"You have {base_avail:g} BTC available to sell")
            if r["reduce"] and side == "buy":
                raise OrderInputError("Reduce-only doesn't apply to spot buys")
        else:
            rows = await self._positions(inst_id)
            long_q = sum(_f(p.get("pos")) or 0 for p in rows if p.get("posSide") == "long")
            short_q = sum(_f(p.get("pos")) or 0 for p in rows if p.get("posSide") == "short")
            net = sum(_f(p.get("pos")) or 0 for p in rows if p.get("posSide") in ("net", "", None)) + long_q - short_q
            before = {"contracts": net}
            delta = szf if side == "buy" else -szf
            if t == "OPTION" or pos_mode == "net_mode":
                after = net + delta
                shrinks = abs(after) < abs(net) - 1e-12 and (net == 0 or after * net >= 0)
                closing = shrinks
                if r["reduce"] and not shrinks:
                    raise OrderInputError(f"Reduce-only: your position is {net:g} contracts, so this "
                                          f"{side} wouldn't reduce it" + (" (or is larger than it)" if net else ""))
                if t != "OPTION" and r["reduce"]:
                    reduce_only = True
                if t == "OPTION":
                    opens_short = after < 0 and after < net
            else:   # long/short mode: closing is chosen by posSide
                if r["reduce"]:
                    pos_side = "long" if side == "sell" else "short"
                    held = long_q if pos_side == "long" else short_q
                    if szf > held + 1e-12:
                        raise OrderInputError(f"Close: you hold {held:g} {pos_side} contracts")
                    closing = True
                    after = (long_q - szf) - short_q if pos_side == "long" else long_q - (short_q - szf)
                else:
                    pos_side = "long" if side == "buy" else "short"
                    after = net + delta
            post_ct = abs(after)
            if cm["usd_per"]:
                post_usd = post_ct * cm["usd_per"]
            elif index:
                post_usd = post_ct * cm["btc_per"] * index
            before["after"] = after

        lever = None
        if t in ("SWAP", "FUTURES") and not closing:
            try:
                lever = await self._leverage(inst_id, mgn)
            except OKXError as e:
                log.warning("Couldn't read leverage for %s: %s", inst_id, e)

        realized = None
        if not closing and R._lim(cfg["daily"]["max_loss_usd"]) is not None:
            realized = await self._realized_today(index)
        fee_rate = await self._fee_rate(meta)
        fee = None
        if fee_rate is not None:
            fee = fee_rate * notional
            if t == "OPTION" and premium:
                fee = min(fee, 0.125 * premium)   # OKX caps option fees at 12.5% of the premium

        band_pct = (float(px) - ref) / ref * 100 if px is not None and ref else None
        o = {"inst_type": t, "inst_id": inst_id, "side": side, "ord_type": r["ordType"], "sz": szf,
             "notional_usd": notional, "premium_usd": premium, "closing": closing, "ref_px": ref,
             "px": float(px) if px is not None else None, "band_pct": band_pct,
             "post_position_usd": None if closing else post_usd,
             "post_position_contracts": None if closing else post_ct,
             "opens_short_option": opens_short, "leverage": lever, "open_orders": len(pend)}
        checks = R.evaluate(cfg, o, self.usage(realized))

        # Worst case, margin, breakeven.
        worst, worst_text, margin, breakeven = None, None, None, None
        strike = _f(inst_id.split("-")[3]) if t == "OPTION" else None
        if t == "OPTION" and premium is not None:
            prem_usd_per_btc = premium / (szf * cm["btc_per"]) if szf else 0
            if side == "buy" and not closing:
                worst = premium + (fee or 0)
                worst_text = "The premium you pay (plus fees) if it expires worthless"
            elif opens_short:
                if inst_id.endswith("-C"):
                    worst_text = "Unlimited: a short call loses more the higher BTC goes"
                else:
                    worst = strike * cm["btc_per"] * szf - premium
                    worst_text = "If BTC went to zero: strike value minus the premium received"
            breakeven = strike + prem_usd_per_btc if inst_id.endswith("-C") else strike - prem_usd_per_btc
        elif t == "SPOT" and side == "buy":
            worst, worst_text = notional, "The full cost if BTC went to zero"
        elif t in ("SWAP", "FUTURES") and not closing and lever:
            margin = notional / lever
            worst_text = (f"About ${margin:,.0f} of margin at {lever:g}x. With cross margin, losses can draw on "
                          "the rest of the account before liquidation")

        warnings = []
        if px is not None:
            if side == "buy" and q["ask"] and float(px) >= q["ask"]:
                warnings.append("Your buy price is at or above the best ask, so it will fill straight away as a taker"
                                if r["ordType"] != "post_only" else "Post-only: this price crosses the ask, so OKX will cancel it")
            if side == "sell" and q["bid"] and float(px) <= q["bid"]:
                warnings.append("Your sell price is at or below the best bid, so it will fill straight away as a taker"
                                if r["ordType"] != "post_only" else "Post-only: this price crosses the bid, so OKX will cancel it")
        if t == "OPTION" and q["bid"] and q["ask"] and q["mark"] and (q["ask"] - q["bid"]) / q["mark"] > 0.15:
            warnings.append(f"Wide market: the bid/ask spread is {(q['ask'] - q['bid']) / q['mark'] * 100:.0f}% of the mark")
        if t == "OPTION" and side == "sell" and not q["bid"]:
            warnings.append("There's no bid for this option right now")

        body = {"instId": inst_id, "tdMode": td_mode, "side": side, "ordType": r["ordType"], "sz": _plain(sz)}
        if px is not None:
            body["px"] = _plain(px)
        if pos_side and pos_mode == "long_short_mode" and t in ("SWAP", "FUTURES"):
            body["posSide"] = pos_side
        if reduce_only:
            body["reduceOnly"] = True
        if t == "SPOT" and r["ordType"] == "market":
            body["tgtCcy"] = "base_ccy"      # market size in BTC, like limit orders

        return {
            "env": self.okx.env_label,
            "request": {**req, "instId": inst_id},
            "okx_order": body,
            "instrument": {"instId": inst_id, "type": t, "about": describe_instrument(meta),
                           "tick": meta["tickSz"], "lot": meta["lotSz"], "min": meta["minSz"],
                           "price_ccy": cm["price_ccy"], "size_unit": cm["unit"]},
            "account": {"level": ACCT_LEVELS.get(acct_lv, acct_lv), "pos_mode": pos_mode, "td_mode": td_mode},
            "market": q,
            "numbers": {"notional_usd": notional, "btc": btc, "premium_usd": premium, "premium_native": premium_native,
                        "fee_usd": fee, "fee_rate": fee_rate, "margin_usd": margin, "leverage": lever,
                        "worst_case_usd": worst, "worst_case": worst_text, "breakeven": breakeven,
                        "fill_px": fill_px},
            "position": {**(before or {}), "closing": closing},
            "checks": checks,
            "blocked": R.blocked(checks),
            "warnings": warnings,
        }

    async def preview(self, req: dict, source: str = "ui") -> dict:
        p = await self.build(req)
        pid = secrets.token_hex(6)
        ttl = float(self.rails.cfg["order"]["preview_ttl_seconds"])
        p["id"], p["expires"] = pid, time.time() + ttl
        p["summary"] = summarize(p)
        self.previews.put(pid, {"request": p["request"], "expires": p["expires"], "source": source,
                                "okx_order": p["okx_order"], "blocked": p["blocked"]})
        return p

    # ---------- place, cancel, halt ----------

    async def place(self, pid: str, source: str = "ui") -> dict:
        async with self._lock:
            entry = self.previews.take(pid)
            if not entry:
                raise OrderInputError("That preview has expired or was already used. Preview the order again.")
            if entry.get("blocked"):
                raise OrderInputError("That preview was blocked by the safety rails, so it can't be placed. "
                                      "Change the order (or your limits) and preview again.")
            p = await self.build(entry["request"])     # fresh prices, fresh checks
            p["summary"] = summarize(p)
            if p["okx_order"] != entry["okx_order"]:
                raise OrderInputError("The order changed since you previewed it (your position or account mode "
                                      "changed). Preview it again.")
            if p["blocked"]:
                self.journal.add({"kind": "order", "status": "blocked", "source": source, "instId": p["request"]["instId"],
                                  "checks": [c for c in p["checks"] if not c["ok"]]})
                raise RailsBlocked(p)
            body = dict(p["okx_order"])
            body["clOrdId"] = "btcliq" + pid
            base = {"kind": "order", "source": source, "instId": body["instId"], "side": body["side"],
                    "sz": body["sz"], "px": body.get("px"), "ordType": body["ordType"], "clOrdId": body["clOrdId"],
                    "notional_usd": p["numbers"]["notional_usd"], "premium_usd": p["numbers"]["premium_usd"],
                    "closing": p["position"]["closing"], "env": self.okx.env_label}
            try:
                d = await self.okx.post("/api/v5/trade/order", body)
            except OKXError as e:
                self.journal.add({**base, "status": "rejected", "error": str(e)})
                raise
            except (asyncio.TimeoutError, aiohttp.ClientError) as e:
                # The order may or may not have reached OKX: look it up by our client id.
                found = None
                try:
                    found = (await self.okx.get("/api/v5/trade/order", instId=body["instId"], clOrdId=body["clOrdId"]) or [None])[0]
                except Exception:
                    pass
                if found:
                    self.journal.add({**base, "status": "placed", "ordId": found.get("ordId"), "note": "confirmed after a network error"})
                    return {"ok": True, "ordId": found.get("ordId"), "clOrdId": body["clOrdId"], "preview": p}
                self.journal.add({**base, "status": "unknown", "error": str(e)})
                raise OKXError("network", f"Network error while sending; the order may not have reached OKX ({e}). "
                                          "Check open orders before trying again.")
            ord_id = d[0].get("ordId") if d else None
            self.journal.add({**base, "status": "placed", "ordId": ord_id})
            self._cache.pop("realized", None)
            log.info("Order placed (%s, %s): %s %s %s @ %s, ordId %s", self.okx.env_label, source, body["side"],
                     body["sz"], body["instId"], body.get("px", "market"), ord_id)
            return {"ok": True, "ordId": ord_id, "clOrdId": body["clOrdId"], "preview": p}

    async def cancel(self, inst_id: str, ord_id: str, source: str = "ui") -> dict:
        d = await self.okx.post("/api/v5/trade/cancel-order", {"instId": inst_id, "ordId": ord_id})
        self.journal.add({"kind": "cancel", "source": source, "instId": inst_id, "ordId": ord_id})
        return {"ok": True, "ordId": (d[0] if d else {}).get("ordId", ord_id)}

    async def amend(self, inst_id: str, ord_id: str, new_px=None, new_sz=None, source: str = "ui") -> dict:
        """Change price or size of a resting order. Bigger sizes and new prices go through the rails."""
        pend = await self.okx.get("/api/v5/trade/orders-pending", instId=inst_id)
        cur = next((o for o in pend if o.get("ordId") == ord_id), None)
        if not cur:
            raise OrderInputError("That order isn't open any more")
        req = {"instId": inst_id, "side": cur["side"], "ordType": cur["ordType"] if cur["ordType"] in ORD_TYPES else "limit",
               "sz": new_sz or cur["sz"], "px": new_px or cur["px"],
               "reduce": cur.get("reduceOnly") == "true"}
        p = await self.build(req)
        # This order already counts as open, so don't hold it against the open-orders cap.
        p["checks"] = [c for c in p["checks"] if c["rule"] != "order.max_open_orders"]
        if R.blocked(p["checks"]):
            raise RailsBlocked(p)
        body = {"instId": inst_id, "ordId": ord_id}
        if new_px is not None:
            body["newPx"] = p["okx_order"]["px"]
        if new_sz is not None:
            body["newSz"] = p["okx_order"]["sz"]
        await self.okx.post("/api/v5/trade/amend-order", body)
        self.journal.add({"kind": "amend", "source": source, "instId": inst_id, "ordId": ord_id,
                          "newPx": body.get("newPx"), "newSz": body.get("newSz")})
        return {"ok": True, "ordId": ord_id}

    async def cancel_all(self, source: str = "ui") -> dict:
        pend = await self.okx.get("/api/v5/trade/orders-pending")
        canceled, failed = 0, []
        for i in range(0, len(pend), 20):
            batch = [{"instId": o["instId"], "ordId": o["ordId"]} for o in pend[i:i + 20]]
            try:
                d = await self.okx.post("/api/v5/trade/cancel-batch-orders", batch)
                canceled += sum(1 for x in d if str(x.get("sCode", "0")) == "0")
            except OKXError as e:
                # A partly failed batch still reports which ones went through.
                ok = [x for x in (e.data or []) if str(x.get("sCode", "")) == "0"]
                canceled += len(ok)
                failed.append(str(e))
        self.journal.add({"kind": "cancel_all", "source": source, "found": len(pend), "canceled": canceled})
        return {"found": len(pend), "canceled": canceled, "errors": failed}

    async def kill(self, reason: str = "", source: str = "ui") -> dict:
        """The kill switch: halt first (so nothing new goes out), then cancel every open order."""
        st = self.halt.set(reason or "Kill switch", source)
        self.journal.add({"kind": "halt", "source": source, "reason": st["reason"]})
        res = {"halted": st}
        try:
            res.update(await self.cancel_all(source))
        except (OKXError, OKXNotConfigured) as e:
            res["errors"] = [str(e)]
        return res

    def resume(self, source: str = "ui") -> dict:
        self.halt.clear()
        self.journal.add({"kind": "resume", "source": source})
        return {"halted": None}

    async def set_leverage(self, inst_id: str, lever: float, source: str = "cli") -> dict:
        meta = await self.instrument(inst_id)
        t = meta["instType"]
        if t not in ("SWAP", "FUTURES"):
            raise OrderInputError("Leverage applies to perpetuals and futures")
        cap = R._lim(self.rails.section(t)["max_leverage"])
        if cap is not None and lever > cap:
            raise OrderInputError(f"{lever:g}x is above your limit of {cap:g}x ({R.TYPE_SECTION[t]}.max_leverage)")
        body = {"instId": inst_id, "lever": f"{lever:g}", "mgnMode": self.rails.cfg["account"]["derivatives_margin"]}
        d = await self.okx.post("/api/v5/account/set-leverage", body)
        self.journal.add({"kind": "leverage", "source": source, "instId": inst_id, "lever": lever})
        return {"ok": True, "result": d}

    # ---------- account snapshot ----------

    def status(self) -> dict:
        self.rails.reload()
        return {"configured": self.okx.configured, "env": self.okx.env_label,
                "rails": self.rails.summary(), "trading": self.okx.configured and self.rails.active,
                "halted": self.halt.state(), "today": self.journal.today()}

    async def account(self) -> dict:
        out = self.status()
        if not self.okx.configured:
            return out
        names = ("config", "balance", "positions", "orders", "fills")
        res = await asyncio.gather(
            self.okx.get("/api/v5/account/config"),
            self.okx.get("/api/v5/account/balance"),
            self.okx.get("/api/v5/account/positions"),
            self.okx.get("/api/v5/trade/orders-pending"),
            self.okx.get("/api/v5/trade/fills", limit="20"),
            return_exceptions=True)
        errors = {}
        for n, v in zip(names, res):
            if isinstance(v, Exception):
                errors[n] = str(v)
        get = lambda i: [] if isinstance(res[i], Exception) else res[i]
        cfg = (get(0) or [{}])[0]
        bal = (get(1) or [{}])[0]
        out["account"] = {"level": ACCT_LEVELS.get(str(cfg.get("acctLv", "")), cfg.get("acctLv")),
                          "pos_mode": cfg.get("posMode"), "total_eq_usd": _f(bal.get("totalEq"))}
        out["balances"] = [{"ccy": d.get("ccy"), "eq": _f(d.get("eq")), "avail": _f(d.get("availBal")),
                            "eq_usd": _f(d.get("eqUsd")), "upl": _f(d.get("upl")), "frozen": _f(d.get("frozenBal"))}
                           for d in bal.get("details", []) if (_f(d.get("eq")) or 0) != 0]
        out["positions"] = [{"instId": p.get("instId"), "type": p.get("instType"), "pos": _f(p.get("pos")),
                             "posSide": p.get("posSide"), "avgPx": _f(p.get("avgPx")), "markPx": _f(p.get("markPx")),
                             "upl": _f(p.get("upl")), "uplRatio": _f(p.get("uplRatio")), "ccy": p.get("ccy"),
                             "notionalUsd": _f(p.get("notionalUsd")), "lever": _f(p.get("lever")),
                             "liqPx": _f(p.get("liqPx")), "mgnMode": p.get("mgnMode"),
                             "delta": _f(p.get("deltaPA")), "theta": _f(p.get("thetaPA"))}
                            for p in get(2) if _f(p.get("pos"))]
        out["orders"] = [{"instId": o.get("instId"), "type": o.get("instType"), "ordId": o.get("ordId"),
                          "clOrdId": o.get("clOrdId"), "side": o.get("side"), "posSide": o.get("posSide"),
                          "ordType": o.get("ordType"), "px": _f(o.get("px")), "sz": _f(o.get("sz")),
                          "filled": _f(o.get("accFillSz")), "state": o.get("state"),
                          "reduceOnly": o.get("reduceOnly") == "true", "cTime": int(o.get("cTime") or 0)}
                         for o in get(3)]
        out["fills"] = [{"instId": f.get("instId"), "side": f.get("side"), "px": _f(f.get("fillPx")),
                         "sz": _f(f.get("fillSz")), "fee": _f(f.get("fee")), "feeCcy": f.get("feeCcy"),
                         "pnl": _f(f.get("fillPnl")), "ts": int(f.get("ts") or 0), "ordId": f.get("ordId")}
                        for f in get(4)]
        out["errors"] = errors
        return out


def _g(v) -> str:
    return "—" if v is None else f"{v:.6g}" if abs(v) < 1000 else f"{v:,.1f}"


def summarize(p: dict) -> list[str]:
    """Plain-text lines for the command line and logs."""
    n, i, o, q = p["numbers"], p["instrument"], p["okx_order"], p["market"]
    unit = "BTC" if i["size_unit"] == "BTC" else "contracts"
    pxs = "market" if o["ordType"] == "market" else f"{o['px']} {i['price_ccy']}"
    lines = [f"{p['env'].upper()} · {o['side'].upper()} {o['sz']} {unit} {i['instId']} @ {pxs} ({o['ordType']}, {o['tdMode']}"
             + (f", {o['posSide']}" if o.get("posSide") else "") + (", reduce-only" if o.get("reduceOnly") else "") + ")",
             i["about"],
             f"Market: bid {_g(q['bid'])} / ask {_g(q['ask'])} / mark {_g(q['mark'])}" + (f" · index {q['index']:,.1f}" if q.get("index") else "")]
    val = f"Order value ${n['notional_usd']:,.2f}" + (f" ({n['btc']:.4f} BTC)" if n.get("btc") else "")
    if n.get("premium_usd") is not None:
        val += f" · premium ${n['premium_usd']:,.2f}"
        if i["price_ccy"] == "BTC":
            val += f" ({n['premium_native']:.6f} BTC)"
    if n.get("fee_usd") is not None:
        val += f" · est. fee ${n['fee_usd']:,.2f}"
    lines.append(val)
    if n.get("worst_case"):
        lines.append("Worst case: " + (f"${n['worst_case_usd']:,.2f}. " if n.get("worst_case_usd") is not None else "") + n["worst_case"])
    if n.get("breakeven"):
        lines.append(f"Breakeven at expiry: ${n['breakeven']:,.0f}")
    pos = p["position"]
    if "contracts" in pos:
        lines.append(f"Position: {pos['contracts']:g} → {pos.get('after', 0):g} contracts" + (" (closing)" if pos["closing"] else ""))
    for w in p["warnings"]:
        lines.append("! " + w)
    for c in p["checks"]:
        lines.append(("  ok  " if c["ok"] else "  NO  ") + c["msg"])
    lines.append("BLOCKED by the safety rails" if p["blocked"] else "Passes every rail")
    return lines
