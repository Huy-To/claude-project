---
categories:
  - "[[Knowledge]]"
summary: Who the user is as a trader and the standing rules ideas must respect - swing BTC options on OKX, defined risk, size from the live rails.
source: Sessions with the user, 2026-11-10 to 2026-11-13
created: 2026-11-13
---
## Use this when
Building any idea, or judging whether a position fits how the user trades.

## Profile
- Swing-trades **BTC options on OKX** to grow capital, and is open to new strategies.
- Watches market opens, the macro calendar, liquidation clusters, order books and Telegram news for timing.
- Uses the Bitcoin power law as the long-horizon frame ([[Bitcoin power law brief]]).
- Wants usable output, not theory: explicit rules, strikes, expiries, invalidation and risk limits.
- A separate Claude Code session can place orders through `btcliq-trade`, always preview then place, inside the same rails.

## Rules for ideas
1. **Defined risk only**: long options and spreads. No naked shorts.
2. **Size from the live rails, never from memory.** Read `safety_rails` each time; the limits in `trading.toml` change.
3. **Check before presenting**: `option_payoff` for anything with more than one leg, then `prepare_order`, which dry-runs every leg and gives the user a card (or `order_check` for the numbers alone).
4. **State what proves the idea wrong** and the strongest case against it.
5. **Power-law horizon gate**: under 3 months to expiry the power law is a filter, never the reason for a directional trade.
6. **Per-trade risk** from the power-law brief: at most 2 to 3% of the account on one trade, 15 to 20% across power-law trades. Size by the loss if the model is wrong.
7. **"No trade" is a valid answer.**

## Things the user has not decided
Take-profit and stop-loss orders are not built in the app yet. Do not assume they exist.

> [!note] Keep this note current
> Lasting rules the user states in chat are better saved as pinned notes ([[Chat memory]]). Edit this note when the profile itself changes.
