"""newslens diagnose — the readout instrument (ADR-0011 D1/D2).

The instrument's contract: read-only, offline, $0, and the three M7-gate
caveats travel WITH the falsifier number on every run — a number that needs
a footnote must never travel without it.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone

import pytest

from newslens import db, diagnose, paths

NOW = datetime(2026, 7, 10, 12, 0, 0, tzinfo=timezone.utc)


def add_event(con, day, kind="read", date="2026-07-05"):
    con.execute(
        "INSERT INTO consumption_events (date, kind, occurred_at)"
        " VALUES (?, ?, ?)", (date, kind, f"{day}T09:00:00.000Z"),
    )
    con.commit()


def seed_world(tmp_paths, events=(), log_entries=()):
    db.migrate()
    con = db.connect()
    for day in events:
        add_event(con, day)
    con.close()
    log = paths.DATA_DIR / "generation_log.jsonl"
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_text(
        "".join(json.dumps(e) + "\n" for e in log_entries), encoding="utf-8"
    )


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


CAVEAT_FRAGMENTS = (
    "caveats — recorded at the M7 gate (NOTES-M2 21a-c); they ride with the number:",
    "a. UI-only capture BY DESIGN",
    "b. one-page architecture",
    f"c. events through {diagnose.CONSTRUCTION_END_UTC} are construction",
)


def test_cutover_constant_is_the_recorded_boundary():
    assert diagnose.CONSTRUCTION_END_UTC == "2026-07-06"
    assert diagnose.WINDOW_DAYS == 14


def test_diagnose_is_read_only_and_offline(tmp_paths, no_network):
    """Byte-identical DB and log after a run; zero sockets; no new files."""
    seed_world(
        tmp_paths, events=["2026-07-05", "2026-07-08"],
        log_entries=[{"date": "2026-07-05", "status": "ok", "ts": "2026-07-05T12:00:00Z",
                      "total_usd": 0.05, "tiers": ["full"], "warnings": []}],
    )
    db_before = digest(paths.DB_PATH)
    log_before = digest(paths.DATA_DIR / "generation_log.jsonl")
    files_before = sorted(p.name for p in paths.DATA_DIR.rglob("*"))

    out = diagnose.run_diagnose(now_utc=NOW)

    assert "read-only · $0" in out
    assert digest(paths.DB_PATH) == db_before
    assert digest(paths.DATA_DIR / "generation_log.jsonl") == log_before
    assert sorted(p.name for p in paths.DATA_DIR.rglob("*")) == files_before
    assert no_network == []


def test_caveats_travel_with_the_number_every_run(tmp_paths):
    """ADR-0011 D1 drift-guards: all three caveats print inline with the
    falsifier — with data AND on an empty install."""
    seed_world(tmp_paths, events=["2026-07-08"])
    out = diagnose.run_diagnose(now_utc=NOW)
    assert "THE FALSIFIER" in out
    for frag in CAVEAT_FRAGMENTS:
        assert frag in out

    # Empty world: same caveats, no crash.
    (paths.DATA_DIR / "generation_log.jsonl").unlink()
    con = db.connect()
    con.execute("DELETE FROM consumption_events")
    con.commit()
    con.close()
    out2 = diagnose.run_diagnose(now_utc=NOW)
    assert "no consumption events in the window" in out2
    for frag in CAVEAT_FRAGMENTS:
        assert frag in out2


def test_construction_cutover_splits_the_readout(tmp_paths):
    """ADR-0011 D2: events at or before the cutover are construction-period
    (shown, labeled, excluded from the usage readout); after counts."""
    seed_world(tmp_paths, events=["2026-07-05", "2026-07-06", "2026-07-08"])
    out = diagnose.run_diagnose(now_utc=NOW)
    assert "3 distinct open day(s) — 1 in the usage window, 2 construction-period" in out
    assert "2026-07-05: 1 read(s), 0 listen(s)  [construction — not principal reads]" in out
    assert "2026-07-06: 1 read(s), 0 listen(s)  [construction — not principal reads]" in out
    # The boundary day itself is construction (<=); the day after is usage.
    assert "usage-window readout: 1 open day(s) — 2026-07-08" in out


def test_trailing_window_boundaries(tmp_paths):
    """FLIPPED per M8 gate residual 5 (window-definition skew): the
    day-granular window was inclusive both ends (15 calendar days) while
    events.trailing_open_days cuts at a true 14-day timestamp. Now a STRICT
    lower bound — window_start_day < day <= today — the 14 days ending
    today, partial boundary day EXCLUDED: the conservative twin of the
    timestamp cutoff (may undercount vs it, never inflate)."""
    seed_world(tmp_paths, events=[
        "2026-06-26",  # the boundary day (14 back): now OUT (strict bound)
        "2026-06-27",  # oldest in-window day: in
        "2026-06-25",  # 15 back: still out
        "2026-07-09",  # in
    ])
    out = diagnose.run_diagnose(now_utc=NOW)
    assert "2026-06-27:" in out
    assert "2026-06-26" not in out
    assert "2026-06-25" not in out
    assert "2 distinct open day(s)" in out


def test_no_usage_data_message_and_generation_record(tmp_paths):
    seed_world(
        tmp_paths, events=["2026-07-05"],
        log_entries=[
            {"date": "2026-07-05", "status": "ok", "ts": "2026-07-05T12:00:00Z",
             "total_usd": 0.045, "tiers": ["full", "medium"],
             "editor": "editor: 900 -> 700 words (22% tighter)",
             "warnings": ["clustering repair: 1 duplicate item assignment(s) dropped"]},
            {"date": "2026-07-05", "status": "ok", "sample": True,
             "ts": "2026-07-05T13:00:00Z"},
            {"date": "2026-07-05", "status": "failed",
             "ts": "2026-07-05T11:00:00Z", "error": "x"},
        ],
    )
    (paths.DATA_DIR / "generation_log.jsonl").open("a").write("not json\n")
    out = diagnose.run_diagnose(now_utc=NOW)
    assert f"usage-window readout: no data yet — the window opens after {diagnose.CONSTRUCTION_END_UTC}" in out
    assert "record runs 2 (1 ok / 1 failed) · labeled samples 1" in out
    assert "tiers (recorded on 1 of 2 record runs): full 1 · medium 1" in out
    assert "tightening median 22%" in out
    assert "disclosed deterministic repairs (M3 class)" in out
    assert "cannot see value" in out  # the interpretation guardrail closes it


def test_cli_diagnose_wiring(tmp_paths, capsys):
    from newslens import cli

    cli.main(["migrate"])
    capsys.readouterr()
    rc = cli.main(["diagnose"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "THE FALSIFIER" in out
    for frag in CAVEAT_FRAGMENTS:
        assert frag in out
def test_fresh_install_readout_creates_nothing(tmp_paths, no_network):
    """M8 gate residual 1: the verdict instrument must not mutate what it
    measures — on a FRESH install (no DB file, no log) diagnose renders an
    honestly empty readout, creates no database, and touches no files."""
    assert not paths.DB_PATH.exists()
    out = diagnose.run_diagnose(now_utc=NOW)
    assert "THE FALSIFIER" in out
    assert "no consumption events in the window" in out
    for frag in CAVEAT_FRAGMENTS:
        assert frag in out          # caveats travel even with zero data
    assert not paths.DB_PATH.exists()          # measurement created no DB
    assert not (paths.DATA_DIR / "generation_log.jsonl").exists()
    assert no_network == []
