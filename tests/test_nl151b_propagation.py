"""NL-151b — ARMING the fetch-skip contract on his ruled arm (ii), IN-BRIEF.

His word, 2026-08-14: "(ii) demote to in brief". NL-151 shipped the detection
and the walk INERT behind `FETCH_SKIP_ARM = "off"` because the arm was his
call. It is made. This file pins the two halves that ruling turns on:

  THE FLIP        the default becomes the in-brief arm, so every armed-only
                  behaviour NL-151 built now runs on a real morning.
  THE PROPAGATION the walk's `depth_tiers` vector becomes the tier truth the
                  writer and the reader consume, at the sites where the depth
                  tier used to be a POSITION (nine of them, four modules).

WHAT ARM (ii) MEANS, as three testable statements:
  * the fetch-failed prioritized story KEEPS ITS SLOT NUMBER and renders as a
    quick-tier In-Brief story — the in-tree slot-3 precedent ("a depth slot
    with no full text becomes quick"), now general;
  * the next prioritized story is PROMOTED into the depth treatment and
    carries NO badge saying so (trust-quiet: the reader gets a normal lead);
  * the disclosure is his sentence, at the literal bottom, once.

PROOF CLASSES. Every pin below is labelled RED or CARRIED in its docstring:
  RED      fails at e9c3358 (the settings-batch ship) — the born-red receipt
           is in the build report, per-pin, with the failure line.
  CARRIED  born green and labelled: an invariant this batch must not break
           (the OFF control, the no-vector fixture contract, the healthy-day
           silence). A carried pin that goes red is a regression, not news.

Offline by construction: autouse sandbox (conftest), no network, no key, $0.
"""

from __future__ import annotations

from html import escape

import pytest

from newslens import analysis, db, generate, labels, server

from test_analysis_brief_qa import DATE
from test_nl148_fetch_failure import _fetch_all_fail
from test_nl151_fetch_skip import _fetch_failing_slots, _run, _seed_slots
from test_nl151_qa import _dead_urls, _log_entries, _real_stage_with
from test_generate import (  # noqa: F401
    ENV as GEN_ENV, A_DAY, compliant_script, fake_model, slot as gen_slot,
    seed_briefing, stories_payload, _fake_audio_ok,
)


# The vector a 5-slot day produces when the LEAD's fetches fail: slot 1 keeps
# its number and becomes In-Brief; the depth treatment (full + 2 medium) walks
# down to slots 2-4; slot 5 was never in the depth tier.
DEMOTED_LEAD_VECTOR = ["quick", "full", "medium", "medium", "quick"]
POSITIONAL_VECTOR = ["full", "medium", "medium", "quick", "quick"]


@pytest.fixture
def db_con(tmp_paths):
    db.migrate()
    con = db.connect()
    try:
        yield con
    finally:
        con.close()


def _armed_world(con, monkeypatch, fake_model, n=5, tiers=None):
    """The pipeline world of test_nl151_qa's `_pipeline_world`, with ONE
    difference that is the whole point of this file: the fake writer returns
    the tier vector the pipeline ASKED FOR, instead of the positional one.

    A real model is told its tier per story in the prompt and complies; the
    fake has to be told the same thing or it is testing a writer that ignores
    its instructions."""
    from newslens import ingest as ingest_mod, ranking as ranking_mod
    slots = [gen_slot(i, title=f"NL151b Story {i}") for i in range(1, n + 1)]
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


def _run_entry(date=A_DAY):
    """The newest RUN entry (not a stage entry) for a date."""
    runs = [e for e in _log_entries()
            if "stage" not in e and "status" in e and e.get("date") == date]
    return runs[-1] if runs else None


def _stage_entry(date=A_DAY):
    stages = [e for e in _log_entries()
              if e.get("stage") == "analysis" and e.get("date") == date]
    return stages[-1] if stages else None


# ===========================================================================
# THE ARM — his ruling, as the default
# ===========================================================================

def test_the_armed_default_is_in_brief():
    """RED at e9c3358. THE RULING PIN, and the inverse of NL-151's
    `test_off_is_the_default_and_the_fork_is_still_open` — that pin's subject
    (an open fork) no longer exists, so it is inverted here rather than left
    to assert a decision the principal has since made.

    It fails the day someone quietly re-defaults the arm, which is the same
    protection pointed the other way: his editorial call may not be undone by
    a diff either."""
    assert analysis.FETCH_SKIP_ARM == analysis.FETCH_SKIP_ARM_IN_BRIEF
    assert analysis.depth_skip_armed() is True


def test_off_remains_constructible_as_the_legacy_arm(db_con, monkeypatch):
    """CARRIED. OFF is no longer the default but it is still REACHABLE, and
    with it today's behaviour: a fetch-failed depth slot mints its degraded
    brief and holds slot 1. Kept so the armed pins measure a real difference
    rather than a world where degraded depth briefs never existed."""
    monkeypatch.setattr(analysis, "FETCH_SKIP_ARM", analysis.FETCH_SKIP_ARM_OFF)
    assert analysis.depth_skip_armed() is False
    _seed_slots(db_con, 3)
    from test_nl151_fetch_skip import _excerpt_brief, _persisted
    _run(db_con, _fetch_failing_slots(1),
         chat=lambda k, p: (_excerpt_brief(), 0.0))
    slot1 = [r for r in _persisted(db_con) if r[0] == 1]
    assert slot1, "slot 1 produced no row at all — the control is not measuring"
    assert any(h.get("degraded") for _s, _t, _st, h in slot1)


def test_the_drop_arm_is_an_unbuilt_placeholder_not_a_second_behaviour():
    """RED at e9c3358 (the helper does not exist).

    HONESTY PIN, and it exists because a constant that names a behaviour
    nobody built is a false affordance. His ruling selected (ii); arm (i)
    "drop" — the story leaving the edition body, which needs the slot-identity
    plumbing NL-151 costed and nobody wrote — was NOT built. The constant
    stays only so the record of the fork is readable in the source.

    So `FETCH_SKIP_ARM_DROP` produces the IN-BRIEF propagation, and that is
    stated here rather than discovered later by someone who set it expecting a
    drop. Retiring the constant, or building (i), is his call; this pin makes
    the current state impossible to misread."""
    assert generate.depth_arm_is_in_brief(analysis.FETCH_SKIP_ARM_IN_BRIEF)
    assert generate.depth_arm_is_in_brief(analysis.FETCH_SKIP_ARM_DROP), (
        "the drop arm silently changed behaviour — if arm (i) was built, this "
        "pin and its docstring are the thing to update first")
    assert not generate.depth_arm_is_in_brief(analysis.FETCH_SKIP_ARM_OFF)


# ===========================================================================
# THE VECTOR — the validator stops being positional
# ===========================================================================

def test_the_validator_takes_the_walks_vector():
    """RED at e9c3358 (`validate_narrative_payload` has no vector parameter).

    generate.py's `allowed` tuple is a REJECTING gate, not a default: under
    arm (ii) a quick lead and a full slot-2 are the correct edition, and today
    that payload raises. This is the single site that made the fork expensive."""
    slots = [gen_slot(i) for i in range(1, 6)]
    payload = stories_payload(slots, tiers=DEMOTED_LEAD_VECTOR)
    stories, _ = generate.validate_narrative_payload(
        payload, slots, "A", depth_tiers=DEMOTED_LEAD_VECTOR)
    assert [s["tier"] for s in stories] == DEMOTED_LEAD_VECTOR


def test_the_validator_still_rejects_a_tier_the_vector_did_not_ask_for():
    """RED at e9c3358. The gate must stay a GATE — vector-driven, not
    vector-suggested. A writer that returns the positional tiers on a day the
    pipeline demoted the lead is returning a full-picture story built on no
    full text, which is exactly what L1 forbids."""
    slots = [gen_slot(i) for i in range(1, 6)]
    positional = stories_payload(slots, tiers=POSITIONAL_VECTOR)
    with pytest.raises(ValueError) as excinfo:
        generate.validate_narrative_payload(
            positional, slots, "A", depth_tiers=DEMOTED_LEAD_VECTOR)
    assert "tier" in str(excinfo.value)


def test_the_validator_without_a_vector_is_todays_positional_gate():
    """CARRIED — and it is the reason the propagation is a small diff rather
    than a 28-file rewrite. `stories_payload` feeds `validate_narrative_payload`
    from twenty-eight test files; the vector is an OPTIONAL argument whose
    absence reproduces the A2 positional contract exactly."""
    slots = [gen_slot(i) for i in range(1, 6)]
    ok = stories_payload(slots)
    stories, _ = generate.validate_narrative_payload(ok, slots, "A")
    assert [s["tier"] for s in stories] == POSITIONAL_VECTOR
    bad = stories_payload(slots, tiers=DEMOTED_LEAD_VECTOR)
    with pytest.raises(ValueError):
        generate.validate_narrative_payload(bad, slots, "A")


def test_the_vector_resolver_falls_back_to_the_positional_contract():
    """RED at e9c3358 (`edition_tiers` does not exist). An edition with no
    stage report — a sample, a `--no-refresh` run on a date the analyst never
    touched, a run whose stage died — has no vector to read, and its answer
    must be today's, not "everything is quick"."""
    assert generate.edition_tiers({}, 5) == POSITIONAL_VECTOR
    assert generate.edition_tiers({"depth_tiers": []}, 5) == POSITIONAL_VECTOR
    assert generate.edition_tiers(
        {"depth_tiers": DEMOTED_LEAD_VECTOR}, 5) == DEMOTED_LEAD_VECTOR
    # a short vector pads with quick rather than silently reverting to
    # positional — a slot the walk never reached was never in the depth tier
    assert generate.edition_tiers({"depth_tiers": ["quick", "full"]}, 4) == \
        ["quick", "full", "quick", "quick"]


# ===========================================================================
# THE WRITER — told the tier, not the position
# ===========================================================================

def test_the_budget_line_keys_on_the_tier_when_one_is_given():
    """RED at e9c3358 (`_slot_budget_line` takes no tier).

    The writer's per-story budget line is the instruction that decides what it
    returns. Under arm (ii) slot 1 must be told IN BRIEF and slot 2 must be
    told it is the lead — keyed on slot number, both are wrong, and the
    validator would then reject a payload the pipeline itself mis-briefed."""
    assert generate._slot_budget_line(1, "quick").startswith("QUICK tier")
    assert generate._slot_budget_line(2, "full").startswith("FULL tier (the lead)")
    assert generate._slot_budget_line(4, "medium").startswith("MEDIUM tier")
    # no tier given -> today's positional line, byte for byte
    assert generate._slot_budget_line(1) == generate._slot_budget_line(1, "full")
    assert generate._slot_budget_line(3) == generate._slot_budget_line(3, "medium")
    assert generate._slot_budget_line(7) == generate._slot_budget_line(7, "quick")


def test_the_prompt_demotes_the_failed_slot_and_promotes_the_next(db_con):
    """RED at e9c3358. The prompt is where the demotion becomes real for the
    writer: STORY 1 gets the In-Brief budget, STORY 2 gets the lead's."""
    slots = [gen_slot(i, title=f"P{i}") for i in range(1, 6)]
    seed_briefing(db_con, DATE, slots)
    inputs = generate.load_briefing_inputs(db_con, DATE)
    inputs["briefs_by_slot"] = {}
    inputs["analyst_slot3_tier"] = None
    inputs["depth_tiers"] = DEMOTED_LEAD_VECTOR

    prompt = generate.build_narrative_prompt(DATE, "A", inputs)
    s1 = prompt.index("STORY 1 —")
    s2 = prompt.index("STORY 2 —")
    s3 = prompt.index("STORY 3 —")
    assert "QUICK tier" in prompt[s1:s2], (
        "the demoted lead is still being briefed as a full-picture story — "
        "the writer will return a tier the validator then rejects")
    assert "FULL tier (the lead)" in prompt[s2:s3]


def test_the_demoted_slot_is_not_told_analysis_is_unavailable(db_con):
    """RED at e9c3358.

    L1's reader-facing half inside the prompt. "(analysis unavailable for this
    story...)" is the DEPTH-tier apology — it exists because a full-picture
    story with no brief is a degraded one. A quick-tier In-Brief story has
    never carried it and must not start: at arm (ii) the demoted slot is not a
    degraded depth story, it is an ordinary In-Brief story, and telling the
    writer otherwise re-imports the degradation L1 removed."""
    slots = [gen_slot(i, title=f"P{i}") for i in range(1, 6)]
    seed_briefing(db_con, DATE, slots)
    inputs = generate.load_briefing_inputs(db_con, DATE)
    # A NON-EMPTY briefs map with nothing behind it, deliberately: the note has
    # a second guard (a wholly absent analysis stage says nothing per-story,
    # because the run-level warning already covers it), and an empty dict would
    # take that branch and make the negative half of this pin pass for the
    # wrong reason. This shape says "the stage ran and these slots got no
    # brief", which is the state the note exists for.
    inputs["briefs_by_slot"] = {2: None}
    inputs["analyst_slot3_tier"] = None
    inputs["depth_tiers"] = DEMOTED_LEAD_VECTOR

    prompt = generate.build_narrative_prompt(DATE, "A", inputs)
    s1 = prompt.index("STORY 1 —")
    s2 = prompt.index("STORY 2 —")
    s3 = prompt.index("STORY 3 —")
    assert "analysis unavailable for this story" not in prompt[s1:s2]
    # ...and an unbriefed slot that IS still in the depth tier keeps it,
    # because that is the degraded-depth case the line was written for
    assert "analysis unavailable for this story" in prompt[s3:]


# ===========================================================================
# BRIEF ROUTING — the brief follows the depth slots, not 1-2-3
# ===========================================================================

def test_the_run_reads_briefs_at_the_promoted_slots(db_con, monkeypatch,
                                                    fake_model):
    """RED at e9c3358. Slot 4 is promoted into the depth tier and its brief is
    persisted at slot 4 — but generate's brief lookup asks for slots 1-3 only,
    so the promoted story's analysis never reaches the writer. The reader gets
    a lead built from excerpts while its brief sits unread in the database."""
    _armed_world(db_con, monkeypatch, fake_model, tiers=DEMOTED_LEAD_VECTOR)
    _real_stage_with(monkeypatch, _dead_urls(1))
    generate.run_generate(date=A_DAY, con=db_con, env=GEN_ENV, refresh=True)

    stage = _stage_entry()
    assert stage["depth_slots"] == [2, 3, 4]
    assert analysis.latest_valid_brief(db_con, A_DAY, 4) is not None
    entry = _run_entry()
    assert entry["deep_views"] == {"2": "available", "3": "available",
                                   "4": "available"}, (
        "deep_views still counts slots 1-3 — the ladder denominator is the "
        "DEPTH tier, and after a demotion those are not the same set")


# ===========================================================================
# END TO END, AT THE ARMED DEFAULT (nothing monkeypatched)
# ===========================================================================

def test_e2e_the_demoted_story_is_in_brief_and_the_promoted_one_leads(
        db_con, monkeypatch, fake_model):
    """RED at e9c3358 — the headline pin of this batch. No arm monkeypatch:
    this is what a real morning does once the default is his ruled arm."""
    _armed_world(db_con, monkeypatch, fake_model, tiers=DEMOTED_LEAD_VECTOR)
    _real_stage_with(monkeypatch, _dead_urls(1))
    generate.run_generate(date=A_DAY, con=db_con, env=GEN_ENV, refresh=True)

    entry = _run_entry()
    assert entry["tiers"] == DEMOTED_LEAD_VECTOR, (
        "the edition of record still runs the positional tier vector — the "
        "demotion stopped at the analysis stage and never reached the reader")
    assert [s["slot"] for s in entry["fetch_skipped"]] == [1]
    assert entry["fetch_skipped"][0]["disclose"] is True


def test_e2e_no_depth_story_carries_the_degraded_apology(db_con, monkeypatch,
                                                         fake_model):
    """RED at e9c3358. L1 IN THE ARTIFACT the reader actually reads. "Analysis:
    unavailable — built from feed excerpts." is the sentence that says a depth
    slot is degraded; after the demotion no story in the edition may carry it,
    because the demoted one is not depth any more and the promoted ones have
    real briefs."""
    _armed_world(db_con, monkeypatch, fake_model, tiers=DEMOTED_LEAD_VECTOR)
    _real_stage_with(monkeypatch, _dead_urls(1))
    generate.run_generate(date=A_DAY, con=db_con, env=GEN_ENV, refresh=True)

    row = db_con.execute("SELECT narrative_text FROM briefings WHERE date = ?",
                         (A_DAY,)).fetchone()
    narrative = row["narrative_text"] or ""
    assert "Analysis: unavailable" not in narrative, (
        "a depth slot in the published edition is disclosing itself as "
        "excerpt-built — L1 says the depth tier never carries that, ever")


def test_e2e_the_disclosure_is_his_sentence_at_the_literal_bottom(
        db_con, monkeypatch, fake_model):
    """RED at e9c3358 (at the OFF default nothing is skipped, so nothing is
    disclosed). Re-proven at the ARMED DEFAULT rather than under a
    monkeypatched arm — NL-151's pins proved the renderer; this proves the
    morning."""
    from newslens import ranking
    _armed_world(db_con, monkeypatch, fake_model, tiers=DEMOTED_LEAD_VECTOR)
    _real_stage_with(monkeypatch, _dead_urls(1))
    generate.run_generate(date=A_DAY, con=db_con, env=GEN_ENV, refresh=True)

    row = db_con.execute("SELECT narrative_text FROM briefings WHERE date = ?",
                         (A_DAY,)).fetchone()
    narrative = row["narrative_text"] or ""
    line = "Fetch failed for prioritized story NL151b Story 1."
    assert line in narrative
    assert narrative.count(line) == 1
    assert narrative.find(ranking.CORROBORATION_CAVEAT) < narrative.find(line)
    assert narrative.rstrip().endswith("*" + line + "*")


def test_e2e_the_promoted_story_carries_no_badge(db_con, monkeypatch,
                                                 fake_model):
    """RED at e9c3358. TRUST-QUIET, as a test. The promoted story is the
    reader's lead and it reads like any other lead: no "promoted", no
    "stand-in", no marker of the machinery that put it there. The one thing
    the reader is told about the day's failure is his one sentence, at the
    bottom, about the story that failed — not a running commentary on the
    story that replaced it."""
    _armed_world(db_con, monkeypatch, fake_model, tiers=DEMOTED_LEAD_VECTOR)
    _real_stage_with(monkeypatch, _dead_urls(1))
    generate.run_generate(date=A_DAY, con=db_con, env=GEN_ENV, refresh=True)

    row = db_con.execute("SELECT narrative_text FROM briefings WHERE date = ?",
                         (A_DAY,)).fetchone()
    narrative = row["narrative_text"] or ""
    head = narrative[:narrative.find("Fetch failed")]
    for word in ("promoted", "Promoted", "stand-in", "replacement lead",
                 "moved up", "substitute"):
        assert word not in head, (
            f"the body announces the promotion ({word!r}) — arm (ii) moves a "
            "tier, it does not narrate itself to the reader")


# ===========================================================================
# THE READER — what the demoted story's tap does
# ===========================================================================

def test_the_demoted_story_taps_into_sources_and_context(db_con, monkeypatch,
                                                         fake_model):
    """RED at e9c3358.

    Requirement 3, and it is a CONFORMANCE pin rather than a new surface: an
    In-Brief story's tap has been the $0 "Sources & context" view since NL-66b
    (`_deep_entry_link`, three binding states). The demoted story is an
    In-Brief story, so it must get that view and nothing invented for it. The
    negative half matters as much: it must NOT get the degraded-hidden silence
    a failed full/medium brief gets, because it is not a failed depth slot any
    more — it is an ordinary quick story with sources to show."""
    _armed_world(db_con, monkeypatch, fake_model, tiers=DEMOTED_LEAD_VECTOR)
    _real_stage_with(monkeypatch, _dead_urls(1))
    generate.run_generate(date=A_DAY, con=db_con, env=GEN_ENV, refresh=True)

    page, _ = server.build_page(db_con, A_DAY)
    assert "sources-context-link" in page, (
        "the demoted lead offers the reader no way into its sources — the "
        "quick tier's own $0 view is the conforming answer, not silence")
    # the label as the page escapes it (`&` -> `&amp;`), not as labels.py
    # spells it — asserting the raw constant would pass over an unescaped
    # render, which is a different bug
    assert escape(labels.SOURCES_CONTEXT) in page


def test_the_promoted_story_taps_into_the_full_picture(db_con, monkeypatch,
                                                       fake_model):
    """RED at e9c3358. The other half: the promoted story has a real brief, so
    its tap is the full deep view — the depth treatment moved WITH the tier."""
    _armed_world(db_con, monkeypatch, fake_model, tiers=DEMOTED_LEAD_VECTOR)
    _real_stage_with(monkeypatch, _dead_urls(1))
    generate.run_generate(date=A_DAY, con=db_con, env=GEN_ENV, refresh=True)

    page, _ = server.build_page(db_con, A_DAY)
    assert labels.FULL_PICTURE in page


# ===========================================================================
# RECOVERY — the paths where the stage does not run
# ===========================================================================

def test_a_no_refresh_run_recovers_the_depth_vector_from_the_record(
        db_con, monkeypatch, fake_model):
    """RED at e9c3358, and this is the L1 HOLE the propagation opens if nobody
    closes it — found in this build, not inherited.

    `--no-refresh` (and a sample, and the backfill) do not run the analysis
    stage, so there is no live `depth_tiers` to consume. Reverting to the
    positional vector there would put slot 1 back in the depth tier with no
    brief — a degraded lead, on the exact path a principal uses to finish an
    interrupted regenerate. The vector is recovered from the run record the
    armed stage already wrote."""
    _armed_world(db_con, monkeypatch, fake_model, tiers=DEMOTED_LEAD_VECTOR)
    _real_stage_with(monkeypatch, _dead_urls(1))
    generate.run_generate(date=A_DAY, con=db_con, env=GEN_ENV, refresh=True)

    recovered = analysis.depth_tiers_from_record(A_DAY, 5)
    assert recovered == DEMOTED_LEAD_VECTOR

    # ...and a date the analyst never touched recovers NOTHING, so its
    # consumers fall back to the positional contract rather than to "quick".
    assert analysis.depth_tiers_from_record("1999-01-01", 5) == []


def test_the_recovery_is_armed_only_so_off_is_byte_identical(db_con,
                                                             monkeypatch):
    """CARRIED-shape pin, RED at e9c3358 (the function does not exist).

    At the OFF arm the recovery must not fire at all. A legacy run that lost
    slot 3 to the budget floor persists no depth row for it, and a record-
    driven vector would call that slot quick — changing `--no-refresh`
    behaviour on a path this ruling never touched."""
    monkeypatch.setattr(analysis, "FETCH_SKIP_ARM", analysis.FETCH_SKIP_ARM_OFF)
    assert generate.edition_tiers(
        {"depth_tiers": DEMOTED_LEAD_VECTOR}, 5) == POSITIONAL_VECTOR, (
        "the OFF arm consumed a depth vector — OFF must reproduce today's "
        "positional edition whatever the record says")


# ===========================================================================
# CLAUSE-4 COMPOSITION, RE-PROVEN AT THE ARMED DEFAULT
# ===========================================================================

def test_clause4_a_systemic_day_still_pauses_at_the_armed_default(db_con):
    """RED-adjacent / CARRIED (the assertion holds at OFF too; its value is
    that it holds AFTER the flip). NL-148 clause 4's pause must not dissolve
    into seven disclosed demotions now that the demotion is live by default.
    No monkeypatch — this is the real arm."""
    _seed_slots(db_con, 7)
    rep = _run(db_con, _fetch_all_fail)
    assert rep["fetch_systemic_failure"] is True
    assert rep["depth_slots"] == []
    assert rep["depth_tiers"] == ["quick"] * 7


def test_one_failed_slot_demotes_and_does_not_pause(db_con, monkeypatch,
                                                    fake_model):
    """RED at e9c3358. The other side of the composition at PIPELINE level and
    at the armed default: one dead slot produces an edition, not a pause."""
    _armed_world(db_con, monkeypatch, fake_model, tiers=DEMOTED_LEAD_VECTOR)
    _real_stage_with(monkeypatch, _dead_urls(1))
    rep = generate.run_generate(date=A_DAY, con=db_con, env=GEN_ENV,
                                refresh=True)
    assert rep.artifact_path
    assert _stage_entry()["fetch_systemic_failure"] is False
    assert _run_entry()["status"] == "ok"


# ===========================================================================
# THE RUN RECORD AND THE REPORTS SCREEN (NL-146 / NL-149 surfaces)
# ===========================================================================

def test_the_run_record_carries_the_demotion(db_con, monkeypatch, fake_model):
    """RED at e9c3358. NL-149's reports screen reads the run record; the
    demotion has to BE there before it can be shown honestly."""
    _armed_world(db_con, monkeypatch, fake_model, tiers=DEMOTED_LEAD_VECTOR)
    _real_stage_with(monkeypatch, _dead_urls(1))
    generate.run_generate(date=A_DAY, con=db_con, env=GEN_ENV, refresh=True)

    entry = _run_entry()
    assert entry["depth_slots"] == [2, 3, 4]
    assert entry["tiers"] == DEMOTED_LEAD_VECTOR
    assert entry["fetch_skipped"][0]["story_title"] == "NL151b Story 1"


def test_the_reports_screen_names_the_skip(db_con, monkeypatch, fake_model):
    """RED at e9c3358. A scheduled run that quietly demoted his lead and told
    him nothing on the one screen whose whole job is the record would be the
    NL-146 charter's own failure mode. One line, counted, no drama."""
    _armed_world(db_con, monkeypatch, fake_model, tiers=DEMOTED_LEAD_VECTOR)
    _real_stage_with(monkeypatch, _dead_urls(1))
    generate.run_generate(date=A_DAY, con=db_con, env=GEN_ENV, refresh=True)

    html = server._render_run(_run_entry())
    assert labels.RUNLOG_DEPTH_SKIPPED_ONE in html


def test_the_reports_screen_says_nothing_on_a_healthy_run():
    """CARRIED. The skip line renders only when a skip happened — a run report
    that mentions demotions on an ordinary morning is furniture."""
    html = server._render_run({"ts": "2026-08-14T12:00:00+00:00", "date": A_DAY,
                               "status": "ok", "total_usd": 0.0})
    assert "skipped" not in html.lower()
    assert "demot" not in html.lower()


# ===========================================================================
# THE NINTH SITE — Concept B's loudness grammar (design harness, $0)
# ===========================================================================

def test_the_concept_b_pack_calls_the_demoted_story_an_in_brief_move(
        db_con, monkeypatch, fake_model):
    """RED at e9c3358.

    The last of the nine positional sites, and the only one outside the
    product's own render path: `moat_battery`'s T3 subcommand extracts the
    material Design hand-builds Concept B from, at $0, and labels each story
    lead / secondary / in-brief. Design's three-level GRAMMAR is untouched —
    it is not this batch's to amend — but which story sits at which level now
    follows the edition. A pack that calls a demoted story "the lead file"
    feeds the design exploration a fact the edition contradicts."""
    from newslens import moat_battery
    _armed_world(db_con, monkeypatch, fake_model, tiers=DEMOTED_LEAD_VECTOR)
    _real_stage_with(monkeypatch, _dead_urls(1))
    generate.run_generate(date=A_DAY, con=db_con, env=GEN_ENV, refresh=True)

    inputs = generate.load_briefing_inputs(db_con, A_DAY)
    _md, payload = moat_battery.concept_b_pack(db_con, A_DAY, inputs)
    loud = {s["slot"]: s["loudness"]
            for s in payload["moved_files"] + payload["new_files"]}
    assert loud[1] == "in-brief", (
        "the design pack still calls the demoted story the lead file")
    assert loud[2] == "lead"
    assert loud[3] == "secondary" and loud[4] == "secondary"


def test_the_concept_b_pack_keeps_the_positional_grammar_without_a_record(
        db_con):
    """CARRIED. An edition with no recorded vector — every edition before the
    contract, and every date the analyst never touched — gets exactly the
    grammar it shipped with."""
    from newslens import moat_battery
    slots = [gen_slot(i, title=f"C{i}") for i in range(1, 6)]
    assert moat_battery._loudness_by_slot("1999-01-01", slots) == {
        1: "lead", 2: "secondary", 3: "secondary",
        4: "in-brief", 5: "in-brief"}


def test_the_concept_b_pack_labels_the_edition_that_shipped_not_the_stage(
        db_con):
    """BORN RED at pre-FIX-3 bytes (gate R-D, 2026-08-14 — the re-aim).

    THE WINDOW: an analysis stage ran and demoted the lead, and then the run
    died before publishing (an interrupted regenerate, a stage whose run never
    finished). The stage entry is on the record; there is NO run entry, so the
    date's SHIPPED edition — the page this pack feeds Design from — is still
    whatever positional edition is on screen.

    `depth_tiers_from_record` answers the STAGE's question ("what vector did
    the analyst walk"), which is the right question for the recovery inside
    `run_generate` and the WRONG one here: it made the pack call slot 2 the
    lead file while the shipped page's lead is slot 1. The docstring already
    claimed "the edition's own depth-tier vector"; `_tiers_for` is the function
    that answers exactly that (last published edition), so the code moves to
    match the claim rather than the claim moving to match the code.

    Pre-fix failure line: `loud[1] == 'lead'` — got 'in-brief'."""
    import json
    from newslens import moat_battery, paths
    slots = [gen_slot(i, title=f"S{i}") for i in range(1, 6)]
    log = paths.DATA_DIR / "generation_log.jsonl"
    log.parent.mkdir(parents=True, exist_ok=True)
    # a STAGE entry only — no run entry for this date anywhere in the log
    log.write_text(json.dumps({
        "ts": "2026-08-14T06:10:00+00:00", "stage": "analysis",
        "date": A_DAY, "status": "ok", "total_usd": 0.0,
        "depth_tiers": DEMOTED_LEAD_VECTOR}) + "\n", encoding="utf-8")
    assert analysis.depth_tiers_from_record(A_DAY, 5) == DEMOTED_LEAD_VECTOR
    assert not [e for e in _log_entries()
                if "stage" not in e and e.get("date") == A_DAY]

    assert moat_battery._loudness_by_slot(A_DAY, slots) == {
        1: "lead", 2: "secondary", 3: "secondary",
        4: "in-brief", 5: "in-brief"}, (
        "the design pack labels by a vector no published edition ran — the "
        "pack and the page it feeds Design from disagree about the lead")


# ===========================================================================
# TIER CONTINUITY — the stage re-reading its own published vector
# ===========================================================================

def test_the_stages_own_tier_default_survives_an_armed_edition(db_con,
                                                               monkeypatch,
                                                               fake_model):
    """RED at e9c3358 (nothing armed publishes a demoted vector today).

    `analysis._tiers_for` seeds the stage's tiers from the last published
    edition when no override is given. After an armed morning that vector is
    ["quick","full","medium","medium","quick"] — and the walk must still
    extract a depth QUEUE of one full and two mediums from it, or a re-run of
    the stage on that date would analyse nothing at all."""
    _armed_world(db_con, monkeypatch, fake_model, tiers=DEMOTED_LEAD_VECTOR)
    _real_stage_with(monkeypatch, _dead_urls(1))
    generate.run_generate(date=A_DAY, con=db_con, env=GEN_ENV, refresh=True)

    tiers = analysis._tiers_for(A_DAY, 5)
    assert tiers == DEMOTED_LEAD_VECTOR
    assert [t for t in tiers if t in ("full", "medium")] == \
        ["full", "medium", "medium"]
