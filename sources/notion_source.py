"""Notion backend (built, dormant until DATA_SOURCE=notion). See SPEC.md §6.1-6.4.

Live workspace access isn't available yet (§4 Path B needs a workspace owner to
create the integration) — verified against mocked fixtures matching the Notion
API's response shape until real access opens up (§16 Phase 2).
"""

from __future__ import annotations

import logging
from pprint import pprint

from notion_client import Client

from models import Entity, IndustryTopic, SourceData
from sources.base import parse_criteria

logger = logging.getLogger(__name__)

# §6.2 — heading text the team is expected to author on the criteria page.
CRITERIA_HEADING_BLOCK_TYPES = {"heading_1", "heading_2", "heading_3"}

# Watchlist DB property names (§6.1's Notion property table).
WATCHLIST_PROPERTIES = {
    "name": "Name",
    "type": "Type",
    "status": "Status",
    "category": "Category",
    "region": "Region",
    "aliases": "Aliases / keywords",
    "source_url": "Source URL",
    "why_tracked": "Why tracked",
    "priority": "Priority",
    "added_by": "Added by",
    "date_added": "Date added",
}

# Industry Topics DB property names (§6.3's Notion mapping).
TOPICS_PROPERTIES = {
    "topic": "Topic",
    "notes": "Notes",
}

# Partners DB property names (§6.4). Region representation on the real Partners
# DB is unconfirmed (multi-select, matching Watchlist's convention, is assumed
# here) — re-verify the moment live access opens up.
PARTNERS_PROPERTIES = {
    "entity": "Entity",
    "status": "Status",
    "region": "Region",
    "sentence": "en | sentence",
    "title": "en | title",
}


class NotionSourceError(RuntimeError):
    """Raised when a required Notion object is unreachable (§7 Stage 1)."""


def _plain_text(rich_text: list[dict]) -> str:
    return "".join(part.get("plain_text", "") for part in rich_text)


def _get_title(properties: dict, name: str) -> str:
    prop = properties.get(name, {})
    return _plain_text(prop.get("title", []))


def _get_rich_text(properties: dict, name: str) -> str:
    prop = properties.get(name, {})
    return _plain_text(prop.get("rich_text", []))


def _get_select(properties: dict, name: str) -> str | None:
    prop = properties.get(name, {})
    select = prop.get("select")
    return select.get("name") if select else None


def _get_multi_select(properties: dict, name: str) -> list[str]:
    prop = properties.get(name, {})
    return [option.get("name") for option in prop.get("multi_select", [])]


def _get_url(properties: dict, name: str) -> str | None:
    prop = properties.get(name, {})
    return prop.get("url")


def _get_created_time(properties: dict, name: str) -> str | None:
    prop = properties.get(name, {})
    return prop.get("created_time")


def _block_plain_text(block: dict) -> str:
    block_type = block.get("type", "")
    payload = block.get(block_type, {})
    return _plain_text(payload.get("rich_text", []))


class NotionSource:
    def __init__(
        self,
        api_key: str,
        watchlist_db_id: str,
        criteria_page_id: str,
        topics_db_id: str,
        partners_db_id: str,
    ):
        self._client = Client(auth=api_key)
        self._watchlist_db_id = watchlist_db_id
        self._criteria_page_id = criteria_page_id
        self._topics_db_id = topics_db_id
        self._partners_db_id = partners_db_id

    def load_all(self) -> SourceData:
        watchlist_entities = self._load_watchlist()
        criteria = self._load_criteria()
        industry_topics = self._load_industry_topics()
        partner_entities, reward_landscape, active_partner_names = self._load_partners()

        excluded_watchlist = [
            entity
            for entity in watchlist_entities
            if entity.type == "Excluded" or entity.status in {"Paused", "Converted"}
        ]
        excluded_names = {
            name for entity in excluded_watchlist for name in (entity.name, *entity.aliases)
        }
        excluded_names |= active_partner_names

        tracked_watchlist_entities = [
            entity
            for entity in watchlist_entities
            if entity.type in {"Competitor", "Partner prospect"} and entity.status == "Active"
        ]

        return SourceData(
            entities=[*tracked_watchlist_entities, *partner_entities],
            excluded_names=excluded_names,
            reward_landscape=reward_landscape,
            industry_topics=industry_topics,
            criteria=criteria,
        )

    def _query_all(self, database_id: str, source_name: str) -> list[dict]:
        results: list[dict] = []
        cursor = None
        try:
            while True:
                response = self._client.databases.query(
                    database_id=database_id, start_cursor=cursor
                )
                results.extend(response["results"])
                if not response.get("has_more"):
                    break
                cursor = response.get("next_cursor")
        except Exception as exc:
            raise NotionSourceError(f"could not query {source_name} (db {database_id}): {exc}") from exc
        return results

    def _load_watchlist(self) -> list[Entity]:
        pages = self._query_all(self._watchlist_db_id, "Watchlist database")

        entities = []
        for page in pages:
            properties = page.get("properties", {})
            name = _get_title(properties, WATCHLIST_PROPERTIES["name"]).strip()
            if not name:
                continue

            aliases_raw = _get_rich_text(properties, WATCHLIST_PROPERTIES["aliases"])
            aliases = [a.strip() for a in aliases_raw.split(",") if a.strip()]

            entities.append(
                Entity(
                    name=name,
                    type=_get_select(properties, WATCHLIST_PROPERTIES["type"]) or "",
                    status=_get_select(properties, WATCHLIST_PROPERTIES["status"]) or "",
                    category=_get_multi_select(properties, WATCHLIST_PROPERTIES["category"]),
                    region=_get_multi_select(properties, WATCHLIST_PROPERTIES["region"]),
                    aliases=aliases,
                    source_url=_get_url(properties, WATCHLIST_PROPERTIES["source_url"]),
                    why_tracked=_get_rich_text(properties, WATCHLIST_PROPERTIES["why_tracked"]),
                    priority=_get_select(properties, WATCHLIST_PROPERTIES["priority"]),
                    added_by=_get_rich_text(properties, WATCHLIST_PROPERTIES["added_by"]) or None,
                    date_added=_get_created_time(properties, WATCHLIST_PROPERTIES["date_added"]),
                    source="watchlist",
                )
            )
        return entities

    def _load_criteria(self):
        blocks: list[dict] = []
        cursor = None
        try:
            while True:
                response = self._client.blocks.children.list(
                    block_id=self._criteria_page_id, start_cursor=cursor
                )
                blocks.extend(response["results"])
                if not response.get("has_more"):
                    break
                cursor = response.get("next_cursor")
        except Exception as exc:
            raise NotionSourceError(
                f"could not read criteria page (page {self._criteria_page_id}): {exc}"
            ) from exc

        sections: dict[str, str] = {}
        current_header: str | None = None
        current_lines: list[str] = []

        def flush():
            if current_header is not None:
                sections[current_header] = "\n".join(current_lines).strip()

        for block in blocks:
            if block.get("type") in CRITERIA_HEADING_BLOCK_TYPES:
                flush()
                current_header = _block_plain_text(block).strip()
                current_lines = []
            elif current_header is not None:
                text = _block_plain_text(block).strip()
                if text:
                    current_lines.append(text)
        flush()

        return parse_criteria(sections)

    def _load_industry_topics(self) -> list[IndustryTopic]:
        pages = self._query_all(self._topics_db_id, "Industry Topics database")

        topics = []
        for page in pages:
            properties = page.get("properties", {})
            topic = _get_title(properties, TOPICS_PROPERTIES["topic"]).strip()
            if not topic:
                continue
            notes = _get_rich_text(properties, TOPICS_PROPERTIES["notes"]).strip()
            topics.append(IndustryTopic(topic=topic, notes=notes))
        return topics

    def _load_partners(self) -> tuple[list[Entity], list[str], set[str]]:
        pages = self._query_all(self._partners_db_id, "Partners database")

        entities: list[Entity] = []
        reward_landscape: list[str] = []
        active_names: set[str] = set()

        for page in pages:
            properties = page.get("properties", {})
            name = _get_title(properties, PARTNERS_PROPERTIES["entity"]).strip()
            if not name:
                continue

            status = _get_select(properties, PARTNERS_PROPERTIES["status"]) or "Active"
            if status != "Active":
                continue

            sentence = _get_rich_text(properties, PARTNERS_PROPERTIES["sentence"]).strip()
            title = _get_rich_text(properties, PARTNERS_PROPERTIES["title"]).strip()
            description = sentence or title

            active_names.add(name)
            if description:
                reward_landscape.append(f"{name}: {description}")

            entities.append(
                Entity(
                    name=name,
                    type="Existing partner",
                    status=status,
                    region=_get_multi_select(properties, PARTNERS_PROPERTIES["region"]),
                    why_tracked=(
                        f"Active reward partner — {description}"
                        if description
                        else "Active reward partner."
                    ),
                    source="partners_db",
                )
            )

        return entities, reward_landscape, active_names


if __name__ == "__main__":
    import config

    logging.basicConfig(level=logging.INFO)
    source = NotionSource(
        api_key=config.config.notion_api_key,
        watchlist_db_id=config.config.notion_watchlist_db_id,
        criteria_page_id=config.config.notion_criteria_page_id,
        topics_db_id=config.config.notion_topics_db_id,
        partners_db_id=config.config.notion_partners_db_id,
    )
    pprint(source.load_all())
