"""Field-charged batch, 2026-07-17 — the subscription lane's first real day.

Ships two urgent field fixes (A + B) from the day-one failures; the ruled
lane defaults (C: writer/analyst -> subscription) and the battery lane arms (E)
are HELD as a coupled config/battery follow-up (a ~38-test lane re-pin — not
tail-of-batch work). The C-cap item DISSOLVED: the principal set his .env to the
shipped 1.50 default himself (DECISIONS 2026-07-17 "cap amended to $1.50");
config.py is untouched.

  A — the CLI-lane structured-output defect. `claude -p` runs the model inside
      the Claude Code agentic harness (large built-in system prompt even under
      --safe-mode; ~4.2k cache_creation tokens/call), so on the SUBSCRIPTION lane
      it emits conversational prose +/- a fenced ```json block, NOT bare JSON.
      The falsifier real run lost 11/24 (every failure "Expecting value: line 1
      column 1 (char 0)"; every success took exactly 2 attempts). Fix: extract
      the JSON object at the seam (json_mode only), with the HARD CONSTRAINT that
      extraction is presentation cleanup — it never weakens the caller's
      validation.

  B — lane-aware timeouts. His first post-B4 generate FAILED at rank: `claude -p`
      exceeded the 90s API-calibrated timeout on BOTH attempts (subprocess pays
      startup + harness overhead). Fix: per-seat subscription timeout
      (timeout_sub_s), used by the subscription provider.

Offline by construction: shims + the conftest stub; no network, no real key, $0.
"""

from __future__ import annotations

import json
import stat
import textwrap

import pytest

from newslens import follow_altitude as fa, llm


# --------------------------------------------------------------------------
# A — _extract_json_result unit pins
# --------------------------------------------------------------------------

def test_extract_unwraps_a_markdown_fence():
    assert json.loads(llm._extract_json_result('```json\n{"a": 1}\n```')) == {"a": 1}


def test_extract_strips_preamble_and_trailing_prose():
    txt = 'Here is my analysis.\n\n{"altitude": "entity"}\n\nThat is my answer.'
    assert json.loads(llm._extract_json_result(txt)) == {"altitude": "entity"}


def test_extract_takes_the_answer_object_after_reasoning_braces():
    # reasoning may contain brace-y fragments; the ANSWER (a parseable dict) is
    # what we return — the "last dict-parseable candidate" heuristic.
    txt = 'I considered {this} and {that} briefly. Final:\n{"altitude": "storyline"}'
    assert json.loads(llm._extract_json_result(txt))["altitude"] == "storyline"


def test_extract_is_a_noop_on_bare_json():
    s = '{"altitude": "entity"}'
    assert llm._extract_json_result(s) == s      # byte-unchanged (api fakes never move)


def test_extract_is_string_brace_safe():
    s = '{"disclosure": "Following the } story"}'
    assert json.loads(llm._extract_json_result("prefix prose " + s)) == json.loads(s)


def test_extract_no_object_stays_unparseable():
    # a result with no JSON object is returned as-is -> the caller's json.loads
    # rejects it (extraction never fabricates an object).
    with pytest.raises(ValueError):
        json.loads(llm._extract_json_result("I cannot help with that."))


# --------------------------------------------------------------------------
# A — the fix, end to end on the subscription lane
# --------------------------------------------------------------------------

def _write_shim(dir_path, result_content: str):
    """A minimal `claude -p` shim whose `result` is `result_content` (used to
    reproduce the field's fenced/verbose harness output)."""
    dir_path.mkdir(parents=True, exist_ok=True)
    src = textwrap.dedent("""\
        #!/usr/bin/env python3
        import sys, json
        if '--version' in sys.argv[1:]:
            print('2.1.212 (field-batch shim)'); sys.exit(0)
        sys.stdin.read()
        print(json.dumps({{'type': 'result', 'subtype': 'success',
                           'is_error': False, 'result': {content!r},
                           'session_id': 'fb', 'total_cost_usd': 0.0,
                           'usage': {{'input_tokens': 10, 'output_tokens': 1200,
                                      'cache_read_input_tokens': 0}}}}))
        """).format(content=result_content)
    shim = dir_path / "claude"
    shim.write_text(src)
    shim.chmod(shim.stat().st_mode | stat.S_IXUSR)
    return shim


def test_subscription_fenced_output_resolves_on_the_first_attempt(monkeypatch, tmp_paths):
    """The field failure shape (fenced + preamble + trailing prose) now parses on
    attempt 1 — no retry needed. Without the seam extraction this fails json.loads
    at char 0 on BOTH attempts -> AltitudeError (the born-red state, DEF-A)."""
    fenced = ("I'll analyze this thread.\n\n```json\n"
              + json.dumps({"altitude": "entity", "primary_entity": "Volkswagen",
                            "disclosure": "Following Volkswagen — the company.",
                            "confidence": "high"})
              + "\n```\n\nThat's my answer.")
    monkeypatch.setenv("NEWSLENS_CLAUDE_BIN", str(_write_shim(tmp_paths / "s1", fenced)))
    res = fa.resolve_altitude(fa.ThreadInput(22, "Volkswagen"))
    assert res.altitude == "entity" and res.primary_entity == "Volkswagen"
    assert res.attempts == 1                     # extracted on the first attempt


def test_subscription_extraction_never_weakens_validation(monkeypatch, tmp_paths):
    """HARD CONSTRAINT: a fenced-but-shape-invalid object (no disclosure) is
    unwrapped by extraction but STILL rejected by the validator -> AltitudeError.
    Extraction is presentation cleanup, never a validation bypass."""
    bad = ("```json\n"
           + json.dumps({"altitude": "entity", "primary_entity": "VW",
                         "confidence": "high"})     # disclosure missing
           + "\n```")
    monkeypatch.setenv("NEWSLENS_CLAUDE_BIN", str(_write_shim(tmp_paths / "s2", bad)))
    with pytest.raises(fa.AltitudeError):
        fa.resolve_altitude(fa.ThreadInput(1, "VW"))


def test_extraction_applies_only_on_json_mode(monkeypatch, tmp_paths):
    """A non-json_mode subscription result is passed through untouched (prose
    seats keep their fenced/backticked content verbatim)."""
    monkeypatch.setenv("NEWSLENS_CLAUDE_BIN",
                       str(_write_shim(tmp_paths / "s3", "```\nplain prose\n```")))
    resp = llm.chat(llm.LaneRequest(
        cfg=llm.resolve_seat("rank"), prompt="p", temperature=0, max_tokens=10,
        json_mode=False, user_agent="ua", api_key="k"))
    assert "```" in resp.content                  # not extracted (json_mode False)


# --------------------------------------------------------------------------
# A′ — the API lane fences too (ranking_runs 36); extraction on the api lane
# --------------------------------------------------------------------------

def test_api_lane_fenced_resolves_on_the_first_attempt_field_run36(monkeypatch, fake_api):
    """A′ (2026-07-17, field-charged): real Haiku fenced the 17k rank prompt on
    the API lane too, on BOTH attempts (ranking_runs 36 — char-0 twice, $0.0602
    charged for nothing, neither attempt truncated). The api provider now extracts
    the JSON object (json_mode) BEFORE the caller parses, so a fenced reply
    recovers on ATTEMPT 1 — no retry, no double-spend. (Born-red: with extraction
    disabled the fake returns fenced for both attempts and the resolver raises the
    exact 'Expecting value: line 1 column 1 (char 0)' field signature.)"""
    monkeypatch.setattr(llm, "ANTHROPIC_MESSAGES_URL",
                        fake_api.base_url + "/v1/messages")
    monkeypatch.setenv("NEWSLENS_LANE_FOLLOW_ALTITUDE", "api")
    monkeypatch.setenv("ANTHROPIC_API_KEY", fake_api.good_key)
    fenced = ("Here is the JSON you asked for:\n```json\n"
              + json.dumps({"altitude": "entity", "primary_entity": "Volkswagen",
                            "disclosure": "Following Volkswagen — the company.",
                            "confidence": "high"}) + "\n```")
    env = json.dumps({
        "id": "msg", "type": "message", "role": "assistant",
        "model": "claude-haiku-4-5",
        "content": [{"type": "text", "text": fenced}],
        "stop_reason": "end_turn",
        "usage": {"input_tokens": 1000, "output_tokens": 200,
                  "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0},
    }).encode("utf-8")
    fake_api.add_route("/v1/messages", 200, env, content_type="application/json")
    res = fa.resolve_altitude(fa.ThreadInput(22, "Volkswagen"))
    assert res.altitude == "entity" and res.primary_entity == "Volkswagen"
    assert res.attempts == 1                       # extracted; no retry / no double-spend
    assert res.lane == "api"


# --------------------------------------------------------------------------
# B — lane-aware timeouts
# --------------------------------------------------------------------------

def test_subscription_seat_timeouts_are_generous():
    assert llm.SEATS["rank"].timeout_sub_s == 300      # 90s api timeout was too tight
    assert llm.SEATS["editor"].timeout_sub_s == 300
    assert llm.SEATS["script"].timeout_sub_s == 300
    assert llm.SEATS["follow_altitude"].timeout_sub_s == 180
    # item C (2026-07-17): writer/analyst joined the subscription lane — sub
    # timeout = api ceiling + a ~300s lane tax (subprocess + harness overhead).
    assert llm.SEATS["analyst"].timeout_sub_s == 540   # 240 api + 300 tax
    assert llm.SEATS["writer"].timeout_sub_s == 900    # 600 api + 300 tax
    # api-lane timeouts are UNCHANGED (the api fall-over paths do not move)
    assert llm.SEATS["rank"].timeout_s == 90
    assert llm.SEATS["editor"].timeout_s == 120
    # a seat that never sets timeout_sub_s falls back to timeout_s (synthesis is
    # the lone api-only gpt-4o seat now)
    assert llm.SEATS["synthesis"].timeout_sub_s is None


def test_subscription_provider_uses_the_lane_timeout(monkeypatch):
    """The subprocess timeout is (timeout_sub_s or timeout_s) — 300 for rank on
    the subscription lane, not the 90s api-calibrated value that timed his rank
    out twice live."""
    captured = {}
    real_run = llm.subprocess.run

    def rec(args, **kw):
        captured["timeout"] = kw.get("timeout")
        return real_run(args, **kw)

    monkeypatch.setattr(llm.subprocess, "run", rec)
    llm.chat(llm.LaneRequest(
        cfg=llm.resolve_seat("rank"), prompt="p", temperature=0, max_tokens=10,
        json_mode=True, user_agent="ua", api_key="k"))
    assert captured["timeout"] == 300
