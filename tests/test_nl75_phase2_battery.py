"""NL-75 Phase-2 battery harness — implementer contract pins (2026-07-24).

Scope: the T1/T2/T3 harness in src/newslens/moat_battery.py + scripts/
moat-battery. QA owns the adversarial pass; these are the contract tests the
milestone ships with (team/ENGINEERING.md: "each milestone ships with tests for
its contract").

PROOF CLASS — stated honestly per the 2026-07-18 born-red ruling: this file
tests a module that DID NOT EXIST at HEAD ce5bb46, so none of it can be "born
red" in the carried-invariant sense (a HEAD run is a collection error, not a
meaningful failure). What IS proof-class here is LIVENESS: the four
enforcement surfaces below were each broken in source and the named test(s)
flipped red, then the source was restored byte-identical (sha256 verified) —
recorded in the implementer report:

  * the degenerate-2x2 guard   -> test_degenerate_2x2_is_blocked_at_zero_cost
    (broken: the plan priced $0.8498 of arms comparing a thing against itself)
  * the ablation seam itself   -> test_ablation_strips_only_the_rung_a_wire
    (broken: 6 tests red, and the degenerate guard then blocked the whole 2x2)
  * the blind-pack cleanliness -> test_blind_pack_assert_catches_contamination
    (broken: DID NOT RAISE. Note test_blind_pack_carries_no_manifest asserts
    the filesystem DIRECTLY, so it is a second, independent net over the same
    property rather than a pin on the assertion helper.)
  * the live cap gate          -> test_execute_cannot_produce_more_cells_than_
    the_plan_disclosed. This one caught a REAL bug during the build: _execute
    originally gated on spend-so-far rather than on the pre-call estimate the
    plan printed, so a run could produce more cells — and spend more money —
    than the dry run the principal approved. Reverted to the old gate, the run
    made 2 calls where the plan disclosed 1.

Offline by construction: no key, no network, no real paths. The autouse
conftest guards (scrub_env, sandbox_paths, loopback_only_network,
real_state_tripwire) stand under everything here; a transport tripwire and a
recursive DATA_DIR snapshot back the zero-calls/zero-writes claims directly.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import re
import urllib.request
from pathlib import Path

import pytest

from conftest import anthropic_envelope, anthropic_sse_bytes
from test_generate import A_DAY, seed_briefing, slot, stories_payload

from newslens import db, generate, llm, memory_core, moat_battery as mb, paths

A_TOPIC = "Strait of Hormuz"
PRIOR = "2026-07-01"          # strictly before A_DAY (2026-07-05)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _guard_sanction(monkeypatch):
    """moat_battery.main() self-sanctions real paths (a real entrypoint, like
    cli/doctor/battery). Pin the flag so the sanction cannot leak past a test."""
    monkeypatch.setattr(paths, "_REAL_PATHS_ALLOWED", paths._REAL_PATHS_ALLOWED)


def _transport_tripwire(monkeypatch):
    calls = []

    def no_http(req, timeout=None):
        calls.append(("http", req.full_url))
        raise AssertionError("HTTP transport reached: " + req.full_url)

    def no_spawn(*a, **k):
        calls.append(("spawn", a))
        raise AssertionError("subprocess spawned")

    monkeypatch.setattr(urllib.request, "urlopen", no_http)
    monkeypatch.setattr(llm.subprocess, "run", no_spawn)
    return calls


def _data_snapshot():
    root = paths.DATA_DIR
    if not Path(root).exists():
        return frozenset()
    return frozenset((str(p), p.stat().st_size, p.stat().st_mtime_ns)
                     for p in Path(root).rglob("*") if p.is_file())


def _seed(date=A_DAY, with_ledger=True, slots=None):
    """Migrate + seed the SANDBOXED record with a briefing row for `date`.
    `with_ledger` also seeds a thread + a strictly-prior dated delta, which is
    what makes the 2x2's context-ON cell differ from context-OFF."""
    if slots is None:
        mem = [A_TOPIC] if with_ledger else []
        slots = [slot(1, mem=mem), slot(2)]
    db.migrate()
    con = db.connect()
    try:
        seed_briefing(con, date, slots, narrative="Published.")
        if with_ledger:
            now = "2026-06-30T00:00:00.000Z"
            cur = con.execute(
                "INSERT INTO memory (topic, status, status_changed_at,"
                " created_at, updated_at) VALUES (?, 'active', ?, ?, ?)",
                (A_TOPIC, now, now, now))
            tid = cur.lastrowid
            con.execute(
                "INSERT INTO thread_deltas (thread_id, edition_date, verdict,"
                " what_happened, significance, cites_json)"
                " VALUES (?, ?, 'advances', ?, ?, ?)",
                (tid, PRIOR, "Transit fees became the dispute.",
                 "It moved the standoff to economics.", json.dumps(["S1"])))
            con.commit()
    finally:
        con.close()
    return slots


def _inputs(date=A_DAY):
    con = db.connect_readonly()
    try:
        return mb.load_inputs(con, date)
    finally:
        con.close()


# ---------------------------------------------------------------------------
# 1. The ablation seam — the one wire the 2x2 actually varies
# ---------------------------------------------------------------------------

def test_ablation_strips_only_the_rung_a_wire():
    """LIVENESS PIN. Context-ON carries the rung-(a) MEMORY block into the
    writer prompt; context-OFF does not — and NOTHING else moves. Break
    ablate_inputs (stop clearing thread_ledger) and this test bites: the two
    prompts become identical and the assertion on the OFF side fails."""
    _seed(with_ledger=True)
    inputs = _inputs()
    on = mb.build_cell_prompt(inputs, mb.CONTEXT_ON, mb.FORM_SECTIONED)
    off = mb.build_cell_prompt(inputs, mb.CONTEXT_OFF, mb.FORM_SECTIONED)

    anchor = f"MEMORY — the record for thread {A_TOPIC!r}"
    assert anchor in on
    assert anchor not in off
    assert "Transit fees became the dispute." in on
    assert "Transit fees became the dispute." not in off
    # Everything outside the memory block is untouched: removing exactly the
    # memory lines from the ON prompt reproduces the OFF prompt.
    stripped = "\n".join(
        ln for ln in on.splitlines()
        if not (ln.startswith("MEMORY —") or ln.startswith("standing state")
                or ln.startswith("this thread's record so far")
                or ln.startswith("  * ")))
    off_stripped = "\n".join(
        ln for ln in off.splitlines() if not ln.startswith("  * "))
    assert stripped == off_stripped


def test_ablate_inputs_never_mutates_the_caller_s_inputs():
    """The ON cell and the RENDER both read the original inputs — an in-place
    ablation would silently strip the ON cell too (and the colophon with it)."""
    _seed(with_ledger=True)
    inputs = _inputs()
    before = inputs["slots"][0]["thread_ledger"]
    assert before.strip()
    off = mb.ablate_inputs(inputs)
    assert off["slots"][0]["thread_ledger"] == ""
    assert inputs["slots"][0]["thread_ledger"] == before      # untouched
    assert off["slots"][0]["matched_tags"] == inputs["slots"][0]["matched_tags"]


def test_ablate_rejects_an_unknown_field():
    _seed(with_ledger=True)
    with pytest.raises(ValueError) as exc:
        mb.ablate_inputs(_inputs(), ("ledger", "nonsense"))
    assert "nonsense" in str(exc.value)


def test_ledger_coverage_counts_slots_carrying_memory():
    _seed(with_ledger=True)
    have, total = mb.ledger_coverage(_inputs())
    assert (have, total) == (1, 2)


# ---------------------------------------------------------------------------
# 2. The degenerate-cell guard — the money guard of the 2x2
# ---------------------------------------------------------------------------

def test_degenerate_2x2_is_blocked_at_zero_cost(monkeypatch, capsys):
    """LIVENESS PIN. An edition with no ledger content builds an IDENTICAL
    prompt on both sides of the ablation, so the 2x2 would spend real money
    comparing a thing against itself. Break ablation_is_degenerate (return
    False) and this test bites: the cells plan instead of blocking."""
    _guard_sanction(monkeypatch)
    _seed(with_ledger=False)
    calls = _transport_tripwire(monkeypatch)
    before = _data_snapshot()

    rc = mb.main(["t2", "--dates", A_DAY])
    out = capsys.readouterr().out
    assert rc == 0
    assert calls == [] and _data_snapshot() == before
    assert "DEGENERATE 2x2" in out
    assert "BLOCKED" in out
    assert "0 cell(s) planned" in out
    assert "est total charged $0.0000" in out


def test_non_degenerate_2x2_plans_all_four_cells(monkeypatch, capsys):
    _guard_sanction(monkeypatch)
    _seed(with_ledger=True)
    calls = _transport_tripwire(monkeypatch)
    monkeypatch.setenv("BUDGET_CAP_USD_PER_RUN", "5.00")

    rc = mb.main(["t2", "--dates", A_DAY])
    out = capsys.readouterr().out
    assert rc == 0 and calls == []
    for cell in ("ctx-on__prose_first", "ctx-on__sectioned",
                 "ctx-off__prose_first", "ctx-off__sectioned"):
        assert cell in out
    assert "4 cell(s) planned" in out
    assert "DRY RUN" in out


def test_render_mode_prices_two_drafts_not_four(monkeypatch, capsys):
    """In render mode both forms share ONE draft, so a 2x2 costs 2 calls per
    edition, not 4 — and the plan says which cells ride a shared draft."""
    _guard_sanction(monkeypatch)
    _seed(with_ledger=True)
    _transport_tripwire(monkeypatch)
    monkeypatch.setenv("BUDGET_CAP_USD_PER_RUN", "5.00")
    mb.main(["t2", "--dates", A_DAY])
    out = capsys.readouterr().out
    assert out.count("shares the draft above (render mode)") == 2


# ---------------------------------------------------------------------------
# 3. Dry-run default: zero calls, zero writes, everywhere
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("argv", [
    ["t1", "--dates", A_DAY],
    ["t2", "--dates", A_DAY],
    ["t3", "--dates", A_DAY],
    ["plan", "--dates", A_DAY],
])
def test_dry_run_makes_zero_calls_and_zero_writes(monkeypatch, capsys, argv):
    _guard_sanction(monkeypatch)
    _seed(with_ledger=True)
    calls = _transport_tripwire(monkeypatch)
    before = _data_snapshot()

    rc = mb.main(argv)
    out = capsys.readouterr().out
    assert rc == 0
    assert calls == []                          # ZERO transport
    assert _data_snapshot() == before           # ZERO writes
    assert "DRY RUN" in out


def test_plan_prints_the_session_total_and_the_per_invocation_caveat(
        monkeypatch, capsys):
    _guard_sanction(monkeypatch)
    _seed(with_ledger=True)
    _transport_tripwire(monkeypatch)
    mb.main(["plan", "--dates", A_DAY])
    out = capsys.readouterr().out
    assert "SESSION TOTAL est charged" in out
    assert "PER INVOCATION" in out
    assert "T3: $0.00" in out


def test_run_refuses_keyless_before_any_spend(monkeypatch, capsys):
    _guard_sanction(monkeypatch)
    _seed(with_ledger=True)
    calls = _transport_tripwire(monkeypatch)
    before = _data_snapshot()
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setenv("BUDGET_CAP_USD_PER_RUN", "5.00")

    rc = mb.main(["t1", "--dates", A_DAY, "--run"])
    err = capsys.readouterr().err
    assert rc == 1
    assert "ANTHROPIC_API_KEY" in err
    assert calls == [] and _data_snapshot() == before


def test_run_refuses_while_any_cell_is_blocked(monkeypatch, capsys):
    """A partial 2x2 is not the pre-registered test — refuse before spending
    on the cells that WOULD have run."""
    _guard_sanction(monkeypatch)
    _seed(with_ledger=False)
    calls = _transport_tripwire(monkeypatch)
    before = _data_snapshot()
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")

    rc = mb.main(["t2", "--dates", A_DAY, "--run"])
    err = capsys.readouterr().err
    assert rc == 1
    assert "BLOCKED" in err
    assert calls == [] and _data_snapshot() == before


def test_refuses_a_date_with_no_briefing_row(monkeypatch, capsys):
    _guard_sanction(monkeypatch)
    db.migrate()
    calls = _transport_tripwire(monkeypatch)
    rc = mb.main(["t1", "--dates", "2031-01-01"])
    out = capsys.readouterr().out
    assert rc == 0 and calls == []
    assert "BLOCKED" in out


def test_refuses_when_no_record_db_exists_at_all(monkeypatch, capsys):
    _guard_sanction(monkeypatch)
    calls = _transport_tripwire(monkeypatch)
    rc = mb.main(["t1", "--dates", A_DAY])
    err = capsys.readouterr().err
    assert rc == 1 and calls == []
    assert "read-only" in err


def test_the_record_is_opened_read_only(monkeypatch, capsys):
    """db.connect (writable) is a tripwire for the whole harness — every read
    goes through db.connect_readonly, and a readonly handle genuinely cannot
    write."""
    _guard_sanction(monkeypatch)
    _seed(with_ledger=True)
    _transport_tripwire(monkeypatch)

    def boom(*a, **k):
        raise AssertionError("moat_battery opened a WRITABLE connection")

    monkeypatch.setattr(mb.db, "connect", boom)
    assert mb.main(["t2", "--dates", A_DAY]) == 0

    con = db.connect_readonly()
    try:
        with pytest.raises(Exception):
            con.execute("DELETE FROM briefings")
    finally:
        con.close()


# ---------------------------------------------------------------------------
# 4. The cap — CHARGED dollars, the named divergence
# ---------------------------------------------------------------------------

def test_cap_binds_charged_dollars_and_skips_the_crossing_cell(
        monkeypatch, capsys):
    _guard_sanction(monkeypatch)
    _seed(with_ledger=True)
    _transport_tripwire(monkeypatch)
    monkeypatch.setenv("BUDGET_CAP_USD_PER_RUN", "0.0001")   # nothing fits
    rc = mb.main(["t2", "--dates", A_DAY])
    out = capsys.readouterr().out
    assert rc == 0
    assert "SKIP" in out and "would exceed the $0.00 cap" in out


def test_subscription_lane_is_zero_charged_with_the_shadow_disclosed(
        monkeypatch, capsys):
    _guard_sanction(monkeypatch)
    _seed(with_ledger=True)
    _transport_tripwire(monkeypatch)
    monkeypatch.setenv("BUDGET_CAP_USD_PER_RUN", "0.0001")   # cannot bind
    rc = mb.main(["t2", "--dates", A_DAY, "--lane", "subscription"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "$0 CHARGED (subscription); shadow $" in out
    assert "est total charged $0.0000" in out
    assert "4 cell(s) planned" in out          # the cap cannot skip a $0 arm


# ---------------------------------------------------------------------------
# 5. The form axis — furniture parity is Content's law, checked not assumed
# ---------------------------------------------------------------------------

def _both_renders():
    slots = _seed(with_ledger=True)
    inputs = _inputs()
    draft = stories_payload(inputs["slots"])
    sect = mb.render_sectioned(A_DAY, draft["stories"], inputs)
    prose = mb.render_prose_first(A_DAY, draft["stories"], inputs)
    return sect, prose, draft


def test_prose_first_kills_the_interpretive_labels_only():
    sect, prose, draft = _both_renders()
    st = draft["stories"][0]
    # class 1 — the labels die, the text survives
    assert f"**{st['why_label']}:**" in sect
    assert f"**{st['why_label']}:**" not in prose
    assert st["why_it_matters"] in sect and st["why_it_matters"] in prose
    assert st["watch_for"] in sect and st["watch_for"] in prose
    assert st["lede"] in prose


def test_furniture_parity_holds_between_the_two_forms():
    sect, prose, _ = _both_renders()
    ok, missing = mb.furniture_parity(sect, prose)
    assert ok, f"furniture missing across the pair: {missing}"
    # class 2 — the epistemic ledger rides both, verbatim
    from newslens import ranking
    assert ranking.CORROBORATION_CAVEAT in sect
    assert ranking.CORROBORATION_CAVEAT in prose
    assert "Here for:" in sect and "Here for:" in prose


def test_furniture_parity_detects_a_dropped_colophon():
    """The parity checker is only worth having if it bites — drop a colophon
    line and it must fail."""
    sect, prose, _ = _both_renders()
    mangled = "\n".join(l for l in prose.splitlines() if "Here for:" not in l)
    ok, missing = mb.furniture_parity(sect, mangled)
    assert not ok and missing


def test_compose_mode_refuses_the_absent_prose_prompt(monkeypatch, capsys):
    """A writer prompt is Content-owned code, not an implementer guess. The
    refusal is a $0 dry-run BLOCK naming the missing artifact."""
    _guard_sanction(monkeypatch)
    _seed(with_ledger=True)
    _transport_tripwire(monkeypatch)
    assert not (paths.PROMPTS_DIR / mb.PROSE_FIRST_PROMPT).exists()

    rc = mb.main(["t1", "--dates", A_DAY, "--form-mode", "compose"])
    out = capsys.readouterr().out
    assert rc == 0
    assert mb.PROSE_FIRST_PROMPT in out
    assert "BLOCKED" in out


# ---------------------------------------------------------------------------
# 6. Scoring hooks + the HSR §7 worksheet
# ---------------------------------------------------------------------------

def test_prose_units_split_the_render_not_the_markdown():
    """A rendered edition is header + TOC + stories + italic furniture. Fed
    raw to a `.!?` splitter, the header/TOC/first-paragraph glue into ONE
    unit — so §1's marking (and the diction counts) must run over reader-facing
    UNITS: TOC entries and headlines stand alone, italic epistemic furniture is
    out, paragraphs are sentence-split."""
    sect, prose, draft = _both_renders()
    units = mb.prose_units(sect)
    assert not any(u.startswith("#") or u.startswith("---") for u in units)
    assert not any("NewsLens sees only its configured sources" in u
                   for u in units)          # the window line is furniture
    assert not any("Here for:" in u for u in units)       # colophon is furniture
    st = draft["stories"][0]
    assert st["headline"] in units                        # TOC + headline stand
    # the lede's first sentence is its own unit, not glued to the header
    assert any(u.startswith("The opening sentence reports") for u in units)
    # the two forms carry the same content, so the same units-with-diction count
    assert (mb.artifact_hooks(sect)["continuity_diction_hits"]
            == mb.artifact_hooks(prose)["continuity_diction_hits"])


def test_artifact_hooks_are_the_register_spec_counts():
    _, prose, _ = _both_renders()
    hooks = mb.artifact_hooks(prose + "\nIt remains to be seen on July 12.")
    assert hooks["banned_lexicon_hits"] == ["remains to be seen"]
    assert hooks["anchor_dates_present"] is True
    assert hooks["anchor_date_count"] >= 1
    assert hooks["continuity_diction_hits"] >= 1


def test_hsr_worksheet_follows_s7_and_refuses_to_print_a_rate():
    _seed(with_ledger=True)
    inputs = _inputs()
    draft = stories_payload(inputs["slots"])
    prose = mb.render_sectioned(A_DAY, draft["stories"], inputs)
    con = db.connect_readonly()
    try:
        sheet = mb.hsr_worksheet(con, A_DAY, draft["stories"],
                                 inputs["slots"], prose)
    finally:
        con.close()
    assert "THIS IS NOT AN HSR NUMBER" in sheet
    # §1 step 5's regex, verbatim — not re-authored
    assert mb.HSR_SWEEP_PATTERN in sheet
    assert ("reinstat|resum|renew|re-?impos|again|once more|consecutive|"
            "last (week|covered|time|month)|previous|remains|continues|"
            "since|follows the") == mb.HSR_SWEEP_PATTERN
    # §7's comparison baselines, quoted
    assert "as-shipped 0/1" in sheet and "ceiling 4/4" in sheet
    assert "Report as-shipped only" in sheet
    # §7's poisoned-antecedent bound
    assert mb.POISONED_ANTECEDENT_BOUND in sheet
    # no rate is ever printed
    assert "HSR =" not in sheet


def test_hsr_worksheet_excludes_baseline_sourced_sentences(monkeypatch):
    """gate FIX-5: a sentence carrying a dated baseline cite is researched
    founding context, excluded from the numerator via
    memory_core.is_baseline_sourced_sentence — this worksheet is that
    predicate's only intended consumer."""
    _seed(with_ledger=True)
    inputs = _inputs()
    draft = stories_payload(inputs["slots"])
    cite = memory_core.baseline_cite("2026-07-01")
    prose = f"The ban was reimposed {cite} and remains in force."
    con = db.connect_readonly()
    try:
        sheet = mb.hsr_worksheet(con, A_DAY, draft["stories"],
                                 inputs["slots"], prose)
    finally:
        con.close()
    assert "EXCLUDED from the numerator" in sheet


# ---------------------------------------------------------------------------
# 7. T3 — the Concept B pack ($0, no LLM, ever)
# ---------------------------------------------------------------------------

def test_concept_b_pack_is_zero_llm_and_separates_moved_from_new_files(
        monkeypatch, capsys):
    _guard_sanction(monkeypatch)
    _seed(with_ledger=True)
    calls = _transport_tripwire(monkeypatch)

    rc = mb.main(["t3", "--dates", A_DAY, "--run"])
    out = capsys.readouterr().out
    assert rc == 0 and calls == []             # NEVER an LLM call
    assert "$0.00" in out

    pack = (paths.DATA_DIR / "battery" / mb.ranking.local_today() / "phase2"
            / "t3" / A_DAY)
    md = (pack / "concept-b-pack.md").read_text(encoding="utf-8")
    payload = json.loads((pack / "concept-b-pack.json").read_text("utf-8"))
    assert A_TOPIC in md
    assert "New files opened" in md
    assert "INPUT PACK, not the artifact" in md
    assert [f["topic"] for f in payload["moved_files"]] == [A_TOPIC]
    assert [f["slot"] for f in payload["new_files"]] == [2]
    assert payload["moved_files"][0]["ledger"][0]["date"] == PRIOR


def test_t3_defaults_to_the_registered_two_mornings(monkeypatch, capsys):
    """data-2 §4 T3: "2 mornings ($0 LLM, design hrs)" — the default N is the
    registered one, and discovery skips editions with no prose (edition 1 was
    rank-only, HSR §3)."""
    _guard_sanction(monkeypatch)
    _seed(with_ledger=True, date="2026-07-05")
    con = db.connect()
    try:
        seed_briefing(con, "2026-07-06", [slot(1)], narrative="Published.")
        seed_briefing(con, "2026-07-07", [slot(1)], narrative=None)  # no prose
    finally:
        con.close()
    assert mb.main(["t3"]) == 0
    out = capsys.readouterr().out
    assert "editions: 2026-07-05, 2026-07-06" in out       # 2, prose-only


def test_concept_b_pack_dry_run_writes_nothing(monkeypatch, capsys):
    _guard_sanction(monkeypatch)
    _seed(with_ledger=True)
    before = _data_snapshot()
    assert mb.main(["t3", "--dates", A_DAY]) == 0
    assert _data_snapshot() == before


# ---------------------------------------------------------------------------
# 8. The blind pack
# ---------------------------------------------------------------------------

def _fake_cells(root: Path, test="t2", date=A_DAY):
    """Stand in for produced cells (the pack seals artifacts, it never renders
    them) — including a manifest per cell, which must NOT reach blind/."""
    for ctx in ("on", "off"):
        for form in ("prose_first", "sectioned"):
            d = root / test / date / f"ctx-{ctx}__{form}"
            d.mkdir(parents=True, exist_ok=True)
            (d / "narrative.md").write_text(
                f"# artifact {ctx} {form}\n", encoding="utf-8")
            (d / "manifest.json").write_text(
                json.dumps({"context": ctx, "form": form}), encoding="utf-8")
    return root


def test_blind_pack_shuffles_seals_and_is_reproducible(tmp_path):
    root = _fake_cells(tmp_path / "phase2")
    res = mb.build_blind_pack(root, seed=1234)
    blind = Path(res["blind_dir"])
    assert res["cells"] == 4 and res["sets"] == 1
    labels = sorted(p.name for p in (blind / "SET-1").iterdir())
    assert labels == ["A.md", "B.md", "C.md", "D.md"]
    key = (blind / "_KEY-do-not-open-until-scored.txt").read_text("utf-8")
    assert "shuffle seed: 1234" in key
    assert "ORDER-ONLY" in key              # the blinding limit is disclosed
    for ctx in ("on", "off"):
        assert f"context={ctx}" in key

    # same seed, same shuffle
    root2 = _fake_cells(tmp_path / "phase2b")
    res2 = mb.build_blind_pack(root2, seed=1234)
    def sig(r):
        return [(m["label"], m["context"], m["form"]) for m in r["mapping"]]
    assert sig(res) == sig(res2)


def test_blind_pack_carries_no_manifest(tmp_path):
    """The blind dir holds artifacts, the questions and the sealed key —
    nothing else. Asserted against the FILESYSTEM directly, so this holds even
    if _assert_blind_clean is broken (its own pin is
    test_blind_pack_assert_catches_contamination)."""
    root = _fake_cells(tmp_path / "phase2")
    res = mb.build_blind_pack(root, seed=7)
    blind = Path(res["blind_dir"])
    assert list(blind.rglob("manifest.json")) == []
    assert list(blind.rglob("*.json")) == []
    top = sorted(p.name for p in blind.iterdir() if p.is_file())
    assert top == ["QUESTIONS.md", "_KEY-do-not-open-until-scored.txt"]
    for p in blind.rglob("*.md"):
        if p.parent != blind:
            assert p.stem in list("ABCDEFGH")


def test_re_sealing_refuses_rather_than_leaving_stale_sets(tmp_path):
    """A second shuffle over fewer cells would leave the first pack's SET dirs
    standing beside the new ones, and the reader could not tell them apart —
    the pack refuses instead of half-overwriting."""
    root = _fake_cells(tmp_path / "phase2")
    first = mb.build_blind_pack(root, seed=11)
    with pytest.raises(mb.MissingArtifact) as exc:
        mb.build_blind_pack(root, seed=12)
    assert "already exists" in str(exc.value)
    # the first pack is intact and untouched
    key = (Path(first["blind_dir"]) / "_KEY-do-not-open-until-scored.txt"
           ).read_text("utf-8")
    assert "shuffle seed: 11" in key


def test_blind_copies_are_redacted_but_the_cell_dir_keeps_the_truth(tmp_path):
    """QA F2 + F8. The blind copy carries neither the render stamp (which
    recovers production order, i.e. the context arm) nor any arm identifier.
    The UNBLINDED cell dir keeps both — redaction is a property of the copy,
    never of the record."""
    root = tmp_path / "phase2"
    for name, stamp in (("ctx-on__sectioned", "2026-07-24 05:07"),
                        ("ctx-off__sectioned", "2026-07-24 05:22")):
        d = root / "t2" / A_DAY / name
        d.mkdir(parents=True, exist_ok=True)
        (d / "narrative.md").write_text(
            f"# NewsLens\n\nBody, arm {name} on claude-opus-4-8.\n\n"
            f"*Generated {stamp} UTC. Covers items fetched X → Y.*\n",
            encoding="utf-8")

    res = mb.build_blind_pack(root, seed=3)
    blind = Path(res["blind_dir"])
    for p in (blind / "SET-1").glob("*.md"):
        text = p.read_text(encoding="utf-8")
        assert "Generated 2026-07-24 05" not in text     # no real stamp
        assert "ctx-on" not in text and "ctx-off" not in text
        assert "claude-opus-4-8" not in text
        assert "redacted" in text                        # visibly withheld
    # the record side is untouched
    original = (root / "t2" / A_DAY / "ctx-on__sectioned"
                / "narrative.md").read_text(encoding="utf-8")
    assert "Generated 2026-07-24 05:07 UTC" in original
    assert "claude-opus-4-8" in original


def test_blind_labels_are_unbounded_and_never_truncate(tmp_path):
    """QA F4. zip() against a fixed 8-char alphabet silently dropped cells 9+.
    A generator cannot truncate."""
    assert [mb.blind_label(i) for i in range(3)] == ["A", "B", "C"]
    assert mb.blind_label(25) == "Z"
    assert mb.blind_label(26) == "AA"
    assert mb.blind_label(27) == "AB"
    assert len({mb.blind_label(i) for i in range(200)}) == 200

    root = tmp_path / "phase2"
    for i in range(12):
        d = root / "t2" / A_DAY / f"ctx-on__f{i}"
        d.mkdir(parents=True, exist_ok=True)
        (d / "narrative.md").write_text(f"# body {i}\n", encoding="utf-8")
    res = mb.build_blind_pack(root, seed=1)
    assert res["cells"] == 12
    assert len(list((Path(res["blind_dir"]) / "SET-1").glob("*.md"))) == 12


def test_blind_clean_is_content_aware_not_just_filename_shaped(tmp_path):
    """QA F8. A planted model name inside a blind artifact passed the old
    filename-only check."""
    root = tmp_path / "phase2"
    d = root / "t2" / A_DAY / "ctx-on__sectioned"
    d.mkdir(parents=True, exist_ok=True)
    (d / "narrative.md").write_text("# body\n", encoding="utf-8")
    res = mb.build_blind_pack(root, seed=4)
    blind = Path(res["blind_dir"])

    (blind / "SET-1" / "A.md").write_text(
        "# body\n\nrendered by claude-opus-4-8\n", encoding="utf-8")
    with pytest.raises(AssertionError) as exc:
        mb._assert_blind_clean(blind)
    assert "claude-opus-4-8" in str(exc.value)

    (blind / "SET-1" / "A.md").write_text(
        "# body\n\n*Generated 2026-07-24 05:07 UTC.*\n", encoding="utf-8")
    with pytest.raises(AssertionError) as exc2:
        mb._assert_blind_clean(blind)
    assert "render timestamp" in str(exc2.value)


def test_receipt_counts_a_corrected_retry_s_first_billed_attempt(
        monkeypatch, capsys, tmp_path):
    """Gate F-A pin 1. call_llm bills attempt 1, rejects it on validation, and
    bills attempt 2. Pricing the FINAL attempt's usage only (what the harness
    used to do) drops attempt 1's dollars while the manifest cheerfully says
    attempts: 2. The receipt must carry BOTH."""
    _guard_sanction(monkeypatch)
    _seed(with_ledger=True)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-moat")
    inputs = _inputs()
    good = stories_payload(inputs["slots"])
    calls = []

    def reply(body, url):
        calls.append(body)
        if len(calls) == 1:                     # billed, then rejected
            return anthropic_envelope({"stories": []}, input_tokens=1000,
                                      output_tokens=500)
        return anthropic_envelope(good, input_tokens=1000, output_tokens=200)

    _wire_scripted(monkeypatch, reply)
    con = db.connect_readonly()
    try:
        rc = mb._execute(con, "t1", [A_DAY], mb.FORM_MODE_RENDER,
                         mb.DEFAULT_ABLATE, "api", 99.0, tmp_path / "phase2")
    finally:
        con.close()
    out = capsys.readouterr().out
    assert rc == 0 and len(calls) == 2

    m = json.loads((tmp_path / "phase2" / "t1" / A_DAY / "ctx-on__sectioned"
                    / "manifest.json").read_text(encoding="utf-8"))
    assert m["attempts"] == 2
    final_only = m["usd_charged_seam"]
    all_att = m["usd_charged_all_attempts"]
    assert all_att > final_only, (
        "all-attempts cost must exceed the final attempt's — attempt 1 billed "
        f"500 output tokens (final={final_only}, all={all_att})")
    receipt = [l for l in out.splitlines() if l.startswith("moat-battery:")][0]
    charged = float(re.search(r"charged \$([0-9.]+)", receipt).group(1))
    assert charged == pytest.approx(all_att, abs=1e-6), (
        f"receipt says ${charged} but {all_att} was billed: {receipt}")
    assert "2 attempts billing" in out          # named on the cell line too


def test_receipt_counts_a_cell_that_failed_after_billing(
        monkeypatch, capsys, tmp_path):
    """Gate F-A pin 2. Both attempts bill and both fail validation, so
    _run_draft raises. A sink owned by the callee would die with its frame and
    the run would report $0 for a cell that billed twice (~$0.90 worst case on
    Opus at the 16k ceiling). The receipt must carry it."""
    _guard_sanction(monkeypatch)
    _seed(with_ledger=True)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-moat")
    calls = []

    def reply(body, url):
        calls.append(body)
        return anthropic_envelope({"stories": []}, input_tokens=1000,
                                  output_tokens=400)      # always malformed

    _wire_scripted(monkeypatch, reply)
    con = db.connect_readonly()
    try:
        rc = mb._execute(con, "t1", [A_DAY], mb.FORM_MODE_RENDER,
                         mb.DEFAULT_ABLATE, "api", 99.0, tmp_path / "phase2")
    finally:
        con.close()
    out, err = capsys.readouterr()
    assert rc == 1 and len(calls) == 2          # billed twice, produced nothing
    assert "FAILED" in err
    assert "billed $" in err and "2 attempt(s) before failing" in err

    receipt = [l for l in out.splitlines() if l.startswith("moat-battery:")][0]
    charged = float(re.search(r"charged \$([0-9.]+)", receipt).group(1))
    assert charged > 0, (
        "a cell that billed twice and failed reported $0 on the receipt — "
        f"the sink was lost with the callee's frame: {receipt}")

    # A FAILED DRAFT IS NOT RE-BILLED BY ITS SIBLING CELL. In render mode both
    # forms share one draft_key; without caching the failure the sibling reran
    # the same doomed prompt for another two billed attempts — 4 where the
    # plan priced 1 draft.
    assert "its draft failed above; not re-billed" in out


def test_missing_ran_at_never_stamps_a_wall_clock(monkeypatch):
    """Gate F-B pin. On a record whose window_meta lacks `ran_at`, neither
    render may substitute its own clock: that is a SECOND timestamp format,
    which _RENDER_STAMP_RE does not match, so redact_for_blind and
    _assert_blind_clean would both miss it — QA F2's channel through another
    door. Both forms must show the sentinel, and must agree."""
    _seed(with_ledger=True)
    inputs = _inputs()
    inputs["window_meta"] = {"window": {}}      # no ran_at
    draft = stories_payload(inputs["slots"])
    sect = mb.render_sectioned(A_DAY, draft["stories"], inputs)
    prose = mb.render_prose_first(A_DAY, draft["stories"], inputs)

    iso_clock = re.compile(r"→ \d{4}-\d{2}-\d{2}T\d{2}:\d{2}")
    for name, text in (("sectioned", sect), ("prose_first", prose)):
        assert not iso_clock.search(text), f"{name} stamped a wall clock"
        assert mb.WINDOW_END_SENTINEL in text, name
    # and the two forms agree on the line, so a blind set cannot be sorted by it
    def window_line(t):
        return [l for l in t.splitlines() if "Covers items fetched" in l][0]
    assert window_line(sect) == window_line(prose)

    # a record that HAS ran_at keeps its real value untouched
    inputs["window_meta"] = {"ran_at": "2026-07-05T09:59:00Z", "window": {}}
    assert "2026-07-05T09:59" in mb.render_sectioned(
        A_DAY, draft["stories"], inputs)


def test_pack_flags_an_incomplete_read_set_not_just_its_count(
        monkeypatch, capsys):
    """QA F4 rider: a 2x2 missing an arm must be FLAGGED, not merely counted —
    a failed arm is exactly when the hole is easiest to miss."""
    _guard_sanction(monkeypatch)
    _seed(with_ledger=True)
    root = paths.DATA_DIR / "battery" / mb.ranking.local_today() / "phase2"
    for n in ("ctx-on__prose_first", "ctx-on__sectioned",
              "ctx-off__prose_first"):          # 3 of 4
        d = root / "t2" / A_DAY / n
        d.mkdir(parents=True, exist_ok=True)
        (d / "narrative.md").write_text("x\n", encoding="utf-8")

    assert mb.main(["pack", "--run", "--seed", "2"]) == 0
    out = capsys.readouterr().out
    assert "INCOMPLETE: 3 of 4" in out
    assert "1 of 1 read-set(s) are INCOMPLETE" in out
    key = (root / "blind" / "_KEY-do-not-open-until-scored.txt").read_text(
        encoding="utf-8")
    assert "INCOMPLETE: 3 of 4" in key           # sealed key says so too


def test_sourcing_rank_hook_is_declared_human_not_silently_missing():
    """QA F7: register-spec §7 names three hooks; two are computable. The
    third must be visibly declared human-adjudicated, never absent."""
    _, prose, _ = _both_renders()
    hooks = mb.artifact_hooks(prose)
    assert "synthesis_line_sourcing_rank" in hooks
    assert "HUMAN ADJUDICATION" in hooks["synthesis_line_sourcing_rank"]


def test_sealed_key_no_longer_claims_unconditional_context_blindness(tmp_path):
    """QA F2 fallout, asserted on the ARTEFACT the reader actually gets: the
    key used to say the context axis is "genuinely blind" while a visible
    render timestamp defeated it. It must now name the closed channels and
    withhold the unconditional claim."""
    root = tmp_path / "phase2"
    d = root / "t2" / A_DAY / "ctx-on__sectioned"
    d.mkdir(parents=True, exist_ok=True)
    (d / "narrative.md").write_text("# body\n", encoding="utf-8")
    res = mb.build_blind_pack(root, seed=8)
    key = (Path(res["blind_dir"]) / "_KEY-do-not-open-until-scored.txt"
           ).read_text(encoding="utf-8")
    assert "does NOT claim" in key
    assert "render timestamp" in key            # channel 1, named
    assert "redact_render_timestamp" in key     # and the mitigation named
    assert "ORDER-ONLY" in key                  # the form limit still stated


def test_writer_layout_pack_seals_the_other_battery_leak_free(tmp_path):
    """QA F2, second half. scripts/battery has NO packer — its shipped 07-23
    blind pack was hand-built, which is exactly why it leaked render order.
    `pack --layout writer` gives that battery a code packer that redacts, so
    the Fable replication arms ship clean. battery.py itself is untouched."""
    root = tmp_path / "2026-07-23"
    for arm, stamp in (("claude-opus-4-8__api", "2026-07-24 05:07"),
                       ("claude-fable-5__subscription", "2026-07-24 05:14"),
                       ("claude-sonnet-5__subscription", "2026-07-24 05:22")):
        d = root / arm
        d.mkdir(parents=True, exist_ok=True)
        (d / "narrative.md").write_text(
            f"# NewsLens\n\nProse from {arm}.\n\n"
            f"*Generated {stamp} UTC.*\n", encoding="utf-8")
        (d / "manifest.json").write_text('{"arm": "x"}', encoding="utf-8")

    assert len(mb._discover_writer_cells(root)) == 3
    res = mb.build_blind_pack(root, seed=42, layout="writer")
    blind = Path(res["blind_dir"])
    assert res["cells"] == 3
    stamps = set()
    for p in (blind / "SET-1").glob("*.md"):
        text = p.read_text(encoding="utf-8")
        stamps.update(re.findall(r"Generated (.*?) UTC", text))
        for m in ("claude-opus-4-8", "claude-fable-5", "claude-sonnet-5",
                  "subscription"):
            assert m not in text
    assert stamps == {"[render time redacted for the blind read]"}
    assert list(blind.rglob("*.json")) == []       # manifests stayed behind
    key = (blind / "_KEY-do-not-open-until-scored.txt").read_text("utf-8")
    assert "arm=claude-opus-4-8__api" in key       # the key still tells all


def test_blind_pack_assert_catches_contamination(tmp_path):
    root = _fake_cells(tmp_path / "phase2")
    res = mb.build_blind_pack(root, seed=7)
    blind = Path(res["blind_dir"])
    (blind / "SET-1" / "manifest.json").write_text("{}", encoding="utf-8")
    with pytest.raises(AssertionError) as exc:
        mb._assert_blind_clean(blind)
    assert "un-blind" in str(exc.value)


def test_blind_label_validator_tracks_the_generator_unbounded(tmp_path):
    """QA R1. The validator built its label space from range(512) while the
    generator is unbounded — at 512+ cells it would reject labels the
    generator lawfully produced. is_blind_label is derived from the
    generator's image instead (bijective base-26 => exactly [A-Z]+), so the
    two cannot drift."""
    for i in (0, 25, 26, 511, 512, 701, 702, 703, 5000):
        assert mb.is_blind_label(mb.blind_label(i)), i
    assert mb.blind_label(701) == "ZZ" and mb.blind_label(702) == "AAA"
    for junk in ("", "a", "A1", "A_B", "ctx-on", "A.md", "Ä"):
        assert not mb.is_blind_label(junk), junk

    # and the validator accepts a lawfully-generated label past the old bound
    blind = tmp_path / "blind"
    (blind / "SET-1").mkdir(parents=True)
    (blind / "QUESTIONS.md").write_text("q\n", encoding="utf-8")
    (blind / "_KEY-do-not-open-until-scored.txt").write_text(
        "k\n", encoding="utf-8")
    (blind / "SET-1" / f"{mb.blind_label(900)}.md").write_text(
        "body\n", encoding="utf-8")
    mb._assert_blind_clean(blind)            # must not raise


def test_pack_flags_a_right_sized_set_with_no_contrast(monkeypatch, capsys):
    """QA R2. Cardinality is not composition: a hand-stitched T1 set of
    ctx-on__sectioned + ctx-off__sectioned is 2-of-2 and used to seal with a
    clean key, yet carries no FORM contrast — the F1 partial-set class in a
    new coat. It must be flagged in stdout AND in the sealed key."""
    _guard_sanction(monkeypatch)
    _seed(with_ledger=True)
    root = paths.DATA_DIR / "battery" / mb.ranking.local_today() / "phase2"
    for n in ("ctx-on__sectioned", "ctx-off__sectioned"):   # 2 of 2, no form
        d = root / "t1" / A_DAY / n
        d.mkdir(parents=True, exist_ok=True)
        (d / "narrative.md").write_text("x\n", encoding="utf-8")

    assert mb.main(["pack", "--run", "--seed", "5"]) == 0
    out = capsys.readouterr().out
    assert "WRONG COMPOSITION" in out
    assert "no form contrast" in out
    assert "1 of 1 read-set(s) are INCOMPLETE or WRONGLY COMPOSED" in out
    key = (root / "blind" / "_KEY-do-not-open-until-scored.txt").read_text(
        encoding="utf-8")
    assert "WRONG COMPOSITION" in key

    # the unit itself, both directions
    ok_pair = [{"context": "on", "form": "prose_first"},
               {"context": "on", "form": "sectioned"}]
    assert mb.set_composition_issue("t1", ok_pair) is None
    full_2x2 = [{"context": c, "form": f}
                for c in ("on", "off") for f in ("prose_first", "sectioned")]
    assert mb.set_composition_issue("t2", full_2x2) is None
    no_ctx = [{"context": "on", "form": f}
              for f in ("prose_first", "sectioned")] * 2
    assert "no context contrast" in mb.set_composition_issue("t2", no_ctx)
    assert mb.set_composition_issue("writer", ok_pair) is None   # no grid


def test_overlap_recipe_names_the_cap_it_needs(monkeypatch, capsys):
    """QA F10. The disjoint-dates recipe has to RUN AS WRITTEN. T2 on its
    registered N=3 editions overruns the default cap, so the harness's own F1
    partial-set gate would refuse the very command the disclosure suggests.
    The note must price the cap that command needs and offer a set that
    clears the current one."""
    _guard_sanction(monkeypatch)
    _transport_tripwire(monkeypatch)
    _seed(with_ledger=True, date="2026-07-05")
    con = db.connect()
    try:
        for d in ("2026-07-06", "2026-07-07", "2026-07-08", "2026-07-09",
                  "2026-07-10", "2026-07-11", "2026-07-12"):
            seed_briefing(con, d, [slot(1, mem=[A_TOPIC]), slot(2)],
                          narrative="Published.")
    finally:
        con.close()
    monkeypatch.setenv("BUDGET_CAP_USD_PER_RUN", "1.20")

    assert mb.main(["plan"]) == 0
    out = capsys.readouterr().out
    assert "OVERLAP:" in out
    assert "BUDGET_CAP_USD_PER_RUN raised to" in out
    assert "context arms" in out
    # the fallback it offers must genuinely clear the cap it just named
    m = re.search(r"`t2 --dates ([0-9,\-]+)` \(~\$([0-9.]+)\), which clears",
                  out)
    assert m, f"no cap-clearing fallback offered:\n{out}"
    assert float(m.group(2)) <= 1.20
    capsys.readouterr()
    assert mb.main(["t2", "--dates", m.group(1)]) == 0
    assert "0 skipped by the cap" in capsys.readouterr().out


def test_questions_file_carries_the_pre_registered_instrument(tmp_path):
    root = _fake_cells(tmp_path / "phase2")
    res = mb.build_blind_pack(root, seed=7)
    q = (Path(res["blind_dir"]) / "QUESTIONS.md").read_text("utf-8")
    assert "What did you learn?" in q
    assert "missing" in q
    assert "tomorrow" in q
    assert "indifference is a result" in q


def test_pack_refuses_when_no_cells_exist(monkeypatch, capsys):
    _guard_sanction(monkeypatch)
    _seed(with_ledger=True)
    rc = mb.main(["pack", "--run"])
    err = capsys.readouterr().err
    assert rc == 1
    assert "no produced cells" in err


def test_pack_dry_run_writes_nothing(monkeypatch, capsys, tmp_path):
    _guard_sanction(monkeypatch)
    _seed(with_ledger=True)
    session = mb.ranking.local_today()
    _fake_cells(paths.DATA_DIR / "battery" / session / "phase2")
    before = _data_snapshot()
    rc = mb.main(["pack"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "DRY RUN" in out
    assert _data_snapshot() == before


# ---------------------------------------------------------------------------
# 9. The LIVE path, against a scripted wire (no key, no network)
# ---------------------------------------------------------------------------

class _Resp:
    """Minimal urlopen response. NL-93 streams the writer seat's long call, so
    readline() must serve SSE; read() serves the same bytes for short seats."""

    def __init__(self, b: bytes):
        self._b = b
        try:
            payload = json.loads(b)
        except (ValueError, TypeError):
            payload = {}
        self._sse = io.BytesIO(anthropic_sse_bytes(
            payload if isinstance(payload, dict) else {}))

    def read(self, *a):
        return self._b

    def readline(self, *a):
        return self._sse.readline()

    def __iter__(self):
        return self

    def __next__(self):
        line = self.readline()
        if not line:
            raise StopIteration
        return line

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _wire(monkeypatch, payload):
    seen = []

    def fake_urlopen(req, timeout=None):
        seen.append(json.loads(req.data.decode("utf-8")))
        return _Resp(anthropic_envelope(payload, input_tokens=1000,
                                        output_tokens=200))

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    return seen


def _wire_scripted(monkeypatch, reply):
    """Like _wire, but `reply(body, url)` returns the envelope bytes per call —
    so a test can bill attempt 1, reject it, and bill attempt 2."""
    def fake_urlopen(req, timeout=None):
        body = json.loads(req.data.decode("utf-8"))
        return _Resp(reply(body, req.full_url))

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)


def test_run_produces_the_whole_2x2_and_never_touches_the_record(
        monkeypatch, capsys):
    """The live path end to end: render mode makes TWO calls for FOUR cells,
    each cell dir carries its artifacts + worksheet + manifest, the ablated
    cells declare what was dropped, the record is byte-identical after, and
    NEWSLENS_LANE_WRITER is restored to its pre-run value."""
    _guard_sanction(monkeypatch)
    slots = _seed(with_ledger=True)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-moat")
    monkeypatch.setenv("BUDGET_CAP_USD_PER_RUN", "5.00")
    monkeypatch.setenv("NEWSLENS_LANE_WRITER", "pre-existing-value")
    inputs = _inputs()
    seen = _wire(monkeypatch, stories_payload(inputs["slots"]))
    db_before = hashlib.sha256(Path(paths.DB_PATH).read_bytes()).hexdigest()

    rc = mb.main(["t2", "--dates", A_DAY, "--run"])
    out = capsys.readouterr().out
    assert rc == 0
    assert len(seen) == 2                      # 2 drafts -> 4 cells
    # one prompt carries the memory block, the other does not — the ablation
    # reached the WIRE, not just the planner
    bodies = [json.dumps(b, ensure_ascii=False) for b in seen]
    assert sum("MEMORY — the record for thread" in b for b in bodies) == 1

    root = (paths.DATA_DIR / "battery" / mb.ranking.local_today() / "phase2"
            / "t2" / A_DAY)
    for cell in ("ctx-on__prose_first", "ctx-on__sectioned",
                 "ctx-off__prose_first", "ctx-off__sectioned"):
        d = root / cell
        assert (d / "narrative.json").exists()
        assert (d / "narrative.md").read_text(encoding="utf-8").strip()
        assert "NOT AN HSR NUMBER" in (d / "hsr-worksheet.md").read_text("utf-8")
        m = json.loads((d / "manifest.json").read_text(encoding="utf-8"))
        assert m["model"] == llm.SEATS["writer"].model    # held FIXED
        assert m["form_mode"] == "render"
        assert m["ablated"] == (["ledger", "watch"] if "off" in cell else [])
        assert "banned_lexicon_hits" in m["hooks"]

    assert hashlib.sha256(
        Path(paths.DB_PATH).read_bytes()).hexdigest() == db_before
    assert os.environ["NEWSLENS_LANE_WRITER"] == "pre-existing-value"
    assert "4 cell(s) produced" in out


def test_execute_gates_on_the_estimate_not_on_spend_so_far(
        monkeypatch, capsys, tmp_path):
    """_execute's cap gate is DEFENCE IN DEPTH behind the F1 partial-set
    refusal: since QA F1 the CLI refuses --run outright when the cap skipped
    anything, so this gate is no longer reachable through main(). It stays
    because _execute must never overspend if called directly or if F1's gate
    ever regresses — a money path gets two locks.

    What it must do is gate on the PRE-CALL ESTIMATE, never on spend-so-far.
    The original implementation used spend-so-far and could produce more cells
    — and spend more — than the dry run the principal approved. Driven at the
    _execute level with a cap admitting exactly one of the two drafts a 2x2
    needs: one call, not two."""
    _guard_sanction(monkeypatch)
    _seed(with_ledger=True)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-moat")
    inputs = _inputs()
    prompt = mb.build_cell_prompt(inputs, mb.CONTEXT_ON, mb.FORM_SECTIONED)
    from newslens import battery as _b
    cap = _b._arm_estimate(prompt, llm.SEATS["writer"].model) * 1.5

    seen = _wire(monkeypatch, stories_payload(inputs["slots"]))
    con = db.connect_readonly()
    try:
        rc = mb._execute(con, "t2", [A_DAY], mb.FORM_MODE_RENDER,
                         mb.DEFAULT_ABLATE, "api", cap, tmp_path / "phase2")
    finally:
        con.close()
    out = capsys.readouterr().out
    assert rc == 0
    assert len(seen) == 1, (
        "the estimate gate let a second draft through — a spend-so-far gate "
        "would bill twice what the plan disclosed")
    assert "2 cell(s) produced" in out         # both forms of the one draft
    assert "over the $" in out                 # the skip is disclosed
    assert "charged $" in out                  # and the receipt prints


def test_cli_refuses_a_cap_truncated_run_before_spending(monkeypatch, capsys):
    """The F1 door from the implementer side: a cap that truncates a 2x2 must
    refuse at $0 rather than ship a read-set with no ablation contrast."""
    _guard_sanction(monkeypatch)
    _seed(with_ledger=True)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-moat")
    inputs = _inputs()
    prompt = mb.build_cell_prompt(inputs, mb.CONTEXT_ON, mb.FORM_SECTIONED)
    from newslens import battery as _b
    est = _b._arm_estimate(prompt, llm.SEATS["writer"].model)
    monkeypatch.setenv("BUDGET_CAP_USD_PER_RUN", f"{est * 1.5:.6f}")

    seen = _wire(monkeypatch, stories_payload(inputs["slots"]))
    rc = mb.main(["t2", "--dates", A_DAY, "--run"])
    err = capsys.readouterr().err
    assert rc == 1 and not seen
    assert "INCOMPLETE read-set" in err
    assert "BUDGET_CAP_USD_PER_RUN" in err     # names the way out


def test_produced_cells_hold_furniture_parity_and_pack_cleanly(
        monkeypatch, capsys):
    """Produce -> pack, end to end: the two forms of a real produced pair keep
    furniture parity (no WARN), and the pack seals them blind."""
    _guard_sanction(monkeypatch)
    _seed(with_ledger=True)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-moat")
    monkeypatch.setenv("BUDGET_CAP_USD_PER_RUN", "5.00")
    inputs = _inputs()
    _wire(monkeypatch, stories_payload(inputs["slots"]))

    assert mb.main(["t1", "--dates", A_DAY, "--run"]) == 0
    err = capsys.readouterr().err
    assert "furniture parity broken" not in err

    assert mb.main(["pack", "--run", "--seed", "99"]) == 0
    out = capsys.readouterr().out
    assert "1 set(s), 2 artifact(s)" in out
    blind = (paths.DATA_DIR / "battery" / mb.ranking.local_today() / "phase2"
             / "blind")
    assert sorted(p.name for p in (blind / "SET-1").iterdir()) == ["A.md", "B.md"]
    assert list(blind.rglob("*.json")) == []


# ---------------------------------------------------------------------------
# 10. The launcher
# ---------------------------------------------------------------------------

def test_launcher_delegates_to_the_module():
    src = (Path(__file__).resolve().parents[1] / "scripts"
           / "moat-battery").read_text(encoding="utf-8")
    assert "from newslens.moat_battery import main" in src
    assert "sys.exit(main())" in src


def test_no_arms_flag_exists(capsys):
    """The 2x2 holds the model fixed on purpose — a model swap here would
    confound the ablation, so --arms must NOT exist on this harness."""
    with pytest.raises(SystemExit):
        mb.main(["t2", "--arms", "claude-opus-4-8"])
    assert "unrecognized arguments" in capsys.readouterr().err
