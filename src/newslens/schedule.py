"""NL-146 — SCHEDULED GENERATION.

His flow word (2026-08-09): "since it takes so long to generate, maybe scheduled
generation — the user just opens, reads, closes." The edition is ready BEFORE he
opens the app; open→read→close replaces open→generate→wait→read.

WHAT THIS MODULE IS AND IS NOT
------------------------------
It is the *entry* a scheduler calls and the *ladder* that entry climbs. It is
NOT a scheduler. macOS already has one (launchd), it is better at this than
anything we would write, and it survives reboots. So:

    launchd  ──fires──▶  `newslens schedule run`  ──▶  run_scheduled()  ──▶  generate

THE ORG NEVER INSTALLS THE LAUNCHD AGENT. This module RENDERS a plist and PRINTS
install instructions; his hands run `launchctl bootstrap`. That is law, not
preference (dispatch 2026-08-13), and it is why nothing here writes to
~/Library/LaunchAgents — the only thing this module ever does with that path is
`stat` it to tell the doctor the truth.

TESTABLE WITHOUT LAUNCHD, BY CONSTRUCTION. `run_scheduled` takes its generator,
its clock and its sleeper as arguments; the suite injects all three. launchd is
his machine's business; the CLI verb is ours, and the verb is what is under test.

WHAT IS DELIBERATELY NOT HERE
-----------------------------
* No new env var. The hour comes from the EXISTING `GENERATE_HOUR_LOCAL`
  (config.py:103, dormant since 2026-07-03 — this milestone is "scheduling ever
  returns"); the kill-switch is a FILE his hands touch, not a variable; the
  session budget reuses `BUDGET_CAP_USD_PER_RUN`.
* No schema change. Idempotence reads the `briefings` row that already exists;
  provenance and fire-decisions ride generation_log.jsonl as additive jsonl
  lines (the NL-149 precedent — every existing reader sees the keys it saw).
* No missed-fire policy. A Mac asleep at 06:00 is the COMMON case and launchd's
  own answer (fire once on wake, coalesced — launchd.plist(5)) has user-visible
  consequences that are the principal's to rule on. The null option ships: we
  add no lateness mechanism, so the behaviour is exactly launchd's. See the
  build report's stop conditions.
"""

from __future__ import annotations

import json
import os
import shutil
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

from . import config, paths, ranking

# ---------------------------------------------------------------------------
# Names. Every one of these is a surface his hands touch, so each is named ONCE
# here and referenced everywhere else (the module-constant idiom generate.py
# uses for MATERIAL_BUDGET_CHARS et al) — a second spelling of the plist label
# or the kill-switch filename is a doctor that reports on a different file than
# the scheduler reads.
# ---------------------------------------------------------------------------

LAUNCHD_LABEL = "com.newslens.generate"
PLIST_NAME = f"{LAUNCHD_LABEL}.plist"

# THE KILL SWITCH (charter item 6, mandatory). A FILE and not an env var, for
# three reasons: (1) a new env var is a checkpoint act and this milestone is
# forbidden one; (2) launchd jobs do not inherit his shell environment, so an
# `export` in .zshrc would not reach the 6am run — the one moment the switch
# exists for; (3) `touch`/`rm` is a flip he can perform in one command from any
# directory, and `ls` is how he checks it.
#
# STOPS THE SCHEDULE WITHOUT UNINSTALLING, which is the charter's exact word:
# launchd still fires, `schedule run` still starts, and it declines before it
# spends anything. The agent stays bootstrapped; nothing needs re-installing to
# resume.
KILL_SWITCH_NAME = "SCHEDULE_PAUSED"

# The fire-decision line's discriminator in generation_log.jsonl. Present on
# schedule lines, absent on run lines — the same shape `stage` uses for the
# analysis stage's instrumentation line, and read the same way (by key
# presence, never by guessing at the line's shape).
SCHEDULE_LINE_KEY = "schedule"

# THE IN-FLIGHT MARKER (fix loop 1, QA F-2). A generate is a ~30-minute
# pipeline that spends real money, and until now every surface that could see
# one running was `server.GEN_JOB` — which is THIS PROCESS's job. A launchd fire
# is another process, so during the whole scheduled run the web UI showed
# "Nothing for today yet" beside a live Generate button and the POST door's
# guard (an in-process lock) waved a second full pipeline through for the same
# date. Two pipelines, one morning, double spend.
#
# A FILE AND NOT A LOG LINE, deliberately. generation_log.jsonl is the RECORD —
# what happened, append-only, never retracted. "A run is happening right now" is
# STATE: it becomes false, and reconstructing a false-able fact by scanning the
# tail of a 660KB append-only file for the absence of a later line is a reader
# that gets slower and more fragile every morning. The file is the same class of
# thing as the kill switch (profile-scoped, under DATA_DIR, one small stat to
# read) — but it is OURS: the org writes and removes it, his hands never touch
# it, which is the exact opposite of SCHEDULE_PAUSED.
IN_FLIGHT_NAME = "RUN_IN_FLIGHT"

# WHEN A MARKER STOPS BEING BELIEVED. Liveness is the PRIMARY test — a marker
# whose pid is gone is a run that died, full stop, and the age never enters into
# it. This ceiling exists only for the one case liveness cannot answer: the OS
# handed the dead run's pid to some unrelated process, so `kill(pid, 0)`
# succeeds for a run that ended hours ago.
#
# FOUR HOURS, and the derivation rather than a vibe: the panel copy his own
# gate ratified says a full edition takes "about half an hour" (server.py, gate
# FIX-3 2026-07-26, measured on six of six logged runs; the taxed era ran ~40
# min), and the ladder's own worst case is three of those with 15 + 45 minutes
# of backoff between them — but each ATTEMPT holds the marker separately (see
# run_scheduled), so the longest any single claim can honestly live is one run.
# Four hours is ~6x the longest measured run, which is far enough out that no
# working pipeline is ever declared dead, and near enough that a pid-reuse
# false-alive wedges his Generate button for one morning rather than forever.
IN_FLIGHT_MAX_AGE_S = 4 * 60 * 60

TRIGGER_SCHEDULED = "scheduled"
TRIGGER_INTERACTIVE = "interactive"

# Fire outcomes. Returned to the CLI, written to the log, read by the doctor.
FIRED_PUBLISHED = "published"        # ran, edition of record written
FIRED_ALREADY = "already-published"  # same-day idempotence: nothing to do
FIRED_PAUSED = "paused-kill-switch"  # his hands flipped the switch
FIRED_FAILED = "failed"              # ladder exhausted, no edition
FIRED_BUDGET = "budget-stopped"      # session cap reached, ladder stopped
FIRED_IN_FLIGHT = "already-running"  # another process holds the date

# THE LADDER (charter item 4, his 2026-08-09 endorsed default): auto-retry with
# backoff, then yesterday's edition + the quiet failure note.
#
# THREE ATTEMPTS, 15 then 45 MINUTES. Both numbers are module constants and not
# env vars on purpose (no new var this milestone), and both are chosen against
# the ONE failure this ladder retries — a systemic fetch failure, i.e. "this
# machine could not reach the internet". 15 minutes is longer than a Wi-Fi
# handshake and a DHCP lease renewal; 45 more covers a router reboot or a
# carrier blip. Past an hour the honest answer is that the network is down, not
# flapping, and the reader is better served by the quiet note than by a fourth
# attempt burning rank spend into a dead link.
MAX_ATTEMPTS = 3
RETRY_BACKOFF_S: Tuple[int, ...] = (15 * 60, 45 * 60)


class ScheduleError(RuntimeError):
    """A schedule surface could not be rendered or read honestly."""


# ---------------------------------------------------------------------------
# Paths — read-only, every one of them
# ---------------------------------------------------------------------------

def plist_path(home: Optional[Path] = None) -> Path:
    """Where the launchd agent WOULD live. Never written by this module.

    `home` is an argument rather than an env override because an env override
    would be a new env var (forbidden this milestone) and because the suite
    needs to point this at a tmp dir without one — the tests pass a path, the
    doctor passes nothing.

    QA F-14: the DEFAULT now resolves through `paths.home_dir()` rather than
    `Path.home()`. Explicit callers are unchanged; what changes is the callers
    that pass nothing — `status()`, and through it the settings rows and the
    doctor — which were stat-ing the founder's real `~/Library/LaunchAgents`
    from inside the suite. `home_dir()` is the real home in a real run and the
    sandbox root wherever NEWSLENS_DATA_DIR redirects, so the seam costs no new
    env var and no behaviour change on his machine."""
    return (home or paths.home_dir()) / "Library" / "LaunchAgents" / PLIST_NAME


def kill_switch_path() -> Path:
    """The pause file. Inside DATA_DIR, so it is profile-scoped exactly like
    every other piece of a reader's state — pausing one profile's schedule must
    not pause another's.

    WRITTEN ONLY BY `set_paused`, WHICH ONLY HIS TAP CALLS (NL-152). Until then
    this module only ever read the file and his hands did the `touch`/`rm`. The
    settings toggle did not mint a second piece of state beside it — it became
    the switch's UI FACE, so there is one state and one truth: the toggle, the
    doctor line, `schedule status` and a 6am fire are all reading the same
    file's existence. A separate `settings.schedule_enabled` key would have been
    a second answer to one question, free to disagree with the file the ladder
    actually consults.

    data/ is founder-owned, and that still holds: nothing in the ORG's own
    passes writes here. `set_paused` is runtime behaviour on his machine at his
    explicit UI action — the same class as the shipped topic editor writing
    sources.yaml (server._yaml_edit, 08-12) — and the suite exercises it only in
    sandboxed DATA_DIRs."""
    return paths.DATA_DIR / KILL_SWITCH_NAME


def schedule_paused() -> bool:
    """Is the schedule switched off? An unreadable data dir is NOT a pause — a
    missing directory means nothing was ever paused, and treating a stat error
    as "paused" would silently stop his mornings on a transient filesystem
    hiccup."""
    try:
        return kill_switch_path().exists()
    except OSError:
        return False


def set_paused(paused: bool) -> bool:
    """Flip the schedule off (True) or on (False). Returns the state that now
    holds, re-read from disk rather than assumed.

    THE TOGGLE'S ONLY MECHANISM. `touch` and `rm`, spelled in Python, so the
    settings switch and his shell are doing literally the same thing to the same
    file — there is no second state to drift.

    IDEMPOTENT ON BOTH ARMS: pausing an already-paused schedule is a no-op that
    does NOT restamp the file (`exist_ok`), and resuming an already-running one
    swallows the missing-file error. A toggle that raises when it agrees with
    the world is a toggle that fails on a double-tap.

    THE RETURN IS A MEASUREMENT, not an echo of the argument. An unwritable
    data dir means the flip did not happen, and the caller must be able to tell
    him that rather than render a switch that moved on screen and nowhere else.
    """
    path = kill_switch_path()
    try:
        if paused:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.touch(exist_ok=True)
        else:
            try:
                path.unlink()
            except FileNotFoundError:
                pass
    except OSError:
        pass
    return schedule_paused()


def in_flight_path() -> Path:
    """The run-in-flight marker. Inside DATA_DIR, so it is profile-scoped
    exactly like the kill switch: two profiles generating at once is legitimate
    and must not look like one profile generating twice.

    WRITTEN AND REMOVED BY THE ORG — the mirror image of `kill_switch_path`,
    which the org only ever reads. His hands never touch this one."""
    return paths.DATA_DIR / IN_FLIGHT_NAME


def _process_alive(pid: int) -> bool:
    """Is that pid a process on this machine right now?

    `kill(pid, 0)` sends no signal; it asks the kernel to do the permission and
    existence checks and nothing else. ProcessLookupError means gone.
    PermissionError means ALIVE and owned by somebody else — a "no" here would
    be the dangerous answer, because it would declare a running pipeline dead
    and let a second one start beside it."""
    if not isinstance(pid, int) or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        # Anything else is a question we could not ask. Believing the marker is
        # the conservative answer: the cost of a false "alive" is a refused
        # button with a reason on it; the cost of a false "dead" is two
        # ~30-minute pipelines charging the same morning.
        return True
    return True


def _as_utc(dt: datetime) -> datetime:
    """Any datetime as an aware UTC one. A NAIVE datetime is read as LOCAL,
    not as UTC, because the naive datetimes this module ever sees come from
    `datetime.now()` — and reading a local wall clock as UTC is a silent
    offset-hour error in whichever direction the machine happens to sit."""
    if dt.tzinfo is None:
        dt = dt.astimezone()
    return dt.astimezone(timezone.utc)


def _marker_age_s(marker: Dict, now: Optional[Callable[[], datetime]] = None
                  ) -> Optional[float]:
    """Seconds since the marker was written, or None if it does not say."""
    try:
        started = datetime.fromisoformat(str(marker.get("started_at") or ""))
    except ValueError:
        return None
    now_fn = now or (lambda: datetime.now(timezone.utc))
    return (_as_utc(now_fn()) - _as_utc(started)).total_seconds()


def read_in_flight(now: Optional[Callable[[], datetime]] = None,
                   reap: bool = True) -> Optional[Dict]:
    """The generation running RIGHT NOW in any process, or None.

    None means exactly "nobody is generating": absent, unreadable, or a marker
    this function has just judged dead. A DEAD marker is REAPED (removed) rather
    than merely ignored, so the file on disk never disagrees with the answer
    every reader is getting — a marker that is ignored-but-present is a trap for
    the next person who cats it. `reap=False` is for readers that must not write
    (the render path, and any probe).

    STALENESS IS LIVENESS FIRST, AGE SECOND. A crashed 6am run — power loss,
    SIGKILL, a laptop lid closed on it — leaves this file behind with no process
    to match it, and a marker that outlived its process must never wedge his
    Generate button. The pid answers that. The age ceiling only covers the case
    the pid cannot: a recycled pid that makes a dead run look alive.

    A MISSING OR UNPARSEABLE `started_at` IS NOT EVIDENCE OF DEATH — liveness
    decides alone. Our writer always stamps an aware UTC time, so this case only
    arises for a marker somebody hand-edited; and there the two errors are not
    symmetric. Disbelieving a marker whose pid is a live pipeline starts a second
    ~30-minute run beside the first and charges for it, which nothing can undo.
    Believing one costs a refused button with a printed reason, is still bounded
    (the pid must be alive for it to hold at all), and `rm data/RUN_IN_FLIGHT` is
    documented in SETUP's troubleshooting as the manual override."""
    path = in_flight_path()
    try:
        raw = path.read_text(encoding="utf-8", errors="replace")
    except (OSError, ValueError):
        return None
    try:
        marker = json.loads(raw)
    except ValueError:
        # A torn or hand-mangled marker names no process we can check, so it can
        # never be shown to expire. Unreadable is not a licence to wedge.
        marker = None
    if not isinstance(marker, dict):
        if reap:
            _remove_marker(path)
        return None

    age = _marker_age_s(marker, now)
    dead = (not _process_alive(marker.get("pid"))
            or (age is not None and age > IN_FLIGHT_MAX_AGE_S))
    if dead:
        if reap:
            _remove_marker(path)
        return None
    return marker


def _remove_marker(path: Path) -> None:
    """Best-effort. A marker we could not delete is re-judged dead on the next
    read, so a failure here costs a repeated stat, never a wrong answer."""
    try:
        path.unlink()
    except OSError:
        pass


def claim_in_flight(date: str, trigger: str,
                    now: Optional[Callable[[], datetime]] = None
                    ) -> Optional[Dict]:
    """Take the machine's generation slot, or report who already has it.

    Returns None when the caller now OWNS the slot, and the live marker when it
    was refused. Callers pair it with `release_in_flight()` in a `finally`.

    O_EXCL IS THE WHOLE GUARD. Read-then-write would be a race between exactly
    the two processes this exists to separate — his Generate button and a
    launchd fire at the same wake — and a check-then-act guard against a
    simultaneous start is not a guard. The create is atomic in the kernel; the
    loser gets FileExistsError and reads back the winner's marker.

    NOT DATE-SCOPED, and that is deliberate. The slot is the MACHINE's, not the
    day's: a generate is one ~30-minute pipeline over one SQLite file and one
    data dir, and two of them at once race each other whatever dates they
    carry. The date rides in the marker so a refusal can say what is running."""
    path = in_flight_path()
    live = read_in_flight(now)
    if live is not None:
        return live
    now_fn = now or (lambda: datetime.now(timezone.utc))
    payload = json.dumps({
        "pid": os.getpid(),
        "date": date,
        "trigger": trigger,
        # ALWAYS AN AWARE UTC STAMP, whatever clock the caller carries. The
        # ladder's clock is `datetime.now` — NAIVE LOCAL, because its other job
        # is picking the local calendar day — and writing that string here would
        # make every marker look `UTC-offset` hours old the moment it was
        # created: on this machine (UTC-7) a brand-new claim read as 7 hours
        # stale and was reaped by the age ceiling instantly, which is a guard
        # that never guards. `naive.astimezone()` attaches the LOCAL offset,
        # which is the correct reading of what `datetime.now()` returned, and
        # the conversion to UTC then matches every other timestamp this app
        # stores (GEN_JOB.started_at, the log's `ts`) so `_utc_hm` renders it.
        "started_at": _as_utc(now_fn()).isoformat(),
    })
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
    except FileExistsError:
        # Somebody won the race between our read and our create. Their marker is
        # the answer; if it is somehow already dead, refusing this once is the
        # safe direction — the next attempt reaps it.
        return read_in_flight(now, reap=False) or {"pid": 0, "date": date,
                                                   "trigger": "", "started_at": ""}
    except OSError:
        # An unwritable data dir must not stop a generation the reader asked
        # for: the marker is a coordination aid, and refusing every run because
        # we cannot write it would turn a nicety into an outage.
        return None
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(payload)
    except OSError:
        _remove_marker(path)
        return None
    return None


def release_in_flight() -> None:
    """Give up the slot — ONLY if this process is the one holding it.

    The ownership check is what keeps a crashed-then-restarted server from
    deleting a live launchd run's marker on its way past."""
    path = in_flight_path()
    try:
        marker = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except (OSError, ValueError):
        return
    if isinstance(marker, dict) and marker.get("pid") == os.getpid():
        _remove_marker(path)


def newslens_bin() -> Path:
    """The absolute path to the `newslens` console script, taken from the
    interpreter that is running right now.

    ABSOLUTE AND VERIFIED, because launchd does not inherit his shell: no PATH
    to search, no virtualenv activated, no cwd. A plist naming a bare `newslens`
    fires and dies at 6am with nothing on screen to see it. If the script is not
    where this expects it, `render_plist` REFUSES rather than emitting a plist
    that points at a binary which is not there.

    NO `.resolve()`, AND THAT IS THE WHOLE POINT — measured on his machine, not
    reasoned about. `.venv/bin/python3` is a SYMLINK to
    /Library/Developer/CommandLineTools/usr/bin/python3, so `.resolve()` walks
    out of the virtualenv entirely and lands in the system framework's bin,
    where no `newslens` exists and never will. The first draft of this function
    resolved, and `newslens schedule plist` refused on his own tree with a
    message naming a CommandLineTools path he has never installed anything into.
    `sys.executable` is already absolute; the venv's identity is exactly the
    part `.resolve()` throws away.

    The PATH fallback is second, not first: it exists for a non-venv install
    where the interpreter and the script live apart, and it is only ever
    consulted when the primary location is empty."""
    candidate = Path(sys.executable).parent / "newslens"
    if candidate.exists():
        return candidate
    found = shutil.which("newslens")
    return Path(found) if found else candidate


# ---------------------------------------------------------------------------
# Same-day idempotence (charter item 2)
# ---------------------------------------------------------------------------

def published_edition_exists(con: sqlite3.Connection, date: str) -> bool:
    """Does `date` already have an edition the reader can OPEN?

    THE PREDICATE IS THE EDITION RENDERER'S OWN, reused and never re-derived —
    `server._stories_for`, the same function `_failure_outcome` was factored out
    to share (NL-103 row 9, gate FIX-1 2026-07-26). Row existence is NOT
    publication: `ranking.persist` commits today's row at the RANK stage and the
    body lands last at `generate.persist_generation`, so a row exists for the
    whole post-rank window with nothing readable behind it. A scheduler that
    read row-existence would look at yesterday's half-finished run, conclude
    "already published", and skip his morning entirely.

    The import is local because the dependency runs ONE way — a web module may
    import the scheduler, never the reverse — the same reason
    `generate._analysis_pause_class` is a function and not a module-level
    import."""
    from . import server

    row = server._briefing_row(con, date)
    if row is None:
        return False
    return bool(server._stories_for(row, server._log_entry_for(date))[0])


# ---------------------------------------------------------------------------
# The fire-decision record
# ---------------------------------------------------------------------------

def log_fire(outcome: str, date: str, detail: str = "",
             attempts: int = 0, charged_usd: float = 0.0) -> None:
    """Record what a scheduled fire DECIDED, in generation_log.jsonl.

    WHY A LINE AT ALL FOR A NO-OP. "Does nothing (quietly, logged)" is the
    charter's phrase, and the logged half is what makes the doctor able to tell
    "launchd never fired" from "launchd fired and there was nothing to do".
    Without it the doctor's last-fire probe can only say "I don't know", which
    on an unattended feature is the least useful true sentence available.

    WHY THIS FILE AND NOT A NEW ONE. It is already the per-run record, already
    append-only, already survives the process, and NL-149 already established
    additive jsonl enrichment as the way to grow it without a schema act. A
    second file would be a second record of one morning that can disagree with
    the first.

    THESE LINES ARE NOT RUNS and must never render as runs: they carry
    SCHEDULE_LINE_KEY, and `server._run_log_entries` filters on its presence the
    same way it already filters the analysis stage's line."""
    from . import generate

    generate.log_generation({
        SCHEDULE_LINE_KEY: outcome,
        "date": date,
        "detail": detail,
        "attempts": attempts,
        "charged_usd": round(charged_usd, 6),
    })


def _charged_for(date: str) -> float:
    """Charged dollars recorded by the run that just finished for `date`.

    Read back from the log rather than returned from the call, because
    `run_generate` RAISES on failure — the report never comes back — while its
    failed arm has already written the money record. `_log_entry_for` is the
    reader's own "last entry for a date wins", reused; it is torn-append hard
    (NL-149 QA F-1) and returns None rather than guessing."""
    from . import server

    entry = server._log_entry_for(date) or {}
    try:
        return float(entry.get("total_usd") or 0.0)
    except (TypeError, ValueError):
        return 0.0


# ---------------------------------------------------------------------------
# THE LADDER
# ---------------------------------------------------------------------------

def run_scheduled(
    date: Optional[str] = None,
    runner: Optional[Callable[..., object]] = None,
    sleeper: Optional[Callable[[float], None]] = None,
    now: Optional[Callable[[], datetime]] = None,
    env: Optional[dict] = None,
) -> Dict:
    """One scheduled fire. Returns {"outcome", "attempts", "charged_usd", "date"}.

    `runner` / `sleeper` / `now` are injected so the whole ladder is testable
    without launchd AND without a real generate — the suite never spends money,
    never waits an hour, and can put the fire on any day it likes. Defaults are
    the real ones.

    `now` IS READ, as of fix loop 1 (QA F-8): it advertised an injectable clock
    that nothing consulted — a seam promised in a signature and ignored in the
    body, which is worse than no seam, because a test that injects it passes
    while proving nothing. It is the clock for BOTH times this fire asks what
    time it is: the DATE the whole ladder is pinned to (once, here, at entry —
    a ladder crossing midnight finishes its own day) and the start-marker's
    timestamp. The date still goes THROUGH `ranking.local_today`, which took an
    optional clock in the same fix, rather than re-deriving `strftime` beside
    it: a second spelling of "today" is how a scheduler and a renderer end up
    disagreeing about which day it is.

    THE ORDER OF THE GATES IS THE DESIGN. Cheapest and most-authoritative first,
    so nothing is spent before every reason not to spend has been checked:

      1. kill switch   — his explicit "not today"; beats everything.
      2. idempotence   — an edition already exists; a second run would archive a
                         readable edition and spend a full pipeline to replace
                         it with one nobody asked for.
      3. the run       — and, on a retryable failure only, the ladder.

    Gates 1 and 2 are RE-CHECKED before every retry. A 45-minute backoff is long
    enough for him to flip the switch after seeing the first failure, and long
    enough for him to press Generate himself — retrying over the edition he just
    made by hand is the same double-spend the idempotence gate exists to stop.
    """
    from . import generate

    runner = runner or generate.run_generate
    sleeper = sleeper or time.sleep
    now = now or datetime.now
    date = date or ranking.local_today(now)

    if schedule_paused():
        log_fire(FIRED_PAUSED, date, detail=str(kill_switch_path()))
        return {"outcome": FIRED_PAUSED, "attempts": 0, "charged_usd": 0.0,
                "date": date}

    con = None
    try:
        from . import db
        db.migrate()
        con = db.connect()
        if published_edition_exists(con, date):
            log_fire(FIRED_ALREADY, date)
            return {"outcome": FIRED_ALREADY, "attempts": 0,
                    "charged_usd": 0.0, "date": date}
    finally:
        if con is not None:
            con.close()

    # THE UNATTENDED-SPEND GUARD (charter item 6, mandatory).
    #
    # The per-run cap is per RUN. Three attempts are three runs, so a naive
    # ladder can spend 3x what an attended run may — which is precisely the
    # routine-derating law's forbidden shape: an unattended run must not be able
    # to spend more than an attended one. So the ladder carries a SESSION budget
    # equal to ONE run's cap, accumulated across attempts from each failed run's
    # own money record, and no attempt starts once that session has charged
    # anything (the rule and its measurement live at the guard below).
    #
    # Reusing BUDGET_CAP_USD_PER_RUN rather than minting a second knob is
    # deliberate: one number, one meaning, no new env var, and the guarantee
    # states itself — a whole unattended morning costs at most one attended run.
    #
    # CHARGED dollars only, matching the lane-aware `_cap_verdict` (NL-148): the
    # subscription lane bills nothing per call and must never kill a run, so
    # shadow spend cannot stop the ladder either. NL-80's no-output-ceiling
    # class is guarded inside the run AND, since fix loop 1, is no longer
    # something this ladder's own bound depends on.
    try:
        cap = config.budget_cap_usd_per_run(env)
    except ValueError as exc:
        # A typo'd BUDGET_CAP_USD_PER_RUN is a DECIDED outcome for an unattended
        # run, not a crash: without a cap there is no session budget, and
        # starting an unwatched pipeline with no spending bound is the one thing
        # item 6 forbids. The doctor already FAILs this line; here it lands on
        # the record so `schedule status` can say why the mornings stopped.
        log_fire(FIRED_FAILED, date, detail=f"budget cap unreadable: {exc}")
        return {"outcome": FIRED_FAILED, "attempts": 0, "charged_usd": 0.0,
                "date": date}
    charged = 0.0
    last_error = ""
    # INITIALISED SO THE COUNT IS ALWAYS A MEASUREMENT (gate FIX-1). Every exit
    # below reports `attempt`, the number of attempts that actually ran; this
    # line is what makes that true even for a MAX_ATTEMPTS of 0, where the loop
    # body never runs and there is no loop variable to report.
    attempt = 0

    for attempt in range(1, MAX_ATTEMPTS + 1):
        # THE IN-FLIGHT GATE (fix loop 1, QA F-2) — claimed PER ATTEMPT, and the
        # per-attempt part is the design, not an implementation detail.
        #
        # What must never overlap is two PIPELINES, and a backoff is not a
        # pipeline: it is 15 or 45 minutes of this process asleep. Holding the
        # slot across the whole ladder would refuse him the Generate button for
        # up to an hour and forty minutes after a failure he just watched fail —
        # and the ladder's own design says the opposite in as many words (see the
        # gate note above: a backoff is long enough for him to press Generate
        # himself, and gate 2 stands the ladder down when he does). So the slot
        # is held exactly while money can be spent, and released the moment the
        # attempt ends.
        #
        # A refusal here is a DECIDED outcome on the record, not a crash and not
        # a silence: the doctor and `schedule status` read the last fire, and
        # "the 6am fire found the reader already generating" is the single most
        # useful sentence available about a morning with no scheduled run in it.
        held = claim_in_flight(date, TRIGGER_SCHEDULED, now)
        if held is not None:
            log_fire(FIRED_IN_FLIGHT, date,
                     detail=(f"a generation is already running in pid "
                             f"{held.get('pid')} for {held.get('date')} "
                             f"(started {held.get('started_at')}) — declining "
                             f"rather than running a second pipeline over it"),
                     attempts=attempt - 1, charged_usd=charged)
            return {"outcome": FIRED_IN_FLIGHT, "attempts": attempt - 1,
                    "charged_usd": charged, "date": date}
        try:
            # THE INNER try/finally IS LOAD-BEARING AND IS NOT AN OUTER ONE.
            # The slot must be released the instant the attempt ends — BEFORE
            # the except body below, which is where the 15/45-minute backoff
            # sleep happens. A single `finally` on the outer try would release
            # after the sleep and hold the machine's generation slot for the
            # whole backoff: exactly the hour-forty lockout the per-attempt
            # design exists to avoid.
            try:
                runner(date=date, env=env, trigger=TRIGGER_SCHEDULED)
            finally:
                release_in_flight()
        except Exception as exc:  # noqa: BLE001 — every failure is the ladder's
            charged += _charged_for(date)
            last_error = f"{type(exc).__name__}: {exc}"[:500]

            # THE FORK, and it is one predicate, not a copy of one:
            # generate.is_retryable is the same rule that stamps `retryable` on
            # the log entry (the seam NL-148 left at generate.py's failed arm).
            # A broken run does NOT retry — it may have spent real money in the
            # tail, and retrying it unattended spends it twice.
            if not generate.is_retryable(exc) or attempt == MAX_ATTEMPTS:
                break

            # THE UNATTENDED LADDER RETRIES ONLY FREE FAILURES (fix loop 1,
            # QA F-3). Once this session has charged ANY real money, no further
            # attempt starts.
            #
            # WHY THE RULE IS THIS BLUNT, and it is the whole finding. The first
            # draft projected the next attempt as the cost of the last one
            # (`charged + last_charged > cap`). That stops the ladder but bounds
            # no ATTEMPT: every retry still ran under the FULL per-run cap, so a
            # session at $2.00 of a $4.25 budget legally started an attempt
            # allowed to spend $4.25 on its own — measured at $6.25, 1.47x what
            # an attended run may spend.
            #
            # AND DERATING THE ATTEMPT'S CAP DOES NOT FIX IT — measured, not
            # reasoned about. Handing attempt 2 an env with
            # BUDGET_CAP_USD_PER_RUN = cap - charged makes the bound depend on
            # the run HONOURING the cap it was handed, which is exactly the
            # guarantee whose failure this guard exists for: NL-80's
            # no-output-ceiling class is a run overshooting its own cap. Probed
            # on an isolated copy with that mechanism planted: attempt 2 was
            # handed $2.25 and the session still landed at $6.25.
            #
            # So the bound is taken where it is unconditional — at the DECISION
            # TO START, using the only number this process can trust, the money
            # already on the record. The session guarantee is then exact rather
            # than delegated: every attempt that starts, starts with the WHOLE
            # cap still unspent, so the session's worst case is one run's cap.
            # That IS the derating contract ("no attempt starts with room it
            # doesn't have") stated as an invariant instead of an argument.
            #
            # THE COST IS REAL AND IT IS THE RIGHT TRADE: a charge of two cents
            # on attempt 1 now costs the morning. The one failure this ladder
            # retries is the systemic fetch pause, and NL-148 pins that pause at
            # $0 charged — nothing published, nothing completed — so the common
            # case still climbs the whole ladder untouched, and a pause that DID
            # charge is by that pin an anomaly. Compounding an anomaly nobody is
            # awake to watch is the thing item 6 forbids.
            #
            # CHARGED DOLLARS ONLY, unchanged (NL-148 lane law): `_charged_for`
            # reads `total_usd`, which counts `usd_charged`; subscription-lane
            # shadow spend is never in this number and so can never stop the
            # ladder.
            if charged > 0.0:
                log_fire(FIRED_BUDGET, date,
                         detail=(f"stopped after attempt {attempt}: "
                                 f"${charged:.4f} already charged this session "
                                 f"against a ${cap:.2f} budget (one run's cap) "
                                 f"— an unattended retry may only follow a "
                                 f"failure that cost nothing"),
                         attempts=attempt, charged_usd=charged)
                return {"outcome": FIRED_BUDGET, "attempts": attempt,
                        "charged_usd": charged, "date": date}
            sleeper(RETRY_BACKOFF_S[min(attempt - 1, len(RETRY_BACKOFF_S) - 1)])

            # RE-CHECKED AFTER THE BACKOFF, NOT BEFORE IT: the whole point is
            # that the world may have changed during those 45 minutes.
            if schedule_paused():
                log_fire(FIRED_PAUSED, date, detail=str(kill_switch_path()),
                         attempts=attempt, charged_usd=charged)
                return {"outcome": FIRED_PAUSED, "attempts": attempt,
                        "charged_usd": charged, "date": date}
            con = None
            try:
                from . import db
                con = db.connect()
                if published_edition_exists(con, date):
                    log_fire(FIRED_ALREADY, date,
                             detail="published during the backoff",
                             attempts=attempt, charged_usd=charged)
                    return {"outcome": FIRED_ALREADY, "attempts": attempt,
                            "charged_usd": charged, "date": date}
            finally:
                if con is not None:
                    con.close()
            continue
        charged += _charged_for(date)
        log_fire(FIRED_PUBLISHED, date, attempts=attempt,
                 charged_usd=charged)
        return {"outcome": FIRED_PUBLISHED, "attempts": attempt,
                "charged_usd": charged, "date": date}

    # THE ATTEMPTS THAT RAN, NOT THE CEILING (gate FIX-1). This arm is reached by
    # `break`, and the break has TWO causes: an exhausted ladder (attempt ==
    # MAX_ATTEMPTS) and — far more likely — a NON-RETRYABLE failure, which the
    # fork refuses to retry after attempt 1. Reporting MAX_ATTEMPTS covered the
    # first case and fabricated the second: `cli.py` prints this number into
    # `schedule-launchd.err.log`, so a morning that tried once told him it had
    # tried three times, on the one surface that exists precisely because nobody
    # was awake to see what happened.
    log_fire(FIRED_FAILED, date, detail=last_error,
             attempts=attempt, charged_usd=charged)
    return {"outcome": FIRED_FAILED, "attempts": attempt,
            "charged_usd": charged, "date": date}


# ---------------------------------------------------------------------------
# The plist — RENDERED here, INSTALLED by his hands
# ---------------------------------------------------------------------------

_PLIST_TEMPLATE = """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" \
"http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>{label}</string>

  <key>ProgramArguments</key>
  <array>
    <string>{binary}</string>
    <string>schedule</string>
    <string>run</string>
  </array>

  <!-- Fires at {hour:02d}:00 local, every day. If the Mac is ASLEEP at that
       time launchd starts the job on the next wake, and several missed days
       coalesce into ONE fire (launchd.plist(5)). -->
  <key>StartCalendarInterval</key>
  <dict>
    <key>Hour</key>
    <integer>{hour}</integer>
    <key>Minute</key>
    <integer>0</integer>
  </dict>

  <!-- FALSE, and it matters: RunAtLoad would start a ~30-minute generate the
       instant you bootstrap this agent, and again at every login. The launchd
       man page calls speculative launches a thing to avoid; here it would also
       spend money you did not ask to spend. -->
  <key>RunAtLoad</key>
  <false/>

  <key>WorkingDirectory</key>
  <string>{project_root}</string>

  <!-- stderr only. stdout of a successful run is the whole narrative and would
       grow this file by an edition a day; stderr is the phase boundaries and
       the errors — which is what you want when a 6am run failed and nobody was
       awake to watch it. The machine-readable record is data/generation_log.jsonl. -->
  <key>StandardErrorPath</key>
  <string>{err_log}</string>
</dict>
</plist>
"""


def render_plist(hour: Optional[int] = None, env: Optional[dict] = None,
                 binary: Optional[Path] = None) -> str:
    """The launchd agent, as text. Writing it is HIS act, never ours.

    REFUSES rather than emitting a plist that cannot work: a plist pointing at a
    console script that is not there is a 6am job that dies before Python starts
    and leaves nothing behind to explain itself."""
    binary = Path(binary) if binary is not None else newslens_bin()
    if not binary.exists():
        raise ScheduleError(
            f"the `newslens` console script is not at {binary} — install the "
            f"package into this interpreter first (`pip install -e .` from "
            f"{paths.PROJECT_ROOT}), then re-run this command. Refusing to "
            f"render a plist that points at a binary which is not there."
        )
    # THE RESOLVER, not the env layer (NL-152). `newslens schedule plist` is the
    # command his settings screen tells him to re-run, so the hour it bakes must
    # be the hour the settings screen showed him — reading GENERATE_HOUR_LOCAL
    # here would have re-rendered the SAME plist he was told to change.
    hour = config.generate_hour_resolved(env)[0] if hour is None else int(hour)
    if not 0 <= hour <= 23:
        raise ScheduleError(f"hour must be 0-23, got {hour}")
    return _PLIST_TEMPLATE.format(
        label=LAUNCHD_LABEL,
        binary=binary,
        hour=hour,
        project_root=paths.PROJECT_ROOT,
        err_log=paths.DATA_DIR / "schedule-launchd.err.log",
    )


# THE PLIST REALITY (NL-152), stated once here because three surfaces render it.
#
# SCOUTED, NOT ASSUMED: `run_scheduled` never consults the hour — grep the whole
# ladder and there is no hour in it. That is not an oversight to fix at fire
# time, it is structural. launchd decides WHEN the process exists; by the time
# `newslens schedule run` is executing, the fire has already happened. A
# fire-time hour check could therefore only ever DECLINE — plist says 06:00,
# settings says 07:00, the 06:00 fire refuses itself and nothing fires at 07:00
# — which trades a visible disagreement for a schedule that silently stops.
#
# So the honest answer is the first of the dispatch's two: the installed agent
# needs a re-render and a re-install, by HIS hands, and every surface that can
# see the disagreement says so. The org never runs these.
def reinstall_commands(home: Optional[Path] = None) -> List[str]:
    """The exact commands his hands run to move an INSTALLED agent to a new
    hour. Two acts: rewrite the file, reload it.

    `bootout` before `bootstrap` is not optional and not defensive — launchd
    refuses to bootstrap a label that is already loaded, so a sequence without
    it fails on the one machine that has the feature working."""
    target = plist_path(home)
    return [
        f"newslens schedule plist > {target}",
        f"launchctl bootout gui/$UID/{LAUNCHD_LABEL} && "
        f"launchctl bootstrap gui/$UID {target}",
    ]


def reinstall_commands_text(home: Optional[Path] = None) -> str:
    """`reinstall_commands` as the indented block the CLI prints."""
    return "".join(f"       {c}\n" for c in reinstall_commands(home))


def install_instructions(hour: Optional[int] = None,
                         env: Optional[dict] = None,
                         home: Optional[Path] = None) -> str:
    """The steps HIS HANDS run. Printed, never executed.

    Deliberately not a script and deliberately not `newslens schedule install`:
    the org never installs the launchd agent (dispatch 2026-08-13, law). What is
    on offer is an exact, copyable sequence — the failure mode this replaces is
    a half-remembered `launchctl load` from a blog post."""
    hour = config.generate_hour_resolved(env)[0] if hour is None else int(hour)
    target = plist_path(home)
    return f"""\
Scheduled generation — install (your hands, three commands)

Today's edition will be generated at {hour:02d}:00 local, every day, so it is
ready before you open the app. NewsLens does not install this for you.

  1. Write the agent file:

       mkdir -p {target.parent}
       newslens schedule plist > {target}

  2. Load it (this does NOT start a run — see RunAtLoad in the file):

       launchctl bootstrap gui/$UID {target}

  3. Confirm:

       newslens schedule status

To change the hour, set it in Settings (or GENERATE_HOUR_LOCAL in your .env),
then re-install the agent — the agent file bakes the hour in, so changing the
setting alone does not move the fire:

{reinstall_commands_text(home)}
To PAUSE without uninstalling:      touch {kill_switch_path()}
To resume:                          rm {kill_switch_path()}
(the Settings toggle flips that same file)

To remove the schedule entirely:

       launchctl bootout gui/$UID/{LAUNCHD_LABEL}
       rm {target}
"""


# ---------------------------------------------------------------------------
# Status — what the doctor and `schedule status` both say (charter item 5)
# ---------------------------------------------------------------------------

def last_fire() -> Optional[Dict]:
    """The newest schedule fire-decision line, or None if nothing ever fired.

    None means EXACTLY "no scheduled fire has been recorded" — never "the
    schedule is broken" and never "it fired and failed". The doctor renders that
    distinction rather than collapsing it.

    NL-154 / QA F-6 — AND "NOTHING EVER FIRED" MUST NOT MEAN "IT SCROLLED OUT
    OF THE LIVE SEGMENT". Rotation MOVES lines, it does not delete them, so a
    fire line older than the retention floor is still on disk in
    `generation_log.archive-0001.jsonl` — and a reader that stopped at the live
    file had the product printing "no scheduled fire has been recorded yet"
    about a record it was still holding. Reachable whenever 60+ runs land with
    no scheduled fire among them: a battery stretch, or a paused schedule with
    manual generates.

    THE READ STAYS BOUNDED IN THE COMMON CASE, and the shape is the one
    `server._log_entry_for` already uses for the same reason: the live segment
    is searched first and wins outright; the archives are opened only when the
    live segment holds no fire AT ALL. Segments are walked NEWEST FIRST and the
    first segment with a hit wins, which preserves this function's own
    last-wins rule across the split — a later segment's fire always supersedes
    an earlier one's.

    The segment list comes from `generate`, not from a second spelling of the
    log's filename here (this function used to hardcode "generation_log.jsonl";
    two spellings of one path is how a rotation and its reader end up looking
    at different files)."""
    from . import generate

    def _scan(path: Path) -> Optional[Dict]:
        found = None
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except (OSError, ValueError):
            return None
        for line in text.splitlines():
            if not line.strip():
                continue
            try:
                e = json.loads(line)
            except ValueError:
                continue
            if isinstance(e, dict) and e.get(SCHEDULE_LINE_KEY) is not None:
                found = e
        return found

    hit = _scan(generate.log_file())
    if hit is not None:
        return hit
    for seg in reversed(generate.log_archives()):
        hit = _scan(seg)
        if hit is not None:
            return hit
    return None


def status(home: Optional[Path] = None, env: Optional[dict] = None) -> Dict:
    """Everything true about the schedule, and nothing that isn't.

    THE HONESTY BOUND, stated because it is the point of the charter's item 5:
    this function can see a FILE and it can see a LOG. It cannot see whether
    launchd has the agent loaded — that is `launchctl`'s to answer and this
    module does not shell out — so `installed` means "the plist file is at the
    path launchd reads", and the caller must render it as that and not as
    "the schedule is running". A plist on disk that was never bootstrapped
    looks identical from here, which is exactly why `last_fire` is reported
    beside it: a file with no fires behind it is the shape that gap takes.
    """
    p = plist_path(home)
    try:
        present = p.exists()
    except OSError:
        present = False

    plist_hour: Optional[int] = None
    if present:
        try:
            import plistlib
            with p.open("rb") as fh:
                data = plistlib.load(fh)
            interval = data.get("StartCalendarInterval")
            if isinstance(interval, dict):
                plist_hour = interval.get("Hour")
            elif isinstance(interval, list) and interval:
                plist_hour = (interval[0] or {}).get("Hour")
        except Exception:  # noqa: BLE001 — an unreadable plist is a REPORTED
            plist_hour = None       # unknown, never a guess and never a crash

    # THE ENV LAYER'S ERROR IS STILL REPORTED (`hour_error`) even when a
    # settings value short-circuits it: a typo'd .env is a config error he
    # should see, and hiding it because the UI happens to override it would be
    # the doctor going quiet about a fault it can see.
    try:
        config.generate_hour_local(env)
        hour_error = ""
    except ValueError as exc:
        hour_error = str(exc)
    configured_hour, hour_source = config.generate_hour_resolved(env)

    return {
        "installed": present,
        "plist_path": str(p),
        "plist_hour": plist_hour,
        "configured_hour": configured_hour,
        # NL-152: which layer supplied `configured_hour` — "settings", "env" or
        # "default". Reported rather than inferred, so the mismatch sentence can
        # name the thing he would actually go and change.
        "hour_source": hour_source,
        "hour_error": hour_error,
        "paused": schedule_paused(),
        "kill_switch_path": str(kill_switch_path()),
        "last_fire": last_fire(),
        # READ-ONLY (reap=False): `status` is a readout, and a readout that
        # deletes the state it describes is a readout that changes the answer
        # for whoever asks next. Reaping belongs to the writers.
        "in_flight": read_in_flight(reap=False),
    }


# WHAT EACH STATUS SENTENCE IS ABOUT — keys, deliberately NOT doctor severity
# levels. This module must not import the doctor's vocabulary (the dependency
# runs one way, exactly as it does with `server`), and a sentence's SUBJECT is
# stable while its severity is a rendering decision the doctor owns. The tag is
# what lets the doctor put its WARN on the sentence the warning is about
# (fix loop 1, QA F-7: the WARN was pinned to out[0] regardless of which line
# earned it, so a PAUSED schedule rendered "no agent file…" as the warning and
# the PAUSED sentence itself as INFO).
LINE_PLAIN = ""
LINE_INSTALLED = "installed"
LINE_NOT_INSTALLED = "not-installed"
LINE_MISMATCH = "hour-mismatch"
LINE_HOUR_ERROR = "hour-error"
LINE_PAUSED = "paused"


def status_lines_tagged(home: Optional[Path] = None, env: Optional[dict] = None,
                        st: Optional[Dict] = None) -> List[Tuple[str, str]]:
    """`status()` as (tag, sentence) pairs — the one construction both the CLI
    verb and the doctor render, so the two surfaces cannot drift into describing
    the same machine differently.

    `st` IS AN ARGUMENT so a caller that also needs the raw dict reads the
    world ONCE (fix loop 1, QA F-7): the doctor used to call `status_lines()`
    and then `status()` again, which is two reads of a kill-switch file and a
    660KB log with a window between them — a switch flipped in that window
    produced sentences and a severity taken from two different worlds."""
    st = st if st is not None else status(home, env)
    out: List[Tuple[str, str]] = []
    if st["installed"]:
        hour = st["plist_hour"]
        when = f"{hour:02d}:00 local" if isinstance(hour, int) else "an unreadable hour"
        out.append((LINE_INSTALLED,
                    f"agent file present at {st['plist_path']} — fires at {when}"))
        out.append((LINE_PLAIN,
                    "  (file present is not proof launchd loaded it — "
                    "`launchctl print gui/$UID/" + LAUNCHD_LABEL + "` is)"))
        cfg_hour = st["configured_hour"]
        if isinstance(hour, int) and isinstance(cfg_hour, int) and hour != cfg_hour:
            # NL-152 extended this sentence from "GENERATE_HOUR_LOCAL says" to
            # "whichever layer actually decided", because the settings tab is
            # now a way to change that number and a warning that named the .env
            # would have sent him to edit a file that was no longer winning.
            where = {config.HOUR_SOURCE_SETTINGS: "your Settings say",
                     config.HOUR_SOURCE_ENV: "GENERATE_HOUR_LOCAL says"}.get(
                         st.get("hour_source") or "", "the default is")
            out.append((
                LINE_MISMATCH,
                f"  MISMATCH: the agent fires at {hour:02d}:00 but "
                f"{where} {cfg_hour:02d}:00 — the agent wins "
                f"until you re-render it (`newslens schedule install-instructions`)"))
    else:
        out.append((LINE_NOT_INSTALLED,
                    f"no agent file at {st['plist_path']} — the schedule is not "
                    f"installed (`newslens schedule install-instructions`)"))
    if st["hour_error"]:
        out.append((LINE_HOUR_ERROR,
                    f"  GENERATE_HOUR_LOCAL is invalid: {st['hour_error']}"))
    if st["paused"]:
        out.append((LINE_PAUSED,
                    f"PAUSED by {st['kill_switch_path']} — scheduled runs decline "
                    f"before spending anything (rm it to resume)"))
    # The in-flight marker, reported because it is the one piece of schedule
    # state that can REFUSE a run he asked for. A reader whose Generate button
    # answers "already running" has exactly one question, and this is the line
    # that answers it. Read-only (`reap=False`): a status readout must not
    # delete state it is describing.
    held = st.get("in_flight")
    if isinstance(held, dict):
        out.append((LINE_PLAIN,
                    f"a generation is in flight: pid {held.get('pid')} for "
                    f"{held.get('date')}, started {held.get('started_at')} "
                    f"({held.get('trigger') or 'unrecorded'}) — a scheduled "
                    f"fire or a Generate press will decline while it runs"))
    lf = st["last_fire"]
    if lf is None:
        out.append((LINE_PLAIN, "no scheduled fire has been recorded yet"))
    else:
        detail = f" — {lf.get('detail')}" if lf.get("detail") else ""
        out.append((LINE_PLAIN,
                    f"last scheduled fire: {lf.get('ts', 'unknown time')} "
                    f"for {lf.get('date', 'an unknown date')} — "
                    f"{lf.get(SCHEDULE_LINE_KEY)}{detail}"))
    return out


def status_lines(home: Optional[Path] = None, env: Optional[dict] = None
                 ) -> List[str]:
    """The sentences `newslens schedule status` prints — the tagged lines with
    their tags dropped. The CLI shows a reader plain prose; the doctor is the
    surface that needs to know which sentence is which."""
    return [text for _tag, text in status_lines_tagged(home, env)]
