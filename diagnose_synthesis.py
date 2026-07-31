"""Diagnostic-only run: Stages 1-5 against real data with verbose synthesis
logging forced on, printed to stdout. Deliberately stops before Stage 6 (never
calls slack_render.post_digest) so it's safe to run repeatedly against the
real Sheets/Notion source without posting to Slack. See the v2 plan, Phase 10.
"""

from __future__ import annotations

import dataclasses
import logging

import anthropic

import config
import synthesize
from gather import run_gather
from main import _dedup_and_split, filter_candidates, load_source_data

logger = logging.getLogger(__name__)


def main() -> None:
    cfg = dataclasses.replace(config.config, synthesis_verbose_log=True)
    client = anthropic.Anthropic(api_key=cfg.anthropic_api_key)

    source_data = load_source_data(cfg)
    monitoring_items, industry_items, candidates = run_gather(client, source_data, cfg)
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
        f"new_candidates={len(digest.new_candidates)}"
    )
    print("\n(Stopped before Stage 6 — nothing was posted to Slack.)")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s:%(name)s:%(message)s")
    main()
