"""Prepared actions: Claude lines something up, you send it.

Each lookup here checks the idea the same way the order ticket does (a dry run against your
safety rails), then shows you a card with one button. The button fills the ticket, opens the
Close panel, or asks you to confirm a cancel or a change. Nothing in this file talks to OKX's
order endpoints: sending still happens only through the page, after your click, with the
rails checked again.

Only the in-app chat has these lookups. Other Claude Code sessions do not.
"""

from __future__ import annotations

import time
from decimal import ROUND_FLOOR, Decimal

from ..trader import _plain, _snap
from . import calc
from .lookups import Ctx, R, _okx, _trader
from .registry import LookupFailed, Param

STEPS = (25, 50, 75, 100)          # what the Close panel offers
ORDER_TYPES = ("limit", "market", "post_only", "ioc", "fok")

LEG = {"type": "object",
       "properties": {"instrument": {"type": "string", "description": "e.g. BTC-USD-261127-75000-C or BTC-USDT-SWAP"},
                      "side": {"type": "string", "enum": ["buy", "sell"]},
                      "size": {"type": "number", "description": "Contracts (options, perps, futures) or BTC (spot)"},
                      "price": {"type": "number", "description": "Limit price in the instrument's own unit; leave out for a market order"},
                      "order_type": {"type": "string", "enum": list(ORDER_TYPES)},
                      "reduce": {"type": "boolean", "description": "True if this only shrinks a position"}},
       "required": ["instrument", "side", "size"], "additionalProperties": False}


def _failed(p: dict) -> str:
    return "; ".join(c["msg"] for c in p["checks"] if not c["ok"] and not c.get("ask")) or "a safety rail"


def _asks(p: dict) -> list[str]:
    return [c["msg"] for c in p["checks"] if not c["ok"] and c.get("ask")]


def _emit(ctx: Ctx, kind: str, **fields) -> None:
    why = " ".join(str(fields.pop("why", "") or "").split())[:240]
    # The event's own keys go last, so no field of the action can overwrite them.
    ctx.chat.emit({**fields, "type": "proposal", "kind": kind, "at": int(time.time() * 1000), "why": why})


async def _leg(tr, leg) -> dict:
    if not isinstance(leg, dict) or not leg.get("instrument"):
        raise LookupFailed("each leg needs instrument, side and size")
    side = str(leg.get("side", "")).lower()
    if side not in ("buy", "sell"):
        raise LookupFailed("a leg's side must be buy or sell")
    otype = leg.get("order_type")
    if otype is not None and otype not in ORDER_TYPES:
        raise LookupFailed(f"order_type must be one of: {', '.join(ORDER_TYPES)}")
    try:
        size = float(leg.get("size"))
        price = None if leg.get("price") in (None, "") else float(leg["price"])
    except (TypeError, ValueError):
        raise LookupFailed("a leg's size and price must be numbers") from None
    req = {"instId": str(leg["instrument"]), "side": side, "sz": f"{size:.10g}",
           "px": None if price is None else f"{price:.10g}", "ordType": otype, "reduce": bool(leg.get("reduce"))}
    p = await _okx(tr.check(req))
    o, n, i = p["okx_order"], p["numbers"], p["instrument"]
    if p["blocked"]:
        raise LookupFailed(f"{o['instId']}: blocked by the user's safety rails ({_failed(p)}). Nothing was shown to "
                           "the user. Resize or reprice it, or tell them why it cannot be done.")
    return {"instId": o["instId"], "side": o["side"], "sz": o["sz"], "px": o.get("px"), "type": o["ordType"],
            "reduce": req["reduce"], "unit": i["size_unit"], "price_ccy": i["price_ccy"], "about": i["about"],
            "value_usd": n["notional_usd"], "premium_usd": n["premium_usd"], "fee_usd": n["fee_usd"],
            "margin_usd": n["margin_usd"], "worst_case_usd": n["worst_case_usd"], "worst_case": n["worst_case"],
            "breakeven": n["breakeven"], "asks": _asks(p), "warnings": p["warnings"],
            "bid": p["market"].get("bid"), "ask": p["market"].get("ask"), "mark": p["market"].get("mark")}


@R.lookup("prepare_order", title="Order prepared", group="prepare", scope="chat", card=False, description="""
    Prepare one order, or a structure of up to four legs, for the user to review. Each leg is
    dry-run against their safety rails exactly as the order ticket does it, then shown to them as
    a card with a "Review in ticket" button. It sends nothing: the user fills the ticket from the
    card and confirms each leg themselves. Use it when you give an idea, or when the user asks you
    to set up an order. It returns each leg's cost, worst case and rails verdict, so you do not need
    order_check first for the same order. A leg blocked by a rail is not shown: resize it or say why.""",
          params={"legs": Param("array", "One to four legs", items=LEG),
                  "why": Param("string", "One line the card shows: what this order is for")},
          required=("legs",))
async def prepare_order(ctx: Ctx, legs: list, why: str | None):
    tr = _trader(ctx)
    if not legs or len(legs) > 4:
        raise LookupFailed("give between 1 and 4 legs")
    built = [await _leg(tr, l) for l in legs]
    _emit(ctx, "order", legs=built, why=why)
    lines = []
    for b in built:
        cost = b["premium_usd"] if b["premium_usd"] is not None else b["value_usd"]
        lines.append(f"- {b['side']} {b['sz']} {b['instId']} at {b['px'] or 'market'} ({b['type']}): "
                     f"{'premium' if b['premium_usd'] is not None else 'value'} ${cost:,.2f}"
                     + (f", worst case ${b['worst_case_usd']:,.2f}" if b["worst_case_usd"] is not None else "")
                     + (f", breakeven ${b['breakeven']:,.0f}" if b["breakeven"] else "")
                     + (". Needs the user's OK for: " + "; ".join(b["asks"]) if b["asks"] else ". Passes every rail")
                     + (". Note: " + "; ".join(b["warnings"]) if b["warnings"] else ""))
    return ("Shown to the user as a card with a Review in ticket button per leg. Nothing has been sent: they "
            "fill the ticket from the card and confirm each leg themselves.\n" + "\n".join(lines))


@R.lookup("prepare_close", title="Close prepared", group="prepare", scope="chat", card=False, description="""
    Prepare closing an open position, in whole or in part, for the user to review. It shows a card
    with an "Open Close panel" button; the panel is the app's own, with the size already chosen.
    It sends nothing. A position held in both cross and isolated margin has two parts: name the
    part with margin_mode. Options close with a limit order at the mid price; perps and futures
    can close at market.""",
          params={"instrument": Param("string", "The position's instrument, e.g. BTC-USD-261114-66000-P"),
                  "percent": Param("integer", "How much of it to close: 25, 50, 75 or 100 (default 100)",
                                   default=100, minimum=1, maximum=100),
                  "margin_mode": Param("string", "Which part, when it is held in both modes", enum=("cross", "isolated")),
                  "order": Param("string", "limit (at the mid) or market; options are always limit", enum=("limit", "market")),
                  "why": Param("string", "One line the card shows: why close it")},
          required=("instrument",))
async def prepare_close(ctx: Ctx, instrument: str, percent: int, margin_mode: str | None, order: str | None, why: str | None):
    tr = _trader(ctx)
    inst = instrument.strip().upper()
    a = await _okx(tr.account())
    held = [p for p in a.get("positions", []) if p.get("instId") == inst and p.get("pos")]
    if not held:
        have = ", ".join(sorted({p["instId"] for p in a.get("positions", [])})) or "none"
        raise LookupFailed(f"there is no open position in {inst}; open positions: {have}")
    modes = sorted({p.get("mgnMode") for p in held})
    if margin_mode:
        held = [p for p in held if p.get("mgnMode") == margin_mode]
        if not held:
            raise LookupFailed(f"{inst} is not held in {margin_mode} margin; it is held in: {', '.join(modes)}")
    elif len(held) > 1:
        raise LookupFailed(f"{inst} is held in {' and '.join(modes)} margin, which are separate positions. "
                           "Prepare one part at a time with margin_mode.")
    p = held[0]
    pct = min(STEPS, key=lambda s: abs(s - percent))
    total, long = abs(p["pos"]), calc.signed_size(p) > 0
    kind = "limit" if p.get("type") == "OPTION" else (order or "market")
    meta = await _okx(tr.instrument(inst))
    q = await _okx(tr.quote(inst))
    lot = Decimal(meta["lotSz"])
    sz = (Decimal(str(total)) * pct / 100 / lot).to_integral_value(rounding=ROUND_FLOOR) * lot
    if sz <= 0:
        sz = min(lot, Decimal(str(total)))
    body = {"instId": inst, "side": "sell" if long else "buy", "sz": _plain(sz), "reduce": True, "ordType": kind}
    if p.get("type") != "SPOT" and p.get("mgnMode"):
        body["mgnMode"] = p["mgnMode"]
    if kind == "limit":
        mid = (q["bid"] + q["ask"]) / 2 if q.get("bid") is not None and q.get("ask") is not None else q.get("mark")
        if mid is None:
            raise LookupFailed(f"there is no price for {inst} right now, so a limit close can't be priced")
        body["px"] = _plain(_snap(Decimal(str(mid)), meta["tickSz"]))
    chk = await _okx(tr.check(body))
    if chk["blocked"]:
        raise LookupFailed(f"closing {inst} is blocked ({_failed(chk)}). Nothing was shown to the user.")
    frac = float(sz) / total if total else 0.0
    est = None if p.get("upl_usd") is None else p["upl_usd"] * frac
    _emit(ctx, "close", instId=inst, mgnMode=p.get("mgnMode"), parts=len(modes), percent=pct, order=kind, side=body["side"],
          sz=body["sz"], total=total, unit=p.get("unit"), px=body.get("px"), price_ccy=p.get("price_ccy"),
          est_pnl_usd=est, asks=_asks(chk), why=why)
    return (f"Shown to the user as a card with an Open Close panel button: close {pct}% of {inst}"
            + (f" ({p.get('mgnMode')} part)" if len(modes) > 1 else "")
            + f", {body['side']} {body['sz']} at {body.get('px') or 'market'}"
            + (f", realizing about ${est:,.2f}" if est is not None else "")
            + ". Nothing has been sent: they review it in the panel and confirm."
            + (" It needs their OK for: " + "; ".join(_asks(chk)) if _asks(chk) else ""))


async def _open_order(tr, order_id: str) -> dict:
    a = await _okx(tr.account())
    orders = a.get("orders", [])
    o = next((o for o in orders if str(o.get("ordId")) == str(order_id).strip()), None)
    if not o:
        have = "; ".join(f"{x['ordId']} ({x['side']} {x['sz']:g} {x['instId']} at {x['px']})" for x in orders) or "none"
        raise LookupFailed(f"there is no open order with id {order_id}; open orders: {have}")
    return o


def _order_fields(o: dict) -> dict:
    return {"instId": o["instId"], "ordId": str(o["ordId"]), "side": o["side"], "sz": o["sz"], "px": o["px"],
            "ordType": o["ordType"], "unit": o.get("unit"), "price_ccy": o.get("price_ccy"), "reduce": o.get("reduceOnly")}


@R.lookup("prepare_cancel", title="Cancel prepared", group="prepare", scope="chat", card=False, description="""
    Prepare cancelling one open order, for the user to confirm. It shows a card with a "Cancel
    order" button and cancels nothing itself. Take the order id from the account lookup.""",
          params={"order_id": Param("string", "The open order's id (ordId), from the account lookup"),
                  "why": Param("string", "One line the card shows: why cancel it")},
          required=("order_id",))
async def prepare_cancel(ctx: Ctx, order_id: str, why: str | None):
    o = await _open_order(_trader(ctx), order_id)
    _emit(ctx, "cancel", why=why, **_order_fields(o))
    return (f"Shown to the user as a card with a Cancel order button for {o['side']} {o['sz']:g} {o['instId']} "
            f"at {o['px']}. Nothing has been cancelled: they press the button.")


@R.lookup("prepare_amend", title="Change prepared", group="prepare", scope="chat", card=False, description="""
    Prepare changing the price or the size of one open order, for the user to confirm. It shows a
    card with a "Review change" button and changes nothing itself. Give exactly one of new_price
    or new_size; prepare two changes for both. Take the order id from the account lookup.""",
          params={"order_id": Param("string", "The open order's id (ordId), from the account lookup"),
                  "new_price": Param("number", "The new limit price, in the instrument's own unit"),
                  "new_size": Param("number", "The new size"),
                  "why": Param("string", "One line the card shows: why change it")},
          required=("order_id",))
async def prepare_amend(ctx: Ctx, order_id: str, new_price: float | None, new_size: float | None, why: str | None):
    if (new_price is None) == (new_size is None):
        raise LookupFailed("give exactly one of new_price or new_size")
    tr = _trader(ctx)
    o = await _open_order(tr, order_id)
    meta = await _okx(tr.instrument(o["instId"]))
    if new_price is not None:
        if new_price <= 0 or o["px"] is None:
            raise LookupFailed("this order has no limit price to change" if o["px"] is None else "new_price must be above zero")
        field, to = "px", _plain(_snap(Decimal(f"{new_price:.10g}"), meta["tickSz"]))
        if float(to) == float(o["px"]):
            raise LookupFailed("that is already the order's price")
    else:
        if new_size <= 0:
            raise LookupFailed("new_size must be above zero")
        field, to = "sz", _plain(_snap(Decimal(f"{new_size:.10g}"), meta["lotSz"]))
        if float(to) < float(meta["minSz"]):
            raise LookupFailed(f"the minimum size for {o['instId']} is {meta['minSz']}")
        if float(to) == float(o["sz"]):
            raise LookupFailed("that is already the order's size")
    _emit(ctx, "amend", why=why, field=field, to=to, **_order_fields(o))
    what = "price" if field == "px" else "size"
    return (f"Shown to the user as a card with a Review change button: {o['side']} {o['instId']}, {what} "
            f"{o[field]} to {to}. Nothing has been changed: they confirm it, and their rails are checked then.")
