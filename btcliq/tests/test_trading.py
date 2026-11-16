"""Trading core tests. OKX is faked in memory: nothing here talks to the real exchange."""

import asyncio
import base64
import hashlib
import hmac
import json
import time
from pathlib import Path

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from btcliq import rails as R
from btcliq import trade_routes
from btcliq.okx_private import OKXError, iso_ts, sign
from btcliq.trader import OrderInputError, RailsBlocked, Trader, inst_type_of

ROOT = Path(__file__).resolve().parents[1]

INSTRUMENTS = {
    "BTC-USD-261127-75000-C": {"instType": "OPTION", "instId": "BTC-USD-261127-75000-C", "instFamily": "BTC-USD",
                               "ctVal": "1", "ctMult": "0.01", "ctValCcy": "BTC", "ctType": "inverse", "settleCcy": "BTC",
                               "tickSz": "0.0001", "lotSz": "1", "minSz": "1", "maxLmtSz": "1000000", "maxMktSz": "5000", "state": "live"},
    "BTC-USD_UM-261127-70000-P": {"instType": "OPTION", "instId": "BTC-USD_UM-261127-70000-P", "instFamily": "BTC-USD_UM",
                                  "ctVal": "1", "ctMult": "0.01", "ctValCcy": "BTC", "ctType": "linear", "settleCcy": "USD",
                                  "tickSz": "5", "lotSz": "1", "minSz": "1", "maxLmtSz": "1000000", "maxMktSz": "5000", "state": "live"},
    "BTC-USDT-SWAP": {"instType": "SWAP", "instId": "BTC-USDT-SWAP", "instFamily": "BTC-USDT", "ctVal": "0.01", "ctMult": "1",
                      "ctValCcy": "BTC", "ctType": "linear", "settleCcy": "USDT", "tickSz": "0.1", "lotSz": "0.01",
                      "minSz": "0.01", "maxLmtSz": "100000000", "maxMktSz": "35000", "state": "live"},
    "BTC-USD-SWAP": {"instType": "SWAP", "instId": "BTC-USD-SWAP", "instFamily": "BTC-USD", "ctVal": "100", "ctMult": "1",
                     "ctValCcy": "USD", "ctType": "inverse", "settleCcy": "BTC", "tickSz": "0.1", "lotSz": "0.1",
                     "minSz": "0.1", "maxLmtSz": "100000000", "maxMktSz": "49000", "state": "live"},
    "BTC-USDT": {"instType": "SPOT", "instId": "BTC-USDT", "baseCcy": "BTC", "quoteCcy": "USDT", "tickSz": "0.1",
                 "lotSz": "0.00000001", "minSz": "0.00001", "maxLmtSz": "9999999999", "maxMktSz": "1000000", "state": "live"},
}
TICKERS = {
    "BTC-USD-261127-75000-C": {"bidPx": "0.0300", "askPx": "0.0310", "last": "0.0305"},
    "BTC-USD_UM-261127-70000-P": {"bidPx": "1500", "askPx": "1560", "last": "1530"},
    "BTC-USDT-SWAP": {"bidPx": "100000.0", "askPx": "100000.1", "last": "100000"},
    "BTC-USD-SWAP": {"bidPx": "100010.0", "askPx": "100010.1", "last": "100010"},
    "BTC-USDT": {"bidPx": "99990.0", "askPx": "99990.1", "last": "99990"},
}
MARKS = {"BTC-USD-261127-75000-C": "0.0305", "BTC-USD_UM-261127-70000-P": "1530", "BTC-USDT-SWAP": "100000",
         "BTC-USD-SWAP": "100010"}


class FakeOKX:
    configured, env_label = True, "live"

    def __init__(self):
        self.positions: dict[str, list] = {}
        self.pending: list[dict] = []
        self.lever = {"BTC-USDT-SWAP": "3", "BTC-USD-SWAP": "3"}
        self.config = {"acctLv": "2", "posMode": "net_mode"}
        self.btc_avail = "0.5"
        self.sent: list[tuple[str, object]] = []
        self.fills: list[dict] = []

    async def public(self, path, **p):
        if path == "/api/v5/public/instruments":
            return [INSTRUMENTS[p["instId"]]] if p["instId"] in INSTRUMENTS else []
        if path == "/api/v5/market/ticker":
            return [TICKERS[p["instId"]]]
        if path == "/api/v5/market/index-tickers":
            return [{"idxPx": "100000"}]
        if path == "/api/v5/public/mark-price":
            return [{"markPx": MARKS[p["instId"]]}]
        raise AssertionError(path)

    async def get(self, path, **p):
        if path == "/api/v5/account/config":
            return [self.config]
        if path == "/api/v5/trade/orders-pending":
            return [o for o in self.pending if "instId" not in p or o["instId"] == p["instId"]]
        if path == "/api/v5/account/positions":
            return self.positions.get(p.get("instId"), [])
        if path == "/api/v5/account/leverage-info":
            return [{"lever": self.lever[p["instId"]]}]
        if path == "/api/v5/account/trade-fee":
            return [{"taker": "-0.0005", "takerU": "-0.0005"}]
        if path == "/api/v5/trade/fills":
            return self.fills
        if path == "/api/v5/account/balance":
            return [{"details": [{"ccy": "BTC", "availBal": self.btc_avail}]}]
        raise AssertionError(path)

    async def post(self, path, body):
        self.sent.append((path, body))
        if path == "/api/v5/trade/order":
            return [{"ordId": "777", "clOrdId": body["clOrdId"], "sCode": "0"}]
        if path == "/api/v5/trade/cancel-batch-orders":
            return [{"ordId": o["ordId"], "sCode": "0"} for o in body]
        return [{}]


def make(tmp_path, toml: str | None = None, fake: FakeOKX | None = None) -> tuple[Trader, FakeOKX]:
    cfg = tmp_path / "trading.toml"
    cfg.write_text(toml if toml is not None else (ROOT / "trading.example.toml").read_text())
    fake = fake or FakeOKX()
    return Trader(None, tmp_path / "data", client=fake, rails=R.Rails(cfg)), fake


def run(coro):
    return asyncio.run(coro)


def failed(p):
    return [c["rule"] for c in p["checks"] if not c["ok"]]


# ---------- signing ----------

def test_sign_matches_okx_recipe():
    ts, secret = "2026-11-13T09:08:57.715Z", "SECRET"
    want = base64.b64encode(hmac.new(b"SECRET", b"2026-11-13T09:08:57.715ZGET/api/v5/account/balance?ccy=BTC",
                                     hashlib.sha256).digest()).decode()
    assert sign(secret, ts, "get", "/api/v5/account/balance?ccy=BTC") == want


def test_iso_timestamp_has_millis_and_z():
    assert iso_ts(1763024937.715) == "2025-11-13T09:08:57.715Z"


def test_inst_types():
    assert inst_type_of("BTC-USDT-SWAP") == "SWAP"
    assert inst_type_of("BTC-USD-261127") == "FUTURES"
    assert inst_type_of("BTC-USD-261127-75000-C") == "OPTION"
    assert inst_type_of("BTC-USD_UM-261127-70000-P") == "OPTION"
    assert inst_type_of("BTC-USDT") == "SPOT"
    with pytest.raises(OrderInputError):
        inst_type_of("ETH-USDT-SWAP")


# ---------- limits file ----------

def test_example_file_is_valid_and_enabled(tmp_path):
    r = R.Rails(ROOT / "trading.example.toml")
    assert r.error is None and r.active


def test_missing_file_means_no_trading(tmp_path):
    r = R.Rails(tmp_path / "nope.toml")
    assert not r.active and "No limits file" in r.error


def test_typo_disables_trading(tmp_path):
    p = tmp_path / "t.toml"
    p.write_text("enabled = true\n[option]\nmax_contract = 5\n")
    r = R.Rails(p)
    assert not r.active and "max_contract" in r.error


@pytest.mark.parametrize("text,needle", [
    ("enabled = 'yes'", "true or false"),
    ("[order]\nmax_notional_usd = -5", "negative"),
    ("[order]\nmax_notional_usd = '5000'", "number"),
    ("instruments = ['OPTION', 'MARGIN']", "MARGIN"),
    ("[account]\nderivatives_margin = 'portfolio'", "cross, isolated"),
    ("enabled = true\n[order", "valid TOML"),
])
def test_bad_values_disable_trading(tmp_path, text, needle):
    p = tmp_path / "t.toml"
    p.write_text(text)
    r = R.Rails(p)
    assert not r.active and needle in r.error


def test_off_and_live_reload(tmp_path):
    p = tmp_path / "t.toml"
    p.write_text("enabled = true\n[daily]\nmax_loss_usd = 'off'\n")
    r = R.Rails(p)
    assert r.active and r.cfg["daily"]["max_loss_usd"] == "off" and r.cfg["order"]["max_notional_usd"] == 5000
    time.sleep(0.01)
    p.write_text("enabled = false\n")
    import os
    os.utime(p, (time.time() + 5, time.time() + 5))
    assert not r.active


# ---------- the rails on real-looking orders ----------

def test_option_buy_prices_premium_and_passes(tmp_path):
    tr, fake = make(tmp_path)
    p = run(tr.build({"instId": "BTC-USD-261127-75000-C", "side": "buy", "sz": 2, "px": "0.0310"}))
    n = p["numbers"]
    assert n["premium_native"] == pytest.approx(2 * 0.01 * 0.0310)
    assert n["premium_usd"] == pytest.approx(62.0)           # 0.00062 BTC × $100k
    assert n["notional_usd"] == pytest.approx(2000.0)        # 0.02 BTC × $100k
    assert n["worst_case_usd"] == pytest.approx(62 + n["fee_usd"])
    assert n["breakeven"] == pytest.approx(75000 + 3100)
    assert not p["blocked"], failed(p)
    assert p["okx_order"] == {"instId": "BTC-USD-261127-75000-C", "tdMode": "cross", "side": "buy",
                              "ordType": "limit", "sz": "2", "px": "0.031"}


def test_usd_margined_option_premium(tmp_path):
    tr, _ = make(tmp_path)
    p = run(tr.build({"instId": "BTC-USD_UM-261127-70000-P", "side": "buy", "sz": 2, "px": 1560}))
    assert p["numbers"]["premium_usd"] == pytest.approx(2 * 0.01 * 1560)
    assert not p["blocked"], failed(p)


def test_option_too_many_contracts_and_premium(tmp_path):
    tr, _ = make(tmp_path)
    p = run(tr.build({"instId": "BTC-USD-261127-75000-C", "side": "buy", "sz": 6, "px": "0.0310"}))
    assert "option.max_contracts" in failed(p)


def test_selling_options_you_dont_own_is_blocked(tmp_path):
    tr, _ = make(tmp_path)
    p = run(tr.build({"instId": "BTC-USD-261127-75000-C", "side": "sell", "sz": 1, "px": "0.0300"}))
    assert failed(p) == ["option.allow_selling_to_open"]
    assert "Unlimited" in p["numbers"]["worst_case"]


def test_selling_options_you_own_is_closing(tmp_path):
    tr, fake = make(tmp_path)
    fake.positions["BTC-USD-261127-75000-C"] = [{"pos": "3", "posSide": "net"}]
    p = run(tr.build({"instId": "BTC-USD-261127-75000-C", "side": "sell", "sz": 2, "px": "0.0300"}))
    assert p["position"]["closing"] and not p["blocked"], failed(p)
    # selling more than you hold opens a short with the remainder
    p = run(tr.build({"instId": "BTC-USD-261127-75000-C", "side": "sell", "sz": 4, "px": "0.0300"}))
    assert not p["position"]["closing"] and "option.allow_selling_to_open" in failed(p)


def test_close_uses_the_margin_mode_the_position_is_held_in(tmp_path):
    """OKX keeps cross and isolated positions apart; a close in the wrong mode would open a new position."""
    tr, fake = make(tmp_path)   # trading config says derivatives_margin = cross
    opt = "BTC-USD-261127-75000-C"
    sell = lambda sz, **kw: run(tr.build({"instId": opt, "side": "sell", "sz": sz, "px": "0.0300", "reduce": True, **kw}))
    # Held only in isolated: a close goes out isolated even though the config says cross.
    fake.positions[opt] = [{"pos": "4", "posSide": "net", "mgnMode": "isolated"}]
    p = sell(4)
    assert p["okx_order"]["tdMode"] == "isolated" and p["position"]["closing"]
    # Split across both modes: each part closes in its own mode, and only counts what it holds.
    fake.positions[opt] = [{"pos": "145", "posSide": "net", "mgnMode": "cross"},
                           {"pos": "155", "posSide": "net", "mgnMode": "isolated"}]
    with pytest.raises(OrderInputError, match="both cross and isolated"):
        sell(155)
    p = sell(155, mgnMode="isolated")
    assert p["okx_order"]["tdMode"] == "isolated" and p["position"]["contracts"] == 155
    assert p["position"]["closing"]
    p = sell(145, mgnMode="cross")
    assert p["okx_order"]["tdMode"] == "cross" and p["position"]["contracts"] == 145
    with pytest.raises(OrderInputError, match="Reduce-only"):
        sell(150, mgnMode="cross")                      # more than the cross part holds
    with pytest.raises(OrderInputError, match="cross or isolated"):
        sell(1, mgnMode="portfolio")
    # Opening orders still follow the config.
    p = run(tr.build({"instId": opt, "side": "buy", "sz": 1, "px": "0.0300"}))
    assert p["okx_order"]["tdMode"] == "cross"


def test_fat_finger_price_band(tmp_path):
    tr, _ = make(tmp_path)
    p = run(tr.build({"instId": "BTC-USDT-SWAP", "side": "buy", "sz": "0.1", "px": "110000"}))
    assert failed(p) == ["price_band_pct"]


def test_options_have_no_market_orders(tmp_path):
    tr, _ = make(tmp_path)
    p = run(tr.build({"instId": "BTC-USD-261127-75000-C", "side": "buy", "sz": 1, "ordType": "market"}))
    assert "order.allow_market_orders" in failed(p)


def test_swap_notional_leverage_and_position(tmp_path):
    tr, fake = make(tmp_path)
    p = run(tr.build({"instId": "BTC-USDT-SWAP", "side": "buy", "sz": "1.5", "px": "100000"}))
    assert p["numbers"]["notional_usd"] == pytest.approx(1500)
    assert p["numbers"]["margin_usd"] == pytest.approx(500)
    assert not p["blocked"], failed(p)
    fake.lever["BTC-USDT-SWAP"] = "10"
    p = run(tr.build({"instId": "BTC-USDT-SWAP", "side": "buy", "sz": "1.5", "px": "100000"}))
    assert failed(p) == ["swap.max_leverage"]
    fake.lever["BTC-USDT-SWAP"] = "3"
    fake.positions["BTC-USDT-SWAP"] = [{"pos": "4", "posSide": "net"}]
    p = run(tr.build({"instId": "BTC-USDT-SWAP", "side": "buy", "sz": "1.5", "px": "100000"}))
    assert failed(p) == ["swap.max_position_usd"]          # 5.5 × 0.01 × 100k = $5,500 > $5,000


def test_inverse_swap_is_dollars_per_contract(tmp_path):
    tr, _ = make(tmp_path)
    p = run(tr.build({"instId": "BTC-USD-SWAP", "side": "sell", "sz": "12", "px": "100010"}))
    assert p["numbers"]["notional_usd"] == pytest.approx(1200)


def test_long_short_mode_close_sets_pos_side(tmp_path):
    tr, fake = make(tmp_path)
    fake.config = {"acctLv": "2", "posMode": "long_short_mode"}
    fake.positions["BTC-USDT-SWAP"] = [{"pos": "2", "posSide": "long"}]
    fake.lever["BTC-USDT-SWAP"] = "50"                     # leverage cap doesn't apply to closing
    p = run(tr.build({"instId": "BTC-USDT-SWAP", "side": "sell", "sz": "2", "px": "100000", "reduce": True}))
    assert p["okx_order"]["posSide"] == "long" and p["position"]["closing"] and not p["blocked"], failed(p)
    with pytest.raises(OrderInputError, match="hold 0 short"):
        run(tr.build({"instId": "BTC-USDT-SWAP", "side": "buy", "sz": "1", "px": "100000", "reduce": True}))


def test_net_mode_reduce_only(tmp_path):
    tr, fake = make(tmp_path)
    fake.positions["BTC-USDT-SWAP"] = [{"pos": "-3", "posSide": "net"}]
    p = run(tr.build({"instId": "BTC-USDT-SWAP", "side": "buy", "sz": "1", "px": "100000", "reduce": True}))
    assert p["okx_order"]["reduceOnly"] is True and p["position"]["closing"]
    with pytest.raises(OrderInputError, match="Reduce-only"):
        run(tr.build({"instId": "BTC-USDT-SWAP", "side": "sell", "sz": "1", "px": "100000", "reduce": True}))


def test_lot_and_tick_are_checked(tmp_path):
    tr, _ = make(tmp_path)
    with pytest.raises(OrderInputError, match="multiple of 0.01"):
        run(tr.build({"instId": "BTC-USDT-SWAP", "side": "buy", "sz": "0.015", "px": "100000"}))
    with pytest.raises(OrderInputError, match="multiple of 0.1"):
        run(tr.build({"instId": "BTC-USDT-SWAP", "side": "buy", "sz": "0.01", "px": "100000.05"}))


def test_spot_market_buy_sizes_in_btc(tmp_path):
    tr, _ = make(tmp_path)
    p = run(tr.build({"instId": "BTC-USDT", "side": "buy", "sz": "0.01", "ordType": "market"}))
    assert p["okx_order"]["tgtCcy"] == "base_ccy" and p["okx_order"]["tdMode"] == "cash"
    assert p["numbers"]["notional_usd"] == pytest.approx(0.01 * 99990.1)
    with pytest.raises(OrderInputError, match="0.5 BTC available"):
        run(tr.build({"instId": "BTC-USDT", "side": "sell", "sz": "0.6", "px": "99990"}))


def test_spot_mode_account_cant_trade_derivatives(tmp_path):
    tr, fake = make(tmp_path)
    fake.config = {"acctLv": "1", "posMode": "net_mode"}
    with pytest.raises(OrderInputError, match="Spot mode"):
        run(tr.build({"instId": "BTC-USDT-SWAP", "side": "buy", "sz": "0.1", "px": "100000"}))


def test_daily_loss_stops_opening_but_not_closing(tmp_path):
    tr, fake = make(tmp_path)
    fake.fills = [{"fillPnl": "-0.006", "ccy": "BTC", "fee": "-0.0001", "feeCcy": "BTC"}]   # -$610
    p = run(tr.build({"instId": "BTC-USD-261127-75000-C", "side": "buy", "sz": 1, "px": "0.0310"}))
    assert "daily.max_loss_usd" in failed(p)
    fake.positions["BTC-USD-261127-75000-C"] = [{"pos": "1", "posSide": "net"}]
    p = run(tr.build({"instId": "BTC-USD-261127-75000-C", "side": "sell", "sz": 1, "px": "0.0300"}))
    assert not p["blocked"], failed(p)


def test_disabled_and_missing_limits(tmp_path):
    tr, _ = make(tmp_path, "enabled = false\n")
    p = run(tr.build({"instId": "BTC-USDT-SWAP", "side": "buy", "sz": "0.1", "px": "100000"}))
    assert "enabled" in failed(p)
    tr, _ = make(tmp_path, "enabled = true\n[order]\nmax_notionl_usd = 1\n")
    with pytest.raises(OrderInputError, match="max_notionl_usd"):
        run(tr.build({"instId": "BTC-USDT-SWAP", "side": "buy", "sz": "0.1", "px": "100000"}))


# ---------- preview → place, halt ----------

def test_preview_then_place_once(tmp_path):
    tr, fake = make(tmp_path)
    p = run(tr.preview({"instId": "BTC-USDT-SWAP", "side": "buy", "sz": "0.1", "px": "100000"}, source="cli"))
    assert not p["blocked"] and p["summary"][-1] == "Passes every rail"
    r = run(tr.place(p["id"], source="cli"))
    path, body = fake.sent[-1]
    assert path == "/api/v5/trade/order" and body["clOrdId"] == "btcliq" + p["id"] and r["ordId"] == "777"
    assert tr.journal.today()["orders"] == 1
    with pytest.raises(OrderInputError, match="expired or was already used"):
        run(tr.place(p["id"]))


def test_blocked_preview_cant_be_placed(tmp_path):
    tr, fake = make(tmp_path)
    p = run(tr.preview({"instId": "BTC-USDT-SWAP", "side": "buy", "sz": "0.1", "px": "120000"}))
    assert p["blocked"]
    with pytest.raises(OrderInputError, match="blocked by the safety rails"):
        run(tr.place(p["id"]))
    assert not fake.sent


def test_place_rechecks_with_fresh_state(tmp_path):
    tr, fake = make(tmp_path)
    p = run(tr.preview({"instId": "BTC-USDT-SWAP", "side": "buy", "sz": "0.1", "px": "100000"}))
    tr.halt.set("test", "test")
    with pytest.raises(RailsBlocked, match="halted"):
        run(tr.place(p["id"]))
    assert not fake.sent


def test_place_refuses_if_order_changed(tmp_path):
    tr, fake = make(tmp_path)
    fake.positions["BTC-USDT-SWAP"] = [{"pos": "-3", "posSide": "net"}]
    p = run(tr.preview({"instId": "BTC-USDT-SWAP", "side": "buy", "sz": "1", "px": "100000", "reduce": True}))
    fake.config = {"acctLv": "2", "posMode": "long_short_mode"}
    tr._cache.clear()
    fake.positions["BTC-USDT-SWAP"] = [{"pos": "3", "posSide": "short"}]
    with pytest.raises(OrderInputError, match="changed since you previewed"):
        run(tr.place(p["id"]))


def test_cooldown(tmp_path):
    tr, _ = make(tmp_path)
    p = run(tr.preview({"instId": "BTC-USDT-SWAP", "side": "buy", "sz": "0.1", "px": "100000"}))
    run(tr.place(p["id"]))
    p = run(tr.preview({"instId": "BTC-USDT-SWAP", "side": "buy", "sz": "0.1", "px": "100000"}))
    assert "order.cooldown_seconds" in failed(p)


def test_kill_switch_halts_and_cancels(tmp_path):
    tr, fake = make(tmp_path)
    fake.pending = [{"instId": "BTC-USDT-SWAP", "ordId": str(i)} for i in range(25)]
    r = run(tr.kill("news", source="ui"))
    assert r["canceled"] == 25 and tr.halt.state()["reason"] == "news"
    assert [len(b) for path, b in fake.sent if path.endswith("cancel-batch-orders")] == [20, 5]
    fake.pending = []
    p = run(tr.build({"instId": "BTC-USDT-SWAP", "side": "buy", "sz": "0.1", "px": "100000"}))
    assert "halt" in failed(p)
    tr.resume()
    p = run(tr.build({"instId": "BTC-USDT-SWAP", "side": "buy", "sz": "0.1", "px": "100000"}))
    assert not p["blocked"], failed(p)


def test_leverage_cap(tmp_path):
    tr, fake = make(tmp_path)
    with pytest.raises(OrderInputError, match="above your limit"):
        run(tr.set_leverage("BTC-USDT-SWAP", 10))
    run(tr.set_leverage("BTC-USDT-SWAP", 2))
    assert fake.sent[-1] == ("/api/v5/account/set-leverage", {"instId": "BTC-USDT-SWAP", "lever": "2", "mgnMode": "cross"})


# ---------- HTTP guard ----------

def test_trade_routes_refuse_other_sites(tmp_path):
    async def go():
        app = web.Application()
        trade_routes.setup(app)
        app["trader"], _ = make(tmp_path)
        async with TestClient(TestServer(app)) as c:
            r = await c.get("/api/trade/status")
            assert r.status == 200
            token = (await r.json())["token"]
            assert (await c.post("/api/trade/kill", json={})).status == 403                         # no token
            assert (await c.post("/api/trade/kill", json={}, headers={"X-Btcliq-Trade": "x"})).status == 403
            assert (await c.get("/api/trade/status", headers={"Host": "evil.example:8050"})).status == 403
            assert (await c.post("/api/trade/resume", headers={"X-Btcliq-Trade": token,
                                                               "Origin": "https://evil.example"})).status == 403
            r = await c.post("/api/trade/preview", json={"instId": "BTC-USDT-SWAP", "side": "buy", "sz": "0.1", "px": "1"},
                             headers={"X-Btcliq-Trade": token})
            assert r.status == 200 and (await r.json())["blocked"]
            r = await c.post("/api/trade/preview", json={"instId": "DOGE", "side": "buy", "sz": 1},
                             headers={"X-Btcliq-Trade": token})
            assert r.status == 400
    run(go())


def test_kill_route_needs_typed_confirmation(tmp_path):
    """The browser's kill switch only fires with the word typed in its confirmation dialog."""
    async def go():
        app = web.Application()
        trade_routes.setup(app)
        tr, fake = make(tmp_path)
        app["trader"] = tr
        fake.pending = [{"instId": "BTC-USDT-SWAP", "ordId": str(i)} for i in range(3)]
        async with TestClient(TestServer(app)) as c:
            h = {"X-Btcliq-Trade": (await (await c.get("/api/trade/status")).json())["token"]}
            for body in ({}, {"reason": "oops"}, {"confirm": ""}, {"confirm": "yes"}, {"confirm": "KIL"}, {"confirm": True}):
                r = await c.post("/api/trade/kill", json=body, headers=h)
                assert r.status == 400, body
                assert "type KILL" in (await r.json())["error"]
            assert tr.halt.state() is None                                    # nothing halted
            assert not [p for p, _ in fake.sent if p.endswith("cancel-batch-orders")]   # nothing canceled
            r = await c.post("/api/trade/kill", json={"reason": "news", "confirm": " kill "}, headers=h)
            assert r.status == 200
            d = await r.json()
            assert d["canceled"] == 3 and d["halted"]["reason"] == "news" and d["halted"]["source"] == "ui"
            assert tr.halt.state()["reason"] == "news"
    run(go())


# ---------- "ask" rules: notify and let the user choose ----------

ASK_TOML = """enabled = true
[order]
max_notional_usd = "off"
cooldown_seconds = 0
[daily]
max_loss_usd = 50
[option]
max_contracts = "off"
max_premium_usd = "off"
max_position_contracts = "off"
ask = ["price_band_pct", "daily.max_loss_usd", "daily.max_orders", "option.allow_selling_to_open"]
"""


def test_ask_list_is_validated(tmp_path):
    p = tmp_path / "t.toml"
    p.write_text("enabled = true\n[option]\nask = ['enabled']\n")
    assert "can't ask for approval" in R.Rails(p).error
    p.write_text("enabled = true\n[option]\nask = 'price_band_pct'\n")
    assert "must be a list" in R.Rails(p).error


def test_take_profit_far_above_mark_asks_instead_of_blocking(tmp_path):
    tr, fake = make(tmp_path, ASK_TOML)
    fake.positions["BTC-USD-261127-75000-C"] = [{"pos": "89", "posSide": "net"}]
    p = run(tr.preview({"instId": "BTC-USD-261127-75000-C", "side": "sell", "sz": 9, "px": "0.2300"}))
    assert not p["blocked"] and p["asks"] == ["price_band_pct"]
    band = next(c for c in p["checks"] if c["rule"] == "price_band_pct")
    assert band["ask"] and "rest in the book" in band["msg"]
    assert p["summary"][-1].startswith("NEEDS YOUR APPROVAL")
    from btcliq.trader import NeedsApproval
    with pytest.raises(NeedsApproval, match="accept: price_band_pct"):
        run(tr.place(p["id"]))
    assert not fake.sent                                     # nothing sent without approval
    r = run(tr.place(p["id"], accept=["price_band_pct"]))    # the preview survived; approve and go
    assert r["ordId"] == "777" and fake.sent[-1][1]["px"] == "0.23"
    assert tr.journal.since(0)[-1]["approved"] == ["price_band_pct"]


def test_risky_direction_message(tmp_path):
    tr, _ = make(tmp_path, ASK_TOML)
    p = run(tr.build({"instId": "BTC-USD-261127-75000-C", "side": "buy", "sz": 1, "px": "0.0500"}))
    band = next(c for c in p["checks"] if c["rule"] == "price_band_pct")
    assert "pay well above" in band["msg"] and band["ask"]


def test_ask_covers_writing_and_daily_loss_but_not_perps(tmp_path):
    tr, fake = make(tmp_path, ASK_TOML)
    fake.fills = [{"fillPnl": "-0.001", "ccy": "BTC", "fee": "0", "feeCcy": "BTC"}]      # -$100 today
    p = run(tr.build({"instId": "BTC-USD-261127-75000-C", "side": "sell", "sz": 1, "px": "0.0300"}))
    assert not p["blocked"] and set(p["asks"]) == {"option.allow_selling_to_open", "daily.max_loss_usd"}
    p = run(tr.build({"instId": "BTC-USDT-SWAP", "side": "buy", "sz": "0.1", "px": "110000"}))
    assert p["blocked"] and not p["asks"]                   # perps: still a hard block


def test_halt_is_never_askable(tmp_path):
    tr, _ = make(tmp_path, ASK_TOML)
    tr.halt.set("x", "test")
    p = run(tr.build({"instId": "BTC-USD-261127-75000-C", "side": "buy", "sz": 1, "px": "0.0310"}))
    assert p["blocked"] and "halt" in failed(p)


def test_route_reports_needs_approval(tmp_path):
    async def go():
        app = web.Application()
        trade_routes.setup(app)
        app["trader"], fake = make(tmp_path, ASK_TOML)
        fake.positions["BTC-USD-261127-75000-C"] = [{"pos": "89", "posSide": "net"}]
        async with TestClient(TestServer(app)) as c:
            token = (await (await c.get("/api/trade/status")).json())["token"]
            h = {"X-Btcliq-Trade": token}
            p = await (await c.post("/api/trade/preview", json={"instId": "BTC-USD-261127-75000-C", "side": "sell",
                                                               "sz": 9, "px": "0.2300"}, headers=h)).json()
            r = await c.post("/api/trade/place", json={"id": p["id"]}, headers=h)
            assert r.status == 409 and (await r.json())["needs_approval"] == ["price_band_pct"]
            r = await c.post("/api/trade/place", json={"id": p["id"], "accept": ["price_band_pct"]}, headers=h)
            assert r.status == 200
    run(go())
