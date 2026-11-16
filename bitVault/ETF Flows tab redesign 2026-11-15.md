---
categories:
  - "[[Decisions]]"
summary: The ETF Flows tab redesign (regime header, bars over price, brush, fund sparklines, issuer river, on-chain ticker): your answers, what was built, and the server work handed to the engineer.
created: 2026-11-15
---
## Decision
The ETF Flows tab was plain tables. The redesign makes it answer one question first: **are flows turning?** It changes only `index.html`, in the patch `btcliq-etf-flows-ui.patch`. The server work is in `btcliq-etf-flows-engineer-note.md` next to the patches.

| Question | You chose |
|---|---|
| Main job of the tab | Trend / regime |
| Hero chart | Daily flow bars with the BTC price |
| Opening window | Since launch (Jan 2024) |
| Units | Dollars and bitcoin side by side |
| Flow colours | "Best for the theme": steel blue in, rose out. Green and red stay for price; amber marks 2σ days |
| Trend aids | All: cumulative line, 5- and 20-day averages, streak counter, regime badge |
| Layout | Story scroll: header, hero, correlations, funds, issuers |
| On-chain moves | A small live ticker in the tools bar; click for the list |
| Hero tools | Range brush with 1M / 3M / YTD / All, hover readout, event marks, click a day for a drawer |
| Funds | Sparkline rows (flow bars, last move, 5 days, assets with share bar, holdings, premium, wallets) |
| Issuers | Stacked area with a Bitcoin held / Share switch |
| Extras | Ask Claude button, premium/discount, flow-vs-price stats |
| Missing data | A note for the engineer; the UI degrades gracefully |
| Delivery | Build straight into the app |
| Regime rule | Both: the word from the 20-day sum and its slope, plus σ against the past year |
| Big day | Beyond 2σ of the trailing 60 trading days |

## How the regime is read
- **The word** comes from the 20-day net flow (BTC) and its change over 5 trading days:
  - Accumulating: in, rising
  - Cooling: in, falling
  - Distributing: out, falling
  - Turning: out, rising
- **σ** compares that 20-day total with its own past year. Amber "unusual" appears at 2σ or more.
- **The streak** counts consecutive days with the same sign. It uses the dollar figure where one exists, otherwise bitcoin.

## Where I departed from the brief
- **Price sits in its own strip above the bars**, sharing the time axis and crosshair, rather than on a second y-axis over the bars. Two scales on one chart make crossings look meaningful when they aren't. If you want the overlay anyway, it's a small change.
- **2σ dots and macro labels show only when 6 months or less are on screen.** At since-launch zoom they buried the bars.
- **Days with a dollar figure but no BTC yet** are drawn faded at dollars ÷ that day's close and marked ≈. This is the only conversion on the page.

## Data limits at build time (2026-11-15)
- Dollars go back about 20 days. Per-fund detail goes back 8 days. Older days in the drawer show the issuer split instead.
- Prices start 2024-02-20. Calendar marks start October 2026.
- The issuer levels are rebuilt backwards from today's holdings, so early 2024 is approximate.
- bitbo and CoinMarketCap can disagree on a day's direction. The day drawer says so when they do.

Related: [[Options tab redesign 2026-11-14]], [[Design decisions 2026-11-13]].
