from datetime import datetime, timezone

import pytest

# The extraction tests use articles published on 2026-09-18. The ingestion gate
# compares incident times with the clock, so the clock of the extraction modules
# is fixed; otherwise those tests would start failing once the articles are older
# than the hard cap of their kind.
FROZEN_NOW = datetime(2026, 9, 19, 12, 0, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def _frozen_extraction_clock(monkeypatch):
    from backend import llm, located

    monkeypatch.setattr(located, "utcnow", lambda: FROZEN_NOW)
    monkeypatch.setattr(llm, "utcnow", lambda: FROZEN_NOW)
