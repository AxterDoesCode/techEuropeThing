import json
from pathlib import Path
from types import SimpleNamespace

import httpx
import numpy as np
import pytest
from shapely.geometry import box, mapping

from backend.sources import mps_lsoa as ml
from backend.sources import police_uk as pk

CSV = Path(__file__).parent / "fixtures" / "mps_lsoa.csv"

# Three squares of 0.01 degrees side by side (0.771 km2 each). The graph bbox
# covers A and B; C is outside it.
POLYGONS = {
    "E01000001": box(-0.14, 51.50, -0.13, 51.51),
    "E01000002": box(-0.13, 51.50, -0.12, 51.51),
    "E01000003": box(-0.12, 51.50, -0.11, 51.51),
}
A, B, C = "E01000001", "E01000002", "E01000003"
AREA_KM2 = 0.01 * ml._M_PER_DEG_LNG * 0.01 * ml._M_PER_DEG_LAT / 1e6


def graph(lengths_m=(2000.0, 4000.0, 3000.0), bbox=(-0.14, 51.50, -0.12, 51.51)):
    """One undirected edge inside each square, stored in both directions."""
    lng = np.array([-0.139, -0.131, -0.129, -0.121, -0.119, -0.111], dtype=np.float32)
    lat = np.full(6, 51.505, dtype=np.float32)
    src, dst = np.array([0, 2, 4], dtype=np.int32), np.array([1, 3, 5], dtype=np.int32)
    length = np.array(lengths_m, dtype=np.float32)
    return SimpleNamespace(
        node_lng=lng, node_lat=lat,
        edge_src=np.concatenate([src, dst]), edge_dst=np.concatenate([dst, src]),
        edge_length=np.concatenate([length, length]), bbox=bbox,
    )


def crime(id, category, lat, lng, street="On or near High Street"):
    return {
        "id": id,
        "category": category,
        "location": {"latitude": str(lat), "longitude": str(lng), "street": {"id": 1, "name": street}},
    }


CRIMES = [
    # A: two street points (3 and 1 relevant records), a hospital, a supermarket
    crime(1, "robbery", 51.502, -0.138),
    crime(2, "violent-crime", 51.502, -0.138),
    crime(3, "public-order", 51.502, -0.138),
    crime(4, "shoplifting", 51.502, -0.138),
    crime(5, "theft-from-the-person", 51.508, -0.132, "On or near Low Street"),
    crime(6, "violent-crime", 51.505, -0.135, "On or near Hospital"),
    crime(7, "violent-crime", 51.505, -0.135, "On or near Hospital"),
    crime(8, "robbery", 51.506, -0.136, "On or near Supermarket"),
    # B: one street point
    crime(9, "possession-of-weapons", 51.505, -0.125, "On or near Mid Street"),
    # C: nothing relevant
    crime(10, "burglary", 51.505, -0.115, "On or near Far Street"),
    # inside the London bbox, outside every LSOA
    crime(11, "robbery", 51.60, 0.10, "On or near Park Road"),
]


def lsoas():
    period, counts = ml.parse_counts(CSV.open(encoding="utf-8"))
    return period, ml.make_lsoas(counts, POLYGONS)


def test_parse_counts_sums_last_12_months_of_relevant_offences():
    period, counts = ml.parse_counts(CSV.open(encoding="utf-8"))
    assert period == "2025-09..2026-08"
    assert counts[A] == ("Testham 001A", 12 * 1.0 + 10 * 0.7, 22)  # shoplifting not counted
    assert counts[B] == ("Testham 001B", 24 * 1.0 + 4 * 0.5, 28)  # business robbery not counted
    assert counts[C] == ("Testham 001C", 6.0, 6)
    assert counts["E01000004"] == ("Testham 001D", 0.0, 0)


def test_parse_counts_accepts_a_byte_order_mark_and_rejects_a_short_file():
    text = "﻿" + CSV.read_text(encoding="utf-8")
    assert ml.parse_counts(text.splitlines())[1][C][1] == 6.0
    with pytest.raises(ValueError):
        ml.parse_counts(["LSOA Code,LSOA Name,Borough,Group,SubGroup,202608"])


def test_make_lsoas_joins_on_code_and_measures_area():
    _, areas = lsoas()
    assert set(areas) == {A, B, C}  # 001D has no polygon
    assert areas[A].area_km2 == pytest.approx(AREA_KM2, rel=1e-6)


def test_street_km_assigns_each_undirected_edge_once():
    _, areas = lsoas()
    assert ml.street_km(areas, graph()) == pytest.approx({A: 2.0, B: 4.0, C: 3.0})
    assert ml.street_km(areas, None) == {}


def test_normalise_per_street_km_with_area_fallback_outside_the_graph():
    _, areas = lsoas()
    d_med = ml.normalise(areas, graph())
    assert d_med == pytest.approx(3.0 / AREA_KM2)  # median of 2 km and 4 km per square
    a, b, c = areas[A], areas[B], areas[C]
    assert (a.from_graph, b.from_graph, c.from_graph) == (True, True, False)
    assert a.rate_per_km == pytest.approx(19.0 / 12 / 2.0)
    assert b.rate_per_km == pytest.approx(26.0 / 12 / 4.0)
    # S = rate x D_med x area
    assert a.point_weight == pytest.approx(19.0 / 12 * 3.0 / 2.0)
    assert b.point_weight == pytest.approx(26.0 / 12 * 3.0 / 4.0)
    # C is outside the graph bbox: L = area x D_med, so S is the monthly count
    assert c.street_km == pytest.approx(3.0)
    assert c.rate_per_km == pytest.approx(6.0 / 12 / 3.0)
    assert c.point_weight == pytest.approx(6.0 / 12)


def test_normalise_treats_sparse_graph_coverage_as_missing():
    _, areas = lsoas()
    wide = (-0.14, 51.50, -0.11, 51.51)
    ml.normalise(areas, graph(lengths_m=(2000.0, 4000.0, 400.0), bbox=wide))
    assert not areas[C].from_graph  # under MIN_STREET_KM
    assert areas[C].point_weight == pytest.approx(6.0 / 12)
    ml.normalise(areas, graph(lengths_m=(8000.0, 8000.0, 1000.0), bbox=wide))
    assert not areas[C].from_graph  # 1 km is under MIN_DENSITY_RATIO x the median of 8 km


def test_normalise_without_a_graph_uses_monthly_counts():
    _, areas = lsoas()
    assert ml.normalise(areas, None) is None
    assert [a.point_weight for a in areas.values()] == pytest.approx([19.0 / 12, 26.0 / 12, 6.0 / 12])
    assert all(a.rate_per_km == 0.0 and not a.from_graph for a in areas.values())


def test_combine_distributes_each_lsoa_over_its_street_points():
    period, areas = lsoas()
    result = ml.combine(period, areas, "2026-07", CRIMES, graph())
    by_street: dict[str, list[pk.CrimePoint]] = {}
    for p in result.points:
        by_street.setdefault(p.street, []).append(p)
    # venue points and the point outside every LSOA are gone
    assert set(by_street) == {"On or near High Street", "On or near Low Street", "On or near Mid Street", "LSOA Testham 001C"}

    # A: 2 of its 7 relevant records are at a hospital: S x (1 - 2 / (7 + k))
    s_a = 19.0 / 12 * 3.0 / 2.0 * (1 - 2 / 8)
    (high,), (low,) = by_street["On or near High Street"], by_street["On or near Low Street"]
    assert high.count == 3 and low.count == 1  # shoplifting is not counted
    assert high.weighted == pytest.approx(s_a * (3 + 1) / (4 + 2))
    assert low.weighted == pytest.approx(s_a * (1 + 1) / (4 + 2))
    assert dict(high.categories) == {"robbery": 1, "violent-crime": 1, "public-order": 1}

    (mid,) = by_street["On or near Mid Street"]
    assert mid.weighted == pytest.approx(26.0 / 12 * 3.0 / 4.0)

    # C has no street point: equal rows on a grid inside the polygon
    fill = by_street["LSOA Testham 001C"]
    assert len(fill) > 1 and all(p.count == 0 and not p.categories for p in fill)
    assert all(POLYGONS[C].contains(box(p.lng, p.lat, p.lng, p.lat)) for p in fill)
    assert sum(p.weighted for p in fill) == pytest.approx(6.0 / 12)
    assert result.stats["rows_without_street_point"] == len(fill)


def test_distribute_skips_an_empty_lsoa_without_weight_and_handles_a_tiny_polygon():
    tiny = ml.Lsoa("X", "Tiny 001A", box(-0.10000, 51.50000, -0.09999, 51.50001), point_weight=2.0)
    zero = ml.Lsoa("Y", "Zero 001A", POLYGONS[C], point_weight=0.0)
    (row,) = ml.distribute({"X": tiny, "Y": zero}, [])
    assert row.street == "LSOA Tiny 001A" and row.weighted == 2.0
    assert (row.lng, row.lat) == pytest.approx((-0.099995, 51.500005), abs=1e-5)


def test_city_of_london_takes_its_counts_from_police_uk():
    city = ml.Lsoa("E01000005", "City of London 001E", POLYGONS[A], weighted_12m=1.4, count_12m=2)
    other = ml.Lsoa(B, "Testham 001B", POLYGONS[B], weighted_12m=26.0, count_12m=28)
    assert ml.add_police_uk_counts({"E01000005": city, B: other}, CRIMES) == 1
    # A holds 2 robbery, 3 violent-crime, 1 public-order, 1 theft-from-the-person (venues included)
    assert city.weighted_12m == pytest.approx(12 * (2 * 1.0 + 3 * 0.25 + 0.4 + 0.7))
    assert city.count_12m == 12 * 7
    assert other.weighted_12m == 26.0


def test_payload_format_and_cells():
    period, areas = lsoas()
    result = ml.combine(period, areas, "2026-07", CRIMES, graph())
    payload = result.payload()
    assert payload["month"] == "2026-07" and payload["period"] == "2025-09..2026-08"
    assert "2026-07" in payload["method"]
    assert payload["columns"] == ["lng", "lat", "count", "weighted", "street", "top_categories"]
    for lng, lat, count, weighted, street, top in payload["rows"]:
        assert isinstance(lng, float) and isinstance(lat, float) and isinstance(count, int)
        assert isinstance(weighted, float) and isinstance(street, str) and isinstance(top, dict)
    weights = [r[3] for r in payload["rows"]]
    assert weights == sorted(weights)
    json.dumps(payload)

    cells = result.cells(9)
    assert cells and max(cells.values()) == 1.0 and all(0.0 <= v <= 1.0 for v in cells.values())


def test_resolve_csv_url_picks_the_newest_lsoa_file():
    resources = {
        "a": {"title": "MPS LSOA Level Crime.csv", "format": "csv", "temporal_coverage_to": "2026-07", "url": "old"},
        "b": {"title": "MPS LSOA Level Crime.csv", "format": "csv", "temporal_coverage_to": "2026-08", "url": "new"},
        "c": {"title": "MPS LSOA Level Crime (Historical).csv", "format": "csv", "temporal_coverage_to": "2026-09", "url": "hist"},
        "d": {"title": "MPS Ward Level Crime.csv", "format": "csv", "temporal_coverage_to": "2026-09", "url": "ward"},
    }
    transport = httpx.MockTransport(lambda req: httpx.Response(200, json={"resources": resources}))
    with httpx.Client(transport=transport) as client:
        assert ml.resolve_csv_url(client) == "new"


def test_fetch_boundaries_pages_and_filters_by_code():
    feature = lambda code: {"type": "Feature", "properties": {"LSOA21CD": code}, "geometry": mapping(POLYGONS[A])}
    pages = {
        "0": {"features": [feature(A), feature("E01999999")], "properties": {"exceededTransferLimit": True}},
        "2": {"features": [feature(B)]},
    }
    transport = httpx.MockTransport(lambda req: httpx.Response(200, json=pages[req.url.params["resultOffset"]]))
    with httpx.Client(transport=transport) as client:
        assert set(ml.fetch_boundaries(client, {A, B, C})) == {A, B}
