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
    """The keyless refusal now lives behind --live (the spike is DRY RUN by
    default since NL-97) and behind the discovery pause, so this case opts past
    the pause to reach the key gate it is about."""
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
        "NEWSLENS_DISCOVERY_ENABLED": "1",
    }
    proc = subprocess.run(
        [sys.executable, str(SPIKE), "--live"],  # venv python: dotenv installed
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


# ---------------------------------------------------------------------------
# NL-97 (gate MAJOR-1) + the discovery pause: the spike is a SPEND INSTRUMENT
# and must default to dry run, read the budget cap, and conform to the pause.
# ---------------------------------------------------------------------------

def _spike_env(tmp_path, **extra):
    env = {
        "PATH": "/usr/bin:/bin",
        "HOME": os.environ.get("HOME", str(tmp_path)),
        "PYTHONPATH": str(tmp_path),
        "PYTHONIOENCODING": "utf-8",
        # A key is DELIBERATELY present in these cases: the whole point is that
        # a present key is no longer sufficient to spend.
        "PERPLEXITY_API_KEY": "pplx-qa-fake-never-real",
        "OPENAI_API_KEY": "",
    }
    env.update(extra)
    return env


def _run_spike(tmp_path, args, **extra_env):
    net_log = tmp_path / "network-attempts.log"
    (tmp_path / "sitecustomize.py").write_text(
        SITECUSTOMIZE_TEMPLATE.format(log_path=str(net_log)), encoding="utf-8"
    )
    proc = subprocess.run(
        [sys.executable, str(SPIKE)] + args,
        capture_output=True, text=True, timeout=120,
        cwd=str(tmp_path), env=_spike_env(tmp_path, **extra_env),
    )
    touched = net_log.read_text() if net_log.exists() else ""
    return proc, touched


def test_spike_default_invocation_is_a_dry_run_that_spends_nothing(tmp_path):
    """NL-97: `scripts/sonar_spike` with a key present used to make 5 paid
    calls immediately. The bare invocation is now a plan, not a purchase."""
    proc, touched = _run_spike(tmp_path, [])
    combined = proc.stdout + proc.stderr
    assert proc.returncode == 0, combined
    assert "Traceback" not in combined
    assert "DRY RUN — no request was built and no socket was touched." in proc.stdout
    assert "worst case:" in proc.stdout          # the cap arithmetic is shown
    assert "BUDGET_CAP_USD_PER_RUN" in proc.stdout
    assert touched == "", f"dry run attempted network: {touched}"


def test_spike_dry_run_reads_the_budget_cap_and_refuses_over_it(tmp_path):
    """The second half of NL-97: the instrument READS the cap. A cap smaller
    than the worst-case spend refuses before the first call, in both modes."""
    proc, touched = _run_spike(
        tmp_path, ["25"], BUDGET_CAP_USD_PER_RUN="0.0001")
    assert proc.returncode == 1
    assert "REFUSED — the worst-case spend" in proc.stdout
    assert "exceeds the cap" in proc.stdout
    assert touched == ""


def test_spike_live_is_refused_while_discovery_is_paused(tmp_path):
    """The pause ruling reaches the instrument, not just the pipeline. Note
    the key IS present and the count IS valid — the refusal is the ruling."""
    proc, touched = _run_spike(tmp_path, ["--live", "3"])
    assert proc.returncode == 1
    assert "REFUSED — tier-2 Sonar discovery is PAUSED by ruling" in proc.stdout
    assert "No network was touched." in proc.stdout
    assert touched == ""


def test_spike_dry_run_names_the_pause_and_the_opt_in(tmp_path):
    proc, _ = _run_spike(tmp_path, [])
    assert "discovery:   PAUSED" in proc.stdout
    assert "NEWSLENS_DISCOVERY_ENABLED=1" in proc.stdout


def test_spike_dry_run_still_dry_when_unpaused(tmp_path):
    """Unpausing does not arm the instrument: --live is its own gate."""
    proc, touched = _run_spike(tmp_path, [], NEWSLENS_DISCOVERY_ENABLED="1")
    assert proc.returncode == 0
    assert "DRY RUN" in proc.stdout
    assert "Re-run with --live to spend the estimate above." in proc.stdout
    assert touched == ""
