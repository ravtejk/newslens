# tests/ — QA-owned (team/ENGINEERING.md)

Run with: `pytest` (installed via `pip install -e ".[dev]"`). ~15s, fully
offline: every feed/API interaction hits a local fake server on 127.0.0.1;
no real endpoint is ever called, no key is ever needed, and the presence of
a real `.env` cannot change test behavior (sandboxed `ENV_FILE`, scrubbed
process env, force-emptied key vars in every subprocess test).

Since M2 the shipped `sources.yaml` is the principal's live outlet list:
tests pin its *structural* invariants only (never counts, never fetches),
and all template-state / feed-check behavior runs against synthetic
fixtures.

## Layout

| File | Covers |
|---|---|
| `conftest.py` | Sandboxed paths (synthetic sources template), keyless env scrub, socket-level zero-network recorder, local fake OpenAI/Perplexity/RSS server with dynamic routes + `make_rss` builder |
| `test_migrations.py` | Runner: apply (0001+0002), idempotent re-run, re-apply after lost record, ordering, failed-migration never recorded |
| `test_schema_constraints.py` | 0001 CHECKs, `UNIQUE(url, fetch-day)`, `UNIQUE(briefings.date)`, `json_valid`, FKs, `briefings_history` append-only triggers |
| `test_migration_0002.py` | `briefings.date` format triggers (INSERT+UPDATE), format-only boundary pin, M1 constraints coexistence |
| `test_config_sources.py` | Shipped seeded-file structural pins, synthetic template refusal, malformed/problem reporting |
| `test_tiers.py` | `reference_only` structurally unfetchable, `cautious` default-disabled + warned, tier validation, YAML-boolean name strictness |
| `test_config_guards.py` | `BUDGET_CAP_USD_PER_RUN` / `GENERATE_HOUR_LOCAL` validation, config + doctor sides |
| `test_ingest.py` | UTC fetch-day upsert idempotency, per-source transactions, exact degradation line, 20-item cap, 1500-char excerpts, 308 handler, hostile XML, tier enforcement at fetch, last-writer-wins pin |
| `test_discovery.py` | Cold seam: keyless/interest-less/budget-abort build no request (socket-guarded), retry discipline (one retry, never 4xx), `search_results`-only storage, no fabricated excerpts, per-day idempotency |
| `test_doctor_offline.py` | Exit-code contract both directions, exact fix hints, mechanical zero-network keyless, 401/5xx/unreachable, secret-leak canary, DB states, feed checks |
| `test_doctor_m2.py` | Dormant `GENERATE_HOUR_LOCAL` (garbage still fails), tier-aware sources section, keyless-never-calls-APIs invariant, unreadable `.env`/ping-file pins |
| `test_cli.py` | `--version`, usage errors, `migrate` output, `ingest` wiring (refusal verbatim, counts, all-down exit 1), `rank` wiring (date regex, keyless exit 1, window/caveat/cost rendering), venv entry point |
| `test_sonar_spike.py` | Keyless polite refusal (exact message), zero network via socket spy, executable bit, probe-count money-knob validation |
| `test_repo_hygiene.py` | `.env.example` contents, `check-ignore` on secrets/state, `.env` never tracked (GitHub-remote tripwire), feedparser declared, versioned prompts |
| `test_preinstall_doctor.py` | Real `scripts/doctor` under system Python 3.9 (no venv), foreign cwd, sitecustomize socket spy, forced-empty keys, 3.9 compile floor |
| `test_migration_0003.py` | `ranking_runs` shape + `json_valid` CHECKs; append-only enforcement (KNOWN-RED BUG-5) |
| `test_net.py` | Shared fetch seam: 4MB byte cap (loud per-source failure), 308 handler, `head_bytes`, one UA across ingest+doctor |
| `test_ranking_validation.py` | `validate_payload` hostility (all-problems reporting, invented/reused ids — the 2026-07-04 live class, re-leveled tags, ranges), retry/429/quota/401 money paths via `OPENAI_CHAT_URL` seam, budget pre-call, spend-proof keyless/interest-less refusals, failed-run instrumentation, render-error class |
| `test_ranking_selection.py` | Principal amendments A (bounded followed boost, generic flag, override-pool exclusion) and B (recency window, own-date exclusion, honesty line), override contract (pool/bar/cap/label), corroboration labels, archive-before-overwrite e2e |
| `test_memory_sync.py` | The memory.md ⇄ SQLite sync contract (lifecycle v2): seeding guard, file-wins with dismissal audit, annotation round-trips, line-numbered hard stops with the file untouched, dormancy clock (mock-time), context cap/order, revive/reference surfaces, `prior_briefing_context` bounds, 0006 rebuild data survival + re-apply |
| `test_memory_ranking.py` | Zero-influence at all three layers (score/selection/vocabulary), revival e2e products (DB + slot JSON + meta.revivals + dated warning + same-run re-render), dismissed_user absent from prompt and unrevivable, sync-first loud + BUG-6-logged, item-11 NULLing, truncation named precisely, Retry-After clamp, `[id=N]` armor, invented-ids hard-reject (no repair extension) |

## KNOWN-RED convention

Tests named `test_BUG<n>_*` encode **confirmed, reported bugs** — they assert
the *contract*, fail against the current code, and go green when the bug is
fixed. They are QA findings for the implementer, not suite breakage.
Do not delete or skip a red BUG test to make the suite pass — fix the bug.

History: the milestone-1 QA pass (2026-07-02) shipped 7 red — BUG-1
(non-finite budget cap accepted by config + doctor, 6 tests) and BUG-2
(unreadable sources.yaml crashed the doctor with a traceback, 1 test). Fix
loop 1 resolved both (validator consolidated into config with the doctor
delegating; unguarded file reads made friendly; DB queries made read-only).
QA re-verified same day: suite intact and unweakened, 169/169 green, both
manual repros now fail friendly. The BUG tests stay as regression guards.

M2 QA pass (2026-07-03): 2 red — BUG-3 (prompt-render crash in discovery)
and BUG-4 (spike not executable). Both fixed in the M2 fix loop (render
errors degrade class-wide with the exception named; spike 100755 + probe-arg
validation); the BUG tests are green regression guards now.

M3 QA pass (2026-07-04): 3 red — BUG-5 (`ranking_runs` append-only was
convention, not structure) and BUG-6 (pre-call failures logged no
instrumentation row). Fix loop 1 resolved both (migration 0004 adds the
abort-trigger pair; `run_rank` restructured so every post-connection
RankingError logs exactly one status=failed row, incl. budget aborts and
no-items refusals). The live duplicate-item-ids class flipped from
reject-and-retry to a DISCLOSED deterministic repair (keep first, drop later,
warn in output, persist detail at `ranking_runs.meta.repairs`) — re-pinned to
the new contract, with scope pins proving every other violation class still
hard-rejects and the validator's own duplicate check retained as backstop.
QA re-verified 2026-07-04: suite unweakened, 372/372 green, fix live on the
real DB (read-only check). BUG tests stay as regression guards.

M4 QA pass (2026-07-04, lifecycle v2 per ADR-0006): 0 red — 436/436 green on
first calibrated run. Eight amendment stales re-pinned (six-migration world;
v2 status trio with v1 values rejected). The sync contract, three-layer
revival zero-influence, dismissed_user-never-revives, 0006 rebuild survival,
both implementer-flagged regressions (verbs surviving their own trailing
sync; [id=N] bracketed keys), and the invented-ids hard-reject boundary are
all pinned. Per the principal's Option A, red tests in this suite are the
acceptance criteria for any future fix loop — no standalone QA re-verify.

## Implementer contract notes for milestone 3 (for QA — appended per dispatch)

New surfaces: `ranking.py` (clustering/scoring/override/corroboration/persist),
`net.py` (shared fetch), migration 0003 (`ranking_runs`), `rank` CLI,
`followed_analyst` + duplicate-lint in config, seeded interests.

Offline-testable by construction (no network, no key): `validate_payload`
(feed it malformed/truncated/invented-id/cross-cluster-dupe/wrong-level
payloads — the spec §E-M3 QA case), `personal_score` / `combined_score` /
`select_slots` (override cap=1, threshold 8, zero-match pool, followed-boost
exclusion from override pool, unfilled slot on quiet days), `corroborate`
(wire exclusion, sonar exclusion, 0/1/N labels), `persist` (idempotent re-rank
archives prior row to briefings_history first; ranking_runs row per run incl.
failed runs), prompt render failure -> RankingError. The LLM call seam is
`_post_chat` / `call_llm_validated` — monkeypatch or point OPENAI_CHAT_URL at
the fake server (it already speaks /chat/completions). Keyless rank must build
no request. Constants QA may pin: OVERRIDE_THRESHOLD=8, MAX_SLOTS=5,
OVERRIDE_LABEL_PREFIX, CORROBORATION_CAVEAT (rendered in CLI output AND stored
in corroboration_labels.standing_caveat).

### Principal-amendment invariants (2026-07-04) — QA-pinnable

1. **Bounded followed-analyst boost:** `ranking.FOLLOWED_BOOST (0.35) <
   ranking.TOPIC_WEIGHT (1.0)`; followed-only personal score = 0.35; a
   followed-only cluster at world 10 loses to a topic-matched cluster at
   world 3 in `combined_score`; `select_slots` contains no followed⇒slot
   path, and followed clusters never enter the override pool.
2. **Recency window:** `ranking.candidate_window` — first-run basis = full
   14d cap; with a prior briefing row (different date) newer than the cap,
   basis = "since your last briefing"; the target date's OWN row is excluded
   (idempotent re-rank must not shrink the window to minutes);
   `RECENCY_CAP_DAYS == 14`. Honesty line: report.warnings carries
   "ingested history available" whenever history < window;
   `ingested_history_days` on an empty DB = 0.0.

### M3 fix-loop-1 behavior changes (for QA re-pin at re-verify)

1. **BUG-5 fixed via migration 0004** (`trg_ranking_runs_no_update/_no_delete`,
   message "ranking_runs is append-only") — test_BUG5_* should go green as-is.
2. **BUG-6 fixed**: ALL post-connection RankingErrors log exactly one
   status=failed ranking_runs row — budget aborts (the red test), prompt
   render failures, and now ALSO no-items-in-window refusals (new behavior,
   QA may want a pin: rows==1 for the no-items case too). Pre-connection
   refusals (keyless / no interests / sources problems) still log nothing.
3. **Clustering repair (disclosed)**: `ranking.repair_duplicate_ids` runs
   between parse and validation inside call_llm_validated. Semantics: keep
   first cluster assignment per item_id, drop later duplicates, drop+disclose
   clusters emptied by repair; returns (payload, info) where info carries
   repaired count, dropped[] (id + cluster label, capped 20), clusters_emptied.
   Disclosure: run warning "clustering repair: N duplicate item
   assignment(s) dropped..." + ranking_runs.meta.repairs. Out-param
   `repairs` dict on call_llm_validated (return shape unchanged).
   `validate_payload` still rejects duplicates when called directly (backstop)
   — your two unit pins stay green; the e2e frozen test
   (test_invalid_payload_twice_is_the_live_failure_end_to_end) flips by
   design: the same payload now repairs, validates, and succeeds with
   disclosure. Other violation classes (invented ids, re-leveled tags,
   ranges, empty fields, non-int ids) still hard-reject end to end.

## Implementer contract notes for milestone 4 (memory + continuity)

New: `memory.py`, migration 0005 (UNIQUE lower(topic) index — schema tests
keep an empty memory table: seeds are NOT in the migration), `memory` CLI
verbs, ranking wiring. Offline-testable surfaces:

- `memory.sync_memory` semantics (ADR-0005 §1): file wins; deleted line ->
  dismissed (audit row kept); section move -> status change (revive/demote);
  note edit -> DB update; new line -> INSERT; canonical rewrite after sync;
  parse problems -> MemorySyncError naming line numbers (duplicate topics,
  unknown headings, pre-section bullets, unreadable file). Rank turns sync
  errors into RankingError (BUG-6-logged).
- `memory.seed_if_first_run`: fires ONLY on empty-table + absent-file; 14
  threads (taxonomy §C — the 5 borderline twins are inside the 14).
- `memory.apply_staleness`: active->stale at >14d from max(created_at,
  referenced briefing's generated_at); note edits do NOT reset the clock;
  transitions surfaced via SyncResult.went_stale -> rank warnings.
- `memory.active_context`: active only, referenced-first ordering, cap 15.
- `memory.update_references` via `ranking.persist`: matched threads get
  last_referenced_briefing_id + updated_at (case-insensitive, never on
  dismissed rows).
- `memory.prior_briefing_context`: latest briefing with date < for_date;
  None when absent; <=5 stories; text_block <=1500 chars; malformed slots
  JSON -> None.
- persist changes: re-rank NULLs narrative_text/script_text/audio_file_path
  (item 11); ranking_runs.token_usage now SQL NULL when usage empty;
  override meta carries {reason, slot} when fired and
  `top_zero_match_world_impact` (renamed from top_zero_match_score).
- `_retry_after_seconds`: clamped finite [0,20]; "-5"/"nan"/"inf" -> default.
- `--date` now rejects impossible calendar dates (2026-13-01) via strptime;
  the "--date must be YYYY-MM-DD" fragment is unchanged.
- CLI `memory list/add/dismiss/note`: every verb syncs before and after;
  `add` on stale/dismissed revives; topic containing " — " rejected exit 2.

### AMENDED for memory lifecycle v2 (principal amendment 2026-07-04) — pin THIS surface

The M4 notes above predate the amendment; where they conflict, this section
wins (ADR-0006). QA pins the amended surface once:

- **States:** active | dormant | dismissed_user (VALID_STATUSES; migration
  0006 rebuilt the table: stale->dormant, dismissed->dismissed_user, new
  status_changed_at column, widened CHECK — schema tests: memory CHECK now
  rejects 'stale'/'dismissed' and accepts the new pair).
- **apply_staleness is now `memory.apply_dormancy`** (same 14d clock,
  DORMANT_AFTER_DAYS; note edits still don't reset it); SyncResult.went_stale
  is now `went_dormant`.
- **memory.md:** two sections, Active / Inactive. Inactive annotations decide
  parse-back state: "(dormant since <d>, last covered <d>)" -> dormant;
  "(dismissed by you <d>)" -> dismissed_user; BARE line under Inactive ->
  dismissed_user (explicit demotion). Deleted line -> dismissed_user. Active
  lines strip "(last referenced: ...)". Inactive renders complete, sorted by
  status_changed_at desc. v1-format files (## Stale / ## Dismissed headings)
  fail the parser LOUDLY with the regenerate hint.
- **Auto-revival (the hard constraint — pin all three layers):**
  `memory.dormant_topics` (status='dormant' only — dismissed_user absent by
  construction) feeds a match-only prompt vocabulary; `matched_dormant`
  validates against it (validate_payload gained optional 5th arg
  dormant_topics, default None -> matches must be empty); `personal_score`
  never reads matched_dormant (zero influence: no boost, no override-pool
  exclusion); revival happens ONLY in `ranking.persist` (post-selection),
  via `memory.revive_matched` (dormant-only), returns [{topic,
  last_covered}], embeds slot.revived_threads (captured PRE-update), writes
  meta.revivals, surfaces a dated run warning, and re-renders memory.md
  immediately.
- **Verbs:** dismiss -> dismissed_user ("never auto-revives" wording); list
  --status choices renamed; every verb's TRAILING step is RENDER-ONLY (a
  trailing full sync would clobber the verb's own change — regression found
  and fixed during the amendment: `memory add` then sync used to
  dismiss-by-deletion the fresh row; pin that `add`/`note` survive their own
  command).
- RankedSlot gained matched_dormant + revived_threads (defaulted — existing
  constructions unaffected).

### M4 gate-fix pin specs (QA writes these; the fixes they pin)

1. **Revival survives its own sync, BOTH paths:** (a) file-move a dormant
   thread to Active -> sync -> thread is ACTIVE at sync end (not just a
   status_changed entry) and survives the NEXT sync too; (b) `memory add` on
   a dormant thread -> next sync_memory -> still active. Mechanism:
   `apply_dormancy` basis now includes status_changed_at; note edits still
   excluded (a >14d note-edited-only thread still goes dormant).
2. **Annotation-kept move to Active = revival:** a line moved up WITH its
   "(dormant since …)" or "(dismissed by you …)" annotation revives the REAL
   thread (topic parsed clean, no junk thread, no dismissed-by-deletion audit
   inversion); kept "(last referenced: …)" on demoted lines strips likewise.
3. **mtime clobber-guard:** touching memory.md between rank's opening sync
   and its post-run refresh -> refresh skipped + "changed while this run was
   in flight" warning; untouched file -> refresh happens.
4. **Bracket sanitize:** a title containing "[id=99]" renders in items_block
   with parens — the only "[id=" tokens in the prompt are the real keys.
