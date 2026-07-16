"""NL-73 — the state-repair rung (gate chip, loop #5).

Under the fixed moved_thread_ids semantics a FAILED state rewrite self-heals
ONLY on the thread's next real move. The repair is the targeted healing:
`newslens memory-repair-state [--thread-id N | --all]` rewrites state for threads
whose latest LIVE delta postdates their latest state (the exact stale shape) —
full-ledger regeneration per the write law, cap pre-checked, disclose-don't-
crash, refuses when nothing is stale.

Folds the D2 residual: rewrite_state raising BETWEEN the paid chat() and its
INSERT loses that call's cost from both ledgers — the paid spend must ride home
on the returned result, never escape as an exception.

RED-first, per team/ENGINEERING.md.
"""

from __future__ import annotations

import json

import pytest

from newslens import generate, memory_core
from test_generate import ENV  # noqa: F401


TEMPLATE = "topic={topic} date={date}\nledger:\n{ledger}\n"


def _seed_thread(con, topic):
    now = "2026-07-01T00:00:00.000Z"
    cur = con.execute(
        "INSERT INTO memory (topic, status, status_changed_at, created_at,"
        " updated_at) VALUES (?, 'active', ?, ?, ?)", (topic, now, now, now))
    con.commit()
    return cur.lastrowid


def _delta(con, tid, date, what="A development.", signif="Changed it."):
    cur = con.execute(
        "INSERT INTO thread_deltas (thread_id, edition_date, verdict,"
        " what_happened, significance, cites_json)"
        " VALUES (?, ?, 'advances', ?, ?, '[\"S1\"]')", (tid, date, what, signif))
    con.commit()
    return cur.lastrowid


def _state(con, tid, as_of, text=None):
    text = text or f"It stands here ({memory_core.human_date(as_of)})."
    con.execute(
        "INSERT INTO thread_state (thread_id, as_of_date, state_text, cost_usd)"
        " VALUES (?, ?, ?, 0)", (tid, as_of, text))
    con.commit()


def _supersede(con, delta_id, by_id):
    con.execute(
        "INSERT INTO thread_delta_supersessions (delta_id, superseded_by, reason)"
        " VALUES (?, ?, 'corrected')", (delta_id, by_id))
    con.commit()


def _chat(date, cost=0.02):
    hd = memory_core.human_date(date)

    def chat(key, prompt):
        return ({"state": f"It is a war now ({hd})."}, cost)
    return chat


# ===========================================================================
# find_stale_state_threads — the exact stale shape
# ===========================================================================

def test_state_behind_its_latest_live_delta_is_stale(migrated_con):
    con = migrated_con
    tid = _seed_thread(con, "Iran War")
    _delta(con, tid, "2026-07-10")
    _state(con, tid, "2026-07-10")
    _delta(con, tid, "2026-07-14")            # a delta landed AFTER the state
    stale = memory_core.find_stale_state_threads(con)
    ids = [s["thread_id"] for s in stale]
    assert tid in ids
    row = next(s for s in stale if s["thread_id"] == tid)
    assert row["latest_delta_date"] == "2026-07-14"
    assert row["state_as_of"] == "2026-07-10"


def test_state_current_with_its_latest_delta_is_not_stale(migrated_con):
    con = migrated_con
    tid = _seed_thread(con, "Iran War")
    _delta(con, tid, "2026-07-14")
    _state(con, tid, "2026-07-14")            # caught up
    assert memory_core.find_stale_state_threads(con) == []


def test_live_deltas_but_no_state_is_stale(migrated_con):
    con = migrated_con
    tid = _seed_thread(con, "Iran War")
    _delta(con, tid, "2026-07-14")            # a delta, but the rewrite never landed
    stale = memory_core.find_stale_state_threads(con)
    assert [s["thread_id"] for s in stale] == [tid]
    assert stale[0]["state_as_of"] == ""


def test_superseded_newest_delta_uses_live_only(migrated_con):
    """The newest delta is superseded (corrected away); the latest LIVE delta is
    older and matches the state — NOT stale (a wrong delta must not force a
    repair)."""
    con = migrated_con
    tid = _seed_thread(con, "Iran War")
    _delta(con, tid, "2026-07-10")
    _state(con, tid, "2026-07-10")
    bad = _delta(con, tid, "2026-07-14")
    corrected = _delta(con, tid, "2026-07-10", what="Correction.")
    _supersede(con, bad, corrected)
    # latest LIVE delta is 2026-07-10 (the 07-14 row is superseded) == state
    assert memory_core.find_stale_state_threads(con) == []


# ===========================================================================
# run_state_repair — heals the stale, refuses when nothing is stale
# ===========================================================================

def test_repair_all_rewrites_the_stale_thread_and_stamps_the_latest_delta(migrated_con):
    con = migrated_con
    tid = _seed_thread(con, "Iran War")
    _delta(con, tid, "2026-07-10")
    _state(con, tid, "2026-07-10")
    _delta(con, tid, "2026-07-14")
    before = con.execute("SELECT COUNT(*) c FROM thread_state WHERE thread_id=?",
                         (tid,)).fetchone()["c"]

    rep = generate.run_state_repair(all_threads=True, con=con, env=ENV,
                                    state_chat=_chat("2026-07-14"))
    assert rep.refused is False
    assert any(r["thread"] == "Iran War" and r["outcome"] == "written"
               for r in rep.repaired)
    after = con.execute("SELECT COUNT(*) c FROM thread_state WHERE thread_id=?",
                        (tid,)).fetchone()["c"]
    assert after == before + 1                       # a NEW state row (append-only)
    latest = memory_core.latest_state(con, tid)
    assert latest["as_of_date"] == "2026-07-14"      # caught up to the latest delta
    # no longer stale
    assert memory_core.find_stale_state_threads(con) == []


def test_repair_refuses_when_nothing_is_stale(migrated_con):
    con = migrated_con
    tid = _seed_thread(con, "Iran War")
    _delta(con, tid, "2026-07-14")
    _state(con, tid, "2026-07-14")
    rep = generate.run_state_repair(all_threads=True, con=con, env=ENV,
                                    state_chat=_chat("2026-07-14"))
    assert rep.refused is True
    assert "stale" in rep.reason.lower()


def test_repair_thread_id_scopes_to_one_thread(migrated_con):
    con = migrated_con
    a = _seed_thread(con, "Iran War")
    b = _seed_thread(con, "Ukraine")
    for tid in (a, b):
        _delta(con, tid, "2026-07-10")
        _state(con, tid, "2026-07-10")
        _delta(con, tid, "2026-07-14")
    rep = generate.run_state_repair(thread_id=a, con=con, env=ENV,
                                    state_chat=_chat("2026-07-14"))
    assert rep.refused is False
    assert [r["thread"] for r in rep.repaired] == ["Iran War"]
    # the other stale thread is untouched
    assert memory_core.latest_state(con, b)["as_of_date"] == "2026-07-10"


def test_repair_requires_exactly_one_selector(migrated_con):
    con = migrated_con
    with pytest.raises(ValueError):
        generate.run_state_repair(con=con, env=ENV)     # neither
    with pytest.raises(ValueError):
        generate.run_state_repair(thread_id=1, all_threads=True, con=con, env=ENV)


# ===========================================================================
# D2 residual — a raise BETWEEN the paid chat() and the INSERT keeps the spend
# ===========================================================================

def test_paid_rewrite_that_raises_post_chat_records_the_spend(migrated_con, monkeypatch):
    """rewrite_state must NEVER let a paid chat's cost escape as an exception: a
    post-chat step failing (here _state_diff) degrades to a returned result with
    the spend recorded, not a raised error that loses the money from the
    ledger."""
    con = migrated_con
    tid = _seed_thread(con, "Iran War")
    _delta(con, tid, "2026-07-14")

    def boom(*a, **k):
        raise RuntimeError("post-chat step blew up")
    monkeypatch.setattr(memory_core, "_state_diff", boom)

    # must NOT raise — and must carry the paid cost home
    res = memory_core.rewrite_state(
        con, tid, "Iran War", "2026-07-14", None, "sk-x", TEMPLATE,
        remaining_usd=1.0, chat=_chat("2026-07-14", cost=0.02))
    assert res.cost_usd == pytest.approx(0.02, abs=1e-9)
    assert res.outcome != "written"                  # the write did not complete
