---
categories:
  - "[[Knowledge]]"
summary: When to trust the GARCH forecast (a rough one-day gauge for spot and perp size) and when not to (options, 7 to 30 day horizons, bad data). QuantClaude's audit of the garch-method plugin.
source: QuantClaude session, 2026-11-13; evidence in claude-project/garch-eval
created: 2026-11-13
---
## Use this when
A question involves the GARCH forecast, the calm / normal / storm regime, the position-size multiplier, or "how much should I put on".

## Rules
1. **GARCH forecasts the size of moves, never the direction.** Say so whenever you present it.
2. **It is a sizing gauge for spot and perps, not an options input.** For anything about option pricing or 7 to 30 day horizons, use DVOL and the chain's IV ([[Reading volatility]]).
3. **Never act on a reading whose `data_quality.ok` is false.** State the issue it lists instead.
4. **The regime is relative.** Calm, normal and storm are the bottom, middle and top third of BTC's own past year. "Storm" means high for BTC lately, not high in absolute terms.
5. **Never size up into a storm.** A multiplier above 1 in a storm regime is ignored.
6. **The multiplier scales a baseline size; it does not create one.** It ranges from 0.25x to 2x and applies to linear exposure only.
7. **Treat it as roughly an EWMA.** It adds little beyond a moving average of recent volatility.

## Evidence
Audit of garch-method (Miles Deutscher, commit e1e93d9), tested out of sample from January 2016 to November 2026:
- **One day ahead:** slightly better than a plain EWMA (0.94), not significantly (QLIKE 3.303 against 3.340).
- **7 to 30 days ahead:** worse than EWMA and biased high by 4 to 9 volatility points.
- **Against DVOL at 30 days:** DVOL explains more of the next month's realized volatility (R2 0.37 against 0.17), and GARCH adds nothing on top of DVOL.
- **The fit:** with the expanding window, alpha plus beta sits at 1.00, so the model never expects volatility to settle. That is why it behaves like an EWMA.
- **Vol targeting:** no significant Sharpe gain after costs. With the corrected automatic target, the demo strategy went from 1.35 to 1.37.

## What the fixed fork changed
The app runs the fork QuantClaude fixed (`garch-method-fixed`), not the original:
- the refit off-by-one is gone (the original was wrong by a median 4% and up to 46% on refit days);
- the still-forming daily bar is dropped, and gaps and stale data are flagged in `data_quality`;
- the default target is the trailing-year median forecast, not a stock-market 15%.

## How the app runs it
`garch_forecast` feeds the plugin about four years of OKX daily closes, which are complete, so `data_quality.ok` is normally true. On Yahoo data the same model read "storm" from a missing day plus an unfinished bar.

## Limits
One asset (BTC), daily bars, one-day horizon. Not validated intraday. Past cycles were far more volatile than 2025 to 2026.
