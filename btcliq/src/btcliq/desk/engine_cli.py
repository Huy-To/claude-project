"""Answers through the Claude Code app on this PC, using your Claude login (no API bill).

One `claude -p` run per question. Claude Code gets:
- the system prompt and the question's context as text,
- one MCP server, btcliq's own lookup endpoint, reachable with a token that is valid only
  for this question,
- no built-in tools at all (no files, no shell), except web search when the switch is on.
It runs in an empty temporary folder, ignores your Claude Code settings and saves no session.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import secrets
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

log = logging.getLogger("btcliq")

MCP_NAME = "btcliq"
MCP_PREFIX = f"mcp__{MCP_NAME}__"
LIMIT_RE = re.compile(r"usage limit|rate.?limit|limit reached|hit your limit|out of (extra )?(usage|credits)|"
                      r"quota|too many requests", re.I)
LOGIN_RE = re.compile(r"log ?in|logged out|authenticat|unauthori[sz]ed|invalid api key|oauth", re.I)


@dataclass
class Job:
    system: str
    prompt: str = ""                       # Claude Code: the whole conversation as one text
    messages: list = field(default_factory=list)   # API: the same conversation as messages
    model: str = "claude-sonnet-5-5"
    effort: str | None = "medium"
    web: bool = False
    mcp_url: str = ""
    token: str = ""
    budget_usd: float | None = None        # API only: stop when this much has been spent


@dataclass
class RunResult:
    text: str = ""
    stop: str = "end"                      # end | limit | error | refusal | cut | cap
    error: str = ""
    usage: dict = field(default_factory=lambda: {"in": 0, "out": 0, "cache_read": 0, "cache_write": 0,
                                                 "web_searches": 0})
    limit: dict | None = None              # when stop == "limit": what Claude Code said about the plan window


def usage_from(u: dict) -> dict:
    """Claude's usage block -> our four counters."""
    u = u or {}
    return {"in": u.get("input_tokens") or 0, "out": u.get("output_tokens") or 0,
            "cache_read": u.get("cache_read_input_tokens") or 0,
            "cache_write": u.get("cache_creation_input_tokens") or 0,
            "web_searches": ((u.get("server_tool_use") or {}).get("web_search_requests") or 0)}


class CliEngine:
    name = "claude-code"
    label = "Claude Code"

    def __init__(self, claude_bin: str):
        self.bin = claude_bin
        # An empty folder for the CLI to run in, so it can't pick up any project files.
        self.workdir = tempfile.mkdtemp(prefix="btcliq-claude-")

    def command(self, job: Job, tools: bool = True, mcp_file: str | None = None) -> list[str]:
        cmd = [self.bin, "-p", "--output-format", "stream-json", "--verbose", "--include-partial-messages",
               "--model", job.model, "--system-prompt", job.system,
               "--setting-sources", "",          # ignore your settings, hooks and CLAUDE.md files
               "--no-session-persistence",       # don't save these chats as Claude Code sessions
               "--permission-prompts", "none"]   # anything not allowed below is refused, never asked
        if job.effort:
            cmd += ["--effort", job.effort]
        allowed = []
        if tools and job.mcp_url:
            cfg = {"mcpServers": {MCP_NAME: {"type": "http", "url": job.mcp_url,
                                             "headers": {"X-Btcliq-Desk": job.token}}}}
            if mcp_file:                  # a private file, so the token never shows in the process list
                Path(mcp_file).write_text(json.dumps(cfg))
                Path(mcp_file).chmod(0o600)
            cmd += ["--mcp-config", mcp_file or json.dumps(cfg), "--strict-mcp-config"]
            allowed.append(f"mcp__{MCP_NAME}")
        else:
            cmd += ["--strict-mcp-config"]
        # Built-in tools: none. With the web switch on, search only: no page fetching, so nothing
        # read on the web can make Claude send your data to an address of its choosing.
        cmd += ["--tools", "WebSearch" if (tools and job.web) else ""]
        if tools and job.web:
            allowed.append("WebSearch")
        if allowed:
            cmd += ["--allowedTools", ",".join(allowed)]
        return cmd

    @staticmethod
    def _env() -> dict:
        env = dict(os.environ)
        env.pop("ANTHROPIC_API_KEY", None)     # make sure it uses your Claude login, not API billing
        env.pop("ANTHROPIC_AUTH_TOKEN", None)
        return env

    async def run(self, job: Job, emit, on_plan=None) -> RunResult:
        """Run one question. `emit(event)` receives text and web-search events as they happen;
        lookup events come from the MCP endpoint, which Claude Code calls while this runs."""
        res = RunResult()
        mcp_file = os.path.join(self.workdir, f"lookups-{secrets.token_hex(6)}.json") if job.mcp_url else None
        proc = await asyncio.create_subprocess_exec(
            *self.command(job, mcp_file=mcp_file), cwd=self.workdir, env=self._env(), limit=32 * 1024 * 1024,
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        web_calls: dict[str, str] = {}
        delta_since_start = False
        try:
            proc.stdin.write(job.prompt.encode())
            await proc.stdin.drain()
            proc.stdin.close()
            async for raw in proc.stdout:
                try:
                    ev = json.loads(raw)
                except ValueError:
                    continue
                kind = ev.get("type")
                if kind == "stream_event":
                    e = ev.get("event") or {}
                    et = e.get("type")
                    if et == "message_start":
                        delta_since_start = False
                    elif et == "content_block_delta" and (e.get("delta") or {}).get("type") == "text_delta":
                        t = e["delta"].get("text") or ""
                        if t:
                            delta_since_start = True
                            res.text += t
                            emit({"type": "text", "text": t})
                elif kind == "assistant":
                    for b in (ev.get("message") or {}).get("content") or []:
                        if b.get("type") == "text" and not delta_since_start and b.get("text"):
                            res.text += b["text"]            # no partial messages came: use the whole block
                            emit({"type": "text", "text": b["text"]})
                        elif b.get("type") == "tool_use" and b.get("name") == "WebSearch":
                            q = str((b.get("input") or {}).get("query") or "")
                            web_calls[b.get("id") or ""] = q
                            emit({"type": "tool", "id": "w" + (b.get("id") or "")[-8:], "state": "start", "name": "web_search",
                                  "title": "Web search", "group": "web", "args": {"query": q}})
                elif kind == "user":
                    for b in (ev.get("message") or {}).get("content") or []:
                        if isinstance(b, dict) and b.get("type") == "tool_result" and b.get("tool_use_id") in web_calls:
                            c = b.get("content")
                            text = c if isinstance(c, str) else "\n".join(
                                x.get("text", "") for x in (c or []) if isinstance(x, dict))
                            emit({"type": "tool", "id": "w" + b["tool_use_id"][-8:], "state": "done",
                                  "ok": not b.get("is_error"), "untrusted": True, "data": text[:6000],
                                  "error": text[:300] if b.get("is_error") else ""})
                elif kind == "system" and ev.get("subtype") == "init":
                    bad = [s.get("name") for s in ev.get("mcp_servers") or [] if s.get("status") != "connected"]
                    if bad:
                        log.warning("Claude Code couldn't connect to the lookup endpoint (%s)", ", ".join(map(str, bad)))
                        emit({"type": "notice", "text": "Claude couldn't reach the app's lookups for this answer, "
                                                        "so it has only the snapshot."})
                elif kind == "rate_limit_event":
                    info = ev.get("rate_limit_info") or {}
                    if on_plan:
                        on_plan(info)
                    if info.get("status") not in (None, "allowed", "allowed_warning"):
                        res.limit = info
                elif kind == "result":
                    res.usage = usage_from(ev.get("usage") or {})
                    if ev.get("is_error"):
                        msg = str(ev.get("result") or ev.get("subtype") or "unknown error")
                        if ev.get("api_error_status") == 429 or LIMIT_RE.search(msg) or res.limit:
                            res.stop, res.error = "limit", msg
                        else:
                            res.stop, res.error = "error", msg
            await proc.wait()
            if res.stop == "end" and res.limit and not res.text.strip():
                res.stop, res.error = "limit", "Your Claude plan's usage limit has been reached."
            if proc.returncode and res.stop == "end" and not res.text.strip():
                err = (await proc.stderr.read()).decode(errors="replace").strip()[-600:]
                if LIMIT_RE.search(err):
                    res.stop, res.error = "limit", err
                else:
                    res.stop = "error"
                    res.error = f"Claude Code stopped (exit {proc.returncode}): {err or 'no details'}"
                    if LOGIN_RE.search(err):
                        res.error += " Run `claude` in a terminal and log in, then try again."
            return res
        finally:
            if proc.returncode is None:        # the browser pressed Stop or went away
                try:
                    proc.kill()
                except ProcessLookupError:
                    pass
                await proc.wait()
            if mcp_file:
                try:
                    os.unlink(mcp_file)
                except OSError:
                    pass

    async def quick(self, system: str, prompt: str, model: str = "claude-haiku-5-5") -> RunResult:
        """A plain question with no tools, for the conversation summaries."""
        return await self.run(Job(system=system, prompt=prompt, model=model, effort="low"), lambda ev: None)
