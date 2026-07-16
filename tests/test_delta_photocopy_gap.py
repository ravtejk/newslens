"""The delta-7 photocopy gap (Sten's audit find, Content council 2026-07-16).

Delta 7's significance clause photocopied a prior delta's significance nearly
verbatim ("The conflict has ESCALATED FROM ..." vs "The conflict has MOVED
BEYOND ..."), and the anti-photocopier law — which governs STATE (regen from
ledger, never from prior state text) — has NO delta-level check.

The honest shape (never rewrite model output silently): WARN + write-as-is + a
`photocopy_suspect` note in the report/meta (visible to diagnose), leaving
supersession/repair to NL-73's machinery. Detection is deterministic (normalized
token overlap, no LLM); the threshold is disclosed.

RED-first, grounded in the REAL delta-5-vs-7 shape from the record.
"""

from __future__ import annotations

import json

import pytest

from newslens import memory_core


# The real record (thread 10, read 2026-07-16, mode=ro):
SIG_5 = ("The conflict has escalated from economic disputes to direct military "
         "confrontation, affecting global oil supply and regional stability.")
SIG_7 = ("The conflict has moved beyond economic disputes to direct military "
         "confrontation, affecting global oil supply and regional stability.")
# delta 3 — genuinely different significance on the same thread:
SIG_3 = ("The dispute stopped being about the price of passage and became a war "
         "over passage itself — no longer contained to the strait, reaching "
         "three third countries.")


def _seed_thread(con, topic="Iran War"):
    now = "2026-07-01T00:00:00.000Z"
    cur = con.execute(
        "INSERT INTO memory (topic, status, status_changed_at, created_at,"
        " updated_at) VALUES (?, 'active', ?, ?, ?)", (topic, now, now, now))
    con.commit()
    return cur.lastrowid


def _delta(con, tid, date, signif, what="An event.", slot=1):
    cur = con.execute(
        "INSERT INTO thread_deltas (thread_id, edition_date, slot, verdict,"
        " what_happened, significance, cites_json)"
        " VALUES (?, ?, ?, 'advances', ?, ?, '[\"S1\"]')",
        (tid, date, slot, what, signif))
    con.commit()
    return cur.lastrowid


def _brief(what, signif, cites=("S1",)):
    return {"brief": {"arc": {"delta": "advances", "what_happened": what,
                              "significance": signif, "cites": list(cites)}}}


# ===========================================================================
# the deterministic detector — the real shape trips, different does not
# ===========================================================================

def test_detector_trips_on_the_real_delta7_shape(migrated_con):
    con = migrated_con
    tid = _seed_thread(con)
    _delta(con, tid, "2026-07-14", SIG_5)                # delta 5 on file
    hit = memory_core.photocopy_suspect_significance(
        con, tid, SIG_7, before_date="2026-07-16")       # delta 7 being written
    assert hit is not None
    assert hit["edition_date"] == "2026-07-14"
    assert hit["score"] >= memory_core.PHOTOCOPY_SIGNIFICANCE_JACCARD


def test_detector_ignores_a_genuinely_different_significance(migrated_con):
    con = migrated_con
    tid = _seed_thread(con)
    _delta(con, tid, "2026-07-14", SIG_5)
    assert memory_core.photocopy_suspect_significance(
        con, tid, SIG_3, before_date="2026-07-16") is None


def test_detector_ignores_same_day_deltas(migrated_con):
    """A sanctioned same-day split (two deltas the SAME date) must not read as a
    photocopy of each other — the check compares only STRICTLY-EARLIER deltas."""
    con = migrated_con
    tid = _seed_thread(con)
    _delta(con, tid, "2026-07-16", SIG_5, slot=1)
    # a near-identical significance the SAME day is not a 'copy of the past'
    assert memory_core.photocopy_suspect_significance(
        con, tid, SIG_7, before_date="2026-07-16") is None


def test_threshold_constant_is_disclosed():
    # the threshold is a named, inspectable constant (disclose-the-threshold)
    assert 0.0 < memory_core.PHOTOCOPY_SIGNIFICANCE_JACCARD <= 1.0


# ===========================================================================
# write path — WARN + write-as-is + a photocopy_suspect note (never a rewrite)
# ===========================================================================

def test_write_flags_photocopy_but_writes_the_delta_as_is(migrated_con):
    con = migrated_con
    tid = _seed_thread(con, "Iran War")
    _delta(con, tid, "2026-07-14", SIG_5)                # the prior delta
    con.execute("INSERT INTO briefings (date, story_slots) VALUES (?, '[]')",
                ("2026-07-16",))
    con.commit()
    slots = [{"slot": "1", "matched_memory": ["Iran War"]}]
    briefs = {1: _brief("New strikes reported.", SIG_7)}

    rep = memory_core.write_deltas_for_edition(con, "2026-07-16", None, briefs, slots)
    # the delta is STILL written (never dropped, never rewritten)
    assert len(rep.written) == 1
    row = con.execute("SELECT significance FROM thread_deltas WHERE thread_id=?"
                      " AND edition_date='2026-07-16'", (tid,)).fetchone()
    assert row["significance"] == SIG_7                  # model output is UNTOUCHED
    # and the photocopy is FLAGGED in the report/meta
    assert rep.photocopy_suspects, "expected a photocopy_suspect note"
    sus = rep.photocopy_suspects[0]
    assert sus["thread"] == "Iran War"
    assert sus["against_edition"] == "2026-07-14"


def test_write_does_not_flag_a_distinct_significance(migrated_con):
    con = migrated_con
    tid = _seed_thread(con, "Iran War")
    _delta(con, tid, "2026-07-14", SIG_5)
    con.execute("INSERT INTO briefings (date, story_slots) VALUES (?, '[]')",
                ("2026-07-16",))
    con.commit()
    slots = [{"slot": "1", "matched_memory": ["Iran War"]}]
    briefs = {1: _brief("A distinct development.", SIG_3)}
    rep = memory_core.write_deltas_for_edition(con, "2026-07-16", None, briefs, slots)
    assert len(rep.written) == 1
    assert rep.photocopy_suspects == []
