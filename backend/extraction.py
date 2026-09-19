"""Article -> events, for every unstructured source.

Order: pre-filter, then the LLM agent when LLM_MODEL is set, otherwise the
rule-based extractor. An article whose LLM run fails is processed by the rules.
"""

from __future__ import annotations

import logging

from . import extract_rules, llm
from .geocode import geocode
from .located import Article, located_event
from .models import Event
from .prefilter import is_incident_candidate

log = logging.getLogger(__name__)

# LLM runs that raised (model error, usage limit, timeout) since process start
llm_failures = 0


def extract_events(
    article: Article,
    source_id: str,
    source_type: str,
    lookup: llm.SimilarEventLookup | None = None,
) -> tuple[list[Event], int]:
    """Returns the events and the number of LLM requests made for this article."""
    global llm_failures
    if not is_incident_candidate(article.title, article.description):
        return [], 0
    if not llm.is_configured():
        return extract_with_rules(article, source_id, source_type), 0

    deps = llm.ExtractionDeps(article=article, geocoder=geocode, lookup=lookup)
    extracted, usage, error = llm.run_counted(deps)
    if extracted is None:
        llm_failures += 1
        log.warning("LLM extraction failed for %s:%s (%r); using rules", source_id, article.guid, error)
        return extract_with_rules(article, source_id, source_type), usage.requests
    return llm.to_events(extracted, deps, source_id, source_type), usage.requests


def extract_with_rules(article: Article, source_id: str, source_type: str) -> list[Event]:
    extracted = extract_rules.extract(article.title, article.description)
    if extracted is None:
        return []
    place = geocode(extracted.place_text)
    if place is None:
        return []
    ev = located_event(
        article=article,
        source_id=source_id,
        source_type=source_type,
        external_ref=f"{source_id}:{article.guid}",
        category=extracted.category,
        title=article.title,
        summary=article.description,
        severity=extracted.severity,
        place=place,
    )
    return [ev] if ev else []
