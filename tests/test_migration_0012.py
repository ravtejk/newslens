"""Migration 0012: thread_delta_supersessions — machine-readable supersession
(NL-75; Rook's gate; principal checkpoint C(ii)).

Contract: a supersession links one delta to a later one; a delta is superseded
at most once (PK); self-supersession is refused; the side table is append-only;
0010's thread_deltas append-only triggers are UNTOUCHED (content still
immutable). The read-side effects (exclude from state regen, struck in
timelines) live in test_nl75_memory.py (and the render-strike acceptance
contracts RED-1/RED-2 in test_nl75_qa.py).
"""

from __future__ import annotations

import sqlite3

import pytest


def _thread(con, topic="Strait of Hormuz"):
    con.execute("INSERT INTO memory (topic, status) VALUES (?, 'active')", (topic,))
    return con.execute("SELECT id FROM memory WHERE topic = ?", (topic,)).fetchone()["id"]


def _delta(con, tid, date, what, slot=1, signif="sig"):
    con.execute(
        "INSERT INTO thread_deltas (thread_id, edition_date, slot, verdict,"
        " what_happened, significance, cites_json) VALUES (?, ?, ?, 'advances', ?, ?, '[\"S1\"]')",
        (tid, date, slot, what, signif))
    return con.execute("SELECT id FROM thread_deltas ORDER BY id DESC LIMIT 1").fetchone()["id"]


def test_supersession_links_a_delta_to_a_later_one(migrated_con):
    tid = _thread(migrated_con)
    a = _delta(migrated_con, tid, "2026-07-05", "fee dispute")
    b = _delta(migrated_con, tid, "2026-07-14", "blockade")
    migrated_con.execute(
        "INSERT INTO thread_delta_supersessions (delta_id, superseded_by, reason)"
        " VALUES (?, ?, 'corrected by the 07-14 entry')", (a, b))
    migrated_con.commit()
    row = migrated_con.execute(
        "SELECT superseded_by, reason FROM thread_delta_supersessions WHERE delta_id = ?",
        (a,)).fetchone()
    assert row["superseded_by"] == b
    assert "corrected" in row["reason"]


def test_a_delta_is_superseded_at_most_once(migrated_con):
    tid = _thread(migrated_con)
    a = _delta(migrated_con, tid, "2026-07-05", "fee dispute")
    b = _delta(migrated_con, tid, "2026-07-10", "closure")
    c = _delta(migrated_con, tid, "2026-07-14", "blockade")
    migrated_con.execute(
        "INSERT INTO thread_delta_supersessions (delta_id, superseded_by) VALUES (?, ?)", (a, b))
    migrated_con.commit()
    with pytest.raises(sqlite3.IntegrityError):  # delta_id is the PK
        migrated_con.execute(
            "INSERT INTO thread_delta_supersessions (delta_id, superseded_by) VALUES (?, ?)", (a, c))


def test_a_delta_cannot_supersede_itself(migrated_con):
    tid = _thread(migrated_con)
    a = _delta(migrated_con, tid, "2026-07-05", "fee dispute")
    with pytest.raises(sqlite3.IntegrityError):
        migrated_con.execute(
            "INSERT INTO thread_delta_supersessions (delta_id, superseded_by) VALUES (?, ?)", (a, a))


def test_supersession_is_append_only(migrated_con):
    tid = _thread(migrated_con)
    a = _delta(migrated_con, tid, "2026-07-05", "fee dispute")
    b = _delta(migrated_con, tid, "2026-07-14", "blockade")
    migrated_con.execute(
        "INSERT INTO thread_delta_supersessions (delta_id, superseded_by) VALUES (?, ?)", (a, b))
    migrated_con.commit()
    with pytest.raises(sqlite3.DatabaseError) as up:
        migrated_con.execute("UPDATE thread_delta_supersessions SET reason = 'x'")
    assert "append-only" in str(up.value)
    with pytest.raises(sqlite3.DatabaseError) as dl:
        migrated_con.execute("DELETE FROM thread_delta_supersessions")
    assert "append-only" in str(dl.value)


def test_ledger_content_stays_immutable_after_0012(migrated_con):
    """0012 must NOT relax 0010: thread_deltas content is still un-rewritable."""
    tid = _thread(migrated_con)
    _delta(migrated_con, tid, "2026-07-05", "fee dispute")
    with pytest.raises(sqlite3.DatabaseError) as exc:
        migrated_con.execute("UPDATE thread_deltas SET what_happened = 'rewritten'")
    assert "append-only" in str(exc.value)


def test_reapply_after_lost_record_is_harmless(tmp_path):
    from newslens import db
    db_path = tmp_path / "reapply.db"
    db.migrate(db_path=db_path)
    con = db.connect(db_path)
    try:
        con.execute("DELETE FROM schema_migrations WHERE filename ="
                    " '0012_thread_delta_supersession.sql'")
        con.commit()
    finally:
        con.close()
    ran = db.migrate(db_path=db_path)  # CREATE ... IF NOT EXISTS — no raise
    assert ran == ["0012_thread_delta_supersession.sql"]
