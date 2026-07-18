"""QA extensions for migration 0014 — the provenance bound (NL-69), adversarial pass.

Scope (QA dispatch 2026-07-16): the seams the implementer's 25 tests do not
reach. Sections map to the dispatch hammer:

  QA-A  the trap END-TO-END through the read site (repetition_antecedent_
        findings), incl. the EMPTY-after-exclusion interaction with D6-R;
  QA-B  the self-mark's honest operationalization: the real deltas-5-6 text,
        the fresh-event and record-backed negatives at the WRITE-PATH level,
        the same-day-sibling strict-before probe, the attributed-word ruling,
        the significance-clause leg, the empty-subject conservative default;
  QA-C  separability BOTH directions (through-0012 / through-0013). The
        implementer's _table_exists fix landed with NO red of its own — these
        are the reds, proven to bite by the comment-out procedure (QA probe
        P3, documented in the pass report);
  QA-D  the command's remaining refusals: absent table (and NEVER
        auto-migrates), absent DB, argparse-level bad grade, already-marked
        at CLI level;
  QA-E  the shared-regex move: identity + no-drift-surface pins;
  QA-F  0011-0014 apply-order on a real-shaped DB (the exact post-gate path:
        the real DB is attested through-0010), incl. the trap-live-then-marked
        flow the CoS will execute under decision B.

Real-DB facts asserted against here were attested READ-ONLY this pass
(2026-07-16): schema through 0010; 8 thread_deltas; deltas 5-6 are the only
rows in the ledger whose text mentions 'blockade'; their shared text is
"The U.S. launched new military strikes on Iran and reinstated a naval
blockade in the Strait of Hormuz." with cites ["S2", "S4", "P1"].
"""

from __future__ import annotations

from pathlib import Path

import pytest

from newslens import cli, db, generate, memory_core, paths

# House pattern (test_backlog_qa, test_m3_qa, ...): the offline-generation
# apparatus is imported from the suite that owns it.
from test_nl75_qa import (  # noqa: F401
    ENV, _dir_through, _payload, _script, _seed_edition, _slot, fake_model,
)

EDITION_AFTER = "2026-07-17"   # from this edition the 07-14 rows PREDATE

# The REAL deltas-5-6 text, verbatim (read-only attestation 2026-07-16).
REAL_POISON_WHAT = ("The U.S. launched new military strikes on Iran and "
                    "reinstated a naval blockade in the Strait of Hormuz.")
REAL_POISON_SIGNIF = ("The conflict has escalated from economic disputes to "
                      "direct military confrontation, affecting global oil "
                      "supply and regional stability.")


# --- local seeding helpers ---------------------------------------------------

def _thread(con, topic="Strait of Hormuz"):
    con.execute("INSERT INTO memory (topic, status) VALUES (?, 'active')", (topic,))
    return con.execute("SELECT id FROM memory WHERE topic = ?",
                       (topic,)).fetchone()["id"]


def _delta(con, tid, date, what, signif="", slot=1, cites='["S1"]'):
    con.execute(
        "INSERT INTO thread_deltas (thread_id, edition_date, slot, verdict,"
        " what_happened, significance, cites_json)"
        " VALUES (?, ?, ?, 'advances', ?, ?, ?)",
        (tid, date, slot, what, signif, cites))
    return con.execute(
        "SELECT id FROM thread_deltas ORDER BY id DESC LIMIT 1").fetchone()["id"]


def _mark(con, delta_id, provenance, reason="qa"):
    con.execute(
        "INSERT INTO thread_delta_provenance (delta_id, provenance, reason)"
        " VALUES (?, ?, ?)", (delta_id, provenance, reason))
    con.commit()


def _arc(what, signif, cites=("S2", "S4", "P1")):
    return {"delta": "advances", "what_happened": what, "significance": signif,
            "cites": list(cites)}


def _write_pass(con, briefs_by_slot, slots, date="2026-07-14"):
    return memory_core.write_deltas_for_edition(con, date, None,
                                                briefs_by_slot, slots)


# ===========================================================================
# QA-A. THE TRAP THROUGH THE READ SITE — repetition_antecedent_findings
#       (the implementer pinned has_predating_antecedent; the shipped surface
#       is the findings function the generation pass actually calls)
# ===========================================================================

def test_readsite_source_echo_prior_no_longer_licenses_the_word_it_echoed(
        migrated_con):
    """END-TO-END TRAP PIN. An edition written AFTER 07-14, whose thread's only
    'blockade' history is the source-echo-marked poison row, must FLAG a bare
    'reinstated ... blockade' — the finding the poisoned antecedent used to
    suppress. Strict before_date alone cannot catch this (the row predates)."""
    con = migrated_con
    tid = _thread(con)
    did = _delta(con, tid, "2026-07-14", REAL_POISON_WHAT, REAL_POISON_SIGNIF,
                 cites='["S2", "S4", "P1"]')
    _mark(con, did, "source-echo")
    stories = [{"headline": "Blockade",
                "lede": "Washington reinstated a naval blockade in response.",
                "why_it_matters": "x"}]
    slots = [{"slot": "1", "matched_memory": ["Strait of Hormuz"]}]
    findings = generate.repetition_antecedent_findings(
        con, stories, slots, EDITION_AFTER)
    assert any("reinstated" in f for f in findings), (
        "the marked poison row STILL licensed the word it echoed — the 0014 "
        "bound is not reaching the shipped read site")


def test_readsite_control_genuine_prior_still_licenses(migrated_con):
    """Control: identical prose, but the predating row is genuine (unmarked =
    record-established). No finding — the bound must not over-refuse."""
    con = migrated_con
    tid = _thread(con)
    _delta(con, tid, "2026-07-05", "The U.S. imposed a naval blockade.",
           "Passage was cut off.")
    stories = [{"headline": "Blockade",
                "lede": "Washington reinstated a naval blockade in response.",
                "why_it_matters": "x"}]
    slots = [{"slot": "1", "matched_memory": ["Strait of Hormuz"]}]
    findings = generate.repetition_antecedent_findings(
        con, stories, slots, EDITION_AFTER)
    assert findings == []


def test_readsite_external_synthesis_prior_also_refuses(migrated_con):
    """The Content-addendum fourth class at the shipped surface: a predating
    external-synthesis row (baseline-derived diction) does not license a bare
    repetition word either."""
    con = migrated_con
    tid = _thread(con)
    did = _delta(con, tid, "2026-07-13",
                 "Baseline: a blockade shaped the strait's 2019 crisis.")
    _mark(con, did, "external-synthesis", "thread-baseline entry-zero")
    stories = [{"headline": "Blockade",
                "lede": "Washington reinstated a naval blockade in response.",
                "why_it_matters": "x"}]
    slots = [{"slot": "1", "matched_memory": ["Strait of Hormuz"]}]
    findings = generate.repetition_antecedent_findings(
        con, stories, slots, EDITION_AFTER)
    assert any("reinstated" in f for f in findings)


def test_all_nonlicensing_prior_set_behaves_like_no_prior_in_both_branches(
        migrated_con):
    """EMPTY-after-exclusion (dispatch item 1): a prior set that is ENTIRELY
    non-licensing (source-echo + external-synthesis mixed) must behave exactly
    like an empty ledger in BOTH has_predating_antecedent branches — the
    subject-discriminated search AND the no-discriminator bool(prior) branch
    (the D6-R interaction: that branch licenses on 'any predating history',
    so a poisoned row surviving the filter would license EVERYTHING)."""
    con = migrated_con
    tid = _thread(con)
    d1 = _delta(con, tid, "2026-07-13", "reinstated the naval blockade")
    d2 = _delta(con, tid, "2026-07-14",
                "Baseline synthesis recounts the blockade history.", slot=2)
    _mark(con, d1, "source-echo")
    _mark(con, d2, "external-synthesis")
    # subject-discriminated branch
    assert memory_core.has_predating_antecedent(
        con, "Strait of Hormuz", {"blockade"}, EDITION_AFTER) is False
    # no-discriminator branch: all-marked prior == no prior
    assert memory_core.has_predating_antecedent(
        con, "Strait of Hormuz", set(), EDITION_AFTER) is False
    # ...and one UNMARKED unrelated row flips ONLY the no-discriminator branch
    # (real history exists again), never the blockade-subject search.
    _delta(con, tid, "2026-07-15", "Tanker queues lengthened at the strait.",
           slot=3)
    assert memory_core.has_predating_antecedent(
        con, "Strait of Hormuz", set(), EDITION_AFTER) is True
    assert memory_core.has_predating_antecedent(
        con, "Strait of Hormuz", {"blockade"}, EDITION_AFTER) is False


# ===========================================================================
# QA-B. THE SELF-MARK'S HONEST OPERATIONALIZATION (dispatch item 2)
#       The implementer's disclosed deviation: the dispatch sketch said
#       "every cite is edition-day", but the REAL poison rows carry a P1
#       cite — so the mark keys on rule-iii logic (repetition word with no
#       predating antecedent in the delta's own text). These tests verify
#       that operationalization is RIGHT, on the real shapes.
# ===========================================================================

def test_write_path_self_marks_the_REAL_deltas_5_6_text(migrated_con):
    """The exact real rows, verbatim (P1 cite included): the write path must
    self-mark them source-echo. This is the proof the implementer's rule-iii
    operationalization catches the shape the cite-based sketch would have
    MISSED (a P1-citing poison row has a non-edition-day cite)."""
    con = migrated_con
    _thread(con, "Strait of Hormuz")
    _thread(con, "Iran War")
    briefs = {1: {"brief": {"arc": _arc(REAL_POISON_WHAT, REAL_POISON_SIGNIF)}}}
    slots = [{"slot": 1, "matched_memory": ["Strait of Hormuz", "Iran War"]}]
    report = _write_pass(con, briefs, slots)
    assert len(report.written) == 2          # one delta per matched thread
    rows = con.execute(
        "SELECT p.provenance, p.reason FROM thread_delta_provenance p").fetchall()
    assert len(rows) == 2, (
        "the real deltas-5-6 shape did not self-mark on both threads")
    assert all(r["provenance"] == "source-echo" for r in rows)
    assert all("0014 self-mark" in r["reason"] for r in rows)


def test_write_path_leaves_fresh_event_unmarked_iran_closed_the_strait(
        migrated_con):
    """A FRESH-EVENT delta — new fact, no repetition word ('Iran closed the
    strait') — must never mark. Over-marking here would silently refuse the
    legitimate antecedent every future 'reopened/resumed' needs."""
    con = migrated_con
    _thread(con)
    briefs = {1: {"brief": {"arc": _arc(
        "Iran closed the strait after both sides traded strikes.",
        "A war over passage itself.")}}}
    slots = [{"slot": 1, "matched_memory": ["Strait of Hormuz"]}]
    _write_pass(con, briefs, slots)
    assert con.execute(
        "SELECT COUNT(*) FROM thread_delta_provenance").fetchone()[0] == 0


def test_write_path_leaves_record_backed_continuity_unmarked(migrated_con):
    """A repetition word WITH a genuine predating antecedent is honest
    continuity — record-established, no mark, licensing preserved."""
    con = migrated_con
    tid = _thread(con)
    _delta(con, tid, "2026-07-05", "The U.S. imposed a naval blockade.",
           "Passage was cut off.")
    briefs = {1: {"brief": {"arc": _arc(
        "The U.S. reinstated the naval blockade after a brief lull.",
        "Shipping stalled anew within hours.")}}}
    slots = [{"slot": 1, "matched_memory": ["Strait of Hormuz"]}]
    _write_pass(con, briefs, slots, date="2026-07-14")
    assert con.execute(
        "SELECT COUNT(*) FROM thread_delta_provenance").fetchone()[0] == 0


def test_same_day_sibling_delta_is_not_an_antecedent_strict_before(
        migrated_con):
    """STRICT-BEFORE PROBE (dispatch: 'the just-written row is excluded from
    its own antecedent search'). One write pass, one edition, two slots on the
    same thread: slot 1 writes the fresh 'imposed a naval blockade'; slot 2
    carries 'reinstated a naval blockade'. Slot 1's row is SAME-DAY — it must
    not license slot 2, so slot 2 still self-marks. (The row's own text can
    never license itself either: classify runs before its INSERT, and
    before_date is exclusive.)"""
    con = migrated_con
    _thread(con)
    briefs = {
        1: {"brief": {"arc": _arc("The U.S. imposed a naval blockade of "
                                  "shipping lanes.", "Passage was cut off.")}},
        2: {"brief": {"arc": _arc("The U.S. reinstated a naval blockade "
                                  "around the ports.", "Escalation resumed "
                                  "across the region.")}},
    }
    slots = [{"slot": 1, "matched_memory": ["Strait of Hormuz"]},
             {"slot": 2, "matched_memory": ["Strait of Hormuz"]}]
    report = _write_pass(con, briefs, slots, date="2026-07-14")
    assert len(report.written) == 2
    marks = con.execute(
        "SELECT d.slot, p.provenance FROM thread_delta_provenance p"
        " JOIN thread_deltas d ON d.id = p.delta_id").fetchall()
    assert [(m["slot"], m["provenance"]) for m in marks] == [(2, "source-echo")], (
        "same-day sibling licensing leak: slot 2's poison must self-mark even "
        "though slot 1 wrote 'blockade' earlier in the SAME pass (not predating)")


def test_attributed_repetition_word_still_self_marks_RULING(migrated_con):
    """ADVERSARIAL (dispatch: 'rule what's built, flag if wrong'). A delta
    whose repetition word is ATTRIBUTED in its own text ('officials said ...
    according to state media') still self-marks as built: classify_delta_
    provenance deliberately does not consult _is_source_attributed.

    QA RULING — CORRECT, and the asymmetry is the point: attribution makes the
    word legal to SHIP (read-site exemption, rule iii's middle state), but a
    said-fact is not a record-fact — letting an attributed claim LICENSE a
    future bare 'reinstated' would launder source diction into the record one
    hop later, the exact poison with an extra step. The mark bounds licensing
    only; the delta itself still ships, still appears in state/timeline.
    Cost of the mark is a warning on a future bare use (warn-grade surface,
    same conservative direction as D6-R); cost of not marking is unearned
    diction shipping unflagged. Flagged to the gate as a conscious semantics
    call — note marks are append-only, so a future policy change means
    relaxing classify for NEW deltas, not re-grading old marks. Pin the built
    behavior."""
    con = migrated_con
    grade = memory_core.classify_delta_provenance(
        con, "Strait of Hormuz",
        "Officials said the U.S. reinstated a naval blockade, according to "
        "state media.",
        "Escalation claims spread quickly.", "2026-07-14")
    assert grade == "source-echo"


def test_selfmark_reads_the_significance_clause_too(migrated_con):
    """The poison word arriving ONLY in significance still marks — both
    clauses are searched (the real rows carry it in what_happened; the next
    backfill may not)."""
    con = migrated_con
    _thread(con)
    briefs = {1: {"brief": {"arc": _arc(
        "The U.S. struck military sites across the coast.",
        "The strikes reinstated a naval blockade in practice.")}}}
    slots = [{"slot": 1, "matched_memory": ["Strait of Hormuz"]}]
    _write_pass(con, briefs, slots)
    row = con.execute(
        "SELECT provenance FROM thread_delta_provenance").fetchone()
    assert row is not None and row["provenance"] == "source-echo"


def test_selfmark_empty_subject_stays_unmarked_conservative_default(
        migrated_con):
    """A repetition word whose subject yields NO discriminating units ('It
    resumed.') must not self-mark — mirrors the read site's D6-R conservative
    default (an empty subject never decides either way at the write side;
    under-marking is recoverable by the supervised command)."""
    con = migrated_con
    _thread(con)
    grade = memory_core.classify_delta_provenance(
        con, "Strait of Hormuz", "It resumed.", "", "2026-07-14")
    assert grade is None


# ===========================================================================
# QA-C. SEPARABILITY, BOTH DIRECTIONS (dispatch item 3)
#       The implementer found+fixed the through-0012/0013 regression
#       (ledger_for_thread's provenance JOIN would die: 'no such table')
#       but landed NO pin. These are the reds; probe P3 in the pass report
#       proves they bite the _table_exists guard.
# ===========================================================================

def _partial_db(tmp_path, last_prefix):
    """A DB migrated 0001..last_prefix only, via the shipped .sql files."""
    mdir = _dir_through(tmp_path, [f"{i:04d}" for i in range(1, last_prefix + 1)])
    db_path = tmp_path / f"through-{last_prefix:04d}.db"
    db.migrate(db_path=db_path, migrations_dir=mdir)
    return db.connect(db_path)


@pytest.mark.parametrize("last", [12, 13], ids=["through-0012", "through-0013"])
def test_pre_0014_db_ledger_degrades_provenance_to_null(tmp_path, last):
    """[SEPARABILITY RED] On a DB without thread_delta_provenance the ledger
    read must not die — every row surfaces provenance=None (table-absence =
    record-established, exactly the row-absence default), and licensing on
    genuine history still works."""
    con = _partial_db(tmp_path, last)
    try:
        tid = _thread(con)
        _delta(con, tid, "2026-07-05", "The U.S. imposed a naval blockade.")
        con.commit()
        rows = memory_core.ledger_for_thread(con, tid)
        assert rows and all("provenance" in r and r["provenance"] is None
                            for r in rows)
        assert memory_core.has_predating_antecedent(
            con, "Strait of Hormuz", {"blockade"}, EDITION_AFTER) is True
    finally:
        con.close()


@pytest.mark.parametrize("last", [12, 13], ids=["through-0012", "through-0013"])
def test_pre_0014_db_write_path_completes_without_marking(tmp_path, last):
    """[SEPARABILITY RED] The write path on a pre-0014 DB: the poison arc
    still WRITES its delta (the ledger is 0010 machinery) but the self-mark is
    skipped entirely — no crash, no table conjured into existence."""
    con = _partial_db(tmp_path, last)
    try:
        _thread(con)
        briefs = {1: {"brief": {"arc": _arc(REAL_POISON_WHAT,
                                            REAL_POISON_SIGNIF)}}}
        slots = [{"slot": 1, "matched_memory": ["Strait of Hormuz"]}]
        report = _write_pass(con, briefs, slots)
        assert len(report.written) == 1
        assert con.execute(
            "SELECT COUNT(*) FROM thread_deltas").fetchone()[0] == 1
        assert con.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND"
            " name='thread_delta_provenance'").fetchone() is None
    finally:
        con.close()


def test_through_0012_db_generation_completes_rung_a_live(tmp_path, fake_model):
    """[SEPARABILITY RED — the deep cut, mirroring RED-9] A through-0012 DB
    (no watch_items, no provenance) still GENERATES: rung (a) reaches the
    writer, the watch register alone degrades with its disclosed warning, the
    edition persists. This is the run that dies with 'no such table:
    thread_delta_provenance' if the provenance JOIN loses its guard."""
    mdir = _dir_through(tmp_path, [f"{i:04d}" for i in range(1, 13)])
    db_path = tmp_path / "pre0014.db"
    db.migrate(db_path=db_path, migrations_dir=mdir)
    con = db.connect(db_path)
    try:
        tid = _thread(con)
        _delta(con, tid, "2026-07-05",
               "Iran offered special transit terms amid US fee objections",
               "the contest was over the terms of passage")
        _delta(con, tid, "2026-07-10",
               "Iran closed the strait after both sides traded strikes",
               "a war over passage itself")
        con.execute(
            "INSERT INTO thread_state (thread_id, as_of_date, state_text)"
            " VALUES (?, ?, ?)",
            (tid, "2026-07-10", "Escalated from fee dispute to closure."))
        slots = [_slot(1, mem=("Strait of Hormuz",)), _slot(2), _slot(3)]
        _seed_edition(con, "2026-07-14", slots)
        fake_model.narrative = _payload(slots)
        fake_model.script = _script(slots)
        rep = generate.run_generate(date="2026-07-14", con=con, env=ENV,
                                    refresh=False)
        assert rep.sample is False
        n_prompt = next(c for c in fake_model.calls if c["json_mode"])["prompt"]
        assert "MEMORY — the record for thread 'Strait of Hormuz'" in n_prompt
        row = con.execute("SELECT narrative_text FROM briefings WHERE date=?",
                          ("2026-07-14",)).fetchone()
        assert row["narrative_text"]
    finally:
        con.close()


def test_through_0013_db_generation_completes(tmp_path, fake_model):
    """[SEPARABILITY RED] Same run on a through-0013 DB (watch_items present,
    0014 absent) — the exact '0014 not yet applied' world."""
    mdir = _dir_through(tmp_path, [f"{i:04d}" for i in range(1, 14)])
    db_path = tmp_path / "pre0014b.db"
    db.migrate(db_path=db_path, migrations_dir=mdir)
    con = db.connect(db_path)
    try:
        tid = _thread(con)
        _delta(con, tid, "2026-07-10",
               "Iran closed the strait after both sides traded strikes",
               "a war over passage itself")
        slots = [_slot(1, mem=("Strait of Hormuz",)), _slot(2), _slot(3)]
        _seed_edition(con, "2026-07-14", slots)
        fake_model.narrative = _payload(slots)
        fake_model.script = _script(slots)
        rep = generate.run_generate(date="2026-07-14", con=con, env=ENV,
                                    refresh=False)
        assert rep.sample is False
        n_prompt = next(c for c in fake_model.calls if c["json_mode"])["prompt"]
        assert "MEMORY — the record for thread 'Strait of Hormuz'" in n_prompt
        row = con.execute("SELECT narrative_text FROM briefings WHERE date=?",
                          ("2026-07-14",)).fetchone()
        assert row["narrative_text"]
    finally:
        con.close()


# ===========================================================================
# QA-D. THE COMMAND'S REMAINING REFUSALS (dispatch item 4)
# ===========================================================================

def test_cli_mark_refuses_on_pre_0014_db_and_NEVER_auto_migrates(
        tmp_paths, tmp_path, capsys):
    """The command on a through-0013 DB refuses with the run-migrate hint —
    and applies NOTHING: a data-touching migration on the real DB is a
    principal checkpoint, never a side effect of a marking command."""
    mdir = _dir_through(tmp_path, [f"{i:04d}" for i in range(1, 14)])
    db.migrate(db_path=paths.DB_PATH, migrations_dir=mdir)
    rc = cli.main(["memory-mark-provenance", "--delta-id", "1",
                   "--provenance", "source-echo"])
    err = capsys.readouterr().err
    assert rc == 1
    assert "migration 0014 has not been applied" in err
    assert "newslens migrate" in err
    con = db.connect(paths.DB_PATH)
    try:
        applied = {r["filename"] for r in con.execute(
            "SELECT filename FROM schema_migrations").fetchall()}
        assert len(applied) == 13
        assert "0014_thread_delta_provenance.sql" not in applied
        assert con.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND"
            " name='thread_delta_provenance'").fetchone() is None
    finally:
        con.close()


def test_cli_mark_refuses_when_no_db_exists(tmp_paths, capsys):
    """No DB at all: refuse with the migrate hint, exit 1, create nothing."""
    assert not paths.DB_PATH.exists()
    rc = cli.main(["memory-mark-provenance", "--delta-id", "1",
                   "--provenance", "source-echo"])
    err = capsys.readouterr().err
    assert rc == 1
    assert "no database" in err
    assert not paths.DB_PATH.exists()   # the refusal did not conjure a DB


def test_cli_mark_rejects_a_bad_provenance_value_at_parse_time(
        tmp_paths, capsys):
    """--provenance is choices-bound: a bad value dies in argparse (exit 2)
    before any DB is touched — the CHECK constraint and PROVENANCE_VALUES
    refusal sit behind it as depth."""
    with pytest.raises(SystemExit) as exc:
        cli.main(["memory-mark-provenance", "--delta-id", "1",
                  "--provenance", "sourcey-echo"])
    assert exc.value.code == 2
    assert "invalid choice" in capsys.readouterr().err
    assert not paths.DB_PATH.exists()


def test_cli_mark_refuses_already_marked_and_still_prints_the_delta(
        tmp_paths, capsys):
    """CLI-level already-marked (the implementer pinned core-level only): the
    second mark exits 1, says REFUSED/already marked, and still prints the
    graded delta text so the operator sees what stands; the original mark is
    untouched."""
    assert cli.main(["migrate"]) == 0
    capsys.readouterr()
    con = db.connect()
    try:
        tid = _thread(con)
        did = _delta(con, tid, "2026-07-14", REAL_POISON_WHAT,
                     REAL_POISON_SIGNIF, cites='["S2", "S4", "P1"]')
        con.commit()
    finally:
        con.close()
    assert cli.main(["memory-mark-provenance", "--delta-id", str(did),
                     "--provenance", "source-echo", "--reason", "first"]) == 0
    capsys.readouterr()
    rc = cli.main(["memory-mark-provenance", "--delta-id", str(did),
                   "--provenance", "external-synthesis", "--reason", "second"])
    captured = capsys.readouterr()
    assert rc == 1
    assert "REFUSED" in captured.err and "already marked" in captured.err
    assert "reinstated a naval blockade" in captured.out
    con = db.connect()
    try:
        row = con.execute(
            "SELECT provenance, reason FROM thread_delta_provenance"
            " WHERE delta_id = ?", (did,)).fetchone()
        assert (row["provenance"], row["reason"]) == ("source-echo", "first")
    finally:
        con.close()


# ===========================================================================
# QA-E. THE SHARED-REGEX MOVE (dispatch item 5)
# ===========================================================================

def test_repetition_machinery_is_one_object_across_modules():
    """generate's names are the SAME objects as memory_core's — an import,
    not a copy. Drift between the read site's lexicon and the write side's
    self-mark is structurally impossible while this holds."""
    assert generate._REPETITION_RE is memory_core._REPETITION_RE
    assert generate._repetition_subject_units is memory_core._repetition_subject_units


def test_generate_owns_no_second_lexicon_definition():
    """No-drift-surface pin: generate.py must not re-grow its own definition
    (a later local `_REPETITION_RE = re.compile(...)` would shadow the import
    and silently fork the lexicon — the exact drift 0014 centralized away).
    memory_core defines each exactly once."""
    gen_src = Path(generate.__file__).read_text(encoding="utf-8")
    assert "_REPETITION_RE = re.compile" not in gen_src
    assert "def _repetition_subject_units" not in gen_src
    assert "_SUBJECT_WINDOW_CHARS = " not in gen_src
    core_src = Path(memory_core.__file__).read_text(encoding="utf-8")
    assert core_src.count("_REPETITION_RE = re.compile") == 1
    assert core_src.count("def _repetition_subject_units") == 1


# ===========================================================================
# QA-F. 0011-0014 APPLY-ORDER ON A REAL-SHAPED DB (dispatch item 6)
#       The real DB is attested through-0010 — this is the exact migration
#       the CoS will run post-gate, then the two decision-B marks.
# ===========================================================================

def test_migrations_0011_0014_apply_in_order_on_a_real_shaped_db(tmp_path):
    """Mirror of the post-gate path on a synthetic twin of the ATTESTED real
    shape (through-0010; two threads; the poison text verbatim on both, dated
    2026-07-14 slot 1 with the P1 cite; later 07-16 rows): migrate applies
    exactly 0011, 0012, 0013, 0014 in order; the trap is then LIVE (the
    unmarked poison licenses 'blockade' — why decision B exists); the two
    decision-B marks close it on both threads."""
    mdir = _dir_through(tmp_path, [f"{i:04d}" for i in range(1, 11)])
    db_path = tmp_path / "real-shaped.db"
    db.migrate(db_path=db_path, migrations_dir=mdir)
    con = db.connect(db_path)
    try:
        iran = _thread(con, "Iran War")
        hormuz = _thread(con, "Strait of Hormuz")
        _delta(con, hormuz, "2026-07-05",
               "Iran's envoy to China offered passage terms.", slot=None)
        _delta(con, hormuz, "2026-07-06",
               "Khamenei's funeral procession ran through Tehran.", slot=None)
        _delta(con, hormuz, "2026-07-10",
               "The U.S. and Iran exchanged strikes across 90 sites.", slot=1)
        _delta(con, hormuz, "2026-07-10",
               "Technical talks survived the strikes.", slot=3)
        poison_hormuz = _delta(con, hormuz, "2026-07-14", REAL_POISON_WHAT,
                               REAL_POISON_SIGNIF, slot=1,
                               cites='["S2", "S4", "P1"]')
        poison_iran = _delta(con, iran, "2026-07-14", REAL_POISON_WHAT,
                             REAL_POISON_SIGNIF, slot=1,
                             cites='["S2", "S4", "P1"]')
        _delta(con, hormuz, "2026-07-16",
               "The US launched fresh military strikes on Iranian targets.",
               slot=1)
        _delta(con, iran, "2026-07-16",
               "The US launched fresh military strikes on Iranian targets.",
               slot=1)
        con.commit()
    finally:
        con.close()

    applied = db.migrate(db_path=db_path)   # full shipped migrations dir
    # 0011-0014 are the memory-core set under test; 0015/0016 are the later
    # collect-now schemas (ruling C); 0018 is the arc-line column (2026-07-18) —
    # all apply in lexicographic order.
    assert [f[:4] for f in applied] == ["0011", "0012", "0013", "0014",
                                        "0015", "0016", "0017", "0018"]

    con = db.connect(db_path)
    try:
        # The trap is LIVE on real-shaped data before marking: from any
        # edition after 07-14 the poison is a predating 'antecedent'.
        for topic in ("Strait of Hormuz", "Iran War"):
            assert memory_core.has_predating_antecedent(
                con, topic, {"blockade"}, EDITION_AFTER) is True
        # Decision B, both commands' core:
        for did in (poison_hormuz, poison_iran):
            ok, msg, row = memory_core.mark_delta_provenance(
                con, did, "source-echo", "07-14 backfill echoed source diction")
            assert ok is True
            assert row["what_happened"] == REAL_POISON_WHAT
        # The trap is CLOSED, and only 'blockade' licensing changed:
        for topic in ("Strait of Hormuz", "Iran War"):
            assert memory_core.has_predating_antecedent(
                con, topic, {"blockade"}, EDITION_AFTER) is False
        # genuine history still licenses its own subjects (07-16 strikes row
        # is unmarked record-established):
        assert memory_core.has_predating_antecedent(
            con, "Iran War", {"strikes"}, EDITION_AFTER) is True
    finally:
        con.close()


def test_selfmark_checks_every_match_not_just_the_first(migrated_con):
    """[GATE FIX-1 RED] TWO continuity words in ONE clause, the FIRST
    record-backed, the SECOND unsupported: the delta must still self-mark.
    _REPETITION_RE.search stopped at the licensed first match and the poison
    shipped unmarked — the unmarked row then licensed a future bare
    're-imposed ... sanctions' (the 5-6 trap, one word over)."""
    con = migrated_con
    tid = _thread(con)
    _delta(con, tid, "2026-07-05", "Nuclear talks began in Vienna.")
    grade = memory_core.classify_delta_provenance(
        con, "Strait of Hormuz",
        "Diplomats resumed talks in Vienna while Washington re-imposed "
        "sweeping oil sanctions.", "", "2026-07-14")
    assert grade == "source-echo"
