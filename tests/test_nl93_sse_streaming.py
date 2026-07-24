"""NL-93 — SSE streaming for the api lane's long-output calls. 2026-07-24.

The api-lane anthropic provider was non-streaming urllib: an xhigh writer call
(adaptive thinking + 16k max_tokens) sat minutes with zero response bytes and
died RemoteDisconnected (reproduced 3/3 arms x retries, sandboxed AND
unsandboxed). This suite is the in-process proof surface for the streaming fix:
we simulate SSE frames (no network — the autouse conftest guards stand under
everything) and pin the three load-bearing contracts:

  * SHAPE (constraint 2): the streamed event deltas reconstruct the SAME native
    /v1/messages dict the non-streaming json.load produced, so _anthropic_content
    / _anthropic_usage / _anthropic_finish_reason and the whole downstream
    validation + cost + retry surface run UNCHANGED — cost must not move by a
    token (usage from message_start + message_delta), stop_reason 'max_tokens'
    must still map to finish_reason 'length' (the truncation guard).

  * FAIL-LOUD (constraint 3): a mid-stream transport death, a malformed SSE
    frame, an in-band error event, or a stream that ends without message_stop is
    a TRANSPORT error — it takes the callers' transport retry (original bytes),
    never the CORRECTED-RETRY arm (ValueError family) and never a silent
    truncated-but-returned text. The exception-class contract is the sharp edge:
    json.JSONDecodeError IS a ValueError, so a naive stream parse would misroute
    a corruption into the corrected-retry arm — pinned here it must not.

  * SCOPE (constraint 4): stream the LONG-call class only — a per-call
    max_tokens >= _STREAM_MIN_MAX_TOKENS budget (NOT the seat's thinking flag).
    Every proven short/medium api path (rank/editor/script/state/follow_altitude)
    stays byte-identical and non-streaming — its request bytes carry no `stream`
    key and it reads via json.load exactly as today.

BORN-RED accounting (HEAD-run of THIS file against the non-streaming provider):
20 red / 4 green. The 20 streaming-behaviour tests fail against HEAD (the
provider calls json.load on the SSE body -> JSONDecodeError, emits no `stream`
flag, or references the not-yet-existing llm._should_stream /
llm._STREAM_MIN_MAX_TOKENS -> AttributeError). The 4 CARRIED INVARIANTS
(born-green) are the preservation tests that pass at HEAD because a short/small
call never streamed there anyway: test_small_budget_thinking_call_does_not_stream
(section 1), test_short_seat_does_not_stream_and_reads_via_json_load and
test_rank_seat_below_threshold_does_not_stream (section 5), and
test_stream_seat_http_error_still_classified_as_http (section 5). NOTE the two
predicate/coupling guards in section 5 (test_should_stream_predicate_matrix,
test_streaming_bar_stays_below_the_writer_and_analyst_budgets) are BORN-RED, not
green — they reference symbols absent at HEAD (AttributeError). Editable install:
`.venv/bin/python` resolves newslens from src/ (the tree) — see the report's
__file__ pin.
"""

from __future__ import annotations

import dataclasses
import http.client
import io
import json
import socket
import time
import urllib.error
import urllib.request

import pytest

from newslens import generate, llm, ranking


# ---------------------------------------------------------------------------
# SSE fakes — in-process frame simulation (no network)
# ---------------------------------------------------------------------------

def _sse_frame(obj: dict) -> str:
    """One SSE event: an `event:` name line + a `data:` JSON line + blank line
    (the Anthropic wire shape). The provider dispatches off the data JSON's own
    `type`, so the event: line is cosmetic — we send it anyway for realism."""
    return f"event: {obj.get('type','')}\ndata: {json.dumps(obj)}\n\n"


def _sse_bytes(events) -> bytes:
    return "".join(_sse_frame(e) for e in events).encode("utf-8")


class _StreamResp:
    """A fake streaming HTTPResponse: readline() over a byte buffer, a context
    manager, and iterable — the surface _accumulate_sse uses. `raise_after`
    injects a transport exception mid-stream (after N readlines) to model a
    RemoteDisconnected / socket.timeout. `read()` is present so the HEAD
    (non-streaming) path's json.load has something to (wrongly) consume — it
    gets the SSE text and fails visibly, which is exactly the born-red signal."""

    def __init__(self, data: bytes, raise_after=None, raise_exc=None):
        self._buf = io.BytesIO(data)
        self._raise_after = raise_after
        self._raise_exc = raise_exc
        self._n = 0

    def readline(self, *a):
        if self._raise_after is not None and self._n >= self._raise_after:
            raise self._raise_exc
        self._n += 1
        return self._buf.readline()

    def read(self, *a):
        return self._buf.read(*a)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def __iter__(self):
        return self

    def __next__(self):
        line = self.readline()
        if not line:
            raise StopIteration
        return line


def _scripted_stream(monkeypatch, responses):
    """urlopen returns the next fake response (a _StreamResp, a _JsonResp, or a
    callable(body,url)->resp, or an exception instance/class to raise), recording
    each request's parsed body + timeout."""
    sent = []

    def fake_urlopen(req, timeout=None):
        body = json.loads(req.data.decode("utf-8"))
        sent.append({"url": req.full_url, "body": body, "timeout": timeout})
        entry = responses.pop(0)
        if isinstance(entry, BaseException) or (
                isinstance(entry, type) and issubclass(entry, BaseException)):
            raise entry
        return entry(body, req.full_url) if callable(entry) else entry

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    return sent


class _JsonResp:
    """The NON-streaming fake (json.load(resp) target) — the twin of the b2 QA
    _Resp, used to prove the short path is untouched and to cross-check that a
    streamed reconstruction equals the non-streamed dict token-for-token."""

    def __init__(self, payload):
        self._b = json.dumps(payload).encode("utf-8")

    def read(self, *a):
        return self._b

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _no_sleep(monkeypatch):
    monkeypatch.setattr(time, "sleep", lambda s: None)


# ---------------------------------------------------------------------------
# Event builders
# ---------------------------------------------------------------------------

def stream_events(text, *, stop="end_turn", inp=2000, out_final=500,
                  cache_read=0, cache_creation=0, thinking=None,
                  model="claude-opus-4-8"):
    """A well-formed writer/analyst SSE event list. Text is split across TWO
    deltas so accumulation (not just single-block echo) is what's proven. Usage
    is faithful to the wire: input + cache land in message_start; the final
    cumulative output_tokens lands in message_delta."""
    evts = [{
        "type": "message_start",
        "message": {
            "id": "msg_nl93", "type": "message", "role": "assistant",
            "model": model, "content": [], "stop_reason": None,
            "usage": {"input_tokens": inp, "output_tokens": 1,
                      "cache_read_input_tokens": cache_read,
                      "cache_creation_input_tokens": cache_creation},
        },
    }]
    idx = 0
    if thinking is not None:
        evts += [
            {"type": "content_block_start", "index": idx,
             "content_block": {"type": "thinking", "thinking": ""}},
            {"type": "content_block_delta", "index": idx,
             "delta": {"type": "thinking_delta", "thinking": thinking}},
            {"type": "content_block_stop", "index": idx},
        ]
        idx += 1
    mid = len(text) // 2
    evts += [
        {"type": "content_block_start", "index": idx,
         "content_block": {"type": "text", "text": ""}},
        {"type": "ping"},  # keep-alive between deltas — must be ignored
        {"type": "content_block_delta", "index": idx,
         "delta": {"type": "text_delta", "text": text[:mid]}},
        {"type": "content_block_delta", "index": idx,
         "delta": {"type": "text_delta", "text": text[mid:]}},
        {"type": "content_block_stop", "index": idx},
        {"type": "message_delta",
         "delta": {"stop_reason": stop, "stop_sequence": None},
         "usage": {"output_tokens": out_final}},
        {"type": "message_stop"},
    ]
    return evts


def nonstream_native(text, *, stop="end_turn", inp=2000, out_final=500,
                     cache_read=0, cache_creation=0, model="claude-opus-4-8"):
    """The non-streaming /v1/messages dict the SAME generation would return —
    the token-for-token cross-check target for the streamed reconstruction."""
    return {
        "id": "msg_nl93", "type": "message", "role": "assistant", "model": model,
        "content": [{"type": "text", "text": text}], "stop_reason": stop,
        "usage": {"input_tokens": inp, "output_tokens": out_final,
                  "cache_read_input_tokens": cache_read,
                  "cache_creation_input_tokens": cache_creation},
    }


def writer_cfg(**over):
    """The writer seat forced onto the api lane (the failing fall-over path)."""
    return dataclasses.replace(llm.SEATS["writer"], lane="api", **over)


def writer_req(prompt="write the edition", max_tokens=16000, json_mode=False,
               cfg=None):
    return llm.LaneRequest(
        cfg=cfg or writer_cfg(), prompt=prompt, temperature=0.0,
        max_tokens=max_tokens, json_mode=json_mode, user_agent="nl93-qa",
        api_key="sk-openai-seam-ignored", url=ranking.OPENAI_CHAT_URL,
    )


@pytest.fixture(autouse=True)
def _ant_key(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-nl93-qa")


# ===========================================================================
# 1. Streaming happy path + reconstruction (BORN-RED)
# ===========================================================================

def test_writer_seat_streams_and_request_body_carries_stream_true(monkeypatch):
    sent = _scripted_stream(monkeypatch, [
        _StreamResp(_sse_bytes(stream_events("Hello world edition.",
                                             thinking="reasoning...")))])
    resp = llm._anthropic_provider(writer_req())
    assert resp.content == "Hello world edition."
    # the long-call path MUST request streaming
    assert sent[0]["body"].get("stream") is True
    assert sent[0]["url"] == llm.ANTHROPIC_MESSAGES_URL


def test_streamed_text_deltas_accumulate_in_order(monkeypatch):
    _scripted_stream(monkeypatch, [
        _StreamResp(_sse_bytes(stream_events(
            "The quick brown fox jumps over the lazy dog.")))])
    resp = llm._anthropic_provider(writer_req())
    assert resp.content == "The quick brown fox jumps over the lazy dog."


def test_streamed_usage_reconstructs_input_from_start_output_from_delta(
        monkeypatch):
    _scripted_stream(monkeypatch, [
        _StreamResp(_sse_bytes(stream_events(
            "body", inp=3210, out_final=987,
            cache_read=100, cache_creation=42)))])
    resp = llm._anthropic_provider(writer_req())
    assert resp.usage.prompt_tokens == 3210          # message_start
    assert resp.usage.completion_tokens == 987       # FINAL message_delta (not the 1)
    assert resp.usage.cache_read_tokens == 100
    assert resp.usage.cache_creation_tokens == 42


def test_thinking_deltas_never_leak_into_content(monkeypatch):
    _scripted_stream(monkeypatch, [
        _StreamResp(_sse_bytes(stream_events(
            "PROSE ONLY", thinking="secret chain of thought")))])
    resp = llm._anthropic_provider(writer_req())
    assert resp.content == "PROSE ONLY"
    assert "secret" not in resp.content


def test_analyst_seat_streams_at_its_real_budget(monkeypatch):
    # the analyst's real budget (ANALYSIS_MAX_TOKENS=6000) sits at/above the bar,
    # so its adaptive-thinking call streams; json_mode extraction still runs.
    cfg = dataclasses.replace(llm.SEATS["analyst"], lane="api")
    sent = _scripted_stream(monkeypatch, [
        _StreamResp(_sse_bytes(stream_events(
            json.dumps({"summary": "ok"}), model="claude-sonnet-5")))])
    req = writer_req(prompt="analyse", max_tokens=6000, json_mode=True, cfg=cfg)
    resp = llm._anthropic_provider(req)
    assert sent[0]["body"].get("stream") is True
    assert json.loads(resp.content) == {"summary": "ok"}


def test_small_budget_thinking_call_does_not_stream(monkeypatch):
    # CARRIED INVARIANT (born-green): a small call never streamed at HEAD either,
    # so this passes pre-diff — it guards the scope boundary, not new behaviour.
    # A thinking seat asked for only a few hundred tokens is FAST (thinking bills
    # against max_tokens) — it stays on the proven non-streaming json.load path,
    # which is exactly what keeps the cheap byte-pin calls byte-identical.
    sent = _scripted_stream(monkeypatch, [
        _JsonResp(nonstream_native("short", model="claude-opus-4-8"))])
    resp = llm._anthropic_provider(writer_req(max_tokens=512))
    assert "stream" not in sent[0]["body"]
    assert resp.content == "short"


# ===========================================================================
# 2. Cost faithfulness — reconstruction == non-streaming, to the token (BORN-RED)
# ===========================================================================

def test_streamed_reconstruction_costs_identical_to_nonstreaming(monkeypatch):
    text = "An edition of the news."
    kw = dict(inp=4096, out_final=2500, cache_read=512, cache_creation=64)
    cfg = writer_cfg()

    _scripted_stream(monkeypatch, [
        _StreamResp(_sse_bytes(stream_events(text, **kw)))])
    streamed = llm._anthropic_provider(writer_req())

    # the non-streaming twin: same tokens, a short seat so json.load is taken
    short = dataclasses.replace(llm.SEATS["editor"], lane="api")
    _scripted_stream(monkeypatch, [
        _JsonResp(nonstream_native(text, model="claude-haiku-4-5", **kw))])
    nonstreamed = llm._anthropic_provider(
        writer_req(max_tokens=1000, cfg=short))  # 1000 < 8000, no thinking

    cs = llm.cost_fields(cfg, streamed.raw["usage"])
    cn = llm.cost_fields(cfg, nonstreamed.raw["usage"])
    assert cs["usd_shadow"] == cn["usd_shadow"]
    assert cs["cache_read_tokens"] == cn["cache_read_tokens"] == 512
    assert cs["cache_creation_tokens"] == cn["cache_creation_tokens"] == 64
    assert streamed.raw["usage"]["prompt_tokens"] == 4096
    assert streamed.raw["usage"]["completion_tokens"] == 2500


def test_stream_max_tokens_stop_reason_maps_to_length_truncation_guard(
        monkeypatch):
    _scripted_stream(monkeypatch, [
        _StreamResp(_sse_bytes(stream_events("partial", stop="max_tokens")))])
    resp = llm._anthropic_provider(writer_req())
    # the load-bearing map: max_tokens -> length, the callers' truncation trigger
    assert resp.finish_reason == "length"


@pytest.mark.parametrize("stop,expected", [
    ("end_turn", "stop"), ("stop_sequence", "stop"),
    ("max_tokens", "length"), ("refusal", "content_filter"),
])
def test_stream_stop_reason_map_rows(monkeypatch, stop, expected):
    _scripted_stream(monkeypatch, [
        _StreamResp(_sse_bytes(stream_events("t", stop=stop)))])
    resp = llm._anthropic_provider(writer_req())
    assert resp.finish_reason == expected


# ===========================================================================
# 3. Fail-loud — the exception-class contract (BORN-RED)
# ===========================================================================

def _assert_transport_class(exc):
    """A mid-stream failure must land in the callers' `except Exception`
    TRANSPORT arm — so it is NOT a member of the corrected-retry tuple."""
    assert not isinstance(exc, (ValueError, KeyError, IndexError, TypeError)), (
        f"{type(exc).__name__} would misroute to the CORRECTED-RETRY arm")
    assert isinstance(exc, Exception)


def test_incomplete_stream_without_message_stop_is_transport_error(monkeypatch):
    # drop the terminal message_stop -> incomplete stream
    events = stream_events("half an edition")[:-1]
    _scripted_stream(monkeypatch, [_StreamResp(_sse_bytes(events))])
    with pytest.raises(Exception) as ei:
        llm._anthropic_provider(writer_req())
    _assert_transport_class(ei.value)


def test_malformed_sse_data_frame_is_transport_not_valueerror(monkeypatch):
    # a data: line whose JSON is broken. json.JSONDecodeError IS a ValueError —
    # this is the sharp edge: it must be re-raised as a transport class.
    good = _sse_frame(stream_events("x")[0])            # message_start
    bad = "event: content_block_delta\ndata: {not json,,,\n\n"
    _scripted_stream(monkeypatch, [
        _StreamResp((good + bad).encode("utf-8"))])
    with pytest.raises(Exception) as ei:
        llm._anthropic_provider(writer_req())
    _assert_transport_class(ei.value)
    assert not isinstance(ei.value, json.JSONDecodeError)


def test_stream_error_event_is_transport_error(monkeypatch):
    events = [
        stream_events("x")[0],   # message_start
        {"type": "error",
         "error": {"type": "overloaded_error", "message": "Overloaded"}},
    ]
    _scripted_stream(monkeypatch, [_StreamResp(_sse_bytes(events))])
    with pytest.raises(Exception) as ei:
        llm._anthropic_provider(writer_req())
    _assert_transport_class(ei.value)


def test_malformed_delta_wrong_type_is_transport_not_typeerror(monkeypatch):
    # F1 (gate): a text_delta whose "text" is a non-string (here an int) makes the
    # accumulator's `blk["text"] += ...` raise TypeError. TypeError is in the
    # corrected-retry tuple, so a leaked TypeError would MISROUTE a stream
    # corruption to the model-correction arm — it must surface as a transport class.
    events = [
        stream_events("x")[0],                              # message_start
        {"type": "content_block_start", "index": 0,
         "content_block": {"type": "text", "text": ""}},
        {"type": "content_block_delta", "index": 0,
         "delta": {"type": "text_delta", "text": 5}},        # int, not str
        {"type": "message_delta",
         "delta": {"stop_reason": "end_turn", "stop_sequence": None},
         "usage": {"output_tokens": 10}},
        {"type": "message_stop"},
    ]
    _scripted_stream(monkeypatch, [_StreamResp(_sse_bytes(events))])
    with pytest.raises(Exception) as ei:
        llm._anthropic_provider(writer_req())
    _assert_transport_class(ei.value)
    assert not isinstance(ei.value, TypeError)


def test_mixed_block_index_types_is_transport_not_typeerror(monkeypatch):
    # F1 (gate): two content blocks whose indices are an int (0) and a str ("1")
    # make the POST-LOOP sorted(blocks) raise TypeError (py3 can't order int<>str).
    # This is the reconstruction-side TypeError the wrap must also catch — same
    # misroute risk, must surface as a transport class.
    events = [
        stream_events("x")[0],                              # message_start
        {"type": "content_block_start", "index": 0,
         "content_block": {"type": "text", "text": ""}},
        {"type": "content_block_delta", "index": 0,
         "delta": {"type": "text_delta", "text": "a"}},
        {"type": "content_block_start", "index": "1",
         "content_block": {"type": "text", "text": ""}},
        {"type": "content_block_delta", "index": "1",
         "delta": {"type": "text_delta", "text": "b"}},
        {"type": "message_delta",
         "delta": {"stop_reason": "end_turn", "stop_sequence": None},
         "usage": {"output_tokens": 10}},
        {"type": "message_stop"},
    ]
    _scripted_stream(monkeypatch, [_StreamResp(_sse_bytes(events))])
    with pytest.raises(Exception) as ei:
        llm._anthropic_provider(writer_req())
    _assert_transport_class(ei.value)
    assert not isinstance(ei.value, TypeError)


def test_midstream_socket_timeout_propagates_as_timeout(monkeypatch):
    # readline raises socket.timeout mid-stream (idle-gap exceeded). It must
    # propagate as a TimeoutError-class transport error, unchanged.
    resp = _StreamResp(_sse_bytes(stream_events("x")),
                       raise_after=2, raise_exc=socket.timeout("timed out"))
    _scripted_stream(monkeypatch, [resp])
    with pytest.raises(Exception) as ei:
        llm._anthropic_provider(writer_req())
    _assert_transport_class(ei.value)
    assert isinstance(ei.value, (TimeoutError, socket.timeout))


def test_midstream_remote_disconnected_propagates(monkeypatch):
    # the exact failure NL-93 fixes, now happening mid-stream instead of at the
    # blocking read — it must stay a transport class (ConnectionError family).
    exc = http.client.RemoteDisconnected("Remote end closed connection")
    resp = _StreamResp(_sse_bytes(stream_events("x")),
                       raise_after=1, raise_exc=exc)
    _scripted_stream(monkeypatch, [resp])
    with pytest.raises(Exception) as ei:
        llm._anthropic_provider(writer_req())
    _assert_transport_class(ei.value)


# ===========================================================================
# 4. End-to-end through generate.call_llm — the retry-CLASSIFICATION wiring proof
# ===========================================================================

def test_streamed_writer_end_to_end_validates_and_returns(monkeypatch):
    _no_sleep(monkeypatch)
    monkeypatch.setenv("NEWSLENS_LANE_WRITER", "api")
    _scripted_stream(monkeypatch, [
        _StreamResp(_sse_bytes(stream_events(
            "A full edition of the news.", thinking="planning")))])
    content, usage = generate.call_llm(
        "sk-openai", "write it", "narrative", generate.NARRATIVE_MAX_TOKENS,
        generate.NARRATIVE_TEMPERATURE, False)
    assert content == "A full edition of the news."
    assert usage["completion_tokens"] == 500


def test_midstream_death_takes_transport_retry_not_corrected_retry(monkeypatch):
    # THE classification proof: an incomplete stream on attempt 1 must retry the
    # ORIGINAL bytes (no correction suffix appended) — the transport arm, not the
    # corrected-retry arm. Two failures -> GenerateError.
    _no_sleep(monkeypatch)
    monkeypatch.setenv("NEWSLENS_LANE_WRITER", "api")
    truncated = _sse_bytes(stream_events("half")[:-1])   # no message_stop
    sent = _scripted_stream(monkeypatch, [
        _StreamResp(truncated), _StreamResp(truncated)])
    with pytest.raises(generate.GenerateError):
        generate.call_llm(
            "sk-openai", "write it", "narrative", generate.NARRATIVE_MAX_TOKENS,
            generate.NARRATIVE_TEMPERATURE, False)
    assert len(sent) == 2
    # transport retry re-sends the ORIGINAL user prompt — no RETRY_CORRECTION echo
    assert sent[1]["body"]["messages"] == sent[0]["body"]["messages"]
    assert "RETRY" not in json.dumps(sent[1]["body"])


# ===========================================================================
# 5. Scope preservation + the predicate/coupling guards
#   CARRIED INVARIANTS (born-green): test_short_seat_does_not_stream_and_reads_
#   via_json_load, test_rank_seat_below_threshold_does_not_stream,
#   test_stream_seat_http_error_still_classified_as_http.
#   BORN-RED (AttributeError at HEAD — reference symbols added by this diff):
#   test_should_stream_predicate_matrix (needs llm._should_stream),
#   test_streaming_bar_stays_below_the_writer_and_analyst_budgets (needs
#   llm._STREAM_MIN_MAX_TOKENS).
# ===========================================================================

def test_short_seat_does_not_stream_and_reads_via_json_load(monkeypatch):
    # editor: 4600 max_tokens -> below the streaming bar -> NON-streaming.
    cfg = dataclasses.replace(llm.SEATS["editor"], lane="api")
    sent = _scripted_stream(monkeypatch, [
        _JsonResp(nonstream_native("tightened", model="claude-haiku-4-5"))])
    resp = llm._anthropic_provider(
        writer_req(prompt="tighten", max_tokens=4600, cfg=cfg))
    assert "stream" not in sent[0]["body"]     # no stream flag on the short path
    assert resp.content == "tightened"


def test_rank_seat_below_threshold_does_not_stream(monkeypatch):
    cfg = dataclasses.replace(llm.SEATS["rank"], lane="api")
    sent = _scripted_stream(monkeypatch, [
        _JsonResp(nonstream_native('{"clusters": []}',
                                   model="claude-haiku-4-5"))])
    llm._anthropic_provider(
        writer_req(prompt="rank", max_tokens=3000, json_mode=True, cfg=cfg))
    assert "stream" not in sent[0]["body"]


def test_should_stream_predicate_matrix():
    # the per-call max_tokens BUDGET decides. Real writer (16000) and analyst
    # (6000) stream; every short/medium seat's real budget stays below the bar.
    writer = writer_cfg()                                           # 16000
    analyst = dataclasses.replace(llm.SEATS["analyst"], lane="api")  # 6000
    editor = dataclasses.replace(llm.SEATS["editor"], lane="api")    # 4600
    rank = dataclasses.replace(llm.SEATS["rank"], lane="api")        # 3000
    assert llm._should_stream(writer, 16000) is True
    assert llm._should_stream(analyst, 6000) is True                 # at the bar
    assert llm._should_stream(editor, 4600) is False
    assert llm._should_stream(rank, 3000) is False
    # a thinking seat asked for a tiny budget is fast -> does NOT stream
    assert llm._should_stream(writer, 512) is False
    # the boundary is inclusive at _STREAM_MIN_MAX_TOKENS
    assert llm._should_stream(editor, llm._STREAM_MIN_MAX_TOKENS) is True
    assert llm._should_stream(editor, llm._STREAM_MIN_MAX_TOKENS - 1) is False


def test_streaming_bar_stays_below_the_writer_and_analyst_budgets():
    # COUPLING TRIPWIRE (NL-93 residual): llm is a leaf and cannot import the
    # NARRATIVE_/ANALYSIS_MAX_TOKENS constants, so the bar is documented, not
    # referenced. If a future edit trims either budget below the bar, the seat
    # would silently stop streaming and could RemoteDisconnect again — this test
    # turns that regression red at the source instead.
    from newslens import analysis as _an, generate as _gen
    assert _gen.NARRATIVE_MAX_TOKENS >= llm._STREAM_MIN_MAX_TOKENS
    assert _an.ANALYSIS_MAX_TOKENS >= llm._STREAM_MIN_MAX_TOKENS
    # and the editor budget stays BELOW the bar (it must not start streaming —
    # it is a fast Haiku call and its byte-pinned tests depend on json.load).
    assert _gen.EDITOR_MAX_TOKENS < llm._STREAM_MIN_MAX_TOKENS


def test_stream_seat_http_error_still_classified_as_http(monkeypatch):
    # a non-200 on the streaming seat raises HTTPError at urlopen (before any
    # SSE read) — the callers' HTTPError arm handles it exactly as today. This
    # is a CARRIED INVARIANT: the streaming change only touches the 200 body.
    err = urllib.error.HTTPError(
        llm.ANTHROPIC_MESSAGES_URL, 529, "Overloaded", {},
        io.BytesIO(b'{"type":"error"}'))
    _scripted_stream(monkeypatch, [err])
    with pytest.raises(urllib.error.HTTPError) as ei:
        llm._anthropic_provider(writer_req())
    assert ei.value.code == 529
