from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime


@dataclass(slots=True)
class Post:
    """One post, normalized so every source looks the same to the rest of the app."""

    id: str
    account: str  # handle without "@"
    text: str
    created_at: datetime
    url: str
    source: str  # which backend fetched it: "x_api", "twitterapi_io", "rss"
    lang: str | None = None
    is_repost: bool = False
    is_quote: bool = False
    metrics: dict[str, int] = field(default_factory=dict)
    links: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["created_at"] = self.created_at.isoformat()
        return d
