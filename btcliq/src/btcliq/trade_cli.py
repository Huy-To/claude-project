"""btcliq-trade: trade BTC on OKX from the command line, through the same safety rails as the
browser ticket. Built for a Claude Code trading session as much as for you.

    btcliq-trade status                                   # keys, account mode, limits, halt, today's usage
    btcliq-trade account                                  # balances, positions, open orders, recent fills
    btcliq-trade quote BTC-USD-261127-75000-C             # spec and live bid/ask/mark
    btcliq-trade preview BTC-USDT-SWAP buy 1 --px 95000   # price + check an order; prints an id
    btcliq-trade place a1b2c3d4e5f6                       # send a previewed order (re-checked first)
    btcliq-trade amend BTC-USDT-SWAP 123456 --px 95100
    btcliq-trade cancel BTC-USDT-SWAP 123456
    btcliq-trade cancel-all
    btcliq-trade kill --reason "news spike"               # halt + cancel every open order
    btcliq-trade resume
    btcliq-trade leverage BTC-USDT-SWAP 3
    btcliq-trade limits                                   # the limits in force, after defaults

Output is JSON (add --text for readable lines). Exit codes: 0 ok, 1 error, 2 blocked by the
safety rails, 3 not set up (no keys or no limits file), 4 needs the user's approval (rules in
[option] ask): ask them, then `place <id> --accept <rules>` only if they say yes.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

import aiohttp

from .chat import load_dotenv
from .okx_private import OKXError, OKXNotConfigured
from .trader import NeedsApproval, OrderInputError, RailsBlocked, Trader

DEFAULT_DATA = Path(__file__).resolve().parents[2] / "data"


def _split(v: str | None) -> list[str]:
    return [x.strip() for x in (v or "").split(",") if x.strip()]


def _emit(obj, text: bool, lines: list[str] | None = None) -> None:
    if text and lines is not None:
        print("\n".join(lines))
    else:
        print(json.dumps(obj, indent=2, default=str))


def _account_lines(a: dict) -> list[str]:
    out = [f"{a['env'].upper()} · trading {'ON' if a['trading'] else 'OFF'}"
           + (f" · HALTED: {a['halted'].get('reason')}" if a.get("halted") else "")]
    if a["rails"]["error"]:
        out.append("Limits: " + a["rails"]["error"])
    if not a["configured"]:
        out.append("No OKX keys: add OKX_API_KEY, OKX_API_SECRET and OKX_API_PASSPHRASE to .env")
        return out
    acc = a.get("account") or {}
    if acc:
        out.append(f"Account: {acc.get('level')} · {acc.get('pos_mode')} · equity ${acc.get('total_eq_usd') or 0:,.2f}")
    for b in a.get("balances", []):
        out.append(f"  {b['ccy']:6} {b['eq']:.8g} (available {b['avail'] or 0:.8g}, ${b['eq_usd'] or 0:,.2f})")
    if a.get("positions"):
        out.append("Positions:")
        for p in a["positions"]:
            out.append(f"  {p['instId']:28} {p['pos']:+g} {p.get('posSide') or ''} avg {p['avgPx']} mark {p['markPx']} "
                       f"uPnL {p['upl']:+.6g} {p.get('ccy') or ''}")
    if a.get("orders"):
        out.append("Open orders:")
        for o in a["orders"]:
            out.append(f"  {o['ordId']} {o['instId']:28} {o['side']} {o['sz']:g} @ {o['px']} ({o['ordType']}, filled {o['filled'] or 0:g})")
    t = a["today"]
    out.append(f"Today (UTC): {t['orders']} orders via btcliq, opening volume ${t['notional_usd']:,.0f}")
    for k, v in (a.get("errors") or {}).items():
        out.append(f"! {k}: {v}")
    return out


async def run(args) -> int:
    load_dotenv()
    async with aiohttp.ClientSession() as session:
        tr = Trader(session, args.data_dir)
        c = args.cmd
        if c == "limits":
            _emit(tr.rails.summary(), False)
            return 0 if not tr.rails.error else 3
        if c == "status":
            st = tr.status()
            if st["configured"]:
                try:
                    acfg = await tr.account_config()
                    st["account"] = {"level": acfg.get("acctLv"), "pos_mode": acfg.get("posMode"),
                                     "permissions": acfg.get("perm"), "ip_allowlist": acfg.get("ip")}
                    st["keys_ok"] = True
                except OKXError as e:
                    st["keys_ok"], st["keys_error"] = False, str(e)
            _emit(st, args.text, [f"{st['env'].upper()} · keys {'missing' if not st['configured'] else 'ok' if st.get('keys_ok') else 'REJECTED: ' + st.get('keys_error', '')}",
                                  f"Limits: {st['rails']['error'] or ('enabled' if st['rails']['enabled'] else 'enabled = false')} ({st['rails']['file']})",
                                  f"Halted: {st['halted']['reason'] if st['halted'] else 'no'}",
                                  f"Today (UTC): {st['today']['orders']} orders, opening volume ${st['today']['notional_usd']:,.0f}"]
                  + ([f"Account: level {st['account']['level']}, {st['account']['pos_mode']}, permissions {st['account']['permissions']}, "
                      f"IP allowlist {st['account']['ip_allowlist'] or 'NONE (bind the key to your IP)'}"]
                     if st.get("account") else []))
            return 0 if st["configured"] and st.get("keys_ok") and not st["rails"]["error"] else 3
        if c == "account":
            a = await tr.account()
            _emit(a, args.text, _account_lines(a))
            return 0
        if c == "quote":
            meta = await tr.instrument(args.instId)
            q = await tr.quote(args.instId)
            from .trader import describe_instrument
            out = {"instId": meta["instId"], "type": meta["instType"], "about": describe_instrument(meta),
                   "tick": meta["tickSz"], "lot": meta["lotSz"], "min": meta["minSz"], **q}
            _emit(out, args.text, [f"{out['instId']} · {out['about']}", f"tick {out['tick']} · lot {out['lot']} · min {out['min']}",
                                   f"bid {q['bid']} × {q['bid_sz']} / ask {q['ask']} × {q['ask_sz']} / mark {q['mark']} / index {q['index']}"])
            return 0
        if c == "preview":
            req = {"instId": args.instId, "side": args.side, "sz": args.sz, "px": args.px, "reduce": args.reduce,
                   "mgnMode": args.margin,
                   "ordType": "market" if args.market else args.type or ("limit" if args.px else None)}
            p = await tr.preview(req, source="cli")
            ttl = int(p["expires"] - __import__("time").time())
            acc = f" --accept {','.join(p['asks'])}" if p["asks"] else ""
            lines = p["summary"] + ([f"Preview id {p['id']}: confirm within {ttl}s with  btcliq-trade place {p['id']}{acc}"]
                                    if not p["blocked"] else [])
            _emit(p, args.text, lines)
            return 2 if p["blocked"] else 4 if p["asks"] else 0
        if c == "place":
            r = await tr.place(args.id, source="cli", accept=_split(args.accept))
            _emit(r, args.text, r["preview"]["summary"] + [f"PLACED · ordId {r['ordId']} · clOrdId {r['clOrdId']}"])
            return 0
        if c == "amend":
            if args.px is None and args.sz is None:
                raise OrderInputError("Give --px and/or --sz")
            r = await tr.amend(args.instId, args.ordId, args.px, args.sz, source="cli", accept=_split(args.accept))
            _emit(r, args.text, [f"Amended {args.ordId}"])
            return 0
        if c == "cancel":
            r = await tr.cancel(args.instId, args.ordId, source="cli")
            _emit(r, args.text, [f"Canceled {r['ordId']}"])
            return 0
        if c == "cancel-all":
            r = await tr.cancel_all(source="cli")
            _emit(r, args.text, [f"Canceled {r['canceled']} of {r['found']} open orders"] + r["errors"])
            return 0 if not r["errors"] else 1
        if c == "kill":
            r = await tr.kill(args.reason, source="cli")
            _emit(r, args.text, [f"HALTED ({r['halted']['reason']}). Canceled {r.get('canceled', 0)} of {r.get('found', '?')} open orders."]
                  + r.get("errors", []) + ["Resume with: btcliq-trade resume"])
            return 0 if not r.get("errors") else 1
        if c == "resume":
            _emit(tr.resume(source="cli"), args.text, ["Trading resumed"])
            return 0
        if c == "leverage":
            r = await tr.set_leverage(args.instId, args.lever, source="cli")
            _emit(r, args.text, [f"Leverage for {args.instId} set to {args.lever:g}x"])
            return 0
    return 1


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(prog="btcliq-trade", description="Trade BTC on OKX through btcliq's safety rails",
                                formatter_class=argparse.RawDescriptionHelpFormatter, epilog=__doc__.split("\n\n", 1)[1])
    p.add_argument("--text", action="store_true", help="readable lines instead of JSON")
    p.add_argument("--data-dir", default=str(DEFAULT_DATA), help="where the journal, previews and halt switch live")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status", help="keys, account mode, limits file, halt, today's usage")
    sub.add_parser("account", help="balances, positions, open orders, recent fills")
    sub.add_parser("limits", help="the limits in force (your file merged over the defaults)")
    q = sub.add_parser("quote", help="instrument spec and live prices")
    q.add_argument("instId")
    pv = sub.add_parser("preview", help="price and check an order without sending it")
    pv.add_argument("instId")
    pv.add_argument("side", choices=["buy", "sell"])
    pv.add_argument("sz", help="size: contracts, or BTC for spot")
    pv.add_argument("--px", help="limit price in the instrument's unit (coin-margined options: BTC)")
    pv.add_argument("--type", choices=["limit", "post_only", "ioc", "fok"], help="order type (default limit)")
    pv.add_argument("--market", action="store_true", help="market order (not for options)")
    pv.add_argument("--reduce", action="store_true", help="only reduce or close an existing position")
    pv.add_argument("--margin", choices=["cross", "isolated"],
                    help="with --reduce: which part to close when you hold it in both cross and isolated margin")
    pl = sub.add_parser("place", help="send a previewed order")
    pl.add_argument("id")
    pl.add_argument("--accept", help="comma-separated 'ask' rules you approve going past (only with the user's say-so)")
    am = sub.add_parser("amend", help="change price or size of an open order")
    am.add_argument("instId")
    am.add_argument("ordId")
    am.add_argument("--px")
    am.add_argument("--sz")
    am.add_argument("--accept", help="comma-separated 'ask' rules you approve going past")
    cn = sub.add_parser("cancel", help="cancel one open order")
    cn.add_argument("instId")
    cn.add_argument("ordId")
    sub.add_parser("cancel-all", help="cancel every open order (doesn't halt)")
    k = sub.add_parser("kill", help="kill switch: halt trading and cancel every open order")
    k.add_argument("--reason", default="")
    sub.add_parser("resume", help="lift the halt")
    lv = sub.add_parser("leverage", help="set leverage for a perp or future (capped by your limits)")
    lv.add_argument("instId")
    lv.add_argument("lever", type=float)
    args = p.parse_args(argv)
    if getattr(args, "market", False) and args.px:
        p.error("--market and --px don't go together")
    try:
        code = asyncio.run(run(args))
    except NeedsApproval as e:
        _emit({"ok": False, "needs_approval": e.missing, "error": str(e), "preview": e.preview}, args.text,
              e.preview.get("summary", []) + [str(e)])
        code = 4
    except RailsBlocked as e:
        _emit({"ok": False, "blocked": True, "error": str(e), "preview": e.preview}, args.text, e.preview.get("summary", [str(e)]))
        code = 2
    except OKXNotConfigured as e:
        _emit({"ok": False, "error": str(e)}, args.text, [str(e)])
        code = 3
    except (OrderInputError, OKXError) as e:
        _emit({"ok": False, "error": str(e)}, args.text, [f"Error: {e}"])
        code = 1
    except (aiohttp.ClientError, asyncio.TimeoutError) as e:
        _emit({"ok": False, "error": f"Network error: {e}"}, args.text, [f"Network error: {e}"])
        code = 1
    sys.exit(code)


if __name__ == "__main__":
    main()
