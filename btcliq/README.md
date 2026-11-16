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
| `--data-dir` | `data/` | Where news history, the channel list and recorded option quotes are saved |
| `--options-recorder` | `on` | Save OKX option quotes to `data/options/` every minute (`off` to stop) |
| `--etf-flows` | `on` | Watch spot Bitcoin ETF wallets and read the published daily flows (`off` to stop) |
| `--chat` | `auto` | How the chat reaches Claude: `claude-code`, `api`, `auto`, `off` |
| `--model` | `claude-sonnet-5-5` | Claude model the chat starts on (the Claude view has a picker) |
| `--effort` | `medium` | How hard Claude thinks per answer: `low`, `medium`, `high`, `xhigh`, `max` |
| `--vault` | `../bitVault` | Obsidian vault with the chat's knowledge notes (or `BTCLIQ_VAULT` in `.env`) |
| `--garch-dir` | `../garch-method-fixed` | Folder of the garch-method plugin, for the GARCH lookup |

Pick the chart timeframe (1m, 5m, 15m, 1H, 4H, 1D) with the buttons above the chart; your
choice is remembered. A countdown to the close of the current candle is shown next to the
buttons and under the price label on the right axis (turn it off under Layers →
Candle countdown). Candles are aligned to UTC and the countdown uses your computer's clock. The liquidation levels don't depend on the timeframe: they're built
from hourly open interest for older history and 5-minute open interest for the last 3 days.

**Heatmap** above the chart (or the **L** key) shows or hides the estimated liquidation
heatmap, its labels and the side profile. **Layers** next to it holds the other chart
settings: heat contrast, hiding small clusters, labels, side profile, real liquidations, book
depth, power-law bands, session bands, calendar events, your orders and positions, your fills,
the candle countdown and the market hours row. All of them are remembered.

Stop it with Ctrl+C.

## Screen layout

The look is "Titanium": warm graphite surfaces, the JetBrains Mono font (bundled in
`src/btcliq/static/fonts/`, SIL Open Font License), uppercase panel titles and rounded cards.

- **Top bar:** price (it flashes on each tick) with its change over the last 24 hours, total
  open interest, estimated long liquidations within 3% below price (▼) and short liquidations
  within 3% above (▲), the power-law zone (click it to jump to the Power law panel), the
  **Chart / Options / Stablecoins / ETF Flows / Claude** switch, the **Panels** button and the connection status.
  Esc goes back to the chart.
- **Market hours** (the row under the top bar, in every view):
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
- **Chart card:** the timeframes, candle countdown, Heatmap and Layers sit in the card's own
  toolbar, with the drawing tools down its left edge. Options, Stablecoins and ETF Flows open inside the
  same card, so the panels and your account tables stay in view.
- **Account tables** (under the chart): Positions, Open orders, Trade history, Order history,
  Funding history and Balances from your OKX account. See [Trading on OKX](#trading-on-okx).
  Drag the gap above them to change their height (double-click resets it); the arrow on the
  right hides them.
- **Panels** (the column beside the chart): Order book, Order ticket, Liquidation levels,
  Polymarket odds, Polymarket up or down, Calendar, News, Exchanges, Power law and Claude (collapsed to start with; see [Ask Claude](#ask-claude-the-claude-view-and-panel)). Each one is a card
  you can arrange, like worldmonitor.app's panels:
  - **Move:** grab a panel by its title (or any empty part of it) and drop it; a glowing bar
    shows where it lands, and Esc cancels.
  - **Resize:** drag a panel's bottom edge to make it taller or shorter (it snaps to rows).
    Drag the gap between the chart and the panels to widen the column; from about 620px it
    holds two columns, and a panel's right edge then makes it one or two columns wide.
    Double-click any edge to reset it.
  - **Collapse** with the arrow in the title bar (or double-click the title). **Hide** with ×.
  - The **Panels** button in the top bar lists every panel: show or hide it, move it up or
    down (handy from the keyboard), put the column on the left or right, or **Reset layout**.
  - All of it is remembered. Under 900px wide everything stacks in one scrolling column.
- **Live touches:** a pulsing green dot on panels whose data is streaming (amber when stale),
  prices and new rows flash briefly, counts bump when news or orders arrive, and a panel glows
  when something lands: gold on Liquidation levels for a big liquidation, amber on News for a
  keyword match and on Calendar for a heads-up. Your system's reduced-motion setting turns
  the motion off.
- **Colors** each mean one thing: green and red are direction (candles, bids and asks,
  buys and sells, calls and puts, gains and losses); gold is estimated liquidation size, dim
  for small clusters and bright for the largest on screen; steel blue is the interface itself
  (selection, focus, "you are here" lines); amber needs attention (warnings, limits, keyword
  matches, high-impact events); violet is the power-law floor and bands.
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

## ETF flows

Open the **ETF Flows** view (the tab next to Stablecoins) for money moving in and out of the
US spot Bitcoin ETFs. It shows two different things and keeps them apart:

- **On-chain moves (live).** Bitcoin entering or leaving a fund's own wallets, checked every
  minute and seen from the mempool on. A move is one transaction's net effect on the fund:
  bitcoin received minus bitcoin spent. Transfers between two wallets of the same fund net to
  zero and are left out, as is anything under 0.01 BTC (fee top-ups and dust).
- **Published numbers (daily).** What the funds report after the US close, for every fund:
  bitcoin added or removed per fund per day (from reported holdings, via bitbo.io; rounded to
  about 1 BTC, and the newest day can be incomplete) and the total net flow per day in USD
  (CoinMarketCap). The two sources date and measure flows differently, so they don't add up
  to each other.

**Which wallets are known.** On-chain tracking needs a fund's addresses, and only one issuer
publishes them:

| Fund | Wallet addresses |
|---|---|
| BITB (Bitwise) | Published by the issuer in bitbetf.com's page data. btcliq re-reads the list every 15 minutes (65 addresses holding all of the fund's bitcoin when this was built). |
| IBIT, FBTC, ARKB | Not published, but traceable on the public chain. A traced list (built 2026-11-15) is in `btcliq-etf-wallets.json` next to this repository; how it was traced and how sure each part is, is in `btcliq-etf-trace/README.md`. |
| GBTC, BTC, HODL, BRRR, EZBC, BTCO, BTCW and newer funds | Not public and not traced yet. Coinbase, the custodian for most of them, does not disclose client wallets. Arkham and Dune attribute addresses to these funds, but only behind an API key. |

To watch a fund whose addresses you have (the traced list, or an export from Arkham), put them
in `data/etf_wallets.json`. The file is re-read every 15 minutes:

```json
{"IBIT": {"addresses": ["bc1q...", "bc1q..."], "redeems_to": ["36YZXc..."], "source": "traced 2026-11-15"},
 "FBTC": {"addresses": ["3Kix5j...", "bc1q..."], "adopts": "p2wsh"},
 "ARKB": ["bc1q...", "1Hnzoz..."]}
```

- **`redeems_to`** is an address the fund's redemptions are paid into and nobody else uses
  (IBIT and ARKB each have one). Whatever a single wallet pays into it is recorded as that
  fund's outflow, and the paying wallet is added to the fund, so outflows are caught even
  from wallets the list is missing. The first read goes back about 150 transactions.
- **`adopts": "p2wsh"`** is for a custodian that sweeps into fresh multisig addresses
  (Fidelity): when most of a transaction's inputs are the fund's, its new P2WSH outputs become
  the fund's wallets instead of counting as an outflow.
- **Long lists.** Balances are checked 1,200 addresses a minute, so a list of 4,000 is covered
  about every four minutes. A fund with more than 500 addresses is watched from the moment it
  is added: its wallets' last 30 days are not read one by one (its `redeems_to` address still
  fills in recent outflows).

A list like that goes stale as a fund opens new wallets: coins moved to a wallet that isn't on
your list show up as an outflow. For BITB, an outflow newer than Bitwise's last published list
is marked "may be a move to a new wallet of the fund", and is corrected by itself once the
list includes the new wallet.

On-chain moves are the settlement of a creation or redemption. In BITB's recent history they
land on business days between about 14:00 and 20:00 UTC, a day or two after the date the flow
is published under. So they confirm the published flow rather than lead it, but they show the
exact time and size, and they can appear before a holdings-based tracker has the day.

- **Funds table:** assets, bitcoin held (reported for BITB; estimated from the share value for
  the others, marked ≈), wallets watched and how much of the fund's bitcoin they cover, on-chain
  net over 24h / 7d / 30d, and the last published move. Click a watched fund to list its wallets.
- **History:** a first run reads the last 30 days of each wallet's transactions. Events are
  kept in `data/etf/state.json`, and the published daily numbers accumulate there too.
- **Claude** can read all of this through the `etf_flows` lookup.

For the page and other tools: `GET /api/etfs` returns everything (`funds`, `totals`, `events`,
`onchain_daily`, `published`, `sources`), `GET /api/etfs/wallets?ticker=BITB` a fund's addresses,
and the websocket sends `{"type": "etfs"}` with the same shape minus the long series after each
check that found a change. The fields are described at the top of `src/btcliq/etfs.py`.

## Bitcoin power law

btcliq includes Giovanni Santostasi's Bitcoin power law, using the calculator from
`btc-powerlaw/powerlaw.py` (vendored unchanged as `src/btcliq/powerlaw_calc.py`) and the
rules in `btc-powerlaw/POWER_LAW_BRIEF.md`. Use it as the brief says: a long-horizon
bias, strike anchor and regime filter, never a short-term timing signal.

- **Power law panel** (or click the zone in the top bar): zone (🚨 Below floor,
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

## Calendar

The **Calendar** panel lists what's scheduled to move the market: Fed meetings and speeches, the President's schedule, and the big US data releases.

- **Next:** the next high-impact event, with a countdown. The same countdown sits at the end
  of the market hours row.
- **The list:** today and the next 13 days, grouped by day, then later high-impact events
  (up to four months out). Times are in your time zone; hover a row for Eastern time and the
  source. Each row has an icon for where it's from (Fed building, White House flag, data
  bars), amber for high impact. Past events from today are dimmed. This week's data releases
  show the forecast and previous values. Fed speeches with a live stream have a link.
- **Filters:** All, Fed, White House or Data (remembered).
- **Heads-up** (the bell switch in the panel's title bar): a notice in the app 15 minutes before
  each high-impact event, once per event, and the panel glows amber. It only shows while btcliq
  is open in your browser.

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

## Polymarket odds

Two read-only panels show what Polymarket's BTC markets are pricing (public Gamma API, no key,
no account). They only fetch while they're on screen; btcliq caches the answers for 30 seconds
(ladders) and 5 seconds (up or down).

- **Polymarket odds** has three kinds of market. Pick one, then pick the date or period:
  - **Hit**: the odds BTC *touches* each price before the market ends (today, this week, this
    month, this year). Prices already touched show as *Hit ✓*. The top line shows where the touch
    odds cross 50% on each side, or the nearest untouched price and its odds.
  - **Above**: the odds BTC is *above* each price at noon ET on that day (Binance BTC/USDT
    1-minute close), one ladder per day for the coming week. The top line shows the median
    (50/50 price) and the range the market gives about 68% (between the 84% and 16% strikes).
  - **Range**: the odds for each $2,000 bucket at the same noon ET close, with the most likely
    bucket and the median.

  Each row shows the odds as a bar, the change over 24 hours in percentage points, and bid / ask
  and volume on hover. The live price is slotted into the ladder. *Open on Polymarket* links the
  market.
- **Polymarket up or down**: the live 5-minute, 15-minute, 1-hour, 4-hour and daily windows, with
  the odds that BTC ends each window higher than it started and a countdown. Click a row to open it.

Odds are shown the way Polymarket shows them: the midpoint of the order book, or the last trade
when the spread is wider than 10¢. These are a crowd's bets, not a forecast, and thin markets
(far strikes, later dates) can sit a few points off. If Polymarket stops answering, the panels
keep the last odds and their dot turns amber.

## Options (OKX BTC/USD)

Open the **Options** view (the tab above the chart) to see every live OKX BTC/USD option, read the market
for an expiry and trade a contract without leaving the chain.

- **Look up a contract** by typing its name and pressing Enter (or `/` to jump to the box). Exact OKX names
  work (`BTC-USD-261127-75000-C`), and so do loose ones: `27nov 75k call`, `261225 70000 put`,
  `75000C 4DEC`, or Deribit-style `BTC-27NOV26-75000-P`. The box also autocompletes. If nothing matches
  exactly, it suggests the closest strikes.
- **Coin-margined** (`BTC-USD`, priced in BTC) or **USD-margined** (`BTC-USD_UM`, priced in USD).
  **Native / USD** switches how prices are shown.
- **Expiries:** one row, grouped as Daily, Weekly (Fridays), Monthly (last Friday) and Quarterly (last
  Friday of March, June, September, December). Each button shows the time left and a bar for its open
  interest; a blue dot means you hold an option in that expiry.
- **Smile card** for the chosen expiry: when it expires, open interest in calls and puts, the put/call
  ratio, ATM IV, the 25-delta skew (put IV minus call IV), max pain, the expected move (the ATM straddle's
  price) and the power-law floor at expiry. Underneath, IV by strike (puts below the index, calls above),
  with each strike's bid–ask IV range, the index line, the expected-move band, max pain and the power-law
  levels. Click a point to open that contract. The arrow folds the chart away (remembered).
- **Chain:** calls | strike | puts with IV, delta, bid, ask, mark and open interest. Open interest is a
  bar growing out of the strike column (hover for the number). The blue line with the price is the
  index, and the chain opens centred on it. In-the-money cells are shaded. Each strike shows its distance
  from the index and tags for the power-law floor, 0.55× and 0.80× levels, max pain and the ±1 expected
  move (strikes inside the move have a blue edge). Options you hold carry your size on the strike (outlined
  when short). **Strikes** narrows the range.
- **Keyboard:** ↑ ↓ move between strikes, ← → switch between call and put, `[` `]` change expiry, Enter
  opens the contract, T opens it with the size box ready, Esc closes the drawer (a second Esc goes back to
  the chart).
- **Contract drawer** (opens on the right; drag its edge to resize, double-click resets; on narrow
  screens it covers the chain):
  - The mark price, bid and ask with sizes, the spread, breakeven at expiry and how far BTC has to move
    to reach it, and IV, delta, gamma, theta per day and vega per vol point.
  - **Mini ticket:** Buy or Sell, size in contracts, a price (Bid, Mid, Ask or Mark fill it), Limit or
    Post-only, the premium, fee and the most you can lose, and the safety-rail check as you type. Its
    button goes to the same confirm window as the Order ticket, with the same rails. **Open in full
    ticket** moves it to the Order ticket. If you hold the contract, its size, entry and PnL show with a
    **Close** button.
  - **Price and volatility:** mark price, bid and ask, IV (left scale) and real trades, over 1H, 1D, 1W
    or All (the contract's whole life). Bid, ask and IV come from the recorder below for the time it
    has run. Before that, the mark comes from OKX's mark-price candles and the IV is rebuilt from it
    (approximate: OKX rounds candle prices), and there is no bid or ask.
  - **Order book**, **Recent trades** and the **Power law check** (horizon gate, floor / 0.55× / 0.80× /
    NvPL at expiry, and the three-world test at the contract's own IV with edge vs the live bid/ask). Each
    section folds, and the drawer remembers which are open.
  - Icons at the top: show the strike on the main chart, **Ask Claude** (sends the contract's numbers to
    the Claude panel, so you stay on Options), and close.

Data refreshes every 10 seconds while the view is open. With the view in use, Claude's chat also gets a
summary of the option market (put/call open interest, ATM IV per expiry, strikes with the most open
interest).

### Recorded option quotes

While btcliq runs, it saves every contract's bid, ask, mark, sizes, IV, delta and open interest for
both families: once a minute whatever view is open, and every 10 seconds for the family open in the
Options view. Samples older than a day are thinned to one a minute, and nothing is deleted after
that. Expect about 120 MB a day. `--options-recorder off` stops it.

The files are plain SQLite, one per month (UTC): `data/options/2026-11.sqlite`. The view `quotes_v`
gives real units (prices in BTC or USD, IV and delta as fractions, `ts` in milliseconds):

```
sqlite3 data/options/2026-11.sqlite "SELECT datetime(ts/1000,'unixepoch'), bid, ask, mark, iv, fwd
  FROM quotes_v WHERE inst_id='BTC-USD-261127-75000-C' ORDER BY ts"
```

One contract over time and one moment across the whole chain (`WHERE ts = ...`) are both fast. The
snapshot times are in the `snaps` table, with the index price. `src/btcliq/options_store.py` describes
the tables.

## Trading on OKX

The **Order ticket** panel, the account tables under the chart and the `btcliq-trade` command
place real orders on your OKX account: BTC options, perpetuals, dated futures and spot. All of
them go through the same **safety rails**, your limits in `trading.toml`, and every order takes
two steps: a preview showing the order value, premium, fees, worst case and each limit check,
then a confirmation that re-checks everything with fresh prices before sending.

**Setup (once):** create an OKX API key with **Read + Trade** only, bound to your IP; add
`OKX_API_KEY`, `OKX_API_SECRET` and `OKX_API_PASSPHRASE` to `.env`; run
`cp trading.example.toml trading.toml` and set your limits; restart btcliq.
**[SAFETY_RAILS.md](SAFETY_RAILS.md) walks through every step and setting, with example profiles.**

**Order ticket**, top to bottom:

- **Instrument:** type one or pick it from the list; the last few you used sit under the box
  as chips. Below: its live bid, mark and ask, and your position in it, if you hold one.
- **Buy / Long or Sell / Short:** the big switch. Its color carries into the slider and the
  button, so the whole ticket shows which way you're trading (just Buy and Sell for spot and
  options). With the ticket focused, B and S switch sides.
- **Type:** Limit, Market (not for options), Post-only or IOC.
- **Price:** Bid, Mid, Ask and Mark fill it. Clicking a row in the Order book panel, or
  Alt+clicking the chart, puts that price in the ticket (BTC perpetuals and spot).
- **Size** and the **percent slider**, OKX-style: drag it, use the arrow keys, or pick 0, 25%,
  50%, 75% or Max. 100% is the most OKX lets you open with your balance (with *Reduce only*,
  the position you can close). The **amber mark** is where your safety rails stop you (for
  example the per-order cap); past it the fill turns amber and the order is blocked, or for
  option rules you've set to *ask*, needs your OK.
- **Live summary:** order value, premium, margin, fee, breakeven, the most you can lose and
  your position after, plus **every rail check** ("Passes every safety rail", or the rule that
  blocks it). It updates a moment after you stop typing, without placing anything.
- **The button says exactly what happens** ("Buy / Long 0.25 contracts"), then the confirm
  dialog: the exact order, its numbers and every rail (✓ or ✕). A single ✕ disables Confirm.
  Option rules set to *ask* show in amber with a **Place anyway** box. The preview expires
  after a minute.
- **Kill switch** (the ticket's title bar): halts trading and cancels every open order. It asks
  first: type `KILL` in its dialog to unlock the button (Esc or Cancel backs out).
  Positions stay open. **Resume trading** lifts it. **Today's limits** at the bottom shows
  today's opening orders and volume against your daily caps.

**Account tables** (under the chart; refreshed every 4 seconds while open):

- **Positions:** size, value, entry, mark, PnL in dollars with ROE, liquidation price,
  breakeven and, for options, delta and theta per day. Options and futures show their expiry
  under the name (OKX settles at 08:00 UTC), in amber in the last 24 hours.
  - **Same instrument, more than one position:** OKX keeps cross and isolated margin apart, so
    one option can be two positions. They show as one row with the total size, the
    size-weighted average entry, and the summed value, PnL and Greeks (ROE on the combined
    margin). **N parts** under the size opens each part, and each part has its own Close, because
    a close has to go out in that part's margin mode. The row's own Close opens the parts.
  - **Close** opens a small panel by the row. **Size:** type how much to close, or pick 25, 50,
    75 or 100% (the box then shows that size). **Order:** market or limit for perps and
    futures; options are always limit. **Price** (limit): starts at the mid; type your own,
    or pick **Bid**, **Mid** or **Ask**. With your own price (typed, or Bid or Ask) the realized
    PnL is worked out at that price from your average entry; with the box empty or on Mid it is
    the estimate at the current mark. Both are before fees. The rail check updates
    as you type, and says why if the size or price can't be used. **Preview close** (or
    Enter) goes to the usual confirm dialog.
  - **⋯** has: close at a limit price, add to position, show strike and breakeven (options) or
    entry and liquidation (perps) on the chart, open it in the ticket, copy the details.
  - Every close goes out in the margin mode the position is held in (cross or isolated), not the
    `derivatives_margin` default, so it can't open a new position in the other mode.
  - **Close all** (top right) lists every position first, with its PnL and how it closes.
    Each close still goes through the rails, and any that need your OK are left for you to
    close from their row. While it runs, each row shows sent or why not.
- **Open orders:** click a price or size to change it in place (Enter saves, Esc cancels; the
  rails check the new price first), or drag the order's line on the chart and confirm. If an
  option rule set to *ask* flags the change, a window shows the old and new price with the rule,
  and **Save anyway** sends it. **Cancel** takes one click; the notice that follows offers
  **Place again**, which only fills the ticket. **Cancel all** lists the orders first.
- **Trade history, Order history, Funding history:** the last 3 months from OKX, loaded when
  you open the tab. Filter by type, side and instrument; **Aggregate** merges the partial fills
  of one order; **Export CSV** saves what's shown. Funding shows each payment with the rate
  OKX settled at.
- **Balances:** equity, available, in orders, USD value and unrealized PnL per currency.
  btcliq's key can read and trade but can't move funds.
- **On the chart:** your open orders on BTC perps and spot are dashed lines (green buy, red sell);
  positions show their entry (steel blue) and liquidation price (amber); your fills are arrows on
  the candles (B under, S over). Layers → My orders and positions, My fills.

**Confirmation windows:** every window that changes something (Close all, Cancel all, a change
that needs your OK, Resume trading, Delete all drawings) shows the change as now → after, lists
what it touches and names its buttons for what they do. In the red and amber ones the safe
button has focus, so Enter never closes, cancels or deletes anything by accident.

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

The **News** panel follows public Telegram channels.

- **Add** a channel by name (`WatcherGuru`), `@name` or link (`https://t.me/WalterBloomberg`).
  It's checked before it's saved. Click a channel to show only its posts; **×** stops watching it.
- New posts show up live (each channel is checked every 30 seconds). While the News panel is
  collapsed, hidden or scrolled out of view, its title counts how many arrived. A keyword
  match makes the panel glow amber.
- **Highlight keywords** (comma-separated) marks matching posts in amber with 🚨. **Alerts** can send a
  desktop notification for keyword matches or for every new post (your browser asks first).
- **Ask Claude** on a post sends it to the chat with the chart data, to ask how it could
  affect BTC. Claude also sees the last 6 hours of posts with every question.

How it works: it reads Telegram's public web preview (`t.me/s/<channel>`), so no Telegram
account or key is needed, but only **public channels** work (not private channels or groups).
Channel list: `data/telegram.json`. Post history: `data/news.db` (SQLite), kept across restarts.

## Order books

The **Order book** panel is a live ladder of resting orders, merged from
OKX, Hyperliquid, Deribit, Kraken (perpetuals) and Coinbase, Bitfinex (spot), with sizes
in BTC. Binance and Bybit books aren't available from the US. In a short, wide panel
(two columns wide and not very tall) bids and asks sit side by side.

- **Group** sets the price bucket ($0.5 to $250). Hover a row to see each venue's share;
  click it to put that price in the order ticket.
- **Venue chips** turn venues on and off; the dot is green when that venue's book is fresh.
- **Align** shifts each venue's book so its mid matches OKX's. Spot and perpetual prices
  differ by tens of dollars, so without this the merged book shows bids above asks.
- The bottom shows bid vs ask totals within ±0.5%, ±1% and ±2%.
- **Book depth** (Layers) draws resting bids and asks as bars from the chart's left edge,
  next to the liquidation levels, with bucket size following zoom.

Books are polled over REST (every 1 to 3 seconds per venue) and only while the Order book
panel is on screen or the depth overlay is on. Resting orders are real but can be pulled at any moment.

## Ask Claude (the Claude view and panel)

**Claude** is the last view, after Chart, Options, Stablecoins and ETF Flows. The chart gives way to
the conversation; the panels and your account tables stay. The small **Claude** panel in the
column shows the same conversation in compact form, for a quick question while you watch the
chart; the arrow in its title opens the view.

Claude reads, analyses and can **prepare** an action for you to confirm. It never sends,
changes or cancels an order itself.

**What Claude works from**

- **A fresh snapshot with every question:** price, open interest per exchange, the biggest
  estimated liquidation levels, real liquidations, order book walls, recent 15m/1H/1D candles,
  the option market, the power-law state, stablecoins, Telegram headlines, the next high-impact
  events, and a few lines on your OKX account (equity, positions, open orders).
- **What is on your screen:** the view and timeframe, the selected contract, what is in the
  order ticket, your drawings, and your time zone.
- **Lookups** it can run while answering, each shown as a card with the data it returned:

  | Group | Lookups |
  |---|---|
  | Market | candles for any timeframe, the full liquidation map, real liquidations, open interest, order book |
  | Options | every expiry, any expiry's chain, one contract in full with the power-law check |
  | Your OKX account (read-only) | account, positions with Greeks, trade, order and funding history, your safety rails, a quote, a dry run of an order against your rails, the largest size allowed |
  | Context | Polymarket odds, the calendar, Telegram news, stablecoins, power-law levels, your screen |
  | Knowledge and memory | your knowledge notes, your past conversations |
  | Calculators | realized vs implied volatility (with DVOL), the GARCH forecast, option payoff, risk and what-if moves across your positions |
  | Prepared actions | an order of one to four legs, closing a position, cancelling an order, changing an order's price or size (see below) |

- **Knowledge notes** from an Obsidian vault: by default a `bitVault` folder next to this
  project, or `--vault PATH`, or `BTCLIQ_VAULT` in `.env`. A note is knowledge when its
  `categories` property links to `[[Knowledge]]`. Claude sees each note's `summary` line and
  opens the note when a question needs it. A note you add is used from the next question.

**In the view**

- **Conversations** (left): New chat, your past conversations by day, and a search over all of
  them. Hover one to rename or delete it. Every conversation is saved word for word in
  `data/desk/conversations/`.
- **Briefs** (top): Morning brief, Position review, Before the next event, Volatility check,
  Liquidation map. Each runs only when you click it.
- **Model** picker: Haiku 5.5 for quick checks, Sonnet 5.5 (the default), Opus 5.5 or
  Fable 5.2 for the deepest analysis. Your choice is remembered.
- **Web** switch, off for every new conversation. On, Claude may search the web (search only;
  it cannot open pages) and treats what it finds as unverified.
- **Usage chip** (`5H 24% · 18.4k tok`): how much of your Claude plan's 5-hour window is used
  and the tokens this conversation has used. Click it for the weekly window, the last answer,
  today's total and the API key's spend against its daily cap. It turns amber from 80%.
- **Notes:** pinned notes are short rules and preferences sent with every question. Claude can
  propose one (**Save / Edit / Skip**); nothing is pinned until you press Save. With a vault
  connected they are the bullets in its `Pinned notes` note, so you can edit them in Obsidian.
- **Ideas:** ask what to do and Claude gives one concrete idea, or says there is no trade: the
  order, cost and worst case, why, what would prove it wrong, the case against, and whether it
  passes your safety rails (it dry-runs the order first). The idea comes with a card to load it
  into the ticket. It is yours to take or leave; you send every order yourself.
- **Prepared actions:** ask Claude to set up an order, close a position, or cancel or change
  an open order, and you get a card headed **Prepared by Claude** with one button:

  | Card | Its button | What happens next |
  |---|---|---|
  | Order (one card per leg for a spread) | **Review in ticket** | The order ticket is filled in. You check it and press Buy or Sell, then confirm as usual |
  | Close position | **Open Close panel** | The app's Close panel opens on that position (the right margin part), with the size chosen. You preview and confirm there |
  | Cancel order | **Cancel order** | A red window names the order. You confirm, and it is cancelled, with **Place again** in the notice |
  | Change order | **Review change** | A window shows the price or size now and after. You confirm, and the change is sent |

  Preparing sends nothing: Claude's side only dry-runs the action against your safety rails,
  the same check the ticket shows, and an action a rail blocks is never shown. Everything
  that reaches OKX still goes through the page after your click, with the rails checked
  again. Claude cannot use the kill switch, resume trading, change leverage or change your
  limits. Other Claude Code sessions do not get the prepare actions.
- **The line under the message box** is always there: Claude can prepare orders but never
  sends them, and the Telegram posts (and web results, with Web on) it reads are written by
  other people and can be worded to mislead it. It also names what the open conversation
  has read. Check every prepared order before you confirm.
- **Long conversations:** past 16 messages the older part is summarized once in the
  background, and later questions carry the summary instead. Nothing is deleted; Claude can
  search the full text.

**How it reaches Claude** (`--chat`)

| `--chat` | Uses | Cost |
|---|---|---|
| `auto` (default) | Claude Code on this PC, logged in with your Claude account. If your plan's limit is reached, the page offers **Continue on API key** | Your plan's usage limits; the key only when you choose it |
| `claude-code` | Claude Code only, no fallback | Your plan's usage limits |
| `api` | `ANTHROPIC_API_KEY` from `.env` (console.anthropic.com) | Billed per token |
| `off` | Nothing | |

The API key is never used past its daily cap: $2 to start with, changed from the usage chip
(or `BTCLIQ_KEY_CAP_USD` in `.env`).

Claude Code runs once per question, in an empty temporary folder, with none of its own tools
(it can't read files, run commands or open web pages), ignoring your Claude Code settings and
without saving a session. The only thing it can call is this app's lookups.

`--effort` (default `medium`) sets how hard Claude thinks per answer: higher is slower and uses
more of your plan. `--model` sets the model the picker starts on.

**Other Claude Code sessions.** While btcliq runs, sessions started in the folder above this
one can use the read-only lookups through the `btcliq` server in that folder's `.mcp.json`
(`python -m btcliq.desk.bridge`). They get no way to trade.

**Unverified text.** Telegram posts and web results are written by other people. They are
marked unverified on their cards, and Claude is told to weigh them as claims and never to
follow instructions found in them.

**Checking answer quality.** `evals/` holds 17 fixed questions with automatic checks.
`.venv/bin/python evals/run.py` asks them against the running app (one answer each, on your
plan) and saves the results in `evals/results/`.

How it is built is written up in the vault: start at `bitVault/Claude Desk.md`.

## Notes (the Notes view and the Scratchpad panel)

An Obsidian-style editor for your vault. Every note is the vault's own `.md` file, so
Obsidian and btcliq show and edit the same notes. The vault is the one the chat uses
(`--vault`, `BTCLIQ_VAULT`, or a `bitVault` folder next to the project).

- **Notes view** (sixth tab): the vault's folders and notes on the left, with search over
  names and text (`Ctrl+O`). **New note** makes one in `Notes/`; **Today** opens a daily
  note in `Notes/Daily/` with Plan / Trades / Notes headings. Type in the title to rename.
  Outline and backlinks sit on the right.
- **Edit and Read** (`Ctrl+E`), like Obsidian's Source mode and Reading view. Edit shows the
  Markdown with syntax colors, a toolbar, list and task continuation on Enter, Tab to indent,
  `Ctrl+B / I / K / L`, `[[` suggestions, and pasted or dropped images (saved to
  `Notes/attachments`). Read shows the formatted note.
- **Formats:** everything Obsidian writes: headings, bold, italic, strikethrough,
  `==highlight==`, lists and nested tasks (tick them in Read), tables, quotes, callouts
  `> [!warning]` (foldable with `-` / `+`), code blocks with syntax colors, math with
  `$…$` and `$$…$$` (KaTeX), Mermaid diagrams, footnotes, `[[links]]`, `[[note#heading|alias]]`,
  embeds `![[note]]`, `![[note#heading]]`, `![[image.png|300]]` and PDFs, `#tags`,
  `%%comments%%`, front matter shown as properties, and inline HTML. Rendered notes go
  through DOMPurify, so a note can never run a script inside the app.
- **Saving:** every edit saves itself about a second later. If the file changed on disk in
  the meantime (Obsidian saved it), nothing is overwritten: a bar offers *Keep mine*, *Load
  the file's version* or *Copy mine*. An outside change to a note you haven't touched just
  reloads. Deleting moves a note to the vault's `.trash` folder, like Obsidian.
- **Scratchpad panel:** a small editor beside the chart for `Notes/Scratch.md`, with its own
  Edit / Read switch.
- **The chat** can search and read the notes in `Notes/` with the `notes_search` and
  `notes_read` lookups (also over `/mcp`, read-only). Knowledge notes elsewhere in the vault
  work as before.

The formatting libraries (markdown-it, DOMPurify, KaTeX, highlight.js, Mermaid) load from
unpkg / cdnjs the first time you open Read, like the chart library. Edit works offline.
Server code: `desk/notebook.py`, routes `/api/desk/vault/*`. Page code: `static/notes.js`,
`static/notes.css`.

## Data sources

All public, no keys. Binance and Bybit block US connections, so their open interest comes
from CoinGecko instead (refreshed every 2 minutes; CoinGecko's own data is roughly 10 minutes
old for Binance and up to ~1.5 hours for Bybit; the Exchanges panel shows the age).

| Venue | Used for |
|---|---|
| OKX (BTC-USDT perp) | Candles, open-interest history, real liquidations |
| OKX (BTC-USD perp), Hyperliquid, Deribit, Kraken Futures | Live open interest and price |
| Coinbase | Spot price |
| Binance, Bybit (via CoinGecko) | Open interest, for the market's total size |
| Polymarket (Gamma API) | BTC prediction-market odds (Polymarket panels) |
| bitbetf.com, blockchain.info, mempool.space (blockstream.info as fallback) | ETF wallets: BITB's address list, balances, transactions |
| bitbo.io, CoinMarketCap | Published ETF flows and fund assets |

The calendar's sources (Federal Reserve, Roll Call Factbase, BEA, ForexFactory) are listed
under [Calendar](#calendar). Your positions, orders, fills, order history, funding payments
and balances come from OKX's private API with your own key (see [Trading on OKX](#trading-on-okx)).

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
  chat.py        the market snapshot sent with every question to Claude
  desk/          the chat: lookups, calculators, vault, saved conversations, engines, lookup endpoint
  book.py        live order books, merged across venues
  telegram.py    Telegram channel monitor (public web preview)
  options.py     OKX BTC/USD options: chain, contract lookup, greeks
  powerlaw.py    power-law state, invalidation, three-world option checks
  stables.py     global stablecoin supply (DefiLlama)
  etfs.py        spot Bitcoin ETF flows: wallets on-chain, published daily numbers
  macro.py       calendar: Fed, White House schedule, US data releases
  okx_private.py signed OKX v5 client (your keys)
  rails.py       safety rails from trading.toml, halt file, journal
  trader.py      orders, live checks, slider sizing, account and 3-month history
  trade_routes.py  /api/trade/* (localhost-only, per-run token)
  trade_cli.py   btcliq-trade command
  powerlaw_calc.py  unchanged copy of btc-powerlaw/powerlaw.py
  static/index.html   the page (TradingView Lightweight Charts)
  static/desk.js, desk.css   the Claude view and panel
  static/fonts/       JetBrains Mono, with its license (OFL.txt)
```
