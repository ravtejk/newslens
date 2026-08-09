# ADR-0023 — NL-17 steering measurement: metrics, trigger table, migration integrity

**Status:** **PROPOSED-FOR-RATIFICATION** — every number in this file is proposed,
none is ratified. Ratification rides the **NL-17 program-M2 checkpoint**; the
thresholds **freeze before the first steered run** and no post-hoc change is
lawful without a logged principal ruling (data's own anti-gaming clause).
· source: `workspace/debates/2026-08-02--newslens--data.md` §4 Deliverables 1–3
· charter: NL-17 program (principal-approved 2026-08-02), program-M2 §10
· landed dark by NL-17 M2 alongside migration 0026.

---

## READ THIS FIRST — metric-M# is NOT milestone-M#

The record uses `M1…M6` for **two unrelated things** and they collide constantly:

| Reads as | In this file | Elsewhere |
|---|---|---|
| **metric-M1 … metric-M6** | the measurement instruments below | data council §4 Deliverable 1 |
| milestone-M0 … milestone-M4 | **never used in this file** | engineering council §5; the program plan |

Every `M#` on this page is a **metric**. The engineering council's "M3 — steering +
looks" is **program-M2**, the milestone that landed this file. Where this document
must name a milestone it writes it out: *program-M2*, *program-M3*.

**One further numbering trap, recorded so it is not re-derived:** the engineering
council reserved migration slot **0025** for `vocabulary_moves`. That slot was spent
by `0025_follow_settle_events.sql` during program-M1. The ledger is **0026**.

---

## Why this file exists at all

A pre-registered threshold that lives only in a debate transcript is not
pre-registered — it is remembered, and remembering is what the record calls
"post-hoc threshold moves". Freezing the table **in-tree, versioned, before the
first steered run** is the anti-gaming mechanism, not the paperwork about it. The
program's honesty block is explicit that the gate is **designed around receipts,
not rates** (day-14 ≈ 7 runs; distinguishing a 0.15 vs 0.35 selection rate needs
≈57 candidate-appearances per arm ≈ 15+ weeks), so these instruments are built to
be *deterministically conclusive per run*, not statistically powered.

---

## 1. The metrics

### metric-M1 — Steering flips (primary instrument for criterion (c)'s "better odds")

- **Definition.** Per ok run, replay selection with entity steering zeroed, all
  other inputs identical (deterministic re-score from persisted per-cluster
  receipts). A slot whose occupant changes = **one flip**. Report flips/run and
  which entity.
- **Denominator.** ok `ranking_runs` post-migration (7 slots each — assumption A1).
- **Counter-metric.** Displaced-story profile (world_impact + tag matches of what
  the counterfactual would have run) + metric-M4's starvation floor.
- **Gaming vector (Stig).** Upstream leakage — if entity identity influences
  cluster formation or world_impact scoring, the "steer-off" replay is contaminated
  and flips **undercount**. Mitigation: receipts captured at the scoring layer
  pre-steer; engineering attests cluster formation is steering-blind; audit
  spot-checks cluster sets.
- **Bound (PROPOSED).** Rolling **10-run mean ≤ 1.0 flips/run**; **single-run max 2
  of 7 slots**. Reading: followed_analyst-style — nudges the marginal slot, not an
  editorial takeover.

*As built (program-M2):* `replay.flip_replay` is the instrument. The bound is
**reported, never clamped** — a clamp would hide the breach the trigger table
exists to catch. The gaming vector is partly closed by construction: cluster
formation is the model's, and the alias match runs *after* it, so entity identity
cannot reach world_impact. The **injected reserved look is the one exception and
must be counted as such** — an injected cluster did not exist in the steer-off
world, so its appearance is a formation effect, not a scoring flip. Per-cluster
`quiet_zero` is persisted in the run envelope, and `flip_replay` holds the
quiet-zero exclusion identical on BOTH slates — a flip the live pipeline cannot
produce is never attributed to steering. An envelope predating the receipt is
NON-ATTRIBUTABLE (`quiet_receipt: "absent"` + rendered caveat) and is excluded
from the metric-M1 count (gate R2 ruling 2026-08-08, ratified mechanism; the
pre-receipt class is empty on the founder record, 0/51 rows).

### metric-M2 — Slot-guarantee falsifier (criterion (c) red line)

- **Definition.** Thin-basis selection = a selected entity story whose steer-off
  counterfactual rank **> 14** (2× slot count) — it wasn't near the line; steering
  carried it from depth. Fallback proxy if rank unavailable: zero tag-vocabulary
  match **AND** world_impact ≤ that run's unselected median. **Falsifier: same
  entity thin-basis-selected 3 consecutive appearance-runs = REVIEW; 4 = FIRED.**
- **Denominator.** Consecutive ok runs in which the entity had a candidate
  appearance (no-appearance runs neither extend nor break the streak).
- **Counter-metric / backstop.** Thin-basis share of the entity's selections
  **> 25%** over trailing 14 ok runs → **FIRED** regardless of streak shape (closes
  alternation gaming).
- **Gaming vectors.** Streak-reset alternation (closed by the backstop); post-hoc
  threshold moves (closed: thresholds freeze before the first steered run; any
  change needs a logged principal ruling).
- **False-positive honesty (Stig).** A genuinely hot entity selects on NON-thin
  basis (tag-relevant coverage or high wi) — legit streaks do not trip this by
  construction.

### metric-M3 — No-stacking audit (b) + XOR scan (a)

- **Definition.** Per ok run, per cluster (**not just selected** — below-line
  corruption still reorders ranks): recompute expected personal/combined from
  persisted inputs under the shipped law; require **exact equality** with recorded
  values (combined is 4-dp-rounded in code — match on that), **AND**
  entity-matched clusters' effective weight == the entity weight **alone**: never
  tag+entity compounded, never multiple increments for multi-thread matches.
  **XOR scan:** no concept present in both tag vocabulary and entity registry —
  run at migration, on every catalog change, and weekly.
- **Denominator.** All clusters × all ok runs; all migrated concepts.
- **Counter-metric.** Receipt completeness = runs recomputable / ok runs. **Must be
  100%.**
- **Gaming vector.** Vacuous pass via missing receipts → **missing = FAIL,
  default-deny** (the audit's own blindness is a violation).

*As built (program-M2):* `replay.audit` (checks C1–C4) and `replay.xor_scan`. The
audit recomputes the scoring pool from a **second implementation written from the
law**, not by calling `ranking.personal_score` — an audit that calls the function
under test only proves the function agrees with itself. C3 reads the **pool**, not
the capped score: `min(base, 1.0)` can launder a sum, so the check is
contribution-level and cap-independent (Rook's tooth).

### metric-M4 — Starvation / displacement floor (zero-sum honesty)

- **Definition.** Per followed tag-topic, selection rate (selections / candidate
  appearances) rolling 10 runs vs its frozen pre-program baseline (**runs 22–47**);
  **relative drop > 50%** with ≥10 appearances in both windows → **review flag**.
- **Denominator.** Candidate appearances per topic.
- **Counter-metric.** Aggregate mean drift (catches broad shallow starvation the
  per-topic floor misses); per-topic **MIN** reported, not just mean (mean-masking).
- **Gaming vector.** Baseline shopping — closed: the baseline is the banked
  zero-weight era, already receipted read-only in the NL-14 package.

### metric-M5 — **ABSENT IN SOURCE**

The program charter names "metric definitions M1–M6". The data council's transcript
defines **M1–M4** in Deliverable 1 and references **M6** in the trigger table and
the readout spec. **There is no M5 anywhere in the source.** It is recorded as
absent rather than invented: fabricating a metric to fill a numbering gap would be
exactly the failure this whole measurement design exists to prevent. If the
principal intended an M5, it needs authoring — flagged at the program-M2 checkpoint.

### metric-M6 — Ops cadence (ok editions per rolling 7 days)

- **Definition.** Ok editions per rolling 7 days, reported with runs-vs-editions
  retry noise and **run timestamps disclosed** (bunching visible — the padding
  gaming vector).
- **Trigger.** < 3 ok editions in a rolling 7 days → T6 below.
- **Why it is a program metric and not ops trivia.** Evidence windows are scored
  only if an edition of record actually ran inside them. A paused clock never
  launders a missed window.

---

## 2. The pre-registered trigger table (Dana's spine — number → action)

**The asymmetry rule (F3, adopted):** deterministic violations (stacking, XOR)
deserve **auto-off**; statistical drifts (displacement, flip-mean) get
**review-first**, because news-cycle confounds produce honest breaches and an
auto-tripwire on a confounded metric invites threshold-gaming.

| # | Condition | Pre-registered action |
|---|---|---|
| **T1** | metric-M3 stacking/XOR **FAIL** on any run, incl. **any receipt gap** | `threads_steer_selection` → **off before next generate**; fix loop; the affected edition stays of record with a disclosure note |
| **T2** | metric-M2 **FIRED** (4-streak or 25% rolling) | entity steering weights → **0**; program-gate review of criterion (c); restart only on re-ratified bounds |
| **T3** | metric-M1 bound breach (10-run mean > 1.0, or any run > 2 flips) | review **within one session**; mechanism-confirmed (not news-shock) → weight step-down; second confirmed breach → T2 treatment |
| **T4** | Migration integrity **FAIL** (§3) | **rollback to pre-migration backup BEFORE first steered generate** |
| **T5** | metric-M4 floor breach | **review, not auto-off** (confound check first — the asymmetry rule) |
| **T6** | metric-M6 drought (< 3 ok editions in rolling 7 days) | CoS same-day flag to principal; windows still **MISS** if unrun — a paused clock never launders a missed window |

---

## 3. Migration integrity (T4 — one-time + weekly re-check)

- **Concept conservation** — every migrated concept appears exactly once across the
  two vocabularies.
- **Row conservation** — thread / delta / state row counts unchanged through
  migration.
- **Render conservation** — one historical edition re-renders **byte-identical**.
- **Backup exists** — a pre-migration DB backup (NL-75 precedent:
  `data/newslens.db.pre-0011`).

*As built (program-M2):* nothing here runs yet, because **program-M2 writes zero
`vocabulary_moves` rows** — there is no migration to check the integrity of until
the program-M3 approval batch. `replay.xor_scan` is the concept-conservation half
and is live now. The remaining three are program-M3's, and the backup is the
principal's restart-time act.

---

## 4. What program-M2 froze, and what it did not

**Froze:** this file — the definitions, the bounds, the trigger table, the
integrity block — landed in-tree, versioned, before any steered run exists.

**Did not freeze — and these are open at the checkpoint:**

1. **Ratification itself.** Every threshold is PROPOSED. Until the principal
   ratifies, no trigger has authority to fire.
2. **metric-M5.** Absent in source (above).
3. **metric-M1's injected-look accounting.** A reserved look that the ranker never
   formed is a *formation* effect; counting it as a scoring flip would inflate the
   flip count against its own bound, and not counting it would hide the mechanism's
   main effect. **Proposed:** report both — `flips` (occupant changes, steer-off
   comparable) and `formations` (injected looks that reached a slot) — as separate
   numbers, never summed. Needs a ruling.
4. **The replay durability bound.** `ingest.upsert_item` UPDATEs `source_items.title`
   in place on every later sighting of a URL (`ingest.py:226`). A revised headline
   inside the candidate window therefore breaks byte-faithful rebuild for that run,
   permanently. The instrument reports the drift loudly (`items_sha256` is stored
   separately from `prompt_sha256` so a mismatch localises) and the audit records
   `recomputable: false` rather than guessing. **Replays are a fresh-run instrument,
   strongest the same day.** Under metric-M3's "receipt completeness must be 100%"
   this makes a late replay a FAIL, which is correct by default-deny but means the
   audit must be **run promptly**, not batched at day 14.
