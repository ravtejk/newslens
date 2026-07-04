"""Pre-install doctor contract (ADR-0002; SETUP.md 'what the doctor looks like').

Runs the REAL scripts/doctor as a subprocess under the system Python
(/usr/bin/python3, no venv, no third-party deps), from a foreign cwd, with a
scrubbed environment — the exact "fresh clone, minutes to green" state.

Zero-network is enforced mechanically: a sitecustomize.py injected via
PYTHONPATH patches socket at interpreter startup to record-and-refuse every
DNS lookup / connect. The doctor's claim only holds if the recording log
stays empty AND the output shows "not set" (never "could not reach").
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from conftest import PROTOTYPE_ROOT

SYSTEM_PYTHON = Path("/usr/bin/python3")

OPENAI_HINT = (
    "OPENAI_API_KEY not set — get one at platform.openai.com/api-keys, "
    "then add to .env"
)
PERPLEXITY_HINT = (
    "PERPLEXITY_API_KEY not set — get one at perplexity.ai/settings/api, "
    "then add to .env"
)

SITECUSTOMIZE_TEMPLATE = """\
import socket

_LOG = {log_path!r}

def _record(kind, detail):
    with open(_LOG, "a") as fh:
        fh.write(kind + " " + repr(detail) + "\\n")

def _spy_getaddrinfo(host, *args, **kwargs):
    _record("getaddrinfo", host)
    raise socket.gaierror("network blocked by QA sitecustomize")

def _spy_connect(self, address):
    _record("connect", address)
    raise OSError("network blocked by QA sitecustomize")

socket.getaddrinfo = _spy_getaddrinfo
socket.socket.connect = _spy_connect
"""


def _scrubbed_env(tmp_path):
    return {
        "PATH": "/usr/bin:/bin",
        "HOME": os.environ.get("HOME", str(tmp_path)),
        "PYTHONPATH": str(tmp_path),
        "PYTHONIOENCODING": "utf-8",
        # Force-empty: a real .env (with a real key) now exists in the
        # checkout. The doctor gives the process environment precedence, so
        # empty vars here keep the keyless contract testable — and make it
        # impossible for this subprocess to reach a paid API, ever.
        "OPENAI_API_KEY": "",
        "PERPLEXITY_API_KEY": "",
        "GNEWS_API_KEY": "",
    }


@pytest.mark.skipif(not SYSTEM_PYTHON.exists(), reason="no /usr/bin/python3 on this machine")
def test_preinstall_doctor_is_friendly_exit_1_with_zero_network(tmp_path):
    net_log = tmp_path / "network-attempts.log"
    (tmp_path / "sitecustomize.py").write_text(
        SITECUSTOMIZE_TEMPLATE.format(log_path=str(net_log)), encoding="utf-8"
    )

    proc = subprocess.run(
        [str(SYSTEM_PYTHON), str(PROTOTYPE_ROOT / "scripts" / "doctor")],
        capture_output=True,
        text=True,
        timeout=120,
        cwd=str(tmp_path),  # foreign cwd: paths must anchor on the checkout, not $PWD
        env=_scrubbed_env(tmp_path),
    )

    combined = proc.stdout + proc.stderr
    assert proc.returncode == 1, combined
    assert "Traceback" not in combined

    # Friendly, specific fix hints for the pre-install state:
    assert "missing Python deps: PyYAML, python-dotenv" in proc.stdout
    assert OPENAI_HINT in proc.stdout
    assert PERPLEXITY_HINT in proc.stdout
    assert "sources.yaml validation skipped (PyYAML not installed" in proc.stdout
    # A real .env may or may not exist in the checkout; both states must
    # render friendly (the forced-empty process env keeps keys "not set").
    assert (
        ".env not found — run: cp .env.example .env" in proc.stdout
        or ".env found" in proc.stdout
    )
    # No secret-shaped value is ever echoed, whatever .env contains.
    import re

    assert not re.search(r"\b(sk|pplx|gsk)-[A-Za-z0-9_-]{12,}", combined)

    # The stdlib-only schema check still works pre-install:
    assert (
        "migrations apply cleanly to a scratch DB — tables: "
        "briefings, briefings_history, memory, source_items"
    ) in proc.stdout

    assert "Doctor exit 1" in proc.stdout
    assert "/usr/bin/python3" in proc.stdout  # really ran on the system interpreter

    # Mechanical zero-network: the spy recorded nothing at all.
    assert not net_log.exists() or net_log.read_text() == "", (
        f"pre-install doctor attempted network calls: {net_log.read_text()}"
    )
    # And no attempted-but-blocked call was swallowed into a friendly line:
    assert "could not reach" not in proc.stdout


@pytest.mark.skipif(not SYSTEM_PYTHON.exists(), reason="no /usr/bin/python3 on this machine")
def test_every_source_file_compiles_on_the_system_39_interpreter(tmp_path):
    """Guards the >=3.9 floor mechanically: any 3.10+-only syntax anywhere in
    the package (or the launcher) fails this compile pass."""
    files = sorted((PROTOTYPE_ROOT / "src" / "newslens").glob("*.py"))
    files.append(PROTOTYPE_ROOT / "scripts" / "doctor")
    prog = (
        "import sys, pathlib\n"
        "for f in sys.argv[1:]:\n"
        "    compile(pathlib.Path(f).read_text(encoding='utf-8'), f, 'exec')\n"
        "print('COMPILED-OK')\n"
    )
    proc = subprocess.run(
        [str(SYSTEM_PYTHON), "-c", prog, *[str(f) for f in files]],
        capture_output=True,
        text=True,
        timeout=120,
        env=_scrubbed_env(tmp_path),
    )
    assert proc.returncode == 0, proc.stderr
    assert "COMPILED-OK" in proc.stdout
