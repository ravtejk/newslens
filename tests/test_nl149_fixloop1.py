"""NL-149 FIX LOOP 1 — the pins the QA acceptance file does not carry.

tests/test_nl149_qa.py holds QA's nine acceptance contracts (F-1..F-4, F-7) and
those are the criteria this loop had to turn green without weakening. This file
pins the three places where the landed fix goes FURTHER than the contract, so
the extra behaviour is asserted rather than merely described:

  §1  F-2 IS STRUCTURAL, NOT MERELY SMALLER. QA's one-line contract (a
      millisecond stamp) removes the truncation half of the over-report and
      leaves the ROUNDING half: with round(), each stage can round up 0.05s
      while the run total rounds down 0.05s, and `sum(steps) > total` still
      measured 30.3% of ten-stage/two-minute runs (99.9% at ~0.06s stages) in a
      2,000-run-per-cell replay. `_wall_seconds_since` therefore FLOORS onto the
      same 0.1s grid, which makes the invariant arithmetic —
      floor(a) + floor(b) <= floor(a + b) — instead of probable. BORN RED
      against the pre-fix tree AND against the stamp-only candidate; both reds
      transcribed in the fix-loop report.

  §2  BOTH TIMING SURFACES FLOOR BY ONE RULE. F-2's second half is the live
      panel and the saved report disagreeing about the same step. Sub-second
      stamps make the two measurements agree to microseconds; a shared rounding
      rule is what stops that agreement being thrown away at the last step.

  §3  THE OLDER LOG READER, BY NAME. F-1's fix contract names two call sites and
      QA's front-door test proves the door. This proves the site — the reader
      that predates NL-149 and that fires on the founder's profile whenever an
      edition row exists.

  §4  THE STALE TAB. The retry loop is deliberately still infinite (a reload
      against a server that is still down replaces a self-healing tab with a
      browser error page); what changed is that the tab stops asserting a
      duration it cannot confirm. STRUCTURAL, not behavioural — this suite runs
      no JS engine, so the pin reads comment-stripped source and is labelled as
      such (the milestone's own §3.4).

Offline by construction: autouse conftest sandbox, no network, no key, $0.
Nothing here writes real state.
"""

from __future__ import annotations

import json
import re
import time
from datetime import datetime, timedelta, timezone

from newslens import generate, paths, server, webui


def _log():
    paths.DATA_DIR.mkdir(parents=True, exist_ok=True)
    return paths.DATA_DIR / "generation_log.jsonl"


# ===========================================================================
# §1 — F-2: the parts can never add up to more than the whole
# ===========================================================================

def test_the_recorded_steps_can_never_add_up_to_more_than_the_run():
    """BORN RED, twice over — and the second red is the point.

    Stage windows are contiguous, non-overlapping and nested inside the run
    window, so `sum(steps) <= total` is arithmetic and not preference. This
    drives the REAL recorder (`timeline_recorder` + `close_stage_timeline`, the
    two functions the pipeline installs) over six stages of ~0.06s, which is
    the worst cell for round-half rounding: a 0.06s span rounds UP to 0.1 while
    contributing 0.06 to a total that rounds DOWN.

      pre-fix tree (whole-second stamps + round)     sum 4.4s vs total 0.8s
      stamp-only candidate (ms stamps + round)       sum 0.6s vs total 0.4s
      landed fix (ms stamps + floor to 0.1)          sum 0.0s vs total 0.3s

    Deterministic by construction rather than by luck: `time.sleep(0.06)` lands
    every stage in [0.060, 0.075], which is inside round()'s round-up band
    [0.05, 0.15) for every stage on every run, so the middle row above is a
    fixed failure and not a sampled one.
    """
    rep = generate.GenReport(date="2026-08-13", variant="full")
    rep.run_started_at = generate._utc_stamp()
    record = generate.timeline_recorder(rep, None)
    for label in ("Gathering the news", "Ranking stories",
                  "Reading the stories closely", "Writing the briefing",
                  "Editing", "Recording the episode"):
        record(label, None)
        time.sleep(0.06)
    generate.close_stage_timeline(rep)

    steps = [s["elapsed_s"] for s in rep.stage_timeline]
    assert len(steps) == 6 and all(isinstance(s, float) for s in steps)
    assert rep.run_elapsed_s + 1e-9 >= sum(steps), (
        f"the record says the run took {rep.run_elapsed_s}s while its own six "
        f"steps add up to {sum(steps)}s — {steps}")


def test_a_recorded_stage_never_claims_more_time_than_the_clock_gave_it():
    """BORN RED — the direction of the error, pinned on its own.

    The whole finding is an over-report, so the invariant worth holding is
    one-sided: a stage may be understated (that is what a floor costs, and this
    helper already declared that trade for the backwards-clock case) and may
    never be overstated. The tolerance is 0.01s — two orders below the 0.1s grid
    the value is reported on, and above the 1ms the stamp truncates — so it
    absorbs stamp resolution without absorbing a rounding step.
    """
    before = datetime.now(timezone.utc)
    stamp = generate._utc_stamp()
    time.sleep(0.27)                       # inside round()'s round-up band
    got = generate._wall_seconds_since(stamp)
    ceiling = (datetime.now(timezone.utc) - before).total_seconds()
    assert got <= ceiling + 0.01, (
        f"a stage measured {got}s over a span that was at most {ceiling:.4f}s")
    assert got >= 0.2, f"and it must still be a real measurement, not 0: {got}"


def test_the_live_and_the_saved_stage_floor_by_the_same_rule():
    """BORN RED on the live half — F-2's cross-surface half.

    `server._GenJob` and `generate` measure the same stage independently, from
    their own stamps taken microseconds apart at the same boundary. Once both
    stamps carry sub-second precision the two measurements agree; they can still
    be pulled apart by the last step, the rounding, which is why both floor onto
    the same 0.1s grid. 1.97s is chosen to sit in round()'s round-up band: the
    old live path returned 2.0 for it, the saved path 1.9, and a reader watching
    a step live and then reading it back in the report saw two numbers.
    """
    end = datetime.now(timezone.utc)
    start = end - timedelta(seconds=1.97)

    job = server._GenJob()
    job.stage = "Editing"
    job.stage_started_at = start.isoformat()
    job._close_stage_locked(end.isoformat())
    live = job.steps[0]["elapsed_s"]

    saved = generate._wall_seconds_since(
        (datetime.now(timezone.utc) - timedelta(seconds=1.97))
        .strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z")

    assert live == 1.9, f"the live panel floors 1.97s to {live}"
    assert saved == 1.9, f"the saved report floors 1.97s to {saved}"


# ===========================================================================
# §2 — F-1: the OLDER reader, by name
# ===========================================================================

def test_the_older_log_reader_survives_a_torn_append():
    """BORN RED — the pre-existing half of F-1, pinned at its site.

    `_log_entry_for` has read generation_log.jsonl behind `except OSError` since
    long before NL-149, and it is reached from `build_page` whenever an edition
    row exists — always, on the founder's profile. UnicodeDecodeError subclasses
    ValueError, so a partial multibyte tail (two appenders, one of them writing
    `ensure_ascii=False`, and no atomicity) came straight back out of it and
    `do_GET` answered HTTP 500. QA's front-door test proves the DOOR; this
    proves the SITE, so the fix cannot be half-landed and read as closed.

    The degradation shape is asserted too: the intact line before the torn tail
    must still be found. A reader that answered None on one bad byte would trade
    a crash for a lie about the day's run.
    """
    good = json.dumps({"ts": "2026-08-13T06:00:00Z", "date": "2026-08-13",
                       "status": "ok", "total_usd": 0.0, "steps": []}) + "\n"
    stage = json.dumps({"ts": "2026-08-13T07:00:00Z", "stage": "analysis",
                        "sonar": "ok — 8 results"},
                       ensure_ascii=False).encode("utf-8")
    torn = stage[:stage.index("—".encode("utf-8")) + 1]      # inside the em dash
    _log().write_bytes(good.encode("utf-8") + torn)

    entry = server._log_entry_for("2026-08-13")
    assert entry is not None, (
        "one torn byte at the end of the file erased the day's run entry")
    assert entry["status"] == "ok"


# ===========================================================================
# §3 — F-3: the stale tab stops asserting what it cannot confirm
# ===========================================================================

def test_the_live_clock_stops_when_contact_with_the_server_does():
    """STRUCTURAL RECEIPT, labelled as such — this suite has no JS engine (the
    milestone's own §3.4). Comments are stripped first, so the prose explaining
    the fix cannot satisfy the assertion.

    `genClockTick` interpolates locally between the 2.5s polls. The `.catch`
    branch retries every 4s and deliberately never reloads, so a tab whose
    server has gone away used to keep counting — minutes of invented elapsed for
    a run it could not see and that may already have ended. Holding the interval
    freezes the last CONFIRMED reading; `genClockSync` restarts the tick from
    the server's own elapsed on the next successful poll, which is why the fix
    is a hold and not a stop.
    """
    js = re.sub(r"/\*.*?\*/", " ", webui.JS, flags=re.S)
    js = re.sub(r"//[^\n]*", " ", js)

    hold = js[js.index("function genClockHold"):]
    hold = hold[:hold.index("\n}") + 2]
    assert "clearInterval(genClock.timer)" in hold and "genClock.timer = null" in hold, hold

    poll = js[js.index("function pollGeneration"):]
    poll = poll[:poll.index("\n}") + 2]
    catch = poll[poll.index(".catch("):]
    assert "genClockHold()" in catch, (
        "the poll's failure branch does not stop the clock, so an abandoned tab "
        f"keeps counting a duration nobody confirmed: {catch}")
    assert "setTimeout(pollGeneration" in catch, (
        "the retry itself must stay — reloading against a server that is still "
        "down replaces a self-healing tab with a browser error page")
    # and the tick is restarted only by a SUCCESSFUL poll's re-sync
    sync = js[js.index("function genClockSync"):]
    sync = sync[:sync.index("\n}") + 2]
    assert "setInterval(genClockTick" in sync, sync
