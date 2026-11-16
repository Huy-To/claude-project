---
categories:
  - "[[Knowledge]]"
summary: How the user's trading limits work and how to respect them when proposing an order. The live numbers come from the safety_rails lookup, not from this note.
source: btcliq SAFETY_RAILS.md
created: 2026-11-13
---
## Use this when
Sizing an idea, explaining why an order would be blocked, or answering "what are my limits".

## How the rails work
- Limits live in `trading.toml` and are re-read on every order. **Always read them with `safety_rails`**; do not quote numbers from memory or from this note.
- Every order is checked when previewed and again just before it is sent. A failed check blocks it.
- **Closing orders** (ones that only shrink a position) skip the halt, the daily caps and the leverage and position caps, so the user can always get out. Size and price limits still apply.
- Some option rules can be set to **ask** instead of block: the order then needs the user's explicit approval.
- The **kill switch** halts trading and cancels every open order. While halted, only closing orders pass.

## The groups of limits
| Group | Covers |
|---|---|
| `order` | One order: value, distance of a limit price from the mark, market orders, open-order count, cooldown |
| `daily` | The UTC day: order count, total value, realized loss |
| `option` | Contracts and premium per order, selling to open, position size, which rules ask |
| `swap`, `futures` | Order size, leverage, position value |
| `spot` | Order size |

## For ideas
1. Read `safety_rails`, then size inside every limit that applies.
2. Run `order_check` and report its verdict in the idea: passes, needs approval (name the rule), or blocked (name the rule).
3. If an idea only works by breaking a limit, say so and offer the largest size that fits. Never suggest editing the limits to make an idea fit.

## What the chat can and cannot do
It can prepare an order, a close, a cancel or a change as a card the user confirms; `prepare_order` and `prepare_close` dry-run it against the rails first and refuse to show one that is blocked. It cannot send, change or cancel anything itself, use the kill switch, resume trading, change leverage or change the limits.
