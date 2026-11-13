"""Read-only trading views: live checks, slider sizing and the account history tabs.

OKX is faked in memory (see test_trading.FakeOKX): nothing here talks to the real exchange.
"""

import json

from test_trading import ROOT, FakeOKX, make, run

from btcliq.trader import match_rate, norm_fill, norm_funding, norm_order, unit_math

NOW_MS = 1_763_000_000_000


class HistOKX(FakeOKX):
    """FakeOKX plus the endpoints the ticket's slider and the history tabs read."""

    def __init__(self):
        super().__init__()
        self.max_size = {"maxBuy": "35", "maxSell": "40"}
        self.hist: dict[tuple, list] = {}
        self.rates: dict[str, list] = {}
        self.calls: list[tuple[str, dict]] = []

    async def public(self, path, **p):
        if path == "/api/v5/public/funding-rate-history":
            return self.rates.get(p["instId"], [])
        return await super().public(path, **p)

    async def get(self, path, **p):
        self.calls.append((path, p))
        if path == "/api/v5/account/max-size":
            return [{"instId": p["instId"], **self.max_size}]
        if path in ("/api/v5/trade/fills-history", "/api/v5/trade/orders-history",
                    "/api/v5/trade/orders-history-archive"):
            return self.hist.get((path, p.get("instType")), [])
        if path in ("/api/v5/account/bills", "/api/v5/account/bills-archive"):
            return self.hist.get((path, None), [])
        return await super().get(path, **p)


def fill(bill, inst, t, side, px, sz, ts, **kw):
    return {"billId": bill, "tradeId": "t" + bill, "instId": inst, "instType": t, "side": side, "fillPx": str(px),
            "fillSz": str(sz), "ts": str(ts), "fee": kw.get("fee", "-0.5"), "feeCcy": kw.get("feeCcy", "USDT"),
            "fillPnl": kw.get("pnl", "0"), "fillIdxPx": "100000", "fillPxUsd": kw.get("pxUsd", ""),
            "ordId": kw.get("ordId", "o" + bill), "execType": kw.get("execType", "T"), "subType": kw.get("subType", "1")}


# ---------- slider sizing ----------

def test_sizing_perp_shows_okx_max_and_the_tightest_rail(tmp_path):
    tr, fake = make(tmp_path, fake=HistOKX())
    s = run(tr.sizing("BTC-USDT-SWAP", "buy", "100000"))
    assert s["max"] == 35.0 and s["unit"] == "contracts"
    # $2,000 per-order cap at 0.01 BTC x $100,000 a contract = 2 contracts, tighter than the $5,000 position cap
    assert s["cap"] == 2.0 and s["cap_rule"] == "order.max_notional_usd"
    assert "$2,000 per-order cap" == s["cap_label"] and not s["cap_ask"]
    assert ("/api/v5/account/max-size", {"instId": "BTC-USDT-SWAP", "tdMode": "cross", "px": "100000.0"}) in fake.calls


def test_sizing_option_counts_underlying_value_and_premium(tmp_path):
    tr, _ = make(tmp_path, fake=HistOKX())
    s = run(tr.sizing("BTC-USD-261127-75000-C", "buy", "0.031"))
    # underlying: 0.01 BTC x $100,000 = $1,000 a contract -> $2,000 cap = 2; premium $31 a contract -> $500 cap = 16
    assert s["cap"] == 2.0 and s["cap_rule"] == "order.max_notional_usd"


def test_sizing_option_ask_rule_is_marked(tmp_path):
    base = (ROOT / "trading.example.toml").read_text()
    base = base.replace('max_notional_usd = 2000', 'max_notional_usd = 100000').replace(
        'ask = []', 'ask = ["option.max_contracts"]')
    tr, _ = make(tmp_path, toml=base, fake=HistOKX())
    s = run(tr.sizing("BTC-USD-261127-75000-C", "buy", "0.031"))
    assert s["cap"] == 5.0 and s["cap_rule"] == "option.max_contracts" and s["cap_ask"]


def test_sizing_reduce_only_is_capped_by_the_position(tmp_path):
    fake = HistOKX()
    fake.positions["BTC-USDT-SWAP"] = [{"posSide": "net", "pos": "3"}]
    tr, _ = make(tmp_path, fake=fake)
    s = run(tr.sizing("BTC-USDT-SWAP", "sell", "100000", reduce=True))
    assert s["max"] == 3.0 and s["closing"]
    assert not any(path == "/api/v5/account/max-size" for path, _ in fake.calls)


def test_sizing_spot_sell_uses_the_btc_balance(tmp_path):
    tr, fake = make(tmp_path, fake=HistOKX())
    fake.btc_avail = "0.25"
    s = run(tr.sizing("BTC-USDT", "sell", "99990"))
    assert s["max"] == 0.25 and s["unit"] == "BTC"


# ---------- live check ----------

def test_check_prices_the_order_without_storing_a_preview(tmp_path):
    tr, _ = make(tmp_path, fake=HistOKX())
    p = run(tr.check({"instId": "BTC-USDT-SWAP", "side": "buy", "sz": "1", "px": "100000", "ordType": "limit"}))
    assert p["numbers"]["notional_usd"] == 1000.0 and not p["blocked"]
    assert "id" not in p and not (tmp_path / "data" / "trade_previews.json").exists()


def test_check_reports_a_blocked_size(tmp_path):
    tr, _ = make(tmp_path, fake=HistOKX())
    p = run(tr.check({"instId": "BTC-USDT-SWAP", "side": "buy", "sz": "3", "px": "100000", "ordType": "limit"}))
    assert p["blocked"] and any(c["rule"] == "order.max_notional_usd" and not c["ok"] for c in p["checks"])


# ---------- history rows ----------

def test_unit_math_falls_back_to_standard_btc_sizes():
    assert unit_math("BTC-USD-260925-60000-P", "OPTION") == {"unit": "contracts", "btc_per": 0.01, "usd_per": None, "price_ccy": "BTC"}
    assert unit_math("BTC-USD-260925", "FUTURES")["usd_per"] == 100.0
    assert unit_math("BTC-USDT-260925", "FUTURES")["btc_per"] == 0.01
    assert unit_math("BTC-USDT", "SPOT")["btc_per"] == 1.0
    assert unit_math("ETH-USDT-SWAP", "SWAP")["btc_per"] is None


def test_norm_fill_values_and_fees_in_usd():
    perp = norm_fill(fill("1", "BTC-USDT-SWAP", "SWAP", "buy", 100000, 2, NOW_MS, fee="-1.2"), unit_math("BTC-USDT-SWAP", "SWAP"))
    assert perp["value_usd"] == 2000.0 and perp["fee_usd"] == -1.2 and perp["unit"] == "contracts"
    opt = norm_fill(fill("2", "BTC-USD-260925-60000-P", "OPTION", "buy", 0.03, 2, NOW_MS, fee="-0.00001", feeCcy="BTC",
                         pxUsd="3000", execType="M"), unit_math("BTC-USD-260925-60000-P", "OPTION"))
    assert opt["value_usd"] == 60.0 and opt["fee_usd"] == -1.0 and opt["maker"] and opt["price_ccy"] == "BTC"
    liq = norm_fill(fill("3", "BTC-USDT-SWAP", "SWAP", "sell", 90000, 1, NOW_MS, subType="104"), unit_math("BTC-USDT-SWAP", "SWAP"))
    assert liq["liquidation"]


def test_norm_order_and_funding_rows():
    o = norm_order({"ordId": "9", "cTime": str(NOW_MS), "instId": "BTC-USDT-SWAP", "instType": "SWAP", "ordType": "limit",
                    "side": "buy", "px": "99000", "avgPx": "", "sz": "2", "accFillSz": "0", "state": "canceled",
                    "reduceOnly": "false"}, unit_math("BTC-USDT-SWAP", "SWAP"))
    assert o["state"] == "canceled" and o["avgPx"] is None and o["filled"] == 0.0 and o["category"] == "normal"
    rates = [(NOW_MS - 8 * 3_600_000, 0.0001), (NOW_MS, 0.00012)]
    assert match_rate(rates, NOW_MS + 4000) == 0.00012 and match_rate(rates, NOW_MS + 3_600_000) is None
    f = norm_funding({"billId": "b1", "ts": str(NOW_MS + 4000), "instId": "BTC-USDT-SWAP", "sz": "50", "px": "100000",
                      "balChg": "-6", "ccy": "USDT", "subType": "173"}, unit_math("BTC-USDT-SWAP", "SWAP"), rates)
    assert f["pos_btc"] == 0.5 and f["pos_usd"] == 50000.0 and f["pay_usd"] == -6.0 and not f["income"]
    assert f["rate"] == 0.00012
    inv = norm_funding({"billId": "b2", "ts": str(NOW_MS), "instId": "BTC-USD-SWAP", "sz": "10", "px": "100000",
                        "balChg": "0.00002", "ccy": "BTC", "subType": "174"}, unit_math("BTC-USD-SWAP", "SWAP"), [])
    assert inv["pos_usd"] == 1000.0 and inv["pay_usd"] == 2.0 and inv["income"] and inv["rate"] is None


def test_history_fills_merges_recent_and_archive_without_duplicates(tmp_path, monkeypatch):
    monkeypatch.setattr("btcliq.trader.time.time", lambda: NOW_MS / 1000)
    fake = HistOKX()
    a = fill("10", "BTC-USDT-SWAP", "SWAP", "buy", 100000, 1, NOW_MS - 1000)
    b = fill("11", "BTC-USD-260925-60000-P", "OPTION", "sell", 0.02, 1, NOW_MS - 86_400_000, pxUsd="2000")
    old = fill("12", "BTC-USDT-SWAP", "SWAP", "sell", 90000, 1, NOW_MS - 100 * 86_400_000)
    fake.fills = [a]
    fake.hist[("/api/v5/trade/fills-history", "SWAP")] = [a, old]
    fake.hist[("/api/v5/trade/fills-history", "OPTION")] = [b]
    tr, _ = make(tmp_path, fake=fake)
    h = run(tr.history("fills", 90))
    assert [r["id"] for r in h["rows"]] == ["10", "11"]          # newest first, duplicate and too-old fills dropped
    assert h["rows"][1]["value_usd"] == 20.0 and not h["truncated"] and h["errors"] == {}
    assert all(p.get("begin") == str(NOW_MS - 90 * 86_400_000) for path, p in fake.calls if "fills" in path)


def test_history_orders_dedupes_the_7_day_and_archive_lists(tmp_path, monkeypatch):
    monkeypatch.setattr("btcliq.trader.time.time", lambda: NOW_MS / 1000)
    fake = HistOKX()
    o = {"ordId": "5", "cTime": str(NOW_MS - 5000), "instId": "BTC-USDT-SWAP", "instType": "SWAP", "ordType": "limit",
         "side": "sell", "px": "101000", "avgPx": "101000", "sz": "1", "accFillSz": "1", "state": "filled"}
    fake.hist[("/api/v5/trade/orders-history", "SWAP")] = [o]
    fake.hist[("/api/v5/trade/orders-history-archive", "SWAP")] = [o]
    tr, _ = make(tmp_path, fake=fake)
    h = run(tr.history("orders", 90))
    assert len(h["rows"]) == 1 and h["rows"][0]["state"] == "filled" and h["rows"][0]["avgPx"] == 101000.0


def test_history_funding_keeps_only_funding_bills_and_finds_the_rate(tmp_path, monkeypatch):
    monkeypatch.setattr("btcliq.trader.time.time", lambda: NOW_MS / 1000)
    fake = HistOKX()
    fund = {"billId": "f1", "ts": str(NOW_MS - 3000), "instId": "BTC-USDT-SWAP", "sz": "50", "px": "100000",
            "balChg": "-5", "ccy": "USDT", "subType": "173", "type": "8"}
    other = {"billId": "x", "ts": str(NOW_MS - 3000), "instId": "BTC-USDT-SWAP", "subType": "1", "type": "2"}
    fake.hist[("/api/v5/account/bills", None)] = [fund, other]
    fake.hist[("/api/v5/account/bills-archive", None)] = [fund]
    fake.rates["BTC-USDT-SWAP"] = [{"fundingTime": str(NOW_MS - 5000), "fundingRate": "0.0002", "realizedRate": "0.0001"}]
    tr, _ = make(tmp_path, fake=fake)
    h = run(tr.history("funding", 90))
    assert [r["id"] for r in h["rows"]] == ["f1"] and h["rows"][0]["rate"] == 0.0001
    assert any(p.get("type") == "8" and p.get("instType") == "SWAP" for path, p in fake.calls if "bills" in path)


def test_history_reports_errors_per_list(tmp_path, monkeypatch):
    monkeypatch.setattr("btcliq.trader.time.time", lambda: NOW_MS / 1000)

    class Flaky(HistOKX):
        async def get(self, path, **p):
            if path == "/api/v5/trade/fills-history" and p.get("instType") == "OPTION":
                from btcliq.okx_private import OKXError
                raise OKXError("50011", "Too Many Requests")
            return await super().get(path, **p)

    tr, _ = make(tmp_path, fake=Flaky())
    h = run(tr.history("fills", 30))
    assert "OPTION" in h["errors"] and h["rows"] == []


def test_history_rejects_unknown_kinds(tmp_path):
    import pytest
    from btcliq.trader import OrderInputError
    tr, _ = make(tmp_path, fake=HistOKX())
    with pytest.raises(OrderInputError):
        run(tr.history("deposits"))


def test_account_positions_carry_usd_pnl_and_greeks(tmp_path):
    fake = HistOKX()

    async def get(path, **p):
        if path == "/api/v5/account/positions" and not p:
            return [{"instId": "BTC-USD-261127-75000-C", "instType": "OPTION", "pos": "2", "posSide": "net", "avgPx": "0.03",
                     "markPx": "0.031", "upl": "0.0002", "uplRatio": "0.03", "ccy": "BTC", "usdPx": "100000",
                     "idxPx": "100000", "bePx": "78100", "deltaPA": "0.004", "thetaBS": "-18.5", "cTime": str(NOW_MS)}]
        if path == "/api/v5/account/balance":
            return [{"totalEq": "1000", "details": []}]
        if path == "/api/v5/trade/fills":
            return []
        return await HistOKX.get(fake, path, **p)
    fake.get = get
    tr, _ = make(tmp_path, fake=fake)
    a = run(tr.account())
    p = a["positions"][0]
    assert p["upl_usd"] == 20.0 and p["bePx"] == 78100.0 and p["thetaBS"] == -18.5 and p["price_ccy"] == "BTC"
    assert json.dumps(a)          # the browser gets plain JSON
