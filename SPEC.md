# Build Spec — NOMO Doomscroller (Competition · Industry · Partner Scouting → Slack)

A scheduled agent that every weekday morning reads a team-owned watchlist, monitors news for the tracked entities, scouts the web for *new* competitors and partner prospects, and posts a ranked, sectioned digest to Slack. The watchlist is data the team owns and edits; the agent is stateless logic that reads it at runtime. Internally nicknamed **Doomscroller** — it doomscrolls so the team doesn't have to.

**Data source:** the agent reads from either **Google Sheets** or **Notion**, chosen at runtime by one config flag (`DATA_SOURCE`) — see §6.0. **v1 runs on Sheets**, since NOMO can't grant Notion workspace access while they're mid-reconfiguration; the Notion path is built in parallel, to the same contract, so switching later is a config change, not a rewrite.

---

## 1. Goals & scope

**v1 (this spec):**
- Read a **watchlist** (competitors, partner prospects, exclusions), a **criteria/config page**, and a **partners source** (read-only, for exclusions and reward-landscape context) at runtime — no hardcoded lists. Backed by **Google Sheets in v1**, with a parallel **Notion** implementation built to the same contract for a later switch (§6.0).
- **Monitoring pass:** find last-24h news for each active tracked entity, plus standalone industry-trend news (policy, litigation, funding) not tied to any single tracked entity.
- **Scouting pass:** discover *new* candidate competitors and partner prospects that aren't tracked yet.
- **Synthesize** a sectioned, ranked digest with a hard relevance bar (says "quiet day" instead of padding).
- **Post to Slack** with a footer showing what's tracked and a link to manage the list.
- Run on a **daily schedule**.

**Non-goals for v1 (see §14):** writing back to the watchlist from Slack, interactive buttons, a slash command. Discovered candidates are *surfaced for a human to approve*, never auto-added.

---

## 2. How it works

```
        ┌──────────────────────── Data source (§6.0) ────────────────────────┐
        │  DATA_SOURCE=sheets (active, v1)  │  DATA_SOURCE=notion (built,    │
        │  Sheets: Watchlist, Criteria,     │  dormant): Notion Watchlist DB, │
        │  Industry Topics, Partners tabs   │  Criteria, Topics, Partners DB  │
        └──────────────────────────┬────────────────────────────────────────┘
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
- **Data source:** pluggable — **Google Sheets** (v1, active) via `gspread` + a Google service account, or **Notion** (built, dormant) via `notion-client`. Selected at runtime by `DATA_SOURCE` (§6.0, §11).
- **Delivery:** Slack **incoming webhook** (v1).
- **Scheduler:** **GitHub Actions** cron (fastest to stand up, version-controlled). Production alternative: AWS Lambda on an EventBridge schedule (they're already on AWS).

> **Build-time note:** the web-search tool `type` string and default model ID in §8 are current as of this writing — re-confirm against docs.claude.com before building, since Anthropic versions both periodically.

---

## 4. Prerequisites & setup (manual, one-time)

The agent supports two interchangeable backends (§6.0). Set up **Path A** now; **Path B** can be done whenever NOMO's Notion access opens up — the code for both ships from day one, so nothing needs rebuilding later.

**Path A — Google Sheets (active for v1)**
1. Create one Google Sheets workbook with four tabs: **Watchlist** (§6.1), **Criteria** (§6.2), **Industry Topics** (§6.3), **Partners** (§6.4 — a live pull from NOMO's Notion database, not manually maintained).
2. Create a **Google Cloud service account** (any Google account can do this — no workspace-ownership gate involved) and enable the **Google Sheets API** for its project; generate a JSON key for it.
3. **Share the workbook** with the service account's email address (found in the JSON key) — Viewer access is enough, since the agent only reads.
4. Note the spreadsheet ID (from its URL) and store the service account JSON as a secret.

**Path B — Notion (built, activate later)**
1. Create the Notion watchlist database (§6.1), criteria page (§6.2), and industry topics database (§6.3), mirroring the Sheets structure.
2. Create a **Notion internal integration** (requires a **workspace owner** — see §6.0), share the new watchlist DB, criteria page, topics DB, and the existing Partners DB with it, and note all four IDs.
3. To switch over: set `DATA_SOURCE=notion` and fill in the `NOTION_*` secrets (§11) — no code changes needed.

**Both paths:**
- Create a **Slack incoming webhook** pointed at the target channel (e.g. `#nomo-doomscroller`).
- Set the secrets in §11 — it's fine to have both Sheets and Notion credentials populated at once; only whichever `DATA_SOURCE` is active gets used.

Claude Code should generate a `README.md` covering both paths.

---

## 5. Repository structure

```
nomo_doomscroller/
├── main.py                 # orchestrator: runs the pipeline end to end
├── config.py               # env vars, constants, model + window settings, DATA_SOURCE switch
├── sources/
│   ├── base.py              # SourceData contract both backends must satisfy (§6.0)
│   ├── sheets_source.py     # Google Sheets backend (active in v1)
│   └── notion_source.py     # Notion backend (built, dormant until DATA_SOURCE=notion)
├── gather.py               # monitoring + industry trends + scouting Claude calls (web search)
├── synthesize.py           # synthesis Claude call -> digest JSON
├── slack_render.py         # digest JSON -> Slack Block Kit -> post
├── models.py               # dataclasses: Entity, NewsItem, Candidate, Digest, SourceData, Criteria
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

### 6.0 Source abstraction

Both backends implement the same contract, defined once in `sources/base.py`:

```python
def load_all() -> SourceData:
    """Returns entities, excluded_names, reward_landscape, industry_topics,
    and parsed criteria — identical shape regardless of backend."""
```

`main.py` picks the implementation via one config value:
```
DATA_SOURCE = "sheets" | "notion"
```
Everything downstream of Stage 1 (§7) — gather, dedup, synthesis, render — consumes `SourceData` and has zero awareness of which backend produced it. Switching from Sheets to Notion later is a one-line config change plus filling in the `NOTION_*` secrets (§11) — no code changes.

> **Why Sheets first:** creating a Notion integration requires **workspace owner** permissions, and NOMO can't grant that while mid-reconfiguration of their Notion setup. Google Sheets sidesteps this — access is per-file, granted to a service account, with no workspace-level gate. Build both now; verify Sheets live; verify Notion against mocked fixtures until real access opens up (§16 Phase 2).

**Structure resilience.** Both source column names, tab layout, and Notion property names will likely drift over time — the Partners pull already lost a `Status` column somewhere between Notion and its Sheets mirror, which is exactly the kind of thing this needs to survive gracefully. Each backend module holds one small **column-name map** — logical field name → actual header string — as the single point of contact with the outside structure:

```python
# sources/sheets_source.py
PARTNERS_COLUMNS = {
    "entity": "Partner (entity)",
    "status": "Status",       # optional — see fallback below
    "sentence": "sentence",
    "title": "title",
    "region_flags": ["All", "US", "UK", "BR", "AU"],
}
```
A structure change (renamed header, reordered columns, a new region added) means editing this map, never the parsing logic itself. At load time, validate against it: a missing **required** field (e.g. `entity`) fails loudly with the specific field and tab named; a missing **optional** field (e.g. `status`) logs a clear warning and falls back to a documented default (§6.4) rather than guessing silently.

### 6.1 Watchlist

One row per tracked entity. Logical schema (the table below shows Notion property types; see the Sheets mapping beneath it for the Sheets equivalent):

| Property | Type | Notes |
|---|---|---|
| **Name** | Title | The entity (e.g. "Uber"). |
| **Type** | Select | `Competitor` · `Partner prospect` · `Excluded` |
| **Status** | Select | `Active` · `Paused` · `Converted` |
| **Category** | Multi-select | For partners: `sports`, `concerts`, `app credits`, `travel`, `dining`, `retail`, … (extensible) |
| **Region** | Multi-select | `US` · `UK` · `BR` · `AU` · `Other` — market(s) this entity is relevant to. Feeds ranking weight, not a hard filter (see §6.2). |
| **Aliases / keywords** | Rich text | Comma-separated variants, e.g. `Uber One, Uber Technologies, Uber Rewards`. |
| **Source URL** | URL | Optional press/blog page for higher-signal monitoring. |
| **Why tracked** | Rich text | Context; also feeds relevance judgement. |
| **Priority** | Select | `High` · `Medium` · `Low` (affects ranking). |
| **Added by** | Rich text | Accountability — plain name, not a linked account (keeps both backends symmetric, avoids extra API scopes). |
| **Date added** | Created time | Auto. |

**Query at runtime:** all rows where `Status = Active` (Paused/Converted are ignored). `Excluded` type rows are loaded but only used as a suppression list.

> **Note:** `Existing partner` is no longer a Type here — real partners live in the **Partners source** (see §6.4), so there's no need to duplicate the ~50 active partner rows into this DB. When a `Partner prospect` converts to an actual partner, set its Watchlist `Status` to `Converted` (stops monitoring/scouting it) and add it to the Partners source through NOMO's normal onboarding process — that's what makes it permanently excluded from future scouting (see §10).

**Sheets mapping:** one row per tracked entity on the **Watchlist** tab, columns matching the property names above 1:1, with two exceptions. Type/Status/Priority use **Data Validation** dropdowns (strict lists — see table below). **Region** is five boolean checkbox columns — `All` / `US` / `UK` / `BR` / `AU` — matching the convention already used on the Partners tab (§6.4), since Sheets has no native multi-select-in-one-cell; `All` means global regardless of the individual flags. `Category` stays a single comma-separated column with a **non-restrictive** suggested list (warn, don't reject, on unlisted values) since that set is expected to keep growing.

**Dropdown / validation options:**

| Field | Kind | Options |
|---|---|---|
| Type | strict, single | `Competitor` · `Partner prospect` · `Excluded` |
| Status | strict, single | `Active` · `Paused` · `Converted` |
| Priority | strict, single | `High` · `Medium` · `Low` |
| Region | strict, multi (checkboxes) | `All` · `US` · `UK` · `BR` · `AU` |
| Category | open, multi (comma-separated) | starter list: `sports`, `concerts`, `app credits`, `travel`, `dining`, `retail`, `gaming`, `streaming`, `education`, `other` — extensible |

### 6.2 Criteria / config

A team-editable text source the agent reads and passes into prompts. Should contain clearly-labeled sections:

- **NOMO context** — 3–5 sentences on what NOMO does and who its users are. Feeds every relevance judgement.
- **Region weighting** — NOMO's active markets and their relative priority (e.g. "BR is the primary market; US, UK, and AU are growing; any other region is exploratory signal only"). Used to **weight, not filter** — an item about a market NOMO doesn't operate in yet can still be worth surfacing if it signals competitor/partner expansion into a region NOMO cares about.
- **Competitor criteria** — what makes something a competitor worth flagging.
- **Partner criteria** — what makes a good rewards partner (has tradeable reward inventory — tickets, travel, app credits — audience fit, dealability).
- **Do-not-suggest** — categories or names to never surface as candidates.

Parse as plain text blocks; do not require rigid structure beyond the section headers.

**Sheets mapping:** a **Criteria** tab with two columns — `Section` and `Content` — one row per section above. Both backends extract a `{section_name: text}` dict from their native format (Notion page blocks vs. sheet rows), then hand it to one **shared parser** (in `sources/base.py`) that does the section-name matching and missing-section fallback/warning — written and tested once, not duplicated per backend.

### 6.3 Industry topics

A team-editable list of standing themes to monitor, independent of any tracked entity — e.g. youth social media policy and regulation, litigation against major platforms, rewards/loyalty funding, ticketing or experiential partnerships. Each active topic gets its own daily Claude call (§7 Stage 2b). Unlike Watchlist entities, topics aren't expected to need pausing — this is a continuously-relevant list, not a fixed campaign — so there's no `Status` field here, just:

| Field | Notes |
|---|---|
| **Topic** | The theme, phrased as a short search-relevant description. |
| **Notes** | Optional context — what specifically counts as in-scope for this topic. |

**Sheets mapping:** an **Industry Topics** tab, one row per topic, columns `Topic` and `Notes`.

**Notion mapping:** a small database (or a plain page with one line per topic) with the same two fields.

### 6.4 Partners source (read-only)

NOMO already maintains a **"Partners and perks"** list (business-owned; not created or edited by this agent). The agent reads it at runtime alongside the Watchlist, Criteria, and Industry Topics sources, but never writes to it.

**Fields the agent needs** (a subset of the full sheet):

| Field | Used for |
|---|---|
| **Entity** | Name matching for the exclusion list. |
| **Status** | Only `Active` rows are used. **Not currently present in the live Sheets pull** — see fallback below. |
| **Region flags** (`All` / `US` / `UK` / `BR` / `AU`) | Feeds the same region-weighting logic as the Watchlist. `All` = global, regardless of the individual flags. |
| **Perk description** (`en \| sentence` + `en \| title`) | Rolled up into a single `reward_landscape` summary (one line per active partner, e.g. `Fever: live-events redemption`), used for gap-analysis in scouting and for grounding `why_fits` in synthesis. See §7 Stage 1, 3, 5. |

Everything else (Badge, color, card images, codes) is a product/UI concern with no bearing on monitoring or scouting — skip it.

**Query at runtime:** all rows where Status = Active. These names feed `excluded_names` exactly like `Excluded`-type Watchlist rows — the agent must never suggest an already-active partner as a "new candidate."

> **`Status` fallback.** The live Sheets pull is currently missing a `Status` column, even though the source Notion database has one — worth fixing at the pull itself (add it to whatever query/formula builds the derived sheet) since that's the correct long-term fix. Until then, and as a permanent defensive measure per §6.0's column-name map, `sheets_source.py` should treat a missing `status` field as: log a clear warning, then treat every row as Active. This is the safer failure mode — worst case it's briefly too generous toward a churned partner, rather than risking a currently-active partner getting wrongly suggested as a "new candidate."

> **Paired-row structure (Sheets only).** Each partner in the live pull spans two consecutive rows — an English row, then a Portuguese translation directly beneath it with a blank `Entity` cell. Don't try to positionally "pair" rows (fragile if a partner ever has 1 row or 3) — instead, **keep only rows where `Entity` is non-blank** and ignore everything else. This is both the simplest implementation and the most robust to variation in how many rows follow each entity.

**Notion mapping:** the live "Partners and perks" Notion database itself, read directly — always current, and without the paired-row or missing-Status quirks, since those are artifacts of how the Sheets mirror was built, not of the underlying database.

**Sheets mapping (v1):** a **Partners** tab that auto-pulls from the live Notion database (already connected — not a manual copy). This resolves the original freshness concern: since it's a live formula-driven pull rather than a hand-maintained copy, staleness isn't a standing risk the way a manual sync would be — the main things to watch for now are structural drift (a renamed or reordered column) and the `Status` gap above, both handled by §6.0's column-name map and fallback.

---

## 7. Pipeline detail

**Stage 1 — Load config** (`sources/sheets_source.py` or `sources/notion_source.py`, selected by `DATA_SOURCE`)
Read the four sources — Watchlist, Criteria, Industry Topics, Partners (§6.0–6.4) — via whichever backend is active. Return a `SourceData` object: `entities: list[Entity]` (active Competitor/Partner prospect rows, each with Region), `excluded_names: set[str]` (from Excluded Watchlist rows + all Active rows in the Partners source), `reward_landscape: list[str]` (one line per active partner, built from the perk description fields), `industry_topics: list[str]` (one entry per row in the Industry Topics source), and `criteria: Criteria` (parsed text sections, including region weighting). Fail loudly if any of the four sources is unreachable.

**Stage 2 — Monitoring pass** (`gather.py`)
Two parts, run together:
- **(a) Per-entity:** for each active Watchlist `Competitor` and `Partner prospect` row (plus every Active row in the Partners source, if `MONITOR_EXISTING_PARTNERS` is on), one Claude call with web search using `prompts/monitor.txt`. Run entities concurrently (bounded, e.g. 5 at a time — comfortably inside standard rate limits at this scale).
- **(b) Industry trends:** one Claude call with web search per entry in `industry_topics` (§6.3 — e.g. youth social media policy, platform litigation, rewards-fintech funding), using `prompts/industry.txt`. Not tied to any tracked entity — this is what feeds the digest's Industry section with standalone policy/regulatory/market news.

Each call returns JSON — §8.1 for (a), §8.1b for (b). On any per-call failure, log and continue — never abort the run.

**Stage 3 — Scouting pass** (`gather.py`)
2–4 discovery Claude calls with web search using `prompts/scout.txt`, one per discovery angle derived from criteria: (a) new entrants/competitors in NOMO's category, (b) recent rewards/loyalty funding or launches, (c) potential partners with reward inventory by category — reasoned against `reward_landscape` to surface categories NOMO doesn't already cover, not just any company with reward inventory. Pass the full tracked-name+alias list (Watchlist + Partners source) so Claude can pre-exclude, plus each candidate's apparent region. Returns §8.2 (now including a `region` field).

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
> You monitor industry trends for NOMO. NOMO context: `{nomo_context}`. Region weighting: `{region_weighting}`. Search the web for news from the **last {window_hours} hours** on this industry topic: **{topic}** (notes: {notes}) — things like youth social media policy and regulation, lawsuits or enforcement actions against major tech/social platforms, loyalty and rewards-program trends, or ticketing/experiential partnerships, depending on which topic this is. This is **not** about a specific tracked company — it's broader market, regulatory, or industry signal that could affect NOMO's positioning, its school/EdTech partnerships, or its partner-prize strategy. Ignore routine coverage, listicles, and SEO filler. Prefer primary sources (regulatory filings, court records, reputable industry press) over aggregators. Return ONLY JSON matching this schema: `{schema}`. If nothing clears the bar, return an empty items array.

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
  with a note: `_Proposed only — add via the watchlist to start tracking._`
- **Footer (context block), every day:**
  `Tracking {competitors} competitors · {partner_prospects} partner prospects · <{manage_list_url}|manage the list>`
  `{manage_list_url}` resolves to `SHEETS_URL` or `NOTION_DB_URL` depending on `DATA_SOURCE`.
- **Quiet day:** header + a single line, e.g. `Quiet day — nothing material to report. (Tracking … · manage the list)`.

Keep messages concise; truncate any section to a max item count (config, default 6) with a "+N more" line.

---

## 10. Status → behavior rules (the Uber example)

Behavior is driven by the Watchlist's `Type`/`Status` fields *and* the Partners source's `Status` field, so the team controls it entirely by editing the active backend — no code changes needed either way:

| Source | Type / Status | Monitored? | Scouted as candidate? | In digest as |
|---|---|---|---|---|
| Watchlist | `Competitor` (Active) | yes | already tracked → excluded | Competition |
| Watchlist | `Partner prospect` (Active) | yes | already tracked → excluded | Partner prospects |
| Watchlist | `Excluded` | no | never suggested | — |
| Watchlist | any, `Status = Paused/Converted` | no | excluded | — |
| Partners source | Active | optional (`MONITOR_EXISTING_PARTNERS`), relationship news only | **never suggested** — always excluded | Partner news, or omitted |

When NOMO partners with Uber: someone sets Uber's Watchlist row `Status` to `Converted` (it stops being monitored/scouted from the Watchlist side) and separately adds Uber to the Partners source through NOMO's normal partner-onboarding process — outside this agent's scope. From the next morning on, Uber is excluded from prospect scouting because it now appears in the Partners source's Active list, independent of its old Watchlist row.

---

## 11. Configuration (env / GitHub secrets)

```
ANTHROPIC_API_KEY
ANTHROPIC_MODEL              # default: current Sonnet-tier model id (claude-sonnet-5 as of writing)
DATA_SOURCE                  # "sheets" (default, v1) or "notion" — selects the backend in sources/
SLACK_WEBHOOK_URL
NEWS_WINDOW_HOURS           # default 24
MAX_ITEMS_PER_SECTION       # default 6
MONITOR_EXISTING_PARTNERS   # default false — if true, monitors Partners source Active rows for relationship news
MONITOR_MAX_USES            # default 3 — web search cap per monitoring call
SCOUT_MAX_USES              # default 8 — web search cap per scouting call

# Used when DATA_SOURCE=sheets (active in v1)
GOOGLE_SHEETS_ID
GOOGLE_SERVICE_ACCOUNT_JSON  # raw JSON string (see §4 Path A)
SHEETS_URL                   # for the "manage the list" footer link

# Used when DATA_SOURCE=notion (built, dormant until switched on)
NOTION_API_KEY
NOTION_WATCHLIST_DB_ID
NOTION_CRITERIA_PAGE_ID
NOTION_PARTNERS_DB_ID
NOTION_DB_URL                # for the "manage the list" footer link
```
`config.py` should only *require* the env vars for whichever `DATA_SOURCE` is active — validate the inactive backend's vars lazily (or not at all) so an incomplete Notion setup never blocks a Sheets-backed run.

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
          DATA_SOURCE: ${{ vars.DATA_SOURCE }}   # repo Variable, not a secret — "sheets" or "notion"
          SLACK_WEBHOOK_URL: ${{ secrets.SLACK_WEBHOOK_URL }}
          GOOGLE_SHEETS_ID: ${{ secrets.GOOGLE_SHEETS_ID }}
          GOOGLE_SERVICE_ACCOUNT_JSON: ${{ secrets.GOOGLE_SERVICE_ACCOUNT_JSON }}
          SHEETS_URL: ${{ secrets.SHEETS_URL }}
          NOTION_API_KEY: ${{ secrets.NOTION_API_KEY }}
          NOTION_WATCHLIST_DB_ID: ${{ secrets.NOTION_WATCHLIST_DB_ID }}
          NOTION_CRITERIA_PAGE_ID: ${{ secrets.NOTION_CRITERIA_PAGE_ID }}
          NOTION_PARTNERS_DB_ID: ${{ secrets.NOTION_PARTNERS_DB_ID }}
          NOTION_DB_URL: ${{ secrets.NOTION_DB_URL }}
```
`workflow_dispatch` gives a one-click manual run for testing. Production alternative: package as a Lambda, schedule with EventBridge `cron`, secrets in SSM/Secrets Manager.

Keeping both credential sets populated as secrets from day one means switching `DATA_SOURCE` from `sheets` to `notion` later is a one-click change to the repo Variable — no redeploy, no code change.

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
- Confirm an entity already Active in the **Partners source** is excluded from prospect scouting, without needing a corresponding Watchlist row.
- Seed a candidate whose reward category is **already covered** in `reward_landscape` and confirm scouting deprioritizes it versus a genuine gap.
- Include entities/candidates across **different regions** (including a non-US/UK/BR one) and confirm ranking reflects region weighting rather than hard-excluding any of them.
- If running on `DATA_SOURCE=sheets`, confirm the **Partners** tab's live pull is actually current before trusting exclusion results — see §6.4.
- Confirm the **paired-row filter** works: seed a fixture with a blank-entity translation row directly beneath a real one, and confirm only the real row is read.
- Confirm the **missing-Status fallback**: with no `Status` column present, confirm the agent logs a warning and treats all Partners rows as Active, rather than failing or silently excluding nothing.
- Rename or reorder one non-required column in a test copy of a sheet/DB and confirm the column-name map (§6.0) surfaces a clear error (for a required field) or a warning (for an optional one) rather than misreading data silently.
- Exercise the **inactive backend** against mocked fixtures too, not just the active one — it shouldn't silently bit-rot before the eventual switch (§16 Phase 2).
- Run via `workflow_dispatch` into a **test channel** for a few days before pointing at the real channel and enabling the schedule.

---

## 15. v2 / future (out of scope now)

- **In-Slack actions:** interactive buttons on each candidate — `Add as competitor` / `Add as prospect` / `Ignore` — that write to the watchlist via its API. Requires a Slack app with an interactivity request URL (a small always-on endpoint), so ship v1 first.
- **`/track add [name]`** slash command for adding entities without opening the watchlist directly.
- **Weekly rollup** and **per-section channel routing**.
- **Feedback signal:** a 👍/👎 on items to tune the relevance bar over time.

---

## 16. Implementation phases (build order)

This is the build order, distinct from §7's runtime execution order — each phase depends only on the ones before it, and each ends with its own verification step so problems surface early rather than after everything's wired together.

**Phase 0 — Scaffolding**
Deliverables: repo skeleton (§5), dependency file, `config.py` (env var loading per §11), `.env.example`, README skeleton.
Verify: `config.py` loads all env vars against a filled-in `.env` without error; missing required vars fail loudly with a clear message.

**Phase 1 — Data models**
Deliverables: `models.py` — dataclasses for `Entity`, `NewsItem`, `Candidate`, `Digest`, `SourceData`, `Criteria`, matching §6 and §8 exactly.
Verify: instantiate each with a hand-written example matching the JSON contracts; no field mismatches.

**Phase 2 — Data source layer**
Deliverables: `sources/base.py` (the `SourceData` contract and column-name maps, §6.0), `sources/sheets_source.py`, and `sources/notion_source.py` — both reading Watchlist, Criteria, Industry Topics, and Partners (§6.1–6.4) into Phase 1's typed objects, building `excluded_names`, `reward_landscape`, and `industry_topics`.
Verify:
- **Sheets (active path):** point at the real workbook, print the loaded objects, manually confirm they match the sheet. Confirm the paired-row filter and missing-Status fallback (§6.4) both behave correctly. First good point to test the §6.2 criteria fallback/warning against a deliberately malformed section header.
- **Notion (dormant path):** live workspace access isn't available yet, so verify against **mocked fixtures** matching the Notion API's response shape — confirm `notion_source.py` produces a `SourceData` object identical in shape to what the Sheets path produces from equivalent fixture data. Re-verify live the moment NOMO grants integration access (§4 Path B).

**Phase 3 — Gather layer**
Deliverables: `gather.py` and `prompts/monitor.txt`, `prompts/industry.txt`, `prompts/scout.txt` — Stage 2(a)+(b) and Stage 3 (§7), with the web search tool config and `max_uses` split from §8.
Verify: run against a couple of real tracked entities and topics; confirm each call's JSON parses cleanly into Phase 1 models, and confirm a simulated per-call failure logs and continues rather than crashing the run.

**Phase 4 — Dedup & filter**
Deliverables: the Stage 4 fuzzy-match logic — candidates checked against `excluded_names` before ever reaching synthesis.
Verify: unit tests with known duplicate names, alias variants, and at least one Partners-source-only exclusion (no matching Watchlist row).

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