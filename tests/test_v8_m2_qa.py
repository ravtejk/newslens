"""v8-M2 QA pass (2026-07-18) — adversarial pins for the newspaper grid + the
slim memory stamp, against the principal's binding contract (DECISIONS
2026-07-18: THE RECTANGLE LAW / THREE-TIER LAW / the stamp's two forms).

The stamp is an HONESTY SURFACE: it makes coverage-history claims to the
principal ("Nth entry on this thread · last covered <date>"). These pins hold
the ordinal arithmetic to the record — split-days, backfills, day-one silence,
malformed ledger dates, per-edition dedup across BOTH stamp forms (the flip-era
counts anchored on the full form only and could not catch a doubled degraded
signal).

The grout heuristic (server._grid_columns) is PRESENTATION ONLY: a pathological
estimate may look unbalanced but must never drop, duplicate, or reorder a
story. The degenerate-input pins here (0-word bodies, huge outliers, ties,
interleaved tiers, 1-slot editions) hold that line.

Offline by construction (autouse conftest sandbox + loopback guard); $0 — no
test in this file reaches an LLM seat.
"""
from __future__ import annotations

import json
import re

import pytest

from newslens import db, server, webui
from newslens import memory_core

from test_ui_polish import slot, story, seed, TODAY
from test_nl68_batch import _seed_thread_with_ledger


def _con():
    db.migrate()
    return db.connect()


def _today_view(page: str) -> str:
    return page.split('id="view-today"')[1].split('id="view-following"')[0]


def _article_ids(html: str):
    return re.findall(r'<article[^>]*\bid="((?:ed[0-9-]+-)?story-\d+)"', html)


def _article(html: str, sid: str) -> str:
    start = html.index(f'id="{sid}"')
    return html[start:html.index("</article>", start)]


def _add_deltas(con, tid: int, dates) -> None:
    for d in dates:
        con.execute(
            "INSERT INTO thread_deltas (thread_id, edition_date, verdict,"
            " what_happened, significance, cites_json, slot) VALUES"
            " (?, ?, 'advances', 'Something happened.', 'It mattered.',"
            " '[]', NULL)", (tid, d))
    con.commit()


def _new_thread(con, topic: str) -> int:
    now = "2026-07-01T00:00:00.000Z"
    tid = con.execute(
        "INSERT INTO memory (topic, status, status_changed_at, created_at,"
        " updated_at) VALUES (?, 'active', ?, ?, ?)", (topic, now, now, now)
    ).lastrowid
    con.commit()
    return tid


def _seed_edition(con, date, slots, stories):
    """seed() with a date parameter — for archive-path (as-of) pins."""
    from newslens import generate, ranking
    from conftest import PROTOTYPE_ROOT  # noqa: F401  (import parity with seed)
    inputs = {"slots": slots, "items_by_slot": {s["slot"]: [] for s in slots},
              "threads": [], "prior_ctx": None, "continuity_status": "none",
              "window_meta": None, "corroboration": {}}
    narrative = generate.assemble_narrative(date, "A", stories, inputs)
    con.execute(
        "INSERT INTO briefings (date, story_slots, corroboration_labels,"
        " narrative_text, generated_at) VALUES (?, ?, ?, ?, ?)",
        (date, json.dumps(slots),
         json.dumps({"standing_caveat": ranking.CORROBORATION_CAVEAT,
                     "per_story": []}),
         narrative, "2026-07-01T00:00:00.000Z"))
    con.commit()


# ==========================================================================
# A. Stamp ordinal edges — the honesty surface
# ==========================================================================

def test_qa_ordinal_num_suffix_sweep():
    """The ordinal suffix is right for every class the record can reach —
    including the 11th/12th/13th teens rule and the 21st/111th boundaries. A
    wrong suffix is a small lie in a machine register that claims precision."""
    expected = {1: "1st", 2: "2nd", 3: "3rd", 4: "4th", 10: "10th",
                11: "11th", 12: "12th", 13: "13th", 14: "14th", 20: "20th",
                21: "21st", 22: "22nd", 23: "23rd", 24: "24th",
                101: "101st", 111: "111th", 112: "112th", 113: "113th",
                121: "121st", 213: "213th"}
    for n, want in expected.items():
        assert server._ordinal_num(n) == want


def test_qa_stamp_none_when_only_todays_own_delta_exists(tmp_paths):
    """A thread whose ONLY ledger entry is today's own edition-date has no
    PRIOR coverage — no stamp, and specifically no '1st entry' noise (day-one
    silence is the claimed behavior; today's delta landing before render must
    not fake a history)."""
    con = _con()
    tid = _new_thread(con, "T")
    _add_deltas(con, tid, ["2026-07-14"])
    assert memory_core.today_memory_stamp(con, tid, "2026-07-14") is None
    con.close()


def test_qa_stamp_none_when_ledger_dates_are_future_only(tmp_paths):
    """Ledger entries dated AFTER today (clock skew / corrupt backfill) are not
    prior coverage. No stamp — the stamp never reaches forward."""
    con = _con()
    tid = _new_thread(con, "T")
    _add_deltas(con, tid, ["2026-07-15", "2026-08-01"])
    assert memory_core.today_memory_stamp(con, tid, "2026-07-14") is None
    con.close()


def test_qa_stamp_ignores_non_calendar_ledger_dates(tmp_paths):
    """Malformed edition_dates ('', 'corrupt', 'July 5') are excluded from the
    ordinal — one real prior + three malformed rows is '2nd entry', not '5th'.
    The ordinal counts EDITIONS THAT EXIST, nothing else."""
    con = _con()
    tid = _new_thread(con, "T")
    _add_deltas(con, tid, ["2026-07-05", "", "corrupt", "July 5"])
    assert memory_core.today_memory_stamp(con, tid, "2026-07-14") == \
        (2, "2026-07-05")
    con.close()


def test_qa_stamp_backfill_distinct_dates_and_latest_prior(tmp_paths):
    """A backfilled ledger (the real 07-14 shape): many deltas across dates,
    including same-date duplicates, count as DISTINCT DATES — 3 distinct priors
    is '4th entry', last covered the LATEST prior date."""
    con = _con()
    tid = _new_thread(con, "T")
    _add_deltas(con, tid, ["2026-07-05", "2026-07-05", "2026-07-06",
                           "2026-07-10", "2026-07-10", "2026-07-10"])
    assert memory_core.today_memory_stamp(con, tid, "2026-07-14") == \
        (4, "2026-07-10")
    con.close()


def test_qa_stamp_teens_ordinal_renders_11th(tmp_paths):
    """Ten distinct priors render '11th entry' (the teens rule end-to-end on
    the page, not just in the suffix unit)."""
    con = _con()
    tid = _new_thread(con, "Long Thread")
    _add_deltas(con, tid, [f"2026-06-{d:02d}" for d in range(1, 11)])
    seed(con, [slot(1, "Lead", mem=("Long Thread",)), slot(2, "S2")],
         [story(1, "Lead"), story(2, "S2", "medium")])
    page, _ = server.build_page(con)
    con.close()
    lead = _article(_today_view(page), "story-0")
    assert "11th entry on this thread" in lead


def test_qa_archive_edition_stamp_is_as_of_that_edition(tmp_paths):
    """The archive path shares the body renderer, so an ARCHIVE edition carries
    the stamp AS OF ITS OWN DATE: a thread covered 07-05 and 07-08, rendered as
    the 07-08 edition, says '2nd entry … last covered Jul 5'. The edition's own
    delta (07-08) must NOT inflate the ordinal — priors are strictly BEFORE the
    edition date (the split-day-robust arithmetic, on the archive surface)."""
    con = _con()
    date = "2026-07-08"
    tid = _new_thread(con, "T")
    _add_deltas(con, tid, ["2026-07-05", date])
    _seed_edition(con, date,
                  [slot(1, "Lead", mem=("T",)), slot(2, "S2")],
                  [story(1, "Lead"), story(2, "S2", "medium")])
    html, rendered = server.build_edition_fragment(con, date)
    con.close()
    assert rendered == date
    lead = _article(html, f"ed{date}-story-0")
    assert "2nd entry on this thread" in lead
    assert "last covered Jul 5" in lead
    assert "3rd entry" not in lead              # own-date delta did not inflate


def test_qa_no_arc_prose_on_archive_edition_path_either(tmp_paths):
    """The no-prose law rides the SHARED body path: an archive edition body
    carries no arc prose block either — the stamp is the memory signal there
    too. (The deep views keep the full register; that is a different section.)"""
    con = _con()
    date = "2026-07-08"
    tid = _new_thread(con, "T")
    _add_deltas(con, tid, ["2026-07-05"])
    _seed_edition(con, date,
                  [slot(1, "Lead", mem=("T",)), slot(2, "S2")],
                  [story(1, "Lead"), story(2, "S2", "medium")])
    html, _ = server.build_edition_fragment(con, date)
    con.close()
    edition = html.split('id="view-edition"')[1].split("</section>")[0]
    assert "today-arc-line" not in edition
    assert "When we last covered this" not in edition
    assert 'class="memline"' in edition


def test_qa_split_day_strip_sibling_shows_no_degraded_stamp(tmp_paths):
    """BUG-35 dedup ACROSS FORMS: on a sanctioned-split day where the sibling
    is a STRIP, the lead carries the full stamp and the strip sibling's smeta
    carries NO degraded stamp — the covered-before signal appears exactly once
    on the page, in exactly one form. (The flip-era pins count the full-form
    phrase / .memline only, which a doubled DEGRADED signal would slip past —
    this pin closes that hole.)"""
    con = _con()
    _seed_thread_with_ledger(con, "Hormuz", prior_date="2026-07-05")
    slots = [slot(1, "Lead", mem=("Hormuz",)), slot(2, "S2"), slot(3, "S3"),
             slot(4, "Sibling strip", mem=("Hormuz",))]
    stories = [story(1, "Lead"), story(2, "S2", "medium"),
               story(3, "S3", "medium"), story(4, "Sibling strip", "quick")]
    seed(con, slots, stories)
    page, _ = server.build_page(con)
    con.close()
    today = _today_view(page)
    lead = _article(today, "story-0")
    assert "entry on this thread" in lead               # prominent slot wins, full form
    strip = _article(today, "story-3")
    assert "mem-dot" not in strip                       # no doubled degraded signal
    assert "last covered" not in strip
    assert today.count("mem-dot") == 1                  # one signal, whole page
    assert "Tracked ongoing story" not in today         # and no marker resurrection


def test_qa_multi_topic_slot_falls_through_day_one_to_historied(tmp_paths):
    """A slot matched to [day-one thread, historied thread] stamps from the
    HISTORIED one — a stampless first topic does not silence the slot."""
    con = _con()
    _new_thread(con, "Fresh")
    _seed_thread_with_ledger(con, "Old Hand", prior_date="2026-07-05")
    seed(con, [slot(1, "Lead", mem=("Fresh", "Old Hand")), slot(2, "S2")],
         [story(1, "Lead"), story(2, "S2", "medium")])
    page, _ = server.build_page(con)
    con.close()
    lead = _article(_today_view(page), "story-0")
    assert "entry on this thread" in lead


def test_qa_multi_topic_deduped_first_topic_silences_the_slot(tmp_paths):
    """CHARACTERIZATION (gate to confirm, not a law): when a slot's FIRST
    historied topic was already stamped on an earlier slot, the current code
    returns no stamp for the whole slot — it does NOT fall through to a second,
    unstamped historied topic (_memory_stamp_inner returns '' on the dedup hit
    rather than continuing). Defensible: the sibling's identity is the deduped
    thread; borrowing thread B's ordinal under thread A's sibling could
    misattribute the history. Pinned so a future change is a conscious flip."""
    con = _con()
    _seed_thread_with_ledger(con, "Alpha", prior_date="2026-07-05")
    _seed_thread_with_ledger(con, "Beta", prior_date="2026-07-06")
    slots = [slot(1, "Lead", mem=("Alpha",)), slot(2, "Card", mem=("Alpha", "Beta")),
             slot(3, "S3")]
    stories = [story(1, "Lead"), story(2, "Card", "medium"),
               story(3, "S3", "medium")]
    seed(con, slots, stories)
    page, _ = server.build_page(con)
    con.close()
    today = _today_view(page)
    assert "entry on this thread" in _article(today, "story-0")   # Alpha, on the lead
    card = _article(today, "story-1")
    assert "entry on this thread" not in card    # Alpha deduped; Beta NOT borrowed
    assert today.count("mem-dot") == 1


def test_qa_unresolvable_topic_never_crashes_never_stamps(tmp_paths):
    """A matched_memory topic with no memory row resolves to no thread — no
    stamp, no crash, page intact."""
    con = _con()
    seed(con, [slot(1, "Lead", mem=("Ghost Topic",)), slot(2, "S2")],
         [story(1, "Lead"), story(2, "S2", "medium")])
    page, _ = server.build_page(con)
    con.close()
    today = _today_view(page)
    assert "mem-dot" not in today
    assert _article_ids(today) == ["story-0", "story-1"]


def test_qa_stamp_absent_below_strips_still_tracking_line(tmp_paths):
    """'Absent below strips' is a law, not an accident: a still-tracking line
    for a HISTORIED thread carries no stamp in any form — below the strip
    register the signal degrades to absence."""
    con = _con()
    _seed_thread_with_ledger(con, "Quiet", prior_date="2026-07-05")
    s3 = slot(3, "Quiet", mem=("Quiet",))
    s3["still_tracking"] = True
    s3["still_tracking_note"] = "no movement since Jul 5"
    seed(con, [slot(1, "Lead"), slot(2, "S2"), s3],
         [story(1, "Lead"), story(2, "S2", "medium"), story(3, "Quiet", "medium")])
    page, _ = server.build_page(con)
    con.close()
    today = _today_view(page)
    still = today.split('class="still-tracking"')[1].split("</div>")[0]
    assert "Still tracking" in still
    assert "mem-dot" not in still
    assert "last covered" not in still
    assert "entry on this thread" not in still


def test_qa_stamp_markup_is_natural_case_css_uppercases(tmp_paths):
    """Screen readers must hear words: the stamp's MARKUP is natural case; the
    visual uppercase is CSS-only (.memline and .strip .smeta both carry
    text-transform: uppercase)."""
    con = _con()
    _seed_thread_with_ledger(con, "T", prior_date="2026-07-05")
    slots = [slot(1, "Lead", mem=("T",)), slot(2, "S2"), slot(3, "S3"),
             slot(4, "Strip")]
    stories = [story(1, "Lead"), story(2, "S2", "medium"), story(3, "S3", "medium"),
               story(4, "Strip", "quick")]
    seed(con, slots, stories)
    page, _ = server.build_page(con)
    con.close()
    today = _today_view(page)
    assert "entry on this thread" in today
    assert "ENTRY ON THIS THREAD" not in today
    assert "LAST COVERED" not in today
    memline_css = webui.CSS.split(".memline {")[1].split("}")[0]
    assert "text-transform: uppercase" in memline_css
    smeta_css = webui.CSS.split(".strip .smeta {")[1].split("}")[0]
    assert "text-transform: uppercase" in smeta_css


# ==========================================================================
# B. The grout heuristic — presentation-only, never drop/duplicate/reorder
# ==========================================================================

def test_qa_grid_columns_unit_properties():
    """_grid_columns invariants on degenerate input: empty in → empty out;
    assigns EXACTLY the strip indices; values only 'a'/'b'; deterministic;
    missing story fields never raise; the all-ties start goes left ('a')."""
    assert server._grid_columns({}) == {}
    gs = {0: ({}, {}, "full", "lead"),
          1: ({}, {}, "medium", "story"),
          2: ({}, {}, "medium", "story"),
          3: ({}, {}, "quick", "strip"),
          4: ({}, {}, "quick", "strip"),
          5: ({}, {}, "quick", "strip")}
    out = server._grid_columns(gs)
    assert set(out) == {3, 4, 5}                 # strips only, all of them
    assert set(out.values()) <= {"a", "b"}
    assert out == server._grid_columns(gs)       # deterministic
    # all-empty stories: lead est 8.0 > cards 8.0? left=8.0, right=4+4=8.0 —
    # exact tie goes LEFT first, then alternates by the running totals.
    assert out[3] == "a"
    # strips with no cards at all (left column can only grow)
    out2 = server._grid_columns({0: ({}, {}, "full", "lead"),
                                 1: ({}, {}, "quick", "strip"),
                                 2: ({}, {}, "quick", "strip")})
    assert set(out2) == {1, 2} and set(out2.values()) <= {"a", "b"}


def _write_entry(tiers, entry_stories):
    """A structured generation-log entry — the path where tiers and stories
    reach the renderer VERBATIM (the plain seed() path has no entry, so tiers
    fall back to the index rule and story dicts are re-parsed from narrative).
    Degenerate-story probes must ride THIS path to actually reach the grid."""
    from newslens import paths
    entry = {"ts": "2026-07-01T00:00:00Z", "date": TODAY, "status": "ok",
             "sample": False, "tiers": list(tiers), "stories": entry_stories}
    (paths.DATA_DIR / "generation_log.jsonl").write_text(
        json.dumps(entry) + "\n", encoding="utf-8")


def test_qa_grid_zero_word_stories_render_all_slots_once(tmp_paths):
    """0-word degenerate bodies REACHING THE GRID (structured-entry path):
    empty headlines/ledes must not drop, duplicate, or reorder a slot — the
    heuristic may balance badly, the page must stay whole.

    (Context, pinned during this pass: via the entry-LESS narrative-parse path,
    empty-headline stories never reach the renderer at all — pre-existing
    parser behavior, verified identical at HEAD, out of this diff's scope.)"""
    con = _con()
    slots = [slot(i, f"S{i}") for i in range(1, 8)]
    stories = [story(i, f"S{i}") for i in range(1, 8)]   # narrative needs titles
    seed(con, slots, stories)
    _write_entry(
        ["full", "medium", "medium", "quick", "quick", "quick", "quick"],
        [{"headline": "", "lede": ""} for _ in range(7)])
    page, _ = server.build_page(con)
    con.close()
    today = _today_view(page)
    ids = _article_ids(today)
    assert ids == [f"story-{i}" for i in range(7)]        # rank order, no drop
    assert len(ids) == len(set(ids))                      # no duplicate
    assert today.count('<article class="strip') == 4


def test_qa_grid_huge_outlier_estimate_never_reorders(tmp_paths):
    """A pathological estimate (one 4000-word movement on a card) skews the
    balance, never the DOM: all slots present exactly once, rank order."""
    con = _con()
    slots = [slot(i, f"S{i}") for i in range(1, 8)]
    stories = []
    for i in range(1, 8):
        st = story(i, f"S{i}", "full" if i == 1 else "medium" if i <= 3 else "quick")
        if i == 2:
            st["why_it_matters"] = "word " * 4000
        stories.append(st)
    seed(con, slots, stories)
    page, _ = server.build_page(con)
    con.close()
    today = _today_view(page)
    ids = _article_ids(today)
    assert ids == [f"story-{i}" for i in range(7)]
    # the skew shows up only as column CLASSES; with the right column huge,
    # every strip should land left — and none anywhere else
    strips = re.findall(r'<article class="strip grid-col-([ab])"', today)
    assert len(strips) == 4
    assert set(strips) == {"a"}                  # all under the (shorter) lead


def test_qa_grid_interleaved_tiers_keep_rank_dom_order(tmp_paths):
    """Tier interleave (quick between mediums — nothing in the ENTRY contract
    forbids the list shape): DOM stays rank order 1→N; roles derive per slot,
    and the page never reflows the document to group tiers. Uses the
    structured-entry path — the plain seed() path ignores story-dict tiers
    (index fallback), so only an entry `tiers` list can actually interleave."""
    con = _con()
    slots = [slot(i, f"S{i}") for i in range(1, 8)]
    stories = [story(i, f"S{i}") for i in range(1, 8)]
    seed(con, slots, stories)
    _write_entry(
        ["full", "quick", "medium", "quick", "medium", "quick", "quick"],
        [{"headline": f"S{i}", "lede": "L."} for i in range(1, 8)])
    page, _ = server.build_page(con)
    con.close()
    today = _today_view(page)
    assert _article_ids(today) == [f"story-{i}" for i in range(7)]
    assert today.count('<article class="strip') == 4
    assert today.count('<article class="story') == 2


def test_qa_grid_one_story_edition_is_a_lone_lead(tmp_paths):
    """A one-story edition renders the lead alone — no strips, no cards, no
    crash; the grid degrades to a single spanning column."""
    con = _con()
    seed(con, [slot(1, "Only")], [story(1, "Only")])
    page, _ = server.build_page(con)
    con.close()
    today = _today_view(page)
    assert _article_ids(today) == ["story-0"]
    assert "grid-lead" in today
    assert '<article class="strip' not in today
    assert '<article class="story' not in today


def test_qa_grid_lead_plus_all_quick_renders_all_strips(tmp_paths):
    """Lead + quick-only (no medium cards at all, via the entry path): every
    quick slot renders as a strip, DOM rank order — the grout survives an
    empty right column. (The greedy balance will fill the EMPTY column first —
    placement may sit beside the lead; page-safety is the law, placement is
    the principal's eye.)"""
    con = _con()
    slots = [slot(i, f"S{i}") for i in range(1, 6)]
    stories = [story(i, f"S{i}") for i in range(1, 6)]
    seed(con, slots, stories)
    _write_entry(["full", "quick", "quick", "quick", "quick"],
                 [{"headline": f"S{i}", "lede": "L."} for i in range(1, 6)])
    page, _ = server.build_page(con)
    con.close()
    today = _today_view(page)
    assert _article_ids(today) == [f"story-{i}" for i in range(5)]
    assert today.count('<article class="strip') == 4
    assert today.count('<article class="story') == 0


def test_qa_four_slot_edition_single_strip(tmp_paths):
    """4 slots = lead + two cards + ONE strip (the smallest grout)."""
    con = _con()
    slots = [slot(i, f"S{i}") for i in range(1, 5)]
    stories = [story(i, f"S{i}", "full" if i == 1 else "medium" if i <= 3
                     else "quick") for i in range(1, 5)]
    seed(con, slots, stories)
    page, _ = server.build_page(con)
    con.close()
    today = _today_view(page)
    assert _article_ids(today) == [f"story-{i}" for i in range(4)]
    assert today.count('<article class="strip') == 1


# ==========================================================================
# C. FIX-1 re-verify additions (2026-07-18, second pass) — _grid_row_spans
#    degenerates: page-safety floor under shapes the implementer's pins skip
# ==========================================================================

def _parse_span(v):
    a, b = v.split(" / ")
    return int(a), int(b)


def _assert_valid_spans(gs, rows):
    """Every slot placed; every span non-empty; within each PRESENTATION
    column the spans are monotone by rank and never overlap (the floor:
    placement may be ragged, never doubled-up or reordered)."""
    assert set(rows) == set(gs)
    for v in rows.values():
        lo, hi = _parse_span(v)
        assert lo < hi
    cols = server._grid_columns(gs)
    for side in ("a", "b"):
        seq = [i for i in sorted(gs)
               if (gs[i][3] == "lead" and side == "a")
               or (gs[i][3] == "story" and side == "b")
               or (gs[i][3] == "strip" and cols.get(i, "a") == side)]
        prev_end = None
        for i in seq:
            lo, hi = _parse_span(rows[i])
            if prev_end is not None:
                assert lo >= prev_end          # monotone, no overlap in-column
            prev_end = hi


def test_qa_row_spans_interleaved_and_all_quick_shapes_stay_valid():
    """Role interleave (quick between mediums) and the no-cards shape (lead +
    quick only) both yield valid, non-overlapping, rank-monotone spans — the
    shapes the fix pins don't touch."""
    inter = {0: ({"headline": "L", "lede": "w " * 50}, {}, "full", "lead"),
             1: ({"headline": "q1", "lede": "b."}, {}, "quick", "strip"),
             2: ({"headline": "C", "lede": "w " * 40}, {}, "medium", "story"),
             3: ({"headline": "q2", "lede": "b."}, {}, "quick", "strip"),
             4: ({"headline": "C2", "lede": "w " * 30}, {}, "medium", "story"),
             5: ({"headline": "q3", "lede": "b."}, {}, "quick", "strip")}
    _assert_valid_spans(inter, server._grid_row_spans(
        inter, server._grid_columns(inter)))
    noca = {0: ({"headline": "L", "lede": "w " * 50}, {}, "full", "lead"),
            1: ({"headline": "q1", "lede": "b."}, {}, "quick", "strip"),
            2: ({"headline": "q2", "lede": "b."}, {}, "quick", "strip"),
            3: ({"headline": "q3", "lede": "b."}, {}, "quick", "strip")}
    _assert_valid_spans(noca, server._grid_row_spans(
        noca, server._grid_columns(noca)))


def test_qa_row_spans_est_outlier_zero_words_and_determinism():
    """A 4000-word outlier card and all-zero-word stories both stay valid (est
    floors make empty spans impossible); the mapping is deterministic."""
    out = {0: ({"headline": "", "lede": ""}, {}, "full", "lead"),
           1: ({"headline": "C", "lede": "w " * 4000}, {}, "medium", "story"),
           2: ({"headline": "", "lede": ""}, {}, "medium", "story"),
           3: ({"headline": "", "lede": ""}, {}, "quick", "strip"),
           4: ({"headline": "", "lede": ""}, {}, "quick", "strip")}
    cols = server._grid_columns(out)
    rows = server._grid_row_spans(out, cols)
    _assert_valid_spans(out, rows)
    assert rows == server._grid_row_spans(out, cols)     # deterministic
    zero = {i: ({"headline": "", "lede": ""}, {}, t, r)
            for i, (t, r) in enumerate([("full", "lead"), ("medium", "story"),
                                        ("medium", "story"), ("quick", "strip"),
                                        ("quick", "strip"), ("quick", "strip"),
                                        ("quick", "strip")])}
    _assert_valid_spans(zero, server._grid_row_spans(
        zero, server._grid_columns(zero)))


def test_qa_row_spans_missing_cols_mapping_defaults_left_and_stays_valid():
    """A cols dict with no entry for a strip defaults it left ('a') — same
    default `_grid_columns` uses — and the spans stay valid (robustness to a
    future caller passing a partial mapping)."""
    gs = {0: ({"headline": "L", "lede": "w " * 30}, {}, "full", "lead"),
          1: ({"headline": "C", "lede": "w " * 30}, {}, "medium", "story"),
          2: ({"headline": "q", "lede": "b."}, {}, "quick", "strip")}
    rows = server._grid_row_spans(gs, {})                # no strip assignment
    assert set(rows) == {0, 1, 2}
    for v in rows.values():
        lo, hi = _parse_span(v)
        assert lo < hi
    lead_end = _parse_span(rows[0])[1]
    assert _parse_span(rows[2])[0] >= lead_end           # defaulted-left strip follows the lead


def test_qa_zero_word_entry_path_page_carries_gr_everywhere(tmp_paths):
    """Page-level floor: the degenerate zero-word edition (structured-entry
    path) still emits a --gr custom property on EVERY grid child and never an
    inline grid-row (the mobile-law guard holds on garbage input too)."""
    con = _con()
    slots = [slot(i, f"S{i}") for i in range(1, 8)]
    stories = [story(i, f"S{i}") for i in range(1, 8)]
    seed(con, slots, stories)
    _write_entry(
        ["full", "medium", "medium", "quick", "quick", "quick", "quick"],
        [{"headline": "", "lede": ""} for _ in range(7)])
    page, _ = server.build_page(con)
    con.close()
    today = _today_view(page)
    assert today.count('style="--gr:') == 7
    assert 'style="grid-row' not in today


def test_qa_strip_smeta_empty_slot_renders_no_empty_tag(tmp_paths):
    """A strip with nothing honest to say (no stamp, no corroboration label, no
    here-for signal) renders NO smeta tag at all — never an empty machine
    line."""
    con = _con()
    s4 = slot(4, "Bare strip")
    s4["corroboration_label"] = ""
    s4["matched_tags"] = []
    s4["matched_memory"] = []
    s4["world_impact_reason"] = ""
    slots = [slot(1, "S1"), slot(2, "S2"), slot(3, "S3"), s4]
    stories = [story(1, "S1"), story(2, "S2", "medium"), story(3, "S3", "medium"),
               story(4, "Bare strip", "quick")]
    seed(con, slots, stories)
    page, _ = server.build_page(con)
    con.close()
    strip = _article(_today_view(page), "story-3")
    # honest degradation: either no smeta at all, or a non-empty one — never
    # an empty <p class="smeta"></p>
    assert '<p class="smeta"></p>' not in strip
