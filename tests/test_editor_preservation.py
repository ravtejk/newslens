"""Editor-preservation batch (2026-07-21) — M1 matcher + M2 A9 teeth + A10 warn.

The HSR leak (day-14 diagnostic): the length-editor deletes the writer's clean
dated ledger callbacks (e8/e9) while keeping e7's source-poisoned "reinstated
Jul 14". The fix is TEETH, not detection — additive, never shortening.

Proof classes in this file:
  * PURE (new-surface, born-green by nature — the functions do not exist at
    HEAD, so they cannot be "born red"; they pin the M1 artifact directly):
      - ledger_callbacks() classifies the e7 poison / e8+e9 clean specimens.
      - the Rook guardrail: POISON rides ONLY a positive source-echo mark.
      - protect_facts_lost() catches wholesale deletion even when the subject
        word is SHARED across callbacks (the canonical HSR shape), and tolerates
        pure rewording.
  * DB-WIRING (new-surface): _ledger_callback_context() reads the real ledger
    (ledger_for_thread provenance JOIN, superseded exclusion, latest_state).
  * BORN-RED (liveness — drives the real run_generate editor/degrade seam with
    ONLY pre-existing symbols): a callback-deleting edit DEGRADES to the draft.
    At unpatched HEAD there is no A9 enforcement, so the deletion ships and this
    test fails red. See the batch report for the HEAD-run fail list.
  * A10 warn-only: a surviving source-echo sentence emits A10-WARN and NEVER
    degrades (the hard-drop is M3, out of scope).

Enforcement-rule note (deliberate, flagged to the gate): protect_facts_lost
treats a fact as LOST when its date OR its subject is gone — NOT the literal
"date AND subject both gone". The HSR specimen shares the subject word
'blockade' between the kept poison callback (e7) and the deleted clean callback
(e8); a both-gone rule could never detect e8's deletion because 'blockade'
survives in e7. test_wholesale_deletion_caught_even_when_subject_word_shared is
the proof.
"""

from __future__ import annotations

import copy
import json
import time

import pytest

from newslens import db, generate, paths
from newslens import memory_core as mc
from test_generate import slot, seed_briefing, stories_payload, compliant_script

ENV = {"OPENAI_API_KEY": "sk-qa-fake"}
EDITION = "2026-07-16"          # after every predating delta below


# --- local seeding helpers (mirror test_migration_0014_qa) -------------------

def _thread(con, topic="Hormuz"):
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


# The e7/e8/e9 specimen as a pure (payload, ledger context) pair -------------

_SPECIMEN_DRAFT = {"stories": [{
    "headline": "US tightens grip on the strait",
    "lede": ("The United States reinstated its naval blockade of the strait on "
             "Jul 14. The blockade was first imposed Jul 9, the record shows."),
    "why_it_matters": "Nuclear talks collapsed Jul 11 after the sanctions vote.",
}]}

# rows PREDATING the edition: two record-established (Jul 9, Jul 11) + one
# POSITIVELY source-echo (Jul 14 — the poison the day-14 diagnostic found typed).
_SPECIMEN_CTX = [{
    "topics": ["Strait of Hormuz"],
    "rows": [
        {"date": "2026-07-09", "text": "U.S. imposed a naval blockade of the strait",
         "provenance": None, "kind": "delta"},
        {"date": "2026-07-11", "text": "Nuclear talks collapsed after the sanctions vote",
         "provenance": None, "kind": "delta"},
        {"date": "2026-07-14", "text": "U.S. reinstated a naval blockade",
         "provenance": "source-echo", "kind": "delta"},
    ],
}]


# ===========================================================================
# 1. M1 matcher — the classification (pure, the specimen the dispatch names)
# ===========================================================================

def test_e7_reinstated_jul14_is_poison_never_protected():
    """e7: 'reinstated ... Jul 14' — continuity diction whose only Jul-14
    antecedent is source-echo. POISON, and NOT protected (no record-established
    Jul-14 antecedent exists — PROTECT must never ride a poison row)."""
    tags = mc.ledger_callbacks(_SPECIMEN_DRAFT, _SPECIMEN_CTX, EDITION)
    e7 = [t for t in tags if "reinstated" in t.sentence and "Jul 14" in t.sentence]
    assert e7 and all(t.tag != "PROTECT" for t in e7)
    assert any(t.tag == "POISON" and t.marker.lower().startswith("reinstat")
               and "blockade" in t.subject_units for t in e7)


def test_e8_e9_clean_dated_callbacks_are_protected():
    """e8 (imposed Jul 9) and e9 (collapsed Jul 11) each match a predating
    record-established delta by date+subject → PROTECT."""
    tags = mc.ledger_callbacks(_SPECIMEN_DRAFT, _SPECIMEN_CTX, EDITION)
    prot = {t.date: t for t in tags if t.tag == "PROTECT"}
    assert "2026-07-09" in prot and "blockade" in prot["2026-07-09"].subject_units
    assert "2026-07-11" in prot
    assert {"collapsed", "talks"} & set(prot["2026-07-11"].subject_units)


def test_untyped_and_absent_grades_never_poison_rook_blocker():
    """Rook (BLOCKING): POISON rides ONLY a POSITIVE source-echo mark. The SAME
    'reinstated' sentence with its Jul-14 antecedent UNTYPED (provenance None)
    is never POISON — and with no antecedent at all it is never POISON (no
    no-antecedent fallback that would nuke legitimate continuity = the moat)."""
    untyped = [{"topics": ["Strait of Hormuz"],
                "rows": [{"date": "2026-07-14", "text": "U.S. reinstated a naval blockade",
                          "provenance": None, "kind": "delta"}]}]
    draft = {"stories": [{"lede": "The U.S. reinstated its naval blockade Jul 14."}]}
    assert not any(t.tag == "POISON"
                   for t in mc.ledger_callbacks(draft, untyped, EDITION))
    no_ante = [{"topics": ["Strait of Hormuz"], "rows": []}]
    assert not any(t.tag == "POISON"
                   for t in mc.ledger_callbacks(draft, no_ante, EDITION))


def test_reworded_clean_callback_still_protects_date_by_year_resolution():
    """A 'July 9' human date resolves to the same ISO as the Jul-09 delta — the
    date-token match is format-tolerant."""
    draft = {"stories": [{"lede": "The naval blockade was imposed July 9."}]}
    tags = mc.ledger_callbacks(draft, _SPECIMEN_CTX, EDITION)
    assert any(t.tag == "PROTECT" and t.date == "2026-07-09" for t in tags)


# ===========================================================================
# 2. M2 teeth — the post-edit diff (pure predicate)
# ===========================================================================

def _protect_facts():
    tags = mc.ledger_callbacks(_SPECIMEN_DRAFT, _SPECIMEN_CTX, EDITION)
    return [(t.date, t.subject_units) for t in tags if t.tag == "PROTECT"]


def test_wholesale_deletion_caught_even_when_subject_word_shared():
    """THE HSR SHAPE. The editor deletes e8+e9 (the clean callbacks) and keeps
    e7 — so 'blockade' SURVIVES (in e7) but the Jul-9 / Jul-11 accountability
    dates are gone. Both facts must read as LOST. A literal 'both gone' rule
    would miss the Jul-9 fact because 'blockade' persists in e7."""
    edited = {"stories": [{
        "headline": "US tightens grip on the strait",
        "lede": "The United States reinstated its naval blockade of the strait on Jul 14.",
        "why_it_matters": "Oil prices surged.",
    }]}
    lost = mc.protect_facts_lost(_protect_facts(), edited, EDITION)
    assert {d for d, _ in lost} == {"2026-07-09", "2026-07-11"}


def test_pure_reword_keeps_the_facts_no_false_degrade():
    """Rewording that keeps every date+subject loses nothing (no over-fire —
    Onna's degrade-rate concern)."""
    edited = {"stories": [{
        "headline": "US grip on the strait",
        "lede": ("Washington reinstated its naval blockade July 14. The blockade "
                 "was imposed July 9."),
        "why_it_matters": "Nuclear talks collapsed July 11 after the vote.",
    }]}
    assert mc.protect_facts_lost(_protect_facts(), edited, EDITION) == []


def test_token_set_sacrifices_plural_tolerance_deliberate_contract():
    """CONSCIOUS TRADE (gate 2026-07-21, F1). protect_facts_lost matches the
    subject by whole-token set (symmetric with the matcher's emission side), NOT
    substring and NOT stemming — so a subject kept only in PLURAL ('blockade' ->
    'blockades') reads as ABSENT and the fact is counted LOST even though the
    date survives. This is a DIRECTION-SAFE over-fire (it degrades to the longer,
    honest draft), accepted deliberately to close the substring false-negative
    ('state' surviving inside 'reinstated'/'United States'). Pinned so the trade
    is a contract, not an accident."""
    facts = [("2026-07-09", ("blockade",))]
    edited = {"stories": [{"lede": "The naval blockades were imposed Jul 9."}]}
    lost = mc.protect_facts_lost(facts, edited, EDITION)
    assert lost == [("2026-07-09", ("blockade",))]   # plural reads as absent -> lost


def test_stripping_only_the_date_is_a_loss():
    """The date IS the accountability stamp: dropping it while keeping the
    subject destroys the dated callback (Eng: 'must not remove the date OR the
    subject')."""
    edited = {"stories": [{
        "lede": "The naval blockade was imposed earlier this month.",  # Jul 9 stripped
        "why_it_matters": "Talks collapsed July 11 after the sanctions vote.",
    }]}
    lost = mc.protect_facts_lost(_protect_facts(), edited, EDITION)
    assert ("2026-07-09" in {d for d, _ in lost}
            and "2026-07-11" not in {d for d, _ in lost})


# ===========================================================================
# 3. DB wiring — the real ledger context (ledger_for_thread provenance JOIN,
#    superseded exclusion, latest_state) feeds the matcher
# ===========================================================================

def test_ledger_context_reads_real_ledger_and_provenance(migrated_con):
    con = migrated_con
    tid = _thread(con, "Hormuz")
    _delta(con, tid, "2026-07-09", "U.S. imposed a naval blockade of the strait")
    echo_id = _delta(con, tid, "2026-07-14", "U.S. reinstated a naval blockade")
    _mark(con, echo_id, mc.PROVENANCE_SOURCE_ECHO)
    con.commit()
    slots = [{"slot": "1", "matched_memory": ["Hormuz"]}]

    ctx = generate._ledger_callback_context(con, slots, EDITION)
    rows = ctx[0]["rows"]
    by_date = {r["date"]: r for r in rows}
    assert by_date["2026-07-09"]["provenance"] is None            # record-established
    assert by_date["2026-07-14"]["provenance"] == mc.PROVENANCE_SOURCE_ECHO

    draft = {"stories": [{
        "lede": ("The U.S. reinstated its naval blockade Jul 14. The blockade "
                 "was imposed Jul 9."),
    }]}
    tags = mc.ledger_callbacks(draft, ctx, EDITION)
    assert any(t.tag == "PROTECT" and t.date == "2026-07-09" for t in tags)
    assert any(t.tag == "POISON" and "blockade" in t.subject_units for t in tags)


def test_superseded_delta_never_anchors_a_protected_fact(migrated_con):
    """Rook's gate carried through: a corrected (superseded) delta is dropped
    from the antecedent set — it cannot anchor a PROTECT."""
    con = migrated_con
    tid = _thread(con, "Hormuz")
    bad = _delta(con, tid, "2026-07-09", "U.S. imposed a naval blockade of the strait")
    good = _delta(con, tid, "2026-07-12", "correction: no blockade was imposed")
    con.execute("INSERT INTO thread_delta_supersessions (delta_id, superseded_by,"
                " reason) VALUES (?, ?, 'correction')", (bad, good))
    con.commit()
    ctx = generate._ledger_callback_context(
        con, [{"slot": "1", "matched_memory": ["Hormuz"]}], EDITION)
    assert "2026-07-09" not in {r["date"] for r in ctx[0]["rows"]}


# ===========================================================================
# 4. LIVENESS (BORN-RED) — the real editor/degrade seam
# ===========================================================================

@pytest.fixture
def fake_model(monkeypatch):
    """1st json call = the writer's narrative; 2nd+ json call = the editor
    (returns .editor, else echoes narrative); non-json = the script."""
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


def _seed_thread_and_briefing(con, deltas, mem="Hormuz"):
    tid = _thread(con, mem)
    ids = []
    for d, what, prov in deltas:
        did = _delta(con, tid, d, what)
        if prov:
            _mark(con, did, prov)
        ids.append(did)
    con.commit()
    slots = [slot(1, mem=[mem]), slot(2), slot(3)]
    seed_briefing(con, EDITION, slots)
    return slots


def test_callback_deleting_edit_degrades_to_draft(migrated_con, fake_model):
    """BORN-RED liveness. The writer's draft carries a clean dated callback
    ('imposed Jul 9') on a thread with a matching predating record delta; the
    editor DELETES it. The A9 teeth must discard the edit and ship the draft
    (callback intact) with a greppable degrade-rate marker. At unpatched HEAD
    there is no enforcement, so the deletion ships and this fails."""
    con = migrated_con
    slots = _seed_thread_and_briefing(
        con, [("2026-07-09", "U.S. imposed a naval blockade of the strait", None)])

    draft = stories_payload(slots)
    draft["stories"][0]["lede"] += " The naval blockade was imposed Jul 9."
    fake_model.narrative = draft
    fake_model.editor = copy.deepcopy(draft)               # editor deletes the callback
    fake_model.editor["stories"][0]["lede"] = stories_payload(slots)["stories"][0]["lede"]
    fake_model.script = compliant_script(slots)

    rep = generate.run_generate(date=EDITION, con=con, env=ENV, refresh=False)

    assert any("A9-DEGRADE" in w for w in rep.warnings), rep.warnings
    # the standard degrade seam disclosed the discard + named the A9 cause
    assert any("the edit was discarded" in w and "A9 preserve-enforcement" in w
               for w in rep.warnings)
    assert "Jul 9" in rep.narrative_text            # the DRAFT (with callback) shipped
    # the structured degrade-rate marker is on the ledger for day-one measurement
    assert any(s.get("step") == "a9_preserve_degrade" and s.get("callbacks_lost")
               for s in rep.steps)


def test_clean_edit_preserving_callbacks_is_not_degraded(migrated_con, fake_model):
    """The mirror: an editor that KEEPS the dated callback (echo) does not fire
    the teeth — no false degrade."""
    con = migrated_con
    slots = _seed_thread_and_briefing(
        con, [("2026-07-09", "U.S. imposed a naval blockade of the strait", None)])
    draft = stories_payload(slots)
    draft["stories"][0]["lede"] += " The naval blockade was imposed Jul 9."
    fake_model.narrative = draft
    fake_model.editor = None                                # echo = keep everything
    fake_model.script = compliant_script(slots)

    rep = generate.run_generate(date=EDITION, con=con, env=ENV, refresh=False)
    assert not any("A9-DEGRADE" in w for w in rep.warnings), rep.warnings
    assert "Jul 9" in rep.narrative_text


def test_surviving_source_echo_sentence_warns_only_no_degrade(migrated_con, fake_model):
    """A10 warn-only: a source-echo continuity sentence that SURVIVES the edit
    emits A10-WARN and does NOT degrade (the hard-drop is M3, out of scope)."""
    con = migrated_con
    slots = _seed_thread_and_briefing(
        con, [("2026-07-14", "U.S. reinstated a naval blockade",
               mc.PROVENANCE_SOURCE_ECHO)])
    draft = stories_payload(slots)
    draft["stories"][0]["lede"] = ("The U.S. reinstated its naval blockade of the "
                                   "strait Jul 14. A second sentence adds context.")
    fake_model.narrative = draft
    fake_model.editor = None                                # poison survives
    fake_model.script = compliant_script(slots)

    rep = generate.run_generate(date=EDITION, con=con, env=ENV, refresh=False)
    assert any("A10-WARN" in w for w in rep.warnings), rep.warnings
    assert not any("A9-DEGRADE" in w for w in rep.warnings)   # no degrade on poison
    assert "reinstated" in rep.narrative_text                 # not dropped this week
