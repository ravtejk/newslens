"""NL-146 FIX LOOP 1 — acceptance pins for the findings QA left no pin for.

QA's own five acceptance contracts live in tests/test_nl146_schedule_qa.py and
are not restated here. This file pins what the fix loop ADDED: the F-2
cross-process in-flight mechanism (new, so its pins are born red by ABSENCE and
are labelled as such — the mutation receipts in the fix-loop report are the
proof that they bite), and the behavioural changes for F-4, F-7, F-8 and the
`generation_log` reader sweep, every one of which is born red BEHAVIOURALLY
against the pre-fix artifact (QA's leg-1 copy; shas in the report).

THE `IN_FLIGHT` HELPER BELOW IS A DELIBERATE getattr, and the reason is a proof
one. Several F-2 pins model "another process is generating" by writing the
marker file BY HAND, which is exactly what a launchd run in another process
looks like from here. Naming `schedule.IN_FLIGHT_NAME` directly would make
those tests die of AttributeError on the pre-fix tree — a red that proves the
constant is missing and nothing about the BEHAVIOUR. With the fallback they run
on both trees and their pre-fix red is the real finding: the door let a second
pipeline through, and Today said "Nothing for today yet" while a run was going.
`test_the_marker_is_the_name_the_module_publishes` pins the fallback to the
module so the two can never drift.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import os
import subprocess
import sys

import pytest

from newslens import (analysis, db, diagnose, doctor, generate, paths, ranking,
                      schedule, server)

from test_server import ui, get, post, seed_briefing            # noqa: F401

TODAY = datetime.now().strftime("%Y-%m-%d")
YDAY = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")

IN_FLIGHT = getattr(schedule, "IN_FLIGHT_NAME", "RUN_IN_FLIGHT")


class Sleeper:
    def __init__(self):
        self.slept = []

    def __call__(self, seconds):
        self.slept.append(seconds)


def _write_marker(pid, started_at=None, date=TODAY, trigger="scheduled"):
    """What another process's live claim looks like on disk."""
    path = paths.DATA_DIR / IN_FLIGHT
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "pid": pid, "date": date, "trigger": trigger,
        "started_at": started_at or datetime.now(timezone.utc).isoformat(),
    }), encoding="utf-8")
    return path


def _a_live_foreign_pid() -> int:
    """A pid that is alive and is not this process. The parent is both by
    construction — pytest is not its own parent — and needs no process spawned
    and left running to hold the property true for the length of a test."""
    pid = os.getppid()
    assert pid and pid != os.getpid(), "premise: this process has a live parent"
    return pid


def _a_dead_pid() -> int:
    """A pid nothing is using: spawn the cheapest possible child, reap it, and
    take the number it left behind. Measured rather than guessed — a hardcoded
    'probably free' pid is a test that fails on somebody else's machine."""
    proc = subprocess.Popen([sys.executable, "-c", ""])
    proc.wait()
    return proc.pid


def _commission():
    """Past the first-run belt. The autouse sandbox writes a zero-source
    template with no interests sections at all, so the shipped profile template
    goes in first — the same two steps the C1 suite's `fresh` fixture takes."""
    paths.SOURCES_FILE.write_text(
        paths.PROFILE_SOURCES_TEMPLATE.read_text(encoding="utf-8"),
        encoding="utf-8")
    ok, why = server.topic_add("Inflation", "specific")
    assert ok, f"premise: the profile can be commissioned ({why})"


def _today_view(raw: bytes) -> str:
    return raw.decode("utf-8").split('id="view-today"')[1] \
                              .split('id="view-following"')[0]


# ---------------------------------------------------------------------------
# F-2 — the cross-process in-flight signal
# ---------------------------------------------------------------------------

def test_the_marker_is_the_name_the_module_publishes(tmp_paths):
    """Keeps this file's `getattr` fallback honest: if the constant is renamed,
    the pins above must follow it rather than silently testing a dead path."""
    assert schedule.IN_FLIGHT_NAME == IN_FLIGHT
    assert schedule.in_flight_path() == paths.DATA_DIR / schedule.IN_FLIGHT_NAME, (
        "the marker must be profile-scoped under DATA_DIR, exactly like the "
        "kill switch — a machine-global marker would let one profile's run "
        "block another's")


def test_the_door_refuses_a_second_pipeline_while_another_process_generates(
        ui, monkeypatch):
    """THE FINDING, at the door. `GEN_JOB.start()` guards THIS process; a
    launchd fire is another one. For the whole ~30 minutes of a scheduled run
    the POST door waved a second full pipeline through for the same day —
    double spend, two writers on one SQLite file — and the likeliest moment for
    it is the wake-coalesced fire, when he opens the lid, sees no edition and
    presses the button."""
    _commission()
    started = []
    monkeypatch.setattr(server.GEN_JOB, "start",
                        lambda: (started.append(1), True)[1])
    _write_marker(_a_live_foreign_pid())

    code, out = post(ui, "/api/generate", {})

    assert code == 409 and out["ok"] is False, (
        "the door started a second pipeline over a run already in flight in "
        "another process")
    assert started == [], "GEN_JOB.start() ran anyway"
    assert "already running" in out["error"], (
        f"the refusal does not say why: {out['error']!r} — a 409 that does not "
        "name the running run reads as a broken button, and the next thing a "
        "reader does with a broken button is press it again")


def test_the_door_opens_again_once_the_other_process_is_gone(ui, monkeypatch):
    """The other half, and the one that matters more: the guard must not become
    a way to lose the Generate button. A marker whose process is gone is a run
    that died, and a dead run holds nothing."""
    _commission()
    started = []
    monkeypatch.setattr(server.GEN_JOB, "start",
                        lambda: (started.append(1), True)[1])
    _write_marker(_a_dead_pid())

    code, out = post(ui, "/api/generate", {})

    assert code == 200 and out["ok"] is True and started == [1], (
        "a marker left behind by a crashed 6am run wedged the button")
    assert not (paths.DATA_DIR / IN_FLIGHT).exists(), (
        "the dead marker was ignored but not reaped — a file that every reader "
        "disagrees with is a trap for whoever cats it next")


def test_today_says_a_run_is_in_flight_instead_of_nothing_yet(ui):
    """THE FINDING, on the page. Through the whole scheduled run Today rendered
    'No edition has been generated for today' beside a live Generate button,
    while an edition was being generated for today. The reader's rational
    response to that screen is the double spend the door now refuses."""
    con = db.connect()
    seed_briefing(con, YDAY)
    con.close()
    _write_marker(_a_live_foreign_pid())

    _, _, raw = get(ui, "/")
    today = _today_view(raw)

    assert "being generated" in today, (
        "Today still claims nothing is happening while a run is happening")
    assert "No edition has been generated for today" not in today, (
        "the false absence is still on the page beside the true one")
    assert "Generate today" not in today, (
        "the Generate button is still offered — the door would refuse it, and "
        "an affordance whose only outcome is a refusal is the affordance-"
        "absence law's own case")


def test_a_dead_marker_leaves_the_honest_empty_state_alone(ui):
    """The staleness bound, on the page. A crashed run must not leave Today
    claiming forever that something is being generated."""
    con = db.connect()
    seed_briefing(con, YDAY)
    con.close()
    _write_marker(_a_dead_pid())

    _, _, raw = get(ui, "/")
    today = _today_view(raw)

    assert "No edition has been generated for today" in today
    assert "being generated" not in today


def test_a_marker_past_the_age_ceiling_is_not_believed(tmp_paths):
    """The second staleness bound — the one liveness cannot give. A recycled
    pid makes a long-dead run look alive; the ceiling is what stops that from
    wedging his mornings forever rather than for one of them."""
    db.migrate()
    old = (datetime.now(timezone.utc)
           - timedelta(seconds=schedule.IN_FLIGHT_MAX_AGE_S + 60)).isoformat()
    _write_marker(os.getpid(), started_at=old)      # alive by construction

    assert schedule.read_in_flight() is None, (
        "a marker older than the ceiling was believed on the strength of a pid "
        "that proves nothing about a run that ended hours ago")


def test_a_marker_inside_the_age_ceiling_is_believed(tmp_paths):
    """The ceiling's other side, so it can never be tightened into uselessness
    by accident: a run that is merely SLOW is still a run."""
    db.migrate()
    recent = (datetime.now(timezone.utc) - timedelta(seconds=45 * 60)).isoformat()
    _write_marker(_a_live_foreign_pid(), started_at=recent)

    held = schedule.read_in_flight()
    assert held is not None and held["pid"] == _a_live_foreign_pid(), (
        "a 45-minute-old run — inside the measured range for a full edition — "
        "was declared dead")


def test_a_timestampless_marker_is_judged_by_liveness_alone(tmp_paths):
    """The two errors here are not symmetric, so the direction is a decision
    rather than a detail. Our writer always stamps an aware UTC time; a marker
    without one was hand-edited. Disbelieving it while its pid is a live
    pipeline starts a second ~30-minute run and charges for it, and nothing
    undoes that. Believing it costs a refused button with a printed reason, and
    it is still bounded — the pid has to be alive for it to hold at all."""
    db.migrate()
    path = paths.DATA_DIR / IN_FLIGHT
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"pid": _a_live_foreign_pid(), "date": TODAY,
                                "trigger": "scheduled", "started_at": "not-a-time"}),
                    encoding="utf-8")

    held = schedule.read_in_flight()

    assert held is not None, (
        "a live pipeline's slot was released because somebody mangled the "
        "timestamp — the reader gets a second run charged over the first")
    assert path.exists(), "and the marker was reaped on the way out"


def test_the_claim_is_exclusive_and_the_loser_is_told_who_holds_it(tmp_paths):
    """The claim is the enforcement; the door's check is only the explanation.
    Two callers, one slot, and the loser gets a marker it can render."""
    db.migrate()
    assert schedule.claim_in_flight(TODAY, schedule.TRIGGER_INTERACTIVE) is None

    refused = schedule.claim_in_flight(TODAY, schedule.TRIGGER_SCHEDULED)

    assert refused is not None and refused["pid"] == os.getpid()
    assert refused["trigger"] == schedule.TRIGGER_INTERACTIVE, (
        "the loser was handed its OWN claim back instead of the winner's")


def test_a_release_never_takes_a_slot_this_process_does_not_hold(tmp_paths):
    """A restarted server walking past a live launchd run must not delete its
    marker on the way."""
    db.migrate()
    _write_marker(_a_live_foreign_pid())

    schedule.release_in_flight()

    assert (paths.DATA_DIR / IN_FLIGHT).exists(), (
        "this process released a slot another process is holding")


def test_the_scheduled_ladder_declines_when_a_generation_is_already_running(
        tmp_paths):
    """The mirror direction, and it is the same double spend: he presses
    Generate at 05:58 and the 06:00 fire lands on top of it. The idempotence
    gate cannot see it — the row is bodyless until the body lands — so without
    this the fire starts a second pipeline over his."""
    db.migrate()
    _write_marker(_a_live_foreign_pid(), trigger=schedule.TRIGGER_INTERACTIVE)
    calls = []

    def runner(**kw):
        calls.append(kw)

    result = schedule.run_scheduled(date=TODAY, runner=runner,
                                    sleeper=Sleeper())

    assert result["outcome"] == schedule.FIRED_IN_FLIGHT
    assert calls == [], "the fire ran a second pipeline over a live run"
    assert result["charged_usd"] == 0.0
    lines = [json.loads(ln) for ln in
             (paths.DATA_DIR / "generation_log.jsonl")
             .read_text(encoding="utf-8").splitlines() if ln.strip()]
    fires = [e for e in lines if e.get(schedule.SCHEDULE_LINE_KEY) is not None]
    assert fires and fires[-1][schedule.SCHEDULE_LINE_KEY] == \
        schedule.FIRED_IN_FLIGHT, (
        "a morning with no scheduled run in it left no record of why")


def test_a_dead_marker_never_wedges_the_scheduled_fire(tmp_paths):
    """FOUND BY MUTATION, and it is the pin the first draft was missing. The
    door reaps a dead marker on its way past, so every door-side pin stayed
    green when `claim_in_flight`'s own reaping read was removed — the ladder has
    no such friend. It calls `claim_in_flight` directly, so if that read stops
    reaping, a marker left by a crashed run refuses his 6am fire every morning
    thereafter, silently and forever. This is the pin that fails when it does."""
    db.migrate()
    _write_marker(_a_dead_pid())
    calls = []

    result = schedule.run_scheduled(date=TODAY,
                                    runner=lambda **kw: calls.append(kw),
                                    sleeper=Sleeper())

    assert result["outcome"] == schedule.FIRED_PUBLISHED and calls, (
        "a marker from a run that died wedged the scheduled fire — the "
        "unattended feature stops silently, which is the one way it must "
        f"never fail (outcome was {result['outcome']})")


def test_an_attempt_holds_the_slot_while_it_is_actually_spending(tmp_paths):
    """The claim is real, taken by the ladder itself, and names the fire."""
    db.migrate()
    seen = {}

    def runner(**kw):
        seen.update(schedule.read_in_flight() or {})

    schedule.run_scheduled(date=TODAY, runner=runner, sleeper=Sleeper())

    assert seen.get("pid") == os.getpid(), (
        "the ladder ran an attempt without holding the machine's slot")
    assert seen.get("trigger") == schedule.TRIGGER_SCHEDULED


def test_the_slot_is_free_during_the_backoff_not_held_across_the_ladder(
        tmp_paths):
    """THE PER-ATTEMPT DESIGN, pinned. A backoff is not a pipeline: it is this
    process asleep for 15 or 45 minutes. Holding the slot across the whole
    ladder would refuse him his own Generate button for up to an hour and forty
    minutes after a failure he just watched — and the ladder's own design says
    the opposite, since gate 2 stands it down when he publishes during the
    backoff."""
    db.migrate()
    free_during_backoff = []

    def sleeper(_seconds):
        free_during_backoff.append(schedule.read_in_flight())

    def runner(**kw):
        raise analysis.SystemicFetchFailure("nothing fetched")

    schedule.run_scheduled(date=TODAY, runner=runner, sleeper=sleeper)

    assert free_during_backoff, "premise: the ladder backed off at least once"
    assert all(held is None for held in free_during_backoff), (
        "the slot was held through the backoff — he cannot press Generate for "
        "the whole hour and forty the ladder is asleep")


def test_an_interactive_job_publishes_the_slot_the_scheduler_reads(tmp_paths,
                                                                   monkeypatch):
    """The two writers meet on ONE file. The web job's claim must be the same
    claim `run_scheduled` refuses on — a second marker class would be two
    processes politely avoiding two different doors."""
    db.migrate()
    job = server._GenJob()
    monkeypatch.setattr(job, "_run", lambda: None)   # disarmed: no pipeline

    assert job.start() is True
    held = schedule.read_in_flight()

    assert held is not None and held["pid"] == os.getpid()
    assert held["trigger"] == schedule.TRIGGER_INTERACTIVE


def test_the_status_readout_never_deletes_the_state_it_describes(tmp_paths):
    """`schedule status` and the doctor read this marker; a readout that reaps
    would change the answer for whoever asks next — including the door."""
    db.migrate()
    _write_marker(_a_live_foreign_pid())

    st = schedule.status(home=tmp_paths / "home")

    assert isinstance(st["in_flight"], dict)
    assert (paths.DATA_DIR / IN_FLIGHT).exists()
    text = "\n".join(schedule.status_lines(home=tmp_paths / "home"))
    assert "in flight" in text, (
        "the one piece of schedule state that can REFUSE a run he asked for is "
        "invisible to the surface he would ask")


# ---------------------------------------------------------------------------
# F-4 — the archive's half of "a row is not an edition"
# ---------------------------------------------------------------------------

def test_an_archive_of_only_bodyless_rows_is_an_honestly_empty_archive(ui):
    """The skip and the empty state have to compose. A world whose every row is
    body-less has no editions, and the archive must say so rather than render a
    month grid with nothing pickable in it."""
    con = db.connect()
    con.execute(
        "INSERT INTO briefings (date, story_slots, corroboration_labels,"
        " token_cost, generated_at) VALUES (?, ?, ?, ?, ?)",
        (TODAY, json.dumps([]), json.dumps([]), json.dumps({}),
         "2026-08-13T04:00:00.000Z"))
    con.commit()
    con.close()

    assert server._archive_body(db.connect()) == "", (
        "a briefings table of body-less rows still rendered an archive")


# ---------------------------------------------------------------------------
# F-7 — the doctor's severity lands on the sentence it is about
# ---------------------------------------------------------------------------

def test_the_doctor_warns_on_the_paused_sentence_not_on_line_zero(tmp_paths,
                                                                  monkeypatch):
    """Measured by QA: with the schedule paused AND not installed, the WARN sat
    on 'no agent file…' — a line this check's own table calls INFO — while
    'PAUSED by …', the sentence the warning is about, rendered INFO."""
    db.migrate()
    monkeypatch.setattr(schedule, "plist_path",
                        lambda home=None: tmp_paths / "nope.plist")
    schedule.kill_switch_path().parent.mkdir(parents=True, exist_ok=True)
    schedule.kill_switch_path().touch()

    results = doctor.check_schedule({})
    warned = [r for r in results if r.status == doctor.WARN]

    assert len(warned) == 1, [(r.status, r.text) for r in results]
    assert "PAUSED" in warned[0].text, (
        f"the WARN is wearing the wrong sentence: {warned[0].text!r}")
    not_installed = [r for r in results if "no agent file" in r.text]
    assert not_installed and not_installed[0].status == doctor.INFO, (
        "an opt-in feature being off is not a warning")


def test_the_doctor_reads_the_schedule_world_once(tmp_paths, monkeypatch):
    """The TOCTOU half of F-7: the old check called `status_lines()` and then
    `status()` again, so the sentences and the severity came from two reads of
    a kill-switch file and a 660KB log with a window between them. A switch
    flipped in that window produced a readout describing two different
    worlds."""
    db.migrate()
    reads = []
    real_status = schedule.status

    def counting(home=None, env=None):
        reads.append(1)
        return real_status(home, env)

    monkeypatch.setattr(schedule, "status", counting)
    doctor.check_schedule({})

    assert len(reads) == 1, f"the doctor read the schedule {len(reads)} times"


# ---------------------------------------------------------------------------
# F-8 — the clock the signature promised
# ---------------------------------------------------------------------------

def test_the_injected_clock_decides_which_day_the_fire_is_for(tmp_paths):
    """`run_scheduled` advertised an injectable clock and nothing read it — a
    seam promised in a signature and ignored in the body, which is worse than
    no seam: a test that injects it passes while proving nothing."""
    db.migrate()
    frozen = datetime(2027, 3, 14, 6, 0, 0)

    result = schedule.run_scheduled(runner=lambda **kw: None,
                                    sleeper=Sleeper(), now=lambda: frozen)

    assert result["date"] == "2027-03-14", (
        f"the ladder fired for {result['date']} — the injected clock was "
        "ignored, exactly as it was before this parameter was wired")
    assert ranking.local_today(lambda: frozen) == "2027-03-14", (
        "the date went through a second spelling of 'today' instead of the "
        "org's one definition")


# ---------------------------------------------------------------------------
# The generation_log reader sweep (F-1's class, one reader over)
# ---------------------------------------------------------------------------

def test_a_fire_decision_line_is_not_a_malformed_generation_record(tmp_paths):
    """F-1's assumption lives in the day-14 diagnostic too: it calls every
    non-analysis line a run and buckets anything without `date`+`status` as
    malformed. A fire line carries `date` and no `status`, so every quiet,
    correct no-op counted itself to the founder as a defect in his own
    record — the same inversion NL-146 built these lines to avoid."""
    db.migrate()
    generate.log_generation({"date": TODAY, "status": "ok", "total_usd": 0.5})
    schedule.log_fire(schedule.FIRED_ALREADY, TODAY)
    schedule.log_fire(schedule.FIRED_PUBLISHED, TODAY, attempts=1)

    out = diagnose.run_diagnose(now_utc=datetime(2026, 8, 13, 12, 0,
                                                 tzinfo=timezone.utc))
    record = [ln for ln in out.splitlines() if ln.strip().startswith("entries:")]

    assert record, "premise: the generation-record section rendered"
    assert "malformed/other 0" in record[0], (
        f"two correct fire lines were reported as defects: {record[0]!r}")
    assert "scheduled fires: 2" in out, (
        "the fire lines were dropped silently instead of named — a record "
        "readout that quietly discards a class of line is the other way to lie "
        "about a file")


# ---------------------------------------------------------------------------
# GATE FIXLET (2026-08-13) — FIX-1, FIX-2, FIX-4
# ---------------------------------------------------------------------------

def test_the_failed_fire_records_the_attempts_that_ran(tmp_paths):
    """FIX-1 — the number on the record must be a MEASUREMENT, not the ceiling.

    The exhausted-ladder arm logged `attempts=MAX_ATTEMPTS` unconditionally, so
    the most likely scheduled failure there is — a broken run, which the fork
    refuses to retry after ONE attempt — wrote `attempts: 3` into the fire line
    and printed "(attempts 3, …)" from `cli.py` into `schedule-launchd.err.log`.
    That log is the primary morning-after surface, and it was telling him the
    machine had tried three times when it had tried once: a fabricated number,
    on the record, on the one surface that exists because nobody was watching.

    BOTH DIRECTIONS ARE PINNED, deliberately — a constant swapped for a smaller
    constant is not a fix. One broken run records 1; three exhausted retryable
    attempts still record 3."""
    db.migrate()
    sleeper = Sleeper()
    calls = []

    def broken(**kw):
        calls.append(kw)
        raise generate.GenerateError("the script stage died")

    result = schedule.run_scheduled(date=TODAY, runner=broken, sleeper=sleeper)

    assert result["outcome"] == schedule.FIRED_FAILED
    assert len(calls) == 1 and sleeper.slept == [], (
        "premise: a broken run is not retried, so exactly one attempt ran")
    assert result["attempts"] == 1, (
        f"one attempt ran and the ladder reported {result['attempts']} — the "
        "failed arm is reporting the ceiling instead of the count")
    fires = [json.loads(ln) for ln in
             (paths.DATA_DIR / "generation_log.jsonl")
             .read_text(encoding="utf-8").splitlines() if ln.strip()]
    fires = [e for e in fires if e.get(schedule.SCHEDULE_LINE_KEY) is not None]
    assert fires and fires[-1]["attempts"] == 1, (
        f"the fire line on the record says attempts={fires[-1]['attempts']} "
        "for a morning that tried once — this is the line the CLI prints into "
        "schedule-launchd.err.log")

    # The other direction: a full ladder still records the full count.
    (paths.DATA_DIR / "generation_log.jsonl").unlink()
    exhausted = schedule.run_scheduled(
        date=TODAY, sleeper=Sleeper(),
        runner=lambda **kw: (_ for _ in ()).throw(
            analysis.SystemicFetchFailure("nothing fetched")))

    assert exhausted["attempts"] == schedule.MAX_ATTEMPTS, (
        f"three attempts ran and the record says {exhausted['attempts']}")


def test_the_quiet_note_never_claims_nothing_was_charged_against_its_own_record(
        tmp_paths):
    """FIX-2 — the note and the JSON line it is rendering must not disagree.

    "Nothing was charged" was unconditional on the retryable arm. It is TRUE for
    the pause NL-148 pins at $0, and it is FALSE exactly on the path this
    milestone's own budget guard creates: a retryable failure that DID charge is
    what stands the ladder down, so the entry carries `retryable: true` AND a
    non-zero `total_usd`, and the page states the opposite of the record it is
    reading. A surface that contradicts its own source is worse than a silent
    one — he has no reason to go look at the number.

    UNREADABLE IS NOT ZERO. A `total_usd` we cannot parse means we do not know
    what was spent, and the honest rendering of not knowing is to make no claim
    — not to fall back on the reassuring half."""
    charged = server._scheduled_failure_note(
        {"status": "failed", "trigger": schedule.TRIGGER_SCHEDULED,
         "retryable": True, "total_usd": 2.0})
    free = server._scheduled_failure_note(
        {"status": "failed", "trigger": schedule.TRIGGER_SCHEDULED,
         "retryable": True, "total_usd": 0})
    unreadable = server._scheduled_failure_note(
        {"status": "failed", "trigger": schedule.TRIGGER_SCHEDULED,
         "retryable": True, "total_usd": "n/a"})

    assert "scheduled edition didn" in charged, (
        "premise: the note still renders for a charged retryable failure")
    assert "nothing was charged" not in charged.lower(), (
        "the note told him nothing was charged while the same log line it is "
        f"rendering records $2.00: {charged!r}")
    assert "nothing was charged" in free.lower(), (
        "a run whose own record says $0 may state that plainly — the fix must "
        "not cost the true case its sentence")
    assert "nothing was charged" not in unreadable.lower(), (
        f"an unparseable money field was read as zero: {unreadable!r}")


def test_the_status_readout_never_deletes_a_dead_marker_either(tmp_paths):
    """FIX-4 — the `reap=False` half the live-marker pin cannot reach.

    `test_the_status_readout_never_deletes_the_state_it_describes` plants a LIVE
    marker, and a live marker is not reaped by anybody — so that pin stays green
    with `reap=True` and the readout's own withholding goes unproven (fix loop 1
    §2b called it unprovable by mutation; it isn't). A DEAD marker is the case
    where the two differ: the judgement says None either way, and only the
    non-reaping reader leaves the file for whoever asks next."""
    db.migrate()
    marker = _write_marker(_a_dead_pid())

    st = schedule.status(home=tmp_paths / "home")

    assert st["in_flight"] is None, (
        "premise: the readout judged a dead-pid marker dead")
    assert marker.exists(), (
        "a readout deleted the state it was asked to describe — a GET that "
        "writes the data dir is the class the no-real-state-writes rule names")
