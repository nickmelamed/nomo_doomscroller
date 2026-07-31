"""Synthesis Claude call -> digest JSON. See SPEC.md §7 Stage 5, §8.3."""

from __future__ import annotations

import json
import logging
from dataclasses import asdict
from datetime import date
from pathlib import Path
from string import Template

import state as state_module
from config import Config
from gather import extract_text, parse_json_response
from models import Candidate, Digest, DigestItem, Entity, NewsItem, SourceData

logger = logging.getLogger(__name__)

PROMPTS_DIR = Path(__file__).parent / "prompts"
# Generous headroom: a realistic-sized run (a dozen-plus candidates, several
# news items) plus adaptive thinking can produce a much larger JSON output
# than small fixtures do — observed a truncated/malformed response at 8192
# against real data (13 candidates, 8 items).
SYNTHESIZE_MAX_TOKENS = 16384

VERBOSE_INSTRUCTIONS_BLOCK = (
    "\nAlso include a top-level `rejected_candidates` array: one entry "
    '{"name": "...", "reason": "..."} for every candidate in the input '
    '`candidates` list that did NOT survive into `new_candidates`, with a '
    "one-line reason it was cut (e.g. off-criteria, vague why_fits, weak "
    "region fit, too similar to an existing partner/competitor). Every input "
    "candidate must appear in exactly one of `new_candidates` or "
    "`rejected_candidates`, never both, never neither.\n"
)

DIGEST_SCHEMA = json.dumps(
    {
        "quiet_day": False,
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
            "new_candidates": [
                {
                    "name": "string",
                    "suggested_type": "Competitor|Rewards partner prospect|GTM partner prospect",
                    "region": "string",
                    "why_fits": "string",
                    "source_url": "string",
                }
            ],
        },
        "tracking_counts": {"competitors": 0, "partner_prospects": 0, "gtm_prospects": 0},
    }
)


def _render_prompt(filename: str, **kwargs) -> str:
    template_text = (PROMPTS_DIR / filename).read_text()
    return Template(template_text).substitute(**kwargs)


def _one_line_summary(text: str, max_len: int = 160) -> str:
    """Compresses a criteria section down to roughly its first sentence, so
    synthesis can be anchored to the same fit bar scouting used without
    re-feeding the full criteria text. Deterministic — no extra Claude call."""
    text = text.strip()
    if not text:
        return "none specified"
    first_sentence = text.split(". ")[0].split("\n")[0].strip().rstrip(".")
    if len(first_sentence) > max_len:
        return first_sentence[:max_len].rstrip() + "..."
    return first_sentence + "."


def _entity_lookup(source_data: SourceData) -> dict[str, Entity]:
    return {entity.name: entity for entity in source_data.entities}


def _first_seen_days_ago(item: NewsItem, seen_stories: dict, today: date) -> int | None:
    """Deterministic Python lookup, not trusted to LLM date math — mirrors
    the compute_tracking_counts pattern below."""
    entity_or_topic = item.entity or item.topic or ""
    story_hash = state_module.hash_story(entity_or_topic, item.headline)
    entry = seen_stories.get(story_hash)
    if entry is None:
        return None
    return (today - date.fromisoformat(entry["first_seen"])).days


def _enrich_news_item(
    item: NewsItem, entity_lookup: dict[str, Entity], seen_stories: dict, today: date
) -> dict:
    entity = entity_lookup.get(item.entity) if item.entity else None
    return {
        "headline": item.headline,
        "url": item.url,
        "source": item.source,
        "published": item.published,
        "summary": item.summary,
        "why_it_matters": item.why_it_matters,
        "relevance": item.relevance,
        "entity": item.entity,
        "entity_type": entity.type if entity else None,
        "priority": entity.priority if entity else None,
        "topic": item.topic,
        "first_seen_days_ago": _first_seen_days_ago(item, seen_stories, today),
    }


def compute_tracking_counts(source_data: SourceData) -> dict[str, int]:
    """Computed deterministically from SourceData rather than trusted to the
    LLM's arithmetic — the model sees these as input context (via
    build_payload), but the final Digest always uses this computed value in
    place of whatever (if anything) the model echoes back."""
    competitors = sum(
        1 for e in source_data.entities if e.source == "watchlist" and e.type == "Competitor"
    )
    partner_prospects = sum(
        1
        for e in source_data.entities
        if e.source == "watchlist" and e.type == "Rewards partner prospect"
    )
    gtm_prospects = sum(
        1
        for e in source_data.entities
        if e.source == "watchlist" and e.type == "GTM partner prospect"
    )
    return {
        "competitors": competitors,
        "partner_prospects": partner_prospects,
        "gtm_prospects": gtm_prospects,
    }


def build_payload(
    monitoring_items: list[NewsItem],
    industry_items: list[NewsItem],
    candidates: list[Candidate],
    source_data: SourceData,
    seen_stories: dict | None = None,
    today: date | None = None,
) -> dict:
    entity_lookup = _entity_lookup(source_data)
    seen_stories = seen_stories or {}
    today = today or date.today()
    return {
        "monitoring_items": [
            _enrich_news_item(i, entity_lookup, seen_stories, today) for i in monitoring_items
        ],
        "industry_items": [
            _enrich_news_item(i, entity_lookup, seen_stories, today) for i in industry_items
        ],
        "candidates": [asdict(c) for c in candidates],
        "reward_landscape": source_data.reward_landscape,
        "gtm_landscape": source_data.gtm_landscape,
        "tracking_counts": compute_tracking_counts(source_data),
    }


def _to_digest_item(raw: dict) -> DigestItem:
    return DigestItem(
        headline=raw.get("headline", ""),
        url=raw.get("url", ""),
        source=raw.get("source", ""),
        summary=raw.get("summary", ""),
    )


def _to_candidate(raw: dict) -> Candidate:
    return Candidate(
        name=raw.get("name", ""),
        suggested_type=raw.get("suggested_type", ""),
        region=raw.get("region", ""),
        why_fits=raw.get("why_fits", ""),
        source_url=raw.get("source_url", ""),
        category=raw.get("category"),
        confidence=raw.get("confidence"),
    )


def _call_and_parse(client, config: Config, prompt: str) -> dict:
    response = client.messages.create(
        model=config.anthropic_model,
        max_tokens=SYNTHESIZE_MAX_TOKENS,
        messages=[{"role": "user", "content": prompt}],
    )
    if response.stop_reason != "end_turn":
        logger.warning(
            "synthesize call ended with stop_reason=%r (not end_turn) — response may be "
            "truncated mid-JSON",
            response.stop_reason,
        )
    text = extract_text(response)
    return parse_json_response(text)


def synthesize(
    client,
    monitoring_items: list[NewsItem],
    industry_items: list[NewsItem],
    candidates: list[Candidate],
    source_data: SourceData,
    config: Config,
    state: dict | None = None,
) -> Digest:
    """§7 Stage 5 — one Claude call, no tools. Unlike gather.py's per-call
    resilience, a synthesis failure is fatal to the run: it propagates rather
    than being caught, since posting a broken/empty digest is worse than not
    posting at all (main.py, Phase 7, is where this becomes a non-zero exit).

    One retry on a JSON parse failure: observed intermittently on large,
    content-rich payloads (a rare escaping slip in the model's output, not
    truncation) — a second attempt at the identical prompt is cheap relative
    to throwing away a whole day's real content. A second consecutive parse
    failure still propagates.

    `state` (v2 Phase 16) carries `seen_stories` for cross-day repeat-story
    suppression — optional so callers that don't have persisted state yet
    (tests, diagnose_synthesis.py) can omit it."""
    seen_stories = (state or {}).get("seen_stories", {})
    payload = build_payload(monitoring_items, industry_items, candidates, source_data, seen_stories)
    prompt = _render_prompt(
        "synthesize.txt",
        nomo_context=source_data.criteria.nomo_context,
        region_weighting=source_data.criteria.region_weighting,
        competitor_criteria_summary=_one_line_summary(source_data.criteria.competitor_criteria),
        reward_partner_criteria_summary=_one_line_summary(
            source_data.criteria.reward_partner_criteria
        ),
        gtm_partner_criteria_summary=_one_line_summary(source_data.criteria.gtm_partner_criteria),
        reward_landscape=(
            "; ".join(source_data.reward_landscape) if source_data.reward_landscape else "none yet"
        ),
        gtm_landscape=(
            "; ".join(source_data.gtm_landscape) if source_data.gtm_landscape else "none yet"
        ),
        schema=DIGEST_SCHEMA,
        verbose_instructions=VERBOSE_INSTRUCTIONS_BLOCK if config.synthesis_verbose_log else "",
        payload=json.dumps(payload, indent=2),
    )

    try:
        result = _call_and_parse(client, config, prompt)
    except json.JSONDecodeError:
        logger.warning("synthesize: JSON parse failed, retrying once with the same prompt")
        result = _call_and_parse(client, config, prompt)

    if config.synthesis_verbose_log:
        for entry in result.get("rejected_candidates", []):
            logger.info(
                "verbose: candidate rejected name=%r reason=%r",
                entry.get("name"),
                entry.get("reason"),
            )

    sections = result.get("sections", {})
    return Digest(
        quiet_day=bool(result.get("quiet_day", False)),
        competition=[_to_digest_item(d) for d in sections.get("competition", [])],
        industry=[_to_digest_item(d) for d in sections.get("industry", [])],
        partner_prospects=[_to_digest_item(d) for d in sections.get("partner_prospects", [])],
        gtm_prospects=[_to_digest_item(d) for d in sections.get("gtm_prospects", [])],
        new_candidates=[_to_candidate(d) for d in sections.get("new_candidates", [])],
        tracking_counts=compute_tracking_counts(source_data),
    )
