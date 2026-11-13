import asyncio
import json

import httpx
import pytest

from xnews.config import Config
from xnews.monitor import Monitor
from xnews.sources import RateLimited, RssSource, TwitterApiIoSource, XApiSource
from xnews.store import Store


def client(handler):
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def run(coro):
    return asyncio.run(coro)


# ---------- official X API ----------

def x_handler(calls):
    def handler(req: httpx.Request) -> httpx.Response:
        calls.append(req)
        assert req.headers["authorization"] == "Bearer tok"
        if req.url.path == "/2/users/by/username/MarioNawfal":
            return httpx.Response(200, json={"data": {"id": "42", "username": "MarioNawfal"}})
        if req.url.path == "/2/users/42/tweets":
            since = req.url.params.get("since_id")
            tweets = [
                {"id": "300", "text": "short...", "created_at": "2026-11-09T10:00:00.000Z",
                 "note_tweet": {"text": "BREAKING: full long text"},
                 "public_metrics": {"like_count": 5},
                 "entities": {"urls": [{"url": "https://t.co/x", "expanded_url": "https://news.example/a"}]}},
                {"id": "200", "text": "RT @someone: hi", "created_at": "2026-11-09T09:00:00.000Z",
                 "referenced_tweets": [{"type": "retweeted", "id": "1"}]},
            ]
            if since:
                tweets = [t for t in tweets if int(t["id"]) > int(since)]
            return httpx.Response(200, json={"data": tweets} if tweets else {"meta": {"result_count": 0}})
        return httpx.Response(404)
    return handler


def test_x_api_parses_and_caches_user_id():
    calls = []
    src = XApiSource("tok", client=client(x_handler(calls)))
    posts = run(src.fetch_latest("MarioNawfal", None, 5))
    assert [p.id for p in posts] == ["300", "200"]
    assert posts[0].text == "BREAKING: full long text"  # long post text used
    assert posts[0].links == ["https://news.example/a"]
    assert posts[1].is_repost
    run(src.fetch_latest("MarioNawfal", "300", 5))
    assert sum(c.url.path.startswith("/2/users/by/") for c in calls) == 1
    last = calls[-1]
    assert last.url.params["since_id"] == "300"
    assert last.url.params["max_results"] == "5"


def test_rate_limit_raises():
    def handler(req):
        return httpx.Response(429, headers={"retry-after": "30"})
    src = XApiSource("tok", client=client(handler))
    with pytest.raises(RateLimited) as e:
        run(src.fetch_latest("a", None, 5))
    assert e.value.retry_after == 30


# ---------- twitterapi.io ----------

def test_twitterapi_io_parses_both_shapes():
    tweets = [
        {"id": "11", "text": "old", "createdAt": "Mon Nov 09 08:00:00 +0000 2026"},
        {"id": "12", "text": "new", "createdAt": "Mon Nov 09 09:00:00 +0000 2026", "likeCount": 3,
         "url": "https://x.com/MarioNawfal/status/12"},
    ]
    for body in ({"status": "success", "tweets": tweets}, {"status": "success", "data": {"tweets": tweets}}):
        def handler(req, body=body):
            assert req.headers["x-api-key"] == "k"
            assert req.url.params["userName"] == "MarioNawfal"
            return httpx.Response(200, json=body)
        src = TwitterApiIoSource("k", client=client(handler))
        posts = run(src.fetch_latest("MarioNawfal", None, 5))
        assert [p.id for p in posts] == ["12", "11"]
        assert posts[0].metrics == {"likeCount": 3}
        assert posts[0].created_at.hour == 9
        assert [p.id for p in run(src.fetch_latest("MarioNawfal", "11", 5))] == ["12"]


# ---------- RSS ----------

RSS = """<?xml version="1.0"?><rss><channel>
<item><title>t</title><description>&lt;p&gt;Hello &amp;amp; world&lt;br&gt;line2&lt;/p&gt;</description>
<link>https://nitter.example/MarioNawfal/status/555#m</link><pubDate>Mon, 09 Nov 2026 10:00:00 GMT</pubDate></item>
<item><title>RT old</title><link>https://x.com/MarioNawfal/status/444</link></item>
</channel></rss>"""


def test_rss_parses():
    src = RssSource("https://bridge/{account}", client=client(lambda r: httpx.Response(200, text=RSS)))
    posts = run(src.fetch_latest("MarioNawfal", None, 5))
    assert [p.id for p in posts] == ["555", "444"]
    assert posts[0].text == "Hello & world\nline2"
    assert posts[0].url == "https://x.com/MarioNawfal/status/555"
    assert posts[1].is_repost


# ---------- monitor + store ----------

def test_monitor_only_reports_new_posts(tmp_path):
    calls = []
    src = XApiSource("tok", client=client(x_handler(calls)))
    cfg = Config(accounts=["MarioNawfal"], source="x_api", source_settings={},
                 db_path=str(tmp_path / "db.sqlite"), include_reposts=False)
    store = Store(cfg.db_path)
    seen = []
    mon = Monitor(cfg, src, store, [seen.append])
    assert run(mon.poll_once()) == 1  # repost filtered out
    assert seen[0].id == "300"
    assert store.since_id("MarioNawfal") == "300"
    assert run(mon.poll_once()) == 0  # nothing newer
    assert json.loads(json.dumps(store.recent()))[0]["id"] == "300"
