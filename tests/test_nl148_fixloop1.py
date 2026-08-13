"""NL-148 FIX LOOP 1 — the pins for the two principal-ordered items and the
prevention-grade half of QA's §B.3 proposals.

WHAT IS PINNED HERE, and what proof class each pin carries:

  §1  THE LANE-AWARE BUDGET CAP (principal 2026-08-12: "raise the budget cap,
      this is all running over subscription anyway. My generation shouldn't
      fail because of it"). BORN RED, both directions: a shadow-heavy
      subscription run must COMPLETE with a warn; a charged-lane breach must
      still KILL. The warn carries the running totals — NL-80's no-output-
      ceiling class is why a demoted kill may not become a silent pass.

  §2  TRIPWIRE ATTRIBUTION (QA §B.3 proposal 1). BORN RED. The autouse
      real-state tripwire detects CHANGE by stat and cannot detect AUTHORSHIP;
      on 2026-08-12 it billed the principal's own live-server click to a
      GET-only test. The in-suite write ledger is what lets the failure say
      which of the two things happened.

  §3  THE SESSION FLOORS (QA §B.3 proposal 2). BORN RED. (a) the five path
      seams hold a process-level value pointing at a directory that does not
      exist, so the founder's real files are unreachable from inside this
      process in EVERY phase — including after a test's monkeypatches unwind,
      which is the straggler window; (b) in-suite HTTP request threads are
      non-daemon and therefore JOINED by server_close, so a straggler stops
      being a thing that can exist.

  §4  F6 — the offline guard's connect_ex seam. BORN RED.

Offline by construction: autouse sandbox + loopback guard, no network, no key,
$0. Nothing here spends and nothing here writes real state.
"""

from __future__ import annotations

import errno
import http.server
import json
import os
import socket
import subprocess
import sys
import threading
import time

import pytest

from newslens import db, generate, paths

import conftest as cf

from test_generate import (                               # noqa: F401
    ENV as GEN_ENV, A_DAY, B_DAY, compliant_script, fake_model,
    slot as gen_slot, seed_briefing, stories_payload, _fake_audio_ok,
)


# ===========================================================================
# §1 — THE LANE-AWARE BUDGET CAP
# ===========================================================================

def _cap_harness(con, monkeypatch, fake_model, est, date=A_DAY):
    """A run_generate that reaches the writer family with a rigged estimate.

    `est` is what every pre-call budget estimate returns, so the gate under
    test is driven by ONE number instead of by a real prompt's size. Same
    device the pre-existing cap tests use (test_generate.py), same reason: the
    thing under test is the VERDICT, not the estimator."""
    slots = [gen_slot(1, title="ONE")]
    seed_briefing(con, date, slots)
    _fake_audio_ok(monkeypatch, [])
    fake_model.narrative = stories_payload(slots)
    fake_model.script = compliant_script(slots)
    monkeypatch.setattr(generate, "_est_cost",
                        lambda p, m, step="narrative": est)
    return slots


def test_cap_verdict_is_lane_aware(monkeypatch):
    """BORN RED — the contract in one place, both lanes, all three verdicts.

    The estimate is shadow-denominated. On the SUBSCRIPTION lane it costs $0,
    so it can never push CHARGED past the cap; on the api lane charged ==
    shadow and the old predicate is reproduced exactly."""
    monkeypatch.setenv("NEWSLENS_LANE_SCRIPT", "api")
    monkeypatch.setenv("NEWSLENS_LANE_EDITOR", "subscription")

    # api lane: charged == shadow, so the old predicate is reproduced exactly
    assert generate._cap_verdict("script", 5.0, spent=2.0, charged=2.0,
                                 cap=10.0) == generate.CAP_CLEAR
    assert generate._cap_verdict("script", 9.0, spent=8.0, charged=8.0,
                                 cap=10.0) == generate.CAP_KILL

    # subscription lane: the same breach is phantom money -> WARN, never KILL
    assert generate._cap_verdict("editor", 9.0, spent=8.0, charged=0.0,
                                 cap=10.0) == generate.CAP_WARN
    # ...and a subscription run whose CHARGED total is already over (real money
    # spent earlier in the run, e.g. an api-lane analyst) still KILLS.
    assert generate._cap_verdict("editor", 0.0, spent=8.0, charged=10.5,
                                 cap=10.0) == generate.CAP_KILL


def test_cap_a_shadow_heavy_subscription_run_completes_with_a_warn(
        tmp_paths, fake_model, monkeypatch):
    """BORN RED — HIS ORDER, the ordered direction.

    Every seat is subscription-default (NL-99), so the whole run bills $0. The
    estimate is rigged past the cap at every gate. Before this fix the run died
    at the narrative gate with a GenerateError; now it warns and ships."""
    db.migrate()
    con = db.connect()
    try:
        _cap_harness(con, monkeypatch, fake_model, est=999.0)
        rep = generate.run_generate(date=A_DAY, con=con, env=GEN_ENV,
                                    refresh=False)

        # THE EDITION SHIPPED — the thing he asked for.
        row = con.execute(
            "SELECT narrative_text, script_text FROM briefings WHERE date = ?",
            (A_DAY,)).fetchone()
        assert row["narrative_text"] and row["script_text"]

        warns = [w for w in rep.warnings if w.startswith("budget:")]
        # the gate that killed HIS run, and the one that used to kill first
        assert any("narrative continued past" in w for w in warns), rep.warnings
        assert any("script continued past" in w for w in warns), rep.warnings
        assert all("SHADOW spend only" in w for w in warns)
    finally:
        con.close()


def test_cap_a_charged_lane_breach_still_kills_the_run(
        tmp_paths, fake_model, monkeypatch):
    """BORN GREEN by design and labeled — the CONTROL half of the pair.

    This is the behaviour that must NOT have moved: real money crossing the cap
    still stops the run cold, before the call. It passes at HEAD because that is
    the whole point of the pair; the pin above is what proves the new arm, and
    this is what proves the new arm did not eat the old one. Its bite is
    measured by mutation instead of by birth (fix-loop report, item 7): with the
    charged arm of `_cap_verdict` deleted, this reds with DID NOT RAISE."""
    monkeypatch.setenv("NEWSLENS_LANE_WRITER", "api")
    db.migrate()
    con = db.connect()
    try:
        _cap_harness(con, monkeypatch, fake_model, est=999.0)
        with pytest.raises(generate.GenerateError) as exc:
            generate.run_generate(date=A_DAY, con=con, env=GEN_ENV,
                                  refresh=False)
        assert "exceeds the remaining budget cap" in str(exc.value)
        # nothing was written on the way out
        row = con.execute(
            "SELECT narrative_text FROM briefings WHERE date = ?",
            (A_DAY,)).fetchone()
        assert row["narrative_text"] is None
    finally:
        con.close()


def test_cap_the_warn_records_the_totals_and_never_reaches_the_edition(
        tmp_paths, fake_model, monkeypatch):
    """BORN RED — NL-80 (the no-output-ceiling class) and the trust-quiet law,
    pinned together because they pull in opposite directions.

    NL-80 says a demoted kill must not become a silent pass: the warn states
    the shadow total, the charged total and the cap, and it lands in the
    generation-log entry, which is where runaway spend has to stay visible.
    Trust-quiet says receipts live on quiet surfaces: the same sentence must
    NOT be anywhere in the edition the reader opens."""
    db.migrate()
    con = db.connect()
    try:
        _cap_harness(con, monkeypatch, fake_model, est=999.0)
        rep = generate.run_generate(date=A_DAY, con=con, env=GEN_ENV,
                                    refresh=False)
        warn = next(w for w in rep.warnings
                    if w.startswith("budget: script continued past"))

        # the totals are IN the sentence — all three figures, named
        assert "$999.0000 estimated" in warn
        assert "shadow" in warn and "actually charged" in warn
        assert f"${generate.config.budget_cap_usd_per_run(GEN_ENV):.2f} cap" in warn

        # ...and it reached the QUIET surface: the generation-log entry
        lines = [json.loads(ln) for ln in
                 (paths.DATA_DIR / "generation_log.jsonl")
                 .read_text(encoding="utf-8").splitlines() if ln.strip()]
        ok = [e for e in lines if e.get("date") == A_DAY
              and e.get("status") == "ok"]
        assert ok, "the completed run left no log entry"
        assert any(w.startswith("budget: script continued past")
                   for w in ok[-1]["warnings"])

        # ...and NOWHERE NEAR the edition body.
        row = con.execute(
            "SELECT narrative_text, script_text FROM briefings WHERE date = ?",
            (A_DAY,)).fetchone()
        assert "budget:" not in (row["narrative_text"] or "")
        assert "budget:" not in (row["script_text"] or "")
        assert "SHADOW" not in (row["narrative_text"] or "")
    finally:
        con.close()


def test_cap_the_env_knob_still_binds_the_charged_lane(
        tmp_paths, fake_model, monkeypatch):
    """CARRIED INVARIANT, born GREEN and labeled as such — BUDGET_CAP_USD_PER_RUN
    keeps working for real money. Measured against the pre-fix artifact: this
    passes there (a cap that low killed on shadow too), so calling it born-red
    would inflate the proof class. Its bite is mutation-proven: with the charged
    arm of `_cap_verdict` deleted it reds with DID NOT RAISE.

    His `.env` sets the knob to 10.00 (raised from 2.50 on his order the same
    day). It must still be the thing that decides where charged dollars stop,
    which means the SAME run must survive a high cap and die under a low one
    with nothing else changed."""
    monkeypatch.setenv("NEWSLENS_LANE_WRITER", "api")
    monkeypatch.setenv("NEWSLENS_LANE_EDITOR", "api")
    monkeypatch.setenv("NEWSLENS_LANE_SCRIPT", "api")
    db.migrate()
    con = db.connect()
    try:
        _cap_harness(con, monkeypatch, fake_model, est=0.10)

        # cap ABOVE the charged projection: the run completes
        rep = generate.run_generate(date=A_DAY, con=con,
                                    env={**GEN_ENV,
                                         "BUDGET_CAP_USD_PER_RUN": "5.00"},
                                    refresh=False)
        assert not [w for w in rep.warnings if w.startswith("budget:")]

        # cap BELOW it: the same run on a fresh day dies, and the knob is the
        # only thing that moved
        _cap_harness(con, monkeypatch, fake_model, est=0.10, date=B_DAY)
        with pytest.raises(generate.GenerateError):
            generate.run_generate(date=B_DAY, con=con,
                                  env={**GEN_ENV,
                                       "BUDGET_CAP_USD_PER_RUN": "0.05"},
                                  refresh=False)
    finally:
        con.close()


# ===========================================================================
# §2 — TRIPWIRE ATTRIBUTION (the in-suite write ledger)
# ===========================================================================

def test_the_write_ledger_records_an_in_suite_write_to_a_watched_path(
        tmp_path, monkeypatch):
    """BORN RED. The ledger's job: when the suite really does write a watched
    real path, the write is on the record with its thread, its test and its
    stack — so a real pinhole fails with the writer in hand instead of a stat
    diff to reason backwards from.

    The watched-prefix list is redirected at tmp_path for the duration: this
    test must PROVE the mechanism, not perform the incident."""
    monkeypatch.setattr(cf, "_LEDGER_WATCH_PREFIXES", (str(tmp_path),))
    mark = len(cf._WRITE_LEDGER)

    target = tmp_path / "pretend-real-sources.yaml"
    with open(target, "w", encoding="utf-8") as fh:          # noqa: SIM115
        fh.write("sources: []\n")

    recorded = cf._WRITE_LEDGER[mark:]
    assert len(recorded) == 1, recorded
    entry = recorded[0]
    assert entry["path"] == str(target)
    assert entry["op"].startswith("open(mode=")
    assert entry["thread"] == threading.current_thread().name
    assert "test_the_write_ledger_records_an_in_suite_write" in entry["test"]
    assert any(__file__.split("/")[-1] in frame for frame in entry["stack"])

    # a READ of the same watched path costs nothing and records nothing —
    # the ledger is about authorship, not traffic
    mark2 = len(cf._WRITE_LEDGER)
    with open(target, encoding="utf-8") as fh:               # noqa: SIM115
        fh.read()
    assert cf._WRITE_LEDGER[mark2:] == []


def test_the_write_ledger_covers_the_atomic_commit_and_delete_verbs(
        tmp_path, monkeypatch):
    """BORN RED. `server._yaml_edit` — the ONE write path to sources.yaml —
    commits with `os.replace`, so a ledger that only saw `open` would miss the
    exact verb the incident's file is written with."""
    monkeypatch.setattr(cf, "_LEDGER_WATCH_PREFIXES", (str(tmp_path),))
    scratch = tmp_path.parent / "off-the-watch.tmp"
    scratch.write_text("x", encoding="utf-8")
    dst = tmp_path / "committed.yaml"

    mark = len(cf._WRITE_LEDGER)
    os.replace(scratch, dst)
    os.remove(dst)
    ops = [e["op"] for e in cf._WRITE_LEDGER[mark:]]
    assert ops == ["os.replace", "os.remove"], cf._WRITE_LEDGER[mark:]


def test_an_empty_ledger_makes_the_tripwire_say_DURING_not_BY():
    """BORN RED — THE INCIDENT-#4 PIN.

    A stat diff with no in-suite write recorded is the signature of an external
    writer (his live `newslens serve` sitting), and the message must say so
    instead of naming the suite. It must also refuse to overclaim: sqlite3's
    writes never pass through the ledger, so an empty ledger is UNATTRIBUTED
    for the db, not proof of innocence."""
    diff = {str(paths._GUARDED["SOURCES_FILE"]):
            {"before": (1786571221076346589, 13028),
             "after": (1786572087652444961, 12931)}}
    msg = cf._tripwire_message(diff, [])

    assert "CHANGED DURING THIS TEST, NOT BY IT" in msg
    assert "EXTERNAL WRITER" in msg
    assert "newslens serve" in msg
    assert "UNATTRIBUTED" in msg
    # the old message's verdict must NOT be asserted over an unattributed change
    assert "sandbox pinhole" not in msg
    # and the stat facts are still all there
    assert "13028" in msg and "12931" in msg


def test_a_nonempty_ledger_makes_the_tripwire_name_the_suite_and_the_writer():
    """BORN RED — the other verdict. A real pinhole must still fail loudly, and
    now with the writing frame attached."""
    diff = {str(paths._GUARDED["MEMORY_FILE"]):
            {"before": (1, 10), "after": (2, 20)}}
    write = {"path": str(paths._GUARDED["MEMORY_FILE"]),
             "op": "open(mode='w')", "thread": "Thread-7",
             "test": "tests/test_x.py::test_y (teardown)",
             "stack": ['  File "tests/test_x.py", line 3, in test_y\n'
                       '    open(real, "w")\n']}
    msg = cf._tripwire_message(diff, [write])

    assert "CHANGED BY THIS SUITE" in msg
    assert "genuine sandbox pinhole" in msg
    assert "Thread-7" in msg
    assert "tests/test_x.py::test_y (teardown)" in msg
    assert "open(mode='w')" in msg
    assert "EXTERNAL WRITER" not in msg


def test_the_tripwire_message_carries_content_shas_but_never_reads_the_env(
        tmp_path):
    """BORN RED. "Includes before/after content shas of the changed file" (QA)
    — because a stat pair says something moved and a sha pair says what it
    became, which is what decides whether a change is his edit or corruption.

    TWO OMISSIONS ARE DELIBERATE AND PINNED HERE, not left to a comment:
      * the principal's `.env` is NEVER read by this suite, not even to digest
        it — the hasher refuses it by identity, whatever its size;
      * a file over the size cap (the 16MB newslens.db) is not hashed either,
        because a per-session 16MB read to improve one failure message is not a
        trade worth making.
    Both SAY so in the message rather than silently printing nothing."""
    import hashlib

    sources = str(paths._GUARDED["SOURCES_FILE"])
    msg = cf._tripwire_message(
        {sources: {"before": (1, 2), "after": (3, 4)}}, [])
    assert "sha at session start:" in msg and "sha now:" in msg

    # the hasher itself: small file -> a real digest; over the cap -> a stated
    # reason, never a silent blank
    small = tmp_path / "small.bin"
    small.write_bytes(b"abc")
    assert cf._content_sha(small) == hashlib.sha256(b"abc").hexdigest()
    big = tmp_path / "big.bin"
    big.write_bytes(b"0" * (cf._SHA_SIZE_CAP_BYTES + 1))
    assert cf._content_sha(big).startswith("not hashed")
    assert str(cf._SHA_SIZE_CAP_BYTES) in cf._content_sha(big)

    # the .env is refused BY IDENTITY — not because it happens to be big
    env_file = paths._GUARDED["ENV_FILE"]
    assert "never read" in cf._content_sha(env_file)
    assert "never read" in cf._SESSION_START_SHAS[str(env_file)]


# ===========================================================================
# §3 — THE SESSION FLOORS
# ===========================================================================

_FLOOR_CHILD = r"""
import os, sys, pathlib
from newslens import paths
paths.allow_real_paths()          # the straggler's worst case: SANCTION ARMED
print("SOURCES_FILE=%s" % paths.SOURCES_FILE)
print("EXISTS=%s" % pathlib.Path(paths.SOURCES_FILE).exists())
"""


def _resolve_in_child(env_extra, unset=()):
    """Resolve paths.SOURCES_FILE in a child under a hand-built environment.

    A CHILD because the property under test is what the process resolves when
    no test's monkeypatch is in scope — which is exactly the phase this test's
    own monkeypatches occupy. Read-only: the child computes a Path and stats
    it; it opens nothing."""
    env = dict(os.environ)
    for name in unset:
        env.pop(name, None)
    env.update(env_extra)
    env["PYTHONPATH"] = str(paths.PROJECT_ROOT / "src")
    out = subprocess.run([sys.executable, "-c", _FLOOR_CHILD],
                         capture_output=True, text=True, env=env,
                         cwd=str(paths.PROJECT_ROOT), timeout=60)
    assert out.returncode == 0, out.stderr
    return dict(ln.split("=", 1) for ln in out.stdout.strip().splitlines())


def test_the_path_seams_have_a_session_floor_that_points_nowhere_real():
    """BORN RED — QA §B.3 proposal 2(a), the prevention-grade half.

    THE GEOMETRY: `paths.__getattr__` resolves the NEWSLENS_* override AHEAD of
    the sanction check. Per-test sandboxing sets those vars; monkeypatch
    restores them to whatever the PROCESS had. When that was 'unset', a
    resolution after the unwind — a straggler request thread, a teardown race,
    the gap between tests — fell through to the sanctioned arm and got the
    founder's real sources.yaml. QA's audit proved the suite never actually
    arms that today; this makes it unreachable rather than merely unvisited.

    MEASURED BOTH WAYS in one child pair, so the pin carries its own control:
      * seams UNSET + sanction armed  -> the REAL sources.yaml (the pre-floor
        post-unwind state, and the reason the floor exists);
      * seams at the FLOOR            -> a path under a directory that is never
        created, which does not exist.
    """
    real = str(paths._GUARDED["SOURCES_FILE"])
    seams = tuple(cf._SESSION_ENV_FLOOR)

    control = _resolve_in_child({"NEWSLENS_PROFILE": "default"}, unset=seams)
    assert control["SOURCES_FILE"] == real, control
    assert control["EXISTS"] == "True"

    floored = _resolve_in_child(
        {**cf._SESSION_ENV_FLOOR, "NEWSLENS_PROFILE": "default"})
    assert floored["SOURCES_FILE"] != real
    assert floored["SOURCES_FILE"] == cf._SESSION_ENV_FLOOR[
        "NEWSLENS_SOURCES_FILE"]
    assert floored["EXISTS"] == "False"

    # the floor is not merely absent — its whole root is absent, so a WRITE
    # there raises ENOENT rather than quietly creating a file somewhere
    assert not cf._SESSION_FLOOR_ROOT.exists()
    # ...and it is nowhere near the checkout
    assert str(paths.PROJECT_ROOT) not in str(cf._SESSION_FLOOR_ROOT)


def test_in_suite_http_request_threads_are_joined_by_server_close():
    """BORN RED — QA §B.3 proposal 2(b).

    `http.server.ThreadingHTTPServer` ships `daemon_threads = True`, and
    socketserver's `_Threads.append` DROPS daemon threads, so `server_close()`
    joins nothing: a handler can still be running after the test that started
    it has finished unwinding. That is the straggler in the F7 geometry.

    Measured behaviourally, not by reading the class attribute: the handler
    stays alive deliberately past its own response, and the pin asserts that
    `server_close()` did not return until the thread was done."""
    started = threading.Event()
    release = threading.Event()
    seen = {}

    class _Lingering(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            body = b"ok"
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            seen["thread"] = threading.current_thread()
            started.set()
            release.wait(10)          # still running when server_close is called

    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Lingering)
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    try:
        import urllib.request
        with urllib.request.urlopen(
                f"http://127.0.0.1:{httpd.server_address[1]}/", timeout=10) as r:
            assert r.read() == b"ok"
        assert started.wait(10)
        handler_thread = seen["thread"]
        assert handler_thread.is_alive()      # the straggler exists right now
        assert not handler_thread.daemon, (
            "a daemon request thread is untracked by socketserver._Threads and "
            "can never be joined")
        release.set()
        httpd.shutdown()
        httpd.server_close()
        # server_close JOINED it — no sleep, no poll, no tolerance window
        assert not handler_thread.is_alive()
    finally:
        release.set()
        try:
            httpd.server_close()
        except Exception:                     # already closed above
            pass


# ===========================================================================
# §3b — NL-149: the masthead date's descenders (his word + two screenshots)
# ===========================================================================

_CHARTER_BOLD_ASCENT_PLUS_DESCENT = (2007 + 492) / 2048        # = 1.2202em


def _dateline_cascade():
    """Every `.dateline` declaration block in the SERVED CSS, paired with the
    @media condition it sits inside (None = unconditional), in source order.

    AUTHORED BY QA (NL-149 pass, F-7) and landed here by NL-149 fix loop 1,
    because this is where the blind pin lives. A real brace walk over the
    artifact the browser gets, not a regex over the first match — that
    difference IS the finding. Comments are stripped first (they legitimately
    contain braces), and only rules whose selector list carries `.dateline`
    ITSELF are collected: `.dateline .dl-year` styles a child and cannot move
    the dateline's own line box."""
    import re as _re
    from newslens import webui

    css = _re.sub(r"/\*.*?\*/", "", webui.CSS, flags=_re.S)
    out, stack, start, i = [], [], 0, 0
    while i < len(css):
        if css[i] == "{":
            stack.append(css[start:i].strip())
            start = i + 1
        elif css[i] == "}":
            prelude = stack.pop() if stack else ""
            if any(s.strip() == ".dateline" for s in prelude.split(",")):
                media = next((p for p in reversed(stack)
                              if p.startswith("@media")), None)
                out.append((media, css[start:i]))
            start = i + 1
        i += 1
    return out


def test_the_dateline_reserves_room_for_its_own_descenders():
    """BORN RED — the spacing he asked for, checked as GEOMETRY not as a string.

    His report: on the no-edition / generating / failed states the date's
    descenders cross the rule below. Cause: `line-height: 1.02` gives the
    dateline a LINE BOX shorter than the display face's ascent+descent, so the
    ink overhangs the element box and the section line's border starts under
    the glyphs. Charter Bold (first --font-display family, and this rule's
    weight) measures unitsPerEm 2048 / hhea ascender 2007 / descender -492 =>
    ascent+descent 1.2202em, so the overhang per side is
    (1.2202 - line-height) / 2 em.

    This reads the SERVED CSS — the artifact the browser gets — parses the
    numbers out of the rules themselves, and asserts the bottom margin actually
    clears the computed overhang. A comment cannot satisfy it and neither can a
    margin that is merely non-zero.

    HARDENED BY NL-149 FIX LOOP 1 — QA F-7, whose cascade walk this is. The
    original resolved the rule with `re.search`, which takes the FIRST match,
    and the served CSS carries `.dateline` TWICE: the base rule (webui.py:110)
    and a mobile override inside `@media (max-width: 900px)` (webui.py:783). So
    this pin had never looked at the phone. It passed because the mobile rule
    overrides font-size ALONE — 2.6rem against an inherited line-height 1.02 and
    an inherited 0.5rem margin, giving 0.2603rem of overhang against 0.5rem of
    reserve — which made the desktop rule the worst case BY LUCK rather than by
    construction. A mobile `margin` or `line-height` override would have sailed
    through while putting the descenders back on the rule at 390px: the exact
    defect he reported, on the exact surface he reads it on. This now resolves
    the CASCADE per breakpoint and asserts the geometry in every context the
    stylesheet defines, so a rule cannot hide behind another rule's position in
    the file.

    Proven to bite (QA mutation, re-taken this loop): adding
    `margin: 0 0 0.1rem;` to the mobile rule, or growing it to 5rem, leaves the
    old first-match form GREEN and turns this one RED."""
    import re as _re

    rules = _dateline_cascade()
    assert len(rules) >= 2, (
        "the two-rule premise moved — re-derive this pin against the served CSS")
    contexts = [None] + sorted({m for m, _ in rules if m})
    checked = 0
    for ctx in contexts:
        eff = {}
        for media, body in rules:
            if media is None or media == ctx:
                eff.update(dict(_re.findall(r"([a-z-]+)\s*:\s*([^;]+)", body)))
        fs = _re.search(r"([\d.]+)rem", eff.get("font-size", ""))
        lh = _re.search(r"([\d.]+)", eff.get("line-height", ""))
        m_bottom = _re.search(r"0 0 ([\d.]+)rem", eff.get("margin", ""))
        assert fs and lh, f"{ctx}: .dateline has no resolvable type geometry"
        assert m_bottom, (
            f"{ctx or 'base'}: the dateline reserves NOTHING below itself — its "
            f"descenders land on whatever follows the masthead. Effective: {eff}")
        mb = float(m_bottom.group(1))
        overhang_rem = ((_CHARTER_BOLD_ASCENT_PLUS_DESCENT - float(lh.group(1)))
                        / 2 * float(fs.group(1)))
        assert overhang_rem > 0, (
            f"{ctx or 'base'}: the line box now contains the ink — this rule's "
            "premise moved")
        assert mb >= overhang_rem, (
            f"{ctx or 'base'}: the dateline reserves {mb}rem below itself but "
            f"its own descenders overhang by {overhang_rem:.4f}rem — they will "
            f"cross the rule again. Effective: {eff}")
        checked += 1
    assert checked >= 2, "only one breakpoint was resolved; the cascade walk broke"


def test_the_bare_masthead_puts_nothing_between_the_date_and_the_rule():
    """BORN GREEN, labeled — the PREMISE of the pin above, pinned so it cannot
    quietly stop being true.

    On the states he screenshotted, `_masthead(None, …)` renders the ceremony
    frame ONLY: wordmark, then dateline, then the header closes and the section
    line's border is the very next thing. That adjacency is why the descenders
    had nothing to fall into. On the normal edition state the dispatch strip
    sits between, which is why that state never showed the defect — and why the
    fix is a margin (it collapses into the strip's larger one) rather than
    padding on the header."""
    from newslens import server

    bare = server._masthead(None, "2026-08-14")
    assert bare.rstrip().endswith("</h1></header>"), bare[-120:]
    assert "dispatch-strip" not in bare


# ===========================================================================
# §4 — F6: the offline guard's connect_ex seam
# ===========================================================================

_UNROUTABLE = ("198.51.100.7", 80)      # TEST-NET-3, RFC 5737


def test_the_no_network_guard_records_and_refuses_connect_ex(no_network):
    """BORN RED (QA F6). `socket.connect_ex` is not implemented in terms of
    `connect` — it is its own method that returns an errno instead of raising —
    so guarding `connect` alone left a second way out of this process.

    This fixture's users assert `attempts == []` to mean "never tried to reach
    the network". Before this fix a connect_ex would have left that list empty
    while actually dialling, which is the assertion reading as proof of
    something it never checked."""
    s = socket.socket()
    s.settimeout(0.01)
    try:
        rc = s.connect_ex(_UNROUTABLE)
    finally:
        s.close()
    assert rc == errno.ECONNREFUSED
    assert no_network == [("connect_ex", str(_UNROUTABLE))]


def test_the_autouse_offline_guard_covers_connect_ex_and_still_passes_loopback():
    """BORN RED. The autouse guard is a BOUNDARY, not a wall: loopback has to
    keep working (`readerserve.port_is_free`'s skip-if-bound handshake is a
    legitimate caller) while everything else is refused."""
    s = socket.socket()
    s.settimeout(0.01)
    try:
        assert s.connect_ex(_UNROUTABLE) == errno.ECONNREFUSED
    finally:
        s.close()

    # a closed loopback port: the refusal comes from the OS, which proves the
    # guard really handed the call through rather than answering for it
    probe = socket.socket()
    probe.bind(("127.0.0.1", 0))
    port = probe.getsockname()[1]
    probe.close()
    s2 = socket.socket()
    s2.settimeout(1.0)
    try:
        rc = s2.connect_ex(("127.0.0.1", port))
    finally:
        s2.close()
    assert rc != 0          # nothing is listening there
