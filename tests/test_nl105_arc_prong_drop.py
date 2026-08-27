"""NL-105 — §F.1's token-fraction prong DROPPED, measured on PRODUCTION TEXT.

Principal ruling 2026-08-24 (DECISIONS, "THE SLATE RULED" item 1), on the
content round of 2026-08-14 (HIGH confidence). The arc line is the memory moat's
reader-visible deep-view continuity line (rendered verbatim by
server._deep_arc_line_html); the directed token-fraction prong kept it blank for
12 consecutive editions, so DROP was the pro-arc ruling.

WHY THIS FILE EXISTS — the pin gap it closes. tests/test_arc_line.py and
tests/test_arc_line_qa.py ran 49 green through all 12 of those blank editions.
Every pin in them tests the validator against strings a person invented; not one
tested it against a line the production model actually wrote. A contract can be
fully pinned and still be wrong about the world. So this file's fixture is not
invented at all: tests/fixtures/arc_line_production_corpus.json holds every arc
candidate the record LOGGED — 42 thread-editions, 84 rejected candidates
(attempt-1 and corrected retry) plus the 4 lines that ever actually rendered,
each paired with the two inputs validate_arc_line saw at generation time (the
accepted state text, the ledger's last-covered anchor). Vera's signing condition
on the DROP, and NL-35's first harness piece.

THE ONE STRUCTURAL GAP, named rather than papered over (QA fixloop-1 finding
#8): _author_arc_line carries rejected candidate TEXT into the warn — and thus
into generation_log — only when BOTH attempts fail; its retry-accepted return
hands back an empty warn, so an attempt-1 that was rejected and then rescued by
a passing retry leaves no logged text anywhere. The population here is therefore
exactly "every LOGGED rejected candidate + every line that ever served", not
"everything the model ever wrote". Every 84-based claim below is unaffected —
the gap can only hide candidates the OLD validator rejected, i.e. more evidence
in the same direction, never less.

DISCLOSED, because it cuts against the ruling and belongs in the open: the
2026-08-24 edition generated while this fix loop was running (interactive, on
pre-fix bytes, 2m06s before the first source edit) and BROKE the blank streak —
one of its three eligible threads authored an arc at directed frac 0.375, the
first line to render since 2026-07-23. It is in the corpus. It does not rescue
the prong: all 84 logged rejected candidates were over the bar, and one line
squeaking under 0.40 in five weeks is what a non-separating classifier's tail
looks like. Precise on the other two threads (QA fixloop-1 finding #9 — the
first wording said both "died on it"): Strait of Hormuz died on the prong ALONE,
both candidates, and renders post-fix; Iran War died on the CAP at attempt-1 and
on run+frac at the retry, so it trips the SURVIVING run tooth and stays lawfully
arcless. Post-fix that edition serves 2 of 3, not 3 of 3.

BORN-RED PROVENANCE (699586a, the pre-fix bytes): Section 1's five pins fail
there — the corpus renders 0/42 and every edition is silent. Sections 2-3 are
labelled CARRIED-INVARIANT (born green): they pin what the drop must NOT have
cost, so passing on both sides of the change is exactly their job.

Offline, deterministic, $0 — no DB, no network, no model call: the fixture is
the record, replayed through the shipped validator.
"""

import json

from newslens import memory_core
from conftest import PROTOTYPE_ROOT

_CORPUS = json.loads(
    (PROTOTYPE_ROOT / "tests" / "fixtures"
     / "arc_line_production_corpus.json").read_text(encoding="utf-8"))
_SPECIMENS = _CORPUS["specimens"]
_ACCEPTED = _CORPUS["accepted"]
_N_CANDIDATES = sum(len(s["candidates"]) for s in _SPECIMENS)

# The measured shape of the corpus on the LANDED bytes. Exact numbers, so any
# later change to the validator flips these CONSCIOUSLY instead of drifting:
# a rule that renders fewer real lines has to say so here.
#
# MOVED 2026-08-27 by NL-165 ② — the first conscious flip this block was built
# for, and it moves in the generous direction. That batch fixed a sentence
# splitter that broke on a numeraled abbreviation ("goal No. 1", "on Aug. 1"),
# and _sentences is SHARED: the same false break that inflated a state
# paragraph's sentence count was hard-REJECTING arc candidates that are one
# sentence, on §E's one-sentence rule. Measured HEAD (30c689e) vs fixed over the
# whole corpus — three verdicts moved and no others:
#
#   2026-08-01  Ukraine War            cand 0   SENT -> PASS
#   2026-08-06  Ukraine War            cand 1   SENT -> PASS
#   2026-08-11  Fed rate-cut policy    cand 1   SENT -> F1-RUN
#
# ZERO candidates went PASS -> reject. The third is not a loss: that line was
# always a state-text paste and now says so. The mechanism is CHECK ORDERING,
# not sentence repair: §E's sentence count runs before §F.1 in
# validate_arc_line, so at HEAD the false split killed the line as SENT and the
# run check never executed. arc_overlap_trips reads the WHOLE text via
# _arc_word_seq (_sentences is not in that path) and already trips on HEAD
# bytes (gate probe, 30c689e: overlap=True). And 2026-08-06
# leaves the silent list — a real edition that served no continuity line at all,
# blank for a reason that was never one of the contract's rules.
_EXPECT_CANDIDATES_ACCEPTED = 56        # of 84 (54 before NL-165 ②)
_EXPECT_SPECIMENS_RENDERING = 34        # of 42 thread-editions (32 before)
_EXPECT_SILENT_EDITIONS = ["2026-08-12"]   # was ["2026-08-06", "2026-08-12"]
_EXPECT_RUN_SHARING = 11                # candidates that paste a 6-word run


def _rule_that_killed(text, spec):
    """'PASS', or the tag of the rule that hard-rejected this candidate."""
    try:
        memory_core.validate_arc_line(text, spec["state_text"],
                                      spec["anchor_iso"])
        return "PASS"
    except memory_core.ArcLineRejected as exc:
        reason = str(exc)
        for needle, tag in (("anchor", "ANCHOR"), ("word cap", "CAP"),
                            ("sentences", "SENT"), ("banned", "BAN"),
                            ("reproduces the state", "F1-RUN")):
            if needle in reason:
                return tag
        return f"UNCLASSIFIED: {reason}"


def _verdicts(spec):
    return [_rule_that_killed(c["text"], spec) for c in spec["candidates"]]


def _renders(spec):
    """True when this thread-edition would have served an arc line: attempt-1
    accepted, or attempt-1 rejected and the corrected retry accepted — the
    retry order _author_arc_line runs."""
    return "PASS" in _verdicts(spec)


def _by_edition():
    out = {}
    for spec in _SPECIMENS:
        out.setdefault(spec["edition"], []).extend(_verdicts(spec))
    return out


def _editions_that_served_an_arc():
    """An edition served a continuity line if any of its candidates is accepted
    now, OR a line from it actually reached a reader at the time. The second
    clause matters: 2026-07-20, 2026-07-23 and 2026-08-24 each served an arc
    while also logging rejected candidates, so counting only the rejected
    population would report those editions as blank when they were not."""
    per_edition = _by_edition()
    served = {e for e, tags in per_edition.items() if "PASS" in tags}
    served |= {line["edition"] for line in _ACCEPTED}
    return sorted(per_edition), served


# ===========================================================================
# 1. THE DROP — born red at 699586a
# ===========================================================================

def test_the_token_fraction_prong_is_gone_from_the_contract():
    """BORN RED. The prong is deleted, not merely re-tuned: the constant is
    gone, and the candidate that proves it is production text, not a fixture —
    a real rejected line that shares NO 6-word run with its state and scored
    0.6296 directed overlap (the 2026-07-20 US-Iran attempt-1, one of the two
    exhibits quoted to the principal at the ruling). Under the shipped 0.40 bar
    it was rejected; it is lawful now."""
    assert not hasattr(memory_core, "ARC_OVERLAP_TOKEN_FRAC")
    spec = next(s for s in _SPECIMENS if s["edition"] == "2026-07-20"
                and s["topic"].startswith("US-Iran"))
    cand = next(c for c in spec["candidates"] if c["attempt"] == "attempt-1")
    assert cand["directed_frac"] > 0.40 and not cand["shares_6_word_run"]
    assert _rule_that_killed(cand["text"], spec) == "PASS"


def test_the_production_corpus_renders_again():
    """BORN RED — the headline measurement, and the whole case for the ruling.
    At 699586a: 0 of 84 candidates accepted, 0 of 42 thread-editions rendering,
    every one of them over the 0.40 bar (floor 0.410, median 0.600) against a
    source comment that claimed valid reframes sat near 0.2. With the prong
    dropped the same 84 candidates, unchanged, produce a real arc line on most
    thread-editions."""
    accepted = sum(v == "PASS" for s in _SPECIMENS for v in _verdicts(s))
    rendering = sum(_renders(s) for s in _SPECIMENS)
    assert accepted == _EXPECT_CANDIDATES_ACCEPTED, (
        f"{accepted}/{_N_CANDIDATES} candidates accepted, expected "
        f"{_EXPECT_CANDIDATES_ACCEPTED} — the corpus verdict moved")
    assert rendering == _EXPECT_SPECIMENS_RENDERING, (
        f"{rendering}/{len(_SPECIMENS)} thread-editions render, expected "
        f"{_EXPECT_SPECIMENS_RENDERING}")


def test_the_two_exhibits_the_ruling_rested_on_now_render():
    """BORN RED. The two candidates quoted VERBATIM to the principal as what
    the reader did not get (scout report §3f): both satisfy every other rule —
    correct anchor, under the cap, one sentence, no banned lexicon, no shared
    6-word run — and both died on the fraction prong alone. They are the
    ruling's evidence; they must be the first things that render."""
    wanted = ("Zelensky had dismissed Defense Minister Reznikov",
              "military strikes had moved to civilian infrastructure")
    found = 0
    for spec in _SPECIMENS:
        for cand in spec["candidates"]:
            if any(w in cand["text"] for w in wanted):
                found += 1
                assert not cand["shares_6_word_run"]
                assert _rule_that_killed(cand["text"], spec) == "PASS", (
                    f"a ruling exhibit still rejects: {cand['text']!r}")
    assert found == 2, f"expected both exhibits in the corpus, found {found}"


def test_no_streak_of_editions_goes_silently_arcless():
    """BORN RED — THE STREAK DETECTOR, the tooth this contract never had. An
    edition whose arc-eligible threads ALL come back empty serves a deep view
    with no continuity line anywhere; a RUN of such editions is the failure
    that went 12 deep unnoticed while 49 synthetic pins stayed green.

    Replayed on the pre-fix bytes this measures exactly the streak the record
    reported: 12 consecutive blank editions, 2026-07-24 through 2026-08-14,
    bounded by the arc that served on 2026-07-23 and the one that served on
    2026-08-24. On the landed bytes the longest run is 1.

    Bound: no two consecutive editions may both go blank. (Consecutive means
    consecutive among editions that produced arc candidates — the population
    the record can speak for.)"""
    editions, served = _editions_that_served_an_arc()
    longest = run = 0
    worst = []
    current = []
    for edition in editions:
        if edition in served:
            run, current = 0, []
        else:
            run += 1
            current.append(edition)
            if run > longest:
                longest, worst = run, list(current)
    assert longest <= 1, (
        f"{longest} consecutive editions served no arc line at all — the "
        f"streak class is back: {worst}")


def test_every_silent_edition_is_explained_by_a_surviving_rule():
    """BORN RED — silence is lawful only when a rule with teeth caused it.
    At 699586a this is red: the silence was §F.1's, in every edition.

    ONE edition still serves no arc, and it is not the tripwire's doing:
    2026-08-12 (four candidates over the 35-word cap). The §E cap is the binding
    constraint now, which is why the prompt's arc_line schema line restates it.

    2026-08-06 LEFT this list on 2026-08-27 (NL-165 ②) and the reason is worth
    keeping: its four candidates were two ANCHOR misses, one over the cap, and
    one "multi-sentence" — except that last one was a single sentence reading
    "By Aug. 1, Russia's barrages had killed nine in Kyiv…", and the splitter was
    counting the period in "Aug. 1" as a full stop. That edition was blank
    because of a tokenizer bug, not because of a rule. It renders now. The
    lesson this file was written to teach — that a contract can be fully pinned
    and still be wrong about the world — applied to the pins themselves."""
    per_edition = _by_edition()
    silent = sorted(e for e in per_edition if "PASS" not in per_edition[e])
    assert silent == _EXPECT_SILENT_EDITIONS, (
        f"the set of arcless editions moved: {silent}")
    for edition in silent:
        assert "F1-RUN" not in per_edition[edition], (
            f"{edition} is arcless because of the reuse tripwire — that is the "
            f"class the drop was ruled to end: {per_edition[edition]}")


# ===========================================================================
# 2. WHAT THE DROP MUST NOT HAVE COST — CARRIED-INVARIANT (born green)
# ===========================================================================

def test_every_paste_shaped_candidate_in_production_is_still_rejected():
    """CARRIED-INVARIANT (born green — labelled per the R4 org law). The paste
    defense the ruling kept: 11 of the 84 production candidates share a 6-word
    run with their own state text, and not one of them is accepted, before or
    after. This is the pin that would catch a drop that went too far — if a
    future change lets a real paste through, it fails here on real text rather
    than in the served briefing."""
    checked = 0
    for spec in _SPECIMENS:
        for cand in spec["candidates"]:
            if not cand["shares_6_word_run"]:
                continue
            checked += 1
            assert _rule_that_killed(cand["text"], spec) != "PASS", (
                f"a state-text paste reached the reader: {cand['text']!r}")
    assert checked == _EXPECT_RUN_SHARING, (
        f"the run-sharing population moved: {checked}")


def test_every_arc_line_that_ever_rendered_still_validates():
    """CARRIED-INVARIANT — the positive control, and the regression tooth with
    the longest reach. Four arc lines have ever reached a reader (2026-07-20 x2,
    2026-07-23, 2026-08-24). They are the only production evidence of what the
    contract ACCEPTS, and any future tightening that would have suppressed one
    of them has to fail here first, on the real served text.

    Their directed overlap fractions — 0.300, 0.382, 0.375, 0.375 — are also
    the measurement that condemns the dropped prong most plainly: the entire
    pass region it allowed in five weeks of production was this sliver under
    0.40, while the 84 lines it rejected started at 0.410."""
    assert len(_ACCEPTED) == 4
    for line in _ACCEPTED:
        clean, _warns = memory_core.validate_arc_line(
            line["arc_line"], line["state_text"], line["anchor_iso"])
        assert clean == line["arc_line"], (
            f"a line that actually served no longer validates "
            f"({line['edition']}): {line['arc_line']!r}")
        assert not line["shares_6_word_run"]


def test_the_survivors_still_have_teeth_on_the_corpus():
    """CARRIED-INVARIANT (born green — verified failing nothing at 699586a).
    The five structural rules the ruling kept are not decorative once the prong
    is gone: on real text every rejection still names one of them, and the cap,
    anchor and one-sentence rules each still bite somewhere in the corpus. A
    validator that stopped rejecting anything would sail through the render
    pins above and be worth nothing; this is the other side of that bound.
    (The exact accepted/rejected split is pinned by
    test_the_production_corpus_renders_again — deliberately not restated here,
    so this pin stays true on both sides of the drop.)

    SENT LEFT THE REQUIRED SET on 2026-08-27 (NL-165 ②), and the honest reading
    is that it never belonged there on this corpus. Every SENT rejection the
    record ever logged was the splitter miscounting a numeraled abbreviation —
    all three of them, enumerated at the top of this file. No production
    candidate has ever been rejected for genuinely running to two sentences. The
    §E one-sentence rule still has teeth; they are exercised by the invented
    multi-sentence strings in test_arc_line.py, which is the right place for a
    rule the real model does not break. Requiring it to bite HERE would be
    requiring a bug to stay."""
    tags = [v for s in _SPECIMENS for v in _verdicts(s)]
    rejected = [t for t in tags if t != "PASS"]
    assert set(rejected) <= {"ANCHOR", "CAP", "SENT", "BAN", "F1-RUN"}, (
        f"an unclassified rejection appeared: {set(rejected)}")
    assert {"CAP", "ANCHOR"} <= set(rejected)
    assert "SENT" not in set(rejected), (
        "a sentence-count rejection reappeared on production text — the "
        f"NL-165 splitter fix may have regressed: {set(rejected)}")


# ===========================================================================
# 3. THE FIXTURE ITSELF — CARRIED-INVARIANT (born green)
# ===========================================================================

def test_fixture_measurements_reproduce_on_the_shipped_normalizer():
    """CARRIED-INVARIANT, and a drift detector with a specific origin: the
    superseded §F.1 calibration pin stated a must-catch margin of 0.071 when
    the real value was 0.0444 — 63% off, undetected for a month because the pin
    asserted that a line 'trips', never what it measured. Every candidate here
    carries its directed overlap fraction and run-sharing flag as recorded at
    extraction; both are re-derived from memory_core's own normalizer on every
    run, so a change to _arc_word_seq or ARC_OVERLAP_RUN can no longer move the
    record silently underneath the numbers quoted in the report."""
    pairs = [(s["state_text"], c) for s in _SPECIMENS for c in s["candidates"]]
    pairs += [(a["state_text"], dict(a, text=a["arc_line"])) for a in _ACCEPTED]
    assert len(pairs) == _N_CANDIDATES + len(_ACCEPTED)
    for state_text, cand in pairs:
        state_seq = memory_core._arc_word_seq(state_text)
        seq = memory_core._arc_word_seq(cand["text"])
        frac = len(set(seq) & set(state_seq)) / len(set(seq))
        assert round(frac, 4) == cand["directed_frac"], (
            f"overlap measurement drifted for {cand['text'][:60]!r}: "
            f"{round(frac, 4)} vs recorded {cand['directed_frac']}")
        assert memory_core._shared_contiguous_run(
            seq, state_seq, memory_core.ARC_OVERLAP_RUN
        ) == cand["shares_6_word_run"]


def test_the_fixture_is_the_whole_logged_population_not_a_sample():
    """CARRIED-INVARIANT. A corpus harness is only evidence if nothing was
    selected out of it: the fixture's own header counts must match its
    contents, every specimen must carry the two generation-time inputs, and
    every candidate must be attempt-labelled. The extraction recipe is recorded
    in the fixture's _meta so the population can be re-derived from
    data/generation_log.jsonl + data/newslens.db at any time."""
    meta = _CORPUS["_meta"]
    assert meta["rejected_specimens"] == len(_SPECIMENS) == 42
    assert meta["rejected_candidates"] == _N_CANDIDATES == 84
    assert meta["accepted_lines"] == len(_ACCEPTED) == 4
    assert sorted({s["edition"] for s in _SPECIMENS}) == meta["editions"]
    for spec in _SPECIMENS:
        assert spec["state_text"].strip() and len(spec["anchor_iso"]) == 10
        assert 1 <= len(spec["candidates"]) <= 2
        assert [c["attempt"] for c in spec["candidates"]] in (
            ["attempt-1"], ["attempt-1", "retry"])
        for cand in spec["candidates"]:
            assert cand["text"].strip()
