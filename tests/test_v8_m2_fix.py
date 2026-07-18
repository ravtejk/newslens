"""v8-M2 gate FIX loop (2026-07-18) — born-red pins for the gate's BLOCKING
FIX-1 (server-computed row placement squaring the newspaper front) and the
FIX-2 rider (stamp date normalization on the honesty surface).

Born-red baseline = the v8-M2 batch WITHOUT these fixes (the grid+stamp code is
itself uncommitted, so HEAD has neither; the pre-fix run below is the batch with
the three FIX edits reversed). Each pin's HEAD-run status is recorded in the
implementer report per the NEW PROOF LAW:

  FIX-1 pins (all BORN-RED — the row mechanism did not exist pre-fix):
    * _grid_row_spans is a NEW function (AttributeError pre-fix).
    * grid children carry the --gr custom property (absent pre-fix).
    * webui.CSS retired the fixed `grid-row: 1 / span 2` for `grid-row:
      var(--gr, auto)` (the fixed span was present pre-fix).
  FIX-2 pin (BORN-RED — the contract's guard input):
    * a timestamped edition_date no longer inflates the ordinal / leaks into
      last_covered (pre-fix yields 3 and the raw timestamp).

Offline by construction (autouse conftest sandbox + loopback guard); $0 — no
test here reaches an LLM seat.
"""
from __future__ import annotations

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


def _grid_stories(n_strips: int):
    """A lead + two medium cards + n_strips quick strips, as the renderer's
    internal {i: (st, slot, tier, role)} shape."""
    gs = {}
    gs[0] = ({"headline": "Lead story with a long headline here",
              "lede": "word " * 90}, {}, "full", "lead")
    gs[1] = ({"headline": "Card two", "lede": "word " * 60}, {}, "medium", "story")
    gs[2] = ({"headline": "Card three runs long", "lede": "word " * 80},
             {}, "medium", "story")
    for k in range(n_strips):
        gs[3 + k] = ({"headline": f"Strip {k}", "lede": "brief."},
                     {}, "quick", "strip")
    return gs


# ==========================================================================
# FIX-1 — the server-computed row placement (the mockup mechanism, generalized)
# ==========================================================================

def test_fix1_grid_row_spans_is_a_real_function_with_valid_spans():
    """BORN-RED: server._grid_row_spans exists (AttributeError pre-fix) and
    returns a valid `<start> / <end>` grid-row for EVERY slot — start < end, no
    empty span, every index placed."""
    gs = _grid_stories(4)
    cols = server._grid_columns(gs)
    rows = server._grid_row_spans(gs, cols)
    assert set(rows) == set(gs)                       # every slot placed
    for v in rows.values():
        m = re.match(r"^(\d+) / (\d+)$", v)
        assert m, f"bad grid-row {v!r}"
        assert int(m.group(1)) < int(m.group(2))      # non-empty span


def test_fix1_lead_span_computed_and_tall_card_spans_down_beside_strips():
    """BORN-RED: the mockup's mechanism, generalized — the lead starts at row
    line 1 with a COMPUTED span (not the retired fixed 1/span 2), the last
    right-column card (#3) spans DOWN past the lead's end line (the s3 trick), and
    the left strips start at or after the lead ends (no void wedged above them)."""
    gs = _grid_stories(4)
    cols = server._grid_columns(gs)
    rows = server._grid_row_spans(gs, cols)
    lead_start, lead_end = (int(x) for x in rows[0].split(" / "))
    assert lead_start == 1                             # lead heads the grid
    card3_end = int(rows[2].split(" / ")[1])
    assert card3_end > lead_end                        # #3 spans down past the lead
    # left-column strips (grid-col a) begin no earlier than the lead's end line
    left_strip_starts = [int(rows[i].split(" / ")[0])
                         for i in gs if gs[i][3] == "strip" and cols.get(i) == "a"]
    assert left_strip_starts and min(left_strip_starts) >= lead_end


def test_fix1_grid_children_carry_gr_custom_property_not_inline_grid_row():
    """BORN-RED (wiring proof): the computed row placement reaches the DOM as the
    --gr CUSTOM PROPERTY on each grid child — never an inline `grid-row`, which
    would beat the ≤900px media-query reset and break the mobile stack."""
    con = _con()
    slots = [slot(i, f"S{i}") for i in range(1, 8)]
    stories = [story(i, f"S{i}", "full" if i == 1 else "medium" if i <= 3
                     else "quick") for i in range(1, 8)]
    seed(con, slots, stories)
    page, _ = server.build_page(con)
    con.close()
    today = _today_view(page)
    lead = today.split('<article class="lead')[1].split(">")[0]
    assert "--gr:" in lead                             # the lead carries a row span
    assert "grid-row:" not in lead                     # ...as a custom prop, not inline grid-row
    assert today.count('style="--gr:') == 7            # all 7 grid children (lead+cards+strips)
    assert 'style="grid-row' not in page               # never inline grid-row (would beat the @media reset)


def test_fix1_css_retired_fixed_lead_span_for_var_gr():
    """BORN-RED: webui.CSS no longer pins the lead to `grid-row: 1 / span 2`;
    the three placement classes read `grid-row: var(--gr, auto)` and the ≤900px
    block still resets grid-row to auto (so the custom property is inert on the
    single-column stack)."""
    css = webui.CSS
    # the fixed lead span is retired as a LIVE RULE (the phrase survives only in
    # the explanatory comment that records what was removed)
    assert ".grid-lead { grid-column: 1; grid-row: 1 / span 2; }" not in css
    assert css.count("grid-row: var(--gr, auto)") == 3  # lead + col-a + col-b
    mobile = css.split("@media (max-width: 900px)")[1]
    assert "grid-row: auto" in mobile                   # the mobile reset holds


def test_fix1_degenerate_floor_lone_lead_and_empty_stay_safe():
    """The degenerate floor: an empty grid yields {}; a lone lead still gets a
    valid single-band span (page-safety, never a crash)."""
    assert server._grid_row_spans({}, {}) == {}
    lone = {0: ({"headline": "Only", "lede": "x"}, {}, "full", "lead")}
    rows = server._grid_row_spans(lone, server._grid_columns(lone))
    assert re.match(r"^1 / \d+$", rows[0])             # spans from line 1


# ==========================================================================
# FIX-2 — stamp date normalization on the honesty surface
# ==========================================================================

def test_fix2_timestamped_edition_date_does_not_inflate_ordinal(tmp_paths):
    """BORN-RED (the contract's guard input): a bare date and its timestamped
    twin are ONE prior edition, and last_covered is the bare date. Pre-fix
    (raw, un-normalized) counts them as two distinct priors → '3rd', and leaks
    the raw 'YYYY-MM-DDT...' into last_covered. The stamp is an honesty surface;
    the ordinal must count editions that exist, not string forms of a date."""
    con = _con()
    now = "2026-07-01T00:00:00.000Z"
    tid = con.execute(
        "INSERT INTO memory (topic, status, status_changed_at, created_at,"
        " updated_at) VALUES ('T', 'active', ?, ?, ?)", (now, now, now)).lastrowid
    for d in ("2026-07-05", "2026-07-05T08:00:00Z"):
        con.execute(
            "INSERT INTO thread_deltas (thread_id, edition_date, verdict,"
            " what_happened, significance, cites_json, slot) VALUES"
            " (?, ?, 'advances', 'x', 'y', '[]', NULL)", (tid, d))
    con.commit()
    stamp = memory_core.today_memory_stamp(con, tid, "2026-07-14")
    con.close()
    assert stamp == (2, "2026-07-05")                  # not (3, '...T08:00:00Z')


def test_fix2_bare_date_path_unchanged_carried_invariant(tmp_paths):
    """BORN-GREEN (carried invariant): the normalization is a no-op on the real
    single-writer shape (bare YYYY-MM-DD rows) — three distinct bare priors is
    still '4th entry · last covered' the latest. Guards against the fix quietly
    changing the conforming path."""
    con = _con()
    now = "2026-07-01T00:00:00.000Z"
    tid = con.execute(
        "INSERT INTO memory (topic, status, status_changed_at, created_at,"
        " updated_at) VALUES ('T', 'active', ?, ?, ?)", (now, now, now)).lastrowid
    for d in ("2026-07-05", "2026-07-06", "2026-07-10"):
        con.execute(
            "INSERT INTO thread_deltas (thread_id, edition_date, verdict,"
            " what_happened, significance, cites_json, slot) VALUES"
            " (?, ?, 'advances', 'x', 'y', '[]', NULL)", (tid, d))
    con.commit()
    stamp = memory_core.today_memory_stamp(con, tid, "2026-07-14")
    con.close()
    assert stamp == (4, "2026-07-10")
