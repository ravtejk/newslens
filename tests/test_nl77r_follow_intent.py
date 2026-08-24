"""NL-77 REMNANT — THE FOLLOW TAP QUEUES THE COLD-START INTENT.

WHAT THIS FILE GUARDS. ADR-0013 §5 rules the entry-zero intent gate as "§F
explicit-action only", and names the wired paths: **follow** (which writes a
`pending` baseline row for a cold-start thread — $0, NO LLM) and the explicit
`newslens memory-baseline` command (which materializes it). `cli.py`'s
`memory add` has honoured that since 2026-07-16 (cli.py:1300-1315).

THE REMNANT: every UI follow door — the four server routes readers actually tap
— queued NOTHING. `memory.write_baseline_intent` had exactly ONE src caller
(cli.py), so the entry-zero gate never fired for an organic follow. A thread the
principal followed from the CLI got its founding floor; the same thread followed
by tapping Follow in the browser did not. The feature shipped, rendered, and was
unreachable from the surface it was for.

FOUR TEETH.

  1. EVERY EXPLICIT FOLLOW DOOR QUEUES. Not one route, all of them: the plain
     follow verb, THE TAP (NL-17-M1c's instant story-seeded commit), the
     reader's altitude pick, and the revive. They are pinned separately because
     they are separate routes with separate call paths, and a chokepoint that
     covers three of four is the bug this file exists to close.
  2. IT IS FREE. The intent is a `pending` row and nothing else — no model call,
     no key read, $0. ADR-0013 §5: "never a silent LLM call from a memory verb."
     Generation stays behind `newslens memory-baseline`, which is cap-gated.
     A follow door that could spend would be a new autonomous-spend path.
  3. COLD START ONLY. A thread that already carries a ledger delta has a real
     record and needs no founding floor — the entry-zero genre is for an EMPTY
     ledger (ADR-0013 context). The gate is mutation-proven below, not assumed.
  4. THE NL-17 REWORK COMPOSES. The settle RE-AIMS the seeded row (mutation law
     — one thread, one identity), so a thread-id-keyed intent survives the
     rename. Pinned, because the intent entrypoint the ADR left ready is
     TOPIC-keyed, and a topic-keyed intent would have gone stale on exactly the
     path NL-17 made normal.

Offline by construction: autouse sandbox (conftest), no network, no key, $0.
"""

from __future__ import annotations

import pytest

from newslens import db, memory, memory_core as mc, paths, server


# ---------------------------------------------------------------------------
# harness — the established double (test_nl17_m1_entity_identity's pattern):
# the REAL handler methods, a captured _send_json.
# ---------------------------------------------------------------------------

STORY = "Volkswagen plans significant job cuts"
HEADLINE = "Volkswagen plans significant job cuts at three plants"


class _Handler:
    _topic_arg = server.Handler._topic_arg
    _raw_topic_arg = server.Handler._raw_topic_arg
    _with_memory = server.Handler._with_memory
    _ref_id_for = server.Handler._ref_id_for
    _commit_altitude = server.Handler._commit_altitude
    _api_follow = server.Handler._api_follow
    _api_follow_seed = server.Handler._api_follow_seed
    _api_follow_at = server.Handler._api_follow_at
    _api_revive = server.Handler._api_revive
    _seed_thread = server.Handler._seed_thread

    def __init__(self):
        self.sent = []

    def _send_json(self, obj, status=200):
        self.sent.append((obj, status))
        return obj


@pytest.fixture()
def con():
    db.migrate(db_path=paths.DB_PATH)
    c = db.connect(paths.DB_PATH)
    yield c
    c.close()


@pytest.fixture(autouse=True)
def quiet_memory(monkeypatch):
    """memory.md sync is not what any of this is about."""
    monkeypatch.setattr(memory, "sync_memory", lambda con, **kw: memory.SyncResult())
    monkeypatch.setattr(memory, "write_memory_file", lambda con: None)


def _tid(con, topic):
    row = con.execute("SELECT id FROM memory WHERE lower(topic) = lower(?)",
                      (topic,)).fetchone()
    return row["id"] if row is not None else None


def _baselines(con, tid):
    return [dict(r) for r in con.execute(
        "SELECT id, status, as_of_date, reason FROM thread_baselines"
        " WHERE thread_id = ? ORDER BY id", (tid,))]


def _delta(con, tid, date="2026-07-20", what="moved", slot=1):
    with con:
        con.execute(
            "INSERT INTO thread_deltas (thread_id, edition_date, slot, verdict,"
            " what_happened, significance, cites_json)"
            " VALUES (?, ?, ?, 'advances', ?, 'x', '[\"S1\"]')",
            (tid, date, slot, what))


# ===========================================================================
# 1 — EVERY EXPLICIT FOLLOW DOOR QUEUES THE INTENT
# ===========================================================================

def test_the_plain_follow_door_queues_a_pending_intent(con):
    """BORN RED — /api/follow wrote a memory row and nothing else.

    The reader-facing equivalent of `newslens memory add`. Same explicit act,
    same §F gate; before the diff only the CLI half honoured it."""
    _Handler()._api_follow({"topic": "Iran talks"})
    tid = _tid(con, "Iran talks")
    assert tid is not None, "the follow itself must still land"
    rows = _baselines(con, tid)
    assert [r["status"] for r in rows] == ["pending"], (
        f"the follow tap must queue exactly one pending intent, got {rows}")


def test_the_tap_queues_a_pending_intent(con):
    """BORN RED — NL-17-M1c's THE TAP is the door readers actually use.

    `_api_follow_seed` commits a story-seeded thread instantly. It is the
    highest-traffic follow surface in the product and it queued nothing."""
    _Handler()._api_follow_seed({"topic": STORY, "origin": HEADLINE})
    tid = _tid(con, STORY)
    assert tid is not None
    assert [r["status"] for r in _baselines(con, tid)] == ["pending"]


def test_the_reader_altitude_pick_queues_a_pending_intent(con):
    """BORN RED — the "Instead:" swap door creates a follow when there is no
    prior one to move, and a created follow is a follow."""
    _Handler()._api_follow_at({"name": "Volkswagen", "altitude": "entity"})
    tid = _tid(con, "Volkswagen")
    assert tid is not None
    assert [r["status"] for r in _baselines(con, tid)] == ["pending"]


def test_the_revive_door_queues_a_pending_intent(con):
    """BORN RED — a revive is an explicit re-follow.

    cli.py's intent block sits AFTER its add/revive branch, so the CLI queues on
    revive too (cli.py:1300-1315). The server's revive door did not."""
    with con:
        con.execute("INSERT INTO memory (topic, status) VALUES (?, 'dismissed_user')",
                    ("Iran talks",))
    _Handler()._api_revive({"topic": "Iran talks"})
    tid = _tid(con, "Iran talks")
    assert con.execute("SELECT status FROM memory WHERE id = ?",
                       (tid,)).fetchone()["status"] == "active"
    assert [r["status"] for r in _baselines(con, tid)] == ["pending"]


# ===========================================================================
# 2 — IT IS FREE (ADR-0013 §5: never a silent LLM call from a memory verb)
# ===========================================================================

def test_the_follow_tap_makes_no_model_call(con, monkeypatch):
    """BORN RED (on the pending-row assertion — nothing queued pre-diff).

    Pinned because the whole risk of wiring the entry-zero gate into a
    reader-facing door is that it starts BILLING on a tap. The intent is a row;
    generation stays behind `memory-baseline`. The no-model half is a CARRIED
    invariant (a tap made no model call before either) — it is the half that
    must not regress as the queueing half lands."""
    from newslens import analysis, generate

    def _boom(*a, **k):
        raise AssertionError("a follow tap must never call a model")

    monkeypatch.setattr(generate, "generate_thread_baseline", _boom)
    monkeypatch.setattr(generate, "run_baseline_backfill", _boom)
    monkeypatch.setattr(analysis, "call_analysis_model", _boom, raising=False)

    _Handler()._api_follow_seed({"topic": STORY, "origin": HEADLINE})

    tid = _tid(con, STORY)
    rows = _baselines(con, tid)
    assert [r["status"] for r in rows] == ["pending"]
    # the pending row carries no spend and no content — it is an intent
    row = con.execute(
        "SELECT cost_usd, backgrounder, model FROM thread_baselines"
        " WHERE id = ?", (rows[0]["id"],)).fetchone()
    assert row["cost_usd"] == 0.0
    assert (row["backgrounder"] or "") == ""
    assert (row["model"] or "") == ""


# ===========================================================================
# 3 — COLD START ONLY, AND NO STACKING
# ===========================================================================

def test_a_thread_with_a_ledger_record_gets_no_intent(con):
    """MUTATION-PROVEN GATE (born green — pre-diff nothing queued at all, so
    this passed over a dead route; post-diff it guards a live one).

    The entry-zero genre is for an EMPTY ledger. A thread that has already moved
    has a real record and needs no founding floor — re-following it must not
    queue one. Proof that this bites: delete the `thread_deltas` check in
    `memory.add_thread` and this test goes red (receipt in the build report)."""
    with con:
        con.execute("INSERT INTO memory (topic, status) VALUES (?, 'dismissed_user')",
                    ("Iran talks",))
    tid = _tid(con, "Iran talks")
    _delta(con, tid)                       # it has a record now

    _Handler()._api_revive({"topic": "Iran talks"})

    assert _baselines(con, tid) == [], (
        "a thread with a ledger delta must get no entry-zero intent")


def test_following_an_already_active_thread_stacks_nothing(con):
    """BORN RED (on the first tap's pending row; the no-stacking half is the
    carried invariant) — `already-active` is not a new follow.

    cli.py returns early on an already-tracked thread and never reaches its
    intent block; the doors must not diverge. Doubly held by
    write_baseline_intent's own dedup, which is the belt to this braces."""
    h = _Handler()
    h._api_follow({"topic": "Iran talks"})
    tid = _tid(con, "Iran talks")
    assert len(_baselines(con, tid)) == 1
    h._api_follow({"topic": "Iran talks"})          # tapped again
    h._api_follow({"topic": "Iran talks"})          # and again
    assert len(_baselines(con, tid)) == 1, "intents must never stack"


def test_a_ready_baseline_is_not_re_queued_by_a_re_follow(con):
    """CARRIED INVARIANT (born green, via write_baseline_intent's dedup) — a
    thread that already HAS its founding floor must not queue a second one when
    the reader unfollows and follows again."""
    with con:
        con.execute("INSERT INTO memory (topic, status) VALUES (?, 'dismissed_user')",
                    ("Iran talks",))
    tid = _tid(con, "Iran talks")
    mc.record_baseline(con, tid, "2026-07-16", "ready", backgrounder="bg 2015")

    _Handler()._api_revive({"topic": "Iran talks"})

    assert [r["status"] for r in _baselines(con, tid)] == ["ready"]


# ===========================================================================
# 4 — THE NL-17 REWORK COMPOSES (the rename the settle performs)
# ===========================================================================

def test_the_settle_rename_leaves_exactly_one_intent_on_the_same_thread(con):
    """BORN RED (the seed queues nothing pre-diff, so there is no intent to
    survive anything).

    THE COMPOSITION THAT MATTERS. NL-17's settle RE-AIMS the seeded row rather
    than creating a second one (mutation law), so the thread's id is stable
    across the rename while its TOPIC is not. The intent is written against the
    id, so it survives — where a topic-keyed intent (the shape ADR-0013 left
    ready in `capture_baseline_intent`) would have been orphaned by the very
    path NL-17 made the normal one."""
    h = _Handler()
    h._api_follow_seed({"topic": STORY, "origin": HEADLINE})
    tid = _tid(con, STORY)
    assert [r["status"] for r in _baselines(con, tid)] == ["pending"]

    # the settle's re-aim, driven directly (the resolver is not what this pins)
    memory.move_follow_altitude(
        con, tid, new_name="Volkswagen", altitude="entity",
        primary_entity="Volkswagen", disclosure="Volkswagen (company)",
        source="auto", log_correction=False, initiator="org")

    assert _tid(con, STORY) is None, "the settle renames rather than forks"
    assert _tid(con, "Volkswagen") == tid, "same row, new name"
    assert [r["status"] for r in _baselines(con, tid)] == ["pending"], (
        "the intent must survive the rename — it is keyed on the thread id")


def test_the_backlog_sweep_sees_a_tap_followed_thread(con):
    """BORN RED — the end-to-end point of the whole remnant.

    A thread followed by TAPPING Follow must show up in the backlog the
    `newslens memory-baseline` command drains. This is the seam the feature was
    missing: intent written at the tap, materialized later behind the explicit,
    cap-gated command — never on the tap itself."""
    _Handler()._api_follow_seed({"topic": STORY, "origin": HEADLINE})
    tid = _tid(con, STORY)

    awaiting = mc.threads_awaiting_baseline(con)
    ids = [a["thread_id"] for a in awaiting]
    assert tid in ids, (
        f"the tapped follow must await a baseline, got {awaiting}")
    mine = [a for a in awaiting if a["thread_id"] == tid][0]
    assert mine["as_of"] is not None, (
        "the pending intent's own date must reach the generator (gate FIX-2: "
        "the intent's date wins over the run date)")
