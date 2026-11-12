"""Chat with Claude about what's on the chart.

Each question is sent with a fresh snapshot of the market (price, open interest per
venue, the biggest estimated liquidation levels, recent real liquidations, recent
candles), so Claude answers from the same data you're looking at.

Two ways to reach Claude:
- "claude-code": runs the Claude Code CLI installed on this PC in headless mode
  (`claude -p`), so questions use your existing Claude login/subscription. No API key.
- "api": calls the Anthropic API directly with ANTHROPIC_API_KEY (from the environment or
  a .env file in the project folder). Billed per token.
"auto" uses Claude Code if it's installed, otherwise the API if a key is set.
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, AsyncIterator

if TYPE_CHECKING:
    from .app import State

DEFAULT_MODEL = "claude-sonnet-5-5"
MAX_HISTORY = 20  # messages kept per conversation

SYSTEM = """You are a crypto market analyst built into a live BTC liquidation-level chart.
The user is looking at the chart while they talk to you. Each message comes with a fresh
market snapshot below. Answer from that data; say so when the data can't answer a question.

How the liquidation levels are made (so you can explain their limits):
- They are ESTIMATES, not real positions. When open interest rises, the model assumes new
  positions opened at that bar's price, half long and half short, spread over a leverage mix
  (5x 10%, 10x 25%, 20x 25%, 25x 15%, 50x 15%, 100x 10%) with 0.5% maintenance margin.
- When open interest falls, all levels shrink proportionally. When price trades through a
  level, it is removed (those positions are assumed liquidated).
- History comes from OKX, scaled up to the whole market (total OI / OKX OI). Live updates
  come from OKX, Hyperliquid, Deribit and Kraken every 30s, scaled up the same way. Binance
  and Bybit block the user's region, so their open interest comes from CoinGecko, minutes to
  ~1.5 hours late; it's used only for the market's total size, not for timing. Levels
  therefore assume Binance/Bybit traders position like traders on the other venues.
- If an order book section is present: those are resting limit orders (real, but they can be
  pulled at any moment, and Binance/Bybit books are missing). Liquidation levels are where
  forced market orders would hit; resting walls are where they'd be absorbed.
- The options section is OKX's live BTC option chain: per-expiry ATM IV, open interest,
  max pain and biggest-OI strikes; a near-the-money chain for the 4 nearest expiries; and
  full details (price, IV, greeks, OI, breakeven) for any contract the user named or has
  open in the options panel. Use these numbers directly when asked about options. If the
  user asks about a contract that isn't detailed, use the chain rows, or tell them to name
  it exactly (e.g. BTC-USD-261127-75000-C). Large OI strikes can pin price near expiry;
  ATM IV implies expected movement (daily ≈ IV / sqrt(365)).
- The power-law section holds the user's standing rules for BTC options. Follow them: apply the
  horizon gate, use NvPL as the baseline (never size on AR), anchor strikes to the floor / 0.55x /
  0.80x / NvPL at expiry, test in three worlds (attractive under nvpl AND survivable under
  risk_neutral; the risk-neutral fair value matches OKX's mark), defined risk only, and check the
  invalidation status first. Use the power law only as a long-horizon bias, strike anchor and regime
  filter, never for short-term timing; for timing use liquidations, order books, IV and news.
- The stablecoin section is global supply from DefiLlama (all chains). Rising supply (net minting)
  means new dollars entering crypto, i.e. potential buying power; falling supply (net redemptions)
  means capital leaving. The supply ratio (BTC market cap / stablecoin supply) is lower when
  stablecoins have more buying power relative to BTC. Treat these as slow, macro-liquidity signals,
  not short-term timing. Peg alerts use DefiLlama prices, which can be stale for illiquid coins.
- If a Telegram news section is present: these are headlines from channels the user chose.
  Treat them as unverified reports, note their time relative to price moves in the candles,
  and say when a headline could plausibly explain or move price.
- "Long" levels sit below price (longs get liquidated when price falls into them). "Short"
  levels sit above price.

Style: be direct and concrete, cite prices and dollar sizes from the snapshot, keep answers
short unless asked for depth. Use short paragraphs or bullets. You can discuss scenarios
(e.g. where liquidation cascades could accelerate a move) but don't present them as
certainties, and don't tell the user to place specific trades: this isn't financial advice,
and the user decides."""


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
    out = ["", f"### Telegram news, last {hours:.0f}h (newest first; channels: "
               f"{', '.join(c['name'] for c in mon.channels.values())})"]
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


class Chat:
    def __init__(self, model: str = DEFAULT_MODEL, backend: str = "auto"):
        load_dotenv()
        self.requested = backend
        self.model = model
        self.key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
        self.claude_bin = shutil.which("claude")
        if backend == "auto":
            backend = "claude-code" if self.claude_bin else "api" if self.key else "off"
        if backend == "api" and not self.key:
            backend = "off"
        if backend == "claude-code" and not self.claude_bin:
            backend = "off"
        self.backend = backend
        self._client = None
        if backend == "api":
            import anthropic
            self._client = anthropic.AsyncAnthropic(api_key=self.key)
        # Empty folder for the CLI to run in, so it doesn't pick up any project files.
        self._workdir = tempfile.mkdtemp(prefix="btcliq-claude-") if backend == "claude-code" else None

    @property
    def enabled(self) -> bool:
        return self.backend != "off"

    @property
    def label(self) -> str:
        return {"api": "Anthropic API", "claude-code": "Claude Code"}.get(self.backend, "")

    @staticmethod
    def _clean(messages: list[dict]) -> list[dict]:
        clean = [
            {"role": m["role"], "content": str(m["content"])[:8000]}
            for m in messages[-MAX_HISTORY:]
            if m.get("role") in ("user", "assistant") and str(m.get("content", "")).strip()
        ]
        while clean and clean[0]["role"] != "user":
            clean.pop(0)
        return clean

    async def stream(self, st: "State", messages: list[dict], chart_tf: str | None,
                     option: str | None = None) -> AsyncIterator[str]:
        clean = self._clean(messages)
        if not clean or clean[-1]["role"] != "user":
            yield "Nothing to answer."
            return
        system = SYSTEM + "\n\n" + market_context(st, chart_tf)
        if getattr(st, "stables", None):
            try:
                system += "\n" + "\n".join(st.stables.context())
            except Exception:
                pass
        if getattr(st, "powerlaw", None):
            try:
                system += "\n" + "\n".join(st.powerlaw.context())
            except Exception:
                pass
        if getattr(st, "options", None):
            try:  # fetched fresh for every question (cached a few seconds), panel open or not
                recent_user = " ".join(m["content"] for m in clean[-3:] if m["role"] == "user")
                pl = getattr(st, "powerlaw", None)
                extra = (lambda c, idx: pl.describe_eval(c["name"], pl.evaluate(c, idx))) if pl and pl.rows else None
                system += "\n" + "\n".join(await st.options.context(recent_user, option, extra))
            except Exception as e:
                system += f"\n\n### OKX options\nunavailable right now ({type(e).__name__})"
        if self.backend == "api":
            async for t in self._stream_api(system, clean):
                yield t
        elif self.backend == "claude-code":
            async for t in self._stream_cli(system, clean):
                yield t
        else:
            yield "Chat is off."

    # ---------- Anthropic API ----------
    async def _stream_api(self, system: str, clean: list[dict]) -> AsyncIterator[str]:
        import anthropic

        try:
            async with self._client.messages.stream(
                model=self.model, max_tokens=2000, system=system, messages=clean,
            ) as s:
                async for text in s.text_stream:
                    yield text
        except anthropic.AuthenticationError:
            yield "\n\n**Error:** your Anthropic API key was rejected. Check ANTHROPIC_API_KEY in .env."
        except anthropic.RateLimitError:
            yield "\n\n**Error:** rate limited by the Anthropic API. Try again in a moment."
        except anthropic.APIStatusError as e:
            yield f"\n\n**Error {e.status_code}:** {getattr(e, 'message', e)}"
        except anthropic.APIConnectionError:
            yield "\n\n**Error:** couldn't reach the Anthropic API. Check your connection."

    # ---------- Claude Code CLI ----------
    @staticmethod
    def _cli_prompt(clean: list[dict]) -> str:
        """`claude -p` takes one prompt, so earlier turns are passed as a transcript."""
        *earlier, last = clean
        if not earlier:
            return last["content"]
        convo = "\n\n".join(f"{'User' if m['role'] == 'user' else 'You'}: {m['content']}" for m in earlier)
        return (f"<conversation_so_far>\n{convo}\n</conversation_so_far>\n\n"
                f"Reply to the user's new message:\n{last['content']}")

    async def _stream_cli(self, system: str, clean: list[dict]) -> AsyncIterator[str]:
        cmd = [
            self.claude_bin, "-p",
            "--output-format", "stream-json", "--verbose", "--include-partial-messages",
            "--model", self.model,
            "--system-prompt", system,
            "--tools", "",                 # no file, shell or web access: chat only
            "--setting-sources", "",       # ignore your settings, hooks and CLAUDE.md files
            "--no-session-persistence",    # don't save these chats as Claude Code sessions
        ]
        env = dict(os.environ)
        env.pop("ANTHROPIC_API_KEY", None)  # make sure it uses your Claude login, not API billing
        proc = await asyncio.create_subprocess_exec(
            *cmd, cwd=self._workdir, env=env, limit=16 * 1024 * 1024,
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        got_text = False
        try:
            proc.stdin.write(self._cli_prompt(clean).encode())
            await proc.stdin.drain()
            proc.stdin.close()
            async for raw in proc.stdout:
                try:
                    ev = json.loads(raw)
                except ValueError:
                    continue
                if ev.get("type") == "stream_event":
                    e = ev.get("event") or {}
                    if e.get("type") == "content_block_delta" and e["delta"].get("type") == "text_delta":
                        got_text = True
                        yield e["delta"]["text"]
                elif ev.get("type") == "result" and ev.get("is_error"):
                    msg = ev.get("result") or ev.get("subtype") or "unknown error"
                    yield f"\n\n**Claude Code error:** {msg}"
                    got_text = True
            await proc.wait()
            if proc.returncode and not got_text:
                err = (await proc.stderr.read()).decode(errors="replace").strip()[-500:]
                hint = ""
                if "login" in err.lower() or "auth" in err.lower():
                    hint = " Run `claude` in a terminal and log in, then try again."
                yield f"**Claude Code failed** (exit {proc.returncode}): {err or 'no details'}.{hint}"
        finally:
            if proc.returncode is None:  # browser pressed Stop or went away
                proc.kill()
                await proc.wait()
