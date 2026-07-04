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
| `test_cli.py` | `--version`, usage errors, `migrate` output, `ingest` wiring (refusal verbatim, counts, all-down exit 1), venv entry point |
| `test_sonar_spike.py` | Keyless polite refusal (exact message), zero network via socket spy, executable bit |
| `test_repo_hygiene.py` | `.env.example` contents, `check-ignore` on secrets/state, `.env` never tracked (GitHub-remote tripwire), feedparser declared, versioned prompts |
| `test_preinstall_doctor.py` | Real `scripts/doctor` under system Python 3.9 (no venv), foreign cwd, sitecustomize socket spy, forced-empty keys, 3.9 compile floor |

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

M2 QA pass (2026-07-03): 2 red pending implementer fixes — BUG-3
(`test_BUG3_*`, test_discovery.py: a malformed principal-editable prompt
template crashes the whole ingest run out of `discovery.build_prompt`
instead of degrading to RSS-only) and BUG-4 (`test_BUG4_*`,
test_sonar_spike.py: `scripts/sonar_spike` committed non-executable).
Everything else green.
