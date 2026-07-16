"""NL-72 — the backfill newer-activity guard (gate chip, loop #5).

`memory-backfill` on an edition OLDER than a thread's existing activity would
build state from FUTURE-DATED ledger entries, stamp it with the older
as_of_date, and poison BUG-30's strict prior-coverage reads (a state stamped
07-10 holding 07-14 knowledge leaks into historical renders). The guard:
REFUSE (with a named reason) when any thread the pass WOULD MOVE already carries
thread_deltas.edition_date > target OR thread_state.as_of_date > target; --force
overrides with a disclosed warning.

RED-first, per team/ENGINEERING.md: these pin the refusal contract and the
force override; the sanctioned newest-edition use stays unrefused.
"""

from __future__ import annotations

import json

import pytest

from newslens import generate, memory_core
from test_generate import ENV, seed_briefing, slot  # noqa: F401


OLD = "2026-07-10"     # the backfill target (older than the newer activity)
NEW = "2026-07-14"     # future-dated activity already on the thread


def _seed_thread(con, topic="Iran War"):
    now = "2026-07-01T00:00:00.000Z"
    cur = con.execute(
        "INSERT INTO memory (topic, status, status_changed_at, created_at,"
        " updated_at) VALUES (?, 'active', ?, ?, ?)", (topic, now, now, now))
    con.commit()
    return cur.lastrowid


def _write_delta(con, tid, date, what="A development.", signif="Changed it."):
    con.execute(
        "INSERT INTO thread_deltas (thread_id, edition_date, verdict,"
        " what_happened, significance, cites_json)"
        " VALUES (?, ?, 'advances', ?, ?, '[\"S1\"]')", (tid, date, what, signif))
    con.commit()


def _write_state(con, tid, as_of, text="It stands here (Jul 14)."):
    con.execute(
        "INSERT INTO thread_state (thread_id, as_of_date, state_text)"
        " VALUES (?, ?, ?)", (tid, as_of, text))
    con.commit()


def _advancing_arc():
    return {"delta": "advances",
            "what_happened": "The U.S. reinstated the naval blockade.",
            "significance": "The dispute escalated from tolls to a shooting war.",
            "cites": ["S2", "S4"]}


def _seed_valid_brief(con, date, slot_n, arc=None):
    con.execute(
        "INSERT INTO analysis_briefs (date, slot, tier, status, brief_json,"
        " model, cost_usd) VALUES (?, ?, 'full', 'valid', ?, 'gpt-4o', 0.0)",
        (date, slot_n, json.dumps({"brief": {"arc": arc or _advancing_arc()}})))
    con.commit()


def _state_chat(date, cost=0.01):
    hd = memory_core.human_date(date)

    def chat(key, prompt):
        return ({"state": f"It is a war now ({hd})."}, cost)
    return chat


def _setup_old_edition(con, tid):
    """A published OLD edition with a valid brief matched to the thread — a
    legitimate backfill target on its own."""
    seed_briefing(con, OLD, [slot(1, mem=["Iran War"])],
                  narrative="Published OLD edition of record.")
    _seed_valid_brief(con, OLD, 1)


# --- the guard fires on a newer DELTA ---------------------------------------

def test_backfill_refuses_when_a_thread_has_a_newer_delta(migrated_con):
    con = migrated_con
    tid = _seed_thread(con, "Iran War")
    _write_delta(con, tid, NEW)                 # future-dated activity on file
    _setup_old_edition(con, tid)

    rep = generate.run_memory_backfill(OLD, con=con, env=ENV,
                                       state_chat=_state_chat(OLD))
    assert rep.refused is True
    # named reason: the thread + the newer date + the poison it prevents
    assert "Iran War" in rep.reason
    assert NEW in rep.reason
    assert "--force" in rep.reason
    # nothing was written for the older target
    assert con.execute("SELECT COUNT(*) c FROM thread_deltas WHERE thread_id=?"
                       " AND edition_date=?", (tid, OLD)).fetchone()["c"] == 0
    assert con.execute("SELECT COUNT(*) c FROM thread_state WHERE thread_id=?",
                       (tid,)).fetchone()["c"] == 0


# --- the guard fires on a newer STATE too -----------------------------------

def test_backfill_refuses_when_a_thread_has_a_newer_state(migrated_con):
    con = migrated_con
    tid = _seed_thread(con, "Iran War")
    _write_delta(con, tid, "2026-07-01")        # only OLD ledger activity
    _write_state(con, tid, NEW)                  # but a FUTURE-dated state
    _setup_old_edition(con, tid)

    rep = generate.run_memory_backfill(OLD, con=con, env=ENV,
                                       state_chat=_state_chat(OLD))
    assert rep.refused is True
    assert NEW in rep.reason


# --- --force overrides, with a disclosed warning ----------------------------

def test_force_overrides_the_guard_with_a_disclosed_warning(migrated_con):
    con = migrated_con
    tid = _seed_thread(con, "Iran War")
    _write_delta(con, tid, NEW)
    _setup_old_edition(con, tid)

    rep = generate.run_memory_backfill(OLD, con=con, env=ENV, force=True,
                                       state_chat=_state_chat(OLD))
    assert rep.refused is False
    # the override is DISCLOSED (never silent)
    assert any("force" in w.lower() and NEW in w for w in rep.warnings), rep.warnings
    # and it actually proceeded — the OLD delta landed
    assert con.execute("SELECT COUNT(*) c FROM thread_deltas WHERE thread_id=?"
                       " AND edition_date=?", (tid, OLD)).fetchone()["c"] == 1


# --- the sanctioned newest-edition use is NEVER refused ---------------------

def test_newest_edition_backfill_is_not_refused(migrated_con):
    """No thread carries activity newer than the target — the safe, sanctioned
    case the guard must leave alone."""
    con = migrated_con
    tid = _seed_thread(con, "Iran War")
    _write_delta(con, tid, "2026-07-01")
    seed_briefing(con, NEW, [slot(1, mem=["Iran War"])],
                  narrative="Published NEWEST edition of record.")
    _seed_valid_brief(con, NEW, 1)

    rep = generate.run_memory_backfill(NEW, con=con, env=ENV,
                                       state_chat=_state_chat(NEW))
    assert rep.refused is False
    assert con.execute("SELECT COUNT(*) c FROM thread_deltas WHERE thread_id=?"
                       " AND edition_date=?", (tid, NEW)).fetchone()["c"] == 1


# --- a merely-matches arc does not trip the guard (no delta would be written) -

def test_non_moving_arc_does_not_trip_the_guard(migrated_con):
    con = migrated_con
    tid = _seed_thread(con, "Iran War")
    _write_delta(con, tid, NEW)
    seed_briefing(con, OLD, [slot(1, mem=["Iran War"])],
                  narrative="Published OLD edition of record.")
    # arc that does NOT move the ledger — the pass would move no thread
    _seed_valid_brief(con, OLD, 1, arc={"delta": "merely-matches",
                                        "what_happened": "Restated context.",
                                        "significance": "", "cites": ["S1"]})
    rep = generate.run_memory_backfill(OLD, con=con, env=ENV,
                                       state_chat=_state_chat(OLD))
    assert rep.refused is False
