"""P3 QA — podcast-quality milestone, adversarial extension (QA-written;
extends tests/test_p3_script.py). Offline; zero consumption events; the
principal's ear-test wav artifacts are never touched.

Focus per dispatch: (1) form-never-facts probed per transform class,
(2) idempotence as fixed points, (3) the A+ ruling case pinned for the
gate, (4) detector quality at boundaries and documented false positives,
(5) liveness-red verification (procedural — see report), (6) hard rules
untouched / detectors warn-grade only.

P3.1 UPDATE (QA, 2026-07-09): focus 6's "warn-grade only" was overruled
by principal ruling (DECISIONS.md 2026-07-06 batch) for the repetition
class — promoted to script_structural_check's hard-with-retry, enforced
in test_p31_enforcement.py. Three pins in this file were re-characterized
accordingly (see their docstrings); rhythm + register remain warn-grade.

RESOLVED:
  BUG18  FIXED (P3.1): both currency rules now consume an optional
         trailing " dollars" (generate.py _TTS_CURRENCY_SUFFIX_RE /
         _TTS_CURRENCY_BARE_RE) — test_BUG18_* below is green and stays
         as the regression pin.

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


def test_proper_noun_recurrence_fp_narrowed_not_relocated():
    """RE-CHARACTERIZED for P3.1 (was: recurring agency names are a
    documented warn-grade FP, with a day-14 threshold/allowlist dial).
    The principal ruling (DECISIONS.md 2026-07-06 batch) promoted
    never-repeat out of validate_script's warn channel into
    script_structural_check's paragraph-pair shape: two >=15-word
    sections sharing >=3 distinct 6-grams. That shape NARROWS the FP
    class rather than relocating it — a single recurring 6-word
    proper-noun phrase yields at most 1-2 shared grams and stays silent;
    the same name carried inside a >=8-word retold run crosses the
    threshold and correctly fires (echo, not furniture). The promised
    dial is no longer needed for this class."""
    agency = "the international atomic energy agency said"
    # The old FP shape no longer draws ANY warn — the channel was removed
    # (promotion, not duplication; generate.py validate_script).
    text = (f"Overnight, {agency} inspections resumed. It's Friday, July 17. "
            f"Later in the hour, {agency} more. That's your briefing.")
    _, _, warns = generate.validate_script(text, agency, _inputs())
    assert not any("never-repeat" in w for w in warns)
    # New structural shape, the bare recurring name: SILENT (FP gone).
    p1 = (f"Overnight the inspections resumed at pace, {agency}, in its "
          "first statement of the week.")
    p2 = (f"Later in the hour there was more, {agency}, in a follow-up "
          "note to member states.")
    assert generate.script_structural_check(p1 + "\n\n" + p2) == []
    # Same name inside an 8-word retold run: fires — the narrowed line.
    said8 = agency + " on monday"
    p3 = (f"Overnight the inspections resumed at pace, {said8}, in its "
          "first statement of the week.")
    p4 = (f"Later in the hour there was more, {said8}, in a follow-up "
          "note to member states.")
    out = generate.script_structural_check(p3 + "\n\n" + p4)
    assert any("retell the same material" in v for v in out)


def test_repetition_is_paragraph_based_anchor_limit_is_cold_open_only():
    """RE-CHARACTERIZED for P3.1 (was: no dateline = never-repeat silent,
    a documented anchor limitation; two-digit days anchor fine). The
    promoted repetition check is paragraph-pair based and DATELINE-FREE —
    the old evasion (drop the dateline, repeat freely) is closed. The
    anchor limitation survives only where the anchor is load-bearing:
    the cold-open cap (its evasion variants are pinned in
    test_p31_enforcement.py). The two-digit-day regression retargets to
    that surviving consumer."""
    run8 = "alpha bravo charlie delta echo foxtrot golf hotel"
    a = f"Opening frame words here, {run8}, and then some closing words too."
    b = f"Different frame entirely now, {run8}, with another ending altogether."
    out = generate.script_structural_check(a + "\n\n" + b)  # no dateline anywhere
    assert any("retell the same material" in v for v in out)
    assert not any(v.startswith("cold open") for v in out)
    # Warn channel stays empty even WITH a dateline present.
    reused = "a six word phrase repeated verbatim"
    with_17 = (f"Open with {reused}. It's Friday, July 17. Then {reused}. "
               "That's your briefing.")
    _, _, warns = generate.validate_script(with_17, reused, _inputs())
    assert not any("never-repeat" in w for w in warns)
    # Two-digit day anchors the cold-open cap fine (July 17).
    four = ("One moved. Two answered. Three held. Four slipped. "
            "It's Friday, July 17. Here's what matters today.\n\n"
            "Body follows in a longer paragraph with enough words to be a "
            "real section of spoken prose here today.")
    assert any(v.startswith("cold open runs 4 sentences")
               for v in generate.script_structural_check(four))


def test_register_detector_lists_each_construction_once():
    text = ("It's Tuesday, July 7. They spoke respectively of the "
            "aforementioned deal. That's your briefing.")
    _, _, warns = generate.validate_script(text, "deal", _inputs())
    hit = next(w for w in warns if "speech-not-prose (P3 #4)" in w)
    assert "respectively" in hit and "aforementioned" in hit
    assert "semicolon" not in hit


def test_rhythm_and_register_stay_warn_grade_repetition_hard_by_ruling(tmp_paths):
    """CONSCIOUS FLIP of the original all-three-warn-grade pin — overruled
    BY PRINCIPAL RULING (DECISIONS.md 2026-07-06 batch: the never-repeat
    warn fired on the exact run that shipped to his ears; a warn was not
    enforcement). What SURVIVES of the original contract, still asserted:
    rhythm (P3 #3) and register (P3 #4) stay warn-grade, and
    validate_script's HARD list is untouched by every P3 detector. The
    ONLY promoted class is the structural pair (cold-open cap +
    cross-section repetition) in script_structural_check, behind exactly
    one disclosed retry (liveness + spend-proof in
    test_p31_enforcement.py) — no further hard-reject class rode in with
    the promotion."""
    reused = "the most consequential meeting of the year"
    long_s = _sentence(26)
    text = (f"Today: {reused}. It's Tuesday, July 7. {reused} again; "
            f"the latter matters. {long_s} {long_s} {long_s} "
            "That's your briefing.")
    _, hard, warns = generate.validate_script(text, reused + " word end",
                                              _inputs())
    assert hard == []  # stronger than the original 'no P3 #' pin
    assert any("rhythm (P3 #3)" in w for w in warns)
    assert any("speech-not-prose (P3 #4)" in w for w in warns)
    assert not any("never-repeat" in w for w in warns)  # promoted, not doubled
    # The promoted home catches the same reuse once it is sectioned:
    pa = (f"Today the wires bring {reused} and its consequences, framed "
          "at the open for everyone listening.")
    pb = (f"The lead this hour returns to {reused} and its consequences, "
          "told once more in the body.")
    out = generate.script_structural_check(pa + "\n\n" + pb)
    assert any("retell the same material" in v for v in out)
