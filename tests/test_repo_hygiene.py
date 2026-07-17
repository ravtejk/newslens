"""Repo hygiene contract (ENGINEERING.md secrets rules; spec §D).

Covers: .env.example carries exactly the spec §D vars plus the B1 provider-
seam vars (ADR-0014) — key-shaped vars EMPTY, guard vars only their documented
non-secret defaults, lane selectors empty, nothing secret-shaped anywhere;
.gitignore actually protects .env / data/ / .venv /
memory.md / audio/ (verified with `git check-ignore`, not by reading the
file); nothing sensitive is git-tracked; scripts/doctor is executable; the
sonar ping prompt is versioned; the test run command is documented.
"""

from __future__ import annotations

import os
import re
import subprocess

import pytest

from conftest import PROTOTYPE_ROOT


def _git(*args):
    return subprocess.run(
        ["git", "-C", str(PROTOTYPE_ROOT), *args],
        capture_output=True,
        text=True,
        timeout=60,
    )


def _env_example_entries():
    entries = {}
    for line in (PROTOTYPE_ROOT / ".env.example").read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        entries[key.strip()] = value.strip()
    return entries


def test_env_example_has_exactly_the_spec_vars_with_no_secret_values():
    entries = _env_example_entries()
    assert entries == {
        "OPENAI_API_KEY": "",
        "PERPLEXITY_API_KEY": "",
        "GNEWS_API_KEY": "",
        # Provider seam (ADR-0014): the Claude API-lane credential — LIVE since
        # B2 (rank/editor/script on Haiku 4.5). Key-shaped, so it stays blank
        # here (agents maintain names only; the principal fills it).
        "ANTHROPIC_API_KEY": "",
        # B1 lane selectors — optional; empty = the gpt-4o api default. Not
        # secret-shaped. Selecting an unimplemented lane fails loud (B1).
        "NEWSLENS_LANE": "",
        "NEWSLENS_LANE_FALLBACK": "",
        # Non-secret guard defaults, exactly as spec §D documents them:
        "BUDGET_CAP_USD_PER_RUN": "0.25",  # M9 ruling 2026-07-06 (was 0.50)
        "GENERATE_HOUR_LOCAL": "6",
    }


def test_setup_md_documents_every_api_key_from_env_example():
    """FIX D (gate, B2): every credential var in .env.example must have a home in
    SETUP.md so no required key — notably ANTHROPIC_API_KEY, live and REQUIRED
    since the B2 Claude-lane flip — is silently unfilled on a fresh install."""
    setup = (PROTOTYPE_ROOT / "SETUP.md").read_text(encoding="utf-8")
    key_vars = [k for k in _env_example_entries() if k.endswith("_API_KEY")]
    assert "ANTHROPIC_API_KEY" in key_vars  # the parser sees it (guards a silent drop)
    missing = [k for k in key_vars if k not in setup]
    assert not missing, f"SETUP.md does not document these keys: {missing}"


def test_env_example_contains_nothing_secret_shaped():
    text = (PROTOTYPE_ROOT / ".env.example").read_text(encoding="utf-8")
    assert not re.search(r"\b(sk|pplx|gsk)-[A-Za-z0-9_\-]{12,}", text)


@pytest.mark.parametrize(
    "path",
    [
        ".env",
        "data/",
        "data/newslens.db",
        ".venv/bin/python",
        "memory.md",
        "audio/briefing-2026-07-02.mp3",
        "src/newslens.egg-info/PKG-INFO",
        "src/newslens/__pycache__/db.cpython-39.pyc",
        ".pytest_cache/CACHEDIR.TAG",
    ],
)
def test_gitignore_protects_secrets_and_local_state(path):
    proc = _git("check-ignore", "-q", path)
    assert proc.returncode == 0, f"{path} is NOT gitignored"


@pytest.mark.parametrize(
    "path",
    [
        "README.md",
        "SETUP.md",
        "sources.yaml",
        ".env.example",
        "pyproject.toml",
        "migrations/0001_initial_schema.sql",
        "migrations/0002_briefings_date_format.sql",
        "prompts/doctor_sonar_ping.txt",
        "prompts/discovery_query.txt",
        "scripts/doctor",
        "scripts/sonar_spike",
        "src/newslens/doctor.py",
        "src/newslens/ingest.py",
        "src/newslens/discovery.py",
        "tests/conftest.py",
    ],
)
def test_gitignore_does_not_swallow_code_or_docs(path):
    proc = _git("check-ignore", "-q", path)
    assert proc.returncode == 1, f"{path} IS gitignored but should be tracked"


def test_git_tracks_nothing_sensitive():
    proc = _git("ls-files")
    assert proc.returncode == 0
    tracked = [l for l in proc.stdout.splitlines() if l.strip()]
    for entry in tracked:
        assert entry != ".env"
        assert not entry.startswith(("data/", ".venv/", "audio/"))
        assert not entry.endswith(".db")


def test_scripts_doctor_is_executable_with_a_python3_shebang():
    script = PROTOTYPE_ROOT / "scripts" / "doctor"
    assert os.access(script, os.X_OK), "scripts/doctor is not executable"
    first_line = script.read_text(encoding="utf-8").splitlines()[0]
    assert first_line == "#!/usr/bin/env python3"


def test_sonar_ping_prompt_is_versioned_and_minimal():
    """Prompts are code (ENGINEERING.md): the doctor's one paid-API prompt
    lives in prompts/, one short line."""
    ping = PROTOTYPE_ROOT / "prompts" / "doctor_sonar_ping.txt"
    content = ping.read_text(encoding="utf-8").strip()
    assert content == "Reply with the single word: ok"
    assert len(content.splitlines()) == 1


def test_readme_documents_the_one_command_test_run():
    readme = (PROTOTYPE_ROOT / "README.md").read_text(encoding="utf-8")
    assert "pytest" in readme  # suite runs with one documented command


def test_pyproject_declares_the_39_floor_and_the_entry_point():
    pyproject = (PROTOTYPE_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert 'requires-python = ">=3.9"' in pyproject
    assert 'newslens = "newslens.cli:main"' in pyproject


def test_pyproject_declares_the_m2_feedparser_dependency():
    """feedparser is M2's one new dependency (ADR-0003 §4, escalation-listed,
    principal-approved). It must be declared where installer and reviewer
    expect it."""
    pyproject = (PROTOTYPE_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert "feedparser" in pyproject


def test_env_file_is_never_tracked_by_git():
    """A real .env now exists locally AND this repo has a GitHub remote —
    this pin is the tripwire that keeps the key out of history: .env must be
    ignored and must never appear in the index."""
    assert _git("check-ignore", "-q", ".env").returncode == 0
    ls = _git("ls-files", "--", ".env")
    assert ls.stdout.strip() == ""
