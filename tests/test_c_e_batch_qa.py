"""C+E batch (writer/analyst lane flip + battery lane arms) — QA adversarial
pass (QA-owned; the attacks the implementer's reworks and +4 do not carry).

Pinned here:
  * RE-LANE MONEY, both usage paths: writer/analyst default subscription with
    charged $0 and Opus/Sonnet-priced shadow on metered AND estimated usage;
    the armed fall works for the newly-subscription seats and the unarmed gate
    names the per-seat fix.
  * TRANSPORT: the writer's subscription spawn path rides the shim (stub-shim
    guard extended to the Opus seat), with the 900s sub timeout captured at the
    subprocess boundary; analyst 540; the api fall-over still passes the api
    knob (600) at urlopen.
  * BATTERY EXPERIMENTAL INTEGRITY: _run_arm restores BOTH env pins on a
    crashed arm (to prior values, not just popped); manifest lane +
    usd_charged_seam honest per lane at the _run_arm level; the cap gate
    counts CHARGED only (api arm skipped, subscription arm planned and run
    under a cap that would refuse any charged spend); confound-guard dodge
    matrix (whitespace, either-axis-alone legal, grid refused).
  * BORN-RED DEFECT PINS: duplicate arms/lanes slip the confound guard and
    collide artifact dirs; the batch-report's "subscription arms -> CLI check"
    has no landing in code (wiring claim without proof).

Offline by construction under the autouse sandbox; $0.
"""

from __future__ import annotations

import json
import os
import stat as stat_mod
import textwrap
import urllib.request
from pathlib import Path

import pytest

from newslens import battery, generate, llm

from test_b4_battery_qa import (_guard_sanction, _seed_sandbox_record,
                                _transport_tripwire)
from test_generate import A_DAY, slot


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------

def _make_shim(dir_path: Path, result_content: str, with_usage: bool = True,
               inp: int = 1000, out: int = 200) -> Path:
    """Recording `claude -p` shim: rec-<n>.json per call; envelope `result` is
    `result_content`; with_usage=False omits usage (the estimated path)."""
    dir_path.mkdir(parents=True, exist_ok=True)
    payload = {"type": "result", "subtype": "success", "is_error": False,
               "result": result_content, "session_id": "ce-qa",
               "total_cost_usd": 0.0}
    if with_usage:
        payload["usage"] = {"input_tokens": inp, "output_tokens": out,
                            "cache_read_input_tokens": 0}
    src = textwrap.dedent("""\
        #!/usr/bin/env python3
        import sys, os, json
        if '--version' in sys.argv[1:]:
            print('2.1.212 (C+E QA shim)'); sys.exit(0)
        DIR = {dir!r}
        data = sys.stdin.read()
        n = 1
        while os.path.exists(os.path.join(DIR, 'rec-%d.json' % n)):
            n += 1
        with open(os.path.join(DIR, 'rec-%d.json' % n), 'w') as f:
            json.dump({{'argv': sys.argv[1:], 'stdin': data}}, f)
        print({payload!r})
        """).format(dir=str(dir_path), payload=json.dumps(payload))
    shim = dir_path / "claude"
    shim.write_text(src)
    shim.chmod(shim.stat().st_mode | stat_mod.S_IXUSR)
    return shim


_DRAFT = json.dumps({"stories": [{"slot": 1, "headline": "H", "body": "B"}]})
_ARM_INPUTS = {"slots": [{"slot": 1}], "date": "2026-07-17"}


def _fake_call_llm(record, usage=None, fail=False):
    """A generate.call_llm stand-in for direct _run_arm tests: records the env
    pins AS SEEN AT CALL TIME, optionally raises, else returns (draft, usage)."""
    usage = usage or {"prompt_tokens": 10_000, "completion_tokens": 2_000,
                      "prompt_tokens_details": {"cached_tokens": 0}}

    def fake(key, prompt, step, max_tokens, temperature, json_mode,
             validate=None, cost_sink=None):
        record.append({
            "model_env": os.environ.get("NEWSLENS_MODEL_WRITER"),
            "lane_env": os.environ.get("NEWSLENS_LANE_WRITER"),
            "step": step,
        })
        if fail:
            raise RuntimeError("arm exploded mid-call")
        if validate:
            validate(_DRAFT)
        if cost_sink is not None:
            cost_sink.append({"step": step, "attempt": 1})
        return _DRAFT, usage
    return fake


# --------------------------------------------------------------------------
# re-lane money: both usage paths, fall armed/unarmed
# --------------------------------------------------------------------------

@pytest.mark.parametrize("seat,model,rate_in,rate_out", [
    ("writer", "claude-opus-4-8", 5.00, 25.00),
    ("analyst", "claude-sonnet-5", 3.00, 15.00),
])
def test_flipped_seats_metered_subscription_charged_zero_shadow_at_seat_rate(
        monkeypatch, tmp_path, seat, model, rate_in, rate_out):
    shim_dir = tmp_path / f"shim-{seat}"
    _make_shim(shim_dir, '{"ok": 1}', inp=100_000, out=20_000)
    monkeypatch.setenv("NEWSLENS_CLAUDE_BIN", str(shim_dir / "claude"))
    cfg = llm.resolve_seat(seat)
    assert (cfg.model, cfg.lane) == (model, "subscription")
    resp = llm.chat(llm.LaneRequest(cfg=cfg, prompt="p", temperature=0,
                                    max_tokens=100, json_mode=True,
                                    user_agent="ua", api_key="k"))
    fields = llm.cost_fields(cfg, resp.raw.get("usage"))
    expected = round(100_000 / 1e6 * rate_in + 20_000 / 1e6 * rate_out, 6)
    assert fields["usd_shadow"] == pytest.approx(expected)   # Opus 0.5+0.5 / Sonnet 0.3+0.3
    assert fields["usd_charged"] == 0.0
    assert (shim_dir / "rec-1.json").exists()      # the shim, never the real CLI


def test_flipped_seats_estimated_usage_still_zero_charged_and_labeled(
        monkeypatch, tmp_path):
    shim_dir = tmp_path / "shim-est"
    _make_shim(shim_dir, '{"ok": 1}', with_usage=False)
    monkeypatch.setenv("NEWSLENS_CLAUDE_BIN", str(shim_dir / "claude"))
    resp = llm.chat(llm.LaneRequest(cfg=llm.resolve_seat("writer"), prompt="p",
                                    temperature=0, max_tokens=100,
                                    json_mode=True, user_agent="ua",
                                    api_key="k"))
    fields = llm.cost_fields(llm.resolve_seat("writer"), resp.raw.get("usage"))
    assert fields.get("usd_shadow_estimated") is True
    assert fields["usd_charged"] == 0.0 and fields["usd_shadow"] > 0


def test_writer_armed_fall_and_unarmed_loud_death(monkeypatch):
    monkeypatch.setenv("NEWSLENS_CLAUDE_BIN", "/nonexistent/claude-ce-qa")
    with pytest.raises(llm.LaneUnavailable) as exc:
        llm.effective_seat("writer")                        # unarmed: loud
    assert "NEWSLENS_LANE_WRITER" in str(exc.value)
    monkeypatch.setenv("NEWSLENS_LANE_FALLBACK", "api")
    cfg, reason = llm.effective_seat("writer")              # armed: ONE labeled fall
    assert cfg.lane == "api" and reason == "subscription_unavailable"
    assert cfg.model == "claude-opus-4-8"                   # model/knobs survive the fall
    assert cfg.effort == "xhigh" and cfg.sampling is False


# --------------------------------------------------------------------------
# transport + timeouts at the mechanical boundary
# --------------------------------------------------------------------------

@pytest.mark.parametrize("seat,expect", [("writer", 900), ("analyst", 540)])
def test_subscription_provider_uses_the_new_sub_knobs(monkeypatch, tmp_path,
                                                      seat, expect):
    shim_dir = tmp_path / f"shim-{seat}"
    _make_shim(shim_dir, '{"ok": 1}')
    monkeypatch.setenv("NEWSLENS_CLAUDE_BIN", str(shim_dir / "claude"))
    captured = {}
    real_run = llm.subprocess.run

    def rec(args, **kw):
        captured["timeout"] = kw.get("timeout")
        return real_run(args, **kw)

    monkeypatch.setattr(llm.subprocess, "run", rec)
    llm.chat(llm.LaneRequest(cfg=llm.resolve_seat(seat), prompt="p",
                             temperature=0, max_tokens=100, json_mode=True,
                             user_agent="ua", api_key="k"))
    assert captured["timeout"] == expect
    assert (shim_dir / "rec-1.json").exists()


def test_writer_api_fall_over_still_uses_the_api_knob(monkeypatch, fake_api):
    captured = {}
    real_urlopen = urllib.request.urlopen

    def rec(req, timeout=None):
        captured["timeout"] = timeout
        return real_urlopen(req, timeout=timeout)

    monkeypatch.setattr(urllib.request, "urlopen", rec)
    monkeypatch.setattr(llm, "ANTHROPIC_MESSAGES_URL",
                        fake_api.base_url + "/v1/messages")
    monkeypatch.setenv("ANTHROPIC_API_KEY", fake_api.good_key)
    llm.chat(llm.LaneRequest(
        cfg=llm.resolve_seat("writer", {"NEWSLENS_LANE_WRITER": "api"}),
        prompt="p", temperature=0, max_tokens=100, json_mode=True,
        user_agent="ua", api_key="k"))
    assert captured["timeout"] == 600                       # api knob, not 900


# --------------------------------------------------------------------------
# battery: _run_arm pin/restore + manifest honesty (direct, seam-faked)
# --------------------------------------------------------------------------

def test_run_arm_pins_both_vars_and_restores_prior_values_on_success(
        monkeypatch, tmp_path):
    monkeypatch.setenv("NEWSLENS_MODEL_WRITER", "prior-model")
    monkeypatch.setenv("NEWSLENS_LANE_WRITER", "prior-lane")
    seen = []
    monkeypatch.setattr(generate, "call_llm", _fake_call_llm(seen))
    monkeypatch.setattr(generate, "assemble_narrative",
                        lambda *a, **k: "PROSE")
    m = battery._run_arm("k", "PROMPT", _ARM_INPUTS, "claude-fable-5",
                         "subscription", tmp_path / "arm")
    assert seen == [{"model_env": "claude-fable-5",
                     "lane_env": "subscription", "step": "narrative"}]
    assert os.environ["NEWSLENS_MODEL_WRITER"] == "prior-model"   # restored, not popped
    assert os.environ["NEWSLENS_LANE_WRITER"] == "prior-lane"
    assert m["arm"] == "claude-fable-5" and m["lane"] == "subscription"
    assert m["usd_charged_seam"] == 0.0                    # subscription arm: $0 seam


def test_run_arm_restores_both_env_pins_when_the_arm_crashes(monkeypatch, tmp_path):
    """The leak the coordinator named: a crashed arm must not bleed
    NEWSLENS_LANE_WRITER (or MODEL) into the next arm. Both restore paths:
    prior-set -> restored, prior-unset -> popped."""
    monkeypatch.setenv("NEWSLENS_MODEL_WRITER", "prior-model")
    monkeypatch.delenv("NEWSLENS_LANE_WRITER", raising=False)
    monkeypatch.setattr(generate, "call_llm", _fake_call_llm([], fail=True))
    with pytest.raises(RuntimeError, match="arm exploded"):
        battery._run_arm("k", "PROMPT", _ARM_INPUTS, "claude-fable-5", "api",
                         tmp_path / "arm")
    assert os.environ["NEWSLENS_MODEL_WRITER"] == "prior-model"
    assert "NEWSLENS_LANE_WRITER" not in os.environ        # popped, no leak


def test_run_arm_manifest_api_lane_charged_equals_opus_shadow(monkeypatch, tmp_path):
    """usd_charged_seam per lane, api direction: the seam charge is the
    SEAT-table (Opus) shadow — kept distinct from usd_real_at_arm_price (the
    arm's own rate), the honest-cost split."""
    usage = {"prompt_tokens": 100_000, "completion_tokens": 20_000,
             "prompt_tokens_details": {"cached_tokens": 0}}
    monkeypatch.setattr(generate, "call_llm", _fake_call_llm([], usage=usage))
    monkeypatch.setattr(generate, "assemble_narrative", lambda *a, **k: "PROSE")
    m = battery._run_arm("k", "PROMPT", _ARM_INPUTS, "claude-fable-5", "api",
                         tmp_path / "arm")
    assert m["lane"] == "api"
    assert m["usd_charged_seam"] == pytest.approx(
        100_000 / 1e6 * 5.00 + 20_000 / 1e6 * 25.00)       # Opus seat table
    on_disk = json.loads((tmp_path / "arm" / "manifest.json").read_text())
    assert on_disk["lane"] == "api"
    assert on_disk["usd_charged_seam"] == m["usd_charged_seam"]


# --------------------------------------------------------------------------
# battery main: cap arithmetic, confound-dodge matrix, born-red defect pins
# --------------------------------------------------------------------------

def test_cap_counts_charged_only_api_skipped_subscription_planned(
        monkeypatch, capsys):
    """A cap below one arm's estimate: the api arm SKIPs (charged), the
    subscription arm stays planned at $0 CHARGED — and a --run under that same
    cap executes the subscription arm (faked seam), whose manifest seam-charge
    is 0.0. A subscription arm cannot smuggle charged spend past the cap
    because its charge is derived from the lane, not asserted by the plan."""
    _guard_sanction(monkeypatch)
    _seed_sandbox_record(slots=[slot(1)])     # one slot — matches _DRAFT's one story
    monkeypatch.setenv("BUDGET_CAP_USD_PER_RUN", "0.0001")
    rc = battery.main(["--date", A_DAY, "--arms", "claude-opus-4-8",
                       "--lanes", "api,subscription"])
    out = capsys.readouterr().out
    assert rc == 0
    api_line = next(l for l in out.splitlines() if "[api]:" in l)
    sub_line = next(l for l in out.splitlines() if "[subscription]:" in l)
    assert "SKIP" in api_line and "SKIP" not in sub_line
    assert "$0 CHARGED (subscription)" in sub_line
    assert "planned 1 arm(s), skipped 1" in out
    assert "est total charged $0.0000" in out
    # and the run direction: the $0 arm executes under the tiny cap
    monkeypatch.setattr(generate, "call_llm", _fake_call_llm([]))
    monkeypatch.setattr(generate, "assemble_narrative", lambda *a, **k: "PROSE")
    rc2 = battery.main(["--date", A_DAY, "--arms", "claude-opus-4-8",
                        "--lanes", "subscription", "--run"])
    cap2 = capsys.readouterr()
    assert rc2 == 0, cap2.err
    from newslens import paths
    man = json.loads((Path(paths.DATA_DIR) / "battery" / A_DAY
                      / "claude-opus-4-8__subscription" / "manifest.json"
                      ).read_text())
    assert man["usd_charged_seam"] == 0.0


def test_confound_guard_dodge_matrix(monkeypatch, capsys):
    """Whitespace-padded lane lists still parse; either axis alone is LEGAL;
    the grid is refused exit 2 regardless of flag order or spacing."""
    _guard_sanction(monkeypatch)
    _seed_sandbox_record()
    _transport_tripwire(monkeypatch)
    # single model x two lanes (whitespace-hostile): LEGAL lane arm
    assert battery.main(["--date", A_DAY, "--arms", "claude-opus-4-8",
                         "--lanes", " api , subscription "]) == 0
    capsys.readouterr()
    # two models x one lane (whitespace-hostile): LEGAL model A/B
    assert battery.main(["--date", A_DAY,
                         "--arms", "claude-opus-4-8,claude-fable-5",
                         "--lanes", " api "]) == 0
    capsys.readouterr()
    # the grid, flags in either order, padded: REFUSED exit 2
    assert battery.main(["--date", A_DAY, "--lanes", "subscription, api",
                         "--arms", "claude-opus-4-8, claude-fable-5"]) == 2
    assert "confounds" in capsys.readouterr().err
    # trailing-comma dupe of the SAME axis value does not sneak the grid in
    assert battery.main(["--date", A_DAY,
                         "--arms", "claude-opus-4-8,claude-fable-5,",
                         "--lanes", "api,subscription,"]) == 2


def test_BORN_RED_duplicate_arms_collide_artifact_dirs(monkeypatch, capsys):
    """BORN RED (QA defect pin, C+E pass). The confound guard counts LIST
    LENGTHS, so duplicates slip it: `--arms X,X --lanes api` is legal today and
    plans TWO arms with the IDENTICAL artifact dir <date>/X__api/ — the second
    arm OVERWRITES the first's manifest/narrative (same for `--lanes api,api`
    on a lane arm). That violates the item-E invariant 'artifact dirs never
    collide across arms'.

    FIX CONTRACT (flips this green): dedupe the planned (model, lane) pairs
    order-preserving (disclosing the drop), OR refuse duplicate pairs exit 2 —
    either way a duplicate input must not plan two arms onto one directory."""
    _guard_sanction(monkeypatch)
    _seed_sandbox_record()
    _transport_tripwire(monkeypatch)
    rc = battery.main(["--date", A_DAY,
                       "--arms", "claude-opus-4-8,claude-opus-4-8",
                       "--lanes", "api"])
    out = capsys.readouterr().out
    assert rc == 2 or "planned 1 arm(s)" in out, (
        "duplicate arms planned twice onto one artifact dir "
        "(<date>/claude-opus-4-8__api) — collision")


def test_BORN_RED_all_subscription_run_preflights_the_cli(monkeypatch, capsys):
    """BORN RED (QA defect pin, C+E pass — a wiring claim without its landing).
    The batch report claims conditional key checks: 'api arms ->
    ANTHROPIC_API_KEY, subscription arms -> CLI'. The api half is real (exit 1
    before any arm). The subscription half has NO code: with every planned arm
    on the subscription lane and the CLI unresolvable, --run proceeds into the
    arm loop and burns every arm as a disclosed per-arm FAILURE instead of
    refusing upfront (the falsifier's --run gates its lane ONCE before spending
    — the established fail-fast pattern this runner claims to mirror).

    FIX CONTRACT (flips this green): before the arm loop, when any planned lane
    is 'subscription', run the check_lane-class binary gate once and refuse
    exit 1 naming the CLI fix with ZERO arm attempts — or the gate rules the
    per-arm disclosure acceptable and the BATCH REPORT's claim is corrected,
    and this pin is re-shaped to the ruled behavior."""
    _guard_sanction(monkeypatch)
    _seed_sandbox_record()
    monkeypatch.setenv("NEWSLENS_CLAUDE_BIN", "/nonexistent/claude-ce-qa")
    calls = []
    monkeypatch.setattr(generate, "call_llm", _fake_call_llm(calls, fail=True))
    rc = battery.main(["--date", A_DAY, "--arms", "claude-opus-4-8",
                       "--lanes", "subscription", "--run"])
    err = capsys.readouterr().err
    assert rc == 1
    assert calls == [], (
        "no upfront CLI preflight: the arm loop was entered with an "
        "unresolvable binary (per-arm failure instead of a gate refusal)")
    assert "claude" in err.lower()
