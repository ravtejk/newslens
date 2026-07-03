"""Migration runner contract (spec §E M1; db.py; ADR-0001).

Covers: 0001 applies and creates exactly the four spec §B tables; re-running
migrate is a no-op ("idempotent, safe to re-run"); re-application after a lost
schema_migrations record is harmless (the documented non-atomicity of script
vs. record); the runner applies files in lexicographic order and never records
a failed migration; FK enforcement is on for every db.connect connection.
"""

from __future__ import annotations

import sqlite3

import pytest

from newslens import db

MIGRATION_0001 = "0001_initial_schema.sql"
EXPECTED_TABLES = {"source_items", "briefings", "memory", "briefings_history"}


def _tables(db_path):
    con = db.connect(db_path)
    try:
        return set(db.table_names(con))
    finally:
        con.close()


def test_fresh_migrate_applies_0001_and_creates_exactly_four_tables(tmp_path):
    db_path = tmp_path / "fresh.db"
    ran = db.migrate(db_path=db_path)
    assert ran == [MIGRATION_0001]
    assert _tables(db_path) == EXPECTED_TABLES | {"schema_migrations"}


def test_second_migrate_is_a_noop(tmp_path):
    db_path = tmp_path / "twice.db"
    assert db.migrate(db_path=db_path) == [MIGRATION_0001]
    assert db.migrate(db_path=db_path) == []  # idempotent: nothing re-applied
    assert _tables(db_path) == EXPECTED_TABLES | {"schema_migrations"}


def test_pending_migrations_lifecycle(tmp_path):
    db_path = tmp_path / "pending.db"
    assert db.pending_migrations(db_path=db_path) == [MIGRATION_0001]
    db.migrate(db_path=db_path)
    assert db.pending_migrations(db_path=db_path) == []


def test_reapply_after_lost_record_is_harmless_and_preserves_data(tmp_path):
    """The documented failure seam: the script ran but the schema_migrations
    record was lost (record insert is not atomic with the script). Re-running
    must re-apply harmlessly (IF NOT EXISTS everywhere) without touching data.
    """
    db_path = tmp_path / "lost-record.db"
    db.migrate(db_path=db_path)

    con = db.connect(db_path)
    try:
        con.execute("INSERT INTO briefings (date) VALUES ('2026-07-01')")
        con.execute("DELETE FROM schema_migrations WHERE filename = ?", (MIGRATION_0001,))
        con.commit()
    finally:
        con.close()

    ran = db.migrate(db_path=db_path)  # must not raise "table already exists"
    assert ran == [MIGRATION_0001]

    con = db.connect(db_path)
    try:
        rows = con.execute("SELECT date FROM briefings").fetchall()
    finally:
        con.close()
    assert [r["date"] for r in rows] == ["2026-07-01"]  # re-apply did not drop data


def test_runner_orders_lexicographically_and_never_records_a_failed_migration(tmp_path):
    mdir = tmp_path / "migs"
    mdir.mkdir()
    (mdir / "0001_ok.sql").write_text(
        "BEGIN; CREATE TABLE IF NOT EXISTS a (x); COMMIT;", encoding="utf-8"
    )
    (mdir / "0002_broken.sql").write_text(
        "BEGIN; CREATE TABLE IF NOT EXISTS b (y); THIS IS NOT SQL; COMMIT;",
        encoding="utf-8",
    )
    db_path = tmp_path / "runner.db"

    with pytest.raises(sqlite3.OperationalError):
        db.migrate(db_path=db_path, migrations_dir=mdir)

    con = db.connect(db_path)
    try:
        applied = db.applied_migrations(con)
    finally:
        con.close()
    assert applied == ["0001_ok.sql"]  # failure is loud and NOT half-recorded

    # Fix the broken file: re-run applies only the failed one.
    (mdir / "0002_broken.sql").write_text(
        "BEGIN; CREATE TABLE IF NOT EXISTS b (y); COMMIT;", encoding="utf-8"
    )
    assert db.migrate(db_path=db_path, migrations_dir=mdir) == ["0002_broken.sql"]


def test_migration_files_missing_dir_is_a_loud_error(tmp_path):
    with pytest.raises(FileNotFoundError) as excinfo:
        db.migration_files(tmp_path / "nope")
    assert "migrations directory not found" in str(excinfo.value)


def test_connect_turns_foreign_keys_on(tmp_path):
    con = db.connect(tmp_path / "fk.db")
    try:
        assert con.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    finally:
        con.close()


def test_shipped_migrations_dir_contains_exactly_0001():
    assert [p.name for p in db.migration_files()] == [MIGRATION_0001]
