"""NL-149 — the generation progress LOG and the saved generation REPORT.

His charter (2026-08-12, DECISIONS): "add a line to the bottom when generating
rather than replacing it with every step… Keep the time elapsed for each step
and a total time, and save the generation report so the user can access in the
settings menu."

Two halves, and they are pinned separately because they fail differently:

  ITEM 1 — THE LIVE LOG. server._GenJob keeps the steps that FINISHED, in order,
  each with the time it took, and the generating panel renders one line per
  finished step above the still-running one. The load-bearing property is
  APPEND-ONLY and it is SERVER-SIDE: the list only grows and an entry is never
  rewritten, which is what makes "a line that appeared never changes" survive a
  reload, a slow poll, or a stage shorter than the 2.5s poll interval. Pinned as
  an invariant over a sequence of snapshots, not as a claim about the client.

  ITEM 2 — THE SAVED REPORT. Every run — published, failed, or paused — persists
  a `stage_timeline` into data/generation_log.jsonl (additive: no schema change,
  no new artifact, every existing reader of that file sees the keys it always
  saw), and Settings reaches a view that renders it. Runs recorded BEFORE this
  says so in place rather than deriving a duration it cannot know.

Offline by construction: the same _chat/audio fakes the M5 writer suite uses,
under the conftest sandbox. $0, no network, no real state.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from newslens import generate, labels, paths, server, webui
from test_generate import (A_DAY, ENV, _fake_audio_ok, compliant_script,
                           seed_briefing, slot, stories_payload)
from test_v7_m2 import _con                                       # noqa: F401
from test_server import replica                                   # noqa: F401


# ---------------------------------------------------------------------------
# offline fakes (mirrors test_nl88_progress_qa._install_fake_model)
# ---------------------------------------------------------------------------

def _install_fake_model(monkeypatch, narrative_payload, script_text):
    import time as _time

    def fake_chat(key, prompt, max_tokens, temperature, json_mode):
        content = json.dumps(narrative_payload) if json_mode else script_text
        return {"choices": [{"finish_reason": "stop",
                             "message": {"content": content}}],
                "usage": {"prompt_tokens": 900, "completion_tokens": 200}}

    monkeypatch.setattr(generate, "_chat", fake_chat)
    monkeypatch.setattr(_time, "sleep", lambda s: None)


def _seeded(tmp_path, name, slots):
    from newslens import db
    p = tmp_path / name
    db.migrate(db_path=p)
    con = db.connect(p)
    seed_briefing(con, A_DAY, slots)
    return con


def _last_log_entry():
    lines = (paths.DATA_DIR / "generation_log.jsonl"
             ).read_text(encoding="utf-8").splitlines()
    return json.loads([l for l in lines if l.strip()][-1])


def _running_job(started_ago_s=0):
    job = server._GenJob()
    job.state = "running"
    job.started_at = (datetime.now(timezone.utc)
                      - timedelta(seconds=started_ago_s)).isoformat()
    return job


# ===========================================================================
# ITEM 1 — the live log
# ===========================================================================

def test_a_finished_step_becomes_a_line_that_keeps_its_own_time():
    """BORN RED. Before this, a phase boundary REPLACED the previous stage and
    the time it took was gone — the single-line surface his charter names."""
    job = _running_job()
    started = datetime.now(timezone.utc) - timedelta(seconds=95)
    job._progress("Gathering the news", None)
    with job.lock:                      # backdate the open stage by 95s
        job.stage_started_at = started.isoformat()
    job._progress("Ranking stories", "claude-haiku-4-5")

    steps = job.snapshot()["steps"]
    assert [s["label"] for s in steps] == ["Gathering the news"]
    assert steps[0]["model"] is None
    # the FINISHED step keeps ITS OWN duration, not the run's
    assert 94.0 <= steps[0]["elapsed_s"] <= 97.0, steps[0]
    # and the live line is the step now running
    assert job.snapshot()["stage"] == "Ranking stories"


def test_the_log_only_grows_and_never_rewrites_a_line():
    """BORN RED. THE load-bearing invariant of item 1: every snapshot's step
    list is a PREFIX-EXTENSION of every earlier one — nothing is reordered,
    rewritten or dropped. A client that re-rendered from a replaced list, or a
    server that kept only the last step, both fail here."""
    job = _running_job()
    seen = []
    for label, model in (("Gathering the news", None),
                         ("Ranking stories", "claude-haiku-4-5"),
                         ("Reading the stories closely", "claude-opus-4-8"),
                         ("Writing the briefing", "claude-opus-4-8")):
        job._progress(label, model)
        seen.append(job.snapshot()["steps"])

    for earlier, later in zip(seen, seen[1:]):
        assert later[:len(earlier)] == earlier, (earlier, later)
        assert len(later) == len(earlier) + 1
    assert [s["label"] for s in seen[-1]] == [
        "Gathering the news", "Ranking stories", "Reading the stories closely"]


def test_the_snapshot_hands_out_copies_not_the_jobs_own_list():
    """A reader of the status endpoint must not be able to reach into the
    running job's state (and json.dumps of a live list is the tear the lock
    exists to prevent)."""
    job = _running_job()
    job._progress("Gathering the news", None)
    job._progress("Ranking stories", None)
    snap = job.snapshot()
    snap["steps"][0]["label"] = "TAMPERED"
    snap["steps"].append({"label": "INJECTED"})
    assert [s["label"] for s in job.snapshot()["steps"]] == ["Gathering the news"]


def test_a_stage_that_never_finished_is_not_written_as_a_finished_step():
    """The error paths clear the live stage WITHOUT logging it: a stage that
    raised mid-way is not a completed step, and giving it a duration would put a
    time on screen for work that never landed. The done path does log it."""
    job = _running_job()
    job._progress("Writing the briefing", "claude-opus-4-8")
    with job.lock:
        job.state = "error"
        job._clear_stage_locked()               # the error path's own call
    assert job.snapshot()["steps"] == []

    ok = _running_job()
    ok._progress("Writing the briefing", "claude-opus-4-8")
    with ok.lock:
        ok.state = "done"
        ok._clear_stage_locked(completed=True)  # the done path's own call
    assert [s["label"] for s in ok.snapshot()["steps"]] == ["Writing the briefing"]


def test_a_new_run_starts_a_new_log(monkeypatch):
    """start() clears the previous run's lines — they are not this run's. (The
    thread start is stubbed: no generate ever fires from a test.)"""
    job = server._GenJob()
    job._progress("Writing the briefing", None)
    job._progress("Saving", None)
    assert len(job.snapshot()["steps"]) == 1

    import threading
    monkeypatch.setattr(threading, "Thread",
                        lambda *a, **k: type("T", (), {"start": lambda s: None})())
    assert job.start() is True
    assert job.snapshot()["steps"] == []


def test_the_generating_panel_renders_a_line_per_finished_step(monkeypatch):
    """BORN RED — the surface his charter is about. One <li> per finished step
    with its time; the live line carries ONLY the step now running."""
    job = _running_job()
    job._progress("Gathering the news", None)
    job._progress("Ranking stories", "claude-haiku-4-5")
    job._progress("Writing the briefing", "claude-opus-4-8")
    monkeypatch.setattr(server, "GEN_JOB", job)

    con = _con()
    page, _ = server.build_page(con)
    con.close()

    log = page[page.index('<ol class="gen-log"'):]
    log = log[:log.index("</ol>") + 5]
    assert log.count("<li>") == 2
    assert "Gathering the news" in log and "Ranking stories" in log
    assert "claude-haiku-4-5" in log
    assert "Writing the briefing" not in log        # still running, not a line
    # the cursor the client appends from == the lines actually rendered
    assert 'data-count="2"' in log
    # the live line is the running step, unchanged in kind
    live = page[page.index('id="gen-live"'):]
    assert "Writing the briefing" in live[:400]


def test_the_log_survives_a_reload_mid_run(monkeypatch):
    """The lines are SERVER-rendered, so a reload in the middle of a 40-minute
    run redraws the whole log — it does not restart empty and it does not
    double-print (the cursor is re-derived from what was rendered)."""
    job = _running_job()
    job._progress("Gathering the news", None)
    job._progress("Ranking stories", None)
    monkeypatch.setattr(server, "GEN_JOB", job)
    con = _con()
    first, _ = server.build_page(con)
    job._progress("Writing the briefing", None)
    second, _ = server.build_page(con)
    con.close()

    assert 'data-count="1"' in first
    assert 'data-count="2"' in second
    assert second.count('<span class="gen-log-step">') == 2
    for label in ("Gathering the news", "Ranking stories"):
        assert second.count(f'>{label}</span>') == 1     # once, never twice


def test_an_unknown_duration_renders_as_a_dash_never_as_zero():
    """A missing elapsed is a missing FACT. 0:00 would claim the step was
    instant."""
    assert server._fmt_elapsed(None) == "—"
    assert server._fmt_elapsed("nonsense") == "—"
    assert server._fmt_elapsed(0) == "0:00"
    assert server._fmt_elapsed(95.4) == "1:35"
    assert server._fmt_elapsed(2489) == "41:29"


# ===========================================================================
# ITEM 2 — the saved report
# ===========================================================================

def test_a_published_run_persists_its_stage_timeline(tmp_path, monkeypatch):
    """BORN RED — the persistence his charter asks for. A real (offline) run
    lands a timeline in generation_log.jsonl whose entries are EXACTLY the
    boundaries the progress channel announced, in order, with models and times.

    The equality with `seen` is the wiring proof: an emit site that stopped
    being recorded, or a timeline built from a different list, both fail."""
    slots = [slot(1), slot(2)]
    _install_fake_model(monkeypatch, stories_payload(slots), compliant_script(slots))
    _fake_audio_ok(monkeypatch, [])
    con = _seeded(tmp_path, "ok.db", slots)
    seen = []
    rep = generate.run_generate(
        date=A_DAY, con=con, env=ENV, refresh=False,
        progress=lambda label, model: seen.append((label, model)))
    con.close()

    entry = _last_log_entry()
    assert entry["status"] == "ok"
    timeline = entry["stage_timeline"]
    assert [(s["label"], s["model"]) for s in timeline] == seen
    assert [s["label"] for s in timeline] == [
        "Writing the briefing", "Editing", "Adapting the script",
        "Making the audio", "Saving", "Updating the story threads"]
    for s in timeline:
        assert isinstance(s["elapsed_s"], float) and s["elapsed_s"] >= 0.0
        assert s["started_at"].endswith("Z")
    assert isinstance(entry["elapsed_s"], float)
    assert rep.run_elapsed_s == entry["elapsed_s"]
    # the money ledger is UNTOUCHED beside it — two ledgers, not one merged one
    assert [s.get("step") for s in entry["steps"]] and "elapsed_s" not in entry["steps"][0]


def test_a_run_with_no_callback_at_all_still_lands_in_the_report(tmp_path, monkeypatch):
    """BORN RED, and the reason the recorder is a WRAPPER installed in
    run_generate: a terminal `newslens generate` (and, tomorrow, an unattended
    NL-146 run) must land in the settings report exactly like a UI run."""
    slots = [slot(1)]
    _install_fake_model(monkeypatch, stories_payload(slots), compliant_script(slots))
    _fake_audio_ok(monkeypatch, [])
    con = _seeded(tmp_path, "silent.db", slots)
    generate.run_generate(date=A_DAY, con=con, env=ENV, refresh=False,
                          progress=None)
    con.close()
    assert [s["label"] for s in _last_log_entry()["stage_timeline"]] == [
        "Writing the briefing", "Editing", "Adapting the script",
        "Making the audio", "Saving", "Updating the story threads"]


def test_a_raising_callback_cannot_stop_the_timeline(tmp_path, monkeypatch):
    """Non-interference, BOTH directions. The recorder runs BEFORE the caller's
    callback, so a callback that raises on every boundary (the pinned NL-88
    `boom` case) still leaves a complete record — and the raise still cannot
    touch the generation."""
    slots = [slot(1)]
    _install_fake_model(monkeypatch, stories_payload(slots), compliant_script(slots))
    _fake_audio_ok(monkeypatch, [])
    con = _seeded(tmp_path, "boom.db", slots)

    def boom(label, model):
        raise RuntimeError("progress blew up")

    rep = generate.run_generate(date=A_DAY, con=con, env=ENV, refresh=False,
                                progress=boom)
    con.close()
    assert rep.narrative_text and rep.script_text          # the run completed
    assert len(_last_log_entry()["stage_timeline"]) == 6


def test_a_failed_run_keeps_the_timeline_it_got_through(tmp_path, monkeypatch):
    """BORN RED. A run that died in the editor after twenty minutes is the most
    useful row the log can hand a reader — and NL-146's unattended runs will
    produce them with nobody watching."""
    slots = [slot(1), slot(2), slot(3)]
    _install_fake_model(monkeypatch, stories_payload(slots),
                        "Way too short. " + generate.SIGNOFF)   # stub -> abort
    _fake_audio_ok(monkeypatch, [])
    con = _seeded(tmp_path, "fail.db", slots)
    with pytest.raises(generate.GenerateError):
        generate.run_generate(date=A_DAY, con=con, env=ENV, refresh=False)
    con.close()

    entry = _last_log_entry()
    assert entry["status"] == "failed"
    labels_seen = [s["label"] for s in entry["stage_timeline"]]
    assert labels_seen[:2] == ["Writing the briefing", "Editing"]
    assert "Adapting the script" in labels_seen          # where it died
    assert all(isinstance(s["elapsed_s"], float) for s in entry["stage_timeline"])
    assert isinstance(entry["elapsed_s"], float)


# --- the view ---------------------------------------------------------------

_TIMELINE = [{"label": "Gathering the news", "model": None,
              "started_at": "2026-08-12T22:00:00Z", "elapsed_s": 61.0},
             {"label": "Writing the briefing", "model": "claude-opus-4-8",
              "started_at": "2026-08-12T22:01:01Z", "elapsed_s": 1502.4}]


def _write_log(*entries):
    paths.DATA_DIR.mkdir(parents=True, exist_ok=True)
    (paths.DATA_DIR / "generation_log.jsonl").write_text(
        "".join(json.dumps(e) + "\n" for e in entries), encoding="utf-8")


def test_the_report_view_renders_runs_and_ignores_stage_lines():
    """BORN RED. Two kinds of line live in generation_log.jsonl — runs and the
    analysis stage's own instrumentation. 23 of the founder's 49 lines are stage
    lines; rendering them as runs would double every day's count."""
    _write_log(
        {"ts": "2026-08-11T10:00:00Z", "date": "2026-08-11", "status": "ok",
         "total_usd": 0.0031, "steps": [{"step": "narrative"}]},          # legacy
        {"ts": "2026-08-12T22:10:53Z", "stage": "analysis", "status": "ok"},
        {"ts": "2026-08-12T23:00:00Z", "date": "2026-08-12", "status": "failed",
         "error": "editor failed: boom", "total_usd": 0.0027, "steps": []},
        {"ts": "2026-08-13T02:51:50Z", "date": "2026-08-12", "status": "ok",
         "total_usd": 0.0, "steps": [], "elapsed_s": 1563.4,
         "stage_timeline": _TIMELINE},
    )
    html = server._render_run_log()
    assert html.count('<div class="runlog-run">') == 3          # not 4
    # newest first
    heads = [h.split("</span>")[0] for h in
             html.split('<span class="runlog-when">')[1:]]
    assert len(heads) == 3 and "Aug 12" in heads[0]

    newest = html.split('<div class="runlog-run">')[1]
    assert labels.RUNLOG_PUBLISHED in newest
    assert "Gathering the news" in newest and "claude-opus-4-8" in newest
    assert "1:01" in newest and "25:02" in newest       # per-step times
    assert f"{labels.RUNLOG_TOTAL} 26:03" in newest     # the run total
    assert f"$0 {labels.RUNLOG_CHARGED}" in newest      # a $0 lane says $0

    failed = html.split('<div class="runlog-run">')[2]
    assert labels.RUNLOG_FAILED in failed
    assert "editor failed: boom" in failed
    assert labels.RUNLOG_NO_TIMING in failed or labels.RUNLOG_STEPS_UNRECORDED in failed

    legacy = html.split('<div class="runlog-run">')[3]
    assert labels.RUNLOG_NO_TIMING in legacy
    assert "$0.0031 " + labels.RUNLOG_CHARGED in legacy


def test_a_paused_run_is_never_reported_as_a_plain_failure():
    """NL-148 clause 4's pause rides as `paused` BESIDE status="failed" (that
    entry kept the old word so no existing consumer moved). The report reads the
    marker FIRST — a run that stopped on purpose must not send the reader
    hunting for a bug. The sentence is read from analysis, not retyped, so a
    re-pin there travels here."""
    from newslens import analysis
    _write_log({"ts": "2026-08-13T06:00:00Z", "date": "2026-08-13",
                "status": "failed", "paused": "fetch", "retryable": True,
                "error": analysis.FETCH_PAUSE_MESSAGE, "total_usd": 0.0,
                "steps": [], "stage_timeline": _TIMELINE, "elapsed_s": 1563.4})
    html = server._render_run_log()
    block = html.split('<div class="runlog-run">')[1]
    assert labels.RUNLOG_PAUSED in block
    assert f'>{labels.RUNLOG_FAILED}<' not in block
    assert analysis.FETCH_PAUSE_MESSAGE[:40] in block


def test_a_sample_run_is_marked_as_one():
    """A sample never touched the edition of record; reporting it as Published
    would put a run on the record that published nothing."""
    _write_log({"ts": "2026-08-13T06:00:00Z", "date": "2026-08-13",
                "status": "ok", "sample": True, "total_usd": 0.0, "steps": []})
    assert labels.RUNLOG_SAMPLE in server._render_run_log()


def test_an_empty_record_says_so_in_both_places(monkeypatch):
    """Honest empty state — and the SAME words in the Settings row and the view,
    so the reader is never told there is something to open when there is not."""
    _write_log()
    con = _con()
    page, _ = server.build_page(con)
    con.close()
    assert page.count(labels.RUNLOG_EMPTY) >= 2


def test_the_report_is_reachable_from_settings(monkeypatch):
    """BORN RED — 'save the generation report so the user can access in the
    settings menu' is the charter clause this pins: the row exists, its action
    opens the view, and the view is on the page."""
    _write_log({"ts": "2026-08-13T06:00:00Z", "date": "2026-08-13",
                "status": "ok", "total_usd": 0.0, "steps": [],
                "elapsed_s": 12.0, "stage_timeline": _TIMELINE})
    con = _con()
    page, _ = server.build_page(con)
    con.close()

    # the slide panel runs from its own id to the first popup scrim after it
    panel = page[page.index('id="slide-panel"'):page.index('class="popup-scrim"')]
    assert labels.RUNLOG_TITLE in panel                 # the row is IN Settings
    assert labels.RUNLOG_OPEN in panel
    assert "openRunLog(event)" in page                  # the row's action
    assert 'id="view-runlog"' in page                   # its destination
    assert labels.RUNLOG_RUNS_ONE in page               # the honest count
    # and the destination is a real view with a working way back
    view = page[page.index('id="view-runlog"'):]
    assert 'aria-label="Back to Today"' in view[:600]
    assert "closeRunLog(event)" in view[:600]


def test_the_settings_row_counts_runs_not_log_lines():
    """The count the row shows is RUNS — the same filter the view applies."""
    _write_log(
        {"ts": "2026-08-11T10:00:00Z", "date": "2026-08-11", "status": "ok"},
        {"ts": "2026-08-11T10:05:00Z", "stage": "analysis", "status": "ok"},
        {"ts": "2026-08-12T10:00:00Z", "date": "2026-08-12", "status": "failed"},
    )
    con = _con()
    page, _ = server.build_page(con)
    con.close()
    assert labels.RUNLOG_RUNS_MANY.format(n=2) in page


def test_the_view_is_bounded_and_says_when_it_is_truncated():
    """An append-only log grows forever; the page does not. When the view shows
    a window, it states the window — a count is never implied by silence."""
    bound = server._RUNLOG_MAX_ROWS + 7
    _write_log(*[{"ts": f"2026-08-{(i % 28) + 1:02d}T10:00:00Z",
                  "date": "2026-08-12", "status": "ok", "total_usd": 0.0}
                 for i in range(bound)])
    runs, total = server._run_log_entries()
    assert len(runs) == server._RUNLOG_MAX_ROWS and total == bound
    html = server._render_run_log()
    assert html.count('<div class="runlog-run">') == server._RUNLOG_MAX_ROWS
    assert labels.RUNLOG_TRUNCATED_MIDDLE in html
    assert str(total) in html


def test_an_unreadable_or_corrupt_log_degrades_honestly():
    """A truncated line (a crash mid-append) loses that line, never the view."""
    paths.DATA_DIR.mkdir(parents=True, exist_ok=True)
    (paths.DATA_DIR / "generation_log.jsonl").write_text(
        json.dumps({"ts": "2026-08-13T06:00:00Z", "date": "2026-08-13",
                    "status": "ok", "total_usd": 0.0}) + "\n"
        + '{"ts": "2026-08-13T07:00:00Z", "date": "2026-0',                 # torn
        encoding="utf-8")
    runs, total = server._run_log_entries()
    assert total == 1
    assert '<div class="runlog-run">' in server._render_run_log()


def test_the_step_line_is_one_shape_wherever_it_is_rendered():
    """The live panel and the saved report render the SAME line — same tags,
    same classes, same M:SS — because both go through server._gen_log_line.
    Two renderers for one line is how a reloaded log starts looking different
    from a live one."""
    step = {"label": "Writing the briefing", "model": "claude-opus-4-8",
            "elapsed_s": 1502.4}
    line = server._gen_log_line(step)
    assert line == ('<li><span class="gen-log-step">Writing the briefing</span>'
                    '<span class="gen-log-model"> · claude-opus-4-8</span>'
                    '<span class="gen-log-time">25:02</span></li>')
    # a model-less stage carries no empty model span
    assert '"gen-log-model"' not in server._gen_log_line(
        {"label": "Saving", "model": None, "elapsed_s": 3})
    # and the client builds the same three spans, in the same order
    js = webui.JS[webui.JS.index("function genLogLine"):]
    js = js[:js.index("function genLogSync")]
    assert js.index("gen-log-step") < js.index("gen-log-model") < js.index("gen-log-time")


def test_a_label_repin_moves_the_rendered_surface(monkeypatch):
    """Label-table liveness (the v7 convention): the view reads labels at render
    time, so a re-pin lands without a second copy to chase."""
    _write_log()
    monkeypatch.setattr(labels, "RUNLOG_TITLE", "Run history")
    monkeypatch.setattr(labels, "RUNLOG_EMPTY", "Nothing recorded.")
    html = server._render_run_log()
    assert "Run history" in html and "Nothing recorded." in html
    assert "Generation reports" not in html
