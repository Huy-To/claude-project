---
categories:
  - "[[Guides]]"
summary: Plain meanings for the terms used in the architecture notes.
created: 2026-11-13
---
| Term | Meaning here |
|---|---|
| **Lookup** | A named question Claude can ask the running app, such as `positions` or `option_payoff`. Elsewhere called a *tool*. See [[Lookups]]. |
| **Tool use** | Claude answering with "run this lookup with these arguments" instead of text. The app runs it, sends the result back, and Claude continues. |
| **Agent loop** | Repeating that exchange until Claude writes the final answer. One answer is usually two or three rounds. |
| **MCP** | Model Context Protocol: the standard way a Claude client discovers and calls tools. btcliq offers its lookups through it. See [[Lookup endpoint (MCP)]]. |
| **Engine** | The path a question takes to Claude: Claude Code (your login) or the Anthropic API (the key). See [[Engines]]. |
| **Claude Code** | Anthropic's command-line app. btcliq runs it once per question, without its file and shell tools. |
| **Headless** | Running Claude Code with no window: text in, a stream of events out. |
| **System prompt** | Claude's standing instructions, sent before the conversation. |
| **Snapshot** | The market summary sent with each question. See [[Snapshot and prompt]]. |
| **Token** | The unit Claude's usage is counted in; roughly four characters of English, fewer for numbers. |
| **Prompt cache** | Claude remembering the start of a request it has just seen, so the same start costs far less the next time. |
| **Context** | Everything in front of Claude for one answer: instructions, lookup list, snapshot, conversation, lookup results. |
| **Pinned note** | A short note you approved, sent with every question. See [[Chat memory]]. |
| **Knowledge note** | A note in this vault that Claude can open when a question needs it. |
| **Prompt injection** | Text written by someone else that tries to give Claude instructions. See [[Safety model]]. |
| **Safety rails** | Your trading limits in `trading.toml`, checked on every order. |
| **Plan window** | Your Claude subscription's usage allowance: a 5-hour window and a weekly one. |
| **Stream** | The answer arriving piece by piece (text, lookup started, lookup finished) instead of all at once. |
