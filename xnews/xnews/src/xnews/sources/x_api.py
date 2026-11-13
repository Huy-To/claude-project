"""Official X API v2 (pay-per-use). Needs a bearer token from console.x.com.

Billing is per resource returned, so this source:
- looks up each account's user id once and caches it (user reads cost extra),
- always passes since_id after the first run, so you only pay for new posts,
- asks for a small batch on the first run instead of the whole timeline.
"""

from __future__ import annotations

from datetime import datetime

import httpx

from ..models import Post
from .base import Source, SourceError

API = "https://api.x.com/2"
TWEET_FIELDS = "created_at,lang,public_metrics,entities,referenced_tweets,note_tweet"


class XApiSource(Source):
    name = "x_api"

    def __init__(self, bearer_token: str, client: httpx.AsyncClient | None = None):
        if not bearer_token:
            raise SourceError("x_api needs a bearer_token (set X_BEARER_TOKEN).")
        super().__init__(client)
        self._auth = {"Authorization": f"Bearer {bearer_token}"}
        self._user_ids: dict[str, str] = {}

    async def _user_id(self, account: str) -> str:
        key = account.lower()
        if key not in self._user_ids:
            resp = await self._client.get(f"{API}/users/by/username/{account}", headers=self._auth)
            self._raise_for_status(resp)
            body = resp.json()
            if "data" not in body:
                raise SourceError(f"Account @{account} not found: {body.get('errors')}")
            self._user_ids[key] = body["data"]["id"]
        return self._user_ids[key]

    async def fetch_latest(self, account: str, since_id: str | None, limit: int) -> list[Post]:
        uid = await self._user_id(account)
        params: dict[str, str | int] = {
            "tweet.fields": TWEET_FIELDS,
            "exclude": "replies",
            # The API accepts 5..100.
            "max_results": max(5, min(100, limit)),
        }
        if since_id:
            params["since_id"] = since_id
        resp = await self._client.get(f"{API}/users/{uid}/tweets", params=params, headers=self._auth)
        self._raise_for_status(resp)
        data = resp.json().get("data", [])
        posts = [self._to_post(account, t) for t in data]
        return posts if since_id else posts[:limit]

    @staticmethod
    def _to_post(account: str, t: dict) -> Post:
        # Long posts (over 280 chars) keep their full text in note_tweet.
        text = (t.get("note_tweet") or {}).get("text") or t.get("text", "")
        refs = {r.get("type") for r in t.get("referenced_tweets", [])}
        urls = [
            u.get("expanded_url") or u.get("url")
            for u in (t.get("entities") or {}).get("urls", [])
        ]
        return Post(
            id=t["id"],
            account=account,
            text=text,
            created_at=datetime.fromisoformat(t["created_at"].replace("Z", "+00:00")),
            url=f"https://x.com/{account}/status/{t['id']}",
            source="x_api",
            lang=t.get("lang"),
            is_repost="retweeted" in refs,
            is_quote="quoted" in refs,
            metrics=t.get("public_metrics") or {},
            links=[u for u in urls if u],
        )
