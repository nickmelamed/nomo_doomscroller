# NOMO Doomscroller

A scheduled agent that reads a team-owned watchlist, monitors news for tracked
competitors and partner prospects, scouts for new candidates, and posts a ranked digest
to Slack Monday and Thursday mornings, each run covering the news since the last
successful run. See [SPEC.md](SPEC.md) for the full design.

The agent supports two interchangeable data-source backends, selected at runtime by
`DATA_SOURCE`:

- **`sheets`** (default, active in v1) — Google Sheets, via a service account.
- **`notion`** (built, dormant) — Notion, ready to switch on once workspace access opens
  up. Both backends implement the same contract (`SourceData`, SPEC.md §6.0), so
  switching later is a config change, not a rewrite.

## Prerequisites & setup (one-time, manual)

Set up **Path A** now. **Path B** can be done whenever Notion workspace access opens up
— the code for both ships from day one, so nothing needs rebuilding later.

### Path A — Google Sheets (active for v1)

1. Create one Google Sheets workbook with four tabs: **Watchlist** (§6.1), **Criteria**
   (§6.2), **Industry Topics** (§6.3), **Partners** (§6.4 — a live pull from NOMO's
   Notion database, not manually maintained).
2. Create a **Google Cloud service account** (any Google account can do this — no
   workspace-ownership gate involved) and enable the **Google Sheets API** for its
   project; generate a JSON key for it.
3. **Share the workbook** with the service account's email address (found in the JSON
   key) — Viewer access is enough, since the agent only reads.
4. Note the spreadsheet ID (from its URL) and store the service account JSON as a
   secret.

### Path B — Notion (built, activate later)

1. Create the Notion watchlist database (§6.1), criteria page (§6.2), and industry
   topics database (§6.3), mirroring the Sheets structure.
2. Create a **Notion internal integration** (requires a **workspace owner**), share the
   new watchlist DB, criteria page, topics DB, and the existing Partners DB with it, and
   note all four IDs (`NOTION_TOPICS_DB_ID` isn't in SPEC.md §11's literal var list, but
   is needed to locate the topics DB — added to close that gap).
3. To switch over: set `DATA_SOURCE=notion` and fill in the `NOTION_*` secrets below —
   no code changes needed.

### Both paths

- Create a **Slack incoming webhook** pointed at your target channel (start with a
  private test channel, e.g. `#nomo-doomscroller-test`), via
  https://api.slack.com/apps → your app → Incoming Webhooks.
- Set the environment variables below. It's fine to have both Sheets and Notion
  credentials populated at once — only whichever `DATA_SOURCE` is active gets used.

## Configuration

See [.env.example](.env.example) for the full list. Required regardless of backend:
`ANTHROPIC_API_KEY`, `SLACK_WEBHOOK_URL`. Required only for the active `DATA_SOURCE`:
`GOOGLE_SHEETS_ID` / `GOOGLE_SERVICE_ACCOUNT_JSON` / `SHEETS_URL` for `sheets`, or
`NOTION_API_KEY` / `NOTION_WATCHLIST_DB_ID` / `NOTION_CRITERIA_PAGE_ID` /
`NOTION_TOPICS_DB_ID` / `NOTION_PARTNERS_DB_ID` / `NOTION_DB_URL` for `notion`.
Everything else has a
code-level default (see `config.py`) and is optional.

> Optional vars are **not** wired into `.github/workflows/nomo_doomscroller.yml`'s
> `env:` block, since they already have working defaults — if you need to override one
> in production (e.g. a different `ANTHROPIC_MODEL`), add it to the workflow file's
> `env:` block explicitly.

## Running

```bash
pip install -r requirements.txt
python main.py
```

## Tests

```bash
pytest
```

## Scheduling & go-live

`.github/workflows/nomo_doomscroller.yml` runs the pipeline on a twice-weekly cron
(currently Monday/Thursday, 13:00 UTC ≈ 6:00am PT) and also exposes a manual
`workflow_dispatch` trigger for testing. One-time setup, in the GitHub repo's
**Settings → Secrets and variables → Actions**:

To change which two days it runs, edit only the `cron:` schedule's day-of-week field
in that workflow file — the gather window is computed dynamically from time since the
last successful run (`main.py`'s `_effective_gather_config`), so no other file needs
to change.

- **Secrets** tab — add whichever set your active `DATA_SOURCE` needs (both sets can be
  populated at once, same as locally): `ANTHROPIC_API_KEY`, `SLACK_WEBHOOK_URL`, and
  either `GOOGLE_SHEETS_ID` / `GOOGLE_SERVICE_ACCOUNT_JSON` / `SHEETS_URL` or
  `NOTION_API_KEY` / `NOTION_WATCHLIST_DB_ID` / `NOTION_CRITERIA_PAGE_ID` /
  `NOTION_TOPICS_DB_ID` / `NOTION_PARTNERS_DB_ID` / `NOTION_DB_URL`.
- **Variables** tab — add `DATA_SOURCE` (`sheets` or `notion`). This one is a repo
  **Variable**, not a secret, since it isn't sensitive.

### Before pointing at the real channel

Point `SLACK_WEBHOOK_URL` at a private test channel first and run a few days of
`workflow_dispatch`, confirming:

- A known-recent-news entity's news actually surfaces.
- A quiet-week entity does *not* get padded with filler.
- A do-not-suggest entry never gets surfaced as a candidate.
- An entity Active only in the Partners source (no Watchlist row) is excluded from
  scouting.
- A candidate whose reward category is already covered gets deprioritized versus a
  genuine gap.
- Cross-region entities/candidates (including a non-primary-market one) are weighted
  in ranking, never hard-excluded.
- If on `DATA_SOURCE=sheets`, the Partners tab's live pull is actually current.
- The paired-row filter and missing-`Status` fallback on the Partners tab both behave
  as documented (§6.4).

Only once those hold up: switch `SLACK_WEBHOOK_URL` to the real channel and let the
`schedule` trigger take over.
