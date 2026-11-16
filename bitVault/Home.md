---
categories:
  - "[[Guides]]"
summary: Start here. What this vault is, how it is organized, and how btcliq's chat uses it.
created: 2026-11-13
---
# bitVault

This vault does two jobs:

1. **It is what btcliq's chat knows.** Every note in the [[Knowledge]] category can be read by Claude inside btcliq. Edit a note here and the chat uses the new text on the next question.
2. **It is the technical reference for the chat.** The [[Architecture]] notes explain how the chat works, the [[Decisions]] notes record what you chose and why, and the [[Guides]] show how to extend it.

## Where to go

| I want to | Open |
|---|---|
| See how the whole thing fits together | [[Claude Desk]] |
| Follow one question from the page to Claude and back | [[Question flow]] |
| See everything Claude can look up | [[Lookups]] |
| See what I decided on 2026-11-13 | [[Design decisions 2026-11-13]] |
| Add research the chat should know | [[How to add a knowledge note]] |
| Understand a term | [[Glossary]] |
| Learn how this vault is laid out | [[How this vault works]] |

## The categories

- [[Knowledge]]: what the chat can read (research, rules, how the data is made)
- [[Architecture]]: how the chat is built
- [[Decisions]]: choices and their reasons
- [[Guides]]: how to do things
- [[Memory]]: the pinned notes the chat carries into every conversation

> [!tip] One rule to remember
> A note is knowledge when its `categories` property links to `[[Knowledge]]`. Nothing else is sent to the chat.
