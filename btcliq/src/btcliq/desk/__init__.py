"""Claude Desk: the chat that can look things up in the running app.

- registry.py   what a lookup is, argument checking, result packing
- lookups.py    the lookups themselves (market, options, account, calendar, knowledge, memory)
- calc.py       calculators (volatility, GARCH, option payoff, position risk)
- vault.py      the Obsidian vault: knowledge notes and pinned notes
- store.py      conversations on disk, search over them, the usage ledger
- prompt.py     what Claude is told: system prompt, snapshot, conversation so far
- engine_cli.py answers through the Claude Code app (your Claude login)
- engine_api.py answers through the Anthropic API (the key in .env), as the fallback
- mcp.py        the lookup endpoint Claude Code and other sessions call
- bridge.py     lets other Claude Code sessions reach that endpoint
- service.py    ties it together: one question in, a stream of events out
- routes.py     the HTTP routes the page uses
"""
