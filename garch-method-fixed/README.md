# GARCH Method

Skill from **video 1 of the Quant Series**: *I Re-Created A Nobel Prize Quant Trading Model With Claude Code.*

> **This is a local fork** of [milesdeutscher/garchmethod](https://github.com/milesdeutscher/garchmethod) at commit `e1e93d9`, with the bug fixes listed under [Fork changes](#fork-changes). `git diff e1e93d9` shows every change.

The model family is Engle's ARCH / Bollerslev's GARCH — the volatility machinery that won the 2003 Sveriges Riksbank Prize (the economics Nobel) and that real risk desks run daily. I'm the guy who turned it into a Claude Code skill.

**One sentence:** every other trading tool tries to answer *which way*. This one answers ***how much*** — and how much is the question that decides whether you survive.

---

## Install

This fork, from its folder:

```
claude plugin marketplace add /path/to/garch-method-fixed
claude plugin install garch-method@garchmethod-fixed --scope project
```

If the upstream version is also installed, disable it (`claude plugin disable garch-method@garchmethod`) so only one `garch-method` skill loads.

Upstream (unfixed), in Claude Code:

```
/plugin marketplace add milesdeutscher/garchmethod
/plugin install garch-method@garchmethod
```

Then just ask in plain English: *"what's the vol forecast on BTC-USD"*, *"how big should my SPY position be"*, *"test my strategy with and without vol targeting"* — Claude fires the skill automatically.

No API keys. No accounts. No sudo. `scripts/garch` brings the dependencies. It uses `uv` if you have it, otherwise a private virtualenv in `~/.cache/garch-method/` that it creates on first run. Nothing is installed system-wide.

```
scripts/garch forecast --ticker BTC-USD --json
scripts/garch size     --ticker BTC-USD --json
scripts/garch compare  --ticker BTC-USD --chart equity.png
```

## What the skill does

It answers one question for **any asset**: how violent is this market, and how big should my position be?

- **Walk-forward GARCH(1,1):** parameters are re-estimated on an expanding window, and every forecast uses only data that existed before that day. Zero lookahead.
- **Volatility forecast:** 1 day ahead, daily and annualized.
- **Vol regime:** calm / normal / storm, by percentile vs the asset's own trailing year.
- **Position size:** `target_vol / forecast_vol`, capped [0.25x, 2.0x]. The default target is `auto`, the asset's trailing-1y median forecast vol, so 1.0x means "a normal day for this asset".
- **Data checks:** drops today's still-open bar, flags missing days and stale data, and reports it all in `data_quality`.
- **The honest test (`compare`):** the same strategy signals sized two ways, fixed vs vol-targeted, with both equity curves and Sharpe / max drawdown / worst month side by side. Costs and funding are not modeled.
- **EMA 9/21 crossover demo** strategy included; bring your own via a signals CSV (`date, signal in {-1,0,1}`).

It takes **either a ticker** (`--ticker BTC-USD`, fetched via `yfinance`) **or your own CSV** (`--csv my_prices.csv`, just a date + close column). That way it drops into whatever data pipeline you already run, on whatever asset you trade.

It's built to **compose**: use it as a sizing layer on a strategy you already have, a standalone risk throttle, or a comparison harness, without rewriting your strategy. See `skills/garch/SKILL.md` for the JSON contract and three worked composition patterns. It also composes cleanly with regime-direction skills (Markov-style bull/bear classifiers): theirs answers *which way*, this answers *how much*.

**What it will never do:** predict direction. GARCH forecasts the *magnitude* of moves — the skill says this in its own output, every time.

## Fork changes

Bugs found in an audit on 2026-11-13. Each fix has a regression test in `tests/test_fixes.py`, and the refit and dependency tests fail on the original code.

1. **`--ticker` was broken on a clean install.** `yfinance` was missing from every script's inline dependencies, so even the README's proof-of-life command exited with "yfinance not installed". Its suggested fix (`uv pip install yfinance`) can't reach `uv run`'s isolated environment. Every entry script now declares `yfinance`. The new `scripts/garch` launcher also works without `uv`, so an agent never has to install tooling to run the skill.
2. **Refit initialization off by one day.** At every refit (every 21 days) the recursion restarted from `res.conditional_volatility[-1]**2`, which is the variance for day t-1, so day t-1's shock was dropped. It now restarts from arch's own one-step forecast for day t, and matches arch's fixed-parameter forecast exactly. On BTC the old version was off by a median 4% (worst 46%) on refit days, largest right after big moves. Example: 51.6% instead of 96.1% for the forecast made on 2019-05-12.
3. **No data validation.** Today's still-open bar was used as if it were a final close, and missing days in 24/7 markets turned multi-day moves into one giant "daily" shock. On 2026-11-13 that turned a normal ~49% reading into a 62% STORM. Now:
   - Still-open bars are dropped.
   - A return across a gap is scaled by 1/sqrt(days) for the variance model, while P&L keeps true returns.
   - Gaps and stale data are reported in `data_quality`, with `ok: false` when the latest forecast shouldn't drive a live decision.
4. **The default target vol (15%) was a stock-index setting.** On BTC it pinned the size at the 0.25x floor 45% of the time. The default is now `auto` (the trailing-1y median forecast vol); fixed targets still work via `--target-vol`.
5. **Smaller fixes:**
   - The regime percentile counted warm-up NaNs as "lower", so "calm" was overstated for the first year of forecasts.
   - `compare` dated each return to the day before it happened.
   - `size` didn't output the regime that composition pattern B needs.
   - SKILL.md's JSON example didn't match the code.
   - The storm-gauge Pine script used a 252-bar "1-year" percentile with crypto's 365-day default and the same 15% target. It now defaults to 365 bars and an automatic target. The Pine changes have not been compiled in TradingView.

Run the tests (needs the dependencies, e.g. the launcher's venv):

```
~/.cache/garch-method/venv/bin/python -m unittest discover -s tests -v
```

## The on-camera build / zero-trust manual path

`garch-method.md` is the original one-shot onboarding prompt, the version built **live on camera**. It fetches the **upstream** files from GitHub, so it installs the unfixed code, not this fork.

## Pine Script bonus — the Storm Gauge

`pine-script/storm-gauge.pine` is a TradingView v5 indicator that puts the framework on a chart:
- vol bands on price, so you can *see* the clustering
- a storm tint
- a corner gauge showing market violence vs normal (or your fixed limit), its 1-year percentile, the weather, and — if you enter — your size, in dollars

Honest label, on the panel itself: the gauge uses *realized* vol (Pine-native math). The walk-forward GARCH *forecast* lives in the skill — the chart tells you how violent the market is; the skill forecasts tomorrow.

Open TradingView → Pine Editor → paste the `.pine` → Save → Add to Chart. **Use the daily chart.** Inputs: 365 periods/year and a 365-bar percentile lookback for crypto, 252 / 252 for stocks.

## Credit

- **Model family:** Robert Engle (ARCH, Nobel 2003) and Tim Bollerslev (GARCH, 1986). Read the originals — the math is theirs.
- **Skill + installer + Pine:** Miles Deutscher.
- **Fork fixes:** see [Fork changes](#fork-changes).

## License

MIT.
