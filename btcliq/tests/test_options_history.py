"""History of one option contract: range mapping, candle paging, IV rebuild, recorder merge."""

import asyncio
import json
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from aiohttp import web

from btcliq import options_history as oh
from btcliq.options import OptionsFeed
from btcliq.options_history import (BAR_MS, INDEX_CANDLES, MARK_CANDLES, TRADES, YEAR_MS, OptionHistory, black76,
                                    implied_vol, parity_forward, plan, rebuilt_iv)
from btcliq.options_store import OptionStore

NOW = int(datetime(2026, 11, 14, 12, 0, 30, tzinfo=timezone.utc).timestamp() * 1000)
EXP = int(datetime(2026, 11, 27, 8, tzinfo=timezone.utc).timestamp() * 1000)
H, D = 3_600_000, 86_400_000
FWD, VOL = 65_000.0, 0.5
CALL, PUT = "BTC-USD-261127-66000-C", "BTC-USD-261127-66000-P"
UM_CALL, UM_PUT = "BTC-USD_UM-261127-66000-C", "BTC-USD_UM-261127-66000-P"

# OKX's own numbers, read live on 2026-11-14: mark price, forward and markVol of in-the-money
# and out-of-the-money contracts in both families.
LIVE = [
    {"name": "BTC-USD-261118-60000-C", "family": "BTC-USD", "type": "C", "strike": 60000.0, "exp_ms": 1794988800000, "ts": 1794687180845, "mark": 0.0899419062201861, "fwd": 65596.9931138693, "iv": 0.7939656030095844},      # ITM
    {"name": "BTC-USD-261127-70000-P", "family": "BTC-USD", "type": "P", "strike": 70000.0, "exp_ms": 1795766400000, "ts": 1794687180845, "mark": 0.0750806192708481, "fwd": 65682.12114881168, "iv": 0.4205907896719639},     # ITM
    {"name": "BTC-USD-261127-74000-C", "family": "BTC-USD", "type": "C", "strike": 74000.0, "exp_ms": 1795766400000, "ts": 1794687180845, "mark": 0.0020218617357885, "fwd": 65682.12114881168, "iv": 0.4108961777528374},     # OTM
    {"name": "BTC-USD-261225-58000-P", "family": "BTC-USD", "type": "P", "strike": 58000.0, "exp_ms": 1798185600000, "ts": 1794687180845, "mark": 0.0200359462062247, "fwd": 65970.96813869642, "iv": 0.5044174954870074},     # OTM
    {"name": "BTC-USD-270326-50000-C", "family": "BTC-USD", "type": "C", "strike": 50000.0, "exp_ms": 1806048000000, "ts": 1794687180845, "mark": 0.275743945407098, "fwd": 66897.06748485452, "iv": 0.5035715859566436},      # ITM
    {"name": "BTC-USD_UM-261127-60000-C", "family": "BTC-USD_UM", "type": "C", "strike": 60000.0, "exp_ms": 1795766400000, "ts": 1794687182462, "mark": 6098.484213646462, "fwd": 65322.8, "iv": 0.5679896217287237},          # ITM
    {"name": "BTC-USD_UM-261127-72000-P", "family": "BTC-USD_UM", "type": "P", "strike": 72000.0, "exp_ms": 1795766400000, "ts": 1794687182462, "mark": 7002.292101502309, "fwd": 65322.8, "iv": 0.4447775143460555},          # ITM
    {"name": "BTC-USD_UM-261127-75000-C", "family": "BTC-USD_UM", "type": "C", "strike": 75000.0, "exp_ms": 1795766400000, "ts": 1794687182462, "mark": 99.8377024364301, "fwd": 65322.8, "iv": 0.43573559027845343},          # OTM
    {"name": "BTC-USD_UM-261225-58000-P", "family": "BTC-USD_UM", "type": "P", "strike": 58000.0, "exp_ms": 1798185600000, "ts": 1794687182462, "mark": 1333.2146027537326, "fwd": 65944.4, "iv": 0.5056886890008492},         # OTM
]


def contract(name: str, **kw) -> dict:
    family = "BTC-USD_UM" if "_UM" in name else "BTC-USD"
    expiry, strike, cp = name.split("-")[-3:]
    c = {"name": name, "family": family, "expiry": expiry, "exp_ms": EXP, "list_ms": NOW - 30 * D,
         "strike": float(strike), "type": cp, "ct_mult": 0.01, "tick": 0.0001 if family == "BTC-USD" else 5.0,
         "bid": None, "bid_sz": None, "ask": None, "ask_sz": None, "mark": None, "iv": VOL,
         "iv_bid": None, "iv_ask": None, "delta": None, "oi": None, "fwd": FWD}
    c.update(kw)
    return c


def model_mark(name: str, ts: int, vol: float = VOL, fwd: float = FWD) -> float:
    """What OKX would publish as this contract's mark at ts, rounded like its candles."""
    c = contract(name)
    usd = black76(fwd, c["strike"], (EXP - ts) / YEAR_MS, vol, c["type"])
    return round(usd / fwd, 4) if c["family"] == "BTC-USD" else round(usd, 1)


class FakeFeed:
    """Stands in for OptionsFeed: a chain, plus OKX candles and trades served page by page."""

    def __init__(self, names=(CALL, PUT, UM_CALL, UM_PUT), page=300, first_candle=0):
        self.contracts = [contract(n) for n in names]
        self.page, self.first_candle = page, first_candle
        self.calls: list[tuple[str, dict]] = []
        self.trades: list[dict] = []
        self.fail: set[str] = set()          # instIds (or paths) OKX refuses

    async def chain(self, family):
        return {"family": family, "index": 64_900.0, "ts": NOW,
                "contracts": [c for c in self.contracts if c["family"] == family]}

    def candle_calls(self, inst_id=None):
        return [p for path, p in self.calls if path != TRADES and (inst_id is None or p["instId"] == inst_id)]

    async def _okx(self, path, **p):
        self.calls.append((path, p))
        if p["instId"] in self.fail or path in self.fail:
            raise RuntimeError(f"OKX {path}: Instrument ID doesn't exist.")
        if path == TRADES:
            return self.trades[:p["limit"]]
        bar = BAR_MS[p["bar"]]
        newest = min(int(p["after"]) - bar, NOW) if "after" in p else NOW
        ts, rows = newest // bar * bar, []
        while len(rows) < min(self.page, p["limit"]) and ts >= self.first_candle:
            close = 64_900.0 if path == INDEX_CANDLES else model_mark(p["instId"], min(ts + bar, NOW))
            rows.append([str(ts), "0", "0", "0", repr(close), "1" if ts + bar <= NOW else "0"])
            ts -= bar
        return rows


@pytest.fixture(autouse=True)
def frozen_clock(monkeypatch):
    monkeypatch.setattr(oh.time, "time", lambda: NOW / 1000)


def history(feed, name=CALL, rng="1D", store=None):
    return asyncio.run(OptionHistory(feed, store).history(name, rng))


# ---------- range to bar ----------
def test_each_range_has_its_bar():
    assert plan("1H", NOW) == (NOW - H, "1m")
    assert plan("1D", NOW) == (NOW - D, "5m")
    assert plan("1W", NOW) == (NOW - 7 * D, "1H")
    assert plan("nonsense", NOW) == (NOW - D, "5m")


@pytest.mark.parametrize("age, bar", [(2 * H, "1m"), (2 * D, "5m"), (5 * D, "15m"), (20 * D, "1H"),
                                      (60 * D, "4H"), (300 * D, "1Dutc")])
def test_all_picks_the_bar_from_the_contracts_age(age, bar):
    since, got = plan("All", NOW, NOW - age)
    assert (since, got) == (NOW - age, bar)
    assert age / BAR_MS[bar] <= oh.ALL_MAX_BARS            # so it takes at most a few requests


def test_all_without_a_listing_time_uses_daily_bars():
    assert plan("All", NOW, None) == (0, "1Dutc")


# ---------- Black-76 and the rebuild ----------
@pytest.mark.parametrize("f", LIVE, ids=lambda f: f["name"])
def test_implied_vol_matches_okx_mark_vol(f):
    coin = f["family"] == "BTC-USD"
    t = (f["exp_ms"] - f["ts"]) / YEAR_MS
    iv = implied_vol(f["mark"] * f["fwd"] if coin else f["mark"], f["fwd"], f["strike"], t, f["type"])
    assert abs(iv - f["iv"]) < 0.0025                      # a quarter of a vol point


@pytest.mark.parametrize("f", LIVE, ids=lambda f: f["name"])
def test_rebuilt_iv_from_the_paired_mark_matches_okx(f):
    """No forward given: it has to come from put-call parity with the other type's mark."""
    coin = f["family"] == "BTC-USD"
    t = (f["exp_ms"] - f["ts"]) / YEAR_MS
    other = "P" if f["type"] == "C" else "C"
    pair = black76(f["fwd"], f["strike"], t, f["iv"], other) / (f["fwd"] if coin else 1)
    assert abs(rebuilt_iv(f, f["ts"], f["mark"], pair=pair) - f["iv"]) < 0.0025
    # Without the pair: index times today's forward/index ratio.
    assert abs(rebuilt_iv(f, f["ts"], f["mark"], index=f["fwd"] / 1.001, basis=1.001) - f["iv"]) < 0.0025


def test_parity_forward_in_both_families():
    t = 20 / 365
    call, put = black76(FWD, 66_000, t, VOL, "C"), black76(FWD, 66_000, t, VOL, "P")
    assert parity_forward(call, put, 66_000, coin=False) == pytest.approx(FWD)
    assert parity_forward(call / FWD, put / FWD, 66_000, coin=True) == pytest.approx(FWD)


def test_implied_vol_has_no_answer_below_intrinsic_value_or_after_expiry():
    assert implied_vol(4_000, 65_000, 60_000, 0.05, "C") is None     # worth at least 5,000
    assert implied_vol(500, 65_000, 66_000, 0, "C") is None
    assert implied_vol(None, 65_000, 66_000, 0.05, "C") is None
    assert rebuilt_iv(contract(CALL), EXP + 1, 0.01, pair=0.02) is None
    assert rebuilt_iv(contract(CALL), NOW, None, pair=0.02) is None
    assert rebuilt_iv(contract(CALL), NOW, 0.01) is None             # no pair and no index: no forward


def test_rebuilt_iv_is_dropped_when_rounding_could_move_it_too_far():
    far = contract("BTC-USD-261127-120000-C")
    t = NOW + 12 * D                                       # a day before expiry: almost no vega out there
    mark, pair = 0.0001, round(black76(FWD, 120_000, (EXP - t) / YEAR_MS, 1.2, "P") / FWD, 4)
    assert rebuilt_iv(far, t, mark, pair=pair, step=0.0) is not None
    assert rebuilt_iv(far, t, mark, pair=pair, step=0.0001) is None


# ---------- candles: paging and caching ----------
def test_candles_are_paged_back_to_the_start_of_the_range():
    feed = FakeFeed(page=300)
    hist = OptionHistory(feed)
    since = NOW - 650 * H
    closes, step = asyncio.run(hist._candles(MARK_CANDLES, CALL, "1H", since))
    calls = feed.candle_calls()
    assert len(calls) == 3
    assert "after" not in calls[0] and all(c["limit"] == 300 and c["bar"] == "1H" for c in calls)
    newest = NOW // H * H
    assert [int(c["after"]) for c in calls[1:]] == [newest - 299 * H, newest - 599 * H]   # each page's oldest bar
    assert min(closes) <= since <= min(closes) + H and max(closes) == newest
    assert step == 0.0001

    asyncio.run(hist._candles(MARK_CANDLES, CALL, "1H", since + 60_000))   # asked again 10 s later: from the cache
    assert len(feed.candle_calls()) == 3


def test_paging_stops_when_okx_has_no_older_candles():
    feed = FakeFeed(page=100, first_candle=NOW - 150 * H)
    closes, _ = asyncio.run(OptionHistory(feed)._candles(MARK_CANDLES, CALL, "1H", 0))
    assert len(feed.candle_calls()) == 3                   # 100, 50, then an empty page
    assert len(closes) == 150


def test_paging_gives_up_after_a_few_requests():
    feed = FakeFeed(page=10)
    asyncio.run(OptionHistory(feed)._candles(MARK_CANDLES, CALL, "1m", 0))
    assert len(feed.candle_calls()) == oh.MAX_PAGES


def test_okx_answers_are_reused_for_a_while(monkeypatch):
    feed = FakeFeed()
    hist = OptionHistory(feed)
    asyncio.run(hist.history(CALL, "1D"))
    n = len(feed.calls)
    asyncio.run(hist.history(CALL, "1D"))
    assert len(feed.calls) == n
    monkeypatch.setattr(oh.time, "time", lambda: NOW / 1000 + oh.OKX_TTL + 1)
    asyncio.run(hist.history(CALL, "1D"))
    assert len(feed.calls) == 2 * n


# ---------- the answer the page gets ----------
def test_bars_without_a_recorder_have_mark_and_rebuilt_iv_and_null_quotes():
    feed = FakeFeed()
    out = history(feed, CALL, "1D")
    bars = out["bars"]
    assert out["bar"] == "5m" and 287 <= len(bars) <= 289
    ts = [b["t"] for b in bars]
    assert ts == sorted(ts) and len(set(ts)) == len(ts)   # oldest first, no repeats
    assert ts[0] >= NOW - D and ts[-1] == NOW              # the open candle counts as "now"
    assert ts[-2] == NOW // 300_000 * 300_000              # closed candles sit at the end of their bar
    for b in bars:
        assert b["bid"] is None and b["ask"] is None
        assert b["mark"] == model_mark(CALL, b["t"])
        assert b["iv_src"] == "rebuilt" and abs(b["iv"] - VOL) < 0.01
    assert out["trades"] == []
    json.dumps(out, allow_nan=False)
    # One request each for the contract, its pair and the trades; the index isn't needed.
    assert sorted({p["instId"] for _, p in feed.calls}) == [CALL, PUT]
    assert not any(path == INDEX_CANDLES for path, _ in feed.calls)


def test_usd_margined_bars_are_in_usd():
    out = history(FakeFeed(), UM_PUT, "1W")
    assert out["bar"] == "1H" and 167 <= len(out["bars"]) <= 169
    last = out["bars"][-1]
    assert last["mark"] == model_mark(UM_PUT, NOW) > 1000
    assert abs(last["iv"] - VOL) < 0.01


def test_all_range_goes_back_to_the_listing():
    feed = FakeFeed()
    out = history(feed, CALL, "All")
    assert out["bar"] == "4H"                              # listed 30 days ago: 180 four-hour bars
    assert 179 <= len(out["bars"]) <= 182
    assert len(feed.candle_calls(CALL)) == 1


def test_recorded_quotes_replace_candles_where_the_recorder_ran(tmp_path):
    store = OptionStore(tmp_path)
    start = NOW - 119 * 10_000 - 5_000                     # recording began 20 minutes ago, every 10 s
    for i in range(120):
        ts = start + i * 10_000
        store.record({"family": "BTC-USD", "index": 64_900.0, "ts": ts, "contracts": [
            contract(CALL, bid=0.0301, ask=0.0309, mark=0.03051234, iv=0.4321)]})
    feed = FakeFeed()

    day = history(feed, CALL, "1D", store)["bars"]
    rec = [b for b in day if b["bid"] is not None]
    assert len(rec) == 5                                   # one per five-minute bar: its last sample
    assert all(b == {"t": b["t"], "mark": 0.0305123, "bid": 0.0301, "ask": 0.0309, "iv": 0.4321} for b in rec)
    assert rec[-1]["t"] == NOW - 5_000
    old = [b for b in day if b["bid"] is None]
    assert old and all(b["iv_src"] == "rebuilt" and b["t"] < rec[0]["t"] for b in old)
    assert [b["t"] for b in day] == sorted(b["t"] for b in day)

    hour = history(feed, CALL, "1H", store)["bars"]        # the shortest range shows every sample
    assert len([b for b in hour if b["bid"] is not None]) == 120
    assert len([b for b in hour if b["bid"] is None]) >= 38
    store.close()


def test_unknown_contract_gets_empty_bars_and_okx_is_not_asked():
    feed = FakeFeed()
    assert history(feed, "BTC-USD-270101-1-C") == {"bars": [], "trades": []}
    assert history(feed, "not a contract") == {"bars": [], "trades": []}
    assert history(feed, "ETH-USD-261127-3000-C") == {"bars": [], "trades": []}
    assert feed.calls == []


def test_expired_contract_is_served_from_the_recorder(tmp_path):
    store = OptionStore(tmp_path)
    gone = "BTC-USD-261113-66000-C"
    exp = NOW - 28 * H
    for i in range(30):
        store.record({"family": "BTC-USD", "index": 64_900.0, "ts": exp - (30 - i) * 60_000, "contracts": [
            contract(gone, exp_ms=exp, bid=0.01, ask=0.011, mark=0.0105, iv=0.6)]})
    feed = FakeFeed()
    out = history(feed, gone, "1H", store)
    assert len(out["bars"]) == 30 and out["bars"][0]["bid"] == 0.01
    assert feed.calls == []
    assert history(feed, "BTC-USD-261112-66000-C", "1H", store) == {"bars": [], "trades": []}
    store.close()


def test_missing_pair_falls_back_to_the_index():
    feed = FakeFeed()
    feed.fail.add(PUT)
    out = history(feed, CALL, "1D")
    assert any(path == INDEX_CANDLES and p["instId"] == "BTC-USD" for path, p in feed.calls)
    ivs = [b["iv"] for b in out["bars"]]
    assert all(v is not None for v in ivs)
    assert all(abs(v - VOL) < 0.02 for v in ivs)           # forward = index * (65,000 / 64,900) = 65,000


def test_bars_survive_when_nothing_can_rebuild_the_iv():
    feed = FakeFeed()
    feed.fail |= {PUT, INDEX_CANDLES}
    bars = history(feed, CALL, "1D")["bars"]
    assert len(bars) >= 287
    assert all(b["iv"] is None and "iv_src" not in b and b["mark"] is not None for b in bars)


def test_okx_failure_is_an_error_unless_the_recorder_has_something(tmp_path):
    feed = FakeFeed()
    feed.fail |= {CALL, PUT}
    with pytest.raises(RuntimeError):
        history(feed, CALL, "1D")
    store = OptionStore(tmp_path)
    store.record({"family": "BTC-USD", "index": 64_900.0, "ts": NOW - 60_000,
                  "contracts": [contract(CALL, bid=0.03, ask=0.031, mark=0.0305, iv=0.5)]})
    out = history(feed, CALL, "1D", store)
    assert [b["bid"] for b in out["bars"]] == [0.03]
    assert "trades" not in out                             # the page keeps its hourly dots
    store.close()


def okx_trade(ts, px="0.0115", sz="16", side="buy"):
    return {"instId": CALL, "tradeId": "1", "px": px, "sz": sz, "side": side, "ts": str(ts)}


def test_trades_are_single_trades_inside_the_range_newest_first():
    feed = FakeFeed()
    feed.trades = [okx_trade(NOW - 5 * 60_000, side="sell"), okx_trade(NOW - 3 * H), okx_trade(NOW - 3 * D)]
    assert history(feed, CALL, "1D")["trades"] == [
        {"time": NOW - 5 * 60_000, "px": 0.0115, "sz": 16.0, "side": "sell"},
        {"time": NOW - 3 * H, "px": 0.0115, "sz": 16.0, "side": "buy"}]
    assert len(history(feed, CALL, "1W")["trades"]) == 3


def test_trades_are_left_out_when_okx_holds_more_than_one_request_reaches():
    feed = FakeFeed()
    feed.trades = [okx_trade(NOW - i * 1000) for i in range(oh.TRADES_MAX)]   # 500 trades in 8 minutes
    assert "trades" not in history(feed, CALL, "1D")       # incomplete: the page uses its hourly candles
    feed2 = FakeFeed()
    feed2.trades = [okx_trade(NOW - i * 3 * 60_000) for i in range(oh.TRADES_MAX)]   # 500 trades in 25 hours
    assert len(history(feed2, CALL, "1D")["trades"]) == 481       # one every 3 minutes, both ends of the day included


# ---------- the route ----------
def ask_route(feed, query):
    from btcliq.app import options_history
    st = SimpleNamespace(option_history=OptionHistory(feed))
    return asyncio.run(options_history(SimpleNamespace(app={"state": st}, query=query)))


def test_route_answers_200_with_no_bars_for_an_unknown_contract():
    r = ask_route(FakeFeed(), {"instId": "BTC-USD-270101-1-C", "range": "1W"})
    assert r.status == 200 and json.loads(r.body) == {"bars": [], "trades": []}


def test_route_serves_bars_and_never_answers_404():
    r = ask_route(FakeFeed(), {"instId": CALL})
    body = json.loads(r.body)
    assert r.status == 200 and len(body["bars"]) >= 287 and set(body["bars"][0]) >= {"t", "mark", "bid", "ask", "iv"}
    feed = FakeFeed()
    feed.fail |= {CALL, PUT}
    r = ask_route(feed, {"instId": CALL})
    assert r.status == 502 and "error" in json.loads(r.body)   # the page falls back for this draw only
    with pytest.raises(web.HTTPBadRequest):
        ask_route(FakeFeed(), {})


# ---------- OptionsFeed: single trades and the recorder hook ----------
class OkxFeed(OptionsFeed):
    """OptionsFeed with canned OKX answers."""

    def __init__(self, answers):
        super().__init__(None)
        self.answers, self.asked = answers, []

    async def _okx(self, path, **params):
        self.asked.append(path)
        a = self.answers[path]
        if isinstance(a, Exception):
            raise a
        return a


def test_contract_detail_carries_single_trades_newest_first():
    answers = {
        "/api/v5/market/books": [{"bids": [["0.0114", "20", "0", "1"]], "asks": [["0.0116", "30", "0", "1"]]}],
        "/api/v5/market/candles": [],
        "/api/v5/market/trades": [okx_trade(NOW - i * 1000, px="0.012", sz="90", side="sell" if i else "buy")
                                  for i in range(60)],
    }
    out = asyncio.run(OkxFeed(answers).detail(CALL))
    assert len(out["trades"]) == 50
    assert out["trades"][0] == {"time": NOW, "px": 0.012, "sz": 90.0, "side": "buy"}
    assert out["trades"][1]["side"] == "sell" and out["trades"][1]["time"] == NOW - 1000
    assert out["book"] == {"bids": [[0.0114, 20.0]], "asks": [[0.0116, 30.0]]}

    answers["/api/v5/market/trades"] = RuntimeError("OKX is down")
    out = asyncio.run(OkxFeed(answers).detail(CALL))
    assert "trades" not in out and out["book"]             # the page falls back to hourly candles


def test_every_fresh_chain_read_is_recorded_once(tmp_path):
    answers = {
        "/api/v5/public/instruments": [{"instId": CALL, "expTime": str(EXP), "listTime": str(NOW - D), "stk": "66000",
                                        "optType": "C", "ctMult": "0.01", "tickSz": "0.0001", "state": "live"}],
        "/api/v5/market/tickers": [{"instId": CALL, "bidPx": "0.0301", "bidSz": "320", "askPx": "0.0309",
                                    "askSz": "100", "last": "", "vol24h": "0"}],
        "/api/v5/public/opt-summary": [{"instId": CALL, "markVol": "0.4321", "bidVol": "0.42", "askVol": "0.44",
                                        "deltaBS": "0.41", "gammaBS": "0.0001", "thetaBS": "-30", "vegaBS": "50",
                                        "fwdPx": "65000"}],
        "/api/v5/public/mark-price": [{"instId": CALL, "markPx": "0.03051234"}],
        "/api/v5/public/open-interest": [{"instId": CALL, "oi": "12", "oiCcy": "0.12", "oiUsd": "7800"}],
        "/api/v5/market/index-tickers": [{"idxPx": "64900"}],
    }

    async def go():
        feed = OkxFeed(answers)
        feed.store = OptionStore(tmp_path)
        first = await feed.chain("BTC-USD")
        again = await feed.chain("BTC-USD")                # a few seconds later: the cached read, not a new one
        assert again is first and len(feed.asked) == 6
        assert first["contracts"][0]["list_ms"] == NOW - D
        await feed.stop()                                  # waits for the write, then closes the file
        return first["ts"]

    ts = asyncio.run(go())
    assert OptionStore(tmp_path).read(CALL, 0, ts) == [
        {"t": ts, "mark": 0.0305123, "bid": 0.0301, "ask": 0.0309, "iv": 0.4321}]


def test_a_recorder_failure_never_breaks_the_chain(tmp_path):
    class Broken:
        def record(self, snap):
            raise OSError("disk full")

        def close(self):
            pass

    async def go():
        feed = OkxFeed({p: [] for p in ("/api/v5/public/instruments", "/api/v5/market/tickers",
                                        "/api/v5/public/opt-summary", "/api/v5/public/mark-price",
                                        "/api/v5/public/open-interest", "/api/v5/market/index-tickers")})
        feed.store = Broken()
        snap = await feed.chain("BTC-USD")
        await feed.stop()
        return snap

    assert asyncio.run(go())["contracts"] == []
