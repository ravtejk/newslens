"""Field-fix batch (A + A' + A'' + state-flip + B) — QA adversarial pass
(QA-owned; the attacks and coverage the implementer's field file leaves open).

Pinned here:
  * EXTRACTION EDGES: multi-answer last-dict-wins (incl. the array-wrapped
    answer — a previously-red shape that can now validate: behavior PINNED,
    posture flagged to the gate), unparseable-last falls back to an earlier
    parseable candidate, nested/escaped braces exactness, fence-with-two-objects
    stays caller-rejected, no-newline fences, empty results.
  * MONEY: the estimated-usage path prices the FULL generated result, never the
    extracted substring; attempt-1 recovery bills exactly once on the resolver
    path; the state seat's charged-0/Haiku-shadow on BOTH metered and estimated
    paths.
  * SCOPING: the api lane's non-json_mode content is byte-faithful (the
    implementer pinned only the subscription side).
  * TIMEOUTS: the full timeout_sub_s map pinned exactly (the exact-set roster
    guard did not grow the new knob); the subscription provider uses the sub
    knob for state (300) and follow_altitude (180); the api provider still
    passes the api knob (rank 90).
  * STATE FLIP: end-to-end _default_state_chat through the subscription shim
    with FENCED state JSON (the flip depends on extraction — proven, not
    assumed); check_lane's gate message on the flipped seat; seat_is_openai
    cannot be fooled by lane/model env overrides.
  * A'' POSITIVE ARMS: the rank and analysis keyless guards actually FIRE when
    their seat resolves to openai (wiring-proof law — the flipped tests only
    prove the absence of the old refusal).
  * DOCTOR: check_openai_key's three new branches (keyless-FAIL naming the lone
    openai seat, key-unused INFO, key-not-needed INFO) — none had any test.
  * FOR THE GATE (pin, not ruling): the state stage preflight is a raw
    check_lane that does NOT honor an armed fallback while effective_seat falls.

Offline by construction under the autouse sandbox; $0.
"""

from __future__ import annotations

import dataclasses
import json
import stat as stat_mod
import textwrap
import urllib.request
from pathlib import Path

import pytest

from newslens import (analysis, config, db, doctor, follow_altitude as fa, llm,
                      memory_core, paths, ranking)


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------

def _pick(altitude="entity", primary="Volkswagen",
          disclosure="Following Volkswagen — the company, not just this story.",
          confidence="high") -> str:
    return json.dumps({"altitude": altitude, "primary_entity": primary,
                       "disclosure": disclosure, "confidence": confidence})


def _make_shim(dir_path: Path, result_content: str, with_usage: bool = True,
               inp: int = 1000, out: int = 200) -> Path:
    """A `claude -p` shim whose envelope `result` is `result_content`.
    with_usage=False OMITS the usage block entirely -> the estimated-usage
    normalization path."""
    dir_path.mkdir(parents=True, exist_ok=True)
    payload = {"type": "result", "subtype": "success", "is_error": False,
               "result": result_content, "session_id": "fbqa",
               "total_cost_usd": 0.0}
    if with_usage:
        payload["usage"] = {"input_tokens": inp, "output_tokens": out,
                            "cache_read_input_tokens": 0}
    src = textwrap.dedent("""\
        #!/usr/bin/env python3
        import sys, json
        if '--version' in sys.argv[1:]:
            print('2.1.212 (field-batch QA shim)'); sys.exit(0)
        sys.stdin.read()
        print({payload!r})
        """).format(payload=json.dumps(payload))
    shim = dir_path / "claude"
    shim.write_text(src)
    shim.chmod(shim.stat().st_mode | stat_mod.S_IXUSR)
    return shim


def _ant_env(text: str, inp: int = 1000, out: int = 200) -> bytes:
    return json.dumps({
        "id": "msg", "type": "message", "role": "assistant",
        "model": "claude-haiku-4-5",
        "content": [{"type": "text", "text": text}],
        "stop_reason": "end_turn",
        "usage": {"input_tokens": inp, "output_tokens": out,
                  "cache_creation_input_tokens": 0,
                  "cache_read_input_tokens": 0},
    }).encode("utf-8")


_GPT4O_STATE_ROW = dict(provider="openai", model="gpt-4o", lane="api",
                        usd_per_mtok_in=2.50, usd_per_mtok_out=10.00)


# --------------------------------------------------------------------------
# extraction edges (unit)
# --------------------------------------------------------------------------

def test_extract_two_answer_objects_last_dict_wins():
    """Documented heuristic pinned: with TWO parseable answer objects, the LAST
    wins (the answer follows any reasoning). This includes contradictory
    candidates — extraction picks silently; the caller's shape validation is
    the only remaining teeth. Behavior pin; posture noted to the gate."""
    two = 'First guess: {"a": 1}\nOn reflection: {"b": 2}'
    assert llm._extract_json_result(two) == '{"b": 2}'


def test_extract_array_wrapped_answer_passes_through_for_the_validator():
    """GATE RULED 2026-07-17 (ruling 3, RESTRICT): whole-result JSON that parses
    as a NON-DICT (array/scalar) passes through extraction INTACT — pre-fix those
    parsed fine and the SHAPE validator rejected them, and extraction must never
    overrule a validation outcome by silently picking one member of an array.
    The caller's json.loads succeeds; its validator drives the corrected retry
    ('expected a single JSON object'). Fenced arrays return the BODY (the
    validator must see the payload, not the fence). Prose-embedded last-dict
    extraction (the observed field shape) is unchanged."""
    assert llm._extract_json_result('[{"a": 1}]') == '[{"a": 1}]'
    assert llm._extract_json_result('[{"a": 1}, {"b": 2}]') == '[{"a": 1}, {"b": 2}]'
    assert llm._extract_json_result('42') == '42'
    assert llm._extract_json_result('```json\n[{"a": 1}, {"b": 2}]\n```') \
        == '[{"a": 1}, {"b": 2}]'


def test_resolver_whole_array_reply_is_refereed_by_the_validator(monkeypatch,
                                                                 tmp_path):
    """Gate FIX-1 e2e: a whole-ARRAY reply on both attempts passes through
    extraction intact, json.loads SUCCEEDS, and the VALIDATOR drives the
    corrected retry with its expected-single-object message — extraction never
    silently picks a member. Both refereed attempts billed; the final
    AltitudeError carries the validator's message and the retry prompt echoes
    it (the shim records stdin per call)."""
    d = tmp_path / "s"
    d.mkdir(parents=True, exist_ok=True)
    rec = d / "stdin.log"
    arr = "[" + _pick() + ", " + _pick() + "]"
    payload = {"type": "result", "subtype": "success", "is_error": False,
               "result": arr, "session_id": "fbqa", "total_cost_usd": 0.0,
               "usage": {"input_tokens": 1000, "output_tokens": 200,
                         "cache_read_input_tokens": 0}}
    src = textwrap.dedent("""\
        #!/usr/bin/env python3
        import sys
        if '--version' in sys.argv[1:]:
            print('2.1.212 (field-batch QA shim)'); sys.exit(0)
        data = sys.stdin.read()
        with open({rec!r}, 'a') as f:
            f.write(data + chr(30))
        print({payload!r})
        """).format(rec=str(rec), payload=json.dumps(payload))
    shim = d / "claude"
    shim.write_text(src)
    shim.chmod(shim.stat().st_mode | stat_mod.S_IXUSR)
    monkeypatch.setenv("NEWSLENS_CLAUDE_BIN", str(shim))
    monkeypatch.setattr(fa.time, "sleep", lambda s: None)
    sink = []
    with pytest.raises(fa.AltitudeError, match="expected a single JSON object"):
        fa.resolve_altitude(fa.ThreadInput(1, "VW"), cost_sink=sink)
    assert len(sink) == 2                     # both refereed attempts billed
    calls = rec.read_text().split(chr(30))
    assert "expected a single JSON object" in calls[1]   # retry echoes the validator


def test_extract_unparseable_last_falls_back_to_earlier_parseable():
    txt = 'answer {"a": 1} but also {not json}'
    assert llm._extract_json_result(txt) == '{"a": 1}'


def test_extract_nested_and_escaped_braces_exact_substring():
    obj = '{"a": {"b": "c}d{"}, "e": "f\\"g{h"}'
    out = llm._extract_json_result("prose before " + obj + " prose after")
    assert out == obj
    assert json.loads(out) == json.loads(obj)


def test_extract_fence_with_two_objects_stays_caller_rejected():
    """A fence whose body is TWO objects comes back whole (starts { ends }) and
    still fails the caller's json.loads — extraction never picks for a fence
    body that is not one object, and never repairs it."""
    fenced = '```json\n{"a": 1} {"b": 2}\n```'
    out = llm._extract_json_result(fenced)
    with pytest.raises(ValueError):
        json.loads(out)


def test_extract_fence_without_newline_still_scans():
    assert llm._extract_json_result('```json{"a": 1}```') == '{"a": 1}'


@pytest.mark.parametrize("empty", ["", "   ", "```\n\n```"])
def test_extract_empty_shapes_stay_unparseable(empty):
    with pytest.raises(ValueError):
        json.loads(llm._extract_json_result(empty))


# --------------------------------------------------------------------------
# money: estimated usage prices the FULL result; attempt-1 recovery bills once
# --------------------------------------------------------------------------

def test_estimated_usage_prices_the_full_result_not_the_extracted_substring(
        monkeypatch, tmp_path):
    """The A-fix's money-honesty rider, mechanically: with the CLI reporting NO
    usage, tokens are estimated from what the model GENERATED (the full fenced/
    verbose result), never from the shorter extracted substring — shrinking the
    bill because we cleaned up presentation would be under-counting."""
    inner = _pick()
    full = ("Let me think about this thread carefully. " * 20
            + "\n```json\n" + inner + "\n```\n" + "Hope that helps! " * 10)
    shim = _make_shim(tmp_path / "s", full, with_usage=False)
    monkeypatch.setenv("NEWSLENS_CLAUDE_BIN", str(shim))
    resp = llm.chat(llm.LaneRequest(
        cfg=llm.resolve_seat("rank"), prompt="p", temperature=0, max_tokens=10,
        json_mode=True, user_agent="ua", api_key="k"))
    assert resp.content == inner                       # extracted for the caller
    est_full = int(len(full) / 3.5)
    est_inner = int(len(inner) / 3.5)
    assert resp.usage.completion_tokens == est_full    # priced on the FULL result
    assert resp.usage.completion_tokens > est_inner    # and provably not the substring
    fields = llm.cost_fields(llm.resolve_seat("rank"), resp.raw.get("usage"))
    assert fields.get("usd_shadow_estimated") is True  # labeled, never fake precision
    assert fields["usd_charged"] == 0.0


def test_resolver_fenced_recovery_bills_exactly_once(monkeypatch, tmp_path):
    fenced = "Here you go:\n```json\n" + _pick() + "\n```"
    monkeypatch.setenv("NEWSLENS_CLAUDE_BIN",
                       str(_make_shim(tmp_path / "s", fenced)))
    sink = []
    res = fa.resolve_altitude(fa.ThreadInput(1, "Volkswagen"), cost_sink=sink)
    assert res.attempts == 1
    assert len(sink) == 1                              # no double-bill on recovery
    assert sink[0]["lane"] == "subscription"
    assert sink[0]["usd_charged"] == 0.0 and sink[0]["usd_shadow"] > 0


# --------------------------------------------------------------------------
# scoping: the api lane's non-json_mode content is byte-faithful
# --------------------------------------------------------------------------

def test_api_lane_non_json_mode_passes_fences_through(monkeypatch, fake_api):
    """The implementer pinned json_mode-only scoping on the subscription side;
    this is the api-lane half: a prose (non-json_mode) anthropic api reply keeps
    its fences verbatim — extraction never touches prose seats on either lane."""
    monkeypatch.setattr(llm, "ANTHROPIC_MESSAGES_URL",
                        fake_api.base_url + "/v1/messages")
    monkeypatch.setenv("ANTHROPIC_API_KEY", fake_api.good_key)
    fenced_prose = "```\na fenced code sample the script seat must keep\n```"
    fake_api.add_route("/v1/messages", 200, _ant_env(fenced_prose),
                       content_type="application/json")
    resp = llm.chat(llm.LaneRequest(
        cfg=llm.resolve_seat("rank", {"NEWSLENS_LANE_RANK": "api"}),
        prompt="p", temperature=0, max_tokens=10, json_mode=False,
        user_agent="ua", api_key="k"))
    assert resp.content == fenced_prose                # byte-faithful


def test_api_lane_json_mode_shape_invalid_fenced_still_rejected(monkeypatch,
                                                                fake_api):
    """A' + the hard constraint on the API lane end-to-end: a FENCED but
    shape-invalid resolver object (no disclosure) is unwrapped, then the
    validator still rejects BOTH attempts -> AltitudeError. Extraction is not a
    validation bypass on this lane either."""
    monkeypatch.setattr(llm, "ANTHROPIC_MESSAGES_URL",
                        fake_api.base_url + "/v1/messages")
    monkeypatch.setenv("NEWSLENS_LANE_FOLLOW_ALTITUDE", "api")
    monkeypatch.setenv("ANTHROPIC_API_KEY", fake_api.good_key)
    monkeypatch.setattr(fa.time, "sleep", lambda s: None)
    bad = ('```json\n' + json.dumps({"altitude": "entity",
                                     "primary_entity": "VW",
                                     "confidence": "high"}) + '\n```')
    fake_api.add_route("/v1/messages", 200, _ant_env(bad),
                       content_type="application/json")
    sink = []
    with pytest.raises(fa.AltitudeError):
        fa.resolve_altitude(fa.ThreadInput(1, "VW"), cost_sink=sink)
    assert len(sink) == 2                              # both rejected attempts billed


# --------------------------------------------------------------------------
# timeouts: the full map, and which knob each provider uses
# --------------------------------------------------------------------------

def test_timeout_sub_map_is_pinned_exactly():
    """The exact-set roster guard did not grow the new knob; this is its
    timeout_sub_s twin. A silent sub-timeout change on ANY seat breaks here by
    name (api timeout_s pinned alongside, unchanged)."""
    # item C (2026-07-17): writer/analyst joined the subscription lane, so their
    # timeout_sub_s grew from None -> api ceiling + ~300s lane tax (540/900).
    # follow_altitude is the INTERACTIVE exception (fix loop 1 FIX-3): a reader
    # waits, so it degrades a stuck provider fast — 8s api / 12s sub, not the
    # generous batch ceilings.
    sub = {"rank": 300, "analyst": 540, "writer": 900, "editor": 300,
           "script": 300, "synthesis": None, "state": 300,
           "follow_altitude": 12}
    api = {"rank": 90, "analyst": 240, "writer": 600, "editor": 120,
           "script": 120, "synthesis": 120, "state": 60, "follow_altitude": 8}
    assert set(sub) == set(llm.SEATS)
    for name, cfg in llm.SEATS.items():
        assert cfg.timeout_sub_s == sub[name], name
        assert cfg.timeout_s == api[name], name


@pytest.mark.parametrize("seat,expect", [("state", 300), ("follow_altitude", 12)])
def test_subscription_provider_uses_the_sub_knob_per_seat(monkeypatch, seat, expect):
    """The implementer proved rank; state and follow_altitude ride the same
    (timeout_sub_s or timeout_s) selection — per seat, mechanically.
    follow_altitude's sub knob is the short INTERACTIVE 12s (fix loop 1 FIX-3)."""
    captured = {}
    real_run = llm.subprocess.run

    def rec(args, **kw):
        captured["timeout"] = kw.get("timeout")
        return real_run(args, **kw)

    monkeypatch.setattr(llm.subprocess, "run", rec)
    llm.chat(llm.LaneRequest(
        cfg=llm.resolve_seat(seat), prompt="p", temperature=0, max_tokens=10,
        json_mode=True, user_agent="ua", api_key="k"))
    assert captured["timeout"] == expect


def test_api_provider_still_uses_the_api_knob(monkeypatch, fake_api):
    """The anthropic API provider passes cfg.timeout_s (rank: 90), NOT the new
    300s sub knob — api-lane behavior is byte-unchanged by B."""
    captured = {}
    real_urlopen = urllib.request.urlopen

    def rec(req, timeout=None):
        captured["timeout"] = timeout
        return real_urlopen(req, timeout=timeout)

    monkeypatch.setattr(urllib.request, "urlopen", rec)
    monkeypatch.setattr(llm, "ANTHROPIC_MESSAGES_URL",
                        fake_api.base_url + "/v1/messages")
    monkeypatch.setenv("ANTHROPIC_API_KEY", fake_api.good_key)
    fake_api.add_route("/v1/messages", 200, _ant_env('{"ok": 1}'),
                       content_type="application/json")
    llm.chat(llm.LaneRequest(
        cfg=llm.resolve_seat("rank", {"NEWSLENS_LANE_RANK": "api"}),
        prompt="p", temperature=0, max_tokens=10, json_mode=True,
        user_agent="ua", api_key="k"))
    assert captured["timeout"] == 90


# --------------------------------------------------------------------------
# the state flip: transport, cost (both usage paths), gate, override-proofing
# --------------------------------------------------------------------------

def test_state_chat_subscription_fenced_state_json_metered(monkeypatch, tmp_path):
    """The flip DEPENDS on extraction: real `claude -p` fences the state JSON.
    End-to-end through _default_state_chat: fenced {"state": ...} unwraps,
    charged is 0.0, shadow is Haiku-priced from the METERED usage."""
    fenced = 'Here is the updated state:\n```json\n{"state": "S."}\n```'
    monkeypatch.setenv("NEWSLENS_CLAUDE_BIN",
                       str(_make_shim(tmp_path / "s", fenced,
                                      inp=1000, out=500)))
    raw, charged, shadow = memory_core._default_state_chat("k", "prompt")
    assert raw == {"state": "S."}
    assert charged == 0.0
    assert shadow == pytest.approx(0.0035)       # 1000/1e6*1.00 + 500/1e6*5.00


def test_state_chat_subscription_estimated_usage_still_zero_charged(
        monkeypatch, tmp_path):
    """The estimated-usage twin: no CLI usage block -> estimated tokens, still
    $0 charged, shadow > 0 (the state ledger row never vanishes for being
    'free', and never charges on the flat-rate lane)."""
    fenced = '```json\n{"state": "S."}\n```'
    monkeypatch.setenv("NEWSLENS_CLAUDE_BIN",
                       str(_make_shim(tmp_path / "s", fenced, with_usage=False)))
    raw, charged, shadow = memory_core._default_state_chat("k", "prompt")
    assert raw == {"state": "S."}
    assert charged == 0.0
    assert shadow > 0


def test_state_gate_message_names_the_seat_and_the_api_escape(monkeypatch):
    monkeypatch.setenv("NEWSLENS_CLAUDE_BIN", "/nonexistent/claude-fbqa")
    with pytest.raises(llm.LaneUnavailable) as exc:
        llm.check_lane(llm.resolve_seat("state"))
    msg = str(exc.value)
    assert "state" in msg and "NEWSLENS_LANE_STATE=api" in msg


def test_seat_is_openai_cannot_be_fooled_by_env_overrides():
    """A'' correctness spine: provider is fixed per seat — lane/model env
    overrides must never flip the key requirement in either direction."""
    assert llm.seat_is_openai("state", {}) is False
    assert llm.seat_is_openai("state", {"NEWSLENS_LANE_STATE": "api"}) is False
    assert llm.seat_is_openai("state", {"NEWSLENS_MODEL_STATE": "gpt-4o"}) is False
    assert llm.seat_is_openai("state", {"NEWSLENS_LANE": "api"}) is False
    assert llm.seat_is_openai("rank", {"NEWSLENS_LANE_RANK": "api"}) is False
    assert llm.seat_is_openai("synthesis", {}) is True
    assert llm.seat_is_openai(
        "synthesis", {"NEWSLENS_LANE_SYNTHESIS": "subscription"}) is True


def test_GATE_state_preflight_check_lane_ignores_an_armed_fallback(monkeypatch):
    """FOR THE GATE — behavior pin, NOT a ruling. The generate stage-entry
    preflight for the state seat is a RAW check_lane: with the binary dead and
    NEWSLENS_LANE_FALLBACK=api armed, effective_seat('state') FALLS (one labeled
    fall, like editor/script callers get) while the preflight call DIES. The
    implementer preserved FIX-1 semantics deliberately; whether the state
    preflight should honor the armed fall is the gate's posture call. This pin
    exists so the asymmetry is a documented choice, not an accident.

    GATE RULED 2026-07-17: posture confirmed DURABLE — the gate and the call
    path agree (memory_core's state chat is itself bare resolve_seat +
    check_lane); honoring the fall at the stage gate alone would let the run
    proceed into rewrite_state's broad-except, converting loud death into a
    silent stale-moat degrade (the FIX-1 breach class). Manual escape:
    NEWSLENS_LANE_STATE=api. Revisit only via the writer/analyst lane follow-up
    batch if the fallback is ever armed after the state flip's evaluation
    window closes."""
    monkeypatch.setenv("NEWSLENS_CLAUDE_BIN", "/nonexistent/claude-fbqa")
    monkeypatch.setenv("NEWSLENS_LANE_FALLBACK", "api")
    cfg, reason = llm.effective_seat("state")
    assert cfg.lane == "api" and reason == "subscription_unavailable"  # the seam falls
    with pytest.raises(llm.LaneUnavailable):
        llm.check_lane(llm.resolve_seat("state"))       # the preflight does not


# --------------------------------------------------------------------------
# A'' positive arms: the guards FIRE when a seat genuinely resolves openai
# --------------------------------------------------------------------------

def test_rank_guard_fires_when_rank_resolves_openai(monkeypatch):
    """Wiring-proof for the rank guard's positive arm (the flipped test only
    proves the old refusal is gone). Pin the rank seat back to gpt-4o/openai:
    keyless must die RankingError naming OPENAI_API_KEY — BEFORE any DB is
    opened (the sandbox DB file must not even exist afterwards)."""
    monkeypatch.setitem(
        llm.SEATS, "rank",
        dataclasses.replace(llm.SEATS["rank"], **_GPT4O_STATE_ROW))
    cfg = config.SourcesConfig(
        sources=[config.Source(name="O1", rss_url="https://o1.example/f")],
        interests_broad=["economy"], interests_granular=["AI regulation"])
    with pytest.raises(ranking.RankingError, match="OPENAI_API_KEY"):
        ranking.run_rank(date="2026-07-01", con=None, cfg=cfg, env={})
    assert not paths.DB_PATH.exists()                  # refused before any DB work


def test_analysis_guard_fires_when_analyst_resolves_openai(monkeypatch):
    monkeypatch.setitem(
        llm.SEATS, "analyst",
        dataclasses.replace(llm.SEATS["analyst"], **_GPT4O_STATE_ROW))
    with pytest.raises(RuntimeError, match="OPENAI_API_KEY"):
        analysis.run_analysis(env={})


# --------------------------------------------------------------------------
# doctor: check_openai_key's three new branches (previously zero coverage)
# --------------------------------------------------------------------------

def test_doctor_keyless_renders_info_naming_the_dormant_seat(no_network,
                                                             monkeypatch):
    """GATE RULED 2026-07-17 (ruling 2): a DORMANT seat (declared, no live call
    site — llm.DORMANT_SEATS) must NOT force the key requirement; there is no
    run for the key to protect. Keyless under the shipped table renders INFO,
    names synthesis as declared-dormant, zero network. The FAIL arm stays live:
    patch a LIVE seat back to openai and keyless FAILs naming it (wiring proof
    both directions). B6 re-arms the requirement by removing synthesis from
    DORMANT_SEATS when its call site lands."""
    results = doctor.check_openai_key({})
    assert len(results) == 1 and results[0].status == doctor.INFO
    assert "not needed" in results[0].text
    assert "synthesis" in results[0].text              # the dormant seat is named
    assert no_network == []                            # probe-free
    # The FAIL arm, unchanged for LIVE openai seats:
    monkeypatch.setitem(
        llm.SEATS, "state",
        dataclasses.replace(llm.SEATS["state"], **_GPT4O_STATE_ROW))
    live = doctor.check_openai_key({})
    assert live[0].status == doctor.FAIL and "state" in live[0].text
    assert no_network == []                            # still no probe without a key


def test_doctor_key_branches_when_no_seat_routes_openai(monkeypatch, no_network):
    """The INFO branches are live code: with synthesis patched anthropic (no
    openai seat anywhere), keyless renders 'not needed' and a set key renders
    'unused' — both without any network probe."""
    monkeypatch.setitem(
        llm.SEATS, "synthesis",
        dataclasses.replace(llm.SEATS["synthesis"], provider="anthropic",
                            model="claude-haiku-4-5", lane="subscription"))
    keyless = doctor.check_openai_key({})
    assert keyless[0].status == doctor.INFO and "not needed" in keyless[0].text
    keyed = doctor.check_openai_key({"OPENAI_API_KEY": "sk-x"})
    assert keyed[0].status == doctor.INFO and "unused" in keyed[0].text
    assert no_network == []


def test_doctor_valid_key_pass_names_the_powered_seats(monkeypatch, fake_api):
    """Post-ruling-2: PASS requires a LIVE openai seat (a set key with only the
    dormant synthesis renders INFO-unused, probe-free — pinned in the keyless
    test's sibling). Patch state live-openai; the probe fires and PASS names
    the powered seat."""
    monkeypatch.setitem(
        llm.SEATS, "state",
        dataclasses.replace(llm.SEATS["state"], **_GPT4O_STATE_ROW))
    monkeypatch.setattr(doctor, "OPENAI_MODELS_URL",
                        fake_api.base_url + "/v1/models")
    results = doctor.check_openai_key({"OPENAI_API_KEY": fake_api.good_key})
    assert results[0].status == doctor.PASS
    assert "state" in results[0].text


# --------------------------------------------------------------------------
# records correctness
# --------------------------------------------------------------------------

def test_no_stale_pointer_to_a_nonexistent_keyless_generate_test():
    """BORN RED (QA defect pin, 2026-07-17 second pass). test_generate.py's
    keyless-CLI docstring points readers at
    'test_keyless_openai_generate_refuses_at_the_state_stage' — no test of that
    name exists anywhere in the tree. The state-stage refusal proof actually
    lives in test_p3_script.py (28a, state pinned back to openai) and the
    completes-keyless proof is
    test_keyless_openai_generate_completes_end_to_end_after_the_state_flip.

    FIX CONTRACT (flips this green): correct the pointer in test_generate.py to
    the real test name(s). Records fix only; no behavior change. Same class as
    the NL-17-M1a stale-comment defect — a wrong pointer is the next reader's
    copy source."""
    tests_dir = Path(__file__).parent
    stale = "test_keyless_openai_generate_refuses_at_the_state_stage"
    hits = {
        p.name for p in tests_dir.glob("test_*.py")
        if stale in p.read_text(encoding="utf-8") and p.name != Path(__file__).name
    }
    # the name must exist as a REAL test somewhere if it is referenced at all
    defining = {
        p.name for p in tests_dir.glob("test_*.py")
        if ("def " + stale) in p.read_text(encoding="utf-8")
    }
    assert not (hits and not defining), (
        f"stale pointer: {sorted(hits)} reference '{stale}' but no file defines "
        "it — fix the docstring pointer in test_generate.py")
