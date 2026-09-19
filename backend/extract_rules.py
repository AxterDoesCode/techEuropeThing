"""Rule-based extraction used when no LLM is configured (LLM_MODEL unset).

It reads the category, severity and place from the headline with keyword and
pattern matching. It handles one incident per item and only the common headline
forms; the LLM extraction agent replaces it for everything else.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .models import Category


@dataclass(frozen=True)
class RuleExtraction:
    category: Category
    severity: float
    place_text: str


# First match wins, so the most severe patterns come first
_CATEGORY_RULES: list[tuple[re.Pattern[str], Category, float]] = [
    (re.compile(r"\b(murder|dies|died|death|fatal|killed)\w*", re.I), Category.VIOLENT_CRIME, 0.95),
    (re.compile(r"\b(stabb|shoot|shot|acid|firearm)\w*", re.I), Category.VIOLENT_CRIME, 0.9),
    (re.compile(r"\b(rape|sexual assault|kidnap)\w*", re.I), Category.VIOLENT_CRIME, 0.85),
    (re.compile(r"\b(assault|attack|robber|mugg)\w*", re.I), Category.VIOLENT_CRIME, 0.7),
    (re.compile(r"\b(disorder|protest|riot)\w*", re.I), Category.DISORDER, 0.6),
    (re.compile(r"\b(fire|explosion|blaze)\w*", re.I), Category.FIRE, 0.7),
    (re.compile(r"\b(collision|crash)\w*", re.I), Category.ROAD_CLOSURE, 0.5),
    (re.compile(r"\b(burglar|theft|stolen|fraud)\w*", re.I), Category.PROPERTY_CRIME, 0.4),
]

# "... in Lloyd Baker Street, Clerkenwell", "... at Brixton station"
_PLACE = re.compile(
    r"\b(?:in|at|on|near|outside)\s+((?:[A-Z][\w'’.-]*)(?:\s+(?:[A-Z][\w'’.-]*|of|the|upon))*"
    r"(?:,\s*[A-Z][\w'’.-]*(?:\s+[A-Z][\w'’.-]*)*)?)"
)
_NOT_PLACES = {"London", "Met", "Metropolitan Police", "UK", "England", "Court", "Crown Court"}


def extract(title: str, description: str = "") -> RuleExtraction | None:
    text = f"{title}. {description}"
    rule = next(((c, s) for p, c, s in _CATEGORY_RULES if p.search(text)), None)
    if rule is None:
        return None
    for source in (title, description):
        for m in _PLACE.finditer(source):
            place = m.group(1).strip(" .,")
            if place not in _NOT_PLACES and len(place) > 3:
                return RuleExtraction(rule[0], rule[1], place)
    return None
