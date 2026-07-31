"""Seeded test-order shuffle — the R5 convention's in-tree residency.

Standing convention (DECISIONS 2026-07-25, R5 record correction): every QA and
gate pass runs the suite TWICE — once ordered, once shuffled under a logged
seed. Order-dependence is the failure class this catches: a test that only
passes because an earlier test left module state, a monkeypatch that leaked, an
autouse fixture someone stopped depending on.

Until Stage-0 M2 the plugin lived OUT of tree and was hand-carried into each
pass, which meant (a) QA and the gate could not reproduce each other's runs
from the repo alone, and (b) the historical `-p no:randomly` flags in older
reports were silent no-ops against a venv that has no randomization plugin at
all — the correction that put this file here.

Usage (the two runs a pass owes):

    pytest                                        # ordered
    pytest -p tools.pytest_shuffle --shuffle      # shuffled, seed auto-picked
    pytest -p tools.pytest_shuffle --shuffle --shuffle-seed 20260725

The chosen seed is printed in the header AND the terminal summary of every
shuffled run, so a report that quotes "seed 20260725" can be replayed exactly.
Without `--shuffle` the plugin is inert: loading it never changes an ordered
run's order, so it is safe to leave on a default addopts line.

`--shuffle-seed N` WITHOUT `--shuffle` is a hard UsageError (NL-118 QA finding
4). Inertness is the property that lets this file live in conftest, and it is
also the exact shape of the silent no-op this plugin was written to end: a pass
that ran `--shuffle-seed 118500`, got an ORDERED run, and attested a shuffled
one. Seed-without-shuffle is never a run anybody wants, so it stops being a
run at all — the seed is the tell that the author believed they were shuffling.

Deliberately dependency-free (stdlib `random` only, no pytest-randomly): the
venv has no randomization plugin and adding one is a new dependency —
an escalation trigger for a 40-line ordering shim.
"""

from __future__ import annotations

import random

import pytest

_SHUFFLE_HELP = (
    "shuffle test execution order (seeded; the seed is printed and can be "
    "replayed with --shuffle-seed)")


def pytest_addoption(parser):
    group = parser.getgroup("shuffle", "seeded test-order shuffle (R5)")
    group.addoption("--shuffle", action="store_true", default=False,
                    help=_SHUFFLE_HELP)
    group.addoption("--shuffle-seed", action="store", type=int, default=None,
                    metavar="N",
                    help="seed for --shuffle (default: a random 32-bit seed, "
                         "printed so the run can be replayed)")


def pytest_configure(config):
    """Resolve the seed ONCE, here, so the header, the summary and the shuffle
    itself all quote the same number even when the seed was auto-picked.

    A seed with no `--shuffle` is refused rather than ignored: that combination
    means the author thinks the run is shuffled, and a silently-ordered run
    that gets reported as shuffled is the R5 failure class this plugin exists
    to close. Failing at configure time costs one run; a hollow attestation
    costs a whole gate's worth of trust in the suite's order-independence."""
    seed = config.getoption("shuffle_seed")
    if not config.getoption("--shuffle"):
        if seed is not None:
            raise pytest.UsageError(
                f"--shuffle-seed {seed} was given WITHOUT --shuffle: this run "
                "would be ORDERED and the seed would do nothing. Any report "
                "calling it a shuffled run would be false. Re-run with "
                f"`--shuffle --shuffle-seed {seed}`, or drop the seed.")
        config._shuffle_seed = None
        return
    if seed is None:
        seed = random.Random().randrange(2 ** 32)
    config._shuffle_seed = int(seed)


def pytest_report_header(config):
    seed = getattr(config, "_shuffle_seed", None)
    if seed is None:
        return "test order: ORDERED (no --shuffle)"
    return f"test order: SHUFFLED — seed {seed} (replay: --shuffle-seed {seed})"


def pytest_collection_modifyitems(session, config, items):
    """Shuffle the whole collected list in place.

    WHOLE-LIST, not shuffle-within-module: a within-module shuffle cannot catch
    cross-FILE ordering coupling, which is the leak this suite actually risks
    (module-level seat resolutions, the paths module-dict shadows, the
    _ACTIVE_ANALYST / _ACTIVE_STEP_SEATS globals). The autouse sandbox fixtures
    make every test independent by construction; this is the check on that
    claim, so it must be allowed to interleave files.
    """
    seed = getattr(config, "_shuffle_seed", None)
    if seed is None:
        return
    random.Random(seed).shuffle(items)


def pytest_terminal_summary(terminalreporter, exitstatus, config):
    seed = getattr(config, "_shuffle_seed", None)
    if seed is not None:
        terminalreporter.write_sep(
            "-", f"shuffled run — seed {seed} (replay: --shuffle-seed {seed})")
