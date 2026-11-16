---
categories:
  - "[[Knowledge]]"
summary: How to read realized volatility, DVOL and option IV together, which to use for which question, and the caveats of each.
source: QuantClaude audit (garch-eval) and the app's volatility lookup
created: 2026-11-13
---
## Use this when
A question is about whether options are cheap or expensive, how big a move is priced, or which volatility number to trust.

## The three numbers

| Number | What it is | From | Best for |
|---|---|---|---|
| Realized | What BTC actually did: annualized close-to-close volatility over 7 to 90 days | `volatility` (OKX daily closes) | Context: what just happened |
| DVOL | Deribit's 30-day implied volatility index | `volatility` | The market's 30-day expectation; the best single predictor of the next month's realized volatility |
| ATM IV per expiry | Implied volatility of the at-the-money option at each OKX expiry | `volatility`, `options_expiries` | Pricing a specific expiry; the term structure |

## Rules
1. **For a specific option, use that contract's own IV**, then its expiry's ATM IV. DVOL is a 30-day average, not that contract.
2. **Convert before comparing.** A daily move is about annual volatility / 19.1. A move to expiry is about IV x sqrt(days / 365). `volatility` gives both.
3. **Implied usually sits above realized.** The gap is the price of insurance, not a mispricing by itself. In the audit, DVOL ran about 11 points above the next 7 days' realized volatility on average.
4. **Compare like with like.** Realized 30d against DVOL (30d). Never the one-day GARCH forecast against a 30-day implied number.
5. **Say "cheap" or "expensive" only against something**: this expiry's IV against DVOL, against its own percentile over the past year, or against realized over the same number of days.
6. **Short-dated IV jumps around events.** An expiry just after CPI or an FOMC decision carries event premium; an expiry before it does not.
7. **Power-law work uses today's regime, about 40% realized**, not the volatility of older cycles ([[Bitcoin power law brief]], rule 8).

## Reading the lookup
`volatility` returns realized for 7, 14, 30, 60 and 90 days; where the 30-day reading sits in the past year; DVOL with its 7 and 30 day change and one-year percentile; and each OKX expiry's ATM IV with the move it implies to that expiry.

## Limits
Realized volatility uses completed UTC days; today's move is reported separately. DVOL is Deribit's market, a different venue from OKX; the two usually agree within a few points.
