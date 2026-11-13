"""Polling loop: checks each account, stores new posts, and hands them to callbacks."""

from __future__ import annotations

import asyncio
import json
import logging
import random
from pathlib import Path
from typing import Awaitable, Callable

from .config import Config
from .models import Post
from .sources import RateLimited, Source, SourceError
from .store import Store

log = logging.getLogger("xnews")
OnPost = Callable[[Post], Awaitable[None] | None]


class Monitor:
    def __init__(self, cfg: Config, source: Source, store: Store, on_post: list[OnPost]):
        self.cfg = cfg
        self.source = source
        self.store = store
        self.on_post = on_post
        self._backoff: dict[str, float] = {}

    async def poll_account(self, account: str) -> list[Post]:
        since = self.store.since_id(account)
        posts = await self.source.fetch_latest(account, since, self.cfg.first_run_limit)
        # Save everything (so the cursor always advances and we never pay twice),
        # then filter what gets reported.
        new = self.store.save(posts)
        if not self.cfg.include_reposts:
            new = [p for p in new if not p.is_repost]
        new.sort(key=lambda p: int(p.id))  # oldest first, so output reads in order
        for p in new:
            for cb in self.on_post:
                result = cb(p)
                if asyncio.iscoroutine(result):
                    await result
        return new

    async def poll_once(self) -> int:
        """Poll every account once. Returns how many new posts arrived."""
        total = 0
        for account in self.cfg.accounts:
            try:
                total += len(await self.poll_account(account))
                self._backoff.pop(account, None)
            except RateLimited as e:
                log.warning("@%s rate limited; waiting %.0fs", account, e.retry_after)
                await asyncio.sleep(min(e.retry_after, 900))
            except SourceError as e:
                log.error("@%s: %s", account, e)
            except Exception as e:  # network blips, bad JSON, etc.
                wait = min(self._backoff.get(account, 5) * 2, 600)
                self._backoff[account] = wait
                log.warning("@%s: %s: %s (next try in up to %.0fs)", account, type(e).__name__, e, wait)
        return total

    async def run_forever(self) -> None:
        log.info(
            "Watching %s via %s, every %ss",
            ", ".join("@" + a for a in self.cfg.accounts), self.source.name, self.cfg.poll_seconds,
        )
        while True:
            await self.poll_once()
            # A little jitter so requests don't land on a fixed rhythm.
            await asyncio.sleep(self.cfg.poll_seconds + random.uniform(0, 10))


def jsonl_writer(path: str) -> OnPost:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)

    def write(post: Post) -> None:
        with p.open("a", encoding="utf-8") as f:
            f.write(json.dumps(post.to_dict(), ensure_ascii=False) + "\n")

    return write


def console_printer(keywords: list[str]) -> OnPost:
    bold, yellow, dim, reset = "\033[1m", "\033[93m", "\033[2m", "\033[0m"

    def show(post: Post) -> None:
        hit = [k for k in keywords if k in post.text.lower()]
        tag = f" {yellow}[{', '.join(hit)}]{reset}" if hit else ""
        kind = " (repost)" if post.is_repost else " (quote)" if post.is_quote else ""
        when = post.created_at.strftime("%Y-%m-%d %H:%M UTC")
        print(f"\n{bold}@{post.account}{reset}{kind} {dim}{when}{reset}{tag}")
        print(post.text)
        print(f"{dim}{post.url}{reset}", flush=True)

    return show
