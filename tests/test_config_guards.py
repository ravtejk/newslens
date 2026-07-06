"""Guard-var validation: BUDGET_CAP_USD_PER_RUN and GENERATE_HOUR_LOCAL.

Contract: "rejects garbage values sanely" (implementer report; ENGINEERING.md
cost-guardrail rule). Both config.py (runtime) and doctor.py (report) validate
these — both are covered, since they are duplicated implementations that can
drift.

KNOWN-RED tests, deliberately failing to document confirmed BUG-1:
`float("nan")` and `float("inf")` pass the `<= 0` gate in BOTH implementations.
`nan` is the dangerous one — every later `cost > cap` comparison involving nan
is False, so the mandated budget abort would never fire, and the doctor
currently blesses it with a green check ("BUDGET_CAP_USD_PER_RUN = nan USD/run").
"""

from __future__ import annotations

import pytest

from newslens import config, doctor


# --- BUDGET_CAP_USD_PER_RUN: config.py ---------------------------------------

@pytest.mark.parametrize(
    "raw, expected",
    [("0.50", 0.50), ("2", 2.0), (" 0.25 ", 0.25), ("0.01", 0.01)],
)
def test_budget_cap_accepts_positive_numbers(raw, expected):
    assert config.budget_cap_usd_per_run({"BUDGET_CAP_USD_PER_RUN": raw}) == expected


@pytest.mark.parametrize("env", [{}, {"BUDGET_CAP_USD_PER_RUN": ""}, {"BUDGET_CAP_USD_PER_RUN": "  "}])
def test_budget_cap_unset_or_blank_uses_documented_default(env):
    # 0.25 since the M9 ruling (2026-07-06; was 0.50)
    assert config.budget_cap_usd_per_run(env) == 0.25


@pytest.mark.parametrize("raw", ["abc", "$1", "1,50", "0", "0.0", "-1", "-inf"])
def test_budget_cap_rejects_garbage_and_nonpositive(raw):
    with pytest.raises(ValueError):
        config.budget_cap_usd_per_run({"BUDGET_CAP_USD_PER_RUN": raw})


@pytest.mark.parametrize("raw", ["nan", "NaN", "inf", "Infinity"])
def test_BUG1_budget_cap_rejects_non_finite_values(raw):
    """KNOWN-RED (BUG-1): non-finite floats are garbage for a spend cap and
    must be rejected. Currently ACCEPTED — float('nan') <= 0 is False, so nan
    sails through, and nan disables every later cost-vs-cap comparison."""
    with pytest.raises(ValueError):
        config.budget_cap_usd_per_run({"BUDGET_CAP_USD_PER_RUN": raw})


# --- BUDGET_CAP_USD_PER_RUN: doctor's report ----------------------------------

def _guard_line(env, needle):
    results = doctor.check_optional_and_guards(env)
    matches = [r for r in results if needle in r.text]
    assert matches, f"no doctor line mentioning {needle!r}"
    return matches[0]


def test_doctor_flags_garbage_budget_cap_as_required_failing():
    line = _guard_line({"BUDGET_CAP_USD_PER_RUN": "abc"}, "BUDGET_CAP_USD_PER_RUN")
    assert line.status == doctor.FAIL
    assert "must be a positive number" in line.text


def test_doctor_passes_a_valid_budget_cap_at_or_below_default():
    line = _guard_line({"BUDGET_CAP_USD_PER_RUN": "0.20"}, "BUDGET_CAP_USD_PER_RUN")
    assert line.status == doctor.PASS
    assert "0.20" in line.text


def test_doctor_nudges_a_cap_pinned_above_the_recommended_default():
    # M9 ruling (2026-07-06): default cut to 0.25; an .env still pinning the
    # old 0.50 (or anything higher than default) gets a visible worth-a-look
    # nudge, never a silent pass and never a hard failure (it's the
    # principal's value to set).
    line = _guard_line({"BUDGET_CAP_USD_PER_RUN": "0.75"}, "BUDGET_CAP_USD_PER_RUN")
    assert line.status == doctor.WARN
    assert "0.75" in line.text and "0.25 recommended" in line.text


def test_doctor_reports_unset_budget_cap_as_default_info():
    line = _guard_line({}, "BUDGET_CAP_USD_PER_RUN")
    assert line.status == doctor.INFO
    assert "default 0.25" in line.text  # M9 ruling 2026-07-06 (was 0.50)


@pytest.mark.parametrize("raw", ["nan", "inf"])
def test_BUG1_doctor_must_not_bless_non_finite_budget_cap(raw):
    """KNOWN-RED (BUG-1, doctor variant): the doctor currently prints a green
    '✓ BUDGET_CAP_USD_PER_RUN = nan USD/run' for a value that would silently
    defeat the cost guardrail."""
    line = _guard_line({"BUDGET_CAP_USD_PER_RUN": raw}, "BUDGET_CAP_USD_PER_RUN")
    assert line.status == doctor.FAIL


# --- GENERATE_HOUR_LOCAL -------------------------------------------------------

@pytest.mark.parametrize("raw, expected", [("0", 0), ("6", 6), ("23", 23), (" 7 ", 7)])
def test_hour_accepts_0_through_23(raw, expected):
    assert config.generate_hour_local({"GENERATE_HOUR_LOCAL": raw}) == expected


@pytest.mark.parametrize("env", [{}, {"GENERATE_HOUR_LOCAL": ""}])
def test_hour_unset_uses_documented_default(env):
    assert config.generate_hour_local(env) == 6


@pytest.mark.parametrize("raw", ["24", "-1", "6.5", "abc", "6am", "1e1"])
def test_hour_rejects_garbage_and_out_of_range(raw):
    with pytest.raises(ValueError):
        config.generate_hour_local({"GENERATE_HOUR_LOCAL": raw})


def test_doctor_flags_out_of_range_hour_as_required_failing():
    line = _guard_line({"GENERATE_HOUR_LOCAL": "24"}, "GENERATE_HOUR_LOCAL")
    assert line.status == doctor.FAIL
    assert "0-23" in line.text


def test_doctor_passes_a_valid_hour():
    line = _guard_line({"GENERATE_HOUR_LOCAL": "7"}, "GENERATE_HOUR_LOCAL")
    assert line.status == doctor.PASS
    assert "07:00 local" in line.text


# --- GNEWS is informational either way -----------------------------------------

@pytest.mark.parametrize("env", [{}, {"GNEWS_API_KEY": "gn-something"}])
def test_gnews_key_is_never_required(env):
    line = _guard_line(env, "GNEWS_API_KEY")
    assert line.status == doctor.INFO
