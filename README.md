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
instrumentation), and live memory: threads you follow explicitly (the M4
first-run taxonomy bootstrap was **killed at Stage-0 M1** — a new reader's
memory starts genuinely empty; ADR-0019 §2.5),
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
script — Kokoro-82M locally by default (NL-96, the $0-run law 2026-07-25: an
unstated engine fails cheap, never paid; isolated engine env via
`scripts/setup_tts`; measured ~4.4x realtime on this machine — below the
community 14x floor, on record, and that re-open is live again now that
kokoro is the default) or gpt-4o-mini-tts by explicit pin (~$0.015/min on the
OpenAI key; the principal's ear-test pick, ruling 2026-07-06 — a VOICE ruling
that stands, superseded on SPEND only), both via
`settings.tts_engine`; a GPT-4o **editor pass** tightens
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
| `newslens migrate [--all-profiles]` | Create/upgrade `data/newslens.db`. Idempotent — safe to re-run any time. `--all-profiles` upgrades every profile's database (Stage-0 M1). |
| `newslens --profile <name> <any verb>` | **Stage-0 M1 — the profile dimension.** Run any verb as a different reader. `default` (the flag's default) is *your* world: `data/`, `memory.md`, `sources.yaml` exactly where they have always been, unmoved — adding profiles moves none of your files. Every other profile owns its own database, corpus, artifacts, spend log, `memory.md` and `sources.yaml` under `profiles/<name>/`; nothing is shared and nothing is inherited (`.env` is the one deliberate exception — keys are machine credentials, not reader state). A profile's state sits behind the **same** real-paths guard as yours: it is real state, never a sandbox redirection. An unknown name is refused, never created. `NEWSLENS_PROFILE=<name>` sets the same thing for a shell or a launchd job; the flag wins over the variable. |
| `newslens profile create <name>` | Provision a new reader: fresh fully-migrated database, a **0-byte `memory.md`**, its own directories, and its own copy of the committed source catalog (`templates/profile-sources.yaml`) with the **interests block empty**. Seeds nothing and copies no other reader's state — so `rank` refuses by name until that reader picks their own interest tags (that choosing is the Stage-0 Commissioning). Refuses over an existing profile, and refuses `default`. |
| `newslens profile list` | Every profile with honest status: schema up to date or N pending, active thread count, `memory.md` size + sync generation, whether interests are set (i.e. whether that profile has been commissioned yet). `*` marks the active profile. |
| `newslens diagnose` | **The readout (M8).** Read-only, offline, $0: the day-30 falsifier (trailing-14-day distinct open days, construction traffic flagged) with its three recorded caveats printed alongside, plus the generation record — tiers, framings, override rate, editor tightening + hedge warns, disclosure buckets, cost totals. The day-14 diagnostic runs exactly this. |
| `newslens serve [--port 8484]` | **The UI (M7).** Local web app at `http://127.0.0.1:8484/` — localhost-only by design. Today (tiered stories, tap-away generation details, play-the-episode, per-story follow), Following (ongoing threads with edit-note/stop/resume/delete, topic and writer editors that round-trip `sources.yaml`), Archive (every edition, tap to reopen). Regenerate lives in Settings. Page views and episode plays land in `consumption_events` (the day-30 falsifier's data — see ADR-0010); thread verbs share the CLI's exact code path. stdlib only, no build step, dies with the terminal. |
| `newslens doctor` / `scripts/doctor` | Health check: Python/deps, keys (validated with harmless read-only calls), schema, `sources.yaml` (tiers, disabled, reference-only), feed URLs, cost estimate. `scripts/doctor` works even before `pip install`. |
| `newslens ingest [--no-discovery]` | Pull all enabled sources into `source_items` (idempotent per UTC fetch-day). **Tier-2 Sonar discovery is PAUSED by ruling (2026-07-25)** — runs are RSS-only and say so, and no metered call is made even with `PERPLEXITY_API_KEY` set (`--no-discovery` is therefore redundant today). Partial feed failures degrade gracefully with a visible "N of M sources unavailable" line. |
| `newslens rank [--date YYYY-MM-DD]` | The editorial pass: clusters items from the recency window (since your last briefing, 14-day cap — the report states plainly when ingested history is shorter), ranks the top 1-5 by world impact + your tags and live threads (topic/thread match outweighs domain match; followed analysts get a bounded boost — better odds, never a guaranteed slot), applies the 1-slot urgency override with its unmissable label, corroboration-labels every story with the standing caveat, writes the briefings row (prior version archived to history first), records thread references, and applies earned-slot auto-revival of dormant threads (dated, disclosed). Runs on the **`rank` seat** — Claude Haiku 4.5 on the `claude -p` SUBSCRIPTION lane (B3), so it needs the logged-in Claude CLI, **not** `OPENAI_API_KEY`; budget-capped on `usd_shadow`; real token cost logged. |
| `newslens memory list [--status active\|dormant\|dismissed_user\|all]` | Show the live threads with notes, states, and last-referenced dates. Same data as `memory.md`. |
| `newslens memory add "<topic>" [--note "..."]` | Start tracking a thread (revives it if dormant/dismissed — explicit revival resets the dormancy clock). For a cold-start thread (empty ledger) this also QUEUES a cold-start backgrounder — a `pending` baseline intent, $0, no LLM (NL-77, §F explicit action); materialize it with `memory-baseline`. |
| `newslens memory dismiss "<topic>"` | Stop tracking — stays visible in `memory.md`, never auto-revives. |
| `newslens memory note "<topic>" "<text>"` | Set the note the generation prompt reads verbatim — the explicit "more/less like this" mechanism. |
| `newslens memory sync [--accept-file]` | **NL-81, the sync resurrection guard.** Reconcile `memory.md` against the database and rewrite it. Every memory verb already does this first; naming it gives the guard's refusal an exit to point at. A `memory.md` that is not the file this database last wrote — a restored backup, a reconstruction, a copy from another profile — is **refused, changing nothing on either side**, with a disclosure of exactly what it would have done. `--accept-file` is your explicit file-wins override: it overrides staleness only, so threads you deleted or renamed are *still* not resurrected (bring one back deliberately with `memory add "<topic>"`), and dismissals it infers are attributed to the file, not to you. Recency is a generation stamp in the file header, **never mtime** — the 2026-07-17 poisoned reconstruction was the newest file on the machine. Embedded syncs (a `rank`/`generate` run, the web UI's thread verbs) *degrade* instead of refusing: they skip the import, run on database state, and warn loudly, because a stale file must never kill the morning edition. |
| `newslens generate [--date] [--variant A\|B] [--no-refresh] [--no-threads]` | The whole product, on demand: chains ingest -> rank, writes the tiered narrative (lead full / second medium / rest quick hits) in **voice A — the voice of record** (editorial review A1; alternation ended), adapts it into a podcast script (fact-subset validated), persists both onto the briefing row (prior narrative archived first), renders to stdout + `data/briefings/<date>.md`, logs per-step real costs (incl. per-story tiers) to `briefings.token_cost` and `data/generation_log.jsonl`. The pipeline's LLM calls run on the **`llm.SEATS` roster**, not on GPT-4o: the writer is Claude Opus 4.8, the analyst Claude Sonnet 5, and rank/editor/script/state Claude Haiku 4.5 — **all six on the `claude -p` SUBSCRIPTION lane** since B3/item C, with the API lane as each seat's registered fall-over. `synthesis` is the only gpt-4o seat left and it has no live call site. Consequence for money: a default edition's CHARGED cost is **~$0** (the subscription covers it; `usd_charged` is 0.00 on that lane), while the SHADOW — API-equivalent compute, which is what the budget cap actually guards — is ~$0.90–1.30/edition. Audio adds $0 (kokoro, the default) or ~$0.015/min (openai). The 2026-07-05 GPT-4o up-tier rulings are history, superseded by B2/B3/B4. `--variant B` renders the retired voice as a labeled comparison SAMPLE; `--no-threads` renders the cold-start view (threads emptied, tags kept) as a labeled SAMPLE — **samples always skip the refresh chain, so the briefing of record is never touched by a sample request**. `--no-refresh` skips the chain for narrative-only iteration on the record. |
| `newslens memory-backfill --date YYYY-MM-DD` | Writes the NL-63 memory pass (delta ledger + standing state) for an already-PUBLISHED edition that missed it — context reconstructed from persisted rows only (byte-identical to the live pass), idempotent, refuses (never fabricates) on unpublished/unrecoverable editions, cap pre-checked, state-rewrite spend folded into `briefings.token_cost` without re-archiving. Money-touching (state rewrites ~$0.01–0.03/thread). |
| `newslens memory close "<topic>" [--reason "..."]` | Records a dated closure fact for a thread that reached its natural end (migration 0015; explicit-action lane, §F). Refuses a re-close — **the row is permanent** (append-only ledger, structurally one closure per thread), so the reason text is written once, forever. Records-only today: status and deltas are untouched; the behavior flips (dated line on the thread page, halting further deltas) ship only with the future closure feature. $0. |
| `newslens memory-repair-state (--thread-id N \| --all)` | NL-73: rewrites standing state for threads whose latest live delta postdates their state — full-ledger regeneration, stamped at the latest live delta's date. Refuses when nothing is stale; single selector enforced. Money-touching (~$0.01–0.03/stale thread); no dry-run — cap pre-checked per rewrite, mid-sweep budget exhaustion skips cleanly with spend disclosed. |
| `newslens memory-baseline (--thread-id N \| --all) [--date YYYY-MM-DD]` | NL-77: writes the cold-start **backgrounder** (entry-zero baseline — "How we got here") for followed threads with an EMPTY ledger. One analyst-model call each (GPT-4o pointed backwards, ~$0.01–0.02, marked `external-synthesis`, cite currency `(baseline, <date>)`), validated (rejects bare continuity diction → an honest `failed` row, never fabricated), spend durable on the `thread_baselines` row. Cap pre-checked; single selector enforced; refuses when nothing awaits. **The `--all` retroactive sweep is a principal checkpoint — thread renames/deletes (the junk sweep) land first.** |
| `scripts/sonar_spike [--live] [N]` | The Sonar **liveness** gate — passed live 2026-07-06 (5/5 probes, 2.9–6.6s, 9–10 `search_results` each, $0.0043). **Read that result correctly: it measured latency, error shape and result PRESENCE — never whether the results were stories** (the misreading that let NL-101 live for three weeks). **Dry run by default** (NL-97): a bare invocation prints the plan, the per-call and worst-case estimate, and the budget cap, and touches no socket. `--live` makes the paid calls and is refused while discovery is paused. |
| `scripts/battery [--date] [--arms] [--lanes] [--run]` | The writer-register **model** battery (B4): one narrative artifact per model arm off the same variant-A prompt, for a date that already has a briefing row. Read-only on the record; artifacts under `data/battery/<date>/<model>__<lane>/`. **Dry-run by default** — `--run` makes the live calls, bounded by `BUDGET_CAP_USD_PER_RUN` (CHARGED dollars). Refuses a models×lanes grid (confound guard). |
| `scripts/moat-battery {plan\|t1\|t2\|t3\|pack} [--run]` | The NL-75 **Phase-2 moat battery**: `t1` prose-first vs sectioned retro-pairs, `t2` the expression-ablation 2×2 (ledger-context on/off × form), `t3` the Concept B input pack ($0 — never makes an LLM call), `pack` the shuffled blind pack + sealed key, `plan` the whole-session cost disclosure. ONE writer model, held fixed — this is not a model A/B (that's `scripts/battery`), so there is deliberately no `--arms`. Read-only on the record; artifacts under `data/battery/<session>/phase2/`. **Dry-run by default**; the cap binds CHARGED dollars **per invocation**, so `plan`'s session total is the number to check against a spend authorisation. A 2×2 on an edition with no ledger content is BLOCKED as degenerate rather than spent. |

Coming later (deliberately not stubbed): `read`/`listen`
(M7 — these log the consumption events the day-30 falsifier is computed from;
v1 is on-demand only, so M7 is manual trigger + instrumentation, no cron).

## Environment variables & scopes

All integrations are plain API keys — no OAuth, no delegated scopes. The
"scope" decision here is *which vendors get a key at all* (narrowest-vendor
rule). You fill `.env` yourself; agents only ever touch `.env.example`.

| Var | Required | Why / scope |
|---|---|---|
| `OPENAI_API_KEY` | Yes | Text generation on GPT-4o: the narrative (writer), analyst, and synthesis seats (ranking, editorial-tighten, and TTS-script moved to the Claude lane in the B2 depth flip, 2026-07-16 — see `ANTHROPIC_API_KEY`). Standard key, default permissions; set a hard spend cap in the OpenAI dashboard. **Audio: this key is NOT charged by default** — NL-96 (the $0-run law, 2026-07-25) made Kokoro-82M local the default voice, so an unstated engine fails cheap. gpt-4o-mini-tts on this same key stays fully built and is the principal's ear-test pick (ruling 2026-07-06 — a voice ruling that stands); it costs ~$0.015/min, ~+$0.07/run once pinned with `settings.tts_engine: openai`. This key is needed for text generation regardless. |
| `ANTHROPIC_API_KEY` | Fall-over only | The Claude **API** lane credential (Claude Haiku 4.5). **B3 (2026-07-16) flipped the ranking, editorial-tighten, and TTS-script seats to the `claude -p` SUBSCRIPTION lane by default** — they ride your Claude subscription (usd_charged $0.00; usd_shadow still ledgered), so the default path needs the CLI installed + logged in, NOT this key (see the Claude CLI row + SETUP.md §2c). The key is required only when a seat runs the API lane: you pin `NEWSLENS_LANE_<SEAT>=api`, or you arm `NEWSLENS_LANE_FALLBACK=api`. **Set a hard monthly cap in the Anthropic console** if you use it. Rollback: flip these seats' lane back to `api` (or GPT-4o) in `llm.py` — one diff. |
| Claude CLI (`claude`) | Yes (subscription lane) | The subscription lane's transport: `rank`/`editor`/`script` default to `claude -p` against your logged-in CLI. Resolution: `NEWSLENS_CLAUDE_BIN` → `PATH` → `~/.local/bin/claude` (the doctor reports which resolved and the version). Grant it by installing the CLI and running `claude` once to log in — NewsLens never handles your credentials; the subprocess strips `ANTHROPIC_API_KEY`, disables all tools + CLAUDE.md/skills/plugins/hooks/MCP, and runs in an empty scratch dir. A missing/unauthed CLI FAILs the run naming the fix (never a silent API call). |
| `PERPLEXITY_API_KEY` | **No — discovery is paused** | Tier-2 discovery is PAUSED by ruling (2026-07-25; 0.41% lifetime citation contribution), so no discovery call is made and the doctor reports the pause instead of asking for the key. **One caller is still live and still metered when this key is set:** analysis VERIFICATION, one Sonar call per depth-tier story on `analyze`/`generate` (~$0.003/edition measured 2026-07-25). Comment the key out of `.env` and every Sonar path in the product is cold. Pay-as-you-go; a prepaid credit cap in their dashboard is the primary spend limit. |
| `BUDGET_CAP_USD_PER_RUN` | Default 0.25 (M9 ruling 2026-07-06; was 0.50) | In-app hard stop per generate run (ENGINEERING.md cost guardrail). Degradation ladder: cheapest inputs first, content protected longest; routine derating at 0.25 escalates to the principal, never absorbed. |
| `GENERATE_HOUR_LOCAL` | Dormant | Nothing reads it in v1 (on-demand only, DECISIONS.md 2026-07-03). Kept optional in case scheduling ever returns; a set-but-invalid value still fails the doctor (typo'd .env is a config error). |
| `GNEWS_API_KEY` | No — leave blank | Fallback discovery vendor, deliberately ungranted unless the Sonar reliability spike fails. |
| `NEWSLENS_REAL_DATA` | No — safety override, not a credential | The real-paths guard's explicit opt-in (incident 2026-07-14: an ad-hoc probe script clobbered the real `generation_log.jsonl` through `paths.DATA_DIR`). `DATA_DIR`/`DB_PATH` refuse to resolve outside the real entrypoints (`newslens …`, `scripts/doctor`); set `NEWSLENS_REAL_DATA=1` only for a deliberate one-off ad-hoc use — the setting is transcript-greppable by design. (The guard's former "under pytest" arm is gone — children spawned by tests inherit `PYTEST_CURRENT_TEST`, which silently sanctioned the QA suite's doctor child against the real `data/`; v7-M1 pinhole, 2026-07-14.) LIMIT: a script hardcoding the `data/...` path string bypasses the guard; the "no real-state writes during probing" rule (ENGINEERING.md, 2026-07-07) remains law. |
| `NEWSLENS_DATA_DIR` / `NEWSLENS_DB_PATH` | No — sandbox redirection, not a credential | Resolve `paths.DATA_DIR`/`paths.DB_PATH` to the given location instead of the checkout's real `data/` — redirection outranks sanction, so no real-data opt-in is involved. This is the only sandbox that crosses a process boundary: the QA suite exports per-test values so every child it spawns (doctor, CLI) lands in the test sandbox (v7-M1 pinhole fix, 2026-07-14). `NEWSLENS_DB_PATH` defaults to `<NEWSLENS_DATA_DIR>/newslens.db` when only the dir is set. **Seam completed 2026-07-16** (after a sandboxed probe rewrote the real `memory.md` through the un-seamed `MEMORY_FILE` path — second pinhole-class instance): `NEWSLENS_SOURCES_FILE` / `NEWSLENS_ENV_FILE` / `NEWSLENS_MEMORY_FILE` redirect the principal-owned files the same way; all five paths sit behind one guard. |
| `NEWSLENS_PROFILE` | No — a selector, not a credential | **Stage-0 M1.** Which reader's world every verb operates on; same thing `--profile` sets, and the flag wins. Unset (or `default`) = your own world, resolved exactly as it was before profiles existed. Deliberately **not** in `.env.example`: it is a process-environment selector like `NEWSLENS_DATA_DIR`, read before `.env` is ever loaded, and `.env` itself is shared across profiles. A malformed value raises rather than falling back to `default` — silently degrading would route a tester's writes into yours. A well-formed name that has never been created is refused, never provisioned. **This is NOT a redirection var:** it selects a profile *inside* the real-paths guard, so a profile's state is refused to an unsanctioned process exactly like yours (multi-user brief 2026-07-16 §1, "the seam's law is redirection = not real state"). |

## What's real vs. faked

**Faked: nothing.** No mock data, no stubbed integrations, no
`// PROTOTYPE: faked` markers anywhere. External calls, all real and all
yours: the doctor's read-only key validations + feed resolution for enabled
sources, `newslens ingest`'s feed GETs (tier-2 Sonar discovery is PAUSED by
ruling, so ingest makes no metered call at all — keyless or not; keyless +
nothing-enabled means zero network, period), and the one metered LLM call
left on the default path: analysis verification's per-depth-story Sonar call
when `PERPLEXITY_API_KEY` is set (the doctor's cost estimate and PREFLIGHT
both flag it; pause-or-sanction is the principal's open ruling; commenting
the key out of `.env` makes every Sonar path cold). `newslens rank` rides
the subscription lane — $0 charged, costs logged to `briefings.token_cost`
and `ranking_runs`.

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
scripts/battery      the writer-register model battery (dry-run default)
scripts/moat-battery the NL-75 Phase-2 moat battery, T1/T2/T3 (dry-run default)
src/newslens/        paths, db (stdlib-only), config, net, ingest, discovery, ranking, memory, profiles, doctor, cli
templates/           committed artifacts a new profile is BORN from (profile-sources.yaml)
sources.yaml         the principal's tiered outlet list + interests (seeded M2) — the DEFAULT profile's
memory.md            (gitignored) the hand-editable live-threads surface — the DEFAULT profile's
data/                (gitignored) SQLite DB and generated artifacts — the DEFAULT profile's
profiles/<name>/     (gitignored) one non-default reader's whole world: data/, memory.md, sources.yaml
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
