"""QA extensions — B2, the Claude API lane + Haiku seat flips. 2026-07-16.

Adversarial pass against the implementer's handoff (trust machinery: money +
provider plumbing — full depth). Covers the seams their B2 tests leave open:

  * json_mode is PROMPT-SHAPED on this lane (no native json_object mode): the
    documented backstop is the caller's parse->validate->CORRECTED-RETRY law.
    Attacked here with the three classic hostile reply shapes (fenced,
    preambled, trailing-prose JSON) — each must take exactly one corrected
    retry (RETRY_CORRECTION riding the second prompt, the json system nudge
    riding BOTH), recover, and bill both attempts at the seat's Haiku prices.
  * The _openai_shaped synthesis + _STOP_REASON_MAP: max_tokens->length is the
    truncation guard's trigger (load-bearing); refusal/tool_use/unknown rows;
    lenient content extraction under malformed content-block arrays; degenerate
    usage tolerance; both cache fields riding the synthesised dict.
  * Cache recording end-to-end: cache_read/cache_creation land in the rank
    cost_sink row and usd_shadow stays UNDISCOUNTED in B2 (the B4 rider).
  * Per-seat shadow math from the SEATS table (Haiku 1.00/5.00 vs gpt-4o
    2.50/10.00) — computed through cost_fields, never a global constant.
  * _ACTIVE_SEAT_CFG (generate.py): the request-scoped seat is armed after the
    gate and ALWAYS disarmed — clean return, transport-error GenerateError,
    validation-exhausted GenerateError, and gate-block (never armed). Nested
    call_llm restores the OUTER seat (stack discipline), and every ledger row
    carries its own step's seat — proven at wire level (inner editor bytes ride
    the anthropic endpoint mid-narrative; the outer retry returns to openai).
  * The state seat's B2 join (gate ruling R1): check_lane preflights before
    any transport; cost derives from the seam (cost_fields, gpt-4o prices);
    the aggregate state_rewrites step row carries the shadow-ledger keys.
  * FIX-1 asymmetry, characterized AS IS (the implementer's stage-preflight
    recommendation is the gate's to rule on): rank/writer lane misconfigs are
    kill-class (raw LaneUnavailable, pinned elsewhere); the analyst's is
    swallowed per-slot into a disclosed $0 'failed' StoryAnalysis, and the
    state seat's into a 'stale' StateRewriteResult — degrade-not-death, zero
    transport either way. (generate_thread_baseline's broad except is the same
    class on the analyst seat.)
  * Doctor: keyless-degrade is a FAIL line with MECHANICALLY zero network
    (no_network recorder); with-key GET /v1/models against the loopback for
    the 401 and 529 shapes; the key value never rides a result line; the
    no-anthropic-seats INFO branches (dormant under the B2 seat map — provider
    is code, not env — probed under a patched all-openai table).
  * ERROR-STRING MISDIRECTION, generate side (rank side characterized in
    test_ranking_validation): an editor-step 401 from the anthropic endpoint
    still reads 'OpenAI rejected the key ... platform.openai.com/api-keys'.
    Pinned as-is with the wire target recorded; the fix is the gate's call.

Offline by construction: loopback fake server or scripted urlopen only; the
autouse conftest guards (scrub_env, loopback_only_network, real_state_tripwire)
stand under everything here. Zero live calls, $0.

B3 CONSCIOUS FLIPS (2026-07-17, QA pass on the subscription lane): 15
assertions here flipped when rank/editor/script's default lane went
subscription. The api-PROVIDER contracts this file pins (request bytes,
retry law, stop_reason map, 401/400 taxonomy, cache recording) are
UNCHANGED — those tests now pin NEWSLENS_LANE_<SEAT>=api, the registered
fall-over, to keep exercising the same wire. Teeth whose trigger was
"subscription is unregistered" are re-expressed on genuinely-unavailable
combos (unknown lanes, openai seats off api, or a subscription seat with an
unresolvable binary). The subscription lane's OWN adversarial suite lives in
test_b3_subscription_lane_qa.py.
"""

from __future__ import annotations

import dataclasses
import json
import time
import urllib.request

import pytest

from conftest import anthropic_envelope
from newslens import analysis, config, doctor, generate, llm, memory_core, ranking

KNOWN_IDS = {1, 2}
TAGS = {"AI regulation": "topic"}


def cluster(ids, title="A story"):
    return {
        "story_title": title, "summary": "What happened.", "item_ids": list(ids),
        "matched_tags": [], "matched_memory": [],
        "world_impact": 5, "world_impact_reason": "Because it matters.",
    }


def ant_native(content, stop="end_turn", inp=1000, out=200,
               cache_creation=0, cache_read=0):
    """The anthropic /v1/messages response as a DICT (scripted-transport twin
    of conftest.anthropic_envelope, which returns bytes for the fake server)."""
    text = json.dumps(content) if isinstance(content, (dict, list)) else str(content)
    return {
        "id": "msg_b2qa", "type": "message", "role": "assistant",
        "model": "claude-haiku-4-5",
        "content": [{"type": "text", "text": text}],
        "stop_reason": stop,
        "usage": {"input_tokens": inp, "output_tokens": out,
                  "cache_creation_input_tokens": cache_creation,
                  "cache_read_input_tokens": cache_read},
    }


OPENAI_CANNED = {
    "choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}],
    "usage": {"prompt_tokens": 1000, "completion_tokens": 200},
}


class _Resp:
    def __init__(self, payload):
        self._b = json.dumps(payload).encode("utf-8")

    def read(self):
        return self._b

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _scripted(monkeypatch, script):
    """Scripted urlopen: consumes `script` in call order. Each entry is a
    payload dict, or a callable(req_body_dict, url) -> payload for by-URL
    branching. Records every request as {'url', 'body', 'req'}."""
    sent = []

    def fake_urlopen(req, timeout=None):
        body = json.loads(req.data.decode("utf-8"))
        sent.append({"url": req.full_url, "body": body, "req": req})
        entry = script.pop(0)
        payload = entry(body, req.full_url) if callable(entry) else entry
        return _Resp(payload)

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    return sent


def _tripwire(monkeypatch):
    calls = []

    def tripwire(req, timeout=None):
        calls.append(req.full_url)
        raise AssertionError("transport reached: " + req.full_url)

    monkeypatch.setattr(urllib.request, "urlopen", tripwire)
    return calls


def _no_sleep(monkeypatch):
    monkeypatch.setattr(time, "sleep", lambda s: None)


HAIKU_1000_200 = 1000 / 1e6 * 1.00 + 200 / 1e6 * 5.00  # = 0.002


# ===========================================================================
# 1. json_mode prompt-shaping under hostile replies -> the corrected-retry law
# ===========================================================================

_GOOD = {"clusters": [cluster([1])]}

_HOSTILE_FIRST_REPLIES = [
    pytest.param("```json\n" + json.dumps(_GOOD) + "\n```", id="fenced"),
    pytest.param("Sure! Here is the JSON you asked for: " + json.dumps(_GOOD),
                 id="preambled"),
    pytest.param(json.dumps(_GOOD) + "\nHope this helps!", id="trailing-prose"),
]


@pytest.mark.parametrize("first_reply", _HOSTILE_FIRST_REPLIES)
def test_rank_hostile_json_reply_takes_one_corrected_retry_and_recovers(
        monkeypatch, first_reply):
    """The documented B2 choice: no native json mode, no silent post-hoc
    repair — a fenced/preambled/trailing-prose reply must fail json.loads and
    take the caller's CORRECTED retry (run-28 law), which recovers. Both
    attempts bill at the rank seat's Haiku prices; the json system nudge rides
    BOTH requests; the retry prompt (and only the retry) carries
    RETRY_CORRECTION anchored to the original prompt.
    B3: pinned to the api fall-over lane (rank defaults to subscription now);
    the corrected-retry law THROUGH the subprocess lane is proven in
    test_b3_subscription_lane_qa."""
    _no_sleep(monkeypatch)
    monkeypatch.setenv("NEWSLENS_LANE_RANK", "api")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-qa")
    sent = _scripted(monkeypatch, [ant_native(first_reply), ant_native(_GOOD)])
    sink = []
    clusters, usage = ranking.call_llm_validated(
        "sk-openai-unused", "BASE-PROMPT", KNOWN_IDS, TAGS, [], cost_sink=sink)
    assert [c["item_ids"] for c in clusters] == [[1]]      # recovered
    assert len(sent) == 2
    assert all(s["url"] == llm.ANTHROPIC_MESSAGES_URL for s in sent)
    assert all(s["body"]["system"] == llm._ANTHROPIC_JSON_SYSTEM for s in sent)
    p1 = sent[0]["body"]["messages"][0]["content"]
    p2 = sent[1]["body"]["messages"][0]["content"]
    assert p1 == "BASE-PROMPT"
    assert p2.startswith("BASE-PROMPT") and ranking.RETRY_CORRECTION in p2
    assert p2 != p1
    # money honesty: the hostile first draw still billed, at HAIKU prices
    assert [(e["step"], e["attempt"]) for e in sink] == [
        ("rank_select", 1), ("rank_select", 2)]
    for e in sink:
        assert e["model"] == "claude-haiku-4-5" and e["lane"] == "api"
        assert e["usd"] == e["usd_charged"] == e["usd_shadow"] \
            == pytest.approx(HAIKU_1000_200)


def test_script_step_no_json_nudge_and_correction_echoes_the_validator(
        monkeypatch):
    """The non-json seats must NOT carry the json system nudge (the writer
    path never sent response_format; its Claude twin is 'no system'), and
    generate's corrected retry echoes the validator's OWN ValueError text.
    B3: script defaults to subscription — the api fall-over is pinned."""
    _no_sleep(monkeypatch)
    monkeypatch.setenv("NEWSLENS_LANE_SCRIPT", "api")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-qa")
    sent = _scripted(monkeypatch, [ant_native("draft one"),
                                   ant_native("draft two")])
    seen = []

    def validate(content):
        seen.append(content)
        if len(seen) == 1:
            raise ValueError("script ran 12 words under the floor")

    content, _ = generate.call_llm("k", "SCRIPT-PROMPT", "script", 400, 0.4,
                                   False, validate=validate)
    assert content == "draft two"
    assert len(sent) == 2
    assert all(s["url"] == llm.ANTHROPIC_MESSAGES_URL for s in sent)
    assert all("system" not in s["body"] for s in sent)   # json_mode off
    p2 = sent[1]["body"]["messages"][0]["content"]
    assert p2.startswith("SCRIPT-PROMPT")
    assert generate.RETRY_CORRECTION_PREFIX in p2
    assert "script ran 12 words under the floor" in p2    # the exact rule
    assert generate._ACTIVE_SEAT_CFG is None              # disarmed after


# ===========================================================================
# 2. The _openai_shaped mapping + stop_reason law (unit hostility)
# ===========================================================================

@pytest.mark.parametrize("stop, expected", [
    ("end_turn", "stop"),
    ("stop_sequence", "stop"),
    ("max_tokens", "length"),          # THE truncation-guard trigger
    ("tool_use", "tool_calls"),
    ("refusal", "content_filter"),
    ("brand_new_reason", "brand_new_reason"),  # unknown passes through
    (None, None),
])
def test_stop_reason_map_rows(stop, expected):
    raw = {"stop_reason": stop} if stop is not None else {}
    assert llm._anthropic_finish_reason(raw) == expected


def test_max_tokens_stop_reason_fires_the_rank_truncation_guard(monkeypatch):
    """Liveness of the load-bearing row end to end: stop_reason='max_tokens'
    must surface as finish_reason 'length' and take the named-truncation
    retry-then-fail path — if the map row breaks, a capped completion would
    instead die as 'malformed LLM output' (the pre-M4 blindness).
    B3: api fall-over pinned — the length-guard is an api-lane-only property
    (`claude -p` manages its own output cap; ADR-0015 known gap)."""
    _no_sleep(monkeypatch)
    monkeypatch.setenv("NEWSLENS_LANE_RANK", "api")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-qa")
    _scripted(monkeypatch, [ant_native('{"clusters": [', stop="max_tokens"),
                            ant_native('{"clusters": [', stop="max_tokens")])
    with pytest.raises(ranking.RankingError) as excinfo:
        ranking.call_llm_validated("k", "p", KNOWN_IDS, TAGS, [])
    msg = str(excinfo.value)
    # Precise naming is the contract: the cap and its value are in the message
    # (the taxonomy wraps it in the malformed retry class — same as pre-B2).
    assert "completion truncated at the max_tokens cap" in msg
    assert str(ranking.MAX_COMPLETION_TOKENS) in msg


@pytest.mark.parametrize("content_value, expected", [
    (None, ""),                                            # missing array
    ([], ""),                                              # empty array
    ("not-a-list", ""),                                    # wrong type
    ([{"type": "text"}], ""),                              # text block, no text key
    ([{"type": "thinking", "thinking": "hmm"}], ""),       # non-text ignored
    ([{"type": "text", "text": "a"}, "garbage",
      {"type": "text", "text": "b"}], "ab"),               # concat, junk skipped
])
def test_anthropic_content_extraction_is_lenient_never_raises(
        content_value, expected):
    raw = {} if content_value is None else {"content": content_value}
    assert llm._anthropic_content(raw) == expected


@pytest.mark.parametrize("usage_value", [None, {}, {"input_tokens": None},
                                         "garbage"])
def test_anthropic_usage_degenerate_shapes_zero_never_raise(usage_value):
    raw = {"usage": usage_value} if usage_value != "garbage" else "garbage"
    u = llm._anthropic_usage(raw if isinstance(raw, dict) else {})
    assert (u.prompt_tokens, u.completion_tokens,
            u.cache_read_tokens, u.cache_creation_tokens) == (0, 0, 0, 0)


def test_openai_shaped_exact_contract():
    """The synthesised dict IS the callers' parse surface and the ledger
    reader's input — pin its exact shape (a missing key here silently zeroes
    a cost or blinds a guard downstream)."""
    usage = llm.Usage(prompt_tokens=7, completion_tokens=3,
                      cache_read_tokens=2, cache_creation_tokens=5)
    native = {"stop_reason": "end_turn"}
    shaped = llm._openai_shaped(usage, "TEXT", "stop", native)
    assert shaped == {
        "choices": [{"message": {"content": "TEXT"}, "finish_reason": "stop"}],
        "usage": {
            "prompt_tokens": 7,
            "completion_tokens": 3,
            "prompt_tokens_details": {"cached_tokens": 2},
            "cache_creation_tokens": 5,
        },
        "_anthropic": native,
    }
    assert shaped["_anthropic"] is native  # forensics ride-along, not a copy


# ===========================================================================
# 3. Cache fields recorded end-to-end; shadow stays UNDISCOUNTED in B2
# ===========================================================================

def test_cache_fields_reach_the_rank_ledger_and_never_discount_shadow(
        fake_api, monkeypatch):
    """B4 reads these ledger fields to engineer caching; B2's law is
    record-don't-discount — a cache-read-heavy reply must not move usd_shadow
    a microdollar off the undiscounted per-seat price.
    B3: api fall-over pinned (rank defaults to subscription)."""
    _no_sleep(monkeypatch)
    monkeypatch.setenv("NEWSLENS_LANE_RANK", "api")
    monkeypatch.setattr(llm, "ANTHROPIC_MESSAGES_URL",
                        fake_api.base_url + "/v1/messages")
    monkeypatch.setenv("ANTHROPIC_API_KEY", fake_api.good_key)
    fake_api.add_route(
        "/v1/messages", status=200,
        body=anthropic_envelope({"clusters": [cluster([1])]},
                                cache_creation=111, cache_read=222),
        content_type="application/json",
    )
    sink = []
    ranking.call_llm_validated("k", "p", KNOWN_IDS, TAGS, [], cost_sink=sink)
    assert len(sink) == 1
    row = sink[0]
    assert row["cache_read_tokens"] == 222
    assert row["cache_creation_tokens"] == 111
    assert row["usd_shadow"] == pytest.approx(HAIKU_1000_200)  # undiscounted
    assert row["usd"] == row["usd_charged"] == row["usd_shadow"]


def test_per_seat_shadow_math_derives_from_the_seat_table():
    """Every seat's cost_fields math must come from ITS row: the three
    anthropic seats at Haiku 1.00/5.00, every openai seat at gpt-4o
    2.50/10.00 — a re-hardcoded global constant on either side breaks here.
    B3 (conscious flip): shadow is LANE-INDEPENDENT (always API-priced);
    charged keys off the lane — equal to shadow on api seats, 0.0 on the
    subscription-default anthropic seats, and back to equal when those seats
    are pinned to the api fall-over."""
    usage = {"prompt_tokens": 1_000_000, "completion_tokens": 1_000_000}
    # B4 flip (conscious): per-MODEL rates — Haiku 1+5 (subscription default,
    # charged 0), Opus 5+25 and Sonnet 3+15 (api: charged == shadow), gpt-4o
    # 2.50+10 for the still-openai seats. The tooth is unchanged: every
    # seat's math comes from ITS row, never a re-hardcoded global.
    per_model = {"claude-haiku-4-5": 1.00 + 5.00,
                 "claude-opus-4-8": 5.00 + 25.00,
                 "claude-sonnet-5": 3.00 + 15.00,
                 "gpt-4o": 2.50 + 10.00}
    for name, cfg in llm.SEATS.items():
        fields = llm.cost_fields(cfg, usage)
        assert fields["usd_shadow"] == pytest.approx(per_model[cfg.model]), name
        if cfg.lane == "subscription":
            assert cfg.model == "claude-haiku-4-5", name
            assert fields["usd_charged"] == 0.0, name
            api_fields = llm.cost_fields(
                dataclasses.replace(cfg, lane="api"), usage)
            assert api_fields["usd_shadow"] == fields["usd_shadow"], name
            assert api_fields["usd_charged"] == api_fields["usd_shadow"], name
        else:
            assert fields["usd_charged"] == fields["usd_shadow"], name  # api
    assert {n for n, c in llm.SEATS.items() if c.provider == "anthropic"} \
        == {"rank", "editor", "script", "writer", "analyst"}
    # ranking's module constants stay pure derivations (no fork):
    assert ranking.RANK_MODEL == llm.SEATS["rank"].model
    assert ranking.RANK_USD_PER_MTOK_IN == llm.SEATS["rank"].usd_per_mtok_in
    assert ranking.RANK_USD_PER_MTOK_OUT == llm.SEATS["rank"].usd_per_mtok_out
    assert ranking.USD_PER_MTOK_IN == ranking.RANK_USD_PER_MTOK_IN
    assert ranking.USD_PER_MTOK_OUT == ranking.RANK_USD_PER_MTOK_OUT


# ===========================================================================
# 4. _ACTIVE_SEAT_CFG — the request-scoped seat's arm/disarm discipline
# ===========================================================================

def test_active_seat_cfg_disarmed_after_clean_editor_call(monkeypatch):
    _no_sleep(monkeypatch)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-qa")
    _scripted(monkeypatch, [ant_native("tightened")])
    assert generate._ACTIVE_SEAT_CFG is None
    generate.call_llm("k", "p", "editor", 100, 0.5, False)
    assert generate._ACTIVE_SEAT_CFG is None


def test_active_seat_cfg_disarmed_after_transport_generate_error(
        fake_api, monkeypatch):
    """GenerateError raised INSIDE the loop (401 fast-fail) must still walk
    the finally — a stuck editor seat would silently re-lane the next direct
    _chat caller onto Haiku. B3: api fall-over pinned (the 401 is an
    api-lane wire shape); the subscription-transport disarm twin lives in
    test_b3_subscription_lane_qa."""
    _no_sleep(monkeypatch)
    monkeypatch.setenv("NEWSLENS_LANE_EDITOR", "api")
    monkeypatch.setattr(llm, "ANTHROPIC_MESSAGES_URL",
                        fake_api.base_url + "/v1/messages")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-wrong-key")
    with pytest.raises(generate.GenerateError):
        generate.call_llm("k", "p", "editor", 100, 0.5, False)
    assert generate._ACTIVE_SEAT_CFG is None


def test_active_seat_cfg_disarmed_after_validation_exhaustion(monkeypatch):
    """GenerateError raised AFTER the loop (both attempts rejected) — the
    other exit path through the finally."""
    _no_sleep(monkeypatch)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-qa")
    _scripted(monkeypatch, [ant_native("bad"), ant_native("bad")])

    def reject(content):
        raise ValueError("always rejected")

    with pytest.raises(generate.GenerateError):
        generate.call_llm("k", "p", "script", 100, 0.4, False, validate=reject)
    assert generate._ACTIVE_SEAT_CFG is None


def test_gate_block_never_arms_the_seat_and_never_transports(
        monkeypatch, tmp_path):
    # B3 re-expression: editor x subscription is registered now, so the gate
    # block is the AVAILABILITY form — the editor's default subscription lane
    # with an unresolvable binary. Same tooth: blocked at the gate, the seat
    # never armed, zero HTTP AND zero subprocess spawns.
    _no_sleep(monkeypatch)
    calls = _tripwire(monkeypatch)
    spawns = []

    def _spawn_tripwire(*a, **k):
        spawns.append(a)
        raise AssertionError("spawned despite a gate block")

    monkeypatch.setattr(llm.subprocess, "run", _spawn_tripwire)
    monkeypatch.setenv("NEWSLENS_CLAUDE_BIN", str(tmp_path / "absent"))
    with pytest.raises(llm.LaneUnavailable):
        generate.call_llm("k", "p", "editor", 100, 0.5, False)
    assert calls == []
    assert spawns == []
    assert generate._ACTIVE_SEAT_CFG is None


def test_nested_call_llm_each_transport_rides_its_own_seat_and_outer_restores(
        monkeypatch):
    """The stack-discipline proof at WIRE level: a narrative call whose
    validator spawns an inner editor call. Transport order must be
    openai (outer#1) -> anthropic (inner, Haiku) -> openai (outer retry) —
    the inner call may not drag the outer retry onto the Claude lane, and the
    shared cost ledger must attribute each row to its own step's seat. This is
    the 'a step ledger row NEVER carries another step's seat' contract under
    re-entrancy, plus the finally's restore-to-PREVIOUS (not to None).
    B3: the inner editor is pinned to the api fall-over so the wire-order
    proof stays HTTP-observable end to end; the cross-TRANSPORT nesting twin
    (openai HTTP outer, claude -p subprocess inner) lives in
    test_b3_subscription_lane_qa."""
    _no_sleep(monkeypatch)
    monkeypatch.setenv("NEWSLENS_LANE_EDITOR", "api")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-qa")

    # B4 flip (conscious): BOTH seats ride the anthropic endpoint now (writer
    # = Opus api, editor pinned to its api fall-over), so the wire-order
    # proof keys on the request BODY's model instead of the URL. The tooth is
    # unchanged: the inner editor call may not drag the outer retry onto the
    # editor's seat, and every ledger row carries its own step's seat.
    def by_body(body, url):
        if body.get("model") == "claude-haiku-4-5":
            return ant_native("inner-edit", inp=10, out=20)
        return ant_native("ok", inp=1000, out=200)

    sent = _scripted(monkeypatch, [by_body, by_body, by_body])
    sink = []
    state = {"inner_ran": False}

    def validate(content):
        if not state["inner_ran"]:
            state["inner_ran"] = True
            inner, _ = generate.call_llm("k", "INNER", "editor", 50, 0.5,
                                         False, cost_sink=sink)
            assert inner == "inner-edit"
            raise ValueError("outer draft rejected once")

    content, _ = generate.call_llm("k", "OUTER", "narrative", 100, 0.3, True,
                                   validate=validate, cost_sink=sink)
    assert content == "ok"
    assert [s["url"] for s in sent] == [llm.ANTHROPIC_MESSAGES_URL] * 3
    assert [s["body"]["model"] for s in sent] == [
        "claude-opus-4-8",              # outer attempt 1 (writer seat)
        "claude-haiku-4-5",             # inner editor
        "claude-opus-4-8",              # outer attempt 2 — RESTORED, not Haiku
    ]
    rows = [(e["step"], e["attempt"], e["model"], e["lane"]) for e in sink]
    assert rows == [
        ("narrative", 1, "claude-opus-4-8", "api"),
        ("editor", 1, "claude-haiku-4-5", "api"),
        ("narrative", 2, "claude-opus-4-8", "api"),
    ]
    # and the money followed each row's own seat (Opus 5/25, Haiku 1/5):
    assert sink[0]["usd"] == pytest.approx(1000 / 1e6 * 5.00 + 200 / 1e6 * 25.00)
    assert sink[1]["usd"] == pytest.approx(10 / 1e6 * 1.00 + 20 / 1e6 * 5.00)
    assert generate._ACTIVE_SEAT_CFG is None


def test_direct_chat_after_an_editor_call_rides_the_writer_seat(monkeypatch):
    """The disarm's observable consequence: a DIRECT _chat call (the
    signature-test/legacy path) after an editor call_llm must ride the
    writer seat (B4: Opus 4.8 -> the anthropic HTTP endpoint), not a leaked
    seat. Still a CROSS-TRANSPORT leak check: the editor call rides its
    default subscription lane (the sandbox stub subprocess — zero HTTP), so
    a leaked editor seat would drag the direct _chat into a subprocess and
    the scripted HTTP recorder would see NOTHING. Exactly one HTTP POST,
    model claude-opus-4-8, proves the disarm."""
    _no_sleep(monkeypatch)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-qa")
    sent = _scripted(monkeypatch, [ant_native("ok")])
    generate.call_llm("k", "p", "editor", 100, 0.5, False)   # stub subprocess
    generate._chat("k", "direct", 100, 0.5, False)
    assert [s["url"] for s in sent] == [llm.ANTHROPIC_MESSAGES_URL]
    assert sent[0]["body"]["model"] == "claude-opus-4-8"
    assert generate._ACTIVE_SEAT_CFG is None


# ===========================================================================
# 5. The state seat's B2 join (gate ruling R1)
# ===========================================================================

def test_state_chat_gate_blocks_before_any_transport(monkeypatch):
    calls = _tripwire(monkeypatch)
    monkeypatch.setenv("NEWSLENS_LANE_STATE", "subscription")
    with pytest.raises(llm.LaneUnavailable) as excinfo:
        memory_core._default_state_chat("k", "prompt")
    assert "NEWSLENS_LANE_STATE" in str(excinfo.value).replace("_\n", "_") or \
        "state" in str(excinfo.value)
    assert calls == []


def test_state_cost_derives_from_the_seam_not_module_constants(monkeypatch):
    """cost == cost_fields(state seat)['usd_charged'] — gpt-4o prices via the
    seam. If a future seat flip re-prices state, this math follows the table."""
    _scripted(monkeypatch, [{
        "choices": [{"message": {"content": json.dumps({"state": "S."})},
                     "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 1000, "completion_tokens": 500},
    }])
    # R-B3a (conscious flip): the default state chat returns a 3-tuple now —
    # (raw, usd_charged, usd_shadow) — so a $0-charged subscription state
    # seat can still ledger its shadow. State is gpt-4o/api this milestone:
    # charged == shadow exactly.
    raw, cost, shadow = memory_core._default_state_chat("k", "prompt")
    assert raw == {"state": "S."}
    fields = llm.cost_fields(
        llm.resolve_seat("state"),
        {"prompt_tokens": 1000, "completion_tokens": 500})
    assert cost == pytest.approx(fields["usd_charged"]) \
        == pytest.approx(0.0075)  # 2.50/10.00
    assert shadow == pytest.approx(fields["usd_shadow"]) == pytest.approx(cost)


def test_state_lane_misconfig_degrades_stale_never_raises(
        migrated_con, monkeypatch):
    """FIX-1 class, state side, UNIT level: rewrite_state's broad except turns
    the gate's LaneUnavailable into a returned 'stale' outcome — prior state
    kept, $0, disclosed detail naming the exception — with ZERO transport.
    B3 UPDATE (FIX-1 ruled and landed): this unit-level degrade now stands
    BEHIND the stage-boundary preflight in generate (_run_generate_body
    checks the state lane at entry and KILLS the run on a config error —
    liveness-proven in test_b3_subscription_lane_qa), so this arm is reached
    only by transient-shaped failures or direct rewrite_state calls."""
    calls = _tripwire(monkeypatch)
    monkeypatch.setenv("NEWSLENS_LANE_STATE", "subscription")
    con = migrated_con
    now = "2026-07-01T00:00:00.000Z"
    cur = con.execute(
        "INSERT INTO memory (topic, status, status_changed_at, created_at,"
        " updated_at) VALUES ('Iran War', 'active', ?, ?, ?)", (now, now, now))
    tid = cur.lastrowid
    con.execute(
        "INSERT INTO thread_deltas (thread_id, edition_date, slot, verdict,"
        " what_happened, significance, cites_json)"
        " VALUES (?, '2026-07-16', 1, 'advances', 'Moved.', 'Matters.',"
        " '[\"S1\"]')", (tid,))
    con.commit()
    res = memory_core.rewrite_state(
        con, tid, "Iran War", "2026-07-16", None, "sk-x",
        "{topic} {date} {ledger}", remaining_usd=1.0)
    assert res.outcome == "stale"
    assert "LaneUnavailable" in res.detail
    assert res.cost_usd == 0.0
    assert calls == []
    assert con.execute("SELECT COUNT(*) FROM thread_state").fetchone()[0] == 0


def test_state_rewrites_step_row_carries_the_shadow_ledger_keys(
        migrated_con, monkeypatch):
    """Gate ruling R1's ledger half: the aggregate state_rewrites row in
    report.steps carries model/lane/usd_shadow/usd_charged off the seam (state
    = gpt-4o/api this milestone, so shadow == charged == the accumulated
    spend). Without these keys the cost dashboard forks the moment the state
    seat's lane flips."""
    from test_generate import seed_briefing, slot

    con = migrated_con
    now = "2026-07-01T00:00:00.000Z"
    cur = con.execute(
        "INSERT INTO memory (topic, status, status_changed_at, created_at,"
        " updated_at) VALUES ('Iran War', 'active', ?, ?, ?)", (now, now, now))
    tid = cur.lastrowid
    con.commit()
    seed_briefing(con, "2026-07-16", [slot(1, mem=["Iran War"])],
                  narrative="Published.")
    con.execute(
        "INSERT INTO analysis_briefs (date, slot, tier, status, brief_json,"
        " model, cost_usd) VALUES ('2026-07-16', 1, 'full', 'valid', ?,"
        " 'gpt-4o', 0.0)",
        (json.dumps({"brief": {"arc": {
            "delta": "advances", "what_happened": "Moved.",
            "significance": "Matters.", "cites": ["S1"]}}}),))
    con.commit()

    def state_chat(key, prompt):
        return ({"state": f"It moved ({memory_core.human_date('2026-07-16')})."},
                0.02)

    report = generate.GenReport(date="2026-07-16", variant=generate.ACTIVE_VOICE)
    generate.run_memory_pass(
        con, "2026-07-16", "sk-qa-fake", cap=1.0, spent=0.0,
        briefs_by_slot={1: {"brief": {"arc": {
            "delta": "advances", "what_happened": "Moved.",
            "significance": "Matters.", "cites": ["S1"]}}}},
        slots=[slot(1, mem=["Iran War"])], report=report, state_chat=state_chat)
    rows = [s for s in report.steps if s["step"] == "state_rewrites"]
    assert len(rows) == 1
    row = rows[0]
    state_cfg = llm.resolve_seat("state")
    assert row["model"] == state_cfg.model == "gpt-4o"
    assert row["lane"] == state_cfg.lane == "api"
    assert row["usd"] == row["usd_shadow"] == row["usd_charged"] \
        == pytest.approx(0.02)


# ===========================================================================
# 6. FIX-1 asymmetry — the analyst's per-slot swallow, characterized AS IS
# ===========================================================================

def test_analyst_lane_misconfig_degrades_per_slot_not_run_killing(
        migrated_con, monkeypatch):
    """FIX-1, analyst side, UNIT level. B4 flip (conscious): analyst x
    subscription is a REGISTERED lane now (Sonnet is anthropic), so the
    misconfig that proves the ladder's containment regrows on a junk lane —
    same raw LaneUnavailable from call_analysis_model, same catch into a
    disclosed $0 'failed' StoryAnalysis. B3 UPDATE (FIX-1 ruled and
    landed): the stage-boundary preflights — run_analysis entry and
    _run_generate_body entry — now kill the run on this config error BEFORE
    any per-slot ladder runs (born-red in test_b3_subscription_lane.py and
    liveness-proven at generate level in test_b3_subscription_lane_qa), so
    this per-slot arm is reached only by transient-shaped failures. The pin
    stays: the ladder's containment behavior must not silently change."""
    calls = _tripwire(monkeypatch)
    monkeypatch.setenv("NEWSLENS_LANE_ANALYST", "junk")
    cfg = config.SourcesConfig(
        sources=[config.Source(name="Outlet A", rss_url="https://a.example/f")],
        interests_granular=["AI regulation"],
    )
    # One cluster item -> a C1 excerpt source, so the thin-material rung
    # passes and the ladder actually REACHES the synthesis chat (whose gate
    # then raises). The fetch stub fails offline; no socket is ever touched.
    migrated_con.execute(
        "INSERT INTO source_items (id, source_type, outlet, url, title,"
        " fetched_at) VALUES (1, 'rss', 'Outlet A', 'https://a.example/1',"
        " 'Story', '2026-07-16T00:00:00.000Z')")
    migrated_con.commit()

    def offline_fetch(url, **kw):
        raise OSError("offline stub — no article fetch in this test")

    sa = analysis.analyze_story(
        migrated_con, "2026-07-16", 1,
        {"story_title": "T", "summary": "S", "item_ids": [1]},
        "full", cfg, openai_key="sk-x", pplx_key="",
        remaining_usd=1.0, memory_lines=[], prior=[],
        fetch=offline_fetch, sleep=lambda s: None)
    assert sa.outcome == "failed"
    assert "LaneUnavailable" in sa.detail
    assert sa.cost_usd == 0.0
    assert calls == []


# ===========================================================================
# 7. Doctor — the ANTHROPIC_API_KEY check's whole surface
# ===========================================================================

def test_doctor_keyless_anthropic_is_a_fail_line_with_zero_network(no_network):
    """Keyless degrade: the seats that need the key are NAMED, the fix is
    actionable (console URL + monthly-cap nudge), and the check makes NO
    network attempt — proven by the socket recorder, not by reading the code."""
    results = doctor.check_anthropic_key({})
    assert [r.status for r in results] == [doctor.FAIL]
    text = results[0].text
    for seat in ("editor", "rank", "script"):
        assert seat in text
    assert "console.anthropic.com" in text
    assert "monthly cap" in text
    assert no_network == []


def test_doctor_rejected_anthropic_key_names_the_rotation_fix(
        fake_api, monkeypatch):
    monkeypatch.setattr(doctor, "ANTHROPIC_MODELS_URL",
                        fake_api.base_url + "/v1/models")
    results = doctor.check_anthropic_key({"ANTHROPIC_API_KEY": "sk-wrong"})
    assert [r.status for r in results] == [doctor.FAIL]
    assert "rejected (HTTP 401)" in results[0].text
    assert "console.anthropic.com" in results[0].text
    assert "sk-wrong" not in results[0].text          # never echoed


def test_doctor_anthropic_529_overload_is_a_retry_hint_not_a_key_blame(
        fake_api, monkeypatch):
    monkeypatch.setattr(doctor, "ANTHROPIC_MODELS_URL",
                        fake_api.base_url + "/v1/models")
    fake_api.add_route("/v1/messages", status=200, body=b"{}")  # unused; isolation
    fake_api.add_route(
        "/v1/models", status=529,
        body=json.dumps({"type": "error",
                         "error": {"type": "overloaded_error",
                                   "message": "Overloaded"}}).encode(),
        content_type="application/json")
    results = doctor.check_anthropic_key({"ANTHROPIC_API_KEY": "sk-any"})
    assert [r.status for r in results] == [doctor.FAIL]
    assert "HTTP 529" in results[0].text
    assert "status.anthropic.com" in results[0].text
    assert "rejected" not in results[0].text          # not blamed on the key


def test_doctor_valid_key_pass_line_never_carries_the_value(
        fake_api, monkeypatch):
    monkeypatch.setattr(doctor, "ANTHROPIC_MODELS_URL",
                        fake_api.base_url + "/v1/models")
    results = doctor.check_anthropic_key(
        {"ANTHROPIC_API_KEY": fake_api.good_key})
    assert [r.status for r in results] == [doctor.PASS]
    assert "read-only GET /v1/models OK" in results[0].text
    assert fake_api.good_key not in results[0].text
    gets = [r for r in fake_api.recorded if r["method"] == "GET"]
    assert [g["path"] for g in gets] == ["/v1/models"]  # exactly one probe


def test_doctor_no_anthropic_seats_branches_are_info_zero_network(
        no_network, monkeypatch):
    """The 'no seat routes to anthropic' INFO branches. DORMANT under the B2
    seat map by construction: env overrides change a seat's LANE, never its
    PROVIDER, so resolve_seat can only ever return anthropic for rank/editor/
    script — the branch is reachable only under a future all-openai seat map
    (probed here by patching SEATS). Pinned so the branch's honesty (say the
    key is unused / not needed; make no call) survives until that day."""
    all_openai = {
        name: dataclasses.replace(
            cfg, provider="openai", model="gpt-4o",
            usd_per_mtok_in=llm.GPT4O_USD_PER_MTOK_IN,
            usd_per_mtok_out=llm.GPT4O_USD_PER_MTOK_OUT)
        for name, cfg in llm.SEATS.items()
    }
    monkeypatch.setattr(llm, "SEATS", all_openai)
    keyless = doctor.check_anthropic_key({})
    assert [r.status for r in keyless] == [doctor.INFO]
    assert "not needed" in keyless[0].text
    keyed = doctor.check_anthropic_key({"ANTHROPIC_API_KEY": "sk-set"})
    assert [r.status for r in keyed] == [doctor.INFO]
    assert "unused" in keyed[0].text
    assert no_network == []


# ===========================================================================
# 8. Error-string misdirection, generate side (characterized, not fixed)
# ===========================================================================

def test_editor_401_from_anthropic_wire_names_the_anthropic_console(
        fake_api, monkeypatch):
    """FIX B (gate, landed), generate twin of the rank-side flip: the editor
    seat 401s at the ANTHROPIC endpoint (x-api-key rejected), and call_llm's
    taxonomy now names the RIGHT console — 'Anthropic rejected the key ...
    console.anthropic.com/settings/keys' — not the OpenAI one. Right class
    (fast-fail, no retry, no sleep) AND right signpost. The wire target is
    recorded to prove which lane failed. B3: api fall-over pinned (editor
    defaults to subscription; a 401 is an api-lane wire shape)."""
    _no_sleep(monkeypatch)
    monkeypatch.setenv("NEWSLENS_LANE_EDITOR", "api")
    monkeypatch.setattr(llm, "ANTHROPIC_MESSAGES_URL",
                        fake_api.base_url + "/v1/messages")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-wrong-anthropic-key")
    with pytest.raises(generate.GenerateError) as excinfo:
        generate.call_llm("sk-openai-fine", "p", "editor", 100, 0.5, False)
    msg = str(excinfo.value)
    assert "Anthropic rejected the key" in msg                   # the RIGHT provider
    assert "console.anthropic.com/settings/keys" in msg          # the RIGHT console
    assert "platform.openai.com" not in msg                      # no longer misdirects
    posts = [r for r in fake_api.recorded if r["method"] == "POST"]
    assert [p["path"] for p in posts] == ["/v1/messages"]        # the true wire


def test_editor_400_credit_exhaustion_from_anthropic_wire_names_billing(
        fake_api, monkeypatch):
    """FIX C generate twin (born-red per the wiring law): the editor seat 400s
    at the ANTHROPIC endpoint with 'credit balance is too low' (key valid, can't
    spend). call_llm's dedicated anthropic billing arm fires AHEAD of the generic
    4xx arm — immediate GenerateError, no retry, no sleep — and names the
    anthropic billing console + the doctor blind-spot honesty line.
    B3: api fall-over pinned — credit exhaustion is an api-lane failure mode
    (the subscription lane bills nothing per call)."""
    _no_sleep(monkeypatch)
    monkeypatch.setenv("NEWSLENS_LANE_EDITOR", "api")
    monkeypatch.setattr(llm, "ANTHROPIC_MESSAGES_URL",
                        fake_api.base_url + "/v1/messages")
    monkeypatch.setenv("ANTHROPIC_API_KEY", fake_api.good_key)
    fake_api.add_route(
        "/v1/messages", status=400,
        body=json.dumps({"type": "error", "error": {
            "type": "invalid_request_error",
            "message": "Your credit balance is too low to access the Anthropic "
                       "API."}}).encode("utf-8"),
        content_type="application/json")
    with pytest.raises(generate.GenerateError) as excinfo:
        generate.call_llm("sk-openai-fine", "p", "editor", 100, 0.5, False)
    msg = str(excinfo.value)
    assert "Anthropic account has no available credit" in msg
    assert "add credits at console.anthropic.com billing" in msg
    assert "cannot catch this" in msg                            # doctor blind-spot honesty
    assert "credit balance is too low" in msg                    # the actionable detail
    assert "OpenAI" not in msg
    posts = [r for r in fake_api.recorded if r["method"] == "POST"]
    assert [p["path"] for p in posts] == ["/v1/messages"]        # non-retryable: 1 post


def test_lane_unavailable_message_names_the_seat_default_and_registered_lanes():
    """FIX A's message-hygiene contract, re-expressed for B3 (conscious flip:
    rank x subscription is registered now, so the probe moves to a combo that
    is STILL unregistered — the openai state seat forced onto subscription).
    The hint must name the seat's ACTUAL SEATS default, list the REAL
    registered-lane roster including the landed subscription lane, and carry
    no stale forward-pointer ('lands in B3' — it landed). The sweep's 'no
    registered implementation' anchor is preserved."""
    cfg = llm.resolve_seat("state", {"NEWSLENS_LANE_STATE": "subscription"})
    with pytest.raises(llm.LaneUnavailable) as excinfo:
        llm.chat(llm.LaneRequest(cfg, "p", 0, 10, True, "ua", "k"))
    msg = str(excinfo.value)
    assert "no registered implementation" in msg          # the sweep's stable anchor
    assert "lands in B2" not in msg                        # landed
    assert "lands in B3" not in msg                        # landed too (B3 flip)
    assert "anthropic/subscription" in msg                 # the roster is current
    assert "openai/gpt-4o on the api lane" in msg          # state's ACTUAL default
    assert "openai runs ONLY on the api lane" in msg       # why the combo is dead
    assert "NEWSLENS_LANE_STATE" in msg                    # names the fix var
