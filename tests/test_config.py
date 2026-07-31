import pytest

from config import REQUIRED_COMMON_VARS, BACKEND_REQUIRED_VARS, ConfigError, load_config

SHEETS_ENV = {
    **{name: f"dummy-{name.lower()}" for name in REQUIRED_COMMON_VARS},
    **{name: f"dummy-{name.lower()}" for name in BACKEND_REQUIRED_VARS["sheets"]},
}

NOTION_ENV = {
    **{name: f"dummy-{name.lower()}" for name in REQUIRED_COMMON_VARS},
    "DATA_SOURCE": "notion",
    **{name: f"dummy-{name.lower()}" for name in BACKEND_REQUIRED_VARS["notion"]},
}


def test_defaults_to_sheets_backend():
    cfg = load_config(env=SHEETS_ENV)
    assert cfg.data_source == "sheets"
    assert cfg.google_sheets_id == "dummy-google_sheets_id"
    assert cfg.manage_list_url == "dummy-sheets_url"


def test_loads_notion_backend_when_selected():
    cfg = load_config(env=NOTION_ENV)
    assert cfg.data_source == "notion"
    assert cfg.notion_watchlist_db_id == "dummy-notion_watchlist_db_id"
    assert cfg.manage_list_url == "dummy-notion_db_url"


def test_gtm_partners_db_id_optional_even_when_notion_active():
    # §6.4a — NOTION_GTM_PARTNERS_DB_ID isn't in BACKEND_REQUIRED_VARS, so the
    # notion backend must load fine without it.
    cfg = load_config(env=NOTION_ENV)
    assert cfg.notion_gtm_partners_db_id is None


def test_gtm_partners_db_id_loaded_when_present():
    env = {**NOTION_ENV, "NOTION_GTM_PARTNERS_DB_ID": "dummy-gtm-db"}
    cfg = load_config(env=env)
    assert cfg.notion_gtm_partners_db_id == "dummy-gtm-db"


def test_documented_defaults():
    cfg = load_config(env=SHEETS_ENV)
    assert cfg.anthropic_model == "claude-sonnet-5"
    assert cfg.news_window_hours == 24
    assert cfg.max_items_per_section == 6
    assert cfg.monitor_existing_partners is False
    assert cfg.monitor_max_uses == 3
    assert cfg.scout_max_uses == 8
    assert cfg.synthesis_verbose_log is True


def test_optional_vars_override_defaults():
    env = {
        **SHEETS_ENV,
        "ANTHROPIC_MODEL": "claude-opus-5",
        "NEWS_WINDOW_HOURS": "48",
        "MAX_ITEMS_PER_SECTION": "10",
        "MONITOR_EXISTING_PARTNERS": "true",
        "MONITOR_MAX_USES": "4",
        "SCOUT_MAX_USES": "12",
        "SYNTHESIS_VERBOSE_LOG": "false",
    }
    cfg = load_config(env=env)

    assert cfg.anthropic_model == "claude-opus-5"
    assert cfg.news_window_hours == 48
    assert cfg.max_items_per_section == 10
    assert cfg.monitor_existing_partners is True
    assert cfg.monitor_max_uses == 4
    assert cfg.scout_max_uses == 12
    assert cfg.synthesis_verbose_log is False


def test_missing_common_required_vars_raises_combined_error():
    with pytest.raises(ConfigError) as exc_info:
        load_config(env={})

    message = str(exc_info.value)
    for name in REQUIRED_COMMON_VARS:
        assert name in message


def test_incomplete_sheets_backend_fails_loudly():
    env = dict(SHEETS_ENV)
    del env["GOOGLE_SERVICE_ACCOUNT_JSON"]

    with pytest.raises(ConfigError) as exc_info:
        load_config(env=env)

    assert "GOOGLE_SERVICE_ACCOUNT_JSON" in str(exc_info.value)


def test_incomplete_notion_backend_fails_loudly():
    env = dict(NOTION_ENV)
    del env["NOTION_CRITERIA_PAGE_ID"]

    with pytest.raises(ConfigError) as exc_info:
        load_config(env=env)

    assert "NOTION_CRITERIA_PAGE_ID" in str(exc_info.value)


def test_incomplete_inactive_backend_does_not_block_active_one():
    # DATA_SOURCE=sheets with only a partial, incomplete Notion setup present —
    # per §11's closing note, the inactive backend must never block a run.
    env = {**SHEETS_ENV, "NOTION_API_KEY": "dummy-notion-key"}
    cfg = load_config(env=env)

    assert cfg.data_source == "sheets"
    assert cfg.notion_api_key == "dummy-notion-key"
    assert cfg.notion_watchlist_db_id is None


def test_both_backends_fully_populated_is_fine():
    env = {**SHEETS_ENV, **NOTION_ENV, "DATA_SOURCE": "sheets"}
    cfg = load_config(env=env)

    assert cfg.data_source == "sheets"
    assert cfg.notion_db_url == "dummy-notion_db_url"


def test_unrecognized_data_source_fails_loudly():
    env = {**SHEETS_ENV, "DATA_SOURCE": "airtable"}

    with pytest.raises(ConfigError) as exc_info:
        load_config(env=env)

    assert "DATA_SOURCE" in str(exc_info.value)
    assert "airtable" in str(exc_info.value)


def test_malformed_int_var_raises_clear_error():
    env = {**SHEETS_ENV, "NEWS_WINDOW_HOURS": "not-a-number"}

    with pytest.raises(ConfigError) as exc_info:
        load_config(env=env)

    assert "NEWS_WINDOW_HOURS" in str(exc_info.value)


def test_malformed_bool_var_raises_clear_error():
    env = {**SHEETS_ENV, "MONITOR_EXISTING_PARTNERS": "sort-of"}

    with pytest.raises(ConfigError) as exc_info:
        load_config(env=env)

    assert "MONITOR_EXISTING_PARTNERS" in str(exc_info.value)
