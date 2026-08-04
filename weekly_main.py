"""Weekly rollup orchestrator: reads the prior Mon-Fri's archived daily
digests and posts a "here's what you missed" summary. Deliberately separate
from main.py::run() — a rollup skips Stages 1-4 entirely (no source load, no
gather, no dedup; the input is already-synthesized digest JSON) and only
reuses Stage 5's call plumbing and Stage 6's render helpers. See the v2 plan,
Phase 19."""

from __future__ import annotations

import json
import logging
from datetime import date, timedelta
from pathlib import Path

import anthropic

import config
import metrics
import slack_render
import state as state_module
from weekly_synthesize import build_weekly_rollup

logger = logging.getLogger(__name__)


def _rejected_this_week(weekdays: list[date]) -> list:
    """Deterministic, no LLM call: pulls the week's rejected candidates
    straight from persisted state (state.rejected_candidates.json) rather
    than asking the model to reprocess them, so this section costs nothing
    in tokens."""
    try:
        rejected_state = state_module.load_rejected_candidates()
    except Exception:
        logger.exception("failed to load persisted rejected-candidates state — continuing with none")
        return []

    week_start, week_end = weekdays[0], weekdays[-1]
    return [
        state_module.entry_to_candidate(entry)
        for entry in rejected_state.values()
        if week_start <= date.fromisoformat(entry["last_rejected"]) <= week_end
    ]


def _metrics_this_week(weekdays: list[date]) -> list[dict]:
    """Reads whichever of the week's state/metrics/*.jsonl files exist — a
    day with a failed run, or one before this feature shipped, simply has no
    file and is skipped. Mirrors _load_week_digests's skip-don't-error
    handling for the same reason."""
    calls: list[dict] = []
    for day in weekdays:
        path = state_module.DEFAULT_METRICS_DIR / f"{day.isoformat()}.jsonl"
        if not path.exists():
            continue
        with path.open() as f:
            for line in f:
                line = line.strip()
                if line:
                    calls.append(json.loads(line))
    return calls


def _prior_week_weekdays(today: date) -> list[date]:
    """The 5 weekdays of the most recently completed Mon-Fri period before
    today's week — works whether this runs on a Monday-morning cron or an
    ad-hoc manual dispatch on some other day."""
    this_monday = today - timedelta(days=today.weekday())
    prior_monday = this_monday - timedelta(days=7)
    return [prior_monday + timedelta(days=i) for i in range(5)]


def _load_week_digests(dir: Path, weekdays: list[date]) -> list[dict]:
    """Reads whichever of the week's daily archives exist — a day with a
    failed run (or one that predates Phase 18 shipping) simply has no
    archive and is skipped, not an error."""
    digests = []
    for day in weekdays:
        path = dir / f"{day.isoformat()}.json"
        if not path.exists():
            continue
        with path.open() as f:
            data = json.load(f)
        digests.append({"date": day.isoformat(), **data})
    return digests


def run() -> int:
    """Returns the process exit code. Wraps _run() so metrics recorded
    before a mid-run failure (e.g. weekly_synthesize's LLM call) still get
    persisted — mirrors main.py's run()/_run() split."""
    today = date.today()
    try:
        return _run(today)
    finally:
        calls = metrics.drain()
        try:
            state_module.append_metrics(calls, today)
        except Exception:
            logger.exception("failed to save call metrics — run outcome unaffected")


def _run(today: date) -> int:
    cfg = config.config
    client = anthropic.Anthropic(api_key=cfg.anthropic_api_key)

    weekdays = _prior_week_weekdays(today)
    daily_digests = _load_week_digests(state_module.DEFAULT_DIGEST_ARCHIVE_DIR, weekdays)

    logger.info(
        "weekly rollup: found %d of %d archived daily digests for week of %s",
        len(daily_digests),
        len(weekdays),
        weekdays[0].isoformat(),
    )

    if not daily_digests:
        logger.info("no archived digests for the prior week — nothing to roll up, skipping post")
        return 0

    try:
        rollup = build_weekly_rollup(client, daily_digests, weekdays[0].isoformat(), cfg)
    except Exception:
        logger.exception("weekly rollup synthesis failed — aborting without posting")
        return 1

    rollup.rejected_candidates = _rejected_this_week(weekdays)

    week_calls = _metrics_this_week(weekdays)
    rollup.metrics_summary = {
        **metrics.summarize(week_calls),
        "by_stage": metrics.summarize_by_stage(week_calls),
    }

    try:
        slack_render.post_weekly_rollup(rollup, cfg, days_covered=len(daily_digests))
    except Exception:
        logger.exception("weekly rollup Slack post failed")
        return 1

    logger.info("weekly rollup posted successfully")
    return 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s:%(name)s:%(message)s")
    raise SystemExit(run())
