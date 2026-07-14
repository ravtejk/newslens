"""P3 — podcast-quality milestone (implementer-written; QA extends).
Offline; the liveness tests fail without the wiring (ENGINEERING.md BUG17
rule: enforcement is born with the red only it can flip)."""

import json
import time

import pytest

from newslens import db, generate

from test_generate import (compliant_script, seed_briefing, slot,
                           stories_payload)
from test_m3_qa import _stage_fakes, fake_chat  # noqa: F401 (fixture)

DATE = "2026-07-07"
ENV = {"OPENAI_API_KEY": "sk-qa-fake"}


# --- P3 #8: the deterministic TTS-safe pass (unit) --------------------------

@pytest.mark.parametrize("before,after", [
    ("OPEC+ raised output.", "OPEC plus raised output."),
    ("a $5T package", "a 5 trillion dollars package"),
    ("$1.2 billion in aid", "1.2 billion dollars in aid"),
    ("costs $188,000 today", "costs 188 thousand dollars today"),
    ("800,000 barrels", "800 thousand barrels"),
    ("3,000,000 people", "3 million people"),
    ("the 2024-2026 window", "the 2024 to 2026 window"),
    ("up 5% on the day", "up 5 percent on the day"),
])
def test_tts_safe_transforms(before, after):
    out, notes = generate.tts_safe_pass(before)
    assert out == after
    assert notes  # every transform discloses


def test_tts_safe_pass_is_idempotent_and_leaves_prose_alone():
    text = "OPEC plus holds. Prices rose 5 percent. A plus for markets."
    out, notes = generate.tts_safe_pass(text)
    assert out == text and notes == []
    once, _ = generate.tts_safe_pass("OPEC+ and $5T and 800,000")
    twice, notes2 = generate.tts_safe_pass(once)
    assert twice == once and notes2 == []


# --- P3 #2/#3/#4: validate_script warns (through the wired surface) --------

def _inputs():
    return {"slots": [slot(1)]}


def _narr(extra=""):
    return ("The summit opens Tuesday. Officials expect a pledge. " + extra)


def test_never_repeat_promoted_out_of_the_warn_channel():
    """P3.1 pin FLIP (implementer-authored pin, flipped by its author):
    the principal ruling 2026-07-06 promoted never-repeat from warn-grade
    to the structural hard-with-retry class. Two poles pinned: (a) the
    warn channel NO LONGER carries it — a silent regression back to
    warn-grade would fail here; (b) the same reuse, expressed as sections,
    is caught by script_structural_check (full enforcement liveness lives
    in test_p31_enforcement.py)."""
    reused = "the most consequential bilateral meeting of the summit"
    text = (f"Today brings {reused}. It's Tuesday, July 7. Here's what "
            f"matters today. First up: {reused}, where leaders gather. "
            + "That's your briefing.")
    _, _, warns = generate.validate_script(text, _narr(reused), _inputs())
    assert not any("never-repeat" in w for w in warns)
    para_a = (f"Today the wires bring us {reused}, and the day's shape "
              "follows from that single story.")
    para_b = (f"First up this hour: {reused}, where the leaders gather "
              "and the stakes are plain.")
    out = generate.script_structural_check(para_a + "\n\n" + para_b)
    assert any("retell the same material" in v for v in out)


def test_rhythm_warn_fires_on_three_long_sentences():
    long_s = ("This sentence carries far too many words for the ear and "
              "keeps adding clauses until any listener has lost the thread "
              "of what it was even about at the start. ")
    text = "It's Tuesday, July 7. " + long_s * 3 + "That's your briefing."
    _, _, warns = generate.validate_script(text, _narr(long_s), _inputs())
    assert any("rhythm (P3 #3)" in w for w in warns)


def test_register_warn_fires_on_written_constructions():
    text = ("It's Tuesday, July 7. The former rose; the latter fell. "
            "That's your briefing.")
    _, _, warns = generate.validate_script(text, _narr(), _inputs())
    hit = next(w for w in warns if "speech-not-prose (P3 #4)" in w)
    assert "the latter" in hit and "semicolon" in hit


def test_clean_script_draws_no_p3_warns():
    text = ("Something happened. It matters because of a named reason. "
            "It's Tuesday, July 7. Here's what matters today. New details "
            "arrived this morning. That's your briefing.")
    _, _, warns = generate.validate_script(text, _narr("New details arrived"),
                                           _inputs())
    assert not any("P3 #" in w for w in warns)


# --- Liveness: the wiring reds (fail without the call site) ----------------

def test_LIVENESS_tts_safe_pass_reaches_the_persisted_script(
        tmp_paths, fake_chat, monkeypatch):
    """Fails if tts_safe_pass is defined but never called on the accepted
    script (the BUG17 dead-validator class): the persisted script_text and
    the run warnings must both carry the pass's work."""
    db.migrate()
    con = db.connect()
    try:
        slots = _stage_fakes(monkeypatch)
        payload = stories_payload(slots)
        payload["stories"][0]["lede"] += " OPEC+ moved output."
        fake_chat.narrative = payload
        script = compliant_script(slots)
        fake_chat.script = script.replace(
            "That's your briefing.",
            "OPEC+ agreed to move. That's your briefing.")
        rep = generate.run_generate(date=DATE, con=con, env=dict(ENV),
                                    refresh=True)
        assert "OPEC plus agreed to move." in rep.script_text
        assert "OPEC+" not in rep.script_text
        assert any("tts-safe pass (P3 #8" in w for w in rep.warnings)
        row = con.execute("SELECT script_text FROM briefings WHERE date=?",
                          (DATE,)).fetchone()
        assert "OPEC plus agreed to move." in row["script_text"]
    finally:
        con.close()


# --- Backlog-minors batch: NOTES 28a/28c liveness pins ----------------------

def test_28a_keyless_refusal_lands_in_the_generation_log(tmp_paths):
    """The one failure the record never saw: a keyless run now logs a failed
    entry exactly like every other GenerateError (fails without the check
    living inside the logged region)."""
    from newslens import paths
    db.migrate()
    con = db.connect()
    try:
        with pytest.raises(generate.GenerateError, match="OPENAI_API_KEY"):
            generate.run_generate(date=DATE, con=con, env={}, refresh=False)
        log = (paths.DATA_DIR / "generation_log.jsonl").read_text(encoding="utf-8")
        entries = [json.loads(l) for l in log.splitlines() if l.strip()]
        assert any(e.get("status") == "failed"
                   and "OPENAI_API_KEY not set" in (e.get("error") or "")
                   for e in entries)
    finally:
        con.close()


def test_28c_caveat_no_longer_appended_so_nothing_to_double():
    """NL-58 ruling 2 (DECISIONS 2026-07-10): the spoken caveat is OUT of the
    podcast. With no verbatim append, the NOTES 28c paraphrase-removal has
    nothing to double against and is retired — a model paraphrase is simply
    left untouched, and the frozen caveat is never inserted. (Was
    test_28c_caveat_paraphrase_is_replaced_never_doubled — flipped.)"""
    from test_generate import _inputs_for, slot
    paraphrase = ("Remember that outlet counts just measure pickup across "
                  "sources and are no guarantee of truth or wire independence.")
    script = ("Something happened today. It's Tuesday, July 7. Here's what "
              "matters today. Details arrived. What I'm watching: the vote. "
              + paraphrase + " " + generate.SIGNOFF + " " + "pad " * 40)
    body, _, warns = generate.validate_script(
        script, "Something happened. Details arrived. The vote.",
        _inputs_for([slot(1)]))
    assert generate.SPOKEN_CAVEAT not in body             # never appended
    assert paraphrase in body                             # model text untouched
    assert not any("PARAPHRASE removed" in w for w in warns)  # logic retired
