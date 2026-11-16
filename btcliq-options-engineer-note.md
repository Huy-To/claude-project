# btcliq Options tab: server work for the engineer

**From:** the Options tab UI/UX redesign, 2026-11-14 (branch `options-ui`, patch `btcliq-options-ui.patch`).
**Scope of this note:** server and data work only. The redesigned page is finished and works without any of it. Each item below makes one part of the page show more, and the page picks it up on its own.

## What the page does today without you

| Part of the drawer | What it shows now | What it shows after your work |
|---|---|---|
| Price and volatility chart | Mark, bid, ask and IV sampled **in the browser** every 10 s while the Options tab is open (lost on reload), plus trades as hourly dots from the existing 1H trade candles | Full history from the recorder and OKX mark-price candles, for any range up to the contract's whole life |
| Recent trades | Trades grouped by hour (from the same 1H candles) | Single trades: time, side, price, size |

The page asks `GET /api/options/history`. Today that returns 404, so the page sets `OP.histApi = false` and uses its fallback for the rest of the session. As soon as the route returns 200, the page uses it.

## 1. Bid/ask/IV recorder (the user's decisions)

The user chose all of these:

- **Storage: SQLite.** No new dependencies. Their research session reads it with plain `sqlite3`. Parquet was offered as an alternative (5–8× smaller, but it adds pyarrow) and declined for now.
- **Families: both.** `BTC-USD` (about 906 contracts) and `BTC-USD_UM` (about 730).
- **Rate:**
  - Every 60 s in the background while btcliq runs, whatever view is open.
  - Every 10 s for the family on screen while the Options tab is open. **Reuse the chain read the page already triggers every 10 s** (`/api/options/chain`, `options.py`) rather than fetching twice. The cleanest hook is wherever the chain snapshot is built (`snap = {...}` near line 154 of `options.py`).
- **Keep forever,** but thin the 10 s samples to 1 minute once they are a day old.
- **Size estimate:** about 2.4 M rows a day at 60 s for both families, so about 95 MB a day, or about 35 GB a year. The disk has about 366 GB free.

Suggested layout (adjust as you see fit):

- Files: `btcliq/data/options/YYYY-MM.sqlite`, one per month. `data/` is already in `.gitignore`.
- `contracts(id INTEGER PRIMARY KEY, inst_id TEXT UNIQUE, family TEXT, expiry TEXT, exp_ms INTEGER, strike REAL, type TEXT, ct_mult REAL, tick REAL)`
- `snaps(ts INTEGER PRIMARY KEY, family TEXT, index_px REAL)`: one row per chain read. The index is stored here, not repeated on every row.
- `quotes(contract_id INTEGER, ts INTEGER, bid INTEGER, ask INTEGER, mark INTEGER, bid_sz INTEGER, ask_sz INTEGER, iv INTEGER, iv_bid INTEGER, iv_ask INTEGER, delta INTEGER, oi INTEGER, fwd REAL, PRIMARY KEY (contract_id, ts)) WITHOUT ROWID`
  - Prices as integers in ticks.
  - IV and delta as integers in basis points (1e-4).
  - Sizes and OI as whole contracts.
- Write in one transaction per snapshot, with WAL mode on.
- Thinning: once a day, keep the last sample of each minute for rows older than 24 h.

## 2. History endpoint the page already calls

```
GET /api/options/history?instId=BTC-USD-261127-75000-C&range=1H|1D|1W|All
200 → {
  "bars":   [ { "t": 1794680400000, "mark": 0.0024, "bid": 0.0023, "ask": 0.0025, "iv": 0.493 }, ... ],   // oldest first, ms
  "trades": [ { "time": 1794680123000, "px": 0.0024, "sz": 12, "side": "buy" }, ... ]                  // optional
}
```

- **Units:**
  - `mark`, `bid`, `ask` and `px` are in the contract's own price unit (BTC for coin-margined, USD for `_UM`), the same as the chain.
  - `iv` is a fraction (0.493 = 49.3%).
  - Any field can be `null`. The page skips nulls, so a bar without bid/ask still draws its mark and IV.
- **Mark history:** OKX `GET /api/v5/market/history-mark-price-candles?instId=<option>&bar=...` works for options. I checked it on 2026-11-14. It returns about 100 bars per request.
  - Pick the bar size from the range: 1H uses 1m, 1D uses 5m, 1W uses 1H.
  - For All, pick it from the contract's age so it takes at most a few requests (for example 4H or 1D).
- **Bid, ask and IV:** from the recorder (item 1) for the time it has run.
- **IV for older bars:** rebuild it from the mark and the index with Black-76, the model OKX uses. Index history comes from `GET /api/v5/market/index-candles?instId=BTC-USD`. Coin-margined option prices are in BTC per 1 BTC of underlying, so convert with the index at that bar.
  - **Check it:** compare the rebuilt IV on the latest bar with OKX's live `markVol` for an ITM and an OTM contract in each family.
  - If it's off by more than about 1 vol point, add `"iv_src": "rebuilt"` per bar, and I'll label it approximate in the UI.
- **Cache** OKX answers for 30–60 s. The page asks again every 10 s while a contract is open.

## 3. Single trades on `/api/options/contract`

Add `"trades": [ { "time": ms, "px": number, "sz": number, "side": "buy" | "sell" }, ... ]` (newest first, up to 50) to the existing contract response. The source is OKX `GET /api/v5/market/trades?instId=<option>&limit=50`. Fetch it next to the book and candles in `options.py` (around line 198, the `asyncio.gather`). The drawer's Recent trades section shows them as soon as they arrive (`dwTrades()` in `index.html`).

## 4. Nothing else is needed

- **Expiry stats** are computed in the browser from the chain the page already loads: put/call, ATM IV, 25Δ skew, max pain and expected move.
- **Mini ticket:** the drawer's ticket uses the existing `/api/trade/check` and `/api/trade/preview`, the same confirm window and the same safety rails. No new order route.
- **Ask Claude:** no change.

## Tests worth adding

- Recorder: writing a snapshot, thinning, the month-file roll-over, and the integer round trip (ticks and bps).
- History endpoint:
  - Range-to-bar mapping and pagination.
  - Null handling.
  - 404-free behaviour for an unknown contract: return 200 with empty `bars`. A 404 would turn the page's fallback on.
- IV rebuild against a few live `markVol` values (stored as fixtures).
