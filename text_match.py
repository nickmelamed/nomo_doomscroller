"""Fuzzy name/headline normalization and matching, shared by main.py's Stage 4
dedup/filter logic and state.py's cross-day story hashing."""

from __future__ import annotations

import re
from difflib import SequenceMatcher

NAME_MATCH_THRESHOLD = 0.85
# Looser than name matching — two outlets phrase the same story differently.
HEADLINE_MATCH_THRESHOLD = 0.80

_NAME_SUFFIXES = (" inc", " llc", " ltd", " corp", " co")


def normalize_name(name: str) -> str:
    text = name.casefold().strip()
    text = re.sub(r"[.,]", "", text)
    for suffix in _NAME_SUFFIXES:
        if text.endswith(suffix):
            text = text[: -len(suffix)]
    return re.sub(r"\s+", " ", text).strip()


def normalize_headline(text: str) -> str:
    normalized = text.casefold().strip()
    normalized = re.sub(r"[^\w\s]", "", normalized)
    return re.sub(r"\s+", " ", normalized).strip()


def fuzzy_match(a: str, b: str, threshold: float) -> bool:
    if a == b:
        return True
    return SequenceMatcher(None, a, b).ratio() >= threshold


def names_match(a: str, b: str) -> bool:
    return fuzzy_match(normalize_name(a), normalize_name(b), NAME_MATCH_THRESHOLD)


def headlines_match(a: str, b: str) -> bool:
    return fuzzy_match(normalize_headline(a), normalize_headline(b), HEADLINE_MATCH_THRESHOLD)
