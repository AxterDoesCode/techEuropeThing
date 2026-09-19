"""Metropolitan Police newsroom RSS (official statements and appeals)."""

from __future__ import annotations

from ..located import HALF_LIFE_MIN  # noqa: F401  (kept as met_news.HALF_LIFE_MIN)
from .rss import RssNewsSource

URL = "https://news.met.police.uk/rss/current_news/66871"


class MetNewsSource(RssNewsSource):
    def __init__(self) -> None:
        super().__init__("met_news", URL, "official_statement")
