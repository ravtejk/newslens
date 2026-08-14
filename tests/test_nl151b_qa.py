"""NL-151b QA pins — the armed-contract adversarial pass's acceptance contracts
(2026-08-14).

WHY THIS FILE EXISTS BESIDE test_nl151b_propagation.py: the build's 27 pins
hold the flip and the nine propagation sites; QA's mutation matrix found two
seams none of them (or anything else) bites on — the BACKFILL's recovered
depth slots and the systemic pause's claim that the gates (not the fakes) keep
it free — plus two places where a surface still speaks the pre-ruling grammar
(the reports label on a Gate-B day; the podcast's lead). Each pin carries its
receipt class.

MUTATION RECEIPTS (QA mirrors, PYTHONPATH pinned, `newslens.__file__` asserted
inside each copy; one mutant per mirror, nothing ever restored):
  MUT-A  both gates neutered at the armed default: 46F across the four
         NL-148/151/151b families (14 of them in the build's new file).
  MUT-B  renumber mutant (walk hands analyze_story a compacted index): 10F
         incl. both NUMBERING pins — the cardinal-breach class still dies.
  MUT-C  `edition_tiers` ignores the arm: exactly ONE pin bites
         (test_the_recovery_is_armed_only_so_off_is_byte_identical). The arm
         gate's liveness rests on that single named pin — observed red.
  MUT-E  the LIVE run's brief routing reverted to `n <= 3`: 2F in the
         build's file — but every backfill test in the tree stayed GREEN.
  MUT-E2 the BACKFILL's own routing reverted (`n in backfill_depth` ->
         `n <= 3`): nothing in the tree bites but this file's backfill pin
         (observed red under MUT-E2 and MUT-G with the pin in place).
  MUT-G  `depth_tiers_from_record` severed: recovery + concept-B pins red;
         the backfill again silent until this file's pin.
  MUT-H  Gate A alone neutered: 42F — but test_clause4_at_his_arm_the_same_
         world_pauses_at_exactly_zero stayed GREEN: its chat sentinel raises
         AssertionError INSIDE analyze_story's `except Exception`, which
         swallows it into outcome "failed" at $0. The sentinel detects
         nothing; the gate-outcome pin below is the live replacement.

BORN-RED IN THIS FILE (open acceptance contracts, fix contracts in their
docstrings, the NL-151 F-3 precedent): test_the_reports_label_does_not_call_a_
never_attempted_skip_a_fetch_failure and test_the_podcast_lead_follows_the_
depth_tier_after_a_demotion. Everything else is born green with the
derivation receipts above.

Offline by construction: autouse sandbox + loopback guard (conftest); $0.
"""
from __future__ import annotations

import json

import pytest

from newslens import analysis, db, generate, labels, server

from test_analysis_brief_qa import DATE, ENV_OK, fetch_fixture, s_brief, sonar_none
from test_nl148_fetch_failure import _fetch_all_fail
from test_nl151_fetch_skip import (
    _excerpt_brief, _fetch_failing_slots, _run, _seed_slots,
)
from test_nl151_qa import _dead_urls, _log_entries, _real_stage_with
from test_generate import (  # noqa: F401
    ENV as GEN_ENV, A_DAY, compliant_script, fake_model, slot as gen_slot,
    seed_briefing, stories_payload, _fake_audio_ok,
)


@pytest.fixture
def db_con(tmp_paths):
    db.migrate()
    con = db.connect()
    try:
        yield con
    finally:
        con.close()


def _world(con, monkeypatch, fake_model, n=5, tiers=None):
    """The propagation file's `_armed_world`, re-declared here so this file
    stays runnable if that file moves; same shape, same fakes."""
    from newslens import ingest as ingest_mod, ranking as ranking_mod
    slots = [gen_slot(i, title=f"QB Story {i}") for i in range(1, n + 1)]
    seed_briefing(con, A_DAY, slots)

    def _noop_rank(*a, **k):
        r = type("R", (), {})()
        r.warnings = []
        return r

    monkeypatch.setattr(ingest_mod, "run_ingest",
                        lambda *a, **k: ingest_mod.IngestReport())
    monkeypatch.setattr(ranking_mod, "run_rank", _noop_rank)
    fake_model.narrative = stories_payload(slots, tiers=tiers)
    fake_model.script = compliant_script(slots)
    _fake_audio_ok(monkeypatch, [])
    return slots


def _runs(date=A_DAY):
    return [e for e in _log_entries()
            if "stage" not in e and "status" in e and e.get("date") == date]


def _stages(date=A_DAY):
    return [e for e in _log_entries()
            if e.get("stage") == "analysis" and e.get("date") == date]


# ===========================================================================
# L1 UNDER MULTI-FAILURE — the walk at the pipeline level, armed default
# ===========================================================================

def test_two_failed_slots_promote_two_and_disclose_twice(db_con, monkeypatch,
                                                         fake_model):
    """Born green (derivation receipt: QA probe P1a). The build proved one
    failure; L1 says EVER, so the two-failure morning is pinned end to end:
    depth walks to [3,4,5], two disclosures, no apology line anywhere."""
    _world(db_con, monkeypatch, fake_model,
           tiers=["quick", "quick", "full", "medium", "medium"])
    _real_stage_with(monkeypatch, _dead_urls(1, 2))
    generate.run_generate(date=A_DAY, con=db_con, env=GEN_ENV, refresh=True)
    stage = _stages()[-1]
    assert stage["depth_slots"] == [3, 4, 5]
    assert [s["slot"] for s in stage["fetch_skipped"]] == [1, 2]
    entry = _runs()[-1]
    assert entry["tiers"] == ["quick", "quick", "full", "medium", "medium"]
    row = db_con.execute("SELECT narrative_text FROM briefings WHERE date=?",
                         (A_DAY,)).fetchone()
    assert row["narrative_text"].count("Fetch failed for prioritized story") == 2
    assert "Analysis: unavailable" not in row["narrative_text"]


def test_three_failed_slots_ship_a_two_story_depth_tier(db_con, monkeypatch,
                                                        fake_model):
    """Born green (P1b). When the walk runs out of healthy stories before the
    queue empties, the depth tier SHRINKS rather than bending L1: [4,5] ship
    at full/medium, three disclosures, the day is NOT systemic (two fetches
    worked), and `deep_views` counts exactly the shrunken tier. No invented
    depth slots, no degraded stand-ins."""
    _world(db_con, monkeypatch, fake_model,
           tiers=["quick", "quick", "quick", "full", "medium"])
    _real_stage_with(monkeypatch, _dead_urls(1, 2, 3))
    rep = generate.run_generate(date=A_DAY, con=db_con, env=GEN_ENV,
                                refresh=True)
    assert rep.artifact_path
    stage = _stages()[-1]
    assert stage["depth_slots"] == [4, 5]
    assert stage["fetch_systemic_failure"] is False
    entry = _runs()[-1]
    assert entry["depth_slots"] == [4, 5]
    assert entry["deep_views"] == {"4": "available", "5": "available"}
    row = db_con.execute("SELECT narrative_text FROM briefings WHERE date=?",
                         (A_DAY,)).fetchone()
    assert row["narrative_text"].count("Fetch failed for prioritized story") == 3
    assert "Analysis: unavailable" not in row["narrative_text"]


def test_six_failed_of_seven_still_ships_with_one_depth_story(db_con,
                                                              monkeypatch,
                                                              fake_model):
    """Born green (P1c). The far edge short of systemic: one healthy story at
    the bottom of a seven-slot ranking carries the whole depth tier. The walk
    never promotes past the slot list (no invented slot 8), the leftover
    queue dies quietly, six disclosures render."""
    _world(db_con, monkeypatch, fake_model, n=7, tiers=["quick"] * 6 + ["full"])
    _real_stage_with(monkeypatch, _dead_urls(1, 2, 3, 4, 5, 6))
    rep = generate.run_generate(date=A_DAY, con=db_con, env=GEN_ENV,
                                refresh=True)
    assert rep.artifact_path
    stage = _stages()[-1]
    assert stage["depth_slots"] == [7]
    assert stage["fetch_systemic_failure"] is False
    row = db_con.execute("SELECT narrative_text FROM briefings WHERE date=?",
                         (A_DAY,)).fetchone()
    assert row["narrative_text"].count("Fetch failed for prioritized story") == 6


# ===========================================================================
# THE CARVE-OUTS — documented, not regressions (broad-L1 is his open call)
# ===========================================================================

def test_budget_skip_consumes_the_tier_so_c5_wins_armed(db_con):
    """Born green (P1d). C-5 precedence CONFIRMED at the armed default: an
    exhausted slot whose fetches also died leaves as `skipped-budget` (the
    floor sits above Gate A), its tier is CONSUMED — the slot stays in
    `depth_slots` with no brief behind it — and nothing lands in
    `fetch_skipped`. That brief-less depth slot is the DOCUMENTED broad-L1
    carve-out (walk comment, analysis.py): only FETCH outcomes hand the tier
    on. Narrow L1 still holds: no degraded row exists."""
    _seed_slots(db_con, 3)
    rep = analysis.run_analysis(
        date=DATE, con=db_con, env=dict(ENV_OK, BUDGET_CAP_USD_PER_RUN="0.0001"),
        chat=lambda k, p: (s_brief(), 0.0), sonar=sonar_none,
        fetch=_fetch_failing_slots(1), sleep=lambda s: None,
        tiers_override=["full", "medium", "medium"])
    by_slot = {r["slot"]: r for r in rep["per_story"]}
    assert by_slot[1]["outcome"] == "skipped-budget"
    assert 1 in rep["depth_slots"], "the budget skip must CONSUME the tier"
    assert rep["fetch_skipped"] == []
    rows = db_con.execute(
        "SELECT brief_json FROM analysis_briefs WHERE date=?", (DATE,)).fetchall()
    assert all(not json.loads(r["brief_json"])["header"].get("degraded")
               for r in rows)


def test_rejected_brief_keeps_its_depth_slot_the_documented_carve_out(
        db_con, monkeypatch, fake_model):
    """Born green (P1e). A model-REJECTED brief at a depth slot: the tier is
    consumed, depth stays [1,2,3], deep_views says "absent", and the edition
    carries the "Analysis: unavailable" apology for it. That is the exact
    residual the build report names (§8.2) and the gate carried to the
    principal (R-D-2) — this pin keeps it a DECISION, not a drift: if the
    walk ever starts re-routing rejection like a fetch failure, this goes red
    and the widening gets ruled, not inherited."""
    real = analysis.run_analysis

    def _wrapped(**kw):
        kw.setdefault("fetch", fetch_fixture)

        def rejecting_chat(key, prompt):
            if "Story 2" in prompt:
                return ({"summary": "no citations here"}, 0.0)
            return (s_brief(), 0.0)
        kw.setdefault("chat", rejecting_chat)
        kw.setdefault("sonar", sonar_none)
        kw.setdefault("sleep", lambda s: None)
        return real(**kw)

    _world(db_con, monkeypatch, fake_model,
           tiers=["full", "medium", "medium", "quick", "quick"])
    monkeypatch.setattr(analysis, "run_analysis", _wrapped)
    generate.run_generate(date=A_DAY, con=db_con, env=GEN_ENV, refresh=True)
    stage = _stages()[-1]
    assert stage["depth_slots"] == [1, 2, 3]
    assert stage["fetch_skipped"] == []
    entry = _runs()[-1]
    assert entry["deep_views"]["2"] == "absent"
    row = db_con.execute("SELECT narrative_text FROM briefings WHERE date=?",
                         (A_DAY,)).fetchone()
    assert "Analysis: unavailable" in row["narrative_text"]


# ===========================================================================
# THE PROMOTE'S PRICE — bounded by the tier, measured
# ===========================================================================

@pytest.mark.parametrize("arm,want_usd,want_depth", [
    ("off", 0.04, [1, 2, 3]),
    ("in-brief", 0.06, [1, 2, 4]),
])
def test_specimen_cost_re_derived_off_004_armed_006(db_con, monkeypatch, arm,
                                                    want_usd, want_depth):
    """Born green (P4b) — the 2026-08-03 specimen's price, both arms, with a
    charging fake ($0.02/brief). OFF: slot 3 demotes free, two briefs, $0.04.
    ARMED: slot 3 skips, slot 4's brief is BOUGHT — $0.06. The +$0.02 is
    L2's promote and it is deliberate coverage, not leakage; the bound pin
    below is what keeps it from ever being more than that."""
    monkeypatch.setattr(analysis, "FETCH_SKIP_ARM", arm)
    _seed_slots(db_con, 4)
    rep = _run(db_con, _fetch_failing_slots(3),
               tiers=("full", "medium", "medium", "quick"),
               chat=lambda k, p: (s_brief(), 0.02))
    assert round(rep["total_usd"], 6) == want_usd
    assert rep["depth_slots"] == want_depth


@pytest.mark.parametrize("dead,want_briefs", [
    ((), 3), ((1,), 3), ((1, 2), 3), ((1, 2, 3), 2), ((1, 2, 3, 4), 1),
])
def test_armed_spend_is_bounded_by_the_depth_tier(db_con, dead, want_briefs):
    """Born green (P5). THE WORST CASE, measured: an armed day never buys more
    briefs than the depth tier holds (ANALYST_TIER_SLOTS). Failed slots cost
    $0 at the gates; each promote buys the brief the failed slot did not; and
    when healthy stories run out the spend SHRINKS with the tier — a 3-failed
    day costs $0.04, not 3 extra briefs. Armed spend <= the healthy day's,
    always; the budget ladder binds each promote like any other slot."""
    _seed_slots(db_con, 5)
    rep = _run(db_con,
               _fetch_failing_slots(*dead) if dead else fetch_fixture,
               tiers=("full", "medium", "medium", "quick", "quick"),
               chat=lambda k, p: (s_brief(), 0.02))
    assert round(rep["total_usd"], 6) == round(want_briefs * 0.02, 6)
    assert len(rep["depth_slots"]) == want_briefs


# ===========================================================================
# THE SEAMS THE MUTATION MATRIX FOUND OPEN — closed here
# ===========================================================================

def test_the_systemic_pause_is_free_because_of_the_gates_not_the_fakes(
        db_con):
    """CLOSES the MUT-H hole (receipt in the module docstring): with Gate A
    neutered, the armed clause-4 twin in test_nl148_fetch_failure.py stays
    green because its chat sentinel is swallowed by analyze_story's
    `except Exception` into outcome "failed" at fake-$0. What actually proves
    the gates held is the OUTCOME VOCABULARY: every slot must leave as the
    GATE outcome, not as a synthesis failure. Observed red under MUT-H at
    exactly this assert; the docstring truth-edit for the twin's sentinel
    claim is enumerated in the QA report."""
    _seed_slots(db_con, 3)
    rep = _run(db_con, _fetch_all_fail, chat=lambda k, p: (s_brief(), 0.02))
    assert rep["fetch_systemic_failure"] is True
    assert rep["total_usd"] == 0.0
    assert [r["outcome"] for r in rep["per_story"]] == \
        [analysis.FETCH_SKIP_OUTCOME] * 3, (
        "a systemic-day slot got past the gates into the ladder — the pause "
        "is only free while the gates return every slot above it")


def test_the_backfill_follows_the_recovered_depth_slots(db_con, monkeypatch,
                                                        fake_model):
    """CLOSES the MUT-E2/MUT-G hole: the backfill's `n in backfill_depth`
    rewiring (generate.py run_memory_backfill) had NO pin that bites — with
    its routing reverted to `n <= 3` (MUT-E2) or its recovery severed
    (MUT-G), every committed test stayed green while the backfill quietly
    read slots 1-3 of a demoted edition, missing the promoted story's brief
    in the permanent thread record. This spy pins the lookup set itself: an
    armed demoted edition's backfill must ask for [2, 3, 4]. Observed red
    under MUT-E2 and MUT-G."""
    _world(db_con, monkeypatch, fake_model,
           tiers=["quick", "full", "medium", "medium", "quick"])
    _real_stage_with(monkeypatch, _dead_urls(1))
    generate.run_generate(date=A_DAY, con=db_con, env=GEN_ENV, refresh=True)
    seen = []
    real_cvb = analysis.coherent_valid_brief

    def spy(con, date, n, published_at):
        seen.append(n)
        return real_cvb(con, date, n, published_at)

    monkeypatch.setattr(analysis, "coherent_valid_brief", spy)
    generate.run_memory_backfill(date=A_DAY, con=db_con, env=GEN_ENV,
                                 force=True)
    assert seen == [2, 3, 4], (
        f"the backfill looked up briefs at {seen} — it must follow the "
        "recovered depth slots, or a promoted story's analysis never reaches "
        "the thread ledger")


def test_torn_and_non_dict_log_lines_do_not_break_the_recovery(db_con,
                                                               monkeypatch,
                                                               fake_model):
    """Born green (P9). The recovery reads a forensic append-only log a
    crashed run can tear: a torn line and a valid-JSON-but-not-a-dict line are
    both passed over, the newest armed stage entry still wins, and a SHORT
    recovered vector pads with quick (never silently reverts to positional —
    that would be the L1 breach by the other door)."""
    _world(db_con, monkeypatch, fake_model,
           tiers=["quick", "full", "medium", "medium", "quick"])
    _real_stage_with(monkeypatch, _dead_urls(1))
    generate.run_generate(date=A_DAY, con=db_con, env=GEN_ENV, refresh=True)
    from newslens import paths
    log = paths.DATA_DIR / "generation_log.jsonl"
    txt = log.read_text(encoding="utf-8")
    log.write_text('{"torn": \n[1, 2, 3]\n' + txt, encoding="utf-8")
    assert analysis.depth_tiers_from_record(A_DAY, 5) == \
        ["quick", "full", "medium", "medium", "quick"]
    assert analysis.depth_tiers_from_record(A_DAY, 7) == \
        ["quick", "full", "medium", "medium", "quick", "quick", "quick"]


def test_the_run_screen_shows_the_demotion_beside_either_trigger(db_con):
    """Born green. NL-146/NL-149 composition: the reports row's skip count is
    trigger-independent — a scheduled run that demoted the lead at 06:00 and
    an interactive one say the same thing, beside their trigger word."""
    from newslens import schedule
    skips = [{"slot": 1, "story_title": "T", "outcome": analysis.FETCH_SKIP_OUTCOME,
              "disclose": True}]
    for trigger, word in ((schedule.TRIGGER_SCHEDULED,
                           labels.RUNLOG_TRIGGER_SCHEDULED),
                          (schedule.TRIGGER_INTERACTIVE,
                           labels.RUNLOG_TRIGGER_INTERACTIVE)):
        html = server._render_run({"ts": "2026-08-14T12:00:00+00:00",
                                   "date": A_DAY, "status": "ok",
                                   "total_usd": 0.0, "trigger": trigger,
                                   "fetch_skipped": skips})
        assert labels.RUNLOG_DEPTH_SKIPPED_ONE in html
        assert word in html


# ===========================================================================
# BORN RED — open acceptance contracts for the gate (NL-151 F-3 precedent)
# ===========================================================================

def test_the_reports_label_does_not_call_a_never_attempted_skip_a_fetch_failure(
        db_con):
    """BORN RED at the current bytes — fix contract below; the gate rules.

    A Gate-B day (every cluster source outside the tier boundaries; zero
    sockets opened) is deliberately NOT disclosed in the briefing because
    "Fetch failed" would be FALSE — nothing was attempted. The reports screen
    then renders the same day as "1 story moved to In Brief (fetch failed)":
    the count comes from `fetch_skipped`, which carries both outcomes, and
    the label hard-codes the Gate-A parenthetical. The record surface states
    the exact sentence the briefing refused on honesty grounds.

    Production-unobserved (0/72 real slots are Gate B) — severity LOW, but it
    is a label-truth defect on the one screen whose job is the record.

    FIX CONTRACT, either lawful:
      (a) split the label by outcome — Gate-A entries keep "(fetch failed)",
          Gate-B entries get their own words ("no fetchable sources"); or
      (b) key the parenthetical off `disclose` (the flag the stage already
          set beside the outcome) so never-attempted skips render the count
          without the false cause.
    Ruling (b)'s minimal shape or (a)'s two labels is the gate's call; if the
    gate instead blesses the current wording, invert this pin with the
    blessing quoted.

    RULED (a) — gate R-A, 2026-08-14: split the label by outcome. "(b) fails
    the mixed morning: one Gate-A + one Gate-B skip under a disclose-keyed
    parenthetical either lies about half the count or goes silent about a true
    cause the record holds." The mixed morning is pinned at the bottom of this
    test, because it is the composition the ruling turns on and nothing else
    in the tree renders it."""
    _seed_slots(db_con, 3)
    db_con.execute(
        "UPDATE source_items SET url = replace(url, 'https://', 'ftp://')")
    db_con.commit()
    rep = _run(db_con, _fetch_all_fail)
    skips = rep["fetch_skipped"]
    assert skips and all(s["outcome"] == analysis.NO_FETCHABLE_OUTCOME
                         for s in skips)
    assert all(s["disclose"] is False for s in skips)
    html = server._render_run({"ts": "2026-08-14T12:00:00+00:00", "date": DATE,
                               "status": "ok", "total_usd": 0.0,
                               "fetch_skipped": skips})
    assert "moved to In Brief" in html, "the count itself is right and stays"
    assert "fetch failed" not in html.lower(), (
        "the reports screen calls a never-attempted skip a FETCH FAILURE — "
        "the briefing renderer refused this exact sentence as false; the "
        "record surface must not state it either")
    # the count is still the whole count, under the true cause
    n = len(skips)
    assert (labels.RUNLOG_DEPTH_NOFETCH_ONE if n == 1
            else labels.RUNLOG_DEPTH_NOFETCH_MANY.format(n=n)) in html

    # THE MIXED MORNING (gate R-A's deciding composition): one slot tried and
    # failed, one slot was never attempted. Both bits render, each counting
    # ONE — a single line would have to pick a cause that is false for half
    # the demotions it counts. Gate A first, the ordinary morning's cause.
    mixed = server._render_run(
        {"ts": "2026-08-14T12:00:00+00:00", "date": DATE, "status": "ok",
         "total_usd": 0.0,
         "fetch_skipped": [
             {"slot": 1, "story_title": "A",
              "outcome": analysis.FETCH_SKIP_OUTCOME, "disclose": True},
             {"slot": 2, "story_title": "B",
              "outcome": analysis.NO_FETCHABLE_OUTCOME, "disclose": False}]})
    assert labels.RUNLOG_DEPTH_SKIPPED_ONE in mixed
    assert labels.RUNLOG_DEPTH_NOFETCH_ONE in mixed
    assert "2 stories" not in mixed, (
        "the mixed morning counted both demotions under one cause")
    assert mixed.index(labels.RUNLOG_DEPTH_SKIPPED_ONE) < \
        mixed.index(labels.RUNLOG_DEPTH_NOFETCH_ONE), "Gate A reads first"


def test_the_podcast_lead_follows_the_depth_tier_after_a_demotion(
        db_con, monkeypatch, fake_model):
    """BORN RED at the current bytes — fix contract below; the gate rules
    fix-now vs charter (the build carried this as §8.3, deliberately
    untouched; the trust-quiet law cuts toward print/audio agreement being
    contract-grade, which is why this is a red pin and not a note).

    THE DISAGREEMENT, measured: after a demotion the PRINT edition's lead —
    the story with the full tier, the 640-word budget, the deep view — is the
    PROMOTED story. The podcast prompt still says "the LEAD (story 1, the
    deepest segment)" and hands story 1 the ~400-word ceiling: the episode's
    center of gravity is the one story the edition just moved to In Brief on
    excerpt-grade material. Print and audio disagree about what today's most
    important story is, every demotion morning (~1 in 17 real dates so far).

    FIX CONTRACT:
      (a) `build_script_prompt` (and `script_covered_slots`/`_script_budgets`
          if the gate wants full agreement) keys the lead segment off the
          edition's tier vector — the lead is the FULL-tier story, covered
          slots follow the depth tier then rank; or
      (b) the principal charters the podcast as rank-ordered regardless of
          tier — then invert this pin with the charter quoted, and the
          episode keeps opening on the demoted story knowingly.
    Decided by neither this pin nor the loop; it stands red until ruled.

    RULED (a) FIX-NOW — gate R-B, 2026-08-14: "the script follows the tier
    vector... one edition must not make two different 'most important story
    today' claims." His override stays open on the ship slate (charter
    rank-order audio, invert this pin with the charter quoted). The positive
    half of the ruling is asserted below: the promoted story is named the LEAD
    and carries the ~400 ceiling, and the demoted story drops to ~200."""
    _world(db_con, monkeypatch, fake_model,
           tiers=["quick", "full", "medium", "medium", "quick"])
    _real_stage_with(monkeypatch, _dead_urls(1))
    generate.run_generate(date=A_DAY, con=db_con, env=GEN_ENV, refresh=True)
    inputs = generate.load_briefing_inputs(db_con, A_DAY)
    inputs["depth_tiers"] = ["quick", "full", "medium", "medium", "quick"]
    prompt = generate.build_script_prompt(A_DAY, "A", "Narrative body.", inputs)
    assert "the LEAD (story 1, the deepest segment)" not in prompt, (
        "the podcast still opens its deepest segment on the story the edition "
        "demoted to In Brief — print and audio disagree about the lead")
    # the positive: the promoted (full-tier) story IS the lead, in both the
    # coverage instruction and the per-story ceiling the writer is handed
    assert "the LEAD (story 2, the deepest segment)" in prompt
    assert generate.script_lead_slot(inputs) == 2
    assert "slot 2: up to ~400" in prompt, (
        "the promoted story must carry the deepest segment's ceiling")
    assert "slot 1: up to ~200" in prompt, (
        "the demoted story must not keep the lead's word budget")
    assert "slot 1: up to ~400" not in prompt
    # coverage is unchanged on a 5-slot edition (k == n): the demotion moves
    # the DEPTH, not who is on the air
    assert generate.script_covered_slots(inputs) == {1, 2, 3, 4, 5}


# ===========================================================================
# GATE R-B's HARD CONSTRAINT — the fix moves bytes ONLY where the vector moved
# ===========================================================================

def _script_inputs(slot_ids, depth_tiers=None):
    inputs = {"slots": [gen_slot(i) for i in slot_ids],
              "items_by_slot": {i: [] for i in slot_ids}, "threads": [],
              "prior_ctx": None, "continuity_status": "none",
              "window_meta": None, "corroboration": {}}
    if depth_tiers is not None:
        inputs["depth_tiers"] = depth_tiers
    return inputs


def test_the_no_vector_script_prompt_carries_the_pre_fix_bytes_exactly():
    """CARRIED — and it is gate R-B's HARD CONSTRAINT, not a nicety: "no-vector
    /OFF days render byte-identical script prompts — coverage and budgets move
    only where the vector says the depth moved."

    The fix touches exactly two surfaces of the built prompt (the coverage
    instruction and the per-story ceiling guide), so those two are pinned here
    as LITERALS transcribed from the pre-fix bytes, across the three coverage
    branches and the ragged-id world whose "1 through {last}" arithmetic the
    old invariant made exact. A world with no `depth_tiers` key is every
    edition published before this commit, every `--no-refresh`, and every day
    at the OFF arm; the ordinary armed morning (a positional vector) is pinned
    beside it, because that is 16 of the 17 real dates.

    If this pin reds, the fix leaked into a morning the ruling did not touch."""
    six = _script_inputs(range(1, 7))                     # k=5 < n=6
    p6 = generate.build_script_prompt(A_DAY, "A", "Narrative body.", six)
    assert ("This episode covers 5 stories — the LEAD (story 1, the deepest "
            "segment) plus the 4 next-most-consequential. Cover stories 1 "
            "through 5 ONLY; the remaining 1 stories are NOT in this episode "
            "— they live in the text briefing. The lead is the episode's "
            "center of gravity; never cover every story.") in p6
    assert generate._script_budgets(6)[1] in p6           # unchanged guide text
    assert "slot 1: up to ~400 · slot 2: up to ~200" in p6

    three = _script_inputs(range(1, 4))                   # k == n branch
    p3 = generate.build_script_prompt(A_DAY, "A", "Narrative body.", three)
    assert ("This episode covers all 3 stories in the edition — the LEAD "
            "(story 1, the deepest segment) plus the other 2, in rank order. "
            "The lead is the episode's center of gravity.") in p3

    one = _script_inputs([1])                             # k <= 1 branch
    p1 = generate.build_script_prompt(A_DAY, "A", "Narrative body.", one)
    assert ("This edition has a single story — cover the LEAD only; there is "
            "no second story to air.") in p1

    ragged = _script_inputs((1, 3, 7, 9, 12, 15, 20))     # non-contiguous ids
    pr = generate.build_script_prompt(A_DAY, "A", "Narrative body.", ragged)
    assert "Cover stories 1 through 12 ONLY" in pr
    assert generate.script_covered_slots(ragged) == {1, 3, 7, 9, 12}

    # THE ORDINARY ARMED MORNING: a live vector that demoted nothing is the
    # positional contract, so it must be byte-identical to the no-vector day.
    ordinary = _script_inputs(
        range(1, 7),
        depth_tiers=["full", "medium", "medium", "quick", "quick", "quick"])
    assert generate.build_script_prompt(
        A_DAY, "A", "Narrative body.", ordinary) == p6
    assert generate.script_lead_slot(ordinary) == 1

    # and OFF is byte-identical whatever the record holds (the arm gate)
    import newslens.analysis as _an
    demoted = _script_inputs(
        range(1, 7),
        depth_tiers=["quick", "full", "medium", "medium", "quick", "quick"])
    _prev = _an.FETCH_SKIP_ARM
    try:
        _an.FETCH_SKIP_ARM = _an.FETCH_SKIP_ARM_OFF
        assert generate.build_script_prompt(
            A_DAY, "A", "Narrative body.", demoted) == p6
    finally:
        _an.FETCH_SKIP_ARM = _prev


def test_a_moved_depth_tier_names_the_covered_slots_instead_of_a_range():
    """The other half of the constraint: where the vector DID move the depth,
    the covered set can stop being a prefix of the present ids — and then
    "Cover stories 1 through {last} ONLY" would order the writer to cover a
    story the episode excludes. A 7-slot demotion morning covers {2,3,4} (the
    depth) plus the two lowest remaining ({1,5}), and the instruction names
    them."""
    seven = _script_inputs(
        range(1, 8),
        depth_tiers=["quick", "full", "medium", "medium", "quick", "quick",
                     "quick"])
    assert generate.script_covered_slots(seven) == {1, 2, 3, 4, 5}
    p = generate.build_script_prompt(A_DAY, "A", "Narrative body.", seven)
    assert "Cover stories 1, 2, 3, 4, 5 ONLY" in p
    assert "Cover stories 1 through" not in p
    assert "the LEAD (story 2, the deepest segment)" in p

    # a NON-PREFIX covered set: slots 6 and 7 hold the depth, so the episode
    # airs them plus the three lowest remaining — the range form would read
    # "1 through 7", i.e. the whole edition the digest exists to cut
    tail = _script_inputs(
        range(1, 8),
        depth_tiers=["quick", "quick", "quick", "quick", "quick", "full",
                     "medium"])
    assert generate.script_covered_slots(tail) == {1, 2, 3, 6, 7}
    pt = generate.build_script_prompt(A_DAY, "A", "Narrative body.", tail)
    assert "Cover stories 1, 2, 3, 6, 7 ONLY" in pt
    assert "the LEAD (story 6, the deepest segment)" in pt
    assert "slot 6: up to ~400" in pt
    assert "slot 7: up to ~200" in pt and "slot 4" not in pt
