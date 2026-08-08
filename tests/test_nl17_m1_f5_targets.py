"""NL-17 M1 rider F-5 — the coarse-pointer tap targets, and the two build-time
asserts the design mini named as conditions of its own confidence.

WHY THIS FILE EXISTS AT ALL. The design audit (workspace/debates/2026-08-08--
newslens--design.md) found the whole `.deck-follow` family sitting at ~17px on
mobile and passing WCAG 2.5.8 AA only through the SPACING EXCEPTION — an
accident of editorial whitespace, not a property anyone chose. Its adjudication
ended: "Build-time checks (pin the math so it can't regress silently): (1) at
390px, assert the padded verb box does not intersect the nearest link box on a
today-grid card…; (2) assert acts-row boxes don't intersect each other." Those
are these tests, and assert (1) FIRED during the build — see below.

WHAT THIS PINS, AND WHAT IT DOES NOT — stated first so a green run is never
over-read. There is no layout engine here. These tests read the DECLARED values
out of the shipped stylesheet (webui.CSS, the executed artifact) and redo the
box arithmetic the audit did by hand. So they pin the ARITHMETIC RELATIONSHIP
between the padding we add and the clearance we have, and they bite the moment
either side changes — which is the regression the audit was actually worried
about ("any future tightening silently breaks AA with no test to catch it").
They do NOT prove a rendered layout: a real geometry assert needs a headless
browser, which is a new dependency and a machine-dependent suite. That residual
is named in the build report, and the browser pass is QA's.

The values are read, never restated: every number below is computed from the
CSS. A test that hard-codes 44.2px would go green against a stylesheet that no
longer produces it.

THE FALLBACK THAT FIRED. The design's ruled block used `padding: 0.85rem 0.5rem`
on the verb (44.2px, WCAG 2.5.5 AAA). At 390px a today-grid card puts
h2.headline (margin-bottom 0.4rem) directly above .deck (padding-top 0.35rem),
so the headline LINK's box sits 12.0px above the verb's — and 13.6px of upward
padding overlaps it by 1.6px. Two live targets sharing pixels is a worse defect
than the undersized target it fixes, so the design's own recorded fallback
applies: "step verb padding-block to 0.7rem (39.4px, still >24 hard) and record
it." 0.7rem = 11.2px clears by 0.8px. Recorded here, in the CSS comment, and in
the build report.

Offline by construction: no network, no key, $0.
"""

from __future__ import annotations

import re

import pytest

from newslens import webui


ROOT_PX = 16.0          # html font-size at mobile widths; the steps only go UP
                        # (17px >=1800px, 18px >=2200px — webui.CSS)
UA_BUTTON_LINE_HEIGHT = 1.2   # the UA `font` shorthand resets line-height to
                              # `normal` on <button>; this component never sets
                              # one, which is WHY the family measures ~17px


def _rem(value: str) -> float:
    """'0.7rem' -> 11.2 (px at ROOT_PX). Bare 0 is 0."""
    v = value.strip()
    if v in ("0", "0px", ""):
        return 0.0
    m = re.fullmatch(r"(-?[\d.]+)rem", v)
    assert m, f"expected a rem length, got {value!r}"
    return float(m.group(1)) * ROOT_PX


def _css(block: str = "") -> str:
    """The stylesheet with COMMENTS STRIPPED — the same discipline
    test_nl143_follow_surface_truth._css_code established, and for the same
    reason it was established for: that file's first draft went red on a
    COMMENT recording a deleted class. A pin a comment can satisfy — or
    falsify — is not a pin, and this file's comments are long and full of the
    very property names it asserts on."""
    return re.sub(r"/\*.*?\*/", " ", block or webui.CSS, flags=re.S)


def _block(selector: str, css: str = "") -> str:
    """The declaration block of the rule whose selector list is EXACTLY
    `selector`. Exact, not substring: `.deck-follow` and
    `.tracked-marker, .deck-follow` are different rules and reading one for the
    other is how a cursor assertion passes against the wrong block."""
    css = _css(css)
    # The selector group takes whatever precedes `{` since the last brace, which
    # is both correct and safe against the overlap trap: a prefix like
    # `(?:^|\})` consumes the closing brace of rule N, so rule N+1 can never
    # match. (That trap cost this file a debugging round.)
    for m in re.finditer(r"([^{}]*)\{([^{}]*)\}", css, re.S):
        sel = " ".join(m.group(1).split())
        if sel == selector:
            return m.group(2)
    raise AssertionError(f"no rule with selector exactly {selector!r}")


def _decl(selector: str, prop: str, css: str = "") -> str:
    """The LAST declared value of `prop` in that rule. Last wins, which is the
    cascade's own rule for repeated declarations in one block."""
    block = _block(selector, css)
    hits = re.findall(rf"(?:^|;)\s*{re.escape(prop)}\s*:\s*([^;]+)", block)
    assert hits, f"{prop!r} not declared on {selector!r}"
    return hits[-1].strip()


def _coarse_block() -> str:
    """The @media (pointer: coarse) block — everything F-5 landed. Comments
    stripped first (see _css)."""
    css = _css()
    i = css.index("@media (pointer: coarse)")
    depth = 0
    for k in range(css.index("{", i), len(css)):
        if css[k] == "{":
            depth += 1
        elif css[k] == "}":
            depth -= 1
            if depth == 0:
                return css[i:k + 1]
    raise AssertionError("unterminated coarse-pointer block")


def _shorthand_block(value: str) -> float:
    """The BLOCK (vertical) term of a `padding`/`margin` shorthand: one value =
    all sides; two = block then inline."""
    parts = value.split()
    return _rem(parts[0])


def _shorthand_inline(value: str) -> float:
    parts = value.split()
    return _rem(parts[1] if len(parts) > 1 else parts[0])


# ===========================================================================
# The block exists at all, and is scoped as ruled
# ===========================================================================

def test_the_treatment_is_coarse_pointer_scoped_with_axels_dissent_on_record():
    """BORN RED (no such block pre-diff). Scoping is the design's adjudication,
    not an implementation choice: unconditional padding puts the verb's
    invisible box over the strip's .smeta line and blocks text selection for
    desktop readers who copy that metadata, while desktop passes AA on the
    exception with ~30px clearances against a 3.5px overhang.

    AXEL'S DISSENT IS ON THE RECORD and is not gated: a mouse tremor does not
    care about a media query, so this leaves desktop motor-impaired readers at
    17px. Falsifier — any observed desktop miss-click drops the query, which is
    a one-line amendment to this block."""
    block = _coarse_block()
    assert ".deck-follow" in block
    assert ".fl-alts a" in block and ".fl-unfollow" in block
    # …and nothing outside the query grew a target
    outside = _css().replace(block, "")
    assert "padding-block" not in outside


def test_the_treatment_rides_the_shared_selectors_not_a_per_surface_fork():
    """BORN RED. The single-rendering law (DECISIONS.md:648): `_follow_control`
    is ONE hoisted component rendering the strip verb, the card line and the
    deep mounts. The fix has to ride the SHARED classes so one block covers
    every mount and every state — server-rendered and JS-re-rendered alike —
    with zero Python or JS touched. A `.strip-follow .deck-follow` or
    `.today-grid .deck-follow` here would be the fork the law forbids."""
    block = _coarse_block()
    for forked in (".strip-follow ", ".today-grid ", ".deck ", ".thread "):
        assert forked not in block, forked


# ===========================================================================
# ASSERT 1 — the verb, against the today-grid headline link at 390px
# ===========================================================================

def test_assert1_the_padded_verb_does_not_intersect_the_today_grid_headline():
    """BORN RED, AND THE ASSERT THAT FIRED.

    The geometry, all of it read from the stylesheet:

      h2.headline margin-bottom (0.4rem)     -- the headline LINK's box bottom
      .today-grid .deck padding-top (0.35rem)
      ------------------------------------------
      = the clearance above the verb's box top

    The verb's negative margin pulls its padded box UP by exactly its
    padding-block, so the box top sits that far above where the 17px text box
    starts. Overlap iff padding-block > clearance. At the design's ruled 0.85rem
    that is 13.6 > 12.0 — an overlap of 1.6px, two live targets sharing pixels.
    The recorded fallback (0.7rem) clears by 0.8px."""
    block = _coarse_block()
    pad = _shorthand_block(_decl(".deck-follow", "padding", block))
    neg = _shorthand_block(_decl(".deck-follow", "margin", block))

    headline_gap = _rem(_decl("h2.headline, h3.headline, h4.headline",
                              "margin").split()[-1])
    deck_pad_top = _shorthand_block(
        _decl(".today-grid article.story .deck", "padding"))
    clearance = headline_gap + deck_pad_top

    assert neg == -pad, "the compensation must exactly cancel the padding"
    assert pad < clearance, (
        f"padded verb box overlaps the today-grid headline link by "
        f"{pad - clearance:.2f}px (padding {pad}px vs clearance {clearance}px) "
        f"— step padding-block down per the design's recorded fallback")


def test_assert1b_the_verb_still_clears_the_24px_box_after_the_fallback():
    """BORN RED. The fallback is only acceptable because it stays above the AA
    box: the whole point of the audit was to stop depending on the spacing
    EXCEPTION. 2.5.5's 44px is out of reach in this adjacency and the design
    accepted that explicitly ("still >24 hard")."""
    block = _coarse_block()
    pad = _shorthand_block(_decl(".deck-follow", "padding", block))
    font = _rem(_decl(".tracked-marker, .deck-follow", "font-size"))
    box = font * UA_BUTTON_LINE_HEIGHT + 2 * pad
    assert box >= 24.0, f"verb box {box:.1f}px falls below the 2.5.8 AA box"
    assert box == pytest.approx(39.4, abs=0.2), box   # the recorded number


def test_assert1c_inline_mounts_get_a_box_to_pad():
    """BORN RED (Greta's named cost). The committed verb renders as an inline
    <a class="deck-follow"> on the strip, and padding on an inline element does
    not size its box. inline-block fixes that — and because an inline-block's
    LINE box is computed from its MARGIN box, the negative margins keep the
    strip's line height unchanged. Zero layout shift, by construction."""
    assert _decl(".deck-follow", "display", _coarse_block()) == "inline-block"


# ===========================================================================
# ASSERT 2 — the acts row
# ===========================================================================

def test_assert2_the_acts_row_grows_vertically_only():
    """BORN RED — INES'S RULING, and it is an error-model argument rather than a
    size one. Three targets share one line ~17px apart and one of them is
    DESTRUCTIVE (Unfollow sits beside the altitude switch). Horizontal padding
    would convert that dead gutter — where a miss costs a retry — into live
    edge-to-edge borders, where a thumb aiming at Unfollow and landing 20px left
    performs an altitude SWITCH instead. Bigger targets, worse errors.

    So: padding-block only, and the horizontal gutters are untouched. Pinned as
    the ABSENCE of any inline padding, which is what bites if someone later
    "improves" this with a shorthand."""
    block = _coarse_block()
    i = block.index(".fl-alts a")
    rule = block[block.index("{", i) + 1:block.index("}", i)]
    assert "padding-block" in rule
    assert "padding-inline" not in rule
    assert not re.search(r"(?:^|;)\s*padding\s*:", rule), rule
    assert not re.search(r"(?:^|;)\s*margin\s*:", rule), rule
    assert "margin-block" in rule


def test_assert2b_the_acts_row_clears_the_following_rows_h2_link():
    """BORN RED — assert (2), aimed at the adjacency that actually exists.

    The design framed it as "acts-row boxes don't intersect each other", and
    vertical-only growth makes that true by construction (the gutters never
    move). The live vertical adjacency is the one worth arithmetic: on a
    Following row with no delta line, the h2 thread-name LINK sits directly
    above the acts, separated by

      .thread-name margin-bottom + .thread .follow-line margin-top
      + .fl-alts margin-top

    and the acts' 16px inline-block sits ~3px inside .fl-alts's 22px line box,
    so the padded box top is (padding - half-leading) above the content top."""
    block = _coarse_block()
    pad = _shorthand_block(_decl(".fl-alts a, .fl-alts .fl-unfollow",
                                 "padding-block", block))
    neg = _shorthand_block(_decl(".fl-alts a, .fl-alts .fl-unfollow",
                                 "margin-block", block))
    assert neg == -pad

    name_gap = _rem(_decl(".thread-name", "margin").split()[-1])
    line_gap = _shorthand_block(_decl(".thread .follow-line", "margin"))
    alts_gap = _rem(_decl(".fl-alts", "margin-top"))
    clearance = name_gap + line_gap + alts_gap

    acts_font = _rem(_decl(".fl-alts", "font-size"))
    body_lh = float(_decl("body", "line-height"))
    half_leading = (acts_font * body_lh - acts_font * UA_BUTTON_LINE_HEIGHT) / 2
    overhang = pad - half_leading

    assert overhang < clearance, (
        f"padded acts box overlaps the Following row's h2 link by "
        f"{overhang - clearance:.2f}px")


def test_assert2c_the_acts_targets_clear_the_24px_box():
    """BORN RED. 2.5.8 AA by BOX on the acts row — the exception stops being
    load-bearing, which was the audit's actual goal."""
    block = _coarse_block()
    pad = _shorthand_block(_decl(".fl-alts a, .fl-alts .fl-unfollow",
                                 "padding-block", block))
    font = _rem(_decl(".fl-alts", "font-size"))
    box = font * UA_BUTTON_LINE_HEIGHT + 2 * pad
    assert box >= 24.0, f"acts box {box:.1f}px falls below the 2.5.8 AA box"
    # 33.9, not the design's 33.6: the audit rounded the acts link's inline box
    # to 16px where 0.85rem x 1.2 is 16.32. Same treatment, same conclusion, and
    # the arithmetic here is the unrounded one — a test that restated the
    # audit's rounding would be pinning the note rather than the stylesheet.
    assert box == pytest.approx(33.9, abs=0.2), box


# ===========================================================================
# The one-token rider
# ===========================================================================

def test_the_tracked_marker_carries_no_pointer_cursor():
    """BORN RED as a SHARED-RULE assertion, and the honest note travels with it:
    Axel's finding was that `.tracked-marker` inherits `cursor: pointer` from the
    shared rule and so promises a tap that does nothing. In the LANDED state that
    promise was already cascade-suppressed — `.tracked-marker { cursor: default }`
    comes later at equal specificity and won — so this rider is HYGIENE, not a
    live bug fix, and the build report says so. What it buys is structure: the
    declaration is out of the shared rule, so a future reorder or specificity
    bump cannot resurrect a fabricated affordance."""
    shared = _decl_or_none(".tracked-marker, .deck-follow", "cursor")
    assert shared is None, "cursor must not be declared on the shared rule"
    assert _decl(".deck-follow", "cursor") == "pointer"
    assert _decl(".tracked-marker", "cursor") == "default"


def _decl_or_none(selector: str, prop: str):
    try:
        return _decl(selector, prop)
    except AssertionError:
        return None
