"""Typed data model for the NOMO Doomscroller pipeline. See SPEC.md §6, §8."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Entity:
    """A tracked row — either a Watchlist DB row (§6.1) or a Partners DB row (§6.3)."""

    name: str
    type: str  # "Competitor" | "Rewards partner prospect" | "GTM partner prospect" | "Excluded"
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
class RejectedCandidate:
    """A scouted candidate that synthesis rejected (§7 Stage 5) — persisted
    across runs (state.rejected_candidates) so it can be suppressed from
    re-entering synthesis while the rejection is fresh, become eligible again
    once it goes stale, and be offered back for manual reconsideration when a
    digest section comes up empty."""

    name: str
    suggested_type: str
    region: str
    why_fits: str
    source_url: str
    reason: str
    category: str | None = None
    confidence: str | None = None
    # Data-collection counters (not rendered yet): how many times this
    # candidate has been re-scouted and rejected again, and how many times
    # it's been surfaced in a reconsider block — signal for whether the
    # suppression/retention windows are calibrated right and whether the
    # reconsider list is actually getting looked at.
    reject_count: int = 0
    shown_count: int = 0


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
    gtm_prospects: list[DigestItem] = field(default_factory=list)
    new_candidates: list[Candidate] = field(default_factory=list)
    # Bookkeeping for main.py, not rendered directly: today's rejections
    # (matched back to full candidate data) feed the persisted
    # rejected-candidates state; reconsider is populated by main.py from that
    # state, for suggested_types where new_candidates came up empty.
    rejected_today: list[RejectedCandidate] = field(default_factory=list)
    reconsider: list[RejectedCandidate] = field(default_factory=list)
    tracking_counts: dict[str, int] = field(default_factory=dict)


@dataclass
class WeeklyRollup:
    """v2 Phase 19 — the weekly "what you missed" rollup, built from a week
    of already-archived daily Digest JSON rather than raw gathered items."""

    week_of: str
    competition: list[DigestItem] = field(default_factory=list)
    industry: list[DigestItem] = field(default_factory=list)
    partner_prospects: list[DigestItem] = field(default_factory=list)
    gtm_prospects: list[DigestItem] = field(default_factory=list)
    notable_candidates: list[Candidate] = field(default_factory=list)
    # Populated deterministically by weekly_main.py from the persisted
    # rejected-candidates state (not by the LLM) — no extra token cost.
    rejected_candidates: list[RejectedCandidate] = field(default_factory=list)
    themes: list[str] = field(default_factory=list)


@dataclass
class Criteria:
    """The parsed criteria/config source (§6.2) — six sections; industry topics
    live separately on SourceData (§6.3), not here."""

    nomo_context: str = ""
    region_weighting: str = ""
    competitor_criteria: str = ""
    reward_partner_criteria: str = ""
    gtm_partner_criteria: str = ""
    do_not_suggest: list[str] = field(default_factory=list)


@dataclass
class IndustryTopic:
    """A standing monitoring topic (§6.3), independent of any tracked entity."""

    topic: str
    notes: str = ""


@dataclass
class SourceData:
    """The backend-agnostic contract both sources/*.py implementations return (§6.0)."""

    entities: list[Entity] = field(default_factory=list)
    excluded_names: set[str] = field(default_factory=set)
    reward_landscape: list[str] = field(default_factory=list)
    gtm_landscape: list[str] = field(default_factory=list)
    industry_topics: list[IndustryTopic] = field(default_factory=list)
    criteria: Criteria = field(default_factory=Criteria)
