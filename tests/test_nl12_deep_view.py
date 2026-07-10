"""NL-12 deep-view render milestone — implementer liveness/contract pins.

Option A as amended (principal ruling 2026-07-09) + the register spec
(2026-07-09 content addendum). Fully offline: server rendered in-process,
validator called directly, ZERO consumption events.

Each test here is a liveness red for one wired obligation — it fails against
the pre-NL-12 render/validator and only passes with the landed change:
  1. five reader sections; 'The facts' is pinned-only (ledger/unresolved gone)
  2. arc -> a cited, clickable context line in the title block (openEdition)
  3. Sources anchor present; no dead anchors on empty sections
  4. per-fact citation fold-away (<details open>, keyboard, no-JS = expanded)
  5. pinned-fact dedupe + chronological ordering (validator-grade)
  6. analyst-prompt register clause (declarative unknowns; settles never shown)
  7. 'What's still open' one-register editor's-memo prose; settles never renders
"""

from __future__ import annotations

from pathlib import Path

from newslens import analysis, paths, server, webui

from test_m3_qa import m3_brief
from test_analysis_brief_qa import qa_brief, validate

DATE = "2026-07-07"


def _deep(brief, anchor="story-0"):
    return server._render_deep_view(anchor, "H", {"header": {}, "brief": brief},
                                    DATE)


def _section(html, anchor_id):
    return html.split(f'id="{anchor_id}"')[1].split("</div>")[0]


# ---------------------------------------------------------------------------
# 1. Five reader sections — 'The facts' is pinned facts ONLY
# ---------------------------------------------------------------------------

def test_the_facts_section_is_pinned_only_no_ledger_no_unresolved():
    """Principal ruling: the reader's 'The facts' shows pinned facts only; the
    Ledger and the Unresolved/discrepancy register are removed from the reader
    view (data stays in brief_json / the writer view, both untouched)."""
    brief = m3_brief(with_discrepancy=True)
    html = _deep(brief)
    facts = _section(html, "story-0-facts")
    assert '<p class="deep-section-label">The facts</p>' in facts
    assert "A cited fact." in facts                     # the pinned fact leads
    assert "Pinned facts" not in html                   # old label retired
    # ledger + unresolved register gone from the reader render entirely
    for gone in ('id="story-0-ledger"', "The ledger", "A ledger claim.",
                 'class="deep-discrepancy"', "unresolved", "Meeting Wednesday"):
        assert gone not in html
    # but the brief object itself still carries the ledger data (writer-side)
    assert brief["ledger"] and any(e.get("discrepancy") for e in brief["ledger"])


# ---------------------------------------------------------------------------
# 2. Arc -> cited, clickable context line in the title block
# ---------------------------------------------------------------------------

def test_arc_is_a_cited_clickable_title_block_line_not_a_section():
    brief = m3_brief(with_arc=True)
    brief["arc"]["cites"] = ["P1"]
    brief["sources"].append(
        {"key": "P1", "kind": "prior-briefing", "outlet": "NewsLens",
         "title": "prior", "url": "", "retrieved_at": "2026-07-05"})
    html = _deep(brief)
    tb = html.split('deep-title-block')[1].split("</div>")[0]
    assert 'class="deep-arc-line"' in tb
    assert "Advances the thread" in tb
    # clickable -> navigates to that briefing via NL-11's openEdition mechanism
    assert "openEdition('2026-07-05', event)" in tb
    assert 'href="/?date=2026-07-05"' in tb             # no-JS graceful fallback
    assert "July 5" in tb                               # the last edition's date
    # arc is no longer a section
    assert 'id="story-0-arc"' not in html
    assert '<p class="deep-section-label">Arc</p>' not in html


def test_arc_line_without_a_prior_edition_cite_carries_no_link():
    """arc citing only same-day sources (no prior-briefing P#) still renders
    the continuity line, but with no navigable date — never a dead link."""
    brief = m3_brief(with_arc=True)                     # arc cites ["S1"]
    html = _deep(brief)
    tb = html.split('deep-title-block')[1].split("</div>")[0]
    assert 'class="deep-arc-line"' in tb and "Advances the thread" in tb
    assert "deep-arc-link" not in tb and "openEdition(" not in html


# ---------------------------------------------------------------------------
# 4. Per-fact citation fold-away
# ---------------------------------------------------------------------------

def test_fact_citations_fold_away_open_by_default_keyboard_no_chips():
    brief = m3_brief()
    html = _deep(brief)
    facts = _section(html, "story-0-facts")
    # <details open> => a no-JS reader sees the citation expanded (more info)
    assert '<details class="cite-fold" open>' in facts
    # keyboard-native summary + a quiet typographic marker (caret), not a chip
    assert "<summary" in facts and 'aria-label="Show sources for this fact"' in facts
    assert 'class="caret"' in facts
    assert "chip" not in facts and "pill" not in facts
    # the revealed body carries the outlet names + count
    assert 'class="cite-fold-body">(The Hill · 1 outlet)' in facts
    # JS collapses on load AND re-collapses injected archive editions
    assert "function collapseCiteFolds" in webui.JS
    assert "collapseCiteFolds(document)" in webui.JS
    assert "collapseCiteFolds(mount)" in webui.JS


# ---------------------------------------------------------------------------
# 5. Pinned-fact dedupe + chronological ordering (validator-grade)
# ---------------------------------------------------------------------------

def test_validator_collapses_near_duplicate_pinned_facts_merging_cites():
    b = qa_brief()
    b["pinned_facts"] = [
        {"fact": "OPEC increases output by 188000 barrels per day.",
         "cites": ["S1"]},
        {"fact": "Prices slipped after the decision.", "cites": ["C2"]},
        {"fact": "OPEC increases output by 188000 barrels per day.",  # identical
         "cites": ["R1"]},
    ]
    clean, warnings = validate(b)
    facts = clean["pinned_facts"]
    assert len(facts) == 2                              # the duplicate collapsed
    survivor = next(f for f in facts if "OPEC increases" in f["fact"])
    assert set(survivor["cites"]) == {"S1", "R1"}       # cites merged, none lost
    assert any("near-duplicate" in w for w in warnings)  # disclosed


def test_validator_orders_dated_facts_chronologically_undated_stable():
    b = qa_brief()
    b["pinned_facts"] = [
        {"fact": "A ministerial meeting is set for August 2, 2026.",
         "cites": ["S1"]},                              # later date, slot 0
        {"fact": "Analysts remain divided on the outlook.", "cites": ["C2"]},
        {"fact": "The initial cut took effect on May 1, 2026.",
         "cites": ["R1"]},                              # earlier date, slot 2
    ]
    facts = [f["fact"] for f in validate(b)[0]["pinned_facts"]]
    # dated facts fill the dated slots oldest-first; the undated one stays put
    assert "May 1, 2026" in facts[0]
    assert "divided on the outlook" in facts[1]
    assert "August 2, 2026" in facts[2]


def test_validator_leaves_undated_and_weekday_only_facts_in_place():
    b = qa_brief()                                      # facts carry no absolute
    before = [f["fact"] for f in b["pinned_facts"]]     # dates (only 'Tuesday')
    after = [f["fact"] for f in validate(b)[0]["pinned_facts"]]
    assert after == before                              # order untouched


# ---------------------------------------------------------------------------
# 6. Analyst-prompt register clause (the one authorized prompt touch)
# ---------------------------------------------------------------------------

def test_analyst_prompt_carries_the_register_clause():
    template = (paths.PROMPTS_DIR / "analysis_brief.txt").read_text(
        encoding="utf-8")
    # declarative-default unknowns; survey register banned
    assert "DECLARATIVE BY DEFAULT" in template
    assert "AT MOST ONE per brief" in template
    assert "Survey register" in template and "BANNED specimen" in template
    assert "tangible changes in Y" in template          # the banned form
    # would_resolve is the test, not the outcome
    assert "the TEST, never the outcome" in template
    # settles stays a join key, never shown to the reader
    assert "The reader never sees `settles`." in template
    # the churn-trap bar held: the mechanism/voice rules are untouched
    assert 'Abstract nouns ("tensions", "dynamics"' in template


# ---------------------------------------------------------------------------
# 7. 'What's still open' — one register, editor's-memo prose
# ---------------------------------------------------------------------------

def test_whats_still_open_is_one_register_no_beats_no_settles():
    brief = m3_brief()
    brief["unknowns"] = [{
        "question": "The ministry has not said which members opposed",
        "why_material": "unanimity turns on the holdouts",
        "would_resolve": "the communique text, due Thursday"}]
    brief["watch"] = [{
        "observable": "Defense budget announcements by the end of 2026.",
        "settles": "whether the pledge holds"}]
    html = _deep(brief)
    sec = _section(html, "story-0-open")
    assert "What’s still open" in sec
    # de-labeled: no beats, no field labels, no settles meta-tail
    for banned in ("unknown-q", "unknown-beat", "why it matters:",
                   "what would resolve it:", "(settles:", "Honest unknowns",
                   "whether the pledge holds"):
        assert banned not in sec
    # three sentence-roles present as prose
    assert "which members opposed" in sec
    assert "unanimity turns on the holdouts" in sec
    assert "What would settle it — the communique text, due Thursday" in sec
    # watch observable renders as a closing paragraph, settles dropped
    assert "Defense budget announcements by the end of 2026." in sec
    # the unknown LEADS the watch (ordering law)
    assert sec.index("which members opposed") < sec.index("Defense budget")


def test_whats_still_open_absent_halves_leave_no_residue():
    # unknowns present, no watch -> no closing watch paragraph
    b = m3_brief(); b["watch"] = []
    sec = _section(_deep(b), "story-0-open")
    assert "Which members resist" in sec                # the unknown stands
    assert "communique by Thursday" not in sec          # (was the watch obs)

    # no unknowns, watch present -> watch paragraph alone, no unknowns opener
    b2 = m3_brief(); b2["unknowns"] = []
    html2 = _deep(b2)
    assert 'id="story-0-open"' in html2
    sec2 = _section(html2, "story-0-open")
    assert "communique by Thursday" in sec2
    assert "What would settle it" not in sec2

    # neither -> no section, no anchor
    b3 = m3_brief(); b3["unknowns"] = []; b3["watch"] = []
    html3 = _deep(b3)
    assert 'id="story-0-open"' not in html3
    assert "What’s still open" not in html3
