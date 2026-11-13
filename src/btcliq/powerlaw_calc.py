# Vendored copy of btc-powerlaw/powerlaw.py (unchanged below this header) so btcliq is self-contained.
# Source: ../btc-powerlaw/powerlaw.py (the sibling btc-powerlaw project). btcliq.powerlaw sets CACHE to btcliq/data/.
#!/usr/bin/env python3
"""
btc-powerlaw: Bitcoin Power Law (Santostasi) state, forward levels and option-probability calculator.

Standard library only (Python 3.9+). Price history: Coin Metrics community API (daily
reference rate "PriceUSD", from 2010-07-18), cached in data/btc_daily.csv next to this file.
The cache is topped up automatically; if the network fails the cached data is used.

Usage
  python3 powerlaw.py                         # state + forward levels at upcoming expiries
  python3 powerlaw.py --live                  # use the live OKX BTC-USD index as today's price
  python3 powerlaw.py --json                  # machine-readable output (everything below)
  python3 powerlaw.py --live --expiry 2027-06-25 --strikes 60000,80000,100000,130000 --iv 0.47
  python3 powerlaw.py --asof 2022-11-21       # what the model said on a past date (refit uses
                                              # only data up to that date)
  python3 powerlaw.py --dates 2027-01-29,2027-03-26   # forward levels at your own dates

Model conventions (see POWER_LAW_BRIEF.md for the full rationale)
  t      = days since the Genesis Block, 2009-01-03 (UTC calendar days)
  trend  = 10**a * t**b                       (log10 price is linear in log10 t)
  SP26   a=-16.509 b=5.690   Santostasi & Perrenod 2026, OLS 2010-07..2026-02  <- primary
  CANON  A=1.0117e-17 b=5.82 Santostasi's widely copied 2018-2024 chart parameters
  MLE26  a=-16.399 b=5.658   Santostasi & Perrenod Oct-2026 skew-normal MLE
  REFIT  OLS on all data from 2010-07-18 to the evaluation date (live, no look-ahead)
  floor  = 0.42 x SP26 trend (Scientific Bitcoin Institute / Newhedge convention)
  resid  = log10(price / trend)  ("dex"; 0.30 dex = factor 2)
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import os
import sys
import time
import urllib.request
from datetime import date, datetime, timedelta, timezone

GENESIS = date(2009, 1, 3)
FIT_START = date(2010, 7, 18)     # first reliable exchange prices (Coin Metrics series start)
AR_START = date(2013, 1, 1)       # residual-dynamics regressions start here (skip the 2010-12 chaos)
FIXED_MODELS = {
    "SP26": (-16.509, 5.690, "Santostasi & Perrenod 2026 published OLS (2010-07..2026-02)"),
    "CANON": (math.log10(1.0117e-17), 5.82, "Santostasi canonical chart params (2018-2024)"),
    "MLE26": (-16.399, 5.658, "Santostasi & Perrenod Oct-2026 skew-normal MLE"),
}
PRIMARY = "SP26"
FLOOR_MULT = 0.42
QR025 = (-17.5463, 5.8744)        # 2.5% quantile-regression line (Burger-style), fit 2010-07..2026-11
# Zone upper bounds on price / SP26-trend ratio
ZONES = [(0.42, "BELOW_FLOOR"), (0.55, "DEEP_VALUE"), (0.80, "VALUE"),
         (1.25, "FAIR"), (2.00, "EXTENDED"), (float("inf"), "BUBBLE")]
HALVINGS = [date(2012, 11, 28), date(2016, 7, 9), date(2020, 5, 11), date(2024, 4, 20)]
NEXT_HALVING_EST = "2028-04 (estimate; block 1,050,000)"
NVPL_ALPHA, NVPL_CENTER = 0.015, 274.0   # Baquero & Tinoco 2026 naive-power-law blend
UA = {"User-Agent": "btc-powerlaw/1.0 (stdlib urllib)"}
HERE = os.path.dirname(os.path.abspath(__file__))
CACHE = os.path.join(HERE, "data", "btc_daily.csv")
CM_URL = ("https://community-api.coinmetrics.io/v4/timeseries/asset-metrics"
          "?assets=btc&metrics=PriceUSD&frequency=1d&page_size=10000&start_time={start}")


# ----------------------------------------------------------------------------- data
def load_cache(path: str) -> list[tuple[date, float]]:
    if not os.path.exists(path):
        return []
    rows = []
    with open(path, newline="") as f:
        for r in csv.DictReader(f):
            p = float(r["price"])
            if p > 0:
                rows.append((date.fromisoformat(r["date"]), p))
    rows.sort()
    return rows


def save_cache(path: str, rows: list[tuple[date, float]]) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", newline="") as f:
        f.write("date,price\n")
        for d, p in rows:
            f.write(f"{d.isoformat()},{p!r}\n")
    os.replace(tmp, path)


def http_json(url: str, timeout: float = 30):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


def fetch_coinmetrics(start: date) -> list[tuple[date, float]]:
    url, out = CM_URL.format(start=start.isoformat()), []
    while url:
        d = http_json(url, timeout=60)
        for x in d.get("data", []):
            if x.get("PriceUSD"):
                out.append((date.fromisoformat(x["time"][:10]), float(x["PriceUSD"])))
        url = d.get("next_page_url")
        if url:
            time.sleep(0.3)
    return out


def update_data(offline: bool) -> tuple[list[tuple[date, float]], str]:
    rows = load_cache(CACHE)
    if offline:
        return rows, "cache (offline)"
    yesterday = datetime.now(timezone.utc).date() - timedelta(days=1)
    if rows and rows[-1][0] >= yesterday:
        return rows, "cache (current)"
    try:
        start = rows[-1][0] + timedelta(days=1) if rows else date(2009, 1, 1)
        new = fetch_coinmetrics(start)
        if new:
            merged = dict(rows)
            merged.update(new)
            rows = sorted(merged.items())
            save_cache(CACHE, rows)
        return rows, f"Coin Metrics (+{len(new)} new days)"
    except Exception as e:  # network down, API change: fall back to cache
        return rows, f"cache (update failed: {type(e).__name__}: {e})"


def live_price() -> tuple[float, str]:
    try:
        d = http_json("https://www.okx.com/api/v5/market/index-tickers?instId=BTC-USD", 15)
        return float(d["data"][0]["idxPx"]), "OKX BTC-USD index (live)"
    except Exception:
        d = http_json("https://api.coinbase.com/v2/prices/BTC-USD/spot", 15)
        return float(d["data"]["amount"]), "Coinbase spot (live)"


# ----------------------------------------------------------------------------- math
def tdays(d: date) -> int:
    return (d - GENESIS).days


def trend(a: float, b: float, t: float) -> float:
    return 10 ** (a + b * math.log10(t))


def ols(xs: list[float], ys: list[float]) -> tuple[float, float, float, float]:
    n = len(xs)
    mx, my = sum(xs) / n, sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    b = sxy / sxx
    a = my - b * mx
    res = [y - (a + b * x) for x, y in zip(xs, ys)]
    ss_res = sum(r * r for r in res)
    ss_tot = sum((y - my) ** 2 for y in ys)
    return a, b, 1 - ss_res / ss_tot, math.sqrt(ss_res / (n - 2))


def pct_rank(values: list[float], x: float) -> float:
    return 100.0 * sum(1 for v in values if v < x) / len(values) if values else float("nan")


def ncdf(x: float) -> float:
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def realized_vol(prices: list[float], n: int) -> float:
    if len(prices) < n + 1:
        return float("nan")
    lr = [math.log(prices[i] / prices[i - 1]) for i in range(len(prices) - n, len(prices))]
    m = sum(lr) / n
    return math.sqrt(sum((r - m) ** 2 for r in lr) / (n - 1)) * math.sqrt(365)


def zone_of(ratio: float) -> str:
    for ub, name in ZONES:
        if ratio < ub:
            return name
    return ZONES[-1][1]


def nvpl_weight(h: float) -> float:
    return 1 / (1 + math.exp(-NVPL_ALPHA * (h - NVPL_CENTER)))


def ar_coeffs(resid: dict[date, float], h: int, start: date, end: date):
    """OLS of r(t+h) on r(t) over pairs with start <= t and t+h <= end. Returns (k, c, sd, n)."""
    xs, ys = [], []
    for d, r in resid.items():
        if d < start:
            continue
        d2 = d + timedelta(days=h)
        if d2 > end:
            continue
        r2 = resid.get(d2)
        if r2 is not None:
            xs.append(r)
            ys.append(r2)
    if len(xs) < 200:
        return None
    c, k, _, sd = ols(xs, ys)
    return k, c, sd, len(xs)


def touch_prob(x0: float, mu: float, sigma: float, T: float) -> float:
    """P(min over [0,T] of X <= 0) for X = x0 + mu t + sigma W, x0 > 0 (continuous monitoring)."""
    if x0 <= 0:
        return 1.0
    if T <= 0 or sigma <= 0:
        return 0.0
    s = sigma * math.sqrt(T)
    a = ncdf((-x0 - mu * T) / s)
    expo = -2 * mu * x0 / sigma ** 2
    b = math.exp(expo) * ncdf((-x0 + mu * T) / s) if expo < 700 else float("inf")
    return min(1.0, a + b)


def bs(S: float, K: float, T: float, r: float, vol: float, kind: str) -> float:
    s = vol * math.sqrt(T)
    d1 = (math.log(S / K) + (r + 0.5 * vol * vol) * T) / s
    d2 = d1 - s
    if kind == "call":
        return S * ncdf(d1) - K * math.exp(-r * T) * ncdf(d2)
    return K * math.exp(-r * T) * ncdf(-d2) - S * ncdf(-d1)


def lognormal_payoff(m: float, s: float, K: float, kind: str) -> float:
    """E[(S-K)+] or E[(K-S)+] for ln S ~ N(m, s^2)."""
    lk, ef = math.log(K), math.exp(m + 0.5 * s * s)
    if kind == "call":
        return ef * ncdf((m - lk + s * s) / s) - K * ncdf((m - lk) / s)
    return K * ncdf((lk - m) / s) - ef * ncdf((lk - m - s * s) / s)


def last_friday(y: int, m: int) -> date:
    nxt = date(y + (m == 12), m % 12 + 1, 1)
    d = nxt - timedelta(days=1)
    while d.weekday() != 4:
        d -= timedelta(days=1)
    return d


def default_dates(today: date) -> list[date]:
    """Next 3 monthly expiries (last Friday) + quarterly expiries out to ~2 years."""
    out, y, m = [], today.year, today.month
    for i in range(26):
        mm, yy = (m - 1 + i) % 12 + 1, y + (m - 1 + i) // 12
        d = last_friday(yy, mm)
        if d <= today:
            continue
        if mm in (3, 6, 9, 12) or len(out) < 3:
            out.append(d)
    return out


# ----------------------------------------------------------------------------- core
def analyse(rows, eval_date: date, price: float, price_src: str, extra_dates, opt):
    rows = [(d, p) for d, p in rows if d <= eval_date]
    if len(rows) < 1000:
        raise SystemExit("Not enough price history up to the evaluation date.")
    t0 = tdays(eval_date)
    fit_rows = [(d, p) for d, p in rows if d >= FIT_START]
    ra, rb, r2, rsd = ols([math.log10(tdays(d)) for d, _ in fit_rows],
                          [math.log10(p) for _, p in fit_rows])
    models = {k: (a, b, desc) for k, (a, b, desc) in FIXED_MODELS.items()}
    models["REFIT"] = (ra, rb, f"OLS {FIT_START}..{rows[-1][0]} (R2 {r2:.4f}, resid sd {rsd:.3f} dex)")

    out = {"asof": eval_date.isoformat(), "price": round(price, 2), "price_source": price_src,
           "days_since_genesis": t0, "data_last_date": rows[-1][0].isoformat(), "models": {}}
    for k, (a, b, desc) in models.items():
        tv = trend(a, b, t0)
        out["models"][k] = {"a": round(a, 4), "b": round(b, 4), "desc": desc, "trend": round(tv),
                            "ratio": round(price / tv, 4), "resid_dex": round(math.log10(price / tv), 4)}

    a, b, _ = models[PRIMARY]
    tv = trend(a, b, t0)
    floor = FLOOR_MULT * tv
    ratio = price / tv
    resid_hist = {d: math.log10(p / trend(a, b, tdays(d))) for d, p in fit_rows}
    r_now = math.log10(ratio)
    vals = list(resid_hist.values())
    since = lambda y: [v for d, v in resid_hist.items() if d.year >= y]
    # date at which a flat price meets the rising floor: 0.42*trend(t*) = price
    t_star = 10 ** ((math.log10(price / FLOOR_MULT) - a) / b)
    g_year = trend(a, b, t0 + 365.25) / tv - 1
    out["primary"] = {
        "model": PRIMARY, "trend": round(tv), "floor_mult": FLOOR_MULT, "floor": round(floor),
        "ratio": round(ratio, 4), "resid_dex": round(r_now, 4), "zone": zone_of(ratio),
        "pct_rank_all": round(pct_rank(vals, r_now), 1),
        "pct_rank_since_2015": round(pct_rank(since(2015), r_now), 1),
        "pct_rank_since_2018": round(pct_rank(since(2018), r_now), 1),
        "floor_cushion_pct": round(100 * (price / floor - 1), 2),
        "floor_qr025": round(trend(*QR025, t0)),
        "qr025_cushion_pct": round(100 * (price / trend(*QR025, t0) - 1), 2),
        "floor_refit": round(FLOOR_MULT * trend(ra, rb, t0)),
        "days_until_flat_price_meets_floor": round(t_star - t0) if price > floor else 0,
        "flat_price_meets_floor_on": (GENESIS + timedelta(days=round(t_star))).isoformat() if price > floor else None,
        "trend_growth_pct_per_year": round(100 * g_year, 2),
        "trend_growth_pct_per_30d": round(100 * (trend(a, b, t0 + 30) / tv - 1), 2),
        "one_sigma_band": [round(tv / 2.0), round(tv * 2.0)],   # sigma ~0.30 dex = x2
    }

    # cycle clock (closes)
    closes = [p for _, p in rows]
    i_ath = max(range(len(rows)), key=lambda i: rows[i][1])
    ath_d, ath_p = rows[i_ath]
    post = rows[i_ath:]
    low_d, low_p = min(post, key=lambda x: x[1])
    last_h = max(h for h in HALVINGS if h <= eval_date)
    out["cycle"] = {
        "ath_close_date": ath_d.isoformat(), "ath_close": round(ath_p),
        "ath_ratio_to_trend": round(ath_p / trend(a, b, tdays(ath_d)), 3),
        "post_ath_low_close_date": low_d.isoformat(), "post_ath_low_close": round(low_p),
        "post_ath_low_ratio_to_trend": round(low_p / trend(a, b, tdays(low_d)), 3),
        "drawdown_ath_to_low_pct": round(100 * (low_p / ath_p - 1), 1),
        "days_since_ath": (eval_date - ath_d).days, "days_since_post_ath_low": (eval_date - low_d).days,
        "last_halving": last_h.isoformat(), "days_since_halving": (eval_date - last_h).days,
        "next_halving": NEXT_HALVING_EST,
    }
    epochs, bounds = [], HALVINGS + [eval_date + timedelta(days=1)]
    for s, e in zip(bounds[:-1], bounds[1:]):
        seg = [(d, v) for d, v in resid_hist.items() if s <= d < e]
        if not seg:
            continue
        mx, mn = max(seg, key=lambda x: x[1]), min(seg, key=lambda x: x[1])
        epochs.append({"from": s.isoformat(), "to": min(e, eval_date).isoformat(),
                       "max_resid": round(mx[1], 3), "max_ratio": round(10 ** mx[1], 2), "max_date": mx[0].isoformat(),
                       "min_resid": round(mn[1], 3), "min_ratio": round(10 ** mn[1], 3), "min_date": mn[0].isoformat(),
                       "mean_resid": round(sum(v for _, v in seg) / len(seg), 3)})
    out["halving_epochs"] = epochs
    out["realized_vol"] = {f"rv{n}": round(realized_vol(closes, n), 4) for n in (30, 90, 365)}

    # forward levels
    refit_resid = {d: math.log10(p / trend(ra, rb, tdays(d))) for d, p in fit_rows}
    r_refit_now = math.log10(price / trend(ra, rb, t0))
    fwd = []
    for fd in sorted(set(extra_dates or default_dates(eval_date))):
        if fd <= eval_date:
            continue
        h, tf = (fd - eval_date).days, tdays(fd)
        T_sp, T_rf = trend(a, b, tf), trend(ra, rb, tf)
        w = nvpl_weight(h)
        nv = 10 ** ((1 - w) * math.log10(price) + w * math.log10(T_rf))
        ar = ar_coeffs(refit_resid, h, AR_START, rows[-1][0])
        row = {"date": fd.isoformat(), "days": h, "trend": round(T_sp), "floor": round(FLOOR_MULT * T_sp),
               "floor_qr025": round(trend(*QR025, tf)), "x0_55": round(0.55 * T_sp), "x0_80": round(0.80 * T_sp), "x1_25": round(1.25 * T_sp),
               "refit_trend": round(T_rf), "nvpl_weight": round(w, 3), "nvpl_forecast": round(nv)}
        if ar:
            k, c, sd, n = ar
            row.update({"ar_k": round(k, 3), "ar_forecast": round(10 ** (math.log10(T_rf) + c + k * r_refit_now)),
                        "ar_resid_sd_dex": round(sd, 3)})
        fwd.append(row)
    out["forward"] = fwd

    if opt and opt.get("expiry"):
        out["options"] = option_block(opt, eval_date, price, a, b, ra, rb, refit_resid, r_refit_now,
                                      rows[-1][0], out["realized_vol"]["rv90"])
    return out


def option_block(opt, eval_date, S, a, b, ra, rb, refit_resid, r_refit_now, data_end, rv90):
    ex = opt["expiry"]
    h = (ex - eval_date).days
    if h <= 0:
        raise SystemExit("--expiry must be after the evaluation date")
    T = h / 365.0
    iv = opt.get("iv") or rv90
    r = opt.get("rate", 0.0)
    s = iv * math.sqrt(T)
    t0, tf = tdays(eval_date), tdays(ex)
    floor0, floorT = FLOOR_MULT * trend(a, b, t0), FLOOR_MULT * trend(a, b, tf)
    g = math.log(floorT / floor0) / T                       # floor log-growth per year
    m_rn = math.log(S) + (r - 0.5 * iv * iv) * T            # risk-neutral mean of ln S_T
    w = nvpl_weight(h)
    m_nv = (1 - w) * math.log(S) + w * math.log(trend(ra, rb, tf))
    measures = {"risk_neutral": m_rn, "nvpl": m_nv}
    ar = ar_coeffs(refit_resid, h, AR_START, data_end)
    if ar:
        k, c, _, _ = ar
        measures["ar"] = math.log(10) * (math.log10(trend(ra, rb, tf)) + c + k * r_refit_now)
    x0 = math.log(S / floor0)
    res = {"expiry": ex.isoformat(), "days": h, "iv_used": round(iv, 4),
           "iv_source": "user" if opt.get("iv") else "realized 90d (pass --iv with the live ATM IV!)",
           "rate": r, "floor_now": round(floor0), "floor_at_expiry": round(floorT),
           "trend_at_expiry": round(trend(a, b, tf)), "nvpl_weight": round(w, 3),
           "median_price": {k: round(math.exp(m)) for k, m in measures.items()},
           "p_touch_floor_before_expiry": {}, "p_below_floor_at_expiry": {}, "strikes": []}
    for name, m in measures.items():
        mu = (m - math.log(S)) / T - g                      # drift of ln(S/floor)
        res["p_touch_floor_before_expiry"][name] = round(touch_prob(x0, mu, iv, T), 4)
        res["p_below_floor_at_expiry"][name] = round(ncdf((math.log(floorT) - m) / s), 4)
    # "mix": trust-weighted blend of the NvPL world (model holds) and the zero-drift world (model fails)
    trust = opt.get("trust", 0.5)
    res["trust"] = trust
    mixf = lambda d: round(trust * d["nvpl"] + (1 - trust) * d["risk_neutral"], 4)
    res["p_touch_floor_before_expiry"]["mix"] = mixf(res["p_touch_floor_before_expiry"])
    res["p_below_floor_at_expiry"]["mix"] = mixf(res["p_below_floor_at_expiry"])
    disc = math.exp(-r * T)
    for K in opt.get("strikes") or []:
        item = {"K": K, "p_above": {}, "call": {}, "put": {}}
        for name, m in measures.items():
            item["p_above"][name] = round(1 - ncdf((math.log(K) - m) / s), 4)
            if name == "risk_neutral":
                item["call"][name] = round(bs(S, K, T, r, iv, "call"), 2)
                item["put"][name] = round(bs(S, K, T, r, iv, "put"), 2)
            else:
                item["call"][name] = round(disc * lognormal_payoff(m, s, K, "call"), 2)
                item["put"][name] = round(disc * lognormal_payoff(m, s, K, "put"), 2)
        for key in ("p_above", "call", "put"):
            item[key]["mix"] = round(trust * item[key]["nvpl"] + (1 - trust) * item[key]["risk_neutral"],
                                     4 if key == "p_above" else 2)
        res["strikes"].append(item)
    return res


# ----------------------------------------------------------------------------- output
def fmt(x) -> str:
    return f"${x:,.0f}"


def print_human(o: dict) -> None:
    p = o["primary"]
    print(f"BTC POWER LAW STATE  {o['asof']}  price {fmt(o['price'])}  [{o['price_source']}]  t={o['days_since_genesis']}d")
    print(f"  Primary {p['model']}: trend {fmt(p['trend'])} | floor ({p['floor_mult']}x) {fmt(p['floor'])} | "
          f"ratio {p['ratio']:.3f} | resid {p['resid_dex']:+.3f} dex | ZONE {p['zone']}")
    print(f"  Residual percentile: all {p['pct_rank_all']}% | since 2015 {p['pct_rank_since_2015']}% | since 2018 {p['pct_rank_since_2018']}%")
    meet = (f"{p['days_until_flat_price_meets_floor']}d ({p['flat_price_meets_floor_on']})"
            if p["flat_price_meets_floor_on"] else "already below floor")
    print(f"  Floor cushion {p['floor_cushion_pct']:+.1f}% | flat price meets floor in {meet} | "
          f"trend/floor grow {p['trend_growth_pct_per_30d']:.2f}%/30d, {p['trend_growth_pct_per_year']:.1f}%/yr")
    print(f"  Floor zone: REFIT floor {fmt(p['floor_refit'])} | SP26 floor {fmt(p['floor'])} | "
          f"QR 2.5% line {fmt(p['floor_qr025'])} (cushion {p['qr025_cushion_pct']:+.1f}%)")
    print("  Other parameterisations: " + " | ".join(
        f"{k} trend {fmt(v['trend'])} ratio {v['ratio']:.3f}" for k, v in o["models"].items() if k != PRIMARY))
    c = o["cycle"]
    print(f"  Cycle: ATH close {fmt(c['ath_close'])} on {c['ath_close_date']} ({c['ath_ratio_to_trend']}x trend), "
          f"post-ATH low {fmt(c['post_ath_low_close'])} on {c['post_ath_low_close_date']} "
          f"({c['post_ath_low_ratio_to_trend']}x, {c['drawdown_ath_to_low_pct']}%); "
          f"{c['days_since_ath']}d since ATH, {c['days_since_post_ath_low']}d since low, "
          f"{c['days_since_halving']}d since halving; next halving {c['next_halving']}")
    rv = o["realized_vol"]
    print(f"  Realized vol: 30d {rv['rv30']:.0%} | 90d {rv['rv90']:.0%} | 365d {rv['rv365']:.0%}")
    print("  Halving epochs (peak/trough ratio to SP26 trend): " + "; ".join(
        f"{e['from'][:4]}: max {e['max_ratio']}x min {e['min_ratio']}x" for e in o["halving_epochs"]))
    print("\n  Forward levels (SP26 trend & floor; REFIT trend; NvPL = out-of-sample-validated blend of"
          " today's price and trend; AR = in-sample residual regression, optimistic)")
    print(f"  {'date':10} {'days':>5} {'floor':>9} {'QR2.5%':>9} {'0.55x':>9} {'0.80x':>9} {'trend':>9} {'1.25x':>9} "
          f"{'REFIT':>9} {'NvPL':>9} {'(w)':>5} {'AR':>9}")
    for r in o["forward"]:
        print(f"  {r['date']} {r['days']:5d} {r['floor']:9,} {r['floor_qr025']:9,} {r['x0_55']:9,} {r['x0_80']:9,} {r['trend']:9,} "
              f"{r['x1_25']:9,} {r['refit_trend']:9,} {r['nvpl_forecast']:9,} {r['nvpl_weight']:5.2f} "
              f"{r.get('ar_forecast', 0):9,}")
    if "options" in o:
        q = o["options"]
        print(f"\n  OPTIONS @ {q['expiry']} ({q['days']}d) IV {q['iv_used']:.1%} [{q['iv_source']}] rate {q['rate']}")
        print(f"  floor now {fmt(q['floor_now'])} -> at expiry {fmt(q['floor_at_expiry'])}; trend at expiry {fmt(q['trend_at_expiry'])}")
        print("  median price at expiry: " + ", ".join(f"{n} {fmt(v)}" for n, v in q["median_price"].items()))
        names = list(q["median_price"]) + ["mix"]
        print(f"  (mix = {q['trust']:.0%} nvpl + {1 - q['trust']:.0%} risk_neutral; set with --trust)")
        print("  P(touch floor before expiry): " + ", ".join(f"{n} {q['p_touch_floor_before_expiry'][n]:.1%}" for n in names))
        print("  P(below floor at expiry):     " + ", ".join(f"{n} {q['p_below_floor_at_expiry'][n]:.1%}" for n in names))
        if q["strikes"]:
            hdr = "  ".join(f"{n[:5]:>5}" for n in names)
            print(f"  {'strike':>9} | P(above): {hdr} | call value: " + "  ".join(f"{n[:5]:>9}" for n in names)
                  + " | put value: " + "  ".join(f"{n[:5]:>9}" for n in names))
            for s in q["strikes"]:
                print(f"  {s['K']:9,.0f} | " + "  ".join(f"{s['p_above'][n]:5.1%}" for n in names) + "            | "
                      + "  ".join(f"{s['call'][n]:9,.0f}" for n in names) + " | "
                      + "  ".join(f"{s['put'][n]:9,.0f}" for n in names))
            print("  (risk_neutral = Black-Scholes at the IV; nvpl/ar = model expected payoff with the same IV as "
                  "dispersion. Compare the market mark to these, never act on the model alone.)")


def parse_dates(s: str | None):
    return [date.fromisoformat(x.strip()) for x in s.split(",")] if s else None


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Bitcoin Power Law (Santostasi) calculator")
    ap.add_argument("--json", action="store_true", help="print JSON only")
    ap.add_argument("--live", action="store_true", help="use live OKX index (fallback Coinbase) as today's price")
    ap.add_argument("--price", type=float, help="override the evaluation price (USD)")
    ap.add_argument("--asof", type=date.fromisoformat, help="evaluate as of YYYY-MM-DD using data up to then")
    ap.add_argument("--dates", help="comma-separated forward dates (default: next expiries)")
    ap.add_argument("--expiry", type=date.fromisoformat, help="option expiry YYYY-MM-DD for the probability block")
    ap.add_argument("--strikes", help="comma-separated strikes in USD, e.g. 60000,80000,100000")
    ap.add_argument("--iv", type=float, help="implied vol as a decimal, e.g. 0.47 (default: 90d realized)")
    ap.add_argument("--rate", type=float, default=0.0, help="continuous rate for BS/discounting (default 0)")
    ap.add_argument("--trust", type=float, default=0.5,
                    help="weight on the model-holds (nvpl) world in the 'mix' columns, 0..1 (default 0.5)")
    ap.add_argument("--offline", action="store_true", help="do not touch the network; use cached data")
    args = ap.parse_args(argv)

    rows, src = update_data(args.offline or bool(args.asof))
    if not rows:
        raise SystemExit(f"No price data: cache {CACHE} missing and download failed ({src}).")
    if args.asof:
        eval_date = args.asof
        hist = [r for r in rows if r[0] <= eval_date]
        if not hist:
            raise SystemExit("No data on or before --asof")
        price, psrc = (args.price, "user") if args.price else (hist[-1][1], f"close {hist[-1][0]} [{src}]")
    elif args.live and not args.price:
        price, psrc = live_price()
        eval_date = datetime.now(timezone.utc).date()
    elif args.price:
        price, psrc, eval_date = args.price, "user", datetime.now(timezone.utc).date()
    else:
        eval_date, price = rows[-1]
        psrc = f"daily close [{src}]"
    opt = None
    if args.expiry:
        if not 0 <= args.trust <= 1:
            raise SystemExit("--trust must be between 0 and 1")
        opt = {"expiry": args.expiry, "iv": args.iv, "rate": args.rate, "trust": args.trust,
               "strikes": [float(x) for x in args.strikes.split(",")] if args.strikes else []}
    out = analyse(rows, eval_date, price, psrc, parse_dates(args.dates), opt)
    if args.json:
        print(json.dumps(out, indent=1))
    else:
        print_human(out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
