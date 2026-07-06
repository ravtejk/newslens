"""consumption_events unit semantics — the day-30 falsifier's data is
money-grade (ADR-0010 §3; migration 0007)."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from newslens import events

NOW = datetime(2026, 7, 5, 12, 0, 0, tzinfo=timezone.utc)


def iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%S.000Z")


def add_event(con, kind, occurred_at, date="2026-07-01"):
    con.execute(
        "INSERT INTO consumption_events (date, kind, occurred_at) VALUES (?, ?, ?)",
        (date, kind, occurred_at),
    )
    con.commit()


def test_0007_shape_kind_is_a_closed_check(migrated_con):
    migrated_con.execute(
        "INSERT INTO consumption_events (date, kind) VALUES ('2026-07-05', 'read')"
    )
    row = migrated_con.execute("SELECT * FROM consumption_events").fetchone()
    assert row["occurred_at"].endswith("Z")  # defaulted, UTC-shaped
    with pytest.raises(sqlite3.IntegrityError):
        migrated_con.execute(
            "INSERT INTO consumption_events (date, kind) VALUES ('2026-07-05', 'view')"
        )


def test_reads_are_raw_listens_dedup_per_briefing_per_day(migrated_con):
    events.log_read(migrated_con, "2026-07-05")
    events.log_read(migrated_con, "2026-07-05")
    assert events.log_listen(migrated_con, "2026-07-05") is True
    assert events.log_listen(migrated_con, "2026-07-05") is False  # same day dedup
    assert events.log_listen(migrated_con, "2026-07-04") is True   # other briefing
    rows = migrated_con.execute(
        "SELECT kind, COUNT(*) AS n FROM consumption_events GROUP BY kind"
    ).fetchall()
    counts = {r["kind"]: r["n"] for r in rows}
    assert counts == {"read": 2, "listen": 2}


def test_listen_dedup_resets_across_calendar_days(migrated_con):
    assert events.log_listen(migrated_con, "2026-07-05") is True
    migrated_con.execute(
        "UPDATE consumption_events SET occurred_at ="
        " datetime(occurred_at, '-1 day')"
    )
    migrated_con.commit()
    assert events.log_listen(migrated_con, "2026-07-05") is True  # new day, new row


def test_trailing_open_days_boundaries(migrated_con):
    assert events.trailing_open_days(migrated_con, now_utc=NOW) == 0  # empty DB
    add_event(migrated_con, "read", iso(NOW - timedelta(days=13)))    # day 13: in
    add_event(migrated_con, "read", iso(NOW - timedelta(days=14)))    # day 14: boundary, in (>=)
    add_event(migrated_con, "read", iso(NOW - timedelta(days=15)))    # day 15: out
    assert events.trailing_open_days(migrated_con, now_utc=NOW) == 2


def test_trailing_open_days_is_flood_immune(migrated_con):
    for _ in range(50):  # a single-day flood of reads + listens
        add_event(migrated_con, "read", iso(NOW - timedelta(days=2)))
    add_event(migrated_con, "listen", iso(NOW - timedelta(days=2)))
    assert events.trailing_open_days(migrated_con, now_utc=NOW) == 1
    add_event(migrated_con, "listen", iso(NOW - timedelta(days=5)))
    assert events.trailing_open_days(migrated_con, now_utc=NOW) == 2


def test_mixed_kinds_on_one_day_count_once(migrated_con):
    add_event(migrated_con, "read", iso(NOW - timedelta(days=1)))
    add_event(migrated_con, "listen", iso(NOW - timedelta(days=1)))
    assert events.trailing_open_days(migrated_con, now_utc=NOW) == 1
