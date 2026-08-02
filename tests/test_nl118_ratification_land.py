"""NL-118 ratification land — the presence pins for clauses (i) and (ii).

The principal approved ruling (i)-(v) verbatim on 2026-08-01. This batch lands
two things and nothing else:

  (i)  `WORD_BUDGETS["medium"]` 450 -> 542, ratified as THROUGHPUT. The ceiling
       (650 = 542 x 1.2) and the disclosure band (731 = 542 x 1.35) DERIVE --
       both factors are untouched, because re-basing by moving a factor would
       be a different and unratified change.
  (ii) The 427-char ANTI-RESTATEMENT FOLD rule enters `prompts/analysis_brief.txt`
       immediately after the LENGTH block's restatement anchor.

BOTH PINS RIDE THE RENDER PATH, NOT THE FILE BYTES. That is the whole point of
them. A pin that reads `prompts/analysis_brief.txt` off disk proves the file
changed; it does not prove the analyst is ever handed the change. These drive
`analyze_story` with an injected `chat` seam and assert against the prompt
STRING THAT WOULD HAVE GONE TO THE MODEL -- so a template the renderer never
loads, a placeholder that silently fails to substitute, or a budget that never
reaches the mapping all fail here.

The rule pin compares WHITESPACE-NORMALIZED, so re-wrapping the insert to the
template's ~78-char prose convention cannot fake presence and cannot break the
pin either. It asserts EXACTLY ONE occurrence: a double-insert is as much a
defect as a missing one (the analyst reading the same binding rule twice is a
prompt bug, and a `str.replace`-class patch applied twice is exactly how it
would happen).

Zero model calls, zero network, zero metered anything.
"""

from __future__ import annotations

import re

import pytest

from newslens import analysis, config

from test_nl118_writer_pipeline import _brief, _fetch, _seed, _slot


# The rule, verbatim and byte-exact as the principal ratified it (427 chars,
# 73 words). This literal is the test's own copy ON PURPOSE -- it is not read
# from the prompt file, because a pin that sources its expectation from the
# artifact it guards proves nothing.
ANTI_RESTATEMENT_FOLD = (
    "ANTI-RESTATEMENT FOLD (effects, binding). An effect states a CONSEQUENCE, "
    "not a re-wording. If an effect repeats the actor and the action, number, "
    "or quoted words of a pinned fact, ledger claim, mechanism clause, or an "
    "earlier effect, DROP it — do not repair it by adding words. A short "
    "effects list is the honest state. Where a real consequence overlaps "
    "something already written, write only the part that is new, and cite it."
)


def _norm(s: str) -> str:
    """Collapse every whitespace run to one space. The insert is re-wrapped to
    the template's prose width, so line breaks inside the rule are expected and
    meaningless; anything else about the text is not."""
    return re.sub(r"\s+", " ", s).strip()


def _rendered_prompt(tmp_paths, tier: str) -> str:
    """The analyst prompt as `analyze_story` actually renders it for `tier`.

    The `chat` seam captures and returns a valid short brief, so the story
    completes normally and the captured string is the real first-call prompt --
    not a hand-assembled approximation of one."""
    con = _seed(tmp_paths)
    calls = []

    def chat(key, prompt):
        calls.append(prompt)
        return _brief([{"observable": "Whether the party registers",
                        "cites": ["S1"]}]), 0.0, 0.0

    sa = analysis.analyze_story(con, "2026-07-26", 1, _slot(), tier,
                                config.SourcesConfig(), "", "", 10.0, [], [],
                                fetch=_fetch, chat=chat,
                                sonar=lambda *a: ([], 0.0, "ok — 0 results"),
                                sleep=lambda s: None)
    assert sa.outcome == "ok", sa.detail
    assert calls, "the analyst seam was never reached — the pin is inert"
    return calls[0]


def test_L1_the_rendered_medium_prompt_carries_the_ratified_542_budget(tmp_paths):
    """BORN RED (clause (i)). The number the analyst is TOLD, on the render
    path, at the medium tier. 450 was the pre-ratification budget; if it still
    appears in the rendered prompt, the constant did not reach the model."""
    prompt = _rendered_prompt(tmp_paths, "medium")
    assert "Word budget for all prose fields combined: 542 words (tier: medium)" \
        in prompt, "the rendered medium prompt does not carry the 542 budget"
    assert "combined: 450 words" not in prompt, \
        "the rendered prompt still carries the pre-ratification 450 budget"


def test_L2_the_full_tier_budget_is_UNTOUCHED_by_the_ratification(tmp_paths):
    """CARRIED INVARIANT (born green, and labelled as such per the born-red
    law). Clause (i) moves the medium tier ONLY. This pin is what makes the
    'full tier untouched' claim falsifiable rather than asserted -- it fails
    the moment someone re-bases both tiers together the way batch B did."""
    prompt = _rendered_prompt(tmp_paths, "full")
    assert "Word budget for all prose fields combined: 750 words (tier: full)" \
        in prompt
    assert analysis.WORD_BUDGETS["full"] == 750


def test_L3_the_rendered_prompt_carries_the_fold_rule_exactly_once(tmp_paths):
    """BORN RED (clause (ii)). The rule reaches the analyst through the render,
    whitespace-normalized so the template's wrapping can neither fake nor break
    it, and EXACTLY once."""
    prompt = _rendered_prompt(tmp_paths, "medium")
    hits = _norm(prompt).count(_norm(ANTI_RESTATEMENT_FOLD))
    assert hits == 1, (
        f"the ANTI-RESTATEMENT FOLD rule appears {hits} times in the rendered "
        f"medium prompt — expected exactly 1")


def test_L4_the_fold_rule_sits_immediately_after_the_restatement_anchor(tmp_paths):
    """BORN RED (clause (ii), placement). Presence is not placement. The rule
    is an elaboration of the LENGTH block's restatement sentence and is
    worthless attached to some other section, so the anchor's last words and
    the rule's first words must be adjacent in the rendered prose -- nothing
    but whitespace between them.

    The anchor is matched in its WRAPPED form's normalized text: the template
    hard-wraps between 'across' and 'fields', and batch D's lesson (§2) is that
    an anchor quoted in reading form silently no-ops against the real file."""
    prompt = _norm(_rendered_prompt(tmp_paths, "medium"))
    anchor = _norm("Restatement across\nfields is the defect; shortness is not.")
    assert anchor in prompt, "the LENGTH-block anchor is not in the rendered prompt"
    assert anchor + " " + _norm(ANTI_RESTATEMENT_FOLD) in prompt, (
        "the fold rule does not sit immediately after the restatement anchor")


def test_L5_the_rule_is_the_ratified_427_char_text(tmp_paths):
    """BORN RED (clause (ii), byte-exactness of the ratified string).

    The ruling landed a specific 427-char / 73-word text 'unchanged'. This pins
    the size of what this test compares against, so that a later edit to the
    rule cannot quietly ride in under a still-passing presence pin: if someone
    rewords the rule, THIS is the test that says the ratified text moved."""
    assert len(ANTI_RESTATEMENT_FOLD) == 427
    assert len(ANTI_RESTATEMENT_FOLD.split()) == 73
    # ...and that exact text is what the analyst is handed.
    assert _norm(ANTI_RESTATEMENT_FOLD) in _norm(_rendered_prompt(tmp_paths, "medium"))
