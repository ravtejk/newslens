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

from conftest import PROTOTYPE_ROOT


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


@pytest.mark.parametrize("not_yet", ["generate", "read", "listen"])
def test_future_pipeline_verbs_do_not_exist_yet(not_yet, capsys):
    """M5/M7 verbs must not be stubbed ('an unimplemented command should not
    exist yet rather than exist and lie' — cli.py docstring)."""
    with pytest.raises(SystemExit) as excinfo:
        cli.main([not_yet])
    assert excinfo.value.code == 2


def test_migrate_applies_then_reports_already_up_to_date(tmp_paths, capsys):
    rc = cli.main(["migrate"])
    out_first = capsys.readouterr().out
    assert rc == 0
    assert "applied 1 migration(s): 0001_initial_schema.sql" in out_first
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
