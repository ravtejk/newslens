"""Schema constraints from migration 0001 (spec §B; ADR-0001).

Every constraint the implementer claims is enforced structurally gets a
negative test here: closed source_type enum, (url, fetch-day) dedupe that
still allows later-day re-fetches, UNIQUE(briefings.date), json_valid CHECKs,
memory.status enum, FK enforcement, and briefings_history append-only via
triggers (INSERT works; UPDATE/DELETE abort).
"""

from __future__ import annotations

import sqlite3

import pytest

from newslens import db


def insert_source(con, **overrides):
    row = {
        "source_type": "rss",
        "outlet": "Outlet A",
        "url": "https://a.example/story-1",
        "title": "A story",
        "fetched_at": "2026-07-02T10:00:00.000Z",
    }
    row.update(overrides)
    con.execute(
        "INSERT INTO source_items (source_type, outlet, url, title, fetched_at)"
        " VALUES (:source_type, :outlet, :url, :title, :fetched_at)",
        row,
    )


# --- source_items ------------------------------------------------------------

@pytest.mark.parametrize("kind", ["rss", "sonar"])
def test_source_type_accepts_the_two_spec_values(migrated_con, kind):
    insert_source(migrated_con, source_type=kind, url=f"https://x.example/{kind}")


@pytest.mark.parametrize("kind", ["gnews", "RSS", "Sonar", "", "web"])
def test_source_type_is_a_closed_enum(migrated_con, kind):
    """Adding a source kind must be a visible migration, never a silent string
    (ADR-0001) — including case variants."""
    with pytest.raises(sqlite3.IntegrityError):
        insert_source(migrated_con, source_type=kind)


@pytest.mark.parametrize("flag", [2, -1, "maybe"])
def test_wire_syndication_flag_is_boolean_int(migrated_con, flag):
    with pytest.raises(sqlite3.IntegrityError):
        migrated_con.execute(
            "INSERT INTO source_items (source_type, outlet, url, title, wire_syndication_flag)"
            " VALUES ('rss', 'A', 'https://a.example/w', 't', ?)",
            (flag,),
        )


def test_same_url_same_fetch_day_is_rejected(migrated_con):
    insert_source(migrated_con, fetched_at="2026-07-02T10:00:00.000Z")
    with pytest.raises(sqlite3.IntegrityError) as excinfo:
        insert_source(migrated_con, fetched_at="2026-07-02T23:59:59.000Z")
    assert "idx_source_items_url_fetch_day" in str(excinfo.value)


def test_same_url_on_a_later_day_is_allowed(migrated_con):
    """Faithfulness by construction: a later-day re-fetch is a NEW snapshot row,
    so day-1 briefing references stay pinned to the day-1 snapshot."""
    insert_source(migrated_con, fetched_at="2026-07-02T10:00:00.000Z")
    insert_source(migrated_con, fetched_at="2026-07-03T00:00:01.000Z")
    count = migrated_con.execute("SELECT COUNT(*) FROM source_items").fetchone()[0]
    assert count == 2


def test_different_urls_same_day_are_allowed(migrated_con):
    insert_source(migrated_con, url="https://a.example/1")
    insert_source(migrated_con, url="https://a.example/2")


def test_fetched_at_defaults_to_utc_iso8601(migrated_con):
    migrated_con.execute(
        "INSERT INTO source_items (source_type, outlet, url, title)"
        " VALUES ('rss', 'A', 'https://a.example/d', 't')"
    )
    val = migrated_con.execute("SELECT fetched_at FROM source_items").fetchone()[0]
    assert val.endswith("Z") and val[4] == "-" and "T" in val


# --- briefings ---------------------------------------------------------------

def test_briefings_date_is_unique(migrated_con):
    migrated_con.execute("INSERT INTO briefings (date) VALUES ('2026-07-02')")
    with pytest.raises(sqlite3.IntegrityError):
        migrated_con.execute("INSERT INTO briefings (date) VALUES ('2026-07-02')")


def test_briefings_minimal_insert_gets_sane_defaults(migrated_con):
    migrated_con.execute("INSERT INTO briefings (date) VALUES ('2026-07-02')")
    row = migrated_con.execute("SELECT * FROM briefings").fetchone()
    assert row["story_slots"] == "[]"
    assert row["corroboration_labels"] == "[]"
    assert row["token_cost"] is None
    assert row["generated_at"].endswith("Z")


@pytest.mark.parametrize(
    "column, bad",
    [
        ("story_slots", "not json"),
        ("story_slots", "{truncated"),
        ("corroboration_labels", "also not json"),
        ("token_cost", "cheap"),
    ],
)
def test_briefings_json_columns_reject_non_json(migrated_con, column, bad):
    """DB-level backstop: malformed LLM output cannot land even if app-level
    validation regresses (ENGINEERING.md structured-output rule)."""
    with pytest.raises(sqlite3.IntegrityError):
        migrated_con.execute(
            f"INSERT INTO briefings (date, {column}) VALUES ('2026-07-02', ?)",
            (bad,),
        )


def test_briefings_json_columns_accept_valid_json_and_null_cost(migrated_con):
    migrated_con.execute(
        "INSERT INTO briefings (date, story_slots, corroboration_labels, token_cost)"
        " VALUES ('2026-07-02', ?, ?, ?)",
        ('[{"slot": 1, "source_item_ids": [1, 2]}]', '["Reported by 2 named outlets"]',
         '{"total_usd": 0.12}'),
    )
    migrated_con.execute("INSERT INTO briefings (date) VALUES ('2026-07-03')")  # NULL cost


# --- memory ------------------------------------------------------------------

@pytest.mark.parametrize("status", ["active", "stale", "dismissed"])
def test_memory_status_accepts_spec_values(migrated_con, status):
    migrated_con.execute(
        "INSERT INTO memory (topic, status) VALUES ('rates', ?)", (status,)
    )


@pytest.mark.parametrize("status", ["archived", "Active", ""])
def test_memory_status_is_a_closed_enum(migrated_con, status):
    with pytest.raises(sqlite3.IntegrityError):
        migrated_con.execute(
            "INSERT INTO memory (topic, status) VALUES ('rates', ?)", (status,)
        )


def test_memory_status_defaults_to_active(migrated_con):
    migrated_con.execute("INSERT INTO memory (topic) VALUES ('chips')")
    row = migrated_con.execute("SELECT status FROM memory").fetchone()
    assert row["status"] == "active"


def test_memory_fk_rejects_nonexistent_briefing(migrated_con):
    with pytest.raises(sqlite3.IntegrityError):
        migrated_con.execute(
            "INSERT INTO memory (topic, last_referenced_briefing_id) VALUES ('x', 999)"
        )


def test_memory_fk_allows_null_and_real_briefing(migrated_con):
    migrated_con.execute("INSERT INTO memory (topic) VALUES ('no-ref-yet')")
    migrated_con.execute("INSERT INTO briefings (date) VALUES ('2026-07-02')")
    bid = migrated_con.execute("SELECT id FROM briefings").fetchone()["id"]
    migrated_con.execute(
        "INSERT INTO memory (topic, last_referenced_briefing_id) VALUES ('ref', ?)",
        (bid,),
    )


# --- briefings_history: append-only ------------------------------------------

def _seed_history(con):
    con.execute("INSERT INTO briefings (date) VALUES ('2026-07-02')")
    bid = con.execute("SELECT id FROM briefings").fetchone()["id"]
    con.execute(
        "INSERT INTO briefings_history (briefing_id, date, narrative_text)"
        " VALUES (?, '2026-07-02', 'v1 text')",
        (bid,),
    )
    return bid


def test_history_insert_works(migrated_con):
    _seed_history(migrated_con)
    count = migrated_con.execute("SELECT COUNT(*) FROM briefings_history").fetchone()[0]
    assert count == 1


def test_history_update_aborts(migrated_con):
    _seed_history(migrated_con)
    with pytest.raises(sqlite3.DatabaseError) as excinfo:
        migrated_con.execute("UPDATE briefings_history SET narrative_text = 'rewritten'")
    assert "append-only" in str(excinfo.value)
    row = migrated_con.execute("SELECT narrative_text FROM briefings_history").fetchone()
    assert row["narrative_text"] == "v1 text"  # unchanged


def test_history_delete_aborts(migrated_con):
    _seed_history(migrated_con)
    with pytest.raises(sqlite3.DatabaseError) as excinfo:
        migrated_con.execute("DELETE FROM briefings_history")
    assert "append-only" in str(excinfo.value)
    count = migrated_con.execute("SELECT COUNT(*) FROM briefings_history").fetchone()[0]
    assert count == 1


def test_history_fk_rejects_nonexistent_briefing(migrated_con):
    with pytest.raises(sqlite3.IntegrityError):
        migrated_con.execute(
            "INSERT INTO briefings_history (briefing_id, date) VALUES (999, '2026-07-02')"
        )


def test_history_pins_its_parent_briefing(migrated_con):
    """Deleting a briefing that has archived history is FK-blocked — history
    stays diff-able against the row it archived (Onna's requirement, held
    structurally)."""
    _seed_history(migrated_con)
    with pytest.raises(sqlite3.IntegrityError):
        migrated_con.execute("DELETE FROM briefings")


def test_fk_enforcement_is_per_connection_so_db_connect_is_load_bearing(tmp_path):
    """Documents the risk called out in db.py itself: a raw sqlite3.connect
    (bypassing db.connect) does NOT enforce FKs. Milestone 2+ code must come
    through db.connect — this test is the tripwire that keeps that claim true.
    """
    db_path = tmp_path / "raw.db"
    db.migrate(db_path=db_path)
    raw = sqlite3.connect(str(db_path))
    try:
        # Orphan insert SUCCEEDS on a raw connection: PRAGMA is per-connection.
        raw.execute(
            "INSERT INTO briefings_history (briefing_id, date) VALUES (999, '2026-07-02')"
        )
    finally:
        raw.rollback()
        raw.close()
