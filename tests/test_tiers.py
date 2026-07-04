"""Source tier model (config.py; ADR-0003 §2).

The two structural promises under test:
  * reference_only is UNFETCHABLE regardless of flags — enabled: true plus a
    live URL still cannot make it fetchable (NYT/AP/Reuters/Wikipedia can
    never be fetched by a config slip).
  * cautious is DEFAULT-DISABLED — omitting `enabled` means off; enabling one
    is an explicit act that generates a warning.
"""

from __future__ import annotations

import pytest

from newslens import config


def load(tmp_path, text):
    p = tmp_path / "sources.yaml"
    p.write_text(text, encoding="utf-8")
    return config.load_sources(p)


# --- reference_only: structurally unfetchable -----------------------------------

def test_reference_only_is_unfetchable_even_when_enabled_with_a_url(tmp_path):
    cfg = load(
        tmp_path,
        "sources:\n"
        "  - name: The New York Times\n"
        "    tier: reference_only\n"
        "    enabled: true\n"
        "    rss_url: https://rss.example/nyt.xml\n",
    )
    assert cfg.problems == []
    (src,) = cfg.sources
    assert src.enabled is True and src.rss_url  # flags say yes...
    assert src.fetchable is False               # ...structure says never
    assert cfg.fetchable_sources == []
    assert not cfg.has_active_sources


def test_reference_only_needs_no_url(tmp_path):
    cfg = load(tmp_path, "sources:\n  - name: Reuters\n    tier: reference_only\n")
    assert cfg.problems == []
    assert cfg.sources[0].rss_url is None
    assert cfg.reference_only_sources == cfg.sources


def test_reference_only_with_garbage_url_is_still_a_problem(tmp_path):
    cfg = load(
        tmp_path,
        "sources:\n"
        "  - name: Ref\n"
        "    tier: reference_only\n"
        "    rss_url: not-a-url\n",
    )
    assert any("must be an http(s) URL" in p for p in cfg.problems)


# --- cautious: default-disabled, warned when enabled ------------------------------

def test_cautious_defaults_to_disabled_when_enabled_omitted(tmp_path):
    cfg = load(
        tmp_path,
        "sources:\n"
        "  - name: Aggregator\n"
        "    rss_url: https://agg.example/feed\n"
        "    tier: cautious\n",
    )
    assert cfg.problems == []
    (src,) = cfg.sources
    assert src.enabled is False
    assert not src.fetchable
    assert cfg.warnings == []  # off by default is the quiet, expected state
    assert src in cfg.disabled_sources


def test_cautious_enabled_true_is_fetchable_and_warned(tmp_path):
    cfg = load(
        tmp_path,
        "sources:\n"
        "  - name: Aggregator\n"
        "    rss_url: https://agg.example/feed\n"
        "    tier: cautious\n"
        "    enabled: true\n",
    )
    assert cfg.problems == []
    assert cfg.sources[0].fetchable
    assert len(cfg.warnings) == 1
    assert "explicitly enabled" in cfg.warnings[0]
    assert "Aggregator" in cfg.warnings[0]


def test_cautious_enabled_false_is_quietly_off(tmp_path):
    cfg = load(
        tmp_path,
        "sources:\n"
        "  - name: Aggregator\n"
        "    rss_url: https://agg.example/feed\n"
        "    tier: cautious\n"
        "    enabled: false\n",
    )
    assert cfg.warnings == [] and not cfg.sources[0].fetchable


# --- the other tiers ----------------------------------------------------------------

def test_full_defaults_to_enabled_and_fetchable(tmp_path):
    cfg = load(
        tmp_path,
        "sources:\n  - name: Outlet\n    rss_url: https://o.example/feed\n",
    )
    (src,) = cfg.sources
    assert src.tier == "full" and src.enabled and src.fetchable


def test_headline_only_is_fetched_like_any_feed(tmp_path):
    """The tier is a downstream promise (titles/summaries + linkout), not a
    fetch change — ingest contract."""
    cfg = load(
        tmp_path,
        "sources:\n"
        "  - name: Bloomberg\n"
        "    rss_url: https://b.example/feed\n"
        "    tier: headline_only\n",
    )
    assert cfg.sources[0].fetchable


def test_explicit_disable_works_on_any_tier(tmp_path):
    # NB: the name must be quoted or non-reserved — bare Off/On/Yes/No are
    # YAML 1.1 booleans, so an unquoted `name: Off` parses as `name: false`
    # and is correctly REJECTED. That strictness is a feature; this test
    # learned it the hard way (M2 QA adjudication).
    cfg = load(
        tmp_path,
        "sources:\n"
        "  - name: \"Off\"\n"
        "    rss_url: https://o.example/feed\n"
        "    enabled: false\n",
    )
    assert cfg.problems == []
    assert not cfg.sources[0].fetchable
    assert cfg.sources[0] in cfg.disabled_sources


def test_bare_yaml_boolean_as_name_is_rejected_not_coerced(tmp_path):
    """Pins the strictness found above: an unquoted YAML-boolean name
    (`name: Off`) must surface as a format problem, never silently coerce
    into the string 'False'."""
    cfg = load(
        tmp_path,
        "sources:\n"
        "  - name: Off\n"
        "    rss_url: https://o.example/feed\n",
    )
    assert any("`name` is required" in p for p in cfg.problems)
    assert cfg.sources == []


def test_disabled_sources_property_excludes_reference_only(tmp_path):
    cfg = load(
        tmp_path,
        "sources:\n"
        "  - name: Ref\n"
        "    tier: reference_only\n"
        "  - name: Muted Outlet\n"
        "    rss_url: https://o.example/feed\n"
        "    enabled: false\n",
    )
    assert cfg.problems == []
    assert [s.name for s in cfg.disabled_sources] == ["Muted Outlet"]


# --- validation problems --------------------------------------------------------------

@pytest.mark.parametrize(
    "snippet, fragment",
    [
        ("    tier: premium\n", "`tier` must be one of"),
        ("    tier: Full\n", "`tier` must be one of"),          # case-sensitive, closed
        ("    enabled: \"yes\"\n", "`enabled` must be true or false"),
        ("    note: 5\n", "`note` must be a string"),
    ],
)
def test_tier_field_validation_problems(tmp_path, snippet, fragment):
    cfg = load(
        tmp_path,
        "sources:\n"
        "  - name: X\n"
        "    rss_url: https://x.example/feed\n" + snippet,
    )
    assert any(fragment in p for p in cfg.problems), cfg.problems


def test_require_active_sources_returns_only_fetchable(tmp_path):
    cfg = load(
        tmp_path,
        "sources:\n"
        "  - name: Fetch Me\n"
        "    rss_url: https://a.example/feed\n"
        "  - name: Ref\n"
        "    tier: reference_only\n"
        "  - name: Caut Default\n"
        "    rss_url: https://c.example/feed\n"
        "    tier: cautious\n"
        "  - name: Muted Outlet\n"
        "    rss_url: https://d.example/feed\n"
        "    enabled: false\n",
    )
    active = config.require_active_sources(cfg)
    assert [s.name for s in active] == ["Fetch Me"]
