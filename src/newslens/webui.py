"""The NewsLens web UI templates — mockup-v5.html ported to server rendering.

BINDING SOURCES: design/mockup-v5.html + DIRECTION-v3.md + the v3 addendum,
plus the four final principal tweaks (M7 dispatch — applied here, marked
"TWEAK n" at each point of implementation):
  1. developing indicator: static dot, ~5% smaller than v5's (0.7rem -> 0.66rem)
  2. dot placement: aligned with the story-title LINE (flex-start + optical
     offset), not card-centered (v5) and not v4's too-high top
  3. date treatment: reverted to basic (serif/small-caps block -> backlog)
  4. NewsLens logo PLACEHOLDER centered in the top bar between date/settings

Axel's law carries through: every quiet affordance is a real, labeled,
focusable <button>; popups are role=dialog with focus management + Escape;
the dot's meaning is always also carried in words.

All dynamic values are html.escape()'d by the builders in server.py before
they reach these templates.
"""

# THE TOKEN BLOCK, on its own (Stage-0 C1). Split out of CSS below — same bytes,
# same order, concatenated back one line down — so a SECOND page can carry the
# DIRECTION-v5 §1 tokens without a second copy of them. The Commissioning's
# founding page is that second page (commissioning.CSS); DESIGN_SYSTEM.md's rule
# is that tokens live in variables, and two files declaring the same hexes is how
# they drift.
TOKENS = """
:root {
  /* v7 palette — DIRECTION-v5 §1 (the committed Front-Page tokens) */
  --paper: #FCFAF5; --ink: #1A1713; --ink-soft: #575046; --ink-faint: #79705F;
  --terra: #8F4A2E; --terra-deep: #6E3722; --moved: #4D6B50; --danger: #7A3B37;
  --rule: #E7DFD2; --cal-bare: #C9C0AF;
  --font-display: Charter, "Iowan Old Style", Georgia, "Palatino Linotype", serif;
  --font-sans: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
  --font-mono: ui-monospace, "SF Mono", Menlo, monospace;
  /* Legacy aliases — RETIRED as the Following/Archive surfaces landed (v7-M2,
     gate watch-for 9): --tracked -> --moved, --font-serif -> --font-display,
     --accent-deep and --max-w dropped (unused). The remainder still name the
     NOT-yet-rebuilt settings/popups/suggest surfaces; they retire when those
     land (tracked for the follow-up milestone). */
  --bg: var(--paper); --surface: #FFFFFF; --accent: var(--terra);
  --overlay-scrim: rgba(26,23,19,0.35); --popup-scrim: rgba(26,23,19,0.28);
  --radius: 10px;
}"""

CSS = TOKENS + """
* { box-sizing: border-box; -webkit-tap-highlight-color: transparent; }
html { -webkit-text-size-adjust: 100%; }
html { scroll-behavior: smooth; }
body { margin: 0; background: var(--paper); color: var(--ink); font-family: var(--font-sans);
  font-size: 1rem; line-height: 1.62; -webkit-font-smoothing: antialiased; }
/* Dark mode — CARRIED functionality (settings toggle). The v7 dark palette is
   NOT design-specified (the mockup is paper-only); this is a mechanical
   inversion holding the AA floor (§11) — FLAGGED for the design team, M2. */
body.dark { --paper: #1A1713; --ink: #FCFAF5; --ink-soft: #C9C0AF; --ink-faint: #9A9082;
  --terra: #D08A63; --terra-deep: #E0A882; --moved: #86B08C; --danger: #C9857B; --rule: #372F27;
  --surface: #241F1A; --overlay-scrim: rgba(0,0,0,0.6); --popup-scrim: rgba(0,0,0,0.5); }
a { color: var(--terra); text-decoration-thickness: 1px; text-underline-offset: 2px; }
a:hover { color: var(--terra-deep); }
button { font-family: var(--font-sans); }
a:focus-visible, button:focus-visible, input:focus-visible, textarea:focus-visible, summary:focus-visible, [tabindex]:focus-visible {
  outline: 3px solid var(--terra-deep); outline-offset: 2px; border-radius: 2px; }
.skip-link { position: absolute; left: -9999px; top: 0; background: var(--ink); color: var(--paper);
  padding: 0.5rem 1rem; z-index: 50; }
.skip-link:focus { left: 0.5rem; top: 0.5rem; }
@media (prefers-reduced-motion: reduce) { html { scroll-behavior: auto; } }

/* Layout: full-bleed views, centered .page reading column (DIRECTION-v5 §4) */
section.view { display: none; } section.view.active { display: block; }
/* NL-143 item 3c (his 07-18 item 4, polish gate APPROVED 4b): the reading
   container steps 72rem -> 84rem. The RECTANGLE LAW IS UNTOUCHED — the ~65%
   lead ratio he floated the same evening was DECLINED, so .today-grid stays
   7fr/5fr and this is a container-width change only. Design's charge-3
   reasoning, carried so nobody re-litigates it: the ratio redistributes width
   INSIDE the same container and does not shrink the margins he screenshotted;
   the container is what he actually complained about. */
.page { max-width: 84rem; margin: 0 auto; padding: 0 2rem; }
article.story { scroll-margin-top: 0.75rem; }

/* ---- Masthead / the dateline ceremony (DIRECTION-v5 §4) ---- */
.masthead { padding-top: 2.25rem; }
.mast-top { display: flex; align-items: baseline; justify-content: space-between; gap: 1rem; }
.wordmark { font-family: var(--font-display); font-size: 0.9rem; font-weight: 700;
  letter-spacing: 0.14em; text-transform: uppercase; color: var(--ink-faint); margin: 0 0 1.4rem; }
.settings-corner { background: transparent; border: 1px solid var(--rule); border-radius: 50%;
  width: 2rem; height: 2rem; display: inline-flex; align-items: center; justify-content: center;
  color: var(--ink-faint); cursor: pointer; flex-shrink: 0; }
.settings-corner:hover { border-color: var(--ink-soft); color: var(--ink); }
.settings-corner svg { display: block; stroke: currentColor; }
/* NL-149 (his word + two screenshots, 2026-08-12): the dateline's DESCENDERS
   (the 'y' of Friday, the comma) crossed the section line's rule on every state
   where nothing follows the date — no-edition, generating, failed. Cause is
   geometry, not layout: `line-height: 1.02` makes the LINE BOX shorter than the
   display face's own ascent+descent, so the ink overhangs the element box top
   and bottom and the next block starts under the glyphs.
   MEASURED, not estimated — read out of the shipped font file rather than eyeballed:
   Charter Bold (the first --font-display family, and the weight this rule sets)
   is unitsPerEm 2048, hhea ascender 2007 / descender -492, so ascent+descent =
   1.2202em against a 1.02em line box. Overhang per side = (1.2202 - 1.02) / 2 =
   0.1001em = 0.4004rem = 6.41px at 4rem/16px root. (OS/2 winAscent+winDescent
   gives the slightly smaller 5.72px; the larger figure is the one to clear.)
   The clearance is a bottom MARGIN on the dateline itself — ONE rule, so it
   applies in every state including the ones _masthead(None, …) renders — and
   0.5rem = 8px clears the measured 6.41px with ~1.6px to spare.
   THE NORMAL EDITION STATE DOES NOT MOVE: there the next sibling is
   `.dispatch-strip` (margin-top 0.8rem), and adjacent-sibling margins COLLAPSE
   to the larger of the two — 0.8rem, exactly what ships today. Deliberately
   NOT padding on .masthead (that would add to every state, including the one
   already correct) and NOT a state-specific selector (the fix belongs to the
   dateline's geometry, not to a panel). */
.dateline { font-family: var(--font-display); font-weight: 700; font-size: 4rem;
  line-height: 1.02; letter-spacing: -0.015em; margin: 0 0 0.5rem; }
.dateline .dl-num { color: var(--terra); }
.dateline .dl-year { font-size: 1.4rem; font-weight: 400; color: var(--ink-faint); letter-spacing: 0; }
.signature { font-family: var(--font-display); font-size: 1.3rem; line-height: 1.45;
  color: var(--ink-soft); margin: 0.9rem 0 0; max-width: 44rem; }
.dispatch-strip { font-family: var(--font-mono); font-size: 0.8rem; line-height: 1.6;
  color: var(--ink-soft); margin: 0.8rem 0 0; }
.dispatch-strip a { color: var(--moved); font-weight: 700; }
.dispatch-strip a:hover { color: var(--terra-deep); }

/* ---- Edition bar: the podcast player (restyled .episode-affordance, §6) ---- */
.episode-affordance { margin: 0.9rem 0 1.4rem; font-size: 0.88rem; color: var(--ink-soft); }
.episode-affordance button { background: none; border: 1px solid var(--terra); border-radius: 2px;
  padding: 0.15rem 0.55rem; font-size: 0.85rem; color: var(--terra); font-weight: 700; cursor: pointer; }
.episode-affordance button:hover { color: var(--terra-deep); border-color: var(--terra-deep); }
.episode-affordance .episode-meta { color: var(--ink-faint); font-size: 0.85rem; font-weight: 400; }
.episode-affordance audio { display: block; width: 100%; margin-top: 0.6rem; }
.edition-episode { border-bottom: 1px solid var(--rule); padding-bottom: 1.1rem; margin-bottom: 0.5rem; }
.player-extra { display: flex; gap: 0.5rem; margin-top: 0.5rem; }
.player-extra .player-btn { background: none; border: 1px solid var(--rule); border-radius: 2px;
  padding: 0.15rem 0.55rem; font-size: 0.8rem; font-family: var(--font-sans);
  color: var(--ink-soft); cursor: pointer; }
.player-extra .player-btn:hover { border-color: var(--ink-soft); color: var(--ink); }
.player-extra .speed-btn { min-width: 3.2rem; text-align: center; font-variant-numeric: tabular-nums; }

/* ---- Section line: nav, sticky, ONE line, the three destinations (§4) ---- */
.section-line { position: sticky; top: 0; z-index: 10; background: var(--paper);
  border-top: 1px solid var(--ink); border-bottom: 1px solid var(--rule); padding: 0.55rem 0; }
.section-line .page { font-size: 0.9rem; }
.section-line a { text-decoration: none; color: var(--ink-soft); margin-right: 1.6rem; }
.section-line a:hover { color: var(--ink); text-decoration: underline; }
.section-line a[aria-current="page"] { color: var(--ink); font-weight: 700; }

/* ---- Mini-masthead (Following / Archive open with this + the section line) ---- */
.mini-head { padding-top: 2rem; }
.mini-head .mast-top { align-items: baseline; }
.mini-head .wordmark { margin: 0 0 0.3rem; }
.mini-head .mh-date { font-family: var(--font-mono); font-size: 0.75rem; color: var(--ink-faint); }

/* ============================ TODAY — the newspaper grid (v8-M2, §12.3) ============================
   #1 heads the left column (slightly wider than half); #2/#3 the right column
   (one tier, roughly equal, content-sized); #4..N are the strips — the GROUT —
   balanced across the bottom (server-side) to square the page into a rectangle.
   DOM is rank order 1→N; grid-column classes place the COLUMN (presentation
   only). FIX-1 (principal 2026-07-18): the ROW placement is server-computed too
   — each slot carries a --gr custom property ("<start> / <end>" grid lines,
   from server._grid_row_spans, generalizing the mockup's grid-areas), so the
   lead's span is COMPUTED (retiring the fixed `grid-row: 1 / span 2` that
   stranded a void beside cards that outran it) and a tall #3 spans DOWN beside
   the left strips. --gr rides an INLINE custom property, never inline grid-row,
   so the ≤900px reset below (grid-row:auto) still wins and the mobile stack is
   untouched. align-items:start so nothing stretches when column heights differ
   (scroll released). */
.today-grid { display: grid; grid-template-columns: 7fr 5fr; gap: 0 4rem;
  padding: 2.2rem 0 1rem; align-items: start; }
.today-grid > .grid-lead { grid-column: 1; grid-row: var(--gr, auto); }
.today-grid > .grid-col-a { grid-column: 1; grid-row: var(--gr, auto); }
.today-grid > .grid-col-b { grid-column: 2; grid-row: var(--gr, auto); }
/* Medium cards (#2/#3) — one tier, quiet register (was the retired .col-right). */
.today-grid article.story { padding: 0 0 1.6rem; margin-bottom: 1.6rem; border-bottom: 1px solid var(--rule); }
.today-grid article.story h2.headline { font-size: 1.4rem; }
.today-grid article.story .body { font-size: 0.95rem; }
.today-grid article.story .body > p { margin: 0 0 0.8rem; }
.today-grid article.story .deck { font-size: 0.82rem; margin-bottom: 0.7rem; padding: 0.35rem 0; }
.today-grid article.story .move-label { font-size: 0.72rem; margin: 1rem 0 0.25rem; }
.today-grid article.story .furniture, .today-grid article.story .meta-footnote { font-size: 0.76rem; margin-top: 0.6rem; }
/* The slim memory stamp (item 2) — pure furniture, machine register, riding the
   deck. Same ● grammar as Following's UPDATED line; text-transform does the
   visual uppercasing so screen readers hear natural case. The WORDS carry it;
   the green dot never alone. */
.memline { font-family: var(--font-mono); font-size: 0.72rem; letter-spacing: 0.06em;
  color: var(--ink-faint); text-transform: uppercase; }
/* NL-17-M1c: .mem-dot is DELETED (grep-verified zero emit sites) — his
   attachment ruling killed the continuity dot on cards generally, so the only
   dot a card shows is the terra follow mark. Moved = the WORD plus a weight
   step; weight is never the sole channel. ink-soft 7.62:1 — AA with headroom. */
.memline.memline--moved { color: var(--ink-soft); font-weight: 700; }
/* Thin strips (#4..N): hairline top rule, headline-link, 4-line-clamped summary,
   machine smeta (the degraded stamp leads it when the thread moved). Never a box.
   NL-143 item 3a: the run-head In-Brief slug rides here too (see .brief-slug).
   NL-143 item 3b (his 07-18 item 3, polish gate APPROVED 4b): the clamp is
   2 -> 4 visible lines — he asked for "+1-2" and design took the top of his
   range because charge 3 widens the container, and wider columns fit more words
   per line, so 3 would have under-delivered his intent. Display-only: the full
   text is the SAME element (nothing is hidden from AT), shorter summaries still
   render unclamped, and the deep view carries everything (superset law).
   The 32rem measure cap RIDES WITH the widening and is not decoration: at
   84rem a left-column strip line runs ~88ch without it, which is exactly the
   unreadable measure the 4-line clamp would then quadruple. */
.strip { border-top: 1px solid var(--rule); padding: 0.8rem 0 1rem; }
.strip h3.headline { font-family: var(--font-display); font-weight: 700; font-size: 1.02rem;
  line-height: 1.3; margin: 0 0 0.2rem; }
.strip .sum { font-size: 0.85rem; line-height: 1.5; color: var(--ink-soft); margin: 0 0 0.35rem;
  max-width: 32rem;
  display: -webkit-box; -webkit-line-clamp: 4; -webkit-box-orient: vertical; overflow: hidden; }
/* NL-143 item 3a — THE IN-BRIEF SLUG RETURNS (his 07-18 item 2, polish gate
   APPROVED 4b as "per-run In-Brief slugs"; re-raised 2026-08-07 when he found
   the approved package had never been built). It reuses the EXISTING small-caps
   label family (.move-label / .section-h) at its faintest step — no new
   typographic species, no chip, no container, no added rule: the strip's own
   hairline is the only rule in play.
   ORNAMENT, NOT STRUCTURE: it is aria-hidden. The h1/h2/h3 heading tree still
   carries the tier for assistive tech (v7-M2), so rendering this as a heading
   would put TWO structural renderings on one semantic tier — phantom structure.
   Sighted scanning gets the word; the heading tree is untouched. */
.brief-slug { font-family: var(--font-sans); font-size: 0.68rem; font-weight: 700;
  letter-spacing: 0.12em; text-transform: uppercase; color: var(--ink-faint);
  margin: 0 0 0.5rem; }
/* NL-143 item 2a — the quick tier's follow mount. Deliberately NOT a .deck:
   that container carries a bottom rule and card-scale margins the strip
   register forbids ("never a box"). One austere line under the smeta, carrying
   the SAME .deck-follow verb the cards render — same component, same
   vocabulary, no new UI species. */
.strip-follow { margin: 0.3rem 0 0; font-size: 0.85rem; }
.strip .smeta { font-family: var(--font-mono); font-size: 0.68rem; letter-spacing: 0.06em;
  color: var(--ink-faint); text-transform: uppercase; }
.lead h2.headline { font-family: var(--font-display); font-weight: 700; font-size: 3.5rem;
  line-height: 1.06; letter-spacing: -0.015em; margin: 0 0 0.7rem; }
.lead .body { font-size: 1.05rem; max-width: 38rem; }
.lead .body > p { margin: 0 0 1rem; }
.move-label { font-family: var(--font-sans); font-size: 0.78rem; font-weight: 700;
  letter-spacing: 0.12em; text-transform: uppercase; color: var(--ink-soft); margin: 1.4rem 0 0.3rem; }
.my-read { font-style: italic; }
/* the deck (under-title): NL-65 leaves ONLY the follow control here */
.deck { display: flex; flex-wrap: wrap; align-items: baseline; gap: 0.35rem 1.1rem;
  margin: 0 0 1.15rem; padding: 0.45rem 0; border-bottom: 1px solid var(--rule); font-size: 0.88rem; }
.deck > * { min-width: 0; }
/* F-5 rider (design mini 2026-08-08, Axel's one-token finding): `cursor` leaves
   this SHARED rule. .tracked-marker is a NON-INTERACTIVE node — a pointer cursor
   on it is a fabricated affordance, promising a tap that does nothing. (Landed
   state, stated honestly: .tracked-marker's own rule below already set
   `cursor: default` and, being later at equal specificity, already won — so the
   declaration here was dead, not live. The rider is hygiene, not a bug fix: it
   removes the trap where a future reorder or a specificity bump resurrects the
   promise. Scoping the cursor to .deck-follow is what makes it structurally
   impossible instead of cascade-dependent.) */
.tracked-marker, .deck-follow { background: none; border: none; padding: 0;
  text-align: left; font-family: var(--font-sans); font-size: 0.88rem; font-weight: 700; color: var(--terra); }
.deck-follow { cursor: pointer; }
/* NL-17-M1c: THE ONE FOLLOW-LINE COMPONENT — ONE persistent .follow-slot node
   (single-rendering law) mounted on four surfaces: today card, continuation
   card, deep view, Following row. Lines of type — no box, no background, no
   border, no spinner. spans-and-links, no new container shapes. MARK MONOSEMY
   on cards: terra ● (followed) is the ONLY dot a card renders — the continuity
   dot died with his 07-25 attachment ruling.
   DELETED with the thread model (grep-verified zero emit sites at the diff):
   .fl-lead / .fl-options (the ask — dead, his ruling ④), .fl-degrade{,-why}
   (the apology door — dead, NL-103 row 3), .fl-switch-failed (superseded by
   .fl-act-refusal, which states a reason), and the [data-state="resolving"] /
   ["asking"] display rules (neither state exists: the settle is invisible). */
.deck-follow:hover { color: var(--terra-deep); text-decoration: underline; }
.deck-follow.not-following { font-weight: 400; color: var(--ink-soft); }
.follow-slot { min-width: 0; }
.follow-slot[data-state="expanded"], .follow-slot[data-state="refused"],
.follow-slot[data-state="unfollowed"] { display: block; flex-basis: 100%; }
.follow-line { max-width: 38rem; margin: 0.35rem 0 0.9rem; }
.thread .follow-line { margin: 0.2rem 0 0; }
.fl-status { display: block; font-size: 0.9rem; color: var(--ink-faint); }
.fl-sentence { display: block; font-size: 0.95rem; color: var(--ink); }
.fl-sentence .fl-dot { color: var(--terra); font-weight: 700; }
.fl-sentence strong { font-weight: 700; }
.fl-alts { display: block; font-size: 0.85rem; color: var(--ink-faint); margin-top: 0.15rem; }
.fl-alts a, .fl-alts .fl-unfollow { color: var(--terra); }
.fl-alts .sep { margin: 0 0.4rem; color: var(--rule); }
.fl-unfollow { background: none; border: none; padding: 0; cursor: pointer;
  font-family: var(--font-sans); font-size: inherit; font-style: normal;
  font-weight: 400; color: var(--terra); }
.fl-unfollow:hover { color: var(--terra-deep); text-decoration: underline; }
/* THE REFUSAL (R-WRITE): ○ = nothing was followed. Loud — full ink at the
   sentence size — and NEVER danger-colored: a refusal is not a generation
   failure, and --danger stays generation-failure-only by law. */
.fl-refusal { display: block; font-size: 0.95rem; color: var(--ink); margin: 0; }
.fl-refusal .fl-dot-off { color: var(--ink-faint); font-weight: 700; }
.fl-refusal-why { display: block; font-size: 0.85rem; color: var(--ink-faint); }
/* ACT-LEVEL refusal: the follow STANDS, so the state line above is untouched
   and this is a second line beneath it. No mark — the ● above still reports the
   follow, and a second glyph here could only contradict it. */
.fl-act-refusal { display: block; font-size: 0.85rem; color: var(--ink-soft);
  margin-top: 0.2rem; }
/* the unfollow receipt — ~3s, then the same slot reverts to the resting CTA. */
.fl-receipt { display: block; font-size: 0.95rem; color: var(--ink); margin: 0; }
/* the compact class qualifier (deck verb + moment line) and the persistent
   Following-row qualifier (Screen 2): words, quiet register, color carries
   nothing. */
.oq { color: var(--ink-soft); font-weight: 400; }
.alt-q { font-weight: 400; color: var(--ink-soft); }
.tracked-marker { color: var(--moved); cursor: default; }
.tracked-marker::before { content: "\\25CF "; }
/* ===========================================================================
   F-5 — COARSE-POINTER TAP TARGETS (design mini 2026-08-08, adjudicated).

   THE FINDING. Every target in the .deck-follow family measures ~17px tall on
   mobile — the verb on cards AND strips (one component: the box is 0.88rem
   times the UA button reset's ~1.2 line-height, because this component never
   sets line-height), and ~16px for the committed anchor and the acts links.
   WCAG 2.5.8 AA passes on all four surfaces, but ONLY through the spacing
   exception — which is an accident of editorial whitespace, not a property
   anyone chose. Any future tightening breaks AA silently. That is the "inherited
   a third time" mechanism the NL-143 gate named when it routed this.

   THE TREATMENT. Invisible padding with negative-margin compensation, on the
   SHARED selectors — so both the server emit and the JS re-renderers are covered
   by one block, and the single-rendering law is not grazed: we are styling the
   one path, not forking it. Zero visual change and zero layout shift by
   construction (the margin box is unchanged in both axes; an inline-block's line
   box is computed from its MARGIN box, which is why the compensation works on
   the strip's inline mounts too).

   TWO ASYMMETRIES, both ruled, both load-bearing:

   1. VERTICAL ONLY on the acts row (Ines, adopted). Three targets share one line
      ~17px apart and one of them is DESTRUCTIVE. Horizontal padding would
      convert that dead gutter — where a miss costs a retry — into live
      edge-to-edge borders, where a thumb aiming at Unfollow and landing 20px
      left performs an ALTITUDE SWITCH instead. Bigger targets, worse errors. So
      the acts grow with padding-block only and the gutters are untouched.

   2. COARSE POINTER ONLY (Greta, adopted; AXEL DISSENTS ON RECORD). Unconditional
      padding puts the verb's invisible box over the strip's .smeta line, and a
      button box over text blocks selection for desktop readers who copy that
      metadata. Desktop passes AA on the exception with wide margins (~30px
      clearances against a 3.5px overhang). Axel's dissent: a mouse tremor does
      not care about a media query, and this leaves desktop motor-impaired
      readers at 17px. Recorded, not gated — falsifier is any observed desktop
      miss-click, which drops the query.

   THE VERB IS 0.7rem, NOT 0.85rem — THE DESIGN'S OWN RECORDED FALLBACK, and the
   build-time assert is what fired it. At 390px a today-grid card puts
   h2.headline (margin-bottom 0.4rem = 6.4px) directly above .deck (padding-top
   0.35rem = 5.6px), so the headline LINK's box sits ~12px above the verb's. The
   ruled 0.85rem (13.6px) of upward padding would have overlapped it by ~1.6px —
   two live targets sharing pixels, which is worse than the undersized target it
   fixes. 0.7rem (11.2px) clears by ~0.8px. Result: verb 17 + 22.4 = 39.4px
   (2.5.8 AA by BOX, exception no longer load-bearing; short of 2.5.5's 44px,
   which the design named as unreachable in that case and accepted). Acts:
   16 + 17.6 = 33.6px, clearing the Following row's h2 link by ~4.6px.
   tests/test_nl17_m1_f5_targets.py pins every number in this paragraph. */
@media (pointer: coarse) {
  .deck-follow { display: inline-block; padding: 0.7rem 0.5rem; margin: -0.7rem -0.5rem; }
  .fl-alts a, .fl-alts .fl-unfollow { display: inline-block;
    padding-block: 0.55rem; margin-block: -0.55rem; }
}
/* NL-65: the deep-view entry moves to the story BOTTOM, before the furniture */
.story-more { margin: 1.1rem 0 0; font-size: 0.88rem; }
.deep-view-entry-link { color: var(--terra); font-weight: 700; text-decoration: none; }
.deep-view-entry-link:hover { color: var(--terra-deep); text-decoration: underline; }
.furniture, .meta-footnote { font-size: 0.8rem; font-style: italic; color: var(--ink-faint);
  margin: 1.1rem 0 0; max-width: 38rem; line-height: 1.5; }
/* NL-134 F3: the why-chosen line REPLACES .override-note, which is deleted
   together with its .reason child — that span WAS the F1 double-render. This
   line rides every story on every tier. The Related-to form reads as quiet
   furniture; the world-impact form keeps the terra prominence the override
   callout had, because it is still the "this one is off your map" signal. */
.why-chosen { font-size: 0.82rem; font-weight: 400; color: var(--ink-faint);
  font-family: var(--font-sans); margin: 0 0 0.6rem; line-height: 1.45; }
.why-chosen .why-label { font-weight: 700; color: var(--ink-soft); }
.why-chosen--world { color: var(--ink); }
.why-chosen--world .why-label { color: var(--terra-deep); }
h2.headline, h3.headline, h4.headline { font-family: var(--font-display); font-weight: 700;
  margin: 0 0 0.4rem; line-height: 1.22; }
/* NL-68 item 8: the story title IS the click-through to its deep view — it reads
   as a headline (inherits type + ink), not a coloured link; the deep-view intent
   shows on hover. Keyboard focus lands on the real <a>. */
.headline a.headline-link { color: inherit; text-decoration: none; }
.headline a.headline-link:hover { color: var(--terra-deep); }

/* FIX-3 (2026-07-18): the "In brief" Today REGION died in v8-M2 (quick-tier
   items are strips now). Its .in-brief/.brief-label/.snippet/.quick-hit rules
   are DELETED — the prior comment's claim that they dressed the NL-66(b) $0
   deep view was false: that view renders .deep-section-label (server.py), and
   these four classes had zero emit sites (grep-verified).
   STILL TRUE after NL-143: the region and its four classes stay dead. What
   returned is the LABEL, as .brief-slug on the run-head strip — no wrapper, no
   container, and no resurrection of these rules. */

/* Still-tracking strip (retro-mock idiom; A8 no-fabrication teeth in the composer) */
.still-tracking { margin: 1.6rem 0 0; padding-top: 1rem; border-top: 1px solid var(--rule); }
.still-tracking .st-line { font-size: 0.82rem; color: var(--ink-soft); margin: 0 0 0.5rem; line-height: 1.5; }
.still-tracking .st-thread { font-family: var(--font-display); font-weight: 700; color: var(--ink); }


.footer-tag { margin-top: 1.5rem; padding-top: 1.25rem; border-top: 1px solid var(--rule); }
.footer-tag button.disclosure-trigger { background: transparent; border: none; padding: 0;
  font-size: 0.74rem; color: var(--ink-faint); cursor: pointer; display: inline-flex;
  align-items: center; gap: 0.3rem; }
.footer-tag button.disclosure-trigger:hover { color: var(--ink-soft); }
.footer-tag button.disclosure-trigger .caret { font-size: 0.65rem; transition: transform 150ms ease-out; display: inline-block; }
.footer-tag button.disclosure-trigger[aria-expanded="true"] .caret { transform: rotate(90deg); }
/* NL-58 ruling 6: the collection window, quiet but always visible on Today. */
.coverage-window { font-size: 0.74rem; color: var(--ink-faint); margin: 0.5rem 0 0; }
.footer-detail { font-size: 0.74rem; color: var(--ink-faint); line-height: 1.6; margin-top: 0.75rem; display: none; }
.footer-detail.open { display: block; }
.footer-detail p { margin: 0 0 0.5rem; } .footer-detail p:last-child { margin-bottom: 0; }

/* Staleness banner (item 1): prominent, sticky at the top of every view; the
   generate trigger refuses server-side, this only warns and names the fix. */
.staleness-banner { position: sticky; top: 0; z-index: 60; background: var(--danger);
  color: #FFF; padding: 0.7rem 1.1rem; font-size: 0.9rem; line-height: 1.45; text-align: center; }
.staleness-banner strong { font-weight: 600; }
.staleness-banner code { font-family: var(--font-mono); font-size: 0.85em;
  background: rgba(255,255,255,0.2); padding: 0.05rem 0.4rem; border-radius: 4px; }
.state-panel { background: var(--surface); border-radius: var(--radius); padding: 1.75rem 1.5rem; margin-top: 1.5rem; }
.state-panel h2 { font-family: var(--font-display); font-size: 1.15rem; margin: 0 0 0.6rem; }
.state-panel p { font-size: 0.9rem; color: var(--ink-soft); margin: 0 0 1rem; }
/* NL-88 live generation status — typography-carried, tokens only, no chrome. */
.gen-live { font-family: var(--font-mono); font-size: 0.82rem; color: var(--ink-soft); margin: 0 0 1rem; }
.gen-live .gen-live-stage { color: var(--ink); }
.gen-live .gen-live-model { color: var(--ink-faint); }
.gen-live .gen-live-clock { display: block; color: var(--ink-faint); font-size: 0.76rem; margin-top: 0.3rem; }
/* NL-149 item 1 — the generating LOG: one line per finished step, added below
   the last one, each keeping the time that step took. The live line (.gen-live
   above) stays underneath as the newest, still-running entry, so the panel reads
   top-down as a log ending in "now". Same mono furniture register as .gen-live —
   this is machine grade, never edition body — and no chrome: the time column is
   held by tabular numerals and a right-aligned span, not by a table or a rule.
   Reduced margin on .gen-live when a log precedes it so the two read as one
   block rather than two stacked panels. */
.gen-log { list-style: none; margin: 0 0 0.35rem; padding: 0; max-width: 34rem;
  font-family: var(--font-mono); font-size: 0.82rem; color: var(--ink-faint); }
.gen-log li { display: flex; align-items: baseline; gap: 0.5rem; padding: 0.15rem 0; }
.gen-log .gen-log-step { color: var(--ink-soft); }
.gen-log .gen-log-model { color: var(--ink-faint); }
.gen-log .gen-log-time { margin-left: auto; font-variant-numeric: tabular-nums; }
.gen-log + .gen-live { margin-top: 0; }
.error-text { font-size: 0.85rem; color: var(--danger); margin: 0 0 1rem; }
.cta-quiet { display: inline-block; background: var(--ink); color: var(--bg); font-size: 0.85rem;
  border: none; padding: 0.6rem 1.1rem; border-radius: var(--radius); cursor: pointer; }
.cta-outline { display: inline-block; background: transparent; color: var(--ink-soft); font-size: 0.85rem;
  border: 1px solid var(--rule); padding: 0.55rem 1.05rem; border-radius: var(--radius); cursor: pointer; }
.cta-outline:hover { border-color: var(--ink-soft); color: var(--ink); }

/* h1.view-title: still the archive-in-place EDITION date title (NL-11). */
h1.view-title { font-family: var(--font-display); font-size: 1.5rem; margin: 1.5rem 0 1.25rem; }
.sub-view { display: none; } .sub-view.active { display: block; }
/* section-h: a real h2 now (v7-M2 heading semantics) — the class carries the
   quiet furniture look so the heading level and the appearance are decoupled. */
.section-h { font-family: var(--font-sans); font-size: 0.75rem; font-weight: 700; letter-spacing: 0.06em;
  text-transform: uppercase; color: var(--ink-faint); margin: 1.75rem 0 0.75rem; }
.section-h:first-child { margin-top: 0; }
.empty-note { font-size: 0.85rem; color: var(--ink-faint); font-style: italic; padding: 0.5rem 0; }

/* ==================== FOLLOWING — the Spine on paper (§7/§12.2/§12.4/§12.5) ==================== */
.page-title { font-family: var(--font-display); font-weight: 700; font-size: 2.6rem;
  line-height: 1.05; margin: 2rem 0 0.4rem; }
.view-line { font-size: 0.9rem; max-width: 44rem; margin: 0 0 2rem;
  padding-bottom: 0.55rem; border-bottom: 1px solid var(--rule); }
.view-line a { text-decoration: none; color: var(--ink-soft); margin-right: 1.6rem; }
.view-line a:hover { color: var(--ink); text-decoration: underline; }
.view-line a[aria-current="true"], .view-line a.current { color: var(--ink); font-weight: 700; }
.follow-story { margin: 0 0 1.5rem; }
.follow-story .follow-new { background: none; border: none; padding: 0; cursor: pointer;
  font-family: var(--font-sans); font-size: 0.9rem; font-weight: 700; color: var(--terra); }
.follow-story .follow-new:hover { color: var(--terra-deep); text-decoration: underline; }
/* Updated rows: full anatomy, loud and few (no cards — hairline separation). */
.thread { border-top: 1px solid var(--rule); padding: 1.4rem 0; max-width: 44rem; }
.t-stamp { display: block; font-family: var(--font-mono); font-size: 0.72rem;
  letter-spacing: 0.06em; color: var(--ink-faint); margin: 0 0 0.25rem; }
.t-stamp .t-moved { color: var(--moved); font-weight: 700; }
.thread-name { font-family: var(--font-display); font-weight: 700; font-size: 1.45rem;
  line-height: 1.2; margin: 0 0 0.3rem; min-width: 0; overflow-wrap: break-word; }
.thread-name a { color: var(--ink); text-decoration: none; }
.thread-name a:hover { color: var(--terra-deep); text-decoration: underline; }
.thread-delta { font-size: 0.95rem; color: var(--ink); margin: 0 0 0.45rem; max-width: 40rem; }
.thread-note { font-size: 0.88rem; color: var(--ink-soft); font-style: italic; margin: 0 0 0.2rem;
  max-width: 40rem; display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; }
/* The counted quiet fold (§12.5): native details/summary, keyboard-operable. */
.quiet-fold { border-top: 1px solid var(--rule); border-bottom: 1px solid var(--rule);
  max-width: 44rem; margin: 0 0 2rem; }
.quiet-fold summary { cursor: pointer; padding: 1rem 0; font-size: 0.92rem; color: var(--ink-soft); }
.quiet-fold summary:hover { color: var(--ink); }
.quiet-fold summary .qf-count { font-weight: 700; color: var(--ink); }
.q-list { list-style: none; margin: 0; padding: 0 0 0.6rem; }
.q-row { border-top: 1px solid var(--rule); padding: 0.5rem 0;
  display: flex; flex-wrap: wrap; align-items: baseline; gap: 0.2rem 0.9rem; }
.q-row > * { min-width: 0; }
.q-row a { font-family: var(--font-display); font-size: 1.05rem; color: var(--ink-soft);
  text-decoration: none; overflow-wrap: break-word; }
.q-row a:hover { color: var(--ink); text-decoration: underline; }
.q-stamp { font-family: var(--font-mono); font-size: 0.68rem; letter-spacing: 0.06em; color: var(--ink-faint); }
.lifecycle-row { border-top: none; padding: 0.4rem 0; }

/* ==================== ARCHIVE — the step-back redesign (§14) ====================
   Day-states are ALL typographic; nothing encloses a numeral, so two-digit dates
   cannot cramp (the fix by construction). Desktop pairs the month grid with the
   day panel (the front page's own 7fr/5fr skeleton); narrow viewports stack them.
   Two functional colors only: moved-green underline = has-edition, terra = today
   / the action button. Scale (not a ring) marks the pick. */
.month-title { font-family: var(--font-display); font-weight: 700; font-size: 3rem;
  line-height: 1; margin: 2rem 0 0.3rem; }
h2.month-title { font-size: 2.2rem; }
.cal-month + .cal-month { margin-top: 2.4rem; }
.month-title .yr { font-size: 1.3rem; font-weight: 400; color: var(--ink-faint); }
.month-nav { font-family: var(--font-mono); font-size: 0.78rem; letter-spacing: 0.06em;
  color: var(--ink-faint); margin: 0 0 1.4rem; display: flex; gap: 1.6rem; }
.month-nav a { color: var(--ink-soft); text-decoration: none; }
.month-nav a:hover { color: var(--ink); text-decoration: underline; }
.arch-cols { display: grid; grid-template-columns: 7fr 5fr; gap: 0 4rem;
  align-items: start; margin: 1.4rem 0 4rem; }
/* gate FIX-2: visually-hidden text for AT (the no-edition today qualifier). */
.sr-only { position: absolute; width: 1px; height: 1px; padding: 0; margin: -1px;
  overflow: hidden; clip: rect(0 0 0 0); white-space: nowrap; border: 0; }
.cal-grid { display: grid; grid-template-columns: repeat(7, 1fr); gap: 0.4rem; }
.cal-dow { font-size: 0.7rem; font-weight: 700; letter-spacing: 0.1em; text-transform: uppercase;
  color: var(--ink-faint); padding-bottom: 0.3rem; }
.cal-cell { min-height: 4rem; padding: 0.3rem 0.35rem; font-family: var(--font-display); }
.cal-num { font-size: 1.2rem; line-height: 1.2; display: inline-block; }
.cal-void .cal-num { color: var(--cal-bare); }           /* pre-history + future: barest */
.cal-gap .cal-num { color: var(--ink-faint); }            /* gap within history: faint, no shame */
.cal-edition button { background: none; border: none; padding: 0; margin: 0; cursor: pointer;
  text-align: left; display: block; width: 100%; color: var(--ink); font-family: var(--font-display); }
.cal-edition .cal-num { font-weight: 700; border-bottom: 2px solid var(--moved); }  /* has-edition */
.cal-edition button:hover .cal-num { color: var(--terra-deep); }
.cal-stamp { display: block; font-family: var(--font-mono); font-size: 0.64rem;
  color: var(--ink-faint); margin-top: 0.2rem; }
.cal-today .cal-num { color: var(--terra); }             /* today: terra numeral, nothing else */
.cal-picked .cal-num { font-size: 2rem; line-height: 1; }  /* picked: the loud numeral — no enclosure */
.day-panel { border-top: 1px solid var(--ink); padding-top: 0.9rem; }
.dp-stamp { font-family: var(--font-mono); font-size: 0.74rem; letter-spacing: 0.06em;
  color: var(--ink-faint); margin: 0 0 0.7rem; }
.dp-action { margin: 0 0 1.1rem; }
.dp-btn { display: inline-block; border: 1px solid var(--terra); border-radius: 2px;
  color: var(--terra); font-weight: 700; font-size: 0.88rem; padding: 0.3rem 0.8rem;
  text-decoration: none; background: none; cursor: pointer; }
.dp-btn:hover { color: var(--terra-deep); border-color: var(--terra-deep); }
.dp-headlines { list-style: none; margin: 0; padding: 0; }
.dp-headlines li { border-top: 1px solid var(--rule); padding: 0.55rem 0; }
.dp-headlines a { font-family: var(--font-display); font-weight: 700; font-size: 1rem;
  color: var(--ink); text-decoration: none; }
.dp-headlines li.dp-lead a { font-size: 1.2rem; }
.dp-headlines a:hover { color: var(--terra-deep); text-decoration: underline; }

/* ==================== THREAD PAGE — the Open thread destination ==================== */
.dossier-state { font-size: 1rem; color: var(--ink); margin: 0 0 0.6rem; max-width: 44rem; line-height: 1.6; }
.dossier-delta { font-size: 0.9rem; color: var(--ink-soft); margin: 0 0 0.4rem; }
.dossier-delta .delta-label { color: var(--ink-faint); }
.state-asof { font-family: var(--font-mono); font-size: 0.72rem; color: var(--ink-faint); }
.thread-editions { font-size: 0.9rem; color: var(--ink-soft); margin: 0; }
.thread-editions a { color: var(--moved); font-weight: 700; text-decoration: none; }
.thread-editions a:hover { color: var(--terra-deep); }
.thread-editions .sep { color: var(--rule); }
.thread-verbs { display: flex; flex-wrap: wrap; gap: 0.5rem; margin: 1.5rem 0 3rem;
  padding-top: 1.25rem; border-top: 1px solid var(--rule); max-width: 44rem; }
.thread-verbs button { font-size: 0.8rem; background: transparent; border: 1px solid var(--rule);
  color: var(--ink-soft); padding: 0.35rem 0.75rem; border-radius: 7px; cursor: pointer; }
.thread-verbs button:hover { border-color: var(--ink); color: var(--ink); }
/* NL-103 row 5 (RATIFIED register, C7): the Delete verb's --danger hover DIED.
   Delete is ink, never danger — the confirm's counts line carries the weight and
   color is never the channel. The generic .thread-verbs button:hover above
   supplies the ink hover; .delete-action stays as the render hook only. */

.token-search { width: 100%; font-size: 0.92rem; font-family: var(--font-sans); color: var(--ink);
  background: var(--surface); border: 1px solid var(--rule); border-radius: var(--radius);
  padding: 0.65rem 0.9rem; margin-bottom: 0.6rem; }
.token-search-hint { font-size: 0.78rem; color: var(--ink-faint); margin: 0 0 1.25rem; }
.token-group { margin-bottom: 1.5rem; }
.token-group-name { font-size: 0.76rem; font-weight: 600; color: var(--ink-faint);
  text-transform: uppercase; letter-spacing: 0.05em; margin: 0 0 0.75rem; }
.token-list { display: flex; flex-wrap: wrap; gap: 0.55rem; }
.token { display: inline-flex; align-items: center; gap: 0.4rem; font-size: 0.85rem; color: var(--ink);
  background: transparent; border: 1px solid var(--rule); border-radius: 999px;
  padding: 0.3rem 0.5rem 0.3rem 0.85rem; }
.token button.token-remove { background: transparent; border: none; padding: 0;
  color: var(--ink-faint); font-size: 0.85rem; line-height: 1; cursor: pointer; }
/* NL-103 FIX-4 (gate 2026-07-26): --ink, not --danger. Remove is list
   membership only — nothing is destroyed — so danger color on it inverted the
   law the same batch settled for Delete. The hover affordance stays. */
.token button.token-remove:hover { color: var(--ink); }

/* NL-11: the shared suggestion combobox (replaces the native datalist).
   House-styled per DIRECTION law — outlined, spaced, uncolored, no chips;
   emphasis is typography (accent text on the active option), never fill. */
.suggest { position: relative; }
.suggest-list { list-style: none; margin: -0.35rem 0 0.6rem; padding: 0.3rem;
  border: 1px solid var(--rule); border-radius: var(--radius); background: var(--surface);
  max-height: 15rem; overflow-y: auto; }
.suggest-list[hidden] { display: none; }
.suggest-list li { padding: 0.5rem 0.65rem; border-radius: 7px; cursor: pointer;
  display: flex; flex-direction: column; gap: 0.1rem; }
.suggest-list li .s-label { font-size: 0.9rem; color: var(--ink); }
.suggest-list li .s-sub { font-size: 0.76rem; color: var(--ink-faint); }
.suggest-list li:hover, .suggest-list li[aria-selected="true"] { background: var(--bg); }
.suggest-list li[aria-selected="true"] .s-label { color: var(--accent); font-weight: 600; }

.slide-scrim { position: fixed; inset: 0; background: var(--overlay-scrim); z-index: 30; display: none; }
.slide-scrim.open { display: block; }
.slide-panel { position: fixed; top: 0; right: 0; bottom: 0; width: min(88vw, 23rem);
  background: var(--bg); z-index: 31; transform: translateX(100%); transition: transform 220ms ease-out;
  padding: 1.5rem 1.25rem; overflow-y: auto; box-shadow: -2px 0 12px rgba(43,38,33,0.12); }
.slide-panel.open { transform: translateX(0); }
.slide-panel h2 { font-family: var(--font-display); font-size: 1.3rem; margin: 0 0 1.25rem; }
.slide-close { background: transparent; border: 1px solid var(--rule); border-radius: 50%;
  width: 2rem; height: 2rem; float: right; cursor: pointer; color: var(--ink-faint); }
.slide-close:hover { border-color: var(--ink-soft); color: var(--ink); }
.settings-row { padding: 0.85rem 0; border-bottom: 1px solid var(--rule); font-size: 0.9rem;
  display: flex; justify-content: space-between; align-items: center; gap: 1rem; }
.settings-row-label { color: var(--ink-faint); font-size: 0.75rem; text-transform: uppercase; letter-spacing: 0.04em; margin: 0 0 0.25rem; }
.settings-row-value { margin: 0; }
.settings-row-main { flex: 1; }
.settings-row-action { font-size: 0.78rem; background: transparent; border: 1px solid var(--rule);
  color: var(--ink-soft); padding: 0.3rem 0.7rem; border-radius: 7px; cursor: pointer; flex-shrink: 0; }
.settings-row-action:hover { border-color: var(--ink); color: var(--ink); }
.settings-row-action.primary { border-color: var(--accent); color: var(--accent); }
.settings-row-action.primary:hover { background: var(--accent); color: #FFFFFF; }
.toggle-switch { width: 2.4rem; height: 1.4rem; border-radius: 999px; border: 1px solid var(--rule);
  background: var(--surface); position: relative; cursor: pointer; flex-shrink: 0; }
.toggle-switch::after { content: ""; position: absolute; top: 1px; left: 1px; width: 1.1rem; height: 1.1rem;
  border-radius: 50%; background: var(--ink-faint); transition: transform 150ms ease-out; }
.toggle-switch[aria-checked="true"]::after { transform: translateX(1rem); background: var(--accent); }

.popup-scrim { position: fixed; inset: 0; background: var(--popup-scrim);
  backdrop-filter: blur(3px); -webkit-backdrop-filter: blur(3px); z-index: 40;
  display: none; align-items: center; justify-content: center; padding: 1.25rem; }
.popup-scrim.open { display: flex; }
.popup-card { background: var(--surface); border-radius: var(--radius); padding: 1.5rem 1.35rem;
  max-width: 26rem; width: 100%; box-shadow: 0 8px 28px rgba(43,38,33,0.18); }
.popup-card h3 { font-family: var(--font-display); font-size: 1.15rem; margin: 0 0 1rem; }
.popup-card label { display: block; font-size: 0.78rem; color: var(--ink-faint); margin: 0 0 0.35rem; }
.popup-card textarea, .popup-card input[type="text"] { width: 100%; font-family: var(--font-sans);
  font-size: 0.92rem; color: var(--ink); background: var(--bg); border: 1px solid var(--rule);
  border-radius: 8px; padding: 0.6rem 0.75rem; margin-bottom: 1rem; resize: vertical; }
.popup-actions { display: flex; justify-content: flex-end; gap: 0.6rem; margin-top: 0.5rem; flex-wrap: wrap; }
.popup-note { font-size: 0.8rem; color: var(--ink-faint); margin: -0.5rem 0 1rem; }
.popup-status { font-size: 0.82rem; color: var(--ink-faint); margin: 0.5rem 0 1rem; display: none; }
.popup-status.showing { display: block; }
.popup-status.found { color: var(--moved); }
.popup-status.err { color: var(--danger); }

/* ===== The full picture (deep view) — v7 Front-Page type (DIRECTION-v5 §9).
   Same class names as before (the structure already matches the mockup); only
   the visual tokens/scale change, so the NL-12/M3 render pins stay green. ===== */
.deep-view-entry { margin-top: 0.5rem; }
.deep-view-entry a { font-size: 0.88rem; color: var(--terra); font-weight: 700; text-decoration: none; }
.deep-view-entry a:hover { color: var(--terra-deep); text-decoration: underline; }
.deep-back { font-size: 0.88rem; color: var(--ink-soft); text-decoration: none;
  display: inline-block; margin: 2rem 0 0; }
.deep-back:hover { color: var(--terra); }
.deep-title-block { margin: 0 0 0; }
.deep-eyebrow { font-family: var(--font-sans); font-size: 0.78rem; font-weight: 700;
  letter-spacing: 0.14em; text-transform: uppercase; color: var(--terra); margin: 1.6rem 0 0.5rem; }
.deep-title { font-family: var(--font-display); font-weight: 700; font-size: 2.4rem;
  line-height: 1.1; letter-spacing: -0.01em; margin: 0 0 0.7rem; max-width: 44rem; }
/* Arc continuity line — a cited context line in the title block. */
.deep-arc-line { font-size: 0.95rem; color: var(--ink-soft); max-width: 44rem;
  margin: 0 0 1rem; line-height: 1.5; }
.deep-jumplist { font-size: 0.85rem; color: var(--ink-faint); margin: 0 0 2rem;
  padding-bottom: 0.6rem; border-bottom: 1px solid var(--rule); max-width: 44rem; line-height: 1.9; }
.deep-jumplist a { color: var(--ink-soft); text-decoration: none; }
.deep-jumplist a:hover { color: var(--ink); text-decoration: underline; }
.deep-jumplist .sep { color: var(--rule); margin: 0 0.45rem; }
.deep-section { max-width: 44rem; margin: 0 0 2rem; scroll-margin-top: 1rem; }
.deep-section-label { font-family: var(--font-sans); font-size: 0.78rem; font-weight: 700;
  letter-spacing: 0.14em; text-transform: uppercase; color: var(--ink-soft); margin: 0 0 0.7rem; }
.deep-section p { margin: 0 0 0.8rem; }
.deep-section p:last-child { margin-bottom: 0; }
.deep-facts-list { margin: 0; padding-left: 1.1rem; }
.deep-facts-list li { margin: 0 0 0.55rem; }
.cite { font-size: 0.8rem; color: var(--ink-faint); }
/* "The story so far" timeline — machine-register dates + quiet significance. */
.deep-timeline-list { list-style: none; margin: 0; padding: 0; }
.tl-entry { margin: 0 0 0.9rem; }
.tl-date { font-family: var(--font-mono); font-size: 0.74rem; letter-spacing: 0.04em;
  color: var(--ink-faint); display: block; }
.tl-signif { color: var(--ink-soft); }
.tl-gap { font-size: 0.85rem; color: var(--ink-faint); font-style: italic; margin: 0 0 0.9rem; }
.deep-effect { margin: 0 0 0.85rem; }
/* v8-M1 item 4: the trailing per-paragraph source cluster in the analysis
   prose sections (mockup-v8). A quiet colophon line — plain text, no tap
   targets; the negative top margin tucks it under its paragraph. */
.src-cluster { font-size: 0.8rem; color: var(--ink-faint); margin: -0.35rem 0 0.9rem; }
.deep-source-row { border-top: 1px solid var(--rule); padding: 0.55rem 0; font-size: 0.88rem; }
.deep-source-row:first-of-type { border-top: none; }
.deep-source-row .source-outlet { font-weight: 700; margin: 0 0 0.1rem; }
.deep-source-row a { color: var(--terra); text-decoration: none; }
.deep-source-row a:hover { color: var(--terra-deep); text-decoration: underline; }
.deep-source-row .source-title { color: var(--ink-soft); font-size: 0.88rem; margin: 0 0 0.15rem; }
.deep-source-row .source-meta { color: var(--ink-faint); font-size: 0.78rem; margin: 0; }
/* NL-63 M3 (Decision B): 'The numbers' reuses the facts list; 'Unresolved'
   renders each cross-source discrepancy as two attributed sides + the note. */
.deep-numbers-list li { font-variant-numeric: tabular-nums; }
.deep-unresolved-row { padding: 0.5rem 0; border-bottom: 1px solid var(--rule); }
.deep-unresolved-row:last-child { border-bottom: none; }
.deep-unresolved-side { margin: 0 0 0.15rem; }
.deep-unresolved-side .cite { color: var(--ink-faint); font-size: 0.9em; }
.deep-unresolved-vs { color: var(--ink-faint); font-size: 0.78rem;
  text-transform: uppercase; letter-spacing: 0.06em; margin: 0.1rem 0; }
.deep-unresolved-note { color: var(--ink-soft); font-size: 0.85rem;
  font-style: italic; margin: 0.2rem 0 0; }
/* NL-68 item 5: the discrepancy sub-group folds behind a collapsed <details> by
   default (his read: mostly noise) — native, keyboard-operable, count in the
   summary; the substantive contested rows stay one click away. */
details.deep-open-discrepancies { margin: 0.4rem 0 0; }
details.deep-open-discrepancies > summary { cursor: pointer; list-style: none;
  font-size: 0.85rem; color: var(--ink-soft); padding: 0.3rem 0; }
details.deep-open-discrepancies > summary::-webkit-details-marker { display: none; }
details.deep-open-discrepancies > summary:hover { color: var(--ink); }
details.deep-open-discrepancies > summary .disc-count { font-weight: 700; color: var(--ink); }
details.deep-open-discrepancies > summary .caret { display: inline-block;
  font-size: 0.85em; transition: transform 0.12s ease; }
details.deep-open-discrepancies[open] > summary .caret { transform: rotate(90deg); }
/* NL-68 item 3 (superset law): the deep view opens with the story's own Today
   prose before the analyst sections — same reading rhythm as the Today body. */
.deep-today-prose .move-label { font-family: var(--font-sans); font-size: 0.78rem;
  font-weight: 700; letter-spacing: 0.12em; text-transform: uppercase;
  color: var(--ink-soft); margin: 1.4rem 0 0.3rem; }
.deep-today-prose > p { margin: 0 0 0.8rem; }
/* NL-66(b): the In-Brief sources-&-context view. */
.sc-tags, .sc-threads, .sc-herefor, .sc-reason { color: var(--ink-soft); font-size: 0.9rem; margin: 0 0 0.35rem; }
.sc-corrob { color: var(--ink-faint); font-size: 0.85rem; margin: 0 0 0.6rem; }
.deep-footer { font-size: 0.78rem; color: var(--ink-faint); padding-top: 1.25rem;
  margin-top: 0.5rem; border-top: 1px solid var(--rule); line-height: 1.6; }
.deep-footer p { margin: 0 0 0.4rem; }
.deep-footer p:last-child { margin-bottom: 0; }

/* Deep + archive-edition views carry no section line, so they center as a page. */
/* NL-143 item 3c: the deep / edition / thread views do NOT use .page — they are
   their own centred container and they carried the same 72rem cap. They step
   with it: a Today page that widened while its deep views stayed narrow would
   read as a layout bug on the very first click-through. */
#view-edition, section[id^="view-deep-"], section[id^="view-thread-"],
#view-runlog { max-width: 84rem; margin: 0 auto; padding: 0 2rem; }
#view-edition .view-title, #view-edition .today-grid, #view-edition .footer-tag { max-width: none; }

/* NL-149 item 2 — the generation reports. NO NEW DIRECTION: the head/title block
   are the deep view's, the step lines are item 1's .gen-log grammar reused
   verbatim (one line shape for "a step and what it took", live and after the
   fact), and the only rules here are the run block's own spacing and the
   outcome word. Machine register throughout — this is the audit trail, and it
   will be read cold, days later, about runs nobody watched (NL-146). */
.runlog-note { font-family: var(--font-mono); font-size: 0.76rem; color: var(--ink-faint);
  max-width: 44rem; margin: 0 0 1.6rem; }
.runlog-run { max-width: 44rem; margin: 0 0 1.8rem; padding: 0 0 1.4rem;
  border-bottom: 1px solid var(--rule); }
.runlog-head { display: flex; align-items: baseline; gap: 0.75rem;
  font-family: var(--font-sans); font-size: 0.78rem; font-weight: 700;
  letter-spacing: 0.14em; text-transform: uppercase; color: var(--ink-soft);
  margin: 0 0 0.35rem; }
/* The outcome is a WORD, never a colored dot or a badge — the same rule the
   memline follows. Weight and position carry it; color is never the channel. */
.runlog-head .runlog-outcome { margin-left: auto; color: var(--ink); }
.runlog-meta { font-family: var(--font-mono); font-size: 0.78rem; color: var(--ink-faint);
  margin: 0 0 0.6rem; }
.runlog-error { font-size: 0.8rem; color: var(--ink-soft); margin: 0 0 0.6rem;
  max-width: 44rem; }
.runlog-run .gen-log { margin: 0; }

/* ============================ THE ROOT STEP (NL-143 item 3c) ============================
   Approved with the container at the 07-18 polish gate. A fixed rem container
   can only ever be a shrinking FRACTION of a very large display — at 2560px,
   84rem is still about half the glass — so the honest big-display answer is
   SCALE, not more width: step the root font size and the whole rem-set page
   grows together while every measure stays constant IN CHARACTERS. Nothing
   below 1800px changes. Disjoint from the <=900px query, so the mobile column
   is untouched; everything is rem, so zoom and OS text-scaling still compose. */
@media (min-width: 1800px) { html { font-size: 17px; } }
@media (min-width: 2200px) { html { font-size: 18px; } }

/* ============================ MOBILE PASS (~390px) ============================ */
@media (max-width: 900px) {
  .page { padding: 0 1.15rem; }
  #view-edition, section[id^="view-deep-"], section[id^="view-thread-"],
  #view-runlog { padding: 0 1.15rem; }
  /* Item 1 is DESKTOP; below 900px the single column stays, DOM = rank order.
     Reset the grid placement so every slot stacks 1→N in one column. */
  .today-grid { grid-template-columns: 1fr; gap: 0; }
  .today-grid > .grid-lead, .today-grid > .grid-col-a, .today-grid > .grid-col-b {
    grid-column: auto; grid-row: auto; }
  .today-grid > .grid-lead { border-bottom: 1px solid var(--ink);
    padding-bottom: 1.4rem; margin-bottom: 0.4rem; }
  /* NL-143 item 3a: one column means the strips stack CONTIGUOUSLY by rank, so
     the second column leg's slug would read as a stutter mid-run. Only the
     rank-first slug survives here; the server marks every later one. */
  .brief-slug--secondary { display: none; }
  .dateline { font-size: 2.6rem; }
  .signature { font-size: 1.1rem; }
  .dispatch-strip { font-size: 0.74rem; }
  .lead h2.headline { font-size: 2.5rem; line-height: 1.08; }
  .section-line a { margin-right: 1.1rem; }
  .deep-title { font-size: 1.8rem; }
  /* v7-M2 surfaces */
  .page-title { font-size: 2.1rem; }
  .view-line a { margin-right: 1.1rem; }
  .month-title { font-size: 2.2rem; }
  h2.month-title { font-size: 1.9rem; }
  /* §14: stack the grid and panel (his approved base geometry, exact) */
  .arch-cols { grid-template-columns: 1fr; gap: 0; }
  .day-panel-stack { margin-top: 1.8rem; }
  .cal-cell { min-height: 3rem; padding: 0.25rem; }
  .cal-picked .cal-num { font-size: 1.7rem; }   /* the pick steps down on phones */
  .cal-stamp { display: none; }   /* stamps are the desktop spread's luxury */
}
"""

# The full page shell (v7 — DIRECTION-v5 §4: no chrome). The masthead ceremony,
# section line, and edition bar are rendered INTO each view by server.py (the
# dateline is per-edition, not shared chrome). Placeholders: {css} {staleness_banner}
# {today_html} {following_html} {archive_html} {settings_html} {popups_html}
# {deep_views_html} {thread_pages_html} {runlog_html} {js}
PAGE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>NewsLens</title>
<style>{css}</style>
</head>
<body>
<a class="skip-link" href="#main">Skip to today’s edition</a>
{staleness_banner}
<main id="main" tabindex="-1">
<section id="view-today" class="view active">{today_html}</section>
<section id="view-following" class="view">{following_html}</section>
<section id="view-archive" class="view">{archive_html}</section>
{deep_views_html}
{thread_pages_html}
<!-- NL-149 item 2: the generation reports, a sibling .view opened from
     Settings. Server-rendered with the page so the audit trail is there the
     moment it is asked for (and readable with the generate machinery idle). -->
{runlog_html}
<!-- NL-11: archive editions inject here as sibling .view sections so opening
     one never replaces Today; empty until an archive row is opened. -->
<div id="edition-mount"></div>
</main>
<div class="slide-scrim" id="slide-scrim" onclick="closeSettings()"></div>
<div class="slide-panel" id="slide-panel" role="dialog" aria-label="Settings" aria-hidden="true">
{settings_html}
</div>
{popups_html}
<script>{nl_labels_js}</script>
<script>{js}</script>
</body>
</html>"""

POPUPS = """
<div class="popup-scrim" id="popup-edit-note" role="dialog" aria-modal="true" aria-labelledby="popup-edit-note-title">
  <div class="popup-card">
    <h3 id="popup-edit-note-title">Edit note — <span id="edit-note-topic-name"></span></h3>
    <!-- NL-103 row 8: tightened to the two facts. The second one is a NON-EFFECT
         sentence, licensed here by §4 row 8 — it kills a false assumption about
         where the note shows up that is both likely and costly. -->
    <label for="edit-note-textarea">This note shapes future editions. It never appears in them.</label>
    <textarea id="edit-note-textarea" rows="4"></textarea>
    <div class="popup-actions">
      <button class="cta-outline" onclick="closePopup('popup-edit-note')">Cancel</button>
      <button class="cta-quiet" onclick="saveNote()">Save</button>
    </div>
  </div>
</div>
<div class="popup-scrim" id="popup-add-topic" role="dialog" aria-modal="true" aria-labelledby="popup-add-topic-title">
  <div class="popup-card">
    <h3 id="popup-add-topic-title">Add topic — <span id="add-topic-name"></span></h3>
    <!-- NL-103 row 16 (A5): topic everywhere in reader copy; the config names
         behind it never render. -->
    <p style="font-size:0.85rem;color:var(--ink-soft);margin:0 0 1rem;">Add this as a broad topic or a specific one?</p>
    <!-- NL-103 FIX-2: refusals announce (§3's refusal-loud floor). polite
         matches the house pattern used elsewhere for status regions. -->
    <p class="popup-status err" id="add-topic-status" aria-live="polite"></p>
    <div class="popup-actions">
      <button class="cta-outline" onclick="closePopup('popup-add-topic')">Cancel</button>
      <button class="cta-outline" onclick="addTopic('broad')">Add as broad</button>
      <button class="cta-quiet" onclick="addTopic('specific')">Add as specific</button>
    </div>
  </div>
</div>
<div class="popup-scrim" id="popup-add-writer" role="dialog" aria-modal="true" aria-labelledby="popup-add-writer-title">
  <div class="popup-card">
    <h3 id="popup-add-writer-title">Follow a writer</h3>
    <label for="add-writer-input">Name or publication</label>
    <input type="text" id="add-writer-input" placeholder="e.g. Byrne Hobart">
    <!-- NL-103 FIX-2: same floor — this element carries both the refusal and
         the follow receipt, so it announces either way. -->
    <p class="popup-status" id="add-writer-status" aria-live="polite"></p>
    <label for="add-writer-url">Paste a link to their feed or site</label>
    <input type="text" id="add-writer-url" placeholder="https://…/feed">
    <!-- NL-103 row 10 (C7a): the static lookup note DIED — a roadmap promise,
         and a second wording of one fact. The two field labels above carry the
         affordance; the ONE refusal-class string lives in addWriter() and fires
         only when the reader actually hits the limit. -->
    <div class="popup-actions">
      <button class="cta-outline" onclick="closePopup('popup-add-writer')">Cancel</button>
      <button class="cta-quiet" onclick="addWriter()">Follow</button>
    </div>
  </div>
</div>
<!-- NL-152: the generation-hour editor. Same popup idiom as add-topic /
     add-writer (scrim + card + label + text input + popup-actions + a
     popup-status that carries both the refusal and the receipt) — no new CSS
     and no new component. The input is type="text" and not type="number"
     because type="text" is the ONE input the design system has a token for
     (.popup-card input[type="text"]); the hour is validated on BOTH sides
     regardless, so the wider keyboard costs nothing. -->
<div class="popup-scrim" id="popup-schedule-hour" role="dialog" aria-modal="true" aria-labelledby="popup-schedule-hour-title">
  <div class="popup-card">
    <h3 id="popup-schedule-hour-title">Generation time</h3>
    <label for="schedule-hour-input">Hour of the day (0–23, your local time)</label>
    <input type="text" id="schedule-hour-input" inputmode="numeric" placeholder="6">
    <p class="popup-note">Today’s edition is generated at this hour, so it is
       ready before you open the app.</p>
    <p class="popup-status" id="schedule-hour-status" aria-live="polite"></p>
    <div class="popup-actions">
      <button class="cta-outline" onclick="closePopup('popup-schedule-hour')">Cancel</button>
      <button class="cta-quiet" onclick="saveScheduleHour()">Save</button>
    </div>
  </div>
</div>
<div class="popup-scrim" id="popup-delete-confirm" role="dialog" aria-modal="true" aria-labelledby="popup-delete-title">
  <div class="popup-card">
    <h3 id="popup-delete-title">Delete “<span id="delete-topic-name"></span>”?</h3>
    <p style="font-size:0.88rem;color:var(--ink-soft);margin:0 0 1.25rem;">This removes it permanently from your list. Past editions that mentioned it are unaffected.</p>
    <div class="popup-actions">
      <button class="cta-outline" onclick="closePopup('popup-delete-confirm')">Cancel</button>
      <!-- NL-103 row 5: ink, never danger — the inline background override is
           gone and .cta-quiet is already the ink button. The confirm's grammar
           carries the weight; color is never the channel. -->
      <button class="cta-quiet" onclick="deleteThread()">Delete</button>
    </div>
  </div>
</div>
"""

JS = """
var CURRENT_DATE = document.body.getAttribute('data-briefing-date') || '';
/* NL-11: own the scroll position across verb reloads (below) rather than
   letting the browser auto-restore/reset it. */
try { if ('scrollRestoration' in history) history.scrollRestoration = 'manual'; } catch (e) {}
function showView(name) {
  /* v7 (DIRECTION-v5 §4): the section line lives INSIDE each view, server-
     rendered with the correct aria-current, so switching the active view shows
     the right nav state — there are no bottom tabs to sync. The old navEl arg is
     dropped; every call site passes the name only. */
  document.querySelectorAll('.view').forEach(function (v) { v.classList.remove('active'); });
  var v = document.getElementById('view-' + name);
  if (v) v.classList.add('active');
  window.scrollTo(0, 0);
}
function showSub(name, el) {
  /* v7-M2 (§12.4): the Following triad is a quiet text line of real links, not
     pills — showSub toggles the sub-view and marks the current link (700 ink +
     aria-current), no fake-disabled state. */
  document.querySelectorAll('.sub-view').forEach(function (v) { v.classList.remove('active'); });
  var sv = document.getElementById('sub-' + name);
  if (sv) sv.classList.add('active');
  document.querySelectorAll('.view-line a').forEach(function (a) {
    a.classList.remove('current'); a.removeAttribute('aria-current'); });
  if (el) { el.classList.add('current'); el.setAttribute('aria-current', 'true'); }
}
function api(path, body, cb) {
  fetch(path, { method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify(body || {}) })
    .then(function (r) { return r.json(); })
    .then(function (d) { if (cb) cb(d); })
    .catch(function (e) { if (cb) cb({ok: false, error: String(e)}); });
}
/* NL-11: ONE mechanism for every verb — reload for fresh server-rendered
   counts, then land the user back on the view + sub-view + scroll they were
   in (never bounce to Today). Verbs replace location.reload() with this. */
function reloadPreservingView(expandQuiet) {
  try {
    var av = document.querySelector('.view.active');
    var view = (av && (av.id === 'view-following' || av.id === 'view-archive'))
      ? av.id.replace('view-', '') : 'today';
    var activeSub = document.querySelector('.sub-view.active');
    var sub = activeSub ? activeSub.id.replace('sub-', '') : null;
    var y = window.scrollY || window.pageYOffset ||
            document.documentElement.scrollTop || 0;
    // NL-68 item 10: a just-followed story is a brand-new thread with no delta,
    // so it lands in the COLLAPSED quiet fold (collapsed whenever any thread
    // updated this edition) — invisible after the refresh. expandQuiet carries a
    // one-shot flag so restore opens the fold and the new follow is seen.
    sessionStorage.setItem('nl-restore',
      JSON.stringify({view: view, sub: sub, y: y, expandQuiet: !!expandQuiet}));
  } catch (e) {}
  location.reload();
}
function restoreViewAfterReload() {
  var raw = null;
  try { raw = sessionStorage.getItem('nl-restore');
        sessionStorage.removeItem('nl-restore'); } catch (e) { return; }
  if (!raw) return;
  var st; try { st = JSON.parse(raw); } catch (e) { return; }
  if (!st) return;
  if (st.view && st.view !== 'today') {
    var sec = document.getElementById('view-' + st.view);
    if (sec) {
      document.querySelectorAll('.view').forEach(function (v) { v.classList.remove('active'); });
      sec.classList.add('active');
      /* v7: no bottom tabs to sync — each view carries its own section line. */
    }
  }
  if (st.sub) {
    var sv = document.getElementById('sub-' + st.sub);
    if (sv) {
      document.querySelectorAll('.sub-view').forEach(function (v) { v.classList.remove('active'); });
      sv.classList.add('active');
      document.querySelectorAll('.view-line a').forEach(function (a) {
        a.classList.remove('current'); a.removeAttribute('aria-current');
        if ((a.getAttribute('onclick') || '').indexOf("'" + st.sub + "'") >= 0) {
          a.classList.add('current'); a.setAttribute('aria-current', 'true'); }
      });
    }
  }
  // NL-68 item 10: open the quiet fold after a story-follow so the just-added
  // (delta-less, therefore quiet) thread is visible rather than buried.
  if (st.expandQuiet) {
    var fold = document.querySelector('#sub-threads details.quiet-fold');
    if (fold) fold.setAttribute('open', '');
  }
  // Defer the scroll to after layout settles (and after the browser's own
  // restoration, which we opt out of below) so the position actually sticks.
  // NL-68 item 10: CLAMP to the document height — the quiet fold collapses on
  // reload (shorter page), so an un-clamped restore could scroll past all
  // content into blank space and read as "no threads".
  if (typeof st.y === 'number' && st.y > 0) {
    var applyScroll = function () {
      var maxY = Math.max(0, document.documentElement.scrollHeight - window.innerHeight);
      window.scrollTo(0, Math.min(st.y, maxY));
    };
    requestAnimationFrame(function () { requestAnimationFrame(applyScroll); });
  }
}
/* ============================================================================
   NL-17-M1c — THE ONE FOLLOW-LINE COMPONENT (mockup-v11: the thread model).

   ONE persistent .follow-slot node (single-rendering law) mounted on FOUR
   surfaces — today card · continuation card · deep view · Following row — and
   morphed by THESE renderers on every one of them. data-mount selects the FORM
   (a card carries no acts; management surfaces carry the acts line); nothing
   else differs, which is the point: three follow treatments shipped today and
   they disagreed about state, verbs and vocabulary.

   THE MODEL (his 07-25 gate rulings):
     * ONE NOUN — thread. The CTA is "Follow this thread".
     * THE TAP COMMITS INSTANTLY, ALWAYS. /api/follow/seed writes a story-seeded
       thread locally, $0, and nothing waits on a model. The reader's act is
       never hostage to a coverage lookup and can never be refused on budget.
     * THE SETTLE IS INVISIBLE. /api/follow/settle decides only what ELSE the
       thread covers: a confident name re-aims it (announced once), anything
       less renders NOTHING and the story-scoped follow simply stands. No
       status line, no ask, no apology — all three states are dead.
     * NO STANDING BROADEN/WIDEN EXISTS. The surviving scope acts are NAMED
       swaps ("Instead: <target>") on management surfaces only.

   THE REFUSAL LAW (this milestone's trust core): A TAP MUST NEVER VANISH
   SILENTLY. Every refusal payload renders its reason, announced via aria-live.
   Routing is on the payload's refusal CLASS, never on the HTTP status — write
   refusals ride 200/ok:false and the coverage refusal rides 409, so a
   status-shaped test misses half of them:
     R-WRITE     nothing was followed        -> ○, loud, replaces the line
     act-level   the follow STANDS, only the act failed -> the ● state line is
                 left UNTOUCHED and the reason renders beneath it. No ○ ever:
                 "nothing followed" over a live follow is the same lie in the
                 other direction.
     R-COVERAGE  only the broadening was refused -> renders NOTHING. The
                 thread stands under its own name, which is the whole
                 disclosure. (NL-17 M1: this used to say the "— this story" row
                 qualifier was — that qualifier is buried by amendment (i), and
                 a comment claiming a mechanism that no longer exists is the
                 gate-F5 class. The RENDER is unchanged: nothing, either way.)
   An ok:false with no class at all (a transport failure, an unmapped raise)
   falls to the frame's fallback arm — words, never silence.

   THE LINE BETWEEN THE TWO LAWS (gate ruling, F1). The refusal law above binds
   READER ACTS — follow, switch, narrow, unfollow. A tap must never vanish, so
   a class-less failure still renders the §2.1.1 required fallback rather than
   reverting in silence. The SETTLE is not a reader act: it is the system's own
   background lookup, the reader asked for nothing beyond the tap they already
   got, and his ruling ④ makes "the story-scoped follow stands, silently" the
   named failure state. So flSettle NEVER routes here, for any payload and any
   class — see its own gate. Everything else on this surface does. */
function flEsc(s) {
  return String(s == null ? '' : s)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}
function flSlot(el) { return el.closest ? el.closest('.follow-slot') : null; }
function flWhen(slot) {
  return slot.getAttribute('data-briefing-date') || CURRENT_DATE;
}
function flDA(slot, name) { return slot.getAttribute('data-' + name) || ''; }
function flMount(slot) { return flDA(slot, 'mount') || 'card'; }
/* render a compact qualifier ("Volkswagen (company)") as name-bold + quiet class;
   a bare name renders bold with no parenthetical (storyline states its class). */
function flQualified(disclosure) {
  var s = String(disclosure || ''), m = s.match(/^(.*) \\(([^()]*)\\)$/);
  if (m) return '<strong>' + flEsc(m[1]) + '</strong> <span class="oq">('
    + flEsc(m[2]) + ')</span>';
  return '<strong>' + flEsc(s) + '</strong>';
}
function flOtherAltitude(a) { return a === 'entity' ? 'storyline' : 'entity'; }
/* the thread's reader-facing NAME — for accessible names and receipts. Never a
   pronoun: an unnamed story-seeded thread falls back to the class noun. */
function flName(slot) {
  return flDA(slot, 'disclosure') || flDA(slot, 'topic')
    || NL_LABELS.threadSelf;
}
/* FIX-2 (focus continuity, fix loop 1): every morph replaces .follow-slot
   innerHTML, destroying whatever child held focus (pre-fix: activeElement fell
   to <body>). Roving tabindex on the PERSISTENT slot — before each morph, if
   focus sits on a child about to be destroyed, move it to the slot so focus is
   never dropped. The slot's aria-live announces the new state without a focus
   jump to a specific child (the mockup's "announced without stealing focus").
   Structural pin only; live keyboard verification is QA's re-check. */
function flHold(slot) {
  slot.setAttribute('tabindex', '-1');
  var a = document.activeElement;
  if (a && a !== slot && slot.contains(a)) {
    try { slot.focus({ preventScroll: true }); } catch (e) { slot.focus(); }
  }
}
/* ============================================================================
   NL-143 ITEM 1 — THE CROSS-MOUNT SWEEP (his 2026-08-07 repro, born red).

   THE BUG. Today and EVERY deep view live in ONE document as client-toggled
   sections, so a followed story has several .follow-slot nodes on the page at
   the same time. The SERVER gets this right: every mount renders through one
   predicate (_follow_recognition) at load, so at load they always agree. The
   CLIENT did not: each renderer morphed only the node that was tapped, and the
   others kept whatever the last page load had put in them. His repro, exactly:
     (a) follow on a card -> open the deep view -> the deep mount still says
         "Follow this thread" for a thread he just followed;
     (b) unfollow in the deep view -> go back to Today -> the card still says
         "Following".
   Nothing was ever wrong in the DATA — the deep-view re-tap hit the seed's XOR
   guard and got the EXISTING row back, no second follow. The lie was pixels.
   That is what made it dangerous: a display-only lie is the kind a reader
   believes, because nothing downstream ever contradicts it.

   THE FIX IS ONE MECHANISM, NOT FOUR. Every state-changing response goes
   through flCommitAll / flRestAll, which morph the acting node and then sweep
   EVERY other slot on the same thread, rendering each through the SAME
   renderers — which already branch on data-mount, so each swept node comes back
   in ITS OWN form (card verb, deep state line + acts, row acts). Per-verb copies
   of this would rot apart exactly the way the four mounts just did.

   MATCHING IS ON THE STABLE KEYS, NEVER THE NEW NAME. A confident settle
   RENAMES the thread: the acting node's data-topic becomes the settled name
   while every other mount still carries the seeded one, so matching on topic
   alone would sweep nothing at the precise moment the page most needs it.
   Identity is therefore data-story (the card's canonical story topic),
   data-origin (the seed headline) and data-topic (the stored follow name),
   captured BEFORE the morph. Values are compared case-insensitively because
   _follow_recognition matches against a lowercased active-topic set.

   AND THE COMPARISON IS TYPED (NL-143 fix loop 1, QA F-2). This used to be a
   flat SET union — any key of A equal to any key of B meant "same thread" — and
   the cross-type crossing in that union was not the harmless display glitch it
   was accepted as. QA's repro: A = story "Chip exports" / headline "US tightens
   chip exports"; B = a DIFFERENT story titled "US tightens chip exports" about
   port talks. A.origin met B.story, the sweep rendered "● Following —
   Semiconductor policy" over a ports story, the server rested it again on
   reload (recognition has no such key), and worse — B's contaminated deep mount
   carried A's ACTS, so tapping its "this story" rung re-aimed the reader's real
   chip follow onto the ports story. A display lie with live acts behind it.
   So: story meets story, origin meets origin, topic meets topic — plus topic
   against story BOTH WAYS, which is the post-commit stored-name case (a
   Following row stamps the thread name as data-story AND data-topic, and a
   renamed follow's data-topic is that stored name). ONLY the cross-type
   crossing stops sweeping. Everything the sweep is FOR still matches on a
   same-type key: same-story mounts share data-story and data-origin exactly,
   so the settle RENAME still converges (keys are captured pre-morph, and
   flRenderCommitted rewrites data-topic only — never data-story/data-origin,
   which is why those two are the stable spine); exact-duplicate stories still
   sync on story↔story; rows still match on topic; cross-edition mounts of one
   thread still match on the stored topic.

   NL-17 M1 / F-6 — MARKS JOIN THE KEY SET, and they join it TYPED. A tracked
   marker is now a .follow-slot (server._tracked_marker_html) whose identity is
   the THREAD NAMES it matched — data-marks, a newline-joined list because one
   story can match several threads. Marks are topic-typed, so they meet marks
   and they meet topics, and nothing else: the cross-type crossing fix loop 1
   landed stays exactly as narrow as it was. */
function flIdentity(slot) {
  var keys = {}, names = ['story', 'origin', 'topic'], i, v;
  for (i = 0; i < names.length; i++) {
    v = flDA(slot, names[i]);
    keys[names[i]] = v ? v.toLowerCase() : '';
  }
  keys.marks = [];
  v = flDA(slot, 'marks');
  if (v) {
    var parts = v.split(/\\r?\\n/);
    for (i = 0; i < parts.length; i++) {
      if (parts[i]) keys.marks.push(parts[i].toLowerCase());
    }
  }
  return keys;
}
function flKeyEq(a, b) { return !!a && a === b; }
/* any mark of A against any mark of B, and either side's marks against the
   other's topic — all topic-typed comparisons (see flIdentity's note). */
function flMarksMeet(a, b) {
  var i, j;
  for (i = 0; i < a.marks.length; i++) {
    if (flKeyEq(a.marks[i], b.topic)) return true;
    for (j = 0; j < b.marks.length; j++) {
      if (flKeyEq(a.marks[i], b.marks[j])) return true;
    }
  }
  for (i = 0; i < b.marks.length; i++) {
    if (flKeyEq(b.marks[i], a.topic)) return true;
  }
  return false;
}
function flSameThread(keys, slot) {
  var id = flIdentity(slot);
  return flKeyEq(keys.story, id.story)
    || flKeyEq(keys.origin, id.origin)
    || flKeyEq(keys.topic, id.topic)
    || flKeyEq(keys.topic, id.story)
    || flKeyEq(keys.story, id.topic)
    || flMarksMeet(keys, id);
}
/* THE SWEEP ITSELF — the only place that walks the document, and after F-6 it
   walks TWO node families under ONE predicate.

   THE GATE'S FINDING (nl143-gate.md:174, routed here): the sweep truthened a
   Following row's follow-SLOT and left the row's h2 chrome — the thread name
   and its altitude qualifier — reading the OLD name after a cross-surface
   rename. Both nodes are derived from the same follow state; only one of them
   was a .follow-slot, so only one got swept. A settle-rename produced the same
   split, from the other direction.

   The fix is not a second sweep with a second matching rule — that is how the
   four mounts drifted apart in the first place. It is ONE walk, ONE predicate
   (flSameThread), and a renderer chosen by what the node IS: slots morph
   through the state renderers; [data-follow-name] chrome re-truthens its text.
   `chromeFn` is optional, so callers that have nothing to say to chrome (there
   are none today, and the argument exists so a future one cannot quietly
   half-sweep) simply pass one function. */
function flSyncOthers(slot, keys, fn, chromeFn) {
  var all = document.querySelectorAll('.follow-slot, [data-follow-name]'), i, o;
  for (i = 0; i < all.length; i++) {
    o = all[i];
    if (o === slot) continue;
    if (!flSameThread(keys, o)) continue;
    if (o.hasAttribute('data-follow-name')) {
      if (chromeFn) chromeFn(o);
    } else {
      fn(o);
    }
  }
}
/* THE ALTITUDE QUALIFIER, client side — the twin of server._altitude_qualifier
   _html, and it must render the same bytes for the same row or a rename makes
   the chrome disagree with itself on reload. Two arms only, because NL-17 M1
   deleted the third: a disclosure carrying a "(class)" renders the quiet
   parenthetical; everything else renders BARE. The narrow arm ("— this story")
   is gone with amendment (i) — not replaced, removed. */
function flAltQualifier(altitude, disclosure) {
  var m = String(disclosure || '').match(/^(.*) \\(([^()]*)\\)$/);
  if (!m) return '';
  return ' <span class="alt-q">(' + flEsc(m[2]) + ')</span>';
}
/* FOLLOW-STATE-DERIVED CHROME, re-truthened (F-6). A Following row's h2 carries
   the thread's NAME and its altitude qualifier, both read off the follow — so a
   rename anywhere on the page (a reader switch, or a settle naming the thread
   two seconds after the tap) makes this heading stale, and it is not a
   .follow-slot so nothing used to walk it.

   It rewrites the LINK's inner content only. The <a>, its onclick, its title and
   the heading itself are untouched: the row's single action still opens the same
   thread, because a rename does not move a thread — it renames one (the mutation
   law). The identity keys are re-stamped so a SECOND rename in the same session
   still finds this node. */
/* the BARE name out of a compact qualifier: "OpenAI (company)" -> "OpenAI".
   The twin of follow_altitude.split_qualifier's first element, and the reason
   the server's h2 renders a name and a class rather than a class twice. */
function flNameOnly(s) {
  var m = String(s || '').match(/^(.*) \\(([^()]*)\\)$/);
  return m ? m[1] : String(s || '');
}
function flRenderChrome(node, topic, altitude, disclosure) {
  var a = node.querySelector('a');
  if (!a) return;
  // FIX LOOP 2 / QA F-5 — THE TWIN DIVERGENCE. This used to compose
  // `disclosure` PLUS the qualifier derived from that same disclosure, so an
  // entity rename rendered "OpenAI (company) (company)" live and disagreed with
  // the server on reload (which self-healed to "OpenAI (company)" — a display
  // lie that repaired itself, the hardest kind to notice).
  //
  // The server twin composes the row's STORED TOPIC + one qualifier
  // (_thread_name_link with _altitude_qualifier_html), and the stored topic is
  // already the split head — `_settle_onto` stores split_qualifier(disclosure).
  // So `topic` is the right name here and the qualifier renders exactly once.
  // The fallback splits the disclosure itself rather than using it whole, so a
  // caller that has no topic still cannot double the class.
  var name = topic || flNameOnly(disclosure);
  a.innerHTML = flEsc(name) + flAltQualifier(altitude, disclosure);
  if (topic) {
    node.setAttribute('data-topic', topic);
    node.setAttribute('data-story', topic);
  }
}
/* COMMIT, EVERYWHERE. The single entry for follow / settle / switch:
   whatever moved the thread, every mount of it now says the same thing.
   `kept` is passed to the ACTING node ONLY, and that is deliberate: the resume
   clause ("picking up 4 entries") is a RECEIPT of the reader's act, not a
   property of the thread. Rendering it on three other mounts would announce one
   act four times and leave it standing on surfaces the reader never touched.

   NL-17 M1 / F-6: the same call now also carries the SETTLE-RENAME to the row's
   h2 chrome. Nothing new decides when — the settle already routes through here
   (flSettle -> flCommitAll), so the rename reaches slots and chrome on the one
   sweep, which is the point of there being one. */
function flCommitAll(slot, topic, altitude, disclosure, altLabel, kept) {
  var keys = flIdentity(slot);
  flRenderCommitted(slot, topic, altitude, disclosure, altLabel, kept);
  flSyncOthers(slot, keys, function (o) {
    flRenderCommitted(o, topic, altitude, disclosure, altLabel);
  }, function (o) {
    flRenderChrome(o, topic, altitude, disclosure);
  });
}
/* UNFOLLOW, EVERYWHERE. Same asymmetry, same reason: the acting surface shows
   the ~3s receipt (the announcement of what the reader just did) and every
   other mount goes straight to rest. Four simultaneous receipts would be four
   claims that four separate unfollows happened.

   NO chromeFn (F-6, and this is a ruling not an omission): an unfollowed
   thread's Following row still carries that thread's NAME, and the name is
   still true — the row is a stale LISTING, not a false statement, and it is
   re-rendered from the server on the next Following open. Rewriting it here
   would be inventing an "unfollowed row" treatment nobody ruled on. The node
   that WAS lying — the tracked marker, which claims a follow that no longer
   exists — is a .follow-slot now, so it rests through the ordinary renderer. */
function flRestAll(slot, name) {
  var keys = flIdentity(slot);
  flReceipt(slot, name);
  flSyncOthers(slot, keys, flRenderResting);
}
function followTap(btn) {
  var slot = flSlot(btn);
  if (!slot) return;
  var state = slot.getAttribute('data-state');
  // A committed line has nothing to expand any more: cards are doors to the
  // deep view (his item 4 took Unfollow off cards, so re-expanding to an
  // actless sentence would be a click with no answer) and management surfaces
  // are always expanded already.
  if (state === 'committed' || state === 'expanded') return;
  flFollow(slot);
}
/* THE TAP. Commits instantly — the line flips to Following before anything
   external is consulted, because nothing external is consulted. */
function flFollow(slot) {
  var origin = flDA(slot, 'origin') || flDA(slot, 'topic');
  flHold(slot);
  slot.setAttribute('data-origin', origin);
  slot.setAttribute('aria-live', 'polite');
  api('/api/follow/seed',
    { topic: flDA(slot, 'topic'), origin: origin, briefing_date: flWhen(slot) },
    function (d) {
      if (!d || d.ok === false) return flRefused(slot, d, 'follow');
      flCommitAll(slot, d.topic, d.altitude, d.disclosure, d.alt_label,
                  d.resumed ? d.kept : null);
      // WHETHER A SETTLE MAY RUN IS THE SERVER'S ANSWER (NL-17 M1), read off
      // `settle` — not the client's inference from `seeded`. It used to be
      // `d.seeded === true`: only a brand-new thread settled. That was right
      // while the only settle was the one at birth, and it is wrong now that a
      // case-(a) thread stays AUTO-WIDENABLE — a later story rejoining a thread
      // that never found an actor is exactly when the ruling wants another
      // attempt, and case (b)'s ONE bounded retry rides the same question. Both
      // depend on the settle history and on a spend bound, neither of which a
      // browser can be trusted to know. A thread whose scope someone already
      // decided still never re-settles: the server answers false.
      if (d.settle === true) flSettle(slot, origin);
    });
}
/* THE SETTLE — invisible by contract, and that is a POSITIVE gate: the ONLY
   thing this leg may ever render is a landed name. Every other outcome —
   unsettled, refused, transport-dead, a 500 with no class at all — renders
   NOTHING, because by the time this runs the reader's follow is already
   committed and already on screen saying so.

   THE BUG THIS GATE CLOSES (gate F1 / QA-1, reproduced on shipped code): the
   old two-guard sequence sent any class-less ok:false here into flRefused with
   verbKey 'follow', which rendered "○ Didn't follow" OVER A COMMITTED FOLLOW —
   three falsehoods at once (the mark, an unwind that never happened, and a
   memory-file reason for what was a dropped connection). The settle had been
   mis-filed under the reader-act law. It is a SYSTEM act: the reader asked for
   nothing beyond the tap, and his ruling ④ already names the failure state —
   the story-scoped follow simply stands, silently. */
function flSettle(slot, origin) {
  var seeded = flDA(slot, 'topic');
  api('/api/follow/settle',
    { topic: seeded, origin: origin, topic_current: seeded,
      briefing_date: flWhen(slot) },
    function (d) {
      if (!d || d.ok !== true || d.settled !== true) return;
      // THE RENAME CASE the sweep's key union exists for: this response gives
      // the thread a NEW name, and every other mount is still carrying the
      // seeded one. flCommitAll captured the identity before this morph.
      flCommitAll(slot, d.topic, d.altitude, d.disclosure, d.alt_label);
    });
}
/* THE CLASS ROUTER — the one place a refusal is dispatched, and it reads the
   PAYLOAD'S CLASS, never the call it came back from and never an HTTP status.
   Write refusals ride 200/ok:false; the coverage refusal rides 409; both land
   here and they carry OPPOSITE marks, so the class is the only honest key. */
function flRefused(slot, d, verbKey) {
  // R-COVERAGE — the follow STANDS; only the broadening was refused. Renders
  // NOTHING: the thread stands under its own name (NL-17 M1 — the "— this
  // story" qualifier this line used to name is buried by amendment (i)).
  if (d && d.refusal === 'coverage') return;
  // R-WRITE on the FOLLOW itself — nothing was followed. ○, loud.
  if (verbKey === 'follow') return flRenderRefusal(slot, d, verbKey);
  // R-WRITE on an act over a STANDING follow — the state line is untouched and
  // the reason renders beneath it. No ○: something IS followed.
  return flActRefusal(slot, d, verbKey);
}
/* THE COMMITTED LINE. Card: the compact steady verb (a door to the deep view
   where one exists). Deep view: the full state line + the acts line. Following
   row: acts only — the row's own title is the object, so a state line would be
   a second rendering of a fact the row already states. */
function flRenderCommitted(slot, topic, altitude, disclosure, altLabel, kept) {
  flHold(slot);
  slot.setAttribute('data-topic', topic || '');
  slot.setAttribute('data-altitude', altitude || '');
  slot.setAttribute('data-alt-label', altLabel || '');
  slot.setAttribute('data-disclosure', disclosure || '');
  var mount = flMount(slot), name = flName(slot);
  if (mount === 'row') {
    slot.setAttribute('data-state', 'committed');
    slot.innerHTML = flActsLine(slot, name);
    return;
  }
  if (mount === 'card') {
    slot.setAttribute('data-state', 'committed');
    slot.innerHTML = flSteadyVerb(slot, disclosure, altitude);
    return;
  }
  slot.setAttribute('data-state', 'expanded');
  var object = (disclosure && altitude !== 'narrow')
    ? flQualified(disclosure)
    : '<strong>' + flEsc(NL_LABELS.threadSelf) + '</strong>';
  var resume = '';
  if (typeof kept === 'number' && kept > 0) {
    // the resume clause renders ONCE, at re-follow — saying it is what keeps an
    // October re-follow from feeling like a haunting.
    resume = '<span class="fl-status">' + flEsc(NL_LABELS.resumedPrefix) + ' '
      + kept + ' ' + flEsc(kept === 1 ? NL_LABELS.resumedEntry
                                      : NL_LABELS.resumedEntries) + '</span>';
  }
  slot.innerHTML = '<span class="fl-sentence">'
    + '<span class="fl-dot" aria-hidden="true">' + flEsc(NL_LABELS.dotOn)
    + '</span> ' + flEsc(NL_LABELS.committedVerb) + ' ' + object + '</span>'
    + flActsLine(slot, name) + resume;
}
/* the card's steady verb — a DOOR to the deep view when this story has one
   (the server stamps the slug), otherwise a plain statement of the fact.

   THREE ARMS, and they are the SERVER'S three (gate F9). This is the twin of
   server._committed_verb_inner, and the two must render the same bytes for the
   same row — the single-rendering law is worth nothing if a card's line changes
   text on reload. The arms:

     narrow      -> "Following — this thread"   (the story-seeded steady form)
     disclosure  -> "Following — <qualified>"   (what the settle named)
     neither     -> "Following", BARE           (unmigrated: nothing settled,
                                                 so nothing is claimed)

   The bare arm is the one that matters. It used to be conflated into the narrow
   arm by `|| !disclosure`, which was harmless while the seed flow made those two
   coincide — and became reachable the moment F4 let a pre-0019 legacy row resume
   at its own empty altitude. Rendering "— this thread" there asserts a
   story-scoped relation for a thread whose scope was never stated: the exact
   fabrication the server's bare arm exists to refuse. */
function flSteadyVerb(slot, disclosure, altitude) {
  var inner = '<span class="fl-dot" aria-hidden="true">'
    + flEsc(NL_LABELS.dotOn) + '</span> ';
  if (altitude === 'narrow') {
    inner += flEsc(NL_LABELS.steadyPrefix) + ' '
      + flEsc(NL_LABELS.threadSelf);
  } else if (disclosure) {
    inner += flEsc(NL_LABELS.steadyPrefix) + ' ' + flQualified(disclosure);
  } else {
    inner += flEsc(NL_LABELS.committedVerb);
  }
  var deep = flDA(slot, 'deep-slug');
  if (!deep) return '<span class="deck-follow">' + inner + '</span>';
  var ret = flDA(slot, 'deep-return');
  var call = "openDeepView('" + flEsc(deep) + "', event"
    + (ret && ret !== 'view-today' ? ", '" + flEsc(ret) + "'" : "") + ")";
  return '<a class="deck-follow" href="#" onclick="' + call + '">' + inner
    + '</a>';
}
/* THE ACTS LINE — management surfaces only, and the whole surviving
   scope-affordance law. Every act NAMES its target; no bare directional verb
   exists. The "Instead:" prefix renders only when a candidate does — a prefix
   with nothing after it is a broken sentence.

   FIX LOOP 2b: the worded-fallback arm is deleted (RECONVENE-2, ruling (b)).
   With no NAMED target there is no anchor, no prose, no aria action and no
   "Instead:" label — the row renders exactly Unfollow. The deleted strings are
   deliberately not quoted anywhere in this emitted script: they are retired in
   labels.py, and a comment naming them would put them back into the shipped
   bytes the ruling says to clear.

   NL-17 M1 — THE NARROW RUNG IS DELETED (twin of server._follow_acts_line, and
   the two must stay twins: this is the same acts line rendered by the other
   half of the single-rendering law). It offered "this story" as a choice on
   every settled thread, which is exactly what the principal's 2026-08-07
   amendment (i) kills. No replacement rung — the council ruled it out without
   one. flPickNarrow went with it: an unreachable handler that can still perform
   a banned write is a door, not dead code. */
/* THE TWIN of server._swap_targets (FIX LOOP 1): the thread's known candidates
   minus wherever it is aimed now. Same rule, same order, same subtraction — and
   the subtraction is what makes "the prior aim joins the swap targets after any
   swap" true without anything having to remember history: the candidate list
   does not change when the reader swaps, so the rung they just left is still in
   it and is no longer the aim, so it renders. The SEED is never a target: it is
   not in the list, because amendment (i) bans offering it back. */
function flSwapTargets(slot) {
  var aim = flDA(slot, 'disclosure').toLowerCase(), out = [], seen = {}, i;
  var raw = flDA(slot, 'candidates'), list = [];
  if (raw) { try { list = JSON.parse(raw) || []; } catch (e) { list = []; } }
  for (i = 0; i < list.length; i++) {
    var c = list[i] || {};
    var disc = String(c.disclosure || ''), a = String(c.altitude || '');
    var key = disc.toLowerCase();
    if (!disc || (a !== 'entity' && a !== 'storyline')) continue;
    if (key === aim || seen[key]) continue;
    seen[key] = 1;
    out.push({ altitude: a, disclosure: disc });
  }
  if (out.length) return out;
  var label = flDA(slot, 'alt-label');
  if (label && label.toLowerCase() !== aim) {
    return [{ altitude: flOtherAltitude(flDA(slot, 'altitude')),
              disclosure: label }];
  }
  return [];
}
function flActsLine(slot, name) {
  var bits = [], targets = flSwapTargets(slot), i, t;
  for (i = 0; i < targets.length; i++) {
    t = targets[i];
    bits.push('<a href="#" data-alt="' + flEsc(t.altitude)
      + '" data-disc="' + flEsc(t.disclosure)
      + '" aria-label="' + flEsc('Switch to ' + t.disclosure + ' — ' + name)
      + '" onclick="flSwitch(this); return false;">'
      + flQualified(t.disclosure) + '</a>');
  }
  // FIX LOOP 2b: the worded-fallback arm is DELETED here too (twin of
  // server._follow_acts_line — the two must render the same bytes or a reload
  // changes what the reader is offered). With no named target there is no
  // anchor, no prose, no aria action and no "Instead:" label: the row renders
  // exactly Unfollow.
  var prefix = bits.length ? flEsc(NL_LABELS.insteadPrefix) + ' ' : '';
  bits.push('<button class="fl-unfollow" type="button" aria-label="'
    + flEsc(NL_LABELS.unfollow + ' ' + name)
    + '" onclick="flUnfollow(this)">' + flEsc(NL_LABELS.unfollow)
    + '</button>');
  return '<span class="fl-alts">' + prefix
    + bits.join('<span class="sep">·</span>') + '</span>';
}
function flRenderResting(slot) {
  flHold(slot);
  slot.setAttribute('data-state', 'resting');
  // A card is not a live region at rest — it is one only for the duration of
  // the act the reader performed on it. The MANAGEMENT mounts are different:
  // the server renders them aria-live from the start (_follow_slot_html), so
  // stripping it here would silently downgrade a surface the server built as a
  // live region. That mattered little while only the tapped node ever changed;
  // with the NL-143 sweep, resting is now something that happens to a deep
  // mount because of a tap on a CARD, and the mount has to keep announcing.
  if (flMount(slot) === 'card') slot.removeAttribute('aria-live');
  slot.removeAttribute('data-altitude');
  slot.removeAttribute('data-alt-label');
  slot.removeAttribute('data-disclosure');
  // F-6: a TRACKED marker resting is the remote-unfollow case — the thread it
  // was matched against is gone, so its marks are no longer identity. Leaving
  // them would keep this node syncing on a thread the reader dropped, which is
  // the same false claim one layer in from the one we just removed.
  slot.removeAttribute('data-marks');
  // FIX LOOP 1: an unfollowed thread has no aim to swap, so its candidates are
  // not an affordance any more. Leaving them would offer "Instead:" beside a
  // thread the reader just dropped.
  slot.removeAttribute('data-candidates');
  // R1 (fix loop 2): the committed render re-stamped data-topic to the STORED
  // follow NAME; on unfollow, restore the card's canonical STORY topic (stamped
  // by the server as data-story) so a re-tap follows the STORY — not the stale
  // follow name — and stores the canonical origin (the 0021 reload bridge).
  var story = flDA(slot, 'story');
  if (story) {
    slot.setAttribute('data-topic', story);
    if (!flDA(slot, 'origin')) slot.setAttribute('data-origin', story);
  }
  slot.innerHTML = flRestingButton(slot);
}
/* THE RESTING CTA, in ONE builder — the twin of the server's own resting arm
   (_follow_control / _follow_slot_html). Two renderers now mount it: rest
   (flRenderResting) and refusal (flRenderRefusal, F-4), and a third hand-written
   copy is exactly how the four follow mounts drifted apart before NL-143. It
   returns markup rather than writing innerHTML so a caller can compose it under
   other content — which is the whole shape of the F-4 fix. */
function flRestingButton(slot) {
  return '<button class="deck-follow not-following" type="button" '
    + 'aria-expanded="false" aria-label="'
    + flEsc(NL_LABELS.followInactiveAria + ' — '
            + (flDA(slot, 'story') || flDA(slot, 'topic')))
    + '" onclick="followTap(this)">'
    + flEsc(NL_LABELS.followInactive) + '</button>';
}
/* R-WRITE — nothing was followed, so the mark is ○ and the line is LOUD. The
   reason and remedy come from the PAYLOAD: the branch that produced the failure
   is the branch that names it, so a copy re-pin can never leave the render
   describing a different condition than the one that fired.

   ===========================================================================
   NL-17 M1 / F-4 — THE REFUSED MOUNT KEEPS ITS CONTROL.

   THE BUG (nl143-gate.md:171, routed here as M1 input; pre-existing, untouched
   by that batch). This renderer replaced the whole slot with two <p>s and
   stopped. The reason rendered — the refusal law was satisfied — but the
   FOLLOW CONTROL WAS GONE, and nothing put it back short of a page reload. A
   reader whose memory.md was briefly unwritable lost the ability to follow that
   story for the rest of the session, and lost it silently: the surface looked
   like a finished explanation rather than a dead end.

   That is the same never-vanishes law the whole M1c milestone is built on,
   failing one case out. A tap must never vanish — and a tap whose ONLY outcome
   is an epitaph has vanished, just more politely.

   THE FIX. Render the reason AND re-mount the resting CTA beneath it, so a
   re-tap re-runs the act against the server:
     * REFUSED (a policy arm — unreadable/unparseable memory.md): the re-tap
       RE-ADJUDICATES server-side and honestly refuses again. That is not a
       pointless loop, it is the truth staying true; the reader can fix the file
       and try again without hunting for a reload.
     * DECLINED (a transient arm — unwritable, transport): the re-tap simply
       retries and succeeds once the condition clears.
   The client cannot and must not tell those apart — which arm fired is the
   SERVER's adjudication, and re-asking is how you find out. So there is one
   behaviour here, not two, and it is correct for both.

   data-state stays 'refused' (the CSS block-level rule and any state-shaped
   test still see the refusal; followTap only short-circuits on committed /
   expanded, so the button is live). The button is built by flRestingButton —
   the SAME builder flRenderResting uses — because two hand-written copies of
   the resting CTA is how the four mounts drifted apart in the first place. */
function flRenderRefusal(slot, d, verbKey) {
  flHold(slot);
  slot.setAttribute('data-state', 'refused');
  slot.setAttribute('aria-live', 'polite');
  var frame = flRefusalFrame(verbKey);
  var r = flRefusalReason(d);
  slot.innerHTML = '<p class="fl-refusal">'
    + '<span class="fl-dot-off" aria-hidden="true">' + flEsc(NL_LABELS.dotOff)
    + '</span> ' + flEsc(frame) + ' ' + flEsc(r.reason) + '.</p>'
    + '<p class="fl-refusal-why">' + flEsc(r.remedy) + '</p>'
    + flRestingButton(slot);
}
/* ACT-LEVEL refusal — the follow STANDS. The state line above is left exactly
   as it was (a refusal never unwinds an existing follow) and the reason renders
   beneath it, announced role="status". A repeat REPLACES rather than stacks. */
function flActRefusal(slot, d, verbKey) {
  // NO flHold here (gate F6): this renderer APPENDS a node and destroys
  // nothing focusable — the button the reader just pressed is still there and
  // still focused. flHold exists to rescue focus from a subtree about to be
  // replaced; calling it here would yank focus off that live button to the
  // slot on every refused act, which is a worse outcome than the one it
  // guards against. The morphing renderers (which DO replace innerHTML) keep
  // it.
  var prev = slot.querySelector('.fl-act-refusal');
  if (prev) prev.parentNode.removeChild(prev);
  var r = flRefusalReason(d);
  var note = document.createElement('span');
  note.className = 'fl-act-refusal';
  note.setAttribute('role', 'status');
  note.textContent = flRefusalFrame(verbKey) + ' ' + r.reason + '. ' + r.remedy;
  slot.appendChild(note);
}
function flRefusalFrame(verbKey) {
  if (verbKey === 'switch') return NL_LABELS.didntSwitch;
  if (verbKey === 'unfollow') return NL_LABELS.didntUnfollow;
  return NL_LABELS.didntFollow;
}
/* THE UNMAPPED ARM. A payload with no reason of its own — a transport failure,
   an unclassified raise — still renders words. Raw CLI prose never reaches a
   card (it is a good CLI string and an unlawful UI one), and a silent revert is
   the bug this milestone exists to kill. */
function flRefusalReason(d) {
  if (d && d.reason && d.remedy) return { reason: d.reason, remedy: d.remedy };
  return { reason: NL_LABELS.refusalFallback,
           remedy: NL_LABELS.refusalFallbackFix };
}
/* the SWITCH ("Instead"): the follow MOVES to the named coverage (from_topic
   set) — never a re-settle.

   FIX LOOP 1 — ONE SWAP PATH, and the target comes off the LINK. It used to be
   derived from the slot (always alt_label, always the other rung), which could
   only ever express a single alternative; the ratified "Instead:" row can now
   carry several, and a second handler for the new ones is how two handlers
   start disagreeing about what a swap is. So every target — persisted
   candidate or stored alt_label — renders as the same link shape with
   data-alt/data-disc, and this one function performs all of them.

   $0 ON THE TAP PATH: nothing here consults a model. The candidates were
   resolved at settle time; the swap is a local write.

   THE GUARD BELOW IS NOW A BACKSTOP, NOT A BEHAVIOUR. The only link that ever
   lacked a target was the worded-fallback arm, and fix loop 2b deleted it — so
   no rendered link reaches this without both attributes. The guard stays
   because a POST built from a missing name is the worse failure, and a
   defensive branch is cheap; it is no longer describing something a reader can
   tap. (The pin that used to enshrine that dead tap now asserts the state
   mounts no swap affordance at all.) */
function flSwitch(a) {
  var slot = flSlot(a);
  var cur = flDA(slot, 'topic');
  var newAlt = a.getAttribute('data-alt') || '';
  var disc = a.getAttribute('data-disc') || '';
  if (!disc || !newAlt) return;
  var newName = disc.replace(/ \\([^()]*\\)$/, '') || cur;
  api('/api/follow/at', {
    name: newName, altitude: newAlt, disclosure: disc,
    alt_label: flDA(slot, 'disclosure'), from_topic: cur,
    briefing_date: flWhen(slot)
  }, function (d) {
    if (!d || d.ok === false) return flRefused(slot, d, 'switch');
    flCommitAll(slot, d.topic, d.altitude, d.disclosure, d.alt_label);
  });
}
/* NL-17 M1: flPickNarrow is DELETED (grep-verified zero call sites at the diff).
   It was the narrow rung's handler — it POSTed altitude:'narrow' with
   source='pick', which is precisely the reader-chosen narrow follow the
   principal's 2026-08-07 amendment (i) bans. Deleting the rung from both
   renderers removed the offer; deleting this removes the act, and
   memory.PICKABLE_ALTITUDES refuses the write even if something still called
   it. Three layers, because a banned data class deserves more than a hidden
   button. */
/* SYMMETRY LAW: one-tap unfollow from the same surface, on ANY entry of a
   followed thread (records the altitude correction server-side for Axel's
   instrument). Bucket 1060 — a silent `return;` before this milestone: the
   server refused, the line still said Following, and nothing was said. */
function flUnfollow(btn) {
  var slot = flSlot(btn);
  var name = flName(slot);
  api('/api/unfollow', { topic: flDA(slot, 'topic') }, function (d) {
    if (!d || d.ok === false) return flRefused(slot, d, 'unfollow');
    flRestAll(slot, name);
  });
}
/* THE UNFOLLOW RECEIPT — announced once, ~3s, then the SAME slot reverts to the
   resting CTA. No undo (his 07-25 verdict killed A11): the control that
   replaces the receipt is the very act a reader would want next, and re-follow
   RESUMES the thread where it left off. */
function flReceipt(slot, name) {
  flHold(slot);
  slot.setAttribute('data-state', 'unfollowed');
  slot.setAttribute('aria-live', 'polite');
  var line = (name && name !== NL_LABELS.threadSelf)
    ? flEsc(NL_LABELS.unfollowedReceipt) + ' <strong>' + flEsc(name)
      + '</strong>.'
    : flEsc(NL_LABELS.unfollowedSelf);
  slot.innerHTML = '<p class="fl-receipt">' + line + '</p>';
  var ms = Number(NL_LABELS.revertMs) || 3000;
  setTimeout(function () {
    if (slot.getAttribute('data-state') === 'unfollowed') flRenderResting(slot);
  }, ms);
}
/* M9-M3: deep-view navigation — v6's lastStoryAnchor logic is the spec.
   Back-navigation restores scroll to the ORIGINATING story, not page top
   (binding: the "resume where you left off" ritual test). */
var lastStoryAnchor = null;
var lastDeepReturn = 'view-today';
function openDeepView(storyId, e, returnId) {
  if (e) e.preventDefault();
  lastStoryAnchor = storyId;
  lastDeepReturn = returnId || 'view-today';
  document.querySelectorAll('.view').forEach(function (v) { v.classList.remove('active'); });
  document.getElementById('view-deep-' + storyId).classList.add('active');
  window.scrollTo(0, 0);
}
function closeDeepView(e, returnId) {
  if (e) e.preventDefault();
  var back = returnId || lastDeepReturn || 'view-today';
  document.querySelectorAll('.view').forEach(function (v) { v.classList.remove('active'); });
  var backEl = document.getElementById(back) || document.getElementById('view-today');
  backEl.classList.add('active');
  if (lastStoryAnchor) {
    var target = document.getElementById(lastStoryAnchor);
    if (target) {
      setTimeout(function () { target.scrollIntoView({ block: 'start' }); }, 0);
    }
  }
  lastStoryAnchor = null;
}
/* v7-M2: the thread page (the "Open thread" destination) — a sibling .view like
   the deep views. openThread is fired by a Following row's name (its single
   action); closeThread returns to Following. Scroll resets to top; Following is
   restored on close. */
function openThread(tid, e) {
  if (e) e.preventDefault();
  var target = document.getElementById('view-thread-' + tid);
  if (!target) return false;
  document.querySelectorAll('.view').forEach(function (v) { v.classList.remove('active'); });
  target.classList.add('active');
  window.scrollTo(0, 0);
  return false;
}
function closeThread(e) {
  if (e) e.preventDefault();
  document.querySelectorAll('.view').forEach(function (v) { v.classList.remove('active'); });
  document.getElementById('view-following').classList.add('active');
  window.scrollTo(0, 0);
  return false;
}
function toggleFooterDisclosure(btn) {
  /* Element-relative (NL-11): Today and an open archive edition each carry a
     footer, so the toggle works off the clicked button, not a fixed id. */
  var detail = btn.parentNode.querySelector('.footer-detail');
  var expanded = btn.getAttribute('aria-expanded') === 'true';
  btn.setAttribute('aria-expanded', String(!expanded));
  if (detail) detail.classList.toggle('open', !expanded);
}
function toggleEpisode() { toggleEpisodeEl('episode-player'); }
function toggleEpisodeEl(id) {
  var el = document.getElementById(id);
  if (!el) return;
  var extra = document.getElementById(id + '-extra');
  if (el.style.display === 'none' || !el.style.display) {
    el.style.display = 'block';
    if (extra) extra.style.display = 'flex';  // NL-58: reveal speed/skip
    el.play();
  } else { el.paused ? el.play() : el.pause(); }
}
/* NL-58 ruling 7: minimal player controls on top of the native <audio> — skip
   +/-15s and a 1x/1.25x/1.5x/2x speed cycle. Clamp the skip to the media
   bounds; the speed button relabels itself to the active rate. */
function skipAudio(id, delta) {
  var el = document.getElementById(id);
  if (!el) return;
  var t = (el.currentTime || 0) + delta;
  if (t < 0) t = 0;
  if (el.duration && t > el.duration) t = el.duration;
  el.currentTime = t;
}
var AUDIO_SPEEDS = [1, 1.25, 1.5, 2];
function cycleSpeed(id, btn) {
  var el = document.getElementById(id);
  if (!el) return;
  var i = AUDIO_SPEEDS.indexOf(el.playbackRate);
  var next = AUDIO_SPEEDS[(i + 1) % AUDIO_SPEEDS.length];
  if (!next) next = 1;
  el.playbackRate = next;
  btn.textContent = next + '\\u00D7';
}
function openSettings() {
  document.getElementById('slide-scrim').classList.add('open');
  var panel = document.getElementById('slide-panel');
  panel.classList.add('open'); panel.setAttribute('aria-hidden', 'false');
  panel.querySelector('.slide-close').focus();
}
function closeSettings() {
  document.getElementById('slide-scrim').classList.remove('open');
  var panel = document.getElementById('slide-panel');
  panel.classList.remove('open'); panel.setAttribute('aria-hidden', 'true');
}
/* NL-149 item 2: Settings -> Generation reports. The panel CLOSES on the way in
   (a slide panel over a full-page destination is two open surfaces arguing), and
   the reader lands back where they were, not on Today — the same return-id
   courtesy openDeepView pays. The visible back label is the app's home because
   that is the one destination guaranteed to exist. */
var lastRunLogReturn = 'view-today';
function openRunLog(e) {
  if (e) e.preventDefault();
  var av = document.querySelector('.view.active');
  lastRunLogReturn = (av && av.id) ? av.id : 'view-today';
  closeSettings();
  document.querySelectorAll('.view').forEach(function (v) { v.classList.remove('active'); });
  var target = document.getElementById('view-runlog');
  if (target) target.classList.add('active');
  window.scrollTo(0, 0);
  return false;
}
function closeRunLog(e) {
  if (e) e.preventDefault();
  document.querySelectorAll('.view').forEach(function (v) { v.classList.remove('active'); });
  var back = document.getElementById(lastRunLogReturn)
             || document.getElementById('view-today');
  if (back) back.classList.add('active');
  window.scrollTo(0, 0);
  return false;
}
/* NL-152 — scheduled generation, from the settings tab.

   THE TOGGLE IS THE KILL SWITCH'S FACE, not a second state: it POSTs to a door
   that touches/removes data/SCHEDULE_PAUSED, and it renders the state the
   SERVER reports back rather than the one it optimistically flipped. That is
   the difference between a switch and a picture of a switch — on a read-only
   data dir the server answers ok:false and the toggle snaps back to the truth
   instead of showing "on" over a schedule that is still paused.

   Deliberately NOT toggleDark's shape (localStorage, no server): dark mode is a
   browser preference and this is machine state a 6am launchd fire reads. */
function toggleSchedule(el) {
  var wasOn = el.getAttribute('aria-checked') === 'true';
  el.setAttribute('aria-checked', String(!wasOn));
  api('/api/schedule/pause', {enabled: !wasOn}, function (d) {
    if (d && d.ok) { reloadPreservingView(); return; }
    /* Put it back where the machine says it is. */
    el.setAttribute('aria-checked', String(wasOn));
    alert((d && d.error) || 'That didn\\u2019t save \\u2014 the schedule is unchanged.');
  });
}
/* ONE SPELLING OF THE RE-INSTALL SENTENCE (QA F-13). Two surfaces reach it now
   — the save that creates the disagreement, and the row that keeps reporting it
   afterwards — and two copies of an instruction is how one of them goes stale.
   The commands themselves are never composed here: they come from
   schedule.reinstall_commands() on the server, which is the single source. */
function showReinstall(s, lead, plistHour, commands) {
  s.textContent = lead + 'The installed agent still fires at '
    + String(plistHour) + ':00 \\u2014 run these two commands to move it:\\n\\n'
    + (commands || []).join('\\n');
  s.style.whiteSpace = 'pre-wrap';
  s.classList.remove('err');
  s.classList.add('showing', 'found');
}
/* `el` is the button that was tapped. When the installed agent disagrees with
   the chosen hour, that button CARRIES the two commands (data-reinstall), so
   opening the editor from the row shows him the way out of the disagreement the
   row is reporting — not only the save that first created it (QA F-13). */
function openScheduleHour(hour, el) {
  var i = document.getElementById('schedule-hour-input');
  i.value = String(hour);
  var s = document.getElementById('schedule-hour-status');
  s.classList.remove('showing', 'found', 'err');
  s.textContent = '';
  s.style.whiteSpace = '';
  var raw = el && el.getAttribute && el.getAttribute('data-reinstall');
  if (raw) {
    try {
      showReinstall(s, '', el.getAttribute('data-plist-hour'), JSON.parse(raw));
    } catch (e) {}
  }
  openPopup('popup-schedule-hour');
}
function saveScheduleHour() {
  var raw = document.getElementById('schedule-hour-input').value.trim();
  var s = document.getElementById('schedule-hour-status');
  api('/api/schedule/hour', {hour: raw}, function (d) {
    if (!d || !d.ok) {
      s.textContent = (d && d.error)
        || 'Nothing was saved \\u2014 pick a whole hour of the day.';
      s.classList.remove('found');
      s.classList.add('showing', 'err');
      return;
    }
    /* THE PLIST REALITY, told at the moment it becomes true (NL-152).
       The launchd agent BAKES the hour, and the fired command never consults
       config \\u2014 so a saved setting alone does not move tomorrow's run. The
       popup stays open carrying the two commands rather than closing on a
       success that would have been a half-truth. Where no agent is installed,
       or its hour already agrees, there is nothing to re-install and the popup
       simply closes. */
    if (d.needs_reinstall) {
      showReinstall(s, 'Saved. ', d.plist_hour, d.commands);
      return;
    }
    closePopup('popup-schedule-hour');
    reloadPreservingView();
  });
}
function toggleDark(el) {
  var on = el.getAttribute('aria-checked') === 'true';
  el.setAttribute('aria-checked', String(!on));
  document.body.classList.toggle('dark', !on);
  try { localStorage.setItem('newslens-dark', String(!on)); } catch (e) {}
}
/* v7: the splash-logo scroll animation is retired — the top-bar logo it drove
   is gone (DIRECTION-v5 §4 no-chrome); the dateline ceremony is the arrival. */
try { if (localStorage.getItem('newslens-dark') === 'true') {
  document.body.classList.add('dark');
  var t = document.getElementById('dark-toggle'); if (t) t.setAttribute('aria-checked', 'true');
} } catch (e) {}
var lastFocusedBeforePopup = null;
function openPopup(id) {
  lastFocusedBeforePopup = document.activeElement;
  var el = document.getElementById(id);
  el.classList.add('open');
  /* Backlog-minors item 1: snapshot each field's opening value so the
     dirty check respects PRE-FILLED popups (edit-note opens with the
     existing note — that text is clean until touched). */
  el.querySelectorAll('input[type="text"], textarea').forEach(function (f) {
    f.dataset.initialValue = f.value;
  });
  var firstField = el.querySelector('input, textarea, button');
  if (firstField) firstField.focus();
}
function closePopup(id) {
  document.getElementById(id).classList.remove('open');
  if (lastFocusedBeforePopup) lastFocusedBeforePopup.focus();
}
/* Backlog-minors item 1 — tap-outside dismisses, built ONCE in the shared
   component (design round 4's single-pattern rule). The recorded nuance is
   binding: a popup with unsaved typed input never dies silently — dirty =
   NO-OP (judgment call, disclosed: a mis-tap on the scrim is common on
   mobile and Cancel stays one tap away; no-op is the least destructive).
   Escape parity: the same guard, the same single path. */
function popupIsDirty(el) {
  var fields = el.querySelectorAll('input[type="text"], textarea');
  for (var i = 0; i < fields.length; i++) {
    if (fields[i].value !== (fields[i].dataset.initialValue || '')) return true;
  }
  return false;
}
function dismissPopup(id) {
  var el = document.getElementById(id);
  if (!el || !el.classList.contains('open')) return;
  if (popupIsDirty(el)) return;  // unsaved text: no-op, never silent loss
  closePopup(id);
}
document.addEventListener('click', function (e) {
  if (e.target.classList && e.target.classList.contains('popup-scrim')) {
    dismissPopup(e.target.id);
  }
});
var noteTopic = null;
function openEditNote(topicName, existing) {
  noteTopic = topicName;
  document.getElementById('edit-note-topic-name').textContent = topicName;
  document.getElementById('edit-note-textarea').value = existing || '';
  openPopup('popup-edit-note');
}
function saveNote() {
  api('/api/note', {topic: noteTopic, note: document.getElementById('edit-note-textarea').value},
      function () { closePopup('popup-edit-note'); reloadPreservingView(); });
}
/* NL-68 item 10: "Follow a new story" is SUGGESTIONS-ONLY (the ruled contract,
   DECISIONS 2026-07-10 NL-58 #3) — the free-text add-story input is gone; a
   follow is created only by picking a recent briefing story/thread from the
   suggestion combobox. followStory reloads with the quiet fold expanded so the
   new (delta-less) thread is visible, not buried. */
function followStory(value) {
  value = (value || '').trim();
  if (!value) return;
  api('/api/follow', {topic: value, briefing_date: CURRENT_DATE},
      function () { reloadPreservingView(true); });
}
var pendingTopic = null;
function openAddTopic(name) {
  if (!name || !name.trim()) return;
  pendingTopic = name.trim();
  document.getElementById('add-topic-name').textContent = pendingTopic;
  document.getElementById('add-topic-status').classList.remove('showing');
  openPopup('popup-add-topic');
}
function addTopic(level) {
  api('/api/topic/add', {name: pendingTopic, level: level}, function (d) {
    if (d.ok) { closePopup('popup-add-topic'); reloadPreservingView(); }
    else {
      var s = document.getElementById('add-topic-status');
      s.textContent = d.error || 'Could not add that topic.';
      s.classList.add('showing');
    }
  });
}
function openAddWriter(name) {
  document.getElementById('add-writer-input').value = name || '';
  document.getElementById('add-writer-status').classList.remove('showing', 'found', 'err');
  openPopup('popup-add-writer');
}
function addWriter() {
  var name = document.getElementById('add-writer-input').value.trim();
  var url = document.getElementById('add-writer-url').value.trim();
  var s = document.getElementById('add-writer-status');
  if (!url) {
    /* NL-103 row 10: the ONE writer-lookup string, refusal class (§3): states
       what did NOT happen + the honest next act, no roadmap promise. */
    s.textContent = 'Name lookup isn\\u2019t available yet \\u2014 paste a link to their feed.';
    s.classList.add('showing'); s.classList.remove('found'); s.classList.add('err');
    return;
  }
  s.textContent = 'Adding\\u2026'; s.classList.add('showing'); s.classList.remove('err');
  api('/api/writer/add', {name: name, url: url}, function (d) {
    if (d.ok) {
      s.classList.add('found');
      /* NL-103 row 11 (C7b): a receipt never takes a pronoun object — the
         nameless fallback is the class noun (what was actually followed). */
      s.textContent = 'Following ' + (name || 'the feed') + ' \\u2014 ' + d.detail;
      setTimeout(function () { closePopup('popup-add-writer'); reloadPreservingView(); }, 1200);
    } else { s.classList.add('err'); s.textContent = d.error || 'Could not add that feed.'; }
  });
}
var deleteTopic = null;
function openDeleteConfirm(topicName) {
  deleteTopic = topicName;
  document.getElementById('delete-topic-name').textContent = topicName;
  openPopup('popup-delete-confirm');
}
function deleteThread() {
  api('/api/thread/delete', {topic: deleteTopic},
      function () { closePopup('popup-delete-confirm'); reloadPreservingView(); });
}
function threadAction(action, topic) {
  api('/api/' + action, {topic: topic}, function () { reloadPreservingView(); });
}
function removeToken(kind, name, el) {
  /* NL-11: remove then reload so the followed COUNT in the group header
     updates (the old in-place hide left the count stale) — and the reload
     preserves the Following view + sub-view + scroll. */
  api('/api/' + kind + '/remove', {name: name}, function (d) {
    if (d && d.ok) { reloadPreservingView(); }
  });
}
function generateAgain() {
  api('/api/generate', {}, function () { reloadPreservingView(); });
}
/* NL-11: shared house-styled suggestion combobox — keyboard-driven
   (Arrow/Enter/Escape), excludes already-followed entries (server-filtered),
   shows a secondary outlet line for writers, and degrades to a plain input
   with no JS (the list stays hidden). One component, both editors. */
function escapeHtml(s) {
  return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) {
    return {'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c];
  });
}
function suggestData(container) {
  if (container._data) return container._data;
  var tag = container.querySelector('script.suggest-data');
  var arr = [];
  try { arr = JSON.parse((tag && tag.textContent) || '[]'); } catch (e) { arr = []; }
  container._data = arr;
  return arr;
}
function suggestInput(inp) {
  var container = inp.closest('.suggest');
  var list = container.querySelector('.suggest-list');
  var q = inp.value.trim().toLowerCase();
  var matches = suggestData(container).filter(function (o) {
    return (o.l || o.v || '').toLowerCase().indexOf(q) >= 0 ||
           (o.s || '').toLowerCase().indexOf(q) >= 0;
  }).slice(0, 8);
  container._matches = matches;
  container._active = -1;
  inp.removeAttribute('aria-activedescendant');
  if (!matches.length) { list.hidden = true; inp.setAttribute('aria-expanded', 'false'); return; }
  list.innerHTML = matches.map(function (o, i) {
    var sub = o.s ? '<span class="s-sub">' + escapeHtml(o.s) + '</span>' : '';
    return '<li role="option" id="' + list.id + '-opt-' + i + '" data-i="' + i + '"' +
      ' aria-selected="false" onmousedown="suggestPick(event,this)">' +
      '<span class="s-label">' + escapeHtml(o.l || o.v) + '</span>' + sub + '</li>';
  }).join('');
  list.hidden = false;
  inp.setAttribute('aria-expanded', 'true');
}
function suggestHighlight(container) {
  var list = container.querySelector('.suggest-list');
  var inp = container.querySelector('input');
  Array.prototype.forEach.call(list.children, function (li, i) {
    var on = i === container._active;
    li.setAttribute('aria-selected', on ? 'true' : 'false');
    if (on) { inp.setAttribute('aria-activedescendant', li.id); li.scrollIntoView({block: 'nearest'}); }
  });
  if (container._active < 0) inp.removeAttribute('aria-activedescendant');
}
function suggestKeydown(e, inp) {
  var container = inp.closest('.suggest');
  var list = container.querySelector('.suggest-list');
  var matches = container._matches || [];
  if (e.key === 'ArrowDown') {
    e.preventDefault();
    if (list.hidden) { suggestInput(inp); return; }
    container._active = Math.min((container._active | 0) + 1, matches.length - 1);
    suggestHighlight(container);
  } else if (e.key === 'ArrowUp') {
    e.preventDefault();
    container._active = Math.max((container._active | 0) - 1, -1);
    suggestHighlight(container);
  } else if (e.key === 'Enter') {
    e.preventDefault();
    if (!list.hidden && container._active >= 0) { suggestChoose(container, container._active); }
    else { suggestSubmit(container, inp.value); }
  } else if (e.key === 'Escape') {
    if (!list.hidden) {
      e.stopPropagation();  // close the list; don't also close a popup/settings
      list.hidden = true; inp.setAttribute('aria-expanded', 'false'); container._active = -1;
    }
  }
}
function suggestPick(e, li) {
  e.preventDefault();  // mousedown fires before blur hides the list
  suggestChoose(li.closest('.suggest'), parseInt(li.getAttribute('data-i'), 10));
}
function suggestChoose(container, i) {
  var o = (container._matches || [])[i];
  if (!o) return;
  suggestSubmit(container, o.v || o.l);
}
function suggestSubmit(container, value) {
  value = (value || '').trim();
  var inp = container.querySelector('input');
  var list = container.querySelector('.suggest-list');
  // NL-68 item 10: a suggestions-only surface (the story follow) must never act
  // on raw typed text — only a value that matches a real suggestion follows. A
  // non-matching entry is a no-op (the input keeps the typed text for
  // correction); picking a suggestion always matches by construction.
  if (container.getAttribute('data-suggest-only') === '1') {
    var match = suggestData(container).some(function (o) {
      return String(o.v || o.l).toLowerCase() === value.toLowerCase();
    });
    if (!value || !match) return;
  }
  list.hidden = true; inp.setAttribute('aria-expanded', 'false'); container._active = -1;
  inp.value = '';
  if (!value) return;
  var kind = container.getAttribute('data-kind');
  if (kind === 'writer') { openAddWriter(value); }
  else if (kind === 'story') { followStory(value); }
  else { openAddTopic(value); }
}
function suggestBlur(inp) {
  var container = inp.closest('.suggest');
  setTimeout(function () {
    var list = container.querySelector('.suggest-list');
    if (list) { list.hidden = true; inp.setAttribute('aria-expanded', 'false'); }
  }, 120);
}
/* NL-11: archive editions open IN-PLACE (Today is never replaced). Fetch the
   edition fragment (the server logs the read as it serves the body — same
   server-side truth as a page-view, not a client beacon), inject it as
   sibling views, switch to it; the href is the no-JS fallback. */
function openEdition(date, e) {
  if (e) e.preventDefault();
  fetch('/edition?date=' + encodeURIComponent(date))
    .then(function (r) { return r.text(); })
    .then(function (html) {
      var mount = document.getElementById('edition-mount');
      mount.innerHTML = html;
      document.querySelectorAll('.view').forEach(function (v) { v.classList.remove('active'); });
      var ed = document.getElementById('view-edition');
      if (ed) ed.classList.add('active');
      window.scrollTo(0, 0);
    })
    .catch(function () { location.href = '/?date=' + encodeURIComponent(date); });
  return false;
}
function backToArchive(e) {
  if (e) e.preventDefault();
  showView('archive');
}
/* §14: pick a day — no fetch. Every edition's panel is already in the DOM
   (hidden); picking swaps which one shows (the aria-live stack announces it),
   and moves the pick's scale + aria-pressed to the chosen edition button. */
function pickDay(date, e) {
  if (e) e.preventDefault();
  var stack = document.querySelector('.day-panel-stack');
  if (stack) {
    stack.querySelectorAll('.day-panel').forEach(function (p) {
      p.hidden = (p.getAttribute('data-date') !== date);
    });
  }
  document.querySelectorAll('.cal-edition').forEach(function (cell) {
    cell.classList.remove('cal-picked');
    var b = cell.querySelector('button');
    if (b) {
      b.setAttribute('aria-pressed', 'false');
      /* gate FIX-1: keep the accessible-name action hint truthful after a
         client-side pick — a stale "showing headlines" on an unpressed button
         would contradict aria-pressed. */
      var lb = b.getAttribute('aria-label');
      if (lb) b.setAttribute('aria-label',
        lb.replace(' — showing headlines', ' — show headlines'));
    }
  });
  var btn = document.querySelector('.cal-edition button[data-date="' + date + '"]');
  if (btn) {
    btn.setAttribute('aria-pressed', 'true');
    var cell = btn.closest('.cal-edition');
    if (cell) cell.classList.add('cal-picked');
    var lbp = btn.getAttribute('aria-label');
    if (lbp) btn.setAttribute('aria-label',
      lbp.replace(' — show headlines', ' — showing headlines'));
  }
  return false;
}
/* §14: page months — fetch the archive guts for a month and swap them in place
   (the openEdition pattern). Inline onclicks in the fragment keep working after
   injection; the href-less links fall back to a full nav if the fetch fails. */
function navMonth(month, e) {
  if (e) e.preventDefault();
  fetch('/archive?am=' + encodeURIComponent(month))
    .then(function (r) { return r.text(); })
    .then(function (html) {
      var host = document.getElementById('archive-body');
      if (host) { host.innerHTML = html; window.scrollTo(0, 0); }
    })
    .catch(function () { location.href = '/archive?am=' + encodeURIComponent(month); });
  return false;
}
restoreViewAfterReload();
/* NL-88: live generation status. The clock ticks client-side (1s) between the
   2.5s polls, re-syncing to the server's elapsed values on every poll so it
   never drifts. Stage label + model come straight from /api/status. */
var genClock = { total: 0, stage: 0, sync: 0, timer: null };
function genFmt(s) {
  s = Math.max(0, Math.floor(s));
  var m = Math.floor(s / 60), r = s % 60;
  return m + ':' + (r < 10 ? '0' : '') + r;
}
function genClockTick() {
  var el = document.getElementById('gen-live-clock');
  if (!el) { return; }
  var d = (Date.now() - genClock.sync) / 1000;
  el.textContent = genFmt(genClock.total + d) + ' elapsed · '
    + genFmt(genClock.stage + d) + ' on this step';
}
function genClockSync(totalS, stageS) {
  if (typeof totalS === 'number' && totalS >= 0) { genClock.total = totalS; }
  genClock.stage = (typeof stageS === 'number' && stageS >= 0) ? stageS : 0;
  genClock.sync = Date.now();
  if (!genClock.timer) { genClock.timer = setInterval(genClockTick, 1000); }
  genClockTick();
}
/* NL-149 item 1: the finished-step log. ADD, never replace — genLogSync only
   ever appends the entries past the cursor it already rendered, so a line that
   has appeared is never rewritten, re-ordered or removed by a later poll. The
   cursor rides on the element (data-count) rather than in a JS variable so the
   server-seeded lines from a mid-run reload are counted the same way the
   appended ones are, and one poll after a reload cannot double-print them.
   Mirrors server._gen_log_line — same tags, same classes, same M:SS. */
function genLogLine(step) {
  var li = document.createElement('li');
  var lab = document.createElement('span');
  lab.className = 'gen-log-step';
  lab.textContent = step.label || '';
  li.appendChild(lab);
  if (step.model) {
    var md = document.createElement('span');
    md.className = 'gen-log-model';
    md.textContent = ' · ' + step.model;
    li.appendChild(md);
  }
  var t = document.createElement('span');
  t.className = 'gen-log-time';
  t.textContent = (typeof step.elapsed_s === 'number') ? genFmt(step.elapsed_s) : '—';
  li.appendChild(t);
  return li;
}
/* THE CURSOR'S IDENTITY (NL-149 QA F-3). data-count says how MANY lines are
   drawn; data-run says WHICH RUN drew them. Without the second, a client that
   outlives a run boundary — and the .catch below retries every 4s and
   deliberately never reloads, so a tab left open through a restart is exactly
   that client — computes `have = 4` against run B's `steps: []`, appends
   nothing, and leaves run A's lines under run B's live clock while B's first
   four boundaries are never drawn. Append-only stays the law WITHIN a run; a
   new identity is a new log, which is the rule _GenJob.start() already applies
   server-side. */
function genLogSync(steps, runId) {
  var log = document.getElementById('gen-log');
  if (!log || !steps) { return; }
  if (runId && log.getAttribute('data-run') !== runId) {
    log.textContent = '';
    log.setAttribute('data-count', '0');
    log.setAttribute('data-run', runId);
  }
  var have = parseInt(log.getAttribute('data-count'), 10) || 0;
  for (var i = have; i < steps.length; i++) { log.appendChild(genLogLine(steps[i])); }
  if (steps.length > have) { log.setAttribute('data-count', String(steps.length)); }
}
/* NL-149 QA F-3, the stale-tab half: THE CLOCK STOPS WHEN CONTACT DOES.
   genClockTick interpolates locally between polls, so a tab whose server has
   gone away kept counting — minutes of invented elapsed for a run it cannot
   see and that may already be over. Clearing the interval in the .catch freezes
   the last CONFIRMED reading; the next successful poll re-syncs from the
   server's own elapsed and restarts the tick. The retry stays indefinite on
   purpose: reloading against a server that is still down would replace a
   self-healing tab with a browser error page, and with data-run above, a tab
   that reconnects into a different run now redraws instead of lying. */
function genClockHold() {
  if (genClock.timer) { clearInterval(genClock.timer); genClock.timer = null; }
}
function pollGeneration() {
  fetch('/api/status').then(function (r) { return r.json(); }).then(function (d) {
    if (d.state === 'running') {
      genLogSync(d.steps, d.started_at);
      var st = document.getElementById('gen-live-stage');
      if (st && d.stage) { st.textContent = d.stage; }
      var md = document.getElementById('gen-live-model');
      if (md) { md.textContent = d.stage_model ? ' · ' + d.stage_model : ''; }
      genClockSync(d.total_elapsed_s, d.stage_elapsed_s);
      setTimeout(pollGeneration, 2500);
    } else { location.reload(); }
  }).catch(function () { genClockHold(); setTimeout(pollGeneration, 4000); });
}
if (document.getElementById('gen-running')) {
  var gl = document.getElementById('gen-live');
  if (gl) {
    genClockSync(parseFloat(gl.getAttribute('data-total')) || 0,
                 parseFloat(gl.getAttribute('data-stage-el')) || 0);
  }
  pollGeneration();
}
document.addEventListener('keydown', function (e) {
  if (e.key !== 'Escape') return;
  closeSettings();
  /* item 1 parity: Escape uses the SAME dirty-guarded path — Escape
     silently eating typed text was the exact bug class being fixed. */
  document.querySelectorAll('.popup-scrim.open').forEach(function (p) { dismissPopup(p.id); });
});
"""
