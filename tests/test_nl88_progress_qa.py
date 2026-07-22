"""NL-88 — the generate live-progress surface (PURE OBSERVABILITY).

Wiring proof for the progress side-channel:

  (a) NON-INTERFERENCE (the one guarantee that must hold): a progress callback
      that RAISES on every call does NOT affect run_generate's success or output
      — byte-identical GenReport with progress=None, progress=raising, and
      progress=recording. Born-red without the swallow in _emit_progress.
  (b) The emits are LIVE: a recording callback captures the real phase-boundary
      sequence (empty without the emit calls — a born-red wiring check), and
      GEN_JOB.snapshot() reflects the latest stage mid-run.
  (c) The CLI passes a printing callback, so a terminal `generate` prints stage
      transitions as they happen.
  (d) snapshot() returns the enriched payload shape the UI + CLI consume.

Fully offline — the same _chat / audio fakes the M5 writer suite uses.
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone

import pytest

from newslens import config, db, generate, llm, server
# Plain module-level helpers (NOT fixtures) reused from the writer suite.
from test_generate import (A_DAY, ENV, _fake_audio_ok, compliant_script,
                           seed_briefing, slot, stories_payload)


def _install_fake_model(monkeypatch, narrative_payload, script_text):
    """Stateful offline fake for both passes (mirrors test_generate.fake_model
    with editor=None -> the editor echoes the narrative, a no-op edit)."""
    calls = []

    def fake_chat(key, prompt, max_tokens, temperature, json_mode):
        calls.append({"json_mode": json_mode})
        content = json.dumps(narrative_payload) if json_mode else script_text
        return {
            "choices": [{"finish_reason": "stop",
                         "message": {"content": content}}],
            "usage": {"prompt_tokens": 900, "completion_tokens": 200},
        }

    monkeypatch.setattr(generate, "_chat", fake_chat)
    monkeypatch.setattr(time, "sleep", lambda s: None)
    return calls


def _build_seeded_con(tmp_path, name, slots):
    p = tmp_path / name
    db.migrate(db_path=p)
    con = db.connect(p)
    seed_briefing(con, A_DAY, slots)
    return con


# --- (a)+(b) NON-INTERFERENCE + live emits ----------------------------------

def test_progress_callback_is_non_interfering_and_live(tmp_path, monkeypatch):
    slots = [slot(1), slot(2)]
    narrative = stories_payload(slots)
    script = compliant_script(slots)
    _install_fake_model(monkeypatch, narrative, script)
    _fake_audio_ok(monkeypatch, [])

    # Baseline: no callback at all.
    con0 = _build_seeded_con(tmp_path, "none.db", slots)
    rep_none = generate.run_generate(date=A_DAY, con=con0, env=ENV,
                                     refresh=False, progress=None)
    con0.close()

    # A callback that blows up on EVERY call. If the swallow in _emit_progress
    # were absent this run would raise and never produce a report.
    def boom(label, model):
        raise RuntimeError("progress blew up — _emit_progress must swallow this")

    con1 = _build_seeded_con(tmp_path, "boom.db", slots)
    rep_boom = generate.run_generate(date=A_DAY, con=con1, env=ENV,
                                     refresh=False, progress=boom)
    con1.close()

    # A recording callback — proves the emits actually FIRE (born-red: this list
    # is empty if the _emit_progress calls were never added).
    seen = []
    con2 = _build_seeded_con(tmp_path, "rec.db", slots)
    rep_rec = generate.run_generate(
        date=A_DAY, con=con2, env=ENV, refresh=False,
        progress=lambda label, model: seen.append((label, model)))
    con2.close()

    # --- Non-interference: output is byte-identical across all three ---
    for rep in (rep_boom, rep_rec):
        assert rep.narrative_text == rep_none.narrative_text
        assert rep.script_text == rep_none.script_text
        assert rep.narrative_words == rep_none.narrative_words
        assert rep.script_words == rep_none.script_words
        assert [s.get("step") for s in rep.steps] == \
            [s.get("step") for s in rep_none.steps]
        assert [s.get("usd") for s in rep.steps] == \
            [s.get("usd") for s in rep_none.steps]
        assert rep.warnings == rep_none.warnings

    # --- The emits are live and in real pipeline order. refresh=False skips
    #     ingest/rank/analysis; a record run (sample=False, no_threads=False)
    #     fires the remaining boundaries in order. ---
    labels = [lbl for lbl, _ in seen]
    assert labels == [
        "Writing the briefing", "Editing", "Adapting the script",
        "Making the audio", "Saving", "Updating the story threads",
    ]
    models = dict(seen)
    assert models["Writing the briefing"] == llm.resolve_seat("writer", ENV).model
    assert models["Editing"] == llm.resolve_seat("editor", ENV).model
    assert models["Adapting the script"] == llm.resolve_seat("script", ENV).model
    assert models["Updating the story threads"] == llm.resolve_seat("state", ENV).model
    # Non-LLM phases carry no model.
    assert models["Making the audio"] is None
    assert models["Saving"] is None


def test_emit_progress_none_is_noop_and_swallows(monkeypatch):
    """Unit-level: the two halves of the guarantee in isolation."""
    # None progress: no-op, never touches the (would-be) model lookup.
    called = {"resolve": False}
    monkeypatch.setattr(llm, "resolve_seat",
                        lambda *a, **k: called.__setitem__("resolve", True))
    generate._emit_progress(None, "narrative", "writer")
    assert called["resolve"] is False

    # A raising callback is swallowed — _emit_progress never propagates.
    def boom(label, model):
        raise ValueError("nope")
    monkeypatch.undo()
    generate._emit_progress(boom, "narrative", "writer", env=ENV)  # must not raise

    # A seat that won't resolve degrades model to None, callback still fires.
    seen = []
    monkeypatch.setattr(llm, "resolve_seat",
                        lambda *a, **k: (_ for _ in ()).throw(KeyError("no seat")))
    generate._emit_progress(lambda l, m: seen.append((l, m)), "rank", "rank")
    assert seen == [("Ranking stories", None)]


# --- (b) GEN_JOB carries the live stage; (d) enriched snapshot shape ---------

_SNAPSHOT_KEYS = {"state", "error", "started_at", "stage", "stage_model",
                  "stage_elapsed_s", "total_elapsed_s"}


def test_genjob_snapshot_shape_and_live_stage():
    job = server._GenJob()

    # Idle: enriched shape, everything past state/error is empty/None.
    idle = job.snapshot()
    assert set(idle) == _SNAPSHOT_KEYS
    assert idle["state"] == "idle" and idle["error"] == ""
    assert idle["stage"] is None and idle["total_elapsed_s"] is None

    # Simulate a running job (start() would spawn a real thread — set directly).
    with job.lock:
        job.state = "running"
        job.started_at = datetime.now(timezone.utc).isoformat()

    run0 = job.snapshot()
    assert run0["state"] == "running"
    assert run0["stage"] is None
    assert run0["total_elapsed_s"] is not None and run0["total_elapsed_s"] >= 0.0

    # The callback fires at a phase boundary -> snapshot reflects it immediately.
    job._progress("Writing the briefing", "claude-opus")
    run1 = job.snapshot()
    assert run1["stage"] == "Writing the briefing"
    assert run1["stage_model"] == "claude-opus"
    assert run1["stage_elapsed_s"] is not None and run1["stage_elapsed_s"] >= 0.0

    # A later boundary REPLACES the stage (model may be None for a non-LLM phase).
    job._progress("Making the audio", None)
    run2 = job.snapshot()
    assert run2["stage"] == "Making the audio"
    assert run2["stage_model"] is None

    # A terminal state clears the live stage (done path).
    with job.lock:
        job.state = "done"
        job._clear_stage_locked()
    done = job.snapshot()
    assert done["state"] == "done"
    assert done["stage"] is None and done["stage_model"] is None
    assert done["stage_elapsed_s"] is None


# --- (c) CLI prints stage transitions ---------------------------------------

def test_cli_generate_passes_printing_progress(monkeypatch, capsys):
    """The CLI wires a printing callback into run_generate (born-red: without
    `progress=_progress` at the call site, the fake sees progress=None and the
    stage lines never print)."""
    monkeypatch.setattr(config, "load_env", lambda *a, **k: None)

    def fake_run_generate(*args, progress=None, **kwargs):
        assert progress is not None, "CLI must pass a progress callback"
        progress("Ranking stories", "claude-haiku")
        progress("Making the audio", None)
        # Short-circuit before the report-printing path (CLI catches this).
        raise generate.GenerateError("halt after progress (test)")

    monkeypatch.setattr(generate, "run_generate", fake_run_generate)

    from newslens import cli
    rc = cli.main(["generate"])
    assert rc == 1
    err = capsys.readouterr().err
    assert "Ranking stories" in err
    assert "claude-haiku" in err           # model shown when present
    assert "Making the audio" in err       # a model-less phase still prints
