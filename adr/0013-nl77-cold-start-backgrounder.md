# ADR-0013: NL-77 the thread cold-start backgrounder — the entry-zero baseline

**Status:** accepted (NL-77 milestone 1)
**Date:** 2026-07-17
**Spec:** Executive Brief workspace/briefs/2026-07-17--newslens--cold-start.md

## Context

When a thread is newly followed (or first opened with an empty ledger) NewsLens
has no record to stand on. NL-77 fills that gap with a one-shot "How we got here"
BACKGROUNDER — a distinct genre the Executive Brief calls **entry-zero**:
synthesized from external background the product never itself covered, so it can
NEVER license a repetition-word continuity claim (migration 0014's fourth
provenance class, `external-synthesis`).

## Decisions

### 1. Entry-zero rides its OWN table (thread_baselines), not thread_deltas

The Brief says the baseline "joins the ledger as provenance-typed entry-zero,"
and 0014 anticipates it graded `external-synthesis`. Both are honored — but the
entry-zero is a *side table* (migration 0017), not a `thread_deltas` row. Three
load-bearing reasons, the same shape as 0003/0012/0014's side-table precedent:

1. **thread_deltas.verdict is CHECK'd `IN ('advances','reverses')`** (0010, "M1
   gate F1: defense-in-depth"). A baseline is neither — it is the founding floor,
   not an edition delta. Widening that CHECK means recreating the trust-critical
   append-only ledger past its RAISE(ABORT) triggers — exactly the carve-out
   0014's header ruled disqualifying.
2. **A baseline has no edition.** Its cite currency is `(baseline, Jul 14)`, not
   an edition date. Stored as a delta it would sort into every edition-keyed
   read: the Today arc's "then" leg (violating the anti-obligation invariant —
   day-one arcs must stay dead), the deep-view "story so far" timeline, and any
   HSR numerator. A side table keeps entry-zero OFF all of them **structurally** —
   the invariants hold by construction, not by a filter a future read could
   forget.
3. **The grade is fixed on the row** (CHECK `provenance = 'external-synthesis'`),
   not looked up in `thread_delta_provenance` (which keys on a `delta_id` this
   genre never mints). 0014's class is not dead: it still grades "any delta
   inheriting baseline diction," a later milestone.

The `external-synthesis` licensing exclusion in `has_predating_antecedent` is
already correct — and a baseline is additionally never a delta, so it is excluded
a fortiori. The grade travels with the baseline row; the semantics are identical,
the physical home differs.

### 2. Lifecycle: versioned append-only, newest-wins (the thread_state model)

`pending` (the §F intent, written on follow / first-open) → `ready`
(backgrounder + a seeded standing state) or `failed` (the honest refusal). A
retry after a failure is a NEW row; a UNIQUE(thread_id) key would forbid that, so
append-only + newest-wins is the choice, matching 0010's thread_state.

### 3. The standing-state seed lives on the baseline row, not in thread_state

`thread_state.state_text` is validated against LEDGER dates (validate_state
hard-rejects a cite to no ledger entry). A baseline has no ledger — its only
currency is `(baseline, <date>)`. So the seeded day-one state lives on the
baseline row (`state_seed`); the thread-page "Where this stands" falls back to it,
disclosed as external synthesis, only until a real record-established
thread_state exists.

### 4. Generation is ONE analyst-model call, behind an injectable seam

The existing analyst machinery pointed backwards (`analysis.call_analysis_model`,
GPT-4o, ~$0.01-0.02) — a new prompt `prompts/thread_baseline.txt`. Seam-neutral:
the provider flip (Claude migration) is config, not code here. Validation teeth
(`_validate_baseline`) reject the bare-continuity poison and any non-string field;
a rejection writes a `failed` row (never fabricated content). Spend is durable on
the row's `cost_usd`; a budget skip writes nothing (the pending intent stands).

### 5. Intent gate is §F explicit-action only; spend stays behind a command

The wired §F intent paths in this milestone are **follow (`memory add`)** — which
writes a `pending` row for a cold-start thread ($0, no LLM) — and the explicit
`newslens memory-baseline` command, which materializes it. **First-open is NOT
yet wired:** `capture_baseline_intent` is the topic-keyed entrypoint ready for it,
but it has **zero src call sites** — the server SPA renders all thread pages at
once, so there is no per-open write signal; first-open capture awaits the
live-phase `log_thread_view` server-side emission. Generation spend is behind the
explicit `memory-baseline` command (`--thread-id` for one just-followed thread,
`--all` for the retroactive backlog) — never a silent LLM call from a memory verb.
This reconciles the Brief's "eager on follow" with the money/checkpoint discipline
(spend behind an explicit action; a checkpoint the principal runs). A
read/thread_view event NEVER triggers a baseline (tested).

## Rejected alternatives

- **A `verdict='baseline'` thread_delta** — requires recreating the CHECK'd
  append-only ledger; leaks into arc/timeline/HSR reads. (Decision 1.)
- **Writing `concept_explanations`** (migration 0016's comment guessed NL-77
  would) — that is the explained-once GLOSSARY registry (explain a term once,
  don't re-explain), a different concern from a thread's founding history. Out of
  the Brief's scope for this milestone; a separable later rung.

## Deferred / flagged to the gate + principal

- **The retroactive `--all` sweep is a principal checkpoint** — thread
  renames/deletes (the junk sweep, TRACKER decision board item 1) land BEFORE
  baselines. The command is built; it was NOT run against real data.
- **The reader-facing "How we got here" render** reuses the approved
  `deep-section` component but introduces new CSS classes
  (`baseline-disclosure`, `baseline-cite`, `baseline-seed`) — flagged for the
  design/mockup confirmation (mockup gate).
- **First-open capture AND generation** both ride the `log_thread_view`
  server-side emission wiring (not yet live). The intent entrypoint
  (`capture_baseline_intent`) is ready but has no src call site yet; follow +
  the explicit command are the only wired paths this milestone.
