"""Migration 0002: briefings.date format triggers (ADR-0003 §1).

Format is enforced on INSERT and UPDATE via BEFORE triggers (SQLite can't
retrofit a CHECK without a table rebuild). Scope is format-only by design —
calendar validity stays pipeline-code responsibility, and that boundary is
pinned here so a silent change in either direction is visible.
"""

from __future__ import annotations

import sqlite3

import pytest

DATE_ERROR = "briefings.date must be YYYY-MM-DD"


def test_valid_date_inserts_and_updates(migrated_con):
    migrated_con.execute("INSERT INTO briefings (date) VALUES ('2026-07-03')")
    migrated_con.execute(
        "UPDATE briefings SET date = '2026-07-04' WHERE date = '2026-07-03'"
    )
    row = migrated_con.execute("SELECT date FROM briefings").fetchone()
    assert row["date"] == "2026-07-04"


@pytest.mark.parametrize(
    "bad",
    [
        "2026-7-3",                 # missing zero-padding
        "2026-07-3",
        "2026-07-03T06:00:00Z",     # timestamp, not a day
        "2026-07-03 06:00",
        "07/03/2026",
        "2026/07/03",
        "20260703",
        "garbage",
        "",
        "2026-07-03\n",             # trailing junk
    ],
)
def test_bad_date_rejected_on_insert(migrated_con, bad):
    with pytest.raises(sqlite3.DatabaseError) as excinfo:
        migrated_con.execute("INSERT INTO briefings (date) VALUES (?)", (bad,))
    assert DATE_ERROR in str(excinfo.value)


def test_bad_date_rejected_on_update_and_row_unchanged(migrated_con):
    migrated_con.execute("INSERT INTO briefings (date) VALUES ('2026-07-03')")
    with pytest.raises(sqlite3.DatabaseError) as excinfo:
        migrated_con.execute("UPDATE briefings SET date = 'not-a-date'")
    assert DATE_ERROR in str(excinfo.value)
    row = migrated_con.execute("SELECT date FROM briefings").fetchone()
    assert row["date"] == "2026-07-03"


def test_updating_other_columns_does_not_fire_the_date_trigger(migrated_con):
    migrated_con.execute("INSERT INTO briefings (date) VALUES ('2026-07-03')")
    migrated_con.execute(
        "UPDATE briefings SET narrative_text = 'draft' WHERE date = '2026-07-03'"
    )  # trigger is UPDATE OF date — this must not abort


def test_format_only_boundary_calendar_nonsense_is_accepted(migrated_con):
    """PINS THE DOCUMENTED SCOPE: '9999-99-99' matches the GLOB and is
    ACCEPTED — calendar validity (month 13, day 32) is pipeline-code
    responsibility, not schema (migration 0002 header; ADR-0003 §1). If this
    test starts failing, the boundary moved: update the docs with it."""
    migrated_con.execute("INSERT INTO briefings (date) VALUES ('9999-99-99')")


def test_m1_protections_survive_0002(migrated_con):
    """0002 must not disturb 0001's constraints: UNIQUE(date), json_valid,
    and briefings_history append-only all still enforce."""
    migrated_con.execute("INSERT INTO briefings (date) VALUES ('2026-07-03')")
    with pytest.raises(sqlite3.IntegrityError):
        migrated_con.execute("INSERT INTO briefings (date) VALUES ('2026-07-03')")
    with pytest.raises(sqlite3.IntegrityError):
        migrated_con.execute(
            "INSERT INTO briefings (date, story_slots) VALUES ('2026-07-05', 'not json')"
        )
    bid = migrated_con.execute("SELECT id FROM briefings").fetchone()["id"]
    migrated_con.execute(
        "INSERT INTO briefings_history (briefing_id, date) VALUES (?, '2026-07-03')",
        (bid,),
    )
    with pytest.raises(sqlite3.DatabaseError) as excinfo:
        migrated_con.execute("DELETE FROM briefings_history")
    assert "append-only" in str(excinfo.value)
