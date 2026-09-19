"""Build the compact walking graph used by backend/routing.py.

  python -m backend.tools.build_graph [--bbox w,s,e,n] [--out data/graph/walk.npz]
                                      [--overpass-url URL] [--download-limit-s 18000]
                                      [--tile-km 12]

Downloads the OpenStreetMap walking network with osmnx, keeps the largest
connected component and writes the .npz format 2 described in backend/routing.py.
osmnx, networkx and shapely are needed only here, not at query time.

The bbox is split into tiles of about --tile-km that are downloaded one by one
(unsimplified, so that ways cut by a tile border join again), composed, cut to
the bbox and simplified once. Overpass responses are cached next to the output
file, so a run that failed or was stopped continues from the tiles already
downloaded.
"""

from __future__ import annotations

import argparse
import json
import math
import re
import resource
import signal
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from ..routing import (
    CLASS_INDEX,
    CLASS_NAMES,
    FLAG_IN_PARK,
    FLAG_SIDEWALK,
    FLAG_UNDERPASS,
    LIT_NO,
    LIT_UNKNOWN,
    LIT_YES,
    _values,
    _xy,
    build_arrays,
    classify,
    infer_lit,
    save_graph,
)

# Bounding box of the Greater London boundary; the graph does not extend past it
GREATER_LONDON = (-0.5104, 51.2868, 0.3340, 51.6919)
INNER_LONDON = (-0.26, 51.45, 0.02, 51.57)
CENTRAL_LONDON = (-0.20, 51.48, -0.05, 51.545)
ATTRIBUTION = "© OpenStreetMap contributors (ODbL)"
DOWNLOAD_LIMIT_S = 5 * 3600
TILE_KM = 12.0
TILE_ATTEMPTS = 4

# Way tags kept on edges. highway, footway, service, tunnel, covered, indoor and
# lit are read by routing.classify / infer_lit; oneway and junction are read by
# osmnx itself; name (or ref, e.g. "A501", when there is no name) labels the
# steps of a route; the rest are kept for inspection of the intermediate graph.
WAY_TAGS = ["highway", "footway", "service", "tunnel", "covered", "indoor", "level", "lit", "foot",
            "access", "bridge", "oneway", "junction", "name", "ref"]

# Polygons that set FLAG_IN_PARK on the edges whose midpoint is inside them:
# open green space that is unlit and has few people in it after dark. Gardens
# (mostly private or small squares), cemeteries, golf courses and woods are not
# included; woods and commons are usually crossed on path/track, which the
# class multiplier and the lit rule already cover.
PARK_TAGS = {
    "leisure": ["park", "nature_reserve", "common"],
    "landuse": ["recreation_ground"],
}

# osmnx removes every road tagged sidewalk=separate from the walk network. In
# central London that is about 89% of primary road centrelines, so main roads
# would exist only as footway=sidewalk ways. These clauses are taken out of the
# osmnx walk filter; everything else it excludes (motorways, foot=no,
# access=private, service=private, cycleways, areas, ...) stays excluded.
_SIDEWALK_CLAUSE = re.compile(r'\["sidewalk(:[a-z]+)?"!~"separate"\]')


def walk_filter() -> str:
    """The installed osmnx walk filter without its sidewalk clauses. With osmnx
    2.1 the result is:
    ["highway"]["area"!~"yes"]["access"!~"private"]["highway"!~"abandoned|bus_guideway|
    construction|cycleway|motor|no|planned|platform|proposed|raceway|razed|rest_area|
    services"]["foot"!~"no"]["service"!~"private"]   (one line)"""
    from osmnx import _overpass

    original = _overpass._get_network_filter("walk")
    derived = _SIDEWALK_CLAUSE.sub("", original)
    if derived == original:
        print("warning: the osmnx walk filter has no sidewalk clauses; using it unchanged")
    if "sidewalk" in derived or '["highway"]' not in derived:
        raise RuntimeError(f"unexpected osmnx walk filter: {original}")
    return derived


def tiles(bbox: tuple[float, float, float, float], tile_km: float) -> list[tuple[float, float, float, float]]:
    """Grid of sub-boxes that covers `bbox` exactly, each at most about tile_km on a side."""
    w, s, e, n = bbox
    width_km = (e - w) * 111.32 * math.cos(math.radians((s + n) / 2))
    height_km = (n - s) * 111.32
    cols = max(1, math.ceil(width_km / tile_km))
    rows = max(1, math.ceil(height_km / tile_km))
    xs = np.linspace(w, e, cols + 1)
    ys = np.linspace(s, n, rows + 1)
    return [(float(xs[c]), float(ys[r]), float(xs[c + 1]), float(ys[r + 1]))
            for r in range(rows) for c in range(cols)]


class _DownloadTimeout(Exception):
    pass


def _rss_mb() -> float:
    # ru_maxrss is in kilobytes on Linux
    return round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024, 1)


def _with_retries(what: str, fetch):
    """Result of fetch(), or None when the area has no matching data."""
    from osmnx._errors import InsufficientResponseError

    for attempt in range(1, TILE_ATTEMPTS + 1):
        try:
            return fetch()
        except (InsufficientResponseError, ValueError) as exc:
            # osmnx raises these for an area without matching elements (open water, fields)
            print(f"  {what}: no data ({exc})")
            return None
        except _DownloadTimeout:
            raise
        except Exception as exc:
            if attempt == TILE_ATTEMPTS:
                raise
            wait = 30 * attempt
            print(f"  {what}: attempt {attempt} failed ({type(exc).__name__}: {exc}); retrying in {wait} s")
            time.sleep(wait)


def _download(bbox: tuple[float, float, float, float], tile_km: float, limit_s: int, pause_s: float, on_tile=None):
    """(unsimplified graph of the whole bbox, park polygons). `on_tile()` is called
    after every tile, e.g. to persist the response cache."""
    import osmnx as ox

    def on_alarm(signum, frame):
        raise _DownloadTimeout

    custom_filter = walk_filter()
    boxes = tiles(bbox, tile_km)
    G = None
    parks: dict = {}
    signal.signal(signal.SIGALRM, on_alarm)
    signal.alarm(limit_s)
    try:
        for i, box in enumerate(boxes, 1):
            started = time.monotonic()
            # osmnx 2.x: bbox = (left, bottom, right, top). network_type only makes
            # every way bidirectional here; the filter is custom_filter.
            # truncate_by_edge keeps the edges that cross the tile border, so the
            # neighbouring tile's copy of the same edge joins the two tiles.
            part = _with_retries(f"tile {i} streets", lambda: ox.graph_from_bbox(
                box, network_type="walk", custom_filter=custom_filter,
                simplify=False, retain_all=True, truncate_by_edge=True))
            if part is None:
                pass
            elif G is None:
                G = part
            else:
                # in place (nx.compose would copy the whole graph for every tile); an
                # edge present in both has the same (u, v, key) and is stored once
                G.update(edges=part.edges(keys=True, data=True), nodes=part.nodes(data=True))
            del part
            found = _with_retries(f"tile {i} parks", lambda: ox.features_from_bbox(box, PARK_TAGS))
            if found is not None:
                for key, geom in found.geometry.items():
                    if geom.geom_type in ("Polygon", "MultiPolygon"):
                        parks[key] = geom
            took = time.monotonic() - started
            print(f"tile {i}/{len(boxes)} {tuple(round(v, 4) for v in box)}: {took:.0f} s, total"
                  f" {0 if G is None else G.number_of_nodes()} nodes, {len(parks)} park polygons,"
                  f" peak RSS {_rss_mb()} MB", flush=True)
            # a tile answered from the cache takes well under a second; only a
            # tile that reached the server is followed by a pause
            if took > 2:
                if on_tile:
                    on_tile()
                if i < len(boxes):
                    time.sleep(pause_s)
    except _DownloadTimeout:
        raise RuntimeError(
            f"download exceeded {limit_s} s after tile {i - 1}/{len(boxes)}. Completed tiles are cached:"
            " run the same command again to continue, or raise --download-limit-s.")
    finally:
        signal.alarm(0)
    if G is None:
        raise RuntimeError(f"no walkable ways found in {bbox}")
    return G, list(parks.values())


def _in_park(midpoints: np.ndarray, parks: list) -> np.ndarray:
    """Per midpoint [lng, lat]: inside any of the park polygons."""
    import shapely

    inside = np.zeros(len(midpoints), dtype=bool)
    if parks and len(midpoints):
        tree = shapely.STRtree(parks)
        hits = tree.query(shapely.points(midpoints), predicate="within")
        inside[np.unique(hits[0])] = True
    return inside


# An unnamed sidewalk takes the name of a named road when a point of that road
# is within this distance of the sidewalk's midpoint and the two run within
# this angle of each other. Crossings run across the road, fail the angle test
# and stay unnamed.
SIDEWALK_NAME_RADIUS_M = 30.0
SIDEWALK_NAME_ANGLE_DEG = 25.0
_ROAD_CLASSES = [CLASS_INDEX[c] for c in ("trunk", "primary", "secondary", "tertiary", "residential", "pedestrian")]


def _axis_deg(a: np.ndarray, b: np.ndarray) -> float:
    """Direction of the line a-b in degrees [0, 180), in the metre projection."""
    return math.degrees(math.atan2(b[0] - a[0], b[1] - a[1])) % 180.0


def name_sidewalks(records: list[dict]) -> int:
    """Fill `name` of unnamed FLAG_SIDEWALK records from the nearest named road
    that runs parallel. Returns the number of records named."""
    from scipy.spatial import cKDTree

    points, axes, names = [], [], []
    for r in records:
        if r["name"] and r["edge_class"] in _ROAD_CLASSES:
            xy = _xy(*np.asarray(r["coords"], dtype=np.float64).T)
            for a, b in zip(xy[:-1], xy[1:]):
                d = float(np.hypot(*(b - a)))
                if d == 0:
                    continue
                # a point every 10 m along the segment, the segment start included
                for t in np.arange(0.0, 1.0, 10.0 / max(d, 10.0)):
                    points.append(a + (b - a) * t)
                    axes.append(_axis_deg(a, b))
                    names.append(r["name"])
    if not points:
        return 0
    tree = cKDTree(np.asarray(points))
    named = 0
    for r in records:
        if r["name"] or not r["flags"] & FLAG_SIDEWALK:
            continue
        xy = _xy(*np.asarray(r["coords"], dtype=np.float64).T)
        seg = np.hypot(*np.diff(xy, axis=0).T)
        if seg.sum() == 0:
            continue
        k = int(np.searchsorted(np.cumsum(seg), seg.sum() / 2))
        axis = _axis_deg(xy[k], xy[k + 1])
        mid = _xy(*_midpoint(r["coords"]))[0]
        dist, idx = tree.query(mid, k=8, distance_upper_bound=SIDEWALK_NAME_RADIUS_M)
        for d, i in zip(dist, idx):
            if np.isfinite(d):
                diff = abs(axes[i] - axis) % 180.0
                if min(diff, 180.0 - diff) <= SIDEWALK_NAME_ANGLE_DEG:
                    r["name"] = names[i]
                    named += 1
                    break
    return named


def _midpoint(coords: list[list[float]]) -> list[float]:
    """Point at half the length of a polyline (in degrees; precise enough for a
    point-in-polygon test)."""
    pts = np.asarray(coords, dtype=np.float64)
    seg = np.hypot(*np.diff(pts, axis=0).T)
    total = seg.sum()
    if total == 0:
        return coords[0]
    along = np.cumsum(seg)
    k = int(np.searchsorted(along, total / 2))
    t = (total / 2 - (along[k] - seg[k])) / seg[k] if seg[k] else 0.0
    return (pts[k] + (pts[k + 1] - pts[k]) * t).tolist()


def build(
    bbox: tuple[float, float, float, float],
    out: Path,
    limit_s: int = DOWNLOAD_LIMIT_S,
    overpass_url: str | None = None,
    tile_km: float = TILE_KM,
    pause_s: float = 2.0,
    on_tile=None,
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
    ox.settings.useful_tags_way = list(WAY_TAGS)

    started = time.monotonic()
    G, parks = _download(bbox, tile_km, limit_s, pause_s, on_tile)
    download_s = time.monotonic() - started
    raw_nodes = G.number_of_nodes()

    # Nothing outside the requested bbox is kept
    G = ox.truncate.truncate_graph_bbox(G, bbox)
    G = ox.simplify_graph(G)
    print(f"simplified {raw_nodes} nodes to {G.number_of_nodes()}, peak RSS {_rss_mb()} MB", flush=True)
    # One record per street segment; to_undirected keeps parallel edges whose geometry differs
    # largest_component needs the directed graph osmnx returns
    U = ox.convert.to_undirected(ox.truncate.largest_component(G))
    del G

    index = {node: i for i, node in enumerate(U.nodes)}
    node_lng = [d["x"] for _, d in U.nodes(data=True)]
    node_lat = [d["y"] for _, d in U.nodes(data=True)]

    records = []
    lit_tagged = 0
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
        edge_class, flags = classify(d)
        # merged ways with different names: the first; without a name, the road number
        label = (_values(d.get("name")) or _values(d.get("ref")) or [""])[0]
        records.append({"u": index[u], "v": index[v], "coords": coords, "length": float(d["length"]),
                        "lit": d.get("lit"), "edge_class": edge_class, "flags": flags, "name": label})
    del U

    inside = _in_park(np.asarray([_midpoint(r["coords"]) for r in records]).reshape(-1, 2), parks)
    for r, park in zip(records, inside):
        if park:
            r["flags"] |= FLAG_IN_PARK
    sidewalks_named = name_sidewalks(records)

    def edges():
        for r in records:
            yield (r["u"], r["v"], r["coords"], r["length"], infer_lit(r["lit"], r["edge_class"], r["flags"]),
                   r["edge_class"], r["flags"], r["name"])

    meta = {
        "format": 2,
        "bbox": list(bbox),
        "built_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "attribution": ATTRIBUTION,
        "network_type": "walk",
        "overpass_filter": walk_filter(),
        "park_tags": PARK_TAGS,
        "class_names": list(CLASS_NAMES),
    }
    arrays = build_arrays(node_lng, node_lat, edges(), meta)
    n_edges = len(arrays["edge_src"]) // 2
    length = arrays["edge_length"][:n_edges].astype(np.float64)
    lit = arrays["edge_lit"][:n_edges]
    classes = arrays["edge_class"][:n_edges]
    flags = arrays["edge_flags"][:n_edges]
    sidewalk = (flags & FLAG_SIDEWALK) > 0
    km_by_class = {name: round(float(length[(classes == i) & ~sidewalk].sum()) / 1000, 1)
                   for i, name in enumerate(CLASS_NAMES)}
    km_by_class["footway (sidewalk, crossing)"] = round(float(length[sidewalk].sum()) / 1000, 1)
    meta |= {
        "nodes": len(node_lng),
        "undirected_edges": n_edges,
        "length_km": round(float(length.sum()) / 1000, 1),
        "lit_tagged_edges": lit_tagged,
        "lit_km": {"unlit": round(float(length[lit == LIT_NO].sum()) / 1000, 1),
                   "unknown": round(float(length[lit == LIT_UNKNOWN].sum()) / 1000, 1),
                   "lit": round(float(length[lit == LIT_YES].sum()) / 1000, 1)},
        "km_by_class": {k: v for k, v in km_by_class.items() if v},
        "in_park_km": round(float(length[(flags & FLAG_IN_PARK) > 0].sum()) / 1000, 1),
        "underpass_km": round(float(length[(flags & FLAG_UNDERPASS) > 0].sum()) / 1000, 1),
        "park_polygons": len(parks),
        "names": len(set(r["name"] for r in records if r["name"])),
        "sidewalks_named_from_road": sidewalks_named,
    }
    arrays["meta"] = np.asarray(json.dumps(meta))
    save_graph(out, arrays)

    return meta | {
        "file": str(out.resolve()),
        "file_mb": round(out.stat().st_size / 1e6, 2),
        "raw_nodes": raw_nodes,
        "download_s": round(download_s, 1),
        "build_s": round(time.monotonic() - started, 1),
        "peak_rss_mb": _rss_mb(),
    }


def _sq(a, b) -> float:
    return (a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--bbox", default=",".join(map(str, GREATER_LONDON)),
                        help="west,south,east,north (default: Greater London)")
    parser.add_argument("--out", default="data/graph/walk.npz")
    parser.add_argument("--download-limit-s", type=int, default=DOWNLOAD_LIMIT_S)
    parser.add_argument("--tile-km", type=float, default=TILE_KM, help="side of a download tile")
    parser.add_argument("--pause-s", type=float, default=2.0, help="pause after each tile that reached the server")
    parser.add_argument(
        "--overpass-url",
        help="Overpass API base URL without /interpreter, e.g. https://overpass.openstreetmap.fr/api"
        " (default: the osmnx default, overpass-api.de)",
    )
    args = parser.parse_args()
    bbox = tuple(float(x) for x in args.bbox.split(","))
    if len(bbox) != 4:
        parser.error("--bbox must be west,south,east,north")
    stats = build(bbox, Path(args.out), args.download_limit_s, args.overpass_url, args.tile_km, args.pause_s)
    for key, value in stats.items():
        print(f"{key}: {value}")
    tagged = stats["lit_tagged_edges"] / stats["undirected_edges"]
    print(f"lit tag coverage: {tagged:.1%} of edges have the tag")


if __name__ == "__main__":
    main()
