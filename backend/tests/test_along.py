"""events_along with synthetic lines and events. The route runs east along lat 51.5."""

import pytest

from backend.along import circle_bbox, events_along, padded_bbox
from backend.scoring import _M_PER_DEG_LAT, _M_PER_DEG_LNG

LAT, LNG = 51.5, -0.1


def at(east_m: float, north_m: float) -> list[float]:
    return [LNG + east_m / _M_PER_DEG_LNG, LAT + north_m / _M_PER_DEG_LAT]


ROUTE = {"type": "LineString", "coordinates": [at(0, 0), at(1000, 0)]}


def point(id_: str, east_m: float, north_m: float, radius_m: float) -> dict:
    return {"type": "Feature", "id": id_, "geometry": {"type": "Point", "coordinates": at(east_m, north_m)},
            "properties": {"radius_m": radius_m, "title": id_}}


def test_point_within_radius_plus_buffer_is_included():
    [hit] = events_along(ROUTE, [point("a", 400, 150, 100)])
    assert hit["feature"]["id"] == "a"
    assert hit["distance_m"] == pytest.approx(150, abs=0.5)
    assert hit["along_m"] == pytest.approx(400, abs=0.5)


def test_point_outside_is_excluded_and_buffer_is_a_parameter():
    far = point("far", 400, 200, 100)
    assert events_along(ROUTE, [far]) == []
    assert [r["feature"]["id"] for r in events_along(ROUTE, [far], buffer_m=120)] == ["far"]


def test_polygon_crossing_the_line_has_distance_zero_and_first_contact():
    ring = [at(600, -50), at(700, -50), at(700, 50), at(600, 50), at(600, -50)]
    poly = {"type": "Feature", "id": "p", "geometry": {"type": "Polygon", "coordinates": [ring]},
            "properties": {"radius_m": 5000}}
    [hit] = events_along(ROUTE, [poly])
    assert hit["distance_m"] == 0
    assert hit["along_m"] == pytest.approx(600, abs=0.5)
    # radius_m applies to Point geometries only
    ring_far = [at(600, 100), at(700, 100), at(700, 200), at(600, 100)]
    assert events_along(ROUTE, [poly | {"geometry": {"type": "Polygon", "coordinates": [ring_far]}}]) == []


def test_event_behind_the_start():
    near, far = point("near", -80, 0, 50), point("far", -200, 0, 50)
    [hit] = events_along(ROUTE, [near, far])
    assert hit["feature"]["id"] == "near" and hit["along_m"] == 0 and hit["distance_m"] == pytest.approx(80, abs=0.5)


def test_order_by_along_and_input_not_modified():
    features = [point("c", 900, 10, 20), point("a", 100, -10, 20), point("b", 500, 0, 20)]
    out = events_along(ROUTE, features)
    assert [r["feature"]["id"] for r in out] == ["a", "b", "c"]
    assert out[1]["distance_m"] == 0 and out[1]["along_m"] == pytest.approx(500, abs=0.5)
    assert all("relevance" not in f["properties"] for f in features)


def test_unusable_geometry_is_skipped():
    assert events_along(ROUTE, [{"type": "Feature", "id": "x", "geometry": None, "properties": {}}]) == []


def test_bboxes():
    w, s, e, n = padded_bbox(ROUTE, 100)
    assert (w, e) == pytest.approx((at(-100, 0)[0], at(1100, 0)[0])) and (s, n) == pytest.approx((at(0, -100)[1], at(0, 100)[1]))
    w, s, e, n = circle_bbox(LNG, LAT, 500)
    assert (w, s) == pytest.approx(at(-500, -500)) and (e, n) == pytest.approx(at(500, 500))
