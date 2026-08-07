"""NL-135 slate land + Q1 coverage honesty + NL-136 ① — the batch's own pins.

BORN RED, and structured so each pin reds INDIVIDUALLY at HEAD rather than
taking the whole module down with a collection error: `newslens.coverage` does
not exist before this batch, so it is imported INSIDE the tests that need it.
At HEAD the slate/NL-136 pins fail on their assertions and the coverage pins
fail on the import — each one naming its own missing thing.

Three things are pinned, in the order the batch does them:

  1. THE SLATE landed in the org template, with the postures the doctor's own
     pipeline-UA run earned on 2026-08-03 — including the two feeds it
     DISQUALIFIED (the CNN section feeds resolve fine and are frozen in 2022
     and 2023; recency is fetch-time, so enabling one would inject an archive
     as fresh news).
  2. Q1 COVERAGE HONESTY: absence asserted only where it is provable, presence
     never claimed as sufficiency, state computed live and never persisted.
  3. NL-136 ①: attribution-only presented as design, the dead aggregator gone.

CARRIED INVARIANTS (born GREEN, labelled as the born-red law requires — these
guard behaviour that already worked and must keep working through the diff):
`test_the_pack_sentence_still_drops_a_clause_it_cannot_fill`,
`test_the_template_still_parses_clean_with_zero_interests`.
"""

from __future__ import annotations

import re

import pytest

from newslens import catalog, commissioning, config, labels, paths

# The slate exactly as chartered: name -> (tier, wire_syndication, enabled)
# `enabled` carried the pool-cap posture ruling 2026-08-06 (DECISIONS) — the 14
# Entertainment/Sports feeds landed staged-but-held "until NL-142's dedupe ends
# the eviction class". THAT CONDITION IS SPENT: NL-142 landed (872113e) and
# ENG-M0 raised the pool cap 550 -> 780 and added fair-fill, which is the
# eviction class it named. POSTURE A: every slated feed is enabled.
SLATE = {
    # Health & Science — the proven gap
    "STAT News": ("full", False, True),
    "KFF Health News": ("full", True, True),
    "NPR Health": ("full", False, True),
    "CIDRAP — Antimicrobial Stewardship": ("full", False, True),
    "Nature": ("headline_only", False, True),
    "Fierce Healthcare": ("full", False, True),
    # Technology
    "TechCrunch": ("full", False, True),
    "MIT Technology Review": ("full", False, True),
    "IEEE Spectrum": ("full", False, True),
    "Ars Technica": ("full", False, True),
    "The Verge": ("full", False, True),
    "Wired": ("headline_only", False, True),
    # Entertainment — ENABLED (ENG-M0 2026-08-06, POSTURE A). These eight were
    # held disabled under the pool-cap posture ruling's bridge (option (a)),
    # explicitly "until NL-142's dedupe lands". It landed (872113e), and ENG-M0
    # raised the pool cap 550 -> 780 with fair-fill, so the hold is discharged
    # and the condition that wrote it is spent.
    "BBC News — Entertainment & Arts": ("full", False, True),
    "NPR Culture": ("full", False, True),
    "NPR Music": ("full", False, True),
    "NBC News — Pop Culture": ("full", False, True),
    "PBS NewsHour — Arts": ("full", False, True),
    "The Guardian — Culture": ("full", False, True),
    "TheWrap": ("full", False, True),
    "Stereogum": ("full", False, True),
    # Sports — ENABLED (same ruling, same discharge as Entertainment above).
    "BBC Sport": ("full", False, True),
    "Washington Times — Sports": ("full", True, True),
    "Yahoo Sports": ("full", True, True),
    "ESPN": ("full", False, True),
    "CBS Sports": ("full", False, True),
    "The Guardian — Sport": ("full", False, True),
    # Business deltas
    "WSJ — US Business": ("headline_only", False, True),
    "Semafor": ("full", False, True),
}

# The eight C1 health topics the 2026-08-02 fresh-profile walk proved
# unservable, plus their three domains.
HEALTH_NAMES = [
    "Public Health", "Vaccine Policy", "Outbreak Surveillance",
    "Antimicrobial Resistance", "Health Systems", "Hospital Capacity",
    "Medicaid", "Drug Pricing", "Health Workforce", "Biomedical Research",
    "Clinical Trials",
]

# The research finding of record: no free full-content outlet covers private
# credit at beat depth, so every source that does is titles-and-summaries.
CREDIT_TOPICS = ["Private Credit", "Direct Lending", "Shadow Banking",
                 "Credit Default Risk", "Household Debt"]


@pytest.fixture
def template_cfg():
    return config.load_sources(paths.PROFILE_SOURCES_TEMPLATE)


def _by_name(cfg):
    return {s.name: s for s in cfg.sources}


# ---------------------------------------------------------------------------
# 1. The slate
# ---------------------------------------------------------------------------

def test_every_slated_feed_landed_enabled_with_its_charted_tier(template_cfg):
    """The slate is IN the org template at the tier the doctor's own run
    earned it. ENG-M0 2026-08-06 — POSTURE A: the 14 Entertainment/Sports feeds
    were held under posture B "until NL-142 dedupe lands"; NL-142 landed
    (872113e) and the pool cap rose 550 -> 780 with fair-fill, so the hold is
    discharged and every slated feed is enabled. A paywalled outlet at `full`
    would put article text in a briefing it is not licensed to carry."""
    found = _by_name(template_cfg)
    missing = [n for n in SLATE if n not in found]
    assert not missing, "slate feeds absent from the template: %s" % missing
    for name, (tier, wire, enabled) in SLATE.items():
        s = found[name]
        assert s.tier == tier, (name, s.tier, tier)
        assert s.wire_syndication is wire, (name, s.wire_syndication, wire)
        assert s.enabled is enabled, (name, s.enabled, enabled)
        assert s.fetchable is enabled, (name, s.fetchable, enabled)
        assert s.rss_url and s.rss_url.startswith(("http://", "https://")), name
        assert s.note, "%s landed without a note — the catalog's idiom is that a feed says why it is here" % name


def test_the_stale_cnn_section_feeds_did_not_land(template_cfg):
    """The doctor's 2026-08-03 run RESOLVED both CNN section feeds — HTTP 200,
    20 items, valid RSS — and disqualified them anyway: the newest item in
    cnn_showbiz is from 2022-12-06 and in edition_sport from 2023-04-16.
    ranking.py measures recency by FETCH time, so enabling a frozen archive
    would hand the ranker twenty three-year-old stories as today's news. This
    is the pin on 'never enabled on hope' — a feed that answers is not a feed
    that publishes."""
    urls = {(s.rss_url or "") for s in template_cfg.sources}
    assert "http://rss.cnn.com/rss/cnn_showbiz.rss" not in urls
    assert "http://rss.cnn.com/rss/edition_sport.rss" not in urls
    names = {s.name for s in template_cfg.sources}
    assert "CNN Entertainment" not in names
    assert "CNN Sport" not in names
    # and the live CNN front page is untouched by all of this
    assert "CNN" in names


def test_the_slate_did_not_disturb_the_existing_catalog(template_cfg):
    """Additive by construction. A slate that silently dropped an outlet the
    reader already had would be a scope change wearing a feed's clothes."""
    kept = {"CNN", "NPR", "NBC News", "CNBC", "BBC News — World",
            "PBS NewsHour", "Al Jazeera", "The Guardian — World",
            "Financial Times", "Bloomberg Markets", "Washington Post — World",
            "Associated Press", "Reuters", "The New York Times", "Wikipedia"}
    names = {s.name for s in template_cfg.sources}
    assert kept <= names, kept - names


# ---------------------------------------------------------------------------
# 2. Q1 — coverage honesty
# ---------------------------------------------------------------------------

def test_the_shipped_coverage_map_validates_against_the_shipped_catalog():
    """Every name in the map exists in the topic catalog. A typo here does not
    fail loudly on its own — it silently moves a beat into the UNSERVED column
    and the picker then asserts absence over a beat that is covered."""
    from newslens import coverage
    cmap = coverage.load()
    assert cmap.feeds
    known = {" ".join(n.split()).lower() for n in catalog.load().names()}
    for f in cmap.feeds:
        for c in f.covers:
            assert " ".join(c.split()).lower() in known, (f.name, c)


def test_the_map_classifies_every_fetchable_feed_in_the_template(template_cfg):
    """Completeness is what makes absence PROVABLE. An unmapped enabled feed
    means 'no source covers this' is a claim made over a source nobody read."""
    from newslens import coverage
    cmap = coverage.load()
    unmapped = [s.name for s in template_cfg.fetchable_sources
                if not cmap.knows(s.name)]
    assert unmapped == [], unmapped


def test_a_malformed_map_is_loud_not_silently_degraded():
    """CoverageError, by name, on a name the catalog does not contain."""
    from newslens import coverage
    bad = "feeds:\n  - name: X\n    covers: [Nonexistent Beat]\n"
    with pytest.raises(coverage.CoverageError):
        coverage.load(_tmpfile(bad))


def test_absence_is_asserted_only_where_it_is_provable():
    """The mechanism's whole point, on the exact shape that produced the
    one-story edition: a list with no health beat marks all eleven health
    names unserved; adding one health feed clears the ones it covers and
    leaves the rest alone. Absence is never asserted over a covered beat."""
    from newslens import coverage
    cat = catalog.load()
    cmap = coverage.load(cat=cat)
    without = config.SourcesConfig(sources=[
        config.Source(name="CNN", rss_url="https://x.invalid/cnn"),
        config.Source(name="NPR", rss_url="https://x.invalid/npr"),
        config.Source(name="The Guardian — World", rss_url="https://x.invalid/g"),
    ])
    st = coverage.state(without, cmap, cat)
    for name in HEALTH_NAMES:
        assert st.of(name).state == coverage.UNSERVED, name
    assert st.of("Geopolitics").state == coverage.SERVED

    withkff = config.SourcesConfig(sources=list(without.sources) + [
        config.Source(name="KFF Health News", rss_url="https://x.invalid/kff")])
    st2 = coverage.state(withkff, cmap, cat)
    assert st2.of("Medicaid").state == coverage.SERVED
    assert st2.of("Public Health").state == coverage.SERVED
    # KFF is not a lab-science outlet and the map does not pretend otherwise
    assert st2.of("Clinical Trials").state == coverage.UNSERVED


def test_an_unfetchable_source_cannot_serve_a_beat():
    """reference_only is cited and never fetched; disabled never runs. Either
    one counting as coverage would be the purest form of the fabrication this
    mechanism exists to prevent — claiming a beat off an outlet the pipeline
    is structurally forbidden to read."""
    from newslens import coverage
    cat = catalog.load()
    cmap = coverage.load(cat=cat)
    cited = config.SourcesConfig(sources=[
        config.Source(name="Associated Press", tier="reference_only"),
        config.Source(name="Reuters", tier="reference_only"),
    ])
    assert coverage.state(cited, cmap, cat).of("Geopolitics").state == \
        coverage.UNSERVED
    off = config.SourcesConfig(sources=[
        config.Source(name="The Guardian — World", rss_url="https://x.invalid/g",
                      enabled=False)])
    assert coverage.state(off, cmap, cat).of("Geopolitics").state == \
        coverage.UNSERVED


def test_the_credit_topics_are_headlines_only_against_the_shipped_template(
        template_cfg):
    """The disclosure line's first customer, from the NL-135 research finding:
    Bloomberg, FT and WSJ all carry the private-credit beat and all three are
    titles-and-summaries. The DOMAIN is not headlines-only (CNBC and Chartbook
    carry it in full text), which is exactly why beat-level entries have to
    beat inheritance — a domain-grain map could not tell this truth."""
    from newslens import coverage
    cat = catalog.load()
    st = coverage.state(template_cfg, coverage.load(cat=cat), cat)
    for topic in CREDIT_TOPICS:
        s = st.of(topic)
        assert s.state == coverage.HEADLINES, (topic, s.state)
        assert s.served_count >= 3, topic
    assert st.of("Systemic Risk").state == coverage.SERVED


def test_positive_claims_stop_at_the_domain_grain(template_cfg):
    """'4 sources cover this area' is INCLUSION and renders on domain rows
    only. A per-topic positive claim is unearnable from a per-feed map, and
    the badge for a served topic is therefore silence."""
    html = commissioning.render("picking", template_cfg, {})
    rows = re.findall(
        r'data-level="(domain|topic)" data-name="[^"]*" data-cov="([a-z]*)"'
        r'[^>]*>.*?</span><span class="pick-name">[^<]*</span>'
        r'(<span class="cov">([^<]*)</span>)?', html)
    assert rows, "no pick rows parsed — the pin is reading nothing"
    for level, state, _, badge in rows:
        if level == "topic" and state == "served":
            assert not badge, ("a served topic claimed coverage", badge)
        if level == "domain" and state == "served":
            assert re.match(r"^\d+ sources? covers? this area\.$", badge), badge


def test_no_surface_ever_claims_sufficiency(template_cfg):
    """The banned register, swept on rendered bytes. Static mapping can prove
    absence; nothing on this page may promise that a topic will be served."""
    html = commissioning.render("picking", template_cfg, {})
    for banned in ("you're covered", "you are covered", "well covered",
                   "fully covered", "we've got", "we have you", "plenty of",
                   "all set", "good coverage", "great coverage"):
        assert banned not in html.lower(), banned


def test_the_coverage_state_is_computed_live_and_never_persisted(tmp_path):
    """Kass's staleness demand, mechanically. A grade written into a profile
    would be wrong the day the org approves a slate; nothing here writes."""
    from newslens import coverage
    src = tmp_path / "sources.yaml"
    src.write_text(paths.PROFILE_SOURCES_TEMPLATE.read_text(encoding="utf-8"),
                   encoding="utf-8")
    before = src.read_bytes()
    cfg = config.load_sources(src)
    cat = catalog.load()
    st = coverage.state(cfg, coverage.load(cat=cat), cat)
    html = commissioning.render("picking", cfg, {}, cat=cat)
    assert st.by_name                       # the computation really ran
    assert 'class="cov"' in html            # and really reached the page
    assert src.read_bytes() == before
    # and the same file read twice answers the same way — no memo, no cache
    assert coverage.state(config.load_sources(src)).by_name.keys() == \
        st.by_name.keys()


def test_the_consequence_line_counts_the_readers_own_unserved_picks():
    """Server-rendered, from the reader's own file, before a byte of script
    runs — the count-line precedent. This is the sentence the 2026-08-02 walk
    needed and did not get."""
    from newslens import coverage
    cat = catalog.load()
    # The walk's shape: money topics served by the list she has, health topics
    # served by nothing in it. Inflation is the control — it must NOT be
    # counted, or the line is just an alarm that always rings.
    cfg = config.SourcesConfig(
        sources=[config.Source(name="CNN", rss_url="https://x.invalid/c"),
                 config.Source(name="CNBC", rss_url="https://x.invalid/n")],
        interests_broad=["Public Health"],
        interests_granular=["Vaccine Policy", "Medicaid", "Inflation"])
    st = coverage.state(cfg, coverage.load(cat=cat), cat)
    assert st.of("Inflation").state == coverage.SERVED
    unserved = st.unserved_among(commissioning.saved_picks(cfg, cat))
    assert sorted(unserved) == ["Medicaid", "Public Health", "Vaccine Policy"]
    html = commissioning.render("picking", cfg, {}, cat=cat)
    assert labels.COMMISSION_COV_UNSERVED_HEAD + " 3 " + \
        labels.COMMISSION_COV_UNSERVED_MANY in html
    assert labels.COMMISSION_COV_UNSERVED_TAIL in html
    assert 'id="c1-coverage" hidden' not in html


def test_the_consequence_line_is_absent_when_nothing_is_unserved(template_cfg):
    """No cheerful inverse. The line appears when there is bad news and is
    silent otherwise — it never congratulates the reader on coverage."""
    html = commissioning.render("picking", template_cfg, {})
    assert 'id="c1-coverage" hidden' in html


def test_the_client_recomputes_the_summary_from_the_same_words(template_cfg,
                                                              monkeypatch):
    """The label-liveness contract this page already keeps: the script holds no
    string of its own, and no coverage RULE crosses the wire — the client
    counts the data-cov the server stamped."""
    monkeypatch.setattr(labels, "COMMISSION_COV_UNSERVED_MANY", "ZZ-TOPICS.")
    html = commissioning.render("picking", template_cfg, {})
    assert "ZZ-TOPICS." in html
    assert "data-cov" in html
    assert "getAttribute('data-cov')" in html


def test_hand_added_sources_the_map_cannot_classify_are_disclosed():
    """The honesty valve. Every absence badge on the page is a claim over the
    reader's list; a source the map has never read has to be named, or the
    claim is made over sources nobody read."""
    from newslens import coverage
    cfg = config.SourcesConfig(sources=[
        config.Source(name="CNN", rss_url="https://x.invalid/c"),
        config.Source(name="Some Local Paper", rss_url="https://x.invalid/l")])
    st = coverage.state(cfg)
    assert st.unclassified == ("Some Local Paper",)
    html = commissioning.render("picking", cfg, {})
    assert labels.COMMISSION_COV_UNKNOWN_ONE in html
    assert 'id="c1-cov-note" hidden' not in html


def test_an_unreadable_map_costs_the_badges_and_makes_no_claims(
        template_cfg, monkeypatch):
    """Degrade rule, stated as a claim rule: a page that cannot read the map
    has no basis for ANY coverage sentence, so it renders none — rather than
    asserting absence it cannot prove."""
    from newslens import coverage
    monkeypatch.setattr(paths, "FEED_COVERAGE",
                        paths.TEMPLATES_DIR / "no-such-map.yaml")
    with pytest.raises(coverage.CoverageError):
        coverage.load()
    html = commissioning.render("picking", template_cfg, {})
    assert 'class="cov"' not in html
    assert labels.COMMISSION_COV_NONE not in html
    assert 'id="c1-coverage" hidden' in html
    assert "Topics" in html                 # the page itself still renders


def test_entertainment_and_sports_feeds_map_to_nothing_on_purpose():
    """Vocabulary honesty as data, not as a comment: fourteen enabled feeds
    covering zero catalog names, because C1 contains no entertainment or
    sports vocabulary. Enabling bought ingestion, not a persona. If NL-116's
    authorship round ever mints those names, this pin is what fails and sends
    someone to the map."""
    from newslens import coverage
    cmap = coverage.load()
    silent = ["BBC News — Entertainment & Arts", "NPR Culture", "NPR Music",
              "NBC News — Pop Culture", "PBS NewsHour — Arts",
              "The Guardian — Culture", "TheWrap", "Stereogum",
              "BBC Sport", "Washington Times — Sports", "Yahoo Sports",
              "ESPN", "CBS Sports", "The Guardian — Sport"]
    for name in silent:
        assert cmap.knows(name), name
        assert cmap.covers(name) == frozenset(), name
    names = {n.lower() for n in catalog.load().names()}
    for absent in ("entertainment", "sports", "film", "music", "television"):
        assert absent not in names


# ---------------------------------------------------------------------------
# 3. NL-136 ① — attribution-only as design, the dead aggregator dropped
# ---------------------------------------------------------------------------

def test_the_dead_aggregator_is_gone_from_the_template(template_cfg):
    """A permanently-off aggregator every new reader inherited, counted in
    their pack, able to do nothing for them."""
    assert "Whatfinger Business" not in {s.name for s in template_cfg.sources}
    # POSTURE A (ENG-M0): nothing in the slate is held any more, so the
    # disabled set is empty. Derived from SLATE either way, never typed.
    assert {s.name for s in template_cfg.disabled_sources} == {
        n for n, (_, _, enabled) in SLATE.items() if not enabled} == set()
    assert not [s for s in template_cfg.sources if s.tier == "cautious"]
    # NOT a source-text sweep: the file still NAMES the outlet in the comment
    # that records the drop and the one-block restore recipe, and that comment
    # is a feature. The pin is on the PARSED catalog, which is the only thing
    # a reader's profile inherits.


def test_the_pack_sentence_presents_attribution_only_as_design(template_cfg):
    """Ruling ①. 'cited but never fetched' read as a shortfall; the four are a
    decision. Numbers still counted, never typed."""
    sentence = commissioning.source_pack_sentence(template_cfg)
    # POSTURE A (ENG-M0): the 14 held feeds are enabled, so the fetched count
    # rises 51 -> 65 and the "sources are off" clause DROPS (the zero guard).
    assert sentence == ("69 outlets. 65 are fetched each morning. "
                        "4 are attribution-only by design.")
    assert "cited but never fetched" not in sentence
    assert "aggregator is off" not in sentence
    html = commissioning.render("picking", template_cfg, {})
    assert sentence in html


def test_the_pack_arithmetic_survives_the_zero_disabled_case(template_cfg):
    """Counted, never typed. ENG-M0 restores POSTURE A: the 14 held feeds are
    enabled, so the disabled set is EMPTY again and this test returns to being
    what its name says — the zero-disabled case. The '0' guard is what it was
    always for: a zero count drops its clause rather than rendering ' 0 '."""
    assert len(template_cfg.disabled_sources) == 0
    assert len(template_cfg.sources) == 69
    assert len(template_cfg.fetchable_sources) == 65
    assert len(template_cfg.reference_only_sources) == 4
    assert " 0 " not in commissioning.source_pack_sentence(template_cfg)


# ---------------------------------------------------------------------------
# Carried invariants — BORN GREEN, and labelled so the proof class stays honest
# ---------------------------------------------------------------------------

def test_the_pack_sentence_still_drops_a_clause_it_cannot_fill():
    """CARRIED INVARIANT (born green): the drop-a-zero-clause guard predates
    this batch. It is pinned again here because NL-136 handed it its first
    real caller — the shipped template now HAS zero disabled sources."""
    cfg = config.SourcesConfig(sources=[
        config.Source(name="Only", rss_url="https://x.invalid/f")])
    assert commissioning.source_pack_sentence(cfg) == \
        "1 outlet. 1 is fetched each morning."


def test_the_template_still_parses_clean_with_zero_interests(template_cfg):
    """CARRIED INVARIANT (born green): the drift guard on the template, re-run
    across a 28-feed edit. A stray tab or a duplicated key would surface as
    `problems`, and provisioning would hand a new reader a broken file."""
    assert template_cfg.problems == []
    assert template_cfg.warnings == []
    assert template_cfg.interests_broad == []
    assert template_cfg.interests_granular == []


def _tmpfile(text: str):
    import tempfile
    from pathlib import Path
    p = Path(tempfile.mkdtemp()) / "map.yaml"
    p.write_text(text, encoding="utf-8")
    return p
