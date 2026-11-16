---
categories:
  - "[[Architecture]]"
summary: What Claude is told on each question, in what order, how big each part is, and why the order saves tokens.
status: built
created: 2026-11-13
---
## The order

| # | Part | Changes | About |
|---|---|---|---|
| 1 | Lookup list (31 definitions) | Only when the code changes | 5k tokens |
| 2 | System prompt: role, working method, data limits, rules for other people's text, the idea format, power-law rules, memory rules | Only when the code changes | 1.8k tokens |
| 3 | Knowledge index: one line per knowledge note | When a note is added or its summary changes | small |
| 4 | Pinned notes | When you pin or unpin | small |
| 5 | Summary of the older part of the conversation | When it is long | up to 300 words |
| 6 | Recent turns, word for word | Every question | varies |
| 7 | Snapshot | Every question | 4 to 6k tokens |
| 8 | Screen: view, timeframe, contract, ticket, drawings, time zone | Every question | small |
| 9 | The question | Every question | |

## Why this order
Claude's **prompt cache** reuses the start of a request it has just seen, at a small fraction of the price. It matches from the first character, so one changed character near the top makes everything after it full price. So:
- parts 1 to 4 hold nothing that changes per question, not even the time;
- the snapshot, which changes every time, comes after all of them.

Measured: a follow-up question read 9.7k of its 19.7k input tokens from cache.

The old chat put the snapshot (with the clock) inside the system prompt, so nothing could be cached.

## The snapshot
You chose to keep the full snapshot on every question. It is built by `chat.py` and `prompt.py`:

| Section | About |
|---|---|
| Price, open interest by venue, liquidations near price | 0.8k chars |
| Biggest long and short liquidation levels | 0.8k |
| Real liquidations, last hour | 0.3k |
| Telegram news, last 6 hours (marked unverified) | varies |
| Order book walls (when the book feed is running) | 0.5k |
| Candles: 32 x 15m, 48 x 1H, 30 x 1D | 4.1k |
| Stablecoins | 1.5k |
| Power-law state (rules are in part 2) | 1.6k |
| Options: expiries, near-the-money chain, contracts you named | 4k |
| Next high-impact events | 0.2k |
| Your OKX account: equity, positions, open orders | 0.4k |

About 14k characters in total. The last two sections are new in this update.

## The idea format
When you ask what to do, Claude is told to give one concrete idea or say there is no trade: the order, cost and worst case, why, what proves it wrong, the case against, and the rails verdict from `order_check`. It must read your rails first and check the order before presenting it.

## Files
`src/btcliq/desk/prompt.py`, `src/btcliq/chat.py`.

## Related
[[Chat memory]] · [[Lookups]] · [[Safety model]]
