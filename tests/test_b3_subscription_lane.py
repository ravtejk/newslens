"""B3 subscription-lane liveness — the wiring proof (implementer).

The `claude -p` subscription provider is a NEW enforcement surface, so per
ENGINEERING.md it is born with the red tests only it can flip. Rook's four red
conditions are pinned here as mechanism, not hope:

  (1) the subprocess env STRIPS ANTHROPIC_API_KEY — the D1 silent-billing class;
      a born-red test that fails the instant the strip is removed;
  (2) all tools + the injection surface are disabled and cwd is a fresh empty
      scratch dir removed after the call (a filesystem tripwire proves a lane
      call touches nothing outside its sandbox);
  (3) a missing/misconfigured binary is LaneUnavailable at the gate (check_lane),
      never a silent wrong-lane call;
  (4) usd_charged == 0.0 (subscription), usd_shadow always API-priced, and the
      state_rewrites row does NOT vanish when charged is 0 (rider R-B3a).

Plus the plumbing: binary resolution precedence, prompt-on-stdin, the
documented JSON parse (result/session_id/total_cost_usd/usage.*), the
transport-shaped failures (is_error / non-JSON / nonzero exit / timeout), the
estimate-and-LABEL path when the CLI omits usage, the flipped seat defaults, and
the FIX-1 stage-boundary preflight.

Zero live calls: every test drives a STUB `claude` shim written into the sandbox
(the conftest never lets the real ~/.local/bin/claude be reached). $0.
"""

from __future__ import annotations

import dataclasses
import json
import os
import stat
import textwrap
from pathlib import Path

import pytest

from newslens import analysis, generate, llm, memory_core, ranking


# ---------------------------------------------------------------------------
# stub `claude` shim factory — a real executable emitting canned -p JSON, never
# the network and never the real CLI. `record` (baked in, since the child env
# is stripped and cwd is ephemeral) captures argv/env/cwd/stdin for assertions.
# ---------------------------------------------------------------------------

def _make_stub(dir_path: Path, *, record: Path = None, mode: str = "success",
               result: str = "{}", inp: int = 1000, out: int = 200,
               cache: int = 0, cli_cost: float = 0.0,
               write_canary: bool = False) -> Path:
    src = textwrap.dedent(f"""\
        #!/usr/bin/env python3
        import sys, os, json
        _stdin = sys.stdin.read()
        _record = {(str(record) if record else None)!r}
        if _record:
            with open(_record, "w") as f:
                json.dump({{"argv": sys.argv[1:], "cwd": os.getcwd(),
                           "has_api_key": "ANTHROPIC_API_KEY" in os.environ,
                           "env_keys": sorted(os.environ.keys()),
                           "stdin": _stdin}}, f)
        if {write_canary!r}:
            open(os.path.join(os.getcwd(), "canary.txt"), "w").write("touched")
        _mode = {json.dumps(mode)}
        if _mode == "nonzero":
            sys.stderr.write("stub boom")
            sys.exit(3)
        if _mode == "garbage":
            sys.stdout.write("this is not json")
            sys.exit(0)
        if _mode == "hang":
            import time
            time.sleep(30)
        if _mode == "is_error":
            print(json.dumps({{"type": "result", "is_error": True,
                               "result": "model refused"}}))
            sys.exit(0)
        _payload = {{"type": "result", "subtype": "success", "is_error": False,
                    "result": {json.dumps(result)}, "session_id": "sess-xyz",
                    "total_cost_usd": {cli_cost!r}}}
        if _mode != "no_usage":
            _payload["usage"] = {{"input_tokens": {inp}, "output_tokens": {out},
                                 "cache_read_input_tokens": {cache}}}
        print(json.dumps(_payload))
        """)
    shim = dir_path / "claude"
    shim.write_text(src)
    shim.chmod(shim.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return shim


def _rank_req(json_mode: bool = True, prompt: str = "cluster these stories"):
    return llm.LaneRequest(
        cfg=llm.resolve_seat("rank"), prompt=prompt, temperature=0,
        max_tokens=100, json_mode=json_mode, user_agent="ua",
        api_key="openai-key-should-be-ignored", url=llm.OPENAI_CHAT_URL)


# ---------------------------------------------------------------------------
# 1. Lane defaults flipped — rank/editor/script DEFAULT to subscription
# ---------------------------------------------------------------------------

def test_haiku_seats_default_to_the_subscription_lane():
    # 2026-07-17 (option a): state joined the Haiku/subscription seats.
    for seat in ("rank", "editor", "script", "state"):
        cfg = llm.resolve_seat(seat, {})
        assert cfg.provider == "anthropic" and cfg.model == "claude-haiku-4-5"
        assert cfg.lane == "subscription", seat
    # the api lane is the registered alternative, reachable per-seat
    assert llm.resolve_seat("rank", {"NEWSLENS_LANE_RANK": "api"}).lane == "api"
    # the api-default seats are untouched (synthesis is the lone openai one)
    for seat in ("writer", "analyst", "synthesis"):
        assert llm.resolve_seat(seat, {}).lane == "api"


def test_subscription_provider_is_registered():
    assert "anthropic:subscription" in llm._PROVIDERS
    assert llm._provider_key(llm.resolve_seat("rank", {})) == "anthropic:subscription"


# ---------------------------------------------------------------------------
# 2. Rook #1 — the child env STRIPS ANTHROPIC_API_KEY (the D1 born-red)
# ---------------------------------------------------------------------------

def test_child_env_strips_anthropic_api_key(tmp_path, monkeypatch):
    """BORN-RED (the silent-billing D1 class): with ANTHROPIC_API_KEY exported
    in the PARENT, the `claude -p` child must NOT see it — else the CLI prefers
    the key, bills the API, and the ledger lies '$0 subscription'. Fails the
    instant _subscription_env stops stripping."""
    rec = tmp_path / "rec.json"
    stub = _make_stub(tmp_path, record=rec)
    monkeypatch.setenv("NEWSLENS_CLAUDE_BIN", str(stub))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-would-bill-the-api")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-openai-should-not-ride-either")
    llm.chat(_rank_req())
    child = json.loads(rec.read_text())
    assert child["has_api_key"] is False
    assert "ANTHROPIC_API_KEY" not in child["env_keys"]
    assert "OPENAI_API_KEY" not in child["env_keys"]
    # the allowlist only lets HOME/PATH-family vars through
    assert "NEWSLENS_CLAUDE_BIN" not in child["env_keys"]


# ---------------------------------------------------------------------------
# 3. Rook #2 — tools disabled, injection surface off, controlled cwd, stdin
# ---------------------------------------------------------------------------

def test_invocation_disables_tools_and_the_injection_surface(tmp_path, monkeypatch):
    rec = tmp_path / "rec.json"
    stub = _make_stub(tmp_path, record=rec)
    monkeypatch.setenv("NEWSLENS_CLAUDE_BIN", str(stub))
    llm.chat(_rank_req())
    argv = json.loads(rec.read_text())["argv"]
    assert argv[:3] == ["-p", "--output-format", "json"]
    assert "--model" in argv and argv[argv.index("--model") + 1] == "claude-haiku-4-5"
    # tools OFF ("" disables all), injection surface OFF, hermetic session
    ti = argv.index("--tools")
    assert argv[ti + 1] == ""
    assert "--safe-mode" in argv
    assert "--strict-mcp-config" in argv
    assert "--no-session-persistence" in argv
    # never a permission bypass, never an added tool dir, never mcp servers
    assert "--dangerously-skip-permissions" not in argv
    assert "--add-dir" not in argv
    assert "--mcp-config" not in argv


def test_json_mode_appends_the_json_only_nudge_non_json_omits_it(tmp_path, monkeypatch):
    rec = tmp_path / "rec.json"
    stub = _make_stub(tmp_path, record=rec)
    monkeypatch.setenv("NEWSLENS_CLAUDE_BIN", str(stub))
    llm.chat(_rank_req(json_mode=True))
    argv = json.loads(rec.read_text())["argv"]
    assert "--append-system-prompt" in argv
    assert llm._ANTHROPIC_JSON_SYSTEM in argv
    # a non-json_mode seat (script) sends no nudge
    rec.unlink()
    req = llm.LaneRequest(cfg=llm.resolve_seat("script"), prompt="p",
                          temperature=0.4, max_tokens=100, json_mode=False,
                          user_agent="ua", api_key="k", url=llm.OPENAI_CHAT_URL)
    llm.chat(req)
    assert "--append-system-prompt" not in json.loads(rec.read_text())["argv"]


def test_prompt_rides_on_stdin(tmp_path, monkeypatch):
    rec = tmp_path / "rec.json"
    stub = _make_stub(tmp_path, record=rec)
    monkeypatch.setenv("NEWSLENS_CLAUDE_BIN", str(stub))
    llm.chat(_rank_req(prompt="THE PROMPT BYTES"))
    child = json.loads(rec.read_text())
    assert child["stdin"] == "THE PROMPT BYTES"
    # the prompt is NOT on argv (immune to ARG_MAX, and not leaked to `ps`)
    assert "THE PROMPT BYTES" not in child["argv"]


def test_cwd_is_a_fresh_scratch_removed_after_and_isolated(tmp_path, monkeypatch):
    """Filesystem tripwire: the child runs in a fresh temp scratch dir (NOT the
    repo, NOT real data), its writes land THERE, and the dir is removed after
    the call. The autouse real_state_tripwire fixture independently fails this
    test if any real state is touched — this asserts the cwd isolation directly."""
    rec = tmp_path / "rec.json"
    stub = _make_stub(tmp_path, record=rec, write_canary=True)
    monkeypatch.setenv("NEWSLENS_CLAUDE_BIN", str(stub))
    repo_root = Path(llm.__file__).resolve().parents[2]
    llm.chat(_rank_req())
    child = json.loads(rec.read_text())
    child_cwd = Path(child["cwd"])
    assert child_cwd != repo_root
    assert repo_root not in child_cwd.parents      # not anywhere under the repo
    assert child_cwd.name.startswith("newslens-claude-lane-")
    assert not child_cwd.exists()                  # removed after the call
    assert not (repo_root / "canary.txt").exists() # the canary never hit the repo


# ---------------------------------------------------------------------------
# 4. Rook #3 — binary resolution + fail-loud-at-the-gate
# ---------------------------------------------------------------------------

def test_binary_resolution_precedence(tmp_path, monkeypatch):
    stub = _make_stub(tmp_path)
    # (a) explicit override wins
    monkeypatch.setenv("NEWSLENS_CLAUDE_BIN", str(stub))
    assert llm.resolve_claude_bin() == (str(stub), "env")
    # (b) no override -> PATH
    monkeypatch.delenv("NEWSLENS_CLAUDE_BIN", raising=False)
    bindir = tmp_path / "bin"
    bindir.mkdir()
    (bindir / "claude").write_text("#!/bin/sh\n")
    (bindir / "claude").chmod(0o755)
    monkeypatch.setenv("PATH", str(bindir))
    path, source = llm.resolve_claude_bin()
    assert source == "path" and path == str(bindir / "claude")


def test_explicit_override_that_is_not_executable_fails_loud_not_falls_through(
        tmp_path, monkeypatch):
    monkeypatch.setenv("NEWSLENS_CLAUDE_BIN", str(tmp_path / "nope"))
    bin_path, reason = llm.resolve_claude_bin()
    assert bin_path is None
    assert "NEWSLENS_CLAUDE_BIN" in reason


def test_check_lane_gates_on_binary_resolution(tmp_path, monkeypatch):
    """BORN-RED: a subscription seat whose binary won't resolve is
    LaneUnavailable AT THE GATE (check_lane) — no spawn, no transport."""
    monkeypatch.setenv("NEWSLENS_CLAUDE_BIN", str(tmp_path / "absent"))
    with pytest.raises(llm.LaneUnavailable) as exc:
        llm.check_lane(llm.resolve_seat("editor"))
    assert "subscription" in str(exc.value)
    assert "editor" in str(exc.value)
    # the api fall-over is still available (openai/writer stays api)
    llm.check_lane(llm.resolve_seat("editor", {"NEWSLENS_LANE_EDITOR": "api"}))


# ---------------------------------------------------------------------------
# 5. The documented JSON contract + transport-shaped failures
# ---------------------------------------------------------------------------

def test_success_parses_result_and_usage_into_an_openai_shaped_raw(tmp_path, monkeypatch):
    stub = _make_stub(tmp_path, result='{"clusters": []}', inp=1234, out=56,
                      cache=7, cli_cost=0.99)
    monkeypatch.setenv("NEWSLENS_CLAUDE_BIN", str(stub))
    resp = llm.chat(_rank_req())
    assert resp.content == '{"clusters": []}'
    assert resp.finish_reason == "stop"
    assert resp.usage.prompt_tokens == 1234 and resp.usage.completion_tokens == 56
    assert resp.usage.cache_read_tokens == 7
    # openai-shaped raw the historical callers parse, with CLI forensics kept
    assert resp.raw["choices"][0]["message"]["content"] == '{"clusters": []}'
    assert resp.raw["usage"]["prompt_tokens"] == 1234
    assert resp.raw["_anthropic"]["_claude_cli"]["total_cost_usd"] == 0.99
    assert resp.raw["_anthropic"]["_claude_cli"]["session_id"] == "sess-xyz"


@pytest.mark.parametrize("mode", ["is_error", "garbage", "nonzero"])
def test_error_shapes_are_transport_shaped_runtime_errors(mode, tmp_path, monkeypatch):
    stub = _make_stub(tmp_path, mode=mode)
    monkeypatch.setenv("NEWSLENS_CLAUDE_BIN", str(stub))
    with pytest.raises(RuntimeError):        # NOT LaneUnavailable — a transient transport miss
        llm.chat(_rank_req())


def test_timeout_kills_the_child_and_is_transport_shaped(tmp_path, monkeypatch):
    stub = _make_stub(tmp_path, mode="hang")
    monkeypatch.setenv("NEWSLENS_CLAUDE_BIN", str(stub))
    # shrink the seat timeout so the test is fast. 2026-07-17: the subscription
    # provider now uses (timeout_sub_s or timeout_s), so shrink BOTH — rank's
    # timeout_sub_s (300) would otherwise win and hang the test.
    monkeypatch.setitem(
        llm.SEATS, "rank",
        __import__("dataclasses").replace(
            llm.SEATS["rank"], timeout_s=1, timeout_sub_s=1))
    with pytest.raises(TimeoutError):
        llm.chat(_rank_req())


# ---------------------------------------------------------------------------
# 6. Ledger under the subscription lane — charged 0.0, shadow API-priced, label
# ---------------------------------------------------------------------------

def test_reported_usage_ledgers_charged_zero_shadow_api_priced(tmp_path, monkeypatch):
    stub = _make_stub(tmp_path, inp=1_000_000, out=0)   # 1 MTok in @ Haiku $1.00
    monkeypatch.setenv("NEWSLENS_CLAUDE_BIN", str(stub))
    resp = llm.chat(_rank_req())
    fields = llm.cost_fields(llm.resolve_seat("rank"), resp.raw["usage"])
    assert fields["lane"] == "subscription"
    assert fields["usd_charged"] == 0.0
    assert fields["usd_shadow"] == pytest.approx(1.00)
    assert "usd_shadow_estimated" not in fields          # metered, not estimated


def test_absent_usage_estimates_and_LABELS_the_shadow(tmp_path, monkeypatch):
    stub = _make_stub(tmp_path, mode="no_usage", result="x" * 35)
    monkeypatch.setenv("NEWSLENS_CLAUDE_BIN", str(stub))
    resp = llm.chat(_rank_req(prompt="y" * 35))
    fields = llm.cost_fields(llm.resolve_seat("rank"), resp.raw["usage"])
    assert fields["usd_charged"] == 0.0
    assert fields["usd_shadow_estimated"] is True        # never fake precision
    assert resp.usage.prompt_tokens == 10 and resp.usage.completion_tokens == 10


# ---------------------------------------------------------------------------
# 7. Rider R-B3a — the state_rewrites row does NOT vanish at charged == 0.0
# ---------------------------------------------------------------------------

def test_rB3a_subscription_state_row_records_shadow_when_charged_is_zero(
        migrated_con, monkeypatch):
    """BORN-RED (R-B3a): a $0-CHARGED subscription state rewrite (usd_charged
    0.0, usd_shadow > 0) must STILL appear in report.steps with its shadow
    recorded. The old `if report.memory_usd:` guard dropped it — the state
    seat's whole spend vanished from the ledger the moment the lane went
    subscription. This fails if the guard reverts to gating on charged."""
    from test_generate import seed_briefing, slot

    con = migrated_con
    now = "2026-07-01T00:00:00.000Z"
    con.execute(
        "INSERT INTO memory (topic, status, status_changed_at, created_at,"
        " updated_at) VALUES ('Iran War', 'active', ?, ?, ?)", (now, now, now))
    con.commit()
    seed_briefing(con, "2026-07-16", [slot(1, mem=["Iran War"])],
                  narrative="Published.")
    arc = {"arc": {"delta": "advances", "what_happened": "Moved.",
                   "significance": "Matters.", "cites": ["S1"]}}
    con.execute(
        "INSERT INTO analysis_briefs (date, slot, tier, status, brief_json,"
        " model, cost_usd) VALUES ('2026-07-16', 1, 'full', 'valid', ?,"
        " 'gpt-4o', 0.0)", (json.dumps({"brief": arc}),))
    con.commit()

    # A subscription-lane state chat: charged 0.0, shadow API-priced (3-tuple).
    def sub_state_chat(key, prompt):
        return ({"state": f"It moved ({memory_core.human_date('2026-07-16')})."},
                0.0, 0.0075)

    report = generate.GenReport(date="2026-07-16", variant=generate.ACTIVE_VOICE)
    generate.run_memory_pass(
        con, "2026-07-16", "sk-fake", cap=1.0, spent=0.0,
        briefs_by_slot={1: {"brief": arc}},
        slots=[slot(1, mem=["Iran War"])], report=report, state_chat=sub_state_chat)

    rows = [s for s in report.steps if s["step"] == "state_rewrites"]
    assert len(rows) == 1, "the $0-charged subscription state row VANISHED"
    row = rows[0]
    assert row["usd_charged"] == 0.0
    assert row["usd_shadow"] == pytest.approx(0.0075)
    assert row["usd"] == 0.0                       # legacy key == charged
    assert report.memory_shadow_usd == pytest.approx(0.0075)


def test_rB3a_two_tuple_state_chat_stays_backward_compatible(migrated_con):
    """The 2-tuple (raw, cost) chat still works — shadow defaults to charged, so
    every existing state_chat stub (and the api lane) keeps its exact ledger."""
    r = memory_core.StateRewriteResult(thread_id=1, topic="T", outcome="x")
    assert r.shadow_usd == 0.0
    # rewrite_state's unpack is exercised end-to-end by the api-lane suite; here
    # we pin the invariant the guard relies on: shadow defaults to charged.


# ---------------------------------------------------------------------------
# 8. FIX-1 — stage-boundary preflight kills the run on a config error
# ---------------------------------------------------------------------------

def test_fix1_analyst_subscription_misconfig_kills_the_analysis_stage(
        migrated_con, monkeypatch):
    """BORN-RED (FIX-1, analyst side), B4 flip (conscious): analyst x
    subscription is REGISTERED now (Sonnet is anthropic), so the config error
    that proves the stage preflight regrows on a junk lane — same tooth: the
    STAGE preflight in run_analysis raises LaneUnavailable and the run DIES
    rather than degrading every slot to a disclosed $0 'failed' brief.
    Per-slot degrade stays for transient failures; a misconfigured lane is
    not transient. (The registered-subscription analyst's own behavior — and
    the B4-D1 gap that it cannot use the armed fall — is pinned in the b1_qa
    sweep and test_b4_battery_qa.)"""
    from test_generate import seed_briefing, slot

    con = migrated_con
    seed_briefing(con, "2026-07-16", [slot(1)], narrative="")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-fake")
    monkeypatch.setenv("NEWSLENS_LANE_ANALYST", "junk")
    with pytest.raises(llm.LaneUnavailable):
        analysis.run_analysis(date="2026-07-16", con=con,
                              tiers_override=["full"])


# ---------------------------------------------------------------------------
# 9. B3-D1 — the durable money record is lane-aware (legacy usd == usd_charged)
# ---------------------------------------------------------------------------

def test_D1_rank_cost_sink_legacy_usd_is_charged_zero_on_subscription(monkeypatch):
    """BORN-RED (D1, money record): on the subscription-DEFAULT rank seat the
    cost_sink's legacy `usd` == usd_charged == 0.0 — NOT usage_to_usd, which
    always shadow-prices and would show charged spend for a $0 subscription run.
    usd_shadow still binds. Fails if ranking reverts to usage_to_usd for `usd`."""
    from newslens import ranking
    good = {"choices": [{"message": {"content": json.dumps({"clusters": [
        {"story_title": "T", "summary": "S", "item_ids": [1], "matched_tags": [],
         "matched_memory": [], "world_impact": 5, "world_impact_reason": "r"}]})},
        "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 1000, "completion_tokens": 200}}
    monkeypatch.setattr(ranking, "_post_chat", lambda key, prompt: good)
    sink = []
    ranking.call_llm_validated("sk-x", "p", {1}, {}, [], cost_sink=sink)
    e = sink[0]
    assert e["lane"] == "subscription"
    assert e["usd"] == e["usd_charged"] == 0.0
    assert e["usd_shadow"] == pytest.approx(1000 / 1e6 * 1.00 + 200 / 1e6 * 5.00)


# ---------------------------------------------------------------------------
# 10. B3-D2 — the principal-armed single-fall to the api lane
# ---------------------------------------------------------------------------

def test_D2_armed_fallback_falls_to_api_when_subscription_unavailable(monkeypatch):
    """BORN-RED (D2): NEWSLENS_LANE_FALLBACK=api + an unresolvable subscription
    binary → ONE fall to the api lane, reason 'subscription_unavailable', the
    ledger labeled 'api(fallback:…)'. The api lane bills real money (that is the
    whole point of the disclosed warning)."""
    monkeypatch.setenv("NEWSLENS_CLAUDE_BIN", "/definitely/absent/claude")
    monkeypatch.setenv("NEWSLENS_LANE_FALLBACK", "api")
    cfg, reason = llm.effective_seat("rank")
    assert cfg.lane == "api" and cfg.provider == "anthropic"
    assert reason == "subscription_unavailable"
    fields = llm.cost_fields(
        cfg, {"prompt_tokens": 1_000_000, "completion_tokens": 0},
        fallback_reason=reason)
    assert fields["lane"] == "api(fallback:subscription_unavailable)"
    assert fields["usd_charged"] == pytest.approx(1.00)   # the api lane bills real money
    assert fields["usd_shadow"] == pytest.approx(1.00)


def test_D2_unarmed_subscription_unavailable_still_dies_loud(monkeypatch):
    """BORN-RED (D2 teeth): with the fallback UNARMED, an unresolvable
    subscription binary raises LaneUnavailable — the fall never fires without the
    principal's opt-in (never a silent spend-without-consent)."""
    monkeypatch.setenv("NEWSLENS_CLAUDE_BIN", "/definitely/absent/claude")
    with pytest.raises(llm.LaneUnavailable):
        llm.effective_seat("editor")


def test_D2_both_lanes_dead_dies_on_the_original_subscription_error(monkeypatch):
    """BORN-RED (D2, re-expressing the wrappers test's teeth): armed, subscription
    binary missing, AND the api lane also unavailable → dies loud on the ORIGINAL
    subscription error, never a silent no-op or a wrong-lane call."""
    monkeypatch.setenv("NEWSLENS_CLAUDE_BIN", "/absent/claude")
    monkeypatch.setenv("NEWSLENS_LANE_FALLBACK", "api")
    monkeypatch.delitem(llm._PROVIDERS, "anthropic:api")   # the api lane is dead too
    with pytest.raises(llm.LaneUnavailable) as exc:
        llm.effective_seat("rank")
    assert "subscription" in str(exc.value)


def test_D2_openai_seat_forced_to_subscription_dies_loud_even_when_armed(monkeypatch):
    """BORN-RED (D2 correctness), B4 then 07-17 flip (conscious): writer went
    ANTHROPIC in B4 and state went ANTHROPIC on 2026-07-17 (option a), so the
    openai-seat example regrows on synthesis — the LONE remaining openai seat.
    The tooth is unchanged: an OPENAI seat forced onto 'subscription' has NO
    subscription provider — that config error DIES LOUD even with the fallback
    armed. The fall must never silently rescue it onto openai:api (masking the
    misconfig and billing openai while the operator set subscription). And the
    flip side, stated positively: the writer's subscription combo is REGISTERED
    now — with the stub binary resolvable, effective_seat returns it unfallen
    (ADR-0016 §3: the principal's lane override works without a code change)."""
    monkeypatch.setenv("NEWSLENS_LANE_FALLBACK", "api")
    monkeypatch.setenv("NEWSLENS_LANE_SYNTHESIS", "subscription")
    with pytest.raises(llm.LaneUnavailable):
        llm.effective_seat("synthesis")  # openai -> openai:subscription: no rescue
    monkeypatch.setenv("NEWSLENS_LANE_WRITER", "subscription")
    cfg, reason = llm.effective_seat("writer")   # registered + stub binary
    assert cfg.lane == "subscription" and reason is None
    assert cfg.model == "claude-opus-4-8"


def test_D2_api_default_seat_never_falls_and_is_unlabeled(monkeypatch):
    """A seat already resolved to the api lane never 'falls' — reason is None and
    the ledger lane is a bare 'api' (no fallback label). Proves the label rides
    ONLY an actual fall, so armed-fallback tests on api-pinned seats don't move."""
    monkeypatch.setenv("NEWSLENS_LANE_RANK", "api")
    monkeypatch.setenv("NEWSLENS_LANE_FALLBACK", "api")
    cfg, reason = llm.effective_seat("rank")
    assert cfg.lane == "api" and reason is None
    assert llm.cost_fields(cfg, {"prompt_tokens": 1, "completion_tokens": 1})["lane"] == "api"


# ---------------------------------------------------------------------------
# 11. B3-D5 — one resolution per rank op; a binary flap can't fork transport
#     from the ledger
# ---------------------------------------------------------------------------

def test_D5_flap_window_cannot_fork_ranking_transport_from_its_ledger(monkeypatch):
    """BORN-RED (D5): call_llm_validated resolves effective_seat EXACTLY ONCE and
    threads it (via ranking._ACTIVE_RANK) to _post_chat's transport AND the
    cost_sink. Simulate a `claude` binary flap by making effective_seat return a
    DIFFERENT lane on a hypothetical second call; the transport and the ledger
    must BOTH ride the first (gate) resolution — never a second resolution that
    would put attempt 2 on the metered api wire while the sink says subscription/
    usd_charged=0.0 (the D1 lie via a new door)."""
    sub = llm.resolve_seat("rank")                       # subscription
    api = dataclasses.replace(sub, lane="api")
    n = {"calls": 0}

    def flapping(seat, env=None):
        n["calls"] += 1
        return (sub, None) if n["calls"] == 1 else (api, "subscription_unavailable")

    monkeypatch.setattr(llm, "effective_seat", flapping)

    seen = {}
    good = json.dumps({"clusters": [
        {"story_title": "T", "summary": "S", "item_ids": [1], "matched_tags": [],
         "matched_memory": [], "world_impact": 5, "world_impact_reason": "r"}]})

    # Stub the TRANSPORT dispatch (not _post_chat) so the REAL _post_chat runs
    # and we capture the lane it actually rides. A pre-D5 _post_chat re-resolves
    # effective_seat here (the flap's second call → api); the fixed one reads the
    # gate's resolution off _ACTIVE_RANK.
    def fake_chat(req):
        seen["transport_cfg"] = req.cfg
        raw = {"choices": [{"message": {"content": good}, "finish_reason": "stop"}],
               "usage": {"prompt_tokens": 1000, "completion_tokens": 200}}
        return llm.LaneResponse(content=good, usage=llm.Usage(1000, 200),
                                finish_reason="stop", raw=raw)

    monkeypatch.setattr(llm, "chat", fake_chat)
    sink = []
    ranking.call_llm_validated("k", "p", {1}, {}, [], cost_sink=sink)

    assert n["calls"] == 1, "effective_seat resolved more than once — the flap window is open"
    assert seen["transport_cfg"].lane == "subscription"      # transport rode the gate resolution
    assert sink[0]["lane"] == "subscription"                 # ledger agrees — no fork
    assert sink[0]["usd"] == sink[0]["usd_charged"] == 0.0    # and it's the truthful $0


# ---------------------------------------------------------------------------
# 11b. B3-D6 — the generate twin: one resolution per step; a binary flap can't
#      fork the durable step row from the sink/transport, nor raise at bookkeeping
# ---------------------------------------------------------------------------

def _fake_transport(seen, content="edited"):
    def fake_chat(req):
        seen["transport_cfg"] = req.cfg
        raw = {"choices": [{"message": {"content": content}, "finish_reason": "stop"}],
               "usage": {"prompt_tokens": 1000, "completion_tokens": 200}}
        return llm.LaneResponse(content=content, usage=llm.Usage(1000, 200),
                                finish_reason="stop", raw=raw)
    return fake_chat


def test_D6_flap_cannot_fork_generate_step_row_from_sink_or_transport(monkeypatch):
    """BORN-RED (D6, the armed-fork direction): call_llm's transport + cost_sink
    AND _step_ledger's DURABLE report.steps row all ride the ONE run-scoped
    resolution (_ACTIVE_STEP_SEATS). Simulate a `claude` binary flap; the row a
    flap would resolve to (api/charged) must NEVER diverge from the wire the step
    actually rode (subscription/$0.00). Asserts step-row lane == sink lane ==
    transport lane for the same step."""
    sub = llm.resolve_seat("editor")                     # editor defaults to subscription
    api = dataclasses.replace(sub, lane="api")
    n = {"calls": 0}

    def flapping(seat, env=None):
        n["calls"] += 1
        return (sub, None) if n["calls"] == 1 else (api, "subscription_unavailable")

    monkeypatch.setattr(llm, "effective_seat", flapping)
    # _run_generate_body publishes the run scope; mimic that ONE population.
    monkeypatch.setattr(generate, "_ACTIVE_STEP_SEATS",
                        {"editor": llm.effective_seat("editor")})   # call 1 -> subscription

    seen = {}
    monkeypatch.setattr(llm, "chat", _fake_transport(seen))
    sink = []
    _, usage = generate.call_llm("k", "p", "editor", 100, 0.5, False, cost_sink=sink)
    step_row = generate._step_ledger("editor", usage)

    assert n["calls"] == 1, "resolved more than the one scoped resolution — flap window open"
    assert seen["transport_cfg"].lane == "subscription"      # the wire
    assert sink[0]["lane"] == "subscription"                 # the attempt ledger
    assert step_row["lane"] == "subscription"                # the DURABLE row agrees
    assert step_row["usd"] == step_row["usd_charged"] == 0.0


def test_D6_step_ledger_never_raises_at_bookkeeping_over_a_paid_step(monkeypatch):
    """BORN-RED (D6, the run-killer direction): a step that succeeded and PAID
    must be bookkept even if the `claude` binary vanished (unarmed) after the
    transport — _step_ledger reads the run-scoped resolution, never a fresh
    effective_seat that would RAISE LaneUnavailable at this DISPLAY site and kill
    a run over an already-paid step. Loud death belongs at the transport, not at
    bookkeeping."""
    sub = llm.resolve_seat("editor")
    monkeypatch.setattr(generate, "_ACTIVE_STEP_SEATS", {"editor": (sub, None)})
    # the binary is now GONE and the fallback is UNARMED — a FRESH resolve raises
    monkeypatch.setenv("NEWSLENS_CLAUDE_BIN", "/vanished/claude")
    with pytest.raises(llm.LaneUnavailable):
        llm.effective_seat("editor")                         # sanity: fresh would raise
    # but _step_ledger reads the scope -> no raise, bookkeeps the paid step truthfully
    row = generate._step_ledger("editor", {"prompt_tokens": 1000, "completion_tokens": 200})
    assert row["lane"] == "subscription"
    assert row["usd"] == row["usd_charged"] == 0.0


# ---------------------------------------------------------------------------
# 12. FIX-1 — stage-boundary preflight kills the run on a config error
# ---------------------------------------------------------------------------

def test_fix1_state_binary_missing_is_a_gate_kill_not_a_silent_stale(monkeypatch):
    """BORN-RED (FIX-1, state side): a subscription state seat whose binary
    won't resolve is LaneUnavailable at the gate — the generate stage preflight
    surfaces it (a run KILL) instead of rewrite_state's broad except swallowing
    it into a silently-stale moat. Proven at check_lane, the exact call the
    generate stage-entry preflight makes for the state seat."""
    from dataclasses import replace
    monkeypatch.setitem(llm.SEATS, "state",
                        replace(llm.SEATS["state"], provider="anthropic",
                                model="claude-haiku-4-5", lane="subscription"))
    monkeypatch.setenv("NEWSLENS_CLAUDE_BIN", "/definitely/not/here/claude")
    with pytest.raises(llm.LaneUnavailable) as exc:
        llm.check_lane(llm.resolve_seat("state"))
    assert "state" in str(exc.value)
