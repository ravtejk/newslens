"""M9-M3 — writer-from-brief + the deep view (implementer-written; QA
extends). Offline: model calls injected, server rendered in-process
(zero consumption events — the M7 pattern)."""

import json

from newslens import analysis, db, generate, server


DATE = "2026-07-07"


def _seed(con, with_brief=True):
    slots = [{"slot": str(n), "story_title": f"Story {n}",
              "summary": f"s{n}", "item_ids": [], "outlets": ["The Hill"],
              "matched_tags": [], "matched_memory": [], "override": False,
              "corroboration_label": "Reported by 1 named outlet"}
             for n in (1, 2, 3)]
    with con:
        con.execute("INSERT INTO briefings (date, story_slots) VALUES (?, ?)",
                    (DATE, json.dumps(slots)))
    if with_brief:
        brief = {"pinned_facts": [{"fact": "A cited fact.", "cites": ["S1"]}],
                 "ledger": [{"claim": "A ledger claim.", "cites": ["S1"],
                             "provenance": "cluster-single"}],
                 "mechanism": "An actor answers to a constraint [S1].",
                 "effects": [{"effect": "A stated take.", "basis": "attributed",
                              "holder": "Jan Novak", "cites": ["S1"]}],
                 "arc": None,
                 "unknowns": [{"question": "Which members resist",
                               "why_material": "blocks unanimity",
                               "would_resolve": "the communique"}],
                 "watch": [{"observable": "communique by Thursday",
                            "settles": "resistance"}],
                 "sources": [{"key": "S1", "outlet": "The Hill",
                              "title": "Story", "url": "https://thehill.com/a",
                              "retrieved_at": "2026-07-07T00:00Z",
                              "kind": "cluster-full-text"}],
                 "notes_for_writer": "trace the pledge number."}
        analysis.persist_brief(
            con, DATE, 1, "full", "valid", brief, "", 0.02,
            {"manifest": {}, "degraded": None},
            sources={"S1": {"kind": "cluster-full-text", "outlet": "The Hill",
                            "title": "Story", "url": "https://thehill.com/a",
                            "retrieved_at": "", "text": "body"}})


# --- writer material: the brief IS the report lane -------------------------

def test_briefed_story_material_is_the_writer_view_not_excerpts(tmp_paths):
    db.migrate()
    con = db.connect()
    _seed(con)
    inputs = generate.load_briefing_inputs(con, DATE)
    inputs["briefs_by_slot"] = {1: analysis.latest_valid_brief(con, DATE, 1)}
    inputs["analyst_slot3_tier"] = None
    prompt = generate.build_narrative_prompt(DATE, "A", inputs)
    assert "TRACE, DON'T GENERATE" in prompt
    assert "PINNED FACTS" in prompt and "A cited fact." in prompt
    assert "trace the pledge number." in prompt          # notes_for_writer rides
    # unbriefed depth stories carry the disclosure line + excerpts path
    assert "analysis unavailable for this story" in prompt
    con.close()


def test_slot3_is_pinned_full_picture_medium(tmp_paths):
    """NL-63 M2: slot 3 is one of the exactly-3 full-picture stories — pinned to
    'medium'. No 'TIER RULED BY THE ANALYST' line; a medium slot-3 is accepted,
    a quick slot-3 is rejected (the demote-to-quick path is retired)."""
    db.migrate()
    con = db.connect()
    _seed(con, with_brief=False)
    inputs = generate.load_briefing_inputs(con, DATE)
    inputs["briefs_by_slot"] = {}
    inputs["analyst_slot3_tier"] = None
    prompt = generate.build_narrative_prompt(DATE, "A", inputs)
    assert "TIER RULED BY THE ANALYST" not in prompt
    base = {"lede": "L", "why_it_matters": "W", "watch_for": "X",
            "why_label": "Why it matters", "watch_label": "Watch for"}
    ok = [{**base, "tier": "full", "headline": "H1 one two"},
          {**base, "tier": "medium", "headline": "H2 one two"},
          {**base, "tier": "medium", "headline": "H3 one two"}]
    stories, _ = generate.validate_narrative_payload(
        {"stories": ok}, inputs["slots"], "A")
    assert stories[2]["tier"] == "medium"
    import pytest
    bad = [dict(ok[0]), dict(ok[1]), {**base, "tier": "quick", "headline": "H3 one two"}]
    with pytest.raises(ValueError, match="tier 'quick' not allowed"):
        generate.validate_narrative_payload(
            {"stories": bad}, inputs["slots"], "A")
    con.close()


# --- the deep view ----------------------------------------------------------

def test_deep_view_renders_with_affordance_only_where_valid_brief(tmp_paths):
    db.migrate()
    con = db.connect()
    _seed(con)
    # log stories so the server has structure
    from newslens import paths
    entry = {"ts": "2026-07-07T01:00:00Z", "date": DATE, "status": "ok",
             "sample": False, "tiers": ["full", "medium", "quick"],
             "stories": [{"headline": f"Headline {n}", "lede": "Lede."}
                          for n in (1, 2, 3)]}
    (paths.DATA_DIR / "generation_log.jsonl").write_text(
        json.dumps(entry) + "\n", encoding="utf-8")
    page, rendered = server.build_page(con, DATE)
    # affordance present ONLY on slot 1 (the one valid brief)
    assert page.count("→ The full picture") == 1
    assert "openDeepView('story-0', event)" in page
    assert "view-deep-story-0" in page and "view-deep-story-1" not in page
    # the file's law: cited never verified; no re-lede; jumplist; back-nav
    assert "cited, not verified" in page
    assert "Lede." not in page.split("view-deep-story-0")[1].split("</section>")[0]
    assert "deep-jumplist" in page and "closeDeepView(event)" in page
    assert "lastStoryAnchor" in page
    # trailing qualifier grammar, not badges
    assert "(The Hill · 1 outlet)" in page
    # degraded-hidden == absent: rejected brief renders nothing
    analysis.persist_brief(con, DATE, 2, "medium", "rejected", None,
                           "fabricated citation", 0.01, {"manifest": {}},
                           sources={})
    page2, _ = server.build_page(con, DATE)
    assert page2.count("→ The full picture") == 1  # unchanged
    con.close()


def test_deep_view_never_regenerates_and_reads_newest_valid(tmp_paths):
    db.migrate()
    con = db.connect()
    _seed(con)
    doc = analysis.latest_valid_brief(con, DATE, 1)
    html = server._render_deep_view("story-0", "Headline 1", doc, DATE)
    assert "Based on 1 cited source(s)" in html
    assert "notes_for_writer" not in html and "trace the pledge" not in html
    assert 'href="https://thehill.com/a"' in html  # real accessible source link
    con.close()


def test_slot3_verdict_derives_from_persisted_rows_both_paths(tmp_paths):
    """M3 gate item 2: the analyst's tier verdict is a binding contract —
    the SAME derivation serves the fresh run and --no-refresh reloads."""
    db.migrate()
    con = db.connect()
    _seed(con, with_brief=False)
    assert analysis.analyst_slot3_tier(con, DATE) is None      # no verdict
    analysis.persist_brief(con, DATE, 3, "medium", "rejected", None,
                           "demoted-quick: thin material", 0.0,
                           {"verdict": "demoted-quick"}, sources={})
    assert analysis.analyst_slot3_tier(con, DATE) == "quick"   # demotion holds
    analysis.persist_brief(con, DATE, 3, "medium", "valid",
                           {"pinned_facts": []}, "", 0.0, {}, sources={})
    assert analysis.analyst_slot3_tier(con, DATE) == "medium"  # newest wins
    con.close()
