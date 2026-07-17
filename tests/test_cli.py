"""CLI contract (cli.py): exactly two commands in milestone 1, honest exits.

Covers: --version; usage errors exit 2; `migrate` applies then reports
idempotent no-op (the documented CLI-level re-run behavior); a migrate failure
is loud on stderr with exit 1; `doctor` dispatches to run_doctor; the venv
entry point actually exists and runs.
"""

from __future__ import annotations

import subprocess

import pytest

from newslens import cli, db, paths

from conftest import PROTOTYPE_ROOT, anthropic_envelope


def test_version_flag(capsys):
    with pytest.raises(SystemExit) as excinfo:
        cli.main(["--version"])
    assert excinfo.value.code == 0
    assert capsys.readouterr().out.strip() == "newslens 0.1.0"


def test_no_command_is_a_usage_error(capsys):
    with pytest.raises(SystemExit) as excinfo:
        cli.main([])
    assert excinfo.value.code == 2
    assert "usage:" in capsys.readouterr().err


def test_unknown_command_is_a_usage_error(capsys):
    with pytest.raises(SystemExit) as excinfo:
        cli.main(["frobnicate"])
    assert excinfo.value.code == 2


@pytest.mark.parametrize("not_yet", ["read", "listen"])
def test_future_pipeline_verbs_do_not_exist_yet(not_yet, capsys):
    """M7 verbs must not be stubbed ('an unimplemented command should not
    exist yet rather than exist and lie' — cli.py docstring).

    POSTMORTEM (M5): `generate` graduated out of this list. While stale, this
    pin EXECUTED the real pipeline un-sandboxed on every suite run (live
    ingest + a paid rank call), because the test carried no fixtures and
    paths.ENV_FILE still pointed at the real .env. Two autouse conftest
    guards now make that class impossible (sandbox_paths +
    loopback_only_network); when `read`/`listen` become real, delete them
    here and pin their actual behavior — the guards will contain any lag."""
    with pytest.raises(SystemExit) as excinfo:
        cli.main([not_yet])
    assert excinfo.value.code == 2


def test_migrate_applies_then_reports_already_up_to_date(tmp_paths, capsys):
    rc = cli.main(["migrate"])
    out_first = capsys.readouterr().out
    assert rc == 0
    assert (
        "applied 17 migration(s): 0001_initial_schema.sql, "
        "0002_briefings_date_format.sql, 0003_ranking_runs.sql, "
        "0004_ranking_runs_append_only.sql, 0005_memory_topic_unique.sql, "
        "0006_memory_lifecycle_v2.sql, 0007_consumption_events.sql, "
        "0008_analysis_briefs.sql, 0009_analysis_append_only_and_retrieval.sql, "
        "0010_thread_memory.sql, 0011_consumption_view_events.sql, "
        "0012_thread_delta_supersession.sql, 0013_watch_items.sql, "
        "0014_thread_delta_provenance.sql, 0015_thread_closures.sql, "
        "0016_concept_explanations.sql, 0017_thread_baselines.sql"
    ) in out_first
    assert str(paths.DB_PATH) in out_first

    rc = cli.main(["migrate"])
    out_second = capsys.readouterr().out
    assert rc == 0
    assert "database already up to date — nothing to apply" in out_second


def test_migrate_failure_is_loud_on_stderr_and_nonzero(monkeypatch, capsys):
    def broken(*args, **kwargs):
        raise RuntimeError("disk on fire")

    monkeypatch.setattr(db, "migrate", broken)
    rc = cli.main(["migrate"])
    captured = capsys.readouterr()
    assert rc == 1
    assert "migrate failed: RuntimeError: disk on fire" in captured.err


def test_doctor_subcommand_dispatches_to_run_doctor(monkeypatch):
    from newslens import doctor

    monkeypatch.setattr(doctor, "run_doctor", lambda: 42)
    assert cli.main(["doctor"]) == 42


def test_venv_entry_point_is_installed_and_runs():
    exe = PROTOTYPE_ROOT / ".venv" / "bin" / "newslens"
    if not exe.exists():
        pytest.skip("venv entry point missing — recreate the venv per SETUP.md")
    proc = subprocess.run(
        [str(exe), "--version"], capture_output=True, text=True, timeout=60
    )
    assert proc.returncode == 0
    assert proc.stdout.strip() == "newslens 0.1.0"


# --- newslens ingest (milestone 2) ---------------------------------------------

def test_ingest_refuses_politely_on_template_sources(tmp_paths, capsys):
    """The polite refusal reaches the CLI verbatim, exit 1."""
    rc = cli.main(["ingest", "--no-discovery"])
    captured = capsys.readouterr()
    assert rc == 1
    assert captured.err.strip() == (
        "sources.yaml has no active sources — uncomment or add your outlets"
    )


def test_ingest_happy_path_reports_counts_and_discovery_state(
    tmp_paths, fake_api, capsys
):
    from conftest import make_rss

    url = fake_api.add_route(
        "/cli.xml",
        body=make_rss(
            [
                {"title": "S1", "url": "https://x.example/s1"},
                {"title": "S2", "url": "https://x.example/s2"},
            ]
        ),
    )
    paths.SOURCES_FILE.write_text(
        f"sources:\n  - name: CLI Feed\n    rss_url: {url}\n", encoding="utf-8"
    )
    rc = cli.main(["ingest", "--no-discovery"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "ingest: 1 of 1 sources ok — 2 new item(s), 0 updated, 0 skipped" in out
    assert "discovery: not attempted" in out
    # The sandboxed DB actually has the rows (CLI wired end to end).
    from newslens import db

    con = db.connect()
    try:
        n = con.execute("SELECT COUNT(*) FROM source_items").fetchone()[0]
    finally:
        con.close()
    assert n == 2


def test_ingest_all_sources_down_is_exit_1_with_degradation_detail(
    tmp_paths, fake_api, capsys
):
    paths.SOURCES_FILE.write_text(
        f"sources:\n  - name: Down\n    rss_url: {fake_api.dead_url('/x.xml')}\n",
        encoding="utf-8",
    )
    rc = cli.main(["ingest", "--no-discovery"])
    captured = capsys.readouterr()
    assert rc == 1
    assert "no source could be fetched this run" in captured.err
    assert "1 of 1 sources unavailable this run" in captured.out
    assert "✗ Down:" in captured.out


# --- newslens rank (milestone 3) --------------------------------------------------

INTERESTED_SOURCES = (
    "sources:\n"
    "  - name: Outlet A\n"
    "    rss_url: https://a.invalid/feed\n"
    "interests:\n"
    "  granular:\n"
    "    - AI regulation\n"
)


def test_rank_rejects_malformed_date_fast(capsys):
    rc = cli.main(["rank", "--date", "2026-7-4"])
    assert rc == 2
    assert "--date must be YYYY-MM-DD" in capsys.readouterr().err


@pytest.mark.parametrize("bad", ["2026-13-01", "2026-02-30", "2026-00-10"])
def test_rank_rejects_calendar_nonsense_dates(capsys, bad):
    """M4 item-12 batch: shape first (strict zero-padding), then strptime for
    calendar truth — month 13 and Feb 30 must exit 2, not reach the pipeline."""
    rc = cli.main(["rank", "--date", bad])
    assert rc == 2
    assert "real calendar date" in capsys.readouterr().err


# --- newslens memory verbs (milestone 4) --------------------------------------

def _memory_world(monkeypatch, tmp_path):
    from newslens import paths as _paths

    memfile = tmp_path / "memory.md"
    monkeypatch.setattr(_paths, "MEMORY_FILE", memfile)
    return memfile


def test_memory_add_survives_its_own_command(tmp_paths, capsys):
    """M4 amendment regression pin (cli.py render-only refresh): the trailing
    sync used to re-read the file written by the OPENING sync and file-wins
    would dismiss the fresh add instantly. A fresh `memory add` must exist,
    active, after its own command AND after the next command's sync."""
    from newslens import db, paths as _paths

    cli.main(["migrate"])
    capsys.readouterr()
    rc = cli.main(["memory", "add", "Fresh Topic"])
    out = capsys.readouterr().out
    assert rc == 0 and "now tracking 'Fresh Topic'" in out

    con = db.connect()
    try:
        row = con.execute(
            "SELECT status FROM memory WHERE topic = 'Fresh Topic'"
        ).fetchone()
    finally:
        con.close()
    assert row is not None and row["status"] == "active"
    text = _paths.MEMORY_FILE.read_text(encoding="utf-8")
    assert "Fresh Topic" in text.split("## Inactive")[0]

    # The NEXT command's opening sync must also leave it alone.
    rc = cli.main(["memory", "list"])
    out = capsys.readouterr().out
    assert rc == 0 and "Fresh Topic" in out
    con = db.connect()
    try:
        status = con.execute(
            "SELECT status FROM memory WHERE topic = 'Fresh Topic'"
        ).fetchone()["status"]
    finally:
        con.close()
    assert status == "active"


def test_memory_verbs_sync_hand_edits_before_acting(tmp_paths, capsys):
    from newslens import db, paths as _paths

    cli.main(["migrate"])
    capsys.readouterr()
    cli.main(["memory", "add", "Existing"])
    capsys.readouterr()
    # Hand-edit the file: add a new line the DB has never seen.
    text = _paths.MEMORY_FILE.read_text(encoding="utf-8")
    _paths.MEMORY_FILE.write_text(
        text.replace("- Existing", "- Existing\n- Hand Added — from the editor"),
        encoding="utf-8",
    )
    rc = cli.main(["memory", "list"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "Hand Added" in out  # opening sync landed the hand edit first
    con = db.connect()
    try:
        row = con.execute(
            "SELECT status, principal_note FROM memory WHERE topic = 'Hand Added'"
        ).fetchone()
    finally:
        con.close()
    assert row["status"] == "active" and row["principal_note"] == "from the editor"


def test_memory_dismiss_and_add_revive_cycle(tmp_paths, capsys):
    from newslens import db

    cli.main(["migrate"])
    cli.main(["memory", "add", "Cycling"])
    capsys.readouterr()
    rc = cli.main(["memory", "dismiss", "Cycling"])
    out = capsys.readouterr().out
    assert rc == 0 and "never auto-revives" in out
    con = db.connect()
    try:
        assert con.execute(
            "SELECT status FROM memory WHERE topic='Cycling'"
        ).fetchone()["status"] == "dismissed_user"
    finally:
        con.close()
    rc = cli.main(["memory", "add", "cycling"])  # case-insensitive revive
    out = capsys.readouterr().out
    # (The verb echoes the argument's casing; the DB row keeps its own.)
    assert rc == 0 and "revived" in out and "(was dismissed_user)" in out
    con = db.connect()
    try:
        assert con.execute(
            "SELECT status FROM memory WHERE topic='Cycling'"
        ).fetchone()["status"] == "active"
    finally:
        con.close()


def test_memory_add_rejects_the_separator(tmp_paths, capsys):
    cli.main(["migrate"])
    capsys.readouterr()
    rc = cli.main(["memory", "add", "bad — topic"])
    assert rc == 2
    assert "may not contain" in capsys.readouterr().err


def test_gatefix1b_memory_add_revival_survives_the_next_sync(tmp_paths, capsys):
    """GATE-FIX PIN 1b: `memory add` on a long-dormant thread sets a fresh
    status_changed_at; the next sync's dormancy pass must respect it (basis
    includes the transition date) instead of instantly re-dormanting a
    thread that is, by definition, >14d unreferenced."""
    from datetime import datetime, timedelta, timezone

    from newslens import db, memory as mem

    cli.main(["migrate"])
    capsys.readouterr()
    old = (datetime.now(timezone.utc) - timedelta(days=60)).strftime(
        "%Y-%m-%dT%H:%M:%S.000Z"
    )
    older = (datetime.now(timezone.utc) - timedelta(days=30)).strftime(
        "%Y-%m-%dT%H:%M:%S.000Z"
    )
    con = db.connect()
    try:
        con.execute(
            "INSERT INTO memory (topic, status, status_changed_at,"
            " created_at, updated_at) VALUES"
            " ('Long Sleeper', 'dormant', ?, ?, ?)", (older, old, old),
        )
        con.commit()
    finally:
        con.close()

    rc = cli.main(["memory", "add", "Long Sleeper"])
    out = capsys.readouterr().out
    assert rc == 0 and "revived" in out

    # The NEXT command runs a full sync-first — the revival must hold.
    con = db.connect()
    try:
        result = mem.sync_memory(con)
        status = con.execute(
            "SELECT status FROM memory WHERE topic = 'Long Sleeper'"
        ).fetchone()["status"]
    finally:
        con.close()
    assert "Long Sleeper" not in result.went_dormant
    assert status == "active"


def test_rank_keyless_openai_rides_the_anthropic_seat_exit_1(
    tmp_paths, fake_api, monkeypatch, capsys
):
    """A″ (2026-07-17, CONSCIOUS FLIP): rank is anthropic since B2 — keyless-OpenAI
    does NOT refuse on the inert OpenAI key. It rides the anthropic seat (the
    sandbox stub `claude` on the subscription lane) and exits 1 for a real reason,
    NOT OPENAI_API_KEY. No POST to the OpenAI endpoint is ever built (the
    subscription lane is a subprocess)."""
    from newslens import ranking

    monkeypatch.setattr(
        ranking, "OPENAI_CHAT_URL", fake_api.base_url + "/chat/completions"
    )
    paths.SOURCES_FILE.write_text(INTERESTED_SOURCES, encoding="utf-8")
    rc = cli.main(["rank"])
    err = capsys.readouterr().err
    assert rc == 1
    assert "OPENAI_API_KEY" not in err          # the stale OpenAI refusal is gone
    assert [r for r in fake_api.recorded if r["method"] == "POST"] == []


def test_rank_happy_path_prints_window_caveat_override_and_cost(
    tmp_paths, fake_api, monkeypatch, capsys
):
    import json as _json

    from newslens import db, llm, ranking

    # B2 fake migration: `newslens rank` transports the rank seat on the
    # Claude API lane — same in-process env/module seams the pipeline reads.
    monkeypatch.setattr(
        ranking, "OPENAI_CHAT_URL", fake_api.base_url + "/chat/completions"
    )
    monkeypatch.setattr(
        llm, "ANTHROPIC_MESSAGES_URL", fake_api.base_url + "/v1/messages"
    )
    monkeypatch.setenv("ANTHROPIC_API_KEY", fake_api.good_key)
    monkeypatch.setenv("NEWSLENS_LANE_RANK", "api")  # B3: rank exercises the api fall-over here
    monkeypatch.setenv("OPENAI_API_KEY", "sk-qa-fake-cli")
    paths.SOURCES_FILE.write_text(INTERESTED_SOURCES, encoding="utf-8")
    db.migrate()
    con = db.connect()
    try:
        from datetime import datetime, timezone

        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")
        con.execute(
            "INSERT INTO source_items (id, source_type, outlet, url, title, fetched_at)"
            " VALUES (1, 'rss', 'Outlet A', 'https://a.invalid/1', 'Story', ?)",
            (now,),
        )
        con.commit()
    finally:
        con.close()
    payload = {
        "clusters": [
            {
                "story_title": "Tagged story",
                "summary": "Matched.",
                "item_ids": [1],
                "matched_tags": [{"name": "AI regulation", "level": "topic"}],
                "matched_memory": [],
                "world_impact": 6,
                "world_impact_reason": "Wide effect",
            }
        ]
    }
    fake_api.add_route(
        "/v1/messages",
        status=200,
        body=anthropic_envelope(payload, input_tokens=900, output_tokens=150),
        content_type="application/json",
    )
    rc = cli.main(["rank", "--date", "2026-07-04"])
    out = capsys.readouterr().out
    assert rc == 0
    assert (
        f"story budget for 2026-07-04 — 1 of {ranking.MAX_SLOTS} slots filled" in out
    )  # M2 contract constant, not a hardcoded 5 (stale-display fix, 2026-07-14)
    assert "candidate window:" in out and "ingested history:" in out  # honesty line
    assert "Note: Corroboration counts distinct outlets" in out       # standing caveat
    assert "[Reported by 1 named outlet]" in out
    assert "cost:" in out and "ranking_runs" in out
