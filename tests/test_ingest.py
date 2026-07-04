"""Ingestion contract (src/newslens/ingest.py contract block; ADR-0003 §4-5,7).

All feeds are served by the local fake server — zero real network. Covers:
idempotent upsert per (url, UTC fetch-day) with fetched_at preserved on
update; next-UTC-day re-snapshot; per-source transaction isolation; the exact
visible degradation line; the 20-item cap and 1500-char excerpt truncation;
the custom 308 redirect handler (3.9 urllib doesn't follow 308 natively);
malformed/empty/hostile feed handling; tier enforcement at the fetch seam;
and the cross-feed last-writer-wins attribution pin (implementer judgment
call, flagged to the reviewer).
"""

from __future__ import annotations

import sqlite3
import urllib.error
import urllib.request

import pytest

from newslens import config, ingest

from conftest import make_rss

DAY1_MORNING = "2026-07-03T08:00:00.000Z"
DAY1_EVENING = "2026-07-03T23:59:59.000Z"
DAY2 = "2026-07-04T00:00:01.000Z"


def mk_item(url="https://x.example/story-1", title="Title", published=None, excerpt=None):
    return ingest.ParsedItem(url=url, title=title, published_at=published, excerpt=excerpt)


def mk_source(name="Outlet A", url="https://a.invalid/feed.xml", **kw):
    return config.Source(name=name, rss_url=url, **kw)


def rows(con):
    return con.execute(
        "SELECT outlet, url, title, fetched_at, raw_excerpt, wire_syndication_flag,"
        " source_type, published_at FROM source_items ORDER BY id"
    ).fetchall()


# --- upsert: the (url, UTC fetch-day) idempotency contract ---------------------

def test_same_utc_day_updates_in_place_and_preserves_fetched_at(migrated_con):
    src = mk_source()
    assert ingest.upsert_item(migrated_con, src, mk_item(title="v1"), DAY1_MORNING) == "new"
    assert (
        ingest.upsert_item(migrated_con, src, mk_item(title="v2 edited upstream"), DAY1_EVENING)
        == "updated"
    )
    all_rows = rows(migrated_con)
    assert len(all_rows) == 1  # no duplicate
    assert all_rows[0]["title"] == "v2 edited upstream"
    # fetched_at preserved: the row never migrates off its original fetch-day.
    assert all_rows[0]["fetched_at"] == DAY1_MORNING


def test_next_utc_day_inserts_a_new_snapshot_row(migrated_con):
    src = mk_source()
    assert ingest.upsert_item(migrated_con, src, mk_item(title="day1"), DAY1_EVENING) == "new"
    assert ingest.upsert_item(migrated_con, src, mk_item(title="day2"), DAY2) == "new"
    all_rows = rows(migrated_con)
    assert len(all_rows) == 2
    assert {r["title"] for r in all_rows} == {"day1", "day2"}


def test_utc_fetch_day_is_the_utc_prefix():
    assert ingest.utc_fetch_day("2026-07-03T23:59:59.000Z") == "2026-07-03"
    assert ingest.utc_fetch_day("2026-07-04T00:00:01.000Z") == "2026-07-04"


def test_utc_now_iso_shape():
    import re

    assert re.fullmatch(
        r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z", ingest.utc_now_iso()
    )


def test_cross_feed_same_url_same_day_is_last_writer_wins(migrated_con):
    """PINS CURRENT BEHAVIOR — implementer judgment call flagged for review:
    when two different sources carry the same URL on the same UTC day, the
    later fetch overwrites outlet/title/wire flag (attribution follows the
    last writer). M3 corroboration counting must account for this. If the
    design changes, change this pin with it."""
    plain = mk_source(name="Outlet A")
    wire = mk_source(name="Wire Republisher", url="https://b.invalid/feed.xml",
                     wire_syndication=True)
    shared = mk_item(url="https://x.example/shared-story")
    assert ingest.upsert_item(migrated_con, plain, shared, DAY1_MORNING) == "new"
    assert ingest.upsert_item(migrated_con, wire, shared, DAY1_EVENING) == "updated"
    row = rows(migrated_con)[0]
    assert row["outlet"] == "Wire Republisher"
    assert row["wire_syndication_flag"] == 1


# --- parsing: caps, truncation, hostile input -----------------------------------

def test_parse_entries_caps_at_20_items():
    raw = make_rss(
        [{"title": f"T{i}", "url": f"https://x.example/{i}"} for i in range(25)]
    )
    items, skipped = ingest.parse_entries(raw)
    assert len(items) == ingest.MAX_ITEMS_PER_FEED == 20
    assert skipped == 0


def test_parse_entries_truncates_excerpt_at_1500_and_strips_html():
    long_html = "<p>" + ("word " * 1000) + "</p><b>bold</b>"
    raw = make_rss([{"title": "T", "url": "https://x.example/1", "summary": long_html}])
    items, _ = ingest.parse_entries(raw)
    assert len(items[0].excerpt) == ingest.MAX_EXCERPT_CHARS == 1500
    assert "<" not in items[0].excerpt


def test_parse_entries_truncates_title_at_500():
    raw = make_rss([{"title": "T" * 600, "url": "https://x.example/1"}])
    items, _ = ingest.parse_entries(raw)
    assert len(items[0].title) == 500


def test_parse_entries_skips_and_counts_items_missing_url_or_title():
    raw = make_rss(
        [
            {"title": "no url here"},
            {"url": "https://x.example/no-title"},
            {"title": "good", "url": "https://x.example/good"},
        ]
    )
    items, skipped = ingest.parse_entries(raw)
    assert [i.url for i in items] == ["https://x.example/good"]
    assert skipped == 2


def test_parse_entries_reads_pubdate_as_utc_iso():
    raw = make_rss(
        [{"title": "T", "url": "https://x.example/1",
          "pubdate": "Thu, 03 Jul 2026 08:05:00 GMT"}]
    )
    items, _ = ingest.parse_entries(raw)
    assert items[0].published_at == "2026-07-03T08:05:00Z"


def test_parse_entries_raises_loud_on_garbage_bytes():
    with pytest.raises(ValueError) as excinfo:
        ingest.parse_entries(b"\x00\x01\x02 this is not xml at all")
    assert "feed did not parse" in str(excinfo.value)


def test_parse_entries_raises_loud_on_tag_soup_html():
    """Broken/hostile HTML (unclosed tags — the common bot-wall page) is
    bozo-with-no-entries and must raise."""
    with pytest.raises(ValueError):
        ingest.parse_entries(b"<html><body><h1>Login required<p>token expired")


def test_parse_entries_wellformed_html_is_a_silent_empty_success():
    """PINS CURRENT BEHAVIOR (M2 QA adjudication, flagged as an observation):
    a WELL-FORMED XML/HTML page that isn't a feed parses with bozo=0 and zero
    entries, so ingestion reports the source as a SUCCESS with 0 items —
    every run, forever, with no degradation line. The doctor catches this
    (feed-marker WARN); the ingest report does not. If M3 adds a 'parsed but
    never yields entries' signal, update this pin with it."""
    items, skipped = ingest.parse_entries(
        b"<html><body><h1>Login required</h1></body></html>"
    )
    assert items == [] and skipped == 0


def test_parse_entries_empty_feed_is_zero_items_not_an_error():
    items, skipped = ingest.parse_entries(make_rss([]))
    assert items == [] and skipped == 0


def test_strip_html_collapses_tags_and_whitespace():
    assert ingest.strip_html("<p>a  b</p>\n<div>c</div>") == "a b c"


# --- HTTP: the 308 redirect handler ----------------------------------------------

def test_stock_urllib_does_not_follow_308_which_is_why_the_handler_exists(fake_api):
    """Control test: on Python 3.9 the default opener raises on 308. If this
    ever FAILS (e.g. a Python upgrade follows 308 natively), the custom
    handler in ingest.py has become redundant — flag it, don't just delete."""
    fake_api.add_route("/moved", status=308, location="/dest.xml")
    fake_api.add_route("/dest.xml", body=make_rss([{"title": "T", "url": "https://x.example/1"}]))
    with pytest.raises(urllib.error.HTTPError) as excinfo:
        urllib.request.urlopen(fake_api.base_url + "/moved", timeout=5)
    assert excinfo.value.code == 308


def test_fetch_feed_bytes_follows_308(fake_api):
    fake_api.add_route("/moved", status=308, location="/dest.xml")
    dest_body = make_rss([{"title": "T", "url": "https://x.example/1"}])
    fake_api.add_route("/dest.xml", body=dest_body)
    raw = ingest.fetch_feed_bytes(fake_api.base_url + "/moved")
    assert raw == dest_body
    paths_hit = [r["path"] for r in fake_api.recorded]
    assert paths_hit == ["/moved", "/dest.xml"]


def test_fetch_feed_bytes_sends_the_pipeline_user_agent(fake_api):
    fake_api.add_route("/ua.xml", body=make_rss([]))
    ingest.fetch_feed_bytes(fake_api.base_url + "/ua.xml")
    assert fake_api.recorded[-1]["user_agent"].startswith("NewsLens/")


# --- per-source transactions -------------------------------------------------------

def test_ingest_source_rolls_back_all_writes_on_midfeed_failure(
    migrated_con, fake_api, monkeypatch
):
    """One transaction per source: a mid-feed failure leaves NO half-writes."""
    fake_api.add_route(
        "/iso.xml",
        body=make_rss(
            [{"title": f"T{i}", "url": f"https://x.example/iso/{i}"} for i in range(3)]
        ),
    )
    calls = {"n": 0}
    real_upsert = ingest.upsert_item

    def flaky(con, source, item, now_iso):
        calls["n"] += 1
        if calls["n"] == 2:
            raise sqlite3.OperationalError("simulated mid-feed failure")
        return real_upsert(con, source, item, now_iso)

    monkeypatch.setattr(ingest, "upsert_item", flaky)
    src = mk_source(name="Iso", url=fake_api.base_url + "/iso.xml")
    with pytest.raises(sqlite3.OperationalError):
        ingest.ingest_source(migrated_con, src, DAY1_MORNING)
    count = migrated_con.execute("SELECT COUNT(*) FROM source_items").fetchone()[0]
    assert count == 0  # the first item's write rolled back with the failure


def test_run_ingest_one_bad_source_cannot_poison_the_others(migrated_con, fake_api):
    fake_api.add_route(
        "/good.xml", body=make_rss([{"title": "G", "url": "https://x.example/g"}])
    )
    fake_api.add_route("/bad.xml", body=b"\xff\xfe totally not a feed")
    cfg = config.SourcesConfig(
        sources=[
            mk_source(name="Good", url=fake_api.base_url + "/good.xml"),
            mk_source(name="Bad", url=fake_api.base_url + "/bad.xml"),
        ]
    )
    report = ingest.run_ingest(con=migrated_con, cfg=cfg, with_discovery=False)
    assert report.succeeded == ["Good"]
    assert list(report.failed) == ["Bad"]
    assert "ValueError" in report.failed["Bad"]
    assert report.items_new == 1
    all_rows = rows(migrated_con)
    assert len(all_rows) == 1 and all_rows[0]["outlet"] == "Good"


# --- graceful degradation: the visible contract line --------------------------------

def test_degradation_message_is_the_exact_visible_line(migrated_con, fake_api):
    fake_api.add_route(
        "/a.xml", body=make_rss([{"title": "A", "url": "https://x.example/a"}])
    )
    fake_api.add_route(
        "/b.xml", body=make_rss([{"title": "B", "url": "https://x.example/b"}])
    )
    cfg = config.SourcesConfig(
        sources=[
            mk_source(name="Alpha", url=fake_api.base_url + "/a.xml"),
            mk_source(name="Beta", url=fake_api.base_url + "/b.xml"),
            mk_source(name="Dead Feed", url=fake_api.dead_url("/dead.xml")),
        ]
    )
    report = ingest.run_ingest(con=migrated_con, cfg=cfg, with_discovery=False)
    assert report.attempted == 3
    assert report.any_success
    assert report.degradation_message == (
        "1 of 3 sources unavailable this run: Dead Feed — "
        "briefing inputs come from the remaining 2"
    )


def test_no_failures_means_no_degradation_message(migrated_con, fake_api):
    fake_api.add_route(
        "/ok.xml", body=make_rss([{"title": "T", "url": "https://x.example/t"}])
    )
    cfg = config.SourcesConfig(sources=[mk_source(name="OK", url=fake_api.base_url + "/ok.xml")])
    report = ingest.run_ingest(con=migrated_con, cfg=cfg, with_discovery=False)
    assert report.degradation_message is None


def test_all_sources_failing_is_reported_not_raised(migrated_con, fake_api):
    cfg = config.SourcesConfig(
        sources=[
            mk_source(name="Dead A", url=fake_api.dead_url("/a.xml")),
            mk_source(name="Dead B", url=fake_api.dead_url("/b.xml")),
        ]
    )
    report = ingest.run_ingest(con=migrated_con, cfg=cfg, with_discovery=False)
    assert not report.any_success
    assert len(report.failed) == 2
    assert report.degradation_message.startswith("2 of 2 sources unavailable this run:")


# --- tier enforcement at the fetch seam ----------------------------------------------

def test_run_ingest_fetches_only_fetchable_sources(migrated_con, fake_api):
    """reference_only is structurally unfetchable EVEN WITH enabled: true and
    a live URL; disabled sources are not fetched. Verified by what the server
    actually saw, not by reading flags."""
    fetchable_url = fake_api.add_route(
        "/yes.xml", body=make_rss([{"title": "Y", "url": "https://x.example/y"}])
    )
    ref_url = fake_api.add_route(
        "/never.xml", body=make_rss([{"title": "N", "url": "https://x.example/n"}])
    )
    off_url = fake_api.add_route(
        "/off.xml", body=make_rss([{"title": "O", "url": "https://x.example/o"}])
    )
    cfg = config.SourcesConfig(
        sources=[
            mk_source(name="Fetch Me", url=fetchable_url),
            config.Source(
                name="Reference Only", rss_url=ref_url, tier="reference_only", enabled=True
            ),
            config.Source(name="Disabled", rss_url=off_url, enabled=False),
        ]
    )
    report = ingest.run_ingest(con=migrated_con, cfg=cfg, with_discovery=False)
    assert report.attempted == 1
    assert report.succeeded == ["Fetch Me"]
    paths_hit = {r["path"] for r in fake_api.recorded}
    assert "/yes.xml" in paths_hit
    assert "/never.xml" not in paths_hit  # structural: never fetched
    assert "/off.xml" not in paths_hit


def test_zero_entry_sources_get_a_visible_warning(migrated_con, fake_api):
    """M3 closes M2 QA observation 2: a source that fetches+parses but yields
    zero entries (well-formed HTML at the rss_url, or a genuinely empty feed)
    now warns in the run report instead of passing silently forever."""
    html_url = fake_api.add_route(
        "/hollow.xml",
        body=b"<html><body><h1>Login required</h1></body></html>",
        content_type="text/html",
    )
    empty_url = fake_api.add_route("/empty.xml", body=make_rss([]))
    good_url = fake_api.add_route(
        "/good2.xml", body=make_rss([{"title": "G", "url": "https://x.example/g2"}])
    )
    cfg = config.SourcesConfig(
        sources=[
            mk_source(name="Hollow", url=html_url),
            mk_source(name="Empty", url=empty_url),
            mk_source(name="Good", url=good_url),
        ]
    )
    report = ingest.run_ingest(con=migrated_con, cfg=cfg, with_discovery=False)
    assert sorted(report.succeeded) == ["Empty", "Good", "Hollow"]
    zero_warnings = [w for w in report.warnings if "yielded 0 entries" in w]
    assert len(zero_warnings) == 2
    assert any(w.startswith("Hollow:") for w in zero_warnings)
    assert any(w.startswith("Empty:") for w in zero_warnings)
    assert not any(w.startswith("Good:") for w in zero_warnings)


def test_run_ingest_refuses_politely_when_nothing_is_fetchable(migrated_con):
    cfg = config.SourcesConfig(
        sources=[config.Source(name="Ref", tier="reference_only")]
    )
    with pytest.raises(config.SourcesParseError) as excinfo:
        ingest.run_ingest(con=migrated_con, cfg=cfg, with_discovery=False)
    assert str(excinfo.value) == config.NO_ACTIVE_SOURCES_MSG


def test_run_ingest_propagates_cautious_enabled_warnings(migrated_con, fake_api):
    url = fake_api.add_route(
        "/c.xml", body=make_rss([{"title": "C", "url": "https://x.example/c"}])
    )
    cfg = config.SourcesConfig(
        sources=[config.Source(name="Caut", rss_url=url, tier="cautious", enabled=True)],
        warnings=["cautious source 'Caut' is explicitly enabled — aggregator "
                  "content will be flagged and down-weighted downstream"],
    )
    report = ingest.run_ingest(con=migrated_con, cfg=cfg, with_discovery=False)
    assert any("explicitly enabled" in w for w in report.warnings)


# --- discovery wiring from the ingest side ---------------------------------------------

def test_run_ingest_keyless_discovery_reports_skip_and_sends_nothing(
    migrated_con, fake_api, monkeypatch
):
    from newslens import discovery

    monkeypatch.setattr(
        discovery, "PERPLEXITY_CHAT_URL", fake_api.base_url + "/chat/completions"
    )
    url = fake_api.add_route(
        "/k.xml", body=make_rss([{"title": "K", "url": "https://x.example/k"}])
    )
    cfg = config.SourcesConfig(
        sources=[mk_source(name="K", url=url)], interests_broad=["tech"]
    )
    report = ingest.run_ingest(con=migrated_con, cfg=cfg, env={}, with_discovery=True)
    assert report.discovery_status.startswith("skipped — PERPLEXITY_API_KEY not set")
    posts = [r for r in fake_api.recorded if r["method"] == "POST"]
    assert posts == []  # cold seam: no request was ever built


def test_run_ingest_no_discovery_flag_means_not_attempted(migrated_con, fake_api):
    url = fake_api.add_route(
        "/nd.xml", body=make_rss([{"title": "N", "url": "https://x.example/nd"}])
    )
    cfg = config.SourcesConfig(sources=[mk_source(name="ND", url=url)])
    report = ingest.run_ingest(con=migrated_con, cfg=cfg, with_discovery=False)
    assert report.discovery_status == "not attempted"
