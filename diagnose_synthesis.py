"""Diagnostic-only run: Stages 1-5 against real data with verbose synthesis
logging forced on, printed to stdout. Never posts to the real Slack channel —
Stage 6 only runs, against a separate SLACK_PREVIEW_WEBHOOK_URL, if that env
var is explicitly set (e.g. a private test channel's webhook); left unset,
this is exactly as safe to run repeatedly as before. Uses the same gap-
recovery window logic as main.py (_effective_gather_config) against the real
persisted state, so a stale/short gap since the last real run is reflected
here too — not just when the real pipeline runs. See the v2 plan, Phase 10,
and the visual/content-preview follow-up.
"""

from __future__ import annotations

import dataclasses
import logging
import os
from datetime import datetime, timezone

import anthropic

import config
import slack_render
import state as state_module
import synthesize
from gather import run_gather
from main import _dedup_and_split, _effective_gather_config, filter_candidates, load_source_data

logger = logging.getLogger(__name__)


def main() -> None:
    cfg = dataclasses.replace(config.config, synthesis_verbose_log=True)
    client = anthropic.Anthropic(api_key=cfg.anthropic_api_key)

    source_data = load_source_data(cfg)

    try:
        pipeline_state = state_module.load_state()
    except Exception:
        logger.exception("failed to load persisted state — continuing with empty state")
        pipeline_state = {"seen_stories": {}, "last_success": None}

    gather_cfg = _effective_gather_config(cfg, pipeline_state, datetime.now(timezone.utc))
    if gather_cfg.news_window_hours != cfg.news_window_hours:
        print(
            f"\n(gap recovery: widening gather window to {gather_cfg.news_window_hours}h, "
            "same as a real run would)"
        )

    monitoring_items, industry_items, candidates = run_gather(client, source_data, gather_cfg)
    deduped_monitoring, deduped_industry = _dedup_and_split(monitoring_items, industry_items)
    filtered_candidates = filter_candidates(candidates, source_data)

    print(f"\nCandidates surfaced (post dedup/filter): {len(filtered_candidates)}")
    for c in filtered_candidates:
        print(f"  - {c.name!r} (confidence={c.confidence}, region={c.region})")

    digest = synthesize.synthesize(
        client, deduped_monitoring, deduped_industry, filtered_candidates, source_data, cfg
    )

    survived = {c.name for c in digest.new_candidates}
    print(f"\nSurvived into new_candidates: {len(survived)}/{len(filtered_candidates)}")
    print("\nPer-candidate outcome:")
    for c in filtered_candidates:
        status = "SURVIVED" if c.name in survived else "REJECTED"
        print(f"  [{status}] {c.name!r} (confidence={c.confidence})")

    print(
        f"\nDigest summary: quiet_day={digest.quiet_day} "
        f"competition={len(digest.competition)} industry={len(digest.industry)} "
        f"partner_prospects={len(digest.partner_prospects)} "
        f"gtm_prospects={len(digest.gtm_prospects)} "
        f"new_candidates={len(digest.new_candidates)}"
    )

    preview_webhook_url = os.environ.get("SLACK_PREVIEW_WEBHOOK_URL")
    if preview_webhook_url:
        preview_cfg = dataclasses.replace(cfg, slack_webhook_url=preview_webhook_url)
        slack_render.post_digest(digest, preview_cfg)
        print("\nPosted to SLACK_PREVIEW_WEBHOOK_URL for visual review.")
    else:
        print(
            "\n(Nothing posted to Slack — set SLACK_PREVIEW_WEBHOOK_URL to a "
            "test channel's webhook to also post this digest there.)"
        )


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s:%(name)s:%(message)s")
    main()
