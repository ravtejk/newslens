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


# --- the shipped template, as shipped ----------------------------------------

def test_shipped_template_parses_to_zero_sources_zero_problems():
    cfg = config.load_sources(PROTOTYPE_ROOT / "sources.yaml")
    assert cfg.sources == []
    assert cfg.problems == []
    assert not cfg.has_active_sources
    assert not cfg.has_interests


def test_template_state_refuses_politely_with_the_documented_message():
    cfg = config.load_sources(PROTOTYPE_ROOT / "sources.yaml")
    with pytest.raises(config.SourcesParseError) as excinfo:
        config.require_active_sources(cfg)
    assert str(excinfo.value) == config.NO_ACTIVE_SOURCES_MSG
    assert (
        str(excinfo.value)
        == "sources.yaml has no active sources — uncomment or add your outlets"
    )


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
