"""RSS/Atom feeds, for example from a self-hosted RSSHub or Nitter instance.

Free, but only as reliable as the bridge behind it. `url_template` gets the handle
filled in, e.g. "http://localhost:1200/twitter/user/{account}".
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from html import unescape

import httpx

from ..models import Post
from .base import Source, SourceError

ATOM = "{http://www.w3.org/2005/Atom}"
_TAG = re.compile(r"<[^>]+>")
_STATUS_ID = re.compile(r"/status(?:es)?/(\d+)")


def _clean(html: str) -> str:
    text = re.sub(r"<br\s*/?>", "\n", html or "", flags=re.I)
    return unescape(_TAG.sub("", text)).strip()


def _time(value: str | None) -> datetime:
    if not value:
        return datetime.now(timezone.utc)
    try:
        return parsedate_to_datetime(value)
    except (TypeError, ValueError):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return datetime.now(timezone.utc)


class RssSource(Source):
    name = "rss"

    def __init__(self, url_template: str, client: httpx.AsyncClient | None = None):
        if "{account}" not in url_template:
            raise SourceError("rss url_template must contain {account}.")
        super().__init__(client)
        self._template = url_template

    async def fetch_latest(self, account: str, since_id: str | None, limit: int) -> list[Post]:
        resp = await self._client.get(self._template.format(account=account))
        self._raise_for_status(resp)
        try:
            root = ET.fromstring(resp.content)
        except ET.ParseError as e:
            raise SourceError(f"Feed for @{account} is not valid XML: {e}") from e

        posts: list[Post] = []
        for item in root.iter("item"):  # RSS 2.0
            posts.append(self._post(
                account,
                link=item.findtext("link", ""),
                guid=item.findtext("guid", ""),
                body=item.findtext("description") or item.findtext("title", ""),
                when=item.findtext("pubDate"),
            ))
        for entry in root.iter(f"{ATOM}entry"):  # Atom
            link_el = entry.find(f"{ATOM}link")
            posts.append(self._post(
                account,
                link=link_el.get("href", "") if link_el is not None else "",
                guid=entry.findtext(f"{ATOM}id", ""),
                body=entry.findtext(f"{ATOM}content") or entry.findtext(f"{ATOM}title", ""),
                when=entry.findtext(f"{ATOM}published") or entry.findtext(f"{ATOM}updated"),
            ))

        posts = [p for p in posts if p.id.isdigit()]
        posts.sort(key=lambda p: int(p.id), reverse=True)
        if since_id:
            return [p for p in posts if int(p.id) > int(since_id)]
        return posts[:limit]

    @staticmethod
    def _post(account: str, link: str, guid: str, body: str, when: str | None) -> Post:
        m = _STATUS_ID.search(link) or _STATUS_ID.search(guid)
        pid = m.group(1) if m else ""
        text = _clean(body)
        return Post(
            id=pid,
            account=account,
            text=text,
            created_at=_time(when),
            url=f"https://x.com/{account}/status/{pid}" if pid else link,
            source="rss",
            is_repost=text.startswith("RT "),
        )
