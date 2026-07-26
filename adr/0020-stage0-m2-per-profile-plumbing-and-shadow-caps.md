# ADR-0020 — Stage-0 M2: per-profile plumbing, shadow-bound caps, and the spoken-continuity net

**Date:** 2026-07-25 · **Status:** accepted (build-crew loop; QA + gate follow)
**Context:** multi-user Executive Brief 2026-07-16 §2 (reconciled Stage-0 scope)
· DECISIONS 2026-07-25 "STAGE-0 M1 SHIPPED" → M2 CONTRACT RIDERS · engineering
mini-round `debates/2026-07-25--newslens--engineering-3.md` (the NL-95 build
contract) · M0 cold-start QA `research/2026-07-25--m0-coldstart-qa.md` finding
F2 · ADR-0019 (the profile dimension this builds on).

---

## 1. The decision

M1 made a profile a *place*. M2 makes it a **budget, an editorial identity, and
a set of interests** — and closes the money-guard hole (NL-95) that would have
made per-profile spend meaningless anyway.

## 2. What / why / what else was considered

### 2.1 Per-profile interests, ingest and rank: mostly ALREADY TRUE, and why

**What:** ingest, rank, discovery and the writer's tag block all read the
**active profile's** `sources.yaml`, never a shared interest list.

**Why this is a short section:** M1's `paths.__getattr__` profile arm already
routed `SOURCES_FILE` per profile, and every reader in the package reaches it
through `config.load_sources()` with no path argument. So the *mechanism* landed
at M1 as a side effect of the guarded-name work; what M2 owed was **proof** and
**honesty**, not machinery:

- an end-to-end pin through the real routing harness that a non-default
  profile's rank prompt carries its own tags and none of the founder's;
- a **mechanical** pin — not an inspection — that a non-default profile never
  opens the founder's `sources.yaml` at all (his file is a standing local edit
  and a sacred file);
- refusals that **name the profile and the resolved path**. "Add interests to
  sources.yaml" stopped being an actionable sentence the moment a second
  profile could exist, and the wrong file to edit is the founder's. The default
  profile's wording is byte-identical to before **for the `cfg.problems`
  refusal via `_sources_label()`**, so his messages do not move. (Gate F3
  scoping: the no-interests refusal's wording moved for ALL profiles including
  the default — no ba72f95 pin broke, the substring pins hold, and the founder
  has interests so he never hits that refusal live; the new message is better.)

**What was NOT done, deliberately:** the brief's phrase "interests out of
sources.yaml" can be read as *move interests into the database*. That would
require a schema migration, which the schema-checkpoint law makes a principal
decision, not a build call — and the dispatch explicitly expected no migration.
It would also mean either editing the founder's `sources.yaml` to strip the
block (forbidden: sacred file) or leaving two sources of truth. Interests stay
in each profile's own YAML.

### 2.2 Caps and ledgers bind the acting profile — proven, not asserted

**What:** the spend ledger (`generation_log.jsonl`) and every DB-side
instrumentation row land in the acting profile's own `DATA_DIR`/database.

**Why the pins are shaped the way they are:** as with 2.1 the mechanism came
free from `DATA_DIR` being profile-resolved, so a pin that merely asserted the
layout would prove nothing a human couldn't read off `paths.py`. The pins
instead go through a **real reader** (`server._log_entry_for`, the one the UI
renders spend from) in both directions — a tester cannot see the founder's
spend, and the founder's ledger is not even *created* by a tester's run — plus
a structural pin that fails if anyone later hardcodes a `data/generation_log`
path, which is the one bypass `paths.__getattr__` documents as its own limit.

**Known and unchanged:** `BUDGET_CAP_USD_PER_RUN` is per *invocation* and lives
in the shared machine-level `.env` (ADR-0019: `.env` is deliberately not
profile-scoped — keys are machine credentials). There is no cross-run daily
budget in the system, so "profile A cannot eat profile B's headroom" is a
statement about ledger separation, which is what the pins assert.

### 2.3 NL-95 — the cap binds `usd_shadow`, via a DUAL-TRACK return

**What:** `call_analysis_model` returns `(parsed, usd_charged, usd_shadow)`,
both accumulated from **one** `cost_fields` call per attempt. All three cap
sites (analysis ladder, generate's edition cap, the baseline backfill)
accumulate **shadow**; every persisted `cost_usd` and every pre-existing log key
still means **charged**.

**Why not the one-line flip** (`usd_charged` → `usd_shadow` at the accumulation
site, which is what the hole looks like from a distance): the same float is
persisted downstream as real money — `analysis_briefs.cost_usd` via
`persist_brief`, `thread_baselines.cost_usd` via `record_baseline` — and folded
into the failed-run money record. Flipping it would have made a $0 subscription
run report dollars it never spent, trading an under-enforced cap for a
fabricated money record. Two figures, two questions, both returned.

**Why it mattered:** on the subscription lane `usd_charged` is 0.00, so
`cap - spent` never shrank. The analysis stage's whole degradation ladder was
inert, and `memory-baseline --all` over a backlog was effectively uncapped.

**No migration, and the reason:** every shadow figure landed under a **new** key
(`usd_shadow`, `total_usd_shadow`, `analysis_usd_shadow`); no column changed and
no existing key's meaning moved, so historical `generation_log` rows do not
diverge from new ones and every enumerated reader uses `.get`. One in-memory
field (`BaselineBackfillReport.spent_usd`) did flip meaning to the cap figure —
never persisted, and its sole reader (`cli.py`'s memory-baseline printer) moved
in the same change.

**Cost of the change, measured:** 2 incidental test reds across the whole suite,
both direct 2-tuple unpacks, both re-pinned. The contract's fallback (a
module-channel accumulator, if re-pins exceeded 12) was **not** needed.

**One fix arrived unguarded, and a mutation pass caught it.** Reverting the
generate-side cap line (fix #2) to the charged total left `tests/test_stage0_m2.py`
entirely green — an enforcement change that disturbs no test, which
ENGINEERING.md flags as suspicious by default. `test_nl95_edition_cap_decrements_
by_the_analysis_stages_shadow` was written afterwards specifically to be the red
test only that line can flip, and verified red under the mutation and green with
it restored. Every other enforcement line in this ADR was mutation-checked the
same way.

### 2.4 The spoken-continuity net (M0 finding F2 / RED-2)

**What:** two halves, gated on **one** predicate.
1. `generate.script_continuity_findings` — warn-grade findings, one per claim,
   naming the phrase, wired on the script that actually ships.
2. `prompts/script_adapt.txt`'s thread-arc callback license is now
   `{continuity_license}`, rendered from the data: on an edition with no prior
   coverage the model is told the silence rule instead of being handed an
   exemplar ("third week we've tracked this") that is itself the fabrication
   shape.

**Why both:** a prompt is a request, not a guarantee; a validator alone leaves
the model *invited* to fabricate. And they share `_has_real_prior_coverage` so
the thing the model is licensed to do and the thing the run record permits can
never drift apart.

**Why the licence needs TWO conditions** (a readable prior briefing AND real
thread history on some story): "we have shipped an edition before" does not make
"the third week we've tracked this" true. Edition 2 with no ledger record on the
story is still a fabrication.

**The subtle one — self-attribution:** the existing `_is_source_attributed`
helper treats "reported" and "told" as attribution markers, so *"As we reported
on Tuesday"* and *"We told you this would come back"* both read as
source-attributed — exactly the fabrications the net exists to catch. The
exemption is therefore **withheld from first-person and audience-addressed
claims**: the show is the speaker, and a speaker cannot be its own citation.

**Warn-grade, tuned toward firing:** a false positive costs a line in the run
record; a false negative ships a fabricated relationship with the listener. The
mandatory dated revival disclosure is explicitly exempt — a validator that
fights a hard requirement is worse than no validator.

### 2.5 A fresh profile follows NOBODY (editorial; one-line reversible)

**What:** the three `followed_analyst: true` flags (Tooze, Smith, Yglesias) ship
**commented out** in `templates/profile-sources.yaml`. The outlets stay in the
catalog.

**Why:** a followed analyst gets a personal-impact ranking boost independent of
topic match. It is a statement about whose writing *this reader* follows — and
these three are the founder's. Shipping them live meant every new profile was
born reading his people: the same defect class as the inherited memory threads
M1 killed, one layer down.

**Reversal cost:** uncomment one line per source. Deliberately a YAML comment
rather than code, so the principal can flip it without a build loop.

**The settings block stays, as decided values:** `tts_engine: kokoro` (the $0
local voice — nobody should start spending on audio they did not choose) and the
shipped `threads_steer_selection: false`. Note `kokoro` differs from the package
default in `config.py`, which is the principal's own ear-test ruling for *his*
install.

### 2.6 One profile boundary, five entrypoints

**What:** `profiles.resolve_entrypoint_profile()` is the single implementation
of "resolve the active profile or refuse"; `battery`, `moat_battery` and
`follow_altitude` now call it immediately after `allow_real_paths()`.

**Why a helper rather than three more copies:** `cli.main` and `doctor.main`
already carried hand-rolled copies of this logic, and duplicated validation is
how BUG-1 shipped in two places at once. Those two are M1-pinned and correct, so
this milestone did not rewrite them — flagged in PREFLIGHT as a follow-up,
**not silently absorbed**.

**Why it must run first:** the refusal has to precede each instrument's own
"cannot open the record" exit, or a ghost profile is merely *masked* by an
unrelated error instead of being named. All three were observed at HEAD exiting
1 on the typo'd profile and raising a raw `ProfileError` traceback on a
malformed one.

### 2.7 `profiles/default` is visible

**What:** `stray_directories()` now reports a directory literally named
`default`.

**Why:** it is a *valid* slug, so the invalid-name filter skipped it, and
`profile_names()` excludes the default name by construction (the founder's root
is the checkout). A directory holding a whole reader's state was invisible in
both listings — and invisibly ignored, with no way for its owner to find out
why.

### 2.8 The shuffle plugin earns in-tree residency

**What:** `tools/pytest_shuffle.py`, re-exported from `tests/conftest.py`
(the only place pytest honours `pytest_addoption` here). Inert without
`--shuffle`; seed printed in the header and the summary.

**Why in-tree:** the R5 convention says every QA/gate pass is one ordered run
plus a seeded shuffle. While the plugin lived out of tree it was hand-carried,
which meant a seed quoted in a report could not be replayed from the repo alone.
Stdlib `random` — adding `pytest-randomly` for a 40-line ordering shim would be
a new dependency, an escalation trigger.

**Whole-list, not within-module:** the coupling worth catching in this suite is
cross-file (module-level seat resolutions, the `paths` module-dict shadows, the
`_ACTIVE_ANALYST`/`_ACTIVE_STEP_SEATS` globals).

## 3. Consequences

- Suite 2447 passed / 1 xfail → **2503 passed, 0 xfail** (55 new pins across
  three files). RED-2's strict-xfail
  did its job on the way out: it xpassed the moment the net landed, reddened the
  suite, and was re-adopted as a normal green in the same change.
- `call_analysis_model`'s arity changed (2 → 3). It remains a monkeypatch target
  and remains signature-pinned; the re-pin is deliberate and in the same change.
- A harness hazard was found and fixed: M1's `real_route` fixture re-anchors
  `PROJECT_ROOT` but leaves `paths._GUARDED` frozen against the **real**
  checkout, so any test exercising the DEFAULT profile through it reads the
  principal's real files. M1's own tests never routed a default profile through
  it, so it never bit. The M2 fixture re-anchors the table too.
- Residual, named in PREFLIGHT: the failed-run fold's **memory** row still
  carries charged only (`memory_shadow_usd` exists; the fix is two lines), and
  `cli.py`'s state-repair printer still labels a shadow figure "spend". Both are
  outside the adjudicated NL-95 contract and were left rather than widened.
