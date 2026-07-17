"""Doctor changes in milestone 2 (doctor.py diff; ADR-0003 §8; NOTES-M2 item 1).

Covers: dormant GENERATE_HOUR_LOCAL wording with the garbage-still-fails pin
HELD; the tier-aware sources section (fetchable-only feed checks, cautious
warnings, reference-only and disabled INFO lines); the sharpened keyless
invariant (active sources fetch feeds but NEVER the key APIs); and the two
QA-owned unreadable-file pins carried over from the M1 review (unreadable
.env, unreadable sonar ping file).
"""

from __future__ import annotations

import os

import pytest

from newslens import config, doctor, paths

from conftest import make_rss


# --- GENERATE_HOUR_LOCAL: dormant, but garbage still fails --------------------------

def _hour_line(env):
    results = doctor.check_optional_and_guards(env)
    matches = [r for r in results if "GENERATE_HOUR_LOCAL" in r.text]
    assert matches
    return matches[0]


def test_unset_hour_is_info_and_says_dormant():
    line = _hour_line({})
    assert line.status == doctor.INFO
    assert "dormant" in line.text and "on-demand" in line.text


def test_valid_hour_passes_and_says_dormant():
    line = _hour_line({"GENERATE_HOUR_LOCAL": "7"})
    assert line.status == doctor.PASS
    assert "07:00 local" in line.text and "dormant" in line.text


@pytest.mark.parametrize("raw", ["24", "-1", "abc", "6.5"])
def test_garbage_hour_still_fails_despite_dormancy(raw):
    """The held pin (ADR-0003 §8): a typo'd .env line is a config error
    regardless of whether anything reads the var yet."""
    line = _hour_line({"GENERATE_HOUR_LOCAL": raw})
    assert line.status == doctor.FAIL
    assert "0-23" in line.text


def test_feed_timeout_is_15s_per_the_m2_sweep():
    assert doctor.FEED_TIMEOUT_S == 15


# --- tier-aware sources section -------------------------------------------------------

TIERED_YAML = """\
sources:
  - name: Full On
    rss_url: {f1}
  - name: Caut On
    rss_url: {f2}
    tier: cautious
    enabled: true
  - name: Caut Default
    rss_url: {f3}
    tier: cautious
  - name: Ref Only
    tier: reference_only
  - name: Full Off
    rss_url: {f4}
    enabled: false
"""


def _write_tiered(fake_api):
    feed = make_rss([{"title": "T", "url": "https://x.example/t"}])
    f1 = fake_api.add_route("/f1.xml", body=feed)
    f2 = fake_api.add_route("/f2.xml", body=feed)
    f3 = fake_api.add_route("/f3.xml", body=feed)
    f4 = fake_api.add_route("/f4.xml", body=feed)
    paths.SOURCES_FILE.write_text(
        TIERED_YAML.format(f1=f1, f2=f2, f3=f3, f4=f4), encoding="utf-8"
    )


def test_sources_section_is_tier_aware_and_checks_only_fetchable_feeds(
    tmp_paths, fake_api
):
    _write_tiered(fake_api)
    results = doctor.check_sources()
    texts = [r.text for r in results]
    by_status = {}
    for r in results:
        by_status.setdefault(r.status, []).append(r.text)

    assert any("2 active source(s) configured" in t for t in texts)
    assert any(
        "cautious source 'Caut On' is explicitly enabled" in t
        for t in by_status.get(doctor.WARN, [])
    )
    assert any(
        "1 reference-only outlet(s) — citable, never fetched: Ref Only" in t
        for t in by_status.get(doctor.INFO, [])
    )
    assert any(
        "2 source(s) present but disabled: Caut Default, Full Off" in t
        for t in by_status.get(doctor.INFO, [])
    )
    # The server saw exactly the two fetchable feeds — nothing else.
    feed_paths = sorted(r["path"] for r in fake_api.recorded if r["method"] == "GET")
    assert feed_paths == ["/f1.xml", "/f2.xml"]


def test_keyless_doctor_with_active_sources_fetches_feeds_but_never_the_apis(
    tmp_paths, fake_api, monkeypatch, capsys
):
    """The M2-sharpened invariant (the test's spine, unchanged): RSS checks need
    no key and may run; the OpenAI/Perplexity endpoints must not see a single
    request when keyless. Verified by pointing BOTH API constants at the local
    fake and asserting it recorded only feed GETs. Gate ruling 2 (2026-07-17):
    keyless-OpenAI is no longer an API-key FAIL — after the state flip no live
    seat routes to gpt-4o, so the OpenAI line renders INFO 'not needed'; the
    honest exit-1 rests on the required ANTHROPIC key instead."""
    _write_tiered(fake_api)
    monkeypatch.setattr(doctor, "OPENAI_MODELS_URL", fake_api.base_url + "/v1/models")
    monkeypatch.setattr(
        doctor, "PERPLEXITY_CHAT_URL", fake_api.base_url + "/chat/completions"
    )
    code = doctor.run_doctor()
    out = capsys.readouterr().out
    assert code == 1  # keys still missing — honest exit
    assert "OPENAI_API_KEY not needed — no live seat routes to OpenAI" in out  # ruling 2: INFO
    assert "ANTHROPIC_API_KEY not set" in out                                  # the real required failure
    hit = {(r["method"], r["path"]) for r in fake_api.recorded}
    assert ("GET", "/v1/models") not in hit
    assert ("POST", "/chat/completions") not in hit
    assert ("GET", "/f1.xml") in hit and ("GET", "/f2.xml") in hit


# --- QA-owned carryover pins: the remaining unreadable-file paths ---------------------

def test_unreadable_env_file_is_a_friendly_failure_line(tmp_paths):
    if os.geteuid() == 0:
        pytest.skip("running as root — chmod 000 is still readable")
    paths.ENV_FILE.write_text("OPENAI_API_KEY=should-never-load\n", encoding="utf-8")
    paths.ENV_FILE.chmod(0)
    try:
        env, notes = doctor.load_effective_env()
    finally:
        paths.ENV_FILE.chmod(0o600)
    fails = [n for n in notes if n.status == doctor.FAIL]
    assert any(".env exists but is not readable" in n.text for n in fails)
    assert "OPENAI_API_KEY" not in env or env["OPENAI_API_KEY"] == ""
    assert "should-never-load" not in " ".join(n.text for n in notes)  # value not echoed


def test_unreadable_ping_file_fails_friendly_before_any_network(
    tmp_path, no_network, monkeypatch
):
    if os.geteuid() == 0:
        pytest.skip("running as root — chmod 000 is still readable")
    pdir = tmp_path / "prompts"
    pdir.mkdir()
    ping = pdir / "doctor_sonar_ping.txt"
    ping.write_text("Reply with the single word: ok\n", encoding="utf-8")
    ping.chmod(0)
    monkeypatch.setattr(paths, "PROMPTS_DIR", pdir)
    try:
        results = doctor.check_perplexity_key({"PERPLEXITY_API_KEY": "pplx-x"})
    finally:
        ping.chmod(0o600)
    assert [r.status for r in results] == [doctor.FAIL]
    assert "exists but is not readable" in results[0].text
    assert no_network == []
