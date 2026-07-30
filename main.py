"""Orchestrator: runs the pipeline end to end. See SPEC.md §7.

Currently holds Stage 4 (dedup & filter) only — full orchestration (Stages
1-6) is wired up in a later phase.
"""

from __future__ import annotations

import logging
import re
from difflib import SequenceMatcher

from gather import known_names
from models import Candidate, NewsItem, SourceData

logger = logging.getLogger(__name__)

NAME_MATCH_THRESHOLD = 0.85
# Looser than name matching — two outlets phrase the same story differently.
HEADLINE_MATCH_THRESHOLD = 0.80

_NAME_SUFFIXES = (" inc", " llc", " ltd", " corp", " co")


def _normalize_name(name: str) -> str:
    text = name.casefold().strip()
    text = re.sub(r"[.,]", "", text)
    for suffix in _NAME_SUFFIXES:
        if text.endswith(suffix):
            text = text[: -len(suffix)]
    return re.sub(r"\s+", " ", text).strip()


def _normalize_headline(text: str) -> str:
    normalized = text.casefold().strip()
    normalized = re.sub(r"[^\w\s]", "", normalized)
    return re.sub(r"\s+", " ", normalized).strip()


def _fuzzy_match(a: str, b: str, threshold: float) -> bool:
    if a == b:
        return True
    return SequenceMatcher(None, a, b).ratio() >= threshold


def _names_match(a: str, b: str) -> bool:
    return _fuzzy_match(_normalize_name(a), _normalize_name(b), NAME_MATCH_THRESHOLD)


def _headlines_match(a: str, b: str) -> bool:
    return _fuzzy_match(_normalize_headline(a), _normalize_headline(b), HEADLINE_MATCH_THRESHOLD)


def filter_candidates(candidates: list[Candidate], source_data: SourceData) -> list[Candidate]:
    """§7 Stage 4: drop candidates whose name fuzzy-matches anything already
    tracked (Watchlist + aliases) or excluded (Excluded/Paused/Converted
    watchlist rows, Active Partners-source rows)."""
    known = known_names(source_data)

    survivors = []
    for candidate in candidates:
        match = next((name for name in known if _names_match(candidate.name, name)), None)
        if match is not None:
            logger.info(
                "dedup: dropping candidate %r — matches already-known name %r",
                candidate.name,
                match,
            )
            continue
        survivors.append(candidate)

    logger.info(
        "dedup: %d candidates in, %d survived filtering", len(candidates), len(survivors)
    )
    return survivors


def dedup_news_items(items: list[NewsItem]) -> list[NewsItem]:
    """§7 Stage 4: dedup entity monitoring + industry trend items together, by
    URL first, then near-duplicate headline (looser ratio, since outlets
    phrase the same story differently)."""
    seen_urls: set[str] = set()
    survivors: list[NewsItem] = []

    for item in items:
        if item.url in seen_urls:
            logger.info("dedup: dropping item %r — duplicate URL", item.headline)
            continue
        duplicate = next(
            (
                existing
                for existing in survivors
                if _headlines_match(item.headline, existing.headline)
            ),
            None,
        )
        if duplicate is not None:
            logger.info(
                "dedup: dropping item %r — near-duplicate of %r",
                item.headline,
                duplicate.headline,
            )
            continue
        seen_urls.add(item.url)
        survivors.append(item)

    logger.info("dedup: %d items in, %d survived deduplication", len(items), len(survivors))
    return survivors
