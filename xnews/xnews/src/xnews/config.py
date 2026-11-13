"""Loads config.toml plus secrets from environment variables (or a .env file)."""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

SECRET_ENV = {
    "x_api": ("bearer_token", "X_BEARER_TOKEN"),
    "twitterapi_io": ("api_key", "TWITTERAPI_IO_KEY"),
}


@dataclass
class Config:
    accounts: list[str]
    source: str
    source_settings: dict
    poll_seconds: int = 300
    first_run_limit: int = 5
    include_reposts: bool = True
    db_path: str = "data/xnews.db"
    jsonl_path: str | None = "data/posts.jsonl"
    keywords: list[str] = field(default_factory=list)


def _load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def load_config(path: str | Path = "config.toml") -> Config:
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(
            f"{path} not found. Copy config.example.toml to config.toml and edit it."
        )
    _load_dotenv(path.parent / ".env")
    raw = tomllib.loads(path.read_text())

    source = raw.get("source", {}).get("use", "x_api")
    settings = dict(raw.get("source", {}).get(source, {}))
    if source in SECRET_ENV:
        key, env = SECRET_ENV[source]
        settings.setdefault(key, os.environ.get(env, ""))

    mon = raw.get("monitor", {})
    accounts = [a.lstrip("@").strip() for a in mon.get("accounts", []) if a.strip()]
    if not accounts:
        raise ValueError("Add at least one handle to [monitor].accounts in config.toml.")
    return Config(
        accounts=accounts,
        source=source,
        source_settings=settings,
        poll_seconds=int(mon.get("poll_seconds", 300)),
        first_run_limit=int(mon.get("first_run_limit", 5)),
        include_reposts=bool(mon.get("include_reposts", True)),
        db_path=mon.get("db_path", "data/xnews.db"),
        jsonl_path=mon.get("jsonl_path", "data/posts.jsonl") or None,
        keywords=[k.lower() for k in mon.get("highlight_keywords", [])],
    )
