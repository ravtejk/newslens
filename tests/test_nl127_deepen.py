"""NL-127 DEEPEN — the R# lane (principal's ruling 2026-08-24, DECISIONS item 2).

WHAT HE RULED, and where each clause is pinned here:

  (1) DEEPEN is built over the R# SONAR ROWS ONLY. The 2026-08-14 scout
      falsified the cluster-side premise against the tree (the pipeline already
      attempts every cluster URL), so that stage is dead and nothing here tests
      it. §D pins that a deepened key stays an R key — it never becomes S# and
      never earns cluster corroboration.
  (2) "Sonar-result fetches obey the SAME outlet-tier law as cluster fetches
      (the ~76 reference_only/headline_only rows stay unfetchable)." §A. The
      refusal is not a new rule: it is `fetch_article` returning TIER_EXCLUDED
      with the 2026-07-06 ruling in its own detail string, and the pins assert
      NO SOCKET was opened, not merely that a flag was set. Gate R-2 (fix-loop
      2026-08-24) extended §A to each restricted outlet's OWN shortener and
      alternate registrable — `_ALIAS_SOURCE_HOSTS` — because the opener
      follows redirects and the tier verdict is taken on the pre-redirect
      string. Same table shape, same refusal, no new vocabulary.
  (3) The trigger is importance x deficit WITH REAL GATING — "the experiment's
      encoding reduces to deficit-only, don't repeat it". §B pins BOTH
      directions of blocking on the principal's OWN measured corpus, including
      the adjacent separating pair that makes importance load-bearing.
  (4) Charter rider (Bucket-B, research/2026-08-24--nl127-bucketB.md): DEEPEN
      persists PER-URL fetch outcome/reason, because production drops them.
      §F. Same report: SINGLE attempt per URL (34 retries rescued 0) — §C —
      and a URL-shape prefilter (~21% of attempts at zero prose cost) — §C.

THE LANE SHIPS INERT. `DEEPEN_ARM` defaults to OFF (gate R-1, 2026-08-24, on
NL-151's precedent), so every pin whose subject is the ON behaviour requests the
non-autouse `armed` fixture below and says so in its own signature. A pin that
does NOT request it is running at the arm his committed tree carries — which is
what §G pins directly, and why `armed` must never become autouse.

PROOF CLASSES — measured, not asserted. Against a read-only `git archive HEAD`
export (de37c46, PYTHONPATH set to the export's src, `newslens.__file__` proven
inside it) this file runs **29 failed, 2 passed, 43 errors** — 72 of 74 pins
non-passing. The 43 ERRORS are the `armed` fixture failing at SETUP: it does a
raising `monkeypatch.setattr(analysis, "DEEPEN_ARM", ...)` and then asserts
`deepen_armed()`, neither of which exists at HEAD. That is deliberate — a
non-raising fixture would silently mint a dead attribute if the constant were
ever renamed, and every armed pin below would quietly run at whatever the real
arm is, which is the "pin over a dead path" class. `analysis` at HEAD has no
`deepen_sonar_results`, no `DEEPEN_*`, no `tier_for_url` and no
`cluster_url_set`, so most pins cannot even import their subjects — which is a
WEAK red for a new surface. Every pin guarding a JUDGEMENT therefore also
carries a MUTATION receipt taken against the LANDED code.

TWO carried invariants remain born-GREEN, both in §H, and both are labelled as
such and carry MUT-8 instead. The other two born-green pins of the pre-fix
build became born-RED in the FIX-1 loop, and the reason is worth stating rather
than absorbing: `test_the_shared_robots_cache_...` and
`test_a_story_deepen_did_not_fire_on_...` both need the lane ARMED for their
route to reach the code they name (the shared cache is only shared if the
DEEPEN pass runs; "the lane never fired" must be the DEFICIT's verdict, not the
arm's). Arming them means touching `analysis.DEEPEN_ARM`, which does not exist
at HEAD — so they now fail there by attribute absence like their peers. Their
MUT-6 / MUT-7 receipts were re-run at the fixed bytes and both still bite.

  BORN-RED (weak, attribute absence at HEAD) + a mutation receipt each:

  MUT-1  DEEPEN_TIER_WEIGHT flattened to {"full": 1.0, "medium": 1.0} — i.e.
         the charter's named failure, "reduces to deficit-only" -> 5 red,
         including `test_the_trigger_does_not_reduce_to_deficit_only`.
  MUT-2  `tier_for_url` returns DEEPEN_UNKNOWN_TIER unconditionally (the tier
         law bypassed) -> 13 red across §A/§C/§F at review bytes; 22 red at
         shipped bytes (the FIX-2 alias params + extended yaml pin biting —
         gate micro-confirm ruled the growth strengthening).
  MUT-3  a spaced retry ladder added to the fetch loop -> 1 red
         (`test_deepen_makes_exactly_one_attempt_per_url`).
  MUT-4  the substitution also writes the fetched page `title` -> 2 red (§D).
  MUT-5  `sa.fetch_ok += deepen ok` -> 2 red (1 §E + 1 §F run-report; the
         brief-header pin is relative and stays green) — the ruled-decision leak.
  MUT-6  the robots cache is not shared with the cluster pass -> 1 red
         (re-run at the fixed bytes; its pin now arms itself, see above).
  MUT-7  the brief header always carries a `deepen` key -> 1 red
         (re-run at the fixed bytes; its pin now arms itself, see above).
  MUT-9  the diagnose readout line rendered unconditionally -> 1 red.
  MUT-10 the `analyze` verb prints the deepen row unconditionally -> 1 red.

  THE TWO BORN-GREEN CARRIED INVARIANTS (§H), which carry MUT-8 instead:
  MUT-8  the deep-view "— full text" suffix made unconditional -> 2 red.
         `test_the_deep_view_names_a_deepened_source_row[row1]` and `[row2]`
         are the "nothing else moved" half and are arm-INDEPENDENT (they render
         a source row; no lane runs), which is exactly why they stay green at
         HEAD. They assert the suffix's ABSENCE, because a bare substring
         assertion is satisfied by an unconditional suffix.

Offline by construction: autouse sandbox (conftest), injected fetch/sonar/chat,
no network, no key, $0.
"""

from __future__ import annotations

import json
import types
import urllib.error

import pytest

from newslens import analysis, config, server

from test_analysis_brief_qa import (  # the stage's own harness, reused
    DATE, ENV_OK, chat_sentinel, seed_min, story_kwargs,
)


# ---------------------------------------------------------------------------
# kit
# ---------------------------------------------------------------------------

# Comfortably over MIN_EXTRACT_CHARS (700) with no anchors, so
# `classify_extraction` returns OK on any host the fake serves.
_ARTICLE = (b"<html><head><title>Deepened page</title></head><body><article>"
            + b"<p>The delegation reached the port before dawn and the "
              b"minister confirmed the shipment had cleared inspection.</p>" * 12
            + b"</article></body></html>")

DEEP_TEXT_MARK = "cleared inspection"


class FetchSpy:
    """A fetch fake that RECORDS every URL it is asked for. The pins assert on
    the recorded list, so 'no socket was opened' is measured rather than
    inferred from an outcome string."""

    def __init__(self, robots_403=(), dead=()):
        self.calls = []
        self.robots_403 = set(robots_403)
        self.dead = set(dead)

    def __call__(self, url, timeout, cap=0, user_agent=""):
        self.calls.append(url)
        if url.endswith("/robots.txt"):
            host = analysis._outlet_of(url)
            if host in self.robots_403:
                raise urllib.error.HTTPError(url, 403, "forbidden", {}, None)
            raise urllib.error.HTTPError(url, 404, "nf", {}, None)
        if url in self.dead:
            raise urllib.error.HTTPError(url, 403, "forbidden", {}, None)
        return _ARTICLE

    @property
    def article_calls(self):
        return [u for u in self.calls if not u.endswith("/robots.txt")]


def src(name, tier="full", rss_url=None):
    return config.Source(name=name, rss_url=rss_url, tier=tier)


# His real restricted set, as sources.yaml carries it: the four reference_only
# outlets have NO rss_url (config.py:157), which is the whole reason
# `_HOSTLESS_SOURCE_HOSTS` exists.
HIS_RESTRICTED = [
    src("Associated Press", "reference_only"),
    src("Reuters", "reference_only"),
    src("The New York Times", "reference_only"),
    src("Wikipedia", "reference_only"),
    src("Bloomberg Markets", "headline_only",
        "https://feeds.bloomberg.com/markets/news.rss"),
    src("Washington Post — World", "headline_only",
        "https://feeds.washingtonpost.com/rss/world"),
    src("Financial Times", "headline_only", "https://www.ft.com/rss/home"),
    # Added with the FIX-2 alias rows (gate R-2): `econ.st` is keyed to this
    # NAME, so the alias pin needs the name present to prove the key matches.
    src("The Economist", "headline_only",
        "https://www.economist.com/latest/rss.xml"),
    src("CNBC", "full", "https://search.cnbc.com/rs/search/combinedcms/view.xml"),
]


def cfg_of(sources):
    return types.SimpleNamespace(sources=list(sources))


def results(*urls):
    """Sonar `search_results`, post-clamp shape: the ≤303-char vendor locator."""
    return [{"url": u, "title": f"Title for {u}", "date": "2026-08-24",
             "snippet": "A three-hundred-character locator stands in for the "
                        "page itself."} for u in urls]


def deepen(sonar_results, tier="full", held=0, sources=(), **over):
    kw = dict(cfg=cfg_of(sources), fetch=over.pop("fetch", FetchSpy()),
              sleep=lambda s: None)
    kw.update(over)
    return analysis.deepen_sonar_results(
        sonar_results, tier=tier, held_chars=held, **kw)


@pytest.fixture
def armed(monkeypatch):
    """Arm the lane FOR THIS TEST — the landed form NL-151 uses (its pins take
    `monkeypatch.setattr(analysis, "FETCH_SKIP_ARM", arm)` per test).

    DELIBERATELY NOT autouse, and that is the whole design: the SHIPPED default
    is OFF (gate R-1, 2026-08-24), and an autouse fixture would unpin it — no
    test in this file would then run at the arm his tree actually carries, and
    every pin below would stop having to declare that its subject is the ON
    behaviour. Requesting this fixture IS that declaration. A test that does not
    request it runs at the shipped default, which is what §G pins directly."""
    monkeypatch.setattr(analysis, "DEEPEN_ARM", analysis.DEEPEN_ARM_ON)
    assert analysis.deepen_armed() is True


# ===========================================================================
# A. HIS TIER LAW — the ruling's own parenthetical, as a measurement
# ===========================================================================

@pytest.mark.parametrize("url,outlet", [
    ("https://www.reuters.com/world/middle-east/a-2026-08-24/", "Reuters"),
    ("https://apnews.com/article/sanctions-9f3ac21b", "Associated Press"),
    ("https://www.nytimes.com/2026/08/24/world/a.html", "The New York Times"),
    ("https://en.wikipedia.org/wiki/Sanctions", "Wikipedia"),
    ("https://www.bloomberg.com/news/articles/2026-08-24/a", "Bloomberg Markets"),
    ("https://www.washingtonpost.com/world/2026/08/24/a/", "Washington Post — World"),
    ("https://www.ft.com/content/abc-123", "Financial Times"),
])
def test_a_restricted_outlets_sonar_url_never_opens_a_socket(url, outlet, armed):
    """His ruling's parenthetical, measured on the fetch fake's call list."""
    spy = FetchSpy()
    out, ledger, warns = deepen(results(url), tier="full", held=0,
                                sources=HIS_RESTRICTED, fetch=spy)
    assert spy.article_calls == [], spy.article_calls
    assert spy.calls == [], "a robots.txt GET is still a socket"
    row, = ledger
    assert row["outcome"] == analysis.TIER_EXCLUDED
    assert row["outlet"] == outlet
    # The refusal string is `fetch_article`'s own — the SAME law, not a copy.
    assert "principal ruling 2026-07-06" in row["detail"]
    assert out[0]["snippet"].startswith("A three-hundred-character")
    assert "deepened" not in out[0]
    assert warns == []


def test_a_full_tier_sonar_url_is_fetched_and_its_snippet_becomes_the_page(armed):
    spy = FetchSpy()
    url = "https://kyivindependent.com/news/a-real-report/"
    out, ledger, _ = deepen(results(url), tier="full", held=0,
                            sources=HIS_RESTRICTED, fetch=spy)
    assert spy.article_calls == [url]
    row, = ledger
    assert row["outcome"] == analysis.OK
    assert row["chars"] > analysis.MIN_EXTRACT_CHARS
    assert DEEP_TEXT_MARK in out[0]["snippet"]
    assert out[0]["deepened"] is True


ALIAS_URLS = [
    # (url, the outlet label the ledger must read)
    ("https://nyti.ms/3xKq2mP", "The New York Times"),
    ("https://www.nyt.com/2026/08/24/world/a.html", "The New York Times"),
    ("https://apne.ws/9f3ac21", "Associated Press"),
    ("https://www.ap.org/press-releases/2026/a", "Associated Press"),
    ("https://reut.rs/4bQz9Lx", "Reuters"),
    ("https://bloom.bg/2Kd8Rm1", "Bloomberg Markets"),
    ("https://wapo.st/3JhT7ne", "Washington Post — World"),
    ("https://econ.st/4aVc2Qd", "The Economist"),
]


@pytest.mark.parametrize("url,outlet", ALIAS_URLS)
def test_a_restricted_outlets_own_shortener_never_opens_a_socket(url, outlet,
                                                                 armed):
    """QA's F-5, closed by gate R-2. A restricted outlet reached through its OWN
    official shortener or alternate registrable breaches his 2026-07-06 ruling
    by the back door: the shared opener FOLLOWS REDIRECTS, so the tier decision
    is taken on "reut.rs" — a string no source claims — and the socket that
    opens lands on reuters.com anyway.

    BORN RED AT THE PRE-FIX BYTES, not merely at HEAD: with `_ALIAS_SOURCE_HOSTS`
    absent these eight hosts resolve to DEEPEN_UNKNOWN_TIER and are fetched.
    Base rate is 0 of 634 all-time R# URLs, so the class is latent — but the URL
    shapes are the vendor's to choose, not ours."""
    spy = FetchSpy()
    out, ledger, warns = deepen(results(url), tier="full", held=0,
                                sources=HIS_RESTRICTED, fetch=spy)
    assert spy.article_calls == [], spy.article_calls
    assert spy.calls == [], "a robots.txt GET is still a socket"
    row, = ledger
    assert row["outcome"] == analysis.TIER_EXCLUDED
    assert row["outlet"] == outlet
    # The SAME refusal, not a copy: `fetch_article`'s own detail string.
    assert "principal ruling 2026-07-06" in row["detail"]
    assert "deepened" not in out[0]
    assert warns == []


def test_an_outlet_he_does_not_list_gets_the_same_default_a_cluster_item_gets():
    """`_cluster_items_for_slot` defaults an unrecognised outlet to 'full'
    (analysis.py, `tier_by_outlet.get(r["outlet"], "full")`). The R lane uses
    the same default — that identity IS 'the same outlet-tier law', and it is
    what keeps the fetchable pool at 197 of the window's 322 rows rather than
    collapsing it to the handful of hosts that happen to have feeds."""
    assert analysis.DEEPEN_UNKNOWN_TIER == "full"
    by_host, _ = analysis.outlet_tiers_by_host(cfg_of(HIS_RESTRICTED))
    tier, label = analysis.tier_for_url("https://english.elpais.com/a", by_host)
    assert (tier, label) == ("full", "english.elpais.com")
    assert analysis.tier_allows_fetch(tier)


def test_a_restricted_source_with_no_derivable_host_is_reported_not_absorbed(armed):
    """THE TRIPWIRE. A hostless restricted outlet this module has never seen
    would otherwise fall through to the unknown default and be fetched — the
    one way his ruling breaks quietly. It must surface as a run warning."""
    exotic = src("Some Paywalled Quarterly", "headline_only")
    by_host, unresolved = analysis.outlet_tiers_by_host(
        cfg_of(HIS_RESTRICTED + [exotic]))
    assert unresolved == ["Some Paywalled Quarterly"]

    _, _, warns = deepen(results("https://kyivindependent.com/a"),
                         tier="full", held=0, sources=HIS_RESTRICTED + [exotic])
    assert len(warns) == 1
    assert "Some Paywalled Quarterly" in warns[0]
    assert "2026-08-24 tier ruling" in warns[0]


def test_his_real_sources_yaml_leaves_no_restricted_outlet_unresolved():
    """The shipped file, read as shipped. Today's answer must be zero — this is
    the pin that goes red the day a hostless restricted outlet is added."""
    from conftest import PROTOTYPE_ROOT
    cfg = config.load_sources(PROTOTYPE_ROOT / "sources.yaml")
    by_host, unresolved = analysis.outlet_tiers_by_host(cfg)
    assert unresolved == []
    for host in ("reuters.com", "apnews.com", "nytimes.com", "wikipedia.org",
                 "bloomberg.com", "washingtonpost.com", "ft.com"):
        tier, _ = by_host[host]
        assert not analysis.tier_allows_fetch(tier), host
    # FIX-2 (gate R-2): the alias rows are keyed by THE NAME AS HE WROTE IT, so
    # they are only live if the keys match his file byte-for-byte — a typo in
    # an em-dash or a casing slip would leave the shortener fetchable and this
    # pin is what catches it. Driven through `tier_for_url`, the same resolver
    # the lane uses, on a URL shaped like the vendor would emit one.
    for url in ("https://nyti.ms/x", "https://www.nyt.com/x",
                "https://apne.ws/x", "https://www.ap.org/x",
                "https://reut.rs/x", "https://bloom.bg/x",
                "https://wapo.st/x", "https://econ.st/x",
                "https://on.ft.com/x"):        # covered by ft.com, not tabled
        tier, label = analysis.tier_for_url(url, by_host)
        assert not analysis.tier_allows_fetch(tier), (url, tier, label)


def test_the_public_suffix_guard_keeps_two_co_uk_outlets_apart():
    """Without the guard `feeds.bbci.co.uk` and `independent.co.uk` both
    collapse to 'co.uk' and one outlet inherits the other's tier. Driven with a
    RESTRICTED .co.uk source so the failure direction is the dangerous one."""
    sources = [src("A Paywalled Paper", "headline_only",
                   "https://feeds.paywalled.co.uk/rss")]
    by_host, unresolved = analysis.outlet_tiers_by_host(cfg_of(sources))
    assert unresolved == []
    blocked, _ = analysis.tier_for_url("https://www.paywalled.co.uk/a", by_host)
    assert not analysis.tier_allows_fetch(blocked)
    free, label = analysis.tier_for_url("https://www.independent.co.uk/a", by_host)
    assert analysis.tier_allows_fetch(free)
    assert label == "independent.co.uk"


def test_the_most_restrictive_tier_wins_on_a_shared_host():
    sources = [src("Free Wing", "full", "https://news.shared.com/rss"),
               src("Paywalled Wing", "headline_only",
                   "https://feeds.shared.com/rss")]
    by_host, _ = analysis.outlet_tiers_by_host(cfg_of(sources))
    tier, _ = analysis.tier_for_url("https://shared.com/a", by_host)
    assert not analysis.tier_allows_fetch(tier)


# ===========================================================================
# B. THE TRIGGER — importance x deficit, with REAL gating
# ===========================================================================
#
# Every (tier, held) pair below is a REAL brief from the principal's own
# analysis_retrieval rows, editions 2026-08-01..24, measured mode=ro.

HIS_CORPUS = [
    # (date, slot, tier, held cluster full-text chars, must fire)
    ("2026-08-01", 2, "medium", 3994, True),
    ("2026-08-03", 1, "full", 10202, True),
    # held=0 is a HISTORICAL row and is no longer reachable: NL-151 armed on
    # 2026-08-14, and Gates A/B now return above Sonar rung 1 for any story
    # with no extraction, so a story reaching DEEPEN holds at least
    # MIN_EXTRACT_CHARS. Kept as an arithmetic pin on the deficit clamp.
    ("2026-08-03", 3, "medium", 0, True),
    ("2026-08-06", 1, "full", 13093, True),
    ("2026-08-11", 3, "medium", 4327, True),
    ("2026-08-12", 3, "medium", 4502, True),
    ("2026-08-13", 1, "full", 13606, True),
    ("2026-08-01", 1, "full", 38200, False),
    ("2026-08-06", 3, "medium", 9989, False),
    ("2026-08-07", 3, "medium", 5745, False),
    ("2026-08-10", 2, "medium", 14064, False),
    ("2026-08-14", 1, "full", 79155, False),
    ("2026-08-14", 3, "medium", 13208, False),
    ("2026-08-24", 2, "medium", 14498, False),
]


@pytest.mark.parametrize("date,slot,tier,held,fires", HIS_CORPUS)
def test_the_trigger_verdict_on_his_own_corpus(date, slot, tier, held, fires):
    fired = analysis.deepen_score(tier, held) >= analysis.DEEPEN_TRIGGER
    assert fired is fires, (
        f"{date} slot {slot} {tier} held={held} "
        f"deficit={analysis.deepen_deficit(held):.3f} "
        f"score={analysis.deepen_score(tier, held):.3f}")


def test_the_trigger_does_not_reduce_to_deficit_only():
    """THE CHARTER'S NAMED FAILURE. There must exist two of his real briefs
    whose DEFICITS are all but identical and whose verdicts differ — decided by
    importance alone. If importance is not load-bearing this cannot hold."""
    full_fires = analysis.deepen_score("full", 10202)      # 2026-08-03 slot 1
    medium_no = analysis.deepen_score("medium", 9989)      # 2026-08-06 slot 3
    assert abs(analysis.deepen_deficit(10202)
               - analysis.deepen_deficit(9989)) < 0.02
    assert full_fires >= analysis.DEEPEN_TRIGGER > medium_no

    # And the band is not a single lucky point: over his window, importance
    # separates every deficit in [0.40, 0.80).
    for held in range(4900, 14400, 500):
        d = analysis.deepen_deficit(held)
        if 0.40 <= d < 0.80:
            assert analysis.deepen_score("full", held) >= analysis.DEEPEN_TRIGGER
            assert analysis.deepen_score("medium", held) < analysis.DEEPEN_TRIGGER


def test_the_deficit_conjunct_blocks_the_most_important_story_there_is(armed):
    """A `full` story that already holds the material budget in cluster prose
    gets nothing — importance alone must never fire the lane."""
    assert analysis.deepen_deficit(analysis.DEEPEN_TEXT_FLOOR) == 0.0
    assert analysis.deepen_score("full", analysis.DEEPEN_TEXT_FLOOR) == 0.0
    spy = FetchSpy()
    out, ledger, _ = deepen(results("https://kyivindependent.com/a"),
                            tier="full", held=analysis.DEEPEN_TEXT_FLOOR,
                            fetch=spy)
    assert spy.calls == []
    assert ledger == []
    assert "deepened" not in out[0]


def test_a_tier_outside_the_depth_ladder_can_never_fire():
    """L3 permits degraded coverage for In-Brief stories; whether DEEPEN reaches
    them is not in his 2026-08-24 ruling, so the weight table refuses."""
    for tier in ("quick", "in-brief", ""):
        assert analysis.deepen_score(tier, 0) == 0.0


def test_the_deficit_is_the_material_budget_not_a_chosen_number():
    assert analysis.DEEPEN_TEXT_FLOOR == analysis.MATERIAL_BUDGET_CHARS


# ===========================================================================
# C. FETCH DISCIPLINE — what Bucket-B measured, as code
# ===========================================================================

def test_deepen_makes_exactly_one_attempt_per_url(armed):
    """34 spaced retries rescued 0 outcomes (Bucket-B). A dead URL is asked
    once and recorded once."""
    dead = "https://kyivindependent.com/dead/"
    spy = FetchSpy(dead=[dead])
    out, ledger, _ = deepen(results(dead), tier="full", held=0, fetch=spy)
    assert spy.article_calls == [dead]
    row, = ledger
    assert row["outcome"] == analysis.ERROR
    assert row["detail"] == "HTTP 403"
    assert "deepened" not in out[0]


@pytest.mark.parametrize("url,reason", [
    # The scout's marquee specimen — and it is DATED, which is why this is not
    # `discovery.url_reject_reason` (that predicate rescues a dated video page
    # as legitimate coverage; this one asks whether prose can be extracted).
    ("https://www.aljazeera.com/video/newsfeed/2026/7/26/new-minister",
     "prose-less page form (/video/)"),
    ("https://www.aljazeera.com/news/liveblog/2026/8/12/iran-war-live",
     "prose-less page form (/liveblog/)"),
    ("https://thehill.com/video-clips/6046874-watch-live-bessent",
     "prose-less page form (/video-clips/)"),
    ("https://www.cnn.com/europe/live-news/russia-ukraine-war-news-04-18-23",
     "prose-less page form (/live-news/)"),
    ("https://www.youtube.com/watch?v=abc", "audio-video-page"),
    ("https://www.facebook.com/post/123", "social-post"),
])
def test_the_url_shape_prefilter_skips_a_prose_less_page_without_a_get(url, reason,
                                                                       armed):
    spy = FetchSpy()
    out, ledger, _ = deepen(results(url), tier="full", held=0, fetch=spy)
    assert spy.calls == []
    row, = ledger
    assert row["outcome"] == analysis.DEEPEN_SKIP_SHAPE
    assert row["detail"] == reason
    assert "deepened" not in out[0]


def test_a_dated_article_url_is_not_caught_by_the_prefilter():
    assert analysis.prose_unlikely(
        "https://www.cnbc.com/2026/08/24/trump-iran-economy.html") == ""


def test_the_fetch_budget_is_capped_per_story(armed):
    urls = [f"https://kyivindependent.com/a{i}/" for i in range(6)]
    spy = FetchSpy()
    out, ledger, _ = deepen(results(*urls), tier="full", held=0, fetch=spy)
    assert len(spy.article_calls) == analysis.DEEPEN_MAX_URLS
    assert [r["outcome"] for r in ledger] == (
        [analysis.OK] * analysis.DEEPEN_MAX_URLS
        + [analysis.DEEPEN_SKIP_BUDGET] * (6 - analysis.DEEPEN_MAX_URLS))
    assert [r["rank"] for r in ledger] == [1, 2, 3, 4, 5, 6]


def test_a_tier_refusal_does_not_consume_the_fetch_budget(armed):
    """Measured need, not a nicety: 2026-08-12 slot 3 carried SIX restricted
    results (5 Reuters + 1 Bloomberg) among its eight. Charging refusals to the
    budget would have spent the whole story on pages nobody may read."""
    urls = ["https://www.reuters.com/a1", "https://www.reuters.com/a2",
            "https://www.bloomberg.com/a3",
            "https://kyivindependent.com/b1", "https://kyivindependent.com/b2",
            "https://kyivindependent.com/b3"]
    spy = FetchSpy()
    _, ledger, _ = deepen(results(*urls), tier="full", held=0,
                          sources=HIS_RESTRICTED, fetch=spy)
    assert len(spy.article_calls) == 3
    assert sum(1 for r in ledger if r["outcome"] == analysis.TIER_EXCLUDED) == 3
    assert sum(1 for r in ledger if r["outcome"] == analysis.OK) == 3
    assert not any(r["outcome"] == analysis.DEEPEN_SKIP_BUDGET for r in ledger)


def test_a_url_the_cluster_already_holds_is_never_deepened(armed):
    """`build_source_map` mints no R key for a URL the cluster owns (the cluster
    key wins), so fetching it would buy a page nothing can cite."""
    url = "https://thehill.com/policy/a"
    spy = FetchSpy()
    _, ledger, _ = deepen(results(url), tier="full", held=0, fetch=spy,
                          cluster_urls={url})
    assert spy.calls == []
    assert ledger[0]["outcome"] == analysis.DEEPEN_SKIP_HELD


def test_robots_is_honoured_on_the_deepen_lane_and_the_reason_is_recorded(armed):
    url = "https://www.npr.org/2026/08/24/g-s1-139736/up-first"
    spy = FetchSpy(robots_403=["npr.org"])
    _, ledger, _ = deepen(results(url), tier="full", held=0, fetch=spy)
    assert spy.article_calls == []          # robots asked, article never opened
    row, = ledger
    assert row["outcome"] == analysis.ROBOTS_DENIED
    assert "HTTP 403" in row["detail"]


def test_the_shared_robots_cache_asks_each_host_once_per_story(tmp_paths, armed):
    """NPR's robots.txt is a measured 15.17s tarpit. The cluster pass and the
    DEEPEN pass must not each pay it."""
    from newslens import db
    db.migrate()
    con = db.connect()
    try:
        seed_min(con, n_items=1)
        spy = FetchSpy()

        def sonar(key, title, claims):
            return results("https://thehill.com/deep/one"), 0.0, "ok — 1 result"

        analysis.analyze_story(
            con, DATE, 1, _slot(con), **story_kwargs(
                tier="full", fetch=spy, sonar=sonar, chat=_chat_ok))
    finally:
        con.close()
    robots_calls = [u for u in spy.calls if u.endswith("/robots.txt")]
    assert robots_calls.count("https://thehill.com/robots.txt") == 1


# ===========================================================================
# D. SUBSTITUTION — one field moves, and the money guard does not
# ===========================================================================

def test_only_the_text_moves_on_a_deepened_result(armed):
    url = "https://kyivindependent.com/a"
    before = results(url)[0]
    out, _, _ = deepen(results(url), tier="full", held=0)
    after = out[0]
    for field in ("url", "title", "date"):
        assert after[field] == before[field], field
    assert after["snippet"] != before["snippet"]


def test_the_deepened_r_key_keeps_kind_title_and_outlet(armed):
    """`kind` is a reader-facing label AND an exact-match discriminator at
    server.py:4693/5278; the R title carries NL-139's vendor clamp and the R
    outlet sits inside NL-142's label budget. None may move."""
    url = "https://kyivindependent.com/a"
    out, _, _ = deepen(results(url), tier="full", held=0)
    sources = analysis.build_source_map([], [], out, [])
    r = sources["R1"]
    assert r["kind"] == "retrieved"
    assert r["title"] == f"Title for {url}"
    assert r["outlet"] == "kyivindependent.com"
    assert r["deepened"] is True
    assert DEEP_TEXT_MARK in r["text"]


def test_an_undeepened_r_key_is_marked_false_and_persists_no_flag():
    sources = analysis.build_source_map([], [], results("https://x.test/a"), [])
    assert sources["R1"]["deepened"] is False
    assert "deepened" not in analysis._persisted_source_row("R1", sources["R1"])


def test_the_persisted_source_row_carries_the_flag_only_when_deepened(armed):
    out, _, _ = deepen(results("https://kyivindependent.com/a"),
                       tier="full", held=0)
    sources = analysis.build_source_map([], [], out, [])
    assert analysis._persisted_source_row("R1", sources["R1"])["deepened"] is True


def test_deepen_cannot_move_the_prompt_bound(armed):
    """The bound is a MONEY guard (`brief_bound_usd` prices it, rung 1 spends
    against it) and the scout measured 105 chars of slack. DEEPEN touches one
    field that reaches the prompt only through a water-filled budget."""
    from newslens import paths
    template = (paths.PROMPTS_DIR / "analysis_brief.txt").read_text(encoding="utf-8")
    before = analysis.brief_bound_chars(template)
    out, _, _ = deepen(results("https://kyivindependent.com/a"),
                       tier="full", held=0)
    assert analysis.brief_bound_chars(template) == before

    huge = out[0] | {"snippet": "x" * (analysis.MATERIAL_BUDGET_CHARS * 4)}
    sources = analysis.build_source_map([], [], [huge], [])
    assert len(analysis.render_material(sources)) <= analysis.MATERIAL_BUDGET_CHARS


def test_a_deepened_page_is_clamped_to_the_snippet_bound_it_replaced(armed):
    """What we HOLD and PERSIST keeps the bound the vendor snippet had."""
    long_page = (b"<html><body><article>"
                 + b"<p>The delegation reached the port before dawn.</p>"
                   * 3000 + b"</article></body></html>")

    def fetch(url, timeout, cap=0, user_agent=""):
        if url.endswith("/robots.txt"):
            raise urllib.error.HTTPError(url, 404, "nf", {}, None)
        return long_page

    out, ledger, _ = deepen(results("https://kyivindependent.com/a"),
                            tier="full", held=0, fetch=fetch)
    assert len(out[0]["snippet"]) == analysis.SONAR_SNIPPET_MAX_CHARS
    assert ledger[0]["chars"] == analysis.SONAR_SNIPPET_MAX_CHARS


def test_the_result_count_is_invariant_so_the_slot_three_rule_is_untouched(armed):
    """`analyze_story`'s slot-3 demotion reads `len(sonar_results) < 2`."""
    for n in range(0, 5):
        given = results(*[f"https://kyivindependent.com/a{i}" for i in range(n)])
        out, _, _ = deepen(given, tier="full", held=0)
        assert len(out) == len(given) == n


def test_a_deepened_key_stays_an_R_key_and_earns_no_cluster_corroboration(armed):
    """It is retrieved material, not his cluster's reporting — a deepened page
    must never be laundered into `cluster-corroborated`."""
    out, _, _ = deepen(results("https://kyivindependent.com/a",
                               "https://english.elpais.com/b"),
                       tier="full", held=0)
    sources = analysis.build_source_map([], [], out, [])
    assert sorted(sources) == ["R1", "R2"]
    assert analysis.compute_provenance(["R1", "R2"], sources) == \
        "retrieved-single (kyivindependent.com)"


# ===========================================================================
# E. COUNTER ISOLATION — DEEPEN may not move a ruled decision
# ===========================================================================

def _slot(con, date=DATE):
    row = con.execute("SELECT story_slots FROM briefings WHERE date=?",
                      (date,)).fetchone()
    return json.loads(row["story_slots"])[0]


def _chat_ok(key, prompt):
    return (json.dumps({
        "pinned_facts": [], "ledger": [], "mechanism": "", "effects": [],
        "arc": "", "unknowns": [], "watch": [], "notes_for_writer": "",
    }), 0.001)


def test_deepen_never_moves_fetch_ok_or_fetch_attempted(tmp_paths, armed):
    """Gates A and B, the clause-4 systemic-failure verdict and the slot-3
    demotion all read these two counters. A DEEPEN success raising `fetch_ok`
    would silently move a reader-visible tier decision."""
    from newslens import db
    db.migrate()
    con = db.connect()
    try:
        seed_min(con, n_items=1)
        spy = FetchSpy()          # one thin cluster page + one deepenable URL

        def sonar(key, title, claims):
            return (results("https://kyivindependent.com/deep"), 0.0,
                    "ok — 1 result")

        sa = analysis.analyze_story(
            con, DATE, 2, _slot(con), **story_kwargs(
                tier="full", fetch=spy, sonar=sonar, chat=_chat_ok))
    finally:
        con.close()
    assert [r["outcome"] for r in sa.deepen_ledger] == [analysis.OK]
    # one cluster socket, one deepen socket — and the counters see only the first
    assert len(spy.article_calls) == 2
    assert sa.fetch_attempted == 1
    assert sa.fetch_ok == 1


def test_a_totally_failed_cluster_fetch_never_reaches_deepen(tmp_paths, armed):
    """A COMPOSITION FACT, pinned so nobody re-derives it from hope: at the
    armed FETCH_SKIP_ARM default, NL-151 Gate A returns above Sonar rung 1, so a
    story whose cluster fetches all failed is skipped out of the depth tier
    before DEEPEN exists. DEEPEN is a lane for THIN stories, never a rescue for
    dead ones — and no Sonar money is spent on the way there either."""
    from newslens import db
    assert analysis.depth_skip_armed()
    db.migrate()
    con = db.connect()
    try:
        seed_min(con, n_items=1)
        spy = FetchSpy(dead=["https://thehill.com/x0"])

        def sonar_sentinel(key, title, claims):
            raise AssertionError("Sonar ran on a Gate-A story")

        sa = analysis.analyze_story(
            con, DATE, 2, _slot(con), **story_kwargs(
                tier="full", fetch=spy, sonar=sonar_sentinel, chat=_chat_ok))
    finally:
        con.close()
    assert sa.outcome == analysis.FETCH_SKIP_OUTCOME
    assert sa.deepen_ledger == []
    assert spy.article_calls == ["https://thehill.com/x0"]


# ===========================================================================
# F. THE RECEIPTS — the charter rider
# ===========================================================================

def test_every_considered_url_gets_one_ledger_row_carrying_its_reason(armed):
    urls = ["https://www.reuters.com/a",                       # tier
            "https://www.aljazeera.com/video/2026/8/1/b",       # shape
            "https://kyivindependent.com/c",                    # ok
            "https://kyivindependent.com/d",                    # ok
            "https://kyivindependent.com/e",                    # ok
            "https://kyivindependent.com/f"]                    # budget
    _, ledger, _ = deepen(results(*urls), tier="full", held=0,
                          sources=HIS_RESTRICTED)
    assert [r["url"] for r in ledger] == urls
    assert [r["outcome"] for r in ledger] == [
        analysis.TIER_EXCLUDED, analysis.DEEPEN_SKIP_SHAPE,
        analysis.OK, analysis.OK, analysis.OK, analysis.DEEPEN_SKIP_BUDGET]
    assert all(r["detail"] for r in ledger)
    stats = analysis.deepen_stats(ledger)
    assert stats == {"considered": 6, "attempted": 3, "ok": 3, "excluded": 1,
                     "chars": stats["chars"]}
    assert stats["chars"] > 3 * analysis.MIN_EXTRACT_CHARS


def test_the_ledger_lands_on_the_brief_header_and_the_run_report(tmp_paths, armed):
    from newslens import db
    db.migrate()
    con = db.connect()
    try:
        seed_min(con, n_items=1)

        def sonar(key, title, claims):
            return (results("https://kyivindependent.com/deep",
                            "https://www.reuters.com/nope"), 0.0,
                    "ok — 2 results")

        sa = analysis.analyze_story(
            con, DATE, 1, _slot(con), **story_kwargs(
                tier="full", fetch=FetchSpy(), sonar=sonar, chat=_chat_ok,
                cfg=cfg_of(HIS_RESTRICTED)))
        doc = json.loads(con.execute(
            "SELECT brief_json FROM analysis_briefs ORDER BY id DESC LIMIT 1"
        ).fetchone()["brief_json"])
    finally:
        con.close()
    deep = doc["header"]["deepen"]
    assert deep["stats"]["ok"] == 1
    assert deep["stats"]["excluded"] == 1
    assert [u["url"] for u in deep["urls"]] == [
        "https://kyivindependent.com/deep", "https://www.reuters.com/nope"]
    assert "principal ruling 2026-07-06" in deep["urls"][1]["detail"]
    # the aggregate keeps the meaning every historical row carries
    assert doc["header"]["fetch"] == {"ok": sa.fetch_ok,
                                      "attempted": sa.fetch_attempted}


def test_the_run_report_carries_a_deepen_row_per_story(tmp_paths, armed):
    """The surface he reads after a generate (`generation_log.jsonl`, and the
    `newslens analyze` line built from it). `null`, not a zeroed dict, on a
    story the lane never fired on."""
    from newslens import db, paths
    # run_analysis loads sources.yaml itself; the sandbox starts in the
    # zero-source template state, so the tier law is given something to read.
    paths.SOURCES_FILE.write_text(
        "sources:\n"
        "  - name: The Hill\n"
        "    rss_url: https://thehill.com/feed/\n"
        "  - name: Reuters\n"
        "    tier: reference_only\n", encoding="utf-8")
    db.migrate()
    con = db.connect()
    try:
        seed_min(con, n_items=1)

        def sonar(key, title, claims):
            return (results("https://kyivindependent.com/deep",
                            "https://www.reuters.com/nope"), 0.0,
                    "ok — 2 results")

        rep = analysis.run_analysis(
            date=DATE, con=con, env=dict(ENV_OK), chat=_chat_ok, sonar=sonar,
            fetch=FetchSpy(), sleep=lambda s: None, tiers_override=["full"])
    finally:
        con.close()
    story, = rep["per_story"]
    assert story["deepen"] == {"considered": 2, "attempted": 1, "ok": 1,
                               "excluded": 1, "chars": story["deepen"]["chars"]}
    assert story["deepen"]["chars"] > analysis.MIN_EXTRACT_CHARS
    # the aggregate fetch counters are untouched by the lane
    assert (story["fetch_ok"], story["fetch_attempted"]) == (1, 1)


def test_a_story_deepen_did_not_fire_on_carries_no_deepen_key(tmp_paths, armed):
    """'never asked' is a different fact from 'asked and got nothing'."""
    from newslens import db
    db.migrate()
    con = db.connect()
    try:
        seed_min(con, n_items=1)

        def sonar(key, title, claims):
            return results("https://kyivindependent.com/deep"), 0.0, "ok — 1"

        # held is huge -> deficit 0 -> the lane never fires
        analysis.analyze_story(
            con, DATE, 1, _slot(con), **story_kwargs(
                tier="full", fetch=_saturating_fetch, sonar=sonar,
                chat=_chat_ok))
        doc = json.loads(con.execute(
            "SELECT brief_json FROM analysis_briefs ORDER BY id DESC LIMIT 1"
        ).fetchone()["brief_json"])
    finally:
        con.close()
    assert "deepen" not in doc["header"]


_SATURATING = (b"<html><body><article>"
               + b"<p>The delegation reached the port before dawn.</p>" * 700
               + b"</article></body></html>")


def _saturating_fetch(url, timeout, cap=0, user_agent=""):
    if url.endswith("/robots.txt"):
        raise urllib.error.HTTPError(url, 404, "nf", {}, None)
    return _SATURATING


# ===========================================================================
# G. THE ARM — off is zero-delta and constructible
# ===========================================================================

def test_the_off_arm_opens_no_socket_and_changes_no_byte(monkeypatch):
    monkeypatch.setattr(analysis, "DEEPEN_ARM", analysis.DEEPEN_ARM_OFF)
    assert analysis.deepen_armed() is False
    spy = FetchSpy()
    given = results("https://kyivindependent.com/a")
    out, ledger, warns = deepen(given, tier="full", held=0, fetch=spy)
    assert spy.calls == []
    assert (ledger, warns) == ([], [])
    assert out == given


def test_the_lane_ships_inert():
    """THE SHIPPED DEFAULT, pinned as a fact about the committed bytes (gate
    R-1, 2026-08-24). This test takes no `armed` fixture — that is the point:
    it reads the module constant as his tree carries it. NL-151 shipped OFF and
    was armed later by his explicit word; DEEPEN does the same, so nothing in
    this lane opens a socket on his next generate until he says so.

    Arming is one line — `DEEPEN_ARM = DEEPEN_ARM_ON` at analysis.py:3218 —
    plus one suite leg. If that line moves, this pin is what says so out loud."""
    assert analysis.DEEPEN_ARM == analysis.DEEPEN_ARM_OFF
    assert analysis.deepen_armed() is False


def test_the_shipped_default_opens_no_socket_without_any_monkeypatch():
    """§G's other half, and the one that matters for HIS morning: with NOTHING
    patched — the module exactly as committed — a story that would otherwise
    fire the lane produces no ledger, no warning and no socket."""
    spy = FetchSpy()
    given = results("https://kyivindependent.com/a")
    out, ledger, warns = deepen(given, tier="full", held=0, fetch=spy)
    assert spy.calls == []
    assert (ledger, warns) == ([], [])
    assert out == given


# ===========================================================================
# H. THE READER SURFACE — what he can see
# ===========================================================================

def test_the_analyze_verb_prints_the_deepen_row_only_where_the_lane_fired(
        tmp_paths, capsys, monkeypatch):
    """The line he reads on the terminal after `newslens analyze`."""
    from newslens import cli
    story = {"slot": 1, "tier": "full", "outcome": "ok", "detail": "d",
             "cost_usd": 0.03, "fetch_ok": 3, "fetch_attempted": 4,
             "sonar": "ok — 8 results"}
    canned = {"date": DATE, "model": "m", "total_usd": 0.04, "status": "ok",
              "derating": False, "warnings": [], "per_story": [
                  dict(story, deepen={"considered": 5, "attempted": 3, "ok": 2,
                                      "excluded": 2, "chars": 7400}),
                  dict(story, slot=2, deepen=None)]}
    monkeypatch.setattr(analysis, "run_analysis", lambda date=None: canned)
    assert cli.main(["analyze"]) == 0
    lines = [l for l in capsys.readouterr().out.splitlines()
             if "deepen:" in l]
    assert lines == ["    deepen: 2/3 fetched of 5 Sonar URLs · 7400 chars · "
                     "2 tier-excluded"]


def test_the_diagnose_readout_carries_the_lane_only_once_it_has_fired(tmp_paths):
    """The longitudinal surface (`newslens diagnose`). A corpus of pre-NL-127
    entries renders exactly as it did — the line appears only when a story
    actually carries a deepen row."""
    from datetime import datetime, timezone
    from newslens import diagnose, paths

    def _entry(per_story):
        return {"stage": "analysis", "date": DATE, "model": "m",
                "total_usd": 0.04, "derating": False, "warnings": [],
                "per_story": per_story}

    old = _entry([{"slot": 1, "tier": "full", "outcome": "ok", "detail": "",
                   "cost_usd": 0.03, "fetch_ok": 3, "fetch_attempted": 4,
                   "sonar": "ok — 8 results"}])
    log = paths.DATA_DIR / "generation_log.jsonl"
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_text(json.dumps(old) + "\n", encoding="utf-8")
    now = datetime(2026, 7, 6, 12, tzinfo=timezone.utc)
    assert "deepen (R# lane)" not in diagnose.run_diagnose(now_utc=now)

    new = _entry([dict(old["per_story"][0],
                       deepen={"considered": 5, "attempted": 3, "ok": 2,
                               "excluded": 2, "chars": 7400})])
    log.write_text(json.dumps(new) + "\n", encoding="utf-8")
    out = diagnose.run_diagnose(now_utc=now)
    assert "deepen (R# lane): fired on 1/1 stories" in out
    assert "2/3 Sonar URLs yielded prose (67%)" in out
    assert "7400 chars bought at $0" in out
    assert "2 tier-excluded" in out


_SUFFIX = " — full text"


@pytest.mark.parametrize("row,expected,suffixed", [
    ({"kind": "retrieved", "deepened": True},
     "retrieved, via Sonar" + _SUFFIX, True),
    ({"kind": "retrieved"}, "retrieved, via Sonar", False),
    ({"kind": "cluster-excerpt"}, "cluster excerpt", False),
])
def test_the_deep_view_names_a_deepened_source_row(row, expected, suffixed):
    """The suffix must be ABSENT on an undeepened row, not merely present on a
    deepened one — a substring assertion alone is satisfied by an unconditional
    suffix, which is the 'pin a comment can satisfy' class."""
    brief = {"sources": [dict(row, key="R1", outlet="kyivindependent.com",
                              title="T", url="https://kyivindependent.com/a",
                              retrieved_at="2026-08-24T09:00Z")]}
    html = server._render_deep_view("story-0", "H",
                                    {"header": {}, "brief": brief}, DATE)
    assert expected in html
    assert (_SUFFIX in html) is suffixed
