---
categories:
  - "[[Architecture]]"
summary: What is tested automatically, what was checked by hand against the real app, and what has not been tested.
status: built
created: 2026-11-13
---
## Automatic tests
Run from the btcliq folder: `.venv/bin/python -m pytest -q`. They use no network, no Claude and no OKX.

| File | Covers |
|---|---|
| `tests/test_desk_core.py` (35 tests) | Argument checking and result cutting; reading this vault's format; pinned notes surviving hand edits; conversations, search and path safety; the usage ledger and cap; prompt order and cache points; summary rules; Black-76, realized volatility, payoff and position-risk arithmetic |
| `tests/test_desk_chat.py` (28 tests) | Every lookup group against a fake app; account lookups against the fake OKX, asserting nothing is ever posted; the `/mcp` protocol; who is let in and who is refused; the bridge when the app is down; Claude Code's flags and its output stream, with a stand-in program; the API loop, cap, refusal and cut-off handling, with a stand-in client; a question end to end, the limit and key flow, Stop, and background summaries |

Together with the 96 older tests: 159. Six of the 28 cover [[Prepared actions]]: a card is shown and nothing is posted to OKX; a blocked or malformed order is never shown; other sessions never get the prepare lookups; a split position needs its part named; a cancel or change needs a real open order; the card is saved with the conversation.

## Checked by hand on 2026-11-13
Against a second copy of the app on another port, with real market data and, with your permission, your real OKX account read-only:
- every lookup returned sensible data, including `order_check` against your real rails;
- GARCH ran on 1,499 OKX daily closes with `data_quality.ok: true`;
- a question travelled end to end through your Claude login, with lookups, in 12 seconds;
- the Claude view and panel in a headless browser: no console errors, cards drawn, usage chip and popovers working.

## Not tested
- **The API key path against the real API.** You declined paid testing, so it is tested against a stand-in only. Its first real run will be the first time you press "Continue on API key".
- **A real plan-limit event.** The handling is tested with a simulated one.
- **Obsidian itself.** The notes and Bases follow Obsidian's documented format but were not opened in Obsidian from here.

## The question set
`evals/` in the btcliq folder holds 17 fixed questions (16 at the first run) with automatic checks, to compare answer quality before and after a change. `evals/README.md` explains how to run it and what it cannot tell you.

First run, 2026-11-13, Sonnet 5.5, against live data and your real account (read-only):

| | |
|---|---|
| Passed as first scored | 14 of 16 |
| The two failures | Both were checks that were too narrow; the answers were right. "No trade" was marked down for not reading your limits, and the GARCH caveat was worded differently from the pattern. The checks were corrected, and all 16 saved answers pass them |
| Time | 147 seconds, 3 to 19 seconds per answer |
| Tokens | 585,000 in (about half read from cache), 13,400 out |

What it showed: Claude read the GARCH knowledge note before answering a GARCH question, refused an instruction planted in a Telegram post and said so, proposed a pinned note without claiming it was saved, said it could not close a position, and applied the power-law horizon rule.

A full run is not free: it used a noticeable share of the plan's 5-hour window. Use `--only` for one or two cases after a small change.

Also checked in the page after that run: saving and removing a pinned note (it appears in [[Pinned notes]]), conversation search and rename, Stop, and the web switch.

## Related
[[Claude Desk]] · [[Build process]]

After patch 2 the four affected questions were run once on Sonnet (idea, the planted instruction, "close my position now", "set up an order"): 151,000 tokens in, 40 seconds. Three passed as first scored; the fourth was again a pattern that was too crude (Claude refused the planted instruction and prepared nothing, but described the post in its own words). All four pass the corrected checks.
