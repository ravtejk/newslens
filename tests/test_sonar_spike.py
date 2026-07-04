"""scripts/sonar_spike keyless contract (ADR-0003; NOTES-M2 item 4).

The spike is gated on the principal granting PERPLEXITY_API_KEY. Until then
it must refuse politely (exit 1, the documented message) and touch no socket —
verified mechanically with the sitecustomize spy, same as the pre-install
doctor test. Key vars are force-emptied in the subprocess env so a real .env
appearing later can never make this test spend money.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from conftest import PROTOTYPE_ROOT

SPIKE = PROTOTYPE_ROOT / "scripts" / "sonar_spike"

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


def test_BUG4_spike_script_is_executable_with_python3_shebang():
    """KNOWN-RED (BUG-4): scripts/sonar_spike is committed mode 100644 —
    the documented invocation `scripts/sonar_spike` fails with Permission
    denied. Implementer fix: chmod +x scripts/sonar_spike and commit the
    mode change (100755, like scripts/doctor)."""
    assert os.access(SPIKE, os.X_OK), "scripts/sonar_spike is not executable"
    first = SPIKE.read_text(encoding="utf-8").splitlines()[0]
    assert first == "#!/usr/bin/env python3"


def test_spike_refuses_politely_keyless_with_zero_network(tmp_path):
    net_log = tmp_path / "network-attempts.log"
    (tmp_path / "sitecustomize.py").write_text(
        SITECUSTOMIZE_TEMPLATE.format(log_path=str(net_log)), encoding="utf-8"
    )
    env = {
        "PATH": "/usr/bin:/bin",
        "HOME": os.environ.get("HOME", str(tmp_path)),
        "PYTHONPATH": str(tmp_path),
        "PYTHONIOENCODING": "utf-8",
        # Force-empty: even if a real .env exists, override=False means the
        # process env wins and the spike stays keyless in this test.
        "PERPLEXITY_API_KEY": "",
        "OPENAI_API_KEY": "",
    }
    proc = subprocess.run(
        [sys.executable, str(SPIKE)],  # venv python: dotenv is installed
        capture_output=True,
        text=True,
        timeout=120,
        cwd=str(tmp_path),
        env=env,
    )
    combined = proc.stdout + proc.stderr
    assert proc.returncode == 1, combined
    assert "Traceback" not in combined
    # The documented polite refusal, exactly:
    assert "PERPLEXITY_API_KEY not set — the spike is gated on the principal" in proc.stdout
    assert "granting the key (get one at perplexity.ai/settings/api" in proc.stdout
    assert "No network was touched." in proc.stdout
    # And "no network" is measured, not narrated:
    assert not net_log.exists() or net_log.read_text() == "", (
        f"keyless spike attempted network calls: {net_log.read_text()}"
    )


@pytest.mark.parametrize(
    "arg, fragment",
    [
        ("abc", "must be a whole number"),
        ("0", "must be between 1 and 25"),
        ("-3", "must be between 1 and 25"),
        ("26", "must be between 1 and 25"),
    ],
)
def test_spike_validates_probe_count_before_any_network(tmp_path, arg, fragment):
    """M2 carryover (review finding 2): the probe count is a money knob —
    non-int/zero/negative/oversize must refuse BEFORE any call. A fake key is
    set so validation (which sits after the key gate) is reachable; the
    socket spy proves refusal happens with zero network."""
    net_log = tmp_path / "network-attempts.log"
    (tmp_path / "sitecustomize.py").write_text(
        SITECUSTOMIZE_TEMPLATE.format(log_path=str(net_log)), encoding="utf-8"
    )
    env = {
        "PATH": "/usr/bin:/bin",
        "HOME": os.environ.get("HOME", str(tmp_path)),
        "PYTHONPATH": str(tmp_path),
        "PYTHONIOENCODING": "utf-8",
        "PERPLEXITY_API_KEY": "pplx-qa-fake-never-real",
        "OPENAI_API_KEY": "",
    }
    proc = subprocess.run(
        [sys.executable, str(SPIKE), arg],
        capture_output=True, text=True, timeout=120, cwd=str(tmp_path), env=env,
    )
    assert proc.returncode == 1
    assert fragment in proc.stdout
    assert "Traceback" not in proc.stdout + proc.stderr
    assert not net_log.exists() or net_log.read_text() == "", (
        f"spike with bad arg {arg!r} attempted network: {net_log.read_text()}"
    )
