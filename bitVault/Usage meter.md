---
categories:
  - "[[Architecture]]"
summary: What the usage chip shows, where each number comes from, and how the API key cap is enforced.
status: built
created: 2026-11-13
---
The chip in the Claude view and panel reads `5H 24% · 18.4k tok`. Click it for the breakdown.

| Number | Source |
|---|---|
| Plan, 5-hour window % and reset time | Claude Code reports it during every answer (`rate_limit_event`) |
| Plan, this week % | Same |
| This conversation, tokens | Sum over its answers: sent plus received |
| Last answer: in, cached, out | Claude Code's result for that answer |
| Today, tokens and answers | The ledger in `data/desk/usage.json` |
| API key today, $ of cap | Tokens on the key path times Anthropic's list prices |

## Reading it
- The plan percentages cover **everything** you do on your plan, including Claude Code sessions outside btcliq. They update only when the chat answers, so they can be stale; a window that has reset since the last answer shows as reset.
- "In" counts every token sent, including the part read from cache, which costs far less. That is why "in" looks large.
- The chip turns amber from 80% of the 5-hour window.
- While a conversation is on the API key, the chip shows dollars against the cap instead.

## The cap
The daily cap (default $2, change it in the chip's panel, or set `BTCLIQ_KEY_CAP_USD`) is checked before each question on the key and between lookup rounds. Prices are in `store.py` (`PRICES`, as of 2026-11-03); an unknown model is priced as the most expensive one, so the cap errs on the safe side.

## Files
`src/btcliq/desk/store.py` (`Usage`), `engine_cli.py` and `engine_api.py` (reading usage), `desk.js` (`dkChip`).

## Related
[[Engines]] · [[Question flow]]
