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

B3 CONSCIOUS FLIPS (2026-07-17, QA pass on the subscription lane): 26
assertions in this file flipped when rank/editor/script's DEFAULT lane went
api -> subscription and `anthropic:subscription` became a REGISTERED lane
(ADR-0015). Every flip preserves the tooth it carried:
  * default-map rows now assert the SUBSCRIPTION default (the flip is the
    deliberate pin, per the B1 guard-test law);
  * tests that assert api-lane transport bytes pin NEWSLENS_LANE_<SEAT>=api
    (the registered fall-over) — the contract is unchanged, the route to it
    is now explicit;
  * every "unavailable lane fails loud" tooth that used rank/editor/script
    x subscription is RE-EXPRESSED, not deleted: either an openai seat on a
    non-api lane (still unregistered) or a subscription seat whose binary
    does not resolve (NEWSLENS_CLAUDE_BIN at an absent path) — "unavailable
    lane dies loud before any transport" stays provable in both forms;
  * the exhaustive sweep gains the subscription dimension: completions on
    the subscription lane must show ZERO HTTP transport, usd_charged == 0.0
    with usd_shadow > 0, and every spawned argv carrying the Rook #2 safety
    flags; a binary-absent axis proves gate-kills across the same space.
"""

from __future__ import annotations

import ast
import inspect
import json
import os
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
    # B3 flip (conscious): rank DEFAULTS to the subscription lane now, so the
    # api lane — the registered fall-over — is pinned explicitly. The byte
    # contract itself is unchanged.
    monkeypatch.setenv("NEWSLENS_LANE_RANK", "api")
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
    # B4 flip (conscious, QA re-pin): the writer rides Opus 4.8 on the Claude
    # API lane. Same exact-bytes law (dict insertion order and all), re-pinned
    # to the anthropic Messages body the provider builds: model, max_tokens,
    # messages, NO temperature (sampling=False — Opus 4.8 rejects it with a
    # 400), the json nudge as a PLAIN-STRING system (no cache prefix on a
    # sentinel-less prompt), thinking adaptive, output_config effort xhigh.
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-qa")
    seen = _capture(monkeypatch)
    generate._chat("sk-qa", "PROMPT-W", 333, 0.7, True)
    expected = json.dumps({
        "model": "claude-opus-4-8",
        "max_tokens": 333,
        "messages": [{"role": "user", "content": "PROMPT-W"}],
        "system": llm._ANTHROPIC_JSON_SYSTEM,
        "thinking": {"type": "adaptive"},
        "output_config": {"effort": "xhigh"},
    }).encode("utf-8")
    assert seen["data"] == expected
    assert b"temperature" not in seen["data"]
    assert b"budget_tokens" not in seen["data"]
    assert seen["url"] == llm.ANTHROPIC_MESSAGES_URL   # lane's own endpoint
    assert seen["timeout"] == 600
    assert _hdr(seen["req"], "x-api-key") == "sk-ant-qa"
    assert _hdr(seen["req"], "User-Agent") == generate.WRITER_UA


def test_writer_request_bytes_identical_json_mode_off(monkeypatch):
    """system OMITTED entirely when json_mode is off and no cache prefix is
    present (the script-shaped call never sent a nudge; its presence would be
    a behavior change). temperature stays omitted — sampling=False."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-qa")
    seen = _capture(monkeypatch)
    generate._chat("sk-qa", "PROMPT-S", 512, 0.4, False)
    expected = json.dumps({
        "model": "claude-opus-4-8",
        "max_tokens": 512,
        "messages": [{"role": "user", "content": "PROMPT-S"}],
        "thinking": {"type": "adaptive"},
        "output_config": {"effort": "xhigh"},
    }).encode("utf-8")
    assert seen["data"] == expected
    assert b"response_format" not in seen["data"]
    assert b"temperature" not in seen["data"]
    assert b"system" not in seen["data"]


def test_analysis_request_bytes_identical_and_historical_url(monkeypatch):
    # B4 flip (conscious, QA re-pin): analyst -> Sonnet 5 on the Claude API
    # lane. Exact bytes: no temperature (400 on Sonnet 5), json nudge as the
    # plain-string system (no sentinel in this prompt), adaptive thinking at
    # effort high, ANALYSIS_MAX_TOKENS = 6000 on the wire.
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-qa")
    seen = _capture(monkeypatch)
    analysis._analysis_chat("sk-qa", "PROMPT-A")
    expected = json.dumps({
        "model": "claude-sonnet-5",
        "max_tokens": analysis.ANALYSIS_MAX_TOKENS,
        "messages": [{"role": "user", "content": "PROMPT-A"}],
        "system": llm._ANTHROPIC_JSON_SYSTEM,
        "thinking": {"type": "adaptive"},
        "output_config": {"effort": "high"},
    }).encode("utf-8")
    assert seen["data"] == expected
    assert b"temperature" not in seen["data"]
    # the anthropic lane reads its own endpoint; the historical openai seam
    # string is still what the openai seats resolve (pinned in the rank test).
    assert seen["url"] == llm.ANTHROPIC_MESSAGES_URL
    assert llm.OPENAI_CHAT_URL == ranking.OPENAI_CHAT_URL \
        == "https://api.openai.com/v1/chat/completions"
    assert seen["timeout"] == 240
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
    # B3 flip (conscious): rank's DEFAULT is the subscription lane now — the
    # default-map rows assert the flipped map, the explicit-api rows assert
    # the registered fall-over. Empty string == unset (dotenv `NEWSLENS_LANE=`
    # line) stays the documented convention: it yields the DEFAULT, whatever
    # the default is.
    ({}, "rank", "subscription"),                            # B3 default
    ({"NEWSLENS_LANE": ""}, "rank", "subscription"),         # empty == unset
    ({"NEWSLENS_LANE_RANK": ""}, "rank", "subscription"),
    ({"NEWSLENS_LANE": "subscription"}, "rank", "subscription"),
    ({"NEWSLENS_LANE": "api"}, "rank", "api"),               # explicit fall-over
    ({"NEWSLENS_LANE": " api "}, "rank", "api"),             # whitespace strip
    ({"NEWSLENS_LANE": "api", "NEWSLENS_LANE_RANK": "api"}, "rank", "api"),
    # the openai seats' default map is UNTOUCHED by B3
    ({}, "writer", "api"),
    ({}, "state", "api"),
])
def test_lane_matrix_rows_follow_the_default_map(env, seat, expect_lane):
    import dataclasses
    cfg = llm.resolve_seat(seat, env)
    assert cfg.lane == expect_lane
    if expect_lane == llm.SEATS[seat].lane:
        # full config identical, not just lane (the identity row)
        assert cfg == llm.SEATS[seat]
    else:
        # an override moves ONLY the lane — model/prices/timeouts never drift
        assert cfg == dataclasses.replace(llm.SEATS[seat], lane=expect_lane)


@pytest.mark.parametrize("env,seat", [
    # B3 flip (conscious): rank/editor/script x subscription is REGISTERED
    # now, so those rows moved out of this matrix. The fail-loud tooth stays
    # provable two ways: (a) here — unknown lanes, and any OPENAI seat forced
    # off the api lane (openai runs ONLY on api); (b) below — a subscription
    # seat whose binary does not resolve (the availability re-expression).
    ({"NEWSLENS_LANE": "claude"}, "rank"),          # unknown string
    ({"NEWSLENS_LANE": "API"}, "rank"),             # case-sensitive: unknown
    ({"NEWSLENS_LANE": "   "}, "rank"),             # whitespace-only -> "" lane
    # B4 flip (conscious): writer/analyst are ANTHROPIC seats now — their
    # subscription combos are REGISTERED and moved out of this fail-loud
    # matrix (positive pins live in test_b1_llm_seam + test_b4_battery_qa).
    # The openai-seat-off-api tooth regrows on the still-openai seats.
    ({"NEWSLENS_LANE": "subscription"}, "state"),   # openai seat, global flip
    ({"NEWSLENS_LANE_SYNTHESIS": "subscription"}, "synthesis"),
    ({"NEWSLENS_LANE_STATE": "subscription"}, "state"),
    ({"NEWSLENS_LANE_WRITER": "nonsense"}, "writer"),
    ({"NEWSLENS_LANE_RANK": "junk", "NEWSLENS_LANE": "api"}, "rank"),
    # empty per-seat does NOT rescue a bad global (empty == unset)
    ({"NEWSLENS_LANE": "junk", "NEWSLENS_LANE_RANK": ""}, "rank"),
])
def test_lane_matrix_rows_that_fail_loud_before_any_transport(
        monkeypatch, env, seat):
    calls = _transport_tripwire(monkeypatch)
    cfg = llm.resolve_seat(seat, env)
    with pytest.raises(llm.LaneUnavailable) as exc:
        llm.chat(llm.LaneRequest(cfg, "p", 0, 10, True, "ua", "k"))
    assert "NEWSLENS_LANE" in str(exc.value)        # names the fix
    assert calls == []                              # never reached a socket


@pytest.mark.parametrize("seat", ["rank", "editor", "script"])
def test_subscription_seat_with_unresolvable_binary_fails_loud_no_transport(
        monkeypatch, seat, tmp_path):
    """The B3 RE-EXPRESSION of the old rank/subscription fail-loud rows:
    'unavailable lane dies loud before any transport' now means a REGISTERED
    subscription lane whose `claude` binary does not resolve. Both the gate
    (check_lane — names the NEWSLENS_LANE_<SEAT>=api fall-over) and the
    provider's own belt-and-suspenders (chat) must raise LaneUnavailable with
    zero HTTP calls and zero subprocess spawns."""
    calls = _transport_tripwire(monkeypatch)
    spawns = []

    def _spawn_tripwire(*a, **k):
        spawns.append(a)
        raise AssertionError("subprocess spawned despite an unresolvable binary")

    monkeypatch.setattr(llm.subprocess, "run", _spawn_tripwire)
    monkeypatch.setenv("NEWSLENS_CLAUDE_BIN", str(tmp_path / "absent-claude"))
    cfg = llm.resolve_seat(seat)
    assert cfg.lane == "subscription"
    with pytest.raises(llm.LaneUnavailable) as gate_exc:
        llm.check_lane(cfg)
    # the gate names BOTH fixes: the binary path problem and the api fall-over
    assert "NEWSLENS_CLAUDE_BIN" in str(gate_exc.value)
    assert f"NEWSLENS_LANE_{seat.upper()}=api" in str(gate_exc.value)
    with pytest.raises(llm.LaneUnavailable):
        llm.chat(llm.LaneRequest(cfg, "p", 0, 10, True, "ua", "k"))
    assert calls == []
    assert spawns == []


def test_per_seat_override_beats_global_and_only_that_seat(monkeypatch):
    env = {"NEWSLENS_LANE": "api", "NEWSLENS_LANE_RANK": "subscription"}
    assert llm.resolve_seat("rank", env).lane == "subscription"
    for other in ("analyst", "writer", "editor", "script", "synthesis"):
        assert llm.resolve_seat(other, env).lane == "api"


@pytest.mark.parametrize("caller,msg_anchor", [
    # B4 flip (conscious): writer and analyst are ANTHROPIC seats now, so a
    # global subscription flip is a REGISTERED lane for all three callers —
    # every row moves to the AVAILABILITY form ranking already used (the same
    # global flip PLUS a binary that does not resolve). Same gate
    # (check_lane), same raw class, same zero-everything; the message names
    # the binary problem. The unregistered form ("no registered
    # implementation") keeps its own teeth on the still-openai seats via the
    # lane-matrix rows above and the state/synthesis wrapper pins elsewhere.
    ("generate", "NEWSLENS_CLAUDE_BIN"),
    ("analysis", "NEWSLENS_CLAUDE_BIN"),
    ("ranking", "NEWSLENS_CLAUDE_BIN"),
])
def test_runtime_env_lane_fails_loud_through_all_three_wrappers(
        monkeypatch, caller, msg_anchor, tmp_path):
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
    monkeypatch.setenv("NEWSLENS_CLAUDE_BIN", str(tmp_path / "absent"))
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
    assert msg_anchor in msg
    assert "NEWSLENS_LANE" in msg           # names an env fix in both forms
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

@pytest.mark.parametrize("step,var,lane,bin_absent,msg_anchor", [
    # B3 flip (conscious): editor/script x subscription is REGISTERED, so the
    # unavailable-lane rows are re-expressed — a registered subscription lane
    # whose binary does not resolve (bin_absent) is the new unavailability,
    # and unknown/typo lanes stay the unregistered form. Same D1 tooth: a
    # per-seat override to an UNAVAILABLE lane dies loud at the gate with
    # zero transport, never a silent wrong-lane call.
    ("editor", "NEWSLENS_LANE_EDITOR", "subscription", True,
     "NEWSLENS_CLAUDE_BIN"),
    ("script", "NEWSLENS_LANE_SCRIPT", "subscription", True,
     "NEWSLENS_CLAUDE_BIN"),
    ("editor", "NEWSLENS_LANE_EDITOR", "junk", False,
     "no registered implementation"),
    ("script_retry", "NEWSLENS_LANE_SCRIPT", "subscriptoin", False,  # typo lane
     "no registered implementation"),
])
def test_per_seat_lane_override_on_generate_steps_fails_loud(
        monkeypatch, step, var, lane, bin_absent, msg_anchor, tmp_path):
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
    This test must pass with the fix and the default-map matrix rows must
    stay green (default env stays byte-identical).

    B3 note: the subscription rows here run with the binary UNRESOLVABLE
    (the availability form of unavailable); a resolvable subscription
    override is a legitimate lane now and its honest completion is pinned in
    the sweep + test_b3_subscription_lane_qa.
    """
    _no_sleep(monkeypatch)
    calls = _transport_tripwire(monkeypatch)
    if bin_absent:
        monkeypatch.setenv("NEWSLENS_CLAUDE_BIN", str(tmp_path / "absent"))
    monkeypatch.setenv(var, lane)
    sink = []
    with pytest.raises(Exception) as exc:
        generate.call_llm("k", "p", step, 100, 0.5, False, cost_sink=sink)
    assert msg_anchor in str(exc.value)
    assert isinstance(exc.value, llm.LaneUnavailable)   # raw, never wrapped
    assert calls == [], (
        f"step {step!r} under {var}={lane} reached the real transport "
        f"{len(calls)} time(s) — charged call misfiled as lane={lane!r}"
    )
    assert sink == []                       # no ledger row for a blocked call


def test_generate_steps_default_env_transport_and_ledger_agree(monkeypatch):
    """The D1 close under B3: with NO lane env, each step's ledger row names
    the transport that actually carried the bytes — narrative on gpt-4o over
    the openai HTTP api lane (charged == shadow == legacy usd), editor/script
    on the Claude Haiku SUBSCRIPTION subprocess (ZERO HTTP calls; legacy usd
    == usd_charged == 0.0 with usd_shadow > 0). The ledger can neither fork
    the model that ran from the price it records, nor claim a $0 subscription
    row for bytes that rode a metered wire (the D1 lie, both directions)."""
    http_calls = []

    def fake_urlopen(req, timeout=None):
        http_calls.append(req.full_url)
        return _Resp(_CANNED)

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    # B4 flip (conscious): narrative rows ride the Opus writer seat on the
    # anthropic api lane now (still HTTP — charged == shadow); editor/script
    # stay on the $0 subscription stub. The D1 tooth is unchanged: the ledger
    # names the transport that actually carried the bytes, both directions.
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-qa")
    expect = {"narrative": ("claude-opus-4-8", "api"),
              "narrative_retry": ("claude-opus-4-8", "api"),
              "editor": ("claude-haiku-4-5", "subscription"),
              "script": ("claude-haiku-4-5", "subscription"),
              "script_retry": ("claude-haiku-4-5", "subscription")}
    for step, (model, lane) in expect.items():
        sink = []
        before_http = len(http_calls)
        generate.call_llm("k", "p", step, 100, 0.5, False, cost_sink=sink)
        e = sink[0]
        assert e["lane"] == lane and e["model"] == model, step
        if lane == "api":
            assert len(http_calls) == before_http + 1, step   # rode the wire
            assert e["usd"] == e["usd_shadow"] == e["usd_charged"], step
        else:
            # subscription: the conftest stub answered — never HTTP
            assert len(http_calls) == before_http, step
            assert e["usd"] == e["usd_charged"] == 0.0, step
            assert e["usd_shadow"] > 0.0, step                # cap still bites


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


def test_armed_fallback_never_falls_below_the_effective_seat_seam(
        monkeypatch, tmp_path):
    """CONSCIOUSLY RE-EXPRESSED (loop 2, 2026-07-17 — the fall is LIVE now,
    resolving the QA-filed D2 doc-overclaim by implementing the behavior):
    the armed single-fall lives at llm.effective_seat and ONLY there. The
    layers BELOW it — resolve_seat, check_lane, chat — must never fall on
    their own, armed or not: they are the fail-safe floor that guarantees a
    caller which skips the effective_seat gate can never silently ride a
    different lane than it resolved (the D1 invariant's foundation). The
    fall's own law (one hop, labeled, disclosed, both-lanes-dead dies on the
    original error) is pinned in test_b3_subscription_lane.py §10, the
    wrappers test above, and the sweep's fallback arm."""
    calls = _transport_tripwire(monkeypatch)
    monkeypatch.setenv("NEWSLENS_LANE_FALLBACK", "api")
    monkeypatch.setenv("NEWSLENS_CLAUDE_BIN", str(tmp_path / "absent"))
    # the floor: resolve/check/chat stay loud even armed
    assert llm.resolve_seat("rank").lane == "subscription"   # resolve never falls
    with pytest.raises(llm.LaneUnavailable):
        llm.check_lane(llm.resolve_seat("rank"))
    with pytest.raises(llm.LaneUnavailable):
        llm.chat(llm.LaneRequest(llm.resolve_seat("rank"), "p", 0, 10, True,
                                 "ua", "k"))
    # the seam: effective_seat is where the armed fall happens, labeled
    cfg, reason = llm.effective_seat("rank")
    assert cfg.lane == "api" and reason == "subscription_unavailable"
    # armed + unregistered combo (openai seat off the api lane): loud at
    # EVERY layer including effective_seat (the no-rescue guard). B4 flip
    # (conscious): writer is anthropic now — its subscription combo is
    # registered and FALLS here instead (asserted below); the no-rescue
    # tooth regrows on the still-openai state seat.
    with pytest.raises(llm.LaneUnavailable):
        llm.chat(llm.LaneRequest(
            llm.resolve_seat("state", {"NEWSLENS_LANE": "subscription"}),
            "p", 0, 10, True, "ua", "k"))
    monkeypatch.setenv("NEWSLENS_LANE_STATE", "subscription")
    with pytest.raises(llm.LaneUnavailable):
        llm.effective_seat("state")
    # and the regrown positive: the writer's registered subscription combo,
    # armed + binary absent, falls ONCE at the seam, labeled — the writer is
    # fall-capable now (its caller resolves via effective_seat).
    monkeypatch.setenv("NEWSLENS_LANE_WRITER", "subscription")
    w_cfg, w_reason = llm.effective_seat("writer")
    assert w_cfg.lane == "api" and w_reason == "subscription_unavailable"
    assert w_cfg.model == "claude-opus-4-8"
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
    import dataclasses
    usage = {"prompt_tokens": 10, "completion_tokens": 10}
    # B3: SEATS["rank"] defaults to the subscription lane — charged is 0.0
    # while shadow stays API-priced; the metered key set is UNCHANGED (no
    # usd_shadow_estimated on a usage-reported row).
    sub = llm.cost_fields(llm.SEATS["rank"], usage)
    assert set(sub) == {"model", "lane", "cache_read_tokens",
                        "cache_creation_tokens",  # B2: recorded for B4 caching
                        "usd_shadow", "usd_charged"}
    assert "usd" not in sub                    # can never displace legacy key
    assert sub["lane"] == "subscription"
    assert sub["usd_charged"] == 0.0
    assert sub["usd_shadow"] > 0.0             # the cap's figure survives $0
    # the api fall-over keeps the B1/B2 invariant exactly
    api = llm.cost_fields(dataclasses.replace(llm.SEATS["rank"], lane="api"),
                          usage)
    assert set(api) == set(sub)
    assert api["usd_charged"] == api["usd_shadow"] == sub["usd_shadow"]


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


_RANK_SINK_GOOD = {
    "choices": [{"message": {"content": json.dumps(
        {"clusters": [{"story_title": "T", "summary": "S",
                       "item_ids": [1], "matched_tags": [],
                       "matched_memory": [], "world_impact": 5,
                       "world_impact_reason": "r"}]})},
        "finish_reason": "stop"}],
    "usage": {"prompt_tokens": 1234, "completion_tokens": 567,
              "prompt_tokens_details": {"cached_tokens": 1000}},
}


def test_rank_sink_entry_full_shape_legacy_usd_untouched(monkeypatch):
    # B3 flip (conscious): rank defaults to subscription now, so the api-lane
    # legacy parity (usd == usage_to_usd == charged == shadow) is pinned on
    # the explicit fall-over; the subscription twin is the next test.
    monkeypatch.setenv("NEWSLENS_LANE_RANK", "api")
    monkeypatch.setattr(ranking, "_post_chat", lambda key, prompt: _RANK_SINK_GOOD)
    sink = []
    ranking.call_llm_validated("k", "p", {1}, {}, [], cost_sink=sink)
    e = sink[0]
    assert set(e) == {"step", "attempt", "prompt_tokens", "completion_tokens",
                      "usd", "model", "lane", "cache_read_tokens",
                      "cache_creation_tokens", "usd_shadow", "usd_charged"}
    assert e["step"] == "rank_select" and e["attempt"] == 1
    # legacy `usd` == usd_charged, both from the (now Haiku) rank seat's prices
    assert e["usd"] == round(ranking.usage_to_usd(_RANK_SINK_GOOD["usage"]), 6)
    assert e["usd"] == e["usd_shadow"] == e["usd_charged"]
    assert e["model"] == "claude-haiku-4-5" and e["lane"] == "api"
    assert e["cache_read_tokens"] == 1000
    assert e["prompt_tokens"] == 1234 and e["completion_tokens"] == 567


def test_rank_sink_entry_subscription_lane_legacy_usd_is_charged_zero(
        monkeypatch):
    """Was RED (B3 QA pass, 2026-07-17) — ACCEPTANCE CONTRACT for defect
    B3-D1; CONSCIOUSLY FLIPPED GREEN by the loop-2 fix the same day
    (ranking.py: sink `usd` = cost_fields' usd_charged off the
    effective_seat resolution; persisted token_cost through cost_fields with
    the full key set — exactly the fix contract below).
    The defect it gated (money-honesty, the inverse-D1 class): ranking built
    the sink's legacy
    `usd` as round(usage_to_usd(usage), 6) (ranking.py ~657) — LANE-BLIND —
    so on the now-DEFAULT subscription lane the real-money column claims
    ~$0.002/call that was never charged, while usd_charged (correct, from
    cost_fields) says 0.0 in the same row. ADR-0014 §5: legacy `usd` is
    retained '== usd_charged'; generate.call_llm honors that
    ('usd': fields['usd_charged']); ranking forked it. The same lane-blind
    pricing persists into briefings.token_cost via run_rank's summary block
    (ranking.py ~1216-1228, which carries NO lane/shadow keys at all — see
    the persisted-row twin in test_b3_subscription_lane_qa).

    FIX CONTRACT: build the entry off cost_fields FIRST and set legacy
    'usd' = fields['usd_charged'] (byte-identical on the api lane, so no
    green api-lane pin moves); route run_rank's persisted token_cost step
    through llm.cost_fields(resolve_seat('rank')) so the durable row carries
    {model, lane, cache_*, usd_shadow, usd_charged} with usd == usd_charged.
    Suppressing the subscription default for rank, or teaching the test that
    'usd' may carry shadow, is NOT acceptable (that codifies the lie).

    The subscription twin of the api-parity pin above: same stubbed reply,
    DEFAULT (subscription) rank seat — the entry keeps the IDENTICAL key
    set, legacy `usd` == usd_charged == 0.0 (the legacy key tracks real
    money, never shadow), usd_shadow alone carries the API-priced figure,
    and no estimated label appears on a usage-reported row."""
    monkeypatch.setattr(ranking, "_post_chat", lambda key, prompt: _RANK_SINK_GOOD)
    sink = []
    ranking.call_llm_validated("k", "p", {1}, {}, [], cost_sink=sink)
    e = sink[0]
    assert set(e) == {"step", "attempt", "prompt_tokens", "completion_tokens",
                      "usd", "model", "lane", "cache_read_tokens",
                      "cache_creation_tokens", "usd_shadow", "usd_charged"}
    assert e["model"] == "claude-haiku-4-5" and e["lane"] == "subscription"
    assert e["usd"] == e["usd_charged"] == 0.0
    assert e["usd_shadow"] == round(ranking.usage_to_usd(_RANK_SINK_GOOD["usage"]), 6)
    assert "usd_shadow_estimated" not in e


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
    # narrative rides the writer seat (B4: Opus 4.8 — WRITER_USD_* derive from
    # the seat), so legacy `usd` == the writer-rate _step_cost == usd_charged
    # == usd_shadow, all at Opus $5/$25 now.
    assert e["usd"] == round(generate._step_cost(resp["usage"]), 6)
    assert e["usd"] == e["usd_shadow"] == e["usd_charged"]
    assert e["usd"] == round(4321 / 1e6 * 5.00 + 765 / 1e6 * 25.00, 6)
    assert e["model"] == "claude-opus-4-8" and e["lane"] == "api"


# ---------------------------------------------------------------------------
# C1/C3 guard — the seat table is the current stack, exactly
# ---------------------------------------------------------------------------

def test_seat_table_pins_the_b3_stack_exactly():
    # B4 (conscious flip of the B3 pin — QA re-pinned against ADR-0016):
    #   writer  -> anthropic/claude-opus-4-8 on the API lane, $5.00/$25.00,
    #              adaptive thinking, effort xhigh, sampling OFF (Opus 4.8
    #              400s on temperature), timeout 600s;
    #   analyst -> anthropic/claude-sonnet-5 on the API lane, $3.00/$15.00
    #              (the STANDARD price, not the 2026-08-31 intro — a money
    #              guard never under-prices), adaptive thinking, effort high,
    #              sampling OFF, timeout 240s;
    #   rank/editor/script stay Haiku/subscription ($1.00/$5.00, sampling ON,
    #   no thinking — mechanical seats); state/synthesis stay openai/gpt-4o
    #   ($2.50/$10.00, sampling ON). This guard is what makes the NEXT
    #   model/lane/knob flip deliberate, never accidental.
    assert set(llm.SEATS) == {"rank", "analyst", "writer", "editor", "script",
                              "synthesis", "state"}
    haiku_sub = {"rank", "editor", "script"}
    timeouts = {"rank": 90, "analyst": 240, "writer": 600, "editor": 120,
                "script": 120, "synthesis": 120, "state": 60}
    for name, cfg in llm.SEATS.items():
        assert cfg.seat == name
        assert cfg.timeout_s == timeouts[name]
        if name == "writer":
            assert (cfg.provider, cfg.model, cfg.lane) == \
                ("anthropic", "claude-opus-4-8", "api")
            assert cfg.usd_per_mtok_in == 5.00
            assert cfg.usd_per_mtok_out == 25.00
            assert cfg.thinking == "adaptive" and cfg.effort == "xhigh"
            assert cfg.sampling is False
        elif name == "analyst":
            assert (cfg.provider, cfg.model, cfg.lane) == \
                ("anthropic", "claude-sonnet-5", "api")
            assert cfg.usd_per_mtok_in == 3.00
            assert cfg.usd_per_mtok_out == 15.00
            assert cfg.thinking == "adaptive" and cfg.effort == "high"
            assert cfg.sampling is False
        elif name in haiku_sub:
            assert cfg.lane == "subscription", name
            assert cfg.provider == "anthropic"
            assert cfg.model == "claude-haiku-4-5"
            assert cfg.usd_per_mtok_in == 1.00
            assert cfg.usd_per_mtok_out == 5.00
            assert cfg.thinking is None and cfg.effort is None
            assert cfg.sampling is True, name    # Haiku still sends temperature
        else:
            assert cfg.lane == "api", name
            assert cfg.provider == "openai"
            assert cfg.model == "gpt-4o"
            assert cfg.usd_per_mtok_in == 2.50
            assert cfg.usd_per_mtok_out == 10.00
            assert cfg.thinking is None and cfg.effort is None
            assert cfg.sampling is True, name    # gpt-4o still sends temperature


def test_seat_for_step_covers_every_live_generate_step():
    # the five step strings generate.call_llm is actually called with today
    assert llm.seat_for_step("narrative") == "writer"
    assert llm.seat_for_step("narrative_retry") == "writer"
    assert llm.seat_for_step("editor") == "editor"
    assert llm.seat_for_step("script") == "script"
    assert llm.seat_for_step("script_retry") == "script"
    assert llm.seat_for_step("rank_select") == "rank"
    # FIX-6 (B4, conscious flip of the B1 default): an unknown step RAISES —
    # the old silent default-to-writer was value-neutral when the whole
    # writer family was gpt-4o, but post-B4 it would bill the Opus seat (the
    # priciest, thinking-on) AND mislabel the ledger under a typo'd/new
    # step. The error must name the fix (the prefix table) so the next new
    # step is a deliberate row, never an accident.
    with pytest.raises(ValueError) as exc:
        llm.seat_for_step("mystery_step")
    msg = str(exc.value)
    assert "mystery_step" in msg
    assert "_STEP_PREFIX_SEAT" in msg
    for prefix in ("rank", "narrative", "editor", "script"):
        assert prefix in msg                       # the known rows, named
    with pytest.raises(ValueError):
        llm.seat_for_step("")                      # empty string is unknown too


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
    # B3 (conscious): shutil/subprocess/tempfile join for the `claude -p`
    # subprocess transport — all stdlib, so the zero-SDK leaf law holds; the
    # allowlist grows only by name so the NEXT import is a deliberate flip too.
    allowed = {"json", "os", "urllib.error", "urllib.request", "dataclasses",
               "typing", "__future__", "shutil", "subprocess", "tempfile"}
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
    # B3: the default map renders rank/editor/script on the subscription lane
    # and the rest on api — every line INFO (the sandbox stub satisfies the
    # binary gate exactly as a real installed CLI would).
    results = doctor.check_llm_lanes({})
    assert len(results) == len(llm.SEATS) + 2       # seats + fallback + lane note
    assert all(r.status == doctor.INFO for r in results)
    seat_lines = [r.text for r in results[:len(llm.SEATS)]]
    for name, cfg in llm.SEATS.items():
        assert any(
            line.startswith(f"{name}: {cfg.provider}/{cfg.model} · "
                            f"lane={cfg.lane}")
            for line in seat_lines), name
    sub_lines = [l for l in seat_lines if "lane=subscription" in l]
    assert len(sub_lines) == 3                       # rank/editor/script
    assert "fallback unarmed" in results[len(llm.SEATS)].text


def test_doctor_lanes_bad_lane_fails_every_seat_naming_the_fix():
    # B3 flip (conscious): a global 'subscription' no longer fails EVERY seat
    # (it is a registered lane for the anthropic seats) — 'junk' is the
    # every-seat config error now.
    results = doctor.check_llm_lanes({"NEWSLENS_LANE": "junk"})
    fails = [r for r in results if r.status == doctor.FAIL]
    assert len(fails) == len(llm.SEATS)
    for r in fails:
        assert "no registered implementation" in r.text
        assert "NEWSLENS_LANE" in r.text


def test_doctor_lanes_global_subscription_fails_only_the_openai_seats():
    """The B3 shape of the old every-seat test, B4-flipped (conscious): the
    writer/analyst joined the anthropic family, so a global subscription flip
    is a config error ONLY for the two still-openai seats (state/synthesis);
    the five anthropic seats render INFO on their registered lane."""
    results = doctor.check_llm_lanes({"NEWSLENS_LANE": "subscription"})
    seat_results = results[:len(llm.SEATS)]
    fails = [r for r in seat_results if r.status == doctor.FAIL]
    infos = [r for r in seat_results if r.status == doctor.INFO]
    assert len(fails) == 2                           # synthesis/state
    assert len(infos) == 5                # rank/editor/script + writer/analyst
    for r in fails:
        assert "no registered implementation" in r.text
    for r in infos:
        assert "lane=subscription" in r.text


def test_doctor_lanes_missing_binary_fails_the_subscription_seats(
        monkeypatch, tmp_path):
    """The doctor's fail-loud twin of check_lane's binary gate: with the
    binary unresolvable (check_lane reads os.environ), the three
    subscription-default seats FAIL naming the fix; the api seats stay INFO."""
    monkeypatch.setenv("NEWSLENS_CLAUDE_BIN", str(tmp_path / "absent"))
    results = doctor.check_llm_lanes({})
    seat_results = results[:len(llm.SEATS)]
    fails = [r for r in seat_results if r.status == doctor.FAIL]
    assert len(fails) == 3
    for r in fails:
        assert "NEWSLENS_CLAUDE_BIN" in r.text
    assert len([r for r in seat_results if r.status == doctor.INFO]) == 4


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
    required = {"NEWSLENS_LANE", "NEWSLENS_LANE_FALLBACK", "ANTHROPIC_API_KEY",
                # B3: the binary override joins the scrub (an ambient export
                # would re-aim every subscription spawn in the suite)
                "NEWSLENS_CLAUDE_BIN"}
    required |= {f"NEWSLENS_LANE_{s.upper()}" for s in llm.SEATS}
    # B4: resolve_seat reads NEWSLENS_MODEL_<SEAT> too (the battery override
    # surface). Same law, same bite (an ambient NEWSLENS_MODEL_WRITER export
    # — exactly what the ~07-24 battery workflow sets — failed 7 seam tests
    # pre-fix). Derived from llm.SEATS so a NEW seat grows its scrub
    # requirement automatically.
    required |= {f"NEWSLENS_MODEL_{s.upper()}" for s in llm.SEATS}
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
    """B2 CLOSED the B1 'B4 residual': _chat transports on the per-step seat
    (via _ACTIVE_SEAT_CFG). B3 makes this test STRONGER, not weaker: with
    NEWSLENS_LANE_WRITER=subscription the WRITER seat resolves to an
    UNREGISTERED combo (openai runs api-only) — so if the editor step read
    the writer seat anywhere (gate, transport, or ledger), this call would
    die loud. Instead it completes on the editor seat's OWN default lane
    (subscription, via the sandbox stub): zero HTTP, ledger names the editor
    seat, charged 0.0. The tripmine and the honest completion in one."""
    _no_sleep(monkeypatch)
    calls = _recording_transport(monkeypatch)
    monkeypatch.setenv("NEWSLENS_LANE_WRITER", "subscription")
    sink = []
    generate.call_llm("k", "p", "editor", 100, 0.5, False, cost_sink=sink)
    assert calls == []                          # subscription: never HTTP
    assert sink and sink[0]["lane"] == "subscription"
    assert sink[0]["model"] == "claude-haiku-4-5"
    assert sink[0]["usd"] == sink[0]["usd_charged"] == 0.0
    assert sink[0]["usd_shadow"] > 0.0


@pytest.mark.parametrize("env,step,expected", [
    # per-seat api pin + global subscription: the editor step rides its OWN
    # api-pinned lane (per-step threading, B2 close)
    ({"NEWSLENS_LANE": "subscription", "NEWSLENS_LANE_EDITOR": "api"},
     "editor", "api"),
    # B3 flip (conscious): per-seat subscription + good global is a LEGITIMATE
    # combo now — completes honestly on the subscription lane ($0 charged,
    # zero HTTP). The gate-raise tooth moves to the junk row below.
    ({"NEWSLENS_LANE": "api", "NEWSLENS_LANE_EDITOR": "subscription"},
     "editor", "subscription"),
    # bad per-seat + good global: gate raises immediately (the original tooth)
    ({"NEWSLENS_LANE": "api", "NEWSLENS_LANE_EDITOR": "junk"},
     "editor", "raise"),
    # both good: completes on api
    ({"NEWSLENS_LANE": "api", "NEWSLENS_LANE_EDITOR": "api"},
     "editor", "api"),
    # narrative step: per-seat writer pin beats bad global on BOTH gate and
    # transport (same seat) -> completes
    ({"NEWSLENS_LANE": "subscription", "NEWSLENS_LANE_WRITER": "api"},
     "narrative", "api"),
])
def test_per_seat_plus_global_combined_rows(monkeypatch, env, step, expected):
    """Coordinator row 2: per-seat override and global NEWSLENS_LANE set
    together — every combination either completes honestly on ITS OWN lane
    (api: charged==shadow over HTTP; subscription: charged 0.0, shadow>0,
    zero HTTP) or dies loud with zero transport and zero ledger rows."""
    _no_sleep(monkeypatch)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-anthropic-fake")
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    sink = []
    if expected == "raise":
        calls = _transport_tripwire(monkeypatch)
        with pytest.raises(Exception) as exc:
            generate.call_llm("k", "p", step, 100, 0.5, False, cost_sink=sink)
        assert "no registered implementation" in str(exc.value)
        assert calls == []
        assert sink == []
        return
    calls = _recording_transport(monkeypatch)
    generate.call_llm("k", "p", step, 100, 0.5, False, cost_sink=sink)
    assert sink[0]["lane"] == expected
    if expected == "api":
        assert len(calls) == 1
        assert sink[0]["usd"] == sink[0]["usd_charged"] == sink[0]["usd_shadow"]
    else:
        assert calls == []                   # subscription never touches HTTP
        assert sink[0]["usd"] == sink[0]["usd_charged"] == 0.0
        assert sink[0]["usd_shadow"] > 0.0


def test_armed_fallback_plus_bad_lane_still_dies_loud_at_wrappers(
        monkeypatch, tmp_path):
    """Coordinator row 3 — CONSCIOUSLY RE-EXPRESSED for the D2 fall (loop 2,
    2026-07-17; the previous form went red the moment the armed fall became
    real behavior, exactly as its docstring promised): with the fall LIVE,
    'armed + unavailable never silently spends' now means BOTH-LANES-DEAD —
    fallback armed, the subscription binary unresolvable, AND the api
    fall-over lane itself dead (anthropic:api deregistered, the implementer's
    test_D2_both_lanes_dead pattern). All three wrappers must die RAW on a
    LaneUnavailable, ranking's naming the ORIGINAL subscription error (never
    an api-shaped error that hides which lane the operator must fix, never a
    chained second fall — there is no third lane), with zero transport, zero
    spawns, zero ledger rows. generate/analysis arms: the global subscription
    flip on openai seats stays a no-rescue config error EVEN ARMED."""
    _no_sleep(monkeypatch)
    calls = _transport_tripwire(monkeypatch)
    spawns = []

    def _spawn_tripwire(*a, **k):
        spawns.append(a)
        raise AssertionError("spawned with both lanes dead")

    monkeypatch.setattr(llm.subprocess, "run", _spawn_tripwire)
    monkeypatch.setenv("NEWSLENS_LANE_FALLBACK", "api")
    monkeypatch.setenv("NEWSLENS_LANE", "subscription")
    monkeypatch.setenv("NEWSLENS_CLAUDE_BIN", str(tmp_path / "absent"))
    monkeypatch.delitem(llm._PROVIDERS, "anthropic:api")   # api lane dead too
    sink = []
    with pytest.raises(llm.LaneUnavailable):
        generate.call_llm("k", "p", "narrative", 100, 0.5, False,
                          cost_sink=sink)
    with pytest.raises(llm.LaneUnavailable) as rank_exc:
        ranking.call_llm_validated("k", "p", {1}, {}, [], cost_sink=sink)
    # the ORIGINAL subscription unavailability is what the operator sees
    assert "claude -p subscription lane" in str(rank_exc.value)
    assert "NEWSLENS_CLAUDE_BIN" in str(rank_exc.value)
    with pytest.raises(llm.LaneUnavailable):
        analysis.call_analysis_model("k", "p")
    assert calls == []
    assert spawns == []
    assert sink == []


# The sweep's registered-lane oracle — MY copy, deliberately independent of
# llm._PROVIDERS, so a registry regression (a lane vanishing or a wrong key
# appearing) diverges from the oracle and fails the sweep instead of
# re-deriving itself true.
_REGISTERED_LANES = {("openai", "api"), ("anthropic", "api"),
                     ("anthropic", "subscription")}

# What the faked `claude -p` child answers in the sweep: a documented-fields
# success envelope whose result validates as rank clusters (the strictest
# caller in the grid).
_SWEEP_SUB_STDOUT = json.dumps({
    "type": "result", "subtype": "success", "is_error": False,
    "result": _RANK_OK_CONTENT, "session_id": "sweep-sub",
    "total_cost_usd": 0.01,
    "usage": {"input_tokens": 1000, "output_tokens": 200},
})


def _recording_subprocess(monkeypatch):
    """Fake llm's subprocess.run at the OS boundary: the provider's whole body
    (binary resolution, argv build, env allowlist, scratch cwd, parse) stays
    LIVE; only the spawn is canned. Every recorded spawn is safety-checked by
    the sweep (Rook #1/#2 as a sweep-wide belt)."""
    spawns = []

    def fake_run(args, **kwargs):
        spawns.append({"args": list(args), "env": dict(kwargs.get("env") or {}),
                       "cwd": kwargs.get("cwd"), "input": kwargs.get("input")})
        import types
        return types.SimpleNamespace(returncode=0, stdout=_SWEEP_SUB_STDOUT,
                                     stderr="")

    monkeypatch.setattr(llm.subprocess, "run", fake_run)
    return spawns


def test_exhaustive_lane_env_sweep_no_transport_with_mismatched_ledger(
        monkeypatch, tmp_path):
    """THE D1-CLOSE INVARIANT, swept exhaustively over the env surface — B3
    CONSCIOUSLY GREW THE SPACE (this rewrite is the deliberate update): the
    subscription lane is REGISTERED for the anthropic seats now, so the sweep
    gains (a) a third completion outcome and (b) a binary-availability axis.
    Grid: global lane {unset, api, subscription, junk} x one per-seat var
    {none, or NEWSLENS_LANE_<SEAT> in {api, subscription, junk} x 7 seats}
    x fallback {unarmed, armed} x 7 entrypoints — 1232 combos with the stub
    binary resolvable, PLUS every combo whose entry seat resolves to the
    subscription lane rerun with the binary UNRESOLVABLE. For every one:

      completed on api          => >=1 HTTP transport call, ZERO subprocess
                                   spawns, every ledger row lane == 'api'
                                   EXACTLY (bare — a fallback label on a
                                   non-fallen row would be a phantom fall),
                                   usd == usd_charged == usd_shadow;
      completed on subscription => ZERO HTTP calls, >=1 recorded spawn whose
                                   argv carries ALL the Rook #2 safety flags
                                   and whose child env is allowlist-only with
                                   ANTHROPIC_API_KEY absent (Rook #1), every
                                   ledger row lane == 'subscription',
                                   usd == usd_charged == 0.0, usd_shadow > 0
                                   (Onna: the cap's figure never vanishes);
      completed via the ARMED FALL (D2, loop 2 — the CONSCIOUS regrowth of
                                   this oracle: a subscription-resolved entry
                                   seat + binary absent + fallback armed)
                                   => >=1 HTTP call, ZERO spawns, and every
                                   ledger row lane ==
                                   'api(fallback:subscription_unavailable)'
                                   EXACTLY — never a bare 'api' that hides
                                   the fall — with usd == usd_charged ==
                                   usd_shadow > 0 (the fall spends REAL
                                   money; that is why it must be labeled);
      gate-blocked (unregistered combo per MY oracle, or a subscription entry
                                   with the binary absent and the fallback
                                   UNARMED) => RAW LaneUnavailable — never
                                   wrapped, zero HTTP, zero spawns, zero
                                   ledger rows;
      anything else => FAILURE (FIX-2 b: the residual acceptance arm stays
                                   deleted — any other exception is a
                                   seat-threading regression).

    Honest completion or loud death — silent wrong-lane, phantom-$0 rows,
    subscription bytes on a metered wire, and UNLABELED fallen spend are
    structurally unreachable across the entire grown space, or this test
    names the combination that breaks it."""
    _no_sleep(monkeypatch)
    # The anthropic API lane wants a key for its x-api-key header (the fake
    # transport ignores the value); the subscription lane must never see it —
    # the recorded child envs prove that below, combo by combo.
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-anthropic-sweep-fake")

    stub_bin = os.environ["NEWSLENS_CLAUDE_BIN"]      # the sandbox's stub shim
    absent_bin = str(tmp_path / "absent-claude")
    allow = set(llm._SUBSCRIPTION_ENV_ALLOW)

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
    sub_axis_runs = 0
    tallies = {"api": 0, "subscription": 0, "fallback": 0, "blocked": 0}
    # B3-D1 (was RED 2026-07-17; CONSCIOUSLY FLIPPED GREEN by the loop-2 fix
    # — ranking now prices legacy `usd` off cost_fields' usd_charged): the
    # collector stays so any recurrence enumerates every violating combo
    # instead of dying on the first; it must remain EMPTY.
    legacy_usd_violations = []
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
                    if name.startswith("generate:"):
                        gate_seat = llm.seat_for_step(step)
                    elif name.startswith("ranking:"):
                        gate_seat = "rank"
                    else:
                        gate_seat = "analyst"
                    gate_cfg = llm.resolve_seat(gate_seat, env)
                    on_sub = gate_cfg.lane == "subscription" and \
                        (gate_cfg.provider, "subscription") in _REGISTERED_LANES
                    # bin axis: stub always; absent ONLY where it can matter
                    # (a subscription-resolved entry seat) — every other combo
                    # is byte-identical to its stub-pass twin.
                    for bin_path in ([stub_bin, absent_bin] if on_sub
                                     else [stub_bin]):
                        for var in lane_vars:
                            monkeypatch.delenv(var, raising=False)
                        for k, v in env.items():
                            monkeypatch.setenv(k, v)
                        monkeypatch.setenv("NEWSLENS_CLAUDE_BIN", bin_path)
                        bin_absent = bin_path == absent_bin
                        if bin_absent:
                            sub_axis_runs += 1
                        calls = _recording_transport(monkeypatch)
                        spawns = _recording_subprocess(monkeypatch)
                        sink = []
                        label = f"{name} under {env} (bin_absent={bin_absent})"
                        # MY oracle, not the registry's. D2 (loop 2): a
                        # subscription entry with the binary absent now
                        # FALLS to the api lane iff armed (the api lane is
                        # registered+available throughout this sweep except
                        # where a combo makes it unregistered — none do);
                        # unarmed stays blocked. An openai seat forced off
                        # api NEVER falls (no subscription provider — the
                        # no-rescue guard), armed or not.
                        registered = (gate_cfg.provider,
                                      gate_cfg.lane) in _REGISTERED_LANES
                        sub_dead = (gate_cfg.lane == "subscription"
                                    and bin_absent)
                        # B4-D1 CLOSED (FIX-1, 2026-07-17 — the conscious
                        # flip this oracle's as-is pin demanded): the
                        # analyst caller now resolves ONCE via the published
                        # _ACTIVE_ANALYST holder (effective_seat under it),
                        # so an analyst-on-subscription outage with the
                        # fallback armed COMPLETES as a labeled fall exactly
                        # like the generate/rank callers — `can_fall` is
                        # deleted and every fall-capable caller shares one
                        # oracle arm again. The analyst's fallen ledger rows
                        # carry lane='api(fallback:subscription_unavailable)'
                        # through cost_fields(fallback_reason=...).
                        if not registered:
                            expect = "blocked"
                        elif sub_dead:
                            expect = "fallback" if armed else "blocked"
                        else:
                            expect = gate_cfg.lane
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
                            assert expect == "blocked", (
                                f"{label}: LaneUnavailable on a combo the "
                                f"oracle says should complete on "
                                f"{expect!r}")
                            assert calls == [], (
                                f"{label}: raised AFTER {len(calls)} HTTP "
                                f"call(s) — charged then refused")
                            assert spawns == [], (
                                f"{label}: raised after spawning the child")
                            assert sink == [], (
                                f"{label}: raised but ledger rows: {sink}")
                            tallies["blocked"] += 1
                        except Exception as exc:  # noqa: BLE001 — sweep verdict
                            pytest.fail(
                                f"{label}: raised {type(exc).__name__} — a "
                                f"gate-blocked combo must raise raw "
                                f"LaneUnavailable and a gate-passing combo "
                                f"must complete; anything else is a "
                                f"seat-threading regression: {exc}")
                        else:
                            assert expect != "blocked", (
                                f"{label}: completed but the oracle says "
                                f"this combo is unavailable")
                            if expect == "api":
                                assert calls, (
                                    f"{label}: completed without transport?")
                                assert spawns == [], (
                                    f"{label}: api completion spawned the "
                                    f"claude child")
                                for row in sink:
                                    # EXACTLY bare 'api': a fallback label
                                    # here would be a phantom fall
                                    assert row["lane"] == "api", (
                                        f"{label}: row lane={row['lane']!r} "
                                        f"on an api-transported call")
                                    assert row["usd"] == row["usd_charged"] \
                                        == row["usd_shadow"], (
                                            f"{label}: charged-honesty "
                                            f"broken: {row}")
                            elif expect == "fallback":
                                # D2: the armed single-fall — real api spend,
                                # labeled as fallen, never spawning the child
                                assert calls, (
                                    f"{label}: fallen completion without "
                                    f"HTTP transport?")
                                assert spawns == [], (
                                    f"{label}: fallen completion spawned "
                                    f"the (absent) claude child?")
                                for row in sink:
                                    assert row["lane"] == (
                                        "api(fallback:"
                                        "subscription_unavailable)"), (
                                        f"{label}: fallen row mislabeled "
                                        f"lane={row['lane']!r} — a bare "
                                        f"'api' would hide the fall")
                                    assert row["usd"] == row["usd_charged"] \
                                        == row["usd_shadow"] > 0.0, (
                                            f"{label}: fallen-spend honesty "
                                            f"broken: {row}")
                            else:
                                assert calls == [], (
                                    f"{label}: subscription completion made "
                                    f"HTTP call(s): {calls} — the D1 lie")
                                assert spawns, (
                                    f"{label}: subscription completion "
                                    f"without a child spawn?")
                                for sp in spawns:
                                    argv = sp["args"][1:]
                                    ti = argv.index("--tools")
                                    assert argv[ti + 1] == "", label
                                    for flag in ("-p", "--safe-mode",
                                                 "--strict-mcp-config",
                                                 "--no-session-persistence"):
                                        assert flag in argv, (
                                            f"{label}: {flag} missing")
                                    assert "ANTHROPIC_API_KEY" not in sp["env"], (
                                        f"{label}: the D1 key leak")
                                    assert set(sp["env"]) <= allow, (
                                        f"{label}: child env beyond the "
                                        f"allowlist: "
                                        f"{set(sp['env']) - allow}")
                                for row in sink:
                                    assert row["lane"] == "subscription", (
                                        f"{label}: row lane={row['lane']!r} "
                                        f"on a subscription call")
                                    assert row["usd_charged"] == 0.0, (
                                        f"{label}: charged non-zero on "
                                        f"the subscription lane: {row}")
                                    if row["usd"] != row["usd_charged"]:
                                        legacy_usd_violations.append(
                                            (label, row["step"], row["usd"]))
                                    assert row["usd_shadow"] > 0.0, (
                                        f"{label}: shadow vanished — the "
                                        f"cap's figure is gone: {row}")
                            tallies[expect] += 1
                        checked += 1
    base_grid = 4 * 22 * 2 * 7   # 4 global x 22 per-seat (7 seats) x 2 x 7
    assert checked == base_grid + sub_axis_runs
    assert sub_axis_runs > 0                       # the new axis actually ran
    # every outcome class is exercised — a sweep that silently stopped
    # reaching one of them would be a hole, not a pass
    assert tallies["api"] > 0
    assert tallies["subscription"] > 0
    assert tallies["fallback"] > 0                 # the D2 arm actually ran
    # B4-D1 closed (FIX-1): EVERY caller is fall-capable again — generate
    # steps and rank via their published step seats, the analyst via the
    # _ACTIVE_ANALYST holder — so the armed axis splits the absent-bin
    # combos exactly in half: armed falls (labeled), unarmed dies. Nothing
    # in between; a caller losing its fall seam breaks this arithmetic.
    assert sub_axis_runs == 2 * tallies["fallback"]
    assert tallies["blocked"] >= sub_axis_runs - tallies["fallback"]
    # B3-D1's acceptance arm (see the collector note above): every completed
    # subscription row must keep legacy usd == usd_charged. RED until the
    # ranking fix lands; the message names every violating combo.
    assert not legacy_usd_violations, (
        f"{len(legacy_usd_violations)} subscription row(s) carry a lane-blind "
        f"legacy usd (B3-D1) — first 5: {legacy_usd_violations[:5]}")
