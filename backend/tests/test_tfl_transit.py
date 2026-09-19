"""Tests of the TfL rail incident source.

On 2026-09-19 neither TfL feed listed an incident, so every incident item in the
fixtures and in this file is SYNTHETIC (fixture items carry `"synthetic": true`):
real stations and coordinates, with incident wording written from memory of
TfL's customary status text. The items that must be dropped are real feed items
captured on 2026-09-19 (station items with their resolved coordinates embedded,
line statuses reduced to the fields the source reads). tfl_line_stops.json is
the real `Line/{id}/StopPoints` response of three lines, reduced to four fields.
"""

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from backend.models import Category
from backend.pipeline import run_poll
from backend.sources import tfl_transit
from backend.sources.tfl_transit import INCIDENT_CAUSES, TflTransitSource, _locate, _normalise, _stop_table, classify
from backend.tests.test_pipeline import MemoryRepo

FIXTURES = Path(__file__).parent / "fixtures"
STATIONS = json.loads((FIXTURES / "tfl_transit.json").read_text())
LINES = json.loads((FIXTURES / "tfl_line_status.json").read_text())
LINE_STOPS = json.loads((FIXTURES / "tfl_line_stops.json").read_text())
REAL_STATIONS = [d for d in STATIONS if not d.get("synthetic")]
REAL_LINES = [line for line in LINES if not line.get("synthetic")]

FIRE = "940GZZLUOXC:Closure:2026-09-19T14:05:00Z"
CASUALTY = "940GZZLUSTD:Closure:2026-09-19T14:05:00Z"
KEPT = {
    FIRE: ("Fire alert: Oxford Circus Underground Station", Category.FIRE, 0.4),
    "940GZZLUBSC:Part Closure:2026-09-19T14:05:00Z": (
        "Customer incident: Barons Court Underground Station",
        Category.TRANSIT_DISRUPTION,
        0.3,
    ),
    CASUALTY: ("Casualty on the track: Stratford Underground Station", Category.TRANSIT_DISRUPTION, 0.5),
    "line:central:Police incident:940GZZLUBLG:2026-09-19": (
        "Police incident: Bethnal Green Underground Station",
        Category.TRANSIT_DISRUPTION,
        0.4,
    ),
    "line:circle:Fire alert:940GZZLUPAC:2026-09-19": (
        "Fire alert: Paddington Underground Station",
        Category.FIRE,
        0.4,
    ),
}
LINE_DUPLICATE = "line:central:Casualty on the track:940GZZLUSTD:2026-09-19"


@pytest.fixture(autouse=True)
def line_stops(monkeypatch):
    monkeypatch.setattr(tfl_transit, "_LINE_STOPS", {k: _stop_table(v) for k, v in LINE_STOPS.items()})


class FixtureSource(TflTransitSource):
    def __init__(self, stations: list[dict], lines: list[dict] = ()) -> None:
        self.stations, self.lines = stations, lines

    def fetch(self, cursor):
        return self.items(self.stations, self.lines), cursor


def _events(stations: list[dict], lines: list[dict] = ()) -> dict:
    src = TflTransitSource()
    return {raw.external_id: src.to_event(raw) for raw in src.items(stations, lines)}


def _station(description: str, **changes) -> dict:
    """SYNTHETIC station item at Oxford Circus."""
    base = next(d for d in STATIONS if d["atcoCode"] == "940GZZLUOXC" and d.get("synthetic"))
    return {**base, "description": description, **changes}


def _line(reason: str, line_id: str = "central", category: str = "RealTime") -> dict:
    """SYNTHETIC line status."""
    base = next(line for line in LINES if line.get("synthetic") and line["id"] == "central")
    status = {**base["lineStatuses"][0], "reason": reason, "disruption": {"category": category}}
    return {**base, "id": line_id, "lineStatuses": [status]}


def test_to_event_maps_fields():
    fire = _events(STATIONS, LINES)[FIRE]
    assert fire.category == Category.FIRE
    assert fire.severity == 0.4
    assert fire.confidence == 0.95
    assert fire.source_confidence == {"tfl_transit": 0.95}
    assert fire.external_ref == f"tfl_transit:{FIRE}"
    assert fire.title == "Fire alert: Oxford Circus Underground Station"
    assert fire.summary.endswith("closed while we respond to a fire alert.")
    assert fire.geometry == {"type": "Point", "coordinates": [-0.141903, 51.515224]}
    assert (fire.lng, fire.lat) == (-0.141903, 51.515224)
    assert fire.radius_m == 200
    assert fire.half_life_min is None
    assert fire.occurred_at == datetime(2026, 9, 19, 14, 5, tzinfo=timezone.utc)
    assert fire.expires_at == datetime(2036, 1, 1, 0, 29, tzinfo=timezone.utc)
    assert fire.source_ids == ["tfl_transit"]


def test_only_incidents_produce_events():
    events = _events(STATIONS, LINES)
    assert {i: (e.title, e.category, e.severity) for i, e in events.items() if e} == KEPT


def test_real_feed_items_of_every_type_produce_no_event():
    """The real captures hold closures, part closures, exit-only, interchange
    messages and information notices; none of them names an incident cause."""
    assert {d["type"] for d in REAL_STATIONS} == {
        "Closure",
        "Part Closure",
        "Exit Only",
        "Interchange Message",
        "Information",
    }
    assert {d["appearance"] for d in REAL_STATIONS} == {"RealTime", "PlannedWork", "Information"}
    events = _events(REAL_STATIONS, REAL_LINES)
    assert len(events) == len(REAL_STATIONS)  # real line statuses give no item at all
    assert all(e is None for e in events.values())


@pytest.mark.parametrize("item", REAL_STATIONS, ids=lambda d: f"{d['atcoCode']}-{d['type']}-{d['appearance']}")
def test_real_station_item_is_dropped_even_when_located_and_realtime(item):
    located = {**item, "lat": 51.5154, "lon": -0.1419, "appearance": "RealTime"}
    assert list(_events([located]).values()) == [None]


@pytest.mark.parametrize("upstream_type", ["Closure", "Part Closure", "Exit Only", "Information", "Future Upstream Type"])
def test_incident_is_kept_whatever_the_upstream_type(upstream_type):
    (event,) = _events([_station("Station closed due to a security alert.", type=upstream_type)]).values()
    assert event.title.startswith("Security alert: ")


@pytest.mark.parametrize(
    "text, label, category, severity",
    [
        ("closed due to a customer incident.", "Customer incident", Category.TRANSIT_DISRUPTION, 0.3),
        ("delays due to a person ill on a train.", "Customer incident", Category.TRANSIT_DISRUPTION, 0.3),
        ("while we help an ill customer.", "Customer incident", Category.TRANSIT_DISRUPTION, 0.3),
        ("a customer taken ill on a train.", "Customer incident", Category.TRANSIT_DISRUPTION, 0.3),
        ("due to a casualty on the track.", "Casualty on the track", Category.TRANSIT_DISRUPTION, 0.5),
        ("due to a person on the track.", "Casualty on the track", Category.TRANSIT_DISRUPTION, 0.5),
        ("due to a person under a train.", "Casualty on the track", Category.TRANSIT_DISRUPTION, 0.5),
        ("following an assault on a member of staff.", "Assault", Category.VIOLENT_CRIME, 0.5),
        ("due to a police investigation.", "Police incident", Category.TRANSIT_DISRUPTION, 0.4),
        ("closed at the request of the police.", "Police incident", Category.TRANSIT_DISRUPTION, 0.4),
        ("while the police respond to an incident.", "Police incident", Category.TRANSIT_DISRUPTION, 0.4),
        ("due to the emergency services dealing with an incident.", "Emergency services incident", Category.TRANSIT_DISRUPTION, 0.4),
        ("while we respond to a fire alert.", "Fire alert", Category.FIRE, 0.4),
        ("due to a fire investigation.", "Fire alert", Category.FIRE, 0.4),
        ("due to reports of smoke in the tunnel.", "Fire alert", Category.FIRE, 0.4),
        ("due to a security alert.", "Security alert", Category.TRANSIT_DISRUPTION, 0.4),
        ("due to an unattended item.", "Security alert", Category.TRANSIT_DISRUPTION, 0.4),
        ("due to a suspect package.", "Security alert", Category.TRANSIT_DISRUPTION, 0.4),
        ("due to trespassers on the track.", "Trespasser", Category.TRANSIT_DISRUPTION, 0.4),
        ("the station has been evacuated.", "Evacuation", Category.TRANSIT_DISRUPTION, 0.4),
        # the most severe cause wins
        ("due to a customer incident, a person under a train.", "Casualty on the track", Category.TRANSIT_DISRUPTION, 0.5),
    ],
)
def test_incident_phrases(text, label, category, severity):
    (event,) = _events([_station(f"Oxford Circus Station: {text}")]).values()
    assert (event.title.split(":")[0], event.category, event.severity) == (label, category, severity)


def test_every_cause_label_is_covered_by_a_phrase_test():
    tested = {args[1] for args in test_incident_phrases.pytestmark[0].args[1]}
    assert tested == {label for label, *_ in INCIDENT_CAUSES}


@pytest.mark.parametrize(
    "text",
    [
        # operational causes (the first five are real wording observed on 2026-09-19)
        "Minor delays due to train cancellations.",
        "Step free access is not available due to a faulty lift.",
        "the station will be closed due to a football match at Emirates Stadium.",
        "This station is closed due to planned engineering works.",
        "a queuing system will be in operation due to a football match.",
        "Severe delays due to a signal failure.",
        "Minor delays due to a faulty train.",
        "No service due to a track fault.",
        "Station closed due to a power failure.",
        "Station closed due to staff shortage.",
        "No service due to strike action by the fire brigade union.",
        "Station closed to prevent overcrowding.",
        "Part closure due to emergency engineering work.",
        # incident over
        "Minor delays due to an earlier customer incident.",
        "Minor delays following an earlier casualty on the track.",
        "Good service has resumed following the earlier fire alert.",
    ],
)
def test_non_incident_causes_are_dropped(text):
    assert list(_events([_station(text)]).values()) == [None]
    assert _events([], [_line(f"Central Line: {text[:-1]} at Stratford.")]) == {}


@pytest.mark.parametrize("appearance", ["PlannedWork", "Information", None])
def test_only_realtime_items_are_incidents(appearance):
    text = "In an emergency evacuation, follow the instructions of the police."
    assert list(_events([_station(text, appearance=appearance)]).values()) == [None]
    assert _events([], [_line("Central Line: police investigation at Stratford.", category=appearance)]) == {}


def test_kept_cause_without_usable_location_is_skipped():
    events = _events(STATIONS, LINES)
    for stop in ("TEST-NO-COORDS", "TEST-OUTSIDE-LONDON"):
        item = next(d for d in STATIONS if d["atcoCode"] == stop)
        assert classify(item) is not None
        assert events[f"{stop}:Closure:2026-09-19T14:05:00Z"] is None


@pytest.mark.parametrize(
    "line_id, reason, stop_id",
    [
        ("central", "Severe delays due to a customer incident at Stratford.", "940GZZLUSTD"),
        ("central", "Severe delays due to a customer incident at Stratford station, tickets accepted", "940GZZLUSTD"),
        ("central", "Delays due to a person ill on a train at Bank. Tickets accepted.", "940GZZLUBNK"),
        ("central", "Delays due to a fire alert at Shepherd's Bush while we investigate", "940GZZLUSBC"),
        ("central", "Delays due to a fire alert at Shepherds Bush", "940GZZLUSBC"),
        ("central", "No service while the police respond to an incident at Bethnal Green station.", "940GZZLUBLG"),
        ("circle", "Delays due to a security alert at King's Cross St. Pancras.", "940GZZLUKSX"),
        ("circle", "Delays due to a security alert at Kings Cross St Pancras", "940GZZLUKSX"),
        ("circle", "Delays due to a fire alert at Edgware Road.", "940GZZLUERC"),
        # two stops named Paddington on this line, 250 m apart: one station
        ("circle", "Delays due to a fire alert at Paddington.", "940GZZLUPAC"),
        ("northern", "Delays due to a customer incident at Elephant & Castle.", "940GZZLUEAC"),
        ("northern", "Delays due to a customer incident at Elephant and Castle station.", "940GZZLUEAC"),
        # not located: no station, station of another line, longer place name, unknown line
        ("central", "Severe delays due to a customer incident.", None),
        ("central", "Severe delays due to a customer incident at Brixton.", None),
        ("northern", "Severe delays due to a police investigation at Hampstead Heath.", None),
        ("central", "Severe delays due to a customer incident at the eastern end of the line.", None),
        ("jubilee", "Severe delays due to a customer incident at Stratford.", None),
    ],
)
def test_line_status_station_resolution(line_id, reason, stop_id):
    items = TflTransitSource().items([], [_line(f"Line: {reason}", line_id)])
    assert [i.payload["atcoCode"] for i in items] == ([stop_id] if stop_id else [])
    if stop_id:
        (item,) = items
        assert item.external_id.startswith(f"line:{line_id}:") and item.external_id.endswith(f":{stop_id}:2026-09-19")
        assert TflTransitSource().to_event(item).geometry["type"] == "Point"


def test_normalise_station_names():
    assert _normalise("Paddington (H&C Line)-Underground") == "paddington"
    assert _normalise("King's Cross St. Pancras Underground Station") == "kings cross st pancras"
    assert _normalise("Harrow-on-the-Hill Underground Station") == "harrow on the hill"
    assert _normalise("Bank DLR Station") == "bank"
    assert _normalise("Heathrow Terminals 2 & 3 Underground Station") == "heathrow terminals 2 and 3"


def test_line_id_does_not_depend_on_delay_wording():
    a = TflTransitSource().items([], [_line("Central Line: Severe delays due to a customer incident at Stratford.")])
    b = TflTransitSource().items([], [_line("Central Line: Minor delays due to a customer incident at Stratford.")])
    assert [i.external_id for i in a] == [i.external_id for i in b]


def test_same_incident_reported_twice_gives_one_event():
    items = {i.external_id: i for i in TflTransitSource().items(STATIONS, LINES)}
    # the station feed and the Central line status both report the casualty at Stratford
    assert items[LINE_DUPLICATE].payload["duplicateOf"] == CASUALTY
    assert TflTransitSource().to_event(items[LINE_DUPLICATE]) is None
    # two lines reporting one incident
    reason = "Line: Severe delays due to a security alert at Bank."
    two = TflTransitSource().items([], [_line(reason, "central"), _line(reason, "northern")])
    assert [i.payload.get("duplicateOf") for i in two] == [None, two[0].external_id]
    # a different cause at the same station is a separate event
    other = TflTransitSource().items(STATIONS, [_line("Line: delays due to a fire alert at Stratford.")])
    assert other[-1].payload.get("duplicateOf") is None


def test_external_ids_are_stable_and_unique():
    src = TflTransitSource()
    first = [raw.external_id for raw in src.items(STATIONS, LINES)]
    second = [raw.external_id for raw in src.items(list(reversed(STATIONS)), list(reversed(LINES)))]
    assert sorted(first) == sorted(second)
    assert len(set(first)) == len(first)
    # Two disruptions at the same stop differ by type and start date
    assert "940GZZLUBSC:Closure:2026-09-19T00:00:00Z" in first
    assert "940GZZLUBSC:Part Closure:2026-07-06T03:30:00Z" in first


def test_colliding_ids_get_description_hash():
    a = dict(STATIONS[0])
    b = {**a, "description": "A second notice with the same stop, type and start."}
    ids = [raw.external_id for raw in TflTransitSource().items([a, b])]
    assert len(set(ids)) == 2
    assert all(i.startswith("940GZZLUBSC:Closure:2026-09-19T00:00:00Z:") for i in ids)
    assert ids == [raw.external_id for raw in TflTransitSource().items([a, b])]


def test_poll_is_idempotent_and_marks_ended():
    repo = MemoryRepo()
    fetched = len(STATIONS) + len(KEPT) - 3 + 1  # located line incidents: 2 kept and 1 duplicate
    first = run_poll(FixtureSource(STATIONS, LINES), repo)
    assert first == {"fetched": fetched, "inserted": len(KEPT), "ended": 0}

    second = run_poll(FixtureSource(STATIONS, LINES), repo)
    assert second == {"fetched": fetched, "inserted": 0, "ended": 0}
    assert set(repo.events) == {f"tfl_transit:{i}" for i in KEPT}

    # The incidents leave both feeds while the convenience notices stay: the fetch
    # result has no kept item but is not empty, so every event is ended.
    third = run_poll(FixtureSource(REAL_STATIONS, REAL_LINES), repo)
    assert third == {"fetched": len(REAL_STATIONS), "inserted": 0, "ended": len(KEPT)}
    assert all(ev.ended_at is not None for ev in repo.events.values())


def test_poll_ends_stored_events_of_the_previous_version():
    """State after deploy: the database holds open events that the previous
    version created from closures, part closures, exit-only and interchange
    notices, under the same external ids. The feed still lists those items."""

    class PreviousVersion(FixtureSource):
        def to_event(self, raw):
            d = {**raw.payload, "appearance": "RealTime", "description": "Closed due to a security alert."}
            return super().to_event(raw.model_copy(update={"payload": d}))

    repo = MemoryRepo()
    run_poll(PreviousVersion(REAL_STATIONS), repo)
    stale = set(repo.events)
    assert {ref.split(":")[2] for ref in stale} == {d["type"] for d in REAL_STATIONS if d["atcoCode"] != "910GCHESHNT"}

    counts = run_poll(FixtureSource(STATIONS, LINES), repo)
    assert counts["inserted"] == len(KEPT) and counts["ended"] == len(stale)
    for ref, ev in repo.events.items():
        assert (ev.ended_at is not None) == (ref in stale)
