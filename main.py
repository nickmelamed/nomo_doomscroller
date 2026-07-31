"""Orchestrator: runs the pipeline end to end. See SPEC.md §7, §13."""

from __future__ import annotations

import dataclasses
import logging
from datetime import date, datetime, timezone

import anthropic

import config
import slack_render
import state as state_module
import synthesize
from gather import known_names, run_gather
from models import Candidate, Digest, NewsItem, SourceData
from text_match import headlines_match as _headlines_match
from text_match import names_match as _names_match

logger = logging.getLogger(__name__)


def filter_candidates(candidates: list[Candidate], source_data: SourceData) -> list[Candidate]:
    """§7 Stage 4: drop candidates whose name fuzzy-matches anything already
    tracked (Watchlist + aliases) or excluded (Excluded/Paused/Converted
    watchlist rows, Active Partners-source rows) — and also drop candidates
    that duplicate another candidate already surfaced in this same batch
    (different scout angles frequently rediscover the same entity), so
    synthesis doesn't have to notice and self-merge duplicates itself."""
    known = known_names(source_data)

    survivors: list[Candidate] = []
    for candidate in candidates:
        match = next((name for name in known if _names_match(candidate.name, name)), None)
        if match is not None:
            logger.info(
                "dedup: dropping candidate %r — matches already-known name %r",
                candidate.name,
                match,
            )
            continue
        duplicate = next(
            (existing for existing in survivors if _names_match(candidate.name, existing.name)),
            None,
        )
        if duplicate is not None:
            logger.info(
                "dedup: dropping candidate %r — duplicate of already-surfaced candidate %r",
                candidate.name,
                duplicate.name,
            )
            continue
        survivors.append(candidate)

    logger.info(
        "dedup: %d candidates in, %d survived filtering", len(candidates), len(survivors)
    )
    return survivors


def dedup_news_items(items: list[NewsItem]) -> list[NewsItem]:
    """§7 Stage 4: dedup entity monitoring + industry trend items together, by
    URL first, then near-duplicate headline (looser ratio, since outlets
    phrase the same story differently)."""
    seen_urls: set[str] = set()
    survivors: list[NewsItem] = []

    for item in items:
        if item.url in seen_urls:
            logger.info("dedup: dropping item %r — duplicate URL", item.headline)
            continue
        duplicate = next(
            (
                existing
                for existing in survivors
                if _headlines_match(item.headline, existing.headline)
            ),
            None,
        )
        if duplicate is not None:
            logger.info(
                "dedup: dropping item %r — near-duplicate of %r",
                item.headline,
                duplicate.headline,
            )
            continue
        seen_urls.add(item.url)
        survivors.append(item)

    logger.info("dedup: %d items in, %d survived deduplication", len(items), len(survivors))
    return survivors


def load_source_data(cfg: config.Config) -> SourceData:
    """§7 Stage 1 — picks the backend by DATA_SOURCE (§6.0). Fails loudly
    (propagates) if the active backend's source is unreachable; that's a
    fatal, whole-run condition, not a per-call one."""
    if cfg.data_source == "sheets":
        from sources.sheets_source import SheetsSource

        backend = SheetsSource(
            spreadsheet_id=cfg.google_sheets_id,
            service_account_json=cfg.google_service_account_json,
        )
    else:
        from sources.notion_source import NotionSource

        backend = NotionSource(
            api_key=cfg.notion_api_key,
            watchlist_db_id=cfg.notion_watchlist_db_id,
            criteria_page_id=cfg.notion_criteria_page_id,
            topics_db_id=cfg.notion_topics_db_id,
            partners_db_id=cfg.notion_partners_db_id,
        )
    return backend.load_all()


def _record_seen_stories(
    seen_stories: dict, items: list[NewsItem], today: date
) -> dict:
    """v2 Phase 16: adds a first-seen entry for each item not already known;
    preserves the original first_seen for stories seen in a prior run."""
    updated = dict(seen_stories)
    for item in items:
        entity_or_topic = item.entity or item.topic or ""
        story_hash = state_module.hash_story(entity_or_topic, item.headline)
        if story_hash not in updated:
            updated[story_hash] = {
                "first_seen": today.isoformat(),
                "headline": item.headline,
                "entity_or_topic": entity_or_topic,
            }
    return updated


def _effective_gather_config(
    cfg: config.Config, pipeline_state: dict, now: datetime
) -> config.Config:
    """v2 Phase 17: if the last successful run was longer ago than
    NEWS_WINDOW_HOURS (e.g. a prior run failed outright), widen the
    effective gather window to cover the gap instead of missing news that
    fell between the two runs. No prior last_success -> unchanged."""
    last_success = pipeline_state.get("last_success")
    if not last_success:
        return cfg
    gap_hours = state_module.hours_since(last_success, now)
    if gap_hours <= cfg.news_window_hours:
        return cfg
    logger.info(
        "run-gap recovery: last success %.1fh ago (> NEWS_WINDOW_HOURS=%d) — "
        "widening gather window to %.1fh",
        gap_hours,
        cfg.news_window_hours,
        gap_hours,
    )
    return dataclasses.replace(cfg, news_window_hours=int(gap_hours))


def _dedup_and_split(
    monitoring_items: list[NewsItem], industry_items: list[NewsItem]
) -> tuple[list[NewsItem], list[NewsItem]]:
    """§7 Stage 4 dedupes monitoring + industry items together (a story could
    surface from both an entity call and a topic call), then splits back into
    the two lists synthesize.py expects — NewsItem always has exactly one of
    entity/topic set, so this split is lossless."""
    deduped = dedup_news_items(monitoring_items + industry_items)
    deduped_monitoring = [item for item in deduped if item.entity is not None]
    deduped_industry = [item for item in deduped if item.topic is not None]
    return deduped_monitoring, deduped_industry


def run() -> int:
    """Runs the full pipeline end to end (§7). Returns the process exit code."""
    cfg = config.config
    client = anthropic.Anthropic(api_key=cfg.anthropic_api_key)

    try:
        source_data = load_source_data(cfg)
    except Exception:
        logger.exception("Stage 1 (load config) failed — aborting run")
        return 1

    watchlist_entities = [e for e in source_data.entities if e.source == "watchlist"]
    partners_entities = [e for e in source_data.entities if e.source == "partners_db"]
    logger.info(
        "Stage 1: loaded %d watchlist entities, %d partners-source entities, "
        "%d industry topics, %d excluded names, %d reward-landscape lines",
        len(watchlist_entities),
        len(partners_entities),
        len(source_data.industry_topics),
        len(source_data.excluded_names),
        len(source_data.reward_landscape),
    )

    try:
        pipeline_state = state_module.load_state()
    except Exception:
        logger.exception("failed to load persisted state — continuing with empty state")
        pipeline_state = {"seen_stories": {}, "last_success": None}

    gather_cfg = _effective_gather_config(cfg, pipeline_state, datetime.now(timezone.utc))

    try:
        monitoring_items, industry_items, candidates = run_gather(client, source_data, gather_cfg)
    except Exception:
        # gather.py already logs and continues on a per-call basis; this is a
        # defensive fallback for a genuinely unexpected failure at the batch
        # level, matching §13's resilience requirement even in that case.
        logger.exception("Stage 2-3 (gather) failed unexpectedly — continuing with no results")
        monitoring_items, industry_items, candidates = [], [], []

    logger.info(
        "Stage 2-3: %d monitoring items, %d industry items, %d candidates surfaced (pre-filter)",
        len(monitoring_items),
        len(industry_items),
        len(candidates),
    )

    deduped_monitoring, deduped_industry = _dedup_and_split(monitoring_items, industry_items)
    filtered_candidates = filter_candidates(candidates, source_data)
    logger.info(
        "Stage 4: %d news items after dedup, %d candidates after filtering",
        len(deduped_monitoring) + len(deduped_industry),
        len(filtered_candidates),
    )

    try:
        digest: Digest = synthesize.synthesize(
            client,
            deduped_monitoring,
            deduped_industry,
            filtered_candidates,
            source_data,
            cfg,
            state=pipeline_state,
        )
    except Exception:
        logger.exception("Stage 5 (synthesize) failed — aborting run without posting")
        return 1

    logger.info(
        "Stage 5: digest ready (quiet_day=%s) — competition=%d industry=%d "
        "partner_prospects=%d new_candidates=%d",
        digest.quiet_day,
        len(digest.competition),
        len(digest.industry),
        len(digest.partner_prospects),
        len(digest.new_candidates),
    )

    try:
        slack_render.post_digest(digest, cfg)
    except Exception:
        logger.exception("Stage 6 (Slack post) failed")
        return 1

    logger.info("Stage 6: digest posted successfully")

    today = date.today()
    updated_seen_stories = _record_seen_stories(
        pipeline_state["seen_stories"], deduped_monitoring + deduped_industry, today
    )
    updated_state = state_module.prune_state(
        {
            "seen_stories": updated_seen_stories,
            "last_success": datetime.now(timezone.utc).isoformat(),
        },
        now=today,
    )
    try:
        state_module.save_state(updated_state)
    except Exception:
        # A persisted-state write failure shouldn't turn a successfully
        # posted digest into a failing run — log and move on.
        logger.exception("failed to save persisted state — digest was still posted successfully")

    try:
        state_module.save_digest_archive(digest, today)
    except Exception:
        logger.exception("failed to archive digest — digest was still posted successfully")

    return 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s:%(name)s:%(message)s")
    raise SystemExit(run())
