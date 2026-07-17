"""B1 provider-seam QA — adversarial contract suite (QA-owned).

Contract under test (B1 dispatch, ADR-0014, engineering-3 §5.1/§5.6):
  C1 zero behavior change — byte-faithful requests per seat
  C2 signature-preserving wrappers
  C3 fail-loud: unimplemented/unknown lane NEVER reaches a real transport
  C4 NEWSLENS_LANE_FALLBACK=api is the only fallback, explicit opt-in
  C5 shadow ledger additive; legacy `usd` never displaced/altered
  C6 llm.py is a leaf
  C7 doctor "LLM lanes" renders + fails loud

RED acceptance contracts in this file (written failing on 2026-07-16, both
CONSCIOUSLY FLIPPED GREEN by the D1/D2 fix loop the same day; docstrings
carry the fix contracts they gated):
  * test_per_seat_lane_override_on_generate_steps_fails_loud  (D1)
  * test_conftest_scrubs_the_b1_lane_env_vars                 (D2)
The "post-fix sweep" section pins the fixed surface adversarially: no
combination of lane env vars may reach a real transport with a mismatched
ledger row, and config errors surface as LaneUnavailable immediately (no
wrap, no retry, no sleep) so they can never be swallowed by the pipeline's
GenerateError/OSError degrade arms.
"""

from __future__ import annotations

import ast
import inspect
import json
import sys
import time
import urllib.request
from pathlib import Path

import pytest

from newslens import analysis, doctor, generate, llm, ranking

# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

_CANNED = {
    "choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}],
    "usage": {"prompt_tokens": 1000, "completion_tokens": 200},
}


class _Resp:
    def __init__(self, payload):
        self._b = json.dumps(payload).encode()

    def read(self):
        return self._b

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _capture(monkeypatch, payload=None):
    seen = {}

    def fake_urlopen(req, timeout=None):
        seen["req"] = req
        seen["data"] = req.data
        seen["url"] = req.full_url
        seen["timeout"] = timeout
        return _Resp(payload or _CANNED)

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    return seen


def _transport_tripwire(monkeypatch):
    """Any urlopen call = the fail-loud contract already lost. Records
    attempts so assertions can show count==0 explicitly."""
    calls = []

    def tripwire(req, timeout=None):
        calls.append(req.full_url)
        raise AssertionError(
            "REAL TRANSPORT REACHED — fail-loud bypassed (C3): " + req.full_url
        )

    monkeypatch.setattr(urllib.request, "urlopen", tripwire)
    return calls


def _hdr(req, name):
    for k, v in req.header_items():
        if k.lower() == name.lower():
            return v
    return None


def _no_sleep(monkeypatch):
    monkeypatch.setattr(time, "sleep", lambda s: None)


# ---------------------------------------------------------------------------
# C1 — byte-faithful requests. Not field-by-field: the EXACT bytes the
# pre-seam code produced (same dict insertion order, int-0 temperature for
# rank, response_format present/absent per caller).
# ---------------------------------------------------------------------------

def test_rank_request_bytes_are_the_anthropic_messages_shape(monkeypatch):
    # B2: rank rides the Claude API lane. The request is the anthropic Messages
    # body (max_tokens REQUIRED; a `system` nudge stands in for json_object mode;
    # temperature preserved as int 0 by the exact-copy law; no thinking/effort on
    # a mechanical Haiku seat), authenticated with x-api-key (the lane's own
    # credential), POSTed to the anthropic endpoint.
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-qa")
    seen = _capture(monkeypatch)
    ranking._post_chat("sk-qa", "PROMPT-R")
    expected = json.dumps({
        "model": "claude-haiku-4-5",
        "max_tokens": ranking.MAX_COMPLETION_TOKENS,
        "messages": [{"role": "user", "content": "PROMPT-R"}],
        "temperature": 0,                     # int 0, not 0.0 (exact-copy law)
        "system": llm._ANTHROPIC_JSON_SYSTEM,
    }).encode("utf-8")
    assert seen["data"] == expected
    assert seen["url"] == llm.ANTHROPIC_MESSAGES_URL
    assert seen["timeout"] == 90
    assert _hdr(seen["req"], "x-api-key") == "sk-ant-qa"
    assert _hdr(seen["req"], "anthropic-version") == llm.ANTHROPIC_VERSION
    assert _hdr(seen["req"], "Content-Type") == "application/json"
    assert _hdr(seen["req"], "User-Agent") == ranking.USER_AGENT


def test_writer_request_bytes_identical_json_mode_on(monkeypatch):
    seen = _capture(monkeypatch)
    generate._chat("sk-qa", "PROMPT-W", 333, 0.7, True)
    expected = json.dumps({
        "model": "gpt-4o",
        "messages": [{"role": "user", "content": "PROMPT-W"}],
        "temperature": 0.7,
        "max_tokens": 333,
        "response_format": {"type": "json_object"},
    }).encode("utf-8")
    assert seen["data"] == expected
    assert seen["url"] == ranking.OPENAI_CHAT_URL   # historical offline seam
    assert seen["timeout"] == 120
    assert _hdr(seen["req"], "Authorization") == "Bearer sk-qa"
    assert _hdr(seen["req"], "User-Agent") == generate.WRITER_UA


def test_writer_request_bytes_identical_json_mode_off(monkeypatch):
    """response_format OMITTED entirely when json_mode is off (the writer
    script path never sent it; its presence would be a behavior change)."""
    seen = _capture(monkeypatch)
    generate._chat("sk-qa", "PROMPT-S", 512, 0.4, False)
    expected = json.dumps({
        "model": "gpt-4o",
        "messages": [{"role": "user", "content": "PROMPT-S"}],
        "temperature": 0.4,
        "max_tokens": 512,
    }).encode("utf-8")
    assert seen["data"] == expected
    assert b"response_format" not in seen["data"]


def test_analysis_request_bytes_identical_and_historical_url(monkeypatch):
    seen = _capture(monkeypatch)
    analysis._analysis_chat("sk-qa", "PROMPT-A")
    expected = json.dumps({
        "model": "gpt-4o",
        "messages": [{"role": "user", "content": "PROMPT-A"}],
        "temperature": 0.2,
        "max_tokens": analysis.ANALYSIS_MAX_TOKENS,
        "response_format": {"type": "json_object"},
    }).encode("utf-8")
    assert seen["data"] == expected
    # analysis historically POSTed the inline literal; the seam default must
    # be the same string.
    assert seen["url"] == "https://api.openai.com/v1/chat/completions"
    assert llm.OPENAI_CHAT_URL == ranking.OPENAI_CHAT_URL == seen["url"]
    assert seen["timeout"] == 90
    assert _hdr(seen["req"], "User-Agent") == analysis.ANALYSIS_UA


# ---------------------------------------------------------------------------
# C2 — signatures of the suite's monkeypatch targets and their wrappers
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("fn,expected", [
    (generate._chat,
     "(key: 'str', prompt: 'str', max_tokens: 'int', temperature: 'float', "
     "json_mode: 'bool') -> 'Dict'"),
    (ranking._post_chat, "(key: 'str', prompt: 'str') -> 'Dict'"),
    (analysis._analysis_chat, "(key: 'str', prompt: 'str') -> 'Dict'"),
    (analysis.call_analysis_model,
     "(key: 'str', prompt: 'str') -> 'Tuple[Dict, float]'"),
])
def test_signatures_preserved(fn, expected):
    assert str(inspect.signature(fn)) == expected


def test_call_llm_and_call_llm_validated_signatures_preserved():
    assert str(inspect.signature(generate.call_llm)) == (
        "(key: 'str', prompt: 'str', step: 'str', max_tokens: 'int', "
        "temperature: 'float', json_mode: 'bool', validate=None, "
        "cost_sink: 'Optional[List[Dict]]' = None) -> 'Tuple[str, Dict]'"
    )
    assert str(inspect.signature(ranking.call_llm_validated)) == (
        "(key: 'str', prompt: 'str', known_ids: 'set', "
        "tag_levels: 'Dict[str, str]', memory_topics: 'List[str]', "
        "repairs: 'Optional[Dict]' = None, "
        "dormant_topics: 'Optional[List[str]]' = None, "
        "cost_sink: 'Optional[List[Dict]]' = None) -> 'Tuple[List[Dict], Dict]'"
    )


# ---------------------------------------------------------------------------
# C3 — the fail-loud matrix. Every raising row must (a) raise before ANY
# transport attempt and (b) name the fix.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("env,seat,expect_lane", [
    ({}, "rank", "api"),                                     # default
    ({"NEWSLENS_LANE": "api"}, "rank", "api"),               # explicit default
    ({"NEWSLENS_LANE": " api "}, "rank", "api"),             # whitespace strip
    # empty string == unset (dotenv `NEWSLENS_LANE=` line): documented
    # convention, NOT an unknown lane.
    ({"NEWSLENS_LANE": ""}, "rank", "api"),
    ({"NEWSLENS_LANE_RANK": ""}, "rank", "api"),
    # per-seat api pin works with or without a global
    ({"NEWSLENS_LANE": "api", "NEWSLENS_LANE_RANK": "api"}, "rank", "api"),
])
def test_lane_matrix_rows_that_stay_on_the_api_default(env, seat, expect_lane):
    cfg = llm.resolve_seat(seat, env)
    assert cfg.lane == expect_lane
    assert cfg == llm.SEATS[seat]           # full config identical, not just lane


@pytest.mark.parametrize("env,seat", [
    ({"NEWSLENS_LANE": "subscription"}, "rank"),
    ({"NEWSLENS_LANE": "claude"}, "rank"),          # unknown string
    ({"NEWSLENS_LANE": "API"}, "rank"),             # case-sensitive: unknown
    ({"NEWSLENS_LANE": "   "}, "rank"),             # whitespace-only -> "" lane
    ({"NEWSLENS_LANE_RANK": "subscription"}, "rank"),
    ({"NEWSLENS_LANE_RANK": "subscription", "NEWSLENS_LANE": "api"}, "rank"),
    # empty per-seat does NOT rescue a bad global (empty == unset)
    ({"NEWSLENS_LANE": "subscription", "NEWSLENS_LANE_RANK": ""}, "rank"),
    ({"NEWSLENS_LANE_ANALYST": "subscription"}, "analyst"),
    ({"NEWSLENS_LANE_WRITER": "nonsense"}, "writer"),
])
def test_lane_matrix_rows_that_fail_loud_before_any_transport(
        monkeypatch, env, seat):
    calls = _transport_tripwire(monkeypatch)
    cfg = llm.resolve_seat(seat, env)
    with pytest.raises(llm.LaneUnavailable) as exc:
        llm.chat(llm.LaneRequest(cfg, "p", 0, 10, True, "ua", "k"))
    assert "NEWSLENS_LANE" in str(exc.value)        # names the fix
    assert calls == []                              # never reached a socket


def test_per_seat_override_beats_global_and_only_that_seat(monkeypatch):
    env = {"NEWSLENS_LANE": "api", "NEWSLENS_LANE_RANK": "subscription"}
    assert llm.resolve_seat("rank", env).lane == "subscription"
    for other in ("analyst", "writer", "editor", "script", "synthesis"):
        assert llm.resolve_seat(other, env).lane == "api"


@pytest.mark.parametrize("caller", ["generate", "ranking", "analysis"])
def test_runtime_env_lane_fails_loud_through_all_three_wrappers(
        monkeypatch, caller):
    """The wrappers read os.environ at call time; a bad global lane must
    surface from every historical entrypoint as RAW LaneUnavailable —
    immediately (check_lane preflight), unwrapped, zero retries, zero
    sleeps, zero transport calls, zero ledger rows.

    The unwrapped class is load-bearing, not cosmetic: GenerateError is
    caught by the pipeline's degrade seams (e.g. generate.py editor arm
    'DEGRADED to unedited draft'), so wrapping a lane config error would
    let a briefing ship with the misconfiguration masked. LaneUnavailable
    escapes every degrade arm and dies at the CLI boundary naming the fix.
    """
    sleeps = []
    monkeypatch.setattr(time, "sleep", lambda s: sleeps.append(s))
    calls = _transport_tripwire(monkeypatch)
    monkeypatch.setenv("NEWSLENS_LANE", "subscription")
    sink = []
    if caller == "generate":
        raiser = lambda: generate.call_llm(
            "k", "p", "narrative", 100, 0.5, False, cost_sink=sink)
    elif caller == "ranking":
        raiser = lambda: ranking.call_llm_validated(
            "k", "p", {1}, {}, [], cost_sink=sink)
    else:
        raiser = lambda: analysis.call_analysis_model("k", "p")
    with pytest.raises(llm.LaneUnavailable) as exc:
        raiser()
    msg = str(exc.value)
    assert "no registered implementation" in msg
    assert "NEWSLENS_LANE" in msg
    assert calls == []
    assert sleeps == []                     # no pointless retry+sleep
    assert sink == []                       # no ledger row for a blocked call


def test_lane_unavailable_escapes_the_degrade_arms_structurally():
    """The pipeline degrades on (GenerateError, OSError) and the rank stage
    on RankingError; a lane config error must never be degradable. Pin the
    class relationships that guarantee it."""
    assert not issubclass(llm.LaneUnavailable, generate.GenerateError)
    assert not issubclass(llm.LaneUnavailable, ranking.RankingError)
    assert not issubclass(llm.LaneUnavailable, OSError)
    assert issubclass(llm.LaneUnavailable, RuntimeError)


# --- D1 RED — the acceptance contract for the per-seat editor/script hole --

@pytest.mark.parametrize("step,var,lane", [
    ("editor", "NEWSLENS_LANE_EDITOR", "subscription"),
    ("script", "NEWSLENS_LANE_SCRIPT", "subscription"),
    ("script_retry", "NEWSLENS_LANE_SCRIPT", "subscriptoin"),  # typo lane
])
def test_per_seat_lane_override_on_generate_steps_fails_loud(
        monkeypatch, step, var, lane):
    """Was RED (D1) — acceptance contract, written failing 2026-07-16;
    CONSCIOUSLY FLIPPED GREEN by the same-day fix (seat_cfg single
    resolution + llm.check_lane preflight in call_llm).

    The hole it pinned: generate's TRANSPORT resolved the 'writer' seat
    while the ledger resolved seat_for_step(step) ('editor'/'script'). With
    NEWSLENS_LANE_EDITOR=subscription an editor step therefore MADE THE REAL
    gpt-4o API CALL (charged) and wrote a ledger row claiming
    lane='subscription', usd_charged=0.0 — a C3 fail-loud bypass and a C5
    shadow-ledger corruption in one (proven live 2026-07-16: 1 transport
    call, entry {'lane': 'subscription', 'usd': 0.0045, 'usd_charged': 0.0}).

    FIX CONTRACT: one resolution per step, transport-seat == ledger-seat, so
    a per-seat override on ANY seat with a live generate call site either
    routes that seat's lane or dies loud naming the fix — never a silent
    wrong-lane call (DECISIONS.md 2026-07-16), never a ledger row whose lane
    differs from the lane that carried the bytes. Signature-preserving
    options: resolve seat_for_step(step) once in call_llm and fail loud via
    llm._select_provider before transport, or thread the seat through _chat
    keyword-defaulted. Suppressing per-seat env for generate steps is NOT
    acceptable (that is the silent wrong-lane call by construction).
    This test must pass with the fix and the three
    test_lane_matrix_rows_that_stay_on_the_api_default rows must stay green
    (default env stays byte-identical).
    """
    _no_sleep(monkeypatch)
    calls = _transport_tripwire(monkeypatch)
    monkeypatch.setenv(var, lane)
    sink = []
    with pytest.raises(Exception) as exc:
        generate.call_llm("k", "p", step, 100, 0.5, False, cost_sink=sink)
    assert "no registered implementation" in str(exc.value)
    assert calls == [], (
        f"step {step!r} under {var}={lane} reached the real transport "
        f"{len(calls)} time(s) — charged call misfiled as lane={lane!r}"
    )


def test_generate_steps_default_env_transport_and_ledger_agree(monkeypatch):
    """The D1 close under B2: with NO lane env, each step's ledger row names the
    seat that actually rode the wire — narrative on gpt-4o, editor/script on the
    Claude API Haiku seat — all on the api lane, charged == shadow == legacy usd.
    The ledger no longer forks the model that ran from the price it records."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-fake")
    _capture(monkeypatch)
    expect_model = {"narrative": "gpt-4o", "narrative_retry": "gpt-4o",
                    "editor": "claude-haiku-4-5", "script": "claude-haiku-4-5",
                    "script_retry": "claude-haiku-4-5"}
    for step in ("narrative", "narrative_retry", "editor", "script",
                 "script_retry"):
        sink = []
        generate.call_llm("k", "p", step, 100, 0.5, False, cost_sink=sink)
        e = sink[0]
        assert e["lane"] == "api" and e["model"] == expect_model[step], step
        assert e["usd"] == e["usd_shadow"] == e["usd_charged"], step


# ---------------------------------------------------------------------------
# C4 — fallback flag: explicit opt-in, reads-and-reports only in B1
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("value,armed", [
    ("api", True),
    ("API", True),          # lenient input parse on the one documented value
    (" api ", True),
    ("none", False),
    ("", False),
    ("1", False),           # truthiness does NOT arm
    ("true", False),
    ("yes", False),
    ("subscription", False),
])
def test_fallback_flag_arms_only_on_the_documented_value(value, armed):
    assert llm.fallback_armed({"NEWSLENS_LANE_FALLBACK": value}) is armed


def test_fallback_unset_is_off():
    assert llm.fallback_armed({}) is False


def test_armed_fallback_does_not_change_b1_chat_outcome(monkeypatch):
    """CONSCIOUS B1 STATE (ADR-0014 §4): the flag is read/reported only; the
    fall itself needs a second lane. Armed + unavailable lane still dies
    loud with zero transport. B2/B3 flips this test DELIBERATELY when the
    fall is implemented (it must then assert the one-fall + stderr warning +
    lane='api(fallback:<reason>)' ledger law instead)."""
    calls = _transport_tripwire(monkeypatch)
    monkeypatch.setenv("NEWSLENS_LANE_FALLBACK", "api")
    cfg = llm.resolve_seat("rank", {"NEWSLENS_LANE": "subscription"})
    with pytest.raises(llm.LaneUnavailable):
        llm.chat(llm.LaneRequest(cfg, "p", 0, 10, True, "ua", "k"))
    assert calls == []


# ---------------------------------------------------------------------------
# C5 — shadow ledger: parity with the legacy formulas, additive keys only
# ---------------------------------------------------------------------------

_TOKEN_SWEEP = [
    (0, 0), (1, 0), (0, 1), (1, 1), (3, 7), (7, 3), (101, 103),
    (1999, 3), (123456, 654321), (333333, 777777), (999999, 1),
    (1, 999999), (1_000_000, 200_000), (2_500_000, 41), (49, 1_000_003),
]


@pytest.mark.parametrize("pt,ct", _TOKEN_SWEEP)
def test_usd_shadow_float_identical_to_both_legacy_formulas(pt, ct):
    usage = {"prompt_tokens": pt, "completion_tokens": ct}
    shadow_rank = llm.cost_fields(llm.SEATS["rank"], usage)["usd_shadow"]
    shadow_writer = llm.cost_fields(llm.SEATS["writer"], usage)["usd_shadow"]
    assert shadow_rank == round(ranking.usage_to_usd(usage), 6)      # not approx
    assert shadow_writer == round(generate._step_cost(usage), 6)     # not approx


def test_cost_fields_exact_key_set_and_charged_semantics():
    fields = llm.cost_fields(llm.SEATS["rank"], {"prompt_tokens": 10,
                                                 "completion_tokens": 10})
    assert set(fields) == {"model", "lane", "cache_read_tokens",
                           "cache_creation_tokens",  # B2: recorded for B4 caching
                           "usd_shadow", "usd_charged"}
    assert "usd" not in fields                 # can never displace legacy key
    assert fields["usd_charged"] == fields["usd_shadow"]     # api lane


def test_cache_read_tokens_recorded_but_never_discounted():
    plain = {"prompt_tokens": 10_000, "completion_tokens": 500}
    cached = dict(plain, prompt_tokens_details={"cached_tokens": 9_000})
    f_plain = llm.cost_fields(llm.SEATS["writer"], plain)
    f_cached = llm.cost_fields(llm.SEATS["writer"], cached)
    assert f_cached["cache_read_tokens"] == 9_000
    assert f_plain["cache_read_tokens"] == 0
    assert f_cached["usd_shadow"] == f_plain["usd_shadow"]     # B1: no discount
    assert f_cached["usd_charged"] == f_plain["usd_charged"]


@pytest.mark.parametrize("usage", [
    None,
    {},
    {"prompt_tokens": None, "completion_tokens": None},
    {"prompt_tokens": 5, "completion_tokens": 5, "prompt_tokens_details": None},
    {"prompt_tokens": 5, "completion_tokens": 5,
     "prompt_tokens_details": ["not", "a", "dict"]},
    {"prompt_tokens": 5, "completion_tokens": 5,
     "prompt_tokens_details": {"cached_tokens": None}},
])
def test_cost_fields_never_crashes_on_degenerate_usage(usage):
    fields = llm.cost_fields(llm.SEATS["rank"], usage)
    assert fields["cache_read_tokens"] == 0
    assert fields["usd_shadow"] >= 0.0


def test_rank_sink_entry_full_shape_legacy_usd_untouched(monkeypatch):
    good = {
        "choices": [{"message": {"content": json.dumps(
            {"clusters": [{"story_title": "T", "summary": "S",
                           "item_ids": [1], "matched_tags": [],
                           "matched_memory": [], "world_impact": 5,
                           "world_impact_reason": "r"}]})},
            "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 1234, "completion_tokens": 567,
                  "prompt_tokens_details": {"cached_tokens": 1000}},
    }
    monkeypatch.setattr(ranking, "_post_chat", lambda key, prompt: good)
    sink = []
    ranking.call_llm_validated("k", "p", {1}, {}, [], cost_sink=sink)
    e = sink[0]
    assert set(e) == {"step", "attempt", "prompt_tokens", "completion_tokens",
                      "usd", "model", "lane", "cache_read_tokens",
                      "cache_creation_tokens", "usd_shadow", "usd_charged"}
    assert e["step"] == "rank_select" and e["attempt"] == 1
    # legacy `usd` == usd_charged, both from the (now Haiku) rank seat's prices
    assert e["usd"] == round(ranking.usage_to_usd(good["usage"]), 6)
    assert e["usd"] == e["usd_shadow"] == e["usd_charged"]
    assert e["model"] == "claude-haiku-4-5" and e["lane"] == "api"
    assert e["cache_read_tokens"] == 1000
    assert e["prompt_tokens"] == 1234 and e["completion_tokens"] == 567


def test_generate_sink_entry_full_shape_legacy_usd_untouched(monkeypatch):
    resp = {"choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 4321, "completion_tokens": 765}}
    monkeypatch.setattr(generate, "_chat",
                        lambda key, prompt, mt, temp, jm: resp)
    sink = []
    generate.call_llm("k", "p", "narrative", 100, 0.5, False, cost_sink=sink)
    e = sink[0]
    assert set(e) == {"step", "attempt", "prompt_tokens", "completion_tokens",
                      "usd", "model", "lane", "cache_read_tokens",
                      "cache_creation_tokens", "usd_shadow", "usd_charged"}
    # narrative rides the writer seat (still gpt-4o), so legacy `usd` == the
    # writer-rate _step_cost == usd_charged == usd_shadow.
    assert e["usd"] == round(generate._step_cost(resp["usage"]), 6)
    assert e["usd"] == e["usd_shadow"] == e["usd_charged"]
    assert e["model"] == "gpt-4o" and e["lane"] == "api"


# ---------------------------------------------------------------------------
# C1/C3 guard — the seat table is the current stack, exactly
# ---------------------------------------------------------------------------

def test_seat_table_pins_the_b2_stack_exactly():
    # B2: rank/editor/script -> anthropic/claude-haiku-4-5 ($1.00/$5.00); the
    # rest stay openai/gpt-4o ($2.50/$10.00); the state seat joined (R1). Every
    # seat is api-lane; timeouts are unchanged from B1. thinking/effort stay None
    # everywhere (Haiku seats are mechanical; no thinking param sent).
    assert set(llm.SEATS) == {"rank", "analyst", "writer", "editor", "script",
                              "synthesis", "state"}
    haiku = {"rank", "editor", "script"}
    timeouts = {"rank": 90, "analyst": 90, "writer": 120, "editor": 120,
                "script": 120, "synthesis": 120, "state": 60}
    for name, cfg in llm.SEATS.items():
        assert cfg.seat == name
        assert cfg.lane == "api"
        assert cfg.timeout_s == timeouts[name]
        assert cfg.thinking is None and cfg.effort is None
        if name in haiku:
            assert cfg.provider == "anthropic"
            assert cfg.model == "claude-haiku-4-5"
            assert cfg.usd_per_mtok_in == 1.00
            assert cfg.usd_per_mtok_out == 5.00
        else:
            assert cfg.provider == "openai"
            assert cfg.model == "gpt-4o"
            assert cfg.usd_per_mtok_in == 2.50
            assert cfg.usd_per_mtok_out == 10.00


def test_seat_for_step_covers_every_live_generate_step():
    # the five step strings generate.call_llm is actually called with today
    assert llm.seat_for_step("narrative") == "writer"
    assert llm.seat_for_step("narrative_retry") == "writer"
    assert llm.seat_for_step("editor") == "editor"
    assert llm.seat_for_step("script") == "script"
    assert llm.seat_for_step("script_retry") == "script"
    assert llm.seat_for_step("rank_select") == "rank"
    # unknown steps default to writer — value-neutral in B1 (identical seat),
    # revisit at B4 when the writer family forks models.
    assert llm.seat_for_step("mystery_step") == "writer"


# ---------------------------------------------------------------------------
# C6 — leaf module: source-level and import-liveness
# ---------------------------------------------------------------------------

def test_llm_module_source_imports_only_stdlib():
    src = Path(inspect.getsourcefile(llm)).read_text(encoding="utf-8")
    tree = ast.parse(src)
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:                      # any relative import at all
                imported.update(f".{a.name}" for a in node.names)
            else:
                imported.add(node.module or "")
    allowed = {"json", "os", "urllib.error", "urllib.request", "dataclasses",
               "typing", "__future__"}
    assert imported <= allowed, f"non-leaf imports: {imported - allowed}"


def test_importing_llm_does_not_pull_in_the_callers():
    """Liveness form: a fresh interpreter importing newslens.llm must not
    load generate/ranking/analysis/memory_core as a side effect."""
    import subprocess
    code = (
        "import sys; import newslens.llm; "
        "bad = [m for m in ('newslens.generate','newslens.ranking',"
        "'newslens.analysis','newslens.memory_core') if m in sys.modules]; "
        "sys.exit(1 if bad else 0)"
    )
    proc = subprocess.run([sys.executable, "-c", code], capture_output=True)
    assert proc.returncode == 0, proc.stderr.decode()


# ---------------------------------------------------------------------------
# C7 — doctor "LLM lanes" section (unit level; the CLI child render was
# attested in the QA pass evidence)
# ---------------------------------------------------------------------------

def test_doctor_lanes_default_env_renders_all_seats_no_fail():
    results = doctor.check_llm_lanes({})
    assert len(results) == len(llm.SEATS) + 2       # seats + fallback + lane note
    assert all(r.status == doctor.INFO for r in results)  # every seat on an api lane
    seat_lines = [r.text for r in results[:len(llm.SEATS)]]
    # B2: rank/editor/script render the Claude lane on Haiku; the rest gpt-4o.
    for name, cfg in llm.SEATS.items():
        assert any(
            line.startswith(f"{name}: {cfg.provider}/{cfg.model} · lane=api")
            for line in seat_lines), name
    assert "fallback unarmed" in results[len(llm.SEATS)].text


def test_doctor_lanes_bad_lane_fails_every_seat_naming_the_fix():
    results = doctor.check_llm_lanes({"NEWSLENS_LANE": "subscription"})
    fails = [r for r in results if r.status == doctor.FAIL]
    assert len(fails) == len(llm.SEATS)
    for r in fails:
        assert "no registered implementation" in r.text
        assert "NEWSLENS_LANE" in r.text


def test_doctor_lanes_armed_fallback_warns():
    results = doctor.check_llm_lanes({"NEWSLENS_LANE_FALLBACK": "api"})
    warns = [r for r in results if r.status == doctor.WARN]
    assert len(warns) == 1 and "ARMED" in warns[0].text


def test_doctor_lanes_probe_makes_no_transport_call(monkeypatch):
    calls = _transport_tripwire(monkeypatch)
    doctor.check_llm_lanes({})
    doctor.check_llm_lanes({"NEWSLENS_LANE": "subscription"})
    assert calls == []


# ---------------------------------------------------------------------------
# D2 RED — suite hermeticity for the B1 env surface
# ---------------------------------------------------------------------------

def test_conftest_scrubs_the_b1_lane_env_vars():
    """Was RED (D2) — acceptance contract, written failing 2026-07-16;
    CONSCIOUSLY FLIPPED GREEN by the same-day conftest extension.

    conftest's own law: SCRUBBED_ENV_VARS holds 'every env var the
    milestone-1 code reads'. B1 added env reads (llm.resolve_seat /
    llm.fallback_armed read os.environ at call time) without extending the
    scrub, so an ambient shell export leaked into every test that exercises
    the real transport path. PROVEN TO BITE pre-fix: `NEWSLENS_LANE=
    subscription pytest tests/test_b1_llm_seam.py` failed 6 of the
    implementer's own 14 tests (2026-07-16 QA run).

    FIX CONTRACT: add NEWSLENS_LANE, NEWSLENS_LANE_FALLBACK, the six
    per-seat NEWSLENS_LANE_<SEAT> names, and ANTHROPIC_API_KEY (unread in
    B1, credential-shaped — scrub before B2 makes it live) to
    SCRUBBED_ENV_VARS. This test then passes and the ambient-export rerun
    above goes green.
    """
    conftest_mod = None
    for mod in sys.modules.values():
        f = getattr(mod, "__file__", None)
        if f and Path(f).name == "conftest.py" and \
                Path(f).parent == Path(__file__).parent:
            conftest_mod = mod
            break
    assert conftest_mod is not None, "tests/conftest.py module not found"
    scrubbed = set(conftest_mod.SCRUBBED_ENV_VARS)
    required = {"NEWSLENS_LANE", "NEWSLENS_LANE_FALLBACK", "ANTHROPIC_API_KEY"}
    required |= {f"NEWSLENS_LANE_{s.upper()}" for s in llm.SEATS}
    missing = required - scrubbed
    assert not missing, (
        f"B1 env vars not scrubbed (ambient exports leak into the suite): "
        f"{sorted(missing)}"
    )


# ---------------------------------------------------------------------------
# POST-FIX SWEEP (D1 close verification, 2026-07-16 fix loop) — no
# combination of lane env vars may reach a real transport with a mismatched
# ledger row. The B4 residual (_chat transports on the frozen 'writer'
# resolution while the gate/ledger use seat_for_step) must be FAIL-SAFE:
# over-loud is acceptable, silent mis-laning is not.
# ---------------------------------------------------------------------------

_RANK_OK_CONTENT = json.dumps(
    {"clusters": [{"story_title": "T", "summary": "S", "item_ids": [1],
                   "matched_tags": [], "matched_memory": [],
                   "world_impact": 5, "world_impact_reason": "r"}]})
_SWEEP_PAYLOAD = {
    "choices": [{"message": {"content": _RANK_OK_CONTENT},
                 "finish_reason": "stop"}],
    "usage": {"prompt_tokens": 1000, "completion_tokens": 200},
}
# B2: the Claude API lane returns an anthropic-SHAPED body; the anthropic
# provider synthesises the OpenAI shape callers parse. The sweep now exercises
# real anthropic seats (rank/editor/script), so the fake transport must serve
# the shape that MATCHES the endpoint the provider POSTs to.
_ANTHROPIC_SWEEP_PAYLOAD = {
    "id": "msg_sweep", "type": "message", "role": "assistant",
    "model": "claude-haiku-4-5",
    "content": [{"type": "text", "text": _RANK_OK_CONTENT}],
    "stop_reason": "end_turn",
    "usage": {"input_tokens": 1000, "output_tokens": 200,
              "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0},
}


def _sweep_resp_for(url):
    if url == llm.ANTHROPIC_MESSAGES_URL:
        return _Resp(_ANTHROPIC_SWEEP_PAYLOAD)
    return _Resp(_SWEEP_PAYLOAD)


def _recording_transport(monkeypatch):
    calls = []

    def fake_urlopen(req, timeout=None):
        calls.append(req.full_url)
        return _sweep_resp_for(req.full_url)

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    return calls


def test_writer_lane_override_does_not_leak_into_editor_step(monkeypatch):
    """B2 CLOSED the B1 'B4 residual': _chat now transports on the per-step seat
    (via _ACTIVE_SEAT_CFG), so NEWSLENS_LANE_WRITER=subscription no longer reaches
    an 'editor' step — that step rides the editor seat (Claude api/Haiku). It
    COMPLETES honestly on its own lane; the writer override is inert for it, and
    the ledger attributes the editor seat, not writer. (Was: died loud via the
    frozen-writer residual; that residual is now structurally impossible, so a
    seat-threading regression would resurface it and fail the sweep — FIX-2 b.)"""
    _no_sleep(monkeypatch)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-anthropic-fake")
    calls = _recording_transport(monkeypatch)
    monkeypatch.setenv("NEWSLENS_LANE_WRITER", "subscription")
    sink = []
    generate.call_llm("k", "p", "editor", 100, 0.5, False, cost_sink=sink)
    assert len(calls) == 1
    assert calls[0] == llm.ANTHROPIC_MESSAGES_URL  # rode the Claude lane, not writer
    assert sink and sink[0]["lane"] == "api"
    assert sink[0]["model"] == "claude-haiku-4-5"
    assert sink[0]["usd"] == sink[0]["usd_charged"] == sink[0]["usd_shadow"]


@pytest.mark.parametrize("env,step,should_complete", [
    # per-seat api pin + bad global: the editor step rides the editor seat
    # (api-pinned), and B2's per-step transport no longer reads the writer seat
    # under the bad global -> the editor COMPLETES on its own lane (was False in
    # B1's frozen-writer residual; the residual is closed)
    ({"NEWSLENS_LANE": "subscription", "NEWSLENS_LANE_EDITOR": "api"},
     "editor", True),
    # bad per-seat + good global: gate raises immediately
    ({"NEWSLENS_LANE": "api", "NEWSLENS_LANE_EDITOR": "subscription"},
     "editor", False),
    # both good: completes on api
    ({"NEWSLENS_LANE": "api", "NEWSLENS_LANE_EDITOR": "api"},
     "editor", True),
    # narrative step: per-seat writer pin beats bad global on BOTH gate and
    # transport (same seat) -> completes
    ({"NEWSLENS_LANE": "subscription", "NEWSLENS_LANE_WRITER": "api"},
     "narrative", True),
])
def test_per_seat_plus_global_combined_rows(monkeypatch, env, step,
                                            should_complete):
    """Coordinator row 2: per-seat override and global NEWSLENS_LANE set
    together — every combination either completes honestly on the api lane
    or dies loud with zero transport and zero ledger rows."""
    _no_sleep(monkeypatch)
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    sink = []
    if should_complete:
        calls = _recording_transport(monkeypatch)
        generate.call_llm("k", "p", step, 100, 0.5, False, cost_sink=sink)
        assert len(calls) == 1
        assert sink[0]["lane"] == "api"
        assert sink[0]["usd"] == sink[0]["usd_charged"] == sink[0]["usd_shadow"]
    else:
        calls = _transport_tripwire(monkeypatch)
        with pytest.raises(Exception) as exc:
            generate.call_llm("k", "p", step, 100, 0.5, False, cost_sink=sink)
        assert "no registered implementation" in str(exc.value)
        assert calls == []
        assert sink == []


def test_armed_fallback_plus_bad_lane_still_dies_loud_at_wrappers(
        monkeypatch):
    """Coordinator row 3: NEWSLENS_LANE_FALLBACK=api armed AND a bad lane —
    B1 has no second lane, so arming changes nothing: LaneUnavailable from
    all three wrappers, zero transport, zero ledger rows."""
    _no_sleep(monkeypatch)
    calls = _transport_tripwire(monkeypatch)
    monkeypatch.setenv("NEWSLENS_LANE_FALLBACK", "api")
    monkeypatch.setenv("NEWSLENS_LANE", "subscription")
    sink = []
    with pytest.raises(llm.LaneUnavailable):
        generate.call_llm("k", "p", "narrative", 100, 0.5, False,
                          cost_sink=sink)
    with pytest.raises(llm.LaneUnavailable):
        ranking.call_llm_validated("k", "p", {1}, {}, [], cost_sink=sink)
    with pytest.raises(llm.LaneUnavailable):
        analysis.call_analysis_model("k", "p")
    assert calls == []
    assert sink == []


def test_exhaustive_lane_env_sweep_no_transport_with_mismatched_ledger(
        monkeypatch):
    """THE D1-CLOSE INVARIANT, swept exhaustively over the whole env surface:
    global lane {unset, api, subscription, junk} x one per-seat var {none, or
    NEWSLENS_LANE_<SEAT> in {api, subscription, junk} for all SEVEN seats
    (rank/analyst/writer/editor/script/synthesis + the B2-joined state seat)}
    x fallback {unarmed, armed} x every live call_llm-family entrypoint (5
    generate steps, rank_select, analysis) — 4 x 22 x 2 x 7 = 1232
    combinations. For every one:

      completed  => exactly the recorded transport calls produced ledger
                    rows, every row lane == 'api', usd == usd_charged ==
                    usd_shadow (charged honesty), and the gate-resolved
                    seat's lane was 'api';
      gate-blocked (the entrypoint's own seat resolves to an unregistered
                    lane) => RAW LaneUnavailable — never wrapped, zero
                    transport calls, zero ledger rows (check_lane law);
      anything else => FAILURE. B2 CLOSED the old "B4 residual" (generate's
                    _chat no longer transports on a frozen 'writer' seat while
                    the ledger uses seat_for_step — it now transports on the
                    gated per-step seat via _ACTIVE_SEAT_CFG). So a gate-passing
                    config can no longer raise a wrapped LaneUnavailable inside
                    the retry loop; if any entrypoint ever does again, that is a
                    seat-threading regression and this sweep must fail on it
                    (FIX-2 b — the residual acceptance arm is deleted).

    Silent wrong-lane and phantom-$0 ledger rows are structurally
    unreachable across the entire env surface, or this test names the
    combination that breaks it."""
    _no_sleep(monkeypatch)
    # B2: rank/editor/script are on the Claude lane now; give the anthropic
    # provider a key so its x-api-key is populated (the fake transport ignores
    # its value — this only avoids an empty-header edge in the provider).
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-anthropic-sweep-fake")

    seats = ["RANK", "ANALYST", "WRITER", "EDITOR", "SCRIPT", "SYNTHESIS",
             "STATE"]
    per_seat_options = [None] + [(f"NEWSLENS_LANE_{s}", v)
                                 for s in seats
                                 for v in ("api", "subscription", "junk")]
    entrypoints = []
    for step in ("narrative", "narrative_retry", "editor", "script",
                 "script_retry"):
        entrypoints.append(("generate:" + step, step))
    entrypoints.append(("ranking:rank_select", None))
    entrypoints.append(("analysis", None))

    lane_vars = ["NEWSLENS_LANE", "NEWSLENS_LANE_FALLBACK"] + \
        [f"NEWSLENS_LANE_{s}" for s in seats]

    checked = 0
    for global_lane in (None, "api", "subscription", "junk"):
        for per_seat in per_seat_options:
            for armed in (False, True):
                env = {}
                if global_lane is not None:
                    env["NEWSLENS_LANE"] = global_lane
                if per_seat is not None:
                    env[per_seat[0]] = per_seat[1]
                if armed:
                    env["NEWSLENS_LANE_FALLBACK"] = "api"
                for name, step in entrypoints:
                    for var in lane_vars:
                        monkeypatch.delenv(var, raising=False)
                    for k, v in env.items():
                        monkeypatch.setenv(k, v)
                    calls = _recording_transport(monkeypatch)
                    sink = []
                    label = f"{name} under {env}"
                    # what the entrypoint's own gate seat resolves to
                    if name.startswith("generate:"):
                        gate_seat = llm.seat_for_step(step)
                    elif name.startswith("ranking:"):
                        gate_seat = "rank"
                    else:
                        gate_seat = "analyst"
                    gate_blocked = llm.resolve_seat(gate_seat).lane != "api"
                    try:
                        if name.startswith("generate:"):
                            generate.call_llm("k", "p", step, 100, 0.5,
                                              False, cost_sink=sink)
                        elif name.startswith("ranking:"):
                            ranking.call_llm_validated("k", "p", {1}, {},
                                                       [], cost_sink=sink)
                        else:
                            analysis.call_analysis_model("k", "p")
                    except llm.LaneUnavailable:
                        assert calls == [], (
                            f"{label}: raised AFTER {len(calls)} transport "
                            f"call(s) — charged then refused")
                        assert sink == [], (
                            f"{label}: raised but ledger rows written: {sink}")
                    except Exception as exc:  # noqa: BLE001 — sweep verdict
                        # FIX-2 b: the residual acceptance arm is DELETED. After
                        # B2's per-step seat threading, a gate-blocked config
                        # raises RAW LaneUnavailable (above) and a gate-passing
                        # config completes — any other exception is a
                        # seat-threading regression (the old frozen-'writer'
                        # residual resurfacing) and MUST fail the sweep.
                        pytest.fail(
                            f"{label}: raised {type(exc).__name__} — a "
                            f"gate-blocked config must raise raw LaneUnavailable "
                            f"and a gate-passing config must complete; any other "
                            f"exception is a seat-threading regression: {exc}")
                    else:
                        assert calls, f"{label}: completed without transport?"
                        if name.startswith("generate:"):
                            gate_cfg = llm.resolve_seat(
                                llm.seat_for_step(step))
                        elif name.startswith("ranking:"):
                            gate_cfg = llm.resolve_seat("rank")
                        else:
                            gate_cfg = llm.resolve_seat("analyst")
                        assert gate_cfg.lane == "api", (
                            f"{label}: completed but gate seat resolved "
                            f"lane={gate_cfg.lane!r}")
                        for row in sink:
                            assert row["lane"] == "api", (
                                f"{label}: ledger row lane={row['lane']!r} "
                                f"on an api-transported call")
                            assert row["usd"] == row["usd_charged"] == \
                                row["usd_shadow"], (
                                    f"{label}: charged-honesty broken: {row}")
                    checked += 1
    assert checked == 4 * 22 * 2 * 7  # 4 global x 22 per-seat (7 seats) x 2 x 7
