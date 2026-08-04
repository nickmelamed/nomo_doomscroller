"""Digest JSON -> Slack Block Kit -> post. See SPEC.md §7 Stage 6, §9."""

from __future__ import annotations

import logging
from datetime import date as date_cls

import httpx

from config import Config
from models import Candidate, Digest, DigestItem, RejectedCandidate, WeeklyRollup

logger = logging.getLogger(__name__)

# Rendering order matches §8.3's JSON contract ordering.
SECTION_TITLES = {
    "competition": "Competition",
    "industry": "Industry",
    "partner_prospects": "Partner prospects",
    "gtm_prospects": "GTM prospects",
}

# Slack rejects a section block whose text exceeds this (400 invalid_blocks).
# Real content easily gets here with several items; fixture testing with
# short placeholder text didn't surface it.
SLACK_SECTION_TEXT_LIMIT = 3000


class SlackDeliveryError(RuntimeError):
    """Raised when the Slack webhook POST fails. main.py (Phase 7) is where
    this becomes the program's non-zero exit (§7 Stage 6: "exit non-zero
    only on delivery failure")."""


def _escape_mrkdwn(text: str) -> str:
    """Slack's mrkdwn parser treats &, <, > as syntax (links/mentions) — any
    dynamic text embedded in an mrkdwn block must have them escaped first, in
    this order, or Slack rejects the whole payload as invalid_blocks."""
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _truncate(items: list, max_items: int) -> tuple[list, int]:
    if len(items) <= max_items:
        return items, 0
    return items[:max_items], len(items) - max_items


def _render_item_line(item: DigestItem) -> str:
    url = _escape_mrkdwn(item.url)
    headline = _escape_mrkdwn(item.headline)
    summary = _escape_mrkdwn(item.summary)
    source = _escape_mrkdwn(item.source)
    return f"• *<{url}|{headline}>* — {summary} _({source})_"


def _render_candidate_line(candidate: Candidate) -> str:
    name = _escape_mrkdwn(candidate.name)
    why_fits = _escape_mrkdwn(candidate.why_fits)
    suggested_type = _escape_mrkdwn(candidate.suggested_type)
    source_url = _escape_mrkdwn(candidate.source_url)
    return f"• *{name}* — {why_fits} · _proposed {suggested_type}_ · <{source_url}|source>"


def _render_reconsider_line(candidate: RejectedCandidate) -> str:
    name = _escape_mrkdwn(candidate.name)
    why_fits = _escape_mrkdwn(candidate.why_fits)
    reason = _escape_mrkdwn(candidate.reason)
    suggested_type = _escape_mrkdwn(candidate.suggested_type)
    source_url = _escape_mrkdwn(candidate.source_url)
    return (
        f"• *{name}* — {why_fits} · _proposed {suggested_type}_ · "
        f"rejected: {reason} · <{source_url}|source>"
    )


_RECONSIDER_NOTE = (
    "_Previously rejected, resurfaced because this category was empty — "
    "add via the watchlist if you disagree._"
)


def _section_blocks(title: str, lines: list[str], more_count: int) -> list[dict]:
    """Splits into multiple section blocks if the combined text would exceed
    Slack's per-block limit — one block per group of lines that fits, with
    the bold title only on the first."""
    all_lines = list(lines)
    if more_count:
        all_lines.append(f"_+{more_count} more_")

    blocks: list[dict] = []
    current = [f"*{title}*"]
    current_len = len(current[0])

    for line in all_lines:
        candidate_len = current_len + 1 + len(line)
        if candidate_len > SLACK_SECTION_TEXT_LIMIT and len(current) > 1:
            blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": "\n".join(current)}})
            current = [line]
            current_len = len(line)
        else:
            current.append(line)
            current_len = candidate_len

    blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": "\n".join(current)}})
    return blocks


def _header_block(today: date_cls) -> dict:
    return {
        "type": "header",
        "text": {"type": "plain_text", "text": f"NOMO News Updates — {today.isoformat()}"},
    }


def _footer_block(config: Config, digest: Digest) -> dict:
    counts = digest.tracking_counts
    manage_list_url = _escape_mrkdwn(config.manage_list_url or "")
    text = (
        f"Tracking {counts.get('competitors', 0)} competitors · "
        f"{counts.get('partner_prospects', 0)} partner prospects · "
        f"{counts.get('gtm_prospects', 0)} GTM prospects · "
        f"<{manage_list_url}|manage the list>"
    )
    return {"type": "context", "elements": [{"type": "mrkdwn", "text": text}]}


def build_blocks(digest: Digest, config: Config, today: date_cls | None = None) -> list[dict]:
    today = today or date_cls.today()
    blocks: list[dict] = [_header_block(today)]

    if digest.quiet_day:
        blocks.append(
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": "Quiet day — nothing material to report."},
            }
        )
        blocks.append(_footer_block(config, digest))
        return blocks

    section_items = {
        "competition": digest.competition,
        "industry": digest.industry,
        "partner_prospects": digest.partner_prospects,
        "gtm_prospects": digest.gtm_prospects,
    }
    for key, title in SECTION_TITLES.items():
        items = section_items[key]
        if not items:
            continue
        shown, more = _truncate(items, config.max_items_per_section)
        blocks.extend(_section_blocks(title, [_render_item_line(i) for i in shown], more))

    if digest.new_candidates:
        shown, more = _truncate(digest.new_candidates, config.max_items_per_section)
        blocks.append({"type": "divider"})
        blocks.extend(
            _section_blocks("New candidates", [_render_candidate_line(c) for c in shown], more)
        )
        blocks.append(
            {
                "type": "context",
                "elements": [
                    {
                        "type": "mrkdwn",
                        "text": "_Proposed only — add via the watchlist to start tracking._",
                    }
                ],
            }
        )

    if digest.reconsider:
        shown, more = _truncate(digest.reconsider, config.max_items_per_section)
        blocks.append({"type": "divider"})
        blocks.extend(
            _section_blocks(
                "Didn't clear the bar — worth a second look?",
                [_render_reconsider_line(c) for c in shown],
                more,
            )
        )
        blocks.append({"type": "context", "elements": [{"type": "mrkdwn", "text": _RECONSIDER_NOTE}]})

    blocks.append(_footer_block(config, digest))
    return blocks


def post_digest(digest: Digest, config: Config, today: date_cls | None = None) -> None:
    """Renders and POSTs the digest to the Slack incoming webhook (§7 Stage 6)."""
    blocks = build_blocks(digest, config, today)
    fallback_text = blocks[0]["text"]["text"]

    response = httpx.post(
        config.slack_webhook_url, json={"text": fallback_text, "blocks": blocks}, timeout=10.0
    )
    if response.status_code != 200:
        raise SlackDeliveryError(
            f"Slack webhook returned {response.status_code}: {response.text}"
        )
    logger.info("posted digest to Slack (%d blocks)", len(blocks))


def _weekly_header_block(rollup: WeeklyRollup) -> dict:
    return {
        "type": "header",
        "text": {
            "type": "plain_text",
            "text": f"NOMO News Updates — Week of {rollup.week_of} (here's what you missed)",
        },
    }


def _themes_block(rollup: WeeklyRollup) -> dict | None:
    if not rollup.themes:
        return None
    lines = "\n".join(f"• {_escape_mrkdwn(t)}" for t in rollup.themes)
    return {"type": "section", "text": {"type": "mrkdwn", "text": f"*This week's themes*\n{lines}"}}


def _weekly_footer_block(config: Config) -> dict:
    manage_list_url = _escape_mrkdwn(config.manage_list_url or "")
    return {
        "type": "context",
        "elements": [{"type": "mrkdwn", "text": f"<{manage_list_url}|manage the list>"}],
    }


def build_weekly_blocks(
    rollup: WeeklyRollup, config: Config, days_covered: int | None = None
) -> list[dict]:
    """v2 Phase 19 — reuses the same section-rendering helpers as the daily
    digest (_render_item_line, _render_candidate_line, _section_blocks)."""
    blocks: list[dict] = [_weekly_header_block(rollup)]

    if days_covered is not None and days_covered < 5:
        blocks.append(
            {
                "type": "context",
                "elements": [
                    {
                        "type": "mrkdwn",
                        "text": f"_Covering {days_covered} of 5 weekdays archived this period._",
                    }
                ],
            }
        )

    has_content = bool(
        rollup.competition
        or rollup.industry
        or rollup.partner_prospects
        or rollup.gtm_prospects
        or rollup.notable_candidates
        or rollup.rejected_candidates
    )
    if not has_content:
        blocks.append(
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": "Quiet week — nothing material to roll up."},
            }
        )
        blocks.append(_weekly_footer_block(config))
        return blocks

    themes_block = _themes_block(rollup)
    if themes_block:
        blocks.append(themes_block)

    section_items = {
        "competition": rollup.competition,
        "industry": rollup.industry,
        "partner_prospects": rollup.partner_prospects,
        "gtm_prospects": rollup.gtm_prospects,
    }
    for key, title in SECTION_TITLES.items():
        items = section_items[key]
        if not items:
            continue
        shown, more = _truncate(items, config.max_items_per_section)
        blocks.extend(_section_blocks(title, [_render_item_line(i) for i in shown], more))

    if rollup.notable_candidates:
        shown, more = _truncate(rollup.notable_candidates, config.max_items_per_section)
        blocks.append({"type": "divider"})
        blocks.extend(
            _section_blocks(
                "Notable candidates", [_render_candidate_line(c) for c in shown], more
            )
        )
        blocks.append(
            {
                "type": "context",
                "elements": [
                    {
                        "type": "mrkdwn",
                        "text": "_Proposed only — add via the watchlist to start tracking._",
                    }
                ],
            }
        )

    if rollup.rejected_candidates:
        shown, more = _truncate(rollup.rejected_candidates, config.max_items_per_section)
        blocks.append({"type": "divider"})
        blocks.extend(
            _section_blocks(
                "Considered but rejected this week",
                [_render_reconsider_line(c) for c in shown],
                more,
            )
        )
        blocks.append({"type": "context", "elements": [{"type": "mrkdwn", "text": _RECONSIDER_NOTE}]})

    blocks.append(_weekly_footer_block(config))
    return blocks


def post_weekly_rollup(
    rollup: WeeklyRollup, config: Config, days_covered: int | None = None
) -> None:
    """Renders and POSTs the weekly rollup to the same Slack webhook (v2 Phase 19)."""
    blocks = build_weekly_blocks(rollup, config, days_covered)
    fallback_text = blocks[0]["text"]["text"]

    response = httpx.post(
        config.slack_webhook_url, json={"text": fallback_text, "blocks": blocks}, timeout=10.0
    )
    if response.status_code != 200:
        raise SlackDeliveryError(
            f"Slack webhook returned {response.status_code}: {response.text}"
        )
    logger.info("posted weekly rollup to Slack (%d blocks)", len(blocks))
