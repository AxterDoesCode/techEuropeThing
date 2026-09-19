"""Rule-based extraction used when no LLM is configured (LLM_MODEL unset).

It reads the category, subtype, severity, place and incident time from the
headline and description with keyword and pattern matching. It handles one
incident per item and only the common headline forms; the LLM extraction agent
replaces it for everything else.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone

from .models import Category


@dataclass(frozen=True)
class RuleExtraction:
    category: Category
    severity: float
    place_text: str
    subtype: str | None = None
    # incident time when the text states a date, else None
    occurred_at: datetime | None = None
    # With occurred_at None: whether the publication time may be used instead
    is_recent: bool = True
    is_ongoing: bool = False
    suspect_at_large: bool = False
    resolved: bool = False


# REVISIT(severity-scale): anchors from the product owner, not tuned; the same
# scale is in llm.SYSTEM_PROMPT. First match wins, so the most severe patterns
# come first. Columns: pattern, category, subtype, severity.
_CATEGORY_RULES: list[tuple[re.Pattern[str], Category, str | None, float]] = [
    (re.compile(r"\b(terror|marauding|bomb)\w*", re.I), Category.VIOLENT_CRIME, "active_attack", 1.0),
    (re.compile(r"\bexplosion\w*", re.I), Category.FIRE, "explosion", 1.0),
    (re.compile(r"\b(murder|dies|died|death|fatal|killed)\w*", re.I), Category.VIOLENT_CRIME, "homicide", 0.9),
    (re.compile(r"\bstabb\w*", re.I), Category.VIOLENT_CRIME, "stabbing", 0.9),
    (re.compile(r"\b(shoot|shot|firearm)\w*", re.I), Category.VIOLENT_CRIME, "shooting", 0.9),
    (re.compile(r"\bacid\b", re.I), Category.VIOLENT_CRIME, "acid_attack", 0.8),
    (re.compile(r"\b(rape|sexual assault)\w*", re.I), Category.VIOLENT_CRIME, "sexual_assault", 0.8),
    (re.compile(r"\bkidnap\w*", re.I), Category.VIOLENT_CRIME, None, 0.8),
    (re.compile(r"\b(armed robb|knifepoint|gunpoint)\w*", re.I), Category.VIOLENT_CRIME, "robbery", 0.7),
    (re.compile(r"\b(assault|attack)\w*", re.I), Category.VIOLENT_CRIME, "assault", 0.7),
    (re.compile(r"\b(robb|mugg)\w*", re.I), Category.VIOLENT_CRIME, "robbery", 0.5),
    (re.compile(r"\b(riot|disorder)\w*", re.I), Category.DISORDER, "violent_disorder", 0.6),
    (re.compile(r"\b(protest|demonstrat)\w*", re.I), Category.DISORDER, "peaceful_protest", 0.3),
    (re.compile(r"\b(fires?|blaze|firefighters)\b", re.I), Category.FIRE, "fire", 0.2),
    (re.compile(r"\b(collision|crash)\w*", re.I), Category.ROAD_CLOSURE, None, 0.5),
    (re.compile(r"\b(burglar|theft|stolen|fraud|pickpocket)\w*", re.I), Category.PROPERTY_CRIME, "theft", 0.2),
]
# A protest with these words is a tense protest (0.4 instead of 0.3)
_TENSE = re.compile(r"\b(scuffle|clash|police line|tension|tense|stand-?off)\w*", re.I)
# A fire with these words is a large fire with evacuation (0.6 instead of 0.2)
_LARGE_FIRE = re.compile(r"\b(evacuat|major|large|huge)\w*", re.I)

_FALSE_ALARM = re.compile(r"\b(false alarm|all[- ]clear|hoax|no threat)\b", re.I)
_RESOLVED = re.compile(r"\b(arrest\w*|scene (?:has been |was |is )?cleared|cordon (?:has been |was |is )?lifted)", re.I)
_AT_LARGE = re.compile(r"\b(at large|on the run|manhunt|fled the scene)\b", re.I)
_ONGOING = re.compile(r"\b(ongoing|under way|underway|remains? in place|still in place|continu(?:es|ing))\b", re.I)

# Wording that indicates an incident within about a day before publication
_RECENT = re.compile(
    r"\b(today|tonight|yesterday|overnight|last night|this (?:morning|afternoon|evening)|early hours|"
    r"(?:on|last) (?:mon|tues|wednes|thurs|fri|satur|sun)day)\b",
    re.I,
)
# Headline wording of delayed reporting (police appeals are often published months
# after the offence). Such an item is kept only with a stated date or recent wording.
_DELAYED = re.compile(r"\b(appeal|anniversary|years? ago|months? ago|last year|renew|reward)\w*", re.I)

_MONTHS = ["january", "february", "march", "april", "may", "june", "july", "august", "september",
           "october", "november", "december"]
_DATE = re.compile(r"\b(\d{1,2})(?:st|nd|rd|th)? (" + "|".join(_MONTHS) + r")(?: (\d{4}))?\b", re.I)

# "... in Lloyd Baker Street, Clerkenwell", "... at Brixton station"
_PLACE = re.compile(
    r"\b(?:in|at|on|near|outside)\s+((?:[A-Z][\w'’.-]*)(?:\s+(?:[A-Z][\w'’.-]*|of|the|upon))*"
    r"(?:,\s*[A-Z][\w'’.-]*(?:\s+[A-Z][\w'’.-]*)*)?)"
)
_NOT_PLACES = {"London", "Met", "Metropolitan Police", "UK", "England", "Court", "Crown Court"}


def stated_date(text: str, published_at: datetime | None) -> datetime | None:
    """The first "1 August 2024" style date in the text, at 00:00 UTC. A date
    without a year is the latest such date that is not after the publication."""
    m = _DATE.search(text)
    if m is None:
        return None
    reference = published_at or datetime.now(timezone.utc)
    day, month = int(m.group(1)), _MONTHS.index(m.group(2).lower()) + 1
    try:
        if m.group(3):
            return datetime(int(m.group(3)), month, day, tzinfo=timezone.utc)
        found = datetime(reference.year, month, day, tzinfo=timezone.utc)
        return found if found <= reference else found.replace(year=reference.year - 1)
    except ValueError:
        return None


def extract(title: str, description: str = "", published_at: datetime | None = None) -> RuleExtraction | None:
    text = f"{title}. {description}"
    if _FALSE_ALARM.search(text):
        return None
    rule = next((r for r in _CATEGORY_RULES if r[0].search(text)), None)
    if rule is None:
        return None
    _, category, subtype, severity = rule
    if subtype == "peaceful_protest" and _TENSE.search(text):
        subtype, severity = "tense_protest", 0.4
    if subtype == "fire" and _LARGE_FIRE.search(text):
        severity = 0.6
    occurred_at = stated_date(text, published_at)
    is_recent = bool(_RECENT.search(text)) or not _DELAYED.search(title)
    for source in (title, description):
        for m in _PLACE.finditer(source):
            place = m.group(1).strip(" .,")
            if place not in _NOT_PLACES and len(place) > 3:
                return RuleExtraction(
                    category=category,
                    severity=severity,
                    place_text=place,
                    subtype=subtype,
                    occurred_at=occurred_at,
                    is_recent=is_recent,
                    is_ongoing=bool(_ONGOING.search(text)),
                    suspect_at_large=bool(_AT_LARGE.search(text)),
                    resolved=bool(_RESOLVED.search(text)),
                )
    return None
