from datetime import datetime, timezone

import pytest

from backend import extract_rules, extraction
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
    assert e.category is Category.VIOLENT_CRIME and e.severity == 0.9 and e.subtype == "homicide"
    assert e.place_text == "Lloyd Baker Street, Clerkenwell"
    assert extract_rules.extract(INCIDENTS[1]).place_text == "Bethnal Green"


def test_rule_extraction_needs_a_place():
    assert extract_rules.extract("Man dies following stabbing") is None
    assert extract_rules.extract("Appeal after assault in London") is None


@pytest.fixture(autouse=True)
def _rules_path(monkeypatch):
    monkeypatch.delenv("LLM_MODEL", raising=False)


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
    monkeypatch.setattr(extraction, "geocode", lambda _: GeoResult(-0.1104, 51.5289, 150, "x", "test"))
    ev = met_news.MetNewsSource().to_event(_raw(INCIDENTS[0]))
    assert ev.external_ref == "met_news:guid-1"
    assert ev.confidence == pytest.approx(0.9)
    assert ev.radius_m == 250
    assert ev.occurred_at.utcoffset() is not None
    assert ev.subtype == "homicide" and not ev.is_ongoing and not ev.feed_managed
    assert ev.mergeable


def test_to_event_area_level_lowers_confidence_and_caps_radius(monkeypatch):
    monkeypatch.setattr(extraction, "geocode", lambda _: GeoResult(-0.0562, 51.5303, 2600, "x", "test"))
    ev = met_news.MetNewsSource().to_event(_raw("Woman raped in Bethnal Green last night"))
    assert ev.confidence == pytest.approx(0.9 * 0.6)
    assert ev.radius_m == 800 and ev.subtype == "sexual_assault"


def test_rules_drop_an_appeal_without_a_recent_incident(monkeypatch):
    monkeypatch.setattr(extraction, "geocode", lambda _: GeoResult(-0.0562, 51.5303, 2600, "x", "test"))
    source = met_news.MetNewsSource()
    # the headline is an appeal and nothing indicates when the offence happened
    assert source.to_event(_raw(INCIDENTS[1])) is None
    # the description states the date: older than the hard cap of 72 h
    raw = _raw(INCIDENTS[1])
    raw.payload["description"] = "The offence happened on 1 August 2024."
    assert source.to_event(raw) is None
    # a stated date inside the hard cap is used as the incident time
    raw.payload["description"] = "The offence happened on 17 September."
    ev = source.to_event(raw)
    assert ev.occurred_at == datetime(2026, 9, 17, tzinfo=timezone.utc)
    assert ev.last_confirmed_at == datetime(2026, 9, 18, 16, 37, tzinfo=timezone.utc)


def test_rules_severity_anchors_and_modifiers():
    def sev(title):
        e = extract_rules.extract(title)
        return e.subtype, e.severity

    assert sev("Large peaceful protest in Whitehall") == ("peaceful_protest", 0.3)
    assert sev("Scuffles at protest in Whitehall") == ("tense_protest", 0.4)
    assert sev("Riot in Whitehall") == ("violent_disorder", 0.6)
    assert sev("Man robbed in Brixton") == ("robbery", 0.5)
    assert sev("Robbery at knifepoint in Brixton") == ("robbery", 0.7)
    assert sev("Phones stolen in Soho") == ("theft", 0.2)
    assert sev("Flats evacuated after fire in Hackney") == ("fire", 0.6)
    assert extract_rules.extract("False alarm after reports of shooting in Soho") is None
    at_large = extract_rules.extract("Man stabbed in Soho, suspect fled the scene")
    arrested = extract_rules.extract("Man stabbed in Soho, arrest made")
    assert at_large.suspect_at_large and arrested.resolved
    from backend.located import adjusted_severity

    assert adjusted_severity(0.9, True, False) == pytest.approx(0.95)
    assert adjusted_severity(1.0, True, False) == 1.0
    assert adjusted_severity(0.9, True, True) == pytest.approx(0.54)


def test_to_event_discards_unresolved_places_and_filtered_items(monkeypatch):
    monkeypatch.setattr(extraction, "geocode", lambda _: None)
    assert met_news.MetNewsSource().to_event(_raw(INCIDENTS[0])) is None
    monkeypatch.setattr(extraction, "geocode", lambda _: pytest.fail("geocoder called for a filtered item"))
    assert met_news.MetNewsSource().to_event(_raw(NOT_INCIDENTS[0])) is None
