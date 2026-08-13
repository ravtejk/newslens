"""NL-149 QA — adversarial pass against the milestone contract (2026-08-13).

The build's own suite (tests/test_nl149_generation_log.py) pins what NL-149
MEANT to do, and it pins it well: 22/22 born red against a `git archive` export
of a36510a, and all four claimed mutation receipts reproduce independently (plus
six more this pass added — every one bites). This file is the other half: the
compositions the milestone's own tests do not reach.

The organising fact of item 2 is that `_run_log_entries()` is called from
`build_page()` — so from NL-149 onward, EVERY page of the app (Today, Following,
Archive, Settings) parses data/generation_log.jsonl before it can render. That
file is append-only, is written by TWO appenders (generate.log_generation with
ensure_ascii=True, analysis.py:3769 with ensure_ascii=False — the founder's file
already carries 560 non-ASCII bytes from the second), and its appends are not
atomic. A reader on that path has to be at least as robust as the file is
fragile. Neither of the two readers now on it is, and the failure is not a
degraded panel: it is HTTP 500 on every HTML door.

CORRECTION THIS PASS OWES ITS OWN FINDING (F-1): the first draft of these tests
called that a NL-149 regression. It is not. `_log_entry_for` (server.py:334) has
read the same file through the same `except OSError` since long before this
milestone, and it is reached from build_page whenever an edition row exists —
which, on the founder's profile, is always. Measured at a36510a: `GET /` and
`GET /archive` already answer HTTP 500 on a torn log. What NL-149 does is add a
SECOND unguarded read and move it to the unconditional part of build_page, so
the same corruption now also reaches the states `_log_entry_for` never did.
Fixing only NL-149's reader leaves the door open — verified, it does.

Every test here runs under the autouse conftest sandbox: no network, no real
data/, $0.
"""

from __future__ import annotations

import json
import re
import threading

import pytest

from newslens import db, labels, paths, server, webui
from test_server import get, replica, seed_briefing, ui           # noqa: F401


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _con():
    db.migrate()
    return db.connect()


def _log():
    paths.DATA_DIR.mkdir(parents=True, exist_ok=True)
    return paths.DATA_DIR / "generation_log.jsonl"


def _run_entry(**over):
    e = {"ts": "2026-08-13T06:00:00Z", "date": "2026-08-13", "status": "ok",
         "total_usd": 0.0, "steps": []}
    e.update(over)
    return e


def _running_job(*boundaries):
    from datetime import datetime, timezone
    job = server._GenJob()
    job.state = "running"
    job.started_at = datetime.now(timezone.utc).isoformat()
    for label, model in boundaries:
        job._progress(label, model)
    return job


# ===========================================================================
# F-1 — a torn append must not take the whole app down          [HIGH, RED]
# ===========================================================================
#
# FIX CONTRACT (one line each, at BOTH reads of this file):
#   server._run_log_entries (server.py:376) AND server._log_entry_for
#   (server.py:334) read the log with
#       log.read_text(encoding="utf-8", errors="replace")
#   and widen their guards to `except (OSError, ValueError)`.
#
#   BOTH, not one: _log_entry_for is the older hole and the one that fires on
#   the founder's profile today; _run_log_entries is the one NL-149 added and
#   the one that fires in the states the older reader never reached. Patching
#   only the new reader was tried this pass and left `GET /` at HTTP 500.
#
#   `errors="replace"` is the load-bearing half and "catch it and return
#   ([], 0)" is NOT an acceptable substitute: one bad byte anywhere in a
#   660KB append-only file would erase the reader's ENTIRE generation record
#   from the report, silently, which is a worse lie than the crash. Replace
#   keeps every intact line and costs exactly the torn one, which then dies at
#   json.loads where a torn line already dies today.
#
#   `except (OSError, ValueError)` is belt-and-braces for the read itself:
#   UnicodeDecodeError subclasses ValueError, not OSError, which is precisely
#   why the current `except OSError` does not see it.

def test_a_torn_append_does_not_take_the_whole_app_down():
    """BORN RED — F-1.

    generation_log.jsonl has two appenders and no atomicity. analysis.py:3769
    writes its stage lines with `ensure_ascii=False`, so real multibyte UTF-8
    is genuinely in that file today (560 non-ASCII bytes in the founder's copy,
    every one of them an em dash inside a `sonar` field). A process kill, a full
    disk, or a power loss between two writev chunks leaves a trailing partial
    multibyte sequence.

    THIS test is the NL-149-specific half: NO edition row exists, so
    `_log_entry_for` is never reached and the only reader on the path is
    `_run_log_entries`. Measured against a `git archive` export of a36510a this
    same call renders a 142,990-byte page; on the working tree it raises. That
    is the widening NL-149 owns, stated exactly.
    """
    intact = json.dumps(_run_entry()) + "\n"
    stage = json.dumps({"ts": "2026-08-13T07:00:00Z", "stage": "analysis",
                        "status": "ok", "sonar": "ok — 8 results"},
                       ensure_ascii=False).encode("utf-8")
    cut = stage.index("—".encode("utf-8")) + 1          # crash INSIDE the em dash
    _log().write_bytes(intact.encode("utf-8") + stage[:cut])

    con = _con()
    try:
        page, _ = server.build_page(con)
    finally:
        con.close()
    assert labels.RUNLOG_TITLE in page


def test_a_torn_append_answers_the_front_door_with_a_500(ui, replica):
    """BORN RED — F-1, PRE-EXISTING half, and the reason the fix contract names
    two call sites.

    `do_GET` catches the exception and answers `<h1>Server error</h1>` with
    status 500 (server.py:4984). So the founder's app does not degrade — it
    stops, on `/` and on `/archive`. (`/api/status` is exempt, which is the
    cruel part: the generating page's poll keeps answering while the page it
    would paint cannot be fetched.)

    This one is RED AT a36510a TOO — measured, not assumed. It is here because
    NL-149's fix loop is the cheap moment to close it and because a milestone
    that doubles a reader of a file owes that file's fragility a look, not
    because NL-149 caused it.

    A PUBLISHED edition is seeded first, deliberately: without one the Stage-0
    first-run router answers `/` with the Commissioning page, which never calls
    build_page — so an unseeded version of this test would pass while proving
    nothing. The founder's profile has 26 editions.
    """
    con = db.connect()
    try:
        seed_briefing(con)
    finally:
        con.close()
    stage = json.dumps({"ts": "2026-08-13T07:00:00Z", "stage": "analysis",
                        "sonar": "ok — 8 results"},
                       ensure_ascii=False).encode("utf-8")
    _log().write_bytes((json.dumps(_run_entry()) + "\n").encode("utf-8")
                       + stage[:stage.index("—".encode("utf-8")) + 1])

    code, _headers, body = get(ui, "/")
    assert code != 500, (
        f"the front door answers HTTP {code} — {body[:200]!r}")
    assert code == 200


def test_a_torn_append_costs_only_the_torn_line():
    """BORN RED — F-1, the honest-degradation half, and the reason the fix is
    `errors="replace"` and not a wider `except` that returns nothing.

    A record that silently shows zero runs because byte 400,001 is half an em
    dash is the failure mode this milestone exists to prevent: the report is
    the surface the reader opens to find out what happened.
    """
    good = "".join(json.dumps(_run_entry(date=f"2026-08-{d:02d}")) + "\n"
                   for d in (11, 12, 13))
    torn = json.dumps({"ts": "2026-08-13T07:00:00Z", "stage": "analysis",
                       "sonar": "ok — 8 results"},
                      ensure_ascii=False).encode("utf-8")
    _log().write_bytes(good.encode("utf-8")
                       + torn[:torn.index("—".encode("utf-8")) + 1])

    runs, total = server._run_log_entries()
    assert total == 3, "the three intact runs must survive one torn byte"
    assert server._render_run_log().count('<div class="runlog-run">') == 3


# ===========================================================================
# F-2 — the SAVED per-step times are systematically wrong        [MED, RED]
# ===========================================================================
#
# FIX CONTRACT (one line):
#   generate._utc_stamp keeps sub-second precision —
#       return (datetime.now(timezone.utc)
#               .strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z")
#   (still ends with "Z", so the milestone's own
#    `assert s["started_at"].endswith("Z")` is unmoved, and
#    _wall_seconds_since already parses it via fromisoformat.)

def test_the_saved_report_never_says_a_run_took_less_than_its_own_steps(
        tmp_path, monkeypatch):
    """BORN RED — F-2, and the invariant is arithmetic, not taste: the stage
    windows are contiguous, non-overlapping, and nested inside the run window,
    so the sum of the steps can never legitimately exceed the total.

    It does today because `generate._utc_stamp()` formats with "%H:%M:%S" —
    the sub-second part of every start is thrown away, so
    `_wall_seconds_since` measures from an instant up to 1s EARLIER than the
    stage actually began. Measured over 12 samples of 0.05s of real work: mean
    +0.550s, max +0.95s, always positive. The run total pays that penalty once;
    a six-stage run pays it six times.

    NOT AN EDGE CASE — it is every run. Simulated over 4,000 start phases at
    several stage counts and durations, `sum > total` held in **100.0%** of
    them: median excess +0.52s at 2 stages, +2.53s at 6, +4.55s at 10. A real
    ten-stage 40-minute run therefore renders `Total 40:00` above steps that add
    to 40:04. M:SS hides it from a glance; it does not survive the reader doing
    on this surface the one thing the surface is for. A fast-failing run (his
    log holds four — the 07-16/07-17 rank failures) shows it outright.

    DETERMINISTIC BY CONSTRUCTION, not by luck: the run is started ~0.9s past a
    second boundary, so every stage stamp truncates back by ~0.9s and the six
    stages over-report by ~5.4s against a run total of well under a second. An
    earlier draft of this test just ran the pipeline and passed or failed
    depending on where in the second it happened to start — that flake is
    itself the finding, so it is pinned out rather than lived with.
    """
    import time as _time
    from datetime import datetime, timezone
    from test_generate import (A_DAY, ENV, _fake_audio_ok, compliant_script,
                               seed_briefing as _seed, slot, stories_payload)
    slots = [slot(1), slot(2)]

    def fake_chat(key, prompt, max_tokens, temperature, json_mode):
        content = (json.dumps(stories_payload(slots)) if json_mode
                   else compliant_script(slots))
        return {"choices": [{"finish_reason": "stop",
                             "message": {"content": content}}],
                "usage": {"prompt_tokens": 900, "completion_tokens": 200}}

    from newslens import generate
    monkeypatch.setattr(generate, "_chat", fake_chat)
    _fake_audio_ok(monkeypatch, [])
    p = tmp_path / "sum.db"
    db.migrate(db_path=p)
    con = db.connect(p)
    _seed(con, A_DAY, slots)
    while datetime.now(timezone.utc).microsecond < 900_000:       # land at ~.9s
        _time.sleep(0.005)
    generate.run_generate(date=A_DAY, con=con, env=ENV, refresh=False,
                          progress=None)
    con.close()

    entry = json.loads(_log().read_text(encoding="utf-8").strip().splitlines()[-1])
    steps_total = sum(s["elapsed_s"] for s in entry["stage_timeline"])
    assert entry["elapsed_s"] + 1e-9 >= steps_total, (
        f"the record says the run took {entry['elapsed_s']}s while its own "
        f"{len(entry['stage_timeline'])} steps add up to {steps_total}s — the "
        "per-stage stamps are truncated to the second and every stage absorbs "
        "the remainder")


def test_the_saved_step_time_agrees_with_the_live_one_for_the_same_work():
    """BORN RED — F-2, the cross-surface half.

    `_gen_log_line` renders BOTH the live panel and the saved report, and the
    milestone's own pin asserts they are one shape. They are not one NUMBER:
    server._GenJob stamps with `datetime.now(timezone.utc).isoformat()`
    (microseconds) while generate._utc_stamp truncates to the second, so the
    same step read live and read back in the report can differ by up to a
    second — on the surface whose entire subject is how long a step took.
    """
    from newslens import generate
    assert re.match(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d+Z$",
                    generate._utc_stamp()), (
        f"generate._utc_stamp() = {generate._utc_stamp()!r} — whole seconds "
        "only, so every persisted stage over-reports by the remainder it "
        "discarded (measured mean +0.55s, max +0.95s, never negative)")


# ===========================================================================
# F-4 — the render path trusts the record's own numbers          [MED, RED]
# ===========================================================================
#
# FIX CONTRACT (two lines, at the render):
#   server._fmt_elapsed  -> `except (TypeError, ValueError, OverflowError)`
#     (int(float("inf")) raises OverflowError, which is an ArithmeticError and
#      is therefore NOT caught by the current tuple; json.loads accepts the
#      bare token `Infinity`, and json.dumps EMITS it for a float inf, so this
#      round-trips through the log by construction the day any writer records
#      one.)
#   server._render_run   -> pass `ts` through `str()` before _fmt_local, or
#     _fmt_local widens to `except (ValueError, AttributeError)`.
#
# Both are "the page must not die of what the log says". Neither invents a
# number: an unreadable duration still renders as the em dash the milestone
# already chose, and an unreadable stamp still renders as itself.

@pytest.mark.parametrize("field, line", [
    ("run elapsed_s",
     '{"ts":"2026-08-13T06:00:00Z","date":"d","status":"ok","total_usd":0,'
     '"elapsed_s":Infinity,"steps":[]}'),
    ("stage elapsed_s",
     '{"ts":"2026-08-13T06:00:00Z","date":"d","status":"ok","total_usd":0,'
     '"steps":[],"stage_timeline":[{"label":"L","model":null,'
     '"elapsed_s":Infinity}]}'),
])
def test_a_nonfinite_duration_in_the_record_cannot_take_the_page_down(field, line):
    """BORN RED — F-4. `Infinity` is a token json.loads accepts and json.dumps
    writes; _fmt_elapsed's guard tuple does not cover the OverflowError that
    int() raises on it, and _fmt_elapsed is on the page-build path."""
    _log().write_text(line + "\n", encoding="utf-8")
    con = _con()
    try:
        server.build_page(con)
    finally:
        con.close()


def test_a_hostile_timestamp_in_the_record_cannot_take_the_page_down():
    """BORN RED — F-4. `_fmt_local` guards ValueError only, so a non-string
    `ts` (a hand-edited log, an epoch written by a future writer, anything but
    the one shape today's writer happens to use) raises AttributeError out of
    _render_run and out of build_page with it. The report is a VIEW over a file
    the app does not own the schema of; it has to survive the file."""
    _log().write_text(json.dumps(_run_entry(ts=1760000000)) + "\n",
                      encoding="utf-8")
    con = _con()
    try:
        server.build_page(con)
    finally:
        con.close()


# ===========================================================================
# F-3 — the log does not say WHICH RUN it belongs to             [MED, RED]
# ===========================================================================
#
# FIX CONTRACT:
#   server._render_today seeds the run's identity beside the cursor —
#       <ol class="gen-log" id="gen-log" data-count="N"
#           data-run="{gen_state['started_at']}">
#   (`started_at` is already on the snapshot; no new key, no route change) —
#   and webui.genLogSync takes it, clearing the list and resetting the cursor
#   when the identity it is handed differs from the one on the element:
#       function genLogSync(steps, runId) {
#         var log = document.getElementById('gen-log');
#         if (!log || !steps) { return; }
#         if (runId && log.getAttribute('data-run') !== runId) {
#           log.textContent = ''; log.setAttribute('data-count', '0');
#           log.setAttribute('data-run', runId);
#         }
#         ...
#       }
#   with pollGeneration passing `d.started_at`.

def test_the_generating_log_says_which_run_it_belongs_to(monkeypatch):
    """BORN RED — F-3.

    The milestone's load-bearing property is "a line that appeared never
    disappears and never changes", and server-side append-only delivers it
    WITHIN a run. Across a run boundary it delivers something worse than a
    disappearing line: a line that is still there and is now false.

    The composition (no JS engine needed to reason about it — the cursor
    arithmetic is arithmetic):

      1. the page is rendered mid-run A with 4 finished steps, so the client's
         cursor is `data-count="4"` over 4 <li>;
      2. the server restarts. pollGeneration's own `.catch` retries in 4s and
         DOES NOT reload — that branch exists precisely so a blip does not
         throw the page away;
      3. run B starts (he re-runs after the restart; the founder runs two
         servers, and `Try again` in a second tab does it too);
      4. the next successful poll returns state="running" with steps=[].
         genLogSync computes `have = 4`, loops `for (i = 4; i < 0; i++)` — zero
         iterations — and leaves data-count at 4.

    Run A's four lines now sit under run B's live clock, and run B's first four
    steps will never be drawn: the log starts appending again only at B's fifth
    boundary. Every line on screen is a lie about which run it belongs to.

    `location.reload()` closes this only when the page SEES a terminal state;
    the restart window is exactly the window where it does not — and that
    window is not 4 seconds. The `.catch` retries every 4s INDEFINITELY: it
    never gives up and never reloads, so a tab left open while `serve` is down
    stays armed for the whole outage, and whatever run is in flight when it
    reconnects is the run it starts appending to.

    The cursor needs an identity beside it, and the snapshot already carries
    one.
    """
    job = _running_job(("Gathering the news", None), ("Ranking stories", None),
                       ("Reading the stories closely", "claude-opus-4-8"))
    monkeypatch.setattr(server, "GEN_JOB", job)
    con = _con()
    try:
        page, _ = server.build_page(con)
    finally:
        con.close()

    ol = page[page.index('<ol class="gen-log"'):]
    ol = ol[:ol.index(">") + 1]
    assert 'data-count="2"' in ol
    assert "data-run=" in ol, (
        "the finished-step log carries a cursor but no run identity, so a "
        "client that survives a run boundary appends run B's steps onto run "
        f"A's lines. As served: {ol}")
    assert job.snapshot()["started_at"] in ol, (
        "the identity must be the run's own — data-run has to change when the "
        "run does, and _GenJob.started_at is already on the snapshot")

    # and the client must actually consult it (source-text receipt, labelled as
    # such — this suite has no JS engine; see the milestone's own §3.4)
    js = webui.JS[webui.JS.index("function genLogSync"):]
    js = js[:js.index("\n}") + 2]
    assert "data-run" in js, (
        "genLogSync ignores the run identity, so seeding it changes nothing")


# ===========================================================================
# F-7 — the .dateline geometry pin reads only the FIRST rule    [LOW, GREEN]
# ===========================================================================

_CHARTER_BOLD_ASCENT_PLUS_DESCENT = (2007 + 492) / 2048        # 1.2202em


def _dateline_cascade():
    """Every `.dateline` declaration block in the SERVED CSS, paired with the
    @media condition it sits inside (None = unconditional), in source order.

    A real brace walk over the artifact the browser gets, not a regex over the
    first match — that difference IS the finding. Comments are stripped first
    (they legitimately contain braces), and only rules whose selector list
    carries `.dateline` ITSELF are collected: `.dateline .dl-year` styles a
    child and cannot move the dateline's own line box.
    """
    css = re.sub(r"/\*.*?\*/", "", webui.CSS, flags=re.S)
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


def test_every_served_dateline_rule_clears_its_own_descenders():
    """CARRIED INVARIANT, hardened — F-7 (disclosed by the build, §0.3).

    NL-148's pin (`test_nl148_fixloop1.py::test_the_dateline_reserves_room_for_
    its_own_descenders`) resolves the rule with `re.search`, which takes the
    FIRST match. The served CSS contains two `.dateline` rules — the base at
    webui.py:110 and the mobile override at webui.py:783 — so that pin has
    never looked at the phone. It passes today because the mobile rule
    overrides font-size ALONE (2.6rem against an inherited line-height 1.02 and
    an inherited 0.5rem margin => 0.2603rem of overhang against 0.5rem of
    reserve), which makes the desktop rule the worst case by luck rather than
    by construction. A mobile `margin` or `line-height` override would sail
    through that pin while putting the descenders back on the rule at 390px —
    the exact defect he reported, on the exact surface he reads it on.

    This resolves the CASCADE per breakpoint and asserts the geometry in every
    context the stylesheet actually defines, so a rule cannot hide behind
    another rule's position in the file.

    Proven to bite (mutation, 2026-08-13): adding `margin: 0 0 0.1rem;` to the
    mobile rule leaves the NL-148 pin GREEN and turns this one RED.
    """
    rules = _dateline_cascade()
    assert len(rules) >= 2, (
        "the two-rule premise moved — re-derive this pin against the served CSS")
    contexts = [None] + sorted({m for m, _ in rules if m})
    checked = 0
    for ctx in contexts:
        eff = {}
        for media, body in rules:
            if media is None or media == ctx:
                eff.update(dict(re.findall(r"([a-z-]+)\s*:\s*([^;]+)", body)))
        fs = re.search(r"([\d.]+)rem", eff.get("font-size", ""))
        lh = re.search(r"([\d.]+)", eff.get("line-height", ""))
        mb = re.search(r"0 0 ([\d.]+)rem", eff.get("margin", ""))
        assert fs and lh, f"{ctx}: .dateline has no resolvable type geometry"
        assert mb, (
            f"{ctx or 'base'}: the dateline reserves NOTHING below itself — "
            f"its descenders land on whatever follows. Effective: {eff}")
        overhang = ((_CHARTER_BOLD_ASCENT_PLUS_DESCENT - float(lh.group(1)))
                    / 2 * float(fs.group(1)))
        assert float(mb.group(1)) >= overhang, (
            f"{ctx or 'base'}: reserves {mb.group(1)}rem below the dateline "
            f"but overhangs by {overhang:.4f}rem — the descenders cross the "
            f"rule again. Effective: {eff}")
        checked += 1
    assert checked >= 2, "only one breakpoint was resolved; the cascade walk broke"


# ===========================================================================
# CARRIED INVARIANTS — what this pass PROVED HELD, pinned so it stays held
# ===========================================================================

def test_the_append_only_invariant_holds_under_concurrent_readers():
    """GREEN, and measured (4 readers x 4000 appends, 0 tears, 0 violations).

    The build pinned `snapshot()` hands out copies but explicitly did not pin a
    RACING reader (its own §7). This is that pin: while the generate thread
    appends, every reader's view is a prefix-extension of its previous view and
    is always JSON-serialisable — which is the tear `self.lock` exists to
    prevent and the reason the status endpoint can serialise mid-run.
    """
    job = _running_job()
    stop = threading.Event()
    errors, violations = [], []

    def writer():
        for i in range(1500):
            job._progress(f"stage {i}", "m" if i % 2 else None)
        stop.set()

    def reader():
        prev = []
        while not stop.is_set():
            try:
                steps = job.snapshot()["steps"]
                json.dumps(steps)
                if steps[:len(prev)] != prev:
                    violations.append((len(prev), len(steps)))
                prev = steps
            except Exception as exc:                        # noqa: BLE001
                errors.append(f"{type(exc).__name__}: {exc}")

    readers = [threading.Thread(target=reader) for _ in range(4)]
    for t in readers:
        t.start()
    w = threading.Thread(target=writer)
    w.start()
    w.join()
    for t in readers:
        t.join()
    assert not errors, errors[:3]
    assert not violations, violations[:3]
    assert len(job.steps) == 1499


def test_the_report_escapes_every_field_the_log_can_carry():
    """GREEN — pinned because the log is the one file on this page whose
    content is ARBITRARY UPSTREAM TEXT: `error` is `str(exc)[:500]` from any
    module, models are ids from a provider, and a stage label is whatever the
    label table says. A future renderer that reaches for an f-string instead of
    _e() would ship stored XSS on the founder's settings surface."""
    payload = '</p><script>alert(1)</script><img src=x onerror=alert(2)>'
    _log().write_text(json.dumps(_run_entry(
        status="failed", date="2026-08-13" + payload, error="upstream: " + payload,
        elapsed_s=61.0,
        stage_timeline=[{"label": "Writing " + payload, "model": "m" + payload,
                         "started_at": "2026-08-13T06:00:00Z",
                         "elapsed_s": 61.0}])) + "\n", encoding="utf-8")
    html = server._render_run_log()
    assert "<script>" not in html and "<img" not in html
    assert html.count("&lt;script&gt;") == 4          # date, error, label, model
    assert "&lt;img src=x onerror=alert(2)&gt;" in html


def test_the_pause_sentence_reaches_the_reader_on_the_report_too():
    """GREEN, and DISCLOSED (see F-5 in the QA report).

    NL-148 clause 4's ruled sentence now has a SECOND reader-facing site: the
    build report's §1 states it "reaches the reader through exactly one site"
    (server.py:2574) and that NL-149 has "zero overlap", which is true of item
    1 and not true of item 2 — `_render_run` renders `entry["error"]`, and for
    a paused run that string IS analysis.FETCH_PAUSE_MESSAGE. The behaviour is
    right (verbatim, escaped, under the word Paused rather than Failed) and the
    milestone's own suite pins it; what was inaccurate is the claim. Pinned
    here so the second site is a known site.
    """
    from newslens import analysis
    _log().write_text(json.dumps(_run_entry(
        status="failed", paused="fetch", retryable=True,
        error=analysis.FETCH_PAUSE_MESSAGE)) + "\n", encoding="utf-8")
    html = server._render_run_log()
    assert analysis.FETCH_PAUSE_MESSAGE in html
    assert labels.RUNLOG_PAUSED in html


def test_the_page_survives_every_other_shape_the_log_can_hold():
    """GREEN — the shapes the build's own `degrades honestly` test does not
    reach. Recorded as a matrix so the next reader of this file knows which
    hostile inputs are ALREADY covered and which three (F-1, F-2, F-4) are not."""
    con = _con()
    log = _log()
    try:
        cases = {
            "missing": lambda: log.unlink(missing_ok=True),
            "empty": lambda: log.write_text("", encoding="utf-8"),
            "blank lines": lambda: log.write_text("\n\n \n", encoding="utf-8"),
            "top-level list": lambda: log.write_text('[{"status":"ok"}]\n',
                                                     encoding="utf-8"),
            "scalars": lambda: log.write_text('42\n"s"\nnull\n', encoding="utf-8"),
            "total_usd a string": lambda: log.write_text(
                json.dumps(_run_entry(total_usd="1.00")) + "\n", encoding="utf-8"),
            "stage_timeline a dict": lambda: log.write_text(
                json.dumps(_run_entry(stage_timeline={"a": 1})) + "\n",
                encoding="utf-8"),
            "timeline holds non-dicts": lambda: log.write_text(
                json.dumps(_run_entry(stage_timeline=[None, 7, "x"])) + "\n",
                encoding="utf-8"),
        }
        for name, setup in cases.items():
            setup()
            server._run_log_entries()
            server.build_page(con)                          # must not raise
    finally:
        con.close()


def test_the_window_boundary_is_exact_and_states_itself():
    """GREEN — the 30-row cap at N and N+1. At exactly the cap the view says
    nothing (there is nothing to say); one run later it states the window."""
    cap = server._RUNLOG_MAX_ROWS
    for n, truncates in ((cap, False), (cap + 1, True)):
        _log().write_text(
            "".join(json.dumps(_run_entry()) + "\n" for _ in range(n)),
            encoding="utf-8")
        runs, total = server._run_log_entries()
        assert (len(runs), total) == (min(n, cap), n)
        html = server._render_run_log()
        assert html.count('<div class="runlog-run">') == min(n, cap)
        assert (labels.RUNLOG_TRUNCATED_MIDDLE in html) is truncates
