from backend.sources import police_uk as pk


def crime(id, category, lat, lng, street="On or near High Street"):
    return {
        "id": id,
        "category": category,
        "location": {"latitude": str(lat), "longitude": str(lng), "street": {"id": 1, "name": street}},
    }


CRIMES = [
    crime(1, "violent-crime", 51.5136, -0.1365),
    crime(2, "shoplifting", 51.5136, -0.1365),
    crime(3, "robbery", 51.5136, -0.1365),
    crime(4, "public-order", 51.6000, 0.1000, "On or near Park Road"),
    crime(5, "violent-crime", 53.4800, -2.2400, "On or near Manchester"),  # outside London
    crime(6, "burglary", 51.5200, -0.1000, "On or near Side Street"),  # no relevant category
    crime(7, "robbery", 51.5300, -0.1100, "On or near Police Station"),
    crime(8, "violent-crime", 51.5400, -0.1200, "On or near HOSPITAL"),
]


def test_tiles_cover_bbox_without_gaps():
    tiles = pk._tiles(pk.LONDON_BBOX)
    w, s, e, n = pk.LONDON_BBOX
    assert min(t[0] for t in tiles) == w and min(t[1] for t in tiles) == s
    assert max(t[2] for t in tiles) == e and max(t[3] for t in tiles) == n
    assert all(t[2] - t[0] <= pk.TILE_LNG + 1e-9 and t[3] - t[1] <= pk.TILE_LAT + 1e-9 for t in tiles)


def test_aggregate_points_groups_by_location_and_drops_outside_london():
    points = {p.street: p for p in pk.aggregate_points(CRIMES)}
    assert set(points) == {"On or near High Street", "On or near Park Road"}
    high = points["On or near High Street"]
    assert high.count == 2  # shoplifting is not counted
    assert high.weighted == 0.0  # set by mps_lsoa.distribute
    assert dict(high.categories) == {"violent-crime": 1, "robbery": 1}


def test_is_venue_matches_any_case():
    assert pk.is_venue("On or near Further/Higher Educational Building")
    assert pk.is_venue("On or near supermarket")
    assert pk.is_venue("On or near London Heathrow Airport")
    assert not pk.is_venue("On or near Hospital Road")


def weighted_points():
    points = pk.aggregate_points(CRIMES)
    for p in points:
        p.weighted = float(p.count)
    return points


def test_baseline_cells_normalised():
    cells = pk.baseline_cells(weighted_points(), months=1)
    assert len(cells) == 2
    assert max(cells.values()) == 1.0 and all(0 < v <= 1 for v in cells.values())
    assert pk.baseline_cells([], months=1) == {}


def test_points_payload_rows_match_columns():
    payload = pk.points_payload(weighted_points(), "2026-07", {"period": "2025-09..2026-08", "month": "ignored"})
    assert payload["month"] == "2026-07" and payload["period"] == "2025-09..2026-08"
    assert all(len(r) == len(payload["columns"]) for r in payload["rows"])
    # sorted ascending by weight so the highest points draw last (on top)
    assert payload["rows"][-1][4] == "On or near High Street"
