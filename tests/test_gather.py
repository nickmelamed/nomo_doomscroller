import json
import logging
from dataclasses import replace
from types import SimpleNamespace

import gather
from config import Config
from models import Criteria, Entity, IndustryTopic, SourceData

TEST_CONFIG = Config(
    anthropic_api_key="dummy",
    anthropic_model="claude-sonnet-5",
    data_source="sheets",
    slack_webhook_url="dummy",
    news_window_hours=24,
    max_items_per_section=6,
    monitor_existing_partners=False,
    monitor_max_uses=3,
    scout_max_uses=8,
    synthesis_verbose_log=False,
    github_repo=None,
    google_sheets_id=None,
    google_service_account_json=None,
    sheets_url=None,
    notion_api_key=None,
    notion_watchlist_db_id=None,
    notion_criteria_page_id=None,
    notion_topics_db_id=None,
    notion_partners_db_id=None,
    notion_gtm_partners_db_id=None,
    notion_db_url=None,
)

TEST_CRITERIA = Criteria(
    nomo_context="NOMO is a rewards app for teens.",
    region_weighting="BR is primary.",
    competitor_criteria="Competes for youth loyalty attention.",
    reward_partner_criteria="Has tradeable reward inventory.",
    gtm_partner_criteria="",
    do_not_suggest=["Meta"],
)


def text_block(text: str):
    return SimpleNamespace(type="text", text=text)


def server_tool_block():
    return SimpleNamespace(type="server_tool_use", text=None)


class FakeMessages:
    """Queues canned responses (or exceptions) returned in call order."""

    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        response = self._responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return SimpleNamespace(
            content=[server_tool_block(), text_block(response)], stop_reason="end_turn"
        )


class FakeClient:
    def __init__(self, responses):
        self.messages = FakeMessages(responses)


UBER = Entity(
    name="Uber",
    type="Competitor",
    status="Active",
    region=["US", "BR"],
    aliases=["Uber One"],
    why_tracked="Direct competitor.",
    source="watchlist",
)


def test_monitor_entity_parses_valid_items():
    payload = json.dumps(
        {
            "entity": "Uber",
            "items": [
                {
                    "headline": "Uber One expands rewards",
                    "url": "https://example.com/article",
                    "source": "TechCrunch",
                    "published": "2026-07-26",
                    "summary": "Summary.",
                    "why_it_matters": "Matters.",
                    "relevance": "high",
                }
            ],
        }
    )
    client = FakeClient([payload])
    items = gather.monitor_entity(client, UBER, TEST_CRITERIA, TEST_CONFIG)

    assert len(items) == 1
    assert items[0].entity == "Uber"
    assert items[0].topic is None
    assert items[0].headline == "Uber One expands rewards"


def test_monitor_entity_prompt_uses_configured_window_hours():
    widened_config = replace(TEST_CONFIG, news_window_hours=50)
    payload = json.dumps({"entity": "Uber", "items": []})
    client = FakeClient([payload])

    gather.monitor_entity(client, UBER, TEST_CRITERIA, widened_config)

    prompt = client.messages.calls[0]["messages"][0]["content"]
    assert "last 50 hours" in prompt
    assert "last 24 hours" not in prompt


def test_monitor_entity_strips_markdown_fences():
    payload = "```json\n" + json.dumps({"entity": "Uber", "items": []}) + "\n```"
    client = FakeClient([payload])
    items = gather.monitor_entity(client, UBER, TEST_CRITERIA, TEST_CONFIG)
    assert items == []


def test_monitor_entity_strips_leading_prose_before_fenced_json():
    payload = (
        "No news from the last 24 hours was found about Uber. All search "
        "results are older coverage with no recent developments.\n\n"
        "```json\n" + json.dumps({"entity": "Uber", "items": []}) + "\n```"
    )
    client = FakeClient([payload])
    items = gather.monitor_entity(client, UBER, TEST_CRITERIA, TEST_CONFIG)
    assert items == []


def test_monitor_entity_extracts_json_with_prose_and_no_fence():
    payload = "Here you go: " + json.dumps({"entity": "Uber", "items": []}) + " Hope that helps."
    client = FakeClient([payload])
    items = gather.monitor_entity(client, UBER, TEST_CRITERIA, TEST_CONFIG)
    assert items == []


def test_source_plausibly_matches_domain_obvious_match():
    assert gather._source_plausibly_matches_domain("TechCrunch", "https://techcrunch.com/a")


def test_source_plausibly_matches_domain_obvious_mismatch():
    assert not gather._source_plausibly_matches_domain(
        "TechCrunch", "https://totallyunrelatedsite.example/a"
    )


def test_source_plausibly_matches_domain_permissive_on_unknown():
    # No registry to check against — an outlet whose brand name doesn't
    # textually resemble its domain must not be flagged as a mismatch.
    assert gather._source_plausibly_matches_domain("Reuters", "https://reuters.com/a")


def test_monitor_entity_logs_but_keeps_mismatched_source(caplog):
    payload = json.dumps(
        {
            "entity": "Uber",
            "items": [
                {
                    "headline": "Uber news",
                    "url": "https://totallyunrelatedsite.example/a",
                    "source": "TechCrunch",
                    "published": "2026-07-26",
                    "summary": "S",
                    "why_it_matters": "W",
                    "relevance": "high",
                }
            ],
        }
    )
    client = FakeClient([payload])

    with caplog.at_level(logging.WARNING):
        items = gather.monitor_entity(client, UBER, TEST_CRITERIA, TEST_CONFIG)

    # Log-only per the v2 plan — no registry exists yet to safely drop on,
    # so a plausibility mismatch is visibility, never a filter.
    assert len(items) == 1
    assert items[0].source == "TechCrunch"
    assert "source/url plausibility mismatch" in caplog.text


def test_monitor_entity_drops_items_with_missing_url():
    payload = json.dumps(
        {
            "entity": "Uber",
            "items": [
                {
                    "headline": "No URL here",
                    "url": "",
                    "source": "X",
                    "published": "2026-07-26",
                    "summary": "S",
                    "why_it_matters": "W",
                    "relevance": "low",
                },
                {
                    "headline": "Has a URL",
                    "url": "https://example.com/real",
                    "source": "X",
                    "published": "2026-07-26",
                    "summary": "S",
                    "why_it_matters": "W",
                    "relevance": "high",
                },
            ],
        }
    )
    client = FakeClient([payload])
    items = gather.monitor_entity(client, UBER, TEST_CRITERIA, TEST_CONFIG)

    assert len(items) == 1
    assert items[0].headline == "Has a URL"


def test_monitor_entity_call_failure_logs_and_returns_empty(caplog):
    client = FakeClient([RuntimeError("API exploded")])
    with caplog.at_level(logging.ERROR):
        items = gather.monitor_entity(client, UBER, TEST_CRITERIA, TEST_CONFIG)

    assert items == []
    assert "Uber" in caplog.text


def test_monitor_entity_malformed_json_logs_and_returns_empty(caplog):
    client = FakeClient(["not valid json {{{"])
    with caplog.at_level(logging.ERROR):
        items = gather.monitor_entity(client, UBER, TEST_CRITERIA, TEST_CONFIG)

    assert items == []
    assert "Uber" in caplog.text


def test_monitor_all_entities_one_failure_does_not_abort_others():
    good_payload = json.dumps(
        {
            "entity": "Lyft",
            "items": [
                {
                    "headline": "Lyft launches program",
                    "url": "https://example.com/lyft",
                    "source": "X",
                    "published": "2026-07-26",
                    "summary": "S",
                    "why_it_matters": "W",
                    "relevance": "high",
                }
            ],
        }
    )
    lyft = Entity(name="Lyft", type="Competitor", status="Active", source="watchlist")
    bad_entity = Entity(name="BadCo", type="Competitor", status="Active", source="watchlist")


    client = FakeClient([RuntimeError("boom"), good_payload])

    items = gather.monitor_all_entities(client, [bad_entity, lyft], TEST_CRITERIA, TEST_CONFIG)

    entities_with_items = {item.entity for item in items}
    assert "Lyft" in entities_with_items or len(items) == 1


def test_monitor_industry_topic_parses_items():
    payload = json.dumps(
        {
            "topic": "youth social media policy",
            "items": [
                {
                    "headline": "New age-verification law",
                    "url": "https://example.com/law",
                    "source": "Reuters",
                    "published": "2026-07-27",
                    "summary": "S",
                    "why_it_matters": "W",
                    "relevance": "high",
                }
            ],
        }
    )
    client = FakeClient([payload])
    topic = IndustryTopic(topic="youth social media policy", notes="Age-verification laws.")
    items = gather.monitor_industry_topic(client, topic, TEST_CRITERIA, TEST_CONFIG)

    assert len(items) == 1
    assert items[0].topic == "youth social media policy"
    assert items[0].entity is None


def test_monitor_all_industry_topics_continues_after_failure(caplog):
    good_payload = json.dumps({"topic": "topic2", "items": []})
    client = FakeClient([RuntimeError("boom"), good_payload])
    topics = [IndustryTopic(topic="topic1"), IndustryTopic(topic="topic2")]

    with caplog.at_level(logging.ERROR):
        items = gather.monitor_all_industry_topics(client, topics, TEST_CRITERIA, TEST_CONFIG)

    assert items == []
    assert "topic1" in caplog.text


def test_scout_angle_parses_candidates_and_uses_scout_max_uses():
    payload = json.dumps(
        {
            "candidates": [
                {
                    "name": "ExampleCo",
                    "suggested_type": "Rewards partner prospect",
                    "category": "travel",
                    "region": "BR",
                    "why_fits": "Fits the criteria.",
                    "source_url": "https://example.com/news",
                    "confidence": "medium",
                }
            ]
        }
    )
    client = FakeClient([payload])
    candidates = gather.scout_angle(
        client,
        "New entrants angle",
        TEST_CRITERIA,
        {"Uber"},
        ["Fever: tickets"],
        ["Lincoln High: pilot"],
        TEST_CONFIG,
    )

    assert len(candidates) == 1
    assert candidates[0].name == "ExampleCo"
    assert client.messages.calls[0]["tools"][0]["max_uses"] == TEST_CONFIG.scout_max_uses


def test_scout_angle_drops_candidates_without_source_url():
    payload = json.dumps(
        {
            "candidates": [
                {
                    "name": "NoUrlCo",
                    "suggested_type": "Competitor",
                    "region": "US",
                    "why_fits": "...",
                    "source_url": "",
                }
            ]
        }
    )
    client = FakeClient([payload])
    candidates = gather.scout_angle(client, "angle", TEST_CRITERIA, set(), [], [], TEST_CONFIG)
    assert candidates == []


def test_scout_all_runs_static_plus_gtm_angles():
    # TEST_CRITERIA.gtm_partner_criteria is empty, so GTM falls back to
    # exactly one generic angle: 3 static angles + 1 GTM angle = 4.
    payload = json.dumps({"candidates": []})
    client = FakeClient([payload, payload, payload, payload])
    source_data = SourceData(
        entities=[UBER],
        excluded_names={"Meta"},
        reward_landscape=[],
        gtm_landscape=[],
        criteria=TEST_CRITERIA,
    )
    candidates = gather.scout_all(client, source_data, TEST_CONFIG)

    assert candidates == []
    assert len(client.messages.calls) == len(gather.DISCOVERY_ANGLES) + 1
    assert len(client.messages.calls) == 4


def test_gtm_discovery_angles_one_per_vertical():
    criteria = replace(
        TEST_CRITERIA, gtm_partner_criteria="School districts\nTelecom carriers\n- Youth nonprofits"
    )
    angles = gather.gtm_discovery_angles(criteria)

    assert len(angles) == 3
    assert any("School districts" in a for a in angles)
    assert any("Telecom carriers" in a for a in angles)
    assert any("Youth nonprofits" in a for a in angles)


def test_gtm_discovery_angles_falls_back_to_generic_when_empty():
    criteria = replace(TEST_CRITERIA, gtm_partner_criteria="")
    angles = gather.gtm_discovery_angles(criteria)

    assert angles == [gather._GENERIC_GTM_ANGLE]


# The real GTM partners criteria cell is authored as a small table (header +
# one row per vertical) pasted into a single sheet cell, which flattens to
# one line per table *cell* rather than one line per vertical.
GTM_TABLE_CRITERIA = "\n".join(
    [
        "#",
        "Vertical",
        "Owner",
        "GTM Motion",
        "Why it matters / Example accounts",
        "1",
        "6-12 Education (Grade 6-12)",
        "Joao",
        "Direct",
        "Districts (LAUSD, NYC DOE), private school groups — high student counts",
        "2",
        "Faith-based & Youth Orgs",
        "Eric",
        "Direct",
        "Boys & Girls Club, YMCA, Scouts — relationship-driven, community trust",
        "3",
        "US Telcos & MVNOs",
        "Leo",
        "Distribution Partner",
        "Regional carriers + MVNOs — distribution at scale",
    ]
)


def test_gtm_discovery_angles_parses_real_table_shape():
    criteria = replace(TEST_CRITERIA, gtm_partner_criteria=GTM_TABLE_CRITERIA)
    angles = gather.gtm_discovery_angles(criteria)

    assert len(angles) == 3
    assert any(
        "6-12 Education (Grade 6-12)" in a and "high student counts" in a for a in angles
    )
    assert any("Faith-based & Youth Orgs" in a for a in angles)
    assert any("US Telcos & MVNOs" in a for a in angles)
    # Row-internal fields that aren't the vertical name/why-it-matters must
    # not leak in as their own bogus "angles".
    assert not any(a for a in angles if a.strip() in {"Joao", "Eric", "Leo", "Direct"})


def test_parse_gtm_table_rows_returns_none_for_non_tabular_input():
    lines = ["School districts", "Telecom carriers", "Youth nonprofits"]
    assert gather._parse_gtm_table_rows(lines) is None


def test_entities_to_monitor_excludes_partners_by_default():
    watchlist_entity = Entity(name="Uber", type="Competitor", status="Active", source="watchlist")
    partner_entity = Entity(
        name="Fever", type="Existing partner", status="Active", source="partners_db"
    )
    source_data = SourceData(entities=[watchlist_entity, partner_entity])

    monitored = gather._entities_to_monitor(source_data, TEST_CONFIG)
    assert monitored == [watchlist_entity]


def test_entities_to_monitor_includes_partners_when_flag_on():
    watchlist_entity = Entity(name="Uber", type="Competitor", status="Active", source="watchlist")
    partner_entity = Entity(
        name="Fever", type="Existing partner", status="Active", source="partners_db"
    )
    gtm_partner_entity = Entity(
        name="Springfield School District",
        type="Existing GTM partner",
        status="Active",
        source="gtm_partners_db",
    )
    source_data = SourceData(entities=[watchlist_entity, partner_entity, gtm_partner_entity])
    config_with_flag = replace(TEST_CONFIG, monitor_existing_partners=True)

    monitored = gather._entities_to_monitor(source_data, config_with_flag)
    assert watchlist_entity in monitored
    assert partner_entity in monitored
    assert gtm_partner_entity in monitored


def test_monitor_uses_configured_max_uses():
    payload = json.dumps({"entity": "Uber", "items": []})
    client = FakeClient([payload])
    gather.monitor_entity(client, UBER, TEST_CRITERIA, TEST_CONFIG)
    assert client.messages.calls[0]["tools"][0]["max_uses"] == TEST_CONFIG.monitor_max_uses
    assert client.messages.calls[0]["tools"][0]["type"] == gather.WEB_SEARCH_TOOL_TYPE


def test_industry_uses_hardcoded_max_uses_not_config():
    payload = json.dumps({"topic": "t", "items": []})
    client = FakeClient([payload])
    topic = IndustryTopic(topic="t")
    gather.monitor_industry_topic(client, topic, TEST_CRITERIA, TEST_CONFIG)
    assert client.messages.calls[0]["tools"][0]["max_uses"] == gather.INDUSTRY_MAX_USES
