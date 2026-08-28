"""NL-138 — THE RANKING MODEL'S PROSE REASON DIES EVERYWHERE.

The principal's ruling ④ (DECISIONS 2026-08-02, "SEVEN-ITEM RULING SLATE"),
which withdrew the CoS's keep-the-prose-for-calibration construction: the
day-14 override calibration keys on STRUCTURED selection data (world-impact
score, override fired, matched tags — all persisted per run), never on the
paragraph. So the paragraph leaves GENERATION (the rank prompt stops asking),
THE LEDGER (slot field + ranking_runs meta), and every READER SURFACE.

His accuracy finding, verbatim, is the charter's evidence — the Iran reason
"implies the user had global oil or middle east stability or energy prices or
international shipping as one of their topics, which they didn't. It doesn't
seem accurate or worth surfacing like that."  The model was scoring WORLD
importance and the sentence read as a claim about USER relevance; a selection
reason written in the wrong register is misinformation about the product's own
behaviour, and that is what these pins guard against coming back.

WHAT REPLACES IT: the code-owned tag form NL-134 F3 already ships on the front
page — labels.WHY_CHOSEN_BECAUSE + labels.WHY_WORLD_NEWS ("Chosen because:
Important World News"). Every override surface composes from those constants,
so the pins below assert the CONSTANTS, never a re-typed string: a re-pin of
the copy must not be able to leave a test asserting the old words.

BORN-RED CLASS (ENGINEERING.md:122): every test here except the two labelled
CARRIED-INVARIANT fails at 38141a3. The HEAD-run fail list is in
research/2026-08-02--nl138-build.md.
"""
import json

import pytest

from newslens import generate, labels, paths, ranking


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

TAG_FORM = f"{labels.WHY_CHOSEN_BECAUSE} {labels.WHY_WORLD_NEWS}"

# The dead prefix, quoted ONCE, here, so the absence assertions below have
# something to look for. It is a literal on purpose: the constant it names is
# deleted, and a test that imported it could not run.
DEAD_PREFIX = ("This story doesn't match your tagged interests, but we "
               "included it because ")


def item(i, outlet, wire=0):
    return {"id": i, "outlet": outlet, "url": f"https://{outlet}.example/{i}",
            "title": f"Story {i}", "published_at": "2026-08-02",
            "fetched_at": "2026-08-02T00:00:00Z", "source_type": "rss",
            "wire_syndication_flag": wire}


def cluster(ids, title="Story", tags=(), impact=5, **extra):
    c = {"story_title": title, "summary": "Summary.", "item_ids": list(ids),
         "matched_tags": [dict(t) for t in tags], "matched_memory": [],
         "world_impact": impact}
    c.update(extra)
    return c


def override_slot_dict(**over):
    """A persisted story_slots row for a fired override, as the READER
    surfaces receive it (a plain dict out of JSON, not a RankedSlot)."""
    d = {"slot": 1, "story_title": "Zero-match shock", "summary": "s",
         "item_ids": [1], "outlets": ["Reuters"], "matched_tags": [],
         "matched_memory": [], "followed_analyst": False, "personal_score": 0.0,
         "world_impact": 9, "combined_score": 0.4, "override": True,
         "corroboration_count": 1, "corroboration_label": "Reported by Reuters",
         "wire_items_excluded": 0, "matched_dormant": [], "revived_threads": []}
    d.update(over)
    return d


# ---------------------------------------------------------------------------
# 1. GENERATION — the rank prompt stops asking
# ---------------------------------------------------------------------------

def test_the_rank_prompt_no_longer_asks_for_a_prose_reason():
    """Surface 1 of the ruling. Prompts are code (ENGINEERING.md): the field
    leaving the response schema is a diff a reviewer reads, and this is the
    test that makes the diff mandatory rather than optional.

    BORN RED at 38141a3: the template names world_impact_reason twice — once
    in the JSON shape, once as rule 5."""
    text = (paths.PROMPTS_DIR / "rank_select.txt").read_text(encoding="utf-8")
    assert "world_impact_reason" not in text, (
        "the rank prompt still asks the model for a prose reason — the field "
        "is gone from the validator and the slot, so this asks a paid seat to "
        "write tokens nothing will ever read")
    # Still asks for the structured half — the pin must not pass by deleting
    # the scoring contract along with the prose.
    assert "world_impact" in text and "matched_tags" in text


# ---------------------------------------------------------------------------
# 2. VALIDATION — not required, and tolerated-and-ignored if volunteered
# ---------------------------------------------------------------------------

def test_a_payload_without_a_prose_reason_validates():
    """BORN RED at 38141a3: raises ValueError 'cluster #1: world_impact_reason
    missing/empty'. Which is the whole point — at HEAD the pipeline REQUIRES
    the sentence the ruling kills, so a prompt that stopped asking for it
    would fail every run."""
    out = ranking.validate_payload(
        {"clusters": [cluster([1])]}, {1}, {}, [])
    assert len(out) == 1
    assert out[0]["world_impact"] == 5


def test_a_model_that_still_emits_the_reason_is_tolerated_and_ignored():
    """The shape chosen, and why (ranking.py states it in place): this
    validator WHITELIST-CONSTRUCTS its output dict from keys it names, and has
    no extra-keys rejection pass anywhere — so ignoring is the file's existing
    idiom, not a new leniency. Rejecting would also turn one stale prompt file
    into a total failure of a paid seat over a field nothing reads.

    BORN RED at 38141a3: HEAD carries the key through into the validated
    cluster, where the slot constructor then persists it."""
    out = ranking.validate_payload(
        {"clusters": [cluster([1], world_impact_reason="A world claim.")]},
        {1}, {}, [])
    assert "world_impact_reason" not in out[0], (
        "the model's prose survived validation: %r" % (out[0],))


# ---------------------------------------------------------------------------
# 3. THE LEDGER — slot field and ranking_runs meta
# ---------------------------------------------------------------------------

def test_the_slot_carries_neither_the_prose_nor_the_prose_label():
    """Ledger death, at the exact address it is written from: `persist` stores
    `json.dumps([s.__dict__ for s in report.slots])`, so a field on this
    dataclass IS a column in story_slots.

    BORN RED at 38141a3: both keys are present on every slot."""
    clusters = [cluster([1], title="Topic",
                        tags=({"name": "AI regulation", "level": "topic"},)),
                cluster([2], title="Zero big", impact=9)]
    items = {1: item(1, "A"), 2: item(2, "B")}
    slots, meta = ranking.select_slots(clusters, items, set())

    fired = [s for s in slots if s.override]
    assert len(fired) == 1, "the override did not fire — pin is not exercising it"
    for s in slots:
        stored = json.loads(json.dumps(s.__dict__))
        assert "world_impact_reason" not in stored
        assert "override_label" not in stored
    # The STRUCTURED record the day-14 calibration reads is untouched.
    assert fired[0].world_impact == 9
    assert fired[0].override is True
    assert meta["override"]["fired"] is True
    assert meta["override"]["top_zero_match_world_impact"] == 9


def test_the_ranking_runs_meta_carries_no_prose_reason():
    """The second ledger address. The ruling's own finding is that the
    calibration keys on structure — so the structured keys are asserted
    PRESENT here, not merely the prose asserted absent. A pin that only
    deleted things could be satisfied by breaking the instrument.

    BORN RED at 38141a3: meta['override']['reason'] carries the sentence."""
    clusters = [cluster([1], title="Zero big", impact=9)]
    _, meta = ranking.select_slots(clusters, {1: item(1, "A")}, set())
    ov = meta["override"]
    assert "reason" not in ov, "the prose reason is still in the ledger: %r" % (ov,)
    for key in ("pool_size", "threshold", "fired",
                "top_zero_match_world_impact", "story", "slot"):
        assert key in ov, f"the calibration lost {key!r}"


# ---------------------------------------------------------------------------
# 4. THE §5.7 FROZEN SURFACE — markdown edition + spoken validator
# ---------------------------------------------------------------------------

def test_the_edition_override_line_is_the_ruled_tag_form():
    """‼ FROZEN-SURFACE pin (contract §5.7), authorised by ruling ④ and cited
    in the diff at generate.OVERRIDE_TEXT_LABEL.

    BORN RED at 38141a3 on BEHAVIOUR: HEAD renders "**Outside your
    interests:** this story matches none of the tags or threads steering your
    selection; it's here because <the model's sentence>"."""
    inputs = {"slots": [override_slot_dict()]}
    stories = [{"headline": "Strait closure", "lede": "L.",
                "why_it_matters": "W.", "watch_for": "F."}]
    md = generate.assemble_narrative("2026-08-02", "A", stories, inputs)

    assert generate.OVERRIDE_TEXT_LABEL in md
    assert labels.WHY_CHOSEN_BECAUSE in md and labels.WHY_WORLD_NEWS in md
    assert "Outside your interests" not in md
    assert DEAD_PREFIX not in md


def test_the_canonical_string_composes_from_the_labels_table():
    """The constant must READ labels.py, not re-type it — one vocabulary, one
    address (the NL-134 F3 design, widened by this batch). A re-pin of the
    copy in labels.py has to move the edition line with it.

    BORN RED at 38141a3: the constant is a {reason} template with none of
    these substrings."""
    assert "{reason}" not in generate.OVERRIDE_TEXT_LABEL
    assert labels.WHY_CHOSEN_BECAUSE in generate.OVERRIDE_TEXT_LABEL
    assert labels.WHY_WORLD_NEWS in generate.OVERRIDE_TEXT_LABEL


def test_the_spoken_disclosure_is_owed_in_the_ruled_vocabulary():
    """‼ FROZEN-SURFACE pin, the spoken half. Two hard checks became one: the
    reason check is UNSATISFIABLE (no prose exists), and the "outside your"
    acknowledgment folds into the tag form, which carries the same fact in the
    words every other surface uses.

    NET DISCLOSURE STRENGTH IS UNCHANGED and this pin is what says so: an
    override the episode airs still HARD-fails without its disclosure.

    BORN RED at 38141a3 on behaviour, in the direction that matters: at HEAD
    the compliant script (the one voicing the ruled form) FAILS, because HEAD
    demands 'outside your' plus four words of a sentence that no longer
    exists."""
    inputs = {"slots": [override_slot_dict()]}
    signoff = generate.SIGNOFF

    compliant = (f"Here's your briefing. This one is here as "
                 f"{labels.WHY_WORLD_NEWS.lower()}, not because it matches "
                 f"anything you follow. {signoff}")
    _, hard, _ = generate.validate_script(compliant, "", inputs)
    assert hard == [], "the compliant script was rejected: %r" % (hard,)

    silent = f"Here's your briefing. A tanker turned around today. {signoff}"
    _, hard2, _ = generate.validate_script(silent, "", inputs)
    assert any("override" in h for h in hard2), (
        "an aired override with NO disclosure passed — the re-scope weakened "
        "the contract instead of moving it: %r" % (hard2,))


def test_the_writers_labels_block_names_the_phrase_the_validator_checks():
    """Instruction and check read the same constant, so they cannot drift into
    a retry loop the writer has no way to satisfy.

    BORN RED at 38141a3: the block emits 'OVERRIDE — reason: <prose>'.

    NL-166 (2026-08-27) SCOPES THIS PIN, and weakens nothing: the validator it
    names is validate_script, so the block that must name its phrase is the
    SCRIPT format's. The assertions are unchanged — only the format the call
    asks for is now explicit. The article format's half (it must NOT name the
    phrase) is pinned in test_nl166_format_split.py."""
    block = generate.build_labels_block({"slots": [override_slot_dict()]},
                                        fmt=generate.FORMAT_SCRIPT)
    assert labels.WHY_WORLD_NEWS.lower() in block.lower()
    assert "reason:" not in block.lower()


def test_the_battery_conformance_render_stays_byte_identical_on_this_line():
    """moat_battery's cheap arm exists to differ from the sectioned render in
    FURNITURE ONLY; the epistemic furniture (class 2) must ride both forms
    verbatim. Reading the same constant is what makes that structural.

    BORN RED at 38141a3: `.format(reason=...)` on a constant with no
    placeholder raises nothing but produces the old prose line; this asserts
    the new one."""
    from newslens import moat_battery
    inputs = {"slots": [override_slot_dict()], "deep_views": {}}
    stories = [{"headline": "H", "lede": "L.", "why_it_matters": "W.",
                "tier": "full"}]
    md = moat_battery.render_prose_first("2026-08-02", stories, inputs)
    assert generate.OVERRIDE_TEXT_LABEL in md
    assert "Outside your interests" not in md


# ---------------------------------------------------------------------------
# 5. READER SURFACES — the deep view
# ---------------------------------------------------------------------------

def test_the_deep_view_renders_no_prose_reason_even_for_an_archived_row():
    """The NL-134 F3 deep-view block is GONE, and the pin drives the hard
    case: an OLD story_slots row that still carries `world_impact_reason` in
    its JSON. Those rows exist on the founder's machine right now, so "we
    stopped writing it" is not sufficient — the render has to stop reading it.

    BORN RED at 38141a3: the row's sentence renders under WHY_FULL_REASON."""
    from newslens import server
    archived = override_slot_dict(
        world_impact_reason="A closure would lift global oil prices.",
        override_label=DEAD_PREFIX + "a closure would lift oil prices.")
    html = server._render_sources_context_view(
        "story-1", "H", {"headline": "H", "lede": "L."}, archived,
        None, "2026-08-02")
    assert labels.WHY_FULL_REASON not in html
    assert "lift global oil prices" not in html
    # The structured provenance this view is FOR still renders. RE-PINNED
    # 2026-08-24 (NL-117, mockup-v13 PASSED): the "Here for: …" sentence in
    # this view became the ruled reason line — same answer, name-led grammar,
    # one spelling across every surface. The property is unchanged: this view
    # still states, in code-owned words, why the story is here.
    assert labels.WHY_WORLD_NEWS in html


def test_the_front_page_reason_line_is_untouched_by_this_batch():
    """CARRIED-INVARIANT (born GREEN at 38141a3 — labelled per
    ENGINEERING.md:122, verified green on the HEAD-mirror run rather than
    assumed). The front page's provenance line is the surface this whole batch
    re-points everything else ONTO; a regression here would make the ruling's
    replacement vocabulary disappear while every deletion pin still passed.

    RE-PINNED 2026-08-24: flag ② (NAME-LED) retired the STEM on this surface
    only. The markdown/§5.7 override vocabulary this batch is actually about —
    TAG_FORM, generate.OVERRIDE_TEXT_LABEL, the spoken disclosure — is
    untouched, and every other test in this file still pins it."""
    from newslens import server
    assert server._reason_line_text(override_slot_dict()) == \
        labels.WHY_WORLD_NEWS
    matched = override_slot_dict(
        override=False, matched_tags=[{"name": "AI regulation",
                                       "level": "topic"}])
    assert server._reason_line_text(matched) == \
        f"AI regulation — {labels.WHY_TOPIC_ONE}"


# ---------------------------------------------------------------------------
# 6. GENERATION, part 2 — the writer prompt
# ---------------------------------------------------------------------------

def test_the_writer_is_never_seeded_with_the_retired_prose():
    """"Generation itself" is the ruling's third surface, and the rank prompt
    is only half of it: the writer used to receive the same sentence as
    "ranking's significance seed" for the Why-it-matters movement (ADR-0007
    item 9, superseded by this batch). Driven with an ARCHIVED row that still
    carries the field, because that is the only way the line could still fire.

    BORN RED at 38141a3: the prompt contains "ranking's significance seed"."""
    archived = override_slot_dict(
        world_impact_reason="A closure would lift global oil prices.")
    inputs = {"slots": [archived], "date": "2026-08-02", "items_by_slot": {},
              "threads": [], "prior_ctx": {}, "continuity_status": "none",
              "window_meta": {}, "corroboration": {}}
    prompt = generate.build_narrative_prompt("2026-08-02", "A", inputs)
    assert "significance seed" not in prompt
    assert "lift global oil prices" not in prompt
    # The structured inputs the writer legitimately needs are still there.
    assert "matched tags" in prompt


def test_the_cold_start_override_arm_survives_intact():
    """CARRIED-INVARIANT (born GREEN at 38141a3 — labelled, HEAD-mirror
    verified). NL-134 F2(a): on a FIRST briefing the writer is told there is
    no reading history to contrast the story against, because two specimens
    wrote a reader-history claim into a first edition. That arm carries no
    prose reason and this batch must not have disturbed it — it is the other
    half of the same accuracy class (claims about a reader we cannot make)."""
    slot = override_slot_dict()
    cold = generate.build_narrative_prompt(
        "2026-08-02", "A",
        {"slots": [slot], "date": "2026-08-02", "items_by_slot": {},
         "threads": [], "prior_ctx": {}, "continuity_status": "none",
         "window_meta": {}, "corroboration": {}})
    assert "READER'S FIRST BRIEFING" in cold
    assert "Make NO claim about what the reader normally reads" in cold

    warm = generate.build_narrative_prompt(
        "2026-08-02", "A",
        {"slots": [slot], "date": "2026-08-02", "items_by_slot": {},
         "threads": [], "prior_ctx": {}, "continuity_status": "continuous",
         "window_meta": {}, "corroboration": {}})
    assert "READER'S FIRST BRIEFING" not in warm
    # NL-166 (2026-08-27) re-points the ESTABLISHED-ARM DISCRIMINATOR only:
    # that arm no longer licenses an in-prose acknowledgment ("no supplied
    # phrasing to copy" described the license), it forbids one. What this pin
    # asserts — a warm reader takes the established arm, and NL-138's batch
    # left both arms carrying no prose reason — is unchanged.
    assert "ALREADY MADE" in warm
    assert "reason" not in warm.split("OVERRIDE STORY —")[1].split("\n")[0]


# ---------------------------------------------------------------------------
# 7. THE SWEEP — nothing left holding the dead vocabulary
# ---------------------------------------------------------------------------

def test_no_symbol_survives_that_could_re_compose_the_dead_label():
    """The consumer sweep, pinned. Both names are deleted rather than kept as
    retired markers (ranking.py says why in place): a dangling
    OVERRIDE_LABEL_PREFIX is an invitation to re-assemble the exact sentence
    the ruling retired.

    BORN RED at 38141a3: all four symbols exist."""
    assert not hasattr(ranking, "OVERRIDE_LABEL_PREFIX")
    assert not hasattr(generate, "_override_reason")
    field_names = {f.name for f in
                   __import__("dataclasses").fields(ranking.RankedSlot)}
    assert "world_impact_reason" not in field_names
    assert "override_label" not in field_names
