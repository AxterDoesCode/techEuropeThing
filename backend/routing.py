"""Risk-weighted walking routes on a compact street graph. No FastAPI, no osmnx.

Graph file (.npz, written by backend/tools/build_graph.py through save_graph):

  node_lng, node_lat   float32 [N]
  edge_src, edge_dst   int32   [2M]  directed edge k and k + M are the two
                                     directions of undirected edge k % M
  edge_length          float32 [2M]  metres
  edge_lit             uint8   [2M]  format 2: 0 unlit, 1 unknown, 2 lit (infer_lit)
                                     format 1: 1 when the OSM tag lit == "yes", else 0
  edge_class           uint8   [2M]  format 2 only: index into CLASS_NAMES
  edge_flags           uint8   [2M]  format 2 only: bitfield FLAG_*
  edge_name            uint32  [M]   optional: index into the name table, 0 = no name
  name_table           uint8         optional: UTF-8 bytes of the names joined by "\n";
                                     entry 0 is the empty string
  cell_offsets         int32   [M+1] CSR over undirected edges
  cells                uint64        H3 res-9 cells (h3.str_to_int) the edge passes through
  geom_offsets         int32   [M+1] CSR over undirected edges, in points
  geom_coords          float32 [P,2] lng, lat from edge_src to edge_dst of edge k < M
  meta                 str           JSON: bbox, built_at, attribution, counts

A file without edge_class is format 1 (written before road classes were stored).
load_graph accepts it and edge_costs then uses the format 1 cost
length * (1 + alpha * risk) * (1 - beta * lit), so a deployed backend keeps
working until the graph is rebuilt.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Sequence

import h3
import numpy as np
from scipy.sparse import csr_matrix
from scipy.sparse.csgraph import dijkstra
from scipy.spatial import cKDTree
from zoneinfo import ZoneInfo

CELL_RES = 9
DEFAULT_ALPHA = 4.0
# Format 1 graphs only: cost factor (1 - beta) on edges tagged lit=yes
DEFAULT_BETA = 0.15
# Format 2 graphs: cost factor (1 + gamma * (1 - lit01)), lit01 = 0 unlit, 0.5 unknown, 1 lit
DEFAULT_GAMMA = 0.3
WALK_SPEED_M_S = 1.35
MAX_SNAP_M = 300.0
# Street-level crime baseline: recorded crime points within this distance of a
# street segment's midpoint count towards it, weighted 1 at 0 m down to 0 here.
BASELINE_RADIUS_M = 120.0
# Log-scaled and clipped at this percentile of segments, like the cell baseline
BASELINE_CLIP_PERCENTILE = 99.5
# Same weight the cell scores give the baseline (scoring.BASELINE_WEIGHT)
BASELINE_WEIGHT = 0.4
SAMPLE_STEP_M = 50.0

# Same equirectangular projection as scoring.py
_REF_LAT = 51.5
_M_PER_DEG_LAT = 111_320.0
_M_PER_DEG_LNG = _M_PER_DEG_LAT * math.cos(math.radians(_REF_LAT))


# edge_lit values (format 2)
LIT_NO, LIT_UNKNOWN, LIT_YES = 0, 1, 2

# edge_class values (format 2). The index is stored in the file: append new
# names at the end, never reorder.
CLASS_NAMES = (
    "other",        # 0  any highway value not listed below (road, busway, elevator, ...)
    "trunk",        # 1  trunk, trunk_link
    "primary",      # 2  primary, primary_link
    "secondary",    # 3  secondary, secondary_link
    "tertiary",     # 4  tertiary, tertiary_link
    "residential",  # 5  residential, unclassified, living_street
    "pedestrian",   # 6
    "service",      # 7  service without service=alley
    "alley",        # 8  service with service=alley
    "footway",      # 9  footway; FLAG_SIDEWALK set when footway=sidewalk|crossing
    "path",         # 10
    "cycleway",     # 11
    "steps",        # 12
    "bridleway",    # 13
    "track",        # 14
    "corridor",     # 15 always carries FLAG_INDOOR
)
CLASS_INDEX = {name: i for i, name in enumerate(CLASS_NAMES)}
HIGHWAY_CLASS = {
    "trunk": "trunk", "trunk_link": "trunk",
    "primary": "primary", "primary_link": "primary",
    "secondary": "secondary", "secondary_link": "secondary",
    "tertiary": "tertiary", "tertiary_link": "tertiary",
    "residential": "residential", "unclassified": "residential", "living_street": "residential",
    "pedestrian": "pedestrian", "service": "service", "footway": "footway", "path": "path",
    "cycleway": "cycleway", "steps": "steps", "bridleway": "bridleway", "track": "track",
    "corridor": "corridor",
}
MAIN_ROAD_CLASSES = ("trunk", "primary", "secondary", "tertiary")
# Classes on which a tunnel tag means a pedestrian underpass
FOOT_CLASSES = ("pedestrian", "footway", "path", "cycleway", "steps", "bridleway", "track", "corridor")

# edge_flags bits (format 2)
FLAG_IN_PARK = 1    # edge midpoint inside a park polygon (build_graph.PARK_TAGS)
FLAG_UNDERPASS = 2  # tunnel (other than building_passage) on a FOOT_CLASSES way
FLAG_COVERED = 4    # covered=* or tunnel=building_passage
FLAG_INDOOR = 8     # indoor=* or highway=corridor
FLAG_SIDEWALK = 16  # highway=footway with footway=sidewalk|crossing

# REVISIT(route-cost-weights): judgment values, not calibrated against any data.
# Crime is recorded where people are, so risk alone sends the safe route to
# towpaths, estate footpaths and back streets; these factors counter that.
CLASS_MULTIPLIER = {
    "other": 1.00,
    "trunk": 0.95,
    "primary": 0.85,
    "secondary": 0.85,
    "tertiary": 0.85,
    "residential": 1.00,
    "pedestrian": 0.90,
    "service": 1.20,
    "alley": 1.50,
    "footway": 1.30,
    "path": 1.30,
    "cycleway": 1.30,
    "steps": 1.30,
    "bridleway": 1.60,
    "track": 1.60,
    "corridor": 1.30,
}
# Replaces the class multiplier of a footway that has FLAG_SIDEWALK
SIDEWALK_MULTIPLIER = 1.00
# Multiplied on top of the class multiplier
IN_PARK_MULTIPLIER = 1.5
# Of underpass, indoor and covered only the largest applicable factor is used:
# an underpass is usually tagged covered=yes as well
UNDERPASS_MULTIPLIER = 1.5
INDOOR_MULTIPLIER = 1.5
COVERED_MULTIPLIER = 1.15

# Classes on which a missing lit tag is read as lit. Measured on the London
# extract: where these roads carry the tag, >= 99% are lit=yes (secondary
# 92-100%); 56-70% of walkable length has no lit tag at all.
_LIT_BY_DEFAULT = ("trunk", "primary", "secondary", "tertiary", "residential", "pedestrian")
# Missing lit tag read as unlit
_UNLIT_BY_DEFAULT = ("path", "bridleway", "track")

# REVISIT(night-multiplier): owner's decision, not calibrated. Factor on the
# crime baseline by London local time: 1.0 from 06:00 to 18:00, a quarter sine
# up to NIGHT_PEAK at 03:00, a quarter sine back down to 1.0 at 06:00.
NIGHT_PEAK = 1.3
NIGHT_START_H, NIGHT_PEAK_H, NIGHT_END_H = 18.0, 3.0, 6.0
LONDON_TZ = ZoneInfo("Europe/London")

# REVISIT(path-risk-score): placeholder. path_risk = 1 - exp(-K * sum(risk * metres)),
# with K set so that 1000 m at risk 0.5 gives 0.5. It is not calibrated against
# outcomes and the edge risk it sums will change as data sources are added.
# Revisit the form and the constant once all data sources are in.
PATH_RISK_K = math.log(2) / 500.0


class RouteError(ValueError):
    """The request cannot be routed (point outside the graph, no path)."""


@dataclass
class Graph:
    node_lng: np.ndarray
    node_lat: np.ndarray
    edge_src: np.ndarray
    edge_dst: np.ndarray
    edge_length: np.ndarray
    edge_lit: np.ndarray
    cell_offsets: np.ndarray
    cells: np.ndarray
    geom_offsets: np.ndarray
    geom_coords: np.ndarray
    # format 1 files have no class or flags: residential and 0 are filled in
    edge_class: np.ndarray
    edge_flags: np.ndarray
    # per undirected edge; all 0 when the file has no names
    edge_name: np.ndarray
    names: list[str]
    meta: dict[str, Any]
    bbox: tuple[float, float, float, float]
    # 1: edge_lit is 0/1 and the cost uses beta; 2: classes, flags, 3-level lit
    format: int
    # derived at load time
    edge_multiplier: np.ndarray  # float32, class and flag factors of the cost
    tree: cKDTree
    unique_cells: np.ndarray  # sorted uint64
    cell_inverse: np.ndarray  # index into unique_cells for every entry of `cells`
    # CSR adjacency structure with one entry per distinct (src, dst) pair.
    # Parallel edges share an entry; its value is the minimum cost of the group.
    perm: np.ndarray  # directed edge ids sorted by (src, dst)
    group_starts: np.ndarray  # start of each (src, dst) group in `perm`
    indptr: np.ndarray
    indices: np.ndarray

    @property
    def n_nodes(self) -> int:
        return len(self.node_lng)

    @property
    def n_undirected(self) -> int:
        return len(self.edge_src) // 2

    def nbytes(self) -> int:
        arrays = [v for v in self.__dict__.values() if isinstance(v, np.ndarray)]
        return sum(a.nbytes for a in arrays) + self.tree.data.nbytes


def _xy(lng: np.ndarray | float, lat: np.ndarray | float) -> np.ndarray:
    return np.column_stack([np.asarray(lng, dtype=np.float64) * _M_PER_DEG_LNG,
                            np.asarray(lat, dtype=np.float64) * _M_PER_DEG_LAT])


def polyline_length_m(coords: Sequence[Sequence[float]]) -> float:
    xy = _xy(*np.asarray(coords, dtype=np.float64).T)
    return float(np.hypot(*np.diff(xy, axis=0).T).sum())


def edge_cells(coords: Sequence[Sequence[float]], step_m: float = SAMPLE_STEP_M) -> list[int]:
    """H3 cells (as integers) at the vertices of a polyline and at points every
    `step_m` metres along it, without duplicates."""
    pts = np.asarray(coords, dtype=np.float64)
    samples = [pts]
    seg_len = np.hypot(*np.diff(_xy(*pts.T), axis=0).T)
    for a, b, d in zip(pts[:-1], pts[1:], seg_len):
        n = int(d // step_m)
        if n:
            t = (np.arange(1, n + 1) * step_m / d)[:, None]
            samples.append(a + (b - a) * t[t[:, 0] < 1.0])
    cells = {h3.str_to_int(h3.latlng_to_cell(lat, lng, CELL_RES)) for lng, lat in np.vstack(samples)}
    return sorted(cells)


def _values(value: Any) -> list[str]:
    """Tag values of an edge. osmnx stores a list when simplification merged ways
    with different values, and nothing (or NaN) when no way had the tag."""
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return []
    return [str(v) for v in value] if isinstance(value, (list, tuple, set)) else [str(value)]


def classify(tags: dict[str, Any]) -> tuple[int, int]:
    """(edge_class, edge_flags without FLAG_IN_PARK) from the OSM tags of an edge.
    When merged ways differ, the class with the largest multiplier is used."""
    highways = _values(tags.get("highway"))
    service = _values(tags.get("service"))
    footway = _values(tags.get("footway"))
    names = []
    for highway in highways:
        name = HIGHWAY_CLASS.get(highway, "other")
        if name == "service" and "alley" in service:
            name = "alley"
        names.append(name)
    # equal multipliers: the class listed first in CLASS_NAMES, so the result
    # does not depend on the order osmnx lists the merged values in
    name = max(names, key=lambda c: (CLASS_MULTIPLIER[c], -CLASS_INDEX[c])) if names else "other"

    flags = 0
    if set(names) == {"footway"} and footway and set(footway) <= {"sidewalk", "crossing"}:
        flags |= FLAG_SIDEWALK
    tunnel = [v for v in _values(tags.get("tunnel")) if v != "no"]
    if name in FOOT_CLASSES and any(v != "building_passage" for v in tunnel):
        flags |= FLAG_UNDERPASS
    if "building_passage" in tunnel or any(v != "no" for v in _values(tags.get("covered"))):
        flags |= FLAG_COVERED
    if "corridor" in highways or any(v != "no" for v in _values(tags.get("indoor"))):
        flags |= FLAG_INDOOR
    return CLASS_INDEX[name], flags


def infer_lit(lit: Any, edge_class: int, flags: int) -> int:
    """LIT_NO, LIT_UNKNOWN or LIT_YES.

    An explicit tag decides: "no" (on any merged way) is unlit, every other
    value (yes, 24/7, automatic, limited, ...) is lit. Without a tag: an edge
    in a park, a path, a bridleway or a track is unlit (inside parks lit=no
    outnumbers lit=yes 2-4x); the roads in _LIT_BY_DEFAULT and sidewalk or
    crossing footways are lit; everything else (other footways, service roads,
    steps, corridors, cycleways) is unknown."""
    values = _values(lit)
    if values:
        return LIT_NO if "no" in values else LIT_YES
    name = CLASS_NAMES[edge_class]
    if flags & FLAG_IN_PARK or name in _UNLIT_BY_DEFAULT:
        return LIT_NO
    if name in _LIT_BY_DEFAULT or flags & FLAG_SIDEWALK:
        return LIT_YES
    return LIT_UNKNOWN


def edge_multipliers(edge_class: np.ndarray, edge_flags: np.ndarray) -> np.ndarray:
    """Class and flag factors of the cost, per edge."""
    table = np.asarray([CLASS_MULTIPLIER[name] for name in CLASS_NAMES], dtype=np.float32)
    # a class index written by a newer build than this code counts as "other"
    mult = table[np.where(edge_class < len(table), edge_class, 0)]
    mult[(edge_flags & FLAG_SIDEWALK) > 0] = SIDEWALK_MULTIPLIER
    passage = np.ones_like(mult)
    for flag, factor in ((FLAG_COVERED, COVERED_MULTIPLIER), (FLAG_INDOOR, INDOOR_MULTIPLIER),
                         (FLAG_UNDERPASS, UNDERPASS_MULTIPLIER)):
        passage = np.where((edge_flags & flag) > 0, np.maximum(passage, factor), passage)
    park = np.where((edge_flags & FLAG_IN_PARK) > 0, IN_PARK_MULTIPLIER, 1.0)
    return (mult * passage * park).astype(np.float32)


def build_arrays(
    node_lng: Sequence[float],
    node_lat: Sequence[float],
    edges: Iterable[Sequence[Any]],
    meta: dict[str, Any],
) -> dict[str, np.ndarray]:
    """File arrays (format 2) from undirected edges
    (u, v, coords from u to v, length_m, lit[, edge_class[, edge_flags[, name]]]).
    `lit` is LIT_NO / LIT_UNKNOWN / LIT_YES, or a bool (False unlit, True lit).
    The class defaults to residential, the flags to 0 and the name to none."""
    src, dst, length, lit, classes, flags = [], [], [], [], [], []
    name_index: dict[str, int] = {"": 0}
    names = []
    cell_offsets, cells, geom_offsets, geom = [0], [], [0], []
    for u, v, coords, length_m, edge_lit, *rest in edges:
        src.append(u)
        dst.append(v)
        length.append(length_m)
        if isinstance(edge_lit, (bool, np.bool_)):
            edge_lit = LIT_YES if edge_lit else LIT_NO
        lit.append(int(edge_lit))
        classes.append(int(rest[0]) if rest else CLASS_INDEX["residential"])
        flags.append(int(rest[1]) if len(rest) > 1 else 0)
        name = (rest[2] or "").replace("\n", " ").strip() if len(rest) > 2 else ""
        names.append(name_index.setdefault(name, len(name_index)))
        cells.extend(edge_cells(coords))
        cell_offsets.append(len(cells))
        geom.extend(coords)
        geom_offsets.append(len(geom))
    return {
        "node_lng": np.asarray(node_lng, dtype=np.float32),
        "node_lat": np.asarray(node_lat, dtype=np.float32),
        "edge_src": np.asarray(src + dst, dtype=np.int32),
        "edge_dst": np.asarray(dst + src, dtype=np.int32),
        "edge_length": np.asarray(length + length, dtype=np.float32),
        "edge_lit": np.asarray(lit + lit, dtype=np.uint8),
        "edge_class": np.asarray(classes + classes, dtype=np.uint8),
        "edge_flags": np.asarray(flags + flags, dtype=np.uint8),
        "edge_name": np.asarray(names, dtype=np.uint32),
        "name_table": np.frombuffer("\n".join(name_index).encode(), dtype=np.uint8),
        "cell_offsets": np.asarray(cell_offsets, dtype=np.int32),
        "cells": np.asarray(cells, dtype=np.uint64),
        "geom_offsets": np.asarray(geom_offsets, dtype=np.int32),
        "geom_coords": np.asarray(geom, dtype=np.float32).reshape(-1, 2),
        "meta": np.asarray(json.dumps(meta)),
    }


def save_graph(path: str | Path, arrays: dict[str, np.ndarray]) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "wb") as fh:
        np.savez_compressed(fh, **arrays)


def load_graph(path: str | Path) -> Graph:
    with np.load(path) as z:
        a = {k: z[k] for k in z.files}
    meta = json.loads(str(a.pop("meta")))
    src, dst = a["edge_src"], a["edge_dst"]
    n = len(a["node_lng"])
    fmt = 2 if "edge_class" in a else 1
    if fmt == 1:
        a["edge_class"] = np.full(len(src), CLASS_INDEX["residential"], dtype=np.uint8)
        a["edge_flags"] = np.zeros(len(src), dtype=np.uint8)
    names = a["name_table"].tobytes().decode().split("\n") if "name_table" in a else [""]
    if "edge_name" not in a:
        a["edge_name"] = np.zeros(len(src) // 2, dtype=np.uint32)
    # arrays added by a later format are ignored by this code
    a = {k: v for k, v in a.items() if k in Graph.__dataclass_fields__}

    unique_cells, cell_inverse = np.unique(a["cells"], return_inverse=True)

    pair = src.astype(np.int64) * n + dst
    perm = np.argsort(pair, kind="stable")
    sorted_pair = pair[perm]
    group_starts = np.flatnonzero(np.r_[True, sorted_pair[1:] != sorted_pair[:-1]])
    group_src = src[perm][group_starts]
    indptr = np.zeros(n + 1, dtype=np.int32)
    np.cumsum(np.bincount(group_src, minlength=n), out=indptr[1:])

    lng, lat = a["node_lng"], a["node_lat"]
    # without a recorded bbox, use the node extent padded by the snap limit
    pad_lng, pad_lat = MAX_SNAP_M / _M_PER_DEG_LNG, MAX_SNAP_M / _M_PER_DEG_LAT
    bbox = tuple(meta.get("bbox") or (float(lng.min()) - pad_lng, float(lat.min()) - pad_lat,
                                      float(lng.max()) + pad_lng, float(lat.max()) + pad_lat))
    return Graph(
        **a,
        meta=meta,
        bbox=bbox,
        names=names,
        format=fmt,
        edge_multiplier=edge_multipliers(a["edge_class"], a["edge_flags"]),
        tree=cKDTree(_xy(lng, lat)),
        unique_cells=unique_cells,
        cell_inverse=cell_inverse.astype(np.int32),
        perm=perm.astype(np.int32),
        group_starts=group_starts.astype(np.int64),
        indptr=indptr,
        indices=dst[perm][group_starts].astype(np.int32),
    )


def edge_risk(graph: Graph, cell_scores: dict[str, float]) -> np.ndarray:
    """Per directed edge: the maximum score of the cells the edge passes through.
    Cells missing from `cell_scores` count as 0."""
    per_cell = np.zeros(len(graph.unique_cells), dtype=np.float32)
    if cell_scores:
        ids = np.fromiter((h3.str_to_int(c) for c in cell_scores), dtype=np.uint64, count=len(cell_scores))
        values = np.fromiter(cell_scores.values(), dtype=np.float32, count=len(cell_scores))
        pos = np.searchsorted(graph.unique_cells, ids)
        pos[pos == len(graph.unique_cells)] = 0
        known = graph.unique_cells[pos] == ids
        per_cell[pos[known]] = values[known]
    # every edge has at least one cell, so no reduceat segment is empty
    undirected = np.maximum.reduceat(per_cell[graph.cell_inverse], graph.cell_offsets[:-1])
    return np.concatenate([undirected, undirected])


def edge_baseline(graph: Graph, crime_rows: Iterable[Sequence[Any]]) -> np.ndarray:
    """Per directed edge: recorded-crime density near the street segment, in [0, 1].

    `crime_rows` are the police.uk street points ([lng, lat, count, weighted, ...]).
    The H3 res-9 cells used for the map are about 350 m across, so parallel
    streets share one value and a route cannot avoid anything by moving one
    street over. police.uk snaps crimes to individual street points, which
    gives a per-street value."""
    m = graph.n_undirected
    rows = [(r[0], r[1], r[3]) for r in crime_rows]
    if not rows:
        return np.zeros(2 * m, dtype=np.float32)
    data = np.asarray(rows, dtype=np.float64)
    points = cKDTree(_xy(data[:, 0], data[:, 1]))
    weights = data[:, 2]

    mid = (graph.geom_offsets[:-1] + graph.geom_offsets[1:] - 1) // 2
    mids = cKDTree(_xy(graph.geom_coords[mid, 0], graph.geom_coords[mid, 1]))
    # All (segment, crime point) pairs within the radius, computed in one call
    pairs = mids.sparse_distance_matrix(points, BASELINE_RADIUS_M, output_type="coo_matrix")
    contribution = weights[pairs.col] * (1.0 - pairs.data / BASELINE_RADIUS_M)
    density = np.bincount(pairs.row, weights=contribution, minlength=m)

    cap = math.log1p(float(np.percentile(density, BASELINE_CLIP_PERCENTILE))) or 1.0
    undirected = np.minimum(1.0, np.log1p(density) / cap).astype(np.float32)
    return np.concatenate([undirected, undirected])


def night_multiplier(when: datetime) -> float:
    """Factor on the crime baseline at `when` (REVISIT(night-multiplier), see the
    constants). A datetime without a timezone is read as London local time."""
    local = when.astimezone(LONDON_TZ) if when.tzinfo else when
    h = local.hour + local.minute / 60 + local.second / 3600
    if NIGHT_END_H <= h < NIGHT_START_H:
        return 1.0
    if h >= NIGHT_START_H or h < NIGHT_PEAK_H:
        rise_h = NIGHT_PEAK_H + 24 - NIGHT_START_H
        phase = ((h - NIGHT_START_H) % 24) / rise_h
        return 1.0 + (NIGHT_PEAK - 1.0) * math.sin(math.pi / 2 * phase)
    phase = (h - NIGHT_PEAK_H) / (NIGHT_END_H - NIGHT_PEAK_H)
    return 1.0 + (NIGHT_PEAK - 1.0) * math.cos(math.pi / 2 * phase)


def combined_risk(live: np.ndarray, baseline: np.ndarray | None, night: float = 1.0) -> np.ndarray:
    """1 - (1 - live) * (1 - k * baseline), the formula the cell scores use, with
    the baseline term multiplied by `night` (night_multiplier). Clamped to [0, 1]."""
    if baseline is None:
        return live
    term = np.clip(BASELINE_WEIGHT * night * baseline, 0.0, 1.0)
    return np.clip(1.0 - (1.0 - live) * (1.0 - term), 0.0, 1.0)


def edge_costs(graph: Graph, risk: np.ndarray, alpha: float, beta: float = DEFAULT_BETA,
               gamma: float = DEFAULT_GAMMA, plain: bool = False) -> np.ndarray:
    """Format 2: length * (1 + alpha * risk) * edge_multiplier * (1 + gamma * (1 - lit01)).
    Format 1: length * (1 + alpha * risk) * (1 - beta * lit).
    `plain` gives the cost of the fast route: length only. Every factor is
    positive, so costs are strictly positive."""
    if alpha < 0 or not 0 <= beta < 1 or gamma < 0:
        raise ValueError("alpha and gamma must be >= 0 and beta in [0, 1)")
    length = graph.edge_length.astype(np.float64)
    if plain:
        costs = length
    elif graph.format == 1:
        costs = length * (1.0 + alpha * risk) * (1.0 - beta * graph.edge_lit)
    else:
        lit01 = graph.edge_lit.astype(np.float64) / LIT_YES
        costs = length * (1.0 + alpha * risk) * graph.edge_multiplier * (1.0 + gamma * (1.0 - lit01))
    assert np.all(costs > 0), "edge costs must be strictly positive"
    return costs


def snap(graph: Graph, point: Sequence[float], label: str = "point") -> int:
    lng, lat = float(point[0]), float(point[1])
    w, s, e, n = graph.bbox
    if not (w <= lng <= e and s <= lat <= n):
        raise RouteError(f"{label} [{lng:.5f}, {lat:.5f}] is outside the routing area {list(graph.bbox)}")
    dist, node = graph.tree.query(_xy(lng, lat)[0])
    if dist > MAX_SNAP_M:
        raise RouteError(f"{label} is {dist:.0f} m from the nearest walkable street (limit {MAX_SNAP_M:.0f} m)")
    return int(node)


def _shortest_path(graph: Graph, costs: np.ndarray, source: int, target: int, limit: float = np.inf) -> list[int]:
    """Directed edge ids from source to target."""
    data = np.minimum.reduceat(costs[graph.perm], graph.group_starts)
    n = graph.n_nodes
    matrix = csr_matrix((data, graph.indices, graph.indptr), shape=(n, n))
    dist, pred = dijkstra(matrix, directed=True, indices=source, return_predecessors=True, limit=limit)
    if not np.isfinite(dist[target]):
        raise RouteError("no walkable path between origin and destination")
    nodes = [target]
    while nodes[-1] != source:
        nodes.append(int(pred[nodes[-1]]))
    nodes.reverse()

    edges = []
    ends = np.r_[graph.group_starts[1:], len(graph.perm)]
    for a, b in zip(nodes[:-1], nodes[1:]):
        lo, hi = graph.indptr[a], graph.indptr[a + 1]
        g = lo + int(np.searchsorted(graph.indices[lo:hi], b))
        group = graph.perm[graph.group_starts[g]:ends[g]]
        edges.append(int(group[np.argmin(costs[group])]))
    return edges


def _edge_coords(graph: Graph, edge: int) -> np.ndarray:
    m = graph.n_undirected
    k = edge % m
    coords = graph.geom_coords[graph.geom_offsets[k]:graph.geom_offsets[k + 1]]
    return coords[::-1] if edge >= m else coords


# Turn-by-turn steps. A step shorter than MIN_STEP_M is merged into a neighbour:
# crossings and junction fragments would otherwise each produce an instruction.
MIN_STEP_M = 15.0
# The direction of a step at its start or end is measured over this distance
BEARING_SPAN_M = 15.0
CONTINUE_BELOW_DEG, BEAR_BELOW_DEG, TURN_UP_TO_DEG = 25.0, 60.0, 150.0
_COMPASS = ("north", "north-east", "east", "south-east", "south", "south-west", "west", "north-west")
# Unnamed stretches: label -> wording after "onto" / "on"
UNNAMED_PHRASE = {
    "underpass": "an underpass",
    "indoor passage": "an indoor passage",
    "path through park": "a path through the park",
    "pavement": "the pavement",
    "steps": "the steps",
    "footpath": "a footpath",
    "track": "a track",
    "alley": "an alley",
    "service road": "a service road",
    "unnamed road": "an unnamed road",
}


def unnamed_label(edge_class: int, flags: int) -> str:
    name = CLASS_NAMES[edge_class] if edge_class < len(CLASS_NAMES) else "other"
    if flags & FLAG_UNDERPASS:
        return "underpass"
    if flags & FLAG_INDOOR:
        return "indoor passage"
    if name == "steps":
        return "steps"
    if flags & FLAG_IN_PARK:
        return "path through park"
    if flags & FLAG_SIDEWALK:
        return "pavement"
    if name in ("footway", "path", "cycleway", "pedestrian"):
        return "footpath"
    if name in ("bridleway", "track"):
        return "track"
    return {"alley": "alley", "service": "service road"}.get(name, "unnamed road")


def bearing_deg(a: Sequence[float], b: Sequence[float]) -> float:
    """Compass bearing in degrees [0, 360) from a to b, both in the metre
    projection of _xy (x east, y north)."""
    return math.degrees(math.atan2(b[0] - a[0], b[1] - a[1])) % 360.0


def turn_instruction(bearing_in: float, bearing_out: float) -> str:
    """Wording for the change from bearing_in to bearing_out, without the street."""
    change = (bearing_out - bearing_in + 180.0) % 360.0 - 180.0  # (-180, 180], positive = right
    side = "right" if change > 0 else "left"
    if abs(change) < CONTINUE_BELOW_DEG:
        return "Continue"
    if abs(change) < BEAR_BELOW_DEG:
        return f"Bear {side}"
    if abs(change) <= TURN_UP_TO_DEG:
        return f"Turn {side}"
    return "Turn around"


def _end_bearing(xy: np.ndarray, at_start: bool) -> float:
    """Bearing of travel over the first (or last) BEARING_SPAN_M of a polyline."""
    pts = xy if at_start else xy[::-1]
    along = np.r_[0.0, np.cumsum(np.hypot(*np.diff(pts, axis=0).T))]
    k = min(int(np.searchsorted(along, BEARING_SPAN_M)), len(pts) - 1)
    # duplicate points: extend until the two points differ
    while k < len(pts) - 1 and along[k] == 0:
        k += 1
    return bearing_deg(pts[0], pts[k]) if at_start else bearing_deg(pts[k], pts[0])


def merge_steps(groups: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """`groups` are {"label", "edges", "length"} in route order. Consecutive groups
    with one label are joined; a group shorter than MIN_STEP_M is then added to
    the previous group (the first one to the next), keeping that group's label."""
    def coalesce(items):
        out: list[dict[str, Any]] = []
        for g in items:
            if out and out[-1]["label"] == g["label"]:
                out[-1] = {**out[-1], "edges": out[-1]["edges"] + g["edges"], "length": out[-1]["length"] + g["length"]}
            else:
                out.append(dict(g))
        return out

    groups = coalesce(groups)
    while len(groups) > 1:
        short = next((i for i, g in enumerate(groups) if g["length"] < MIN_STEP_M), None)
        if short is None:
            break
        keep = short - 1 if short else 1
        a, b = sorted((keep, short))
        merged = {"label": groups[keep]["label"], "edges": groups[a]["edges"] + groups[b]["edges"],
                  "length": groups[a]["length"] + groups[b]["length"]}
        groups = coalesce(groups[:a] + [merged] + groups[b + 1:])
    return groups


def route_steps(graph: Graph, edges: list[int], risk: np.ndarray) -> list[dict[str, Any]]:
    """Turn-by-turn steps of a route. Consecutive edges with the same street name
    form a step; unnamed edges are grouped by unnamed_label."""
    m = graph.n_undirected
    groups = []
    for e in edges:
        name = graph.names[graph.edge_name[e % m]]
        label = ("name", name) if name else ("unnamed", unnamed_label(int(graph.edge_class[e]), int(graph.edge_flags[e])))
        groups.append({"label": label, "edges": [e], "length": float(graph.edge_length[e])})

    steps = []
    previous_bearing = None
    for g in merge_steps(groups):
        coords = np.vstack([_edge_coords(graph, e) for e in g["edges"]]).astype(np.float64)
        xy = _xy(coords[:, 0], coords[:, 1])
        kind, text = g["label"]
        where = text if kind == "name" else UNNAMED_PHRASE[text]
        start_bearing = _end_bearing(xy, at_start=True)
        if previous_bearing is None:
            instruction = f"Head {_COMPASS[int((start_bearing + 22.5) // 45) % 8]} on {where}"
        else:
            instruction = f"{turn_instruction(previous_bearing, start_bearing)} onto {where}"
        previous_bearing = _end_bearing(xy, at_start=False)
        length = graph.edge_length[g["edges"]].astype(np.float64)
        total = float(length.sum())
        lit = graph.edge_lit[g["edges"]] == (LIT_YES if graph.format == 2 else 1)
        steps.append({
            "instruction": instruction,
            "street": text if kind == "name" else None,
            "distance_m": round(total, 1),
            "duration_s": round(total / WALK_SPEED_M_S, 1),
            "lit": bool(length[lit].sum() > total / 2),
            "risk": round(float((length * risk[g["edges"]]).sum() / total), 4),
            "start": coords[0].round(6).tolist(),
        })
    end = _edge_coords(graph, edges[-1])[-1].astype(np.float64).round(6).tolist()
    steps.append({"instruction": "Arrive at destination", "street": None, "distance_m": 0.0, "duration_s": 0.0,
                  "lit": steps[-1]["lit"], "risk": 0.0, "start": end})
    return steps


def path_risk(risk: np.ndarray, length_m: np.ndarray) -> float:
    """REVISIT(path-risk-score): placeholder score of a whole route in [0, 1),
    1 - exp(-PATH_RISK_K * sum(risk * metres)). See PATH_RISK_K."""
    exposure = float((np.asarray(risk, dtype=np.float64) * np.asarray(length_m, dtype=np.float64)).sum())
    return 1.0 - math.exp(-PATH_RISK_K * exposure)


def _describe(graph: Graph, edges: list[int], risk: np.ndarray) -> dict[str, Any]:
    coords: list[list[float]] = []
    for e in edges:
        part = _edge_coords(graph, e).astype(np.float64).round(6).tolist()
        coords.extend(part[1:] if coords and coords[-1] == part[0] else part)
    length = graph.edge_length[edges].astype(np.float64)
    r = risk[edges].astype(np.float64)
    total = float(length.sum())
    flags = graph.edge_flags[edges]
    main = np.isin(graph.edge_class[edges], [CLASS_INDEX[name] for name in MAIN_ROAD_CLASSES])
    # format 1 files record only lit=yes, as the value 1
    lit = graph.edge_lit[edges] == (LIT_YES if graph.format == 2 else 1)
    return {
        "geometry": {"type": "LineString", "coordinates": coords},
        "length_m": round(total, 1),
        "duration_min": round(total / WALK_SPEED_M_S / 60, 1),
        "mean_risk": round(float((length * r).sum() / total), 4),
        "max_risk": round(float(r.max()), 4),
        "path_risk": round(path_risk(r, length), 4),
        "lit_share": round(float(length[lit].sum() / total), 4),
        # road centrelines only: a sidewalk mapped as its own way is a footway
        "main_road_share": round(float(length[main].sum() / total), 4),
        "park_m": round(float(length[(flags & FLAG_IN_PARK) > 0].sum()), 1),
        "underpass_m": round(float(length[(flags & FLAG_UNDERPASS) > 0].sum()), 1),
        "steps": route_steps(graph, edges, risk),
    }


# The fast search is first limited to this multiple of the straight-line distance
# plus a constant, so a short request does not scan the whole graph
FAST_SEARCH_FACTOR = 1.6
FAST_SEARCH_EXTRA_M = 500.0


def route(
    graph: Graph,
    origin: Sequence[float],
    destination: Sequence[float],
    cell_scores: dict[str, float],
    alpha: float = DEFAULT_ALPHA,
    beta: float = DEFAULT_BETA,
    baseline: np.ndarray | None = None,
    gamma: float = DEFAULT_GAMMA,
    depart_at: datetime | None = None,
) -> dict[str, Any]:
    """`cell_scores` are per-cell risk values; with a street-level `baseline`
    (edge_baseline) pass the cells' live component only, and the two are combined
    per edge, the baseline scaled by night_multiplier(depart_at) (1.0 without
    `depart_at`). `fast` minimises length; `safe` minimises edge_costs. With
    alpha == 0 the class and lit factors are not applied either, so both routes
    are the same path."""
    source = snap(graph, origin, "origin")
    target = snap(graph, destination, "destination")
    if source == target:
        raise RouteError("origin and destination are at the same street node")

    night = night_multiplier(depart_at) if depart_at is not None else 1.0
    risk = combined_risk(edge_risk(graph, cell_scores), baseline, night)
    fast_costs = edge_costs(graph, risk, 0.0, plain=True)
    straight = float(np.hypot(*(graph.tree.data[source] - graph.tree.data[target])))
    try:
        fast_edges = _shortest_path(graph, fast_costs, source, target,
                                    limit=straight * FAST_SEARCH_FACTOR + FAST_SEARCH_EXTRA_M)
    except RouteError:
        fast_edges = _shortest_path(graph, fast_costs, source, target)
    if alpha > 0:
        safe_costs = edge_costs(graph, risk, alpha, beta, gamma)
        # the fast path is a feasible solution, so its cost bounds the search
        bound = float(safe_costs[fast_edges].sum()) * (1 + 1e-9)
        safe_edges = _shortest_path(graph, safe_costs, source, target, limit=bound)
    else:
        safe_edges = fast_edges

    fast = _describe(graph, fast_edges, risk)
    safe = _describe(graph, safe_edges, risk)
    # The safe route minimises cost, not mean risk, so its mean (and its maximum)
    # can be above the fast route's; the reduction is reported as 0 then
    reduction = max(0.0, 1 - safe["mean_risk"] / fast["mean_risk"]) if fast["mean_risk"] > 0 else 0.0
    return {
        "fast": fast,
        "safe": safe,
        "alpha": alpha,
        "beta": beta if graph.format == 1 else 0.0,
        "gamma": gamma if graph.format == 2 else 0.0,
        "night_multiplier": round(night, 4),
        "risk_reduction": round(reduction, 4),
        "extra_distance_m": round(safe["length_m"] - fast["length_m"], 1),
        "attribution": graph.meta.get("attribution", ""),
    }
