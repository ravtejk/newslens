"""NL-63 M1 — the memory core (implementer-written; QA extends adversarially).

The moat build: the delta ledger (Pax's economy), the standing state (Content's
write law + anti-photocopier), the arc render (Sten's kill-test AS CODE +
Kass's reversion law AS CODE), the timeline, thread-scoped P, provenance
honesty. Offline, deterministic; the state-rewrite LLM seam is injected.

These pin the mechanics and the WIRING (per team/ENGINEERING.md: a wiring claim
travels with the red test only it can flip).
"""

import json
import sqlite3

import pytest

from newslens import analysis, db, generate, memory_core, server


# --- fixtures ---------------------------------------------------------------

def _seed_thread(con, topic, tid=None):
    now = "2026-07-01T00:00:00.000Z"
    cur = con.execute(
        "INSERT INTO memory (topic, status, status_changed_at, created_at,"
        " updated_at) VALUES (?, 'active', ?, ?, ?)", (topic, now, now, now))
    return cur.lastrowid


def _seed_briefing(con, date):
    cur = con.execute("INSERT INTO briefings (date, story_slots) VALUES (?, '[]')",
                      (date,))
    con.commit()
    return cur.lastrowid


def _brief_with_arc(delta="advances", what_happened="X happened today.",
                    significance="It changed the story.", cites=("S1",)):
    return {"brief": {"arc": {"delta": delta, "what_happened": what_happened,
                              "significance": significance,
                              "cites": list(cites)}}}


def _write_delta(con, tid, date, verdict="advances",
                 what="A dated development.", signif="Changed the frame.",
                 cites=("S1",)):
    con.execute(
        "INSERT INTO thread_deltas (thread_id, edition_date, verdict,"
        " what_happened, significance, cites_json) VALUES (?, ?, ?, ?, ?, ?)",
        (tid, date, verdict, what, signif, json.dumps(list(cites))))
    con.commit()


# --- schema / append-only ---------------------------------------------------

def test_thread_tables_exist_and_are_append_only(migrated_con):
    con = migrated_con
    tid = _seed_thread(con, "T")
    _write_delta(con, tid, "2026-07-05")
    con.execute(
        "INSERT INTO thread_state (thread_id, as_of_date, state_text)"
        " VALUES (?, '2026-07-05', 'state (Jul 5).')", (tid,))
    con.commit()
    with pytest.raises(sqlite3.DatabaseError) as e1:
        con.execute("UPDATE thread_deltas SET verdict='reverses' WHERE thread_id=?", (tid,))
    assert "append-only" in str(e1.value)
    with pytest.raises(sqlite3.DatabaseError):
        con.execute("DELETE FROM thread_deltas WHERE thread_id=?", (tid,))
    with pytest.raises(sqlite3.DatabaseError) as e2:
        con.execute("UPDATE thread_state SET state_text='x' WHERE thread_id=?", (tid,))
    assert "append-only" in str(e2.value)


# --- 1. the delta ledger (Pax's economy) ------------------------------------

def test_advance_arc_writes_a_two_clause_delta(migrated_con):
    con = migrated_con
    tid = _seed_thread(con, "Iran War")
    bid = _seed_briefing(con, "2026-07-10")
    slots = [{"slot": "1", "matched_memory": ["Iran War"]}]
    rep = memory_core.write_deltas_for_edition(
        con, "2026-07-10", bid, {1: _brief_with_arc()}, slots)
    assert len(rep.written) == 1 and rep.written[0]["thread"] == "Iran War"
    row = con.execute("SELECT * FROM thread_deltas WHERE thread_id=?", (tid,)).fetchone()
    assert row["verdict"] == "advances"
    assert row["what_happened"] == "X happened today."
    assert row["significance"] == "It changed the story."
    assert tid in rep.moved_thread_ids


def test_delta_row_stores_its_writing_slot(migrated_con):
    """M1 gate F2: the slot column records which slot wrote the delta —
    nullable (seeds carry NULL), no uniqueness (a real same-day second
    development must never be refused as amnesia; M2 carries the
    regen-dedup contract)."""
    con = migrated_con
    _seed_thread(con, "T")
    bid = _seed_briefing(con, "2026-07-10")
    memory_core.write_deltas_for_edition(
        con, "2026-07-10", bid, {2: _brief_with_arc()},
        [{"slot": "2", "matched_memory": ["T"]}])
    row = con.execute("SELECT slot FROM thread_deltas").fetchone()
    assert row["slot"] == 2


def test_merely_matches_writes_nothing(migrated_con):
    con = migrated_con
    _seed_thread(con, "T")
    rep = memory_core.write_deltas_for_edition(
        con, "2026-07-10", 1,
        {1: _brief_with_arc(delta="merely-matches")},
        [{"slot": "1", "matched_memory": ["T"]}])
    assert rep.written == []
    assert con.execute("SELECT COUNT(*) c FROM thread_deltas").fetchone()["c"] == 0


def test_p_only_arc_is_refused_self_reference(migrated_con):
    con = migrated_con
    _seed_thread(con, "T")
    rep = memory_core.write_deltas_for_edition(
        con, "2026-07-10", 1,
        {1: _brief_with_arc(cites=("P1",))},
        [{"slot": "1", "matched_memory": ["T"]}])
    assert rep.written == []
    assert any("self-reference" in s for s in rep.skipped)


def test_delta_write_is_idempotent_across_regeneration(migrated_con):
    con = migrated_con
    _seed_thread(con, "T")
    bid = _seed_briefing(con, "2026-07-10")
    slots = [{"slot": "1", "matched_memory": ["T"]}]
    memory_core.write_deltas_for_edition(con, "2026-07-10", bid, {1: _brief_with_arc()}, slots)
    memory_core.write_deltas_for_edition(con, "2026-07-10", bid, {1: _brief_with_arc()}, slots)
    assert con.execute("SELECT COUNT(*) c FROM thread_deltas").fetchone()["c"] == 1


def test_one_development_moves_every_matched_thread(migrated_con):
    con = migrated_con
    _seed_thread(con, "Iran War")
    _seed_thread(con, "Strait of Hormuz")
    bid = _seed_briefing(con, "2026-07-10")
    rep = memory_core.write_deltas_for_edition(
        con, "2026-07-10", bid, {1: _brief_with_arc()},
        [{"slot": "1", "matched_memory": ["Iran War", "Strait of Hormuz"]}])
    assert len(rep.written) == 2


# --- 3. no-backfill ---------------------------------------------------------

def test_fresh_ledger_is_empty_no_backfill(migrated_con):
    con = migrated_con
    tid = _seed_thread(con, "T")
    assert memory_core.ledger_for_thread(con, tid) == []


# --- 2. the standing state: write law + anti-photocopier + stale-honest -----

def test_state_hard_rejects_a_cite_to_a_non_edition_date(migrated_con):
    con = migrated_con
    with pytest.raises(memory_core.StateRejected) as e:
        memory_core.validate_state(
            "The war escalated (Jul 99-shaped: Aug 30).",
            ledger_dates={"2026-07-10"})
    assert "fabrication" in str(e.value)


def test_state_accepts_resolvable_edition_cites(migrated_con):
    clean, warns = memory_core.validate_state(
        "The strait is closed (Jul 10). Fees preceded it (Jul 5).",
        ledger_dates={"2026-07-05", "2026-07-10"})
    assert "closed" in clean


def test_in_prose_content_date_is_not_a_required_cite(migrated_con):
    # 'July 12' (a scheduled talks date) is content, not an edition citation.
    clean, warns = memory_core.validate_state(
        "Talks are set for July 12 (Jul 10).",
        ledger_dates={"2026-07-10"})
    assert "July 12" in clean


def test_rewrite_state_written_then_stale_but_honest(migrated_con):
    con = migrated_con
    tid = _seed_thread(con, "T")
    _write_delta(con, tid, "2026-07-10", what="Strikes.", signif="War.")
    con.execute("INSERT INTO briefings (date, story_slots) VALUES ('2026-07-10','[]')")
    con.commit()
    tmpl = "topic {topic} date {date}\n{ledger}"

    def good_chat(key, prompt):
        return ({"state": "It is a war now (Jul 10)."}, 0.001)
    r = memory_core.rewrite_state(con, tid, "T", "2026-07-10", 1, "k", tmpl, 0.25, chat=good_chat)
    assert r.outcome == "written"
    assert memory_core.latest_state(con, tid)["state_text"] == "It is a war now (Jul 10)."

    def boom_chat(key, prompt):
        raise RuntimeError("network down")
    r2 = memory_core.rewrite_state(con, tid, "T", "2026-07-10", 1, "k", tmpl, 0.25, chat=boom_chat)
    assert r2.outcome == "stale"
    # prior state kept, NO new row written (stale-but-honest)
    assert con.execute("SELECT COUNT(*) c FROM thread_state").fetchone()["c"] == 1


def test_state_regenerates_from_ledger_not_prior_state(migrated_con):
    """Anti-photocopier: the prompt the model sees carries the LEDGER, never
    the prior state text (a state written from a state is the photocopier)."""
    con = migrated_con
    tid = _seed_thread(con, "T")
    _write_delta(con, tid, "2026-07-10", what="A ledger fact.", signif="A frame.")
    entries = memory_core.ledger_for_thread(con, tid)
    prompt = memory_core.render_state_prompt("T", "2026-07-10", entries,
                                             "{topic}|{date}|{ledger}")
    assert "A ledger fact." in prompt and "A frame." in prompt


# --- 5. the timeline (never-re-lede is the caller's job) ---------------------

def test_timeline_before_date_is_exclusive(migrated_con):
    con = migrated_con
    tid = _seed_thread(con, "T")
    _write_delta(con, tid, "2026-07-05")
    _write_delta(con, tid, "2026-07-10")
    before = memory_core.ledger_for_thread(con, tid, before_date="2026-07-10")
    assert [e["edition_date"] for e in before] == ["2026-07-05"]


# --- 3. thread-scoped P + provenance honesty --------------------------------

def test_prior_for_slot_uses_thread_record_when_present(migrated_con):
    con = migrated_con
    tid = _seed_thread(con, "Iran War")
    _write_delta(con, tid, "2026-07-05", what="Transit fees.", signif="Pricing.")
    slot = {"matched_memory": ["Iran War"]}
    p = memory_core.prior_for_slot(con, "2026-07-10", slot, generic_prior=[{"date": "x", "text": "GENERIC"}])
    assert len(p) == 1 and "PER OUR PRIOR COVERAGE" in p[0]["text"]
    assert "GENERIC" not in p[0]["text"]


def test_prior_for_slot_falls_back_to_generic_when_no_record(migrated_con):
    con = migrated_con
    _seed_thread(con, "Iran War")   # no ledger yet
    slot = {"matched_memory": ["Iran War"]}
    generic = [{"date": "x", "text": "GENERIC"}]
    assert memory_core.prior_for_slot(con, "2026-07-10", slot, generic) == generic


def test_p_only_provenance_is_prior_coverage_not_background():
    src = {"P1": {"kind": "prior-briefing", "outlet": "NewsLens (prior edition)"}}
    assert analysis.compute_provenance(["P1"], src) == "prior-coverage"
    assert analysis.compute_provenance([], src) == "stable-background"


# --- server renders (item 4/5/6 wiring) -------------------------------------

def test_deep_timeline_html_renders_from_the_ledger(migrated_con):
    con = migrated_con
    tid = _seed_thread(con, "Strait of Hormuz")
    con.execute("INSERT INTO briefings (date, story_slots) VALUES ('2026-07-05','[]')")
    _write_delta(con, tid, "2026-07-05", what="Transit fees imposed.", signif="Pricing.")
    con.commit()
    slot = {"matched_memory": ["Strait of Hormuz"]}
    html = server._deep_timeline_html(con, slot, "2026-07-10", "story-0")
    assert "The story so far" in html and "Transit fees imposed." in html


def test_dossier_state_card_renders_state_and_last_delta():
    t = {"topic": "T", "state_text": "It is a war now (Jul 10).",
         "state_as_of": "2026-07-10",
         "last_delta": {"date": "2026-07-10", "what_happened": "Strikes.",
                        "significance": "War."}}
    html = server._thread_state_card(t)
    assert 'class="dossier-state"' in html and "It is a war now" in html
    assert 'class="dossier-delta"' in html and "Strikes." in html


# --- WIRING liveness: generate.py drives the memory pass --------------------

def test_run_memory_pass_wires_ledger_and_state(tmp_paths):
    """The wiring proof: generate.run_memory_pass — the exact glue _run_generate_
    body calls on a refresh — writes the delta AND rewrites the state, offline,
    with the state model injected. A red test only the wiring can flip."""
    db.migrate()
    con = db.connect()
    now = "2026-07-01T00:00:00.000Z"
    con.execute("INSERT INTO memory (topic, status, status_changed_at,"
                " created_at, updated_at) VALUES ('Iran War','active',?,?,?)",
                (now, now, now))
    slots = [{"slot": "1", "matched_memory": ["Iran War"]}]
    con.execute("INSERT INTO briefings (date, story_slots) VALUES ('2026-07-10', ?)",
                (json.dumps(slots),))
    con.commit()
    report = generate.GenReport(date="2026-07-10", variant="A")

    def state_chat(key, prompt):
        return ({"state": "It is a war now (Jul 10)."}, 0.001)

    spent = generate.run_memory_pass(
        con, "2026-07-10", "k", cap=0.25, spent=0.0,
        briefs_by_slot={1: _brief_with_arc()}, slots=slots, report=report,
        state_chat=state_chat)

    assert con.execute("SELECT COUNT(*) c FROM thread_deltas").fetchone()["c"] == 1
    assert con.execute("SELECT COUNT(*) c FROM thread_state").fetchone()["c"] == 1
    assert report.memory["deltas_written"] == 1
    assert report.memory["state_rewrites"][0]["outcome"] == "written"
    assert spent > 0.0 and report.memory_usd > 0.0
    # and the glue is actually called from the generate body (grep-proof twin)
    import inspect
    assert "run_memory_pass" in inspect.getsource(generate._run_generate_body)
    con.close()


def test_wiring_call_sites_present():
    """Grep-proof (team/ENGINEERING.md): the render surfaces call the memory
    core — the slim Today stamp, the deep-view arc line, the deep timeline, the
    dossier state card.

    ANTI-RESURRECTION TRIPWIRE, strengthened (arc-line batch, 2026-07-18): the
    Today arc carries NO generated prose — furniture only. v8-M2 pinned the
    absence of the CALL; this batch DELETED the render itself, so the tripwire now
    pins the FUNCTIONS gone (_today_arc_html in server, render_today_arc in the
    memory core) AND the new stored-field render wired. If a future edit revives
    Today-arc prose, or wires arc prose anywhere but the deep view's stored field,
    this fails."""
    import inspect
    ssrc = inspect.getsource(server)
    msrc = inspect.getsource(memory_core)
    # the deterministic Today-arc render is gone at the ROOT (not just uncalled):
    assert "_today_arc_html(con, slot, st, date, arc_seen)" not in ssrc
    assert "def _today_arc_html" not in ssrc            # the whole render is deleted
    assert "def render_today_arc" not in msrc           # its memory-core producer too
    # Today carries FURNITURE only — the slim stamp, no prose:
    assert "_memory_stamp_inner(con, slot, date, degraded=" in ssrc  # stamp wired into _render_story
    # the ONLY generated arc prose is the deep view's stored, authored arc_line:
    assert "_deep_arc_line_html(con, slot, date)" in ssrc            # render swap wired (item 3)
    assert "def state_for_edition" in msrc                           # the reader it renders from
    assert "_deep_timeline_html(con, slot, date, story_anchor)" in ssrc  # deep-view timeline
    assert "_thread_state_card(t)" in ssrc                         # dossier state card (Following)
    asrc = inspect.getsource(analysis.analyze_story)
    assert "prior_for_slot" in asrc                                # analyst prior wired
