"""Podcast floor-removal QA extensions (QA-written, 2026-07-14; extends, never
replaces, the flipped contract tests in test_generate.py /
test_script_floor_retry.py / test_script_floor_retry_qa.py).

Contract under test: DECISIONS 2026-07-14 "NewsLens — podcast floor REMOVED"
(principal). NO length contract below the <11-min ceiling; the ONLY lower
check is the flat, coverage-independent SCRIPT_DEGENERATE_WORDS brokenness
backstop — explicitly NOT a length contract. Ceiling 1650 / SCRIPT_MAX_TOKENS
and the informed-retry machinery unchanged.

What this file adds over the flipped pins:

  1. The backstop's exact BOUNDARY (119 aborts / 120 ships / 121 ships),
     sized from the constant so a gate-retuned threshold moves the test with
     it, with the full NOT-a-length-contract error text pinned at the bite.

  2. The non-floor property AT EVERY COVERAGE k: complete ~150-word and
     300-word digests ship un-length-warned on 1-, 2-, 3-, and 7-slot
     editions (7 slots = the k=5 cap) — lengths the OLD contract (600 flat,
     369/501 relaxed) failed or would have warned on at every one of those k.

  3. Coverage-independence AT THE ENFORCEMENT MESSAGE, thin edge: the k=1
     degenerate abort quotes the SAME flat backstop text (byte-exact into the
     informed retry through the real _validate_script) — not a 369-shaped
     relaxation; the retry's prompt is ORIGINAL + one correction block.

  4. Template hygiene at k=1/2/3/7: the built prompt carries ZERO brace
     residue end-to-end (labels/narrative substituted, nothing unformatted
     anywhere, not just before LABEL DATA), the ceiling renders from
     SCRIPT_CEILING_WORDS, "There is no minimum." renders at every k, and no
     floor-shaped number (600/369/501 as words) survives anywhere in it.

  5. The ceiling side untouched, at its boundary: 1650/3000 constants pinned;
     the one-directional overrun warn stays silent AT the k=3 margin (1104)
     and fires just past it (1105) — and the short direction never warns.

Fully offline: fake_model patches generate._chat at the module seam; autouse
sandbox fixtures redirect DATA_DIR/DB_PATH; the loopback guard refuses any
real socket. No source files are modified by this file.
"""

from __future__ import annotations

import re

import pytest

from newslens import generate
from test_generate import (
    A_DAY, ENV, _digest_script, _inputs_for, fake_model, run, seed_briefing,
    slot, stories_payload,
)

PREFIX = generate.RETRY_CORRECTION_PREFIX
SUFFIX = generate.RETRY_CORRECTION_SUFFIX

# Warn substrings that would betray ANY resurrected lower-length judgment
# (the dead viability/severe-short vocabulary plus the flat guard's own),
# and the overrun warn's fingerprint for the never-fill direction.
LOWER_LENGTH_WARNS = ("not viable", "degenerate", "broken", "severely short",
                      "shortfall")


def _no_lower_length_warns(rep):
    return not any(any(t in w for t in LOWER_LENGTH_WARNS)
                   for w in rep.warnings)


# =========================================================================
# 1 — the backstop boundary, sized from the constant
# =========================================================================

def test_backstop_boundary_bites_below_ships_at_and_above(migrated_con,
                                                          fake_model):
    """floor-1 words aborts with the full NOT-a-length-contract text after
    one informed retry; exactly floor and floor+1 SHIP clean. Sized from
    SCRIPT_DEGENERATE_WORDS so a gate-retuned threshold moves this pin with
    it (the ruling: exact value is an implementation call)."""
    floor = generate.SCRIPT_DEGENERATE_WORDS
    slots = [slot(1), slot(2), slot(3)]
    seed_briefing(migrated_con, A_DAY, slots)
    fake_model.narrative = stories_payload(slots)

    fake_model.script = _digest_script(slots, floor - 1)
    with pytest.raises(generate.GenerateError) as excinfo:
        run(migrated_con, date=A_DAY)
    msg = str(excinfo.value)
    assert f"script degenerate: {floor - 1} words" in msg
    assert f"below the {floor}-word brokenness backstop" in msg
    assert "NOT a length contract" in msg
    assert "it is a stub, not a short episode" in msg
    # both attempts consumed: one informed retry, then the visible failure
    assert len([c for c in fake_model.calls if not c["json_mode"]]) == 2

    for exactly in (floor, floor + 1):
        fake_model.script = _digest_script(slots, exactly)
        rep = run(migrated_con, date=A_DAY)
        assert rep.script_words == exactly
        assert _no_lower_length_warns(rep), (exactly, rep.warnings)


# =========================================================================
# 2 — the non-floor property at every coverage k
# =========================================================================

@pytest.mark.parametrize("n_slots,total", [
    (1, 150), (1, 300),      # OLD: k=1 floor 369 — both ABORTED
    (2, 150), (2, 300),      # OLD: k=2 floor 501 — both ABORTED
    (3, 150), (3, 300),      # OLD: k=3 floor 600 — both ABORTED
    (7, 150), (7, 300),      # OLD: k=5 floor 600 — both ABORTED
])
def test_complete_short_digests_ship_unwarned_at_every_k(migrated_con,
                                                         fake_model,
                                                         n_slots, total):
    """The backstop is not a floor in disguise: complete, disclosure-clean
    digests at ~150 and 300 words ship at EVERY coverage k with no
    lower-length warn and no overrun warn — every one of these eight shapes
    aborted under the retired {369/501/600} table. The 7-slot edition
    exercises the k=5 coverage cap (script voices the covered five only)."""
    slots = [slot(i) for i in range(1, n_slots + 1)]
    seed_briefing(migrated_con, A_DAY, slots)
    fake_model.narrative = stories_payload(slots)
    covered = sorted(generate.script_covered_slots(_inputs_for(slots)))
    fake_model.script = _digest_script([s for s in slots
                                        if s["slot"] in covered], total)
    rep = run(migrated_con, date=A_DAY)
    assert rep.script_words == total
    assert _no_lower_length_warns(rep), (n_slots, total, rep.warnings)
    assert not any("over the ~" in w for w in rep.warnings)


# =========================================================================
# 3 — coverage-independent enforcement text + informed retry at the thin edge
# =========================================================================

def test_k1_degenerate_retry_quotes_the_flat_backstop_text_byte_exact(
        migrated_con, fake_model):
    """The k=1 abort speaks the SAME flat backstop as k=5 — no 369-shaped
    relaxation survives in the enforcement message — and the informed retry
    (through the REAL _validate_script; fake_model fakes only the _chat
    transport) POSTs byte-exactly ORIGINAL PROMPT + one correction block
    quoting that text. The old shape said '369-word floor for a 1-story
    digest'; this pin goes red if that message shape returns (a silent
    CONDITION-only floor return is the ship pins' job — parametrized above)."""
    floor = generate.SCRIPT_DEGENERATE_WORDS
    slots = [slot(1)]
    seed_briefing(migrated_con, A_DAY, slots)
    fake_model.narrative = stories_payload(slots)
    fake_model.script = _digest_script(slots, 60)
    with pytest.raises(generate.GenerateError):
        run(migrated_con, date=A_DAY)
    s_calls = [c for c in fake_model.calls if not c["json_mode"]]
    assert len(s_calls) == 2
    expected_err = (
        f"script degenerate: 60 words — below the {floor}-word brokenness "
        "backstop (NOT a length contract; disclosures and truncation "
        "checked and clear — this output cannot contain intro + lead + "
        "outro, it is a stub, not a short episode)"
    )
    assert s_calls[1]["prompt"] == (
        s_calls[0]["prompt"] + "\n\n" + PREFIX + expected_err + SUFFIX)
    assert "369" not in s_calls[1]["prompt"]
    assert "floor for a 1-story digest" not in s_calls[1]["prompt"]


# =========================================================================
# 4 — template hygiene across k: zero residue, constants render, no floor numbers
# =========================================================================

@pytest.mark.parametrize("n_slots", [1, 2, 3, 7])
def test_built_prompt_no_residue_ceiling_from_constant_no_floor_numbers(
        n_slots):
    """With a brace-free narrative the WHOLE built prompt is brace-free (every
    placeholder — labels_block and narrative_text included — substituted; a
    stray '{' anywhere means residue). The ceiling strings derive from
    SCRIPT_CEILING_WORDS, 'There is no minimum.' renders at every k, and no
    floor-shaped number (600 / 369 / 501 as standalone words) appears."""
    built = generate.build_script_prompt(
        A_DAY, "A", "A brace-free narrative body.",
        _inputs_for([slot(i) for i in range(1, n_slots + 1)]))
    assert "{" not in built and "}" not in built
    norm = " ".join(built.split())
    ceiling = generate.SCRIPT_CEILING_WORDS
    assert f"(~{ceiling} words)" in norm
    assert f"up to ~{round(ceiling / 150)} minutes" in norm
    assert "a ceiling, not a target" in norm
    assert "There is no minimum." in norm
    for n in (600, 369, 501):
        assert not re.search(rf"\b{n}\b", built), (n_slots, n)
    assert "minimum" not in norm.replace("There is no minimum.", "").lower()


# =========================================================================
# 5 — ceiling side untouched, at its own boundary
# =========================================================================

def test_ceiling_constants_and_overrun_warn_boundary(migrated_con, fake_model):
    """Hammer on the unchanged upper side: the constants stand (1650 words /
    3000 tokens), and the ONE warned direction has its exact margin — a k=3
    digest at int(960*1.15)=1104 words ships silent, 1105 draws exactly one
    overrun warn naming the guide, the 1650 hard ceiling, and tighten-never-
    fill. No lower-direction warn exists at either size."""
    assert generate.SCRIPT_CEILING_WORDS == 1650
    assert generate.SCRIPT_MAX_TOKENS == 3000
    slots = [slot(1), slot(2), slot(3)]
    guide = generate._script_budgets(3)[0]
    assert guide == 960
    margin = int(guide * 1.15)
    seed_briefing(migrated_con, A_DAY, slots)
    fake_model.narrative = stories_payload(slots)

    fake_model.script = _digest_script(slots, margin)
    rep = run(migrated_con, date=A_DAY)
    assert rep.script_words == margin
    assert not any("over the ~" in w for w in rep.warnings)
    assert _no_lower_length_warns(rep)

    fake_model.script = _digest_script(slots, margin + 1)
    rep = run(migrated_con, date=A_DAY)
    over = [w for w in rep.warnings if f"over the ~{guide}-word guide" in w]
    assert len(over) == 1
    assert "tighten, never fill" in over[0]
    assert str(generate.SCRIPT_CEILING_WORDS) in over[0]
    assert _no_lower_length_warns(rep)
