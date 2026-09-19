"""Tests for the LondonAir source.

fixtures/london_air.json has two keys:
- "real": flattened site payloads captured from the live feed on 2026-09-19
  (bulletin 12:00). Every site was in the "Low" band or had no data.
- "synthetic": NOT real measurements. Copies of the real sites BQ7, BT4 and BT5
  with the highest pollutant index raised to 5, 8 and 10 and the band set to
  match, so that the severity mapping has inputs.
"""

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from backend.models import Category, RawItem
from backend.pipeline import run_poll
from backend.sources.london_air import LondonAirSource, _external_id, flatten, severity
from backend.tests.test_pipeline import MemoryRepo

FIXTURE = json.loads((Path(__file__).parent / "fixtures" / "london_air.json").read_text())
REAL: list[dict] = FIXTURE["real"]
SYNTHETIC: list[dict] = FIXTURE["synthetic"]
SYNTHETIC_CODES = {p["site_code"] for p in SYNTHETIC}
# One payload per site: the synthetic variant replaces the real one.
POLL_PAYLOADS = [p for p in REAL if p["site_code"] not in SYNTHETIC_CODES] + SYNTHETIC


class FixtureSource(LondonAirSource):
    def __init__(self, payloads: list[dict]) -> None:
        self.payloads = payloads

    def fetch(self, cursor):
        items = [
            RawItem(source_id=self.id, external_id=_external_id(p), payload=p)
            for p in self.payloads
        ]
        return items, cursor


def _raw(payload: dict) -> RawItem:
    return RawItem(source_id="london_air", external_id=_external_id(payload), payload=payload)


def _site(code: str, species, lat: str | None = "51.5", lon: str | None = "-0.1") -> dict:
    site = {
        "@BulletinDate": "2026-09-19 11:00:00",
        "@SiteCode": code,
        "@SiteName": f"Site {code}",
        "Species": species,
    }
    if lat is not None:
        site["@Latitude"] = lat
    if lon is not None:
        site["@Longitude"] = lon
    return site


def _species(code: str, index: int, band: str) -> dict:
    return {"@SpeciesCode": code, "@AirQualityIndex": str(index), "@AirQualityBand": band}


def test_flatten_handles_dict_and_list_shapes():
    data = {
        "HourlyAirQualityIndex": {
            "LocalAuthority": [
                # Site as a dict, Species as a dict
                {"Site": _site("A1", _species("NO2", 2, "Low"))},
                # Site as a list, Species as a list
                {
                    "Site": [
                        _site("B1", [_species("NO2", 1, "Low"), _species("PM10", 5, "Moderate")]),
                        _site("B2", [_species("O3", 0, "No data")]),
                    ]
                },
                # authority without sites
                {"@LocalAuthorityName": "Empty"},
                # missing or empty coordinates are skipped
                {"Site": [_site("C1", _species("NO2", 7, "High"), lat=None)]},
                {"Site": _site("C2", _species("NO2", 7, "High"), lon="")},
            ]
        }
    }
    payloads = {p["site_code"]: p for p in flatten(data)}
    assert list(payloads) == ["A1", "B1", "B2"]
    assert payloads["A1"] == {
        "site_code": "A1",
        "site_name": "Site A1",
        "lat": 51.5,
        "lon": -0.1,
        "bulletin_date": "2026-09-19 11:00:00",
        "max_index": 2,
        "max_species": "NO2",
        "band": "Low",
        "species": {"NO2": 2},
    }
    b1 = payloads["B1"]
    assert (b1["max_index"], b1["max_species"], b1["band"]) == (5, "PM10", "Moderate")
    assert b1["species"] == {"NO2": 1, "PM10": 5}
    assert payloads["B2"]["max_index"] == 0


def test_flatten_single_authority_as_dict():
    data = {"HourlyAirQualityIndex": {"LocalAuthority": {"Site": _site("A1", [])}}}
    (payload,) = flatten(data)
    assert payload["max_index"] == 0 and payload["max_species"] is None


def test_external_id_includes_bulletin_hour():
    assert _external_id(REAL[0]) == "BG1:2026-09-19T12"


def test_threshold():
    src = LondonAirSource()
    base = SYNTHETIC[0]
    assert src.to_event(_raw(base | {"max_index": 3, "band": "Low"})) is None
    assert src.to_event(_raw(base | {"max_index": 4, "band": "Moderate"})) is not None


def test_real_sites_produce_no_events():
    src = LondonAirSource()
    assert all(src.to_event(_raw(p)) is None for p in REAL)


@pytest.mark.parametrize(
    ("index", "expected"),
    [(4, 0.2), (5, 0.25), (6, 0.3), (7, 0.45), (8, 0.53), (9, 0.61), (10, 0.8)],
)
def test_severity(index, expected):
    assert severity(index) == pytest.approx(expected)


def test_to_event_maps_fields():
    src = LondonAirSource()
    events = {p["site_code"]: src.to_event(_raw(p)) for p in SYNTHETIC}
    assert [e.severity for e in events.values()] == pytest.approx([0.25, 0.53, 0.8])

    high = events["BT4"]
    assert high.external_ref == "london_air:BT4:2026-09-19T12"
    assert high.category == Category.AIR_QUALITY
    assert high.title == "Air quality 8 (High, PM10): Brent - Ikea"
    assert high.geometry == {"type": "Point", "coordinates": [-0.258089, 51.552476]}
    assert high.half_life_min == 60
    assert high.radius_m == 1000
    assert high.confidence == 0.95
    assert high.source_confidence == {"london_air": 0.95}


def test_outside_london_is_skipped():
    payload = SYNTHETIC[0] | {"lat": 52.2, "lon": 0.12}
    assert LondonAirSource().to_event(_raw(payload)) is None


def test_bulletin_date_is_converted_from_london_time_to_utc():
    src = LondonAirSource()
    # British Summer Time: UTC+1
    summer = src.to_event(_raw(SYNTHETIC[0]))
    assert summer.occurred_at == datetime(2026, 9, 19, 11, 0, tzinfo=timezone.utc)
    assert summer.occurred_at.utcoffset().total_seconds() == 0
    # Greenwich Mean Time: UTC+0
    winter = src.to_event(_raw(SYNTHETIC[0] | {"bulletin_date": "2026-01-15 09:00:00"}))
    assert winter.occurred_at == datetime(2026, 1, 15, 9, 0, tzinfo=timezone.utc)


def test_poll_is_idempotent_and_ends_previous_hour():
    repo = MemoryRepo()
    first = run_poll(FixtureSource(POLL_PAYLOADS), repo)
    assert first == {"fetched": len(POLL_PAYLOADS), "inserted": len(SYNTHETIC), "ended": 0}

    second = run_poll(FixtureSource(POLL_PAYLOADS), repo)
    assert second == {"fetched": len(POLL_PAYLOADS), "inserted": 0, "ended": 0}
    assert len(repo.events) == len(SYNTHETIC)

    # The next hourly bulletin has new external ids: the same sites are inserted
    # as new events and the previous hour's events are marked ended.
    next_hour = [p | {"bulletin_date": "2026-09-19 13:00:00"} for p in POLL_PAYLOADS]
    third = run_poll(FixtureSource(next_hour), repo)
    assert third["inserted"] == len(SYNTHETIC) and third["ended"] == len(SYNTHETIC)
    assert repo.events["london_air:BT4:2026-09-19T12"].ended_at is not None
    assert repo.events["london_air:BT4:2026-09-19T13"].ended_at is None
