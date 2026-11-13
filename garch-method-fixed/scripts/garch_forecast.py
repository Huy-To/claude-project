# /// script
# requires-python = ">=3.10"
# dependencies = ["arch>=6.0", "pandas>=2.2", "numpy>=1.24", "matplotlib>=3.7", "yfinance>=0.2.54"]
# ///
"""
garch_forecast.py — walk-forward GARCH(1,1) volatility forecasting.

What this does:
  Fits a GARCH(1,1) model walk-forward (no lookahead) and produces a
  1-day-ahead volatility forecast for every day in the sample.

What this does NOT do:
  Predict direction. GARCH forecasts the MAGNITUDE of moves, not which
  way they go. Every output of this module carries that note on purpose.

Usage (the launcher brings its own dependencies, with or without uv):
  scripts/garch forecast --ticker BTC-USD            # via yfinance (needs internet)
  scripts/garch forecast --csv prices.csv --json     # date + close columns
  uv run scripts/garch_forecast.py --ticker BTC-USD  # also fine when uv is installed
"""

import argparse
import json
import sys
import time
import warnings

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")

TRADING_DAYS_CRYPTO = 365
TRADING_DAYS_EQUITY = 252
MIN_TRAIN = 500          # days of history before first forecast
REFIT_EVERY = 21         # re-estimate params every N days (walk-forward)
REGIME_LOOKBACK = 365    # window for vol percentile / regime classification
MIN_PCTILE_OBS = 90      # earlier forecasts required before a percentile is reported
EQUITY_FINAL_NY = (16, 30)  # a US-equity daily bar counts as final after 16:30 New York time
RECENT_DAYS = 30         # a data gap this recent makes the latest forecast unreliable

HONESTY_NOTE = "GARCH forecasts magnitude (volatility), not direction. It tells you how violent tomorrow is likely to be — not which way it goes."


# ---------------------------------------------------------------------- data
def add_data_args(ap: argparse.ArgumentParser) -> None:
    """Data arguments shared by every script."""
    ap.add_argument("--csv", help="CSV with a date column and a close/price column")
    ap.add_argument("--ticker", help="yfinance ticker, e.g. BTC-USD (needs internet)")
    ap.add_argument("--periods-per-year", type=int, default=TRADING_DAYS_CRYPTO,
                    help="365 for crypto / 24-7 markets (default), 252 for stocks; "
                         "also picks the calendar the data checks use")
    ap.add_argument("--keep-partial", action="store_true",
                    help="keep today's still-open bar (default: drop it, its close is not final)")


def _download_yf(ticker: str, tries: int = 3) -> pd.DataFrame:
    try:
        import yfinance as yf
    except ImportError:
        sys.exit("yfinance is not installed in this environment. Run through the launcher "
                 "(scripts/garch ...) or `uv run`, which both install it, or pass --csv.")
    data, err = None, None
    for attempt in range(tries):
        try:
            data = yf.download(ticker, period="max", auto_adjust=True, progress=False)
        except Exception as e:  # network errors, rate limits
            data, err = None, e
        if data is not None and len(data):
            break
        if attempt < tries - 1:
            time.sleep(2 * (attempt + 1))
    else:
        sys.exit(f"yfinance returned no data for {ticker!r} (rate-limited, offline or unknown ticker"
                 f"{': ' + str(err) if err else ''}). Retry later or pass --csv.")
    if isinstance(data.columns, pd.MultiIndex):  # newer yfinance returns (field, ticker) columns
        data.columns = data.columns.get_level_values(0)
    data = data.reset_index()
    out = data[[data.columns[0], "Close"]].copy()
    out.columns = ["date", "close"]
    return out


def validate_prices(raw: pd.DataFrame, periods_per_year: int = TRADING_DAYS_CRYPTO,
                    keep_partial: bool = False, now=None):
    """
    Clean a daily date/close series and report what was wrong with it.

    - drops unreadable rows, non-positive prices and duplicate dates (keeps the last)
    - drops today's still-open bar (crypto: the current UTC day; stocks: today before
      16:30 New York), because its "close" is only the latest trade
    - 24/7 markets: finds missing calendar days. The row after a gap gets
      span = calendar days its return covers, so walkforward_garch can scale that
      return to a one-day shock instead of reading it as one huge daily move
    - reports how many bars the latest complete bar is behind

    Returns (DataFrame[date, close, span], quality). quality["ok"] is False when a
    recent gap or a stale last bar makes the latest forecast unfit for live decisions.
    """
    issues, notes, recent_missing = [], [], []
    df = raw[["date", "close"]].copy()
    df["close"] = pd.to_numeric(df["close"], errors="coerce")
    d = pd.to_datetime(df["date"], errors="coerce")
    if isinstance(d.dtype, pd.DatetimeTZDtype):
        d = d.dt.tz_localize(None)  # keep the market's own calendar date
    df["date"] = d.dt.normalize()
    bad = df["date"].isna() | df["close"].isna() | (df["close"] <= 0)
    if bad.any():
        notes.append(f"dropped {int(bad.sum())} row(s) with an unreadable date or a missing/non-positive close")
    df = df[~bad].sort_values("date", kind="stable")
    dup = df["date"].duplicated(keep="last")
    if dup.any():
        notes.append(f"{int(dup.sum())} duplicate date(s): kept the last row of each")
    df = df[~dup].reset_index(drop=True)
    if df.empty:
        sys.exit("No usable price rows after cleaning.")

    crypto = periods_per_year >= TRADING_DAYS_CRYPTO
    now = pd.Timestamp.now(tz="UTC") if now is None else pd.Timestamp(now)
    if now.tzinfo is None:
        now = now.tz_localize("UTC")
    if crypto:
        today = now.tz_convert("UTC").tz_localize(None).normalize()
        last_final = today - pd.Timedelta(days=1)
    else:
        ny = now.tz_convert("America/New_York")
        today = ny.tz_localize(None).normalize()
        last_final = today if (ny.hour, ny.minute) >= EQUITY_FINAL_NY else today - pd.Timedelta(days=1)

    still_open = df["date"] > last_final
    if still_open.any():
        days = ", ".join(x.date().isoformat() for x in df.loc[still_open, "date"])
        if keep_partial:
            notes.append(f"kept still-open bar {days} (--keep-partial): its return is not a full day")
        else:
            notes.append(f"dropped still-open bar {days}: its close is not final yet (--keep-partial keeps it)")
            df = df[~still_open].reset_index(drop=True)
    if df.empty:
        sys.exit("No complete price bars left after dropping the still-open bar.")

    gap = df["date"].diff().dt.days.fillna(1).astype(int).clip(lower=1)
    if crypto:
        span = gap
        missing = []
        for i in np.flatnonzero(span.to_numpy() > 1):
            missing.extend(pd.date_range(df.at[i - 1, "date"] + pd.Timedelta(days=1),
                                         df.at[i, "date"] - pd.Timedelta(days=1), freq="D"))
        if missing:
            cutoff = last_final - pd.Timedelta(days=RECENT_DAYS)
            recent_missing = [x.date().isoformat() for x in missing if x > cutoff]
            msg = (f"{len(missing)} missing calendar day(s) in a 24/7 market (latest {missing[-1].date()}); "
                   "a return spanning a gap is scaled by 1/sqrt(days) before it enters GARCH")
            (issues if recent_missing else notes).append(msg)
    else:
        span = pd.Series(1, index=df.index)  # weekends and holidays are normal for session markets
        long_gap = gap > 5
        if long_gap.any():
            latest = df.loc[long_gap, "date"].iloc[-1]
            msg = (f"{int(long_gap.sum())} gap(s) longer than 5 calendar days "
                   f"(latest ending {latest.date()}): check the data")
            (issues if latest > last_final - pd.Timedelta(days=RECENT_DAYS) else notes).append(msg)
    df["span"] = span.to_numpy()

    last = df["date"].iloc[-1]
    if crypto:
        behind = max(0, int((last_final - last).days))
    else:
        behind = max(0, int(np.busday_count((last + pd.Timedelta(days=1)).date(),
                                            (last_final + pd.Timedelta(days=1)).date())))
    if behind:
        issues.append(f"latest complete bar is {last.date()}, {behind} bar(s) behind {last_final.date()}"
                      + ("" if crypto else " (or a market holiday)"))

    quality = {"ok": not issues, "last_bar": last.date().isoformat(), "bars_behind": behind,
               "issues": issues, "notes": notes}
    if recent_missing:
        quality["missing_days_recent"] = recent_missing
    return df, quality


def load_prices(csv=None, ticker=None, periods_per_year: int = TRADING_DAYS_CRYPTO,
                keep_partial: bool = False, now=None) -> pd.DataFrame:
    """Load a daily price series from CSV (date + close) or yfinance, then validate it.

    The data-quality report from validate_prices is attached as df.attrs["data_quality"].
    """
    if csv:
        df = pd.read_csv(csv)
        cols = {c.lower().strip(): c for c in df.columns}
        date_col = next((cols[k] for k in ("date", "time", "timestamp") if k in cols), df.columns[0])
        px_col = next((cols[k] for k in ("close", "price", "priceusd", "adj close", "adj_close") if k in cols), df.columns[1])
        raw = df[[date_col, px_col]].copy()
        raw.columns = ["date", "close"]
    elif ticker:
        raw = _download_yf(ticker)
    else:
        sys.exit("Provide --csv or --ticker")

    out, quality = validate_prices(raw, periods_per_year, keep_partial=keep_partial, now=now)
    out.attrs["data_quality"] = quality
    return out


# --------------------------------------------------------------------- model
def _pctile(w: np.ndarray) -> float:
    """Percentile of the newest forecast vs the earlier ones in the window, ignoring NaNs."""
    cur, prior = w[-1], w[:-1]
    prior = prior[~np.isnan(prior)]
    if np.isnan(cur) or len(prior) < MIN_PCTILE_OBS:
        return np.nan
    return float((prior < cur).mean() * 100.0)


def walkforward_garch(prices: pd.DataFrame, periods_per_year: int = TRADING_DAYS_CRYPTO,
                      min_train: int = MIN_TRAIN, refit_every: int = REFIT_EVERY) -> pd.DataFrame:
    """
    Walk-forward GARCH(1,1). For each day t >= min_train, forecast the vol of
    day t+1 using ONLY data available at the close of day t.

    Params are re-estimated every `refit_every` days on an expanding window.
    At a refit the recursion restarts from the model's variance for day t
    (built from data through t-1) and then takes day t's shock. Between refits
    it rolls forward with the last fitted params — still zero lookahead,
    because params were estimated on strictly prior data.

    If `prices` has a `span` column (load_prices adds it), a return covering
    several calendar days is scaled by 1/sqrt(span) before it enters the
    model, so a data gap isn't read as one giant daily shock. `ret` stays the
    true return.

    Returns a DataFrame indexed like `prices` (minus the first row) with:
      ret          — daily % return
      span         — calendar days the return covers (1 unless the data has gaps)
      fcast_vol    — 1-day-ahead conditional vol forecast (daily, %)
      fcast_vol_ann— annualized forecast vol (%)
      vol_pctile   — percentile of today's forecast vs the trailing REGIME_LOOKBACK
      regime       — calm / normal / storm (bottom / middle / top third of that window)
    """
    from arch import arch_model

    px = prices["close"].to_numpy(dtype=float)
    rets = 100.0 * np.diff(px) / px[:-1]           # daily % returns, scaled for arch
    n = len(rets)
    if n < min_train + 10:
        sys.exit(f"Need at least {min_train + 11} days of prices; got {n + 1}.")
    span = prices["span"].to_numpy(dtype=float)[1:] if "span" in prices.columns else np.ones(n)
    shocks = rets / np.sqrt(span)                   # one-day-equivalent returns for the variance model

    fcast_var = np.full(n, np.nan)                  # forecast of NEXT day's variance, made at t
    mu = omega = alpha = beta = sigma2 = None

    for t in range(min_train, n):
        if (t - min_train) % refit_every == 0:
            am = arch_model(shocks[:t], vol="GARCH", p=1, q=1, mean="Constant", dist="t")
            res = am.fit(disp="off", show_warning=False)
            p = res.params
            mu, omega, alpha, beta = p["mu"], p["omega"], p["alpha[1]"], p["beta[1]"]
            # Variance for day t given data through t-1. (v1.0.0 restarted from
            # res.conditional_volatility[-1]**2, which is day t-1's variance, so
            # day t-1's shock was dropped at every refit.)
            sigma2 = float(res.forecast(horizon=1).variance.to_numpy()[-1, 0])
        eps = shocks[t] - mu
        sigma2 = omega + alpha * eps ** 2 + beta * sigma2   # variance for day t+1
        fcast_var[t] = sigma2                               # made at close of t, for day t+1

    out = prices.iloc[1:].copy().reset_index(drop=True)
    out["ret"] = rets
    out["span"] = span.astype(int)
    out["fcast_vol"] = np.sqrt(fcast_var)                                # daily %
    out["fcast_vol_ann"] = out["fcast_vol"] * np.sqrt(periods_per_year)  # annualized %
    out["vol_pctile"] = out["fcast_vol"].rolling(REGIME_LOOKBACK, min_periods=1).apply(_pctile, raw=True)
    out["regime"] = pd.cut(out["vol_pctile"], bins=[-1, 33, 67, 101],
                           labels=["calm", "normal", "storm"])
    return out


# ------------------------------------------------------------------- helpers
def next_period(day: pd.Timestamp, periods_per_year: int) -> str:
    """The day a forecast made at `day`'s close is for."""
    if periods_per_year >= TRADING_DAYS_CRYPTO:
        return (day + pd.Timedelta(days=1)).date().isoformat()
    return str(np.busday_offset(day.date(), 1, roll="forward"))


def num_or_none(x, nd: int = 1):
    return None if pd.isna(x) else round(float(x), nd)


def label_or_none(x):
    return None if pd.isna(x) else str(x)


def print_quality(quality: dict) -> None:
    for msg in quality.get("issues", []):
        print(f"  ⚠ DATA: {msg}")
    for msg in quality.get("notes", []):
        print(f"  · data: {msg}")


def main():
    ap = argparse.ArgumentParser(description="Walk-forward GARCH(1,1) 1-day volatility forecast.")
    add_data_args(ap)
    ap.add_argument("--json", action="store_true", help="print latest forecast as JSON")
    ap.add_argument("--out-csv", help="write full walk-forward series to CSV")
    args = ap.parse_args()

    prices = load_prices(csv=args.csv, ticker=args.ticker, periods_per_year=args.periods_per_year,
                         keep_partial=args.keep_partial)
    quality = prices.attrs["data_quality"]
    res = walkforward_garch(prices, periods_per_year=args.periods_per_year)
    latest = res.dropna(subset=["fcast_vol"]).iloc[-1]

    payload = {
        "asset": args.ticker or args.csv,
        "as_of": str(latest["date"].date()),
        "forecast_for": next_period(latest["date"], args.periods_per_year),
        "forecast_vol_daily_pct": round(float(latest["fcast_vol"]), 3),
        "forecast_vol_annualized_pct": round(float(latest["fcast_vol_ann"]), 1),
        "vol_percentile_1y": num_or_none(latest["vol_pctile"]),
        "regime": label_or_none(latest["regime"]),
        "data_quality": quality,
        "note": HONESTY_NOTE,
    }
    if args.out_csv:
        res.to_csv(args.out_csv, index=False)
        payload["series_csv"] = args.out_csv
    if args.json:
        print(json.dumps(payload, indent=2))
    else:
        print(f"\n  {payload['asset']} — as of {payload['as_of']} (forecast for {payload['forecast_for']})")
        print(f"  1-day vol forecast : {payload['forecast_vol_daily_pct']}% daily "
              f"({payload['forecast_vol_annualized_pct']}% annualized)")
        print(f"  vol percentile (1y): {payload['vol_percentile_1y']}")
        print(f"  regime             : {(payload['regime'] or 'n/a').upper()}")
        if quality["issues"] or quality["notes"]:
            print()
            print_quality(quality)
        print(f"\n  ⚠ {HONESTY_NOTE}\n")


if __name__ == "__main__":
    main()
