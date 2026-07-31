"""Process-wide clock shift — the third-leg convention's in-tree residency.

Standing convention (DECISIONS 2026-07-31, gate ruling R3, NL-126): every suite
pass runs a THIRD leg — ordered @ a shifted clock, default +365d — alongside the
two legs the R5 shuffle convention already requires. The failure class this
catches is the fixture time bomb: an ABSOLUTE date literal in a fixture that
falls out of a trailing now-anchored window as real time passes, turning a green
test red on a calendar date nobody computed.

That class has bitten this tree twice. `tests/test_memory_sync.py:30-36` carries
an autouse `frozen_clock` whose docstring records the first bite ("the suite
started failing 14 real days after `add_row`'s default `created_at`"). NL-126 was
the second: four fixtures stamped `2026-07-17T00:00:00.000Z` against a 14-day
recency cap detonated at 2026-07-30 20:00 EDT — BETWEEN a gate's suite run and
its land, which is the worst timing shape a suite has.

Usage (the third leg a pass owes):

    pytest                                        # leg 1: ordered @ real clock
    pytest --shuffle                              # leg 2: shuffled @ real clock
    pytest --clockshift                           # leg 3: ordered @ +365d
    pytest --clockshift --clockshift-by +30d      # an explicit horizon
    NEWSLENS_CLOCKSHIFT=+365d pytest              # identical, env form

Routine QA loops may fold legs 2 and 3 (`--shuffle --clockshift`) at the cost of
one disambiguation re-run on any red. Gate passes run all three.

--------------------------------------------------------------------------
COVERAGE BOUND — stated here so a green third leg is never over-read
--------------------------------------------------------------------------
This closes the **Python-clock class only**. SQL-side clocks are structurally
out of its reach: a process-clock shift cannot move SQLite's `'now'`, so
`date('now')` predicates in product code (`src/newslens/events.py:36`, the
"one listen per calendar day" dedup) and the ~21 schema columns defaulting to
`strftime('%Y-%m-%dT%H:%M:%fZ','now')` (including `source_items.fetched_at` and
`briefings.generated_at` — the two columns at the heart of NL-126 itself) are
invisible to this plugin at every horizon. A green run here means "no remaining
**Python-clock** instance." It does NOT mean "the tree has no time bombs."
That class is NL-126d, and it owns its own guard.

Two further bounds, disclosed for the same reason:
  * Only `datetime` is swapped. `time.time()`, `time.localtime()` and
    `time.monotonic()` are NOT shifted — the convention names the datetime
    wall-clock entry points and nothing else, and widening the swap is what
    turns a detector into a noise machine (see THE CONVERSION LAW).
  * The swap is installed at plugin-import time, which is before this repo's
    conftest and before any `newslens` module. Modules imported EARLIER than
    that (pytest's own dependencies, stdlib) already hold the real class in
    their namespaces if they did `from datetime import datetime`. The guard
    below makes the in-repo half of that loud; the pytest-internal half is
    accepted and is why the instrument is a detector, not a simulator.
  * sqlite3's default datetime ADAPTERS are registered by exact type and
    predate the swap, so binding datetime OBJECTS as SQL parameters under a
    shifted run diverges by sqlite3 import order (QA F-D, measured both
    directions). Unreachable today — src/ binds ISO strings only and
    `db.connect` sets no `detect_types` — recorded so a parameterized bind
    that someday fails only under leg 3 is diagnosable in one read.
  * Shifted instances are UNPICKLABLE (closure-local classes; deepcopy works)
    — QA F-E. No datetime is pickled anywhere in src/ or tests/ today.

--------------------------------------------------------------------------
THE CONVERSION LAW — shift entry points, never conversions
--------------------------------------------------------------------------
Shift ONLY the wall-clock entry points: `datetime.now`, `datetime.utcnow`,
`datetime.today`, `date.today`. NEVER conversions: `fromtimestamp`, `strptime`,
`combine`, `fromisoformat`, `fromordinal`. A conversion takes its value from its
ARGUMENT, not from the wall clock; shifting one moves data out from under code
that correctly pins its own `now_utc=` and manufactures failures that look like
findings.

This is measured, not asserted. Building the NL-126 sweep, shifting only product
code produced 86 failures at +30d; shifting product + test modules produced 71;
shifting the process clock at import produced 6 (the port-class only, i.e. zero
signal beyond the known-unrelated). QA's independent rebuild then re-learned the
same lesson from the other end: overriding `fromtimestamp` manufactured exactly
two false positives in `tests/test_diagnose.py`, because `diagnose.run_diagnose`
(`diagnose.py:202-204`) round-trips its OWN pinned `now_utc` through
`.timestamp()` -> `fromtimestamp(...)`. Horizon-independence was the tell: a real
fixture expiry is horizon-DEPENDENT; those two failed identically at +30d and
+365d. 86 -> 71 -> 6 is the whole design in three numbers.

--------------------------------------------------------------------------
WHY `-p`, AND NOT CONFTEST RE-EXPORT (the one place this differs from
tools/pytest_shuffle.py)
--------------------------------------------------------------------------
The shuffle plugin registers by re-exporting its hooks into `tests/conftest.py`.
This plugin CANNOT use that mechanism, and the reason is mechanical rather than
stylistic: `tests/conftest.py:26` runs `from newslens import db, paths` twelve
lines BEFORE the re-export point, so by the time a conftest-registered plugin
could act, product modules have already bound `from datetime import datetime` in
their own namespaces and the swap silently under-detects.

Measured in this repo, printing `sys.modules` at each hook:

    -p plugin import  ->  newslens modules = []                          CLEAN
    pytest_configure  ->  ['newslens', 'newslens.db', 'newslens.paths']  TOO LATE

So the swap happens at MODULE IMPORT, driven by `sys.argv` / the environment,
not at `pytest_configure`. Registration is `addopts = "-p tools.pytest_clockshift"`
in `pyproject.toml`, with `pythonpath = ["."]` beside it so `tools` is importable
before plugin loading under BOTH run forms (`python -m pytest` and the
`pytest` console script; without `pythonpath` the console-script form dies on
`No module named 'tools'`).

Do NOT also re-export these hooks from a conftest: as with the shuffle plugin,
double registration dies on `--clockshift already added` before a single test
runs.

And because "loaded too late" is otherwise a SILENT under-detection, it is made
loud: if any `newslens.*` module is already in `sys.modules` when a shift is
about to be installed, the install raises instead of swapping. The import-order
accident becomes a failure you cannot miss.

--------------------------------------------------------------------------
INERTNESS, and the two guards
--------------------------------------------------------------------------
Without a requested shift the plugin is inert in the strongest sense: no swap is
installed at all, `datetime.datetime is` the real class, and the collected order
and outcome of an ordered run are byte-for-byte what they were before this file
existed. That is what lets it live on a default addopts line.

`--clockshift-by SPEC` WITHOUT `--clockshift` is a hard `UsageError`, the twin of
the shuffle plugin's seed guard and for the same reason: that combination means
the author believes the run is shifted, and a silently-unshifted run reported as
a shifted leg is exactly the hollow attestation this convention exists to end.
The spec is the tell that the author believed they were shifting.

`--clockshift` parsed at configure time but with NO swap installed at import time
is also a hard `UsageError` — it means the plugin was loaded late (dropped from
addopts, or re-exported from a conftest) or the flag lives where the argv scan
cannot see it (ini addopts, PYTEST_ADDOPTS), so the flag would have been
accepted and done nothing.

The MIRROR is refused too: a swap installed at import with no `--clockshift`
parsed by pytest's own parser (pytest.main() inside a process whose argv
carries the flag, or the flag after a bare `--`) is a hard `UsageError` —
unless the source was the env var, which is the documented flagless path.

`-p no:tools.pytest_clockshift` does NOT unshift, and with a shift requested it
is refused at load time: blocking silences the HOOKS, but the module is already
imported by the addopts `-p` and the swap already installed, so the run would
be shifted with the header, the summary and every refusal silenced (QA F-A).
With nothing requested, blocking is honored and inert. To run unshifted, drop
the flag / unset the env var — never `-p no:`.

Value grammar (`--clockshift-by` and `NEWSLENS_CLOCKSHIFT` share it):

    +365d   365d   365      days (a bare number is days)
    +12h    18h             hours
    1.5d    0.5h            fractional is fine
    (leading + is optional; no internal spaces; one term only)

A NEGATIVE horizon must use the `=` form — `--clockshift-by=-12h`, not
`--clockshift-by -12h`. That is argparse, not this plugin: a bare `-12h` after
an option is read as another option and the run dies on "expected one
argument". The env form has no such problem (`NEWSLENS_CLOCKSHIFT=-12h`).
Backwards clocks are not a real failure mode for this class — trailing windows
only expire forwards — so the form is documented rather than smoothed over.

Precedence is by SOURCE, whole-request: the command line beats the environment,
so a bare `--clockshift` resolves the DEFAULT +365d even while
NEWSLENS_CLOCKSHIFT carries some other horizon (QA F-C) — the flag never
half-reads the environment, which keeps the binding leg's horizon independent
of lingering shell state. The header always echoes what actually resolved.

Sharp edge, disclosed rather than defended: a bare number is DAYS, so
`NEWSLENS_CLOCKSHIFT=1` means +1 day, not "on". It is not silent — the header
and the terminal summary of every shifted run print the resolved shift AND the
absolute datetime the run believes it is, so a quoted report replays exactly.
Boolean-looking words (`true`/`yes`/`on`/...) are refused outright rather than
guessed at.

Deliberately dependency-free (stdlib only, no freezegun/time-machine): those
libraries are per-test context managers, not a process-wide swap installed
before conftest, so they cannot reach this failure class — and adding a
dependency for a ~120-line shim is an escalation trigger, not a default.
"""

from __future__ import annotations

import datetime as _datetime_module
import os
import re
import shlex
import sys
from typing import Optional

import pytest

ENV_VAR = "NEWSLENS_CLOCKSHIFT"
DEFAULT_SPEC = "+365d"

#: The genuine stdlib classes, captured before any swap. Tests and any caller
#: that needs the true wall clock under a shifted run go through these.
REAL_DATETIME = _datetime_module.datetime
REAL_DATE = _datetime_module.date

_BOOLEAN_WORDS = {"true", "false", "yes", "no", "on", "off", "y", "n", "t", "f"}
_UNITS = {"d": "days", "h": "hours"}
_SPEC_RE = re.compile(r"^([+-]?)(\d+(?:\.\d+)?)([dhDH]?)$")

_CLOCKSHIFT_HELP = (
    "run the suite at a shifted process clock (default +365d; the resolved "
    "shift and the absolute datetime are printed and can be replayed with "
    "--clockshift-by)")


class ClockshiftError(RuntimeError):
    """Raised at plugin-import time when a shift is requested but cannot be
    installed honestly. Deliberately NOT a UsageError: this fires during
    `-p` plugin import, before pytest owns the error channel, and it must be
    impossible to mistake for a test failure."""


def parse_shift(spec: str) -> _datetime_module.timedelta:
    """`'+365d'` / `'365'` / `'-12h'` / `'1.5d'` -> timedelta. Raises ValueError.

    Bare numbers are DAYS. The grammar is deliberately tiny: one signed number,
    one optional unit. Compound specs (`365d12h`) are not accepted — a horizon
    that needs two terms is a horizon nobody will quote correctly in a report.
    """
    raw = (spec or "").strip()
    if not raw:
        raise ValueError("empty clock shift")
    if raw.lower() in _BOOLEAN_WORDS:
        raise ValueError(
            f"{raw!r} is not a clock shift. This option takes a HORIZON, not a "
            "boolean: e.g. +365d, +30d, -12h, 365 (a bare number is days).")
    # Matched whole, not parsed piecewise. A hand-rolled strip-the-suffix,
    # strip-the-sign, float() version accepts `30 d` and `++5d` silently
    # (float() tolerates surrounding whitespace, and one sign-strip leaves the
    # next one for float) — caught by this module's own grammar tests. A horizon
    # that parses as something other than what was typed is the same defect
    # class as a leg that reports shifted and runs real: refuse, never guess.
    match = _SPEC_RE.match(raw)
    if match is None:
        raise ValueError(
            f"{spec!r} is not a clock shift. Accepted: one signed number with "
            "an optional d/h suffix and no internal spaces — +365d, 30d, 365, "
            "-12h, 1.5d.")
    sign, amount, unit = match.groups()
    factor = -1.0 if sign == "-" else 1.0
    return _datetime_module.timedelta(
        **{_UNITS[unit.lower() or "d"]: factor * float(amount)})


def _scan_argv(argv) -> "tuple[bool, Optional[str]]":
    """Read `--clockshift` / `--clockshift-by` straight off argv at import time.

    argparse has not run yet and cannot: `pytest_addoption` fires long after the
    swap must be installed (see the module docstring's measurement). argv is
    fully intact at `-p` import time — verified, including the `-p` args
    themselves — so this scan is the earliest honest read of the flag.

    It sees ONLY the real command line. A `--clockshift` living in `addopts`
    (ini) is invisible here; `pytest_configure` catches that case and refuses
    the run rather than letting it pass as a shifted leg that never shifted.
    """
    on = False
    spec = None
    for i, arg in enumerate(argv[1:], start=1):
        if arg == "--clockshift":
            on = True
        elif arg == "--clockshift-by":
            spec = argv[i + 1] if i + 1 < len(argv) else None
        elif arg.startswith("--clockshift-by="):
            spec = arg.split("=", 1)[1]
        elif arg.startswith("--clockshift="):
            # argparse would reject this (store_true takes no value); surface it
            # as "on" so configure's own parser produces the real error message.
            on = True
    return on, spec


_BLOCK_SPEC = "no:tools.pytest_clockshift"


def _plugin_block_requested() -> bool:
    """True if this run also asks pytest to BLOCK this plugin (`-p no:...`).

    Blocking cannot unshift: `-p no:` silences the HOOKS, but the module was
    already imported by the addopts `-p` and the swap already installed — so
    the run would be shifted with the header, the summary and every refusal
    silenced, rc 0 (QA finding F-A, measured both ways). The guard in
    `_install` turns that state into a load-time hard failure instead. Covers
    the spaced and fused argv forms and PYTEST_ADDOPTS. A block written into
    the ini's own addopts is not scanned: editing the ini is a reviewed tree
    change, and DELETING the `-p` line there stops the module loading at all,
    which is honestly inert.
    """
    tokens = list(sys.argv[1:])
    try:
        tokens += shlex.split(os.environ.get("PYTEST_ADDOPTS", ""))
    except ValueError:
        pass  # unbalanced quotes; pytest refuses the same string on its own
    for i, arg in enumerate(tokens):
        if arg == "-p" and i + 1 < len(tokens) and tokens[i + 1] == _BLOCK_SPEC:
            return True
        if arg == "-p" + _BLOCK_SPEC:
            return True
    return False


def _assert_swap_is_still_early(spec: str) -> None:
    """The loud half of the `-p` requirement.

    A swap installed after a `newslens` module has bound `from datetime import
    datetime` under-detects SILENTLY — the leg runs, reports green, and proves
    nothing. Convert that accident into a failure nobody can miss.
    """
    late = sorted(m for m in sys.modules
                  if m == "newslens" or m.startswith("newslens."))
    if not late:
        return
    raise ClockshiftError(
        f"clockshift {spec} was requested, but {len(late)} newslens module(s) "
        f"are ALREADY imported at swap time: {', '.join(late)}.\n"
        "A shift installed this late under-detects silently — modules that ran "
        "`from datetime import datetime` before the swap keep the real class, "
        "so their now-reads never move and the leg proves nothing.\n"
        "Load this plugin at import time: `-p tools.pytest_clockshift` in "
        "addopts (never re-exported from a conftest — tests/conftest.py imports "
        "newslens twelve lines before its re-export point).")


class _ShiftedMeta(type):
    """Makes the swap transparent to isinstance/issubclass in BOTH directions.

    Without this, a `datetime` built before the swap (or by a module holding the
    real class) fails `isinstance(x, datetime.datetime)` once that name points at
    a subclass — a false-positive factory in exactly the "noise machine" family
    the conversion law exists to avoid. Delegating the checks to the real class
    means the swap changes what `now()` RETURNS and nothing else about the type
    system. It also fixes the date/datetime cross-check that plain subclassing
    breaks: `isinstance(<shifted datetime>, datetime.date)` stays True.
    """

    def __instancecheck__(cls, obj):
        return isinstance(obj, cls._real)

    def __subclasscheck__(cls, sub):
        return issubclass(sub, cls._real)


def _build_shifted_classes(shift: _datetime_module.timedelta):
    """Subclasses whose ONLY difference is where `now`/`utcnow`/`today` read.

    Everything else — arithmetic, comparison, formatting, and every conversion
    constructor (`fromtimestamp`, `strptime`, `combine`, `fromisoformat`,
    `fromordinal`) — is inherited untouched. That inheritance IS the conversion
    law: those constructors take their value from their argument and return an
    instance of whatever class they were called on, so they stay correct for
    free precisely because nothing here overrides them.
    """

    class ShiftedDate(REAL_DATE, metaclass=_ShiftedMeta):
        _real = REAL_DATE

        @classmethod
        def today(cls):
            # Via the shifted DATETIME, not `REAL_DATE.today() + shift`:
            # date + timedelta drops the sub-day part, so a +12h shift taken at
            # 20:00 would not roll the date over. The NL-126 sweep's sub-day
            # horizons (+12h/+18h) exist to move `generate._time_of_day()`
            # across its morning/afternoon/evening bands; they have to work.
            moved = REAL_DATETIME.now() + shift
            return cls(moved.year, moved.month, moved.day)

    class ShiftedDateTime(REAL_DATETIME, metaclass=_ShiftedMeta):
        _real = REAL_DATETIME

        @classmethod
        def _wrap(cls, moved):
            return cls(moved.year, moved.month, moved.day, moved.hour,
                       moved.minute, moved.second, moved.microsecond,
                       moved.tzinfo, fold=moved.fold)

        @classmethod
        def now(cls, tz=None):
            return cls._wrap(REAL_DATETIME.now(tz) + shift)

        @classmethod
        def utcnow(cls):
            return cls._wrap(REAL_DATETIME.utcnow() + shift)

        @classmethod
        def today(cls):
            return cls._wrap(REAL_DATETIME.today() + shift)

    return ShiftedDate, ShiftedDateTime


# --------------------------------------------------------------------------
# Import-time install. Everything above is definitions; this is the swap.
# --------------------------------------------------------------------------

_shift: Optional[_datetime_module.timedelta] = None
_spec: Optional[str] = None
_source: Optional[str] = None


def _resolve_request() -> "tuple[Optional[str], Optional[str]]":
    """(spec, source) for the requested shift, or (None, None) for an inert run.

    Precedence: the command line beats the environment, so a one-off
    `--clockshift-by +30d` overrides an exported default without editing it.
    `--clockshift-by` WITHOUT `--clockshift` resolves to nothing here on
    purpose — `pytest_configure` raises the UsageError, where pytest renders it
    as one clean line instead of an import traceback.
    """
    on, spec = _scan_argv(sys.argv)
    if on:
        return (spec or DEFAULT_SPEC), "--clockshift"
    if spec is not None:
        return None, None
    env = os.environ.get(ENV_VAR, "").strip()
    if env:
        return env, ENV_VAR
    return None, None


def _install() -> None:
    global _shift, _spec, _source
    spec, source = _resolve_request()
    if spec is None:
        return
    if _plugin_block_requested():
        raise ClockshiftError(
            f"clockshift {spec} (via {source}) was requested, but this run "
            f"also blocks the plugin (`-p {_BLOCK_SPEC}`). Blocking does NOT "
            "unshift — the swap installs at module import, so the run would "
            "be SHIFTED with the header, the summary and every refusal "
            "silenced: a shifted run reporting nothing. To run unshifted, "
            f"drop --clockshift / unset {ENV_VAR}. To rule this plugin out "
            "while debugging, do that AND remove `-p tools.pytest_clockshift` "
            "from addopts.")
    try:
        shift = parse_shift(spec)
    except ValueError as exc:
        raise ClockshiftError(f"bad clock shift from {source}: {exc}") from None
    _assert_swap_is_still_early(spec)
    shifted_date, shifted_datetime = _build_shifted_classes(shift)
    _datetime_module.date = shifted_date
    _datetime_module.datetime = shifted_datetime
    _shift, _spec, _source = shift, spec, source


_install()


def installed_shift() -> Optional[_datetime_module.timedelta]:
    """The shift actually installed in THIS process, or None if inert."""
    return _shift


def installed_spec() -> Optional[str]:
    """The spec string as the caller wrote it (`'+365d'`), or None if inert."""
    return _spec


def installed_source() -> Optional[str]:
    """`'--clockshift'`, `'NEWSLENS_CLOCKSHIFT'`, or None if inert."""
    return _source


# --------------------------------------------------------------------------
# Hooks
# --------------------------------------------------------------------------

def pytest_addoption(parser):
    group = parser.getgroup("clockshift", "shifted-clock suite leg (NL-126b)")
    group.addoption("--clockshift", action="store_true", default=False,
                    help=_CLOCKSHIFT_HELP)
    group.addoption("--clockshift-by", action="store", default=None,
                    metavar="SPEC",
                    help=f"horizon for --clockshift (default: {DEFAULT_SPEC}); "
                         "a signed number with an optional d/h suffix — "
                         "+365d, 30d, 365, 1.5d. A negative horizon needs the "
                         "= form (--clockshift-by=-12h), an argparse rule")


def pytest_configure(config):
    """Three refusals, all aimed at the same defect family: a run whose
    reported clock and actual clock could be read apart.

    (1) a horizon with no `--clockshift` — the author thinks it is shifted;
    (2) a `--clockshift` that argparse saw but the import-time swap did not —
        either the flag lives where the argv scan cannot see it (ini addopts,
        PYTEST_ADDOPTS) or the plugin truly loaded too late. Either way the
        flag was accepted and shifted nothing — the silent under-detection
        the `-p` requirement exists to prevent, caught from the other end;
    (3) the mirror of (2): a swap INSTALLED with no `--clockshift` parsed —
        the process argv carried the flag for some OTHER parser
        (pytest.main() inside a shifted outer process, or a flag after a
        bare `--`). The env var is exempt: NEWSLENS_CLOCKSHIFT is the
        documented flagless path.
    """
    on = config.getoption("--clockshift")
    spec = config.getoption("clockshift_by")
    if not on:
        if spec is not None:
            raise pytest.UsageError(
                f"--clockshift-by {spec} was given WITHOUT --clockshift: this "
                "run would use the REAL clock and the horizon would do "
                "nothing. Any report calling it a shifted leg would be false. "
                f"Re-run with `--clockshift --clockshift-by {spec}`, or drop "
                "the horizon.")
        if _shift is not None and _source != ENV_VAR:
            raise pytest.UsageError(
                f"a {_spec} clock shift is installed in this process (via "
                f"{_source}), but --clockshift was NOT parsed from this "
                "run's own arguments — the process argv carried the flag "
                "for some other parser (pytest.main() inside a shifted "
                "outer process, or after a bare `--`). The run would be "
                "shifted under a pytest command line that never asked for "
                "it. Pass --clockshift in the args given to pytest itself, "
                f"or use the environment form: {ENV_VAR}={_spec}.")
        return
    if _shift is None:
        raise pytest.UsageError(
            "--clockshift was parsed, but no shift is installed in this "
            "process. Either the flag lives where the import-time argv scan "
            "cannot see it (ini addopts, PYTEST_ADDOPTS), or "
            "tools.pytest_clockshift was imported too late to swap the "
            "clock. Either way the flag was accepted and shifted nothing. "
            "Pass --clockshift on the command line itself, with `-p "
            "tools.pytest_clockshift` in addopts, or use the environment "
            f"form: {ENV_VAR}={spec or DEFAULT_SPEC}.")


def pytest_report_header(config):
    if _shift is None:
        return "clock: REAL (no --clockshift)"
    now = _datetime_module.datetime.now(_datetime_module.timezone.utc)
    real = REAL_DATETIME.now(_datetime_module.timezone.utc)
    return [
        f"clock: SHIFTED {_spec} (via {_source}) — this run believes now is "
        f"{now.isoformat()} (real {real.isoformat()})",
        f"clock: replay with `--clockshift --clockshift-by {_spec}` or "
        f"`{ENV_VAR}={_spec}` — coverage bound: Python-clock class only, "
        "SQL-side date('now')/strftime('now') is out of reach (NL-126d)",
    ]


def pytest_terminal_summary(terminalreporter, exitstatus, config):
    if _shift is None:
        return
    now = _datetime_module.datetime.now(_datetime_module.timezone.utc)
    terminalreporter.write_sep(
        "-", f"shifted-clock run — {_spec} (via {_source}); this run believed "
             f"now was {now.isoformat()} (replay: --clockshift "
             f"--clockshift-by {_spec})")
