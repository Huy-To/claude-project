---
categories:
  - "[[Decisions]]"
summary: Every choice made for the Claude Desk update, with the reason, and what is built versus waiting.
created: 2026-11-13
---
The brief: give btcliq's chat access to live data and to QuantClaude's knowledge. These are your answers to the design questions, in the order asked.

## What it should know and do

| # | Question | You chose | Why it matters | Status |
|---|---|---|---|---|
| 1 | What "QuantClaude knowledge" means | Its research findings, its tools run live, and future research. No live link to the session | A live link costs tens of seconds and plan usage per question; a shared vault gives the same knowledge for free | Built |
| 2 | Extra live data | All four: your OKX account, Polymarket and calendar, deeper market data, what is on your screen | | Built |
| 3 | Read or act | Read, plus prepare orders that you confirm, with a notice about the risk | Nothing is sent without your click; rails apply | Built (reading in patch 1, preparing in patch 2) |
| 4 | Where | This PC only | Keys and feeds stay local; Vercel not needed | Built |

## Engine, model, cost

| # | Question | You chose | Status |
|---|---|---|---|
| 5 | Engine | Claude login first, API key as fallback | Built |
| 6 | Model | A picker, Sonnet 5.5 by default | Built |
| 7 | Data sent up front | The full snapshot, plus lookups | Built |
| 8 | Calculations | Built-in calculators only. No free-form code | Built |

## Behaviour

| # | Question | You chose | Status |
|---|---|---|---|
| 9 | Advice style | Concrete ideas, you decide: order, cost, why, wrong-if, against, rails | Built, with the "Review in ticket" card |
| 10 | Web access | A switch, off by default | Built (search only, no page fetching) |
| 11 | Memory | Most token-efficient, least data loss | Built as three layers, see [[Chat memory]] |
| 12 | Key fallback | Ask first, with a daily cap, plus an indicator showing usage % and tokens | Built |
| 13 | Who pins notes | You approve each one | Built |
| 14 | Usage meter | A chip, details on click | Built |
| 15 | Where the chat sits | Its own view | Built |
| 16 | Showing its work | Full cards: every lookup with its data | Built |

## Actions (patch 2)

| # | Question | You chose | Status |
|---|---|---|---|
| 17 | What Claude may prepare | New orders, closing a position, cancel or amend, two-leg ideas | Built, see [[Prepared actions]] |
| 18 | Risk notice | One always-visible line in the Claude view | Built: under the message box in the view and in the panel |

## Experience and delivery

| # | Question | You chose | Status |
|---|---|---|---|
| 19 | Shortcuts | One-click briefs; nothing runs by itself | Built |
| 20 | The small panel | Keep it, showing the same conversation | Built |
| 21 | Other sessions | Both directions | Built, see [[Other sessions]] |
| 22 | Delivery | The reading part first, then wait for your feedback | Done: patch 1, then patch 2 after you said to go on |
| 23 | Code home | A new branch `claude-desk` and a patch file | Done |
| 24 | Explainer | A technical reference in Obsidian structure | This vault |
| 25 | Vault | A new one, next to your projects, named bitVault | Done |
| 26 | One vault | Yes: the knowledge notes are what the chat reads | Built |
| 27 | Conventions | Like the School vault, improved | [[How this vault works]] |
| 28 | Conversations | A list with search | Built |
| 29 | Test set | Build it, run once, keep it | Done: 16 questions in `evals/`, results in [[Testing]] |
| 30 | Paid API testing | No: stand-in only | Honoured. The key path is untested against the real API |
| 31 | Read-only OKX checks | Yes | Done |

## Choices I made for you
Where you left it to me, and why:
- **Lookups over MCP, hosted inside the app**, not a separate program: one process, no copy of the data.
- **Web search without page fetching**: closes the one route by which injected text could send your data out.
- **Effort "medium"**: Anthropic's starting point for multi-step tool use on Sonnet 5.5; less thinking per answer than the default, which saves plan usage.
- **Power-law rules moved from the snapshot into the fixed instructions**: same text, but now cached instead of resent.
- **Pinned notes stored in this vault**, so Obsidian and the app edit the same file.
- **A skill, not a long CLAUDE.md**, to teach other sessions the note format: it costs tokens only when used.
- **Vercel plugin not used**: you chose this-PC-only. It would be the route to a hosted version.

## Related
[[Claude Desk]] · [[Build process]] · [[Safety model]]
