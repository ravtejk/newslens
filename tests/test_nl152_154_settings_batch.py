"""NL-152 / NL-153 / NL-154 — the settings batch's behavioural contracts.

Every pin here is BORN RED at a62c1aa unless its docstring says
carried-invariant. The three rows:

  NL-152  scheduled generation, settable from the settings tab
  NL-153  the failure-morning door ("Read last generated edition")
  NL-154  generation_log retention

The sandbox fixtures are autouse (tests/conftest.py:714), so DATA_DIR,
SOURCES_FILE and the DB are per-test temporaries and nothing here can reach the
founder's files.

FIX LOOP 1 (2026-08-14) — the pins marked "MUTATION-HARDENED" were rewritten
after QA's 26-mutant campaign left 11 mutants alive against this file's 52
pins. Every one of those pins asserted something ADJACENT to its own docstring's
claim (a source grep for a property about the filesystem; a return value for a
property about a record; one branch of a five-branch ladder). The claim did not
change; what changed is that the pin now falsifies it.
"""

import json
import os
import re
import sqlite3
import stat
from html import unescape
from pathlib import Path

import pytest

from newslens import config, generate, paths, schedule, server, webui


# ===========================================================================
# NL-152 — persistence: the hour lives in sources.yaml `settings:`
# ===========================================================================

_YAML = """\
settings:
  # a comment that must survive every edit
  threads_steer_selection: true
  tts_engine: kokoro

sources:
  - name: Example
    rss_url: https://example.com/feed
interests:
  broad:
    - Markets
"""


def _write_sources(text: str = _YAML):
    paths.SOURCES_FILE.write_text(text, encoding="utf-8")
    return paths.SOURCES_FILE


def _with_hour(hour) -> str:
    return _YAML.replace("tts_engine: kokoro",
                         f"tts_engine: kokoro\n  generate_hour: {hour}")


@pytest.fixture(autouse=True)
def _no_agent_lands_in_his_real_home():
    """MODULE TRIPWIRE. This is the only file in the suite that WRITES a launchd
    agent, and `~/Library/LaunchAgents` is the one location the org's real rule
    ("the org never installs the agent — his hands do") has no mechanism behind.

    Written after this fix loop proved the hazard on live hardware: running the
    F-14 mutant (the seam removed) made `_install_agent` write a real, armed
    plist into the founder's home — inert only because launchd had not loaded it
    yet. A seam is not a mechanism until something notices when it is gone. This
    compares his directory before and against after, so his machine's state
    never decides a verdict — only a CHANGE to it does."""
    lib = Path.home() / "Library" / "LaunchAgents"
    before = sorted(p.name for p in lib.glob("*")) if lib.exists() else None
    yield
    after = sorted(p.name for p in lib.glob("*")) if lib.exists() else None
    assert after == before, (
        "a test reached the founder's real ~/Library/LaunchAgents — the "
        f"sandbox seam (paths.home_dir) is not holding: {before} -> {after}")


def _install_agent(hour=None, body=None):
    """Put a launchd agent file where `schedule.status()` will find it.

    Reaches the SANDBOX home, not his: `plist_path()` resolves through
    `paths.home_dir()` (QA F-14), which is the sandbox root while
    NEWSLENS_DATA_DIR redirects. Before that seam this helper could not have
    been written without either a real write under his `~` or an explicit
    `home=` at every call site.

    THE ASSERT IS NOT DECORATION. A launchd agent written into his real home is
    the one artifact in this codebase that can spend money unattended: macOS
    auto-loads `~/Library/LaunchAgents` at login, and this plist carries a
    StartCalendarInterval. So the helper REFUSES rather than trusting the seam —
    proven necessary, not hypothetical (see the fixture above)."""
    p = schedule.plist_path()
    assert paths.home_dir() != Path.home(), \
        "REFUSING to write a launchd agent: paths.home_dir() is his REAL home"
    assert str(p).startswith(str(paths.home_dir())), p
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body if body is not None
                 else schedule.render_plist(hour=hour,
                                            binary=schedule.newslens_bin()),
                 encoding="utf-8")
    return p


def _value_lines(html=None):
    """The rendered `.settings-row-value` texts, in order: [schedule, hour]."""
    return re.findall(r'class="settings-row-value">([^<]*)<',
                      html if html is not None else server._render_schedule_rows())


def test_settings_generate_hour_parses():
    _write_sources(_YAML.replace("tts_engine: kokoro",
                                 "tts_engine: kokoro\n  generate_hour: 9"))
    cfg = config.load_sources()
    assert cfg.problems == []
    assert cfg.generate_hour == 9


def test_absent_generate_hour_is_none_not_midnight():
    """None and 0 are different facts: absent means "the env layer decides",
    midnight means he chose 00:00. A default of 0 would collapse them."""
    _write_sources()
    assert config.load_sources().generate_hour is None


@pytest.mark.parametrize("raw", ["24", "-1", "'6'", "6.5", "true"])
def test_out_of_range_generate_hour_is_a_loud_problem(raw):
    """`true` is in this list deliberately: YAML parses it as a bool and bool
    subclasses int, so a naive isinstance check would land it as hour 1.

    ASSERTS THE RANGE VALIDATOR'S OWN SENTENCE, not the substring
    "generate_hour". The looser form passed at a62c1aa for the WRONG REASON —
    the key was UNKNOWN there, so `settings: unknown key \\`generate_hour\\``
    satisfied it and the pin was green over a validator that did not exist.
    That is the dead-path class the org names (gate ruling 2026-08-03); the
    exact message is the only text the new validator can produce."""
    _write_sources(_YAML.replace("tts_engine: kokoro",
                                 f"tts_engine: kokoro\n  generate_hour: {raw}"))
    cfg = config.load_sources()
    assert "settings.generate_hour must be a whole hour 0-23" in cfg.problems, \
        cfg.problems
    assert not any("unknown key" in p for p in cfg.problems), cfg.problems


def test_settings_hour_beats_env():
    """THE DEAD-ON-ARRIVAL PIN. His own .env line 39 sets GENERATE_HOUR_LOCAL,
    so an env-wins layering would ship a settings control that silently does
    nothing on the one machine it was built for."""
    _write_sources(_YAML.replace("tts_engine: kokoro",
                                 "tts_engine: kokoro\n  generate_hour: 9"))
    hour, source = config.generate_hour_resolved({"GENERATE_HOUR_LOCAL": "6"})
    assert (hour, source) == (9, config.HOUR_SOURCE_SETTINGS)


def test_env_still_decides_when_settings_is_silent():
    _write_sources()
    assert config.generate_hour_resolved({"GENERATE_HOUR_LOCAL": "7"}) == (
        7, config.HOUR_SOURCE_ENV)


def test_default_when_neither_layer_speaks():
    _write_sources()
    assert config.generate_hour_resolved({}) == (
        config.DEFAULT_GENERATE_HOUR_LOCAL, config.HOUR_SOURCE_DEFAULT)


def test_a_typod_env_var_does_not_brick_a_settings_hour():
    _write_sources(_YAML.replace("tts_engine: kokoro",
                                 "tts_engine: kokoro\n  generate_hour: 9"))
    assert config.generate_hour_resolved({"GENERATE_HOUR_LOCAL": "banana"})[0] == 9


# ===========================================================================
# NL-152 — the writer: line surgery, comments survive, validated
# ===========================================================================

def test_settings_set_hour_writes_and_keeps_his_comments():
    path = _write_sources()
    ok, _ = server.settings_set_hour(9)
    assert ok
    text = path.read_text(encoding="utf-8")
    assert "generate_hour: 9" in text
    assert "a comment that must survive every edit" in text
    assert config.load_sources().generate_hour == 9


def test_settings_set_hour_replaces_rather_than_duplicates():
    path = _write_sources()
    server.settings_set_hour(9)
    server.settings_set_hour(21)
    text = path.read_text(encoding="utf-8")
    assert text.count("generate_hour:") == 1
    assert config.load_sources().generate_hour == 21


def test_the_hour_rewrite_keeps_the_trailing_comment_on_that_line():
    """GATE G-1 (2026-08-14), FIX-4. The rewrite arm replaced the WHOLE line —
    `lines[i] = f"{indent}generate_hour: {hour}"` — so a note he had written
    beside the value disappeared under a "generation hour set to 09:00"
    success message. Measured by the gate on his own file's shape.

    THE COMMENT-SURVIVAL CONTRACT WAS ALREADY THE ROW'S HEADLINE CLAIM: line
    surgery exists here precisely so his annotations live through a UI write
    (`server.py:1370`). It held for every OTHER line in the file and failed on
    the one line the product itself rewrites — the same silent-loss-under-
    success class as QA F-7, on the only line where this code is the writer.

    BOUND, stated once: the fragment preserved is a ` #…` comment tail, and it
    is NOT preserved when the value before it carries a quote (a `#` inside a
    quoted scalar is data, not a comment, and re-emitting it as a comment would
    corrupt the value)."""
    path = _write_sources(
        _YAML.replace("  tts_engine: kokoro",
                      "  tts_engine: kokoro\n"
                      "  generate_hour: 6  # 6am, before the school run"))
    ok, msg = server.settings_set_hour(9)
    assert ok, msg
    text = path.read_text(encoding="utf-8")
    assert re.search(r"^  generate_hour: 9  # 6am, before the school run$",
                     text, re.M), text
    assert text.count("generate_hour:") == 1
    assert config.load_sources().generate_hour == 9

    # THE BOUND, same pin so preservation cannot grow into corruption: a `#`
    # inside a quoted scalar is data, and it leaves with the value it belonged
    # to rather than being promoted to a comment on the rewritten line.
    path = _write_sources(
        _YAML.replace("  tts_engine: kokoro",
                      '  tts_engine: kokoro\n  generate_hour: "6 # data"'))
    ok, _ = server.settings_set_hour(9)
    assert ok
    quoted = path.read_text(encoding="utf-8")
    assert "# data" not in quoted, quoted
    assert config.load_sources().generate_hour == 9


@pytest.mark.parametrize("bad", [24, -1, "six", None])
def test_settings_set_hour_refuses_out_of_range_before_writing(bad):
    path = _write_sources()
    before = path.read_text(encoding="utf-8")
    ok, msg = server.settings_set_hour(bad)
    assert not ok
    assert path.read_text(encoding="utf-8") == before
    assert "whole hour" in msg


def test_settings_set_hour_refuses_a_file_with_no_settings_block():
    path = _write_sources("sources:\n  - name: X\n    rss_url: https://x/f\n"
                          "interests:\n  broad:\n    - Markets\n")
    before = path.read_text(encoding="utf-8")
    ok, _ = server.settings_set_hour(9)
    assert not ok
    assert path.read_text(encoding="utf-8") == before


# ===========================================================================
# NL-152 — one state, one truth: the toggle IS the kill switch
# ===========================================================================

def test_the_toggle_is_the_kill_switch_file():
    """No second state. `set_paused` must move the very file the 6am ladder
    consults, not a parallel settings key that can disagree with it."""
    assert schedule.set_paused(True) is True
    assert schedule.kill_switch_path().exists()
    assert schedule.schedule_paused() is True
    assert schedule.set_paused(False) is False
    assert not schedule.kill_switch_path().exists()


def test_set_paused_is_idempotent_on_both_arms():
    assert schedule.set_paused(True) is True
    assert schedule.set_paused(True) is True
    assert schedule.set_paused(False) is False
    assert schedule.set_paused(False) is False


def test_set_paused_returns_a_measurement_and_not_an_echo():
    """MUTATION-HARDENED (fix loop 1 — the unpinned `set_paused` contract).

    `set_paused` returns `schedule_paused()` RE-READ FROM DISK, never the
    argument it was handed, and the whole 500 arm of `_api_schedule_pause`
    derives from that: it compares the measurement against the request and
    refuses when they differ. Nothing pinned it, so `return paused` — a
    one-token mutant — survived, and with it a toggle that reports success on a
    data dir where nothing moved. A picture of a switch.

    The unwritable-dir arm is the only place the two answers can disagree, so it
    is the only place that can falsify the claim."""
    d = paths.DATA_DIR
    d.mkdir(parents=True, exist_ok=True)
    mode = d.stat().st_mode
    os.chmod(d, stat.S_IRUSR | stat.S_IXUSR)
    try:
        assert schedule.set_paused(True) is False, \
            "returned the argument, not the state that now holds"
        assert schedule.schedule_paused() is False
        assert not schedule.kill_switch_path().exists()
    finally:
        os.chmod(d, mode)
    # ...and the same measurement is what keeps the API honest.
    assert schedule.set_paused(True) is True


def test_a_paused_schedule_still_declines_before_spending():
    """The toggle composes with the ladder rather than bypassing it —
    carried-invariant (NL-146) re-pinned through the NEW writer."""
    schedule.set_paused(True)
    out = schedule.run_scheduled(
        date="2026-08-14",
        runner=lambda **kw: pytest.fail("a paused schedule must not spend"),
        sleeper=lambda s: None)
    assert out["outcome"] == schedule.FIRED_PAUSED
    assert out["charged_usd"] == 0.0


# ===========================================================================
# NL-152 — the plist reality
# ===========================================================================

def test_render_plist_bakes_the_settings_hour():
    _write_sources(_YAML.replace("tts_engine: kokoro",
                                 "tts_engine: kokoro\n  generate_hour: 9"))
    xml = schedule.render_plist(env={"GENERATE_HOUR_LOCAL": "6"},
                                binary=schedule.newslens_bin())
    assert "<integer>9</integer>" in xml
    assert "<integer>6</integer>" not in xml


def test_status_reports_which_layer_decided():
    _write_sources(_YAML.replace("tts_engine: kokoro",
                                 "tts_engine: kokoro\n  generate_hour: 9"))
    st = schedule.status(env={"GENERATE_HOUR_LOCAL": "6"})
    assert st["configured_hour"] == 9
    assert st["hour_source"] == config.HOUR_SOURCE_SETTINGS


def test_mismatch_line_names_settings_when_settings_won(tmp_path):
    """THE NEVER-SILENT PIN. An installed agent baked at 06:00 against a
    settings value of 09:00 must say so, and must send him to the surface that
    actually decided — not to a .env that is no longer winning."""
    _write_sources(_YAML.replace("tts_engine: kokoro",
                                 "tts_engine: kokoro\n  generate_hour: 9"))
    home = tmp_path / "home"
    target = schedule.plist_path(home)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(schedule.render_plist(hour=6,
                                            binary=schedule.newslens_bin()),
                      encoding="utf-8")
    tags = dict((t, s) for t, s in schedule.status_lines_tagged(home=home))
    assert schedule.LINE_MISMATCH in tags
    line = tags[schedule.LINE_MISMATCH]
    assert "your Settings say 09:00" in line
    assert "fires at 06:00" in line


def test_reinstall_commands_bootout_before_bootstrap(tmp_path):
    """launchd refuses to bootstrap a label that is already loaded, so a
    sequence missing the bootout fails on the one machine that has the feature
    working."""
    cmds = schedule.reinstall_commands(tmp_path / "home")
    assert len(cmds) == 2
    assert cmds[0].startswith("newslens schedule plist > ")
    assert cmds[1].index("bootout") < cmds[1].index("bootstrap")


def test_the_fire_path_never_consults_the_hour():
    """CARRIED-INVARIANT (BORN-GREEN, labelled): this was already true at
    a62c1aa and the batch deliberately keeps it true.

    SCOUTED AND PINNED: `run_scheduled` takes no hour and reads none. That is
    why a settings change needs a re-render rather than a fire-time lookup — a
    fire-time check could only ever DECLINE (launchd has already decided the
    process exists), trading a visible disagreement for a silent stoppage. The
    pin exists so a later fix-loop cannot quietly "improve" this into the
    decline-shaped bug.

    MUTATION-HARDENED (fix loop 1 — mutant M15). The pin WAS
    `"generate_hour" not in inspect.getsource(run_scheduled)`, and a grep is not
    this property: a real fire-time consult spelled
    `status(env=env)["configured_hour"]` walks straight past it, and so does any
    delegation into a helper. The property is BEHAVIOURAL — with a settings hour
    that disagrees with the wall clock the run must still fire — so it is now
    asserted behaviourally, and the source-shape checks are kept only as the
    cheap second net they always were."""
    import inspect
    _write_sources(_with_hour((config.DEFAULT_GENERATE_HOUR_LOCAL + 7) % 24))
    fired = {"n": 0}

    def runner(**kw):
        fired["n"] += 1
        return {"status": "ok", "total_usd": 0.0}

    out = schedule.run_scheduled(date="2026-08-14", runner=runner,
                                 sleeper=lambda s: None)
    assert fired["n"] == 1, f"the fire declined itself over the hour: {out}"
    assert out["outcome"] == schedule.FIRED_PUBLISHED, out
    assert "hour" not in str(out.get("outcome", "")).lower()
    src = inspect.getsource(schedule.run_scheduled)
    assert "generate_hour" not in src
    assert "hour" not in inspect.signature(schedule.run_scheduled).parameters


# ===========================================================================
# NL-152 — the settings rows render
# ===========================================================================

def test_settings_rows_render_the_toggle_and_the_hour():
    """MUTATION-HARDENED (fix loop 1 — mutant M25's shape, applied to the
    toggle). The routing strings were asserted as loose substrings over the
    whole fragment, so a control with NO handler and the string parked anywhere
    else in the markup passed. Both handlers are now bound to the ELEMENT that
    is supposed to carry them."""
    _write_sources(_with_hour(9))
    html = server._render_schedule_rows()
    toggle = re.search(r"<div([^>]*id=\"schedule-toggle\"[^>]*)>", html, re.S)
    assert toggle, html
    tattrs = toggle.group(1)
    assert 'role="switch"' in tattrs
    assert 'onclick="toggleSchedule(this)"' in tattrs
    assert "aria-label=" in tattrs and "tabindex=" in tattrs
    change = re.search(r"<button([^>]*)>Change</button>", html, re.S)
    assert change, html
    assert "openScheduleHour(9, this)" in change.group(1), change.group(1)
    assert "09:00 local" in html


def test_the_toggle_renders_the_state_the_machine_is_actually_in():
    """MUTATION-HARDENED (fix loop 1 — the unpinned toggle wiring). Only the
    PAUSED arm was pinned, so a toggle wired permanently OFF passed: it would
    have rendered "off" over a schedule that fires at 6am, which is the same
    picture-of-a-switch failure `set_paused`'s measurement return exists to
    prevent. Both arms, one test — a constant cannot satisfy both."""
    _write_sources()
    paths.DATA_DIR.mkdir(parents=True, exist_ok=True)
    on = server._render_schedule_rows()
    assert 'aria-checked="true"' in on
    assert "Paused" not in on
    schedule.set_paused(True)
    off = server._render_schedule_rows()
    assert 'aria-checked="false"' in off
    assert "Paused" in off
    schedule.set_paused(False)
    assert 'aria-checked="true"' in server._render_schedule_rows()


def test_the_value_line_says_a_different_thing_in_every_state():
    """MUTATION-HARDENED (fix loop 1 — mutant M14). The value line is a
    five-branch state ladder and the only thing pinned was the literal word
    "Paused", so collapsing every other branch onto one string killed nothing.
    Each state the ladder can reach must be distinguishable from every other —
    that is the entire reason the ladder exists rather than one sentence.

    Reachable in-suite only because `plist_path()` now has a sandbox seam
    (QA F-14); before it, an installed-agent state could not be staged without
    writing under his real home."""
    _write_sources(_with_hour(9))
    paths.DATA_DIR.mkdir(parents=True, exist_ok=True)
    seen = {}

    seen["not-installed"] = _value_lines()[0]
    schedule.set_paused(True)
    seen["paused-and-not-installed"] = _value_lines()[0]
    _install_agent(hour=9)
    seen["paused-and-installed"] = _value_lines()[0]
    schedule.set_paused(False)
    seen["installed-and-agreeing"] = _value_lines()[0]
    _install_agent(hour=6)
    seen["installed-but-stale"] = _value_lines()[0]
    _install_agent(body="not a plist at all")
    seen["installed-but-unreadable"] = _value_lines()[0]

    assert len(set(seen.values())) == len(seen), seen
    assert "Paused" in seen["paused-and-installed"]
    assert "Paused" in seen["paused-and-not-installed"]
    assert "Paused" not in seen["installed-and-agreeing"]
    assert "06:00" in seen["installed-but-stale"]


def test_schedule_rows_use_no_new_css_classes():
    """DESIGN BOUND: the batch introduces no component vocabulary. Every class
    on these rows already exists in webui.py's stylesheet.

    MUTATION-HARDENED (fix loop 1 — QA F-12). This pin BOUND `css` and never
    read it: it checked the rendered classes against a hand-written allowlist,
    so a class in the allowlist but absent from the stylesheet passed, and so
    would a typo shared by both. It now asserts against the stylesheet the
    sentence is about. (Its old fallback arm `webui.page_shell.__doc__` was
    itself dead — `webui` has no `page_shell` — so the branch would have raised
    AttributeError the day `CSS` was renamed.)"""
    _write_sources(_with_hour(9))
    _install_agent(hour=6)      # the widest markup: the mismatch arm too
    html = server._render_schedule_rows()
    flat = {c for group in re.findall(r'class="([^"]+)"', html)
            for c in group.split()}
    assert flat, "no classes found — the renderer changed shape"
    assert flat <= {"settings-row", "settings-row-main", "settings-row-label",
                    "settings-row-value", "settings-row-action",
                    "toggle-switch"}, flat
    missing = sorted(c for c in flat if f".{c}" not in webui.CSS)
    assert not missing, f"classes with no stylesheet rule: {missing}"


def test_the_mismatch_row_carries_a_route_back_to_the_two_commands():
    """QA F-13. The value line says "re-install to move it" on EVERY page load,
    but the two commands existed only on the save response — so once that popup
    closed, the row was an instruction with no route to the thing it instructs
    him to run. The commands ride the button that carries the handler (not a
    parked <span> — mutant M25's shape), come from
    `schedule.reinstall_commands()` rather than a second composition, and the
    editor that the button opens actually reads them."""
    _write_sources(_with_hour(9))
    _install_agent(hour=6)
    html = server._render_schedule_rows()
    attrs = re.search(r"<button([^>]*)>Change</button>", html, re.S).group(1)
    assert "openScheduleHour(9, this)" in attrs
    raw = re.search(r'data-reinstall="([^"]*)"', attrs)
    assert raw, attrs
    cmds = json.loads(unescape(raw.group(1)))
    assert cmds == schedule.reinstall_commands(), cmds
    assert cmds[1].index("bootout") < cmds[1].index("bootstrap")
    assert 'data-plist-hour="06"' in attrs
    # the wiring half: the editor consumes the attribute rather than ignoring it
    assert "data-reinstall" in webui.JS
    assert "showReinstall(" in webui.JS


def test_no_commands_offered_when_there_is_nothing_to_re_install():
    """v4 affordance-absence law, and the same bound the save response keeps:
    telling a reader with no disagreement to `launchctl bootout` a label is a
    command that fails in his terminal for no reason."""
    _write_sources(_with_hour(9))
    assert "data-reinstall" not in server._render_schedule_rows()
    _install_agent(hour=9)
    assert "data-reinstall" not in server._render_schedule_rows()


def test_the_plist_path_is_sandboxed_from_his_real_home(tmp_path):
    """QA F-14. `plist_path()` resolved `Path.home()` directly, so every caller
    that passes no `home` — `status()`, and through it the settings rows and the
    doctor's schedule check — stat-ed the founder's REAL
    ~/Library/LaunchAgents from inside the suite. Read-only, so nothing of his
    was at risk; but a verdict that depends on whether he happens to have the
    agent installed is the v7-M1 pinhole class recurring.

    NO NEW ENV VAR: the seam rides the existing NEWSLENS_DATA_DIR redirection,
    the way DB_PATH does — one variable sandboxes them all."""
    assert paths.home_dir() == tmp_path
    assert paths.home_dir() != Path.home()
    p = schedule.plist_path()
    assert p == tmp_path / "Library" / "LaunchAgents" / schedule.PLIST_NAME
    assert str(Path.home()) not in str(p)
    assert schedule.status()["plist_path"] == str(p)
    # an explicit `home` still wins, so every existing caller is unchanged
    assert schedule.plist_path(tmp_path / "elsewhere").parent.parent.parent \
        == tmp_path / "elsewhere"


# ===========================================================================
# NL-153 — the failure-morning door
# ===========================================================================

_FAILED = {"date": "2026-08-14", "trigger": schedule.TRIGGER_SCHEDULED,
           "status": "failed", "retryable": True, "total_usd": 0.0}


def test_the_button_carries_his_exact_copy():
    note = server._scheduled_failure_note(
        _FAILED, {"date": "2026-08-11", "human": "Tuesday, August 11"})
    assert "Read last generated edition" in note


def test_the_button_makes_the_editions_real_date_unmissable():
    """NL-11's law. The label and the date travel together, in both human and
    ISO form, BEFORE the tap — an older edition is never dressed as current."""
    note = server._scheduled_failure_note(
        _FAILED, {"date": "2026-08-11", "human": "Tuesday, August 11"})
    assert "Tuesday, August 11" in note
    assert "2026-08-11" in note
    assert note.index("2026-08-11") < note.index("Read last generated edition")


def test_the_button_routes_to_that_edition():
    """MUTATION-HARDENED (fix loop 1 — mutant M25). The assertion was a loose
    substring over the WHOLE note, so an inert button with the routing string
    parked on a hidden <span> passed it — a button that looks like the way back
    to his last edition and does nothing. The handler must be bound to the
    BUTTON ELEMENT, and the function it names must exist in the shipped JS."""
    note = server._scheduled_failure_note(
        _FAILED, {"date": "2026-08-11", "human": "Tuesday, August 11"})
    m = re.search(r"<button([^>]*)>Read last generated edition</button>",
                  note, re.S)
    assert m, note
    assert "openEdition('2026-08-11', event)" in m.group(1), m.group(1)
    assert "function openEdition" in webui.JS


def test_no_button_when_there_is_nothing_to_offer():
    """v4 affordance-absence law: a button whose only outcome is an empty page
    is worse than no button."""
    note = server._scheduled_failure_note(_FAILED, None)
    assert "This morning's scheduled edition didn't finish" in note
    assert "Read last generated edition" not in note


def test_the_note_is_still_silent_on_every_other_case():
    """Carried-invariant (NL-146): interactive failures own the error panel."""
    last = {"date": "2026-08-11", "human": "Tuesday, August 11"}
    assert server._scheduled_failure_note(
        {**_FAILED, "trigger": schedule.TRIGGER_INTERACTIVE}, last) == ""
    assert server._scheduled_failure_note(
        {**_FAILED, "status": "ok"}, last) == ""
    assert server._scheduled_failure_note(None, last) == ""


def test_quiet_register_no_alert_role_no_colour():
    """MUTATION-HARDENED (fix loop 1 — mutant M18). `role="alert"` is one
    spelling: `role="alertdialog"` slipped past, and so did a loud red button,
    because the register was checked over the note's prose and never on the
    BUTTON. The trust-quiet claim is about the control."""
    note = server._scheduled_failure_note(
        _FAILED, {"date": "2026-08-11", "human": "Tuesday, August 11"})
    attrs = re.search(r"<button([^>]*)>Read last generated edition</button>",
                      note, re.S).group(1)
    assert 'class="cta-quiet"' in attrs, attrs
    assert "role=" not in attrs, attrs
    assert 'role="alert"' not in note
    assert "danger" not in note
    assert "color:" not in note and "background:" not in note
    assert 'class="empty-note"' in note


def _seed_edition(con, date, readable=True):
    con.execute(
        "INSERT INTO briefings (date, narrative_text) VALUES (?, ?)",
        (date, ("# X\n\n---\n\n**Headline**\n\nlede\n\n---\n\n*footer*"
                if readable else "")))
    con.commit()


def test_last_generated_edition_never_offers_today():
    """The note's own sentence says today did not finish; a button resolving to
    today would contradict it."""
    from newslens import db
    db.migrate()
    con = db.connect()
    _seed_edition(con, "2026-08-14")
    _seed_edition(con, "2026-08-11")
    got = server._last_generated_edition(con, "2026-08-14")
    con.close()
    assert got is not None
    assert got["date"] == "2026-08-11"


def test_last_generated_edition_skips_unreadable_rows():
    """A row exists from the RANK stage with nothing behind it; offering it
    opens an empty page on exactly the morning this note exists for."""
    from newslens import db
    db.migrate()
    con = db.connect()
    _seed_edition(con, "2026-08-13", readable=False)
    _seed_edition(con, "2026-08-11", readable=True)
    got = server._last_generated_edition(con, "2026-08-14")
    con.close()
    assert got is not None and got["date"] == "2026-08-11"


# ===========================================================================
# NL-154 — retention
# ===========================================================================

def _fill_log(runs=200, blob=24000, mode="w"):
    live = generate.log_file()
    live.parent.mkdir(parents=True, exist_ok=True)
    with live.open(mode, encoding="utf-8") as fh:
        for i in range(runs):
            fh.write(json.dumps({"stage": "analysis", "n": i}) + "\n")
            fh.write(json.dumps({"date": f"2026-01-{i % 28 + 1:02d}",
                                 "status": "ok", "total_usd": 1.0,
                                 "blob": "x" * blob}) + "\n")
            if i % 10 == 0:
                fh.write(json.dumps({"schedule": "published",
                                     "date": "2026-01-01"}) + "\n")
    return live


def test_rotation_moves_bytes_and_never_deletes_them():
    """THE CENTRAL PIN. The union of the archive and the live file must be the
    original file, exactly, in order — no line lost, no line duplicated."""
    live = _fill_log()
    before = live.read_text(encoding="utf-8").splitlines()
    seg = generate.rotate_log_if_needed()
    assert seg is not None and seg.exists()
    assert (seg.read_text(encoding="utf-8").splitlines()
            + live.read_text(encoding="utf-8").splitlines()) == before


def test_rotation_retains_the_floor_exactly():
    _fill_log()
    generate.rotate_log_if_needed()
    live_runs = sum(1 for ln in generate.log_file()
                    .read_text(encoding="utf-8").splitlines()
                    if generate._safe_is_run(ln))
    assert live_runs == generate.LOG_RETAIN_RUNS


def test_the_retained_floor_covers_the_reports_screen():
    """The reports screen shows 30; retention keeps 60. The screen must never
    be able to outrun the floor."""
    assert generate.LOG_RETAIN_RUNS >= 2 * server._RUNLOG_MAX_ROWS


def test_no_rotation_below_the_floor():
    """A file that is huge but holds fewer runs than the floor is left alone —
    rotation protects the retention contract rather than the size target."""
    _fill_log(runs=10, blob=600000)
    assert generate.log_file().stat().st_size > generate.LOG_MAX_BYTES
    assert generate.rotate_log_if_needed() is None
    assert generate.log_archives() == []


def test_no_rotation_under_the_size_cap():
    _fill_log(runs=5)
    assert generate.rotate_log_if_needed() is None


def test_segments_are_named_as_archives_and_numbered_in_cut_order():
    _fill_log()
    generate.rotate_log_if_needed()
    _fill_log(runs=200)
    generate.rotate_log_if_needed()
    names = [p.name for p in generate.log_archives()]
    assert names == ["generation_log.archive-0001.jsonl",
                     "generation_log.archive-0002.jsonl"]
    assert all("archive" in n for n in names)


def test_the_run_count_still_spans_the_archives():
    """NO SILENT SHRINK. The settings row quotes this number at him; a
    live-only count would have dropped it the morning after the first cut."""
    _fill_log()
    _, total_before = server._run_log_entries()
    generate.rotate_log_if_needed()
    _, total_after = server._run_log_entries()
    assert total_after == total_before == 200


def test_the_reports_screen_still_renders_its_rows_after_rotation():
    _fill_log()
    generate.rotate_log_if_needed()
    rows, _ = server._run_log_entries()
    assert len(rows) == server._RUNLOG_MAX_ROWS
    assert all(r.get("stage") is None for r in rows)
    assert all(r.get(schedule.SCHEDULE_LINE_KEY) is None for r in rows)


def test_log_entry_for_falls_back_into_the_archive():
    """An edition deep in the archive screen must keep its structured stories
    rather than degrading to the legacy narrative parse."""
    live = generate.log_file()
    live.parent.mkdir(parents=True, exist_ok=True)
    with live.open("w", encoding="utf-8") as fh:
        fh.write(json.dumps({"date": "2025-01-01", "status": "ok",
                             "stories": [{"headline": "old"}],
                             "blob": "x" * 24000}) + "\n")
        for i in range(200):
            fh.write(json.dumps({"date": f"2026-03-{i % 28 + 1:02d}",
                                 "status": "ok", "blob": "x" * 24000}) + "\n")
    generate.rotate_log_if_needed()
    # the old date is now in the archive, not the live file
    assert "2025-01-01" not in generate.log_file().read_text(encoding="utf-8")
    entry = server._log_entry_for("2025-01-01")
    assert entry is not None and entry["stories"] == [{"headline": "old"}]


def test_the_schedule_audit_trail_survives_rotation():
    """NL-146 forensics: `last_fire` reads the newest fire line, and rotation
    must never truncate the schedule's own record out from under it."""
    _fill_log()
    generate.log_file().open("a", encoding="utf-8").write(
        json.dumps({"schedule": schedule.FIRED_PUBLISHED,
                    "date": "2026-08-14", "ts": "2026-08-14T06:00:00Z"}) + "\n")
    generate.rotate_log_if_needed()
    lf = schedule.last_fire()
    assert lf is not None and lf["date"] == "2026-08-14"


def test_diagnose_totals_span_the_archives():
    """A live-only total would silently reset his lifetime spend history."""
    from newslens import diagnose
    _fill_log()
    before, _ = diagnose._load_entries()
    generate.rotate_log_if_needed()
    after, _ = diagnose._load_entries()
    assert len(after) == len(before)
    assert sum(e.get("total_usd") or 0 for e in after) == 200.0


def test_the_segment_concatenation_is_rotation_invariant():
    """The property readerserve's session delta leans on: rotation moves lines
    without reordering them, so the concatenation is unchanged across a cut."""
    _fill_log()
    before = "".join(p.read_text(encoding="utf-8")
                     for p in generate.log_segments())
    generate.rotate_log_if_needed()
    after = "".join(p.read_text(encoding="utf-8")
                    for p in generate.log_segments())
    assert after == before


def test_log_generation_rotates_on_its_way_in():
    """The wiring pin: rotation is not a verb somebody has to remember to
    call — it rides the one funnel every fat line goes through."""
    _fill_log()
    assert generate.log_archives() == []
    generate.log_generation({"date": "2026-08-14", "status": "ok"})
    assert len(generate.log_archives()) == 1


def test_rotation_never_raises_into_a_generate(monkeypatch):
    """A ~30-minute pipeline that already succeeded must not lose its record
    because housekeeping hit a full disk.

    MUTATION-HARDENED (fix loop 1 — mutant M20). The pin asserted only that the
    call RETURNED None, so a failure arm that truncated the live log to zero
    bytes passed it — destroying the exact record the docstring says must
    survive, and reporting "nothing needed moving" while it did. The return
    value is the small half of the contract; the RECORD is the contract."""
    live = _fill_log()
    before = live.read_bytes()

    def boom(*a, **k):
        raise OSError("disk full")

    monkeypatch.setattr("pathlib.Path.write_text", boom)
    assert generate.rotate_log_if_needed() is None
    monkeypatch.undo()
    assert live.read_bytes() == before, "the failure arm ate the run record"
    assert generate.log_archives() == []
    assert not list(paths.DATA_DIR.glob("*.tmp"))


def _archived_runs_on_disk() -> int:
    """The truth the index is a cache OF: run entries actually in the segments,
    counted off the filesystem rather than read out of the cache under test."""
    return sum(1 for p in generate.log_archives()
               for ln in p.read_text(encoding="utf-8").splitlines()
               if ln.strip() and generate._safe_is_run(ln))


def test_deleting_an_archived_segment_recomputes_the_total_to_truth():
    """GATE R-B (QA F-15), FIX-1. Deleting an old segment is an operation SETUP
    invites in its own words ("Deleting old segments is your call"), and before
    this fix it made the run total over-report forever.

    THE DEFECT WAS THE DIRECTION OF THE CREDIBILITY TEST. The index was trusted
    whenever the segments on disk were a SUBSET of the ones it lists — which is
    exactly true after a deletion, so the stale total was served as fact. Set
    EQUALITY closes it in both directions: a segment that arrived without the
    index hearing (the write is best-effort and last) and a segment that left
    are both drift, and both force a recount.

    STICKINESS IS HALF THE FINDING. The gate measured the over-report BAKING IN:
    the next rotation writes `prior + moved` into a fresh index, so a wrong
    `prior` becomes a wrong index that now accounts for every segment on disk
    and is therefore credible forever. The third leg below is that half."""
    _fill_log()
    seg1 = generate.rotate_log_if_needed()
    _fill_log(mode="a")
    seg2 = generate.rotate_log_if_needed()
    assert seg1 is not None and seg2 is not None and seg1 != seg2
    assert generate.archived_run_count() == _archived_runs_on_disk()

    seg1.unlink()                     # his hands, in the sandbox
    truth = _archived_runs_on_disk()
    assert truth > 0 and truth < generate.read_log_index()["archived_runs"], \
        "fixture did not stage a stale index"
    assert generate.archived_run_count() == truth, \
        "the deleted segment's runs are still being reported"

    _fill_log(mode="a")
    seg3 = generate.rotate_log_if_needed()
    assert seg3 is not None
    assert generate.archived_run_count() == _archived_runs_on_disk(), \
        "the over-report baked itself into a fresh, credible-looking index"


def test_nothing_is_deleted_by_rotation():
    """Deletion is HIS future call; this row only ever moves bytes.

    MUTATION-HARDENED (fix loop 1 — mutant M11). The pin used to grep the
    rotator's source for "unlink"/"rmtree", which has ZERO detection power over
    the property: an `os.remove(segment)` that destroys the whole archived
    record walks straight past both spellings, and so does anything that
    delegates. The claim is about the FILESYSTEM, so it is read off the
    filesystem — across TWO cuts, so an EXISTING segment is covered and not only
    the one the current call wrote."""
    _fill_log()
    seg1 = generate.rotate_log_if_needed()
    assert seg1 is not None and seg1.exists()
    _fill_log(mode="a")                       # more history on top of the tail
    union_before = [ln for p in generate.log_segments()
                    for ln in p.read_text(encoding="utf-8").splitlines()]
    seg2 = generate.rotate_log_if_needed()
    assert seg2 is not None and seg2 != seg1
    union_after = [ln for p in generate.log_segments()
                   for ln in p.read_text(encoding="utf-8").splitlines()]
    # Not a count and not a set: the ORDER is the record's append order, which
    # is what every segment-spanning reader concatenates on.
    assert union_after == union_before, \
        f"{len(union_before) - len(union_after)} lines left the record"
    assert seg1.exists() and seg2.exists()
    assert len(generate.log_archives()) == 2
    import inspect                           # the cheap second net, kept
    src = inspect.getsource(generate.rotate_log_if_needed)
    assert "unlink" not in src
    assert "rmtree" not in src
