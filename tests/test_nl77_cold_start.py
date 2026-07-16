"""NL-77 the thread cold-start backgrounder — the entry-zero baseline genre.

Acceptance-contract reds (each fails on the pre-NL-77 tree — the migration, the
memory_core baseline helpers, the generator, the diction validator, the
retroactive command, and the intent gate do not exist there; each is the red its
own wiring flips):

  * storage — migration 0017 side table, append-only, provenance pinned;
  * the cite currency '(baseline, <date>)', dated-anchored never bare;
  * the generator (analyst pointed backwards, injected chat) — ready / failed /
    budget-skip, refusal never fabricates, spend durable on the row, marked
    external-synthesis;
  * the anti-obligation invariant — a baseline NEVER feeds a Today arc's "then"
    leg and NEVER enters the story-so-far timeline (it is not a delta);
  * writer-flow LAST — the backgrounder rides the writer prompt as its own
    labeled section, AFTER the ledger, never blended;
  * the dated-anchored diction validator (licensed only dated-anchored);
  * the HSR-numerator exclusion;
  * the §F intent gate (follow / first-open capture; NEVER a read event);
  * the retroactive-baseline command driver.
"""

from __future__ import annotations

import sqlite3

import pytest

from newslens import db, events, generate, memory_core as mc, paths


# --- helpers ---------------------------------------------------------------

def _thread(con, topic, status="active", note=None):
    con.execute("INSERT INTO memory (topic, status, principal_note) VALUES (?, ?, ?)",
                (topic, status, note))
    return con.execute("SELECT id FROM memory WHERE topic = ?", (topic,)).fetchone()["id"]


def _delta(con, tid, date, what, slot=1, signif="x", verdict="advances"):
    con.execute(
        "INSERT INTO thread_deltas (thread_id, edition_date, slot, verdict,"
        " what_happened, significance, cites_json) VALUES (?, ?, ?, ?, ?, ?, '[\"S1\"]')",
        (tid, date, slot, verdict, what, signif))


def _good_chat(bg="The dispute began in the early 2010s.\n\nBy 2015 a deal was struck.",
               seed="It broadly stands unresolved.", cites=("established background",),
               cost=0.0123):
    def chat(key, prompt):
        return ({"backgrounder": bg, "state_seed": seed, "cites": list(cites)}, cost)
    return chat


# --- storage: migration 0017 -----------------------------------------------

def test_thread_baselines_table_is_append_only(migrated_con):
    con = migrated_con
    tid = _thread(con, "A")
    bid = mc.record_baseline(con, tid, "2026-07-14", "ready", backgrounder="bg 2015")
    with pytest.raises(sqlite3.IntegrityError):
        con.execute("UPDATE thread_baselines SET status='pending' WHERE id=?", (bid,))
    with pytest.raises(sqlite3.IntegrityError):
        con.execute("DELETE FROM thread_baselines WHERE id=?", (bid,))


def test_baseline_provenance_is_pinned_external_synthesis(migrated_con):
    con = migrated_con
    tid = _thread(con, "A")
    bid = mc.record_baseline(con, tid, "2026-07-14", "ready", backgrounder="bg 2015")
    row = con.execute("SELECT provenance FROM thread_baselines WHERE id=?", (bid,)).fetchone()
    assert row["provenance"] == "external-synthesis"
    with pytest.raises(sqlite3.IntegrityError):
        con.execute(
            "INSERT INTO thread_baselines (thread_id, as_of_date, status, provenance)"
            " VALUES (?, '2026-07-14', 'ready', 'record-established')", (tid,))


def test_baseline_status_check_rejects_unknown(migrated_con):
    con = migrated_con
    tid = _thread(con, "A")
    with pytest.raises(sqlite3.IntegrityError):
        con.execute("INSERT INTO thread_baselines (thread_id, as_of_date, status)"
                    " VALUES (?, '2026-07-14', 'done')", (tid,))


# --- intent capture + newest-wins ------------------------------------------

def test_write_baseline_intent_pending_then_dedups(migrated_con):
    con = migrated_con
    tid = _thread(con, "A")
    first = mc.write_baseline_intent(con, tid, "2026-07-16")
    assert first is not None
    assert mc.latest_baseline(con, tid)["status"] == "pending"
    # a standing pending/ready intent is not re-stacked
    assert mc.write_baseline_intent(con, tid, "2026-07-16") is None


def test_ready_then_later_failed_makes_ready_stale(migrated_con):
    con = migrated_con
    tid = _thread(con, "A")
    mc.record_baseline(con, tid, "2026-07-14", "ready", backgrounder="bg 2015")
    assert mc.ready_baseline(con, tid) is not None
    mc.record_baseline(con, tid, "2026-07-16", "failed", reason="retry failed")
    # newest wins: the newest is 'failed', so there is no live ready baseline
    assert mc.ready_baseline(con, tid) is None
    assert mc.latest_baseline(con, tid)["status"] == "failed"


def test_failed_baseline_reopens_intent(migrated_con):
    con = migrated_con
    tid = _thread(con, "A")
    mc.record_baseline(con, tid, "2026-07-14", "failed", reason="rejected")
    # a failed newest allows a fresh pending intent (a retry can be requested)
    assert mc.write_baseline_intent(con, tid, "2026-07-16") is not None


# --- the cite currency, dated-anchored never bare --------------------------

def test_baseline_cite_currency():
    assert mc.baseline_cite("2026-07-14") == "(baseline, Jul 14)"


def test_has_baseline_cite_requires_a_date():
    assert mc.has_baseline_cite("reinstated (baseline, Jul 14)")
    assert mc.has_baseline_cite("(baseline, 2026-07-14)")
    assert mc.has_baseline_cite("(baseline, July 14)")
    # bare references are NOT the dated-anchored form
    assert not mc.has_baseline_cite("per the baseline, reinstated")
    assert not mc.has_baseline_cite("(baseline)")
    assert not mc.has_baseline_cite("the blockade was reinstated")


# --- the generator (injected chat; no spend) --------------------------------

def test_generate_writes_ready_and_marks_external_synthesis(migrated_con):
    con = migrated_con
    tid = _thread(con, "Iran talks")
    res = generate.generate_thread_baseline(
        con, tid, "Iran talks", "", "2026-07-16", "k", remaining_usd=0.25,
        chat=_good_chat())
    assert res.outcome == "written"
    assert res.cost_usd == pytest.approx(0.0123)
    b = mc.ready_baseline(con, tid)
    assert b and b["provenance"] == "external-synthesis"
    assert b["state_seed"] and b["backgrounder"]
    assert b["cost_usd"] == pytest.approx(0.0123)      # spend durable on the row


def test_generate_rejects_bare_continuity_to_failed_never_fabricates(migrated_con):
    con = migrated_con
    tid = _thread(con, "Blockade")
    res = generate.generate_thread_baseline(
        con, tid, "Blockade", "", "2026-07-16", "k", remaining_usd=0.25,
        chat=_good_chat(bg="The blockade was reinstated.", seed="It stands."))
    assert res.outcome == "rejected"
    b = mc.latest_baseline(con, tid)
    assert b["status"] == "failed"
    assert b["backgrounder"] == ""     # the honest gap, not invented content
    assert mc.ready_baseline(con, tid) is None


def test_generate_budget_skip_keeps_pending_writes_no_row(migrated_con):
    con = migrated_con
    tid = _thread(con, "A")
    mc.write_baseline_intent(con, tid, "2026-07-16")
    before = con.execute("SELECT COUNT(*) c FROM thread_baselines").fetchone()["c"]
    res = generate.generate_thread_baseline(
        con, tid, "A", "", "2026-07-16", "k", remaining_usd=0.0, chat=_good_chat())
    assert res.outcome == "skipped-budget"
    after = con.execute("SELECT COUNT(*) c FROM thread_baselines").fetchone()["c"]
    assert after == before             # nothing written; pending intent stands
    assert mc.latest_baseline(con, tid)["status"] == "pending"


def test_generate_call_failure_records_failed_with_spend(migrated_con):
    con = migrated_con
    tid = _thread(con, "A")

    def boom(key, prompt):
        exc = RuntimeError("network down")
        exc.usd_spent = 0.007
        raise exc

    res = generate.generate_thread_baseline(
        con, tid, "A", "", "2026-07-16", "k", remaining_usd=0.25, chat=boom)
    assert res.outcome == "failed"
    b = mc.latest_baseline(con, tid)
    assert b["status"] == "failed"
    assert b["cost_usd"] == pytest.approx(0.007)   # BUG-32 money-honesty class


# --- the anti-obligation invariant: a baseline is not a delta ---------------

def test_baseline_never_feeds_today_arc(migrated_con):
    con = migrated_con
    tid = _thread(con, "Blockade")
    mc.record_baseline(con, tid, "2026-07-14", "ready",
                       backgrounder="Ships were blocked in March 2026.")
    # day-one thread (empty ledger) with a baseline STILL gets no arc, ever
    arc = mc.render_today_arc(con, tid, "Blockade", "Ships moved today.", "2026-07-20")
    assert arc is None


def test_baseline_never_enters_story_so_far_timeline(migrated_con):
    con = migrated_con
    tid = _thread(con, "Blockade")
    mc.record_baseline(con, tid, "2026-07-14", "ready", backgrounder="bg 2015")
    assert mc.timeline_rows(con, tid) == []     # baseline is not a ledger row


# --- writer-flow LAST -------------------------------------------------------

def test_writer_baseline_block_is_labeled_and_carries_non_licensing_law(migrated_con):
    con = migrated_con
    tid = _thread(con, "Iran talks")
    mc.record_baseline(con, tid, "2026-07-14", "ready",
                       backgrounder="The talks began in the early 2010s.")
    block = mc.writer_baseline_block(con, "Iran talks", before_date="2026-07-20")
    assert "BACKGROUNDER" in block
    assert "(baseline, Jul 14)" in block          # its cite currency
    assert "EXTERNAL SYNTHESIS" in block
    assert "BARE" in block or "bare" in block      # the non-licensing law inline


def test_writer_baseline_block_empty_without_ready_baseline(migrated_con):
    con = migrated_con
    tid = _thread(con, "Iran talks")
    mc.write_baseline_intent(con, tid, "2026-07-16")   # pending only
    assert mc.writer_baseline_block(con, "Iran talks", "2026-07-20") == ""


def test_writer_prompt_places_baseline_after_the_ledger(monkeypatch):
    # Writer-flow LAST, as a liveness check on the prompt assembly: the
    # backgrounder block rides AFTER the ledger block, never merged into it.
    inputs = {
        "threads": [],
        "continuity_status": "none",
        "prior_ctx": {},
        "items_by_slot": {1: []},
        "briefs_by_slot": {},
        "slots": [{
            "slot": 1, "story_title": "t", "summary": "s",
            "matched_tags": [], "matched_memory": ["X"], "world_impact_reason": "",
            "thread_ledger": "LEDGER_BLOCK_MARK",
            "thread_baseline": "BASELINE_BLOCK_MARK", "expired_watch": [],
        }],
    }
    prompt = generate.build_narrative_prompt("2026-07-20", "A", inputs)
    assert "BASELINE_BLOCK_MARK" in prompt and "LEDGER_BLOCK_MARK" in prompt
    assert prompt.index("BASELINE_BLOCK_MARK") > prompt.index("LEDGER_BLOCK_MARK")


# --- the dated-anchored diction validator -----------------------------------

def _story(lede):
    return {"headline": "h", "lede": lede, "why_it_matters": ""}


def test_dated_anchored_baseline_cite_licenses_repetition(migrated_con):
    con = migrated_con
    tid = _thread(con, "Blockade")
    mc.record_baseline(con, tid, "2026-07-14", "ready", backgrounder="bg 2015")
    slots = [{"slot": 1, "matched_memory": ["Blockade"]}]
    stories = [_story("The blockade was reinstated (baseline, Jul 14).")]
    assert generate.forward_claim_findings(con, stories, slots, "2026-07-20") == []


def test_bare_repetition_on_baselined_thread_flagged_once(migrated_con):
    con = migrated_con
    tid = _thread(con, "Blockade")
    mc.record_baseline(con, tid, "2026-07-14", "ready", backgrounder="bg 2015")
    slots = [{"slot": 1, "matched_memory": ["Blockade"]}]
    stories = [_story("The blockade was reinstated.")]     # bare — no cite
    findings = generate.forward_claim_findings(con, stories, slots, "2026-07-20")
    assert len(findings) == 1                              # no double-surfacing


def test_baseline_gesture_without_date_is_flagged(migrated_con):
    con = migrated_con
    tid = _thread(con, "Blockade")
    mc.record_baseline(con, tid, "2026-07-14", "ready", backgrounder="bg 2015")
    slots = [{"slot": 1, "matched_memory": ["Blockade"]}]
    # "per the baseline" reads as source-attributed to the generic net; only the
    # dated-anchored validator catches the missing date.
    stories = [_story("Per the baseline, the blockade was reinstated.")]
    findings = generate.baseline_diction_findings(con, stories, slots, "2026-07-20")
    assert len(findings) == 1
    assert "dated-anchored" in findings[0]


def test_diction_validator_silent_without_baseline(migrated_con):
    con = migrated_con
    _thread(con, "Blockade")
    slots = [{"slot": 1, "matched_memory": ["Blockade"]}]
    stories = [_story("Per the baseline, the blockade was reinstated.")]
    assert generate.baseline_diction_findings(con, stories, slots, "2026-07-20") == []


# --- HSR-numerator exclusion -----------------------------------------------

def test_baseline_cited_sentence_excluded_from_hsr():
    assert mc.is_baseline_sourced_sentence("reimposed the ban (baseline, Jul 14)")
    assert not mc.is_baseline_sourced_sentence("reimposed the ban (Jul 14)")


# --- the §F intent gate -----------------------------------------------------

def test_follow_captures_baseline_intent(monkeypatch, tmp_path):
    from newslens import cli
    rc = cli.main(["memory", "add", "Some New Thread"])
    assert rc == 0
    con = db.connect(paths.DB_PATH)
    try:
        row = con.execute(
            "SELECT b.status FROM thread_baselines b JOIN memory m ON m.id=b.thread_id"
            " WHERE lower(m.topic)='some new thread'").fetchone()
        assert row is not None and row["status"] == "pending"
    finally:
        con.close()


def test_capture_baseline_intent_topic_keyed(migrated_con):
    con = migrated_con
    tid = _thread(con, "Iran talks")
    # the topic-keyed §F entrypoint (the server's first-open call surface)
    assert mc.capture_baseline_intent(con, "iran talks", "2026-07-16") is not None
    assert mc.latest_baseline(con, tid)["status"] == "pending"


def test_capture_baseline_intent_skips_dismissed_thread(migrated_con):
    con = migrated_con
    _thread(con, "Old thread", status="dismissed_user")
    # a dismissed thread is not resolvable — no baseline intent (§F: the reader
    # said stop; nothing is inferred back into wanting a baseline)
    assert mc.capture_baseline_intent(con, "Old thread", "2026-07-16") is None
    assert con.execute("SELECT COUNT(*) c FROM thread_baselines").fetchone()["c"] == 0


def test_read_events_never_trigger_a_baseline(migrated_con):
    con = migrated_con
    tid = _thread(con, "Iran talks")
    # a reader opening/reading is NOT an explicit follow — §F: never inferred
    events.log_read(con, "2026-07-20")
    events.log_thread_view(con, "2026-07-20", "Iran talks", referrer="today")
    events.log_deep_view(con, "2026-07-20", "Iran talks", referrer="today")
    assert con.execute("SELECT COUNT(*) c FROM thread_baselines").fetchone()["c"] == 0


# --- the retroactive-baseline command driver --------------------------------

def test_retro_backfill_all_floors_empty_ledger_threads(migrated_con):
    con = migrated_con
    _thread(con, "Alpha")
    _thread(con, "Beta")
    rep = generate.run_baseline_backfill(
        all_threads=True, con=con, env={}, date="2026-07-20", chat=_good_chat())
    assert not rep.refused
    assert {g["thread"] for g in rep.generated} == {"Alpha", "Beta"}
    assert all(g["outcome"] == "written" for g in rep.generated)
    assert rep.spent_usd == pytest.approx(0.0246)     # 2 * 0.0123, durable total


def test_retro_backfill_skips_threads_with_a_ledger(migrated_con):
    con = migrated_con
    tid = _thread(con, "HasRecord")
    _delta(con, tid, "2026-07-10", "something happened")
    _thread(con, "ColdStart")
    rep = generate.run_baseline_backfill(
        all_threads=True, con=con, env={}, date="2026-07-20", chat=_good_chat())
    assert {g["thread"] for g in rep.generated} == {"ColdStart"}   # not HasRecord


def test_retro_backfill_thread_id_refuses_when_ledger_present(migrated_con):
    con = migrated_con
    tid = _thread(con, "HasRecord")
    _delta(con, tid, "2026-07-10", "something happened")
    rep = generate.run_baseline_backfill(
        thread_id=tid, con=con, env={}, date="2026-07-20", chat=_good_chat())
    assert rep.refused and "ledger" in rep.reason


def test_retro_backfill_refuses_when_nothing_awaits(migrated_con):
    con = migrated_con
    tid = _thread(con, "A")
    mc.record_baseline(con, tid, "2026-07-14", "ready", backgrounder="bg 2015")
    rep = generate.run_baseline_backfill(
        all_threads=True, con=con, env={}, date="2026-07-20", chat=_good_chat())
    assert rep.refused


def test_retro_backfill_needs_exactly_one_selector(migrated_con):
    with pytest.raises(ValueError):
        generate.run_baseline_backfill(con=migrated_con, env={})
    with pytest.raises(ValueError):
        generate.run_baseline_backfill(thread_id=1, all_threads=True,
                                       con=migrated_con, env={})


# --- separability: reads degrade on a pre-0017 DB ---------------------------

def test_baseline_reads_degrade_on_pre_0017_db(tmp_path):
    db_path = tmp_path / "pre0017.db"
    con = db.connect(db_path)
    con.isolation_level = None
    con.execute("CREATE TABLE schema_migrations (filename TEXT PRIMARY KEY,"
                " applied_at TEXT)")
    for p in db.migration_files():
        if p.name >= "0017":
            break
        con.executescript(p.read_text(encoding="utf-8"))
        con.execute("INSERT INTO schema_migrations (filename) VALUES (?)", (p.name,))
    tid = _thread(con, "A")
    # table absent -> reads return None/[]/no-op, never crash (the contract)
    assert mc.latest_baseline(con, tid) is None
    assert mc.ready_baseline(con, tid) is None
    assert mc.write_baseline_intent(con, tid, "2026-07-16") is None
    assert mc.writer_baseline_block(con, "A", "2026-07-20") == ""
    con.close()
