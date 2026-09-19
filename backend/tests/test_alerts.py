from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend import alerts, db
from backend.alerts import EMERGENCY_ALERTS, MET_OFFICE, Alert, covers_london
from backend.api_alerts import current_alerts
from backend.db import SqliteRepo

FIXTURES = Path(__file__).parent / "fixtures"
# Feed of 2026-08-12 from the Internet Archive, unchanged
MET_ARCHIVED = (FIXTURES / "met_office_se_archived_20260812.xml").read_bytes()
MET_SYNTHETIC = (FIXTURES / "met_office_se_synthetic.xml").read_bytes()
# Real entries of the live feed plus two synthetic entries that are in force
EA_FEED = (FIXTURES / "emergency_alerts.atom").read_bytes()
MET_EMPTY = b'<?xml version="1.0"?><rss version="2.0"><channel><title>t</title></channel></rss>'


def utc(*args: int) -> datetime:
    return datetime(*args, tzinfo=timezone.utc)


@pytest.fixture
def repo(tmp_path):
    db.connect(tmp_path / "risk.sqlite")
    return SqliteRepo()


# Met Office


def test_met_archived_item():
    [a] = alerts.parse_met_office(MET_ARCHIVED)
    assert a.id == "met_office:27b2ddcc-de43-4935-a544-9eb81e6e4cf8"
    assert (a.source, a.level, a.hazard) == (MET_OFFICE, "amber", "extreme heat")
    assert a.headline == "Amber warning of extreme heat affecting London & South East England"
    # no year in the feed: taken from the link's date=2026-08-13; times read as UTC
    assert a.starts_at == utc(2026, 8, 13, 8, 0) and a.ends_at == utc(2026, 8, 13, 22, 59)
    assert a.url.startswith("https://www.metoffice.gov.uk/weather/warnings-and-advice/uk-warnings#?date=2026-08-13&id=27b2ddcc")
    assert a.area_text.split(", ")[:3] == ["Bracknell Forest", "Buckinghamshire", "Greater London"]
    assert a.area_text.endswith("Wokingham")
    assert covers_london(a.source, a.area_text)
    assert a.raw[0]["image"].endswith("amber-extreme-heat.png")


def test_met_multi_day_items_are_grouped_and_year_is_inferred_over_new_year():
    by_level = {a.level: a for a in alerts.parse_met_office(MET_SYNTHETIC)}
    assert set(by_level) == {"amber", "yellow", "red"}  # the malformed item is skipped
    amber = by_level["amber"]
    assert amber.id == "met_office:11111111-aaaa-4bbb-8ccc-222222222222"
    assert amber.hazard == "wind" and len(amber.raw) == 3
    assert amber.starts_at == utc(2026, 12, 30, 18, 0)
    assert amber.ends_at == utc(2027, 1, 1, 6, 0)
    assert "date=2026-12-30" in amber.url  # link of the earliest day
    assert amber.area_text == "Greater London, Kent, Surrey"
    assert by_level["red"].hazard == "snow and ice"


def test_met_london_membership_needs_greater_london_in_the_area_list():
    by_level = {a.level: a for a in alerts.parse_met_office(MET_SYNTHETIC)}
    assert covers_london(MET_OFFICE, by_level["yellow"].area_text)
    assert not covers_london(MET_OFFICE, by_level["red"].area_text)  # Kent, Medway
    assert not covers_london(MET_OFFICE, "London Colney, Kent")


def test_met_item_with_unexpected_structure_is_none():
    link = "https://x/#?date=2026-08-13&id=27b2ddcc-de43-4935-a544-9eb81e6e4cf8"
    ok = "Amber warning of wind affecting X: Greater London valid from 0800 Thu 13 Aug to 2259 Thu 13 Aug"
    assert alerts.parse_met_item("Amber warning of wind affecting X", ok, link) is not None
    assert alerts.parse_met_item("Severe weather", ok, link) is None
    assert alerts.parse_met_item("Amber warning of wind affecting X", "no validity", link) is None
    assert alerts.parse_met_item("Amber warning of wind affecting X", ok, "https://x/#?id=none") is None


def test_met_empty_feed():
    assert alerts.parse_met_office(MET_EMPTY) == []


# UK Emergency Alerts


def test_emergency_alerts_stopped_entries_and_tests_are_excluded():
    got = alerts.parse_emergency_alerts(EA_FEED, utc(2026, 9, 19, 12, 0))
    # every real entry is stopped; only the two synthetic open-ended ones remain
    assert [a.id for a in got] == [
        "uk_emergency_alerts:synthetic-active-london",
        "uk_emergency_alerts:synthetic-active-newport",
    ]
    a = got[0]
    assert (a.source, a.level, a.ends_at) == (EMERGENCY_ALERTS, "red", None)
    assert a.starts_at == utc(2026, 9, 19, 9, 30)  # [2026-09-19 10:30 BST]
    assert a.url == "https://www.gov.uk/alerts/synthetic-active-london"


def test_emergency_alert_is_active_until_its_stopped_stamp():
    # National wildfire alert: sent [2026-08-14 19:01 BST], stopped [2026-08-14 23:00 BST]
    during = {a.id: a for a in alerts.parse_emergency_alerts(EA_FEED, utc(2026, 8, 14, 20, 0))}
    wildfire = during["uk_emergency_alerts:14-aug-2026-2"]
    assert wildfire.area_text == "England, Wales"
    assert wildfire.starts_at == utc(2026, 8, 14, 18, 1) and wildfire.ends_at == utc(2026, 8, 14, 22, 0)
    assert wildfire.headline == "There is a very high risk of wildfires nationally."
    assert covers_london(wildfire.source, wildfire.area_text)
    wales = during["uk_emergency_alerts:14-aug-2026"]
    assert wales.area_text == "Wales" and not covers_london(wales.source, wales.area_text)
    after = {a.id for a in alerts.parse_emergency_alerts(EA_FEED, utc(2026, 8, 14, 22, 0))}
    assert "uk_emergency_alerts:14-aug-2026-2" not in after


def test_emergency_alert_operator_tests_are_excluded_while_being_sent():
    # 2025-09-07 national test, sent 15:00 BST, stopped 15:20 BST; area includes England
    ids = {a.id for a in alerts.parse_emergency_alerts(EA_FEED, utc(2025, 9, 7, 14, 10))}
    assert "uk_emergency_alerts:7-sep-2025" not in ids
    # 2021 wording: "The UK government is testing Emergency Alerts in East Suffolk."
    ids = {a.id for a in alerts.parse_emergency_alerts(EA_FEED, utc(2021, 5, 25, 12, 10))}
    assert "uk_emergency_alerts:25-may-2021" not in ids


def test_emergency_alert_gmt_stamp_and_inline_issuer():
    # Plymouth, sent [2024-02-23 12:06 GMT]
    got = {a.id: a for a in alerts.parse_emergency_alerts(EA_FEED, utc(2024, 2, 23, 13, 0))}
    plymouth = got["uk_emergency_alerts:23-feb-2024"]
    assert plymouth.starts_at == utc(2024, 2, 23, 12, 6)
    assert plymouth.headline.startswith("The WWII bomb found in Keyham")
    assert not covers_london(plymouth.source, plymouth.area_text)


@pytest.mark.parametrize(
    "area, expected",
    [
        ("England, Wales", True),
        ("England, Northern Ireland, Scotland, Wales", True),
        ("United Kingdom", True),
        ("Greater London", True),
        ("City of London", True),
        ("Camden, Islington, Hertfordshire", True),
        ("Kingston upon Thames", True),
        ("Wales", False),
        ("Cardiff", False),
        ("Newport, Londonderry", False),
        ("Bristol, City of, Devon", False),
        ("River Roe and River Ive from Highbridge to Stockdalewath", False),
    ],
)
def test_emergency_alert_london_matching(area, expected):
    assert covers_london(EMERGENCY_ALERTS, area) is expected


# Storage and polling


def make_alert(n: int, *, source: str = MET_OFFICE, level: str = "amber", start: datetime, end: datetime | None,
               area: str = "Greater London, Kent") -> Alert:
    return Alert(id=f"{source}:{n}", source=source, level=level, hazard="wind", headline=f"headline {n}",
                 area_text=area, url=f"https://example.org/{n}", starts_at=start, ends_at=end, raw={"n": n})


NOW = utc(2026, 9, 19, 12, 0)
HOUR = timedelta(hours=1)


def test_replace_alerts_replaces_all_rows_of_one_source_only(repo):
    a1, a2 = (make_alert(n, start=NOW - HOUR, end=NOW + HOUR) for n in (1, 2))
    ea = make_alert(9, source=EMERGENCY_ALERTS, level="red", start=NOW - HOUR, end=None, area="England")
    assert repo.replace_alerts(MET_OFFICE, [a1, a2], NOW) == {"stored": 2, "deleted": 0}
    repo.replace_alerts(EMERGENCY_ALERTS, [ea], NOW)

    a2_red = a2.model_copy(update={"level": "red"})
    assert repo.replace_alerts(MET_OFFICE, [a2_red], NOW + HOUR / 12) == {"stored": 1, "deleted": 1}
    stored = {a["id"]: a for a in db.stored_alerts()}
    assert set(stored) == {"met_office:2", "uk_emergency_alerts:9"}
    assert stored["met_office:2"]["level"] == "red"
    assert stored["met_office:2"]["starts_at"] == NOW - HOUR
    assert stored["uk_emergency_alerts:9"]["ends_at"] is None

    # an empty successful result withdraws everything of that source
    assert repo.replace_alerts(MET_OFFICE, [], NOW) == {"stored": 0, "deleted": 1}
    assert [a["id"] for a in db.stored_alerts()] == ["uk_emergency_alerts:9"]

    with pytest.raises(ValueError):
        repo.replace_alerts(MET_OFFICE, [ea], NOW)


def test_failed_fetch_keeps_rows_and_other_source_is_still_replaced(repo):
    old = make_alert(1, start=NOW - HOUR, end=NOW + HOUR)
    repo.replace_alerts(MET_OFFICE, [old], NOW - HOUR)

    def failing(now):
        raise OSError("connection refused")

    fetchers = {MET_OFFICE: failing, EMERGENCY_ALERTS: lambda now: alerts.parse_emergency_alerts(EA_FEED, now)}
    result = alerts.poll(repo, NOW, fetchers)
    assert "connection refused" in result[MET_OFFICE]["error"]
    assert result[EMERGENCY_ALERTS] == {"stored": 2, "deleted": 0}
    ids = {a["id"] for a in db.stored_alerts()}
    assert "met_office:1" in ids and "uk_emergency_alerts:synthetic-active-london" in ids

    # invalid XML is a failure as well, not an empty result
    alerts.poll(repo, NOW, {MET_OFFICE: lambda now: alerts.parse_met_office(b"<html>gateway timeout")})
    assert "met_office:1" in {a["id"] for a in db.stored_alerts()}

    # a later successful poll with no items removes the warning
    alerts.poll(repo, NOW, {MET_OFFICE: lambda now: alerts.parse_met_office(MET_EMPTY)})
    assert "met_office:1" not in {a["id"] for a in db.stored_alerts()}


def test_claim_alerts_poll_is_true_once_per_interval(repo):
    sources = list(alerts.SOURCES)
    assert repo.claim_alerts_poll(NOW, 285, sources) is True
    assert repo.claim_alerts_poll(NOW + timedelta(seconds=60), 285, sources) is False
    assert repo.claim_alerts_poll(NOW + timedelta(seconds=284), 285, sources) is False
    assert repo.claim_alerts_poll(NOW + timedelta(seconds=285), 285, sources) is True
    # a failure does not move the attempt time
    repo.record_alerts_failure(MET_OFFICE, "boom", NOW + timedelta(seconds=290))
    assert repo.claim_alerts_poll(NOW + timedelta(seconds=300), 285, sources) is False


# API


def test_api_filters_by_time_window_level_and_london(repo):
    repo.replace_alerts(
        MET_OFFICE,
        [
            make_alert(1, start=NOW - HOUR, end=NOW + HOUR),  # in force
            make_alert(2, start=NOW + 23 * HOUR, end=NOW + 30 * HOUR),  # starts within 24 h
            make_alert(3, start=NOW + 25 * HOUR, end=NOW + 30 * HOUR),  # too far ahead
            make_alert(4, start=NOW - 5 * HOUR, end=NOW - HOUR),  # ended, still listed upstream
            make_alert(5, level="yellow", start=NOW - HOUR, end=NOW + HOUR),
            make_alert(6, level="red", start=NOW - HOUR, end=NOW + HOUR, area="Kent, Medway"),
            make_alert(7, level="red", start=NOW + 2 * HOUR, end=NOW + 3 * HOUR),
        ],
        NOW,
    )
    repo.replace_alerts(
        EMERGENCY_ALERTS,
        [
            make_alert(8, source=EMERGENCY_ALERTS, level="red", start=NOW - 2 * HOUR, end=None, area="England, Wales"),
            make_alert(9, source=EMERGENCY_ALERTS, level="red", start=NOW - HOUR, end=None, area="Wales"),
        ],
        NOW,
    )
    got = current_alerts(NOW)
    # red before amber, then by start
    assert [a["id"] for a in got] == ["uk_emergency_alerts:8", "met_office:7", "met_office:1", "met_office:2"]
    assert [a["active"] for a in got] == [True, False, True, False]
    assert got[0]["source_label"] == "GOV.UK Emergency Alerts" and got[2]["source_label"] == "Met Office"
    assert set(got[0]) == {"id", "source", "source_label", "level", "hazard", "headline", "url",
                           "starts_at", "ends_at", "active"}

    assert "met_office:5" in {a["id"] for a in current_alerts(NOW, "yellow")}
    assert {a["level"] for a in current_alerts(NOW, "red")} == {"red"}

    # an open-ended alert is dropped when no poll has succeeded for 6 hours
    assert "uk_emergency_alerts:8" in {a["id"] for a in current_alerts(NOW + 5 * HOUR)}
    assert "uk_emergency_alerts:8" not in {a["id"] for a in current_alerts(NOW + 7 * HOUR)}


def test_api_endpoint(repo):
    from backend.api import web
    from backend.models import utcnow

    now = utcnow()
    repo.replace_alerts(
        MET_OFFICE,
        [make_alert(1, start=now - HOUR, end=now + HOUR), make_alert(2, level="yellow", start=now - HOUR, end=now + HOUR)],
        now,
    )
    client = TestClient(web)
    body = client.get("/api/alerts").json()
    assert [a["id"] for a in body] == ["met_office:1"]
    assert body[0]["active"] is True and body[0]["url"] == "https://example.org/1"
    assert datetime.fromisoformat(body[0]["ends_at"]).utcoffset() == timedelta(0)
    assert len(client.get("/api/alerts", params={"min_level": "yellow"}).json()) == 2
    assert client.get("/api/alerts", params={"min_level": "purple"}).status_code == 422
