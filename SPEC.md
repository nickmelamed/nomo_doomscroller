# Build Spec — NOMO Doomscroller (Competition · Industry · Partner Scouting → Slack)

A scheduled agent that every weekday morning reads a team-owned watchlist from Notion, monitors news for the tracked entities, scouts the web for *new* competitors and partner prospects, and posts a ranked, sectioned digest to Slack. The watchlist is data the team owns and edits; the agent is stateless logic that reads it at runtime. Internally nicknamed **Doomscroller** — it doomscrolls so the team doesn't have to.

---

## 1. Goals & scope

**v1 (this spec):**
- Read a **Notion watchlist** (competitors, partner prospects, exclusions), a **criteria/config page**, and NOMO's existing **Partners and perks** DB (read-only, for exclusions and reward-landscape context) at runtime — no hardcoded lists.
- **Monitoring pass:** find last-24h news for each active tracked entity, plus standalone industry-trend news (policy, litigation, funding) not tied to any single tracked entity.
- **Scouting pass:** discover *new* candidate competitors and partner prospects that aren't tracked yet.
- **Synthesize** a sectioned, ranked digest with a hard relevance bar (says "quiet day" instead of padding).
- **Post to Slack** with a footer showing what's tracked and a link to manage the list.
- Run on a **daily schedule**.

**Non-goals for v1 (see §14):** writing back to Notion from Slack, interactive buttons, a slash command. Discovered candidates are *surfaced for a human to approve*, never auto-added.

---

## 2. How it works

```
                ┌────────────────────────── Notion ──────────────────────────┐
                │  Watchlist DB (new, team-owned)                            │
                │  Criteria/Config page (new, team-owned)                    │
                │  Partners & Perks DB (existing, read-only)                 │
                └──────────────────────────┬──────────────────────────────────┘
                                        │ read at runtime
                                        ▼
  ┌──────────┐   ┌──────────────┐   ┌──────────────┐   ┌───────────┐   ┌────────┐
  │ 1. Load  │──▶│ 2. Monitoring│──▶│ 3. Scouting  │──▶│ 4. Dedup +│──▶│ 5.     │
  │  config  │   │  (per entity,│   │ (discovery,  │   │  filter   │   │ Synth. │
  │          │   │  web search) │   │  web search) │   │           │   │ + rank │
  └──────────┘   └──────────────┘   └──────────────┘   └───────────┘   └───┬────┘
                                                                           ▼
                                                                    ┌────────────┐
                                                                    │ 6. Render +│
                                                                    │  post Slack│
                                                                    └────────────┘
```

Two kinds of Claude calls: **gather calls** use the server-side web search tool and return structured JSON; the **synthesis call** takes that JSON (no tools), applies the relevance bar, and returns the final structured digest. Code renders the digest to Slack. This separation keeps synthesis cheap, deterministic, and testable.

---

## 3. Tech stack

- **Language:** Python 3.11+
- **LLM:** Anthropic API via the `anthropic` SDK, with the **server-side web search tool** (no separate news API needed). Model configurable via env; default to a current Sonnet-tier model.
- **Watchlist store:** Notion API via `notion-client`.
- **Delivery:** Slack **incoming webhook** (v1).
- **Scheduler:** **GitHub Actions** cron (fastest to stand up, version-controlled). Production alternative: AWS Lambda on an EventBridge schedule (they're already on AWS).

> **Build-time note:** the web-search tool `type` string and default model ID in §8 are current as of this writing — re-confirm against docs.claude.com before building, since Anthropic versions both periodically.

---

## 4. Prerequisites & setup (manual, one-time)

1. **Create the Notion watchlist database** with the schema in §6.1, and the **criteria page** in §6.2.
2. Create a **Notion internal integration**, share the new watchlist DB, the criteria page, **and the existing "Partners and perks" DB** (read-only — §6.3) with it, and note all three IDs (**database ID**, **page ID**, **partners DB ID**).
3. Create a **Slack incoming webhook** pointed at the target channel (e.g. `#nomo-doomscroller`).
4. Set the secrets in §11.

Claude Code should generate a `README.md` with these steps and a `scripts/create_notion_db.py` helper that provisions the DB via the Notion API if desired.

---

## 5. Repository structure

```
nomo_doomscroller/
├── main.py                 # orchestrator: runs the pipeline end to end
├── config.py               # env vars, constants, model + window settings
├── notion_source.py        # read watchlist + criteria page + partners DB -> typed objects
├── gather.py               # monitoring + industry trends + scouting Claude calls (web search)
├── synthesize.py           # synthesis Claude call -> digest JSON
├── slack_render.py         # digest JSON -> Slack Block Kit -> post
├── models.py               # dataclasses: Entity, NewsItem, Candidate, Digest
├── prompts/
│   ├── monitor.txt
│   ├── industry.txt
│   ├── scout.txt
│   └── synthesize.txt
├── tests/
│   └── test_fixtures/      # canned config + known-event fixtures (see §13)
├── requirements.txt
├── README.md
└── .github/workflows/nomo_doomscroller.yml
```

---

## 6. Data model

### 6.1 Notion watchlist database

One row per tracked entity. Properties:

| Property | Type | Notes |
|---|---|---|
| **Name** | Title | The entity (e.g. "Uber"). |
| **Type** | Select | `Competitor` · `Partner prospect` · `Excluded` |
| **Status** | Select | `Active` · `Paused` · `Converted` |
| **Category** | Multi-select | For partners: `sports`, `concerts`, `app credits`, `travel`, `dining`, `retail`, … (extensible) |
| **Region** | Multi-select | `US` · `UK` · `BR` · `Other` — market(s) this entity is relevant to. Feeds ranking weight, not a hard filter (see §6.2). |
| **Aliases / keywords** | Rich text | Comma-separated variants, e.g. `Uber One, Uber Technologies, Uber Rewards`. |
| **Source URL** | URL | Optional press/blog page for higher-signal monitoring. |
| **Why tracked** | Rich text | Context; also feeds relevance judgement. |
| **Priority** | Select | `High` · `Medium` · `Low` (affects ranking). |
| **Added by** | Person / Rich text | Accountability. |
| **Date added** | Created time | Auto. |

**Query at runtime:** all rows where `Status = Active` (Paused/Converted are ignored). `Excluded` type rows are loaded but only used as a suppression list.

> **Note:** `Existing partner` is no longer a Type here — real partners live in NOMO's existing **Partners and perks** database (see §6.3), so there's no need to duplicate the ~50 active partner rows into this DB. When a `Partner prospect` converts to an actual partner, set its Watchlist `Status` to `Converted` (stops monitoring/scouting it) and add it to the Partners DB through NOMO's normal onboarding process — that's what makes it permanently excluded from future scouting (see §10).

### 6.2 Notion criteria / config page

A single Notion page whose text content the agent reads and passes into prompts. Team-editable. Should contain clearly-labeled sections:

- **NOMO context** — 3–5 sentences on what NOMO does and who its users are. Feeds every relevance judgement.
- **Region weighting** — NOMO's active markets and their relative priority (e.g. "BR is the primary market; US and UK are growing; any other region is exploratory signal only"). Used to **weight, not filter** — an item about a market NOMO doesn't operate in yet can still be worth surfacing if it signals competitor/partner expansion into a region NOMO cares about.
- **Competitor criteria** — what makes something a competitor worth flagging.
- **Partner criteria** — what makes a good rewards partner (has tradeable reward inventory — tickets, travel, app credits — audience fit, dealability).
- **Industry topics** — themes/keywords to monitor beyond named entities (e.g. loyalty program launches, rewards-fintech funding, ticketing partnerships, youth social media policy, litigation against major platforms).
- **Do-not-suggest** — categories or names to never surface as candidates.

Parse as plain text blocks; do not require rigid structure beyond the section headers.

### 6.3 Existing partners source (read-only)

NOMO already maintains a **"Partners and perks"** Notion database (business-owned, not created or edited by this agent). The agent reads it at runtime alongside the Watchlist DB and criteria page, but never writes to it.

**Fields the agent needs** (a subset of the full sheet):

| Field | Used for |
|---|---|
| **Entity** | Name matching for the exclusion list. |
| **Status** | Only `Active` rows are used. |
| **Region** (US/UK/BR view) | Feeds the same region-weighting logic as the Watchlist DB. |
| **Perk description** (`en \| sentence` + `en \| title`) | Rolled up into a single `reward_landscape` summary (one line per active partner, e.g. `Fever: live-events redemption`), used for gap-analysis in scouting and for grounding `why_fits` in synthesis. See §7 Stage 1, 3, 5. |

Everything else in that database (Badge, color, card images, codes) is a product/UI concern with no bearing on monitoring or scouting — skip it.

**Query at runtime:** all rows where Status = Active. These names feed `excluded_names` exactly like `Excluded`-type Watchlist rows — the agent must never suggest an already-active partner as a "new candidate."

---

## 7. Pipeline detail

**Stage 1 — Load config** (`notion_source.py`)
Read three Notion sources: the Watchlist DB, the criteria/config page, and the existing **Partners and perks** DB (read-only, §6.3). Return: `entities: list[Entity]` (active Competitor/Partner prospect rows, each with Region), `excluded_names: set[str]` (from Excluded Watchlist rows + all Active rows in the Partners DB), `reward_landscape: list[str]` (one line per active partner, built from the perk description fields), and `criteria: Criteria` (parsed text sections, including region weighting). Fail loudly if any of the three Notion objects is unreachable.

**Stage 2 — Monitoring pass** (`gather.py`)
Two parts, run together:
- **(a) Per-entity:** for each active Watchlist `Competitor` and `Partner prospect` row (plus every Active row in the Partners DB, if `MONITOR_EXISTING_PARTNERS` is on), one Claude call with web search using `prompts/monitor.txt`. Run entities concurrently (bounded, e.g. 5 at a time — comfortably inside standard rate limits at this scale).
- **(b) Industry trends:** one Claude call with web search per topic listed in the criteria page's "Industry topics" section (e.g. youth social media policy, platform litigation, rewards-fintech funding), using `prompts/industry.txt`. Not tied to any tracked entity — this is what feeds the digest's Industry section with standalone policy/regulatory/market news.

Each call returns JSON — §8.1 for (a), §8.1b for (b). On any per-call failure, log and continue — never abort the run.

**Stage 3 — Scouting pass** (`gather.py`)
2–4 discovery Claude calls with web search using `prompts/scout.txt`, one per discovery angle derived from criteria: (a) new entrants/competitors in NOMO's category, (b) recent rewards/loyalty funding or launches, (c) potential partners with reward inventory by category — reasoned against `reward_landscape` to surface categories NOMO doesn't already cover, not just any company with reward inventory. Pass the full tracked-name+alias list (Watchlist + Partners DB) so Claude can pre-exclude, plus each candidate's apparent region. Returns §8.2 (now including a `region` field).

**Stage 4 — Dedup & filter** (`main.py`)
- Drop candidates whose name/alias fuzzy-matches anything in the watchlist or `excluded_names` (normalize case/punctuation; use a simple ratio match).
- Dedup news items — both entity monitoring items and industry trend items — by URL and near-duplicate headline.

**Stage 5 — Synthesize & rank** (`synthesize.py`)
One Claude call, no tools, using `prompts/synthesize.txt`. Input: all monitoring items, all industry trend items, all surviving candidates, `criteria.nomo_context`, `criteria.region_weighting`, priority hints, `reward_landscape` (to ground candidate `why_fits` in concrete comparisons), and tracking counts. Output: the digest JSON in §8.3 — monitoring items populate **Competition** (and partner-relationship news, if `MONITOR_EXISTING_PARTNERS` is on), industry trend items populate **Industry**, and candidates populate **New candidates**. The prompt enforces the relevance bar, weights (never excludes) by region, and `quiet_day` behavior.

**Stage 6 — Render & post** (`slack_render.py`)
Convert digest JSON to Slack Block Kit (§9) and POST to the webhook. If `quiet_day` is true, post the short quiet-day message. Exit non-zero only on delivery failure.

---

## 8. Claude API usage & JSON contracts

**Web search tool config:**
```python
# Monitoring calls: narrow, single-entity, last-24h — few searches needed
monitor_tools = [{"type": "web_search_20250305", "name": "web_search", "max_uses": 3}]

# Industry trends calls: one topic at a time, moderately broad
industry_tools = [{"type": "web_search_20250305", "name": "web_search", "max_uses": 5}]

# Scouting calls: broad discovery across a category — needs more searches
scout_tools = [{"type": "web_search_20250305", "name": "web_search", "max_uses": 8}]
```
`web_search_20250305` (basic search, no dynamic filtering) is sufficient for this use case — re-verify the current tool version and default model ID against docs.claude.com before build, since both are versioned and change over time. As of this writing, the current Sonnet-tier model ID is `claude-sonnet-5`.

All gather calls must instruct Claude to return **only** the specified JSON (no prose, no markdown fences); parse defensively and strip fences if present.

### 8.1 Monitoring output (per entity)
```json
{
  "entity": "Uber",
  "items": [
    {
      "headline": "Uber One expands ticketing rewards",
      "url": "https://example.com/article",
      "source": "TechCrunch",
      "published": "2026-07-26",
      "summary": "One-sentence factual summary.",
      "why_it_matters": "One sentence tied to NOMO.",
      "relevance": "high"
    }
  ]
}
```
Return `"items": []` when nothing in the last 24h clears the bar. Never fabricate URLs.

### 8.1b Industry trends output (per topic)
```json
{
  "topic": "youth social media policy",
  "items": [
    {
      "headline": "State proposes age-verification rule for social apps",
      "url": "https://example.com/article",
      "source": "Reuters",
      "published": "2026-07-27",
      "summary": "One-sentence factual summary.",
      "why_it_matters": "One sentence tied to NOMO's positioning or partner interests.",
      "relevance": "high"
    }
  ]
}
```
Same shape as §8.1, keyed by `topic` instead of `entity` — no named entity required. Feeds the digest's **Industry** section directly. Return `"items": []` when nothing clears the bar.

### 8.2 Scouting output (per discovery call)
```json
{
  "candidates": [
    {
      "name": "ExampleCo",
      "suggested_type": "Partner prospect",
      "category": "travel",
      "region": "BR",
      "why_fits": "One sentence against the criteria.",
      "source_url": "https://example.com/news",
      "confidence": "medium"
    }
  ]
}
```

### 8.3 Synthesis output (final digest)
```json
{
  "quiet_day": false,
  "sections": {
    "competition": [{"headline": "...", "url": "...", "source": "...", "summary": "..."}],
    "industry":    [{"headline": "...", "url": "...", "source": "...", "summary": "..."}],
    "partner_prospects": [{"headline": "...", "url": "...", "source": "...", "summary": "..."}],
    "new_candidates": [
      {"name": "ExampleCo", "suggested_type": "Partner prospect", "region": "BR", "why_fits": "...", "source_url": "..."}
    ]
  },
  "tracking_counts": {"competitors": 12, "partner_prospects": 8}
}
```

### 8.4 Prompt templates (intent — Claude Code to finalize wording)

**`prompts/monitor.txt`**
> You monitor news for NOMO. NOMO context: `{nomo_context}`. Search the web for news from the **last 24 hours** about **{entity_name}** (also known as: {aliases}), a tracked **{type}** relevant to **{regions}**. Why it's tracked: {why_tracked}. Look for anything relevant to NOMO's competitive, partnership, or industry-risk picture — product moves, funding, partnerships, and sales, **as well as regulatory, legal, or policy developments** (e.g. lawsuits, youth social media or data-privacy regulation, age-verification laws) involving this entity. Ignore routine coverage, listicles, and SEO filler. Prefer primary sources (company press, reputable industry press, court filings, funding databases, regulatory releases). Return ONLY JSON matching this schema: `{schema}`. If nothing clears the bar, return an empty items array.

**`prompts/industry.txt`**
> You monitor industry trends for NOMO. NOMO context: `{nomo_context}`. Region weighting: `{region_weighting}`. Search the web for news from the **last {window_hours} hours** on this industry topic: **{topic}** — things like youth social media policy and regulation, lawsuits or enforcement actions against major tech/social platforms, loyalty and rewards-program trends, or ticketing/experiential partnerships, depending on which topic this is. This is **not** about a specific tracked company — it's broader market, regulatory, or industry signal that could affect NOMO's positioning, its school/EdTech partnerships, or its partner-prize strategy. Ignore routine coverage, listicles, and SEO filler. Prefer primary sources (regulatory filings, court records, reputable industry press) over aggregators. Return ONLY JSON matching this schema: `{schema}`. If nothing clears the bar, return an empty items array.

**`prompts/scout.txt`**
> You scout for NOMO. NOMO context: `{nomo_context}`. Partner/competitor criteria: `{criteria}`. Region weighting: `{region_weighting}`. Discovery angle: `{angle}`. NOMO's current reward partner lineup (for gap analysis on the partner angle): `{reward_landscape}`. Find entities that fit but are **NOT** in this already-tracked list: `{tracked_names}`. Also honor this do-not-suggest list: `{do_not_suggest}`. Prefer recent (last ~30 days) signals. Return ONLY JSON matching `{schema}`, including each candidate's apparent region. Be conservative — quality over quantity; return few, strong candidates.

**`prompts/synthesize.txt`**
> You are the editor of NOMO's daily digest. Given the monitoring items, industry trend items, and scouted candidates below, produce the final digest. Apply a hard relevance bar: cut anything marginal, dedupe overlapping stories, and rank by importance to NOMO (respect priority hints and region weighting: `{region_weighting}` — weight by region, never exclude solely for it). Use NOMO's current reward lineup (`{reward_landscape}`) to ground candidate `why_fits` in concrete comparisons where useful. Organize into Competition, Industry, and Partner prospects, plus a New candidates section (clearly proposed, not yet tracked). If, after filtering, there is little of substance, set `quiet_day` to true. Return ONLY JSON matching `{schema}`. Data: `{payload}`.

---

## 9. Slack output format (Block Kit)

- **Header:** `📱 NOMO Doomscroller — {date}`
- One **section per category** with a bold subheader; each item rendered as:
  `• *<{url}|{headline}>* — {summary} _({source})_`
- **New candidates** section, visually distinct, each:
  `• *{name}* — {why_fits} · _proposed {suggested_type}_ · <{source_url}|source>`
  with a note: `_Proposed only — add via the Notion list to start tracking._`
- **Footer (context block), every day:**
  `Tracking {competitors} competitors · {partner_prospects} partner prospects · <{notion_db_url}|manage the list>`
- **Quiet day:** header + a single line, e.g. `Quiet day — nothing material to report. (Tracking … · manage the list)`.

Keep messages concise; truncate any section to a max item count (config, default 6) with a "+N more" line.

---

## 10. Status → behavior rules (the Uber example)

Behavior is driven by the Watchlist DB's `Type`/`Status` fields *and* the existing Partners DB's `Status` field, so the team controls it entirely by editing Notion — no code changes needed either way:

| Source | Type / Status | Monitored? | Scouted as candidate? | In digest as |
|---|---|---|---|---|
| Watchlist | `Competitor` (Active) | yes | already tracked → excluded | Competition |
| Watchlist | `Partner prospect` (Active) | yes | already tracked → excluded | Partner prospects |
| Watchlist | `Excluded` | no | never suggested | — |
| Watchlist | any, `Status = Paused/Converted` | no | excluded | — |
| Partners DB | Active | optional (`MONITOR_EXISTING_PARTNERS`), relationship news only | **never suggested** — always excluded | Partner news, or omitted |

When NOMO partners with Uber: someone sets Uber's Watchlist row `Status` to `Converted` (it stops being monitored/scouted from the Watchlist side) and separately adds Uber to the Partners DB through NOMO's normal partner-onboarding process — outside this agent's scope. From the next morning on, Uber is excluded from prospect scouting because it now appears in the Partners DB's Active list, independent of its old Watchlist row.

---

## 11. Configuration (env / GitHub secrets)

```
ANTHROPIC_API_KEY
ANTHROPIC_MODEL              # default: current Sonnet-tier model id (claude-sonnet-5 as of writing)
NOTION_API_KEY
NOTION_WATCHLIST_DB_ID
NOTION_CRITERIA_PAGE_ID
NOTION_PARTNERS_DB_ID        # existing "Partners and perks" DB, read-only
NOTION_DB_URL               # for the "manage the list" footer link
SLACK_WEBHOOK_URL
NEWS_WINDOW_HOURS           # default 24
MAX_ITEMS_PER_SECTION       # default 6
MONITOR_EXISTING_PARTNERS   # default false — if true, monitors Partners DB Active rows for relationship news
MONITOR_MAX_USES            # default 3 — web search cap per monitoring call
SCOUT_MAX_USES              # default 8 — web search cap per scouting call
```

---

## 12. Scheduling

`.github/workflows/nomo_doomscroller.yml`:
```yaml
name: NOMO Doomscroller
on:
  schedule:
    - cron: "0 13 * * 1-5"   # 13:00 UTC ≈ 6:00am PT, weekdays
  workflow_dispatch: {}        # manual test runs
jobs:
  run:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: "3.11" }
      - run: pip install -r requirements.txt
      - run: python main.py
        env:
          ANTHROPIC_API_KEY: ${{ secrets.ANTHROPIC_API_KEY }}
          NOTION_API_KEY: ${{ secrets.NOTION_API_KEY }}
          NOTION_WATCHLIST_DB_ID: ${{ secrets.NOTION_WATCHLIST_DB_ID }}
          NOTION_CRITERIA_PAGE_ID: ${{ secrets.NOTION_CRITERIA_PAGE_ID }}
          NOTION_PARTNERS_DB_ID: ${{ secrets.NOTION_PARTNERS_DB_ID }}
          NOTION_DB_URL: ${{ secrets.NOTION_DB_URL }}
          SLACK_WEBHOOK_URL: ${{ secrets.SLACK_WEBHOOK_URL }}
```
`workflow_dispatch` gives a one-click manual run for testing. Production alternative: package as a Lambda, schedule with EventBridge `cron`, secrets in SSM/Secrets Manager.

---

## 13. Error handling & quality bar

- **Resilience:** one entity or discovery call failing must not abort the run — log and continue with partial results.
- **Relevance bar:** enforced in both gather and synthesis; empty results are valid and produce a quiet-day post rather than filler.
- **Freshness:** respect `NEWS_WINDOW_HOURS`; dedupe by URL + near-duplicate headline.
- **Source quality:** prefer primary sources; the prompts explicitly deprioritize listicles/SEO content.
- **No fabrication:** URLs must come from search results; discard any item whose URL wasn't returned by the tool.
- **Observability:** log per-stage counts (entities scanned, items found, candidates surfaced, items after filtering) to stdout for the Actions log.

---

## 14. Validation before go-live

Mirror the query-layer discipline: build a small **fixture set** and confirm the agent finds what it should and skips what it shouldn't.

- Seed the watchlist with a few known entities that had **real recent news**, and confirm the monitoring pass surfaces those stories.
- Include an entity with a **quiet week** and confirm it does *not* get padded.
- Add a **do-not-suggest** entry and confirm scouting never surfaces it.
- Confirm an entity already Active in the **Partners DB** is excluded from prospect scouting, without needing a corresponding Watchlist row.
- Seed a candidate whose reward category is **already covered** in `reward_landscape` and confirm scouting deprioritizes it versus a genuine gap.
- Include entities/candidates across **different regions** (including a non-US/UK/BR one) and confirm ranking reflects region weighting rather than hard-excluding any of them.
- Run via `workflow_dispatch` into a **test channel** for a few days before pointing at the real channel and enabling the schedule.

---

## 15. v2 / future (out of scope now)

- **In-Slack actions:** interactive buttons on each candidate — `Add as competitor` / `Add as prospect` / `Ignore` — that write to the Notion watchlist via its API. Requires a Slack app with an interactivity request URL (a small always-on endpoint), so ship v1 first.
- **`/track add [name]`** slash command for adding entities without opening Notion.
- **Weekly rollup** and **per-section channel routing**.
- **Feedback signal:** a 👍/👎 on items to tune the relevance bar over time.

---

## 16. Implementation phases (build order)

This is the build order, distinct from §7's runtime execution order — each phase depends only on the ones before it, and each ends with its own verification step so problems surface early rather than after everything's wired together.

**Phase 0 — Scaffolding**
Deliverables: repo skeleton (§5), dependency file, `config.py` (env var loading per §11), `.env.example`, README skeleton.
Verify: `config.py` loads all env vars against a filled-in `.env` without error; missing required vars fail loudly with a clear message.

**Phase 1 — Data models**
Deliverables: `models.py` — dataclasses for `Entity`, `NewsItem`, `Candidate`, `Digest`, `Criteria`, matching §6 and §8 exactly.
Verify: instantiate each with a hand-written example matching the JSON contracts; no field mismatches.

**Phase 2 — Notion read layer**
Deliverables: `notion_source.py` — reads the Watchlist DB, criteria page, and Partners DB (§6.1–6.3) into Phase 1's typed objects, building `excluded_names` and `reward_landscape`.
Verify: point at the real Notion workspace, print the loaded objects, and manually confirm they match what's actually in Notion. First good point to test the §6.2 criteria-parsing fallback/warning against a deliberately malformed section header.

**Phase 3 — Gather layer**
Deliverables: `gather.py` and `prompts/monitor.txt`, `prompts/industry.txt`, `prompts/scout.txt` — Stage 2(a)+(b) and Stage 3 (§7), with the web search tool config and `max_uses` split from §8.
Verify: run against a couple of real tracked entities and topics; confirm each call's JSON parses cleanly into Phase 1 models, and confirm a simulated per-call failure logs and continues rather than crashing the run.

**Phase 4 — Dedup & filter**
Deliverables: the Stage 4 fuzzy-match logic — candidates checked against `excluded_names` before ever reaching synthesis.
Verify: unit tests with known duplicate names, alias variants, and at least one Partners-DB-only exclusion (no matching Watchlist row).

**Phase 5 — Synthesize**
Deliverables: `synthesize.py` and `prompts/synthesize.txt` — Stage 5, producing the final digest JSON (§8.3).
Verify: run against fixture inputs from Phase 3/4 (not live calls); confirm the relevance bar cuts marginal items, `quiet_day` triggers on a deliberately weak fixture, and region weighting affects ranking without ever hard-excluding.

**Phase 6 — Slack render & post**
Deliverables: `slack_render.py` — Block Kit formatting (§9) and webhook posting.
Verify: post a fixture-generated digest to a private test Slack channel; visually confirm formatting, section ordering, and the "manage the list" footer link.

**Phase 7 — Orchestration**
Deliverables: `main.py` wiring Phases 2–6 end-to-end, with the error handling and observability logging from §13.
Verify: a single command runs the full pipeline against fixtures and produces a complete digest — the end-to-end verification step the whole build has been working toward.

**Phase 8 — Scheduling & go-live**
Deliverables: `.github/workflows/nomo_doomscroller.yml` (§12), GitHub secrets wired up, README finalized.
Verify: run the full §14 checklist via `workflow_dispatch` into the test channel for a few days before pointing at the real channel and enabling the schedule.