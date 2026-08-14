"""QA adversarial pass — NL-152 / NL-153 / NL-154 settings batch.

RED TESTS ARE ACCEPTANCE CRITERIA. Every test in the `F-n` sections below is
BORN RED at the batch's current bytes and states, in its docstring, the exact
FIX CONTRACT that turns it green. A fix that makes the test pass by weakening
the assertion is a failed fix.

The `GREEN ARMOUR` section at the bottom is born GREEN and is labelled as such:
those are behaviours the batch got RIGHT that no fix-loop may regress while
closing the reds above.

Sandbox: the autouse fixtures in tests/conftest.py redirect DATA_DIR,
SOURCES_FILE and the DB per-test. Nothing here can reach the founder's files.
The founder's real sources.yaml is READ (never written) by `_his_shape` so the
write path is exercised against his actual file structure, not a toy.
"""

import inspect
import json
import os
import stat

import pytest

from newslens import (config, diagnose, doctor, generate, paths, readerserve,
                      schedule, server, webui)

# The founder's real file, READ-ONLY, copied into the sandbox. Its SHAPE is the
# thing under test: a 2-space `settings:` block with his comments inside it, a
# blank line, then `sources:`. A toy fixture cannot catch an indent or
# block-boundary defect that only his structure produces.
_HIS_SOURCES = paths.PROJECT_ROOT / "sources.yaml"


def _his_shape() -> str:
    return _HIS_SOURCES.read_text(encoding="utf-8")


def _install(text: str):
    paths.SOURCES_FILE.write_text(text, encoding="utf-8")
    return paths.SOURCES_FILE


def _with_hour(text: str, hour) -> str:
    return text.replace("  tts_engine: kokoro",
                        f"  tts_engine: kokoro\n  generate_hour: {hour}")


def _fill_log(runs=200, blob=24000, mode="w", fire_first=False,
              torn_tail=False):
    live = generate.log_file()
    live.parent.mkdir(parents=True, exist_ok=True)
    with live.open(mode, encoding="utf-8") as fh:
        if fire_first:
            fh.write(json.dumps({"schedule": schedule.FIRED_PUBLISHED,
                                 "date": "2026-06-01",
                                 "ts": "2026-06-01T06:00:00Z"}) + "\n")
        for i in range(runs):
            fh.write(json.dumps({"stage": "analysis", "n": i}) + "\n")
            fh.write(json.dumps({"date": f"2026-01-{i % 28 + 1:02d}",
                                 "status": "ok", "total_usd": 1.0,
                                 "trigger": schedule.TRIGGER_SCHEDULED,
                                 "blob": "x" * blob}) + "\n")
    if torn_tail:
        with live.open("a", encoding="utf-8") as fh:
            fh.write('{"date": "2026-02-02", "status": "ok", "tor')
    return live


def _true_archived_runs() -> int:
    return sum(1 for p in generate.log_archives()
               for ln in p.read_text(encoding="utf-8").splitlines()
               if generate._safe_is_run(ln))


# ===========================================================================
# F-1 (HIGH) — the archive index is not rebuildable, and a lost index makes
#             the run total permanently and cumulatively wrong.
# ===========================================================================

def test_f1_a_lost_index_is_rebuilt_from_the_segments():
    """FIX CONTRACT: when the index is missing or unreadable,
    `generate.archived_run_count()` must recompute the count by reading the
    archive segments rather than answering 0.

    WHY THIS IS THE HIGH ONE. NL-154's stated purpose for the index is that the
    settings row's "N runs recorded" and the report's "the 30 most recent of N"
    must not silently shrink after a cut. Delete the index — or, far more
    likely, let its best-effort write fail on the same full disk every other arm
    of `rotate_log_if_needed` defends against — and the number does exactly
    that. Worse, it never recovers: the next rotation re-bases from the stale
    prior (`prior = int(idx.get("archived_runs") or 0)`), so the loss compounds
    with every subsequent cut.

    `archived_run_count`'s docstring already promises this property — "DERIVED
    AND REBUILDABLE, never authoritative: every number in it can be recomputed
    by reading the segments" — and no code recomputes it. The fix makes the
    docstring true.

    A cheap fix that keeps the bounded-read property: recompute ONLY when the
    index is absent/corrupt (the rare path), keep reading the index otherwise.
    """
    _fill_log()
    generate.rotate_log_if_needed()
    _fill_log()
    generate.rotate_log_if_needed()
    truth = _true_archived_runs()
    assert truth > 0, "fixture did not archive anything"
    (paths.DATA_DIR / generate.LOG_INDEX_NAME).unlink()
    assert generate.archived_run_count() == truth


def test_f1_rotation_after_an_index_loss_does_not_re_base_from_zero():
    """FIX CONTRACT: a rotation that follows a lost index must not restart the
    running total. After the fix, the archived count following the third cut
    equals the number of run lines actually sitting in the segments.

    MEASURED at current bytes: 420 runs in the segments, index reports 140, and
    `server._run_log_entries()` shows him a total of 200 where the truth is 480.
    """
    _fill_log()
    generate.rotate_log_if_needed()
    _fill_log()
    generate.rotate_log_if_needed()
    (paths.DATA_DIR / generate.LOG_INDEX_NAME).unlink()
    _fill_log()
    generate.rotate_log_if_needed()
    assert generate.archived_run_count() == _true_archived_runs()


def test_f1_the_total_the_reader_is_shown_matches_the_record():
    """FIX CONTRACT: the number rendered on the settings row and the reports
    truncation line must equal live runs + runs actually held in the archives,
    under every index state. This is the row's whole reason to exist."""
    _fill_log()
    generate.rotate_log_if_needed()
    (paths.DATA_DIR / generate.LOG_INDEX_NAME).unlink()
    live_runs = sum(1 for ln in generate.log_file()
                    .read_text(encoding="utf-8").splitlines()
                    if generate._safe_is_run(ln))
    _, shown = server._run_log_entries()
    assert shown == live_runs + _true_archived_runs()


# ===========================================================================
# F-2 (MED) — a line appended during rotation is destroyed.
#   GATE R-D (2026-08-14): CLAIM NARROWED, LOCK NOT ORDERED, BOUND PINNED.
# ===========================================================================

@pytest.mark.xfail(strict=True, reason=(
    "GATE R-D — documented bound, not a defect left open. The window is real "
    "but reachable only cross-process on one DATA_DIR behind the in-flight "
    "guard, the loss bound is one line, and a lock is real machinery for a "
    "one-user app. What the org owed was the overclaim: generate.py's "
    "'DUPLICATION, NEVER LOSS' now carries the single-writer-per-DATA_DIR "
    "qualifier (FIX-5). STRICT so an xpass is a failure: the day anyone lands "
    "the rename-or-flock fix, this pin passes and forces the marker off."))
def test_f2_a_line_appended_during_rotation_is_not_lost(monkeypatch):
    """ORIGINAL FINDING (QA F-2, MED), kept verbatim below because the bound
    this marker documents is only meaningful with the measurement attached.

    FIX CONTRACT: rotation must not be able to destroy an append that landed
    after it read the file. `rotate_log_if_needed` currently does
    read-all -> write-archive -> os.replace(live, kept-tail); anything appended
    inside that window is overwritten by the replace and is gone.

    Two writers append to this file and neither takes a lock:
    `generate.log_generation` (which is also the rotator) and
    `analysis._append_log`. In one process they are sequential, so the reachable
    case is two processes on one DATA_DIR — a manual generate overlapping a
    launchd fire. The in-flight marker guards that, but the guard is itself
    check-then-act.

    THE POINT OF THE PIN IS THE CLAIM, not only the window. The rotator states
    "THE CRASH DIRECTION IS DUPLICATION, NEVER LOSS" and the suite pins byte
    conservation in a single-writer world, so the code reads as no-loss while a
    loss window is open.

    ACCEPTABLE FIXES: (a) rename the live file to the segment and recreate it,
    so appends follow the inode and are never overwritten; (b) hold an advisory
    lock (flock) around append and rotation; (c) re-read after the archive is
    cut and carry any tail that arrived. Suppressing the test is not a fix.

    THE BOUND THIS PIN NOW STATES: with ONE WRITER PER DATA_DIR, rotation loses
    nothing — that is what the rest of this file's conservation pins measure.
    With a second process appending inside the read-all -> os.replace window,
    the lines it wrote in that window are lost, bounded at whatever landed
    there. That sentence is now also in `generate.rotate_log_if_needed`'s own
    docstring, which is where the overclaim was.
    """
    live = _fill_log()
    marker = json.dumps({"date": "APPENDED-MID-ROTATION", "status": "ok"})
    real_read = type(live).read_text
    fired = {"n": 0}

    def hooked(self, *a, **k):
        out = real_read(self, *a, **k)
        if fired["n"] == 0 and self.name == generate.GENERATION_LOG_NAME:
            fired["n"] = 1
            with self.open("a", encoding="utf-8") as fh:
                fh.write(marker + "\n")
        return out

    monkeypatch.setattr("pathlib.Path.read_text", hooked)
    seg = generate.rotate_log_if_needed()
    monkeypatch.undo()
    union = ((seg.read_text(encoding="utf-8") if seg else "")
             + live.read_text(encoding="utf-8"))
    assert "APPENDED-MID-ROTATION" in union


# ===========================================================================
# F-3 (MED) — the doctor states two contradictory facts about one hour.
# ===========================================================================

def _hour_lines(env):
    return [f"{r.status} {r.text}"
            for r in doctor.check_optional_and_guards(env)
            if "hour" in r.text.lower()]


def test_f3_the_doctor_does_not_contradict_itself_about_the_hour():
    """FIX CONTRACT: when the settings layer wins, the doctor must not ALSO
    assert that the env value is "the hour a newly-rendered launchd agent will
    fire at". One fact, one sentence.

    MEASURED at current bytes, with settings=09 and GENERATE_HOUR_LOCAL=6 —
    which is his live configuration, because his .env line 39 sets that var:

      PASS  GENERATE_HOUR_LOCAL = 6 (06:00 local) — the hour a newly-rendered
            launchd agent will fire at                          <- now FALSE
      PASS  scheduled generation is set to 09:00 local in Settings — that value
            wins over GENERATE_HOUR_LOCAL ...                    <- true

    Both PASS. NL-152 added the second line and left the first standing. The
    dispatch's contract is that the doctor tells the truth about which layer
    wins; a true line under a false one does not.

    FIX: make the existing GENERATE_HOUR_LOCAL sentence conditional on the env
    layer actually winning (`config.generate_hour_resolved`), or reword it to
    describe the variable rather than the outcome.
    """
    _install(_with_hour(_his_shape(), 9))
    lines = _hour_lines({"GENERATE_HOUR_LOCAL": "6"})
    claims_env_fires = [l for l in lines
                        if "GENERATE_HOUR_LOCAL = 6" in l
                        and "will fire at" in l]
    assert not claims_env_fires, lines


def test_f3_the_not_set_branch_does_not_claim_the_default_fires():
    """FIX CONTRACT: same defect on the other arm. With no env var and a
    settings hour of 09, the doctor says "the default 6 (06:00 local) is the
    hour `newslens schedule plist` writes into the launchd agent" — false, the
    plist renderer now resolves 09 through `generate_hour_resolved`."""
    _install(_with_hour(_his_shape(), 9))
    lines = _hour_lines({})
    assert not [l for l in lines
                if "not set" in l and "writes into the launchd agent" in l], lines


# ===========================================================================
# F-4 (MED) — the settings tab never discloses which layer won.
# ===========================================================================

def test_f4_the_settings_tab_discloses_that_it_overrides_the_env():
    """FIX CONTRACT: when `schedule.status()["hour_source"]` is `settings` AND
    GENERATE_HOUR_LOCAL is also set, the Generation time row must say so — the
    reader whose .env sets that variable must learn from the screen he is
    looking at that the file is no longer deciding.

    WHY IT MATTERS HERE SPECIFICALLY. The batch chose settings-beats-env for a
    measured reason (env-wins would have shipped a dead control on his machine).
    The cost of that choice is that his .env line 39 becomes inert, and the
    product currently tells him nowhere he is likely to look. `hour_source` is
    computed in `schedule.status()` and rendered in NO UI surface.

    The build report's "four surfaces" all describe the PLIST disagreement, not
    the LAYER disagreement. Only two surfaces name the layer — the doctor, and
    the `schedule status` MISMATCH line, and that line fires only when an agent
    file exists AND its baked hour disagrees. With no agent installed (his state
    today) the doctor is the only disclosure in the product.
    """
    _install(_with_hour(_his_shape(), 9))
    os.environ["GENERATE_HOUR_LOCAL"] = "6"
    try:
        html = server._render_schedule_rows()
    finally:
        os.environ.pop("GENERATE_HOUR_LOCAL", None)
    assert "GENERATE_HOUR_LOCAL" in html or "Settings" in html or ".env" in html, \
        html


# ===========================================================================
# F-5 (MED) — the failure-morning door vanishes after five unreadable rows.
# ===========================================================================

def _seed(con, date, readable=True):
    con.execute("INSERT INTO briefings (date, narrative_text) VALUES (?, ?)",
                (date, ("# X\n\n---\n\n**Headline**\n\nlede\n\n---\n\n*footer*"
                        if readable else "")))
    con.commit()


def test_f5_the_door_survives_a_run_of_unreadable_rows():
    """FIX CONTRACT: `_last_generated_edition` must find the newest READABLE
    edition, not merely look at the five newest ROWS.

    The `LIMIT 5` is justified in the docstring as "five days back is a week of
    consecutive failures" — but the window is spent on ROWS, and a failed
    scheduled morning that reached the rank stage LEAVES a row with nothing
    behind it. So five failed mornings consume the whole window and the button
    disappears on exactly the morning it was built for, while a perfectly
    readable edition sits at row six.

    MEASURED: 5 unreadable rows + a readable 2026-08-08 resolves to None.

    FIX: raise the bound, or bound on candidates EXAMINED against a readable
    result (scan until one readable edition is found, capped at a larger row
    count). The per-candidate cost is one `_log_entry_for`, which NL-154 just
    made a bounded read.

    RESOLVED by gate R-C / FIX-3 (2026-08-14): `LIMIT 5` became the named
    constant `server._LAST_EDITION_SCAN_ROWS = 30`, and the docstring's stale
    cost justification ("a full read of a log that is ~700KB") was corrected.
    THE NUMBER IS ASSERTED HERE TOO, not just the behaviour: six rows would flip
    this pin green while leaving the ratified depth unpinned — the same
    one-directional hole the armour section closes for LOG_RETAIN_RUNS and
    _RUNLOG_MAX_ROWS (mutants M4/M5b).
    """
    from newslens import db
    db.migrate()
    con = db.connect()
    for d in ["2026-08-13", "2026-08-12", "2026-08-11",
              "2026-08-10", "2026-08-09"]:
        _seed(con, d, readable=False)
    _seed(con, "2026-08-08", readable=True)
    got = server._last_generated_edition(con, "2026-08-14")
    con.close()
    assert got is not None and got["date"] == "2026-08-08", got
    assert server._LAST_EDITION_SCAN_ROWS == 30


# ===========================================================================
# F-6 (LOW-MED) — a rotated fire line makes the CLI deny the record exists.
# ===========================================================================

def test_f6_last_fire_still_reads_a_rotated_fire_line():
    """FIX CONTRACT: `schedule.last_fire()` must fall back into the archive
    segments the way `server._log_entry_for` now does, so the schedule surface
    never says "no scheduled fire has been recorded yet" about a fire that is
    sitting in `generation_log.archive-0001.jsonl`.

    NL-154 asserts nothing is deleted — true. But hunt item 5 is that the
    NL-146 audit trail survives rotation READABLY, and this reader cannot see
    past the live segment. The record is intact and the product denies it.

    MEASURED: a fire line older than 60 runs rotates into the archive and
    `last_fire()` returns None; `schedule status` then prints "no scheduled fire
    has been recorded yet".

    Reachable when 60+ runs land with no scheduled fire among them — a battery
    stretch, or a paused schedule with manual generates.
    """
    _fill_log(runs=200, fire_first=True)
    seg = generate.rotate_log_if_needed()
    assert seg is not None and '"schedule"' in seg.read_text(encoding="utf-8"), \
        "fixture did not push the fire line into the archive"
    assert schedule.last_fire() is not None


# ===========================================================================
# F-7 (LOW, PRE-EXISTING in _yaml_edit) — silent byte rewrites of his file.
#   GATE R-E (2026-08-14): ACCEPT-AND-INVERT. Both pins below now assert the
#   CHOSEN CONTRACT (LF is the line ending this editor writes) rather than the
#   fix that was not ordered. The finding stands recorded in each docstring.
# ===========================================================================

def test_f7_the_settings_writer_normalises_exotic_line_breaks_to_lf():
    """LF-ONLY CONTRACT, pinned as the bound the gate chose.

    ORIGINAL FINDING (QA F-7, LOW, pre-existing): `server._yaml_edit` splits
    with `str.splitlines()` — which also splits on U+2028, U+2029, U+0085, VT
    and FF — and rebuilds with `"\\n".join(...)`, so those characters come back
    as newlines. Where the split breaks the YAML the reload-validate reverts and
    the file is protected (verified); the residue is a U+2028 joining two
    COMMENT lines, which splits into two valid comments, parses, and is gone
    under a success message.

    WHY IT WAS NOT FIXED (gate R-E): pre-existing and unchanged by this batch,
    `topic_add`/`topic_remove` carry the same behaviour, and his file measured
    ZERO such characters — no live impact. The day his file gains one is a
    different world, and this pin will say so by failing.

    So the pin asserts the contract in both directions: the break is normalised,
    and the PROSE AROUND IT SURVIVES INTACT — which is the property that makes
    the normalisation acceptable rather than lossy in any way that matters."""
    sep = " "
    original = ("# Pick by ear at the M6 checkpoint; flipping is just "
                "editing this line.")
    text = _his_shape().replace(
        original,
        f"# Pick by ear at the M6 checkpoint;{sep}  # flipping is just editing this.")
    p = _install(text)
    ok, _ = server.settings_set_hour(9)
    assert ok
    body = p.read_text(encoding="utf-8")
    assert sep not in body, "the LF-only contract changed — F-7 is now live"
    assert "# Pick by ear at the M6 checkpoint;" in body
    assert "# flipping is just editing this." in body
    assert config.load_sources().generate_hour == 9


def test_f7_the_settings_writer_normalises_crlf_to_lf():
    """The same contract on the CRLF arm.

    ORIGINAL FINDING (QA F-7): a CRLF sources.yaml loses its CRLF endings across
    a settings write — measured 279 CRLF -> 0, the file shrinks 260 bytes, and
    the write reports success.

    WHY IT WAS NOT FIXED (gate R-E): his file is LF today (measured 0 CRLF) and
    this is a Unix-only tool writing a file it also parses. LF is the contract;
    what the pin protects is that normalisation is the WHOLE change — every
    line's content survives, and the file still loads."""
    text = _his_shape().replace("\n", "\r\n")
    p = _install(text)
    ok, _ = server.settings_set_hour(9)
    assert ok
    body = p.read_text(encoding="utf-8")
    assert body.count("\r\n") == 0, "the LF-only contract changed"
    # Every one of his lines survives; the only difference is the ending and the
    # one key the write was asked to add.
    assert [l for l in body.splitlines() if "generate_hour" not in l] \
        == text.splitlines(), \
        "normalisation lost or changed a line, not just its ending"
    assert config.load_sources().generate_hour == 9


# ===========================================================================
# F-8 (MED) — the reader ledger still dies on a torn append, in a function
#             this batch rewrote, one file away from the fix it applied.
# ===========================================================================

def test_f8_a_torn_append_does_not_crash_the_reader_ledger():
    """FIX CONTRACT: `readerserve.read_ledger` must read with
    `errors="replace"` and catch `(OSError, ValueError)`, exactly as
    `diagnose._load_entries` does after this batch.

    THIS IS NL-149 QA F-1's CLASS, RE-OPENED. UnicodeDecodeError subclasses
    ValueError, NOT OSError, so `except OSError` never sees a partial multibyte
    tail. The batch closed this in `diagnose._load_entries` IN THE SAME DIFF and
    named NL-149 QA F-1 in the comment while doing it — and left `read_ledger`,
    a function it rewrote (+38 −20) in the same batch, on `encoding="utf-8"`
    with a bare `except OSError`.

    THE BATCH ALSO WIDENED THE EXPOSURE. `read_ledger` used to read one file;
    it now reads every segment, so a torn tail anywhere in a profile's whole
    archived history raises where only the live file could before.

    MEASURED: a 77-byte ledger ending in a truncated UTF-8 sequence gives
    `UnicodeDecodeError: 'utf-8' codec can't decode byte 0xc3 in position 76`
    out of `read_ledger`, while `diagnose._load_entries` and
    `server._run_log_entries` both survive the identical file.

    REACHED FROM TWO PLACES, one of them a cleanup path:
    `readerserve.py:506` before the reader server starts, and `:517` inside the
    `finally` that prints the session delta on the way out.
    """
    slug = "tester1"
    d = paths.profile_layout(slug)["DATA_DIR"]
    d.mkdir(parents=True, exist_ok=True)
    with (d / readerserve.LEDGER_NAME).open("wb") as fh:
        fh.write((json.dumps({"date": "2026-08-01", "total_usd": 1.0})
                  + "\n").encode("utf-8"))
        fh.write(b'{"date": "2026-08-02", "note": "caf\xc3')
    led = readerserve.read_ledger(slug)
    assert len(led.entries) == 1
    assert led.malformed == 1


# ===========================================================================
# F-9 (LOW) — a top-level comment misfiles the inserted key.
#   GATE R-E (2026-08-14): ACCEPT-AND-INVERT to a placement bound.
# ===========================================================================

def test_f9_the_column_zero_comment_placement_bound_is_what_it_is():
    """THE STATED BOUND on where an absent key gets filed.

    ORIGINAL FINDING (QA F-9, LOW): `_settings_block` treats a column-0 COMMENT
    as part of the settings body (it terminates only on a non-comment,
    non-blank column-0 line), so a section header between `settings:` and the
    next top-level key extends the block past it and the inserted key lands
    BELOW the header:

        settings:
          tts_engine: kokoro

        # --- Sources ----------
        # rationale line
          generate_hour: 9        <- his settings key, filed under Sources
        sources:

    WHY IT WAS NOT FIXED (gate R-E): it parses, the hour reads back correctly,
    and the shape is ABSENT from his file (a blank line, then `sources:`).
    Walking back over trailing comments is surgery on the crown-jewel writer for
    a cosmetic shape nobody has — worse than the defect. The docstring on
    `server._settings_block` now states the bound.

    SO THIS PIN MEASURES THE BOUND, and the two things that make it acceptable:
    the file still parses, and the hour still round-trips. If the placement ever
    changes — because someone fixed it, or because the block walk drifted — this
    pin fails and the bound gets re-decided rather than silently moving."""
    text = _his_shape().replace(
        "\nsources:\n",
        "\n# --- Sources -----------------\n# rationale line\nsources:\n", 1)
    p = _install(text)
    ok, _ = server.settings_set_hour(9)
    assert ok
    lines = p.read_text(encoding="utf-8").split("\n")
    i = next(i for i, l in enumerate(lines) if "generate_hour" in l)
    # THE BOUND: under a column-0 header, the key files below it.
    assert lines[i - 1].lstrip().startswith("#"), \
        f"placement changed — re-decide the bound: {lines[i - 3:i + 2]}"
    # WHAT MAKES THE BOUND SURVIVABLE: it is a filing defect, never a data one.
    assert config.load_sources().generate_hour == 9
    assert not config.load_sources().problems
    # And the shape stays absent from HIS file: no header, no misfiling.
    assert "\nsources:\n" in _his_shape(), \
        "his file grew a header above `sources:` — F-9 is now live for him"


# ===========================================================================
# F-10 (LOW) — byte conservation is pinned at line level, not byte level.
#   GATE R-E (2026-08-14): ACCEPT-AND-INVERT to a byte bound.
# ===========================================================================

def test_f10_rotation_conserves_bytes_exactly_but_terminates_a_torn_tail():
    """THE BYTE-LEVEL BOUND, measured in both directions.

    ORIGINAL FINDING (QA F-10, LOW): the batch's central pin compares
    `splitlines()` LISTS, which cannot see that the `"\\n".join(kept) + "\\n"`
    rebuild appends a newline to a torn partial last line — measured
    4,820,133 -> 4,820,134 bytes.

    WHY IT WAS NOT FIXED (gate R-E): one byte, ADDED not lost, in the
    duplication direction the rotator explicitly chooses, on a line every reader
    already skips as unparseable.

    SO THE CLAIM IS PINNED AT ITS TRUE STRENGTH: the union is the original
    exactly, plus exactly one newline and only when the tail was torn. Both arms
    run here, so the bound cannot quietly widen (a second byte, or any byte on
    an intact tail, fails this) and cannot quietly narrow without notice."""
    live = _fill_log(torn_tail=True)
    before = live.read_bytes()
    assert not before.endswith(b"\n"), "fixture did not stage a torn tail"
    seg = generate.rotate_log_if_needed()
    after = (seg.read_bytes() if seg else b"") + live.read_bytes()
    assert after == before + b"\n", \
        f"torn-tail delta {len(after) - len(before)} bytes, expected exactly +1"

    # THE OTHER ARM: an intact tail is conserved to the byte, no terminator
    # invented. This is the arm that makes the +1 above a bound and not a habit.
    for p in generate.log_archives():
        p.unlink()
    live = _fill_log()
    whole = live.read_bytes()
    assert whole.endswith(b"\n")
    seg = generate.rotate_log_if_needed()
    assert seg is not None
    assert (seg.read_bytes() + live.read_bytes()) == whole


# ===========================================================================
# F-11 (LOW) — a dead branch that re-spells a constant. BORN RED.
# ===========================================================================

def test_f11_the_hour_row_has_no_unreachable_not_set_branch():
    """FIX CONTRACT: `config.generate_hour_resolved` always returns an int, so
    `_render_schedule_rows`'s `"not set"` value branch and its
    `openScheduleHour({... else 6})` fallback are unreachable — and the literal
    `6` is a second spelling of `config.DEFAULT_GENERATE_HOUR_LOCAL`, which is
    the exact "two spellings of the hour" hazard the resolver's own comment was
    written to close. Remove the dead arms, or source the literal from the
    constant."""
    src = inspect.getsource(server._render_schedule_rows)
    assert "else 6" not in src, "hardcoded default duplicates config's constant"


# ===========================================================================
# F-12 (LOW) — a pin that does not establish its own claim. BORN GREEN:
# the replacement below is the check the batch's pin describes but omits, and
# it passes, so the CLAIM is true today. The finding is the pin, not a defect.
# ===========================================================================

def test_f12_the_no_new_css_pin_actually_consults_the_stylesheet():
    """BORN GREEN — this is the REPLACEMENT pin, and the fact that it passes is
    the point: the classes really are all in the stylesheet.

    THE FINDING IS THE BATCH'S OWN PIN. `test_schedule_rows_use_no_new_css_classes`
    says "Every
    class on these rows already exists in webui.py's stylesheet" and does not
    check the stylesheet — it binds `css` and never reads it, asserting only
    against a hand-written allowlist. A class in the allowlist but absent from
    the CSS would pass; so would a typo shared by both.

    (The six classes DO all exist today — verified independently — so the claim
    is true and the pin is merely not establishing it. This is a surviving
    sibling of the dead-path class the build self-caught.)

    Its fallback arm `webui.page_shell.__doc__` is also dead: `webui` has no
    `page_shell`, so that branch would raise AttributeError if `CSS` ever went
    away.

    FIX: assert every class used by `_render_schedule_rows` appears in
    `webui.CSS`, which is what this test does.
    """
    import re
    html = server._render_schedule_rows()
    used = {c for group in re.findall(r'class="([^"]+)"', html)
            for c in group.split()}
    assert used, "no classes found — the renderer changed shape"
    missing = [c for c in used if f".{c}" not in webui.CSS]
    assert not missing, f"classes with no stylesheet rule: {missing}"


# ===========================================================================
# GREEN ARMOUR — born GREEN at the batch's bytes, labelled.
# These are the behaviours the batch got right. A fix-loop closing the reds
# above must not regress any of them.
# ===========================================================================

def test_green_his_real_file_round_trips_byte_for_byte_outside_the_block():
    """BORN GREEN. THE CROWN-JEWELS PIN, and the batch passes it: writing the
    hour into his ACTUAL file shape leaves every other byte identical — his
    comments, his 2-ins-2-del, the interests blocks, the section headers — and
    the key lands on the line after `tts_engine`, inside the block, before the
    blank."""
    before = _his_shape()
    p = _install(before)
    ok, _ = server.settings_set_hour(9)
    assert ok
    after = p.read_text(encoding="utf-8")
    rest = [l for l in after.split("\n") if "generate_hour" not in l]
    assert rest == before.split("\n")
    assert "  generate_hour: 9" in after
    cfg = config.load_sources()
    assert cfg.generate_hour == 9 and cfg.problems == []


def test_green_a_commented_out_key_is_neither_matched_nor_duplicated():
    """BORN GREEN. His own idiom is to leave commented-out settings as notes
    (see the tts_engine block); the writer must not rewrite one, and must not
    add a second live key beside it."""
    text = _his_shape().replace(
        "  tts_engine: kokoro",
        "  tts_engine: kokoro\n  # generate_hour: 7   <- his note")
    p = _install(text)
    ok, _ = server.settings_set_hour(9)
    assert ok
    after = p.read_text(encoding="utf-8")
    assert after.count("\n  generate_hour:") == 1
    assert "# generate_hour: 7   <- his note" in after
    assert config.load_sources().generate_hour == 9


def test_green_a_file_that_already_has_problems_is_refused_not_clobbered():
    """BORN GREEN on the safety half. A sources.yaml carrying a pre-existing
    validation problem is left byte-identical rather than written.

    NOTE FOR THE RECORD (not a red): the refusal sentence is "Nothing was saved
    — that change would have broken your sources file", which is the "true
    sentence about the wrong thing" class the batch pre-validates the hour range
    to avoid. His change did not break it; it was already broken, and the hour
    is unsettable via the UI until he hand-fixes the unrelated key. Left as a
    finding rather than a red because the FILE is safe, which is the contract."""
    text = _his_shape().replace("  tts_engine: kokoro",
                                "  tts_engine: bogus_engine_name")
    p = _install(text)
    before = p.read_text(encoding="utf-8")
    ok, _ = server.settings_set_hour(9)
    assert not ok
    assert p.read_text(encoding="utf-8") == before


def test_green_an_odd_indent_block_is_refused_not_clobbered():
    """BORN GREEN on the safety half. The insert hardcodes two spaces while the
    replace preserves `m.group(1)`; against a 4-space block the insert yields
    invalid YAML and the validator reverts. File intact."""
    text = _his_shape().replace("  threads_steer_selection: true",
                                "    threads_steer_selection: true").replace(
        "  tts_engine: kokoro", "    tts_engine: kokoro")
    p = _install(text)
    before = p.read_text(encoding="utf-8")
    ok, _ = server.settings_set_hour(9)
    assert not ok
    assert p.read_text(encoding="utf-8") == before


def test_green_a_tap_during_a_config_read_never_shows_a_torn_file():
    """BORN GREEN. `_yaml_edit`'s tmp+os.replace means a reader at generate
    start sees the old file or the new one, never a half-written one. 24
    consecutive writes under a concurrent reader produced zero problems and zero
    truncated source lists."""
    import threading
    _install(_his_shape())
    errs = []
    stop = threading.Event()

    def reader():
        while not stop.is_set():
            try:
                cfg = config.load_sources()
                if cfg.problems or len(cfg.sources) < 5:
                    errs.append((list(cfg.problems), len(cfg.sources)))
            except Exception as exc:      # noqa: BLE001
                errs.append(repr(exc))

    t = threading.Thread(target=reader, daemon=True)
    t.start()
    try:
        for h in range(24):
            server.settings_set_hour(h)
    finally:
        stop.set()
        t.join(timeout=5)
    assert errs == []
    assert config.load_sources().generate_hour == 23


def test_green_an_externally_touched_kill_switch_shows_as_paused():
    """BORN GREEN. `touch data/SCHEDULE_PAUSED` from his shell and the settings
    tab agrees — one state, and the render re-reads disk."""
    _install(_his_shape())
    paths.DATA_DIR.mkdir(parents=True, exist_ok=True)
    schedule.kill_switch_path().touch()
    html = server._render_schedule_rows()
    assert 'aria-checked="false"' in html
    assert "Paused" in html


def test_green_a_read_only_data_dir_leaves_the_toggle_telling_the_truth():
    """BORN GREEN. `set_paused` returns a MEASUREMENT, not an echo: on an
    unwritable data dir the flip does not happen, the return is False, the API
    answers ok:false, and a re-render still shows the schedule ON — which is the
    truth. No picture-of-a-switch."""
    _install(_his_shape())
    d = paths.DATA_DIR
    d.mkdir(parents=True, exist_ok=True)
    mode = d.stat().st_mode
    os.chmod(d, stat.S_IRUSR | stat.S_IXUSR)
    try:
        assert schedule.set_paused(True) is False
        assert 'aria-checked="true"' in server._render_schedule_rows()
    finally:
        os.chmod(d, mode)


def test_green_pausing_while_a_run_is_in_flight_stays_honest():
    """BORN GREEN. The in-flight marker and the kill switch are independent
    files; pausing mid-run flips the switch, leaves the marker alone, and the
    row reports paused without claiming the running generate stopped."""
    import time
    _install(_his_shape())
    paths.DATA_DIR.mkdir(parents=True, exist_ok=True)
    schedule.in_flight_path().write_text(
        json.dumps({"pid": os.getpid(), "started": time.time()}),
        encoding="utf-8")
    assert schedule.set_paused(True) is True
    assert "Paused" in server._render_schedule_rows()
    assert schedule.in_flight_path().exists()


def test_green_the_door_names_a_three_day_old_editions_own_date():
    """BORN GREEN. NL-11 conformance: a 3-day-old last edition is labelled with
    ITS date in human and ISO form, before the button, and the button routes to
    that date — nothing dressed as current."""
    from newslens import db
    db.migrate()
    con = db.connect()
    _seed(con, "2026-08-11")
    got = server._last_generated_edition(con, "2026-08-14")
    con.close()
    note = server._scheduled_failure_note(
        {"date": "2026-08-14", "trigger": schedule.TRIGGER_SCHEDULED,
         "status": "failed", "retryable": True, "total_usd": 0.0}, got)
    assert "Tuesday, August 11" in note and "2026-08-11" in note
    assert note.index("2026-08-11") < note.index("Read last generated edition")
    assert "openEdition('2026-08-11', event)" in note
    assert "function openEdition" in webui.JS


def test_green_rotation_does_not_thrash_and_the_second_cut_is_a_no_op():
    """BORN GREEN. One cut takes the live file well under the cap (measured
    1.45 MB against a 4 MB cap), so the next append does not rotate again."""
    _fill_log()
    assert generate.rotate_log_if_needed() is not None
    assert generate.log_file().stat().st_size < generate.LOG_MAX_BYTES
    assert generate.rotate_log_if_needed() is None


def test_green_trigger_provenance_survives_into_the_archive():
    """BORN GREEN. NL-146's `trigger` key is carried into the segment verbatim,
    so the archived record still says which fires were scheduled."""
    _fill_log(runs=200)
    seg = generate.rotate_log_if_needed()
    body = seg.read_text(encoding="utf-8")
    assert body.count('"trigger"') > 0
    assert all(json.loads(ln).get("trigger") == schedule.TRIGGER_SCHEDULED
               for ln in body.splitlines()
               if generate._safe_is_run(ln))


def test_green_diagnose_and_the_reports_screen_agree_across_a_cut():
    """BORN GREEN. diagnose spans segments for lifetime totals; the reports
    screen keeps its 30 rows from the live segment alone."""
    _fill_log()
    ent_b, _ = diagnose._load_entries()
    rows_b, _ = server._run_log_entries()
    generate.rotate_log_if_needed()
    ent_a, _ = diagnose._load_entries()
    rows_a, _ = server._run_log_entries()
    assert len(ent_a) == len(ent_b)
    assert len(rows_a) == len(rows_b) == server._RUNLOG_MAX_ROWS


# ===========================================================================
# MUTATION ARMOUR — born GREEN. Each pin here exists because a MUTANT that
# breaks a property the batch's own docstrings call load-bearing survived the
# batch's 52 pins. The mutant IDs are from the QA mutation campaign and are
# named so a fix-loop can re-run them.
#
# THE FINDING IS THE PIN, NOT THE CODE: the shipped code is correct on every
# one of these. What was missing was anything that would notice if it stopped
# being correct. These are the siblings of the dead-path pin the build
# self-caught — same class, found by mutation rather than by re-derivation.
# ===========================================================================

def test_armour_the_fire_path_declines_nothing_on_an_hour_disagreement():
    """Kills M15. The batch pins the fire path with
    `assert "generate_hour" not in inspect.getsource(run_scheduled)` — a grep,
    which a real fire-time consult spelled `status(env=env)["configured_hour"]`
    walks straight past, and which is blind to delegation into a helper.

    The property is BEHAVIOURAL and must be pinned behaviourally: with a
    settings hour that disagrees with the wall clock, a scheduled run must
    still fire. This is the pin the docstring's stated purpose needs — "so a
    later fix-loop cannot quietly improve this into the decline-shaped bug"."""
    _install(_with_hour(_his_shape(), (config.DEFAULT_GENERATE_HOUR_LOCAL + 7) % 24))
    fired = {"n": 0}

    def runner(**kw):
        fired["n"] += 1
        return {"status": "ok", "total_usd": 0.0}

    out = schedule.run_scheduled(date="2026-08-14", runner=runner,
                                 sleeper=lambda s: None)
    assert fired["n"] > 0 or out["outcome"] != schedule.FIRED_PAUSED
    assert "hour" not in str(out.get("outcome", "")).lower()


def test_armour_rotation_actually_leaves_the_archive_on_disk():
    """Kills M11. `test_nothing_is_deleted_by_rotation` greps the source for
    "unlink"/"rmtree"; an `os.remove(segment)` that destroys the archived
    record passes it. The property is about the FILESYSTEM, so read the
    filesystem: after a cut the segment exists and holds the dropped runs."""
    _fill_log()
    seg = generate.rotate_log_if_needed()
    assert seg is not None and seg.exists()
    archived = [ln for ln in seg.read_text(encoding="utf-8").splitlines()
                if generate._safe_is_run(ln)]
    assert len(archived) > 0
    assert len(generate.log_archives()) == 1


def test_armour_a_failed_rotation_leaves_the_log_exactly_as_it_was():
    """Kills M20. `test_rotation_never_raises_into_a_generate` asserts only
    that the call returns None — so a failure arm that TRUNCATES the live log
    to zero bytes passes it, destroying the ~30-minute pipeline record the
    docstring says must survive. Assert the record, not the return value."""
    live = _fill_log()
    before = live.read_bytes()

    def boom(*a, **k):
        raise OSError("disk full")

    import pathlib
    real = pathlib.Path.write_text
    pathlib.Path.write_text = boom
    try:
        assert generate.rotate_log_if_needed() is None
    finally:
        pathlib.Path.write_text = real
    assert live.read_bytes() == before
    assert generate.log_archives() == []


def test_armour_the_archive_is_written_before_the_live_file_is_replaced():
    """Kills M12. The rotator states "THE CRASH DIRECTION IS DUPLICATION,
    NEVER LOSS", which is true only while the archive lands FIRST — invert the
    two `os.replace` calls and there is a window in which the dropped lines
    exist nowhere. Nothing in the batch pins the order.

    Observe the actual call order rather than reading the source."""
    _fill_log()
    order = []
    import os as _os
    real = _os.replace

    def watched(src, dst, *a, **k):
        order.append(pathlib_name(dst))
        return real(src, dst, *a, **k)

    def pathlib_name(p):
        return os.path.basename(str(p))

    _os.replace = watched
    try:
        generate.rotate_log_if_needed()
    finally:
        _os.replace = real
    assert generate.GENERATION_LOG_NAME in order, order
    seg_i = next(i for i, n in enumerate(order)
                 if n.startswith(generate.LOG_ARCHIVE_PREFIX)
                 and not n.startswith(generate.LOG_INDEX_NAME))
    live_i = order.index(generate.GENERATION_LOG_NAME)
    assert seg_i < live_i, f"live replaced before the archive landed: {order}"


def test_armour_the_value_line_says_a_different_thing_in_each_state():
    """Kills M14. `_render_schedule_rows` has a five-branch state ladder and
    the batch pins only the literal word "Paused" — collapsing every branch to
    the paused string kills nothing. Each state must be distinguishable."""
    _install(_with_hour(_his_shape(), 9))
    paths.DATA_DIR.mkdir(parents=True, exist_ok=True)

    def value_lines():
        import re
        return re.findall(r'class="settings-row-value">([^<]*)<',
                          server._render_schedule_rows())

    not_installed = value_lines()[0]
    schedule.set_paused(True)
    paused = value_lines()[0]
    schedule.set_paused(False)
    assert "Paused" in paused
    assert "Paused" not in not_installed
    assert not_installed != paused


def test_armour_the_button_carries_its_own_handler_and_quiet_register():
    """Kills M25 and M18. The batch's routing pin is a loose substring over the
    whole note, so a button with NO handler and the routing string parked on a
    hidden <span> passes it; and its quiet-register pin checks only
    `role="alert"` (which `role="alertdialog"` slips past) and the word
    "danger", so a loud red button passes too.

    Bind the handler to the button element and pin the quiet class."""
    import re
    note = server._scheduled_failure_note(
        {"date": "2026-08-14", "trigger": schedule.TRIGGER_SCHEDULED,
         "status": "failed", "retryable": True, "total_usd": 0.0},
        {"date": "2026-08-11", "human": "Tuesday, August 11"})
    m = re.search(r"<button([^>]*)>Read last generated edition</button>", note)
    assert m, note
    attrs = m.group(1)
    assert "openEdition('2026-08-11', event)" in attrs, attrs
    assert 'class="cta-quiet"' in attrs, attrs
    assert "role=" not in attrs, attrs
    assert "color:" not in note and "background:" not in note


@pytest.mark.parametrize("name,want", [("LOG_RETAIN_RUNS", 60),
                                       ("LOG_MAX_BYTES", 4 * 1024 * 1024)])
def test_armour_the_retention_constants_are_the_ratified_numbers(name, want):
    """Kills M4. The batch's only constraint on LOG_RETAIN_RUNS is
    `>= 2 * _RUNLOG_MAX_ROWS`, which is one-directional: 60 -> 61 kills
    nothing, and neither does 30 -> 29 on the screen constant. These are the
    numbers the charter ratified and the SETUP.md prose quotes at him."""
    assert getattr(generate, name) == want


def test_armour_the_reports_screen_row_cap_is_thirty():
    """Kills M5b. Same reasoning: SETUP.md tells him the screen shows 30."""
    assert server._RUNLOG_MAX_ROWS == 30
