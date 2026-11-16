---
categories:
  - "[[Architecture]]"
summary: How the lookups are offered to Claude Code - the /mcp endpoint, its two kinds of token, and the protocol in brief.
status: built
created: 2026-11-13
---
**MCP** (Model Context Protocol) is the standard way a Claude client finds and calls tools. btcliq's server is itself a small MCP server at `http://127.0.0.1:8050/mcp`.

## Why MCP
Claude Code cannot be handed Python functions. It can be handed the address of an MCP server. Hosting one inside the app means the lookups run in the app's own process, next to the live data, with no second program and no copy of the data.

## The protocol, as much as is used
Each message is one JSON-RPC request sent by HTTP POST and answered as JSON.

| Method | Answer |
|---|---|
| `initialize` | The protocol version both sides will speak, and "this server has tools" |
| `tools/list` | Every lookup the caller may use: name, description, argument schema |
| `tools/call` | The lookup's result as text, with `isError` set when it failed |
| `ping` | An empty result |
| `notifications/*` | No answer (HTTP 202) |

Claude Code 2.1.325 first tries a newer method, `server/discover`. The server answers "method not found" and Claude Code falls back to `initialize`. This was observed in testing and works.

## Two kinds of caller

| Caller | Token | Lifetime | Sees |
|---|---|---|---|
| The in-app chat | Random, made per question | Until that answer is done | All 35 lookups; its calls draw cards on the page |
| Other Claude Code sessions | `data/desk/session-token` | Until you delete the file | The 29 read-only lookups; no `screen`, no `propose_note`, no prepare lookups |

The token travels in an `X-Btcliq-Desk` header (or `Authorization: Bearer`). The page's own token is refused here, and a desk token is refused on the page's routes.

## The guard
`routes.py` applies the same checks trading uses, before any handler runs:
- the `Host` header must be localhost (stops DNS-rebinding sites);
- a browser `Origin`, when present, must be local (stops other websites);
- then the token.

## Files
`src/btcliq/desk/mcp.py` (protocol), `routes.py` (guard), `bridge.py` ([[Other sessions]]).

## Related
[[Lookups]] · [[Safety model]] · [[Engines]]
