import time

from btcliq.book import Book, BookFeed


def feed_with(**books):
    f = BookFeed.__new__(BookFeed)  # no network session needed for aggregate()
    f.books = {k: Book() for k in ("OKX", "Hyperliquid", "Deribit", "Kraken", "Coinbase", "Bitfinex")}
    f.books.update(books)
    return f


def test_buckets_never_cross_and_sum_venues():
    now = time.time()
    f = feed_with(
        OKX=Book([(100.4, 1.0), (99.6, 2.0)], [(100.6, 1.5), (101.4, 0.5)], now),
        Kraken=Book([(100.2, 3.0)], [(100.8, 1.0)], now),
    )
    agg = f.aggregate(step=1, ref_mid=100.5, rng=0.05, align=False)
    assert agg["best_bid"] <= agg["best_ask"]
    bids = {r[0]: r for r in agg["bids"]}
    assert bids[100][1] == 4.0 and bids[100][2] == {"OKX": 1.0, "Kraken": 3.0}
    asks = {r[0]: r for r in agg["asks"]}
    assert asks[101][1] == 2.5  # 100.6 + 100.8 round up into the 101 bucket


def test_align_shifts_spot_onto_perp_mid():
    now = time.time()
    f = feed_with(
        OKX=Book([(100.0, 1.0)], [(101.0, 1.0)], now),        # mid 100.5
        Coinbase=Book([(90.0, 2.0)], [(91.0, 2.0)], now),      # mid 90.5: $10 basis
    )
    raw = f.aggregate(step=1, ref_mid=100.5, rng=0.2, align=False)
    assert raw["bids"][0][0] == 100 and raw["asks"][0][0] == 91  # crossed without alignment
    al = f.aggregate(step=1, ref_mid=100.5, rng=0.2, align=True)
    assert al["bids"][0][1] == 3.0 and al["asks"][0][1] == 3.0  # both venues land on the same buckets
    assert al["venues"]["Coinbase"]["shift"] == 10


def test_stale_or_empty_venues_are_skipped():
    f = feed_with(OKX=Book([(100.0, 1.0)], [(101.0, 1.0)], time.time() - 60))
    agg = f.aggregate(step=1, ref_mid=100.5)
    assert agg["bids"] == [] and agg["asks"] == []
    assert agg["venues"]["OKX"]["age"] > 15


def test_range_limits_levels():
    now = time.time()
    f = feed_with(OKX=Book([(100.0, 1.0), (50.0, 9.0)], [(101.0, 1.0), (500.0, 9.0)], now))
    agg = f.aggregate(step=1, ref_mid=100.5, rng=0.02)
    assert [r[0] for r in agg["bids"]] == [100] and [r[0] for r in agg["asks"]] == [101]
