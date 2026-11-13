# Safety rails for OKX trading

btcliq can send real orders to your OKX account, from the **Trade** tab in the browser or
from the `btcliq-trade` command (which your Claude Code trading session uses). This guide
sets up the protections around that, in the order an order meets them:

| Layer | What it stops | Where it lives |
|---|---|---|
| 1. API key permissions | Withdrawals, transfers, loans: the key can only read and trade | okx.com → API |
| 2. IP allowlist | Anyone who steals the key, from any other machine | okx.com → API |
| 3. Limits file | Orders that are too big, badly priced, too frequent, or over your daily loss | `trading.toml` |
| 4. Preview and confirm | Orders you didn't mean: you see cost, worst case and every check before sending | browser dialog / `preview` then `place` |
| 5. Kill switch | Everything new, at once, and cancels open orders | Kill button on the Order ticket (type `KILL` to confirm) / `btcliq-trade kill` |
| 6. Journal | Nothing, but records every attempt for review | `data/trade_journal.jsonl` |

If the limits file is missing or has a mistake in it, **trading is off**. btcliq never sends
an order under limits you didn't write.

---

## 1. Create the OKX API key

On okx.com: **Profile → API → Create API key**.

1. **Purpose:** API trading.
2. **Permissions:** tick **Read** and **Trade** only. Leave **Onchain**, **Transfer**, **Earn** and **Loan**
   unticked. Without those, the key can't move funds out of your account even if it leaks.
3. **IP allowlist:** add your computer's public IP address (search "what is my IP" in a browser).
   OKX then refuses the key from any other address. OKX also deletes trade-enabled keys that
   aren't bound to an IP after a stretch of inactivity, so binding it keeps it alive. If your IP
   changes (home internet sometimes does), btcliq shows *"Your IP address isn't on this API key's
   IP allowlist"*. Edit the key on OKX and add the new address.
4. **Passphrase:** pick one and keep it. OKX shows the secret once, so copy all three now:
   API key, secret key, passphrase.

> Account mode matters: OKX's **Spot mode** can't trade options, perps or futures. If you want
> derivatives, switch to **Futures mode** (Trade → settings → Account mode) before trading.
> btcliq tells you if this is the problem.

## 2. Put the key in `.env`

In the `btcliq` folder, add to `.env` (the file is ignored by git, so it never gets committed):

```ini
OKX_API_KEY=xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx
OKX_API_SECRET=XXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXX
OKX_API_PASSPHRASE=your passphrase
```

Restrict who can read it:

```bash
chmod 600 .env
```

The keys stay on the btcliq server. The browser never sees them.

> Want to rehearse first? Create a key under OKX **Demo trading** instead, and add `OKX_DEMO=1`.
> The Order ticket's title bar then says **Demo** instead of **Live**. Demo and live keys aren't
> interchangeable: OKX error 50101 means the key and `OKX_DEMO` don't match.

## 3. Set your limits in `trading.toml`

```bash
cp trading.example.toml trading.toml
```

Then edit it. A few rules:

- Changes apply to the **next order**. No restart needed.
- **A misspelled setting turns trading off.** For example, `max_contract = 5` instead of
  `max_contracts`. The Order ticket and `btcliq-trade status` show which line is wrong. This is
  deliberate: a typo must never silently remove a limit.
- Any number can be `"off"` (with quotes) to stop enforcing it. `0` means zero, not off.
- **Closing orders get through the "stop" rails.** These are orders that only shrink a position you
  hold. They skip the halt, the daily caps, `max_open_orders`, leverage, the position caps and the
  selling-to-open check, so you can always get out. Size, price and cooldown checks still apply to them.

### Every setting

**Top level**

| Setting | Default | What it does |
|---|---|---|
| `enabled` | `false` | Master switch. `false` blocks every order (handy for pausing your trading session). |
| `instruments` | all four | Which kinds of BTC instrument can trade: `"OPTION"`, `"SWAP"` (perps), `"FUTURES"`, `"SPOT"`. Only BTC is ever traded, whatever you list. |

**`[order]`: applies to every order**

| Setting | Example | What it does |
|---|---|---|
| `max_notional_usd` | `2000` | Largest order by **underlying value**: options and USDT perps = contracts × 0.01 BTC × price; coin-margined perps/futures = contracts × $100; spot = BTC × price. A 2-contract option order is $1,370 at BTC $68,500. |
| `price_band_pct` | `3` | Fat-finger guard: a limit price more than ±3% from the mark price is refused. A buy at $78,000 with BTC at $68,500 is blocked. |
| `allow_market_orders` | `true` | Market orders for spot, perps and futures. OKX has no market orders for options, so those always need a price. |
| `max_open_orders` | `10` | No new opening order while this many orders rest on the account (all of OKX, not just btcliq). |
| `cooldown_seconds` | `3` | Minimum gap between two orders. Stops a runaway loop from firing 50 orders in a second. |
| `preview_ttl_seconds` | `60` | How long a preview stays valid. After that you preview again with fresh prices. |

**`[daily]`: per UTC day (resets 00:00 UTC; 01:00 in Europe or 19:00 in New York)**

| Setting | Example | What it does |
|---|---|---|
| `max_orders` | `20` | Opening orders placed through btcliq today. |
| `max_notional_usd` | `10000` | Total underlying value of today's opening orders through btcliq. |
| `max_loss_usd` | `500` | Daily stop: once realized P&L plus fees today drops below −$500, opening orders are refused for the rest of the day. Read from your OKX fills, so it **includes trades made in the OKX app**. Coin-settled P&L is converted at the index price. |

**`[option]`**

| Setting | Example | What it does |
|---|---|---|
| `max_contracts` | `5` | Per order. 1 contract = 0.01 BTC, so 5 = 0.05 BTC of exposure. |
| `max_premium_usd` | `500` | Premium paid (or received) by one order. 3 contracts at 0.0150 BTC with BTC at $68,500 = $30.83. |
| `price_band_pct` | `30` | Option quotes are wide: a 0.0010 BTC option often has a 0.0009/0.0011 market, which is 10% either side. So options get their own wider band. It overrides `[order]`. |
| `allow_selling_to_open` | `false` | Selling options you don't own (writing them) is blocked. Short calls have unlimited risk. Selling options you **do** own (closing) is always fine. |
| `max_position_contracts` | `20` | Largest position in any one option contract after the order fills. |
| `ask` | `[]` | Option rules that **ask you** instead of blocking. See below. |

**`[swap]` and `[futures]`**

| Setting | Example | What it does |
|---|---|---|
| `max_order_usd` | `"off"` | Optional per-order cap tighter than `[order] max_notional_usd`, just for perps or futures. |
| `max_leverage` | `3` | New orders are refused while the instrument's leverage on OKX is above 3x. Lower it with `btcliq-trade leverage BTC-USDT-SWAP 3` (which also won't go above your cap). |
| `max_position_usd` | `5000` | Position value after the order fills. Long 0.5 BTC-USDT-SWAP (50 contracts) at $68,500 = $34,250. |

**`[spot]`**: `max_order_usd`, a per-order cap for spot only.

**`[halt]`**: `allow_closing = true` lets closing orders through while halted. Set it to `false` to
freeze everything.

**`[account]`**

| Setting | Default | What it does |
|---|---|---|
| `derivatives_margin` | `"cross"` | Margin mode for options, perps and futures: `"cross"` (shares your whole account's margin) or `"isolated"` (each position has its own margin, so a loss can't spread). |
| `spot_mode` | `"auto"` | `"cash"` in Spot/Futures mode, `"cross"` in multi-currency or portfolio margin. Leave it on auto unless OKX rejects spot orders. |

### Ask instead of block (options)

Some rules are better as a question than a wall. List them under `[option] ask`, and an option
order that breaks one isn't refused. Instead:

- **In the browser**, the confirm dialog says **Needs your OK** and shows each problem in amber with
  a **Place anyway** box. Confirm unlocks only after you tick every box. If you don't tick, nothing is sent.
- **From the command line**, `preview` exits with code **4** and prints the rules that need approval.
  `place <id>` refuses (code 4) until you add `--accept rule1,rule2`.

```toml
[option]
ask = ["price_band_pct", "daily.max_loss_usd", "daily.max_orders", "option.allow_selling_to_open"]
```

Rules that can ask: `price_band_pct`, `daily.max_orders`, `daily.max_notional_usd`, `daily.max_loss_usd`,
`option.allow_selling_to_open`, `option.max_contracts`, `option.max_premium_usd`,
`option.max_position_contracts`, `order.max_notional_usd`, `order.max_open_orders`.
The master switch, the BTC-only rule, the halt (kill switch) and the cooldown always block, and perps
always use hard limits.

What the price check tells you: selling **above** the option's value (a take-profit) or buying
**below** it just waits in the book, so the message says *"it will rest in the book until the market
gets there"*. Selling far below or buying far above the value is the dangerous direction, so the
message says *"you'd pay well above / sell well below what it's worth. A typo?"*

Approval is checked again when you confirm, with fresh prices. If something new comes up in
between, the dialog shows it and waits for you to tick it too.

### Example profiles

Copy the parts you want into `trading.toml`. Anything you leave out keeps its default.

**A. First week: prove it works with tiny sizes**

```toml
enabled = true
instruments = ["OPTION", "SWAP"]

[order]
max_notional_usd = 300        # about 0.004 BTC
price_band_pct = 2
allow_market_orders = false   # limit orders only while you learn the ticket
max_open_orders = 3
cooldown_seconds = 10

[daily]
max_orders = 5
max_notional_usd = 1000
max_loss_usd = 50

[option]
max_contracts = 1
max_premium_usd = 50
allow_selling_to_open = false
max_position_contracts = 3

[swap]
max_leverage = 2
max_position_usd = 300
```

**B. Options buyer only (your swing style: long calls and puts, defined risk)**

```toml
enabled = true
instruments = ["OPTION"]      # perps, futures and spot are refused outright

[order]
max_notional_usd = 7000       # 10 contracts = 0.1 BTC of exposure

[daily]
max_orders = 10
max_notional_usd = 20000
max_loss_usd = 750

[option]
max_contracts = 10
max_premium_usd = 600         # never pay more than $600 premium in one order
allow_selling_to_open = false # you can sell what you bought, never write new options
max_position_contracts = 25
```

With this, the worst case of any single order is its premium. The confirm dialog shows that
number before you send.

**C. Options plus a perp hedge (delta-hedging a long option position)**

```toml
enabled = true
instruments = ["OPTION", "SWAP"]

[order]
max_notional_usd = 10000
price_band_pct = 2

[option]
max_contracts = 10
max_premium_usd = 1000

[swap]
max_leverage = 3
max_position_usd = 10000      # a hedge never needs more than your option exposure
```

**D. For your Claude Code trading session (an agent placing orders)**

Agents can loop. These settings cap what a confused loop can do:

```toml
enabled = true                # flip to false to pause the agent without touching anything else
instruments = ["OPTION"]

[order]
max_notional_usd = 3500
allow_market_orders = false
max_open_orders = 4
cooldown_seconds = 30         # at most 2 orders a minute
preview_ttl_seconds = 45

[daily]
max_orders = 6                # a swing trader rarely needs more
max_notional_usd = 10000
max_loss_usd = 300            # the agent stops opening trades after a $300 day

[option]
max_contracts = 5
max_premium_usd = 300
allow_selling_to_open = false
max_position_contracts = 10
```

btcliq and the command line read the same `trading.toml`, so this applies to you in the
browser too. If you want looser limits for yourself, raise them while you trade by hand
and put them back after. Changes apply immediately.

**E. Writing options (only if you mean to)**

```toml
[option]
allow_selling_to_open = true
max_contracts = 2
max_position_contracts = 4    # caps how short you can get in any one contract
max_premium_usd = 400
```

The confirm dialog shows **"Worst case: Unlimited"** for short calls, and the strike value
minus premium for short puts.

## 4. What you see when an order is checked

**In the browser**, *Preview* opens a dialog with:

- the order exactly as it will be sent (side, size, price, type, margin mode),
- order value, premium, estimated fee, margin, breakeven, worst case,
- your position before → after,
- warnings that don't block the order (for example "your buy price is above the ask, so it fills now as a taker"),
- every rail: **✓** passed, **✕** failed. One ✕ disables the Confirm button. Rules you set to ask
  show in amber with a **Place anyway** box instead.

The Confirm button is colored by side, and the focus starts on **Cancel**, so pressing Enter never sends.
The preview expires (60 s by default). Confirming runs **every rail again with fresh prices**. If
anything changed, for example the price moved outside the band, your position changed, or the kill
switch was pressed, the order is refused and you see why.

**From the command line**, the same two steps:

```bash
$ btcliq-trade --text preview BTC-USD-261127-75000-C buy 2 --px 0.0150
LIVE · BUY 2 contracts BTC-USD-261127-75000-C @ 0.015 BTC (limit, cross)
Option: 1 contract = 0.01 BTC (settles in BTC); priced in BTC per 1 BTC
Market: bid 0.0145 / ask 0.0155 / mark 0.015 · index 68,500.0
Order value $1,370.00 (0.0200 BTC) · premium $20.55 (0.000300 BTC) · est. fee $0.41
Worst case: $20.96. The premium you pay (plus fees) if it expires worthless
Breakeven at expiry: $76,028
Position: 0 → 2 contracts
  ok  Trading is enabled in trading.toml
  ok  OPTION on BTC is allowed
  ok  Order value $1,370 vs limit $2,000
  ok  2 contracts vs limit 5
  ...
Passes every rail
Preview id 3f9a1c22b7e0: confirm within 59s with  btcliq-trade place 3f9a1c22b7e0

$ btcliq-trade --text place 3f9a1c22b7e0
...
PLACED · ordId 1234567890 · clOrdId btcliq3f9a1c22b7e0
```

A blocked preview prints `NO` next to each failed rail and exits with code 2.

## 5. The kill switch

Any of these halts trading and cancels **every open order on the account**:

- the **Kill** button in the Order ticket's title bar,
- `btcliq-trade kill --reason "CPI spike"`,
- or, if btcliq isn't running: `touch data/TRADING_HALTED` (halts only; it can't cancel orders without btcliq).

The **Kill** button doesn't fire on one click. It opens a dialog that shows how many open orders
will be canceled, and its button stays locked until you type `KILL` (any case; Enter then
confirms, Esc backs out). The server checks this too: `/api/trade/kill` refuses a request that
doesn't carry the typed word, so a stray click or an old cached page can't trip it. The
command line has no extra prompt, so the trading agent can still halt in one step.

While halted, new opening orders are refused everywhere, browser and command line alike, until you
resume. Closing orders still go through unless `[halt] allow_closing = false`. Resume with the
**Resume trading** button, `btcliq-trade resume`, or by deleting `data/TRADING_HALTED`.

What the kill switch does **not** do:

- **It doesn't close positions.** Use the **Close** button next to each position (it fills the ticket with a reduce-only order), or OKX.
- **It doesn't cancel trigger or TP/SL orders** you set up in the OKX app (btcliq never creates those).

## 6. Using it from your Claude Code trading session

Run from the `btcliq` folder with its virtualenv active (`source .venv/bin/activate`), or call
`.venv/bin/btcliq-trade` directly. Output is JSON. Exit codes:

| Code | Meaning |
|---|---|
| 0 | Done |
| 1 | Error (bad input, OKX rejected it, network) |
| 2 | **Blocked by the safety rails.** Read `checks` for the rules that failed. Don't retry the same order. |
| 3 | Not set up (no keys, or no valid `trading.toml`) |
| 4 | **Needs the user's approval.** Rules in `[option] ask` failed; `needs_approval` lists them. Ask the user. |

```bash
btcliq-trade status                         # keys valid? permissions? limits OK? halted?
btcliq-trade account                        # equity, balances, positions, open orders, recent fills
btcliq-trade limits                         # the limits in force, after defaults
btcliq-trade quote BTC-USD-261127-75000-C   # contract spec + bid/ask/mark/index
btcliq-trade preview BTC-USD-261127-75000-C buy 2 --px 0.0150    # → JSON with "id", "blocked", "checks", "numbers"
btcliq-trade place <id>                     # send it (re-checked first)
btcliq-trade amend BTC-USD-261127-75000-C <ordId> --px 0.0145
btcliq-trade cancel BTC-USD-261127-75000-C <ordId>
btcliq-trade cancel-all
btcliq-trade kill --reason "agent: invalidation hit"
btcliq-trade leverage BTC-USDT-SWAP 3
```

Preview flags: `--px PRICE` (limit), `--type post_only|ioc|fok`, `--market` (not options), `--reduce`
(only close). Sizes are contracts, or BTC for spot. Prices are in the instrument's unit:
**BTC for coin-margined options** (`BTC-USD-…`), USD for `BTC-USD_UM-…` options, perps, futures and spot.

Suggested rules for the agent's brief:

1. Run `status` at the start of a session. Stop if the exit code isn't 0.
2. Always `preview`, read `numbers.worst_case_usd` and `warnings`, then `place`. Never skip the preview.
3. On exit code 2, report the failed `checks` to the user. Don't adjust the order to slip under a limit.
4. On exit code 4, show the user the messages for the rules in `needs_approval` and ask. Only after
   they say yes, run `place <id> --accept <those rules>`. Never pass `--accept` on its own judgment.
5. Run `kill` if its own invalidation rules fire and it can't manage positions safely.

## 7. Test your setup safely

1. `btcliq-trade --text status`: you should see `keys ok`, `permissions read_only,trade`, and your IP
   allowlist. If permissions show more than read and trade, delete the key and create a new one.
2. Look at the Order ticket: its title bar should say **Live**, and the Balances tab under the chart
   should show your balances.
3. Place an order that can't fill. Buy 0.01 contract of `BTC-USDT-SWAP` (0.0001 BTC, about $7),
   **Post-only**, at the bid minus 2% (inside a 3% band). Confirm it, check it appears under Open orders
   and as a dashed line on the chart, then cancel it with ×.
4. Try to break a rule on purpose: set the price 10% away and preview. You should see a ✕ for the
   price band and a disabled Confirm button.
5. Press the **Kill switch**: the button in its dialog stays locked until you type `KILL`. Confirm,
   check the halt banner, then **Resume trading**.

## 8. What the rails don't cover

- **Orders placed in the OKX app or website** don't go through btcliq. The daily loss stop counts
  their P&L, and `max_open_orders` counts their open orders, but the daily order and volume caps
  only count orders sent through btcliq.
- **Fees are estimates** (your fee tier × order value; option fees capped at 12.5% of premium).
- **Market orders** can fill worse than the quote in a fast market. The band check only applies to limit prices.
- **Cross margin** means a losing perp or futures position can draw on your whole account. Use
  `derivatives_margin = "isolated"` if you want each position walled off.
- **Positions aren't managed for you.** Nothing closes a position automatically, and liquidation is OKX's.
- The journal and halt file live in `data/`. Deleting that folder resets today's counters.
