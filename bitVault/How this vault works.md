---
categories:
  - "[[Guides]]"
summary: The few Obsidian ideas this vault uses - properties, categories, Bases, links, templates - and why.
created: 2026-11-13
---
This vault follows the same pattern as your School vault, with a few additions. Five ideas cover all of it.

## 1. Notes are flat
Almost every note sits in the vault's top folder. You find notes by **link**, **category** or **search** (Ctrl+O), not by browsing folders. Only three folders exist: `Categories`, `Templates` and Obsidian's own `.obsidian`.

## 2. Properties
The block between the two `---` lines at the top of a note holds its properties. This vault uses:

| Property | Meaning |
|---|---|
| `categories` | Which category notes this note belongs to. A note can be in several. |
| `summary` | One line describing the note. For knowledge notes, this is the line Claude sees. |
| `created` | The date the note was written. |
| `source` | Where the content came from (a session, a file, a link). |
| `status` | For architecture notes: `built`, `planned` or `changed`. |

## 3. Categories and Bases
A category is an ordinary note in the `Categories` folder, such as [[Knowledge]]. It shows a live table of every note whose `categories` property links to it. The table is a **Base**: a small `.base` file in `Templates/Bases` that says "list the notes that link here". You never edit the table. You add a note to a category by putting the category in the note's properties.

> [!note] Improved from the School vault
> The School bases match a note only when the category is its *only* category. These match a note that has the category *among* its categories, so a note can belong to two.

## 4. Links
`[[Note name]]` links to another note. Links are what make the graph view (Ctrl+G) and backlinks useful, so the notes here link wherever one mentions another.

## 5. Templates
`Templates` holds starting points. To make a new knowledge note: create a note, run **Templates: Insert template** from the command palette (Ctrl+P), pick **Knowledge note**. The Templates core plugin's folder must be set to `Templates` (Settings, Core plugins, Templates).

## What btcliq reads
Only notes in the [[Knowledge]] category, plus [[Pinned notes]]. It never reads `Templates`, `.obsidian` or the other categories. Details in [[Knowledge notes in the vault]].
