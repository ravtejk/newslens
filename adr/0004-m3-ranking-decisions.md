# ADR 0004 — Milestone-3 ranking + corroboration decisions

**Date:** 2026-07-04 · **Status:** accepted · **Milestone:** 3
**Contract:** `workspace/debates/2026-07-03--newslens--product.md` (taxonomy/
override), spec §B steps 2-3, §E-M3.

## Decisions

1. **LLM/deterministic split.** The model decides only semantics: same-story
   clustering, which listed tags/threads a cluster genuinely matches, and a
   0-10 world-impact score with a one-sentence reason. Code decides weights
   (topic 1.0 / domain 0.5 / memory 1.0 / followed boost +0.35, personal
   share 0.55), slot selection, the override gate (threshold 8, cap 1), and
   corroboration. Every constant is a reviewed diff in `ranking.py`, not an
   env var. "Why did this rank?" is answerable from stored data.
2. **Tags stay in `sources.yaml` for M3; no `tags` table yet.** The contract
   §A defines `tags(id, name, level, status)` — deferred to the milestone
   that ships the tag CLI verbs (M4+, contract §F). Rationale: `interests.
   broad/granular` maps 1:1 to domain/topic; the file is already the
   transparent, principal-editable surface (same pattern as `memory.md`);
   creating the table before the verbs exist would mean two sources of truth
   with no manager. `status=inactive` soft-delete arrives with `tag drop`.
   Flagged in the M3 report — this is an interpretation, not spec'd.
3. **Interests seeded exactly per contract §C** with one honest correction:
   §C's prose says "~59" topic tags but its own list enumerates 40; seeded =
   the 40 listed + BRICS (deduped) + the 5 borderline topic-twins = 46 topic
   + 14 domain. The tally discrepancy is noted for the principal's checkpoint
   review rather than silently reconciled. Memory items (14) deliberately NOT
   seeded — M4 scope. Latin/South America overlap left as a flagged comment
   for the principal (contract explicitly refused to resolve it unilaterally).
   **Amended same-day (checkpoint outcome, 2026-07-04):** the principal merged
   "South America" into "Latin America" → current tally = 46 topic + **13
   domain = 59**; `sources.yaml` carries the authoritative inline merge note.
   The principal also explained the "~59" prose: it predated the live-thread
   split — no gap to fill.
4. **LWW same-URL attribution ruling (M2 carryover 5): keep last-writer-wins;
   corroboration counts stored outlets.** Consequences: a URL syndicated
   across N of our own feeds counts ONCE per day (whichever outlet's snapshot
   won); if a wire-flagged republisher won the row, the item is excluded from
   the count entirely. Both failure directions UNDERCOUNT — the conservative
   direction for a trust label (never inflates corroboration). The
   alternative (multi-outlet attribution per URL) needs a schema change and
   real-data evidence it matters; revisit with week-one data per NOTES item.
5. **Sonar-discovered items are citable but never "named outlets"** in
   corroboration counts — the label's words are "named outlets" and discovery
   hits are not from the principal's list. Zero-named clusters get an honest
   floor label ("treat as a single source").
6. **Override instrumentation = `ranking_runs` append-only table (migration
   0003), not a briefings column.** ALTER ADD COLUMN can't be re-apply-safe
   (0001's binding migration convention), and the day-14 recalibration needs
   fire/no-fire rates OVER TIME across idempotent re-ranks — a log, not a
   last-write cell. Failed runs log too (`status=failed`).
7. **Ranking prompt gets `id | outlet | title` lines only** (no excerpts):
   clustering+matching works on titles at ~1/5 the token cost; excerpts join
   at M5 for the selected stories' narrative only. Item window: trailing 36h,
   capped at 550 most-recent (cap hit -> visible warning).
8. **`briefings-history-before-overwrite` goes live NOW** — rank writes
   briefings rows, so the idempotent-re-run archival rule (ADR-0001, spec'd
   for M5-7) binds from this milestone. Implemented in `ranking.persist`.
9. **`rank` is its own CLI verb** (not folded into `ingest`): pull and
   editorial pass are independently re-runnable and independently priced;
   M5's `generate` chains them. `--date` labels the briefing row; the
   candidate window is always computed at run time per amendment B below
   (backdating semantics documented in the CLI help honestly rather than
   pretended away).
10. **No new dependencies.** OpenAI called via urllib JSON-mode chat
    completions, same shape as discovery's Sonar call. One retry total
    (network-shaped or validation), then a visible `RankingError`.

## Alternatives rejected

- Letting the LLM pick the 5 slots directly (uninspectable; override cap and
  label become model promises instead of code guarantees — Kass's dissent
  demands guarantees).
- `ON CONFLICT` upserts and OpenAI SDK (same reasons as ADR-0003: expression
  index + boring-first, and no new deps mid-loop).
- Multi-outlet URL attribution now (schema change on a hunch; undercounting
  is the safe failure while we gather real data).

## Principal amendments — 2026-07-04 (folded in before QA)

**A. Followed-analyst boost is a bounded odds-shifter, never an auto-include**
(principal's words: "not enough to automatically be included in the briefing,
but does increase the chances"). The mechanism is generic (any source can
carry `followed_analyst: true`) and structurally incapable of guaranteeing a
slot: the boost only adds +0.35 to the personal score inside the same sorted
selection — there is no followed⇒slot branch anywhere. Pinnable invariants
(QA): `FOLLOWED_BOOST < TOPIC_WEIGHT`; a followed-only cluster scores
personal 0.35 vs 1.0 for a topic match, so `combined(followed-only, world 10)
= 0.6425 < combined(topic-match, world 3) = 0.685` — a weak-scoring followed
item MUST lose to a strong non-followed candidate, and `select_slots` has no
override/exception path for followed content (it is also excluded from the
urgency-override pool, since it carries a personal signal).

**B. Recency window for briefing candidates.** Eligible = occurred/developed
since the last briefing OR within the cap, whichever is shorter:
`window = min(now - last_briefing.generated_at, RECENCY_CAP_DAYS=14)`
(principal gave 10-14; 14 chosen, one reviewed constant). First-ever briefing
= full cap. "Last briefing" excludes the target date's own row, so an
idempotent re-rank uses the window since the PREVIOUS briefing, not since its
own prior version minutes earlier. "Developed" anchors on fetch time
(first-seen) — published_at is too unreliable across feeds. Cluster
eligibility ("newest item in-window; an old story with a fresh development
qualifies") holds by construction: the LLM only sees in-window items, so an
old story enters through its fresh items. Honesty requirement implemented:
the run report and CLI always print `candidate window: Xd (basis); ingested
history: Yd`, with an explicit warning when history < window — early runs
must never imply a lookback the corpus doesn't have. Repeat-suppression
("don't re-cover what the last briefing said unless it developed") is
deliberately NOT here — it is M4/memory scope. This supersedes §7's original
trailing-36h window.

## Fix-loop-1 amendment — 2026-07-04 (M3 QA: BUG-5, BUG-6, clustering repair)

**BUG-5 → migration 0004, not an amended 0003.** ranking_runs' append-only
promise is now structural (the same UPDATE/DELETE abort-trigger pair 0001
gave briefings_history). A new migration because 0003 is already applied to
the real DB, which carries the ingested history feeding the recency window —
amending 0003 in place would enforce nothing there short of a destructive
reset. 0004 adds no new semantics: it enforces exactly what 0003 was approved
to be (noted for the milestone checkpoint, per QA).

**BUG-6 → every post-connection RankingError logs.** run_rank now wraps its
whole body: no-items refusals, prompt render failures, budget aborts, and
LLM/validation failures all append a status=failed ranking_runs row (exactly
one — the previous inner LLM-only logging is removed). Pre-connection
refusals (no key, no interests, sources problems) have no connection to log
through, by construction. Agreed with QA's recommendation; no contract
narrowing.

**Clustering repair — the live blocker.** 2/2 live runs on ~600 real items
failed validation because the model re-used item_ids across clusters
(stories straddling topics). Two-part fix per the dispatch contract:
1. Prompt hardening: rule 1 now states the partition constraint as a HARD
   CONSTRAINT with the "place it where it's most central" instruction.
2. `repair_duplicate_ids` — a disclosed deterministic repair for exactly this
   violation class, running BETWEEN parse and validation: keep each item's
   first cluster assignment, drop later duplicates, drop (and disclose)
   clusters emptied by the repair. Every repair is counted, rendered as a
   visible run warning, and persisted in ranking_runs.meta.repairs — never
   silent. All other violation classes still hard-reject, and
   validate_payload RETAINS its own duplicate rejection as a backstop behind
   the repair (defense in depth; also keeps QA's direct-unit pins meaningful).
   Consequence QA must re-pin at re-verify: the end-to-end duplicate-payload
   test frozen against pre-fix behavior
   (test_invalid_payload_twice_is_the_live_failure_end_to_end) now takes the
   repair path instead of rejecting — that single flip is the intended
   behavior change; the two direct validate_payload duplicate pins stay green.
