# ADR 0009 — M6 fix loop + Round-2 text-quality package (A7/A8)

**Date:** 2026-07-05 (evening) · **Status:** accepted
**Contract:** content contract "PRINCIPAL REVIEW ROUND 2" (A7/A8 active;
9-item podcast backlog PARKED verbatim — no audio work in this loop).

1. **BUG-8 closed:** post-edit re-validation now lives inside the degrade
   seam — a validator-violating edit (live class: editor clipped a mandatory
   revival date) discards the edit with a disclosed "editor: output FAILED
   validation — degraded to the writer's draft", re-validates the draft, and
   only a draft that ALSO fails raises a logged GenerateError. Never a raw
   crash (ADR-0008 §9 honored end-to-end).
2. **Slot-dup guard (code-owned):** deterministic near-duplicate detection
   across SELECTED slots — significant-token Jaccard over title+summary,
   stopword-stripped, threshold DEDUP_JACCARD = 0.45. **Calibration record,
   reconciled at the M6 gate (2026-07-05):** the loop's first guess (0.55)
   failed its own emulated live pair pre-normalization (0.43); stopword +
   plural normalization was added in response. Two post-normalization
   figures were then reported for that emulated pair (0.46 in this ADR's
   earlier draft; 0.583 in the loop report) — the pair was ad-hoc and is NOT
   reproducible, so both are superseded by the reproducible reference: the
   pinned QA fixture pair (test_ranking_selection.py, chip-export write-ups)
   measures **J = 0.667** under the shipped code (gate-corrected: the CoS's
   first "reproducible" figure, 0.833, was computed against a reconstructed
   rather than actual fixture string — the fourth figure in this record and
   the reason the figure is now pinned in the suite itself, carryover 20a);
   distinct same-domain pairs
   measured <0.35 during the loop (cross-domain reproducible pair: 0.000).
   The threshold sits above the distinct ceiling with wide margin below the
   reproducible true-dup; day-14 re-tunes against accumulated meta.dedup
   data, as a reviewed diff. Earlier-ranked
   selection wins; next-ranked non-duplicate primary is promoted; a dropped
   override instance leaves its slot unfilled (normal outcome). Disclosed in
   run warnings + meta.dedup.dropped.
3. **Warning retention:** generation_log entries (ok AND failed) carry the
   full report.warnings array + declared framings — quality scans no longer
   evaporate with the terminal.
4. **A7 framing menus:** WHY_FRAMINGS (7) / WATCH_FRAMINGS (4) finalized in
   generate.py + prompt A; the writer declares why_label/watch_label per
   movement story; validators enforce MENU MEMBERSHIP (unknown = error);
   assembly renders the declared labels (furniture stays code-built); the
   editor may not change labels (code guard); all-one-rhythm across >=3
   movement stories warns. Movement-count/tier rules unchanged.
5. **A8:** editor-prompt AUDIT result: the never-add-facts constraint
   existed in binding language ("THE ONE HARD CONSTRAINT — YOU NEVER ADD
   FACTS... it does not exist for you") — retained and hardened ("contract
   breach, not a style choice"; knowledge/memory/inference named). Added
   priority-0 DELETE-ON-SIGHT with the principal's canonized examples +
   specific>abstract as the core operation + lead-depth pressure; code-side
   lead-near-slot-2 warn (<=240 words).
