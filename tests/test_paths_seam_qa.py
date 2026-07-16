"""Seam completion for the principal-owned files (v7.2 gate FIX-3).

The 2026-07-16 incident: a QA serve probe ran sandboxed via NEWSLENS_DATA_DIR/
NEWSLENS_DB_PATH — but SOURCES_FILE / ENV_FILE / MEMORY_FILE had no env seam
and resolved REAL under the CLI's self-sanction; the probe's follow-click
rewrote the principal's real memory.md (repaired from a byte-verified
reconstruction; snapshot preserved). Second instance of the v7-M1 pinhole
class: the sandbox seam was incomplete outside pytest.

The three principal-owned paths now live behind the same PEP 562 guard with
the same precedence chain (redirection > sanction > refusal). Pins 1-3 born
red on the pre-seam tree; pin 4 guards the entrypoint lane.
"""

from __future__ import annotations

import os
import subprocess
import sys

from newslens import paths

CHECKOUT = str(paths.PROJECT_ROOT)
_SEAM_VARS = ("NEWSLENS_REAL_DATA", "NEWSLENS_DATA_DIR", "NEWSLENS_DB_PATH",
              "NEWSLENS_SOURCES_FILE", "NEWSLENS_ENV_FILE",
              "NEWSLENS_MEMORY_FILE")


def _run(code: str, extra_env: dict | None = None, scrub: bool = True):
    env = dict(os.environ)
    if scrub:
        for k in _SEAM_VARS + ("PYTEST_CURRENT_TEST",):
            env.pop(k, None)
    if extra_env:
        env.update(extra_env)
    return subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True,
        cwd=CHECKOUT, env=env, timeout=60,
    )


def test_unsanctioned_child_refuses_all_three_principal_files():
    """Pin 1 (born red pre-seam): SOURCES_FILE / ENV_FILE / MEMORY_FILE refuse
    in an unsanctioned process, exactly like DATA_DIR/DB_PATH."""
    for name in ("SOURCES_FILE", "ENV_FILE", "MEMORY_FILE"):
        r = _run(f"from newslens import paths; paths.{name}")
        assert r.returncode != 0, name
        assert "refused" in r.stderr, name


def test_env_seams_redirect_each_principal_file(tmp_path):
    """Pin 2 (born red pre-seam): each NEWSLENS_*_FILE var redirects its path
    with no real-path sanction involved."""
    for name, var in (("SOURCES_FILE", "NEWSLENS_SOURCES_FILE"),
                      ("ENV_FILE", "NEWSLENS_ENV_FILE"),
                      ("MEMORY_FILE", "NEWSLENS_MEMORY_FILE")):
        target = tmp_path / f"sb-{name}"
        r = _run(f"from newslens import paths; print(paths.{name})",
                 extra_env={var: str(target)})
        assert r.returncode == 0, (name, r.stderr)
        assert r.stdout.strip() == str(target), name


def test_incident_shape_child_of_pytest_resolves_sandbox_memory_file():
    """Pin 3 (born red pre-seam) — THE 2026-07-16 INCIDENT SHAPE: a child
    process inheriting the suite's environment (as a serve probe would) must
    resolve MEMORY_FILE to the sandbox value the conftest seam exported —
    never the real PROJECT_ROOT/memory.md."""
    sandbox_val = os.environ.get("NEWSLENS_MEMORY_FILE")
    assert sandbox_val, "conftest must export the memory seam for children"
    r = _run("from newslens import paths; print(paths.MEMORY_FILE)",
             scrub=False)
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip() == sandbox_val
    assert r.stdout.strip() != str(paths.PROJECT_ROOT / "memory.md")


def test_entrypoint_sanction_still_resolves_real_principal_files():
    """Pin 4: the real entrypoints (allow_real_paths) still resolve the real
    files when no redirection is set — the guard gates probes, not the app."""
    r = _run("from newslens import paths; paths.allow_real_paths(); "
             "print(paths.MEMORY_FILE); print(paths.SOURCES_FILE)")
    assert r.returncode == 0, r.stderr
    lines = r.stdout.strip().splitlines()
    assert lines[0].endswith("memory.md") and lines[1].endswith("sources.yaml")
