from dataclasses import asdict

from models import Candidate, Criteria, Digest, DigestItem, Entity, NewsItem


def test_entity_matches_watchlist_schema_sec6_1():
    entity = Entity(
        name="Uber",
        type="Competitor",
        status="Active",
        category=[],
        region=["US", "BR"],
        aliases=["Uber One", "Uber Technologies", "Uber Rewards"],
        source_url="https://uber.com/press",
        why_tracked="Direct competitor for youth loyalty spend.",
        priority="High",
        added_by="Jane Doe",
        date_added="2026-01-01",
        source="watchlist",
    )
    assert asdict(entity)["aliases"] == ["Uber One", "Uber Technologies", "Uber Rewards"]


def test_entity_partners_db_row_sec6_3():
    entity = Entity(
        name="Fever",
        type="Partner prospect",
        status="Active",
        region=["US"],
        why_tracked="Active reward partner — live-events redemption.",
        source="partners_db",
    )
    assert entity.source == "partners_db"
    assert entity.priority is None


def test_news_item_monitoring_output_sec8_1():
    item = NewsItem(
        headline="Uber One expands ticketing rewards",
        url="https://example.com/article",
        source="TechCrunch",
        published="2026-07-26",
        summary="One-sentence factual summary.",
        why_it_matters="One sentence tied to NOMO.",
        relevance="high",
        entity="Uber",
    )
    assert item.entity == "Uber"
    assert item.topic is None


def test_news_item_industry_trends_output_sec8_1b():
    item = NewsItem(
        headline="State proposes age-verification rule for social apps",
        url="https://example.com/article",
        source="Reuters",
        published="2026-07-27",
        summary="One-sentence factual summary.",
        why_it_matters="One sentence tied to NOMO's positioning or partner interests.",
        relevance="high",
        topic="youth social media policy",
    )
    assert item.topic == "youth social media policy"
    assert item.entity is None


def test_candidate_scouting_output_sec8_2():
    candidate = Candidate(
        name="ExampleCo",
        suggested_type="Partner prospect",
        category="travel",
        region="BR",
        why_fits="One sentence against the criteria.",
        source_url="https://example.com/news",
        confidence="medium",
    )
    assert asdict(candidate)["category"] == "travel"


def test_candidate_new_candidates_trimmed_shape_sec8_3():
    # §8.3's new_candidates example omits category/confidence.
    candidate = Candidate(
        name="ExampleCo",
        suggested_type="Partner prospect",
        region="BR",
        why_fits="...",
        source_url="...",
    )
    assert candidate.category is None
    assert candidate.confidence is None


def test_digest_synthesis_output_sec8_3():
    digest = Digest(
        quiet_day=False,
        competition=[
            DigestItem(headline="...", url="...", source="...", summary="...")
        ],
        industry=[DigestItem(headline="...", url="...", source="...", summary="...")],
        partner_prospects=[
            DigestItem(headline="...", url="...", source="...", summary="...")
        ],
        new_candidates=[
            Candidate(
                name="ExampleCo",
                suggested_type="Partner prospect",
                region="BR",
                why_fits="...",
                source_url="...",
            )
        ],
        tracking_counts={"competitors": 12, "partner_prospects": 8},
    )
    payload = asdict(digest)
    assert payload["tracking_counts"] == {"competitors": 12, "partner_prospects": 8}
    assert payload["quiet_day"] is False
    assert len(payload["new_candidates"]) == 1


def test_digest_quiet_day_defaults_to_empty_sections():
    digest = Digest(quiet_day=True, tracking_counts={"competitors": 5, "partner_prospects": 3})
    assert digest.competition == []
    assert digest.industry == []
    assert digest.partner_prospects == []
    assert digest.new_candidates == []


def test_criteria_six_sections_sec6_2():
    criteria = Criteria(
        nomo_context="NOMO is a rewards app for teens...",
        region_weighting="BR is primary; US and UK are growing.",
        competitor_criteria="Anything competing for youth loyalty attention.",
        partner_criteria="Has tradeable reward inventory and audience fit.",
        industry_topics=["youth social media policy", "rewards-fintech funding"],
        do_not_suggest=["Meta", "TikTok"],
    )
    assert criteria.industry_topics == ["youth social media policy", "rewards-fintech funding"]
    assert criteria.do_not_suggest == ["Meta", "TikTok"]


def test_criteria_defaults_are_empty_not_none():
    criteria = Criteria()
    assert criteria.nomo_context == ""
    assert criteria.industry_topics == []
    assert criteria.do_not_suggest == []
