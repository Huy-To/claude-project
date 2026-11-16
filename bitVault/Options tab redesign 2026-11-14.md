---
categories:
  - "[[Decisions]]"
summary: The Options tab redesign (chain, smile, contract drawer, mini ticket, keyboard) - your answers, what was built, and the server work handed to the engineer.
created: 2026-11-14
---
## Decision
The Options tab was "hard to navigate" and "all over the place". The redesign keeps the chain on screen and puts everything about one contract in a drawer beside it. It is UI-only, built on branch `options-ui`; the server work is in `btcliq-options-engineer-note.md` next to the patches.

| Question | You chose |
|---|---|
| What the tab is for | All four: find a strike, check one contract, watch positions, read the market |
| Where a contract opens | A side drawer, about 420px, drag to resize; a full-width sheet on narrow screens |
| Chain columns | Keep all: bid, ask, mark, IV, delta, open interest |
| Where am I | Index line with ATM centred, OI as bars from the strike column, stronger ITM shading, distance from index |
| Expiries | One row grouped as daily, weekly, monthly, quarterly, with an OI bar and a dot when you hold something |
| Market read | IV smile above the chain, collapsible; its header carries the expiry stats |
| Expiry stats | Put/call, max pain, ATM IV with 25Δ skew, expected move |
| Drawer top | Price and spread, breakeven and the move needed, Greeks |
| Drawer sections | Live chart, order book, recent trades, power law check; each folds, remembered |
| Live chart | Mark price, plus a bid/ask band and IV; ranges 1H / 1D / 1W / All |
| Trade | A mini ticket in the drawer: side, size, price, Limit or Post-only, live rail check, same confirm window |
| Your positions in the chain | A chip with your size on the strike |
| Keyboard | ↑↓ strike, ←→ call or put, [ ] expiry, Enter opens, T trades, / search, Esc closes |
| Ask Claude | Keep it; it sends to the Claude panel so you stay on Options |
| Look | Titanium, sharper: the strike column is the one bold element |
| Recorder | SQLite, both families, every 60s in the background and 10s while the tab is open, 10s samples thinned to 1 minute after a day, kept forever |
| Scope | You: "only handle the UI/UX"; anything backend goes to your engineer as a note |

## Why
- **Drawer instead of the card above the chain:** the old card pushed the chain off screen and its grid misaligned values.
- **Strike column as the spine:** OI bars, index line, tags and position chips all sit on or next to it, so one glance answers "where is price, where is the interest, what do I hold".
- **Mini ticket reuses the Order ticket's check, preview and confirm:** two places to trade, one set of rails.
- **Keys only act when focus is on the page or inside the Options view,** so the Order ticket and account tables keep their own Enter, Esc and arrows.

## What was not chosen
- Compact strip above the chain, expanding rows, a separate contract page.
- Dropdown expiry picker; term-structure chart.
- Recording only the open contract; Parquet storage (5–8× smaller, needs pyarrow); recording only while the tab is open.
- Fill-the-ticket-only Trade button and separate Buy/Sell buttons.

## Waiting on the server
Until the engineer adds `/api/options/history` and single trades, the drawer chart uses what the browser records every 10 seconds (lost on reload) plus hourly trade dots, and Recent trades are grouped by hour. See `btcliq-options-engineer-note.md`.

## Related
[[Design decisions 2026-11-13]] · [[Claude Desk]]
