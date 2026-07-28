"""Typed data model for the NOMO Doomscroller pipeline. See SPEC.md §6, §8."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Entity:
    """A tracked row — either a Watchlist DB row (§6.1) or a Partners DB row (§6.3)."""

    name: str
    type: str  # "Competitor" | "Partner prospect" | "Excluded"
    status: str  # "Active" | "Paused" | "Converted"
    category: list[str] = field(default_factory=list)
    region: list[str] = field(default_factory=list)
    aliases: list[str] = field(default_factory=list)
    source_url: str | None = None
    why_tracked: str = ""
    priority: str | None = None  # "High" | "Medium" | "Low"
    added_by: str | None = None
    date_added: str | None = None
    source: str = "watchlist"  # "watchlist" | "partners_db" — internal provenance


@dataclass
class NewsItem:
    """A monitoring (§8.1) or industry trends (§8.1b) result item."""

    headline: str
    url: str
    source: str
    published: str
    summary: str
    why_it_matters: str
    relevance: str  # "high" | "medium" | "low"
    entity: str | None = None  # set for monitoring items
    topic: str | None = None  # set for industry trend items


@dataclass
class Candidate:
    """A scouted candidate (§8.2), also used (trimmed) for §8.3 new_candidates."""

    name: str
    suggested_type: str
    region: str
    why_fits: str
    source_url: str
    category: str | None = None
    confidence: str | None = None


@dataclass
class DigestItem:
    """A rendered digest entry (§8.3 competition/industry/partner_prospects, §9)."""

    headline: str
    url: str
    source: str
    summary: str


@dataclass
class Digest:
    """The final synthesis output (§8.3)."""

    quiet_day: bool
    competition: list[DigestItem] = field(default_factory=list)
    industry: list[DigestItem] = field(default_factory=list)
    partner_prospects: list[DigestItem] = field(default_factory=list)
    new_candidates: list[Candidate] = field(default_factory=list)
    tracking_counts: dict[str, int] = field(default_factory=dict)


@dataclass
class Criteria:
    """The parsed criteria/config page (§6.2)."""

    nomo_context: str = ""
    region_weighting: str = ""
    competitor_criteria: str = ""
    partner_criteria: str = ""
    industry_topics: list[str] = field(default_factory=list)
    do_not_suggest: list[str] = field(default_factory=list)
