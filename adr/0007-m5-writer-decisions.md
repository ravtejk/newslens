# ADR 0007 — Milestone-5 writer decisions

**Date:** 2026-07-05 · **Status:** accepted · **Milestone:** 5
**Contract:** workspace/debates/2026-07-05--newslens--content.md §5 (the
Content Lead's implementable contract); spec §B steps 4-7, §E-M5.

## Decisions

1. **The model writes prose fields; code assembles the briefing.** The
   narrative call returns JSON: per story, exactly
   headline/lede/why_it_matters/watch_for (+ nullable my_read on B days).
   Code deterministically assembles: title line, at-a-glance list, the
   CANONICAL override label (§5.7 shape) above override stories, the "My
   read:" label itself, per-story meta-lines (corroboration + outlets +
   "Here for:" provenance), and the footer block (window honesty line +
   standing caveat verbatim + variant stamp). Extends M3's
   furniture-ownership split: binding labels never depend on a stochastic
   writer, and three-movement structure/variant conformance become
   mechanical checks.
2. **Chain semantics: `generate` is end-to-end by default** — ingest (fresh
   pull, discovery when keyed) -> rank (idempotent; prior version archived)
   -> narrative -> script. On-demand means fresh: a briefing generated at
   6pm from a morning pull would be quietly stale. `--no-refresh` consumes
   the existing row (narrative-only iteration; also the variant-sample
   path). Rank re-runs are cheap ($0.003) and archived, so re-generating is
   never destructive.
3. **Alternation: A on even date-ordinals** (anchor: 2026-07-05 = dogfood
   day 1 = A), computed from the date, never model-chosen (§5.2). Forcing
   the off-schedule variant = SAMPLE MODE: rendered to
   data/briefings/<date>-variant-X-SAMPLE.md with an explicit header, no
   briefings-row write, no record-log entry as briefing-of-record —
   alternation-of-record stays clean while the principal compares voices.
4. **Instrumentation is a state file, not a migration** (§5.10):
   data/generation_log.jsonl, append-only JSONL — variant, sample flag, word
   counts, per-step costs, disclosure renders, failures. M7 joins by date.
   Rationale: no schema escalation for log data; the file is greppable and
   diffable at personal scale.
5. **Validation split — structure blocks, style warns.** Blocking (retry
   once, then visible failure): JSON shape, story-count == slot-count,
   empty movements, my_read on an A day, revival date absent from the
   lede's first two sentences (mandatory disclosure), spoken override
   elements missing, schedule promises. Warn-grade (§5.9 explicitly:
   budgets warn; fact-subset/hedge flagged for review, never auto-fixed):
   word bands, headline length, banned-string hits, script numerals absent
   from narrative, coarse will-check, single-source lede naming. Frozen
   spoken furniture (caveat + sign-off) is APPENDED verbatim if missing —
   deterministic strings, not facts — with a disclosure warning.
6. **Script pass consumes narrative + label data only** (never raw sources,
   §5.8), enforcing fact-subset by construction at the input boundary and
   by numeral-proxy check at the output.
7. **Continuity distinction (M4 gate must-address):** generate re-queries
   for a prior-briefing ROW; row-exists + context-None = CORRUPT — the
   writer is told continuity is suspended, the run warns loudly, and the
   log records it. Distinguished from "first briefing" (no row). No change
   to memory.prior_briefing_context's QA-pinned shape.
8. **Per-step costs merge into briefings.token_cost** (spec §B step 7):
   generate appends narrative/script steps to rank's existing entry and
   recomputes total. Budget cap checked before EACH call, cumulative within
   the run; a cap abort between passes leaves the row untouched (narrative
   is only persisted together with its script).
9. **Ranker's reason persisted per slot** (RankedSlot.world_impact_reason,
   defaulted) — §5.1 makes it seed material for "Why it matters"; older
   rows lack it and the prompt says so.
10. **Prompt duplication accepted:** variant A and B are two complete files
    (per dispatch); B is generated from A with three surgical differences.
    Drift risk noted; QA's variant-conformance checks and the byte-identical
    reporting-layer rule guard behavior, not file bytes.

## Alternatives rejected

- Model writes the full markdown briefing (labels become model promises —
  exactly what §5.7 forbids).
- Variant state in a DB column (migration for a config-grade fact; parity
  computation needs no storage at all).
- Auto-fixing fact-subset violations (contract: flag for review).
- Skipping repeated stories at the writer (delta-treatment is the writer's
  job; skip/keep decisions belong to ranking).

## Principal-checkpoint amendments — 2026-07-05 (DECISIONS.md same date)

1. **Writer up-tier to GPT-4o** (WRITER_MODEL constant + writer-rate cost
   math; ranking deliberately stays gpt-4o-mini). Executes the content
   contract's pre-registered trigger: 4o-mini failed register-holding at the
   day-1 checkpoint ("quality of analysis and prose was not good enough").
   Next fallback rung (Claude-class) is a one-constant change. Honest cost
   lines updated (~$0.03-0.06/briefing writer passes; actuals in the M5
   report).
2. **Variant B commit-or-null hardened** in narrative_variant_b.txt: a
   my_read is a directional call — "I expect X because Y; I'm wrong if Z" —
   with weather-speak named as a contract violation and explicit null as the
   professional alternative. **The alternation clock RESTARTS at the
   up-tier** so day-30 compares real variants on the real model: restart
   logged in generation_log.jsonl; the parity anchor already makes
   2026-07-05 = A, so day 1 of the restarted window is today's A-of-record.
3. **No-threads SAMPLE** (`generate --no-threads`): the cold-start view —
   thread list, per-story matched_memory, and revival data stripped from a
   COPY of the inputs (tags kept), so prompt, validators, assembly
   meta-lines, and script labels are consistently thread-free; labeled
   header + `<date>-no-threads-SAMPLE.md`; always SAMPLE (record untouched).

4. **Mechanism-depth is universal** (principal amendment, queued same day):
   both narrative prompts now carry a standard §5.4-layer obligation — every
   story's "Why it matters" names the enabling mechanism or established arc
   the event belongs to (stable-background or prior-coverage lane; contested
   mechanisms named as contested). Consequence-listing without mechanism is
   named in-prompt as the product's defining failure mode (it is what
   triggered the GPT-4o up-tier). Not mechanically checkable — prompt
   directive + day-14 quality read. The Content Lead's contract §5.4 gains
   this by reference (no re-convene for one principle, per the CoS).

## Amendment 2 — 2026-07-05: the principal's editorial review (A1-A6)

Binding package (contract file, final section; DECISIONS.md same date).
Implementation decisions:
- **A1:** ACTIVE_VOICE="A"; alternation ended (alternation_end logged;
  parity code dormant for reproducibility); variant_b prompt retired in
  place with a header note; --variant B still renders comparison SAMPLES.
  The prediction rule replaces the ladder's prediction rungs in prompt A:
  own voice never predicts; forward-looking = attributed or
  reporting-backed; "will" only for scheduled certainties; Watch-for =
  observables. No methodology self-reference; the footer variant stamp
  retired with the window (samples carry file-header labels).
- **A2:** model proposes tiers, code enforces sanity (story 1 full, 2
  medium, 3 medium-or-quick by model judgment, 4+ quick). Quick hits:
  headline + 1-3-sentence lede, no movements — a movement field on a quick
  hit is a validation ERROR (structure blocks); tier bands warn (full
  250-550 / medium 100-300 / quick 15-110 words). Assembly renders quick
  hits with trust furniture (meta-line) and no movement labels. Revival
  text-disclosure check unchanged (applies to any tier's lede).
- **A3:** prompt rules + warn-scans from the principal's own examples
  (TRUISM_WARN_STRINGS, MORALIZE_WARN_STRINGS); quotes are legal, so
  warn-grade by design; the rest is day-14 read material.
- **A4:** intro formula with the principal's model intro verbatim as the
  prompt example; dateline must not open the script (warn if within the
  first 60 chars); MECHANICAL_TRANSITIONS warn-list.
- **A5:** spoken single-source attribution presence check REMOVED (editorial
  judgment); spoken revival date downgraded hard->warn (the TEXT disclosure
  stays hard — A5's hard list omits revival, resolved toward text-hard/
  spoken-licensed). Unchanged hard: fact-subset, hedge preservation, spoken
  override elements, schedule-promise ban, correction disclosures.
- **A6:** settings.threads_steer_selection (sources.yaml, default false,
  strict-keys validated, doctor-rendered). Steering-off = matched_memory is
  recognition-only in personal_score (the M4 zero-influence pattern);
  recording, revival, reference updates, and continuity all continue in
  persist(). meta.threads_steer_selection logged per run. --no-threads
  SAMPLE unchanged (that strips recognition too — the cold-start view).
