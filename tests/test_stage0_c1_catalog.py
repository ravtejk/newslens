"""Stage-0 C1 — the topic catalog (NL-116): data, loader, and no fabrication.

The catalog is the vocabulary contract behind the Commissioning picker. Two
properties are worth a suite tooth, and they are different properties:

  * MECHANICAL — the file parses into two levels that map cleanly onto
    sources.yaml, every name can actually be written by the shipped editor, and
    a name outside the file never resolves (which is how "typing filters this
    list; it never adds to it" is true in the mechanism rather than in copy).

  * NO FABRICATION — every name in the catalog appears verbatim as a shipped
    interest tag at 93e64c9. The mockup's 8 x 54 specimens were placeholders
    and the standing law forbids inventing vocabulary; this file is what makes
    "These are the topics NewsLens can rank" a checked statement instead of a
    hopeful one.

Sandbox: the tree conftest's autouse fixtures apply. $0 by construction — the
catalog is a file, nothing here reaches a model or a network.
"""

from __future__ import annotations

import pytest
import yaml

from newslens import catalog, paths, personas, server

from conftest import PROTOTYPE_ROOT

# ---------------------------------------------------------------------------
# PROVENANCE — the founder's own interest tags, frozen at 93e64c9.
#
# Frozen rather than read from sources.yaml at test time on purpose: that file
# is the principal's LIVE working state and he edits it. A test that read it
# would turn red the day he added a tag, which is a false alarm about the
# catalog. The constant is the record of what the vocabulary WAS when the
# catalog was authored; the containment assert below is what it is for.
# ---------------------------------------------------------------------------
FOUNDER_DOMAINS = (
    'Middle East Conflict', 'International Law', 'Geopolitics',
    'Economic Policy', 'Central Bank Policy', 'Global Trade', 'Africa',
    'Latin America', 'Oceania', 'Europe', 'East Asia', 'Civic Tech',
    'Independent Media', 'Diplomacy',
)
FOUNDER_TOPICS = (
    'US-Israel Relations', 'War Crimes', 'Proxy Conflicts',
    'India Politics', 'BRICS', 'Oil Markets', 'Energy Supply Shock',
    'Strategic Petroleum Reserve', 'OPEC+', 'Maritime Chokepoints',
    'Strait of Hormuz', 'China-Taiwan', 'Inflation', 'Federal Reserve',
    'ECB', 'US Economic Outlook', 'European Economy', 'Asian Markets',
    'Mergers & Acquisitions', 'Private Credit', 'Direct Lending',
    'Shadow Banking', 'Credit Default Risk', 'Recession Risk',
    'Stagflation', 'Fertilizer Supply', 'Food Security',
    'Agricultural Commodities', 'Supply Chain Disruption',
    'Semiconductor Supply', 'Artificial Intelligence', 'Housing Policy',
    'Housing Affordability', 'Household Debt', 'US Legislative Process',
    'Financial Analysis', 'Institutional Research', 'Policy Failure',
    'Regulatory Design', 'Systemic Risk', 'Consumer Protection',
    'NYC Subway', 'New York Politics', 'New York City', 'Micromobility',
)

# The four topics that are REAL vocabulary and deliberately NOT in the catalog:
# there is no local/regional DOMAIN in the shipped domain list to hang them
# from, and inventing one would mint a tag nobody authored. The 2026-07-03
# taxonomy round named this exact gap. Pinned so the omission stays a decision
# on the record rather than an oversight nobody notices.
KNOWN_ABSENT = frozenset({
    "NYC Subway", "New York Politics", "New York City", "Micromobility"})


def _persona_tags():
    out = {"domain": set(), "topic": set()}
    for path in sorted((PROTOTYPE_ROOT / "personas").glob("*.yaml")):
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        for level, tags in (raw.get("interests") or {}).items():
            out.setdefault(level, set()).update(tags or [])
    return out


@pytest.fixture(scope="module")
def cat():
    return catalog.load()


# ---------------------------------------------------------------------------
# The file ships and parses
# ---------------------------------------------------------------------------

def test_the_catalog_ships_beside_the_profile_template():
    """One authored data file, in the shipped-artifacts directory — the same
    class as templates/profile-sources.yaml, migrations/ and prompts/. NL-116's
    whole point is that adding a topic is a data edit, so the data has to be a
    file somebody can open."""
    assert paths.TOPIC_CATALOG.exists(), paths.TOPIC_CATALOG
    assert paths.TOPIC_CATALOG.parent == paths.TEMPLATES_DIR


def test_the_catalog_has_two_levels_and_a_real_shape(cat):
    assert cat.domain_count > 0 and cat.topic_count > 0
    assert cat.entry_count == cat.domain_count + cat.topic_count
    # Every domain resolves as a domain and every topic as a topic — the level
    # is a property of the catalog, which is the whole reason the reader is
    # never asked about it.
    for d in cat.domains:
        assert cat.level_of(d.name) == catalog.DOMAIN
        for t in d.topics:
            assert cat.level_of(t) == catalog.TOPIC


def test_the_shipped_catalog_counts_are_what_the_report_claims(cat):
    """The honest count, pinned. The mockup's specimen catalog was 8 domains x
    54 topics; this is what the REAL vocabulary yields. If an authorship round
    grows it, this assert is the line that says so out loud."""
    assert (cat.domain_count, cat.topic_count) == (18, 48)


# ---------------------------------------------------------------------------
# No fabrication
# ---------------------------------------------------------------------------

def test_every_catalog_name_is_real_shipped_vocabulary(cat):
    """THE NO-FABRICATION TOOTH. Every name the picker offers must already be a
    tag the ranker consumes — from the founder's own file or from one of the
    three shipped persona fixtures. A future edit that invents a plausible
    topic fails here, which is the only reliable way to keep the picker's own
    sentence ("These are the topics NewsLens can rank") true."""
    persona = _persona_tags()
    real = (set(FOUNDER_DOMAINS) | set(FOUNDER_TOPICS)
            | persona.get("domain", set()) | persona.get("topic", set()))
    invented = sorted(n for n in cat.names() if n not in real)
    assert not invented, (
        "catalog names that appear in NO shipped interest list — the "
        "no-fabrication law forbids minting vocabulary: " + repr(invented))


def test_every_persona_tag_is_offered_by_the_catalog(cat):
    """The three shipped worlds must be reachable from the picker. A persona
    tag missing from the catalog would mean the org authored a reader it cannot
    commission through its own first-run page."""
    persona = _persona_tags()
    names = {n.lower() for n in cat.names()}
    missing = sorted(t for t in (persona.get("domain", set())
                                 | persona.get("topic", set()))
                     if t.lower() not in names)
    assert not missing, missing


def test_the_omitted_local_cluster_is_the_only_omission(cat):
    """Real vocabulary left OUT of the catalog is exactly the four local tags
    with no domain to hang from — nothing else quietly went missing."""
    persona = _persona_tags()
    real = (set(FOUNDER_DOMAINS) | set(FOUNDER_TOPICS)
            | persona.get("domain", set()) | persona.get("topic", set()))
    names = set(cat.names())
    absent = {n for n in real if n not in names}
    assert absent == KNOWN_ABSENT, sorted(absent)


# ---------------------------------------------------------------------------
# Every name can actually be written
# ---------------------------------------------------------------------------

def test_every_catalog_name_survives_the_shipped_editors_guard(cat):
    """A catalog entry the sources.yaml editor would refuse is a trap: the
    reader picks it, the write fails, and the founding page has to refuse an
    act it offered. server._bad_name is that guard, called here on the real
    strings rather than approximated."""
    rejected = [n for n in cat.names() if server._bad_name(n)]
    assert not rejected, rejected


def test_a_catalog_pick_round_trips_into_a_fresh_profiles_sources_file(
        tmp_paths, cat):
    """The whole SEAM-1 door in one pass, on the shipped EMPTY template: pick
    one name at each level, write through the editor, read the file back."""
    paths.SOURCES_FILE.write_text(
        paths.PROFILE_SOURCES_TEMPLATE.read_text(encoding="utf-8"),
        encoding="utf-8")
    domain = cat.domains[0]
    ok, _ = server.topic_add(domain.name,
                             catalog.LEVEL_TO_EDITOR_LEVEL[catalog.DOMAIN])
    assert ok
    topic = domain.topics[0]
    ok, _ = server.topic_add(topic,
                             catalog.LEVEL_TO_EDITOR_LEVEL[catalog.TOPIC])
    assert ok
    from newslens import config
    cfg = config.load_sources()
    assert cfg.interests_broad == [domain.name]
    assert cfg.interests_granular == [topic]
    assert not cfg.problems


def test_the_level_adapters_agree_with_the_persona_lanes(cat):
    """personas.py holds the same two adapters for its own provisioning lane.
    Two copies of a mapping is how a domain pick silently lands at the topic
    level; they agree by construction and this says so."""
    assert catalog.LEVEL_TO_YAML_KEY == personas.ALTITUDE_TO_YAML_KEY
    assert catalog.LEVEL_TO_EDITOR_LEVEL == personas.ALTITUDE_TO_EDITOR_LEVEL


# ---------------------------------------------------------------------------
# Resolution — the mechanism behind "suggestions only"
# ---------------------------------------------------------------------------

def test_resolution_is_case_and_whitespace_insensitive(cat):
    name = cat.domains[0].topics[0]
    assert cat.resolve("   " + name.upper() + "  ") == (name, catalog.TOPIC)


def test_the_canonical_spelling_is_what_gets_written(cat):
    name = cat.domains[0].name
    assert cat.canonical(name.lower()) == name


def test_a_name_outside_the_catalog_never_resolves(cat):
    for stranger in ("Pickleball", "", "   ", "Inflation ; rm -rf",
                     "Local news"):
        assert cat.resolve(stranger) is None
        assert cat.level_of(stranger) is None


# ---------------------------------------------------------------------------
# The loader refuses what it must
# ---------------------------------------------------------------------------

def _write(tmp_path, text):
    p = tmp_path / "cat.yaml"
    p.write_text(text, encoding="utf-8")
    return p


def test_a_duplicate_name_is_refused(tmp_path):
    src = _write(tmp_path, "domains:\n  - name: A\n    topics: [X]\n"
                           "  - name: B\n    topics: [x]\n")
    with pytest.raises(catalog.CatalogError) as exc:
        catalog.load(src)
    assert "repeats" in str(exc.value)


def test_a_name_the_editor_could_not_write_is_refused(tmp_path):
    src = _write(tmp_path, 'domains:\n  - name: "Bad: name"\n    topics: []\n')
    with pytest.raises(catalog.CatalogError) as exc:
        catalog.load(src)
    assert "sources.yaml" in str(exc.value)


def test_an_empty_or_malformed_catalog_is_refused_loudly(tmp_path):
    for text in ("", "domains: []\n", "domains:\n  - topics: [X]\n",
                 "domains:\n  - name: A\n    topics: {}\n"):
        with pytest.raises(catalog.CatalogError):
            catalog.load(_write(tmp_path, text))


def test_a_missing_catalog_file_is_refused_loudly(tmp_path):
    with pytest.raises(catalog.CatalogError):
        catalog.load(tmp_path / "nope.yaml")
