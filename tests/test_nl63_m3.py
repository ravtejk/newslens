"""NL-63 M3 — receipts-forward depth + NL-66(b) — implementer liveness pins.

Three render additions + one contract decision, all offline (server rendered
in-process, validator called directly, ZERO consumption events — the M7/NL-12
pattern). Each test is a liveness red for ONE wired obligation: it fails against
the pre-M3 render/validator and only passes with the landed change. Probes
IMPLEMENTATION markers (section ids, labels, class names, rendered content), not
retro-mock labels (the A' render-probe lesson).

Serves the principal's 2026-07-10 receipts-restoration amendments (Decision B:
restore "The numbers" and the Unresolved register, DEEP-VIEW ONLY, IN NEW FORM)
and NL-66 ruled option (b) ($0 sources-&-context view for In-Brief slots from
existing machinery, honestly labeled).

  1. "The numbers" — verified-specifics from pinned_facts + non-discrepancy
     ledger claims that carry a figure; full statement + attribution; absent
     when a story carries no numeric receipts (D4, absent halves leave no
     residue). Lead + full-picture tiers (the analyst deep view).
  2. The Unresolved register (new form) — the ledger's cross-source
     `discrepancy` entries (the "Unresolved/discrepancy register" the 07-09
     ruling pulled from the reader view; Decision B restores it deep-view-only);
     two attributed sides + the note; distinct from "What's still open".
  3. NL-66(b) — In-Brief (quick-tier) slots get a "Sources & context" deep view
     from what ALREADY exists (summary, source list, matched tags/threads,
     "Here for"), honestly labeled — NOT the analyst tier, $0, no generation.
  4. The M2-gate cites fork — thread_state.cites_json is ledger-resolved-only;
     validate_state is narrowed to match (an edition-date that is not a ledger
     date is the BUG-25 fabrication class — rejected).
"""

from __future__ import annotations

import json

import pytest

from newslens import analysis, db, memory_core, server

from test_m3_qa import m3_brief

DATE = "2026-07-07"


def _deep(brief, anchor="story-0", con=None, slot=None):
    return server._render_deep_view(anchor, "H", {"header": {}, "brief": brief},
                                    DATE, con=con, slot=slot)


def _section(html, anchor_id):
    return html.split(f'id="{anchor_id}"')[1].split("</div>")[0]


def _numeric_brief():
    """A brief whose receipts carry figures: two numeric pinned facts and one
    numeric non-discrepancy ledger claim. Mirrors the live Kyiv-toll shape
    (fixture, NEVER the live DB)."""
    b = m3_brief()
    b["pinned_facts"] = [
        {"fact": "A Russian attack on Kyiv killed at least 11 people.",
         "cites": ["S1"]},
        {"fact": "Ukraine reported 68 missiles and 351 drones overnight.",
         "cites": ["S1"]},
        {"fact": "No casualties were confirmed at the port.", "cites": ["C1"]},
    ]
    b["ledger"] = [
        {"claim": "The attack injured at least 46 people in Kyiv.",
         "cites": ["S1"], "provenance": "cluster-single"},
        {"claim": "Rescue crews reached the site.", "cites": ["S1"],
         "provenance": "cluster-single"},
    ]
    return b


# ===========================================================================
# 1. Verified specifics — NL-29 consolidation slate (DECISIONS 2026-07-14
#    "NL-29 RULED: the consolidation slate", Merge 2 — CoS interpretation,
#    flagged for the principal's veto at NL-68): the numeric-specifics run
#    FOLDS INTO "The facts" as a sub-group. WAS the standalone "The numbers"
#    section (NL-63 M3, Decision B); the numeric-ledger-claim rows survive
#    byte-for-byte, relocated under the facts .deep-section as deep-numbers-list.
# ===========================================================================

def test_numeric_ledger_claims_fold_into_the_facts_subgroup():
    """The numeric LEDGER claim the facts slice didn't previously show surfaces
    INSIDE 'The facts' as the specifics sub-group (deep-numbers-list), its full
    statement carried with the same cite-fold the facts use. No standalone
    'The numbers' section or anchor survives.
    WAS test_the_numbers_renders_numeric_receipts_with_attribution."""
    html = _deep(_numeric_brief())
    assert 'id="story-0-numbers"' not in html            # section retired
    assert ">The numbers<" not in html                   # label retired
    facts = _section(html, "story-0-facts")              # facts incl. the sub-group
    assert 'class="deep-facts-list deep-numbers-list"' in facts
    # the numeric LEDGER claim (reader-invisible until M3), now folded in here
    assert "injured at least 46 people" in facts
    # the numeric pinned facts stay in the facts list above (unchanged)
    assert "killed at least 11 people" in facts
    assert "68 missiles and 351 drones" in facts
    # v8-M1 item 4 (CONSCIOUS FLIP): attribution rides as the same PLAIN
    # end-of-line outlet count as the facts — the ▸ cite-fold DIES; the outlet
    # names live in the Sources drawer. (WAS: a revealed cite-fold body.)
    assert "cite-fold" not in facts
    assert 'class="cite">(' in facts                     # a plain outlet count
    assert "The Hill" not in facts                       # name moved to the Sources drawer


def test_facts_subgroup_excludes_non_numeric_ledger_claims():
    """A ledger claim with no figure is not a 'specific' — it never enters the
    numbers sub-group (the sub-group is the numeric ledger subset). A non-numeric
    PINNED fact still shows in the main facts list, never the sub-group.
    WAS test_the_numbers_excludes_non_numeric_receipts."""
    facts = _section(_deep(_numeric_brief()), "story-0-facts")
    sub = facts.split('deep-numbers-list')[1] if 'deep-numbers-list' in facts else ""
    assert "Rescue crews reached the site." not in sub   # no digit -> excluded
    assert "No casualties were confirmed at the port." in facts   # pinned, main list
    assert "No casualties were confirmed at the port." not in sub


def test_facts_subgroup_absent_when_no_numeric_ledger_claims():
    """m3_brief() carries no numeric ledger claim -> no sub-group, no 'The
    numbers' residue anywhere (D4: absent halves leave no residue).
    WAS test_the_numbers_absent_when_no_numeric_receipts."""
    html = _deep(m3_brief())                             # 'A ledger claim.' — no digit
    assert 'deep-numbers-list' not in html
    assert 'id="story-0-numbers"' not in html
    assert "The numbers" not in html


def test_no_numbers_jumplist_entry_after_the_fold():
    """The fold retires the 'The numbers' jumplist entry entirely (it is no
    longer a section); the facts anchor still leads.
    WAS test_the_numbers_in_jumplist_only_when_present."""
    jl = _deep(_numeric_brief()).split('deep-jumplist')[1].split("</p>")[0]
    assert 'href="#story-0-numbers"' not in jl
    assert 'href="#story-0-facts"' in jl


def test_the_facts_still_render_the_numeric_facts_too():
    """The pinned numeric facts still render in 'The facts' main list (the fold
    ADDS the ledger specifics; it never removes pinned facts). Heading semantics
    (v7-M2): the section label is an <h2>."""
    html = _deep(_numeric_brief())
    facts = _section(html, "story-0-facts")
    assert "killed at least 11 people" in facts          # still in The facts
    assert '<h2 class="deep-section-label">The facts</h2>' in facts


# ===========================================================================
# 2. The discrepancy register — NL-29 consolidation slate (Merge 1): the
#    register FOLDS INTO "What's still open" as a visually distinct attributed
#    sub-group (two not-settled sections become one). WAS the standalone
#    "Unresolved" section (NL-63 M3, Decision B); the attributed rows survive
#    byte-for-byte, relocated under story-0-open as deep-open-discrepancies.
# ===========================================================================

def test_discrepancies_fold_into_open_with_sides_and_note():
    """Each cross-source discrepancy renders both attributed sides + the note,
    now inside 'What's still open' as the deep-open-discrepancies sub-group.
    WAS test_unresolved_register_renders_discrepancy_sides_and_note."""
    html = _deep(m3_brief(with_discrepancy=True))
    assert 'id="story-0-unresolved"' not in html          # section retired
    assert ">Unresolved<" not in html                     # label retired
    open_sec = _section(html, "story-0-open")
    assert 'class="deep-open-discrepancies"' in open_sec
    # both sides of the live slot-2 shape, each with its source
    assert "Meeting July 8" in open_sec and "Meeting Wednesday" in open_sec
    assert "dates differ" in open_sec                     # the note
    assert "rferl.org" in open_sec or "The Hill" in open_sec


def test_discrepancy_subgroup_absent_when_no_discrepancy():
    """A ledger with only plain claims shows no discrepancy sub-group and no
    retired-section residue. WAS test_unresolved_absent_when_no_discrepancy."""
    html = _deep(m3_brief())
    assert 'class="deep-open-discrepancies"' not in html
    assert 'id="story-0-unresolved"' not in html


def test_discrepancies_do_not_leak_into_the_facts():
    """'The facts' stays pinned-only — the discrepancy folds into 'What's still
    open', never facts. WAS
    test_unresolved_is_a_separate_section_not_folded_into_facts_or_open (which
    pinned a SEPARATE section AND 'not in open'; the 07-14 consolidation folds it
    INTO open, so the open half is inverted here)."""
    html = _deep(m3_brief(with_discrepancy=True))
    facts = _section(html, "story-0-facts")
    assert "Meeting Wednesday" not in facts               # facts stays pinned-only
    assert "A cited fact." in facts
    # the discrepancy now lives in 'What's still open' (Merge 1), with the unknown
    open_sec = _section(html, "story-0-open")
    assert "Meeting Wednesday" in open_sec


def test_no_unresolved_jumplist_entry_after_the_fold():
    """The fold retires the 'Unresolved' jumplist entry; 'What's still open'
    carries its own single entry (present when it has prose OR discrepancies).
    WAS test_unresolved_in_jumplist_only_when_present."""
    jl = _deep(m3_brief(with_discrepancy=True)).split(
        'deep-jumplist')[1].split("</p>")[0]
    assert 'href="#story-0-unresolved"' not in jl
    assert 'href="#story-0-open"' in jl                   # open present (unknown + disc)


# ===========================================================================
# 3. NL-66(b) — In-Brief sources-&-context view ($0, honestly labeled)
# ===========================================================================

def _seed_quick_edition(con):
    """An edition with a quick-tier In-Brief slot (slot 4) that has NO analysis
    brief — its item_ids resolve to real source_items rows."""
    with con:
        con.execute(
            "INSERT INTO source_items (id, source_type, outlet, url, title)"
            " VALUES (?,?,?,?,?)",
            (900101, "rss", "Al Jazeera", "https://aj.example/a",
             "Court independence in question"))
        con.execute(
            "INSERT INTO source_items (id, source_type, outlet, url, title)"
            " VALUES (?,?,?,?,?)",
            (900102, "rss", "CNBC", "https://cnbc.example/b", "Markets react"))
    slots = []
    for n in (1, 2, 3):
        slots.append({"slot": str(n), "story_title": f"Story {n}",
                      "summary": f"s{n}", "item_ids": [], "outlets": ["The Hill"],
                      "matched_tags": [], "matched_memory": [], "override": False,
                      "corroboration_label": "Reported by 1 named outlet"})
    slots.append({"slot": "4", "story_title": "Supreme Court independence",
                  "summary": "A quick-hit summary of the court story.",
                  "item_ids": [900101, 900102], "outlets": ["Al Jazeera", "CNBC"],
                  "matched_tags": [{"name": "US politics"}],
                  "matched_memory": ["Supreme Court"], "override": False,
                  "corroboration_label": "Reported by 2 named outlets"})
    with con:
        con.execute("INSERT INTO briefings (date, story_slots) VALUES (?, ?)",
                    (DATE, json.dumps(slots)))
    # the one analyst brief (slot 1) so the analyst path stays exercised
    analysis.persist_brief(
        con, DATE, 1, "full", "valid",
        {"pinned_facts": [{"fact": "A cited fact.", "cites": ["S1"]}],
         "ledger": [], "mechanism": "m [S1].", "effects": [], "arc": None,
         "unknowns": [], "watch": [],
         "sources": [{"key": "S1", "outlet": "The Hill", "title": "Story",
                      "url": "https://thehill.com/a", "retrieved_at": "",
                      "kind": "cluster-full-text"}],
         "notes_for_writer": ""},
        "", 0.0, {"manifest": {}, "degraded": None},
        sources={"S1": {"kind": "cluster-full-text", "outlet": "The Hill",
                        "title": "Story", "url": "https://thehill.com/a",
                        "retrieved_at": "", "text": "b"}})
    entry = {"ts": "2026-07-07T01:00:00Z", "date": DATE, "status": "ok",
             "sample": False,
             "tiers": ["full", "medium", "medium", "quick"],
             "stories": [{"headline": f"Headline {n}", "lede": "Lede."}
                         for n in (1, 2, 3, 4)]}
    from newslens import paths
    (paths.DATA_DIR / "generation_log.jsonl").write_text(
        json.dumps(entry) + "\n", encoding="utf-8")


def test_in_brief_quick_slot_gets_sources_context_view(tmp_paths):
    """A quick-tier story with no analyst brief still has the $0 sources-&-
    context deep view. v8-M2: the quick slot is a lean STRIP — its headline is
    the door to that view (openDeepView), and the redundant '→ Sources &
    context' BOTTOM link is gone (the view labels itself with the eyebrow).
    'The full picture' stays the analyst tier's affordance."""
    db.migrate()
    con = db.connect()
    _seed_quick_edition(con)
    page, _ = server.build_page(con, DATE)
    con.close()
    # analyst affordance count is unchanged (only slot 1 has a valid brief)
    assert page.count("→ The full picture") == 1
    # the quick slot (story-3) reaches the sources-&-context view via its headline
    assert "→ Sources &amp; context" not in page        # no redundant bottom link on a strip
    assert "openDeepView('story-3', event)" in page     # the headline is the door
    assert 'id="view-deep-story-3"' in page             # ...and the $0 view is collected
    assert "Sources &amp; context" in page              # the view labels itself (eyebrow)


def test_sources_context_view_shows_summary_sources_tags_and_here_for(tmp_paths):
    """The $0 view surfaces what ALREADY exists for the slot: the summary, a
    source list (outlet + title from source_items), matched tags/threads, and
    the 'Here for' rationale — all from persisted rows, zero generation."""
    db.migrate()
    con = db.connect()
    _seed_quick_edition(con)
    page, _ = server.build_page(con, DATE)
    con.close()
    sec = page.split('id="view-deep-story-3"')[1].split("</section>")[0]
    # honestly labeled — sources & context, NOT the analyst 'full picture'
    assert "Sources &amp; context" in sec
    assert "The full picture" not in sec
    # NL-68 item 3 (SUPERSET LAW, DECISIONS 2026-07-16): the view opens with the
    # story's Today blurb — the SAME text the In-Brief snippet shows (st.lede) —
    # so it is never thinner than the Today card. WAS: it opened with the ranker
    # summary (slot['summary']), which could differ from the Today blurb.
    assert "Lede." in sec                        # the Today In-Brief blurb (superset)
    # a real source list resolved from item_ids -> source_items
    assert "Al Jazeera" in sec and "Court independence in question" in sec
    assert 'href="https://aj.example/a"' in sec
    assert "CNBC" in sec
    # matched tag + tracked thread + the corroboration label
    assert "US politics" in sec and "Supreme Court" in sec
    assert "Reported by 2 named outlets" in sec
    # the 'Here for' rationale (shared with Today's meta-footnote logic)
    assert "Here for" in sec


def test_sources_context_view_is_not_the_analyst_tier(tmp_paths):
    """No analyst-only furniture leaks into the $0 view: it does not claim the
    analyst trust footer ('cited, not verified') nor a 'The full picture'
    eyebrow — the honest-labeling contract (NOT the analyst tier)."""
    db.migrate()
    con = db.connect()
    _seed_quick_edition(con)
    page, _ = server.build_page(con, DATE)
    con.close()
    sec = page.split('id="view-deep-story-3"')[1].split("</section>")[0]
    assert "cited, not verified" not in sec
    assert '<p class="deep-eyebrow">The full picture</p>' not in sec


def test_sources_context_renders_in_archive_edition_path_too(tmp_paths):
    """The sources-&-context deep view rides the archive-in-place path (NL-11),
    same as the analyst deep views — reached from the strip headline and
    slug-prefixed. v8-M2: no '→ Sources & context' bottom link (lean strip); the
    view itself (eyebrow + slug-prefixed id) is what rides the path."""
    db.migrate()
    con = db.connect()
    _seed_quick_edition(con)
    html, rendered = server.build_edition_fragment(con, DATE)
    con.close()
    assert rendered == DATE
    assert "→ Sources &amp; context" not in html                    # no bottom link on a strip
    assert f"openDeepView('ed{DATE}-story-3'" in html               # the headline is the door
    assert f'id="view-deep-ed{DATE}-story-3"' in html               # the $0 view rides the path
    assert "Sources &amp; context" in html                          # the view labels itself


def test_sources_context_honest_empty_when_slot_has_no_sources(tmp_paths):
    """A quick slot with no resolvable sources and no tags renders the view
    with an honest empty note, never a crash or a fabricated source (NL-11
    missing-input class)."""
    db.migrate()
    con = db.connect()
    slots = [{"slot": "1", "story_title": "S1", "summary": "s1", "item_ids": [],
              "outlets": [], "matched_tags": [], "matched_memory": [],
              "override": False, "corroboration_label": ""}]
    with con:
        con.execute("INSERT INTO briefings (date, story_slots) VALUES (?, ?)",
                    (DATE, json.dumps(slots)))
    from newslens import paths
    entry = {"date": DATE, "status": "ok", "tiers": ["quick"],
             "stories": [{"headline": "Lone quick hit", "lede": ""}]}
    (paths.DATA_DIR / "generation_log.jsonl").write_text(
        json.dumps(entry) + "\n", encoding="utf-8")
    html = server._render_sources_context_view(
        "story-0", "Lone quick hit",
        {"headline": "Lone quick hit", "lede": "", "movements": []},
        slots[0], con, DATE)
    con.close()
    assert 'id="view-deep-story-0"' in html          # view still renders
    assert "No sources" in html or "no source" in html.lower()  # honest empty


# ===========================================================================
# 4. The M2-gate cites fork — validate_state is ledger-resolved-only
# ===========================================================================

def test_validate_state_rejects_an_edition_only_cite_as_fabrication():
    """M3 decision (fork carried from the M2 gate): thread_state.cites_json
    persists ledger-resolved cites ONLY, so validate_state must not ACCEPT a
    cite that cites_json would drop. An edition date that is NOT a ledger date
    is the BUG-25 fabrication class — rejected. RED against the pre-M3 wider
    `resolvable = ledger_dates | edition_dates`."""
    with pytest.raises(memory_core.StateRejected, match="fabrication"):
        memory_core.validate_state(
            "The strait stayed shut (2026-07-08).",
            # 2026-07-08 never moved THIS thread; with edition_dates dropped
            # (NL-75) a non-ledger date is unresolvable -> the fabrication class.
            ledger_dates={"2026-07-10"})


def test_validate_state_still_accepts_a_ledger_resolved_cite():
    """No regression: a cite that resolves to a real ledger date validates."""
    clean, _ = memory_core.validate_state(
        "The strait stayed shut (2026-07-10).",
        ledger_dates={"2026-07-10"})
    assert clean.startswith("The strait stayed shut")
