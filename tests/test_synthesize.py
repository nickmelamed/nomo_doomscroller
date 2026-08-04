import json
from datetime import date
from types import SimpleNamespace

import state
import synthesize
from models import Candidate, Criteria, Entity, NewsItem, SourceData


def text_block(text: str):
    return SimpleNamespace(type="text", text=text)


class FakeMessages:
    """Accepts either a single response text (repeated for every call) or a
    list of response texts (popped in call order, for testing retries)."""

    def __init__(self, responses):
        self._queue = list(responses) if isinstance(responses, list) else None
        self._single = responses if self._queue is None else None
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        text = self._queue.pop(0) if self._queue is not None else self._single
        return SimpleNamespace(content=[text_block(text)], stop_reason="end_turn")


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
    type="Rewards partner prospect",
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

TEST_CONFIG = SimpleNamespace(anthropic_model="claude-sonnet-5", synthesis_verbose_log=False)
VERBOSE_CONFIG = SimpleNamespace(anthropic_model="claude-sonnet-5", synthesis_verbose_log=True)


def test_compute_tracking_counts_only_counts_active_watchlist_rows():
    counts = synthesize.compute_tracking_counts(SOURCE_DATA)
    assert counts == {"competitors": 1, "partner_prospects": 1, "gtm_prospects": 0}


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
    assert payload["tracking_counts"] == {"competitors": 1, "partner_prospects": 1, "gtm_prospects": 0}
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


def test_build_payload_first_seen_days_ago_null_for_new_item():
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
    payload = synthesize.build_payload([item], [], [], SOURCE_DATA, seen_stories={})
    assert payload["monitoring_items"][0]["first_seen_days_ago"] is None


def test_build_payload_first_seen_days_ago_computed_for_repeat_item():
    item = NewsItem(
        headline="Uber One expands rewards",
        url="https://example.com/a",
        source="X",
        published="2026-07-29",
        summary="S",
        why_it_matters="W",
        relevance="high",
        entity="Uber",
    )
    story_hash = state.hash_story("Uber", "Uber One expands rewards")
    seen_stories = {story_hash: {"first_seen": "2026-07-27", "headline": item.headline, "entity_or_topic": "Uber"}}

    payload = synthesize.build_payload(
        [item], [], [], SOURCE_DATA, seen_stories=seen_stories, today=date(2026, 7, 30)
    )
    assert payload["monitoring_items"][0]["first_seen_days_ago"] == 3


def test_synthesize_passes_seen_stories_from_state_into_payload():
    item = NewsItem(
        headline="Uber One expands rewards",
        url="https://example.com/a",
        source="X",
        published="2026-07-29",
        summary="S",
        why_it_matters="W",
        relevance="high",
        entity="Uber",
    )
    story_hash = state.hash_story("Uber", item.headline)
    pipeline_state = {
        "seen_stories": {
            story_hash: {"first_seen": "2026-07-27", "headline": item.headline, "entity_or_topic": "Uber"}
        },
        "last_success": None,
    }

    captured = {}
    real_build_payload = synthesize.build_payload

    def spy(*args, **kwargs):
        result = real_build_payload(*args, **kwargs)
        captured["payload"] = result
        return result

    synthesize.build_payload = spy
    try:
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
        synthesize.synthesize(
            client, [item], [], [], SOURCE_DATA, TEST_CONFIG, state=pipeline_state
        )
    finally:
        synthesize.build_payload = real_build_payload

    assert captured["payload"]["monitoring_items"][0]["first_seen_days_ago"] is not None


def test_render_prompt_includes_repeat_story_guidance():
    prompt = synthesize._render_prompt(
        "synthesize.txt",
        nomo_context="ctx",
        region_weighting="rw",
        competitor_criteria_summary="c",
        reward_partner_criteria_summary="p",
        gtm_partner_criteria_summary="g",
        reward_landscape="none yet",
        gtm_landscape="none yet",
        schema=synthesize.DIGEST_SCHEMA,
        verbose_instructions="",
        payload="{}",
    )
    assert "first_seen_days_ago" in prompt
    assert "ongoing" in prompt


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
                        "suggested_type": "Rewards partner prospect",
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
    assert digest.tracking_counts == {"competitors": 1, "partner_prospects": 1, "gtm_prospects": 0}


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
    # propagate — never silently degrade to an empty/broken digest. Both
    # attempts (the retry too) return malformed JSON here, so it must raise.
    client = FakeClient(["not valid json {{{", "still not valid json {{{"])
    try:
        synthesize.synthesize(client, [], [], [], SOURCE_DATA, TEST_CONFIG)
        assert False, "expected an exception to propagate"
    except Exception:
        pass
    assert len(client.messages.calls) == 2


def test_synthesize_retries_once_on_parse_failure_then_succeeds():
    good_response = json.dumps(
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
    client = FakeClient(["not valid json {{{", good_response])

    digest = synthesize.synthesize(client, [], [], [], SOURCE_DATA, TEST_CONFIG)

    assert digest.quiet_day is True
    assert len(client.messages.calls) == 2


def _candidate(name: str, confidence: str, why_fits: str) -> Candidate:
    return Candidate(
        name=name,
        suggested_type="Rewards partner prospect",
        region="BR",
        why_fits=why_fits,
        source_url=f"https://example.com/{name.lower()}",
        confidence=confidence,
    )


VERBOSE_CANDIDATES = [
    _candidate("StrongCo", "high", "Concrete overlap with reward lineup gap."),
    _candidate("SolidCo", "high", "Clear regional fit and tradeable inventory."),
    _candidate("MaybeCo", "medium", "Plausible but thin why_fits."),
    _candidate("MaybeTwoCo", "medium", "Some signal, unclear fit."),
    _candidate("VagueCo", "low", "Generic, little differentiation."),
    _candidate("OffRegionCo", "low", "Wrong region for current weighting."),
    _candidate("WeakCo", "low", "Barely resembles a fit."),
    _candidate("ThinCo", "medium", "Not enough detail to judge."),
]


def _verbose_response(accepted: list[str], rejected: list[tuple[str, str]]) -> str:
    return json.dumps(
        {
            "quiet_day": False,
            "sections": {
                "competition": [],
                "industry": [],
                "partner_prospects": [],
                "new_candidates": [
                    {
                        "name": name,
                        "suggested_type": "Rewards partner prospect",
                        "region": "BR",
                        "why_fits": "Fits.",
                        "source_url": f"https://example.com/{name.lower()}",
                    }
                    for name in accepted
                ],
            },
            "rejected_candidates": [{"name": name, "reason": reason} for name, reason in rejected],
        }
    )


def test_synthesize_verbose_off_ignores_rejected_candidates(caplog):
    response = _verbose_response(
        accepted=["StrongCo"],
        rejected=[("VagueCo", "generic why_fits")],
    )
    client = FakeClient(response)

    with caplog.at_level("INFO"):
        digest = synthesize.synthesize(
            client, [], [], VERBOSE_CANDIDATES, SOURCE_DATA, TEST_CONFIG
        )

    assert [c.name for c in digest.new_candidates] == ["StrongCo"]
    assert "verbose: candidate rejected" not in caplog.text


def test_synthesize_verbose_on_logs_every_rejected_candidate(caplog):
    accepted = ["StrongCo", "SolidCo"]
    rejected = [
        ("MaybeCo", "thin why_fits"),
        ("MaybeTwoCo", "unclear fit"),
        ("VagueCo", "generic, little differentiation"),
        ("OffRegionCo", "wrong region for current weighting"),
        ("WeakCo", "barely resembles a fit"),
        ("ThinCo", "not enough detail to judge"),
    ]
    response = _verbose_response(accepted=accepted, rejected=rejected)
    client = FakeClient(response)

    with caplog.at_level("INFO"):
        digest = synthesize.synthesize(
            client, [], [], VERBOSE_CANDIDATES, SOURCE_DATA, VERBOSE_CONFIG
        )

    assert [c.name for c in digest.new_candidates] == accepted
    for name, reason in rejected:
        assert f"name={name!r}" in caplog.text
        assert f"reason={reason!r}" in caplog.text
    # Digest's shape is untouched by verbose mode — no new attribute leaks in.
    assert not hasattr(digest, "rejected_candidates")

    all_accounted_for = set(accepted) | {name for name, _ in rejected}
    assert all_accounted_for == {c.name for c in VERBOSE_CANDIDATES}


def test_synthesize_verbose_on_populates_rejected_today_matched_to_full_candidate():
    response = _verbose_response(
        accepted=["StrongCo"],
        rejected=[("MaybeCo", "thin why_fits")],
    )
    client = FakeClient(response)

    digest = synthesize.synthesize(client, [], [], VERBOSE_CANDIDATES, SOURCE_DATA, VERBOSE_CONFIG)

    assert len(digest.rejected_today) == 1
    rejected = digest.rejected_today[0]
    assert rejected.name == "MaybeCo"
    assert rejected.reason == "thin why_fits"
    # Fields beyond name/reason come from the matched input Candidate, not
    # from the model's rejected_candidates entry (which only has name+reason).
    assert rejected.suggested_type == "Rewards partner prospect"
    assert rejected.region == "BR"
    assert rejected.confidence == "medium"
    assert rejected.source_url == "https://example.com/maybeco"


def test_synthesize_verbose_off_leaves_rejected_today_empty():
    response = _verbose_response(
        accepted=["StrongCo"],
        rejected=[("MaybeCo", "thin why_fits")],
    )
    client = FakeClient(response)

    digest = synthesize.synthesize(client, [], [], VERBOSE_CANDIDATES, SOURCE_DATA, TEST_CONFIG)

    assert digest.rejected_today == []


def test_synthesize_verbose_on_unmatched_rejected_name_is_skipped():
    # Model returns a rejected_candidates entry whose name doesn't match any
    # input candidate (e.g. it mangled the name) — must not crash or fabricate.
    response = _verbose_response(
        accepted=["StrongCo"],
        rejected=[("SomeUnknownName", "reason")],
    )
    client = FakeClient(response)

    digest = synthesize.synthesize(client, [], [], VERBOSE_CANDIDATES, SOURCE_DATA, VERBOSE_CONFIG)

    assert digest.rejected_today == []


def test_synthesize_verbose_on_missing_field_does_not_crash(caplog):
    # Model ignored the verbose instructions and returned no rejected_candidates
    # key at all — must not crash, just treated as empty.
    response = json.dumps(
        {
            "quiet_day": False,
            "sections": {
                "competition": [],
                "industry": [],
                "partner_prospects": [],
                "new_candidates": [],
            },
        }
    )
    client = FakeClient(response)

    with caplog.at_level("INFO"):
        digest = synthesize.synthesize(client, [], [], [], SOURCE_DATA, VERBOSE_CONFIG)

    assert digest.new_candidates == []
    assert "verbose: candidate rejected" not in caplog.text


def test_render_prompt_includes_verbose_instructions_when_enabled():
    prompt = synthesize._render_prompt(
        "synthesize.txt",
        nomo_context="ctx",
        region_weighting="rw",
        competitor_criteria_summary="Competes for youth attention.",
        reward_partner_criteria_summary="Has tradeable reward inventory.",
        gtm_partner_criteria_summary="none specified",
        reward_landscape="none yet",
        gtm_landscape="none yet",
        schema=synthesize.DIGEST_SCHEMA,
        verbose_instructions=synthesize.VERBOSE_INSTRUCTIONS_BLOCK,
        payload="{}",
    )
    assert "rejected_candidates" in prompt


def test_render_prompt_omits_verbose_instructions_when_disabled():
    prompt = synthesize._render_prompt(
        "synthesize.txt",
        nomo_context="ctx",
        region_weighting="rw",
        competitor_criteria_summary="Competes for youth attention.",
        reward_partner_criteria_summary="Has tradeable reward inventory.",
        gtm_partner_criteria_summary="none specified",
        reward_landscape="none yet",
        gtm_landscape="none yet",
        schema=synthesize.DIGEST_SCHEMA,
        verbose_instructions="",
        payload="{}",
    )
    assert "rejected_candidates" not in prompt


def test_one_line_summary_takes_first_sentence():
    text = "Competes for youth loyalty attention. Also consider adjacent EdTech."
    assert synthesize._one_line_summary(text) == "Competes for youth loyalty attention."


def test_one_line_summary_truncates_long_single_sentence():
    text = "x" * 300
    summary = synthesize._one_line_summary(text, max_len=20)
    assert summary == "x" * 20 + "..."


def test_one_line_summary_handles_empty_text():
    assert synthesize._one_line_summary("") == "none specified"


def test_synthesize_prompt_includes_confidence_guidance_and_criteria_summary():
    criteria = Criteria(
        nomo_context="NOMO context.",
        region_weighting="BR is primary.",
        competitor_criteria="Competes for youth loyalty attention. More detail here.",
        reward_partner_criteria="Has tradeable reward inventory. More detail here.",
    )
    source_data = SourceData(entities=[], excluded_names=set(), criteria=criteria)

    captured = {}
    real_render = synthesize._render_prompt

    def spy(filename, **kwargs):
        captured.update(kwargs)
        return real_render(filename, **kwargs)

    synthesize._render_prompt = spy
    try:
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
        synthesize.synthesize(client, [], [], [], source_data, TEST_CONFIG)
    finally:
        synthesize._render_prompt = real_render

    assert captured["competitor_criteria_summary"] == "Competes for youth loyalty attention."
    assert captured["reward_partner_criteria_summary"] == "Has tradeable reward inventory."
    assert "confidence" in real_render(
        "synthesize.txt",
        nomo_context="ctx",
        region_weighting="rw",
        competitor_criteria_summary="c",
        reward_partner_criteria_summary="p",
        gtm_partner_criteria_summary="g",
        reward_landscape="none yet",
        gtm_landscape="none yet",
        schema=synthesize.DIGEST_SCHEMA,
        verbose_instructions="",
        payload="{}",
    )


def test_render_prompt_includes_recency_ranking_guidance():
    prompt = synthesize._render_prompt(
        "synthesize.txt",
        nomo_context="ctx",
        region_weighting="rw",
        competitor_criteria_summary="c",
        reward_partner_criteria_summary="p",
        gtm_partner_criteria_summary="g",
        reward_landscape="none yet",
        gtm_landscape="none yet",
        schema=synthesize.DIGEST_SCHEMA,
        verbose_instructions="",
        payload="{}",
    )
    assert "published" in prompt
    assert "more recently published items higher" in prompt


def test_synthesize_does_not_retry_more_than_once():
    client = FakeClient(["bad {{{", "bad again {{{"])

    raised = False
    try:
        synthesize.synthesize(client, [], [], [], SOURCE_DATA, TEST_CONFIG)
    except Exception:
        raised = True

    assert raised is True
    # Exactly 2 calls: the original attempt plus exactly one retry, no more.
    assert len(client.messages.calls) == 2
