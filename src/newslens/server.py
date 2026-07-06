"""newslens serve — the local web UI (milestone 7).

stdlib ONLY (http.server.ThreadingHTTPServer), bound to 127.0.0.1 — this is
a personal, single-user surface; it is never exposed beyond the machine.

Architecture (ADR-0010):
  * ONE server-rendered page carrying all three views (Today / Following /
    Archive) with client-side view switching — exactly the mockup's shape;
    every render is fresh-from-SQLite, no cache, no state in the server
    beyond the single background generation job.
  * Structured stories come from the generation log entry's `stories` field
    (written from M7 on). Pre-M7 briefings fall back to parsing the
    assembled narrative markdown — safe because assemble_narrative() is
    code-owned and deterministic, so the parser mirrors a format we control.
  * Trust furniture (corroboration lines, "Here for", tracked markers,
    override notes) renders from SLOTS — code-owned data — never from prose.
  * Consumption events (the day-30 falsifier): a rendered briefing page-view
    logs `read`; serving the episode WAV from byte 0 logs `listen` (deduped
    to one per briefing-date per calendar day; see events.py). Server-side
    only — no client beacon to trust.
  * Thread mutations go through memory.py's shared verbs — the SAME code
    path as the CLI (sync file -> verb -> render-only file write).
  * sources.yaml edits are LINE-TARGETED (insert/remove/flip single lines),
    never a parse-and-rewrite — the file carries principal comments that a
    regeneration would destroy. Every edit is validated by re-loading the
    file afterward; on failure the original text is restored.
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import threading
import wave
from datetime import datetime, timedelta, timezone
from html import escape
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from urllib.parse import parse_qs, urlparse

from . import config, db, events, memory, paths, webui

DEFAULT_PORT = 8484
DEVELOPING_WINDOW_DAYS = 7  # dot = thread picked up within this many days

# ---------------------------------------------------------------------------
# Background generation job (one at a time; the UI polls /api/status)
# ---------------------------------------------------------------------------


class _GenJob:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.state = "idle"  # idle | running | done | error
        self.error = ""
        self.started_at: Optional[str] = None

    def start(self) -> bool:
        with self.lock:
            if self.state == "running":
                return False
            self.state = "running"
            self.error = ""
            self.started_at = datetime.now(timezone.utc).isoformat()
        threading.Thread(target=self._run, daemon=True).start()
        return True

    def _run(self) -> None:
        try:
            from . import generate
            config.load_env()
            generate.run_generate()
            with self.lock:
                self.state = "done"
        except Exception as exc:  # surfaced verbatim in the error panel
            with self.lock:
                self.state = "error"
                self.error = str(exc)

    def snapshot(self) -> Dict[str, str]:
        with self.lock:
            return {"state": self.state, "error": self.error}


GEN_JOB = _GenJob()


# ---------------------------------------------------------------------------
# Data layer
# ---------------------------------------------------------------------------

def _briefing_row(con: sqlite3.Connection, date: Optional[str] = None):
    if date:
        return con.execute(
            "SELECT * FROM briefings WHERE date = ?", (date,)).fetchone()
    return con.execute(
        "SELECT * FROM briefings ORDER BY date DESC LIMIT 1").fetchone()


def _log_entry_for(date: str) -> Optional[Dict]:
    """Last generation_log entry for a date wins (regenerations append)."""
    log = paths.DATA_DIR / "generation_log.jsonl"
    if not log.exists():
        return None
    found = None
    try:
        for line in log.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                e = json.loads(line)
            except ValueError:
                continue
            if e.get("date") == date and not e.get("sample"):
                found = e
    except OSError:
        return None
    return found


_MOVE_RE = re.compile(r"^\*\*(?P<label>[^*]+):\*\*\s*(?P<text>.*)$", re.S)


def _parse_narrative(narrative: str) -> Tuple[List[Dict], List[str]]:
    """Fallback for pre-M7 briefings: recover story structure from the
    assembled markdown. Mirrors assemble_narrative()'s deterministic format;
    returns (stories, footer_lines). Meta/override furniture is ignored here
    — it re-renders from slots."""
    chunks = (narrative or "").split("\n---\n")
    if len(chunks) < 2:
        return [], []
    footer_lines = [
        ln.strip().strip("*").strip()
        for ln in chunks[-1].strip().splitlines() if ln.strip()
    ]
    stories: List[Dict] = []
    for chunk in chunks[1:-1]:
        blocks = [b.strip() for b in chunk.strip().split("\n\n") if b.strip()]
        story: Dict = {"movements": []}
        for block in blocks:
            if block.startswith("*") and not block.startswith("**"):
                continue  # meta italic line — slots re-render this
            m = _MOVE_RE.match(block)
            if m and block.startswith("**") and block.rstrip().endswith("**") \
                    and "\n" not in block and not m.group("text"):
                pass  # not reachable; headline handled below
            if block.startswith("**") and block.endswith("**") and "headline" not in story:
                story["headline"] = block.strip("*").strip()
                continue
            if m:
                story["movements"].append(
                    {"label": m.group("label").strip(),
                     "text": m.group("text").strip()})
                continue
            if "headline" in story and "lede" not in story:
                story["lede"] = block
            elif "headline" not in story:
                # override label line precedes the headline; slots carry it
                continue
        if story.get("headline"):
            stories.append(story)
    return stories, footer_lines


def _stories_for(row, entry: Optional[Dict]) -> Tuple[List[Dict], List[str]]:
    """Normalize to render shape: headline, lede, movements[{label,text,em}].
    Prefers the log's structured `stories` (M7+); falls back to parsing."""
    narrative = row["narrative_text"] or ""
    parsed_stories, footer_lines = _parse_narrative(narrative)
    raw = (entry or {}).get("stories")
    if isinstance(raw, list) and raw:
        out = []
        for s in raw:
            if not isinstance(s, dict):
                continue
            movements = []
            if s.get("why_it_matters"):
                movements.append({"label": s.get("why_label") or "Why it matters",
                                  "text": s["why_it_matters"]})
            if s.get("my_read"):
                movements.append({"label": "My read", "text": s["my_read"],
                                  "em": True})
            if s.get("watch_for"):
                movements.append({"label": s.get("watch_label") or "Watch for",
                                  "text": s["watch_for"]})
            out.append({"headline": s.get("headline", ""),
                        "lede": s.get("lede", ""), "movements": movements})
        return out, footer_lines
    stories = parsed_stories
    for st in stories:
        for mv in st["movements"]:
            if mv["label"].strip().lower() == "my read":
                mv["em"] = True
    return stories, footer_lines


def _slots_for(row) -> List[Dict]:
    try:
        slots = json.loads(row["story_slots"] or "[]")
        return slots if isinstance(slots, list) else []
    except ValueError:
        return []


def _fmt_local(iso_utc: Optional[str], with_date: bool = False) -> str:
    """Display-local per the addendum; storage stays UTC."""
    if not iso_utc:
        return "unknown"
    try:
        s = iso_utc.replace("Z", "+00:00")
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        local = dt.astimezone()
        t = local.strftime("%I:%M %p").lstrip("0")
        if with_date:
            return f"{local.strftime('%a, %b')} {local.day}, {t}"
        return t
    except ValueError:
        return iso_utc


def _human_date(date_str: str) -> str:
    try:
        d = datetime.strptime(date_str, "%Y-%m-%d")
        return f"{d.strftime('%A, %B')} {d.day}"
    except ValueError:
        return date_str


def _short_date(iso: Optional[str]) -> str:
    if not iso:
        return "—"
    try:
        d = datetime.fromisoformat(iso.replace("Z", "+00:00"))
        return f"{d.strftime('%b')} {d.day}"
    except ValueError:
        return iso[:10]


def _wav_duration(path_str: Optional[str]) -> Optional[str]:
    if not path_str:
        return None
    p = Path(path_str)
    if not p.exists():
        return None
    try:
        with wave.open(str(p), "rb") as w:
            secs = int(round(w.getnframes() / float(w.getframerate() or 1)))
        return f"{secs // 60}:{secs % 60:02d}"
    except (OSError, wave.Error):
        return None


def _following_rows(con: sqlite3.Connection) -> Dict[str, List[Dict]]:
    rows = con.execute(
        "SELECT m.*, b.date AS ref_date FROM memory m LEFT JOIN briefings b"
        " ON b.id = m.last_referenced_briefing_id ORDER BY m.id"
    ).fetchall()
    cutoff = (datetime.now(timezone.utc)
              - timedelta(days=DEVELOPING_WINDOW_DAYS)).strftime("%Y-%m-%d")
    grouped: Dict[str, List[Dict]] = {"active": [], "dormant": [],
                                      "dismissed_user": []}
    for r in rows:
        grouped.setdefault(r["status"], []).append({
            "topic": r["topic"],
            "note": r["principal_note"] or "",
            "since": _short_date(r["created_at"]),
            "last": r["ref_date"] or "",
            "quiet_since": _short_date(r["status_changed_at"]),
            "developing": bool(r["ref_date"] and r["ref_date"] >= cutoff),
        })
    return grouped


def _active_topics_lower(con: sqlite3.Connection) -> set:
    return {r["topic"].lower() for r in con.execute(
        "SELECT topic FROM memory WHERE status = 'active'")}


def _archive_rows(con: sqlite3.Connection) -> List[Dict]:
    out = []
    for r in con.execute(
            "SELECT date, story_slots FROM briefings ORDER BY date DESC"):
        keywords: List[str] = []
        for slot in _slots_for(r):
            for t in slot.get("matched_tags") or []:
                name = t.get("name") if isinstance(t, dict) else None
                if name and name not in keywords:
                    keywords.append(name)
            for name in slot.get("matched_memory") or []:
                if name not in keywords:
                    keywords.append(name)
        if not keywords:
            keywords = [s.get("story_title", "")[:40]
                        for s in _slots_for(r)[:3] if s.get("story_title")]
        out.append({"date": r["date"], "human": _human_date(r["date"]),
                    "keywords": keywords[:3] or ["(no tags recorded)"]})
    return out


# ---------------------------------------------------------------------------
# sources.yaml line surgery (comments survive; validated after every edit)
# ---------------------------------------------------------------------------

_YAML_LOCK = threading.Lock()


def _yaml_edit(mutate) -> Tuple[bool, str]:
    """Apply mutate(lines)->(ok, msg, lines); reload-validate; restore on
    failure."""
    with _YAML_LOCK:
        path = paths.SOURCES_FILE
        original = path.read_text(encoding="utf-8")
        ok, msg, lines = mutate(original.splitlines(keepends=False))
        if not ok:
            return False, msg

        def _write(text: str) -> None:
            # M7 gate ruling 1: atomic replace — this file is the pipeline's
            # root config and Ctrl-C mid-flush is this tool's natural failure
            # mode ("dies with the terminal"), so no torn writes, ever.
            tmp = path.with_name(path.name + ".tmp")
            tmp.write_text(text, encoding="utf-8")
            os.replace(tmp, path)

        _write("\n".join(lines) + "\n")
        try:
            cfg = config.load_sources()
        except Exception as exc:
            _write(original)
            return False, f"edit produced an invalid sources.yaml — reverted ({exc})"
        if cfg.problems:
            # M7 gate finding 1: load_sources reports most malformations via
            # cfg.problems WITHOUT raising — shipping a problems-state file
            # would brick every later pipeline run until a hand-edit. Treat
            # problems as validation failure, same as an exception.
            _write(original)
            return False, ("edit produced an invalid sources.yaml — reverted "
                           f"({'; '.join(cfg.problems[:2])})")
        return True, msg


def _find_interest_list(lines: List[str], level: str) -> Tuple[int, int]:
    """(start, end) line range of `broad:`/`granular:` list items."""
    key = "broad:" if level == "broad" else "granular:"
    in_interests = False
    start = -1
    for i, ln in enumerate(lines):
        if ln.startswith("interests:"):
            in_interests = True
            continue
        if in_interests and ln.strip().startswith(key):
            start = i + 1
            continue
        if start >= 0:
            if ln.startswith("    -") or not ln.strip() \
                    or ln.strip().startswith("#"):
                continue
            return start, i
    return (start, len(lines)) if start >= 0 else (-1, -1)


def _bad_name(name: str) -> str:
    """Structural characters would change sources.yaml's meaning (M7 gate
    finding 1 follow-on): reject with a friendly error before surgery."""
    if ":" in name:
        return "names can't contain ':' (it changes the file's structure)"
    if "\n" in name or "\r" in name:
        return "names can't contain line breaks"
    if name.lstrip().startswith("#"):
        return "names can't start with '#' (that's a comment)"
    return ""


def topic_add(name: str, level: str) -> Tuple[bool, str]:
    if level not in ("broad", "specific"):
        return False, "level must be broad or specific"
    bad = _bad_name(name)
    if bad:
        return False, bad
    yaml_level = "broad" if level == "broad" else "granular"

    def mutate(lines):
        start, end = _find_interest_list(lines, yaml_level)
        if start < 0:
            return False, f"could not locate interests.{yaml_level} in sources.yaml", lines
        existing = {ln.strip()[1:].split("#")[0].strip().lower()
                    for ln in lines[start:end] if ln.strip().startswith("-")}
        if name.lower() in existing:
            return False, f"{name!r} is already in your {level} interests", lines
        insert_at = end
        while insert_at > start and not lines[insert_at - 1].strip():
            insert_at -= 1
        lines.insert(insert_at, f"    - {name}")
        return True, f"added {name!r} as a {level} interest", lines

    return _yaml_edit(mutate)


def topic_remove(name: str) -> Tuple[bool, str]:
    def mutate(lines):
        pat = re.compile(r"^\s{4}-\s*" + re.escape(name) + r"\s*(#.*)?$", re.I)
        for lvl in ("broad", "granular"):
            start, end = _find_interest_list(lines, lvl)
            if start < 0:
                continue
            for i in range(start, end):
                if pat.match(lines[i]):
                    del lines[i]
                    return True, f"removed {name!r}", lines
        return False, f"{name!r} not found in interests", lines

    return _yaml_edit(mutate)


def writer_add(name: str, url: str) -> Tuple[bool, str]:
    """Paste-a-link path: append a followed_analyst source entry at the end
    of the sources list (just before the interests block)."""
    if not url.lower().startswith(("http://", "https://")):
        return False, "that doesn't look like a link — feed URLs start with http(s)://"
    bad = _bad_name(name.strip() or url)
    if bad:
        return False, bad
    cfg = config.load_sources()
    display = name.strip() or url
    for s in cfg.sources:
        if s.name.lower() == display.lower() or s.rss_url == url:
            return False, f"{s.name!r} is already in your sources"

    def mutate(lines):
        anchor = next((i for i, ln in enumerate(lines)
                       if ln.startswith("interests:")), -1)
        if anchor < 0:
            return False, "could not locate the interests block to anchor the insert", lines
        insert_at = anchor
        while insert_at > 0 and (not lines[insert_at - 1].strip()
                                 or lines[insert_at - 1].lstrip().startswith("#")):
            insert_at -= 1
        today = datetime.now().strftime("%Y-%m-%d")
        entry = [
            f"  - name: {display}",
            f"    rss_url: {url}",
            "    followed_analyst: true",
            f"    note: \"principal-followed analyst: added via web UI {today}\"",
        ]
        lines[insert_at:insert_at] = entry
        return True, "their feed is in your sources pool now", lines

    return _yaml_edit(mutate)


def writer_remove(name: str) -> Tuple[bool, str]:
    """Unfollow = disable the whole entry (the feed exists because it was
    followed) + drop the analyst flag. Line-targeted; comments survive."""
    def mutate(lines):
        start = -1
        for i, ln in enumerate(lines):
            # BUG-9: tolerate a trailing inline comment, same as topic_remove —
            # the file is the principal's to comment; a comment must never make
            # a source un-unfollowable (silent-collection risk).
            if re.match(r"^\s{2}-\s+name:\s*" + re.escape(name) + r"\s*(#.*)?$", ln):
                start = i
                break
        if start < 0:
            return False, f"no source named {name!r}", lines
        end = start + 1
        while end < len(lines) and not re.match(r"^\s{2}-\s+name:", lines[end]) \
                and (lines[end].startswith("    ") or not lines[end].strip()):
            if not lines[end].strip():
                break
            end += 1
        flagged = False
        for i in range(start, end):
            if "followed_analyst: true" in lines[i]:
                lines[i] = lines[i].replace("followed_analyst: true",
                                            "followed_analyst: false")
                flagged = True
        if not flagged:
            return False, f"{name!r} is not a followed writer", lines
        if not any("enabled:" in lines[i] for i in range(start, end)):
            lines.insert(start + 1, "    enabled: false")
        else:
            for i in range(start, end):
                if "enabled:" in lines[i]:
                    lines[i] = re.sub(r"enabled:.*", "enabled: false", lines[i])
        return True, f"unfollowed {name!r} (source disabled)", lines

    return _yaml_edit(mutate)


# ---------------------------------------------------------------------------
# Renderers (every dynamic value escaped HERE)
# ---------------------------------------------------------------------------

def _e(v) -> str:
    return escape(str(v if v is not None else ""), quote=True)


def _js_str(v: str) -> str:
    return json.dumps(str(v or ""))


def _render_story(i: int, st: Dict, slot: Dict, tier: str,
                  active_topics: set) -> str:
    h = {"full": "h2", "medium": "h3"}.get(tier, "h4")
    parts = [f'<article class="story{" quick-hit" if tier == "quick" else ""}" id="story-{i}">']

    marks = list(slot.get("matched_memory") or [])
    if marks:
        parts.append(
            f'<span class="tracked-marker">Tracked ongoing story — '
            f'{_e(", ".join(marks))}</span>')
    if slot.get("override"):
        label = slot.get("override_label") or "Editor's override"
        reason = slot.get("world_impact_reason") or ""
        parts.append(
            f'<p class="override-note">{_e(label)}'
            + (f'<span class="reason">{_e(reason)}</span>' if reason else "")
            + "</p>")

    parts.append(f'<{h} class="headline">{_e(st.get("headline", ""))}</{h}>')
    if st.get("lede"):
        parts.append(f'<p class="lede">{_e(st["lede"])}</p>')
    for mv in st.get("movements") or []:
        em = ' my-read' if mv.get("em") else ""
        parts.append(
            f'<div class="movement"><span class="movement-label">'
            f'{_e(mv["label"])}</span><p class="{em.strip()}">{_e(mv["text"])}</p></div>')

    # Meta footnote — CODE-OWNED, from the slot (never prose)
    matches = ", ".join(
        [t.get("name", "") for t in slot.get("matched_tags") or []
         if isinstance(t, dict)] + marks)
    if matches:
        here_for = matches
    elif slot.get("override"):
        here_for = "editor's override — see note above"
    else:
        here_for = "world-impact selection (no tag or thread match)"
    outlets = slot.get("outlets") or []
    meta = slot.get("corroboration_label", "")
    if outlets:
        meta += f' — {", ".join(outlets)}'
    parts.append(
        f'<p class="meta-footnote">{_e(meta)}. Here for: {_e(here_for)}.</p>')

    topic = slot.get("story_title") or st.get("headline") or ""
    followed = topic.lower() in active_topics
    pressed = "true" if followed else "false"
    label = "Following this story" if followed else "＋ Follow this story"
    cls = ' class="followed"' if followed else ""
    parts.append(
        f'<div class="follow-story"><button{cls} data-topic={_e_attr(topic)} '
        f'aria-pressed="{pressed}" onclick="toggleFollow(this)">{_e(label)}'
        f'</button></div>')
    parts.append("</article>")
    return "".join(parts)


def _e_attr(v: str) -> str:
    return '"' + escape(str(v or ""), quote=True) + '"'


def _render_today(con: sqlite3.Connection, row, entry: Optional[Dict],
                  gen_state: Dict[str, str]) -> str:
    if gen_state["state"] == "running":
        return """
<div class="state-panel" id="gen-running">
  <h3>Generating today’s edition…</h3>
  <p>Fetching your sources, ranking, writing, and recording the episode.
     This usually takes a couple of minutes; the page refreshes itself
     when it’s ready.</p>
  <ul class="steps">
    <li class="active">ingest — fetching your sources</li>
    <li class="pending">rank — choosing today’s stories</li>
    <li class="pending">write — drafting the briefing</li>
    <li class="pending">edit — the second read</li>
    <li class="pending">voice — recording the episode</li>
  </ul>
</div>"""
    if gen_state["state"] == "error":
        return f"""
<div class="state-panel">
  <h3>Today’s edition failed</h3>
  <p class="error-text">{_e(gen_state["error"])}</p>
  <p>Nothing was published — a failed run never produces a partial edition.</p>
  <button class="cta-quiet" onclick="generateAgain()">Try again</button>
</div>"""
    if row is None:
        return """
<div class="state-panel">
  <h3>Nothing yet</h3>
  <p>No edition has been generated. The first one takes a couple of minutes:
     it fetches your sources, picks the stories, writes the briefing, and
     records the episode.</p>
  <button class="cta-quiet" onclick="generateAgain()">Generate today’s edition</button>
</div>"""

    stories, footer_lines = _stories_for(row, entry)
    slots = _slots_for(row)
    tiers = (entry or {}).get("tiers") or []
    active = _active_topics_lower(con)

    glance_bits = []
    for i, st in enumerate(stories):
        glance_bits.append(
            f'<a href="#story-{i}">{_e(st.get("headline", ""))}</a>')
    html = []
    if glance_bits:
        html.append('<p class="glance">In today’s briefing: '
                    + '<span class="sep">·</span>'.join(glance_bits) + "</p>")

    for i, st in enumerate(stories):
        slot = slots[i] if i < len(slots) else {}
        tier = tiers[i] if i < len(tiers) else ("full" if i == 0 else "medium" if i <= 2 else "quick")
        html.append(_render_story(i, st, slot, tier, active))

    # Footer disclosure (addendum #3): quiet line; window/caveat/cost a tap away
    gen_local = _fmt_local(row["generated_at"])
    detail_ps = [f"<p>{_e(ln)}</p>" for ln in footer_lines]
    cost = _run_cost(entry)
    dur = _wav_duration(row["audio_file_path"])
    edition_bits = []
    if dur:
        edition_bits.append(f"{dur} audio")
    edition_bits.append(cost)
    detail_ps.append(f'<p>This edition: {_e(" · ".join(edition_bits))}</p>')
    html.append(f"""
<div class="footer-tag">
  <button class="disclosure-trigger" id="footer-disclosure-btn" aria-expanded="false"
          aria-controls="footer-disclosure-detail" onclick="toggleFooterDisclosure()">
    <span class="caret">▸</span> Generated {_e(gen_local)}
  </button>
  <div class="footer-detail" id="footer-disclosure-detail">{"".join(detail_ps)}</div>
</div>""")
    return "".join(html)


def _run_cost(entry: Optional[Dict]) -> str:
    usd = (entry or {}).get("total_usd")
    if usd is None:
        return "cost not recorded for this edition"
    try:
        return f"generated for ${float(usd):.2f}" if float(usd) > 0 \
            else "generated locally at $0 marginal"
    except (TypeError, ValueError):
        return "cost not recorded for this edition"


def _dossier(t: Dict, actions: str, meta: str) -> str:
    dot = "●" if t.get("developing") else ""
    return f"""
<div class="dossier">
  <span class="dot-slot" aria-hidden="true">{dot}</span>
  <div class="dossier-row-body">
    <div class="dossier-main">
      <p class="dossier-topic">{_e(t["topic"])}</p>
      <p class="dossier-meta">{meta}</p>
    </div>
    <div class="dossier-actions">{actions}</div>
  </div>
</div>"""


def _render_following(con: sqlite3.Connection) -> str:
    g = _following_rows(con)
    cfg = config.load_sources()

    def note_btn(t):
        return (f'<button onclick="openEditNote({_e(_js_str(t["topic"]))}, '
                f'{_e(_js_str(t["note"]))})">Edit note</button>')

    ongoing = ['<p class="indicator-note"><span class="dot">●</span> marks a '
               'story still developing — picked up within the last week.</p>']
    ongoing.append('<div class="follow-story" style="margin:0 0 1.25rem;">'
                   '<button onclick="openAddStory()">＋ Follow a new story</button></div>')
    ongoing.append('<p class="section-h">Following</p>')
    if g["active"]:
        for t in g["active"]:
            last = _human_short(t["last"]) if t["last"] else "not picked up yet"
            meta = f"Following since {_e(t['since'])} · Last picked up {_e(last)}"
            acts = note_btn(t) + (
                f'<button onclick="threadAction(\'dismiss\', {_e(_js_str(t["topic"]))})">Stop</button>')
            ongoing.append(_dossier(t, acts, meta))
    else:
        ongoing.append('<p class="empty-note">Nothing yet</p>')

    if g["dormant"]:
        ongoing.append('<p class="section-h">Quiet for now</p>')
        for t in g["dormant"]:
            meta = f"Quiet since {_e(t['quiet_since'])} · revives on its own if the story returns"
            acts = note_btn(t) + (
                f'<button onclick="threadAction(\'revive\', {_e(_js_str(t["topic"]))})">Resume</button>')
            ongoing.append(_dossier(t, acts, meta))

    if g["dismissed_user"]:
        ongoing.append('<p class="section-h">You stopped following</p>')
        for t in g["dismissed_user"]:
            meta = f"Stopped {_e(t['quiet_since'])} · never revives on its own"
            acts = (
                f'<button onclick="threadAction(\'revive\', {_e(_js_str(t["topic"]))})">Resume</button>'
                f'<button class="delete-action" onclick="openDeleteConfirm({_e(_js_str(t["topic"]))})">Delete</button>')
            ongoing.append(_dossier(t, acts, meta))

    def token(name: str, kind: str, label: Optional[str] = None) -> str:
        return (f'<span class="token">{_e(label or name)}'
                f'<button class="token-remove" aria-label="Remove {_e(label or name)}"'
                f' onclick="removeToken({_e(_js_str(kind))}, {_e(_js_str(name))}, this)">×</button></span>')

    topics = [
        '<input class="token-search" type="text" placeholder="Search or add a topic…"'
        ' aria-label="Search or add a topic"'
        ' onkeydown="if(event.key===\'Enter\'){openAddTopic(this.value); this.value=\'\';}">',
        '<p class="token-search-hint">Type a topic and press Enter to add it '
        'as a broad or specific interest.</p>',
    ]
    for group, label in ((cfg.interests_broad, "Broad"),
                         (cfg.interests_granular, "Specific")):
        topics.append(f'<div class="token-group"><p class="token-group-name">'
                      f'{label} ({len(group)})</p><div class="token-list">')
        topics.extend(token(n, "topic") for n in group)
        if not group:
            topics.append('<p class="empty-note">Nothing yet</p>')
        topics.append("</div></div>")

    writers = [
        '<input class="token-search" type="text" placeholder="Search or add a writer…"'
        ' aria-label="Search or add a writer"'
        ' onkeydown="if(event.key===\'Enter\'){openAddWriter(this.value); this.value=\'\';}">',
        '<p class="token-search-hint">Following a writer adds their feed to '
        'your sources and boosts their pieces in ranking.</p>',
        '<div class="token-group"><div class="token-list">',
    ]
    followed = cfg.followed_analyst_sources
    if followed:
        for s in followed:
            m = re.match(r"^(.*)\s+\((.+)\)\s*$", s.name)
            display = f"{m.group(2)} — {m.group(1)}" if m else s.name
            writers.append(token(s.name, "writer", label=display))
    else:
        writers.append('<p class="empty-note">Nothing yet</p>')
    writers.append("</div></div>")

    return f"""
<h1 class="view-title">Following</h1>
<div class="following-switcher" role="tablist">
  <button class="current" onclick="showSub('ongoing', this)">Ongoing stories</button>
  <button onclick="showSub('topics', this)">Topics</button>
  <button onclick="showSub('writers', this)">Writers</button>
</div>
<div id="sub-ongoing" class="sub-view active">{"".join(ongoing)}</div>
<div id="sub-topics" class="sub-view">{"".join(topics)}</div>
<div id="sub-writers" class="sub-view">{"".join(writers)}</div>"""


def _human_short(date_str: str) -> str:
    try:
        d = datetime.strptime(date_str, "%Y-%m-%d")
        return f"{d.strftime('%b')} {d.day}"
    except ValueError:
        return date_str


def _render_archive(con: sqlite3.Connection) -> str:
    rows = _archive_rows(con)
    if not rows:
        return ('<h1 class="view-title">Archive</h1>'
                '<p class="empty-note">Nothing yet</p>')
    html = ['<h1 class="view-title">Archive</h1>']
    for r in rows:
        kw = '<span class="sep">·</span>'.join(_e(k) for k in r["keywords"])
        html.append(f"""
<div class="archive-row">
  <a href="/?date={_e(r["date"])}">
    <p class="archive-date">{_e(r["human"])}</p>
    <p class="archive-keywords">{kw}</p>
  </a>
</div>""")
    return "".join(html)


def _render_settings(con: sqlite3.Connection, row, entry: Optional[Dict]) -> str:
    cfg = config.load_sources()
    enabled = len(cfg.fetchable_sources) + len(cfg.reference_only_sources)
    # M7 gate finding 7: display the CONFIGURED engine, not the constant.
    engine = ("Kokoro (local, $0/episode)" if cfg.tts_engine == "kokoro"
              else "OpenAI gpt-4o-mini-tts (~$0.015/min)")
    cap = config.budget_cap_usd_per_run()
    gen_val = ("Generated " + _fmt_local(row["generated_at"])) if row is not None \
        else "Nothing yet"
    # Sources / Voice / Budget rows show VALUES only — their editors aren't
    # built in M7, and a dead "Edit" button that looks operable would be an
    # accessibility miss by the addendum's own standard. Values are honest;
    # affordances arrive with their features.
    return f"""
<button class="slide-close" onclick="closeSettings()" aria-label="Close settings">×</button>
<h2>Settings</h2>
<div class="settings-row">
  <div class="settings-row-main">
    <p class="settings-row-label">Today’s edition</p>
    <p class="settings-row-value">{_e(gen_val)}</p>
  </div>
  <button class="settings-row-action primary" onclick="generateAgain(); closeSettings();">Generate again</button>
</div>
<div class="settings-row">
  <div class="settings-row-main">
    <p class="settings-row-label">Account</p>
    <p class="settings-row-value">Single user, local — no account yet</p>
  </div>
</div>
<div class="settings-row">
  <div class="settings-row-main">
    <p class="settings-row-label">Sources</p>
    <p class="settings-row-value">{enabled} enabled — edit sources.yaml</p>
  </div>
</div>
<div class="settings-row">
  <div class="settings-row-main">
    <p class="settings-row-label">Voice / model</p>
    <p class="settings-row-value">{_e(engine)}</p>
  </div>
</div>
<div class="settings-row">
  <div class="settings-row-main">
    <p class="settings-row-label">Budget cap per run</p>
    <p class="settings-row-value">${cap:.2f}</p>
  </div>
</div>
<div class="settings-row">
  <div class="settings-row-main">
    <p class="settings-row-label">Dark mode</p>
  </div>
  <div class="toggle-switch" id="dark-toggle" role="switch" aria-checked="false"
       tabindex="0" aria-label="Dark mode" onclick="toggleDark(this)"
       onkeydown="if(event.key===' '||event.key==='Enter'){{event.preventDefault();this.click();}}"></div>
</div>"""


def build_page(con: sqlite3.Connection, date: Optional[str] = None) -> Tuple[str, Optional[str]]:
    """Returns (html, briefing_date_rendered)."""
    gen_state = GEN_JOB.snapshot()
    row = _briefing_row(con, date)
    entry = _log_entry_for(row["date"]) if row is not None else None

    date_label = _human_date(row["date"]) if row is not None \
        else _human_date(datetime.now().strftime("%Y-%m-%d"))

    episode_html = ""
    if row is not None and gen_state["state"] != "running":
        dur = _wav_duration(row["audio_file_path"])
        if dur:
            episode_html = f"""
<div class="episode-affordance">
  <button onclick="toggleEpisode()" aria-label="Play full episode, {_e(dur)}">▷ Play full episode
    <span class="episode-meta"> · {_e(dur)}</span></button>
  <audio id="episode-player" style="display:none" controls preload="none"
         src="/audio/{_e(row["date"])}.wav"></audio>
</div>"""

    page = webui.PAGE.format(
        css=webui.CSS,
        date_label=_e(date_label),
        episode_html=episode_html,
        today_html=_render_today(con, row, entry, gen_state),
        following_html=_render_following(con),
        archive_html=_render_archive(con),
        settings_html=_render_settings(con, row, entry),
        popups_html=webui.POPUPS,
        js=webui.JS,
    )
    # M7 gate finding 4: a read event means the briefing BODY was actually
    # shown — when the running/error panel replaces it, nobody read anything,
    # and the raw table must stay honest for tomorrow's questions.
    briefing_shown = row is not None and gen_state["state"] not in ("running", "error")
    rendered_date = row["date"] if briefing_shown else None
    page = page.replace("<body>", f'<body data-briefing-date="{_e(rendered_date or "")}">', 1)
    return page, rendered_date


# ---------------------------------------------------------------------------
# HTTP handler
# ---------------------------------------------------------------------------

class Handler(BaseHTTPRequestHandler):
    server_version = "newslens"

    def log_message(self, fmt, *args):  # quiet default; errors still raise
        pass

    def _send_html(self, body: str, status: int = 200) -> None:
        data = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _send_json(self, obj: Dict, status: int = 200) -> None:
        data = json.dumps(obj).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _read_body(self) -> Dict:
        try:
            n = int(self.headers.get("Content-Length") or 0)
            if n <= 0 or n > 1_000_000:
                return {}
            obj = json.loads(self.rfile.read(n).decode("utf-8"))
            return obj if isinstance(obj, dict) else {}
        except (ValueError, OSError):
            return {}

    # -- GET ---------------------------------------------------------------
    def do_GET(self) -> None:  # noqa: N802 (stdlib API)
        parsed = urlparse(self.path)
        try:
            if parsed.path == "/":
                return self._page(parse_qs(parsed.query))
            if parsed.path == "/api/status":
                return self._send_json(GEN_JOB.snapshot())
            m = re.match(r"^/audio/(\d{4}-\d{2}-\d{2})\.wav$", parsed.path)
            if m:
                return self._audio(m.group(1))
            self._send_html("<h1>Not found</h1>", 404)
        except BrokenPipeError:
            pass
        except Exception as exc:
            self._send_html(f"<h1>Server error</h1><pre>{_e(exc)}</pre>", 500)

    def _page(self, qs: Dict[str, List[str]]) -> None:
        date = (qs.get("date") or [None])[0]
        if date and not re.match(r"^\d{4}-\d{2}-\d{2}$", date):
            date = None
        con = db.connect()
        try:
            page, rendered = build_page(con, date)
            if rendered:
                # the day-30 falsifier: an actual open of a real briefing
                events.log_read(con, rendered)
            self._send_html(page)
        finally:
            con.close()

    def _audio(self, date: str) -> None:
        con = db.connect()
        try:
            row = con.execute(
                "SELECT audio_file_path FROM briefings WHERE date = ?",
                (date,)).fetchone()
            path = Path(row["audio_file_path"]) if row and row["audio_file_path"] else None
            if not path or not path.exists():
                return self._send_html("<h1>No episode for that date</h1>", 404)
            size = path.stat().st_size
            range_header = self.headers.get("Range") or ""
            m = re.match(r"bytes=(\d*)-(\d*)$", range_header.strip())
            start, end = 0, size - 1
            partial = False
            if m and (m.group(1) or m.group(2)):
                partial = True
                if m.group(1):
                    start = int(m.group(1))
                    if m.group(2):
                        end = min(int(m.group(2)), size - 1)
                else:  # suffix range: last N bytes
                    start = max(0, size - int(m.group(2)))
            if start >= size:
                self.send_response(416)
                self.send_header("Content-Range", f"bytes */{size}")
                self.end_headers()
                return
            if start == 0:
                # a play begins at byte 0 exactly once — the listen event
                # (further deduped to one per briefing-date per day)
                events.log_listen(con, date)
            self.send_response(206 if partial else 200)
            self.send_header("Content-Type", "audio/wav")
            self.send_header("Accept-Ranges", "bytes")
            self.send_header("Content-Length", str(end - start + 1))
            if partial:
                self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
            self.end_headers()
            with path.open("rb") as f:
                f.seek(start)
                remaining = end - start + 1
                while remaining > 0:
                    chunk = f.read(min(65536, remaining))
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    remaining -= len(chunk)
        except BrokenPipeError:
            pass
        finally:
            con.close()

    # -- POST ---------------------------------------------------------------
    def do_POST(self) -> None:  # noqa: N802 (stdlib API)
        parsed = urlparse(self.path)
        # M7 gate finding 2 (CSRF): a cross-origin no-cors POST cannot carry
        # this content type without a preflight this server never grants; the
        # UI's single fetch helper always sends it. Blocks hostile webpages
        # from firing the spend-capable and destroy-capable endpoints.
        ctype = (self.headers.get("Content-Type") or "").split(";")[0].strip().lower()
        if ctype != "application/json":
            return self._send_json(
                {"ok": False, "error": "unsupported content type"}, 415)
        body = self._read_body()
        try:
            handler = {
                "/api/follow": self._api_follow,
                "/api/unfollow": self._api_dismiss,
                "/api/dismiss": self._api_dismiss,
                "/api/revive": self._api_revive,
                "/api/thread/delete": self._api_delete,
                "/api/note": self._api_note,
                "/api/topic/add": self._api_topic_add,
                "/api/topic/remove": self._api_topic_remove,
                "/api/writer/add": self._api_writer_add,
                "/api/writer/remove": self._api_writer_remove,
                "/api/generate": self._api_generate,
            }.get(parsed.path)
            if handler is None:
                return self._send_json({"ok": False, "error": "no such endpoint"}, 404)
            handler(body)
        except BrokenPipeError:
            pass
        except Exception as exc:
            self._send_json({"ok": False, "error": str(exc)}, 500)

    def _with_memory(self, fn) -> Dict:
        """The CLI verb protocol: sync -> verb -> render-only file write."""
        con = db.connect()
        try:
            try:
                memory.sync_memory(con)
            except memory.MemorySyncError as exc:
                return {"ok": False, "error": str(exc)}
            result = fn(con)
            memory.write_memory_file(con)
            return result
        finally:
            con.close()

    def _topic_arg(self, body: Dict) -> str:
        topic = str(body.get("topic") or "").strip()
        if memory.SEPARATOR in topic:
            return ""
        return topic

    def _api_follow(self, body: Dict) -> None:
        topic = self._topic_arg(body)
        if not topic:
            return self._send_json({"ok": False, "error": "topic required"}, 400)
        briefing_date = str(body.get("briefing_date") or "").strip() or None

        def verb(con):
            ref_id = None
            if briefing_date:
                r = con.execute("SELECT id FROM briefings WHERE date = ?",
                                (briefing_date,)).fetchone()
                ref_id = r["id"] if r else None
            outcome = memory.add_thread(con, topic,
                                        last_referenced_briefing_id=ref_id)
            return {"ok": True, "outcome": outcome}

        self._send_json(self._with_memory(verb))

    def _api_dismiss(self, body: Dict) -> None:
        topic = self._topic_arg(body)
        if not topic:
            return self._send_json({"ok": False, "error": "topic required"}, 400)
        self._send_json(self._with_memory(
            lambda con: {"ok": memory.dismiss_thread(con, topic)}))

    def _api_revive(self, body: Dict) -> None:
        topic = self._topic_arg(body)
        if not topic:
            return self._send_json({"ok": False, "error": "topic required"}, 400)
        self._send_json(self._with_memory(
            lambda con: {"ok": memory.add_thread(con, topic) in
                         ("revived", "already-active", "added")}))

    def _api_delete(self, body: Dict) -> None:
        topic = self._topic_arg(body)
        if not topic:
            return self._send_json({"ok": False, "error": "topic required"}, 400)
        def act(con):
            ok, msg = memory.delete_thread(con, topic)
            return {"ok": ok} if ok else {"ok": False, "error": msg}
        self._send_json(self._with_memory(act))

    def _api_note(self, body: Dict) -> None:
        topic = self._topic_arg(body)
        if not topic:
            return self._send_json({"ok": False, "error": "topic required"}, 400)
        note = str(body.get("note") or "")
        if memory.SEPARATOR in note:
            return self._send_json(
                {"ok": False,
                 "error": f"note may not contain {memory.SEPARATOR!r}"}, 400)
        self._send_json(self._with_memory(
            lambda con: {"ok": memory.set_note(con, topic, note)}))

    def _api_topic_add(self, body: Dict) -> None:
        name = str(body.get("name") or "").strip()
        level = str(body.get("level") or "").strip()
        if not name:
            return self._send_json({"ok": False, "error": "name required"}, 400)
        ok, msg = topic_add(name, level)
        self._send_json({"ok": ok, "detail" if ok else "error": msg})

    def _api_topic_remove(self, body: Dict) -> None:
        name = str(body.get("name") or "").strip()
        if not name:
            return self._send_json({"ok": False, "error": "name required"}, 400)
        ok, msg = topic_remove(name)
        self._send_json({"ok": ok, "detail" if ok else "error": msg})

    def _api_writer_add(self, body: Dict) -> None:
        name = str(body.get("name") or "").strip()
        url = str(body.get("url") or "").strip()
        if not url:
            return self._send_json({"ok": False, "error": "feed link required"}, 400)
        ok, msg = writer_add(name, url)
        self._send_json({"ok": ok, "detail" if ok else "error": msg})

    def _api_writer_remove(self, body: Dict) -> None:
        name = str(body.get("name") or "").strip()
        if not name:
            return self._send_json({"ok": False, "error": "name required"}, 400)
        ok, msg = writer_remove(name)
        self._send_json({"ok": ok, "detail" if ok else "error": msg})

    def _api_generate(self, body: Dict) -> None:
        started = GEN_JOB.start()
        self._send_json({"ok": True,
                         "detail": "started" if started else "already running"})


def serve(port: int = DEFAULT_PORT) -> int:
    db.migrate()
    httpd = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(f"NewsLens is reading the paper at http://127.0.0.1:{port}/  "
          "(Ctrl-C stops it; localhost only, by design)", flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")
    finally:
        httpd.server_close()
    return 0
