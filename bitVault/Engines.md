---
categories:
  - "[[Architecture]]"
summary: The two ways a question reaches Claude - your login through Claude Code, and the API key as a capped fallback - and exactly what each is allowed.
status: built
created: 2026-11-13
---
An **engine** is the path a question takes to Claude. Both engines get the same system prompt, the same conversation and the same lookups.

| | Claude Code engine | API engine |
|---|---|---|
| Uses | Your Claude subscription, through the Claude Code app on this PC | `ANTHROPIC_API_KEY` in `.env` |
| Cost | Counts toward your plan's 5-hour and weekly windows | Billed per token |
| When | Always first | Only when you choose it, and never past the daily cap |
| Lookups reach Claude as | MCP tools at `/mcp` | Tool definitions in the request |
| Tested | Against the real Claude, end to end | Against a stand-in only (you declined paid testing) |
| File | `engine_cli.py` | `engine_api.py` |

## Choosing
- `--chat auto` (default): Claude Code when installed; the key is offered when the plan limit is hit.
- `--chat claude-code`: login only, no fallback. `--chat api`: key only. `--chat off`: no chat.
- When the plan limit is reached the page shows **Continue on API key**. Saying yes applies to that conversation. The daily cap (default $2, editable from the usage chip) is checked before each question and between lookup rounds.

## What Claude Code is allowed
One `claude -p` run per question, with:

| Flag | Effect |
|---|---|
| `--tools ""` | No built-in tools: no files, no shell, no code |
| `--tools WebSearch` | Only when the web switch is on. Search only: no page fetching, so nothing read on the web can make Claude send your data to an address of its choosing |
| `--mcp-config … --strict-mcp-config` | The only MCP server is btcliq's `/mcp` |
| `--allowedTools mcp__btcliq` | Lookups run without asking |
| `--permission-prompts none` | Anything else is refused, never asked |
| `--setting-sources ""` | Your Claude Code settings, hooks and CLAUDE.md files are ignored |
| `--no-session-persistence` | The run is not saved as a Claude Code session |
| `--effort medium` | Less thinking per answer than Claude Code's default. Set with `btcliq --effort` |

It runs in an empty temporary folder, with the API key removed from its environment so it cannot bill the key.

## What the API engine does
The standard loop: send the conversation with the lookups as tools; when Claude asks for lookups, run them together, return every result in one message, repeat (at most 12 rounds). It marks two cache points, uses search-only web search when the switch is on, and asks Anthropic to retry on another model if a safety classifier declines a request (if the API rejects that option, it carries on without it).

## Models
The picker offers Haiku 5.5, Sonnet 5.5 (default), Opus 5.5 and Fable 5.2. `btcliq --model` sets the starting choice. Summaries of long conversations always use Haiku.

## Related
[[Question flow]] · [[Usage meter]] · [[Safety model]]
