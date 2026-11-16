# claude-project workspace

- **bitVault/** is the Obsidian vault the Claude chat inside btcliq reads. To make research or a rule available to that chat, save it as a knowledge note there: use the `bitvault-note` skill. `bitVault/Home.md` explains the vault; `bitVault/Claude Desk.md` explains how the chat works.
- **Live btcliq data.** When btcliq is running, the `btcliq` MCP server (registered in `.mcp.json`) offers read-only lookups: market snapshot, candles, option chain, volatility, GARCH, your OKX account, calendar. It cannot trade; orders go through `btcliq-trade` and its safety rails.
