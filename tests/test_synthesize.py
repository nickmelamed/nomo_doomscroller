import json
from types import SimpleNamespace

import synthesize
from models import Candidate, Criteria, Entity, NewsItem, SourceData


def text_block(text: str):
    return SimpleNamespace(type="text", text=text)


class FakeMessages:
    def __init__(self, response_text):
        self._response_text = response_text
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(content=[text_block(self._response_text)], stop_reason="end_turn")


class FakeClient:
    def __init__(self, response_text):
        self.messages = FakeMessages(response_text)


UBER = Entity(
    name="Uber",
    type="Competitor",
    status="Active",
    priority="High",
    source="watchlist",
)
LONG_GAME = Entity(
    name="Long Game",
    type="Partner prospect",
    status="Active",
    priority="Medium",
    source="watchlist",
)
FEVER = Entity(name="Fever", type="Existing partner", status="Active", source="partners_db")

SOURCE_DATA = SourceData(
    entities=[UBER, LONG_GAME, FEVER],
    excluded_names={"Fever"},
    reward_landscape=["Fever: live-events redemption"],
    criteria=Criteria(nomo_context="NOMO context.", region_weighting="BR is primary."),
)

TEST_CONFIG = SimpleNamespace(anthropic_model="claude-sonnet-5")


def test_compute_tracking_counts_only_counts_active_watchlist_rows():
    counts = synthesize.compute_tracking_counts(SOURCE_DATA)
    assert counts == {"competitors": 1, "partner_prospects": 1}


def test_build_payload_enriches_items_with_entity_type_and_priority():
    item = NewsItem(
        headline="Uber news",
        url="https://example.com/a",
        source="X",
        published="2026-07-29",
        summary="S",
        why_it_matters="W",
        relevance="high",
        entity="Uber",
    )
    payload = synthesize.build_payload([item], [], [], SOURCE_DATA)

    enriched = payload["monitoring_items"][0]
    assert enriched["entity_type"] == "Competitor"
    assert enriched["priority"] == "High"
    assert payload["tracking_counts"] == {"competitors": 1, "partner_prospects": 1}
    assert payload["reward_landscape"] == ["Fever: live-events redemption"]


def test_build_payload_handles_item_with_unknown_entity():
    item = NewsItem(
        headline="Unlisted news",
        url="https://example.com/a",
        source="X",
        published="2026-07-29",
        summary="S",
        why_it_matters="W",
        relevance="low",
        entity="NotTracked",
    )
    payload = synthesize.build_payload([item], [], [], SOURCE_DATA)
    enriched = payload["monitoring_items"][0]
    assert enriched["entity_type"] is None
    assert enriched["priority"] is None


def test_synthesize_parses_full_digest():
    response = json.dumps(
        {
            "quiet_day": False,
            "sections": {
                "competition": [
                    {
                        "headline": "Uber news",
                        "url": "https://example.com/a",
                        "source": "X",
                        "summary": "S",
                    }
                ],
                "industry": [],
                "partner_prospects": [],
                "new_candidates": [
                    {
                        "name": "ExampleCo",
                        "suggested_type": "Partner prospect",
                        "region": "BR",
                        "why_fits": "Fits.",
                        "source_url": "https://example.com/b",
                    }
                ],
            },
            "tracking_counts": {"competitors": 999, "partner_prospects": 999},
        }
    )
    client = FakeClient(response)
    digest = synthesize.synthesize(client, [], [], [], SOURCE_DATA, TEST_CONFIG)

    assert digest.quiet_day is False
    assert len(digest.competition) == 1
    assert digest.competition[0].headline == "Uber news"
    assert len(digest.new_candidates) == 1
    assert digest.new_candidates[0].name == "ExampleCo"
    # tracking_counts is always the deterministic computed value, never the
    # model's echoed (possibly wrong) numbers.
    assert digest.tracking_counts == {"competitors": 1, "partner_prospects": 1}


def test_synthesize_quiet_day_true_with_empty_sections():
    response = json.dumps(
        {
            "quiet_day": True,
            "sections": {
                "competition": [],
                "industry": [],
                "partner_prospects": [],
                "new_candidates": [],
            },
        }
    )
    client = FakeClient(response)
    digest = synthesize.synthesize(client, [], [], [], SOURCE_DATA, TEST_CONFIG)

    assert digest.quiet_day is True
    assert digest.competition == []
    assert digest.new_candidates == []


def test_synthesize_strips_leading_prose_before_fenced_json():
    response = (
        "Here is the digest based on today's inputs.\n\n```json\n"
        + json.dumps(
            {
                "quiet_day": True,
                "sections": {
                    "competition": [],
                    "industry": [],
                    "partner_prospects": [],
                    "new_candidates": [],
                },
            }
        )
        + "\n```"
    )
    client = FakeClient(response)
    digest = synthesize.synthesize(client, [], [], [], SOURCE_DATA, TEST_CONFIG)
    assert digest.quiet_day is True


def test_synthesize_malformed_json_raises_not_swallowed():
    # Unlike gather.py's per-call resilience, synthesis failures must
    # propagate — never silently degrade to an empty/broken digest.
    client = FakeClient("not valid json {{{")
    try:
        synthesize.synthesize(client, [], [], [], SOURCE_DATA, TEST_CONFIG)
        assert False, "expected an exception to propagate"
    except Exception:
        pass
