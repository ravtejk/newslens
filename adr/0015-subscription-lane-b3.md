# ADR-0015: The `claude -p` subscription lane (B3)

**Status:** accepted (depth-architecture build, milestone B3)
**Date:** 2026-07-16
**Spec:** Engineering council transcript
workspace/debates/2026-07-16--newslens--engineering-3.md §5.2 (the `claude -p`
binding) + §5.1 (the seam); ADR-0014 (the B1 seam this plugs into);
DECISIONS.md 2026-07-16 "subscription-lane mandate" (subscription is ALWAYS the
priority; the one-loop kill-switch countermanded).

## Context

ADR-0014 built one seam (`llm.py`) so a second lane is a plug, not surgery. B2
registered `anthropic:api`. B3 registers the **subscription lane**: the
anthropic seats (rank/editor/script) run on the principal's Claude subscription
via the `claude -p` CLI instead of the metered API — ~$30-40/month saved at solo
scale. The mandate makes subscription the DEFAULT for those seats and the API
lane their registered fall-over.

The lane feeds prompts built from **untrusted fetched news text** into a full
agent binary, so it ships behind Rook's four red conditions or it doesn't ship.

## Decisions

### 1. Subprocess, not the Python Agent SDK

The Agent SDK needs Python ≥3.10; the pinned floor is 3.9.6 (ADR-0014 spike).
Every seat is a single-turn, tools-disabled text completion — an agent harness
to not-use-agents is dead weight. `_subscription_provider` is a thin
`subprocess.run` of `claude -p`, zero new Python deps (stdlib subprocess/shutil/
tempfile), preserving the raw-urllib/stdlib posture. The seam keeps
`anthropic:subscription` swappable for an SDK provider if the runtime ever moves
(Ada's dissent, preserved).

### 2. The invocation contract (flags pinned READ-ONLY vs the installed --help)

`claude -p --output-format json --model <model> --tools "" --safe-mode
--strict-mcp-config --no-session-persistence`, prompt on **stdin** (ARG_MAX-
immune; not leaked to `ps`), cwd = a fresh empty scratch dir removed after,
json_mode adds `--append-system-prompt <the JSON-only nudge>`. Verified against
CLI v2.1.212:
- `--tools ""` — "" disables ALL built-in tools (Rook #2).
- `--safe-mode` — no CLAUDE.md/skills/plugins/hooks/MCP/agents (the injection
  surface); auth is left intact.
- `--strict-mcp-config` — no MCP servers (none are passed).
- `--no-session-persistence` — hermetic; nothing written outside the scratch dir.

Parse ONLY documented-stable fields: `result`, `session_id`, `total_cost_usd`,
`usage.*`. `is_error` / non-zero exit / non-JSON stdout are transport-shaped
(RuntimeError → the caller retries the ORIGINAL bytes once, like a 5xx); a
timeout SIGKILLs the child (subprocess.run) and surfaces as TimeoutError.

### 3. The env allowlist STRIPS ANTHROPIC_API_KEY (Rook #1, the D1 class)

The child env is an ALLOWLIST (HOME/PATH-family only) with `ANTHROPIC_API_KEY`
guaranteed absent — else the CLI prefers the key, bills the API, and the ledger
lies "$0 subscription". A born-red test fails the instant the strip is removed.
The lane owns its own auth (the logged-in CLI); `LaneRequest.api_key`/`.url`
(the openai offline-test seam) are ignored.

### 4. Binary resolution + fail-loud-at-the-gate (Rook #3)

`resolve_claude_bin`: `NEWSLENS_CLAUDE_BIN` → `PATH` → `~/.local/bin/claude`
(the CLI is not on the non-login-shell PATH here). An explicit override that
isn't executable fails loud (no silent fall-through). `check_lane` verifies the
binary resolves for a subscription seat (a stat, no spawn), so a missing/
misconfigured CLI is `LaneUnavailable` **at the gate** naming the install fix —
never a silent wrong-lane call, never retried into a GenerateError.

### 5. Ledger: usd_charged 0.0, usd_shadow always API-priced, caps on shadow

`cost_fields` sets `usd_charged` == 0.0 on the subscription lane and
`usd_shadow` from the seat's price table (Haiku $1/$5). If the CLI reports
usage we ledger it; if not we estimate from char length and LABEL it
(`usd_shadow_estimated=True` — never fake precision). The CLI's `total_cost_usd`
is kept as a cross-check field, never as `usd_charged`. **Budget caps now bind
on shadow** (Onna's law): the pipeline's `spent` accumulators switched from the
charged `usd` to `usd_shadow` — a no-op on the api lane (shadow == charged) but
the difference that keeps the cap real once editor/script bill $0.

### 6. Rider R-B3a — the $0-charged state row must not vanish

The state-rewrite step was appended to `report.steps` only `if
report.memory_usd` (charged). A subscription state seat bills charged 0.0, so
the whole row — and its shadow spend — vanished from the ledger. Fixed:
`_default_state_chat` returns (raw, charged, shadow); `StateRewriteResult` and
`report.memory_shadow_usd` carry shadow; the row is gated on SHADOW spend and
records both figures. 2-tuple state chats stay backward-compatible (shadow
defaults to charged). Born-red pinned.

### 7. Gate FIX-1 — stage-boundary lane preflight

Analyst and state lane misconfigs were swallowed per-slot / per-thread into a
disclosed $0 'failed' brief or a silently-stale moat (ADR-0014 known gap). B3
adds a preflight — `check_lane(resolve_seat("analyst"))` and `("state")` — at
generate stage entry (before any expensive work or persist) and in standalone
`run_analysis`, OUTSIDE the per-slot/per-thread excepts. A config error now
KILLS the run at every stage, consistently. Per-slot degrade stays for TRANSIENT
failures. Born-red pinned.

## Alternatives considered

- **Non-existent-sentinel test binary** (fail-safe to LaneUnavailable): rejected
  as too aggressive — it fired at `check_lane` for every subscription seat even
  when the transport was stubbed, reddening ~680 assertions. The dispatch's
  "stub claude binary in the sandbox" is the pattern: the conftest points
  `NEWSLENS_CLAUDE_BIN` at a canned-success shim (safe: never the real CLI), and
  api-provider transport tests pin `NEWSLENS_LANE_<SEAT>=api`.
- **Auto-fallback to the API lane on subscription failure:** rejected as a
  spend-without-consent bug. Fail-loud is the default; `NEWSLENS_LANE_FALLBACK
  =api` is the principal-armed, ledger-labeled opt-in (the ship checkpoint asks).

## Consequences / known gaps (for the gate)

- **No caller `max_tokens` on the subscription lane.** `claude -p` manages its
  own output limit; the api lane's truncation guard (`finish_reason=="length"`)
  cannot fire here (a success maps to "stop"). The caller's json.loads/validate
  is the real backstop, unchanged. Acceptable for the short rank/editor/script
  outputs; revisit if a seat with a tight cap joins the lane.
- **`thread_state.cost_usd` stays charged** (0.0 on subscription). The shadow is
  in `report.steps` / `briefings.token_cost`; the per-state-row column is the
  real-money column and is not migrated (no schema change in B3).
- **41 QA-seam assertions flip** (test_b1_llm_seam_qa, test_b2_claude_lane_qa):
  the deliberate default-lane flip + subscription availability + the R-B3a/
  FIX-1 rulings the B2 QA file explicitly deferred "to the gate." Categorized
  for QA's pass; the teeth (env-strip, tools-off, cwd isolation, fs tripwire,
  ledger, R-B3a, FIX-1) are re-pinned born-red in test_b3_subscription_lane.py.
- **The auth probe is the principal's.** The doctor confirms binary+version but
  does NOT prove login (that spends quota). The live smoke is the principal's.

- Gate loop-4 residual (LOW, accepted): a seat left unscoped at publication
  (unavailable + unarmed) that HEALS mid-run rides two fresh resolutions
  (call_llm, _step_ledger) — a fork there requires a triple binary flap
  inside one run; the unscoped arm exists precisely to preserve deferred
  kill. Same accepted shape as ranking's direct-_post_chat fallback.
