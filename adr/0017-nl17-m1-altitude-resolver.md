# ADR-0017 — NL-17-M1 increment A: the altitude resolver + the falsifier instrument

**Status:** accepted (gate-pending) · 2026-07-17
**Milestone:** NL-17-M1 "the altitude slice", increment A (ratified roadmap item,
follow-altitude round product-4 2026-07-16). Builds on the B1–B4 provider seam
(ADR-0014/0015/0016).

## Context

The follow-altitude round ruled v1 a HYBRID: at follow time the system proposes
ONE follow at the best altitude, pre-selected and NAMED IN WORDS, with the other
rung + "just this story" one tap away. Kass's pre-registered falsifier gates that
shape — dry-run the auto-altitude pick over the principal's existing followed
threads; **>1-in-5 primary-entity misses flips v1 from a pre-selected default to a
blank picker.** This increment builds the resolver and the dry-run instrument and
STOPS: the falsifier verdict + a principal-approved follow-moment mockup gate all
UI/migration work (mockup-gate law; the NL-77 waiver was one-time and explicit).

## Decision

### 1. The resolver contract (two rungs only)

`follow_altitude.resolve_altitude(thread) -> AltitudeResult` emits, for one
followed thread (title + whatever ledger/state context exists):

```
{altitude: "entity" | "storyline", primary_entity, disclosure, confidence}
```

- **Two rungs, entity + storyline.** `storyline` is the thread/topic tier per the
  product-4 transcript — the ongoing story at proper altitude (e.g. "the
  Volkswagen job-cuts story"), never the headline string. `industry` and `region`
  are hypotheses, deferred to the NL-17/18 taxonomy round as unproven.
  `ALTITUDES = ("entity","storyline")` is the no-new-vocabulary tripwire: the
  validator REJECTS any third rung (including `industry`), so a widened vocabulary
  needs a code change the QA pin flips red on.
  - **Records correction (2026-07-17):** the dispatch's CONTRACT wrote the second
    rung as `industry`, contradicting the product-4 adjudication ("entity +
    storyline"). The implementer flagged the discrepancy; the **principal ruled
    STORYLINE** — the adjudication stands, `industry`/`region` stay deferred. The
    error is corrected in DECISIONS 2026-07-17 ("multi-user A–D APPROVED + THE
    STORYLINE CORRECTION"). This ADR and the code implement the ruling.
- **The disclosure line names the altitude in words** (Kass's adopted clause): a
  default a reader confirms with one tap is silent inference unless the words on
  screen say what they got. A missing/empty disclosure is a hard validation
  failure.
- **confidence ∈ {high, medium, low}** so the falsifier report flags the
  ambiguous picks (the ones to scrutinise for the miss count).

### 2. No stored state — the output is a REPORT

The resolver writes NOTHING: no selection weight, no follow vocabulary, no
ranking touch, no DB write. NL-17 acceptance (a)–(d) (one-vocabulary XOR,
MOVES-never-copies, no-stacking, A6 steering OFF behind the NL-14 gate) are
untouched **because v1 stores nothing this increment** — it mints one concept in
one vocabulary from day one by minting none. The falsifier reads the record
read-only and writes only an artifact under `<DATA_DIR>/follow_altitude/`.

### 3. The seam: a new `follow_altitude` seat (subscription-default Haiku)

The model call rides a new `follow_altitude` row in `llm.SEATS` — Haiku 4.5, the
same `_HAIKU_SUB` row as rank/editor/script (subscription-first mandate; the api
lane is the registered fall-over via `NEWSLENS_LANE_FOLLOW_ALTITUDE=api` or the
armed `NEWSLENS_LANE_FALLBACK=api`). One `effective_seat` resolution per call,
threaded through the gate + both transport attempts + every cost row (the B3-D6
one-resolution law); prompt-shaped JSON rides the corrected-retry law (rank's
twin — a malformed answer takes one corrected retry echoing the exact failure;
every billed attempt lands in the cost_sink). thinking/effort None (a mechanical
classification, not reasoning). **Deliberately NOT in `_STEP_PREFIX_SEAT`:** it is
not a `generate` edition step, so it is never reachable through `seat_for_step` /
`generate.call_llm`.

### 4. The falsifier instrument (dry-run default, principal-executed)

`follow_altitude.main` / `scripts/follow-altitude` mirrors `scripts/battery`:
- **Read-only** on the record (`db.connect_readonly`); refuses cleanly on an
  absent/unopenable DB.
- **Dry-run default** — ZERO calls, ZERO writes: the thread list (followed =
  `status IN ('active','dormant')`, the `threads_awaiting_baseline` predicate) +
  the per-thread `usd_shadow` estimate + the resolved lane + the cap gate.
- **`--run`** gates the lane once (fail-loud with a named fix), resolves every
  followed thread, and writes a per-thread report (pick + disclosure + confidence
  + cost). The **verdict is a human read**: count primary-entity misses by hand;
  there is no ground-truth oracle — that is the whole point of a pre-registered
  falsifier.
- **Spends nothing without `--run`.** Cost bounded by `BUDGET_CAP_USD_PER_RUN`.
  ~19 threads × Haiku ≈ cents (usd_shadow; the subscription lane charges $0).

## Alternatives considered

- **api lane for the resolver seat (reliability-first).** Rejected as the
  DEFAULT: the subscription-first mandate is explicit, and follow_altitude is the
  same seat class as rank/editor/script (a lone api-lane Haiku seat would be more
  surprising than matching the family). The reliability concern (the `claude -p`
  headless smoke is still an open principal checkpoint) is mitigated — the dry-run
  prints the resolved lane before any spend, the api fall-over is one env var, and
  an unavailable lane fails loud with a named fix. The principal/CoS can flip to
  api at run time.
- **Routing through `generate.call_llm`.** Rejected: that helper is seat-locked to
  `generate` steps via `seat_for_step`; conflating a diagnostic/UI resolver with
  edition steps would blur the report-not-state boundary. The resolver mirrors the
  corrected-retry law in its own focused seam call.
- **Storing altitude at follow time now.** Out of scope by law — the falsifier
  verdict + mockup gate precede all migration/UI work.

## Consequences / for the human reviewer (NL-33)

- New spend surface: `scripts/follow-altitude --run` (Haiku; dry-run default).
- New seat `follow_altitude` → new (optional) override vars
  `NEWSLENS_LANE_FOLLOW_ALTITUDE` / `NEWSLENS_MODEL_FOLLOW_ALTITUDE`, scrubbed in
  conftest (hermeticity, the `NEWSLENS_MODEL_*` class).
- The resolver was NOT run against the principal's real threads this increment
  (dispatch §4). The real dry-run is CoS/principal-executed after the gate.
- Prompt: `prompts/follow_altitude.txt` (prompts are code).
