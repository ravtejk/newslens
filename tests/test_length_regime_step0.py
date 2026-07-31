"""Spec-4(c) STEP 0 — the ceiling re-base, and why it is measurement honesty.

NL-118 content-enforcement leg, batch B item 1 (charter 2026-07-31; the
principal's 2026-07-30 length-regime ratification is what it serves).

THE ONE CHANGE, stated exactly: `arc` enters `_prose_words`, and the tier
budgets move medium 400 -> 450 and full 700 -> 750 in the SAME step. The
`WORD_CEILING_FACTOR` of 1.2 is untouched, so the ceilings become 540 and 900.

Why those two halves are one change and cannot be split: the arc RENDERS. It
is on the reader's page (`analysis.py` ARC block) and it has never been in the
number the ceiling binds — roughly 55 words, about 14% of a medium budget,
invisible to the validator. Landing the arc alone would tighten the real
ceiling by ~14% overnight; landing the budgets alone would genuinely widen it.
Together they re-base the SAME behaviour onto an honest measurement: the
observed ~487-word median prose plus ~55 words of arc is ~540, which is the
new ceiling. This is the measurement catching up with the product, not the
product being given more room.

BORN RED at d0df055 — all four tests. S1 fails because the arc is invisible;
S2 because the budgets are 400/700; S3 because the ceiling that raises is 480
computed over an arc-exclusive count; S4 because the exception's own numbers
are the pre-base ones.
"""
import pytest

from newslens import analysis

from test_analysis_brief import corpus_of, good_brief, sources_fixture


ARC_WORDS = ("what_happened", "significance", "what_changed")


def _arc_word_count(arc):
    """The arc's rendered words, counted the way the ARC block renders them:
    the NL-63 two-clause shape when present, else the legacy single clause."""
    if arc.get("what_happened"):
        return len((arc.get("what_happened", "") + " "
                    + arc.get("significance", "")).split())
    return len(arc.get("what_changed", "").split())


def _prose_only(clean):
    """Every prose carrier EXCEPT the arc — the pre-step-0 measurement."""
    return len(" ".join(
        [p.get("fact", "") for p in clean["pinned_facts"]]
        + [e.get("claim", "") for e in clean["ledger"]
           if not e.get("discrepancy")]
        + [clean["mechanism"]]
        + [e["effect"] for e in clean["effects"]]
        + [u.get("question", "") + " " + u.get("why_material", "")
           + " " + u.get("would_resolve", "") for u in clean["unknowns"]]
        + [w.get("observable", "") for w in clean["watch"]]).split())


def test_S1_the_arc_is_inside_the_number_the_ceiling_binds():
    """BORN RED. The arc renders on the reader's page and was invisible to the
    validator's word count. After step 0 the measured number MOVES by exactly
    the arc's rendered length — not approximately, exactly."""
    src = sources_fixture()
    b = good_brief()
    b["arc"] = {"delta": "advances",
                "what_happened": "The summit opened with the spending pledge "
                                 "still unsigned by three members.",
                "significance": "It moves the dispute from staging into the "
                                "room where the communique is written.",
                "cites": ["P1"]}
    clean, _ = analysis.validate_brief(b, src, "medium", corpus_of(src))
    arc_n = _arc_word_count(clean["arc"])
    assert arc_n > 0, "the fixture's arc carries no words — test is inert"

    measured = analysis._prose_words(
        clean["pinned_facts"], clean["ledger"], clean["mechanism"],
        clean["effects"], clean["unknowns"], clean["watch"], clean["arc"])
    assert measured == _prose_only(clean) + arc_n, (
        f"arc is not in the counted number: measured={measured}, "
        f"prose-only={_prose_only(clean)}, arc={arc_n}")


def test_S2_the_budgets_are_re_based_and_the_factor_is_untouched():
    """BORN RED. medium 400->450, full 700->750, x1.2 UNCHANGED. The factor is
    pinned because re-basing by moving the factor instead would be a different
    (and unratified) change."""
    assert analysis.WORD_BUDGETS["medium"] == 450
    assert analysis.WORD_BUDGETS["full"] == 750
    assert analysis.WORD_CEILING_FACTOR == 1.2
    assert int(analysis.WORD_BUDGETS["medium"]
               * analysis.WORD_CEILING_FACTOR) == 540
    assert int(analysis.WORD_BUDGETS["full"]
               * analysis.WORD_CEILING_FACTOR) == 900


def test_S3_a_brief_over_540_ARC_INCLUSIVE_is_what_raises():
    """BORN RED. The teeth are on the arc-inclusive number: a brief whose
    NON-arc prose sits comfortably under 540 but whose arc carries it over is
    rejected, and it is rejected AT 540."""
    src = sources_fixture()
    b = good_brief()
    # 78 baseline prose words + 8/rep => 518 non-arc, +90 of arc => 608.
    b["mechanism"] = "Each member government answers to its own parliament. " * 55
    b["arc"] = {"delta": "advances",
                "what_happened": "Ministers argued past midnight. " * 12,
                "significance": "The communique text is now the fight. " * 6,
                "cites": ["P1"]}
    with pytest.raises(analysis.BriefOverCeiling) as over:
        analysis.validate_brief(b, src, "medium", corpus_of(src))
    assert over.value.ceiling == 540 and over.value.budget == 450
    assert over.value.words > 540

    # ...and the arc is what carried it over. `arc` is a MANDATORY section, so
    # it is SHRUNK rather than removed: the identical brief with a short arc
    # ships, which is only possible if the long arc is what raised.
    b["arc"] = {"delta": "advances", "what_changed": "staging ends.",
                "cites": ["P1"]}
    clean, warnings = analysis.validate_brief(b, src, "medium", corpus_of(src))
    assert _prose_only(clean) <= 540, (
        "fixture drifted — the non-arc prose must be UNDER the ceiling for "
        "this test to prove the arc is what raised")
    assert any("word" in w and "ceiling" in w for w in warnings)


def test_S4_over_budget_still_only_warns_between_450_and_540():
    """BORN RED (on the numbers). The two-tier contract is unchanged in SHAPE:
    over BUDGET warns, past BUDGET x 1.2 raises. Only the numbers re-base."""
    src = sources_fixture()
    b = good_brief()
    # 78 baseline + 8/rep + 9 words of legacy arc => 487, the warn band.
    b["mechanism"] = "Each member government answers to its own parliament. " * 50
    clean, warnings = analysis.validate_brief(b, src, "medium", corpus_of(src))
    measured = analysis._prose_words(
        clean["pinned_facts"], clean["ledger"], clean["mechanism"],
        clean["effects"], clean["unknowns"], clean["watch"], clean.get("arc"))
    assert 450 < measured <= 540, f"fixture drifted out of the warn band: {measured}"
    assert any("word" in w and "ceiling" in w for w in warnings)
    assert any("450-word" in w for w in warnings), (
        f"the warning still quotes the pre-base budget: {warnings}")
