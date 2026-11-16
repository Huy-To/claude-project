# btcliq ETF Flows tab: server work for the engineer

**From:** the ETF Flows tab UI/UX redesign, 2026-11-15 (patch `btcliq-etf-flows-ui.patch`, `index.html` only).
**Scope of this note:** server and data work only. The redesigned page works without any of it. Each item says whether the page picks it up on its own or needs a small page change. The page side is UI/UX work: hand it back to the UI session.

## What the page uses today

| Part of the page | Data it reads | What's missing |
|---|---|---|
| Hero bars, regime, streak, 2σ marks, cumulative strip | `published.total_btc` (daily net BTC, all funds, since 2024-01-11) | Nothing for BTC. Dollars exist only for the last ~20 days (`published.total_usd`) |
| Price strip, flow-vs-price correlations | `GET /api/candles?tf=1D` (1000 daily OKX candles, so from 2024-02-20) | 2024-01-11 to 2024-02-19 has no price |
| Fund sparklines, day drawer per fund, hover "top funds" | `published.by_fund` | Only the last 8 days. Older days fall back to the issuer split |
| Issuer chart (bitcoin held / share) | `published.issuer_btc` plus each fund's `holdings_btc` | The levels are rebuilt backwards from today's holdings. The early-2024 values drift: BlackRock starts at about 24k BTC instead of 0, and Others goes slightly negative (clamped to 0) |
| Event marks | `GET /api/calendar` events with impact 3 (FOMC, CPI, Jobs, PCE) | The calendar only reaches back to October 2026 |
| Premium / discount | `funds[].premium_pct` (today only) | No history |

## 1. Dollar flow since launch (most wanted)

**Page change needed:** no, if you lengthen `total_usd` in place.

The user chose to see **both units side by side** everywhere, and the hero opens on **since launch**. Today, dollars stop after 20 days.

- Add `published.total_usd_full: [[day_ms, usd], ...]` back to 2024-01-11, or extend `total_usd` itself.
  - The best source is CoinMarketCap's own history, if the endpoint pages back. The alternative is bitbo's BTC per day × that day's close, marked as derived.
- The page reads `total_usd`. If you lengthen it in place, nothing else changes.

Today the page converts in one place: for days that have a dollar figure but no BTC figure yet (bitbo publishes a day or two later), it draws a faded bar at dollars ÷ that day's close and marks it ≈.

## 2. Per-fund history since launch

**Page change needed:** none for the sparklines and drawer if you lengthen `by_fund` in place; the sparklines already cap at 20 days. Moving the issuer chart to a per-fund build is a page change.

- Extend `published.by_fund` to the whole history (it already exists on bitbo; the snapshot keeps 8 days).
- Or add `published.by_fund_full` and keep the websocket payload small. The page would need a one-line switch to read it.
- With that:
  - the sparklines can show 20 days
  - the day drawer shows funds for any day
  - the issuer chart can be built per fund, without the drift.

## 3. Holdings at launch

**Page change needed:** yes, small (`efIssBuild` anchors on it).

To anchor the issuer chart exactly, add `funds[].holdings_at_launch_btc`, or a per-fund `holdings_btc` series. GBTC's 2024-01-11 balance (about 619k BTC) is the one that matters.

## 4. Daily prices back to 2024-01-11

**Page change needed:** no if you raise the 1D candle limit; yes if you add `published.px`.

`/api/candles?tf=1D` returns `CHART_BARS = 1000` candles. Either raise the limit for `1D` only, or add `published.px: [[day_ms, close], ...]` to the ETF snapshot so the tab doesn't depend on the chart's candle cache.

## 5. Premium history

**Page change needed:** yes (a new chart or a sparkline column).

Store `premium_pct` once a day per fund (after the US close) and expose `published.premium: {ticker: [[day_ms, pct], ...]}`. A widening discount over days is the useful signal; today's value alone is weak.

## 6. Calendar history

**Page change needed:** yes for `history=1` (the page asks `/api/calendar` once); no if past events are added to the normal response.

`/api/calendar` serves only upcoming and recent events. To mark FOMC and CPI days across the whole hero chart, add a `history=1` option, or include a static list of past FOMC/CPI/Jobs/PCE dates since 2024.

## Known data quirk

bitbo (BTC) and CoinMarketCap (USD) sometimes disagree on the direction of a day. On 2026-11-11, bitbo shows +427 BTC and CoinMarketCap shows −$308.6M. The page draws such days faded and says so in the hover readout and the day drawer. If you can tell which fund causes it (usually a late reporter), consider a `published.notes` field per day.
