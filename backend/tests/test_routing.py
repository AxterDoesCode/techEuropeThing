from datetime import datetime, timedelta, timezone
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
    assert np.allclose(routing.edge_costs(graph, risk, 0.0, plain=True), graph.edge_length)
    # an all-residential, all-unlit graph: every edge carries the same lit factor
    assert np.allclose(routing.edge_costs(graph, risk, 0.0, gamma=0.3), graph.edge_length * 1.3)
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
    # the committed fixture is a format 1 file (no edge_class, edge_flags, names)
    graph = routing.load_graph(FIXTURE_GRAPH)
    built = make_graph(tmp_path)
    assert (graph.format, built.format) == (1, 2)
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


# --- format 2: road classes, lit inference, flags ---

C = routing.CLASS_INDEX


def test_classify_and_worst_class_of_merged_ways():
    assert routing.classify({"highway": "primary_link"}) == (C["primary"], 0)
    assert routing.classify({"highway": "living_street"}) == (C["residential"], 0)
    assert routing.classify({"highway": "service", "service": "alley"}) == (C["alley"], 0)
    assert routing.classify({"highway": "service", "service": "driveway"}) == (C["service"], 0)
    assert routing.classify({"highway": "busway"}) == (C["other"], 0)
    assert routing.classify({"highway": "footway", "footway": "sidewalk"}) == (C["footway"], routing.FLAG_SIDEWALK)
    assert routing.classify({"highway": "footway", "footway": ["crossing", "sidewalk"]})[1] == routing.FLAG_SIDEWALK
    # a sidewalk merged with another kind of way is not a sidewalk
    assert routing.classify({"highway": ["footway", "path"], "footway": "sidewalk"}) == (C["footway"], 0)
    assert routing.classify({"highway": "footway", "footway": ["sidewalk", "access_aisle"]}) == (C["footway"], 0)
    # merged ways: the class with the largest multiplier, in either order
    assert routing.classify({"highway": ["residential", "track"]})[0] == C["track"]
    assert routing.classify({"highway": ["track", "primary"]})[0] == C["track"]
    assert routing.classify({"highway": ["steps", "footway"]})[0] == routing.classify({"highway": ["footway", "steps"]})[0]
    # tunnel: an underpass only on a foot-type way; a building passage is "covered"
    assert routing.classify({"highway": "footway", "tunnel": "yes"})[1] == routing.FLAG_UNDERPASS
    assert routing.classify({"highway": "primary", "tunnel": "yes"})[1] == 0
    assert routing.classify({"highway": "footway", "tunnel": "building_passage"})[1] == routing.FLAG_COVERED
    assert routing.classify({"highway": "footway", "covered": "yes", "tunnel": float("nan")})[1] == routing.FLAG_COVERED
    assert routing.classify({"highway": "corridor"}) == (C["corridor"], routing.FLAG_INDOOR)
    assert routing.classify({"highway": "footway", "indoor": "yes"})[1] == routing.FLAG_INDOOR


@pytest.mark.parametrize("lit, highway, flags, expected", [
    ("yes", "path", 0, routing.LIT_YES),
    ("24/7", "footway", 0, routing.LIT_YES),
    ("automatic", "track", routing.FLAG_IN_PARK, routing.LIT_YES),
    ("limited", "service", 0, routing.LIT_YES),
    ("no", "primary", 0, routing.LIT_NO),
    (["yes", "no"], "residential", 0, routing.LIT_NO),
    *[(None, h, 0, routing.LIT_YES) for h in ("trunk", "primary", "secondary", "tertiary", "residential", "pedestrian")],
    (None, "footway", routing.FLAG_SIDEWALK, routing.LIT_YES),
    *[(None, h, 0, routing.LIT_NO) for h in ("path", "bridleway", "track")],
    (None, "residential", routing.FLAG_IN_PARK, routing.LIT_NO),
    (None, "footway", routing.FLAG_SIDEWALK | routing.FLAG_IN_PARK, routing.LIT_NO),
    *[(None, h, 0, routing.LIT_UNKNOWN) for h in ("footway", "service", "alley", "steps", "corridor", "cycleway", "other")],
    (float("nan"), "footway", 0, routing.LIT_UNKNOWN),
])
def test_lit_inference_table(lit, highway, flags, expected):
    assert routing.infer_lit(lit, C[highway], flags) == expected


def test_class_multipliers_and_flag_factors():
    assert set(routing.CLASS_MULTIPLIER) == set(routing.CLASS_NAMES)
    assert all(v > 0 for v in routing.CLASS_MULTIPLIER.values())
    names = ["primary", "secondary", "tertiary", "trunk", "pedestrian", "residential", "service", "alley",
             "footway", "path", "cycleway", "steps", "bridleway", "track"]
    mult = routing.edge_multipliers(np.array([C[n] for n in names], dtype=np.uint8), np.zeros(len(names), dtype=np.uint8))
    assert mult == pytest.approx([0.85, 0.85, 0.85, 0.95, 0.90, 1.0, 1.2, 1.5, 1.3, 1.3, 1.3, 1.3, 1.6, 1.6])

    F = routing
    cases = [
        (C["footway"], F.FLAG_SIDEWALK, 1.0),
        (C["footway"], F.FLAG_IN_PARK, 1.3 * 1.5),
        (C["footway"], F.FLAG_UNDERPASS, 1.3 * 1.5),
        # an underpass tagged covered as well: the larger factor once
        (C["footway"], F.FLAG_UNDERPASS | F.FLAG_COVERED, 1.3 * 1.5),
        (C["footway"], F.FLAG_COVERED, 1.3 * 1.15),
        (C["corridor"], F.FLAG_INDOOR, 1.3 * 1.5),
        (C["path"], F.FLAG_IN_PARK | F.FLAG_UNDERPASS, 1.3 * 1.5 * 1.5),
        (200, 0, 1.0),  # a class index this code does not know
    ]
    got = routing.edge_multipliers(np.array([c for c, _, _ in cases], dtype=np.uint8),
                                   np.array([f for _, f, _ in cases], dtype=np.uint8))
    assert got == pytest.approx([m for _, _, m in cases])


def line_graph(tmp_path, segments, name="line.npz"):
    """Graph from [(u, v, [points], lit, class name, flags, street name)]; node i is
    the first point seen for it."""
    nodes: dict[int, list[float]] = {}
    edges = []
    for u, v, pts, lit, cls, flags, street in segments:
        nodes.setdefault(u, pts[0])
        nodes.setdefault(v, pts[-1])
        edges.append((u, v, pts, routing.polyline_length_m(pts), lit, C[cls], flags, street))
    order = sorted(nodes)
    assert order == list(range(len(order)))
    path = tmp_path / name
    routing.save_graph(path, routing.build_arrays([nodes[i][0] for i in order], [nodes[i][1] for i in order], edges, {}))
    return routing.load_graph(path)


def pt(east_m: float, north_m: float) -> list[float]:
    return [LNG0 + east_m / routing._M_PER_DEG_LNG, LAT0 + north_m / routing._M_PER_DEG_LAT]


def test_costs_positive_for_every_class_flag_and_lit_value(tmp_path):
    segments = []
    k = 0
    for cls in routing.CLASS_NAMES:
        for flags in (0, 1, 2, 4, 8, 16, 31):
            for lit in (0, 1, 2):
                segments.append((k, k + 1, [pt(k * 10, 0), pt(k * 10 + 10, 0)], lit, cls, flags, None))
                k += 1
    graph = line_graph(tmp_path, segments)
    risk = np.random.default_rng(1).random(2 * graph.n_undirected)
    for alpha, gamma in ((0.0, 0.0), (10.0, 0.3), (10.0, 5.0)):
        costs = routing.edge_costs(graph, risk, alpha, gamma=gamma)
        assert np.all(costs > 0) and np.all(np.isfinite(costs))
    with pytest.raises(ValueError):
        routing.edge_costs(graph, risk, 1.0, gamma=-0.1)


def test_safe_route_keeps_to_main_road_without_any_risk(tmp_path):
    # A to B: 400 m straight along an unlit towpath through a park, or 520 m
    # round three sides on a primary road
    a, b, c, d = pt(0, 0), pt(400, 0), pt(0, 60), pt(400, 60)
    graph = line_graph(tmp_path, [
        (0, 1, [a, b], routing.LIT_NO, "footway", routing.FLAG_IN_PARK, "Canal towpath"),
        (0, 2, [a, c], routing.LIT_YES, "primary", 0, "High Road"),
        (2, 3, [c, d], routing.LIT_YES, "primary", 0, "High Road"),
        (3, 1, [d, b], routing.LIT_YES, "primary", 0, "High Road"),
    ])
    out = routing.route(graph, a, b, {}, alpha=4.0)
    fast, safe = out["fast"], out["safe"]
    assert fast["length_m"] == pytest.approx(400, abs=1) and safe["length_m"] == pytest.approx(520, abs=1)
    assert (fast["park_m"], fast["main_road_share"], fast["lit_share"]) == (pytest.approx(400, abs=1), 0, 0)
    assert (safe["park_m"], safe["main_road_share"], safe["lit_share"]) == (0, 1, 1)
    assert out["risk_reduction"] == 0 and fast["path_risk"] == safe["path_risk"] == 0
    # alpha 0 is "shortest": the class and lit factors are not applied
    assert routing.route(graph, a, b, {}, alpha=0.0)["safe"]["length_m"] == fast["length_m"]


def test_old_format_graph_loads_and_costs_as_before():
    graph = routing.load_graph(FIXTURE_GRAPH)
    assert graph.format == 1 and set(np.unique(graph.edge_lit)) <= {0, 1}
    assert np.all(graph.edge_class == C["residential"]) and not graph.edge_flags.any()
    assert not graph.edge_name.any() and graph.names == [""]
    risk = routing.edge_risk(graph, RISKY)
    expected = graph.edge_length * (1 + 4.0 * risk) * (1 - 0.15 * graph.edge_lit)
    assert np.allclose(routing.edge_costs(graph, risk, 4.0, 0.15), expected)
    out = routing.route(graph, lnglat(0, 0), lnglat(0, 5), RISKY, alpha=4.0)
    assert out["safe"]["length_m"] > out["fast"]["length_m"] and out["beta"] == 0.15 and out["gamma"] == 0.0
    # no names in the file: one step, without a street
    assert [s["street"] for s in out["fast"]["steps"]] == [None, None]
    assert out["fast"]["steps"][0]["instruction"] == "Head east on an unnamed road"


# --- night multiplier, path risk ---

def london(hour: int, minute: int = 0, second: int = 0) -> datetime:
    return datetime(2026, 1, 15, hour, minute, second, tzinfo=routing.LONDON_TZ)


def test_night_multiplier_values_and_continuity():
    m = routing.night_multiplier
    assert m(london(12)) == 1.0 and m(london(6)) == pytest.approx(1.0) and m(london(18)) == pytest.approx(1.0)
    assert m(london(22, 30)) == pytest.approx(1 + 0.3 * np.sin(np.pi / 4))  # 1.2121, half way up
    assert m(london(3)) == pytest.approx(1.3)
    assert m(london(4, 30)) == pytest.approx(1 + 0.3 * np.cos(np.pi / 4))
    assert m(london(0)) == pytest.approx(1 + 0.3 * np.sin(np.pi / 2 * 6 / 9))
    # continuous: no step larger than the slope allows anywhere in the day, midnight included
    start = london(0)
    values = [m(start + timedelta(seconds=30 * i)) for i in range(2 * 60 * 24 + 1)]
    assert max(abs(np.diff(values))) < 0.002 and min(values) == 1.0 and max(values) == pytest.approx(1.3)
    assert values[0] == pytest.approx(values[-1])
    # London local time: 02:00 UTC in July is 03:00 BST; a naive time is read as local
    assert m(datetime(2026, 7, 15, 2, 0, tzinfo=timezone.utc)) == pytest.approx(1.3)
    assert m(datetime(2026, 7, 15, 3, 0)) == pytest.approx(1.3)


def test_night_multiplier_scales_the_baseline_only_and_risk_stays_in_range():
    live = np.array([0.0, 0.5, 1.0, 0.0]); base = np.array([1.0, 0.5, 0.2, 0.0])
    assert routing.combined_risk(live, base, 1.3) == pytest.approx([0.52, 1 - 0.5 * (1 - 0.26), 1.0, 0.0])
    assert routing.combined_risk(live, None, 1.3) is live
    wild = routing.combined_risk(np.array([0.0, 1.2]), np.array([5.0, 1.0]), 1.3)
    assert wild.min() >= 0 and wild.max() <= 1


def test_path_risk_calibration():
    assert routing.path_risk(np.array([0.5]), np.array([1000.0])) == pytest.approx(0.5)
    assert routing.path_risk(np.array([0.5, 0.5]), np.array([400.0, 600.0])) == pytest.approx(0.5)
    assert routing.path_risk(np.array([0.0]), np.array([5000.0])) == 0
    assert routing.path_risk(np.array([1.0]), np.array([1000.0])) == pytest.approx(0.75)
    assert routing.path_risk(np.array([1.0]), np.array([1e6])) <= 1


def test_risk_reduction_is_never_negative(tmp_path):
    # the lit main road carries more risk than the unlit park path, and still costs less
    a, b, c = pt(0, 0), pt(300, 0), pt(150, 20)
    graph = line_graph(tmp_path, [
        (0, 1, [a, b], routing.LIT_NO, "track", routing.FLAG_IN_PARK, None),
        (0, 2, [a, c], routing.LIT_YES, "primary", 0, "High Road"),
        (2, 1, [c, b], routing.LIT_YES, "primary", 0, "High Road"),
    ])
    m = graph.n_undirected
    base = np.tile(np.array([0.0, 0.5, 0.5], dtype=np.float32), 2)
    out = routing.route(graph, a, b, {}, alpha=1.0, baseline=base)
    assert out["safe"]["mean_risk"] > out["fast"]["mean_risk"] == 0
    assert out["safe"]["max_risk"] > out["fast"]["max_risk"]
    assert out["risk_reduction"] == 0


# --- steps ---

def test_turn_classification_from_bearings():
    t = routing.turn_instruction
    assert t(0, 10) == "Continue" and t(0, 350) == "Continue"
    assert t(350, 10) == "Continue" and t(10, 350) == "Continue"  # wrap-around at 360
    assert t(350, 20) == "Bear right" and t(20, 350) == "Bear left"
    assert t(0, 25) == "Bear right" and t(0, 59) == "Bear right" and t(0, 60) == "Turn right"
    assert t(90, 0) == "Turn left" and t(0, 90) == "Turn right"
    assert t(300, 30) == "Turn right" and t(30, 300) == "Turn left"
    assert t(0, 150) == "Turn right" and t(0, 210) == "Turn left"
    assert t(0, 151) == "Turn around" and t(0, 180) == "Turn around" and t(270, 95) == "Turn around"
    assert routing.bearing_deg([0, 0], [0, 1]) == 0 and routing.bearing_deg([0, 0], [1, 0]) == 90
    assert routing.bearing_deg([0, 0], [-1, 0]) == 270 and routing.bearing_deg([0, 0], [0, -1]) == 180


def test_merge_steps_by_label_and_short_steps():
    def g(label, length, edge):
        return {"label": label, "edges": [edge], "length": length}

    merged = routing.merge_steps([g("A", 50, 0), g("A", 70, 1), g("B", 30, 2)])
    assert [(s["label"], s["length"], s["edges"]) for s in merged] == [("A", 120, [0, 1]), ("B", 30, [2])]
    # a 10 m crossing between two parts of one street disappears; the street is one step
    merged = routing.merge_steps([g("A", 100, 0), g("x", 10, 1), g("A", 80, 2), g("B", 40, 3)])
    assert [(s["label"], s["length"], s["edges"]) for s in merged] == [("A", 190, [0, 1, 2]), ("B", 40, [3])]
    # a short first step joins the next one; a short last step joins the previous one
    merged = routing.merge_steps([g("x", 5, 0), g("A", 100, 1), g("y", 14.9, 2)])
    assert [(s["label"], s["length"], s["edges"]) for s in merged] == [("A", 119.9, [0, 1, 2])]
    # exactly 15 m is kept; a route of short pieces only still has one step
    assert len(routing.merge_steps([g("A", 100, 0), g("B", 15, 1)])) == 2
    assert [s["edges"] for s in routing.merge_steps([g("A", 5, 0), g("B", 5, 1), g("C", 5, 2)])] == [[0, 1, 2]]


def test_route_steps(tmp_path):
    # east 200 m on High Road (two edges), a 10 m unnamed crossing, north 100 m on
    # Mill Lane, then 50 m of unnamed park path to the north-west
    p0, p1, p2, p3, p4, p5 = pt(0, 0), pt(120, 0), pt(200, 0), pt(200, 10), pt(200, 110), pt(165, 145)
    graph = line_graph(tmp_path, [
        (0, 1, [p0, p1], routing.LIT_YES, "primary", 0, "High Road"),
        (2, 1, [p2, p1], routing.LIT_YES, "primary", 0, "High Road"),  # stored against the direction of travel
        (2, 3, [p2, p3], routing.LIT_YES, "footway", routing.FLAG_SIDEWALK, None),
        (3, 4, [p3, p4], routing.LIT_UNKNOWN, "residential", 0, "Mill Lane"),
        (4, 5, [p4, p5], routing.LIT_NO, "path", routing.FLAG_IN_PARK, ""),
    ])
    base = np.tile(np.array([0.5, 0.25, 0.0, 0.0, 1.0], dtype=np.float32), 2)
    steps = routing.route(graph, p0, p5, {}, alpha=0.0, baseline=base)["fast"]["steps"]
    assert [s["instruction"] for s in steps] == [
        "Head east on High Road", "Turn left onto Mill Lane", "Bear left onto a path through the park",
        "Arrive at destination"]
    assert [s["street"] for s in steps] == ["High Road", "Mill Lane", None, None]
    assert [s["distance_m"] for s in steps] == pytest.approx([210, 100, 49.5, 0], abs=0.2)
    assert steps[0]["duration_s"] == pytest.approx(210 / 1.35, abs=0.2)
    assert [s["lit"] for s in steps] == [True, False, False, False]
    # length-weighted mean of 0.4 * baseline: 120 m at 0.2, 80 m at 0.1, 10 m at 0
    assert steps[0]["risk"] == pytest.approx((120 * 0.2 + 80 * 0.1) / 210, abs=1e-3)
    assert steps[2]["risk"] == pytest.approx(0.4) and steps[3]["risk"] == 0
    assert steps[0]["start"] == pytest.approx(p0, abs=1e-5) and steps[1]["start"] == pytest.approx(p3, abs=1e-5)
    assert steps[3]["start"] == pytest.approx(p5, abs=1e-5)
    assert sum(s["distance_m"] for s in steps) == pytest.approx(360, abs=1)


def test_unnamed_labels():
    F = routing
    assert F.unnamed_label(C["footway"], F.FLAG_SIDEWALK) == "pavement"
    assert F.unnamed_label(C["footway"], 0) == "footpath"
    assert F.unnamed_label(C["footway"], F.FLAG_IN_PARK) == "path through park"
    assert F.unnamed_label(C["footway"], F.FLAG_UNDERPASS | F.FLAG_IN_PARK) == "underpass"
    assert F.unnamed_label(C["steps"], 0) == "steps"
    assert F.unnamed_label(C["residential"], 0) == "unnamed road"
    assert set(F.UNNAMED_PHRASE) >= {F.unnamed_label(c, f) for c in range(len(F.CLASS_NAMES)) for f in range(32)}


# --- API schema ---

LEG_FIELDS = {"geometry", "length_m", "duration_min", "mean_risk", "max_risk", "path_risk", "lit_share",
              "main_road_share", "park_m", "underpass_m", "steps"}
STEP_FIELDS = {"instruction", "street", "distance_m", "duration_s", "lit", "risk", "start"}


def test_api_response_schema_and_depart_at(client, tmp_path, monkeypatch):
    path = tmp_path / "v2.npz"
    routing.save_graph(path, grid_arrays())
    monkeypatch.setenv("GRAPH_PATH", str(path))
    monkeypatch.setattr(api_route, "_baseline", None)
    SqliteRepo().save_crime_points("2026-07", {"month": "2026-07", "rows": [crime_row(0, 2.5, 40.0)]})
    body = {"origin": lnglat(0, 0), "destination": lnglat(0, 5), "alpha": 4}

    night = client.post("/api/route", json=body | {"depart_at": "2026-01-15T03:00:00+00:00"}).json()
    assert set(night) == {"fast", "safe", "alpha", "beta", "gamma", "night_multiplier", "risk_reduction",
                          "extra_distance_m", "attribution"}
    assert night["night_multiplier"] == pytest.approx(1.3) and night["gamma"] == 0.3
    for leg in (night["fast"], night["safe"]):
        assert set(leg) == LEG_FIELDS
        assert 0 <= leg["path_risk"] < 1 and 0 <= leg["lit_share"] <= 1 and 0 <= leg["main_road_share"] <= 1
        assert leg["steps"][-1]["instruction"] == "Arrive at destination"
        for step in leg["steps"]:
            assert set(step) == STEP_FIELDS and isinstance(step["lit"], bool) and len(step["start"]) == 2
    assert night["risk_reduction"] >= 0

    day = client.post("/api/route", json=body | {"depart_at": "2026-01-15T12:00:00"}).json()
    assert day["night_multiplier"] == 1.0
    assert day["fast"]["max_risk"] < night["fast"]["max_risk"] <= 1
    # without depart_at the current time is used
    assert 1.0 <= client.post("/api/route", json=body).json()["night_multiplier"] <= 1.3
    assert client.post("/api/route", json=body | {"depart_at": "tonight"}).status_code == 422
