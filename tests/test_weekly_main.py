import json
from datetime import date
from types import SimpleNamespace

import state as state_module
import weekly_main
from models import RejectedCandidate, WeeklyRollup


def test_prior_week_weekdays_from_a_monday():
    # today = Monday 2026-07-27 -> prior week is Mon 2026-07-20..Fri 2026-07-24
    weekdays = weekly_main._prior_week_weekdays(date(2026, 7, 27))
    assert weekdays == [
        date(2026, 7, 20),
        date(2026, 7, 21),
        date(2026, 7, 22),
        date(2026, 7, 23),
        date(2026, 7, 24),
    ]


def test_prior_week_weekdays_from_a_midweek_manual_dispatch():
    # today = Wednesday 2026-07-29 -> current week starts Mon 2026-07-27,
    # so the prior *completed* week is still Mon 2026-07-20..Fri 2026-07-24.
    weekdays = weekly_main._prior_week_weekdays(date(2026, 7, 29))
    assert weekdays[0] == date(2026, 7, 20)
    assert weekdays[-1] == date(2026, 7, 24)


def test_load_week_digests_skips_missing_days(tmp_path):
    weekdays = [date(2026, 7, 20), date(2026, 7, 21), date(2026, 7, 22)]
    (tmp_path / "2026-07-20.json").write_text(json.dumps({"quiet_day": False}))
    (tmp_path / "2026-07-22.json").write_text(json.dumps({"quiet_day": True}))
    # 2026-07-21 has no archive — e.g. that day's run failed.

    digests = weekly_main._load_week_digests(tmp_path, weekdays)

    assert len(digests) == 2
    assert [d["date"] for d in digests] == ["2026-07-20", "2026-07-22"]


def test_run_skips_posting_when_no_digests_archived(monkeypatch):
    monkeypatch.setattr(weekly_main, "_load_week_digests", lambda dir, weekdays: [])

    post_called = {"value": False}
    monkeypatch.setattr(
        weekly_main.slack_render,
        "post_weekly_rollup",
        lambda *a, **k: post_called.__setitem__("value", True),
    )

    exit_code = weekly_main.run()

    assert exit_code == 0
    assert post_called["value"] is False


def test_run_happy_path_builds_and_posts_rollup(monkeypatch):
    one_day = {"date": "2026-07-20", "quiet_day": False}
    monkeypatch.setattr(weekly_main, "_load_week_digests", lambda dir, weekdays: [one_day])

    rollup = WeeklyRollup(week_of="2026-07-20")
    captured = {}

    def fake_build_weekly_rollup(client, daily_digests, week_of, cfg):
        captured["daily_digests"] = daily_digests
        captured["week_of"] = week_of
        return rollup

    def fake_post(r, cfg, days_covered=None):
        captured["posted_rollup"] = r
        captured["days_covered"] = days_covered

    monkeypatch.setattr(weekly_main, "build_weekly_rollup", fake_build_weekly_rollup)
    monkeypatch.setattr(weekly_main.slack_render, "post_weekly_rollup", fake_post)

    exit_code = weekly_main.run()

    assert exit_code == 0
    assert captured["daily_digests"] == [one_day]
    assert captured["posted_rollup"] is rollup
    assert captured["days_covered"] == 1


def test_run_synthesis_failure_never_posts(monkeypatch):
    one_day = {"date": "2026-07-20", "quiet_day": False}
    monkeypatch.setattr(weekly_main, "_load_week_digests", lambda dir, weekdays: [one_day])

    def failing_build(client, daily_digests, week_of, cfg):
        raise RuntimeError("malformed rollup JSON")

    post_called = {"value": False}
    monkeypatch.setattr(weekly_main, "build_weekly_rollup", failing_build)
    monkeypatch.setattr(
        weekly_main.slack_render,
        "post_weekly_rollup",
        lambda *a, **k: post_called.__setitem__("value", True),
    )

    exit_code = weekly_main.run()

    assert exit_code == 1
    assert post_called["value"] is False


def test_run_end_to_end_with_fixture_archive_and_fake_client(monkeypatch, tmp_path):
    """Drives weekly_main.py end-to-end (real _load_week_digests, real
    weekly_synthesize prompt render/parse) against synthetic archived digests
    for a partial week (3 of 5 weekdays) — only the Anthropic client and the
    Slack POST are faked."""

    def digest_fixture(headline: str) -> dict:
        return {
            "quiet_day": False,
            "competition": [
                {
                    "headline": headline,
                    "url": "https://example.com/a",
                    "source": "X",
                    "summary": "S",
                }
            ],
            "industry": [],
            "partner_prospects": [],
            "new_candidates": [],
            "tracking_counts": {"competitors": 1, "partner_prospects": 0},
        }

    (tmp_path / "2026-07-20.json").write_text(json.dumps(digest_fixture("Monday news")))
    (tmp_path / "2026-07-22.json").write_text(json.dumps(digest_fixture("Wednesday news")))
    (tmp_path / "2026-07-24.json").write_text(json.dumps(digest_fixture("Friday news")))
    # 2026-07-21 and 2026-07-23 have no archive — a partial week.

    monkeypatch.setattr(weekly_main.state_module, "DEFAULT_DIGEST_ARCHIVE_DIR", tmp_path)
    monkeypatch.setattr(weekly_main, "date", SimpleNamespace(today=lambda: date(2026, 7, 27)))

    response_text = json.dumps(
        {
            "week_of": "2026-07-20",
            "sections": {
                "competition": [
                    {
                        "headline": "Monday + Wednesday + Friday news, rolled up",
                        "url": "https://example.com/a",
                        "source": "X",
                        "summary": "S",
                    }
                ],
                "industry": [],
                "partner_prospects": [],
            },
            "notable_candidates": [],
            "themes": ["Steady competitor activity all week"],
        }
    )

    class FakeStream:
        def __init__(self, message):
            self._message = message

        def __enter__(self):
            return self

        def __exit__(self, *exc_info):
            return False

        def get_final_message(self):
            return self._message

    class FakeMessages:
        def stream(self, **kwargs):
            return FakeStream(
                SimpleNamespace(
                    content=[SimpleNamespace(type="text", text=response_text)],
                    stop_reason="end_turn",
                )
            )

    class FakeAnthropicClient:
        def __init__(self, api_key=None):
            self.messages = FakeMessages()

    monkeypatch.setattr(weekly_main.anthropic, "Anthropic", FakeAnthropicClient)

    posted = {}

    def fake_post(rollup, cfg, days_covered=None):
        posted["rollup"] = rollup
        posted["days_covered"] = days_covered

    monkeypatch.setattr(weekly_main.slack_render, "post_weekly_rollup", fake_post)

    exit_code = weekly_main.run()

    assert exit_code == 0
    assert posted["days_covered"] == 3  # partial week — only 3 of 5 archived
    assert posted["rollup"].week_of == "2026-07-20"
    assert len(posted["rollup"].competition) == 1
    assert posted["rollup"].themes == ["Steady competitor activity all week"]


def test_rejected_this_week_filters_to_week_range(monkeypatch):
    weekdays = [date(2026, 7, 20), date(2026, 7, 21), date(2026, 7, 22), date(2026, 7, 23), date(2026, 7, 24)]
    rejected_state = {}
    rejected_state = state_module.upsert_rejected_candidate(
        rejected_state,
        RejectedCandidate(
            name="InWeek",
            suggested_type="Competitor",
            region="US",
            why_fits="Fits.",
            source_url="https://example.com/a",
            reason="Weak fit.",
        ),
        today=date(2026, 7, 22),  # inside the week
    )
    rejected_state = state_module.upsert_rejected_candidate(
        rejected_state,
        RejectedCandidate(
            name="BeforeWeek",
            suggested_type="Competitor",
            region="US",
            why_fits="Fits.",
            source_url="https://example.com/b",
            reason="Weak fit.",
        ),
        today=date(2026, 7, 10),  # before the week
    )

    monkeypatch.setattr(weekly_main.state_module, "load_rejected_candidates", lambda: rejected_state)

    result = weekly_main._rejected_this_week(weekdays)

    assert [c.name for c in result] == ["InWeek"]


def test_rejected_this_week_load_failure_returns_empty(monkeypatch):
    def failing_load():
        raise RuntimeError("disk error")

    monkeypatch.setattr(weekly_main.state_module, "load_rejected_candidates", failing_load)

    result = weekly_main._rejected_this_week([date(2026, 7, 20), date(2026, 7, 24)])

    assert result == []


def test_run_attaches_rejected_candidates_to_posted_rollup(monkeypatch):
    one_day = {"date": "2026-07-20", "quiet_day": False}
    monkeypatch.setattr(weekly_main, "_load_week_digests", lambda dir, weekdays: [one_day])
    monkeypatch.setattr(
        weekly_main, "build_weekly_rollup", lambda client, dd, wk, cfg: WeeklyRollup(week_of=wk)
    )
    monkeypatch.setattr(
        weekly_main,
        "_rejected_this_week",
        lambda weekdays: [
            RejectedCandidate(
                name="Bolt",
                suggested_type="Competitor",
                region="US",
                why_fits="Fits.",
                source_url="https://example.com/a",
                reason="Weak fit.",
            )
        ],
    )

    posted = {}

    def fake_post(rollup, cfg, days_covered=None):
        posted["rollup"] = rollup

    monkeypatch.setattr(weekly_main.slack_render, "post_weekly_rollup", fake_post)

    exit_code = weekly_main.run()

    assert exit_code == 0
    assert [c.name for c in posted["rollup"].rejected_candidates] == ["Bolt"]


def test_run_slack_failure_is_nonzero_exit(monkeypatch):
    one_day = {"date": "2026-07-20", "quiet_day": False}
    monkeypatch.setattr(weekly_main, "_load_week_digests", lambda dir, weekdays: [one_day])
    monkeypatch.setattr(
        weekly_main, "build_weekly_rollup", lambda *a, **k: WeeklyRollup(week_of="2026-07-20")
    )

    def failing_post(r, cfg, days_covered=None):
        raise weekly_main.slack_render.SlackDeliveryError("Slack webhook returned 500")

    monkeypatch.setattr(weekly_main.slack_render, "post_weekly_rollup", failing_post)

    exit_code = weekly_main.run()

    assert exit_code == 1
