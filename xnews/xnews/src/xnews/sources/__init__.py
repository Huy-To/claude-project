from .base import Source, SourceError, RateLimited
from .x_api import XApiSource
from .twitterapi_io import TwitterApiIoSource
from .rss import RssSource

__all__ = [
    "Source",
    "SourceError",
    "RateLimited",
    "XApiSource",
    "TwitterApiIoSource",
    "RssSource",
    "build_source",
]


def build_source(name: str, settings: dict) -> Source:
    """Create a source from its config name and settings table."""
    if name == "x_api":
        return XApiSource(bearer_token=settings["bearer_token"])
    if name == "twitterapi_io":
        return TwitterApiIoSource(api_key=settings["api_key"])
    if name == "rss":
        return RssSource(url_template=settings["url_template"])
    raise ValueError(f"Unknown source {name!r}. Use x_api, twitterapi_io or rss.")
