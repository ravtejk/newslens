"""Deterministic ranking layer: the two principal amendments (2026-07-04),
the override contract, corroboration labels, and archive-before-overwrite.

Amendment A — bounded followed boost: never a guaranteed slot, generic
mechanism, excluded from the override pool.
Amendment B — recency window: min(since last briefing, 14d), own-date row
excluded on re-rank, honesty line whenever history < window.

No network anywhere in this file; select_slots/corroborate run on plain
dicts, window tests on scratch DBs, the end-to-end run on the fake server.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from newslens import config, ranking

NOW = datetime(2026, 7, 4, 12, 0, 0, tzinfo=timezone.utc)


def item(id, outlet, source_type="rss", wire=0):
    return {
        "id": id,
        "outlet": outlet,
        "source_type": source_type,
        "wire_syndication_flag": wire,
    }


def cluster(ids, title="Story", tags=(), memory=(), impact=5, reason="Reason here"):
    return {
        "story_title": title,
        "summary": "Summary.",
        "item_ids": list(ids),
        "matched_tags": [dict(t) for t in tags],
        "matched_memory": list(memory),
        "world_impact": impact,
        "world_impact_reason": reason,
    }


TOPIC = ({"name": "AI regulation", "level": "topic"},)
DOMAIN = ({"name": "economy", "level": "domain"},)


# --- Amendment A: bounded followed-analyst boost ------------------------------------

def test_followed_boost_constant_is_bounded_below_topic_weight():
    assert ranking.FOLLOWED_BOOST < ranking.TOPIC_WEIGHT
    assert ranking.FOLLOWED_BOOST == 0.35
    assert ranking.PERSONAL_SHARE == 0.55
    assert ranking.RECENCY_CAP_DAYS == 14
    assert ranking.OVERRIDE_THRESHOLD == 8
    assert ranking.MAX_SLOTS == 5


def test_followed_only_at_world_10_loses_to_topic_match_at_world_3():
    """THE amendment-A invariant, verbatim from the principal's ruling."""
    followed_c = cluster([1], title="Followed hot take", impact=10)
    topic_c = cluster([2], title="Topic story", tags=TOPIC, impact=3)
    items = {1: item(1, "Followed Blog"), 2: item(2, "Reuters-ish Outlet")}
    slots, meta = ranking.select_slots(
        [followed_c, topic_c], items, followed_outlets={"Followed Blog"}
    )
    assert slots[0].story_title == "Topic story"
    assert slots[0].combined_score == pytest.approx(0.685)
    assert slots[1].story_title == "Followed hot take"
    assert slots[1].combined_score == pytest.approx(0.6425)
    assert slots[1].personal_score == pytest.approx(0.35)


def test_followed_content_never_gets_a_guaranteed_slot():
    """Five stronger topic clusters fill the budget; the followed-only cluster
    is simply outranked — no followed=>slot branch exists to rescue it."""
    clusters = [
        cluster([i], title=f"Topic {i}", tags=TOPIC, impact=4 + i) for i in range(1, 6)
    ]
    clusters.append(cluster([9], title="Followed only", impact=10))
    items = {i: item(i, f"Outlet {i}") for i in range(1, 6)}
    items[9] = item(9, "Followed Blog")
    slots, _ = ranking.select_slots(clusters, items, followed_outlets={"Followed Blog"})
    assert len(slots) == 5
    assert all(s.story_title != "Followed only" for s in slots)


def test_followed_clusters_are_excluded_from_the_override_pool():
    """Followed content carries a personal signal, so it is never override
    material — even at world impact 10 with zero tag matches."""
    followed_c = cluster([1], title="Followed only", impact=10)
    items = {1: item(1, "Followed Blog")}
    slots, meta = ranking.select_slots([followed_c], items, {"Followed Blog"})
    assert meta["override"]["pool_size"] == 0
    assert meta["override"]["fired"] is False
    assert len(slots) == 1 and slots[0].override is False  # slotted as a primary
    assert slots[0].followed_analyst is True


def test_followed_boost_is_additive_and_capped_at_one():
    topic_followed = cluster([1], tags=TOPIC)
    assert ranking.personal_score(topic_followed, followed=True) == 1.0  # min(1.35, 1)
    domain_followed = cluster([1], tags=DOMAIN)
    assert ranking.personal_score(domain_followed, followed=True) == pytest.approx(0.85)


def test_followed_mechanism_is_generic_any_outlet_name_works():
    c = cluster([1], impact=5)
    items = {1: item(1, "Some Local Paper Nobody Hardcoded")}
    slots, _ = ranking.select_slots([c], items, {"Some Local Paper Nobody Hardcoded"})
    assert slots[0].followed_analyst is True
    assert slots[0].personal_score == pytest.approx(0.35)


def test_config_followed_analyst_flag_parses_on_any_source(tmp_path):
    p = tmp_path / "sources.yaml"
    p.write_text(
        "sources:\n"
        "  - name: Any Random Feed\n"
        "    rss_url: https://any.example/feed\n"
        "    followed_analyst: true\n"
        "  - name: Normal Feed\n"
        "    rss_url: https://n.example/feed\n",
        encoding="utf-8",
    )
    cfg = config.load_sources(p)
    assert cfg.problems == []
    assert [s.name for s in cfg.followed_analyst_sources] == ["Any Random Feed"]


def test_config_followed_analyst_must_be_boolean(tmp_path):
    p = tmp_path / "sources.yaml"
    p.write_text(
        "sources:\n"
        "  - name: X\n"
        "    rss_url: https://x.example/feed\n"
        "    followed_analyst: \"yes\"\n",
        encoding="utf-8",
    )
    cfg = config.load_sources(p)
    assert any("`followed_analyst` must be true or false" in pr for pr in cfg.problems)


# --- the override contract ------------------------------------------------------------

def test_override_pool_is_zero_personal_signal_only():
    clusters = [
        cluster([1], title="Topic", tags=TOPIC, impact=9),
        cluster([2], title="Domain", tags=DOMAIN, impact=9),
        cluster([3], title="Memory", memory=["chip export controls"], impact=9),
        cluster([4], title="Zero", impact=9),
    ]
    items = {i: item(i, f"O{i}") for i in range(1, 5)}
    # A6 default (steering OFF): the memory-matched cluster carries no
    # personal signal either -> pool of TWO zero-signal clusters.
    slots, meta = ranking.select_slots(clusters, items, followed_outlets=set())
    assert meta["override"]["pool_size"] == 2  # "Memory" + "Zero"
    assert meta["override"]["fired"] is True
    # With steering ON, the M3/M4 semantics return: only "Zero" qualifies.
    _, meta_on = ranking.select_slots(
        clusters, items, followed_outlets=set(), memory_steers=True
    )
    assert meta_on["override"]["pool_size"] == 1
    assert meta_on["override"]["story"] == "Zero"


def test_override_bar_is_8_and_the_slot_may_stay_empty():
    clusters = [
        cluster([1], title="Topic", tags=TOPIC, impact=5),
        cluster([2], title="Zero at 7", impact=7),
    ]
    items = {1: item(1, "A"), 2: item(2, "B")}
    slots, meta = ranking.select_slots(clusters, items, set())
    assert meta["override"]["pool_size"] == 1
    assert meta["override"]["fired"] is False
    assert len(slots) == 1  # a quiet day: unfilled slots are normal, not failure
    assert all(not s.override for s in slots)


def test_override_cap_is_one_even_with_two_qualifying_zero_clusters():
    clusters = [
        cluster([1], title="Zero 9", impact=9),
        cluster([2], title="Zero 10", impact=10),
    ]
    items = {1: item(1, "A"), 2: item(2, "B")}
    slots, meta = ranking.select_slots(clusters, items, set())
    assert len(slots) == 1  # cap 1: the second zero-cluster is NOT slotted
    assert slots[0].story_title == "Zero 10"  # highest world impact wins the slot
    assert slots[0].override is True


def test_override_label_carries_prefix_and_reason_and_only_on_the_override():
    clusters = [
        cluster([1], title="Topic", tags=TOPIC, impact=5),
        cluster([2], title="Zero big", impact=9, reason="Global systemic thing"),
    ]
    items = {1: item(1, "A"), 2: item(2, "B")}
    slots, _ = ranking.select_slots(clusters, items, set())
    override_slots = [s for s in slots if s.override]
    assert len(override_slots) == 1
    assert override_slots[0].override_label == (
        ranking.OVERRIDE_LABEL_PREFIX + "Global systemic thing."
    )
    assert all(s.override_label is None for s in slots if not s.override)


def test_override_consumes_one_of_the_five_slots():
    clusters = [
        cluster([i], title=f"T{i}", tags=TOPIC, impact=5) for i in range(1, 7)
    ]
    clusters.append(cluster([9], title="Zero 9", impact=9))
    items = {i: item(i, f"O{i}") for i in list(range(1, 7)) + [9]}
    slots, _ = ranking.select_slots(clusters, items, set())
    assert len(slots) == 5
    assert sum(1 for s in slots if s.override) == 1  # 4 primaries + 1 override


# --- corroboration ----------------------------------------------------------------------

def test_corroborate_counts_distinct_named_outlets():
    count, label, wire, named = ranking.corroborate(
        [item(1, "A"), item(2, "B"), item(3, "C"), item(4, "A")]
    )
    assert count == 3
    assert label == "Reported by 3 named outlets"
    assert named == ["A", "B", "C"]


def test_corroborate_singular_for_one_outlet():
    count, label, _, _ = ranking.corroborate([item(1, "Solo")])
    assert count == 1 and label == "Reported by 1 named outlet"


def test_corroborate_excludes_wire_and_says_so_in_the_label():
    count, label, wire, named = ranking.corroborate(
        [item(1, "Named"), item(2, "Yahoo Finance", wire=1), item(3, "Investing", wire=1)]
    )
    assert count == 1 and wire == 2
    assert "Reported by 1 named outlet" in label
    assert "(plus 2 wire-syndicated item(s), excluded from the count)" in label


def test_corroborate_sonar_items_are_never_named_outlets():
    count, label, _, named = ranking.corroborate(
        [item(1, "elsewhere.example", source_type="sonar"),
         item(2, "other.example", source_type="sonar")]
    )
    assert count == 0 and named == []
    assert label.startswith("Sourced via wire syndication or discovery only")
    assert "treat as a single source" in label


def test_corroborate_zero_named_floor_label_with_wire_suffix():
    _, label, _, _ = ranking.corroborate([item(1, "Wire Co", wire=1)])
    assert label.startswith("Sourced via wire syndication or discovery only")
    assert "excluded from the count" in label


# --- Amendment B: recency window ----------------------------------------------------------

def _add_briefing(con, date, generated_at):
    con.execute(
        "INSERT INTO briefings (date, generated_at) VALUES (?, ?)",
        (date, generated_at),
    )
    con.commit()


def test_first_run_uses_the_full_cap(migrated_con):
    w = ranking.candidate_window(migrated_con, "2026-07-04", now_utc=NOW)
    assert w["days"] == 14.0
    assert w["basis"] == "first briefing — full cap"


def test_window_since_last_briefing_when_newer_than_cap(migrated_con):
    _add_briefing(migrated_con, "2026-07-01", "2026-07-01T12:00:00.000Z")
    w = ranking.candidate_window(migrated_con, "2026-07-04", now_utc=NOW)
    assert w["days"] == pytest.approx(3.0)
    assert w["basis"] == "since your last briefing"


def test_window_caps_at_14_days_when_last_briefing_is_older(migrated_con):
    _add_briefing(migrated_con, "2026-06-10", "2026-06-10T12:00:00.000Z")
    w = ranking.candidate_window(migrated_con, "2026-07-04", now_utc=NOW)
    assert w["days"] == 14.0
    assert w["basis"] == "14d cap (last briefing is older)"


def test_rerank_excludes_the_target_dates_own_row(migrated_con):
    """Idempotent re-rank must window from the PREVIOUS briefing, not from
    its own prior version minutes earlier."""
    _add_briefing(migrated_con, "2026-07-01", "2026-07-01T12:00:00.000Z")
    _add_briefing(migrated_con, "2026-07-04", "2026-07-04T11:59:00.000Z")  # just ran
    w = ranking.candidate_window(migrated_con, "2026-07-04", now_utc=NOW)
    assert w["days"] == pytest.approx(3.0)  # anchored on 07-01, not 11:59
    assert w["basis"] == "since your last briefing"


def test_ingested_history_days_empty_db_is_zero(migrated_con):
    assert ranking.ingested_history_days(migrated_con, now_utc=NOW) == 0.0


def test_ingested_history_days_measures_oldest_fetch(migrated_con):
    migrated_con.execute(
        "INSERT INTO source_items (source_type, outlet, url, title, fetched_at)"
        " VALUES ('rss', 'A', 'https://a.example/1', 't', '2026-07-02T12:00:00.000Z')"
    )
    migrated_con.commit()
    assert ranking.ingested_history_days(migrated_con, now_utc=NOW) == pytest.approx(2.0)


def test_gather_items_respects_window_and_cap(migrated_con, monkeypatch):
    for i, fetched in enumerate(
        ["2026-07-03T12:00:00.000Z", "2026-07-02T12:00:00.000Z", "2026-06-01T12:00:00.000Z"],
        start=1,
    ):
        migrated_con.execute(
            "INSERT INTO source_items (id, source_type, outlet, url, title, fetched_at)"
            " VALUES (?, 'rss', 'A', ?, 't', ?)",
            (i, f"https://a.example/{i}", fetched),
        )
    migrated_con.commit()
    rows = ranking.gather_items(migrated_con, "2026-06-20T12:00:00")
    assert [r["id"] for r in rows] == [1, 2]  # newest first, out-of-window excluded
    monkeypatch.setattr(ranking, "MAX_INPUT_ITEMS", 1)
    rows = ranking.gather_items(migrated_con, "2026-06-20T12:00:00")
    assert [r["id"] for r in rows] == [1]


# --- end-to-end: persist, archive-before-overwrite, honesty line ---------------------------

def _seed_and_route(con, fake_api, monkeypatch):
    import time as _time

    monkeypatch.setattr(
        ranking, "OPENAI_CHAT_URL", fake_api.base_url + "/chat/completions"
    )
    monkeypatch.setattr(_time, "sleep", lambda s: None)
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    for i, (outlet, wire) in enumerate(
        [("Outlet A", 0), ("Outlet B", 0), ("Wire Co", 1)], start=1
    ):
        con.execute(
            "INSERT INTO source_items (id, source_type, outlet, url, title,"
            " fetched_at, wire_syndication_flag) VALUES (?, 'rss', ?, ?, ?, ?, ?)",
            (i, outlet, f"https://{i}.example/s", f"Story {i}", now, wire),
        )
    con.commit()
    payload = {
        "clusters": [
            {
                "story_title": "Tagged story",
                "summary": "Matched your tags.",
                "item_ids": [1, 2],
                "matched_tags": [{"name": "AI regulation", "level": "topic"}],
                "matched_memory": [],
                "world_impact": 5,
                "world_impact_reason": "Sector-wide effect",
            },
            {
                "story_title": "Zero-match shock",
                "summary": "No tag matched.",
                "item_ids": [3],
                "matched_tags": [],
                "matched_memory": [],
                "world_impact": 9,
                "world_impact_reason": "Global systemic consequence",
            },
        ]
    }
    body = json.dumps(
        {
            "choices": [{"message": {"content": json.dumps(payload)}}],
            "usage": {"prompt_tokens": 1000, "completion_tokens": 200},
        }
    ).encode("utf-8")
    fake_api.add_route("/chat/completions", status=200, body=body,
                       content_type="application/json")


def rank_cfg():
    return config.SourcesConfig(
        sources=[config.Source(name="Outlet A", rss_url="https://a.example/f")],
        interests_broad=["economy"],
        interests_granular=["AI regulation"],
    )


def test_end_to_end_rank_persists_archives_and_tells_the_truth(
    migrated_con, fake_api, monkeypatch
):
    _seed_and_route(migrated_con, fake_api, monkeypatch)
    env = {"OPENAI_API_KEY": "sk-qa-fake"}

    report = ranking.run_rank(date="2026-07-04", con=migrated_con, cfg=rank_cfg(), env=env)

    # Slots: tagged primary first, zero-match override second (labeled).
    assert [s.story_title for s in report.slots] == ["Tagged story", "Zero-match shock"]
    assert report.slots[1].override is True
    assert report.slots[1].override_label.startswith(ranking.OVERRIDE_LABEL_PREFIX)
    assert report.override_fired and report.override_pool_size == 1
    # Corroboration: two named outlets on the tagged story; wire item excluded.
    assert report.slots[0].corroboration_label == "Reported by 2 named outlets"
    assert report.slots[1].wire_items_excluded == 1

    # Honesty line: brand-new corpus, 14d window -> warning present.
    assert any(
        "candidate window:" in w and "ingested history available:" in w
        for w in report.warnings
    )

    # Persistence: briefings row with caveat stored at the contract path.
    row = migrated_con.execute(
        "SELECT * FROM briefings WHERE date = '2026-07-04'"
    ).fetchone()
    stored = json.loads(row["corroboration_labels"])
    assert stored["standing_caveat"] == ranking.CORROBORATION_CAVEAT
    assert len(json.loads(row["story_slots"])) == 2
    # Pinned against the RANK seam constants (up-tiered to gpt-4o 2026-07-05)
    # so the next model change updates one place, not this test.
    expected_usd = (
        1000 / 1e6 * ranking.RANK_USD_PER_MTOK_IN
        + 200 / 1e6 * ranking.RANK_USD_PER_MTOK_OUT
    )
    assert json.loads(row["token_cost"])["total_usd"] == pytest.approx(expected_usd)
    runs = migrated_con.execute("SELECT meta FROM ranking_runs").fetchall()
    assert len(runs) == 1
    meta = json.loads(runs[0]["meta"])
    assert meta["status"] == "ok" and meta["override"]["fired"] is True

    # Re-rank the same date: archive-before-overwrite (ADR-0001, live now).
    report2 = ranking.run_rank(
        date="2026-07-04", con=migrated_con, cfg=rank_cfg(), env=env
    )
    assert report2.window_basis == "first briefing — full cap"  # own row excluded
    briefings = migrated_con.execute("SELECT id FROM briefings").fetchall()
    assert len(briefings) == 1  # updated in place, same row
    history = migrated_con.execute(
        "SELECT date, story_slots FROM briefings_history"
    ).fetchall()
    assert len(history) == 1  # prior version archived FIRST
    assert history[0]["date"] == "2026-07-04"
    assert len(json.loads(history[0]["story_slots"])) == 2
    assert (
        migrated_con.execute("SELECT COUNT(*) FROM ranking_runs").fetchone()[0] == 2
    )
