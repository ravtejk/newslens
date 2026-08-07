"""NL-142 — cross-day dedupe, pool receipts, and the staleness tooth.

Charter: slate-land gate `research/2026-08-03--slateland-gate.md` §R-C option
(c) + §R-E, ruled by the principal 2026-08-06.

Three mechanisms, one ingest surface:

  1. URL IDENTITY + FIRST-SEEN RECENCY. One URL is one row across all days,
     and its fetched_at is the first sighting and never moves. The pair is the
     fix; either half alone is not.
  2. POOL RECEIPTS. The 550-item cap is a selection-capacity derate that had
     been firing unremarked on 27 of 48 runs. Every run now records its pool
     composition, and a run that evicts anything names what it threw away.
  3. THE STALENESS TOOTH. A feed frozen years ago answers HTTP 200 with valid
     RSS and passes every other check. Live specimen: CNN's top-stories feed,
     frozen at 2023-04-25, in the principal's own file and in the shipped
     template.

Every pin here is born red against HEAD `ac11a3f` unless labelled otherwise.
"""

import json
import sqlite3

import pytest

from newslens import discovery, doctor, ingest, ranking


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

DAY1 = "2026-08-01T12:00:00.000Z"
DAY2 = "2026-08-02T12:00:00.000Z"
DAY9 = "2026-08-09T12:00:00.000Z"


def mk_source(name="Outlet", url="https://a.invalid/feed.xml", **kw):
    from newslens import config

    return config.Source(name=name, rss_url=url, **kw)


def mk_item(url="https://x.example/story", title="T", published=None, excerpt=None):
    return ingest.ParsedItem(
        url=url, title=title, published_at=published, excerpt=excerpt
    )


@pytest.fixture
def profile_db():
    """A migrated DB at the PROFILE's real path (sandboxed by the autouse
    fixture), not a side scratch file.

    check_pool_capacity() resolves paths.DB_PATH itself and opens it mode=ro —
    so driving it through the real path is what proves the doctor reads the
    profile it claims to be reporting on, rather than a connection a test
    handed it."""
    from newslens import db, paths

    db.migrate(db_path=paths.DB_PATH)
    con = db.connect(paths.DB_PATH)
    yield con
    con.close()


def insert_pool_row(con, outlet, url, fetched_at):
    con.execute(
        "INSERT INTO source_items (source_type, outlet, url, title, fetched_at)"
        " VALUES ('rss', ?, ?, ?, ?)",
        (outlet, url, f"title for {url}", fetched_at),
    )


# ---------------------------------------------------------------------------
# 1. URL identity + the recency anchor
# ---------------------------------------------------------------------------

def test_a_url_seen_on_a_later_day_does_not_create_a_second_row(migrated_con):
    """The re-insert machine, dead. Nine days, one row."""
    src = mk_source()
    item = mk_item()
    assert ingest.upsert_item(migrated_con, src, item, DAY1) == "new"
    for day in (DAY2, DAY9):
        assert ingest.upsert_item(migrated_con, src, item, day) == "updated"
    n = migrated_con.execute("SELECT COUNT(*) FROM source_items").fetchone()[0]
    assert n == 1


def test_a_re_fetch_never_refreshes_the_recency_anchor(migrated_con):
    """THE load-bearing pin of this batch.

    If fetched_at moved on re-sighting, an item that merely lingers in a feed
    would be permanently 'recent', the 14-day candidate window would never age
    anything out, and the fix would have reproduced the disease under a new
    key. An item is exactly as old as its FIRST sighting."""
    src = mk_source()
    ingest.upsert_item(migrated_con, src, mk_item(title="v1"), DAY1)
    ingest.upsert_item(migrated_con, src, mk_item(title="v2"), DAY9)
    row = migrated_con.execute(
        "SELECT fetched_at, title FROM source_items"
    ).fetchone()
    assert row["fetched_at"] == DAY1  # eight days later, still day one
    assert row["title"] == "v2"       # mutable fields DO follow upstream edits


def test_the_aging_consequence_is_real_not_just_bookkeeping(migrated_con):
    """The point of the anchor: a lingering item LEAVES the candidate window.

    Pinned through gather_items rather than by reading the column, because the
    behavior that matters is pool eligibility, not the stored value."""
    src = mk_source()
    item = mk_item(url="https://x.example/lingering")
    ingest.upsert_item(migrated_con, src, item, DAY1)
    ingest.upsert_item(migrated_con, src, item, DAY9)  # still in the feed
    # A window opening after the first sighting must not see it.
    rows = ranking.gather_items(migrated_con, "2026-08-05T00:00:00")
    assert [r["url"] for r in rows] == []


def test_pre_nl142_duplicate_rows_bind_to_the_earliest_sighting(migrated_con):
    """Rows written before this batch have several copies of one URL. The
    upsert must bind to the FIRST, or fetched_at silently becomes 'most
    recent copy' — the anchor defect wearing a different hat."""
    url = "https://x.example/historical"
    for day in (DAY1, DAY2, DAY9):
        insert_pool_row(migrated_con, "Outlet", url, day)
    assert ingest.upsert_item(
        migrated_con, mk_source(), mk_item(url=url, title="fresh"), DAY9
    ) == "updated"
    rows = migrated_con.execute(
        "SELECT fetched_at, title FROM source_items ORDER BY id"
    ).fetchall()
    assert rows[0]["fetched_at"] == DAY1 and rows[0]["title"] == "fresh"
    # The later leftovers are untouched — nothing is deleted (ADR-0022).
    assert [r["fetched_at"] for r in rows] == [DAY1, DAY2, DAY9]
    assert rows[1]["title"] != "fresh"


def test_same_day_rerun_is_still_idempotent(migrated_con):
    """CARRIED INVARIANT (born green, labelled): the org's re-run law widens
    from 'safe within one UTC day' to 'safe on any day' — the old promise is a
    strict subset, and it must not have been traded away for the new one."""
    src = mk_source()
    ingest.upsert_item(migrated_con, src, mk_item(), DAY1)
    ingest.upsert_item(migrated_con, src, mk_item(), DAY1)
    n = migrated_con.execute("SELECT COUNT(*) FROM source_items").fetchone()[0]
    assert n == 1


def test_the_surviving_day_index_can_never_be_violated(migrated_con):
    """CARRIED INVARIANT (born green, labelled): no migration ships with this
    batch, so UNIQUE(url, date(fetched_at)) is still live. It is a strictly
    weaker constraint than the URL key, so the new upsert cannot trip it —
    proven by driving many days through the real code path."""
    src = mk_source()
    for day in (DAY1, DAY2, DAY9, "2026-09-01T00:00:00.000Z"):
        ingest.upsert_item(migrated_con, src, mk_item(), day)  # must not raise
    idx = migrated_con.execute(
        "SELECT name FROM sqlite_master WHERE type='index'"
        " AND name='idx_source_items_url_fetch_day'"
    ).fetchone()
    assert idx is not None


def test_discovery_dedupes_across_days_too(migrated_con):
    """The twin upsert. Half a dedupe is no dedupe: had discovery kept the day
    key, it would have gone on re-inserting the same URLs daily."""
    result = [{"url": "https://x.example/sonar-story", "title": "S", "date": None}]
    assert discovery._store_results(migrated_con, result, DAY1)[0] == 1
    stored, dropped = discovery._store_results(migrated_con, result, DAY9)
    assert stored == 0
    assert dropped == {"already-known": 1}
    n = migrated_con.execute("SELECT COUNT(*) FROM source_items").fetchone()[0]
    assert n == 1


def test_a_known_url_is_not_reported_as_a_dropped_row(migrated_con):
    """CARRIED INVARIANT (born green — measured, not assumed).

    The behavior is unchanged by this batch, which is exactly why it needs a
    pin: the drop label moved from 'already-known-today' to 'already-known'
    and _dropped_phrase's exemption had to move WITH it. Move one and not the
    other and every known URL starts rendering in the run report's dropped
    count — a loud false alarm on the most common outcome there is, with no
    other test in the suite watching for it.
    MUTATION RECEIPT (pin-route law): reverting only the _dropped_phrase
    filter to 'already-known-today' turns this red."""
    result = [{"url": "https://x.example/sonar-story", "title": "S", "date": None}]
    discovery._store_results(migrated_con, result, DAY1)
    _, dropped = discovery._store_results(migrated_con, result, DAY9)
    assert discovery._dropped_phrase(dropped) == ""


# ---------------------------------------------------------------------------
# 2. Pool receipts + the cap tripwire
# ---------------------------------------------------------------------------

def test_pool_composition_counts_the_whole_window_not_the_capped_slice(migrated_con):
    over = ranking.MAX_INPUT_ITEMS + 25
    for i in range(over):
        insert_pool_row(migrated_con, "Outlet", f"https://x.example/{i}", DAY1)
    comp = ranking.pool_composition(migrated_con, "2026-07-01T00:00:00")
    assert comp["window_total"] == over
    assert comp["capped_to"] == ranking.MAX_INPUT_ITEMS
    assert comp["evicted"] == 25


def test_the_evicted_outlets_are_named_in_file_order(migrated_con):
    """Eviction is positional, not by age: one ingest run shares one stamp, so
    ORDER BY fetched_at DESC, id DESC is reverse INSERTION order, and
    insertion order is sources.yaml order. The outlet at the TOP of the file
    is the one that loses its day. This is the receipt that says so — it is
    what 27 silent capped runs never produced."""
    insert_pool_row(migrated_con, "First In File", "https://x.example/a", DAY1)
    insert_pool_row(migrated_con, "First In File", "https://x.example/b", DAY1)
    for i in range(ranking.MAX_INPUT_ITEMS):
        insert_pool_row(migrated_con, "Later Outlet", f"https://x.example/l{i}", DAY1)
    comp = ranking.pool_composition(migrated_con, "2026-07-01T00:00:00")
    assert comp["evicted"] == 2
    assert comp["evicted_by_outlet"] == [("First In File", 2)]


def test_the_cap_warning_names_the_count_and_the_outlets(migrated_con):
    """'hit the cap' alone is not a receipt — it tells a reader nothing they
    can act on. The count and the names are the whole point."""
    insert_pool_row(migrated_con, "Bloomberg Markets", "https://x.example/bb", DAY1)
    for i in range(ranking.MAX_INPUT_ITEMS):
        insert_pool_row(migrated_con, "Other", f"https://x.example/o{i}", DAY1)
    comp = ranking.pool_composition(migrated_con, "2026-07-01T00:00:00")
    warning = ranking.pool_warning(comp)
    assert warning is not None
    assert "1 EVICTED" in warning
    assert "Bloomberg Markets (1)" in warning
    assert "sources.yaml order" in warning


def test_a_pool_under_the_cap_stays_quiet(migrated_con):
    """A tripwire that fires when nothing happened trains the reader to ignore
    it. Silence here is the feature."""
    for i in range(10):
        insert_pool_row(migrated_con, "Outlet", f"https://x.example/{i}", DAY1)
    comp = ranking.pool_composition(migrated_con, "2026-07-01T00:00:00")
    assert comp["evicted"] == 0
    assert ranking.pool_warning(comp) is None


def test_doctor_surfaces_an_evicting_pool_from_the_run_record(profile_db):
    profile_db.execute(
        "INSERT INTO ranking_runs (date, meta) VALUES ('2026-08-06', ?)",
        (json.dumps({"pool": {"window_total": 574, "capped_to": 550, "evicted": 24,
                              "evicted_by_outlet": [["Bloomberg Markets", 20],
                                                    ["Bloomberg Politics", 4]]}}),),
    )
    profile_db.commit()
    results = doctor.check_pool_capacity()
    texts = " | ".join(r.text for r in results)
    assert any(r.status == doctor.WARN for r in results)
    assert "24 evicted" in texts and "Bloomberg Markets (20)" in texts


def test_doctor_reads_pre_nl142_runs_through_item_count(profile_db):
    """The 27 capped runs already on record predate meta['pool']. Their
    item_count is the only signal they left; the check must still see them or
    the history it exists to surface stays invisible."""
    for _ in range(3):
        profile_db.execute(
            "INSERT INTO ranking_runs (date, meta) VALUES ('2026-08-02', ?)",
            (json.dumps({"item_count": ranking.MAX_INPUT_ITEMS}),),
        )
    profile_db.commit()
    results = doctor.check_pool_capacity()
    assert any("3 of the last 3" in r.text for r in results)


def test_doctor_pool_check_passes_when_nothing_is_evicted(profile_db):
    profile_db.execute(
        "INSERT INTO ranking_runs (date, meta) VALUES ('2026-08-06', ?)",
        (json.dumps({"pool": {"window_total": 347, "capped_to": 347, "evicted": 0,
                              "evicted_by_outlet": []}}),),
    )
    profile_db.commit()
    results = doctor.check_pool_capacity()
    assert [r.status for r in results] == [doctor.PASS]


def test_doctor_pool_check_degrades_to_silence_not_failure(profile_db):
    """An empty or unreadable run history is not the reader's problem and must
    never become a doctor FAIL."""
    profile_db.execute(
        "INSERT INTO ranking_runs (date, meta) VALUES ('2026-08-06', '{}')"
    )
    profile_db.commit()
    assert doctor.check_pool_capacity() == []


# ---------------------------------------------------------------------------
# 3. The staleness tooth
# ---------------------------------------------------------------------------

def test_a_frozen_feed_is_flagged_by_age():
    """Shape taken from the live specimen: CNN top-stories, HTTP 200, valid
    RSS, 20 items, newest 2023-04-25 — enabled in the principal's own file and
    in the shipped template, feeding 20 three-year-old stories a day."""
    items = [mk_item(url=f"https://cnn.invalid/{i}", published="2023-04-25T17:44:36Z")
             for i in range(20)]
    staleness = ingest.feed_staleness(items, now_iso="2026-08-06T12:00:00.000Z")
    assert staleness["age_days"] > 1000
    warning = ingest.stale_feed_warning("CNN", staleness)
    assert warning is not None
    assert "CNN" in warning and "2023-04-25" in warning


def test_a_fresh_feed_says_nothing():
    items = [mk_item(published="2026-08-06T09:00:00Z")]
    staleness = ingest.feed_staleness(items, now_iso="2026-08-06T12:00:00.000Z")
    assert ingest.stale_feed_warning("STAT News", staleness) is None


def test_a_dateless_feed_is_exempt_not_stale():
    """Fierce Healthcare ships 20 undated items. Calling that 'stale' would be
    a fabricated claim about content we cannot date."""
    items = [mk_item(url=f"https://f.invalid/{i}") for i in range(20)]
    staleness = ingest.feed_staleness(items, now_iso="2026-08-06T12:00:00.000Z")
    assert staleness["dateless"] is True
    assert staleness["age_days"] is None
    assert ingest.stale_feed_warning("Fierce Healthcare", staleness) is None


def test_a_slow_specialist_feed_clears_the_threshold():
    """The threshold is derived, not guessed: the widest-spanning HEALTHY feed
    measured in the slate probes is CIDRAP, whose 20 items spanned 55.93 days
    on 2026-08-06. A 45d threshold would cry wolf on it; 60d clears it."""
    items = [mk_item(url="https://cidrap.invalid/old", published="2026-06-11T00:00:00Z"),
             mk_item(url="https://cidrap.invalid/new", published="2026-08-05T00:00:00Z")]
    staleness = ingest.feed_staleness(items, now_iso="2026-08-06T12:00:00.000Z")
    assert ingest.stale_feed_warning("CIDRAP", staleness) is None


def test_staleness_reads_the_newest_date_not_the_first_entry():
    """A feed ordered oldest-first must be judged on the newest date actually
    present, never on position — otherwise ordering alone fabricates a stale
    verdict."""
    items = [mk_item(url="https://o.invalid/1", published="2020-01-01T00:00:00Z"),
             mk_item(url="https://o.invalid/2", published="2026-08-05T00:00:00Z")]
    staleness = ingest.feed_staleness(items, now_iso="2026-08-06T12:00:00.000Z")
    assert ingest.stale_feed_warning("Oldest First", staleness) is None


def test_an_unparseable_date_degrades_to_undeterminable_not_to_a_wrong_age():
    items = [mk_item(published="not-a-date")]
    staleness = ingest.feed_staleness(items, now_iso="2026-08-06T12:00:00.000Z")
    assert staleness["age_days"] is None
    assert ingest.stale_feed_warning("Broken Dates", staleness) is None


def test_the_ingest_run_warns_on_a_stale_enabled_feed(migrated_con, fake_api):
    """The runtime half, through the real run_ingest path — not the helper."""
    from newslens import config

    fake_api.add_route(
        "/frozen.xml",
        body=(
            b'<?xml version="1.0"?><rss version="2.0"><channel><title>Frozen</title>'
            b"<item><title>Old news</title><link>https://frozen.invalid/1</link>"
            b"<pubDate>Tue, 25 Apr 2023 17:44:36 GMT</pubDate></item>"
            b"</channel></rss>"
        ),
    )
    cfg = config.SourcesConfig(
        sources=[mk_source(name="Frozen Wire", url=fake_api.base_url + "/frozen.xml")],
        interests_broad=["x"],
    )
    report = ingest.run_ingest(con=migrated_con, cfg=cfg, with_discovery=False)
    assert any("Frozen Wire" in w and "days old" in w for w in report.warnings)


def test_the_ingest_run_stays_quiet_on_a_healthy_feed(migrated_con, fake_api):
    """CARRIED INVARIANT (born green — measured, not assumed).

    Trivially true at HEAD, where no staleness warning exists at all; it earns
    its place only after the tooth lands, as the false-positive guard. A
    staleness check that warns on live feeds is worse than none.
    MUTATION RECEIPT (pin-route law): setting ingest.STALE_FEED_DAYS to 0
    turns this red, so the assertion is reached and does bite."""
    from datetime import datetime, timedelta, timezone
    from email.utils import format_datetime

    from newslens import config

    recent = format_datetime(datetime.now(timezone.utc) - timedelta(days=1))
    fake_api.add_route(
        "/live.xml",
        body=(
            b'<?xml version="1.0"?><rss version="2.0"><channel><title>Live</title>'
            b"<item><title>Today</title><link>https://live.invalid/1</link>"
            b"<pubDate>" + recent.encode() + b"</pubDate></item>"
            b"</channel></rss>"
        ),
    )
    cfg = config.SourcesConfig(
        sources=[mk_source(name="Live Wire", url=fake_api.base_url + "/live.xml")],
        interests_broad=["x"],
    )
    report = ingest.run_ingest(con=migrated_con, cfg=cfg, with_discovery=False)
    assert not any("days old" in w for w in report.warnings)


def test_doctor_flags_a_frozen_feed_from_the_bytes_it_already_fetched():
    """One round trip, two verdicts: the staleness read rides the same prefix
    the shape sniff fetched. No second fetch was added to the doctor."""
    body = (
        b'<?xml version="1.0"?><rss version="2.0"><channel><title>Frozen</title>'
        b"<item><title>Old</title><link>https://frozen.invalid/1</link>"
        b"<pubDate>Tue, 25 Apr 2023 17:44:36 GMT</pubDate></item>"
        b"</channel></rss>"
    )
    result = doctor._feed_age_result("CNN", body)
    assert result is not None and result.status == doctor.WARN
    assert "2023-04-25" in result.text


def test_doctor_says_nothing_about_a_healthy_feed():
    """Silent when healthy, by design: a doctor that adds 65 rows of 'this
    feed is fine' buries the rows that matter — and this is what keeps the
    existing check_feed_urls pins passing unchanged."""
    from datetime import datetime, timedelta, timezone
    from email.utils import format_datetime

    recent = format_datetime(datetime.now(timezone.utc) - timedelta(days=1))
    body = (
        b'<?xml version="1.0"?><rss version="2.0"><channel><title>Live</title>'
        b"<item><title>Today</title><link>https://live.invalid/1</link>"
        b"<pubDate>" + recent.encode() + b"</pubDate></item>"
        b"</channel></rss>"
    )
    assert doctor._feed_age_result("STAT News", body) is None


def test_doctor_staleness_never_turns_a_reachable_feed_into_a_failure():
    """This verdict rides a fetch that already produced its own PASS. A parse
    hiccup here must degrade to silence, never to a doctor FAIL."""
    assert doctor._feed_age_result("Garbage", b"\xff\xfe not a feed at all") is None
    assert doctor._feed_age_result("Empty", b"") is None
