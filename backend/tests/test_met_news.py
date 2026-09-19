import pytest

from backend import extract_rules
from backend.geocode import GeoResult
from backend.models import Category, RawItem
from backend.prefilter import is_incident_candidate
from backend.sources import met_news

# Headlines taken from the live feed on 2026-09-19
INCIDENTS = [
    "Man dies following stabbing in Lloyd Baker Street, Clerkenwell",
    "Detectives appeal for information after rape in Bethnal Green",
]
NOT_INCIDENTS = [
    "Two teenagers sentenced over death of 17-year-old",
    "Man jailed for life following murder in Westminster",
    "Man charged with Tottenham murder to appear in court",
    "Two more officers dismissed over Charing Cross conduct",
    "Man charged in Covent Garden rape",
    "Zombie knife importer convicted",
    "Officer dismissed after breaching Met social media policies",
]


@pytest.mark.parametrize("title", INCIDENTS)
def test_prefilter_accepts_current_incidents(title):
    assert is_incident_candidate(title)


@pytest.mark.parametrize("title", NOT_INCIDENTS)
def test_prefilter_rejects_court_and_conduct_reports(title):
    assert not is_incident_candidate(title)


def test_rule_extraction_reads_category_severity_and_place():
    e = extract_rules.extract(INCIDENTS[0])
    assert e.category is Category.VIOLENT_CRIME and e.severity == 0.95
    assert e.place_text == "Lloyd Baker Street, Clerkenwell"
    assert extract_rules.extract(INCIDENTS[1]).place_text == "Bethnal Green"


def test_rule_extraction_needs_a_place():
    assert extract_rules.extract("Man dies following stabbing") is None
    assert extract_rules.extract("Appeal after assault in London") is None


def _raw(title):
    payload = {
        "title": title,
        "description": "",
        "link": "https://news.met.police.uk/news/x",
        "guid": "guid-1",
        "pub_date": "Fri, 18 Sep 2026 17:37:00 +0100",
    }
    return RawItem(source_id="met_news", external_id="guid-1", payload=payload)


def test_to_event_street_level(monkeypatch):
    monkeypatch.setattr(met_news, "geocode", lambda _: GeoResult(-0.1104, 51.5289, 150, "x", "test"))
    ev = met_news.MetNewsSource().to_event(_raw(INCIDENTS[0]))
    assert ev.external_ref == "met_news:guid-1"
    assert ev.confidence == pytest.approx(0.9)
    assert ev.radius_m == 250
    assert ev.occurred_at.utcoffset() is not None
    assert ev.half_life_min == met_news.HALF_LIFE_MIN
    assert ev.mergeable


def test_to_event_area_level_lowers_confidence_and_widens_radius(monkeypatch):
    monkeypatch.setattr(met_news, "geocode", lambda _: GeoResult(-0.0562, 51.5303, 2600, "x", "test"))
    ev = met_news.MetNewsSource().to_event(_raw(INCIDENTS[1]))
    assert ev.confidence == pytest.approx(0.9 * 0.6)
    assert ev.radius_m == 2600


def test_to_event_discards_unresolved_places_and_filtered_items(monkeypatch):
    monkeypatch.setattr(met_news, "geocode", lambda _: None)
    assert met_news.MetNewsSource().to_event(_raw(INCIDENTS[0])) is None
    monkeypatch.setattr(met_news, "geocode", lambda _: pytest.fail("geocoder called for a filtered item"))
    assert met_news.MetNewsSource().to_event(_raw(NOT_INCIDENTS[0])) is None
