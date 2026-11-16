---
categories:
  - "[[Guides]]"
summary: The recipe for giving the chat one more thing it can read from the app.
created: 2026-11-13
---
A lookup is one function in `src/btcliq/desk/lookups.py`. Adding one makes it available to the in-app chat, the API fallback and other Claude Code sessions at once. See [[Lookups]] for the idea.

## The recipe
```python
@R.lookup("funding_rate", title="Funding rate", group="market", description="""
    Current and recent funding on the OKX BTC-USDT perpetual. Use it for questions
    about carry or crowded positioning.""",
          params={"hours": Param("integer", "How far back (default 24)", default=24, minimum=1, maximum=720)})
async def funding_rate(ctx: Ctx, hours: int):
    rows = await ctx.desk.ex.okx_funding(hours)        # a fetcher you would add to exchanges.py; read-only
    return {"columns": ["time", "rate_pct"], "rows": rows}
```

## Rules
1. **Read only.** A lookup never places, changes or cancels anything.
2. **The description is the interface.** Claude decides from it alone. Say what the lookup returns and when to use it.
3. **Return a table as `{"columns": [...], "rows": [[...]]}`.** It costs the fewest tokens and the page draws it as a table. A plain object draws as a grid of labelled values.
4. **Name fields for their unit**: `_usd`, `_pct`, `_btc`. The card formats values by their names.
5. **Raise `LookupFailed("what to change")`** for anything Claude can fix by asking differently.
6. **Mark other people's text** with `untrusted=True`, as `news` does.
7. **Chat only?** Add `scope="chat"`. Other sessions will not see it.

## Then
- Add a test in `tests/test_desk_chat.py` (see [[Testing]]).
- Restart btcliq. The lookup count on the Claude view's welcome text goes up by one.
- If it deserves a line in Claude's instructions, edit `SYSTEM` in `src/btcliq/desk/prompt.py`. Usually the description is enough.
