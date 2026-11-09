from btcliq.options import parse_query


def test_parse_okx_names():
    assert parse_query("BTC-USD-261127-75000-C") == {"family": None, "expiry": "261127", "strike": 75000.0, "type": "C"}
    assert parse_query("btc-usd_um-261225-80000-p") == {"family": "BTC-USD_UM", "expiry": "261225", "strike": 80000.0, "type": "P"}


def test_parse_loose_names():
    q = parse_query("27nov 75k call")
    assert q["expiry_dm"] == (27, 11, None) and q["strike"] == 75000 and q["type"] == "C"
    q = parse_query("BTC-27NOV26-75000-P")  # Deribit style
    assert q["expiry_dm"] == (27, 11, 26) and q["strike"] == 75000 and q["type"] == "P"
    q = parse_query("75000C 4DEC")
    assert q["strike"] == 75000 and q["type"] == "C" and q["expiry_dm"] == (4, 12, None)
    assert parse_query("261225 70000 put")["type"] == "P"


def test_parse_written_dates_and_years():
    for q in ["75000 call expiring 27 nov 2026", "the 75k call for November 27th", "Nov 27, 2026 75000 C"]:
        p = parse_query(q)
        assert p["expiry_dm"][:2] == (27, 11) and p["strike"] == 75000 and p["type"] == "C", (q, p)
    assert parse_query("75000 call expiring 27 nov 2026")["expiry_dm"] == (27, 11, 26)
