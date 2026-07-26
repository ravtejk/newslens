"""The in-tree seeded shuffle plugin (Stage-0 M2 item 8, R5 convention).

The plugin exists so QA and the gate stop hand-carrying an out-of-tree file
between passes — which means the thing that actually needs pinning is not
"does it shuffle" but "can a report's seed be REPLAYED from the repo alone",
and "does merely loading it change an ordered run". Both are checked here by
running pytest-in-pytest against a throwaway suite (`pytester`-style, with
plain subprocesses so no plugin state is shared).

$0, no network: these subprocesses run a two-line synthetic test file.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from tools import pytest_shuffle

PROTOTYPE_ROOT = Path(__file__).resolve().parents[1]

SYNTHETIC = "\n".join(
    f"def test_case_{i}():\n    assert True\n" for i in range(12))


@pytest.fixture
def mini_suite(tmp_path):
    """A throwaway 12-test suite with its own conftest that loads the plugin
    exactly the way the real one does."""
    d = tmp_path / "mini"
    d.mkdir()
    (d / "test_mini.py").write_text(SYNTHETIC, encoding="utf-8")
    (d / "conftest.py").write_text(
        "import sys\n"
        f"sys.path.insert(0, {str(PROTOTYPE_ROOT)!r})\n"
        "from tools.pytest_shuffle import (pytest_addoption, "
        "pytest_collection_modifyitems, pytest_configure, "
        "pytest_report_header, pytest_terminal_summary)\n",
        encoding="utf-8")
    return d


def collect(d, *args):
    """Collected test order as a list of node names."""
    out = subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q", *args],
        cwd=str(d), capture_output=True, text=True, timeout=120)
    assert out.returncode == 0, out.stdout + out.stderr
    return [ln.strip() for ln in out.stdout.splitlines()
            if ln.strip().startswith("test_mini.py::")]


def test_loading_the_plugin_does_not_reorder_an_ordered_run(mini_suite):
    """The property that lets it live in conftest permanently: without
    --shuffle it is inert, so `pytest` is still an ordered run and nobody has
    to remember a flag to get a reproducible baseline."""
    assert collect(mini_suite) == [f"test_mini.py::test_case_{i}"
                                   for i in range(12)]


def test_the_same_seed_reproduces_the_same_order(mini_suite):
    """The whole point of logging a seed: a red shuffled run in a report can
    be replayed exactly, from the repo, by anyone."""
    first = collect(mini_suite, "--shuffle", "--shuffle-seed", "20260726")
    second = collect(mini_suite, "--shuffle", "--shuffle-seed", "20260726")
    assert first == second
    assert len(first) == 12


def test_different_seeds_give_different_orders(mini_suite):
    """A 'shuffle' that always produced one order would prove nothing about
    order-independence."""
    a = collect(mini_suite, "--shuffle", "--shuffle-seed", "1")
    b = collect(mini_suite, "--shuffle", "--shuffle-seed", "2")
    assert a != b
    assert sorted(a) == sorted(b)          # same tests, different order


def test_the_seed_is_reported_so_a_run_can_be_replayed(mini_suite):
    """An auto-picked seed that was never printed is an unreproducible run.
    The header and the summary must both carry it."""
    out = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "--shuffle"],
        cwd=str(mini_suite), capture_output=True, text=True, timeout=120)
    assert out.returncode == 0, out.stdout + out.stderr
    assert "--shuffle-seed" in out.stdout, out.stdout
    assert "shuffled run — seed" in out.stdout


def test_shuffling_never_drops_or_duplicates_a_test(mini_suite):
    """An in-place shuffle that lost an item would silently shrink the suite —
    the failure mode nobody would notice in a green run."""
    for seed in ("7", "1234", "909090"):
        got = collect(mini_suite, "--shuffle", "--shuffle-seed", seed)
        assert sorted(got) == [f"test_mini.py::test_case_{i}"
                               for i in sorted(range(12), key=str)]


def test_the_plugin_adds_no_third_party_dependency():
    """Stdlib `random` only. Adding pytest-randomly for a 40-line ordering
    shim would be a new dependency — an ENGINEERING.md escalation trigger."""
    src = Path(pytest_shuffle.__file__).read_text(encoding="utf-8")
    for banned in ("import pytest_randomly", "from pytest_randomly",
                   "import numpy", "faker"):
        assert banned not in src
    assert "import random" in src
