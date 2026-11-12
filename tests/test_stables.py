import asyncio

from btcliq.stables import Stablecoins

ASSETS = {"peggedAssets": [
    {"id": "1", "name": "Tether", "symbol": "USDT", "pegType": "peggedUSD", "pegMechanism": "fiat-backed",
     "price": 0.999, "circulating": {"peggedUSD": 200.0e9}, "circulatingPrevDay": {"peggedUSD": 199.0e9},
     "circulatingPrevWeek": {"peggedUSD": 190.0e9}, "circulatingPrevMonth": {"peggedUSD": 180.0e9},
     "chains": ["Ethereum", "Tron"], "chainCirculating": {"Tron": {"current": {"peggedUSD": 120e9}},
                                                           "Ethereum": {"current": {"peggedUSD": 80e9}}}},
    {"id": "9", "name": "Euro Coin", "symbol": "EURC", "pegType": "peggedEUR", "pegMechanism": "crytpo-backed",
     "price": 1.10, "circulating": {"peggedEUR": 1.0e9}, "circulatingPrevDay": {"peggedEUR": 1.0e9},
     "circulatingPrevWeek": {}, "circulatingPrevMonth": {"peggedEUR": 0.5e9}, "chains": [], "chainCirculating": {}},
    {"id": "7", "name": "Broken", "symbol": "BRK", "pegType": "peggedUSD", "pegMechanism": "algorithmic",
     "price": 0.90, "circulating": {"peggedUSD": 100e6}, "circulatingPrevDay": {"peggedUSD": 100e6},
     "circulatingPrevWeek": {"peggedUSD": 100e6}, "circulatingPrevMonth": {"peggedUSD": 100e6},
     "chains": [], "chainCirculating": {}},
]}
CHAINS = [{"name": "Tron", "totalCirculatingUSD": {"peggedUSD": 120e9}},
          {"name": "Ethereum", "totalCirculatingUSD": {"peggedUSD": 81.1e9}}]
HISTORY = [{"date": str(1700000000 + i * 86400), "totalCirculatingUSD": {"peggedUSD": 150e9 + i * 1e8}} for i in range(400)]


def make():
    s = Stablecoins.__new__(Stablecoins)
    Stablecoins.__init__(s, session=None)

    async def fake_get(path, **params):
        return {"/stablecoins": ASSETS, "/stablecoinchains": CHAINS, "/stablecoincharts/all": HISTORY}[path]

    async def fake_btc(price):
        return {"price": 100_000, "mcap": 2.0e12, "source": "test"}

    s._get, s._btc = fake_get, fake_btc
    return s


def test_refresh_parses_and_summarizes():
    s = make()
    asyncio.run(s.refresh())
    sm = s.summary
    assert [a["symbol"] for a in s.assets] == ["USDT", "EURC", "BRK"]
    eurc = s.assets[1]
    assert eurc["supply"] == 1.1e9 and eurc["peg"] == "EUR" and eurc["mechanism"] == "crypto-backed"
    assert sm["total"] == 200e9 + 1.1e9 + 100e6
    # 7d change only compares coins that report a 7d value (EURC doesn't)
    assert round(sm["change_7d"]["abs"]) == round(200e9 + 100e6 - 190e9 - 100e6)
    assert [d["symbol"] for d in sm["depegs"]] == ["BRK"]
    assert round(sm["ssr"], 3) == round(2.0e12 / sm["total"], 3)
    assert len(s.history) == 400 and s.chains[0]["name"] == "Tron"
    assert any("PEG ALERTS" in l for l in s.context())
