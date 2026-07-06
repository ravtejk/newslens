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
  --bg: #FBF8F2; --surface: #FFFFFF; --ink: #2B2621; --ink-soft: #6B6258;
  --ink-faint: #8B8175; --accent: #A85D3E; --accent-deep: #7A4029;
  --tracked: #5C7A5E; --danger: #7A3B37; --rule: #E5DCCC;
  --overlay-scrim: rgba(43,38,33,0.35); --popup-scrim: rgba(43,38,33,0.28);
  --font-serif: Charter, "Iowan Old Style", Georgia, "Palatino Linotype", serif;
  --font-sans: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
  --font-mono: ui-monospace, "SF Mono", Menlo, monospace;
  --radius: 10px; --max-w: 34rem;
}
* { box-sizing: border-box; -webkit-tap-highlight-color: transparent; }
html { -webkit-text-size-adjust: 100%; }
body { margin: 0; background: var(--bg); color: var(--ink); font-family: var(--font-sans);
  font-size: 1.02rem; line-height: 1.65; -webkit-font-smoothing: antialiased; padding-bottom: 4.75rem; }
body.dark { --bg: #201C18; --surface: #2B2621; --ink: #EFE9DF; --ink-soft: #B5AB9E;
  --ink-faint: #8B8175; --rule: #453D34; --overlay-scrim: rgba(0,0,0,0.5); }
a { color: var(--accent); text-decoration-thickness: 1px; text-underline-offset: 2px; }
a:hover { color: var(--accent-deep); }
button { font-family: var(--font-sans); }
a:focus-visible, button:focus-visible, input:focus-visible, textarea:focus-visible, [tabindex]:focus-visible {
  outline: 3px solid var(--accent-deep); outline-offset: 2px; border-radius: 4px; }
main { max-width: var(--max-w); margin: 0 auto; padding: 0 1.25rem; }
section.view { display: none; } section.view.active { display: block; }

/* TOP BAR — TWEAK 3 (basic date) + TWEAK 4 (centered logo placeholder) */
.top-bar { max-width: var(--max-w); margin: 0 auto; padding: 1.5rem 1.25rem 0.25rem;
  display: flex; align-items: center; justify-content: space-between; gap: 1rem; }
.edition-date { font-size: 0.85rem; color: var(--ink-soft); flex: 1; }
.logo-placeholder { flex: 1; text-align: center; font-family: var(--font-serif);
  font-weight: 700; font-size: 1.02rem; letter-spacing: 0.01em; color: var(--ink);
  border: 1px dashed var(--rule); border-radius: 6px; padding: 0.15rem 0.5rem;
  align-self: center; max-width: 9rem; margin: 0 auto; }
.top-bar-right { flex: 1; display: flex; justify-content: flex-end; }
.settings-corner { background: transparent; border: 1px solid var(--rule); border-radius: 50%;
  width: 2.1rem; height: 2.1rem; display: flex; align-items: center; justify-content: center;
  color: var(--ink-faint); cursor: pointer; flex-shrink: 0; }
.settings-corner:hover { border-color: var(--ink-soft); color: var(--ink); }
.settings-corner svg { display: block; stroke: currentColor; }

.episode-affordance { max-width: var(--max-w); margin: 0.5rem auto 0; padding: 0 1.25rem 1.1rem;
  border-bottom: 1px solid var(--rule); }
.episode-affordance button { background: transparent; border: none; padding: 0;
  font-size: 0.85rem; color: var(--accent); cursor: pointer; }
.episode-affordance button:hover { color: var(--accent-deep); }
.episode-affordance .episode-meta { color: var(--ink-faint); font-size: 0.85rem; }
.episode-affordance audio { display: block; width: 100%; margin-top: 0.6rem; }

.glance { font-size: 0.87rem; color: var(--ink-soft); margin: 1.1rem 0 2.25rem;
  padding-bottom: 1.5rem; border-bottom: 1px solid var(--rule); }
.glance a { color: var(--ink-soft); text-decoration: none; }
.glance a:hover { color: var(--accent); }
.glance .sep { color: var(--ink-faint); margin: 0 0.4em; }

article.story { padding: 0 0 3rem; }
article.story:last-of-type { padding-bottom: 1rem; }
.tracked-marker { display: inline-block; font-size: 0.74rem; font-weight: 600;
  color: var(--tracked); margin-bottom: 0.6rem; }
.tracked-marker::before { content: "\\25CF "; }
.override-note { font-size: 0.82rem; font-weight: 700; color: var(--accent-deep);
  background: rgba(168,93,62,0.08); padding: 0.65rem 0.9rem; border-radius: var(--radius); margin: 0 0 1rem; }
.override-note .reason { display: block; font-weight: 400; margin-top: 0.2rem; color: var(--ink); }
h2.headline, h3.headline, h4.headline { font-family: var(--font-serif); font-weight: 700;
  margin: 0 0 0.85rem; line-height: 1.28; }
h2.headline { font-size: 1.5rem; } h3.headline { font-size: 1.2rem; }
h4.headline { font-size: 1.03rem; margin-bottom: 0.4rem; }
p.lede { margin: 0 0 1rem; }
.movement { margin: 0 0 0.85rem; }
.movement-label { display: block; font-size: 0.7rem; font-weight: 600; letter-spacing: 0.06em;
  text-transform: uppercase; color: var(--ink-faint); margin: 0 0 0.3rem; }
.movement p { margin: 0; }
.my-read { font-style: italic; }
.quick-hit p { margin: 0 0 0.5rem; font-size: 0.95rem; }
.meta-footnote { font-size: 0.74rem; color: var(--ink-faint); margin-top: 1rem; line-height: 1.5; }
.follow-story { margin-top: 0.5rem; }
.follow-story button { background: transparent; border: none; padding: 0; font-size: 0.8rem;
  color: var(--ink-soft); cursor: pointer; }
.follow-story button:hover { color: var(--accent); }
.follow-story button.confirming { color: var(--tracked); font-weight: 600; }
.follow-story button.followed { color: var(--tracked); }

.footer-tag { margin-top: 1.5rem; padding-top: 1.25rem; border-top: 1px solid var(--rule); }
.footer-tag button.disclosure-trigger { background: transparent; border: none; padding: 0;
  font-size: 0.74rem; color: var(--ink-faint); cursor: pointer; display: inline-flex;
  align-items: center; gap: 0.3rem; }
.footer-tag button.disclosure-trigger:hover { color: var(--ink-soft); }
.footer-tag button.disclosure-trigger .caret { font-size: 0.65rem; transition: transform 150ms ease-out; display: inline-block; }
.footer-tag button.disclosure-trigger[aria-expanded="true"] .caret { transform: rotate(90deg); }
.footer-detail { font-size: 0.74rem; color: var(--ink-faint); line-height: 1.6; margin-top: 0.75rem; display: none; }
.footer-detail.open { display: block; }
.footer-detail p { margin: 0 0 0.5rem; } .footer-detail p:last-child { margin-bottom: 0; }

.state-panel { background: var(--surface); border-radius: var(--radius); padding: 1.75rem 1.5rem; margin-top: 1.5rem; }
.state-panel h3 { font-family: var(--font-serif); font-size: 1.15rem; margin: 0 0 0.6rem; }
.state-panel p { font-size: 0.9rem; color: var(--ink-soft); margin: 0 0 1rem; }
.steps { list-style: none; margin: 0 0 1rem; padding: 0; font-family: var(--font-mono); font-size: 0.78rem; color: var(--ink-faint); }
.steps li { padding: 0.2rem 0; }
.steps li.done { color: var(--ink); } .steps li.done::before { content: "\\2713 "; color: var(--tracked); }
.steps li.active::before { content: "\\2026 "; } .steps li.pending::before { content: "\\00B7 "; }
.error-text { font-size: 0.85rem; color: var(--danger); margin: 0 0 1rem; }
.cta-quiet { display: inline-block; background: var(--ink); color: var(--bg); font-size: 0.85rem;
  border: none; padding: 0.6rem 1.1rem; border-radius: var(--radius); cursor: pointer; }
.cta-outline { display: inline-block; background: transparent; color: var(--ink-soft); font-size: 0.85rem;
  border: 1px solid var(--rule); padding: 0.55rem 1.05rem; border-radius: var(--radius); cursor: pointer; }
.cta-outline:hover { border-color: var(--ink-soft); color: var(--ink); }

h1.view-title { font-family: var(--font-serif); font-size: 1.5rem; margin: 1.5rem 0 1.25rem; }
.archive-row { background: var(--surface); border-radius: var(--radius); padding: 1rem 1.15rem; margin-bottom: 0.65rem; }
.archive-row a { color: var(--ink); text-decoration: none; display: block; }
.archive-date { font-family: var(--font-serif); font-size: 1.03rem; font-weight: 700; margin: 0 0 0.25rem; }
.archive-keywords { font-size: 0.82rem; color: var(--ink-soft); margin: 0; }
.archive-keywords .sep { color: var(--ink-faint); margin: 0 0.3em; }

.following-switcher { display: flex; gap: 1.5rem; margin: 1.5rem 0 1.5rem; padding-bottom: 0.75rem; border-bottom: 1px solid var(--rule); }
.following-switcher button { background: transparent; border: none; padding: 0 0 0.5rem;
  font-size: 0.92rem; font-weight: 600; color: var(--ink-faint); cursor: pointer;
  margin-bottom: -0.8rem; border-bottom: 2px solid transparent; }
.following-switcher button.current { color: var(--ink); border-bottom-color: var(--accent); }
.sub-view { display: none; } .sub-view.active { display: block; }

.indicator-note { font-size: 0.8rem; color: var(--ink-faint); margin: 0 0 1.25rem; }
.indicator-note .dot { color: var(--tracked); }
/* TWEAK 1+2: dot ~5% smaller than v5's 0.7rem, and aligned with the story
   TITLE line — flex-start plus an optical offset matching .dossier-topic's
   first line box (1.08rem * 1.28lh ≈ 1.38rem box; dot 0.66rem -> ~0.36rem
   top offset centers the dot on the title's line). */
.dossier { background: var(--surface); border-radius: var(--radius); padding: 1.1rem 1.25rem;
  margin-bottom: 0.7rem; display: flex; align-items: flex-start; gap: 0.85rem; }
.dossier .dot-slot { width: 0.6rem; flex-shrink: 0; text-align: center; color: var(--tracked);
  font-size: 0.66rem; line-height: 1; align-self: flex-start; margin-top: 0.36rem; }
.dossier-row-body { flex: 1; display: flex; justify-content: space-between; align-items: center; gap: 1rem; flex-wrap: wrap; }
.dossier-main { flex: 1; min-width: 12rem; }
.dossier-topic { font-family: var(--font-serif); font-size: 1.08rem; font-weight: 700; margin: 0 0 0.3rem; }
.dossier-meta { font-size: 0.78rem; color: var(--ink-faint); margin: 0; }
.dossier-actions { display: flex; gap: 0.5rem; flex-shrink: 0; }
.dossier-actions button { font-size: 0.74rem; background: transparent; border: 1px solid var(--rule);
  color: var(--ink-soft); padding: 0.3rem 0.65rem; border-radius: 7px; cursor: pointer; }
.dossier-actions button:hover { border-color: var(--ink); color: var(--ink); }
.dossier-actions button.delete-action:hover { border-color: var(--danger); color: var(--danger); }
.section-h { font-size: 0.75rem; font-weight: 600; letter-spacing: 0.06em; text-transform: uppercase;
  color: var(--ink-faint); margin: 1.75rem 0 0.75rem; }
.section-h:first-child { margin-top: 0; }
.empty-note { font-size: 0.85rem; color: var(--ink-faint); font-style: italic; padding: 0.5rem 0; }

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

.slide-scrim { position: fixed; inset: 0; background: var(--overlay-scrim); z-index: 30; display: none; }
.slide-scrim.open { display: block; }
.slide-panel { position: fixed; top: 0; right: 0; bottom: 0; width: min(88vw, 23rem);
  background: var(--bg); z-index: 31; transform: translateX(100%); transition: transform 220ms ease-out;
  padding: 1.5rem 1.25rem; overflow-y: auto; box-shadow: -2px 0 12px rgba(43,38,33,0.12); }
.slide-panel.open { transform: translateX(0); }
.slide-panel h2 { font-family: var(--font-serif); font-size: 1.3rem; margin: 0 0 1.25rem; }
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
.popup-card h3 { font-family: var(--font-serif); font-size: 1.15rem; margin: 0 0 1rem; }
.popup-card label { display: block; font-size: 0.78rem; color: var(--ink-faint); margin: 0 0 0.35rem; }
.popup-card textarea, .popup-card input[type="text"] { width: 100%; font-family: var(--font-sans);
  font-size: 0.92rem; color: var(--ink); background: var(--bg); border: 1px solid var(--rule);
  border-radius: 8px; padding: 0.6rem 0.75rem; margin-bottom: 1rem; resize: vertical; }
.popup-actions { display: flex; justify-content: flex-end; gap: 0.6rem; margin-top: 0.5rem; flex-wrap: wrap; }
.popup-note { font-size: 0.8rem; color: var(--ink-faint); margin: -0.5rem 0 1rem; }
.popup-status { font-size: 0.82rem; color: var(--ink-faint); margin: 0.5rem 0 1rem; display: none; }
.popup-status.showing { display: block; }
.popup-status.found { color: var(--tracked); }
.popup-status.err { color: var(--danger); }

nav.bottom-nav { position: fixed; bottom: 0; left: 0; right: 0; background: var(--bg);
  border-top: 1px solid var(--rule); display: flex; justify-content: space-around;
  padding: 0.5rem 0 max(0.5rem, env(safe-area-inset-bottom)); z-index: 15; }
nav.bottom-nav button { background: transparent; border: none; display: flex; flex-direction: column;
  align-items: center; gap: 0.25rem; font-size: 0.7rem; color: var(--ink-faint); cursor: pointer;
  padding: 0.25rem 0.85rem; }
nav.bottom-nav button .icon svg { display: block; stroke: currentColor; }
nav.bottom-nav button.current { color: var(--accent); font-weight: 600; }

@media (min-width: 640px) { main, .top-bar, .episode-affordance { max-width: 38rem; } }
"""

# The full page shell. Placeholders: {css} {date_label} {episode_html}
# {today_html} {following_html} {archive_html} {settings_html} {popups_html} {js}
PAGE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>NewsLens</title>
<style>{css}</style>
</head>
<body>
<div class="top-bar">
  <span class="edition-date">{date_label}</span>
  <!-- TWEAK 4: logo placeholder, centered — the principal designs the real
       logo later; this dashes-outlined wordmark holds the slot honestly. -->
  <span class="logo-placeholder" aria-label="NewsLens logo placeholder">NewsLens</span>
  <span class="top-bar-right">
    <button class="settings-corner" aria-label="Settings" onclick="openSettings()">
      <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke-width="1.7">
        <line x1="4" y1="7" x2="20" y2="7"/><circle cx="14" cy="7" r="2" fill="var(--bg)"/>
        <line x1="4" y1="12" x2="20" y2="12"/><circle cx="9" cy="12" r="2" fill="var(--bg)"/>
        <line x1="4" y1="17" x2="20" y2="17"/><circle cx="16" cy="17" r="2" fill="var(--bg)"/>
      </svg>
    </button>
  </span>
</div>
{episode_html}
<main>
<section id="view-today" class="view active">{today_html}</section>
<section id="view-following" class="view">{following_html}</section>
<section id="view-archive" class="view">{archive_html}</section>
</main>
<div class="slide-scrim" id="slide-scrim" onclick="closeSettings()"></div>
<div class="slide-panel" id="slide-panel" role="dialog" aria-label="Settings" aria-hidden="true">
{settings_html}
</div>
{popups_html}
<nav class="bottom-nav">
  <button class="current" data-nav="today" onclick="showView('today', this)">
    <span class="icon"><svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke-width="1.6"><path d="M4 4h11a3 3 0 0 1 3 3v13H7a3 3 0 0 1-3-3V4Z"/><path d="M18 7h2v13h-2"/><path d="M8 9h6M8 12h6M8 15h4"/></svg></span>
    Today
  </button>
  <button data-nav="following" onclick="showView('following', this)">
    <span class="icon"><svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke-width="1.6"><path d="M6 3h12v18l-6-4-6 4V3Z"/></svg></span>
    Following
  </button>
  <button data-nav="archive" onclick="showView('archive', this)">
    <span class="icon"><svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke-width="1.6"><rect x="3.5" y="5" width="17" height="16" rx="1.5"/><path d="M3.5 9.5h17" stroke-width="2.2"/><path d="M8 3v4M16 3v4"/></svg></span>
    Archive
  </button>
</nav>
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
function showView(name, navEl) {
  document.querySelectorAll('.view').forEach(function (v) { v.classList.remove('active'); });
  document.getElementById('view-' + name).classList.add('active');
  document.querySelectorAll('.bottom-nav button').forEach(function (b) { b.classList.remove('current'); });
  var target = navEl && navEl.tagName === 'BUTTON' ? navEl : document.querySelector('[data-nav="' + name + '"]');
  if (target) target.classList.add('current');
  window.scrollTo(0, 0);
}
function showSub(name, btnEl) {
  document.querySelectorAll('.sub-view').forEach(function (v) { v.classList.remove('active'); });
  document.getElementById('sub-' + name).classList.add('active');
  document.querySelectorAll('.following-switcher button').forEach(function (b) { b.classList.remove('current'); });
  btnEl.classList.add('current');
}
function api(path, body, cb) {
  fetch(path, { method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify(body || {}) })
    .then(function (r) { return r.json(); })
    .then(function (d) { if (cb) cb(d); })
    .catch(function (e) { if (cb) cb({ok: false, error: String(e)}); });
}
/* Per-story follow: in-place swap by design (v3 §Today #3) — the one
   popup-pattern carve-out; requires no further input, so no popup. */
function toggleFollow(btn) {
  var topic = btn.getAttribute('data-topic');
  var pressed = btn.getAttribute('aria-pressed') === 'true';
  if (pressed) {
    api('/api/unfollow', {topic: topic}, function () {});
    btn.setAttribute('aria-pressed', 'false');
    btn.classList.remove('followed');
    btn.textContent = '\\uFF0B Follow this story';
    return;
  }
  api('/api/follow', {topic: topic, briefing_date: CURRENT_DATE}, function () {});
  btn.classList.add('confirming');
  btn.textContent = '\\u2713 Following \\u2014 see it under Following \\u2192 Ongoing stories';
  setTimeout(function () {
    btn.classList.remove('confirming');
    btn.classList.add('followed');
    btn.setAttribute('aria-pressed', 'true');
    btn.textContent = 'Following this story';
  }, 1800);
}
function toggleFooterDisclosure() {
  var btn = document.getElementById('footer-disclosure-btn');
  var detail = document.getElementById('footer-disclosure-detail');
  var expanded = btn.getAttribute('aria-expanded') === 'true';
  btn.setAttribute('aria-expanded', String(!expanded));
  detail.classList.toggle('open', !expanded);
}
function toggleEpisode() {
  var el = document.getElementById('episode-player');
  if (!el) return;
  if (el.style.display === 'none' || !el.style.display) {
    el.style.display = 'block'; el.play();
  } else { el.paused ? el.play() : el.pause(); }
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
try { if (localStorage.getItem('newslens-dark') === 'true') {
  document.body.classList.add('dark');
  var t = document.getElementById('dark-toggle'); if (t) t.setAttribute('aria-checked', 'true');
} } catch (e) {}
var lastFocusedBeforePopup = null;
function openPopup(id) {
  lastFocusedBeforePopup = document.activeElement;
  var el = document.getElementById(id);
  el.classList.add('open');
  var firstField = el.querySelector('input, textarea, button');
  if (firstField) firstField.focus();
}
function closePopup(id) {
  document.getElementById(id).classList.remove('open');
  if (lastFocusedBeforePopup) lastFocusedBeforePopup.focus();
}
var noteTopic = null;
function openEditNote(topicName, existing) {
  noteTopic = topicName;
  document.getElementById('edit-note-topic-name').textContent = topicName;
  document.getElementById('edit-note-textarea').value = existing || '';
  openPopup('popup-edit-note');
}
function saveNote() {
  api('/api/note', {topic: noteTopic, note: document.getElementById('edit-note-textarea').value},
      function () { closePopup('popup-edit-note'); location.reload(); });
}
function openAddStory() { openPopup('popup-add-story'); }
function addStory() {
  var v = document.getElementById('add-story-input').value.trim();
  if (!v) return;
  api('/api/follow', {topic: v, briefing_date: CURRENT_DATE},
      function () { closePopup('popup-add-story'); location.reload(); });
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
    if (d.ok) { closePopup('popup-add-topic'); location.reload(); }
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
      setTimeout(function () { closePopup('popup-add-writer'); location.reload(); }, 1200);
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
      function () { closePopup('popup-delete-confirm'); location.reload(); });
}
function threadAction(action, topic) {
  api('/api/' + action, {topic: topic}, function () { location.reload(); });
}
function removeToken(kind, name, el) {
  api('/api/' + kind + '/remove', {name: name}, function (d) {
    if (d.ok) { el.closest('.token').style.display = 'none'; }
  });
}
function generateAgain() {
  api('/api/generate', {}, function () { location.reload(); });
}
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
  document.querySelectorAll('.popup-scrim.open').forEach(function (p) { closePopup(p.id); });
});
"""
