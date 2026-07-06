"""P3 QA — podcast-quality milestone, adversarial extension (QA-written;
extends tests/test_p3_script.py). Offline; zero consumption events; the
principal's ear-test wav artifacts are never touched.

Focus per dispatch: (1) form-never-facts probed per transform class,
(2) idempotence as fixed points, (3) the A+ ruling case pinned for the
gate, (4) detector quality at boundaries and documented false positives,
(5) liveness-red verification (procedural — see report), (6) hard rules
untouched / detectors warn-grade only.

KNOWN-RED:
  BUG18  the currency transforms don't consume an existing trailing
         "dollars", so the model's common redundancy "$2 billion dollars"
         becomes "2 billion dollars dollars" in the persisted script and
         the wav — the exact tics class P3 exists to kill. (Stable under
         re-application, but the FIRST output already stutters.) Fix:
         both currency rules consume an optional trailing \\s*dollars\\b.

Gate items in docstrings: "A+ rating" -> "A plus rating" pinned as actual
(recommend ratify — correct for ratings/blood types; enumerated-only
leaves C++/15+/leading-+ untouched, the safe direction); "$1,200,000" ->
"1,200 thousand dollars" (value-preserving, ear-hostile — mixed-magnitude
comma numbers aren't in the enumerated classes); "3-4%" keeps its dash.
"""

from __future__ import annotations

import json
import time

import pytest

from newslens import db, generate

from test_generate import compliant_script, slot, stories_payload
from test_m3_qa import _stage_fakes, fake_chat  # noqa: F401 (fixture)

DATE = "2026-07-07"
ENV = {"OPENAI_API_KEY": "sk-qa-fake"}


# ---------------------------------------------------------------------------
# 1. Transform classes, adversarially (pins-as-actual where flagged)
# ---------------------------------------------------------------------------

SAFE_UNCHANGED = [
    "a 3-2 vote on the measure",          # score: no 4-digit pair
    "C++ developers shipped it",          # not an enumerated class
    "iPhone 15+ sales figures",           # digit-led token: no letter start
    "call +1 for the operator",           # leading plus, not a suffix
    "Section 8,0001 of the code",         # not an even-thousands shape
]


@pytest.mark.parametrize("text", SAFE_UNCHANGED)
def test_non_enumerated_shapes_pass_through_untouched(text):
    """Enumerated-only is the safety property: anything outside the seven
    classes must flow through byte-identical with no disclosure."""
    out, notes = generate.tts_safe_pass(text)
    assert out == text and notes == []


ACTUAL_TRANSFORMS = [
    # (before, after, flagged-as-actual?)
    ("an A+ rating from S&P", "an A plus rating from S&P"),   # THE gate case
    ("type AB+ donors needed", "type AB plus donors needed"),
    ("a $5 trillion package", "a 5 trillion dollars package"),  # word form, no doubling
    ("the 2024–2026 window", "the 2024 to 2026 window"),   # en-dash
    ("up 3-4% this year", "up 3-4 percent this year"),          # dash survives (actual)
    ("spent $1,200,000 on repairs", "spent 1,200 thousand dollars on repairs"),
    ("rates hit 100%", "rates hit 100 percent"),
    ("a 60 % swing", "a 60 percent swing"),
]


@pytest.mark.parametrize("before,after", ACTUAL_TRANSFORMS)
def test_transform_shapes_pinned_as_actual(before, after):
    """The adversarial shapes, frozen. Two carry gate notes:

    - "an A+ rating" -> "an A plus rating": the implementer's flagged
      ruling case. Facts for the gate: correct for the ear on ratings,
      blood types, and OPEC+; the pattern requires a letter-led token and
      a terminal +, so C++/15+/leading-+ stay untouched. Recommend ratify.
    - "$1,200,000" -> "1,200 thousand dollars": value-preserving
      (1,200 x 1,000) but ear-hostile — the mixed-magnitude comma form is
      NOT one of the enumerated classes, it's the bare-currency and
      even-thousands rules composing. If the gate wants "1.2 million
      dollars", that's a new enumerated class, not a fix to these.
    - "3-4%" keeps its dash (only the %% transforms): TTS voices the dash
      inconsistently; a small-range class ("3 to 4 percent") is a gate
      option, pinned here as out of scope today."""
    out, _ = generate.tts_safe_pass(before)
    assert out == after


def test_BUG18_existing_dollars_word_must_not_double(tmp_paths):
    """KNOWN-RED (BUG18). The model routinely writes the redundancy
    "$2 billion dollars" — today's pass emits "2 billion dollars dollars"
    (and "$188,000 dollars" -> "188 thousand dollars dollars"): a stutter
    in the wav, the exact tics class P3 was cut to kill. Form-never-facts
    holds (value intact) but the form itself is now defective.

    Fix contract: both currency rules consume an optional existing
    trailing " dollars" ("\\s*dollars\\b") so the output carries exactly
    one; the ACTUAL_TRANSFORMS and idempotence pins in this file must
    keep passing; disclosure notes still fire once per application."""
    out, _ = generate.tts_safe_pass("a $2 billion dollars package")
    assert out == "a 2 billion dollars package"
    out2, _ = generate.tts_safe_pass("costs $188,000 dollars today")
    assert out2 == "costs 188 thousand dollars today"


# ---------------------------------------------------------------------------
# 2. Idempotence as fixed points (extends their one round-trip)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("before,after", ACTUAL_TRANSFORMS)
def test_every_output_is_a_true_fixed_point(before, after):
    """f(f(x)) == f(x) with EMPTY notes — a second pass that re-fires its
    disclosure would poison the warning channel even if the text held."""
    out2, notes2 = generate.tts_safe_pass(after)
    assert out2 == after and notes2 == []


def test_opec_plus_survives_a_third_pass():
    text = "OPEC+ raised output."
    for _ in range(3):
        text, _ = generate.tts_safe_pass(text)
    assert text == "OPEC plus raised output."


def test_even_the_BUG18_doubled_output_is_stable():
    """The doubling is a single-application defect, not an amplifier: the
    doubled form is itself a fixed point (documents that repeated passes
    can't make it worse while BUG18 stands)."""
    once, _ = generate.tts_safe_pass("a $2 billion dollars package")
    twice, notes = generate.tts_safe_pass(once)
    assert twice == once and notes == []


# ---------------------------------------------------------------------------
# 3. Ordering: validators see the model's own output, never the transform
# ---------------------------------------------------------------------------

def test_transforms_never_trip_the_pre_transform_fact_checks(
        tmp_paths, fake_chat, monkeypatch):
    """The form-never-facts ordering, pinned at run level: the narrative
    says "$188,000"; the model's script says "$188,000" (numeral-subset
    holds pre-transform); the persisted script says "188 thousand dollars".
    If the script-numerals check ever moved AFTER the pass, "188" vs
    "188,000" would false-warn — its absence proves the ordering."""
    db.migrate()
    con = db.connect()
    try:
        slots = _stage_fakes(monkeypatch)
        payload = stories_payload(slots)
        payload["stories"][0]["lede"] += " The repair bill is $188,000."
        fake_chat.narrative = payload
        script = compliant_script(slots)
        fake_chat.script = script.replace(
            "That's your briefing.",
            "The repair bill is $188,000. That's your briefing.")
        rep = generate.run_generate(date=DATE, con=con, env=dict(ENV),
                                    refresh=True)
        assert "188 thousand dollars" in rep.script_text
        assert "$188,000" not in rep.script_text
        assert not any("script numerals absent" in w for w in rep.warnings)
        assert any("tts-safe pass (P3 #8" in w for w in rep.warnings)
    finally:
        con.close()


# ---------------------------------------------------------------------------
# 4. Detector quality: boundaries and documented false positives
# ---------------------------------------------------------------------------

def _inputs():
    return {"slots": [slot(1)]}


def _sentence(n_words):
    return " ".join(["word"] * (n_words - 1)) + " end."


def test_rhythm_boundary_exactly_25_counts_24_resets():
    base = "It's Tuesday, July 7. "
    tail = " That's your briefing."
    three_25 = base + " ".join(_sentence(25) for _ in range(3)) + tail
    _, _, warns = generate.validate_script(three_25, "word end", _inputs())
    assert any("rhythm (P3 #3)" in w for w in warns)
    broken_run = base + " ".join(
        [_sentence(25), _sentence(25), _sentence(24), _sentence(25)]) + tail
    _, _, warns2 = generate.validate_script(broken_run, "word end", _inputs())
    assert not any("rhythm (P3 #3)" in w for w in warns2)


def test_never_repeat_proper_noun_recurrence_is_a_documented_fp():
    """Dispatch probe: a legitimately-recurring 6-gram (agency names, org
    titles) DOES warn — the detector can't tell furniture from echo.
    Pinned as the accepted warn-grade cost (a log line, never a block);
    if day-14 listening shows this drowning real repeats, the threshold
    or a proper-noun allowlist is the gate's dial."""
    agency = "the international atomic energy agency said"
    text = (f"Overnight, {agency} inspections resumed. It's Friday, July 17. "
            f"Later in the hour, {agency} more. That's your briefing.")
    _, _, warns = generate.validate_script(text, agency, _inputs())
    assert any("never-repeat (P3 #2)" in w and "atomic energy" in w
               for w in warns)


def test_never_repeat_needs_a_dateline_anchor_and_two_digit_days_work():
    """No dateline = no cold-open boundary = detector silent (documented
    limitation — the dateline is enforced by the script contract
    upstream); a two-digit day anchors fine."""
    reused = "a six word phrase repeated verbatim exactly"
    no_dateline = (f"Open with {reused}. More prose. Then {reused}. "
                   "That's your briefing.")
    _, _, warns = generate.validate_script(no_dateline, reused, _inputs())
    assert not any("never-repeat" in w for w in warns)
    with_17 = (f"Open with {reused}. It's Friday, July 17. Then {reused}. "
               "That's your briefing.")
    _, _, warns2 = generate.validate_script(with_17, reused, _inputs())
    assert any("never-repeat (P3 #2)" in w for w in warns2)


def test_register_detector_lists_each_construction_once():
    text = ("It's Tuesday, July 7. They spoke respectively of the "
            "aforementioned deal. That's your briefing.")
    _, _, warns = generate.validate_script(text, "deal", _inputs())
    hit = next(w for w in warns if "speech-not-prose (P3 #4)" in w)
    assert "respectively" in hit and "aforementioned" in hit
    assert "semicolon" not in hit


def test_all_three_detectors_are_warn_grade_never_hard(tmp_paths):
    """Focus 6: a script tripping never-repeat AND rhythm AND register
    lands in warnings with the HARD list untouched — no new reject class
    was smuggled in under the podcast milestone."""
    reused = "the most consequential meeting of the year"
    long_s = _sentence(26)
    text = (f"Today: {reused}. It's Tuesday, July 7. {reused} again; "
            f"the latter matters. {long_s} {long_s} {long_s} "
            "That's your briefing.")
    _, hard, warns = generate.validate_script(text, reused + " word end",
                                              _inputs())
    assert any("never-repeat (P3 #2)" in w for w in warns)
    assert any("rhythm (P3 #3)" in w for w in warns)
    assert any("speech-not-prose (P3 #4)" in w for w in warns)
    assert not any("P3 #" in h for h in hard)
