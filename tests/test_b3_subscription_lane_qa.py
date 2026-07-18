"""QA extensions — B3, the `claude -p` subscription lane. 2026-07-17.

Adversarial pass against the implementer's B3 handoff (trust machinery at its
most sensitive: a subprocess AGENT BINARY in the money path — full depth).
Covers the seams the born-red wiring file (test_b3_subscription_lane.py)
leaves open:

  * The stub-shim attack surface THROUGH THE CALLERS, not just llm.chat:
    transport-shaped failures (garbage stdout, is_error) must take the
    retry-ORIGINAL-bytes-once law end to end (identical stdin both spawns,
    no correction, recovery billed honestly); a hung child is SIGKILLed
    (pid provably dead, scratch dir provably gone); enormous stdout does not
    deadlock; the estimated-usage path labels the LEDGER row
    (usd_shadow_estimated=True through call_llm's sink — never silently
    precise); stderr noise on success is ignored; degenerate payload shapes
    (JSON array, empty stdout, missing result) fail transport-shaped or
    degrade to empty content, never crash-classes the callers can't route.
  * Env hygiene in its STRONGEST form: the child env is a SUBSET of the
    allowlist under a hostile parent (every key-shaped var + canaries set),
    HOME rides (the CLI's own auth needs it), and the allowlist tuple itself
    is pinned so the next entry is a conscious flip. cwd: empty at spawn,
    outside the repo AND the data sandbox, removed on success AND failure.
  * Binary resolution: the default-leg (~/.local/bin/claude) precedence is
    LIVE (proven against a synthetic default — that hole is why the conftest
    stub pin is load-bearing), PATH beats default, non-executable PATH
    candidates are skipped, and the conftest guard itself is verified both
    ways: the sandbox resolves the stub for every test, and WITHOUT the pin
    resolution escapes to the machine default (the test_preinstall_doctor
    pinhole, fixed this pass, is pinned by regression here).
  * Lane priority: the multi-user flip (NEWSLENS_LANE=api) moves all three
    subscription seats to the api lane with ZERO spawns; per-seat overrides
    move only their seat.
  * Caps-bind-on-shadow, END TO END: a $0-charged subscription state chat
    exhausts run_memory_pass's cap (thread 2 skipped-budget); a full
    default-lane generate run completes with editor/script charged 0.00 and
    shadow ledgered; the same run with a shadow-huge editor DIES at the
    script budget guard while the failed run's money record shows charged
    pennies — the cap provably binds on shadow, not charged.
  * FIX-1 stage preflights at GENERATE entry — the liveness tests only those
    two landed lines can flip (the born-red file proves run_analysis's and
    check_lane's own arms; generate's two preflight lines sit ABOVE broad
    stage-degrade excepts and needed their own bite): an analyst misconfig
    kills a refresh run raw, a state misconfig kills a no-refresh run raw,
    both before any model call or persist.
  * R-B3a hardening: the failed-but-paid exception path carries shadow into
    the stale row AND the state_rewrites step row still exists (shadow-gated)
    when charged is 0; a legacy 2-tuple chat rides rewrite_state end to end.
  * Doctor: the subscription section resolves/FAILs correctly, and its ONLY
    child invocation is `--version` — never `-p` (the doctor must not spend
    quota; the probe flag prints its design and still does not fire).

RED ACCEPTANCE CONTRACTS in this file:
  * test_run_rank_persisted_token_cost_is_charged_honest_on_subscription —
    defect B3-D1 (written failing 2026-07-17; CONSCIOUSLY FLIPPED GREEN by
    the loop-2 ranking fix the same day — flip history in its docstring).
  * test_flap_window_cannot_fork_ranking_transport_from_its_ledger — defect
    B3-D5 (loop 2, written failing 2026-07-17): ranking resolves
    effective_seat TWICE per call (gate for the ledger, _post_chat for the
    transport); the resolution is filesystem-dependent since D2, so a binary
    flap between the two forks transport from ledger — the D1 invariant's
    race window. Fix contract in the docstring.

Zero live calls, zero real-CLI spawns, $0: every subprocess in this file is
a stub shim written into the test sandbox; the autouse conftest guards
(scrub_env, sandbox_paths with the stub NEWSLENS_CLAUDE_BIN, loopback-only
network, real_state_tripwire) stand under everything here.
"""

from __future__ import annotations

import dataclasses
import errno
import json
import os
import stat
import textwrap
import time
import urllib.request
from pathlib import Path
from types import SimpleNamespace

import pytest

from newslens import doctor, generate, llm, memory_core, paths, ranking
from test_generate import (A_DAY, ENV, compliant_script, run, seed_briefing,
                           slot, stories_payload)

# ---------------------------------------------------------------------------
# Stateful stub shim: per-call scripted behaviour (the counter lives in the
# shim's own dir — the child's cwd is an ephemeral scratch and its env is
# stripped, so state must ride embedded absolute paths), full per-call
# recording (argv/env/cwd/stdin/pid/cwd-listing) to rec-<n>.json.
# ---------------------------------------------------------------------------

def make_scripted_stub(dir_path: Path, specs, version="2.1.212 (QA scripted stub)"):
    """`specs` is a list of per-call dicts consumed in call order (the last
    repeats). Keys: mode (success|garbage|nonzero|is_error|hang|no_usage),
    result (str), inp/out/cache (ints), huge (int -> result of that many x)."""
    dir_path.mkdir(parents=True, exist_ok=True)
    src = textwrap.dedent("""\
        #!/usr/bin/env python3
        import sys, os, json, time
        if '--version' in sys.argv[1:]:
            print({version!r}); sys.exit(0)
        DIR = {dir_path!r}
        cnt = os.path.join(DIR, "calls.count")
        n = 1
        if os.path.exists(cnt):
            with open(cnt) as f:
                n = int(f.read().strip() or 0) + 1
        with open(cnt, "w") as f:
            f.write(str(n))
        SPECS = json.loads({specs_json!r})
        spec = SPECS[min(n - 1, len(SPECS) - 1)]
        data = sys.stdin.read()
        with open(os.path.join(DIR, "rec-%d.json" % n), "w") as f:
            json.dump({{"argv": sys.argv[1:], "cwd": os.getcwd(),
                       "cwd_listing": sorted(os.listdir(os.getcwd())),
                       "env_keys": sorted(os.environ.keys()),
                       "env_home": os.environ.get("HOME"),
                       "stdin": data, "pid": os.getpid()}}, f)
        mode = spec.get("mode", "success")
        if spec.get("self_destruct"):
            # flap simulation: this call succeeds, then the binary is GONE —
            # the next resolution sees an unresolvable path (deterministic
            # stand-in for a CLI uninstall/upgrade mid-call).
            try:
                os.remove(sys.argv[0])
            except OSError:
                pass
        if mode == "hang":
            time.sleep(30)
        if mode == "nonzero":
            sys.stderr.write(spec.get("stderr", "stub boom"))
            sys.exit(int(spec.get("code", 3)))
        if mode == "garbage":
            sys.stdout.write(spec.get("stdout", "not json at all"))
            sys.exit(0)
        if mode == "is_error":
            print(json.dumps({{"type": "result", "is_error": True,
                               "result": "the model refused"}}))
            sys.exit(0)
        result = spec.get("result", "{{}}")
        if spec.get("huge"):
            result = "x" * int(spec["huge"])
        payload = {{"type": "result", "subtype": "success", "is_error": False,
                   "result": result, "session_id": "qa-scripted",
                   "total_cost_usd": 0.0}}
        if mode != "no_usage":
            payload["usage"] = {{"input_tokens": spec.get("inp", 1000),
                                "output_tokens": spec.get("out", 200),
                                "cache_read_input_tokens": spec.get("cache", 0)}}
        if spec.get("stderr_noise"):
            sys.stderr.write(spec["stderr_noise"])
        print(json.dumps(payload))
        """).format(version=version, dir_path=str(dir_path),
                    specs_json=json.dumps(specs))
    shim = dir_path / "claude"
    shim.write_text(src)
    shim.chmod(shim.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return shim


def stub_calls(dir_path: Path):
    recs = sorted(dir_path.glob("rec-*.json"),
                  key=lambda p: int(p.stem.split("-")[1]))
    return [json.loads(p.read_text()) for p in recs]


def _no_sleep(monkeypatch):
    monkeypatch.setattr(time, "sleep", lambda s: None)


def _http_tripwire(monkeypatch):
    calls = []

    def tripwire(req, timeout=None):
        calls.append(req.full_url)
        raise AssertionError("HTTP transport reached: " + req.full_url)

    monkeypatch.setattr(urllib.request, "urlopen", tripwire)
    return calls


_RANK_CLUSTERS = {"clusters": [{"story_title": "T", "summary": "S",
                                "item_ids": [1], "matched_tags": [],
                                "matched_memory": [], "world_impact": 5,
                                "world_impact_reason": "r"}]}


# ===========================================================================
# 1. Transport-shaped failures take the retry-ORIGINAL law through the caller
# ===========================================================================

@pytest.mark.parametrize("first_mode", ["garbage", "is_error"])
def test_rank_transport_miss_retries_original_bytes_once_and_recovers(
        first_mode, tmp_path, monkeypatch):
    """ADR-0015 §2's claim, proven END TO END (the born-red file stops at
    llm.chat): garbage stdout / is_error=true are transport-shaped, so the
    rank caller re-sends the ORIGINAL prompt bytes (no RETRY_CORRECTION — a
    corrected retry would be the wrong law for a transport miss), recovers on
    the second spawn, and the sink records ONLY the attempt that returned
    usage (nothing billed on the miss => nothing recorded)."""
    _no_sleep(monkeypatch)
    http = _http_tripwire(monkeypatch)
    stub = make_scripted_stub(
        tmp_path / "shim",
        [{"mode": first_mode},
         {"mode": "success", "result": json.dumps(_RANK_CLUSTERS),
          "inp": 1000, "out": 200}])
    monkeypatch.setenv("NEWSLENS_CLAUDE_BIN", str(stub))
    sink = []
    clusters, usage = ranking.call_llm_validated(
        "sk-openai-unused", "ORIGINAL-RANK-PROMPT", {1}, {}, [],
        cost_sink=sink)
    assert [c["item_ids"] for c in clusters] == [[1]]
    calls = stub_calls(tmp_path / "shim")
    assert len(calls) == 2
    assert calls[0]["stdin"] == calls[1]["stdin"] == "ORIGINAL-RANK-PROMPT"
    assert ranking.RETRY_CORRECTION not in calls[1]["stdin"]
    # money honesty: the failed draw returned no usage -> exactly one row.
    # (The row's LEGACY `usd` parity is deliberately NOT asserted here: it is
    # defect B3-D1's contract, owned by its two designated red tests — the
    # sink twin in test_b1_llm_seam_qa and the persisted twin below.)
    assert [(e["attempt"], e["lane"], e["usd_charged"]) for e in sink] \
        == [(2, "subscription", 0.0)]
    assert sink[0]["usd_shadow"] == pytest.approx(1000 / 1e6 * 1.00
                                                  + 200 / 1e6 * 5.00)
    assert http == []


def test_rank_persistent_transport_failure_dies_loud_after_one_retry(
        tmp_path, monkeypatch):
    stub = make_scripted_stub(tmp_path / "shim", [{"mode": "is_error"}])
    monkeypatch.setenv("NEWSLENS_CLAUDE_BIN", str(stub))
    _no_sleep(monkeypatch)
    sink = []
    with pytest.raises(ranking.RankingError):
        ranking.call_llm_validated("k", "p", {1}, {}, [], cost_sink=sink)
    assert len(stub_calls(tmp_path / "shim")) == 2      # once + ONE retry
    assert sink == []                                    # $0, recorded as $0


def test_editor_subscription_transport_error_disarms_the_seat(
        tmp_path, monkeypatch):
    """The b2 disarm suite's subscription twin: a GenerateError born from a
    subprocess failure (nonzero exit, both attempts) must still walk
    call_llm's finally and disarm _ACTIVE_SEAT_CFG."""
    _no_sleep(monkeypatch)
    stub = make_scripted_stub(tmp_path / "shim", [{"mode": "nonzero"}])
    monkeypatch.setenv("NEWSLENS_CLAUDE_BIN", str(stub))
    with pytest.raises(generate.GenerateError) as exc:
        generate.call_llm("k", "p", "editor", 100, 0.5, False)
    assert "failed after one retry" in str(exc.value)
    assert generate._ACTIVE_SEAT_CFG is None


# ===========================================================================
# 2. Timeout: the child is provably DEAD and the scratch dir provably gone
# ===========================================================================

def test_timeout_leaves_no_orphan_child_and_no_scratch_dir(
        tmp_path, monkeypatch):
    """Onna's launchd-orphan condition, mechanically: the hang stub records
    its pid BEFORE sleeping; after TimeoutError that pid must be gone (give
    SIGKILL a short grace), and the scratch cwd must be removed (the finally
    runs on the timeout path too)."""
    stub = make_scripted_stub(tmp_path / "shim", [{"mode": "hang"}])
    monkeypatch.setenv("NEWSLENS_CLAUDE_BIN", str(stub))
    # 2026-07-17: shrink BOTH timeouts — the subscription provider uses
    # (timeout_sub_s or timeout_s), and rank's timeout_sub_s (300) would win.
    monkeypatch.setitem(llm.SEATS, "rank",
                        dataclasses.replace(llm.SEATS["rank"],
                                            timeout_s=1, timeout_sub_s=1))
    req = llm.LaneRequest(cfg=llm.resolve_seat("rank"), prompt="p",
                          temperature=0, max_tokens=10, json_mode=True,
                          user_agent="ua", api_key="k")
    with pytest.raises(TimeoutError) as exc:
        llm.chat(req)
    assert "killed" in str(exc.value)
    rec = stub_calls(tmp_path / "shim")[0]
    # pid dead (ESRCH) — poll briefly to absorb kill/reap latency
    deadline = time.time() + 3.0
    alive = True
    while time.time() < deadline:
        try:
            os.kill(rec["pid"], 0)
        except OSError as e:
            assert e.errno == errno.ESRCH
            alive = False
            break
        time.sleep(0.05)
    assert not alive, f"child {rec['pid']} still alive after the timeout kill"
    assert not Path(rec["cwd"]).exists()      # scratch removed on the timeout path


def test_scratch_dir_removed_even_when_the_child_fails(tmp_path, monkeypatch):
    stub = make_scripted_stub(tmp_path / "shim", [{"mode": "nonzero"}])
    monkeypatch.setenv("NEWSLENS_CLAUDE_BIN", str(stub))
    with pytest.raises(RuntimeError):
        llm.chat(llm.LaneRequest(cfg=llm.resolve_seat("rank"), prompt="p",
                                 temperature=0, max_tokens=10, json_mode=True,
                                 user_agent="ua", api_key="k"))
    rec = stub_calls(tmp_path / "shim")[0]
    assert not Path(rec["cwd"]).exists()


# ===========================================================================
# 3. Payload hostility: huge output, stderr noise, degenerate JSON shapes
# ===========================================================================

def test_enormous_stdout_does_not_deadlock_and_round_trips(
        tmp_path, monkeypatch):
    """5MB of result through the pipes: subprocess.run's communicate must
    drain concurrently (a naive wait() deadlocks on full pipes). Bounded by
    the seat timeout so a regression fails fast instead of hanging QA."""
    stub = make_scripted_stub(tmp_path / "shim", [{"huge": 5_000_000}])
    monkeypatch.setenv("NEWSLENS_CLAUDE_BIN", str(stub))
    resp = llm.chat(llm.LaneRequest(cfg=llm.resolve_seat("rank"), prompt="p",
                                    temperature=0, max_tokens=10,
                                    json_mode=True, user_agent="ua",
                                    api_key="k"))
    assert len(resp.content) == 5_000_000
    assert resp.content == "x" * 5_000_000


def test_stderr_noise_on_a_successful_exit_is_ignored(tmp_path, monkeypatch):
    stub = make_scripted_stub(
        tmp_path / "shim",
        [{"result": "clean result", "stderr_noise":
          "npm WARN deprecated something\nDeprecationWarning: telemetry\n"}])
    monkeypatch.setenv("NEWSLENS_CLAUDE_BIN", str(stub))
    resp = llm.chat(llm.LaneRequest(cfg=llm.resolve_seat("rank"), prompt="p",
                                    temperature=0, max_tokens=10,
                                    json_mode=True, user_agent="ua",
                                    api_key="k"))
    assert resp.content == "clean result"


def test_unicode_result_round_trips_through_the_pipes(tmp_path, monkeypatch):
    text = "Überblick — 東京の動き; emoji \U0001F30D; quotes »…«"
    stub = make_scripted_stub(tmp_path / "shim", [{"result": text}])
    monkeypatch.setenv("NEWSLENS_CLAUDE_BIN", str(stub))
    resp = llm.chat(llm.LaneRequest(cfg=llm.resolve_seat("rank"),
                                    prompt="prömpt — stdin bytes",
                                    temperature=0, max_tokens=10,
                                    json_mode=True, user_agent="ua",
                                    api_key="k"))
    assert resp.content == text


@pytest.mark.parametrize("spec, exc_fragment", [
    ({"mode": "garbage", "stdout": "[1, 2, 3]"}, "error result"),   # JSON, not a dict
    ({"mode": "garbage", "stdout": ""}, "non-JSON stdout"),          # empty stdout
    ({"mode": "garbage", "stdout": '{"result": "trunca'}, "non-JSON stdout"),
])
def test_degenerate_stdout_shapes_are_transport_shaped(
        spec, exc_fragment, tmp_path, monkeypatch):
    stub = make_scripted_stub(tmp_path / "shim", [spec])
    monkeypatch.setenv("NEWSLENS_CLAUDE_BIN", str(stub))
    with pytest.raises(RuntimeError) as exc:
        llm.chat(llm.LaneRequest(cfg=llm.resolve_seat("rank"), prompt="p",
                                 temperature=0, max_tokens=10, json_mode=True,
                                 user_agent="ua", api_key="k"))
    assert not isinstance(exc.value, llm.LaneUnavailable)   # transient, not config
    assert exc_fragment in str(exc.value)


def test_missing_result_field_degrades_to_empty_content_with_metered_usage(
        tmp_path, monkeypatch):
    """`result` absent but usage present: content is '' (the caller's own
    json.loads/validator handles emptiness exactly as an empty api reply) and
    the METERED usage still ledgers — never an exception the callers can't
    route, never fabricated content."""
    stub = make_scripted_stub(tmp_path / "shim",
                              [{"result": "", "inp": 42, "out": 0}])
    monkeypatch.setenv("NEWSLENS_CLAUDE_BIN", str(stub))
    resp = llm.chat(llm.LaneRequest(cfg=llm.resolve_seat("rank"), prompt="p",
                                    temperature=0, max_tokens=10,
                                    json_mode=True, user_agent="ua",
                                    api_key="k"))
    assert resp.content == ""
    assert resp.usage.prompt_tokens == 42
    assert "_token_source" not in resp.raw["usage"]


def test_estimated_usage_label_reaches_the_call_llm_ledger_row(
        tmp_path, monkeypatch):
    """The never-fake-precision mandate at the LEDGER, not just cost_fields:
    a no-usage reply produces a call_llm sink row with usd_shadow_estimated
    True; the metered control row (same run shape) carries NO label."""
    _no_sleep(monkeypatch)
    stub = make_scripted_stub(tmp_path / "shim",
                              [{"mode": "no_usage", "result": "e" * 350}])
    monkeypatch.setenv("NEWSLENS_CLAUDE_BIN", str(stub))
    sink = []
    generate.call_llm("k", "p" * 700, "editor", 100, 0.5, False,
                      cost_sink=sink)
    row = sink[0]
    assert row["usd_shadow_estimated"] is True
    assert row["lane"] == "subscription"
    assert row["usd"] == row["usd_charged"] == 0.0
    assert row["usd_shadow"] > 0.0            # estimated, but present and capped-on
    # metered control: the sandbox default stub reports usage
    monkeypatch.setenv("NEWSLENS_CLAUDE_BIN",
                       os.environ["NEWSLENS_CLAUDE_BIN"])
    stub2 = make_scripted_stub(tmp_path / "shim2",
                               [{"result": "ok", "inp": 10, "out": 10}])
    monkeypatch.setenv("NEWSLENS_CLAUDE_BIN", str(stub2))
    sink2 = []
    generate.call_llm("k", "p", "editor", 100, 0.5, False, cost_sink=sink2)
    assert "usd_shadow_estimated" not in sink2[0]


# ===========================================================================
# 4. Env hygiene — the subset law under a hostile parent; the allowlist pin
# ===========================================================================

def test_child_env_is_a_strict_subset_of_the_allowlist_under_hostile_parent(
        tmp_path, monkeypatch):
    """Stronger than name-by-name absence: with every key-shaped var, proxy
    var, and a planted canary exported in the parent, the child env's keys
    must be a SUBSET of _SUBSCRIPTION_ENV_ALLOW (allowlist, not blocklist —
    Rook #1's construction, proven as a construction). HOME rides (the CLI's
    subscription auth lives under it)."""
    hostile = {
        "ANTHROPIC_API_KEY": "sk-ant-bill-me",
        "OPENAI_API_KEY": "sk-openai",
        "PERPLEXITY_API_KEY": "pplx-x",
        "GNEWS_API_KEY": "gnews-x",
        "AWS_SECRET_ACCESS_KEY": "aws-x",
        "NEWSLENS_QA_CANARY_SECRET": "canary",
        "HTTP_PROXY": "http://mitm.example:8080",
        "LD_PRELOAD": "/tmp/evil.so",
        "PYTHONSTARTUP": "/tmp/evil.py",
    }
    for k, v in hostile.items():
        monkeypatch.setenv(k, v)
    stub = make_scripted_stub(tmp_path / "shim", [{}])
    monkeypatch.setenv("NEWSLENS_CLAUDE_BIN", str(stub))
    llm.chat(llm.LaneRequest(cfg=llm.resolve_seat("rank"), prompt="p",
                             temperature=0, max_tokens=10, json_mode=True,
                             user_agent="ua", api_key="k"))
    rec = stub_calls(tmp_path / "shim")[0]
    child_keys = set(rec["env_keys"])
    allow = set(llm._SUBSCRIPTION_ENV_ALLOW)
    # Observed INSIDE a python child on macOS, two names appear that the
    # parent never passed: __CF_USER_TEXT_ENCODING (injected by the OS at
    # exec for every spawned process) and LC_CTYPE (CPython's PEP 538 C.UTF-8
    # locale coercion sets it in its OWN environ when no LC_* rides in).
    # Neither is a parent leak — the subset law is asserted over everything
    # else, and the hostile names are asserted absent by name below.
    platform_injected = {"__CF_USER_TEXT_ENCODING", "LC_CTYPE"}
    assert child_keys - platform_injected <= allow, (
        f"beyond the allowlist: {child_keys - platform_injected - allow}")
    for name in hostile:
        assert name not in child_keys, name
    # the sandbox's own redirections must not ride either (defense in depth:
    # the child needs NOTHING of NewsLens)
    assert "NEWSLENS_DATA_DIR" not in child_keys
    assert "NEWSLENS_CLAUDE_BIN" not in child_keys
    assert rec["env_home"] == os.environ.get("HOME")   # auth discovery intact


def test_subscription_env_allowlist_tuple_is_pinned():
    """The allowlist IS the security boundary — growing it must be a
    conscious, test-breaking act. No credential-shaped name may ever join."""
    assert llm._SUBSCRIPTION_ENV_ALLOW == (
        "HOME", "PATH", "USER", "LOGNAME", "LANG", "LC_ALL", "TERM", "TMPDIR")
    for name in llm._SUBSCRIPTION_ENV_ALLOW:
        assert "KEY" not in name and "TOKEN" not in name and \
            "SECRET" not in name


def test_child_cwd_is_empty_at_spawn_and_outside_repo_and_data_sandbox(
        tmp_path, monkeypatch):
    stub = make_scripted_stub(tmp_path / "shim", [{}])
    monkeypatch.setenv("NEWSLENS_CLAUDE_BIN", str(stub))
    llm.chat(llm.LaneRequest(cfg=llm.resolve_seat("rank"), prompt="p",
                             temperature=0, max_tokens=10, json_mode=True,
                             user_agent="ua", api_key="k"))
    rec = stub_calls(tmp_path / "shim")[0]
    assert rec["cwd_listing"] == []                      # EMPTY scratch
    child_cwd = Path(rec["cwd"]).resolve()
    repo = Path(llm.__file__).resolve().parents[2]
    assert repo != child_cwd and repo not in child_cwd.parents
    data_dir = Path(os.environ["NEWSLENS_DATA_DIR"]).resolve()
    assert data_dir != child_cwd and data_dir not in child_cwd.parents
    assert not child_cwd.exists()                        # removed after


# ===========================================================================
# 5. Binary resolution — the default leg is live; the conftest guard bites
# ===========================================================================

def _fake_claude(dir_path: Path, name="claude", executable=True) -> Path:
    dir_path.mkdir(parents=True, exist_ok=True)
    p = dir_path / name
    p.write_text("#!/bin/sh\nexit 0\n")
    if executable:
        p.chmod(0o755)
    return p


def test_default_leg_resolves_when_env_and_path_are_empty(tmp_path, monkeypatch):
    """The ~/.local/bin/claude default leg is LIVE code, not documentation —
    proven against a synthetic default so no real binary is ever touched.
    This hole is exactly why the conftest's env pin is load-bearing."""
    fake_default = _fake_claude(tmp_path / "localbin")
    monkeypatch.setattr(llm, "CLAUDE_BIN_DEFAULT", str(fake_default))
    env = {"PATH": str(tmp_path / "emptybin")}
    assert llm.resolve_claude_bin(env) == (str(fake_default), "default")
    # non-executable default: nothing resolves, reason names the fix surface
    fake_default.chmod(0o644)
    bin_path, reason = llm.resolve_claude_bin(env)
    assert bin_path is None
    assert "NEWSLENS_CLAUDE_BIN" in reason and "PATH" in reason


def test_path_beats_default_and_non_executable_path_candidate_is_skipped(
        tmp_path, monkeypatch):
    fake_default = _fake_claude(tmp_path / "localbin")
    monkeypatch.setattr(llm, "CLAUDE_BIN_DEFAULT", str(fake_default))
    on_path = _fake_claude(tmp_path / "pathbin")
    env = {"PATH": str(tmp_path / "pathbin")}
    assert llm.resolve_claude_bin(env) == (str(on_path), "path")
    # a non-executable PATH candidate is skipped -> falls through to default
    on_path.chmod(0o644)
    assert llm.resolve_claude_bin(env) == (str(fake_default), "default")


def test_conftest_stub_pin_holds_for_every_test_and_is_what_chat_spawns():
    """Guard liveness, positive half: under the sandbox, resolution lands on
    the conftest stub via the ENV leg for every test by construction, and a
    default-lane chat provably executes THAT shim (its canned session_id
    surfaces in the response forensics)."""
    bin_path, source = llm.resolve_claude_bin()
    assert source == "env"
    assert bin_path == os.environ["NEWSLENS_CLAUDE_BIN"]
    assert Path(bin_path).name == "claude"
    assert "newslens-qa-stub-claude" in bin_path         # the conftest shim dir
    resp = llm.chat(llm.LaneRequest(cfg=llm.resolve_seat("rank"), prompt="p",
                                    temperature=0, max_tokens=10,
                                    json_mode=True, user_agent="ua",
                                    api_key="k"))
    assert resp.raw["_anthropic"]["_claude_cli"]["session_id"] == "stub-session"


def test_without_the_pin_resolution_escapes_to_the_machine_default(
        tmp_path, monkeypatch):
    """Guard liveness, negative half (the bite): drop ONLY the env pin and
    resolution falls through to CLAUDE_BIN_DEFAULT — i.e. on this machine the
    REAL ~/.local/bin/claude. Proven against a synthetic default (a stat, no
    spawn), machine-independent: the conftest pin is the ONLY thing standing
    between the suite and the real agent binary."""
    fake_real = _fake_claude(tmp_path / "machine-default")
    monkeypatch.setattr(llm, "CLAUDE_BIN_DEFAULT", str(fake_real))
    env = {k: v for k, v in os.environ.items() if k != "NEWSLENS_CLAUDE_BIN"}
    env["PATH"] = str(tmp_path / "no-claude-on-path")
    bin_path, source = llm.resolve_claude_bin(env)
    assert (bin_path, source) == (str(fake_real), "default")


def test_preinstall_doctor_child_env_pins_the_binary_var():
    """Regression for the 2026-07-17 pinhole: test_preinstall_doctor's
    hand-built doctor-child env used to omit NEWSLENS_CLAUDE_BIN, so that
    child resolved and SPAWNED the real ~/.local/bin/claude for --version (a
    real agent-binary execution from inside the suite, invisible to the
    sitecustomize socket spy — it patches python's socket, not a node
    grandchild's). The hand-built env must pin the var to something that
    does NOT resolve."""
    from test_preinstall_doctor import _scrubbed_env
    env = _scrubbed_env(Path("/tmp/qa-probe-never-created"))
    assert "NEWSLENS_CLAUDE_BIN" in env
    bin_path, _ = llm.resolve_claude_bin(env)
    assert bin_path is None


# ===========================================================================
# 6. Lane priority — the multi-user flip and per-seat fall-over
# ===========================================================================

def test_global_api_flip_moves_all_subscription_seats_with_zero_spawns(
        monkeypatch, fake_api):
    """'Multi-user flip = NEWSLENS_LANE=api. Config, not code.' — under the
    global flip every anthropic seat resolves api and a rank round trips the
    LOOPBACK wire with zero subprocess spawns."""
    _no_sleep(monkeypatch)
    spawns = []

    def _spawn_tripwire(*a, **k):
        spawns.append(a)
        raise AssertionError("subprocess spawned under the global api flip")

    monkeypatch.setattr(llm.subprocess, "run", _spawn_tripwire)
    monkeypatch.setenv("NEWSLENS_LANE", "api")
    for seat in ("rank", "editor", "script"):
        assert llm.resolve_seat(seat).lane == "api"
    monkeypatch.setattr(llm, "ANTHROPIC_MESSAGES_URL",
                        fake_api.base_url + "/v1/messages")
    monkeypatch.setenv("ANTHROPIC_API_KEY", fake_api.good_key)
    from conftest import anthropic_envelope
    fake_api.add_route("/v1/messages", status=200,
                       body=anthropic_envelope(_RANK_CLUSTERS),
                       content_type="application/json")
    sink = []
    ranking.call_llm_validated("k", "p", {1}, {}, [], cost_sink=sink)
    assert sink[0]["lane"] == "api"
    assert sink[0]["usd"] == sink[0]["usd_charged"] == sink[0]["usd_shadow"]
    assert spawns == []


def test_per_seat_api_override_moves_only_that_seat(monkeypatch):
    monkeypatch.setenv("NEWSLENS_LANE_RANK", "api")
    assert llm.resolve_seat("rank").lane == "api"
    assert llm.resolve_seat("editor").lane == "subscription"
    assert llm.resolve_seat("script").lane == "subscription"


def test_nested_cross_transport_call_restores_the_outer_http_seat(
        monkeypatch):
    """The b2 stack-discipline proof ACROSS TRANSPORT KINDS: an openai-HTTP
    narrative whose validator spawns a subscription-SUBPROCESS editor call.
    The outer retry must return to the HTTP wire (a leaked editor seat would
    route it into the subprocess and the HTTP recorder would see one call),
    and each ledger row carries its own step's seat and its own lane's
    charged semantics."""
    _no_sleep(monkeypatch)
    http = []

    class _R:
        def __init__(self, b):
            self._b = b

        def read(self):
            return self._b

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    # item C (2026-07-17): the writer defaults to subscription now; pin its api
    # fall-over so the outer narrative rides the ANTHROPIC HTTP wire (Opus/api) —
    # the fake serves the anthropic envelope. The cross-transport tooth (HTTP
    # outer vs subprocess inner editor, outer retry restored to HTTP) is unchanged.
    monkeypatch.setenv("NEWSLENS_LANE_WRITER", "api")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-qa")

    def fake_urlopen(req, timeout=None):
        http.append(req.full_url)
        return _R(json.dumps({
            "id": "msg_b3qa", "type": "message", "role": "assistant",
            "model": "claude-opus-4-8",
            "content": [{"type": "text", "text": "outer"}],
            "stop_reason": "end_turn",
            "usage": {"input_tokens": 1000, "output_tokens": 200},
        }).encode())

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    sink = []
    state = {"inner_ran": False}

    def validate(content):
        if not state["inner_ran"]:
            state["inner_ran"] = True
            inner, _ = generate.call_llm("k", "INNER", "editor", 50, 0.5,
                                         False, cost_sink=sink)
            assert inner == "{}"              # the conftest stub's canned result
            raise ValueError("outer draft rejected once")

    content, _ = generate.call_llm("k", "OUTER", "narrative", 100, 0.3, True,
                                   validate=validate, cost_sink=sink)
    assert content == "outer"
    assert len(http) == 2                     # outer attempt 1 + outer retry
    rows = [(e["step"], e["attempt"], e["lane"], e["usd_charged"] == 0.0)
            for e in sink]
    assert rows == [
        ("narrative", 1, "api", False),
        ("editor", 1, "subscription", True),
        ("narrative", 2, "api", False),
    ]
    assert generate._ACTIVE_SEAT_CFG is None


# ===========================================================================
# 7. Caps bind on SHADOW — memory pass, and the full generate loop
# ===========================================================================

def _seed_two_threads(con):
    now = "2026-07-01T00:00:00.000Z"
    tids = {}
    for topic in ("Iran War", "Chip Export Rules"):
        cur = con.execute(
            "INSERT INTO memory (topic, status, status_changed_at, created_at,"
            " updated_at) VALUES (?, 'active', ?, ?, ?)", (topic, now, now, now))
        tids[topic] = cur.lastrowid
    con.commit()
    arc = {"arc": {"delta": "advances", "what_happened": "Moved.",
                   "significance": "Matters.", "cites": ["S1"]}}
    seed_briefing(con, "2026-07-16",
                  [slot(1, mem=["Iran War"]), slot(2, mem=["Chip Export Rules"])],
                  narrative="Published.")
    for i in (1, 2):
        con.execute(
            "INSERT INTO analysis_briefs (date, slot, tier, status, brief_json,"
            " model, cost_usd) VALUES ('2026-07-16', ?, 'full', 'valid', ?,"
            " 'gpt-4o', 0.0)", (i, json.dumps({"brief": arc})))
    con.commit()
    return arc


def test_memory_pass_cap_binds_on_shadow_not_charged(migrated_con):
    """Onna's law in the REAL loop: two threads, a subscription state chat
    billing charged 0.0 / shadow 0.30 per rewrite, cap 0.302. Thread 1 spends
    shadow 0.30; thread 2's estimate (~$0.007 at state prices) must then
    EXCEED cap - spent (0.002) and be skipped-budget. If `spent` accumulated
    CHARGED (0.0 — the pre-B3 code), thread 2 would run — this test is the
    red that bites on that revert."""
    arc = _seed_two_threads(migrated_con)

    def sub_state_chat(key, prompt):
        return ({"state": f"Moved ({memory_core.human_date('2026-07-16')})."},
                0.0, 0.30)

    report = generate.GenReport(date="2026-07-16", variant=generate.ACTIVE_VOICE)
    generate.run_memory_pass(
        migrated_con, "2026-07-16", "sk-fake", cap=0.302, spent=0.0,
        briefs_by_slot={1: {"brief": arc}, 2: {"brief": arc}},
        slots=[slot(1, mem=["Iran War"]), slot(2, mem=["Chip Export Rules"])],
        report=report, state_chat=sub_state_chat)
    outcomes = {r["thread"]: r["outcome"] for r in report.memory["state"]} \
        if "state" in report.memory else {}
    if not outcomes:  # fall back to the warning surface if the shape differs
        outcomes = {}
    rewrites = [s for s in report.steps if s["step"] == "state_rewrites"]
    assert len(rewrites) == 1
    assert rewrites[0]["usd_charged"] == 0.0
    assert rewrites[0]["usd_shadow"] == pytest.approx(0.30)   # thread 1 only
    assert report.memory_shadow_usd == pytest.approx(0.30)
    assert report.memory_usd == 0.0
    # thread 2 was budget-skipped: its skip is disclosed in the warnings
    assert any("skipped-budget" in w for w in report.warnings), report.warnings


def _generate_harness(monkeypatch, con, editor_inp, editor_out=200):
    """A full no-refresh generate run. item C (2026-07-17): the writer defaults
    to the subscription lane now, so this harness PINS it to its api fall-over
    (NEWSLENS_LANE_WRITER=api) to keep the narrative on the ANTHROPIC HTTP wire —
    the fake serves the anthropic envelope for /v1/messages and refuses any other
    HTTP target (so audio degrades exactly like the fake_model harnesses). That
    keeps the run genuinely MIXED (narrative charged on the api wire; editor+
    script on a per-call scripted subscription stub, $0 charged — call 1: the
    edited payload; call 2: the compliant script), which is what lets these tests
    observe the narrative's charged cost distinctly from the $0 subscription rows."""
    _no_sleep(monkeypatch)
    monkeypatch.setenv("NEWSLENS_LANE_WRITER", "api")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-qa")
    slots = [slot(1)]
    seed_briefing(con, A_DAY, slots, narrative="Published.")
    narrative = stories_payload(slots)

    class _R:
        def __init__(self, b):
            self._b = b

        def read(self):
            return self._b

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def fake_urlopen(req, timeout=None):
        if req.full_url == llm.ANTHROPIC_MESSAGES_URL:
            return _R(json.dumps({
                "id": "msg_h", "type": "message", "role": "assistant",
                "model": "claude-opus-4-8",
                "content": [{"type": "text", "text": json.dumps(narrative)}],
                "stop_reason": "end_turn",
                "usage": {"input_tokens": 900, "output_tokens": 200},
            }).encode())
        raise OSError("offline: only the narrative wire is scripted")

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    return slots, narrative


def test_default_lane_generate_run_ships_with_charged_zero_editor_script(
        migrated_con, monkeypatch, tmp_path):
    """THE B3 END-TO-END: a plain no-refresh generate on the DEFAULT seat map
    completes with editor+script on the claude -p lane — their step rows say
    lane=subscription, legacy usd == usd_charged == 0.00, usd_shadow > 0 —
    and the briefing of record persists. The run's charged total is the
    narrative's alone."""
    slots, narrative = _generate_harness(monkeypatch, migrated_con,
                                         editor_inp=1000)
    stub = make_scripted_stub(
        tmp_path / "shim",
        [{"result": json.dumps(narrative), "inp": 1000, "out": 200},
         {"result": compliant_script(slots), "inp": 1200, "out": 400}])
    monkeypatch.setenv("NEWSLENS_CLAUDE_BIN", str(stub))
    # B4: cap raised for the harness — the narrative pre-check prices the
    # 16k-token Opus ceiling at ~$0.40, so the old 0.20 would abort at the
    # gate before proving anything. 2.00 clears it; the $0-charged
    # editor/script tooth is cap-independent.
    rep = run(migrated_con, env=dict(ENV, BUDGET_CAP_USD_PER_RUN="2.00"))
    by_step = {s["step"]: s for s in rep.steps}
    narrative_row = by_step["narrative_A"]         # report names carry variant
    assert narrative_row["lane"] == "api"
    assert narrative_row["usd"] == narrative_row["usd_charged"] > 0
    for step in ("editor_pass", "script_adapt"):
        row = by_step[step]
        assert row["lane"] == "subscription", step
        assert row["model"] == "claude-haiku-4-5", step
        assert row["usd"] == row["usd_charged"] == 0.0, step
        assert row["usd_shadow"] > 0.0, step
    charged_total = sum(s.get("usd_charged") or 0 for s in rep.steps)
    assert charged_total == pytest.approx(narrative_row["usd_charged"])
    row = migrated_con.execute(
        "SELECT narrative_text, script_text FROM briefings WHERE date = ?",
        (A_DAY,)).fetchone()
    assert row["narrative_text"] and row["script_text"]
    calls = stub_calls(tmp_path / "shim")
    assert len(calls) == 2                          # editor, then script
    assert "--append-system-prompt" in calls[0]["argv"]   # editor is json_mode
    assert "--append-system-prompt" not in calls[1]["argv"]  # script is prose


def test_mid_run_cap_exhaustion_on_shadow_kills_the_run_before_the_script(
        migrated_con, monkeypatch, tmp_path):
    """Onna's law at generate scale: the SAME run, but the editor's reported
    usage prices its SHADOW at ~$1.00 (charged still $0.00). The script's
    pre-call budget guard must trip on cap - spent(shadow) and kill the run
    (the same GenerateError class as before B3), the failed run's logged
    money record must show charged == narrative pennies, and the record
    briefing must be untouched. If `spent` accumulated charged, the run
    would complete — the revert bite."""
    slots, narrative = _generate_harness(monkeypatch, migrated_con,
                                         editor_inp=1_000_000)
    stub = make_scripted_stub(
        tmp_path / "shim",
        [{"result": json.dumps(narrative), "inp": 1_000_000, "out": 200},
         {"result": compliant_script(slots), "inp": 1200, "out": 400}])
    monkeypatch.setenv("NEWSLENS_CLAUDE_BIN", str(stub))
    # B4 arithmetic (conscious re-pin): the cap must clear the narrative
    # pre-check (~$0.40 at the 16k Opus ceiling) and still be exhausted by
    # the editor's $1.001 SHADOW before the script's ~$0.015+ estimate:
    # 1.02 - 0.0095 (narrative shadow) - 1.001 (editor shadow) = 0.0095
    # remaining < script est -> the script guard trips on SHADOW, exactly
    # the pre-B4 tooth at B4 prices.
    with pytest.raises(generate.GenerateError) as exc:
        run(migrated_con, env=dict(ENV, BUDGET_CAP_USD_PER_RUN="1.02"))
    assert "budget" in str(exc.value)
    # only editor spawned; the script guard fired BEFORE spawn #2
    assert len(stub_calls(tmp_path / "shim")) == 1
    # the failed run's logged ledger: charged stayed pennies; shadow shows the truth
    log_lines = (paths.DATA_DIR / "generation_log.jsonl").read_text().splitlines()
    entry = json.loads(log_lines[-1])
    assert entry["status"] == "failed"
    editor_rows = [s for s in entry["steps"] if s.get("step") == "editor"]
    assert editor_rows and editor_rows[0]["usd"] == 0.0
    assert editor_rows[0]["usd_shadow"] == pytest.approx(1.001, abs=0.01)
    assert entry["total_usd"] < 0.01                      # real money: pennies
    # the record was never touched (death before persist)
    row = migrated_con.execute(
        "SELECT narrative_text FROM briefings WHERE date = ?", (A_DAY,)
    ).fetchone()
    assert row["narrative_text"] == "Published."


# ===========================================================================
# 8. FIX-1 — the GENERATE-entry preflights (the liveness only they can flip)
# ===========================================================================

def test_fix1_generate_entry_state_misconfig_kills_before_any_model_call(
        migrated_con, monkeypatch):
    """LIVENESS for generate.py's state preflight line (the born-red file
    proves check_lane and run_analysis's own arm — NOT these two lines):
    run_memory_pass sits behind post-persist broad excepts, so WITHOUT the
    entry preflight a state lane misconfig would surface only as a swallowed
    stale-moat warning after the whole edition generated. With it, the run
    dies RAW at stage entry: zero model calls, record untouched."""
    seed_briefing(migrated_con, A_DAY, [slot(1)], narrative="Published.")
    chats = []

    def chat_tripwire(*a, **k):
        chats.append(a)
        raise AssertionError("model call before the stage preflight fired")

    monkeypatch.setattr(generate, "_chat", chat_tripwire)
    monkeypatch.setenv("NEWSLENS_LANE_STATE", "junk")
    with pytest.raises(llm.LaneUnavailable) as exc:
        run(migrated_con)
    assert "state" in str(exc.value)
    assert chats == []
    row = migrated_con.execute(
        "SELECT narrative_text FROM briefings WHERE date = ?", (A_DAY,)
    ).fetchone()
    assert row["narrative_text"] == "Published."


def test_fix1_generate_entry_analyst_misconfig_kills_the_refresh_run_raw(
        migrated_con, monkeypatch):
    """LIVENESS for generate.py's analyst preflight line: generate wraps
    run_analysis in a STAGE-WIDE broad except ('disclosed degrade'), so
    run_analysis's own preflight CANNOT kill a generate run — without the
    entry line, an analyst misconfig ships a depth-absent edition with a
    warning. With it, the refresh run dies raw before the analysis stage:
    zero model calls, zero analysis rows, nothing persisted over the seed."""
    seed_briefing(migrated_con, A_DAY, [slot(1)], narrative="Published.")
    from newslens import ingest as ingest_mod
    monkeypatch.setattr(
        ingest_mod, "run_ingest",
        lambda con=None, env=None: SimpleNamespace(
            succeeded=[1], attempted=1, items_new=0,
            discovery_status="skipped", degradation_message=None))
    monkeypatch.setattr(
        ranking, "run_rank",
        lambda date=None, con=None, env=None, cfg=None: SimpleNamespace(
            warnings=[]))
    chats = []

    def chat_tripwire(*a, **k):
        chats.append(a)
        raise AssertionError("model call before the stage preflight fired")

    monkeypatch.setattr(generate, "_chat", chat_tripwire)
    monkeypatch.setenv("NEWSLENS_LANE_ANALYST", "junk")
    with pytest.raises(llm.LaneUnavailable) as exc:
        run(migrated_con, refresh=True)
    assert "analyst" in str(exc.value)
    assert chats == []
    assert migrated_con.execute(
        "SELECT COUNT(*) FROM analysis_briefs").fetchone()[0] == 0
    row = migrated_con.execute(
        "SELECT narrative_text FROM briefings WHERE date = ?", (A_DAY,)
    ).fetchone()
    assert row["narrative_text"] == "Published."


# ===========================================================================
# 9. R-B3a hardening — the failure path, and the 2-tuple compat end to end
# ===========================================================================

def test_rB3a_failed_but_paid_subscription_state_chat_still_ledgers_shadow(
        migrated_con):
    """BUG-32's subscription twin: a state chat that FAILS after billing
    (exception carrying usd_spent=0.0, usd_shadow>0) must produce a stale
    row whose shadow_usd survives, AND the aggregate state_rewrites step row
    must still exist — the shadow-gated guard (R-B3a) covers the failure
    shape too, or a failed-but-quota-consuming run vanishes from the record."""
    arc = _seed_two_threads(migrated_con)

    def failing_paid_chat(key, prompt):
        e = RuntimeError("stub: died after the paid attempt")
        e.usd_spent = 0.0
        e.usd_shadow = 0.0042
        raise e

    report = generate.GenReport(date="2026-07-16", variant=generate.ACTIVE_VOICE)
    generate.run_memory_pass(
        migrated_con, "2026-07-16", "sk-fake", cap=1.0, spent=0.0,
        briefs_by_slot={1: {"brief": arc}, 2: {"brief": arc}},
        slots=[slot(1, mem=["Iran War"]), slot(2, mem=["Chip Export Rules"])],
        report=report, state_chat=failing_paid_chat)
    rows = [s for s in report.steps if s["step"] == "state_rewrites"]
    assert len(rows) == 1, "failed-but-paid shadow vanished from the ledger"
    assert rows[0]["usd_charged"] == 0.0
    assert rows[0]["usd_shadow"] == pytest.approx(0.0084)   # both threads' misses
    assert report.memory_usd == 0.0
    assert report.memory_shadow_usd == pytest.approx(0.0084)


def test_rB3a_two_tuple_state_chat_rides_rewrite_state_end_to_end(
        migrated_con):
    """Back-compat, driven through rewrite_state itself (the born-red file
    pins only the dataclass default): a legacy 2-tuple chat yields
    shadow_usd == cost_usd on the result — the api-lane invariant every
    existing injected chat silently relies on."""
    now = "2026-07-01T00:00:00.000Z"
    cur = migrated_con.execute(
        "INSERT INTO memory (topic, status, status_changed_at, created_at,"
        " updated_at) VALUES ('Iran War', 'active', ?, ?, ?)", (now, now, now))
    tid = cur.lastrowid
    migrated_con.execute(
        "INSERT INTO thread_deltas (thread_id, edition_date, slot, verdict,"
        " what_happened, significance, cites_json)"
        " VALUES (?, '2026-07-16', 1, 'advances', 'Moved.', 'Matters.',"
        " '[\"S1\"]')", (tid,))
    migrated_con.commit()

    def legacy_chat(key, prompt):
        return ({"state": f"Moved ({memory_core.human_date('2026-07-16')})."},
                0.02)

    res = memory_core.rewrite_state(
        migrated_con, tid, "Iran War", "2026-07-16", None, "sk-x",
        "{topic} {date} {ledger}", remaining_usd=1.0, chat=legacy_chat)
    assert res.outcome == "written", res.detail
    assert res.cost_usd == pytest.approx(0.02)
    assert res.shadow_usd == pytest.approx(0.02)


# ===========================================================================
# 10. Doctor — the subscription section spends NOTHING and tells the truth
# ===========================================================================

def test_doctor_subscription_section_missing_binary_fails_naming_both_fixes(
        tmp_path):
    results = doctor.check_subscription_lane(
        {"NEWSLENS_CLAUDE_BIN": str(tmp_path / "absent")})
    assert results[0].status == doctor.FAIL
    text = results[0].text
    assert "install" in text.lower()
    assert "NEWSLENS_LANE_<SEAT>=api" in text
    for seat in ("rank", "editor", "script"):
        assert seat in text


def test_doctor_subscription_section_spawns_only_version_never_p(
        tmp_path, monkeypatch):
    """The no-spend law, mechanically: every subprocess the section runs is
    `<bin> --version` — never `-p` (a -p invocation would spend subscription
    quota). The probe flag prints its design as a WARN and STILL does not
    fire. Recorder delegates to the real run so the section's version parse
    is exercised against a live child (the scripted stub, never the real
    CLI)."""
    stub = make_scripted_stub(tmp_path / "shim", [{}],
                              version="2.1.212 (QA doctor stub)")
    spawned = []
    real_run = doctor.subprocess.run

    def recording_run(args, **kwargs):
        spawned.append(list(args))
        return real_run(args, **kwargs)

    monkeypatch.setattr(doctor.subprocess, "run", recording_run)
    env = {"NEWSLENS_CLAUDE_BIN": str(stub)}
    results = doctor.check_subscription_lane(env)
    statuses = [r.status for r in results]
    assert statuses[0] == doctor.INFO and "resolved via env" in results[0].text
    assert doctor.PASS in statuses                 # 2.1.212 >= the effort floor
    assert any("auth NOT probed" in r.text for r in results)
    # probe flag: designed, printed, NOT fired
    results2 = doctor.check_subscription_lane(
        dict(env, NEWSLENS_DOCTOR_SUBSCRIPTION_PROBE="1"))
    assert any(r.status == doctor.WARN and "NOT fired" in r.text
               for r in results2)
    for argv in spawned:
        assert argv[1:] == ["--version"], argv
        assert "-p" not in argv
    assert len(spawned) == 2                       # one per section render


def test_doctor_subscription_section_no_sub_seats_is_info_no_spawn(
        monkeypatch):
    spawned = []
    monkeypatch.setattr(doctor.subprocess, "run",
                        lambda *a, **k: spawned.append(a))
    results = doctor.check_subscription_lane({"NEWSLENS_LANE": "api"})
    assert [r.status for r in results] == [doctor.INFO]
    assert "nothing to check" in results[0].text
    assert spawned == []


def test_doctor_subscription_version_below_floor_warns_not_fails(
        tmp_path, monkeypatch):
    stub = make_scripted_stub(tmp_path / "shim", [{}],
                              version="2.0.9 (old CLI)")
    results = doctor.check_subscription_lane({"NEWSLENS_CLAUDE_BIN": str(stub)})
    warns = [r for r in results if r.status == doctor.WARN]
    assert any("effort-control floor" in w.text for w in warns)
    assert not [r for r in results if r.status == doctor.FAIL]


# ===========================================================================
# 11. RED — defect B3-D1: the PERSISTED rank ledger row lies on the $0 lane
# ===========================================================================

# ===========================================================================
# 12. Loop-2 pokes — the D2 armed fall, adversarially
# ===========================================================================

def _ant(content, inp=1000, out=200):
    """anthropic /v1/messages response DICT for scripted urlopen (the twin of
    conftest.anthropic_envelope, which returns bytes for the fake server)."""
    text = json.dumps(content) if isinstance(content, (dict, list)) else str(content)
    return {"id": "msg_b3d2", "type": "message", "role": "assistant",
            "model": "claude-haiku-4-5",
            "content": [{"type": "text", "text": text}],
            "stop_reason": "end_turn",
            "usage": {"input_tokens": inp, "output_tokens": out,
                      "cache_creation_input_tokens": 0,
                      "cache_read_input_tokens": 0}}


class _RResp:
    def __init__(self, payload):
        self._b = json.dumps(payload).encode()

    def read(self):
        return self._b

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_flap_window_cannot_fork_ranking_transport_from_its_ledger(
        tmp_path, monkeypatch):
    """RED (loop 2, 2026-07-17) — ACCEPTANCE CONTRACT for defect B3-D5, the
    D1 invariant's race window: ranking resolves llm.effective_seat TWICE per
    call — once at the gate (feeding the cost ledger) and again inside
    _post_chat (feeding the transport). Since D2 that resolution depends on
    the FILESYSTEM (the binary stat) and, when armed, on the fall — so a
    binary flap between the two resolutions forks the lane the bytes ride
    from the lane the ledger records. Deterministic reproduction (no race):
    armed fallback; binary present at the gate (ledger cfg = subscription);
    the attempt-1 stub SELF-DESTRUCTS and returns malformed content (the
    corrected retry fires); attempt 2 re-resolves — binary gone + armed —
    and SILENTLY FALLS to the api wire. The sink's attempt-2 row is priced
    and labeled off the GATE cfg: lane='subscription', usd_charged=0.0 — for
    bytes that rode the metered HTTP lane. Real spend recorded as $0: the
    exact lie D1/D2's labels exist to prevent. (generate is immune — call_llm
    threads its ONE resolution through _ACTIVE_SEAT_CFG; run_rank's
    post-call disclosure at ranking.py:1508 re-resolves a THIRD time, same
    class.)

    FIX CONTRACT: ONE effective_seat resolution per call_llm_validated call,
    threaded to the transport (the generate _ACTIVE_SEAT_CFG pattern — a
    module-level active-cfg seam or a default-arg cfg on _post_chat that
    preserves its monkeypatch signature) and reused by run_rank's disclosure
    — so gate, ledger, transport, and warning can never diverge, whatever
    the filesystem does between them. Acceptable post-fix outcomes here:
    the call dies loud on the vanished binary (threaded subscription cfg →
    provider LaneUnavailable → the caller's retry-then-RankingError), or —
    if a future fix re-gates per attempt — every row's lane matches the
    transport that actually carried that attempt. What can never happen is
    the fork this asserts against."""
    _no_sleep(monkeypatch)
    monkeypatch.setenv("NEWSLENS_LANE_FALLBACK", "api")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-fall")
    stub = make_scripted_stub(
        tmp_path / "shim",
        [{"result": "not-the-json-the-validator-wants", "inp": 500,
          "out": 50, "self_destruct": True}])
    monkeypatch.setenv("NEWSLENS_CLAUDE_BIN", str(stub))
    http = []

    def fake_urlopen(req, timeout=None):
        http.append(req.full_url)
        return _RResp(_ant(_RANK_CLUSTERS))

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    sink = []
    outcome_err = None
    try:
        ranking.call_llm_validated("k", "PROMPT", {1}, {}, [], cost_sink=sink)
    except (ranking.RankingError, llm.LaneUnavailable) as exc:
        outcome_err = exc
    assert not Path(str(stub)).exists()        # the flap really happened
    if outcome_err is None:
        # The call completed and attempt 2 rode HTTP (the fall): its ledger
        # row must SAY so — api-family label, real charged money.
        assert http, "completed without the api transport this scenario forces"
        assert len(stub_calls(tmp_path / "shim")) == 1   # attempt 1 only
        row2 = sink[-1]
        assert row2["lane"].startswith("api(fallback:"), (
            "B3-D5: attempt 2 rode the metered api wire but the ledger row "
            f"claims lane={row2['lane']!r} — real spend recorded as "
            f"usd_charged={row2['usd_charged']} (the D1 fork, flap window)")
        assert row2["usd_charged"] > 0.0
    # loud death is the acceptable (post-fix) alternative: no silent fork.


def test_flap_vanish_after_gate_unarmed_dies_loud_with_honest_zero_ledger(
        tmp_path, monkeypatch):
    """The coordinator's named direction, UNARMED (green pin): binary
    resolvable at the gate, gone at the next resolution — with no fallback
    armed the call must die LOUD (the caller's wrapped retry-then-error is
    acceptable; it names the binary problem), having recorded only the
    attempt that returned usage (the pre-flap subscription attempt), and
    NEVER touching HTTP."""
    _no_sleep(monkeypatch)
    stub = make_scripted_stub(
        tmp_path / "shim",
        [{"result": "malformed-so-a-retry-fires", "inp": 500, "out": 50,
          "self_destruct": True}])
    monkeypatch.setenv("NEWSLENS_CLAUDE_BIN", str(stub))
    http = _http_tripwire(monkeypatch)
    sink = []
    with pytest.raises(ranking.RankingError) as exc:
        ranking.call_llm_validated("k", "p", {1}, {}, [], cost_sink=sink)
    assert "LaneUnavailable" in str(exc.value) or "claude" in str(exc.value)
    assert http == []
    assert len(stub_calls(tmp_path / "shim")) == 1
    rows = [(e["lane"], e["usd_charged"]) for e in sink]
    assert rows == [("subscription", 0.0)]     # only the true, pre-flap row


def test_fall_provenance_survives_the_corrected_retry(tmp_path, monkeypatch):
    """A fallen call whose FIRST api attempt draws a malformed reply takes
    the corrected retry on the SAME fallen lane — both sink rows carry the
    full 'api(fallback:subscription_unavailable)' label and real charged
    money. Attempt 2 must never revert to a bare 'api' (provenance loss) or
    to 'subscription' (the flap fork). Green today (the resolution is
    loop-invariant); this pins it against any future per-attempt re-gating."""
    _no_sleep(monkeypatch)
    monkeypatch.setenv("NEWSLENS_LANE_FALLBACK", "api")
    monkeypatch.setenv("NEWSLENS_CLAUDE_BIN", str(tmp_path / "absent"))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-fall")
    replies = [_ant("```json fenced garbage"), _ant(_RANK_CLUSTERS)]
    http = []

    def fake_urlopen(req, timeout=None):
        http.append(req.full_url)
        return _RResp(replies[len(http) - 1])

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    sink = []
    clusters, _ = ranking.call_llm_validated("k", "BASE", {1}, {}, [],
                                             cost_sink=sink)
    assert [c["item_ids"] for c in clusters] == [[1]]
    assert len(http) == 2 and len(sink) == 2
    for e in sink:
        assert e["lane"] == "api(fallback:subscription_unavailable)", e
        assert e["usd"] == e["usd_charged"] == e["usd_shadow"] > 0.0, e


@pytest.mark.parametrize("armed_value", ["API", " api ", "Api"])
def test_armed_value_variants_trigger_the_fall(armed_value, tmp_path,
                                               monkeypatch):
    """fallback_armed's lenient parse ('API', ' api ') now carries BEHAVIOR —
    each documented-equivalent spelling must actually arm the fall."""
    monkeypatch.setenv("NEWSLENS_CLAUDE_BIN", str(tmp_path / "absent"))
    cfg, reason = llm.effective_seat(
        "rank", dict(os.environ, NEWSLENS_LANE_FALLBACK=armed_value))
    assert cfg.lane == "api" and reason == "subscription_unavailable"


@pytest.mark.parametrize("not_armed", ["1", "true", "yes", "subscription"])
def test_non_documented_fallback_values_do_not_arm_the_fall(
        not_armed, tmp_path, monkeypatch):
    monkeypatch.setenv("NEWSLENS_CLAUDE_BIN", str(tmp_path / "absent"))
    with pytest.raises(llm.LaneUnavailable):
        llm.effective_seat(
            "rank", dict(os.environ, NEWSLENS_LANE_FALLBACK=not_armed))


def test_single_fall_semantics_per_resolution_no_latch_no_chain_and_heals(
        tmp_path, monkeypatch):
    """What 'single fall' MEANS operationally, pinned: ONE lane hop
    (subscription -> api) within ONE resolution — no third lane, no chained
    fall (both-lanes-dead dies on the original error, pinned in the wrappers
    test and the implementer's D2 suite) — and the decision is re-made PER
    RESOLUTION with no process-wide latch: every gated call while the binary
    is missing falls (and is labeled/disclosed), and the FIRST call after
    the binary returns rides subscription again with reason None (a bare,
    unlabeled row). A CLI reinstall mid-day heals the next call, never
    retroactively relabels an old one."""
    monkeypatch.setenv("NEWSLENS_LANE_FALLBACK", "api")
    bin_path = tmp_path / "claude"
    monkeypatch.setenv("NEWSLENS_CLAUDE_BIN", str(bin_path))
    # absent: every resolution falls, independently (no once-only latch)
    for _ in range(2):
        cfg, reason = llm.effective_seat("editor")
        assert cfg.lane == "api" and reason == "subscription_unavailable"
    # the binary returns: the very next resolution heals to subscription
    bin_path.write_text("#!/bin/sh\nexit 0\n")
    bin_path.chmod(0o755)
    cfg, reason = llm.effective_seat("editor")
    assert cfg.lane == "subscription" and reason is None
    assert llm.cost_fields(cfg, {"prompt_tokens": 1, "completion_tokens": 1},
                           fallback_reason=reason)["lane"] == "subscription"


def test_run_rank_fall_is_disclosed_and_the_persisted_row_is_labeled(
        migrated_con, tmp_path, monkeypatch, fake_api):
    """The rank fall end to end on the principal's surfaces: armed + binary
    absent -> run_rank completes on the api fall-over; the run report WARNS
    in plain language (real API money, the fix, the label), and the durable
    briefings.token_cost row carries lane='api(fallback:subscription_
    unavailable)' with usd == usd_charged == usd_shadow > 0 — labeled real
    spend, never a bare api and never a phantom $0."""
    _no_sleep(monkeypatch)
    monkeypatch.setenv("NEWSLENS_LANE_FALLBACK", "api")
    monkeypatch.setenv("NEWSLENS_CLAUDE_BIN", str(tmp_path / "absent"))
    monkeypatch.setattr(llm, "ANTHROPIC_MESSAGES_URL",
                        fake_api.base_url + "/v1/messages")
    monkeypatch.setenv("ANTHROPIC_API_KEY", fake_api.good_key)
    from conftest import anthropic_envelope
    ranked = {"clusters": [{
        "story_title": "Tagged story", "summary": "Matched your tags.",
        "item_ids": [1],
        "matched_tags": [{"name": "AI regulation", "level": "topic"}],
        "matched_memory": [], "world_impact": 5,
        "world_impact_reason": "Sector-wide effect"}]}
    fake_api.add_route("/v1/messages", status=200,
                       body=anthropic_envelope(ranked),
                       content_type="application/json")
    now = "2026-07-17T00:00:00.000Z"
    migrated_con.execute(
        "INSERT INTO source_items (id, source_type, outlet, url, title,"
        " fetched_at) VALUES (1, 'rss', 'Outlet A', 'https://a.example/1',"
        " 'Story', ?)", (now,))
    migrated_con.commit()
    from newslens import config
    cfg = config.SourcesConfig(
        sources=[config.Source(name="Outlet A", rss_url="https://a.example/f")],
        interests_broad=["economy"], interests_granular=["AI regulation"])
    report = ranking.run_rank(date="2026-07-17", con=migrated_con, cfg=cfg,
                              env={"OPENAI_API_KEY": "sk-qa-fake"})
    assert report.slots
    fall_warnings = [w for w in report.warnings
                     if "rank ran the API fall-over lane" in w]
    assert len(fall_warnings) == 1
    assert "billed real API money" in fall_warnings[0]
    row = migrated_con.execute(
        "SELECT token_cost FROM briefings WHERE date = '2026-07-17'"
    ).fetchone()
    token_cost = json.loads(row["token_cost"])
    step = token_cost["steps"][0]
    assert step["lane"] == "api(fallback:subscription_unavailable)"
    assert step["usd"] == step["usd_charged"] == step["usd_shadow"] \
        == pytest.approx(0.002)
    assert token_cost["total_usd"] == pytest.approx(0.002)


def test_generate_fall_discloses_editor_and_script_and_labels_the_ledger(
        migrated_con, tmp_path, monkeypatch):
    """The generate-side fall end to end: armed + binary absent -> a full
    no-refresh run completes with editor/script on the labeled api fall-over.
    The run report carries BOTH plain-language fall warnings (the stage-entry
    disclosure), the step rows are labeled with real charged money (charged
    == shadow > 0 — no phantom $0), and the ok-run generation_log entry — the
    surface the principal actually reads next morning — carries the warnings
    AND the labeled rows."""
    _no_sleep(monkeypatch)
    slots = [slot(1)]
    seed_briefing(migrated_con, A_DAY, slots, narrative="Published.")
    narrative = stories_payload(slots)
    monkeypatch.setenv("NEWSLENS_LANE_FALLBACK", "api")
    monkeypatch.setenv("NEWSLENS_CLAUDE_BIN", str(tmp_path / "absent"))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-fall")
    # 2026-07-17: state is a subscription seat now, but its stage-entry preflight
    # is a raw check_lane (FIX-1, unweakened) that does NOT apply the armed fall,
    # so an absent binary would kill it at entry. This test is about editor/script
    # falling — pin state to api (no memory threads here, so no state transport).
    monkeypatch.setenv("NEWSLENS_LANE_STATE", "api")
    # item C (2026-07-17): the writer defaults to subscription too now — pin it to
    # api so the narrative rides the api wire DIRECTLY (not a fall), keeping this
    # test's disclosure surface exactly the two seats it is about: editor+script.
    monkeypatch.setenv("NEWSLENS_LANE_WRITER", "api")
    # the narrative rides the anthropic wire (writer = Opus/api), so the fake
    # routes by request-body MODEL — Opus ->
    # the narrative envelope; Haiku (the fallen editor/script) -> the scripted
    # replies in order. The fall tooth itself is unchanged.
    anthropic_replies = [_ant(narrative, inp=1000, out=200),          # editor
                         _ant(compliant_script(slots), inp=1200, out=400)]
    state = {"anthropic_served": 0}

    def fake_urlopen(req, timeout=None):
        if req.full_url == llm.ANTHROPIC_MESSAGES_URL:
            body = json.loads(req.data.decode())
            if body.get("model") == "claude-opus-4-8":
                return _RResp({
                    "id": "msg_fall", "type": "message", "role": "assistant",
                    "model": "claude-opus-4-8",
                    "content": [{"type": "text",
                                 "text": json.dumps(narrative)}],
                    "stop_reason": "end_turn",
                    "usage": {"input_tokens": 900, "output_tokens": 200}})
            i = min(state["anthropic_served"], len(anthropic_replies) - 1)
            state["anthropic_served"] += 1
            return _RResp(anthropic_replies[i])
        raise OSError("offline: unscripted target " + req.full_url)

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    rep = run(migrated_con, env=dict(ENV, BUDGET_CAP_USD_PER_RUN="2.00"))
    for seat in ("editor", "script"):
        matches = [w for w in rep.warnings
                   if f"{seat} ran the API fall-over lane" in w]
        assert len(matches) == 1, (seat, rep.warnings)
    by_step = {s["step"]: s for s in rep.steps}
    for step_name in ("editor_pass", "script_adapt"):
        row = by_step[step_name]
        assert row["lane"] == "api(fallback:subscription_unavailable)", row
        assert row["usd"] == row["usd_charged"] == row["usd_shadow"] > 0.0, row
    assert state["anthropic_served"] == 2
    entry = json.loads((paths.DATA_DIR / "generation_log.jsonl")
                       .read_text().splitlines()[-1])
    assert entry["status"] == "ok"
    log_fall_warnings = [w for w in entry["warnings"]
                         if "ran the API fall-over lane" in w]
    assert len(log_fall_warnings) == 2
    logged_lanes = {s.get("step"): s.get("lane") for s in entry["steps"]}
    assert logged_lanes["editor_pass"] == \
        logged_lanes["script_adapt"] == "api(fallback:subscription_unavailable)"


# ===========================================================================
# 14. Loop-4 pokes — D6's deferred kill at run level; D7's persist threading
# ===========================================================================

def test_D6_deferred_kill_unarmed_unavailable_editor_dies_at_its_own_stage(
        migrated_con, monkeypatch, tmp_path):
    """The D6 arm the snapshot must NOT break, at RUN level: an unavailable +
    UNARMED editor/script seat is left UNSCOPED by _run_generate_body (the
    `except LaneUnavailable: pass` publication arm), so the run proceeds and
    dies RAW at the stage that actually uses the seat — AFTER the narrative
    transported (deferred kill: a seat a path might never reach must not be a
    stage-entry death), BEFORE any editor/script transport, with the record
    untouched. If publication ever propagated the raise, this dies with ZERO
    narrative calls and the http assert names it; if the gate ever soft-
    skipped, the raises-check names that."""
    _no_sleep(monkeypatch)
    slots = [slot(1)]
    seed_briefing(migrated_con, A_DAY, slots, narrative="Published.")
    narrative = stories_payload(slots)
    http = []
    # B4 flip (conscious): the narrative wire is the anthropic endpoint now.
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-qa")

    def fake_urlopen(req, timeout=None):
        if req.full_url == llm.ANTHROPIC_MESSAGES_URL:
            http.append(req.full_url)
            return _RResp({
                "id": "msg_d6", "type": "message", "role": "assistant",
                "model": "claude-opus-4-8",
                "content": [{"type": "text", "text": json.dumps(narrative)}],
                "stop_reason": "end_turn",
                "usage": {"input_tokens": 900, "output_tokens": 200}})
        raise OSError("offline: unscripted target " + req.full_url)

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setenv("NEWSLENS_CLAUDE_BIN", str(tmp_path / "absent"))
    # 2026-07-17: state is a subscription seat now, so its raw stage-entry
    # preflight (FIX-1) would kill on the absent binary BEFORE the narrative —
    # not this test's subject. Pin state to the api lane (no binary needed) so
    # the deferred-kill tooth stays about the EDITOR at its own stage.
    monkeypatch.setenv("NEWSLENS_LANE_STATE", "api")
    # item C: the writer defaults to subscription too — pin it api so the
    # narrative TRANSPORTS (HTTP) first; the deferred kill must land at the
    # editor stage, not starve the narrative on the absent binary.
    monkeypatch.setenv("NEWSLENS_LANE_WRITER", "api")
    # fallback UNARMED: the editor's gate at its own stage must fail loud
    with pytest.raises(llm.LaneUnavailable) as exc:
        run(migrated_con)
    assert "editor" in str(exc.value)
    assert len(http) == 1, (
        "deferred kill broken: expected the narrative to transport first "
        f"(got {len(http)} HTTP calls before the editor-stage death)")
    row = migrated_con.execute(
        "SELECT narrative_text FROM briefings WHERE date = ?", (A_DAY,)
    ).fetchone()
    assert row["narrative_text"] == "Published."   # nothing persisted


def _d7_flap_run(migrated_con, tmp_path, monkeypatch, armed: bool):
    """D7 (loop 4): run_rank's PERSIST leg (ranking.py:1282) and DISCLOSURE
    leg (:1576) ride the run-scoped _effective_rank — wired in loop 3, pinned
    HERE end to end (a fresh-resolve mutation at either leg passed every
    prior test clean; the gate ran exactly that mutation). Scenario: the rank
    transport rides the subscription stub, which SELF-DESTRUCTS after
    serving — the binary is gone by persist time. The persisted
    briefings.token_cost row must record THE TRANSPORT'S lane
    (subscription, usd == usd_charged == 0.0) and the report must carry NO
    fall warning — regardless of arming. Under the persist-leg mutation the
    armed run persists 'api(fallback:…)' + charged money for bytes that rode
    the $0 stub (caught by the lane/usd asserts) and the unarmed run CRASHES
    at bookkeeping over a paid transport (caught by the completed-run
    asserts); under the disclosure-leg mutation the armed run false-warns
    (caught by the no-warning assert)."""
    _no_sleep(monkeypatch)
    if armed:
        monkeypatch.setenv("NEWSLENS_LANE_FALLBACK", "api")
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-should-not-be-needed")
    http = _http_tripwire(monkeypatch)
    ranked = {"clusters": [{
        "story_title": "Tagged story", "summary": "Matched your tags.",
        "item_ids": [1],
        "matched_tags": [{"name": "AI regulation", "level": "topic"}],
        "matched_memory": [], "world_impact": 5,
        "world_impact_reason": "Sector-wide effect"}]}
    stub = make_scripted_stub(
        tmp_path / "shim",
        [{"result": json.dumps(ranked), "inp": 1000, "out": 200,
          "self_destruct": True}])
    monkeypatch.setenv("NEWSLENS_CLAUDE_BIN", str(stub))
    now = "2026-07-17T00:00:00.000Z"
    migrated_con.execute(
        "INSERT INTO source_items (id, source_type, outlet, url, title,"
        " fetched_at) VALUES (1, 'rss', 'Outlet A', 'https://a.example/1',"
        " 'Story', ?)", (now,))
    migrated_con.commit()
    from newslens import config
    cfg = config.SourcesConfig(
        sources=[config.Source(name="Outlet A", rss_url="https://a.example/f")],
        interests_broad=["economy"], interests_granular=["AI regulation"])
    report = ranking.run_rank(date="2026-07-17", con=migrated_con, cfg=cfg,
                              env={"OPENAI_API_KEY": "sk-qa-fake"})
    # the flap really happened, and the transport really was the stub
    assert not Path(str(stub)).exists()
    assert len(stub_calls(tmp_path / "shim")) == 1
    assert http == []                          # no silent api transport either
    assert report.slots                        # a paid, completed rank
    # DISCLOSURE leg: the run did NOT fall — no fall warning may appear
    assert not [w for w in report.warnings
                if "API fall-over lane" in w], report.warnings
    # PERSIST leg: the durable row records the lane the bytes actually rode
    row = migrated_con.execute(
        "SELECT token_cost FROM briefings WHERE date = '2026-07-17'"
    ).fetchone()
    token_cost = json.loads(row["token_cost"])
    step = token_cost["steps"][0]
    assert step["lane"] == "subscription", (
        "D7 fork: persisted lane diverged from the transport lane: "
        + json.dumps(step))
    assert step["usd"] == step["usd_charged"] == 0.0, step
    assert step["usd_shadow"] == pytest.approx(0.002)
    assert token_cost["total_usd"] == 0.0


def test_D7_flap_under_persist_armed_records_the_transport_lane(
        migrated_con, tmp_path, monkeypatch):
    _d7_flap_run(migrated_con, tmp_path, monkeypatch, armed=True)


def test_D7_flap_under_persist_unarmed_still_bookkeeps_the_paid_run(
        migrated_con, tmp_path, monkeypatch):
    _d7_flap_run(migrated_con, tmp_path, monkeypatch, armed=False)


# ===========================================================================
# 15. Was-RED B3-D1 — the persisted rank ledger row (flipped green, loop 2)
# ===========================================================================

def test_run_rank_persisted_token_cost_is_charged_honest_on_subscription(
        migrated_con, tmp_path, monkeypatch):
    """Was RED (B3 QA pass, 2026-07-17) — ACCEPTANCE CONTRACT for defect
    B3-D1; CONSCIOUSLY FLIPPED GREEN by the loop-2 fix the same day
    (run_rank's persisted token_cost now routes through
    llm.cost_fields(effective_seat) with the full key set and
    usd/total_usd == usd_charged). The defect it gated —
    the PERSISTED half (sink half + fix contract: see
    test_rank_sink_entry_subscription_lane_legacy_usd_is_charged_zero in
    test_b1_llm_seam_qa.py): run_rank's briefings.token_cost summary block
    (ranking.py ~1216-1228) prices the rank step with lane-blind
    usage_to_usd and carries NO lane/shadow keys, so a DEFAULT-configuration
    rank run persists `usd`/`total_usd` ~$0.002 of real-money spend that the
    subscription lane never charged — the durable cost record lies, and
    nothing in the row lets a reader detect it (the D1 class, persisted).

    FIX CONTRACT: the persisted step row and total go through
    llm.cost_fields(resolve_seat('rank')) — usd == usd_charged (0.0 here,
    == usage_to_usd on the api lane so the existing api-pinned e2e test does
    not move), with {model, lane, cache_read_tokens, usd_shadow,
    usd_charged} present per the engineering-3 cost-ledger law. total_usd
    stays the CHARGED total (legacy semantics: real money)."""
    now = "2026-07-17T00:00:00.000Z"
    migrated_con.execute(
        "INSERT INTO source_items (id, source_type, outlet, url, title,"
        " fetched_at) VALUES (1, 'rss', 'Outlet A', 'https://a.example/1',"
        " 'Story', ?)", (now,))
    migrated_con.commit()
    from newslens import config
    cfg = config.SourcesConfig(
        sources=[config.Source(name="Outlet A", rss_url="https://a.example/f")],
        interests_broad=["economy"], interests_granular=["AI regulation"])
    ranked = {"clusters": [{
        "story_title": "Tagged story", "summary": "Matched your tags.",
        "item_ids": [1],
        "matched_tags": [{"name": "AI regulation", "level": "topic"}],
        "matched_memory": [], "world_impact": 5,
        "world_impact_reason": "Sector-wide effect"}]}
    stub = make_scripted_stub(
        tmp_path / "shim",
        [{"result": json.dumps(ranked), "inp": 1000, "out": 200}])
    monkeypatch.setenv("NEWSLENS_CLAUDE_BIN", str(stub))
    report = ranking.run_rank(date="2026-07-17", con=migrated_con, cfg=cfg,
                              env={"OPENAI_API_KEY": "sk-qa-fake"})
    assert report.slots                                  # the run really ranked
    row = migrated_con.execute(
        "SELECT token_cost FROM briefings WHERE date = '2026-07-17'"
    ).fetchone()
    token_cost = json.loads(row["token_cost"])
    step = token_cost["steps"][0]
    # the transport really was the subscription stub (one spawn, zero HTTP):
    assert len(stub_calls(tmp_path / "shim")) == 1
    # THE CONTRACT: real-money columns carry real money only
    assert step.get("lane") == "subscription", (
        "persisted rank row has no honest lane key: " + json.dumps(step))
    assert step["usd"] == 0.0, (
        "persisted rank row claims charged spend on the $0 lane: "
        + json.dumps(step))
    assert step.get("usd_charged") == 0.0
    assert step.get("usd_shadow") == pytest.approx(0.002)
    assert token_cost["total_usd"] == 0.0
