---
categories:
  - "[[Architecture]]"
summary: How btcliq finds this vault, which notes count as knowledge, and how Claude reads them without loading them all.
status: built
created: 2026-11-13
---
## Finding the vault
In order: `btcliq --vault PATH`, then `BTCLIQ_VAULT` in `.env`, then a folder named `bitVault` next to the btcliq project. No personal path is stored in the code.

## What counts as knowledge
A note whose `categories` property links to `[[Knowledge]]`. Skipped: `Templates`, `.obsidian`, `.trash`, and `Pinned notes.md` (handled separately, see [[Chat memory]]).

## How Claude reads them
Loading every note into every question would cost thousands of tokens each time. Instead:
1. **Index, always.** The system prompt lists each knowledge note as `title: summary`. A dozen notes cost a few hundred tokens.
2. **Body, on demand.** `knowledge_read` returns a note. A note over about 9,000 characters comes back as its outline; Claude then asks for the section it needs.
3. **Search.** `knowledge_search` returns matching passages with their note and section.

Claude is told the summary is a pointer, not the rule, and to read the note before applying it.

## Freshness
The vault is re-scanned at most every two seconds, and a file is re-read only when it changed. A note added in Obsidian or by another session is available on the next question.

## Who writes knowledge
- You, in Obsidian.
- Claude Code sessions in `claude-project`, such as QuantClaude ([[Other sessions]]).
- The in-app chat cannot write knowledge notes. It can only *propose* pinned notes.

## Files
`src/btcliq/desk/vault.py`.

## Related
[[How to add a knowledge note]] · [[Knowledge]]
