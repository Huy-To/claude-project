---
name: garch-method
description: Volatility forecasting and position sizing via walk-forward GARCH(1,1). Use whenever the user asks about volatility forecasts, position sizing, "how much should I put on", vol targeting, risk throttling, storm/calm regimes, or wants to test whether vol-targeted sizing improves an existing strategy. Works on any ticker (yfinance) or any CSV with date + close columns. Answers "how much" — never "which way".
---

# GARCH Method — volatility forecasting + position sizing

This skill answers the question retail never asks and every fund asks daily: **how much?**

It does NOT predict direction. GARCH forecasts the *magnitude* of moves — how violent tomorrow is likely to be, not which way it goes. Say this to the user whenever presenting results.

## Running the tools

One launcher runs every tool and brings its own dependencies. It uses `uv` when installed, otherwise a private virtualenv at `~/.cache/garch-method/venv` that it creates on first run (about a minute). Never install `uv` or Python packages system-wide for this skill.

```
G="${CLAUDE_SKILL_DIR}/../../scripts/garch"     # scripts/ sits two levels above this skill's folder
bash "$G" forecast --ticker BTC-USD --json
bash "$G" size     --ticker BTC-USD --json
bash "$G" compare  --ticker BTC-USD --chart equity.png --json
```

Every command also takes `--csv prices.csv` (a date column plus a close/price column) instead of `--ticker`.

### 1. `forecast` — the forecast (garch_forecast.py)
Walk-forward GARCH(1,1) with Student-t errors and zero lookahead. Parameters are re-estimated every 21 days on an expanding window, and between refits the recursion rolls forward using only past data.

Output: the 1-day-ahead vol forecast (daily and annualized), its percentile vs the trailing year, and a regime. The regime is calm / normal / storm, meaning the bottom / middle / top third of the asset's own past year. It is relative, not absolute.

### 2. `size` — the size (vol_target.py)
The whole idea: `size = target_vol / forecast_vol`, capped at [0.25x, 2.0x].

The default target is `auto`: the asset's own trailing-1y median forecast vol. So 1.0x means a normal day for this asset, below 1 means stormier than usual, and above 1 means calmer. `--target-vol 40` sets a fixed annualized target instead.

Output: a position-size multiplier ("run 0.93x your baseline"), plus the regime.

### 3. `compare` — the honest test (compare.py)
Runs the same signals twice, at fixed size and vol-targeted, and shows both equity curves plus the stats side by side. It ships with an EMA 9/21 crossover demo. For any other strategy pass `--signals mine.csv` with columns `date, signal` (signal in {-1, 0, 1}, known at that day's close and applied to the next day's return).

Output: CAGR, ann vol, Sharpe, max drawdown, worst month and final equity for both versions, plus a chart with storm regimes shaded.

Costs, slippage and funding are **not** modeled. The vol-targeted book re-sizes daily and trades more, so small Sharpe gains may not survive real trading.

## Data checks — read before using any number

Every output carries `data_quality`:
- **Still-open bar dropped:** today's bar is removed automatically, because its close is not final (the current UTC day for crypto; before 16:30 New York for stocks). `--keep-partial` overrides this.
- **Missing days flagged:** in 24/7 markets, a return that spans a gap is scaled by 1/sqrt(days) before it enters GARCH, so the gap is not read as one giant daily move.
- **`ok: false`:** means a recent gap or a stale last bar. Tell the user, and do **not** use the numbers for a live sizing decision until the data is complete. For example, pass `--csv` with prices from the exchange you trade on.
- **`as_of` / `forecast_for`:** `as_of` is the last complete bar the forecast uses; `forecast_for` is the day it applies to.

## JSON contract

`forecast`:
```json
{
  "asset": "BTC-USD",
  "as_of": "2026-11-12",
  "forecast_for": "2026-11-13",
  "forecast_vol_daily_pct": 2.587,
  "forecast_vol_annualized_pct": 49.4,
  "vol_percentile_1y": 61.5,
  "regime": "normal",
  "data_quality": {"ok": true, "last_bar": "2026-11-12", "bars_behind": 0, "issues": [], "notes": []},
  "note": "GARCH forecasts magnitude (volatility), not direction. ..."
}
```

`size` adds `target_vol_pct`, `target_mode`, `position_size_multiplier` (e.g. 0.93 for 45.9 / 49.4), `read_as` and `caps`, and repeats the regime and `data_quality`.

## Three composition patterns

**A. Sizing layer** — bolt onto any existing strategy. Your strategy decides *if*; this skill decides *how much*. Multiply the strategy's position by `position_size_multiplier`.

**B. Risk throttle** — when `regime == "storm"`, cap exposure at `min(1.0, position_size_multiplier)`, so you never size up into a storm. A storm label can come with a multiplier above 1 right after an unusually calm year, because the regime is relative to that year.

**C. Comparison harness** — before trusting any strategy, run it through `compare` and check whether vol targeting improves its Sharpe and drawdown. If sizing doesn't help, the strategy's edge may be too weak to survive real conditions.

This skill pairs with regime-direction skills (e.g. Markov-style bull/bear classifiers): theirs answers *which way*, this one answers *how much*.

## Defaults & conventions

- **Calendar:** crypto uses `--periods-per-year 365` (the default), stocks use `252`. This also picks the calendar the data checks use (24/7 vs weekday sessions).
- **Target:** `auto` by default. Fixed targets are absolute annualized %. 15% suits a stock index; BTC usually runs 45–60%, so 15% would pin the size at the 0.25x floor.
- **Linear exposure only:** the multiplier sizes spot, futures and perps. It does not translate to options, which you size by premium at risk and greeks. For BTC's 7–30 day realized vol, implied vol (e.g. Deribit DVOL) has been the stronger predictor; this forecast is a 1-day-ahead number.
- **Minimum history:** ~510 daily observations before the first forecast.

## Honesty rules (non-negotiable)

1. Never present GARCH output as a direction call.
2. Never hide the drawdown or worst-month numbers when reporting a comparison.
3. If vol targeting does NOT improve the user's strategy, say so plainly — that result is just as valuable.
4. Never hide or skip over `data_quality` issues.
