# NewsLens (personal prototype)

A memory-threaded daily news briefing with a single-narrator audio pass, built
for exactly one user. It pulls from outlets *you* name (RSS) plus one capped
discovery query per run, ranks the top 1–5 stories by world + personal impact,
threads continuity through a transparent, hand-editable memory, and labels
corroboration honestly (counts of distinct named outlets — never the word
"verified").

**Status: milestone 3 of 8** (ranking + corroboration). What exists: the
schema, the doctor, working tier-1 ingestion (`newslens ingest` — idempotent,
per-feed graceful degradation), and the editorial pass: `newslens rank`
clusters the day's items, selects the top 1–5 by world + personal impact
(bounded followed-analyst boost, 1-slot labeled urgency override, 14-day
recency window with an honesty line), attaches corroboration labels, and
writes the briefing row — instrumented in append-only `ranking_runs`. Narrative
text and audio are milestones 5–6; memory threading is milestone 4. The Sonar
discovery seam is built but cold (key deferred by choice). Spec:
`workspace/debates/2026-07-02--newslens--engineering.md` (§A–F); scope change:
**v1 is on-demand only** — no scheduled generation (DECISIONS.md 2026-07-03).

## Quickstart

```bash
cd workspace/products/newslens/prototype
python3 -m venv .venv && source .venv/bin/activate
pip install --upgrade pip          # stock 3.9 pip predates editable pyproject installs
pip install -e ".[dev]"
cp .env.example .env               # then fill keys in yourself — see SETUP.md
newslens migrate                   # creates data/newslens.db (idempotent)
scripts/doctor                     # or: newslens doctor
```

The doctor is the one command to run when anything seems off. Exit `0` means
everything required for a real run is in place; exit `1` means at least one
`✗` line above the summary tells you what to fix and how. Running it with no
keys and no sources is expected to exit `1` today — that is the honest state,
and every missing item comes with its fix.

## Commands (milestone 3)

| Command | What it does |
|---|---|
| `newslens migrate` | Create/upgrade `data/newslens.db`. Idempotent — safe to re-run any time. |
| `newslens doctor` / `scripts/doctor` | Health check: Python/deps, keys (validated with harmless read-only calls), schema, `sources.yaml` (tiers, disabled, reference-only), feed URLs, cost estimate. `scripts/doctor` works even before `pip install`. |
| `newslens ingest [--no-discovery]` | Pull all enabled sources into `source_items` (idempotent per UTC fetch-day), then the one capped Sonar discovery call if `PERPLEXITY_API_KEY` exists (RSS-only otherwise, and it says so). Partial feed failures degrade gracefully with a visible "N of M sources unavailable" line. |
| `newslens rank [--date YYYY-MM-DD]` | The editorial pass (M3): clusters items from the recency window (since your last briefing, 14-day cap — the report states plainly when ingested history is shorter), ranks the top 1-5 by world impact + your tags (topic match outweighs domain match; followed analysts get a bounded boost — better odds, never a guaranteed slot), applies the 1-slot urgency override with its unmissable label, corroboration-labels every story with the standing caveat, and writes the briefings row (prior version archived to history first). Needs `OPENAI_API_KEY`; budget-capped; real token cost logged. |
| `scripts/sonar_spike` | The pending Sonar reliability gate — one command the moment the key lands. Refuses politely without it. |

Coming later (deliberately not stubbed): `generate` (M5), `read`/`listen`
(M7 — these log the consumption events the day-30 falsifier is computed from;
v1 is on-demand only, so M7 is manual trigger + instrumentation, no cron).

## Environment variables & scopes

All integrations are plain API keys — no OAuth, no delegated scopes. The
"scope" decision here is *which vendors get a key at all* (narrowest-vendor
rule). You fill `.env` yourself; agents only ever touch `.env.example`.

| Var | Required | Why / scope |
|---|---|---|
| `OPENAI_API_KEY` | Yes | Text generation (GPT-4o-mini): ranking, narrative, script adaptation. Standard key, default permissions; set a hard spend cap in the OpenAI dashboard. **Audio (M6): Kokoro-82M local is the v1 default** — no key, no metered cost; gpt-4o-mini-tts on this same key is the built fallback, and the principal picks by ear at the milestone-6 listening test. This key is needed for text generation regardless. |
| `PERPLEXITY_API_KEY` | Yes | One capped Sonar discovery query per run. Pay-as-you-go; a prepaid credit cap in their dashboard is the primary spend limit. |
| `BUDGET_CAP_USD_PER_RUN` | Default 0.50 | In-app hard stop per generate run (ENGINEERING.md cost guardrail). |
| `GENERATE_HOUR_LOCAL` | Dormant | Nothing reads it in v1 (on-demand only, DECISIONS.md 2026-07-03). Kept optional in case scheduling ever returns; a set-but-invalid value still fails the doctor (typo'd .env is a config error). |
| `GNEWS_API_KEY` | No — leave blank | Fallback discovery vendor, deliberately ungranted unless the Sonar reliability spike fails. |

## What's real vs. faked

**Faked: nothing.** No mock data, no stubbed integrations, no
`// PROTOTYPE: faked` markers anywhere. External calls, all real and all
yours: the doctor's read-only key validations + feed resolution for enabled
sources, and `newslens ingest`'s feed GETs + the one capped Sonar call (only
when its key exists — keyless runs are RSS-only and say so; keyless +
nothing-enabled means zero network, period).

`sources.yaml` is **seeded with the principal's outlet list** (2026-07-03),
tiered and live-verified — see the file header. The no-defaults rule still
holds where it matters: interests are empty until the principal supplies tags
(discovery skips itself and says why), reference-only outlets are never
fetched, cautious aggregators are default-disabled, and an emptied file still
refuses politely rather than inventing sources.

## Ingestion contract (milestone 2 — binding, see `src/newslens/ingest.py`)

- **Fetch-day = UTC day.** `source_items` dedupes on `(url, UTC fetch-day)`;
  the boundary is midnight UTC, not your local midnight. A late-evening local
  run and next morning's run may re-snapshot the same URL on different
  fetch-days: understood behavior. (`briefings.date` stays principal-local —
  two clocks, on purpose.)
- **Idempotent:** same-UTC-day re-runs update snapshots in place, never
  duplicate; `fetched_at` is preserved on update.
- **Tiers are promises:** `headline_only` items carry titles/summaries with
  attribution + linkout downstream; `reference_only` outlets (NYT, Wikipedia,
  AP, Reuters) are structurally unfetchable; `cautious` aggregators are
  default-disabled and warned when enabled.
- **Degrades gracefully, visibly:** per-source failures never kill a run;
  the report prints "N of M sources unavailable this run: ..." naming each
  failure. A run fails outright only when *every* source fails.
- **No scraping:** feed-provided content only, tags stripped, excerpts
  truncated (1500 chars), max 20 items per feed per run.

## Data model (migrations 0001–0004)

| Table | Concern |
|---|---|
| `source_items` | Raw pulled content (RSS + Sonar), one row per (url, fetch-day); `wire_syndication_flag` feeds honest corroboration counting. |
| `briefings` | One row per day; story slots reference `source_items` ids (faithfulness by construction); `UNIQUE(date)` anchors idempotent re-runs. |
| `memory` | Tracked topics — `active`/`stale`/`dismissed` staleness policy (14-day auto-stale, principal-only dismissal); syncs to a hand-editable `memory.md` at milestone 4. |
| `briefings_history` | Append-only log of superseded briefing versions (UPDATE/DELETE abort via triggers) so a re-run can never destroy yesterday's output. |
| `ranking_runs` | Append-only instrumentation (UPDATE/DELETE abort via triggers since 0004): one row per rank attempt incl. failures — override fired/pool, repairs, cost; feeds the day-14 recalibration readout. |

Timestamps are UTC ISO-8601 text; `briefings.date` is your local calendar day,
format-enforced (`YYYY-MM-DD`) by triggers since migration 0002. Rationale:
`adr/0001-schema-three-tables-plus-history.md`, `adr/0003-m2-ingestion-decisions.md`,
`adr/0004-m3-ranking-decisions.md`.

## Repo layout

```
migrations/          numbered .sql files; IF NOT EXISTS everywhere (re-apply safe)
prompts/             every LLM-facing prompt is a versioned file, never inline
scripts/doctor       health check; works pre-install (stdlib-only bootstrap)
scripts/sonar_spike  the pending Sonar reliability gate (needs the key)
src/newslens/        paths, db (stdlib-only), config, net, ingest, discovery, ranking, doctor, cli
sources.yaml         the principal's tiered outlet list + interests (seeded M2)
data/                (gitignored) SQLite DB and generated artifacts
adr/                 one short file per significant technical decision
NOTES-M2.md          living carryover file between milestones
tests/               QA-owned; run with: pytest
```

## Tests

`pytest` (installed via the `[dev]` extra). The suite is QA-owned per
`team/ENGINEERING.md`. Milestone 2 adds to the contract: ingestion is
idempotent per UTC fetch-day and degrades gracefully with the visible
"N of M sources unavailable" line (kill-3-feeds case, spec §E M2); tiers
behave (`reference_only` structurally unfetchable, `cautious`
default-disabled); keyless discovery builds no request; migration 0002's
date-format triggers reject malformed `briefings.date`.

## Docs

- `SETUP.md` — clone → keys → first doctor pass, step by step
- `adr/` — decision records (schema, doctor design)
- Spec: `workspace/debates/2026-07-02--newslens--engineering.md`
