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

import calendar
import json
import os
import re
import sqlite3
import subprocess
import threading
import wave
from datetime import datetime, timedelta, timezone
from html import escape
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from urllib.parse import parse_qs, urlparse

from . import analysis, config, db, events, labels, memory, paths, webui

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
# Code-identity staleness guard (NL-60 class, 2nd occurrence -> a mechanism)
#
# The incident (2026-07-16): a UI-triggered generate ran inside a server whose
# in-memory modules predated two COMMITTED milestones — a defective edition
# went out with zero disclosure. Reading a stale-rendered page is tolerable;
# WRITING an edition with stale code is the incident. So this stamps the
# process with its code identity at serve() boot and, per request, compares it
# to disk: on divergence the UI shows a banner and — the teeth — the generate
# trigger REFUSES (see _api_generate). Honest states: an unresolvable identity
# disables the guard entirely (no banner, no refusal, one startup log line) —
# the guard never blocks on a broken check.
# ---------------------------------------------------------------------------

# (kind, value): "git" -> a HEAD sha, "mtime" -> newest package *.py mtime_ns.
# None until serve() stamps it, so in-process test harnesses that instantiate
# Handler directly (without serve()) are un-guarded unless they set it.
_STARTUP_IDENTITY: Optional[Tuple[str, str]] = None


def _git_head() -> Optional[str]:
    """The committed code identity: `git rev-parse HEAD`, read-only, run in the
    project repo. Returns None when git is unavailable, this is not a checkout,
    or the call errors — the caller falls back to source mtime. Never writes and
    never touches the working tree (the no-real-state-writes rule covers git)."""
    try:
        out = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=str(paths.PROJECT_ROOT),
            capture_output=True, text=True, timeout=5, check=False)
    except (OSError, subprocess.SubprocessError):
        return None
    head = out.stdout.strip()
    return head if out.returncode == 0 and head else None


def _src_mtime() -> Optional[str]:
    """The git-unavailable fallback identity: the newest mtime across the
    package's own *.py — the very files whose in-memory copies go stale. Catches
    the incident shape (a milestone's edits bump mtimes) with no subprocess."""
    try:
        pkg = Path(__file__).resolve().parent
        newest = max((p.stat().st_mtime_ns for p in pkg.glob("*.py")),
                     default=0)
    except OSError:
        return None
    return str(newest) if newest else None


def _code_identity() -> Optional[Tuple[str, str]]:
    """(kind, value) for the code on disk right now. git HEAD is primary — it is
    exactly 'which committed milestones exist', the incident's own axis; source
    mtime is the git-unavailable fallback. None only when NEITHER resolves."""
    head = _git_head()
    if head:
        return ("git", head)
    mtime = _src_mtime()
    if mtime:
        return ("mtime", mtime)
    return None


def _identity_of_kind(kind: str) -> Optional[str]:
    """Recompute the CURRENT identity via the same mechanism the process was
    stamped with, so startup and now are always compared like-for-like."""
    if kind == "git":
        return _git_head()
    if kind == "mtime":
        return _src_mtime()
    return None


def _stamp_startup_identity() -> None:
    """Called once at serve() boot: freeze the identity of the code this process
    actually loaded. On an unresolvable identity the guard disables itself and
    says so once — it must never block generation on a check it cannot make."""
    global _STARTUP_IDENTITY
    _STARTUP_IDENTITY = _code_identity()
    if _STARTUP_IDENTITY is None:
        print("newslens: code-identity unresolvable at startup (no git, no "
              "readable package mtime) — staleness guard disabled", flush=True)


def _server_is_stale() -> bool:
    """True only when the startup identity resolved, the CURRENT identity
    resolves via the SAME mechanism, and they diverge. Any unresolved side ->
    False: the guard never blocks on a broken check (honest-states rule)."""
    startup = _STARTUP_IDENTITY
    if startup is None:
        return False
    kind, startup_val = startup
    current = _identity_of_kind(kind)
    if current is None:
        return False
    return current != startup_val


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


# ---------------------------------------------------------------------------
# v7 shell (DIRECTION-v5 §4) — the masthead ceremony, section line, mini-head.
# Each view renders its OWN masthead/mini-head + section line; there is no
# shared top-bar or bottom-nav chrome (both killed by §4).
# ---------------------------------------------------------------------------

_SETTINGS_GEAR = (
    '<button class="settings-corner" aria-label="Settings" onclick="openSettings()">'
    '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke-width="1.7">'
    '<line x1="4" y1="7" x2="20" y2="7"/><circle cx="14" cy="7" r="2" fill="var(--paper)"/>'
    '<line x1="4" y1="12" x2="20" y2="12"/><circle cx="9" cy="12" r="2" fill="var(--paper)"/>'
    '<line x1="4" y1="17" x2="20" y2="17"/><circle cx="16" cy="17" r="2" fill="var(--paper)"/>'
    '</svg></button>')


def _utc_hm(iso_utc: Optional[str]) -> str:
    """HH:MM in UTC from a stored ISO timestamp (storage is UTC). Empty on a bad
    value — the dispatch strip omits the clause rather than show garbage."""
    if not iso_utc:
        return ""
    try:
        dt = datetime.fromisoformat(str(iso_utc).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).strftime("%H:%M")
    except ValueError:
        return ""


def _dateline_html(date_str: str) -> str:
    """The masthead dateline: 'Friday, July [10] [2026]' — day numeral in terra,
    year quiet (§2 LOUD register). Plain text on a non-calendar date."""
    if _is_calendar_date(date_str):
        d = datetime.strptime(date_str, "%Y-%m-%d")
        return (f'<h1 class="dateline">{_e(d.strftime("%A, %B"))} '
                f'<span class="dl-num">{d.day}</span> '
                f'<span class="dl-year">{d.year}</span></h1>')
    return f'<h1 class="dateline">{_e(date_str)}</h1>'


def _wordmark_row() -> str:
    """The masthead's top line: the wordmark + the quiet settings entry. The v7
    mockup shows no settings control (design-incomplete); the gear rides the
    wordmark row — never the section line, which §4 reserves for the three
    destinations — so settings stays reachable on every view (implementer call,
    disclosed)."""
    return f'<div class="mast-top"><p class="wordmark">NewsLens</p>{_SETTINGS_GEAR}</div>'


def _section_line(current: str) -> str:
    """The sticky, one-line nav (§4): Today · Following · Archive, nothing else.
    Each view renders its own with the right aria-current; showView toggles the
    active view (no bottom-nav). The href is the no-JS fallback."""
    def link(view: str, label: str) -> str:
        cur = ' aria-current="page"' if view == current else ''
        return (f'<a href="#view-{view}" onclick="showView(\'{view}\'); '
                f'return false;"{cur}>{_e(label)}</a>')
    return (f'<nav class="section-line" aria-label="Sections"><div class="page">'
            f'{link("today", labels.NAV_TODAY)}'
            f'{link("following", labels.NAV_FOLLOWING)}'
            f'{link("archive", labels.NAV_ARCHIVE)}</div></nav>')


def _mono_date(date_str: str) -> str:
    """FRI · JUL 10 · 2026 for the mini-masthead (machine register). Stdlib
    strftime only — no %-d (not portable)."""
    if _is_calendar_date(date_str):
        d = datetime.strptime(date_str, "%Y-%m-%d")
        return f"{d.strftime('%a').upper()} · {d.strftime('%b').upper()} {d.day} · {d.year}"
    return date_str


def _mini_head(date_str: str) -> str:
    """Following/Archive open with the compressed ceremony (wordmark + mono date)
    then the section line (§4)."""
    return (f'<div class="page mini-head">{_wordmark_row()}'
            f'<span class="mh-date">{_e(_mono_date(date_str))}</span></div>')


def _edition_bar(row) -> str:
    """The podcast player as edition-level furniture (§6): present only when a
    real episode exists (duration reads from the wav); absence is the signal
    (pre-generation / no audio). The player JS is unchanged; only the frame is
    v7. (The 'skipped this run' state needs a run-log field the empty log can't
    supply today — degrades to absent, honestly.)"""
    dur = _wav_duration(row["audio_file_path"])
    if not dur:
        return ""
    return (f'<div class="episode-affordance">'
            f'<button onclick="toggleEpisode()" '
            f'aria-label="Play full episode, {_e(dur)}">▶ {_e(labels.LISTEN_TO_EDITION)}'
            f'<span class="episode-meta"> · {_e(dur)}</span></button>'
            f'<audio id="episode-player" style="display:none" controls '
            f'preload="none" src="/audio/{_e(row["date"])}.wav"></audio>'
            f'{_player_extra_controls("episode-player")}</div>')


def _masthead(row, date_str: str) -> str:
    """The full Today ceremony, fixed order (§4): wordmark → dateline →
    [signature] → dispatch strip → edition bar. The SIGNATURE (the kind-of-
    morning line) and the strip's source-count / threads-advanced clauses are
    NL-63's to compute and are NOT in the data yet; per A8 no-fabrication we OMIT
    them (degrade to dateline + assembled time), never invent them. `row` is
    None on empty/running/error states — then only the ceremony frame renders."""
    parts = ['<header class="page masthead">', _wordmark_row(),
             _dateline_html(date_str)]
    if row is not None:
        hm = _utc_hm(row["generated_at"])
        if hm:
            parts.append(f'<p class="dispatch-strip">Edition assembled {_e(hm)} UTC</p>')
        parts.append(_edition_bar(row))
    parts.append('</header>')
    return "".join(parts)


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


def _latest_edition_date(con: sqlite3.Connection) -> str:
    """The most recent edition date — 'this edition' for the Following spine's
    updated/quiet split and the archive's today-class. '' when no editions."""
    row = con.execute("SELECT MAX(date) AS d FROM briefings").fetchone()
    return (row["d"] or "") if row else ""


def _following_rows(con: sqlite3.Connection) -> Dict[str, List[Dict]]:
    rows = con.execute(
        "SELECT m.*, b.date AS ref_date FROM memory m LEFT JOIN briefings b"
        " ON b.id = m.last_referenced_briefing_id ORDER BY m.id"
    ).fetchall()
    # Obs 7: the developing-window cutoff must share the LOCAL clock that
    # briefing dates are stamped in (ranking.local_today, i.e. datetime.now()
    # below) — a UTC cutoff here skewed the "developing" dot by a day across the
    # UTC/local boundary. `today` was already local; now both agree.
    cutoff = (datetime.now()
              - timedelta(days=DEVELOPING_WINDOW_DAYS)).strftime("%Y-%m-%d")
    today = datetime.now().strftime("%Y-%m-%d")
    # §7/§12.2: "updated THIS EDITION" is a delta dated the latest edition; the
    # split drives loud-updated rows vs the counted quiet fold (§12.5). Real DB
    # rows (thread_deltas), NOT a log-derived field — the empty run-log can't
    # supply this, and it doesn't need to (the ledger does).
    latest_ed = _latest_edition_date(con)
    grouped: Dict[str, List[Dict]] = {"active": [], "dormant": [],
                                      "dismissed_user": []}
    from . import memory_core
    for r in rows:
        # NL-58 future-date guard: "last picked up" is the DATE of the joined
        # last-referenced briefing; a value later than today is data corruption
        # (a briefing dated in the future) and must never render as a real
        # pickup — it degrades to "not yet picked up", the honest state. Guards
        # the reported "Last picked up Jul 13" (a future date) at the source.
        ref_date = r["ref_date"] or ""
        last = ref_date if (ref_date and ref_date <= today) else ""
        state = memory_core.latest_state(con, r["id"])
        ledger = memory_core.ledger_for_thread(con, r["id"])
        last_delta = ledger[-1] if ledger else None
        # this-edition delta: the (latest) ledger entry dated the current edition
        this_delta = None
        if latest_ed:
            for e in reversed(ledger):
                if e["edition_date"] == latest_ed:
                    this_delta = e
                    break
        # the quiet-row "LAST UPDATED" stamp: newest ledger date, else the joined
        # last-referenced briefing date; future dates degrade to '' (no stamp).
        lu = (ledger[-1]["edition_date"] if ledger else last)
        last_updated = lu if (lu and lu <= today) else ""
        grouped.setdefault(r["status"], []).append({
            "id": r["id"],
            "topic": r["topic"],
            "note": r["principal_note"] or "",
            "since": _short_date(r["created_at"]),
            "last": last,
            "quiet_since": _short_date(r["status_changed_at"]),
            "developing": bool(last and last >= cutoff),
            "state_text": (state or {}).get("state_text", ""),
            "state_as_of": (state or {}).get("as_of_date", ""),
            "updated": this_delta is not None,
            "this_delta": ({"date": this_delta["edition_date"],
                            "what_happened": this_delta["what_happened"],
                            "significance": this_delta.get("significance", "")}
                           if this_delta else None),
            "last_updated": last_updated,
            "last_delta": ({"date": last_delta["edition_date"],
                            "what_happened": last_delta["what_happened"],
                            "significance": last_delta.get("significance", "")}
                           if last_delta else None),
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


def _today_arc_html(con, slot: Dict, st: Dict, date: str,
                    seen: Optional[set] = None) -> str:
    """NL-63 item 4: the then -> now -> difference continuity line under the
    lead, gated by Sten's kill-test AS CODE (renders ONLY when it carries a
    dated past fact absent from today's story) and Kass's reversion law AS CODE
    (a ledger-integrity failure shows a bare citation line, disclosed). Day-one
    threads get NO arc, ever. Deterministic — no LLM, computed from the ledger.
    The kill-test runs against today's RENDERED story text (headline + lede +
    movements).

    BUG-35: `seen` is the caller's per-EDITION dedup set. On a sanctioned-split
    day (two same-thread slots in one edition) the ledger composes the IDENTICAL
    arc text for both — the prominent (earliest-rendered) slot wins and the
    sibling suppresses its duplicate. The per-story kill-test is unchanged (it
    runs above, against each story's own text); this dedups only the rendered
    line. Passed None (standalone) it falls back to a per-slot set."""
    if con is None or not _is_calendar_date(date):
        return ""
    from . import memory_core
    today_text = " ".join(
        [st.get("headline", ""), st.get("lede", "")]
        + [m.get("text", "") for m in (st.get("movements") or [])
           if isinstance(m, dict)])
    # NL-60 never-a-dead-link law (M1 gate F): the arc line's prior-edition link
    # must point at an edition that EXISTS. A seeded ledger (the A′ Hormuz seed)
    # or a corrupt date can cite an edition with no briefing row; rendering a
    # link to it is a dead link. Guard on real briefing rows, exactly as the
    # deep-view timeline does.
    have_edition = {r["date"] for r in con.execute("SELECT date FROM briefings")}
    if seen is None:                           # standalone: per-slot fallback
        seen = set()
    out: List[str] = []
    for topic in slot.get("matched_memory") or []:
        tid = memory_core.resolve_thread_id(con, topic)
        if tid is None:
            continue
        arc = memory_core.render_today_arc(con, tid, topic, today_text, date)
        if arc is None or arc.text in seen:
            continue
        seen.add(arc.text)
        link = ""
        if arc.prior_date and _is_calendar_date(arc.prior_date) \
                and arc.prior_date in have_edition:
            link = (
                f' <a class="today-arc-link" '
                f'href={_e_attr("/?date=" + arc.prior_date)} '
                f'onclick="return openEdition(\'{_e(arc.prior_date)}\', event)">'
                f'· from the {_e(_human_date(arc.prior_date))} edition</a>')
        disc = (f'<span class="today-arc-disclosure"> — {_e(arc.disclosure)}</span>'
                if arc.disclosure else "")
        cls = "today-arc-line" + (" reverted" if arc.kind == "reverted" else "")
        out.append(f'<p class="{cls}">{_e(arc.text)}{disc}{link}</p>')
    return "".join(out)


def _story_movement_paras(st: Dict, date: str) -> List[str]:
    """The story's Today body beats — the 'Why it matters' / 'Watch for'
    movements as .move-label headers. Shared by the Today story body AND the
    deep view's opening prose (NL-68 item 3 superset), so the two carry the
    identical beats. NL-68 item 4: a forward-looking watch beat carrying a date
    already past relative to the edition has that stale sentence stripped, and
    the beat is dropped whole if nothing forward-looking survives; non-watch
    beats ('Why it matters') legitimately cite past dates and are untouched."""
    out: List[str] = []
    for mv in st.get("movements") or []:
        text = mv.get("text", "")
        if _is_watch_label(mv.get("label", "")) and _is_calendar_date(date):
            text, _stale = analysis.strip_stale_watch(text, date)
            if not (text or "").strip():
                continue
        em = " my-read" if mv.get("em") else ""
        out.append(f'<p class="move-label">{_e(mv["label"])}</p>'
                   f'<p class="{em.strip()}">{_e(text)}</p>')
    return out


def _deep_today_prose(st: Dict, date: str) -> str:
    """NL-68 item 3 (THE SUPERSET LAW): the analyst deep view OPENS with the
    story's own Today prose — its lede + the 'Why it matters'/'Watch for' beats —
    before the analyst sections, so a story's deep view always contains AT LEAST
    its Today-page content, plus more. Byte-for-byte the Today beats (the shared
    movement helper); '' when the story carries no prose (no residue)."""
    paras: List[str] = []
    if st and st.get("lede"):
        paras.append(f'<p>{_e(st["lede"])}</p>')
    if st:
        paras.extend(_story_movement_paras(st, date))
    if not paras:
        return ""
    return f'<div class="deep-section deep-today-prose">{"".join(paras)}</div>'


def _render_story(i: int, st: Dict, slot: Dict, tier: str,
                  active_topics: set, has_file: bool = False,
                  slug: Optional[str] = None, date: str = "",
                  deep_return: str = "view-today", con=None,
                  arc_seen: Optional[set] = None, role: str = "story") -> str:
    """One story in the v7 grid. `role` selects the shape:
    - "lead"    → article.lead: h1 + deck + body + [full picture] + furniture
    - "story"   → article.story (col-right full-picture story): h2 + deck + body + …
    - "snippet" → article.snippet (In Brief, quick tier): h3 + compact deck + body + …
    NL-65: the DECK under the title carries ONLY the follow control; the deep-view
    entry moves to the story BOTTOM (`.story-more`), just before the furniture."""
    slug = slug or f"story-{i}"
    # Heading semantics (v7-M2): ONE h1 per document view — the dateline (Today)
    # / view-title (edition) is the h1, so the lead story demotes to h2 (WAS h1;
    # the M1 multiple-h1 leftover). Stories are h2, In-Brief snippets h3.
    wrap_cls, h = {"lead": ("lead", "h2"), "snippet": ("snippet", "h3")}.get(
        role, ("story", "h2"))
    parts = [f'<article class="{wrap_cls}" id="{_e(slug)}">']

    marks = list(slot.get("matched_memory") or [])
    # The override callout stays ABOVE the title (it explains why an off-beat
    # story is here); the tracked-ongoing marker sits in the deck under the title.
    if slot.get("override"):
        label = slot.get("override_label") or "Editor's override"
        reason = slot.get("world_impact_reason") or ""
        parts.append(
            f'<p class="override-note">{_e(label)}'
            + (f'<span class="reason">{_e(reason)}</span>' if reason else "")
            + "</p>")

    # NL-68 item 6: the visible "The Lead" kicker DIES — the design carries the
    # hierarchy (lead = largest type, top-left of the asymmetric grid). No label.
    parts.append(_headline_html(h, st.get("headline", ""), slot, has_file, tier,
                                slug, deep_return))

    # NL-68 item 7 (kill the covered-before DUPE): the arc line ("When we last
    # covered this …") and the tracked-ongoing marker ("Tracked ongoing story —
    # …") BOTH signal prior coverage — on the real 07-14 lead they rendered
    # together (marker in the deck + arc in the body = the signal TWICE). Compute
    # the arc first: when it renders on a tracked story it is the richer,
    # superseding signal (then → now → difference, with a link to the prior
    # edition), so the redundant deck marker is suppressed. A tracked story with
    # NO arc (day-one / tells-nothing) keeps the marker as its sole signal; a
    # non-tracked story keeps its follow toggle (never carries an arc). This kills
    # the dupe without redesigning the marker/follow control (NL-68 item 2's job).
    arc_html = _today_arc_html(con, slot, st, date, arc_seen)
    suppress_marker = bool(marks) and bool(arc_html)
    follow = "" if suppress_marker else _follow_control(
        st, slot, marks, active_topics, date)
    if follow:
        parts.append(f'<p class="deck">{follow}</p>')

    # Body: lede, arc continuity line, then the "Why it matters"/"Watch for"
    # beats as .move-label headers (§2). Quick-tier snippets carry the lede only.
    body_parts: List[str] = []
    if st.get("lede"):
        body_parts.append(f'<p>{_e(st["lede"])}</p>')
    if arc_html:
        body_parts.append(arc_html)
    if role != "snippet":
        body_parts.extend(_story_movement_paras(st, date))
    parts.append(f'<div class="body">{"".join(body_parts)}</div>')

    # NL-65: the deep-view entry moves to the story BOTTOM, before the furniture.
    entry_link = _deep_entry_link(has_file, tier, slug, deep_return)
    if entry_link:
        parts.append(f'<p class="story-more">{entry_link}</p>')

    # Corroboration furniture — CODE-OWNED, from the slot (never prose).
    here_for = _here_for(slot)
    outlets = slot.get("outlets") or []
    meta = slot.get("corroboration_label", "")
    if outlets:
        meta += f' — {", ".join(outlets)}'
    parts.append(
        f'<p class="furniture">{_e(meta)}. Here for: {_e(here_for)}.</p>')

    parts.append("</article>")
    return "".join(parts)


def _still_tracking_line(slot: Dict) -> str:
    """The compact still-tracking register on Today (INHERITED slot-contract
    requirement; the render that never landed). Composed per the retro-mock
    idiom — state + the dated 'no movement since' note + the next fixed point —
    with A8 no-fabrication teeth: a MISSING note yields NO date clause (never an
    invented date), and the fixed point has no data source in the model yet so it
    degrades to the honest '<STILL_TRACKING_NO_DATE>'. Empty thread name → no
    line (nothing honest to say)."""
    thread = (slot.get("story_title") or "").strip()
    if not thread:
        return ""
    note = (slot.get("still_tracking_note") or "").strip()
    body = (f'{_e(labels.STILL_TRACKING_PREFIX)} '
            f'<span class="st-thread">{_e(thread)}</span>')
    if note:
        body += f' — {_e(note)}'
    body += f'. {_e(labels.STILL_TRACKING_NO_DATE)}'
    return f'<p class="st-line">{body}</p>'


def _here_for(slot: Dict) -> str:
    """The 'Here for' rationale — CODE-OWNED, from the slot (never prose). One
    source of truth shared by Today's meta-footnote and NL-66(b)'s sources-&-
    context view: matched tags + tracked threads, else the editor's override,
    else the world-impact fallback.

    NL-68 exhibit ('Strait of Hormuz, Strait of Hormuz'): a tag and a tracked
    thread of the same name doubled the line. Dedupe case-insensitively and
    order-preserving — tags first, then threads; a thread that only repeats a
    tag name (any case) is dropped. Empty names are dropped too."""
    ordered: List[str] = []
    seen: set = set()
    tag_names = [t.get("name", "") for t in slot.get("matched_tags") or []
                 if isinstance(t, dict)]
    for name in tag_names + list(slot.get("matched_memory") or []):
        if not name:
            continue
        key = name.lower()
        if key in seen:
            continue
        seen.add(key)
        ordered.append(name)
    matches = ", ".join(ordered)
    if matches:
        return matches
    if slot.get("override"):
        return "editor's override — see note above"
    return "world-impact selection (no tag or thread match)"


def _follow_control(st: Dict, slot: Dict, marks: List[str],
                    active_topics: set, date: str) -> str:
    """The under-title follow control (NL-65: it STAYS under the title, alone).
    Recognition (NL-58 P3a, both directions): a story reads as followed when
    EITHER its thread is active (matched_memory `marks`) OR its story-follow
    title is active — checked against both story_title and headline, so a follow
    created under one edition's phrasing survives title drift into the next.
    Thread-tracked stories show the marker STATE; story-follows are a toggle."""
    if marks:
        return (f'<span class="tracked-marker">{_e(labels.TRACKED_ONGOING_PREFIX)} '
                f'{_e(", ".join(marks))}</span>')
    topic = slot.get("story_title") or st.get("headline") or ""
    headline = st.get("headline") or ""
    t_in = topic.lower() in active_topics
    h_in = headline.lower() in active_topics
    followed = t_in or h_in
    if followed and not t_in:
        # NL-60 gate F1: unfollow must target the STORED thread phrasing —
        # dismiss_thread is an exact match, so a drift-recognized follow sending
        # the unmatched title would be visible but unfollowable.
        topic = headline
    pressed = "true" if followed else "false"
    label = (labels.FOLLOW_STORY_ACTIVE if followed
             else labels.FOLLOW_STORY_INACTIVE)
    cls = " followed" if followed else ""
    date_attr = f' data-briefing-date={_e_attr(date)}' if date else ""
    return (f'<button class="follow-story-btn{cls}" data-topic={_e_attr(topic)}'
            f'{date_attr} aria-pressed="{pressed}" onclick="toggleFollow(this)">'
            f'{_e(label)}</button>')


def _has_deep_view(has_file: bool, tier: str) -> bool:
    """A story has an openable deep view iff it carries an analyst brief
    (has_file) OR it is an In-Brief quick-tier item (the $0 sources-&-context
    view). Everything else — a degraded-hidden full/medium, a still-tracking
    status line — has none, and must never render a dead link (NL-68 item 8)."""
    return bool(has_file) or tier == "quick"


def _deep_view_onclick(slug: str, deep_return: str) -> str:
    """The ONE deep-view open call, shared by the story title (NL-68 item 8) and
    the bottom entry link so both target the identical view. openDeepView calls
    e.preventDefault(), so the href='#' fallback never navigates."""
    ret = "" if deep_return == "view-today" else f", '{_e(deep_return)}'"
    return f"openDeepView('{_e(slug)}', event{ret})"


def _headline_html(tag: str, headline: str, slot: Dict, has_file: bool,
                   tier: str, slug: str, deep_return: str) -> str:
    """The story headline. NL-68 item 8: when the story has a deep view, the
    TITLE itself is a click-through to it (same target as the bottom entry), a
    real keyboard-operable <a> (never a bare onclick div). A story with no deep
    view (degraded-hidden) renders a plain heading — no dead link."""
    text = _e(headline)
    if _has_deep_view(has_file, tier):
        text = (f'<a class="headline-link" href="#" '
                f'onclick="{_deep_view_onclick(slug, deep_return)}">{text}</a>')
    return f'<{tag} class="headline">{text}</{tag}>'


def _deep_entry_link(has_file: bool, tier: str, slug: str,
                     deep_return: str) -> str:
    """The deep-view entry (NL-65: moved to the story BOTTOM). Three binding
    states (v4 addendum): 'The full picture' for a briefed slot; the In-Brief
    quick tier's own $0 'Sources & context' entry (NL-66b); and degraded-hidden
    (a failed full/medium brief renders NOTHING — absence is the signal).
    has_file wins: a briefed slot is never demoted to the sources-&-context
    label."""
    onclick = _deep_view_onclick(slug, deep_return)
    if has_file:
        return (f'<a class="deep-view-entry-link" href="#" '
                f'onclick="{onclick}">'
                f'→ {_e(labels.FULL_PICTURE)}</a>')
    if tier == "quick":
        return (f'<a class="deep-view-entry-link sources-context-link" href="#" '
                f'onclick="{onclick}">'
                f'→ {_e(labels.SOURCES_CONTEXT)}</a>')
    return ""


def _e_attr(v: str) -> str:
    return '"' + escape(str(v or ""), quote=True) + '"'


def _render_today(con: sqlite3.Connection, row, entry: Optional[Dict],
                  gen_state: Dict[str, str],
                  briefs: Optional[Dict[int, Dict]] = None) -> str:
    """The Today view: the v7 masthead ceremony + section line, then the edition
    grid (or an honest empty/loading/error state). On the non-edition states the
    masthead shows the dateline only (no dispatch strip / edition bar — nothing
    to receipt)."""
    mast_date = row["date"] if row is not None \
        else datetime.now().strftime("%Y-%m-%d")
    running_or_error = gen_state["state"] in ("running", "error")
    head = (_masthead(None if running_or_error else row, mast_date)
            + _section_line("today"))

    if gen_state["state"] == "running":
        body = """
<div class="state-panel" id="gen-running">
  <h2>Generating today’s edition…</h2>
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
    elif gen_state["state"] == "error":
        body = f"""
<div class="state-panel">
  <h2>Today’s edition failed</h2>
  <p class="error-text">{_e(gen_state["error"])}</p>
  <p>No half-written edition ever goes out: a failure before the save
     publishes nothing; one during file export after the save leaves the
     saved edition intact.</p>
  <button class="cta-quiet" onclick="generateAgain()">Try again</button>
</div>"""
    elif row is None:
        # NL-11: no edition for TODAY -> the empty state, never an older edition
        # dressed as current. If the archive has earlier editions, point there.
        has_archive = con.execute(
            "SELECT 1 FROM briefings LIMIT 1").fetchone() is not None
        if has_archive:
            body = """
<div class="state-panel">
  <h2>Nothing for today yet</h2>
  <p>No edition has been generated for today. A new one takes a couple of
     minutes: it fetches your sources, picks the stories, writes the briefing,
     and records the episode.</p>
  <button class="cta-quiet" onclick="generateAgain()">Generate today’s edition</button>
  <p class="empty-note" style="margin-top:1rem;">Earlier editions are in your
     <a href="#" onclick="showView('archive'); return false;">Archive</a>.</p>
</div>"""
        else:
            body = """
<div class="state-panel">
  <h2>Nothing yet</h2>
  <p>No edition has been generated. The first one takes a couple of minutes:
     it fetches your sources, picks the stories, writes the briefing, and
     records the episode.</p>
  <button class="cta-quiet" onclick="generateAgain()">Generate today’s edition</button>
</div>"""
    else:
        # NL-11: the glance ("In today’s briefing") section is REMOVED. The lead
        # story opens the reading surface.
        body = _render_briefing_body(con, row, entry, briefs, "", "view-today")

    return head + f'<div class="page">{body}</div>'


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

    # BUG-35: one dedup set per EDITION — a same-thread arc line renders under
    # its most prominent (earliest) slot only; a split-day sibling suppresses
    # the identical continuity paragraph.
    arc_seen: set = set()
    lead_html = ""
    right_stories: List[str] = []      # col-right full/medium "full picture" stories
    brief_snips: List[str] = []        # In Brief (quick tier)
    still_lines: List[str] = []        # still-tracking register
    # §12.3 slot routing. Ids/tiers use the ORIGINAL enumerate index so
    # _collect_deep_views stays aligned (a still-tracking slot consumes its
    # index but gets no deep view — it is a status line, not a story).
    for i, st in enumerate(stories):
        slot = slots[i] if i < len(slots) else {}
        tier = tiers[i] if i < len(tiers) else (
            "full" if i == 0 else "medium" if i <= 2 else "quick")
        if slot.get("still_tracking"):
            line = _still_tracking_line(slot)
            if line:
                still_lines.append(line)
            continue
        role = "lead" if i == 0 else ("snippet" if tier == "quick" else "story")
        rendered = _render_story(
            i, st, slot, tier, active, has_file=(i + 1) in (briefs or {}),
            slug=f"{slug_prefix}story-{i}", date=row["date"],
            deep_return=deep_return, con=con, arc_seen=arc_seen, role=role)
        if role == "lead":
            lead_html = rendered
        elif role == "snippet":
            brief_snips.append(rendered)
        else:
            right_stories.append(rendered)

    col_parts: List[str] = list(right_stories)
    if brief_snips:
        col_parts.append(
            f'<div class="in-brief" role="region" aria-label={_e_attr(labels.IN_BRIEF)}>'
            f'<h2 class="brief-label">{_e(labels.IN_BRIEF)}</h2>'
            + "".join(brief_snips) + "</div>")
    if still_lines:
        col_parts.append(
            f'<div class="still-tracking" role="region" '
            f'aria-label={_e_attr(labels.STILL_TRACKING_PREFIX)}>'
            + "".join(still_lines) + "</div>")
    col_right = (f'<div class="col-right">{"".join(col_parts)}</div>'
                 if col_parts else "")
    grid = f'<div class="today-grid">{lead_html}{col_right}</div>'

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
    footer = f"""
<footer class="edition-footer footer-tag">
  <button class="disclosure-trigger" id="{btn_id}" aria-expanded="false"
          aria-controls="{dtl_id}" onclick="toggleFooterDisclosure(this)">
    <span class="caret">▸</span> Generated {_e(gen_local)}
  </button>{window_html}
  <div class="footer-detail" id="{dtl_id}">{"".join(detail_ps)}</div>
</footer>"""
    return grid + footer


def _run_cost(entry: Optional[Dict]) -> str:
    usd = (entry or {}).get("total_usd")
    if usd is None:
        return "cost not recorded for this edition"
    try:
        return f"generated for ${float(usd):.2f}" if float(usd) > 0 \
            else "generated locally at $0 marginal"
    except (TypeError, ValueError):
        return "cost not recorded for this edition"


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
    """NL-68 item 12: the Topics search suggests LIVE topics — the tags the
    LATEST edition matched — minus what you already follow. WAS drawn from
    _topic_vocabulary (the ALL-TIME accumulation of every tag ever matched), so a
    topic you'd DELETED lingered as a suggestion forever ('returns only deleted
    topics'). Scoping to the latest edition keeps the recall live and stops old
    deleted topics resurfacing. DECISIONS 2026-07-17 "standing orders": the
    Topics combobox is now suggestions-only (like story follows), so only a
    name offered here can be added — free-typing a new topic no longer acts.

    Flagged (NL-17/18): matched_tags are structurally a subset of your followed
    vocabulary, so in steady state this is empty — a real 'topics to discover'
    add-source is the skeleton-catalog work, not a suggestion off past editions.
    Topics carry no secondary line."""
    followed = {t.lower() for t in cfg.interests_broad} \
        | {t.lower() for t in cfg.interests_granular}
    row = con.execute("SELECT story_slots FROM briefings"
                      " ORDER BY date DESC LIMIT 1").fetchone()
    if row is None:
        return []
    try:
        slots = json.loads(row["story_slots"] or "[]")
    except (ValueError, TypeError):
        return []
    names: Dict[str, str] = {}
    for s in slots if isinstance(slots, list) else []:
        for tg in s.get("matched_tags") or []:
            if not (isinstance(tg, dict) and tg.get("name")):
                continue
            key = tg["name"].lower()
            if key not in followed and key not in names:
                names[key] = tg["name"]
    return [{"v": n, "l": n} for _, n in sorted(names.items())]


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
                    aria_label: str, data: List[Dict],
                    suggest_only: bool = False) -> str:
    """The shared house-styled suggestion combobox (NL-11) — replaces the
    native datalist, which is browser-dependent (notoriously weak in Safari)
    and structurally could not exclude followed entries, carry a secondary
    line, or be styled. Settings-context editor exception under DIRECTION law:
    outlined, spaced, uncolored, no chips. Keyboard-driven (arrow/enter/escape)
    in the shipped JS; with no JS the list stays hidden and the field degrades
    to a plain text input. The JSON payload is <>&-escaped so a hostile
    recalled name can't break out of the <script> element.

    NL-68 item 10: suggest_only marks a surface where raw typed text must NEVER
    act — only a picked suggestion follows (the ruled story-follow contract);
    the client JS reads data-suggest-only and no-ops any non-matching entry."""
    only = ' data-suggest-only="1"' if suggest_only else ''
    payload = (json.dumps(data, ensure_ascii=False)
               .replace("<", "\\u003c").replace(">", "\\u003e")
               .replace("&", "\\u0026"))
    return (
        f'<div class="suggest" data-kind="{_e(kind)}"{only}>'
        f'<input class="token-search" type="text" role="combobox"'
        f' aria-expanded="false" aria-autocomplete="list"'
        f' aria-controls="{_e(list_id)}" autocomplete="off"'
        f' placeholder="{_e(placeholder)}" aria-label="{_e(aria_label)}"'
        f' oninput="suggestInput(this)" onkeydown="suggestKeydown(event,this)"'
        f' onfocus="suggestInput(this)" onblur="suggestBlur(this)">'
        f'<ul class="suggest-list" id="{_e(list_id)}" role="listbox" hidden></ul>'
        f'<script type="application/json" class="suggest-data">{payload}</script>'
        f'</div>')


def _thread_state_card(t: Dict) -> str:
    """NL-63 item 6: the standing state + last-delta line inside a Following
    dossier. The state carries a stale-but-honest 'as of <date>' when it is
    older than the last delta; a thread with no state yet shows just the last
    delta. Structure now; v7 visual styling refines after sight-approval."""
    from . import memory_core, ranking
    # M1 gate F: derive "today" from the SAME clock that mints edition dates
    # (ranking.local_today) — an ad-hoc datetime.now() here risked a local-vs-UTC
    # split with the as_of_date it compares against, mislabeling a fresh state
    # stale (or the reverse) across a midnight boundary.
    today = ranking.local_today()
    bits: List[str] = []
    state_text = t.get("state_text") or ""
    as_of = t.get("state_as_of") or ""
    if state_text:
        stale, note = memory_core.state_is_stale(
            {"as_of_date": as_of}, today)
        as_of_html = (f' <span class="state-asof">({_e(note)})</span>'
                      if stale and note else
                      (f' <span class="state-asof">(as of '
                       f'{_e(memory_core.human_date(as_of))})</span>'
                       if as_of else ""))
        bits.append(f'<p class="dossier-state">{_e(state_text)}{as_of_html}</p>')
    d = t.get("last_delta")
    if d:
        signif = f" — {_e(d['significance'])}" if d.get("significance") else ""
        bits.append(
            f'<p class="dossier-delta"><span class="delta-label">Latest '
            f'({_e(memory_core.human_date(d["date"]))}):</span> '
            f'{_e(d["what_happened"])}{signif}</p>')
    return "".join(bits)


def _thread_name_link(tid: int, topic: str, tag: str = "h2") -> str:
    """The thread NAME as a Following row's single action (Design's ruling —
    extends the §12.5 fold grammar to the loud updated rows too): a link to the
    thread page (openThread). Accessible name = the topic (distinguishable across
    19+ rows, §12.5 'label = accessible name'); the shared 'fallback control
    label' labels.THREAD_WHOLE rides as the control's title so the row's single
    action is named from the label table. The name is a real heading so AT can
    navigate the thread list."""
    return (f'<{tag} class="thread-name"><a href="#" '
            f'onclick="openThread(\'{tid}\', event); return false;" '
            f'title={_e_attr(labels.THREAD_WHOLE)}>{_e(topic)}</a></{tag}>')


def _thread_row_link(tid: int, topic: str) -> str:
    """The compressed-row variant (quiet fold + lifecycle rows): the name as a
    plain link (not a heading — 17 quiet names as headings would flood the
    heading list), same single-action grammar and label."""
    return (f'<a href="#" onclick="openThread(\'{tid}\', event); return false;" '
            f'title={_e_attr(labels.THREAD_WHOLE)}>{_e(topic)}</a>')


def _spine_updated_row(t: Dict) -> str:
    """§7 anatomy for an updated row: ●UPDATED stamp (machine register) → thread
    name (single action) → one-line delta → optional note (2-line clamp). The
    lifecycle verbs move to the thread page (§10: one inline action per row; all
    other verbs live in the editor). Delta lines are real NL-63 ledger output."""
    d = t.get("this_delta") or {}
    date_h = _human_short(d.get("date", "")).upper() if d.get("date") else ""
    stamp = (f'<span class="t-stamp"><span class="t-moved">{_e(labels.UPDATED_DOT)} '
             f'{_e(labels.UPDATED_STAMP)}</span> · {_e(labels.UPDATED_THIS_EDITION)}'
             + (f' · {_e(date_h)}' if date_h else "") + '</span>')
    name = _thread_name_link(t["id"], t["topic"], tag="h2")
    delta_html = (f'<p class="thread-delta">{_e(d.get("what_happened", ""))}</p>'
                  if d.get("what_happened") else "")
    note = (t.get("note") or "").strip()
    note_html = f'<p class="thread-note">{_e(note)}</p>' if note else ""
    return f'<article class="thread">{stamp}{name}{delta_html}{note_html}</article>'


def _quiet_fold_html(quiet: List[Dict], zero_updated: bool) -> str:
    """§12.5: ALL quiet active threads behind ONE counted, keyboard-operable
    disclosure. Compressed rows = name-as-link (the single action) + LAST UPDATED
    stamp where a date exists. Order: last-updated recency then A–Z. Defaults
    OPEN on a zero-updated morning (a lone closed fold reads as an empty page).
    Native <details>/<summary>; the count rides in the summary's accessible
    name (color is never the sole channel)."""
    n = len(quiet)
    noun = labels.QUIET_FOLD_NOUN_ONE if n == 1 else labels.QUIET_FOLD_NOUN
    rows = []
    for t in quiet:
        stamp = ""
        if t.get("last_updated"):
            stamp = (f' <span class="q-stamp">{_e(labels.LAST_UPDATED)} '
                     f'{_e(_human_short(t["last_updated"]).upper())}</span>')
        rows.append(f'<li class="q-row">{_thread_row_link(t["id"], t["topic"])}'
                    f'{stamp}</li>')
    open_attr = " open" if zero_updated else ""
    return (f'<details class="quiet-fold"{open_attr}>'
            f'<summary><span class="qf-count">{n} {_e(noun)}</span> · '
            f'{_e(labels.QUIET_FOLD_SUFFIX)}</summary>'
            f'<ul class="q-list">{"".join(rows)}</ul></details>')


def _lifecycle_row(t: Dict, stamp: str) -> str:
    """A dormant/dismissed row: name-as-action (single action → thread page) +
    a quiet lifecycle stamp. The Resume/Delete verbs live on the thread page."""
    return (f'<div class="q-row lifecycle-row">'
            f'{_thread_row_link(t["id"], t["topic"])} '
            f'<span class="q-stamp">{_e(stamp)}</span></div>')


def _story_follow_suggestions(con: sqlite3.Connection) -> List[Dict]:
    """NL-68 item 10: the SUGGESTIONS the 'Follow a new story' combobox offers —
    recent briefing stories/threads you don't already actively follow (the ruled
    contract: no free text). Sources: the story titles from recent editions, then
    dormant/dismissed threads you could re-follow. Active follows are excluded
    (you can't follow what you have). Deterministic, deduped, recent-first."""
    active = {r["topic"].lower() for r in con.execute(
        "SELECT topic FROM memory WHERE status = 'active'")}
    seen: set = set()
    out: List[Dict] = []
    for r in con.execute(
            "SELECT story_slots FROM briefings ORDER BY date DESC LIMIT 10"):
        try:
            slots = json.loads(r["story_slots"] or "[]")
        except (ValueError, TypeError):
            continue
        for s in slots if isinstance(slots, list) else []:
            title = (s.get("story_title") or "").strip()
            key = title.lower()
            if title and key not in active and key not in seen:
                seen.add(key)
                out.append({"v": title, "l": title})
    for r in con.execute(
            "SELECT topic FROM memory WHERE status != 'active' ORDER BY id DESC"):
        topic = (r["topic"] or "").strip()
        key = topic.lower()
        if topic and key not in active and key not in seen:
            seen.add(key)
            out.append({"v": topic, "l": topic, "s": "an earlier thread"})
    return out


def _following_threads_subview(g: Dict[str, List[Dict]],
                               story_suggest: str = "") -> str:
    """The Threads view — the Spine at real scale (§7/§12.2/§12.5): loud updated
    rows (few), then the counted quiet fold; then the lifecycle sections (Quiet
    for now / You stopped following) below, their headers real h2s. NL-68 item
    10: 'Follow a new story' is a suggestions-only combobox (story_suggest),
    never a free-text field."""
    active = g["active"]
    updated = sorted((t for t in active if t.get("updated")),
                     key=lambda t: t["topic"].lower())
    quiet = [t for t in active if not t.get("updated")]
    quiet.sort(key=lambda t: t["topic"].lower())                    # A–Z tiebreak
    quiet.sort(key=lambda t: t.get("last_updated") or "", reverse=True)  # recency

    out = [f'<div class="follow-story">{story_suggest}</div>']
    if not active:
        out.append(f'<p class="empty-note">{_e(labels.FOLLOWING_EMPTY)}</p>')
    for t in updated:
        out.append(_spine_updated_row(t))
    if quiet:
        out.append(_quiet_fold_html(quiet, zero_updated=(not updated)))
    if g["dormant"]:
        out.append(f'<h2 class="section-h">{_e(labels.FOLLOWING_DORMANT_H)}</h2>')
        for t in g["dormant"]:
            out.append(_lifecycle_row(
                t, f"Quiet since {_human_short(t['quiet_since'])}"
                if t.get("quiet_since") else "Quiet"))
    if g["dismissed_user"]:
        out.append(f'<h2 class="section-h">{_e(labels.FOLLOWING_DISMISSED_H)}</h2>')
        for t in g["dismissed_user"]:
            out.append(_lifecycle_row(
                t, f"Stopped {_human_short(t['quiet_since'])}"
                if t.get("quiet_since") else "Stopped"))
    return "".join(out)


def _render_following(con: sqlite3.Connection) -> str:
    g = _following_rows(con)
    cfg = config.load_sources()
    # NL-68 item 10: the story-follow combobox — suggestions-only (no free text).
    story_suggest = _render_suggest(
        "story", "story-suggest", "Follow a story…", "Follow a story",
        _story_follow_suggestions(con), suggest_only=True)
    threads_html = _following_threads_subview(g, story_suggest)

    def token(name: str, kind: str, label: Optional[str] = None) -> str:
        return (f'<span class="token">{_e(label or name)}'
                f'<button class="token-remove" aria-label="Remove {_e(label or name)}"'
                f' onclick="removeToken({_e(_js_str(kind))}, {_e(_js_str(name))}, this)">×</button></span>')

    # NL-11: the shared house-styled suggestion component (replaces the native
    # datalist). Excludes already-followed topics; keyboard-accessible; no-JS
    # degrades to a plain input.
    # NL-68 item 14: the "suggestions draw from everything coverage has
    # matched…" explainer DIES (interface narration; also stale after item 12).
    # The placeholder carries the affordance.
    # Free-text topic entry DIES (DECISIONS 2026-07-17 "standing orders"): the
    # type-to-add that survived v7.2 item 12 becomes suggestions-only, exactly
    # like the story combobox — only a picked suggestion adds a topic; raw typed
    # text no-ops. Suggestions-only is the product law for topic/thread surfaces.
    topics = [
        _render_suggest("topic", "topic-suggest", "Search topics…",
                        "Search topics", _topic_suggestions(con, cfg),
                        suggest_only=True),
    ]
    for group, label in ((cfg.interests_broad, "Broad"),
                         (cfg.interests_granular, "Specific")):
        topics.append(f'<div class="token-group"><p class="token-group-name">'
                      f'{label} ({len(group)})</p><div class="token-list">')
        topics.extend(token(n, "topic") for n in group)
        if not group:
            topics.append('<p class="empty-note">Nothing yet</p>')
        topics.append("</div></div>")

    # NL-68 item 14: the "Suggestions recall writers the system already knows…"
    # interface narration DIES; the functional facts (a follow adds their feed;
    # a new writer needs a feed link) stay — they tell the user what an action
    # does and what it requires, not "here's what you're looking at".
    writers = [
        _render_suggest("writer", "writer-suggest", "Search or add a writer…",
                        "Search or add a writer", _writer_suggestions(cfg)),
        '<p class="token-search-hint">Following a writer adds their feed to '
        'your sources and boosts their pieces in ranking; adding someone '
        'new takes their feed link.</p>',
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

    # v7-M2 (§4 + §12.4): mini-masthead + section line, the LOUD page-title, then
    # the triad view-line (Threads · Topics · Writers) as a quiet text line — real
    # links, current at 700 ink, no pills; INTERIM pending NL-18. The Threads view
    # is the Spine; its rows target the M2 thread page.
    head = _mini_head(datetime.now().strftime("%Y-%m-%d")) + _section_line("following")

    def triad(sub: str, label: str, current: bool) -> str:
        cur = ' aria-current="true" class="current"' if current else ''
        return (f'<a href="#"{cur} onclick="showSub(\'{sub}\', this); '
                f'return false;">{_e(label)}</a>')

    return head + f"""<div class="page">
<h1 class="page-title">{_e(labels.NAV_FOLLOWING)}</h1>
<nav class="view-line" aria-label="Following views">
  {triad("threads", labels.FOLLOWING_TRIAD_THREADS, True)}
  {triad("topics", labels.FOLLOWING_TRIAD_TOPICS, False)}
  {triad("writers", labels.FOLLOWING_TRIAD_WRITERS, False)}
</nav>
<div id="sub-threads" class="sub-view active">{threads_html}</div>
<div id="sub-topics" class="sub-view">{"".join(topics)}</div>
<div id="sub-writers" class="sub-view">{"".join(writers)}</div></div>"""


def _human_short(date_str: str) -> str:
    try:
        d = datetime.strptime(date_str, "%Y-%m-%d")
        return f"{d.strftime('%b')} {d.day}"
    except ValueError:
        return date_str


def _cal_accessible_name(dstr: str, is_today: bool) -> str:
    """Full accessible name for an edition cell (§8): 'Friday, July 10, 2026 —
    edition' (or '— today’s edition')."""
    d = datetime.strptime(dstr, "%Y-%m-%d")
    human = f"{d.strftime('%A, %B')} {d.day}, {d.year}"
    return human + (" — today’s edition" if is_today else " — edition")


def _calendar_html(latest_ed: str, ed_dates: set, utc_by_date: Dict[str, str],
                   today: str) -> str:
    """The §8 calendar for the month of the latest edition. Three exhaustive day
    classes: edition (ink 700, moved-green underline, whole cell a link with a
    full accessible name, real assembly stamp) · gap-in-history (ink-faint, no
    shame copy) · pre-history + future (--cal-bare, barest). Today's edition adds
    the terracotta ring. Sunday-start grid; decorative cells are aria-hidden; the
    list below it is the primary accessible rendering. No streaks, no topic
    words. The grid is an index."""
    y, m = int(latest_ed[:4]), int(latest_ed[5:7])
    ndays = calendar.monthrange(y, m)[1]
    first_ed = min(ed_dates) if ed_dates else ""
    lead_blanks = (datetime(y, m, 1).weekday() + 1) % 7   # Sunday-start offset
    dow = "".join(f'<span class="cal-dow" aria-hidden="true">{d}</span>'
                  for d in ("Su", "Mo", "Tu", "We", "Th", "Fr", "Sa"))
    cells = ['<span class="cal-cell" aria-hidden="true"></span>'] * lead_blanks
    for day in range(1, ndays + 1):
        dstr = f"{y:04d}-{m:02d}-{day:02d}"
        if dstr in ed_dates:
            is_today = dstr == today
            cls = "cal-cell cal-edition" + (" cal-today" if is_today else "")
            stamp = (f'<span class="cal-stamp">{_e(utc_by_date.get(dstr, ""))} UTC</span>'
                     if utc_by_date.get(dstr) else "")
            cells.append(
                f'<span class="{cls}"><a href={_e_attr("/?date=" + dstr)} '
                f'onclick="return openEdition(\'{_e(dstr)}\', event)" '
                f'aria-label={_e_attr(_cal_accessible_name(dstr, is_today))}>'
                f'<span class="cal-num">{day}</span>{stamp}</a></span>')
        elif first_ed and first_ed <= dstr <= today:
            cells.append('<span class="cal-cell cal-gap" aria-hidden="true">'
                         f'<span class="cal-num">{day}</span></span>')
        else:
            cells.append('<span class="cal-cell cal-void" aria-hidden="true">'
                         f'<span class="cal-num">{day}</span></span>')
    return f'<div class="cal-grid">{dow}{"".join(cells)}</div>'


def _render_archive(con: sqlite3.Connection) -> str:
    """§8 rebuild: the Study/Wire calendar in Front Page type + the list-below as
    the PRIMARY accessible rendering. mini-masthead + section line open the view
    (§4). The month shown is the latest edition's; a month with zero editions
    renders the honest empty state (paging across months is a follow-up)."""
    head = _mini_head(datetime.now().strftime("%Y-%m-%d")) + _section_line("archive")
    today = datetime.now().strftime("%Y-%m-%d")
    editions: List[Dict] = []
    for row in con.execute("SELECT * FROM briefings ORDER BY date DESC"):
        entry = _log_entry_for(row["date"])
        stories, _ = _stories_for(row, entry)
        lead = stories[0].get("headline", "") if stories else ""
        editions.append({"date": row["date"], "utc": _utc_hm(row["generated_at"]),
                         "lead": lead})
    if not editions:
        return head + (f'<div class="page">'
                       f'<h1 class="page-title">{_e(labels.NAV_ARCHIVE)}</h1>'
                       f'<p class="empty-note">{_e(labels.ARCHIVE_EMPTY)}</p></div>')
    ed_dates = {e["date"] for e in editions}
    latest_ed = max(ed_dates)
    utc_by_date = {e["date"]: e["utc"] for e in editions}
    y, m = int(latest_ed[:4]), int(latest_ed[5:7])
    month_name = datetime(y, m, 1).strftime("%B")
    n_ed = sum(1 for d in ed_dates if d[:7] == latest_ed[:7])
    cal = _calendar_html(latest_ed, ed_dates, utc_by_date, today)
    list_items = []
    for e in editions:
        tag = f' · {labels.ARCHIVE_TODAY_TAG}' if e["date"] == today else ""
        stamp = (f'{e["date"]} · {e["utc"]} UTC{tag}' if e["utc"]
                 else f'{e["date"]}{tag}')
        # NL-11: JS opens the edition IN-PLACE; the href is the no-JS fallback.
        list_items.append(
            f'<li><span class="al-date">{_e(stamp)}</span>'
            f'<a href={_e_attr("/?date=" + e["date"])} '
            f'onclick="return openEdition(\'{_e(e["date"])}\', event)">'
            f'{_e(e["lead"] or "(untitled edition)")}</a></li>')
    return head + (
        f'<div class="page">'
        f'<h1 class="month-title">{_e(month_name)} <span class="yr">{y}</span></h1>'
        # NL-68 item 14: the "The grid is an index of the list below it."
        # interface-explainer DIES (the audience is smarter). The edition COUNT
        # is a factual caption, not condescension, and stays.
        f'<p class="cal-note">{n_ed} edition{"s" if n_ed != 1 else ""} this month.</p>'
        f'{cal}'
        f'<ul class="archive-list">{"".join(list_items)}</ul></div>')


# ---------------------------------------------------------------------------
# The thread page (the "Open thread" destination — DECISIONS 2026-07-14: "the
# thread page ... standing state + full timeline + open question/next fixed
# point + verbs, composed from M1's components"). Renders from thread_state +
# thread_deltas + memory ONLY — persisted, honest empty states, no invented
# fields (the kill-test law: a day-one thread gets no arc/story-so-far, ever).
# Reached from a Following row (the name is the single action, openThread); a
# sibling .view like the deep views, switched client-side.
# ---------------------------------------------------------------------------

def _superseded_li_marks(con: sqlite3.Connection, by_id: Dict,
                         superseded_by) -> Tuple[str, str]:
    """D1 / the 0012 read-side contract (migration header; Rook's gate;
    memory_core's own 'the server strikes it'): a superseded ledger row renders
    STRUCK and annotated with the date of the entry that corrected it — never
    dropped, never indistinguishable from live history (else the reader-facing
    archive and the machine state disagree). Returns (li_class_suffix,
    correction_note_html); ('', '') for a live row. The superseding date comes
    from the same row set when present (both render paths carry `id`), else a
    direct ledger lookup so a corrector outside the shown window still names."""
    if not superseded_by:
        return "", ""
    from . import memory_core
    row = by_id.get(superseded_by)
    raw = (row.get("date") or row.get("edition_date")) if row else None
    if raw is None:
        r = con.execute("SELECT edition_date FROM thread_deltas WHERE id = ?",
                        (superseded_by,)).fetchone()
        raw = r["edition_date"] if r else None
    when = memory_core.human_date(raw).upper() if raw else ""
    tail = f" {_e(when)}" if when else ""
    note = f' <span class="tl-superseded-note">— superseded{tail}</span>'
    return " tl-superseded", note


def _thread_timeline_html(con: sqlite3.Connection, tid: int, anchor: str) -> str:
    """The story so far — the FULL dated ledger (the thread page is edition-
    independent, so no never-re-lede bound; it shows every entry incl. today's).
    Deterministic from thread_deltas; gaps are named by absence, never
    backfilled (Sten's law). '' when the thread has no ledger (day-one)."""
    from . import memory_core
    rows = memory_core.timeline_rows(con, tid)          # oldest first, full ledger
    if not rows:
        return ""
    items = []
    by_id = {e.get("id"): e for e in rows}
    for e in rows:
        hd = memory_core.human_date(e["date"]).upper()
        signif = (f' <span class="tl-signif">— {_e(e["significance"])}</span>'
                  if e.get("significance") else "")
        sup_class, sup_note = _superseded_li_marks(con, by_id, e.get("superseded_by"))
        what = (f'<s class="tl-struck">{_e(e["what_happened"])}</s>'
                if sup_class else _e(e["what_happened"]))
        items.append(f'<li class="tl-entry{sup_class}"><span class="tl-date">'
                     f'{_e(hd)}</span> — {what}{signif}{sup_note}</li>')
    return (f'<div class="deep-section" id="{anchor}-timeline">'
            f'<h2 class="deep-section-label">{_e(labels.THE_STORY_SO_FAR)}</h2>'
            f'<ul class="deep-timeline-list">{"".join(items)}</ul></div>')


def _thread_editions_html(con: sqlite3.Connection, ledger: List[Dict],
                          anchor: str) -> str:
    """The edition back-links: the distinct dated editions that moved this
    thread, each linking to that edition in place (openEdition; the href is the
    no-JS fallback). Never-a-dead-link (NL-60): only editions that exist as
    briefing rows are linked, the rest render as plain dates."""
    have = {r["date"] for r in con.execute("SELECT date FROM briefings")}
    seen: List[str] = []
    for e in ledger:
        if e["edition_date"] not in seen:
            seen.append(e["edition_date"])
    links = []
    for d in seen:
        hd = _human_short(d)
        if d in have and _is_calendar_date(d):
            links.append(f'<a href={_e_attr("/?date=" + d)} '
                         f'onclick="return openEdition(\'{_e(d)}\', event)">'
                         f'{_e(hd)}</a>')
        else:
            links.append(f'<span>{_e(hd)}</span>')
    return (f'<div class="deep-section" id="{anchor}-editions">'
            f'<h2 class="deep-section-label">{_e(labels.THREAD_EDITIONS_LABEL)}</h2>'
            f'<p class="thread-editions">'
            + ' <span class="sep">·</span> '.join(links) + '</p></div>')


def _thread_verbs_html(topic: str, note: str, status: str) -> str:
    """The lifecycle verbs (§10: one inline action per Following ROW — the name;
    every OTHER verb lives in the editor, and the thread page IS the editor).
    Status-scoped: active → Edit note + Stop; dormant → Edit note + Resume;
    dismissed → Resume + Delete. Shared JS with the CLI-equivalent verbs."""
    js = _js_str(topic)
    b: List[str] = []
    edit = (f'<button onclick="openEditNote({_e(js)}, {_e(_js_str(note))})">'
            f'{_e(labels.VERB_EDIT_NOTE)}</button>')
    if status == "active":
        b.append(edit)
        b.append(f'<button onclick="threadAction(\'dismiss\', {_e(js)})">'
                 f'{_e(labels.VERB_STOP)}</button>')
    elif status == "dormant":
        b.append(edit)
        b.append(f'<button onclick="threadAction(\'revive\', {_e(js)})">'
                 f'{_e(labels.VERB_RESUME)}</button>')
    elif status == "dismissed_user":
        b.append(f'<button onclick="threadAction(\'revive\', {_e(js)})">'
                 f'{_e(labels.VERB_RESUME)}</button>')
        b.append(f'<button class="delete-action" onclick="openDeleteConfirm({_e(js)})">'
                 f'{_e(labels.VERB_DELETE)}</button>')
    return f'<div class="thread-verbs">{"".join(b)}</div>'


def _render_thread_page(con: sqlite3.Connection, mrow) -> str:
    """One thread page. Standing state ("Where this stands", as-of + staleness)
    then the story-so-far (full ledger) then the edition back-links then the
    verbs. Honest empty states throughout; a day-one thread (no state, no ledger)
    renders the honest 'new thread' notes, never a fabricated arc."""
    from . import memory_core
    tid, topic, status = mrow["id"], mrow["topic"], mrow["status"]
    anchor = f"thread-{tid}"
    out = [f'<section id="view-{anchor}" class="view">']
    out.append('<a class="deep-back" href="#" '
               f'onclick="closeThread(event); return false;">{_e(labels.THREAD_BACK)}</a>')
    out.append('<div class="deep-title-block">'
               f'<p class="deep-eyebrow">{_e(labels.NAV_FOLLOWING)}</p>'
               f'<h1 class="deep-title">{_e(topic)}</h1></div>')

    # Where this stands — the standing state (no last-delta; the timeline carries
    # deltas). _thread_state_card(t) is the wired call (grep-proof, ENGINEERING).
    state = memory_core.latest_state(con, tid)
    t = {"topic": topic,
         "state_text": (state or {}).get("state_text", ""),
         "state_as_of": (state or {}).get("as_of_date", ""),
         "last_delta": None}
    card = _thread_state_card(t)
    state_body = card or f'<p class="empty-note">{_e(labels.THREAD_NO_STATE)}</p>'
    out.append(f'<div class="deep-section" id="{anchor}-state">'
               f'<h2 class="deep-section-label">{_e(labels.WHERE_THIS_STANDS)}</h2>'
               f'{state_body}</div>')

    # The story so far — full ledger; day-one (no ledger) is an honest empty
    # state, never a fabricated arc (the kill-test law).
    ledger = memory_core.ledger_for_thread(con, tid)
    if ledger:
        out.append(_thread_timeline_html(con, tid, anchor))
        out.append(_thread_editions_html(con, ledger, anchor))
    else:
        out.append(f'<div class="deep-section" id="{anchor}-timeline">'
                   f'<h2 class="deep-section-label">{_e(labels.THE_STORY_SO_FAR)}</h2>'
                   f'<p class="empty-note">{_e(labels.THREAD_NO_ARC)}</p></div>')

    out.append(_thread_verbs_html(topic, mrow["principal_note"] or "", status))
    out.append("</section>")
    return "".join(out)


def _collect_thread_pages(con: sqlite3.Connection) -> str:
    """A thread page per memory row (every status) — the Following rows' name-as-
    action targets one of these sibling views by id (view-thread-<id>)."""
    rows = con.execute("SELECT * FROM memory ORDER BY id").fetchall()
    return "".join(_render_thread_page(con, r) for r in rows)


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
        # Rook's loop mitigation (NL-63, 2026-07-10): a P-only claim is OUR
        # prior coverage — say so, never launder a prior edition into an
        # outlet name. P earns no corroboration; this is the honest label.
        return "(per our prior coverage)"
    return f"({names})"


def _is_watch_label(label: str) -> bool:
    """A forward-looking beat label ('Watch for', 'What could follow', 'What to
    watch') — the seam NL-68 item 4's stale-date guard applies to. Matched by
    keyword so a copy re-pin of the label doesn't silently unwire the guard."""
    low = (label or "").lower()
    return "watch" in low or "could follow" in low or "what's next" in low


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


def _open_watch_prose(watch: List[Dict], edition_date: str = "") -> str:
    """All watch observables as one closing forward-calendar paragraph
    (register spec D2/D3): observables in contract order, `settles` never
    rendered (it is a join key, not reading material). No lead-in label and no
    unknowns-flavored opener (D4). NL-68 item 4: an observable whose only
    forward date is already past relative to the edition is stripped (the same
    guard the Today 'Watch for' beat uses); an observable that goes entirely
    stale is dropped."""
    guard = _is_calendar_date(edition_date)
    sents = []
    for w in watch:
        obs = (w.get("observable", "") or "").strip()
        if not obs:
            continue
        if guard:
            obs, _stale = analysis.strip_stale_watch(obs, edition_date)
            if not obs.strip():
                continue
        sents.append(_glue_sentence(obs))
    return " ".join(sents)


def _deep_timeline_html(con, slot: Optional[Dict], date: str,
                        anchor: str) -> str:
    """NL-63 item 5: the deep view's flagship 'story so far' — a deterministic
    render of the thread's ledger (dated entries, edition-linked via the
    calendar-guarded openEdition pattern from NL-60). Never-re-lede: it ends
    BEFORE today (today is the page you're already on — retro-mock §4). No LLM."""
    if con is None or not slot or not _is_calendar_date(date):
        return ""
    from . import memory_core
    for topic in slot.get("matched_memory") or []:
        tid = memory_core.resolve_thread_id(con, topic)
        if tid is None:
            continue
        rows = memory_core.ledger_for_thread(con, tid, before_date=date)
        if not rows:
            continue
        have_edition = {r["date"] for r in con.execute(
            "SELECT date FROM briefings")}
        items = []
        by_id = {e.get("id"): e for e in rows}
        for e in rows:
            d = e["edition_date"]
            hd = memory_core.human_date(d)
            if _is_calendar_date(d) and d in have_edition:
                date_html = (
                    f'<a class="tl-date-link" href={_e_attr("/?date=" + d)} '
                    f'onclick="return openEdition(\'{_e(d)}\', event)">{_e(hd)}</a>')
            else:
                date_html = f'<span class="tl-date">{_e(hd)}</span>'
            signif = (f' <span class="tl-signif">— {_e(e["significance"])}</span>'
                      if e.get("significance") else "")
            # D1: the 0012 read-side contract on the deep view's story-so-far —
            # a superseded prior delta renders struck/annotated here too.
            sup_class, sup_note = _superseded_li_marks(
                con, by_id, e.get("superseded_by"))
            what = (f'<s class="tl-struck">{_e(e["what_happened"])}</s>'
                    if sup_class else _e(e["what_happened"]))
            items.append(
                f'<li class="tl-entry{sup_class}">{date_html} — '
                f'{what}{signif}{sup_note}</li>')
        return (f'<div class="deep-section deep-timeline" id="{anchor}-timeline">'
                f'<h2 class="deep-section-label">{_e(labels.THE_STORY_SO_FAR)}</h2>'
                f'<ul class="deep-timeline-list">{"".join(items)}</ul></div>')
    return ""


_NUMBER_RE = re.compile(r"\d")


def _deep_numbers_subgroup(brief: Dict, story_anchor: str,
                           src_by_key: Dict[str, Dict]) -> str:
    """NL-29 consolidation slate (DECISIONS 2026-07-14 'NL-29 RULED: the
    consolidation slate', Merge 2 — CoS interpretation, flagged for the
    principal's veto at NL-68): the verified-specifics run FOLDS INTO 'The
    facts' as a sub-group rather than standing as its own 'The numbers'
    section. It carries the numeric LEDGER claims the facts slice didn't
    previously show (Decision B's specifics — non-discrepancy ledger claims
    that carry a figure), each as its FULL statement (never a decontextualized
    bare number — extracting a figure out of its sentence would be a new claim,
    which the two-lane source rule forbids) with the same quiet cite-fold
    attribution the facts use. Pinned facts already render in the facts list
    above, so they are NOT duplicated here (the de-dup that the fold makes
    visible; the old standalone section double-showed them — flagged in the
    report). Zero LLM, zero schema change. Gated on content: no numeric ledger
    claims -> no sub-group, no dead anchor. Returns the bare <ul> (byte-for-byte
    the same rows the retired 'The numbers' section rendered), for placement
    INSIDE the facts .deep-section."""
    items = []
    for e in brief.get("ledger", []):
        if e.get("discrepancy"):
            continue                      # contested figures live in What's still open
        text = e.get("claim", "")
        if _NUMBER_RE.search(text or ""):
            q = _cite_qualifier(e.get("cites", []), src_by_key,
                                e.get("provenance", "")
                                or compute_prov_display(e.get("cites", []), src_by_key))
            items.append(f'<li>{_e(text)} '
                         + _cite_fold(q, "Show sources for this figure") + '</li>')
    if not items:
        return ""
    return (f'<ul class="deep-facts-list deep-numbers-list">'
            f'{"".join(items)}</ul>')


def _deep_discrepancy_subgroup(brief: Dict, src_by_key: Dict[str, Dict]) -> str:
    """NL-29 consolidation slate (DECISIONS 2026-07-14, Merge 1): the discrepancy
    register FOLDS INTO 'What's still open' as an ATTRIBUTED sub-group. Each entry
    is the two sides the record reports, EACH attributed, plus the record's note.

    NL-68 item 5 (his read: 'mostly noise' — matches the CoS 12-entry scan):
      * RAISE THE BAR — a row survives only if it is a SUBSTANTIVE contested
        FIGURE/claim. Same-referent figure pairs (a number and its paraphrase/
        rounding restatement) are not a contradiction and are dropped, reusing
        analysis' same-referent machinery. Disclosed bar: '20%' vs 'about 20
        percent' drops; '20% closed' vs '20% open' and 'fully closed' vs 'not
        fully closed' (the live 07-14 row) survive. (Same-referent DATE pairs are
        already dropped upstream at generation — validate_brief's Editor F2 rule —
        so the bar here is the new FIGURE class, not a re-application.)
      * COLLAPSE BY DEFAULT — the surviving rows render inside a native, keyboard-
        operable <details> (closed), the count carried in the summary's
        accessible name. Removal stays one ruling away (the principal's veto).
    Display-only; no LLM. Gated on content (no substantive discrepancy -> no
    sub-group, no fold). For placement INSIDE the open .deep-section."""
    rows = []
    for e in brief.get("ledger", []):
        if not e.get("discrepancy"):
            continue
        a, b = e.get("a") or {}, e.get("b") or {}
        a_val, b_val = str(a.get("value", "")), str(b.get("value", ""))
        # Raise the bar: drop same-referent figure restatements (paraphrase/round).
        if analysis.same_referent_numbers(a_val, b_val):
            continue
        # D1 (M3 gate): non-str treated as absent, never str()-ed — a dict
        # repr is not disclosure; historical rows bypass the validator-side
        # typing, so both surfaces guard.
        raw_note = e.get("note")
        note = raw_note.strip() if isinstance(raw_note, str) else ""
        note_html = (f'<p class="deep-unresolved-note">{_e(note)}</p>'
                     if note else "")
        rows.append(
            '<div class="deep-unresolved-row">'
            f'<p class="deep-unresolved-side">{_e(a_val)} '
            f'<span class="cite">{_e(_cite_qualifier(_cites_list(a), src_by_key))}'
            '</span></p>'
            '<p class="deep-unresolved-vs" aria-hidden="true">vs</p>'
            f'<p class="deep-unresolved-side">{_e(b_val)} '
            f'<span class="cite">{_e(_cite_qualifier(_cites_list(b), src_by_key))}'
            '</span></p>'
            f'{note_html}</div>')
    if not rows:
        return ""
    n = len(rows)
    noun = labels.DISCREPANCY_FOLD_ONE if n == 1 else labels.DISCREPANCY_FOLD
    return (f'<details class="deep-open-discrepancies">'
            f'<summary><span class="caret" aria-hidden="true">▸</span> '
            f'<span class="disc-count">{n} {_e(noun)}</span></summary>'
            + "".join(rows) + "</details>")


def _render_deep_view(story_anchor: str, headline: str, doc: Dict,
                      date: str, back_label: Optional[str] = None,
                      return_view: str = "view-today", con=None,
                      slot: Optional[Dict] = None,
                      story: Optional[Dict] = None) -> str:
    """The reader rendering — v6-as-edited is the spec. One artifact, two
    renderings (§5.3): this template never re-composes, never re-ledes;
    'cited' never 'verified'; notes_for_writer never renders. NL-11: back-link
    returns to `return_view` (Today by default; an archive-in-place edition
    passes its own view) and `story_anchor` may be slug-prefixed so archive
    editions never collide with Today's deep-view ids."""
    # Gate FIX-2 (v7-M2): the label resolves at CALL time — a def-time default
    # captures the import-time value and breaks the table's re-pin contract.
    back_label = labels.BACK_TO_TODAY if back_label is None else back_label
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
        verdict = {"advances": labels.ARC_ADVANCES,
                   "reverses": labels.ARC_REVERSES,
                   "merely-matches": labels.ARC_MATCHES}.get(
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
        # NL-63: the two-clause significance shape, legacy what_changed fallback.
        arc_body = (arc.get("significance") or arc.get("what_changed")
                    or arc.get("what_happened") or "")
        arc_line = (f'<p class="deep-arc-line">'
                    f'<span class="deep-arc-verdict">{_e(verdict)}</span> — '
                    f'{_e(arc_body)}{cite_html}</p>')
    out.append(f'<div class="deep-title-block"><p class="deep-eyebrow">'
               f'{_e(labels.DEEP_EYEBROW)}</p>'
               f'<h1 class="deep-title">{_e(headline)}</h1>'
               f'{arc_line}</div>')

    # NL-68 item 3 (THE SUPERSET LAW): open with the story's OWN Today prose
    # (lede + why-it-matters + watch-for) before any analyst section, so the deep
    # view carries at least everything the Today story showed, plus more.
    prose_block = _deep_today_prose(story or {}, date)
    if prose_block:
        out.append(prose_block)

    # NL-63 item 5: the "story so far" timeline — the deep view's flagship
    # section, deterministic from the ledger, sitting under the title block.
    timeline_html = _deep_timeline_html(con, slot, date, story_anchor)
    if timeline_html:
        out.append(timeline_html)

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
    open_watch_para = _open_watch_prose(brief.get("watch", []), date)
    if open_watch_para:
        open_paras.append(f'<p>{_e(open_watch_para)}</p>')

    # NL-29 consolidation slate (DECISIONS 2026-07-14): FIVE reader sections —
    # The facts (specifics fold in) · How this works · What could follow ·
    # What's still open (discrepancies fold in) · Sources. Facts / How this
    # works / Sources always render; "What could follow" and "What's still open"
    # emit only with content, and no dead jumplist anchor otherwise (M7
    # precedent). The specifics sub-group and the discrepancy sub-group are
    # computed here — before the jumplist — so the "What's still open" anchor
    # gates on the SAME rendered content (open prose OR a discrepancy sub-group):
    # a bare open well with only discrepancies still earns its section+anchor,
    # and an empty one emits neither. The retired 'The numbers'/'Unresolved'
    # sections and their story-*-numbers/-unresolved anchors are GONE.
    numbers_sub = _deep_numbers_subgroup(brief, story_anchor, src_by_key)
    disc_sub = _deep_discrepancy_subgroup(brief, src_by_key)
    open_has_content = bool(open_paras or disc_sub)

    jump_items = [("facts", labels.JUMP_FACTS)]
    jump_items.append(("mechanism", labels.DEEP_MECHANISM))
    if brief.get("effects"):
        jump_items.append(("effects", labels.DEEP_EFFECTS))
    if open_has_content:
        jump_items.append(("open", labels.JUMP_OPEN))
    jump_items.append(("sources", labels.DEEP_SOURCES))
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
    # NL-29 consolidation (Merge 2, flagged): the verified-specifics run folds in
    # here as a sub-group — the numeric ledger claims the facts slice didn't show
    # (byte-for-byte the rows the retired 'The numbers' section rendered).
    out.append(f'<div class="deep-section" id="{story_anchor}-facts">'
               f'<h2 class="deep-section-label">{_e(labels.DEEP_FACTS)}</h2>'
               f'<ul class="deep-facts-list">{"".join(lis)}</ul>'
               f'{numbers_sub}</div>')

    # 2. How this works — inline [S#] keys become the SAME quiet citation fold the
    # facts use (NL-58 parity ruling): the qualifier reveals on tap behind a
    # caret, not inline as plain text. (NL-29: WAS 'Mechanism'; the label is the
    # one-string re-pin, the anchor id story-*-mechanism is unchanged so the
    # jumplist stays live.)
    mech = brief.get("mechanism", "")
    mech_display = re.sub(
        r"\s*\[([SCRP]\d+(?:,\s*[SCRP]\d+)*)\]",
        lambda m: " " + _cite_fold(_cite_qualifier(
            [k.strip() for k in m.group(1).split(",")], src_by_key),
            "Show sources for this claim"),
        _e(mech))
    out.append(f'<div class="deep-section" id="{story_anchor}-mechanism">'
               f'<h2 class="deep-section-label">{_e(labels.DEEP_MECHANISM)}</h2>'
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
                   f'<h2 class="deep-section-label">{_e(labels.DEEP_EFFECTS)}</h2>'
                   + "".join(effs) + "</div>")

    # 4. What's still open — Honest Unknowns + Watch For fused at section level
    # (register spec, 2026-07-09 addendum). One register end to end: editor's-
    # memo prose, body ink, body size. Unknowns lead as one paragraph each
    # (three sentence-roles, no beats/labels/tails); one closing paragraph
    # carries the watch observables; `settles` never renders. NL-29 consolidation
    # (Merge 1): the discrepancy register folds in below the prose as a visually
    # distinct attributed sub-group (byte-for-byte the rows the retired
    # 'Unresolved' section rendered). Absent halves leave no residue (D4) —
    # both precomputed above; emit only if prose OR a discrepancy sub-group
    # survives (the anchor gates on the same open_has_content signal).
    if open_has_content:
        out.append(f'<div class="deep-section" id="{story_anchor}-open">'
                   f'<h2 class="deep-section-label">{_e(labels.DEEP_OPEN)}</h2>'
                   + "".join(open_paras) + disc_sub + "</div>")

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
               f'<h2 class="deep-section-label">{_e(labels.DEEP_SOURCES)}</h2>'
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


def _sources_context_source_rows(con, slot: Dict) -> List[str]:
    """NL-66(b): the In-Brief slot's source list, resolved FROM persisted rows.
    Primary path — item_ids -> source_items (outlet + linked title), in slot
    order. Fallback — bare outlet rows from slot['outlets'] when nothing
    resolves (a slot whose items were pruned). Honest empty -> [] (the caller
    renders the empty note; never a fabricated source)."""
    rows: List[str] = []
    item_ids = [i for i in (slot.get("item_ids") or []) if isinstance(i, int)]
    if con is not None and item_ids:
        qs = ",".join("?" * len(item_ids))
        by_id = {r["id"]: r for r in con.execute(
            f"SELECT id, outlet, title, url FROM source_items WHERE id IN ({qs})",
            item_ids)}
        for iid in item_ids:                        # preserve slot order
            r = by_id.get(iid)
            if r is None:
                continue
            title = _e(r["title"] or "(untitled)")
            link = (f'<a href={_e_attr(r["url"])}>{title}</a>'
                    if r["url"] else title)
            rows.append('<div class="deep-source-row">'
                        f'<p class="source-outlet">{_e(r["outlet"] or "")}</p>'
                        f'<p class="source-title">{link}</p></div>')
    if rows:
        return rows
    for o in slot.get("outlets") or []:             # fallback: outlets only
        rows.append('<div class="deep-source-row">'
                    f'<p class="source-outlet">{_e(o)}</p></div>')
    return rows


def _render_sources_context_view(story_anchor: str, headline: str, st: Dict,
                                 slot: Dict, con, date: str,
                                 back_label: Optional[str] = None,
                                 return_view: str = "view-today") -> str:
    """NL-66(b) ruled option (b): the In-Brief (quick-tier) deep view — a $0
    sources-and-context surface built ENTIRELY from what already exists for the
    slot, honestly labeled. It is NOT the analyst tier: no generation, no model
    call, and no 'cited, not verified' analyst trust footer — surfacing that
    line here would misrepresent an unanalyzed item as analyzed. It shows the
    slot summary, the source list (item_ids -> source_items), the matched
    tags/threads, and the 'Here for' rationale. Missing inputs render an honest
    empty state (the NL-11 missing-input class), never a fabricated source."""
    back_label = labels.BACK_TO_TODAY if back_label is None else back_label
    out = [f'<section id="view-deep-{story_anchor}" class="view">']
    ret = "" if return_view == "view-today" else f", '{_e(return_view)}'"
    out.append(f'<a class="deep-back" href="#" onclick="closeDeepView(event{ret})">'
               f'{_e(back_label)}</a>')
    out.append('<div class="deep-title-block">'
               f'<p class="deep-eyebrow">{_e(labels.SOURCES_CONTEXT)}</p>'
               f'<h1 class="deep-title">{_e(headline)}</h1></div>')

    # NL-68 item 3 (superset): open with the story's Today blurb — the SAME text
    # the In-Brief snippet shows (st.lede), so the sources-&-context view is never
    # thinner than the Today card. Falls back to the ranker summary only when the
    # narrative lede is absent.
    summary = (st.get("lede") or slot.get("summary") or "").strip()
    if summary:
        out.append(f'<div class="deep-section" id="{story_anchor}-summary">'
                   f'<h2 class="deep-section-label">{_e(labels.IN_BRIEF)}</h2>'
                   f'<p>{_e(summary)}</p></div>')

    # why-you're-seeing-this — matched topics, tracked threads, and the shared
    # 'Here for' rationale (the same code path as Today's meta-footnote)
    tags = [t.get("name", "") for t in slot.get("matched_tags") or []
            if isinstance(t, dict) and t.get("name")]
    threads = [m for m in slot.get("matched_memory") or [] if m]
    ctx = []
    if tags:
        ctx.append('<p class="sc-tags">Matched topics: '
                   f'{_e(", ".join(tags))}</p>')
    if threads:
        ctx.append('<p class="sc-threads">Tracked threads: '
                   f'{_e(", ".join(threads))}</p>')
    ctx.append(f'<p class="sc-herefor">Here for: {_e(_here_for(slot))}.</p>')
    out.append(f'<div class="deep-section" id="{story_anchor}-context">'
               f'<h2 class="deep-section-label">{_e(labels.DEEP_WHY_SEEING)}</h2>'
               + "".join(ctx) + "</div>")

    # sources — outlets/corroboration label; honest empty when none resolve
    src_rows = _sources_context_source_rows(con, slot)
    corrob = (slot.get("corroboration_label") or "").strip()
    corrob_html = f'<p class="sc-corrob">{_e(corrob)}</p>' if corrob else ""
    body = corrob_html + ("".join(src_rows) if src_rows else
                          '<p class="empty-note">No sources are recorded for '
                          'this In-Brief item.</p>')
    out.append(f'<div class="deep-section" id="{story_anchor}-sources">'
               f'<h2 class="deep-section-label">{_e(labels.DEEP_SOURCES)}</h2>'
               + body + "</div>")

    # honest footer — this is context, NOT the analyst report (no trust-line
    # borrowing; the two-lane distinction is the whole point of NL-66(b)). NL-68
    # item 14: the interface-narration ("This is the sources-and-context view
    # for an In-Brief item") is trimmed; the load-bearing HONESTY disclosure —
    # this is NOT a full-picture analysis — stays (boundary: disclosures live).
    out.append('<div class="deep-footer"><p>Sources and context already '
               'collected — not a full-picture analysis.</p></div>')
    out.append("</section>")
    return "".join(out)


def _collect_deep_views(con: sqlite3.Connection, row, entry: Optional[Dict],
                        slug_prefix: str, back_label: str,
                        return_view: str) -> Tuple[Dict[int, Dict], List[str]]:
    """Newest-valid-wins brief reads for one edition (M9-M3); renders FROM the
    persisted row, never regenerates. Returns ({slot_no: doc}, [sections]).
    Shared by Today and the archive-in-place edition (NL-11). NL-66(b): a quick-
    tier In-Brief slot with no analyst brief gets the $0 sources-&-context view
    (a failed full/medium brief stays degraded-hidden — only quick tier does)."""
    briefs: Dict[int, Dict] = {}
    sections: List[str] = []
    from . import analysis as analysis_mod
    stories_probe, _ = _stories_for(row, entry)
    slots = _slots_for(row)
    tiers = (entry or {}).get("tiers") or []
    for i, st in enumerate(stories_probe):
        slot = slots[i] if i < len(slots) else None
        # A still-tracking slot renders as a status strip on Today, not a story,
        # so it gets NO deep view — skip it here to match _render_briefing_body
        # (index preserved so every other slot keeps its story-{i} anchor).
        if slot and slot.get("still_tracking"):
            continue
        # tier derivation MATCHES _render_briefing_body's so the entry link and
        # the collected view agree for every slot (no link without a view).
        tier = tiers[i] if i < len(tiers) else (
            "full" if i == 0 else "medium" if i <= 2 else "quick")
        doc = analysis_mod.latest_valid_brief(con, row["date"], i + 1)
        if doc and doc.get("brief"):
            briefs[i + 1] = doc
            sections.append(_render_deep_view(
                f"{slug_prefix}story-{i}", st.get("headline", ""), doc,
                row["date"], back_label=back_label, return_view=return_view,
                con=con, slot=slot, story=st))
        elif tier == "quick":
            sections.append(_render_sources_context_view(
                f"{slug_prefix}story-{i}", st.get("headline", ""), st,
                slot or {}, con, row["date"], back_label=back_label,
                return_view=return_view))
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
                f'{_e(labels.BACK_TO_ARCHIVE)}</a>'
                '<p class="empty-note">That edition is unavailable.</p>'
                '</section>', None)
    entry = _log_entry_for(row["date"])
    slug_prefix = f"ed{date}-"
    briefs, deep_sections = _collect_deep_views(
        con, row, entry, slug_prefix, labels.BACK_TO_EDITION, "view-edition")
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
            f'{_e(labels.BACK_TO_ARCHIVE)}</a>'
            f'<h1 class="view-title">{_e(_human_date(row["date"]))}</h1>'
            f'{episode}{body}</section>')
    return head + "".join(deep_sections), row["date"]


def _nl_labels_js() -> str:
    """The client-facing label subset (item 5): the follow-control copy the JS
    renders, injected as window.NL_LABELS so a labels.py re-pin lands in the
    client too — the same one-place re-pin the server renders enjoy. <>&-escaped
    so a re-pin can never break out of the <script> element."""
    payload = {"followActive": labels.FOLLOW_STORY_ACTIVE,
               "followInactive": labels.FOLLOW_STORY_INACTIVE,
               "followConfirm": labels.FOLLOW_STORY_CONFIRM}
    blob = (json.dumps(payload, ensure_ascii=False)
            .replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026"))
    return "window.NL_LABELS = " + blob + ";"


def _staleness_banner_html() -> str:
    """The prominent staleness banner (item 1): shown on every view when the
    running code predates disk. Reading is fine — the enforcement (the refusal)
    is in _api_generate; this only tells the reader why generation is paused and
    how to fix it. Empty string when fresh or when the guard is disabled."""
    if not _server_is_stale():
        return ""
    return (
        '<div class="staleness-banner" role="alert">'
        f'<strong>{_e(labels.STALENESS_BANNER_TITLE)}</strong> '
        f'{_e(labels.STALENESS_BANNER_BODY)} '
        '<code>newslens serve</code></div>')


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

    # v7: the masthead (dateline + dispatch strip + edition-bar player) is
    # rendered INTO the Today view by _render_today/_masthead — no shared top-bar
    # date label or top-level episode player anymore (DIRECTION-v5 §4).

    # M9-M3: newest-valid-wins brief reads; the view renders FROM the
    # persisted row (never regenerates); date-addressed like briefings.
    briefs: Dict[int, Dict] = {}
    deep_sections: List[str] = []
    if row is not None and gen_state["state"] != "running":
        briefs, deep_sections = _collect_deep_views(
            con, row, entry, "", labels.BACK_TO_TODAY, "view-today")

    page = webui.PAGE.format(
        css=webui.CSS,
        staleness_banner=_staleness_banner_html(),
        today_html=_render_today(con, row, entry, gen_state, briefs=briefs),
        following_html=_render_following(con),
        archive_html=_render_archive(con),
        settings_html=_render_settings(con, row, entry),
        popups_html=webui.POPUPS,
        deep_views_html="".join(deep_sections),
        thread_pages_html=_collect_thread_pages(con),
        nl_labels_js=_nl_labels_js(),
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
        # The teeth of the staleness guard (2026-07-16 incident): writing an
        # edition with stale code is the failure this batch exists to stop.
        # Refuse the trigger when the running code predates disk — reading is
        # still served, and the banner explains the one-line restart.
        if _server_is_stale():
            return self._send_json(
                {"ok": False, "error": labels.STALENESS_REFUSAL}, 409)
        started = GEN_JOB.start()
        self._send_json({"ok": True,
                         "detail": "started" if started else "already running"})


def serve(port: int = DEFAULT_PORT) -> int:
    db.migrate()
    _stamp_startup_identity()  # freeze this process's code identity (item 1)
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
