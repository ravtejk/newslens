"""Migration 0013: watch_items — the expiry register (NL-75; Content council
2026-07-16, Forward-Claim Rules item 2). CHECKPOINT-flagged (third migration).

Contract: an 'open' watch-for persists (observable, due-date when parseable);
a conversion is a NEW row (resolved|unanswered|superseded) that `converts` the
open one; the kind CHECK is closed; the register is append-only.
"""

from __future__ import annotations

import sqlite3

import pytest


def _thread(con, topic="Strait of Hormuz"):
    con.execute("INSERT INTO memory (topic, status) VALUES (?, 'active')", (topic,))
    return con.execute("SELECT id FROM memory WHERE topic = ?", (topic,)).fetchone()["id"]


def test_open_watch_item_persists_with_optional_due_date(migrated_con):
    tid = _thread(migrated_con)
    migrated_con.execute(
        "INSERT INTO watch_items (thread_id, slot, edition_date, kind, observable, due_date)"
        " VALUES (?, 1, '2026-07-10', 'open', 'Switzerland talks on July 12 will indicate"
        " whether diplomacy holds', '2026-07-12')", (tid,))
    migrated_con.commit()
    row = migrated_con.execute("SELECT * FROM watch_items").fetchone()
    assert row["kind"] == "open"
    assert row["due_date"] == "2026-07-12"
    assert "Switzerland" in row["observable"]


def test_dateless_open_watch_item_allows_null_due_date(migrated_con):
    tid = _thread(migrated_con)
    migrated_con.execute(
        "INSERT INTO watch_items (thread_id, edition_date, kind, observable, due_date)"
        " VALUES (?, '2026-07-10', 'open', 'whether the blockade holds', NULL)", (tid,))
    migrated_con.commit()
    assert migrated_con.execute(
        "SELECT due_date FROM watch_items").fetchone()["due_date"] is None


def test_conversion_is_a_new_row_pointing_at_the_open_item(migrated_con):
    tid = _thread(migrated_con)
    migrated_con.execute(
        "INSERT INTO watch_items (thread_id, edition_date, kind, observable, due_date)"
        " VALUES (?, '2026-07-10', 'open', 'Switzerland talks July 12', '2026-07-12')", (tid,))
    open_id = migrated_con.execute("SELECT id FROM watch_items").fetchone()["id"]
    migrated_con.execute(
        "INSERT INTO watch_items (thread_id, edition_date, kind, observable, converts)"
        " VALUES (?, '2026-07-14', 'unanswered', 'none of today''s outlets mention the talks', ?)",
        (tid, open_id))
    migrated_con.commit()
    conv = migrated_con.execute(
        "SELECT kind, converts FROM watch_items WHERE kind != 'open'").fetchone()
    assert conv["kind"] == "unanswered"
    assert conv["converts"] == open_id


def test_kind_check_is_closed(migrated_con):
    tid = _thread(migrated_con)
    with pytest.raises(sqlite3.IntegrityError):
        migrated_con.execute(
            "INSERT INTO watch_items (thread_id, edition_date, kind, observable)"
            " VALUES (?, '2026-07-10', 'pending', 'x')", (tid,))


def test_watch_items_is_append_only(migrated_con):
    tid = _thread(migrated_con)
    migrated_con.execute(
        "INSERT INTO watch_items (thread_id, edition_date, kind, observable)"
        " VALUES (?, '2026-07-10', 'open', 'x')", (tid,))
    migrated_con.commit()
    with pytest.raises(sqlite3.DatabaseError) as up:
        migrated_con.execute("UPDATE watch_items SET kind = 'resolved'")
    assert "append-only" in str(up.value)
    with pytest.raises(sqlite3.DatabaseError) as dl:
        migrated_con.execute("DELETE FROM watch_items")
    assert "append-only" in str(dl.value)


def test_reapply_after_lost_record_is_harmless(tmp_path):
    from newslens import db
    db_path = tmp_path / "reapply.db"
    db.migrate(db_path=db_path)
    con = db.connect(db_path)
    try:
        con.execute("DELETE FROM schema_migrations WHERE filename = '0013_watch_items.sql'")
        con.commit()
    finally:
        con.close()
    ran = db.migrate(db_path=db_path)
    assert ran == ["0013_watch_items.sql"]
