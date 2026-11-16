---
categories:
  - "[[Architecture]]"
summary: What a lookup is, the rules every lookup follows, and the full list - 31 that read and 4 that prepare an action.
status: built
created: 2026-11-13
---
A **lookup** is a named, typed question Claude can ask the running app. Each is one small Python function in `lookups.py` with a description of its arguments. Claude sees the name and description, decides when to call it, and gets compact JSON back.

## Rules every lookup follows
- **Nothing is sent.** Account lookups use OKX's read endpoints or the ticket's own dry run. `order_check` sends nothing and stores nothing. The four prepare lookups show you a card and stop there.
- **Checked arguments.** `registry.py` rejects unknown arguments, fills defaults, and clamps numbers, with a message Claude can act on.
- **Bounded results.** At most about 24,000 characters; tables are cut to the rows that fit, with a note saying so.
- **Failures are answers.** A lookup that fails returns an error result; it never breaks the conversation.
- **Other people's text is marked.** `news`, `market_snapshot` and web search results are tagged unverified ([[Safety model]]).

## The list

| Group | Lookup | Returns |
|---|---|---|
| Market | `market_snapshot` | The full text snapshot (mainly for other sessions) |
| | `candles` | OHLC for any timeframe, up to 500 bars |
| | `liquidation_levels` | The estimated liquidation map near price |
| | `real_liquidations` | Actual OKX liquidations seen since the app started |
| | `open_interest` | Open interest per exchange |
| | `order_book` | Merged depth and the largest walls |
| Options | `options_expiries` | Every expiry: days, ATM IV, open interest, max pain |
| | `options_chain` | One expiry's chain with Greeks |
| | `option_contract` | One contract in full, plus the power-law three-world check |
| Account | `account` | Equity, balances, positions, open orders, recent fills |
| | `positions` | Positions in full, with Greeks |
| | `history` | Fills, finished orders or funding, with totals |
| | `safety_rails` | Your limits and today's usage of them |
| | `quote` | Live bid, ask, mark for any BTC instrument |
| | `order_check` | Dry run of an order against your rails. Sends nothing |
| | `sizing` | The largest size OKX and your rails allow |
| Context | `polymarket` | BTC odds ladders and up-or-down windows |
| | `calendar` | Macro events by impact |
| | `news` | Telegram posts from your channels (unverified) |
| | `stablecoins` | Global stablecoin supply summary |
| | `power_law` | Model state and forward levels |
| | `screen` | What is on your screen, with every drawing (chat only) |
| Knowledge | `knowledge_search` | Passages from this vault's knowledge notes |
| | `knowledge_read` | One knowledge note, or one section of it |
| Memory | `conversation_search` | Passages from past conversations |
| | `conversation_read` | A stretch of one past conversation |
| | `propose_note` | Proposes a pinned note for you to approve (chat only) |
| Calculators | `volatility` | Realized vs DVOL vs OKX ATM IV |
| | `garch_forecast` | The GARCH forecast on clean OKX daily data |
| | `option_payoff` | P&L at expiry for one leg or a spread |
| | `position_risk` | Net delta, theta and what-if moves across positions |
| Prepare | `prepare_order` | A card per leg (one to four) with **Review in ticket** (chat only) |
| | `prepare_close` | A card with **Open Close panel** for one position part (chat only) |
| | `prepare_cancel` | A card with **Cancel order** for one open order (chat only) |
| | `prepare_amend` | A card with **Review change** for one open order's price or size (chat only) |

Web search is not in this list: it is a built-in tool of Claude itself, switched on per conversation ([[Engines]]).

## Why calculators are lookups
Claude is good at reasoning and unreliable at arithmetic. A payoff table or a what-if across positions is computed by code with the app's exact contract math, and Claude reads the result. `garch_forecast` runs the plugin QuantClaude fixed, fed with OKX daily closes so its data-quality check passes ([[GARCH volatility]]).

## Related
[[How to add a lookup]] · [[Lookup endpoint (MCP)]] · [[Claude Desk]]
