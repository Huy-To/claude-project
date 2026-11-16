---
categories:
  - "[[Knowledge]]"
summary: Where each number in the app comes from and how far to trust it - above all, that liquidation levels are model estimates, not real positions.
source: btcliq README and source code
created: 2026-11-13
---
## Use this when
The user asks how reliable something is, or an answer leans on liquidation levels, open interest, the order book or Polymarket.

## Liquidation levels: estimates
- They are **not real positions**. No exchange publishes where traders would be liquidated.
- **Method:** when open interest rises, the model assumes new positions opened at that bar's price, half long and half short, over a fixed leverage mix (5x 10%, 10x 25%, 20x 25%, 25x 15%, 50x 15%, 100x 10%) with 0.5% maintenance margin. When open interest falls, every level shrinks in proportion. When price trades through a level, it is removed.
- **History:** hourly open interest for older days and 5-minute for the last 3 days, both from OKX, scaled up to the whole market (total open interest / OKX's).
- **Live:** OKX, Hyperliquid, Deribit and Kraken every 30 seconds, scaled the same way.
- **Blind spot:** Binance and Bybit block the user's region. Their open interest arrives minutes to about 1.5 hours late through CoinGecko and is used for the market's size only. So the map assumes their traders position like traders elsewhere.
- **How to use them:** as zones where forced orders would cluster, wider than the bucket. A cluster is a reason a move could accelerate, not a prediction that price goes there.

## Real liquidations: an undercount
OKX's public feed returns only the most recent liquidation orders, and only OKX. Totals understate the market.

## Order book: real, but removable
Resting orders merged from OKX, Hyperliquid, Deribit, Kraken, Coinbase and Bitfinex. Walls can be pulled at any moment, and Binance and Bybit are missing. Liquidation levels are where forced market orders would hit; walls are where they would be absorbed.

## Options: OKX's live chain
Prices, IV and Greeks come from OKX's public endpoints, cached a few seconds. Max pain and "top open-interest strikes" are computed from open interest. See [[OKX option contract math]].

## Power law: daily and slow
Daily closes from Coin Metrics, live price from the OKX index. A long-horizon model: see [[Bitcoin power law brief]].

## Stablecoins: about hourly
DefiLlama, all chains. Peg prices can be stale for illiquid coins.

## Polymarket: crowd odds
Probabilities are the last traded odds in a prediction market. Thin markets move on small bets. Use them as a second opinion on levels, not as a forecast.

## Calendar
Fed events from the Federal Reserve's calendar, the President's schedule via Roll Call Factbase, data dates from BEA and BLS, forecasts from ForexFactory. Exchange holidays are not included.

## Telegram news: unverified
Posts from channels the user follows. Claims, not facts. Note a post's time against the candles before linking it to a move.
