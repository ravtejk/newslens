"""NL-166 — HIS FORMAT LAW AS CODE: the article and the script stop sharing a
prompt block.

HIS RULING, VERBATIM (2026-08-27, DECISIONS "HIS FORMAT LAW (fourth sitting)"):

    "The written article is different from the voice script by design - those
    are consumed in two different formats, dont conflate them."

THE SPECIMEN THAT CHARTERED IT (his words, third sitting): "I dont like the
weird long form way the 'chosen for' is being explained in the briefing, and
how its part of the prose/narrative of the story."  The 08-27 edition carried
the override disclosure TWICE on the text surface — the ruled label above the
headline (generate.OVERRIDE_TEXT_LABEL, his own 08-02 tag form) AND a woven
prose clause in the story's first paragraph.

THE MECHANISM, in three parts (all three are fixed here):
  1. `build_labels_block` emitted a PERFORMATIVE spoken instruction ("say it
     was chosen as important world news… must be spoken") written for the
     audio lane, where prose is the only carrier a disclosure has.
  2. That one block fed BOTH prompts — the script (build_script_prompt) and
     the article's editor pass — so the editor was handed an audio
     instruction and dutifully obeyed it in print.
  3. Independently, the WRITER's own per-story override note LICENSED the lede
     to acknowledge the override in prose ("your lede may acknowledge
     naturally, in your own words"), and both narrative templates carried the
     matching bullet. The article therefore had TWO in-prose routes to a
     disclosure the label already pays.

WHAT THE SPLIT DOES NOT TOUCH: disclosure STRENGTH (the NL-138 standard). An
aired override must still voice labels.WHY_WORLD_NEWS or validate_script hard-
fails it; the markdown label still renders. Only the ARTICLE's prose loses a
duplicate it never owed.

PROOF CLASSES, labelled per test and honest about the difference (gate ruling
2026-08-27: a HEAD red by REFERENCE is not born-red currency — the mutation
receipt is):
  * BORN RED (behaviour) — section 1: these call HEAD's own API with no new
    symbol and fail at 20cc320 on what the code does.
  * REFERENCE RED + MUTATION-PROVEN — sections 2-3: the article format does
    not exist at 20cc320, so these cannot fail at HEAD on behaviour. Their
    currency is the mutation receipt in the build report (the format routing
    reverted to one shared block => these go red).

Offline by construction (conftest autouse sandbox); no model calls.
"""
from __future__ import annotations

import pytest

from newslens import generate, labels, paths


DATE = "2026-08-27"
REVIVED_ON = "2026-08-14"


def slot(n=1, override=False, corroboration_count=2, outlets=("Reuters",),
         tags=(), revived=()):
    """A persisted story_slots row, the shape both prompt builders receive."""
    return {
        "slot": n, "story_title": f"Story {n}", "summary": "S.",
        "item_ids": [n], "outlets": list(outlets),
        "matched_tags": [dict(t) for t in tags], "matched_memory": [],
        "matched_dormant": [], "followed_analyst": False,
        "personal_score": 0.0 if override else 1.0,
        "world_impact": 9 if override else 5,
        "combined_score": 0.4, "override": override,
        "corroboration_count": corroboration_count,
        "corroboration_label": "Reported by Reuters",
        "wire_items_excluded": 0,
        "revived_threads": [dict(r) for r in revived],
        "still_tracking": False, "still_tracking_note": "",
    }


def inputs_for(slots, continuity="ok"):
    prior = {"text_block": "PRIOR EDITION CONTEXT"} if continuity == "ok" else None
    return {"slots": slots, "items_by_slot": {s["slot"]: [] for s in slots},
            "threads": [], "prior_ctx": prior,
            "continuity_status": continuity, "window_meta": None,
            "corroboration": {}}


OVERRIDE = [slot(1, override=True, corroboration_count=1,
                 outlets=("Reuters",),
                 revived=({"topic": "Strait shipping",
                           "last_covered": REVIVED_ON},))]


# ===========================================================================
# 1. THE WRITER'S PROSE LICENSE DIES — BORN RED (behaviour) at 20cc320
# ===========================================================================

def test_the_writer_is_no_longer_licensed_to_voice_the_override_in_prose():
    """BORN RED (behaviour) at 20cc320 — no new symbol is referenced.

    HEAD's established-reader arm reads: "OVERRIDE STORY — outside the
    reader's tags (the pipeline renders its own label; your lede may
    acknowledge naturally, in your own words — no supplied phrasing to copy)".
    That license is the article's OWN route to the prose disclosure he
    objected to — the label above the headline already pays it once, and
    NL-134's just-once spec forbids the second.

    NOTE FOR THE RECORD: this REVERSES the posture NL-134 F2(a) left in place
    ("acknowledging off-interest inclusion stays legal on a non-first
    edition"). It is reversed on his own later and more specific words, and it
    is disclosed as this batch's one scope call."""
    prompt = generate.build_narrative_prompt(DATE, "A", inputs_for(OVERRIDE))
    assert "may acknowledge" not in prompt, (
        "the writer is still licensed to voice the override in the lede — the "
        "article keeps its own route to the doubled disclosure")
    assert "do NOT acknowledge" in prompt, (
        "the established-reader override note must now FORBID the prose "
        "acknowledgment, not merely stop inviting it")


@pytest.mark.parametrize("variant", ["A", "B"])
def test_both_writer_templates_drop_the_prose_acknowledgment_bullet(variant):
    """BORN RED (behaviour) at 20cc320 — the templates are read off disk.

    The per-story note and the template bullet must not contradict each other:
    HEAD's bullet ("your lede may acknowledge that in YOUR OWN WORDS, once")
    would license exactly what the note now forbids."""
    name = generate.PROMPT_A if variant == "A" else generate.PROMPT_B
    text = (paths.PROMPTS_DIR / name).read_text(encoding="utf-8")
    assert "may acknowledge that in YOUR OWN WORDS" not in text, (
        f"variant {variant} still licenses the in-prose override "
        "acknowledgment")
    assert "THE PIPELINE'S LABEL CARRIES IT" in text, (
        f"variant {variant} must say WHERE the disclosure is paid instead")


def test_carried_invariant_the_cold_start_ban_survives_the_split():
    """CARRIED-INVARIANT (born-green). NL-134 F2(a)'s first-briefing arm
    already BANNED every reader-history claim; the split must not disturb it —
    it is now the same law both arms serve, for two different reasons."""
    prompt = generate.build_narrative_prompt(
        DATE, "A", inputs_for(OVERRIDE, continuity="none"))
    assert "THIS IS THE READER'S FIRST BRIEFING" in prompt
    assert "Make NO claim about what the reader normally reads" in prompt


def test_carried_invariant_the_override_note_stays_override_scoped():
    """CARRIED-INVARIANT (born-green) — negative-space scope guard, carried
    from NL-134: neither arm may leak onto a matched story."""
    prompt = generate.build_narrative_prompt(DATE, "A", inputs_for([slot(1)]))
    assert "OVERRIDE STORY" not in prompt


# ===========================================================================
# 2. THE TWO FORMAT-TYPED BLOCKS — reference-red, MUTATION-PROVEN
# ===========================================================================

def _script_block(slots=None):
    return generate.build_labels_block(
        inputs_for(slots or OVERRIDE), fmt=generate.FORMAT_SCRIPT)


def _article_block(slots=None):
    return generate.build_labels_block(
        inputs_for(slots or OVERRIDE), fmt=generate.FORMAT_ARTICLE)


def test_the_script_format_keeps_the_spoken_instruction_verbatim():
    """The audio half of "net disclosure strength unchanged": the say-it
    instruction and the phrase validate_script checks are untouched on the
    lane that owns them."""
    block = _script_block()
    assert "story 1: OVERRIDE" in block
    assert "say it was chosen as" in block
    assert labels.WHY_WORLD_NEWS.lower() in block.lower()
    assert "must be spoken" in block


def test_the_article_format_never_carries_the_spoken_instruction():
    """THE ACCEPTANCE PIN. His law at its narrowest: a performative
    speak-this instruction never reaches the prompt that writes print."""
    block = _article_block()
    low = block.lower()
    assert "must be spoken" not in block
    assert "say it was chosen" not in low
    assert "the rest of the sentence is yours" not in low


def test_the_article_format_supplies_no_phrase_to_copy():
    """NL-134's example-becomes-the-template lesson, applied to the new
    instruction: the article block must not hand the editor the very words the
    label carries, or "do not restate" becomes a script to restate FROM."""
    block = _article_block()
    assert labels.WHY_WORLD_NEWS.lower() not in block.lower(), (
        "the article block hands the editor the label's own vocabulary")
    assert generate.OVERRIDE_TEXT_LABEL not in block


def test_the_article_format_orders_the_editor_not_to_restate():
    """The other half of the split: silence alone would leave the editor free
    to keep a writer's stray acknowledgment. It is told the disclosure is
    already made, and told to cut a restatement it finds."""
    block = _article_block()
    assert "story 1: OVERRIDE" in block
    assert "do NOT restate" in block
    assert "above this story's headline" in block


def test_the_revival_date_survives_on_both_formats_and_only_the_verb_splits():
    """THE REGRESSION GUARD, and the reason this line is not simply
    audio-only: "say the date" is performative (audio), but the DATE itself is
    a fact the article owes HARD — validate_narrative_payload raises when the
    lede's first two sentences lose it. Strip the whole line from the article
    format and the editor no longer knows the date is protected."""
    script, article = _script_block(), _article_block()
    assert f"REVIVAL — say the date: last covered {REVIVED_ON}" in script
    assert f"REVIVAL — last covered {REVIVED_ON}" in article
    assert "say the date" not in article


def test_shared_facts_ride_both_formats_unchanged():
    """Facts both formats need stay shared — the split is about REGISTER and
    performative instructions, not about starving the article of data."""
    script, article = _script_block(), _article_block()
    for block in (script, article):
        assert "story 1: SINGLE-SOURCE — outlet: Reuters" in block
        assert block.splitlines()[-1] == "corrections flagged upstream: none this run"


def test_the_ear_register_is_audio_only():
    """"corroboration for the ear" is audio register handed to print at HEAD —
    the same conflation class as the say-it line, one line down. The
    corroboration FACT is shared; the ear is not."""
    script, article = _script_block(), _article_block()
    assert "corroboration for the ear: Reported by Reuters" in script
    assert "for the ear" not in article
    assert "story 1: corroboration: Reported by Reuters" in article


def test_an_unknown_format_is_a_hard_failure_never_a_silent_default():
    """The footgun the old signature was: a new caller that names no format
    must not quietly inherit the other lane's instructions. That silence is
    exactly how the editor got an audio instruction for five editions."""
    with pytest.raises(ValueError) as exc:
        generate.build_labels_block(inputs_for(OVERRIDE), fmt="podcast")
    assert "podcast" in str(exc.value)


def test_the_covered_scoping_is_unchanged_by_the_split():
    """CARRIED-INVARIANT in behaviour, REFERENCE-RED at 20cc320 (it names
    FORMAT_SCRIPT). The digest-coverage contract (principal 2026-07-14) rides
    the script format exactly as before: an override on an uncovered slot is
    the article's disclosure, never the episode's."""
    slots = [slot(i) for i in range(1, 6)] + [slot(6, override=True)]
    inputs = inputs_for(slots)
    covered = generate.script_covered_slots(inputs)
    assert 6 not in covered
    block = generate.build_labels_block(
        inputs, covered=covered, fmt=generate.FORMAT_SCRIPT)
    assert "story 6" not in block


# ===========================================================================
# 3. THE CALL SITES — wiring proof (behaviour, not source text)
# ===========================================================================

def test_the_script_prompt_carries_the_spoken_instruction():
    """WIRING PROOF, script side: the assembled prompt — not the builder — is
    what the model reads."""
    prompt = generate.build_script_prompt(DATE, "A", "The narrative.",
                                          inputs_for(OVERRIDE))
    assert "must be spoken" in prompt
    assert labels.WHY_WORLD_NEWS.lower() in prompt.lower()


def test_the_editor_prompt_carries_the_do_not_restate_instruction():
    """WIRING PROOF, article side — THE PIN THE WHOLE BATCH EXISTS FOR. The
    editor prompt was assembled inline inside the run body at HEAD, which is
    why nothing could pin it; it is now a named builder like its script twin.

    Route the format back to the shared block and this pin goes red on the
    exact defect his 08-27 edition shipped."""
    prompt = generate.build_editor_prompt(
        inputs_for(OVERRIDE), "(nothing pinned)", {"stories": []})
    assert "do NOT restate" in prompt
    assert "must be spoken" not in prompt, (
        "the editor prompt is being handed the audio lane's speak-this "
        "instruction again — this is the 08-27 defect")
    assert labels.WHY_WORLD_NEWS.lower() not in prompt.lower()


def test_the_editor_prompt_still_carries_its_other_blocks():
    """CARRIED-INVARIANT in behaviour, REFERENCE-RED at 20cc320 (the builder
    did not exist): extracting it must not drop a slot the inline .format()
    filled."""
    prompt = generate.build_editor_prompt(
        inputs_for(OVERRIDE), "PINNED-CALLBACK-SENTINEL",
        {"stories": [{"headline": "H"}]})
    assert "PINNED-CALLBACK-SENTINEL" in prompt
    assert '"headline": "H"' in prompt


# ===========================================================================
# 4. DISCLOSURE STRENGTH IS UNCHANGED — the NL-138 standard
# ===========================================================================

def test_an_aired_override_with_no_spoken_disclosure_still_hard_fails():
    """CARRIED-INVARIANT (born-green), and it is the point of the whole batch:
    the split must move WHICH PROMPT gets an instruction, never how strongly
    the product discloses. validate_script's §5.7 check is untouched."""
    inputs = inputs_for(OVERRIDE)
    silent = f"Here's your briefing. A tanker turned around today. {generate.SIGNOFF}"
    _, hard, _ = generate.validate_script(silent, "narrative", inputs)
    assert any("override" in h for h in hard), (
        "an aired override with NO spoken disclosure passed — the split "
        "weakened the contract instead of moving an instruction: %r" % (hard,))

    compliant = (f"Here's your briefing. This one is here as "
                 f"{labels.WHY_WORLD_NEWS.lower()}, not because it matches "
                 f"anything you follow. {generate.SIGNOFF}")
    _, hard2, _ = generate.validate_script(compliant, "narrative", inputs)
    assert hard2 == [], "the compliant script was rejected: %r" % (hard2,)


def test_no_validator_requires_the_override_phrase_in_the_article():
    """CARRIED-INVARIANT (born-green) — the charter asked this be VERIFIED,
    so it is verified by a test and not by a reading. An article whose prose
    never mentions the override validates clean; only the revival DATE is
    hard on the text side."""
    slots = [slot(1, override=True, corroboration_count=1)]
    payload = {"stories": [{
        "tier": "full", "headline": "A headline",
        "lede": "A tanker turned around in the strait today. The owner "
                "confirmed the diversion.",
        "why_label": "Why it matters", "why_it_matters": "Effects.",
        "watch_label": "Watch for", "watch_for": "The vote.",
        "my_read": None}]}
    stories, warnings = generate.validate_narrative_payload(payload, slots, "A")
    assert len(stories) == 1
    joined = " ".join(warnings).lower()
    assert labels.WHY_WORLD_NEWS.lower() not in joined
    assert "override" not in joined


def _payload(lede, extra=None):
    story = {"tier": "full", "headline": "A headline", "lede": lede,
             "why_label": "Why it matters", "why_it_matters": "Effects.",
             "watch_label": "Watch for", "watch_for": "The vote.",
             "my_read": None}
    story.update(extra or {})
    return {"stories": [story]}


# His 08-27 specimen's operative clause, trimmed to the shape the check sees.
SPECIMEN = ("A tanker turned around in the strait today — carried here as "
            "important world news though it matches none of your usual "
            "threads.")


def test_a_restated_override_disclosure_warns_and_never_kills_the_run():
    """NEW OBSERVABILITY SURFACE (NL-166), born with the test only it can
    flip. WARN-GRADE by design: the writer is forbidden and the editor is told
    to cut, but a model's obedience is only knowable on his next override
    edition — so the run reports the echo instead of dying on it. A hard
    reject would be new severity, which is a checkpoint, not an implementer's
    call."""
    slots = [slot(1, override=True, corroboration_count=1)]
    stories, warnings = generate.validate_narrative_payload(
        _payload(SPECIMEN), slots, "A")
    assert len(stories) == 1, "the check must never block the run"
    joined = " ".join(warnings)
    assert "restated in the article's prose" in joined
    assert labels.WHY_WORLD_NEWS in joined


def test_the_anti_restatement_check_is_override_scoped():
    """Scope guard: a matched (non-override) story that happens to use the
    words owes nothing — there is no label above it to double."""
    slots = [slot(1, override=False, corroboration_count=1)]
    _, warnings = generate.validate_narrative_payload(
        _payload(SPECIMEN), slots, "A")
    assert not any("restated in the article's prose" in w for w in warnings)


def test_the_markdown_label_still_renders_above_the_override_story():
    """CARRIED-INVARIANT (born-green): his ruled 08-02 tag form is the
    article's ONE disclosure now, so the pin that it renders matters more
    after the split than before."""
    slots = [slot(1, override=True, corroboration_count=1)]
    stories = [{"tier": "full", "headline": "A headline",
                "lede": "The lede.", "why_label": "Why it matters",
                "why_it_matters": "Effects.", "watch_label": "Watch for",
                "watch_for": "The vote.", "my_read": None}]
    md = generate.assemble_narrative(DATE, "A", stories, inputs_for(slots))
    assert md.count(generate.OVERRIDE_TEXT_LABEL) == 1
