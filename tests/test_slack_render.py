from dataclasses import replace
from datetime import date

import pytest

import slack_render
from config import Config
from models import Candidate, Digest, DigestItem

TODAY = date(2026, 7, 29)

TEST_CONFIG = Config(
    anthropic_api_key="dummy",
    anthropic_model="claude-sonnet-5",
    data_source="sheets",
    slack_webhook_url="https://hooks.slack.com/services/T000/B000/XXXX",
    news_window_hours=24,
    max_items_per_section=2,
    monitor_existing_partners=False,
    monitor_max_uses=3,
    scout_max_uses=8,
    google_sheets_id="sheet-id",
    google_service_account_json="{}",
    sheets_url="https://docs.google.com/spreadsheets/d/abc123",
    notion_api_key=None,
    notion_watchlist_db_id=None,
    notion_criteria_page_id=None,
    notion_topics_db_id=None,
    notion_partners_db_id=None,
    notion_db_url=None,
)


def digest_item(headline: str, **overrides) -> DigestItem:
    fields = {
        "headline": headline,
        "url": "https://example.com/a",
        "source": "TechCrunch",
        "summary": "Summary.",
    }
    fields.update(overrides)
    return DigestItem(**fields)


def candidate(name: str, **overrides) -> Candidate:
    fields = {
        "name": name,
        "suggested_type": "Partner prospect",
        "region": "BR",
        "why_fits": "Fits the criteria.",
        "source_url": "https://example.com/news",
    }
    fields.update(overrides)
    return Candidate(**fields)


def test_header_block_format():
    digest = Digest(quiet_day=True, tracking_counts={"competitors": 1, "partner_prospects": 1})
    blocks = slack_render.build_blocks(digest, TEST_CONFIG, today=TODAY)

    assert blocks[0]["type"] == "header"
    assert blocks[0]["text"]["text"] == "\U0001f4f1 NOMO Doomscroller — 2026-07-29"


def test_quiet_day_is_header_plus_line_plus_footer():
    digest = Digest(quiet_day=True, tracking_counts={"competitors": 3, "partner_prospects": 2})
    blocks = slack_render.build_blocks(digest, TEST_CONFIG, today=TODAY)

    assert len(blocks) == 3
    assert blocks[1]["type"] == "section"
    assert "Quiet day" in blocks[1]["text"]["text"]
    assert blocks[2]["type"] == "context"
    assert "3 competitors" in blocks[2]["elements"][0]["text"]
    assert "2 partner prospects" in blocks[2]["elements"][0]["text"]
    assert TEST_CONFIG.sheets_url in blocks[2]["elements"][0]["text"]


def test_section_rendering_and_ordering():
    digest = Digest(
        quiet_day=False,
        competition=[digest_item("Uber news")],
        industry=[digest_item("Policy news")],
        partner_prospects=[digest_item("Fever update")],
        tracking_counts={"competitors": 1, "partner_prospects": 1},
    )
    blocks = slack_render.build_blocks(digest, TEST_CONFIG, today=TODAY)

    section_blocks = [b for b in blocks if b["type"] == "section"]
    titles_in_order = [b["text"]["text"].splitlines()[0] for b in section_blocks]
    assert titles_in_order == ["*Competition*", "*Industry*", "*Partner prospects*"]


def test_item_line_format():
    digest = Digest(
        quiet_day=False,
        competition=[
            digest_item(
                "Uber One expands rewards",
                url="https://example.com/uber",
                summary="One-sentence summary.",
                source="TechCrunch",
            )
        ],
        tracking_counts={"competitors": 1, "partner_prospects": 0},
    )
    blocks = slack_render.build_blocks(digest, TEST_CONFIG, today=TODAY)
    section = next(b for b in blocks if b["type"] == "section" and "Competition" in b["text"]["text"])

    assert (
        "• *<https://example.com/uber|Uber One expands rewards>* — "
        "One-sentence summary. _(TechCrunch)_" in section["text"]["text"]
    )


def test_escapes_ampersand_and_angle_brackets_in_item_text():
    # Slack's mrkdwn parser treats &, <, > as syntax — real headlines/
    # summaries routinely contain these (e.g. "Snap & TikTok settle <case>")
    # and an unescaped one causes Slack to reject the whole payload.
    digest = Digest(
        quiet_day=False,
        competition=[
            digest_item(
                "Snap & TikTok settle <landmark> case",
                summary="Reported at >$1B, per <the filing>.",
                source="Reuters & AP",
            )
        ],
        tracking_counts={"competitors": 1, "partner_prospects": 0},
    )
    blocks = slack_render.build_blocks(digest, TEST_CONFIG, today=TODAY)
    section = next(b for b in blocks if b["type"] == "section" and "Competition" in b["text"]["text"])
    text = section["text"]["text"]

    assert "Snap &amp; TikTok settle &lt;landmark&gt; case" in text
    assert "&gt;$1B" in text
    assert "&lt;the filing&gt;" in text
    assert "Reuters &amp; AP" in text
    # No raw unescaped &, <, > from the dynamic content should survive.
    assert "Snap & TikTok" not in text
    assert "<landmark>" not in text


def test_escapes_candidate_and_footer_text():
    digest = Digest(
        quiet_day=False,
        new_candidates=[
            candidate("R&D Co", why_fits="Fits because of A&B synergies.")
        ],
        tracking_counts={"competitors": 1, "partner_prospects": 0},
    )
    blocks = slack_render.build_blocks(digest, TEST_CONFIG, today=TODAY)
    candidates_section = next(
        b for b in blocks if b["type"] == "section" and "New candidates" in b["text"]["text"]
    )
    assert "R&amp;D Co" in candidates_section["text"]["text"]
    assert "A&amp;B synergies" in candidates_section["text"]["text"]


def test_splits_section_into_multiple_blocks_when_over_slack_text_limit():
    # 6 items with long realistic-length text can exceed Slack's 3000-char
    # per-block limit when packed into one block, as fixture testing (short
    # placeholder text) never surfaced.
    long_config = replace(TEST_CONFIG, max_items_per_section=6)
    long_summary = "x" * 600
    items = [
        digest_item(f"Headline {i}", url=f"https://example.com/{i}", summary=long_summary)
        for i in range(6)
    ]
    digest = Digest(
        quiet_day=False, competition=items, tracking_counts={"competitors": 6, "partner_prospects": 0}
    )
    blocks = slack_render.build_blocks(digest, long_config, today=TODAY)
    competition_blocks = [
        b for b in blocks if b["type"] == "section" and "Headline" in b["text"]["text"]
    ]

    assert len(competition_blocks) > 1
    for block in competition_blocks:
        assert len(block["text"]["text"]) <= slack_render.SLACK_SECTION_TEXT_LIMIT
    # No content lost across the split.
    combined = "\n".join(b["text"]["text"] for b in competition_blocks)
    for i in range(6):
        assert f"Headline {i}" in combined


def test_truncates_to_max_items_per_section_with_more_line():
    # TEST_CONFIG.max_items_per_section == 2
    items = [digest_item(f"Item {i}", url=f"https://example.com/{i}") for i in range(5)]
    digest = Digest(
        quiet_day=False, competition=items, tracking_counts={"competitors": 5, "partner_prospects": 0}
    )
    blocks = slack_render.build_blocks(digest, TEST_CONFIG, today=TODAY)
    section = next(b for b in blocks if b["type"] == "section" and "Competition" in b["text"]["text"])

    text = section["text"]["text"]
    assert "Item 0" in text and "Item 1" in text
    assert "Item 2" not in text
    assert "_+3 more_" in text


def test_empty_sections_are_omitted():
    digest = Digest(
        quiet_day=False,
        competition=[digest_item("Uber news")],
        industry=[],
        partner_prospects=[],
        tracking_counts={"competitors": 1, "partner_prospects": 0},
    )
    blocks = slack_render.build_blocks(digest, TEST_CONFIG, today=TODAY)
    section_texts = [b["text"]["text"] for b in blocks if b["type"] == "section"]

    assert not any("*Industry*" in t for t in section_texts)
    assert not any("*Partner prospects*" in t for t in section_texts)


def test_new_candidates_section_is_visually_distinct_with_note():
    digest = Digest(
        quiet_day=False,
        new_candidates=[
            candidate(
                "ExampleCo",
                why_fits="Great fit.",
                suggested_type="Partner prospect",
                source_url="https://example.com/news",
            )
        ],
        tracking_counts={"competitors": 0, "partner_prospects": 0},
    )
    blocks = slack_render.build_blocks(digest, TEST_CONFIG, today=TODAY)

    assert any(b["type"] == "divider" for b in blocks)
    candidates_section = next(
        b for b in blocks if b["type"] == "section" and "New candidates" in b["text"]["text"]
    )
    assert (
        "• *ExampleCo* — Great fit. · _proposed Partner prospect_ · "
        "<https://example.com/news|source>" in candidates_section["text"]["text"]
    )
    note_block = next(
        b
        for b in blocks
        if b["type"] == "context" and "Proposed only" in b["elements"][0]["text"]
    )
    assert "add via the watchlist" in note_block["elements"][0]["text"]


def test_footer_present_every_day_normal_and_quiet():
    normal = Digest(
        quiet_day=False,
        competition=[digest_item("Uber news")],
        tracking_counts={"competitors": 4, "partner_prospects": 6},
    )
    quiet = Digest(quiet_day=True, tracking_counts={"competitors": 4, "partner_prospects": 6})

    for digest in (normal, quiet):
        blocks = slack_render.build_blocks(digest, TEST_CONFIG, today=TODAY)
        footer = blocks[-1]
        assert footer["type"] == "context"
        text = footer["elements"][0]["text"]
        assert "4 competitors" in text
        assert "6 partner prospects" in text
        assert "manage the list" in text
        assert TEST_CONFIG.sheets_url in text


def test_manage_list_url_resolves_per_data_source():
    from dataclasses import replace

    notion_config = replace(
        TEST_CONFIG,
        data_source="notion",
        notion_db_url="https://notion.so/watchlist-db",
    )
    digest = Digest(quiet_day=True, tracking_counts={"competitors": 1, "partner_prospects": 1})
    blocks = slack_render.build_blocks(digest, notion_config, today=TODAY)
    footer_text = blocks[-1]["elements"][0]["text"]
    assert "https://notion.so/watchlist-db" in footer_text


def test_post_digest_success(monkeypatch):
    calls = []

    class FakeResponse:
        status_code = 200
        text = "ok"

    def fake_post(url, json, timeout):
        calls.append((url, json, timeout))
        return FakeResponse()

    monkeypatch.setattr(slack_render.httpx, "post", fake_post)

    digest = Digest(quiet_day=True, tracking_counts={"competitors": 1, "partner_prospects": 1})
    slack_render.post_digest(digest, TEST_CONFIG, today=TODAY)

    assert len(calls) == 1
    url, payload, _ = calls[0]
    assert url == TEST_CONFIG.slack_webhook_url
    assert "blocks" in payload
    assert "text" in payload


def test_post_digest_raises_on_non_200(monkeypatch):
    class FakeResponse:
        status_code = 500
        text = "internal error"

    def fake_post(url, json, timeout):
        return FakeResponse()

    monkeypatch.setattr(slack_render.httpx, "post", fake_post)

    digest = Digest(quiet_day=True, tracking_counts={"competitors": 1, "partner_prospects": 1})
    with pytest.raises(slack_render.SlackDeliveryError):
        slack_render.post_digest(digest, TEST_CONFIG, today=TODAY)
