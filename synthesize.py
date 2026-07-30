"""Synthesis Claude call -> digest JSON. See SPEC.md §7 Stage 5, §8.3."""

from __future__ import annotations

import json
import logging
from dataclasses import asdict
from pathlib import Path
from string import Template

from config import Config
from gather import extract_text, parse_json_response
from models import Candidate, Digest, DigestItem, Entity, NewsItem, SourceData

logger = logging.getLogger(__name__)

PROMPTS_DIR = Path(__file__).parent / "prompts"
SYNTHESIZE_MAX_TOKENS = 8192

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
            "new_candidates": [
                {
                    "name": "string",
                    "suggested_type": "Competitor|Partner prospect",
                    "region": "string",
                    "why_fits": "string",
                    "source_url": "string",
                }
            ],
        },
        "tracking_counts": {"competitors": 0, "partner_prospects": 0},
    }
)


def _render_prompt(filename: str, **kwargs) -> str:
    template_text = (PROMPTS_DIR / filename).read_text()
    return Template(template_text).substitute(**kwargs)


def _entity_lookup(source_data: SourceData) -> dict[str, Entity]:
    return {entity.name: entity for entity in source_data.entities}


def _enrich_news_item(item: NewsItem, entity_lookup: dict[str, Entity]) -> dict:
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
        1 for e in source_data.entities if e.source == "watchlist" and e.type == "Partner prospect"
    )
    return {"competitors": competitors, "partner_prospects": partner_prospects}


def build_payload(
    monitoring_items: list[NewsItem],
    industry_items: list[NewsItem],
    candidates: list[Candidate],
    source_data: SourceData,
) -> dict:
    entity_lookup = _entity_lookup(source_data)
    return {
        "monitoring_items": [_enrich_news_item(i, entity_lookup) for i in monitoring_items],
        "industry_items": [_enrich_news_item(i, entity_lookup) for i in industry_items],
        "candidates": [asdict(c) for c in candidates],
        "reward_landscape": source_data.reward_landscape,
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


def synthesize(
    client,
    monitoring_items: list[NewsItem],
    industry_items: list[NewsItem],
    candidates: list[Candidate],
    source_data: SourceData,
    config: Config,
) -> Digest:
    """§7 Stage 5 — one Claude call, no tools. Unlike gather.py's per-call
    resilience, a synthesis failure is fatal to the run: it propagates rather
    than being caught, since posting a broken/empty digest is worse than not
    posting at all (main.py, Phase 7, is where this becomes a non-zero exit)."""
    payload = build_payload(monitoring_items, industry_items, candidates, source_data)
    prompt = _render_prompt(
        "synthesize.txt",
        nomo_context=source_data.criteria.nomo_context,
        region_weighting=source_data.criteria.region_weighting,
        reward_landscape=(
            "; ".join(source_data.reward_landscape) if source_data.reward_landscape else "none yet"
        ),
        schema=DIGEST_SCHEMA,
        payload=json.dumps(payload, indent=2),
    )

    response = client.messages.create(
        model=config.anthropic_model,
        max_tokens=SYNTHESIZE_MAX_TOKENS,
        messages=[{"role": "user", "content": prompt}],
    )
    text = extract_text(response)
    result = parse_json_response(text)

    sections = result.get("sections", {})
    return Digest(
        quiet_day=bool(result.get("quiet_day", False)),
        competition=[_to_digest_item(d) for d in sections.get("competition", [])],
        industry=[_to_digest_item(d) for d in sections.get("industry", [])],
        partner_prospects=[_to_digest_item(d) for d in sections.get("partner_prospects", [])],
        new_candidates=[_to_candidate(d) for d in sections.get("new_candidates", [])],
        tracking_counts=compute_tracking_counts(source_data),
    )
