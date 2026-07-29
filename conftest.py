"""Seeds dummy required env vars so `import config` succeeds during test
collection even when no real .env is present. Individual tests that need to
exercise config loading behavior call config.load_config(env=...) directly
with their own values, bypassing these defaults entirely.
"""

import os

_DUMMY_REQUIRED = {
    "ANTHROPIC_API_KEY": "dummy",
    "SLACK_WEBHOOK_URL": "dummy",
    # DATA_SOURCE defaults to "sheets", so its backend vars are the ones required
    # by default; Notion vars are left unset (optional/lazy per §11).
    "GOOGLE_SHEETS_ID": "dummy",
    "GOOGLE_SERVICE_ACCOUNT_JSON": "dummy",
    "SHEETS_URL": "dummy",
}

for _key, _value in _DUMMY_REQUIRED.items():
    os.environ.setdefault(_key, _value)
