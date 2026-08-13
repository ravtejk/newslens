"""NL-146 — scheduled generation: the behavioural contracts.

Every test here is born red against 1f92260 (the module and the branches did not
exist). The ones that pin a REUSED invariant rather than new code say so and
carry a mutation receipt instead — the pin-proves-its-route law.

launchd never runs in this file. `run_scheduled` takes its generator, its clock
and its sleeper as arguments precisely so the ladder is testable without it: the
suite injects a runner that records calls and raises to order, and a sleeper that
records durations and returns instantly. Nothing here spends money and nothing
here waits.
"""

from __future__ import annotations

import json
import plistlib
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from newslens import analysis, db, generate, paths, schedule, server

from test_server import ui, get, seed_briefing            # noqa: F401


TODAY = datetime.now().strftime("%Y-%m-%d")


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

class Recorder:
    """A stand-in for `generate.run_generate` that records every call and
    raises whatever the test queued for that attempt."""

    def __init__(self, *raises):
        self.calls = []
        self.raises = list(raises)

    def __call__(self, **kw):
        self.calls.append(kw)
        exc = self.raises.pop(0) if self.raises else None
        if exc is not None:
            raise exc
        return object()


class Sleeper:
    def __init__(self):
        self.slept = []

    def __call__(self, seconds):
        self.slept.append(seconds)


def _schedule_lines():
    log = paths.DATA_DIR / "generation_log.jsonl"
    if not log.exists():
        return []
    out = []
    for line in log.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        e = json.loads(line)
        if e.get(schedule.SCHEDULE_LINE_KEY) is not None:
            out.append(e)
    return out


def _bodyless_row(con, date=TODAY):
    """The post-rank window: `ranking.persist` has committed the row, the body
    has not landed. Hand-built rather than run through the pipeline so the test
    costs nothing and cannot flake on a stage."""
    con.execute(
        "INSERT INTO briefings (date, story_slots, corroboration_labels,"
        " token_cost, generated_at) VALUES (?, ?, ?, ?, ?)",
        (date, json.dumps([]), json.dumps([]), json.dumps({}),
         "2026-08-13T04:00:00.000Z"))
    con.commit()


# ---------------------------------------------------------------------------
# ITEM 2 — same-day idempotence
# ---------------------------------------------------------------------------

def test_a_published_edition_makes_the_fire_a_quiet_no_op(tmp_paths):
    db.migrate()
    con = db.connect()
    seed_briefing(con, TODAY)
    con.commit()
    con.close()

    r = Recorder()
    result = schedule.run_scheduled(date=TODAY, runner=r)

    assert result["outcome"] == schedule.FIRED_ALREADY
    assert r.calls == [], "a second run would archive a readable edition and " \
                          "spend a full pipeline replacing it"
    assert result["charged_usd"] == 0.0
    lines = _schedule_lines()
    assert [l[schedule.SCHEDULE_LINE_KEY] for l in lines] == [schedule.FIRED_ALREADY]


def test_idempotence_reads_readability_not_row_existence(tmp_paths):
    """THE MUTATION THIS PIN EXISTS FOR: swapping `published_edition_exists` to
    `row is not None` makes it green-looking and silently kills his mornings —
    yesterday's half-finished run leaves a row for today's date, the scheduler
    concludes "already published", and no edition is ever generated again."""
    db.migrate()
    con = db.connect()
    _bodyless_row(con)
    con.close()

    con = db.connect()
    assert schedule.published_edition_exists(con, TODAY) is False
    con.close()

    r = Recorder()
    result = schedule.run_scheduled(date=TODAY, runner=r)
    assert result["outcome"] == schedule.FIRED_PUBLISHED
    assert len(r.calls) == 1


def test_the_no_op_is_quiet_on_the_reports_screen(tmp_paths):
    """A fire-decision line is not a run. Rendered as one it would appear as a
    failed generation that never happened — a no-op turned into an alarm."""
    db.migrate()
    con = db.connect()
    seed_briefing(con, TODAY)
    con.commit()
    con.close()
    schedule.run_scheduled(date=TODAY, runner=Recorder())

    runs, total = server._run_log_entries()
    assert runs == [] and total == 0, \
        f"the fire-decision line leaked into the run report: {runs}"


# ---------------------------------------------------------------------------
# ITEM 6 — the kill switch and the unattended-spend guard
# ---------------------------------------------------------------------------

def test_the_kill_switch_declines_before_spending_anything(tmp_paths):
    db.migrate()
    schedule.kill_switch_path().parent.mkdir(parents=True, exist_ok=True)
    schedule.kill_switch_path().touch()

    r = Recorder()
    result = schedule.run_scheduled(date=TODAY, runner=r)

    assert result["outcome"] == schedule.FIRED_PAUSED
    assert r.calls == []
    assert result["charged_usd"] == 0.0


def test_the_kill_switch_is_re_read_during_the_backoff(tmp_paths):
    """A 45-minute backoff is long enough for him to see the first failure and
    flip the switch. A ladder that read the switch once would go on spending
    after he said stop."""
    db.migrate()
    sleeper = Sleeper()

    def flip(_seconds):
        schedule.kill_switch_path().parent.mkdir(parents=True, exist_ok=True)
        schedule.kill_switch_path().touch()
        sleeper(_seconds)

    r = Recorder(analysis.SystemicFetchFailure("nothing fetched"),
                 analysis.SystemicFetchFailure("nothing fetched"))
    result = schedule.run_scheduled(date=TODAY, runner=r, sleeper=flip)

    assert result["outcome"] == schedule.FIRED_PAUSED
    assert len(r.calls) == 1, "the second attempt ran after the switch was set"


def test_an_unattended_morning_cannot_outspend_one_attended_run(tmp_paths,
                                                               monkeypatch):
    """THE ROUTINE-DERATING LAW, structurally. The cap is per RUN; three
    attempts are three runs, so a naive ladder may spend 3x what an attended run
    may. The session budget is ONE run's cap, accumulated across attempts."""
    db.migrate()
    cap = 4.25
    monkeypatch.setattr(schedule.config, "budget_cap_usd_per_run",
                        lambda env=None: cap)
    # Each failed attempt is recorded as having charged most of the cap.
    monkeypatch.setattr(schedule, "_charged_for", lambda date: 3.00)

    sleeper = Sleeper()
    r = Recorder(analysis.SystemicFetchFailure("x"),
                 analysis.SystemicFetchFailure("x"),
                 analysis.SystemicFetchFailure("x"))
    result = schedule.run_scheduled(date=TODAY, runner=r, sleeper=sleeper)

    assert result["outcome"] == schedule.FIRED_BUDGET
    assert len(r.calls) == 1, (
        "attempt 2 ran with $3.00 of a $4.25 session budget already spent — "
        "the unattended morning is on course to outspend an attended run")
    assert sleeper.slept == [], "it should stop, not back off and stop"
    assert _schedule_lines()[-1][schedule.SCHEDULE_LINE_KEY] == schedule.FIRED_BUDGET


# ---------------------------------------------------------------------------
# ITEM 4 — the ladder and its fork
# ---------------------------------------------------------------------------

def test_a_systemic_fetch_failure_retries_with_backoff(tmp_paths):
    db.migrate()
    sleeper = Sleeper()
    r = Recorder(analysis.SystemicFetchFailure("nothing fetched"),
                 analysis.SystemicFetchFailure("nothing fetched"),
                 analysis.SystemicFetchFailure("nothing fetched"))

    result = schedule.run_scheduled(date=TODAY, runner=r, sleeper=sleeper)

    assert len(r.calls) == schedule.MAX_ATTEMPTS
    assert sleeper.slept == list(schedule.RETRY_BACKOFF_S)
    assert result["outcome"] == schedule.FIRED_FAILED


def test_a_broken_run_is_never_retried_unattended(tmp_paths):
    """THE MONEY LAW OF THE LADDER. A GenerateError may have spent real dollars
    in the tail — the editor, both script attempts — and retrying it unattended
    spends them twice. Only the pause, which NL-148 proved completes no story
    and charges nothing at the instant it fires, is retryable."""
    db.migrate()
    sleeper = Sleeper()
    r = Recorder(generate.GenerateError("the script stage died"),
                 generate.GenerateError("the script stage died"))

    result = schedule.run_scheduled(date=TODAY, runner=r, sleeper=sleeper)

    assert len(r.calls) == 1, "a broken run was retried"
    assert sleeper.slept == []
    assert result["outcome"] == schedule.FIRED_FAILED


def test_the_fork_is_one_predicate_shared_with_the_log_arm(tmp_paths):
    """`generate.is_retryable` is the rule; the log arm stamps `retryable` from
    it and the ladder turns on it. Two copies of a money-shaped rule drift, and
    the direction they drift is the expensive one."""
    assert generate.is_retryable(analysis.SystemicFetchFailure("x")) is True
    assert generate.is_retryable(generate.GenerateError("x")) is False
    assert generate.is_retryable(RuntimeError("x")) is False


def test_an_unreadable_budget_cap_stops_the_run_on_the_record(tmp_paths):
    """Without a cap there is no session budget, and starting an unwatched
    pipeline with no spending bound is what item 6 forbids. A typo'd .env is a
    DECIDED outcome for a 6am run, not a traceback — and it lands on the record
    so `schedule status` can say why the mornings stopped."""
    db.migrate()
    r = Recorder()
    result = schedule.run_scheduled(date=TODAY, runner=r,
                                    env={"BUDGET_CAP_USD_PER_RUN": "banana"})
    assert result["outcome"] == schedule.FIRED_FAILED
    assert r.calls == []
    assert "budget cap unreadable" in _schedule_lines()[-1]["detail"]


def test_a_success_publishes_and_records_one_fire(tmp_paths):
    db.migrate()
    r = Recorder()
    result = schedule.run_scheduled(date=TODAY, runner=r)
    assert result["outcome"] == schedule.FIRED_PUBLISHED
    assert r.calls[0]["trigger"] == schedule.TRIGGER_SCHEDULED
    assert r.calls[0]["date"] == TODAY


def test_a_publish_during_the_backoff_stops_the_ladder(tmp_paths):
    """He can press Generate himself while the ladder is between attempts.
    Retrying over the edition he just made by hand is the same double-spend the
    idempotence gate exists to stop."""
    db.migrate()

    def publish_meanwhile(_seconds):
        con = db.connect()
        seed_briefing(con, TODAY)
        con.commit()
        con.close()

    r = Recorder(analysis.SystemicFetchFailure("x"),
                 analysis.SystemicFetchFailure("x"))
    result = schedule.run_scheduled(date=TODAY, runner=r,
                                    sleeper=publish_meanwhile)
    assert result["outcome"] == schedule.FIRED_ALREADY
    assert len(r.calls) == 1


# ---------------------------------------------------------------------------
# ITEM 4 — trigger provenance on the record
# ---------------------------------------------------------------------------

def test_trigger_rides_both_log_arms_and_is_absent_when_unnamed(tmp_paths):
    """CARRIED-INVARIANT SHAPE for the ok arm (exercised through the failed arm
    here because a real ok arm is a 30-minute pipeline); the ABSENCE half is the
    contract that keeps every historical run honest (fix loop 1, QA F-6: this
    line used to quote a run count that was never measured)."""
    db.migrate()

    rep = generate.GenReport(date=TODAY, variant="A")
    rep.trigger = schedule.TRIGGER_SCHEDULED
    generate.log_generation({"date": TODAY, "status": "failed",
                             **({"trigger": rep.trigger} if rep.trigger else {})})
    rep2 = generate.GenReport(date=TODAY, variant="A")
    generate.log_generation({"date": TODAY, "status": "failed",
                             **({"trigger": rep2.trigger} if rep2.trigger else {})})

    runs, _ = server._run_log_entries()
    assert runs[1].get("trigger") == schedule.TRIGGER_SCHEDULED
    assert "trigger" not in runs[0], \
        "an unnamed caller was given a provenance it never claimed"


def test_run_generate_accepts_and_carries_the_trigger(tmp_paths, monkeypatch):
    """Wiring proof: the argument reaches report.trigger, which is what both log
    arms read. Mutating the assignment away makes this red."""
    db.migrate()
    seen = {}

    def boom(con, date, src_env, key, report, refresh, no_threads=False,
             progress=None):
        seen["trigger"] = report.trigger
        raise generate.GenerateError("stop here")

    monkeypatch.setattr(generate, "_run_generate_body", boom)
    with pytest.raises(generate.GenerateError):
        generate.run_generate(date=TODAY, env={},
                              trigger=schedule.TRIGGER_SCHEDULED)
    assert seen["trigger"] == schedule.TRIGGER_SCHEDULED

    entry = server._log_entry_for(TODAY)
    assert entry["trigger"] == schedule.TRIGGER_SCHEDULED


def test_the_reports_screen_names_the_trigger_only_when_recorded(tmp_paths):
    html_s = server._render_run({"date": TODAY, "status": "ok",
                                 "trigger": schedule.TRIGGER_SCHEDULED})
    html_i = server._render_run({"date": TODAY, "status": "ok",
                                 "trigger": schedule.TRIGGER_INTERACTIVE})
    html_none = server._render_run({"date": TODAY, "status": "ok"})

    from newslens import labels
    assert labels.RUNLOG_TRIGGER_SCHEDULED in html_s
    assert labels.RUNLOG_TRIGGER_INTERACTIVE in html_i
    assert labels.RUNLOG_TRIGGER_SCHEDULED not in html_none
    assert labels.RUNLOG_TRIGGER_INTERACTIVE not in html_none


# ---------------------------------------------------------------------------
# ITEM 3 — atomic publish, and the half-edition hole the scheduled path opened
# ---------------------------------------------------------------------------

def test_a_bodyless_today_row_never_renders_as_an_edition(ui):
    """THE HOLE, MEASURED AT 1f92260: masthead ceremony over an empty grid —
    1547 bytes, one heading (the dateline), no state panel — because GEN_JOB is
    THIS process's job and a scheduled run dies in another one."""
    con = db.connect()
    seed_briefing(con, (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d"))
    _bodyless_row(con)
    con.close()

    assert server.GEN_JOB.snapshot()["state"] not in ("running", "error"), \
        "premise: the job is idle — another process ran this"

    _, _, raw = get(ui, "/")
    page = raw.decode("utf-8")
    today = page.split('id="view-today"')[1].split('id="view-following"')[0]

    assert 'class="state-panel"' in today, (
        "the reader gets a dateline announcing today's edition with no edition "
        "under it:\n" + today[:800])
    assert "dispatch-strip" not in today, \
        "the masthead receipted an edition that was never assembled"


def test_a_scheduled_run_killed_mid_pipeline_publishes_nothing(ui, monkeypatch):
    """ATOMIC PUBLISH ON THE SCHEDULED PATH — the charter's "kill mid-run ->
    yesterday's edition intact at open".

    The invariant itself is CARRIED (persist_generation is one transaction and
    lands last — NL-106); what is new and therefore pinned is that it still
    holds when the run is unattended, i.e. through `run_scheduled` and its
    ladder, with the reader arriving afterwards to an idle GEN_JOB.

    The kill is planted where a real one lands: inside `_run_generate_body`,
    AFTER the rank stage has committed today's row and BEFORE the body write.
    That runs the real `run_generate` failed arm, the real log write, the real
    ladder and the real reader — only the pipeline's 30 minutes are replaced."""
    yday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
    con = db.connect()
    seed_briefing(con, yday)
    con.close()

    def die_after_rank(con, date, src_env, key, report, refresh,
                       no_threads=False, progress=None):
        _bodyless_row(con, date)          # what ranking.persist commits
        raise generate.GenerateError("killed mid-pipeline")

    monkeypatch.setattr(generate, "_run_generate_body", die_after_rank)
    # The sleeper is injected even though a broken run must never reach it: an
    # injected sleeper turns "the fork regressed" into a RED, where the real one
    # would turn it into a suite that hangs for an hour. (Learned the hard way —
    # mutation leg 2 of this build did exactly that.)
    sleeper = Sleeper()
    result = schedule.run_scheduled(date=TODAY, env={}, sleeper=sleeper)
    assert result["outcome"] == schedule.FIRED_FAILED
    assert sleeper.slept == [], "a broken run backed off to retry itself"

    con = db.connect()
    # Nothing published for today...
    assert schedule.published_edition_exists(con, TODAY) is False
    # ...and yesterday is byte-for-byte the edition it was.
    assert server._stories_for(server._briefing_row(con, yday), None)[0], \
        "a dead run for today damaged yesterday's edition"
    con.close()

    # At open: today is an honest absence, yesterday still opens.
    _, _, raw = get(ui, "/")
    today_view = raw.decode("utf-8").split('id="view-today"')[1] \
                    .split('id="view-following"')[0]
    assert 'class="state-panel"' in today_view
    assert "dispatch-strip" not in today_view

    _, _, raw = get(ui, f"/?date={yday}")
    assert b"Chip export controls pass" in raw


def test_the_quiet_note_speaks_only_for_an_unwatched_failure(tmp_paths):
    sched = server._scheduled_failure_note(
        {"status": "failed", "trigger": schedule.TRIGGER_SCHEDULED})
    paused = server._scheduled_failure_note(
        {"status": "failed", "trigger": schedule.TRIGGER_SCHEDULED,
         "retryable": True})
    inter = server._scheduled_failure_note(
        {"status": "failed", "trigger": schedule.TRIGGER_INTERACTIVE})
    unnamed = server._scheduled_failure_note({"status": "failed"})
    okrun = server._scheduled_failure_note(
        {"status": "ok", "trigger": schedule.TRIGGER_SCHEDULED})

    assert "scheduled edition didn" in sched
    assert "nothing was charged" in paused.lower()
    assert inter == "", "he watched it fail and already has the error panel"
    assert unnamed == "", "a pre-NL-146 run makes no claim about who ran it"
    assert okrun == ""


def test_the_note_reaches_the_page(ui):
    con = db.connect()
    # Yesterday published, so the Commissioning founding page (a profile that
    # has never published anything) is satisfied and the app shell renders —
    # which is the state a scheduled failure actually happens in.
    seed_briefing(con, (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d"))
    _bodyless_row(con)
    con.close()
    generate.log_generation({"date": TODAY, "status": "failed",
                             "trigger": schedule.TRIGGER_SCHEDULED,
                             "retryable": True, "paused": "fetch"})

    _, _, raw = get(ui, "/")
    page = raw.decode("utf-8")
    today = page.split('id="view-today"')[1].split('id="view-following"')[0]
    assert "scheduled edition didn" in today


# ---------------------------------------------------------------------------
# ITEM 1 — the launchd agent (rendered here, installed by his hands)
# ---------------------------------------------------------------------------

def test_the_rendered_agent_is_a_valid_plist_that_will_not_self_start(tmp_paths):
    xml = schedule.render_plist(hour=6, env={}, binary=Path("/bin/echo"))
    d = plistlib.loads(xml.encode("utf-8"))

    assert d["Label"] == schedule.LAUNCHD_LABEL
    assert d["RunAtLoad"] is False, (
        "RunAtLoad true starts a ~30-minute generate the instant he bootstraps "
        "the agent, and again at every login")
    assert d["StartCalendarInterval"] == {"Hour": 6, "Minute": 0}
    assert d["ProgramArguments"][1:] == ["schedule", "run"]
    assert Path(d["ProgramArguments"][0]).is_absolute(), \
        "launchd has no PATH — a bare command dies at 6am with nothing to see"


def test_the_agent_takes_its_hour_from_the_existing_config_var(tmp_paths):
    xml = schedule.render_plist(env={"GENERATE_HOUR_LOCAL": "9"},
                                binary=Path("/bin/echo"))
    assert plistlib.loads(xml.encode())["StartCalendarInterval"]["Hour"] == 9

    xml = schedule.render_plist(env={}, binary=Path("/bin/echo"))
    assert (plistlib.loads(xml.encode())["StartCalendarInterval"]["Hour"]
            == schedule.config.DEFAULT_GENERATE_HOUR_LOCAL)


def test_the_binary_is_taken_from_the_venv_not_resolved_out_of_it(tmp_paths,
                                                                 monkeypatch,
                                                                 tmp_path):
    """MEASURED REGRESSION, not a hypothetical. `.venv/bin/python3` on his
    machine is a symlink into /Library/Developer/CommandLineTools; the first
    draft called `.resolve()` and `newslens schedule plist` refused on his own
    tree, naming a framework path he has never installed into. `sys.executable`
    is already absolute — resolving throws away the only part that matters."""
    venv_bin = tmp_path / "venv" / "bin"
    venv_bin.mkdir(parents=True)
    real_bin = tmp_path / "system" / "bin"
    real_bin.mkdir(parents=True)
    (real_bin / "python3").write_text("#!/bin/sh\n")
    (venv_bin / "python3").symlink_to(real_bin / "python3")
    (venv_bin / "newslens").write_text("#!/bin/sh\n")     # only in the venv

    monkeypatch.setattr(schedule.sys, "executable", str(venv_bin / "python3"))
    assert schedule.newslens_bin() == venv_bin / "newslens"


def test_rendering_refuses_rather_than_pointing_at_a_missing_binary(tmp_paths):
    with pytest.raises(schedule.ScheduleError) as exc:
        schedule.render_plist(hour=6, env={},
                              binary=Path("/nonexistent/newslens"))
    assert "console script is not at" in str(exc.value)


def test_the_org_never_writes_the_agent_file(tmp_paths, tmp_path):
    """LAW, not preference (dispatch 2026-08-13). Rendering, printing status and
    printing instructions must all leave ~/Library/LaunchAgents untouched."""
    home = tmp_path / "home"
    target = schedule.plist_path(home)
    schedule.render_plist(hour=6, env={}, binary=Path("/bin/echo"))
    schedule.install_instructions(hour=6, env={}, home=home)
    schedule.status(home=home, env={})
    assert not target.exists()
    assert not target.parent.exists()


def test_the_instructions_name_the_switch_and_the_removal(tmp_paths, tmp_path):
    text = schedule.install_instructions(hour=6, env={}, home=tmp_path)
    assert "launchctl bootstrap" in text
    assert "bootout" in text
    assert str(schedule.kill_switch_path()) in text
    assert "06:00" in text


# ---------------------------------------------------------------------------
# ITEM 5 — the doctor says what it can see and nothing more
# ---------------------------------------------------------------------------

def test_status_never_claims_the_agent_is_loaded(tmp_paths, tmp_path):
    """A plist on disk that was never bootstrapped is indistinguishable from a
    live one at this altitude. The readout must say file, not running — and must
    point at the command that does answer it."""
    home = tmp_path / "home"
    target = schedule.plist_path(home)
    target.parent.mkdir(parents=True)
    target.write_bytes(
        schedule.render_plist(hour=6, env={},
                              binary=Path("/bin/echo")).encode("utf-8"))

    lines = schedule.status_lines(home=home, env={})
    blob = "\n".join(lines).lower()
    assert "agent file present" in blob
    assert "launchctl print" in blob
    assert "no scheduled fire has been recorded yet" in blob
    assert "is running" not in blob


def test_status_reports_the_hour_mismatch_the_reader_would_never_guess(
        tmp_paths, tmp_path):
    home = tmp_path / "home"
    target = schedule.plist_path(home)
    target.parent.mkdir(parents=True)
    target.write_bytes(
        schedule.render_plist(hour=6, env={},
                              binary=Path("/bin/echo")).encode("utf-8"))

    lines = schedule.status_lines(home=home, env={"GENERATE_HOUR_LOCAL": "9"})
    assert any("MISMATCH" in l for l in lines), lines


def test_status_reports_the_last_fire_and_the_pause(tmp_paths, tmp_path):
    db.migrate()
    schedule.log_fire(schedule.FIRED_ALREADY, TODAY)
    schedule.kill_switch_path().parent.mkdir(parents=True, exist_ok=True)
    schedule.kill_switch_path().touch()

    blob = "\n".join(schedule.status_lines(home=tmp_path, env={}))
    assert schedule.FIRED_ALREADY in blob
    assert "PAUSED" in blob


def test_the_doctor_section_is_the_same_sentences(tmp_paths, tmp_path,
                                                  monkeypatch):
    """One machine, one description. The doctor and `schedule status` share
    `status_lines` so they cannot drift."""
    from newslens import doctor

    monkeypatch.setattr(schedule, "plist_path",
                        lambda home=None: tmp_path / "nope.plist")
    results = doctor.check_schedule({})
    assert results, "the doctor grew a section with nothing in it"
    assert "not installed" in results[0].text
