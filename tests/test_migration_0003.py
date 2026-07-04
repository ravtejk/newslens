"""Migration 0003: ranking_runs instrumentation table (ADR-0004 §6).

The ADR calls this table APPEND-ONLY — the day-14 override recalibration
reads fire/no-fire rates over time, which is only trustworthy if history
cannot be rewritten. 0001 set the precedent: append-only is enforced by
triggers (briefings_history), not by intention.

KNOWN-RED: test_BUG5_* — migration 0003 ships NO UPDATE/DELETE triggers on
ranking_runs, so the append-only claim is currently convention, not
structure.
"""

from __future__ import annotations

import sqlite3

import pytest


def _insert_run(con, date="2026-07-04", meta='{"status": "ok"}'):
    con.execute(
        "INSERT INTO ranking_runs (date, meta, token_usage) VALUES (?, ?, NULL)",
        (date, meta),
    )


def test_ranking_runs_accepts_valid_rows_and_defaults_ran_at(migrated_con):
    _insert_run(migrated_con)
    row = migrated_con.execute("SELECT * FROM ranking_runs").fetchone()
    assert row["date"] == "2026-07-04"
    assert row["ran_at"].endswith("Z")
    assert row["token_usage"] is None


def test_ranking_runs_meta_must_be_valid_json(migrated_con):
    with pytest.raises(sqlite3.IntegrityError):
        _insert_run(migrated_con, meta="not json at all")


def test_ranking_runs_token_usage_rejects_non_json(migrated_con):
    with pytest.raises(sqlite3.IntegrityError):
        migrated_con.execute(
            "INSERT INTO ranking_runs (date, meta, token_usage)"
            " VALUES ('2026-07-04', '{}', 'garbage')"
        )


def test_BUG5_ranking_runs_update_must_abort(migrated_con):
    """KNOWN-RED (BUG-5): ADR-0004 §6 declares ranking_runs append-only and
    the day-14 recalibration depends on unrewritten history, but migration
    0003 ships no UPDATE trigger — this UPDATE currently succeeds. Fix shape:
    the same abort-trigger pair 0001 gave briefings_history."""
    _insert_run(migrated_con)
    with pytest.raises(sqlite3.DatabaseError) as excinfo:
        migrated_con.execute("UPDATE ranking_runs SET meta = '{\"rewritten\": true}'")
    assert "append-only" in str(excinfo.value)


def test_BUG5_ranking_runs_delete_must_abort(migrated_con):
    """KNOWN-RED (BUG-5, delete half): same contract, DELETE path."""
    _insert_run(migrated_con)
    with pytest.raises(sqlite3.DatabaseError) as excinfo:
        migrated_con.execute("DELETE FROM ranking_runs")
    assert "append-only" in str(excinfo.value)
