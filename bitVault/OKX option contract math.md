---
categories:
  - "[[Knowledge]]"
summary: The arithmetic of OKX BTC options - contract size, coin-margined versus USD-margined, premium and breakeven in dollars, and what the Greeks are denominated in.
source: btcliq source code, checked against a live OKX position on 2026-11-13
created: 2026-11-13
---
## Use this when
Converting an option price to dollars, sizing in contracts, or reading position Greeks. Prefer the calculators (`option_payoff`, `order_check`, `position_risk`) over doing this by hand.

## The two families

| | BTC-USD (coin-margined) | BTC-USD_UM (USD-margined) |
|---|---|---|
| Price quoted in | BTC per 1 BTC of underlying | USD per 1 BTC of underlying |
| Premium and P&L settle in | BTC | USD |
| Name | `BTC-USD-261127-75000-C` | `BTC-USD_UM-261127-75000-C` |

Both: **1 contract = 0.01 BTC** of underlying. The name reads family, expiry (YYMMDD), strike, C or P. Expiry is at 08:00 UTC.

## Dollars
- **Premium for n contracts, coin-margined:** n x 0.01 x price in BTC x index. Example: 2 contracts at 0.0030 BTC with BTC at 68,000 costs 2 x 0.01 x 0.0030 x 68,000 = $4.08.
- **Premium, USD-margined:** n x 0.01 x price in USD.
- **Breakeven at expiry:** strike plus premium per BTC for a call, strike minus it for a put, with the premium in dollars per 1 BTC.
- **Payoff at expiry in dollars:** n x 0.01 x max(0, spot - strike) for a call, max(0, strike - spot) for a put.
- **Coin-margined subtlety:** the premium was paid in BTC and the payoff arrives in BTC, so the dollar result also moves with BTC itself. The calculators convert at today's index and say so.

## Buying and selling
- A **long option's worst case is the premium paid** plus fees.
- **Selling to open** is blocked by the user's rails unless they allow it; a short call's loss is unlimited.
- OKX has **no market orders for options**: every option order is a limit order.

## Greeks
From the chain (`options_chain`, `option_contract`), per 1 BTC of underlying: delta (BTC), gamma (per $1), theta (USD per day), vega (USD per volatility point). Multiply by contracts x 0.01.

From positions (`positions`, `position_risk`), already totalled for the position:
- `delta_btc`: the BTC you are effectively long (+) or short (-). Times the index for dollars.
- `theta_usd_per_day`: time decay per day at today's rate. Near expiry this rate can exceed the option's whole remaining value.
- OKX also reports "PA" Greeks (price-adjusted for coin-margined options, in BTC); the app shows them as `delta_pa_btc` and `theta_pa_btc_per_day`.

## Margin modes
The same contract can be held in cross and in isolated margin at once; OKX keeps them as two positions. A closing order must go out in the mode of the part it closes.
