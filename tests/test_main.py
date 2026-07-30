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


def install_happy_path(monkeypatch, source_data=BASE_SOURCE_DATA, digest=EMPTY_DIGEST):
    calls = {"gather": None, "synthesize": None, "post": None}

    def fake_load_source_data(cfg):
        return source_data

    def fake_run_gather(client, sd, cfg):
        calls["gather"] = (sd, cfg)
        return [], [], []

    def fake_synthesize(client, monitoring, industry, candidates, sd, cfg):
        calls["synthesize"] = (monitoring, industry, candidates, sd, cfg)
        return digest

    def fake_post_digest(d, cfg):
        calls["post"] = (d, cfg)

    monkeypatch.setattr(main, "load_source_data", fake_load_source_data)
    monkeypatch.setattr(main, "run_gather", fake_run_gather)
    monkeypatch.setattr(main.synthesize, "synthesize", fake_synthesize)
    monkeypatch.setattr(main.slack_render, "post_digest", fake_post_digest)
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

    def fake_synthesize(client, monitoring, industry, candidates, sd, cfg):
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

    def failing_synthesize(client, monitoring, industry, candidates, sd, cfg):
        raise RuntimeError("malformed synthesis JSON")

    def fake_post_digest(d, cfg):
        post_called["value"] = True

    monkeypatch.setattr(main, "load_source_data", fake_load_source_data)
    monkeypatch.setattr(main, "run_gather", fake_run_gather)
    monkeypatch.setattr(main.synthesize, "synthesize", failing_synthesize)
    monkeypatch.setattr(main.slack_render, "post_digest", fake_post_digest)

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
        suggested_type="Partner prospect",
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

    def fake_synthesize(client, monitoring, industry, candidates, sd, cfg):
        captured["monitoring"] = monitoring
        captured["industry"] = industry
        captured["candidates"] = candidates
        return EMPTY_DIGEST

    monkeypatch.setattr(main, "load_source_data", fake_load_source_data)
    monkeypatch.setattr(main, "run_gather", fake_run_gather)
    monkeypatch.setattr(main.synthesize, "synthesize", fake_synthesize)
    monkeypatch.setattr(main.slack_render, "post_digest", lambda d, cfg: None)

    exit_code = main.run()

    assert exit_code == 0
    assert len(captured["monitoring"]) == 1  # duplicate URL collapsed
    assert len(captured["industry"]) == 1
    assert [c.name for c in captured["candidates"]] == ["GenuinelyNewCo"]
