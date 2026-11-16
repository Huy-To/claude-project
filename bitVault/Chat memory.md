---
categories:
  - "[[Architecture]]"
summary: The three layers of memory - pinned notes, a running summary, and a word-for-word archive - and what each costs in tokens.
status: built
created: 2026-11-13
---
You asked for memory that uses the fewest tokens and loses the least. The design separates *what is kept* (everything) from *what is sent* (very little).

| Layer | Holds | Sent with a question | Where |
|---|---|---|---|
| Pinned notes | Rules, preferences, open theses you approved | Always, in full | [[Pinned notes]] in this vault |
| Running summary | The older part of a long conversation, in under 300 words | Only in that conversation | In the conversation's file |
| Recent turns | The last 8 messages, word for word | Always | In the conversation's file |
| Archive | Every conversation in full: text, lookup cards, usage | Never, until searched | `btcliq/data/desk/conversations/` |

## Pinned notes
- Claude may **propose** a note with `propose_note`. You see **Save / Edit / Skip**. Nothing is pinned until you press Save.
- You can add, edit and remove notes from **Notes** in the Claude view, or by editing the bullets in [[Pinned notes]] in Obsidian. Both read the same file.
- Limits: 40 notes, 400 characters each. Longer material belongs in a knowledge note.
- Claude is told never to propose something that came from a post or a web page.

## Running summary
When a conversation passes 16 messages (or about 28,000 characters), the older part is summarized once by Haiku in the background, and later questions carry the summary instead of those messages. The last 8 messages always travel in full. The summary keeps decisions, your stated rules, and every concrete idea with its exact numbers.

## Archive and recall
Nothing is deleted by summarizing. Claude can search every past conversation with `conversation_search` and read a stretch with `conversation_read`. So "what did we decide about the 75k calls last week?" works, at the cost of one lookup, only when asked.

## What it costs
A question's memory overhead is the pinned notes (tens to a few hundred tokens) plus recent turns. Starting a new chat for a new topic keeps it lowest.

## Files
`src/btcliq/desk/store.py` (archive), `vault.py` (pinned notes), `prompt.py` (summary rules), `service.py` (when to summarize).

## Related
[[Snapshot and prompt]] · [[Usage meter]]
