"""The discovery-pause batch: the pause itself, NL-101's usability gate, and
the retro-clean. Acceptance tests for the 2026-07-25 SONAR ruling.

Three contracts, one file because they are one decision:

  1. THE PAUSE (DECISIONS.md "[2026-07-25] SONAR RULED: discovery PAUSED").
     No metered discovery call runs on the default path, ever, by default —
     key present or absent. An explicit opt-in exists for NL-102's testing
     phase and nothing else.

  2. NL-101 — the usability gate. A retrieval set the ANSWER ITSELF disclaims
     must not enter source_items, and neither may the structural non-article
     classes the parity probe recorded. Correctness holds while paused: this
     is the unpause path, and it must be safe on the day it is taken.

  3. THE RETRO-CLEAN. A dry-run-default tool that removes the historical junk
     rows, never touching a row a shipped briefing cites.

SPECIMEN PROVENANCE. Every disclaimer string and every junk URL below is
copied from workspace/debates/2026-07-25--newslens--engineering-5.md (§3.2 the
URL slice, §3.4a the verbatim I3 answer, §9(b)/(c) the social-post class and
the arm-A/arm-C disclaimers). Nothing here is invented; that is the point —
these are the shapes that were live in the principal's database.
"""

from __future__ import annotations

import json

import pytest

from newslens import config, discovery, ingest

NOW = "2026-07-26T09:00:00.000Z"
OPT_IN = {config.DISCOVERY_OPT_IN_ENV: "1"}
KEY_ENV = dict(OPT_IN, PERPLEXITY_API_KEY="pplx-qa-fake-key")

# --- the recorded specimens --------------------------------------------------

# engineering-5.md §3.4a — Sonar's own answer for interest I3, verbatim.
DISCLAIMER_I3 = (
    "I can't reliably answer this from the provided results alone: the search "
    "set is dominated by Bloomberg items and does not include enough "
    "non-mainstream, hyperlocal NYC transit reporting to identify developments "
    "that are both current and unlikely to be covered by the outlets you "
    "already follow."
)
# engineering-5.md §9(c) — the two shapes the recency-filtered arms produced.
DISCLAIMER_ARM_A = (
    "I don't see enough current, directly sourced local-transit reporting in "
    "these results to name a development you would have missed."
)
DISCLAIMER_ARM_C = (
    "The search results are dominated by broad market and Middle East "
    "coverage, so I cannot identify an under-covered development here."
)
USABLE_ANSWER = (
    "Two developments stand out today: a regional grid operator filed an "
    "emergency interconnection request, and a mid-size chipmaker disclosed a "
    "packaging-capacity deal."
)

# THIRD-PARTY-SUBJECT FALSE POSITIVES (gate MEDIUM-1; QA-found, 8/8 reproduced
# against the first cut of the regex). Every one is a legitimate opening a
# WORKING discovery answer could produce: the sentence's subject is a news
# actor — a safety board, regulators, city officials, an election — not the
# answering model disclaiming its own retrieval. The first cut left the
# first-person anchor optional, so all eight would have dropped a usable set.
THIRD_PARTY_SPECIMENS = [
    "The safety board said it cannot determine the cause of the outage from the wreckage recovered so far.",
    "The commission says it cannot identify the source of the contamination in the delta.",
    "Regulators do not see enough progress on grid interconnection to approve the merger.",
    "Analysts don't see enough demand to justify a second fab this year, three banks wrote.",
    "City officials can't name a timeline for reopening the bridge after the barge strike.",
    "There are not enough shelter beds for displaced residents, county data released today shows.",
    "The audit found the utility does not include enough reserve margin in its summer forecast.",
    "Election results are dominated by turnout in the northern provinces, with two races too close to call.",
]

# The impersonal shapes that ARE disclaimers, because they name WHERE the
# shortfall is. These keep the anchor rule honest in the other direction: the
# fix must not just demand "I" everywhere and call the job done.
IMPERSONAL_DISCLAIMERS = [
    "There are not enough local-transit items in these results to name a development you would have missed.",
    "The search set does not include enough hyperlocal reporting in the returned results.",
]

# engineering-5.md §3.2 — the representative slice, all of which the shipped
# _store_results would have inserted as source_items rows.
JUNK_SPECIMENS = [
    ("https://www.bloomberg.com/", "homepage"),
    ("https://www.bloomberg.com/markets", "section-front"),
    ("https://www.bloomberg.com/podcasts/series/daybreak-americas",
     "listing-or-section-page"),
    ("https://www.bloomberg.com/sitemaps/news/latest.xml",
     "feed-or-sitemap-file"),
    ("https://www.youtube.com/watch?v=YkocwwyF5LI", "audio-video-page"),
    ("https://www.advisorperspectives.com/firm/bloomberg-news",
     "listing-or-section-page"),
    ("https://www.moneycontrol.com/author/bloomberg-21811/",
     "listing-or-section-page"),
    # §9(b): a class the recency filter INTRODUCED — social posts as news.
    ("https://www.facebook.com/some.page/posts/1234567890", "social-post"),
    ("https://www.instagram.com/p/CxYzAbCdEf/", "social-post"),
]

# Real article shapes that must keep flowing (over-filtering is its own bug).
ARTICLE_SPECIMENS = [
    "https://www.reuters.com/business/energy/ercot-files-emergency-request-2026-07-26/",
    "https://apnews.com/article/chipmaker-packaging-capacity-9f3ac21b",
    "https://www.bloomberg.com/news/articles/2026-07-26/oil-jumps-as-hormuz-risk-returns",
]

# FOUND BY THE FIRST DRY RUN AGAINST THE PRINCIPAL'S REAL STORE, not by
# reasoning about it: the naive segment filter classed three Guardian LIVE
# BLOGS as listings because "live" is a listing word — and all three had been
# CITED by shipped briefings. A dated live blog is how an outlet covers a
# breaking story. These are verbatim rows from data/newslens.db (read-only).
LIVE_STORY_SPECIMENS = [
    "https://www.theguardian.com/world/live/2026/jun/09/middle-east-crisis-iran-israel-us-donald-trump-strait-of-hormuz-peace-deal-latest-news-updates",
    "https://www.theguardian.com/world/live/2026/apr/17/middle-east-crisis-live-news-israel-lebanon-ceasefire-iran-war-us-latest-updates",
    "https://www.cnn.com/2026/07/16/world/video/cncpm-iran-strait-of-hormuz-red-line-strikes",
]

# ...and the class that fix REOPENED (gate MEDIUM-2, 12/12). Letting a ≥3-word
# tail slug rescue a listing page was the error: a headline-shaped slug is
# exactly what listing pages carry. Five live `/world/series/` rows in the
# principal's own store walked back through. Only a DATE rescues now, and only
# on the dated-FORMAT segments — never on a pure index.
LISTING_LEAK_SPECIMENS = [
    "https://www.theguardian.com/world/series/middle-east-crisis",
    "https://example.com/tag/israel-iran-conflict",
    "https://example.com/topics/artificial-intelligence-regulation",
    "https://example.com/author/jane-marie-smith",
    "https://example.com/video/best-news-clips",
    "https://example.com/podcasts/the-daily-briefing-show",
    "https://example.com/category/middle-east-security-policy",
    "https://example.com/search/iran-sanctions-oil",
    "https://example.com/archive/top-stories-this-week",
    "https://example.com/section/climate-and-energy",
    "https://example.com/series/inside-the-fed-decision",
    "https://example.com/live/markets-open-coverage-today",
]

# A dated INDEX page is a day's archive — still a page of links. The date
# rescue must not reach it.
DATED_INDEX_SPECIMENS = [
    "https://example.com/archive/2026/07/16/top-stories-of-the-day",
    "https://example.com/tag/2026/07/16/iran-sanctions-coverage",
    "https://example.com/author/2026/07/16/jane-marie-smith-columns",
]


def cfg_with_interests():
    return config.SourcesConfig(
        sources=[config.Source(name="Outlet A", rss_url="https://a.invalid/f.xml")],
        interests_broad=["technology"],
        interests_granular=["AI regulation"],
    )


def sonar_payload(urls, answer=USABLE_ANSWER):
    return {
        "id": "qa",
        "model": "sonar",
        "usage": {"total_tokens": 850},
        "choices": [{"message": {"role": "assistant", "content": answer}}],
        "search_results": [
            {"title": f"Result {i}", "url": url, "date": "2026-07-26"}
            for i, url in enumerate(urls)
        ],
    }


def route(fake_api, monkeypatch, payload):
    monkeypatch.setattr(
        discovery, "PERPLEXITY_CHAT_URL", fake_api.base_url + "/chat/completions"
    )
    fake_api.add_route(
        "/chat/completions",
        status=200,
        body=json.dumps(payload).encode("utf-8"),
        content_type="application/json",
    )


def sonar_urls(con):
    return [r["url"] for r in con.execute(
        "SELECT url FROM source_items WHERE source_type = 'sonar' ORDER BY id"
    )]


# ===========================================================================
# 1. THE PAUSE
# ===========================================================================

def test_discovery_is_paused_by_default_even_with_a_key(migrated_con, no_network):
    """THE ruling, mechanically: a key in the environment is not consent to
    spend. No request is built and no socket is touched."""
    status = discovery.run_discovery(
        migrated_con, cfg_with_interests(),
        env={"PERPLEXITY_API_KEY": "pplx-qa-fake-key"}, now_iso=NOW,
    )
    assert status.startswith("paused — ")
    assert "PAUSED by ruling (2026-07-25)" in status
    assert "RSS-only run" in status
    assert no_network == []
    assert sonar_urls(migrated_con) == []


def test_pause_is_reported_before_the_key_not_after(migrated_con, no_network):
    """Keyless AND paused reports the PAUSE, not the missing key: a decision
    reads differently from a to-do, and the doctor/report copy depends on it."""
    status = discovery.run_discovery(
        migrated_con, cfg_with_interests(), env={}, now_iso=NOW)
    assert status.startswith("paused — ")
    assert "PERPLEXITY_API_KEY not set" not in status
    assert no_network == []


@pytest.mark.parametrize("value", ["", "0", "true", "TRUE", "yes", "on", " ", "2"])
def test_only_the_exact_string_1_unpauses(migrated_con, no_network, value):
    """Fail-cheap, never fail-paid (the NL-96 direction): a typo leaves the
    metered path closed."""
    status = discovery.run_discovery(
        migrated_con, cfg_with_interests(),
        env={config.DISCOVERY_OPT_IN_ENV: value,
             "PERPLEXITY_API_KEY": "pplx-qa-fake-key"},
        now_iso=NOW,
    )
    assert status.startswith("paused — ")
    assert no_network == []


def test_the_opt_in_unpauses_and_the_call_goes_through(
    migrated_con, fake_api, monkeypatch
):
    """The pause is a pause, not a removal — NL-102's testing phase needs the
    path to still work."""
    route(fake_api, monkeypatch, sonar_payload(ARTICLE_SPECIMENS))
    status = discovery.run_discovery(
        migrated_con, cfg_with_interests(), env=KEY_ENV, now_iso=NOW)
    assert status.startswith("ok — 1 Sonar call, 3 discovered item(s) stored")
    assert sonar_urls(migrated_con) == ARTICLE_SPECIMENS


def test_ingest_reports_the_pause_by_default(migrated_con, no_network):
    """The default path the principal actually runs. `--no-discovery` is not
    required to get a $0 ingest any more."""
    cfg = cfg_with_interests()
    report = ingest.run_ingest(
        con=migrated_con, cfg=cfg, env={"PERPLEXITY_API_KEY": "pplx-qa-fake-key"},
        with_discovery=True,
    )
    assert report.discovery_status.startswith("paused — ")
    assert report.discovery_items == 0
    # The RSS feed host is legitimately attempted (and refused by the guard);
    # what must be absent is any contact with the METERED vendor.
    assert not any("perplexity" in str(target).lower()
                   for _, target in no_network), no_network


def test_config_predicate_is_the_single_reader(monkeypatch):
    """One validator, per the config.py house rule — doctor and sonar_spike
    render this, they never re-implement it."""
    assert config.discovery_enabled({}) is False
    assert config.discovery_enabled({config.DISCOVERY_OPT_IN_ENV: "1"}) is True
    # .strip() runs before the comparison, so a whitespace-padded value from a
    # hand-edited .env still unpauses. Documented, not accidental.
    assert config.discovery_enabled({config.DISCOVERY_OPT_IN_ENV: " 1 "}) is True
    monkeypatch.setenv(config.DISCOVERY_OPT_IN_ENV, "1")
    assert config.discovery_enabled() is True          # falls back to os.environ
    monkeypatch.delenv(config.DISCOVERY_OPT_IN_ENV)
    assert config.discovery_enabled() is False


# ===========================================================================
# 2. NL-101 — the usability gate
# ===========================================================================

@pytest.mark.parametrize("answer,label", [
    (DISCLAIMER_I3, "I3 verbatim (engineering-5 §3.4a)"),
    (DISCLAIMER_ARM_A, "arm-A (§9c)"),
    (DISCLAIMER_ARM_C, "arm-C (§9c)"),
])
def test_every_recorded_disclaimer_specimen_is_detected(answer, label):
    assert discovery.answer_disclaims(answer) is not None, label


def test_a_usable_answer_is_not_flagged():
    """CARRIED INVARIANT (born-green): the gate must not eat working runs."""
    assert discovery.answer_disclaims(USABLE_ANSWER) is None
    assert discovery.answer_disclaims("") is None
    assert discovery.answer_disclaims(None) is None


@pytest.mark.parametrize("answer", THIRD_PARTY_SPECIMENS)
def test_a_third_party_subject_is_never_read_as_the_model_disclaiming(answer):
    """gate MEDIUM-1. Only the answering model can disclaim the answering
    model's retrieval. A sentence whose subject is a safety board, a regulator
    or an election is NEWS — flagging it drops a working run's whole set, and
    the drop is silent from the reader's side."""
    assert discovery.answer_disclaims(answer) is None


@pytest.mark.parametrize("answer", IMPERSONAL_DISCLAIMERS)
def test_an_impersonal_disclaimer_still_fires_when_it_names_the_retrieval(answer):
    """The other direction of the anchor rule: requiring first person
    everywhere would have been a cheap fix and a wrong one. 'There are not
    enough X IN THESE RESULTS' is the vendor disclaiming, whoever says it."""
    assert discovery.answer_disclaims(answer) == "not-enough-in-the-retrieval-set"


def test_dominated_by_only_fires_about_the_SEARCH_not_about_the_news():
    """A news answer may legitimately say a market was dominated by something.
    The pattern is anchored to the retrieval subject for exactly that reason."""
    assert discovery.answer_disclaims(
        "Trading was dominated by energy names after the Hormuz headlines."
    ) is None
    assert discovery.answer_disclaims(
        "The search results are dominated by wire copy."
    ) is not None


def test_a_disclaimed_set_stores_nothing_and_says_so(
    migrated_con, fake_api, monkeypatch
):
    """THE NL-101 BUG, in its recorded shape: Sonar's answer disclaims the set
    and the nine junk URLs went into source_items anyway. Now: zero rows, and
    the run report names the signal."""
    urls = [u for u, _ in JUNK_SPECIMENS]
    route(fake_api, monkeypatch, sonar_payload(urls, answer=DISCLAIMER_I3))
    status = discovery.run_discovery(
        migrated_con, cfg_with_interests(), env=KEY_ENV, now_iso=NOW)
    assert status.startswith("degraded — Sonar's own answer disclaims")
    assert "discarded unstored" in status
    assert sonar_urls(migrated_con) == []


def test_a_disclaimed_set_is_dropped_even_when_the_urls_are_clean(
    migrated_con, fake_api, monkeypatch
):
    """The two gates are independent. A well-formed article set is still
    unusable when the vendor says it could not answer from it — that is the
    §3.4a finding, and a URL-shape filter alone would have missed it."""
    route(fake_api, monkeypatch,
          sonar_payload(ARTICLE_SPECIMENS, answer=DISCLAIMER_ARM_A))
    status = discovery.run_discovery(
        migrated_con, cfg_with_interests(), env=KEY_ENV, now_iso=NOW)
    assert status.startswith("degraded — ")
    assert sonar_urls(migrated_con) == []


@pytest.mark.parametrize("url,reason", JUNK_SPECIMENS)
def test_each_recorded_junk_url_is_classified(url, reason):
    assert discovery.url_reject_reason(url) == reason


@pytest.mark.parametrize("url", ARTICLE_SPECIMENS)
def test_real_article_urls_are_not_filtered(url):
    """CARRIED INVARIANT (born-green): over-filtering would trade a junk bug
    for a recall bug. Note the third specimen — a bloomberg.com URL passes; the
    filter is about page CLASS, never about the outlet."""
    assert discovery.url_reject_reason(url) is None


@pytest.mark.parametrize("url", LIVE_STORY_SPECIMENS)
def test_dated_live_coverage_is_a_story_not_a_listing(url):
    """The dry run's own finding, pinned. A DATE component or a real headline
    slug outvotes a listing word in the path — otherwise the cleanup would
    have proposed deleting rows that shipped briefings cite."""
    assert discovery.url_reject_reason(url) is None


@pytest.mark.parametrize("url", LISTING_LEAK_SPECIMENS)
def test_a_headline_shaped_slug_does_not_rescue_a_listing_page(url):
    """gate MEDIUM-2. My first fix let a ≥3-word tail slug outvote the listing
    segments, which reopened the entire class — listing pages carry
    headline-shaped slugs by design. Only a DATE rescues now."""
    assert discovery.url_reject_reason(url) == "listing-or-section-page"


@pytest.mark.parametrize("url", DATED_INDEX_SPECIMENS)
def test_a_date_does_not_rescue_a_pure_index_page(url):
    """The precedence rule's other half: a date on an index page narrows the
    INDEX ('everything from 16 July'), it does not make the page a story."""
    assert discovery.url_reject_reason(url) == "listing-or-section-page"


def test_the_article_escape_hatch_does_not_outvote_the_host_rules():
    """The escape hatch is bounded. A platform page with a gorgeous slug is
    still a platform page — outlets' own reporting is what we are after."""
    assert discovery.url_reject_reason(
        "https://www.youtube.com/watch/2026/07/16/iran-strait-of-hormuz-red-line"
    ) == "audio-video-page"
    assert discovery.url_reject_reason(
        "https://www.facebook.com/2026/07/16/five-big-news-stories-overnight"
    ) == "social-post"
    assert discovery.url_reject_reason(
        "https://www.bloomberg.com/2026/07/16/sitemaps/news-latest-index.xml"
    ) == "feed-or-sitemap-file"


def test_the_sitemap_xml_never_enters_the_news_database(
    migrated_con, fake_api, monkeypatch
):
    """The headline specimen: 'Sonar returned a sitemap XML file as a news
    result' (engineering-5 §3.2)."""
    urls = ARTICLE_SPECIMENS + ["https://www.bloomberg.com/sitemaps/news/latest.xml"]
    route(fake_api, monkeypatch, sonar_payload(urls))
    status = discovery.run_discovery(
        migrated_con, cfg_with_interests(), env=KEY_ENV, now_iso=NOW)
    stored = sonar_urls(migrated_con)
    assert stored == ARTICLE_SPECIMENS
    assert not any(u.endswith(".xml") for u in stored)
    # Degrade LOUDLY: the drop is reported, never silently swallowed.
    assert "1 dropped (feed-or-sitemap-file 1)" in status


def test_the_full_junk_slice_is_dropped_and_itemised(
    migrated_con, fake_api, monkeypatch
):
    urls = [u for u, _ in JUNK_SPECIMENS] + ARTICLE_SPECIMENS
    route(fake_api, monkeypatch, sonar_payload(urls))
    status = discovery.run_discovery(
        migrated_con, cfg_with_interests(), env=KEY_ENV, now_iso=NOW)
    stored = sonar_urls(migrated_con)
    # MAX_DISCOVERY_ITEMS caps the slice at 8, so only the first 8 of the 12
    # results are considered at all — every one of those 8 is junk here.
    assert stored == []
    assert "dropped (" in status
    assert "8 dropped" in status


def test_the_answer_text_is_still_never_persisted(
    migrated_con, fake_api, monkeypatch
):
    """CARRIED INVARIANT (born-green): NL-101 READS the answer as a signal. It
    must not start STORING it — the faithfulness rule is unchanged."""
    sentinel = "ANSWER-TEXT-SENTINEL-9f3a " + USABLE_ANSWER
    route(fake_api, monkeypatch, sonar_payload(ARTICLE_SPECIMENS, answer=sentinel))
    discovery.run_discovery(
        migrated_con, cfg_with_interests(), env=KEY_ENV, now_iso=NOW)
    hits = migrated_con.execute(
        "SELECT COUNT(*) FROM source_items WHERE title LIKE '%SENTINEL%'"
        " OR raw_excerpt LIKE '%SENTINEL%' OR outlet LIKE '%SENTINEL%'"
    ).fetchone()[0]
    assert hits == 0


# ===========================================================================
# 3. THE RETRO-CLEAN
# ===========================================================================

def seed_rows(con, rows):
    """rows: [(url, title, source_type)] -> ids, in order."""
    ids = []
    with con:
        for url, title, source_type in rows:
            cur = con.execute(
                "INSERT INTO source_items"
                " (source_type, outlet, url, title, published_at, fetched_at,"
                "  raw_excerpt, wire_syndication_flag)"
                " VALUES (?, 'seed.example', ?, ?, NULL, ?, NULL, 0)",
                (source_type, url, title, NOW),
            )
            ids.append(cur.lastrowid)
    return ids


def seed_briefing(con, item_ids):
    with con:
        con.execute(
            "INSERT INTO briefings (date, story_slots, corroboration_labels,"
            " narrative_text, script_text, audio_file_path, token_cost,"
            " generated_at)"
            " VALUES ('2026-07-20', ?, '[]', '', '', NULL, 0.0, ?)",
            (json.dumps([{"slot": 1, "item_ids": list(item_ids)}]), NOW),
        )


def test_scan_separates_junk_articles_and_cited_rows(migrated_con):
    junk_ids = seed_rows(migrated_con, [
        (u, f"junk {i}", "sonar") for i, (u, _) in enumerate(JUNK_SPECIMENS)])
    good_ids = seed_rows(migrated_con, [
        (u, f"story {i}", "sonar") for i, u in enumerate(ARTICLE_SPECIMENS)])
    rss_ids = seed_rows(migrated_con, [
        ("https://www.ft.com/", "an RSS homepage row", "rss")])
    seed_briefing(migrated_con, [junk_ids[0], good_ids[0]])

    scan = discovery.scan_discovery_rows(migrated_con)
    assert [e["id"] for e in scan["cited"]] == [junk_ids[0]]      # junk BUT cited
    assert [e["id"] for e in scan["removable"]] == junk_ids[1:]
    assert [e["id"] for e in scan["kept"]] == good_ids
    # RSS rows are never in scope — this tool is about tier-2 discovery only.
    assert rss_ids[0] not in [e["id"] for e in
                              scan["cited"] + scan["removable"] + scan["kept"]]


def test_dry_run_is_the_default_and_deletes_nothing(migrated_con):
    """NL-97's posture, applied to the cleanup: a destructive instrument
    defaults to showing you what it would do."""
    seed_rows(migrated_con, [(u, "junk", "sonar") for u, _ in JUNK_SPECIMENS])
    before = migrated_con.execute(
        "SELECT COUNT(*) FROM source_items").fetchone()[0]
    result = discovery.clean_discovery_rows(migrated_con)      # no apply=
    assert result["applied"] is False
    assert result["deleted"] == 0
    assert len(result["removable"]) == len(JUNK_SPECIMENS)
    assert migrated_con.execute(
        "SELECT COUNT(*) FROM source_items").fetchone()[0] == before


def test_apply_deletes_exactly_the_rows_the_dry_run_listed(migrated_con):
    junk_ids = seed_rows(migrated_con, [
        (u, "junk", "sonar") for u, _ in JUNK_SPECIMENS])
    good_ids = seed_rows(migrated_con, [
        (u, "story", "sonar") for u in ARTICLE_SPECIMENS])
    seed_briefing(migrated_con, [junk_ids[0]])

    planned = [e["id"] for e in
               discovery.clean_discovery_rows(migrated_con)["removable"]]
    result = discovery.clean_discovery_rows(migrated_con, apply=True)
    assert result["applied"] is True
    assert result["deleted"] == len(planned)

    survivors = [r["id"] for r in migrated_con.execute(
        "SELECT id FROM source_items ORDER BY id")]
    assert survivors == [junk_ids[0]] + good_ids     # the cited junk row stayed


def test_a_cited_row_is_never_deleted_even_when_it_is_junk(migrated_con):
    """A shipped briefing's citation must keep resolving. Cleaning history is
    not worth breaking the record."""
    junk_ids = seed_rows(migrated_con, [
        ("https://www.bloomberg.com/sitemaps/news/latest.xml", "sitemap", "sonar")])
    seed_briefing(migrated_con, junk_ids)
    result = discovery.clean_discovery_rows(migrated_con, apply=True)
    assert result["deleted"] == 0
    assert [e["id"] for e in result["cited"]] == junk_ids
    assert migrated_con.execute(
        "SELECT COUNT(*) FROM source_items WHERE id = ?", (junk_ids[0],)
    ).fetchone()[0] == 1


def test_archived_briefings_protect_their_citations_too(migrated_con):
    junk_ids = seed_rows(migrated_con, [
        ("https://www.bloomberg.com/markets", "section", "sonar")])
    seed_briefing(migrated_con, [])          # briefings_history FKs to briefings
    briefing_id = migrated_con.execute(
        "SELECT id FROM briefings ORDER BY id DESC LIMIT 1").fetchone()[0]
    with migrated_con:
        migrated_con.execute(
            "INSERT INTO briefings_history (briefing_id, date, story_slots,"
            " corroboration_labels, narrative_text, script_text,"
            " audio_file_path, token_cost, generated_at, archived_at)"
            " VALUES (?, '2026-07-19', ?, '[]', '', '', NULL, 0.0, ?, ?)",
            (briefing_id, json.dumps([{"slot": 1, "item_ids": junk_ids}]),
             NOW, NOW),
        )
    result = discovery.clean_discovery_rows(migrated_con, apply=True)
    assert result["deleted"] == 0
    assert [e["id"] for e in result["cited"]] == junk_ids


def test_clean_is_idempotent(migrated_con):
    seed_rows(migrated_con, [(u, "junk", "sonar") for u, _ in JUNK_SPECIMENS])
    first = discovery.clean_discovery_rows(migrated_con, apply=True)
    second = discovery.clean_discovery_rows(migrated_con, apply=True)
    assert first["deleted"] == len(JUNK_SPECIMENS)
    assert second["deleted"] == 0
    assert second["removable"] == []


def test_clean_is_offline_and_free(migrated_con, no_network):
    seed_rows(migrated_con, [(u, "junk", "sonar") for u, _ in JUNK_SPECIMENS])
    discovery.clean_discovery_rows(migrated_con, apply=True)
    assert no_network == []


# ===========================================================================
# 4. THE CLI SURFACES — the verbs the principal actually types
# ===========================================================================

def test_discovery_clean_verb_is_dry_run_by_default(tmp_paths, capsys):
    """Wiring proof for the retro-clean: the verb exists, reaches the real
    classifier, and changes nothing without --apply."""
    from newslens import cli, db

    db.migrate()
    con = db.connect()
    try:
        seed_rows(con, [(u, "junk", "sonar") for u, _ in JUNK_SPECIMENS])
    finally:
        con.close()

    rc = cli.main(["discovery-clean"])
    out = capsys.readouterr().out
    assert rc == 0
    assert f"{len(JUNK_SPECIMENS)} junk-classed and uncited — would delete" in out
    assert "DRY RUN — nothing was changed" in out
    assert "[feed-or-sitemap-file] https://www.bloomberg.com/sitemaps/news/latest.xml" in out

    con = db.connect()
    try:
        assert con.execute(
            "SELECT COUNT(*) FROM source_items").fetchone()[0] == len(JUNK_SPECIMENS)
    finally:
        con.close()


def test_discovery_clean_apply_deletes_and_says_so(tmp_paths, capsys):
    from newslens import cli, db

    db.migrate()
    con = db.connect()
    try:
        seed_rows(con, [(u, "junk", "sonar") for u, _ in JUNK_SPECIMENS])
        seed_rows(con, [(u, "story", "sonar") for u in ARTICLE_SPECIMENS])
    finally:
        con.close()

    rc = cli.main(["discovery-clean", "--apply"])
    out = capsys.readouterr().out
    assert rc == 0
    assert f"APPLIED — {len(JUNK_SPECIMENS)} row(s) deleted." in out

    con = db.connect()
    try:
        survivors = [r["url"] for r in con.execute(
            "SELECT url FROM source_items ORDER BY id")]
    finally:
        con.close()
    assert survivors == ARTICLE_SPECIMENS


def test_ingest_help_no_longer_promises_a_discovery_call(capsys):
    """Doc-honesty pin: the help text told the principal ingest 'adds the
    capped Sonar discovery call when PERPLEXITY_API_KEY is set'. Under the
    pause that sentence is false."""
    from newslens import cli

    with pytest.raises(SystemExit):
        cli.main(["--help"])
    parent = " ".join(capsys.readouterr().out.split())   # argparse rewraps
    assert "Tier-2 Sonar discovery is PAUSED by ruling" in parent
    assert "adds the capped Sonar discovery call" not in parent

    with pytest.raises(SystemExit):
        cli.main(["ingest", "--help"])
    verb = " ".join(capsys.readouterr().out.split())
    assert "redundant while discovery is paused" in verb


def test_show_is_display_only_and_never_bounds_what_apply_deletes(tmp_paths, capsys):
    """gate ruling 2026-07-26. The flag was always display-only, but named
    --limit it read as a bound on --apply — the gate's own `--apply --limit 3`
    deleted all 38 rows. Renamed --show; this pins that the SEMANTICS the name
    now promises are the semantics it has."""
    from newslens import cli, db

    db.migrate()
    con = db.connect()
    try:
        seed_rows(con, [(u, "junk", "sonar") for u, _ in JUNK_SPECIMENS])
    finally:
        con.close()

    rc = cli.main(["discovery-clean", "--apply", "--show", "3"])
    out = capsys.readouterr().out
    assert rc == 0
    # Three rows PRINTED...
    assert out.count("    [") == 3
    assert f"… and {len(JUNK_SPECIMENS) - 3} more (--show" in out
    # ...and ALL of them deleted. The count line said so before the deletion.
    assert f"APPLIED — {len(JUNK_SPECIMENS)} row(s) deleted." in out
    con = db.connect()
    try:
        assert con.execute(
            "SELECT COUNT(*) FROM source_items").fetchone()[0] == 0
    finally:
        con.close()


def test_the_old_limit_spelling_is_rejected(tmp_paths, capsys):
    """The rename has teeth: `--limit` must not keep working silently beside
    `--show`, or the footgun survives in muscle memory."""
    from newslens import cli, db

    db.migrate()
    with pytest.raises(SystemExit) as excinfo:
        cli.main(["discovery-clean", "--limit", "3"])
    assert excinfo.value.code == 2
    assert "unrecognized arguments: --limit" in capsys.readouterr().err
