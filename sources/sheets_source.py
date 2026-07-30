"""Google Sheets backend (active in v1). See SPEC.md §6.1-6.4, §7 Stage 1."""

from __future__ import annotations

import json
import logging
from pprint import pprint

import gspread
from google.oauth2.service_account import Credentials

from models import Entity, IndustryTopic, SourceData
from sources.base import check_columns, parse_criteria

logger = logging.getLogger(__name__)

_SCOPES = ["https://www.googleapis.com/auth/spreadsheets.readonly"]
_TRUE_VALUES = {"TRUE", "1", "YES", "X"}

# Column-name maps (§6.0) — logical field name -> actual header string. A
# structure change (renamed header, reordered columns) means editing one of
# these, never the parsing logic below. region_flags maps raw header -> the
# canonical region tag it represents, since the real workbook doesn't use the
# same header spelling across tabs (Watchlist uses "AUS", Partnerships "AU").

WATCHLIST_TAB = "Watchlist"
CRITERIA_TAB = "Criteria"
INDUSTRY_TOPICS_TAB = "Industry Topics"
# §6.4 calls this source "Partners" — the real workbook's tab is titled
# "Partnerships", confirmed against the live workbook (Phase 2 verify).
PARTNERS_TAB = "Partnerships"

WATCHLIST_COLUMNS = {
    "name": "Name",
    "type": "Type",
    "status": "Status",
    "category": "Category",
    "region_flags": {"All": "All", "US": "US", "UK": "UK", "BR": "BR", "AUS": "AU"},
    "aliases": "Aliases / keywords",
    "source_url": "Source URL",
    "why_tracked": "Why tracked",
    "priority": "Priority",
    "added_by": "Added by",
    "date_added": "Date added",
}
WATCHLIST_REQUIRED = {"name", "type", "status"}

CRITERIA_COLUMNS = {
    "section": "Section",
    "content": "Content",
}
CRITERIA_REQUIRED = {"section", "content"}

INDUSTRY_TOPICS_COLUMNS = {
    "topic": "Topic",
    "notes": "Notes",
}
INDUSTRY_TOPICS_REQUIRED = {"topic"}

# Header names confirmed against the live workbook (Phase 2 verify) — plain
# "sentence"/"title", matching §6.0's own illustrative example rather than
# §6.4's "en | sentence"/"en | title" (that's the Notion-side, multi-language
# framing; the Sheets mirror flattens it to English-only column names).
PARTNERS_COLUMNS = {
    "entity": "Partner (entity)",
    "status": "Status",  # optional — §6.4's missing-Status fallback
    "sentence": "sentence",
    "title": "title",
    "region_flags": {"All": "All", "US": "US", "UK": "UK", "BR": "BR", "AU": "AU"},
}
PARTNERS_REQUIRED = {"entity"}


class SheetsSourceError(RuntimeError):
    """Raised when the workbook or one of its required tabs is unreachable (§7 Stage 1)."""


def _is_true(value) -> bool:
    return str(value).strip().upper() in _TRUE_VALUES


def _parse_region_flags(row: dict, flag_map: dict[str, str]) -> list[str]:
    """Converts the five-boolean-column region convention (§6.1, §6.4) into an
    active-region-tag list, e.g. {"US": True, "BR": True} -> ["US", "BR"].
    flag_map is {raw header: canonical tag} rather than assuming the header
    spelling matches the tag, since it doesn't (Watchlist's "AUS" -> "AU").
    'All' means global regardless of the individual flags, so it's returned as
    its own single-entry list when set."""
    active = [canonical for raw, canonical in flag_map.items() if _is_true(row.get(raw, ""))]
    return ["All"] if "All" in active else active


def _split_comma_list(raw: str) -> list[str]:
    return [item.strip() for item in raw.split(",") if item.strip()]


def _normalize_text(raw: str) -> str:
    """Collapses embedded newlines/extra whitespace into a single line — the
    real Partnerships tab's 'sentence' cells sometimes wrap mid-phrase."""
    return " ".join(raw.split())


class SheetsSource:
    def __init__(self, spreadsheet_id: str, service_account_json: str):
        credentials = Credentials.from_service_account_info(
            json.loads(service_account_json), scopes=_SCOPES
        )
        self._client = gspread.authorize(credentials)
        self._spreadsheet_id = spreadsheet_id

    def load_all(self) -> SourceData:
        try:
            workbook = self._client.open_by_key(self._spreadsheet_id)
        except gspread.exceptions.APIError as exc:
            raise SheetsSourceError(f"could not open Google Sheets workbook: {exc}") from exc

        watchlist_entities = self._load_watchlist(workbook)
        criteria = self._load_criteria(workbook)
        industry_topics = self._load_industry_topics(workbook)
        partner_entities, reward_landscape, active_partner_names = self._load_partners(workbook)

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

    def _get_worksheet(self, workbook, tab_name: str):
        try:
            return workbook.worksheet(tab_name)
        except gspread.exceptions.WorksheetNotFound as exc:
            raise SheetsSourceError(f"required tab {tab_name!r} not found in workbook") from exc

    def _load_watchlist(self, workbook) -> list[Entity]:
        worksheet = self._get_worksheet(workbook, WATCHLIST_TAB)
        headers = set(worksheet.row_values(1))
        check_columns(headers, WATCHLIST_COLUMNS, WATCHLIST_REQUIRED, "Watchlist tab")

        entities = []
        for row in worksheet.get_all_records():
            name = str(row.get(WATCHLIST_COLUMNS["name"], "")).strip()
            if not name:
                continue

            entities.append(
                Entity(
                    name=name,
                    type=str(row.get(WATCHLIST_COLUMNS["type"], "")).strip(),
                    status=str(row.get(WATCHLIST_COLUMNS["status"], "")).strip(),
                    category=_split_comma_list(str(row.get(WATCHLIST_COLUMNS["category"], ""))),
                    region=_parse_region_flags(row, WATCHLIST_COLUMNS["region_flags"]),
                    aliases=_split_comma_list(str(row.get(WATCHLIST_COLUMNS["aliases"], ""))),
                    source_url=str(row.get(WATCHLIST_COLUMNS["source_url"], "")).strip() or None,
                    why_tracked=str(row.get(WATCHLIST_COLUMNS["why_tracked"], "")).strip(),
                    priority=str(row.get(WATCHLIST_COLUMNS["priority"], "")).strip() or None,
                    added_by=str(row.get(WATCHLIST_COLUMNS["added_by"], "")).strip() or None,
                    date_added=str(row.get(WATCHLIST_COLUMNS["date_added"], "")).strip() or None,
                    source="watchlist",
                )
            )
        return entities

    def _load_criteria(self, workbook):
        worksheet = self._get_worksheet(workbook, CRITERIA_TAB)
        headers = set(worksheet.row_values(1))
        check_columns(headers, CRITERIA_COLUMNS, CRITERIA_REQUIRED, "Criteria tab")

        sections: dict[str, str] = {}
        for row in worksheet.get_all_records():
            section = str(row.get(CRITERIA_COLUMNS["section"], "")).strip()
            content = str(row.get(CRITERIA_COLUMNS["content"], "")).strip()
            if section:
                sections[section] = content

        return parse_criteria(sections)

    def _load_industry_topics(self, workbook) -> list[IndustryTopic]:
        worksheet = self._get_worksheet(workbook, INDUSTRY_TOPICS_TAB)
        headers = set(worksheet.row_values(1))
        check_columns(
            headers, INDUSTRY_TOPICS_COLUMNS, INDUSTRY_TOPICS_REQUIRED, "Industry Topics tab"
        )

        topics = []
        for row in worksheet.get_all_records():
            topic = str(row.get(INDUSTRY_TOPICS_COLUMNS["topic"], "")).strip()
            if not topic:
                continue
            notes = str(row.get(INDUSTRY_TOPICS_COLUMNS["notes"], "")).strip()
            topics.append(IndustryTopic(topic=topic, notes=notes))
        return topics

    def _load_partners(self, workbook) -> tuple[list[Entity], list[str], set[str]]:
        worksheet = self._get_worksheet(workbook, PARTNERS_TAB)
        headers = set(worksheet.row_values(1))
        check_columns(headers, PARTNERS_COLUMNS, PARTNERS_REQUIRED, "Partnerships tab")

        status_column_present = PARTNERS_COLUMNS["status"] in headers
        if not status_column_present:
            logger.warning(
                "Partners tab: 'Status' column not found — treating all rows as Active (§6.4)"
            )

        entities: list[Entity] = []
        reward_landscape: list[str] = []
        active_names: set[str] = set()

        for row in worksheet.get_all_records():
            # §6.4 paired-row structure: translation rows have a blank Entity cell.
            name = str(row.get(PARTNERS_COLUMNS["entity"], "")).strip()
            if not name:
                continue

            if status_column_present:
                status = str(row.get(PARTNERS_COLUMNS["status"], "")).strip() or "Active"
            else:
                status = "Active"
            if status != "Active":
                continue

            sentence = _normalize_text(str(row.get(PARTNERS_COLUMNS["sentence"], "")))
            title = _normalize_text(str(row.get(PARTNERS_COLUMNS["title"], "")))
            description = sentence or title

            active_names.add(name)
            if description:
                reward_landscape.append(f"{name}: {description}")

            entities.append(
                Entity(
                    name=name,
                    type="Existing partner",
                    status=status,
                    region=_parse_region_flags(row, PARTNERS_COLUMNS["region_flags"]),
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
    source = SheetsSource(
        spreadsheet_id=config.config.google_sheets_id,
        service_account_json=config.config.google_service_account_json,
    )
    pprint(source.load_all())
