"""THE ARC-LINE CONTRACT v1 — QA adversarial pins (arc-line batch, 2026-07-18).

QA-owned companions to tests/test_arc_line.py. R4 honesty label: every test in
this file is a **carried-invariant / pin (born-green)** — none claims born-red.
They pin current behavior at the batch's judgment-call boundaries so any later
change is a CONSCIOUS flip, and they close coverage the implementer's file
leaves open (migration upgrade path on a trigger-armed DB, the repair-path arc,
the timeline/arc thread-resolution divergence, the baseline anti-obligation
invariant on the NEW arc path, the §D strip-test residual, §F.1 calibration
anchors at the measured margins, and the BUG-22 normalization re-pin orphaned
by the arc-render deletion).

Threshold verdict carried here (QA tuning, §F.1 "QA tunes", evidence in the
2026-07-18 QA report): the starter thresholds STAND — ≥6-word run OR >0.40
directed (arc∩state)/|arc|, stopwords included. Measured: the served defect and
every trivial mutation trip (reorder 0.667, 2-synonym 0.588/run6, 4-synonym
0.471 — fraction-prong only, the thinnest must-catch margin); genuinely
independent lines clear at ~0.23; the two false-trip classes (verbatim ≥6-word
endpoint quotes; short topic-heavy lines vs long states, up to 0.70) both
degrade the SAFE direction (corrected retry that coaches the fix, then
absence — never a shipped bad line). Dropping stopwords or raising either
threshold loses the 4-synonym catch; lowering either false-rejects the lawful
short-line class harder. Offline, deterministic, $0.
"""

import json
import shutil

import pytest

from newslens import db, generate, llm, memory_core, paths, server
from conftest import PROTOTYPE_ROOT


# --- fixtures (mirror tests/test_arc_line.py) --------------------------------

def _seed_thread(con, topic):
    now = "2026-07-01T00:00:00.000Z"
    cur = con.execute(
        "INSERT INTO memory (topic, status, status_changed_at, created_at,"
        " updated_at) VALUES (?, 'active', ?, ?, ?)", (topic, now, now, now))
    return cur.lastrowid


def _write_delta(con, tid, date, what="A dated development.",
                 signif="Changed the frame.", cites=("S1",)):
    cur = con.execute(
        "INSERT INTO thread_deltas (thread_id, edition_date, verdict,"
        " what_happened, significance, cites_json) VALUES"
        " (?, ?, 'advances', ?, ?, ?)",
        (tid, date, what, signif, json.dumps(list(cites))))
    con.commit()
    return cur.lastrowid


def _seed_state_row(con, tid, date, arc_line, state_text="s (Jul 10)."):
    con.execute(
        "INSERT INTO thread_state (thread_id, as_of_date, state_text, arc_line)"
        " VALUES (?, ?, ?, ?)", (tid, date, state_text, arc_line))
    con.commit()


_TEMPLATE = "topic={topic} date={date}\n{ledger}"

V = memory_core.validate_arc_line
R = memory_core.ArcLineRejected
_STATE = "The strait is closed and shipping has rerouted (Jul 10)."


# ===========================================================================
# 1. VALIDATOR BOUNDARY PINS — flagged judgment calls, visible to the gate
# ===========================================================================

def test_bare_lowercase_us_trips_the_we_ban_gate_R5():
    """CONSCIOUS FLIP of the interpretation-7 pin (gate ruling R5, 2026-07-18):
    _ARC_BAN_WE gains the scoped case-sensitive island (?-i:us) — lowercase
    'us' (the newsroom pronoun; its only lowercase reading in this register)
    now REJECTS, completing §F.4's ban paradigm (we/us/our/ours/ourselves).
    The collision the original omission protected stays protected: 'US' /
    'U.S.' (United States) MUST keep passing."""
    with pytest.raises(R):
        V("The story reached us on Jul 5; a war has since broken out.",
          _STATE, "2026-07-05")
    with pytest.raises(R):
        V("Sources told us on Jul 5 that fees rose; a war has since broken out.",
          _STATE, "2026-07-05")
    # the collision the island preserves, both caps forms — MUST keep passing:
    V("US sanctions tightened after Jul 5; a war has since broken out.",
      _STATE, "2026-07-05")
    V("U.S. sanctions tightened after Jul 5; a war has since broken out.",
      _STATE, "2026-07-05")


def test_one_sentence_check_survives_protected_abbreviations():
    """§E's one-sentence count must not split on domain abbreviations
    (_ABBR_PROTECT: U.S./U.N./U.K./E.U./U.S.A.) nor on decimals; semicolon and
    em-dash joins are lawful §E clause joiners, never sentence breaks."""
    line = ("On Jul 5 U.S. escorts held the strait; a war has since broken "
            "out — E.U. transit included.")
    assert V(line, _STATE, "2026-07-05")[0] == line
    line2 = "On Jul 5 exports ran 1.5 million barrels; the flow has since halved."
    assert V(line2, _STATE, "2026-07-05")[0] == line2


def test_PIN_dotted_month_and_honorific_false_reject_as_two_sentences():
    """PIN of a known false-reject residual (safe direction — retry, then
    absence; never a shipped bad line): _MONTH_DAY_RE accepts 'Jul. 5' as an
    anchor form, but _sentences splits on the dot, so the SAME line rejects as
    two sentences — the two checks disagree about dotted months. Honorifics
    ('Mr.') split identically. Documented, not fixed here: extending
    _ABBR_PROTECT is shared-surface (validate_state's sentence cap counts with
    the same splitter) and belongs to its own change if the cost ever shows up
    on real editions."""
    with pytest.raises(R, match="sentence"):
        V("As of Jul. 5 fees were the dispute; a war has since broken out.",
          _STATE, "2026-07-05")
    with pytest.raises(R, match="sentence"):
        V("On Jul 5 Mr. Araghchi still negotiated; talks have since collapsed.",
          _STATE, "2026-07-05")


def test_anchor_present_with_other_dates_passes():
    """§C.1 binds the anchor to the ledger's last-covered date but does not
    forbid OTHER dates — the ADVANCE class dates its delta ('after the Jul 3
    retaliation'). Any-match semantics, pinned."""
    line = ("On Jul 5 fees ruled the dispute; after the Jul 3 retaliation a "
            "war has since broken out.")
    assert V(line, _STATE, "2026-07-05")[0] == line


def test_PIN_forward_ban_is_tense_blind_expected_and_likely_to_trip():
    """PIN of a documented false-trip class: §F.3's lexicon ('expect',
    'likely to') fires on PAST-TENSE backward-looking then-legs — exactly the
    REVERSAL class's natural phrasing ('a reopening was expected within
    days'). Contract-literal (§F.3 names the lexemes); the contract's own
    REVERSAL specimen writes around it ('looked likely within days' — no
    'likely to'), and the corrected retry names the rule, so the recovery path
    is real. Cost: one retry (or absence) on expectation-verb then-legs."""
    with pytest.raises(R, match="forward"):
        V("On Jul 5 a reopening was expected within days; the strait has "
          "since stayed shut.", _STATE, "2026-07-05")
    with pytest.raises(R, match="forward"):
        V("On Jul 5 a deal looked likely to close; the talks have since "
          "collapsed.", _STATE, "2026-07-05")


def test_contract_worked_specimens_pass_the_validator():
    """The contract's own ADVANCE, REVERSAL, and skeleton-freedom specimens
    (2026-07-18 debate, worked examples) pass against realistic paired states —
    guarding the lexicon/length/anchor checks against future over-tightening
    that would outlaw the contract's own exhibits. (The FULL reframe specimen
    is deliberately NOT here — see the §F.1 boundary pin below.)"""
    V("When this record last covered Hormuz (Jul 16), Israeli strikes had "
      "just resumed after the Jul 10 retaliation; a third exchange has since "
      "followed, the largest yet.",
      "A third exchange of strikes is under way, the largest of the war so "
      "far; ports remain shut (Jul 17).", "2026-07-16")
    V("When this record last covered Hormuz (Jul 16), a negotiated reopening "
      "looked likely within days; that expectation has collapsed — the strait "
      "stayed closed and the talks went unmentioned in today's sourcing.",
      "The strait remains closed and no talks are scheduled (Jul 17).",
      "2026-07-16")
    V("The strait's closure has outlived the fighting that caused it: when "
      "this record last covered Hormuz (Jul 16), the strikes were the story; "
      "today the restructuring is.",
      "Shipping and insurance are restructuring around a strait treated as "
      "closed (Jul 17).", "2026-07-16")


# ===========================================================================
# 2. §F.1 CALIBRATION ANCHORS — the measured margins, pinned
# ===========================================================================

_DEFECT_ERA_STATE = ("The conflict has moved beyond economic disputes into "
                     "open competition for the strait's traffic (Jul 16).")


def test_defect_mutations_trip_reorder_synonyms_noise():
    """Anti-evasion floor: the served defect must trip under trivial mutation.
    Reorder and noise ride the run prong; the 4-synonym paraphrase rides ONLY
    the directed fraction (measured 0.471 vs the 0.40 bar — the thinnest
    must-catch margin in the calibration). If a threshold change breaks any of
    these, mutation robustness is lost — that is the flip this pin makes
    conscious."""
    for mutated in (
        # clauses reordered (run prong):
        "The conflict has moved beyond economic disputes into open "
        "competition, as when this record last covered it (Jul 16).",
        # 2 synonyms swapped (run + fraction):
        "When this record last covered it (Jul 16), the dispute has shifted "
        "beyond economic disputes into open competition.",
        # 4 synonyms swapped (FRACTION PRONG ONLY, margin 0.071):
        "When this record last covered it (Jul 16), the standoff has drifted "
        "past commercial disputes into open competition.",
        # punctuation/whitespace noise (normalizer):
        "When this record last covered it (Jul 16) — the conflict, has moved "
        "— beyond economic disputes,  into open competition...",
        # whole state pasted under a lawful anchor clause:
        "When this record last covered it (Jul 16), the conflict has moved "
        "beyond economic disputes into open competition for the strait's "
        "traffic.",
    ):
        assert memory_core.arc_overlap_trips(mutated, _DEFECT_ERA_STATE), (
            f"defect mutation escaped §F.1: {mutated!r}")


def test_PIN_verbatim_endpoint_quote_trips_reworded_endpoint_passes():
    """The §A/§F.1 boundary, pinned with the CONTRACT'S OWN specimens: a
    now-as-endpoint is licensed (§A), but an endpoint that QUOTES the current
    state for ≥6 contiguous words is treated as paste — the contract's FULL
    reframe specimen (endpoint spelled out) and its minimal-repair specimen
    both TRIP against states that phrase the same endpoint (measured run 7 /
    frac 0.483 and run 8 / 0.593). Working-as-intended verdict: §F.1's job is
    forcing the arc to say the endpoint in its own words; the corrected retry
    coaches exactly that, and the reworded endpoint passes with margin (0.259).
    NB the implementer's calibration test uses the TRUNCATED reframe form —
    this pin carries the full-form behavior honestly."""
    state = ("Shipping and insurance are restructuring around a strait "
             "treated as closed; tanker rates have tripled since the strikes "
             "(Jul 17).")
    full = ("When this record last covered Hormuz (Jul 16), the story was the "
            "strikes themselves; since then it has become the markets — "
            "shipping and insurance restructuring around a strait treated as "
            "closed.")
    with pytest.raises(R, match="reproduces the state"):
        V(full, state, "2026-07-16")
    reworded = ("When this record last covered Hormuz (Jul 16), the story was "
                "the strikes themselves; since then it has become the markets "
                "— insurers and shippers now price the strait as shut.")
    assert V(reworded, state, "2026-07-16")[0] == reworded


def test_PIN_short_topic_heavy_lawful_line_false_trips_the_fraction():
    """PIN of the second documented false-trip class: a SHORT lawful line
    (≤ ~13 words) over a LONG state inflates the directed fraction — every
    thread-vocabulary noun is a large step of |arc|. Measured 0.545 here.
    Degrade direction is safe (retry coaches 'under 40% shared words' → the
    model pads/rephrases; worst case absence). Pinned so a threshold retune
    weighs this class against test_defect_mutations' 0.471 must-catch —
    the two bounds bracket the fraction bar from both sides."""
    long_state = ("The strait is closed to commercial transit and the strikes "
                  "have paused; insurance rates for the region have tripled "
                  "and talks are stalled in Vienna with no date set to resume "
                  "(Jul 16).")
    short_lawful = "On Jul 16 the strait was closed; transit has since resumed."
    with pytest.raises(R, match="reproduces the state"):
        V(short_lawful, long_state, "2026-07-16")


# ===========================================================================
# 3. THE §D STRIP-TEST RESIDUAL — made visible, sized, and pinned
# ===========================================================================

def test_PIN_tense_splice_by_paraphrase_passes_the_mechanical_floor():
    """THE §D RESIDUAL, pinned at its true size (QA boundary confirmation,
    dispatch item 2): a tense splice whose present-state payload is PARAPHRASED
    (tokens disjoint from the state summary) passes every mechanical check —
    anchor correct, one sentence, ≤35 words, no banned lexicon, overlap far
    under threshold (measured frac 0.125, no run). 'maritime commerce is
    reorganizing' under a Jul-16 anchor IS the defect class, mechanically
    invisible. QA searched for a structural mechanization and confirms the
    implementer's boundary: a tense classifier is teeth on phrasing (Clash-1
    bars it) and a remainder-vs-state proxy false-rejects the licensed REFRAME
    class (§A). This defect class is owned by the state spot-check + falsifier
    #1 (one served occurrence returns it to the content seat) with the
    pre-registered writer-seat revert behind it. If generation-time §D teeth
    are ever added, THIS pin flips consciously — that is its job."""
    state = ("Shipping and insurance are restructuring around a strait "
             "treated as closed (Jul 17).")
    splice = ("When this record last covered Hormuz (Jul 16), maritime "
              "commerce is reorganizing around the blocked waterway.")
    clean, _ = V(splice, state, "2026-07-16")   # passes — the residual, honestly
    assert clean == splice


# ===========================================================================
# 4. AUTHORING-GATE EDGES — baseline, non-string, superseded anchor, repair
# ===========================================================================

def test_baseline_never_creates_prior_coverage_for_the_arc(migrated_con):
    """NL-77's anti-obligation invariant carried onto the NEW arc path (the
    deleted test_baseline_never_feeds_today_arc guarded the deleted render;
    its today_memory_stamp repurpose covers the stamp only): a thread whose
    ONLY prior context is an entry-zero baseline gets NO arc line — the
    baseline is external synthesis, never edition-cited coverage (§C.2,
    kill-test law), and prior_dates reads the LEDGER alone."""
    con = migrated_con
    tid = _seed_thread(con, "Blockade")
    memory_core.record_baseline(con, tid, "2026-07-01", "ready",
                                backgrounder="Ships were blocked in March.")
    _write_delta(con, tid, "2026-07-10", what="First ledger development.")
    res = memory_core.rewrite_state(
        con, tid, "Blockade", "2026-07-10", None, "k", _TEMPLATE, 1.0,
        chat=lambda k, p: ({"state": "First development on record (Jul 10).",
                            "arc_line": "When this record last covered it "
                            "(Jul 1), ships were blocked; a first development "
                            "has since landed."}, 0.001))
    assert res.outcome == "written"
    row = memory_core.state_for_edition(con, tid, "2026-07-10")
    # day-one-by-ledger: even a model-volunteered, baseline-dated line is absence
    assert row["arc_line"] == ""


def test_non_string_arc_line_degrades_to_absence_no_crash(migrated_con):
    """BUG-31 class on the arc field: int / list / dict arc_line values never
    crash the paid rewrite and never retry (semantics per gate FIX-2: non-string
    == authored-none == lawful absence WITH the observability warn, same lane
    as the missing key)."""
    con = migrated_con
    tid = _seed_thread(con, "Strait")
    _write_delta(con, tid, "2026-07-05", what="Fees imposed.")
    _write_delta(con, tid, "2026-07-10", what="Strikes.")
    for weird in (7, ["a", "b"], {"text": "x"}, None):
        res = memory_core.rewrite_state(
            con, tid, "Strait", "2026-07-10", None, "k", _TEMPLATE, 1.0,
            chat=lambda k, p, w=weird: ({"state": _STATE, "arc_line": w}, 0.001))
        assert res.outcome == "written"
    row = memory_core.state_for_edition(con, tid, "2026-07-10")
    assert row["arc_line"] == ""


def test_superseded_last_delta_moves_the_anchor_to_the_live_date(migrated_con):
    """Rook's-gate coherence: the anchor is the last LIVE prior date — a
    superseded (corrected-away) newest prior delta must not be the 'last
    covered' the arc cites, matching what the timeline shows struck-through
    and what the state regenerates from."""
    con = migrated_con
    tid = _seed_thread(con, "Strait")
    _write_delta(con, tid, "2026-07-03", what="Fees imposed.")
    bad = _write_delta(con, tid, "2026-07-05", what="Wrong claim.")
    fix = _write_delta(con, tid, "2026-07-03", what="Correction of the claim.")
    _write_delta(con, tid, "2026-07-10", what="Strikes broke out.")  # today's move
    con.execute(
        "INSERT INTO thread_delta_supersessions (delta_id, superseded_by,"
        " reason) VALUES (?, ?, 'corrected')", (bad, fix))
    con.commit()

    def chat(key, prompt):
        return ({"state": "Corrected record stands (Jul 10).",
                 "arc_line": "On Jul 3 fees were the dispute; strikes have "
                             "since broken out."}, 0.001)

    res = memory_core.rewrite_state(
        con, tid, "Strait", "2026-07-10", None, "k", _TEMPLATE, 1.0, chat=chat)
    assert res.outcome == "written"
    row = memory_core.state_for_edition(con, tid, "2026-07-10")
    # anchored Jul 3 (last LIVE) — a Jul 5 anchor would have been rejected:
    assert "Jul 3" in row["arc_line"]


def test_repair_path_authors_an_arc_stamped_at_the_repaired_delta_date(migrated_con):
    """Interpretation 6 (implementer-flagged): run_state_repair → rewrite_state
    stamped at the latest LIVE delta's date also authors an arc line. Coherence
    pinned: the anchor is the last live date BEFORE the repaired date (Jul 10),
    the row lands as_of the repaired delta's date (Jul 14), and THAT edition's
    deep view serves it — the repaired edition gains its arc retroactively,
    versioned correctly (state_for_edition on Jul 14, nothing on Jul 15)."""
    con = migrated_con
    tid = _seed_thread(con, "Iran War")
    _write_delta(con, tid, "2026-07-10", what="Strikes began.")
    _seed_state_row(con, tid, "2026-07-10", "", "Strikes began (Jul 10).")
    _write_delta(con, tid, "2026-07-14", what="Ceasefire signed.")  # rewrite failed

    def chat(key, prompt):
        return ({"state": "A ceasefire is signed and holding (Jul 14).",
                 "arc_line": "When this record last covered the war (Jul 10), "
                             "strikes had just begun; a ceasefire has since "
                             "been signed."}, 0.001)

    rep = generate.run_state_repair(thread_id=tid, con=con,
                                    env={"OPENAI_API_KEY": "k"},
                                    state_chat=chat)
    assert rep.refused is False
    row = memory_core.state_for_edition(con, tid, "2026-07-14")
    assert row is not None and "Jul 10" in row["arc_line"]
    assert memory_core.state_for_edition(con, tid, "2026-07-15") is None


# ===========================================================================
# 5. RENDER EDGES — divergence pin, whitespace absence
# ===========================================================================

def test_PIN_timeline_and_arc_can_resolve_DIFFERENT_threads(migrated_con):
    """DIVERGENCE PIN (implementer-flagged surface, interpretation 5): the
    timeline returns the first matched thread WITH LEDGER ROWS; the arc render
    returns the first matched thread WITH A NON-EMPTY ARC. On a multi-thread
    slot where the first recorded thread authored no arc this edition, the deep
    view shows thread A's timeline over thread B's arc line. Pinned as CURRENT
    BEHAVIOR for the gate — a same-thread rule (arc from the timeline's thread
    only) is the alternative; design's call, not silently changed here."""
    con = migrated_con
    a = _seed_thread(con, "Alpha")
    b = _seed_thread(con, "Beta")
    _write_delta(con, a, "2026-07-05", what="Alpha prior fact.")
    _write_delta(con, b, "2026-07-05", what="Beta prior fact.")
    _seed_state_row(con, a, "2026-07-10", "")                    # A: no arc
    _seed_state_row(con, b, "2026-07-10",
                    "On Jul 5 Beta stood still; it has since moved.")
    slot = {"matched_memory": ["Alpha", "Beta"]}
    tl = server._deep_timeline_html(con, slot, "2026-07-10", "s0")
    arc = server._deep_arc_line_html(con, slot, "2026-07-10")
    assert "Alpha prior fact." in tl and "Beta" not in tl        # timeline: Alpha
    assert "Beta stood still" in arc                             # arc: Beta


def test_whitespace_only_arc_line_renders_nothing(migrated_con):
    con = migrated_con
    tid = _seed_thread(con, "Strait")
    _seed_state_row(con, tid, "2026-07-10", "   \n\t ")
    assert server._deep_arc_line_html(
        con, {"matched_memory": ["Strait"]}, "2026-07-10") == ""


# ===========================================================================
# 6. MIGRATION 0018 — upgrade path on a TRIGGER-ARMED DB with existing rows
# ===========================================================================

def test_0018_applies_over_armed_append_only_triggers_and_keeps_them(tmp_path):
    """The additive claim, PROVEN on the upgrade shape the real DB will take:
    a DB migrated through 0017 (0010's RAISE(ABORT) trigger pair armed) with
    EXISTING thread_state rows takes 0018 cleanly — ALTER TABLE ADD COLUMN is
    schema, not a row UPDATE/DELETE, so the triggers never fire; existing rows
    read arc_line == '' (contract absence); and the triggers are STILL armed
    after (update/delete still abort). Complements test_migrations' fresh-DB
    0001→0018 and test_nl75's idempotency pins."""
    part = tmp_path / "mig-through-0017"
    part.mkdir()
    for f in sorted((PROTOTYPE_ROOT / "migrations").glob("*.sql")):
        if f.name < "0018":
            shutil.copy(f, part / f.name)
    db_path = tmp_path / "upgrade.db"
    applied = db.migrate(db_path=db_path, migrations_dir=part)
    assert applied[-1].startswith("0017")
    con = db.connect(db_path)
    try:
        now = "2026-07-01T00:00:00.000Z"
        tid = con.execute(
            "INSERT INTO memory (topic, status, status_changed_at, created_at,"
            " updated_at) VALUES ('T', 'active', ?, ?, ?)",
            (now, now, now)).lastrowid
        con.execute("INSERT INTO thread_state (thread_id, as_of_date,"
                    " state_text) VALUES (?, '2026-07-05', 's (Jul 5).')", (tid,))
        con.commit()
    finally:
        con.close()
    # the upgrade: 0018 (+ the later NL-17-M1b additive pair) pending against the
    # full shipped dir; all apply cleanly over 0010's armed append-only triggers.
    applied2 = db.migrate(db_path=db_path)
    assert applied2 == ["0018_thread_state_arc_line.sql",
                        "0019_memory_follow_altitude.sql",
                        "0020_follow_altitude_events.sql",
                        "0021_memory_follow_origin.sql"]
    con = db.connect(db_path)
    try:
        row = con.execute("SELECT arc_line FROM thread_state").fetchone()
        assert row["arc_line"] == ""            # pre-contract row reads absence
        with pytest.raises(Exception, match="append-only"):
            con.execute("UPDATE thread_state SET arc_line='x'")
        with pytest.raises(Exception, match="append-only"):
            con.execute("DELETE FROM thread_state")
        con.execute("INSERT INTO thread_state (thread_id, as_of_date,"
                    " state_text, arc_line) VALUES (1, '2026-07-06', 's2 "
                    "(Jul 6).', 'On Jul 5 x; y has since z.')")
        con.commit()                            # INSERT (append) still lawful
    finally:
        con.close()


# ===========================================================================
# 7. EXTRACTION + SHARED-HELPER PINS
# ===========================================================================

def test_fenced_preambled_result_preserves_the_arc_line_key():
    """NL-35 standing-gap note: the subscription lane emits prose ± fenced
    JSON; _extract_json_result must hand back an object whose NEW arc_line key
    survives to rewrite_state's raw.get('arc_line'). Mechanically key-agnostic
    — pinned here so the arc field's ride on that seam is a stated invariant,
    not an accident."""
    fenced = ("Here is the updated state you asked for:\n"
              "```json\n"
              "{\"state\": \"s (Jul 5).\", \"cites\": [\"2026-07-05\"],"
              " \"arc_line\": \"On Jul 5 x stood; y has since z.\"}\n"
              "```\nLet me know if you need anything else.")
    raw = json.loads(llm._extract_json_result(fenced))
    assert raw["arc_line"] == "On Jul 5 x stood; y has since z."


def test_ledger_integrity_BUG24_repinned_for_its_surviving_consumer():
    """_ledger_integrity RE-PIN: the reversion-law tests (TestReversionLaw,
    deleted with render_today_arc) were the only pins on this helper — it
    SURVIVES as diagnose.py's reversion-risk metric, so its contract stays
    pinned here: it examines EVERY entry handed to it (the BUG-24 fix shape —
    corruption is caught regardless of where a garbage date sorts), and each
    corruption class names its reason. NB (QA report, gate-routed): diagnose's
    surrounding prose still says a failing ledger 'would show a bare citation
    line' — that render died with this batch; the METRIC stays honest, the
    wording needs the sweep."""
    ok, why = memory_core._ledger_integrity(
        [{"edition_date": "2026-07-05", "what_happened": "A fact.",
          "cites_json": "[]"}])
    assert ok
    for bad, expect in (
        ({"edition_date": "garbage-date", "what_happened": "x",
          "cites_json": "[]"}, "non-calendar"),          # BUG-24: sorts after today
        ({"edition_date": "2026-07-05", "what_happened": "",
          "cites_json": "[]"}, "no 'what happened'"),
        ({"edition_date": "2026-07-05", "what_happened": "x",
          "cites_json": "not json"}, "unparseable"),
        ({"edition_date": "2026-07-05", "what_happened": "x",
          "cites_json": "{}"}, "unparseable"),           # parses, not a list
    ):
        ok, why = memory_core._ledger_integrity([
            {"edition_date": "2026-07-05", "what_happened": "Clean.",
             "cites_json": "[]"}, bad])
        assert not ok and expect in why


def test_salient_units_BUG22_edge_normalization_repinned():
    """BUG-22 RE-PIN: the arc-render deletion took the only tests exercising
    _salient_units' edge normalization (possessive suffix, sentence-final
    period fused to a number) — the helper SURVIVES with live consumers
    (delta-hijack detection, observable-subject extraction), so its fixed
    behavior must stay pinned or a refactor regresses it silently."""
    units = memory_core._salient_units(
        "Khamenei's funeral procession ran through Tehran.")
    assert "khamenei" in units and "khamenei's" not in units
    units2 = memory_core._salient_units("The confirmed toll rose to 12.")
    assert "12" in units2 and "12." not in units2
