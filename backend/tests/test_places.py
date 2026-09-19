import h3
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend import api_platform, db, places
from backend.db import SqliteRepo
from backend.models import CellScore, utcnow
from backend.scoring import FINE_RES

# Shape of an Overpass `out center tags` response; values invented
OVERPASS = {
    "elements": [
        {"type": "node", "id": 1, "lat": 51.5136, "lon": -0.1365,
         "tags": {"tourism": "hotel", "name": "Soho Test Hotel", "stars": "4", "addr:street": "Dean Street",
                  "addr:housenumber": "10", "website": "https://example.org"}},
        {"type": "way", "id": 2, "center": {"lat": 51.5300, "lon": -0.1240},
         "tags": {"tourism": "hostel", "name": "Kings Cross Test Hostel"}},
        {"type": "node", "id": 3, "lat": 51.5136, "lon": -0.1365, "tags": {"tourism": "hotel"}},  # no name
        {"type": "node", "id": 4, "lat": 53.48, "lon": -2.24, "tags": {"tourism": "hotel", "name": "Manchester"}},
    ]
}
STATIONS = {"elements": [{"type": "node", "id": 9, "lat": 51.5154, "lon": -0.1301,
                          "tags": {"railway": "station", "name": "Tottenham Court Road", "network": "London Underground"}}]}


def test_parse_keeps_named_places_in_london():
    got = places.parse(OVERPASS, "hotel")
    assert [p["id"] for p in got] == ["osm:node/1", "osm:way/2"]
    assert got[0]["details"] == {"subtype": "hotel", "stars": "4", "website": "https://example.org",
                                 "address": "10 Dean Street"}
    assert got[1]["lat"] == 51.53 and got[1]["details"] == {"subtype": "hostel"}


def test_query_covers_the_london_bbox():
    q = places.overpass_query("hotel")
    assert "51.2868,-0.5104,51.6919,0.334" in q and "out center tags" in q
    assert q.count("51.2868,-0.5104,51.6919,0.334") == 6 and 'node["tourism"="hostel"]["name"]' in q


@pytest.fixture
def client(tmp_path, monkeypatch):
    db.connect(tmp_path / "risk.sqlite")
    monkeypatch.setattr(api_platform, "_cache", {})
    return TestClient(_app())


def _app():
    app = FastAPI()
    app.include_router(api_platform.router)
    return app


def test_hotels_endpoint(client):
    params = {"lat": 51.5136, "lng": -0.1365, "radius_m": 3000}
    assert client.get("/api/hotels", params=params).status_code == 503

    assert places.replace("hotel", places.parse(OVERPASS, "hotel")) == 2
    places.replace("station", places.parse(STATIONS, "station"))
    risky = h3.latlng_to_cell(51.5136, -0.1365, FINE_RES)
    SqliteRepo().replace_cell_scores([CellScore(h3=risky, res=FINE_RES, live=0.0, baseline=1.0, score=0.4)], utcnow())

    body = client.get("/api/hotels", params=params).json()
    assert body["total"] == 2
    # sorted by modelled risk of the surroundings: the hostel's area has no scored cell
    assert [h["name"] for h in body["hotels"]] == ["Kings Cross Test Hostel", "Soho Test Hotel"]
    soho = body["hotels"][1]
    assert soho["risk"]["max_score"] == 0.4 and soho["distance_m"] == 0
    assert soho["nearest_station"]["name"] == "Tottenham Court Road"
    assert 400 < soho["nearest_station"]["distance_m"] < 600
    by_distance = client.get("/api/hotels", params=params | {"sort": "distance"}).json()
    assert by_distance["hotels"][0]["name"] == "Soho Test Hotel"
    assert client.get("/api/hotels", params=params | {"radius_m": 500}).json()["total"] == 1

    # replace() swaps the whole kind
    assert places.replace("hotel", []) == 0 and places.count("hotel") == 0
