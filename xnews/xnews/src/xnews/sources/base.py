from __future__ import annotations

from abc import ABC, abstractmethod

import httpx

from ..models import Post


class SourceError(Exception):
    """A source failed in a way that retrying right away won't fix."""


class RateLimited(SourceError):
    def __init__(self, retry_after: float, message: str = "rate limited"):
        super().__init__(message)
        self.retry_after = retry_after


class Source(ABC):
    """A place to read posts from. Swap implementations without touching the monitor."""

    name: str = "base"

    def __init__(self, client: httpx.AsyncClient | None = None):
        self._client = client or httpx.AsyncClient(
            timeout=httpx.Timeout(20.0),
            headers={"User-Agent": "xnews/0.1"},
            follow_redirects=True,
        )

    @abstractmethod
    async def fetch_latest(
        self, account: str, since_id: str | None, limit: int
    ) -> list[Post]:
        """Return posts newer than since_id (or the newest `limit` posts), newest first."""

    async def aclose(self) -> None:
        await self._client.aclose()

    @staticmethod
    def _raise_for_status(resp: httpx.Response) -> None:
        if resp.status_code == 429:
            reset = resp.headers.get("x-rate-limit-reset")
            retry = resp.headers.get("retry-after")
            wait = 60.0
            if retry and retry.isdigit():
                wait = float(retry)
            elif reset and reset.isdigit():
                import time

                wait = max(1.0, float(reset) - time.time())
            raise RateLimited(wait)
        if resp.status_code in (401, 403):
            raise SourceError(
                f"{resp.status_code} from {resp.url.host}: check your key/token and plan. "
                f"Body: {resp.text[:300]}"
            )
        if resp.status_code >= 400:
            raise SourceError(f"HTTP {resp.status_code} from {resp.url}: {resp.text[:300]}")
