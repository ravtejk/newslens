"""Doctor contract (ADR-0002; doctor.py exit-code contract; spec §E M1).

All network-shaped checks run against a local fake API on 127.0.0.1 — this
suite never touches a real endpoint and never needs (or accepts) a real key.
The headline claims verified mechanically here:

  * keyless + template sources -> exit 1, exact fix-hint per missing key,
    ZERO network attempts (socket-level recorder, not code reading);
  * exit 0 with warnings allowed once everything required passes;
  * 401 / 5xx / unreachable paths produce the documented friendly lines;
  * the scratch-DB migration check reports the four spec §B tables and the
    doctor never creates the real DB;
  * secrets are never echoed.

KNOWN-RED: test_BUG2_* documents that an unreadable sources.yaml crashes the
doctor with a raw PermissionError traceback, violating its own "friendly
report line, never a traceback" contract.
"""

from __future__ import annotations

import os
import sqlite3
import sys
import types

import pytest

from newslens import config, db, doctor, paths

OPENAI_HINT = (
    "OPENAI_API_KEY not set — get one at platform.openai.com/api-keys, "
    "then add to .env"
)
PERPLEXITY_HINT = (
    "PERPLEXITY_API_KEY not set — deferred by choice; ingest runs RSS-only "
    "and says so. To add discovery later: perplexity.ai/settings/api → .env"
)  # M8 ruling: deferred-by-principal-choice = ○ informational, not ✗ required
SCRATCH_TABLES_LINE = (
    "migrations apply cleanly to a scratch DB — tables: "
    "analysis_briefs, analysis_retrieval, briefings, briefings_history, concept_explanations, consumption_events, memory, ranking_runs, source_items"
)


def run_doctor_captured(capsys):
    code = doctor.run_doctor()
    return code, capsys.readouterr().out


# --- the headline claim: keyless + template = exit 1, friendly, zero network ---

def test_keyless_template_run_exits_1_with_fix_hints_and_zero_network(
    tmp_paths, no_network, capsys
):
    code, out = run_doctor_captured(capsys)

    assert code == 1
    assert OPENAI_HINT in out
    assert PERPLEXITY_HINT in out
    assert ".env not found — run: cp .env.example .env" in out
    assert config.NO_ACTIVE_SOURCES_MSG in out
    assert "Doctor exit 1 — fix the ✗ lines above" in out

    # Mechanical zero-network: not one DNS lookup or connect was attempted.
    assert no_network == []
    # And the keys were never *attempted* (an attempted-but-blocked call would
    # surface as a caught "could not reach" line instead of "not set").
    assert "could not reach" not in out
    assert "Traceback" not in out
    # GNews stays informational, never a required failure.
    assert "○ GNEWS_API_KEY not set" in out
    # Unset guards report their documented defaults.
    assert "default 0.25" in out  # M9 ruling 2026-07-06
    assert "default 6" in out


def test_keyless_run_reports_scratch_migration_with_the_four_spec_tables(
    tmp_paths, no_network, capsys
):
    _, out = run_doctor_captured(capsys)
    assert SCRATCH_TABLES_LINE in out


def test_doctor_does_not_create_the_real_db_and_cleans_its_write_probe(
    tmp_paths, no_network, capsys
):
    _, out = run_doctor_captured(capsys)
    assert "not created yet — run: newslens migrate" in out
    assert not paths.DB_PATH.exists()  # health check must not create state
    assert list(paths.DATA_DIR.iterdir()) == []  # write probe removed


# --- database states ------------------------------------------------------------

def test_doctor_reports_migrated_db_up_to_date(tmp_paths, no_network, capsys):
    db.migrate()  # sandboxed DB_PATH via tmp_paths
    _, out = run_doctor_captured(capsys)
    assert "present and up to date" in out


def test_doctor_flags_a_db_behind_on_migrations(tmp_paths, no_network, capsys):
    paths.DATA_DIR.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(paths.DB_PATH))
    con.execute("CREATE TABLE placeholder (x)")
    con.commit()
    con.close()
    code, out = run_doctor_captured(capsys)
    assert code == 1
    assert (
        "behind by 16 migration(s) (0001_initial_schema.sql, "
        "0002_briefings_date_format.sql, 0003_ranking_runs.sql, "
        "0004_ranking_runs_append_only.sql, 0005_memory_topic_unique.sql, "
        "0006_memory_lifecycle_v2.sql, 0007_consumption_events.sql, "
        "0008_analysis_briefs.sql, 0009_analysis_append_only_and_retrieval.sql, "
        "0010_thread_memory.sql, 0011_consumption_view_events.sql, "
        "0012_thread_delta_supersession.sql, 0013_watch_items.sql, "
        "0014_thread_delta_provenance.sql, 0015_thread_closures.sql, "
        "0016_concept_explanations.sql)"
    ) in out
    assert "run: newslens migrate" in out


def test_doctor_flags_a_corrupt_db_file_without_crashing(tmp_paths, no_network, capsys):
    paths.DATA_DIR.mkdir(parents=True, exist_ok=True)
    paths.DB_PATH.write_bytes(b"this is definitely not a sqlite file")
    code, out = run_doctor_captured(capsys)
    assert code == 1
    assert "exists but is unreadable" in out
    assert "Traceback" not in out


def test_scratch_migration_failure_is_reported_as_broken_schema(monkeypatch):
    def broken(*args, **kwargs):
        raise RuntimeError("kaboom")

    monkeypatch.setattr(db, "migrate", broken)
    results = doctor.check_database()
    assert len(results) == 1
    assert results[0].status == doctor.FAIL
    assert "schema itself is broken" in results[0].text
    assert "kaboom" in results[0].text


# --- key checks against the local fake API ---------------------------------------

def test_openai_check_passes_with_a_valid_key(fake_api, monkeypatch):
    monkeypatch.setattr(doctor, "OPENAI_MODELS_URL", fake_api.base_url + "/v1/models")
    results = doctor.check_openai_key({"OPENAI_API_KEY": fake_api.good_key})
    assert [r.status for r in results] == [doctor.PASS]
    assert "read-only GET /v1/models OK" in results[0].text
    assert "2 models visible" in results[0].text
    get = [r for r in fake_api.recorded if r["method"] == "GET"]
    assert get and get[0]["user_agent"].startswith("NewsLens-doctor/")


def test_openai_check_401_names_the_fix(fake_api, monkeypatch):
    monkeypatch.setattr(doctor, "OPENAI_MODELS_URL", fake_api.base_url + "/v1/models")
    results = doctor.check_openai_key({"OPENAI_API_KEY": "sk-wrong"})
    assert [r.status for r in results] == [doctor.FAIL]
    assert "rejected (401)" in results[0].text
    assert "regenerate" in results[0].text


def test_openai_check_5xx_is_a_distinct_friendly_failure(fake_api, monkeypatch):
    monkeypatch.setattr(doctor, "OPENAI_MODELS_URL", fake_api.base_url + "/boom")
    results = doctor.check_openai_key({"OPENAI_API_KEY": "sk-any"})
    assert [r.status for r in results] == [doctor.FAIL]
    assert "HTTP 500" in results[0].text


def test_openai_check_unreachable_is_network_shaped_not_a_crash(fake_api, monkeypatch):
    monkeypatch.setattr(doctor, "OPENAI_MODELS_URL", fake_api.dead_url("/v1/models"))
    results = doctor.check_openai_key({"OPENAI_API_KEY": "sk-any"})
    assert [r.status for r in results] == [doctor.FAIL]
    assert "could not reach" in results[0].text


def test_perplexity_check_passes_and_sends_the_versioned_minimal_ping(
    fake_api, monkeypatch
):
    monkeypatch.setattr(
        doctor, "PERPLEXITY_CHAT_URL", fake_api.base_url + "/chat/completions"
    )
    results = doctor.check_perplexity_key({"PERPLEXITY_API_KEY": fake_api.good_key})
    assert [r.status for r in results] == [doctor.PASS]
    assert "minimal sonar query OK" in results[0].text

    posts = [r for r in fake_api.recorded if r["method"] == "POST"]
    assert len(posts) == 1
    body = posts[0]["body"]
    assert body["model"] == "sonar"
    # minimal by construction — but 16 is Perplexity's enforced floor (400
    # below it, found live 2026-07-06); "as small as the API allows".
    assert body["max_tokens"] == 16
    ping = (paths.PROMPTS_DIR / "doctor_sonar_ping.txt").read_text(encoding="utf-8")
    assert body["messages"] == [{"role": "user", "content": ping.strip()}]


def test_perplexity_check_401_names_the_fix(fake_api, monkeypatch):
    monkeypatch.setattr(
        doctor, "PERPLEXITY_CHAT_URL", fake_api.base_url + "/chat/completions"
    )
    results = doctor.check_perplexity_key({"PERPLEXITY_API_KEY": "pplx-wrong"})
    assert [r.status for r in results] == [doctor.FAIL]
    assert "rejected (401)" in results[0].text


def test_perplexity_missing_ping_file_fails_before_any_network(
    tmp_path, no_network, monkeypatch
):
    monkeypatch.setattr(paths, "PROMPTS_DIR", tmp_path / "empty-prompts")
    results = doctor.check_perplexity_key({"PERPLEXITY_API_KEY": "pplx-any"})
    assert [r.status for r in results] == [doctor.FAIL]
    assert "checkout is incomplete" in results[0].text
    assert no_network == []  # never built the request


# --- the exit-0 contract -----------------------------------------------------------

def test_exit_0_with_warnings_once_everything_required_passes(
    fake_api, tmp_paths, monkeypatch, capsys
):
    """Exit 0 = ready for a real run, warnings allowed: keys validate against
    the (fake) endpoints, DB migrated, guards defaulted — while the template
    sources still produce ⚠ lines."""
    monkeypatch.setattr(doctor, "OPENAI_MODELS_URL", fake_api.base_url + "/v1/models")
    monkeypatch.setattr(
        doctor, "PERPLEXITY_CHAT_URL", fake_api.base_url + "/chat/completions"
    )
    monkeypatch.setenv("OPENAI_API_KEY", fake_api.good_key)
    monkeypatch.setenv("PERPLEXITY_API_KEY", fake_api.good_key)
    db.migrate()  # sandboxed
    # M6: the TTS engine is REQUIRED (listening-primary product). Fake its
    # presence in the sandbox and skip the real synthesis via the DISCLOSED
    # marker (QA ruling: accepted BECAUSE the skip renders an INFO line and
    # never masks engine absence — both pinned in test_audio.py).
    # P3.1 item 4 flip (mechanical, intended): the synth-skip machinery is
    # kokoro's; with the default now openai, pin kokoro to keep exercising
    # it — which also matches the principal's real install (his sources.yaml
    # pins kokoro; the recommended-default nudge is a non-blocking WARN).
    paths.SOURCES_FILE.write_text(
        paths.SOURCES_FILE.read_text(encoding="utf-8")
        + "settings:\n  tts_engine: kokoro\n",
        encoding="utf-8",
    )
    venv_py = paths.DATA_DIR / "tts" / "venv" / "bin" / "python"
    venv_py.parent.mkdir(parents=True, exist_ok=True)
    venv_py.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    (paths.DATA_DIR / "tts" / "kokoro-v1.0.onnx").write_bytes(b"fake")
    (paths.DATA_DIR / "tts" / "voices-v1.0.bin").write_bytes(b"fake")
    monkeypatch.setenv("NEWSLENS_DOCTOR_TTS_SYNTH", "0")

    code, out = run_doctor_captured(capsys)

    assert code == 0
    assert "tts real-synthesis check skipped" in out  # the disclosed marker
    assert "Doctor exit 0 — everything required passes; the ⚠ lines are worth a look." in out
    assert "0 required failing" in out
    assert config.NO_ACTIVE_SOURCES_MSG in out  # warnings present, not blocking
    # Exactly the two key validations hit the network — nothing else.
    assert [(r["method"], r["path"]) for r in fake_api.recorded] == [
        ("GET", "/v1/models"),
        ("POST", "/chat/completions"),
    ]


def test_doctor_never_echoes_a_key_value(fake_api, tmp_paths, monkeypatch, capsys):
    canary = fake_api.good_key  # secret-shaped: "sk-qa-local-fake-good-key-0000"
    monkeypatch.setattr(doctor, "OPENAI_MODELS_URL", fake_api.base_url + "/v1/models")
    monkeypatch.setattr(
        doctor, "PERPLEXITY_CHAT_URL", fake_api.base_url + "/chat/completions"
    )
    monkeypatch.setenv("OPENAI_API_KEY", canary)
    monkeypatch.setenv("PERPLEXITY_API_KEY", canary)
    _, out = run_doctor_captured(capsys)
    assert canary not in out


# --- environment checks --------------------------------------------------------------

def test_check_python_passes_on_39_and_fails_below(monkeypatch):
    monkeypatch.setattr(sys, "version_info", types.SimpleNamespace(major=3, minor=9, micro=6))
    assert doctor.check_python()[0].status == doctor.PASS

    monkeypatch.setattr(sys, "version_info", types.SimpleNamespace(major=3, minor=8, micro=17))
    result = doctor.check_python()[0]
    assert result.status == doctor.FAIL
    assert "too old" in result.text


def test_check_deps_passes_in_the_installed_venv():
    results = doctor.check_deps()
    assert [r.status for r in results] == [doctor.PASS]


def test_env_file_values_load_but_real_environment_wins(tmp_paths, monkeypatch):
    paths.ENV_FILE.write_text(
        "OPENAI_API_KEY=from-file\nBUDGET_CAP_USD_PER_RUN=0.99\n", encoding="utf-8"
    )
    monkeypatch.setenv("OPENAI_API_KEY", "from-process")
    env, notes = doctor.load_effective_env()
    assert env["OPENAI_API_KEY"] == "from-process"  # process wins
    assert env["BUDGET_CAP_USD_PER_RUN"] == "0.99"  # file fills the gaps
    assert any(".env found" in n.text for n in notes)
    assert "OPENAI_API_KEY" not in os.environ or os.environ["OPENAI_API_KEY"] == "from-process"


def test_fallback_env_parser_handles_the_documented_shapes(tmp_path):
    p = tmp_path / ".env"
    p.write_text(
        "# comment line\n"
        "\n"
        "export EXPORTED=one\n"
        "SINGLE='quoted value'\n"
        'DOUBLE="also quoted"\n'
        "PLAIN=bare\n"
        "NOEQUALS\n"
        "EMPTY=\n",
        encoding="utf-8",
    )
    values = doctor._parse_env_fallback(p)
    assert values == {
        "EXPORTED": "one",
        "SINGLE": "quoted value",
        "DOUBLE": "also quoted",
        "PLAIN": "bare",
        "EMPTY": "",
    }


# --- sources rendering through the doctor ---------------------------------------------

def test_doctor_renders_malformed_yaml_as_a_friendly_failure(tmp_paths, no_network):
    paths.SOURCES_FILE.write_text("sources: [unclosed\n  - what", encoding="utf-8")
    results = doctor.check_sources()
    assert [r.status for r in results] == [doctor.FAIL]
    assert "not valid YAML" in results[0].text
    assert "fix sources.yaml" in results[0].text


def test_doctor_renders_format_problems_as_failures(tmp_paths, no_network):
    paths.SOURCES_FILE.write_text("sauces:\n  - name: A\n", encoding="utf-8")
    results = doctor.check_sources()
    fails = [r for r in results if r.status == doctor.FAIL]
    assert any("unknown top-level key `sauces`" in r.text for r in fails)


def test_doctor_missing_sources_file_is_a_failure_not_a_crash(tmp_paths, no_network):
    paths.SOURCES_FILE.unlink()
    results = doctor.check_sources()
    assert [r.status for r in results] == [doctor.FAIL]
    assert "sources.yaml is missing" in results[0].text


def test_BUG2_unreadable_sources_yaml_must_be_friendly_not_a_traceback(tmp_paths):
    """KNOWN-RED (BUG-2): the doctor's contract is 'a friendly report line,
    never a traceback' (module docstring; ADR-0002). An unreadable
    sources.yaml currently raises PermissionError straight through
    check_sources, which would crash run_doctor mid-report."""
    if os.geteuid() == 0:
        pytest.skip("running as root — chmod 000 is still readable")
    paths.SOURCES_FILE.chmod(0)
    try:
        results = doctor.check_sources()  # must not raise
    finally:
        paths.SOURCES_FILE.chmod(0o600)
    assert any(r.status == doctor.FAIL for r in results)


# --- feed checks, fully offline ---------------------------------------------------------

def test_feed_check_passes_on_a_real_feed_shape(fake_api):
    src = config.Source(name="QA Feed", rss_url=fake_api.base_url + "/feed.xml")
    results = doctor.check_feed_urls([src])
    assert [r.status for r in results] == [doctor.PASS]
    assert "feed resolves: QA Feed" in results[0].text


def test_feed_check_warns_when_url_is_not_a_feed(fake_api):
    src = config.Source(name="Homepage", rss_url=fake_api.base_url + "/page.html")
    results = doctor.check_feed_urls([src])
    assert [r.status for r in results] == [doctor.WARN]
    assert "does not look like an RSS/Atom feed" in results[0].text


def test_feed_check_fails_friendly_on_unreachable_url(fake_api):
    src = config.Source(name="Gone", rss_url=fake_api.dead_url("/feed.xml"))
    results = doctor.check_feed_urls([src])
    assert [r.status for r in results] == [doctor.FAIL]
    assert "failed to resolve" in results[0].text


def test_doctor_with_one_active_source_checks_exactly_that_feed(fake_api, tmp_paths):
    paths.SOURCES_FILE.write_text(
        f"sources:\n  - name: QA Feed\n    rss_url: {fake_api.base_url}/feed.xml\n",
        encoding="utf-8",
    )
    results = doctor.check_sources()
    texts = [r.text for r in results]
    assert any("1 active source(s) configured" in t for t in texts)
    assert any("feed resolves: QA Feed" in t for t in texts)
    assert any(config.NO_INTERESTS_MSG in t for t in texts)  # still nudges interests
    feed_hits = [r for r in fake_api.recorded if r["path"] == "/feed.xml"]
    assert len(feed_hits) == 1  # one configured feed -> exactly one GET
