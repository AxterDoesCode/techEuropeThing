"""Write API-shaped JSON files from live source data, without a database.

Lets the web client run before the backend is deployed:
  python -m backend.tools.export_sample web/public/sample
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from uuid import uuid4

from ..features import event_feature
from ..models import utcnow
from ..pipeline import SOURCES
from ..scoring import compute_cell_scores
from ..sources import mps_lsoa


def main(out_dir: str) -> None:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    now = utcnow()
    events = []
    for source in SOURCES.values():
        items, _ = source.fetch({})
        for item in items:
            if ev := source.to_event(item):
                ev.id = uuid4()
                events.append(ev)
    scores = compute_cell_scores(events, now)
    features = [event_feature(ev, now) for ev in events]
    (out / "events.json").write_text(
        json.dumps({"type": "FeatureCollection", "features": features})
    )
    for res in (7, 9):
        cells = [c.model_dump(mode="json") for c in scores if c.res == res]
        (out / f"cells_{res}.json").write_text(json.dumps(cells))
    print(f"{len(events)} events, {len(scores)} cells -> {out}")

    graph = mps_lsoa.load_graph_if_present(os.environ.get("GRAPH_PATH", "data/graph/walk.npz"))
    result = mps_lsoa.build(graph)
    (out / "crime_points.json").write_text(json.dumps(result.payload(), separators=(",", ":")))
    print(f"{result.period}, police.uk {result.month}: {result.stats}")


if __name__ == "__main__":
    main(sys.argv[1])
