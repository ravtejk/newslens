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

from . import (analysis, catalog, commissioning, config, db, entities, events,
               follow_altitude, labels, memory, paths, schedule, webui)
# `schedule` is imported at module level and imports THIS module only inside its
# functions — the dependency runs one way (web reads the scheduler's names; the
# scheduler reads the reader's predicates at call time), so nothing here can
# invert into an import cycle.

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
        # NL-88: the live stage the generate thread is in RIGHT NOW. Written by
        # _progress (the callback run_generate fires at each phase boundary),
        # read by snapshot(), BOTH under self.lock — the server thread reads
        # while the generate thread writes, so this MUST stay lock-guarded.
        self.stage: Optional[str] = None
        self.stage_model: Optional[str] = None
        self.stage_started_at: Optional[str] = None
        # NL-149 item 1 (his charter, 2026-08-12): the steps that have FINISHED,
        # in the order they finished, each keeping the time it took. The panel
        # adds a line instead of replacing one, so a ~40-minute run reads as a
        # log of what happened rather than a single sentence that keeps changing.
        #
        # APPEND-ONLY IS THE LOAD-BEARING PROPERTY, and it lives HERE rather than
        # in the client: this list only ever grows, and an entry is never
        # rewritten once its elapsed is stamped. That is what makes "a line that
        # appeared never disappears and never changes" true across a page
        # reload, a slow poll, and a stage shorter than the 2.5s poll interval —
        # none of which a client that diffed labels could survive.
        self.steps: List[Dict[str, object]] = []

    def start(self) -> bool:
        with self.lock:
            if self.state == "running":
                return False
            self.state = "running"
            self.error = ""
            self.started_at = datetime.now(timezone.utc).isoformat()
            self.stage = None
            self.stage_model = None
            self.stage_started_at = None
            # A NEW RUN STARTS A NEW LOG. The previous run's finished steps are
            # not this run's; they live on in generation_log.jsonl, which is
            # where the settings report reads them from (item 2).
            self.steps = []
        # NL-146 fix loop 1 (QA F-2) — CLAIM THE MACHINE'S GENERATION SLOT.
        #
        # This lock guards THIS PROCESS. A launchd fire is another one, so
        # without a claim on disk the two 30-minute pipelines that must never
        # overlap are exactly the two that nothing was stopping. Claimed here
        # rather than inside `_run` so the slot is held before `start()` returns
        # True — a marker written on the worker thread leaves a window in which
        # the door has already answered "started" and the disk still says idle.
        #
        # A LOST RACE IS NOT AN ERROR, it is the guard working: unwind the state
        # this method just set and answer False, which the door already renders
        # as "already running" (with the reason, from the door).
        if schedule.claim_in_flight(
                datetime.now().strftime("%Y-%m-%d"),
                schedule.TRIGGER_INTERACTIVE) is not None:
            with self.lock:
                self.state = "idle"
                self.started_at = None
            return False
        try:
            threading.Thread(target=self._run, daemon=True).start()
        except BaseException:
            # A thread that never started holds nothing. Releasing here is what
            # keeps a failed spawn from wedging the slot until the age ceiling.
            schedule.release_in_flight()
            with self.lock:
                self.state = "idle"
                self.started_at = None
            raise
        return True

    def _progress(self, label: str, model: Optional[str]) -> None:
        # NL-88: the progress callback handed to run_generate. Fires on the
        # GENERATE thread at each phase boundary; takes the SAME lock snapshot()
        # reads under, so a mid-run status read never tears. run_generate wraps
        # this in a swallow (_emit_progress), so even a raise here can never
        # touch the generation — but it must not raise anyway (a stage stamp is
        # a couple of assignments under a short-held lock).
        now = datetime.now(timezone.utc).isoformat()
        with self.lock:
            # NL-149 item 1: a new boundary means the previous stage FINISHED —
            # close it into the log before opening the next one.
            self._close_stage_locked(now)
            self.stage = label
            self.stage_model = model
            self.stage_started_at = now

    def _run(self) -> None:
        try:
            from . import generate, schedule
            config.load_env()
            # NL-146: this job exists because he pressed the button — the second
            # caller for which the word "interactive" is literally true.
            generate.run_generate(progress=self._progress,
                                  trigger=schedule.TRIGGER_INTERACTIVE)
            with self.lock:
                self.state = "done"
                self._clear_stage_locked(completed=True)
        except Exception as exc:  # surfaced verbatim in the FOUNDER's panel
            # C1 fix loop 1 (QA-6): the founding page now renders this sentence
            # only when it matches a reader-safe form (commissioning.
            # unfit_for_readers), so on a stranger's first run it is usually
            # omitted — a model id, a spend figure or a credential error must
            # not be a stranger's first screen. Omitted is not lost: the
            # operator grade of the same fact goes to the serve terminal, which
            # is where it was always the right grade of prose.
            print(f"generate failed: {exc}", flush=True)
            with self.lock:
                self.state = "error"
                self.error = str(exc)
                self._clear_stage_locked()
        finally:
            # NL-146 fix loop 1 (QA F-2): the slot goes back the moment this
            # pipeline stops, on EVERY exit — done, error, or a BaseException
            # that skips the except above. A marker outliving its run is the
            # failure mode that would wedge his Generate button, so its release
            # sits in the same `finally` that already exists to keep the state
            # honest for exactly that class of exit.
            from . import schedule as _schedule
            _schedule.release_in_flight()
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
                    self._clear_stage_locked()

    def _close_stage_locked(self, now_iso: Optional[str] = None) -> None:
        """Caller holds self.lock. Move the open stage into the finished log with
        the time it took. No open stage (the first boundary of a run, or a second
        close) is a no-op, which is what keeps this idempotent.

        FLOORED to 0.1s by the SAME rule as generate._wall_seconds_since (see
        that docstring for why floor and not round — NL-149 QA F-2). These are
        two independent measurements of the same stage: this one from the
        server's own boundary stamps, the other from generate's. Once both
        stamps carry sub-second precision the two agree to microseconds, and a
        shared rounding rule is what stops that agreement from being thrown away
        at the last step — the finding was the live panel and the saved report
        disagreeing about the same step. The floor runs on exact microseconds,
        not on `total_seconds() * 10`, which is a float."""
        if not self.stage:
            return
        started = self.stage_started_at
        elapsed = None
        if started:
            try:
                end = (datetime.fromisoformat(now_iso) if now_iso
                       else datetime.now(timezone.utc))
                elapsed = max(0.0, ((end - datetime.fromisoformat(started))
                                    // timedelta(seconds=0.1)) / 10)
            except Exception:  # never let a clock read break a running job
                elapsed = None
        self.steps.append({"label": self.stage, "model": self.stage_model,
                           "elapsed_s": elapsed})

    def _clear_stage_locked(self, completed: bool = False) -> None:
        # Caller holds self.lock. Terminal states carry no live stage.
        #
        # NL-149 item 1: `completed` says whether the stage that was open got to
        # FINISH. On the done path it did, so it earns its line in the log. On
        # the error paths it did not — a stage that raised mid-way is not a
        # completed step, and writing it as one would put a duration on screen
        # for work that never landed. (The failed run still keeps its full
        # timeline in generation_log.jsonl, where the settings report reads it —
        # that record is generate.py's, written on both arms.)
        if completed:
            self._close_stage_locked()
        self.stage = None
        self.stage_model = None
        self.stage_started_at = None

    def snapshot(self) -> Dict[str, object]:
        with self.lock:
            now = datetime.now(timezone.utc)

            def _elapsed(iso: Optional[str]) -> Optional[float]:
                if not iso:
                    return None
                try:
                    return round((now - datetime.fromisoformat(iso)).total_seconds(), 1)
                except Exception:  # never let a clock read break the status endpoint
                    return None

            return {
                "state": self.state,
                "error": self.error,
                "started_at": self.started_at,
                "stage": self.stage,
                "stage_model": self.stage_model,
                "stage_elapsed_s": _elapsed(self.stage_started_at),
                "total_elapsed_s": _elapsed(self.started_at),
                # NL-149 item 1: COPIES, not the list itself — a caller (the
                # status endpoint, a render) must not be able to reach into the
                # job's state, and json.dumps of a live list read on another
                # thread is exactly the tear the lock exists to prevent.
                "steps": [dict(s) for s in self.steps],
            }


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
    """Last generation_log entry for a date wins (regenerations append).

    Reads with `errors="replace"` — see the TORN-APPEND note above
    `_run_log_entries`. This is the OLDER of the file's two page-path readers
    and the one that fires on the founder's profile (an edition row always
    exists there), so a torn tail here has been answering `GET /` and
    `GET /archive` with HTTP 500 since long before NL-149. Fixed with the
    reader NL-149 added because one site is not closure (NL-149 QA F-1).

    THE `schedule` FILTER IS THE SAME LOAD-BEARING FILTER `_run_log_entries`
    CARRIES, and it is here because NL-146 QA F-1 measured what its absence
    costs. A fire-decision line is a decision ABOUT a run, never a run: it
    carries `date`, carries no `sample`, and — unlike the analysis stage's line,
    which lands BEFORE the run entry and so never won last-wins — it is the
    first writer in this file that lands AFTER it. So from the moment a ladder
    records any decision, the fire line WAS "the run entry for the date"
    everywhere this function feeds, and all three consequences were measured on
    the build tree: the scheduled-failure note never rendered in the real
    exhaustion flow (`_scheduled_failure_note` keys on `trigger`/`status`, both
    absent on a fire line), every scheduled SUCCESS degraded to the legacy
    narrative parse because the structured `stories` were gone, and
    `schedule._charged_for` read $0.00 past a run entry recording $1.23 — an
    unattended-spend guard reading nothing.

    Filtering by KEY PRESENCE and not by guessing at the line's shape is the
    file's own idiom (`stage`, then `schedule`); a fourth line class added to
    this log gets a third `continue` here on the day it is written.

    NL-154 — THE ARCHIVE FALLBACK, and why the ORDER is live-first. This reader
    feeds the edition renderer for ANY date, including one deep in the archive
    screen, so a date whose entry has rotated out of the live segment must not
    lose its structured `stories` and degrade to the legacy narrative parse.
    But the overwhelmingly common call is for a RECENT date, which is always
    live — so the live segment is searched first and the archives are touched
    only on a miss. The steady-state cost is unchanged; the fallback is paid
    exactly by the reader who opened a months-old edition.

    Segments are searched NEWEST FIRST and the first hit wins, which preserves
    the function's own last-entry-wins rule across the split: within a segment
    the last match is taken, and a later segment's match always supersedes an
    earlier one's.
    """
    from . import generate

    def _scan(path: Path) -> Optional[Dict]:
        found = None
        try:
            if not path.exists():
                return None
            for line in path.read_text(encoding="utf-8",
                                       errors="replace").splitlines():
                if not line.strip():
                    continue
                try:
                    e = json.loads(line)
                except ValueError:
                    continue
                if e.get(schedule.SCHEDULE_LINE_KEY) is not None:  # NL-146 fires
                    continue
                if e.get("date") == date and not e.get("sample"):
                    found = e
        except (OSError, ValueError):
            return None
        return found

    hit = _scan(generate.log_file())
    if hit is not None:
        return hit
    for seg in reversed(generate.log_archives()):
        hit = _scan(seg)
        if hit is not None:
            return hit
    return None


# NL-149 item 2 — the generation report's source.
#
# THE CHOICE, STATED: the report is a VIEW over data/generation_log.jsonl, not a
# new artifact class. That log is already the per-run record of steps, models and
# money, already append-only, already written on BOTH the ok and the failed arm,
# and already survives the process — everything his charter asks a saved report
# to be. Minting a second file would have created two records of one run that
# can disagree, and a schema change (the DB) would have been a checkpoint. What
# NL-149 added is a field inside the entry that was already being written
# (`stage_timeline`), which is additive jsonl enrichment: every existing reader
# — _log_entry_for, diagnose, the UI's story source — sees the keys it always saw.
_RUNLOG_MAX_ROWS = 30


def _run_log_entries(limit: int = _RUNLOG_MAX_ROWS) -> Tuple[List[Dict], int]:
    """(newest-first RUN entries, total run count) from generation_log.jsonl.

    THREE kinds of line live in that file: RUN entries (one per generate
    attempt, written by generate.log_generation), per-STAGE instrumentation
    entries (the analysis stage writes one, keyed `stage`), and NL-146's
    scheduled-fire DECISION lines (keyed `schedule`). Only runs are reports, so
    the other two are filtered out by the presence of their key rather than by
    guessing at the run shape — 23 of the founder's 49 lines are stage lines,
    and rendering them as runs would double every day's count.

    THE `schedule` FILTER IS LOAD-BEARING, not tidiness. A fire that decided
    "today's edition already exists, do nothing" carries no status, no steps and
    no timeline; rendered as a run it would appear in his report as a failed
    generation that never happened — a quiet no-op turned into a visible alarm,
    which is the opposite of the charter's "does nothing (quietly, logged)".

    Unreadable file -> ([], 0); an unparseable line is skipped, never guessed at.

    TORN APPEND (NL-149 QA F-1, HIGH). This file has TWO appenders —
    generate.log_generation (ensure_ascii=True) and analysis.py's stage line
    (ensure_ascii=False, and the founder's copy already carries 560 non-ASCII
    bytes from it) — and an append is not atomic. A kill, a full disk or a power
    loss between flush chunks leaves a partial multibyte tail, and
    UnicodeDecodeError subclasses ValueError, NOT OSError, so the old
    `except OSError` never saw it. Since this read sits in the UNCONDITIONAL
    part of build_page, that decode error left do_GET answering HTTP 500 on
    every HTML door.

    `errors="replace"` is the load-bearing half, and returning ([], 0) on the
    wider except is NOT a substitute: one bad byte in a 660KB append-only file
    would silently erase the founder's entire generation record from the report,
    which is a worse lie than the crash. Replace keeps every intact line and
    costs exactly the torn one — which then dies at json.loads, where a torn
    line already died. The widened except stays as belt-and-braces for the read
    itself."""
    log = paths.DATA_DIR / "generation_log.jsonl"
    runs: List[Dict] = []
    try:
        text = log.read_text(encoding="utf-8", errors="replace")
    except (OSError, ValueError):
        return [], 0
    for line in text.splitlines():
        if not line.strip():
            continue
        try:
            e = json.loads(line)
        except ValueError:
            continue
        if not isinstance(e, dict) or e.get("stage") is not None:
            continue
        if e.get(schedule.SCHEDULE_LINE_KEY) is not None:   # NL-146 fire lines
            continue
        runs.append(e)
    runs.reverse()                      # newest first — the log appends
    # NL-154 — THE COUNT SPANS THE ARCHIVES, THE ROWS DO NOT.
    #
    # This function reads the LIVE segment only, and that is the whole point of
    # the row: rotation retains 60 runs live against this screen's 30, so the
    # rows it renders are complete without ever touching an archive. The TOTAL
    # is different — it is the number the settings row and the truncation line
    # quote at him ("the 30 most recent of N runs"), and a live-only N would
    # have silently dropped the morning after the first rotation. The archived
    # figure comes from the index generate writes AT rotation, so this stays one
    # bounded read of one file no matter how much history accumulates.
    #
    # An unreadable index contributes 0 — under-reporting history that the
    # segments still hold, never inventing runs that never happened.
    from . import generate
    return runs[:max(0, limit)], len(runs) + generate.archived_run_count()


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
    """Display-local per the addendum; storage stays UTC.

    UNTRUSTED INPUT (NL-149 QA F-4). Every caller feeds this a value read back
    from a record the app does not own the schema of — generation_log.jsonl
    lines, a `generated_at` column, a source's `retrieved_at`. A non-string
    stamp (a hand-edited log, an epoch from a future writer) raised
    AttributeError straight out of _render_run and out of build_page with it,
    which is HTTP 500 on the reports page. The guard now covers the type as
    well as the value, and the fallback is unchanged in kind: an unparseable
    stamp renders AS ITSELF rather than as an invented time."""
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
    except (AttributeError, TypeError, ValueError):
        return str(iso_utc)


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
        # v8-M1 item 5 (2026-07-17): an EMPTY thread — no state, no deltas, no
        # READY baseline — has no content date. The old fallback to `last` (the
        # ref/join date) rendered "LAST UPDATED <date>" off the follow's BIRTH,
        # not any coverage. Detect empty and stamp the honest "FOLLOWED
        # <created_at>" instead. GATE RULED 2026-07-17: a pending/failed
        # baseline is nothing a reader can open (→ FOLLOWED); a ready-baseline-
        # only thread stamps LAST UPDATED off the baseline's OWN as_of_date —
        # the content's date, never the ref/join pickup date (the follow's
        # birth in disguise).
        baseline = memory_core.latest_baseline(con, r["id"])
        ready_baseline = (baseline
                          if (baseline and baseline.get("status") == "ready")
                          else None)
        is_empty = (state is None) and (not ledger) and (ready_baseline is None)
        lu = (ledger[-1]["edition_date"] if ledger else
              (ready_baseline["as_of_date"] if ready_baseline else last))
        last_updated = "" if is_empty else (lu if (lu and lu <= today) else "")
        followed_on = _short_date(r["created_at"]) if is_empty else ""
        grouped.setdefault(r["status"], []).append({
            "id": r["id"],
            "topic": r["topic"],
            # NL-17-M1b: the persisted altitude disclosure (0019) — Kass's law,
            # rendered on every Following surface. Bare '' for unmigrated threads
            # (the honest v1 mix — nothing backfilled).
            "altitude": _row_col(r, "altitude"),
            "disclosure": _row_col(r, "disclosure"),
            "altitude_source": _row_col(r, "altitude_source"),
            # NL-17-M1c: the OTHER rung's name — the acts line's named swap
            # target on this row. Bare '' when nothing settled, and then the
            # "Instead:" prefix does not render at all (never a fabricated
            # candidate).
            "alt_label": _row_col(r, "alt_label"),
            # FIX LOOP 1: what an unconfident settle came back holding (0025's
            # `settled_low` detail). Read HERE because this is the loop that
            # already has the connection open per row; the row-mount renderer
            # has never taken one. [] on a pre-0025 record, which renders as the
            # shipped silence — the ruling's own degenerate case.
            "settle_candidates": memory.settle_candidates(con, r["id"]),
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
            "followed_on": followed_on,
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


def _row_col(r, name: str, default: str = "") -> str:
    """Safe column read from a sqlite3.Row: an older DB that predates a column
    (a real record not yet re-migrated) reads the default rather than raising —
    the same degrade-to-honest posture load_thread_input takes."""
    try:
        return (r[name] if name in r.keys() else default) or default
    except (IndexError, KeyError):
        return default


def _altitude_qualifier_html(row: Dict) -> str:
    """Kass's disclosure, persistent form (mockup-v9 grammar): the altitude
    qualifier appended to a Following row's NAME. entity/ambiguous-storyline ->
    the quiet '(class)' parenthetical; everything else -> BARE (the name states
    its own class, or no altitude exists — never a fabricated qualifier). Words
    only; the .alt-q register (serif 400 ink-soft) carries it — color is never
    the signal.

    NL-17 M1 — THE NARROW ARM IS DELETED. It rendered '— this story' on every
    story-seeded row, and the principal's 2026-08-07 amendment (i) kills that
    phrase outright: the product council's case (a) says "'This story' appears
    nowhere — not as label, not as qualifier, not as rung." This was the
    QUALIFIER seat. A story-seeded thread's row now renders its NAME BARE, which
    is the ruling's own copy ("Following <thread name>", storyline name bare) and
    costs no new string — one was removed, not added. The row is still telling
    the whole truth: a thread that has not settled has no class to state, and
    stating one it does not have was the only reason this arm existed.

    What survives is a BARE narrow branch, and that is not a leftover: a
    story-seeded thread carries no disclosure by construction (the seed writes
    ''), so falling through to the disclosure arm could only ever matter for a
    row holding a STALE one — and rendering that row's old class would claim a
    scope this thread does not have. Bare is the honest answer for narrow
    whatever else the row happens to be carrying."""
    if (row.get("altitude") or "") == "narrow":
        return ""
    disclosure = row.get("disclosure") or ""
    if not disclosure:
        return ""                              # unmigrated — honest bare
    _name, cls = follow_altitude.split_qualifier(disclosure)
    if cls:
        return f' <span class="alt-q">({_e(cls)})</span>'
    return ""                                  # descriptive storyline — bare by grammar


# NL-17-M1c / NL-103 row 3: _altitude_upgrade_line is DELETED. A follow that
# landed story-scoped because the settle did not land is an ORDINARY narrow
# follow — the "— this story" row qualifier is its whole disclosure, and the
# apology door ("Couldn't fetch broader follow — choose it anytime.") went with
# the standing-broaden class his 07-25 ruling killed. Nothing replaces it: there
# is no bare directional verb left anywhere in the product to point at.


def _active_topics_lower(con: sqlite3.Connection) -> set:
    return {r["topic"].lower() for r in con.execute(
        "SELECT topic FROM memory WHERE status = 'active'")}


def _held_topics_lower(con: sqlite3.Connection) -> set:
    """Threads the reader STILL HOLDS — every memory row that is not an explicit
    unfollow. NL-145 (M1 gate ruling R2) reads follow state through this, and it
    is deliberately NOT `_active_topics_lower`.

    THE DEFECT (F-6's reload half). The tracked-ongoing marker is DERIVED FROM
    FOLLOW STATE: it renders because the story matched a thread the reader
    follows. NL-143 made the live page honest — a remote unfollow sweeps the
    marker into a resting CTA — but a RELOAD re-renders it from the stored
    slot's `matched_memory`, which is an EDITION RECORD and knows nothing about
    an unfollow that happened afterwards. So the marker came back from the dead
    and, worse, came back INSTEAD of the follow control (his 2026-08-07
    directive ③: a story must never be left with no follow control).

    WHY `status != 'dismissed_user'` AND NOT `status = 'active'`. Dormancy is a
    LIFECYCLE state, not an unfollow — the reader still holds a dormant thread
    and it is still listed under Following. Filtering on 'active' would kill the
    marker for a thread he never let go of and offer him "Follow this thread"
    for something he already follows, which is precisely the double-mint bug
    `_deep_follow_line`'s own header records ("one tap minted a SECOND active
    thread beside the tracked one"). Only `dismiss_thread` writes
    `dismissed_user` (memory.py:1733), and a hard-deleted row does not come back
    from this query at all, so both real unfollow paths are covered.
    """
    return {r["topic"].lower() for r in con.execute(
        "SELECT topic FROM memory WHERE status != 'dismissed_user'")}


def _live_marks(slot: Dict, held_topics: Optional[set]) -> List[str]:
    """The slot's tracked-thread marks, filtered to threads still held.

    THE ONE FILTER, called at both marker-class sites (the Today card and the
    deep view's follow mount) — the same reason `_tracked_marker_html` is one
    function: a second copy of this predicate is how two surfaces drift apart,
    which is the class NL-143 and this rider both exist to close.

    `held_topics=None` means "no follow state available" (a fixture render with
    no connection) and returns the marks unchanged: the stored edition record is
    then the only truth there is, and inventing a follow state from nothing would
    be worse than reporting what the edition said.
    """
    marks = [m for m in (slot.get("matched_memory") or []) if m]
    if held_topics is None:
        return marks
    return [m for m in marks if m.lower() in held_topics]


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
    failure.

    LF IS THE CONTRACT (QA F-7 / gate R-E, 2026-08-14). This splits with
    `str.splitlines()` and rebuilds with `"\\n".join(...)`, so every edit
    NORMALISES the file's line breaks to LF: CRLF endings, and the exotic
    breaks `splitlines()` also honours (U+2028, U+2029, U+0085, VT, FF), come
    back as newlines. Chosen, not overlooked — this is a Unix-only tool writing
    a file it also parses, and his file measured 0 CRLF and 0 U+2028. Where it
    would matter it usually cannot bite: a break that damages the YAML is caught
    by the reload-validate below and reverted. The residue is a break that
    splits two comments into two valid comments — the file parses, the revert
    never fires, and the character is gone under a success message. Pinned as a
    contract, not a defect, in tests/test_nl152_154_settings_batch_qa.py."""
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
        except Exception:
            _write(original)
            # NL-103 FIX-2: reader-rendered. The exception text (and the
            # problems list below) named the file and its internals; the
            # reader gets the outcome — nothing saved, nothing broken.
            # `doctor.py:740` is where the diagnostics live.
            return False, ("Nothing was saved — that change would have broken "
                           "your sources file.")
        if cfg.problems:
            # M7 gate finding 1: load_sources reports most malformations via
            # cfg.problems WITHOUT raising — shipping a problems-state file
            # would brick every later pipeline run until a hand-edit. Treat
            # problems as validation failure, same as an exception.
            _write(original)
            return False, ("Nothing was saved — that change would have broken "
                           "your sources file.")
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


def _open_empty_flow_list(lines: List[str], key_index: int, yaml_level: str) -> bool:
    """Rewrite `broad: []` in place to the bare-key form, so a block-sequence
    item can be appended under it. Returns True if it changed the line.

    Stage-0 M1, QA fix loop 1 (F3). An EMPTY flow list has no block sequence
    to append to: inserting "    - X" beneath it yields invalid YAML, so the
    editor's own validator reverts and the reader is told the edit failed.
    That shut the add-your-first-interest door on every freshly provisioned
    profile — the one act the Commissioning routes a new reader toward — and
    on any hand-written `broad: []` besides. Bare-key IS this editor's own
    canonical empty shape: it is exactly what topic_remove leaves behind when
    you delete your last interest.

    Deliberately narrow. A POPULATED flow list (`broad: [Alpha, Beta]`) is
    left completely alone: converting it means moving items, which is a
    different and riskier edit than this fix loop is scoped for, and its
    current behaviour (honest revert, file restored intact) is already safe.
    """
    if not 0 <= key_index < len(lines):
        return False
    line = lines[key_index]
    body, sep, comment = line.partition("#")
    if body.replace(" ", "") != f"{yaml_level}:[]":
        return False
    indent = line[:len(line) - len(line.lstrip())]
    tail = ""
    if sep:
        # Keep the original run of spaces before the '#' so the column stays
        # aligned — and never drop it to zero: `broad:#note` has no space
        # before the hash, so YAML reads it as a scalar, not a comment.
        gap = body[body.rindex("]") + 1:] or "  "
        tail = f"{gap}{sep}{comment}"
    lines[key_index] = f"{indent}{yaml_level}:{tail}"
    return True


def _bad_name(name: str) -> str:
    """Structural characters would change the sources file's meaning (M7 gate
    finding 1 follow-on): reject before surgery.

    NL-103 FIX-2 (gate 2026-07-26): these strings reach the reader — both popup
    status elements render the API's `error` — so they are REFUSALS under §3:
    what did not happen, in reader-world terms, no config paths, no filenames,
    no internal vocabulary. The mechanism ("it changes the file's structure")
    is the machine's business, not the reader's.
    """
    if ":" in name:
        return "Nothing was added — a name can’t contain ':'."
    if "\n" in name or "\r" in name:
        return "Nothing was added — a name can’t contain line breaks."
    if name.lstrip().startswith("#"):
        return "Nothing was added — a name can’t start with '#'."
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
            # NL-103 FIX-2: reader-rendered refusal — no config path, no
            # filename, and it names the reader's level, never `yaml_level`.
            return False, (f"Didn’t add it — your sources file has no section "
                           f"for {level} topics."), lines
        _open_empty_flow_list(lines, start - 1, yaml_level)
        existing = {ln.strip()[1:].split("#")[0].strip().lower()
                    for ln in lines[start:end] if ln.strip().startswith("-")}
        if name.lower() in existing:
            return False, (f"Didn’t add it — {name} is already in your "
                           f"{level} topics."), lines
        insert_at = end
        while insert_at > start and not lines[insert_at - 1].strip():
            insert_at -= 1
        lines.insert(insert_at, f"    - {name}")
        # Not reader-rendered today (addTopic discards `detail` on ok), but it
        # was the last live `interest` in the module — FIX-2 kills it too.
        return True, f"added {name} as a {level} topic", lines

    return _yaml_edit(mutate)


# ---------------------------------------------------------------------------
# THE FOUND ACT'S ANSWER — Stage-0 C1, QA-9 (HIGH, fix loop 2)
# ---------------------------------------------------------------------------
# The whole answer is computed HERE, off the socket, and the handler below does
# nothing but send it. Two reasons, both load-bearing:
#
#   1. A broad guard that wraps self._send_json() can DOUBLE-SEND — the send
#      itself sits inside the region it is guarding. Computing the payload
#      first means the guard has exactly one thing to protect and one thing to
#      answer with, and no arm where half a response is already on the wire.
#   2. It is callable without a socket, so the falsifier for QA-9 can inject a
#      fault at every seam in this function and read the payload directly,
#      rather than inferring the wire's contents from a rendered page.

def _commission_door(name: str, level: str) -> Tuple[bool, str]:
    """topic_add, with a RAISE treated as the door saying no.

    QA-9's mechanism: _yaml_edit wraps only config.load_sources() in try/except
    — `path.read_text()`, `tmp.write_text()` and `os.replace()` are bare — so a
    read-only profile directory walks a real OSError straight out of topic_add.

    Answering `(False, "")` hands that fault to the one path commission()
    already models truthfully: it re-reads the file, computes what actually
    landed, and names it. A door that raises and a door that refuses are the
    same fact about the file, and the caller must not be able to tell them
    apart — otherwise the refusal's truthfulness would depend on which of the
    two happened, which is the QA-3 defect wearing a fourth face.

    The message is emptied deliberately: topic_add's own strings are vouched
    for, an exception's are not, and this function cannot tell which it is
    holding by the time it returns. commission() composes the reader's sentence
    from the FILE either way (`_refusal_for`), so nothing is lost. The
    operator's grade goes to the serve terminal."""
    try:
        return topic_add(name, level)
    except Exception as exc:            # noqa: BLE001 — breadth is the point
        print(f"commission: saving {name!r} failed: {exc}", flush=True)
        return False, ""


def _commission_answer(body: Dict) -> Tuple[Dict, int]:
    """(payload, status) for POST /api/commission — SEAM 2's ordering.

    Write the topics, VERIFY they are readable, and only then start the
    generate. The verification is not a formality: a run started on a profile
    whose interests did not land dies ~30 minutes later in `run_rank`'s
    refusal, which names the profile and a filesystem path — a CLI sentence as
    a stranger's first screen, which is the exact thing this milestone exists
    to make impossible. `commissioning.commission` returns the go-ahead or a
    reader-world refusal; there is no third answer and no path from a refusal
    to GEN_JOB.start().

    QA-9 (fix loop 2) — THE ANSWER'S VOCABULARY IS CLOSED. Every `error` this
    function can return is the output of `commissioning.reader_refusal()`,
    whose codomain is the five blessed constants plus the one partial sentence
    re-derived from what landed on disk. `str(exc)` has no route out of here:
    the broad arm below catches everything the catalog load, the YAML edit, the
    staleness check and the job start can raise, answers a blessed sentence,
    and puts the operator's grade of the same fact in the serve terminal —
    exactly as _GenJob._run already does for a failed generate.

    KNOWN IMPRECISION, disclosed rather than hidden: the broad arm answers
    COMMISSION_WRITE_REFUSAL. Every raise the write door itself can make is
    already routed through _commission_door into commission()'s truthful
    machinery, so this arm is reachable only via the catalog load,
    config.load_sources(), the staleness check or GEN_JOB.start(). For the last
    two the topics ARE saved, which makes "your topics couldn't be saved"
    imprecise. The alternative was a new reader-facing string, which the QA-9
    contract does not admit; and the picker discloses the truth on the very
    next load regardless (a saved topic re-renders checked, QA-7). Flagged for
    the gate, not ruled."""
    try:
        raw = body.get("topics") if isinstance(body, dict) else None
        names = [str(t) for t in raw] if isinstance(raw, list) else []
        try:
            cat = catalog.load()
        except catalog.CatalogError as exc:
            # Loud in the log, reader-world on screen. A picker that cannot
            # read its own catalog has nothing honest to offer.
            print(f"commissioning: {exc}", flush=True)
            return {"ok": False, "error": labels.COMMISSION_WRITE_REFUSAL}, 500
        ok, refusal, written = commissioning.commission(
            names, _commission_door, cat=cat)
        if not ok:
            return ({"ok": False,
                     "error": commissioning.reader_refusal(refusal, written)},
                    400)
        if _server_is_stale():
            # Same teeth as the generate trigger — the topics ARE saved (that
            # write is the reader's, and it stands), so the answer names the
            # one thing that did not start.
            return {"ok": False, "error": labels.STALENESS_REFUSAL}, 409
        started = GEN_JOB.start()
        return ({"ok": True,
                 "detail": "started" if started else "already running"}, 200)
    except Exception as exc:            # noqa: BLE001 — breadth is the point
        print(f"commission failed: {exc}", flush=True)
        return {"ok": False, "error": labels.COMMISSION_WRITE_REFUSAL}, 500


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


# ---------------------------------------------------------------------------
# NL-152 — the generation hour, written where every other principal switch lives
# ---------------------------------------------------------------------------
# THROUGH `_yaml_edit`, DELIBERATELY, and not through a yaml.safe_dump round
# trip: that file is 200+ lines of HIS comments (source rationales, the tier
# taxonomy, the tts_engine ear-test note), and a dump would silently delete all
# of them the first time he changed the time his news arrives. Line surgery
# keeps the comments and gets the same reload-validate-or-revert guarantee the
# topic editor has had since M7.

_SETTINGS_KEY = "generate_hour"


def _settings_block(lines: List[str]) -> Tuple[int, int]:
    """(start, end) line range of the `settings:` block's BODY, or (-1, -1).

    The block ends at the next line that starts in column 0 — the same
    structural read `_find_interest_list` does — so blank lines and comments
    inside the block ride along with it and are never used as terminators.

    THE STATED BOUND (QA F-9 / gate R-E, 2026-08-14): a COLUMN-0 comment is
    adopted into the body by that rule, so a `# --- Section ---` header sitting
    between this block and the next top-level key extends the block past it and
    an absent key is appended BELOW the header. The file still parses and the
    hour still reads back correctly — it is a filing defect, not a data one —
    and the shape is absent from his file (a blank line, then `sources:`).
    Accepted rather than fixed: skipping trailing comments on the walk-back is
    surgery on the crown-jewel writer for a cosmetic shape nobody has, and this
    function's other bound (a comment INSIDE the block is part of the block) is
    the one that keeps his in-block notes safe. Pinned as a placement bound, not
    a promise, in tests/test_nl152_154_settings_batch_qa.py."""
    start = -1
    for i, ln in enumerate(lines):
        if start < 0:
            if ln.startswith("settings:"):
                start = i + 1
            continue
        if ln.strip() and not ln[0].isspace() and not ln.startswith("#"):
            return start, i
    return (start, len(lines)) if start >= 0 else (-1, -1)


def _trailing_comment(line: str) -> str:
    """The ` #…` fragment at the end of a `key: value` line, or "".

    GATE G-1 (2026-08-14). The hour rewrite below replaces the WHOLE line, so a
    note he had written beside the value vanished under a success message — the
    one line in this file the product itself rewrites, in a file organised
    entirely around his annotations. Comment survival is this editor's headline
    claim (see the block comment above); it held everywhere except here.

    BOUND: a `#` inside a QUOTED scalar is data, not a comment, and re-emitting
    it after a bare integer would change what the line means. So the fragment is
    taken only when nothing between the colon and the `#` opens a quote. Losing
    a comment is bad; inventing one out of a value is worse."""
    after = line.split(":", 1)[1] if ":" in line else ""
    # `\s+` and not `\s`: the whole run of whitespace belongs to the fragment,
    # or a `value  # note` he aligned by hand comes back one space narrower —
    # a smaller version of the same silent rewrite this function exists to stop.
    m = re.search(r"\s+#.*$", after)
    if not m or '"' in after[:m.start()] or "'" in after[:m.start()]:
        return ""
    return m.group(0)


def settings_set_hour(hour: int) -> Tuple[bool, str]:
    """Persist the scheduled-generation hour into sources.yaml `settings:`.

    VALIDATES BEFORE IT WRITES, because `_yaml_edit`'s revert protects the FILE
    and not the reader: an out-of-range hour would round-trip through YAML
    intact, land in `cfg.problems`, and come back as the generic "that change
    would have broken your sources file" — a true sentence about the wrong
    thing. The bound here is config's own (0-23), stated once."""
    try:
        hour = int(hour)
    except (TypeError, ValueError):
        return False, "Nothing was saved — pick a whole hour of the day."
    if not 0 <= hour <= 23:
        return False, "Nothing was saved — pick a whole hour of the day."

    def mutate(lines):
        start, end = _settings_block(lines)
        if start < 0:
            # No `settings:` block at all. REFUSED rather than created: this
            # editor's whole contract is surgery on a structure that exists,
            # and inventing a top-level block is the one edit whose failure
            # mode is a file that parses but means something new.
            return False, ("Nothing was saved — your sources file has no "
                           "settings section."), lines
        pat = re.compile(r"^(\s+)" + _SETTINGS_KEY + r"\s*:.*$")
        for i in range(start, end):
            m = pat.match(lines[i])
            if m:
                lines[i] = (f"{m.group(1)}{_SETTINGS_KEY}: {hour}"
                            + _trailing_comment(lines[i]))
                return True, f"generation hour set to {hour:02d}:00", lines
        # Absent key: append at the block's end, past any trailing blank lines
        # so the new key lands inside the block rather than after the gap that
        # separates it from `sources:`.
        insert_at = end
        while insert_at > start and not lines[insert_at - 1].strip():
            insert_at -= 1
        lines.insert(insert_at, f"  {_SETTINGS_KEY}: {hour}")
        return True, f"generation hour set to {hour:02d}:00", lines

    return _yaml_edit(mutate)


def writer_add(name: str, url: str) -> Tuple[bool, str]:
    """Paste-a-link path: append a followed_analyst source entry at the end
    of the sources list (just before the interests block)."""
    if not url.lower().startswith(("http://", "https://")):
        # NL-103 FIX-2: refusal form — what didn't happen, why in reader-world
        # terms, and the shape of the thing that would work.
        return False, ("Didn’t follow — that doesn’t look like a link. Feed "
                       "links start with http:// or https://.")
    bad = _bad_name(name.strip() or url)
    if bad:
        return False, bad
    cfg = config.load_sources()
    display = name.strip() or url
    for s in cfg.sources:
        if s.name.lower() == display.lower() or s.rss_url == url:
            return False, f"Didn’t follow — {s.name} is already in your sources."

    def mutate(lines):
        anchor = next((i for i, ln in enumerate(lines)
                       if ln.startswith("interests:")), -1)
        if anchor < 0:
            # NL-103 FIX-2: the anchor is an implementation detail; the reader
            # gets the standing condition in their own terms.
            return False, ("Didn’t follow — your sources file is missing its "
                           "topics section."), lines
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
        # READER-RENDERED (gate-found): the handler returns this as `detail`
        # and row 11's receipt renders it — "Following <name> — added to your
        # sources". "pool" was internal vocabulary on a live reader surface.
        return True, "added to your sources", lines

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


def _ordinal_num(n: int) -> str:
    """1 -> '1st', 2 -> '2nd', 3 -> '3rd', 11 -> '11th'. Natural case: the
    memline's CSS text-transform does the visual uppercasing, so screen readers
    hear 'third', not the letters of an uppercased 'THRD'."""
    if 10 <= n % 100 <= 20:
        suf = "th"
    else:
        suf = {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suf}"


def _memory_stamp_inner(con, slot: Dict, date: str, degraded: bool = False,
                        seen: Optional[set] = None) -> str:
    """The slim memory stamp's INNER html (v8-M2 item 2) — PURE FURNITURE, no
    generated prose. Full form (lead + medium cards): '● Nth entry on this
    thread · last covered <Mon D>'. Degraded form (strips): '● last covered
    <Mon D>' — the ordinal drops. Below the strip meta line there is no smaller
    slot, so the signal degrades to ABSENT (''), never a partial bare-dot: the
    deep view still carries the full arc, so absence loses nothing durable, and
    a lone green dot would make colour the sole channel (Axel's veto). The WORDS
    carry the meaning. Renders for the slot's FIRST followed thread that moved
    this edition with prior coverage; '' otherwise.

    BUG-35 carried forward: `seen` is the caller's per-EDITION dedup set. On a
    sanctioned-split day (two same-thread slots in one edition) the stamp is
    identical for both — the prominent (earliest-rendered) slot wins and the
    sibling suppresses, so the covered-before signal never doubles (the register
    the principal reviewed against). Keyed on thread id."""
    if con is None or not _is_calendar_date(date):
        return ""
    from . import memory_core
    for topic in slot.get("matched_memory") or []:
        tid = memory_core.resolve_thread_id(con, topic)
        if tid is None:
            continue
        stamp = memory_core.today_memory_stamp(con, tid, date)
        if stamp is None:
            continue
        if seen is not None:
            if tid in seen:
                return ""                 # per-edition dedup: prominent slot won
            seen.add(tid)
        ordinal, _last = stamp
        # NL-17-M1c — his 07-25 ruling ⑤ + the attachment ruling: THE SECOND DOT
        # IS DEAD (the only dot a card shows again is the terra follow mark) and
        # the moved indication is the single word "Updated". This stamp fires
        # ONLY for a thread that moved this edition with prior coverage
        # (memory_core.today_memory_stamp), so every stamp it renders IS the
        # moved state — the "last covered <date>" clause it replaces is what v11
        # shows on the UNMOVED card, which this stamp never renders. Weight is
        # never the sole channel: the WORD changes too (Axel's law).
        if degraded:
            return _e(labels.MEMLINE_UPDATED)
        return (f'{_e(_ordinal_num(ordinal))} entry on this thread '
                f'· {_e(labels.MEMLINE_UPDATED)}')
    return ""


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


def _strip_smeta(slot: Dict, stamp_inner: str) -> str:
    """The strip's machine meta line (v8-M2 item 1): a mono register line led,
    when its followed thread moved this edition, by the DEGRADED stamp
    (● last covered <date>), then the corroboration count and the primary
    selecting topic. CODE-OWNED, never prose; '' when there is nothing honest to
    say. Uppercase is CSS presentation (screen readers hear natural case)."""
    bits: List[str] = []
    if stamp_inner:
        bits.append(stamp_inner)
    meta = (slot.get("corroboration_label") or "").strip()
    if meta:
        bits.append(_e(meta))
    # NL-134 F3: the primary-selecting-topic echo is GONE from this line. The
    # why-chosen line now rides above every strip's headline and carries the
    # WHOLE answer ("Related to: <every match>"), not the first name before the
    # first comma — and the principal's spec is that the reason shows JUST
    # once. _here_for is untouched: it still serves the deep view and the
    # markdown briefing's meta-line.
    if not bits:
        return ""
    return f'<p class="smeta">{" · ".join(bits)}</p>'


def _selection_names(slot: Dict) -> List[str]:
    """The followed things this slot matched — tag names first, then tracked
    threads; order-preserving, case-insensitively deduped, empties dropped.

    Extracted from _here_for (NL-134 F3) so the why-chosen line and the 'Here
    for' rationale cannot drift apart. The NL-68 exhibit ('Strait of Hormuz,
    Strait of Hormuz' — a tag and a tracked thread of the same name doubling the
    line) must stay dead on BOTH surfaces, and ONE dedupe is how that stays
    true. Behaviour is byte-identical to the code this replaced."""
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
    return ordered


def _followed_writer_outlets() -> set:
    """Outlet names the reader follows as WRITERS (sources.yaml
    `followed_analyst: true`). Needed only to NAME the writer in the why-chosen
    line — the slot itself carries a bare `followed_analyst` bool, so the name
    has to come from config.

    An unreadable or absent sources file degrades to the UN-NAMED credit ("a
    writer you follow"). That is a disclosed degrade, not a silent catch: the
    line still renders, still says a followed writer is why the story is here,
    and never invents an outlet name — and a config problem never takes the
    front page down. Called once per edition render, not once per story."""
    try:
        return {s.name for s in config.load_sources().followed_analyst_sources}
    except (config.SourcesParseError, OSError):
        return set()


def _why_chosen_parts(slot: Dict,
                      followed_writers: Optional[set] = None) -> Tuple[str, str]:
    """(prefix, subject) for THE WHY-CHOSEN LINE — code-owned, never prose,
    never empty (NL-134 F3, folding NL-117's why-chosen provenance order).

    THE PRINCIPAL'S DISPLAY SPEC, 2026-08-02, verbatim: "The reason for the
    story should be just be displayed as 'Chosen because:' or 'Related to:' and
    then '{relevant topics the user follows} or Important World News.'" Two
    forms, nothing else:

        Related to: <the followed things that put this story here>
        Chosen because: Important World News

    Precedence follows _here_for's, deliberately: what the reader FOLLOWS
    outranks the world-impact fallback, so a story is never told "we picked
    this for you" when the reader's own topics are the true answer.

    The followed-writer credit joins the Related-to list whenever
    followed_analyst is a basis. It NAMES the outlet when sources.yaml resolves
    one (the slot's own outlets ∩ the followed set) and stays un-named
    otherwise — never a fabricated byline. A followed outlet dropped from the
    slot's named outlets (wire-excluded) simply yields the un-named credit.

    NO match at all — the world-impact override, and any zero-match slot the
    combined score carried — takes the Chosen-because form. That is TRUE by
    construction (personal_score contributed nothing to the pick) and it is the
    exact wording the principal specified, so the line is never empty, never
    false, and never the model's prose."""
    names = _selection_names(slot)
    if slot.get("followed_analyst"):
        named = [o for o in (slot.get("outlets") or [])
                 if o in (followed_writers or set())]
        credited = {o.lower() for o in named}
        names = [n for n in names if n.lower() not in credited]
        names = names + ([f"{o} ({labels.WHY_FOLLOWED_WRITER})" for o in named]
                         or [labels.WHY_FOLLOWED_WRITER])
    if names:
        return labels.WHY_RELATED_TO, ", ".join(names)
    return labels.WHY_CHOSEN_BECAUSE, labels.WHY_WORLD_NEWS


def _why_chosen(slot: Dict, followed_writers: Optional[set] = None) -> str:
    """The why-chosen line as plain text (see _why_chosen_parts)."""
    prefix, subject = _why_chosen_parts(slot, followed_writers)
    return f"{prefix} {subject}"


def _why_chosen_html(slot: Dict,
                     followed_writers: Optional[set] = None) -> str:
    """The why-chosen line as front-page markup. The world-impact form keeps
    the visual prominence the old override note had (it is still the "this is
    off your map" signal); the Related-to form reads as quiet furniture."""
    prefix, subject = _why_chosen_parts(slot, followed_writers)
    cls = ("why-chosen why-chosen--world"
           if prefix == labels.WHY_CHOSEN_BECAUSE else "why-chosen")
    return (f'<p class="{cls}"><span class="why-label">{_e(prefix)}</span> '
            f'{_e(subject)}</p>')


def _render_story(i: int, st: Dict, slot: Dict, tier: str,
                  active_topics: set, has_file: bool = False,
                  slug: Optional[str] = None, date: str = "",
                  deep_return: str = "view-today", con=None,
                  arc_seen: Optional[set] = None, role: str = "story",
                  grid_cls: str = "", grid_row: str = "",
                  followed_writers: Optional[set] = None,
                  brief_slug: str = "",
                  held_topics: Optional[set] = None) -> str:
    """One story in the v8 newspaper grid. `role` selects the shape:
    - "lead"  → article.lead: h2 + deck (follow + slim memory stamp) + body +
                [full picture] + furniture (the dominant left column, spanning).
    - "story" → article.story (medium card, right column): h2 + deck + body + …
    - "strip" → article.strip (the quick-tier GROUT): hairline top rule +
                headline-link + 4-line-clamped summary + machine smeta + the
                follow mount (NL-143 item 2a — his directive; the strip was the
                one tier a reader could not follow). Still no deck, no body
                beats, no bottom link — the headline IS the deep-view door
                (NL-68 item 8).
    `brief_slug` ("first"/"secondary"/"") marks a strip as the head of an
    In-Brief run and renders the aria-hidden section slug (NL-143 item 3a — his
    07-18 item 2, approved at that evening's polish gate). v8-M2's claim that
    the label was dead because "scale and placement are the label" held at
    lead/card scale and misread at strip scale: a clamped strip reads as
    truncated coverage until the register is named.
    `grid_cls` is the CSS grid COLUMN placement; `grid_row` (FIX-1) is the
    computed "<start> / <end>" row span, emitted as the --gr custom property so
    the ≥900px grid squares the rectangle while the ≤900px stack (which resets
    grid-row to auto) is untouched. Both are presentation only; DOM stays rank
    order. v8-M2: the arc PROSE block is gone from Today entirely — a slim
    machine STAMP rides the deck (full form) / the smeta (degraded); the full
    arc register lives only in the deep view."""
    slug = slug or f"story-{i}"
    # Heading semantics (v7-M2): ONE h1 per document view — the dateline (Today)
    # / view-title (edition) is the h1, so the lead story demotes to h2. Stories
    # are h2; the strips (quick tier) are h3 — the heading tree carries the tier
    # for screen readers. NL-143 item 3a returns a VISIBLE "In brief" slug, but
    # only as aria-hidden ornament — this heading tree remains the sole
    # structural rendering of the tier, which is why the slug is a <p>.
    wrap_cls, h = {"lead": ("lead", "h2"), "strip": ("strip", "h3")}.get(
        role, ("story", "h2"))
    stamp = _memory_stamp_inner(con, slot, date, degraded=(role == "strip"),
                                seen=arc_seen)
    row_style = f' style="--gr:{grid_row}"' if grid_row else ""
    parts = [f'<article class="{wrap_cls}{grid_cls}" id="{_e(slug)}"{row_style}>']

    # NL-143 item 3a — THE IN-BRIEF SLUG, at the head of a strip run (see
    # _brief_slug_heads for which strips get one and why). aria-hidden ORNAMENT:
    # the h3 heading below already carries the quick tier for assistive tech, so
    # a second structural rendering of one semantic tier would be phantom
    # structure. Sighted scanning is the whole job — a clamped strip that ends
    # mid-thought reads as truncated coverage until something names the register
    # as deliberate brevity.
    if brief_slug:
        second = " brief-slug--secondary" if brief_slug != "first" else ""
        parts.append(f'<p class="brief-slug{second}" aria-hidden="true">'
                     f'{_e(labels.IN_BRIEF)}</p>')

    # THE WHY-CHOSEN LINE (NL-134 F1 + F3) — above the title, on EVERY story and
    # every tier, where the override callout used to sit: the "why am I seeing
    # this" answer arrives before the story, which is NL-117's whole point.
    #
    # It REPLACES the override note. That block read the slot's stored
    # `override_label` (a prose prefix + the ranker's one-sentence reason) and
    # then appended `world_impact_reason`, THE SAME TEXT, in a <span
    # class="reason"> with no separator between them. The principal's fresh1
    # specimen (2026-08-02) read "…energy prices.Pause in potential…".
    #
    # NL-138 (his ruling ④, same day) finished the job F1 started: the ranker's
    # prose reason is gone from the pipeline entirely — not written, not
    # stored, not rendered anywhere, including the deep view that briefly held
    # it. Both fields this comment used to name are deleted from RankedSlot.
    # This line is now the ONLY answer any surface gives to "why am I seeing
    # this", which is what "just once" was always supposed to mean.
    parts.append(_why_chosen_html(slot, followed_writers))

    # NL-68 item 6: the visible "The Lead" kicker DIES — scale + placement carry
    # the hierarchy. NL-68 item 8: the title itself is the deep-view door.
    parts.append(_headline_html(h, st.get("headline", ""), slot, has_file, tier,
                                slug, deep_return))

    # NL-145 (M1 gate R2), marker-class site 1 of 2: `matched_memory` is the
    # EDITION's record of what the ranker matched — a fact about that morning,
    # never a live claim about what the reader follows now. The marker makes a
    # live claim, so it reads through the filter; every OTHER consumer of
    # matched_memory on this page (the archive keywords, the why-chosen line,
    # the timeline, the arc line, the deep view's "Tracked threads:") is
    # reporting the edition and is deliberately left alone.
    marks = _live_marks(slot, held_topics)
    # NL-68 item 7 (kill the covered-before DUPE), v8-M2 form: the slim stamp and
    # the tracked-ongoing marker BOTH signal prior coverage. Where the stamp
    # shows (the thread moved this edition, with history), the redundant marker
    # is suppressed and the stamp is its richer replacement. A tracked story with
    # NO stamp (day-one / did-not-move) keeps the marker as its sole signal; a
    # non-tracked story keeps its follow toggle. The marker/follow-control
    # redesign itself stays NL-68 item 2's job.
    suppress_marker = bool(marks) and bool(stamp)
    # v11 FLAG ③: the committed card verb opens this story's deep view when one
    # exists (_has_deep_view is the same predicate the bottom entry link uses,
    # so the verb can never become a dead door the entry link knows is dead).
    card_door = slug if _has_deep_view(has_file, tier) else ""
    # NL-143 item 2a: computed ABOVE the strip branch now, because the quick tier
    # mounts the very same control. One computation, three roles — a second
    # strip-only call would be a second follow rendering, which is the thing the
    # single-rendering law exists to forbid.
    follow = "" if suppress_marker else _follow_control(
        st, slot, marks, active_topics, date, slug=slug, con=con,
        deep_slug=card_door, deep_return=deep_return)

    if role == "strip":
        # Hairline strip: 4-line-clamped summary (the lede; a headline-only
        # strip carries none) + machine smeta with the degraded stamp + the
        # follow mount.
        if st.get("lede"):
            parts.append(f'<p class="sum">{_e(st["lede"])}</p>')
        parts.append(_strip_smeta(slot, stamp))
        # NL-143 item 2a — HIS 2026-08-07 DIRECTIVE: the quick tier is
        # followable. The strip branch used to return here, so an In-Brief item
        # was the one story tier a reader could not follow at all.
        # It mounts THE SAME component in its SAME card form (data-mount="card",
        # the compact deck verb, the identical resting/committed vocabulary) —
        # no new UI species, and the strip stays austere: no .deck wrapper,
        # because that container carries a bottom rule and card margins the
        # strip register forbids.
        #
        # AUSTERITY BOUND (caught by test_v8_m2_qa's marker-resurrection pin):
        # the MARKS path is deliberately not mounted here. When a story matches
        # a tracked thread, _follow_control renders the "Tracked ongoing story
        # — <thread>" MARKER in place of a control — right on a card, wrong on a
        # strip twice: it is a second vocabulary on the tier ruled austere, and
        # v8-M2 already gave the strip its own memory signal (the degraded stamp
        # in the smeta). Nothing is withheld from the reader by this: a
        # marks-carrying story is one he ALREADY follows, so there was never a
        # follow on offer — which is exactly the card's behaviour too.
        if follow and not marks:
            parts.append(f'<p class="strip-follow">{follow}</p>')
        parts.append("</article>")
        return "".join(parts)

    deck_bits: List[str] = []
    if follow:
        deck_bits.append(follow)
    if stamp:
        # the stamp only ever renders the MOVED state (see
        # _memory_stamp_inner), so the weight step rides it unconditionally.
        deck_bits.append(
            f'<span class="memline memline--moved">{stamp}</span>')
    if deck_bits:
        parts.append(f'<p class="deck">{"".join(deck_bits)}</p>')

    # Body: lede, then the "Why it matters"/"Watch for" beats as .move-label
    # headers (§2). NO arc prose on Today anymore (v8-M2) — the stamp above is
    # the whole Today memory signal.
    body_parts: List[str] = []
    if st.get("lede"):
        body_parts.append(f'<p>{_e(st["lede"])}</p>')
    body_parts.extend(_story_movement_paras(st, date))
    parts.append(f'<div class="body">{"".join(body_parts)}</div>')

    # NL-65: the deep-view entry moves to the story BOTTOM, before the furniture.
    entry_link = _deep_entry_link(has_file, tier, slug, deep_return)
    if entry_link:
        parts.append(f'<p class="story-more">{entry_link}</p>')

    # Corroboration furniture — CODE-OWNED, from the slot (never prose).
    # NL-134 F3: the "Here for: …" clause is GONE from this line. The why-chosen
    # line above the headline is now the story's ONE reason display, and the
    # principal's spec says the reason is shown JUST that way — a second clause
    # restating the same answer in a second vocabulary is the duplication class
    # F1 exists to kill. What remains here is corroboration, which the
    # why-chosen line never carried. _here_for itself is untouched and still
    # serves the deep view and generate.py's markdown meta-line.
    outlets = slot.get("outlets") or []
    meta = slot.get("corroboration_label", "")
    if outlets:
        meta += f' — {", ".join(outlets)}'
    parts.append(f'<p class="furniture">{_e(meta)}.</p>')

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
    tag name (any case) is dropped. Empty names are dropped too. NL-134 F3 moved
    that dedupe into _selection_names, shared with the why-chosen line, so the
    two surfaces can never disagree about what the reader matched.

    FRONT-PAGE NOTE (NL-134 F3): Today's story cards no longer render this
    line — the why-chosen line replaced it there. This remains the deep view's
    rationale and generate.py's markdown meta-line."""
    matches = ", ".join(_selection_names(slot))
    if matches:
        return matches
    if slot.get("override"):
        return "editor's override — see note above"
    return "world-impact selection (no tag or thread match)"


# NL-17-M1c: _altitude_options is DELETED. THE ASK IS DEAD (his 07-25 ruling
# ④) — an unconfident settle leaves the story-scoped follow standing, silently,
# so there is no option list, no lead line and no low-confidence state left to
# build one for. Nothing else read this function (grep-verified at the diff).


def _follow_altitude_row(con, topic: str) -> Dict:
    """The stored disclosure for an active follow (0019 columns) — read verbatim
    for the committed deck verb. Empty dict when there is no con / no such active
    row / an older DB without the columns (degrade to a BARE committed verb — the
    honest unmigrated v1 mix, never a fabricated qualifier)."""
    if con is None:
        return {}
    try:
        r = con.execute(
            "SELECT id, altitude, primary_entity, disclosure, alt_label,"
            " altitude_source FROM memory"
            " WHERE lower(topic) = lower(?) AND status = 'active'", (topic,)
        ).fetchone()
    except sqlite3.OperationalError:
        return {}
    return dict(r) if r else {}


def _origin_follow_row(con, story_topic: str, headline: str) -> Dict:
    """The active follow this story is the ORIGIN of — an altitude-renamed picker
    follow stored under the resolver's entity/storyline name (so NOT recognizable
    by the story's title in active_topics), bridged by the 0021 origin_story
    column (NL-17-M1b FIX LOOP 1 FIX-1). Matches origin_story against the story's
    canonical topic OR its headline, so the origin card recognizes its follow
    across reload AND across a regenerate (origin_story is the stable story_title,
    surviving a headline drift). Render-time, read-only, NO llm call. {} on an
    unmigrated DB (no origin_story column) / no such row."""
    if con is None:
        return {}
    try:
        r = con.execute(
            "SELECT id, altitude, primary_entity, disclosure, alt_label,"
            " altitude_source, topic, origin_story FROM memory"
            " WHERE status = 'active' AND origin_story != ''"
            "   AND lower(origin_story) IN (lower(?), lower(?))"
            " ORDER BY id DESC LIMIT 1", (story_topic, headline)).fetchone()
    except sqlite3.OperationalError:
        return {}
    return dict(r) if r else {}


def _resolve_guard_row(con, story_topic: str, headline: str) -> Dict:
    """The follow-resolve XOR guard (NL-17-M1b FIX LOOP 1 FIX-1): the active
    follow this story ALREADY carries — by the follow's NAME (topic == the
    story's canonical topic or headline) or by ORIGIN (origin_story == either). A
    tap on an already-followed story is the steady-state expand: the resolve
    endpoint returns THIS committed row and never runs a second paid resolve or
    creates a divergent second active follow (QA's double: "…job cuts" +
    "Volkswagen" for one story). Read-only; {} when unfollowed / on an unmigrated
    DB (the guard degrades off, resolve proceeds as before).

    NL-17 M1: `id` rides the projection. The settle door needs the thread's id to
    append its 0025 outcome and to ask the retry bound about it, and re-looking
    that id up by name afterwards would be a SECOND predicate that can disagree
    with the one that already resolved this row — the NL-139 divergence class,
    one layer over."""
    if con is None:
        return {}
    try:
        r = con.execute(
            "SELECT id, altitude, primary_entity, disclosure, alt_label,"
            " altitude_source, topic, origin_story FROM memory"
            " WHERE status = 'active' AND ("
            "     lower(topic) IN (lower(?), lower(?))"
            "     OR (origin_story != ''"
            "         AND lower(origin_story) IN (lower(?), lower(?))))"
            " ORDER BY id DESC LIMIT 1",
            (story_topic, headline, story_topic, headline)).fetchone()
    except sqlite3.OperationalError:
        return {}
    return dict(r) if r else {}


# --- NL-17-M1c: the write-refusal payload (R-WRITE) -------------------------
# The three MemorySyncError arms, keyed by the `kind` the RAISE SITE names. The
# fallback arm exists because an unmapped raise must never render raw CLI prose
# and must never silently revert — the two failure modes this milestone kills.
_WRITE_REFUSAL_ARMS = {
    "unreadable": (labels.REFUSAL_MEM_UNREADABLE,
                   labels.REFUSAL_MEM_UNREADABLE_FIX),
    "unparseable": (labels.REFUSAL_MEM_UNPARSEABLE,
                    labels.REFUSAL_MEM_UNPARSEABLE_FIX),
    "unwritable": (labels.REFUSAL_MEM_UNWRITABLE,
                   labels.REFUSAL_MEM_UNWRITABLE_FIX),
}


def _write_refusal(exc: Exception, verb: str = "follow") -> Dict:
    """One place composes every R-WRITE payload, so the class, the arm and the
    reason can never disagree. TRANSPORT-SHAPE-INDEPENDENT by construction: this
    rides a 200 with ok:false (the _send_json default) exactly like the coverage
    refusal rides a 409 — the client routes on `refusal`, never on the status.

    `error` keeps str(exc) for diagnostics/logging; `reason`/`remedy` are the
    UI-lane clauses and are the ONLY halves any surface renders."""
    kind = getattr(exc, "kind", "") or ""
    reason, remedy = _WRITE_REFUSAL_ARMS.get(
        kind, (labels.REFUSAL_MEM_FALLBACK, labels.REFUSAL_MEM_FALLBACK_FIX))
    return {"ok": False, "refusal": "write", "verb": verb, "arm": kind,
            "reason": reason, "remedy": remedy, "error": str(exc)}


def _committed_verb_inner(alt: Dict) -> str:
    """The committed deck verb's steady label (single-rendering law STATE 5):
    "● Following — <qualifier>", the disclosure carried on Today. A story-seeded
    thread wears the object seat's deictic ("this thread"); a named follow wears
    the compact qualifier (name + quiet class); an UNMIGRATED follow (no stored
    disclosure) renders bare "● Following" (honest — nothing settled, nothing
    fabricated)."""
    dot = _e(labels.FOLLOW_DOT_ON)
    altitude = alt.get("altitude") or ""
    disclosure = alt.get("disclosure") or ""
    if altitude == "narrow":
        # v11 two-referent noun law: the OBJECT seat takes thread. The scope
        # fact ("— this story") is the management ROW's qualifier, not the
        # state line's — the two never name the same extension four words apart.
        return (f'{dot} {_e(labels.FOLLOW_STEADY_PREFIX)} '
                f'{_e(labels.FOLLOW_THREAD_SELF)}')
    if disclosure:
        name, cls = follow_altitude.split_qualifier(disclosure)
        qual = _e(name)
        if cls:
            qual += f' <span class="oq">({_e(cls)})</span>'
        return f'{dot} {_e(labels.FOLLOW_STEADY_PREFIX)} {qual}'
    return f'{dot} {_e(labels.FOLLOW_COMMITTED_VERB)}'   # unmigrated — bare


def _follow_recognition(con, topic: str, headline: str,
                        active_topics: set) -> Tuple[str, bool, Dict]:
    """Is this story followed, and under which stored name? ONE predicate, so
    every mount of the follow line answers identically (the single-rendering law
    is worth nothing if two surfaces disagree about the STATE they render).

    Two paths, both carried unchanged from the card: (1) NAME (NL-58 P3a) —
    followed when the story_title OR headline is an active topic, so a follow
    survives title drift and the committed reads target the story's phrasing
    (NL-60 gate F1); (2) ORIGIN (FIX LOOP 1) — a settle-renamed follow is stored
    under the settled name, not in active_topics under the story's title, and
    the 0021 origin_story column bridges it back.

    Returns (resolve_subject_topic, followed, origin_row)."""
    t_in = topic.lower() in active_topics
    h_in = headline.lower() in active_topics
    name_followed = t_in or h_in
    if name_followed and not t_in:
        topic = headline
    origin_row = {} if name_followed else _origin_follow_row(con, topic, headline)
    return topic, bool(name_followed or origin_row), origin_row


MARKS_SEPARATOR = "\n"


def _settle_candidate_pair(res) -> List[Dict]:
    """The two rungs an unconfident settle came back holding, as candidates.

    The resolver's contract already names both (prompts/follow_altitude.txt:
    "At low the reader is asked to choose — so make BOTH the disclosure (your
    best rung) and alt_label (the other rung) clean, named options"), so this
    RE-SHAPES that answer and authors nothing: `disclosure` is whichever rung it
    leaned toward, `alt_label` is the other one. Order is leaned-toward first,
    which is the only ranking information the resolver gave us and the order the
    "Instead:" row renders in.

    Degrades to the one candidate it has when alt_label is empty (the M1a-shaped
    answer the validator still admits) — a one-item Instead: row is a lawful
    sentence; a fabricated second option would not be.
    """
    other = "storyline" if res.altitude == "entity" else "entity"
    pair = [{"altitude": res.altitude, "disclosure": res.disclosure}]
    if (res.alt_label or "").strip():
        pair.append({"altitude": other, "disclosure": res.alt_label})
    return pair


def _apply_entity_identity(con, thread_id: int, *, altitude: str,
                           disclosure: str, primary_entity: str) -> Tuple:
    """THE ENTITY-IDENTITY RULE, in one place, for BOTH doors that can aim a
    thread — the system settle (`_settle_onto`) and the reader swap
    (`_api_follow_at`). Returns `(entity_id, detail)`.

    One implementation on purpose: a second copy of this rule is how the two
    copies disagree about what a thread's identity is, and "one concept = one
    vocabulary" (NL-17 acceptance (a)) cannot survive two answers.

    WEIGHT REPLACES, NEVER STACKS (criterion (b)), and that is what the
    unconditional write below is for: aiming at an entity points the thread at
    that entity; aiming at a storyline clears the column to NULL. A thread
    therefore holds AT MOST ONE identity at any instant — there is no reachable
    state in which an old aim's entity and a new aim's entity both apply, which
    is the no-stacking property expressed as a schema fact rather than as a
    ranking-time subtraction. (Ranking itself is M2; nothing here scores.)

    Callers hold the transaction; this never opens one.
    """
    eid, detail = (None, "not-entity-altitude")
    if altitude == "entity":
        eid, detail = entities.mint_or_match(
            con, disclosure=disclosure, primary_entity=primary_entity)
    elif altitude == "storyline":
        detail = "storyline"
    memory.set_thread_entity(con, thread_id, eid)
    return eid, detail


def _tracked_marker_html(marks: List[str], *, slot_id: str = "",
                         mount: str = "card", story_topic: str = "",
                         headline: str = "", date: str = "") -> str:
    """THE ONE tracked-ongoing marker rendering (NL-143 fix loop 1, QA F-1).

    It was a literal inside _follow_control while the card was the only surface
    that could render it. The deep view now needs the same state — and a second
    literal is exactly how the four follow mounts drifted apart in the first
    place, which is the bug this whole batch exists to close.

    ========================================================================
    NL-17 M1 / F-6 — THE MARKER IS NOW A .follow-slot, and that is the whole
    fix for NL-143's fix-loop surprise 4.

    THE BUG. This marker is DERIVED FROM FOLLOW STATE: it renders because the
    story matched a thread the reader follows. Unfollow that thread anywhere
    else on the page and the claim becomes false — but the marker was a bare
    <span> outside every .follow-slot, so the cross-mount sweep walked straight
    past it and it kept announcing "Tracked ongoing story" until a reload.
    Exactly the class NL-143 exists to kill, hiding one node outside the sweep's
    reach.

    THE FIX IS NOT A SECOND MECHANISM. Wrapping the marker in the same
    .follow-slot the sweep already walks means ONE predicate keeps covering
    everything (the F-6 ruling's own word: "one predicate, one sweep"). On a
    remote unfollow the sweep hands this node to flRenderResting and it becomes
    the honest resting CTA — the story is no longer tracked and IS followable,
    which also keeps his 08-07 directive ③ (a story must never be left with no
    follow control) true on this surface instead of leaving a dead marker.

    IDENTITY IS THE MARKS, and they are THREAD NAMES — the same type as
    data-topic, never a story key. That matters for the typed comparison
    NL-143's fix loop 1 landed: marks meet marks and marks meet topics, and the
    cross-type crossing that once rendered one story's follow over another's
    card stays impossible. data-marks carries the whole list (one story can
    match several threads) newline-joined; data-topic carries the first, so a
    node that never syncs still names something true.
    """
    marker = (f'<span class="tracked-marker">{_e(labels.TRACKED_ONGOING_PREFIX)} '
              f'{_e(", ".join(marks))}</span>')
    attrs = [f'data-mount={_e_attr(mount)}',
             f'data-marks={_e_attr(MARKS_SEPARATOR.join(marks))}']
    if slot_id:
        attrs.insert(0, f'id={_e_attr(slot_id)}')
    if marks:
        attrs.append(f'data-topic={_e_attr(marks[0])}')
    if story_topic:
        attrs.append(f'data-story={_e_attr(story_topic)}')
    if headline:
        attrs.append(f'data-origin={_e_attr(headline)}')
    if date:
        attrs.append(f'data-briefing-date={_e_attr(date)}')
    return (f'<span class="follow-slot" {" ".join(attrs)} '
            f'data-state="tracked">{marker}</span>')


def _follow_control(st: Dict, slot: Dict, marks: List[str],
                    active_topics: set, date: str, slug: str = "",
                    con=None, deep_slug: str = "",
                    deep_return: str = "view-today") -> str:
    """The under-title follow control — the follow-altitude picker's single
    persistent node (NL-17-M1b, mockup-v9). SINGLE-RENDERING LAW: ONE
    `.follow-slot` element carries BOTH the compact deck verb (rest, steady) and
    the expanded follow-line (resolving/asking/commit, rendered by the client
    into this same node) — never two follow-state surfaces. The v8
    aria-haspopup="dialog" is retired for this behavior: the committed verb
    carries aria-expanded and re-opens the line (the EDITOR keeps aria-haspopup).

    marks path (a thread-tracked story) is unchanged — it shows the marker STATE,
    not the picker. Recognition has two paths: (1) NAME (NL-58 P3a) — followed
    when the story_title OR headline is an active topic, so a follow survives
    title drift, and unfollow/altitude reads target the story's phrasing (NL-60
    gate F1); (2) ORIGIN (FIX LOOP 1) — an altitude-renamed follow is stored under
    the RESOLVER's name (not in active_topics under the story's title), bridged
    back to its origin card by the 0021 origin_story column, and its committed
    data-topic is the STORED name so unfollow/switch exact-match the real row."""
    topic, followed, origin_row = _follow_recognition(
        con, slot.get("story_title") or st.get("headline") or "",
        st.get("headline") or "", active_topics)
    headline = st.get("headline") or ""
    slot_id = f"follow-{slug}" if slug else "follow-slot"
    if marks:
        # F-6: the marker carries this card's identity so the sweep can find it
        # and truthen it (see _tracked_marker_html). Recognition is resolved
        # ABOVE this branch now — it costs one read that the marks path used to
        # skip, and it buys the story key the swept node rests onto.
        return _tracked_marker_html(
            marks, slot_id=slot_id, mount="card", story_topic=topic,
            headline=headline, date=date)
    date_attr = f' data-briefing-date={_e_attr(date)}' if date else ""
    origin_attr = f' data-origin={_e_attr(headline)}' if headline else ""
    # R1 (fix loop 2): the card's canonical STORY topic. Once committed, data-topic
    # is the STORED follow name (an altitude-renamed follow), so the client stamps
    # this to restore data-topic to the STORY on unfollow — a re-tap then resolves
    # the STORY (not the stale follow name) and stores the canonical origin (the
    # 0021 reload bridge). `topic` here is the resting resolve subject (story_title,
    # else headline; the drift reassignment above is already applied).
    story_attr = f' data-story={_e_attr(topic)}'
    # v11 FLAG ③ — the card's steady verb is a DOOR. With Unfollow gone from
    # cards (his item 4) and "Instead:" ruled off them (his ruling ②), a verb
    # that re-expanded to an actless sentence would be a click with no answer.
    # It opens the story's deep view — the thread's management home — when this
    # story HAS one. When it does NOT (a degraded-hidden slot), the else arm
    # below renders a plain <span> that states the fact and stops: no door, and
    # no toggle either — the collapse toggle died with the acts it expanded to
    # (gate F5: this comment used to claim a mechanism that no longer exists).
    mount_attr = f' data-mount={_e_attr("card")}'
    if deep_slug:
        # stamped so the CLIENT can rebuild the same door after a live follow —
        # one door definition, two renderers (server-rendered and post-tap).
        mount_attr += (f' data-deep-slug={_e_attr(deep_slug)}'
                       f' data-deep-return={_e_attr(deep_return)}')
    if not followed:
        # RESTING: the follow target is the story's canonical topic (story_title,
        # else headline — the v7/NL-65 selection, preserved). The tap COMMITS a
        # story-seeded thread instantly ($0, local); what settles in background
        # is only what else it covers. data-origin (the headline) is the seed's
        # own name. aria-expanded="false" — the line is closed; a tap opens it
        # in this same node.
        # M1c #49: the resting CTA names its object. On a today page of 8-12
        # cards a screen-reader button list otherwise reads "Follow this thread"
        # a dozen times, indistinguishably.
        aria = f"{labels.FOLLOW_THREAD_ARIA} — {topic}"
        return (f'<span class="follow-slot" id={_e_attr(slot_id)} '
                f'data-topic={_e_attr(topic)}{origin_attr}{story_attr}{date_attr}'
                f'{mount_attr} '
                f'data-state="resting">'
                f'<button class="deck-follow not-following" type="button" '
                f'aria-expanded="false" aria-label={_e_attr(aria)} '
                f'onclick="followTap(this)">'
                f'{_e(labels.FOLLOW_THREAD_INACTIVE)}</button></span>')
    if origin_row:
        # the follow lives under the resolver's name — data-topic is that STORED
        # name so unfollow/switch exact-match the real row; the disclosure is read
        # from this row (never a second lookup by the story's — different — title).
        alt = origin_row
        committed_topic = origin_row.get("topic") or topic
    else:
        alt = _follow_altitude_row(con, topic)
        committed_topic = topic
    if deep_slug:
        verb = (f'<a class="deck-follow" href="#" '
                f'onclick="{_deep_view_onclick(deep_slug, deep_return)}">'
                f'{_committed_verb_inner(alt)}</a>')
    else:
        # no deep view on this card: the steady verb states the fact and stops.
        # A dead door is worse than a quiet line (NL-68 item 8's law), and there
        # is nothing left to expand — cards carry no acts (his ruling ②).
        verb = f'<span class="deck-follow">{_committed_verb_inner(alt)}</span>'
    return (f'<span class="follow-slot" id={_e_attr(slot_id)} '
            f'data-topic={_e_attr(committed_topic)}{origin_attr}{story_attr}{date_attr}'
            f'{mount_attr} '
            f'data-state="committed" '
            f'data-altitude={_e_attr(alt.get("altitude") or "")} '
            f'data-alt-label={_e_attr(alt.get("alt_label") or "")} '
            f'data-disclosure={_e_attr(alt.get("disclosure") or "")}>'
            f'{verb}</span>')


def _thread_display_name(alt: Dict, fallback: str) -> str:
    """The thread's reader-facing NAME for an accessible name / a receipt: the
    settled disclosure where one exists, else the row's own title. Never a
    pronoun (§3: a receipt takes the class noun, never "it")."""
    disclosure = (alt.get("disclosure") or "").strip()
    return disclosure or (alt.get("topic") or "").strip() or fallback


def _swap_targets(alt: Dict, candidates: Optional[List[Dict]] = None) -> List[Dict]:
    """THE "Instead:" TARGET SET — one function, so the server render, the client
    render and the swap door can never disagree about what is on offer.

    `targets = the thread's known candidates, minus wherever it is aimed now`.
    Two sources, in priority order:

      1. PERSISTED CANDIDATES (fix loop 1) — what an unconfident settle came back
         holding, read from its `settled_low` event. This is the new case: a
         thread sitting at seed, with two named rungs nobody chose, on a
         management surface.
      2. THE STORED alt_label — the single alternative a CONFIDENT settle named,
         which is the shipped behaviour and stays exactly as it was.

    "THE PRIOR AIM JOINS THE SWAP TARGETS AFTER ANY SWAP" (Ines's fixability;
    supplemental ruling §R.3) FALLS OUT OF THE SUBTRACTION rather than needing
    machinery: the candidate list does not change when the reader swaps, so
    whichever rung they just left is still in it and is no longer the current
    aim — so it renders. A mis-swap is one tap from home, permanently, without
    anything having to remember history.

    AND THE SEED IS NOT A TARGET, deliberately. The prior aim of a FIRST swap is
    the story-seeded state, and amendment (i) bans offering that back: a
    reader-chosen narrow follow is the junk class the whole milestone removes.
    That is why targets come from the CANDIDATE list rather than from "wherever
    this thread has been" — every target is a rung the resolver named, and
    'narrow' is not one of them. Leaving the thread entirely is still one tap,
    on the same line, under the symmetry law (Unfollow).
    """
    aim = (alt.get("disclosure") or "").strip().casefold()
    out: List[Dict] = []
    seen = set()
    for c in (candidates or []):
        disc = (c.get("disclosure") or "").strip()
        altitude = (c.get("altitude") or "").strip()
        key = disc.casefold()
        if not disc or altitude not in memory.PICKABLE_ALTITUDES:
            continue
        if key == aim or key in seen:
            continue
        seen.add(key)
        out.append({"altitude": altitude, "disclosure": disc})
    if out:
        return out
    alt_label = (alt.get("alt_label") or "").strip()
    if alt_label and alt_label.casefold() != aim:
        current = (alt.get("altitude") or "").strip()
        other = "storyline" if current == "entity" else "entity"
        return [{"altitude": other, "disclosure": alt_label}]
    return []


def _follow_acts_line(alt: Dict, name: str, unfollow_name: str = "",
                      candidates: Optional[List[Dict]] = None) -> str:
    """THE ACTS LINE — the whole surviving scope-affordance law, in one place.

    Rendered on MANAGEMENT SURFACES ONLY (deep view, Following row). His 07-25
    ruling ②: today cards carry no acts at all. NO BARE DIRECTIONAL VERB EXISTS
    — "Widen"/"Broaden" render nowhere and no affordance answers "what happens
    if I tap this?" with a direction. Every scope act NAMES its target.

    Composition (content pass §2.4, as amended by RECONVENE-2):
      one or more NAMED targets        -> "Instead: <name> (<class>) · <name>"
      no named target, whatever the
      thread's altitude                -> NOTHING. No anchor, no prose, no aria
                                          action, and no "Instead:" label. The
                                          row renders exactly Unfollow.
    Every rung carries `Switch to <target> — <thread name>` as its accessible
    name (§3 aria law; the artifact implemented it nowhere).

    FIX LOOP 2b — THE WORDED-FALLBACK ARM IS DELETED (product RECONVENE-2,
    unanimous, ruling (b); QA F-4). It rendered "Instead: the wider story" (or
    "the company") on a settled thread whose settle never named the other rung —
    an anchor with an aria promise and NO target identity behind it. Post-fix-
    loop-1 that promise became a silent no-op, which is bucket 1051 exactly: a
    control that announces a switch to AT and pointer alike and does nothing.

    It dies rather than gets repaired, and the grounds are worth keeping here
    because "restore the old behaviour" is the obvious wrong fix:
      * A RESTORE IS DEFINITIONALLY EMPTY. This state exists only when nothing
        resolved, so there is no candidate identity to carry. The one nameable
        target in reach is the thread's own seed storyline title — which is
        precisely the story-scoped offer amendment (i) bans.
      * INERT PROSE FAILS THE REGISTER. "the wider story" with no tap states
        nothing, and the directive is that copy states what is happening and
        nothing more. Nothing is happening.
      * A BARE "Instead:" IS A FALSE LABEL — it asserts alternatives that do
        not exist.
    What remains is not a stranding: Unfollow is one tap (symmetry law) and the
    next story's own follow line is the broaden path. This was the last
    generic-widen ghost — v10 killed the language, v11 the posture; this makes
    the burial deliberate instead of accidental. When a later settle supplies a
    named target (the rename / auto-widen class), the Instead: row returns
    lawfully with that name.

    NL-17 M1 — THE NARROW RUNG IS DELETED, and this is the amendment's whole
    point of impact. It offered "this story" as a rung on every settled thread,
    which is precisely the OFFERED CHOICE the principal's 2026-08-07 amendment
    (i) kills ("too specific and it'll just complicate data for no reason"). It
    dies with NO REPLACEMENT — the council ruled the story rung out without a
    substitute, and the acts line simply carries one fewer bit.

    Note what this does NOT touch: `altitude='narrow'` is still a stored value,
    because the SEED writes it and the seed is the instant-commit law's landing.
    What is gone is the reader's ability to CHOOSE it — which is the exact
    lifecycle distinction the council ruled on (a system-owned, auto-widenable
    seed state is kept; a user-chosen narrow altitude that forks vocabulary
    forever is banned). The door that minted the banned class is closed one
    layer down, in `_api_follow_at`.

    FIX LOOP 1 — `candidates` (supplemental ruling 2026-08-08 §R.3). An
    unconfident settle's named rungs now reach this line, so a thread sitting at
    seed with two plausible readings offers them instead of nothing:

        Instead: <Name (kind)> · <Storyline name>

    ZERO NEW STRINGS, and that is the ruling's own test of itself: the row is
    v11 ruling ②'s, the compact qualifier grammar is the principal's 07-18 menu,
    the bare storyline name is that menu's option 3. Nothing was authored here
    because nothing needed to be.

    THIS FUNCTION IS MANAGEMENT-ONLY AND ALWAYS WAS — its callers are the deep
    mount and the Following row (`_follow_slot_html`), never `_follow_control`.
    That is what keeps the new affordance off today cards, per ruling ②'s own
    ban, and it is why the ruling could reach for this line rather than build a
    surface: the carve-out already existed. The law that reconciles it with the
    silence ruling, verbatim: THE SETTLE NEVER ANNOUNCES; MANAGEMENT SURFACES
    MAY AFFORD."""
    bits: List[str] = []
    for target in _swap_targets(alt, candidates):
        disc = target["disclosure"]
        aria = f"Switch to {disc} — {name}"
        bits.append(
            f'<a href="#" data-alt={_e_attr(target["altitude"])} '
            f'data-disc={_e_attr(disc)} '
            f'aria-label={_e_attr(aria)} '
            f'onclick="flSwitch(this); return false;">'
            f'{_qualified_html(disc)}</a>')
    # THE "Instead:" LABEL RENDERS ONLY WHEN A NAMED TARGET DOES. `bits` holds
    # only named targets now, so this reads as the ruling states it: the label
    # asserts that alternatives exist, and rendering it bare is a false
    # statement (RECONVENE-2 §R2.3).
    prefix = f'{_e(labels.FOLLOW_INSTEAD_PREFIX)} ' if bits else ""
    bits.append(f'<button class="fl-unfollow" type="button" '
                f'aria-label='
                f'{_e_attr(f"{labels.FOLLOW_UNFOLLOW} {unfollow_name or name}")} '
                f'onclick="flUnfollow(this)">'
                f'{_e(labels.FOLLOW_UNFOLLOW)}</button>')
    return (f'<span class="fl-alts">{prefix}'
            + '<span class="sep">·</span>'.join(bits) + '</span>')


def _qualified_html(disclosure: str) -> str:
    """"Volkswagen (company)" -> name-bold + quiet class. The ONE compact
    qualifier render the server owns; the client's flQualified is its twin."""
    name, cls = follow_altitude.split_qualifier(disclosure)
    out = f'<strong>{_e(name)}</strong>'
    if cls:
        out += f' <span class="oq">({_e(cls)})</span>'
    return out


def _follow_slot_html(*, slot_id: str, mount: str, followed: bool,
                      committed_topic: str, story_topic: str, headline: str,
                      date: str, alt: Dict,
                      candidates: Optional[List[Dict]] = None) -> str:
    """THE ONE FOLLOW-LINE COMPONENT, mounted on a MANAGEMENT surface.

    Same node, same data-* contract and same client renderers as the card mount
    (_follow_control) — that IS the single-rendering law: one component, four
    mounts, and a state change on any of them renders through the same code.

    mount="deep"  the full form, always expanded: the state line + the acts line.
                  Not followed -> the resting CTA (the deep view is a follow
                  surface too, not only a management one).
    mount="row"   the Following row's ACTS-ONLY primary (his 07-25 blessing):
                  the ROW's own title is the object, so the state line would be
                  a second rendering of a fact the row already states.

    The memory stamp stays a SEPARATE node by design — the single-rendering law
    governs the follow-STATE node, not the continuity stamp (07-20 design)."""
    attrs = [f'id={_e_attr(slot_id)}', f'data-mount={_e_attr(mount)}']
    if headline:
        attrs.append(f'data-origin={_e_attr(headline)}')
    if story_topic:
        attrs.append(f'data-story={_e_attr(story_topic)}')
    if date:
        attrs.append(f'data-briefing-date={_e_attr(date)}')
    if not followed:
        aria = f"{labels.FOLLOW_THREAD_ARIA} — {story_topic}"
        attrs.append(f'data-topic={_e_attr(story_topic)}')
        return (f'<span class="follow-slot" {" ".join(attrs)} '
                f'data-state="resting">'
                f'<button class="deck-follow not-following" type="button" '
                f'aria-expanded="false" aria-label={_e_attr(aria)} '
                f'onclick="followTap(this)">'
                f'{_e(labels.FOLLOW_THREAD_INACTIVE)}</button></span>')
    name = _thread_display_name(alt, committed_topic)
    attrs.extend([
        f'data-topic={_e_attr(committed_topic)}',
        f'data-altitude={_e_attr(alt.get("altitude") or "")}',
        f'data-alt-label={_e_attr(alt.get("alt_label") or "")}',
        f'data-disclosure={_e_attr(alt.get("disclosure") or "")}',
    ])
    # FIX LOOP 1: the persisted candidates ride the slot so the CLIENT renders
    # the same "Instead:" set after a swap that the server rendered at load —
    # the single-rendering law applied to the new affordance. Stamped ONLY on
    # management mounts, because only management mounts call this function;
    # today cards go through _follow_control and never see it (ruling ②).
    if candidates:
        attrs.append(f'data-candidates={_e_attr(json.dumps(candidates))}')
    acts = _follow_acts_line(alt, name, candidates=candidates)
    if mount == "row":
        # the row's own title IS the object, and its accessible name matches
        # what the title renders (§3 aria law exemplar: "Unfollow Volkswagen
        # (company)" under a row headed "Volkswagen (company)").
        return (f'<span class="follow-slot" {" ".join(attrs)} '
                f'data-state="committed" data-object-slot="surface" '
                f'aria-live="polite">{acts}</span>')
    disclosure = (alt.get("disclosure") or "").strip()
    if disclosure and (alt.get("altitude") or "") != "narrow":
        object_html = _qualified_html(disclosure)
    else:
        # story-seeded (or unsettled): the thread wears its own name. The state
        # line says only what it knows — §1.1 forbids both referents naming the
        # same extension, and NL-17 M1 buried the row qualifier that used to
        # carry the scope fact.
        object_html = f'<strong>{_e(labels.FOLLOW_THREAD_SELF)}</strong>'
        # …and the Unfollow's accessible name follows the artifact's exemplar:
        # the deictic the button sits under, plus the named target, so a button
        # list never reads a bare "Unfollow" against an unnamed thread.
        acts = _follow_acts_line(
            alt, name, candidates=candidates,
            unfollow_name=f"{labels.FOLLOW_THREAD_SELF} — {story_topic or name}")
    sentence = (f'<span class="fl-sentence">'
                f'<span class="fl-dot" aria-hidden="true">'
                f'{_e(labels.FOLLOW_DOT_ON)}</span> '
                f'{_e(labels.FOLLOW_COMMITTED_VERB)} {object_html}</span>')
    return (f'<span class="follow-slot" {" ".join(attrs)} '
            f'data-state="expanded" aria-live="polite">'
            f'{sentence}{acts}</span>')


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


def _back_link(label: str, onclick: str) -> str:
    """The one-line back affordance — deep views, archive editions, thread page.

    NL-103 row 17 (B8, RATIFIED register): the VISIBLE label is the bare
    destination (`← Today` · `← This edition` · `← Archive` · `← Following`),
    matching the mockup. The ACCESSIBLE name names the destination and CONTAINS
    the visible label verbatim — §3 aria law / WCAG 2.5.3 label-in-name — so a
    reader who says "back to today" and a reader who hears the link agree.

    The aria name is DERIVED (drop the arrow, prefix "Back to") rather than
    tabled, for two reasons: a labels.py re-pin re-pins the accessible name with
    it (the table's one-place contract, gate FIX-2), and containment is exact by
    construction, so no future back label can silently lose or contradict its
    name. Every deep-back anchor in this module goes through here.
    """
    dest = label.lstrip("←").strip()
    if not dest:
        # QA-4 / gate FIX-6: an empty or arrow-only label would render
        # `aria-label="Back to "` — a nameless link, the exact failure this
        # derivation exists to prevent. Fail loudly at the re-pin instead.
        raise ValueError(f"back label must name a destination: {label!r}")
    return (f'<a class="deep-back" href="#" aria-label="Back to {_e(dest)}" '
            f'onclick="{onclick}">{_e(label)}</a>')


def _fmt_elapsed(seconds) -> str:
    """M:SS for a duration — the SAME shape webui.genFmt produces client-side and
    _wav_duration produces for episode length, so a step's seeded line and the
    line the client appends for the next step are typographically identical.
    Anything unreadable as a number renders as an em dash: an unknown duration
    says so rather than claiming 0:00.

    OverflowError is in the tuple deliberately (NL-149 QA F-4): `Infinity` is a
    token json.loads ACCEPTS and json.dumps EMITS for a float inf, so it
    round-trips through generation_log.jsonl by construction the day any writer
    records one — and `int(float("inf"))` raises OverflowError, an
    ArithmeticError, which the value/type pair never covered. This helper is on
    the page-build path, so that was HTTP 500 rather than a bad cell."""
    try:
        s = int(max(0.0, float(seconds)))
    except (TypeError, ValueError, OverflowError):
        return "—"
    return f"{s // 60}:{s % 60:02d}"


def _gen_log_line(step: Dict) -> str:
    """One FINISHED step as one line of the generating log (NL-149 item 1).

    Kept in server.py rather than composed in the client because the seeded
    lines (a reload mid-run) and the appended lines (the poll) must be the same
    markup — two renderers for one line is how the reloaded half of a log starts
    looking different from the live half. The client mirrors this shape; the
    pin (test) renders both and compares."""
    label = _e(str(step.get("label") or ""))
    model = step.get("model")
    model_html = (f'<span class="gen-log-model"> · {_e(str(model))}</span>'
                  if model else "")
    return (f'<li><span class="gen-log-step">{label}</span>{model_html}'
            f'<span class="gen-log-time">{_e(_fmt_elapsed(step.get("elapsed_s")))}</span></li>')


def _failure_outcome(con: sqlite3.Connection, row=None) -> str:
    """Which of the three post-failure positions the reader is in.

    Factored out of _render_today at Stage-0 C1 so the Commissioning's own
    failure panel states the SAME fact from the SAME predicate — the founding
    page and the app must never disagree about whether anything was published.
    Behaviour is unchanged: same order, same strings, same readability
    predicate (`_stories_for`, the edition renderer's own, reused and never
    re-derived), still read for TODAY because GEN_JOB always generates today."""
    _today = datetime.now().strftime("%Y-%m-%d")
    _saved = (row if (row is not None and row["date"] == _today)
              else _briefing_row(con, _today))
    if _saved is None:
        return "Nothing was published."
    if _stories_for(_saved, _log_entry_for(_today))[0]:
        return "The saved edition is intact."
    return "The saved edition is empty."


# How far back the failure-morning door looks for something readable. THIRTY,
# and deliberately the same number as `_RUNLOG_MAX_ROWS` above — the reports
# screen's depth is the depth the product already tells him it remembers, so the
# door and the screen agree about how much history is in view. Tied by intent,
# not aliased: they are two different promises that happen to want one depth,
# and either could move without the other (QA F-5 / gate R-C, 2026-08-14).
_LAST_EDITION_SCAN_ROWS = 30


def _last_generated_edition(con: sqlite3.Connection,
                            before_date: str) -> Optional[Dict]:
    """The newest edition STRICTLY BEFORE `before_date` that a reader can
    actually open, or None. {"date", "human"}.

    READABLE AND NOT MERELY PRESENT — the same predicate the scheduler's
    idempotence gate uses (`_stories_for` non-empty, schedule.published_edition_
    exists), reused rather than re-derived. A briefings ROW exists from the rank
    stage onward with nothing behind it, so a row-existence test would have
    offered him a button that opens an empty page on exactly the mornings this
    note exists for.

    STRICTLY BEFORE, because this is only ever called from the today-is-absent
    branch: today's own row is by construction the unreadable one, and offering
    "the last generated edition" that resolves to today would be the button
    contradicting the sentence above it.

    BOUNDED AT `_LAST_EDITION_SCAN_ROWS`, and the bound is spent on ROWS, which
    is why the old five was wrong (QA F-5 / gate R-C, 2026-08-14). A scheduled
    morning that failed after the rank stage LEAVES a row with nothing behind
    it, so five failed mornings consumed the whole window and the button
    disappeared on exactly the morning it was built for, with a readable edition
    sitting at row six. The door's whole job is to survive a run of failures.

    THE COST LINE THAT JUSTIFIED FIVE IS GONE, not just outvoted: it said each
    candidate is "a full read of a log that is ~700KB", but NL-154 made
    `_log_entry_for` a bounded read of the LIVE segment with the archives
    touched only on a miss, so the per-candidate cost no longer grows with the
    archive. The loop below also returns on the first READABLE row, so the deeper
    bound is paid only in the failure run it exists for."""
    try:
        rows = con.execute(
            "SELECT * FROM briefings WHERE date < ? ORDER BY date DESC LIMIT ?",
            (before_date, _LAST_EDITION_SCAN_ROWS)).fetchall()
    except Exception:      # noqa: BLE001 — a missing table is "nothing to
        return None        # offer", never a 500 on the failure morning itself
    for r in rows:
        try:
            if _stories_for(r, _log_entry_for(r["date"]))[0]:
                return {"date": r["date"], "human": _human_date(r["date"])}
        except Exception:  # noqa: BLE001 — one unrenderable edition is not a
            continue       # reason to withhold an older one that works
    return None


def _scheduled_failure_note(entry: Optional[Dict],
                            last: Optional[Dict] = None) -> str:
    """NL-146 item 4 — the quiet note for a run nobody was awake to watch.

    A scheduled run fires from launchd in ANOTHER PROCESS, so when it fails
    there is no GEN_JOB error panel in this one and no terminal anybody was
    looking at. The only witness is the log entry that run wrote on its way out.
    This renders that entry and nothing else.

    SILENT IN EVERY OTHER CASE, deliberately:
      * an INTERACTIVE failure gets the error panel — he watched it happen, and
        a second note on the same fact is nagging, not informing;
      * a successful run has nothing to report;
      * every run already in his log predates this key and carries no `trigger`
        at all, so they make no claim either way and get no note (NL-149's
        precedent: an absent field is unrecorded, never inferred). NO COUNT IS
        STATED HERE, deliberately (fix loop 1, QA F-6): the figure this prose
        used to carry was never measured, and a hand-baked one goes stale the
        day after it is written. The property is what matters and it is
        checkable — `grep -c '"trigger":' data/generation_log.jsonl`. THE KEY
        SHAPE IS THE CHECK (gate FIX-3): bare `grep -c trigger` counts the WORD,
        which story prose in `draft_stories`/`stories` also contains — measured 8
        on his real log today, where the property's true count is 0. An offered
        check that measures the wrong thing is the same class of defect as the
        hand-baked figure this prose stopped carrying.

    QUIET IS A DESIGN WORD HERE, not a hedge: `empty-note`, no role="alert", no
    colour. The reader opening at 07:00 to an absence needs the reason and the
    way forward in two sentences, not an incident report."""
    if not isinstance(entry, dict):
        return ""
    if entry.get("trigger") != schedule.TRIGGER_SCHEDULED:
        return ""
    if entry.get("status") != "failed":
        return ""
    if entry.get("retryable"):
        # The systemic fetch pause. NL-148 pins that nothing was published and
        # nothing completed at the instant it fires, so "nothing was charged" is
        # a fact this surface may state, not a reassurance it is offering.
        #
        # BUT IT IS STATED FROM THE RECORD, NOT FROM THE PIN (gate FIX-2). The
        # claim used to be unconditional, and NL-146's own budget guard creates
        # the world where it is false: the ladder stands down on a retryable
        # failure that DID charge, so that entry carries `retryable: true` AND a
        # non-zero `total_usd` — and this note would render the opposite of the
        # same JSON line it is reading. A surface contradicting its own source
        # is worse than a quiet one, because it gives him no reason to look.
        #
        # `_charged_for`'s float guard, with ONE deliberate difference: that
        # reader coerces an unparseable figure to 0.0 because a ladder must
        # decide something; this one makes NO claim, because "we could not read
        # what was spent" is not "nothing was spent" and prose has the option of
        # saying less.
        try:
            charged = float(entry.get("total_usd") or 0.0)
        except (TypeError, ValueError):
            charged = None
        why = "nothing could be fetched from your sources. Nothing was published"
        why += " and nothing was charged." if charged == 0.0 else "."
    else:
        why = "the run stopped before it could publish anything."
    note = ('<p class="empty-note" style="margin-top:1rem;">'
            "This morning's scheduled edition didn't finish — " + _e(why)
            + "</p>")

    # NL-153 — THE FAILURE-MORNING DOOR (his copy, verbatim).
    #
    # OFFERED, NEVER TAKEN. The note still says the absence first; this is a
    # second affordance under it, not a redirect. Nothing auto-opens, nothing
    # substitutes: the reader who wants today's absence to stand simply doesn't
    # press it. That is the trust-quiet register — same `empty-note` prose, same
    # `cta-quiet` button the empty state already uses, no alert role, no colour.
    #
    # NL-11'S LAW IS THE CONSTRAINT AND IT IS SATISFIED TWICE OVER: the date is
    # named in the sentence ABOVE the button in both human and ISO form, so the
    # offer is unambiguous before it is taken, and the destination fragment
    # leads with `_human_date(row["date"])` as its view title, so the edition
    # cannot be mistaken for today's after it opens either. An older edition is
    # never dressed as current — it is labelled as what it is at both ends.
    #
    # ABSENT WHEN THERE IS NOTHING TO OFFER (v4 affordance-absence law): `last`
    # is None on a first-week profile, and a button whose only outcome is an
    # empty page is worse than no button.
    if isinstance(last, dict) and last.get("date"):
        note += (
            '<p class="empty-note" style="margin-top:0.75rem;">'
            f'The last edition NewsLens generated is from {_e(last.get("human") or "")}'
            f' ({_e(last["date"])}).</p>'
            f'<button class="cta-quiet" style="margin-top:0.5rem;"'
            f' onclick="return openEdition(\'{_e(last["date"])}\', event)">'
            'Read last generated edition</button>')
    return note


def _in_flight_elsewhere() -> Optional[Dict]:
    """The generation running in ANOTHER process right now, or None.

    Two things make this the render path's reader rather than
    `schedule.read_in_flight` directly:

    * `reap=False` — a GET must not delete a file. The staleness JUDGEMENT still
      runs (a dead marker reads as None here exactly as it does anywhere else);
      what is withheld is the unlink, which belongs to the writers' path. A page
      render that mutates the data dir is the class of thing the no-real-state-
      writes rule exists for, and "it was only a cleanup" is how that rule gets
      eroded.
    * OUR OWN marker is not "elsewhere". When this process is the one
      generating, `gen_state["state"]` is `running` and the live panel — with
      its stage, its clock and its step log — is strictly better information
      than a timestamp read off disk.
    """
    try:
        held = schedule.read_in_flight(reap=False)
    except Exception:  # noqa: BLE001 — a marker must never break a page render
        return None
    if held is None or held.get("pid") == os.getpid():
        return None
    return held


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

    # NL-146 item 3 — THE HALF-EDITION HOLE, CLOSED.
    #
    # A ROW IS NOT AN EDITION. `ranking.persist` commits the date's row at the
    # RANK stage and the body lands last, at `generate.persist_generation`, so
    # for the whole post-rank window a row exists with nothing readable behind
    # it. Until now that window was covered by the GEN_JOB panels — but GEN_JOB
    # is THIS process's job, and a scheduled run fires from launchd in ANOTHER
    # process. A 6am run that died after rank therefore left the job idle, both
    # panels unreachable, and the `else` arm below rendering the masthead
    # ceremony over an empty grid: a dateline announcing today's edition with no
    # edition under it, no explanation, and no way to retry.
    #
    # MEASURED, not reasoned about: at 1f92260 that page was 1547 bytes of Today
    # view, one heading (the dateline), and no state panel at all.
    #
    # The predicate is `_stories_for` — the edition renderer's own, reused and
    # never re-derived (the NL-103 row-9 law) — so what this branch claims and
    # what the body would render cannot disagree.
    readable = row is not None and bool(_stories_for(row, entry)[0])
    # ONE read of the marker per render — the panel below needs both the fact
    # and the timestamp, and stat'ing the same file twice in one page is how a
    # render answers from two different worlds.
    inflight = None if readable else _in_flight_elsewhere()

    head = (_masthead(None if (running_or_error or not readable) else row,
                      mast_date)
            + _section_line("today"))

    if gen_state["state"] == "running":
        # NL-88: a LIVE status line (stage label + model + a ticking clock)
        # replaces the old hardcoded ladder that always showed "ingest active"
        # — a full edition runs ~40 min, and a featureless panel read as
        # "stuck" (the 2026-07-18 incident). Seeded server-side from the
        # current snapshot; webui.js updates it each poll and ticks the clock
        # between polls.
        _stage_label = gen_state.get("stage") or "Starting…"
        _stage_model = gen_state.get("stage_model") or ""
        _model_suffix = f" · {_e(_stage_model)}" if _stage_model else ""
        _total0 = gen_state.get("total_elapsed_s") or 0
        _stage0 = gen_state.get("stage_elapsed_s") or 0
        # NL-149 item 1: the finished steps, server-rendered so the log SURVIVES
        # A RELOAD. The client appends to this same <ol> as later steps finish;
        # data-count is the cursor it appends from, so a reload mid-run picks up
        # exactly where the previous DOM left off instead of starting empty or
        # double-printing.
        #
        # data-run IS THE CURSOR'S IDENTITY (NL-149 QA F-3). A cursor alone says
        # HOW MANY lines are drawn, never WHICH RUN drew them, and append-only
        # then delivers something worse than a vanished line across a run
        # boundary: a line that is still on screen and is now false. The path is
        # not exotic — pollGeneration's `.catch` retries every 4s and
        # deliberately does NOT reload (a blip must not throw the page away), so
        # a tab left open through a restart reconnects to whatever run is in
        # flight, computes `have = 4` against `steps: []`, draws nothing, and
        # leaves run A's four lines under run B's clock while B's first four
        # boundaries are never drawn. `started_at` is already on the snapshot,
        # so the identity costs no new key and no route change.
        _done_steps = gen_state.get("steps") or []
        _log_html = "".join(_gen_log_line(s) for s in _done_steps)
        # NL-103 row 18: the stage tour ("Fetching your sources, ranking,
        # writing, editing, and recording the episode.") DIED — the live status
        # line below states the stage it is actually in, so enumerating the
        # pipeline in prose narrates the interface and duplicates the fact.
        # Kept: the duration fact, the pointer to the live status, the refresh
        # fact.
        body = f"""
<div class="state-panel" id="gen-running">
  <h2>Generating today’s edition…</h2>
  <p>A full edition takes a while — the live status below shows exactly where
     it is; the page refreshes itself when it’s ready.</p>
  <ol class="gen-log" id="gen-log" data-count="{len(_done_steps)}" data-run="{_e(gen_state.get("started_at") or "")}">{_log_html}</ol>
  <p class="gen-live" id="gen-live" data-total="{_total0}" data-stage-el="{_stage0}">
    <span class="gen-live-stage" id="gen-live-stage">{_e(_stage_label)}</span><span class="gen-live-model" id="gen-live-model">{_model_suffix}</span>
    <span class="gen-live-clock" id="gen-live-clock"></span>
  </p>
</div>"""
    elif gen_state["state"] == "error":
        # NL-103 row 9: the invariant narration ("No half-written edition ever
        # goes out: a failure before the save publishes nothing; one during file
        # export after the save leaves the saved edition intact.") DIED — it
        # recited the system's guarantee in BOTH positions instead of stating
        # this run's fact. The panel states which position the reader is in.
        # The subject is TODAY's edition (the panel's own h2) and GEN_JOB always
        # generates today, so the fact is read for today's date — never for
        # whatever date the page was addressed with (?date= renders an archive
        # day inside this view). The already-resolved row is reused when it IS
        # today's, so the common path adds no query.
        # THREE states, not two (gate FIX-1 / QA-1, 2026-07-26). Row existence
        # is NOT publication: ranking.persist() commits today's row at the rank
        # stage and a re-rank NULLs the body on the live row, while the body
        # UPDATE lands last (generate.persist_generation) — so a row exists for
        # the whole post-rank window with nothing readable behind it, and a
        # failed run's log entry carries no `stories` fallback either. The
        # readability predicate is therefore the EDITION RENDERER'S OWN
        # `_stories_for` — reused, never re-derived — so the panel and the body
        # the reader can open cannot disagree. Pinned by
        # tests/test_nl103_row9_acceptance.py (claim == what /edition returns).
        _outcome = _failure_outcome(con, row)
        body = f"""
<div class="state-panel">
  <h2>Today’s edition failed</h2>
  <p class="error-text">{_e(gen_state["error"])}</p>
  <p>{_e(_outcome)}</p>
  <button class="cta-quiet" onclick="generateAgain()">Try again</button>
</div>"""
    elif inflight is not None:
        # NL-146 fix loop 1 (QA F-2) — "NOTHING YET" WAS A FALSE STATEMENT FOR
        # ~30 MINUTES EVERY MORNING.
        #
        # A launchd fire runs in ANOTHER process. GEN_JOB — the only run-state
        # this page had — is this process's job, so through the whole scheduled
        # run Today rendered the empty state: "No edition has been generated for
        # today", beside a Generate button, while an edition was being generated
        # for today. The reader's rational response to that screen is to press
        # the button, which is exactly the double spend the door now refuses.
        #
        # This arm sits AFTER the running/error panels (an in-process run is
        # better information than a marker on disk, and it can actually show
        # live progress) and BEFORE the empty state, whose claim it contradicts.
        # Deliberately NOT shown when the edition IS readable: a re-generation
        # over a published day is not an absence and needs no explaining.
        #
        # NO GENERATE BUTTON. The door would refuse it; offering an affordance
        # whose only outcome is a refusal is the v4 affordance-absence law's
        # exact case.
        _when = _utc_hm(inflight.get("started_at"))
        body = f"""
<div class="state-panel">
  <h2>{_e(labels.INFLIGHT_TITLE)}</h2>
  <p>{_e(labels.INFLIGHT_BODY.format(when=_when) if _when
         else labels.INFLIGHT_BODY_NO_TIME)}</p>
</div>"""
    elif not readable:
        # NL-11: no edition for TODAY -> the empty state, never an older edition
        # dressed as current. If the archive has earlier editions, point there.
        #
        # NL-146 WIDENED THE CONDITION FROM `row is None` TO `not readable`, and
        # the two are the same fact to a reader: a row whose body never landed
        # is nothing to read. The narrow test let the post-rank window through to
        # the edition renderer (see the `readable` note above). Everything below
        # is unchanged — same panels, same words, same Archive pointer — plus the
        # quiet scheduled-failure note, which renders only when the last recorded
        # run for this date was a SCHEDULED failure.
        has_archive = con.execute(
            "SELECT 1 FROM briefings LIMIT 1").fetchone() is not None
        # NL-153: the last-edition lookup is done ONLY on the arm that can
        # render the note (`has_archive` is already computed here, and it is
        # exactly the precondition for there being anything to offer), so the
        # ordinary morning pays nothing for it.
        sched_note = _scheduled_failure_note(
            entry, _last_generated_edition(con, mast_date) if has_archive
            else None)
        # NL-103 row 18: both panels lose the pipeline enumeration ("it fetches
        # your sources, picks the stories, writes the briefing, and records the
        # episode") — a tour of machinery the reader did not ask for. What stays
        # is what a first run needs: the absence, the duration, and the act (plus
        # the Archive pointer where earlier editions exist).
        # The duration is "about half an hour" (gate FIX-3, 2026-07-26): the
        # ratified "a couple of minutes" was off by an order of magnitude on six
        # of six logged runs. Soft-figured on purpose — honest across the taxed
        # era (~40 min) and the post-NL-99 regime (~20 min, n=1). The h2
        # "Nothing yet"
        # below is NOT the bare empty state §3 bans — its own panel body names
        # the class in the next sentence.
        if has_archive:
            body = f"""
<div class="state-panel">
  <h2>Nothing for today yet</h2>
  <p>No edition has been generated for today. Generating one takes about half
     an hour.</p>
  <button class="cta-quiet" onclick="generateAgain()">Generate today’s edition</button>
  {sched_note}
  <p class="empty-note" style="margin-top:1rem;">Earlier editions are in your
     <a href="#" onclick="showView('archive'); return false;">Archive</a>.</p>
</div>"""
        else:
            body = f"""
<div class="state-panel">
  <h2>Nothing yet</h2>
  <p>No edition has been generated. Generating one takes about half an
     hour.</p>
  <button class="cta-quiet" onclick="generateAgain()">Generate today’s edition</button>
  {sched_note}
</div>"""
    else:
        # NL-11: the glance ("In today’s briefing") section is REMOVED. The lead
        # story opens the reading surface.
        body = _render_briefing_body(con, row, entry, briefs, "", "view-today")

    return head + f'<div class="page">{body}</div>'


def _grid_est(st: Dict, role: str) -> float:
    """Rough content-height estimate (arbitrary units) for one grid slot, by
    role. Deterministic, off headline+lede+movement word counts. Shared by the
    column-balance heuristic (_grid_columns) and the row placement
    (_grid_row_spans) so both read the same proxy — a bad estimate yields a
    slightly uneven bottom, NEVER a broken page or a reordered DOM. Missing
    fields never raise (str()/.get defaults)."""
    words = len((str(st.get("headline", "")) + " "
                 + str(st.get("lede", ""))).split())
    words += sum(len(str(m.get("text", "")).split())
                 for m in (st.get("movements") or []) if isinstance(m, dict))
    if role == "lead":
        return 8.0 + words / 9.0
    if role == "story":
        return 4.0 + words / 8.0
    return 4.0                              # strips clamp to a ~constant height


def _grid_columns(grid_stories: Dict[int, Tuple]) -> Dict[int, str]:
    """v8-M2 strip GROUT balance (governing amendment, principal 2026-07-18):
    the strips (#4..N — however many the edition has) slot into whichever column
    is currently SHORTER, evening the bottom edge into a newspaper-like
    rectangle. Returns {index: 'a'|'b'} for the strip indices only (the lead is
    always column a, the medium cards always column b).

    PRESENTATION ONLY — the DOM stays rank-ordered 1→N (grid-column CSS classes
    place the slots; screen readers hear rank order). Deterministic, server-side,
    off a rough content-length estimate: a bad estimate yields a slightly uneven
    bottom, NEVER a broken page or a reordered DOM. The earlier 'strips under the
    lead' sketch is the special case where the lead ran shorter than #2+#3 and
    the left column was the shorter one. Chosen the amendment's sanctioned SIMPLE
    version (greedy shorter-column-first; no clever bin-packing, no cross-column
    bottom band) over a clever one."""
    left = right = 0.0
    for st, _slot, _tier, role in grid_stories.values():
        if role == "lead":
            left += _grid_est(st, role)
        elif role == "story":
            right += _grid_est(st, role)
    assign: Dict[int, str] = {}
    for i in sorted(grid_stories):
        st, _slot, _tier, role = grid_stories[i]
        if role != "strip":
            continue
        if left <= right:
            assign[i] = "a"
            left += 4.0
        else:
            assign[i] = "b"
            right += 4.0
    return assign


def _grid_row_spans(grid_stories: Dict[int, Tuple],
                    cols: Dict[int, str]) -> Dict[int, str]:
    """v8-M2 FIX-1 — the RECTANGLE-squaring row placement (principal 2026-07-18):
    the server-computed generalization of the approved mockup's grid-areas
    mechanism (design/mockup-v8.html lines 207-219). Returns {index:
    "<start> / <end>"}, a CSS grid-row per slot, emitted by the caller as the
    --gr custom property so the ≤900px single-column stack (which resets
    grid-row to auto) is untouched; the placement applies at ≥900px only.

    THE MECHANISM: the two presentation columns — left = the lead then its
    balance-assigned strips; right = the medium cards then theirs, both in rank
    order — are laid end-to-end against a SHARED set of row lines derived from
    each slot's rough content height (_grid_est). Each slot spans the lines its
    cumulative-height band covers, so a tall right card (#3) spans DOWN across
    the lines its left-column strips occupy (the mockup's s3 trick, computed) and
    the lead's span is COMPUTED, not the retired fixed `grid-row: 1 / span 2`
    that stranded a void beside cards which outran a shorter lead. Auto-sized
    rows mean a bad estimate yields a ragged bottom, NEVER overlap or a reordered
    DOM.

    Degenerate floor: a lone lead / no strips / an all-quick right column still
    gets valid spans (each slot simply spans its own band); page-safety is the
    law. PRESENTATION ONLY — placement, never a DOM reorder."""
    if not grid_stories:
        return {}
    left_seq: List[int] = []
    right_seq: List[int] = []
    for i in sorted(grid_stories):
        _st, _slot, _tier, role = grid_stories[i]
        if role == "lead":
            left_seq.append(i)
        elif role == "story":
            right_seq.append(i)
        else:                               # strip: placed by the balance class
            (left_seq if cols.get(i, "a") == "a" else right_seq).append(i)

    # Cumulative-height bands per column, off the shared _grid_est proxy. Each
    # item's [lo, hi) rounds to collapse float drift so consecutive same-column
    # items share an exact edge (contiguous, non-overlapping) and a left/right
    # pair that lands at the same height shares one row line.
    band: Dict[int, Tuple[float, float]] = {}
    for seq in (left_seq, right_seq):
        acc = 0.0
        for i in seq:
            st, _slot, _tier, role = grid_stories[i]
            lo = round(acc, 3)
            hi = round(acc + _grid_est(st, role), 3)
            band[i] = (lo, hi)
            acc = hi

    # Map every distinct band edge (both columns share the row-line space) to a
    # 1-based CSS grid line. A slot's grid-row is line(lo) / line(hi).
    edges = sorted({0.0} | {e for lohi in band.values() for e in lohi})
    line = {v: n + 1 for n, v in enumerate(edges)}
    return {i: f"{line[lo]} / {line[hi]}" for i, (lo, hi) in band.items()}


def _brief_slug_heads(grid_stories: Dict[int, Tuple],
                      cols: Dict[int, str]) -> Dict[int, str]:
    """NL-143 item 3a — which strips head an In-Brief run.

    Returns {index: "first"|"secondary"}: ONE slug per COLUMN LEG, placed on
    that leg's lowest-ranked strip. "first" is the rank-first leg (the only one
    that survives the <=900px stack, where the strips become contiguous by rank
    and a second slug would read as a stutter mid-run).

    WHY PER COLUMN LEG AND NOT PER CONTIGUOUS RUN. The 07-18 design round said
    "per contiguous strip run" and worked its example on the assumption that a
    column leg IS a run (07-10: slugs above #4 and #7), auditing the busy-guard
    at "one two-word slug per run, <=2 per page" — which is what the polish gate
    approved. But the balance in _grid_columns is greedy shorter-column-first
    and adds a constant per strip, so once the two columns level out the
    assignment ALTERNATES a,b,a,b: every strip becomes its own contiguous run,
    the page grows a slug above each one, and the approved section label
    silently becomes the per-card label his own charge ruled out. Per column
    leg reproduces the round's worked example exactly, holds the <=2 the gate
    approved against, and cannot degrade into per-card labelling for any
    edition shape.

    PRESENTATION ONLY, and deliberately downstream of the grid: this reads the
    balance assignment and never feeds it. The rectangle machinery
    (_grid_columns / _grid_row_spans / the 7fr/5fr law) is not touched, so a
    slug can shift nothing about where a story lands."""
    heads: Dict[int, str] = {}
    legs: List[str] = []
    for i in sorted(grid_stories):
        if grid_stories[i][3] != "strip":
            continue
        col = cols.get(i, "a")
        if col in legs:
            continue
        legs.append(col)
        heads[i] = "first" if len(legs) == 1 else "secondary"
    return heads


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
    # NL-145: resolved ONCE per edition beside `active`, same reason. The two
    # sets answer different questions and both are needed: `active` decides
    # RECOGNITION (is this story's own title a live follow), `held` decides
    # whether a stored MARK still describes something the reader follows.
    held = _held_topics_lower(con)
    # NL-134 F3: resolved ONCE per edition, not once per story — the why-chosen
    # line needs outlet names to credit a followed writer, and the slot only
    # carries a bool.
    followed_writers = _followed_writer_outlets()

    # BUG-35: one dedup set per EDITION — a same-thread arc line renders under
    # its most prominent (earliest) slot only; a split-day sibling suppresses
    # the identical continuity paragraph.
    arc_seen: set = set()
    still_lines: List[str] = []        # still-tracking register (below the grid)
    grid_stories: Dict[int, Tuple] = {}   # i -> (st, slot, tier, role)
    # §12.3 slot routing. Ids/tiers use the ORIGINAL enumerate index so
    # _collect_deep_views stays aligned (a still-tracking slot consumes its
    # index but gets no deep view — it is a status line, not a story).
    # NL-151b: the reader side was ALREADY vector-driven — `tiers` is the run
    # record's own `tiers` key, which is the writer's shipped tier per story,
    # which after a demotion is ["quick", "full", "medium", ...]. So this loop
    # needed no change to honour arm (ii); the fallback below is the positional
    # A2 contract for entries that carry no vector (pre-NL-63 editions), and it
    # now names the one shared definition instead of open-coding a fourth copy.
    from . import analysis as analysis_mod
    _positional = analysis_mod.positional_tiers(len(stories))
    for i, st in enumerate(stories):
        slot = slots[i] if i < len(slots) else {}
        tier = tiers[i] if i < len(tiers) else _positional[i]
        if slot.get("still_tracking"):
            line = _still_tracking_line(slot)
            if line:
                still_lines.append(line)
            continue
        # v8-M2 tiers → grid roles: lead (i==0), medium cards (right column),
        # quick-tier strips (the grout). The v8-M2 "In brief" labelled REGION
        # stays dead — nothing wraps the strips — but NL-143 restores the label
        # itself as a per-leg slug on the run-head strip (_brief_slug_heads).
        role = "lead" if i == 0 else ("strip" if tier == "quick" else "story")
        grid_stories[i] = (st, slot, tier, role)

    # v8-M2: the newspaper grid. All slots are DIRECT children of .today-grid in
    # rank/DOM order 1→N (screen readers hear rank); grid-column classes place
    # them — lead left (spanning), cards right, strips balanced across the
    # bottom to square the rectangle. No wrapper column (that would break DOM
    # rank order); no "In brief" WRAPPER — NL-143's slug rides INSIDE the
    # run-head strip precisely so this stays true and the grid math is untouched.
    cols = _grid_columns(grid_stories)
    rows = _grid_row_spans(grid_stories, cols)     # FIX-1: computed row placement
    slugs = _brief_slug_heads(grid_stories, cols)  # NL-143: the In-Brief slug
    grid_html: List[str] = []
    for i in sorted(grid_stories):
        st, slot, tier, role = grid_stories[i]
        if role == "lead":
            grid_cls = " grid-lead"
        elif role == "story":
            grid_cls = " grid-col-b"
        else:                               # strip: balanced column a or b
            grid_cls = " grid-col-" + cols.get(i, "a")
        grid_html.append(_render_story(
            i, st, slot, tier, active, has_file=(i + 1) in (briefs or {}),
            slug=f"{slug_prefix}story-{i}", date=row["date"],
            deep_return=deep_return, con=con, arc_seen=arc_seen, role=role,
            grid_cls=grid_cls, grid_row=rows.get(i, ""),
            followed_writers=followed_writers,
            brief_slug=slugs.get(i, ""), held_topics=held))

    still_html = ""
    if still_lines:
        still_html = (
            f'<div class="still-tracking" role="region" '
            f'aria-label={_e_attr(labels.STILL_TRACKING_PREFIX)}>'
            + "".join(still_lines) + "</div>")
    grid = f'<div class="today-grid">{"".join(grid_html)}</div>{still_html}'

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


def _thread_name_link(tid: int, topic: str, tag: str = "h2",
                      qualifier: str = "", derived: bool = False) -> str:
    """The thread NAME as a Following row's single action (Design's ruling —
    extends the §12.5 fold grammar to the loud updated rows too): a link to the
    thread page (openThread). Accessible name = the topic (distinguishable across
    19+ rows, §12.5 'label = accessible name'); the shared 'fallback control
    label' labels.THREAD_WHOLE rides as the control's title so the row's single
    action is named from the label table. The name is a real heading so AT can
    navigate the thread list. NL-17-M1b: the altitude qualifier rides INSIDE the
    link (the accessible name carries the class — Kass's disclosure).

    NL-17 M1 / F-6 — `derived=True` stamps this heading as FOLLOW-STATE-DERIVED
    CHROME. That is the gate's finding stated precisely: the cross-mount sweep
    truthened the row's follow-SLOT and left the row's h2 saying the old name
    after a rename, because the h2 is not a slot and nothing walked it. It is
    still derived — the name and its qualifier are both read off the follow — so
    it gets the identity keys the sweep matches on (data-topic / data-story, the
    same names and the same types the slots use, so ONE predicate serves both)
    and a marker attribute the sweep selects on. Stamped only on the LOUD
    updated rows, which is where a live settle-rename can land while the reader
    is looking at it; the quiet-fold and lifecycle rows are re-rendered from the
    server on their next open."""
    stamp = ""
    if derived:
        stamp = (f' data-follow-name="row" data-topic={_e_attr(topic)}'
                 f' data-story={_e_attr(topic)}')
    return (f'<{tag} class="thread-name"{stamp}><a href="#" '
            f'onclick="openThread(\'{tid}\', event); return false;" '
            f'title={_e_attr(labels.THREAD_WHOLE)}>{_e(topic)}{qualifier}</a></{tag}>')


def _thread_row_link(tid: int, topic: str, qualifier: str = "") -> str:
    """The compressed-row variant (quiet fold + lifecycle rows): the name as a
    plain link (not a heading — 17 quiet names as headings would flood the
    heading list), same single-action grammar and label. NL-17-M1b: the altitude
    qualifier rides inside the link (accessible name carries the class)."""
    return (f'<a href="#" onclick="openThread(\'{tid}\', event); return false;" '
            f'title={_e_attr(labels.THREAD_WHOLE)}>{_e(topic)}{qualifier}</a>')


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
    name = _thread_name_link(t["id"], t["topic"], tag="h2",
                             qualifier=_altitude_qualifier_html(t),
                             derived=True)   # F-6: swept chrome
    delta_html = (f'<p class="thread-delta">{_e(d.get("what_happened", ""))}</p>'
                  if d.get("what_happened") else "")
    note = (t.get("note") or "").strip()
    note_html = f'<p class="thread-note">{_e(note)}</p>' if note else ""
    # NL-17-M1c — MOUNT 4: the Following row's ACTS-ONLY primary (his 07-25
    # blessing). The row's own title is the object, so the state line would be a
    # second rendering of a fact the row already states; the acts line IS the
    # row's one inline action cluster.
    acts = _following_row_follow_line(t)
    return (f'<article class="thread">{stamp}{name}{delta_html}'
            f'{note_html}{acts}</article>')


def _following_row_follow_line(t: Dict) -> str:
    """A Following row's follow-line mount — the same component, acts-only.

    `t` carries its own `settle_candidates` (attached in `_following_rows`,
    which is where the connection is). Fetching them here instead would mean
    this function needed a `con` it has never had, and every caller threading
    one — the row builder already reads the record once per row, so the
    candidates ride along."""
    return ('<div class="follow-line">' + _follow_slot_html(
        slot_id=f"follow-row-{t['id']}", mount="row", followed=True,
        committed_topic=t["topic"], story_topic=t["topic"], headline="",
        date="", alt=t,
        candidates=t.get("settle_candidates") or []) + "</div>")


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
        elif t.get("followed_on"):
            # v8-M1 item 5: empty thread — the honest date is the follow's birth,
            # stamped FOLLOWED (never LAST UPDATED off a date with no coverage).
            stamp = (f' <span class="q-stamp">{_e(labels.FOLLOWED)} '
                     f'{_e(_human_short(t["followed_on"]).upper())}</span>')
        rows.append(
            f'<li class="q-row">'
            f'{_thread_row_link(t["id"], t["topic"], _altitude_qualifier_html(t))}'
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
            f'{_thread_row_link(t["id"], t["topic"], _altitude_qualifier_html(t))} '
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
            # NL-103 row 20: bare "Nothing yet" DIED here. The only adjacent
            # text is the group name ("Broad (0)"), a <p> with no heading
            # semantics and no aria linkage — no programmatic section context —
            # so the class noun rides in-string. It carries the group adjective
            # too (the register's own row-16 vocabulary): under "Broad (0)" a
            # flat "No topics yet" would read as "no topics at all" for a reader
            # who has specific ones, which is not what IS.
            topics.append(f'<p class="empty-note">No {label.lower()} topics '
                          f'yet</p>')
        topics.append("</div></div>")

    # NL-68 item 14: the "Suggestions recall writers the system already knows…"
    # interface narration DIES; the functional facts (a follow adds their feed;
    # a new writer needs a feed link) stay — they tell the user what an action
    # does and what it requires, not "here's what you're looking at".
    writers = [
        _render_suggest("writer", "writer-suggest", "Search or add a writer…",
                        "Search or add a writer", _writer_suggestions(cfg)),
        # NL-103 row 7 (C6): the ranking clause ("boosts their pieces in
        # ranking") DIED — system explanation, and "ranking" is internal
        # vocabulary. Two facts survive, one sentence each: what a follow DOES,
        # and what adding a new writer REQUIRES.
        # HONEST-BASIS NOTE (register convention): the ranking effect is real
        # (ranking.FOLLOWED_BOOST) and now goes UNDISCLOSED. That is a ruled
        # silence with an armed falsifier (§6.6) — if testers are confused why a
        # followed writer's pieces dominate, the fact returns in register form.
        # It reverses the NL-68 item-14 boundary pin, which the ratified register
        # supersedes.
        '<p class="token-search-hint">Following a writer adds their feed to '
        'your sources. Adding someone new takes their feed link.</p>',
        '<div class="token-group"><div class="token-list">',
    ]
    followed = cfg.followed_analyst_sources
    if followed:
        for s in followed:
            m = re.match(r"^(.*)\s+\((.+)\)\s*$", s.name)
            display = f"{m.group(2)} — {m.group(1)}" if m else s.name
            writers.append(token(s.name, "writer", label=display))
    else:
        # NL-103 row 20: this group has no name row at all (the div above is a
        # bare token-group/token-list pair) — nothing programmatic to sit under,
        # so the class noun rides in-string.
        writers.append('<p class="empty-note">No writers yet</p>')
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


def _cal_accessible_name(dstr: str, is_today: bool, picked: bool) -> str:
    """Full accessible name for an edition BUTTON (§14): 'Friday, July 10, 2026 —
    edition — show headlines' (picked: '— showing headlines'; today: '— today’s
    edition'). The action hint is the approved mockup's (gate FIX-1): it is the
    only cue that the button populates the panel rather than navigating. Only
    edition days are focusable; the pick ALSO rides aria-pressed — pickDay keeps
    the hint in sync client-side."""
    d = datetime.strptime(dstr, "%Y-%m-%d")
    human = f"{d.strftime('%A, %B')} {d.day}, {d.year}"
    return (human + (" — today’s edition" if is_today else " — edition")
            + (" — showing headlines" if picked else " — show headlines"))


def _month_label(month: str) -> str:
    """'2026-07' -> 'July 2026' (nav links, aria labels)."""
    y, m = int(month[:4]), int(month[5:7])
    return f"{datetime(y, m, 1).strftime('%B')} {y}"


def _prev_month(month: str) -> str:
    """The calendar month immediately before 'YYYY-MM'."""
    y, m = int(month[:4]), int(month[5:7])
    y, m = (y - 1, 12) if m == 1 else (y, m - 1)
    return f"{y:04d}-{m:02d}"


def _calendar_html(month: str, ed_dates: set, utc_by_date: Dict[str, str],
                   today: str, picked: str, first_ed: str) -> str:
    """The §14 calendar grid for ONE month. All day-states are typographic and
    NOTHING encloses a numeral, so two-digit dates cannot cramp (the redesign's
    fix by construction):
      edition — ink 700 + moved-green underline + mono stamp; a <button> (only
                edition days are focusable); aria-pressed carries the pick;
                cal-picked adds the display-scale jump on the picked day
      today   — terra numeral (color marks today, and only today). Today WITH an
                edition is a button; today WITHOUT one is a non-interactive span
                (the pre-generation-morning state the old design never named)
      gap     — ink-faint numeral, inside history, no shame
      bare    — --cal-bare, pre-history and future
    Sunday-start; decorative lead cells aria-hidden. No ring, no box, ever."""
    y, m = int(month[:4]), int(month[5:7])
    ndays = calendar.monthrange(y, m)[1]
    lead_blanks = (datetime(y, m, 1).weekday() + 1) % 7   # Sunday-start offset
    dow = "".join(f'<span class="cal-dow" aria-hidden="true">{d}</span>'
                  for d in ("Su", "Mo", "Tu", "We", "Th", "Fr", "Sa"))
    cells = ['<span class="cal-cell" aria-hidden="true"></span>'] * lead_blanks
    for day in range(1, ndays + 1):
        dstr = f"{y:04d}-{m:02d}-{day:02d}"
        is_today = dstr == today
        if dstr in ed_dates:
            cls = "cal-cell cal-edition"
            if is_today:
                cls += " cal-today"
            if dstr == picked:
                cls += " cal-picked"
            stamp = (f'<span class="cal-stamp">{_e(utc_by_date.get(dstr, ""))} UTC</span>'
                     if utc_by_date.get(dstr) else "")
            pressed = "true" if dstr == picked else "false"
            cells.append(
                f'<span class="{cls}"><button type="button" '
                f'data-date="{_e(dstr)}" '
                f'onclick="return pickDay(\'{_e(dstr)}\', event)" '
                f'aria-pressed="{pressed}" '
                f'aria-label={_e_attr(_cal_accessible_name(dstr, is_today, dstr == picked))}>'
                f'<span class="cal-num">{day}</span>{stamp}</button></span>')
        elif is_today:
            # Today, no edition yet (pre-generation morning): terra numeral, no
            # underline, non-interactive. Not aria-hidden — the date still reads,
            # and the sr-only label says WHY it reads (gate FIX-2: terra alone is
            # color-alone non-visually; a bare numeral in the a11y tree is worse
            # than silence).
            cells.append('<span class="cal-cell cal-today">'
                         f'<span class="cal-num">{day}</span>'
                         f'<span class="sr-only"> — '
                         f'{_e(labels.ARCHIVE_TODAY_NO_EDITION)}</span></span>')
        elif first_ed and first_ed <= dstr <= today:
            cells.append('<span class="cal-cell cal-gap" aria-hidden="true">'
                         f'<span class="cal-num">{day}</span></span>')
        else:
            cells.append('<span class="cal-cell cal-void" aria-hidden="true">'
                         f'<span class="cal-num">{day}</span></span>')
    return (f'<div class="cal-grid" role="group" '
            f'aria-label={_e_attr(_month_label(month) + " calendar")}>'
            f'{dow}{"".join(cells)}</div>')


def _day_panel_html(date: str, utc: str, stories: List[Dict], today: str,
                    hidden: bool) -> str:
    """One edition's day panel (§14): the mono stamp, the View-briefing jump
    ABOVE the headlines (his approved spec), then EVERY headline — each a door to
    the same edition ('two doors, one room'). openEdition is the read-logging
    open; the href is the no-JS fallback. Hidden until picked; the picked panel's
    reveal is announced by the aria-live stack that wraps these."""
    d = datetime.strptime(date, "%Y-%m-%d")
    bits = [f"{d.strftime('%A, %B')} {d.day}, {d.year}".upper()]
    if utc:
        bits.append(f"ASSEMBLED {utc} UTC")   # the whole stamp is _e'd on render
    if date == today:
        bits.append(labels.ARCHIVE_TODAY_TAG)      # the TODAY tag lives here now
    heads = []
    for i, s in enumerate(stories):
        h = s.get("headline") or ""
        if not h:
            continue
        cls = ' class="dp-lead"' if i == 0 else ""
        heads.append(
            f'<li{cls}><a href={_e_attr("/?date=" + date)} '
            f'onclick="return openEdition(\'{_e(date)}\', event)">{_e(h)}</a></li>')
    hidden_attr = " hidden" if hidden else ""
    return (
        f'<div class="day-panel"{hidden_attr} data-date="{_e(date)}">'
        f'<p class="dp-stamp">{_e(" · ".join(bits))}</p>'
        f'<p class="dp-action"><a class="dp-btn" href={_e_attr("/?date=" + date)} '
        f'onclick="return openEdition(\'{_e(date)}\', event)">'
        f'{_e(labels.ARCHIVE_VIEW_BRIEFING)} →</a></p>'
        f'<ol class="dp-headlines">{"".join(heads)}</ol></div>')


def _archive_body(con: sqlite3.Connection, anchor_month: Optional[str] = None) -> str:
    """§14 archive guts (titles + month nav + the grid/panel pair), returned
    WITHOUT the page shell so /archive?am= can serve it as a fetch fragment
    (mirrors /edition). '' when there are no editions — the caller renders the
    honest empty state.

    Window / month-depth (the principal's build-time reach-back rider, 2026-07-18
    — interpretation flagged in the build report): the window is the anchor month
    plus its immediately-preceding calendar month WHEN that month carries
    editions — so the trailing month is VISIBLE whenever it has content, and an
    editionless trailing month is never rendered as empty noise. Default anchor is
    the latest edition's month.

    Month nav pages to the nearest edition-bearing month strictly outside the
    window on each side; a link renders only when reachable and is ABSENT
    otherwise (never a disabled link — v4 affordance-absence law)."""
    from . import ranking   # local import: the file's established ranking pattern
    today = ranking.local_today()
    editions: List[Dict] = []
    for row in con.execute("SELECT * FROM briefings ORDER BY date DESC"):
        entry = _log_entry_for(row["date"])
        stories, _ = _stories_for(row, entry)
        # NL-146 fix loop 1 (QA F-4) — A ROW IS NOT AN EDITION, HERE TOO.
        #
        # The half-edition law landed on Today only, and the archive is where it
        # LASTS: a run that dies after `ranking.persist` leaves the date's row
        # committed with no body behind it forever, and NL-146 makes those
        # deaths routine and unwatched. Presenting that date as an edition puts
        # a calendar tile, a UTC stamp and a "View briefing →" on a day that
        # never published, opening onto the honest-empty view — the same lie the
        # Today fix closed, one view over.
        #
        # The predicate is `_stories_for`, already computed on the line above:
        # the edition renderer's own, reused and never re-derived (NL-103 row 9),
        # so what the archive OFFERS and what the edition view would RENDER
        # cannot disagree. Skipping is the smallest true change — an unreadable
        # date is absent from the month grid, the day-panel stack and the month
        # window arithmetic alike, because all three read `editions`.
        if not stories:
            continue
        editions.append({"date": row["date"], "utc": _utc_hm(row["generated_at"]),
                         "stories": stories})
    if not editions:
        return ""
    ed_dates = {e["date"] for e in editions}
    first_ed = min(ed_dates)
    utc_by_date = {e["date"]: e["utc"] for e in editions}
    ed_months = sorted({d[:7] for d in ed_dates})
    valid_anchor = (anchor_month and re.match(r"^\d{4}-\d{2}$", anchor_month)
                    and anchor_month in ed_months)
    anchor = anchor_month if valid_anchor else max(ed_months)
    trailing = _prev_month(anchor)
    window = [anchor] + ([trailing] if trailing in ed_months else [])  # newest first
    earliest_shown = min(window)
    window_eds = sorted((e for e in editions if e["date"][:7] in window),
                        key=lambda e: e["date"])
    picked = window_eds[-1]["date"] if window_eds else ""

    older = max((mo for mo in ed_months if mo < earliest_shown), default=None)
    newer = min((mo for mo in ed_months if mo > anchor), default=None)
    nav_bits = []
    if older:
        nav_bits.append(
            f'<a class="month-nav-link mn-prev" href="#" '
            f'onclick="return navMonth(\'{older}\', event)">'
            f'← {_e(_month_label(older))}</a>')
    if newer:
        nav_bits.append(
            f'<a class="month-nav-link mn-next" href="#" '
            f'onclick="return navMonth(\'{newer}\', event)">'
            f'{_e(_month_label(newer))} →</a>')
    nav_html = f'<p class="month-nav">{"".join(nav_bits)}</p>' if nav_bits else ""

    grids = []
    for i, mo in enumerate(window):
        y = int(mo[:4])
        title = (f'<h1 class="month-title">{datetime(y, int(mo[5:7]), 1).strftime("%B")} '
                 f'<span class="yr">{y}</span></h1>') if i == 0 else (
                 f'<h2 class="month-title">{datetime(y, int(mo[5:7]), 1).strftime("%B")} '
                 f'<span class="yr">{y}</span></h2>')
        grid = _calendar_html(mo, ed_dates, utc_by_date, today, picked, first_ed)
        # nav flanks the anchor (top) title only — the trailing month is the
        # reach-back, not a second nav origin.
        grids.append(f'<div class="cal-month">{title}{nav_html if i == 0 else ""}{grid}</div>')
    panels = [_day_panel_html(e["date"], e["utc"], e["stories"], today,
                              hidden=(e["date"] != picked))
              for e in reversed(window_eds)]   # newest first in DOM
    return (
        f'<div class="arch-cols">'
        f'<div class="cal-col">{"".join(grids)}</div>'
        f'<div class="day-panel-stack" aria-live="polite">{"".join(panels)}</div>'
        f'</div>')


def _render_archive(con: sqlite3.Connection) -> str:
    """§14 archive (the step-back redesign, APPROVED 2026-07-18): the month grid
    paired with a day panel BESIDE it (desktop) / below it (mobile), the front
    page's own 7fr/5fr skeleton. The list-below is dead; the panel carries the
    picked day's headlines with View-briefing above them. mini-masthead + section
    line open the view (§4). Zero editions renders the honest empty state.
    The guts live in _archive_body so /archive?am= can page months as a fragment;
    #archive-body is the swap target."""
    from . import ranking   # local import: the file's established ranking pattern
    head = _mini_head(ranking.local_today()) + _section_line("archive")
    body = _archive_body(con)
    if not body:
        return head + (f'<div class="page">'
                       f'<h1 class="page-title">{_e(labels.NAV_ARCHIVE)}</h1>'
                       f'<p class="empty-note">{_e(labels.ARCHIVE_EMPTY)}</p></div>')
    return head + f'<div class="page"><div id="archive-body">{body}</div></div>'


# ---------------------------------------------------------------------------
# The thread page (the "Open thread" destination — DECISIONS 2026-07-14: "the
# thread page ... standing state + full timeline + open question/next fixed
# point + verbs, composed from M1's components"). Renders from thread_state +
# thread_deltas + memory ONLY — persisted, honest empty states, no invented
# fields (day-one silence — render condition §B / contract Clash-3: a day-one
# thread gets no arc/story-so-far, ever).
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


def _baseline_state_seed_html(con: sqlite3.Connection, tid: int) -> str:
    """NL-77: the baseline's seeded day-one standing state, for "Where this
    stands" when no real thread_state exists yet. Disclosed as external synthesis
    and cited '(baseline, <date>)' so it is never mistaken for our own record.
    Returns '' unless a 'ready' baseline with a non-empty seed exists."""
    from . import memory_core
    try:
        b = memory_core.ready_baseline(con, tid)
    except Exception:  # noqa: BLE001 — pre-0017/absent table = no seed
        return ""
    if not b or not (b.get("state_seed") or "").strip():
        return ""
    cite = memory_core.baseline_cite(b["as_of_date"])
    return (f'<div class="baseline-seed">'
            f'<p class="dossier-state">{_e(b["state_seed"].strip())}</p>'
            f'<p class="baseline-disclosure">{_e(labels.BASELINE_DISCLOSURE)} '
            f'<span class="baseline-cite">{_e(cite)}</span></p></div>')


def _thread_baseline_html(con: sqlite3.Connection, tid: int, anchor: str) -> str:
    """NL-77 the cold-start backgrounder — "How we got here", a permanent section
    between "Where this stands" and "The story so far". Renders the newest
    baseline: a 'ready' one shows its external-synthesis background (ALWAYS
    disclosed as not-our-record — the writer-flow's non-licensing law made
    reader-visible); a 'pending' one shows the honest "preparing" note. Returns ''
    when there is no baseline (a thread predating NL-77, or one that needs no
    founding floor) — the section simply does not appear. Degrades to '' on a
    pre-0017 DB."""
    from . import memory_core
    try:
        b = memory_core.latest_baseline(con, tid)
    except Exception:  # noqa: BLE001 — a pre-0017/absent table is just "no baseline"
        return ""
    if not b:
        return ""
    status = b.get("status")
    if status == memory_core.BASELINE_STATUS_PENDING:
        body = f'<p class="empty-note">{_e(labels.BASELINE_PENDING)}</p>'
    elif status == memory_core.BASELINE_STATUS_READY and (b.get("backgrounder") or "").strip():
        cite = memory_core.baseline_cite(b["as_of_date"])
        paras = [p.strip() for p in re.split(r"\n\s*\n", b["backgrounder"].strip())
                 if p.strip()]
        body = (f'<p class="baseline-disclosure">{_e(labels.BASELINE_DISCLOSURE)} '
                f'<span class="baseline-cite">{_e(cite)}</span></p>'
                + "".join(f"<p>{_e(p)}</p>" for p in paras))
    else:
        return ""      # a 'failed' newest baseline renders nothing (honest gap)
    return (f'<div class="deep-section" id="{anchor}-baseline">'
            f'<h2 class="deep-section-label">{_e(labels.HOW_WE_GOT_HERE)}</h2>'
            f'{body}</div>')


def _render_thread_page(con: sqlite3.Connection, mrow) -> str:
    """One thread page. Standing state ("Where this stands", as-of + staleness)
    then the cold-start backgrounder ("How we got here", NL-77) then the
    story-so-far (full ledger) then the edition back-links then the verbs. Honest
    empty states throughout; a day-one thread (no state, no ledger) renders the
    honest 'new thread' notes, never a fabricated arc."""
    from . import memory_core
    tid, topic, status = mrow["id"], mrow["topic"], mrow["status"]
    anchor = f"thread-{tid}"
    out = [f'<section id="view-{anchor}" class="view">']
    out.append(_back_link(labels.THREAD_BACK,
                          "closeThread(event); return false;"))
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
    if card:
        state_body = card
    else:
        # NL-77: with no real (record-established) standing state yet, fall back
        # to the baseline's seeded day-one state, disclosed as external
        # synthesis and cited "(baseline, <date>)" — never dressed as our record.
        seed = _baseline_state_seed_html(con, tid)
        state_body = seed or f'<p class="empty-note">{_e(labels.THREAD_NO_STATE)}</p>'
    out.append(f'<div class="deep-section" id="{anchor}-state">'
               f'<h2 class="deep-section-label">{_e(labels.WHERE_THIS_STANDS)}</h2>'
               f'{state_body}</div>')

    # NL-77 the cold-start backgrounder — "How we got here", between the standing
    # state and the story-so-far. '' when there is no baseline (section absent).
    out.append(_thread_baseline_html(con, tid, anchor))

    # The story so far — full ledger; day-one (no ledger) is an honest empty
    # state, never a fabricated arc (day-one silence — render condition §B /
    # contract Clash-3).
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


def _run_outcome(entry: Dict) -> str:
    """The word for how a recorded run ended.

    ORDER MATTERS: NL-148 clause 4's systemic pause rides as `paused` BESIDE
    `status: "failed"` (that entry deliberately kept the old word so no existing
    consumer moved), so the pause marker is read FIRST — a paused run reported
    as a plain failure would send the reader hunting for a bug in a run that
    stopped on purpose."""
    if entry.get("paused"):
        return labels.RUNLOG_PAUSED
    if entry.get("status") == "ok":
        return labels.RUNLOG_SAMPLE if entry.get("sample") else labels.RUNLOG_PUBLISHED
    return labels.RUNLOG_FAILED


def _render_run(entry: Dict) -> str:
    """One recorded run as one report block (NL-149 item 2).

    The step lines come from `stage_timeline` ONLY — the reader-world stage words
    with the time each took. They are deliberately NOT backfilled from the money
    ledger (`steps`), which counts LLM CALLS under internal names
    (`narrative_A`, `script_retry`) and cannot say what a stage took. A run
    recorded before NL-149 has no timeline and says so, in place, rather than
    borrowing a number from the one end-of-run timestamp it does carry."""
    when = _fmt_local(entry.get("ts"), with_date=True)
    outcome = _run_outcome(entry)
    bits = []
    date = entry.get("date")
    if date:
        bits.append(_e(str(date)))
    # NL-146 — TRIGGER PROVENANCE, rendered only when the record HAS it. The
    # charter's requirement is that scheduled runs render honestly, and honest
    # here has two halves: name the trigger when it was recorded, and claim
    # nothing when it wasn't. Every run in his log before this milestone, and
    # every scripted caller after it, carries no `trigger` key and gets no word
    # — a defaulted "You ran it" on a battery run would be a fabricated fact
    # about a human, on the one screen whose whole job is the record.
    _trigger = entry.get("trigger")
    if _trigger == schedule.TRIGGER_SCHEDULED:
        bits.append(_e(labels.RUNLOG_TRIGGER_SCHEDULED))
    elif _trigger == schedule.TRIGGER_INTERACTIVE:
        bits.append(_e(labels.RUNLOG_TRIGGER_INTERACTIVE))
    # NL-151b — the depth demotion, counted, on the run's own meta line. The
    # count comes from `fetch_skipped`, the analysis stage's own record, so
    # this line cannot claim a demotion the stage did not make; and a run
    # recorded before the contract existed has no key and gets no word, the
    # same silence-is-honest rule the trigger word above follows.
    #
    # GATE R-A (2026-08-14, QA F-1) — BUCKETED BY OUTCOME, not counted as one.
    # `fetch_skipped` carries both gates, and their CAUSES differ: Gate A tried
    # and failed, Gate B never opened a socket. One count under the Gate-A
    # parenthetical would put on the record screen the exact sentence the
    # briefing renderer refuses for a Gate-B skip as false. So each non-empty
    # bucket gets its own bit, Gate A first (the ordinary morning's cause, and
    # 72/72 of the real log to date). An entry that is not a dict, or carries an
    # outcome nobody wrote, counts as Gate A — the same total the pre-fix line
    # rendered, never a dropped demotion.
    _skipped = entry.get("fetch_skipped")
    if isinstance(_skipped, list) and _skipped:
        from . import analysis as analysis_mod
        _gate_a: List[Dict] = []
        _gate_b: List[Dict] = []
        for _s in _skipped:
            (_gate_b if isinstance(_s, dict)
             and _s.get("outcome") == analysis_mod.NO_FETCHABLE_OUTCOME
             else _gate_a).append(_s)
        for _bucket, _one, _many in (
                (_gate_a, labels.RUNLOG_DEPTH_SKIPPED_ONE,
                 labels.RUNLOG_DEPTH_SKIPPED_MANY),
                (_gate_b, labels.RUNLOG_DEPTH_NOFETCH_ONE,
                 labels.RUNLOG_DEPTH_NOFETCH_MANY)):
            if _bucket:
                bits.append(_e(_one if len(_bucket) == 1
                               else _many.format(n=len(_bucket))))
    total_el = entry.get("elapsed_s")
    if isinstance(total_el, (int, float)) and total_el > 0:
        bits.append(f"{_e(labels.RUNLOG_TOTAL)} {_fmt_elapsed(total_el)}")
    usd = entry.get("total_usd")
    if isinstance(usd, (int, float)):
        # "$0" and not "$0.0000" for an exactly-free run — generate.py's own
        # audio warning already writes zero that way, and four decimals of
        # nothing reads like a rounding artifact rather than the fact it is.
        # THE WORD "charged" IS LOAD-BEARING: on the subscription lane a real
        # run genuinely costs $0 charged while its shadow price is dollars, and
        # the shadow figure is deliberately NOT summed here — the log carries no
        # run-level shadow total, and deriving one on a money surface would be
        # inventing a figure the record never wrote.
        money = f"${usd:.4f}" if usd else "$0"
        bits.append(f"{money} {_e(labels.RUNLOG_CHARGED)}")
    timeline = entry.get("stage_timeline")
    if isinstance(timeline, list) and timeline:
        detail = ('<ol class="gen-log">'
                  + "".join(_gen_log_line(s) for s in timeline if isinstance(s, dict))
                  + "</ol>")
    elif entry.get("status") == "ok" or entry.get("steps"):
        detail = f'<p class="empty-note">{_e(labels.RUNLOG_NO_TIMING)}</p>'
    else:
        detail = f'<p class="empty-note">{_e(labels.RUNLOG_STEPS_UNRECORDED)}</p>'
    error = entry.get("error")
    err_html = (f'<p class="runlog-error">{_e(str(error))}</p>'
                if error and outcome != labels.RUNLOG_PUBLISHED else "")
    return (f'<div class="runlog-run">'
            f'<h2 class="runlog-head"><span class="runlog-when">{_e(when)}</span>'
            f'<span class="runlog-outcome">{_e(outcome)}</span></h2>'
            f'<p class="runlog-meta">{" · ".join(bits)}</p>'
            f'{err_html}{detail}</div>')


def _render_run_log(recorded: Optional[Tuple[List[Dict], int]] = None) -> str:
    """The Generation reports view — a sibling .view, reached from Settings.

    A full destination rather than a panel section because the slide panel is
    23rem wide and a step table inside it would need a layout of its own; this
    composes the SAME components every other destination uses (deep-back,
    deep-title-block, the .gen-log line grammar item 1 introduced) and mints no
    new design direction.

    `recorded` lets build_page hand in the read it already did for the Settings
    row's count — one parse of the log per page, not two. Passing nothing reads
    it, so the view is still callable on its own."""
    runs, total = recorded if recorded is not None else _run_log_entries()
    out = ['<section id="view-runlog" class="view">',
           _back_link(labels.BACK_TO_TODAY, "closeRunLog(event); return false;"),
           '<div class="deep-title-block">'
           f'<p class="deep-eyebrow">{_e(labels.RUNLOG_EYEBROW)}</p>'
           f'<h1 class="deep-title">{_e(labels.RUNLOG_TITLE)}</h1></div>']
    if not runs:
        out.append(f'<p class="empty-note">{_e(labels.RUNLOG_EMPTY)}</p>')
    else:
        if total > len(runs):
            out.append(
                f'<p class="runlog-note">{_e(labels.RUNLOG_TRUNCATED_PREFIX)} '
                f'{len(runs)} {_e(labels.RUNLOG_TRUNCATED_MIDDLE)} {total} '
                f'{_e(labels.RUNLOG_TRUNCATED_SUFFIX)}</p>')
        out.extend(_render_run(e) for e in runs)
    out.append("</section>")
    return "".join(out)


def _render_schedule_rows() -> str:
    """NL-152 — scheduled generation, settable from the settings tab.

    TWO ROWS AND NOT ONE, because they are two different facts and the existing
    `.settings-row` is a flex with room for exactly one control: whether the
    schedule runs at all (the toggle) and what time it runs (the popup). Both
    are the shipped idiom — the toggle is Dark mode's, the popup is the
    add-topic/add-writer editor's — so this row pair introduces no CSS.

    THE VALUE LINE IS THE HONEST ONE, and it is where the plist reality lands.
    `installed` means the agent FILE is at the path launchd reads and nothing
    more (schedule.status's own bound), so the copy never says "the schedule is
    running". Where the file's baked hour disagrees with the chosen hour, the
    row says which one actually fires — the agent — because that is the one
    that will wake his machine tomorrow morning.
    """
    st = schedule.status()
    hour = st["configured_hour"]
    paused = bool(st["paused"])
    plist_hour = st["plist_hour"]
    checked = "false" if paused else "true"

    # PAUSED IS TESTED FIRST because it is the fact the TOGGLE beside this line
    # is displaying. A paused-and-not-installed schedule read "No agent
    # installed yet" while the switch showed off — a value line describing a
    # different fact than the control it sits next to, which is the settings
    # screen's own version of two records of one morning.
    if paused:
        state = "Paused — scheduled runs decline before spending anything"
        if not st["installed"]:
            state += "; no agent installed either"
    elif not st["installed"]:
        # NOT AN ERROR AND NOT A NAG. He may simply not have installed the agent
        # yet; the row states the standing condition and the settings still
        # persist, so the value he picks is already correct when he does.
        state = "No agent installed yet — nothing fires automatically"
    elif isinstance(plist_hour, int) and isinstance(hour, int) \
            and plist_hour != hour:
        state = (f"Installed, but the agent still fires at "
                 f"{plist_hour:02d}:00 — re-install to move it")
    elif isinstance(plist_hour, int):
        state = f"On — today’s edition is generated at {plist_hour:02d}:00 local"
    else:
        state = "Installed — the agent file’s hour could not be read"

    # `configured_hour` IS ALWAYS AN INT — `config.generate_hour_resolved`
    # returns the default on every arm it can reach, INCLUDING the typo'd-env
    # arm, and `status()` passes it straight through. So there is no "not set"
    # state for this row to render, and the hardcoded fallback that used to sit
    # in the button's onclick was a SECOND SPELLING of
    # `config.DEFAULT_GENERATE_HOUR_LOCAL` — the exact two-spellings hazard the
    # resolver's own comment was written to close (QA F-11). The invariant is
    # pinned BEHAVIOURALLY rather than defended by a dead branch here; a
    # defensive re-spelling would only hide the day the resolver stopped
    # honouring it.
    # QA F-4 / gate R-A — THE ROW NAMES THE LAYER WHEN THIS SCREEN IS THE ONE
    # THAT WON. Settings-beats-env was chosen for a measured reason (his .env
    # line 39 already sets GENERATE_HOUR_LOCAL, so env-wins would have shipped a
    # control that reported success and changed nothing). The cost of that
    # choice is that the .env line goes inert, and before this the product said
    # so only in the doctor and in a `schedule status` MISMATCH line that needs
    # an installed agent to fire — nowhere on the screen he is actually looking
    # at while he changes the hour. Named here, and only when both layers are
    # really in play: a reader with no such variable gets no note about one.
    hour_val = f"{hour:02d}:00 local"
    if (st.get("hour_source") == config.HOUR_SOURCE_SETTINGS
            and (os.environ.get("GENERATE_HOUR_LOCAL") or "").strip()):
        hour_val += " — set here; GENERATE_HOUR_LOCAL in your .env no longer decides"
    mismatch = isinstance(plist_hour, int) and plist_hour != hour
    if mismatch:
        hour_val += f" — the installed agent still fires at {plist_hour:02d}:00"

    # QA F-13 — THE ROW MUST CARRY A PATH BACK TO THE TWO COMMANDS. The save
    # popup hands them over at the moment of the save, but this mismatch state
    # PERSISTS across reloads ("re-install to move it") — and once that popup
    # was closed, the row was an instruction with no route to the thing it
    # instructs him to run. The commands now ride the Change button he is
    # already being pointed at, and the popup renders them in the same
    # `popup-status` block, in the same words, the post-save arm uses.
    # `schedule.reinstall_commands` stays the single source. No new component,
    # no new CSS, and the data rides the element that carries the handler
    # rather than a parked <span> (QA's M25 shape).
    hour_attrs = ""
    if mismatch:
        hour_attrs = (f' data-plist-hour="{plist_hour:02d}"'
                      ' data-reinstall='
                      + _e_attr(json.dumps(schedule.reinstall_commands())))

    return f"""
<div class="settings-row">
  <div class="settings-row-main">
    <p class="settings-row-label">Scheduled generation</p>
    <p class="settings-row-value">{_e(state)}</p>
  </div>
  <div class="toggle-switch" id="schedule-toggle" role="switch"
       aria-checked="{checked}" tabindex="0" aria-label="Scheduled generation"
       onclick="toggleSchedule(this)"
       onkeydown="if(event.key===' '||event.key==='Enter'){{event.preventDefault();this.click();}}"></div>
</div>
<div class="settings-row">
  <div class="settings-row-main">
    <p class="settings-row-label">Generation time</p>
    <p class="settings-row-value">{_e(hour_val)}</p>
  </div>
  <button class="settings-row-action"{hour_attrs}
          onclick="openScheduleHour({hour}, this)">Change</button>
</div>"""


def _render_settings(con: sqlite3.Connection, row, entry: Optional[Dict],
                     recorded: Optional[Tuple[List[Dict], int]] = None) -> str:
    cfg = config.load_sources()
    enabled = len(cfg.fetchable_sources) + len(cfg.reference_only_sources)
    # M7 gate finding 7: display the CONFIGURED engine, not the constant.
    engine = ("Kokoro (local, $0/episode)" if cfg.tts_engine == "kokoro"
              else "OpenAI gpt-4o-mini-tts (~$0.015/min)")
    cap = config.budget_cap_usd_per_run()
    # NL-103 row 20: the Settings row VALUE names its own class. The row label
    # ("Today's edition") is adjacent but is a plain <p> with no aria linkage,
    # and a value slot is not an empty state under a head — so the class noun
    # rides in-string.
    gen_val = ("Generated " + _fmt_local(row["generated_at"])) if row is not None \
        else "No edition yet"
    # NL-149 item 2: the row's value is the COUNT the log actually holds — the
    # reader learns whether there is anything to open before opening it, and an
    # empty record says so in the same words the view does.
    _n_runs = (recorded if recorded is not None else _run_log_entries())[1]
    runs_val = (labels.RUNLOG_EMPTY if _n_runs == 0
                else labels.RUNLOG_RUNS_ONE if _n_runs == 1
                else labels.RUNLOG_RUNS_MANY.format(n=_n_runs))
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
    <p class="settings-row-label">{_e(labels.RUNLOG_TITLE)}</p>
    <p class="settings-row-value">{_e(runs_val)}</p>
  </div>
  <button class="settings-row-action" onclick="openRunLog(event)">{_e(labels.RUNLOG_OPEN)}</button>
</div>
{_render_schedule_rows()}
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
    which the two-lane source rule forbids) with the same plain end-of-line
    outlet count the facts use (v8-M1 item 4). Pinned facts already render in the facts list
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
            # v8-M1 item 4: the verified-specifics run folds into the facts, so it
            # carries the same PLAIN end-of-line outlet count — the ▸ cite-fold
            # dies here too (no inline apparatus anywhere in the deep view,
            # EXCEPT the contested-figures drawer inside What's still open —
            # per-side attribution is the content there; its caret is NL-68's
            # section collapse, not a cite fold. Gate-ruled 2026-07-17).
            count = _facts_outlet_count(e.get("cites", []), src_by_key)
            items.append(f'<li>{_e(text)}'
                         + (f' {count}' if count else "") + '</li>')
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


def _analysis_src_cluster(cite_keys: List[str],
                          src_by_key: Dict[str, Dict]) -> str:
    """v8-M1 item 4 (2026-07-17, the citation second-raise): the trailing
    per-paragraph SOURCE CLUSTER that replaces the inline cite apparatus (▸
    caret folds, mid-prose `(via X)`) in the analysis prose sections. One quiet
    colophon line naming the DISTINCT outlets — plain text, no tap targets, no
    floating markers, sentence flow never interrupted (the fix is structural, not
    cosmetic). '' when no cite resolves to an outlet (no dead cluster). Claim-
    level mapping coarsens to paragraph-level here BY DESIGN; the per-claim detail
    survives in the stored brief and the Sources drawer below."""
    outlets: List[str] = []
    for k in cite_keys:
        s = src_by_key.get(k)
        outlet = s.get("outlet", "") if isinstance(s, dict) else ""
        if outlet and outlet not in outlets:
            outlets.append(outlet)
    if not outlets:
        return ""
    return f'<p class="src-cluster">— {_e(" · ".join(outlets))}</p>'


def _facts_outlet_count(cites: List[str], src_by_key: Dict[str, Dict]) -> str:
    """v8-M1 item 4: the facts-list keeps its END-OF-LINE outlet COUNT (the
    boundary case the round preserved: `(6 outlets)`, never mid-sentence, never a
    caret). Plain text — the outlet NAMES live in the Sources drawer, not a
    tap-to-reveal fold. '' when nothing resolves."""
    n = len({s.get("outlet", "") for c in cites
             if isinstance((s := src_by_key.get(c)), dict) and s.get("outlet")})
    if n <= 0:
        return ""
    return f'<span class="cite">({n} outlet{"s" if n != 1 else ""})</span>'


def _deep_arc_line_html(con, slot: Optional[Dict], date: str) -> str:
    """The deep-view continuity line (arc-line contract v1, 2026-07-18): the
    memory pass's AUTHORED arc_line, rendered VERBATIM (Q3: the render stays dumb
    — the deep view is a record surface). Renders iff the EDITION's thread entry
    carries a non-empty arc_line; the memory pass authors one ONLY when the thread
    moved this edition AND has prior edition-cited coverage (render condition §B),
    so a non-empty stored field IS the render condition — the render never
    recomputes it. Absence renders NOTHING (no placeholder). Model text is escaped.
    Resolves the slot's first thread CARRYING an arc (iteration mirrors
    _deep_timeline_html, but selection can diverge from the timeline's
    first-with-rows thread on multi-thread stories — see the QA divergence pin,
    tests/test_arc_line_qa.py; unification is design's call)."""
    if con is None or not slot or not _is_calendar_date(date):
        return ""
    from . import memory_core
    for topic in slot.get("matched_memory") or []:
        tid = memory_core.resolve_thread_id(con, topic)
        if tid is None:
            continue
        row = memory_core.state_for_edition(con, tid, date)
        arc = (row or {}).get("arc_line", "") or ""
        if arc.strip():
            return f'<p class="deep-arc-line">{_e(arc)}</p>'
    return ""


def _deep_follow_line(con, slot: Optional[Dict], headline: str, date: str,
                      story_anchor: str) -> str:
    """The deep view's follow mount. Degrades to '' with no connection (the
    fixture/no-db render paths) rather than guessing a state — an unknown follow
    state must never render as "not followed", which would offer a second follow
    on a thread the reader already has.

    MARKS AWARENESS (NL-143 fix loop 1, QA F-1). The same law, one case further
    in: a story that MATCHES a tracked thread is one the reader already follows,
    so no follow is on offer here either. _follow_control has always rendered
    the marker STATE instead of the picker for that story; this mount never took
    marks, so the card withheld the control while the story's own management
    home offered "Follow this thread" — and one tap on it minted a SECOND active
    thread beside the tracked one ("Hormuz shipping watch" beside "Hormuz").
    The XOR guard cannot catch that: a name mismatch is the NORMAL case for
    marks, which is what made the two surfaces disagree in the first place.

    The arm is `marks and not followed`, not a blanket `marks`. A story can be
    marks-carrying AND followed under its own name; the card suppresses its verb
    either way, but this mount is M1c's MANAGEMENT HOME — where Unfollow lives —
    and blanking it for a thread the reader actually holds would strand that
    thread with no acts surface outside the Following row. The bug is the
    resting PICKER on a tracked story, and `not followed` is exactly the picker
    branch. (If the gate reads QA's "the same rule _follow_control applies on
    cards" as unconditional, it is deleting `and not followed` — one line.)"""
    if con is None:
        return ""
    topic = (slot or {}).get("story_title") or headline or ""
    if not topic:
        return ""
    subject, followed, origin_row = _follow_recognition(
        con, topic, headline, _active_topics_lower(con))
    # NL-145 (M1 gate R2), marker-class site 2 of 2 — the deep view's management
    # mount. Same filter, same reason as the card: an unfollowed thread's mark
    # must not keep this surface in the `tracked` state, because that state is
    # where Unfollow lives and it withholds the resting control the reader now
    # needs. `_held_topics_lower` is read here rather than passed in — this
    # function already takes `con` and already issues the recognition read.
    marks = _live_marks(slot or {}, _held_topics_lower(con))
    if marks and not followed:
        return ('<div class="follow-line">'
                + _tracked_marker_html(
                    marks, slot_id=f"follow-deep-{story_anchor}", mount="deep",
                    story_topic=subject, headline=headline, date=date)
                + "</div>")
    alt = origin_row or (_follow_altitude_row(con, subject) if followed else {})
    committed = (origin_row.get("topic") if origin_row else subject) or subject
    # FIX LOOP 1: the deep view is the OTHER management surface ruling ② carved
    # out, so it affords the same candidates the Following row does. Both
    # projections that build `alt` carry the row id, which is the join key.
    return ('<div class="follow-line">' + _follow_slot_html(
        slot_id=f"follow-deep-{story_anchor}", mount="deep", followed=followed,
        committed_topic=committed, story_topic=subject, headline=headline,
        date=date, alt=alt,
        candidates=memory.settle_candidates(con, alt.get("id"))) + "</div>")


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
    out.append(_back_link(back_label, f"closeDeepView(event{ret})"))
    # THE ARC-LINE CONTRACT v1 (2026-07-18): the deep-view continuity line is the
    # memory pass's AUTHORED, contracted arc_line, rendered VERBATIM under render
    # condition §B (the deep view is a record surface — Q3: the render stays dumb).
    # This REPLACES the old render-time derivation from the analyst's arc object
    # (arc.significance), which reused state-summary-grade text as arc prose — the
    # tense-splice defect (principal's 2026-07-17 served review item 2). brief['arc']
    # still feeds the LEDGER (memory_core.write_deltas_for_edition) and the analyst
    # prompt; only THIS render stops deriving prose from it. Absence renders
    # nothing (no placeholder); the anchor date is navigable via the story-so-far
    # timeline below, which carries every prior entry's openEdition link.
    arc_line = _deep_arc_line_html(con, slot, date)
    out.append(f'<div class="deep-title-block"><p class="deep-eyebrow">'
               f'{_e(labels.DEEP_EYEBROW)}</p>'
               f'<h1 class="deep-title">{_e(headline)}</h1>'
               f'{arc_line}</div>')

    # NL-17-M1c — MOUNT 3: the deep view is the thread's MANAGEMENT HOME.
    # Mounted where the blessed artifact puts it (SCREEN C: under the arc, above
    # the jumplist), rendered by the SAME component the card mounts. This is
    # where Unfollow lives now that his item 4 took it off cards — and it is why
    # the card's steady verb became a door: every entry of a followed thread
    # reaches the acts line in one tap, not just the card that created it.
    out.append(_deep_follow_line(con, slot, headline, date, story_anchor))

    # NL-68 item 3 (THE SUPERSET LAW): open with the story's OWN Today prose
    # (lede + why-it-matters + watch-for) before any analyst section, so the deep
    # view carries at least everything the Today story showed, plus more.
    prose_block = _deep_today_prose(story or {}, date)
    if prose_block:
        out.append(prose_block)

    # NL-63 item 5: the "story so far" timeline — deterministic from the ledger.
    # v8-M1 item 3 (2026-07-17): it RELOCATES from under the title block to
    # second-from-last, directly before Sources — an unbounded-growth receipt
    # belongs with the receipts, not atop the day's matter. Computed here (needs
    # story_anchor); appended below, before the sources section, and given a
    # jumplist door so the catch-up reader still reaches it in one tap. ONE
    # ordering decision (Ines's falsifier is armed principal-side: if he misses
    # it, flipping to below-the-jumplist is moving this single append up).
    timeline_html = _deep_timeline_html(con, slot, date, story_anchor)

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
    # v8-M1 item 3: the relocated timeline gets its jumplist door, second-to-last
    # (before Sources) — gated on real content so no dead anchor when absent.
    if timeline_html:
        jump_items.append(("timeline", labels.THE_STORY_SO_FAR))
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
    # v8-M1 item 4: the facts keep an END-OF-LINE outlet COUNT (`(6 outlets)`) —
    # the ▸ caret fold DIES with the rest of the inline apparatus; the outlet
    # names live in the Sources drawer below (never a mid-list tap target).
    lis = []
    for f in brief.get("pinned_facts", []):
        cites = f.get("cites", [])
        count = _facts_outlet_count(cites, src_by_key)
        lis.append(
            f'<li>{_e(f.get("fact", ""))}'
            + (f' {count}' if count else "") + '</li>')
    # NL-29 consolidation (Merge 2, flagged): the verified-specifics run folds in
    # here as a sub-group — the numeric ledger claims the facts slice didn't show
    # (byte-for-byte the rows the retired 'The numbers' section rendered).
    out.append(f'<div class="deep-section" id="{story_anchor}-facts">'
               f'<h2 class="deep-section-label">{_e(labels.DEEP_FACTS)}</h2>'
               f'<ul class="deep-facts-list">{"".join(lis)}</ul>'
               f'{numbers_sub}</div>')

    # 2. How this works — (NL-29: WAS 'Mechanism'; the label is the one-string
    # re-pin, the anchor id story-*-mechanism is unchanged so the jumplist
    # stays live.)
    # v8-M1 item 4: the inline [S#] cite apparatus (the ▸ fold) DIES here — the
    # keys are STRIPPED from the prose (the leading whitespace with them, so no
    # orphaned marker or double space), the prose reads uninterrupted, and one
    # trailing source cluster names the distinct outlets. No inline apparatus
    # anywhere in the deep view, EXCEPT the contested-figures drawer inside
    # What's still open (attribution IS the content there — gate-ruled
    # 2026-07-17).
    mech = brief.get("mechanism", "")
    _cite_re = re.compile(r"\s*\[([SCRP]\d+(?:,\s*[SCRP]\d+)*)\]")
    mech_keys: List[str] = []
    for m in _cite_re.finditer(mech):
        mech_keys.extend(k.strip() for k in m.group(1).split(","))
    mech_display = _cite_re.sub("", mech).strip()
    mech_cluster = _analysis_src_cluster(mech_keys, src_by_key)
    out.append(f'<div class="deep-section" id="{story_anchor}-mechanism">'
               f'<h2 class="deep-section-label">{_e(labels.DEEP_MECHANISM)}</h2>'
               f'<p>{_e(mech_display)}</p>{mech_cluster}</div>')

    # 3. effects (What could follow) — v8-M1 item 4: the inline "(via Outlet)"
    # apparatus DIES; each effect paragraph reads plain and closes with its own
    # trailing source cluster (per-paragraph, prose never interrupted). A
    # background-only effect (no resolving outlet) simply carries no cluster.
    effs = []
    for e in brief.get("effects", []):
        holder = e.get("holder", "")
        lead_in = f"{_e(holder)}: " if holder else ""
        eff_cluster = _analysis_src_cluster(e.get("cites", []), src_by_key)
        effs.append(f'<p class="deep-effect">{lead_in}{_e(e.get("effect", ""))}</p>'
                    f'{eff_cluster}')
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

    # v8-M1 item 3: the story-so-far timeline lands HERE — second-from-last,
    # directly before Sources (the receipts). Its jumplist door was added above.
    if timeline_html:
        out.append(timeline_html)

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
    out.append(_back_link(back_label, f"closeDeepView(event{ret})"))
    out.append('<div class="deep-title-block">'
               f'<p class="deep-eyebrow">{_e(labels.SOURCES_CONTEXT)}</p>'
               f'<h1 class="deep-title">{_e(headline)}</h1></div>')

    # NL-143 item 2b — MOUNT 3 REACHES THE QUICK TIER. M1c's law is that the
    # deep view is the thread's MANAGEMENT HOME: it is where Unfollow lives now
    # that his item 4 took it off cards, and it is why a committed card verb is
    # a door at all. That law never carried a tier qualifier — the $0 view
    # simply never mounted the line, so a followed In-Brief item had a door that
    # opened onto a room with no controls in it. Same component, same call, same
    # placement as the analyst view (under the title block, above the body); it
    # degrades to '' with no connection exactly as the analyst mount does.
    out.append(_deep_follow_line(con, slot, headline, date, story_anchor))

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
    # NL-138 (principal's ruling ④, DECISIONS 2026-08-02): the WHY_FULL_REASON
    # block is DELETED. NL-134 F3 had parked the ranker's full prose reason
    # here — off the front page but preserved, labeled, as provenance. His
    # accuracy finding retired that compromise a few hours later: the sentence
    # is not provenance, it is a claim about the reader's interests that the
    # model was never in a position to make ("implies the user had global oil
    # … as one of their topics, which they didn't"). Preserving it on a quieter
    # surface preserves the same inaccuracy in smaller type.
    #
    # Provenance is NOT destroyed, because the true provenance is structured
    # and all of it renders above: matched topics, tracked threads, and the
    # Here-for rationale — plus world_impact and the override flag in the
    # ranking_runs ledger the day-14 calibration actually reads. Old rows still
    # carry `world_impact_reason` in their story_slots JSON; nothing reads it,
    # so an archived edition's deep view renders exactly what a fresh one does.
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
    """Brief reads for one edition (M9-M3); renders FROM the persisted row,
    never regenerates. Returns ({slot_no: doc}, [sections]).
    Shared by Today and the archive-in-place edition (NL-11). NL-66(b): a quick-
    tier In-Brief slot with no analyst brief gets the $0 sources-&-context view
    (a failed full/medium brief stays degraded-hidden — only quick tier does).

    NL-107: newest valid wins, EXCEPT where a rival generation context is
    staged for this date — then the read is bounded by the row's own publish
    stamp, so a regenerate that died after its analysis stage can never hang
    its "full picture" under the stories of the edition that survived it. The
    stamp comes off `row` itself; the rival check is an existence bit inside
    `analysis.coherent_valid_brief`, and no staged content enters here."""
    briefs: Dict[int, Dict] = {}
    sections: List[str] = []
    from . import analysis as analysis_mod
    stories_probe, _ = _stories_for(row, entry)
    slots = _slots_for(row)
    tiers = (entry or {}).get("tiers") or []
    _positional = analysis_mod.positional_tiers(len(stories_probe))
    for i, st in enumerate(stories_probe):
        slot = slots[i] if i < len(slots) else None
        # A still-tracking slot renders as a status strip on Today, not a story,
        # so it gets NO deep view — skip it here to match _render_briefing_body
        # (index preserved so every other slot keeps its story-{i} anchor).
        if slot and slot.get("still_tracking"):
            continue
        # tier derivation MATCHES _render_briefing_body's so the entry link and
        # the collected view agree for every slot (no link without a view).
        tier = tiers[i] if i < len(tiers) else _positional[i]
        # NL-151b LEFT THIS `i + 1` ALONE, DELIBERATELY. It is the brief lookup
        # by POSITION, and under arm (i) ("drop") it would have had to become
        # `slot["slot"]` or the reader would meet another story's analysis —
        # the cardinal-breach class NL-148 finding (b) named. Arm (ii) removes
        # nothing from the body and reorders nothing, so position and slot
        # number stay identical and this line stays correct. That equality is
        # the whole reason (ii) was the cheap arm, and it is pinned by name
        # (test_L2_slot_NUMBERING_never_moves...).
        #
        # GATE R-C (2026-08-14, QA F-4): a DEMOTED slot with a same-date
        # coherent brief from an EARLIER run serves the full deep view here
        # while the body row says In-Brief. That co-occurrence is lawful and
        # deliberate — NL-66(b) is ruled law (brief present -> deep view),
        # the earlier brief is true, sourced, same-date coverage, and
        # bounding this read to the current run's depth slots would delete
        # real coverage to buy cosmetic body/tap register alignment.
        # Accepted at the gate; revisit only on the principal's word.
        doc = analysis_mod.coherent_valid_brief(con, row["date"], i + 1,
                                                row["generated_at"])
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
                + _back_link(labels.BACK_TO_ARCHIVE, "backToArchive(event)")
                + '<p class="empty-note">That edition is unavailable.</p>'
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
            f'{_back_link(labels.BACK_TO_ARCHIVE, "backToArchive(event)")}'
            f'<h1 class="view-title">{_e(_human_date(row["date"]))}</h1>'
            f'{episode}{body}</section>')
    return head + "".join(deep_sections), row["date"]


def _nl_labels_js() -> str:
    """The client-facing label subset (item 5): the follow-control copy the JS
    renders, injected as window.NL_LABELS so a labels.py re-pin lands in the
    client too — the same one-place re-pin the server renders enjoy. <>&-escaped
    so a re-pin can never break out of the <script> element."""
    # NL-17-M1c: THE ONE FOLLOW-LINE COMPONENT's client copy — the JS morphs the
    # persistent .follow-slot through resting/committed/expanded/refused/
    # unfollowed from this one table (a labels.py re-pin lands client-side too).
    # RETIRED with the thread model and deliberately ABSENT here, so no client
    # branch can render them by accident: the settling status (ruling ①), the ask
    # lead + its option row (ruling ④), the degrade pair (NL-103 row 3) and the
    # cap refusal (Arm A). Their constants stay in labels.py marked
    # RETIRED-NOT-RENDERED; this table is what the reader can actually reach.
    #
    # NL-17 M1 adds two more to that absent list: the qualifier seat and the
    # rung seat of the story-scope deictic, both buried by the principal's
    # amendment (i) (their constants are marked retired in labels.py and are
    # deliberately NOT named here — the retired-constant tooth is a source grep,
    # and a comment must not be able to satisfy or break it). Removing them from
    # THIS table is not cosmetic: it is what makes the client structurally unable
    # to render the phrase, so the burial cannot be undone by a stray branch.
    payload = {"followInactive": labels.FOLLOW_THREAD_INACTIVE,
               "followInactiveAria": labels.FOLLOW_THREAD_ARIA,
               "committedVerb": labels.FOLLOW_COMMITTED_VERB,
               "steadyPrefix": labels.FOLLOW_STEADY_PREFIX,
               "threadSelf": labels.FOLLOW_THREAD_SELF,
               "dotOn": labels.FOLLOW_DOT_ON, "dotOff": labels.FOLLOW_DOT_OFF,
               "insteadPrefix": labels.FOLLOW_INSTEAD_PREFIX,
               "unfollow": labels.FOLLOW_UNFOLLOW,
               "unfollowedReceipt": labels.FOLLOW_UNFOLLOWED_RECEIPT,
               "unfollowedSelf": labels.FOLLOW_UNFOLLOWED_SELF,
               "revertMs": labels.FOLLOW_REVERT_MS,
               "resumedPrefix": labels.FOLLOW_RESUMED_PREFIX,
               "resumedEntries": labels.FOLLOW_RESUMED_ENTRIES,
               "resumedEntry": labels.FOLLOW_RESUMED_ENTRY,
               # the refusal frame: verb + reason + remedy. The reason/remedy
               # arrive ON THE PAYLOAD (the branch that produced them names
               # them); these are the frame's fixed halves plus the fallback,
               # so an unmapped arm still renders words, never silence.
               "didntFollow": labels.REFUSAL_DIDNT_FOLLOW,
               "didntSwitch": labels.REFUSAL_DIDNT_SWITCH,
               "didntUnfollow": labels.REFUSAL_DIDNT_UNFOLLOW,
               "refusalFallback": labels.REFUSAL_MEM_FALLBACK,
               "refusalFallbackFix": labels.REFUSAL_MEM_FALLBACK_FIX}
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

    # M9-M3 / NL-107: brief reads bounded to this edition (newest valid wins
    # unless a rival run is staged — see _collect_deep_views); the view renders
    # FROM the persisted row (never regenerates); date-addressed like briefings.
    briefs: Dict[int, Dict] = {}
    deep_sections: List[str] = []
    if row is not None and gen_state["state"] != "running":
        briefs, deep_sections = _collect_deep_views(
            con, row, entry, "", labels.BACK_TO_TODAY, "view-today")

    # NL-149 item 2: ONE read of generation_log.jsonl per page — the Settings
    # row's count and the reports view are two views of the same read. (His log
    # is 660KB / 3.6ms today; it is append-only and will not stay that size.)
    _recorded = _run_log_entries()

    page = webui.PAGE.format(
        css=webui.CSS,
        staleness_banner=_staleness_banner_html(),
        today_html=_render_today(con, row, entry, gen_state, briefs=briefs),
        following_html=_render_following(con),
        archive_html=_render_archive(con),
        settings_html=_render_settings(con, row, entry, recorded=_recorded),
        popups_html=webui.POPUPS,
        deep_views_html="".join(deep_sections),
        thread_pages_html=_collect_thread_pages(con),
        runlog_html=_render_run_log(_recorded),
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
            if parsed.path == "/api/status":
                # NOT gated by the first-run router below: the ceremony's own
                # vigil polls this endpoint, and the reader-world stage word
                # rides along so the map (SEAM 3) stays server-side, in one
                # place, rather than being re-implemented in JavaScript.
                snap = GEN_JOB.snapshot()
                snap["reader_stage"] = commissioning.reader_stage(
                    snap.get("stage"))
                return self._send_json(snap)
            # STAGE-0 C1 — THE FIRST-RUN ROUTER. "PROFILE HAS NO TOPICS -> the
            # founding page, at every URL" (v12 state matrix): every HTML door
            # a first-run reader can open answers with the Commissioning, so a
            # deep link into Today, an edition or the Archive cannot strand a
            # stranger in three empty rooms. /api/status is exempt above;
            # /audio/ has nothing to serve before an edition exists and 404s on
            # its own.
            if parsed.path in ("/", "/edition", "/archive"):
                first_run = self._commissioning_page()
                if first_run is not None:
                    return self._send_html(first_run)
            if parsed.path == "/":
                return self._page(parse_qs(parsed.query))
            if parsed.path == "/edition":
                return self._edition(parse_qs(parsed.query))
            if parsed.path == "/archive":
                return self._archive(parse_qs(parsed.query))
            m = re.match(r"^/audio/(\d{4}-\d{2}-\d{2})\.wav$", parsed.path)
            if m:
                return self._audio(m.group(1))
            self._send_html("<h1>Not found</h1>", 404)
        except BrokenPipeError:
            pass
        except Exception as exc:
            self._send_html(f"<h1>Server error</h1><pre>{_e(exc)}</pre>", 500)

    def _commissioning_page(self) -> Optional[str]:
        """The founding page's HTML while this profile is in its first run, or
        None once it is out of it (Stage-0 C1).

        Every state is derived HERE, server-side, from the job's snapshot — the
        page's script only polls and reloads. That is what keeps "Closing this
        page won't stop it — the edition will be here when it's ready." true
        (SEAM 4): the run is a background thread in this process, and returning
        to any URL re-enters the wait from its snapshot."""
        con = db.connect()
        try:
            gen_state = GEN_JOB.snapshot()
            state = commissioning.first_run_state(con, gen_state)
            if state is None:
                return None
            outcome = (_failure_outcome(con)
                       if state == commissioning.FAILED else "")
        finally:
            con.close()
        return commissioning.render(
            state, config.load_sources(), gen_state, outcome=outcome,
            staleness_banner=_staleness_banner_html())

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

    def _archive(self, qs: Dict[str, List[str]]) -> None:
        """§14 month paging: serve the archive guts for the requested month as a
        fetch fragment (the /edition pattern). No read is logged — paging months
        is not opening an edition. A bad/absent month falls back to the default
        window inside _archive_body."""
        am = (qs.get("am") or [None])[0]
        if am and not re.match(r"^\d{4}-\d{2}$", am):
            am = None
        con = db.connect()
        try:
            body = _archive_body(con, am)
            self._send_html(body or "<p class='empty-note'>No editions yet.</p>")
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
                # NL-17-M1c: /api/follow/resolve is RETIRED and split in two.
                # The old route made the reader's act wait on (and be refusable
                # by) the coverage lookup; the thread model forbids both.
                "/api/follow/seed": self._api_follow_seed,
                "/api/follow/settle": self._api_follow_settle,
                "/api/follow/at": self._api_follow_at,
                "/api/unfollow": self._api_dismiss,
                "/api/dismiss": self._api_dismiss,
                "/api/revive": self._api_revive,
                "/api/thread/delete": self._api_delete,
                "/api/note": self._api_note,
                "/api/topic/add": self._api_topic_add,
                "/api/topic/remove": self._api_topic_remove,
                "/api/schedule/pause": self._api_schedule_pause,
                "/api/schedule/hour": self._api_schedule_hour,
                "/api/writer/add": self._api_writer_add,
                "/api/writer/remove": self._api_writer_remove,
                "/api/commission": self._api_commission,
                "/api/generate": self._api_generate,
            }.get(parsed.path)
            if handler is None:
                return self._send_json({"ok": False, "error": "no such endpoint"}, 404)
            handler(body)
        except BrokenPipeError:
            pass
        except Exception as exc:
            self._send_json({"ok": False, "error": str(exc)}, 500)

    def _with_memory(self, fn, verb: str = "follow") -> Dict:
        """The CLI verb protocol: sync -> verb -> render-only file write.

        NL-81 EMBEDDED DEGRADE (contract §5.2): a stale memory.md does NOT kill
        the verb — this is a reader mid-session, and refusing their tap over a
        file they may not even know exists is worse than the disease. The
        opening sync skipped the import and rewrote nothing; the verb runs on
        database state, the file rewrite is skipped too (so "neither side
        mutated" stays true for the file), and the refusal rides back in the
        JSON response where the client can surface it.

        NL-17-M1c — THE WRITE-REFUSAL PAYLOAD (R-WRITE). A MemorySyncError from
        the OPENING sync means the verb never ran: nothing was followed, so the
        client's mark is ○ and the line is loud. The payload carries its CLASS
        (`refusal: "write"`) and its UI-lane reason/remedy, keyed off the arm
        named at the raise site — never str(exc), which is a good CLI sentence
        and an unlawful UI one. `error` keeps that CLI sentence for diagnostics.

        `verb` swaps the frame's verb for the three acts that route through here
        (follow / switch / unfollow). A refusal on switch or unfollow leaves an
        EXISTING follow standing, so those never wear ○ — the mark would report
        "nothing followed" over a live follow, the same lie in the other
        direction (content pass §4.1). The client owns that split; the server
        states which act was refused and why.
        """
        con = db.connect()
        try:
            try:
                sync = memory.sync_memory(con)
            except memory.MemorySyncError as exc:
                return _write_refusal(exc, verb)
            result = fn(con)
            warnings = list(sync.guard_lines())
            if not sync.stale_refusal:
                try:
                    memory.write_memory_file(con)
                except OSError as exc:      # noqa: PERF203 — one narrow arm
                    # THE VERB ALREADY SUCCEEDED. The follow is recorded; only
                    # the render-only file refresh failed. ○ here would lie
                    # (M1c's whole charge), and so would a 500 — the generic
                    # handler answers ok:False, which the client reads as a
                    # write refusal. Keep the success, and put the adjacent
                    # fact where NL-110 will render it. M1c renders `warnings`
                    # NOWHERE (build tooth); this is the fact waiting for that
                    # surface, not a new silent hole: the DB is the record and
                    # the next sync reports the file as stale.
                    warnings.append(f"cannot write memory.md ({exc})")
            if warnings and isinstance(result, dict):
                result["warnings"] = list(result.get("warnings") or []) + warnings
            return result
        finally:
            con.close()

    def _topic_arg(self, body: Dict) -> str:
        """The posted thread name, as a MEMORY KEY (NL-139 fix loop 1, QA F-1).

        Clamped HERE, at the door, because everything below this line uses the
        value to COMPARE against stored rows, to STORE, to ECHO back, or to
        UNFOLLOW — and storage is clamped. A caller keying on the raw string
        speaks a different key from the database, and that does not fail
        loudly: it reads as "no such thread".

        The bug that put this here: `_seed_thread`'s RESUMED-vs-NEW predicate
        matched `lower(topic) = lower(<raw headline>)`. For a canonical topic
        over `memory.TOPIC_MAX_CHARS` — the founder's story_slots carry titles
        to 108 chars today, against an 80 clamp — the predicate MISSED the row
        its own seed had just created, so an unfollow→refollow took the NEW
        landing on an EXISTING thread: `_set_altitude_columns` overwrote
        altitude / disclosure / alt_label and the handler answered
        `seeded: True`, which LICENSES THE SETTLE TO RE-AIM A THREAD THE READER
        ALREADY HAD. That is the exact property gate F4 / QA-3 exists to
        protect, and the one `_seed_thread`'s own docstring promises ("the
        settle must never re-aim a thread whose identity someone already
        decided").

        This is the choke point for every topic-keyed endpoint — follow,
        follow-seed, follow-settle, dismiss, revive, delete, note — so ONE
        clamp makes predicate, storage, response and unfollow speak one key.
        Callers that ALSO need the raw string use `_raw_topic_arg` and say why
        in place.

        The SEPARATOR rejection stays AHEAD of the clamp: a malformed name is
        refused, never silently repaired into a legal one."""
        topic = str(body.get("topic") or "").strip()
        if memory.SEPARATOR in topic:
            return ""
        return memory.clamp_topic(topic)[0]

    def _raw_topic_arg(self, body: Dict) -> str:
        """The posted name UNCLAMPED — for the two uses that are a STORY
        identity rather than a memory key:

          * `origin_story`, which `_origin_follow_row` / `_resolve_guard_row`
            match at RENDER time against raw `story_slots` titles. Clamping it
            would break origin-card recognition for every long-titled story —
            a second regression in the shape of the first.
          * the settle's model input, which should see the whole headline.

        Never use this to look up, store, or echo a thread NAME."""
        return str(body.get("topic") or "").strip()

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

    def _ref_id_for(self, con, briefing_date: Optional[str]):
        if not briefing_date:
            return None
        r = con.execute("SELECT id FROM briefings WHERE date = ?",
                        (briefing_date,)).fetchone()
        return r["id"] if r else None

    def _commit_altitude(self, con, *, name: str, altitude: str,
                         primary_entity: str = "", disclosure: str = "",
                         alt_label: str = "", confidence: str = "",
                         source: str = "auto", origin_story: str = "",
                         briefing_date: Optional[str] = None) -> Dict:
        """Store a picker follow at an altitude (the resolve auto-commit, a low/
        switch reader pick, or the degrade narrow landing) + log the instrument
        event. One place, so every commit path shares the storage contract.
        origin_story (FIX-1) records the story this follow was born from, so the
        origin card recognizes an altitude-renamed follow across reload."""
        ref_id = self._ref_id_for(con, briefing_date)
        outcome, tid = memory.add_thread_at_altitude(
            con, name, altitude=altitude, primary_entity=primary_entity,
            disclosure=disclosure, alt_label=alt_label, confidence=confidence,
            source=source, origin_story=origin_story,
            last_referenced_briefing_id=ref_id)
        # NL-139 fix loop 1 (QA F-1): echo the STORED topic, read back from the
        # row rather than re-derived from `name`. `add_thread_at_altitude`
        # clamps, so returning `name` handed the client a key the database does
        # not hold — and the client keys its unfollow/settle calls on this
        # value, so the divergence travelled. Read-back rather than
        # clamp_topic(name) on purpose: the response then reports what IS
        # stored, which stays true if the storage rule ever changes again.
        row = con.execute("SELECT topic FROM memory WHERE id = ?",
                          (tid,)).fetchone()
        stored = row["topic"] if row is not None else name
        return {"ok": True, "outcome": outcome, "topic": stored,
                "thread_id": tid}

    def _api_follow_seed(self, body: Dict) -> None:
        """THE TAP — NL-17-M1c, the thread model's first half.

        A tap COMMITS A STORY-SEEDED THREAD INSTANTLY: written locally, $0,
        nothing waits on a model. This route makes zero external calls and can
        never be refused on budget, because there is nothing here to bill. That
        is the whole point of splitting it out — under the old single route the
        reader's act was hostage to a 9-46s resolve (~2s post-NL-99) and a cap
        gate that could refuse the follow itself.

        The only way this route fails is a MEMORY WRITE refusal, and then
        nothing was followed — ○, loud, with its reason (R-WRITE)."""
        headline = self._topic_arg(body)
        if not headline:
            return self._send_json({"ok": False, "error": "topic required"}, 400)
        # NL-139 fix loop 1: `headline` is now the CLAMPED memory key (every
        # predicate, store and echo below speaks it). `raw_topic` is the story's
        # canonical topic as posted — needed unclamped because origin_story is
        # a STORY identity matched at render time against raw story_slots
        # titles, not a thread name.
        raw_topic = self._raw_topic_arg(body)
        # data-origin is the raw headline (the resting card carries both — see
        # _follow_control); the story's canonical topic (`raw_topic`, the
        # data-topic) is the origin key we STORE, and both are match keys.
        origin = str(body.get("origin") or "").strip() or raw_topic
        briefing_date = str(body.get("briefing_date") or "").strip() or None
        # XOR / recognition guard (FIX-1): a tap on an ALREADY-followed story is
        # the steady state, not a fresh follow. Return the committed row — never
        # a divergent second active follow (QA NO-GO: rows "…job cuts" +
        # "Volkswagen" for one story). Read-only.
        con = db.connect()
        try:
            existing = _resolve_guard_row(con, headline, origin)
            # NL-17 M1 — WHO DECIDES WHETHER A SETTLE RUNS. It used to be the
            # client, on `seeded === true`: a brand-new thread settled, and a
            # thread that already existed never did. That was right while the
            # only settle was the one at birth, and it is wrong now that case
            # (a) threads stay AUTO-WIDENABLE — a later story rejoining a thread
            # that never found an actor is exactly the moment the ruling wants a
            # fresh attempt, and case (b)'s ONE retry lives in the same question.
            # So the SERVER answers it, from the record, and the client just
            # obeys: eligibility depends on the settle history and on a spend
            # bound, and neither is a thing a browser can be trusted to know.
            existing_may_settle = bool(
                existing
                and (existing.get("altitude") or "") == "narrow"
                and memory.settle_allowed(con, existing.get("id")))
        finally:
            con.close()
        if existing:
            return self._send_json({
                "ok": True, "state": "committed", "seeded": False,
                "settle": existing_may_settle,
                "topic": existing["topic"],
                "altitude": existing.get("altitude") or "",
                "disclosure": existing.get("disclosure") or "",
                "alt_label": existing.get("alt_label") or ""})
        out = self._with_memory(
            lambda con: self._seed_thread(con, headline, briefing_date,
                                          origin_story=raw_topic))
        if out.get("ok") is False:
            return self._send_json(out)          # R-WRITE — nothing followed
        return self._send_json(out)

    def _seed_thread(self, con, headline: str,
                     briefing_date: Optional[str],
                     origin_story: str = "") -> Dict:
        """Commit the story-seeded thread. TWO landings, and the difference is
        the whole reason this is not one call to _commit_altitude:

          NEW      -> a narrow, story-seeded follow. `seeded: True` licenses the
                      settle: this thread has no scope anyone chose, so naming
                      one is the system doing its job.
          RESUMED  -> a thread that EXISTED comes back as itself, at whatever
                      scope it had, with its kept entries. `seeded: False` — the
                      settle must never re-aim a thread whose identity someone
                      already decided. add_thread_at_altitude would have
                      overwritten those columns with narrow/'', silently
                      converting "picked up where it left off" into "quietly
                      re-scoped behind your back".

        THE PREDICATE IS EXISTENCE, NOT SCOPE (gate F4 / QA-3). It first read
        `prior is not None and prior['altitude']`, which quietly excluded every
        pre-0019 legacy row — and those are not empty threads: on his real
        archive they are most of his follows and they carry real history (Strait
        of Hormuz alone has 12 ledger entries). This milestone put unfollow one
        tap from every entry of a thread, so one unfollow+refollow would have
        re-seeded a 12-entry thread HE named, dropped its resume clause, and let
        the settle rename it. A row with no altitude never settled and was never
        renamed; it comes back BARE, which is the honest unmigrated render, and
        the settle stays out of it.

        NL-139 fix loop 1 (QA F-1): `headline` arrives CLAMPED from
        `_topic_arg`, which is what makes the predicate below able to find the
        row this function's own NEW landing stored. Keyed on the raw name it
        missed every canonical topic over TOPIC_MAX_CHARS, and the miss was
        silent — it looked exactly like "never followed", so the NEW landing
        ran on an existing thread and answered `seeded: True`. `topic` is
        echoed from the STORED row for the same reason: a response carrying a
        key the client cannot use to unfollow is the same divergence one layer
        out. `origin_story` stays RAW — it is a story identity, not a thread
        name (see `_raw_topic_arg`)."""
        prior = None
        try:
            prior = con.execute(
                "SELECT id, topic, altitude, disclosure, alt_label FROM memory"
                " WHERE lower(topic) = lower(?)", (headline,)).fetchone()
        except sqlite3.OperationalError:      # pre-0019 DB — no altitude columns
            prior = None
        if prior is not None:
            from . import memory_core
            outcome = memory.add_thread(
                con, headline,
                last_referenced_briefing_id=self._ref_id_for(con, briefing_date))
            kept = len(memory_core.ledger_for_thread(con, prior["id"]))
            # `settle` mirrors _api_follow_seed's guard-lane answer for the
            # RESUME landing: a resumed thread settles only if it is still
            # story-seeded AND the record allows another attempt. A thread that
            # came back at a scope someone already decided stays untouched —
            # the reason `seeded: False` existed in the first place.
            return {"ok": True, "outcome": outcome, "seeded": False,
                    "state": "committed", "topic": prior["topic"],
                    "thread_id": prior["id"],
                    "settle": (_row_col(prior, "altitude") == "narrow"
                               and memory.settle_allowed(con, prior["id"])),
                    "altitude": _row_col(prior, "altitude"),
                    "disclosure": _row_col(prior, "disclosure"),
                    "alt_label": _row_col(prior, "alt_label"),
                    "resumed": outcome == "revived", "kept": kept}
        out = self._commit_altitude(
            con, name=headline, altitude="narrow", source="seed",
            origin_story=origin_story or headline,
            briefing_date=briefing_date)
        out.update({"state": "committed", "seeded": True, "settle": True,
                    "altitude": "narrow", "disclosure": "", "alt_label": ""})
        return out

    def _log_settle(self, thread_id, topic: str, outcome: str, attempt: int,
                    *, entity_id=None, detail: str = "") -> None:
        """Append one settle outcome (0025) from a landing that does NOT hold a
        write transaction of its own — the cap refusal, the resolver raise, the
        low-confidence finding, the write-refused re-aim. (The confident landing
        logs inside `_settle_onto`'s transaction instead, because there the
        outcome and the re-aim must be atomic with each other.)

        FAILING TO LOG NEVER FAILS THE REQUEST, and never fails it SILENTLY
        either: the reader's follow already stands and is already on screen
        saying so, so a 500 here would take a working follow away over a
        forensic write. A pre-0025 database is the expected case on a server the
        principal has not restarted yet — the sanction rides that restart — so
        it degrades to a printed line, the same lane every other server-side
        diagnostic uses.
        """
        try:
            con = db.connect()
            try:
                with con:
                    memory.log_settle_outcome(
                        con, thread_id, topic, outcome, attempt=attempt,
                        entity_id=entity_id, detail=detail)
            finally:
                con.close()
        except Exception as exc:  # noqa: BLE001 — never 500 over a forensic write
            print(f"settle-outcome log failed ({outcome} for {topic!r}): {exc}",
                  flush=True)

    def _api_follow_settle(self, body: Dict) -> None:
        """THE SETTLE — the thread model's second half, and it is INVISIBLE.

        The follow already exists (the seed committed it). All this decides is
        what ELSE the thread covers. Three landings, and only ONE of them
        renders anything:

          * a confident name -> the follow is RE-AIMED at it and the client
            announces the name-change once. Never a second row: this MOVES the
            seeded thread (from_topic), so a thread has exactly one identity.
          * unconfident / failed / no thread to settle -> NOTHING renders. The
            story-scoped follow simply stands, and its own name is the whole
            disclosure. THE ASK IS DEAD (his ruling ④); so is the apology.
          * over budget -> R-COVERAGE. The follow STANDS; only the broadening
            was refused, so the client renders nothing here either. This is why
            the ruled cap sentence retires from reader copy (content pass §5.1
            Arm A): it described a pre-commit refusal that no longer exists, and
            the case it now describes has no reader-facing render at all. Its
            constant is marked RETIRED-NOT-RENDERED in labels.py, and this
            module may not so much as name it — the sweep marker's claim is
            enforced by a source grep, deliberately.

        NL-17 M1 — WHAT IS NEW HERE, and it is all record, never chrome. Every
        landing above now APPENDS ITS OUTCOME to follow_settle_events (0025), and
        the confident-name landing additionally runs the MINT-OR-MATCH DOOR
        (entities.mint_or_match) so a named actor becomes one durable identity
        that every thread about it points at. The reader-facing behaviour of the
        three landings is BYTE-IDENTICAL to what shipped — cases (a) and (b) of
        the 2026-08-08 product ruling are both silence, and silence is what this
        route already rendered. The events are what make that silence
        accountable, and they are what the retry bound and the auto-widen
        distinction are decided from:

          settled_entity  an actor was named AND minted/matched. memory.entity_id
                          is set in the SAME transaction as the re-aim.
          settled_none    the settle RAN and produced no entity — low confidence,
                          a storyline (a broader story is not an actor), or a
                          class outside org/place/person. Case (a): terminal,
                          lawful, and auto-widenable forever.
          settle_failed   the settle could not run to an answer (raise, dead
                          lane, dead transport, cap refusal). Case (b): silence,
                          and exactly ONE re-settle, bounded HERE — a bound the
                          client is trusted to honour is not a bound.

        SUBSCRIPTION lane by default (NL-99 / THE $0-RUN LAW, 2026-07-26) —
        ~2s measured with thinking suppressed, $0 charged."""
        headline = self._topic_arg(body)
        if not headline:
            return self._send_json({"ok": False, "error": "topic required"}, 400)
        # NL-139 fix loop 1 (QA F-1) — the settle needs BOTH keys, and mixing
        # them up is how the seed lane broke. `raw_topic` is the story identity
        # (origin_story matching + the model's own input, which should see the
        # whole headline); `current` is a MEMORY KEY, so it is clamped like any
        # other thread name before `_resolve_guard_row` compares it.
        raw_topic = self._raw_topic_arg(body)
        origin = str(body.get("origin") or "").strip() or raw_topic
        current = memory.clamp_topic(
            str(body.get("topic_current") or "").strip())[0] or headline
        # Nothing to settle onto: the seed is gone (unfollowed mid-settle, or
        # never landed). Silence is the honest answer — re-creating the follow
        # here would resurrect an act the reader just undid.
        con = db.connect()
        try:
            seeded = _resolve_guard_row(con, current, origin)
            # asked while the connection is open, so the eligibility answer and
            # the row it is about come from ONE read of the record.
            may_settle = memory.settle_allowed(con, (seeded or {}).get("id"))
            attempt = memory.settle_attempts(con, (seeded or {}).get("id")) + 1
        finally:
            con.close()
        if not seeded:
            return self._send_json({"ok": True, "state": "unsettled",
                                    "settled": False})
        if (seeded.get("altitude") or "") != "narrow":
            # already settled (a reload, a double-fire, a reader switch) — the
            # settle never re-aims a thread the reader or an earlier settle
            # already named. The mutation law, unchanged.
            return self._send_json({"ok": True, "state": "committed",
                                    "settled": False,
                                    "topic": seeded["topic"],
                                    "altitude": seeded.get("altitude") or "",
                                    "disclosure": seeded.get("disclosure") or "",
                                    "alt_label": seeded.get("alt_label") or ""})
        # THE RETRY BOUND (product ruling case (b)), enforced at the door that
        # can spend. memory.settle_allowed carries the whole rule: a thread whose
        # last settle FAILED gets exactly one more attempt, while a terminal-none
        # thread stays widenable indefinitely. This refusal is silent and FREE —
        # it returns above the cap gate, and no event is appended, because
        # nothing was attempted and an outcome row for an attempt that never ran
        # would corrupt the very streak the bound reads.
        if not may_settle:
            return self._send_json({"ok": True, "state": "unsettled",
                                    "settled": False, "retry_exhausted": True})
        # R1 CAP GATE (2026-07-25, PREFLIGHT gate order). Everything above this
        # line is free; everything below can SPEND. resolve_cost_gate is the
        # falsifier's own arithmetic (one estimate, one cap, one implementation
        # — a second copy of this math is how BUG-1 shipped in two places).
        # Refuse BEFORE any transport. 409 matches _api_generate's staleness
        # refusal: a well-formed request declined on policy. THE REFUSAL NO
        # LONGER COSTS THE READER THEIR FOLLOW — it costs them the broadening.
        stands = {"ok": False, "refusal": "coverage", "state": "refused",
                  "topic": seeded["topic"], "follow_stands": True}
        tid, ttopic = seeded["id"], seeded["topic"]
        try:
            allowed, est_usd, cap_usd = follow_altitude.resolve_cost_gate(headline)
        except ValueError as exc:        # malformed BUDGET_CAP_USD_PER_RUN
            self._log_settle(tid, ttopic, "settle_failed", attempt,
                             detail=f"budget-cap-malformed: {exc}")
            return self._send_json(dict(stands, detail=str(exc)), 409)
        if not allowed:
            # R-COVERAGE. The reader sees nothing — but the RECORD says the
            # settle never got to run, which is a failure of the settle and not
            # a finding of "no entity". Filing it as settled_none would tell the
            # backfill this thread was examined and found actorless; it was not
            # examined at all. It is also therefore retry-eligible.
            self._log_settle(tid, ttopic, "settle_failed", attempt,
                             detail="coverage")
            return self._send_json(dict(
                stands,
                detail=(f"estimated coverage check ${est_usd:.5f} exceeds "
                        f"BUDGET_CAP_USD_PER_RUN ${cap_usd:.2f} — no call was "
                        "made and the story-scoped follow stands"),
                est_usd=est_usd, cap_usd=cap_usd), 409)
        try:
            res = follow_altitude.resolve_altitude(
                # NL-139 fix loop 1: the MODEL gets the whole headline. This is
                # the settle's evidence, not a memory key — truncating it would
                # hand the namer less story to name from for no bound benefit
                # (nothing here is stored).
                follow_altitude.ThreadInput(thread_id=None, topic=raw_topic),
                retry_transport=False)   # R3: a reader waits — degrade on the
                                         # first timeout window, never retry to ~25s
        except Exception as exc:  # noqa: BLE001 — AltitudeError/LaneUnavailable/transport
            # The settle failed. NOTHING renders: the follow the reader made is
            # untouched and already correct at its own scope. Case (b): the
            # 07-18 failure copy is buried, the silence is the ruling, and this
            # event is the only place the failure exists.
            self._log_settle(tid, ttopic, "settle_failed", attempt,
                             detail=f"{type(exc).__name__}: {exc}")
            return self._send_json({"ok": True, "state": "unsettled",
                                    "settled": False, "reason": str(exc)})
        if res.confidence == "low":
            # UNCONFIDENT — case (c), FINAL FORM (supplemental ruling
            # 2026-08-08, product RECONVENE §R.3; supersedes §5's case (c),
            # which legislated for a picker M1c had already deleted).
            #
            # THE STATE LAYER STAYS SILENT, unchanged and everywhere: nothing is
            # attached, the thread stays at seed, this response renders nothing,
            # the today card is untouched. THE SETTLE NEVER ANNOUNCES.
            #
            # What is new is that the resolver's NAMED CANDIDATES are no longer
            # discarded. "Low" in this prompt means it came back holding two
            # clean options it would not choose between — and the ratified
            # management-surface "Instead:" row (v11 ruling ②'s own carve-out:
            # deep view + Following, banned on today cards) is where a standing
            # affordance lawfully lives. So they persist on the event, and those
            # two surfaces read them. MANAGEMENT SURFACES MAY AFFORD.
            #
            # settled_low, NOT settled_none: a thread that found two plausible
            # things and could not pick between them is not a thread that found
            # nothing, and the principal rules on that second count (0025).
            self._log_settle(
                tid, ttopic, "settled_low", attempt,
                detail=memory.encode_settle_candidates(
                    _settle_candidate_pair(res),
                    primary_entity=res.primary_entity))
            return self._send_json({"ok": True, "state": "unsettled",
                                    "settled": False})
        name, _cls = follow_altitude.split_qualifier(res.disclosure)
        # NL-139 fix loop 1: this becomes a STORED thread name, and its first
        # two sources are MODEL output. `move_follow_altitude` clamps it (door
        # 5) so the column is bounded either way; the fallback uses the clamped
        # `headline` rather than the raw so the no-name case stores the same
        # key the seed already stored instead of a longer one that would then
        # rename the thread for no reason.
        name = name or res.primary_entity or headline
        out = self._with_memory(lambda con: self._settle_onto(
            con, from_topic=seeded["topic"], name=name, res=res,
            origin_story=raw_topic, attempt=attempt), verb="follow")
        if out.get("ok") is False:
            # The re-aim could not be written. The SEEDED follow still stands —
            # so this is not ○ and it is not loud: it is the same silence as any
            # other unlanded settle. Nothing on screen is false.
            #
            # Logged from OUT HERE, not inside _settle_onto: the write refusal
            # comes from _with_memory's opening sync, so the transaction the
            # outcome would have ridden never opened. Nothing was re-aimed and
            # nothing was minted, so the honest outcome is a failure — and a
            # retry-eligible one.
            self._log_settle(tid, ttopic, "settle_failed", attempt,
                             detail=f"write-refused: {out.get('error') or ''}")
            return self._send_json({"ok": True, "state": "unsettled",
                                    "settled": False, "error": out.get("error")})
        out.update({"state": "committed", "settled": True,
                    "altitude": res.altitude, "disclosure": res.disclosure,
                    "alt_label": res.alt_label, "confidence": res.confidence})
        return self._send_json(out)

    def _settle_onto(self, con, *, from_topic: str, name: str, res,
                     origin_story: str, attempt: int = 1) -> Dict:
        """MOVE the seeded thread onto the settled coverage — never a second
        row. Reuses the switch lane (move_follow_altitude) exactly as the
        mockup's seam note specs it: "its landing applied via the existing
        switch lane as a system-initiated re-aim".

        initiator="org" (gate F3): a system re-aim must not sign the NL-81
        forensic log with the reader's name. Rename tombstones from this lane
        stamp actor='org'; a settle-merge leaves the merged-away row's
        dismissed_via NULL, because no person's verb dismissed it.

        NL-17 M1 — THE MINT-OR-MATCH DOOR RUNS HERE, and here is the only place
        it runs. Three facts have to become true together or not at all: the
        thread is re-aimed, the entity exists, and the thread points at it. They
        share this function's connection and therefore its transaction, so a
        crash between them cannot leave a thread claiming an entity that was
        never minted (or an entity nothing references).

        WHICH LANDINGS MINT: only `altitude == 'entity'` with a disclosure whose
        class maps to org/place/person. A STORYLINE settle names a broader story,
        and a story is not an actor — it re-aims the thread (a real, useful
        outcome the reader sees as a better name) and mints NOTHING, entity_id
        stays NULL, outcome `settled_none`. That is not a gap: it is the ruling's
        own vocabulary, where "no broader concept" means no ENTITY, and it is
        why the terminal-none bucket in the backfill has to be counted rather
        than assumed empty.
        """
        row = con.execute(
            "SELECT id FROM memory WHERE lower(topic) = lower(?)"
            " AND status = 'active'", (from_topic,)).fetchone()
        if row is None:
            # the seed is gone (unfollowed mid-settle). No thread, no outcome:
            # an event whose thread_id names a row that no longer exists would
            # make the backfill's counts describe a thread nobody holds.
            return {"ok": True, "outcome": "gone", "topic": from_topic}
        survivor = memory.move_follow_altitude(
            con, row["id"], new_name=name, altitude=res.altitude,
            primary_entity=res.primary_entity, disclosure=res.disclosure,
            alt_label=res.alt_label, confidence=res.confidence, source="auto",
            log_correction=False, initiator="org")
        tid = survivor if survivor else row["id"]
        # THE DOOR. `entity_id` is written even when it is None — that write is
        # the first-class "storyline thread, no broader concept" semantic, not a
        # no-op, and on a settle-MERGE the survivor may be carrying a stale
        # entity_id from a life the reader has since re-aimed.
        with con:
            eid, detail = _apply_entity_identity(
                con, tid, altitude=res.altitude, disclosure=res.disclosure,
                primary_entity=res.primary_entity)
            memory.log_settle_outcome(
                con, tid, name,
                "settled_entity" if eid is not None else "settled_none",
                attempt=attempt, entity_id=eid,
                detail="" if eid is not None else detail)
        # NL-139 fix loop 2 (QA R-1): echo the STORED topic. This was the one
        # lane that missed the read-back rule the rest of the perimeter
        # follows — `name` here is MODEL output, so a >TOPIC_MAX_CHARS settle
        # name stored 80 chars and announced 139. Functionally absorbed today
        # (every verb the client makes next passes a clamped door, so the raw
        # key still resolves — QA executed that), which is exactly why it is
        # fixed as a CONTRACT rather than as a bug: the client is told the
        # thread's new name, and a name the database does not hold is the
        # wrong thing to say even when nothing downstream trips over it.
        settled = con.execute(
            "SELECT topic FROM memory WHERE id = ?", (tid,)).fetchone()
        return {"ok": True, "outcome": "settled",
                "topic": settled["topic"] if settled else name,
                "thread_id": tid}

    def _api_follow_at(self, body: Dict) -> None:
        """A reader PICK at a chosen altitude (an "Instead:" candidate, or a
        switch from a committed follow). Pre-altituded — the resolver is NOT
        re-consulted (mutation law). A switch (from_topic present + active) MOVES
        the existing follow; otherwise it creates one.

        ===================================================================
        FIX LOOP 1 — THE SWAP DOOR. This route is where the ratified
        "Instead:" affordance lands, and it was already almost all of what the
        supplemental ruling asks for: one tap, instant, and $0 ON THE TAP PATH
        because the candidates were resolved at settle time and nothing here
        consults a model. What it gained is the ENTITY-IDENTITY half — a swap
        now runs the same mint-or-match door the system settle runs, so aiming
        at an actor gives the thread that actor's identity, and aiming at a
        storyline clears it. Weight REPLACES, never stacks (criterion (b)),
        because a thread holds at most one entity_id at any instant.

        THE PIN-SCOPE TRIPWIRE, CHECKED AND CLEAR (binding fix-loop item). "A
        settled thread is never re-aimed" guards the SYSTEM settle route, and
        it still does — that guard lives in `_api_follow_settle`'s altitude
        check and in `memory.settle_allowed`, and this route touches NEITHER.
        It never asks whether a settle is allowed, never calls the resolver,
        and never appends a settle outcome. The two doors were already distinct
        endpoints; adding the identity write to this one moved nothing across
        that line, so the pin did not have to widen and did not.
        The distinctness is asserted, not assumed:
        test_the_swap_door_is_distinct_from_the_system_settle_route.
        """
        name = str(body.get("name") or "").strip()
        altitude = str(body.get("altitude") or "").strip()
        if not name or memory.SEPARATOR in name:
            return self._send_json({"ok": False, "error": "name required"}, 400)
        # NL-17 M1 — THE PICK DOOR REFUSES 'narrow'. This route is where a
        # READER-CHOSEN altitude becomes a stored one (source='pick'), and
        # reader-chosen narrow is exactly the class the principal's amendment (i)
        # bans: the council's lifecycle distinction keeps the system-owned,
        # auto-widenable SEED (written by _seed_thread, source='seed') and kills
        # the deliberate narrow act that forks a thread's vocabulary forever.
        # Deleting the rung from both renderers removes the OFFER; this removes
        # the ROUTE, so the banned row cannot be minted by a request that never
        # came from a rendered control. PICKABLE_ALTITUDES is narrower than
        # STORED_ALTITUDES on purpose, and the gap between them is the ruling.
        if altitude not in memory.PICKABLE_ALTITUDES:
            return self._send_json({"ok": False, "error": "bad altitude"}, 400)
        # NL-139 fix loop 1 (QA F-1, same class one endpoint over — this route
        # does NOT read `topic`, so `_topic_arg` never saw it). BOTH values are
        # memory keys: `name` becomes a stored thread name, `from_topic`
        # RESOLVES the row to move. Unclamped, a switch away from a >80-char
        # thread found no row and fell through to the CREATE branch — a
        # divergent second active follow for one story, which is precisely the
        # double the XOR guard exists to prevent. `origin` below stays raw: it
        # is a story identity (see _raw_topic_arg).
        name = memory.clamp_topic(name)[0]
        from_topic = memory.clamp_topic(
            str(body.get("from_topic") or "").strip())[0]
        disclosure = str(body.get("disclosure") or "")
        alt_label = str(body.get("alt_label") or "")
        primary_entity = str(body.get("primary_entity") or "")
        # the story this pick was made from (FIX-1) — stored on a CREATE so a
        # low-confidence pick's altitude-renamed follow is recognized on reload;
        # a MOVE keeps the moved row's original origin (never rewritten).
        origin = str(body.get("origin") or "").strip()
        briefing_date = str(body.get("briefing_date") or "").strip() or None

        def verb(con):
            if from_topic:
                row = con.execute(
                    "SELECT id FROM memory WHERE lower(topic) = lower(?)"
                    " AND status = 'active'", (from_topic,)).fetchone()
                if row is not None:
                    # R2: the switch may revive-merge onto a dismissed holder of
                    # `name` — report the SURVIVING active row, not the moved one.
                    survivor = memory.move_follow_altitude(
                        con, row["id"], new_name=name, altitude=altitude,
                        primary_entity=primary_entity, disclosure=disclosure,
                        alt_label=alt_label, source="pick")
                    tid = survivor if survivor else row["id"]
                    # FIX LOOP 1 — the swap's identity half. Same door the
                    # system settle uses, so one rule decides what a thread's
                    # identity IS however it got aimed; and it runs inside this
                    # verb's transaction, so the re-aim and the identity land
                    # together or not at all. NO settle outcome is appended:
                    # nothing settled, a reader chose, and writing one here
                    # would let a reader's tap move the retry bound.
                    with con:
                        _apply_entity_identity(
                            con, tid, altitude=altitude, disclosure=disclosure,
                            primary_entity=primary_entity)
                    # NL-139 fix loop 1: echo the STORED name, same reason as
                    # _commit_altitude — the client keys its next call on this.
                    moved = con.execute(
                        "SELECT topic FROM memory WHERE id = ?", (tid,)).fetchone()
                    return {"ok": True, "outcome": "moved",
                            "topic": moved["topic"] if moved else name,
                            "thread_id": tid}
            out = self._commit_altitude(
                con, name=name, altitude=altitude, primary_entity=primary_entity,
                disclosure=disclosure, alt_label=alt_label, source="pick",
                origin_story=origin, briefing_date=briefing_date)
            # the CREATE branch takes the same door — a pick that lands a NEW
            # thread at an entity rung deserves the same identity a swap gets,
            # or the same tap would mean two different things depending on
            # whether a thread happened to exist.
            if out.get("ok") is not False and out.get("thread_id"):
                with con:
                    _apply_entity_identity(
                        con, out["thread_id"], altitude=altitude,
                        disclosure=disclosure, primary_entity=primary_entity)
            return out

        # M1c: a refused SWITCH leaves the existing follow standing — the frame's
        # verb says so, and the client leaves the ● state line untouched.
        out = self._with_memory(verb, verb="switch")
        if out.get("ok") is False:
            return self._send_json(out)
        out.update({"state": "committed", "altitude": altitude,
                    "disclosure": disclosure, "alt_label": alt_label})
        self._send_json(out)

    def _api_dismiss(self, body: Dict) -> None:
        topic = self._topic_arg(body)
        if not topic:
            return self._send_json({"ok": False, "error": "topic required"}, 400)

        def verb(con):
            # SYMMETRY LAW: unfollow from the same surface. Log the altitude
            # correction FIRST (Axel's instrument — a within-24h unfollow of a
            # medium auto-commit counts), then dismiss.
            memory.record_altitude_correction(con, topic)
            if memory.dismiss_thread(con, topic):
                return {"ok": True, "outcome": "unfollowed"}
            # M1c ROUTING CALL (no new copy invented): there is no active row,
            # so the act's GOAL STATE already holds. Answering ok:false here
            # would push a truthful tap into the refusal frame and make it say
            # "your memory file couldn't be saved" — false in a new direction.
            # The honest answer is the receipt: you are not following this.
            return {"ok": True, "outcome": "already"}

        # M1c: an unfollow the WRITE refuses leaves the follow STANDING — the
        # line still says Following, which is TRUE, so the mark stays ● and the
        # refusal renders beneath it. Never ○: nothing was unfollowed, but
        # something IS followed.
        self._send_json(self._with_memory(verb, verb="unfollow"))

    def _api_revive(self, body: Dict) -> None:
        topic = self._topic_arg(body)
        if not topic:
            return self._send_json({"ok": False, "error": "topic required"}, 400)
        # NL-139: 'added-truncated' is a SUCCESSFUL add whose stored name is
        # shorter than the one posted (memory.TOPIC_MAX_CHARS). Listing it here
        # is not cosmetic — omitted, this endpoint would answer ok:false for a
        # revive that actually happened.
        self._send_json(self._with_memory(
            lambda con: {"ok": memory.add_thread(con, topic) in
                         ("revived", "already-active", "added",
                          "added-truncated")}))

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

    def _api_schedule_pause(self, body: Dict) -> None:
        """NL-152 — the settings toggle. ONE state: data/SCHEDULE_PAUSED.

        The answer reports the state that NOW HOLDS, re-read from disk by
        `set_paused`, never the state that was asked for. A toggle that reports
        its own argument back is a switch that always looks like it worked —
        including on a read-only data dir, where nothing moved."""
        paused = not bool(body.get("enabled", True))
        now_paused = schedule.set_paused(paused)
        if now_paused != paused:
            return self._send_json(
                {"ok": False, "paused": now_paused,
                 "error": "That didn’t save — the schedule is unchanged."}, 500)
        self._send_json({"ok": True, "paused": now_paused})

    def _api_schedule_hour(self, body: Dict) -> None:
        """NL-152 — the generation hour, plus the plist truth.

        THE RE-INSTALL DISCLOSURE RIDES ON THE SAVE RESPONSE and is computed
        from `schedule.status()`, not assumed: the commands are worth showing
        only when an agent file actually exists and its baked hour actually
        disagrees with what he just chose. Telling a reader with no agent
        installed to `launchctl bootout` a label that was never loaded is a
        command that fails in his terminal for no reason."""
        raw = body.get("hour")
        try:
            hour = int(raw)
        except (TypeError, ValueError):
            return self._send_json(
                {"ok": False,
                 "error": "Nothing was saved — pick a whole hour of the day."},
                400)
        ok, msg = settings_set_hour(hour)
        if not ok:
            return self._send_json({"ok": False, "error": msg}, 400)
        st = schedule.status()
        stale = (st["installed"] and isinstance(st["plist_hour"], int)
                 and st["plist_hour"] != hour)
        self._send_json({
            "ok": True, "detail": msg, "hour": hour,
            "needs_reinstall": bool(stale),
            "plist_hour": st["plist_hour"] if stale else None,
            "commands": schedule.reinstall_commands() if stale else [],
        })

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

    def _api_commission(self, body: Dict) -> None:
        """THE FOUND ACT — Stage-0 C1, and the build-blocking ordering (SEAM 2).

        The act itself is `_commission_answer` (module level, above topic_remove):
        it computes the whole answer off the socket so its broad guard cannot
        double-send, and so QA-9's falsifier can read the wire's exact contents
        without a browser. This method is the socket and nothing else — every
        string it sends came out of `commissioning.reader_refusal()`."""
        payload, status = _commission_answer(body)
        self._send_json(payload, status)

    def _api_generate(self, body: Dict) -> None:
        # The teeth of the staleness guard (2026-07-16 incident): writing an
        # edition with stale code is the failure this batch exists to stop.
        # Refuse the trigger when the running code predates disk — reading is
        # still served, and the banner explains the one-line restart.
        if _server_is_stale():
            return self._send_json(
                {"ok": False, "error": labels.STALENESS_REFUSAL}, 409)
        # STAGE-0 C1 / SEAM 2, THE BELT. A generate on a profile with no
        # interests cannot succeed — run_rank refuses it — and the refusal it
        # raises is the CLI sentence naming a profile and a filesystem path,
        # surfaced verbatim in the failure panel. Refusing at the trigger means
        # that sentence is never generated in the first place, on this route or
        # any other: the founding page's "Try again" lands here too.
        if not config.load_sources().has_interests:
            return self._send_json(
                {"ok": False, "error": labels.COMMISSION_FOUND_REFUSAL}, 409)
        # NL-146 fix loop 1 (QA F-2) — THE CROSS-PROCESS DOOR.
        #
        # `GEN_JOB.start()` guards duplicates in THIS process. The 6am launchd
        # fire is another process, and for the whole ~30 minutes it runs this
        # door was open: pressing Generate started a second full pipeline over
        # the same date — double spend and two writers racing one SQLite file.
        # Worst at the wake-coalesced fire, which is the exact moment he opens
        # the lid, sees no edition, and presses the button.
        #
        # REFUSED WITH THE REASON, not with a generic 409: a refusal that does
        # not say a run is already going reads as the app being broken, and the
        # next thing a reader does with a broken button is press it again.
        # `start()` claims the slot itself, so this check is the honest ANSWER
        # rather than the enforcement — the enforcement is atomic, one layer
        # down (schedule.claim_in_flight's O_EXCL).
        held = schedule.read_in_flight()
        if held is not None and held.get("pid") != os.getpid():
            when = _utc_hm(held.get("started_at"))
            return self._send_json(
                {"ok": False,
                 "error": (labels.INFLIGHT_REFUSAL.format(when=when) if when
                           else labels.INFLIGHT_REFUSAL_NO_TIME)}, 409)
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
