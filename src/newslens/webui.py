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

CSS = """
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
}
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
.page { max-width: 72rem; margin: 0 auto; padding: 0 2rem; }
article.story { scroll-margin-top: 0.75rem; }
.snippet { scroll-margin-top: 0.75rem; }

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
.dateline { font-family: var(--font-display); font-weight: 700; font-size: 4rem;
  line-height: 1.02; letter-spacing: -0.015em; margin: 0; }
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

/* ============================ TODAY — asymmetric grid (§12.3) ============================ */
.today-grid { display: grid; grid-template-columns: 7fr 5fr; gap: 0 4rem; padding: 2.2rem 0 1rem; }
.kicker { font-family: var(--font-sans); font-size: 0.78rem; font-weight: 700;
  letter-spacing: 0.14em; text-transform: uppercase; color: var(--terra); margin: 0 0 0.6rem; }
.lead h2.headline { font-family: var(--font-display); font-weight: 700; font-size: 3.5rem;
  line-height: 1.06; letter-spacing: -0.015em; margin: 0 0 0.7rem; }
.lead .body { font-size: 1.05rem; max-width: 38rem; }
.lead .body > p { margin: 0 0 1rem; }
.move-label { font-family: var(--font-sans); font-size: 0.78rem; font-weight: 700;
  letter-spacing: 0.12em; text-transform: uppercase; color: var(--ink-soft); margin: 1.4rem 0 0.3rem; }
.col-right .move-label { font-size: 0.72rem; margin: 1rem 0 0.25rem; }
.my-read { font-style: italic; }
/* the deck (under-title): NL-65 leaves ONLY the follow control here */
.deck { display: flex; flex-wrap: wrap; align-items: baseline; gap: 0.35rem 1.1rem;
  margin: 0 0 1.15rem; padding: 0.45rem 0; border-bottom: 1px solid var(--rule); font-size: 0.88rem; }
.deck > * { min-width: 0; }
.tracked-marker, .follow-story-btn { background: none; border: none; padding: 0; cursor: pointer;
  text-align: left; font-family: var(--font-sans); font-size: 0.88rem; font-weight: 700; color: var(--terra); }
.follow-story-btn:hover { color: var(--terra-deep); text-decoration: underline; }
.follow-story-btn:not(.followed):not(.confirming) { font-weight: 400; color: var(--ink-soft); }
.follow-story-btn.followed, .follow-story-btn.confirming { color: var(--moved); }
.tracked-marker { color: var(--moved); cursor: default; }
.tracked-marker::before { content: "\\25CF "; }
/* NL-65: the deep-view entry moves to the story BOTTOM, before the furniture */
.story-more { margin: 1.1rem 0 0; font-size: 0.88rem; }
.deep-view-entry-link { color: var(--terra); font-weight: 700; text-decoration: none; }
.deep-view-entry-link:hover { color: var(--terra-deep); text-decoration: underline; }
.furniture, .meta-footnote { font-size: 0.8rem; font-style: italic; color: var(--ink-faint);
  margin: 1.1rem 0 0; max-width: 38rem; line-height: 1.5; }
.override-note { font-size: 0.82rem; font-weight: 700; color: var(--terra-deep); margin: 0 0 0.8rem; }
.override-note .reason { display: block; font-weight: 400; margin-top: 0.2rem; color: var(--ink); }
h2.headline, h3.headline, h4.headline { font-family: var(--font-display); font-weight: 700;
  margin: 0 0 0.4rem; line-height: 1.22; }

/* Right column: quiet register */
.col-right article.story { padding: 0 0 1.6rem; margin-bottom: 1.6rem; border-bottom: 1px solid var(--rule); }
.col-right h2.headline { font-size: 1.4rem; }
.col-right .body { font-size: 0.95rem; }
.col-right .body > p { margin: 0 0 0.8rem; }
.col-right .deck { font-size: 0.82rem; margin-bottom: 0.7rem; padding: 0.35rem 0; }
.col-right .furniture, .col-right .meta-footnote { font-size: 0.76rem; margin-top: 0.6rem; }

/* In brief */
.in-brief { margin-top: 0.5rem; }
.brief-label { font-family: var(--font-sans); font-size: 0.78rem; font-weight: 700;
  letter-spacing: 0.14em; text-transform: uppercase; color: var(--ink-soft); margin: 0 0 0.8rem; }
.snippet { margin: 0 0 1.3rem; }
.snippet h3.headline, .snippet h4.headline { font-family: var(--font-display); font-weight: 700;
  font-size: 1.02rem; line-height: 1.3; margin: 0 0 0.15rem; }
.snippet .body > p, .quick-hit p { font-size: 0.88rem; line-height: 1.5; margin: 0; color: var(--ink-soft); }
.snippet .deck { font-size: 0.78rem; margin: 0.2rem 0 0.35rem; padding: 0; border: none; }
.snippet .story-more { margin: 0.25rem 0 0; font-size: 0.78rem; }
.snippet .furniture, .snippet .meta-footnote { font-size: 0.74rem; margin-top: 0.25rem; }

/* Still-tracking strip (retro-mock idiom; A8 no-fabrication teeth in the composer) */
.still-tracking { margin: 1.6rem 0 0; padding-top: 1rem; border-top: 1px solid var(--rule); }
.still-tracking .st-line { font-size: 0.82rem; color: var(--ink-soft); margin: 0 0 0.5rem; line-height: 1.5; }
.still-tracking .st-thread { font-family: var(--font-display); font-weight: 700; color: var(--ink); }

/* Today arc continuity line (deterministic then→now line under a story). */
.today-arc-line { font-size: 0.9rem; color: var(--ink-soft); margin: 0.6rem 0 0; line-height: 1.5; }
.today-arc-line.reverted { color: var(--danger); }
.today-arc-link { color: var(--moved); font-weight: 700; text-decoration: none; white-space: nowrap; }
.today-arc-link:hover { color: var(--terra-deep); }
.today-arc-disclosure { color: var(--ink-faint); font-style: italic; }

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

.state-panel { background: var(--surface); border-radius: var(--radius); padding: 1.75rem 1.5rem; margin-top: 1.5rem; }
.state-panel h2 { font-family: var(--font-display); font-size: 1.15rem; margin: 0 0 0.6rem; }
.state-panel p { font-size: 0.9rem; color: var(--ink-soft); margin: 0 0 1rem; }
.steps { list-style: none; margin: 0 0 1rem; padding: 0; font-family: var(--font-mono); font-size: 0.78rem; color: var(--ink-faint); }
.steps li { padding: 0.2rem 0; }
.steps li.done { color: var(--ink); } .steps li.done::before { content: "\\2713 "; color: var(--moved); }
.steps li.active::before { content: "\\2026 "; } .steps li.pending::before { content: "\\00B7 "; }
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

/* ==================== ARCHIVE — the Study/Wire calendar (§8) ==================== */
.month-title { font-family: var(--font-display); font-weight: 700; font-size: 3rem;
  line-height: 1; margin: 2rem 0 0.2rem; }
.month-title .yr { font-size: 1.3rem; font-weight: 400; color: var(--ink-faint); }
.cal-note { font-size: 0.8rem; color: var(--ink-faint); margin: 0.2rem 0 1.6rem; }
.cal-grid { display: grid; grid-template-columns: repeat(7, 1fr); gap: 0.4rem;
  max-width: 42rem; margin-bottom: 0.6rem; }
.cal-dow { font-size: 0.7rem; font-weight: 700; letter-spacing: 0.1em; text-transform: uppercase;
  color: var(--ink-faint); padding-bottom: 0.3rem; }
.cal-cell { min-height: 3.6rem; padding: 0.3rem 0.35rem; font-family: var(--font-display); }
.cal-num { font-size: 1.2rem; line-height: 1.3; display: inline-block; }
.cal-void .cal-num { color: var(--cal-bare); }           /* pre-history + future: barest */
.cal-gap .cal-num { color: var(--ink-faint); }            /* gap within history: faint, no shame */
.cal-edition a { text-decoration: none; color: var(--ink); display: block; }
.cal-edition .cal-num { font-weight: 700; border-bottom: 2px solid var(--moved); }
.cal-edition a:hover .cal-num { color: var(--terra-deep); }
.cal-stamp { display: block; font-family: var(--font-mono); font-size: 0.64rem;
  color: var(--ink-faint); margin-top: 0.2rem; }
.cal-today .cal-num { color: var(--terra); border: 2px solid var(--terra);
  border-radius: 50%; width: 2rem; height: 2rem; display: inline-flex;
  align-items: center; justify-content: center; }
.archive-list { list-style: none; padding: 0; margin: 1.6rem 0 4rem; max-width: 46rem;
  border-top: 1px solid var(--ink); }
.archive-list li { border-bottom: 1px solid var(--rule); padding: 0.8rem 0; }
.archive-list .al-date { font-family: var(--font-mono); font-size: 0.74rem; color: var(--ink-faint); display: block; }
.archive-list a { font-family: var(--font-display); font-weight: 700; font-size: 1.1rem;
  color: var(--ink); text-decoration: none; }
.archive-list a:hover { color: var(--terra-deep); text-decoration: underline; }

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
.thread-verbs button.delete-action:hover { border-color: var(--danger); color: var(--danger); }

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
.token button.token-remove:hover { color: var(--danger); }

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
.deep-arc-verdict { font-weight: 700; color: var(--moved); }
.deep-arc-link { color: var(--ink-faint); text-decoration: none; white-space: nowrap; }
.deep-arc-link:hover { color: var(--terra); }
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
/* NL-58 parity: the citation fold reads the same in the facts list AND in the
   mechanism prose. <details open> => no-JS shows it expanded; JS collapses it. */
.fact-cite { color: var(--ink-faint); font-size: 0.85em; }
.cite-fold { display: inline; }
.cite-fold:not([open]) .cite-fold-body { display: none; }
.cite-fold summary { display: inline; cursor: pointer; list-style: none; color: var(--ink-faint); }
.cite-fold summary::-webkit-details-marker { display: none; }
.cite-fold summary:focus-visible { outline: 2px solid var(--terra); outline-offset: 2px; border-radius: 2px; }
.cite-fold summary .caret { display: inline-block; font-size: 0.85em; transition: transform 0.12s ease; }
.cite-fold[open] summary .caret { transform: rotate(90deg); }
.cite-fold .cite-fold-body { color: var(--ink-faint); }
.deep-effect { margin: 0 0 0.85rem; }
.deep-effect .cite { color: var(--ink-faint); font-size: 0.9em; }
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
/* NL-66(b): the In-Brief sources-&-context view. */
.sc-tags, .sc-threads, .sc-herefor { color: var(--ink-soft); font-size: 0.9rem; margin: 0 0 0.35rem; }
.sc-corrob { color: var(--ink-faint); font-size: 0.85rem; margin: 0 0 0.6rem; }
.deep-footer { font-size: 0.78rem; color: var(--ink-faint); padding-top: 1.25rem;
  margin-top: 0.5rem; border-top: 1px solid var(--rule); line-height: 1.6; }
.deep-footer p { margin: 0 0 0.4rem; }
.deep-footer p:last-child { margin-bottom: 0; }

/* Deep + archive-edition views carry no section line, so they center as a page. */
#view-edition, section[id^="view-deep-"], section[id^="view-thread-"] { max-width: 72rem; margin: 0 auto; padding: 0 2rem; }
#view-edition .view-title, #view-edition .today-grid, #view-edition .footer-tag { max-width: none; }

/* ============================ MOBILE PASS (~390px) ============================ */
@media (max-width: 900px) {
  .page { padding: 0 1.15rem; }
  #view-edition, section[id^="view-deep-"], section[id^="view-thread-"] { padding: 0 1.15rem; }
  .today-grid { grid-template-columns: 1fr; gap: 0; }
  .dateline { font-size: 2.6rem; }
  .signature { font-size: 1.1rem; }
  .dispatch-strip { font-size: 0.74rem; }
  .lead h2.headline { font-size: 2.5rem; line-height: 1.08; }
  .col-right { border-top: 1px solid var(--ink); padding-top: 1.6rem; margin-top: 0.6rem; }
  .section-line a { margin-right: 1.1rem; }
  .deep-title { font-size: 1.8rem; }
  /* v7-M2 surfaces */
  .page-title { font-size: 2.1rem; }
  .view-line a { margin-right: 1.1rem; }
  .month-title { font-size: 2.2rem; }
  .cal-cell { min-height: 2.8rem; padding: 0.25rem; }
  .cal-stamp { display: none; }   /* stamps are the desktop spread's luxury */
}
"""

# The full page shell (v7 — DIRECTION-v5 §4: no chrome). The masthead ceremony,
# section line, and edition bar are rendered INTO each view by server.py (the
# dateline is per-edition, not shared chrome). Placeholders: {css} {today_html}
# {following_html} {archive_html} {settings_html} {popups_html} {deep_views_html} {js}
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
<main id="main" tabindex="-1">
<section id="view-today" class="view active">{today_html}</section>
<section id="view-following" class="view">{following_html}</section>
<section id="view-archive" class="view">{archive_html}</section>
{deep_views_html}
{thread_pages_html}
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
    <label for="edit-note-textarea">This note shapes how future editions frame this story. It never appears on the card or in the edition itself.</label>
    <textarea id="edit-note-textarea" rows="4"></textarea>
    <div class="popup-actions">
      <button class="cta-outline" onclick="closePopup('popup-edit-note')">Cancel</button>
      <button class="cta-quiet" onclick="saveNote()">Save</button>
    </div>
  </div>
</div>
<div class="popup-scrim" id="popup-add-story" role="dialog" aria-modal="true" aria-labelledby="popup-add-story-title">
  <div class="popup-card">
    <h3 id="popup-add-story-title">Follow a new story</h3>
    <label for="add-story-input">What are you tracking?</label>
    <input type="text" id="add-story-input" placeholder="e.g. Redistricting fight in Texas">
    <div class="popup-actions">
      <button class="cta-outline" onclick="closePopup('popup-add-story')">Cancel</button>
      <button class="cta-quiet" onclick="addStory()">Follow</button>
    </div>
  </div>
</div>
<div class="popup-scrim" id="popup-add-topic" role="dialog" aria-modal="true" aria-labelledby="popup-add-topic-title">
  <div class="popup-card">
    <h3 id="popup-add-topic-title">Add topic — <span id="add-topic-name"></span></h3>
    <p style="font-size:0.85rem;color:var(--ink-soft);margin:0 0 1rem;">Add this as a broad interest or a specific one?</p>
    <p class="popup-status err" id="add-topic-status"></p>
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
    <p class="popup-status" id="add-writer-status"></p>
    <label for="add-writer-url">Paste a link to their feed or site</label>
    <input type="text" id="add-writer-url" placeholder="https://…/feed">
    <p class="popup-note">Name-only lookup is coming; pasting a feed link works today.</p>
    <div class="popup-actions">
      <button class="cta-outline" onclick="closePopup('popup-add-writer')">Cancel</button>
      <button class="cta-quiet" onclick="addWriter()">Follow</button>
    </div>
  </div>
</div>
<div class="popup-scrim" id="popup-delete-confirm" role="dialog" aria-modal="true" aria-labelledby="popup-delete-title">
  <div class="popup-card">
    <h3 id="popup-delete-title">Delete “<span id="delete-topic-name"></span>”?</h3>
    <p style="font-size:0.88rem;color:var(--ink-soft);margin:0 0 1.25rem;">This removes it permanently from your list. Past editions that mentioned it are unaffected.</p>
    <div class="popup-actions">
      <button class="cta-outline" onclick="closePopup('popup-delete-confirm')">Cancel</button>
      <button class="cta-quiet" style="background:var(--danger);" onclick="deleteThread()">Delete</button>
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
function reloadPreservingView() {
  try {
    var av = document.querySelector('.view.active');
    var view = (av && (av.id === 'view-following' || av.id === 'view-archive'))
      ? av.id.replace('view-', '') : 'today';
    var activeSub = document.querySelector('.sub-view.active');
    var sub = activeSub ? activeSub.id.replace('sub-', '') : null;
    var y = window.scrollY || window.pageYOffset ||
            document.documentElement.scrollTop || 0;
    sessionStorage.setItem('nl-restore',
      JSON.stringify({view: view, sub: sub, y: y}));
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
  // Defer the scroll to after layout settles (and after the browser's own
  // restoration, which we opt out of below) so the position actually sticks.
  if (typeof st.y === 'number' && st.y > 0) {
    var applyScroll = function () { window.scrollTo(0, st.y); };
    requestAnimationFrame(function () { requestAnimationFrame(applyScroll); });
  }
}
/* Per-story follow: in-place swap by design (v3 §Today #3) — the one
   popup-pattern carve-out; requires no further input, so no popup. */
function toggleFollow(btn) {
  var topic = btn.getAttribute('data-topic');
  var when = btn.getAttribute('data-briefing-date') || CURRENT_DATE;
  var pressed = btn.getAttribute('aria-pressed') === 'true';
  if (pressed) {
    btn.setAttribute('aria-pressed', 'false');
    btn.classList.remove('followed');
    btn.textContent = NL_LABELS.followInactive;
    api('/api/unfollow', {topic: topic}, function (d) {
      if (d && d.ok === false) {  // server refused: don't lie about the state
        btn.setAttribute('aria-pressed', 'true');
        btn.classList.add('followed');
        btn.textContent = NL_LABELS.followActive;
      }
    });
    return;
  }
  btn.classList.add('confirming');
  btn.textContent = NL_LABELS.followConfirm;
  api('/api/follow', {topic: topic, briefing_date: when}, function (d) {
    if (d && d.ok === false) {  // no silent lie — revert on refusal
      btn.classList.remove('confirming');
      btn.textContent = NL_LABELS.followInactive;
      return;
    }
    setTimeout(function () {
      btn.classList.remove('confirming');
      btn.classList.add('followed');
      btn.setAttribute('aria-pressed', 'true');
      btn.textContent = NL_LABELS.followActive;
    }, 2800);  // NL-58 ruling 5: confirm state holds 2x the old duration
  });
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
function collapseCiteFolds(root) {
  /* NL-12: per-fact citations ship as <details open> so a no-JS reader sees
     them expanded (degrade = more information). With JS, collapse them to the
     quiet marker; re-run against injected archive editions too. */
  (root || document).querySelectorAll('details.cite-fold[open]').forEach(
    function (d) { d.removeAttribute('open'); });
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
function openAddStory() { openPopup('popup-add-story'); }
function addStory() {
  var v = document.getElementById('add-story-input').value.trim();
  if (!v) return;
  api('/api/follow', {topic: v, briefing_date: CURRENT_DATE},
      function () { closePopup('popup-add-story'); reloadPreservingView(); });
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
    s.textContent = 'Name-only lookup is coming \\u2014 paste their feed link to follow today.';
    s.classList.add('showing'); s.classList.remove('found'); s.classList.add('err');
    return;
  }
  s.textContent = 'Adding\\u2026'; s.classList.add('showing'); s.classList.remove('err');
  api('/api/writer/add', {name: name, url: url}, function (d) {
    if (d.ok) {
      s.classList.add('found');
      s.textContent = 'Following ' + (name || 'them') + ' \\u2014 ' + d.detail;
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
  list.hidden = true; inp.setAttribute('aria-expanded', 'false'); container._active = -1;
  inp.value = '';
  if (!value) return;
  if (container.getAttribute('data-kind') === 'writer') { openAddWriter(value); }
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
      collapseCiteFolds(mount);
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
restoreViewAfterReload();
collapseCiteFolds(document);
function pollGeneration() {
  fetch('/api/status').then(function (r) { return r.json(); }).then(function (d) {
    if (d.state === 'running') { setTimeout(pollGeneration, 2500); }
    else { location.reload(); }
  }).catch(function () { setTimeout(pollGeneration, 4000); });
}
if (document.getElementById('gen-running')) { pollGeneration(); }
document.addEventListener('keydown', function (e) {
  if (e.key !== 'Escape') return;
  closeSettings();
  /* item 1 parity: Escape uses the SAME dirty-guarded path — Escape
     silently eating typed text was the exact bug class being fixed. */
  document.querySelectorAll('.popup-scrim.open').forEach(function (p) { dismissPopup(p.id); });
});
"""
