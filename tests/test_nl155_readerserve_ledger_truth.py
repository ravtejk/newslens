"""NL-155 item 4 — the reader door's ledger tells the truth about what it counted.

THE CHARTER, and what scouting it actually turned up. NL-146's QA F-1 swept every
`generation_log.jsonl` reader for the fire-line hole and fixed all but one;
`readerserve` was routed to the gate, which ruled CHARTER A ROW ("Third reader of
the same file with the same class hole; F-1's own lesson is that one site is not
closure") and the row landed here. It was still open at ce334d1: `readerserve` is
the only reader of that file in `src/newslens/` with no discriminator — zero
occurrences of `schedule.SCHEDULE_LINE_KEY`, `_is_run_line`, or any `stage` test —
while `server.py:519,521,438`, `diagnose.py:320`, and `generate.py:3785,3887` all
filter.

WHAT THE SCOUT DID NOT PREDICT, AND MEASUREMENT DID: the missing filter is not
only a cosmetic `status '?'` row. The analysis stage writes its OWN line into this
file carrying `total_usd` (analysis.py:4176 -> _append_log at :4253), and the run
entry then carries the SAME money forward as `analysis_usd`
(generate.py:4501 -> emitted at :5269). `entry_money` adds `analysis_usd` to the
run entry's total ON PURPOSE — and with the stage line also in `entries`, the
metered Sonar spend lands in the CHARGED total twice. Measured before the fix, on
the founder-log figure `entry_money`'s own docstring cites:

    stage line alone : (0.013814, 0.0)
    run entry alone  : (0.013814, 0.433814)
    totals([stage, run]) charged = $0.027628   TRUE charged = $0.013814
                                                over-report = 2.0x

The docstring at readerserve.py:279-288 reasons explicitly about double-counting
and concludes "adding them here cannot double-count" — sound for the case it
considered (a FAILED entry, where fold_late_steps moves the analysis row inside
`steps` and the top-level keys are absent) and blind to the case where the stage's
own line is sitting in the same list. Shadow is unaffected: the stage line carries
`total_usd_shadow`, which `entry_money` never reads (it reads
`analysis_usd_shadow`), so only REAL MONEY was overstated. This door's stated
purpose is to prove the metered Sonar money is zero; it was reporting it at 2x.

NL-156's sibling law applies here too: one discriminator, imported from its owner,
never re-spelled. `generate._is_run_line` is promoted to public `is_run_line` in
this batch for exactly that reason.

THE BOUND ON WHAT THESE PINS PROVE (fix loop 1, QA F-2, 2026-08-14). The figures
above are FIXTURE-exact, and the door's post-fix number is RUNS-ONLY — not
log-exact. A metered analysis pass that no run entry carries is invisible to it,
which is a real state: on the founder's own log `analyze` ran twice on 2026-07-17
and the following run carried only the later pass's figure, orphaning that stage
line. Measured across that log: old door $0.926921, this door $0.684297, log-true
$0.686870 — i.e. +$0.240051 (1.35x over) became -$0.002573 (0.37% under). These
tests assert the run-accounted total and must not be read as asserting the log's
total; attributing orphaned stage lines is a separate job.
"""
from __future__ import annotations

import json

import pytest

from newslens import generate, paths, readerserve, schedule

# The founder-log analysis figure entry_money's own docstring cites.
ANALYSIS_USD = 0.013814


def _write_ledger(slug, *lines):
    d = paths.profile_layout(slug)["DATA_DIR"]
    d.mkdir(parents=True, exist_ok=True)
    with (d / readerserve.LEDGER_NAME).open("w", encoding="utf-8") as fh:
        for line in lines:
            fh.write(json.dumps(line, ensure_ascii=False) + "\n")
    return d


def _fire_line():
    """NL-146's fire-decision line, keyed the way its owner keys it."""
    return {"ts": "T0", schedule.SCHEDULE_LINE_KEY: "skipped",
            "reason": "already generated"}


def _analysis_stage_line():
    """What analysis._append_log writes: a stage line whose total_usd is the
    metered Sonar money."""
    return {"ts": "T1", "stage": "analysis", "date": "2026-08-14", "status": "ok",
            "total_usd": ANALYSIS_USD, "total_usd_shadow": ANALYSIS_USD}


def _run_entry(ts="T2"):
    """The edition's own entry, carrying the SAME analysis money forward."""
    return {"ts": ts, "status": "ok", "date": "2026-08-14",
            "total_usd": 0.0,
            "steps": [{"name": "writer", "usd": 0.0, "usd_shadow": 0.42}],
            "analysis_usd": ANALYSIS_USD, "analysis_usd_shadow": ANALYSIS_USD}


def _unreadable_ledger(slug, *lines):
    """A Ledger read while the file is unopenable — `unreadable=True`, and the
    file is restored before we hand it back. This is the state `main` can hold
    in `before` (readerserve.py:563 reads it at startup; the `finally` at :574
    reads `after` at shutdown), so a permission fixed — or a segment restored —
    mid-session produces exactly this pair."""
    d = _write_ledger(slug, *lines)
    (d / readerserve.LEDGER_NAME).chmod(0o000)
    try:
        led = readerserve.read_ledger(slug)
    finally:
        (d / readerserve.LEDGER_NAME).chmod(0o644)
    assert led.unreadable and led.count == 0     # the state under test, not a proxy
    return d, led


def _money_lines(text):
    return [ln.strip() for ln in text.splitlines()
            if ln.strip().startswith("session:")]


def test_the_ledger_door_counts_runs_not_stage_or_fire_lines():
    """BORN RED at ce334d1 (3 entries, not 1).

    KEY PRESENCE, not line shape — the same discriminator every other reader of
    this file uses, imported rather than re-spelled."""
    _write_ledger("tester1", _fire_line(), _analysis_stage_line(), _run_entry())
    led = readerserve.read_ledger("tester1")
    assert led.count == 1, (
        "the reader ledger is counting non-run lines as entries: "
        f"{[e.get('ts') for e in led.entries]}")
    assert led.entries[0]["ts"] == "T2"
    assert led.malformed == 0


def test_the_charged_total_does_not_double_count_analysis_money():
    """BORN RED at ce334d1 ($0.027628 for $0.013814 of real spend).

    The single most load-bearing number this door prints — REAL money — was
    exactly 2x for every edition that ran a metered analysis pass.

    SCOPE (QA F-2): this asserts the RUN-ACCOUNTED total, which is exact here
    because the fixture's stage line has a carrier. It does not assert that the
    door equals the log's total charge — see the module docstring for the
    measured orphan and its size."""
    _write_ledger("tester1", _analysis_stage_line(), _run_entry())
    led = readerserve.read_ledger("tester1")
    charged, shadow = readerserve.totals(led.entries)
    assert charged == pytest.approx(ANALYSIS_USD), (
        f"charged ${charged:.6f} != the ${ANALYSIS_USD:.6f} the runs account "
        "for — the analysis stage line and the run entry are both contributing "
        "it")
    # the shadow half was always right; guard it against an over-correction that
    # "fixes" the double-count by dropping the analysis money altogether
    assert shadow == pytest.approx(0.42 + ANALYSIS_USD)


def test_a_fire_only_ledger_reports_no_runs_rather_than_a_question_mark_row():
    """BORN RED at ce334d1. A profile whose only lines are scheduled-fire
    decisions has generated nothing; the door said "1 entry" and rendered a row
    whose status was literally `?`."""
    _write_ledger("tester1", _fire_line())
    led = readerserve.read_ledger("tester1")
    assert led.count == 0
    before = readerserve.Ledger(path=led.path, exists=True, entries=[],
                                malformed=0)
    text = "\n".join(readerserve.session_lines(before, led, live=False))
    assert "?" not in text.split("LEDGER")[1].split("charged is REAL")[0]


def test_the_lifetime_line_says_runs_and_discloses_the_archive_span():
    """NL-155: the printout names the LIVE file but its totals span every
    rotated segment (NL-154 made that deliberate). The reader was never told —
    the sibling surface discloses it (server.py's "30 most recent of N runs"),
    this one asserted a bare "(N entries)" beside a single filename."""
    _write_ledger("tester1", _run_entry())
    led = readerserve.read_ledger("tester1")
    before = readerserve.Ledger(path=led.path, exists=True, entries=[],
                                malformed=0)
    text = "\n".join(readerserve.session_lines(before, led, live=False))
    assert "run" in text.lower()
    assert "entries)" not in text, (
        "the lifetime line still calls filtered run entries 'entries'")
    assert "archive" in text.lower() or "segment" in text.lower(), (
        "the lifetime total spans rotated segments and does not say so")


def test_an_unreadable_segment_is_disclosed_in_words_not_as_minus_one():
    """NL-155. The unreadable-segment arm returns malformed=-1 as a sentinel,
    and `session_lines` renders it straight into the sentence — the principal
    was shown "-1 unparseable line(s) in this ledger". Worse, that arm discards
    every entry already parsed from earlier segments and then prints
    "lifetime: charged $0.0000" as a flat assertion, which reads as "you have
    never spent anything" rather than "this could not be read"."""
    d = _write_ledger("tester1", _run_entry())
    (d / readerserve.LEDGER_NAME).chmod(0o000)
    try:
        led = readerserve.read_ledger("tester1")
    finally:
        (d / readerserve.LEDGER_NAME).chmod(0o644)
    before = readerserve.Ledger(path=led.path, exists=True, entries=[],
                                malformed=0)
    text = "\n".join(readerserve.session_lines(before, led, live=False))
    assert "-1" not in text, f"the sentinel leaked into the printout:\n{text}"
    assert "could not be read" in text.lower()
    # and it must not assert a lifetime total it does not have
    assert "lifetime" not in text.lower() or "$0.0000" not in text


def test_an_unreadable_ledger_at_session_START_is_never_billed_as_a_delta():
    """FIX LOOP 1, QA F-1 (2026-08-14). BORN RED at the NL-155 build bytes
    (readerserve.py sha256 881d4f10…), where this printed "this session appended
    2 runs" + a session money line for a session that appended nothing.

    `session_lines` guarded `after.unreadable` and never `before.unreadable`.
    When the START read failed, `before.count` is 0 because WE COULD NOT LOOK —
    not because there were no runs — and the delta arm cannot tell those apart:
    its predicate `after.entries[:0] == []` matches VACUOUSLY, so every run this
    profile ever recorded was relabelled as this session's append and charged to
    it. That is the same class of sentence the batch exists to kill: a money
    figure the door cannot support. The state is constructible on this very
    fixture — the batch's own chmod test reaches it."""
    d, before = _unreadable_ledger("tester1", _run_entry("T1"), _run_entry("T2"))
    after = readerserve.read_ledger("tester1")
    assert after.count == 2 and not after.unreadable      # the readable shutdown
    text = "\n".join(readerserve.session_lines(before, after, live=False))
    assert "this session appended" not in text, (
        "the door billed a lifetime to one session — the START read failed, so "
        f"there is no delta to report:\n{text}")
    assert _money_lines(text) == [], (
        f"a 'session:' money line for a session with no measurable delta:\n{text}")
    # it must SAY why, and the lifetime figure (which IS computable) stays
    assert "could not be read" in text.lower()
    assert "lifetime" in text.lower()
    assert f"${2 * ANALYSIS_USD:.4f}" in text


def test_a_ledger_that_changed_shape_charges_no_session_figure_either():
    """FIX LOOP 1, same branch, same defect class (disclosed as a one-line scope
    extension beyond QA F-1's literal text). BORN RED at the NL-155 build bytes.

    The changed-shape arm already refuses to print a DELTA — it says so in
    words and lists the whole file instead — and then printed that whole file's
    money under the word `session:` anyway. Whichever arm we are in, if `new`
    IS `after.entries` the figure is the lifetime figure, and the lifetime line
    below already states it correctly, once."""
    _write_ledger("tester1", _run_entry("T1"), _run_entry("T2"), _run_entry("T3"))
    before = readerserve.read_ledger("tester1")
    assert before.count == 3
    _write_ledger("tester1", _run_entry("T9"))            # truncated under us
    after = readerserve.read_ledger("tester1")
    text = "\n".join(readerserve.session_lines(before, after, live=False))
    assert "changed shape" in text
    assert _money_lines(text) == [], (
        f"the whole file's money is still billed to 'session:':\n{text}")
    assert f"${ANALYSIS_USD:.4f}" in text                  # lifetime, stated once


def test_the_discriminator_is_the_shared_one_not_a_second_spelling():
    """The law `_is_run_line`'s own docstring states: "a second spelling of that
    discriminator is a rotation that counts fire lines as runs while the reports
    screen does not". readerserve must USE it, not reimplement it."""
    assert callable(generate.is_run_line)          # promoted public in NL-155
    src = (paths.PROJECT_ROOT / "src" / "newslens" / "readerserve.py").read_text(
        encoding="utf-8")
    assert "is_run_line" in src, "readerserve does not use the shared predicate"
    assert "SCHEDULE_LINE_KEY" not in src, (
        "readerserve re-spells the schedule discriminator instead of calling "
        "the shared predicate")
    assert 'get("stage")' not in src, (
        "readerserve re-spells the stage half of the discriminator")
