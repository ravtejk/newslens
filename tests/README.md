# tests/ — QA-owned (team/ENGINEERING.md)

Run with: `pytest` (installed via `pip install -e ".[dev]"`). ~7s, fully
offline: API-shaped checks hit a local fake server on 127.0.0.1; no real
endpoint is ever called and no key is ever needed.

## Layout (milestone 1)

| File | Covers |
|---|---|
| `conftest.py` | Sandboxed paths, keyless env scrub, socket-level zero-network recorder, local fake OpenAI/Perplexity/RSS server |
| `test_migrations.py` | Runner: apply, idempotent re-run, re-apply after lost record, ordering, failed-migration never recorded |
| `test_schema_constraints.py` | CHECKs (source_type, status, wire flag), `UNIQUE(url, fetch-day)`, `UNIQUE(briefings.date)`, `json_valid`, FKs, `briefings_history` append-only triggers |
| `test_config_sources.py` | Template (zero sources, zero problems), valid, malformed, problem reporting, polite-refusal message |
| `test_config_guards.py` | `BUDGET_CAP_USD_PER_RUN` / `GENERATE_HOUR_LOCAL` validation, config + doctor sides |
| `test_doctor_offline.py` | Exit-code contract both directions, exact fix hints, mechanical zero-network keyless, 401/5xx/unreachable paths, secret-leak canary, DB states, feed checks |
| `test_cli.py` | `--version`, usage errors, `migrate` idempotency at CLI level, loud failure path, venv entry point |
| `test_repo_hygiene.py` | `.env.example` contents, `git check-ignore` on secrets/state, executable doctor, versioned ping prompt |
| `test_preinstall_doctor.py` | Real `scripts/doctor` under system Python 3.9 (no venv), foreign cwd, sitecustomize socket spy, 3.9 compile floor |

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
