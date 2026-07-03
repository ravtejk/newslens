# NewsLens (personal prototype)

A memory-threaded daily news briefing with a single-narrator audio pass, built
for exactly one user. It pulls from outlets *you* name (RSS) plus one capped
discovery query per run, ranks the top 1–5 stories by world + personal impact,
threads continuity through a transparent, hand-editable memory, and labels
corroboration honestly (counts of distinct named outlets — never the word
"verified").

**Status: milestone 1 of 8** (skeleton + doctor). There is no pipeline yet —
what exists today is the schema, the config surfaces, and a doctor that tells
you exactly what's missing and how to fix it. Spec:
`workspace/debates/2026-07-02--newslens--engineering.md` (§A–F).

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

## Commands (milestone 1)

| Command | What it does |
|---|---|
| `newslens migrate` | Create/upgrade `data/newslens.db`. Idempotent — safe to re-run any time. |
| `newslens doctor` / `scripts/doctor` | Health check: Python/deps, keys (validated with harmless read-only calls), schema, `sources.yaml`, feed URLs, cost estimate. `scripts/doctor` works even before `pip install`. |

Coming later (deliberately not stubbed): `generate` (M5), `read`/`listen`
(M7 — these log the consumption events the day-30 falsifier is computed from).

## Environment variables & scopes

All integrations are plain API keys — no OAuth, no delegated scopes. The
"scope" decision here is *which vendors get a key at all* (narrowest-vendor
rule). You fill `.env` yourself; agents only ever touch `.env.example`.

| Var | Required | Why / scope |
|---|---|---|
| `OPENAI_API_KEY` | Yes | Text generation (GPT-4o-mini): ranking, narrative, script adaptation. Standard key, default permissions; set a hard spend cap in the OpenAI dashboard. **TTS backend is TBD at milestone 6** (local/open-source vs hosted under re-evaluation) — audio may need no key or a different vendor's key; this key is needed for text generation regardless. |
| `PERPLEXITY_API_KEY` | Yes | One capped Sonar discovery query per run. Pay-as-you-go; a prepaid credit cap in their dashboard is the primary spend limit. |
| `BUDGET_CAP_USD_PER_RUN` | Default 0.50 | In-app hard stop per generate run (ENGINEERING.md cost guardrail). |
| `GENERATE_HOUR_LOCAL` | Default 6 | Local hour the daily run fires (wired up at milestone 7). |
| `GNEWS_API_KEY` | No — leave blank | Fallback discovery vendor, deliberately ungranted unless the Sonar reliability spike fails. |

## What's real vs. faked

**Faked: nothing.** No mock data, no stubbed integrations, no
`// PROTOTYPE: faked` markers anywhere. The only external calls in milestone 1
live in the doctor and only fire for things you've configured: a read-only
OpenAI `GET /v1/models`, a minimal Sonar query (~a fraction of a cent — its
prompt is versioned at `prompts/doctor_sonar_ping.txt`), and a `GET` of each
RSS feed you've actively added to `sources.yaml`. With no keys and no sources
configured, the doctor makes no network calls at all.

`sources.yaml` ships with **zero active sources and zero interests** on
purpose (principal decision, 2026-07-02): the pipeline refuses politely rather
than ever using outlets you didn't choose.

## Data model (migration 0001)

| Table | Concern |
|---|---|
| `source_items` | Raw pulled content (RSS + Sonar), one row per (url, fetch-day); `wire_syndication_flag` feeds honest corroboration counting. |
| `briefings` | One row per day; story slots reference `source_items` ids (faithfulness by construction); `UNIQUE(date)` anchors idempotent re-runs. |
| `memory` | Tracked topics — `active`/`stale`/`dismissed` staleness policy (14-day auto-stale, principal-only dismissal); syncs to a hand-editable `memory.md` at milestone 4. |
| `briefings_history` | Append-only log of superseded briefing versions (UPDATE/DELETE abort via triggers) so a re-run can never destroy yesterday's output. |

Timestamps are UTC ISO-8601 text; `briefings.date` is your local calendar day.
Rationale: `adr/0001-schema-three-tables-plus-history.md`.

## Repo layout

```
migrations/        numbered .sql files; IF NOT EXISTS everywhere (re-apply safe)
prompts/           every LLM-facing prompt is a versioned file, never an inline string
scripts/doctor     health check; works pre-install (stdlib-only bootstrap)
src/newslens/      paths, db (stdlib-only), config, doctor, cli
sources.yaml       YOUR outlets + interests — ships as a documented template
data/              (gitignored) SQLite DB and generated artifacts
adr/               one short file per significant technical decision
tests/             QA-owned; run with: pytest
```

## Tests

`pytest` (installed via the `[dev]` extra). The suite is QA-owned per
`team/ENGINEERING.md`; milestone 1's contract for QA: migrations are
idempotent, `briefings_history` rejects UPDATE/DELETE, `load_sources` handles
template/valid/malformed files, doctor exits 1 with missing keys and 0 when
everything required passes.

## Docs

- `SETUP.md` — clone → keys → first doctor pass, step by step
- `adr/` — decision records (schema, doctor design)
- Spec: `workspace/debates/2026-07-02--newslens--engineering.md`
