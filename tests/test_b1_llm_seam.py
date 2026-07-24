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
    # B2: rank flipped to the Claude API lane (claude-haiku-4-5). The request is
    # now the anthropic Messages shape (x-api-key, anthropic-version, max_tokens
    # required, a system nudge for json_mode instead of response_format, no
    # thinking/effort on a Haiku mechanical seat), POSTed to the anthropic
    # endpoint (the lane reads its own endpoint + credential, ignoring the
    # openai offline-seam url the caller still passes).
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-fake")
    # B3: rank DEFAULTS to the subscription lane now; this test asserts the
    # anthropic API-lane request bytes (the registered fall-over), so pin it.
    monkeypatch.setenv("NEWSLENS_LANE_RANK", "api")
    seen = _capture(monkeypatch)
    ranking._post_chat("sk-x", "hello")
    body = seen["body"]
    assert body["model"] == "claude-haiku-4-5"
    assert body["temperature"] == 0
    assert body["max_tokens"] == ranking.MAX_COMPLETION_TOKENS
    assert body["messages"] == [{"role": "user", "content": "hello"}]
    assert "system" in body                      # json_mode nudge (no json_object mode)
    assert "response_format" not in body
    assert "thinking" not in body and "output_config" not in body  # Haiku: mechanical
    assert _hdr(seen["req"], "x-api-key") == "sk-ant-fake"
    assert _hdr(seen["req"], "anthropic-version") == llm.ANTHROPIC_VERSION
    assert _hdr(seen["req"], "User-Agent") == ranking.USER_AGENT
    assert seen["url"] == llm.ANTHROPIC_MESSAGES_URL
    assert seen["timeout"] == llm.SEATS["rank"].timeout_s == 90


def test_generate_chat_routes_through_seam_as_writer_seat(monkeypatch):
    # item C (2026-07-17): the writer defaults to the SUBSCRIPTION lane now, but
    # its Claude API lane is the registered fall-over and its bytes MUST stay
    # correct — pin NEWSLENS_LANE_WRITER=api to exercise that wire. Same proof:
    # _chat routes through the seam and the request is shaped by the SEAT row —
    # the anthropic Messages body: temperature OMITTED (sampling=False; Opus 4.8
    # 400s on it), thinking adaptive + effort xhigh, max_tokens passed through,
    # POSTed to the anthropic endpoint (req.url ignored), api timeout 600s.
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-fake")
    monkeypatch.setenv("NEWSLENS_LANE_WRITER", "api")
    seen = _capture(monkeypatch)
    generate._chat("sk-x", "hello", 512, 0.7, True)
    body = seen["body"]
    assert body["model"] == "claude-opus-4-8"
    assert "temperature" not in body            # sampling=False — never a 400
    assert body["max_tokens"] == 512
    assert body["thinking"] == {"type": "adaptive"}
    assert body["output_config"] == {"effort": "xhigh"}
    assert "budget_tokens" not in json.dumps(body)
    assert body["system"] == llm._ANTHROPIC_JSON_SYSTEM  # json nudge, no prefix
    assert "response_format" not in body        # anthropic body, not openai
    assert _hdr(seen["req"], "User-Agent") == generate.WRITER_UA
    assert _hdr(seen["req"], "x-api-key") == "sk-ant-fake"
    # the anthropic lane reads its own endpoint; the openai offline-seam url
    # the caller still passes is ignored.
    assert seen["url"] == llm.ANTHROPIC_MESSAGES_URL
    assert seen["timeout"] == llm.SEATS["writer"].timeout_s == 600


def test_generate_chat_omits_response_format_when_not_json_mode(monkeypatch):
    monkeypatch.setenv("NEWSLENS_LANE_WRITER", "api")  # item C: exercise the api fall-over bytes
    seen = _capture(monkeypatch)
    generate._chat("sk-x", "hi", 300, 0.5, False)
    assert "response_format" not in seen["body"]


def test_analysis_chat_routes_through_seam_as_analyst_seat(monkeypatch):
    # item C (2026-07-17): analyst defaults to SUBSCRIPTION now; its api lane is
    # the registered fall-over whose bytes must stay correct — pin
    # NEWSLENS_LANE_ANALYST=api to exercise that wire. Same routing proof on the
    # anthropic body: no temperature (Sonnet 5 400s on it), adaptive thinking at
    # effort high, ANALYSIS_MAX_TOKENS (6000 — thinking headroom), api timeout 240s.
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-fake")
    monkeypatch.setenv("NEWSLENS_LANE_ANALYST", "api")
    seen = _capture(monkeypatch)
    analysis._analysis_chat("sk-x", "hello")
    body = seen["body"]
    assert body["model"] == "claude-sonnet-5"
    assert "temperature" not in body            # sampling=False — never a 400
    assert body["max_tokens"] == analysis.ANALYSIS_MAX_TOKENS == 6000
    assert body["thinking"] == {"type": "adaptive"}
    assert body["output_config"] == {"effort": "high"}
    assert "budget_tokens" not in json.dumps(body)
    assert "response_format" not in body
    assert _hdr(seen["req"], "User-Agent") == analysis.ANALYSIS_UA
    assert seen["url"] == llm.ANTHROPIC_MESSAGES_URL
    assert seen["timeout"] == llm.SEATS["analyst"].timeout_s == 240


# ---------------------------------------------------------------------------
# fail-loud ruling — a lane with no registered provider raises, never a
# silent wrong-lane call
# ---------------------------------------------------------------------------

def test_unavailable_lane_fails_loud_naming_the_fix():
    # B4 flip (conscious, QA re-pin): the writer is ANTHROPIC now, so
    # writer x subscription is a REGISTERED lane (the gate/principal's
    # override path — see the resolve assert below). The fail-loud case moves
    # to a still-openai seat forced off the api lane. 2026-07-17 the state seat
    # flipped to anthropic (option a), so synthesis is the LONE remaining openai
    # seat (openai runs ONLY on api). Still fail-loud, still names the fix.
    cfg = llm.resolve_seat("synthesis", {"NEWSLENS_LANE": "subscription"})
    assert cfg.lane == "subscription" and cfg.provider == "openai"
    with pytest.raises(llm.LaneUnavailable) as exc:
        llm.chat(llm.LaneRequest(cfg, "p", 0, 10, True, "ua", "k"))
    assert "NEWSLENS_LANE" in str(exc.value)
    # the conscious regrow, stated positively: NEWSLENS_LANE_WRITER=subscription
    # is now a valid registered anthropic lane (ADR-0016 §3 — the lane-ruling
    # override must work without a code change). resolve keeps everything but
    # the lane; the provider registry has a row for it.
    w = llm.resolve_seat("writer", {"NEWSLENS_LANE_WRITER": "subscription"})
    assert w.provider == "anthropic" and w.lane == "subscription"
    assert w.model == "claude-opus-4-8"          # only the lane moved
    llm._select_provider(w)                      # registered — does NOT raise


def test_per_seat_lane_override_wins_over_global():
    env = {"NEWSLENS_LANE": "api", "NEWSLENS_LANE_RANK": "subscription"}
    assert llm.resolve_seat("rank", env).lane == "subscription"
    assert llm.resolve_seat("writer", env).lane == "api"


# ---------------------------------------------------------------------------
# config defaults — the current stack is the expressed default (a guard so
# B2's model/lane flips are deliberate, never accidental)
# ---------------------------------------------------------------------------

def test_seat_map_after_b2_haiku_flip():
    # B4 flip + 2026-07-17: the content seats' lanes. WRITER -> Opus 4.8 and
    # ANALYST -> Sonnet 5, and item C (field-proven edition 7) flipped BOTH onto
    # the subscription lane, keeping their models (the api lane is the registered
    # fall-over). rank/editor/script are Haiku on subscription; state flipped to
    # Haiku/subscription too (option a); synthesis is the LONE remaining
    # gpt-4o/api seat. This guard makes every model/lane flip deliberate.
    haiku_sub = {"rank", "editor", "script"}
    for name, cfg in llm.SEATS.items():
        if name in haiku_sub:
            assert cfg.provider == "anthropic", name
            assert cfg.model == "claude-haiku-4-5", name
            assert cfg.lane == "subscription", name
        elif name == "follow_altitude":
            # RESOLVER LANE FIX (2026-07-20): the interactive resolver seat is the
            # one anthropic seat defaulting to the API lane (subscription is its
            # fall-over) — same Haiku model, different transport by design.
            assert cfg.provider == "anthropic", name
            assert cfg.model == "claude-haiku-4-5", name
            assert cfg.lane == "api", name
        elif name == "writer":
            assert cfg.provider == "anthropic" and cfg.lane == "subscription"
            assert cfg.model == "claude-opus-4-8"
        elif name == "analyst":
            assert cfg.provider == "anthropic" and cfg.lane == "subscription"
            assert cfg.model == "claude-sonnet-5"
        elif name == "state":
            # 2026-07-17 ruling (option a): the memory/state seat is now Haiku on
            # the subscription lane — the last content seat off gpt-4o.
            assert cfg.provider == "anthropic", name
            assert cfg.model == "claude-haiku-4-5", name
            assert cfg.lane == "subscription", name
        else:
            assert cfg.provider == "openai", name
            assert cfg.model == "gpt-4o", name
            assert cfg.lane == "api", name
    assert "state" in llm.SEATS  # R1: the state/memory seat joined the table


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
    # rank is Haiku now ($1.00 in / $5.00 out per MTok) — per-seat prices. B3:
    # SEATS["rank"] DEFAULTS to the subscription lane, so pin an api-lane copy to
    # assert the api invariant (shadow == charged). The subscription case (charged
    # == 0.0) is the very next test.
    from dataclasses import replace
    usage = {"prompt_tokens": 1_000_000, "completion_tokens": 0}
    fields = llm.cost_fields(replace(llm.SEATS["rank"], lane="api"), usage)
    assert fields["lane"] == "api"
    assert fields["model"] == "claude-haiku-4-5"
    assert fields["usd_shadow"] == pytest.approx(1.00)
    assert fields["usd_charged"] == pytest.approx(1.00)


def test_cost_fields_records_cache_read_tokens():
    usage = {"prompt_tokens": 100, "completion_tokens": 10,
             "prompt_tokens_details": {"cached_tokens": 40}}
    assert llm.cost_fields(llm.SEATS["rank"], usage)["cache_read_tokens"] == 40


def test_cost_fields_subscription_lane_charges_zero_shadow_holds():
    from dataclasses import replace
    sub = replace(llm.SEATS["rank"], lane="subscription")
    usage = {"prompt_tokens": 1_000_000, "completion_tokens": 0}
    fields = llm.cost_fields(sub, usage)
    assert fields["usd_shadow"] == pytest.approx(1.00)   # shadow still binds (Haiku)
    assert fields["usd_charged"] == 0.0                   # subscription = $0


# ---------------------------------------------------------------------------
# ledger wiring — the lane/shadow keys reach the cost_sink entries additively
# (legacy `usd` retained for back-compat)
# ---------------------------------------------------------------------------

def test_rank_cost_sink_gains_lane_and_shadow_keys(monkeypatch):
    good = {
        "choices": [{"message": {"content": json.dumps(
            {"clusters": [{"story_title": "T", "summary": "S",
                           "item_ids": [ranking.encode_rank_key(1)],  # NL-70: keys-only model output
                           "matched_tags": [],
                           "matched_memory": [], "world_impact": 5,
                           "world_impact_reason": "r"}]})},
            "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 1000, "completion_tokens": 200},
    }
    monkeypatch.setattr(ranking, "_post_chat", lambda key, prompt: good)
    # B3: rank defaults to subscription; pin the api fall-over so this asserts
    # the api-lane ledger row (charged == shadow). The subscription-lane row
    # (charged == 0) is proven in test_b3_subscription_lane.py.
    monkeypatch.setenv("NEWSLENS_LANE_RANK", "api")
    sink = []
    ranking.call_llm_validated("sk-x", "p", {1}, {}, [], cost_sink=sink)
    assert sink, "cost_sink recorded no attempt"
    e = sink[0]
    assert e["lane"] == "api" and e["model"] == "claude-haiku-4-5"  # B2: Haiku
    assert "usd_shadow" in e and "usd_charged" in e
    assert e["usd"] == e["usd_charged"]        # legacy key preserved, per-seat priced


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
    # item C (2026-07-17): the narrative row names the Opus writer seat on its
    # DEFAULT lane — subscription now, so usd_charged is 0.0 while usd_shadow
    # stays API-priced (Opus $5/$25).
    assert e["lane"] == "subscription" and e["model"] == "claude-opus-4-8"
    assert "usd_shadow" in e and "usd_charged" in e
    assert e["usd_shadow"] > 0.0 and e["usd_charged"] == 0.0
    assert e["usd"] == e["usd_charged"] == 0.0  # legacy key == charged (subscription)
