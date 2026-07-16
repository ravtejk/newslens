"""The collect-now schemas (substrate ruling C, principal-approved 2026-07-16).

Three items, staged to collect data ahead of the features that consume it:

  (a) FETCH CLOCKS on analyst externals — NL-74's chronology work needs a fetch
      clock on every externally-fetched source. This LOCKS that the retrieval
      manifest already stamps one (analysis_retrieval.retrieved_at) — the clock
      exists TODAY; the test keeps it from silently disappearing. No migration.

  (b) CLOSURE RECORDS — a minimal thread_closures shape + the CLI verb
      `memory close <topic> --reason` as the explicit-action lane (§F). The
      closure FEATURE (render, stop-generating) is a backlog row; SCHEMA + verb
      ship now.

  (c) EXPLAINED-ONCE REGISTRY — concept_explanations SCHEMA ONLY; NL-77's
      backgrounder writes it later.

RED-first, per team/ENGINEERING.md.
"""

from __future__ import annotations

import sqlite3

import pytest

from newslens import analysis, cli, db, memory_core


def _cols(con, table):
    return {r["name"] for r in con.execute(f"PRAGMA table_info({table})")}


# ===========================================================================
# (a) fetch clocks — the retrieval manifest stamps one on every external source
# ===========================================================================

def test_retrieval_manifest_stamps_a_fetch_clock_on_externals():
    """build_source_map records a fetch clock (retrieved_at) on the externally-
    fetched S# (full-text) and R# (Sonar) sources — NL-74's chronology input."""
    fr = analysis.FetchRecord(
        url="https://example.com/a", source_name="Outlet A", tier="lead",
        outcome=analysis.OK, chars=100, title="A", text="body text")
    sonar = [{"url": "https://example.com/r", "title": "R", "snippet": "snip"}]
    sources = analysis.build_source_map([fr], [], sonar, [])
    assert sources["S1"]["retrieved_at"], "S# external missing a fetch clock"
    assert sources["R1"]["retrieved_at"], "R# external missing a fetch clock"
    # a plausible ISO-ish stamp, not a placeholder
    assert sources["S1"]["retrieved_at"].startswith("20")


def test_analysis_retrieval_persists_the_fetch_clock_column(migrated_con):
    assert "retrieved_at" in _cols(migrated_con, "analysis_retrieval")


# ===========================================================================
# (b) closure records — schema + the explicit-action verb
# ===========================================================================

def test_thread_closures_schema_shape_and_append_only(migrated_con):
    con = migrated_con
    cols = _cols(con, "thread_closures")
    assert {"thread_id", "closed_at", "reason", "edition_date"} <= cols
    con.execute(
        "INSERT INTO memory (topic, status, status_changed_at, created_at,"
        " updated_at) VALUES ('T', 'active', '', '', '')")
    con.execute(
        "INSERT INTO thread_closures (thread_id, reason, edition_date)"
        " VALUES (1, 'ended', '2026-07-16')")
    con.commit()
    with pytest.raises(sqlite3.DatabaseError) as e1:
        con.execute("UPDATE thread_closures SET reason='x' WHERE thread_id=1")
    assert "append-only" in str(e1.value)
    with pytest.raises(sqlite3.DatabaseError):
        con.execute("DELETE FROM thread_closures WHERE thread_id=1")


def _seed_thread(con, topic="Iran War"):
    now = "2026-07-01T00:00:00.000Z"
    cur = con.execute(
        "INSERT INTO memory (topic, status, status_changed_at, created_at,"
        " updated_at) VALUES (?, 'active', ?, ?, ?)", (topic, now, now, now))
    con.commit()
    return cur.lastrowid


def test_close_thread_records_the_closure(migrated_con):
    con = migrated_con
    tid = _seed_thread(con, "Iran War")
    ok, msg, cid = memory_core.close_thread(
        con, "Iran War", "the story ended", "2026-07-16")
    assert ok is True and cid is not None
    row = con.execute("SELECT * FROM thread_closures WHERE id=?", (cid,)).fetchone()
    assert row["thread_id"] == tid
    assert row["reason"] == "the story ended"
    assert row["edition_date"] == "2026-07-16"


def test_close_thread_refuses_unknown_topic(migrated_con):
    con = migrated_con
    ok, msg, cid = memory_core.close_thread(con, "Nonexistent", "x", "2026-07-16")
    assert ok is False and cid is None
    assert con.execute("SELECT COUNT(*) c FROM thread_closures").fetchone()["c"] == 0


def test_close_thread_refuses_a_second_closure(migrated_con):
    con = migrated_con
    _seed_thread(con, "Iran War")
    memory_core.close_thread(con, "Iran War", "ended", "2026-07-16")
    ok, msg, cid = memory_core.close_thread(con, "Iran War", "again", "2026-07-17")
    assert ok is False
    assert "already closed" in msg.lower()
    assert con.execute("SELECT COUNT(*) c FROM thread_closures").fetchone()["c"] == 1


def test_memory_close_cli_writes_a_closure(tmp_paths, capsys):
    cli.main(["migrate"])
    cli.main(["memory", "add", "Iran War"])
    capsys.readouterr()
    rc = cli.main(["memory", "close", "Iran War", "--reason", "story concluded"])
    out = capsys.readouterr().out
    assert rc == 0
    con = db.connect()
    try:
        row = con.execute(
            "SELECT reason FROM thread_closures c JOIN memory m ON m.id=c.thread_id"
            " WHERE m.topic='Iran War'").fetchone()
    finally:
        con.close()
    assert row is not None and row["reason"] == "story concluded"


# ===========================================================================
# (c) explained-once registry — schema only (NL-77 writes it later)
# ===========================================================================

def test_concept_explanations_schema_shape(migrated_con):
    con = migrated_con
    cols = _cols(con, "concept_explanations")
    assert {"concept", "first_explained_edition", "brief_id"} <= cols
    # explained-ONCE: concept is unique; a duplicate INSERT is rejected
    con.execute(
        "INSERT INTO concept_explanations (concept, first_explained_edition)"
        " VALUES ('the strait', '2026-07-16')")
    con.commit()
    with pytest.raises(sqlite3.DatabaseError):
        con.execute(
            "INSERT INTO concept_explanations (concept, first_explained_edition)"
            " VALUES ('the strait', '2026-07-17')")


def test_concept_explanations_is_append_only(migrated_con):
    con = migrated_con
    con.execute(
        "INSERT INTO concept_explanations (concept, first_explained_edition)"
        " VALUES ('c', '2026-07-16')")
    con.commit()
    with pytest.raises(sqlite3.DatabaseError) as e:
        con.execute("UPDATE concept_explanations SET concept='d' WHERE concept='c'")
    assert "append-only" in str(e.value)
