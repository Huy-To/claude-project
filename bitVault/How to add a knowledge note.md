---
categories:
  - "[[Guides]]"
summary: Three steps to make the chat know something new, by hand or from a Claude Code session.
created: 2026-11-13
---
## By hand
1. Create a note in the vault's top folder. Name it for what it covers, for example `Funding rate signals`.
2. Insert the **Knowledge note** template (see [[How this vault works]]), or add these properties yourself:
   ```yaml
   categories:
     - "[[Knowledge]]"
   summary: One line saying what this note lets the chat do.
   ```
3. Write the note. Use `##` headings: the chat reads long notes one section at a time.

The chat sees the new note on its next question. No restart.

## From a Claude Code session
Sessions started in the `claude-project` folder know about this vault (see [[Other sessions]]). Ask one: "save this as a knowledge note in bitVault". It uses the same template.

## What makes a good knowledge note
- **The summary is a pointer.** Claude sees only the summary until a question needs the note. Say what the note is *for*: "When to trust the GARCH forecast, and what to use instead for options".
- **Rules first, evidence second.** State the rule with its number and its limit, then what it rests on.
- **Sections under about 1,500 words.** One read returns at most about 9,000 characters; longer notes come back as an outline and are read by section.
- **Date anything that goes stale.** Live numbers belong in a lookup, not a note.

## Checking it worked
In btcliq's Claude view, click **Notes**: the line at the bottom says how many knowledge notes Claude can read. Or ask: "which knowledge notes do you have?"
