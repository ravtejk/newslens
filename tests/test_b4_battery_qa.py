"""QA born-red pins + adversarial pass — B4: writer→Opus 4.8, analyst→Sonnet 5,
prompt caching, register-spec prompt law, battery harness. 2026-07-17.

Trust machinery (money + provider plumbing + the principal's battery runner):
full depth. What this file pins that the re-pinned B1-B3 suites do not:

  * THE 400 TEETH, as correctness, not style: temperature is OMITTED for the
    Claude 4.6+ seats (Opus 4.8 writer / Sonnet 5 analyst reject it with a
    400) while the Haiku api fall-over and the gpt-4o seats KEEP theirs — the
    contrast in one test, so a global sampling regression cannot pass half.
    budget_tokens NEVER appears in any anthropic body (removed on 4.7+ — a
    reintroduction is a 400 on every flipped-seat call).
  * THE CACHE SPLIT, end to end on the REAL variant-A template: the writer's
    law prefix (REGISTER LAW + BANNED CHARMS + the DATED CALLBACK
    requirement) rides ABOVE the split sentinel, reaches the wire as the
    cache_control:{ephemeral} system block, and system+user reassemble to the
    byte-identical full prompt (the law text is never mutated by the split).
    A sentinel-drift regression — law text sliding below the sentinel — would
    silently move the law out of the cached prefix; the position pin bites it.
  * PROVIDER-GATED REVERT: SEATS["writer"] flipped back to the gpt-4o row
    sends the prompt as ONE user message, byte-identical to the pre-B4 openai
    body — the split never leaks into a revert.
  * SUBSCRIPTION FOLD: a writer forced onto the (now registered) subscription
    lane still sees its law — the prefix rides --append-system-prompt, never
    dropped. NEWSLENS_LANE_WRITER=subscription resolves + dispatches without
    a code change (ADR-0016 §3: the gate/principal's override path).
  * THE B4 RIDER (from B2 QA): cache_read/cache_creation are RECORDED nonzero
    through the writer's ledger row while usd_shadow stays UNDISCOUNTED —
    the money guard never under-counts on an unproven hit.
  * R-B4a LIVENESS: WRITER_MODEL / ANALYSIS_MODEL / STATE_MODEL derive from
    SEATS — proven by a clean subprocess in which the seat is deleted before
    the caller imports: KeyError, never a stale literal.
  * NEWSLENS_MODEL_<SEAT>: swaps ONLY the model string (provider/lane/
    thinking/effort/sampling/prices/timeouts identical), reaches the wire,
    and the seam shadow keeps pricing at the SEAT's table (the documented
    battery caveat — the arm's real rate lives in the battery manifest).
  * .format() SAFETY: hostile brace-laden slot content (titles/excerpts with
    {placeholders}, {0}, {a[b]}) renders through the MODIFIED template
    verbatim and splits/ships without a format-time explosion.
  * THE BATTERY HARNESS, adversarially: dry-run makes ZERO transport calls
    and ZERO filesystem writes (tripwired at urlopen + subprocess.run + a
    recursive DATA_DIR snapshot); --run refuses keyless before any spend;
    the record is opened via db.connect_readonly ONLY (db.connect is a
    tripwire) and a readonly connection genuinely cannot write (bitten
    directly); the cap gate's cumulative math skips the arm that would cross
    and still admits a later cheaper arm; artifacts land under the
    sandbox-redirected DATA_DIR/battery tree; a failing arm is disclosed
    while others continue; a missing briefing row refuses.

Offline by construction: scripted urlopen / recording stub shims only; the
autouse conftest guards (scrub_env, sandbox_paths, loopback_only_network,
real_state_tripwire) stand under everything here. Zero live calls, $0.

LIVENESS PROOFS RUN THIS PASS (comment-out procedure, language-anchored
restore, hash-verified): the `if cfg.sampling:` gate, the `_split_cache_prefix`
provider gate, and the battery cap-gate skip arm — each broken in source made
the named test(s) here fail, then the source was restored byte-identical
(sha256 checked). Recorded in the QA report.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import os
import subprocess
import sys
import urllib.request
from pathlib import Path

import pytest

from conftest import anthropic_envelope
from test_generate import A_DAY, seed_briefing, slot, stories_payload

from newslens import analysis, battery, config, db, generate, llm, memory_core, paths, ranking

PROTOTYPE_ROOT = Path(__file__).resolve().parents[1]


# ---------------------------------------------------------------------------
# capture / scripting helpers
# ---------------------------------------------------------------------------

class _Resp:
    def __init__(self, b: bytes):
        self._b = b

    def read(self):
        return self._b

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _capture(monkeypatch, reply=None):
    """Capture every urlopen POST; answer with `reply` (bytes or a callable
    (body, url) -> bytes). Defaults to a minimal anthropic success."""
    seen = []

    def fake_urlopen(req, timeout=None):
        body = json.loads(req.data.decode("utf-8"))
        seen.append({"url": req.full_url, "body": body, "data": req.data,
                     "timeout": timeout, "req": req})
        r = reply(body, req.full_url) if callable(reply) else reply
        return _Resp(r or anthropic_envelope("{}"))

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    return seen


def _transport_tripwire(monkeypatch):
    """Any HTTP call or subprocess spawn is an immediate failure."""
    calls = []

    def no_http(req, timeout=None):
        calls.append(("http", req.full_url))
        raise AssertionError("HTTP transport reached: " + req.full_url)

    def no_spawn(*a, **k):
        calls.append(("spawn", a))
        raise AssertionError("subprocess spawned")

    monkeypatch.setattr(urllib.request, "urlopen", no_http)
    monkeypatch.setattr(llm.subprocess, "run", no_spawn)
    return calls


def _data_snapshot():
    """Recursive (path, size, mtime_ns) set of the SANDBOX data dir — the
    zero-writes oracle for dry-run/keyless battery invocations."""
    root = paths.DATA_DIR
    if not Path(root).exists():
        return frozenset()
    return frozenset(
        (str(p), p.stat().st_size, p.stat().st_mtime_ns)
        for p in Path(root).rglob("*") if p.is_file())


def _seed_sandbox_record(date=A_DAY, slots=None, hostile=False):
    """Migrate + seed the SANDBOXED paths.DB_PATH (what battery's
    db.connect_readonly() opens) with a briefing row for `date`."""
    if slots is None:
        titles = ["Fed hikes {rates} to {0}% — {a[b]} say {'k': 1}",
                  "Plain second story"] if hostile else None
        slots = [slot(1, title=titles[0]) if titles else slot(1),
                 slot(2, title=titles[1]) if titles else slot(2)]
    db.migrate()
    con = db.connect()
    try:
        seed_briefing(con, date, slots, narrative="Published.")
        if hostile:
            con.execute(
                "UPDATE source_items SET raw_excerpt = ? WHERE id = 1",
                ("Excerpt with braces {x} and {} and %s and {1:>8}.",))
            con.commit()
    finally:
        con.close()
    return slots


def _guard_sanction(monkeypatch):
    """battery.main() self-sanctions real paths (it is a real entrypoint,
    like cli/doctor). Pin the flag's pre-test value so the sanction can
    never leak past this test into the rest of the suite."""
    monkeypatch.setattr(paths, "_REAL_PATHS_ALLOWED",
                        paths._REAL_PATHS_ALLOWED)


# ===========================================================================
# 1. The 400 teeth — sampling omission is per-seat CORRECTNESS, with the
#    keep-side contrast in the same test; budget_tokens never exists
# ===========================================================================

def test_sampling_omitted_for_claude46_seats_kept_for_haiku_and_gpt4o(
        monkeypatch):
    """One test, both directions: the flipped seats OMIT temperature (Opus
    4.8 / Sonnet 5 reject it with a 400 — this is the correctness tooth, not
    style), while the Haiku api fall-over KEEPS temperature (byte-unchanged
    B2 body) and the gpt-4o state seat keeps its openai temperature. A
    sampling regression in either direction fails here by name."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-b4")
    openai_shape = json.dumps({
        "choices": [{"message": {"content": "{}"}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 1, "completion_tokens": 1},
    }).encode()
    seen = _capture(monkeypatch,
                    reply=lambda body, url: (
                        openai_shape if url == llm.OPENAI_CHAT_URL
                        else anthropic_envelope("{}")))

    # writer (Opus 4.8) and analyst (Sonnet 5): no temperature key at all
    generate._chat("sk-x", "W", 100, 0.9, True)
    analysis._analysis_chat("sk-x", "A")
    # rank pinned to its api fall-over (Haiku): temperature RIDES, int 0
    monkeypatch.setenv("NEWSLENS_LANE_RANK", "api")
    ranking._post_chat("sk-x", "R")
    # state (gpt-4o, openai): temperature rides the openai body
    memory_core._default_state_chat("sk-openai", "S")

    writer_b, analyst_b, rank_b, state_b = (s["body"] for s in seen)
    assert writer_b["model"] == "claude-opus-4-8"
    assert analyst_b["model"] == "claude-sonnet-5"
    assert rank_b["model"] == "claude-haiku-4-5"
    assert state_b["model"] == "gpt-4o"
    assert "temperature" not in writer_b
    assert "temperature" not in analyst_b
    assert rank_b["temperature"] == 0            # Haiku: byte-unchanged from B2
    assert "temperature" in state_b              # openai body unchanged
    # the schema knob that drives it, pinned per seat:
    assert llm.SEATS["writer"].sampling is False
    assert llm.SEATS["analyst"].sampling is False
    assert llm.SEATS["rank"].sampling is True
    assert llm.SEATS["state"].sampling is True


def test_no_anthropic_body_ever_carries_budget_tokens_or_top_p(monkeypatch):
    """budget_tokens is REMOVED on Opus 4.8 / Sonnet 5 (400 if sent) and was
    never part of this codebase's request law; top_p/top_k likewise. Sweep
    every anthropic-api seat's real caller bytes for all three. A
    reintroduction anywhere in the provider fails here before it 400s in
    production."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-b4")
    monkeypatch.setenv("NEWSLENS_LANE_RANK", "api")
    monkeypatch.setenv("NEWSLENS_LANE_EDITOR", "api")
    monkeypatch.setenv("NEWSLENS_LANE_SCRIPT", "api")
    seen = _capture(monkeypatch)
    generate._chat("sk-x", "W", 100, 0.9, True)          # writer / Opus
    analysis._analysis_chat("sk-x", "A")                 # analyst / Sonnet
    ranking._post_chat("sk-x", "R")                      # rank / Haiku api
    generate.call_llm("sk-x", "E", "editor", 50, 0.5, False)   # editor / Haiku
    generate.call_llm("sk-x", "S", "script", 50, 0.5, False)   # script / Haiku
    assert len(seen) == 5
    for s in seen:
        raw = s["data"]
        assert b"budget_tokens" not in raw, s["body"]["model"]
        assert b"top_p" not in raw, s["body"]["model"]
        assert b"top_k" not in raw, s["body"]["model"]
        # thinking, when present, is adaptive-only (the 4.6+ shape)
        if "thinking" in s["body"]:
            assert s["body"]["thinking"] == {"type": "adaptive"}


def test_max_tokens_headroom_constants_and_wire_values(monkeypatch):
    """The B4 headroom raise, pinned at the wire: NARRATIVE_MAX_TOKENS =
    16,000 and ANALYSIS_MAX_TOKENS = 6,000 (adaptive thinking BILLS AS
    OUTPUT and counts against max_tokens — a prose-sized ceiling would
    length-finish inside the thinking block: a failed run + a paid retry).
    The values ride the real call sites' bodies."""
    assert generate.NARRATIVE_MAX_TOKENS == 16000
    assert analysis.ANALYSIS_MAX_TOKENS == 6000
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-b4")
    seen = _capture(monkeypatch)
    generate.call_llm("sk-x", "P", "narrative", generate.NARRATIVE_MAX_TOKENS,
                      generate.NARRATIVE_TEMPERATURE, True)
    analysis._analysis_chat("sk-x", "A")
    assert seen[0]["body"]["max_tokens"] == 16000
    assert seen[1]["body"]["max_tokens"] == 6000


# ===========================================================================
# 2. The cache split — the law rides the cached system prefix, byte-preserved
# ===========================================================================

def _built_narrative_prompt(hostile=False):
    _seed_sandbox_record(hostile=hostile)
    con = db.connect_readonly()
    try:
        inputs = generate.load_briefing_inputs(con, A_DAY)
        briefs = {}
        for s in inputs["slots"]:
            doc = analysis.latest_valid_brief(con, A_DAY, int(s["slot"]))
            if doc:
                briefs[int(s["slot"])] = doc
        inputs["briefs_by_slot"] = briefs
        return generate.build_narrative_prompt(A_DAY, "A", inputs), inputs
    finally:
        con.close()


_LAW_ANCHORS = (
    "REGISTER LAW",
    "BANNED CHARMS",
    "DATED CALLBACK — REQUIRED",
    "Verification theater",
    "Manufactured periodization",
)


def test_register_law_rides_above_the_sentinel_in_the_built_prompt():
    """POSITION IS THE CONTRACT (ADR-0016 §6): the register-spec law — §6
    BANNED CHARMS, the §2 sourcing hierarchy, the HSR DATED CALLBACK
    requirement — sits ABOVE the split sentinel in the BUILT prompt, so it
    rides the cached system prefix. A sentinel-drift regression (law text
    landing below the sentinel, or the sentinel string diverging between
    template and code) would silently un-cache the law; this pin bites it
    mechanically, on the real template + real builder."""
    prompt, _ = _built_narrative_prompt()
    sentinel_at = prompt.find(generate._NARRATIVE_CACHE_SENTINEL)
    assert sentinel_at > 0, "split sentinel missing from the built prompt"
    for anchor in _LAW_ANCHORS:
        at = prompt.find(anchor)
        assert at != -1, f"law anchor {anchor!r} missing from the prompt"
        assert at < sentinel_at, (
            f"law anchor {anchor!r} sits BELOW the cache sentinel — the law "
            "has drifted out of the cached system prefix")
    # variant B is retired and carries no register law — the sentinel split
    # must stay unique to what each template actually holds
    tmpl_b = (paths.PROMPTS_DIR / "narrative_variant_b.txt").read_text(
        encoding="utf-8")
    assert "REGISTER LAW" not in tmpl_b


def test_writer_wire_shape_cached_system_prefix_and_reassembly_identity(
        monkeypatch):
    """The full wire proof on the REAL prompt: body['system'] is a LIST whose
    first block is the law prefix marked cache_control:{ephemeral} and whose
    second is the json nudge WITHOUT cache_control (volatile-free, after the
    breakpoint); messages[0] carries the remainder starting at the sentinel;
    and system-block-0 + user body == the original prompt BYTE-IDENTICAL
    (the split moves the law's ROLE, never its text)."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-b4")
    prompt, _ = _built_narrative_prompt()
    seen = _capture(monkeypatch)
    generate._chat("sk-x", prompt, generate.NARRATIVE_MAX_TOKENS,
                   generate.NARRATIVE_TEMPERATURE, True)
    body = seen[0]["body"]
    assert isinstance(body["system"], list) and len(body["system"]) == 2
    law_block, nudge_block = body["system"]
    assert law_block["cache_control"] == {"type": "ephemeral"}
    assert "cache_control" not in nudge_block
    assert nudge_block["text"] == llm._ANTHROPIC_JSON_SYSTEM
    for anchor in _LAW_ANCHORS:
        assert anchor in law_block["text"], anchor
    user = body["messages"][0]["content"]
    assert user.startswith("\n=== THE READER'S TAGS")
    assert law_block["text"] + user == prompt        # byte identity
    for anchor in _LAW_ANCHORS:                      # law never duplicated
        assert anchor not in user, anchor
    # and the writer knobs ride the same request
    assert "temperature" not in body
    assert body["thinking"] == {"type": "adaptive"}
    assert body["output_config"] == {"effort": "xhigh"}


def test_analyst_split_at_its_sentinel_and_no_split_without_it(monkeypatch):
    """The analyst's split sentinel ('Word budget for all prose fields
    combined:') puts the static instruction block in the cached system slot
    and the per-slot data in the user prompt — reassembly byte-identical. A
    prompt without the sentinel (or with it at position 0) ships unsplit,
    and the json nudge stays the plain B2 string."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-b4")
    instructions = "STATIC BRIEF INSTRUCTIONS\nrules and law here\n"
    data = analysis._ANALYST_CACHE_SENTINEL + " 400\nPER-SLOT DATA {hostile}"
    prompt = instructions + data
    seen = _capture(monkeypatch)
    analysis._analysis_chat("sk-x", prompt)
    body = seen[0]["body"]
    assert isinstance(body["system"], list) and len(body["system"]) == 2
    assert body["system"][0]["text"] == instructions   # exact prefix, verbatim
    assert body["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert body["system"][0]["text"] + body["messages"][0]["content"] == prompt
    # sentinel at position 0 -> no split (idx > 0 guard), nudge plain string
    seen.clear()
    analysis._analysis_chat("sk-x", data)
    body0 = seen[0]["body"]
    assert body0["system"] == llm._ANTHROPIC_JSON_SYSTEM
    assert body0["messages"][0]["content"] == data


def test_openai_revert_sends_one_user_message_byte_identical(monkeypatch):
    """PROVIDER-GATED (ADR-0016 §4): with SEATS['writer'] flipped back to the
    gpt-4o row (the documented one-diff revert), the SAME sentinel-bearing
    prompt ships as ONE user message in the exact pre-B4 openai byte shape —
    no system split, law inline where it always was. The split can never
    leak into a revert."""
    gpt4o_writer = dataclasses.replace(
        llm.SEATS["writer"], provider="openai", model="gpt-4o", lane="api",
        usd_per_mtok_in=2.50, usd_per_mtok_out=10.00, timeout_s=120,
        thinking=None, effort=None, sampling=True)
    monkeypatch.setitem(llm.SEATS, "writer", gpt4o_writer)
    prompt = ("LAW TEXT: REGISTER LAW etc.\n"
              + generate._NARRATIVE_CACHE_SENTINEL + " ===\nvolatile stuff")
    seen = _capture(monkeypatch, reply=json.dumps({
        "choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 1, "completion_tokens": 1},
    }).encode())
    generate._chat("sk-qa", prompt, 333, 0.7, True)
    expected = json.dumps({
        "model": "gpt-4o",
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.7,
        "max_tokens": 333,
        "response_format": {"type": "json_object"},
    }).encode("utf-8")
    assert seen[0]["data"] == expected               # the pre-B4 bytes exactly
    assert seen[0]["url"] == ranking.OPENAI_CHAT_URL


def test_subscription_lane_folds_the_prefix_inline_never_dropped(
        monkeypatch, tmp_path):
    """NEWSLENS_LANE_WRITER=subscription is a REGISTERED lane now (the
    gate/principal's Option-C override — no code change). The lane has no
    cache surface, so the law prefix must ride --append-system-prompt and
    the volatile remainder stdin — never dropped, never reordered."""
    record = tmp_path / "spawn.json"
    shim = tmp_path / "claude"
    shim.write_text(
        "#!/usr/bin/env python3\n"
        "import sys, json, os\n"
        "payload = {'argv': sys.argv[1:], 'stdin': sys.stdin.read()}\n"
        f"open({str(record)!r}, 'w').write(json.dumps(payload))\n"
        "print(json.dumps({'type': 'result', 'subtype': 'success',"
        " 'is_error': False, 'result': '{}', 'session_id': 's',"
        " 'total_cost_usd': 0.0, 'usage': {'input_tokens': 1,"
        " 'output_tokens': 1}}))\n")
    shim.chmod(0o755)
    monkeypatch.setenv("NEWSLENS_CLAUDE_BIN", str(shim))
    monkeypatch.setenv("NEWSLENS_LANE_WRITER", "subscription")
    cfg = llm.resolve_seat("writer")
    assert cfg.lane == "subscription" and cfg.provider == "anthropic"
    prompt, _ = _built_narrative_prompt()
    system, user = generate._split_cache_prefix(cfg, prompt)
    assert system and user and system + user == prompt
    llm.chat(llm.LaneRequest(cfg=cfg, prompt=user, temperature=0.3,
                             max_tokens=100, json_mode=False,
                             user_agent="ua", api_key="ignored",
                             system=system))
    spawn = json.loads(record.read_text())
    argv = spawn["argv"]
    assert "--append-system-prompt" in argv
    sys_arg = argv[argv.index("--append-system-prompt") + 1]
    assert sys_arg == system                          # the law, byte-preserved
    for anchor in _LAW_ANCHORS:
        assert anchor in sys_arg, anchor
    assert spawn["stdin"] == user                     # volatile body on stdin


def test_cache_read_recorded_nonzero_while_usd_shadow_stays_undiscounted(
        monkeypatch):
    """THE B4 RIDER (carried from B2 QA, ADR-0016 §4): the cache surface is
    WIRED — cache_read/cache_creation flow from the anthropic usage into the
    writer's ledger row nonzero — but usd_shadow stays the full undiscounted
    in+out price (a money guard never under-counts on an unproven hit; the
    ~0.1x is measured by the battery, not assumed here)."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-b4")
    _capture(monkeypatch, reply=anthropic_envelope(
        "ok", input_tokens=10_000, output_tokens=500,
        cache_creation=1_000, cache_read=9_000))
    sink = []
    generate.call_llm("sk-x", "P", "narrative", 100, 0.3, False,
                      cost_sink=sink)
    e = sink[0]
    assert e["cache_read_tokens"] == 9_000
    assert e["cache_creation_tokens"] == 1_000
    full_price = round(10_000 / 1e6 * 5.00 + 500 / 1e6 * 25.00, 6)
    assert e["usd_shadow"] == full_price              # UNDISCOUNTED
    assert e["usd_charged"] == e["usd"] == full_price


# ===========================================================================
# 3. R-B4a — literals derive from SEATS or die (KeyError liveness)
# ===========================================================================

def test_model_constants_equal_their_seat_rows_exactly():
    w, a, st = llm.SEATS["writer"], llm.SEATS["analyst"], llm.SEATS["state"]
    assert generate.WRITER_MODEL == w.model == "claude-opus-4-8"
    assert generate.WRITER_USD_PER_MTOK_IN == w.usd_per_mtok_in == 5.00
    assert generate.WRITER_USD_PER_MTOK_OUT == w.usd_per_mtok_out == 25.00
    assert analysis.ANALYSIS_MODEL == a.model == "claude-sonnet-5"
    assert analysis.ANALYSIS_USD_IN_PER_MTOK == a.usd_per_mtok_in == 3.00
    assert analysis.ANALYSIS_USD_OUT_PER_MTOK == a.usd_per_mtok_out == 15.00
    assert memory_core.STATE_MODEL == st.model == "gpt-4o"
    assert memory_core.STATE_USD_IN_PER_MTOK == st.usd_per_mtok_in == 2.50
    assert memory_core.STATE_USD_OUT_PER_MTOK == st.usd_per_mtok_out == 10.00


@pytest.mark.parametrize("seat,module", [
    ("writer", "newslens.generate"),
    ("analyst", "newslens.analysis"),
    ("state", "newslens.memory_core"),
])
def test_vanished_seat_keyerrors_at_import_never_a_stale_literal(seat, module):
    """R-B4a's liveness, in a CLEAN interpreter: delete the seat row before
    the caller imports — the import must die with a KeyError naming the
    lookup, proving the constant reads llm.SEATS at import time rather than
    holding a fork-able literal."""
    code = (
        "import newslens.llm as llm\n"
        f"del llm.SEATS[{seat!r}]\n"
        f"import {module}\n"
    )
    proc = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True, text=True, timeout=60,
        env={**os.environ, "PYTHONPATH": str(PROTOTYPE_ROOT / "src")},
        cwd=str(PROTOTYPE_ROOT))
    assert proc.returncode != 0
    assert "KeyError" in proc.stderr and seat in proc.stderr


# ===========================================================================
# 4. NEWSLENS_MODEL_<SEAT> — the battery override surface
# ===========================================================================

def test_model_override_swaps_only_the_model_string():
    base = llm.SEATS["writer"]
    got = llm.resolve_seat("writer",
                           {"NEWSLENS_MODEL_WRITER": "claude-fable-5"})
    assert got == dataclasses.replace(base, model="claude-fable-5")
    # empty/whitespace value == unset; identity object returned untouched
    assert llm.resolve_seat("writer", {"NEWSLENS_MODEL_WRITER": ""}) == base
    assert llm.resolve_seat("writer", {"NEWSLENS_MODEL_WRITER": "  "}) == base
    # any seat, and it composes with a lane override — still only those two
    got2 = llm.resolve_seat("analyst", {
        "NEWSLENS_MODEL_ANALYST": "claude-opus-4-7",
        "NEWSLENS_LANE_ANALYST": "subscription"})
    assert got2 == dataclasses.replace(llm.SEATS["analyst"],
                                       model="claude-opus-4-7",
                                       lane="subscription")


def test_model_override_reaches_the_wire_with_seat_knobs_and_seat_prices(
        monkeypatch):
    """A Fable arm through the real writer path: ONLY the model string moves
    on the wire (thinking/effort/no-temperature/max_tokens identical), and
    the seam's ledger row prices at the SEAT's table (Opus $5/$25 — the
    documented caveat; the arm's real rate is the battery manifest's job)."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-b4")
    monkeypatch.setenv("NEWSLENS_MODEL_WRITER", "claude-fable-5")
    seen = _capture(monkeypatch, reply=anthropic_envelope(
        "ok", input_tokens=1000, output_tokens=200))
    sink = []
    generate.call_llm("sk-x", "P", "narrative", 400, 0.3, False,
                      cost_sink=sink)
    body = seen[0]["body"]
    assert body["model"] == "claude-fable-5"
    assert "temperature" not in body
    assert body["thinking"] == {"type": "adaptive"}
    assert body["output_config"] == {"effort": "xhigh"}
    assert body["max_tokens"] == 400
    e = sink[0]
    assert e["model"] == "claude-fable-5"             # honest model label
    assert e["usd_shadow"] == round(1000 / 1e6 * 5.00 + 200 / 1e6 * 25.00, 6)


# ===========================================================================
# 4b. FIX-1 (B4-D1 closed) — the analyst's published resolution vs mid-call
#     flaps (the ranking-D5 / generate-D6 shape, analyst twin)
# ===========================================================================

def test_flap_window_cannot_fork_analyst_transport_from_its_cost(monkeypatch):
    """FIX-1's one-resolution law at the call level: call_analysis_model
    publishes effective_seat EXACTLY ONCE (_ACTIVE_ANALYST) and both
    _analysis_chat's transport AND the cost leg ride it. Simulate a `claude`
    flap by making effective_seat answer a DIFFERENT (cfg, reason) on a
    hypothetical second call: the transport must ride resolution #1
    (subscription) and the returned cost must be the subscription $0 — never
    a second resolution that puts the bytes on the metered api wire while
    the cost says $0, or vice versa (the D1 lie via the analyst's new door).

    LIVENESS (proven this pass, comment-out procedure): disabling the holder
    read in analysis._effective_analyst (fresh effective_seat every call)
    fails this test by name — resolution count 2 + transport on the flapped
    api cfg."""
    sub = dataclasses.replace(llm.SEATS["analyst"], lane="subscription")
    api = llm.SEATS["analyst"]
    n = {"calls": 0}

    def flapping(seat, env=None):
        n["calls"] += 1
        return (sub, None) if n["calls"] == 1 \
            else (api, "subscription_unavailable")

    monkeypatch.setattr(llm, "effective_seat", flapping)
    seen = {}
    brief = json.dumps({"anything": "goes"})

    def fake_chat(req):
        seen["transport_cfg"] = req.cfg
        raw = {"choices": [{"message": {"content": brief},
                            "finish_reason": "stop"}],
               "usage": {"prompt_tokens": 1000, "completion_tokens": 200}}
        return llm.LaneResponse(content=brief, usage=llm.Usage(1000, 200),
                                finish_reason="stop", raw=raw)

    monkeypatch.setattr(llm, "chat", fake_chat)
    payload, cost = analysis.call_analysis_model("k", "p")
    assert n["calls"] == 1, (
        "effective_seat resolved more than once — the analyst flap window "
        "is open")
    assert seen["transport_cfg"].lane == "subscription"   # rode resolution #1
    assert cost == 0.0                                    # $0 sub, not api $$
    assert analysis._ACTIVE_ANALYST is None               # own-scope teardown


def test_flap_window_cannot_fork_analyst_report_lane(monkeypatch):
    """FIX-1's one-resolution law at STAGE level, both label arms:
    run_analysis publishes once at entry and the report's `lane` field rides
    that (cfg, reason) — a flap between publish and report can never
    mislabel the stage. Arm 1: resolution #1 = subscription -> report lane
    'subscription' exactly. Arm 2: resolution #1 = a labeled fall -> report
    lane 'api(fallback:subscription_unavailable)' exactly, never a bare
    'api' that hides the fall. Slots are budget-skipped (the m3 no-headroom
    trick) so zero model calls ride either arm."""
    db.migrate()
    con = db.connect()
    try:
        seed_briefing(con, A_DAY, [slot(1)], narrative="Published.")

        def run_with(first):
            n = {"calls": 0}

            def flapping(seat, env=None):
                n["calls"] += 1
                if n["calls"] == 1:
                    return first
                return (llm.SEATS["analyst"], "subscription_unavailable") \
                    if first[1] is None else \
                    (dataclasses.replace(llm.SEATS["analyst"],
                                         lane="subscription"), None)

            monkeypatch.setattr(llm, "effective_seat", flapping)
            rep = analysis.run_analysis(
                date=A_DAY, con=con, env={"OPENAI_API_KEY": "sk-qa-fake"},
                chat=lambda k, p: (_ for _ in ()).throw(
                    AssertionError("synthesis called with no headroom")),
                sonar=lambda k, t, c: (_ for _ in ()).throw(
                    AssertionError("sonar called with no headroom")),
                fetch=lambda *a, **k: b"", sleep=lambda s: None,
                already_spent=999.0, tiers_override=["full"])
            return n["calls"], rep

        sub = dataclasses.replace(llm.SEATS["analyst"], lane="subscription")
        calls, rep = run_with((sub, None))
        assert calls == 1
        assert rep["lane"] == "subscription"
        assert analysis._ACTIVE_ANALYST is None           # teardown held
        calls, rep = run_with((llm.SEATS["analyst"],
                               "subscription_unavailable"))
        assert calls == 1
        assert rep["lane"] == "api(fallback:subscription_unavailable)"
        assert analysis._ACTIVE_ANALYST is None
    finally:
        con.close()


# ===========================================================================
# 5. .format() safety — hostile braces in slot content
# ===========================================================================

def test_hostile_braces_in_titles_and_excerpts_render_split_and_ship(
        monkeypatch):
    """The MODIFIED template (register law added) still renders via
    str.format with brace-laden data riding the argument side: hostile
    titles/excerpts arrive VERBATIM in the built prompt, the split keeps
    byte identity, and the wire carries them in the volatile user body. A
    double-format regression (formatting a string that already embeds slot
    data) would KeyError/IndexError here by name."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-b4")
    prompt, _ = _built_narrative_prompt(hostile=True)
    assert "Fed hikes {rates} to {0}% — {a[b]} say {'k': 1}" in prompt
    assert "braces {x} and {} and %s and {1:>8}." in prompt
    for anchor in _LAW_ANCHORS:
        assert anchor in prompt
    seen = _capture(monkeypatch)
    generate._chat("sk-x", prompt, 100, 0.3, True)
    body = seen[0]["body"]
    user = body["messages"][0]["content"]
    assert "Fed hikes {rates} to {0}%" in user        # data side = volatile
    assert body["system"][0]["text"] + user == prompt


# ===========================================================================
# 6. The battery harness — adversarial
# ===========================================================================

def test_battery_dry_run_makes_zero_calls_and_zero_writes(monkeypatch,
                                                          capsys):
    _guard_sanction(monkeypatch)
    _seed_sandbox_record()
    calls = _transport_tripwire(monkeypatch)
    before = _data_snapshot()
    rc = battery.main(["--date", A_DAY])
    out = capsys.readouterr().out
    assert rc == 0
    assert calls == []                                # ZERO transport
    assert _data_snapshot() == before                 # ZERO writes
    assert "DRY RUN" in out
    assert "claude-opus-4-8" in out and "claude-fable-5" in out \
        and "claude-sonnet-5" in out                  # the default arms
    assert "budget cap $1.50/run" in out              # the B4 default binds


def test_battery_run_refuses_keyless_before_any_spend(monkeypatch, capsys):
    _guard_sanction(monkeypatch)
    _seed_sandbox_record()
    calls = _transport_tripwire(monkeypatch)
    before = _data_snapshot()
    rc = battery.main(["--date", A_DAY, "--run"])
    err = capsys.readouterr().err
    assert rc == 1
    assert "ANTHROPIC_API_KEY" in err
    assert calls == [] and _data_snapshot() == before


def test_battery_refuses_a_date_with_no_briefing_row(monkeypatch, capsys):
    _guard_sanction(monkeypatch)
    db.migrate()                                      # empty sandbox record
    calls = _transport_tripwire(monkeypatch)
    rc = battery.main(["--date", "2031-01-01"])
    err = capsys.readouterr().err
    assert rc == 1 and "refused" in err
    assert calls == []


def test_battery_refuses_when_no_record_db_exists_at_all(monkeypatch, capsys):
    """FIX-3 (B4-D4 closed): a DATA_DIR with no newslens.db at all — a fresh
    checkout, or a mispointed NEWSLENS_DATA_DIR — is the harness's own
    'refused' line naming the read-only open failure, rc 1, zero transport
    and zero writes. Pre-fix this was a raw sqlite3.OperationalError
    traceback out of connect_readonly."""
    _guard_sanction(monkeypatch)
    assert not Path(paths.DB_PATH).exists()           # truly empty sandbox
    calls = _transport_tripwire(monkeypatch)
    before = _data_snapshot()
    rc = battery.main(["--date", A_DAY])
    err = capsys.readouterr().err
    assert rc == 1
    assert "refused" in err and "read-only" in err
    assert "Traceback" not in err
    assert calls == [] and _data_snapshot() == before
    assert not Path(paths.DB_PATH).exists()           # never created the file


def test_battery_opens_the_record_readonly_and_readonly_cannot_write(
        monkeypatch, capsys):
    """Two bites: (a) the battery resolves the record ONLY through
    db.connect_readonly — a db.connect call anywhere in its path is an
    immediate failure; (b) the readonly connection genuinely refuses a
    write (SQLite mode=ro), so 'read-only on the record' is a mechanism,
    not a promise."""
    _guard_sanction(monkeypatch)
    _seed_sandbox_record()
    # (b) bite the connection itself
    con = db.connect_readonly()
    try:
        with pytest.raises(db.sqlite3.OperationalError):
            con.execute("UPDATE briefings SET narrative_text = 'clobbered'")
        with pytest.raises(db.sqlite3.OperationalError):
            con.execute("INSERT INTO memory (topic, status, status_changed_at,"
                        " created_at, updated_at) VALUES ('x','active','t','t','t')")
    finally:
        con.close()
    # (a) the battery path never opens a writable connection
    def no_writable_connect(*a, **k):
        raise AssertionError("battery opened a WRITABLE db connection")
    monkeypatch.setattr(db, "connect", no_writable_connect)
    _transport_tripwire(monkeypatch)
    rc = battery.main(["--date", A_DAY])
    assert rc == 0
    capsys.readouterr()


def test_battery_cap_gate_cumulative_skips_the_crossing_arm_only(
        monkeypatch, capsys):
    """The cumulative math, adversarially: cap $1.00, arms fable ($0.80+),
    opus ($0.40+), haiku ($0.08+) in that order. fable plans (~0.80), opus
    would cross (~1.20) -> SKIPPED AND EXCLUDED from the running total, so
    haiku still fits (~0.88). A gate that added skipped estimates to the
    cumulative — or aborted the whole plan at the first crossing — fails
    here."""
    _guard_sanction(monkeypatch)
    _seed_sandbox_record()
    _transport_tripwire(monkeypatch)
    monkeypatch.setenv("BUDGET_CAP_USD_PER_RUN", "1.00")
    rc = battery.main([
        "--date", A_DAY,
        "--arms", "claude-fable-5,claude-opus-4-8,claude-haiku-4-5"])
    out = capsys.readouterr().out
    assert rc == 0
    lines = {m: next(l for l in out.splitlines() if f"- {m}:" in l)
             for m in ("claude-fable-5", "claude-opus-4-8",
                       "claude-haiku-4-5")}
    assert "SKIP" not in lines["claude-fable-5"]
    assert "SKIP" in lines["claude-opus-4-8"]
    assert "SKIP" not in lines["claude-haiku-4-5"]    # later cheaper arm fits
    assert "planned 2 arm(s), skipped 1" in out


def test_battery_run_produces_per_arm_artifacts_under_sandboxed_data_dir(
        monkeypatch, capsys):
    """The live path against a scripted wire: per-arm model on the request,
    artifacts under <sandbox DATA_DIR>/battery/<date>/<arm>/, manifest
    numbers at the ARM's real rate vs the seam's Opus-priced shadow (kept
    distinct — the honest-cost split), cache_read from the envelope, the
    briefing record untouched, and NEWSLENS_MODEL_WRITER restored to its
    pre-run value."""
    _guard_sanction(monkeypatch)
    slots = _seed_sandbox_record()
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-battery")
    monkeypatch.setenv("NEWSLENS_MODEL_WRITER", "pre-existing-value")
    payload = stories_payload(slots)
    seen = _capture(monkeypatch, reply=lambda body, url: anthropic_envelope(
        payload, input_tokens=1000, output_tokens=200, cache_read=900))
    db_before = hashlib.sha256(
        Path(paths.DB_PATH).read_bytes()).hexdigest()
    rc = battery.main(["--date", A_DAY,
                       "--arms", "claude-opus-4-8,claude-fable-5", "--run"])
    out = capsys.readouterr().out
    assert rc == 0
    assert [s["body"]["model"] for s in seen] == \
        ["claude-opus-4-8", "claude-fable-5"]         # the single-variable arm
    for s in seen:
        assert "temperature" not in s["body"]
        assert s["body"]["max_tokens"] == generate.NARRATIVE_MAX_TOKENS
    root = Path(paths.DATA_DIR) / "battery" / A_DAY
    for arm, pin, pout in (("claude-opus-4-8", 5.0, 25.0),
                           ("claude-fable-5", 10.0, 50.0)):
        d = root / arm
        assert (d / "narrative.json").exists()
        assert (d / "narrative.md").read_text(encoding="utf-8").strip()
        m = json.loads((d / "manifest.json").read_text(encoding="utf-8"))
        assert m["arm"] == arm
        assert m["cache_read_tokens"] == 900
        assert m["usd_real_at_arm_price"] == round(
            1000 / 1e6 * pin + 200 / 1e6 * pout, 6)
        assert m["usd_shadow_seam"] == round(
            1000 / 1e6 * 5.0 + 200 / 1e6 * 25.0, 6)   # Opus-priced, distinct
    # the record: byte-identical (never touched)
    assert hashlib.sha256(
        Path(paths.DB_PATH).read_bytes()).hexdigest() == db_before
    # the override env var: restored exactly
    assert os.environ["NEWSLENS_MODEL_WRITER"] == "pre-existing-value"
    assert "2/2 arms produced" in out


def test_battery_discloses_a_failed_arm_and_continues(monkeypatch, capsys):
    _guard_sanction(monkeypatch)
    slots = _seed_sandbox_record()
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-battery")
    payload = stories_payload(slots)

    def reply(body, url):
        if body["model"] == "claude-opus-4-8":
            raise OSError("scripted transport failure for the opus arm")
        return anthropic_envelope(payload, input_tokens=10, output_tokens=10)

    _capture(monkeypatch, reply=reply)
    rc = battery.main(["--date", A_DAY,
                       "--arms", "claude-opus-4-8,claude-sonnet-5", "--run"])
    cap = capsys.readouterr()
    assert rc == 0                                    # one arm produced
    assert "claude-opus-4-8: FAILED" in cap.err
    assert "disclosed, other arms continue" in cap.err
    root = Path(paths.DATA_DIR) / "battery" / A_DAY
    assert not (root / "claude-opus-4-8" / "manifest.json").exists()
    assert (root / "claude-sonnet-5" / "manifest.json").exists()
    # env override cleaned up even on the failure path (prev unset -> popped)
    assert "NEWSLENS_MODEL_WRITER" not in os.environ


def test_battery_out_flag_redirects_artifacts(monkeypatch, tmp_path, capsys):
    _guard_sanction(monkeypatch)
    slots = _seed_sandbox_record()
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-battery")
    payload = stories_payload(slots)
    _capture(monkeypatch, reply=lambda b, u: anthropic_envelope(
        payload, input_tokens=10, output_tokens=10))
    out_root = tmp_path / "elsewhere"
    rc = battery.main(["--date", A_DAY, "--arms", "claude-opus-4-8",
                       "--out", str(out_root), "--run"])
    capsys.readouterr()
    assert rc == 0
    assert (out_root / A_DAY / "claude-opus-4-8" / "manifest.json").exists()
    assert not (Path(paths.DATA_DIR) / "battery").exists()
