"""Polymarket parsing: odds, hit-ladder dedupe, summaries, up/down windows and the cache.

Events are synthetic but shaped like Gamma API responses (no network).
"""

import asyncio
import json
import time

import pytest

from btcliq import polymarket as P


def mk(label, yes=None, bid=None, ask=None, last=None, closed=False, q="", outcomes=("Yes", "No"), **kw):
    m = {"groupItemTitle": label, "question": q, "outcomes": json.dumps(list(outcomes)),
         "outcomePrices": json.dumps([str(yes), str(1 - yes)] if yes is not None else []),
         "bestBid": bid, "bestAsk": ask, "lastTradePrice": last, "closed": closed,
         "volume24hr": kw.pop("vol24", 100.0), "oneDayPriceChange": kw.pop("chg", None), "slug": kw.pop("slug", label)}
    m.update(kw)
    return m


def ev(title, markets, slug="ev", end="2026-11-14T17:00:00Z", tags=("bitcoin",), **kw):
    return {"title": title, "slug": slug, "endDate": end, "tags": [{"slug": t} for t in tags],
            "markets": markets, "volume24hr": 1000, **kw}


# ---------- odds ----------

def test_odds_follow_polymarket_display_rule():
    assert P.outcome_prob(mk("x", 0.4, bid=0.43, ask=0.45, last=0.40)) == pytest.approx(0.44)    # midpoint
    assert P.outcome_prob(mk("x", 0.4, bid=0.20, ask=0.45, last=0.41)) == pytest.approx(0.41)    # wide: last trade
    assert P.outcome_prob(mk("x", 0.0005, bid=None, ask=0.001)) == pytest.approx(0.0005)          # dead strike
    assert P.outcome_prob(mk("x", 1.0, bid=0.999, ask=1, closed=True)) == 1.0                     # resolved
    up_down = mk(None, 0.6, bid=0.58, ask=0.60, outcomes=("Up", "Down"))
    assert P.outcome_prob(up_down, "Up") == pytest.approx(0.59)
    assert P.outcome_prob(up_down, "Down") == pytest.approx(0.41)                                 # complement of the book
    lopsided = mk("x", 0.3, bid=0.25, ask=None, last=None)                                       # one-sided book, no trades
    assert P.outcome_prob(lopsided, "Yes") == pytest.approx(0.3)
    assert P.outcome_prob(lopsided, "No") == pytest.approx(0.7)


def test_labels():
    assert P._label("What price will Bitcoin hit in November?") == "In November"
    assert P._label("What price will Bitcoin hit November 9-15?") == "November 9-15"
    assert P._label("What price will Bitcoin hit on November 13?") == "On November 13"
    assert P._label("Bitcoin above ___ on November 14?") == "On November 14"
    assert P._label("Bitcoin price on November 14?") == "On November 14"


# ---------- ladders ----------

def test_hit_ladder_prefers_open_market_and_marks_touched():
    markets = [
        mk("↑ 80,000", 1, closed=True, q="Will Bitcoin reach $80,000 in November?", slug="a"),
        mk("↑ 80,000", 0.065, bid=0.06, ask=0.07, q="Will Bitcoin reach $80,000 in November?", slug="b"),
        mk("↓ 70,000", 1, closed=True, q="Will Bitcoin dip to $70,000 in November?"),
        mk("↓ 70,000", 1, closed=True, q="Will Bitcoin dip to $70,000 in November?"),   # duplicate re-listing
        mk("↑ 72,500", 0.40, bid=0.40, ask=0.41),
        mk("↑ 70,000", 0.70, bid=0.70, ask=0.71),
        mk("↓ 67,500", 0.89, bid=0.88, ask=0.90),
        mk("↓ 65,000", 0.55, bid=0.55, ask=0.56),
        mk("↓ 62,500", 0.30, bid=0.30, ask=0.31),
        mk("↓ 99,000", 0, closed=True),                                                 # resolved No: dropped
    ]
    e = P.parse_event(ev("What price will Bitcoin hit in November?", markets, tags=("bitcoin", "monthly")), "hit")
    rows = [(m["dir"], m["strike"], m.get("touched", False)) for m in e["markets"]]
    assert rows == [("up", 80000, False), ("up", 72500, False), ("up", 70000, False), ("down", 70000, True),
                    ("down", 67500, False), ("down", 65000, False), ("down", 62500, False)]
    assert e["markets"][0]["slug"] == "b" and e["horizon"] == "month" and e["label"] == "In November"
    s = e["summary"]
    assert s["near_up"] == {"strike": 70000, "p": pytest.approx(0.705)}
    assert s["near_down"]["strike"] == 67500
    assert 70000 < s["up50"] < 72500                         # 0.705 at 70k → 0.405 at 72.5k
    assert 62500 < s["down50"] < 65000


def test_yearly_hit_event_is_labelled_year_not_month():
    e = P.parse_event(ev("What price will Bitcoin hit in 2026?", [mk("↑ 90,000", 0.07, bid=0.07, ask=0.08)],
                         tags=("bitcoin", "yearly", "hit-price")), "hit")
    assert e["horizon"] == "year" and e["label"] == "In 2026"


def test_above_ladder_median_and_range():
    strikes = [(66000, 0.96), (68000, 0.52), (70000, 0.08), (72000, 0.01)]
    e = P.parse_event(ev("Bitcoin above ___ on November 14?",
                         [mk(f"{k:,}", p, bid=p - 0.005, ask=p + 0.005) for k, p in strikes]), "above")
    assert [m["strike"] for m in e["markets"]] == [72000, 70000, 68000, 66000]
    s = e["summary"]
    assert s["median"] == pytest.approx(68000 + (0.5 - 0.52) / (0.08 - 0.52) * 2000)
    assert 66000 < s["lo"] < 68000 < s["hi"] < 70000


def test_range_ladder_order_and_summary():
    buckets = [("<66,000", 0.04), ("66,000-68,000", 0.44), ("68,000-70,000", 0.50), ("70,000-72,000", 0.03), (">72,000", 0.01)]
    e = P.parse_event(ev("Bitcoin price on November 14?", [mk(l, p, bid=p, ask=p + 0.01) for l, p in buckets]), "range")
    assert [m["label"] for m in e["markets"]] == [">72,000", "70,000-72,000", "68,000-70,000", "66,000-68,000", "<66,000"]
    s = e["summary"]
    assert s["top"]["label"] == "68,000-70,000"
    assert 67000 < s["median"] < 69000
    # The median lands in the bucket right after the open-ended "<66,000" one (both start at 66,000).
    low = [("<66,000", 0.04), ("66,000-68,000", 0.47), ("68,000-70,000", 0.45), ("70,000-72,000", 0.04)]
    e = P.parse_event(ev("Bitcoin price on November 14?", [mk(l, p, bid=p, ask=p) for l, p in low]), "range")
    assert e["summary"]["median"] == pytest.approx(66000 + (0.5 - 0.04) / 0.47 * 2000)


# ---------- up / down ----------

def test_updown_window():
    now = P._ms("2026-11-13T23:20:00Z")
    e = ev("Bitcoin Up or Down - November 13, 6:15PM-6:30PM ET",
           [mk(None, 0.55, bid=0.54, ask=0.56, outcomes=("Up", "Down"))], end="2026-11-13T23:30:00Z",
           startTime="2026-11-13T23:15:00Z")
    w = P.parse_updown(e, "15m", now)
    assert w["live"] and w["up"] == pytest.approx(0.55) and w["end"] - w["start"] == 15 * 60000
    assert P.parse_updown(e, "15m", P._ms("2026-11-13T23:31:00Z")) is None          # already over


# ---------- fetching and cache ----------

def test_cache_and_stale_fallback(monkeypatch):
    async def go():
        pm = P.Polymarket(session=None)
        calls = []

        async def fake_events(series, limit, ascending=False):
            calls.append(series)
            if fail["on"]:
                raise OSError("network down")
            if series == "btc-multi-strikes-weekly":
                return [ev("Bitcoin above ___ on November 14?", [mk("68,000", 0.5, bid=0.5, ask=0.51)], slug="a14")]
            return []

        fail = {"on": False}
        monkeypatch.setattr(pm, "_events", fake_events)
        d = await pm.ladders()
        assert d["ready"] and not d.get("stale") and d["ladders"]["above"][0]["slug"] == "a14"
        n = len(calls)
        await pm.ladders()
        assert len(calls) == n                                   # cached
        fail["on"] = True
        pm._cache["ladders"] = (time.time() - P.LADDER_TTL - 1, pm._cache["ladders"][1])
        d = await pm.ladders()
        assert d["ready"] and d["stale"] and "network down" in d["error"]   # last good odds, marked stale
        assert d["ladders"]["above"][0]["slug"] == "a14"
        pm2 = P.Polymarket(session=None)
        monkeypatch.setattr(pm2, "_events", fake_events)
        d = await pm2.ladders()
        assert not d["ready"] and "network down" in d["error"]  # nothing good yet
    asyncio.run(go())
