"""NL-126b — the third-leg clock tooth's own contract.

`tools/pytest_clockshift.py` is an INSTRUMENT: the gate refuses a pass that skips
the third leg, so a green shifted leg is an attestation. That makes the plugin's
own correctness load-bearing in a way ordinary product code is not — an
instrument that silently fails to shift reports green on a bomb.

This batch's own history is the argument for testing it hard. Building the
NL-126 sweep, the first instrument produced 86 false positives at +30d and the
second 71 before the third produced 6; QA's independent rebuild then manufactured
two more by overriding `fromtimestamp`. Every one of those was a *plausible*
instrument that had not been proven to bite. So the contract tested here is not
"does it run" but: it shifts what it claims, it shifts NOTHING else, it is inert
when not asked, and it refuses every arrangement in which it would report a
shifted leg that ran unshifted.

Most tests spawn a CHILD pytest against a throwaway test file, because the
property under test is a process-wide swap installed at plugin-import time —
before this process's conftest existed. Every child's environment is set
EXPLICITLY (never inherited), which is also what makes this file leg-independent:
it passes identically whether the parent suite is running at the real clock or at
+365d, and a child never inherits the parent's horizon.

The independent oracle throughout is `time.time()`. The plugin swaps `datetime`
and nothing else (a stated coverage bound), so `time.time()` is a true wall clock
inside a shifted run — which makes the delta measurable rather than asserted.
"""

from __future__ import annotations

import datetime
import os
import re
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from tools import pytest_clockshift

PROTOTYPE_ROOT = Path(__file__).resolve().parents[1]

DAY = 86400.0

_ISO_IN_HEADER = re.compile(r"\d{4}-\d{2}-\d{2}T[\d:.]+\+\d{2}:\d{2}")

# The oracle body every child test file shares: report the observed delta
# between the (possibly shifted) datetime clock and the never-shifted time.time().
_OBSERVE = """
import datetime, time

def _delta_seconds():
    return datetime.datetime.now(datetime.timezone.utc).timestamp() - time.time()
"""


def _child_env(**overrides) -> dict:
    """A child environment built explicitly, not inherited.

    Two reasons. (1) Leg independence: if the parent suite is itself running at
    +365d, an inherited NEWSLENS_CLOCKSHIFT would shift every child and every
    assertion below would be measuring the parent's horizon. (2) Sacred
    surfaces: these children run with no conftest, so none of the suite's autouse
    scrubs protect them — the keys are popped here instead.
    """
    env = dict(os.environ)
    env.pop(pytest_clockshift.ENV_VAR, None)
    for key in ("PERPLEXITY_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY"):
        env.pop(key, None)
    env["PYTHONPATH"] = str(PROTOTYPE_ROOT)
    env.update(overrides)
    return env


def _run_child(tmp_path, body: str, *args, ini_addopts: str = "", **env_overrides):
    """Run a child pytest over one throwaway test file, fully isolated.

    Isolated three ways: its own ini (so the repo's addopts do not apply), its
    own rootdir under tmp (so the repo's conftest is not in the ancestry, and
    the real 3000-test suite is never collected), and an explicit environment.
    The plugin under test is reached through PYTHONPATH, exactly as the repo
    reaches it through `pythonpath = ["."]`.
    """
    tmp_path.mkdir(parents=True, exist_ok=True)
    (tmp_path / "test_child.py").write_text(_OBSERVE + textwrap.dedent(body))
    addopts = ("-p tools.pytest_clockshift " + ini_addopts).strip()
    (tmp_path / "pytest.ini").write_text(
        "[pytest]\naddopts = " + addopts + "\n")
    return subprocess.run(
        [sys.executable, "-m", "pytest", "-c", str(tmp_path / "pytest.ini"),
         str(tmp_path / "test_child.py"), *args],
        cwd=str(tmp_path), env=_child_env(**env_overrides),
        capture_output=True, text=True, timeout=180)


# ---------------------------------------------------------------------------
# 1. Registration — the plugin is live in THIS suite, not merely importable
# ---------------------------------------------------------------------------

def test_the_options_are_registered_in_the_running_suite(pytestconfig):
    """Registration proven from inside the real run, not from a --help scrape.

    `getoption` raises ValueError for an unknown option, so this passes only if
    the plugin's `pytest_addoption` actually fired in the process collecting
    this file — i.e. the addopts entry is real and the plugin loaded.
    """
    assert pytestconfig.getoption("--clockshift") in (True, False)
    assert "clockshift_by" in vars(pytestconfig.option)


def test_registration_is_by_addopts_and_never_by_conftest_re_export():
    """The `-p` requirement, asserted against the two files that carry it.

    Not style. `tests/conftest.py` imports newslens twelve lines before the
    point a re-export could act, so a conftest-registered clock swap is already
    too late and would under-detect silently. `pythonpath` rides along because
    without it `tools.` is unimportable at plugin-load time under the `pytest`
    console script.
    """
    pyproject = (PROTOTYPE_ROOT / "pyproject.toml").read_text()
    assert 'addopts = "-p tools.pytest_clockshift"' in pyproject, (
        "the third leg is only binding if every run loads the plugin at "
        "import time; `-p` in addopts is what makes that the default")
    assert 'pythonpath = ["."]' in pyproject, (
        "without it `tools.` is unimportable at plugin-load time under the "
        "`pytest` console script — every run dies on No module named 'tools'")

    conftest = (PROTOTYPE_ROOT / "tests" / "conftest.py").read_text()
    assert "pytest_clockshift" not in conftest, (
        "the clockshift plugin must NOT be re-exported from conftest — "
        "double registration dies on `--clockshift already added`, and a "
        "configure-time swap is too late to shift anything")


# ---------------------------------------------------------------------------
# 2. Inertness — the property that lets this live on a default addopts line
# ---------------------------------------------------------------------------

def test_the_swap_state_matches_what_the_plugin_recorded():
    """The in-process invariant, true under EVERY leg.

    Written against the plugin's recorded state rather than against a fixed
    expectation, because this same test runs in the ordered/real leg (where
    nothing is installed) and in the shifted leg (where something is). Either
    way the recorded state and the actual state of the `datetime` module must
    agree — a plugin that says it shifted but did not is the whole defect class.
    """
    shift = pytest_clockshift.installed_shift()
    if shift is None:
        assert datetime.datetime is pytest_clockshift.REAL_DATETIME
        assert datetime.date is pytest_clockshift.REAL_DATE
        assert pytest_clockshift.installed_spec() is None
    else:
        assert datetime.datetime is not pytest_clockshift.REAL_DATETIME
        observed = (datetime.datetime.now(datetime.timezone.utc)
                    - pytest_clockshift.REAL_DATETIME.now(datetime.timezone.utc))
        assert abs(observed.total_seconds() - shift.total_seconds()) < 10
        assert pytest_clockshift.installed_spec() is not None


def test_a_child_with_the_plugin_loaded_and_no_flag_is_on_the_real_clock(tmp_path):
    """Inertness end to end: loading the plugin changes nothing without a flag."""
    result = _run_child(tmp_path, """
        def test_clock_is_real():
            assert abs(_delta_seconds()) < 5
        def test_class_is_the_stdlib_one():
            import datetime
            assert datetime.datetime.__module__ == "datetime"
            assert type(datetime.datetime).__name__ == "type"
    """)
    assert result.returncode == 0, result.stdout
    assert "2 passed" in result.stdout
    assert "clock: REAL (no --clockshift)" in result.stdout


# ---------------------------------------------------------------------------
# 3. The shift is real — measured against an oracle the plugin does not touch
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("spec,expected_seconds", [
    ("+365d", 365 * DAY),
    ("30d", 30 * DAY),
    ("365", 365 * DAY),
    ("+12h", 12 * 3600.0),
    ("1.5d", 1.5 * DAY),
])
def test_the_requested_horizon_is_the_horizon_the_child_experiences(
        tmp_path, spec, expected_seconds):
    """The core claim, measured: `datetime.now()` moves by exactly the spec.

    Against `time.time()`, which the plugin never touches — so this cannot pass
    by the plugin agreeing with itself.
    """
    result = _run_child(tmp_path, f"""
        def test_shift_is_applied():
            assert abs(_delta_seconds() - {expected_seconds!r}) < 10
    """, "--clockshift", "--clockshift-by", spec)
    assert result.returncode == 0, result.stdout


def test_date_today_goes_through_the_shifted_datetime_not_date_plus_timedelta(
        tmp_path):
    """`date.today()` must roll on a sub-day horizon. `date + timedelta` cannot.

    `date.__add__` uses only `timedelta.days`, so the naive implementation
    leaves `date.today()` frozen under any horizon under 24h — while the NL-126
    sweep's sub-day horizons (+12h/+18h) exist precisely to move a within-day
    now-read (`generate._time_of_day()`) across its morning/afternoon/evening
    bands. If `today()` does not move with them, half the instrument is dead.

    The horizon is COMPUTED from the current time rather than hardcoded, and
    that is the whole care of this test. A fixed `+24h` does not discriminate:
    24h is a whole day, so correct and naive agree — my first version used it
    and a deliberately-broken twin passed. But a fixed sub-day horizon like
    +12h discriminates only between noon and midnight, which would make this
    test's own truth depend on the wall clock — a fixture time bomb, in the
    file whose subject is fixture time bombs. So: pick the midpoint between
    "just past midnight" and "a full day", which is always strictly greater
    than the time remaining today and always strictly under 24h, at every hour.

    The assertion is a RELATIONSHIP (`date.today()` agrees with `now().date()`)
    rather than a named date, for the same reason: a named date computed in this
    process and asserted in a child straddles a real midnight a few times a year.
    """
    real_now = pytest_clockshift.REAL_DATETIME.now()
    next_midnight = (real_now + datetime.timedelta(days=1)).replace(
        hour=0, minute=0, second=0, microsecond=0)
    hours_left_today = (next_midnight - real_now).total_seconds() / 3600.0
    spec = "%.4fh" % ((hours_left_today + 24.0) / 2.0)

    result = _run_child(tmp_path, """
        import datetime
        def test_today_tracks_the_shifted_now():
            assert datetime.date.today() == datetime.datetime.now().date()
        def test_utc_today_tracks_the_shifted_utc_now():
            assert datetime.datetime.utcnow().date() == \\
                   datetime.datetime.now(datetime.timezone.utc).date()
    """, "--clockshift", "--clockshift-by", spec)
    assert result.returncode == 0, (spec, result.stdout)
    assert "2 passed" in result.stdout


def test_the_env_form_and_the_flag_form_shift_identically(tmp_path):
    """The convention names the env var; the flag is ergonomics over it.

    Both resolve at plugin-import time, so both must produce the same swap —
    otherwise a report quoting one form could not be replayed with the other.
    """
    body = """
        def test_shift_is_applied():
            assert abs(_delta_seconds() - 30 * 86400.0) < 10
    """
    via_env = _run_child(tmp_path / "env", body, NEWSLENS_CLOCKSHIFT="+30d")
    via_flag = _run_child(tmp_path / "flag", body,
                          "--clockshift", "--clockshift-by", "+30d")
    assert via_env.returncode == 0, via_env.stdout
    assert via_flag.returncode == 0, via_flag.stdout
    assert "clock: SHIFTED +30d (via NEWSLENS_CLOCKSHIFT)" in via_env.stdout
    assert "clock: SHIFTED +30d (via --clockshift)" in via_flag.stdout


def test_a_negative_horizon_needs_the_equals_form(tmp_path):
    """Pinned because it is a sharp edge a reader will hit, not because it is
    a behaviour worth having.

    `--clockshift-by -12h` dies in argparse — a bare `-12h` after an option
    reads as another option. The `=` form and the env form both work. Recorded
    as a test so the docstring's claim is checked rather than trusted, and so
    a future argparse change is caught rather than silently widening the
    grammar.
    """
    body = """
        def test_backwards():
            assert abs(_delta_seconds() + 12 * 3600.0) < 10
    """
    equals = _run_child(tmp_path / "eq", body, "--clockshift",
                        "--clockshift-by=-12h")
    assert equals.returncode == 0, equals.stdout
    assert "clock: SHIFTED -12h" in equals.stdout

    env = _run_child(tmp_path / "env", body, NEWSLENS_CLOCKSHIFT="-12h")
    assert env.returncode == 0, env.stdout

    spaced = _run_child(tmp_path / "sp", body, "--clockshift",
                        "--clockshift-by", "-12h")
    assert spaced.returncode == 4, spaced.stdout
    assert "expected one argument" in spaced.stdout + spaced.stderr


def test_the_command_line_beats_the_environment(tmp_path):
    """Documented precedence, tested: a one-off horizon overrides an exported one."""
    result = _run_child(tmp_path, """
        def test_flag_won():
            assert abs(_delta_seconds() - 7 * 86400.0) < 10
    """, "--clockshift", "--clockshift-by", "+7d", NEWSLENS_CLOCKSHIFT="+365d")
    assert result.returncode == 0, result.stdout
    assert "clock: SHIFTED +7d (via --clockshift)" in result.stdout


# ---------------------------------------------------------------------------
# 4. Proven to bite — the receipt without which the leg means nothing
# ---------------------------------------------------------------------------

def test_the_instrument_bites_the_real_defect_class_and_only_that_class(tmp_path):
    """The NL-126 mechanism in miniature: bomb detected, correct fixture spared.

    This is the test that earns the third leg its authority. A clock-shift
    plugin that swaps `now` but never detects a bomb is an expensive no-op that
    reports green forever — and the gate's ruling was explicit that an
    instrument must be proven to bite before it is trusted. So both halves of
    the asymmetry are exercised against the real mechanism:

      BOMB    an ABSOLUTE stamp consumed by a trailing 14-day window (exactly
              `ranking.candidate_window`'s shape, `RECENCY_CAP_DAYS = 14`).
              Green at the real clock, RED at +365d — because the window moves
              and the literal does not. This is what detonated on 2026-07-30.

      SAFE    the same window with a NOW-ANCHORED seed. Green at BOTH clocks —
              both sides move together, so it can never expire.

    That asymmetry IS the NL-126 class and nothing else, which is why a shifted
    run is a detector rather than a noise machine. Measured here, not argued.

    The bomb's literal is recomputed from the real clock on every run of this
    test, so this file never grows the very bomb it is demonstrating.
    """
    live_stamp = (pytest_clockshift.REAL_DATETIME.now(datetime.timezone.utc)
                  - datetime.timedelta(days=1))
    bomb = f"""
        import datetime
        # An absolute literal, exactly as a fixture would carry it. One day old
        # at the real clock; 366 days old once the window moves a year.
        STAMP = datetime.datetime.fromisoformat({live_stamp.isoformat()!r})

        def test_stamp_is_inside_the_trailing_window():
            window_start = (datetime.datetime.now(datetime.timezone.utc)
                            - datetime.timedelta(days=14))
            assert STAMP >= window_start
    """
    safe = """
        import datetime

        def test_stamp_is_inside_the_trailing_window():
            stamp = (datetime.datetime.now(datetime.timezone.utc)
                     - datetime.timedelta(days=1))
            window_start = (datetime.datetime.now(datetime.timezone.utc)
                            - datetime.timedelta(days=14))
            assert stamp >= window_start
    """

    bomb_real = _run_child(tmp_path / "bomb_real", bomb)
    bomb_shifted = _run_child(tmp_path / "bomb_shifted", bomb, "--clockshift")
    safe_real = _run_child(tmp_path / "safe_real", safe)
    safe_shifted = _run_child(tmp_path / "safe_shifted", safe, "--clockshift")

    assert bomb_real.returncode == 0, (
        "the bomb must be GREEN today, or this proves nothing about detection"
        + bomb_real.stdout)
    assert bomb_shifted.returncode == 1, (
        "THE INSTRUMENT DID NOT BITE: an absolute stamp against a trailing "
        "window survived +365d, which is the exact fixture time bomb this "
        "convention exists to catch" + bomb_shifted.stdout)
    assert "test_stamp_is_inside_the_trailing_window" in bomb_shifted.stdout

    assert safe_real.returncode == 0, safe_real.stdout
    assert safe_shifted.returncode == 0, (
        "FALSE POSITIVE: a correctly now-anchored fixture went red under the "
        "shift — a detector that flags correct code is a noise machine"
        + safe_shifted.stdout)


# ---------------------------------------------------------------------------
# 5. THE CONVERSION LAW — the line between a detector and a noise machine
# ---------------------------------------------------------------------------

def test_conversions_are_not_shifted(tmp_path):
    """`fromtimestamp`/`strptime`/`combine`/`fromisoformat`/`fromordinal` unmoved.

    This is the 86 -> 71 -> 6 lesson as an executable assertion. A conversion
    takes its value from its ARGUMENT; shifting one moves data out from under
    code that correctly pins its own `now_utc=`, which is how QA's rebuild
    manufactured two failures in `tests/test_diagnose.py` — `run_diagnose`
    round-trips its own pinned clock through `.timestamp()` -> `fromtimestamp`.
    """
    result = _run_child(tmp_path, """
        import datetime

        def test_fromtimestamp_is_untouched():
            assert (datetime.datetime.fromtimestamp(0, datetime.timezone.utc)
                    == datetime.datetime(1970, 1, 1, tzinfo=datetime.timezone.utc))

        def test_strptime_is_untouched():
            got = datetime.datetime.strptime("2026-07-17", "%Y-%m-%d")
            assert (got.year, got.month, got.day) == (2026, 7, 17)

        def test_fromisoformat_is_untouched():
            got = datetime.datetime.fromisoformat("2026-07-17T00:00:00")
            assert (got.year, got.month, got.day) == (2026, 7, 17)

        def test_combine_is_untouched():
            got = datetime.datetime.combine(
                datetime.date(2026, 7, 17), datetime.time(12, 0))
            assert (got.year, got.month, got.day, got.hour) == (2026, 7, 17, 12)

        def test_fromordinal_is_untouched():
            ref = datetime.date(2026, 7, 17)
            assert datetime.date.fromordinal(ref.toordinal()) == ref

        def test_arithmetic_and_comparison_are_untouched():
            a = datetime.datetime(2026, 7, 17)
            assert (a + datetime.timedelta(days=14)).day == 31
            assert a < datetime.datetime(2026, 7, 18)

        def test_but_the_entry_points_ARE_shifted():
            assert abs(_delta_seconds() - 365 * 86400.0) < 10
    """, "--clockshift")
    assert result.returncode == 0, result.stdout
    assert "7 passed" in result.stdout


def test_time_module_is_not_shifted(tmp_path):
    """A stated coverage bound, pinned so a later widening is a deliberate act.

    `time.time()` is also the oracle every other test here leans on; if some
    future edit shifts it, those tests would start passing vacuously. This is
    the test that would go red first.
    """
    result = _run_child(tmp_path, """
        import time, datetime
        def test_time_time_is_real():
            # ~30 years of drift would be needed for this to be ambiguous.
            assert time.gmtime(time.time()).tm_year == \\
                   datetime.datetime.utcfromtimestamp(time.time()).year
            assert _delta_seconds() > 300 * 86400.0
    """, "--clockshift")
    assert result.returncode == 0, result.stdout


def test_isinstance_survives_the_swap_in_both_directions(tmp_path):
    """The swap must be invisible to the type system, or it manufactures reds.

    A plain subclass breaks two checks: a datetime built by a module holding the
    pre-swap class fails `isinstance(x, datetime.datetime)`, and a shifted
    datetime fails `isinstance(x, datetime.date)` (the shifted datetime class
    does not inherit the shifted date class — C layout forbids it). Both are
    false-positive factories; the metaclass delegates the checks to the real
    classes so only the RETURN VALUE of now/today ever differs.
    """
    result = _run_child(tmp_path, """
        import datetime
        from tools.pytest_clockshift import REAL_DATE, REAL_DATETIME

        def test_shifted_instance_passes_both_names():
            now = datetime.datetime.now()
            assert isinstance(now, datetime.datetime)
            assert isinstance(now, datetime.date)     # datetime IS-A date
            assert isinstance(now, REAL_DATETIME)
            assert isinstance(datetime.date.today(), datetime.date)

        def test_a_pre_swap_instance_still_passes_the_swapped_name():
            real = REAL_DATETIME(2026, 7, 17)
            assert isinstance(real, datetime.datetime)
            assert isinstance(real, datetime.date)
            assert isinstance(REAL_DATE(2026, 7, 17), datetime.date)
            assert not isinstance(REAL_DATE(2026, 7, 17), datetime.datetime)

        def test_issubclass_is_delegated_too():
            assert issubclass(datetime.datetime, datetime.date)
            assert issubclass(REAL_DATETIME, datetime.date)
    """, "--clockshift")
    assert result.returncode == 0, result.stdout
    assert "3 passed" in result.stdout


# ---------------------------------------------------------------------------
# 6. The refusals — every arrangement that would report an unshifted leg
# ---------------------------------------------------------------------------

def test_a_horizon_without_the_flag_is_a_hard_usage_error(tmp_path):
    """The twin of the shuffle plugin's seed guard, and the same defect class.

    `--clockshift-by` alone is the tell that the author believed the run was
    shifted. Under a plugin that merely ignored it, the run would be ORDERED at
    the REAL clock and get reported as the third leg. Refusing costs one run; a
    hollow attestation costs the convention's whole value.
    """
    result = _run_child(tmp_path, """
        def test_never_runs():
            assert False
    """, "--clockshift-by", "+30d")
    assert result.returncode == 4, (result.returncode, result.stdout, result.stderr)
    combined = result.stdout + result.stderr
    assert "WITHOUT --clockshift" in combined
    assert "never runs" not in result.stdout, "no test may execute under a UsageError"


def test_the_flag_arriving_too_late_to_swap_is_refused(tmp_path):
    """The other end of the same defect: a flag accepted that shifted nothing.

    Reproduced through the real mechanism rather than a mock — `--clockshift`
    placed in the ini's addopts is invisible to the import-time argv scan, so
    argparse sees the flag while no swap was ever installed. That is exactly the
    shape a future edit would produce by moving the flag into addopts and
    believing every run was shifted.
    """
    result = _run_child(tmp_path, """
        def test_never_runs():
            assert False
    """, ini_addopts="--clockshift")
    assert result.returncode == 4, (result.returncode, result.stdout, result.stderr)
    combined = result.stdout + result.stderr
    assert "no shift is installed in this process" in combined
    assert "imported too late" in combined


def test_a_late_import_hard_fails_instead_of_under_detecting():
    """The `newslens.*`-already-imported guard, exercised directly.

    A fake `newslens` module is injected rather than importing the real one:
    the guard reads `sys.modules` keys, so a stand-in exercises it exactly —
    and the real `newslens.paths` import is refused by the 2026-07-14 real-data
    incident guard anyway, which this test has no business overriding.
    """
    script = textwrap.dedent("""
        import sys, types
        sys.modules["newslens"] = types.ModuleType("newslens")
        sys.modules["newslens.paths"] = types.ModuleType("newslens.paths")
        try:
            import tools.pytest_clockshift  # noqa: F401
        except Exception as exc:
            print(type(exc).__name__)
            print(exc)
            sys.exit(7)
        sys.exit(0)
    """)
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=str(PROTOTYPE_ROOT), env=_child_env(NEWSLENS_CLOCKSHIFT="+365d"),
        capture_output=True, text=True, timeout=60)
    assert result.returncode == 7, (result.returncode, result.stdout, result.stderr)
    assert "ClockshiftError" in result.stdout
    assert "ALREADY imported at swap time" in result.stdout
    assert "newslens, newslens.paths" in result.stdout


def test_the_same_late_import_is_harmless_when_nothing_was_requested():
    """Inertness beats the guard: no shift requested, no swap, nothing to fail.

    Without this, the guard would make the plugin unimportable from any process
    that happens to have newslens loaded — which would be a new failure mode
    invented by the instrument itself.
    """
    script = textwrap.dedent("""
        import sys, types
        sys.modules["newslens"] = types.ModuleType("newslens")
        import tools.pytest_clockshift as cs
        assert cs.installed_shift() is None
        import datetime
        assert datetime.datetime is cs.REAL_DATETIME
        sys.exit(0)
    """)
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=str(PROTOTYPE_ROOT), env=_child_env(),
        capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, (result.stdout, result.stderr)


def test_blocking_the_plugin_while_requesting_a_shift_dies_loudly(tmp_path):
    """QA F-A: `-p no:tools.pytest_clockshift` silences the hooks but cannot
    un-run the import-time swap.

    Unguarded, both vectors below ran SHIFTED with the header, the summary and
    every refusal silenced, rc 0 — a shifted run reporting nothing, measured
    by QA and re-measured at the gate. The guard turns that state into a
    load-time hard failure. Blocking with NOTHING requested stays legal
    (inertness makes it honest), which is why the guard fires only when a
    shift is requested.
    """
    via_flag = _run_child(tmp_path / "flag", """
        def test_never_runs():
            assert False
    """, "--clockshift", "-p", "no:tools.pytest_clockshift")
    assert via_flag.returncode != 0, via_flag.stdout
    combined = via_flag.stdout + via_flag.stderr
    assert "Blocking does NOT unshift" in combined
    assert "never_runs" not in via_flag.stdout

    via_env = _run_child(tmp_path / "env", """
        def test_never_runs():
            assert False
    """, "-p", "no:tools.pytest_clockshift", NEWSLENS_CLOCKSHIFT="+365d")
    assert via_env.returncode != 0, via_env.stdout
    combined = via_env.stdout + via_env.stderr
    assert "Blocking does NOT unshift" in combined
    assert "never_runs" not in via_env.stdout

    inert_block = _run_child(tmp_path / "inert", """
        def test_runs_on_the_real_clock():
            assert abs(_delta_seconds()) < 5
    """, "-p", "no:tools.pytest_clockshift")
    assert inert_block.returncode == 0, inert_block.stdout


def test_a_swap_from_the_outer_argv_is_refused_when_the_flag_was_not_parsed(
        tmp_path):
    """The r1 mirror refusal: pytest.main() inside a process whose OWN argv
    carries --clockshift.

    The import-time scan reads the process argv (nothing else exists that
    early); pytest.main()'s args are what argparse parses. When they disagree
    the swap is installed but no flag was parsed — before the guard, that ran
    SHIFTED under a pytest command line that never asked for it (truthfully
    reported, but a run nobody requested — gate NL-126b ruling on r1). The
    env form stays exempt as the documented flagless path, pinned green by
    test_the_env_form_and_the_flag_form_shift_identically.
    """
    tmp_path.mkdir(parents=True, exist_ok=True)
    (tmp_path / "test_inner.py").write_text(textwrap.dedent("""
        def test_never_runs():
            assert False
    """))
    (tmp_path / "pytest.ini").write_text(
        "[pytest]\naddopts = -p tools.pytest_clockshift\n")
    ini = str(tmp_path / "pytest.ini")
    inner = str(tmp_path / "test_inner.py")
    script = textwrap.dedent(f"""
        import sys
        sys.argv = ["outer_prog", "--clockshift"]
        import pytest
        raise SystemExit(int(pytest.main(["-c", {ini!r}, {inner!r}])))
    """)
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=str(tmp_path), env=_child_env(),
        capture_output=True, text=True, timeout=120)
    assert result.returncode == 4, (result.returncode, result.stdout,
                                    result.stderr)
    combined = result.stdout + result.stderr
    assert "NOT parsed from this" in combined
    assert "never_runs" not in result.stdout


# ---------------------------------------------------------------------------
# 7. The header/summary echo — what makes a quoted report replayable
# ---------------------------------------------------------------------------

def test_the_header_echoes_the_shift_the_absolute_datetime_and_the_replay(tmp_path):
    """A report that quotes the header must replay exactly, and be readable.

    The absolute datetime matters as much as the offset: `+365d` alone leaves a
    reader computing dates by hand from a run whose date they do not know.
    """
    result = _run_child(tmp_path, """
        def test_ok():
            assert True
    """, "--clockshift", "--clockshift-by", "+30d")
    assert result.returncode == 0, result.stdout

    header = next(line for line in result.stdout.splitlines()
                  if line.startswith("clock: SHIFTED"))
    assert "+30d" in header
    assert "(via --clockshift)" in header
    assert "real " in header, "the real clock is printed beside the shifted one"

    # Compared as a DELTA, not as a `%Y-%m-%d` string. Asserting the expected
    # calendar date would make this test fail whenever the parent and the child
    # straddle a real UTC midnight — a fixture time bomb with a once-a-day fuse,
    # in the file whose subject is fixture time bombs.
    believed, real = (datetime.datetime.fromisoformat(stamp)
                      for stamp in _ISO_IN_HEADER.findall(header))
    assert abs((believed - real) - datetime.timedelta(days=30)) < \
        datetime.timedelta(seconds=5), header

    replay = next(line for line in result.stdout.splitlines()
                  if line.startswith("clock: replay"))
    assert "--clockshift --clockshift-by +30d" in replay
    assert "NEWSLENS_CLOCKSHIFT=+30d" in replay


def test_the_header_states_the_coverage_bound_so_a_green_leg_is_not_over_read(
        tmp_path):
    """The bound rides on the run itself, not only in a file nobody opens.

    A green third leg proves "no remaining Python-clock instance" and nothing
    more; the SQL clock (`date('now')`, `strftime('now')` defaults) cannot be
    moved by a process-clock shift at any horizon. Printing that where the
    result is read is what stops the over-reading.
    """
    result = _run_child(tmp_path, """
        def test_ok():
            assert True
    """, "--clockshift")
    assert "Python-clock class only" in result.stdout
    assert "NL-126d" in result.stdout


def test_the_terminal_summary_repeats_the_horizon(tmp_path):
    """Long runs scroll the header off; the summary is what a reader actually sees."""
    shifted = _run_child(tmp_path / "on", """
        def test_ok():
            assert True
    """, "--clockshift", "--clockshift-by", "+90d")
    assert "shifted-clock run — +90d" in shifted.stdout
    assert "--clockshift --clockshift-by +90d" in shifted.stdout

    inert = _run_child(tmp_path / "off", """
        def test_ok():
            assert True
    """)
    assert "shifted-clock run" not in inert.stdout


def test_the_two_legs_can_be_folded(tmp_path):
    """`--shuffle --clockshift` — the two-leg variant the convention permits.

    The plugins register through different mechanisms (conftest re-export vs
    `-p`), which is exactly the kind of asymmetry that breaks when combined, so
    it is tested rather than assumed. The child has no conftest, so the shuffle
    plugin is loaded by `-p` here; the coexistence claim under test is that both
    option groups and both headers survive in one run.
    """
    result = _run_child(tmp_path, """
        def test_ok():
            assert True
    """, "--shuffle", "--shuffle-seed", "126", "--clockshift",
        ini_addopts="-p tools.pytest_shuffle")
    assert result.returncode == 0, result.stdout
    assert "test order: SHUFFLED — seed 126" in result.stdout
    assert "clock: SHIFTED +365d" in result.stdout


# ---------------------------------------------------------------------------
# 8. The value grammar
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("spec,seconds", [
    ("+365d", 365 * DAY),
    ("365d", 365 * DAY),
    ("365", 365 * DAY),
    ("  +30d  ", 30 * DAY),
    ("1.5d", 1.5 * DAY),
    ("+12h", 12 * 3600.0),
    ("-12h", -12 * 3600.0),
    ("0.5h", 1800.0),
    ("-1", -DAY),
    ("0d", 0.0),
    ("+578d", 578 * DAY),
])
def test_the_grammar_parses(spec, seconds):
    assert pytest_clockshift.parse_shift(spec).total_seconds() == seconds


@pytest.mark.parametrize("spec", ["", "   ", "d", "h", "abc", "30x", "1d2h",
                                  "30 d", "++5d", "true", "yes", "on", "off"])
def test_the_grammar_refuses_what_it_cannot_mean(spec):
    """Refusal, never a guess. A misparsed horizon runs a leg nobody asked for
    and reports it under the name they did ask for."""
    with pytest.raises(ValueError):
        pytest_clockshift.parse_shift(spec)


def test_boolean_words_get_a_message_that_names_the_real_grammar():
    """`--clockshift-by true` is the predictable mistake; a bare number is DAYS,
    so guessing here would silently run a horizon nobody chose."""
    with pytest.raises(ValueError, match="takes a HORIZON, not a boolean"):
        pytest_clockshift.parse_shift("true")


def test_a_bad_spec_fails_the_run_rather_than_falling_back_to_a_default(tmp_path):
    result = _run_child(tmp_path, """
        def test_never_runs():
            assert False
    """, "--clockshift", "--clockshift-by", "banana")
    assert result.returncode != 0
    combined = result.stdout + result.stderr
    assert "not a clock shift" in combined
    assert "never runs" not in result.stdout


# ---------------------------------------------------------------------------
# 9. The coverage bound is IN the docstring — the law's own deliverable
# ---------------------------------------------------------------------------

def test_the_docstring_carries_the_coverage_bound():
    """Contract, not decoration: the gate's ruling requires the bound to live
    with the instrument, because the instrument is what a future reader opens
    when deciding what a green leg proved."""
    doc = pytest_clockshift.__doc__
    assert "COVERAGE BOUND" in doc
    assert "Python-clock class only" in doc
    for sql_clock in ("date('now')", "strftime(", "NL-126d"):
        assert sql_clock in doc
    assert "It does NOT mean" in doc


def test_the_docstring_carries_the_conversion_law():
    doc = pytest_clockshift.__doc__
    assert "THE CONVERSION LAW" in doc
    for name in ("fromtimestamp", "strptime", "combine", "fromisoformat",
                 "fromordinal"):
        assert name in doc
    assert "86" in doc and "71" in doc


def test_the_shuffle_plugin_usage_line_is_the_corrected_form():
    """NL-126 gate hygiene rider, carried here so it cannot silently regress.

    The `-p tools.pytest_shuffle` usage line was correct only while that plugin
    lived out of tree; after the residency move it double-registers and dies on
    `--shuffle already added`. QA lost a run to it on 2026-07-31. This file is
    the natural home for the check because the two plugins are now siblings
    that register by DIFFERENT mechanisms, and the difference is load-bearing.
    """
    doc = (PROTOTYPE_ROOT / "tools" / "pytest_shuffle.py").read_text()
    assert "pytest --shuffle" in doc
    assert "Do NOT pass `-p tools.pytest_shuffle`" in doc
