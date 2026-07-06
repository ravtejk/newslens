# ADR-0012: the analysis organ — code-computed receipts, borrowed-inference enforcement, stage logging

**Status:** accepted (M9 milestone 2)
**Date:** 2026-07-06

## Decisions

### 1. The model emits citation KEYS; code computes everything trust-shaped
The synthesis model receives a code-built source map ([S#] fetched full
text, [C#] cluster excerpts, [R#] Sonar results, [P#] prior briefings) and
may cite only those keys. Provenance tiers (contract §5.1.2), the source
table (§5.1.8), and the retrieval manifest are COMPUTED from the cited keys
— never model-claimed. A key outside the map is fabrication: HARD REJECT,
brief discarded for both consumers, rejection persisted for forensics
(status='rejected' rows in analysis_briefs). Quotes must be verbatim
substrings of retrieved material (whitespace-normalized).

### 2. Borrowed-inference enforcement: drop-with-disclosure, not whole-brief reject
The principal's ruling bans own-voice inference structurally. Enforcement:
an effect whose basis is not in {attributed, mechanical, historical-pattern}
— or that carries no citation — is DROPPED and disclosed in run warnings
("borrowed-inference enforcement: dropped N effect(s)"). The artifact never
carries own-voice inference (the rule holds absolutely); the brief survives
one stray take. Whole-brief rejection is reserved for fabrication, missing
sections, non-verbatim quotes, uncited pinned facts, one-sided
discrepancies, and the banned generic-unknown class.

### 3. Slot-3 demotion precedes the total-failure rule
The analyst holds the medium-vs-quick call for slot 3 (reconciliation
2026-07-06, confirmed M9-M1). Thin material — including NO material —
demotes slot 3 to quick before the no-brief path is considered: for that
slot the tier call IS the outcome, and the writer gets a clean quick-hit
directive instead of a degraded medium.

### 4. Analysis runs log as their own stage
generation_log.jsonl entries gain `stage: "analysis"` with per-story
outcome/cost/fetch/sonar rows (Onna's per-story cost demand). diagnose
splits them out of the generation record into their own readout section —
extraction success rate (the week-1 <30% dep trigger), outcome counts,
cost, and derating flags (escalation class, never absorbed).

### 5. Prompt rendering is explicit replacement, not str.format
The analysis prompt shows a literal JSON example; str.format would read its
braces as fields (discovery's BUG-3 class). Placeholders are replaced
explicitly so the prompt file stays principal-editable without {{}} noise.

## First live contact (the checkpoint run, 2026-07-05 edition)
2 depth stories, both briefs valid, $0.0424 total, 7/7 extraction, Sonar 8
results/story — and the machinery caught a REAL cross-source discrepancy
(meeting date: July 8 per rferl.org vs Wednesday per the cluster), rendering
it as a discrepancy entry, never averaged. The design's first real test
exercised exactly the path it was built for.
