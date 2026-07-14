"""Live-contact fix #4 — the moat gap on `--no-refresh` record runs (NL-63).

The finding: today's edition of record (2026-07-14, briefing id 5) shipped via
`generate --no-refresh` (rank was already paid; generate's built-in re-rank had
failed, so `--no-refresh` was the correct completion path). It published cleanly
BUT the memory pass never ran — the old gate (`if refresh and not no_threads`)
gated the moat on the refresh chain, so a `--no-refresh` record completion wrote
no delta. On the day the blockade was reinstated, the Hormuz thread got no entry.

Two contracts pinned here (RED-first, per team/ENGINEERING.md):

  * THE GATE FLIP — the memory pass runs on ANY run that PERSISTS the edition of
    record (a `--no-refresh` record completion writes the moat like any other
    persisted run); sample runs still never write it (they never persist); a
    REPEAT run writes no new delta, moves no thread, and bills nothing
    (idempotency made self-limiting: `moved_thread_ids` is now newly-written
    only — see write_deltas_for_edition).

  * THE BACKFILL — `generate.run_memory_backfill(date)` reconstructs the pass
    context from PERSISTED rows ONLY (briefs_by_slot from latest_valid_brief,
    slots from the briefing's story_slots — the SAME sources the live inline
    pass reads), writes the missing deltas + standing state idempotently, never
    touches the edition's narrative/script, shows the cap pre-check, and REFUSES
    (never fabricates) when the source arc is unrecoverable.

Fully offline: the state-rewrite seam is injected; ZERO live LLM calls, $0.
"""

from __future__ import annotations

import json

import pytest

from newslens import config, db, generate, memory_core, paths
from test_generate import (ENV, A_DAY, compliant_script, fake_model,  # noqa: F401
                           seed_briefing, slot, stories_payload)


# --- local seeding helpers --------------------------------------------------

def _seed_thread(con, topic="Iran War"):
    now = "2026-07-01T00:00:00.000Z"
    cur = con.execute(
        "INSERT INTO memory (topic, status, status_changed_at, created_at,"
        " updated_at) VALUES (?, 'active', ?, ?, ?)", (topic, now, now, now))
    con.commit()
    return cur.lastrowid


def _seed_prior_delta(con, tid, date="2026-07-01",
                      what="Transit fees imposed on shipping.",
                      signif="A pricing dispute over passage."):
    con.execute(
        "INSERT INTO thread_deltas (thread_id, edition_date, verdict,"
        " what_happened, significance, cites_json)"
        " VALUES (?, ?, 'advances', ?, ?, '[\"S1\"]')",
        (tid, date, what, signif))
    con.commit()


def _advancing_arc(what="The U.S. reinstated the naval blockade in the strait.",
                   signif="The dispute escalated from tolls to a shooting war.",
                   cites=("S2", "S4")):
    return {"delta": "advances", "what_happened": what,
            "significance": signif, "cites": list(cites)}


def _brief_doc(arc=None):
    return {"brief": {"arc": arc or _advancing_arc()}}


def _seed_valid_brief(con, date, slot_n, arc=None):
    """A persisted valid analysis brief — exactly what latest_valid_brief reads
    back on BOTH the refresh and the --no-refresh path."""
    con.execute(
        "INSERT INTO analysis_briefs (date, slot, tier, status, brief_json,"
        " model, cost_usd) VALUES (?, ?, 'full', 'valid', ?, 'gpt-4o', 0.0)",
        (date, slot_n, json.dumps(_brief_doc(arc))))
    con.commit()


def _state_chat_citing(date, cost=0.02):
    """An injected state-rewrite seam that returns a cited paragraph — no live
    call, deterministic spend."""
    hd = memory_core.human_date(date)

    def chat(key, prompt):
        return ({"state": f"The conflict is now open war ({hd})."}, cost)
    return chat


# ===========================================================================
# 1. moved_thread_ids semantics — the idempotency safety the gate flip rests on
# ===========================================================================

def test_idempotent_repeat_moves_no_thread(migrated_con):
    """CONSCIOUS FLIP: a second pass on the same edition writes no new delta AND
    returns an EMPTY moved_thread_ids — so no state rewrite (no spend) fires on a
    repeat. Previously an idempotent skip still appended the thread to
    moved_thread_ids (re-firing the paid state rewrite on every re-run); the
    field now means 'the ledger MOVED this pass' (newly-written only), which is
    what makes gate-flip (a) self-limiting."""
    con = migrated_con
    tid = _seed_thread(con, "Iran War")
    con.execute("INSERT INTO briefings (date, story_slots) VALUES (?, '[]')", (A_DAY,))
    con.commit()
    slots = [{"slot": "1", "matched_memory": ["Iran War"]}]
    briefs = {1: _brief_doc()}
    r1 = memory_core.write_deltas_for_edition(con, A_DAY, None, briefs, slots)
    assert r1.moved_thread_ids == [tid] and len(r1.written) == 1
    r2 = memory_core.write_deltas_for_edition(con, A_DAY, None, briefs, slots)
    assert r2.written == []
    assert r2.moved_thread_ids == []          # the flip: no thread moves on a repeat
    assert con.execute("SELECT COUNT(*) c FROM thread_deltas").fetchone()["c"] == 1


def test_repeat_memory_pass_writes_nothing_and_bills_nothing(migrated_con):
    """The run_memory_pass contract the gate flip depends on: the FIRST pass
    writes the delta + state and bills; a REPEAT pass writes no new delta, no new
    state row, and bills $0 (moved_thread_ids empty -> rewrite_state never
    fires)."""
    con = migrated_con
    _seed_thread(con, "Iran War")
    con.execute("INSERT INTO briefings (date, story_slots) VALUES (?, '[]')", (A_DAY,))
    con.commit()
    slots = [{"slot": "1", "matched_memory": ["Iran War"]}]
    briefs = {1: _brief_doc()}
    chat = _state_chat_citing(A_DAY)

    r1 = generate.GenReport(date=A_DAY, variant="A")
    spent1 = generate.run_memory_pass(con, A_DAY, "k", cap=0.25, spent=0.0,
                                      briefs_by_slot=briefs, slots=slots,
                                      report=r1, state_chat=chat)
    assert con.execute("SELECT COUNT(*) c FROM thread_deltas").fetchone()["c"] == 1
    assert con.execute("SELECT COUNT(*) c FROM thread_state").fetchone()["c"] == 1
    assert spent1 > 0.0 and r1.memory_usd > 0.0

    r2 = generate.GenReport(date=A_DAY, variant="A")
    spent2 = generate.run_memory_pass(con, A_DAY, "k", cap=0.25, spent=0.0,
                                      briefs_by_slot=briefs, slots=slots,
                                      report=r2, state_chat=chat)
    assert con.execute("SELECT COUNT(*) c FROM thread_deltas").fetchone()["c"] == 1
    assert con.execute("SELECT COUNT(*) c FROM thread_state").fetchone()["c"] == 1
    assert spent2 == 0.0 and r2.memory_usd == 0.0
    assert r2.memory["threads_moved"] == 0


# ===========================================================================
# 2. the gate flip — end-to-end through run_generate
# ===========================================================================

def test_no_refresh_record_run_writes_the_moat(migrated_con, fake_model, monkeypatch):
    """RED before the flip: a `--no-refresh` record run (refresh=False, NOT a
    sample) persists the edition but the old gate skipped the memory pass, so no
    delta was written. The flip makes the persisted edition write the moat from
    the SAME briefs_by_slot the live pass reads (latest_valid_brief)."""
    con = migrated_con
    tid = _seed_thread(con, "Iran War")
    _seed_prior_delta(con, tid, "2026-07-01")
    slots = [slot(1, mem=["Iran War"])]
    seed_briefing(con, A_DAY, slots)
    _seed_valid_brief(con, A_DAY, 1)
    fake_model.narrative = stories_payload(slots)
    fake_model.script = compliant_script(slots)
    monkeypatch.setattr(memory_core, "_default_state_chat", _state_chat_citing(A_DAY))

    rep = generate.run_generate(date=A_DAY, con=con, env=ENV, refresh=False)
    assert rep.sample is False
    n = con.execute("SELECT COUNT(*) c FROM thread_deltas WHERE thread_id=?"
                    " AND edition_date=?", (tid, A_DAY)).fetchone()["c"]
    assert n == 1, "a --no-refresh record run must write the moat"
    assert rep.memory.get("deltas_written") == 1
    assert con.execute("SELECT COUNT(*) c FROM thread_state WHERE thread_id=?",
                       (tid,)).fetchone()["c"] == 1


def test_sample_run_never_writes_the_moat(migrated_con, fake_model, monkeypatch):
    """REGRESSION GUARD (green before and after): a SAMPLE run (retired variant B
    on an A day) never persists the edition of record, so it must never write a
    new delta — the flip must not have opened that door."""
    con = migrated_con
    tid = _seed_thread(con, "Iran War")
    _seed_prior_delta(con, tid, "2026-07-01")
    slots = [slot(1, mem=["Iran War"])]
    seed_briefing(con, A_DAY, slots)
    _seed_valid_brief(con, A_DAY, 1)
    fake_model.narrative = stories_payload(slots, variant="B", my_read="A judgment.")
    fake_model.script = compliant_script(slots)
    monkeypatch.setattr(memory_core, "_default_state_chat", _state_chat_citing(A_DAY))

    rep = generate.run_generate(date=A_DAY, con=con, env=ENV, variant_override="B")
    assert rep.sample is True
    n = con.execute("SELECT COUNT(*) c FROM thread_deltas WHERE thread_id=?",
                    (tid,)).fetchone()["c"]
    assert n == 1, "sample must add NO new delta (only the seeded prior remains)"


# ===========================================================================
# 3. the backfill — the already-published 2026-07-14 gap
# ===========================================================================

def test_backfill_writes_missing_deltas_idempotently(migrated_con):
    """The backfill reconstructs run_memory_pass's context from persisted rows,
    writes the missing delta + state, leaves the edition's narrative UNTOUCHED,
    and is idempotent — a second backfill writes nothing new and bills $0."""
    con = migrated_con
    tid = _seed_thread(con, "Iran War")
    _seed_prior_delta(con, tid, "2026-07-01")
    slots = [slot(1, mem=["Iran War"])]
    seed_briefing(con, A_DAY, slots, narrative="Published edition of record.")
    _seed_valid_brief(con, A_DAY, 1)
    chat = _state_chat_citing(A_DAY)

    rep = generate.run_memory_backfill(A_DAY, con=con, env=ENV, state_chat=chat)
    assert rep.refused is False
    assert rep.deltas_written == 1 and rep.threads_moved == 1
    assert con.execute("SELECT COUNT(*) c FROM thread_deltas WHERE thread_id=?"
                       " AND edition_date=?", (tid, A_DAY)).fetchone()["c"] == 1
    assert con.execute("SELECT COUNT(*) c FROM thread_state WHERE thread_id=?",
                       (tid,)).fetchone()["c"] == 1
    # the edition of record is NEVER rewritten
    row = con.execute("SELECT narrative_text FROM briefings WHERE date=?",
                      (A_DAY,)).fetchone()
    assert row["narrative_text"] == "Published edition of record."

    rep2 = generate.run_memory_backfill(A_DAY, con=con, env=ENV, state_chat=chat)
    assert rep2.refused is False
    assert rep2.deltas_written == 0 and rep2.memory_usd == 0.0
    assert con.execute("SELECT COUNT(*) c FROM thread_deltas WHERE thread_id=?",
                       (tid,)).fetchone()["c"] == 2   # prior + today, no third
    assert con.execute("SELECT COUNT(*) c FROM thread_state WHERE thread_id=?",
                       (tid,)).fetchone()["c"] == 1   # no second state row


def test_backfill_folds_state_spend_into_edition_cost_never_touching_content(migrated_con):
    """Money honesty: the state-rewrite spend is folded into the edition's
    token_cost (as the live path's _fold_cost_steps does), WITHOUT re-archiving
    or touching the narrative/script."""
    con = migrated_con
    tid = _seed_thread(con, "Iran War")
    _seed_prior_delta(con, tid, "2026-07-01")
    slots = [slot(1, mem=["Iran War"])]
    seed_briefing(con, A_DAY, slots, narrative="Published edition of record.",
                  token_cost={"steps": [{"step": "rank_select", "usd": 0.001}],
                              "total_usd": 0.001})
    _seed_valid_brief(con, A_DAY, 1)

    generate.run_memory_backfill(A_DAY, con=con, env=ENV,
                                 state_chat=_state_chat_citing(A_DAY, cost=0.02))
    tc = json.loads(con.execute("SELECT token_cost FROM briefings WHERE date=?",
                                (A_DAY,)).fetchone()["token_cost"])
    steps = [s["step"] for s in tc["steps"]]
    assert "state_rewrites" in steps                 # the moat spend is on the record
    assert "rank_select" in steps                    # the prior steps survive (no re-archive)
    assert tc["total_usd"] == pytest.approx(0.021, abs=1e-6)
    # history table stays empty — the edition was NOT archived/rewritten
    assert con.execute("SELECT COUNT(*) c FROM briefings_history"
                       " WHERE date=?", (A_DAY,)).fetchone()["c"] == 0


def test_backfill_refuses_when_arc_is_unrecoverable(migrated_con):
    """Stale-but-honest beats fabricated-context: an edition with matched threads
    but NO valid analysis brief has no arc to write FROM — the backfill refuses
    with a named reason and writes nothing, rather than invent the delta."""
    con = migrated_con
    _seed_thread(con, "Iran War")
    slots = [slot(1, mem=["Iran War"])]
    seed_briefing(con, A_DAY, slots, narrative="Published, but analysis never ran.")
    # deliberately NO analysis_briefs row

    rep = generate.run_memory_backfill(A_DAY, con=con, env=ENV,
                                       state_chat=_state_chat_citing(A_DAY))
    assert rep.refused is True
    assert "brief" in rep.reason.lower()
    assert con.execute("SELECT COUNT(*) c FROM thread_deltas").fetchone()["c"] == 0


def test_backfill_refuses_when_no_edition_of_record(migrated_con):
    """No briefing row for the date — nothing published to backfill; the backfill
    refuses cleanly (never crashes, never fabricates a row)."""
    rep = generate.run_memory_backfill("2026-07-14", con=migrated_con, env=ENV,
                                       state_chat=None)
    assert rep.refused is True
    assert "briefing" in rep.reason.lower()


def test_backfill_cost_precheck_against_the_cap(migrated_con):
    """The cap pre-check is enforced: under a tiny cap the state rewrite is
    skipped-budget ($0 spend, no state row, disclosed), while the $0 delta ledger
    still writes — disclose-don't-crash, money-honest."""
    con = migrated_con
    tid = _seed_thread(con, "Iran War")
    _seed_prior_delta(con, tid, "2026-07-01")
    slots = [slot(1, mem=["Iran War"])]
    seed_briefing(con, A_DAY, slots, narrative="Published edition of record.")
    _seed_valid_brief(con, A_DAY, 1)
    tiny = dict(ENV, BUDGET_CAP_USD_PER_RUN="0.00001")

    rep = generate.run_memory_backfill(A_DAY, con=con, env=tiny,
                                       state_chat=_state_chat_citing(A_DAY))
    assert rep.refused is False
    assert rep.cap == pytest.approx(0.00001)
    assert rep.deltas_written == 1               # ledger is $0 — always writes
    assert rep.memory_usd == 0.0                 # state rewrite skipped under cap
    assert any("budget" in w.lower() for w in rep.warnings)
    assert con.execute("SELECT COUNT(*) c FROM thread_state").fetchone()["c"] == 0


def test_cli_memory_backfill_refuses_without_briefing(tmp_paths, capsys):
    """CLI smoke: the subcommand parses, runs offline (no key, no briefing), and
    refuses with a nonzero exit — no spend, no crash."""
    from newslens import cli
    db.migrate()
    rc = cli.main(["memory-backfill", "--date", "2026-07-05"])
    assert rc == 1
    assert "REFUSED" in capsys.readouterr().err


def test_backfill_refuses_a_ranked_but_never_published_edition(migrated_con):
    """Gate Fix 3 (loop #5, gate's own finding): rank creates the briefings row
    BEFORE generate publishes, so a rank-succeeded/generate-failed day leaves
    row + slots + valid brief + NULL narrative. A backfill there would write
    ledger deltas citing an edition that never shipped — the orphan-delta class
    the M1 gate-F reorder exists to prevent. The backfill must refuse with the
    unpublished-edition reason and the cure, and write/spend nothing."""
    con = migrated_con
    tid = _seed_thread(con, "Iran War")
    _seed_prior_delta(con, tid, "2026-07-01")
    slots = [slot(1, mem=["Iran War"])]
    seed_briefing(con, A_DAY, slots, narrative="placeholder")
    _seed_valid_brief(con, A_DAY, 1)
    tc_before = con.execute("SELECT token_cost FROM briefings WHERE date=?",
                            (A_DAY,)).fetchone()["token_cost"]
    # the rank-only shape: the row exists, the narrative never landed
    con.execute("UPDATE briefings SET narrative_text = NULL WHERE date=?",
                (A_DAY,))
    con.commit()

    rep = generate.run_memory_backfill(A_DAY, con=con, env=ENV,
                                       state_chat=_state_chat_citing(A_DAY))
    assert rep.refused is True
    assert "never PUBLISHED" in rep.reason
    assert "generate --no-refresh" in rep.reason      # the cure is named
    assert con.execute("SELECT COUNT(*) c FROM thread_deltas"
                       " WHERE edition_date=?", (A_DAY,)).fetchone()["c"] == 0
    assert con.execute("SELECT COUNT(*) c FROM thread_state").fetchone()["c"] == 0
    tc_after = con.execute("SELECT token_cost FROM briefings WHERE date=?",
                           (A_DAY,)).fetchone()["token_cost"]
    assert tc_after == tc_before                       # zero spend, zero fold
