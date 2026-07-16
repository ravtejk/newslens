"""Backlog-minors batch QA (QA-written; extends the implementer's three
pins in test_generate.py / test_p3_script.py). Offline; zero events.

Teeth per dispatch: the dirty-guard (text-loss protection) and 28b (a
fact-subset EXEMPTION change). Dirty-guard behavior pins are structural
source pins per the M7 precedent (the suite runs no browser JS); the
recorded browser-pass item covers live interaction.

HISTORICAL (machinery since DELETED — NL-58 ruling 2 / NL-60: the spoken
caveat is retired from the podcast entirely; no removal block, no stem
matching, no re-append exists in current code):
  BUG19  was: substring stems ate legitimate prose in the 28c removal
         block; fixed with word-boundary stems, then the whole block was
         deleted with the caveat's retirement. The surviving test pins
         that the retired machinery STAYS retired.
  BUG20  was withdrawn during probing (the then-live gating contract);
         its sibling pins were retired with the machinery — dated
         tombstone below.

Gate items: 28b does NOT only narrow — {1..slot_count} narrows 1-slot
days but WIDENS typical 5-slot days (invented "4"/"5" now exempt where
the old {2,3} blanket flagged them; "1" newly exempt everywhere). The
implementer's docstring discloses the principle honestly; the
only-narrows framing in the dispatch record needs correcting. Pinned as
actual below, both directions.
"""

from __future__ import annotations

import json
import re
import types

import pytest

from newslens import db, generate, server, webui
from test_generate import _inputs_for, slot

DATE = "2026-07-07"


# ---------------------------------------------------------------------------
# 1. Dirty-guard — structural pins (M7 precedent: source-level JS pins)
# ---------------------------------------------------------------------------

def test_dirty_guard_snapshots_on_open_and_compares_values():
    """Text-loss protection anatomy: per-field OPENING snapshots (so a
    pre-filled edit-note popup opens CLEAN and dirties only when touched)
    and a value-comparison dirty test. Value-diff is the load-bearing
    choice: a datalist SELECTION fires input events, not keystrokes — a
    keystroke-counting guard would miss it; comparing values catches any
    change however it arrived. (Live selection->scrim-tap behavior is on
    the recorded browser-pass list.)"""
    assert "f.dataset.initialValue = f.value;" in webui.JS
    assert "fields[i].value !== (fields[i].dataset.initialValue || '')" \
        in webui.JS
    guard = webui.JS.split("function popupIsDirty")[1].split("function ")[0]
    assert "addEventListener" not in guard  # value-diff, not event counting


def test_dismiss_path_guards_and_both_dismissal_routes_use_it():
    """dismissPopup: not-open no-op, dirty no-op, else close — and BOTH
    scrim tap and Escape route through it. The old direct
    closePopup(p.id) Escape path (the live-since-M7 silent text-eat) must
    be gone entirely."""
    dismiss = webui.JS.split("function dismissPopup")[1].split("function ")[0]
    assert "if (popupIsDirty(el)) return;" in dismiss
    assert dismiss.index("popupIsDirty") < dismiss.index("closePopup")
    assert "dismissPopup(e.target.id)" in webui.JS      # scrim delegation
    assert "dismissPopup(p.id)" in webui.JS             # Escape parity
    assert "closePopup(p.id)" not in webui.JS           # the old eater is dead


def test_cancel_buttons_keep_an_explicit_ungated_exit():
    """The guard must not trap: every popup keeps an explicit
    closePopup('popup-...') control that bypasses the dirty check —
    Cancel is a deliberate act, the guard is for accidents."""
    # NL-68 item 10 (DECISIONS 2026-07-16): popup-add-story (the free-text follow)
    # is GONE — the story follow is a suggestions-only combobox, not a popup.
    for pid in ("popup-add-topic", "popup-add-writer", "popup-edit-note",
                "popup-delete-confirm"):
        assert f"closePopup('{pid}')" in webui.PAGE + webui.JS


def test_popup_snapshots_cannot_bleed_across_popups():
    """Snapshots live on each field's own dataset and popupIsDirty scopes
    its query to the one popup element — structurally per-popup. (Two
    popups sharing state would need a shared variable; there is none:
    the guard block declares no snapshot globals.)"""
    guard_region = webui.JS.split("function popupIsDirty")[1].split(
        "document.addEventListener")[0]
    assert "el.querySelectorAll" in guard_region or \
        "fields = el.querySelectorAll" in guard_region
    assert "var openSnapshot" not in webui.JS  # no popup-global snapshot state


# ---------------------------------------------------------------------------
# 2. Vocabulary endpoints
# ---------------------------------------------------------------------------

def _cfg(sources=(), broad=(), granular=()):
    src = [types.SimpleNamespace(name=n, followed_analyst=f)
           for n, f in sources]
    return types.SimpleNamespace(
        sources=src,
        followed_analyst_sources=[s for s in src if s.followed_analyst],
        interests_broad=list(broad), interests_granular=list(granular))


def test_topic_vocabulary_dedupes_sorts_and_survives_malformed_rows(tmp_paths):
    db.migrate()
    con = db.connect()
    try:
        tag_slot = json.dumps([{"slot": "1", "story_title": "t", "summary": "s",
                                "item_ids": [], "matched_tags":
                                [{"name": "Ukraine"}, {"name": "ai policy"}]}])
        with con:
            for d in ("2026-07-01", "2026-07-02", "2026-07-03"):
                con.execute("INSERT INTO briefings (date, story_slots)"
                            " VALUES (?, ?)", (d, tag_slot))
        # (a malformed story_slots row is uninsertable on fresh DBs —
        # migration 0008-era json_valid CHECK — so the endpoint's
        # ValueError tolerance is legacy-defensive only; not fixturable)
        vocab = server._topic_vocabulary(
            con, _cfg(broad=["AI policy", "Ukraine"], granular=["OPEC"]))
        assert vocab.count("Ukraine") == 1          # deduped across briefings
        assert vocab == sorted(vocab, key=str.lower)  # stable ordering
        assert "OPEC" in vocab
        # case-variant duplication pinned as actual (set is case-sensitive):
        # curated "AI policy" and matched "ai policy" both survive — cosmetic,
        # noted for the gate; they sort adjacently.
        assert "AI policy" in vocab and "ai policy" in vocab
    finally:
        con.close()


def test_suggest_component_escapes_hostile_tag_names(tmp_paths):
    """NL-11: tag names arrive from the ranked web; a hostile name must not
    break out of the suggestion component's <script> JSON payload."""
    db.migrate()
    con = db.connect()
    try:
        hostile = 'x"><script>alert(1)</script>'
        with con:
            con.execute(
                "INSERT INTO briefings (date, story_slots) VALUES (?, ?)",
                ("2026-07-01", json.dumps([{"slot": "1",
                                            "matched_tags": [{"name": hostile}]}])))
        sugg = server._topic_suggestions(con, _cfg())
        assert any(o["v"] == hostile for o in sugg)   # recalled faithfully...
        html = server._render_suggest("topic", "topic-suggest", "p", "a", sugg)
        payload = html.split('class="suggest-data">')[1].split("</script>")[0]
        assert "<" not in payload and ">" not in payload   # ...no raw angle brackets
        assert "\\u003c" in payload                        # the hostile '<' is encoded
        assert "<script>alert(1)</script>" not in html
    finally:
        con.close()


def test_writer_suggestions_recall_shapes_and_exclude_followed():
    """NL-11: writer suggestions carry the outlet as a secondary line, split
    "Pub (Name)" to name/outlet, and EXCLUDE already-followed analysts."""
    cfg = _cfg(sources=[
        ("Politico (Jack Blanchard)", False),   # writer-shaped, not followed -> rich entry
        ("The Hill", False),                    # plain feed -> excluded
        ("Solo Analyst Feed", True),            # already followed -> excluded (NL-11)
        ("A (B) (C)", False),                   # greedy edge, pinned actual
    ])
    sugg = server._writer_suggestions(cfg)
    by_label = {o["l"]: o for o in sugg}
    assert by_label["Jack Blanchard"]["s"] == "Politico"   # outlet secondary line
    assert by_label["Jack Blanchard"]["v"] == "Jack Blanchard"
    assert "The Hill" not in by_label                      # plain feed excluded
    assert "Solo Analyst Feed" not in by_label             # already followed excluded
    # greedy regex takes the LAST parenthetical as the name — actual, cosmetic
    assert by_label["C"]["s"] == "A (B)"
    labels = [o["l"] for o in sugg]
    assert labels == sorted(labels, key=str.lower)


# ---------------------------------------------------------------------------
# 3. 28b — the exemption's direction, both ways (gate correction pinned)
# ---------------------------------------------------------------------------

def _script_with(extra, n_pad=40, caveat=True):
    mid = (generate.SPOKEN_CAVEAT + " ") if caveat else ""
    return ("One story today. It's Tuesday, July 7. " + extra + " "
            + mid + generate.SIGNOFF + " " + "pad " * n_pad)


def test_28b_beyond_count_numerals_still_flag_on_a_five_slot_day():
    slots = [slot(i) for i in range(1, 6)]
    _, _, warns = generate.validate_script(
        _script_with("There are 7 invented reasons."), "One story today.",
        _inputs_for(slots))
    assert any("'7'" in w for w in warns)


def test_28b_widening_on_typical_days_pinned_as_actual_for_the_gate():
    """GATE CORRECTION (pinned as actual): the dispatch record says the
    new bound 'only ever narrows'. It narrows 1-slot days (their pin) but
    WIDENS the typical 5-slot day: an invented '4 billion' figure whose
    '4' is nowhere in the narrative now passes as enumeration furniture —
    the old {2,3} blanket flagged '4'. Same for '5', and '1' is newly
    exempt on every day. The furniture-vs-figure ambiguity is inherent
    (\"story four\" vs \"$4 billion\" both normalize to '4'); the
    implementer's docstring discloses the principle honestly. Whether the
    trade is right is the gate's call — this freezes what shipped."""
    slots = [slot(i) for i in range(1, 6)]
    _, _, warns = generate.validate_script(
        _script_with("They pledged 4 billion in aid and 5 more ships."),
        "One story today.", _inputs_for(slots))
    assert not any("'4'" in w or "'5'" in w for w in warns)  # exempt (actual)
    _, _, warns1 = generate.validate_script(
        _script_with("The 1 thing to watch."), "One story today.",
        _inputs_for([slot(1)]))
    assert not any("'1'" in w for w in warns1)               # '1' exempt (actual)


# ---------------------------------------------------------------------------
# 4. 28c — stem-removal boundaries, BUG19, BUG20
# ---------------------------------------------------------------------------

def test_BUG19_substring_stem_hits_must_not_eat_legitimate_prose():
    """GREEN — was KNOWN-RED (BUG19), resolved by deletion (NL-58 ruling 2, NL-60): the 28c removal block is gone; this now pins that the retired machinery stays retired — legitimate prose is never eaten and no PARAPHRASE disclosure fires. Original finding. All three 'stem' hits here are substring
    artifacts — country->count, wired->wire, outsourced->source — yet the
    sentence is removed from the persisted script. The removal block must
    match stems on word boundaries; this legitimate outro sentence
    survives and no PARAPHRASE disclosure fires."""
    legit = ("Across the country, the wired testimony was outsourced "
             "overnight for a second review.")
    body, _, warns = generate.validate_script(
        _script_with(legit, caveat=False), "One story today. " + legit,
        _inputs_for([slot(1)]))
    assert legit in body
    assert not any("PARAPHRASE removed" in w for w in warns)


# RETIRED 2026-07-13 (NL-60) — three 28c pins deleted as vacuous. They pinned the
# caveat-removal machinery (the remove-then-reappend churn, its stem-threshold
# boundaries, and the `if SPOKEN_CAVEAT not in low` gate), which NL-58 ruling 2
# (DECISIONS 2026-07-10) deleted entirely: the spoken caveat is out of the podcast,
# nothing appends it, so nothing removes it. With the machinery gone the three
# assertions passed trivially. The surviving live contract — "a generated script
# never carries the spoken caveat" — stays pinned by
# test_28c_paraphrase_left_untouched_now_that_caveat_is_removed (below,
# `assert generate.SPOKEN_CAVEAT not in body`) and, prompt-side, by
# test_nl58_batch.test_script_prompt_does_not_request_the_spoken_caveat.
#   was: test_verbatim_caveat_gates_the_removal_block_off
#   was: test_28c_never_doubled_invariant_holds_even_while_BUG20_stands
#   was: test_28c_two_real_stems_survive_and_short_three_stem_survives


def test_28c_paraphrase_left_untouched_now_that_caveat_is_removed():
    """NL-58 ruling 2 (DECISIONS 2026-07-10): the spoken caveat is OUT of the
    podcast, so the NOTES 28c paraphrase-removal — which existed only to stop a
    model paraphrase from doubling the verbatim append — is retired. A model
    paraphrase is now left in place and no verbatim caveat is inserted. (Was
    test_28c_genuine_paraphrase_removed_with_the_quote_in_the_disclosure —
    flipped.)"""
    paraphrase = ("Keep in mind that outlet and source counts are pickup "
                  "measures across the wire, never a guarantee of truth.")
    body, _, warns = generate.validate_script(
        _script_with(paraphrase, caveat=False), "One story today.",
        _inputs_for([slot(1)]))
    assert paraphrase in body                     # model text untouched
    assert generate.SPOKEN_CAVEAT not in body     # never appended
    assert not any("PARAPHRASE removed" in w for w in warns)
