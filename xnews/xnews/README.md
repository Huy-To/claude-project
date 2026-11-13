# xnews

Watches X accounts (e.g. [@MarioNawfal](https://x.com/MarioNawfal)) for new posts, prints
them as they arrive, and stores them for later analysis.

## Setup

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
cp config.example.toml config.toml
cp .env.example .env      # then put your key in .env
```

## Run

```bash
xnews once          # check every account one time
xnews watch         # keep checking every poll_seconds
xnews recent MarioNawfal -n 10
```

New posts are printed to the terminal, saved in `data/xnews.db` (SQLite) and appended to
`data/posts.jsonl`. Posts matching `highlight_keywords` are tagged.

## Choosing a source

Set `[source].use` in `config.toml`:

| Source | Cost | Needs | Notes |
|---|---|---|---|
| `x_api` | Pay per post read (about $0.005/post in 2026; check console.x.com) | X developer account, bearer token | Official and the most reliable. |
| `twitterapi_io` | Paid; usually cheaper | Key from twitterapi.io | Unofficial third party. |
| `rss` | Free | A self-hosted RSSHub or Nitter bridge | Breaks whenever X changes things. |

Cost control: on its first run an account fetches only `first_run_limit` posts. After that
each poll asks only for posts newer than the last one seen, so you pay only for new posts.
Polling more often does not cost more on `x_api`, because you are billed per post returned,
not per request.

## Layout

```
src/xnews/
  sources/   one file per backend, all returning the same Post object
  store.py   SQLite: posts plus the last seen id per account
  monitor.py polling loop, console + JSONL output
  cli.py     xnews command
```

To add a backend, subclass `Source`, implement `fetch_latest`, and register it in
`sources/__init__.py`.
