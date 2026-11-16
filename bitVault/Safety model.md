---
categories:
  - "[[Architecture]]"
summary: What could go wrong with a chat that reads your account and other people's text, and what stops each thing.
status: built
created: 2026-11-13
---
## The risks and what stops them

| Risk | What stops it |
|---|---|
| The chat places or changes an order | It has no lookup that can. Lookups read, and the four prepare lookups only draw a card ([[Prepared actions]]). Claude Code runs with no shell, no files, no code. Covered by tests that check nothing is ever posted to OKX, including by the prepare lookups. |
| A Telegram post or web page tells Claude what to do (**prompt injection**) | Such text is marked unverified in the data and on the card. Claude's instructions say to treat it as a claim, never as an instruction, and to tell you when a source tried. Even if that fails, the worst outcome is a wrong answer or a card you did not ask for: nothing reaches OKX without your click, and your rails are checked again when it does. The line under the message box says this at all times. |
| Injected text makes Claude leak your account data | With web off there is nowhere to send it. With web on, Claude can search but cannot fetch a page, so it cannot call an address chosen by an attacker. |
| Another website reads your account through the chat | `/api/desk/*` and `/mcp` answer only on localhost, refuse other origins, and need a token a website cannot obtain. |
| Another program on this PC calls the lookups | It would need `data/desk/session-token`, readable only by your user. A program running as you could read your `.env` anyway. |
| The API key runs up a bill | Used only when you choose it; a daily cap; the cap is rechecked between lookup rounds. |
| A bad note steers every future answer | Pinned notes are saved only when you press Save. Knowledge notes are written by you or by sessions you run. |
| A lookup floods the conversation | Results are cut to about 24,000 characters. |

## What is deliberately not there
- No way to edit files, run commands or run code from the chat.
- No page fetching, only search.
- No automatic runs: nothing spends your plan unless you ask a question.

## The one place to stay alert
A prepared card looks the same whether Claude reasoned well or was misled. No card acts on one click: each hands over to a screen or a window where you confirm (the Cancel card got its window on 2026-11-13, at your request). Read the card and the window: instrument, side, size, price. The always-visible line names what the conversation has read.

## Related
[[Lookup endpoint (MCP)]] · [[Engines]] · [[Design decisions 2026-11-13]]
