"""M9-M1 QA additions — adversarial extensions over the implementer's fetcher
battery (tests/test_analysis_fetcher.py). Zero network: the two REAL fixtures
here were captured live by the QA pass (2026-07-06, robots-verified first,
one fetch per page, ANALYSIS_UA, timestamps in the fixture headers); the
suite reads the saved artifacts only.
"""

from pathlib import Path

import pytest
import urllib.error

from newslens import analysis

FIXTURES = Path(__file__).parent / "fixtures" / "analysis"


def fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def robots_404(url, timeout, cap=0, user_agent=""):
    if url.endswith("/robots.txt"):
        raise urllib.error.HTTPError(url, 404, "nf", {}, None)
    raise AssertionError(f"unexpected fetch: {url}")


# --- more real DOM shapes (the fallback-weight question) --------------------------------

@pytest.mark.parametrize(
    "name, must_contain, title_frag",
    [
        ("bbc_real.html", "Kyiv", "Kyiv"),
        ("guardian_real.html", "Byzantine", "Byzantine"),
    ],
)
def test_more_real_pages_extract_ok(name, must_contain, title_frag):
    """QA-captured corpus extension: two more full-tier outlets with
    different DOM shapes. Evidence note: BOTH won via the <article> scope,
    making article-tag 3-of-5 across the live corpus — the paragraph-density
    fallback still carried 2 of the implementer's probes and stays
    load-bearing."""
    res = analysis.extract_article_text(fixture(name))
    outcome, detail = analysis.classify_extraction(res)
    assert outcome == analysis.OK, detail
    assert res.method == "article-tag"
    assert res.chars >= analysis.MIN_EXTRACT_CHARS
    assert must_contain in res.text
    assert title_frag in res.title


# --- the tier gate through the DEFAULT wiring ---------------------------------------------

def test_excluded_tiers_through_default_fetch_open_no_socket(no_network):
    """Defense in depth past the implementer's injected-fake pins: run the
    LOOP with the real default fetch (net.fetch_bytes) and only excluded-tier
    items — the socket recorder proves nothing was even attempted."""
    items = [
        {"url": "https://bloomberg.com/x", "source_name": "B",
         "tier": "headline_only"},
        {"url": "https://nytimes.com/y", "source_name": "N",
         "tier": "reference_only"},
    ]
    sleeps = []
    records = analysis.fetch_cluster_articles(items, sleep=sleeps.append)
    assert [r.outcome for r in records] == [analysis.TIER_EXCLUDED] * 2
    assert all(r.attempted is False for r in records)
    assert sleeps == []          # exclusions never pay the politeness delay
    assert no_network == []      # and never touch a socket, by default wiring


# --- pacing: delay discipline edges ----------------------------------------------------------

def _page_fetch(pages):
    def fetch(url, timeout, cap=0, user_agent=""):
        if url.endswith("/robots.txt"):
            raise urllib.error.HTTPError(url, 404, "nf", {}, None)
        return pages[url].encode()
    return fetch


def test_no_leading_delay_and_n_minus_one_sleeps():
    good = fixture("clean_article.html")
    pages = {f"https://s{i}.com/a": good for i in range(3)}
    sleeps = []
    items = (
        [{"url": "https://wapo.com/pre", "source_name": "W", "tier": "headline_only"}]
        + [{"url": u, "source_name": "S", "tier": "full"} for u in sorted(pages)]
    )
    analysis.fetch_cluster_articles(items, fetch=_page_fetch(pages),
                                    sleep=sleeps.append)
    # A leading tier-exclusion never triggers a delay; three network fetches
    # cost exactly two pauses.
    assert sleeps == [analysis.POLITE_DELAY_S] * 2


def test_cached_robots_denial_currently_pays_a_delay():
    """PINS ACTUAL BEHAVIOR + flags the docstring: fetch_cluster_articles
    says cached robots denials 'cost no delay', but the delay decision is
    tier-only and fires BEFORE the cache is consulted — a second same-host
    item pays the pause and is then denied from cache with no network. The
    deviation errs OVER-polite (never under), so it is a documentation
    mismatch, not a boundary breach — flagged for the implementer to align
    either the sleep gate or the docstring."""
    calls = []

    def fetch(url, timeout, cap=0, user_agent=""):
        calls.append(url)
        if url.endswith("/robots.txt"):
            return b"User-agent: *\nDisallow: /\n"
        raise AssertionError("article fetch past a robots deny")

    sleeps = []
    items = [
        {"url": "https://ex.com/one", "source_name": "E", "tier": "full"},
        {"url": "https://ex.com/two", "source_name": "E", "tier": "full"},
    ]
    records = analysis.fetch_cluster_articles(items, fetch=fetch,
                                              sleep=sleeps.append)
    assert [r.outcome for r in records] == [analysis.ROBOTS_DENIED] * 2
    assert calls == ["https://ex.com/robots.txt"]  # one robots fetch, cached
    assert sleeps == [analysis.POLITE_DELAY_S]     # the documented-free pause


# --- robots edges -----------------------------------------------------------------------------

def test_robots_rules_are_path_scoped_not_host_blanket():
    def fetch(url, timeout, cap=0, user_agent=""):
        return b"User-agent: *\nDisallow: /premium/\n"
    cache = analysis.RobotsCache(fetch=fetch)
    ok, _ = cache.allows("https://ex.com/free/story")
    denied, why = cache.allows("https://ex.com/premium/story")
    assert ok is True and denied is False
    assert "disallows this path" in why


def test_unparseable_robots_is_treated_as_no_rules():
    """PINS THE STANCE: a robots.txt that is SERVED but unparseable binary is
    'no rules stated' -> allow (parse errors are ignored per convention);
    only unreachable/denied robots deny conservatively."""
    def fetch(url, timeout, cap=0, user_agent=""):
        return b"\x00\xff\xfe binary sludge \x00"
    cache = analysis.RobotsCache(fetch=fetch)
    ok, why = cache.allows("https://ex.com/story")
    assert ok is True


def test_non_http_urls_are_denied_at_the_robots_gate():
    cache = analysis.RobotsCache(fetch=robots_404)
    for bad in ("ftp://ex.com/a", "file:///etc/passwd", "not-a-url"):
        ok, why = cache.allows(bad)
        assert ok is False and "http(s)" in why


# --- fetch plumb: the net.py discipline actually travels --------------------------------------

def test_fetch_kwargs_carry_cap_timeout_and_analyst_ua():
    seen = {}

    def fetch(url, timeout, cap=0, user_agent=""):
        if url.endswith("/robots.txt"):
            seen["robots"] = {"timeout": timeout, "cap": cap, "ua": user_agent}
            raise urllib.error.HTTPError(url, 404, "nf", {}, None)
        seen["article"] = {"timeout": timeout, "cap": cap, "ua": user_agent}
        return fixture("clean_article.html").encode()

    robots = analysis.RobotsCache(fetch=fetch)
    rec = analysis.fetch_article("https://ex.com/a", "E", "full", robots,
                                 fetch=fetch)
    assert rec.outcome == analysis.OK
    assert seen["article"] == {"timeout": analysis.FETCH_TIMEOUT_S,
                               "cap": analysis.MAX_ARTICLE_BYTES,
                               "ua": analysis.ANALYSIS_UA}
    assert seen["robots"]["cap"] == 512_000
    assert seen["robots"]["ua"] == analysis.ANALYSIS_UA


# --- paywall classification: both directions at the boundary -----------------------------------

def _page_with(prose_chars: int, marker: str) -> str:
    body = "<p>" + ("word " * (prose_chars // 5)) + "</p>"
    return f"<html><body><article>{body}<p>{marker}</p></article></body></html>"


def test_marker_with_thin_text_is_paywall_suspected():
    res = analysis.extract_article_text(
        _page_with(800, "Subscribe to continue reading this story."))
    outcome, detail = analysis.classify_extraction(res)
    assert outcome == analysis.PAYWALL_SUSPECTED
    assert "subscribe to continue" in detail


def test_marker_quoted_in_long_prose_is_not_a_false_positive():
    """The dispatch's named case: a real story QUOTING a wall phrase in
    ample prose must classify OK — the near-chars gate protects it."""
    res = analysis.extract_article_text(_page_with(
        analysis.PAYWALL_NEAR_CHARS + 400,
        'The outlet now greets readers with "subscribe to continue" banners.'))
    outcome, detail = analysis.classify_extraction(res)
    assert outcome == analysis.OK, detail


def test_extraction_floor_boundary_is_honest():
    at_floor = analysis.extract_article_text(_page_with(analysis.MIN_EXTRACT_CHARS + 60, "x"))
    assert classify_ok(at_floor)
    below = analysis.extract_article_text(
        "<html><body><article><p>short.</p></article></body></html>")
    outcome, _ = analysis.classify_extraction(below)
    assert outcome == analysis.EMPTY


def classify_ok(res):
    outcome, _ = analysis.classify_extraction(res)
    return outcome == analysis.OK


# --- _decode charset edges ----------------------------------------------------------------------

def test_decode_honors_declared_charset():
    html = '<meta charset="latin-1"><p>caf\xe9</p>'.encode("latin-1")
    assert "café" in analysis._decode(html)


def test_decode_bogus_charset_falls_back_to_utf8_replace():
    html = '<meta charset="klingon-8"><p>caf\xc3\xa9</p>'.encode("latin-1")
    text = analysis._decode(html)
    assert "café" in text  # utf-8 bytes decoded on the fallback path


def test_decode_quoted_uppercase_charset_and_late_meta():
    html = ("<meta charset='ISO-8859-1'>" + "x" * 10 + "caf\xe9").encode("latin-1")
    assert "café" in analysis._decode(html)
    # A meta beyond the 2KB sniff window: utf-8 replacement, never a crash.
    late = (" " * 3000 + '<meta charset="latin-1">').encode("ascii") + b"\xe9"
    out = analysis._decode(late)
    assert "�" in out  # replacement char, fetch survives


# --- attribution completeness ---------------------------------------------------------------------

def test_every_unique_item_lands_in_the_manifest():
    good = fixture("clean_article.html")

    def fetch(url, timeout, cap=0, user_agent=""):
        if url.endswith("/robots.txt"):
            if "denyhost" in url:
                return b"User-agent: *\nDisallow: /\n"
            raise urllib.error.HTTPError(url, 404, "nf", {}, None)
        if "boom" in url:
            raise urllib.error.HTTPError(url, 500, "err", {}, None)
        return good.encode()

    items = [
        {"url": "https://ok.com/a", "source_name": "OK", "tier": "full"},
        {"url": "https://denyhost.com/b", "source_name": "D", "tier": "full"},
        {"url": "https://boom.com/c", "source_name": "B", "tier": "full"},
        {"url": "https://ref.com/d", "source_name": "R", "tier": "reference_only"},
        {"url": "ftp://weird/e", "source_name": "F", "tier": "full"},
    ]
    records = analysis.fetch_cluster_articles(items, fetch=fetch,
                                              sleep=lambda s: None)
    assert [r.url for r in records] == [i["url"] for i in items]  # nothing off the record
    assert [r.outcome for r in records] == [
        analysis.OK, analysis.ROBOTS_DENIED, analysis.ERROR,
        analysis.TIER_EXCLUDED, analysis.ERROR,
    ]
    stats = analysis.fetch_stats(records)
    assert stats["records"] == 5
    assert stats["attempted"] == 3       # ok + robots-denied + boom
    assert stats["success_rate"] == pytest.approx(0.333)
    assert stats["by_outcome"][analysis.ERROR] == 2


def test_fetch_stats_none_rate_when_nothing_attempted():
    recs = [analysis.FetchRecord(url="u", source_name="R", tier="reference_only",
                                 outcome=analysis.TIER_EXCLUDED, attempted=False)]
    assert analysis.fetch_stats(recs)["success_rate"] is None
# --- Crawl-delay clamp (M9-M1 gate ruling; QA recommendation adopted) --------------------------

def _robots_with(text):
    def fetch(url, timeout, cap=0, user_agent=""):
        return text.encode()
    return fetch


@pytest.mark.parametrize(
    "robots_text, expected",
    [
        ("User-agent: *\nCrawl-delay: 5\nAllow: /\n", 5.0),      # stated, in range
        ("User-agent: *\nCrawl-delay: 86400\nAllow: /\n", 10.0), # hostile: ceiling
        ("User-agent: *\nCrawl-delay: 0.1\nAllow: /\n", 1.0),    # sub-floor: floor
        ("User-agent: *\nAllow: /\n", 1.0),                        # unset: floor
    ],
)
def test_delay_for_clamps_stated_crawl_delays(robots_text, expected):
    cache = analysis.RobotsCache(fetch=_robots_with(robots_text))
    cache.allows("https://ex.com/a")  # loads + caches the host's robots
    assert cache.delay_for("https://ex.com/a") == expected


def test_delay_for_floor_on_absent_robots_and_unconsulted_hosts():
    cache = analysis.RobotsCache(fetch=robots_404)
    cache.allows("https://ex.com/a")  # 404 -> parser None -> convention allow
    assert cache.delay_for("https://ex.com/a") == analysis.POLITE_DELAY_S
    # A host never consulted returns the floor (stated delays bind from the
    # second same-host attempt — the gate's stated subtlety).
    assert cache.delay_for("https://never-seen.com/x") == analysis.POLITE_DELAY_S


def test_delay_for_floor_when_the_parser_misbehaves():
    cache = analysis.RobotsCache(fetch=robots_404)

    class RudeParser:
        def crawl_delay(self, ua):
            raise RuntimeError("robotparser had a bad day")

    cache._parsers["ex.com"] = RudeParser()
    assert cache.delay_for("https://ex.com/a") == analysis.POLITE_DELAY_S


def test_loop_sleeps_the_stated_delay_proving_the_callable_is_consulted():
    """The gate's regression tripwire: floor == POLITE_DELAY_S, so only a
    STATED delay distinguishes `sleep(robots.delay_for(url))` from a silent
    regression to the constant. Host A states 4s; host B states none. The
    sleep sequence must be [4.0 (A, second same-host attempt — the stated
    delay now bound), 1.0 (B, unconsulted -> floor)] — a constant would
    produce [1.0, 1.0] and fail here."""
    good = fixture("clean_article.html")

    def fetch(url, timeout, cap=0, user_agent=""):
        if url.endswith("/robots.txt"):
            if "slowhost" in url:
                return b"User-agent: *\nCrawl-delay: 4\nAllow: /\n"
            raise urllib.error.HTTPError(url, 404, "nf", {}, None)
        return good.encode()

    sleeps = []
    items = [
        {"url": "https://slowhost.com/one", "source_name": "S", "tier": "full"},
        {"url": "https://slowhost.com/two", "source_name": "S", "tier": "full"},
        {"url": "https://fast.com/three", "source_name": "F", "tier": "full"},
    ]
    records = analysis.fetch_cluster_articles(items, fetch=fetch,
                                              sleep=sleeps.append)
    assert [r.outcome for r in records] == [analysis.OK] * 3
    assert sleeps == [4.0, analysis.POLITE_DELAY_S]
    assert analysis.CRAWL_DELAY_CEILING_S == 10.0  # the adopted ceiling, pinned
