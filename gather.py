"""Monitoring + industry trends + scouting Claude calls (web search). See SPEC.md §7 Stage 2-3, §8."""

from __future__ import annotations

import json
import logging
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from string import Template

from config import Config
from models import Candidate, Criteria, Entity, IndustryTopic, NewsItem, SourceData

logger = logging.getLogger(__name__)

PROMPTS_DIR = Path(__file__).parent / "prompts"

WEB_SEARCH_TOOL_TYPE = "web_search_20250305"
# Generous headroom for adaptive thinking 
GATHER_MAX_TOKENS = 8192

# Bounded concurrency for per-entity monitoring calls (§7 Stage 2a)
ENTITY_CONCURRENCY = 5

# Industry trends calls: one topic at a time, moderately broad (§8)
INDUSTRY_MAX_USES = 5


DISCOVERY_ANGLES = [
    "New entrants or competitors emerging in NOMO's category (screen-time "
    "rewards, teen loyalty/engagement apps).",
    "Recent rewards or loyalty funding rounds, launches, or partnership "
    "announcements in the broader space.",
    "Potential reward partners with tradeable inventory (tickets, travel, "
    "credits, experiences) in categories NOMO doesn't already cover well — "
    "reason against the current reward lineup to find genuine gaps, not just "
    "any company with reward inventory.",
]

MONITOR_SCHEMA = json.dumps(
    {
        "entity": "string",
        "items": [
            {
                "headline": "string",
                "url": "string",
                "source": "string",
                "published": "YYYY-MM-DD",
                "summary": "string",
                "why_it_matters": "string",
                "relevance": "high|medium|low",
            }
        ],
    }
)

INDUSTRY_SCHEMA = json.dumps(
    {
        "topic": "string",
        "items": [
            {
                "headline": "string",
                "url": "string",
                "source": "string",
                "published": "YYYY-MM-DD",
                "summary": "string",
                "why_it_matters": "string",
                "relevance": "high|medium|low",
            }
        ],
    }
)

SCOUT_SCHEMA = json.dumps(
    {
        "candidates": [
            {
                "name": "string",
                "suggested_type": "Competitor|Partner prospect",
                "category": "string",
                "region": "US|UK|BR|AU|Other|All",
                "why_fits": "string",
                "source_url": "string",
                "confidence": "high|medium|low",
            }
        ]
    }
)


_FENCE_RE = re.compile(r"```(?:json)?\s*\n?(.*?)\n?```", re.DOTALL)


def _render_prompt(filename: str, **kwargs) -> str:
    template_text = (PROMPTS_DIR / filename).read_text()
    return Template(template_text).substitute(**kwargs)


def _extract_text(response) -> str:
    return "".join(
        block.text for block in response.content if getattr(block, "type", None) == "text"
    )


def _strip_fences(text: str) -> str:
    match = _FENCE_RE.search(text.strip())
    return match.group(1).strip() if match else text.strip()


def _parse_json_response(text: str) -> dict:
    candidate = _strip_fences(text)
    try:
        return json.loads(candidate)
    except json.JSONDecodeError:
        # Last resort for stray leading/trailing prose with no fence at all:
        # slice from the first '{' to the last '}' and try again.
        start, end = candidate.find("{"), candidate.rfind("}")
        if start != -1 and end != -1 and end > start:
            return json.loads(candidate[start : end + 1])
        raise


def _valid_url(url) -> bool:
    return isinstance(url, str) and url.strip().lower().startswith(("http://", "https://"))


def _call_claude_with_search(client, config: Config, prompt: str, max_uses: int) -> str:
    response = client.messages.create(
        model=config.anthropic_model,
        max_tokens=GATHER_MAX_TOKENS,
        messages=[{"role": "user", "content": prompt}],
        tools=[{"type": WEB_SEARCH_TOOL_TYPE, "name": "web_search", "max_uses": max_uses}],
    )
    if response.stop_reason != "end_turn":
        logger.warning(
            "gather call ended with stop_reason=%r (not end_turn) — response may be "
            "truncated or an incomplete paused search turn",
            response.stop_reason,
        )
    return _extract_text(response)


def _entities_to_monitor(source_data: SourceData, config: Config) -> list[Entity]:
    """§7 Stage 2a: Watchlist entities always; Partners-source entities only
    when MONITOR_EXISTING_PARTNERS is on. SourceData.entities carries both,
    tagged by `source` — this is where that gate is applied."""
    entities = [e for e in source_data.entities if e.source == "watchlist"]
    if config.monitor_existing_partners:
        entities += [e for e in source_data.entities if e.source == "partners_db"]
    return entities


def monitor_entity(client, entity: Entity, criteria: Criteria, config: Config) -> list[NewsItem]:
    """One monitoring call for a single entity — §7 Stage 2a, §8.1."""
    prompt = _render_prompt(
        "monitor.txt",
        nomo_context=criteria.nomo_context,
        entity_name=entity.name,
        aliases=", ".join(entity.aliases) if entity.aliases else "none",
        type=entity.type,
        regions=", ".join(entity.region) if entity.region else "unspecified",
        why_tracked=entity.why_tracked or "not specified",
        schema=MONITOR_SCHEMA,
    )
    try:
        text = _call_claude_with_search(client, config, prompt, config.monitor_max_uses)
        payload = _parse_json_response(text)
    except Exception:
        logger.exception("monitor call failed for entity %r", entity.name)
        return []

    items = []
    for raw in payload.get("items", []):
        url = raw.get("url")
        if not _valid_url(url):
            logger.warning(
                "monitor(%s): dropping item with missing/invalid url: %r",
                entity.name,
                raw.get("headline"),
            )
            continue
        items.append(
            NewsItem(
                headline=raw.get("headline", ""),
                url=url,
                source=raw.get("source", ""),
                published=raw.get("published", ""),
                summary=raw.get("summary", ""),
                why_it_matters=raw.get("why_it_matters", ""),
                relevance=raw.get("relevance", ""),
                entity=entity.name,
            )
        )
    return items


def monitor_all_entities(
    client, entities: list[Entity], criteria: Criteria, config: Config
) -> list[NewsItem]:
    """Runs monitor_entity concurrently, bounded (§7 Stage 2a). One entity's
    failure never aborts the batch — monitor_entity already catches its own
    exceptions and returns []; the outer try/except here is a safety net
    against unexpected executor-level errors."""
    items: list[NewsItem] = []
    with ThreadPoolExecutor(max_workers=ENTITY_CONCURRENCY) as executor:
        futures = {
            executor.submit(monitor_entity, client, entity, criteria, config): entity
            for entity in entities
        }
        for future in as_completed(futures):
            entity = futures[future]
            try:
                items.extend(future.result())
            except Exception:
                logger.exception("unexpected error monitoring entity %r", entity.name)

    logger.info("monitoring: scanned %d entities, found %d items", len(entities), len(items))
    return items


def monitor_industry_topic(
    client, topic: IndustryTopic, criteria: Criteria, config: Config
) -> list[NewsItem]:
    """One industry trends call for a single topic — §7 Stage 2b, §8.1b."""
    prompt = _render_prompt(
        "industry.txt",
        nomo_context=criteria.nomo_context,
        region_weighting=criteria.region_weighting,
        window_hours=config.news_window_hours,
        topic=topic.topic,
        notes=topic.notes or "none",
        schema=INDUSTRY_SCHEMA,
    )
    try:
        text = _call_claude_with_search(client, config, prompt, INDUSTRY_MAX_USES)
        payload = _parse_json_response(text)
    except Exception:
        logger.exception("industry call failed for topic %r", topic.topic)
        return []

    items = []
    for raw in payload.get("items", []):
        url = raw.get("url")
        if not _valid_url(url):
            logger.warning(
                "industry(%s): dropping item with missing/invalid url: %r",
                topic.topic,
                raw.get("headline"),
            )
            continue
        items.append(
            NewsItem(
                headline=raw.get("headline", ""),
                url=url,
                source=raw.get("source", ""),
                published=raw.get("published", ""),
                summary=raw.get("summary", ""),
                why_it_matters=raw.get("why_it_matters", ""),
                relevance=raw.get("relevance", ""),
                topic=topic.topic,
            )
        )
    return items


def monitor_all_industry_topics(
    client, topics: list[IndustryTopic], criteria: Criteria, config: Config
) -> list[NewsItem]:
    items: list[NewsItem] = []
    for topic in topics:
        try:
            items.extend(monitor_industry_topic(client, topic, criteria, config))
        except Exception:
            logger.exception("unexpected error monitoring industry topic %r", topic.topic)

    logger.info("industry trends: scanned %d topics, found %d items", len(topics), len(items))
    return items


def scout_angle(
    client,
    angle: str,
    criteria: Criteria,
    tracked_names: set[str],
    reward_landscape: list[str],
    config: Config,
) -> list[Candidate]:
    """One discovery call for a single angle — §7 Stage 3, §8.2."""
    combined_criteria = (
        f"Competitor criteria: {criteria.competitor_criteria}\n"
        f"Partner criteria: {criteria.partner_criteria}"
    )
    prompt = _render_prompt(
        "scout.txt",
        nomo_context=criteria.nomo_context,
        criteria=combined_criteria,
        region_weighting=criteria.region_weighting,
        angle=angle,
        reward_landscape="; ".join(reward_landscape) if reward_landscape else "none yet",
        tracked_names=", ".join(sorted(tracked_names)) if tracked_names else "none",
        do_not_suggest=", ".join(criteria.do_not_suggest) if criteria.do_not_suggest else "none",
        schema=SCOUT_SCHEMA,
    )
    try:
        text = _call_claude_with_search(client, config, prompt, config.scout_max_uses)
        payload = _parse_json_response(text)
    except Exception:
        logger.exception("scout call failed for angle %r", angle)
        return []

    candidates = []
    for raw in payload.get("candidates", []):
        url = raw.get("source_url")
        if not _valid_url(url):
            logger.warning(
                "scout(%s): dropping candidate with missing/invalid source_url: %r",
                angle,
                raw.get("name"),
            )
            continue
        candidates.append(
            Candidate(
                name=raw.get("name", ""),
                suggested_type=raw.get("suggested_type", ""),
                region=raw.get("region", ""),
                why_fits=raw.get("why_fits", ""),
                source_url=url,
                category=raw.get("category"),
                confidence=raw.get("confidence"),
            )
        )
    return candidates


def scout_all(client, source_data: SourceData, config: Config) -> list[Candidate]:
    tracked_names = {
        name for entity in source_data.entities for name in (entity.name, *entity.aliases)
    }
    tracked_names |= source_data.excluded_names

    candidates: list[Candidate] = []
    for angle in DISCOVERY_ANGLES:
        try:
            candidates.extend(
                scout_angle(
                    client, angle, source_data.criteria, tracked_names,
                    source_data.reward_landscape, config,
                )
            )
        except Exception:
            logger.exception("unexpected error on scouting angle %r", angle)

    logger.info(
        "scouting: %d angles run, %d candidates surfaced", len(DISCOVERY_ANGLES), len(candidates)
    )
    return candidates


def run_gather(
    client, source_data: SourceData, config: Config
) -> tuple[list[NewsItem], list[NewsItem], list[Candidate]]:
    """Runs Stage 2a, 2b, and Stage 3. Returns (monitoring_items, industry_items, candidates)."""
    monitoring_items = monitor_all_entities(
        client, _entities_to_monitor(source_data, config), source_data.criteria, config
    )
    industry_items = monitor_all_industry_topics(
        client, source_data.industry_topics, source_data.criteria, config
    )
    candidates = scout_all(client, source_data, config)
    return monitoring_items, industry_items, candidates
