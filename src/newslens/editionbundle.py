"""THE EDITION BUNDLE — an edition frozen into one self-contained artifact.

NL-163 Stage-A, milestone 1. Chartered by the engineering adjudication of
2026-08-29 (Q1 bundle spec, Q7 read-pure) on the principal's "build that whole
thing out and make sure it works", after the phone-mockup gate passed.

WHAT PROBLEM THIS ENDS
----------------------
The served page is a LIVE FIVE-SOURCE VIEW (the code-truth bracket, DECISIONS
2026-08-29 ①): the briefings row, the generation-log entry's stories/tiers,
live memory/follow queries, the analysis briefs, and sources.yaml — all read at
REQUEST time. That is right for a Mac surface sitting next to the database. It
is wrong for a phone: unfollow a thread in the evening and last Tuesday's
edition silently re-renders as though you never followed it. The edition of
record would keep changing after it was published.

So the bundle is minted AT THE PUBLISH SEAM, in the generate's own process,
while every one of those five sources is in hand — and what it writes is the
rendered document, not a recipe for rendering one later. Reason lines, follow
state, continuity stamps, deep views: all resolved once, at publish, and never
again. FROZEN BY CONSTRUCTION, not by discipline.

THE THREE THINGS THIS MODULE REFUSES TO DO
------------------------------------------
1. It never re-scans `generation_log.jsonl`. The caller hands it the entry it
   just logged; `server._log_entry_for` (the file read) is bypassed entirely.
   A file read would be a second source of truth for a fact we already hold.
2. It never RECONSTRUCTS a bundle for a back edition. Rendering yesterday's
   edition today would render TODAY's follow state and stamp it "frozen at
   publish" — a lie with a timestamp on it. `newslens bundle --date <old>`
   refuses and says why (the no-seeding ruling; day-one archive is empty, at
   his eye as consolidated item ④).
3. It never re-derives a renderer. Every story, stamp, reason line and deep
   view comes from `server`'s own builders — the same code that draws his Mac
   surface — under the read-pure seam (`server.read_pure_render`). A second
   renderer for the same edition is how two surfaces start disagreeing about
   what was published.

READ-PURE (adjudication Q7, unanimous). The phone gets no interactive verbs:
a verb would mutate the Mac-bound memory DB through a hosted write path, which
is the general inbound command channel Stage B's vocabulary pin forbids. Follow
STATE still renders — as furniture. See the seam's own note in server.py.

STDLIB ONLY. The product tree's three-dependency law holds here; the hosted
service is a separate deployable and is not this module's business.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from . import paths, webui

BUNDLE_VERSION = 1

# The local artifact. `<date>.phone.json`, beside `<date>.md` in data/briefings/
# — one directory per the write_artifact convention (generate.write_artifact),
# and gitignored by `data/` like every other piece of reader state.
BRIEFINGS_DIR_NAME = "briefings"
ARTIFACT_SUFFIX = ".phone.json"


class BundleError(RuntimeError):
    """A bundle could not be built or read. At the mint site this is CONTAINED
    (the edition is already published and is never put at risk by it); at the
    CLI it is printed and returns non-zero."""


# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

def bundle_dir() -> Path:
    return paths.DATA_DIR / BRIEFINGS_DIR_NAME


def bundle_path(date: str) -> Path:
    return bundle_dir() / f"{date}{ARTIFACT_SUFFIX}"


def available_dates() -> List[str]:
    """Every date that HAS a bundle on disk, oldest first. This is the archive
    the phone surface can honestly offer — never the list of editions, because
    an edition without a bundle has no frozen document and will not get one."""
    try:
        names = os.listdir(bundle_dir())
    except OSError:
        return []
    out = []
    for name in names:
        if name.endswith(ARTIFACT_SUFFIX):
            stem = name[:-len(ARTIFACT_SUFFIX)]
            if re.fullmatch(r"\d{4}-\d{2}-\d{2}", stem):
                out.append(stem)
    return sorted(out)


# ---------------------------------------------------------------------------
# Tokens and the ratified phone scale — CONSUMED from webui, never copied
# ---------------------------------------------------------------------------
#
# DESIGN_SYSTEM.md's rule is that tokens live in variables and two files
# declaring the same hexes is how they drift; the same reasoning is why the
# phone TYPE SCALE is extracted rather than retyped. The addendum §3 ratifies
# webui.py's mobile pass AS the phone scale — so the honest way to carry it is
# to read it out of the shipped stylesheet at mint time. Re-pin the scale in
# webui.py and this document follows on the next edition, with no second table
# to remember.

_MOBILE_QUERY = "@media (max-width: 900px)"


def _extract_block(css: str, opener: str) -> str:
    """The body of the brace-balanced block introduced by `opener`.

    Brace-counting, not a regex, because the mobile pass contains nested rules
    and a lazy `.*?}` would stop at the first inner close. Raises rather than
    returning a plausible-looking empty string: a silently empty phone pass
    would ship a desktop-scaled document that LOOKS finished.

    SINGLE OCCURRENCE IS REQUIRED, and the check is loud for the same reason.
    `find` takes the FIRST hit and then scans to the next `{` — so a mere
    COMMENT naming the opener above the real rule (or a second rule matching
    it) silently anchors the scan in the wrong block and returns a
    plausible-looking value from somewhere else entirely. Measured shape: a
    comment naming `body.dark` above the real inversion yields `#111111` where
    the rule says `#222222`, with no error. A wrong status-bar hex that nothing
    complains about is exactly what the derive-never-retype law exists to
    prevent, and webui's dark register is OPEN design work (addendum §10), so
    edits to these very blocks are named-coming."""
    start = css.find(opener)
    if start < 0:
        raise BundleError(
            f"webui.CSS no longer contains {opener!r} — the phone surface reads "
            "its ratified scale out of that block (DIRECTION-phone-addendum §3). "
            "If the query moved, move this reader with it")
    elif css.count(opener) > 1:
        raise BundleError(
            f"{opener!r} occurs {css.count(opener)} times in webui.CSS — this "
            "reader anchors on the FIRST occurrence and would extract the "
            "wrong block silently (a comment naming the opener above the real "
            "rule is enough). Make the opener unique, or teach this reader "
            "which occurrence is the rule")
    i = css.index("{", start)
    depth = 0
    for j in range(i, len(css)):
        if css[j] == "{":
            depth += 1
        elif css[j] == "}":
            depth -= 1
            if depth == 0:
                return css[i + 1:j]
    raise BundleError(f"unbalanced braces after {opener!r} in webui.CSS")


def phone_pass_rules() -> str:
    """The ratified phone pass (webui.py's ~390px mobile block), lifted out of
    its media query so it applies AT EVERY WIDTH in the bundle.

    Why unconditional: the bundle is a PHONE artifact. The addendum §2 lists
    what must not survive the shrink — the 7fr/5fr today-grid and the rectangle
    law — and those are exactly the rules this block resets. Leaving them behind
    a `max-width: 900px` query would mean a desk preview of the same file
    rendered a layout that is not the product."""
    return _extract_block(webui.CSS, _MOBILE_QUERY)


def _token_value(css: str, block_opener: str, name: str) -> str:
    """One custom-property value out of a named CSS block (`:root` for the light
    palette, `body.dark` for the inversion). Used ONLY for the two `theme-color`
    metas, which are attributes and cannot take a var()."""
    body = _extract_block(css, block_opener)
    m = re.search(re.escape(name) + r"\s*:\s*(#[0-9A-Fa-f]{3,8})\s*;", body)
    if not m:
        raise BundleError(
            f"{name} not found in the {block_opener!r} block — the status-bar "
            "colour is derived from the palette, never retyped beside it")
    return m.group(1)


def theme_colors() -> Tuple[str, str]:
    """(light, dark) status-bar colours, DERIVED from the shipped palette.

    Addendum §5 pairs `meta theme-color` with the palette. A meta attribute
    cannot hold a `var()`, so this is the one place a hex has to be spelled —
    and it is READ OUT of webui's own `:root` / `body.dark` rather than typed
    next to them, which is the same law TOKENS lives under."""
    return (_token_value(webui.TOKENS, ":root", "--paper"),
            _token_value(webui.CSS, "body.dark", "--paper"))


# ---------------------------------------------------------------------------
# The phone delta — the only CSS this module AUTHORS
# ---------------------------------------------------------------------------
#
# Everything above is consumed. What follows is the standalone-frame law, which
# has no shipped implementation to consume because the Mac surface has no
# notch, no home indicator, and a browser back button.

PHONE_CSS = """
/* ==========================================================================
   NL-163 Stage-A — the standalone phone delta (DIRECTION-phone-addendum).
   Everything ABOVE this comment is webui's shipped stylesheet plus its own
   ratified phone pass, lifted unconditional (§2/§3). Below is only what the
   standalone frame adds: safe areas, the offline stamp, the read-pure quieting
   of surfaces whose verbs are withheld.
   ========================================================================== */

/* §5 SAFE AREAS + THE READING COLUMN, on ONE container.

   The gutters ABSORB env(safe-area-inset-left/right), the masthead's top
   padding absorbs inset-top, and the document pads for the home indicator, so
   no content and no tap target ever sits under the notch or the bar.

   THE COLUMN LIVES ON THE VIEW, NOT ON `.page`, and this was MEASURED. On the
   Mac, `.page` is the reading column and the width lives there — but only SOME
   of an edition is wrapped in one. The masthead is a `.page`; the story grid
   and the trust footer are not. Capping `.page` therefore split the document
   in two the moment the window was wider than the column: at 1400px the
   masthead was 430px and centred while the today-grid ran 1348px full-bleed,
   which is the first thing he would have seen opening the artifact on a desk.
   Putting the cap on the VIEW makes one column of everything inside it at
   every width, and every `.page` within is made transparent below. */
#view-today, section[id^="view-deep-"] {
  max-width: 430px; margin-left: auto; margin-right: auto;
  padding-left: calc(1.15rem + env(safe-area-inset-left));
  padding-right: calc(1.15rem + env(safe-area-inset-right)); }
#view-today .page, section[id^="view-deep-"] .page {
  max-width: none; margin-left: 0; margin-right: 0;
  padding-left: 0; padding-right: 0; }
.masthead { padding-top: calc(1.6rem + env(safe-area-inset-top)); }
body { padding-bottom: calc(2rem + env(safe-area-inset-bottom)); }
/* A deep view has no masthead, so nothing else absorbs inset-top for it — and
   the first thing in it is the back link, which under the no-chrome law is the
   ONLY way out. Measured before this rule existed: the link's box started 32px
   down, comfortably inside an iPhone notch inset. The shipped 2rem top margin
   on .deep-back is folded into this padding so the optical position does not
   move on a device with no inset. */
section[id^="view-deep-"] { padding-top: env(safe-area-inset-top); }

/* F-5 COARSE-POINTER TAP BOX for the back link — the addendum §1 promotes the
   coarse-pointer targets from "the media-query case" to THE case on a phone,
   and the shipped @media (pointer: coarse) block only reaches .deck-follow and
   the .fl-alts acts (webui.py:348-352) because on the Mac the back link is a
   mouse target. Measured height before: 22.8px. The idiom is the shipped one —
   padding out, equal negative margin back — so the box grows without the text
   moving. 22.8 + 2*11.2 = 45.2px, over the 2.5.5 44px floor. */
.deep-back { padding: 0.7rem 0.5rem; margin: calc(2rem - 0.7rem) -0.5rem 0; }

/* §7 THE OFFLINE / STALENESS STAMP — one machine-register line that EXTENDS
   the dispatch-strip block (same font, size, colour; its own line). Never a
   banner, never sticky, never danger-coloured. M1 renders the SLOT and leaves
   it empty; the service worker fills it in M2. `hidden` is honoured explicitly
   because the shipped reset gives <p> a display. */
.offline-line { font-family: var(--font-mono); font-size: 0.74rem;
  line-height: 1.6; color: var(--ink-soft); margin: 0.2rem 0 0; }
.offline-line strong { font-weight: 700; }
.offline-line[hidden] { display: none; }

/* §4 no-chrome: today is the single top-level view in a bundle, so the section
   line carries one destination and it is the current one — a live word, not a
   link to nowhere. Same geometry as the shipped nav so the ceremony's closing
   rule is unmoved. */
.section-line .section-current { color: var(--ink); font-weight: 700;
  display: inline-block; padding: 0.35rem 0; margin-right: 1.1rem; }

/* NO READ-PURE QUIETING RULE IS NEEDED, and the absence is deliberate. The
   obvious worry is that a deck holding only the withheld verb collapses to an
   empty ruled row and leaves a stray hairline across the page. It cannot: the
   shipped renderer emits the container only when it has something to put in it
   (`if deck_bits:` — server.py, the deck emission), and the deep view's follow
   mount returns "" rather than an empty <div>. A `.deck:empty` rule would be
   dead CSS claiming to handle a case the code cannot produce, which is worse
   than no rule at all — it would read as a fact about the renderer that is not
   true. (Measured on a real edition: 2 decks, 0 empty.)

   §11 MOTION. No page-transition animation, no pull-to-refresh theatre, no
   skeleton shimmer — first paint is content. The shipped smooth scroll is the
   one carried behaviour and it is already behind prefers-reduced-motion; the
   runtime forces it OFF for view switches specifically, because a view change
   is not a scroll the reader asked for (see jumpTo in the bundle runtime).

   SCROLL ANCHORING OFF, and this one was MEASURED, not anticipated. Switching
   views changes the document height twice in one frame (today hides, the deep
   view shows). With `overflow-anchor: auto` the browser treats that as content
   shifting under a reader and RESTORES the old offset — which silently beat
   the runtime's own scroll-to-top: opening a deep view from 1400px down left
   the reader 1400px down inside a different document, on a paragraph they had
   never seen. Receipt: sampled scrollY 1400 at +80ms and +780ms after the
   open, with the scroll call executing. Anchoring exists for infinite feeds;
   this document is a fixed set of views and never needs it. */
html { overflow-anchor: none; }
"""


# ---------------------------------------------------------------------------
# The bundle's own JavaScript — READ-ONLY, and small enough to read in one sit
# ---------------------------------------------------------------------------
#
# webui.JS is explicitly NOT reused (adjudication Q1): it is 1,500 lines of
# follow-state machine, POST helpers and a generation poller, none of which has
# any meaning in a document with no server behind it. What is reused instead is
# the FUNCTION NAMES the server-rendered markup calls, so the markup needs no
# rewriting — four of them, and every one is a read.

BUNDLE_JS = """
/* NL-163 Stage-A bundle runtime. Four behaviours, all reads:
   view switching (today <-> its deep views), back with scroll restored,
   the three-state dark override, and the trust footer's disclosure.
   There is no fetch, no XHR, no storage of anything but the theme choice, and
   no /api path anywhere in this file — the document is the whole product. */
(function () {
  'use strict';

  /* ---- view switching + THE ON-PAGE BACK LAW (addendum §6) --------------
     Standalone mode has no browser back button, so every view must be
     exitable by an element ON the page. Deep views open with the back link;
     this is the other half of it — the scroll position the reader left. */
  /* null, NOT 0 — the sentinel has to mean "no open has happened yet", and 0
     is a real scroll position a reader can legitimately be at. Seeded at 0,
     a deep view opened from exactly the top restored via the story anchor
     instead of the exact position, and scrollIntoView is outside jump()'s
     behaviour-off path, so that fallback ANIMATED (measured: settle 0 → open →
     close landed 277, story-0's top, where §6 wants 0). Reachable whenever a
     door is visible without scrolling — tall desk windows, short leads. */
  var returnScroll = null;
  var lastStoryAnchor = null;

  /* An IMMEDIATE scroll, never an animated one. A view switch is not a scroll
     the reader asked for, so the shipped `scroll-behavior: smooth` is turned
     off for the duration of the jump and restored after — rather than relying
     on `behavior: 'instant'`, which older Safari ignores, and ignoring it
     there would animate a 1400px slide through content the reader is leaving.

     SYNCHRONOUS FIRST, then one re-assert on the next frame. The re-assert is
     there because the class swap this follows resizes the document and a late
     relayout can land the offset somewhere else; it is NOT the primary,
     because requestAnimationFrame does not fire in a hidden document and a
     scroll law that only holds while the tab is foreground is not a law
     (measured: rAF did not fire at all in the harness this was verified in). */
  function jump(y) {
    var de = document.documentElement;
    var prev = de.style.scrollBehavior;
    de.style.scrollBehavior = 'auto';
    /* THE FLUSH IS LOAD-BEARING, and it cost a debugging session to find.
       Writing the inline style and reverting it in the same tick means the
       computed value never changes — no style recalc happens in between — so
       the scroll below ran under the stylesheet's `smooth` after all and
       ANIMATED. Reading a layout property forces the recalc first. Symptom
       when it is missing: opening a deep view from 1400px down sampled 1389 at
       +50ms and 341 at +350ms, i.e. the reader watched the old view's content
       slide past on the way into a new one. */
    void de.offsetHeight;
    window.scrollTo(0, y);
    de.style.scrollBehavior = prev;
  }

  function jumpTo(y) {
    jump(y);
    /* One re-assert on the next frame, for a late relayout. Never the
       primary: rAF does not fire in a hidden document, and a scroll law that
       only holds while the tab is foreground is not a law. */
    try { requestAnimationFrame(function () { jump(y); }); } catch (e) {}
  }

  function activate(id) {
    var views = document.querySelectorAll('.view');
    for (var i = 0; i < views.length; i++) views[i].classList.remove('active');
    var el = document.getElementById(id);
    if (el) el.classList.add('active');
    return !!el;
  }

  window.openDeepView = function (storyId, e, returnId) {
    if (e) e.preventDefault();
    returnScroll = window.scrollY || window.pageYOffset || 0;
    lastStoryAnchor = storyId;
    if (activate('view-deep-' + storyId)) jumpTo(0);
    return false;
  };

  window.closeDeepView = function (e) {
    if (e) e.preventDefault();
    activate('view-today');
    /* Exact position first (what "restored" means to a reader mid-column);
       the originating story is the fallback for a first-open-with-no-history
       case and for a layout that reflowed under a Dynamic Type change while
       the deep view was open. */
    if (returnScroll !== null) {
      jumpTo(returnScroll);
    } else if (lastStoryAnchor) {
      var target = document.getElementById(lastStoryAnchor);
      if (target) target.scrollIntoView({ block: 'start' });
    }
    lastStoryAnchor = null;
    returnScroll = null;
    return false;
  };

  /* The trust footer's provenance disclosure — element-relative, exactly as
     the shipped surface does it, so one footer's toggle never opens another's. */
  window.toggleFooterDisclosure = function (btn) {
    var detail = btn.parentNode.querySelector('.footer-detail');
    var expanded = btn.getAttribute('aria-expanded') === 'true';
    btn.setAttribute('aria-expanded', String(!expanded));
    if (detail) detail.classList.toggle('open', !expanded);
  };
})();
"""

# The boot script. Runs as the FIRST child of <body> — before the edition is
# painted — so the reader never sees a light flash resolve into dark, and never
# sees the type re-flow one beat after arriving.
BOOT_JS = """
/* NL-163 boot — theme + Dynamic Type, before first paint. */
(function () {
  'use strict';
  var KEY = 'newslens-theme';   /* 'system' | 'light' | 'dark' */

  /* §10 DARK MODE. prefers-color-scheme is the DEFAULT; the manual control is
     a THREE-STATE OVERRIDE persisted per device, amending (not removing) the
     shipped two-state localStorage toggle. The palette itself is the shipped
     mechanical inversion — this only decides whether `body.dark` is on, so
     there is no second hex table anywhere in this document. */
  function preferred() {
    try {
      return window.matchMedia('(prefers-color-scheme: dark)').matches;
    } catch (e) { return false; }
  }
  function choice() {
    try { return localStorage.getItem(KEY) || 'system'; } catch (e) { return 'system'; }
  }
  function apply() {
    var c = choice();
    var dark = c === 'dark' || (c === 'system' && preferred());
    document.body.classList.toggle('dark', dark);
  }
  window.setTheme = function (mode) {
    /* 'system' | 'light' | 'dark'. Exposed for the M2 shell's control; the
       bundle itself renders no toggle (no-chrome law, §4). */
    try { localStorage.setItem(KEY, mode); } catch (e) {}
    apply();
  };
  apply();
  try {
    window.matchMedia('(prefers-color-scheme: dark)')
      .addEventListener('change', apply);
  } catch (e) {}

  /* §4 DYNAMIC TYPE. A standalone web app honours the iOS text-size setting
     only if something reads it: `font: -apple-system-body` on a probe resolves
     to the user's chosen body size, and seeding the root from it scales the
     whole rem tree with the OS. Everything else in this document is already
     rem, so nothing else has to know. Left alone on any platform that returns
     an implausible value — a wrong seed is worse than the default.

     GATED ON A TOUCH DEVICE, and the gate is not paranoia. The ruled mechanism
     is iOS DYNAMIC TYPE — a reader's text-size preference. Desktop Safari
     resolves the same shorthand to its plain system body size (13px measured),
     which is NOT a preference and would seed the root at 13/16 = an 81%-scale
     document: `newslens bundle --open` on the desk would render a shrunken
     edition that is not the product. The approved mockup was passed unseeded
     in a desktop browser, so unseeded IS the approved desk state. Firefox and
     Chromium reject the shorthand outright and were never affected — Safari is
     the case this closes. `navigator.maxTouchPoints` is undefined on old
     engines → falsy → no seed, which is the safe default in the same
     direction as the band. */
  try {
    var probe = document.createElement('div');
    probe.style.cssText = 'font: -apple-system-body; position:absolute;' +
      'visibility:hidden; height:0; width:0; overflow:hidden;';
    document.body.appendChild(probe);
    var px = parseFloat(window.getComputedStyle(probe).fontSize);
    probe.parentNode.removeChild(probe);
    if (px >= 12 && px <= 60 && navigator.maxTouchPoints > 1) {
      document.documentElement.style.fontSize = px + 'px';
    }
  } catch (e) {}
})();
"""


# ---------------------------------------------------------------------------
# The document
# ---------------------------------------------------------------------------

SHELL = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0, viewport-fit=cover">
<meta name="theme-color" media="(prefers-color-scheme: light)" content="{theme_light}">
<meta name="theme-color" media="(prefers-color-scheme: dark)" content="{theme_dark}">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-status-bar-style" content="default">
<meta name="apple-mobile-web-app-title" content="NewsLens">
<title>NewsLens — {title_date}</title>
<style>{css}</style>
</head>
<body data-briefing-date="{date}" data-bundle-version="{bundle_version}">
<script>{boot_js}</script>
<a class="skip-link" href="#main">Skip to today’s edition</a>
<main id="main" tabindex="-1">
<section id="view-today" class="view active">{today_html}</section>
{deep_views_html}
</main>
<script>{js}</script>
</body>
</html>
"""


def _masthead_html(server, row, date: str) -> str:
    """The masthead ceremony for the bundle, in the fixed §4 order:
    wordmark -> dateline -> [signature] -> dispatch strip -> offline slot ->
    section line.

    Not `server._masthead`, and the difference is exactly two things, both
    ruled: the shipped one hangs the SETTINGS GEAR off the wordmark row (there
    is no settings surface in a bundle, and a gear that opens nothing is the
    dead control the no-dead-buttons law forbids), and it closes with the
    EDITION BAR, which reads a WAV off the local disk — audio is deferred from
    Stage A, `audio` is null in the envelope, and no bar renders (adjudication
    Q1: no dead UI). The dateline itself — the only piece with real logic — is
    the shipped function, called, not copied.

    The offline stamp's SLOT is emitted here and left empty: §7 puts the line
    in the dispatch-strip block, and the service worker that knows whether this
    document arrived from the network is M2's. Rendering the slot now means M2
    fills a place that already exists in the ceremony instead of inserting one
    into a fixed order."""
    _e = server._e
    parts = ['<header class="page masthead">',
             '<div class="mast-top"><p class="wordmark">NewsLens</p></div>',
             server._dateline_html(date)]
    if row is not None:
        hm = server._utc_hm(row["generated_at"])
        if hm:
            # CONSUMED, not retyped — the same law as the tokens and the phone
            # scale above. A re-pin of the strip's grammar on the Mac reaches
            # the next edition's document with no second string to remember.
            parts.append(server._dispatch_strip_html(hm))
    parts.append('<p class="offline-line" id="offline-stamp" hidden></p>')
    # §4 no-chrome: Following and Archive are Mac/host surfaces and do not ride
    # the bundle, so the section line names the one view this document has. It
    # is a WORD, not a link — the destination is where you already are.
    parts.append(
        '<nav class="section-line" aria-label="Sections"><div class="page">'
        f'<span class="section-current" aria-current="page">'
        f'{_e(server.labels.NAV_TODAY)}</span></div></nav>')
    parts.append('</header>')
    return "".join(parts)


def build_html(con, date: str, entry: Optional[Dict]) -> str:
    """The whole edition as ONE self-contained document.

    `entry` is the generation-log entry AS AN OBJECT — the caller has it in
    hand and passing it is what keeps this off `generation_log.jsonl`. Every
    other input is read through the live connection, in this process, at this
    moment: that is what "frozen at publish" means operationally.

    Raises BundleError when the edition has no row (nothing to freeze)."""
    from . import server  # function-level: server imports generate lazily too,
    #                       and this keeps import order a non-question

    row = server._briefing_row(con, date)
    if row is None:
        raise BundleError(f"no briefing row for {date} — nothing to freeze")

    with server.read_pure_render():
        briefs, deep_sections = server._collect_deep_views(
            con, row, entry, "", server.labels.BACK_TO_TODAYS_EDITION,
            "view-today")
        body = server._render_briefing_body(
            con, row, entry, briefs, "", "view-today")
        head = _masthead_html(server, row, row["date"])

    light, dark = theme_colors()
    return SHELL.format(
        theme_light=light,
        theme_dark=dark,
        title_date=server._human_date(row["date"]),
        css=webui.CSS + phone_pass_rules() + PHONE_CSS,
        date=server._e(row["date"]),
        bundle_version=BUNDLE_VERSION,
        boot_js=BOOT_JS,
        today_html=head + body,
        deep_views_html="".join(deep_sections),
        js=BUNDLE_JS,
    )


# ---------------------------------------------------------------------------
# The envelope
# ---------------------------------------------------------------------------

def _tier_counts(entry: Optional[Dict]) -> Dict[str, int]:
    counts: Dict[str, int] = {}
    for tier in ((entry or {}).get("tiers") or []):
        if isinstance(tier, str):
            counts[tier] = counts.get(tier, 0) + 1
    return counts


def _arc_present(entry: Optional[Dict]) -> bool:
    """Did this edition serve a continuity line?

    Reuses the doctor's own arc predicate rather than inventing a second one.
    `_arc_editions_from_log` keys on memory_core's ARC_AUTHORED / ARC_OMITTED
    marker constants inside `memory.state_rewrites[].detail`, and returns a row
    only for an ARC-ELIGIBLE edition — so an edition whose threads did not move
    reads False here for the honest reason (no arc was due), which is the same
    population the NL-160 drought detector speaks for. One definition, two
    readers."""
    from . import doctor
    eligible = doctor._arc_editions_from_log([entry or {}])
    return bool(eligible) and eligible[0][1]


def build_bundle(con, date: str, entry: Optional[Dict]) -> Dict:
    """The JSON envelope. Field set is the adjudication's, exactly.

    `stories: []` is DELIBERATELY NOT EMITTED in v1 — Ada's dissent (ship the
    structured stories while the data is in hand, so a later stage can
    re-render the past) is on the record and was accepted-against: frozen IS
    the point, and baked HTML is what freezing looks like. `bundle_version`
    exists so v2 can add the field without a migration.

    `audio: null` is a RESERVED SCHEMA, not an omission: the reader renders no
    edition bar when it is null, so there is no dead player. Most editions have
    no audio, a 10-minute WAV is ~25-30MB, and the AAC path (afconvert, $0,
    stock macOS) is documented for v1.1."""
    from . import server

    row = server._briefing_row(con, date)
    if row is None:
        raise BundleError(f"no briefing row for {date} — nothing to freeze")
    html = build_html(con, date, entry)
    stories = (entry or {}).get("stories") or []
    return {
        "bundle_version": BUNDLE_VERSION,
        "edition_date": row["date"],
        "generated_at": row["generated_at"],
        "variant": (entry or {}).get("variant") or "",
        # The push endpoint's idempotency key (adjudication Q4: same sha ->
        # 200 no-op, changed -> replace + receipt). It digests the DOCUMENT,
        # because the document is what gets pushed and what gets read.
        "content_sha256": hashlib.sha256(html.encode("utf-8")).hexdigest(),
        "meta": {
            "story_count": len(stories),
            "tier_counts": _tier_counts(entry),
            "cost_usd": (entry or {}).get("total_usd"),
            "arc_present": _arc_present(entry),
            # Always false at the mint. The field exists so that IF a
            # reconstruction path is ever chartered, every honest artifact is
            # already distinguishable from it — rather than the question being
            # unanswerable for everything minted before the day it mattered.
            "reconstructed": False,
        },
        "audio": None,
        "html": html,
    }


# ---------------------------------------------------------------------------
# Writing and reading the artifact
# ---------------------------------------------------------------------------

def write_bundle(bundle: Dict, path: Optional[Path] = None) -> Path:
    """Atomic tmp+rename into data/briefings/, the write_artifact convention.

    Atomic because the push client and `newslens bundle --open` both read this
    file, and a reader that catches a half-written document would see a
    truncated edition rather than an error.

    The `finally` sweeps the tmp so a failed mint leaves no litter beside the
    briefings: on success `os.replace` has already consumed it (the unlink is a
    no-op), and on either failure point the half-written `<date>.phone.json.tmp`
    goes rather than sitting next to a good artifact looking like one. A SIGKILL
    inside the window still leaks — `finally` cannot run — and that residue is
    accepted: it is inert, `available_dates()` ignores any name that is not
    exactly `<date>.phone.json`, and the next mint overwrites it."""
    target = path or bundle_path(bundle["edition_date"])
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(target.name + ".tmp")
    try:
        tmp.write_text(json.dumps(bundle, ensure_ascii=False), encoding="utf-8")
        os.replace(str(tmp), str(target))
    finally:
        try:
            tmp.unlink()
        except OSError:
            pass
    return target


def mint(con, date: str, entry: Optional[Dict]) -> Path:
    """Build and write the bundle for `date`. The generate's call site wraps
    this in post-publish containment — see the mount in generate.py."""
    return write_bundle(build_bundle(con, date, entry))


def load(date: str) -> Dict:
    """Read a minted bundle. Refuses — loudly, by date — when there is none.

    THE REFUSAL IS THE FEATURE. A bundle for a back edition could be built at
    any time; it would render TODAY's follow state, today's thread names and
    today's reason lines, and label the result frozen-at-publish. The archive
    is honestly empty for every edition published before this milestone, and
    it fills one morning at a time."""
    path = bundle_path(date)
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        have = available_dates()
        detail = (f"bundles on file: {', '.join(have)}" if have
                  else "no editions have been bundled yet")
        raise BundleError(
            f"no frozen edition for {date}. This edition was published before "
            "its bundle existed, and one will NOT be reconstructed: a "
            "reconstruction would render today's follow state and call it "
            f"frozen at publish. ({detail})")
    except OSError as exc:
        raise BundleError(f"could not read {path} ({exc})")
    try:
        bundle = json.loads(raw)
    except ValueError as exc:
        raise BundleError(f"{path} is not readable JSON ({exc})")
    if not isinstance(bundle, dict) or "html" not in bundle:
        raise BundleError(f"{path} is not an edition bundle (no html field)")
    return bundle


def latest_date() -> Optional[str]:
    dates = available_dates()
    return dates[-1] if dates else None
