"""The option recorder: snapshots in, real units out, monthly files, thinning."""

import sqlite3
from datetime import datetime, timezone

from btcliq.options_store import DAY_MS, OptionStore, month_of


def ms(*a) -> int:
    return int(datetime(*a, tzinfo=timezone.utc).timestamp() * 1000)


def contract(name: str, **kw) -> dict:
    family = "BTC-USD_UM" if "_UM" in name else "BTC-USD"
    expiry, strike, cp = name.split("-")[-3:]
    c = {"name": name, "family": family, "expiry": expiry,
         "exp_ms": int(datetime.strptime(expiry, "%y%m%d").replace(hour=8, tzinfo=timezone.utc).timestamp() * 1000),
         "strike": float(strike), "type": cp, "ct_mult": 0.01, "tick": 0.0001 if family == "BTC-USD" else 5.0,
         "bid": None, "bid_sz": None, "ask": None, "ask_sz": None, "mark": None,
         "iv": None, "iv_bid": None, "iv_ask": None, "delta": None, "oi": None, "fwd": None}
    c.update(kw)
    return c


def snap(ts: int, *contracts: dict, index: float = 65644.9) -> dict:
    return {"family": contracts[0]["family"], "index": index, "ts": ts, "contracts": list(contracts)}


COIN = "BTC-USD-261127-75000-C"
UM = "BTC-USD_UM-261127-75000-C"


def test_snapshot_round_trip_in_ticks_and_basis_points(tmp_path):
    store = OptionStore(tmp_path)
    t = ms(2026, 11, 14, 12)
    n = store.record(snap(t, contract(COIN, bid=0.0115, bid_sz=320.0, ask=0.012, ask_sz=3043.0, mark=0.0117234561,
                                      iv=0.56093, iv_bid=0.5469, iv_ask=0.5664, delta=-0.44882, oi=12.0,
                                      fwd=65662.83)))
    assert n == 1
    store.record(snap(t + 1, contract(UM, bid=595.0, ask=600.0, mark=7.286452, iv=1.4656, fwd=65320.5)))

    # Stored as small integers: ticks, thousandths of a tick, basis points, whole contracts.
    con = sqlite3.connect(tmp_path / "2026-11.sqlite")
    assert con.execute("SELECT bid, ask, mark, bid_sz, ask_sz, iv, iv_bid, iv_ask, delta, oi FROM quotes q "
                       "JOIN contracts c ON c.id=q.contract_id WHERE inst_id=?", (COIN,)).fetchone() == (
        115, 120, 117235, 320, 3043, 5609, 5469, 5664, -4488, 12)
    assert con.execute("SELECT bid, ask, mark, iv FROM quotes q JOIN contracts c ON c.id=q.contract_id "
                       "WHERE inst_id=?", (UM,)).fetchone() == (119, 120, 1457, 14656)
    assert con.execute("PRAGMA journal_mode").fetchone()[0] == "wal"

    # ...and read back in real units.
    assert store.read(COIN, 0, t + 10) == [{"t": t, "mark": 0.0117235, "bid": 0.0115, "ask": 0.012, "iv": 0.5609}]
    assert store.read(UM, 0, t + 10) == [{"t": t + 1, "mark": 7.285, "bid": 595.0, "ask": 600.0, "iv": 1.4656}]

    # The view a research session reads with plain sqlite3.
    row = con.execute("SELECT ts, inst_id, family, strike, type, bid, ask, mark, bid_sz, iv, delta, oi, index_px, fwd "
                      "FROM quotes_v WHERE inst_id=?", (COIN,)).fetchone()
    assert row[:5] == (t, COIN, "BTC-USD", 75000.0, "C")
    assert [round(v, 7) for v in row[5:8]] == [0.0115, 0.012, 0.0117235]
    assert row[8:] == (320, 0.5609, -0.4488, 12, 65644.9, 65662.83)
    con.close()
    store.close()


def test_missing_numbers_stay_null_and_empty_contracts_are_skipped(tmp_path):
    store = OptionStore(tmp_path)
    t = ms(2026, 11, 14, 12)
    n = store.record(snap(t,
                          contract(COIN, mark=0.0021, iv=0.42),                   # no bid or ask
                          contract("BTC-USD-261127-76000-C"),                     # nothing at all
                          contract("BTC-USD-261127-77000-C", mark=0.001, tick=None)))  # can't be put in ticks
    assert n == 1
    assert store.read(COIN, 0, t) == [{"t": t, "mark": 0.0021, "bid": None, "ask": None, "iv": 0.42}]
    assert store.read("BTC-USD-261127-76000-C", 0, t) == []
    assert store.read("BTC-USD-000000-1-C", 0, t) == []
    store.close()


def test_same_snapshot_twice_is_stored_once(tmp_path):
    store = OptionStore(tmp_path)
    t = ms(2026, 11, 14, 12)
    s = snap(t, contract(COIN, bid=0.0115, mark=0.0117))
    store.record(s)
    store.record(s)
    assert len(store.read(COIN, 0, t)) == 1
    store.close()


def test_month_roll_over_writes_a_new_file_and_reads_span_both(tmp_path):
    store = OptionStore(tmp_path)
    nov, dec = ms(2026, 11, 30, 23, 59, 50), ms(2026, 12, 1, 0, 0, 10)
    store.record(snap(nov, contract(COIN, bid=0.0110, mark=0.0111)))
    store.record(snap(dec, contract(COIN, bid=0.0120, mark=0.0121)))
    assert (month_of(nov), month_of(dec)) == ("2026-11", "2026-12")
    assert store.months() == ["2026-11", "2026-12"]
    assert (tmp_path / "2026-11.sqlite").exists() and (tmp_path / "2026-12.sqlite").exists()
    assert [(r["t"], r["bid"]) for r in store.read(COIN, 0, dec)] == [(nov, 0.011), (dec, 0.012)]
    assert [r["t"] for r in store.read(COIN, dec, dec)] == [dec]
    # Going back to the earlier month still works (e.g. after a clock correction).
    store.record(snap(nov + 1000, contract(COIN, bid=0.0112, mark=0.0111)))
    assert [r["bid"] for r in store.read(COIN, 0, dec)] == [0.011, 0.0112, 0.012]
    store.close()


def test_a_changed_tick_gets_its_own_contract_row(tmp_path):
    store = OptionStore(tmp_path)
    t = ms(2026, 11, 14, 12)
    store.record(snap(t, contract(COIN, bid=0.0115, tick=0.0001)))
    store.record(snap(t + 10_000, contract(COIN, bid=0.0115, tick=0.0005)))
    assert [r["bid"] for r in store.read(COIN, 0, t + 10_000)] == [0.0115, 0.0115]
    store.close()


def test_bucketed_read_returns_the_last_sample_of_each_bucket(tmp_path):
    store = OptionStore(tmp_path)
    t = ms(2026, 11, 14, 12)
    for i in range(12):                                   # every 10 s for two minutes
        store.record(snap(t + i * 10_000, contract(COIN, bid=0.0100 + i * 0.0001, mark=0.01)))
    rows = store.read(COIN, 0, t + 200_000, bucket_ms=60_000)
    assert [(r["t"], r["bid"]) for r in rows] == [(t + 50_000, 0.0105), (t + 110_000, 0.0111)]
    assert len(store.read(COIN, t + 30_000, t + 200_000)) == 9
    store.close()


def test_thinning_keeps_the_last_sample_of_each_minute_once_a_day_old(tmp_path):
    store = OptionStore(tmp_path)
    now = ms(2026, 11, 14, 12, 0, 30)
    old, fresh = ms(2026, 11, 13, 9), ms(2026, 11, 14, 11)
    other = "BTC-USD-261127-76000-P"
    for base in (old, fresh):
        for i in range(18):                               # every 10 s for three minutes
            ts = base + i * 10_000
            store.record(snap(ts, contract(COIN, bid=0.0100 + i * 0.0001, mark=0.01, fwd=65000.0),
                              contract(other, bid=0.02, mark=0.02, fwd=65000.0)))
        for i in range(3):                                # the other family: already one a minute
            store.record(snap(base + i * 60_000 + 7, contract(UM, bid=595.0, mark=596.0)))

    removed = store.thin(now)
    assert removed == 2 * 15                              # 15 of 18 old snapshots, two contracts each
    kept = store.read(COIN, old, old + 180_000)
    assert [(r["t"] - old, r["bid"]) for r in kept] == [(50_000, 0.0105), (110_000, 0.0111), (170_000, 0.0117)]
    assert len(store.read(other, old, old + 180_000)) == 3
    assert len(store.read(UM, old, old + 180_000)) == 3   # nothing to thin there
    assert len(store.read(COIN, fresh, fresh + 180_000)) == 18   # younger than a day: untouched

    con = sqlite3.connect(tmp_path / "2026-11.sqlite")
    assert con.execute("SELECT count(*) FROM snaps WHERE family='BTC-USD' AND ts<?", (fresh,)).fetchone()[0] == 3
    assert con.execute("SELECT count(*) FROM fwds WHERE ts<?", (fresh,)).fetchone()[0] == 3
    con.close()

    assert store.thin(now) == 0                           # already done up to here
    # A day later the "fresh" samples are old enough.
    assert store.thin(now + DAY_MS) == 2 * 15
    assert len(store.read(COIN, fresh, fresh + 180_000)) == 3
    store.close()


def test_thinning_reaches_last_months_file(tmp_path):
    store = OptionStore(tmp_path)
    old = ms(2026, 11, 30, 23, 50)
    for i in range(6):
        store.record(snap(old + i * 10_000, contract(COIN, bid=0.01, mark=0.01)))
    now = ms(2026, 12, 2, 0, 0)
    store.record(snap(now - 5_000, contract(COIN, bid=0.01, mark=0.01)))   # the writer is on December now
    assert store.thin(now) == 5
    assert [r["t"] - old for r in store.read(COIN, old, old + 60_000)] == [50_000]
    assert store.record(snap(now, contract(COIN, bid=0.01, mark=0.01))) == 1   # still writable afterwards
    store.close()


def test_contract_lookup_for_a_contract_that_no_longer_trades(tmp_path):
    store = OptionStore(tmp_path)
    store.record(snap(ms(2026, 10, 30, 12), contract("BTC-USD-261031-72000-C", bid=0.01, mark=0.01)))
    store.record(snap(ms(2026, 11, 14, 12), contract(COIN, bid=0.01, mark=0.01)))
    known = store.contract("BTC-USD-261031-72000-C")
    assert (known["family"], known["strike"], known["type"], known["tick"]) == ("BTC-USD", 72000.0, "C", 0.0001)
    assert store.contract("BTC-USD-250101-1-C") is None
    store.close()
    assert OptionStore(tmp_path / "nothing-here").months() == []
