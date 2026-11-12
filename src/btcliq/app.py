"""Server: loads history, keeps the model live, pushes updates to the browser chart.

    btcliq --open             # http://localhost:8050
    btcliq --days 30          # longer liquidation history (max ~45)

The chart timeframe is picked in the browser. The liquidation model doesn't depend on
it: it's built from hourly open interest for older history and 5-minute open interest
for the last 3 days (the finest OKX keeps), then updated live every 30 seconds.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import time
import webbrowser
from datetime import datetime, time as dt_time, timezone
from pathlib import Path

import aiohttp
from aiohttp import web

from .book import VENUES as BOOK_VENUES, BookFeed
from .chat import DEFAULT_MODEL, Chat
from .exchanges import TIMEFRAMES, Exchanges
from .macro import MacroCalendar
from .options import FAMILIES, OptionsFeed
from .powerlaw import PowerLaw
from .stables import Stablecoins
from .telegram import TelegramMonitor
from .model import LiqModel

log = logging.getLogger("btcliq")
STATIC = Path(__file__).parent / "static"
DEFAULT_DATA = Path(__file__).resolve().parents[2] / "data"  # btcliq/data
MAX_DAYS = 45            # OKX hourly open interest history
FINE_DAYS = 3            # OKX 5-minute open interest history (~3.5 days available)
CHART_BARS = 1000        # candles sent per timeframe
CACHE_SECONDS = 120


class State:
    def __init__(self, args):
        self.args = args
        self.model = LiqModel(args.bin)
        self.price = 0.0
        self.last_1m: dict | None = None
        self.venues: dict[str, tuple[float, float]] = {}  # live venues: (oi_usd, price)
        self.delayed: dict[str, tuple[float, float, int]] = {}  # via CoinGecko: (oi, price, data_ms)
        self.liqs: list[dict] = []
        self.liq_ids: set[str] = set()
        self.clients: set[web.WebSocketResponse] = set()
        self.candle_cache: dict[str, tuple[float, list[dict]]] = {}
        self.candle_locks: dict[str, asyncio.Lock] = {}
        self.scale = 1.0
        self.ready = False
        self.book: BookFeed | None = None
        self.news: TelegramMonitor | None = None
        self.options: OptionsFeed | None = None
        self.powerlaw: PowerLaw | None = None
        self.stables: Stablecoins | None = None
        self.calendar: MacroCalendar | None = None

    @property
    def live_oi(self) -> float:
        return sum(oi for oi, _ in self.venues.values())

    @property
    def total_oi(self) -> float:
        return self.live_oi + sum(v[0] for v in self.delayed.values())

    @property
    def live_scale(self) -> float:
        """Live venues are ~1/3 of the market; scale their OI changes up to the full market."""
        return self.total_oi / self.live_oi if self.live_oi else 1.0

    def levels_msg(self) -> dict:
        p = self.price
        return {
            "type": "levels",
            "levels": self.model.levels(p, self.args.window, self.args.max_levels),
            "nearby": {f"{int(k * 100)}%": self.model.nearby(p, k) for k in (0.01, 0.03, 0.05)},
        }

    def venues_msg(self) -> dict:
        return {
            "type": "venues",
            "venues": [{"name": n, "oi": oi, "price": px} for n, (oi, px) in self.venues.items()]
                      + [{"name": n, "oi": oi, "price": px, "updated": ts}
                         for n, (oi, px, ts) in self.delayed.items()],
            "total_oi": self.total_oi,
            "live_scale": self.live_scale,
            "scale": self.scale,
        }

    def snapshot(self) -> dict:
        return {
            "type": "snapshot",
            "meta": {"days": self.args.days, "bin": self.args.bin,
                     "timeframes": {k: ms for k, (_, ms) in TIMEFRAMES.items()}},
            "price": self.price,
            "liqs": self.liqs[-2000:],
            "news": self.news.recent(200) if self.news else [],
            "channels": self.news.channel_list() if self.news else [],
            "powerlaw": self.powerlaw.state() if self.powerlaw else {"ready": False},
            "stables": self.stables.snapshot(compact=True) if self.stables else {"ready": False},
            "calendar": self.calendar.snapshot() if self.calendar else {"ready": False},
            **{k: v for k, v in self.levels_msg().items() if k != "type"},
            **{k: v for k, v in self.venues_msg().items() if k != "type"},
        }

    async def broadcast(self, msg: dict) -> None:
        if not self.clients:
            return
        data = json.dumps(msg)
        for ws in list(self.clients):
            try:
                await ws.send_str(data)
            except Exception:
                self.clients.discard(ws)


# ---------- startup: rebuild the model from history ----------
def _feed(st: State, candles: list[dict], oi: dict[int, float], bar_ms: int) -> None:
    for c in candles:
        cur, nxt = oi.get(c["time"]), oi.get(c["time"] + bar_ms)  # OI at start / end of bar
        if cur is not None and nxt is not None:
            d, before = (nxt - cur) * st.scale, cur * st.scale
        else:
            d, before = 0.0, 0.0
        st.model.step(c["time"], c["high"], c["low"], c["close"], d, before)


async def bootstrap(st: State, ex: Exchanges) -> None:
    days = st.args.days
    now = int(time.time() * 1000)
    start = now - int(days * 86_400_000)
    fine_start = now - FINE_DAYS * 86_400_000
    fine_start -= fine_start % 3_600_000  # hour boundary, so coarse and fine meet cleanly
    log.info("Loading %s days of open interest and candles from OKX...", days)

    coarse_c, coarse_oi, fine_c, fine_oi, venues = await asyncio.gather(
        ex.okx_candles_history("1H", start),
        ex.okx_oi_history("1H", start),
        ex.okx_candles_history("5m", fine_start),
        ex.okx_oi_history("5m", fine_start),
        ex.snapshot(),
    )
    st.venues = venues
    st.delayed = await ex.delayed()
    okx_now = venues.get("OKX USDT", (0, 0))[0]
    # OKX history stands in for the whole market: scale its changes by total / OKX.
    st.scale = st.total_oi / okx_now if okx_now else 1.0
    log.info("Market OI $%.2fB: $%.2fB live (%s) + $%.2fB delayed via CoinGecko (%s). "
             "OKX history scaled x%.2f",
             st.total_oi / 1e9, st.live_oi / 1e9, ", ".join(venues),
             (st.total_oi - st.live_oi) / 1e9, ", ".join(st.delayed) or "none", st.scale)

    if days > FINE_DAYS:
        _feed(st, [c for c in coarse_c if c["time"] + 3_600_000 <= fine_start], coarse_oi, 3_600_000)
    _feed(st, [c for c in fine_c if c["time"] >= max(fine_start, start)], fine_oi, 300_000)
    if fine_c:
        st.price = fine_c[-1]["close"]

    try:
        for l in await ex.okx_liquidations():
            if l["id"] not in st.liq_ids:
                st.liq_ids.add(l["id"])
                st.liqs.append(l)
        st.liqs.sort(key=lambda l: l["time"])
    except Exception as e:
        log.warning("Liquidations unavailable: %s", e)
    st.ready = True
    log.info("Model ready: %d long and %d short levels", len(st.model.long), len(st.model.short))


# ---------- live loops ----------
async def tick_loop(st: State, ex: Exchanges) -> None:
    """Every second: read the 1-minute candle, remove levels price just hit, push it out.
    The browser folds 1-minute updates into whatever timeframe it's showing."""
    while True:
        try:
            for c in await ex.okx_latest_candles("1m"):
                prev = st.last_1m
                if prev and c["time"] < prev["time"]:
                    continue
                if prev and c["time"] == prev["time"]:
                    lo, hi = min(prev["close"], c["close"]), max(prev["close"], c["close"])
                    if c["low"] < prev["low"]:
                        lo = c["low"]
                    if c["high"] > prev["high"]:
                        hi = c["high"]
                else:
                    lo, hi = c["low"], c["high"]
                    if prev:
                        lo, hi = min(lo, prev["close"]), max(hi, prev["close"])
                st.last_1m = c
                st.price = c["close"]
                hit = st.model.sweep(lo, hi)
                await st.broadcast({"type": "tick", "candle": c})
                if hit["long"] or hit["short"]:
                    await st.broadcast(st.levels_msg())
        except Exception as e:
            log.warning("price update failed: %s", e)
        await asyncio.sleep(1)


async def oi_loop(st: State, ex: Exchanges) -> None:
    """Every 30s: read OI on every venue and feed the change into the model."""
    while True:
        await asyncio.sleep(st.args.oi_seconds)
        try:
            now, delayed = await asyncio.gather(ex.snapshot(), ex.delayed())
            # Timing comes from the live venues; only compare venues seen both times, so a
            # venue dropping out isn't a fake drop. Size is scaled up to the whole market
            # (including Binance and Bybit, whose numbers arrive minutes late via CoinGecko).
            st.delayed.update(delayed)
            st.venues.update({n: v for n, v in now.items() if n not in st.venues})
            raw = sum(now[n][0] - st.venues[n][0] for n in now if n in st.venues)
            raw_before = sum(st.venues[n][0] for n in now if n in st.venues)
            st.venues.update(now)
            scale = st.live_scale
            d, before = raw * scale, raw_before * scale
            ts = st.last_1m["time"] if st.last_1m else int(time.time() * 1000)
            if d > 0:
                st.model.open_positions(ts, st.price, d)
            elif d < 0 and before > 0:
                st.model.close_fraction(min(1.0, -d / before))
            log.info("OI $%.2fB total, live change %+.1fM -> %+.1fM after x%.2f scale, price %.1f",
                     st.total_oi / 1e9, raw / 1e6, d / 1e6, scale, st.price)
            await st.broadcast(st.venues_msg())
            await st.broadcast(st.levels_msg())
        except Exception as e:
            log.warning("OI update failed: %s", e)


async def liq_loop(st: State, ex: Exchanges) -> None:
    """Every 10s: real liquidations on OKX, drawn as markers."""
    while True:
        await asyncio.sleep(10)
        try:
            new = [l for l in await ex.okx_liquidations() if l["id"] not in st.liq_ids]
            for l in new:
                st.liq_ids.add(l["id"])
            if new:
                new.sort(key=lambda l: l["time"])
                st.liqs.extend(new)
                st.liqs = st.liqs[-5000:]
                await st.broadcast({"type": "liqs", "liqs": new})
        except Exception as e:
            log.warning("liquidation update failed: %s", e)


# ---------- web ----------
async def index(_req):
    return web.FileResponse(STATIC / "index.html", headers={"Cache-Control": "no-store"})


async def candles_handler(req):
    st: State = req.app["state"]
    tf = req.query.get("tf", "15m")
    if tf not in TIMEFRAMES:
        raise web.HTTPBadRequest(text=f"tf must be one of {', '.join(TIMEFRAMES)}")
    return web.json_response(await get_candles(st, req.app["ex"], tf))


async def get_candles(st: State, ex: Exchanges, tf: str) -> list[dict]:
    async with st.candle_locks.setdefault(tf, asyncio.Lock()):  # one fetch per timeframe at a time
        cached = st.candle_cache.get(tf)
        if cached and time.time() - cached[0] < CACHE_SECONDS:
            return cached[1]
        okx_bar, ms = TIMEFRAMES[tf]
        start = int(time.time() * 1000) - CHART_BARS * ms
        candles = await ex.okx_candles_history(okx_bar, start)
        st.candle_cache[tf] = (time.time(), candles)
        return candles


async def prewarm_loop(st: State, ex: Exchanges) -> None:
    """Keep every timeframe's candles cached so switching is instant."""
    while True:
        for tf in TIMEFRAMES:
            try:
                await get_candles(st, ex, tf)
            except Exception as e:
                log.warning("couldn't preload %s candles: %s", tf, e)
        await asyncio.sleep(CACHE_SECONDS - 5)


BOOK_STEPS = (0.5, 1, 5, 10, 25, 50, 100, 250)


async def book_handler(req):
    st: State = req.app["state"]
    feed: BookFeed = req.app["book"]
    feed.touch()
    q = req.query
    try:
        step = float(q.get("step", 10))
        rng = min(0.1, max(0.001, float(q.get("range", 0.02))))
    except ValueError:
        raise web.HTTPBadRequest(text="step and range must be numbers")
    step = min(BOOK_STEPS, key=lambda x: abs(x - step))
    venues = [v for v in q.get("venues", "").split(",") if v in BOOK_VENUES] or None
    align = q.get("align", "1") != "0"
    ref = feed.books["OKX"].mid or st.price
    if not ref:
        return web.json_response({"bids": [], "asks": [], "venues": {}, "mid": None, "step": step})
    return web.json_response(feed.aggregate(step, ref, rng, venues, align))


async def telegram_handler(req):
    st: State = req.app["state"]
    mon: TelegramMonitor = st.news
    if req.method == "POST":
        body = await req.json()
        try:
            if body.get("add"):
                await mon.add(str(body["add"]))
            elif body.get("remove"):
                mon.remove(str(body["remove"]))
        except ValueError as e:
            return web.json_response({"error": str(e)}, status=400)
        except aiohttp.ClientResponseError as e:
            return web.json_response({"error": f"Telegram returned {e.status} for that channel"}, status=400)
        except Exception as e:
            return web.json_response({"error": f"Couldn't reach that channel: {e}"}, status=400)
        await st.broadcast({"type": "channels", "channels": mon.channel_list(),
                            "news": mon.recent(200)})
    return web.json_response({"channels": mon.channel_list(), "news": mon.recent(200)})


async def options_chain(req):
    st: State = req.app["state"]
    family = req.query.get("family", "BTC-USD")
    if family not in FAMILIES:
        raise web.HTTPBadRequest(text=f"family must be one of {', '.join(FAMILIES)}")
    try:
        return web.json_response(await st.options.chain(family))
    except Exception as e:
        log.warning("options chain failed: %s", e)
        return web.json_response({"error": f"Couldn't load options from OKX: {e}"}, status=502)


async def options_contract(req):
    st: State = req.app["state"]
    q = req.query.get("q", "").strip()
    if not q:
        raise web.HTTPBadRequest(text="q is required")
    try:
        contract, suggestions, _ = await st.options.find(q, req.query.get("family"))
        out = {"query": q, "contract": contract, "suggestions": suggestions}
        if contract:
            out.update(await st.options.detail(contract["name"]))
            out["index"] = (await st.options.chain(contract["family"]))["index"]
            if st.powerlaw and st.powerlaw.rows:
                try:
                    out["powerlaw"] = await asyncio.get_running_loop().run_in_executor(
                        None, st.powerlaw.evaluate, contract, out["index"])
                except Exception as e:
                    out["powerlaw"] = {"error": f"{type(e).__name__}: {e}"}
        return web.json_response(out)
    except Exception as e:
        log.warning("options lookup failed: %s", e)
        return web.json_response({"error": f"Couldn't query OKX: {e}"}, status=502)


MAX_DRAWINGS_BYTES = 2_000_000


async def drawings_handler(req):
    """Chart drawings (trend lines, fibs, ...), saved in data/drawings.json."""
    st: State = req.app["state"]
    path = Path(st.args.data_dir) / "drawings.json"
    if req.method == "PUT":
        raw = await req.read()
        if len(raw) > MAX_DRAWINGS_BYTES:
            raise web.HTTPRequestEntityTooLarge(max_size=MAX_DRAWINGS_BYTES, actual_size=len(raw))
        try:
            body = json.loads(raw)
            assert isinstance(body.get("drawings"), list)
        except (ValueError, AssertionError, AttributeError):
            raise web.HTTPBadRequest(text="expected {\"drawings\": [...]}")
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"drawings": body["drawings"]}))
        tmp.replace(path)  # atomic, so a crash mid-write can't corrupt saved drawings
        return web.json_response({"saved": len(body["drawings"])})
    try:
        return web.json_response(json.loads(path.read_text()))
    except (FileNotFoundError, ValueError):
        return web.json_response({"drawings": []})


async def stables_handler(req):
    st: State = req.app["state"]
    out = st.stables.snapshot() if st.stables else {"ready": False}
    if st.powerlaw and st.powerlaw.rows:  # daily BTC closes for the overlay (Coin Metrics)
        out["btc_history"] = [[int(datetime.combine(d, dt_time(), timezone.utc).timestamp() * 1000), px]
                              for d, px in st.powerlaw.rows if d.year >= 2017]
    return web.json_response(out)


async def stables_coin_handler(req):
    st: State = req.app["state"]
    coin = req.query.get("id", "")
    if not coin.isdigit():
        raise web.HTTPBadRequest(text="id must be a DefiLlama stablecoin id")
    try:
        return web.json_response({"id": coin, "history": await st.stables.coin_history(coin)})
    except Exception as e:
        return web.json_response({"error": f"Couldn't load that coin's history: {e}"}, status=502)


async def calendar_handler(req):
    st: State = req.app["state"]
    return web.json_response(st.calendar.snapshot() if st.calendar else {"ready": False})


async def powerlaw_handler(req):
    st: State = req.app["state"]
    return web.json_response(st.powerlaw.state() if st.powerlaw else {"ready": False})


async def chat_status(req):
    chat: Chat = req.app["chat"]
    return web.json_response({"enabled": chat.enabled, "model": chat.model, "backend": chat.backend,
                              "label": chat.label, "disabled_by_flag": chat.requested == "off"})


async def chat_handler(req):
    st: State = req.app["state"]
    chat: Chat = req.app["chat"]
    if not chat.enabled:
        raise web.HTTPServiceUnavailable(text="Chat is off: install Claude Code or add ANTHROPIC_API_KEY to .env, then restart btcliq.")
    body = await req.json()
    resp = web.StreamResponse(headers={"Content-Type": "text/plain; charset=utf-8",
                                       "Cache-Control": "no-store"})
    await resp.prepare(req)
    async for text in chat.stream(st, body.get("messages", []), body.get("tf"), body.get("option")):
        await resp.write(text.encode())
    await resp.write_eof()
    return resp


async def ws_handler(req):
    st: State = req.app["state"]
    ws = web.WebSocketResponse(heartbeat=30)
    await ws.prepare(req)
    while not st.ready:
        await asyncio.sleep(0.5)
    await ws.send_str(json.dumps(st.snapshot()))
    st.clients.add(ws)
    try:
        async for _ in ws:
            pass
    finally:
        st.clients.discard(ws)
    return ws


async def start_background(app):
    st: State = app["state"]
    session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=20), trust_env=True,
                                    headers={"User-Agent": "btcliq/0.1"})
    app["session"] = session
    app["ex"] = ex = Exchanges(session)
    app["book"] = st.book = BookFeed(session)
    st.options = OptionsFeed(session)
    st.powerlaw = PowerLaw(session, Path(st.args.data_dir))

    async def on_powerlaw(state):
        await st.broadcast({"type": "powerlaw", "state": state})

    st.powerlaw.on_update = on_powerlaw
    st.stables = Stablecoins(session)

    async def on_stables(snap):
        await st.broadcast({"type": "stables", "data": snap})

    st.stables.on_update = on_stables
    st.calendar = MacroCalendar(session)

    async def on_calendar(snap):
        await st.broadcast({"type": "calendar", "data": snap})

    st.calendar.on_update = on_calendar
    st.news = TelegramMonitor(session, Path(st.args.data_dir), st.args.telegram_seconds,
                              [c for c in st.args.telegram.split(",") if c.strip()])

    async def on_news(items, initial=False):
        await st.broadcast({"type": "news", "items": items, "initial": initial})

    async def on_status():
        await st.broadcast({"type": "channels", "channels": st.news.channel_list()})

    st.news.on_new, st.news.on_status = on_news, on_status
    await bootstrap(st, ex)
    app["tasks"] = [asyncio.create_task(f(st, ex)) for f in (tick_loop, oi_loop, liq_loop, prewarm_loop)]
    st.news.start()
    st.powerlaw.start(lambda: st.price or None)
    st.stables.start(lambda: st.price or None)
    st.calendar.start()
    if st.news.channels:
        log.info("Watching Telegram: %s", ", ".join(c["name"] for c in st.news.channels.values()))
    url = f"http://localhost:{st.args.port}"
    log.info("Chart ready at %s", url)
    if st.args.open:
        webbrowser.open(url)


async def stop_background(app):
    for t in app.get("tasks", []):
        t.cancel()
    if "book" in app:
        await app["book"].stop()
    if app["state"].news:
        await app["state"].news.stop()
    if app["state"].powerlaw:
        await app["state"].powerlaw.stop()
    if app["state"].stables:
        await app["state"].stables.stop()
    if app["state"].calendar:
        await app["state"].calendar.stop()
    await app["session"].close()


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(prog="btcliq", description="Live BTC liquidation-level chart")
    p.add_argument("--days", type=float, default=14,
                   help=f"liquidation history to build from (default 14, max {MAX_DAYS})")
    p.add_argument("--bin", type=float, default=50, help="price bucket size in USD (default 50)")
    p.add_argument("--window", type=float, default=0.15, help="show levels within this fraction of price")
    p.add_argument("--max-levels", type=int, default=600)
    p.add_argument("--oi-seconds", type=int, default=30, help="how often to read open interest")
    p.add_argument("--port", type=int, default=8050)
    p.add_argument("--open", action="store_true", help="open the chart in your browser")
    p.add_argument("--telegram", default="", help="extra Telegram channels to watch, comma-separated "
                   "(they're saved; you can also add channels in the browser)")
    p.add_argument("--telegram-seconds", type=int, default=30, help="how often to check each channel")
    p.add_argument("--data-dir", default=str(DEFAULT_DATA), help="where news history and settings are saved")
    p.add_argument("--chat", choices=["auto", "claude-code", "api", "off"], default="auto",
                   help="how the chat reaches Claude: your local Claude Code login, an API key, "
                        "or auto (Claude Code if installed, else API key)")
    p.add_argument("--model", default=DEFAULT_MODEL, help=f"Claude model for the chat (default {DEFAULT_MODEL})")
    args = p.parse_args(argv)
    if not 0 < args.days <= MAX_DAYS:
        p.error(f"--days must be between 0 and {MAX_DAYS} (OKX's hourly open-interest history)")

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
                        datefmt="%H:%M:%S")
    logging.getLogger("aiohttp.access").setLevel(logging.WARNING)

    app = web.Application()
    app["state"] = State(args)
    app["chat"] = Chat(args.model, args.chat)
    if app["chat"].enabled:
        log.info("Chat uses %s (%s)", app["chat"].label, args.model)
    elif args.chat == "off":
        log.info("Chat is off (--chat off)")
    else:
        log.info("Chat is off: install Claude Code or add ANTHROPIC_API_KEY to .env")
    app.router.add_get("/", index)
    app.router.add_get("/api/candles", candles_handler)
    app.router.add_get("/api/book", book_handler)
    app.router.add_route("*", "/api/telegram", telegram_handler)
    app.router.add_get("/api/options/chain", options_chain)
    app.router.add_get("/api/options/contract", options_contract)
    app.router.add_get("/api/powerlaw", powerlaw_handler)
    app.router.add_get("/api/stables", stables_handler)
    app.router.add_route("*", "/api/drawings", drawings_handler)
    app.router.add_get("/api/stables/coin", stables_coin_handler)
    app.router.add_get("/api/calendar", calendar_handler)
    app.router.add_get("/api/chat", chat_status)
    app.router.add_post("/api/chat", chat_handler)
    app.router.add_get("/ws", ws_handler)
    app.on_startup.append(start_background)
    app.on_cleanup.append(stop_background)
    web.run_app(app, host="127.0.0.1", port=args.port, print=None)


if __name__ == "__main__":
    main()
