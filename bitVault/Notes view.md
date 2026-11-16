---
categories:
  - "[[Architecture]]"
summary: How btcliq's Notes view and Scratchpad panel read and write this vault's Markdown files, what formats they show, and how they avoid overwriting Obsidian.
status: built
created: 2026-11-15
---
btcliq has an Obsidian-style editor for this vault: the **Notes** tab (sixth view) and the **Scratchpad** panel beside the chart. The notes are the vault's own `.md` files, so Obsidian and btcliq edit the same thing. Delivered as `btcliq-notes.patch` in the workspace root.

## Choices (2026-11-15)

| Question | Answer |
|---|---|
| Which notes | All of the vault. New notes go into `Notes/`, daily notes into `Notes/Daily/`, pasted images into `Notes/attachments/` |
| Editor | Edit / Read toggle (`Ctrl+E`), like Obsidian's Source mode and Reading view. Live Preview was offered and not chosen |
| Where | Both: its own view tab, plus a Scratchpad panel for `Notes/Scratch.md` |
| Chat access | The chat can search and read every note in `Notes/` (`notes_search`, `notes_read`) |

## Formats

Everything Obsidian writes renders in Read: headings, bold, italic, strikethrough, `==highlight==`, lists, nested tasks (tick them in Read), tables, quotes, callouts `> [!type]` (foldable with `-`/`+`), code with syntax colors, `$math$` and `$$math$$` (KaTeX), Mermaid, footnotes, `[[links]]`, `[[note#heading|alias]]`, embeds `![[note]]`, `![[note#heading]]`, `![[image.png|300]]`, PDFs, `#tags`, `%%comments%%`, front matter as a properties table, and inline HTML. `$95k to $100k` stays text, as in Obsidian.

## Safety

- Paths are checked on the server (`desk/notebook.py`): they must stay inside the vault, must not touch hidden folders like `.obsidian`, and must be `.md` (or an image or PDF for embeds). Symlinks that point out of the vault are refused.
- Routes are `/api/desk/vault/*`, so they get the same localhost, origin and page-token checks as the chat.
- Every rendered note goes through DOMPurify, because the page holds the trading token. Mermaid runs in strict mode. Pasted SVGs are refused.

## Not overwriting Obsidian

Each save names the hash of the version it started from. If the file changed on disk since (Obsidian saved it), the server refuses with `409` and the current text. The page then shows a bar: *Keep mine*, *Load the file's version*, *Copy mine*. Both overwrite choices go through the styled `ask()` window and show the first line that differs. An outside change to a note with no unsaved edits just reloads (checked every 4 seconds). Deleting moves the note to `.trash`, as Obsidian does.

## Known limits

- Renaming a note does not rewrite `[[links]]` that point to it (Obsidian does). The page says so after a rename.
- The formatting libraries load from unpkg / cdnjs the first time Read opens. Edit works offline.
- The Claude view's **Notes** button still means pinned notes, which can be confused with the new Notes tab.

Related: [[Claude Desk]], [[Pinned notes]], [[Lookups]].
