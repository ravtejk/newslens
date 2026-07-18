"""THE ARC-LINE CONTRACT v1 — implementer liveness/enforcement pins.

The deep-view continuity line becomes a SEPARATELY-AUTHORED field of the state
rewrite (workspace/debates/2026-07-18--newslens--content.md), killing the
tense-splice defect by construction (state-summary text reused as arc prose —
principal's 2026-07-17 served review item 2).

Each test in Sections 1–7 is born red against v8-M2 (HEAD 1472008): the
validator (memory_core.validate_arc_line), the authored field
(thread_state.arc_line + memory_core._author_arc_line), and the render swap
(server._deep_arc_line_html) do not exist there — the tests fail on
AttributeError / 'no such column' / absent render. They pass only with the
landed batch. Section 8 (2026-07-18 observability mini) carries its own
provenance: born red vs c1d5322, except the one labeled carried-invariant.
Offline, deterministic, $0 (the state seam is injected; no paid calls).
"""

import json

import pytest

from newslens import db, generate, memory_core, paths, server


# --- fixtures ---------------------------------------------------------------

def _seed_thread(con, topic):
    now = "2026-07-01T00:00:00.000Z"
    cur = con.execute(
        "INSERT INTO memory (topic, status, status_changed_at, created_at,"
        " updated_at) VALUES (?, 'active', ?, ?, ?)", (topic, now, now, now))
    return cur.lastrowid


def _write_delta(con, tid, date, verdict="advances",
                 what="A dated development.", signif="Changed the frame.",
                 cites=("S1",)):
    con.execute(
        "INSERT INTO thread_deltas (thread_id, edition_date, verdict,"
        " what_happened, significance, cites_json) VALUES (?, ?, ?, ?, ?, ?)",
        (tid, date, verdict, what, signif, json.dumps(list(cites))))
    con.commit()


_TEMPLATE = "topic={topic} date={date}\n{ledger}"   # stub; the injected chat ignores it

# A contract-compliant line for anchor Jul 5, state describing 'now' (Jul 10).
_GOOD = ("When this record last covered the strait (Jul 5), transit fees were "
         "the dispute; a shooting war has since broken out.")
_STATE_NOW = "The strait is closed and shipping has rerouted (Jul 10)."


def _state_chat(arc_line, state=_STATE_NOW):
    """Main-call chat returning a state + arc_line; a retry (prompt carries
    'CORRECTION') returns `arc_line` under the arc_line key only."""
    def chat(key, prompt):
        if "CORRECTION" in prompt:
            return ({"arc_line": arc_line}, 0.0005)
        return ({"state": state, "arc_line": arc_line}, 0.001)
    return chat


def _retry_chat(bad, good, state=_STATE_NOW):
    def chat(key, prompt):
        if "CORRECTION" in prompt:
            return ({"arc_line": good}, 0.0005)
        return ({"state": state, "arc_line": bad}, 0.001)
    return chat


# ===========================================================================
# 1. THE VALIDATOR — mechanical anatomy only (Clash-1: structure, not phrasing)
# ===========================================================================

def test_validator_accepts_a_contract_compliant_line():
    clean, warns = memory_core.validate_arc_line(_GOOD, _STATE_NOW, "2026-07-05")
    assert clean == _GOOD


def test_validator_rejects_missing_anchor_date():
    with pytest.raises(memory_core.ArcLineRejected) as e:
        memory_core.validate_arc_line(
            "Transit fees were the dispute; a shooting war has since broken out.",
            _STATE_NOW, "2026-07-05")
    assert "anchor" in str(e.value).lower()


def test_validator_rejects_a_wrong_anchor_date():
    """§C.1: the anchor is the ledger's LAST-COVERED date, not any date — a line
    naming Jul 4 (the previous calendar day) when the anchor is Jul 5 fails."""
    with pytest.raises(memory_core.ArcLineRejected):
        memory_core.validate_arc_line(
            "When this record last covered the strait (Jul 4), fees were the "
            "dispute; a war has since broken out.", _STATE_NOW, "2026-07-05")


def test_validator_accepts_iso_and_full_month_anchor_forms():
    for form in ("2026-07-05", "July 5"):
        line = (f"As of {form} the strait faced only a fees dispute; a shooting "
                "war has since broken out overnight.")
        clean, _ = memory_core.validate_arc_line(line, _STATE_NOW, "2026-07-05")
        assert clean == line


def test_validator_rejects_over_length():
    long = ("When this record last covered the strait (Jul 5), transit fees were "
            "merely the dispute at hand, but a genuine shooting war has now since "
            "broken out across the whole contested waterway and beyond it too.")
    assert len(long.split()) > memory_core.ARC_MAX_WORDS
    with pytest.raises(memory_core.ArcLineRejected) as e:
        memory_core.validate_arc_line(long, _STATE_NOW, "2026-07-05")
    assert "word" in str(e.value).lower()


def test_validator_rejects_two_sentences():
    with pytest.raises(memory_core.ArcLineRejected) as e:
        memory_core.validate_arc_line(
            "On Jul 5 fees were the dispute. A war has since broken out.",
            _STATE_NOW, "2026-07-05")
    assert "sentence" in str(e.value).lower()


def test_validator_rejects_newsroom_we():
    with pytest.raises(memory_core.ArcLineRejected) as e:
        memory_core.validate_arc_line(
            "When we last covered the strait (Jul 5), fees were the dispute; a "
            "war has since broken out.", _STATE_NOW, "2026-07-05")
    assert "we" in str(e.value).lower()


def test_validator_rejects_forward_promise():
    with pytest.raises(memory_core.ArcLineRejected):
        memory_core.validate_arc_line(
            "Since Jul 5 fees became a war; watch for a full closure of the "
            "strait next.", _STATE_NOW, "2026-07-05")


def test_validator_rejects_ordinal_entry_count():
    with pytest.raises(memory_core.ArcLineRejected):
        memory_core.validate_arc_line(
            "The third entry on this thread since Jul 5 records that fees became "
            "a shooting war.", _STATE_NOW, "2026-07-05")


def test_validator_allows_named_record_entry_anchor():
    """§F.4: 'the Jul 5 entry' is LAWFUL anchor diction (no ordinal, not 'entry
    on this thread') — the mirror ban must not fire on it."""
    line = ("The Jul 5 entry had fees as the dispute; a shooting war has since "
            "broken out across the strait.")
    clean, _ = memory_core.validate_arc_line(line, _STATE_NOW, "2026-07-05")
    assert clean == line


def test_validator_rejects_state_overlap_contiguous_run():
    state = ("The strait is closed and shipping insurance has restructured "
             "around the loss (Jul 10).")
    line = ("As of Jul 5 fees were the dispute; the strait is closed and "
            "shipping insurance has restructured around the loss.")   # ≥6-word paste
    with pytest.raises(memory_core.ArcLineRejected) as e:
        memory_core.validate_arc_line(line, state, "2026-07-05")
    assert "reuse" in str(e.value).lower() or "state" in str(e.value).lower()


def test_the_served_defect_specimen_is_rejected():
    """The exact principal-flagged specimen (2026-07-17 review item 2): a past
    anchor governing a NOW-state clause whose payload IS the reused state text.
    Caught mechanically twice over — the 'we' ban AND the overlap tripwire."""
    state = ("The conflict has moved beyond economic disputes into open "
             "competition for the strait's traffic (Jul 16).")
    specimen = ("When we last covered this (Jul 16), the conflict has moved "
                "beyond economic disputes into open competition.")
    with pytest.raises(memory_core.ArcLineRejected):
        memory_core.validate_arc_line(specimen, state, "2026-07-16")


def test_overlap_helper_bounds_a_valid_reframe_below_the_threshold():
    """The reframe class (delta endpoint == the new state) must PASS: its
    now-as-endpoint legitimately shares state tokens but stays under the
    directed >40% bar — the calibration that lets §F.1 catch pastes without
    false-rejecting valid reframes (the strip-test-boundary rationale)."""
    state = ("Shipping and insurance are restructuring around a strait treated "
             "as closed (Jul 16).")
    reframe = ("When this record last covered Hormuz (Jul 16), the story was the "
               "strikes themselves; since then it has become the markets.")
    assert not memory_core.arc_overlap_trips(reframe, state)
    clean, _ = memory_core.validate_arc_line(reframe, state, "2026-07-16")
    assert clean == reframe


# ===========================================================================
# 2. AUTHORING — rewrite_state authors + stores the field; §B; retry; absence
# ===========================================================================

def test_rewrite_state_authors_and_stores_the_arc_line(migrated_con):
    con = migrated_con
    tid = _seed_thread(con, "Strait")
    _write_delta(con, tid, "2026-07-05", what="Transit fees imposed.",
                 signif="A pricing dispute.")
    _write_delta(con, tid, "2026-07-10", what="Strikes exchanged.", signif="A war.")
    res = memory_core.rewrite_state(
        con, tid, "Strait", "2026-07-10", None, "k", _TEMPLATE, 1.0,
        chat=_state_chat(_GOOD))
    assert res.outcome == "written"
    row = memory_core.state_for_edition(con, tid, "2026-07-10")
    assert row is not None and row["arc_line"] == _GOOD


def test_day_one_thread_authors_no_arc_line(migrated_con):
    """§B: a thread's FIRST entry has no prior edition-cited coverage — no arc
    line, ever (absence, never filler), even if the model returns one."""
    con = migrated_con
    tid = _seed_thread(con, "Fresh")
    _write_delta(con, tid, "2026-07-10", what="First development.", signif="New.")
    res = memory_core.rewrite_state(
        con, tid, "Fresh", "2026-07-10", None, "k", _TEMPLATE, 1.0,
        chat=_state_chat("When this record last covered Fresh (Jul 5), x; y since."))
    assert res.outcome == "written"
    row = memory_core.state_for_edition(con, tid, "2026-07-10")
    assert row["arc_line"] == ""


def test_rejected_arc_line_recovers_on_one_corrected_retry(migrated_con):
    con = migrated_con
    tid = _seed_thread(con, "Strait")
    _write_delta(con, tid, "2026-07-05", what="Fees imposed.", signif="Dispute.")
    _write_delta(con, tid, "2026-07-10", what="Strikes.", signif="War.")
    bad = "When we last covered this (Jul 5), fees were the dispute; war since."  # 'we'
    res = memory_core.rewrite_state(
        con, tid, "Strait", "2026-07-10", None, "k", _TEMPLATE, 1.0,
        chat=_retry_chat(bad, _GOOD))
    row = memory_core.state_for_edition(con, tid, "2026-07-10")
    assert row["arc_line"] == _GOOD                 # the corrected retry landed
    assert res.cost_usd > 0.001                     # the retry billed (money-honesty)


def test_unrecoverable_arc_line_degrades_to_absence_never_blocks_state(migrated_con):
    con = migrated_con
    tid = _seed_thread(con, "Strait")
    _write_delta(con, tid, "2026-07-05", what="Fees imposed.", signif="Dispute.")
    _write_delta(con, tid, "2026-07-10", what="Strikes.", signif="War.")
    bad = "When we covered this yesterday, we saw fees; we expect a war."  # 'we' both times
    res = memory_core.rewrite_state(
        con, tid, "Strait", "2026-07-10", None, "k", _TEMPLATE, 1.0,
        chat=_retry_chat(bad, bad))
    assert res.outcome == "written"                 # the STATE row still lands
    row = memory_core.state_for_edition(con, tid, "2026-07-10")
    assert row["arc_line"] == ""                    # absence, not a shipped bad line
    assert "arc line rejected" in res.detail        # disclosed


def test_retry_transport_failure_carries_billed_cost_and_degrades(migrated_con):
    """BUG-32 money-honesty parity: a corrected retry that BILLS then fails
    (transport) must not lose the spend, and still degrades to absence — never
    blocks the state row."""
    con = migrated_con
    tid = _seed_thread(con, "Strait")
    _write_delta(con, tid, "2026-07-05", what="Fees imposed.", signif="Dispute.")
    _write_delta(con, tid, "2026-07-10", what="Strikes.", signif="War.")
    bad = "When we last covered this (Jul 5), fees were the dispute; war since."

    def chat(key, prompt):
        if "CORRECTION" in prompt:
            exc = RuntimeError("state seat 503")
            exc.usd_spent = 0.002
            exc.usd_shadow = 0.002
            raise exc
        return ({"state": _STATE_NOW, "arc_line": bad}, 0.001)

    res = memory_core.rewrite_state(
        con, tid, "Strait", "2026-07-10", None, "k", _TEMPLATE, 1.0, chat=chat)
    assert res.outcome == "written"
    row = memory_core.state_for_edition(con, tid, "2026-07-10")
    assert row["arc_line"] == ""                       # absence, state still landed
    assert res.cost_usd == pytest.approx(0.003)        # main 0.001 + billed retry 0.002


def test_absent_arc_line_field_is_lawful_absence_with_warn(migrated_con):
    """Gate FIX-2 (2026-07-18): a missing arc_line on an arc-ELIGIBLE thread is
    still lawful absence (§B, no retry — the paid retry stays reserved for the
    garbage case) but is now OBSERVABLE: the warn lands in res.detail so a
    quiet contract miss never passes unseen. The warn is not a rejection."""
    con = migrated_con
    tid = _seed_thread(con, "Strait")
    _write_delta(con, tid, "2026-07-05", what="Fees imposed.", signif="Dispute.")
    _write_delta(con, tid, "2026-07-10", what="Strikes.", signif="War.")
    res = memory_core.rewrite_state(         # chat returns state only, no arc_line
        con, tid, "Strait", "2026-07-10", None, "k", _TEMPLATE, 1.0,
        chat=lambda k, p: ({"state": _STATE_NOW}, 0.001))
    row = memory_core.state_for_edition(con, tid, "2026-07-10")
    assert row["arc_line"] == "" and "rejected" not in res.detail
    assert "authored no arc_line" in res.detail          # the FIX-2 warn, surfaced
    assert res.cost_usd == pytest.approx(0.001)          # and no retry was paid


# ===========================================================================
# 3. THE RENDER SWAP — deep view renders the stored field VERBATIM (dumb)
# ===========================================================================

def _slot(topic):
    return {"slot": "1", "matched_memory": [topic]}


def _seed_state_row(con, tid, date, arc_line, state_text=_STATE_NOW):
    con.execute(
        "INSERT INTO thread_state (thread_id, as_of_date, state_text, arc_line)"
        " VALUES (?, ?, ?, ?)", (tid, date, state_text, arc_line))
    con.commit()


def test_deep_view_renders_the_stored_arc_line_verbatim(migrated_con):
    con = migrated_con
    tid = _seed_thread(con, "Strait")
    _seed_state_row(con, tid, "2026-07-10", _GOOD)
    html = server._deep_arc_line_html(con, _slot("Strait"), "2026-07-10")
    assert 'class="deep-arc-line"' in html and _GOOD in html


def test_deep_view_arc_absence_renders_nothing(migrated_con):
    con = migrated_con
    tid = _seed_thread(con, "Strait")
    _seed_state_row(con, tid, "2026-07-10", "")          # authored absence
    assert server._deep_arc_line_html(con, _slot("Strait"), "2026-07-10") == ""
    # no state row for this edition at all -> also nothing (no placeholder)
    assert server._deep_arc_line_html(con, _slot("Strait"), "2026-07-11") == ""
    assert server._deep_arc_line_html(None, _slot("Strait"), "2026-07-10") == ""


def test_deep_view_arc_escapes_model_text(migrated_con):
    """The stored line is model output rendered into HTML — a script tag renders
    inert (preserves the XSS invariant from the deleted _today_arc_html test)."""
    con = migrated_con
    tid = _seed_thread(con, "Strait")
    _seed_state_row(con, tid, "2026-07-10",
                    "As of Jul 5 <script>alert(1)</script> fees; war since.")
    html = server._deep_arc_line_html(con, _slot("Strait"), "2026-07-10")
    assert "<script>" not in html and "&lt;script&gt;" in html


def test_deep_view_arc_is_versioned_by_edition(migrated_con):
    """A historical deep view shows THAT edition's authored line, never the
    newest — state_for_edition keys on as_of_date == the edition rendered."""
    con = migrated_con
    tid = _seed_thread(con, "Strait")
    _seed_state_row(con, tid, "2026-07-10", "As of Jul 5 fees; a war since broke out.")
    _seed_state_row(con, tid, "2026-07-12", "As of Jul 10 war; a ceasefire since held.")
    old = server._deep_arc_line_html(con, _slot("Strait"), "2026-07-10")
    assert "Jul 5" in old and "ceasefire" not in old


def test_render_deep_view_swaps_arc_into_title_block(migrated_con):
    """Integration: the full deep view carries the stored arc line in the title
    block (the render swap is wired), and no longer derives it from brief['arc']."""
    con = migrated_con
    tid = _seed_thread(con, "Strait")
    _seed_state_row(con, tid, "2026-07-10", _GOOD)
    doc = {"header": {}, "brief": {"arc": {"delta": "advances",
                                           "significance": "SHOULD NOT RENDER",
                                           "cites": []}, "sources": []}}
    html = server._render_deep_view("story-0", "H", doc, "2026-07-10",
                                    con=con, slot=_slot("Strait"))
    tb = html.split("deep-title-block")[1].split("</div>")[0]
    assert _GOOD in tb                              # stored field, verbatim
    assert "SHOULD NOT RENDER" not in html          # brief['arc'] no longer derived


# ===========================================================================
# 8. OBSERVABILITY MINI (2026-07-18) — the rejected candidate TEXT rides the
#    warn into generation_log's state_rewrites detail, so a §F.1 double
#    rejection is DIAGNOSABLE (was the model pasting state text, or writing a
#    decent line that trips on shared proper nouns?). Log-only: absence (§B),
#    retry, money, and render semantics are all unchanged. Born red vs HEAD
#    c1d5322 EXCEPT the one explicitly-labeled carried-invariant (R4 org law).
# ===========================================================================

# Two DISTINCT lines that each trip the validator (newsroom 'we', §F.4), so the
# first attempt AND the corrected retry are both rejected — the exact live
# edition-8 shape (both attempts rejected), forced offline and deterministic.
_BAD_A1 = ("When we last covered the strait (Jul 5), transit fees were the "
           "dispute; a shooting war has since broken out.")
_BAD_A2 = ("As we reported on Jul 5, the fees still ruled; the strait has "
           "since closed to all traffic.")


def test_first_attempt_rejected_candidate_text_reaches_the_detail(migrated_con):
    """CONTRACT (a), BORN RED: on a double rejection the FIRST attempt's
    candidate is carried VERBATIM and attempt-labeled in res.detail. At HEAD
    the detail holds only the reason strings (the matched banned token, never
    the whole line), so the full-line assertion fails there."""
    con = migrated_con
    tid = _seed_thread(con, "Strait")
    _write_delta(con, tid, "2026-07-05", what="Fees imposed.", signif="Dispute.")
    _write_delta(con, tid, "2026-07-10", what="Strikes.", signif="War.")
    res = memory_core.rewrite_state(
        con, tid, "Strait", "2026-07-10", None, "k", _TEMPLATE, 1.0,
        chat=_retry_chat(_BAD_A1, _BAD_A2))
    assert res.outcome == "written"                 # the state row still lands
    assert memory_core.state_for_edition(con, tid, "2026-07-10")["arc_line"] == ""
    assert "attempt-1 candidate:" in res.detail
    assert _BAD_A1 in res.detail                    # VERBATIM, the whole line
    assert "arc line rejected" in res.detail        # reason string still present


def test_retry_rejected_candidate_text_is_attempt_labeled(migrated_con):
    """CONTRACT (b), BORN RED: the corrected RETRY's candidate is carried
    VERBATIM under a distinct 'retry candidate:' label, ordered after
    attempt-1 — the diagnosis 'did the retry paste too, or trip elsewhere?'
    needs both lines side by side."""
    con = migrated_con
    tid = _seed_thread(con, "Strait")
    _write_delta(con, tid, "2026-07-05", what="Fees imposed.", signif="Dispute.")
    _write_delta(con, tid, "2026-07-10", what="Strikes.", signif="War.")
    res = memory_core.rewrite_state(
        con, tid, "Strait", "2026-07-10", None, "k", _TEMPLATE, 1.0,
        chat=_retry_chat(_BAD_A1, _BAD_A2))
    assert "retry candidate:" in res.detail
    assert _BAD_A2 in res.detail                    # the retry line, verbatim
    assert (res.detail.index("attempt-1 candidate:")
            < res.detail.index("retry candidate:"))     # attempt-labeled, ordered


def test_no_candidate_path_detail_is_unchanged_CARRIED_INVARIANT(migrated_con):
    """CONTRACT (c), CARRIED-INVARIANT (born GREEN — labeled per the R4 org
    law): the model-authored-no-arc_line path (§B absence + gate FIX-2 warn) is
    NOT a rejection and must stay byte-for-byte as it was — no candidate labels,
    no retry. This passes at HEAD too; it pins that the observability change
    left the absence path untouched."""
    con = migrated_con
    tid = _seed_thread(con, "Strait")
    _write_delta(con, tid, "2026-07-05", what="Fees imposed.", signif="Dispute.")
    _write_delta(con, tid, "2026-07-10", what="Strikes.", signif="War.")
    res = memory_core.rewrite_state(         # chat returns state only, no arc_line
        con, tid, "Strait", "2026-07-10", None, "k", _TEMPLATE, 1.0,
        chat=lambda k, p: ({"state": _STATE_NOW}, 0.001))
    assert res.detail.endswith(
        "model authored no arc_line for an arc-eligible thread — arc omitted "
        "this edition (absence, §B)")
    assert "candidate:" not in res.detail           # no attempt-1/retry labels leak
    assert "rejected" not in res.detail             # not a rejection


def test_rejected_candidate_is_truncated_defensively_in_the_warn(migrated_con):
    """CONTRACT (3), BORN RED: a runaway candidate (far over the ≤35-word law)
    is truncated at ~400 chars with an honest marker so it cannot bloat the
    append-only generation log — the verbatim PREFIX is kept, the tail is not."""
    con = migrated_con
    tid = _seed_thread(con, "Strait")
    _write_delta(con, tid, "2026-07-05", what="Fees imposed.", signif="Dispute.")
    _write_delta(con, tid, "2026-07-10", what="Strikes.", signif="War.")
    runaway = ("When we last covered the strait (Jul 5), "
               + "fees ruled the dispute and shipping rerouted again " * 20)
    assert len(runaway) > 400
    res = memory_core.rewrite_state(
        con, tid, "Strait", "2026-07-10", None, "k", _TEMPLATE, 1.0,
        chat=_retry_chat(runaway, runaway))
    assert "…[truncated," in res.detail             # honest truncation marker
    assert runaway not in res.detail                # the full runaway is NOT logged
    assert runaway[:120] in res.detail              # the verbatim prefix IS


def test_rejected_candidate_text_reaches_the_generation_log_record(tmp_paths):
    """WIRING PROOF (BORN RED): the composed candidate text rides res.detail ->
    run_memory_pass state_results -> report.memory['state_rewrites'] ->
    log_generation's 'memory' block, landing in the persisted (sandboxed)
    generation_log.jsonl. A red test only the candidate-logging wiring flips —
    at HEAD the record carries reason strings alone. Offline, $0 (state seam
    injected); sacred state untouched (NEWSLENS_DATA_DIR sandbox)."""
    db.migrate()
    con = db.connect()
    now = "2026-07-01T00:00:00.000Z"
    con.execute("INSERT INTO memory (topic, status, status_changed_at,"
                " created_at, updated_at) VALUES ('Strait','active',?,?,?)",
                (now, now, now))
    tid = con.execute("SELECT id FROM memory").fetchone()["id"]
    # A PRIOR edition-cited delta makes today's move arc-ELIGIBLE (an anchor
    # exists), so the arc author actually runs and can reject:
    _write_delta(con, tid, "2026-07-05", what="Fees imposed.", signif="Dispute.")
    slots = [{"slot": "1", "matched_memory": ["Strait"]}]
    con.execute("INSERT INTO briefings (date, story_slots) VALUES ('2026-07-10', ?)",
                (json.dumps(slots),))
    con.commit()
    report = generate.GenReport(date="2026-07-10", variant="A")
    brief = {"brief": {"arc": {"delta": "advances",
                               "what_happened": "Strikes broke out overnight.",
                               "significance": "A shooting war began.",
                               "cites": ["S9"]}}}

    def state_chat(key, prompt):
        if "CORRECTION" in prompt:
            return ({"arc_line": _BAD_A2}, 0.0005)
        return ({"state": _STATE_NOW, "arc_line": _BAD_A1}, 0.001)

    generate.run_memory_pass(
        con, "2026-07-10", "k", cap=1.0, spent=0.0,
        briefs_by_slot={1: brief}, slots=slots, report=report,
        state_chat=state_chat)

    detail = report.memory["state_rewrites"][0]["detail"]
    assert _BAD_A1 in detail and "attempt-1 candidate:" in detail
    assert _BAD_A2 in detail and "retry candidate:" in detail

    # ...and it survives serialization into the ACTUAL sandboxed log file:
    generate.log_generation({"date": "2026-07-10", "memory": report.memory})
    log_text = (paths.DATA_DIR / generate.GENERATION_LOG_NAME).read_text(
        encoding="utf-8")
    assert _BAD_A1 in log_text and _BAD_A2 in log_text
    con.close()
