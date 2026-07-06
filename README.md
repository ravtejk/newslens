# NewsLens (personal prototype)

A memory-threaded daily news briefing with a single-narrator audio pass, built
for exactly one user. It pulls from outlets *you* name (RSS) plus one capped
discovery query per run, ranks the top 1–5 stories by world + personal impact,
threads continuity through a transparent, hand-editable memory, and labels
corroboration honestly (counts of distinct named outlets — never the word
"verified").

**Status: milestone 8 of 8 — construction complete; the usage window is
running.** The full path works end to end: `newslens generate` produces the
day's tiered briefing and voices it; `newslens serve` is the daily surface;
`newslens diagnose` is the self-caveating readout the day-14/day-30
verdicts will read; `PREFLIGHT.md` is the human engineer's review guide
(org law: model-reviewed-model needs human eyes before anything public).
**In construction: M9 "the Analyst"** (approved 2026-07-06) — milestone 1
landed the retrieval leg (tier-scoped, robots-respecting, attributed,
single-user-paced fetch with per-fetch instrumentation); milestone 2 landed
the organ itself: `newslens analyze` produces one cited analysis brief per
depth-tier story (fetch + Sonar verification + gpt-4o synthesis), validated
deterministically — fabricated citations hard-reject, quotes must be
verbatim substrings, provenance tiers and source tables are code-computed,
own-voice inference is dropped structurally (borrowed-inference rule) —
and persisted to `analysis_briefs` (migration 0008). Reader copy says
"cited," never "verified." Milestone 3 closed the loop: the writer
writes FROM the brief (trace-don't-generate; the analyst's slot-3 tier call
binds), and every depth story with a valid brief carries "→ The full
picture" — the deep view: 8 sections, typography-carried provenance,
"cited" never "verified", back-nav restoring your exact story position.
Cap $0.25/run per the ruling; measured full run incl. analysis: ~$0.12.
What
exists: the schema, the doctor, working tier-1 ingestion (`newslens ingest` —
idempotent, per-feed graceful degradation), the editorial pass (`newslens
rank` — clustering, top 1–5 by world + personal impact, bounded
followed-analyst boost, 1-slot labeled urgency override, recency window with
an honesty line, corroboration labels, append-only `ranking_runs`
instrumentation), and live memory: threads seeded from the taxonomy contract,
matched threads scoring at full personal weight and recording their
referencing briefing, the three-state lifecycle (`active` / `dormant` /
`dismissed_user`, ADR-0006) with 14-day dormancy and earned-slot
auto-revival, and the hand-editable `memory.md` two-way sync (file wins,
loudly). Narrative text and audio are milestones 5–6. The Sonar discovery
seam is LIVE as of 2026-07-06 (key granted post-construction; reliability
spike passed 5/5 — see the build log). New at M5 (as
amended by the principal's editorial review, contract §A1-A6):
`newslens generate` — the end-to-end on-demand briefing (ingest -> rank ->
narrative -> script) per the Content Lead's contract + amendments
(`workspace/debates/2026-07-05--newslens--content.md` §5 + the A1-A6
review): TIERED stories (one full-depth lead, tight-medium second/third,
quick hits for the rest — lead-heavy by design), voice A only (B retired;
the briefing's own voice never predicts — forward-looking claims are
attributed or absent; no methodology self-reference), concreteness rules
(specifics from sources, truisms banned, no moralization — show, don't
label), the intro formula (what happened + why you care + what's uncertain,
then the dateline), two-lane source rule, code-owned trust furniture,
delta-only continuity callbacks with mandatory text disclosures, and a
spoken pass under the hard fact-subset/hedge rules with editorial license
over script attribution (A5). Selection runs on tags + world impact only —
threads are recorded and woven into continuity, never steering
(`settings.threads_steer_selection`, A6). Audio ships at M6: `generate` ends by voicing the
script — Kokoro-82M locally by default (free; isolated engine env via
`scripts/setup_tts`; measured ~4.4x realtime on this machine — below the
community 14x floor, flagged at the ear test) or gpt-4o-mini-tts
(~$0.015/min) via `settings.tts_engine`; a GPT-4o **editor pass** tightens
every draft (cut/concretize only, never adds facts, fully re-validated,
disclosed in the run log) before validation. Spec:
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

## Commands (milestone 8 — final)

| Command | What it does |
|---|---|
| `newslens migrate` | Create/upgrade `data/newslens.db`. Idempotent — safe to re-run any time. |
| `newslens diagnose` | **The readout (M8).** Read-only, offline, $0: the day-30 falsifier (trailing-14-day distinct open days, construction traffic flagged) with its three recorded caveats printed alongside, plus the generation record — tiers, framings, override rate, editor tightening + hedge warns, disclosure buckets, cost totals. The day-14 diagnostic runs exactly this. |
| `newslens serve [--port 8484]` | **The UI (M7).** Local web app at `http://127.0.0.1:8484/` — localhost-only by design. Today (tiered stories, tap-away generation details, play-the-episode, per-story follow), Following (ongoing threads with edit-note/stop/resume/delete, topic and writer editors that round-trip `sources.yaml`), Archive (every edition, tap to reopen). Regenerate lives in Settings. Page views and episode plays land in `consumption_events` (the day-30 falsifier's data — see ADR-0010); thread verbs share the CLI's exact code path. stdlib only, no build step, dies with the terminal. |
| `newslens doctor` / `scripts/doctor` | Health check: Python/deps, keys (validated with harmless read-only calls), schema, `sources.yaml` (tiers, disabled, reference-only), feed URLs, cost estimate. `scripts/doctor` works even before `pip install`. |
| `newslens ingest [--no-discovery]` | Pull all enabled sources into `source_items` (idempotent per UTC fetch-day), then the one capped Sonar discovery call if `PERPLEXITY_API_KEY` exists (RSS-only otherwise, and it says so). Partial feed failures degrade gracefully with a visible "N of M sources unavailable" line. |
| `newslens rank [--date YYYY-MM-DD]` | The editorial pass: clusters items from the recency window (since your last briefing, 14-day cap — the report states plainly when ingested history is shorter), ranks the top 1-5 by world impact + your tags and live threads (topic/thread match outweighs domain match; followed analysts get a bounded boost — better odds, never a guaranteed slot), applies the 1-slot urgency override with its unmissable label, corroboration-labels every story with the standing caveat, writes the briefings row (prior version archived to history first), records thread references, and applies earned-slot auto-revival of dormant threads (dated, disclosed). Needs `OPENAI_API_KEY`; budget-capped; real token cost logged. |
| `newslens memory list [--status active\|dormant\|dismissed_user\|all]` | Show the live threads with notes, states, and last-referenced dates. Same data as `memory.md`. |
| `newslens memory add "<topic>" [--note "..."]` | Start tracking a thread (revives it if dormant/dismissed — explicit revival resets the dormancy clock). |
| `newslens memory dismiss "<topic>"` | Stop tracking — stays visible in `memory.md`, never auto-revives. |
| `newslens memory note "<topic>" "<text>"` | Set the note the generation prompt reads verbatim — the explicit "more/less like this" mechanism. |
| `newslens generate [--date] [--variant A\|B] [--no-refresh] [--no-threads]` | The whole product, on demand: chains ingest -> rank, writes the tiered narrative (lead full / second medium / rest quick hits) in **voice A — the voice of record** (editorial review A1; alternation ended), adapts it into a podcast script (fact-subset validated), persists both onto the briefing row (prior narrative archived first), renders to stdout + `data/briefings/<date>.md`, logs per-step real costs (incl. per-story tiers) to `briefings.token_cost` and `data/generation_log.jsonl`. All three LLM calls run on **GPT-4o** (writer up-tier: principal, 2026-07-05, register-holding trigger; ranking up-tier: CoS recommendation same day after loose semantic matches — 4o-mini stays as the documented fallback rung on both seams); expect ~$0.10–0.14/full text pipeline (rank + writer + editor); audio adds $0 (kokoro) or ~$0.015/min (openai). `--variant B` renders the retired voice as a labeled comparison SAMPLE; `--no-threads` renders the cold-start view (threads emptied, tags kept) as a labeled SAMPLE — **samples always skip the refresh chain, so the briefing of record is never touched by a sample request**. `--no-refresh` skips the chain for narrative-only iteration on the record. |
| `scripts/sonar_spike` | The Sonar reliability gate — **passed live 2026-07-06** (5/5 probes, 2.9–6.6s, 9–10 search_results each, $0.0043). Re-runnable anytime; refuses politely without the key. |

Coming later (deliberately not stubbed): `read`/`listen`
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
| `BUDGET_CAP_USD_PER_RUN` | Default 0.25 (M9 ruling 2026-07-06; was 0.50) | In-app hard stop per generate run (ENGINEERING.md cost guardrail). Degradation ladder: cheapest inputs first, content protected longest; routine derating at 0.25 escalates to the principal, never absorbed. |
| `GENERATE_HOUR_LOCAL` | Dormant | Nothing reads it in v1 (on-demand only, DECISIONS.md 2026-07-03). Kept optional in case scheduling ever returns; a set-but-invalid value still fails the doctor (typo'd .env is a config error). |
| `GNEWS_API_KEY` | No — leave blank | Fallback discovery vendor, deliberately ungranted unless the Sonar reliability spike fails. |

## What's real vs. faked

**Faked: nothing.** No mock data, no stubbed integrations, no
`// PROTOTYPE: faked` markers anywhere. External calls, all real and all
yours: the doctor's read-only key validations + feed resolution for enabled
sources, `newslens ingest`'s feed GETs + the one capped Sonar call (only
when its key exists — keyless runs are RSS-only and say so; keyless +
nothing-enabled means zero network, period), and `newslens rank`'s one
budget-capped OpenAI chat call per run — the product's only paid LLM call,
real token cost logged to `briefings.token_cost` and `ranking_runs`.

`sources.yaml` is **seeded with the principal's outlet list** (2026-07-03),
tiered and live-verified — see the file header; interests carry the
principal's 59-tag taxonomy (seeded M3, principal-blessed 2026-07-04). The
no-defaults rule still holds where it matters: nothing was invented — every
outlet and tag traces to the principal's own lists; reference-only outlets
are never fetched, cautious aggregators are default-disabled, and an emptied
file still refuses politely rather than inventing sources.

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

## Data model (migrations 0001–0006)

| Table | Concern |
|---|---|
| `source_items` | Raw pulled content (RSS + Sonar), one row per (url, fetch-day); `wire_syndication_flag` feeds honest corroboration counting. |
| `briefings` | One row per day; story slots reference `source_items` ids (faithfulness by construction); `UNIQUE(date)` anchors idempotent re-runs (re-rank archives the prior version and NULLs stale narrative fields). |
| `memory` | Live threads, lifecycle v2 (rebuilt in 0006): `active` / `dormant` (14-day unreferenced, auto-revives when a slot-earning story matches) / `dismissed_user` (never auto-revives); unique topics (0005); `status_changed_at` dates every transition; two-way synced with hand-editable `memory.md` (file wins, loudly). |
| `briefings_history` | Append-only log of superseded briefing versions (UPDATE/DELETE abort via triggers) so a re-run can never destroy yesterday's output. |
| `ranking_runs` | Append-only instrumentation (UPDATE/DELETE abort via triggers since 0004): one row per rank attempt incl. failures — override fired/pool, repairs, revivals, cost; feeds the day-14 recalibration readout. |

Timestamps are UTC ISO-8601 text; `briefings.date` is your local calendar day,
format-enforced (`YYYY-MM-DD`) by triggers since migration 0002. Rationale:
`adr/0001-schema-three-tables-plus-history.md`, `adr/0003-m2-ingestion-decisions.md`,
`adr/0004-m3-ranking-decisions.md`, `adr/0006-memory-lifecycle-v2.md`.

## Repo layout

```
migrations/          numbered .sql files; IF NOT EXISTS everywhere (re-apply safe)
prompts/             every LLM-facing prompt is a versioned file, never inline
scripts/doctor       health check; works pre-install (stdlib-only bootstrap)
scripts/sonar_spike  the Sonar reliability gate (passed 2026-07-06; re-runnable)
src/newslens/        paths, db (stdlib-only), config, net, ingest, discovery, ranking, memory, doctor, cli
sources.yaml         the principal's tiered outlet list + interests (seeded M2)
memory.md            (gitignored) the hand-editable live-threads surface
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
