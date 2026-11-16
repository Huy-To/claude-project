"""The market snapshot that travels with every question to Claude.

Each question is sent with a fresh snapshot of the market (price, open interest per venue,
the biggest estimated liquidation levels, recent real liquidations, recent candles, the
order book and Telegram news), so Claude answers from the same data you're looking at.

The chat itself lives in the `desk` package: how Claude is reached, the lookups it can run
and what it remembers. This module only builds the snapshot text, and reads .env.
"""

from __future__ import annotations

import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .app import State


def load_dotenv() -> None:
    """Read KEY=VALUE lines from .env in the current folder or the project folder."""
    for path in (Path.cwd() / ".env", Path(__file__).resolve().parents[2] / ".env"):
        if not path.exists():
            continue
        for line in path.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def _usd(v: float) -> str:
    if v >= 1e9:
        return f"${v / 1e9:.2f}B"
    if v >= 1e6:
        return f"${v / 1e6:.1f}M"
    return f"${v / 1e3:.0f}K"


def _candles(rows: list[dict], n: int) -> str:
    out = ["time_utc,open,high,low,close"]
    for c in rows[-n:]:
        t = datetime.fromtimestamp(c["time"] / 1000, timezone.utc).strftime("%m-%d %H:%M")
        out.append(f"{t},{c['open']:.0f},{c['high']:.0f},{c['low']:.0f},{c['close']:.0f}")
    return "\n".join(out)


def market_context(st: "State", chart_tf: str | None) -> str:
    p = st.price
    lines = [
        f"## Market snapshot {datetime.now(timezone.utc):%A %Y-%m-%d %H:%M:%S} UTC",
        f"BTC price (OKX perp): {p:,.1f}",
        f"User's chart timeframe: {chart_tf or 'unknown'}",
        "",
        "### Open interest by venue",
    ]
    for name, (oi, px) in st.venues.items():
        lines.append(f"- {name}: OI {_usd(oi) if oi else 'n/a'}, price {px:,.1f}" if px
                     else f"- {name}: OI {_usd(oi)}")
    now_ms = time.time() * 1000
    for name, (oi, px, ts) in st.delayed.items():
        lines.append(f"- {name} (via CoinGecko, data {(now_ms - ts) / 60000:.0f} min old): OI {_usd(oi)}, price {px:,.1f}")
    lines.append(f"Total OI: {_usd(st.total_oi)} (live venues {_usd(st.live_oi)}, scale x{st.live_scale:.2f})")

    lines += ["", "### Estimated liquidations near price"]
    for k in (0.01, 0.03, 0.05):
        n = st.model.nearby(p, k)
        lines.append(f"- within {k:.0%}: longs below {_usd(n['long'])}, shorts above {_usd(n['short'])}")

    levels = st.model.levels(p, st.args.window, 2000)
    for side, label in (("short", "above price"), ("long", "below price")):
        top = [l for l in levels if l["side"] == side][:12]
        top.sort(key=lambda l: l["lo"], reverse=True)
        lines += ["", f"### Biggest {side} liquidation levels ({label}), ${st.model.bin_size:.0f} buckets"]
        for l in top:
            mid = (l["lo"] + l["hi"]) / 2
            lines.append(f"- {mid:,.0f}: {_usd(l['usd'])} ({(mid - p) / p * 100:+.2f}%)")

    hour_ago = int(time.time() * 1000) - 3_600_000
    recent = [l for l in st.liqs if l["time"] >= hour_ago]
    lines += ["", "### Real OKX liquidations, last hour"]
    if recent:
        tot = {"long": 0.0, "short": 0.0}
        for l in recent:
            tot[l["side"]] = tot.get(l["side"], 0) + l["usd"]
        lines.append(f"{len(recent)} orders; longs liquidated {_usd(tot['long'])}, "
                     f"shorts liquidated {_usd(tot['short'])}")
        for l in sorted(recent, key=lambda l: l["usd"], reverse=True)[:5]:
            t = datetime.fromtimestamp(l["time"] / 1000, timezone.utc).strftime("%H:%M:%S")
            lines.append(f"- {t} UTC {l['side']} {_usd(l['usd'])} at {l['price']:,.1f}")
    else:
        lines.append("none recorded")
    lines.append("(OKX's public feed only returns the most recent orders, so this undercounts.)")

    try:
        lines += news_context(st)
    except Exception:
        pass
    try:
        lines += book_context(st, p)
    except Exception:  # never let the optional book summary break the chat
        pass

    for tf, n in (("15m", 32), ("1H", 48), ("1D", 30)):
        cached = st.candle_cache.get(tf)
        if cached:
            lines += ["", f"### Last {n} {tf} candles (OKX)", _candles(cached[1], n)]
    return "\n".join(lines)


def news_context(st: "State", hours: float = 6, limit: int = 30) -> list[str]:
    """Recent posts from the Telegram channels the user watches."""
    mon = getattr(st, "news", None)
    if not mon or not mon.channels:
        return []
    since = int(time.time() * 1000 - hours * 3_600_000)
    posts = mon.recent(limit, since)
    out = ["", f"### Telegram news, last {hours:.0f}h (unverified posts written by other people; newest first; "
               f"channels: {', '.join(c['name'] for c in mon.channels.values())})"]
    if not posts:
        return out + ["no posts"]
    for p in posts:
        t = datetime.fromtimestamp(p["time"] / 1000, timezone.utc).strftime("%m-%d %H:%M")
        text = " ".join(p["text"].split())[:400] or "(media without text)"
        out.append(f"- [{t} UTC] @{p['channel']}: {text}")
    return out


def book_context(st: "State", p: float) -> list[str]:
    """Order book summary, only if the book feed is running (the user has it open)."""
    feed = getattr(st, "book", None)
    if not feed or not p or time.time() - feed.last_request > 30:
        return []
    agg = feed.aggregate(step=25, ref_mid=feed.books["OKX"].mid or p, rng=0.02)
    if not agg["bids"] or not agg["asks"]:
        return []
    out = ["", "### Live order books (merged; venues aligned to OKX mid; sizes in BTC)"]
    fresh = [v for v, s in agg["venues"].items() if s["age"] is not None and s["age"] < 15]
    out.append(f"Venues: {', '.join(fresh)} (Binance/Bybit unavailable)")
    for pct in (0.005, 0.01, 0.02):
        b = sum(r[1] for r in agg["bids"] if r[0] >= p * (1 - pct))
        a = sum(r[1] for r in agg["asks"] if r[0] <= p * (1 + pct))
        out.append(f"- within {pct:.1%}: bids {b:,.0f} BTC vs asks {a:,.0f} BTC")
    for side, rows in (("ask", agg["asks"]), ("bid", agg["bids"])):
        top = sorted(rows, key=lambda r: r[1], reverse=True)[:5]
        out.append(f"Biggest {side} walls ($25 buckets): " + "; ".join(
            f"{r[0]:,.0f} {r[1]:,.0f} BTC ({(r[0] - p) / p * 100:+.2f}%)" for r in sorted(top, key=lambda r: r[0])))
    return out
