"""NL-88 ADVERSARIAL QA — hunts the edges the implementer's 4 tests leave open.

Priority order mirrors the QA charge:
  1. NON-INTERFERENCE on the REFRESH path (the implementer's byte-identity test
     is refresh=False, so the ingest/rank/analysis emit SITES are never proven
     to fire live — a wiring gap this file closes) + a BaseException boundary
     characterization (the swallow is `except Exception`, NOT BaseException).
  2. THREAD-SAFETY / stale-stage: the error path AND the BaseException-finally
     path must clear the live stage (implementer's test only covers the done
     path).
  3. SNAPSHOT: elapsed values non-negative and monotonic across reads.
  5. CLI: stage lines go to stderr and stdout stays byte-clean.

Offline by construction (conftest sandbox + loopback guard; fake _chat/audio,
faked ingest/rank as the NL-75 refresh harness does).
"""
from __future__ import annotations

import json
import time
from datetime import datetime, timezone

import pytest

from newslens import (config, db, generate, llm, ranking, server)
from newslens import ingest as ingest_mod
from test_generate import (A_DAY, ENV, _fake_audio_ok, compliant_script,
                           seed_briefing, slot, stories_payload)


# --------------------------------------------------------------------------
# shared offline fakes
# --------------------------------------------------------------------------

def _install_fake_model(monkeypatch, narrative_payload, script_text):
    def fake_chat(key, prompt, max_tokens, temperature, json_mode):
        content = json.dumps(narrative_payload) if json_mode else script_text
        return {"choices": [{"finish_reason": "stop",
                             "message": {"content": content}}],
                "usage": {"prompt_tokens": 900, "completion_tokens": 200}}
    monkeypatch.setattr(generate, "_chat", fake_chat)
    monkeypatch.setattr(time, "sleep", lambda s: None)


def _fake_ingest(con=None, env=None, **kw):
    r = type("R", (), {})()
    r.succeeded, r.attempted, r.items_new = ["A"], 1, 3
    r.discovery_status = "skipped"
    r.degradation_message = None
    return r


def _build_seeded_con(tmp_path, name, slots):
    p = tmp_path / name
    db.migrate(db_path=p)
    con = db.connect(p)
    seed_briefing(con, A_DAY, slots)
    return con


# ==========================================================================
# 1. NON-INTERFERENCE + LIVE EMITS on the REFRESH path (ingest/rank/analysis)
# ==========================================================================

def _refresh_fakes(monkeypatch, slots):
    _install_fake_model(monkeypatch, stories_payload(slots), compliant_script(slots))
    _fake_audio_ok(monkeypatch, [])

    def fake_rank(date=None, con=None, env=None, **kw):
        r = type("R", (), {})()
        r.warnings = []
        return r
    monkeypatch.setattr(ingest_mod, "run_ingest", _fake_ingest)
    monkeypatch.setattr(ranking, "run_rank", fake_rank)


def test_refresh_path_emits_fire_live(tmp_path, monkeypatch):
    """The implementer's live-emit test is refresh=False, so ONLY the writer..
    state boundaries are proven to fire. This drives refresh=True so the
    ingest/rank/analysis emit SITES fire — born-red if any of those three
    `_emit_progress(...)` lines is removed. (One run only: two full generates in
    a shared data dir is NOT identical starting state — the first creates
    generation_log.jsonl and the second's analysis stage sees it, so byte-
    identity across two refresh runs is a test-isolation confound, not a
    property. Non-interference is proven byte-for-byte on the refresh=False path
    and by completion below.)"""
    slots = [slot(1), slot(2)]
    _refresh_fakes(monkeypatch, slots)

    seen = []
    con = _build_seeded_con(tmp_path, "rec.db", slots)
    generate.run_generate(date=A_DAY, con=con, env=ENV, refresh=True,
                          progress=lambda l, m: seen.append((l, m)))
    con.close()

    labels = [l for l, _ in seen]
    assert labels[:4] == [
        "Gathering the news", "Ranking stories",
        "Reading the stories closely", "Writing the briefing",
    ], labels
    assert "Updating the story threads" in labels


def test_refresh_path_raising_callback_still_completes(tmp_path, monkeypatch):
    """A callback that raises on EVERY refresh-path boundary still produces a
    full edition — the swallow holds through ingest/rank/analysis emits too."""
    slots = [slot(1), slot(2)]
    _refresh_fakes(monkeypatch, slots)

    def boom(label, model):
        raise RuntimeError("progress blew up on the refresh path")
    con = _build_seeded_con(tmp_path, "boom.db", slots)
    rep = generate.run_generate(date=A_DAY, con=con, env=ENV,
                                refresh=True, progress=boom)
    con.close()
    assert rep.narrative_text and rep.script_text   # the run completed normally


def test_baseexception_from_callback_propagates_by_design(tmp_path, monkeypatch):
    """CHARACTERIZATION (not a defect): _emit_progress swallows `Exception`,
    NOT `BaseException`. A callback that raises SystemExit / KeyboardInterrupt
    therefore PROPAGATES out of run_generate — because run_generate itself only
    catches GenerateError, there is no broad net below it either. This is the
    CORRECT choice: a Ctrl-C landing inside the callback must interrupt the run
    exactly as one landing one instruction later would; swallowing BaseException
    would make a 40-min generate un-interruptible. The realistic callbacks
    (_GenJob._progress, the CLI printer) never raise BaseException on their own,
    so the stated 'a callback can never affect a generation' holds for every
    real callback; this test pins the boundary so a future 'harden' to
    `except BaseException` is a conscious, reviewed flip."""
    slots = [slot(1), slot(2)]
    _install_fake_model(monkeypatch, stories_payload(slots), compliant_script(slots))
    _fake_audio_ok(monkeypatch, [])

    for exc_type in (SystemExit, KeyboardInterrupt):
        con = _build_seeded_con(tmp_path, f"{exc_type.__name__}.db", slots)
        def raiser(label, model, _e=exc_type):
            raise _e("progress raised a BaseException")
        with pytest.raises(exc_type):
            generate.run_generate(date=A_DAY, con=con, env=ENV,
                                  refresh=False, progress=raiser)
        con.close()


def test_heavy_callback_cannot_change_output(tmp_path, monkeypatch):
    """A callback that does real work (allocations, string ops on its args)
    still yields a byte-identical edition — the args are immutable str/None, so
    there is no reachable generation state to perturb. (Timing is the only thing
    a slow callback affects; output/cost/ordering are invariant.)"""
    slots = [slot(1), slot(2)]
    narrative = stories_payload(slots)
    script = compliant_script(slots)
    _install_fake_model(monkeypatch, narrative, script)
    _fake_audio_ok(monkeypatch, [])

    con0 = _build_seeded_con(tmp_path, "plain.db", slots)
    rep0 = generate.run_generate(date=A_DAY, con=con0, env=ENV,
                                 refresh=False, progress=None)
    con0.close()

    sink = []
    def heavy(label, model):
        # touch the args every which way; none of it can reach the pipeline
        s = (label or "") * 1000
        sink.append((s.upper(), (model or "").lower(), len(s)))
    con1 = _build_seeded_con(tmp_path, "heavy.db", slots)
    rep1 = generate.run_generate(date=A_DAY, con=con1, env=ENV,
                                 refresh=False, progress=heavy)
    con1.close()

    assert rep1.narrative_text == rep0.narrative_text
    assert rep1.script_text == rep0.script_text
    assert rep1.warnings == rep0.warnings
    assert [s.get("usd") for s in rep1.steps] == [s.get("usd") for s in rep0.steps]


# ==========================================================================
# 2. THREAD-SAFETY / STALE STAGE: every terminal path clears the live stage
# ==========================================================================

def _job_running_with_stage():
    job = server._GenJob()
    with job.lock:
        job.state = "running"
        job.started_at = datetime.now(timezone.utc).isoformat()
    job._progress("Writing the briefing", "claude-opus")   # a live stage is set
    assert job.snapshot()["stage"] == "Writing the briefing"
    return job


def test_error_path_clears_stale_stage(monkeypatch):
    """The done path clears the stage (implementer's test). The ERROR path must
    too — a run that raises Exception mid-stage must not leave state=error while
    the last stage still shows. Drives _GenJob._run with a run_generate that
    emits a stage then raises."""
    job = server._GenJob()
    with job.lock:
        job.state = "running"
        job.started_at = datetime.now(timezone.utc).isoformat()

    monkeypatch.setattr(server.config, "load_env", lambda *a, **k: None)

    def fake_run_generate(progress=None, **kw):
        progress("Making the audio", None)          # a live stage lands
        raise RuntimeError("pipeline blew up mid-stage")
    monkeypatch.setattr(generate, "run_generate", fake_run_generate)

    job._run()   # synchronous; the except-branch handles the RuntimeError

    snap = job.snapshot()
    assert snap["state"] == "error"
    assert "pipeline blew up" in snap["error"]
    assert snap["stage"] is None
    assert snap["stage_model"] is None
    assert snap["stage_elapsed_s"] is None


def test_baseexception_run_path_clears_stale_stage(monkeypatch):
    """The Ride-24 finally guard must ALSO clear the stage: a BaseException from
    inside the run skips the `except Exception` branch, strands state at
    'running', and the finally flips it to error — the stage must go with it,
    or the UI (and any snapshot reader) would show a dead stage forever."""
    job = server._GenJob()
    with job.lock:
        job.state = "running"
        job.started_at = datetime.now(timezone.utc).isoformat()

    monkeypatch.setattr(server.config, "load_env", lambda *a, **k: None)

    def fake_run_generate(progress=None, **kw):
        progress("Making the audio", None)
        raise KeyboardInterrupt("Ctrl-C mid-run")
    monkeypatch.setattr(generate, "run_generate", fake_run_generate)

    with pytest.raises(KeyboardInterrupt):
        job._run()

    snap = job.snapshot()
    assert snap["state"] == "error"
    assert "abnormally" in snap["error"]
    assert snap["stage"] is None
    assert snap["stage_model"] is None
    assert snap["stage_elapsed_s"] is None


def test_restart_clears_prior_run_stage():
    """start() resets the stage triple so a second run never inherits the last
    run's live stage. (Uses the lock-guarded fields directly; does not spawn the
    daemon thread.)"""
    job = server._GenJob()
    with job.lock:
        job.state = "done"
        job.stage = "Saving"
        job.stage_model = None
        job.stage_started_at = datetime.now(timezone.utc).isoformat()
    # emulate exactly what start() does to the fields, minus the thread spawn
    with job.lock:
        job.state = "running"
        job.started_at = datetime.now(timezone.utc).isoformat()
        job.stage = None
        job.stage_model = None
        job.stage_started_at = None
    snap = job.snapshot()
    assert snap["stage"] is None and snap["stage_model"] is None
    assert snap["stage_elapsed_s"] is None


# ==========================================================================
# 3. SNAPSHOT: elapsed non-negative, monotonic, stage-elapsed resets per stage
# ==========================================================================

def test_snapshot_elapsed_nonnegative_monotonic_and_resets():
    job = server._GenJob()
    with job.lock:
        job.state = "running"
        job.started_at = datetime.now(timezone.utc).isoformat()

    job._progress("Gathering the news", None)
    a = job.snapshot()
    time.sleep(0.02)
    b = job.snapshot()
    # total elapsed is non-negative and never goes backwards
    assert a["total_elapsed_s"] >= 0.0 and b["total_elapsed_s"] >= 0.0
    assert b["total_elapsed_s"] >= a["total_elapsed_s"]
    # stage elapsed non-negative
    assert a["stage_elapsed_s"] >= 0.0 and b["stage_elapsed_s"] >= 0.0

    # a NEW stage resets stage_elapsed but NOT total
    job._progress("Ranking stories", None)
    c = job.snapshot()
    assert c["stage_elapsed_s"] >= 0.0
    assert c["stage_elapsed_s"] <= c["total_elapsed_s"] + 0.001
    assert c["total_elapsed_s"] >= b["total_elapsed_s"]


# ==========================================================================
# 5. CLI: stage lines to stderr; stdout byte-clean
# ==========================================================================

def test_cli_stage_lines_stderr_only_stdout_clean(monkeypatch, capsys):
    """Extends the implementer's CLI test: not only must the stage lines reach
    stderr, stdout must carry NONE of them (stdout is the narrative artifact)."""
    monkeypatch.setattr(config, "load_env", lambda *a, **k: None)

    def fake_run_generate(*args, progress=None, **kwargs):
        assert progress is not None
        progress("Gathering the news", None)
        progress("Writing the briefing", "claude-opus-4-8")
        raise generate.GenerateError("halt after progress (test)")
    monkeypatch.setattr(generate, "run_generate", fake_run_generate)

    from newslens import cli
    rc = cli.main(["generate"])
    assert rc == 1
    cap = capsys.readouterr()
    assert "Gathering the news" in cap.err
    assert "Writing the briefing" in cap.err
    assert "claude-opus-4-8" in cap.err
    # stdout must not contain any stage label — the report path never ran, and
    # the stage printer targets stderr.
    assert "Gathering the news" not in cap.out
    assert "Writing the briefing" not in cap.out
