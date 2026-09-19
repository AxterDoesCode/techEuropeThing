from __future__ import annotations

from typing import Any, Protocol

from ..models import Event, RawItem


class StructuredSource(Protocol):
    """A feed whose items map to at most one event each, identified upstream.

    `snapshot` is True when every fetch returns the complete set of currently
    active items. Events from such a source are marked ended once they are no
    longer in the fetch result. An empty fetch result ends nothing unless the
    source also sets `empty_is_valid = True`.
    """

    id: str
    snapshot: bool

    def fetch(self, cursor: dict[str, Any]) -> tuple[list[RawItem], dict[str, Any]]:
        """Return the fetched items and the cursor to store for the next poll."""
        ...

    def to_event(self, raw: RawItem) -> Event | None:
        """Map one item to an event. None when the item should not produce one."""
        ...


def external_ref(raw: RawItem) -> str:
    return f"{raw.source_id}:{raw.external_id}"
