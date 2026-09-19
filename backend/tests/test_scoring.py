from datetime import datetime, timedelta, timezone
from uuid import uuid4

import h3
import pytest

from backend.models import TIMING, Category, Event, Subtype, timing_for
from backend.scoring import (
    COARSE_RES,
    FINE_RES,
    cell_weights,
    combine,
    compute_cell_scores,
    event_end,
    event_risk,
    event_state,
    merge,
    should_merge,
    with_baseline,
)

T0 = datetime(2026, 9, 19, 12, 0, tzinfo=timezone.utc)
SOHO = (-0.1365, 51.5136)


def make_event(lng=SOHO[0], lat=SOHO[1], **kw) -> Event:
    defaults = dict(
        id=uuid4(),
        category=Category.VIOLENT_CRIME,
        title="test",
        geometry={"type": "Point", "coordinates": [lng, lat]},
        lng=lng,
        lat=lat,
        radius_m=250,
        severity=0.8,
        confidence=0.5,
        source_confidence={"bbc_london": 0.5},
        occurred_at=T0,
        source_ids=["bbc_london"],
        mergeable=True,
    )
    return Event(**(defaults | kw))


def test_one_off_incident_is_ended_when_reported_and_only_the_residual_decays():
    # violent crime without subtype: robbery/assault row, half-life 8 h, hard cap 36 h
    ev = make_event()
    assert event_state(ev, T0) == "ended" and event_end(ev, T0) == T0
    assert event_risk(ev, T0) == pytest.approx(0.4)
    assert event_risk(ev, T0 + timedelta(hours=8)) == pytest.approx(0.2)
    assert event_risk(ev, T0 + timedelta(hours=16)) == pytest.approx(0.1)
    assert event_risk(ev, T0 + timedelta(hours=36)) == pytest.approx(0.4 * 0.5 ** 4.5)
    assert event_risk(ev, T0 + timedelta(hours=36, seconds=1)) == 0.0


def test_timing_is_keyed_by_category_and_subtype():
    serious = make_event(subtype="stabbing")
    assert (serious.timing.freshness_h, serious.timing.half_life_h, serious.timing.hard_cap_h) == (6, 18, 72)
    assert event_risk(serious, T0 + timedelta(hours=18)) == pytest.approx(0.2)
    assert event_risk(serious, T0 + timedelta(hours=72, seconds=1)) == 0.0
    assert timing_for(Category.VIOLENT_CRIME, "robbery") == timing_for(Category.VIOLENT_CRIME, None)
    assert timing_for(Category.FIRE, "explosion").hard_cap_h == 48
    assert timing_for(Category.ROAD_CLOSURE).half_life_h == 0.25
    # every category has a default row, and every subtype key is in the closed list
    assert {c for c, sub in TIMING if sub is None} == set(Category)
    assert {sub for _, sub in TIMING if sub} <= {s.value for s in Subtype}


def protest(**kw):
    defaults = dict(
        category=Category.DISORDER,
        subtype="peaceful_protest",
        occurred_at=T0 - timedelta(days=2),
        is_ongoing=True,
        last_confirmed_at=T0 - timedelta(hours=1),
    )
    return make_event(**(defaults | kw))


def test_ongoing_event_does_not_decay_however_long_ago_it_started():
    ev = protest()
    assert event_state(ev, T0) == "ongoing" and event_end(ev, T0) is None
    assert event_risk(ev, T0) == pytest.approx(0.4)
    # still inside the 12 h freshness window of the last confirmation
    assert event_risk(ev, T0 + timedelta(hours=10, minutes=59)) == pytest.approx(0.4)


def test_unconfirmed_ongoing_event_is_ended_at_its_last_confirmation():
    # stabbing scene: freshness 6 h, half-life 18 h
    ev = make_event(subtype="stabbing", is_ongoing=True, occurred_at=T0 - timedelta(days=2), last_confirmed_at=T0)
    assert event_risk(ev, T0 + timedelta(hours=6)) == pytest.approx(0.4)
    t = T0 + timedelta(hours=9)
    assert event_state(ev, t) == "ended" and event_end(ev, t) == T0
    assert event_risk(ev, t) == pytest.approx(0.4 * 0.5 ** (9 / 18))
    # disorder: the freshness window equals the hard cap, so the risk goes to 0
    stale = protest(last_confirmed_at=T0 - timedelta(hours=12, minutes=1))
    assert event_end(stale, T0) == stale.last_confirmed_at and event_risk(stale, T0) == 0.0


def test_stated_end_time_wins_and_lengthens_the_freshness_window():
    ev = protest(last_confirmed_at=T0 - timedelta(hours=20), expires_at=T0 + timedelta(hours=1))
    assert event_state(ev, T0) == "ongoing"  # 24 h window because an end time is stated
    assert event_state(ev, T0 + timedelta(hours=2)) == "ended"
    assert event_risk(ev, T0 + timedelta(hours=3)) == pytest.approx(0.4 * 0.5)  # 2 h after the stated end
    # confirmed a minute ago, but the stated end has passed
    confirmed = protest(last_confirmed_at=T0, expires_at=T0 - timedelta(hours=2))
    assert event_end(confirmed, T0) == T0 - timedelta(hours=2)


def test_property_crime_is_never_ongoing():
    ev = make_event(category=Category.PROPERTY_CRIME, is_ongoing=True, last_confirmed_at=T0)
    assert event_state(ev, T0 + timedelta(minutes=1)) == "ended" and event_end(ev, T0) == T0


def test_risk_is_zero_before_the_event():
    assert event_risk(make_event(), T0 - timedelta(minutes=1)) == 0.0


def test_feed_managed_event_has_full_risk_while_listed_then_decays_after_end():
    # `half_life_min=None` is how the structured sources still mark a feed-managed event
    ev = make_event(category=Category.ROAD_CLOSURE, half_life_min=None)
    assert ev.feed_managed and ev.is_ongoing and ev.half_life_min is None
    assert event_state(ev, T0 - timedelta(minutes=1)) == "upcoming"
    assert event_risk(ev, T0 + timedelta(days=3)) == pytest.approx(0.4)
    ended = ev.model_copy(update={"ended_at": T0 + timedelta(hours=1)})
    assert event_risk(ended, T0 + timedelta(hours=1)) == pytest.approx(0.4)
    # road incident row: half-life 15 min, hard cap 1 h
    assert event_risk(ended, T0 + timedelta(hours=1, minutes=15)) == pytest.approx(0.2)
    assert event_risk(ended, T0 + timedelta(hours=2)) == pytest.approx(0.4 / 16)
    assert event_risk(ended, T0 + timedelta(hours=2, seconds=1)) == 0.0


def test_expires_at_ends_the_event():
    ev = make_event(category=Category.FLOOD, feed_managed=True, expires_at=T0 + timedelta(hours=1))
    assert event_risk(ev, T0 + timedelta(minutes=59)) == pytest.approx(0.4)
    assert event_risk(ev, T0 + timedelta(hours=7)) == pytest.approx(0.2)  # flood half-life 6 h
    # the earlier of ended_at and expires_at is the end
    dropped = ev.model_copy(update={"ended_at": T0 + timedelta(minutes=30)})
    assert event_end(dropped, T0 + timedelta(hours=2)) == T0 + timedelta(minutes=30)


def test_a_legacy_half_life_value_is_ignored():
    ev = make_event(half_life_min=1440)
    assert not ev.feed_managed and ev.half_life_min == 8 * 60
    assert "half_life_min" not in ev.model_dump()


def test_naive_datetime_rejected():
    with pytest.raises(ValueError):
        make_event(occurred_at=datetime(2026, 9, 19, 12, 0))


def test_combine_is_bounded_and_order_independent():
    assert combine([]) == 0.0
    assert combine([0.5, 0.5]) == pytest.approx(0.75)
    assert combine([0.2, 0.7]) == pytest.approx(combine([0.7, 0.2]))
    assert combine([0.9] * 50) <= 1.0


def test_with_baseline():
    assert with_baseline(0.0, 0.0) == 0.0
    assert with_baseline(0.0, 1.0) == pytest.approx(0.4)
    assert with_baseline(1.0, 0.5) == 1.0


def test_point_event_cell_weights():
    ev = make_event()
    weights = cell_weights(ev)
    home = h3.latlng_to_cell(ev.lat, ev.lng, FINE_RES)
    assert weights[home] == 1.0
    assert all(0 < w <= 1 for w in weights.values())
    # 250 m radius at res 9 (~350 m between centres) reaches the home cell and
    # some of its 6 neighbours, never the second ring
    assert 1 <= len(weights) <= 7
    assert set(weights) <= set(h3.grid_disk(home, 1))


def test_larger_radius_reaches_more_cells():
    assert len(cell_weights(make_event(radius_m=1000))) > len(cell_weights(make_event()))


def test_polygon_event_gives_full_weight_inside():
    w, s, e, n = -0.15, 51.50, -0.12, 51.52
    ev = make_event(
        lng=(w + e) / 2,
        lat=(s + n) / 2,
        category=Category.FLOOD,
        radius_m=100,
        geometry={"type": "Polygon", "coordinates": [[[w, s], [e, s], [e, n], [w, n], [w, s]]]},
    )
    weights = cell_weights(ev)
    inside = h3.latlng_to_cell(51.505, -0.145, FINE_RES)  # near the SW corner, far from centroid
    assert weights[inside] == 1.0
    assert len(weights) > 20


def test_cell_scores_fine_and_coarse():
    ev = make_event()
    scores = compute_cell_scores([ev], T0)
    fine = {c.h3: c for c in scores if c.res == FINE_RES}
    coarse = {c.h3: c for c in scores if c.res == COARSE_RES}
    home = h3.latlng_to_cell(ev.lat, ev.lng, FINE_RES)
    assert fine[home].live == pytest.approx(0.4)
    assert fine[home].top_event_ids == [ev.id]
    parent = h3.cell_to_parent(home, COARSE_RES)
    assert 0.2 <= coarse[parent].score < 0.4
    assert ev.id in coarse[parent].top_event_ids


def test_fifty_overlapping_events_stay_bounded():
    scores = compute_cell_scores([make_event(severity=1, confidence=1) for _ in range(50)], T0)
    assert scores and all(0 <= c.score <= 1 for c in scores)


def test_decayed_events_are_dropped_from_scoring():
    assert compute_cell_scores([make_event()], T0 + timedelta(hours=30)) == []


def test_baseline_only_cell():
    cell = h3.latlng_to_cell(SOHO[1], SOHO[0], FINE_RES)
    scores = compute_cell_scores([], T0, baseline={cell: 0.5})
    fine = [c for c in scores if c.res == FINE_RES]
    assert len(fine) == 1 and fine[0].live == 0 and fine[0].score == pytest.approx(0.2)


def test_should_merge_rules():
    a = make_event()
    near = make_event(lng=SOHO[0] + 0.001)  # ~70 m east
    assert should_merge(a, near)
    assert not should_merge(a, make_event(lng=SOHO[0] + 0.02))  # ~1.4 km
    assert not should_merge(a, make_event(occurred_at=T0 + timedelta(hours=4)))
    assert not should_merge(a, make_event(category=Category.FIRE))
    # property and violent crime share a group
    assert should_merge(a, make_event(category=Category.PROPERTY_CRIME))
    # two structured events are matched by external_ref only
    structured = make_event(external_ref="tfl_road:1", mergeable=False)
    assert not should_merge(structured, make_event(external_ref="tfl_road:2", mergeable=False))
    # a news event merges with a structured one, and with another news event that has a ref
    assert should_merge(structured, make_event(external_ref="met_news:1"))
    assert should_merge(make_event(external_ref="bbc_london:1"), make_event(external_ref="met_news:1"))


def test_merge_combines_sources_and_raises_confidence():
    a = make_event(urls=["https://a"], raw_item_ids=[1])
    b = make_event(
        radius_m=100,
        lng=SOHO[0] + 0.001,
        severity=0.9,
        confidence=0.7,
        source_confidence={"met_news": 0.7},
        source_ids=["met_news"],
        urls=["https://b"],
        raw_item_ids=[2],
        occurred_at=T0 - timedelta(minutes=20),
    )
    m = merge(a, b)
    assert m.id == a.id
    assert m.confidence == pytest.approx(1 - 0.5 * 0.3)
    assert m.severity == 0.9
    assert m.radius_m == 100 and m.lng == b.lng
    assert m.source_ids == ["bbc_london", "met_news"]
    assert m.urls == ["https://a", "https://b"]
    assert m.raw_item_ids == [1, 2]
    assert m.occurred_at == b.occurred_at


def test_merge_same_source_does_not_raise_confidence():
    a = make_event()
    assert merge(a, make_event()).confidence == pytest.approx(0.5)
