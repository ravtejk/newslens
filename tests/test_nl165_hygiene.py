"""NL-165 — the hygiene batch's acceptance tests (three chartered items).

Chartered 2026-08-27 (DECISIONS "[2026-08-27] NewsLens — RESUME + HIS BLANKET
EXECUTED", item 5) off the resume sweep's live find. One file, three sections,
each pinning a defect that was reproduced AT HEAD from the principal's own
artifacts before a line of source moved:

  ① THE MARKDOWN "Here for:" DEDUPE.  The NL-68 dedupe law ("tag+thread twins
    must stay dead on BOTH surfaces") held on the web lane only.  generate.py's
    markdown meta line joined matched_tags + matched_memory RAW, so a tag and a
    tracked thread of the same name doubled the name every edition.  Live
    specimens, transcribed from the real DB read-only and reproduced verbatim
    below: data/briefings/2026-08-26.md slot 3 and 2026-08-24.md slot 2.

  ② THE "goal No." SENTENCE-SPLITTER.  memory_core._sentences split on the
    period inside a numeraled abbreviation, truncating the sentence the cite
    checker then evaluated and inflating the count against the 5-sentence cap.
    Specimens from data/generation_log.jsonl (08-26 run, ts 2026-08-27T02:14:08Z)
    and from thread_state.state_text itself.

  ③ THE DEAD _here_for PINS.  NL-117 rider (c) / gate R-2 chartered retirement
    "to the NEXT HYGIENE BOUNDARY".  This batch is that boundary.  The pins are
    NOT deleted — item ① gives the law a live second door on the markdown lane,
    so they are RE-AIMED at it (see the re-aimed pins in
    test_serverside_batch_20260717.py, test_serverside_batch_qa_20260717.py and
    test_nl134_fresh_profile_fixes.py) and the dead server-side shims they used
    to hold up are retired.  The tooth for the retirement itself is here.

Hermetic and $0: no model call, no network, no real state.  Every fixture slot
is constructed; the two specimen slots are transcriptions of real rows, not
reads of them.
"""

from __future__ import annotations

import pytest

from newslens import generate, memory_core, moat_battery, server
from test_generate import A_DAY, _inputs_for, slot, stories_payload


def _meta_lines(text: str):
    """The markdown lane's per-story colophon lines."""
    return [ln for ln in text.splitlines() if "Here for:" in ln]


def _rendered(slots):
    stories, _ = generate.validate_narrative_payload(
        stories_payload(slots), slots, "A")
    return generate.assemble_narrative(A_DAY, "A", stories, _inputs_for(slots))


# ==========================================================================
# ① the markdown lane joins through the NL-68 dedupe law
# ==========================================================================
def test_i1_md_meta_line_dedupes_tag_and_same_named_thread():
    """BORN RED at 30c689e — the charter's own acceptance shape.  A tag and a
    tracked thread of the same name render the name ONCE on the markdown lane,
    exactly as they already did on the web lane."""
    sl = slot(1, tags=({"name": "Strait of Hormuz", "level": "specific"},),
              mem=("Strait of Hormuz",))
    line = _meta_lines(_rendered([sl]))[0]
    assert line.count("Strait of Hormuz") == 1
    assert "Here for: Strait of Hormuz." in line


def test_i1_md_lane_reproduces_the_0826_specimen_deduped():
    """BORN RED at 30c689e.  The live specimen, transcribed from the real
    briefings row for 2026-08-26 slot 3 (read-only pull; the rendered artifact
    is data/briefings/2026-08-26.md line 43).  HEAD rendered:

        Here for: Middle East Conflict, Strait of Hormuz, US-Iran Tensions
        Escalate with Military Strikes, Iran War, Strait of Hormuz.

    The tail twin is the defect; the other four names are all distinct and must
    survive in their original order, tags first."""
    sl = slot(1,
              tags=({"name": "Middle East Conflict", "level": "broad"},
                    {"name": "Strait of Hormuz", "level": "specific"}),
              mem=("US-Iran Tensions Escalate with Military Strikes",
                   "Iran War", "Strait of Hormuz"))
    line = _meta_lines(_rendered([sl]))[0]
    assert ("Here for: Middle East Conflict, Strait of Hormuz, US-Iran "
            "Tensions Escalate with Military Strikes, Iran War." in line)
    assert line.count("Strait of Hormuz") == 1


def test_i1_md_lane_reproduces_the_0824_specimen_deduped():
    """BORN RED at 30c689e.  The second live specimen — real briefings row for
    2026-08-24 slot 2, rendered at data/briefings/2026-08-24.md line 32.  Here
    the twin is the FIRST tag, so the surviving copy is the leading one: the
    dedupe is first-occurrence-wins, not last."""
    sl = slot(1,
              tags=({"name": "Strait of Hormuz", "level": "specific"},
                    {"name": "Maritime Chokepoints", "level": "broad"},
                    {"name": "Oil Markets", "level": "broad"}),
              mem=("Strait of Hormuz",))
    line = _meta_lines(_rendered([sl]))[0]
    assert ("Here for: Strait of Hormuz, Maritime Chokepoints, "
            "Oil Markets." in line)
    assert line.count("Strait of Hormuz") == 1


def test_i1_md_dedupe_is_case_insensitive_and_first_casing_wins():
    """BORN RED at 30c689e.  The server lane's exact semantics, not a second
    divergent spelling of them: the match is case-insensitive and the casing
    that renders is the one seen FIRST (tags before threads)."""
    sl = slot(1, tags=({"name": "Strait of Hormuz", "level": "specific"},),
              mem=("strait of hormuz",))
    assert "Here for: Strait of Hormuz." in _meta_lines(_rendered([sl]))[0]

    sl2 = slot(1, tags=({"name": "Alpha", "level": "broad"},
                        {"name": "Beta", "level": "broad"}),
               mem=("beta", "Gamma", "ALPHA", "Gamma"))
    assert "Here for: Alpha, Beta, Gamma." in _meta_lines(_rendered([sl2]))[0]


def test_i1_md_lane_drops_empty_and_malformed_entries():
    """BORN RED at 30c689e — and red by CRASH there, not by a wrong string:
    HEAD's markdown lane indexes `t["name"]` unguarded, so a tag dict without a
    name raises KeyError and a non-dict entry raises TypeError, taking the whole
    edition render down.  The web lane has tolerated both since NL-68; sharing
    the law closes the latent crash as a side effect worth naming.

    The malformed entries are assigned AFTER the fixture builds the slot: the
    shared `slot()` helper normalises tags with `dict(t)` and so cannot express
    a malformed one — which is itself the point, and is why this shape reaches
    the renderer only from the ranked web, never from a test fixture."""
    sl = slot(1, mem=("", "Real Thread"))
    sl["matched_tags"] = [{"name": ""}, {"nope": 1}, "not-a-dict",
                          {"name": "Real Tag"}]
    assert "Here for: Real Tag, Real Thread." in _meta_lines(_rendered([sl]))[0]


def test_i1_carried_invariant_md_fallbacks_are_untouched():
    """CARRIED-INVARIANT (born-green).  The two no-match branches are the part
    of this line NOT under change; they are pinned so the extraction cannot
    quietly re-route them.  (The literal strings are shared verbatim with the
    retired server shim — that identity is what let the ③ pins be re-aimed
    without editing a single assertion.)"""
    text = _rendered([slot(1, tags=(), mem=()),
                      slot(2, override=True, tags=(), mem=())])
    lines = _meta_lines(text)
    assert "Here for: world-impact selection (no tag or thread match)." in lines[0]
    assert "Here for: editor's override — see note above." in lines[1]


def test_i1_moat_battery_colophon_parity_survives_the_twin():
    """BORN RED at 30c689e.  moat_battery carried a THIRD literal copy of this
    colophon (_colophon_line), and its docstring claimed it was "built by the
    SAME code path as the sectioned render so parity is structural, not
    remembered" — which was false at HEAD: the sectioned form delegates to
    generate.assemble_narrative, the prose form re-implemented it.

    That matters beyond tidiness.  T1's whole premise is furniture parity —
    "the colophon rides both forms, byte-identical" (Content's law, module
    docstring).  Fixing generate.py alone would have made a twin-name slot
    render deduped in the sectioned arm and doubled in the prose arm, i.e. an
    unblinded furniture difference inside a form comparison."""
    slots = [slot(1, tags=({"name": "Strait of Hormuz", "level": "specific"},),
                  mem=("Strait of Hormuz",))]
    stories, _ = generate.validate_narrative_payload(
        stories_payload(slots), slots, "A")
    inputs = _inputs_for(slots)
    sect = moat_battery.render_sectioned(A_DAY, stories, inputs)
    prose = moat_battery.render_prose_first(A_DAY, stories, inputs)
    assert _meta_lines(sect) == _meta_lines(prose)
    assert _meta_lines(prose)[0].count("Strait of Hormuz") == 1


# ==========================================================================
# ② the sentence splitter survives a numeraled abbreviation
# ==========================================================================
# Transcribed verbatim from thread_state.state_text (read-only pull, 2026-08-27).
# These are the two rows the 08-26 run logged Editor's-eye violations against.
SPECIMEN_ADMIN = ('The administration ranks cheap US gas as "goal No. 1" above '
                  "Iran's nuclear program (Aug 14).")
SPECIMEN_VANCE = ('Vance ranks cheap US gas as the war\'s stated "goal No. 1" '
                  "above Iran's nuclear program, and Bessent prepares "
                  "unprecedented economic-isolation measures (Aug 14).")


@pytest.mark.parametrize("text", [SPECIMEN_ADMIN, SPECIMEN_VANCE])
def test_i2_goal_no_numeral_is_one_sentence(text):
    """BORN RED at 30c689e.  The literal specimens from the generation log.
    HEAD split each at the period in 'No. 1' and returned two fragments, the
    first truncated at `... "goal No.` — the exact string the log printed."""
    assert memory_core._sentences(text) == [text]


def test_i2_month_abbrev_before_a_numeral_is_the_same_class():
    """BORN RED at 30c689e.  The sibling the real corpus actually produces —
    found by running HEAD's splitter over every thread_state row and flagging
    each fragment whose successor starts with a digit.  The whole census is 15
    breaks from exactly three tokens: 'Aug.' x8, 'No.' x4, 'Jul.' x3, and ZERO
    fragments in that corpus legitimately begin with a digit.  Both month
    specimens are transcribed from thread_state.state_text."""
    aug = ("Russia struck Kyiv with ballistic missiles on Aug. 1, killing at "
           "least nine and wounding over 30 (Aug 2).")
    jul = ("Zelenskyy removed Defense Minister Reznikov on Jul. 16, then fired "
           "military chief Syrskyi (Jul 17).")
    assert memory_core._sentences(aug) == [aug]
    assert memory_core._sentences(jul) == [jul]


def test_i2_validate_state_checks_the_whole_sentence_and_counts_it_once():
    """BORN RED at 30c689e — the defect's two logged harms, together.

    HEAD warned `sentence carries no dated edition cite: 'The administration
    ranks cheap US gas as "goal No.'` (the truncated head has no parenthetical)
    AND counted the paragraph at 6 sentences against the 5-cap.  Both are
    artifacts of the split.  Post-fix: five sentences, every one of them cited,
    no warnings at all.

    Severity is deliberately unchanged — these were warn-grade, the outcome was
    'written', and this batch corrects the counting and the checking only."""
    text = " ".join([
        "Iran closed the strait to tanker traffic (Aug 12).",
        "Washington moved a carrier group into the Gulf (Aug 13).",
        SPECIMEN_ADMIN,
        "Brent settled nine percent higher on the week (Aug 14).",
        "Talks remain unscheduled (Aug 14).",
    ])
    ledger = {"2026-08-12", "2026-08-13", "2026-08-14"}
    clean, warnings = memory_core.validate_state(text, ledger)
    assert clean == text
    assert len(memory_core._sentences(text)) == memory_core.STATE_MAX_SENTENCES
    assert warnings == []


def test_i2_carried_invariant_real_terminators_and_the_us_family_survive():
    """CARRIED-INVARIANT (born-green).  The guard against over-fixing: the
    protection is scoped to an abbreviation followed by a NUMERAL, so a genuine
    sentence end still ends a sentence — including a sentence that really does
    end on 'No.' — and the committed U.S./U.N. family keeps splitting exactly
    as it did.  Without this bound the fix would be a sentence-count silencer
    rather than a correction."""
    assert memory_core._sentences("The answer was No. Talks resume (Aug 14).") \
        == ["The answer was No.", "Talks resume (Aug 14)."]
    # "May" is the one month left OUT of the numeraled table on purpose: nobody
    # writes "May. 12" as a date, so protecting it would buy nothing and cost
    # exactly this sentence boundary.  Born-green (HEAD splits it too) — the
    # point is that it must STAY split.
    assert memory_core._sentences("The deal closed in May. 12 firms followed.") \
        == ["The deal closed in May.", "12 firms followed."]
    assert memory_core._sentences("The U.S. and the U.N. met (Aug 14). It held.") \
        == ["The U.S. and the U.N. met (Aug 14).", "It held."]
    assert memory_core._sentences("One (Aug 12). Two (Aug 13). Three (Aug 14).") \
        == ["One (Aug 12).", "Two (Aug 13).", "Three (Aug 14)."]


# ==========================================================================
# ③ the dead _here_for shims are retired
# ==========================================================================
def test_i3_the_dead_server_here_for_shims_are_gone():
    """BORN RED at 30c689e — both names resolve there.

    NL-117 rider (c) / gate R-2: "the ~10 dead-code _here_for pins = retirement
    chartered to the NEXT HYGIENE BOUNDARY".  server._here_for and
    server._selection_names had had NO render site in server.py since the
    NL-117/121 reason line took the last one (truthed in their own docstrings on
    2026-08-24); they survived only to host QA's pins of the NL-68 dedupe law.

    Item ① gives that law a LIVE door — generate.selection_names, which the
    markdown lane (the deep-view-free CLI artifact) and moat_battery's colophon
    both now read — so the pins were re-aimed there rather than deleted, and the shims that
    were holding them up are retired.  This asserts the retirement itself; the
    re-aimed pins assert the law still bites."""
    assert not hasattr(server, "_here_for")
    assert not hasattr(server, "_selection_names")


def test_i3_the_law_has_exactly_one_home():
    """The point of the retirement: ONE dedupe implementation, not three.  Both
    surviving surfaces read the same function object, so a future edit to the
    law cannot land on one lane and miss the other — which is precisely how the
    markdown lane drifted for the six weeks between NL-68 and this batch."""
    assert moat_battery.generate.selection_names is generate.selection_names
    sl = {"matched_tags": [{"name": "Strait of Hormuz"}],
          "matched_memory": ["strait of hormuz"]}
    assert generate.selection_names(sl) == ["Strait of Hormuz"]
