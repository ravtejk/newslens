"""v8 archive redesign — implementer contract / liveness pins.

The step-back-round archive (APPROVED 2026-07-18): the calendar's marking
language is rebuilt with ZERO enclosures (the two-digit-cramping circle dies by
construction), the list-below is replaced by a day panel BESIDE the grid, month
nav renders only when reachable, and the default view reaches back through the
trailing month when that month carries editions.

Except the four labeled CARRIED-INVARIANT (born green) pins, each test here is
born red against HEAD (80dac48) — it fails on the pre-redesign render (ring on
cal-today, <a> cells, archive-list, single-month grid, no panel, no nav) and
only passes with the landed redesign (team/ENGINEERING.md R4: a new enforcement
surface is born with the red test only its wiring can flip).

Fully offline; in-process render only; the autouse sandbox (conftest) redirects
DATA_DIR/DB_PATH and a real-state tripwire guards the real checkout. Every
edition below is a FIXTURE, never the live DB.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone

import pytest

from newslens import db, labels, ranking, server, webui


def _iso(date: str, hm: str = "04:44") -> str:
    return f"{date}T{hm}:00.000Z"


def _con():
    db.migrate()
    return db.connect()


def _seed_ed(con, date, headlines, generated=None):
    """A past-dated edition whose headlines flow through the STRUCTURED path
    (generation_log stories — the shape _stories_for prefers), so the day panel
    renders every real headline, not just the lead. narrative_text stays empty:
    the log entry is authoritative for _stories_for."""
    generated = generated or _iso(date)
    slots = [{"slot": i + 1, "story_title": h, "summary": "S.", "item_ids": [],
              "matched_tags": [], "matched_memory": [], "matched_dormant": [],
              "corroboration_count": 1, "corroboration_label": "Reported by 1"}
             for i, h in enumerate(headlines)]
    stories = [{"tier": "full", "headline": h, "lede": "The lede.",
                "why_it_matters": "Effects.", "watch_for": "The vote.",
                "why_label": "Why it matters", "watch_label": "Watch for",
                "my_read": None} for h in headlines]
    con.execute(
        "INSERT INTO briefings (date, story_slots, narrative_text, generated_at)"
        " VALUES (?, ?, ?, ?)",
        (date, json.dumps(slots), "", generated))
    con.commit()
    from newslens import paths
    log = paths.DATA_DIR / "generation_log.jsonl"
    log.parent.mkdir(parents=True, exist_ok=True)
    entry = {"date": date, "variant": "A", "sample": False, "status": "ok",
             "stories": stories}
    with log.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")


def _pin_today(monkeypatch, date):
    """Pin the archive's notion of 'today' through the canonical seam
    (ranking.local_today) so today-relative day-states are deterministic."""
    monkeypatch.setattr(ranking, "local_today", lambda: date)


def _july(con):
    """The real dataset's shape: editions Jul 5 / 6 / 10, gap Jul 7-9, June empty."""
    _seed_ed(con, "2026-07-05", ["Strait fees floated", "Envoy courts allies"],
             generated=_iso("2026-07-05", "22:10"))
    _seed_ed(con, "2026-07-06", ["Funeral procession in Tehran", "OPEC+ nudges output"],
             generated=_iso("2026-07-06", "09:46"))
    _seed_ed(con, "2026-07-10",
             ["U.S.-Iran strikes close Strait of Hormuz",
              "SK Hynix sets listing record",
              "Supreme Court independence questioned"],
             generated=_iso("2026-07-10", "04:44"))


# ===========================================================================
# 1. THE MARKING LANGUAGE — zero enclosures (the cramping dies by construction)
# ===========================================================================

def test_edition_cells_are_buttons_carrying_aria_pressed(monkeypatch):
    """Only edition days are focusable — buttons, not <a> — and aria-pressed
    carries the pick non-visually. Born red: HEAD renders <a onclick=openEdition>
    on the cell with no aria-pressed."""
    _pin_today(monkeypatch, "2026-07-18")
    con = _con()
    _july(con)
    html = server._render_archive(con)
    con.close()
    # an edition cell wraps a <button>, not an anchor
    assert re.search(r'class="cal-cell cal-edition[^"]*"><button\b', html)
    assert 'aria-pressed="true"' in html      # the picked day
    assert 'aria-pressed="false"' in html     # the other edition days
    # the cell itself no longer navigates as a link
    assert re.search(r'class="cal-cell cal-edition[^"]*"><a\b', html) is None
    # gate FIX-1: the approved mockup's state-varying action hint is the name's
    # tail — the only cue these buttons populate a panel rather than navigate
    assert " — showing headlines" in html     # the picked day announces state
    assert " — show headlines" in html        # unpicked editions invite action


def test_default_day_is_scale_picked_not_ringed(monkeypatch):
    """PICKED = display-scale jump (the numeral gets wider), never an enclosure.
    The most recent edition opens picked. Born red: no cal-picked at HEAD."""
    _pin_today(monkeypatch, "2026-07-18")
    con = _con()
    _july(con)
    html = server._render_archive(con)
    con.close()
    assert "cal-picked" in html
    # the picked cell is the latest edition (Jul 10) and it is pressed
    m = re.search(r'(<span class="cal-cell cal-edition[^"]*cal-picked[^"]*">.*?</span></span>)',
                  html)
    assert m and "aria-pressed=\"true\"" in m.group(1)


def test_the_ring_is_gone_and_scale_rule_is_present():
    """The terracotta RING on cal-today (the enclosure that cramps two-digit
    dates) is deleted from the assembled CSS; the scale rule replaces it. Born
    red: HEAD's .cal-today .cal-num carries `border: 2px solid var(--terra)` +
    border-radius:50%."""
    assert "border: 2px solid var(--terra)" not in webui.CSS   # the ring, gone
    assert ".cal-picked .cal-num" in webui.CSS                 # scale is the pick
    assert ".cal-today .cal-num" in webui.CSS                  # today keeps terra


def test_today_without_edition_is_terra_and_non_interactive(monkeypatch):
    """A real daily state the old design never named: today, pre-generation,
    with no edition yet — terra numeral, NO underline, NOT a button. Born red:
    HEAD marks cal-today only inside an edition cell, so a no-edition today
    renders as a plain gap cell."""
    _pin_today(monkeypatch, "2026-07-18")   # 07-18 has no edition in _july
    con = _con()
    _july(con)
    html = server._render_archive(con)
    con.close()
    # the 18th cell is cal-today (terra), carries no button, and is not an edition
    cell = re.search(r'<span class="cal-cell cal-today">.*?</span></span>', html, re.S)
    assert cell is not None                       # today, no edition, marked terra
    assert '<span class="cal-num">18</span>' in cell.group(0)
    assert "cal-edition" not in cell.group(0)     # not an edition
    assert "<button" not in cell.group(0)         # non-interactive
    # gate FIX-2: the state is AUDIBLE, never color-alone — sr-only qualifier in-cell
    assert "today — no edition yet" in cell.group(0)


# ===========================================================================
# 2. THE DAY PANEL — beside the grid, View-briefing above every headline
# ===========================================================================

def test_day_panel_beside_grid_with_all_headlines(monkeypatch):
    """The list-below is dead; a day panel pairs the grid (arch-cols, the front
    page's 7fr/5fr skeleton). The panel lists EVERY headline of the picked
    edition (not just the lead) with the View-briefing button ABOVE them. Born
    red: HEAD emits archive-list + only the lead per edition, no panel."""
    _pin_today(monkeypatch, "2026-07-18")
    con = _con()
    _july(con)
    html = server._render_archive(con)
    con.close()
    assert "archive-list" not in html            # the list stays dead
    assert 'class="arch-cols"' in html           # grid + panel skeleton
    assert 'class="day-panel"' in html
    assert 'class="dp-headlines"' in html
    # the picked edition (Jul 10) shows all THREE of its headlines
    for h in ("U.S.-Iran strikes close Strait of Hormuz",
              "SK Hynix sets listing record",
              "Supreme Court independence questioned"):
        assert h in html
    # View-briefing sits ABOVE the headlines and reuses the read-logging open
    dp = html[html.index('class="day-panel"'):]
    assert dp.index("dp-btn") < dp.index("dp-headlines")
    assert "openEdition('2026-07-10'" in dp and 'href="/?date=2026-07-10"' in dp


def test_panels_exist_for_every_edition_hidden_until_picked(monkeypatch):
    """Picking a day repopulates the panel with no fetch: a data-date panel per
    edition, all but the picked one hidden, inside the aria-live stack. Born red:
    no panels at HEAD."""
    _pin_today(monkeypatch, "2026-07-18")
    con = _con()
    _july(con)
    html = server._render_archive(con)
    con.close()
    assert 'aria-live="polite"' in html
    for d in ("2026-07-05", "2026-07-06", "2026-07-10"):
        assert f'data-date="{d}"' in html
    # a panel per edition; all but the picked (latest) are hidden
    assert html.count('class="day-panel"') == 3
    assert html.count('class="day-panel" hidden') == 2
    # the picked (latest) panel is the one that is NOT hidden
    assert 'class="day-panel" hidden data-date="2026-07-10"' not in html
    assert 'class="day-panel" data-date="2026-07-10"' in html


def test_panel_today_tag_when_picked_day_is_today(monkeypatch):
    """When the picked day is today, the panel stamp carries the TODAY tag (the
    only place the tag lives now the list is gone)."""
    _pin_today(monkeypatch, "2026-07-10")   # make the latest edition == today
    con = _con()
    _july(con)
    html = server._render_archive(con)
    con.close()
    assert labels.ARCHIVE_TODAY_TAG in html


# ===========================================================================
# 3. MONTH DEPTH — reach back through the trailing month when it has editions
# ===========================================================================

def test_trailing_month_renders_when_it_has_editions(monkeypatch):
    """The principal's build-time rider: the default view reaches back through
    the trailing month. Interpretation (flagged in the report): render the
    latest-edition month AND the immediately-preceding month WHEN that month
    carries editions. Born red: HEAD renders the latest month only."""
    _pin_today(monkeypatch, "2026-07-18")
    con = _con()
    _seed_ed(con, "2026-06-28", ["June lead A"], generated=_iso("2026-06-28"))
    _seed_ed(con, "2026-06-29", ["June lead B"], generated=_iso("2026-06-29"))
    _seed_ed(con, "2026-07-02", ["July lead"], generated=_iso("2026-07-02"))
    html = server._render_archive(con)
    con.close()
    assert 'class="month-title">July' in html    # anchor month
    assert 'class="month-title">June' in html    # the trailing month, VISIBLE
    # both months' editions are pickable
    assert 'data-date="2026-06-28"' in html and 'data-date="2026-07-02"' in html


def test_trailing_month_absent_when_empty_matches_mockup(monkeypatch):
    """CARRIED-INVARIANT (born green, labeled per R4): when the trailing month
    has NO editions (the real July-only dataset), only the anchor month renders
    — no empty June grid as noise. Guards against over-eager two-month render."""
    _pin_today(monkeypatch, "2026-07-18")
    con = _con()
    _july(con)                                   # June is empty in this dataset
    html = server._render_archive(con)
    con.close()
    assert 'class="month-title">July' in html
    assert 'class="month-title">June' not in html


# ===========================================================================
# 4. MONTH NAV — rendered only when reachable; functional when present
# ===========================================================================

def test_no_month_nav_for_single_window_dataset(monkeypatch):
    """CARRIED-INVARIANT (born green, labeled): affordance absence — with every
    edition inside the default window and nothing older/newer, no nav link
    renders (never a disabled link). This is the mockup's in-frame state."""
    _pin_today(monkeypatch, "2026-07-18")
    con = _con()
    _july(con)
    html = server._render_archive(con)
    con.close()
    assert "month-nav-link" not in html


def test_month_nav_present_and_functional_when_older_reachable(monkeypatch):
    """When an edition-bearing month sits before the default window, the 'older'
    nav link renders AND its target renders through _archive_body. Born red: no
    nav and no _archive_body at HEAD."""
    _pin_today(monkeypatch, "2026-07-18")
    con = _con()
    _seed_ed(con, "2026-05-20", ["May lead"], generated=_iso("2026-05-20"))
    _seed_ed(con, "2026-06-28", ["June lead"], generated=_iso("2026-06-28"))
    _seed_ed(con, "2026-07-02", ["July lead"], generated=_iso("2026-07-02"))
    # default window = July + June (trailing has editions); May is older-reachable
    html = server._render_archive(con)
    assert "month-nav-link" in html
    assert "navMonth('2026-05'" in html          # older-reachable target
    # the target window renders May as its anchor
    body = server._archive_body(con, "2026-05")
    con.close()
    assert 'class="month-title">May' in body


def test_archive_body_route_serves_month_fragment(monkeypatch):
    """The /archive?am= fetch pattern (mirrors /edition): the fragment carries
    its own grid + panels, no page shell. Born red: no _archive_body at HEAD."""
    _pin_today(monkeypatch, "2026-07-18")
    con = _con()
    _july(con)
    body = server._archive_body(con)
    con.close()
    assert "<!DOCTYPE html>" not in body         # a fragment, not a page
    assert 'class="arch-cols"' in body
    assert 'class="day-panel"' in body


# ===========================================================================
# 5. CARRIED INVARIANTS — the empty state and the count-line death survive
# ===========================================================================

def test_empty_state_unchanged(monkeypatch):
    """CARRIED-INVARIANT (born green): zero editions still renders the honest
    empty note and no calendar grid."""
    _pin_today(monkeypatch, "2026-07-18")
    con = _con()
    html = server._render_archive(con)
    con.close()
    assert labels.ARCHIVE_EMPTY in html
    assert 'class="cal-grid"' not in html


def test_count_line_stays_dead(monkeypatch):
    """CARRIED-INVARIANT (born green): v8-M1 item 8 killed the 'N editions this
    month' line; the redesign does not resurrect it."""
    _pin_today(monkeypatch, "2026-07-18")
    con = _con()
    _july(con)
    html = server._render_archive(con)
    con.close()
    assert "editions this month" not in html
