"""NL-17 M3 — WHICH INPUT ACTUALLY ARMS STEERING.

Origin, and it is the reason this file exists: M3 was dispatched to "arm" and
offered two forms — flip the code default at `config.py:203`, or the
`settings:` row. MEASURED, the first cannot arm the principal's record and the
second is his file to edit:

    YAML shape                          code default True -> loaded value
    settings names the key false          (HIS SHAPE)        False
    settings block exists, key ABSENT                        False
    no settings block at all                                 True
    settings names the key true                              True

His `sources.yaml` names the key `false` explicitly, and all four profiles name
it too — so flipping the dataclass default would arm NOTHING that exists while
silently arming any future config that ships no `settings:` block. That is the
worst shape available: ineffective where it was aimed, live where it was not.

These pins are CARRIED-INVARIANTS (the semantics are HEAD's, unchanged by M3).
They are retro-pins in the ENGINEERING.md sense, so each travels with a mutation
receipt proving its route reaches the code under test — see the report.
"""

from __future__ import annotations

import textwrap

import pytest

from newslens import config


def _load(tmp_path, yaml_text, name="sources.yaml"):
    p = tmp_path / name
    p.write_text(textwrap.dedent(yaml_text), encoding="utf-8")
    return config.load_sources(p)


def test_an_explicit_false_beats_any_code_default(tmp_path):
    """HIS SHAPE. `config.py:433-437` assigns from the settings mapping whenever
    that mapping exists, so the dataclass default at :203 is never consulted on
    this path. Flipping :203 cannot arm him."""
    cfg = _load(tmp_path, """
        settings:
          threads_steer_selection: false
        sources: []
    """)
    assert cfg.threads_steer_selection is False
    assert cfg.problems == []


def test_a_settings_block_that_omits_the_key_does_not_arm(tmp_path):
    """The second trap: `raw_settings.get("threads_steer_selection", False)`
    carries its OWN hard-coded False, so a settings block that simply never
    names the key stays dark regardless of the field default."""
    cfg = _load(tmp_path, """
        settings:
          tts_engine: kokoro
        sources: []
    """)
    assert cfg.threads_steer_selection is False


def test_no_settings_block_at_all_takes_the_code_default(tmp_path):
    """FIX LOOP 1, F-4 — the fourth shape, and the only one where flipping
    `config.py:203` DOES arm. It is the silent-hazard row of the docstring
    table: his file and all four profiles name the key, so a `:203` flip reaches
    nothing that exists today and everything that ships without a `settings:`
    block tomorrow.

    QA's finding: the flip WAS caught, but only by the non-boolean pin, and only
    because its refusal path happens to leave the dataclass default in place — a
    routing accident. A refuse-by-assigning-False refactor would have left the
    flip caught by ZERO pins. This pin names the shape directly.

    Fixture-file based, so unlike the deleted sixth pin it cannot go red when the
    principal arms his own file."""
    cfg = _load(tmp_path, """
        sources: []
    """)
    assert cfg.threads_steer_selection is config.SourcesConfig.threads_steer_selection
    assert cfg.threads_steer_selection is False


def test_the_settings_row_is_the_one_input_that_arms(tmp_path):
    """A6's design (config.py:200-202: "Principal-flippable in sources.yaml
    `settings:`"). One line, his file — and the same one line is the clean
    un-arm."""
    cfg = _load(tmp_path, """
        settings:
          threads_steer_selection: true
        sources: []
    """)
    assert cfg.threads_steer_selection is True


def test_a_non_boolean_refuses_rather_than_coercing(tmp_path):
    """`"yes"` is not true. A truthy-string coercion here would arm selection on
    a typo — the loudest possible failure for the quietest possible cause."""
    cfg = _load(tmp_path, """
        settings:
          threads_steer_selection: "yes"
        sources: []
    """)
    assert cfg.threads_steer_selection is False
    assert any("threads_steer_selection" in p for p in cfg.problems)


# A SIXTH PIN WAS WRITTEN AND DELETED, and the reason is worth more than the
# pin was. It claimed to assert that the principal's live sources.yaml is dark,
# via `paths.SOURCES_FILE` — but the autouse conftest sandbox redirects that to
# a pytest tmp file, so it read a fixture while reporting a fact about his
# record: a pin passing over a dead path (ENGINEERING.md's named failure; it
# survived mutation M3 for exactly that reason, which is how it was caught).
# Reaching around the sandbox to his real file would have been worse, twice
# over: the suite would depend on his local edits, and the pin would go RED the
# moment he ARMS — failing precisely when the system does the right thing. The
# live-file state is a REPORT receipt (measured read-only, quoted in the M3
# addendum), not a suite invariant.
