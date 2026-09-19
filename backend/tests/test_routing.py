from pathlib import Path

import h3
import numpy as np
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend import api_route, db, routing
from backend.db import SqliteRepo
from backend.models import CellScore, utcnow

FIXTURE_GRAPH = Path(__file__).parent / "fixtures" / "grid_graph.npz"

N = 6
SPACING_M = 100.0
LNG0, LAT0 = -0.1300, 51.5080
DLNG = SPACING_M / routing._M_PER_DEG_LNG
DLAT = SPACING_M / routing._M_PER_DEG_LAT


def node(row: int, col: int) -> int:
    return row * N + col


def lnglat(row: int, col: int) -> list[float]:
    return [LNG0 + col * DLNG, LAT0 + row * DLAT]


def grid_arrays(lit_edges: set[tuple[int, int]] = frozenset()) -> dict[str, np.ndarray]:
    """6 x 6 street grid, 100 m spacing, near Trafalgar Square."""
    points = [lnglat(r, c) for r in range(N) for c in range(N)]
    edges = []
    for r in range(N):
        for c in range(N):
            for r2, c2 in ((r, c + 1), (r + 1, c)):
                if r2 < N and c2 < N:
                    u, v = node(r, c), node(r2, c2)
                    edges.append((u, v, [points[u], points[v]], SPACING_M, (u, v) in lit_edges))
    meta = {"attribution": "synthetic", "bbox": [LNG0 - 0.01, LAT0 - 0.01, LNG0 + 0.02, LAT0 + 0.02]}
    return routing.build_arrays([p[0] for p in points], [p[1] for p in points], edges, meta)


def make_graph(tmp_path, **kwargs) -> routing.Graph:
    path = tmp_path / "grid.npz"
    routing.save_graph(path, grid_arrays(**kwargs))
    return routing.load_graph(path)


def cell_at(row: float, col: float) -> str:
    return h3.latlng_to_cell(LAT0 + row * DLAT, LNG0 + col * DLNG, routing.CELL_RES)


@pytest.fixture
def graph(tmp_path):
    return make_graph(tmp_path)


# a high score in the cell at the middle of the bottom row
RISKY = {cell_at(0, 2.5): 0.9}


def test_alpha_zero_gives_identical_routes(graph):
    out = routing.route(graph, lnglat(0, 0), lnglat(0, 5), RISKY, alpha=0.0)
    assert out["safe"] == out["fast"]
    assert out["extra_distance_m"] == 0 and out["risk_reduction"] == 0
    assert out["fast"]["length_m"] == 500.0
    assert out["fast"]["duration_min"] == round(500 / 1.35 / 60, 1)


def test_safe_route_detours_around_high_risk_cell(graph):
    out = routing.route(graph, lnglat(0, 0), lnglat(0, 5), RISKY, alpha=4.0)
    fast, safe = out["fast"], out["safe"]
    assert fast["max_risk"] == pytest.approx(0.9)
    assert safe["length_m"] > fast["length_m"]
    assert safe["mean_risk"] < fast["mean_risk"]
    assert out["extra_distance_m"] == pytest.approx(safe["length_m"] - fast["length_m"])
    assert out["risk_reduction"] == pytest.approx(1 - safe["mean_risk"] / fast["mean_risk"], abs=1e-3)
    assert safe["geometry"]["coordinates"] != fast["geometry"]["coordinates"]


def test_edge_risk_is_max_over_cells_and_costs_are_positive(graph):
    risk = routing.edge_risk(graph, RISKY | {"8928308280fffff": 1.0})  # a cell outside the graph is ignored
    m = graph.n_undirected
    assert risk.shape == (2 * m,) and np.array_equal(risk[:m], risk[m:])
    assert risk.max() == pytest.approx(0.9) and risk.min() == 0.0
    k = int(np.flatnonzero((graph.edge_src[:m] == node(0, 2)) & (graph.edge_dst[:m] == node(0, 3)))[0])
    assert risk[k] == pytest.approx(0.9)
    for alpha, beta in ((0.0, 0.0), (10.0, 0.15), (10.0, 0.99)):
        assert np.all(routing.edge_costs(graph, risk, alpha, beta) > 0)
    assert np.allclose(routing.edge_costs(graph, risk, 0.0, 0.0), graph.edge_length)
    with pytest.raises(ValueError):
        routing.edge_costs(graph, risk, 1.0, 1.0)


def test_lit_edges_preferred_when_otherwise_equal(tmp_path):
    lit = {(node(0, 0), node(1, 0)), (node(1, 0), node(1, 1))}
    graph = make_graph(tmp_path, lit_edges=lit)
    out = routing.route(graph, lnglat(0, 0), lnglat(1, 1), {}, alpha=1.0, beta=0.15)
    via = [round(x, 5) for x in lnglat(1, 0)]
    assert [round(x, 5) for x in out["safe"]["geometry"]["coordinates"][1]] == via
    assert out["safe"]["length_m"] == out["fast"]["length_m"] == 200.0


def test_snapping_errors(graph):
    with pytest.raises(routing.RouteError, match="nearest walkable street"):
        routing.route(graph, [LNG0 + 0.015, LAT0], lnglat(0, 5), {})
    with pytest.raises(routing.RouteError, match="outside the routing area"):
        routing.route(graph, lnglat(0, 0), [2.35, 48.85], {})
    with pytest.raises(routing.RouteError, match="same street node"):
        routing.route(graph, lnglat(0, 0), lnglat(0, 0), {})


def test_geometry_is_continuous_and_follows_edge_shapes(tmp_path):
    # one street with a bend, stored from node 1 to node 0 so it is traversed in reverse
    a, bend, b, c = [-0.1300, 51.5080], [-0.1295, 51.5085], [-0.1290, 51.5080], [-0.1280, 51.5080]
    edges = [(1, 0, [b, bend, a], routing.polyline_length_m([b, bend, a]), False),
             (1, 2, [b, c], routing.polyline_length_m([b, c]), False)]
    path = tmp_path / "bend.npz"
    routing.save_graph(path, routing.build_arrays([a[0], b[0], c[0]], [a[1], b[1], c[1]], edges, {}))
    out = routing.route(routing.load_graph(path), a, c, {})
    coords = out["fast"]["geometry"]["coordinates"]
    assert np.allclose(coords, [a, bend, b, c], atol=1e-5)
    assert len(coords) == 4  # the shared joint appears once
    assert out["fast"]["length_m"] == pytest.approx(routing.polyline_length_m([a, bend, b, c]), abs=0.2)

    grid = make_graph(tmp_path)
    line = routing.route(grid, lnglat(0, 0), lnglat(5, 5), RISKY)["safe"]["geometry"]["coordinates"]
    steps = np.hypot(*(np.diff(np.asarray(line), axis=0) * [routing._M_PER_DEG_LNG, routing._M_PER_DEG_LAT]).T)
    assert np.allclose(steps, SPACING_M, atol=1.0)


def test_parallel_edges_use_the_cheaper_one(tmp_path):
    a, b = [-0.1300, 51.5080], [-0.1290, 51.5080]
    long_way = [a, [-0.1295, 51.5090], b]
    edges = [(0, 1, long_way, routing.polyline_length_m(long_way), False),
             (0, 1, [a, b], routing.polyline_length_m([a, b]), False)]
    path = tmp_path / "parallel.npz"
    routing.save_graph(path, routing.build_arrays([a[0], b[0]], [a[1], b[1]], edges, {}))
    out = routing.route(routing.load_graph(path), a, b, {})
    assert len(out["fast"]["geometry"]["coordinates"]) == 2


def test_committed_fixture_matches_builder(tmp_path):
    graph = routing.load_graph(FIXTURE_GRAPH)
    built = make_graph(tmp_path)
    assert np.array_equal(graph.edge_src, built.edge_src) and np.array_equal(graph.cells, built.cells)
    assert "osmnx" not in routing.__dict__ and "networkx" not in routing.__dict__


@pytest.fixture
def client(tmp_path, monkeypatch):
    db.connect(tmp_path / "risk.sqlite")
    cell = next(iter(RISKY))
    SqliteRepo().replace_cell_scores(
        [CellScore(h3=cell, res=9, live=0.9, baseline=0.0, score=0.9, top_event_ids=[])], utcnow()
    )
    monkeypatch.setenv("GRAPH_PATH", str(FIXTURE_GRAPH))
    app = FastAPI()
    app.include_router(api_route.router)
    return TestClient(app)


def test_api_route(client):
    res = client.post("/api/route", json={"origin": lnglat(0, 0), "destination": lnglat(0, 5), "alpha": 4})
    assert res.status_code == 200
    body = res.json()
    assert body["fast"]["geometry"]["type"] == "LineString"
    assert body["fast"]["max_risk"] == pytest.approx(0.9)
    assert body["safe"]["mean_risk"] < body["fast"]["mean_risk"]
    assert body["risk_reduction"] > 0 and body["extra_distance_m"] > 0

    same = client.post("/api/route", json={"origin": lnglat(0, 0), "destination": lnglat(0, 5), "alpha": 0}).json()
    assert same["safe"] == same["fast"]


def test_api_route_errors(client, monkeypatch, tmp_path):
    ok = {"origin": lnglat(0, 0), "destination": lnglat(0, 5)}
    assert client.post("/api/route", json=ok | {"alpha": 11}).status_code == 422
    assert client.post("/api/route", json={"origin": [0, 0, 0], "destination": lnglat(0, 5)}).status_code == 422
    assert client.post("/api/route", json=ok | {"origin": [-0.13, 95]}).status_code == 422
    far = client.post("/api/route", json=ok | {"destination": [2.35, 48.85]})
    assert far.status_code == 422 and "outside the routing area" in far.json()["detail"]

    monkeypatch.setenv("GRAPH_PATH", str(tmp_path / "missing.npz"))
    missing = client.post("/api/route", json=ok)
    assert missing.status_code == 503 and "build_graph" in missing.json()["detail"]


def crime_row(row: float, col: float, weighted: float) -> list:
    lng, lat = lnglat(row, col)
    return [lng, lat, int(weighted), weighted, "On or near Test Street", {}]


def test_edge_baseline_is_per_street(graph):
    # Crime points on the top row between columns 2 and 3; the row below is 100 m away
    base = routing.edge_baseline(graph, [crime_row(0, 2.5, 30.0), crime_row(0, 2.4, 10.0)])
    m = graph.n_undirected
    assert base.shape == (2 * m,) and np.array_equal(base[:m], base[m:])
    assert 0 <= base.min() and base.max() == pytest.approx(1.0)

    def edge(a: int, b: int) -> int:
        for k in range(m):
            if {int(graph.edge_src[k]), int(graph.edge_dst[k])} == {a, b}:
                return k
        raise AssertionError("no such edge")

    assert base[edge(node(0, 2), node(0, 3))] == pytest.approx(1.0)
    # the parallel street one block south is within the 120 m radius but much lower
    assert 0 < base[edge(node(1, 2), node(1, 3))] < base[edge(node(0, 2), node(0, 3))]
    # a street three blocks away is unaffected
    assert base[edge(node(3, 2), node(3, 3))] == 0
    assert not routing.edge_baseline(graph, []).any()


def test_baseline_alone_moves_the_safe_route_one_street_over(graph):
    base = routing.edge_baseline(graph, [crime_row(0, c + 0.5, 40.0) for c in range(5)])
    plain = routing.route(graph, lnglat(0, 0), lnglat(0, 5), {}, alpha=10)
    assert plain["safe"]["geometry"] == plain["fast"]["geometry"]
    res = routing.route(graph, lnglat(0, 0), lnglat(0, 5), {}, alpha=10, baseline=base)
    assert res["safe"]["mean_risk"] < res["fast"]["mean_risk"]
    assert res["extra_distance_m"] > 0
    assert res["fast"]["max_risk"] == pytest.approx(routing.BASELINE_WEIGHT)


def test_combined_risk_matches_cell_score_formula():
    live = np.array([0.0, 0.5, 1.0]); base = np.array([1.0, 0.5, 0.2])
    assert routing.combined_risk(live, None) is live
    assert routing.combined_risk(live, base) == pytest.approx([0.4, 0.6, 1.0])


def test_api_uses_crime_points_and_recomputes_for_a_new_month(client, monkeypatch):
    monkeypatch.setattr(api_route, "_baseline", None)
    repo = SqliteRepo()
    # live risk is removed so only the street-level baseline can cause a detour
    repo.replace_cell_scores([], utcnow())
    body = {"origin": lnglat(0, 0), "destination": lnglat(0, 5), "alpha": 10}
    assert client.post("/api/route", json=body).json()["extra_distance_m"] == 0

    repo.save_crime_points("2026-07", {"month": "2026-07", "rows": [crime_row(0, c + 0.5, 40.0) for c in range(5)]})
    assert client.post("/api/route", json=body).json()["extra_distance_m"] > 0

    repo.save_crime_points("2026-08", {"month": "2026-08", "rows": []})
    assert client.post("/api/route", json=body).json()["extra_distance_m"] == 0
