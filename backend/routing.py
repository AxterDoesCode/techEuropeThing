"""Risk-weighted walking routes on a compact street graph. No FastAPI, no osmnx.

Graph file (.npz, written by backend/tools/build_graph.py through save_graph):

  node_lng, node_lat   float32 [N]
  edge_src, edge_dst   int32   [2M]  directed edge k and k + M are the two
                                     directions of undirected edge k % M
  edge_length          float32 [2M]  metres
  edge_lit             uint8   [2M]  1 when the OSM tag lit == "yes"
  cell_offsets         int32   [M+1] CSR over undirected edges
  cells                uint64        H3 res-9 cells (h3.str_to_int) the edge passes through
  geom_offsets         int32   [M+1] CSR over undirected edges, in points
  geom_coords          float32 [P,2] lng, lat from edge_src to edge_dst of edge k < M
  meta                 str           JSON: bbox, built_at, attribution, counts
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Sequence

import h3
import numpy as np
from scipy.sparse import csr_matrix
from scipy.sparse.csgraph import dijkstra
from scipy.spatial import cKDTree

CELL_RES = 9
DEFAULT_ALPHA = 4.0
DEFAULT_BETA = 0.15
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
    meta: dict[str, Any]
    bbox: tuple[float, float, float, float]
    # derived at load time
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


def build_arrays(
    node_lng: Sequence[float],
    node_lat: Sequence[float],
    edges: Iterable[tuple[int, int, Sequence[Sequence[float]], float, bool]],
    meta: dict[str, Any],
) -> dict[str, np.ndarray]:
    """File arrays from undirected edges (u, v, coords from u to v, length_m, lit)."""
    src, dst, length, lit = [], [], [], []
    cell_offsets, cells, geom_offsets, geom = [0], [], [0], []
    for u, v, coords, length_m, is_lit in edges:
        src.append(u)
        dst.append(v)
        length.append(length_m)
        lit.append(1 if is_lit else 0)
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
    tree = cKDTree(_xy(data[:, 0], data[:, 1]))
    weights = data[:, 2]

    mid = (graph.geom_offsets[:-1] + graph.geom_offsets[1:] - 1) // 2
    mids = _xy(graph.geom_coords[mid, 0], graph.geom_coords[mid, 1])
    density = np.zeros(m, dtype=np.float64)
    for i, near in enumerate(tree.query_ball_point(mids, BASELINE_RADIUS_M)):
        if near:
            d = np.linalg.norm(tree.data[near] - mids[i], axis=1)
            density[i] = float(np.sum(weights[near] * (1.0 - d / BASELINE_RADIUS_M)))

    cap = math.log1p(float(np.percentile(density, BASELINE_CLIP_PERCENTILE))) or 1.0
    undirected = np.minimum(1.0, np.log1p(density) / cap).astype(np.float32)
    return np.concatenate([undirected, undirected])


def combined_risk(live: np.ndarray, baseline: np.ndarray | None) -> np.ndarray:
    """1 - (1 - live) * (1 - k * baseline), the formula the cell scores use."""
    if baseline is None:
        return live
    return 1.0 - (1.0 - live) * (1.0 - BASELINE_WEIGHT * baseline)


def edge_costs(graph: Graph, risk: np.ndarray, alpha: float, beta: float) -> np.ndarray:
    if alpha < 0 or not 0 <= beta < 1:
        raise ValueError("alpha must be >= 0 and beta in [0, 1)")
    costs = graph.edge_length.astype(np.float64) * (1.0 + alpha * risk) * (1.0 - beta * graph.edge_lit)
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


def _describe(graph: Graph, edges: list[int], risk: np.ndarray) -> dict[str, Any]:
    coords: list[list[float]] = []
    for e in edges:
        part = _edge_coords(graph, e).astype(np.float64).round(6).tolist()
        coords.extend(part[1:] if coords and coords[-1] == part[0] else part)
    length = graph.edge_length[edges].astype(np.float64)
    r = risk[edges].astype(np.float64)
    total = float(length.sum())
    return {
        "geometry": {"type": "LineString", "coordinates": coords},
        "length_m": round(total, 1),
        "duration_min": round(total / WALK_SPEED_M_S / 60, 1),
        "mean_risk": round(float((length * r).sum() / total), 4),
        "max_risk": round(float(r.max()), 4),
    }


def route(
    graph: Graph,
    origin: Sequence[float],
    destination: Sequence[float],
    cell_scores: dict[str, float],
    alpha: float = DEFAULT_ALPHA,
    beta: float = DEFAULT_BETA,
    baseline: np.ndarray | None = None,
) -> dict[str, Any]:
    """`cell_scores` are per-cell risk values; with a street-level `baseline`
    (edge_baseline) pass the cells' live component only, and the two are combined
    per edge. `fast` minimises length; `safe` minimises length * (1 + alpha * risk) *
    (1 - beta * lit). With alpha == 0 the lit term is not applied either, so both
    routes are the same path."""
    source = snap(graph, origin, "origin")
    target = snap(graph, destination, "destination")
    if source == target:
        raise RouteError("origin and destination are at the same street node")

    risk = combined_risk(edge_risk(graph, cell_scores), baseline)
    fast_edges = _shortest_path(graph, edge_costs(graph, risk, 0.0, 0.0), source, target)
    if alpha > 0:
        safe_costs = edge_costs(graph, risk, alpha, beta)
        # the fast path is a feasible solution, so its cost bounds the search
        bound = float(safe_costs[fast_edges].sum()) * (1 + 1e-9)
        safe_edges = _shortest_path(graph, safe_costs, source, target, limit=bound)
    else:
        safe_edges = fast_edges

    fast = _describe(graph, fast_edges, risk)
    safe = _describe(graph, safe_edges, risk)
    reduction = 1 - safe["mean_risk"] / fast["mean_risk"] if fast["mean_risk"] > 0 else 0.0
    return {
        "fast": fast,
        "safe": safe,
        "alpha": alpha,
        "beta": beta,
        "risk_reduction": round(reduction, 4),
        "extra_distance_m": round(safe["length_m"] - fast["length_m"], 1),
        "attribution": graph.meta.get("attribution", ""),
    }
