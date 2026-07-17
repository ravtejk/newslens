"""QA extensions — live-contact fix loop #5 (gate-flip + memory-backfill), NL-63.

QA-owned adversarial pass against the loop-#5 contract, beyond the implementer's
own reds in test_memory_moat_backfill.py. Hammer surfaces, per the QA brief:

  1. moved_thread_ids semantics (HIGHEST BLAST RADIUS): the partial-new pass
     (one slot skips idempotent, another writes fresh -> MUST move exactly once),
     the A' slot-NULL seed-row shapes, same-day rephrased regeneration, and the
     falsification probe the implementer invited: every real ledger change must
     move its thread.
  2. Backfill money: sequential cap decrement inside one pass (the REAL 07-14
     shape: one slot, two matched threads), byte-level fold idempotency, the
     $0-noop when nothing moves, and the raise-after-pay fold window (pinned
     actual, fix contract in the docstring).
  3. Refusal honesty: named reasons, zero writes of ANY kind (deltas, state,
     token_cost bytes).
  4. CLI surface: printed cost reconciles with the folded token_cost; keyless
     offline degrade bills $0 through the REAL seam (mechanical spend-proof);
     malformed-date exits.
  5. The newly-reachable containment path: a --no-refresh record run whose
     memory pass raises post-persist must disclose-and-contain.
  6. The implementer's equivalence claim, verified byte-for-byte: the backfill's
     state prompt and written rows are identical to a live inline pass over the
     same persisted context.

Fully offline: the state seam is injected or the loopback guard proves the real
seam cannot reach the network. ZERO live LLM calls, $0. No source edits ride
with this file.
"""

from __future__ import annotations

import json
import re

import pytest

from newslens import db, generate, memory_core, paths
from test_generate import (ENV, A_DAY, compliant_script, fake_model,  # noqa: F401
                           seed_briefing, slot, stories_payload)


# --- local seeding helpers (mirror the implementer's file) -------------------

def _seed_thread(con, topic="Iran War"):
    now = "2026-07-01T00:00:00.000Z"
    cur = con.execute(
        "INSERT INTO memory (topic, status, status_changed_at, created_at,"
        " updated_at) VALUES (?, 'active', ?, ?, ?)", (topic, now, now, now))
    con.commit()
    return cur.lastrowid


def _seed_delta(con, tid, date, what, signif="Prior significance.", slot_n=None):
    """slot_n=None writes a slot-NULL row — the A' seed shape."""
    con.execute(
        "INSERT INTO thread_deltas (thread_id, edition_date, slot, verdict,"
        " what_happened, significance, cites_json)"
        " VALUES (?, ?, ?, 'advances', ?, ?, '[\"S1\"]')",
        (tid, date, slot_n, what, signif))
    con.commit()


def _arc(what="The U.S. reinstated the naval blockade in the strait.",
         signif="The dispute escalated from tolls to a shooting war.",
         cites=("S2", "S4")):
    return {"delta": "advances", "what_happened": what,
            "significance": signif, "cites": list(cites)}


def _brief_doc(arc=None):
    return {"brief": {"arc": arc or _arc()}}


def _seed_valid_brief(con, date, slot_n, arc=None):
    con.execute(
        "INSERT INTO analysis_briefs (date, slot, tier, status, brief_json,"
        " model, cost_usd) VALUES (?, ?, 'full', 'valid', ?, 'gpt-4o', 0.0)",
        (date, slot_n, json.dumps(_brief_doc(arc))))
    con.commit()


def _citing_chat(date, cost=0.02, text=None, log=None):
    hd = memory_core.human_date(date)

    def chat(key, prompt):
        if log is not None:
            log.append(prompt)
        return ({"state": text or f"The conflict is now open war ({hd})."}, cost)
    return chat


def _pass(con, date, briefs, slots, chat, cap=0.25, spent=0.0):
    report = generate.GenReport(date=date, variant="A")
    new_spent = generate.run_memory_pass(
        con, date, "k", cap=cap, spent=spent, briefs_by_slot=briefs,
        slots=slots, report=report, state_chat=chat)
    return report, new_spent


def _token_cost(con, date):
    return con.execute("SELECT token_cost FROM briefings WHERE date=?",
                       (date,)).fetchone()["token_cost"]


# ===========================================================================
# 1. moved_thread_ids blast radius
# ===========================================================================

class TestMovedSemantics:
    def test_partial_new_pass_moves_thread_and_reversions_state(self, migrated_con):
        """THE PARTIAL-NEW CASE (the :623 re-characterization leans on 'covered
        by the sanctioned-split tests', but those pin LEDGER rows only): pass 2
        sees the SAME thread in an idempotent skip (slot 1, on file) AND a fresh
        write (slot 3, new). The ledger genuinely moved, so the thread MUST move
        — exactly once — and the state MUST re-version. A skip in the same pass
        must not mask a real move."""
        con = migrated_con
        tid = _seed_thread(con, "Iran War")
        con.execute("INSERT INTO briefings (date, story_slots) VALUES (?, '[]')",
                    (A_DAY,))
        con.commit()
        texts = iter([f"It is a war now ({memory_core.human_date(A_DAY)}).",
                      f"It is a wider war now ({memory_core.human_date(A_DAY)})."])

        def chat(key, prompt):
            return ({"state": next(texts)}, 0.001)

        briefs1 = {1: _brief_doc(_arc(what="Strikes exchanged."))}
        slots1 = [{"slot": "1", "matched_memory": ["Iran War"]}]
        rep1, spent1 = _pass(con, A_DAY, briefs1, slots1, chat)
        assert rep1.memory["threads_moved"] == 1

        briefs2 = {1: _brief_doc(_arc(what="Strikes exchanged.")),
                   3: _brief_doc(_arc(what="Talks survived; waiver withdrawn.",
                                      cites=("S7",)))}
        slots2 = [{"slot": "1", "matched_memory": ["Iran War"]},
                  {"slot": "3", "matched_memory": ["Iran War"]}]
        rep2, spent2 = _pass(con, A_DAY, briefs2, slots2, chat)

        assert rep2.memory["deltas_written"] == 1     # slot 3 only
        assert rep2.memory["threads_moved"] == 1, (
            "a fresh slot-3 delta is a REAL ledger change — the same-pass "
            "idempotent skip at slot 1 must not mask the move")
        rows = con.execute("SELECT COUNT(*) c FROM thread_deltas"
                           " WHERE thread_id=?", (tid,)).fetchone()["c"]
        assert rows == 2
        srows = con.execute("SELECT state_text FROM thread_state WHERE thread_id=?"
                            " ORDER BY id", (tid,)).fetchall()
        assert len(srows) == 2, "a moved thread re-versions its standing state"
        assert "wider war" in srows[-1]["state_text"]
        assert spent2 == pytest.approx(0.001)          # exactly ONE rewrite in pass 2

    def test_skip_then_write_order_also_moves(self, migrated_con):
        """Order-of-iteration guard for the partial-new case: the skip arrives
        FIRST (slot 1 on file), the fresh write second (slot 3) — sorted slot
        order makes this the actual iteration order; the move must survive it."""
        con = migrated_con
        tid = _seed_thread(con, "Iran War")
        _seed_delta(con, tid, A_DAY, "Strikes exchanged.", slot_n=1)
        briefs = {1: _brief_doc(_arc(what="Strikes exchanged.")),
                  3: _brief_doc(_arc(what="A new diplomatic track opened.",
                                     cites=("S9",)))}
        slots = [{"slot": "1", "matched_memory": ["Iran War"]},
                 {"slot": "3", "matched_memory": ["Iran War"]}]
        rep = memory_core.write_deltas_for_edition(con, A_DAY, None, briefs, slots)
        assert rep.moved_thread_ids == [tid]
        assert len(rep.written) == 1
        assert any("already on file" in s for s in rep.skipped)

    def test_a_prime_slot_null_seed_same_clause_does_not_move(self, migrated_con):
        """A' seed shape, direction 1: a slot-NULL seed row for the SAME edition
        carrying the SAME what_happened — the slot key misses (NULL != 1) but
        the clause fallback catches it. No new row, no move, no spend."""
        con = migrated_con
        tid = _seed_thread(con, "Strait of Hormuz")
        _seed_delta(con, tid, A_DAY, "Transit fees imposed on shipping.",
                    slot_n=None)                       # the A' seed: slot NULL
        briefs = {1: _brief_doc(_arc(what="Transit fees imposed on shipping."))}
        slots = [{"slot": "1", "matched_memory": ["Strait of Hormuz"]}]
        rep = memory_core.write_deltas_for_edition(con, A_DAY, None, briefs, slots)
        assert rep.written == [] and rep.moved_thread_ids == []
        assert con.execute("SELECT COUNT(*) c FROM thread_deltas"
                           " WHERE thread_id=?", (tid,)).fetchone()["c"] == 1

    def test_a_prime_slot_null_seed_new_clause_writes_and_moves(self, migrated_con):
        """A' seed shape, direction 2 — the implementer's invited falsification
        probe: a slot-NULL seed exists for the edition, and the pass carries a
        genuinely NEW development (different clause, different slot). Neither
        idempotency key matches -> the delta lands -> the ledger changed -> the
        thread MUST move. A ledger change that failed to move would be the
        correctness loss the 'strictly a spend reduction' claim misses."""
        con = migrated_con
        tid = _seed_thread(con, "Strait of Hormuz")
        _seed_delta(con, tid, A_DAY, "Transit fees imposed on shipping.",
                    slot_n=None)
        briefs = {1: _brief_doc(_arc(what="The blockade was reinstated."))}
        slots = [{"slot": "1", "matched_memory": ["Strait of Hormuz"]}]
        rep = memory_core.write_deltas_for_edition(con, A_DAY, None, briefs, slots)
        assert len(rep.written) == 1
        assert rep.moved_thread_ids == [tid], (
            "delta written but thread not moved — state would go stale on a "
            "real ledger change (the correctness loss the claim excludes)")
        assert con.execute("SELECT COUNT(*) c FROM thread_deltas"
                           " WHERE thread_id=?", (tid,)).fetchone()["c"] == 2

    def test_same_day_rephrased_regen_still_dedups_and_moves_nothing(self, migrated_con):
        """M1 gate F regen-dedup survives the semantics change: a same-day
        re-analysis that REPHRASES slot 1's arc matches on (thread, date, slot),
        writes nothing, and now also moves nothing (no re-billed state)."""
        con = migrated_con
        tid = _seed_thread(con, "Iran War")
        _seed_delta(con, tid, A_DAY, "Strikes exchanged.", slot_n=1)
        briefs = {1: _brief_doc(_arc(what="An exchange of strikes occurred."))}
        slots = [{"slot": "1", "matched_memory": ["Iran War"]}]
        rep = memory_core.write_deltas_for_edition(con, A_DAY, None, briefs, slots)
        assert rep.written == [] and rep.moved_thread_ids == []
        assert con.execute("SELECT COUNT(*) c FROM thread_deltas"
                           " WHERE thread_id=?", (tid,)).fetchone()["c"] == 1

    def test_failed_state_rewrite_is_not_retried_by_rerun(self, migrated_con):
        """CONSCIOUS CONSEQUENCE, pinned (QA loop #5): under the new semantics a
        state rewrite that FAILS on the day its ledger moved is NOT retried by
        re-running the edition — the delta is on file, the repeat pass moves
        nothing, and the state stays stale until the thread's NEXT real move
        (when the full-ledger regeneration catches it up; the ledger entry
        itself is never lost). Under the OLD semantics the re-run re-fired the
        rewrite — recovery-by-rerun existed only as a side effect of the
        re-billing defect. Pinned so the tradeoff is on the record; NOTE: the
        BUG-34 containment message still advertises 're-run to backfill the
        moat', which is now false for the state half — reported to the gate as
        a wording defect (generate.py:2303, :1501-:1503)."""
        con = migrated_con
        tid = _seed_thread(con, "Iran War")
        con.execute("INSERT INTO briefings (date, story_slots) VALUES (?, '[]')",
                    (A_DAY,))
        con.commit()
        briefs = {1: _brief_doc()}
        slots = [{"slot": "1", "matched_memory": ["Iran War"]}]

        def failing_chat(key, prompt):
            raise RuntimeError("state model unreachable")

        rep1, _ = _pass(con, A_DAY, briefs, slots, failing_chat)
        assert any("stale" in w for w in rep1.warnings)
        assert con.execute("SELECT COUNT(*) c FROM thread_state").fetchone()["c"] == 0

        healthy_calls = []
        rep2, spent2 = _pass(con, A_DAY, briefs, slots,
                             _citing_chat(A_DAY, log=healthy_calls))
        assert healthy_calls == []                     # never retried
        assert spent2 == 0.0
        assert con.execute("SELECT COUNT(*) c FROM thread_state").fetchone()["c"] == 0
        assert rep2.memory["threads_moved"] == 0


# ===========================================================================
# 2. money — cap math, fold idempotency, $0 no-ops, the raise-after-pay window
# ===========================================================================

class TestBackfillMoney:
    def test_multi_thread_slot_cap_decrements_sequentially(self, migrated_con):
        """The REAL 2026-07-14 shape: ONE slot, TWO matched threads. The cap is
        checked per rewrite against (cap - spent-so-far): with cap == the price
        of exactly one rewrite, thread A writes and thread B is skipped-budget —
        while BOTH deltas (the $0 ledger) still land."""
        con = migrated_con
        tid_a = _seed_thread(con, "Strait of Hormuz")
        tid_b = _seed_thread(con, "Iran War")
        con.execute("INSERT INTO briefings (date, story_slots) VALUES (?, '[]')",
                    (A_DAY,))
        con.commit()
        briefs = {1: _brief_doc()}
        slots = [{"slot": "1",
                  "matched_memory": ["Strait of Hormuz", "Iran War"]}]
        rep, spent = _pass(con, A_DAY, briefs, slots,
                           _citing_chat(A_DAY, cost=0.02), cap=0.02)

        assert rep.memory["deltas_written"] == 2       # ledger is $0, always writes
        outcomes = [(s["thread"], s["outcome"]) for s in rep.memory["state_rewrites"]]
        assert outcomes == [("Strait of Hormuz", "written"),
                            ("Iran War", "skipped-budget")]
        assert spent == pytest.approx(0.02)
        assert rep.memory_usd == pytest.approx(0.02)
        srows = con.execute("SELECT thread_id FROM thread_state").fetchall()
        assert [r["thread_id"] for r in srows] == [tid_a]
        assert tid_b not in [r["thread_id"] for r in srows]
        assert any("budget" in w.lower() for w in rep.warnings)

    def test_backfill_second_run_leaves_token_cost_byte_identical(self, migrated_con):
        """Fold idempotency at the BYTE level: the second backfill folds
        nothing — briefings.token_cost is byte-for-byte what the first run
        left, with exactly one state_rewrites step ever."""
        con = migrated_con
        tid = _seed_thread(con, "Iran War")
        _seed_delta(con, tid, "2026-07-01", "Transit fees imposed on shipping.")
        slots = [slot(1, mem=["Iran War"])]
        seed_briefing(con, A_DAY, slots, narrative="Published edition of record.")
        _seed_valid_brief(con, A_DAY, 1)

        generate.run_memory_backfill(A_DAY, con=con, env=ENV,
                                     state_chat=_citing_chat(A_DAY, cost=0.02))
        tc_after_first = _token_cost(con, A_DAY)
        steps = [s["step"] for s in json.loads(tc_after_first)["steps"]]
        assert steps.count("state_rewrites") == 1

        rep2 = generate.run_memory_backfill(A_DAY, con=con, env=ENV,
                                            state_chat=_citing_chat(A_DAY, cost=0.02))
        assert rep2.memory_usd == 0.0
        assert _token_cost(con, A_DAY) == tc_after_first, (
            "second backfill must fold NOTHING — token_cost changed bytes")

    def test_backfill_with_no_matched_threads_is_zero_dollar_noop(self, migrated_con):
        """Valid briefs, but no slot matches any thread (slots 2-7 of the real
        07-14 edition): NOT a refusal — an honest $0 no-op. No deltas, no state,
        token_cost bytes untouched (no empty fold)."""
        con = migrated_con
        _seed_thread(con, "Iran War")                  # exists, but unmatched
        slots = [slot(1, mem=[])]
        seed_briefing(con, A_DAY, slots, narrative="Published edition of record.")
        _seed_valid_brief(con, A_DAY, 1)
        tc_before = _token_cost(con, A_DAY)

        rep = generate.run_memory_backfill(A_DAY, con=con, env=ENV,
                                           state_chat=_citing_chat(A_DAY))
        assert rep.refused is False
        assert rep.deltas_written == 0 and rep.threads_moved == 0
        assert rep.memory_usd == 0.0
        assert con.execute("SELECT COUNT(*) c FROM thread_deltas").fetchone()["c"] == 0
        assert con.execute("SELECT COUNT(*) c FROM thread_state").fetchone()["c"] == 0
        assert _token_cost(con, A_DAY) == tc_before

    def test_raise_after_pay_still_folds_the_paid_portion(self, migrated_con):
        """Gate Fix 1 (loop #5, was the pinned-actual fold-window gap): the
        memory pass raising AFTER a paid rewrite (e.g. a DB error on the NEXT
        thread) must still land the state_rewrites step — run_memory_pass
        appends it in a try/finally around the rewrite loop, so the caller's
        containment fold sees the paid portion and briefings.token_cost never
        under-reports money the CLI simultaneously prints."""
        con = migrated_con
        _seed_thread(con, "Strait of Hormuz")
        _seed_thread(con, "Iran War")
        slots = [slot(1, mem=["Strait of Hormuz", "Iran War"])]
        seed_briefing(con, A_DAY, slots, narrative="Published edition of record.")
        _seed_valid_brief(con, A_DAY, 1)

        real_rewrite = memory_core.rewrite_state
        seen = []

        def paying_then_bomb(con_, tid_, topic_, date_, briefing_id_, key_,
                             template_, remaining_usd, chat=None):
            if seen:
                raise RuntimeError("probe: DB fell over after the paid rewrite")
            seen.append(topic_)
            return real_rewrite(con_, tid_, topic_, date_, briefing_id_, key_,
                                template_, remaining_usd,
                                chat=_citing_chat(A_DAY, cost=0.02))

        import unittest.mock as mock
        with mock.patch.object(memory_core, "rewrite_state", paying_then_bomb):
            bf = generate.run_memory_backfill(A_DAY, con=con, env=ENV)

        assert bf.refused is False
        assert any("backfill pass failed" in w for w in bf.warnings)
        assert bf.memory_usd == pytest.approx(0.02)    # the money WAS spent...
        tc = json.loads(_token_cost(con, A_DAY))
        assert tc["total_usd"] == pytest.approx(0.021), (
            "gate Fix 1: the paid $0.02 must fold into token_cost even on "
            "the raise path (try/finally lands the step before propagation)")


# ===========================================================================
# 3. refusal honesty — named reasons, zero writes of any kind
# ===========================================================================

class TestRefusalHonesty:
    def test_empty_story_slots_refuses_named_and_writes_nothing(self, migrated_con):
        """An edition row whose story_slots is empty (rank refused/produced
        nothing): the backfill refuses with the rank-naming reason and touches
        NOTHING — no deltas, no state, token_cost bytes intact."""
        con = migrated_con
        _seed_thread(con, "Iran War")
        seed_briefing(con, A_DAY, [], narrative="Published, oddly.")
        tc_before = _token_cost(con, A_DAY)

        rep = generate.run_memory_backfill(A_DAY, con=con, env=ENV,
                                           state_chat=_citing_chat(A_DAY))
        assert rep.refused is True
        assert "rank" in rep.reason.lower()
        assert con.execute("SELECT COUNT(*) c FROM thread_deltas").fetchone()["c"] == 0
        assert con.execute("SELECT COUNT(*) c FROM thread_state").fetchone()["c"] == 0
        assert _token_cost(con, A_DAY) == tc_before

    def test_no_valid_brief_refusal_leaves_token_cost_untouched(self, migrated_con):
        """Tightens the implementer's arc-unrecoverable red: the refusal also
        never folds, never writes state — the edition row's bytes stay put."""
        con = migrated_con
        _seed_thread(con, "Iran War")
        slots = [slot(1, mem=["Iran War"])]
        seed_briefing(con, A_DAY, slots, narrative="Published, analysis never ran.")
        con.execute("INSERT INTO analysis_briefs (date, slot, tier, status,"
                    " brief_json, model, cost_usd) VALUES (?, 1, 'full',"
                    " 'rejected', '{}', 'gpt-4o', 0.0)", (A_DAY,))  # rejected only
        con.commit()
        tc_before = _token_cost(con, A_DAY)

        rep = generate.run_memory_backfill(A_DAY, con=con, env=ENV,
                                           state_chat=_citing_chat(A_DAY))
        assert rep.refused is True and "brief" in rep.reason.lower()
        assert con.execute("SELECT COUNT(*) c FROM thread_state").fetchone()["c"] == 0
        assert _token_cost(con, A_DAY) == tc_before


# ===========================================================================
# 4. CLI surface — printed money reconciles; keyless offline cannot spend
# ===========================================================================

class TestCliSurface:
    def _seed_sandbox_db(self, with_brief=True):
        """Seed the SANDBOX db (paths.DB_PATH — autouse-redirected) the way the
        CLI will find it."""
        db.migrate()
        con = db.connect()
        _seed_thread(con, "Iran War")
        slots = [slot(1, mem=["Iran War"])]
        seed_briefing(con, A_DAY, slots, narrative="Published edition of record.")
        if with_brief:
            _seed_valid_brief(con, A_DAY, 1)
        return con

    def test_cli_printed_spend_reconciles_with_folded_token_cost(
            self, tmp_paths, capsys, monkeypatch):
        """A NEW money-touching command's printed cost must reconcile with the
        durable record: patch the REAL default seam to a $0.02 fake, run the
        CLI, and require printed spend == the state_rewrites step folded into
        briefings.token_cost, to the cent and beyond."""
        from newslens import cli
        con = self._seed_sandbox_db()
        monkeypatch.setattr(memory_core, "_default_state_chat",
                            _citing_chat(A_DAY, cost=0.02))
        rc = cli.main(["memory-backfill", "--date", A_DAY])
        out = capsys.readouterr().out
        assert rc == 0
        m = re.search(r"state-rewrite spend \$([0-9.]+)", out)
        assert m, f"CLI must print the spend (got: {out!r})"
        printed = float(m.group(1))
        tc = json.loads(_token_cost(con, A_DAY))
        folded = [s for s in tc["steps"] if s["step"] == "state_rewrites"]
        assert len(folded) == 1
        assert printed == pytest.approx(folded[0]["usd"], abs=5e-5)
        assert tc["total_usd"] == pytest.approx(0.001 + 0.02)
        assert "deltas written: 1" in out and "threads moved: 1" in out
        con.close()

    def test_offline_backfill_bills_zero_and_degrades_stale(
            self, tmp_paths, capsys, monkeypatch):
        """Mechanical spend-proof for the new command: the REAL
        _default_state_chat runs but the state seat cannot transport, so the
        command completes rc 0, writes the $0 ledger, keeps the state stale-but-
        honest, prints $0.0000, and folds nothing. 2026-07-17 (option a): the
        state seat is Haiku/subscription now (keyless-OpenAI no longer starves
        it), so 'offline' means the claude binary is unavailable — check_lane
        raises LaneUnavailable BEFORE any attempt, rewrite_state degrades stale,
        and with no attempt billed the ledger folds nothing (exactly as the old
        keyless-OpenAI connect-refused path did)."""
        from newslens import cli
        monkeypatch.setenv("NEWSLENS_CLAUDE_BIN", "/nonexistent/claude-absent-xyz")
        con = self._seed_sandbox_db()
        tc_before = _token_cost(con, A_DAY)
        rc = cli.main(["memory-backfill", "--date", A_DAY])
        out = capsys.readouterr().out
        assert rc == 0
        assert "state-rewrite spend $0.0000" in out
        assert con.execute("SELECT COUNT(*) c FROM thread_deltas").fetchone()["c"] == 1
        assert con.execute("SELECT COUNT(*) c FROM thread_state").fetchone()["c"] == 0
        assert "stale" in out                           # disclosed, not hidden
        assert _token_cost(con, A_DAY) == tc_before     # $0 folds nothing
        con.close()

    @pytest.mark.parametrize("bad", ["2026-13-01", "2026-7-4", "yesterday"])
    def test_cli_rejects_malformed_dates_exit_2(self, tmp_paths, capsys, bad):
        from newslens import cli
        rc = cli.main(["memory-backfill", "--date", bad])
        assert rc == 2
        assert "YYYY-MM-DD" in capsys.readouterr().err


# ===========================================================================
# 5. the newly-reachable containment path (--no-refresh + BUG-34 wrapper)
# ===========================================================================

class TestNoRefreshContainment:
    def test_memory_pass_raise_on_no_refresh_run_is_contained(
            self, migrated_con, fake_model, monkeypatch):
        """The gate flip makes the post-persist memory block reachable on a
        --no-refresh record run for the first time — the BUG-34 wrapper must
        wrap it THERE too: a memory pass that raises post-persist discloses and
        contains; the published edition, artifact, and report all survive."""
        con = migrated_con
        _seed_thread(con, "Iran War")
        slots = [slot(1, mem=["Iran War"])]
        seed_briefing(con, A_DAY, slots)
        _seed_valid_brief(con, A_DAY, 1)
        fake_model.narrative = stories_payload(slots)
        fake_model.script = compliant_script(slots)

        def bomb(*a, **k):
            raise RuntimeError("memory pass exploded post-persist")

        monkeypatch.setattr(memory_core, "write_deltas_for_edition", bomb)
        rep = generate.run_generate(date=A_DAY, con=con, env=ENV, refresh=False)

        assert rep.sample is False
        assert any("memory pass failed after persist" in w for w in rep.warnings)
        row = con.execute("SELECT narrative_text, script_text FROM briefings"
                          " WHERE date=?", (A_DAY,)).fetchone()
        assert row["narrative_text"] and row["script_text"]   # edition published
        assert rep.artifact_path and paths.DATA_DIR.exists()
        assert con.execute("SELECT COUNT(*) c FROM thread_deltas").fetchone()["c"] == 0


# ===========================================================================
# 6. the equivalence claim, verified — backfill context == live pass context
# ===========================================================================

class TestContextEquivalence:
    def _seed_world(self, con, narrative=None):
        tid = _seed_thread(con, "Iran War")
        _seed_delta(con, tid, "2026-07-01", "Transit fees imposed on shipping.")
        slots = [slot(1, mem=["Iran War"])]
        seed_briefing(con, A_DAY, slots, narrative=narrative)
        _seed_valid_brief(con, A_DAY, 1)
        return tid

    def test_backfill_rows_and_state_prompt_byte_identical_to_live_pass(
            self, migrated_con, tmp_path, fake_model, monkeypatch):
        """The implementer's CONTEXT FIDELITY claim, checked byte-for-byte:
        two identically-seeded DBs; DB A runs the LIVE inline pass (a
        --no-refresh record run through run_generate), DB B runs the BACKFILL.
        The state prompt each seam received must be byte-identical, and the
        written thread_deltas / thread_state rows must match on every
        content column."""
        con_a = migrated_con
        db_b = tmp_path / "equiv-b.db"
        db.migrate(db_path=db_b)
        con_b = db.connect(db_b)
        try:
            self._seed_world(con_a)                     # live run writes narrative
            self._seed_world(con_b, narrative="Published edition of record.")

            prompts_a, prompts_b = [], []
            monkeypatch.setattr(
                memory_core, "_default_state_chat",
                _citing_chat(A_DAY, cost=0.02, log=prompts_a))
            fake_model.narrative = stories_payload([slot(1, mem=["Iran War"])])
            fake_model.script = compliant_script([slot(1, mem=["Iran War"])])
            generate.run_generate(date=A_DAY, con=con_a, env=ENV, refresh=False)

            bf = generate.run_memory_backfill(
                A_DAY, con=con_b, env=ENV,
                state_chat=_citing_chat(A_DAY, cost=0.02, log=prompts_b))
            assert bf.refused is False

            assert len(prompts_a) == 1 and len(prompts_b) == 1
            assert prompts_a[0] == prompts_b[0], (
                "backfill state prompt differs from the live pass — the "
                "byte-identical context claim fails")

            cols_d = ("thread_id, briefing_id, brief_id, edition_date, slot,"
                      " verdict, what_happened, significance, cites_json")
            rows_a = [tuple(r) for r in con_a.execute(
                f"SELECT {cols_d} FROM thread_deltas ORDER BY edition_date, id")]
            rows_b = [tuple(r) for r in con_b.execute(
                f"SELECT {cols_d} FROM thread_deltas ORDER BY edition_date, id")]
            assert rows_a == rows_b

            cols_s = ("thread_id, briefing_id, as_of_date, state_text,"
                      " cites_json, diff_json, model, cost_usd")
            srows_a = [tuple(r) for r in con_a.execute(
                f"SELECT {cols_s} FROM thread_state ORDER BY id")]
            srows_b = [tuple(r) for r in con_b.execute(
                f"SELECT {cols_s} FROM thread_state ORDER BY id")]
            assert srows_a == srows_b and len(srows_a) == 1
        finally:
            con_b.close()
