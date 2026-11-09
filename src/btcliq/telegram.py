"""Monitor public Telegram channels for news.

Uses Telegram's public web preview (https://t.me/s/<channel>), which shows a channel's
recent posts without logging in. Works for public channels only; private channels
or groups would need a logged-in Telegram client.

Posts are kept in SQLite (data/news.db) so history survives restarts, and the channel
list lives in data/telegram.json so it can be edited from the browser.
"""

from __future__ import annotations

import asyncio
import json
import logging
import random
import re
import sqlite3
import time
from datetime import datetime
from html import unescape
from html.parser import HTMLParser
from pathlib import Path

import aiohttp

log = logging.getLogger("btcliq")
CHANNEL_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]{3,31}$")


def normalize_channel(raw: str) -> str | None:
    """Accept 'name', '@name', 't.me/name' or 'https://t.me/s/name'; return 'name' or None."""
    s = raw.strip()
    s = re.sub(r"^(https?://)?(www\.)?(t|telegram)\.me/(s/)?", "", s)
    s = s.lstrip("@").split("/")[0].split("?")[0]
    return s if CHANNEL_RE.match(s) else None


class _PageParser(HTMLParser):
    """Pulls posts out of a t.me/s/<channel> page."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.posts: list[dict] = []
        self.title = ""
        self.cur: dict | None = None
        self._text_depth = 0      # >0 while inside the message text div
        self._div_depth = 0
        self._text_start = 0
        self._in_views = False
        self._in_fwd = False
        self._in_title = False
        self._link: str | None = None

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        cls = a.get("class", "")
        if tag == "meta" and a.get("property") == "og:title":
            self.title = a.get("content", "")
        if tag == "div":
            self._div_depth += 1
            if "tgme_widget_message " in cls + " " and a.get("data-post"):
                self.cur = {"post": a["data-post"], "text": "", "links": [], "photo": None,
                            "time": None, "views": None, "forwarded": None}
                self.posts.append(self.cur)
            elif self.cur is not None and "tgme_widget_message_text" in cls and not self._text_depth:
                self._text_depth = 1
                self._text_start = self._div_depth
        if self.cur is None:
            return
        if self._text_depth:
            if tag == "br":
                self.cur["text"] += "\n"
            elif tag == "a" and a.get("href"):
                self._link = a["href"]
        if tag == "time" and a.get("datetime") and not self.cur["time"]:
            self.cur["time"] = a["datetime"]
        if tag == "span" and "tgme_widget_message_views" in cls:
            self._in_views = True
        if "tgme_widget_message_forwarded_from_name" in cls:
            self._in_fwd = True
            self.cur["forwarded"] = ""
        if tag == "a" and "tgme_widget_message_photo_wrap" in cls and not self.cur["photo"]:
            m = re.search(r"url\('([^']+)'\)", a.get("style", ""))
            if m:
                self.cur["photo"] = m.group(1)

    def handle_startendtag(self, tag, attrs):
        if tag == "br" and self.cur is not None and self._text_depth:
            self.cur["text"] += "\n"
        else:
            self.handle_starttag(tag, attrs)
            if tag == "div":
                self._div_depth -= 1

    def handle_endtag(self, tag):
        if tag == "div":
            if self._text_depth and self._div_depth == self._text_start:
                self._text_depth = 0
            self._div_depth -= 1
        elif tag == "span":
            self._in_views = False
        elif tag == "a":
            if self._link and self.cur is not None:
                self.cur["links"].append(self._link)
            self._link = None
            self._in_fwd = False

    def handle_data(self, data):
        if self.cur is None:
            return
        if self._text_depth:
            self.cur["text"] += data
        elif self._in_views:
            self.cur["views"] = data.strip()
        elif self._in_fwd:
            self.cur["forwarded"] += data


def parse_page(html: str, channel: str) -> tuple[str, list[dict]]:
    p = _PageParser()
    p.feed(html)
    posts = []
    for raw in p.posts:
        ch, _, pid = raw["post"].partition("/")
        if not pid.isdigit():
            continue
        try:
            ts = int(datetime.fromisoformat(raw["time"]).timestamp() * 1000) if raw["time"] else None
        except ValueError:
            ts = None
        text = unescape(re.sub(r"\n{3,}", "\n\n", raw["text"])).strip()
        posts.append({
            "id": f"{ch.lower()}/{pid}",
            "channel": ch,
            "num": int(pid),
            "time": ts or int(time.time() * 1000),
            "text": text,
            "url": f"https://t.me/{ch}/{pid}",
            "photo": raw["photo"],
            "views": raw["views"],
            "forwarded": (raw["forwarded"] or "").strip() or None,
            "links": [l for l in raw["links"] if not l.startswith("https://t.me/" + ch)][:5],
        })
    return p.title, posts


class TelegramMonitor:
    def __init__(self, session: aiohttp.ClientSession, data_dir: Path, poll_seconds: int = 30,
                 extra_channels: list[str] | None = None):
        self.s = session
        self.poll_seconds = poll_seconds
        data_dir.mkdir(parents=True, exist_ok=True)
        self.cfg_path = data_dir / "telegram.json"
        self.db = sqlite3.connect(data_dir / "news.db")
        self.db.execute("""CREATE TABLE IF NOT EXISTS posts (
            id TEXT PRIMARY KEY, channel TEXT, num INTEGER, time INTEGER, data TEXT)""")
        self.db.execute("CREATE INDEX IF NOT EXISTS posts_time ON posts(time)")
        self.channels: dict[str, dict] = {}  # lower name -> {name, title, status, error, last}
        for name in self._load_cfg() + [c for c in (extra_channels or [])]:
            n = normalize_channel(name)
            if n and n.lower() not in self.channels:
                self.channels[n.lower()] = {"name": n, "title": "", "error": "", "last": 0}
        self._save_cfg()
        self.on_new = None     # async callback(list[post], initial=bool)
        self.on_status = None  # async callback() when a channel's status changes
        self._tasks: dict[str, asyncio.Task] = {}

    # ---------- config ----------
    def _load_cfg(self) -> list[str]:
        try:
            return json.loads(self.cfg_path.read_text()).get("channels", [])
        except (FileNotFoundError, ValueError):
            return []

    def _save_cfg(self) -> None:
        self.cfg_path.write_text(json.dumps(
            {"channels": [c["name"] for c in self.channels.values()]}, indent=2) + "\n")

    def channel_list(self) -> list[dict]:
        return [{"name": c["name"], "title": c["title"], "error": c["error"], "last": c["last"]}
                for c in self.channels.values()]

    # ---------- storage ----------
    def recent(self, limit: int = 200, since_ms: int = 0) -> list[dict]:
        names = list(self.channels)
        if not names:
            return []
        marks = ",".join("?" * len(names))
        rows = self.db.execute(
            f"SELECT data FROM posts WHERE channel IN ({marks}) AND time >= ? ORDER BY time DESC LIMIT ?",
            (*names, since_ms, limit))
        return [json.loads(r[0]) for r in rows]

    def _store(self, posts: list[dict]) -> list[dict]:
        new = []
        with self.db:
            for p in posts:
                cur = self.db.execute("INSERT OR IGNORE INTO posts VALUES (?, ?, ?, ?, ?)",
                                      (p["id"], p["channel"].lower(), p["num"], p["time"], json.dumps(p)))
                if cur.rowcount:
                    new.append(p)
        return new

    # ---------- fetching ----------
    async def fetch(self, name: str) -> tuple[str, list[dict]]:
        async with self.s.get(f"https://t.me/s/{name}", headers={"Accept-Language": "en"},
                              allow_redirects=True) as r:
            r.raise_for_status()
            html = await r.text()
        title, posts = parse_page(html, name)
        if not posts and "tgme_channel_info" not in html:
            raise ValueError(f"Couldn't find a public channel called {name}. Private channels and groups aren't supported.")
        return title, posts

    async def _poll(self, key: str) -> None:
        await asyncio.sleep(random.uniform(0, 3))
        first = True
        while key in self.channels:
            ch = self.channels[key]
            before = (ch["error"], bool(ch["last"]))
            try:
                title, posts = await self.fetch(ch["name"])
                ch["title"], ch["error"], ch["last"] = title, "", int(time.time() * 1000)
                new = self._store(posts)
                if new and self.on_new:
                    new.sort(key=lambda p: (p["time"], p["num"]))
                    # On the first fetch, posts are history: send them but don't alert.
                    await self.on_new(new, initial=first)
            except Exception as e:
                ch["error"] = f"{type(e).__name__}: {e}"[:200]
                log.warning("Telegram @%s: %s", ch["name"], ch["error"])
            if self.on_status and (ch["error"], bool(ch["last"])) != before and key in self.channels:
                await self.on_status()
            first = False
            await asyncio.sleep(self.poll_seconds + random.uniform(0, 5))

    def start(self) -> None:
        for key in self.channels:
            if key not in self._tasks:
                self._tasks[key] = asyncio.create_task(self._poll(key))

    async def add(self, raw: str) -> dict:
        name = normalize_channel(raw)
        if not name:
            raise ValueError("Enter a public channel name like WatcherGuru or a t.me link")
        key = name.lower()
        if key in self.channels:
            return self.channels[key]
        title, _ = await self.fetch(name)  # validate before saving
        self.channels[key] = {"name": name, "title": title, "error": "", "last": 0}
        self._save_cfg()
        self._tasks[key] = asyncio.create_task(self._poll(key))
        return self.channels[key]

    def remove(self, raw: str) -> None:
        name = normalize_channel(raw) or raw
        key = name.lower()
        self.channels.pop(key, None)
        t = self._tasks.pop(key, None)
        if t:
            t.cancel()
        self._save_cfg()

    async def stop(self) -> None:
        for t in self._tasks.values():
            t.cancel()
        self.db.close()
