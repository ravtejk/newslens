"""B1 provider-seam liveness — the wiring proof.

The seam refactor deliberately does NOT disturb the existing suite (it stubs
generate._chat / ranking._post_chat / analysis._analysis_chat at the function
level, or redirects ranking.OPENAI_CHAT_URL at a loopback fake server). Per
ENGINEERING.md "claims of wiring travel with proof … enforcement that lands
without disturbing any existing test is suspicious by default," these tests
fail if any of the three transports stops routing through llm.py, and they
pin the fail-loud / shadow-ledger / config-default rulings the seam is built
to (DECISIONS.md 2026-07-16).
"""

from __future__ import annotations

import json
import urllib.request

import pytest

from newslens import analysis, generate, llm, ranking


# ---------------------------------------------------------------------------
# helpers — capture the urllib Request the seam actually builds
# ---------------------------------------------------------------------------

class _Resp:
    def __init__(self, payload):
        self._b = json.dumps(payload).encode()

    def read(self):
        return self._b

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


_CANNED = {
    "choices": [{"message": {"content": "{}"}, "finish_reason": "stop"}],
    "usage": {"prompt_tokens": 10, "completion_tokens": 5},
}


def _capture(monkeypatch, payload=None):
    """Stub urllib.request.urlopen (the module llm calls through) and capture
    the Request object + timeout of the single POST."""
    seen = {}

    def fake_urlopen(req, timeout=None):
        seen["req"] = req
        seen["url"] = req.full_url
        seen["body"] = json.loads(req.data.decode())
        seen["timeout"] = timeout
        return _Resp(payload or _CANNED)

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    return seen


def _hdr(req, name):
    for k, v in req.header_items():
        if k.lower() == name.lower():
            return v
    return None


# ---------------------------------------------------------------------------
# transport routing — each historical function routes through llm.chat, and
# the request is byte-faithful to the pre-seam POST (model/temp/tokens/UA/url/
# timeout all sourced correctly)
# ---------------------------------------------------------------------------

def test_post_chat_routes_through_seam_as_rank_seat(monkeypatch):
    seen = _capture(monkeypatch)
    ranking._post_chat("sk-x", "hello")
    assert seen["body"]["model"] == "gpt-4o"
    assert seen["body"]["temperature"] == 0
    assert seen["body"]["max_tokens"] == ranking.MAX_COMPLETION_TOKENS
    assert seen["body"]["response_format"] == {"type": "json_object"}
    assert _hdr(seen["req"], "User-Agent") == ranking.USER_AGENT
    assert seen["url"] == ranking.OPENAI_CHAT_URL
    assert seen["timeout"] == llm.SEATS["rank"].timeout_s == 90


def test_generate_chat_routes_through_seam_as_writer_seat(monkeypatch):
    seen = _capture(monkeypatch)
    generate._chat("sk-x", "hello", 512, 0.7, True)
    assert seen["body"]["model"] == "gpt-4o"
    assert seen["body"]["temperature"] == 0.7
    assert seen["body"]["max_tokens"] == 512
    assert seen["body"]["response_format"] == {"type": "json_object"}
    assert _hdr(seen["req"], "User-Agent") == generate.WRITER_UA
    # generate has always POSTed via ranking.OPENAI_CHAT_URL (the offline seam)
    assert seen["url"] == ranking.OPENAI_CHAT_URL
    assert seen["timeout"] == llm.SEATS["writer"].timeout_s == 120


def test_generate_chat_omits_response_format_when_not_json_mode(monkeypatch):
    seen = _capture(monkeypatch)
    generate._chat("sk-x", "hi", 300, 0.5, False)
    assert "response_format" not in seen["body"]


def test_analysis_chat_routes_through_seam_as_analyst_seat(monkeypatch):
    seen = _capture(monkeypatch)
    analysis._analysis_chat("sk-x", "hello")
    assert seen["body"]["model"] == "gpt-4o"
    assert seen["body"]["temperature"] == 0.2
    assert seen["body"]["max_tokens"] == analysis.ANALYSIS_MAX_TOKENS
    assert seen["body"]["response_format"] == {"type": "json_object"}
    assert _hdr(seen["req"], "User-Agent") == analysis.ANALYSIS_UA
    assert seen["timeout"] == llm.SEATS["analyst"].timeout_s == 90


# ---------------------------------------------------------------------------
# fail-loud ruling — a lane with no registered provider raises, never a
# silent wrong-lane call
# ---------------------------------------------------------------------------

def test_unavailable_lane_fails_loud_naming_the_fix():
    cfg = llm.resolve_seat("rank", {"NEWSLENS_LANE": "subscription"})
    assert cfg.lane == "subscription"
    with pytest.raises(llm.LaneUnavailable) as exc:
        llm.chat(llm.LaneRequest(cfg, "p", 0, 10, True, "ua", "k"))
    assert "NEWSLENS_LANE" in str(exc.value)


def test_per_seat_lane_override_wins_over_global():
    env = {"NEWSLENS_LANE": "api", "NEWSLENS_LANE_RANK": "subscription"}
    assert llm.resolve_seat("rank", env).lane == "subscription"
    assert llm.resolve_seat("writer", env).lane == "api"


# ---------------------------------------------------------------------------
# config defaults — the current stack is the expressed default (a guard so
# B2's model/lane flips are deliberate, never accidental)
# ---------------------------------------------------------------------------

def test_every_seat_defaults_to_gpt4o_openai_api():
    for name, cfg in llm.SEATS.items():
        assert cfg.provider == "openai", name
        assert cfg.model == "gpt-4o", name
        assert cfg.lane == "api", name


def test_default_env_is_behaviour_neutral():
    assert llm.resolve_seat("rank", {}) == llm.SEATS["rank"]
    assert llm.resolve_seat("writer", {}) == llm.SEATS["writer"]


def test_fallback_flag_is_opt_in_default_off():
    assert llm.fallback_armed({}) is False
    assert llm.fallback_armed({"NEWSLENS_LANE_FALLBACK": "api"}) is True
    assert llm.fallback_armed({"NEWSLENS_LANE_FALLBACK": "none"}) is False


# ---------------------------------------------------------------------------
# shadow ledger — usd_shadow always from the QA-pinned table; on the api lane
# usd_charged == usd_shadow; cache-read tokens recorded
# ---------------------------------------------------------------------------

def test_cost_fields_api_lane_shadow_equals_charged():
    usage = {"prompt_tokens": 1_000_000, "completion_tokens": 0}
    fields = llm.cost_fields(llm.SEATS["rank"], usage)
    assert fields["lane"] == "api"
    assert fields["model"] == "gpt-4o"
    assert fields["usd_shadow"] == pytest.approx(2.50)
    assert fields["usd_charged"] == pytest.approx(2.50)


def test_cost_fields_records_cache_read_tokens():
    usage = {"prompt_tokens": 100, "completion_tokens": 10,
             "prompt_tokens_details": {"cached_tokens": 40}}
    assert llm.cost_fields(llm.SEATS["rank"], usage)["cache_read_tokens"] == 40


def test_cost_fields_subscription_lane_charges_zero_shadow_holds():
    from dataclasses import replace
    sub = replace(llm.SEATS["rank"], lane="subscription")
    usage = {"prompt_tokens": 1_000_000, "completion_tokens": 0}
    fields = llm.cost_fields(sub, usage)
    assert fields["usd_shadow"] == pytest.approx(2.50)   # shadow still binds
    assert fields["usd_charged"] == 0.0                   # subscription = $0


# ---------------------------------------------------------------------------
# ledger wiring — the lane/shadow keys reach the cost_sink entries additively
# (legacy `usd` retained for back-compat)
# ---------------------------------------------------------------------------

def test_rank_cost_sink_gains_lane_and_shadow_keys(monkeypatch):
    good = {
        "choices": [{"message": {"content": json.dumps(
            {"clusters": [{"story_title": "T", "summary": "S",
                           "item_ids": [1], "matched_tags": [],
                           "matched_memory": [], "world_impact": 5,
                           "world_impact_reason": "r"}]})},
            "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 1000, "completion_tokens": 200},
    }
    monkeypatch.setattr(ranking, "_post_chat", lambda key, prompt: good)
    sink = []
    ranking.call_llm_validated("sk-x", "p", {1}, {}, [], cost_sink=sink)
    assert sink, "cost_sink recorded no attempt"
    e = sink[0]
    assert e["lane"] == "api" and e["model"] == "gpt-4o"
    assert "usd_shadow" in e and "usd_charged" in e
    assert e["usd"] == e["usd_charged"]        # legacy key preserved


def test_generate_cost_sink_gains_lane_and_shadow_keys(monkeypatch):
    resp = {"choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 1000, "completion_tokens": 200}}
    monkeypatch.setattr(generate, "_chat",
                        lambda key, prompt, mt, temp, jm: resp)
    sink = []
    generate.call_llm("sk-x", "p", "narrative", 500, 0.5, False, cost_sink=sink)
    assert sink, "cost_sink recorded no attempt"
    e = sink[0]
    assert e["step"] == "narrative"
    assert e["lane"] == "api" and e["model"] == "gpt-4o"
    assert "usd_shadow" in e and "usd_charged" in e
    assert e["usd"] == e["usd_charged"]        # legacy key preserved
