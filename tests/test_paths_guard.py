"""The real-paths guard (M3 gate FIX-5, incident 2026-07-14).

An ad-hoc render-proof script imported newslens outside pytest, resolved
paths.DATA_DIR to the real data/ directory, and clobbered the operational
generation_log.jsonl — the second recurrence of the class the 2026-07-07
ENGINEERING.md rule ("no real-state writes during probing") was written for.
A procedure that fails twice needs a mechanism: DATA_DIR/DB_PATH are now
PEP 562 module attributes that resolve a NEWSLENS_DATA_DIR/NEWSLENS_DB_PATH
env override when one is set (redirection outranks sanction — an overridden
location is not real state), and otherwise REFUSE unless the process is
sanctioned — (a) a real entrypoint called paths.allow_real_paths()
(cli.main / doctor.main), or (b) NEWSLENS_REAL_DATA=1 is set explicitly
(the conscious, transcript-greppable one-off opt-in).

There is deliberately NO pytest arm: the original guard sanctioned any
process with PYTEST_CURRENT_TEST in its environment, and children spawned
by tests INHERIT that variable — the v7-M1 pinhole (2026-07-14): the
suite's doctor child probed the real data/ directory with the guard's
blessing. Under pytest the conftest sandbox provides both the module-dict
shadow and the env overrides; nothing legitimate needs a pytest arm.

HONEST LIMIT (documented in the refusal message and README): a script that
hardcodes the 'data/...' path string bypasses this guard entirely. The
probe-discipline rule remains law; this mechanism just makes the EASY
mistake — importing newslens and writing through paths — impossible to
make silently.

These are subprocess liveness tests: the child env is scrubbed of both
sanction variables, so the only way test 1/2 pass is the guard refusing,
and the only way test 3 passes is allow_real_paths() existing and working.
Born red (guard not yet implemented), flipped by the guard alone.
"""

from __future__ import annotations

import os
import subprocess
import sys

from newslens import paths

CHECKOUT = str(paths.PROJECT_ROOT)


def _run(code: str, extra_env: dict | None = None):
    env = {k: v for k, v in os.environ.items()
           if k not in ("PYTEST_CURRENT_TEST", "NEWSLENS_REAL_DATA",
                        "NEWSLENS_DATA_DIR", "NEWSLENS_DB_PATH")}
    if extra_env:
        env.update(extra_env)
    return subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True,
        cwd=CHECKOUT, env=env, timeout=60,
    )


def test_unsanctioned_process_cannot_resolve_db_path():
    """The incident shape: bare `python -c` importing newslens and touching
    DB_PATH must die with the refusal, never resolve the real path."""
    r = _run("from newslens import paths; paths.DB_PATH")
    assert r.returncode != 0
    assert "refused" in r.stderr
    assert "newslens.db" not in r.stdout


def test_unsanctioned_process_cannot_resolve_data_dir():
    """Same for DATA_DIR — the exact attribute the render-proof script
    wrote through."""
    r = _run("from newslens import paths; paths.DATA_DIR")
    assert r.returncode != 0
    assert "refused" in r.stderr


def test_allow_real_paths_sanctions_the_process():
    """The entrypoint lane: after allow_real_paths() the same process
    resolves DB_PATH normally (this is what cli.main/doctor.main call)."""
    r = _run("from newslens import paths; paths.allow_real_paths(); "
             "print(paths.DB_PATH)")
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip().endswith("newslens.db")


def test_explicit_env_opt_in_sanctions_the_process():
    """The conscious one-off lane: NEWSLENS_REAL_DATA=1 resolves — the
    transcript-greppable opt-in for a deliberate ad-hoc use."""
    r = _run("from newslens import paths; print(paths.DATA_DIR)",
             extra_env={"NEWSLENS_REAL_DATA": "1"})
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip().endswith("data")


def test_refusal_names_the_limit_and_the_cures():
    """The refusal message must teach: name the incident class, the sanction
    lanes, the sandbox redirection lane, and the hardcoded-path limit — a
    guard nobody understands gets NEWSLENS_REAL_DATA=1'd into decoration."""
    r = _run("from newslens import paths; paths.DB_PATH")
    assert "NEWSLENS_REAL_DATA=1" in r.stderr
    assert "NEWSLENS_DATA_DIR" in r.stderr
    assert "LIMIT" in r.stderr


def test_env_inheriting_child_of_pytest_still_refuses():
    """THE v7-M1 pinhole shape (2026-07-14): a child process that inherits
    pytest's environment (PYTEST_CURRENT_TEST rides along in os.environ) is
    NOT the pytest process and must get no real-path sanction from it.
    Born red: the guard's original pytest arm blessed every env-inheriting
    child, which is exactly how the suite's doctor child reached the real
    data/ directory."""
    r = _run("from newslens import paths; paths.DB_PATH",
             extra_env={"PYTEST_CURRENT_TEST": "tests/fake.py::t (call)"})
    assert r.returncode != 0
    assert "refused" in r.stderr


def test_child_with_data_dir_override_resolves_the_override(tmp_path):
    """The sandbox lane for children: NEWSLENS_DATA_DIR redirects DATA_DIR
    and (derived) DB_PATH with no real-path sanction involved — this is how
    the QA sandbox now crosses the process boundary. Born red with the
    pinhole fix."""
    sb = tmp_path / "sb"
    r = _run("from newslens import paths; "
             "print(paths.DATA_DIR); print(paths.DB_PATH)",
             extra_env={"NEWSLENS_DATA_DIR": str(sb),
                        "PYTEST_CURRENT_TEST": "tests/fake.py::t (call)"})
    assert r.returncode == 0, r.stderr
    lines = r.stdout.strip().splitlines()
    assert lines[0] == str(sb)
    assert lines[1] == str(sb / "newslens.db")
