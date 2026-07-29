"""Environment configuration for the NOMO Doomscroller pipeline. See SPEC.md §11.

Only the env vars for the active DATA_SOURCE backend are required; the inactive
backend's vars are read but never validated, so an incomplete Notion setup never
blocks a Sheets-backed run (and vice versa) — see SPEC.md §11's closing note.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()

REQUIRED_COMMON_VARS = ["ANTHROPIC_API_KEY", "SLACK_WEBHOOK_URL"]

BACKEND_REQUIRED_VARS = {
    "sheets": ["GOOGLE_SHEETS_ID", "GOOGLE_SERVICE_ACCOUNT_JSON", "SHEETS_URL"],
    "notion": [
        "NOTION_API_KEY",
        "NOTION_WATCHLIST_DB_ID",
        "NOTION_CRITERIA_PAGE_ID",
        "NOTION_PARTNERS_DB_ID",
        "NOTION_DB_URL",
    ],
}

_TRUE_VALUES = {"1", "true", "yes", "on"}


class ConfigError(RuntimeError):
    """Raised when required environment variables are missing or malformed."""


@dataclass(frozen=True)
class Config:
    anthropic_api_key: str
    anthropic_model: str
    data_source: str  # "sheets" | "notion"
    slack_webhook_url: str
    news_window_hours: int
    max_items_per_section: int
    monitor_existing_partners: bool
    monitor_max_uses: int
    scout_max_uses: int

    # Sheets backend (§11) — populated if set, required only when data_source == "sheets"
    google_sheets_id: str | None
    google_service_account_json: str | None
    sheets_url: str | None

    # Notion backend (§11) — populated if set, required only when data_source == "notion"
    notion_api_key: str | None
    notion_watchlist_db_id: str | None
    notion_criteria_page_id: str | None
    notion_partners_db_id: str | None
    notion_db_url: str | None

    @property
    def manage_list_url(self) -> str | None:
        """The 'manage the list' footer link (§9) — resolves per active backend."""
        return self.sheets_url if self.data_source == "sheets" else self.notion_db_url


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

    data_source = source.get("DATA_SOURCE", "sheets").strip().lower()
    if data_source not in BACKEND_REQUIRED_VARS:
        errors.append(
            f"DATA_SOURCE must be one of {sorted(BACKEND_REQUIRED_VARS)}, got {data_source!r}"
        )
        backend_required = []
    else:
        backend_required = BACKEND_REQUIRED_VARS[data_source]

    required = REQUIRED_COMMON_VARS + backend_required
    missing = [name for name in required if not source.get(name)]
    if missing:
        errors.append(f"missing required environment variable(s): {', '.join(missing)}")

    def optional_str(name: str) -> str | None:
        value = source.get(name)
        return value if value else None

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
        anthropic_api_key=source.get("ANTHROPIC_API_KEY", ""),
        anthropic_model=anthropic_model,
        data_source=data_source,
        slack_webhook_url=source.get("SLACK_WEBHOOK_URL", ""),
        news_window_hours=news_window_hours,
        max_items_per_section=max_items_per_section,
        monitor_existing_partners=monitor_existing_partners,
        monitor_max_uses=monitor_max_uses,
        scout_max_uses=scout_max_uses,
        google_sheets_id=optional_str("GOOGLE_SHEETS_ID"),
        google_service_account_json=optional_str("GOOGLE_SERVICE_ACCOUNT_JSON"),
        sheets_url=optional_str("SHEETS_URL"),
        notion_api_key=optional_str("NOTION_API_KEY"),
        notion_watchlist_db_id=optional_str("NOTION_WATCHLIST_DB_ID"),
        notion_criteria_page_id=optional_str("NOTION_CRITERIA_PAGE_ID"),
        notion_partners_db_id=optional_str("NOTION_PARTNERS_DB_ID"),
        notion_db_url=optional_str("NOTION_DB_URL"),
    )


config = load_config()
