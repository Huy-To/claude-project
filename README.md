# btcliq

A live BTC chart in your browser showing **estimated liquidation levels**, built from
open interest across several exchanges.

## Run

```bash
cd ~/Desktop/claude-project/btcliq
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

Pick the chart timeframe (1m, 5m, 15m, 1H, 4H, 1D) with the buttons at the top; your
choice is remembered. A countdown to the close of the current candle is shown next to the
buttons and under the price label on the right axis (turn it off under Display →
Candle countdown). Candles are aligned to UTC and the countdown uses your computer's clock. The liquidation levels don't depend on the timeframe: they're built
from hourly open interest for older history and 5-minute open interest for the last 3 days.

Stop it with Ctrl+C.

## Options (OKX BTC/USD)

Press **Options** in the header to see every live OKX BTC/USD option and look up contracts.

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
  mark. The yellow line marks the index; shaded cells are in the money. Click any cell to
  open that contract. **Strikes** narrows the range around the index.
- **Coin-margined** (`BTC-USD`, priced in BTC) or **USD-margined** (`BTC-USD_UM`, priced
  in USD). **Native / USD** switches how prices are shown.

Data refreshes every 10 seconds while the panel is open. With the panel in use, Claude's
chat also gets a summary of the option market (put/call open interest, ATM IV per expiry,
strikes with the most open interest).

## Telegram news

Open the **News** tab at the top of the sidebar to follow public Telegram channels.

- **Add** a channel by name (`WatcherGuru`), `@name` or link (`https://t.me/WalterBloomberg`).
  It's checked before it's saved. Click a channel to show only its posts; **×** stops watching it.
- New posts show up live (each channel is checked every 30 seconds). While you're on the
  Market tab, the News tab shows how many arrived.
- **Highlight keywords** (comma-separated) marks matching posts in yellow. **Alerts** can send a
  desktop notification for keyword matches or for every new post (your browser asks first).
- **Ask Claude** on a post sends it to the chat with the chart data, to ask how it could
  affect BTC. Claude also sees the last 6 hours of posts with every question.

How it works: it reads Telegram's public web preview (`t.me/s/<channel>`), so no Telegram
account or key is needed, but only **public channels** work (not private channels or groups).
Channel list: `data/telegram.json`. Post history: `data/news.db` (SQLite), kept across restarts.

## Order books

Press **Order book** in the header to open a live ladder of resting orders, merged from
OKX, Hyperliquid, Deribit, Kraken (perpetuals) and Coinbase, Bitfinex (spot), with sizes
in BTC. Binance and Bybit books aren't available from the US.

- **Group** sets the price bucket ($0.5 to $250). Hover a row to see each venue's share.
- **Venue chips** turn venues on and off; the dot is green when that venue's book is fresh.
- **Align** shifts each venue's book so its mid matches OKX's. Spot and perpetual prices
  differ by tens of dollars, so without this the merged book shows bids above asks.
- The bottom shows bid vs ask totals within ±0.5%, ±1% and ±2%.
- **Book depth on chart** (Display section) draws resting bids and asks as bars from
  the chart's left edge, next to the liquidation levels, with bucket size following zoom.

Books are polled over REST (every 1 to 3 seconds per venue) and only while the panel or
the depth overlay is on. Resting orders are real but can be pulled at any moment.

## Ask Claude (chat panel)

The lower half of the sidebar is a chat with Claude. Each question is sent with a fresh
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

Drag the bar between the market info and the chat to resize them. Your conversation is
kept in the browser until you press Clear.

## Data sources

All public, no keys. Binance and Bybit block US connections, so their open interest comes
from CoinGecko instead (refreshed every 2 minutes; CoinGecko's own data is roughly 10 minutes
old for Binance and up to ~1.5 hours for Bybit; the sidebar shows the age).

| Venue | Used for |
|---|---|
| OKX (BTC-USDT perp) | Candles, open-interest history, real liquidations |
| OKX (BTC-USD perp), Hyperliquid, Deribit, Kraken Futures | Live open interest and price |
| Coinbase | Spot price |
| Binance, Bybit (via CoinGecko) | Open interest, for the market's total size |

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
  static/index.html   chart (TradingView Lightweight Charts)
```
