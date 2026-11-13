"""twitterapi.io: an unofficial third-party X data provider (no X developer account needed).

Not affiliated with X. Check their current docs and pricing at twitterapi.io; the
response shape below follows their "user last tweets" endpoint and is parsed
defensively in case fields move.
"""

from __future__ import annotations

from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

import httpx

from ..models import Post
from .base import Source, SourceError

API = "https://api.twitterapi.io/twitter/user/last_tweets"


def _parse_time(value: str) -> datetime:
    # Seen as Twitter's classic format ("Tue Nov 04 12:00:00 +0000 2026") or ISO 8601.
    try:
        return datetime.strptime(value, "%a %b %d %H:%M:%S %z %Y")
    except (ValueError, TypeError):
        pass
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        pass
    try:
        return parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return datetime.now(timezone.utc)


class TwitterApiIoSource(Source):
    name = "twitterapi_io"

    def __init__(self, api_key: str, client: httpx.AsyncClient | None = None):
        if not api_key:
            raise SourceError("twitterapi_io needs an api_key (set TWITTERAPI_IO_KEY).")
        super().__init__(client)
        self._headers = {"X-API-Key": api_key}

    async def fetch_latest(self, account: str, since_id: str | None, limit: int) -> list[Post]:
        resp = await self._client.get(
            API, params={"userName": account, "includeReplies": "false"}, headers=self._headers
        )
        self._raise_for_status(resp)
        body = resp.json()
        if body.get("status") == "error":
            raise SourceError(f"twitterapi.io: {body.get('msg') or body}")
        tweets = body.get("tweets")
        if tweets is None:
            tweets = (body.get("data") or {}).get("tweets", [])

        posts = [self._to_post(account, t) for t in tweets if t.get("id")]
        posts.sort(key=lambda p: int(p.id), reverse=True)
        if since_id:
            posts = [p for p in posts if int(p.id) > int(since_id)]
            return posts
        return posts[:limit]

    @staticmethod
    def _to_post(account: str, t: dict) -> Post:
        entities = t.get("entities") or {}
        urls = [u.get("expanded_url") or u.get("url") for u in entities.get("urls", [])]
        return Post(
            id=str(t["id"]),
            account=account,
            text=t.get("text", ""),
            created_at=_parse_time(t.get("createdAt", "")),
            url=t.get("url") or f"https://x.com/{account}/status/{t['id']}",
            source="twitterapi_io",
            lang=t.get("lang"),
            is_repost=bool(t.get("retweeted_tweet")),
            is_quote=bool(t.get("quoted_tweet")),
            metrics={
                k: int(t[k])
                for k in ("likeCount", "retweetCount", "replyCount", "quoteCount", "viewCount")
                if isinstance(t.get(k), (int, float))
            },
            links=[u for u in urls if u],
        )
