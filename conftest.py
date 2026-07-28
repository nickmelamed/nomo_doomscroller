"""Seeds dummy required env vars so `import config` succeeds during test
collection even when no real .env is present. Individual tests that need to
exercise config loading behavior call config.load_config(env=...) directly
with their own values, bypassing these defaults entirely.
"""

import os

_DUMMY_REQUIRED = {
    "ANTHROPIC_API_KEY": "dummy",
    "NOTION_API_KEY": "dummy",
    "NOTION_WATCHLIST_DB_ID": "dummy",
    "NOTION_CRITERIA_PAGE_ID": "dummy",
    "NOTION_PARTNERS_DB_ID": "dummy",
    "NOTION_DB_URL": "dummy",
    "SLACK_WEBHOOK_URL": "dummy",
}

for _key, _value in _DUMMY_REQUIRED.items():
    os.environ.setdefault(_key, _value)
