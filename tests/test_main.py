from datetime import datetime, timezone

import main
from models import Candidate, Criteria, Digest, Entity, NewsItem, SourceData

UBER = Entity(name="Uber", type="Competitor", status="Active", source="watchlist")
FEVER = Entity(name="Fever", type="Existing partner", status="Active", source="partners_db")

BASE_SOURCE_DATA = SourceData(
    entities=[UBER, FEVER],
    excluded_names={"Fever"},
    reward_landscape=["Fever: live-events redemption"],
    criteria=Criteria(nomo_context="NOMO context."),
)

EMPTY_DIGEST = Digest(quiet_day=True, tracking_counts={"competitors": 1, "partner_prospects": 0})

EMPTY_STATE = {"seen_stories": {}, "last_success": None}


def install_state(monkeypatch, initial_state=None):
    """Stubs state persistence so tests never touch the real filesystem;
    returns a dict tracking whether save_state/save_digest_archive were
    called and with what."""
    saved = {"called": False, "state": None, "archive_called": False, "archived_digest": None}

    def fake_load_state():
        return dict(initial_state) if initial_state is not None else dict(EMPTY_STATE)

    def fake_save_state(state):
        saved["called"] = True
        saved["state"] = state

    def fake_save_digest_archive(digest, run_date):
        saved["archive_called"] = True
        saved["archived_digest"] = digest

    monkeypatch.setattr(main.state_module, "load_state", fake_load_state)
    monkeypatch.setattr(main.state_module, "save_state", fake_save_state)
    monkeypatch.setattr(main.state_module, "save_digest_archive", fake_save_digest_archive)
    return saved


def install_happy_path(monkeypatch, source_data=BASE_SOURCE_DATA, digest=EMPTY_DIGEST):
    calls = {"gather": None, "synthesize": None, "post": None}

    def fake_load_source_data(cfg):
        return source_data

    def fake_run_gather(client, sd, cfg):
        calls["gather"] = (sd, cfg)
        return [], [], []

    def fake_synthesize(client, monitoring, industry, candidates, sd, cfg, state=None):
        calls["synthesize"] = (monitoring, industry, candidates, sd, cfg, state)
        return digest

    def fake_post_digest(d, cfg):
        calls["post"] = (d, cfg)

    monkeypatch.setattr(main, "load_source_data", fake_load_source_data)
    monkeypatch.setattr(main, "run_gather", fake_run_gather)
    monkeypatch.setattr(main.synthesize, "synthesize", fake_synthesize)
    monkeypatch.setattr(main.slack_render, "post_digest", fake_post_digest)
    install_state(monkeypatch)
    return calls


def test_happy_path_returns_zero_and_calls_every_stage(monkeypatch):
    calls = install_happy_path(monkeypatch)
    exit_code = main.run()

    assert exit_code == 0
    assert calls["gather"] is not None
    assert calls["synthesize"] is not None
    assert calls["post"] is not None
    assert calls["post"][0] is EMPTY_DIGEST


def test_stage1_failure_aborts_before_any_other_stage(monkeypatch):
    calls = {"gather": False, "synthesize": False, "post": False}

    def failing_load_source_data(cfg):
        raise RuntimeError("Sheets unreachable")

    def should_not_be_called(*args, **kwargs):
        calls["gather"] = True
        raise AssertionError("run_gather should not be called after a Stage 1 failure")

    monkeypatch.setattr(main, "load_source_data", failing_load_source_data)
    monkeypatch.setattr(main, "run_gather", should_not_be_called)
    monkeypatch.setattr(
        main.synthesize,
        "synthesize",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("should not be called")),
    )
    monkeypatch.setattr(
        main.slack_render,
        "post_digest",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("should not be called")),
    )

    exit_code = main.run()

    assert exit_code == 1
    assert calls["gather"] is False


def test_stage2_gather_failure_continues_with_empty_results(monkeypatch):
    def fake_load_source_data(cfg):
        return BASE_SOURCE_DATA

    def failing_run_gather(client, sd, cfg):
        raise RuntimeError("unexpected gather crash")

    synthesize_args = {}

    def fake_synthesize(client, monitoring, industry, candidates, sd, cfg, state=None):
        synthesize_args["monitoring"] = monitoring
        synthesize_args["industry"] = industry
        synthesize_args["candidates"] = candidates
        return EMPTY_DIGEST

    posted = {}

    def fake_post_digest(d, cfg):
        posted["digest"] = d

    monkeypatch.setattr(main, "load_source_data", fake_load_source_data)
    monkeypatch.setattr(main, "run_gather", failing_run_gather)
    monkeypatch.setattr(main.synthesize, "synthesize", fake_synthesize)
    monkeypatch.setattr(main.slack_render, "post_digest", fake_post_digest)
    install_state(monkeypatch)

    exit_code = main.run()

    assert exit_code == 0
    assert synthesize_args["monitoring"] == []
    assert synthesize_args["industry"] == []
    assert synthesize_args["candidates"] == []
    assert "digest" in posted


def test_stage5_synthesize_failure_is_fatal_and_never_posts(monkeypatch):
    post_called = {"value": False}

    def fake_load_source_data(cfg):
        return BASE_SOURCE_DATA

    def fake_run_gather(client, sd, cfg):
        return [], [], []

    def failing_synthesize(client, monitoring, industry, candidates, sd, cfg, state=None):
        raise RuntimeError("malformed synthesis JSON")

    def fake_post_digest(d, cfg):
        post_called["value"] = True

    monkeypatch.setattr(main, "load_source_data", fake_load_source_data)
    monkeypatch.setattr(main, "run_gather", fake_run_gather)
    monkeypatch.setattr(main.synthesize, "synthesize", failing_synthesize)
    monkeypatch.setattr(main.slack_render, "post_digest", fake_post_digest)
    install_state(monkeypatch)

    exit_code = main.run()

    assert exit_code == 1
    assert post_called["value"] is False


def test_stage6_slack_failure_is_reported_as_nonzero_exit(monkeypatch):
    calls = install_happy_path(monkeypatch)

    def failing_post_digest(d, cfg):
        raise main.slack_render.SlackDeliveryError("Slack webhook returned 500")

    monkeypatch.setattr(main.slack_render, "post_digest", failing_post_digest)

    exit_code = main.run()
    assert exit_code == 1


def test_state_is_saved_only_after_full_success(monkeypatch):
    install_happy_path(monkeypatch)
    saved_state = install_state(monkeypatch)  # re-patch to get a fresh tracker

    exit_code = main.run()

    assert exit_code == 0
    assert saved_state["called"] is True
    assert saved_state["state"]["last_success"] is not None
    assert saved_state["archive_called"] is True
    assert saved_state["archived_digest"] is EMPTY_DIGEST


def test_state_is_not_saved_when_stage5_fails(monkeypatch):
    def fake_load_source_data(cfg):
        return BASE_SOURCE_DATA

    def fake_run_gather(client, sd, cfg):
        return [], [], []

    def failing_synthesize(client, monitoring, industry, candidates, sd, cfg, state=None):
        raise RuntimeError("malformed synthesis JSON")

    monkeypatch.setattr(main, "load_source_data", fake_load_source_data)
    monkeypatch.setattr(main, "run_gather", fake_run_gather)
    monkeypatch.setattr(main.synthesize, "synthesize", failing_synthesize)
    monkeypatch.setattr(
        main.slack_render,
        "post_digest",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("should not be called")),
    )
    saved_state = install_state(monkeypatch)

    exit_code = main.run()

    assert exit_code == 1
    assert saved_state["called"] is False
    assert saved_state["archive_called"] is False


def test_state_is_not_saved_when_stage6_fails(monkeypatch):
    install_happy_path(monkeypatch)
    saved_state = install_state(monkeypatch)

    def failing_post_digest(d, cfg):
        raise main.slack_render.SlackDeliveryError("Slack webhook returned 500")

    monkeypatch.setattr(main.slack_render, "post_digest", failing_post_digest)

    exit_code = main.run()

    assert exit_code == 1
    assert saved_state["called"] is False
    assert saved_state["archive_called"] is False


def test_record_seen_stories_adds_new_entries():
    from datetime import date

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

    updated = main._record_seen_stories({}, [item], today=date(2026, 7, 30))

    assert len(updated) == 1
    entry = next(iter(updated.values()))
    assert entry["first_seen"] == "2026-07-30"
    assert entry["entity_or_topic"] == "Uber"


def test_record_seen_stories_preserves_existing_first_seen():
    import state as state_module
    from datetime import date

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
    story_hash = state_module.hash_story("Uber", item.headline)
    existing = {story_hash: {"first_seen": "2026-07-20", "headline": item.headline, "entity_or_topic": "Uber"}}

    updated = main._record_seen_stories(existing, [item], today=date(2026, 7, 30))

    assert updated[story_hash]["first_seen"] == "2026-07-20"


def test_effective_gather_config_widens_window_after_a_stale_gap():
    from config import Config

    cfg = Config(
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
    now = datetime(2026, 7, 30, 15, 0, tzinfo=timezone.utc)
    stale_state = {"seen_stories": {}, "last_success": "2026-07-28T13:00:00+00:00"}  # 50h ago

    effective = main._effective_gather_config(cfg, stale_state, now)

    assert effective.news_window_hours == 50


def test_effective_gather_config_unchanged_within_normal_gap():
    from config import Config

    cfg = Config(
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
    now = datetime(2026, 7, 30, 13, 30, tzinfo=timezone.utc)
    recent_state = {"seen_stories": {}, "last_success": "2026-07-30T13:00:00+00:00"}  # 30m ago

    effective = main._effective_gather_config(cfg, recent_state, now)

    assert effective is cfg


def test_effective_gather_config_unchanged_with_no_prior_success():
    from config import Config

    cfg = Config(
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
    now = datetime(2026, 7, 30, 13, 30, tzinfo=timezone.utc)

    effective = main._effective_gather_config(cfg, EMPTY_STATE, now)

    assert effective is cfg


def test_dedup_and_filter_are_actually_applied_before_synthesize(monkeypatch):
    duplicate_url_item = NewsItem(
        headline="Uber news",
        url="https://example.com/a",
        source="X",
        published="2026-07-29",
        summary="S",
        why_it_matters="W",
        relevance="high",
        entity="Uber",
    )
    duplicate_again = NewsItem(
        headline="Uber news (wire copy)",
        url="https://example.com/a",
        source="Y",
        published="2026-07-29",
        summary="S2",
        why_it_matters="W2",
        relevance="high",
        entity="Uber",
    )
    industry_item = NewsItem(
        headline="Policy news",
        url="https://example.com/b",
        source="Reuters",
        published="2026-07-29",
        summary="S",
        why_it_matters="W",
        relevance="high",
        topic="youth policy",
    )
    known_candidate = Candidate(
        name="Fever",  # already an active partner — must be filtered out
        suggested_type="Rewards partner prospect",
        region="BR",
        why_fits="...",
        source_url="https://example.com/c",
    )
    new_candidate = Candidate(
        name="GenuinelyNewCo",
        suggested_type="Competitor",
        region="US",
        why_fits="...",
        source_url="https://example.com/d",
    )

    def fake_load_source_data(cfg):
        return BASE_SOURCE_DATA

    def fake_run_gather(client, sd, cfg):
        return (
            [duplicate_url_item, duplicate_again],
            [industry_item],
            [known_candidate, new_candidate],
        )

    captured = {}

    def fake_synthesize(client, monitoring, industry, candidates, sd, cfg, state=None):
        captured["monitoring"] = monitoring
        captured["industry"] = industry
        captured["candidates"] = candidates
        return EMPTY_DIGEST

    monkeypatch.setattr(main, "load_source_data", fake_load_source_data)
    monkeypatch.setattr(main, "run_gather", fake_run_gather)
    monkeypatch.setattr(main.synthesize, "synthesize", fake_synthesize)
    monkeypatch.setattr(main.slack_render, "post_digest", lambda d, cfg: None)
    install_state(monkeypatch)

    exit_code = main.run()

    assert exit_code == 0
    assert len(captured["monitoring"]) == 1  # duplicate URL collapsed
    assert len(captured["industry"]) == 1
    assert [c.name for c in captured["candidates"]] == ["GenuinelyNewCo"]
