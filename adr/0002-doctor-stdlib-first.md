# ADR 0002 — Doctor is stdlib-first, key-optional, and exits honestly

**Date:** 2026-07-02 · **Status:** accepted · **Milestone:** 1

## Context

The principal has not yet granted `OPENAI_API_KEY` or `PERPLEXITY_API_KEY`
(the Sonar reliability spike is deferred until they do — DECISIONS.md
2026-07-02, third entry). The dispatch requirement: the doctor must run
cleanly **today**, on a fresh clone, with no keys and possibly no
`pip install`, reporting exactly what's missing and how to fix it — and its
exit code must reflect overall health. This machine's only Python is the
system 3.9.6.

## Decision

1. **Stdlib-only at import time.** `doctor.py` (and everything it imports
   unconditionally: `paths`, `db`) uses only the standard library. Third-party
   imports (`yaml` via `config`, `dotenv`) happen inside individual checks,
   guarded — a missing dep is a `✗` report line with the install command, and
   dependent checks degrade to "skipped (see missing-deps line)".
2. **`scripts/doctor` bootstraps `sys.path`** to the checkout's `src/`, so the
   doctor runs pre-install and always diagnoses the code in the checkout.
3. **HTTP via `urllib`, not `requests`/SDKs.** The two key checks are single
   read-only calls (OpenAI `GET /v1/models`; minimal Sonar completion with
   `max_tokens=8`, prompt versioned at `prompts/doctor_sonar_ping.txt` per the
   prompts-are-code rule). urllib keeps milestone 1's dependency footprint to
   exactly `pyyaml` + `python-dotenv`. Later milestones choose their own HTTP
   stack; the doctor doesn't preempt that.
4. **Minimal fallback `.env` parser** inside the doctor, used only when
   python-dotenv isn't installed yet, so key diagnosis works pre-install. The
   runtime path (`config.load_env`) uses python-dotenv; the fallback is
   doctor-only, ~15 lines, documented as deliberately simple. Real environment
   variables always win over file values; no secret value is ever echoed.
5. **Exit code contract:** `0` = everything *required for a real daily run* is
   in place (warnings allowed); `1` = any `✗` line. Today's no-keys state
   correctly exits `1` — "runs cleanly" means no tracebacks and a fix hint per
   line, not a false green. Marker semantics: `✓` pass, `✗` required-failing,
   `⚠` action needed / advisory, `○` informational.
6. **No keys → no API calls.** The only network the doctor ever touches
   without keys is resolving RSS feeds the principal has *actively configured*
   (none, in the shipped template). Every external call has an explicit
   timeout (15s OpenAI / 20s Perplexity / 10s per feed).
7. **Static cost-per-run line** (~$0.18/run, ~$5.50/mo from spec §C, TTS-share
   caveat) satisfies ENGINEERING.md's "doctor prints estimated cost-per-run"
   until the pipeline exists to log real per-run costs.

## Alternatives rejected

- **`requests` + official SDKs now** — two more dependencies for two one-off
  GET/POSTs; SDK choice belongs to the milestone that builds the pipeline.
- **Hard-require install before doctor runs** — fails the "fresh clone,
  minutes to green" bar; the doctor's first job is diagnosing exactly that state.
- **Exit 0 with warnings when keys are missing** — a dishonest green; QA and
  launchd-era automation need the exit code to mean "ready".
- **Validating RSS URLs from the commented template examples** — network calls
  for sources the principal never chose; violates the no-defaults decision.

## Consequences

- The doctor works identically via `scripts/doctor` (pre-install) and
  `newslens doctor` (post-install).
- Two `.env` parsers exist (dotenv + fallback); drift risk is bounded by the
  fallback's doctor-only use and deliberate simplicity.
- QA contract: no-keys fresh-clone run exits `1` with friendly `✗` lines;
  post-install no-keys run shows deps/schema `✓` and keys `✗`; exit flips to
  `0` only when keys validate and required checks pass.

## Amendment — 2026-07-02, QA fix loop 1 (BUG-1, BUG-2, read-only observation)

QA's milestone-1 pass changed three of the decisions above; recorded here so
the ADR describes the code as it is:

1. **Validators are consolidated; config is stdlib-only at import time.**
   BUG-1 (non-finite budget cap accepted, guardrail silently defeatable) shipped
   *because* item 1's original shape forced the doctor to duplicate config's
   guard-var validation, and the copies drifted together into the same hole
   (`float("nan") <= 0` is False). Fix to the class, not the symptom:
   `config.py` now imports yaml lazily inside `load_sources`, making the module
   stdlib-importable pre-install, so the doctor imports it unconditionally and
   renders `config.budget_cap_usd_per_run` / `config.generate_hour_local` —
   the ONLY implementations, which now reject non-finite values
   (`math.isfinite`). A missing PyYAML surfaces as ImportError at the
   `load_sources` call site, preserving the pre-install "validation skipped"
   line. The doctor-local fallback `.env` parser remains the one deliberate
   duplication (dotenv genuinely absent pre-install), unchanged in scope.
2. **"Never a traceback" now covers unreadable files, not just missing ones**
   (BUG-2): `load_sources` wraps its read in OSError → `SourcesParseError`;
   the sonar-ping read and the `.env` read (both dotenv and fallback paths)
   degrade to ✗ lines with permission hints. `chmod 000 sources.yaml` is a red
   line, not a crash.
3. **The doctor is read-only toward real state** (QA observation):
   `db.applied_migrations` / `db.pending_migrations` are read-only by
   construction (sqlite_master check + `mode=ro` URI connect; a missing DB
   file means "everything pending", not "create it"); `db.migrate` is the
   module's only writer. Sole deliberate doctor write: the data-directory
   writability probe — writability cannot be verified without writing; it
   cleans up after itself and never touches the DB file.
