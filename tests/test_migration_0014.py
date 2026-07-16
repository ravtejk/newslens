"""Migration 0014: thread_delta_provenance — the poisoned-antecedent bound
(NL-69; HSR baseline §5.1(2); engineering council 2026-07-17, Ruling 1; the
Content-council external-synthesis addendum 2026-07-17).

Born-red: thread_delta_provenance, memory_core.classify_delta_provenance /
mark_delta_provenance / effective_provenance, the read-site source-echo
exclusion in has_predating_antecedent, the self-mark inside
write_deltas_for_edition, and the `memory-mark-provenance` CLI verb do not exist
on b02ab11.

The trap this closes: the 07-14 backfill wrote "reinstated a naval blockade"
into thread_deltas 5-6. Strict before_date protected the antecedent validator
only while those rows were same-day; from 2026-07-17 they PREDATE every new
edition, so the date bound alone now VALIDATES the poison. The provenance mark
is what keeps a source-echo row from ever licensing the word it echoed.
"""

from __future__ import annotations

import json
import sqlite3

import pytest

from newslens import cli, db, memory_core


# --- helpers ----------------------------------------------------------------

def _thread(con, topic="Strait of Hormuz"):
    con.execute("INSERT INTO memory (topic, status) VALUES (?, 'active')", (topic,))
    return con.execute("SELECT id FROM memory WHERE topic = ?", (topic,)).fetchone()["id"]


def _delta(con, tid, date, what, signif="", slot=1, cites='["S1"]'):
    con.execute(
        "INSERT INTO thread_deltas (thread_id, edition_date, slot, verdict,"
        " what_happened, significance, cites_json)"
        " VALUES (?, ?, ?, 'advances', ?, ?, ?)",
        (tid, date, slot, what, signif, cites))
    return con.execute(
        "SELECT id FROM thread_deltas ORDER BY id DESC LIMIT 1").fetchone()["id"]


def _mark(con, delta_id, provenance, reason="test"):
    con.execute(
        "INSERT INTO thread_delta_provenance (delta_id, provenance, reason)"
        " VALUES (?, ?, ?)", (delta_id, provenance, reason))
    con.commit()


# ===========================================================================
# A. MIGRATION STRUCTURE — side table, CHECK, PK, append-only (0012 pattern)
# ===========================================================================

def test_table_and_index_exist_after_migrate(migrated_con):
    con = migrated_con
    assert con.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND "
        "name='thread_delta_provenance'").fetchone() is not None
    assert con.execute(
        "SELECT 1 FROM sqlite_master WHERE type='index' AND "
        "name='idx_provenance_provenance'").fetchone() is not None


def test_migration_is_idempotent(tmp_path):
    """Re-running migrate applies 0014 exactly once, then nothing (IF NOT
    EXISTS everywhere — ENGINEERING.md idempotency rule)."""
    db_path = tmp_path / "idem.db"
    ran = db.migrate(db_path=db_path)
    assert "0014_thread_delta_provenance.sql" in ran
    assert db.migrate(db_path=db_path) == []  # nothing re-applied


def test_check_accepts_the_four_grades(migrated_con):
    con = migrated_con
    tid = _thread(con)
    for i, grade in enumerate(
            ("source-echo", "record-established", "reader-explicit",
             "external-synthesis")):
        did = _delta(con, tid, "2026-07-05", f"event {i}", slot=i + 1)
        con.execute("INSERT INTO thread_delta_provenance (delta_id, provenance)"
                    " VALUES (?, ?)", (did, grade))
    con.commit()
    assert con.execute("SELECT COUNT(*) FROM thread_delta_provenance").fetchone()[0] == 4


def test_check_rejects_an_unknown_grade(migrated_con):
    con = migrated_con
    tid = _thread(con)
    did = _delta(con, tid, "2026-07-05", "event")
    with pytest.raises(sqlite3.IntegrityError):
        con.execute("INSERT INTO thread_delta_provenance (delta_id, provenance)"
                    " VALUES (?, 'made-up')", (did,))


def test_delta_id_is_pk_a_delta_is_marked_at_most_once(migrated_con):
    con = migrated_con
    tid = _thread(con)
    did = _delta(con, tid, "2026-07-05", "event")
    _mark(con, did, "source-echo")
    with pytest.raises(sqlite3.IntegrityError):
        con.execute("INSERT INTO thread_delta_provenance (delta_id, provenance)"
                    " VALUES (?, 'record-established')", (did,))


def test_append_only_no_update_no_delete(migrated_con):
    con = migrated_con
    tid = _thread(con)
    did = _delta(con, tid, "2026-07-05", "event")
    _mark(con, did, "source-echo")
    with pytest.raises(sqlite3.IntegrityError):
        con.execute("UPDATE thread_delta_provenance SET provenance='record-established'"
                    " WHERE delta_id = ?", (did,))
    with pytest.raises(sqlite3.IntegrityError):
        con.execute("DELETE FROM thread_delta_provenance WHERE delta_id = ?", (did,))


def test_marked_at_defaults_to_a_timestamp(migrated_con):
    con = migrated_con
    tid = _thread(con)
    did = _delta(con, tid, "2026-07-05", "event")
    _mark(con, did, "source-echo")
    marked = con.execute(
        "SELECT marked_at FROM thread_delta_provenance WHERE delta_id = ?",
        (did,)).fetchone()["marked_at"]
    assert marked and marked.endswith("Z")


# ===========================================================================
# B. THE READ-SITE BOUND — the trap pin (the exact 07-14 shape)
# ===========================================================================

_LATER_EDITION = "2026-07-17"   # from today the 07-14 rows PREDATE this edition


def test_source_echo_predating_row_still_refuses_to_license(migrated_con):
    """THE TRAP PIN. A source-echo-marked row that PREDATES the edition still
    refuses to license the word it echoed — the exact deltas-5-6 shape read
    from an edition AFTER 07-14. Strict before_date no longer protects (the row
    predates); only the provenance mark does."""
    con = migrated_con
    tid = _thread(con)
    did = _delta(con, tid, "2026-07-14",
                 "The U.S. launched new military strikes and reinstated a naval "
                 "blockade in the Strait of Hormuz.")
    _mark(con, did, "source-echo",
          "the 07-14 backfill echoed edition-day source diction")
    assert memory_core.has_predating_antecedent(
        con, "Strait of Hormuz", {"blockade"}, _LATER_EDITION) is False


def test_the_same_row_UNMARKED_would_license(migrated_con):
    """Control for the trap pin: identical row, NO mark → record-established
    default → it DOES license. Proves the provenance mark is the thing that
    refuses, not the date or the words."""
    con = migrated_con
    tid = _thread(con)
    _delta(con, tid, "2026-07-14",
           "The U.S. launched new military strikes and reinstated a naval "
           "blockade in the Strait of Hormuz.")
    assert memory_core.has_predating_antecedent(
        con, "Strait of Hormuz", {"blockade"}, _LATER_EDITION) is True


def test_external_synthesis_predating_row_still_refuses_to_license(migrated_con):
    """Addendum pin: an external-synthesis row (baseline-derived diction) that
    predates the edition also refuses to license a bare repetition word."""
    con = migrated_con
    tid = _thread(con)
    did = _delta(con, tid, "2026-07-14",
                 "The blockade of the strait resumed after a lull.")
    _mark(con, did, "external-synthesis", "thread-baseline entry-zero diction")
    assert memory_core.has_predating_antecedent(
        con, "Strait of Hormuz", {"blockade"}, _LATER_EDITION) is False


def test_source_echo_excluded_even_in_the_no_discriminator_branch(migrated_con):
    """With an empty subject set has_predating_antecedent returns bool(prior);
    a source-echo-only prior set must be treated as empty (return False), not
    license on 'any predating history'."""
    con = migrated_con
    tid = _thread(con)
    did = _delta(con, tid, "2026-07-14", "reinstated the blockade")
    _mark(con, did, "source-echo")
    assert memory_core.has_predating_antecedent(
        con, "Strait of Hormuz", set(), _LATER_EDITION) is False


def test_record_established_and_reader_explicit_still_license(migrated_con):
    con = migrated_con
    tid = _thread(con)
    d1 = _delta(con, tid, "2026-07-05", "The U.S. imposed a naval blockade.")
    _mark(con, d1, "record-established")
    assert memory_core.has_predating_antecedent(
        con, "Strait of Hormuz", {"blockade"}, _LATER_EDITION) is True
    d2 = _delta(con, tid, "2026-07-06", "The reader flagged the blockade.",
                slot=2)
    _mark(con, d2, "reader-explicit")   # d1's record-established mark stays
    assert memory_core.has_predating_antecedent(
        con, "Strait of Hormuz", {"blockade"}, _LATER_EDITION) is True


def test_effective_provenance_defaults_to_record_established():
    assert memory_core.effective_provenance({}) == "record-established"
    assert memory_core.effective_provenance(
        {"provenance": None}) == "record-established"
    assert memory_core.effective_provenance(
        {"provenance": "source-echo"}) == "source-echo"


def test_ledger_for_thread_surfaces_provenance(migrated_con):
    con = migrated_con
    tid = _thread(con)
    did = _delta(con, tid, "2026-07-14", "reinstated the blockade")
    _mark(con, did, "source-echo")
    _delta(con, tid, "2026-07-05", "imposed the blockade", slot=2)  # unmarked
    rows = memory_core.ledger_for_thread(con, tid)
    by_id = {r["id"]: r for r in rows}
    assert by_id[did]["provenance"] == "source-echo"
    other = [r for r in rows if r["id"] != did][0]
    assert other["provenance"] is None  # unmarked surfaces as NULL


# ===========================================================================
# C. THE SELF-MARKING WRITE PATH — classify + live wiring
# ===========================================================================

def test_classify_marks_the_poison_shape_source_echo(migrated_con):
    """A delta carrying a repetition word with NO predating antecedent on the
    thread is source-echo (the deltas-5-6 shape)."""
    con = migrated_con
    tid = _thread(con)
    grade = memory_core.classify_delta_provenance(
        con, "Strait of Hormuz",
        "The U.S. launched new military strikes and reinstated a naval blockade "
        "in the Strait of Hormuz.",
        "The conflict escalated to direct military confrontation.",
        "2026-07-14")
    assert grade == "source-echo"


def test_classify_leaves_a_fresh_event_delta_unmarked(migrated_con):
    """A fresh-event delta (no repetition word) is record-grade → None. Marking
    it source-echo would silently refuse a legitimate FUTURE antecedent."""
    con = migrated_con
    tid = _thread(con)
    grade = memory_core.classify_delta_provenance(
        con, "Strait of Hormuz",
        "The U.S. imposed a naval blockade of the strait.",
        "Oil markets seized.", "2026-07-14")
    assert grade is None


def test_classify_leaves_a_record_backed_continuity_unmarked(migrated_con):
    """A repetition word WITH a real predating antecedent is record-established
    (not source-echo) — the honest label, and it keeps licensing intact."""
    con = migrated_con
    tid = _thread(con)
    _delta(con, tid, "2026-07-05", "The U.S. imposed a naval blockade.")
    grade = memory_core.classify_delta_provenance(
        con, "Strait of Hormuz",
        "The U.S. reinstated the naval blockade after a brief lull.",
        "", "2026-07-14")
    assert grade is None


def test_write_deltas_for_edition_self_marks_the_poison(migrated_con):
    """LIVENESS: the wiring is proven by the write path itself. A backfill delta
    citing only edition-day sources with the poison shape gets a source-echo row
    written in the SAME pass — this test fails without the self-mark call inside
    write_deltas_for_edition."""
    con = migrated_con
    tid = _thread(con)
    arc = {
        "delta": "advances",
        "what_happened": ("The U.S. launched new military strikes and reinstated "
                          "a naval blockade in the Strait of Hormuz."),
        "significance": "The conflict escalated to direct military confrontation.",
        "cites": ["S2", "S4"],   # edition-day sources only
    }
    briefs_by_slot = {1: {"brief": {"arc": arc}}}
    slots = [{"slot": 1, "matched_memory": ["Strait of Hormuz"]}]
    report = memory_core.write_deltas_for_edition(
        con, "2026-07-14", None, briefs_by_slot, slots)
    assert len(report.written) == 1
    did = con.execute(
        "SELECT id FROM thread_deltas WHERE thread_id = ? AND edition_date = ?",
        (tid, "2026-07-14")).fetchone()["id"]
    grade = con.execute(
        "SELECT provenance FROM thread_delta_provenance WHERE delta_id = ?",
        (did,)).fetchone()
    assert grade is not None and grade["provenance"] == "source-echo"


def test_write_deltas_for_edition_does_not_mark_a_fresh_event(migrated_con):
    """A live fresh-event delta must NOT get a provenance row — it stays
    record-established so it can license future repetition words."""
    con = migrated_con
    tid = _thread(con)
    arc = {
        "delta": "advances",
        "what_happened": "The U.S. imposed a naval blockade of the strait.",
        "significance": "Oil markets seized.",
        "cites": ["S1"],
    }
    briefs_by_slot = {1: {"brief": {"arc": arc}}}
    slots = [{"slot": 1, "matched_memory": ["Strait of Hormuz"]}]
    memory_core.write_deltas_for_edition(
        con, "2026-07-14", None, briefs_by_slot, slots)
    assert con.execute(
        "SELECT COUNT(*) FROM thread_delta_provenance").fetchone()[0] == 0


# ===========================================================================
# D. THE SUPERVISED MARKING COMMAND — the tool + its refusals
# ===========================================================================

def test_mark_delta_provenance_happy_path(migrated_con):
    con = migrated_con
    tid = _thread(con)
    did = _delta(con, tid, "2026-07-14", "reinstated the blockade")
    ok, msg, row = memory_core.mark_delta_provenance(
        con, did, "source-echo", "the CoS ran this with the principal's word")
    assert ok is True
    assert row["id"] == did
    assert con.execute(
        "SELECT provenance FROM thread_delta_provenance WHERE delta_id = ?",
        (did,)).fetchone()["provenance"] == "source-echo"


def test_mark_delta_provenance_refuses_unknown_id(migrated_con):
    ok, msg, row = memory_core.mark_delta_provenance(
        migrated_con, 99999, "source-echo")
    assert ok is False and row is None
    assert "no thread_delta with id 99999" in msg


def test_mark_delta_provenance_refuses_already_marked(migrated_con):
    con = migrated_con
    tid = _thread(con)
    did = _delta(con, tid, "2026-07-14", "reinstated the blockade")
    memory_core.mark_delta_provenance(con, did, "source-echo")
    ok, msg, row = memory_core.mark_delta_provenance(
        con, did, "record-established")
    assert ok is False
    assert "already marked" in msg
    assert row is not None  # the delta row is still returned so the caller sees it
    # the original mark is untouched (append-only)
    assert con.execute(
        "SELECT provenance FROM thread_delta_provenance WHERE delta_id = ?",
        (did,)).fetchone()["provenance"] == "source-echo"


def test_mark_delta_provenance_refuses_unknown_grade(migrated_con):
    con = migrated_con
    tid = _thread(con)
    did = _delta(con, tid, "2026-07-14", "reinstated the blockade")
    ok, msg, row = memory_core.mark_delta_provenance(con, did, "not-a-grade")
    assert ok is False
    assert "unknown provenance" in msg


def test_cli_marks_a_delta_and_prints_its_text(tmp_paths, capsys):
    """End-to-end CLI: migrate the sandbox DB, insert a delta, mark it. The
    command prints the delta text it graded and exits 0."""
    assert cli.main(["migrate"]) == 0
    capsys.readouterr()
    con = db.connect()
    try:
        tid = _thread(con)
        did = _delta(con, tid, "2026-07-14",
                     "reinstated a naval blockade in the Strait of Hormuz")
        con.commit()
    finally:
        con.close()
    rc = cli.main(["memory-mark-provenance", "--delta-id", str(did),
                   "--provenance", "source-echo", "--reason", "07-14 backfill"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "reinstated a naval blockade" in out          # printed the delta text
    assert f"marked delta {did} provenance=source-echo" in out
    con = db.connect()
    try:
        assert con.execute(
            "SELECT provenance FROM thread_delta_provenance WHERE delta_id = ?",
            (did,)).fetchone()["provenance"] == "source-echo"
    finally:
        con.close()


def test_cli_refuses_a_nonexistent_delta(tmp_paths, capsys):
    assert cli.main(["migrate"]) == 0
    capsys.readouterr()
    rc = cli.main(["memory-mark-provenance", "--delta-id", "424242",
                   "--provenance", "source-echo"])
    err = capsys.readouterr().err
    assert rc == 1
    assert "REFUSED" in err and "424242" in err
