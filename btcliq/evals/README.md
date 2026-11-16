# The chat's question set

Seventeen fixed questions for the Claude chat, each with checks a script applies. Use it to see
whether a change to the instructions, a lookup or a knowledge note made answers better or worse.

```bash
.venv/bin/python evals/run.py                       # all 17, on the running app (port 8050)
.venv/bin/python evals/run.py --only idea,limits    # just these
.venv/bin/python evals/run.py --model claude-haiku-5-5
.venv/bin/python evals/run.py --rescore evals/results/FILE.json   # re-apply the checks to saved answers; asks nothing
```

btcliq must be running. Each question is asked in a new conversation, the way the page asks
it, and the conversation is deleted afterwards (`--keep` leaves it). Results go to
`evals/results/` (not kept in git): every answer, the lookups Claude ran, and each check.

**Cost.** One answer per question on your Claude plan, never the API key. The run prints the
tokens it used. It stops by itself if your plan's limit is reached.

## What is checked

`questions.json` holds the questions. A check is one of:

| Check | Passes when |
|---|---|
| `must_call`, `must_call_any`, `must_not_call` | Claude ran (or did not run) those lookups |
| `must_match`, `must_match_any`, `must_not_match` | The answer contains (or does not contain) text matching a pattern |
| `must_propose_note`, `must_not_propose_note` | Claude did (or did not) propose a pinned note |
| `must_prepare`, `must_not_prepare` | Claude did (or did not) prepare an action card of that kind: `order`, `close`, `cancel`, `amend` |
| `if_match` with `then_call`, `then_prepare`, `then_match` | When the answer gives an idea, it also read your limits, prepared the order as a card, and has its Wrong if / Against / Rails lines |

The questions cover: account and risk, volatility, GARCH used rightly and wrongly, the
liquidation map and its limits, a full idea, a spread payoff, your limits, time zones, an
instruction planted in a Telegram post, a "remember this", data the app does not have, a
request to close a position, a request to set up an order, and the power-law horizon rule.

## What it cannot tell you

- **Whether an answer is right.** The checks see that Claude used the right data and said the
  required things, not that its reading of the market is good. Read the saved answers for that.
- **The same thing twice.** The market and your account are live, and Claude's wording varies,
  so one failure is a reason to read that answer, not proof of a fault. Run a failing case
  again with `--only` before changing anything.
- **Patterns miss paraphrases.** If Claude says the right thing in words a pattern does not
  expect, the check fails. Widen the pattern rather than the instructions.

## Adding a question

Add a case to `questions.json` with an `id`, the `ask`, a `why`, and the checks. Prefer checks
on behaviour (which lookups ran) over wording, and never check a live number.

## Baseline

`baseline/2026-11-13-sonnet.json` is the first run: which cases passed, the lookups each one ran, time
and tokens. It leaves the answers out, because they contain account details.
