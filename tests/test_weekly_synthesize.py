import json
from types import SimpleNamespace

import weekly_synthesize

TEST_CONFIG = SimpleNamespace(anthropic_model="claude-sonnet-5")


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


ONE_DAY = {
    "date": "2026-07-20",
    "quiet_day": False,
    "competition": [
        {"headline": "Uber news", "url": "https://example.com/a", "source": "X", "summary": "S"}
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
    "tracking_counts": {"competitors": 1, "partner_prospects": 1},
}


def test_build_weekly_rollup_parses_full_response():
    response = json.dumps(
        {
            "week_of": "2026-07-20",
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
            },
            "notable_candidates": [
                {
                    "name": "ExampleCo",
                    "suggested_type": "Partner prospect",
                    "region": "BR",
                    "why_fits": "Fits.",
                    "source_url": "https://example.com/b",
                }
            ],
            "themes": ["Competitor funding wave"],
        }
    )
    client = FakeClient(response)

    rollup = weekly_synthesize.build_weekly_rollup(client, [ONE_DAY], "2026-07-20", TEST_CONFIG)

    assert rollup.week_of == "2026-07-20"
    assert len(rollup.competition) == 1
    assert rollup.competition[0].headline == "Uber news"
    assert len(rollup.notable_candidates) == 1
    assert rollup.notable_candidates[0].name == "ExampleCo"
    assert rollup.themes == ["Competitor funding wave"]


def test_build_weekly_rollup_empty_week_returns_empty_rollup():
    response = json.dumps(
        {
            "week_of": "2026-07-20",
            "sections": {"competition": [], "industry": [], "partner_prospects": []},
            "notable_candidates": [],
            "themes": [],
        }
    )
    client = FakeClient(response)

    rollup = weekly_synthesize.build_weekly_rollup(client, [ONE_DAY], "2026-07-20", TEST_CONFIG)

    assert rollup.competition == []
    assert rollup.notable_candidates == []
    assert rollup.themes == []


def test_render_prompt_warns_against_overstating_recurrence():
    prompt = weekly_synthesize._render_prompt(
        "weekly_rollup.txt",
        week_of="2026-07-20",
        days_covered=5,
        schema=weekly_synthesize.WEEKLY_ROLLUP_SCHEMA,
        payload="[]",
    )
    assert "only" in prompt and "genuinely" in prompt
    assert "more than one day's digest" in prompt


def test_build_weekly_rollup_sends_daily_digests_in_payload():
    response = json.dumps(
        {
            "week_of": "2026-07-20",
            "sections": {"competition": [], "industry": [], "partner_prospects": []},
            "notable_candidates": [],
            "themes": [],
        }
    )
    client = FakeClient(response)

    weekly_synthesize.build_weekly_rollup(client, [ONE_DAY], "2026-07-20", TEST_CONFIG)

    prompt = client.messages.calls[0]["messages"][0]["content"]
    assert "Uber news" in prompt
    assert "2026-07-20" in prompt
