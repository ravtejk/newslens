"""NL-77 REMNANT / migration 0027 — THE DELETE TRIANGLE, DISSOLVED.

THE TRIANGLE (proven on untouched 7d878a7 bytes before this file existed): three
shipped rules could not all hold at once —

  1. ADR-0010 §4 — a memory row is HARD-deleted; delete is the product's only
     irreversible verb.
  2. migration 0017's `trg_thread_baselines_no_delete` — a baseline row is NEVER
     deleted (append-only; a baseline is a dated fact).
  3. migration 0017's FK — `thread_id NOT NULL REFERENCES memory(id)` with no
     cascade, and `db.connect()` turns foreign keys ON for every connection.

So any thread that ever received a baseline row — a `pending` intent counts —
became permanently undeletable, and `memory.delete_thread` raised
`sqlite3.IntegrityError: FOREIGN KEY constraint failed` from inside its own
transaction, taking the NL-81 tombstone down with it. Live in the principal's
tree for all 18 of his retro-baselined threads; this migration unblocks 15 —
ids 13/16/21 stay blocked by other FK legs (deltas/watch items), outside the
ruled scope (see the scope tripwire below).

THE RULING (principal, 2026-08-24): Option A — recreate the FK with ON DELETE
CASCADE. Deleting a thread deletes its baseline rows with it.

THE REEF THIS FILE ALSO GUARDS. A BEFORE DELETE trigger in SQLite fires on rows
removed by a cascade too, so the FK change alone would have been a no-op: 0017's
trigger would have caught the very cascade the ruling created. 0027 recreates
the trigger GUARDED —

    WHEN EXISTS (SELECT 1 FROM memory WHERE id = OLD.thread_id)

— because the parent row is already gone by the time a cascade fires the child's
trigger, and still standing when a direct `DELETE FROM thread_baselines` fires
it. That is the structural difference between the two deletes, and it is what
keeps this NARROWER than the rejected Option B: a direct delete of a live
thread's baseline still ABORTs (pinned below), so the append-only law is
untouched everywhere except in the arms of the thread delete he ruled on.

SCOPE, PINNED RATHER THAN ASSUMED (last test in this file): thread_baselines is
one of FIVE tables whose FK points at memory(id) with no cascade. The other four
— thread_deltas, thread_state, watch_items, thread_closures — block
`delete_thread` identically and are NOT fixed here. That is a measured, named
scope bound, not an oversight, and the tripwire holds the record to it.

Offline by construction: autouse sandbox (conftest), no network, no key, $0.
"""

from __future__ import annotations

import sqlite3

import pytest

from newslens import db, memory, memory_core as mc


MIGRATION = "0027_thread_baselines_delete_cascade.sql"


# --- helpers ---------------------------------------------------------------

def _thread(con, topic, status="active"):
    with con:
        con.execute("INSERT INTO memory (topic, status) VALUES (?, ?)",
                    (topic, status))
    return con.execute("SELECT id FROM memory WHERE topic = ?",
                       (topic,)).fetchone()["id"]


def _baselines(con, tid):
    return [dict(r) for r in con.execute(
        "SELECT id, status FROM thread_baselines WHERE thread_id = ? ORDER BY id",
        (tid,))]


def _dismiss(con, tid):
    with con:
        con.execute("UPDATE memory SET status='dismissed_user' WHERE id = ?",
                    (tid,))


# ===========================================================================
# 1 — THE TRIANGLE IS GONE
# ===========================================================================

def test_a_baselined_thread_deletes_instead_of_raising(migrated_con):
    """BORN RED — the whole point. On 7d878a7 this raised
    `sqlite3.IntegrityError: FOREIGN KEY constraint failed`."""
    con = migrated_con
    tid = _thread(con, "Government Shutdown")
    mc.record_baseline(con, tid, "2026-07-16", "ready", backgrounder="How we got here.")
    _dismiss(con, tid)

    assert memory.delete_thread(con, "Government Shutdown") == (True, "deleted")
    assert con.execute("SELECT 1 FROM memory WHERE id = ?", (tid,)).fetchone() is None


def test_a_pending_intent_alone_no_longer_makes_a_thread_undeletable(migrated_con):
    """BORN RED — the sharpest edge of the triangle, and the one the NL-77
    remnant would have widened from the CLI to every UI tap: a `pending` row is
    an INTENT, $0, no content, and it was enough to brick the delete verb."""
    con = migrated_con
    tid = _thread(con, "Helium Shortage")
    assert mc.write_baseline_intent(con, tid, "2026-07-16") is not None
    assert [r["status"] for r in _baselines(con, tid)] == ["pending"]
    _dismiss(con, tid)

    assert memory.delete_thread(con, "Helium Shortage") == (True, "deleted")


def test_the_baseline_rows_go_with_the_thread(migrated_con):
    """BORN RED — the ruled CONSEQUENCE, pinned as a consequence and not as a
    side effect: every version of the thread's founding floor is removed."""
    con = migrated_con
    tid = _thread(con, "Stagflation")
    mc.write_baseline_intent(con, tid, "2026-07-16")
    mc.record_baseline(con, tid, "2026-07-17", "failed", reason="no sources")
    mc.record_baseline(con, tid, "2026-07-18", "ready", backgrounder="bg")
    assert len(_baselines(con, tid)) == 3
    _dismiss(con, tid)

    memory.delete_thread(con, "Stagflation")
    assert _baselines(con, tid) == []
    assert con.execute("SELECT count(*) FROM thread_baselines").fetchone()[0] == 0


def test_only_the_deleted_threads_baselines_go(migrated_con):
    """BORN RED — a cascade that took a sibling's founding floor with it would
    be a data-loss bug wearing the ruling's name."""
    con = migrated_con
    keeper = _thread(con, "Keeper")
    goner = _thread(con, "Goner")
    mc.record_baseline(con, keeper, "2026-07-16", "ready", backgrounder="keep me")
    mc.record_baseline(con, goner, "2026-07-16", "ready", backgrounder="drop me")
    _dismiss(con, goner)

    memory.delete_thread(con, "Goner")
    assert [r["status"] for r in _baselines(con, keeper)] == ["ready"]
    assert mc.latest_baseline(con, keeper)["backgrounder"] == "keep me"


def test_the_deletion_tombstone_lands_with_the_cascade(migrated_con):
    """BORN RED, and it is the subtle half. NL-81 §5.1 writes the tombstone in
    the SAME transaction as the DELETE, so the FK failure did not merely refuse
    the delete — it rolled the deletion RECORD back too. A thread that could not
    be deleted also could not be recorded as deleted."""
    con = migrated_con
    tid = _thread(con, "ECB")
    mc.record_baseline(con, tid, "2026-07-16", "ready", backgrounder="bg")
    _dismiss(con, tid)

    memory.delete_thread(con, "ECB")
    rows = [dict(r) for r in con.execute(
        "SELECT topic, kind, thread_id FROM memory_tombstones ORDER BY id")]
    assert rows == [{"topic": "ECB", "kind": "delete", "thread_id": tid}]


# ===========================================================================
# 2 — THE APPEND-ONLY LAW IS NARROWED, NOT PUNCTURED
# ===========================================================================

def test_a_direct_baseline_delete_is_still_refused(migrated_con):
    """CARRIED INVARIANT (born green — 0017's unguarded trigger refused this
    too), and the one that decides whether 0027 stayed inside the ruling or
    drifted into the rejected Option B. MUTATION-PROVEN: replace 0027's
    `WHEN EXISTS (...)` guard with `WHEN 0` and this goes green-to-red
    (receipt in the build report)."""
    con = migrated_con
    tid = _thread(con, "Recession Risk")
    bid = mc.record_baseline(con, tid, "2026-07-16", "ready", backgrounder="bg")

    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        con.execute("DELETE FROM thread_baselines WHERE id = ?", (bid,))
    assert len(_baselines(con, tid)) == 1


def test_a_wholesale_baseline_wipe_is_still_refused(migrated_con):
    """CARRIED INVARIANT — `DELETE FROM thread_baselines` with no WHERE is the
    shape an accident takes. Every row belongs to a live thread, so the guard's
    parent-exists condition is TRUE for each — the trigger fires on every row
    and the statement aborts whole."""
    con = migrated_con
    for topic in ("A", "B", "C"):
        mc.record_baseline(con, _thread(con, topic), "2026-07-16", "ready",
                           backgrounder="bg")
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        con.execute("DELETE FROM thread_baselines")
    assert con.execute("SELECT count(*) FROM thread_baselines").fetchone()[0] == 3


def test_a_baseline_row_is_still_never_edited(migrated_con):
    """CARRIED INVARIANT — 0027 recreates the UPDATE trigger verbatim and
    UNGUARDED. Nothing about a thread delete touches rewriting a dated fact."""
    con = migrated_con
    tid = _thread(con, "OPEC+")
    bid = mc.record_baseline(con, tid, "2026-07-16", "ready", backgrounder="bg")
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        con.execute("UPDATE thread_baselines SET status='pending' WHERE id = ?", (bid,))


def test_the_fk_carries_the_cascade_and_the_table_keeps_both_triggers(migrated_con):
    """The applied SCHEMA, read from the database rather than from the migration
    text — the reef was that a cascade alone is inert while an unguarded trigger
    stands, so both halves are pinned together.

    HALF BORN RED, half carried, and the halves are labelled because the
    born-red claim is proof-class currency: the `on_delete == CASCADE` assert
    FAILED at 7d878a7 ('NO ACTION'), while the trigger roster PASSED there —
    0017 created both triggers under these exact names. The roster half is a
    tripwire against a future 'fix' that resolves the reef by deleting the
    trigger instead of guarding it."""
    con = migrated_con
    fk = [dict(r) for r in con.execute("PRAGMA foreign_key_list(thread_baselines)")]
    assert len(fk) == 1
    assert (fk[0]["table"], fk[0]["from"], fk[0]["to"]) == ("memory", "thread_id", "id")
    assert fk[0]["on_delete"] == "CASCADE"

    triggers = [r["name"] for r in con.execute(
        "SELECT name FROM sqlite_master WHERE type='trigger'"
        " AND tbl_name='thread_baselines' ORDER BY name")]
    assert triggers == ["trg_thread_baselines_no_delete",
                        "trg_thread_baselines_no_update"]


# ===========================================================================
# 3 — THE REBUILD PRESERVED THE RECORD
# ===========================================================================

def test_the_rebuild_preserves_rows_ids_and_newest_wins(tmp_path):
    """The table is rebuilt (SQLite cannot ALTER an FK in place), so the rows
    that existed before 0027 must cross unchanged — ids INCLUDED, because
    `latest_baseline` resolves newest-wins by `ORDER BY id DESC` and a
    renumbering would silently reorder a thread's history."""
    db_path = tmp_path / "rebuild.db"
    applied = db.migrate(db_path=db_path)
    assert applied[-1] == MIGRATION

    con = db.connect(db_path)
    try:
        tid = _thread(con, "Redistricting")
        first = mc.record_baseline(con, tid, "2026-07-16", "failed", reason="thin")
        second = mc.record_baseline(con, tid, "2026-07-17", "ready", backgrounder="bg2")
        before = [tuple(r) for r in con.execute(
            "SELECT id, thread_id, as_of_date, status, backgrounder, state_seed,"
            " cites_json, provenance, reason, model, cost_usd, created_at"
            " FROM thread_baselines ORDER BY id")]
        # the documented lost-record crash gap: the script ran, the ledger row
        # did not land. Re-applying must be harmless (db.py's idempotency rule).
        with con:
            con.execute("DELETE FROM schema_migrations WHERE filename = ?", (MIGRATION,))
    finally:
        con.close()

    assert db.migrate(db_path=db_path) == [MIGRATION]

    con = db.connect(db_path)
    try:
        after = [tuple(r) for r in con.execute(
            "SELECT id, thread_id, as_of_date, status, backgrounder, state_seed,"
            " cites_json, provenance, reason, model, cost_usd, created_at"
            " FROM thread_baselines ORDER BY id")]
        assert after == before
        assert [r[0] for r in after] == [first, second]
        assert mc.latest_baseline(con, tid)["backgrounder"] == "bg2"
        assert con.execute(
            "SELECT count(*) FROM sqlite_master WHERE name LIKE '%\\_v2' ESCAPE '\\'"
        ).fetchone()[0] == 0
    finally:
        con.close()


def test_the_thread_index_survives_the_rebuild(migrated_con):
    """CARRIED INVARIANT (born green — 0017 created this index, so it passed at
    7d878a7 too). It is here because DROP TABLE takes its indexes with it: 0027
    must recreate the one 0017 created, or every baseline read silently loses
    its (thread_id, id) key."""
    names = [r["name"] for r in migrated_con.execute(
        "SELECT name FROM sqlite_master WHERE type='index'"
        " AND tbl_name='thread_baselines' AND name NOT LIKE 'sqlite_%'")]
    assert names == ["idx_thread_baselines_thread"]


# ===========================================================================
# 4 — THE SCOPE BOUND, AS A TRIPWIRE
# ===========================================================================

def test_the_other_four_memory_fks_still_block_delete(migrated_con):
    """THE SCOPE TRIPWIRE (the pattern of test_nl107_brief_coherence's
    read-side-only pin), born GREEN by construction — it pins what 0027 did NOT
    change. 0027 fixes the BASELINES leg of the triangle — the leg
    the principal ruled on — and NOT the other four FKs that point at
    memory(id) with no cascade. Each of these was measured raising the same
    IntegrityError on 7d878a7, and each still does.

    This is a MEASUREMENT, not an endorsement: a thread carrying a ledger delta,
    a state row, a watch item or a closure is still undeletable, which was true
    before baselines existed (0010 shipped 2026-07-14) and is a separate ruling.
    When that ruling lands, this test goes red and drags the record with it."""
    con = migrated_con
    children = {
        "thread_deltas": ("INSERT INTO thread_deltas (thread_id, edition_date,"
                          " verdict, what_happened, significance)"
                          " VALUES (?, '2026-07-16', 'advances', 'x', 'y')"),
        "thread_state": ("INSERT INTO thread_state (thread_id, as_of_date,"
                         " state_text) VALUES (?, '2026-07-16', 's')"),
        "watch_items": ("INSERT INTO watch_items (thread_id, edition_date,"
                        " observable) VALUES (?, '2026-07-16', 'o')"),
        "thread_closures": ("INSERT INTO thread_closures (thread_id,"
                            " edition_date) VALUES (?, '2026-07-16')"),
    }
    for table, sql in children.items():
        topic = f"blocked by {table}"
        tid = _thread(con, topic)
        with con:
            con.execute(sql, (tid,))
        _dismiss(con, tid)
        with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY"):
            memory.delete_thread(con, topic)
        assert con.execute("SELECT 1 FROM memory WHERE id = ?",
                           (tid,)).fetchone() is not None
