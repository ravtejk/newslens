"""Migration 0011: consumption_events gains thread_view/deep_view + target +
referrer (NL-75; Data council 2026-07-16; principal checkpoint C(i)).

Contract: the two new kinds are insertable with a target and a referrer; the
kind and referrer CHECKs stay closed; read/listen keep working with NULL
target/referrer; the rebuild preserves pre-existing rows; re-apply in the
lost-record gap is harmless and non-destructive.
"""

from __future__ import annotations

import shutil
import sqlite3

import pytest

from newslens import db, paths

BEFORE_0011 = [f"{i:04d}" for i in range(1, 11)]  # 0001..0010


def _dir_through(tmp_path, prefixes):
    """A migrations dir holding only the migration files whose 4-digit prefix
    is in `prefixes` (real shipped files, copied)."""
    mdir = tmp_path / "migs"
    mdir.mkdir(exist_ok=True)
    for p in paths.MIGRATIONS_DIR.glob("*.sql"):
        if p.name[:4] in prefixes:
            shutil.copy(p, mdir / p.name)
    return mdir


def test_new_kinds_accept_target_and_referrer(migrated_con):
    migrated_con.execute(
        "INSERT INTO consumption_events (date, kind, target, referrer)"
        " VALUES ('2026-07-14', 'thread_view', 'Strait of Hormuz', 'today')")
    migrated_con.execute(
        "INSERT INTO consumption_events (date, kind, target, referrer)"
        " VALUES ('2026-07-14', 'deep_view', 'story-1', 'archive')")
    rows = migrated_con.execute(
        "SELECT kind, target, referrer FROM consumption_events"
        " ORDER BY id").fetchall()
    assert [(r["kind"], r["target"], r["referrer"]) for r in rows] == [
        ("thread_view", "Strait of Hormuz", "today"),
        ("deep_view", "story-1", "archive"),
    ]


def test_read_and_listen_still_work_with_null_target_referrer(migrated_con):
    migrated_con.execute(
        "INSERT INTO consumption_events (date, kind) VALUES ('2026-07-14', 'read')")
    row = migrated_con.execute(
        "SELECT kind, target, referrer FROM consumption_events").fetchone()
    assert (row["kind"], row["target"], row["referrer"]) == ("read", None, None)


def test_kind_check_stays_closed(migrated_con):
    with pytest.raises(sqlite3.IntegrityError):
        migrated_con.execute(
            "INSERT INTO consumption_events (date, kind) VALUES ('2026-07-14', 'view')")


def test_referrer_check_rejects_unknown_surface(migrated_con):
    with pytest.raises(sqlite3.IntegrityError):
        migrated_con.execute(
            "INSERT INTO consumption_events (date, kind, referrer)"
            " VALUES ('2026-07-14', 'thread_view', 'gmail')")


def test_rebuild_preserves_preexisting_read_and_listen_rows(tmp_path):
    """Real-DB-shaped: rows written under the 0007 schema survive the 0011
    rebuild with their id/date/kind/occurred_at intact."""
    db_path = tmp_path / "rebuild.db"
    pre = _dir_through(tmp_path, BEFORE_0011)
    db.migrate(db_path=db_path, migrations_dir=pre)
    con = db.connect(db_path)
    try:
        con.execute("INSERT INTO consumption_events (date, kind, occurred_at)"
                    " VALUES ('2026-07-05', 'read', '2026-07-05T09:00:00.000Z')")
        con.execute("INSERT INTO consumption_events (date, kind, occurred_at)"
                    " VALUES ('2026-07-05', 'listen', '2026-07-05T10:00:00.000Z')")
        con.commit()
    finally:
        con.close()

    full = _dir_through(tmp_path, BEFORE_0011 + ["0011"])
    ran = db.migrate(db_path=db_path, migrations_dir=full)
    assert ran == ["0011_consumption_view_events.sql"]
    con = db.connect(db_path)
    try:
        rows = con.execute("SELECT date, kind, occurred_at FROM consumption_events"
                           " ORDER BY id").fetchall()
    finally:
        con.close()
    assert [(r["date"], r["kind"], r["occurred_at"]) for r in rows] == [
        ("2026-07-05", "read", "2026-07-05T09:00:00.000Z"),
        ("2026-07-05", "listen", "2026-07-05T10:00:00.000Z"),
    ]


def test_reapply_after_lost_record_is_harmless_and_preserves_rows(tmp_path):
    db_path = tmp_path / "reapply.db"
    db.migrate(db_path=db_path)
    con = db.connect(db_path)
    try:
        con.execute("INSERT INTO consumption_events (date, kind, target, referrer)"
                    " VALUES ('2026-07-14', 'read', NULL, NULL)")
        con.execute("DELETE FROM schema_migrations WHERE filename ="
                    " '0011_consumption_view_events.sql'")
        con.commit()
    finally:
        con.close()
    ran = db.migrate(db_path=db_path)  # must not raise
    assert ran == ["0011_consumption_view_events.sql"]
    con = db.connect(db_path)
    try:
        rows = con.execute("SELECT date, kind FROM consumption_events").fetchall()
    finally:
        con.close()
    assert [(r["date"], r["kind"]) for r in rows] == [("2026-07-14", "read")]
