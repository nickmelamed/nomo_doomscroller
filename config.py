"""Environment configuration for the NOMO Doomscroller pipeline. See SPEC.md §11."""

from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()

REQUIRED_VARS = [
    "ANTHROPIC_API_KEY",
    "NOTION_API_KEY",
    "NOTION_WATCHLIST_DB_ID",
    "NOTION_CRITERIA_PAGE_ID",
    "NOTION_PARTNERS_DB_ID",
    "NOTION_DB_URL",
    "SLACK_WEBHOOK_URL",
]

_TRUE_VALUES = {"1", "true", "yes", "on"}


class ConfigError(RuntimeError):
    """Raised when required environment variables are missing or malformed."""


@dataclass(frozen=True)
class Config:
    anthropic_api_key: str
    notion_api_key: str
    notion_watchlist_db_id: str
    notion_criteria_page_id: str
    notion_partners_db_id: str
    notion_db_url: str
    slack_webhook_url: str

    anthropic_model: str
    news_window_hours: int
    max_items_per_section: int
    monitor_existing_partners: bool
    monitor_max_uses: int
    scout_max_uses: int


def _parse_bool(name: str, raw: str, errors: list[str]) -> bool:
    lowered = raw.strip().lower()
    if lowered in _TRUE_VALUES:
        return True
    if lowered in {"0", "false", "no", "off"}:
        return False
    errors.append(f"{name} must be a boolean (true/false), got {raw!r}")
    return False


def _parse_int(name: str, raw: str, errors: list[str]) -> int:
    try:
        return int(raw)
    except ValueError:
        errors.append(f"{name} must be an integer, got {raw!r}")
        return 0


def load_config(env: dict | None = None) -> Config:
    """Load and validate config from the given mapping (default: os.environ).

    Raises ConfigError naming every missing/malformed variable at once.
    """
    source = os.environ if env is None else env
    errors: list[str] = []

    missing = [name for name in REQUIRED_VARS if not source.get(name)]
    if missing:
        errors.append(f"missing required environment variable(s): {', '.join(missing)}")

    required_values = {name: source.get(name, "") for name in REQUIRED_VARS}

    anthropic_model = source.get("ANTHROPIC_MODEL", "claude-sonnet-5")

    news_window_hours = _parse_int(
        "NEWS_WINDOW_HOURS", source.get("NEWS_WINDOW_HOURS", "24"), errors
    )
    max_items_per_section = _parse_int(
        "MAX_ITEMS_PER_SECTION", source.get("MAX_ITEMS_PER_SECTION", "6"), errors
    )
    monitor_existing_partners = _parse_bool(
        "MONITOR_EXISTING_PARTNERS",
        source.get("MONITOR_EXISTING_PARTNERS", "false"),
        errors,
    )
    monitor_max_uses = _parse_int(
        "MONITOR_MAX_USES", source.get("MONITOR_MAX_USES", "3"), errors
    )
    scout_max_uses = _parse_int(
        "SCOUT_MAX_USES", source.get("SCOUT_MAX_USES", "8"), errors
    )

    if errors:
        raise ConfigError("; ".join(errors))

    return Config(
        anthropic_api_key=required_values["ANTHROPIC_API_KEY"],
        notion_api_key=required_values["NOTION_API_KEY"],
        notion_watchlist_db_id=required_values["NOTION_WATCHLIST_DB_ID"],
        notion_criteria_page_id=required_values["NOTION_CRITERIA_PAGE_ID"],
        notion_partners_db_id=required_values["NOTION_PARTNERS_DB_ID"],
        notion_db_url=required_values["NOTION_DB_URL"],
        slack_webhook_url=required_values["SLACK_WEBHOOK_URL"],
        anthropic_model=anthropic_model,
        news_window_hours=news_window_hours,
        max_items_per_section=max_items_per_section,
        monitor_existing_partners=monitor_existing_partners,
        monitor_max_uses=monitor_max_uses,
        scout_max_uses=scout_max_uses,
    )


config = load_config()
