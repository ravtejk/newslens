"""NL-142 QA — the findings, as acceptance contracts.

NL-142 closes the ingest residue at the RENDER door. This file is QA's pass over
that claim. Two pins are RED against the NL-142 build and carry their fix
contract; three are pins the build's own handoff asked for or that characterise
an undisclosed delta, and are labelled CARRIED-INVARIANT / NEW-PIN born green.

THE SCOPE THE BOUND ACTUALLY HAS (QA enumeration, receipts in
research/2026-08-13--nl142-qa.md). The ingest-owned title/outlet reach a model
through FIVE renderers, not one:

  1. analysis.render_source_map          — CLAMPED by NL-142.
  2. analysis._material_header           — title clamped (it is clamped into the
     map DICT); OUTLET NOT CLAMPED. Same prompt, same key, other string. F-1.
  3. generate.render_writer_view         — writer prompt: outlet and full URL
     unclamped (the persisted row keeps them whole).
  4. generate.py's cluster/source item    — writer prompt: the raw 500-char
     lines                                 stored title and the raw outlet.
  5. ranking.render_items_block          — ranker prompt: same two raw fields.

3-5 sit outside `analysis.brief_bound_chars`, which is analyst-scoped by
definition, so they are outside NL-142's charter — but the residue comment says
"All four are closed at the RENDER door", which reads wider than what landed.
That is a comment fix, not a code fix, and it is NOT pinned here: a test a
comment can satisfy is not a pin (ENGINEERING.md).
"""
import types

import pytest

from newslens import analysis, db, discovery, memory, ranking


HOSTILE_OUTLET = "O" * 2_000
HOSTILE_HOST = "h" * 300


def _hostile_c_key():
    """One cluster item whose outlet and host are far past every clamp."""
    return analysis.build_source_map([], [{
        "url": "https://%s.example/a" % HOSTILE_HOST, "outlet": HOSTILE_OUTLET,
        "title": "T" * 400, "raw_excerpt": "body " * 200,
        "fetched_at": "2026-08-02T00:00Z", "published_at": "2026-08-02"}], [], [])


def _map_outlet(rendered, key):
    line = next(l for l in rendered.splitlines() if l.startswith("[%s]" % key))
    return line[len(key) + 3:].split(" — ")[0]


def _material_outlet(rendered, key):
    line = next(l for l in rendered.splitlines() if l.startswith("--- [%s]" % key))
    return line[len(key) + 7:].split(" — ")[0]


# --------------------------------------------------------------------------
# F-1 — the render-door close is incomplete INSIDE the analyst prompt
# --------------------------------------------------------------------------

def test_the_material_header_shows_the_same_outlet_the_source_map_shows():
    """F-1, RED against the NL-142 build.

    `render_source_map` clamps the outlet label to MAP_LABEL_BUDGET_CHARS.
    `_material_header` — the OTHER renderer of the same field, into the SAME
    analyst prompt — reads `s["outlet"]` straight from the map dict, which
    NL-142 deliberately leaves whole. So one key carries two different outlet
    strings in one prompt: measured 37 chars in the map line and 2,000 in its
    material header. The map is introduced to the model as the closed citation
    vocabulary; a material header that names a different outlet for the same
    key is a coherence defect in exactly the hostile case this batch exists to
    bound.

    NOT a bound breach — `render_material` self-caps at MATERIAL_BUDGET_CHARS
    and QA swept 2,197 (title x host x outlet) combinations without finding one
    over 78,588. This is about what the model is told, not about the size.

    FIX CONTRACT — and the trap in it. Fix at the RENDER door, in
    `_material_header`, by reusing `clamp_map_labels`' outlet share (or the
    same budget) on S/C keys. DO NOT clamp `s["outlet"]` in `build_source_map`:
    `compute_provenance` builds its corroboration set out of the raw
    `sources[c]["outlet"]` values, so a dict-level clamp could merge two
    outlets and DEFLATE a corroboration count — the precise failure NL-142
    keeps `_outlet_id` unclamped to avoid, arriving through the other door."""
    sources = _hostile_c_key()
    map_outlet = _map_outlet(analysis.render_source_map(sources), "C1")
    mat_outlet = _material_outlet(analysis.render_material(sources), "C1")
    assert map_outlet == mat_outlet, (
        "one key, two outlets in one prompt: the source map renders %d chars "
        "and the material header renders %d — the render-door close covers "
        "render_source_map only" % (len(map_outlet), len(mat_outlet)))


def test_a_hostile_outlet_cannot_shed_the_material_the_clamped_case_receives():
    """F-1b, RED against the NL-142 build — the behavioural cost of F-1, and
    the reason the batch's identity claim needs a scope line.

    `test_hostile_and_at_the_clamps_measure_the_SAME_prompt` is true and is a
    LENGTH identity. Underneath it the two prompts are not the same prompt:
    `render_material` pays for the unclamped header out of
    MATERIAL_BUDGET_CHARS and pops keys until the block fits, so the hostile
    map delivers HALF the source material at the same byte count. Measured:
    44 material keys at the clamps, 22 hostile. "Past the clamps, more input
    buys the prompt nothing" holds for the byte count and fails for the
    content — at 1,000,000/50,000/50,000 the worst prompt is 78,492, SHORTER
    than the 78,588 at the clamps, on 23 keys.

    FIX CONTRACT: the same one-line fix as F-1 — once the header outlet is
    clamped, header overhead stops depending on remote input and the two cases
    render the same keys. If the org would rather keep the shedding, this pin
    should be replaced by one that ASSERTS the shed count, so the degradation
    is a pinned decision rather than a side effect."""
    half = analysis.MAP_LABEL_BUDGET_CHARS // 2
    at_clamps = _material_keys(title=analysis.MAP_TITLE_MAX_CHARS, host=half,
                               outlet=analysis.MAP_LABEL_BUDGET_CHARS - half)
    hostile = _material_keys(title=100_000, host=2_000, outlet=2_000)
    assert len(hostile) == len(at_clamps), (
        "a hostile outlet sheds material the clamped case receives: %d keys "
        "at the clamps, %d hostile — the length identity holds and the "
        "content identity does not" % (len(at_clamps), len(hostile)))


def _material_keys(**kw):
    sources = _worst_map(**kw)
    return sorted({l.split("]")[0][5:] for l in
                   analysis.render_material(sources).splitlines()
                   if l.startswith("--- [")})


def _worst_map(*, title, host, outlet, fetched=True):
    """The NL-133/NL-139/NL-142 worst map, rebuilt here so this file does not
    depend on another test module's private helper."""
    h = ("h" * max(0, host - 4)) + ".com"
    t, o = "T" * title, "O" * outlet
    items = [{"outlet": o, "url": "https://%s/%d" % (h, i), "title": t,
              "raw_excerpt": "x" * 8000, "fetched_at": "2026-08-02T00:00Z",
              "published_at": "2026-08-02", "source_name": o, "tier": "full"}
             for i in range(ranking.MAX_CLUSTER_ITEMS)]
    records = ([analysis.FetchRecord(
        url=it["url"], source_name=o, tier="full", outcome=analysis.OK,
        attempted=True, title=t, text="x" * 8000) for it in items]
        if fetched else [])
    sonar = [{"url": "https://%s/s%d" % ("s" * 249 + ".com", i),
              "title": "T" * analysis.SONAR_TITLE_MAX_CHARS,
              "date": "2026-08-02", "snippet": "s" * 8000}
             for i in range(analysis.SONAR_MAX_RESULTS)]
    priors = [{"date": "2026-08-02", "text": "p" * 8000,
               "thread": "M" * memory.TOPIC_MAX_CHARS}
              for _ in range(memory.CONTEXT_CAP)]
    return analysis.build_source_map(records, items, sonar, priors)


# --------------------------------------------------------------------------
# F-3 — the margin delta stated in the shipped comment is off by one
# --------------------------------------------------------------------------

def test_the_margin_delta_that_unbinds_the_cap_pin_is_911(monkeypatch):
    """F-3, NEW PIN born GREEN (labelled — ENGINEERING.md makes the currency
    the claim, not the colour). It freezes the number two shipped artifacts
    misstate.

    `analysis.py`'s new "THE MARGIN IS EXHAUSTED" block and the build record
    both say "at +910 chars the bound passes cap+2 and
    test_raising_the_cap_without_the_margin_breaks_this_test_first goes red".
    Measured here: at +910 the pin still BINDS (bound 79,531 vs cap+2 79,532);
    the first delta that unbinds it is +911. One character, in the comment this
    batch wrote to correct another comment's drift.

    FIX CONTRACT: change +910 to +911 in analysis.py's margin block. No code
    change. This pin then keeps the number honest under any future
    re-derivation of MAX_CLUSTER_ITEMS or the material budget."""
    import os
    import sys
    here = os.path.dirname(os.path.abspath(__file__))
    if here not in sys.path:
        sys.path.insert(0, here)
    from test_nl133_cluster_item_cap import _worst_prompt_chars
    over, _ = _worst_prompt_chars(ranking.MAX_CLUSTER_ITEMS + 2)
    base = analysis.PROMPT_MARGIN_CHARS

    monkeypatch.setattr(analysis, "PROMPT_MARGIN_CHARS", base + 910)
    _, bound_910 = _worst_prompt_chars(ranking.MAX_CLUSTER_ITEMS)
    assert over > bound_910, (
        "+910 was supposed to unbind the cap pin; it still binds (%d > %d)"
        % (over, bound_910))

    monkeypatch.setattr(analysis, "PROMPT_MARGIN_CHARS", base + 911)
    _, bound_911 = _worst_prompt_chars(ranking.MAX_CLUSTER_ITEMS)
    assert not (over > bound_911), (
        "+911 no longer unbinds the cap pin (%d vs %d) — the margin arithmetic "
        "moved; re-derive the comment in analysis.py" % (over, bound_911))


# --------------------------------------------------------------------------
# F-6 — R-E-3a flips a live, reader-visible tier. The end-to-end pin the
#       build's own handoff (item 4) asked for.
# --------------------------------------------------------------------------

def _sonar_transport(results, monkeypatch):
    monkeypatch.setattr(discovery, "call_sonar", lambda key, prompt: {
        "usage": {"total_tokens": 100}, "search_results": results})


@pytest.mark.parametrize("second_url,expected", [
    ("https://b.example/2", False),   # two citable results — slot 3 stays medium
    ("   ", True),                    # one is URL-less — NL-142 drops it, slot 3 demotes
])
def test_the_empty_url_drop_flips_slot_three_from_medium_to_quick(
        tmp_paths, monkeypatch, second_url, expected):
    """F-6, NEW PIN born GREEN — the flip is CORRECT and was UNPINNED.

    R-E-3a is not only a status-line fix. `analyse_slot`'s slot-3 demotion gate
    reads `len(sonar_results) < 2`, so dropping a URL-less result moves a slot
    across that threshold: a slot 3 that used to be written as a MEDIUM brief
    is now DEMOTED TO QUICK when one of its two Sonar results carried no URL.
    That is the honest verdict — the model never received the dropped result,
    because `build_source_map` always skipped it — but it is a reader-visible
    tier change in the founder's edition and the build recorded it as a count
    fix. The build's own QA handoff (item 4) named this pin as the stronger
    one; it is written here rather than asserted.

    Driven through the REAL route: the production `_sonar_verify` with the
    transport stubbed, NOT an injected `sonar` seam — an injected seam replaces
    the very function the drop lives in and would prove nothing."""
    db.migrate()
    con = db.connect()
    try:
        _sonar_transport([
            {"url": "https://a.example/1", "title": "a", "snippet": "s"},
            {"url": second_url, "title": "b", "snippet": "s"}], monkeypatch)
        sa = analysis.analyze_story(
            con, "2026-08-13", 3, {"story_title": "Slot three", "summary": "s",
                                   "item_ids": []},
            tier="medium", cfg=types.SimpleNamespace(sources=[]),
            openai_key="sk-test-not-real", pplx_key="pplx-test-not-real",
            remaining_usd=100.0, memory_lines=[],
            prior=[{"date": "2026-08-12", "text": "prior"}],
            sonar=analysis._sonar_verify,
            chat=lambda key, prompt: ("not json", 0.0),
            sleep=lambda s: None)
        assert (sa.outcome == "demoted-quick") is expected, (
            "slot-3 demotion did not follow the URL-less drop: outcome=%r "
            "status=%r" % (sa.outcome, sa.sonar_status))
    finally:
        con.close()


# --------------------------------------------------------------------------
# F-7 — the render clamp also shortens a PERSISTED record
# --------------------------------------------------------------------------

def test_the_render_clamp_also_shortens_the_persisted_hand_trace_title(migrated_con):
    """F-7, NEW PIN born GREEN — characterising a storage change the build did
    not disclose.

    NL-142 clamps the title INTO the map dict (`build_source_map`), not at the
    render call. `persist_brief` writes that same dict, so
    `analysis_retrieval.title` now stores at most MAP_TITLE_MAX_CHARS where it
    used to store up to ingest.STORED_TITLE_MAX_CHARS. R-E-3b's companion
    change to the URL is argued at length as storage-only; this one changes
    storage without being named.

    Harmless on the founder's corpus — the longest title over 15,359
    source_items is 183 and over 1,618 analysis_retrieval rows is 159, both
    inside 189, so no existing or foreseeable row is shortened — and no
    migration is implied (the clamp is write-time; existing rows are
    untouched). Pinned so the hand-trace record's bound is a stated property
    rather than a side effect of where the clamp was placed.

    FIX CONTRACT: none in code. Name it in the R-E-3 disclosure so the record
    says what `analysis_retrieval` now holds."""
    sources = analysis.build_source_map([], [{
        "url": "https://feed.example/a", "outlet": "Feed", "title": "T" * 400,
        "raw_excerpt": "x", "fetched_at": "2026-08-02T00:00Z",
        "published_at": "2026-08-02"}], [], [])
    analysis.persist_brief(migrated_con, "2026-08-13", 1, "medium", "valid",
                           {"b": 1}, "", 0.0, {"slot": 1}, sources=sources)
    row = migrated_con.execute(
        "SELECT title FROM analysis_retrieval WHERE key = 'C1'").fetchone()
    assert len(row["title"]) == analysis.MAP_TITLE_MAX_CHARS, (
        "the persisted hand-trace title is %d chars; the render clamp is %d"
        % (len(row["title"]), analysis.MAP_TITLE_MAX_CHARS))
