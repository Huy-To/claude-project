---
categories:
  - "[[Decisions]]"
summary: How the update was built, step by step, including what was measured, which tools and plugins were used, and which were not.
created: 2026-11-13
---
## Steps
1. **Read first.** The chat code, the server, the page, every data module, the trading rails, and the transcripts of the QuantClaude session, to learn what "QuantClaude knowledge" referred to (the GARCH audit and fix).
2. **Prove the risky idea before asking.** A 60-line test server and one run of Claude Code showed that your Claude login can call lookups hosted by the app. Timings with two lookups: Haiku 2 s, Sonnet 4 s, Fable 7 s, Opus 11 s to first text.
3. **Ask.** Nine rounds of questions, 34 in all ([[Design decisions 2026-11-13]]). The usage data Claude Code reports was checked before promising a usage meter.
4. **Build the server side in layers**, each usable alone: registry, calculators, vault, store, prompts, engines, endpoint, service, routes.
5. **Check each layer against reality**: every lookup against live data and your real account (read-only); one question end to end.
6. **Build the page**, then drive it in a headless browser.
7. **Write tests** for what the checks had shown, with every outside party faked.
8. **Write this vault.**

## What changed because of a measurement
- OKX's `deltaBS` turned out to be in BTC, not dollars. Found on your real position; the label was fixed before anything used it.
- A typical question sends about 17k tokens per round, more than the "3,000+" I estimated when asking about the snapshot. The fixed 7k is cached; the snapshot is 4 to 6k. The numbers are in [[Question flow]].
- Claude Code tries a newer protocol method first. The endpoint's "method not found" answer makes it fall back, which was confirmed rather than assumed.

## Plugins and tools

| Tool | Used for |
|---|---|
| `claude-api` skill | The API loop, cache points, model choices, effort, refusal handling |
| `skill-creator` plugin | The `bitvault-note` skill for other sessions |
| garch-method (your fixed fork) | Runs behind the `garch_forecast` lookup |
| Vercel plugin | Read, not used: you chose this-PC-only |
| Headless Chromium | Driving the page for checks |

## How it is delivered
Branch `claude-desk` in a separate working copy, exported as `btcliq-claude-desk.patch`. Your running btcliq folder changes only when you apply the patch.

## Related
[[Testing]] · [[Claude Desk]]
