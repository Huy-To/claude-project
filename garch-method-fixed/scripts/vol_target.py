# /// script
# requires-python = ">=3.10"
# dependencies = ["arch>=6.0", "pandas>=2.2", "numpy>=1.24", "yfinance>=0.2.54"]
# ///
"""
vol_target.py — turn a volatility forecast into a position size.

The entire idea in one line:
    size = target_vol / forecast_vol      (capped so it never does anything insane)

Storm coming -> smaller position. Calm ahead -> bigger position.
Same trades, different sizes. This is the "how much" answer.

Target: 'auto' (default) is the asset's own trailing-1y median forecast vol, so
1.0x means "a normal day for this asset". Pass a number for a fixed annualized
target instead (15 suits a stock index; BTC usually runs 45-60).

Usage:
  scripts/garch size --ticker BTC-USD --json
  scripts/garch size --csv prices.csv --target-vol 40
"""

import argparse
import json

import numpy as np
import pandas as pd

MAX_LEVERAGE = 2.0
MIN_SIZE = 0.25
AUTO_LOOKBACK = 365   # days of past forecasts behind the 'auto' target


def parse_target(value):
    """argparse type for --target-vol: 'auto' or a positive annualized %."""
    if str(value).strip().lower() == "auto":
        return "auto"
    try:
        v = float(value)
    except ValueError:
        raise argparse.ArgumentTypeError("must be 'auto' or a number") from None
    if v <= 0:
        raise argparse.ArgumentTypeError("must be positive")
    return v


def auto_target(fcast_vol_ann: pd.Series, lookback: int = AUTO_LOOKBACK) -> pd.Series:
    """Trailing median of the forecast vol, using only forecasts made up to each date."""
    return fcast_vol_ann.rolling(lookback, min_periods=1).median()


def size_from_vol(forecast_vol_ann: float, target_vol_ann: float = 15.0,
                  max_leverage: float = MAX_LEVERAGE, min_size: float = MIN_SIZE) -> float:
    """Position size multiplier from an annualized vol forecast (%)."""
    if forecast_vol_ann is None or np.isnan(forecast_vol_ann) or forecast_vol_ann <= 0:
        return min_size
    return float(np.clip(target_vol_ann / forecast_vol_ann, min_size, max_leverage))


def size_series(fcast_vol_ann: pd.Series, target_vol_ann=15.0,
                max_leverage: float = MAX_LEVERAGE, min_size: float = MIN_SIZE) -> pd.Series:
    """Vectorized version for backtests. `target_vol_ann` may be a number or a Series (auto_target)."""
    s = target_vol_ann / fcast_vol_ann
    return s.clip(lower=min_size, upper=max_leverage).fillna(min_size)


def main():
    from garch_forecast import (HONESTY_NOTE, add_data_args, label_or_none, load_prices,
                                next_period, num_or_none, print_quality, walkforward_garch)

    ap = argparse.ArgumentParser(description="Position-size multiplier from a walk-forward GARCH vol forecast.")
    add_data_args(ap)
    ap.add_argument("--target-vol", type=parse_target, default="auto",
                    help="annualized target vol %% or 'auto' (default): the asset's trailing-1y "
                         "median forecast vol, so 1.0x = normal conditions for this asset")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    prices = load_prices(csv=args.csv, ticker=args.ticker, periods_per_year=args.periods_per_year,
                         keep_partial=args.keep_partial)
    quality = prices.attrs["data_quality"]
    res = walkforward_garch(prices, periods_per_year=args.periods_per_year)
    valid = res.dropna(subset=["fcast_vol"])
    latest = valid.iloc[-1]
    auto = args.target_vol == "auto"
    target = float(auto_target(valid["fcast_vol_ann"]).iloc[-1]) if auto else args.target_vol
    mult = size_from_vol(float(latest["fcast_vol_ann"]), target)

    payload = {
        "asset": args.ticker or args.csv,
        "as_of": str(latest["date"].date()),
        "forecast_for": next_period(latest["date"], args.periods_per_year),
        "forecast_vol_annualized_pct": round(float(latest["fcast_vol_ann"]), 1),
        "target_vol_pct": round(target, 1),
        "target_mode": "auto (trailing-1y median forecast vol)" if auto else "fixed",
        "position_size_multiplier": round(mult, 2),
        "read_as": f"run {mult:.2f}x your baseline position size",
        "caps": {"max": MAX_LEVERAGE, "min": MIN_SIZE},
        "vol_percentile_1y": num_or_none(latest["vol_pctile"]),
        "regime": label_or_none(latest["regime"]),
        "data_quality": quality,
        "note": HONESTY_NOTE,
    }
    if args.json:
        print(json.dumps(payload, indent=2))
    else:
        print(f"\n  as of {payload['as_of']}: forecast vol {payload['forecast_vol_annualized_pct']}% "
              f"vs target {payload['target_vol_pct']}% ({'auto' if auto else 'fixed'})")
        print(f"  → position size: {payload['position_size_multiplier']}x baseline "
              f"(regime {(payload['regime'] or 'n/a').upper()})\n")
        print_quality(quality)
        print()


if __name__ == "__main__":
    main()
