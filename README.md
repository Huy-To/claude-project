# btcliq

A live BTC chart in your browser showing **estimated liquidation levels**, built from
open interest across several exchanges.

## Run

```bash
cd btcliq              # this project folder
source .venv/bin/activate
btcliq --open          # http://localhost:8050
```

Options:

| Flag | Default | Meaning |
|---|---|---|
| `--days` | `14` | Liquidation history to build from (max 45, OKX's limit) |
| `--bin` | `50` | Price bucket size in USD |
| `--window` | `0.15` | Only show levels within ±15% of price |
| `--oi-seconds` | `30` | How often open interest is read |
| `--port` | `8050` | Local port |
| `--telegram` | | Extra channels to watch, comma-separated (also saved) |
| `--telegram-seconds` | `30` | How often each channel is checked |
| `--data-dir` | `data/` | Where news history and the channel list are saved |
| `--chat` | `auto` | How the chat reaches Claude: `claude-code`, `api`, `auto`, `off` |
| `--model` | `claude-sonnet-5-5` | Claude model for the chat panel |

Pick the chart timeframe (1m, 5m, 15m, 1H, 4H, 1D) with the buttons above the chart; your
choice is remembered. A countdown to the close of the current candle is shown next to the
buttons and under the price label on the right axis (turn it off under Layers →
Candle countdown). Candles are aligned to UTC and the countdown uses your computer's clock. The liquidation levels don't depend on the timeframe: they're built
from hourly open interest for older history and 5-minute open interest for the last 3 days.

**Heatmap** above the chart (or the **L** key) shows or hides the estimated liquidation
heatmap, its labels and the side profile. **Layers** next to it holds the other chart
settings: heat contrast, hiding small clusters, labels, side profile, real liquidations, book
depth, power-law bands, session bands, calendar events, the candle countdown and the market
hours row. All of them are remembered.

Stop it with Ctrl+C.

## Screen layout

- **Top bar:** price (it flashes on each tick), total open interest, estimated long
  liquidations within 3% below price (▼) and short liquidations within 3% above (▲), the
  power-law zone (click it for the details) and the connection status.
- **Views:** Chart, Options and Stablecoins tabs sit above the main area. Each view's
  controls sit next to the tabs. Esc goes back to the chart.
- **Market hours** (the row under the view tabs, in every view):
  - A bar of the trading sessions over the last 6 and next 18 hours (Asia, London, New York;
    brighter where they overlap; the blue line is now), with the open sessions and when the
    next one starts. Sessions use forex hours: Asia 9:00–18:00 Tokyo time, London 8:00–17:00,
    New York 8:00–17:00, on weekdays.
  - Live countdowns for the Tokyo, Hong Kong, London and New York stock exchanges: a green
    dot while open, then "closes in" (or "lunch in" for Tokyo and Hong Kong); "opens in" when
    closed, in amber within 15 minutes of the open.
  - CME Bitcoin futures, which have traded 24/7 since May 29, 2026: a countdown to the weekly
    maintenance break (Saturday 2:00–4:00 Chicago time) and "reopens in" during it.
  - The next high-impact event from the calendar (for example "FOMC in 26d 4h"), in amber
    within 15 minutes. Click it to open the calendar.
  - Hours follow each market's own clock, including daylight saving; weekends are skipped,
    but **exchange holidays aren't included**. Hover an item for its hours in your time.
    When the row runs out of room it switches to short market names (TYO, HK, LDN, NY).
    Layers → Market hours row hides it.
- **Session bands:** on 1m to 1H charts, faint bands behind the candles mark the Asia,
  London and New York sessions (Layers → Session bands).
- **Calendar events:** on 1m to 1H charts, each high-impact event from the calendar gets a
  dashed amber line with a short label along the bottom (CPI, FOMC, Jobs), so you can see
  how price reacted (Layers → Calendar events).
- **Side panel:** Book, Market, News and Claude tabs, in one full-height section to start
  with (Claude last). It's yours to rearrange:
  - Drag a tab to reorder it. Drop it on the panel's lower half (or right half, when docked
    at the bottom) to split the panel into two sections, e.g. Book on top and News below;
    drag tabs between the sections afterwards.
  - Drag the panel button (top right of the panel) onto the left, right or bottom edge to
    dock the panel there. Clicking it gives the same choices plus **Reset panel layout**.
  - Drag the panel's edge, or the line between its sections, to resize. Double-click
    either to reset it.
  - All of this is remembered. In windows narrower than 1000px the panel sits under the
    chart; below 700px it becomes a single tab bar.
- **Colors** each mean one thing: green and red are direction (candles, bids and asks,
  calls and puts, gains and losses); gold is estimated liquidation size, dim for small
  clusters and bright for the largest on screen; blue is your selection and "you are here"
  (the index line, a strike you're showing); amber needs attention (warnings, keyword
  matches); violet is the power-law floor and bands.
- **Real liquidations:** dots on candles are real OKX liquidations of $50K or more: red
  under the candle when longs were liquidated (price fell), green over it when shorts were
  (price rose), with 💥 from $1M. A short notice also pops up when one of $250K or more
  arrives live.

## Drawing tools

The toolbar on the chart's left edge holds TradingView-style drawing tools (hover one for its
name and shortcut). The list button at the bottom of the toolbar shows your drawings: click
one to select it (the chart scrolls to it), × to delete.

| Tool | Shortcut | Notes |
|---|---|---|
| Cursor | Esc | Select, move and edit drawings |
| Trend line | Alt+T | Two points |
| Ray | | From a point through another, extended right |
| Horizontal line | Alt+H | Price shown on the axis |
| Horizontal ray | Alt+J | From a point to the right |
| Vertical line | Alt+V | |
| Rectangle | Alt+Shift+R | |
| Fib retracement | Alt+F | 0, 0.236, 0.382, 0.5, 0.618, 0.786, 1 with prices |
| Price range | Alt+M | Change in $ and %, bars and time |
| Text | | Double-click a note to edit it |

- **Place** a drawing with click-click, or press and drag. After placing, the tool returns
  to the cursor.
- **Select** by clicking a drawing: drag it to move, drag its round handles to reshape,
  and use the bar at the top to change color, line width, or delete it (or press Delete).
- **Magnet** snaps points to the candle's open, high, low or close. **Eye** hides all
  drawings. **Ctrl+Z** undoes. The bin deletes everything (undoable).
- Drawings are anchored to real time and price, so they stay in place across zoom, scroll
  and timeframes, and are saved in `data/drawings.json`.

## Stablecoins

Open the **Stablecoins** view (the tab above the chart) for global stablecoin supply (all
coins, all chains), from DefiLlama's free API.

- **Headline numbers:** total supply, change over 24h / 7d / 30d / 90d / 1y, all-time high,
  USDT and USDC share, and the **stablecoin supply ratio** (BTC market cap ÷ stablecoin
  supply; lower means stablecoins have more buying power relative to BTC).
- **Chart:** total supply (or any one coin: pick it from the menu or click it in the table)
  with daily net mint/burn bars underneath and BTC price on the left axis. Ranges 3M, 1Y,
  3Y, All (back to Nov 2017).
- **Tables:** top 30 stablecoins (supply, share, 24h/7d/30d change, price), supply by
  blockchain, and by backing (fiat-backed, crypto-backed, algorithmic).
- **Peg alerts:** USD stablecoins with over $50M in circulation trading more than 0.5% away
  from $1. DefiLlama prices can lag for illiquid coins.

DefiLlama updates supply about hourly; btcliq refreshes every 5 minutes and moves today's
point on the chart. Claude's chat gets a summary with every question.

## Bitcoin power law

btcliq includes Giovanni Santostasi's Bitcoin power law, using the calculator from
`btc-powerlaw/powerlaw.py` (vendored unchanged as `src/btcliq/powerlaw_calc.py`) and the
rules in `btc-powerlaw/POWER_LAW_BRIEF.md`. Use it as the brief says: a long-horizon
bias, strike anchor and regime filter, never a short-term timing signal.

- **Market tab → Power law** (or click the zone in the top bar): zone (🚨 Below floor,
  🧊 Deep value, ❄️ Value, ⚖️ Fair, 🔥 Extended, 🫧 Bubble), price / SP26 trend, the floor
  (0.42× trend) and how far above it price is, alternative floors, the flat-price test date,
  REFIT exponent, realized vol, the zone's menu of structures, and the **invalidation
  status** (✅ valid, ⚠️ warning, ⛔ invalidated) from the brief's section 8 (two weekly
  closes below the floor, a daily close below 0.35× trend, or 30+ days below the floor).
  Recomputed every minute from the OKX index. The top bar shows ⚠️ or ⛔ next to the zone
  when the status isn't valid.
- **Chart:** floor (solid violet), 0.55×, 0.80×, trend and 1.25× lines (Layers → Power law
  bands). They don't affect the price scale; they're most useful on 1D/4H.
- **Options → contract card → Power law check:** the horizon gate (primary ≥ 6 months,
  secondary 3–6, filter < 3), floor / 0.55× / 0.80× / NvPL forecast at that expiry, and the
  three-world test at the contract's own IV: P(finish in the money), fair value, and edge vs
  the live bid/ask under risk-neutral, NvPL and mix, plus rule checks (floor anchor, −25%
  excursion, call-selling zone). The risk-neutral world uses the rate implied by OKX's
  forward, so its fair value matches OKX's mark. The chain tags the floor / 0.55× / 0.80×
  strikes for each expiry.
- **Claude:** every question includes the condensed operating rules, the live power-law
  state and invalidation status, and the three-world check for any contract you mention.

Daily closes come from Coin Metrics and are cached in `data/btc_daily.csv` (seeded from the
btc-powerlaw project's cache if present).

## Calendar (Market tab)

The **Calendar** section at the bottom of the Market tab lists what's scheduled to move the
market: Fed meetings and speeches, the President's schedule, and the big US data releases.

- **Next:** the next high-impact event, with a countdown. The same countdown sits at the end
  of the market hours row.
- **The list:** today and the next 13 days, grouped by day, then later high-impact events
  (up to four months out). Times are in your time zone; hover a row for Eastern time and the
  source. Each row has an icon for where it's from (Fed building, White House flag, data
  bars), amber for high impact. Past events from today are dimmed. This week's data releases
  show the forecast and previous values. Fed speeches with a live stream have a link.
- **Filters:** All, Fed, White House or Data (remembered).
- **Heads-up** (the switch next to the title): a notice in the app 15 minutes before each
  high-impact event, once per event. It only shows while btcliq is open in your browser.

**Impact.** High: FOMC rate decisions, CPI, the jobs report, PCE inflation, the GDP advance
estimate, the Fed Chair speaking or testifying, whatever ForexFactory rates high that week,
and White House appearances about tariffs, trade, crypto, the Fed or the economy (plus any
address to the nation or unscheduled press conference). Medium: FOMC minutes, PPI, later GDP
estimates, the Vice Chair, governors speaking on the economy, and the President's other
public appearances. Low: everything else, such as the President's closed-door meetings.

**Where it comes from** (all public, no keys):

| Part | Source | Refreshed |
|---|---|---|
| FOMC decisions and press conferences, minutes, Beige Book, testimony, Board members' speeches | Federal Reserve Board calendar (through December), plus the FOMC meeting calendar for next year | Every 6 hours |
| The President's full public schedule, press briefings, and unscheduled appearances from pool reports | White House press office, via Roll Call's Factbase | Every 10 minutes |
| GDP, personal income and outlays (PCE) | BEA release calendar | Daily |
| CPI, jobs report, PPI | Built in, from the BLS 2026 schedule and OMB's 2027 schedule of principal economic indicators | — |
| Forecasts, previous values and impact for this week; other US releases rated medium or high (jobless claims, retail sales, ISM, regional Fed presidents) | ForexFactory's weekly calendar | Hourly |

Limits:

- The White House releases each day's schedule the evening before, so the White House part
  only covers today and tomorrow.
- bls.gov blocks automated requests, so the CPI, jobs report and PPI dates are built in, and
  they run out after December 2027. OMB publishes the next year's schedule each fall; add
  it to `BLS_DATES` in `src/btcliq/macro.py`. The server log warns 60 days before the dates
  run out.
- Forecasts are only available for the current week.
- Holidays aren't listed, and agencies sometimes reschedule releases (during a government
  shutdown, for example). The Fed, BEA, White House and ForexFactory parts pick up changes
  on their next refresh; the built-in BLS dates don't.

## Options (OKX BTC/USD)

Open the **Options** view (the tab above the chart) to see every live OKX BTC/USD option and
look up contracts.

- **Look up a contract** by typing its name and pressing Enter. Exact OKX names work
  (`BTC-USD-261127-75000-C`), and so do loose ones: `27nov 75k call`, `261225 70000 put`,
  `75000C 4DEC`, or Deribit-style `BTC-27NOV26-75000-P`. The box also autocompletes. If
  nothing matches exactly, it suggests the closest strikes.
- **Contract card:** expiry and time left, strike vs index, mark/bid/ask/last (with sizes),
  implied volatility, greeks (delta, gamma, theta $/day, vega $/vol point), open interest,
  breakeven at expiry, forward price, the live order book and 7 days of trades.
  **Show strike on chart** draws the strike on the main chart; **Ask Claude** sends the
  contract's numbers to the chat for analysis.
- **Chain:** pick an expiry, then calls | strike | puts with OI, IV, delta, bid, ask and
  mark. The blue line and blue strike mark the index; shaded cells are in the money. Click
  any cell to open that contract. **Strikes** narrows the range around the index.
- **Coin-margined** (`BTC-USD`, priced in BTC) or **USD-margined** (`BTC-USD_UM`, priced
  in USD). **Native / USD** switches how prices are shown.

Data refreshes every 10 seconds while the panel is open. With the panel in use, Claude's
chat also gets a summary of the option market (put/call open interest, ATM IV per expiry,
strikes with the most open interest).

## Trading on OKX

The **Trade** tab (side panel) and the `btcliq-trade` command place real orders on your OKX
account: BTC options, perpetuals, dated futures and spot. Both go through the same
**safety rails**, your limits in `trading.toml`, and every order takes two steps: a preview showing
the order value, premium, fees, worst case and each limit check, then a confirmation that
re-checks everything with fresh prices before sending.

**Setup (once):** create an OKX API key with **Read + Trade** only, bound to your IP; add
`OKX_API_KEY`, `OKX_API_SECRET` and `OKX_API_PASSPHRASE` to `.env`; run
`cp trading.example.toml trading.toml` and set your limits; restart btcliq.
**[SAFETY_RAILS.md](SAFETY_RAILS.md) walks through every step and setting, with example profiles.**

- **Order ticket:** type or pick an instrument (the options contract card has a **Trade** button
  that fills it in), choose buy or sell, limit / post-only / IOC / market, price (Bid, Mid, Ask and
  Mark fill it), size in contracts (BTC for spot) and *Reduce only* to close. The line under the size
  shows what it's worth in BTC and USD, plus the premium for options.
- **Confirm dialog:** the exact order, its numbers and every rail (✓ or ✕). A single ✕ disables
  Confirm. The preview expires after a minute.
- **Positions, open orders, balances** refresh every few seconds while the tab is open. **Close**
  fills the ticket with an order that closes that position; × cancels an order. The tab's badge
  counts open orders.
- **On the chart:** your open orders on BTC perps and spot are dashed lines (green buy, red sell);
  positions show their entry (blue) and liquidation price (amber). Layers → My orders and positions.
- **Kill switch** (top of the tab): halts trading and cancels every open order. Positions stay
  open. **Resume trading** lifts it.
- **Today's limits** shows today's opening orders and volume against your daily caps.

From a terminal or your Claude Code trading session (JSON output; `--text` for readable lines):

```bash
btcliq-trade status                                          # keys, permissions, limits, halt
btcliq-trade account                                         # balances, positions, orders, fills
btcliq-trade preview BTC-USD-261127-75000-C buy 2 --px 0.015 # prints an id; exit code 2 = blocked
btcliq-trade place <id>
btcliq-trade kill --reason "news"                            # / resume
```

Every order attempt, cancel and halt is logged to `data/trade_journal.jsonl`.

## Telegram news

Open the **News** tab in the side panel to follow public Telegram channels.

- **Add** a channel by name (`WatcherGuru`), `@name` or link (`https://t.me/WalterBloomberg`).
  It's checked before it's saved. Click a channel to show only its posts; **×** stops watching it.
- New posts show up live (each channel is checked every 30 seconds). While the News tab
  isn't showing, it counts how many arrived.
- **Highlight keywords** (comma-separated) marks matching posts in amber with 🚨. **Alerts** can send a
  desktop notification for keyword matches or for every new post (your browser asks first).
- **Ask Claude** on a post sends it to the chat with the chart data, to ask how it could
  affect BTC. Claude also sees the last 6 hours of posts with every question.

How it works: it reads Telegram's public web preview (`t.me/s/<channel>`), so no Telegram
account or key is needed, but only **public channels** work (not private channels or groups).
Channel list: `data/telegram.json`. Post history: `data/news.db` (SQLite), kept across restarts.

## Order books

The **Book** tab in the side panel is a live ladder of resting orders, merged from
OKX, Hyperliquid, Deribit, Kraken (perpetuals) and Coinbase, Bitfinex (spot), with sizes
in BTC. Binance and Bybit books aren't available from the US. In a short, wide section
(for example with the panel docked at the bottom) bids and asks sit side by side.

- **Group** sets the price bucket ($0.5 to $250). Hover a row to see each venue's share.
- **Venue chips** turn venues on and off; the dot is green when that venue's book is fresh.
- **Align** shifts each venue's book so its mid matches OKX's. Spot and perpetual prices
  differ by tens of dollars, so without this the merged book shows bids above asks.
- The bottom shows bid vs ask totals within ±0.5%, ±1% and ±2%.
- **Book depth** (Layers) draws resting bids and asks as bars from the chart's left edge,
  next to the liquidation levels, with bucket size following zoom.

Books are polled over REST (every 1 to 3 seconds per venue) and only while the Book tab is
showing or the depth overlay is on. Resting orders are real but can be pulled at any moment.

## Ask Claude (chat panel)

The **Claude** tab (the last tab of the side panel to start with) is a chat with Claude.
Asking about a post or a contract opens it if it's hidden. Each question is sent with a fresh
snapshot of what the chart shows (price, open interest per exchange, the biggest estimated
liquidation levels, recent real liquidations, and recent 15m/1H/1D candles), so you can
ask things like "where are the biggest clusters?" or "summarize today's move".

It can reach Claude two ways (`--chat`):

| `--chat` | Uses | Cost |
|---|---|---|
| `auto` (default) | Claude Code if installed, otherwise an API key | |
| `claude-code` | The Claude Code CLI on this PC (`claude -p`), logged in with your Claude account | Counts toward your Claude plan's usage limits |
| `api` | `ANTHROPIC_API_KEY` from `.env` (console.anthropic.com) | Billed per token |
| `off` | Nothing | |

In Claude Code mode the chat runs with no tools (it can't read files, run commands or browse),
in an empty temporary folder, ignoring your Claude Code settings, and without saving sessions.

Pick the model with `--model` (default `claude-sonnet-5-5`), e.g. `--model claude-opus-5-5`
for deeper answers or `--model claude-haiku-5-5` for faster, lighter ones. Each question
sends about 3,000 tokens of market data along with it.

Your conversation is kept in the browser until you press Clear.

## Data sources

All public, no keys. Binance and Bybit block US connections, so their open interest comes
from CoinGecko instead (refreshed every 2 minutes; CoinGecko's own data is roughly 10 minutes
old for Binance and up to ~1.5 hours for Bybit; the Market tab shows the age).

| Venue | Used for |
|---|---|
| OKX (BTC-USDT perp) | Candles, open-interest history, real liquidations |
| OKX (BTC-USD perp), Hyperliquid, Deribit, Kraken Futures | Live open interest and price |
| Coinbase | Spot price |
| Binance, Bybit (via CoinGecko) | Open interest, for the market's total size |

The calendar's sources (Federal Reserve, Roll Call Factbase, BEA, ForexFactory) are listed
under [Calendar](#calendar-market-tab).

## How the levels are estimated

1. When open interest rises, new positions opened near that bar's price. Half the new
   notional is long and half short (every contract has both sides).
2. Each is spread across a leverage mix (5x 10%, 10x 25%, 20x 25%, 25x 15%, 50x 15%,
   100x 10%). A long at price E with leverage L liquidates near `E × (1 − 1/L + 0.5%)`;
   a short near `E × (1 + 1/L − 0.5%)`.
3. When open interest falls, every level shrinks by the same fraction.
4. When price trades through a level, it's removed.

History comes from OKX, the only one of these venues that publishes it, scaled up by
(total market OI ÷ OKX OI). Live updates come from OKX, Hyperliquid, Deribit and Kraken every
30 seconds, scaled up by (total market OI ÷ their OI). Binance and Bybit set the total but
not the timing, because their numbers arrive late; so the model assumes their traders
position like traders on the other venues.

**These are estimates, not real positions.** Exchanges don't publish where positions sit.
Positions opened before the loaded history aren't counted. Compare with the real OKX
liquidation dots on the chart.

## Layout

```
src/btcliq/
  exchanges.py   API calls per venue
  model.py       liquidation level model
  app.py         server, history load, live loops
  chat.py        chat with Claude (market snapshot + streaming)
  book.py        live order books, merged across venues
  telegram.py    Telegram channel monitor (public web preview)
  options.py     OKX BTC/USD options: chain, contract lookup, greeks
  powerlaw.py    power-law state, invalidation, three-world option checks
  stables.py     global stablecoin supply (DefiLlama)
  macro.py       calendar: Fed, White House schedule, US data releases
  powerlaw_calc.py  unchanged copy of btc-powerlaw/powerlaw.py
  static/index.html   chart (TradingView Lightweight Charts)
```
