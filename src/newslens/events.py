"""Consumption events — the day-30 falsifier's raw data (milestone 7).

Server-side capture only (the dispatch's ruling): a briefing page-view is a
'read'; an episode play is a 'listen'. Generation is a different table
entirely (generation_log.jsonl) — the whole point is distinguishing "cron/
command produced it" from "the principal actually came to read it".

Dedup ruling (ADR-0010): reads log EVERY view — raw truth, cheap, and the
metric dedups; listens log at most once per (briefing-date, calendar-day)
because <audio> elements issue Range-request bursts per play. The day-30
metric (trailing-two-week unprompted open days) is flood-immune either way:
it counts DISTINCT days with any event.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Optional


def log_read(con: sqlite3.Connection, briefing_date: str) -> None:
    with con:
        con.execute(
            "INSERT INTO consumption_events (date, kind) VALUES (?, 'read')",
            (briefing_date,),
        )


def log_listen(con: sqlite3.Connection, briefing_date: str) -> bool:
    """At most one listen row per (briefing date, calendar day). Returns
    whether a row was written."""
    with con:
        exists = con.execute(
            "SELECT 1 FROM consumption_events WHERE date = ? AND kind = 'listen'"
            " AND date(occurred_at) = date('now') LIMIT 1",
            (briefing_date,),
        ).fetchone()
        if exists:
            return False
        con.execute(
            "INSERT INTO consumption_events (date, kind) VALUES (?, 'listen')",
            (briefing_date,),
        )
    return True


def trailing_open_days(con: sqlite3.Connection, days: int = 14,
                       now_utc: Optional[datetime] = None) -> int:
    """The day-30 read: distinct calendar days with ANY consumption event in
    the trailing window."""
    now_utc = now_utc or datetime.now(timezone.utc)
    cutoff = (now_utc - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%S")
    row = con.execute(
        "SELECT COUNT(DISTINCT date(occurred_at)) AS c FROM consumption_events"
        " WHERE occurred_at >= ?",
        (cutoff,),
    ).fetchone()
    return row["c"] if row else 0
