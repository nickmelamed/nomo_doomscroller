import pytest

from config import REQUIRED_VARS, ConfigError, load_config

REQUIRED_FILLED = {name: f"dummy-{name.lower()}" for name in REQUIRED_VARS}


def test_loads_with_all_required_vars_and_documented_defaults():
    cfg = load_config(env=REQUIRED_FILLED)

    assert cfg.anthropic_api_key == "dummy-anthropic_api_key"
    assert cfg.notion_watchlist_db_id == "dummy-notion_watchlist_db_id"
    assert cfg.anthropic_model == "claude-sonnet-5"
    assert cfg.news_window_hours == 24
    assert cfg.max_items_per_section == 6
    assert cfg.monitor_existing_partners is False
    assert cfg.monitor_max_uses == 3
    assert cfg.scout_max_uses == 8


def test_optional_vars_override_defaults():
    env = {
        **REQUIRED_FILLED,
        "ANTHROPIC_MODEL": "claude-opus-5",
        "NEWS_WINDOW_HOURS": "48",
        "MAX_ITEMS_PER_SECTION": "10",
        "MONITOR_EXISTING_PARTNERS": "true",
        "MONITOR_MAX_USES": "4",
        "SCOUT_MAX_USES": "12",
    }
    cfg = load_config(env=env)

    assert cfg.anthropic_model == "claude-opus-5"
    assert cfg.news_window_hours == 48
    assert cfg.max_items_per_section == 10
    assert cfg.monitor_existing_partners is True
    assert cfg.monitor_max_uses == 4
    assert cfg.scout_max_uses == 12


def test_missing_required_vars_raises_one_combined_error():
    with pytest.raises(ConfigError) as exc_info:
        load_config(env={})

    message = str(exc_info.value)
    for name in REQUIRED_VARS:
        assert name in message


def test_missing_some_required_vars_names_only_those():
    env = dict(REQUIRED_FILLED)
    del env["SLACK_WEBHOOK_URL"]
    del env["NOTION_API_KEY"]

    with pytest.raises(ConfigError) as exc_info:
        load_config(env=env)

    message = str(exc_info.value)
    assert "SLACK_WEBHOOK_URL" in message
    assert "NOTION_API_KEY" in message
    assert "ANTHROPIC_API_KEY" not in message


def test_malformed_int_var_raises_clear_error():
    env = {**REQUIRED_FILLED, "NEWS_WINDOW_HOURS": "not-a-number"}

    with pytest.raises(ConfigError) as exc_info:
        load_config(env=env)

    assert "NEWS_WINDOW_HOURS" in str(exc_info.value)


def test_malformed_bool_var_raises_clear_error():
    env = {**REQUIRED_FILLED, "MONITOR_EXISTING_PARTNERS": "sort-of"}

    with pytest.raises(ConfigError) as exc_info:
        load_config(env=env)

    assert "MONITOR_EXISTING_PARTNERS" in str(exc_info.value)
