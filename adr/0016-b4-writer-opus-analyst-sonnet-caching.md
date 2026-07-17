# ADR-0016: B4 — writer→Opus 4.8, analyst→Sonnet 5, prompt caching, cost envelope

**Status:** accepted (depth-architecture build, milestone B4) — the LANE RULING
(§3) is PRESENTED TO THE GATE/PRINCIPAL at checkpoint; Option C shipped as the
default pending ratification.
**Date:** 2026-07-16
**Spec:** Engineering council transcript
workspace/debates/2026-07-16--newslens--engineering-3.md §5.6 (B4 row) + §5.1
(the seam / caching); ADR-0014 (B1 seam, incl. R-B4a + FIX-2 riders) and
ADR-0015 (B3 subscription lane) this plugs into; register-target-spec
(research/2026-07-16--register-target-spec.md); HSR after-measurement
(research/2026-07-16--hsr-after.md). Battery gate: lands before ~07-24.

## Context

B1–B3 built the seam and the two Claude lanes on the cheapest/most-validated
seats (rank/editor/script → Haiku). B4 flips the two content seats the ~07-24
blind battery judges: the WRITER to Opus 4.8 and the ANALYST to Sonnet 5, wires
prompt caching on the API lane, enters the register spec as writer-prompt law,
and ships the battery's one-command runner. The writer register is measured by
the battery, not by this milestone — so no new validators land here.

## Decisions

### 1. Writer → Opus 4.8, analyst → Sonnet 5 (SEATS flip; llm.py)

`SEATS["writer"]` = `claude-opus-4-8` on the api lane, adaptive thinking, effort
`xhigh`, timeout 600s; `SEATS["analyst"]` = `claude-sonnet-5` on the api lane,
adaptive thinking, effort `high`, timeout 240s. Both flip via the one-constant
seam (a config diff, revert = flip the row back to `**_GPT4O_API`). Editor/
script stay Haiku (subscription); state/synthesis stay gpt-4o.

### 2. Sampling omission — the 400 the flip would otherwise cause

Opus 4.8 and Sonnet 5 (the Claude 4.6+ family) **reject `temperature` with a
400**. `SeatConfig.sampling: bool` (default True) gates it: the api provider
omits `temperature` when `sampling=False` (writer/analyst). Haiku 4.5 and GPT-4o
keep `sampling=True`, so their request bytes are byte-unchanged (the B1/B2 body
pins do not move). The callers still pass their historical temperature; the
provider ignores it for the flipped seats.

### 3. LANE RULING (gate-ordered — PRESENTED, gate/principal decides)

The subscription lane has NO `max_tokens` (its truncation guard cannot fire) and
`--effort` is best-effort (may not hold). The writer/analyst are truncation- and
effort-sensitive. Options presented at checkpoint:
- **(a)** Opus writer defaults subscription + a code-owned post-hoc length/
  truncation validator compensates.
- **(b)** Opus defaults subscription; the battery A/Bs both lanes; evidence rules.
- **(c)** Opus writer + Sonnet analyst default to the **api lane** until the CLI
  gains the knobs; the subscription mandate is honored on the already-flipped
  Haiku seats (rank/editor/script).

**Shipped default: Option C** — effort maps exactly on the api lane
(`output_config.effort`), `max_tokens` is required and its `finish_reason==
"length"` guard fires there (§5.1; ADR-0015 known gap: the subscription lane
can't). The seam still ALLOWS forcing either seat to subscription
(`NEWSLENS_LANE_WRITER=subscription`, a valid registered anthropic lane now) —
the gate/principal can override the default without a code change.

### 4. Prompt caching on the api lane (measured, not assumed)

`LaneRequest.system` carries the stable prefix; the api provider emits it as a
`system` block with `cache_control: {type: "ephemeral"}` (render order tools →
system → messages puts the breakpoint ahead of the volatile user prompt). The
callers split at a sentinel (writer: `=== THE READER'S TAGS`; analyst:
`Word budget for all prose…`), provider-gated so an openai revert sends the
prompt as one user message unchanged. The subscription lane has no cache surface
and folds the prefix inline (never dropped). `usd_shadow` stays UNDISCOUNTED —
the transcript's "the ~0.1x is measured, not assumed": the surface is wired and
`cache_read` goes nonzero on within-TTL reuse, but a money guard never
under-counts on an unproven hit. Expectations: the analyst instruction prefix
(~1.5k tok) sits BELOW Sonnet's 2048 cache minimum today; the writer system
prefix (~4.1k tok, grown by the register spec) sits just ABOVE Opus's 4096 min
but reuses only on a retry / same-day re-run (variant B retired = one writer call
per edition). The battery/live runs measure the real rate.

### 5. R-B4a — model literals derive from SEATS or die

`generate.WRITER_MODEL`, `analysis.ANALYSIS_MODEL`, `memory_core.STATE_MODEL`
(and their USD constants) now read `llm.SEATS[...]` (the ranking.RANK_MODEL:61-63
shape) — a `KeyError` if the seat vanishes, never a stale literal.

### 6. Register spec → writer prompt (variant A only; B is retired)

The PROMPT-expressible register-target-spec law lands in
prompts/narrative_variant_a.txt: the §6 BANNED CHARMS, the §2 synthesis-line
sourcing hierarchy, and the HSR dated-callback REQUIREMENT (when a MEMORY block
carries a dated delta). Prompt-level only — validators stay code-owned, no new
ones this milestone (the battery measures first). The law sits above the split
sentinel, so it rides the cached system prefix.

### 7. Cost envelope — budget cap 0.25 → 1.50 (PRINCIPAL MONEY CHECKPOINT)

`NARRATIVE_MAX_TOKENS` 4600 → 16000 and `ANALYSIS_MAX_TOKENS` 1400 → 6000 give
adaptive thinking (billed as output) headroom above prose so a run doesn't
length-finish into a failed run + paid retry. The GPT-4o-era $0.25 cap made the
pessimistic narrative pre-check ($0.40 at the Opus ceiling) abort every edition;
raised to $1.50 (approved envelope ~$0.90–1.30/edition; the shadow is
undiscounted so the cap over-counts — the safe direction). Tune down once
measured spend lands.

### 8. Battery harness — scripts/battery + src/newslens/battery.py

One command, given a date with an existing briefing row: builds the variant-A
narrative prompt (read-only via `db.connect_readonly`) and produces one
narrative artifact per writer-model arm (default Opus/Sonnet/Fable via
`NEWSLENS_MODEL_WRITER`). DRY-RUN DEFAULT (zero calls, zero writes — prints the
plan + per-arm estimates); `--run` makes the live calls (principal-executed,
needs ANTHROPIC_API_KEY), bounded cumulatively by BUDGET_CAP_USD_PER_RUN; never
touches the record (artifacts under DATA_DIR/battery/). Sanctions the incident
guard like the other real entrypoints.

## Consequences / known gaps (for the gate)

- **~45 conscious QA flips** (the B2/B3 pattern): the B1/B2/B3 seam tests pin the
  pre-B4 stack (writer/analyst = gpt-4o) and the $0.25 cap; they flip to Opus/
  Sonnet + $1.50 + the new `NEWSLENS_MODEL_WRITER` env var. Re-pinned by QA
  against the B4 contract; none is a regression (each verified).
- **FIX-2 riders (ADR-0014):** (a) the GenerateError-wrapped-LaneUnavailable
  carve-out is structurally impossible (gate-before-loop, landed B2) — the Opus
  writer is api-lane-registered, so no LaneUnavailable reaches the loop; (b) the
  exhaustive sweep's residual-acceptance arm stays deleted (`pytest.fail`); (c)
  the pre-fail sleep on that dead path is gone with the path — line 544 is the
  live 429/5xx/validation retry backoff, untouched. B4 does not reintroduce any.
- **Analyst-on-subscription is now a valid lane** (it's anthropic). The prior
  "openai seat forced to subscription dies loud" invariant now holds only for the
  still-openai seats (state/synthesis).
- **Cache discount deferred** — `usd_shadow` undiscounted until the measured hit
  rate justifies it (a follow-up, not this milestone).
- **Live smoke is the principal's** — the Opus/Sonnet request shapes are pinned
  via fakes (zero live calls in tests); the first real edition + the battery are
  principal-executed.

## Gate fixes (APPROVED 2026-07-17 — six enumerated)

Option C ratified as the technical default (principal's mandate call pending,
config-only). Six fixes landed on approve:
- **FIX-1 (D1, the wiring):** the analyst joins the one-resolution seam (the
  `ranking._ACTIVE_RANK` twin). `_ACTIVE_ANALYST` holds ONE `effective_seat
  ("analyst")` published by the outermost of generate's stage-entry preflight /
  `run_analysis` / `call_analysis_model`; `_analysis_chat`'s transport,
  `call_analysis_model`'s `cost_fields`, and `run_analysis`'s report lane
  (`fallback_lane_label`) all consume it — a mid-stage `claude` flap can no
  longer fork the transport from the ledger/report. The analyst now also
  participates in the armed fall (effective_seat), so a dead-subscription+armed
  combo falls to api (the sweep's `can_fall` arm flips to "fallback"). Request
  bytes unchanged (byte pins hold).
- **FIX-2 (D2):** the Sonar-skip probe derives —
  `est_synth_probe = ANALYSIS_MAX_TOKENS/1e6 * ANALYSIS_USD_OUT_PER_MTOK` ($0.09),
  not a stale $0.05. (remaining in (0.05, 0.09] now skips Sonar.)
- **FIX-3 (D4):** the battery catches `sqlite3.OperationalError` around
  `connect_readonly` — an empty/unopenable record refuses cleanly (exit 1, zero
  transport), never a stack trace.
- **FIX-4 (NEW-1):** the doctor cost prose is rewritten for the B4 stack with
  figures DERIVED from `llm.SEATS` (Opus/Sonnet, envelope ~$0.90-1.30, cap
  $1.50, shadow over-counts, "measured at first edition + battery"); the stale
  `check_llm_lanes` docstring fixed.
- **FIX-5 (doc):** battery retry-spend disclosure (worst case ≈ 2× the printed
  estimate; the cap prices the single pre-call estimate) in the module docstring
  + `--run` help.
- **FIX-6:** `seat_for_step` raises `ValueError` (naming the step + known
  prefixes) on an unknown step — the silent default now lands on the priciest
  seat (Opus), so a silent default would mean silent Opus spend + a mislabeled
  ledger.
