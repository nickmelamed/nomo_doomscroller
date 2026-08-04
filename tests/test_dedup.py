import logging
from datetime import date

import main
import state as state_module
from models import Candidate, Criteria, Entity, NewsItem, RejectedCandidate, SourceData


def candidate(name: str, **overrides) -> Candidate:
    fields = {
        "name": name,
        "suggested_type": "Rewards partner prospect",
        "region": "US",
        "why_fits": "Fits the criteria.",
        "source_url": "https://example.com/news",
    }
    fields.update(overrides)
    return Candidate(**fields)


def news_item(headline: str, url: str, **overrides) -> NewsItem:
    fields = {
        "headline": headline,
        "url": url,
        "source": "TechCrunch",
        "published": "2026-07-26",
        "summary": "Summary.",
        "why_it_matters": "Matters.",
        "relevance": "high",
    }
    fields.update(overrides)
    return NewsItem(**fields)


UBER_WATCHLIST = Entity(
    name="Uber",
    type="Competitor",
    status="Active",
    aliases=["Uber One", "Uber Technologies"],
    source="watchlist",
)

FEVER_PARTNER = Entity(
    name="Fever",
    type="Existing partner",
    status="Active",
    source="partners_db",
)


def make_source_data(entities=None, excluded_names=None) -> SourceData:
    return SourceData(
        entities=entities or [UBER_WATCHLIST],
        excluded_names=excluded_names or {"Fever"},
        criteria=Criteria(),
    )


def test_exact_name_match_is_dropped(caplog):
    source_data = make_source_data()
    candidates = [candidate("Uber")]

    with caplog.at_level(logging.INFO):
        survivors = main.filter_candidates(candidates, source_data)

    assert survivors == []
    assert "Uber" in caplog.text


def test_alias_variant_match_is_dropped():
    source_data = make_source_data()
    candidates = [candidate("Uber Technologies")]

    survivors = main.filter_candidates(candidates, source_data)
    assert survivors == []


def test_corporate_suffix_variant_is_dropped():
    source_data = make_source_data()
    candidates = [candidate("Uber, Inc.")]

    survivors = main.filter_candidates(candidates, source_data)
    assert survivors == []


def test_distinct_name_survives_no_false_positive():
    source_data = make_source_data()
    candidates = [candidate("Lyft")]

    survivors = main.filter_candidates(candidates, source_data)
    assert len(survivors) == 1
    assert survivors[0].name == "Lyft"


def test_partners_source_only_exclusion_no_watchlist_row():
    # Fever is Active in the Partners source but has NO corresponding
    # Watchlist row (source_data.entities only has Uber) — must still exclude.
    source_data = make_source_data(entities=[UBER_WATCHLIST], excluded_names={"Fever"})
    candidates = [candidate("Fever")]

    survivors = main.filter_candidates(candidates, source_data)
    assert survivors == []


def test_duplicate_candidate_within_batch_is_dropped(caplog):
    # Different scout angles frequently rediscover the same real entity —
    # Stage 4 must dedupe candidates against each other, not just against
    # already-tracked/excluded names.
    source_data = make_source_data()
    candidates = [
        candidate("Dayo (Dayo Deals)", source_url="https://example.com/a"),
        candidate("Dayo (Dayo Deals)", source_url="https://example.com/b"),
    ]

    with caplog.at_level(logging.INFO):
        survivors = main.filter_candidates(candidates, source_data)

    assert len(survivors) == 1
    assert survivors[0].source_url == "https://example.com/a"
    assert "duplicate of already-surfaced candidate" in caplog.text


def test_fuzzy_duplicate_candidate_within_batch_is_dropped():
    source_data = make_source_data()
    candidates = [candidate("Dayo Deals, Inc."), candidate("Dayo Deals")]

    survivors = main.filter_candidates(candidates, source_data)
    assert len(survivors) == 1


def test_multiple_candidates_mixed_survival():
    source_data = make_source_data()
    candidates = [candidate("Uber"), candidate("Fever"), candidate("Lyft")]

    survivors = main.filter_candidates(candidates, source_data)
    assert [c.name for c in survivors] == ["Lyft"]


def test_low_confidence_candidate_is_dropped(caplog):
    source_data = make_source_data()
    candidates = [candidate("Lyft", confidence="low")]

    with caplog.at_level(logging.INFO):
        survivors = main.filter_candidates(candidates, source_data)

    assert survivors == []
    assert "low confidence" in caplog.text


def test_high_and_medium_confidence_candidates_survive():
    source_data = make_source_data()
    candidates = [
        candidate("Lyft", confidence="high"),
        candidate("Bolt", confidence="medium"),
        candidate("Grab", confidence=None),
    ]

    survivors = main.filter_candidates(candidates, source_data)
    assert {c.name for c in survivors} == {"Lyft", "Bolt", "Grab"}


def test_dedup_news_items_drops_exact_duplicate_url():
    items = [
        news_item("Uber One expands rewards", "https://example.com/a"),
        news_item("Uber One expands rewards program", "https://example.com/a"),
    ]

    survivors = main.dedup_news_items(items)
    assert len(survivors) == 1


def test_dedup_news_items_drops_near_duplicate_headline_different_url():
    items = [
        news_item("Uber One expands ticketing rewards", "https://example.com/a"),
        news_item("Uber One expands its ticketing rewards", "https://example.com/b"),
    ]

    survivors = main.dedup_news_items(items)
    assert len(survivors) == 1
    assert survivors[0].url == "https://example.com/a"


def test_dedup_news_items_keeps_distinct_items():
    items = [
        news_item("Uber One expands ticketing rewards", "https://example.com/a"),
        news_item("Lyft announces new loyalty program", "https://example.com/b"),
    ]

    survivors = main.dedup_news_items(items)
    assert len(survivors) == 2


def test_dedup_news_items_empty_list():
    assert main.dedup_news_items([]) == []


def rejected_candidate(name: str, **overrides) -> RejectedCandidate:
    fields = {
        "name": name,
        "suggested_type": "Competitor",
        "region": "US",
        "why_fits": "Fits the criteria.",
        "source_url": "https://example.com/news",
        "reason": "Weak fit.",
    }
    fields.update(overrides)
    return RejectedCandidate(**fields)


def rejected_state_with(*entries: tuple[RejectedCandidate, str]) -> dict:
    """Builds rejected-candidates state via the real upsert path so entries
    have realistic first_rejected/last_rejected/last_shown shapes."""
    state = {}
    for candidate, rejected_on in entries:
        state = state_module.upsert_rejected_candidate(
            state, candidate, today=date.fromisoformat(rejected_on)
        )
    return state


def test_exclude_previously_rejected_drops_suppressed_candidate():
    rejected_state = rejected_state_with((rejected_candidate("Bolt"), "2026-07-25"))
    candidates = [candidate("Bolt", suggested_type="Competitor")]

    survivors = main.exclude_previously_rejected(candidates, rejected_state, today=date(2026, 7, 30))

    assert survivors == []


def test_exclude_previously_rejected_keeps_candidate_past_suppression_window():
    rejected_state = rejected_state_with((rejected_candidate("Bolt"), "2026-07-01"))
    candidates = [candidate("Bolt", suggested_type="Competitor")]

    survivors = main.exclude_previously_rejected(candidates, rejected_state, today=date(2026, 7, 30))

    assert len(survivors) == 1
    assert survivors[0].name == "Bolt"


def test_exclude_previously_rejected_keeps_unrelated_candidate():
    rejected_state = rejected_state_with((rejected_candidate("Bolt"), "2026-07-25"))
    candidates = [candidate("Lyft", suggested_type="Competitor")]

    survivors = main.exclude_previously_rejected(candidates, rejected_state, today=date(2026, 7, 30))

    assert len(survivors) == 1
    assert survivors[0].name == "Lyft"


def test_build_reconsider_only_fills_empty_categories():
    rejected_state = rejected_state_with(
        (rejected_candidate("Bolt", suggested_type="Competitor"), "2026-07-25"),
        (rejected_candidate("Dayo", suggested_type="Rewards partner prospect"), "2026-07-25"),
    )
    new_candidates = [candidate("Grab", suggested_type="Competitor")]  # Competitor not empty

    reconsider, _ = main.build_reconsider(new_candidates, rejected_state, today=date(2026, 7, 30))

    assert [c.name for c in reconsider] == ["Dayo"]


def test_build_reconsider_caps_at_three_per_category():
    rejected_state = rejected_state_with(
        (rejected_candidate("A", suggested_type="Competitor"), "2026-07-25"),
        (rejected_candidate("B", suggested_type="Competitor"), "2026-07-25"),
        (rejected_candidate("C", suggested_type="Competitor"), "2026-07-25"),
        (rejected_candidate("D", suggested_type="Competitor"), "2026-07-25"),
    )

    reconsider, _ = main.build_reconsider([], rejected_state, today=date(2026, 7, 30))

    assert len(reconsider) == 3


def test_build_reconsider_prefers_never_shown_then_marks_shown():
    rejected_state = rejected_state_with(
        (rejected_candidate("A", suggested_type="Competitor"), "2026-07-25"),
    )
    # B was already shown once — A (never shown) should be preferred.
    rejected_state = state_module.mark_shown(rejected_state, "A", today=date(2026, 7, 20))
    rejected_state = state_module.upsert_rejected_candidate(
        rejected_state, rejected_candidate("B", suggested_type="Competitor"), today=date(2026, 7, 26)
    )

    reconsider, updated_state = main.build_reconsider([], rejected_state, today=date(2026, 7, 30))

    assert [c.name for c in reconsider] == ["B", "A"]
    assert updated_state["b"]["last_shown"] == "2026-07-30"
    assert updated_state["b"]["shown_count"] == 1
    assert updated_state["a"]["shown_count"] == 2  # was already shown once before this pick


def test_build_reconsider_no_rejected_candidates_for_empty_category():
    reconsider, updated_state = main.build_reconsider([], {}, today=date(2026, 7, 30))

    assert reconsider == []
    assert updated_state == {}
