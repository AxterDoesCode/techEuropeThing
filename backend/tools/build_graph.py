"""Build the compact walking graph used by backend/routing.py.

  python -m backend.tools.build_graph [--bbox w,s,e,n] [--out data/graph/walk.npz]
                                      [--overpass-url URL] [--download-limit-s 600]

Downloads the OpenStreetMap walking network with osmnx, keeps the largest
connected component and writes the .npz format described in backend/routing.py.
osmnx and networkx are needed only here, not at query time.
"""

from __future__ import annotations

import argparse
import json
import resource
import signal
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from ..routing import build_arrays, save_graph

INNER_LONDON = (-0.26, 51.45, 0.02, 51.57)
CENTRAL_LONDON = (-0.20, 51.48, -0.05, 51.545)
ATTRIBUTION = "© OpenStreetMap contributors (ODbL)"
DOWNLOAD_LIMIT_S = 600


class _DownloadTimeout(Exception):
    pass


def _download(bbox: tuple[float, float, float, float], limit_s: int):
    import osmnx as ox

    def on_alarm(signum, frame):
        raise _DownloadTimeout

    signal.signal(signal.SIGALRM, on_alarm)
    signal.alarm(limit_s)
    try:
        # osmnx 2.x: bbox = (left, bottom, right, top)
        return ox.graph_from_bbox(bbox, network_type="walk", simplify=True, retain_all=False)
    finally:
        signal.alarm(0)


def _is_lit(value) -> bool:
    """Simplified edges can carry a list of values from the merged OSM ways."""
    values = value if isinstance(value, list) else [value]
    return "yes" in values and "no" not in values


def build(
    bbox: tuple[float, float, float, float],
    out: Path,
    limit_s: int = DOWNLOAD_LIMIT_S,
    overpass_url: str | None = None,
) -> dict:
    import osmnx as ox

    ox.settings.use_cache = True
    ox.settings.cache_folder = str(out.parent / "cache")
    ox.settings.requests_timeout = 600
    ox.settings.log_console = False
    if overpass_url:
        ox.settings.overpass_url = overpass_url.rstrip("/")
        # the /status slot check is specific to overpass-api.de
        ox.settings.overpass_rate_limit = False
    if "lit" not in ox.settings.useful_tags_way:
        ox.settings.useful_tags_way = [*ox.settings.useful_tags_way, "lit"]

    started = time.monotonic()
    try:
        G = _download(bbox, limit_s)
    except _DownloadTimeout:
        print(f"download of {bbox} exceeded {limit_s} s; building central London {CENTRAL_LONDON} instead")
        bbox = CENTRAL_LONDON
        G = _download(bbox, limit_s)
    download_s = time.monotonic() - started

    # One record per street segment; to_undirected keeps parallel edges whose geometry differs
    # largest_component needs the directed graph osmnx returns
    U = ox.convert.to_undirected(ox.truncate.largest_component(G))

    index = {node: i for i, node in enumerate(U.nodes)}
    node_lng = [d["x"] for _, d in U.nodes(data=True)]
    node_lat = [d["y"] for _, d in U.nodes(data=True)]

    lit_tagged = 0

    def edges():
        nonlocal lit_tagged
        for u, v, d in U.edges(data=True):
            if u == v:
                continue
            start = (node_lng[index[u]], node_lat[index[u]])
            end = (node_lng[index[v]], node_lat[index[v]])
            if "geometry" in d:
                coords = [list(c) for c in d["geometry"].coords]
                # geometry direction is not guaranteed to match (u, v)
                if _sq(coords[0], start) > _sq(coords[-1], start):
                    coords.reverse()
                coords[0], coords[-1] = list(start), list(end)
            else:
                coords = [list(start), list(end)]
            if "lit" in d:
                lit_tagged += 1
            yield index[u], index[v], coords, float(d["length"]), _is_lit(d.get("lit"))

    meta = {
        "bbox": list(bbox),
        "built_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "attribution": ATTRIBUTION,
        "network_type": "walk",
    }
    arrays = build_arrays(node_lng, node_lat, edges(), meta)
    n_edges = len(arrays["edge_src"]) // 2
    meta |= {
        "nodes": len(node_lng),
        "undirected_edges": n_edges,
        "lit_tagged_edges": lit_tagged,
        "lit_yes_edges": int(arrays["edge_lit"][:n_edges].sum()),
    }
    arrays["meta"] = np.asarray(json.dumps(meta))
    save_graph(out, arrays)

    return meta | {
        "file": str(out.resolve()),
        "file_mb": round(out.stat().st_size / 1e6, 2),
        "download_s": round(download_s, 1),
        "build_s": round(time.monotonic() - started, 1),
        # ru_maxrss is in kilobytes on Linux
        "peak_rss_mb": round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024, 1),
    }


def _sq(a, b) -> float:
    return (a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--bbox", default=",".join(map(str, INNER_LONDON)), help="west,south,east,north")
    parser.add_argument("--out", default="data/graph/walk.npz")
    parser.add_argument("--download-limit-s", type=int, default=DOWNLOAD_LIMIT_S)
    parser.add_argument(
        "--overpass-url",
        help="Overpass API base URL without /interpreter, e.g. https://overpass.openstreetmap.fr/api"
        " (default: the osmnx default, overpass-api.de)",
    )
    args = parser.parse_args()
    bbox = tuple(float(x) for x in args.bbox.split(","))
    if len(bbox) != 4:
        parser.error("--bbox must be west,south,east,north")
    stats = build(bbox, Path(args.out), args.download_limit_s, args.overpass_url)
    for key, value in stats.items():
        print(f"{key}: {value}")
    tagged = stats["lit_tagged_edges"] / stats["undirected_edges"]
    print(f"lit tag coverage: {tagged:.1%} of edges have the tag")


if __name__ == "__main__":
    main()
