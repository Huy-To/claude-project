from btcliq.model import LiqModel


def test_open_places_levels_on_both_sides():
    m = LiqModel(bin_size=100, leverage=[(10, 1.0)], mmr=0.0)
    m.open_positions(0, 100_000, 2_000_000)
    assert list(m.long) == [900]    # 100k * (1 - 1/10) = 90k
    assert list(m.short) == [1100]                  # 100k * (1 + 1/10) = 110k
    assert m.long[900].usd == 1_000_000 and m.short[1100].usd == 1_000_000


def test_sweep_removes_hit_levels_only():
    m = LiqModel(bin_size=100, leverage=[(10, 0.5), (20, 0.5)], mmr=0.0)
    m.open_positions(0, 100_000, 4_000_000)          # longs at 90k, 95k; shorts 105k, 110k
    hit = m.sweep(low=94_000, high=106_000)
    assert hit == {"long": 1_000_000, "short": 1_000_000}
    assert list(m.long) == [900] and list(m.short) == [1100]


def test_oi_drop_shrinks_levels():
    m = LiqModel(bin_size=100, leverage=[(10, 1.0)], mmr=0.0)
    m.step(0, 100_100, 99_900, 100_000, 2_000_000, 0)
    m.step(1, 100_100, 99_900, 100_000, -500_000, 2_000_000)  # 25% closed
    assert round(m.short[1100].usd) == 750_000


def test_levels_output_sorted_and_windowed():
    m = LiqModel(bin_size=100)
    m.open_positions(5, 100_000, 10_000_000)
    rows = m.levels(100_000, window=0.05)
    assert rows and all(95_000 <= (r["lo"] + r["hi"]) / 2 <= 105_000 for r in rows)
    assert rows == sorted(rows, key=lambda r: r["usd"], reverse=True)
    assert all(r["start"] == 5 for r in rows)
