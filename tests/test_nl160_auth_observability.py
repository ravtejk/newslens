"""NL-160 — the rank-lane observability batch, born of the 2026-08-24 outage.

Principal ruling 2026-08-24 (DECISIONS, "second sitting" item 2). Three defects,
one root cause: the product could not SAY that its CLI was logged out.

  1. llm.py interpolated only STDERR into the transport failure. `claude -p
     --output-format json` reports its own failures on STDOUT and leaves stderr
     empty, so the production-likeliest failure printed a blank. The record
     carries it five times over (data/generation_log.jsonl, 2026-08-24):
     "claude -p (rank) exited 1:  — no briefing row was written".
  2. The doctor's live auth probe was DESIGNED and printed, never fired — so the
     doctor ran green through the whole outage (binary and version both pass on
     a logged-out machine). Pinned in tests/test_b3_subscription_lane_qa.py.
  3. The one-retry law fired on a failure that can never succeed: a second
     attempt against the same expired session, one backoff later. Measured on
     the record at ~35-41s per doomed generate.

Plus the NL-105 gate's R-4 routing: a LIVE arc-drought detector, because the
NL-105 streak pins replay a frozen corpus and cannot move for a drought that
starts tomorrow.

BORN-RED PROVENANCE, TRANSCRIBED (9107699, read-only `git archive` export with
src/ chmod a-w, PYTHONPATH pinned to the export's src and newslens.__file__
asserted inside it): **28 failed, 1 passed**.

The one that passes is the CONTROL —
`test_ranking_STILL_spends_two_attempts_on_an_ordinary_transport_failure` — and
it is the only genuine carried-invariant here: an ordinary transport RuntimeError
was retried before this batch and must still be retried after it. It is what
distinguishes "fail fast on auth" from "the retry law is broken".

Everything else is born red, though for two different reasons, and the
distinction is stated rather than blurred: most fail because the behaviour is
new (blank excerpt, doomed retry, no detector at all), while a handful fail
merely because the SYMBOL they name does not exist at 9107699 —
`subscription_failure_detail`, `is_auth_failure_text`, `SubscriptionAuthError`,
`ARC_AUTHORED_MARK`. Those pins guard behaviour that is partly carried (stderr
is still quoted when stdout has no envelope; the auth class is still a
RuntimeError), so their red is an AttributeError rather than an assertion — a
weaker red, and named as such so the proof class is not inflated.

FIXLOOP-1 ADDITIONS (2026-08-26, gate FIX-1..FIX-7 — sections 5 to 10). The
gate's MUT-2 found what the 29 above could not see: revert the two arms in
`llm._subscription_provider` that MINT SubscriptionAuthError to their HEAD shape,
leave the class and the predicate standing, and every shipped pin stays green —
the whole batch dead under a green suite. Sections 5-10 add the transport-level
pins those arms answer to, the per-site transport CONTROLS (F-3), the
source-restricted classifier (R-5), the money-ledger fold (R-6), the probe-flag
scrub (F-8) and the rotation-aware detector (F-9). Their red is proof-class in
three different currencies, and each pin's docstring names its own: born-red on
pre-fix bytes, red under MUT-2 (both minting arms reverted), red under a
per-site MUT-3 widen — or CARRIED-INVARIANT (born green), said plainly, never
counted as a red.

Deterministic and $0. NOT subprocess-free since fixloop-1: sections 5 and 7
spawn a SCRIPTED LOCAL `claude` stub (the only way to execute the minting arms
at all), which is the same shape the conftest's own canned stub has had since
B3 — a local python script, no network, no real CLI, nothing billable. Offline
proven by re-run with outbound connects blocked. No DB, no real data path, no
model call.
"""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path

import pytest

from newslens import doctor, llm, memory_core, ranking

# The envelope MEASURED live on 2026-08-24: is_error on stdout, stderr 0 bytes.
OUTAGE_ENVELOPE = {
    "type": "result",
    "is_error": True,
    "result": "Failed to authenticate: OAuth session expired. "
              "Please run `claude login`.",
    "duration_api_ms": 0,
    "session_id": "0af1e2c4",
}


# ===========================================================================
# 1. The blank excerpt — the defect the record shows five times
# ===========================================================================

def test_the_2026_08_24_envelope_no_longer_prints_a_blank():
    """BORN RED at 9107699. The exact bytes the CLI returned, and the exact
    thing the principal saw: nothing at all between the colon and the em-dash."""
    detail = llm.subscription_failure_detail(json.dumps(OUTAGE_ENVELOPE), "")
    assert detail.strip(), "the failure detail is STILL blank"
    assert "Failed to authenticate" in detail
    assert "OAuth session expired" in detail


def test_stderr_still_wins_when_stdout_carries_no_envelope():
    """The BEHAVIOUR is carried; the PIN is born red on the symbol (there is no
    `subscription_failure_detail` at 9107699). The old code was not wrong, only
    incomplete — a CLI that does write to stderr must still be quoted from it,
    and this is the pin that would catch an envelope-first rewrite that dropped
    the stderr leg entirely."""
    assert llm.subscription_failure_detail(
        "not json at all", "boom: the binary segfaulted") == (
            "boom: the binary segfaulted")


def test_raw_stdout_is_the_third_fallback():
    """Non-JSON stdout with empty stderr: quote what there is."""
    assert llm.subscription_failure_detail("segfault (core dumped)", "") == (
        "segfault (core dumped)")


def test_silence_is_STATED_never_rendered_as_a_blank():
    """The defect class in one line. A process that said nothing must produce a
    message that says so — "no output" is information; "" is the bug."""
    detail = llm.subscription_failure_detail("", "")
    assert detail == "no output on stdout or stderr"


def test_the_detail_is_length_capped():
    """The 200-char cap the old stderr slice had — a runaway CLI cannot flood
    the log line."""
    assert len(llm.subscription_failure_detail("x" * 5000, "")) == 200


def test_a_nested_error_message_is_reached():
    """{"error": {"message": ...}} — the API-shaped nesting the CLI can proxy."""
    detail = llm.subscription_failure_detail(
        json.dumps({"error": {"message": "overloaded_error"}}), "")
    assert detail == "overloaded_error"


# ===========================================================================
# 2. The auth classifier — one implementation, two readers
# ===========================================================================

@pytest.mark.parametrize("text", [
    "Failed to authenticate: OAuth session expired.",
    "OAuth token expired — please run claude login",
    "You are not logged in",
    "Invalid API key",
    "HTTP 401 Unauthorized",
])
def test_auth_class_texts_are_recognised(text):
    assert llm.is_auth_failure_text(text)


@pytest.mark.parametrize("text", [
    "no output on stdout or stderr",
    "disk quota exceeded",
    "overloaded_error",
    "rate limited (HTTP 429)",
    "segfault (core dumped)",
    "",
])
def test_non_auth_failures_are_NOT_recognised(text):
    """THE PIN THAT PROTECTS THE RETRY LAW. Every text here is a failure a retry
    might genuinely fix; misclassifying one as auth would silently disarm the
    one-retry law for that whole class. A loose marker is the expensive
    direction of this rule's drift, so the negative cases are pinned as hard as
    the positive ones."""
    assert not llm.is_auth_failure_text(text)


def test_the_doctor_and_the_transport_share_ONE_classifier(monkeypatch):
    """MUTATION PIN. The doctor's live-probe verdict and the transport's raise
    must be the same rule, not two copies of it — a doctor that says "logged in"
    about a machine the pipeline cannot authenticate is the 2026-08-24 failure
    wearing a different hat. Swap the marker table for one that recognises
    nothing, and the DOCTOR's verdict must follow the transport's."""
    monkeypatch.setattr(llm, "_AUTH_FAILURE_MARKERS", ("zzz-nothing-matches",))
    monkeypatch.setattr(doctor, "_run_auth_probe", lambda *a, **k: _proc(
        1, json.dumps(OUTAGE_ENVELOPE)))
    monkeypatch.setattr(llm, "resolve_claude_bin",
                        lambda env=None: ("/fake/claude", "env"))
    monkeypatch.setattr(doctor.subprocess, "run",
                        lambda *a, **k: _proc(0, "2.1.212 (stub)"))
    results = doctor.check_subscription_lane(
        {"NEWSLENS_CLAUDE_BIN": "/fake/claude",
         "NEWSLENS_DOCTOR_SUBSCRIPTION_PROBE": "1"})
    line = next(r for r in results if "auth probe" in r.text)
    # With the classifier blinded the doctor must NOT claim an auth verdict —
    # it degrades to "inconclusive". If this still said "NOT authenticated" the
    # doctor would be reading a second, hand-typed copy of the rule.
    assert line.status == doctor.WARN and "inconclusive" in line.text


def _proc(returncode, stdout, stderr=""):
    from types import SimpleNamespace
    return SimpleNamespace(returncode=returncode, stdout=stdout, stderr=stderr)


# ===========================================================================
# 3. No retry on a failure that cannot succeed
# ===========================================================================

def _auth_raiser(calls):
    def _boom(*a, **k):
        calls.append(1)
        raise llm.SubscriptionAuthError(
            "claude -p (rank) exited 1: Failed to authenticate: OAuth session "
            f"expired. — {llm.AUTH_FIX_HINT}")
    return _boom


def _transport_raiser(calls):
    def _boom(*a, **k):
        calls.append(1)
        raise RuntimeError("claude -p (rank) exited 1: overloaded_error")
    return _boom


def test_ranking_spends_ONE_attempt_on_auth(monkeypatch):
    """BORN RED at 9107699 (measured there: 2 attempts). This is the site that
    actually bit — every one of the five 2026-08-24 rows is the `rank` seat."""
    calls = []
    monkeypatch.setattr(ranking, "_post_chat", _auth_raiser(calls))
    monkeypatch.setattr(ranking.time, "sleep", lambda *a, **k: None)
    with pytest.raises(ranking.RankingError) as caught:
        ranking._call_llm_validated("k", "prompt", set(), {}, [])
    assert len(calls) == 1, f"the doomed retry still fires ({len(calls)} calls)"
    assert "cannot authenticate" in str(caught.value)
    # The honest message: it must NOT claim a retry it did not make.
    assert "after one retry" not in str(caught.value)
    # And it must carry the fix the principal can act on.
    assert "run `claude` once interactively" in str(caught.value)


def test_ranking_STILL_spends_two_attempts_on_an_ordinary_transport_failure(
        monkeypatch):
    """THE CONTROL, and the pin that makes the one above mean something. The
    carve-out must be surgical: a plain transport RuntimeError is exactly as
    retryable as it was before NL-160. Without this, "fail fast on auth" and
    "the retry law is broken" are indistinguishable."""
    calls = []
    monkeypatch.setattr(ranking, "_post_chat", _transport_raiser(calls))
    monkeypatch.setattr(ranking.time, "sleep", lambda *a, **k: None)
    with pytest.raises(ranking.RankingError) as caught:
        ranking._call_llm_validated("k", "prompt", set(), {}, [])
    assert len(calls) == 2, "the one-retry law was disarmed for the whole class"
    assert "after one retry" in str(caught.value)


def test_the_auth_error_is_still_a_RuntimeError(monkeypatch):
    """Born red on the SYMBOL at 9107699 (no such class), guarding a CARRIED
    invariant — so its red is an AttributeError, not an assertion, and the
    proof class is named rather than inflated.

    Load-bearing all the same. Every existing caller catches this lane's
    failures as `except Exception` / RuntimeError. The subclass must add a
    question those callers can ask, never a new escape route past them — a bare
    Exception subclass here would turn a handled failure into a crash at every
    site that was NOT updated, and there are five retry sites plus the
    scheduler's ladder downstream of this type."""
    assert issubclass(llm.SubscriptionAuthError, RuntimeError)


def test_memory_core_breaks_rather_than_raising_so_the_spend_still_rides(
        monkeypatch):
    """BORN RED at 9107699 (2 attempts). And the shape is the point: the state
    seat's loop MUST leave by `break`, not `raise`, because the BUG-32 block
    after the loop stamps accrued spend onto the exception. Escaping by `raise`
    would skip that and silently re-open the money-honesty hole — a fix for one
    defect quietly cutting a second one."""
    calls = []

    def chat(req):
        calls.append(1)
        raise llm.SubscriptionAuthError("Failed to authenticate: expired")

    import time as _time

    monkeypatch.setattr(llm, "chat", chat)
    monkeypatch.setattr(llm, "check_lane", lambda *a, **k: None)
    # memory_core does a function-local `import time`, so patch the module.
    monkeypatch.setattr(_time, "sleep", lambda *a, **k: None)
    with pytest.raises(llm.SubscriptionAuthError) as caught:
        memory_core._default_state_chat("k", "prompt")
    assert len(calls) == 1, "the doomed retry still fires at the state seat"
    # The post-loop money stamping was NOT skipped.
    assert hasattr(caught.value, "usd_spent")
    assert hasattr(caught.value, "usd_shadow")


def test_every_subscription_lane_retry_site_declines_the_doomed_attempt(
        monkeypatch):
    """THE ROUTE PIN, for the three sites the ranking/state pins above do not
    cover — and it is a route pin, not a repetition, because each of these
    guards sits in a bare `except Exception` at the END of a chain. If an
    earlier clause intercepted first (HTTPError, the ValueError family,
    TimeoutError), the guard would sit on a dead path and pass while doing
    nothing. Inspection says SubscriptionAuthError is a RuntimeError and reaches
    none of those clauses; this makes the claim executable.

    SCOPE, and why it is five sites and not the one the dispatch named. Seven of
    the eight seats resolve to the subscription lane (rank, analyst, writer,
    editor, script, follow_altitude, state — only synthesis is api), so every
    one of these loops can carry an auth failure. Fixing only the site that
    happened to bite on 2026-08-24 would leave four identical holes."""
    from newslens import analysis, follow_altitude, generate

    def raiser(*a, **k):
        raise llm.SubscriptionAuthError(
            f"claude -p exited 1: Failed to authenticate — {llm.AUTH_FIX_HINT}")

    import time as _time
    monkeypatch.setattr(_time, "sleep", lambda *a, **k: None)

    # generate.call_llm — the editor / script / writer steps.
    calls = []
    monkeypatch.setattr(generate, "_chat",
                        lambda *a, **k: calls.append(1) or raiser())
    monkeypatch.setattr(generate.llm, "check_lane", lambda *a, **k: None)
    with pytest.raises(generate.GenerateError) as caught:
        generate.call_llm("k", "p", "editor", 100, 0.0, False)
    assert len(calls) == 1, "generate.call_llm still spends the doomed retry"
    assert "cannot authenticate" in str(caught.value)
    assert "after one retry" not in str(caught.value)

    # analysis.call_analysis_model — the analyst seat.
    calls2 = []
    monkeypatch.setattr(analysis, "_analysis_chat",
                        lambda *a, **k: calls2.append(1) or raiser())
    monkeypatch.setattr(analysis.llm, "check_lane", lambda *a, **k: None)
    with pytest.raises(llm.SubscriptionAuthError):
        analysis.call_analysis_model("k", "p")
    assert len(calls2) == 1, "analysis still spends the doomed retry"

    # follow_altitude.resolve_altitude — the resolver seat.
    calls3 = []
    monkeypatch.setattr(follow_altitude.llm, "chat",
                        lambda *a, **k: calls3.append(1) or raiser())
    monkeypatch.setattr(follow_altitude.llm, "check_lane", lambda *a, **k: None)
    thread = follow_altitude.ThreadInput(thread_id=1, topic="Iran War")
    with pytest.raises(follow_altitude.AltitudeError) as caught3:
        follow_altitude.resolve_altitude(thread, api_key="k")
    assert len(calls3) == 1, "follow_altitude still spends the doomed retry"
    assert "cannot authenticate" in str(caught3.value)


# ===========================================================================
# 4. The live arc-drought detector (NL-105 gate R-4)
# ===========================================================================

def _edition(date, authored=0, omitted=0, eligible=True):
    """One generation_log row, built through memory_core's OWN markers."""
    rewrites = []
    for _ in range(authored):
        rewrites.append({"outcome": "written",
                         "detail": f"5 sentence(s); {memory_core.ARC_AUTHORED_MARK}"})
    for _ in range(omitted):
        rewrites.append({"outcome": "written",
                         "detail": f"arc line rejected (cap) — "
                                   f"{memory_core.ARC_OMITTED_MARK}"})
    if not eligible:
        rewrites.append({"outcome": "written", "detail": "5 sentence(s)"})
    return {"date": date, "status": "ok", "memory": {"state_rewrites": rewrites}}


def _run_detector(tmp_path, monkeypatch, rows, archived=()):
    """Drive the detector over a synthetic record.

    THE SEGMENTS ARE REAL (NL-160 gate R-7): `log_archives` and `log_file` are
    redirected and `generate.log_segments()` — the house's own reading order — is
    left to compose them, so these pins exercise the path production takes rather
    than a stub of it. `archived` fills a rotated-out segment; empty by default,
    which is the un-rotated world every earlier pin here describes."""
    from newslens import generate
    log = tmp_path / "generation_log.jsonl"
    log.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    archives = []
    if archived:
        arc = tmp_path / "generation_log.1.jsonl"
        arc.write_text("".join(json.dumps(r) + "\n" for r in archived),
                       encoding="utf-8")
        archives.append(arc)
    monkeypatch.setattr(generate, "log_file", lambda data_dir=None: log)
    monkeypatch.setattr(generate, "log_archives", lambda data_dir=None: archives)
    return doctor.check_arc_continuity()


def test_the_drought_warns_at_three_consecutive_blank_editions(
        tmp_path, monkeypatch):
    """BORN RED at 9107699 — check_arc_continuity did not exist.

    The real drought ran TWELVE editions (2026-07-24 to 2026-08-14) while 49
    synthetic pins stayed green; this line is what would have spoken on day
    three. Replayed against the principal's own log at that cutoff, it does."""
    rows = [_edition("2026-07-23", authored=1),
            _edition("2026-07-24", omitted=2),
            _edition("2026-07-25", omitted=3),
            _edition("2026-07-26", omitted=3)]
    results = _run_detector(tmp_path, monkeypatch, rows)
    assert results[0].status == doctor.WARN
    assert "arc DROUGHT: 3 consecutive" in results[0].text
    assert "2026-07-24 to 2026-07-26" in results[0].text


def test_two_blank_editions_are_quiet(tmp_path, monkeypatch):
    """The noise floor. One or two quiet editions are ordinary; a monitor that
    cries on them gets ignored, and an ignored monitor is the one that misses
    edition twelve."""
    rows = [_edition("2026-07-23", authored=1),
            _edition("2026-07-24", omitted=2),
            _edition("2026-07-25", omitted=1)]
    results = _run_detector(tmp_path, monkeypatch, rows)
    assert results[0].status == doctor.PASS


def test_editions_with_no_arc_eligible_thread_are_EXCLUDED_not_counted_blank(
        tmp_path, monkeypatch):
    """A day whose threads did not move is not a day the arc machinery failed.
    Counting it as blank would make the detector cry drought over quiet news —
    the same population rule the NL-105 streak pin uses ("consecutive among
    editions that produced arc candidates")."""
    rows = [_edition("2026-07-23", authored=1),
            _edition("2026-07-24", eligible=False),
            _edition("2026-07-25", eligible=False),
            _edition("2026-07-26", eligible=False)]
    results = _run_detector(tmp_path, monkeypatch, rows)
    assert results[0].status == doctor.PASS
    assert "1 of the last 1" in results[0].text


def test_a_torn_log_line_is_counted_not_fatal(tmp_path, monkeypatch):
    """An append-only log a crash can interrupt will have a torn tail one day.
    The detector says so and keeps reading — it never takes the doctor down."""
    log = tmp_path / "generation_log.jsonl"
    log.write_text(json.dumps(_edition("2026-07-23", authored=1))
                   + "\n{\"date\": \"2026-07-24\", trunc\n", encoding="utf-8")
    from newslens import generate
    monkeypatch.setattr(generate, "log_file", lambda data_dir=None: log)
    monkeypatch.setattr(generate, "log_archives", lambda data_dir=None: [])
    results = doctor.check_arc_continuity()
    assert any("unparseable line(s)" in r.text for r in results)


def test_the_detector_follows_memory_cores_marker_CONSTANT(
        tmp_path, monkeypatch):
    """MUTATION PIN — the one that keeps this detector from going blind.

    The doctor counts arcs by looking for a substring that memory_core writes.
    If the detector held its own retyped copy, a future reword of the writer's
    phrasing would leave the doctor reporting a healthy streak forever: a
    monitor that says "fine" because it stopped recognising the thing it
    watches. That is the 2026-08-24 defect class exactly. So: move the constant
    to a phrase that appears nowhere in this repo, write the log through it, and
    require the count to follow. A hand-typed detector cannot."""
    monkeypatch.setattr(memory_core, "ARC_AUTHORED_MARK",
                        "zzq-continuity-line-shipped")
    rows = [_edition("2026-07-23", authored=1),
            _edition("2026-07-24", authored=1)]
    results = _run_detector(tmp_path, monkeypatch, rows)
    assert results[0].status == doctor.PASS
    assert "2 of the last 2" in results[0].text, (
        "the detector did not follow the writer's marker — it is holding its "
        "own retyped copy, and it will go blind the next time the phrase moves")


def test_no_log_at_all_is_an_INFO_not_a_failure(tmp_path, monkeypatch):
    """A fresh install has nothing to read. That is not a health problem."""
    from newslens import generate
    monkeypatch.setattr(generate, "log_file",
                        lambda data_dir=None: tmp_path / "absent.jsonl")
    monkeypatch.setattr(generate, "log_archives", lambda data_dir=None: [])
    results = doctor.check_arc_continuity()
    assert results[0].status == doctor.INFO


# ===========================================================================
# 5. THE TRANSPORT LEVEL — the arms that MINT the type (gate FIX-1 / F-1 HIGH)
# ===========================================================================
#
# Everything in section 3 injects SubscriptionAuthError from a site-local stub:
# those pins prove the five retry loops ASK the right question, and prove
# nothing at all about the two lines that decide the ANSWER. The gate measured
# the hole (MUT-2): revert both minting arms in `_subscription_provider` to
# their HEAD shape, leave the class and the predicate standing, and all 99
# shipped pins stay green — the entire batch dead under a green suite, the
# NL-139 class exactly.
#
# So these pins start where the CLI does: a scripted `claude` child, through
# `_subscription_provider` itself, and (for the spawn counts) through ranking's
# production wiring with no site-local stub anywhere.
#
# THE COUNTER RECORDS BESIDE THE STUB, never through an env var (gate G-1).
# `llm._subscription_env` is an 8-key ALLOWLIST, so any bookkeeping variable a
# test exports is stripped before the child can read it — a counter keyed on one
# silently counts zero, which is a spawn pin that cannot fail. The child finds
# its own directory from __file__ instead, and nothing has to survive the strip.

_STUB_HEAD = """\
#!/usr/bin/env python3
import sys, os, json
# --version answers WITHOUT touching stdin (the conftest stub's lesson: the
# doctor spawns `<bin> --version` with no stdin pipe) and is NOT counted — the
# counts below are spawns of the model call itself.
if '--version' in sys.argv[1:]:
    print('2.1.212 (NL-160 pin stub, not the real CLI)')
    sys.exit(0)
d = os.path.dirname(os.path.abspath(__file__))
n = len([p for p in os.listdir(d) if p.startswith('call-')]) + 1
with open(os.path.join(d, 'call-%d.json' % n), 'w') as f:
    json.dump({'argv': sys.argv[1:]}, f)
sys.stdin.read()
"""

# The literal 2026-08-24 envelope, on STDOUT, stderr empty — exit 1.
_TAIL_AUTH_EXIT_1 = """\
sys.stdout.write(json.dumps({'type': 'result', 'is_error': True,
    'result': 'Failed to authenticate: OAuth session expired. '
              'Please run `claude login`.',
    'duration_api_ms': 0}))
sys.exit(1)
"""

# The SAME rejection the CLI's other way round: exit 0, is_error true. Which arm
# catches an expired session is the CLI's business, not a reason to retry one.
_TAIL_AUTH_IS_ERROR = """\
sys.stdout.write(json.dumps({'type': 'result', 'is_error': True,
    'result': 'Failed to authenticate: OAuth session expired. '
              'Please run `claude login`.',
    'duration_api_ms': 0}))
sys.exit(0)
"""

# Transport-shaped: the failure text lands on stderr, nothing on stdout.
_TAIL_TRANSPORT = """\
sys.stderr.write('upstream connect error or disconnect/reset before headers')
sys.exit(1)
"""

# A NON-auth envelope failure: the CLI diagnosing itself on stdout, stderr empty.
# This is the pair to the auth arms — same channel, same read, opposite verdict.
_TAIL_NONAUTH_ENVELOPE = """\
sys.stdout.write(json.dumps({'type': 'result', 'is_error': True,
    'result': 'API Error: 529 overloaded_error (Overloaded)',
    'duration_api_ms': 0}))
sys.exit(1)
"""


def _counting_stub(bin_dir: Path, tail: str) -> Path:
    """A scripted `claude` that counts its own spawns beside itself."""
    bin_dir.mkdir(parents=True, exist_ok=True)
    stub = bin_dir / "claude"
    stub.write_text(_STUB_HEAD + tail, encoding="utf-8")
    stub.chmod(stub.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return stub


def _spawns(bin_dir: Path):
    return sorted(p.name for p in bin_dir.iterdir()
                  if p.name.startswith("call-"))


def _rank_request(stub: Path) -> "llm.LaneRequest":
    cfg = llm.resolve_seat("rank", {"NEWSLENS_CLAUDE_BIN": str(stub)})
    assert cfg.lane == "subscription", (
        "the rank seat no longer resolves to the subscription lane — this pin "
        "would be probing a lane that cannot mint the type")
    return llm.LaneRequest(cfg=cfg, prompt="x", temperature=0.0, max_tokens=16,
                           json_mode=False, user_agent="nl160-pin",
                           api_key="", url="")


def test_the_exit1_arm_MINTS_the_type_from_a_real_child(tmp_path, monkeypatch):
    """ARM (a). The 2026-08-24 bytes, through `_subscription_provider` itself:
    child stdout -> envelope walk -> classification -> SubscriptionAuthError,
    with the CLI's own words and the fix hint in the message.

    BORN RED under the gate's MUT-2 (the arm reverted to its HEAD shape, class
    and predicate standing): a plain RuntimeError whose detail is the empty
    stderr — the blank the record shows five times."""
    stub = _counting_stub(tmp_path / "bin", _TAIL_AUTH_EXIT_1)
    monkeypatch.setenv("NEWSLENS_CLAUDE_BIN", str(stub))
    with pytest.raises(llm.SubscriptionAuthError) as caught:
        llm.chat(_rank_request(stub))
    msg = str(caught.value)
    assert "exited 1" in msg
    assert "Failed to authenticate: OAuth session expired." in msg
    assert "run `claude` once interactively" in msg


def test_the_is_error_arm_MINTS_the_type_from_a_real_child(tmp_path,
                                                           monkeypatch):
    """ARM (b) — the arm no probe had ever executed. A CLI that reports the SAME
    expired session with exit 0 + is_error=true must reach the SAME class; a
    fast-fail that depends on which arm the CLI happened to pick is a fast-fail
    that will stop working the day the CLI changes its mind.

    BORN RED under MUT-2 (plain RuntimeError -> the caller retries)."""
    stub = _counting_stub(tmp_path / "bin", _TAIL_AUTH_IS_ERROR)
    monkeypatch.setenv("NEWSLENS_CLAUDE_BIN", str(stub))
    with pytest.raises(llm.SubscriptionAuthError) as caught:
        llm.chat(_rank_request(stub))
    msg = str(caught.value)
    assert "reported an error result" in msg
    assert "OAuth session expired" in msg
    assert "run `claude` once interactively" in msg


def test_a_transport_shaped_child_still_mints_a_PLAIN_RuntimeError(
        tmp_path, monkeypatch):
    """CONTROL (c-i), carried-invariant (born green at 9107699 and green under
    MUT-2 — that is what makes it a control). The carve-out must be surgical at
    the MINT as well as at the sites: a stderr-shaped crash is not auth, so it
    keeps the retry the one-retry law owes it."""
    stub = _counting_stub(tmp_path / "bin", _TAIL_TRANSPORT)
    monkeypatch.setenv("NEWSLENS_CLAUDE_BIN", str(stub))
    with pytest.raises(RuntimeError) as caught:
        llm.chat(_rank_request(stub))
    assert not isinstance(caught.value, llm.SubscriptionAuthError)
    assert "upstream connect error" in str(caught.value)


def test_a_nonauth_envelope_failure_is_QUOTED_and_still_retryable(
        tmp_path, monkeypatch):
    """CONTROL (c-ii) — envelope honesty without envelope credulity. The CLI
    diagnosed itself on stdout with stderr empty: the message must carry those
    words (defect ① is the blank), and the verdict must stay transport-shaped
    (defect ③'s carve-out must not swallow the whole envelope class).

    BORN RED under MUT-2 — the HEAD arm quotes stderr only, so this failure
    printed a blank too."""
    stub = _counting_stub(tmp_path / "bin", _TAIL_NONAUTH_ENVELOPE)
    monkeypatch.setenv("NEWSLENS_CLAUDE_BIN", str(stub))
    with pytest.raises(RuntimeError) as caught:
        llm.chat(_rank_request(stub))
    assert not isinstance(caught.value, llm.SubscriptionAuthError)
    msg = str(caught.value)
    assert "overloaded_error" in msg, "the envelope's own words went missing"
    assert msg.rstrip().endswith("(Overloaded)"), (
        f"the detail is blank or truncated to nothing: {msg!r}")


def test_the_beside_itself_spawn_counter_actually_RECORDS(tmp_path,
                                                          monkeypatch):
    """MECHANISM SANITY, kept from QA's battery and reworked per gate G-1. The
    two spawn-count pins below are only as good as this: prove ONE call through
    the seam leaves exactly ONE record. QA's env-var counter failed here (the
    allowlist strips it) and the spawn pins it fed passed at zero spawns."""
    bin_dir = tmp_path / "bin"
    stub = _counting_stub(bin_dir, _TAIL_AUTH_EXIT_1)
    monkeypatch.setenv("NEWSLENS_CLAUDE_BIN", str(stub))
    with pytest.raises(llm.SubscriptionAuthError):
        llm.chat(_rank_request(stub))
    assert _spawns(bin_dir) == ["call-1.json"], (
        "the counter recorded nothing — the spawn counts below are meaningless")


def test_full_chain_ranking_over_a_real_child_spends_ONE_spawn_on_auth(
        tmp_path, monkeypatch):
    """(d) THE WHOLE CHAIN, production wiring, no site-local stub: ranking ->
    _post_chat -> llm.chat -> _subscription_provider -> a real child. The
    2026-08-24 run, replayed, and the number that is the whole point — ONE
    spawn, not two.

    BORN RED under MUT-2: two spawns, and a RankingError that claims a retry."""
    bin_dir = tmp_path / "bin"
    stub = _counting_stub(bin_dir, _TAIL_AUTH_EXIT_1)
    monkeypatch.setenv("NEWSLENS_CLAUDE_BIN", str(stub))
    monkeypatch.setattr(ranking.time, "sleep", lambda *a, **k: None)
    with pytest.raises(ranking.RankingError) as caught:
        ranking._call_llm_validated("", "rank this", set(), {}, [])
    assert _spawns(bin_dir) == ["call-1.json"], (
        f"the doomed retry spawned a second child: {_spawns(bin_dir)}")
    msg = str(caught.value)
    assert "cannot authenticate" in msg and "OAuth session expired" in msg
    assert "after one retry" not in msg


def test_full_chain_ranking_over_a_real_child_still_spends_TWO_on_transport(
        tmp_path, monkeypatch):
    """(d) THE CONTROL, carried-invariant (born green; green under MUT-2). Two
    real spawns for a real transport failure — the receipt that the one above
    measures a carve-out and not a broken retry loop."""
    bin_dir = tmp_path / "bin"
    stub = _counting_stub(bin_dir, _TAIL_TRANSPORT)
    monkeypatch.setenv("NEWSLENS_CLAUDE_BIN", str(stub))
    monkeypatch.setattr(ranking.time, "sleep", lambda *a, **k: None)
    with pytest.raises(ranking.RankingError) as caught:
        ranking._call_llm_validated("", "rank this", set(), {}, [])
    assert _spawns(bin_dir) == ["call-1.json", "call-2.json"], (
        f"a transport failure lost its retry: {_spawns(bin_dir)}")
    assert "after one retry" in str(caught.value)
    assert "upstream connect error" in str(caught.value)


# ===========================================================================
# 6. THE PER-SITE TRANSPORT CONTROLS (gate FIX-3 / F-3)
# ===========================================================================
#
# Section 3 proves each site declines the DOOMED attempt. Only ranking had the
# other half — that an ORDINARY transport failure still gets its retry. The gate
# measured what that costs (MUT-3): widen analysis's guard to every RuntimeError
# — every analyst transport retry dead — and all 29 shipped pins stay green.
# One control per site, each red-proven by widening that site's own guard.


def _counting_transport_raiser(calls):
    def _boom(*a, **k):
        calls.append(1)
        raise RuntimeError("claude -p (seat) exited 1: overloaded_error")
    return _boom


@pytest.fixture
def no_sleep(monkeypatch):
    """The four sites reach `time.sleep` three different ways (module attribute,
    function-local import, `ranking.time`); patch the module object once."""
    import time as _time
    monkeypatch.setattr(_time, "sleep", lambda *a, **k: None)
    monkeypatch.setattr(ranking.time, "sleep", lambda *a, **k: None)
    return None


@pytest.mark.parametrize("step", ["editor", "script", "narrative"])
def test_generate_STILL_spends_two_attempts_on_transport(step, monkeypatch,
                                                         no_sleep):
    """CONTROL for generate.call_llm, on each of the three seats it serves —
    the build's own thinnest-coverage note (its route pin exercised `editor`
    only). Red when this site's guard is widened to all RuntimeError."""
    from newslens import generate
    calls = []
    monkeypatch.setattr(generate, "_chat", _counting_transport_raiser(calls))
    monkeypatch.setattr(generate.llm, "check_lane", lambda *a, **k: None)
    with pytest.raises(generate.GenerateError) as caught:
        generate.call_llm("k", "p", step, 100, 0.0, False)
    assert len(calls) == 2, f"{step}: the one-retry law was disarmed"
    assert "after one retry" in str(caught.value)


def test_analysis_STILL_spends_two_attempts_on_transport(monkeypatch, no_sleep):
    """CONTROL for the analyst seat — the site the gate's MUT-3 killed with all
    29 shipped pins still green."""
    from newslens import analysis
    calls = []
    monkeypatch.setattr(analysis, "_analysis_chat",
                        _counting_transport_raiser(calls))
    monkeypatch.setattr(analysis.llm, "check_lane", lambda *a, **k: None)
    with pytest.raises(RuntimeError) as caught:
        analysis.call_analysis_model("k", "p")
    assert not isinstance(caught.value, llm.SubscriptionAuthError)
    assert len(calls) == 2, "the analyst one-retry law was disarmed"


def test_memory_core_STILL_spends_two_attempts_on_transport(monkeypatch,
                                                            no_sleep):
    """CONTROL for the state seat. It also re-states the BUG-32 shape from the
    other direction: the transport path leaves by the same post-loop block, so
    the spend still rides out on the exception."""
    calls = []
    monkeypatch.setattr(llm, "chat", _counting_transport_raiser(calls))
    monkeypatch.setattr(llm, "check_lane", lambda *a, **k: None)
    with pytest.raises(RuntimeError) as caught:
        memory_core._default_state_chat("k", "p")
    assert not isinstance(caught.value, llm.SubscriptionAuthError)
    assert len(calls) == 2, "the state-seat one-retry law was disarmed"
    assert hasattr(caught.value, "usd_spent")


def test_follow_altitude_STILL_spends_two_attempts_on_transport(monkeypatch,
                                                                no_sleep):
    """CONTROL for the resolver seat, on the BATCH path — the interactive path
    already declines a second window for the timeout class, so the batch path is
    where a widened guard would silently kill a real retry."""
    from newslens import follow_altitude
    calls = []
    monkeypatch.setattr(follow_altitude.llm, "chat",
                        _counting_transport_raiser(calls))
    monkeypatch.setattr(follow_altitude.llm, "check_lane", lambda *a, **k: None)
    thread = follow_altitude.ThreadInput(thread_id=1, topic="Iran War")
    with pytest.raises(follow_altitude.AltitudeError) as caught:
        follow_altitude.resolve_altitude(thread, api_key="k")
    assert len(calls) == 2, "the resolver one-retry law was disarmed"
    assert "after one retry" in str(caught.value)


# ===========================================================================
# 7. WHAT THE CLASSIFIER IS ALLOWED TO READ (gate FIX-4 / R-5, F-5)
# ===========================================================================


# A CLI that crashed mid-write: stdout holds a TRUNCATED envelope, so nothing
# parses, and what is left is the model's own PROSE. The story is a security
# story, because for a news product that is Tuesday.
_TAIL_TRUNCATED_NEWS_PROSE = (
    'sys.stdout.write(\'{"type":"result","is_error":false,"result":"The report '
    "described unauthorized access to defence networks and')\n"
    "sys.exit(1)\n"
)


def test_news_prose_in_a_TRUNCATED_envelope_keeps_its_retry(tmp_path,
                                                            monkeypatch):
    """THE FALSE POSITIVE, INVERTED — QA's construction turned into the pin that
    forbids it, and driven through a real child so it measures the whole chain.

    "unauthorized" is ordinary vocabulary here. Classifying on it takes away a
    retry a genuinely transient crash had coming AND prescribes a needless
    re-login — the expensive direction of this rule's drift, twice over. The
    detail is still QUOTED verbatim (a blank is the other defect this batch
    exists to close); it simply does not get a vote.

    BORN RED on pre-fix bytes: ONE spawn and a "cannot authenticate" verdict."""
    bin_dir = tmp_path / "bin"
    stub = _counting_stub(bin_dir, _TAIL_TRUNCATED_NEWS_PROSE)
    monkeypatch.setenv("NEWSLENS_CLAUDE_BIN", str(stub))
    monkeypatch.setattr(ranking.time, "sleep", lambda *a, **k: None)
    with pytest.raises(ranking.RankingError) as caught:
        ranking._call_llm_validated("", "rank this", set(), {}, [])
    msg = str(caught.value)
    assert _spawns(bin_dir) == ["call-1.json", "call-2.json"], (
        f"model prose stole the retry: {_spawns(bin_dir)} spawn(s), {msg!r}")
    assert "cannot authenticate" not in msg
    assert "unauthorized access to defence networks" in msg, (
        "the prose stopped being quoted — the blank is back")


def test_the_prose_that_steals_the_retry_is_still_MARKER_MATCHED():
    """The half of the pin above that keeps it honest. If the construction ever
    stops containing an auth marker, the pin above would pass for the wrong
    reason — it would be proving nothing rather than proving the source gate.
    So: the text DOES match the marker table, and is refused anyway, on source."""
    truncated = ('{"type":"result","is_error":false,"result":"The report '
                 'described unauthorized access to defence networks and')
    detail, source = llm.subscription_failure_parts(truncated, "")
    assert detail == truncated[:200], "the raw-stdout fallback stopped quoting"
    assert source == llm.DETAIL_FROM_STDOUT
    assert llm.is_auth_failure_text(detail), (
        "the construction no longer contains an auth marker — re-derive it, or "
        "the end-to-end pin above proves nothing")
    assert not llm.is_auth_failure_detail(detail, source)


def test_the_no_output_sentinel_can_never_classify():
    """The other message-only source. "no output on stdout or stderr" is this
    module's own words, not the CLI's — a sentence NewsLens wrote must never be
    read back as evidence about the CLI's login state."""
    detail, source = llm.subscription_failure_parts("", "")
    assert detail == "no output on stdout or stderr"
    assert source == llm.DETAIL_FROM_NOTHING
    assert not llm.is_auth_failure_detail(detail, source)


@pytest.mark.parametrize("stdout,stderr,expected_source", [
    (json.dumps(OUTAGE_ENVELOPE), "", "envelope"),
    ("", "Failed to authenticate: OAuth session expired.", "stderr"),
    ("Failed to authenticate (bare stdout)", "", "stdout"),
    ("", "", "none"),
])
def test_the_walker_reports_where_it_read_from(stdout, stderr, expected_source):
    """One walk, two answers. The source is what the classifier gates on, so it
    is pinned as hard as the text — and there is exactly ONE function producing
    both, which is what keeps a second envelope reader from growing somewhere
    and disagreeing about what the CLI said."""
    detail, source = llm.subscription_failure_parts(stdout, stderr)
    assert source == expected_source
    assert detail.strip()


def test_an_auth_rejection_on_STDERR_is_still_classified(tmp_path, monkeypatch):
    """The stderr channel keeps its vote — it is the CLI's own diagnostic
    stream. Narrowing the classifier's input must not narrow it to the envelope
    alone: a `claude` build that reports auth failures the ordinary Unix way
    still has to fast-fail."""
    stub = _counting_stub(
        tmp_path / "bin",
        "sys.stderr.write('Error: not logged in. Run `claude login`.')\n"
        "sys.exit(1)\n")
    monkeypatch.setenv("NEWSLENS_CLAUDE_BIN", str(stub))
    with pytest.raises(llm.SubscriptionAuthError) as caught:
        llm.chat(_rank_request(stub))
    assert "not logged in" in str(caught.value)


def test_the_doctor_verdict_rides_the_SAME_source_restriction(monkeypatch):
    """One rule, two readers — including the half that says which text counts.
    A probe whose only output is unparseable stdout carrying auth-ish prose must
    read INCONCLUSIVE, never "NOT authenticated": the doctor prescribing a
    re-login off model prose is the 2026-08-24 defect inverted."""
    monkeypatch.setattr(doctor, "_run_auth_probe", lambda *a, **k: _proc(
        1, '{"type":"result","result":"...reported unauthorized access to'))
    monkeypatch.setattr(llm, "resolve_claude_bin",
                        lambda env=None: ("/fake/claude", "env"))
    monkeypatch.setattr(doctor.subprocess, "run",
                        lambda *a, **k: _proc(0, "2.1.212 (stub)"))
    results = doctor.check_subscription_lane(
        {"NEWSLENS_CLAUDE_BIN": "/fake/claude",
         "NEWSLENS_DOCTOR_SUBSCRIPTION_PROBE": "1"})
    line = next(r for r in results if "auth probe" in r.text)
    assert line.status == doctor.WARN and "inconclusive" in line.text
    assert "unauthorized access" in line.text, "the detail stopped being quoted"


# ===========================================================================
# 8. THE MONEY RECORD ACROSS THE CARVE-OUT (gate FIX-5 / R-6, F-6)
# ===========================================================================


def _billing_then(second_exc):
    """Attempt 1 BILLS and then fails validation (a truncated draw is a billed
    draw); attempt 2 raises `second_exc`. QA's template."""
    state = {"n": 0}

    def _chat(key, prompt):
        state["n"] += 1
        if state["n"] == 1:
            return {"usage": {"prompt_tokens": 100, "completion_tokens": 200},
                    "choices": [{"finish_reason": "length",
                                 "message": {"content": "truncated"}}]}
        raise second_exc
    return _chat


def test_a_billed_attempt_survives_the_AUTH_raise(monkeypatch, no_sleep):
    """BORN RED on pre-fix bytes (`llm_attempts` absent from the exception).

    The session expires BETWEEN attempts: attempt 1 truncated after billing,
    attempt 2 hits an expired session. The in-loop carve-out leaves before the
    post-loop stamping, and run_rank reads the ledger OFF THE EXCEPTION — so
    without this, a run that spent real money files a failure row with no money
    on it, against this function's own docstring. Run 28's hole, re-opened by a
    fix for a different defect."""
    sink = []
    monkeypatch.setattr(ranking, "_post_chat", _billing_then(
        llm.SubscriptionAuthError(
            "claude -p (rank) exited 1: Failed to authenticate: OAuth session "
            f"expired. — {llm.AUTH_FIX_HINT}")))
    with pytest.raises(ranking.RankingError) as caught:
        ranking._call_llm_validated("k", "p", set(), {}, [], cost_sink=sink)
    assert len(sink) == 1, "attempt 1 did not ledger into the sink"
    assert getattr(caught.value, "llm_attempts", None) == sink, (
        "the auth raise dropped the ledger — ranking_runs.meta would show a "
        "billed run with no attempts recorded")


def test_a_billed_attempt_survives_the_TRANSPORT_path_unchanged(monkeypatch,
                                                                no_sleep):
    """THE OTHER DIRECTION, carried-invariant (born green). The post-loop
    stamping law is what the pin above restores parity WITH; if this ever moves,
    the pin above is measuring against a moved baseline."""
    sink = []
    monkeypatch.setattr(ranking, "_post_chat", _billing_then(
        RuntimeError("claude -p (rank) exited 1: overloaded_error")))
    with pytest.raises(ranking.RankingError) as caught:
        ranking._call_llm_validated("k", "p", set(), {}, [], cost_sink=sink)
    assert len(sink) == 1
    assert getattr(caught.value, "llm_attempts", None) == sink


# ===========================================================================
# 9. SUITE HERMETICITY FOR THE PROBE FLAG (gate FIX-6 / F-8)
# ===========================================================================


def test_the_live_probe_flag_is_scrubbed_from_the_suite():
    """BORN RED on pre-fix bytes (the flag was not in SCRUBBED_ENV_VARS).

    NEWSLENS_DOCTOR_SUBSCRIPTION_PROBE=1 arms a REAL `claude -p` spawn inside
    check_subscription_lane, and the effective env is process-env-wins — so an
    ambient shell export (exactly what the principal's own manual smoke leaves
    behind) reaches every test that reaches that section. It is $0 today only
    because NEWSLENS_CLAUDE_BIN is redirected to the conftest stub: one env pin
    standing between an ambient export and a metered path. The D2-hermeticity
    law says scrub the flag itself, and the discovery opt-in is the precedent.

    The hostile-ambient half is run out of process (`export ... =1 pytest`); this
    sentinel keeps the list from regressing."""
    import conftest as _cft
    assert "NEWSLENS_DOCTOR_SUBSCRIPTION_PROBE" in _cft.SCRUBBED_ENV_VARS
    assert "NEWSLENS_DOCTOR_SUBSCRIPTION_PROBE" not in os.environ


# ===========================================================================
# 10. THE DETECTOR READS THE WHOLE RECORD (gate FIX-2 / FIX-7)
# ===========================================================================


def test_a_drought_SPANNING_A_ROTATION_counts_whole(tmp_path, monkeypatch):
    """BORN RED on pre-fix bytes (PASS, not WARN — the detector read the live
    segment only and saw a 1-edition blank run).

    NL-154 rotation MOVES lines from the live file into an archive segment. A
    detector wired to `log_file()` alone therefore SHORTENS every drought that
    straddles a cut — and a drought-detector that under-reports droughts is the
    exact failure it was built to catch. `generate.log_segments()` is the house's
    documented reading order and the archive+live union is disjoint by
    construction, so reading it counts the run whole and counts nothing twice."""
    archived = [_edition("2026-07-23", authored=1),
                _edition("2026-07-24", omitted=2),
                _edition("2026-07-25", omitted=1)]
    live = [_edition("2026-07-26", omitted=3)]
    results = _run_detector(tmp_path, monkeypatch, live, archived=archived)
    assert results[0].status == doctor.WARN, (
        "the drought stopped at the rotation boundary — the detector is "
        "reading the live segment only")
    assert "arc DROUGHT: 3 consecutive" in results[0].text
    assert "2026-07-24 to 2026-07-26" in results[0].text
    # And the archived edition that DID carry an arc still counts in the ratio.
    assert "1 of the last 4" in results[0].text


def test_a_MULTIBYTE_torn_tail_is_survived_not_fatal(tmp_path, monkeypatch):
    """BORN RED on pre-fix bytes: UnicodeDecodeError, uncaught (only OSError
    was), escaping check_arc_continuity — and because run_doctor builds EVERY
    section before it prints anything, the whole doctor dies with ZERO output at
    precisely the moment a crashed generate is why it is being run.

    The tail here is cut at byte 2 of the 3-byte em-dash that arc detail strings
    are full of. `errors="replace"` turns the torn bytes into a torn LINE, which
    dies at json.loads and is counted by the skipped-line WARN that already
    existed — no new control flow, and the failure stays visible."""
    from newslens import generate
    log = tmp_path / "generation_log.jsonl"
    good = json.dumps(_edition("2026-07-23", authored=1)).encode("utf-8") + b"\n"
    torn = ('{"date": "2026-07-24", "memory": {"state_rewrites": '
            '[{"detail": "arc line rejected (cap) —').encode("utf-8")[:-1]
    log.write_bytes(good + torn)
    monkeypatch.setattr(generate, "log_file", lambda data_dir=None: log)
    monkeypatch.setattr(generate, "log_archives", lambda data_dir=None: [])

    results = doctor.check_arc_continuity()   # must not raise
    assert results[0].status == doctor.PASS
    assert "1 of the last 1" in results[0].text
    assert any("unparseable line(s)" in r.text and r.status == doctor.WARN
               for r in results), "the torn tail was swallowed silently"


def test_run_doctor_survives_a_multibyte_torn_log(tmp_path, monkeypatch, capsys):
    """THE BLAST RADIUS, pinned where it actually hurt: the whole doctor. A
    section that raises takes every other section's output with it, because
    run_doctor computes them all before printing. This is the end-to-end half of
    the pin above — it prints, and the arc section is in what it prints."""
    from newslens import generate
    log = tmp_path / "generation_log.jsonl"
    log.write_bytes(
        json.dumps(_edition("2026-07-23", authored=1)).encode("utf-8") + b"\n"
        + '{"date": "2026-07-24", "d": "—'.encode("utf-8")[:-1])
    monkeypatch.setattr(generate, "log_file", lambda data_dir=None: log)
    monkeypatch.setattr(generate, "log_archives", lambda data_dir=None: [])
    doctor.run_doctor()
    printed = capsys.readouterr().out
    assert "Arc continuity" in printed
    assert "unparseable line(s)" in printed
