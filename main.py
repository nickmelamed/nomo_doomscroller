"""Orchestrator: runs the pipeline end to end. See SPEC.md §7, §13."""

from __future__ import annotations

import logging
import re
from difflib import SequenceMatcher

import anthropic

import config
import slack_render
import synthesize
from gather import known_names, run_gather
from models import Candidate, Digest, NewsItem, SourceData

logger = logging.getLogger(__name__)

NAME_MATCH_THRESHOLD = 0.85
# Looser than name matching — two outlets phrase the same story differently.
HEADLINE_MATCH_THRESHOLD = 0.80

_NAME_SUFFIXES = (" inc", " llc", " ltd", " corp", " co")


def _normalize_name(name: str) -> str:
    text = name.casefold().strip()
    text = re.sub(r"[.,]", "", text)
    for suffix in _NAME_SUFFIXES:
        if text.endswith(suffix):
            text = text[: -len(suffix)]
    return re.sub(r"\s+", " ", text).strip()


def _normalize_headline(text: str) -> str:
    normalized = text.casefold().strip()
    normalized = re.sub(r"[^\w\s]", "", normalized)
    return re.sub(r"\s+", " ", normalized).strip()


def _fuzzy_match(a: str, b: str, threshold: float) -> bool:
    if a == b:
        return True
    return SequenceMatcher(None, a, b).ratio() >= threshold


def _names_match(a: str, b: str) -> bool:
    return _fuzzy_match(_normalize_name(a), _normalize_name(b), NAME_MATCH_THRESHOLD)


def _headlines_match(a: str, b: str) -> bool:
    return _fuzzy_match(_normalize_headline(a), _normalize_headline(b), HEADLINE_MATCH_THRESHOLD)


def filter_candidates(candidates: list[Candidate], source_data: SourceData) -> list[Candidate]:
    """§7 Stage 4: drop candidates whose name fuzzy-matches anything already
    tracked (Watchlist + aliases) or excluded (Excluded/Paused/Converted
    watchlist rows, Active Partners-source rows)."""
    known = known_names(source_data)

    survivors = []
    for candidate in candidates:
        match = next((name for name in known if _names_match(candidate.name, name)), None)
        if match is not None:
            logger.info(
                "dedup: dropping candidate %r — matches already-known name %r",
                candidate.name,
                match,
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
        monitoring_items, industry_items, candidates = run_gather(client, source_data, cfg)
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
            client, deduped_monitoring, deduped_industry, filtered_candidates, source_data, cfg
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
    return 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s:%(name)s:%(message)s")
    raise SystemExit(run())
