"""config.load_sources / require_active_sources contract (tests/README, spec §A).

Covers the three documented states — shipped template (zero sources, zero
problems), valid file, malformed YAML — plus the polite-refusal rule
(DECISIONS.md 2026-07-02: no default outlets, ever) and the "a typo'd key must
never be silently ignored" rule via the problems list.
"""

from __future__ import annotations

import pytest

from newslens import config

from conftest import PROTOTYPE_ROOT


def write_yaml(tmp_path, text):
    p = tmp_path / "sources.yaml"
    p.write_text(text, encoding="utf-8")
    return p


# --- the SHIPPED file (seeded since M2) and the template contract --------------
# The shipped sources.yaml is the principal's live outlet list. Tests pin its
# structural invariants, never its exact contents (the principal edits it),
# and NEVER fetch from it. The template/refusal contract is pinned separately
# against a synthetic zero-sources file.

def test_shipped_seeded_file_parses_clean_with_valid_tier_structure():
    cfg = config.load_sources(PROTOTYPE_ROOT / "sources.yaml")
    assert cfg.problems == [], f"shipped file has format problems: {cfg.problems}"
    assert cfg.has_active_sources  # seeded state: there IS something to fetch
    # Structural tier invariants, regardless of the principal's future edits:
    for s in cfg.reference_only_sources:
        assert not s.fetchable, f"reference_only source {s.name!r} is fetchable"
    for s in cfg.fetchable_sources:
        assert s.enabled and s.rss_url and s.tier != "reference_only"
    # Every enabled cautious source must carry its explicit-enable warning.
    enabled_cautious = [s for s in cfg.sources if s.tier == "cautious" and s.enabled]
    assert len(cfg.warnings) == len(enabled_cautious)


def test_shipped_seeded_file_refuses_nothing_but_returns_only_fetchable():
    cfg = config.load_sources(PROTOTYPE_ROOT / "sources.yaml")
    active = config.require_active_sources(cfg)
    assert active == cfg.fetchable_sources
    assert all(s.fetchable for s in active)


def test_template_state_refuses_politely_with_the_documented_message(tmp_path):
    """The polite-refusal rule (DECISIONS.md 2026-07-02) is a contract about
    the zero-active-sources STATE, pinned here against a synthetic template —
    decoupled from whatever the shipped file currently contains."""
    p = write_yaml(
        tmp_path,
        "# fully commented template — zero active sources\n"
        "# sources:\n"
        "#   - name: Example\n"
        "#     rss_url: https://example.invalid/feed.xml\n",
    )
    cfg = config.load_sources(p)
    assert cfg.sources == [] and cfg.problems == []
    with pytest.raises(config.SourcesParseError) as excinfo:
        config.require_active_sources(cfg)
    assert str(excinfo.value) == config.NO_ACTIVE_SOURCES_MSG
    assert (
        str(excinfo.value)
        == "sources.yaml has no active sources — uncomment or add your outlets"
    )


def test_disabled_and_reference_only_sources_do_not_count_as_active(tmp_path):
    """A file with entries but nothing fetchable still refuses politely —
    'active' means fetchable, not merely present."""
    p = write_yaml(
        tmp_path,
        "sources:\n"
        "  - name: Ref Outlet\n"
        "    tier: reference_only\n"
        "  - name: Off Outlet\n"
        "    rss_url: https://example.invalid/feed.xml\n"
        "    enabled: false\n"
        "  - name: Cautious Outlet\n"
        "    rss_url: https://example.invalid/c.xml\n"
        "    tier: cautious\n",
    )
    cfg = config.load_sources(p)
    assert cfg.problems == []
    assert len(cfg.sources) == 3
    assert not cfg.has_active_sources
    with pytest.raises(config.SourcesParseError) as excinfo:
        config.require_active_sources(cfg)
    assert str(excinfo.value) == config.NO_ACTIVE_SOURCES_MSG


# --- valid files --------------------------------------------------------------

VALID_YAML = """
sources:
  - name: "  BBC News  "
    rss_url: "https://feeds.bbci.co.uk/news/rss.xml"
  - name: Reuters Wire
    rss_url: https://example.com/reuters.xml
    wire_syndication: true
interests:
  broad:
    - technology
  granular:
    - AI regulation
    - " semiconductor supply chains "
"""


def test_valid_file_parses_sources_interests_and_strips_whitespace(tmp_path):
    cfg = config.load_sources(write_yaml(tmp_path, VALID_YAML))
    assert cfg.problems == []
    assert [s.name for s in cfg.sources] == ["BBC News", "Reuters Wire"]
    assert cfg.sources[0].wire_syndication is False  # default
    assert cfg.sources[1].wire_syndication is True
    assert cfg.interests_broad == ["technology"]
    assert cfg.interests_granular == ["AI regulation", "semiconductor supply chains"]
    assert cfg.has_active_sources and cfg.has_interests


def test_require_active_sources_returns_the_sources_for_a_valid_file(tmp_path):
    cfg = config.load_sources(write_yaml(tmp_path, VALID_YAML))
    sources = config.require_active_sources(cfg)
    assert len(sources) == 2


# --- malformed / wrong-shape files --------------------------------------------

def test_malformed_yaml_is_a_loud_friendly_parse_error(tmp_path):
    p = write_yaml(tmp_path, "sources: [unclosed\n  - what")
    with pytest.raises(config.SourcesParseError) as excinfo:
        config.load_sources(p)
    assert "not valid YAML" in str(excinfo.value)


def test_missing_file_is_a_parse_error_naming_the_path(tmp_path):
    with pytest.raises(config.SourcesParseError) as excinfo:
        config.load_sources(tmp_path / "gone.yaml")
    assert "not found" in str(excinfo.value)


@pytest.mark.parametrize(
    "yaml_text, expected_fragment",
    [
        ("- a\n- b\n", "top level must be a mapping"),
        ("sauces:\n  - name: A\n", "unknown top-level key `sauces`"),
        (
            "sources:\n  - name: Twin\n    rss_url: https://a.example/1\n"
            "  - name: twin\n    rss_url: https://a.example/2\n",
            "duplicate source name",
        ),
        (
            "sources:\n  - name: First\n    rss_url: https://same.example/f\n"
            "  - name: Second\n    rss_url: https://same.example/f\n",
            "duplicate rss_url on 'Second' and 'First'",
        ),
        ("sources: {}\n", "`sources` must be a list"),
        ("sources:\n  - just-a-string\n", "source #1 must be a mapping"),
        ("sources:\n  - rss_url: https://x.example/f\n", "`name` is required"),
        ("sources:\n  - name: A\n", "`rss_url` is required"),
        (
            "sources:\n  - name: A\n    rss_url: ftp://x.example/f\n",
            "must be an http(s) URL",
        ),
        (
            "sources:\n  - name: A\n    rss_url: https://x.example/f\n    feed: x\n",
            "unknown key `feed`",
        ),
        (
            "sources:\n  - name: A\n    rss_url: https://x.example/f\n"
            "    wire_syndication: \"yes\"\n",
            "`wire_syndication` must be true or false",
        ),
        ("interests: [tech]\n", "`interests` must be a mapping"),
        ("interests:\n  wide:\n    - x\n", "unknown key `wide`"),
        ("interests:\n  broad: technology\n", "`interests.broad` must be a list"),
        ("interests:\n  granular:\n    - \"\"\n", "`interests.granular` must be a list"),
    ],
)
def test_format_problems_are_reported_never_silently_ignored(
    tmp_path, yaml_text, expected_fragment
):
    cfg = config.load_sources(write_yaml(tmp_path, yaml_text))
    assert any(expected_fragment in p for p in cfg.problems), (
        f"expected a problem containing {expected_fragment!r}, got {cfg.problems!r}"
    )


def test_problems_block_require_active_sources_even_with_valid_sources_present(tmp_path):
    p = write_yaml(
        tmp_path,
        "sources:\n"
        "  - name: A\n"
        "    rss_url: https://x.example/f\n"
        "typo_key: oops\n",
    )
    cfg = config.load_sources(p)
    assert cfg.has_active_sources  # the good source did parse...
    with pytest.raises(config.SourcesParseError) as excinfo:
        config.require_active_sources(cfg)  # ...but problems still refuse loudly
    assert "sources.yaml has problems" in str(excinfo.value)
    assert "typo_key" in str(excinfo.value)
