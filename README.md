# NOMO Doomscroller

A scheduled agent that reads a team-owned Notion watchlist, monitors news for tracked
competitors and partner prospects, scouts for new candidates, and posts a ranked digest
to Slack every weekday morning. See [SPEC.md](SPEC.md) for the full design.

## Prerequisites & setup (one-time, manual)

1. **Create the Notion watchlist database** with the schema in SPEC.md §6.1. You can do
   this by hand in Notion, or run the provisioning helper:

   ```bash
   pip install -r requirements.txt
   python scripts/create_notion_db.py --parent-page-id <a-notion-page-id>
   ```

   This prints the new database's ID and URL — save the ID as
   `NOTION_WATCHLIST_DB_ID` below.

2. **Create the criteria/config page** (SPEC.md §6.2) as a plain Notion page with these
   section headings (heading blocks), in any order, each followed by its content:
   - `NOMO context`
   - `Region weighting`
   - `Competitor criteria`
   - `Partner criteria`
   - `Industry topics` (one topic per line/bullet)
   - `Do-not-suggest` (one entry per line/bullet)

3. **Create a Notion internal integration** (https://www.notion.so/my-integrations),
   then share all three Notion objects with it:
   - The Watchlist database (step 1)
   - The criteria page (step 2)
   - NOMO's existing **Partners and perks** database (read-only — the agent never
     writes to it)

   Note the integration's API key and the three object IDs.

4. **Create a Slack incoming webhook** pointed at your target channel (start with a
   private test channel, e.g. `#nomo-doomscroller-test`), via
   https://api.slack.com/apps → your app → Incoming Webhooks.

5. **Set the environment variables** below (locally via a `.env` file — copy
   `.env.example` — and in GitHub Actions as repo secrets for the scheduled run).

## Configuration

See [.env.example](.env.example) for the full list. Required (no default):
`ANTHROPIC_API_KEY`, `NOTION_API_KEY`, `NOTION_WATCHLIST_DB_ID`,
`NOTION_CRITERIA_PAGE_ID`, `NOTION_PARTNERS_DB_ID`, `NOTION_DB_URL`,
`SLACK_WEBHOOK_URL`. Everything else has a code-level default (see `config.py`) and is
optional to set.

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
