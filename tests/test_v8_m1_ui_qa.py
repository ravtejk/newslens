"""v8-M1 UI increment (items 3/4/5/8) — QA adversarial pass (QA-owned).

Item 4 at full grade (the principal's twice-raised must-be-visibly-right
surface): the stripper's punctuation edge set, zero-source/unresolvable-key
paragraphs, unicode + hostile outlet names, per-paragraph cluster independence,
outlet dedup, the ▸ glyph grep-dead on non-discrepancy deep views, the
discrepancy drawer pinned as the ONE surviving inline-attribution surface
(carve-out FLAGGED to the gate), and the Sources drawer as the compensating
name surface. Item 5's sharper boundaries (baseline-only, pending/failed-
baseline-only) pinned and FLAGGED. Item 3's full five-section ordering chain +
door gating. One records defect born-red.

Offline by construction under the autouse sandbox; $0.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from newslens import db, labels, server, webui

DATE = "2026-07-10"


def _con():
    db.migrate()
    return db.connect()


def _brief(mechanism="M.", facts=None, effects=None, sources=None, ledger=None):
    return {"pinned_facts": facts if facts is not None else [],
            "mechanism": mechanism,
            "effects": effects if effects is not None else [],
            "ledger": ledger if ledger is not None else [],
            "sources": sources if sources is not None else []}


def _deep(brief, **kw):
    return server._render_deep_view("story-0", "H", {"header": {}, "brief": brief},
                                    DATE, **kw)


def _section(html, anchor):
    return html.split(f'id="{anchor}"')[1].split("</div>")[0]


_SOURCES = [{"key": "S1", "outlet": "Reuters"}, {"key": "S2", "outlet": "AP"},
            {"key": "R2", "outlet": "BBC"}, {"key": "P3", "outlet": "The Hill"}]


# ==========================================================================
# item 4 — the stripper edge set (punctuation integrity)
# ==========================================================================

@pytest.mark.parametrize("mech,expected_prose", [
    ("Rose 40% [S1].", "Rose 40%."),                      # sentence-final key
    ("Rose [S1], then fell [S2].", "Rose, then fell."),   # comma follows the key
    ("[S1] Leading key.", "Leading key."),                # key opens the prose
    ("Adjacent [S1][S2] keys.", "Adjacent keys."),        # back-to-back keys
    ('He said "it works [S1]" today.', 'He said "it works" today.'),  # in-quote
    ("Multi [S1, S2] key.", "Multi key."),                # multi-key group
    ("Mixed [S1,R2, P3] keys.", "Mixed keys."),           # mixed families, spacing
])
def test_item4_stripper_edge_set_reads_unbroken(mech, expected_prose):
    """The stripped mechanism prose must read as if the keys never existed —
    no orphaned punctuation, no double spaces, no leftover brackets."""
    html = _deep(_brief(mechanism=mech, sources=_SOURCES))
    m = _section(html, "story-0-mechanism")
    prose = m.split("<p>")[1].split("</p>")[0]
    assert prose == server._e(expected_prose)
    assert "  " not in prose                              # no double spaces
    assert "[" not in prose and "]" not in prose


@pytest.mark.parametrize("mech", [
    "The [2024] budget survives.",        # bracketed year — content, not a key
    "See section [A1] of the act.",       # non-SCRP letter — content
    "lower [s1] is not a key.",           # the grammar is case-sensitive
])
def test_item4_non_key_brackets_survive_as_content(mech):
    """The stripper must never eat bracketed CONTENT — only the [SCRP]\\d+ cite
    grammar. A bracketed year or clause reference is prose."""
    html = _deep(_brief(mechanism=mech, sources=_SOURCES))
    m = _section(html, "story-0-mechanism")
    assert server._e(mech) in m                           # byte-intact prose


def test_item4_parenthesized_key_leaves_empty_parens_pinned():
    """BEHAVIOR PIN (accepted residual unless the gate rules otherwise): a key
    an analyst wrapped in parens — '(see [S1])' — strips to '(see)'; a BARE
    parenthesized key '([S1])' strips to '()'. The cite grammar puts keys bare
    in prose, so this shape is analyst misbehavior; the renderer's contract is
    strip-only, never rewrite. Pinned so the residual is a documented choice."""
    html = _deep(_brief(mechanism="A claim ([S1]) here.", sources=_SOURCES))
    m = _section(html, "story-0-mechanism")
    assert "A claim () here." in m                        # today's honest residual


# ==========================================================================
# item 4 — zero-source / unresolvable keys / dedup / unicode
# ==========================================================================

def test_item4_zero_source_surfaces_carry_no_apparatus_at_all():
    """No cites anywhere: no src-cluster, no em-dash orphan, no empty count
    span, byte-clean list items."""
    brief = _brief(mechanism="Plain mechanism prose.",
                   facts=[{"fact": "Uncited fact.", "cites": []}],
                   effects=[{"effect": "Uncited effect.", "cites": []}],
                   ledger=[{"claim": "Rose 40% this year.", "cites": [],
                            "verified": True}],
                   sources=_SOURCES)
    html = _deep(brief)
    assert "src-cluster" not in html
    assert 'class="cite"' not in html                     # no counts either
    assert "<li>Uncited fact.</li>" in html               # byte-clean, no orphan space
    assert "— <" not in html and "—</p>" not in html      # no dangling em-dash


def test_item4_unresolvable_keys_strip_but_emit_no_dead_cluster():
    """Keys that resolve to NO outlet (unknown key, or a source row with no
    outlet name): stripped from prose, and NO cluster/count renders — never an
    empty '— ' colophon or '(0 outlets)'."""
    brief = _brief(mechanism="Claim [S9] stands [X1].",
                   facts=[{"fact": "F.", "cites": ["S9"]}],
                   effects=[{"effect": "E.", "cites": ["S9"]}],
                   sources=[{"key": "S9", "outlet": ""}])   # resolves, but nameless
    html = _deep(brief)
    m = _section(html, "story-0-mechanism")
    assert "[S9]" not in m
    assert "[X1]" in m                       # X is outside the SCRP grammar — content
    assert "src-cluster" not in html
    assert "(0 outlet" not in html


def test_item4_outlet_dedup_by_name_in_count_and_cluster():
    brief = _brief(mechanism="Claim [S1] and again [S2].",
                   facts=[{"fact": "F.", "cites": ["S1", "S2"]}],
                   sources=[{"key": "S1", "outlet": "AP"},
                            {"key": "S2", "outlet": "AP"}])
    html = _deep(brief)
    facts = _section(html, "story-0-facts")
    assert '<span class="cite">(1 outlet)</span>' in facts    # dedup by NAME
    m = _section(html, "story-0-mechanism")
    assert '<p class="src-cluster">— AP</p>' in m             # named once
    assert m.count("AP") == 1


def test_item4_unicode_and_hostile_outlet_names_render_escaped():
    brief = _brief(
        mechanism="Global claim [S1][S2][R2].",
        sources=[{"key": "S1", "outlet": "Süddeutsche Zeitung"},
                 {"key": "S2", "outlet": "朝日新聞"},
                 {"key": "R2", "outlet": 'A&B <News>'}])
    html = _deep(brief)
    m = _section(html, "story-0-mechanism")
    assert "Süddeutsche Zeitung · 朝日新聞" in m
    assert "A&amp;B &lt;News&gt;" in m                    # escaped, not raw
    assert "<News>" not in m


def test_item4_each_effect_paragraph_closes_with_its_own_cluster():
    """Per-paragraph independence: three cited effects carry three clusters in
    order; the uncited one between them carries none."""
    brief = _brief(
        effects=[{"effect": "First.", "cites": ["S1"]},
                 {"effect": "Background only.", "cites": []},
                 {"effect": "Second.", "cites": ["S2"]},
                 {"effect": "Third.", "cites": ["R2"]}],
        sources=_SOURCES)
    eff = _section(_deep(brief), "story-0-effects")
    clusters = re.findall(r'<p class="src-cluster">— ([^<]+)</p>', eff)
    assert clusters == ["Reuters", "AP", "BBC"]           # order preserved, 3 not 4
    # the uncited paragraph is followed directly by the next paragraph, no cluster
    assert "Background only.</p><p" in eff.replace("\n", "")
    # and no "(background)" inline marker survives from the old grammar
    assert "(background)" not in eff


# ==========================================================================
# item 4 — the glyph is dead on non-discrepancy views; the carve-out pinned
# ==========================================================================

def test_item4_caret_glyph_grep_dead_on_a_full_nondiscrepancy_deep_view(tmp_paths):
    """The acceptance grep, on a deep view exercising EVERY section including
    the relocated timeline: no ▸, no caret class, no cite-fold, no inline
    (via ...), no raw keys."""
    con = _con()
    try:
        now = "2026-07-01T00:00:00.000Z"
        cur = con.execute(
            "INSERT INTO memory (topic, status, status_changed_at, created_at, "
            "updated_at) VALUES ('Iran War', 'active', ?, ?, ?)", (now, now, now))
        tid = cur.lastrowid
        con.execute("INSERT INTO briefings (date, story_slots) VALUES "
                    "('2026-07-05', '[]')")
        con.execute(
            "INSERT INTO thread_deltas (thread_id, edition_date, slot, verdict, "
            "what_happened, significance, cites_json) VALUES "
            "(?, '2026-07-05', 1, 'advances', 'It moved.', 'M.', '[\"S1\"]')",
            (tid,))
        con.commit()
        brief = _brief(mechanism="Claim [S1].",
                       facts=[{"fact": "F.", "cites": ["S1"]}],
                       effects=[{"effect": "E.", "cites": ["S2"]}],
                       ledger=[{"claim": "Rose 40%.", "cites": ["S1"],
                                "verified": True}],
                       sources=_SOURCES)
        brief["unknowns"] = [{"question": "What next?"}]
        html = _deep(brief, con=con,
                     slot={"matched_memory": ["Iran War"],
                           "story_title": "Iran War"})
    finally:
        con.close()
    assert "▸" not in html
    assert 'class="caret"' not in html
    assert "cite-fold" not in html
    assert "(via" not in html
    assert not re.search(r"\[[SCRP]\d", html)             # no raw keys anywhere


def test_item4_discrepancy_drawer_is_the_one_surviving_inline_surface_FLAG():
    """CARVE-OUT PINNED, FLAGGED TO THE GATE (not ruled here): the contested-
    figures drawer inside 'What's still open' still renders per-side INLINE
    attribution qualifiers and a ▸ summary caret. Defensible — attribution IS
    the content of a discrepancy row ('AP says 12, BBC says 15' is meaningless
    nameless) — but it is a carve-out from item 4's 'no inline apparatus
    anywhere in the deep view', and the batch did not state it. This pin makes
    the current behavior deliberate; the gate rules whether the carve-out
    stands or the drawer gets the item-4 treatment.

    GATE RULED 2026-07-17: STANDS. Per-side attribution is not apparatus, it
    is the content ('12 (AP) vs 15 (BBC)' attributed to nobody resolves
    nothing); the ▸ here is NL-68's collapse-by-default section drawer, not a
    mid-prose cite fold. The law's wording in server.py now states the
    exception explicitly."""
    brief = _brief(
        ledger=[{"claim": "Death toll contested.", "discrepancy": True,
                 "a": {"value": "12 dead", "cites": ["S1"]},
                 "b": {"value": "15 dead", "cites": ["S2"]}}],
        sources=[{"key": "S1", "outlet": "Reuters", "kind": "press"},
                 {"key": "S2", "outlet": "AP", "kind": "press"}])
    brief["unknowns"] = []
    html = _deep(brief)
    assert 'class="deep-open-discrepancies"' in html      # the drawer renders
    disc = html.split('class="deep-open-discrepancies"')[1].split("</details>")[0]
    assert "▸" in disc                                    # the carve-out caret
    assert "12 dead" in disc and "15 dead" in disc
    assert 'class="cite"' in disc                         # inline qualifiers survive
    # and it stays a CLOSED drawer (never open-by-default inline apparatus)
    assert "<details open" not in html.split(
        'class="deep-open-discrepancies"')[0][-60:] + html.split(
        'class="deep-open-discrepancies"')[1][:1]


def test_item4_dead_apparatus_absent_from_shipped_page_assets():
    """GATE FIX-2 re-introduction guard (2026-07-17): the fold apparatus is
    REMOVED, not merely unused — `cite-fold`, `collapseCiteFolds`, and
    `cal-note` must be absent from the assembled page CSS/JS. A green suite
    with a dead corpse shipping on every morning page is the stale-records
    class this repo has now fixed three times."""
    assets = webui.CSS + webui.JS + webui.PAGE
    for dead in ("cite-fold", "collapseCiteFolds", "cal-note"):
        assert dead not in assets, f"dead apparatus {dead!r} still ships"


def test_item4_sources_drawer_still_carries_the_full_names():
    """The compensating surface: outlet names absent from facts/mechanism prose
    MUST resolve in the Sources section — the caret-kill moved names, never
    deleted them."""
    brief = _brief(mechanism="Claim [S1].",
                   facts=[{"fact": "F.", "cites": ["P3"]}],
                   sources=[{"key": "S1", "outlet": "Reuters", "url": "https://r.example/x"},
                            {"key": "P3", "outlet": "The Hill", "url": "https://h.example/y"}])
    html = _deep(brief)
    facts = _section(html, "story-0-facts")
    assert "The Hill" not in facts
    sources = html.split('id="story-0-sources"')[1]
    assert "Reuters" in sources and "The Hill" in sources


# ==========================================================================
# item 5 — the sharper boundaries (pinned + FLAGGED)
# ==========================================================================

def _seed_thread(con, topic, ref=True):
    now = "2026-07-01T00:00:00.000Z"
    if ref:
        con.execute("INSERT OR IGNORE INTO briefings (id, date, story_slots) "
                    "VALUES (1, '2026-07-08', '[]')")
    cur = con.execute(
        "INSERT INTO memory (topic, status, status_changed_at, created_at, "
        "updated_at, last_referenced_briefing_id) VALUES (?, 'active', ?, ?, ?, ?)",
        (topic, now, now, now, 1 if ref else None))
    return cur.lastrowid


def test_item5_ready_baseline_only_thread_reads_LAST_UPDATED_pinned_FLAG(tmp_paths):
    """GATE RULED 2026-07-17: a thread whose only content is a READY baseline
    (a real 'How we got here' a reader can open) is NOT empty and stamps LAST
    UPDATED off the baseline's OWN as_of_date — the content's date — never the
    ref/join pickup date (which is the follow's birth in disguise and leaves a
    no-stamp gap on never-referenced threads). Seeded so as_of (07-03) differs
    from the ref date (07-08) to prove the date source."""
    con = _con()
    try:
        tid = _seed_thread(con, "Baselined Quiet")
        con.execute(
            "INSERT INTO thread_baselines (thread_id, as_of_date, status, "
            "backgrounder, state_seed) VALUES (?, '2026-07-03', 'ready', "
            "'How we got here prose.', 'Seed.')", (tid,))
        con.commit()
        html = server._render_following(con)
    finally:
        con.close()
    assert "Baselined Quiet" in html
    assert "LAST UPDATED" in html                          # not empty: openable content
    assert "JUL 3" in html                                 # the baseline's OWN date
    assert "JUL 8" not in html                             # never the ref/pickup date
    assert "FOLLOWED" not in html


@pytest.mark.parametrize("bstatus", ["pending", "failed"])
def test_item5_pending_or_failed_baseline_only_thread_pinned_FLAG(tmp_paths, bstatus):
    """GATE RULED 2026-07-17: a thread whose only row is a PENDING or FAILED
    baseline has NOTHING a reader can open — machinery, not coverage — so the
    emptiness check demands status='ready' and these threads stamp the honest
    FOLLOWED <created>, never LAST UPDATED off a pickup date."""
    con = _con()
    try:
        tid = _seed_thread(con, "Machinery Only")
        con.execute(
            "INSERT INTO thread_baselines (thread_id, as_of_date, status, reason) "
            "VALUES (?, '2026-07-03', ?, 'not done')", (tid, bstatus))
        con.commit()
        html = server._render_following(con)
    finally:
        con.close()
    assert "Machinery Only" in html
    assert "FOLLOWED" in html                              # the ruled semantics
    assert "JUL 1" in html                                 # the follow's real date
    assert "LAST UPDATED" not in html


def test_item5_state_only_thread_keeps_LAST_UPDATED(tmp_paths):
    con = _con()
    try:
        tid = _seed_thread(con, "Stateful Quiet")
        con.execute(
            "INSERT INTO thread_state (thread_id, as_of_date, state_text) "
            "VALUES (?, '2026-07-06', 'Standing state.')", (tid,))
        con.commit()
        html = server._render_following(con)
    finally:
        con.close()
    assert "Stateful Quiet" in html
    assert "LAST UPDATED" in html and "FOLLOWED" not in html


def test_item5_empty_thread_without_any_briefing_ref(tmp_paths):
    """Empty thread, NO ref join at all: still FOLLOWED <created>, no crash,
    no LAST UPDATED, and the stamp carries the actual short date."""
    con = _con()
    try:
        _seed_thread(con, "Fresh Follow", ref=False)
        con.commit()
        html = server._render_following(con)
    finally:
        con.close()
    assert "Fresh Follow" in html
    assert "FOLLOWED" in html and "LAST UPDATED" not in html
    assert "JUL 1" in html                                # the created date renders


# ==========================================================================
# item 3 — the full ordering chain + door gating
# ==========================================================================

def test_item3_full_five_section_chain_and_door_order(tmp_paths):
    con = _con()
    try:
        now = "2026-07-01T00:00:00.000Z"
        cur = con.execute(
            "INSERT INTO memory (topic, status, status_changed_at, created_at, "
            "updated_at) VALUES ('Iran War', 'active', ?, ?, ?)", (now, now, now))
        tid = cur.lastrowid
        con.execute("INSERT INTO briefings (date, story_slots) VALUES "
                    "('2026-07-05', '[]')")
        con.execute(
            "INSERT INTO thread_deltas (thread_id, edition_date, slot, verdict, "
            "what_happened, significance, cites_json) VALUES "
            "(?, '2026-07-05', 1, 'advances', 'Moved.', 'M.', '[\"S1\"]')", (tid,))
        con.commit()
        brief = _brief(mechanism="M [S1].",
                       facts=[{"fact": "F.", "cites": ["S1"]}],
                       effects=[{"effect": "E.", "cites": ["S2"]}],
                       sources=_SOURCES)
        brief["unknowns"] = [{"question": "Open?"}]
        html = _deep(brief, con=con,
                     slot={"matched_memory": ["Iran War"],
                           "story_title": "Iran War"})
    finally:
        con.close()
    order = [html.index(f'id="story-0-{a}"')
             for a in ("facts", "mechanism", "effects", "open", "timeline",
                       "sources")]
    assert order == sorted(order)                          # strictly the ruled order
    assert html.count('id="story-0-timeline"') == 1        # exactly one anchor
    jl = html.split('deep-jumplist')[1].split("</p>")[0]
    doors = re.findall(r'#story-0-([a-z]+)"', jl)
    assert doors.index("timeline") == len(doors) - 2       # second-from-last door
    assert doors[-1] == "sources"


def test_item3_no_timeline_no_door_no_dead_anchor(tmp_paths):
    html = _deep(_brief(mechanism="M.", facts=[{"fact": "F.", "cites": []}],
                        sources=[]))
    assert 'id="story-0-timeline"' not in html
    jl = html.split('deep-jumplist')[1].split("</p>")[0]
    assert "#story-0-timeline" not in jl                   # no dead door


# ==========================================================================
# records correctness
# ==========================================================================

def test_BORN_RED_v8_test_file_docstring_claims_only_shipped_items():
    """BORN RED (QA records pin). tests/test_v8_m1_ui.py's module docstring
    describes item-2 (slim memory stamp) and item-1 (newspaper front grid)
    pins — both HELD to the next increment; no such tests exist in the file.
    A reader grepping the docstring believes the held items are pinned. Same
    class as the two prior stale-pointer defects.

    FIX CONTRACT (flips green): trim the docstring to the shipped items
    (5/3/4[, 8 lives in test_nl68_batch]) or mark items 1+2 explicitly as HELD
    — the docstring must not describe absent tests as present."""
    doc = (Path(__file__).parent / "test_v8_m1_ui.py").read_text(
        encoding="utf-8").split('"""')[1]
    for phrase in ("newspaper front grid", "slim memory stamp"):
        assert phrase not in doc or "HELD" in doc, (
            f"docstring describes '{phrase}' but the increment holds it and no "
            "such test exists in the file")
