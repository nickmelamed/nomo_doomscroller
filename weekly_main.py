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
import slack_render
import state as state_module
from weekly_synthesize import build_weekly_rollup

logger = logging.getLogger(__name__)


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
    """Returns the process exit code."""
    cfg = config.config
    client = anthropic.Anthropic(api_key=cfg.anthropic_api_key)

    today = date.today()
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
