"""The backend-agnostic contract both sources/*.py implementations satisfy (§6.0).

Holds the pieces that are genuinely shared between backends: the SourceBackend
protocol, column-map validation (structure resilience), and the criteria-section
parser (§6.2) — written and tested once, not duplicated per backend. Each
backend's own column-name map stays local to that backend's module, since it's
the single point of contact with that backend's outside structure.
"""

from __future__ import annotations

import logging
from difflib import SequenceMatcher
from typing import Protocol

from models import Criteria, SourceData

logger = logging.getLogger(__name__)

_SECTION_MATCH_THRESHOLD = 0.8


class SourceBackend(Protocol):
    def load_all(self) -> SourceData:
        """Returns entities, excluded_names, reward_landscape, industry_topics,
        and parsed criteria — identical shape regardless of backend."""
        ...


class MissingRequiredFieldError(RuntimeError):
    """Raised when a required column/property is absent from a source (§6.0)."""


def check_columns(
    present_headers: set[str],
    column_map: dict[str, str | list[str] | dict[str, str]],
    required_keys: set[str],
    source_name: str,
) -> None:
    """Validates a backend's column-name map against what's actually present.

    A missing required field fails loudly, naming the field and source. A
    missing optional field logs a warning and lets the caller fall back to its
    documented default — never guesses silently (§6.0). A dict-valued entry
    (e.g. region_flags mapping raw header -> canonical tag) is checked by its
    keys, since those are the actual headers expected in the sheet/DB.
    """
    for key, header in column_map.items():
        if isinstance(header, dict):
            headers = list(header.keys())
        elif isinstance(header, list):
            headers = header
        else:
            headers = [header]
        missing = [h for h in headers if h not in present_headers]
        if not missing:
            continue
        if key in required_keys:
            raise MissingRequiredFieldError(
                f"{source_name}: required field {key!r} not found "
                f"(expected column/property {missing})"
            )
        logger.warning(
            "%s: optional field %r not found (expected %s) — falling back to documented default",
            source_name,
            key,
            missing,
        )


# §6.2 — the six canonical Criteria sections, field name -> the label the team
# is expected to author as a heading/Section value.
CRITERIA_SECTION_LABELS = {
    "nomo_context": "NOMO context",
    "region_weighting": "Region weighting",
    "competitor_criteria": "Competitor criteria",
    "reward_partner_criteria": "Rewards partners criteria",
    "gtm_partner_criteria": "GTM partners criteria",
    "do_not_suggest": "Do-not-suggest",
}


def _normalize_header(text: str) -> str:
    return text.strip().rstrip(":").casefold()


def _match_section(raw_header: str) -> str | None:
    normalized = _normalize_header(raw_header)

    for field_name, label in CRITERIA_SECTION_LABELS.items():
        if normalized == _normalize_header(label):
            return field_name

    best_field, best_ratio = None, 0.0
    for field_name, label in CRITERIA_SECTION_LABELS.items():
        ratio = SequenceMatcher(None, normalized, _normalize_header(label)).ratio()
        if ratio > best_ratio:
            best_field, best_ratio = field_name, ratio

    return best_field if best_ratio >= _SECTION_MATCH_THRESHOLD else None


def split_list_field(text: str) -> list[str]:
    """Splits a free-text block into one entry per non-empty line, stripping
    leading bullet/dash markers. Used for do_not_suggest (§6.2) and reused by
    gather.py to parse GTM target verticals out of the gtm_partner_criteria
    section."""
    entries = []
    for raw_line in text.splitlines():
        line = raw_line.strip().lstrip("-*•").strip()
        if line:
            entries.append(line)
    return entries


def parse_criteria(sections: dict[str, str]) -> Criteria:
    """Builds a typed Criteria from a {raw section header: text} dict.

    Both backends extract this flat dict from their native format (Notion page
    blocks vs. sheet rows) and hand it here — the only place section-name
    matching and missing-section fallback/warning logic lives (§6.2). A header
    that doesn't match any canonical section (typo-tolerant via a difflib
    ratio) is logged and ignored; a canonical section that's never matched
    defaults to empty rather than raising, since a malformed heading on an
    otherwise-reachable page/tab shouldn't abort the run.
    """
    matched: dict[str, str] = {}
    for raw_header, text in sections.items():
        field_name = _match_section(raw_header)
        if field_name is None:
            logger.warning("criteria: unrecognized section header %r — ignoring", raw_header)
            continue
        matched[field_name] = text.strip()

    for field_name, label in CRITERIA_SECTION_LABELS.items():
        if field_name not in matched:
            logger.warning("criteria: section %r missing — using empty default", label)

    return Criteria(
        nomo_context=matched.get("nomo_context", ""),
        region_weighting=matched.get("region_weighting", ""),
        competitor_criteria=matched.get("competitor_criteria", ""),
        reward_partner_criteria=matched.get("reward_partner_criteria", ""),
        gtm_partner_criteria=matched.get("gtm_partner_criteria", ""),
        do_not_suggest=split_list_field(matched.get("do_not_suggest", "")),
    )
