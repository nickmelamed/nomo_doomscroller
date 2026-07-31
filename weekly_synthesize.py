"""Weekly rollup Claude call -> WeeklyRollup. Reuses Stage 5's call plumbing
(synthesize._call_and_parse) but feeds a week of already-archived daily
Digest JSON instead of one day's raw gathered items. See the v2 plan, Phase
19; weekly_main.py is the orchestrator that calls this."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from string import Template

from config import Config
from models import WeeklyRollup
from synthesize import _call_and_parse, _to_candidate, _to_digest_item

logger = logging.getLogger(__name__)

PROMPTS_DIR = Path(__file__).parent / "prompts"

WEEKLY_ROLLUP_SCHEMA = json.dumps(
    {
        "week_of": "YYYY-MM-DD",
        "sections": {
            "competition": [
                {"headline": "string", "url": "string", "source": "string", "summary": "string"}
            ],
            "industry": [
                {"headline": "string", "url": "string", "source": "string", "summary": "string"}
            ],
            "partner_prospects": [
                {"headline": "string", "url": "string", "source": "string", "summary": "string"}
            ],
            "gtm_prospects": [
                {"headline": "string", "url": "string", "source": "string", "summary": "string"}
            ],
        },
        "notable_candidates": [
            {
                "name": "string",
                "suggested_type": "Competitor|Rewards partner prospect|GTM partner prospect",
                "region": "string",
                "why_fits": "string",
                "source_url": "string",
            }
        ],
        "themes": ["string"],
    }
)


def _render_prompt(filename: str, **kwargs) -> str:
    template_text = (PROMPTS_DIR / filename).read_text()
    return Template(template_text).substitute(**kwargs)


def build_weekly_rollup(
    client, daily_digests: list[dict], week_of: str, config: Config
) -> WeeklyRollup:
    """`daily_digests` is a list of raw archived-digest dicts (one per day
    that has an archive, per state.save_digest_archive) — not re-gathered
    from scratch. Caller (weekly_main.py) is responsible for skipping the
    call entirely when there's nothing archived for the week."""
    prompt = _render_prompt(
        "weekly_rollup.txt",
        week_of=week_of,
        days_covered=len(daily_digests),
        schema=WEEKLY_ROLLUP_SCHEMA,
        payload=json.dumps(daily_digests, indent=2),
    )
    result = _call_and_parse(client, config, prompt)

    sections = result.get("sections", {})
    return WeeklyRollup(
        week_of=result.get("week_of", week_of),
        competition=[_to_digest_item(d) for d in sections.get("competition", [])],
        industry=[_to_digest_item(d) for d in sections.get("industry", [])],
        partner_prospects=[_to_digest_item(d) for d in sections.get("partner_prospects", [])],
        gtm_prospects=[_to_digest_item(d) for d in sections.get("gtm_prospects", [])],
        notable_candidates=[_to_candidate(d) for d in result.get("notable_candidates", [])],
        themes=result.get("themes", []),
    )
