"""QA extensions — the memory-core chips batch (NL-72 guard, NL-73 repair rung,
the delta-7 photocopy detector, collect-now schemas 0015/0016). 2026-07-16.

Adversarial pass against the implementer's handoff. Covers the seams their 29
tests leave open:

  * NL-72 — multi-offender refusal naming; unmatched/unresolvable threads never
    block; the idempotent re-backfill shape (RED — the would-move keying counts
    a delta already on file); the --force poison landing as the disclosed
    latest-by-id regression AND NL-73 healing it (the cross-chip recovery loop);
    the CLI --force plumb-through.
  * NL-73 — the mixed-shape staleness sweep in ONE db; budget exhaustion
    MID-SWEEP (thread 1 paid, thread 2 skipped-budget, both disclosed, spend
    exact); post-paid failure honesty at function, sweep, and live-pass level
    (one RED: the live pass's warning tuple omits the new reachable "failed");
    scoped refusal naming; the CLI selector contract.
  * Photocopy — the >= 0.7 boundary pinned from both sides; the no-stopword
    worst case characterized and quantified (single-content-word swap in a
    short clause false-positives at n >= 6 tokens: (n-1)/(n+1) >= 0.7); the
    sanctioned same-day split end-to-end through write_deltas_for_edition;
    the suspect note riding report.memory + report.warnings (diagnose's feed);
    summary() disclosure.
  * Schemas — thread_closures has NO structural one-per-thread bound (RED —
    a check-then-insert race lands a permanent, undeletable duplicate);
    cross-connection closure visibility; the closure verb flips NOTHING
    (conscious-flip pin for the future closure feature); the missing DELETE
    trigger arm on concept_explanations.

Real-record grounding (read 2026-07-16, sqlite3 mode=ro, zero writes): the
implementer's SIG_5/SIG_7 fixtures are byte-identical to thread 10's deltas
5/7; the shipped scorer gives 0.8 on that pair and 0.079 on the 3-vs-5 control.

RED tests are acceptance contracts (house pattern): each carries its fix
contract in the docstring and FAILS on today's tree by design.
"""

from __future__ import annotations

import json
import sqlite3

import pytest

from newslens import cli, generate, memory_core, paths
from test_generate import ENV, seed_briefing, slot  # noqa: F401


OLD = "2026-07-10"
NEW = "2026-07-14"
TODAY = "2026-07-16"


# ---------------------------------------------------------------------------
# local seed helpers (same shapes the implementer's chip tests use)
# ---------------------------------------------------------------------------

def _seed_thread(con, topic):
    now = "2026-07-01T00:00:00.000Z"
    cur = con.execute(
        "INSERT INTO memory (topic, status, status_changed_at, created_at,"
        " updated_at) VALUES (?, 'active', ?, ?, ?)", (topic, now, now, now))
    con.commit()
    return cur.lastrowid


def _delta(con, tid, date, what="A development.", signif="Changed it.", slot_n=None):
    cur = con.execute(
        "INSERT INTO thread_deltas (thread_id, edition_date, slot, verdict,"
        " what_happened, significance, cites_json)"
        " VALUES (?, ?, ?, 'advances', ?, ?, '[\"S1\"]')",
        (tid, date, slot_n, what, signif))
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


def _arc(what="The blockade was reinstated.",
         signif="The dispute escalated from tolls to a shooting war.",
         verdict="advances", cites=("S2",)):
    return {"delta": verdict, "what_happened": what, "significance": signif,
            "cites": list(cites)}


def _seed_valid_brief(con, date, slot_n, arc):
    con.execute(
        "INSERT INTO analysis_briefs (date, slot, tier, status, brief_json,"
        " model, cost_usd) VALUES (?, ?, 'full', 'valid', ?, 'gpt-4o', 0.0)",
        (date, slot_n, json.dumps({"brief": {"arc": arc}})))
    con.commit()


def _chat_for(date, cost=0.02):
    hd = memory_core.human_date(date)

    def chat(key, prompt):
        return ({"state": f"It is a war now ({hd})."}, cost)
    return chat


# ===========================================================================
# NL-72 — the backfill newer-activity guard
# ===========================================================================

def test_refusal_names_every_offending_thread(migrated_con):
    """Two threads the pass would move, one newer-DELTA and one newer-STATE-only:
    the single refusal names BOTH topics and both newer dates (the operator sees
    the whole blast radius, not the first offender)."""
    con = migrated_con
    ta = _seed_thread(con, "Iran War")           # newer ledger activity
    tb = _seed_thread(con, "Red Sea Shipping")   # newer STATE only
    _delta(con, ta, NEW)
    _delta(con, tb, "2026-07-01")
    _state(con, tb, NEW)
    seed_briefing(con, OLD, [slot(1, mem=["Iran War"]),
                             slot(2, mem=["Red Sea Shipping"])],
                  narrative="Published OLD edition.")
    _seed_valid_brief(con, OLD, 1, _arc())
    _seed_valid_brief(con, OLD, 2, _arc(what="Insurers pulled cover.",
                                        signif="Shipping rerouted around the cape."))

    rep = generate.run_memory_backfill(OLD, con=con, env=ENV,
                                       state_chat=_chat_for(OLD))
    assert rep.refused is True
    assert "Iran War" in rep.reason and "Red Sea Shipping" in rep.reason
    assert rep.reason.count(NEW) >= 2            # each offender's newer date named
    # nothing landed for either thread
    for tid in (ta, tb):
        assert con.execute(
            "SELECT COUNT(*) c FROM thread_deltas WHERE thread_id=? AND"
            " edition_date=?", (tid, OLD)).fetchone()["c"] == 0


def test_unmatched_thread_with_newer_activity_never_blocks(migrated_con):
    """The would-move keying, negative space: a thread with newer activity that
    NO moving slot matched must not block a backfill that cannot touch it."""
    con = migrated_con
    ta = _seed_thread(con, "Iran War")           # the backfill's actual target
    tb = _seed_thread(con, "Unrelated Saga")     # newer activity, matched nowhere
    _delta(con, tb, NEW)
    seed_briefing(con, OLD, [slot(1, mem=["Iran War"])],
                  narrative="Published OLD edition.")
    _seed_valid_brief(con, OLD, 1, _arc())

    rep = generate.run_memory_backfill(OLD, con=con, env=ENV,
                                       state_chat=_chat_for(OLD))
    assert rep.refused is False
    assert con.execute("SELECT COUNT(*) c FROM thread_deltas WHERE thread_id=?"
                       " AND edition_date=?", (ta, OLD)).fetchone()["c"] == 1
    assert con.execute("SELECT COUNT(*) c FROM thread_deltas WHERE thread_id=?",
                       (tb,)).fetchone()["c"] == 1   # untouched


def test_unresolvable_matched_topic_never_blocks(migrated_con):
    """matched_memory naming a topic with no memory row: the pass would skip it
    (unresolvable), so the guard must too — never a refusal over a ghost."""
    con = migrated_con
    seed_briefing(con, OLD, [slot(1, mem=["Ghost Topic"])],
                  narrative="Published OLD edition.")
    _seed_valid_brief(con, OLD, 1, _arc())
    rep = generate.run_memory_backfill(OLD, con=con, env=ENV,
                                       state_chat=_chat_for(OLD))
    assert rep.refused is False                    # skipped-unresolvable, not blocked


def test_idempotent_rebackfill_is_not_refused_after_the_thread_moves(migrated_con):
    """RED (acceptance contract) — the documented idempotence of the backfill
    verb survives the thread moving later.

    run_memory_backfill's own contract: 'Idempotent: a second backfill writes no
    new delta, moves no thread, bills $0.' After the first backfill cures OLD,
    the thread moves at NEW. A re-run for OLD would idempotent-skip every write
    (the delta is on file), move nothing, and rewrite no state — there is no
    poison. NL-72's guard keys on the arc verdict alone, counts the thread as
    would-move, and REFUSES with a reason claiming the backfill 'would MOVE'
    a thread it would not move.

    Fix contract: backfill_newer_activity skips a (thread, slot) whose delta
    for `date` is already on file — memory_core._delta_exists, the SAME primary
    idempotence gate write_deltas_for_edition uses — so the guard keys on what
    the pass WOULD WRITE. (Alternative conscious flip: amend the verb's
    docstring + refusal text to declare re-backfills of moved threads
    refused-by-design; that surrenders the documented idempotence property and
    leaves the reason text asserting a move that would not happen.)
    """
    con = migrated_con
    tid = _seed_thread(con, "Iran War")
    seed_briefing(con, OLD, [slot(1, mem=["Iran War"])],
                  narrative="Published OLD edition.")
    _seed_valid_brief(con, OLD, 1, _arc())

    first = generate.run_memory_backfill(OLD, con=con, env=ENV,
                                         state_chat=_chat_for(OLD))
    assert first.refused is False and first.deltas_written == 1  # cured

    _delta(con, tid, NEW)                        # the thread moves later, live

    second = generate.run_memory_backfill(OLD, con=con, env=ENV,
                                          state_chat=_chat_for(OLD))
    # the documented idempotent no-op: not refused, nothing new written
    assert second.refused is False
    assert second.deltas_written == 0 and second.threads_moved == 0
    assert con.execute("SELECT COUNT(*) c FROM thread_deltas WHERE thread_id=?"
                       " AND edition_date=?", (tid, OLD)).fetchone()["c"] == 1


def test_force_poison_is_the_disclosed_regression_and_repair_heals_it(migrated_con):
    """The cross-chip recovery loop, end to end. --force writes the OLD-stamped
    state AFTER the NEW-stamped one (latest_state is by id — the standing state
    REGRESSES, exactly the disclosed poison); the force warning AND the pass
    summary BOTH survive into bf.warnings (the clobber fix); NL-73 then sees the
    stale shape and heals the thread back to its latest live delta."""
    con = migrated_con
    tid = _seed_thread(con, "Iran War")
    _delta(con, tid, NEW)
    _state(con, tid, NEW)
    seed_briefing(con, OLD, [slot(1, mem=["Iran War"])],
                  narrative="Published OLD edition.")
    _seed_valid_brief(con, OLD, 1, _arc())

    bf = generate.run_memory_backfill(OLD, con=con, env=ENV, force=True,
                                      state_chat=_chat_for(OLD))
    assert bf.refused is False
    # both warning families survive (append, never replace):
    assert any("--force" in w or "force" in w.lower() for w in bf.warnings), bf.warnings
    assert any(w.startswith("memory: ledger:") for w in bf.warnings), bf.warnings
    # the poison landed exactly as disclosed: standing state regressed to OLD
    assert memory_core.latest_state(con, tid)["as_of_date"] == OLD
    # NL-73 names the shape...
    stale = memory_core.find_stale_state_threads(con)
    assert [(s["thread_id"], s["latest_delta_date"], s["state_as_of"])
            for s in stale] == [(tid, NEW, OLD)]
    # ...and heals it
    rep = generate.run_state_repair(all_threads=True, con=con, env=ENV,
                                    state_chat=_chat_for(NEW))
    assert rep.repaired and rep.repaired[0]["outcome"] == "written"
    assert memory_core.latest_state(con, tid)["as_of_date"] == NEW
    assert memory_core.find_stale_state_threads(con) == []


def test_cli_plumbs_force_and_defaults_it_off(tmp_paths, monkeypatch, capsys):
    """`newslens memory-backfill` passes force=False by default and force=True
    under --force — captured at the seam, no pipeline run."""
    seen = []

    def capture(date=None, force=False):
        seen.append({"date": date, "force": force})
        return generate.BackfillReport(date=date or "2026-07-16")

    monkeypatch.setattr(generate, "run_memory_backfill", capture)
    assert cli.main(["memory-backfill", "--date", OLD]) == 0
    assert cli.main(["memory-backfill", "--date", OLD, "--force"]) == 0
    capsys.readouterr()
    assert seen == [{"date": OLD, "force": False}, {"date": OLD, "force": True}]


# ===========================================================================
# NL-73 — find_stale_state_threads / run_state_repair
# ===========================================================================

def test_mixed_shapes_in_one_db_and_ordering(migrated_con):
    """All six shapes side by side; only the two genuinely stale threads
    surface, ordered by thread id."""
    con = migrated_con
    a = _seed_thread(con, "A stale delta-behind")     # stale: delta > state
    b = _seed_thread(con, "B current")                # not stale
    c = _seed_thread(con, "C no state")               # stale: absent state
    d = _seed_thread(con, "D superseded newest")      # not stale (live == state)
    _seed_thread(con, "E day-one no deltas")          # never listed
    f = _seed_thread(con, "F all superseded")         # never listed

    _delta(con, a, OLD); _state(con, a, OLD); _delta(con, a, NEW)
    _delta(con, b, NEW); _state(con, b, NEW)
    _delta(con, c, NEW)
    _delta(con, d, OLD); _state(con, d, OLD)
    bad = _delta(con, d, NEW)
    fix = _delta(con, d, OLD, what="Correction.")
    _supersede(con, bad, fix)
    f1 = _delta(con, f, OLD)
    f2 = _delta(con, f, NEW)
    _supersede(con, f1, f2)
    _supersede(con, f2, f1)                            # everything superseded

    stale = memory_core.find_stale_state_threads(con)
    assert [(s["thread_id"], s["latest_delta_date"], s["state_as_of"])
            for s in stale] == [(a, NEW, OLD), (c, NEW, "")]
    # thread_id scoping: a no-delta thread is NOT stale (day-one != stale)
    e_scoped = memory_core.find_stale_state_threads(
        con, thread_id=con.execute(
            "SELECT id FROM memory WHERE topic='E day-one no deltas'"
        ).fetchone()["id"])
    assert e_scoped == []


def test_budget_exhausts_mid_sweep_both_disclosed_spend_exact(migrated_con):
    """Two stale threads, a cap that covers exactly one rewrite: thread 1 pays
    and heals, thread 2 is skipped-budget — BOTH ride rep.repaired, the skip is
    warned, rep.spent_usd is thread 1's cost to the cent, and thread 2 stays
    verifiably stale (nothing half-written)."""
    con = migrated_con
    a = _seed_thread(con, "Alpha War")
    b = _seed_thread(con, "Bravo War")
    for tid in (a, b):
        _delta(con, tid, OLD)
        _state(con, tid, OLD)
        _delta(con, tid, NEW)

    # est computed exactly as run_state_repair -> rewrite_state will see it
    template = (paths.PROMPTS_DIR / "thread_state.txt").read_text(encoding="utf-8")
    entries = memory_core._live_entries(memory_core.ledger_for_thread(con, a))
    est = memory_core.estimate_state_usd(
        memory_core.render_state_prompt("Alpha War", NEW, entries, template))
    cap = round(est + 0.001, 6)
    cost1 = round(est + 0.0005, 6)      # leaves cap-cost1 ~= 0.0005 < est

    env = {"OPENAI_API_KEY": "sk-qa-fake", "BUDGET_CAP_USD_PER_RUN": f"{cap:.6f}"}
    rep = generate.run_state_repair(all_threads=True, con=con, env=env,
                                    state_chat=_chat_for(NEW, cost=cost1))
    assert rep.refused is False
    assert [(r["thread"], r["outcome"]) for r in rep.repaired] == [
        ("Alpha War", "written"), ("Bravo War", "skipped-budget")]
    assert rep.repaired[1]["usd"] == 0.0
    assert any("Bravo War" in w and "skipped-budget" in w for w in rep.warnings)
    assert rep.spent_usd == pytest.approx(cost1, abs=1e-9)
    # thread 1 healed, thread 2 verifiably still stale
    assert memory_core.latest_state(con, a)["as_of_date"] == NEW
    assert memory_core.latest_state(con, b)["as_of_date"] == OLD
    assert [s["thread_id"] for s in memory_core.find_stale_state_threads(con)] == [b]


def test_post_paid_failure_full_honesty_at_function_level(migrated_con, monkeypatch):
    """D2's degraded result is honest end to end: outcome is exactly 'failed'
    (never fake-success), the detail names the failure, NO state row lands, and
    the paid cost rides the result."""
    con = migrated_con
    tid = _seed_thread(con, "Iran War")
    _delta(con, tid, NEW)

    def boom(*a, **k):
        raise RuntimeError("post-chat step blew up")
    monkeypatch.setattr(memory_core, "_state_diff", boom)

    res = memory_core.rewrite_state(
        con, tid, "Iran War", NEW, None, "sk-x",
        "topic={topic} date={date}\n{ledger}\n",
        remaining_usd=1.0, chat=_chat_for(NEW, cost=0.02))
    assert res.outcome == "failed"
    assert "state write failed" in res.detail and "RuntimeError" in res.detail
    assert res.cost_usd == pytest.approx(0.02, abs=1e-9)
    assert con.execute("SELECT COUNT(*) c FROM thread_state WHERE thread_id=?",
                       (tid,)).fetchone()["c"] == 0    # prior state kept (none)


def test_post_paid_failure_in_the_sweep_is_disclosed_and_spend_kept(migrated_con, monkeypatch):
    """The same failure through run_state_repair: the repaired entry says
    'failed', a warning names the thread, spent_usd keeps the paid cost, and
    the thread remains verifiably stale."""
    con = migrated_con
    tid = _seed_thread(con, "Iran War")
    _delta(con, tid, NEW)

    def boom(*a, **k):
        raise RuntimeError("post-chat step blew up")
    monkeypatch.setattr(memory_core, "_state_diff", boom)

    rep = generate.run_state_repair(all_threads=True, con=con, env=ENV,
                                    state_chat=_chat_for(NEW, cost=0.02))
    assert [(r["thread"], r["outcome"]) for r in rep.repaired] == [
        ("Iran War", "failed")]
    assert any("Iran War" in w and "failed" in w for w in rep.warnings)
    assert rep.spent_usd == pytest.approx(0.02, abs=1e-9)
    assert [s["thread_id"] for s in memory_core.find_stale_state_threads(con)] == [tid]


def test_post_paid_failure_reaches_the_live_pass_durable_record(migrated_con, monkeypatch):
    """The LIVE memory pass (run_memory_pass): a post-paid 'failed' rewrite
    lands honestly in report.memory (state_rewrites outcome/usd) and the spend
    reaches report.steps — the generation-log feed diagnose aggregates."""
    con = migrated_con
    tid = _seed_thread(con, "Iran War")
    seed_briefing(con, TODAY, [slot(1, mem=["Iran War"])], narrative="Published.")
    _seed_valid_brief(con, TODAY, 1, _arc())

    def boom(*a, **k):
        raise RuntimeError("post-chat step blew up")
    monkeypatch.setattr(memory_core, "_state_diff", boom)

    report = generate.GenReport(date=TODAY, variant=generate.ACTIVE_VOICE)
    briefs = {1: {"brief": {"arc": _arc()}}}
    spent = generate.run_memory_pass(
        con, TODAY, "sk-qa-fake", cap=1.0, spent=0.0,
        briefs_by_slot=briefs, slots=[slot(1, mem=["Iran War"])],
        report=report, state_chat=_chat_for(TODAY, cost=0.02))
    srs = report.memory["state_rewrites"]
    assert [(s["outcome"], s["usd"]) for s in srs] == [("failed", 0.02)]
    assert spent == pytest.approx(0.02, abs=1e-9)
    assert report.steps and report.steps[-1]["step"] == "state_rewrites"


def test_post_paid_failure_is_disclosed_in_live_pass_warnings(migrated_con, monkeypatch):
    """RED (acceptance contract) — disclose-don't-crash applies to the outcome
    the D2 fix made reachable.

    Before this batch, a post-paid raise ESCAPED rewrite_state and the callers'
    containment produced a loud generic warning. The D2 fix converts it to a
    returned outcome 'failed' — but run_memory_pass's warning tuple
    ('stale', 'rejected', 'skipped-budget', 'skipped-no-ledger') was not
    extended, so the live pass now warns on every degraded outcome EXCEPT the
    one that cost money and failed. run_state_repair discloses it (any
    != 'written'); the live pass must too.

    Fix contract: add 'failed' to the warning-outcomes tuple in
    run_memory_pass (generate.py, the `sr.outcome in (...)` check).
    """
    con = migrated_con
    _seed_thread(con, "Iran War")
    seed_briefing(con, TODAY, [slot(1, mem=["Iran War"])], narrative="Published.")
    _seed_valid_brief(con, TODAY, 1, _arc())

    def boom(*a, **k):
        raise RuntimeError("post-chat step blew up")
    monkeypatch.setattr(memory_core, "_state_diff", boom)

    report = generate.GenReport(date=TODAY, variant=generate.ACTIVE_VOICE)
    generate.run_memory_pass(
        con, TODAY, "sk-qa-fake", cap=1.0, spent=0.0,
        briefs_by_slot={1: {"brief": {"arc": _arc()}}},
        slots=[slot(1, mem=["Iran War"])],
        report=report, state_chat=_chat_for(TODAY, cost=0.02))
    assert report.memory["state_rewrites"][0]["outcome"] == "failed"  # precondition
    assert any("Iran War" in w and "failed" in w for w in report.warnings), (
        "a paid, failed state write produced no warning line")


def test_scoped_refusal_names_the_thread(migrated_con):
    con = migrated_con
    tid = _seed_thread(con, "Iran War")            # no deltas: day-one, not stale
    rep = generate.run_state_repair(thread_id=tid, con=con, env=ENV,
                                    state_chat=_chat_for(NEW))
    assert rep.refused is True
    assert f"(thread {tid})" in rep.reason


def test_cli_selector_contract_is_argparse_enforced(tmp_paths, capsys):
    """`memory-repair-state` with neither or both selectors dies at the parser
    (exit 2), before any DB or spend surface is touched."""
    with pytest.raises(SystemExit) as e1:
        cli.main(["memory-repair-state"])
    assert e1.value.code == 2
    with pytest.raises(SystemExit) as e2:
        cli.main(["memory-repair-state", "--thread-id", "1", "--all"])
    assert e2.value.code == 2
    capsys.readouterr()


# ===========================================================================
# Photocopy — boundary, worst case, same-day split, the feed
# ===========================================================================

def test_jaccard_boundary_pinned_from_both_sides(migrated_con):
    """The disclosed threshold is >= 0.7 exactly: overlap 7/10 trips, 7/11 does
    not. Pinned with synthetic token sets so a threshold or comparator drift
    (>= to >, 0.7 to 0.75) fails here by name."""
    con = migrated_con
    tid = _seed_thread(con, "Iran War")
    base = "w1 w2 w3 w4 w5 w6 w7"
    at_boundary = base + " a1 a2 a3"          # |A|=10, inter 7, union 10 = 0.70
    under = base + " a1 a2 a3 a4"             # |A|=11, inter 7, union 11 ~= 0.636
    _delta(con, tid, NEW, signif=base)
    assert memory_core._significance_overlap(at_boundary, base) == pytest.approx(0.7)
    assert memory_core.photocopy_suspect_significance(
        con, tid, at_boundary, before_date=TODAY) is not None
    assert memory_core._significance_overlap(under, base) < 0.7
    assert memory_core.photocopy_suspect_significance(
        con, tid, under, before_date=TODAY) is None


def test_loose_paraphrase_under_threshold_writes_silently(migrated_con):
    """The disclosed under-flag direction on a REAL-shaped clause: a loose
    paraphrase of the record's delta-5 significance scores well under 0.7 and
    writes with zero suspects."""
    con = migrated_con
    tid = _seed_thread(con, "Iran War")
    sig5 = ("The conflict has escalated from economic disputes to direct "
            "military confrontation, affecting global oil supply and regional "
            "stability.")
    paraphrase = ("The confrontation has escalated from an economic dispute "
                  "into direct military conflict, threatening global oil "
                  "supplies and the region's stability.")
    _delta(con, tid, NEW, signif=sig5)
    assert memory_core._significance_overlap(paraphrase, sig5) < 0.7

    con.execute("INSERT INTO briefings (date, story_slots) VALUES (?, '[]')",
                (TODAY,))
    con.commit()
    rep = memory_core.write_deltas_for_edition(
        con, TODAY, None, {1: {"brief": {"arc": _arc(signif=paraphrase)}}},
        [{"slot": "1", "matched_memory": ["Iran War"]}])
    assert len(rep.written) == 1
    assert rep.photocopy_suspects == []


def test_short_clause_single_word_swap_is_the_known_false_positive(migrated_con):
    """CHARACTERIZATION (not an endorsement): the no-stopword-list decision's
    worst case, quantified. With s shared distinct tokens and one unique token
    per side, the score is s/(s+2) >= 0.7 as soon as s >= 5 — so ANY short
    clause differing by a single content word reads as a photocopy, INCLUDING a
    meaning inversion ('escalation' -> 'cooling', 7/9 = 0.778 here). Hyphenated
    negation is worse: 'de-escalation' tokenizes to {de, escalation}, a strict
    SUPERSET of the original (8/9 = 0.889). Cost today is one WARN note (the
    delta writes as-is; nothing dropped or rewritten). If this shape starts
    flagging real distinct developments, the cure is a minimum-token floor or a
    stopword-insensitive score — a conscious flip of the disclosed no-stopword
    decision, which this pin makes visible."""
    con = migrated_con
    tid = _seed_thread(con, "Iran War")
    esc = "It marks a major escalation of the standoff."
    cool = "It marks a major cooling of the standoff."         # inverted meaning
    de_esc = "It marks a major de-escalation of the standoff."  # hyphen negation
    _delta(con, tid, NEW, signif=esc)
    assert memory_core._significance_overlap(cool, esc) == pytest.approx(
        7 / 9, abs=1e-9)                                        # 0.778 >= 0.7
    hit = memory_core.photocopy_suspect_significance(
        con, tid, cool, before_date=TODAY)
    assert hit is not None and hit["score"] == round(7 / 9, 3)
    assert memory_core._significance_overlap(de_esc, esc) == pytest.approx(
        8 / 9, abs=1e-9)                                        # 0.889 >= 0.7
    assert memory_core.photocopy_suspect_significance(
        con, tid, de_esc, before_date=TODAY) is not None


def test_same_day_split_end_to_end_never_self_trips(migrated_con):
    """The sanctioned same-day split THROUGH the writer: two slots, one thread,
    near-identical significances, one pass. Slot 1's delta is already INSERTED
    when slot 2's check runs — strictly-earlier comparison is the only thing
    keeping the split from self-flagging. Both write; zero suspects."""
    con = migrated_con
    tid = _seed_thread(con, "Iran War")
    con.execute("INSERT INTO briefings (date, story_slots) VALUES (?, '[]')",
                (TODAY,))
    con.commit()
    sig = ("The conflict has escalated from economic disputes to direct "
           "military confrontation, affecting global oil supply and regional "
           "stability.")
    sig_twin = ("The conflict has moved beyond economic disputes to direct "
                "military confrontation, affecting global oil supply and "
                "regional stability.")
    briefs = {1: {"brief": {"arc": _arc(what="Strikes resumed.", signif=sig)}},
              2: {"brief": {"arc": _arc(what="A second carrier arrived.",
                                        signif=sig_twin)}}}
    slots = [{"slot": "1", "matched_memory": ["Iran War"]},
             {"slot": "2", "matched_memory": ["Iran War"]}]
    rep = memory_core.write_deltas_for_edition(con, TODAY, None, briefs, slots)
    assert len(rep.written) == 2                     # BUG-27 split honored
    assert rep.photocopy_suspects == []              # never a copy of ITSELF
    assert con.execute(
        "SELECT COUNT(*) c FROM thread_deltas WHERE thread_id=? AND"
        " edition_date=?", (tid, TODAY)).fetchone()["c"] == 2


def test_suspect_rides_report_memory_and_warnings(migrated_con):
    """The diagnose feed: run_memory_pass carries the suspect into
    report.memory['photocopy_suspects'] (durable, next to
    deltas_skipped_reasons) AND a WARN line naming thread, score, the prior
    edition, and write-as-is. The delta text itself is byte-identical."""
    con = migrated_con
    tid = _seed_thread(con, "Iran War")
    sig5 = ("The conflict has escalated from economic disputes to direct "
            "military confrontation, affecting global oil supply and regional "
            "stability.")
    sig7 = ("The conflict has moved beyond economic disputes to direct "
            "military confrontation, affecting global oil supply and regional "
            "stability.")
    _delta(con, tid, NEW, signif=sig5)
    seed_briefing(con, TODAY, [slot(1, mem=["Iran War"])], narrative="Published.")
    _seed_valid_brief(con, TODAY, 1, _arc(what="New strikes.", signif=sig7))

    report = generate.GenReport(date=TODAY, variant=generate.ACTIVE_VOICE)
    generate.run_memory_pass(
        con, TODAY, "sk-qa-fake", cap=1.0, spent=0.0,
        briefs_by_slot={1: {"brief": {"arc": _arc(what="New strikes.",
                                                  signif=sig7)}}},
        slots=[slot(1, mem=["Iran War"])],
        report=report, state_chat=_chat_for(TODAY))
    sus = report.memory["photocopy_suspects"]
    assert len(sus) == 1 and sus[0]["thread"] == "Iran War"
    assert sus[0]["against_edition"] == NEW and sus[0]["score"] >= 0.7
    warn = [w for w in report.warnings if "photocopy-suspect delta on" in w]
    assert len(warn) == 1
    # ...and the pass summary line counts it too
    assert any("1 photocopy-suspect" in w for w in report.warnings)
    assert "Iran War" in warn[0] and NEW in warn[0] and "as-is" in warn[0]
    # and the written delta is the model's bytes, untouched
    row = con.execute("SELECT significance FROM thread_deltas WHERE thread_id=?"
                      " AND edition_date=?", (tid, TODAY)).fetchone()
    assert row["significance"] == sig7


def test_summary_discloses_suspect_count_only_when_present():
    rep = memory_core.DeltaWriteReport()
    rep.written.append({"thread": "T", "thread_id": 1, "date": TODAY,
                        "verdict": "advances"})
    assert "photocopy-suspect" not in rep.summary()
    rep.photocopy_suspects.append({"thread": "T"})
    assert "1 photocopy-suspect" in rep.summary()


# ===========================================================================
# Collect-now schemas — 0015/0016
# ===========================================================================

def test_thread_closures_structurally_refuses_a_second_closure(migrated_con):
    """RED (acceptance contract) — 'one closure per thread' must be structural,
    not just polite.

    close_thread guards with a SELECT outside any transaction, then INSERTs.
    Two racing calls (the implementer's own concurrent-ish ask) both pass the
    SELECT and both INSERT: two 'dated facts' for one thread, and the 0015
    append-only triggers forbid DELETE — the duplicate is UNREPAIRABLE by
    design. The migration's own comment calls the triggers 'the structural
    backstop', but triggers backstop rewrites, not duplicates; only a UNIQUE
    bound does that. This test performs the exact writes an interleaved pair of
    close_thread calls would perform.

    Fix contract: 0015 gains
      CREATE UNIQUE INDEX IF NOT EXISTS uq_thread_closures_one_per_thread
          ON thread_closures (thread_id);
    (close_thread's named-refusal SELECT stays as the friendly path; the index
    is the backstop). Cheap NOW — 0015 is unshipped, the real DB sits at 0014;
    after a duplicate ever lands, append-only forbids cleanup.
    """
    con = migrated_con
    tid = _seed_thread(con, "Iran War")
    con.execute("INSERT INTO thread_closures (thread_id, reason, edition_date)"
                " VALUES (?, 'first', ?)", (tid, TODAY))
    con.commit()
    with pytest.raises(sqlite3.IntegrityError):
        con.execute("INSERT INTO thread_closures (thread_id, reason,"
                    " edition_date) VALUES (?, 'raced duplicate', ?)",
                    (tid, "2026-07-17"))
    assert con.execute("SELECT COUNT(*) c FROM thread_closures WHERE"
                       " thread_id=?", (tid,)).fetchone()["c"] == 1


def test_close_refusal_reads_committed_state_across_connections(migrated_con, tmp_path):
    """Sequential cross-connection honesty (the non-racing half of the
    concurrent-ish ask): a closure committed on connection A refuses the
    re-close on a FRESH connection B — the guard reads committed state, not
    anything connection-local."""
    from newslens import db as db_mod
    db_path = tmp_path / "closures-x-con.db"
    db_mod.migrate(db_path=db_path)
    con_a = db_mod.connect(db_path)
    con_b = db_mod.connect(db_path)
    try:
        _seed_thread(con_a, "Iran War")
        ok_a, _, _ = memory_core.close_thread(con_a, "Iran War", "ended", TODAY)
        assert ok_a is True
        ok_b, msg_b, cid_b = memory_core.close_thread(
            con_b, "Iran War", "again", "2026-07-17")
        assert ok_b is False and cid_b is None
        assert "already closed" in msg_b.lower() and TODAY in msg_b
        assert con_b.execute("SELECT COUNT(*) c FROM thread_closures"
                             ).fetchone()["c"] == 1
    finally:
        con_a.close()
        con_b.close()


def test_closure_verb_flips_nothing_pin(migrated_con):
    """CONSCIOUS-FLIP PIN (disclosed UX decision, 2026-07-16): `memory close`
    records the dated fact and nothing else — status stays untouched and the
    thread still TAKES DELTAS. When the closure FEATURE ships (render the dated
    line, halt further deltas), it must flip THIS test deliberately, with the
    behavior change on the record — never as a side effect."""
    con = migrated_con
    tid = _seed_thread(con, "Iran War")
    _delta(con, tid, NEW)
    ok, _, _ = memory_core.close_thread(con, "Iran War", "story ended", TODAY)
    assert ok is True
    # status untouched
    assert con.execute("SELECT status FROM memory WHERE id=?",
                       (tid,)).fetchone()["status"] == "active"
    # the ledger still moves — closure does NOT halt deltas yet
    con.execute("INSERT INTO briefings (date, story_slots) VALUES (?, '[]')",
                (TODAY,))
    con.commit()
    rep = memory_core.write_deltas_for_edition(
        con, TODAY, None, {1: {"brief": {"arc": _arc()}}},
        [{"slot": "1", "matched_memory": ["Iran War"]}])
    assert len(rep.written) == 1


def test_concept_explanations_delete_is_refused(migrated_con):
    """The DELETE trigger arm (the implementer's suite covers UPDATE only)."""
    con = migrated_con
    con.execute("INSERT INTO concept_explanations (concept,"
                " first_explained_edition) VALUES ('the strait', ?)", (TODAY,))
    con.commit()
    with pytest.raises(sqlite3.DatabaseError) as e:
        con.execute("DELETE FROM concept_explanations WHERE concept='the strait'")
    assert "append-only" in str(e.value)
