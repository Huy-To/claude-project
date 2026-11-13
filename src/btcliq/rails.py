"""Safety rails for live orders: the limits file, the checks, the halt switch and the journal.

Every order, from the browser or the command line, is checked here twice: when it's
previewed and again right before it's sent (prices move in between). Any failed check
blocks the order. See SAFETY_RAILS.md for what each setting does, with examples.

- Limits live in trading.toml (next to .env). No file, or a file with a mistake in it,
  means no trading at all: an order is never sent under limits you didn't write.
- The file is re-read whenever it changes, so edits apply without a restart.
- Rules listed in [option] ask don't block option orders: they need your approval instead
  (a "Place anyway" tick in the browser, or --accept on the command line).
- Closing orders (ones that only shrink a position) always get through the "stop" rails:
  the halt switch, the daily caps, the leverage and position caps. Size and price checks
  still apply to them.
"""

from __future__ import annotations

import json
import os
import time
import tomllib
from datetime import datetime, timezone
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG = PROJECT / "trading.toml"
TYPES = ("OPTION", "SWAP", "FUTURES", "SPOT")
TYPE_SECTION = {"OPTION": "option", "SWAP": "swap", "FUTURES": "futures", "SPOT": "spot"}
OFF = "off"   # a limit set to "off" is not enforced

# Every key the file may contain, with its default. Defaults are deliberately small.
DEFAULTS: dict = {
    "enabled": False,
    "instruments": ["OPTION", "SWAP", "FUTURES", "SPOT"],
    "order": {
        "max_notional_usd": 5000,      # underlying value of one order
        "price_band_pct": 5,           # limit price within ±5% of the mark price
        "allow_market_orders": True,   # spot, perps and futures (OKX has no market orders for options)
        "max_open_orders": 10,         # resting orders on the account before another is refused
        "cooldown_seconds": 2,         # minimum gap between two new orders
        "preview_ttl_seconds": 60,     # how long a preview can be confirmed
    },
    "daily": {                         # reset at 00:00 UTC
        "max_orders": 30,
        "max_notional_usd": 25000,
        "max_loss_usd": 1000,          # realized P&L plus fees today, from OKX's fill history
    },
    "option": {
        "max_contracts": 10,           # 1 contract = 0.01 BTC
        "max_premium_usd": 1000,       # premium paid or received by one order
        "price_band_pct": 30,          # option quotes are wide, so the band is wider
        "allow_selling_to_open": False,
        "max_position_contracts": 30,  # per contract, after the order fills
        "ask": [],                     # rules that ask for approval on option orders instead of blocking
    },
    "swap": {
        "max_order_usd": OFF,          # tighter per-order cap for perps than [order], if wanted
        "max_leverage": 5,
        "max_position_usd": 20000,
    },
    "futures": {
        "max_order_usd": OFF,
        "max_leverage": 5,
        "max_position_usd": 20000,
    },
    "spot": {
        "max_order_usd": OFF,
    },
    "halt": {
        "allow_closing": True,         # while halted, orders that only close positions still go through
    },
    "account": {
        "derivatives_margin": "cross",  # cross or isolated
        "spot_mode": "auto",            # auto, cash or cross
    },
}
# Rules that [option] ask can turn from "block" into "ask me". The master switch, the BTC-only
# rule, the halt and the cooldown always block.
ASKABLE = ("price_band_pct", "daily.max_orders", "daily.max_notional_usd", "daily.max_loss_usd",
           "option.allow_selling_to_open", "option.max_contracts", "option.max_premium_usd",
           "option.max_position_contracts", "order.max_notional_usd", "order.max_open_orders")
CHOICES = {("account", "derivatives_margin"): ("cross", "isolated"),
           ("account", "spot_mode"): ("auto", "cash", "cross")}


class RailsConfigError(Exception):
    pass


def _check_value(where: str, default, value):
    if isinstance(default, bool):
        if not isinstance(value, bool):
            raise RailsConfigError(f"{where} must be true or false, not {value!r}")
        return value
    if isinstance(default, list):
        if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
            raise RailsConfigError(f"{where} must be a list like [\"OPTION\", \"SWAP\"]")
        bad = [v for v in value if v.upper() not in TYPES]
        if bad:
            raise RailsConfigError(f"{where}: unknown instrument type {bad[0]!r} (use {', '.join(TYPES)})")
        return [v.upper() for v in value]
    if isinstance(default, str) and default != OFF:
        if not isinstance(value, str):
            raise RailsConfigError(f"{where} must be text")
        return value
    # numbers (a few default to "off")
    if value == OFF:
        return OFF
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise RailsConfigError(f'{where} must be a number or "off", not {value!r}')
    if value < 0:
        raise RailsConfigError(f"{where} can't be negative")
    return value


def parse_config(raw: dict) -> dict:
    """Merge a parsed TOML dict over the defaults. Unknown keys are errors (a typo must
    never silently drop a limit)."""
    cfg = json.loads(json.dumps(DEFAULTS))
    for key, value in raw.items():
        if key not in DEFAULTS:
            raise RailsConfigError(f"unknown setting {key!r} (check the spelling against SAFETY_RAILS.md)")
        default = DEFAULTS[key]
        if isinstance(default, dict):
            if not isinstance(value, dict):
                raise RailsConfigError(f"[{key}] must be a section")
            for k, v in value.items():
                if k not in default:
                    raise RailsConfigError(f"unknown setting {k!r} in [{key}] (check the spelling against SAFETY_RAILS.md)")
                if (key, k) == ("option", "ask"):
                    if not isinstance(v, list) or not all(isinstance(x, str) for x in v):
                        raise RailsConfigError('option.ask must be a list like ["price_band_pct", "daily.max_orders"]')
                    bad = [x for x in v if x not in ASKABLE]
                    if bad:
                        raise RailsConfigError(f"option.ask: {bad[0]!r} can't ask for approval (choose from {', '.join(ASKABLE)})")
                    cfg[key][k] = list(dict.fromkeys(v))
                    continue
                v = _check_value(f"{key}.{k}", default[k], v)
                if (key, k) in CHOICES and v not in CHOICES[(key, k)]:
                    raise RailsConfigError(f"{key}.{k} must be one of {', '.join(CHOICES[(key, k)])}")
                cfg[key][k] = v
        else:
            cfg[key] = _check_value(key, default, value)
    if cfg["order"]["preview_ttl_seconds"] in (OFF, 0):
        raise RailsConfigError("order.preview_ttl_seconds must be a positive number of seconds")
    return cfg


class Rails:
    """The current limits, re-read from disk whenever the file changes."""

    def __init__(self, path: Path | str | None = None):
        self.path = Path(path or os.environ.get("BTCLIQ_TRADING_CONFIG") or DEFAULT_CONFIG)
        self._mtime: float | None = None
        self.cfg: dict = json.loads(json.dumps(DEFAULTS))
        self.error: str | None = None
        self.reload()

    def reload(self) -> None:
        try:
            mtime = self.path.stat().st_mtime
        except FileNotFoundError:
            self._mtime, self.error = None, (f"No limits file: copy trading.example.toml to {self.path.name} "
                                             "and set your limits (see SAFETY_RAILS.md). Trading is off until then.")
            self.cfg = json.loads(json.dumps(DEFAULTS))
            return
        if mtime == self._mtime:
            return
        self._mtime = mtime
        try:
            self.cfg = parse_config(tomllib.loads(self.path.read_text()))
            self.error = None
        except tomllib.TOMLDecodeError as e:
            self.error = f"{self.path.name} isn't valid TOML: {e}. Trading is off until it's fixed."
        except RailsConfigError as e:
            self.error = f"{self.path.name}: {e}. Trading is off until it's fixed."

    @property
    def active(self) -> bool:
        self.reload()
        return self.error is None and self.cfg["enabled"]

    def section(self, inst_type: str) -> dict:
        return self.cfg[TYPE_SECTION[inst_type]]

    def summary(self) -> dict:
        self.reload()
        return {"file": str(self.path), "error": self.error, "enabled": self.cfg["enabled"], "limits": self.cfg}


# ---------- halt switch ----------

class Halt:
    """A file in the data folder, so the browser and the command line share one switch."""

    def __init__(self, data_dir: Path):
        self.path = Path(data_dir) / "TRADING_HALTED"

    def state(self) -> dict | None:
        try:
            return json.loads(self.path.read_text())
        except FileNotFoundError:
            return None
        except Exception:
            return {"reason": "halt file present", "at": None}

    def set(self, reason: str, source: str) -> dict:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        st = {"reason": reason or "Kill switch", "source": source, "at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
        self.path.write_text(json.dumps(st))
        return st

    def clear(self) -> None:
        self.path.unlink(missing_ok=True)


# ---------- journal ----------

def utc_midnight_ms(now: float | None = None) -> int:
    d = datetime.fromtimestamp(now or time.time(), timezone.utc)
    return int(datetime(d.year, d.month, d.day, tzinfo=timezone.utc).timestamp() * 1000)


class Journal:
    """Append-only log of every order attempt, cancel and halt (data/trade_journal.jsonl).
    The daily caps count orders placed through btcliq from here."""

    def __init__(self, data_dir: Path):
        self.path = Path(data_dir) / "trade_journal.jsonl"

    def add(self, entry: dict) -> dict:
        entry = {"ts": int(time.time() * 1000), **entry}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a") as f:
            f.write(json.dumps(entry) + "\n")
        return entry

    def since(self, ms: int) -> list[dict]:
        try:
            lines = self.path.read_text().splitlines()
        except FileNotFoundError:
            return []
        out = []
        for line in reversed(lines):   # newest last in the file; stop at the first older entry
            try:
                e = json.loads(line)
            except ValueError:
                continue
            if e.get("ts", 0) < ms:
                break
            out.append(e)
        return out[::-1]

    def today(self) -> dict:
        placed = [e for e in self.since(utc_midnight_ms()) if e.get("kind") == "order" and e.get("status") == "placed"]
        opening = [e for e in placed if not e.get("closing")]
        return {"orders": len(placed), "opening_orders": len(opening),
                "notional_usd": sum(e.get("notional_usd") or 0 for e in opening),
                "last_order_ms": max((e["ts"] for e in placed), default=None)}


# ---------- the checks ----------

def _lim(v):
    return None if v == OFF else v


def _fmt_usd(v: float) -> str:
    return f"${v:,.0f}" if abs(v) >= 100 else f"${v:,.2f}"


def evaluate(cfg: dict, o: dict, usage: dict) -> list[dict]:
    """Run every rail against one order. Returns [{rule, ok, msg}], one per rule that applies.

    `o` (built by the trader): inst_type, inst_id, side, ord_type, sz, notional_usd,
    premium_usd, closing, ref_px, px, band_pct (distance from ref in %), post_position_usd,
    post_position_contracts, opens_short_option, leverage, open_orders.
    `usage`: halted (dict|None), orders, opening_orders, notional_usd, last_order_ms, realized_usd.
    """
    out: list[dict] = []

    def rule(name: str, ok: bool, msg: str):
        out.append({"rule": name, "ok": bool(ok), "msg": msg})

    t, closing = o["inst_type"], o.get("closing", False)
    sec = cfg[TYPE_SECTION[t]]
    order, daily = cfg["order"], cfg["daily"]

    rule("enabled", cfg["enabled"], "Trading is enabled in trading.toml" if cfg["enabled"]
         else "Trading is off: set enabled = true in trading.toml")
    rule("instrument", t in cfg["instruments"] and o["inst_id"].startswith("BTC-"),
         f"{t} on BTC is allowed" if t in cfg["instruments"] else f"{t} isn't in instruments = {cfg['instruments']}")

    halted = usage.get("halted")
    if halted:
        let_through = closing and cfg["halt"]["allow_closing"]
        rule("halt", let_through, "Halted, but closing orders are allowed" if let_through
             else f"Trading is halted ({halted.get('reason', 'kill switch')}). Resume it first.")

    # --- size: applies to every order ---
    cap = _lim(order["max_notional_usd"])
    if cap is not None:
        rule("order.max_notional_usd", o["notional_usd"] <= cap,
             f"Order value {_fmt_usd(o['notional_usd'])} vs limit {_fmt_usd(cap)}")
    type_cap = _lim(sec.get("max_order_usd", OFF))
    if type_cap is not None:
        rule(f"{TYPE_SECTION[t]}.max_order_usd", o["notional_usd"] <= type_cap,
             f"Order value {_fmt_usd(o['notional_usd'])} vs {TYPE_SECTION[t]} limit {_fmt_usd(type_cap)}")
    if t == "OPTION":
        mc = _lim(sec["max_contracts"])
        if mc is not None:
            rule("option.max_contracts", o["sz"] <= mc, f"{o['sz']:g} contracts vs limit {mc:g}")
        mp = _lim(sec["max_premium_usd"])
        if mp is not None and o.get("premium_usd") is not None:
            rule("option.max_premium_usd", o["premium_usd"] <= mp,
                 f"Premium {_fmt_usd(o['premium_usd'])} vs limit {_fmt_usd(mp)}")

    # --- price: applies to every order ---
    if o["ord_type"] == "market":
        ok = t != "OPTION" and order["allow_market_orders"]
        rule("order.allow_market_orders", ok, "Market order allowed" if ok else
             ("OKX has no market orders for options: use a limit price" if t == "OPTION"
              else "Market orders are off (order.allow_market_orders = false)"))
    else:
        band = _lim(sec.get("price_band_pct", order["price_band_pct"]))
        if band is not None and o.get("band_pct") is not None:
            b = o["band_pct"]
            passive = (o["side"] == "buy" and b < 0) or (o["side"] == "sell" and b > 0)
            if abs(b) <= band:
                msg = f"Limit price is {b:+.1f}% from mark (band ±{band:g}%)"
            elif passive:
                msg = (f"{o['side'].capitalize()} price is {b:+.1f}% from mark (band ±{band:g}%): it will rest in the "
                       "book until the market gets there")
            else:
                msg = (f"{o['side'].capitalize()} price is {b:+.1f}% from mark (band ±{band:g}%): you'd "
                       + ("pay well above" if o["side"] == "buy" else "sell well below") + " what it's worth. A typo?")
            rule("price_band_pct", abs(b) <= band, msg)

    # --- account-wide ---
    mo = _lim(order["max_open_orders"])
    if mo is not None and o.get("open_orders") is not None and not closing:
        rule("order.max_open_orders", o["open_orders"] < mo, f"{o['open_orders']} open orders (limit {mo:g})")
    cd = _lim(order["cooldown_seconds"])
    if cd and usage.get("last_order_ms"):
        gap = (time.time() * 1000 - usage["last_order_ms"]) / 1000
        rule("order.cooldown_seconds", gap >= cd, f"{gap:.0f}s since the last order (minimum {cd:g}s)")

    # --- stop rails: skipped for closing orders ---
    if not closing:
        m = _lim(daily["max_orders"])
        if m is not None:
            rule("daily.max_orders", usage.get("opening_orders", 0) < m,
                 f"{usage.get('opening_orders', 0)} opening orders today (limit {m:g})")
        m = _lim(daily["max_notional_usd"])
        if m is not None:
            after = usage.get("notional_usd", 0) + o["notional_usd"]
            rule("daily.max_notional_usd", after <= m, f"Today's opening volume would be {_fmt_usd(after)} (limit {_fmt_usd(m)})")
        m = _lim(daily["max_loss_usd"])
        if m is not None and usage.get("realized_usd") is not None:
            r = usage["realized_usd"]
            rule("daily.max_loss_usd", r > -m, f"Realized today {_fmt_usd(r)} (stop at -{_fmt_usd(m)})")
        if t == "OPTION":
            if o.get("opens_short_option"):
                ok = sec["allow_selling_to_open"]
                rule("option.allow_selling_to_open", ok, "Selling to open is allowed" if ok else
                     "This sells options you don't own (a short position). Turn on option.allow_selling_to_open to allow it.")
            mp = _lim(sec["max_position_contracts"])
            if mp is not None and o.get("post_position_contracts") is not None:
                rule("option.max_position_contracts", o["post_position_contracts"] <= mp,
                     f"Position after fill {o['post_position_contracts']:g} contracts (limit {mp:g})")
        if t in ("SWAP", "FUTURES"):
            ml = _lim(sec["max_leverage"])
            if ml is not None and o.get("leverage") is not None:
                rule(f"{TYPE_SECTION[t]}.max_leverage", o["leverage"] <= ml,
                     f"Leverage {o['leverage']:g}x (limit {ml:g}x)" + ("" if o["leverage"] <= ml else
                     f". Lower it: btcliq-trade leverage {o['inst_id']} {ml:g}"))
            mp = _lim(sec["max_position_usd"])
            if mp is not None and o.get("post_position_usd") is not None:
                rule(f"{TYPE_SECTION[t]}.max_position_usd", o["post_position_usd"] <= mp,
                     f"Position after fill {_fmt_usd(o['post_position_usd'])} (limit {_fmt_usd(mp)})")
    if t == "OPTION":
        ask = set(cfg["option"].get("ask") or [])
        for c in out:
            if not c["ok"] and c["rule"] in ask:
                c["ask"] = True
                if c["rule"] == "option.allow_selling_to_open":
                    c["msg"] = ("This sells options you don't own, opening a short position: "
                                "you could lose much more than the premium (see the worst case)")
    return out


def blocked(checks: list[dict]) -> bool:
    """Failed rules that can't be approved."""
    return any(not c["ok"] and not c.get("ask") for c in checks)


def asks(checks: list[dict]) -> list[str]:
    """Failed rules waiting for your approval."""
    return [c["rule"] for c in checks if not c["ok"] and c.get("ask")]
