"""NL-75 view-event helpers (Data council 2026-07-16; migration 0011). The
thread_view / deep_view instrument with its referrer split. Born-red: these
helpers do not exist on 9c3078b.
"""

from __future__ import annotations

import sqlite3

import pytest

from newslens import events


def test_log_thread_view_records_target_and_referrer(migrated_con):
    events.log_thread_view(migrated_con, "2026-07-14", "Strait of Hormuz", "today")
    row = migrated_con.execute(
        "SELECT kind, target, referrer FROM consumption_events").fetchone()
    assert (row["kind"], row["target"], row["referrer"]) == (
        "thread_view", "Strait of Hormuz", "today")


def test_log_deep_view_records_target_and_referrer(migrated_con):
    events.log_deep_view(migrated_con, "2026-07-14", "story-1", "archive")
    row = migrated_con.execute(
        "SELECT kind, target, referrer FROM consumption_events").fetchone()
    assert (row["kind"], row["target"], row["referrer"]) == (
        "deep_view", "story-1", "archive")


def test_referrer_is_optional():
    """A view with no known origin surface still logs (referrer NULL)."""
    from newslens import db
    con = db.connect(":memory:")  # in-memory: exercise the NULL path directly
    # not migrated; build the table shape 0011 ships
    con.execute("CREATE TABLE consumption_events (id INTEGER PRIMARY KEY, date TEXT,"
                " kind TEXT, target TEXT, referrer TEXT)")
    events.log_thread_view(con, "2026-07-14", "T", None)
    assert con.execute("SELECT referrer FROM consumption_events").fetchone()[0] is None
    con.close()


def test_view_referrer_check_rejects_unknown_surface(migrated_con):
    with pytest.raises(sqlite3.IntegrityError):
        events.log_thread_view(migrated_con, "2026-07-14", "T", "gmail")


def test_the_referrer_menu_is_the_documented_split():
    assert events.VIEW_REFERRERS == ("today", "following", "archive")
