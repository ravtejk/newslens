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
        finally:
            # Ride 24 (M8): a BaseException (KeyboardInterrupt delivered to
            # this thread, SystemExit from deep inside a lib, MemoryError)
            # would skip the except above and strand state at "running" —
            # the UI would show the loading panel until restart. The guard
            # keeps state truthful; the BaseException itself still
            # propagates and ends the thread.
            with self.lock:
                if self.state == "running":
                    self.state = "error"
                    self.error = ("generation thread exited abnormally "
                                  "(BaseException) — check the serve "
                                  "terminal for the traceback")

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
            # Ordering note (ride 26, M8): the headline check below runs
            # BEFORE the movement branch, so a colon-terminated bold
            # headline ("**The question now:**") — which _MOVE_RE would
            # also match, with empty text — binds as the headline. A dead
            # third branch that restated this was removed here.
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


def _is_calendar_date(date_str: str) -> bool:
    """True only for a zero-padded YYYY-MM-DD that is a REAL calendar date.
    The ISO-shape regex alone (the old guard) accepts calendar-impossible tokens
    like '2026-13-45', which then render as live dead-end edition links; strptime
    rejects the impossible month/day, and the strftime round-trip also rejects
    non-zero-padded shapes the /?date= route would 404 on."""
    try:
        return datetime.strptime(date_str, "%Y-%m-%d").strftime("%Y-%m-%d") == date_str
    except ValueError:
        return False


def _short_date(iso: Optional[str]) -> str:
    if not iso:
        return "—"
    try:
        d = datetime.fromisoformat(iso.replace("Z", "+00:00"))
        return f"{d.strftime('%b')} {d.day}"
    except ValueError:
        return iso[:10]


_WINDOW_RE = re.compile(r"overs items fetched\s+(\S+)\s*(?:→|->)\s*(\S+)")


def _coverage_window_line(footer_lines: List[str]) -> str:
    """NL-58 ruling 6: the collection window is surfaced as a quiet VISIBLE
    line on Today, not buried in the tap-away footer. Reads the same
    'Covers items fetched X → Y' phrase the detail carries and renders it in
    the plain 'from X to Y' register (DIRECTION quiet)."""
    for ln in footer_lines:
        m = _WINDOW_RE.search(ln)
        if m:
            # NL-60 gate F3: both tokens must be real calendar dates —
            # _human_short returns its INPUT on parse failure, so a garbage
            # token would otherwise render as a fake value on a trust line.
            if not (_is_calendar_date(m.group(1)[:10])
                    and _is_calendar_date(m.group(2)[:10])):
                return ""
            a, b = _human_short(m.group(1)[:10]), _human_short(m.group(2)[:10])
            if a and b:
                return f"Covers items from {a} to {b}"
    return ""


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


def _player_extra_controls(player_id: str) -> str:
    """NL-58 ruling 7: playback speed (1x/1.25x/1.5x/2x) + skip ±15s on top of
    the native audio controls (which keep scrubbing and volume). Minimal
    buttons wired to the shared skipAudio/cycleSpeed JS; revealed with the
    player. player_id is a code-owned literal (never user input)."""
    pid = _e(player_id)
    return (
        f'<div class="player-extra" id="{pid}-extra" style="display:none">'
        f'<button type="button" class="player-btn" '
        f'onclick="skipAudio(\'{pid}\', -15)" aria-label="Back 15 seconds">'
        '« 15s</button>'
        f'<button type="button" class="player-btn speed-btn" '
        f'onclick="cycleSpeed(\'{pid}\', this)" aria-label="Change playback '
        'speed">1×</button>'
        f'<button type="button" class="player-btn" '
        f'onclick="skipAudio(\'{pid}\', 15)" aria-label="Forward 15 seconds">'
        '15s »</button>'
        '</div>')


def _following_rows(con: sqlite3.Connection) -> Dict[str, List[Dict]]:
    rows = con.execute(
        "SELECT m.*, b.date AS ref_date FROM memory m LEFT JOIN briefings b"
        " ON b.id = m.last_referenced_briefing_id ORDER BY m.id"
    ).fetchall()
    cutoff = (datetime.now(timezone.utc)
              - timedelta(days=DEVELOPING_WINDOW_DAYS)).strftime("%Y-%m-%d")
    today = datetime.now().strftime("%Y-%m-%d")
    grouped: Dict[str, List[Dict]] = {"active": [], "dormant": [],
                                      "dismissed_user": []}
    for r in rows:
        # NL-58 future-date guard: "last picked up" is the DATE of the joined
        # last-referenced briefing; a value later than today is data corruption
        # (a briefing dated in the future) and must never render as a real
        # pickup — it degrades to "not yet picked up", the honest state. Guards
        # the reported "Last picked up Jul 13" (a future date) at the source.
        ref_date = r["ref_date"] or ""
        last = ref_date if (ref_date and ref_date <= today) else ""
        grouped.setdefault(r["status"], []).append({
            "topic": r["topic"],
            "note": r["principal_note"] or "",
            "since": _short_date(r["created_at"]),
            "last": last,
            "quiet_since": _short_date(r["status_changed_at"]),
            "developing": bool(last and last >= cutoff),
        })
    # P1 polish (2026-07-06): Ongoing sorts by recency of last pickup, most
    # recent first; never-picked-up threads sink to the end, original (id)
    # order preserved within ties — display-order only, no lifecycle change.
    grouped["active"].sort(key=lambda th: th["last"] or "", reverse=True)
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
        # Ride 25 (M8), BUG-9's write-side sibling: match the KEY, not the
        # substring (a comment that merely mentions "enabled:" must not be
        # rewritten), and preserve any inline comment when flipping the
        # value — the file is the principal's to comment.
        enabled_re = re.compile(r"^(\s*enabled:\s*)\S+(\s*#.*)?$")
        hit = next((i for i in range(start, end)
                    if enabled_re.match(lines[i])), None)
        if hit is None:
            lines.insert(start + 1, "    enabled: false")
        else:
            lines[hit] = enabled_re.sub(
                lambda mm: mm.group(1) + "false" + (mm.group(2) or ""),
                lines[hit])
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
                  active_topics: set, has_file: bool = False,
                  slug: Optional[str] = None, date: str = "",
                  deep_return: str = "view-today") -> str:
    slug = slug or f"story-{i}"
    h = {"full": "h2", "medium": "h3"}.get(tier, "h4")
    parts = [f'<article class="story{" quick-hit" if tier == "quick" else ""}" id="{_e(slug)}">']

    marks = list(slot.get("matched_memory") or [])
    # The override callout stays ABOVE the title (it explains why an off-beat
    # story is here); the tracked-ongoing marker moved DOWN into the merged
    # control under the title (NL-58 ruling 4).
    if slot.get("override"):
        label = slot.get("override_label") or "Editor's override"
        reason = slot.get("world_impact_reason") or ""
        parts.append(
            f'<p class="override-note">{_e(label)}'
            + (f'<span class="reason">{_e(reason)}</span>' if reason else "")
            + "</p>")

    parts.append(f'<{h} class="headline">{_e(st.get("headline", ""))}</{h}>')

    # NL-58 ruling 4: the tracked-ongoing marker and the follow button are ONE
    # control, and it sits with "The full picture" in a single row directly
    # under the title (layout only; DIRECTION tokens binding). Recognition
    # spans both signals so a follow is never shown as "＋ Follow this story"
    # when either its thread is active (marks) or its title is followed.
    affordances = _story_affordances(st, slot, marks, active_topics,
                                     has_file, slug, date, deep_return)
    if affordances:
        parts.append(affordances)

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

    parts.append("</article>")
    return "".join(parts)


def _story_affordances(st: Dict, slot: Dict, marks: List[str],
                       active_topics: set, has_file: bool, slug: str,
                       date: str, deep_return: str) -> str:
    """The single story-affordance row under the title (NL-58 ruling 4): the
    merged tracked-marker/follow control plus "The full picture" entry, grouped
    and aligned so nothing floats or mis-indents.

    Recognition (NL-58 P3a, both directions): a story reads as followed when
    EITHER its thread is active (matched_memory `marks`) OR its story-follow
    title is active — checked against both story_title and headline, so a
    follow created under one edition's phrasing survives title drift into the
    next. Thread-tracked stories show the marker STATE (the thread is managed
    under Following); story-follows are a toggle button (the M7 contract)."""
    bits: List[str] = []
    if marks:
        # Thread-tracked: the marker state of the merged control.
        bits.append(
            f'<span class="tracked-marker">Tracked ongoing story — '
            f'{_e(", ".join(marks))}</span>')
    else:
        topic = slot.get("story_title") or st.get("headline") or ""
        headline = st.get("headline") or ""
        t_in = topic.lower() in active_topics
        h_in = headline.lower() in active_topics
        followed = t_in or h_in
        if followed and not t_in:
            # NL-60 gate F1: unfollow must target the STORED thread phrasing —
            # dismiss_thread is an exact match, so a drift-recognized follow
            # sending the unmatched title would be visible but unfollowable.
            topic = headline
        pressed = "true" if followed else "false"
        label = "Following this story" if followed else "＋ Follow this story"
        cls = " followed" if followed else ""
        date_attr = f' data-briefing-date={_e_attr(date)}' if date else ""
        bits.append(
            f'<button class="follow-story-btn{cls}" data-topic={_e_attr(topic)}'
            f'{date_attr} aria-pressed="{pressed}" onclick="toggleFollow(this)">'
            f'{_e(label)}</button>')
    # M9-M3 entry affordance — three binding states (v4 addendum): present
    # (valid brief), absent (quick tier), degraded-hidden (failed brief —
    # renders IDENTICALLY to absent; total absence is the signal).
    if has_file:
        # 2-arg form for the Today path (the deep view returns there by
        # default); archive-in-place editions pass their own return view.
        ret = "" if deep_return == "view-today" else f", '{_e(deep_return)}'"
        bits.append(
            f'<a class="deep-view-entry-link" href="#" '
            f'onclick="openDeepView(\'{_e(slug)}\', event{ret})">→ The full '
            f'picture</a>')
    if not bits:
        return ""
    return '<div class="story-affordances">' + "".join(bits) + "</div>"


def _e_attr(v: str) -> str:
    return '"' + escape(str(v or ""), quote=True) + '"'


def _render_today(con: sqlite3.Connection, row, entry: Optional[Dict],
                  gen_state: Dict[str, str],
                  briefs: Optional[Dict[int, Dict]] = None) -> str:
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
  <p>No half-written edition ever goes out: a failure before the save
     publishes nothing; one during file export after the save leaves the
     saved edition intact.</p>
  <button class="cta-quiet" onclick="generateAgain()">Try again</button>
</div>"""
    if row is None:
        # NL-11: no edition for TODAY -> the empty state, never an older
        # edition dressed as current. If the archive has earlier editions,
        # point there; the copy still carries "No edition has been generated"
        # so the drift-guard and the no-briefings case read the same.
        has_archive = con.execute(
            "SELECT 1 FROM briefings LIMIT 1").fetchone() is not None
        if has_archive:
            return """
<div class="state-panel">
  <h3>Nothing for today yet</h3>
  <p>No edition has been generated for today. A new one takes a couple of
     minutes: it fetches your sources, picks the stories, writes the briefing,
     and records the episode.</p>
  <button class="cta-quiet" onclick="generateAgain()">Generate today’s edition</button>
  <p class="empty-note" style="margin-top:1rem;">Earlier editions are in your
     <a href="#" onclick="showView('archive'); return false;">Archive</a>.</p>
</div>"""
        return """
<div class="state-panel">
  <h3>Nothing yet</h3>
  <p>No edition has been generated. The first one takes a couple of minutes:
     it fetches your sources, picks the stories, writes the briefing, and
     records the episode.</p>
  <button class="cta-quiet" onclick="generateAgain()">Generate today’s edition</button>
</div>"""

    # NL-11: the glance ("In today’s briefing") section is REMOVED (rework
    # backlogged, NL-20). The lead story now opens the reading surface.
    return _render_briefing_body(con, row, entry, briefs, "", "view-today")


def _render_briefing_body(con: sqlite3.Connection, row, entry: Optional[Dict],
                          briefs: Optional[Dict[int, Dict]],
                          slug_prefix: str, deep_return: str) -> str:
    """Stories + trust footer for one edition. Shared by Today and the
    archive-in-place edition view (NL-11) so both render identically. The
    slug_prefix keeps ids collision-free when an archive edition is injected
    alongside Today; deep_return names the view its deep-view back-link
    returns to."""
    stories, footer_lines = _stories_for(row, entry)
    slots = _slots_for(row)
    tiers = (entry or {}).get("tiers") or []
    active = _active_topics_lower(con)

    html = []
    for i, st in enumerate(stories):
        slot = slots[i] if i < len(slots) else {}
        tier = tiers[i] if i < len(tiers) else ("full" if i == 0 else "medium" if i <= 2 else "quick")
        html.append(_render_story(
            i, st, slot, tier, active, has_file=(i + 1) in (briefs or {}),
            slug=f"{slug_prefix}story-{i}", date=row["date"],
            deep_return=deep_return))

    # Footer disclosure (addendum #3): quiet line; window/caveat/cost a tap
    # away. Ids are slug_prefix-scoped so Today's footer and an open archive
    # edition's footer never collide; the toggle works off the button element.
    gen_local = _fmt_local(row["generated_at"])
    detail_ps = [f"<p>{_e(ln)}</p>" for ln in footer_lines]
    cost = _run_cost(entry)
    dur = _wav_duration(row["audio_file_path"])
    edition_bits = []
    if dur:
        edition_bits.append(f"{dur} audio")
    edition_bits.append(cost)
    detail_ps.append(f'<p>This edition: {_e(" · ".join(edition_bits))}</p>')
    btn_id = f"{slug_prefix}footer-disclosure-btn"
    dtl_id = f"{slug_prefix}footer-disclosure-detail"
    window_line = _coverage_window_line(footer_lines)
    window_html = (f'\n  <p class="coverage-window">{_e(window_line)}</p>'
                   if window_line else "")
    html.append(f"""
<div class="footer-tag">
  <button class="disclosure-trigger" id="{btn_id}" aria-expanded="false"
          aria-controls="{dtl_id}" onclick="toggleFooterDisclosure(this)">
    <span class="caret">▸</span> Generated {_e(gen_local)}
  </button>{window_html}
  <div class="footer-detail" id="{dtl_id}">{"".join(detail_ps)}</div>
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


def _topic_vocabulary(con: sqlite3.Connection, cfg) -> List[str]:
    """Backlog-minors item 2: the autofill vocabulary — the principal's
    current interests (curated baseline) + every tag name coverage has
    matched (accumulated in the persisted slots). Sorted, deduped."""
    vocab = set(cfg.interests_broad) | set(cfg.interests_granular)
    for r in con.execute("SELECT story_slots FROM briefings"):
        try:
            slots = json.loads(r["story_slots"] or "[]")
        except ValueError:
            continue
        for s in slots if isinstance(slots, list) else []:
            for tg in s.get("matched_tags") or []:
                if isinstance(tg, dict) and tg.get("name"):
                    vocab.add(tg["name"])
    return sorted(vocab, key=str.lower)


def _topic_suggestions(con: sqlite3.Connection, cfg) -> List[Dict]:
    """NL-11 suggestions for the Topics add-field: the recall vocabulary MINUS
    what the principal already follows (you can't add what you have). Topics
    carry no secondary line."""
    followed = {t.lower() for t in cfg.interests_broad} \
        | {t.lower() for t in cfg.interests_granular}
    return [{"v": name, "l": name}
            for name in _topic_vocabulary(con, cfg)
            if name.lower() not in followed]


def _writer_suggestions(cfg) -> List[Dict]:
    """NL-11 suggestions for the Writers add-field: writer-shaped feeds the
    system already knows, EXCLUDING ones already followed, each carrying its
    outlet as a secondary line. "Pub (Name)" splits to name=label,
    publication=sub; a plain followed-analyst name has no sub. Name->feed
    RESOLUTION stays P4 (NL-21); this suggests recall, never resolves."""
    followed = {s.name.lower() for s in cfg.followed_analyst_sources}
    out: List[Dict] = []
    seen = set()
    for s in cfg.sources:
        m = re.match(r"^(.*)\s+\((.+)\)\s*$", s.name)
        if not (s.followed_analyst or m):
            continue
        if s.name.lower() in followed:
            continue  # already followed -> excluded (NL-11 ruling)
        if m:
            writer, pub = m.group(2).strip(), m.group(1).strip()
            entry, key = {"v": writer, "l": writer, "s": pub}, writer.lower()
        else:
            entry, key = {"v": s.name, "l": s.name}, s.name.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(entry)
    return sorted(out, key=lambda o: o["l"].lower())


def _render_suggest(kind: str, list_id: str, placeholder: str,
                    aria_label: str, data: List[Dict]) -> str:
    """The shared house-styled suggestion combobox (NL-11) — replaces the
    native datalist, which is browser-dependent (notoriously weak in Safari)
    and structurally could not exclude followed entries, carry a secondary
    line, or be styled. Settings-context editor exception under DIRECTION law:
    outlined, spaced, uncolored, no chips. Keyboard-driven (arrow/enter/escape)
    in the shipped JS; with no JS the list stays hidden and the field degrades
    to a plain text input. The JSON payload is <>&-escaped so a hostile
    recalled name can't break out of the <script> element."""
    payload = (json.dumps(data, ensure_ascii=False)
               .replace("<", "\\u003c").replace(">", "\\u003e")
               .replace("&", "\\u0026"))
    return (
        f'<div class="suggest" data-kind="{_e(kind)}">'
        f'<input class="token-search" type="text" role="combobox"'
        f' aria-expanded="false" aria-autocomplete="list"'
        f' aria-controls="{_e(list_id)}" autocomplete="off"'
        f' placeholder="{_e(placeholder)}" aria-label="{_e(aria_label)}"'
        f' oninput="suggestInput(this)" onkeydown="suggestKeydown(event,this)"'
        f' onfocus="suggestInput(this)" onblur="suggestBlur(this)">'
        f'<ul class="suggest-list" id="{_e(list_id)}" role="listbox" hidden></ul>'
        f'<script type="application/json" class="suggest-data">{payload}</script>'
        f'</div>')


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
            # NL-58: never-picked-up renders as its own honest phrase, not the
            # broken "Last picked up not picked up yet" concatenation.
            if t["last"]:
                meta = (f"Following since {_e(t['since'])} · "
                        f"Last picked up {_e(_human_short(t['last']))}")
            else:
                meta = f"Following since {_e(t['since'])} · Not yet picked up"
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

    # NL-11: the shared house-styled suggestion component (replaces the native
    # datalist). Excludes already-followed topics; keyboard-accessible; no-JS
    # degrades to a plain input.
    topics = [
        _render_suggest("topic", "topic-suggest", "Search or add a topic…",
                        "Search or add a topic", _topic_suggestions(con, cfg)),
        '<p class="token-search-hint">Type a topic and press Enter to add, or '
        'pick a suggestion — suggestions draw from everything coverage has '
        'matched that you don’t already follow.</p>',
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
        _render_suggest("writer", "writer-suggest", "Search or add a writer…",
                        "Search or add a writer", _writer_suggestions(cfg)),
        '<p class="token-search-hint">Following a writer adds their feed to '
        'your sources and boosts their pieces in ranking. Suggestions recall '
        'writers the system already knows (with their outlet); adding someone '
        'new still takes their feed link.</p>',
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
        # NL-11: JS opens the edition IN-PLACE (Today never replaced); the
        # href is the no-JS graceful fallback (full navigation to ?date=).
        html.append(f"""
<div class="archive-row">
  <a href="/?date={_e(r["date"])}" onclick="return openEdition('{_e(r["date"])}', event)">
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


def _cite_qualifier(cites: List[str], src_by_key: Dict[str, Dict],
                    provenance: str = "") -> str:
    """The v4-addendum trailing qualifier: '(Outlet · N outlets)',
    '(Outlet · via Sonar)', '(background)'. Typography-carried provenance —
    never a badge, never an icon (Axel's rationale)."""
    outlets = []
    kinds = set()
    for c in cites:
        s = src_by_key.get(c)
        if not s:
            continue
        kinds.add(s.get("kind", ""))
        o = s.get("outlet", "")
        if o and o not in outlets:
            outlets.append(o)
    if not outlets:
        return "(background)"
    if not provenance:
        # BUG16 (M3 gate): ONE provenance path, not two — a caller that has
        # no provenance string gets it derived from the resolved keys, so
        # multi-outlet cites can never read "· 1 outlet".
        provenance = compute_prov_display(cites, src_by_key)
    names = ", ".join(outlets[:2])
    if provenance.startswith("cluster-corroborated"):
        n = provenance.split("(")[-1].split()[0]
        return f"({names} · {n} outlets)"
    if provenance == "cluster-single" or (
            not provenance and kinds & {"cluster-full-text", "cluster-excerpt"}):
        return f"({names} · 1 outlet)"
    if provenance.startswith("retrieved-single") or kinds == {"retrieved"}:
        return f"({names} · via Sonar)"
    if kinds == {"prior-briefing"}:
        return f"({names})"
    return f"({names})"


def _glue_sentence(s: str) -> str:
    """Dumb glue (register spec D5): fixed connective punctuation only — a
    trailing period so joined field-strings read as separate sentences. Never
    rewrites, re-cases, truncates, or reorders the field's own words."""
    s = (s or "").strip()
    if s and s[-1] not in ".?!:":
        s += "."
    return s


def _open_unknown_prose(u: Dict) -> str:
    """One unknown as one editor's-memo paragraph (register spec §B/D1): the
    three fields join as three sentence-roles — what is unsettled (question),
    why it bites (why_material), the test (would_resolve, after the fixed
    phrase). No labels, no beats, no meta-tails. Declarative or survey-register
    is the analyst's job; the renderer only joins what it is handed."""
    parts = []
    q = _glue_sentence(u.get("question", ""))
    if q:
        parts.append(q)
    why = _glue_sentence(u.get("why_material", ""))
    if why:
        parts.append(why)
    res = (u.get("would_resolve", "") or "").strip()
    if res:
        parts.append(_glue_sentence("What would settle it — " + res))
    return " ".join(parts)


def _open_watch_prose(watch: List[Dict]) -> str:
    """All watch observables as one closing forward-calendar paragraph
    (register spec D2/D3): observables in contract order, `settles` never
    rendered (it is a join key, not reading material). No lead-in label and no
    unknowns-flavored opener (D4)."""
    sents = [_glue_sentence(w.get("observable", "")) for w in watch
             if (w.get("observable", "") or "").strip()]
    return " ".join(sents)


def _render_deep_view(story_anchor: str, headline: str, doc: Dict,
                      date: str, back_label: str = "← Back to today’s edition",
                      return_view: str = "view-today") -> str:
    """The reader rendering — v6-as-edited is the spec. One artifact, two
    renderings (§5.3): this template never re-composes, never re-ledes;
    'cited' never 'verified'; notes_for_writer never renders. NL-11: back-link
    returns to `return_view` (Today by default; an archive-in-place edition
    passes its own view) and `story_anchor` may be slug-prefixed so archive
    editions never collide with Today's deep-view ids."""
    brief = doc.get("brief") or {}
    header = doc.get("header") or {}
    src_by_key = {s["key"]: s for s in brief.get("sources", [])}

    out = [f'<section id="view-deep-{story_anchor}" class="view">']
    ret = "" if return_view == "view-today" else f", '{_e(return_view)}'"
    out.append(f'<a class="deep-back" href="#" onclick="closeDeepView(event{ret})">'
               f'{_e(back_label)}</a>')
    # Arc is no longer a section (NL-12): it renders as a cited continuity line
    # in the title block, carrying the last edition that picked up this thread
    # and navigating there in-place (reuses NL-11's openEdition; the href is the
    # no-JS fallback). Zero content loss; the continuity job moves to the top,
    # where orientation lives.
    arc = brief.get("arc")
    arc_line = ""
    if arc:
        verdict = {"advances": "Advances the thread",
                   "reverses": "Reverses the thread",
                   "merely-matches": "Merely matches the thread"}.get(
                       arc.get("delta", ""), _e(str(arc.get("delta", ""))))
        prior_date = None
        for c in _cites_list(arc):
            s = src_by_key.get(c)
            if s and s.get("kind") == "prior-briefing":
                rd = str(s.get("retrieved_at", ""))[:10]
                # NL-60: only a real calendar date becomes a navigable edition
                # (the arc line had no guard at all); a bad date renders the
                # continuity line with no link rather than a dead one.
                if _is_calendar_date(rd):
                    prior_date = rd
                break
        cite_html = ""
        if prior_date:
            cite_html = (
                f' <a class="deep-arc-link" href={_e_attr("/?date=" + prior_date)} '
                f'onclick="return openEdition(\'{_e(prior_date)}\', event)">'
                f'· from the {_e(_human_date(prior_date))} edition</a>')
        arc_line = (f'<p class="deep-arc-line">'
                    f'<span class="deep-arc-verdict">{_e(verdict)}</span> — '
                    f'{_e(arc.get("what_changed", ""))}{cite_html}</p>')
    out.append(f'<div class="deep-title-block"><p class="deep-eyebrow">The full '
               f'picture</p><h1 class="deep-title">{_e(headline)}</h1>'
               f'{arc_line}</div>')

    # "What's still open" paragraphs are computed HERE — before the jumplist —
    # so the anchor and the section gate on the SAME rendered content (D4,
    # "absent halves leave no residue"): empty/whitespace watch observables and
    # empty unknowns collapse to zero paragraphs, so neither a live jumplist
    # anchor nor a header-only section is emitted. Truthiness on the raw lists
    # was the wrong signal — a list of all-empty observables is truthy.
    open_paras = [f'<p>{_e(prose)}</p>'
                  for prose in (_open_unknown_prose(u)
                                for u in brief.get("unknowns", []))
                  if prose]
    open_watch_para = _open_watch_prose(brief.get("watch", []))
    if open_watch_para:
        open_paras.append(f'<p>{_e(open_watch_para)}</p>')

    # NL-12: five reader sections. Facts and Sources always render; Mechanism is
    # validator-required; "What could follow" and "What's still open" emit only
    # with content, and no dead jumplist anchor otherwise (M7 precedent).
    jump_items = [("facts", "Facts"), ("mechanism", "Mechanism")]
    if brief.get("effects"):
        jump_items.append(("effects", "What could follow"))
    if open_paras:
        jump_items.append(("open", "Still open"))
    jump_items.append(("sources", "Sources"))
    out.append('<p class="deep-jumplist">'
               + '<span class="sep">·</span>'.join(
                   f'<a href="#{story_anchor}-{sid}">{label}</a>'
                   for sid, label in jump_items)
               + "</p>")

    # 1. The facts — pinned facts ONLY (principal ruling 2026-07-09: the Ledger
    # and the Unresolved/discrepancy register are removed from the READER view
    # entirely; the data stays in brief_json and the writer view). Per-fact
    # citations fold behind a quiet typographic marker (NL-12): the outlet
    # names + count reveal on tap; `<details open>` means no-JS shows them
    # expanded (degrade = more information) and the summary is keyboard-native.
    lis = []
    for f in brief.get("pinned_facts", []):
        cites = f.get("cites", [])
        q = _cite_qualifier(cites, src_by_key,
                            compute_prov_display(cites, src_by_key))
        lis.append(
            f'<li>{_e(f.get("fact", ""))} '
            + _cite_fold(q, "Show sources for this fact") + '</li>')
    out.append(f'<div class="deep-section" id="{story_anchor}-facts">'
               '<p class="deep-section-label">The facts</p>'
               f'<ul class="deep-facts-list">{"".join(lis)}</ul></div>')

    # 2. mechanism — inline [S#] keys become the SAME quiet citation fold the
    # facts use (NL-58 parity ruling): the qualifier reveals on tap behind a
    # caret, not inline as plain text.
    mech = brief.get("mechanism", "")
    mech_display = re.sub(
        r"\s*\[([SCRP]\d+(?:,\s*[SCRP]\d+)*)\]",
        lambda m: " " + _cite_fold(_cite_qualifier(
            [k.strip() for k in m.group(1).split(",")], src_by_key),
            "Show sources for this claim"),
        _e(mech))
    out.append(f'<div class="deep-section" id="{story_anchor}-mechanism">'
               '<p class="deep-section-label">Mechanism</p>'
               f'<p>{mech_display}</p></div>')

    # 3. effects — the citation IS the basis marker (Thread D)
    effs = []
    for e in brief.get("effects", []):
        holder = e.get("holder", "")
        # v6 grammar (M3 gate): bare "(via Outlet)" — the deviation batch
        # killed both "(via X · 1 outlet)" and the double-via Sonar shape.
        via_outlets = []
        for c in e.get("cites", []):
            s = src_by_key.get(c)
            if s and s.get("outlet") and s["outlet"] not in via_outlets:
                via_outlets.append(s["outlet"])
        via = f"(via {', '.join(via_outlets[:2])})" if via_outlets else "(background)"
        lead_in = f"{_e(holder)}: " if holder else ""
        effs.append(f'<p class="deep-effect">{lead_in}{_e(e.get("effect", ""))} '
                    f'<span class="cite">{_e(via)}</span></p>')
    if effs:
        out.append(f'<div class="deep-section" id="{story_anchor}-effects">'
                   '<p class="deep-section-label">What could follow</p>'
                   + "".join(effs) + "</div>")

    # 4. What's still open — Honest Unknowns + Watch For fused at section level
    # (register spec, 2026-07-09 addendum). One register end to end: editor's-
    # memo prose, body ink, body size. Unknowns lead as one paragraph each
    # (three sentence-roles, no beats/labels/tails); one closing paragraph
    # carries the watch observables; `settles` never renders. Absent halves
    # leave no residue (D4) — paragraphs precomputed above; emit only if any
    # rendered content survives (the anchor gates on the same list).
    if open_paras:
        out.append(f'<div class="deep-section" id="{story_anchor}-open">'
                   '<p class="deep-section-label">What’s still open</p>'
                   + "".join(open_paras) + "</div>")

    # 5. source table — rows, real accessible names (Axel)
    rows = []
    for s in brief.get("sources", []):
        kind = s.get("kind", "")
        when = _fmt_local(s.get("retrieved_at")) if "T" in str(s.get("retrieved_at", "")) \
            else _e(str(s.get("retrieved_at", "")))
        kind_label = {"cluster-full-text": "cluster, full text",
                      "cluster-excerpt": "cluster excerpt",
                      "retrieved": "retrieved, via Sonar",
                      "prior-briefing": "prior NewsLens edition"}.get(
                          kind, kind)
        # NL-58: a prior-edition source says WHICH edition and links to it
        # (openEdition — the same in-place open as the arc line; the href is
        # the no-JS fallback). Real prior-briefing rows carry an empty url and
        # a machine title ("briefing 2026-07-06"); both are replaced here.
        ed_date = str(s.get("retrieved_at", ""))[:10]
        # NL-60: guard by real calendar date, not ISO shape alone — a shaped-but-
        # impossible '2026-13-45' must fall through to the plain unlinked title,
        # never a live dead-end edition link.
        if kind == "prior-briefing" and _is_calendar_date(ed_date):
            title = f"NewsLens — {_e(_human_date(ed_date))} edition"
            link = (f'<a href={_e_attr("/?date=" + ed_date)} '
                    f'onclick="return openEdition(\'{_e(ed_date)}\', event)">'
                    f'{title}</a>')
        else:
            title = _e(s.get("title", "") or "(untitled)")
            link = (f'<a href={_e_attr(s["url"])}>{title}</a>' if s.get("url")
                    else title)
        rows.append('<div class="deep-source-row">'
                    f'<p class="source-outlet">{_e(s.get("outlet", ""))}</p>'
                    f'<p class="source-title">{link}</p>'
                    f'<p class="source-meta">Retrieved {when} · {_e(kind_label)}</p></div>')
    out.append(f'<div class="deep-section" id="{story_anchor}-sources">'
               '<p class="deep-section-label">Sources</p>'
               + "".join(rows) + "</div>")

    # deterministic footer — cited, never verified (Sten's law, binding copy)
    n_src = len(brief.get("sources", []))
    degraded = header.get("degraded")
    deg_line = (f"<p>Limited source access for this story — analysis is "
                f"based on {n_src} source(s): {_e(degraded)}</p>") if degraded else ""
    out.append(f'<div class="deep-footer"><p>Based on {n_src} cited '
               f'source(s) for the {_e(_human_date(date))} edition.</p>'
               f'{deg_line}'
               '<p>Citations in this brief are cited, not verified: they '
               'resolve to real retrieved text, but NewsLens cannot confirm '
               'every source characterizes its own claim fairly. Treat this '
               'as receipts, not proof.</p></div>')
    out.append("</section>")
    return "".join(out)


def _cite_fold(qualifier: str, aria: str) -> str:
    """The quiet citation fold (NL-12), shared by the facts list AND the
    mechanism prose so both citation surfaces read identically (NL-58 parity
    ruling, DECISIONS 2026-07-10: mechanism citations previously rendered
    inline and unfolded, unlike facts'). Ships as <details open> so a no-JS
    reader sees the qualifier expanded (degrade = more information); the shared
    collapseCiteFolds() JS closes it to the caret marker. Empty qualifier folds
    to nothing rather than an empty caret."""
    q = (qualifier or "").strip()
    if not q:
        return ""
    return ('<span class="fact-cite"><details class="cite-fold" open>'
            f'<summary aria-label="{_e(aria)}">'
            '<span class="caret" aria-hidden="true">▸</span></summary>'
            f'<span class="cite-fold-body">{_e(q)}</span></details></span>')


def _cites_list(d: Dict) -> List[str]:
    out = []
    for c in d.get("cites") or []:
        if isinstance(c, str):
            out.append(c.strip().strip("[]"))
    return out


def compute_prov_display(cites: List[str], src_by_key: Dict[str, Dict]) -> str:
    kinds = {src_by_key[c]["kind"] for c in cites if c in src_by_key}
    outlets = {src_by_key[c]["outlet"] for c in cites
               if c in src_by_key and src_by_key[c]["kind"].startswith("cluster")}
    if len(outlets) >= 2:
        return f"cluster-corroborated ({len(outlets)} outlets)"
    if len(outlets) == 1:
        return "cluster-single"
    if "retrieved" in kinds:
        return "retrieved-single (x)"
    return ""


def _collect_deep_views(con: sqlite3.Connection, row, entry: Optional[Dict],
                        slug_prefix: str, back_label: str,
                        return_view: str) -> Tuple[Dict[int, Dict], List[str]]:
    """Newest-valid-wins brief reads for one edition (M9-M3); renders FROM the
    persisted row, never regenerates. Returns ({slot_no: doc}, [sections]).
    Shared by Today and the archive-in-place edition (NL-11)."""
    briefs: Dict[int, Dict] = {}
    sections: List[str] = []
    from . import analysis as analysis_mod
    stories_probe, _ = _stories_for(row, entry)
    for i, st in enumerate(stories_probe):
        doc = analysis_mod.latest_valid_brief(con, row["date"], i + 1)
        if doc and doc.get("brief"):
            briefs[i + 1] = doc
            sections.append(_render_deep_view(
                f"{slug_prefix}story-{i}", st.get("headline", ""), doc,
                row["date"], back_label=back_label, return_view=return_view))
    return briefs, sections


def build_edition_fragment(con: sqlite3.Connection,
                           date: str) -> Tuple[str, Optional[str]]:
    """NL-11: an archive edition rendered as an in-place view fragment — the
    edition body + its deep views + a top-left "Back to Archive" affordance
    (the deep-view back pattern). The client injects this alongside Today, so
    Today is NEVER replaced. Ids are slug-prefixed per date; deep-view sections
    are siblings of the edition section (not nested — a nested .view can't show
    when its ancestor is display:none). Returns (html, date_read_or_None); the
    caller logs the read server-side exactly as a page-view does."""
    row = _briefing_row(con, date)
    if row is None:
        return ('<section class="view active" id="view-edition">'
                '<a class="deep-back" href="#" onclick="backToArchive(event)">'
                '← Back to Archive</a>'
                '<p class="empty-note">That edition is unavailable.</p>'
                '</section>', None)
    entry = _log_entry_for(row["date"])
    slug_prefix = f"ed{date}-"
    briefs, deep_sections = _collect_deep_views(
        con, row, entry, slug_prefix, "← Back to this edition", "view-edition")
    episode = ""
    dur = _wav_duration(row["audio_file_path"])
    if dur:
        episode = (f'<div class="episode-affordance edition-episode">'
                   f'<button onclick="toggleEpisodeEl(\'ep-{_e(date)}\')" '
                   f'aria-label="Play full episode, {_e(dur)}">▷ Play full episode'
                   f'<span class="episode-meta"> · {_e(dur)}</span></button>'
                   f'<audio id="ep-{_e(date)}" style="display:none" controls '
                   f'preload="none" src="/audio/{_e(date)}.wav"></audio>'
                   f'{_player_extra_controls(f"ep-{date}")}</div>')
    body = _render_briefing_body(con, row, entry, briefs, slug_prefix,
                                 "view-edition")
    head = (f'<section class="view active" id="view-edition">'
            f'<a class="deep-back" href="#" onclick="backToArchive(event)">'
            f'← Back to Archive</a>'
            f'<h1 class="view-title">{_e(_human_date(row["date"]))}</h1>'
            f'{episode}{body}</section>')
    return head + "".join(deep_sections), row["date"]


def build_page(con: sqlite3.Connection, date: Optional[str] = None) -> Tuple[str, Optional[str]]:
    """Returns (html, briefing_date_rendered)."""
    gen_state = GEN_JOB.snapshot()
    if date:
        row = _briefing_row(con, date)
    else:
        # NL-11: Today shows TODAY's edition or the empty state — never an
        # older edition dressed as current. Deep-linked/archive ?date= still
        # addresses any date (the no-JS archive path).
        row = _briefing_row(con, datetime.now().strftime("%Y-%m-%d"))
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
  {_player_extra_controls("episode-player")}
</div>"""

    # M9-M3: newest-valid-wins brief reads; the view renders FROM the
    # persisted row (never regenerates); date-addressed like briefings.
    briefs: Dict[int, Dict] = {}
    deep_sections: List[str] = []
    if row is not None and gen_state["state"] != "running":
        briefs, deep_sections = _collect_deep_views(
            con, row, entry, "", "← Back to today’s edition", "view-today")

    page = webui.PAGE.format(
        css=webui.CSS,
        date_label=_e(date_label),
        episode_html=episode_html,
        today_html=_render_today(con, row, entry, gen_state, briefs=briefs),
        following_html=_render_following(con),
        archive_html=_render_archive(con),
        settings_html=_render_settings(con, row, entry),
        popups_html=webui.POPUPS,
        deep_views_html="".join(deep_sections),
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

    # Ride 22 (M8): DNS-rebinding belt over the content-type CSRF gate. A
    # hostile page can point its own domain at 127.0.0.1 and bypass
    # same-origin — but the browser still sends the attacker's hostname in
    # Host. Only localhost names may address this server. Port is ignored
    # (it varies with --port); an absent Host header is allowed because
    # HTTP/1.0 tools (and our own curl checks) omit it and the socket is
    # already bound to loopback.
    _ALLOWED_HOSTS = {"127.0.0.1", "localhost", "[::1]", "::1"}

    def _host_allowed(self) -> bool:
        host = (self.headers.get("Host") or "").strip().lower()
        if not host:
            return True
        if host.startswith("["):
            bare = host.split("]")[0] + "]"
        else:
            bare = host.rsplit(":", 1)[0] if ":" in host else host
        return bare in self._ALLOWED_HOSTS

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
        if not self._host_allowed():
            return self._send_html("<h1>Forbidden</h1>", 403)
        parsed = urlparse(self.path)
        try:
            if parsed.path == "/":
                return self._page(parse_qs(parsed.query))
            if parsed.path == "/edition":
                return self._edition(parse_qs(parsed.query))
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

    def _edition(self, qs: Dict[str, List[str]]) -> None:
        """NL-11: the archive-in-place edition fragment. Same server-side
        read-logging as a page-view — the read fires because the server
        actually served the edition body, not a client beacon."""
        date = (qs.get("date") or [None])[0]
        if not date or not re.match(r"^\d{4}-\d{2}-\d{2}$", date):
            return self._send_html("<p class='empty-note'>bad date</p>", 400)
        con = db.connect()
        try:
            html, rendered = build_edition_fragment(con, date)
            if rendered:
                events.log_read(con, rendered)
            self._send_html(html)
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
        if not self._host_allowed():
            return self._send_json({"ok": False, "error": "forbidden host"}, 403)
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
