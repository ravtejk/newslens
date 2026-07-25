"""NL-75 Phase-2 battery — QA adversarial pass (2026-07-24).

Written by QA against the milestone contract, NOT by the implementer. These
cover the implementer's own declared-untested surface plus the defects the QA
pass found. Red tests here are ACCEPTANCE CRITERIA (team/ENGINEERING.md).

PROOF CLASS, stated honestly per the 2026-07-18 born-red ruling: the module did
not exist at HEAD ce5bb46, so nothing here is "born red" in the carried-
invariant sense. What the four RED tests below are is FAILING-AGAINST-THE-
LANDED-DIFF — they fail on the implementer's own working tree, right now, and
each names the fix that turns it green. That is the strongest proof class
available for a new-file milestone: the test bites the code as shipped.

  RED-1  test_cap_skip_must_not_ship_a_partial_read_set
  RED-2  test_blind_artifacts_must_not_leak_production_order_via_timestamp
  RED-3  test_pack_must_not_silently_drop_cells_past_the_label_alphabet
  RED-4  test_execute_reports_spend_when_the_record_moves_under_it

The remaining tests are CARRIED-INVARIANT (born-green) pins on behaviour that
is already correct and must not regress — labelled as such, never counted as
red.
"""

from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path

import pytest

from test_generate import A_DAY, seed_briefing, slot, stories_payload
from test_nl75_phase2_battery import (_guard_sanction, _seed, _inputs, _wire,
                                      _transport_tripwire, _data_snapshot,
                                      A_TOPIC)

from newslens import battery as _b, db, llm, moat_battery as mb, paths


# ---------------------------------------------------------------------------
# RED-1 — the cap-SKIP path ships a partial pre-registered test AND spends
# ---------------------------------------------------------------------------

def test_cap_skip_must_not_ship_a_partial_read_set(monkeypatch, capsys):
    """RED. FIX CONTRACT: _cmd_paid refuses --run when the cap SKIPPED any
    cell, exactly as it already refuses when any cell is BLOCKED.

    The harness already owns the principle and states it in its own refusal
    text: "A partial 2x2/pair set is not the pre-registered test". But it only
    enforces it for BLOCKED. A cap SKIP reaches the identical bad state by a
    different door: with the cap admitting only part of a 2x2, --run spends
    real money and produces ctx-on WITHOUT its ctx-off counterpart. That is a
    read-set with no ablation contrast in it — the reader ranks two renders of
    the SAME context arm, and the render-mode leak makes them near-identical
    prose. Money spent on a scientifically void cell.

    This is not hypothetical: `moat-battery plan` on the real record today
    prints exactly this shape for T2 on 2026-07-24 (2 cells SKIPped by the
    $2.50 cap, the ctx-on pair still PLANNED).

    Fix: treat `skipped` like `blocked` before calling _execute — refuse, name
    the incomplete read-set, and tell the operator to raise the cap or narrow
    --dates.
    """
    _guard_sanction(monkeypatch)
    _seed(with_ledger=True)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-qa")
    inputs = _inputs()

    # A cap that admits exactly ONE of the two drafts a 2x2 needs.
    prompt = mb.build_cell_prompt(inputs, mb.CONTEXT_ON, mb.FORM_SECTIONED)
    est = _b._arm_estimate(prompt, llm.SEATS["writer"].model)
    monkeypatch.setenv("BUDGET_CAP_USD_PER_RUN", f"{est * 1.5:.6f}")

    seen = _wire(monkeypatch, stories_payload(inputs["slots"]))
    rc = mb.main(["t2", "--dates", A_DAY, "--run"])
    out, err = capsys.readouterr()

    produced = sorted(
        p.name for p in
        (paths.DATA_DIR / "battery" / mb.ranking.local_today() / "phase2"
         / "t2" / A_DAY).glob("ctx-*")) if (
        paths.DATA_DIR / "battery" / mb.ranking.local_today() / "phase2"
        / "t2" / A_DAY).is_dir() else []

    assert rc == 1, (
        "a cap-truncated 2x2 was EXECUTED (rc=0) and spent %d live call(s); "
        "it produced %r — an incomplete read-set with no ablation contrast. "
        "The same condition reached via BLOCKED refuses. Produced: %s"
        % (len(seen), produced, out))
    assert not seen, "money was spent before the partial-set refusal"


# ---------------------------------------------------------------------------
# RED-2 — the render timestamp de-blinds the one axis claimed to be blind
# ---------------------------------------------------------------------------

_GENERATED_RE = re.compile(r"Generated (\d{4}-\d{2}-\d{2} \d{2}:\d{2}) UTC")


def test_blind_artifacts_must_not_leak_production_order_via_timestamp(
        tmp_path):
    """RED. FIX CONTRACT: artifacts copied into blind/ carry no per-cell
    render timestamp — normalise the `Generated ... UTC` stamp across a
    read-set (or render every cell of a set at one pinned timestamp).

    The sealed key asserts: "The CONTEXT axis (T2 ledger on/off) is genuinely
    blind." It is not. generate.assemble_narrative (and moat_battery
    ._window_line, which copies it) stamps WINDOW_LINE with datetime.now() AT
    RENDER TIME, and build_blind_pack copies each cell's prose into blind/
    VERBATIM. _execute renders cells in a fixed, code-known order — plan_cells
    yields contexts (on, off) — and each draft is a live writer call that takes
    minutes. So the two earliest stamps are context-ON and the two latest are
    context-OFF, and a reader who sorts the blind files by their own visible
    timestamp recovers the ablation arm deterministically.

    This is not a theoretical channel. The SHIPPED 2026-07-23 writer-battery
    blind pack carries three artifacts stamped 05:07, 05:14 and 05:22 UTC —
    real production order, readable off the blinded files.

    Note the harness already knows this line is unstable: furniture_parity()
    deliberately splits on "Generated " to neutralise it before comparing. The
    same neutralisation never reaches the blind copy.
    """
    root = tmp_path / "phase2"
    # Two cells of one read-set, rendered minutes apart — the real shape.
    for name, stamp in (("ctx-on__sectioned", "2026-07-24 05:07"),
                        ("ctx-off__sectioned", "2026-07-24 05:22")):
        d = root / "t2" / A_DAY / name
        d.mkdir(parents=True, exist_ok=True)
        (d / "narrative.md").write_text(
            "# NewsLens\n\nBody text.\n\n"
            "*Generated %s UTC. Covers items fetched X → Y. NewsLens sees "
            "only its configured sources within this window.*\n" % stamp,
            encoding="utf-8")

    res = mb.build_blind_pack(root, seed=5)
    blind = Path(res["blind_dir"])
    stamps = {}
    for p in sorted((blind / "SET-1").glob("*.md")):
        m = _GENERATED_RE.search(p.read_text(encoding="utf-8"))
        if m:
            stamps[p.name] = m.group(1)

    assert len(set(stamps.values())) <= 1, (
        "the blind artifacts carry DISTINCT render timestamps %r — sorting "
        "them recovers production order, and production order is context "
        "(on before off) by construction in _execute. The sealed key claims "
        "this axis is 'genuinely blind'." % stamps)


# ---------------------------------------------------------------------------
# RED-3 — the pack silently drops cells past 'ABCDEFGH'
# ---------------------------------------------------------------------------

def test_pack_must_not_silently_drop_cells_past_the_label_alphabet(tmp_path):
    """RED. FIX CONTRACT: build_blind_pack raises (or extends the alphabet)
    when a read-set holds more cells than _BLIND_LABELS has labels — it must
    never seal a subset while reporting success.

    zip(_BLIND_LABELS, members) truncates at 8. A read-set with more cells
    loses the excess with no error, no warning, and a cell count in the
    success line that does not match what exists on disk — the operator is
    told "N artifact(s)" and believes the pack is complete.
    """
    root = tmp_path / "phase2"
    names = [f"ctx-on__f{i}" for i in range(6)] + [f"ctx-off__f{i}"
                                                   for i in range(6)]
    for n in names:
        d = root / "t2" / A_DAY / n
        d.mkdir(parents=True, exist_ok=True)
        (d / "narrative.md").write_text(f"# {n}\n", encoding="utf-8")

    discovered = len(mb._discover_cells(root))
    assert discovered == 12                     # setup sanity

    res = mb.build_blind_pack(root, seed=1)
    assert res["cells"] == discovered, (
        "build_blind_pack discovered %d cells but sealed only %d — %d were "
        "dropped silently and the caller was told it succeeded"
        % (discovered, res["cells"], discovered - res["cells"]))


# ---------------------------------------------------------------------------
# RED-4 — a live generate moving the record under --run kills the spend report
# ---------------------------------------------------------------------------

def test_execute_reports_spend_when_the_record_moves_under_it(
        monkeypatch, capsys):
    """RED. FIX CONTRACT: _execute survives the record changing mid-run — a
    per-date failure is disclosed and the run still prints its spend line.

    The implementer flagged "--run path racing a LIVE generate" as untested.
    QA found TWO unguarded load_inputs call sites, and this test pins the one
    that costs money:

      site 1 (cheap)   _plan_test:1012 — catches generate.GenerateError only,
                       so sqlite3.OperationalError ("database is locked", the
                       exact signature of a concurrent generate write txn)
                       escapes as a traceback. Costs $0, ugly but harmless.
      site 2 (COSTLY)  _execute:1172 — load_inputs(con, date) sits OUTSIDE the
                       per-cell try/except. The plan has already passed, the
                       run has already CHARGED for every earlier date, and
                       then the record moves. The exception propagates out of
                       main(); the
                       "moat-battery: N cell(s) produced; charged $X" line is
                       BELOW the date loop and never executes. The principal
                       gets a stack trace and no idea what was billed.

    This test drives site 2 specifically: the date loads cleanly during
    planning and fails on the second load, which is the one _execute makes —
    exactly what a generate that commits between the two reads looks like.

    Fix: wrap the per-date load in the same disclose-and-continue handler the
    per-cell path already uses, so the spend summary always prints; and widen
    _plan_test's except to sqlite3.OperationalError for site 1.
    """
    _guard_sanction(monkeypatch)
    _seed(with_ledger=True)                     # A_DAY is good
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-qa")
    monkeypatch.setenv("BUDGET_CAP_USD_PER_RUN", "5.00")
    inputs = _inputs()

    # A second date that plans fine and then goes away under the run.
    con = db.connect()
    try:
        seed_briefing(con, "2026-07-06", [slot(1, mem=[A_TOPIC])],
                      narrative="Published.")
    finally:
        con.close()

    real_load = mb.load_inputs
    loads = {"2026-07-06": 0}

    def flaky(con_, date):
        if date in loads:
            loads[date] += 1
            if loads[date] > 1:          # plan read OK; _execute's read races
                raise sqlite3.OperationalError("database is locked")
        return real_load(con_, date)

    monkeypatch.setattr(mb, "load_inputs", flaky)
    seen = _wire(monkeypatch, stories_payload(inputs["slots"]))

    crashed = None
    try:
        rc = mb.main(["t1", "--dates", f"{A_DAY},2026-07-06", "--run"])
    except sqlite3.OperationalError as exc:
        crashed = exc
        rc = None
    out = capsys.readouterr().out

    assert crashed is None, (
        "the record moving under --run crashed _execute with a raw %s: %s — "
        "%d live call(s) had ALREADY been charged and the spend line never "
        "printed. Output the principal actually got:\n%s"
        % (type(crashed).__name__, crashed, len(seen), out))
    assert "charged $" in out, (
        "the run ended without disclosing what it charged: %s" % out)
    assert rc in (0, 1)


# ---------------------------------------------------------------------------
# RED-5 — the parity law is defeatable by a duplicate colophon
# ---------------------------------------------------------------------------

def test_furniture_parity_counts_lines_not_just_membership():
    """RED. FIX CONTRACT: furniture_parity compares MULTISETS —
    `collections.Counter(ka) == Counter(kb)` — not list membership.

    Content's furniture law is "every epistemic-furniture line present in one
    form is present in the other". The implementation asks only whether each
    line EXISTS in the other form:

        missing = [x for x in ka if x not in kb] + [x for x in kb if x not in ka]

    So if two stories carry textually identical colophons — same corroboration
    label, same outlets, same "Here for:" tags, which happens whenever two
    slots share an outlet set and a tag — a form that DROPS one of them still
    passes, because the surviving duplicate satisfies the membership test.
    A parity checker that can be defeated by a duplicate is not enforcing the
    law it documents.
    """
    col = "*Reported by 2 named outlets — Outlet A. Here for: AI regulation.*"
    both = "# H\n\n**S1**\n\n%s\n\n**S2**\n\n%s\n\n*caveat*\n" % (col, col)
    dropped = "# H\n\n**S1**\n\n%s\n\n**S2**\n\n*caveat*\n" % col

    ok, missing = mb.furniture_parity(both, dropped)
    assert not ok, (
        "a form that dropped one of two identical colophons passed the "
        "parity law (missing=%r) — the check tests set membership, not "
        "count" % missing)


# ---------------------------------------------------------------------------
# CARRIED-INVARIANT (born-green) pins — correct today, must not regress
# ---------------------------------------------------------------------------

def test_discover_cells_rejects_hand_edited_and_hostile_dir_names(tmp_path):
    """CARRIED-INVARIANT. The implementer's untested item (d): _discover_cells
    against hostile cell dir names. It is safe — the `ctx-` prefix + `__`
    split rejects junk, and `form` is only ever rendered as TEXT into the key
    file, never used as a path segment (blind files are named from
    _BLIND_LABELS). Path traversal is unreachable: '/' cannot appear in a
    directory name."""
    root = tmp_path / "phase2"
    for n in ("ctx-evil", "no-prefix__x", "ctx-on__sectioned",
              "__leading", "ctx-on__form__extra", ".hidden__x"):
        d = root / "t2" / A_DAY / n
        d.mkdir(parents=True, exist_ok=True)
        (d / "narrative.md").write_text("x\n", encoding="utf-8")

    found = {c["cell"] for c in mb._discover_cells(root)}
    assert found == {"ctx-on__sectioned", "ctx-on__form__extra"}
    for c in mb._discover_cells(root):
        assert "/" not in c["form"] and ".." not in Path(c["cell"]).parts


def test_compose_mode_refuses_at_zero_dollars_with_an_honest_message(
        monkeypatch, capsys):
    """CARRIED-INVARIANT. The implementer's untested item (f). The refusal
    must name the missing artifact, cost $0, write nothing, make no call, and
    say what to do instead."""
    _guard_sanction(monkeypatch)
    _seed(with_ledger=True)
    calls = _transport_tripwire(monkeypatch)
    before = _data_snapshot()
    assert not (paths.PROMPTS_DIR / mb.PROSE_FIRST_PROMPT).exists()

    rc = mb.main(["t2", "--dates", A_DAY, "--form-mode", "compose", "--run"])
    out, err = capsys.readouterr()

    assert calls == [] and _data_snapshot() == before      # $0, zero writes
    assert mb.PROSE_FIRST_PROMPT in out                    # names the artifact
    assert "Content-owned" in out                          # says whose job
    assert "--form-mode render" in out                     # says the way out
    # --run with a blocked cell must refuse rather than half-produce.
    assert rc == 1 and "BLOCKED" in err


def test_out_outside_the_data_dir_is_honoured_and_never_touches_data_dir(
        monkeypatch, capsys, tmp_path):
    """CARRIED-INVARIANT. The implementer's untested item (c). --out is an
    unvalidated operator-supplied path by design (it is how the principal
    parks a session elsewhere). What must hold: it is honoured exactly, and
    NOTHING lands in DATA_DIR when it is used."""
    _guard_sanction(monkeypatch)
    _seed(with_ledger=True)
    _transport_tripwire(monkeypatch)
    outside = tmp_path / "elsewhere" / "batteries"
    before = _data_snapshot()

    rc = mb.main(["t3", "--dates", A_DAY, "--out", str(outside), "--run"])
    assert rc == 0
    written = list(outside.rglob("concept-b-pack.md"))
    assert written, "--out was ignored"
    assert _data_snapshot() == before, "DATA_DIR was written despite --out"


def test_prose_units_mid_quote_break_is_cosmetic_on_the_real_corpus():
    """CARRIED-INVARIANT + measured verdict on the implementer's untested item
    (e). The inherited memory_core._sentences splitter breaks inside a
    multi-sentence quotation (lookbehind is [.!?], so `over." Then` does not
    split but `over. We` inside a quote does). QA measured the blast radius
    across every shipped edition in the real record: 2 fragments / 1692 units,
    both in a non-edition eartest notes file, 0 in any shipped edition.

    Verdict: COSMETIC, not material to §7. The worksheet deliberately
    over-fires and every hit is hand-adjudicated, and a split fragment still
    surfaces the same text to the adjudicator. This pin records the defect so
    a future change that makes it material is visible."""
    units = mb.prose_units(
        'She said, "The blockade remains. We are done." Analysts disagree.')
    # documented current behaviour: the quote IS broken mid-way
    assert units == ['She said, "The blockade remains.',
                     'We are done." Analysts disagree.']
    # and the consequence is bounded: it can only ever inflate, never hide
    assert all(u.strip() for u in units)


def test_pack_discloses_an_incomplete_read_set(tmp_path, monkeypatch, capsys):
    """CARRIED-INVARIANT (weak). The implementer's untested item (b): `pack`
    with an odd cell count, i.e. a failed arm. The pack seals whatever exists
    and PRINTS the per-set count, so an attentive operator can see "3 cell(s)"
    where a 2x2 wants 4 — but nothing flags it. This pin records that the
    count is at least visible; it is deliberately not a red test, because the
    disclosure exists even though it is weak."""
    _guard_sanction(monkeypatch)
    _seed(with_ledger=True)
    root = paths.DATA_DIR / "battery" / mb.ranking.local_today() / "phase2"
    for n in ("ctx-on__prose_first", "ctx-on__sectioned",
              "ctx-off__prose_first"):        # 3 of 4 — one arm failed
        d = root / "t2" / A_DAY / n
        d.mkdir(parents=True, exist_ok=True)
        (d / "narrative.md").write_text("x\n", encoding="utf-8")

    assert mb.main(["pack"]) == 0
    out = capsys.readouterr().out
    assert "3 cell(s)" in out
