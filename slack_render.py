"""Digest JSON -> Slack Block Kit -> post. See SPEC.md §7 Stage 6, §9."""

from __future__ import annotations

import logging
from datetime import date as date_cls

import httpx

from config import Config
from models import Candidate, Digest, DigestItem

logger = logging.getLogger(__name__)

# Rendering order matches §8.3's JSON contract ordering.
SECTION_TITLES = {
    "competition": "Competition",
    "industry": "Industry",
    "partner_prospects": "Partner prospects",
}


class SlackDeliveryError(RuntimeError):
    """Raised when the Slack webhook POST fails. main.py (Phase 7) is where
    this becomes the program's non-zero exit (§7 Stage 6: "exit non-zero
    only on delivery failure")."""


def _truncate(items: list, max_items: int) -> tuple[list, int]:
    if len(items) <= max_items:
        return items, 0
    return items[:max_items], len(items) - max_items


def _render_item_line(item: DigestItem) -> str:
    return f"• *<{item.url}|{item.headline}>* — {item.summary} _({item.source})_"


def _render_candidate_line(candidate: Candidate) -> str:
    return (
        f"• *{candidate.name}* — {candidate.why_fits} · "
        f"_proposed {candidate.suggested_type}_ · <{candidate.source_url}|source>"
    )


def _section_block(title: str, lines: list[str], more_count: int) -> dict:
    text = f"*{title}*\n" + "\n".join(lines)
    if more_count:
        text += f"\n_+{more_count} more_"
    return {"type": "section", "text": {"type": "mrkdwn", "text": text}}


def _header_block(today: date_cls) -> dict:
    return {
        "type": "header",
        "text": {"type": "plain_text", "text": f"\U0001f4f1 NOMO Doomscroller — {today.isoformat()}"},
    }


def _footer_block(config: Config, digest: Digest) -> dict:
    counts = digest.tracking_counts
    text = (
        f"Tracking {counts.get('competitors', 0)} competitors · "
        f"{counts.get('partner_prospects', 0)} partner prospects · "
        f"<{config.manage_list_url}|manage the list>"
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
    }
    for key, title in SECTION_TITLES.items():
        items = section_items[key]
        if not items:
            continue
        shown, more = _truncate(items, config.max_items_per_section)
        blocks.append(_section_block(title, [_render_item_line(i) for i in shown], more))

    if digest.new_candidates:
        shown, more = _truncate(digest.new_candidates, config.max_items_per_section)
        blocks.append({"type": "divider"})
        blocks.append(
            _section_block("New candidates", [_render_candidate_line(c) for c in shown], more)
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
