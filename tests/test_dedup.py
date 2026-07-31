import logging

import main
from models import Candidate, Criteria, Entity, NewsItem, SourceData


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
