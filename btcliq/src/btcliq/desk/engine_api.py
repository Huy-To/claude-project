"""Answers through the Anthropic API with the key in .env. This is the fallback path, billed
per token, so it runs only when you choose it and stops at your daily cap.

The loop is the standard one: send the conversation with the lookups as tools; when Claude
asks for lookups, run them, send the results back, and repeat until it answers.
"""

from __future__ import annotations

import asyncio
import logging

from .engine_cli import Job, RunResult
from .store import cost_usd

log = logging.getLogger("btcliq")

MAX_STEPS = 12                  # lookup rounds per question
MAX_TOKENS = 16_000
FALLBACK_BETA = "server-side-fallback-2026-07-01"
# Models whose safety classifiers can decline a request and that support a server-side retry on
# another model. Haiku has no server-side fallback.
WITH_FALLBACK = ("claude-sonnet-5-5", "claude-opus-5-5", "claude-fable-5-2")
WEB_TOOL = {"type": "web_search_20260209", "name": "web_search", "max_uses": 5}


def _add(total: dict, usage) -> None:
    if usage is None:
        return
    total["in"] += getattr(usage, "input_tokens", 0) or 0
    total["out"] += getattr(usage, "output_tokens", 0) or 0
    total["cache_read"] += getattr(usage, "cache_read_input_tokens", 0) or 0
    total["cache_write"] += getattr(usage, "cache_creation_input_tokens", 0) or 0
    stu = getattr(usage, "server_tool_use", None)
    total["web_searches"] += (getattr(stu, "web_search_requests", 0) or 0) if stu else 0


class ApiEngine:
    name = "api"
    label = "Anthropic API"

    def __init__(self, key: str, client=None):
        if client is None:
            import anthropic
            client = anthropic.AsyncAnthropic(api_key=key)
        self.client = client
        self.use_fallbacks = True      # turned off for the session if the API rejects the option

    def _stream(self, job: Job, tools: list, messages: list):
        kw = dict(model=job.model, max_tokens=MAX_TOKENS, messages=messages,
                  # The system prompt never changes between questions, so it is a cache point; the
                  # top-level marker caches the growing tail between the steps of one answer.
                  system=[{"type": "text", "text": job.system, "cache_control": {"type": "ephemeral"}}],
                  cache_control={"type": "ephemeral"})
        if tools:
            kw["tools"] = tools
        if job.effort:
            kw["output_config"] = {"effort": job.effort}
        if self.use_fallbacks and job.model in WITH_FALLBACK:
            return self.client.beta.messages.stream(betas=[FALLBACK_BETA], fallbacks="default", **kw)
        return self.client.messages.stream(**kw)

    async def run(self, job: Job, emit, tools: list | None = None, call_tool=None) -> RunResult:
        """`tools` are the lookup definitions; `call_tool(name, args)` runs one and returns
        (text for Claude, ok)."""
        import anthropic

        res = RunResult()
        tools = list(tools or [])
        if job.web:
            tools.append(WEB_TOOL)      # runs on Anthropic's side; search only, no page fetching
        messages = list(job.messages)
        bad_json = 0
        try:
            for _ in range(MAX_STEPS):
                if job.budget_usd is not None and cost_usd(job.model, res.usage) >= job.budget_usd:
                    res.stop, res.error = "cap", "Today's API key cap was reached partway through this answer."
                    return res
                try:
                    async with self._stream(job, tools, messages) as stream:
                        async for event in stream:
                            if event.type == "content_block_delta" and event.delta.type == "text_delta":
                                res.text += event.delta.text
                                emit({"type": "text", "text": event.delta.text})
                        msg = await stream.get_final_message()
                    bad_json = 0
                except ValueError:
                    # A lookup's arguments arrived as JSON the SDK couldn't parse. Nothing ran; ask again.
                    bad_json += 1
                    if bad_json > 2:
                        raise
                    continue
                except anthropic.BadRequestError as e:
                    if self.use_fallbacks and job.model in WITH_FALLBACK and not messages[len(job.messages):]:
                        log.info("API rejected the refusal-fallback option (%s); continuing without it", e)
                        self.use_fallbacks = False
                        continue
                    raise
                _add(res.usage, msg.usage)
                self._web_cards(msg, emit)
                if msg.stop_reason == "refusal":
                    res.stop = "refusal"
                    res.error = "Claude declined to answer this one."
                    return res
                if msg.stop_reason == "pause_turn":          # a web search is still running: send it back to continue
                    messages.append({"role": "assistant", "content": msg.content})
                    continue
                calls = [b for b in msg.content if b.type == "tool_use"]
                if not calls:
                    if msg.stop_reason == "max_tokens":
                        res.stop, res.error = "cut", "The answer hit the length limit and was cut off."
                    return res
                if msg.stop_reason == "max_tokens":           # a cut-off lookup request can't be trusted: don't run it
                    res.stop, res.error = "cut", "The answer hit the length limit before a lookup was complete."
                    return res
                if call_tool is None:
                    return res
                # Run the lookups together and return every result in ONE user message.
                outs = await asyncio.gather(*(call_tool(b.name, b.input) for b in calls))
                messages.append({"role": "assistant", "content": msg.content})
                messages.append({"role": "user", "content": [
                    {"type": "tool_result", "tool_use_id": b.id, "content": text, **({} if ok else {"is_error": True})}
                    for b, (text, ok) in zip(calls, outs)]})
            res.stop, res.error = "cut", f"Stopped after {MAX_STEPS} rounds of lookups."
            return res
        except anthropic.AuthenticationError:
            res.stop, res.error = "error", "Your Anthropic API key was rejected. Check ANTHROPIC_API_KEY in .env."
        except anthropic.RateLimitError:
            res.stop, res.error = "error", "The Anthropic API is rate limiting this key. Try again in a moment."
        except anthropic.APIStatusError as e:
            res.stop, res.error = "error", f"Anthropic API error {e.status_code}: {getattr(e, 'message', e)}"
        except anthropic.APIConnectionError:
            res.stop, res.error = "error", "Couldn't reach the Anthropic API. Check your connection."
        except ValueError as e:
            res.stop, res.error = "error", f"Claude sent a lookup request that couldn't be read ({e})."
        return res

    @staticmethod
    def _web_cards(msg, emit) -> None:
        """Show web searches (run on Anthropic's side) as cards, like any other lookup."""
        queries = {}
        for b in msg.content:
            if b.type == "server_tool_use" and getattr(b, "name", "") == "web_search":
                queries[b.id] = str((getattr(b, "input", None) or {}).get("query") or "")
            elif b.type == "web_search_tool_result":
                cid = "w" + str(getattr(b, "tool_use_id", ""))[-8:]
                emit({"type": "tool", "id": cid, "state": "start", "name": "web_search", "title": "Web search",
                      "group": "web", "args": {"query": queries.get(getattr(b, "tool_use_id", ""), "")}})
                content = getattr(b, "content", None)
                if isinstance(content, list):       # success: a list of results
                    rows = [[getattr(r, "title", ""), getattr(r, "url", "")] for r in content]
                    emit({"type": "tool", "id": cid, "state": "done", "ok": True, "untrusted": True,
                          "data": {"columns": ["title", "url"], "rows": rows}})
                else:                               # failure: one error object, not an exception
                    emit({"type": "tool", "id": cid, "state": "done", "ok": False, "untrusted": True,
                          "error": str(getattr(content, "error_code", "") or "web search failed")})

    async def quick(self, system: str, prompt: str, model: str = "claude-haiku-5-5") -> RunResult:
        """A plain question with no tools, for the conversation summaries."""
        job = Job(system=system, model=model, effort="low",
                  messages=[{"role": "user", "content": [{"type": "text", "text": prompt}]}])
        return await self.run(job, lambda ev: None, tools=None)
