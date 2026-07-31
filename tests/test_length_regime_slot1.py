"""Length regime 2026-07-30 — the slot-1 THIN-CORPUS instrument (Spec-4(a)).

The principal ratified floors -> targets product-wide on 2026-07-30, and D3
(slot-1 NO HARD FLOOR) carries a week-of-editions revisit falsifier. This file
is the acceptance instrument for the demotion in Spec-4(b): T1-T4 are born red
against the pre-demotion machinery and go green when the floors come out.

The case they serve is REAL, not synthetic: founder brief 17 (2026-07-16 slot
1) is the THINNEST reconstructable slot-1 day in the record — 15 retrieval
rows, only TWO full texts over 2,500 chars, 16,717 held chars total. Frozen
read-only into tests/fixtures/slot1_thin_corpus.json (`analysis_retrieval`
carries no-update and no-delete triggers, so those rows are the exact material
the analyst was handed). On a day like that a short lead is HONEST, and the
450-word floor's only available answer was "pad".

Proof classes in this file:
  * BORN RED (T1, T2, T3) — they fail against the floor machinery; the HEAD-run
    fail list is in the batch report. T3 additionally covers the Spec-4(b).1
    slot-2/3 and slot-4+ floor deletions, which sit on the same prompt surface.
  * CARRIED INVARIANT, born green (the fixture checksum; T2's paired A9
    assertion; T4) — labelled per the R4 born-red law. T2's pairing is the
    load-bearing one: removing the LENGTH discard must not remove the A9
    fact-loss discard that shares the same degrade seam.

Zero model calls, zero network: generate._chat is a sequenced fake and the
autouse sandbox redirects DATA_DIR/DB_PATH per test.

NL-126 (fixture time-bombs): this fixture carries ABSOLUTE stamps —
retrieved_at 2026-07-14 and 2026-07-16, the very 07-16/17 band whose bombs
detonated on 2026-07-30 — and the edition under test is 2026-07-16 with dated
memory rows behind it. The module therefore pins the product clock from birth
(conftest.pin_product_clock), so the seeded stamps and every now-anchored
window agree forever. The fixture is NEVER re-dated: re-dating hides the class
and re-arms the fuse at a later date.
"""

from __future__ import annotations

import copy
import hashlib
import json
import time
from datetime import datetime, timezone
from pathlib import Path

import pytest

from newslens import analysis, db, generate

from conftest import pin_product_clock
from test_generate import (compliant_script, seed_briefing, slot,
                           stories_payload)

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "slot1_thin_corpus.json"
EDITION = "2026-07-16"          # the fixture's own day
ENV = {"OPENAI_API_KEY": "sk-qa-fake"}

# The pin sits at noon of the edition under test: after the 07-14/07-16 stamps
# the fixture and the memory seeds carry, so every now-anchored window
# (ranking.candidate_window, memory.apply_dormancy) sees them as recent
# forever. Reads are frozen, writes are not — see pin_product_clock's docstring.
MODULE_NOW = datetime(2026, 7, 16, 12, 0, 0, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def _frozen_window_clock(monkeypatch):
    pin_product_clock(monkeypatch, MODULE_NOW)


def thin() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# 0. The frozen fixture itself
# ---------------------------------------------------------------------------

def test_the_thin_corpus_fixture_is_present_and_checksums_to_its_export():
    """CARRIED INVARIANT by construction (born green — it IS the deliverable
    this pins, exactly like brief47/50's checksum test). It guards the
    instrument's foundation: a fixture edited later would silently move every
    measurement above it.

    The census numbers are the claim that makes this the THIN day, so they are
    asserted, not described."""
    doc = thin()
    assert (doc["brief_id"], doc["date"], doc["slot"]) == (17, EDITION, 1)
    assert doc["tier"] == "full" and doc["status"] == "valid"
    assert len(doc["rows"]) == 15
    blob = json.dumps(doc["rows"], ensure_ascii=False,
                      sort_keys=True).encode("utf-8")
    assert hashlib.sha256(blob).hexdigest() == doc["rows_sha256"]

    held = sum(len(r["text"]) for r in doc["rows"])
    fat = [r["key"] for r in doc["rows"] if len(r["text"]) > 2500]
    assert held == 16_717                       # thinnest slot-1 day on record
    assert fat == ["S1", "S2"]                  # exactly two full texts
    # and the shape that makes "short is honest" true: the eight R keys are
    # search stubs, not articles
    stubs = [r for r in doc["rows"] if r["kind"] == "retrieved"]
    assert len(stubs) == 8 and max(len(r["text"]) for r in stubs) <= 350


# ---------------------------------------------------------------------------
# harness
# ---------------------------------------------------------------------------

@pytest.fixture
def fake_seq(monkeypatch):
    """Position-sequenced stateful fake (local copy per the fixtures-don't-
    import-across-modules convention, same as test_p31_enforcement's): the Nth
    json call serves .narratives[N-1] (last entry sticks — so an echoing editor
    is the default); the Nth non-json call serves .scripts[N-1]."""
    state = type("S", (), {})()
    state.calls, state.narratives, state.scripts = [], [], []

    def chat(key, prompt, max_tokens, temperature, json_mode):
        state.calls.append({"json_mode": json_mode, "prompt": prompt})
        if json_mode:
            n = sum(1 for c in state.calls if c["json_mode"])
            content = json.dumps(
                state.narratives[min(n - 1, len(state.narratives) - 1)])
        else:
            n = sum(1 for c in state.calls if not c["json_mode"])
            content = state.scripts[min(n - 1, len(state.scripts) - 1)]
        return {"choices": [{"finish_reason": "stop",
                             "message": {"content": content}}],
                "usage": {"prompt_tokens": 900, "completion_tokens": 200}}

    monkeypatch.setattr(generate, "_chat", chat)
    monkeypatch.setattr(time, "sleep", lambda s: None)
    return state


FILLER = "The analysis continues with sourced detail and measured context. "


def lead_of(slots, words: int, sentinel: str, band: int = 25):
    """stories_payload with story 1's lede padded to ~`words` lead-words, using
    the numeral-free, hedge-free filler test_p31_enforcement already uses (it
    must clear the trace-check and the hedge tripwire, not just the counter).
    The achieved count is ASSERTED into a band so a helper drift can never
    quietly move which side of a threshold a test sits on."""
    payload = copy.deepcopy(stories_payload(slots))
    base = generate._lead_words(payload)
    need = max(0, words - base - len(sentinel.split()))
    payload["stories"][0]["lede"] += (
        " " + FILLER * (need // len(FILLER.split())) + sentinel)
    got = generate._lead_words(payload)
    assert abs(got - words) <= band, f"helper drift: {got} words, wanted ~{words}"
    return payload


def persist_thin_slot1(con, date=EDITION):
    """Seed the slot-1 analysis brief FROM THE FROZEN THIN CORPUS, so the run
    under test is a real thin day and not a shape stub: the brief the analyst
    actually wrote on 2026-07-16, with the 15 rows it was actually given."""
    doc = thin()
    sources = {r["key"]: {k: r[k] for k in
                          ("kind", "outlet", "title", "url", "retrieved_at",
                           "text")}
               for r in doc["rows"]}
    return analysis.persist_brief(
        con, date, 1, "full", "valid", doc["original_brief"], "", 0.0,
        doc["original_header"], sources=sources)


def persist_rich_slot1(con, date=EDITION):
    """The other direction (T4): brief 47's corpus — 19 rows, 39,636 held
    chars, seven full texts. Same seam, a day with material to spend."""
    doc = json.loads((FIXTURE.parent / "analysis" / "brief47_corpus.json")
                     .read_text(encoding="utf-8"))
    sources = {r["key"]: {k: r[k] for k in
                          ("kind", "outlet", "title", "url", "retrieved_at",
                           "text")}
               for r in doc["rows"]}
    return analysis.persist_brief(
        con, date, 1, "full", "valid", doc["original_brief"], "", 0.0,
        doc["original_header"], sources=sources)


def run(con, fake_seq, date=EDITION):
    return generate.run_generate(date=date, con=con, env=dict(ENV),
                                 refresh=False)


def json_prompts(fake_seq):
    return [c["prompt"] for c in fake_seq.calls if c["json_mode"]]


# ---------------------------------------------------------------------------
# T1 — a short briefed lead on a thin day is a PASS, not a retry
# ---------------------------------------------------------------------------

def test_T1_a_320_word_briefed_lead_on_the_thin_day_ships_without_a_retry(
        migrated_con, fake_seq):
    """BORN RED. The whole regime change in one test.

    Slot 1 has a valid analysis brief built on the thinnest real corpus in the
    record, and the writer returns a 320-word lead. Under the floor machinery
    that is a TIER-EXPRESSION VIOLATION: a `narrative_retry` fires, the model is
    told to write "much longer", and the retry is billed. Under the length
    regime it is a pass — the material does not support 640 words and padding
    toward a minimum is the banned behaviour, so the payload ships and the run
    leaves a NOTE for D3's week-of-editions falsifier instead of an action.
    """
    con = migrated_con
    slots = [slot(1), slot(2), slot(3)]
    seed_briefing(con, EDITION, slots)
    persist_thin_slot1(con)
    sentinel = "The lead reports what the two full texts actually carry."
    fake_seq.narratives = [lead_of(slots, 320, sentinel)]
    fake_seq.scripts = [compliant_script(slots)]

    rep = run(con, fake_seq)

    # 1. no retry: not as a call, not as a step, not on the bill
    assert len(json_prompts(fake_seq)) == 2, "narrative + editor only"
    assert not any(s.get("step") == "narrative_retry" for s in rep.steps)
    assert not any(e["step"] == "narrative_retry" for e in rep.attempt_ledger)
    assert not any("TIER-EXPRESSION" in p for p in json_prompts(fake_seq))

    # 2. none of the retired rewrite-longer vocabulary survives
    for retired in ("lead tier floor", "tier floor", "retry brought the lead",
                    "retry improved", "retry did not improve", "retry skipped",
                    "floor retry failed"):
        assert not any(retired in w for w in rep.warnings), \
            (retired, rep.warnings)

    # 3. the payload SHIPS, and the short lead is disclosed as a NOTE — the one
    #    thing the regime replaced the action with (D3's falsifier data)
    assert sentinel in rep.narrative_text
    note = [w for w in rep.warnings
            if "no floor action; length regime 2026-07-30" in w]
    assert len(note) == 1, rep.warnings
    assert "316 words" in note[0] or "ran 3" in note[0]     # the measured count


# ---------------------------------------------------------------------------
# T2 — the editor may cut the lead; it still may not lose facts
# ---------------------------------------------------------------------------

def _thread(con, topic="Hormuz"):
    con.execute("INSERT INTO memory (topic, status) VALUES (?, 'active')",
                (topic,))
    return con.execute("SELECT id FROM memory WHERE topic = ?",
                       (topic,)).fetchone()["id"]


def _delta(con, tid, date, what, cites='["S1"]'):
    con.execute(
        "INSERT INTO thread_deltas (thread_id, edition_date, slot, verdict,"
        " what_happened, significance, cites_json)"
        " VALUES (?, ?, ?, 'advances', ?, '', ?)", (tid, date, 1, what, cites))
    con.commit()


CALLBACK = " The naval blockade was imposed Jul 9."


def _seed_callback_day(con):
    """A dated ledger callback with a matching record delta — the A9 specimen,
    on this module's edition."""
    tid = _thread(con)
    _delta(con, tid, "2026-07-09", "U.S. imposed a naval blockade of the strait")
    slots = [slot(1, mem=["Hormuz"]), slot(2), slot(3)]
    seed_briefing(con, EDITION, slots)
    persist_thin_slot1(con)
    return slots


def test_T2_a_shorter_valid_edit_is_no_longer_discarded_for_length(
        migrated_con, fake_seq):
    """BORN RED. The editor's cut power stops carrying a floor.

    The draft clears the old 450 floor; the edit comes back at ~300 words with
    the specifics and the dated A9 callback INTACT. Under the floor machinery
    that valid edit is thrown away for its length alone and the longer draft
    ships. Under the length regime the edit is what ships."""
    con = migrated_con
    slots = _seed_callback_day(con)
    draft = lead_of(slots, 520, "Draft sentinel about the strait.")
    draft["stories"][0]["lede"] += CALLBACK
    edit = copy.deepcopy(draft)
    edit["stories"][0]["lede"] = ("The edit reports the same development in "
                                  "fewer words." + CALLBACK)
    fake_seq.narratives = [draft, edit]
    fake_seq.scripts = [compliant_script(slots)]

    rep = run(con, fake_seq)

    assert generate._lead_words(edit) < 450 <= generate._lead_words(draft)
    assert not any("editor cut the lead to" in w for w in rep.warnings), \
        rep.warnings
    assert not any("the edit was discarded" in w for w in rep.warnings), \
        rep.warnings
    assert "The edit reports the same development" in rep.narrative_text
    assert "Draft sentinel" not in rep.narrative_text     # the DRAFT did not ship
    assert "Jul 9" in rep.narrative_text                  # callback survived
    # Gate rider (R4, F1): the note measures the SHIPPED lead. The edit
    # ships at ~300 words, so this run carries exactly one note naming the
    # EDIT's count — born red against the draft-side placement it replaces
    # (the 520-word draft drew no note there).
    notes = [w for w in rep.warnings if "length regime 2026-07-30" in w]
    assert len(notes) == 1, rep.warnings
    assert f"ran {generate._lead_words(edit)} words" in notes[0], notes


def test_T2b_the_A9_fact_loss_discard_still_fires_after_the_length_discard_goes(
        migrated_con, fake_seq):
    """CARRIED INVARIANT (born green), and the paired assertion that makes T2
    safe. The two discards share one degrade seam. Removing the LENGTH one must
    not remove the FACT one, so this edit is length-neutral (both sides clear
    450) and loses only the dated callback — A9 must still discard it."""
    con = migrated_con
    slots = _seed_callback_day(con)
    draft = lead_of(slots, 520, "Draft sentinel about the strait.")
    draft["stories"][0]["lede"] += CALLBACK
    edit = copy.deepcopy(draft)
    edit["stories"][0]["lede"] = draft["stories"][0]["lede"].replace(CALLBACK, "")
    fake_seq.narratives = [draft, edit]
    fake_seq.scripts = [compliant_script(slots)]

    rep = run(con, fake_seq)

    assert generate._lead_words(edit) >= 450, "length-neutral by construction"
    assert any("A9-DEGRADE" in w for w in rep.warnings), rep.warnings
    assert any("the edit was discarded" in w and "A9 preserve-enforcement" in w
               for w in rep.warnings), rep.warnings
    assert any(s.get("step") == "a9_preserve_degrade" for s in rep.steps)
    assert "Jul 9" in rep.narrative_text          # the draft, callback intact


# ---------------------------------------------------------------------------
# T3 — no floor language reaches the model
# ---------------------------------------------------------------------------

FLOOR_STRINGS = ("NEVER under", "never under 550", "absolute floor")
FLOOR_MSG_MARKERS = ("TIER-EXPRESSION VIOLATION", "story 1 (the lead) ran",
                     "an absolute floor of")


def test_T3_no_built_prompt_carries_floor_language(migrated_con, fake_seq):
    """BORN RED. The steering the model actually receives, checked on the REAL
    built prompts of a real run — not on the template file.

    Spec-4(a) T3 names four things: "NEVER under", "never under 550",
    "absolute floor", and the floor_msg block. Spec-4(b).1 deletes two more on
    the same surface (slot 2-3's "floor 350", slot 4+'s "floor 180"), so those
    are pinned here too — a floor deleted from the lead while the medium tier
    keeps one is not the ratified regime."""
    con = migrated_con
    slots = [slot(1), slot(2), slot(3)]
    seed_briefing(con, EDITION, slots)
    persist_thin_slot1(con)
    fake_seq.narratives = [lead_of(slots, 320, "A short honest lead.")]
    fake_seq.scripts = [compliant_script(slots)]

    run(con, fake_seq)

    for prompt in json_prompts(fake_seq):
        low = prompt.lower()
        for s in FLOOR_STRINGS:
            assert s.lower() not in low, f"{s!r} still reaches the model"
        for m in FLOOR_MSG_MARKERS:
            assert m not in prompt, f"floor_msg block survives: {m!r}"

    # the unit surface the prompts are built from (Spec-4(b).1, all three tiers)
    lead, med, quick = (generate._slot_budget_line(1),
                        generate._slot_budget_line(2),
                        generate._slot_budget_line(4))
    assert "never under" not in lead.lower() and "550" not in lead
    assert "reads as short as a slot-2 story is a failure" not in lead
    assert "floor 350" not in med
    assert "floor 180" not in quick

    # and the constant itself is no longer a floor
    assert not hasattr(generate, "LEAD_FLOOR_WORDS")
    assert generate.LEAD_SHORT_NOTE_WORDS == 450     # a disclosure threshold


# ---------------------------------------------------------------------------
# T4 — the regression in the other direction
# ---------------------------------------------------------------------------

def test_T4_a_rich_corpus_slot1_keeps_its_640_target_steering(
        migrated_con, fake_seq):
    """CARRIED INVARIANT (born green, and it must STAY green). Floors became
    TARGETS — they did not become nothing. On a day with material to spend, the
    writer must still be steered to ~640 words and to the lead's ordering
    primacy; only the punitive minimum is gone."""
    con = migrated_con
    slots = [slot(1), slot(2), slot(3)]
    seed_briefing(con, EDITION, slots)
    persist_rich_slot1(con)
    fake_seq.narratives = [lead_of(slots, 660, "A lead that spends the day.")]
    fake_seq.scripts = [compliant_script(slots)]

    rep = run(con, fake_seq)

    narrative_prompt = json_prompts(fake_seq)[0]
    assert "TARGET ~640 words" in narrative_prompt
    assert "LONGEST story of the day" in narrative_prompt   # ordering survives
    assert "TARGET ~440" in narrative_prompt                # medium tier target
    # slot 4+ does not exist on a 3-slot day, so its target is pinned on the
    # line the prompt is built from rather than on this prompt
    assert "TARGET ~220" in generate._slot_budget_line(4)
    # a lead at target draws no note at all
    assert not any("length regime 2026-07-30" in w for w in rep.warnings)
    assert len(json_prompts(fake_seq)) == 2
