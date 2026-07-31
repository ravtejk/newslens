"""Spec-1 — the editorial-marks canon, enforced.

NL-118 content-enforcement leg, batch B item 4. The content round ruled on six
worked spans from the founder DB and produced a canon; this is that canon with
teeth.

Inside a quoted span, LAWFUL:
  (a) square-bracket insertion/substitution
  (b) ellipsis-marked omission (`...`/`…`)
  (c) typographic normalization already symmetric (curly, dashes, case)
  (d) boundary punctuation (candidate side, shipped)
UNLAWFUL: any UNMARKED wording alteration — inflection, compression,
substitution.

The promotion the round ruled YES on: BOTH quote families are now enforced by
MARKS-LAWFULNESS rather than by delimiter glyph, and the penalty is a reject
carrying ONE instructed redraft (`BriefQuoteUnmarked`), never the bare
one-line `raise` the round explicitly REFUSED. `notes_for_writer` keeps its disclosure warning and is exempt from reject
for the SINGLE family only — the double family keeps its Gate-residual-2
hard reject (M6b holds the narrowing; the pin wins over Spec-1's broader
scope sentence).

BORN RED at the pre-marks tree: M1 (format chars not normalized), M2/M3 (a
lawful bracket/ellipsis span in the DOUBLE family is hard-rejected), M4 (the
cross-source splice raises the wrong class), M5 (an unlawful SINGLE-quoted
span only warns — the promotion is the whole point), M7 (no redraft exists),
M8 (no prompt rider).
"""
import pytest

from newslens import analysis

from test_analysis_brief import good_brief, sources_fixture


def _sources(text, second=None):
    src = {"S1": {"kind": "article", "outlet": "BBC", "title": "",
                  "url": "https://bbc.example/1", "retrieved_at": "2026-07-20",
                  "published_at": "2026-07-20", "text": text}}
    if second is not None:
        src["C1"] = {"kind": "article", "outlet": "Reuters", "title": "",
                     "url": "https://reuters.example/1",
                     "retrieved_at": "2026-07-20",
                     "published_at": "2026-07-20", "text": second}
    for oid, keys in analysis.outlet_index(src).items():
        for k in keys:
            src[k]["outlet_id"] = oid
            src[k]["outlet_keys"] = list(keys)
    return src


def _brief_quoting(span, field="mechanism", cites=("S1",)):
    """A minimal valid brief that carries `span` in one prose field."""
    b = good_brief()
    b["pinned_facts"] = [{"fact": "The ministry published the notice on 20 "
                                  "July.", "cites": list(cites)}]
    b["ledger"] = [{"claim": "The notice names the review board.",
                    "cites": list(cites)}]
    b["effects"] = []
    b["arc"] = None
    b["unknowns"] = [{"question": "The ministry has not named the board chair",
                      "why_material": "the chair decides the marking rules",
                      "would_resolve": "the board's own published roster"}]
    b["watch"] = [{"observable": "Any roster published by the review board",
                   "settles": "who chairs it", "basis": "mechanical",
                   "cites": list(cites)},
                  {"observable": "Any further notice from the ministry",
                   "settles": "whether the rules change", "basis": "mechanical",
                   "cites": list(cites)}]
    b["mechanism"] = "The board sets the rules [S1]."
    if field == "mechanism":
        b["mechanism"] = f'The board says {span} [S1].'
    elif field == "notes_for_writer":
        b["notes_for_writer"] = f"The board says {span}."
    return b


def _validate(b, src):
    return analysis.validate_brief(b, src, "medium",
                                   analysis.verbatim_corpus(src))


# ---------------------------------------------------------------------------
# M1 — the normalization precondition
# ---------------------------------------------------------------------------

def test_M1_format_characters_and_nbsp_normalize_away():
    """BORN RED. Brief 49's real bytes: the source carries a WORD JOINER
    (U+2060) mid-phrase, so a faithful quotation of it was called fabrication.
    Format characters carry no glyph and no meaning; a reader cannot see them
    and neither should the verbatim check. Symmetric on both sides, so it can
    only repair a glyph-variant match and never manufacture one."""
    assert analysis._norm_glyphs("United ⁠States") == "United States"
    for ch in ("​", "‌", "‍", "⁠", "﻿", "­"):
        assert analysis._norm_glyphs(f"co{ch}operate") == "cooperate", ch
    assert analysis._norm_glyphs("United States") == "United States"
    # ...and the shipped curly-glyph normalization is untouched
    assert analysis._norm_glyphs("“quoted”") == '"quoted"'


# ---------------------------------------------------------------------------
# M2/M3 — the two lawful marks, in the family that HARD-REJECTS
# ---------------------------------------------------------------------------

def test_M2_a_bracketed_span_is_lawful_in_the_double_family():
    """BORN RED. Spec-1's brief-49 span, verbatim, in the family that rejects.
    The bracket is the DISCLOSED mark: inline it and the span is verbatim."""
    src = _sources("Iran stands down as long as the United ⁠States "
                   "maintains its own halt on strikes.")
    b = _brief_quoting('"as long as the United States maintain[s]"')
    clean, warnings = _validate(b, src)
    assert clean["mechanism"]


def test_M3_an_ellipsis_elision_is_lawful_in_the_double_family():
    """BORN RED. Spec-1's brief-39 span: the ellipsis marks the omission, and
    every fragment is verbatim and IN ORDER inside one source."""
    src = _sources("The general said the army is not only holding the line "
                   "but is advancing on every front this week.")
    b = _brief_quoting('"not only holding the line but... advancing"')
    clean, warnings = _validate(b, src)
    assert clean["mechanism"]


# ---------------------------------------------------------------------------
# M4 — what the per-source rule exists to kill
# ---------------------------------------------------------------------------

def test_M4_an_ellipsis_may_not_splice_two_sources_together():
    """The ellipsis rule is PER SOURCE, not against the concatenated blob.
    Two true fragments from two different outlets, joined by an ellipsis, is a
    quotation of a sentence nobody wrote — the exact fabrication a marks
    allowance could otherwise smuggle in."""
    src = _sources("The minister resigned on 20 July after the review.",
                   "The committee met on 20 July without any warning.")
    b = _brief_quoting('"The minister resigned... without any warning"',
                       cites=("S1", "C1"))
    with pytest.raises(analysis.BriefQuoteUnmarked):
        _validate(b, src)


def test_M4b_the_same_elision_inside_ONE_source_is_lawful():
    """The paired assertion — M4 must fail because of the SPLICE, not because
    the mechanics are broken."""
    src = _sources("The minister resigned on 20 July after the committee met "
                   "without any warning from the ministry.")
    b = _brief_quoting('"The minister resigned... without any warning"')
    clean, _ = _validate(b, src)
    assert clean["mechanism"]


def test_M4c_fragments_must_appear_in_SOURCE_ORDER():
    src = _sources("The minister resigned on 20 July after the committee met "
                   "without any warning from the ministry.")
    b = _brief_quoting('"without any warning... The minister resigned"')
    with pytest.raises(analysis.BriefQuoteUnmarked):
        _validate(b, src)


# ---------------------------------------------------------------------------
# M5 — THE PROMOTION
# ---------------------------------------------------------------------------

def test_M5_an_unmarked_alteration_in_the_SINGLE_family_now_rejects():
    """BORN RED, and this is the promotion itself. Spec-1's brief-27 span:
    the source says "how you LOSE the AI race", the brief said "losing the AI
    race" — an unmarked inflection. Before the promotion the single family
    only WARNED, which is how it reached the reader."""
    src = _sources("The chief executive asked how you lose the AI race "
                   "when the models are commodities.")
    b = _brief_quoting("'losing the AI race and the talent war'")
    with pytest.raises(analysis.BriefQuoteUnmarked) as exc:
        _validate(b, src)
    assert isinstance(exc.value, analysis.BriefRejected)


def test_M5b_the_two_families_are_now_symmetric():
    """Enforcement is by MARKS-LAWFULNESS, not by delimiter glyph: the same
    lawful span passes in both families and the same unlawful span fails in
    both."""
    src = _sources("Iran stands down as long as the United States maintains "
                   "its own halt on strikes.")
    for span in ('"as long as the United States maintain[s]"',
                 "'as long as the United States maintain[s]'"):
        clean, _ = _validate(_brief_quoting(span), src)
        assert clean["mechanism"], span
    for span in ('"as long as the United States held back"',
                 "'as long as the United States held back'"):
        with pytest.raises(analysis.BriefQuoteUnmarked):
            _validate(_brief_quoting(span), src)


# ---------------------------------------------------------------------------
# M6 — the exempt channel
# ---------------------------------------------------------------------------

def test_M6_notes_for_writer_is_exempt_and_still_disclosed():
    """Spec-1 scope: `notes_for_writer` is an INTERNAL channel that never
    renders to the reader, so it keeps the disclosure warning and never costs
    a brief. Brief 40 is the worked example — and its span is SINGLE-quoted,
    which is what makes the narrowing in M6b cost Spec-1 nothing."""
    src = _sources("Three US military personnel were killed by an Iranian "
                   "attack on a base in Jordan on 20 July.")
    b = _brief_quoting("'three US personnel killed in Jordan'",
                       field="notes_for_writer")
    clean, warnings = _validate(b, src)
    assert clean["notes_for_writer"]
    assert any("notes_for_writer" in w and "not a verbatim" in w
               for w in warnings), warnings


def test_M6b_the_exemption_does_NOT_un_pin_the_double_quote_reject():
    """SPEC-1 vs AN ORDERED GATE PIN — the pin wins, and this is where that is
    held.

    Spec-1 scope says "`notes_for_writer` exempt from reject". The Gate
    residual 2 ORDERED PIN says the opposite in the product's own words, at
    the `check_quotes(notes, ...)` call site in `analysis.py`:
    notes_for_writer "cannot stay quote-exempt", because it flows into writer
    material where the fact-subset chain treats it as given. Its assertions
    live in `tests/test_analysis_brief_qa.py`
    (`test_fabricated_quote_in_notes_for_writer_hard_rejects`, docstring-
    labelled "Gate residual 2 (ordered pin)", and its curly sibling,
    labelled "BUG11 direction-safety carried onto the newly covered
    surface"; the residual-2 label also sits on the unknowns
    green-direction test).

    A spec clause does not silently un-pin a gate-ordered assertion. The
    exemption is therefore narrowed to the minimum that satisfies both: it
    covers the SINGLE family — the one Spec-1's promotion newly enforces, and
    the one its own acceptance case lives in (founder brief 40 carries no
    double-quoted span at all). The DOUBLE family keeps the hard reject it
    already had here, and marks-lawfulness still governs both families.

    This test exists so the narrowing cannot be silently widened back."""
    src = _sources("The ministry published the roster on 20 July.")
    b = _brief_quoting('"a fully invented closing line for the reader"',
                       field="notes_for_writer")
    with pytest.raises(analysis.BriefRejected):
        _validate(b, src)


# ---------------------------------------------------------------------------
# M7 — ONE instructed redraft, never a bare raise
# ---------------------------------------------------------------------------

def test_M7_the_rejection_carries_the_instruction_and_names_the_span():
    """The round REFUSED the bare one-line `raise`. The exception has to be
    actionable: it names the offending span and states the two lawful outs."""
    src = _sources("The chief executive asked how you lose the AI race.")
    b = _brief_quoting("'losing the AI race and the talent war'")
    with pytest.raises(analysis.BriefQuoteUnmarked) as exc:
        _validate(b, src)
    msg = str(exc.value)
    assert "losing the AI race" in msg
    instruction = analysis.QUOTE_REDRAFT_INSTRUCTION.format(
        span=exc.value.span, where=exc.value.where)
    low = " ".join(instruction.lower().split())
    assert exc.value.span.lower() in low, "the redraft does not name the span"
    # both lawful outs, named — the round's condition for saying yes at all
    assert "quote the exact words" in low
    assert "drop the quote marks" in low and "paraphrase" in low
    assert "keeping the citation" in low
    # ...and the marks that ARE allowed, so out 1 is actionable
    assert "square brackets" in low and "ellipsis" in low


def test_M7b_the_class_degrades_like_every_other_rejection():
    """`BriefQuoteUnmarked` is a SUBCLASS of BriefRejected on purpose: a caller
    that knows nothing about the redraft still degrades exactly as it always
    did — the same argument `BriefOverCeiling` was built on."""
    assert issubclass(analysis.BriefQuoteUnmarked, analysis.BriefRejected)


# ---------------------------------------------------------------------------
# M8 — the prompt rider
# ---------------------------------------------------------------------------

def test_M8_the_CITATIONS_rider_reaches_the_built_prompt():
    """BORN RED. Enforcement the model is never told about is a trap, not a
    rule: the canon has to be in the CITATIONS block it governs."""
    from newslens import paths
    raw = (paths.PROMPTS_DIR / "analysis_brief.txt").read_text(
        encoding="utf-8")
    # whitespace-normalized containment: the prompt hard-wraps, so a raw
    # substring test would pass or fail on where the line breaks fall
    text = " ".join(raw.split())
    for fragment in (
            "Quote marks promise EXACT words.",
            "quote the exact words, quote a shorter exact span, or drop the "
            "marks and paraphrase with the citation",
            "square brackets for an inserted/adjusted word",
            "an ellipsis (...) for an omission that does not change meaning",
            "Never splice quotations from different sources into one span."):
        assert fragment in text, fragment
    i_cit = raw.index("CITATIONS:")
    i_voice = raw.index("VOICE (Voice A, binding)")
    assert i_cit < raw.index("Quote marks promise EXACT words") < i_voice, \
        "the rider landed outside the CITATIONS block"


# ---------------------------------------------------------------------------
# M9 — the shipped behaviour the canon must not break
# ---------------------------------------------------------------------------

def test_M9_a_plainly_verbatim_quote_still_passes_untouched():
    src = sources_fixture()
    clean, _ = analysis.validate_brief(good_brief(), src, "medium",
                                       analysis.verbatim_corpus(src))
    assert clean["pinned_facts"]


def test_M9b_boundary_punctuation_tolerance_survives():
    """BUG11's candidate-side trim (NL-118 QA finding 3) is canon item (d) and
    must not be regressed by the marks work."""
    src = _sources("The ministry said the roll has been damaged permanently.")
    b = _brief_quoting('"the roll has been damaged permanently,"')
    clean, _ = _validate(b, src)
    assert clean["mechanism"]
