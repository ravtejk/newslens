"""QA adversarial pass — editor-preservation batch (M1 matcher + M2 A9/A10).
Opus-produced (Fable weekly cap; enters the 07-23 Fable re-check queue).

These tests attack the contract in `workspace/briefs/2026-07-21--newslens--
editor-preservation.md` from the angles the dispatch flagged as weakest. RED
tests here are ACCEPTANCE CRITERIA — a failing one names a defect the batch
must answer for (or the gate must consciously accept). GREEN tests are the
moat proofs (Rook's blocker, determinism, spend integrity) that must stay green.

Finding map (see the QA verdict for full write-ups):
  F1  RED  — protect_facts_lost substring subject-match misses a real deletion.
  F2  chr  — either-gone date-form brittleness (degrade-rate / cap coupling).
  F3  grn  — poison ∩ protect double-tag: coherent THIS week, M3 deadlock flag.
  F4  chr  — multi-thread topic-word union can suppress a PROTECT discriminator.
  F5  chr  — _dates_in parses the modal 'may'+N as a May date (latent, off-July).
  Rook grn — POISON rides ONLY a positive source-echo mark (the BLOCKING moat).
  det  grn — matcher pure/stable; subject_units sorted.
  6/8  grn — A9 degrade adds no LLM call, no double-charge; invalid draft -> clean
             GenerateError, not a raw crash.
"""
from __future__ import annotations

import copy
import json
import time

import pytest

from newslens import generate
from newslens import memory_core as mc
from test_generate import stories_payload, compliant_script
from test_editor_preservation import (
    _thread, _delta, _mark, _seed_thread_and_briefing,
    _SPECIMEN_DRAFT, _SPECIMEN_CTX, ENV, EDITION,
)


# ===========================================================================
# Rook's BLOCKER (dispatch item 1) — the poison path rides ONLY a POSITIVE
# 'source-echo' mark, never a defaulted/absent grade, never a no-antecedent
# fallback. The existing suite covers None + no-antecedent; this pins the
# EXPLICIT non-echo grade strings too (record-established / reader-explicit /
# external-synthesis), closing the matrix.
# ===========================================================================

_REINSTATE_DRAFT = {"stories": [{
    "lede": "The U.S. reinstated its naval blockade Jul 14."}]}


@pytest.mark.parametrize("grade", [
    None, "record-established", "reader-explicit", "external-synthesis"])
def test_rook_poison_never_fires_without_a_positive_source_echo(grade):
    ctx = [{"topics": ["Strait of Hormuz"], "rows": [
        {"date": "2026-07-14", "text": "U.S. reinstated a naval blockade",
         "provenance": grade, "kind": "delta"}]}]
    tags = mc.ledger_callbacks(_REINSTATE_DRAFT, ctx, EDITION)
    assert not any(t.tag == "POISON" for t in tags), (grade, tags)


def test_rook_no_antecedent_at_all_never_poisons():
    ctx = [{"topics": ["Strait of Hormuz"], "rows": []}]
    assert not any(t.tag == "POISON"
                   for t in mc.ledger_callbacks(_REINSTATE_DRAFT, ctx, EDITION))


def test_rook_positive_source_echo_DOES_poison():
    """The other half of the blocker: the positive mark, and only it, poisons."""
    ctx = [{"topics": ["Strait of Hormuz"], "rows": [
        {"date": "2026-07-14", "text": "U.S. reinstated a naval blockade",
         "provenance": mc.PROVENANCE_SOURCE_ECHO, "kind": "delta"}]}]
    tags = mc.ledger_callbacks(_REINSTATE_DRAFT, ctx, EDITION)
    assert any(t.tag == "POISON" and "blockade" in t.subject_units for t in tags)


# ===========================================================================
# F1 (dispatch item 3) — RED. The teeth (protect_facts_lost) use a SUBSTRING
# subject test with NO word boundary. A real deletion slips whenever a lost
# subject unit is a substring of a word the editor KEPT and the date survives
# independently. The canonical kept-poison sentence 'reinstated' literally
# contains 'state'; 'United States' contains it too — so a clean Jul-11
# callback whose discriminating subject is 'state' reads as "still present"
# after it is wholly deleted. That defeats the teeth.
# ===========================================================================

def test_F1_substring_subject_match_misses_a_real_deletion():
    """RED — ACCEPTANCE CONTRACT (F1, teeth false-negative).

    FIX CONTRACT: protect_facts_lost must treat a subject unit as PRESENT only
    when it survives as a WHOLE token (or a legitimate inflection of one —
    blockade/blockades, the reword tolerance the substring test was chosen for),
    NOT as an arbitrary substring of an unrelated kept word. A wholesale
    deletion of a dated callback must be detected as LOST even when one of its
    subject units happens to be a substring of a word the editor kept and the
    date recurs elsewhere. A naive \\bword\\b fix is INSUFFICIENT (it would
    break blockade->blockades); the fix needs token-aware / stem-aware matching.
    Flips GREEN when the deletion below is reported lost.
    NOTE: does NOT bite the exact canonical e8/e9 specimen (there the date does
    not independently survive, so either-gone catches it via the date) — this is
    a realistic-neighbor hardening gap, not a break of the primary HSR unblock.
    """
    # The clean Jul-11 callback ('the State Department froze assets Jul 11')
    # is WHOLLY DELETED. The editor keeps the poison 'reinstated' sentence and,
    # elsewhere, a Jul-11 date on an unrelated clause. 'state' survives as a
    # substring of 'reinstated' AND 'States'; 'Jul 11' survives on the unrelated
    # clause -> protect_facts_lost wrongly reports nothing lost.
    protect_facts = [("2026-07-11", ("state",))]
    edited = {"stories": [{
        "headline": "US holds the strait",
        "lede": "The United States reinstated its naval blockade on Jul 14.",
        "why_it_matters": "Oil markets stayed calm through Jul 11.",
    }]}
    lost = mc.protect_facts_lost(protect_facts, edited, EDITION)
    assert ("2026-07-11", ("state",)) in lost, (
        "DELETION MASKED: 'state' survived inside 'reinstated'/'States' and "
        "'Jul 11' survived on an unrelated clause; the teeth missed a wholesale "
        "deletion of the Jul-11 accountability callback")


def test_F1b_word_boundary_variant_shows_it_is_the_substring():
    """Control: rename the surviving words so 'state' is NOT a substring of any
    kept word — then the SAME deletion is correctly caught. Isolates the cause
    to the missing word boundary (not the date logic)."""
    protect_facts = [("2026-07-11", ("state",))]
    edited = {"stories": [{
        "headline": "US holds the strait",
        "lede": "Washington renewed its naval blockade on Jul 14.",   # no 'state'
        "why_it_matters": "Oil markets stayed calm through Jul 11.",
    }]}
    lost = mc.protect_facts_lost(protect_facts, edited, EDITION)
    assert ("2026-07-11", ("state",)) in lost   # caught once the substring is gone


# ===========================================================================
# F2 (dispatch item 2) — CHARACTERIZATION. Either-gone over-fire: the date
# extractor recognizes only 'Mon D' (month-first, no ordinal) + ISO. Ordinal,
# day-first, numeric, relative, and spelled-out forms all read as date-GONE ->
# a FALSE degrade-to-draft (longer, pricier text — Onna's cap coupling). Safe
# DIRECTION (the callback still ships in the draft) but a live degrade-rate
# risk. This test PINS the surface so a future widening is a conscious diff.
# ===========================================================================

_DATE_KEPT = ["Jul 9", "July 9", "Jul 09", "2026-07-09"]
_DATE_OVERFIRE = ["July 9th", "9 July", "on the 9th", "07/09",
                  "the ninth of July", "last Wednesday"]


@pytest.mark.parametrize("form", _DATE_KEPT)
def test_F2_date_forms_that_survive(form):
    edited = {"stories": [{"lede": f"The naval blockade was imposed {form}."}]}
    assert mc.protect_facts_lost([("2026-07-09", ("blockade",))],
                                 edited, EDITION) == []


@pytest.mark.parametrize("form", _DATE_OVERFIRE)
def test_F2_date_forms_that_FALSE_DEGRADE(form):
    """Documents the over-fire. 'July 9th' and '9 July' are unambiguously the
    SAME date and common news style, yet both trigger a false degrade — the two
    most defensible candidates for widening _MONTH_DAY_RE."""
    edited = {"stories": [{"lede": f"The naval blockade was imposed {form}."}]}
    lost = mc.protect_facts_lost([("2026-07-09", ("blockade",))], edited, EDITION)
    assert lost == [("2026-07-09", ("blockade",))]   # over-fire (safe direction)


# ===========================================================================
# F3 (dispatch item 4) — poison ∩ protect. When the same date carries BOTH a
# record-established row (licenses PROTECT) and a source-echo row (POISON), one
# sentence gets BOTH tags. Coherent THIS week (A10 is warn-only: keep + warn).
# FLAG for M3: a hard-drop A10 would deadlock against A9-preserve on the SAME
# sentence, and today A9 will RESURRECT such a sentence if the editor removes it
# (degrade reships the draft), then A10 merely warns.
# ===========================================================================

def test_F3_same_sentence_can_be_both_protect_and_poison():
    ctx = [{"topics": ["Strait of Hormuz"], "rows": [
        {"date": "2026-07-14", "text": "U.S. imposed a naval blockade",
         "provenance": None, "kind": "delta"},                    # record-established
        {"date": "2026-07-14", "text": "U.S. reinstated a naval blockade",
         "provenance": mc.PROVENANCE_SOURCE_ECHO, "kind": "delta"}]}
    ]
    draft = {"stories": [{"lede": "The U.S. reinstated its naval blockade Jul 14."}]}
    tags = mc.ledger_callbacks(draft, ctx, EDITION)
    by = {t.tag for t in tags}
    assert "PROTECT" in by and "POISON" in by
    # both tags share the same discriminating subject -> the M3 conflict surface
    prot = next(t for t in tags if t.tag == "PROTECT")
    pois = next(t for t in tags if t.tag == "POISON")
    assert set(prot.subject_units) & set(pois.subject_units)


# ===========================================================================
# F4 (dispatch item 5) — multi-thread slots. Per-story context UNIONS the
# topics of every matched thread, and _discriminating_units subtracts ALL topic
# words. So a word that is a legitimate discriminator for thread A but appears
# in thread B's TOPIC NAME is stripped. When it is the callback's ONLY >=5-char
# discriminator, the PROTECT vanishes -> that callback is silently unprotected.
# ===========================================================================

def test_F4_co_thread_topic_word_can_suppress_a_protect():
    # Story matched to BOTH 'Strait of Hormuz' and 'Blockade'. 'blockade' is now
    # a topic word -> stripped from discriminators. The terse callback's only
    # >=5 unit IS 'blockade' -> no PROTECT at all.
    ctx = [{"topics": ["Strait of Hormuz", "Blockade"], "rows": [
        {"date": "2026-07-09", "text": "naval blockade",
         "provenance": None, "kind": "delta"}]}]
    draft = {"stories": [{"lede": "The blockade held Jul 9."}]}
    prot = [t for t in mc.ledger_callbacks(draft, ctx, EDITION)
            if t.tag == "PROTECT"]
    assert prot == []   # suppressed: the callback is left unprotected

    # Control: single-thread, same callback -> PROTECT stands (proves the union
    # is the cause, not the callback's terseness).
    ctx1 = [{"topics": ["Strait of Hormuz"], "rows": [
        {"date": "2026-07-09", "text": "naval blockade",
         "provenance": None, "kind": "delta"}]}]
    prot1 = [t for t in mc.ledger_callbacks(draft, ctx1, EDITION)
             if t.tag == "PROTECT"]
    assert any(t.date == "2026-07-09" for t in prot1)


# ===========================================================================
# F5 (bonus) — _dates_in resolves the modal 'may' + number as a May date. On a
# MAY edition this can MASK a real May-dated callback loss (false-negative in
# the teeth) or spuriously PROTECT. Off the current July specimen's blast
# radius but a latent correctness bug. Pinned so it is not a surprise later.
# ===========================================================================

def test_F5_modal_may_plus_number_parses_as_a_date():
    # The modal 'may' followed by a number is read as the calendar date May D.
    assert "2026-05-09" in mc._dates_in("the toll may 9 civilians", "2026")
    assert "2026-05-09" in mc._dates_in("prices may 9 percent higher", "2026")
    # Consequence on a MAY edition: a deleted 'May 9' callback whose subject is
    # also gone still reads date-PRESENT whenever kept prose contains 'may <n>',
    # masking the loss (false-negative). Shown at the date layer here; the full
    # teeth-masking needs a May edition, off the current July specimen.


# ===========================================================================
# Determinism (dispatch item 7) — the matcher is pure and stable; subject_units
# are emitted sorted (no set-ordering leakage into the tag).
# ===========================================================================

def test_matcher_is_deterministic_and_units_sorted():
    a = mc.ledger_callbacks(_SPECIMEN_DRAFT, _SPECIMEN_CTX, EDITION)
    b = mc.ledger_callbacks(_SPECIMEN_DRAFT, _SPECIMEN_CTX, EDITION)
    assert a == b
    assert all(list(t.subject_units) == sorted(t.subject_units) for t in a)


def test_protect_facts_lost_is_deterministic():
    pf = [(t.date, t.subject_units)
          for t in mc.ledger_callbacks(_SPECIMEN_DRAFT, _SPECIMEN_CTX, EDITION)
          if t.tag == "PROTECT"]
    edited = {"stories": [{"lede": "US reinstated the blockade Jul 14."}]}
    r1 = mc.protect_facts_lost(pf, edited, EDITION)
    r2 = mc.protect_facts_lost(pf, edited, EDITION)
    assert r1 == r2


# ===========================================================================
# Degrade-path integrity + cap interaction (dispatch items 6 & 8). Liveness
# against the real editor/degrade seam.
# ===========================================================================

@pytest.fixture
def fake_model(monkeypatch):
    """1st json call = writer; 2nd+ json = editor (returns .editor, else echoes
    narrative); non-json = script. Mirrors the existing suite's fixture."""
    state = type("S", (), {})()
    state.calls, state.narrative, state.editor, state.script = [], None, None, None

    def fake_chat(key, prompt, max_tokens, temperature, json_mode):
        state.calls.append({"json_mode": json_mode, "prompt": prompt})
        if json_mode:
            n_json_before = sum(1 for c in state.calls[:-1] if c["json_mode"])
            payload = (state.narrative if n_json_before == 0
                       else (state.editor if state.editor is not None
                             else state.narrative))
            content = json.dumps(payload)
        else:
            content = state.script
        return {"choices": [{"finish_reason": "stop",
                             "message": {"content": content}}],
                "usage": {"prompt_tokens": 900, "completion_tokens": 200}}

    monkeypatch.setattr(generate, "_chat", fake_chat)
    monkeypatch.setattr(time, "sleep", lambda s: None)
    return state


def _deleting_setup(con, fake_model):
    """A draft with a clean Jul-9 callback on a record-delta thread; the editor
    DELETES the callback -> A9 must fire."""
    slots = _seed_thread_and_briefing(
        con, [("2026-07-09", "U.S. imposed a naval blockade of the strait", None)])
    draft = stories_payload(slots)
    draft["stories"][0]["lede"] += " The naval blockade was imposed Jul 9."
    fake_model.narrative = draft
    fake_model.editor = copy.deepcopy(draft)
    fake_model.editor["stories"][0]["lede"] = \
        stories_payload(slots)["stories"][0]["lede"]      # callback deleted
    fake_model.script = compliant_script(slots)
    return draft


def test_degrade_adds_no_llm_call_and_no_double_charge(migrated_con, fake_model):
    """Item 8: the A9 degrade discards the edit and reships the draft WITHOUT a
    third model call, and the a9 step carries NO cost key (cost-folding cannot
    double-count it)."""
    con = migrated_con
    _deleting_setup(con, fake_model)
    rep = generate.run_generate(date=EDITION, con=con, env=ENV, refresh=False)

    assert any("A9-DEGRADE" in w for w in rep.warnings), rep.warnings
    # the editor prompt is the LAST json-mode call — the degrade added none after
    json_calls = [c for c in fake_model.calls if c["json_mode"]]
    assert "DATED LEDGER CALLBACKS TO PRESERVE" in json_calls[-1]["prompt"]
    # the degrade-rate step is cost-free (no usd / usd_shadow) -> no double-count
    a9 = [s for s in rep.steps if s.get("step") == "a9_preserve_degrade"]
    assert a9 and "usd" not in a9[0] and "usd_shadow" not in a9[0]
    assert "Jul 9" in rep.narrative_text            # the draft (with callback) shipped


def test_degrade_to_an_invalid_draft_raises_clean_generate_error(migrated_con,
                                                                 fake_model):
    """Item 6: when A9 discards the edit and the DRAFT itself fails narrative
    validation, run_generate raises a clean GenerateError (disclosed lineage) —
    never a raw crash. The writer shape-check only guards story COUNT, so a
    bogus why_label reaches validate_narrative_payload on the degrade path."""
    con = migrated_con
    _deleting_setup(con, fake_model)
    # corrupt BOTH draft and echoed editor identically (so _editor_shape's
    # 'labels unchanged' check still passes and A9 is what fires).
    fake_model.narrative["stories"][0]["why_label"] = "Totally Bogus Framing"
    fake_model.editor["stories"][0]["why_label"] = "Totally Bogus Framing"

    with pytest.raises(generate.GenerateError) as ei:
        generate.run_generate(date=EDITION, con=con, env=ENV, refresh=False)
    assert "after editor degrade" in str(ei.value), str(ei.value)
