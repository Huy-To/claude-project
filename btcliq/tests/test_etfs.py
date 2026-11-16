import asyncio
import json
import time
from datetime import datetime, timezone

import pytest

from btcliq import etfs
from btcliq.etfs import EtfFlows, parse_bitbo, parse_bitwise, parse_cmc_flow, parse_cmc_funds, tx_flow

A1, A2, A3 = "bc1qzculrus53vszftujqztm4c8lpe00sutplajkr8", "bc1qr47xza5284dd476wtvaljm66rx2jdehzmfdra5", "bc1qs3njm2cnmj4s2nuk444vm9cfyxs8ktzqzsx2qh"
PRIME, DEPOSIT, FEES = "bc1qh9xvgsqh648qexternalprimewalletaaaaaaa0", "38ACfxHJwKnKFW2EaaaaaaaaaaaaaaaaaZ", "3J7cUjBZxvGRCwaaaaaaaaaaaaaaaaaaaZ"
BTC = etfs.SATS


def ms(y, m, d, h=0):
    return int(datetime(y, m, d, h, tzinfo=timezone.utc).timestamp() * 1000)


def bitwise_page(addresses, updated="2026-11-15T03:15:04.039Z"):
    data = {"props": {"pageProps": {"fundData": {"data": {
        "fundDetails": {"sharesOutstanding": 67650000},
        "holdings": {"basket": [{"companyName": "BITCOIN", "shares": 1300.5}], "asOfDate": "2026-11-13"},
        "wallets": {"updatedAt": updated,
                    "walletBalances": [{"active": True, "address": a, "balance": 1.0} for a in addresses]}}}}}}
    return f'<html><script id="__NEXT_DATA__" type="application/json">{json.dumps(data)}</script></html>'


def tx(txid, ins, outs, block_time=None, fee=1500):
    return {"txid": txid, "fee": fee,
            "vin": [{"prevout": {"scriptpubkey_address": a, "value": v}} for a, v in ins],
            "vout": [{"scriptpubkey_address": a, "value": v} for a, v in outs],
            "status": {"confirmed": block_time is not None, "block_time": block_time}}


# ---------- parsers ----------

def test_parse_bitwise_reads_the_wallet_list():
    b = parse_bitwise(bitwise_page([A1, A2, "not-an-address"]))
    assert [w[0] for w in b["wallets"]] == [A1, A2]
    assert b["holdings_btc"] == 1300.5 and b["shares"] == 67650000
    assert b["updated"] == ms(2026, 11, 15, 3) + 15 * 60_000 + 4039
    with pytest.raises(ValueError):
        parse_bitwise("<html>no data</html>")


def test_bitbo_dates_match_what_its_table_shows():
    # checked against the live page: the point stamped Nov 12 is the table's Nov 10 row, Nov 11 -> Nov 9
    assert etfs._bitbo_date(ms(2026, 11, 12)) == "2026-11-10"
    assert etfs._bitbo_date(ms(2026, 11, 11)) == "2026-11-09"
    # Saturday, Sunday and Monday snapshots all land on Friday
    assert {etfs._bitbo_date(ms(2026, 11, d)) for d in (8, 9, 10)} == {"2026-11-06"}
    assert etfs._bitbo_date(ms(2026, 11, 7)) == "2026-11-05"


BITBO = """
<script>
const historyBtc = [ [ getPreviousBusinessDay(1794355200000), truncate(-99999, 2) ] ];
aum_etf_blackrock = [ [ getPreviousBusinessDay(1794355200000), truncate(-2852 * 65081.61, 2) ] ,
                      [ getPreviousBusinessDay(1794441600000), truncate(-4686 * 65081.61, 2) ] ];
aum_etf_ark = [ ];
aum_etf_fidelity = [ [ getPreviousBusinessDay(1794441600000), truncate(-500 * 65081.61, 2) ] ];
</script>
<table class="stats-table"><tbody>
<tr><th><span>Date</span></th><th><span>IBIT</span></th><th><span>FBTC</span></th><th><span>Totals</span></th></tr>
<tr><td>Nov 10, 2026</td><td>-305.0</td><td>-32.5</td><td>-337.5</td></tr>
<tr><td>Nov 09, 2026</td><td>-185.6</td><td>0.0</td><td>-185.6</td></tr>
<tr><td>Total</td><td>-1,525.2</td><td>34.3</td><td>-1,490.9</td></tr>
</tbody></table>
"""


def test_parse_bitbo_turns_the_table_back_into_btc():
    b = parse_bitbo(BITBO)
    assert b["tickers"] == ["IBIT", "FBTC"] and b["price_used"] == 65081.61
    assert b["by_fund"] == {"2026-11-10": {"IBIT": -4686, "FBTC": -499}, "2026-11-09": {"IBIT": -2852, "FBTC": 0}}
    assert b["issuer_btc"] == {"BlackRock": [[ms(2026, 11, 9), -2852.0], [ms(2026, 11, 10), -4686.0]],
                               "Fidelity": [[ms(2026, 11, 10), -500.0]]}
    # the total adds the issuers up; the page's own historyBtc is ignored
    assert b["total_btc"] == [[ms(2026, 11, 9), -2852.0], [ms(2026, 11, 10), -5186.0]]
    with pytest.raises(ValueError):
        parse_bitbo("<html></html>")


def test_parse_cmc():
    funds = parse_cmc_funds({"data": [
        {"ticker": "IBIT", "type": "Spot", "country": "us", "name": "iShares Bitcoin Trust", "aum": 6e10, "price": 38.9,
         "volume": 1e9, "premiumDiscount": 0.02, "netFee": 0.25, "updateTime": "1794714470329"},
        {"ticker": "BITO", "type": "Futures", "country": "us", "aum": 1e9},
        {"ticker": "CHINAAMC", "type": "Spot", "country": "hk", "aum": None}]})
    assert list(funds) == ["IBIT"] and funds["IBIT"]["aum"] == 6e10
    flow = parse_cmc_flow({"data": {"points": [{"value": -185400000, "timestamp": str(ms(2026, 11, 13))},
                                               {"value": None, "timestamp": str(ms(2026, 11, 14))}]}})
    assert flow == {"2026-11-13": -185400000.0}


# ---------- one transaction ----------

def test_tx_flow_inflow_outflow_internal_and_dust():
    mine = {A1, A2}
    f = tx_flow(tx("in", [(PRIME, 190 * BTC), (PRIME, 1000)], [(A1, 190 * BTC), (FEES, 546)], 1794000000), mine)
    assert f["sats"] == 190 * BTC and f["cp"] == PRIME and f["cp_n"] == 1 and f["confirmed"]
    # a redemption: part leaves, the change stays in the fund
    f = tx_flow(tx("out", [(A1, 490 * BTC)], [(DEPOSIT, 271 * BTC), (A2, 219 * BTC - 2000), (FEES, 546)]), mine)
    assert f["sats"] == -271 * BTC - 2000 and f["cp"] == DEPOSIT and f["cp_n"] == 2 and not f["confirmed"]
    # wallet to wallet inside the fund, and a fee top-up, are not flows
    assert tx_flow(tx("move", [(A1, 900 * BTC)], [(A2, 900 * BTC - 2000), (FEES, 546)]), mine) is None
    assert tx_flow(tx("dust", [(FEES, 30000)], [(A1, 20000), (FEES, 8000)]), mine) is None
    assert tx_flow(tx("other", [(PRIME, BTC)], [(DEPOSIT, BTC)]), mine) is None


# ---------- the tracker ----------

class Fake(EtfFlows):
    """EtfFlows with the network replaced: `page` is the Bitwise page, `chain` maps address -> transactions."""

    def __init__(self, data_dir, addresses, chain):
        super().__init__(None, data_dir, price_at=lambda t: 70_000.0)
        self.page, self.chain, self.calls = bitwise_page(addresses), chain, []

    async def _fetch(self, url, params=None, as_json=True, timeout=45):
        self.calls.append(url)
        if url == etfs.BITWISE_URL:
            return self.page
        if url == etfs.BALANCE_API:
            out = {}
            for a in params["active"].split("|"):
                txs = self.chain.get(a, [])
                bal = sum(v for t in txs for o, v in ((x["scriptpubkey_address"], x["value"]) for x in t["vout"]) if o == a) \
                    - sum(x["prevout"]["value"] for t in txs for x in t["vin"] if x["prevout"]["scriptpubkey_address"] == a)
                out[a] = {"final_balance": bal, "n_tx": len(txs)}
            return out
        raise AssertionError(url)

    async def _esplora(self, path):
        return list(self.chain.get(path.split("/")[2], []))


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    async def instant(_):
        return None
    monkeypatch.setattr(etfs.asyncio, "sleep", instant)


def test_tracks_flows_and_corrects_a_move_to_a_wallet_not_yet_listed(tmp_path):
    now = int(time.time())
    deposit = tx("t-in", [(PRIME, 500 * BTC)], [(A1, 500 * BTC)], now - 3 * 86400)
    redeem = tx("t-out", [(A1, 500 * BTC)], [(DEPOSIT, 100 * BTC), (A1, 400 * BTC - 1000)], now - 7200)
    rotate = tx("t-move", [(A1, 400 * BTC - 1000)], [(A3, 400 * BTC - 3000)], now - 600)     # A3 isn't on the list yet
    old = tx("t-old", [(PRIME, 9 * BTC)], [(A2, 9 * BTC)], now - 90 * 86400)                # before the window
    chain = {A1: [rotate, redeem, deposit], A2: [old], A3: [rotate]}
    e = Fake(tmp_path, [A1, A2], chain)
    stamp = lambda secs_ago: datetime.fromtimestamp(now - secs_ago, timezone.utc).isoformat()
    e.page = bitwise_page([A1, A2], updated=stamp(1800))                    # published before the move to A3

    async def run():
        await e.refresh_wallets()
        assert await e.sync_chain() is True
        s = e.snapshot()
        bitb = next(f for f in s["funds"] if f["ticker"] == "BITB")
        assert bitb["wallets"] == "issuer" and bitb["addresses"] == 2 and bitb["syncing"] == 0
        assert bitb["tracked_btc"] == 9.0 and bitb["holdings_btc"] == 1300.5
        moves = {ev["txid"]: ev for ev in s["events"]}
        assert set(moves) == {"t-in", "t-out", "t-move"}                    # t-old is outside the 30 days
        assert round(moves["t-move"]["btc"]) == -400
        assert moves["t-in"]["usd"] == 500 * 70_000.0 and moves["t-in"]["cp"] == PRIME
        # only the move made after the issuer last published its list is in doubt
        assert [t for t, ev in moves.items() if ev["unsettled"]] == ["t-move"]
        assert await e.sync_chain() is False                                # nothing changed, nothing re-read

        # Bitwise republishes with the new wallet: the "outflow" to it becomes an internal move
        e.page = bitwise_page([A1, A2, A3], updated=stamp(0))
        await e.refresh_wallets()
        assert await e.sync_chain() is True
        s = e.snapshot()
        assert {ev["txid"] for ev in s["events"]} == {"t-in", "t-out"}
        bitb = next(f for f in s["funds"] if f["ticker"] == "BITB")
        assert round(bitb["onchain"]["d7"]["net"], 3) == 400.0 and bitb["onchain"]["d7"]["n"] == 2
        assert round(bitb["onchain"]["h24"]["out"], 3) == 100.0 and bitb["onchain"]["h24"]["in"] == 0
        assert s["totals"]["tracked"] == ["BITB"] and "IBIT" in s["totals"]["untracked"]
        assert s["onchain_daily"]["BITB"][-1][2] == pytest.approx(100.0, abs=1e-3)
        assert any("BITB on-chain net" in line for line in e.context())
        e._save()

    asyncio.run(run())
    # a restart reads the same state back, with nothing left to re-read
    again = Fake(tmp_path, [A1, A2, A3], chain)
    assert set(again.events) == {"BITB:t-in", "BITB:t-out"}
    assert not any(w["dirty"] for w in again.wallets["BITB"].values())


def test_an_outflow_after_the_issuers_last_list_is_marked_unsettled(tmp_path):
    now = int(time.time())
    e = Fake(tmp_path, [A1], {A1: [tx("t-out", [(A1, 50 * BTC)], [(DEPOSIT, 50 * BTC - 500)], now - 60),
                                    tx("t-in", [(PRIME, 50 * BTC)], [(A1, 50 * BTC)], now - 3600)]})
    e.page = bitwise_page([A1], updated=datetime.fromtimestamp(now - 1800, timezone.utc).isoformat())

    async def run():
        await e.refresh_wallets()
        await e.sync_chain()

    asyncio.run(run())
    by = {ev["txid"]: ev for ev in e.snapshot()["events"]}
    assert by["t-out"]["unsettled"] is True and by["t-in"]["unsettled"] is False


def test_your_own_wallet_file_adds_and_removes_funds(tmp_path):
    now = int(time.time())
    e = Fake(tmp_path, [A1], {A2: [tx("t-ibit", [(PRIME, 300 * BTC)], [(A2, 300 * BTC)], now - 100)]})
    e.user_file.write_text(json.dumps({"ibit": {"addresses": [A2, A1, "junk"], "source": "Arkham export"}}))

    async def run():
        await e.refresh_wallets()
        await e.sync_chain()

    asyncio.run(run())
    assert list(e.wallets["IBIT"]) == [A2]                      # A1 already belongs to BITB; junk is refused
    assert e.lists["IBIT"]["source"] == "Arkham export" and e.lists["IBIT"]["rejected"] == 1
    s = e.snapshot()
    ibit = next(f for f in s["funds"] if f["ticker"] == "IBIT")
    assert ibit["wallets"] == "file" and ibit["tracked_btc"] == 300.0 and ibit["onchain"]["h24"]["net"] == 300.0
    assert e.wallet_list("ibit")["wallets"][0]["address"] == A2

    e.user_file.unlink()
    asyncio.run(e.refresh_wallets())
    assert "IBIT" not in e.wallets and "IBIT" not in e.lists


def test_published_numbers_build_up_and_survive_a_failed_refresh(tmp_path):
    e = Fake(tmp_path, [A1], {})
    docs = {f"{etfs.CMC_API}/list": {"data": [{"ticker": "IBIT", "type": "Spot", "country": "us", "aum": 7e10}]},
            f"{etfs.CMC_API}/overview/netflow/chart": {"data": {"points": [{"value": -1e8, "timestamp": str(ms(2026, 11, 12))}]}},
            etfs.BITBO_URL: BITBO}

    async def fetch(url, params=None, as_json=True, timeout=45):
        if isinstance(docs[url], Exception):
            raise docs[url]
        return docs[url]

    e._fetch = fetch
    asyncio.run(e.refresh_published())
    docs[f"{etfs.CMC_API}/overview/netflow/chart"] = {"data": {"points": [{"value": -2e8, "timestamp": str(ms(2026, 11, 13))}]}}
    docs[etfs.BITBO_URL] = RuntimeError("blocked")
    asyncio.run(e.refresh_published())
    s = e.snapshot()
    assert [p[1] for p in s["published"]["total_usd"]] == [-1e8, -2e8]              # both days kept
    assert s["totals"]["published_usd_last"] == {"date": "2026-11-13", "usd": -2e8}
    assert s["sources"]["bitbo"]["ok"] is False and "blocked" in s["sources"]["bitbo"]["error"]
    ibit = next(f for f in s["funds"] if f["ticker"] == "IBIT")
    assert ibit["published_last"] == {"date": "2026-11-10", "btc": -4686} and ibit["published_5d"] == -7538
    fbtc = next(f for f in s["funds"] if f["ticker"] == "FBTC")
    assert fbtc["published_last"] == {"date": "2026-11-10", "btc": -499}            # a zero day is not "the last move"
    assert ibit["holdings_estimated"] and round(ibit["holdings_btc"]) == 1_000_000  # assets / price
    assert "IBIT: -4,686 BTC on 2026-11-10" in "\n".join(e.context())


def test_a_long_list_is_swept_in_turns_and_starts_from_now(tmp_path, monkeypatch):
    monkeypatch.setattr(etfs, "BATCH", 2)
    monkeypatch.setattr(etfs, "BALANCE_BATCHES", 2)
    monkeypatch.setattr(etfs, "BIG_LIST", 3)
    now = int(time.time())
    slots = ["bc1q" + "x" * 36 + c for c in ("ac", "ad", "ae", "af", "ag", "ah")]
    chain = {a: [tx(f"fill-{i}", [(PRIME, 300 * BTC)], [(a, 300 * BTC)], now - 5 * 86400)] for i, a in enumerate(slots)}
    e = Fake(tmp_path, [A1], chain)
    e.user_file.write_text(json.dumps({"IBIT": {"addresses": slots}}))

    async def cycle(n):
        for _ in range(n):
            await e.sync_chain()

    asyncio.run(e.refresh_wallets())
    e.wallets["BITB"][A1]["dirty"] = False
    e.calls.clear()
    asyncio.run(cycle(1))
    assert e.calls.count(etfs.BALANCE_API) == 2                         # 7 addresses, 4 looked at this cycle
    asyncio.run(cycle(3))
    assert all(w["bal"] == 300 * BTC and not w["dirty"] for w in e.wallets["IBIT"].values())
    assert not e.events                                                 # the fills five days ago were not back-filled

    chain[slots[0]].insert(0, tx("redeem", [(slots[0], 300 * BTC)], [(DEPOSIT, 300 * BTC - 2000)], now - 60))
    asyncio.run(cycle(4))
    # once an address moves, its own last 30 days are read: the redemption and that address's earlier fill
    assert sorted(round(ev["btc"]) for ev in e.events.values()) == [-300, 300]
    assert {ev["txid"] for ev in e.events.values()} == {"redeem", "fill-0"}


def test_a_redemption_address_catches_outflows_from_wallets_not_on_the_list(tmp_path):
    now = int(time.time())
    gate, known, unknown = "36YZXcTVLPdyapYuqXdJEt46oMVB2NrzVv", "bc1q" + "x" * 36 + "ac", "bc1q" + "x" * 36 + "ad"
    pay = tx("pay", [(unknown, 300 * BTC + 10_000)], [(gate, 300 * BTC), (FEES, 10_000)], now - 3600)
    sweep = tx("sweep", [(gate, 300 * BTC)], [(PRIME, 300 * BTC - 5000)], now - 1800)
    two = tx("two", [(PRIME, 5 * BTC), (A2, 5 * BTC)], [(gate, 10 * BTC)], now - 900)       # not one wallet paying in
    e = Fake(tmp_path, [A1], {gate: [two, sweep, pay], unknown: [pay]})
    e.user_file.write_text(json.dumps({"IBIT": {"addresses": [known], "redeems_to": [gate, "junk"]}}))

    async def run():
        await e.refresh_wallets()
        await e.sync_chain()
        await e.sync_chain()

    asyncio.run(run())
    assert e.lists["IBIT"]["redeems_to"] == [gate]
    assert [(ev["txid"], round(ev["btc"]), ev["cp"]) for ev in e.events.values()] == [("pay", -300, gate)]
    assert e.wallets["IBIT"][unknown]["src"] == "seen" and e.wallets["IBIT"][unknown]["bal"] == -(300 * BTC + 10_000)
    assert gate not in e.wallets["IBIT"]

    e.user_file.write_text(json.dumps({"IBIT": {"addresses": [known, unknown]}}))            # later it goes on your list
    asyncio.run(e.refresh_wallets())
    assert e.wallets["IBIT"][unknown]["src"] == "file" and e.gateways == {}


def test_a_fund_can_adopt_the_fresh_multisig_addresses_its_own_sweeps_create(tmp_path):
    now = int(time.time())
    intake, cold1, cold2 = "3Kix5jApgaEs7AxF4NQoXDspJiGmk8ST7b", "bc1q" + "x" * 56 + "ac", "bc1q" + "x" * 56 + "ad"
    create = tx("create", [(PRIME, 500 * BTC)], [(intake, 500 * BTC)], now - 7200)
    sweep = tx("sweep", [(intake, 500 * BTC)], [(cold1, 250 * BTC), (cold2, 250 * BTC - 4000)], now - 3600)
    redeem = tx("redeem", [(cold1, 250 * BTC)], [(DEPOSIT, 100 * BTC), (cold2, 150 * BTC - 3000)], now - 600)
    e = Fake(tmp_path, [A1], {intake: [sweep, create], cold1: [redeem, sweep], cold2: [redeem, sweep]})
    e.user_file.write_text(json.dumps({"FBTC": {"addresses": [intake], "adopts": "p2wsh"}}))

    async def run():
        await e.refresh_wallets()
        for _ in range(3):
            await e.sync_chain()

    asyncio.run(run())
    assert {a: w["src"] for a, w in e.wallets["FBTC"].items()} == {intake: "file", cold1: "seen", cold2: "seen"}
    flows = {ev["txid"]: round(ev["btc"]) for ev in e.events.values()}
    assert flows == {"create": 500, "redeem": -100}                      # the sweep into cold storage is not an outflow
