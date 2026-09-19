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
    crime(4, "burglary", 51.6000, 0.1000, "On or near Park Road"),
    crime(5, "violent-crime", 53.4800, -2.2400, "On or near Manchester"),  # outside London
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
    assert high.count == 3
    assert high.weighted == 1.0 + 0.2 + 1.0
    assert high.categories["violent-crime"] == 1


def test_baseline_cells_normalised():
    cells = pk.baseline_cells(pk.aggregate_points(CRIMES), months=1)
    assert len(cells) == 2
    assert max(cells.values()) == 1.0 and all(0 < v <= 1 for v in cells.values())
    assert pk.baseline_cells([], months=1) == {}


def test_points_payload_rows_match_columns():
    payload = pk.points_payload(pk.aggregate_points(CRIMES), "2026-07")
    assert payload["month"] == "2026-07"
    assert all(len(r) == len(payload["columns"]) for r in payload["rows"])
    # sorted ascending by weight so the highest points draw last (on top)
    assert payload["rows"][-1][4] == "On or near High Street"
