"""memory.md ⇄ SQLite sync — the transparency surface (ADR-0005 §1 as amended
by ADR-0006; lifecycle v2).

The one unforgivable failure here is silence: hand edits must apply loudly,
unparseable files must stop the run with line numbers and an UNTOUCHED file,
and every automatic transition must surface dated. All tests run on scratch
DBs and sandboxed memory.md paths — the real file is live principal state.

Self-contained acceptance (Option A): any red test in this file states its
own fix contract in the docstring.
"""

from __future__ import annotations

import os
import shutil
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from newslens import db, memory, paths

from conftest import PROTOTYPE_ROOT

NOW = datetime(2026, 7, 4, 12, 0, 0, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def frozen_clock(monkeypatch):
    """Every date-relative seed in this file is written against the frozen
    NOW, so the module's clock must be frozen too — otherwise sync_memory's
    internal dormancy pass drifts against the seeds (the suite started
    failing 14 real days after add_row's default created_at)."""
    monkeypatch.setattr(memory, "_utc_now", lambda: NOW)


@pytest.fixture
def memfile(tmp_path, monkeypatch):
    f = tmp_path / "memory.md"
    monkeypatch.setattr(paths, "MEMORY_FILE", f)
    return f


def iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%S.000Z")


def add_row(
    con,
    topic,
    status="active",
    note=None,
    changed=None,
    ref=None,
    created=None,
    updated=None,
):
    con.execute(
        "INSERT INTO memory (topic, status, principal_note, status_changed_at,"
        " last_referenced_briefing_id, created_at, updated_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            topic, status, note, changed, ref,
            created or iso(NOW - timedelta(days=1)),
            updated or iso(NOW - timedelta(days=1)),
        ),
    )
    con.commit()


def add_briefing(con, date, generated_at):
    cur = con.execute(
        "INSERT INTO briefings (date, generated_at) VALUES (?, ?)",
        (date, generated_at),
    )
    con.commit()
    return cur.lastrowid


def statuses(con):
    return {
        r["topic"]: r["status"]
        for r in con.execute("SELECT topic, status FROM memory")
    }


MINIMAL_FILE = """# NewsLens memory
## Active threads
- {line}
## Inactive
"""


# --- seeding guard ---------------------------------------------------------------

def test_first_run_seeds_the_14_taxonomy_threads(migrated_con, memfile):
    assert len(memory.SEED_THREADS) == 14
    result = memory.sync_memory(migrated_con)
    assert result.seeded == 14
    assert memfile.exists()
    text = memfile.read_text(encoding="utf-8")
    assert "## Active threads" in text and "- Iran War" in text
    # Second sync: no re-seed, no duplicates.
    again = memory.sync_memory(migrated_con)
    assert again.seeded == 0
    count = migrated_con.execute("SELECT COUNT(*) FROM memory").fetchone()[0]
    assert count == 14


def test_no_reseed_when_table_has_rows_even_if_file_deleted(migrated_con, memfile):
    """A deleted file regenerates from the DB — it can NEVER resurrect
    dismissed threads via a fresh seed."""
    add_row(migrated_con, "Old Thread", status="dismissed_user",
            changed=iso(NOW - timedelta(days=2)))
    assert not memfile.exists()
    result = memory.sync_memory(migrated_con)
    assert result.seeded == 0
    assert statuses(migrated_con) == {"Old Thread": "dismissed_user"}
    assert "(dismissed by you" in memfile.read_text(encoding="utf-8")


def test_no_seed_when_file_exists_but_table_is_empty(migrated_con, memfile):
    memfile.write_text(MINIMAL_FILE.format(line="My Own Topic"), encoding="utf-8")
    result = memory.sync_memory(migrated_con)
    assert result.seeded == 0
    assert result.added == ["My Own Topic"]


# --- file-wins semantics ------------------------------------------------------------

def test_deleted_line_is_dismissal_with_audit_row(migrated_con, memfile):
    add_row(migrated_con, "Keep Me")
    add_row(migrated_con, "Delete Me")
    memfile.write_text(MINIMAL_FILE.format(line="Keep Me"), encoding="utf-8")
    result = memory.sync_memory(migrated_con)
    assert result.dismissed_by_deletion == ["Delete Me"]
    row = migrated_con.execute(
        "SELECT status, status_changed_at FROM memory WHERE topic = 'Delete Me'"
    ).fetchone()
    assert row is not None  # audit row kept, never hard-deleted
    assert row["status"] == "dismissed_user"
    assert row["status_changed_at"] is not None
    assert "(dismissed by you" in memfile.read_text(encoding="utf-8")


def test_hand_edited_note_is_honored_and_written_back(migrated_con, memfile):
    add_row(migrated_con, "Iran War", note="old note")
    memfile.write_text(
        MINIMAL_FILE.format(line="Iran War — fresh principal wording"),
        encoding="utf-8",
    )
    result = memory.sync_memory(migrated_con)
    assert result.notes_updated == ["Iran War"]
    row = migrated_con.execute(
        "SELECT principal_note FROM memory WHERE topic = 'Iran War'"
    ).fetchone()
    assert row["principal_note"] == "fresh principal wording"
    assert "- Iran War — fresh principal wording" in memfile.read_text(encoding="utf-8")


def test_added_line_creates_an_active_row(migrated_con, memfile):
    add_row(migrated_con, "Existing")
    memfile.write_text(
        "# x\n## Active threads\n- Existing\n- Brand New — with note\n## Inactive\n",
        encoding="utf-8",
    )
    result = memory.sync_memory(migrated_con)
    assert result.added == ["Brand New"]
    assert statuses(migrated_con)["Brand New"] == "active"


def test_bare_line_under_inactive_is_an_explicit_dismissal(migrated_con, memfile):
    add_row(migrated_con, "Pushed Down")
    memfile.write_text(
        "# x\n## Active threads\n## Inactive\n- Pushed Down\n", encoding="utf-8"
    )
    result = memory.sync_memory(migrated_con)
    assert result.status_changed == ["Pushed Down: active->dismissed_user"]
    assert statuses(migrated_con)["Pushed Down"] == "dismissed_user"


def test_rendered_dormant_line_round_trips_as_dormant(migrated_con, memfile):
    """The annotation IS the state: a canonical dormant line re-parsed must
    stay dormant — not resurrect as active, not demote to dismissed."""
    b1 = add_briefing(migrated_con, "2026-06-01", "2026-06-01T12:00:00.000Z")
    add_row(migrated_con, "Idle Thread", status="dormant",
            changed=iso(NOW - timedelta(days=3)), ref=b1)
    memfile.write_text(memory.render_file(migrated_con), encoding="utf-8")
    rendered = memfile.read_text(encoding="utf-8")
    assert "(dormant since 2026-07-01, last covered 2026-06-01)" in rendered
    result = memory.sync_memory(migrated_con)
    assert result.status_changed == [] and result.dismissed_by_deletion == []
    assert statuses(migrated_con)["Idle Thread"] == "dormant"


def test_dismissed_annotation_round_trips(migrated_con, memfile):
    add_row(migrated_con, "Done With", status="dismissed_user",
            changed=iso(NOW - timedelta(days=5)))
    memfile.write_text(memory.render_file(migrated_con), encoding="utf-8")
    result = memory.sync_memory(migrated_con)
    assert result.status_changed == []
    assert statuses(migrated_con)["Done With"] == "dismissed_user"


@pytest.mark.parametrize("start", ["dormant", "dismissed_user"])
def test_moving_a_line_back_to_active_revives_it(migrated_con, memfile, start):
    add_row(migrated_con, "Come Back", status=start,
            changed=iso(NOW - timedelta(days=5)))
    memfile.write_text(MINIMAL_FILE.format(line="Come Back"), encoding="utf-8")
    result = memory.sync_memory(migrated_con)
    assert result.status_changed == [f"Come Back: {start}->active"]
    assert statuses(migrated_con)["Come Back"] == "active"


def test_case_insensitive_line_matching_updates_not_duplicates(migrated_con, memfile):
    add_row(migrated_con, "Iran War")
    memfile.write_text(
        MINIMAL_FILE.format(line="iran war — lowercase edit"), encoding="utf-8"
    )
    result = memory.sync_memory(migrated_con)
    assert result.added == []  # matched the existing row
    # (Summary echoes the file's casing; the row keeps the canonical one.)
    assert [t.casefold() for t in result.notes_updated] == ["iran war"]
    count = migrated_con.execute("SELECT COUNT(*) FROM memory").fetchone()[0]
    assert count == 1
    topic = migrated_con.execute("SELECT topic FROM memory").fetchone()["topic"]
    assert topic == "Iran War"  # DB casing preserved; canonical rewrite emits it


def test_db_unique_index_rejects_case_variant_duplicates(migrated_con):
    """What migration 0005 exists for: the sync's line<->row matching is only
    unambiguous if the DB can't hold two case-variants of one topic."""
    add_row(migrated_con, "Iran War")
    with pytest.raises(sqlite3.IntegrityError):
        migrated_con.execute("INSERT INTO memory (topic) VALUES ('IRAN WAR')")


def test_rename_dismisses_old_and_starts_new(migrated_con, memfile):
    """Documented in the file header: renaming = dismiss old + add new."""
    add_row(migrated_con, "Old Name")
    memfile.write_text(MINIMAL_FILE.format(line="New Name"), encoding="utf-8")
    result = memory.sync_memory(migrated_con)
    assert result.added == ["New Name"]
    assert result.dismissed_by_deletion == ["Old Name"]


# --- loud parse failures: hard stop, line numbers, file untouched --------------------

def _assert_untouched(memfile, before, migrated_con, before_statuses):
    assert memfile.read_text(encoding="utf-8") == before  # file NOT rewritten
    assert statuses(migrated_con) == before_statuses      # DB NOT mutated


def test_unrecognized_line_is_a_line_numbered_hard_stop(migrated_con, memfile):
    add_row(migrated_con, "Existing")
    before_statuses = statuses(migrated_con)
    text = "# x\n## Active threads\n- Existing\nstray prose line here\n"
    memfile.write_text(text, encoding="utf-8")
    with pytest.raises(memory.MemorySyncError) as excinfo:
        memory.sync_memory(migrated_con)
    assert "line 4" in str(excinfo.value)
    assert "regenerate" in str(excinfo.value)  # the fix hint
    _assert_untouched(memfile, text, migrated_con, before_statuses)


def test_duplicate_topic_lines_are_a_hard_stop(migrated_con, memfile):
    text = (
        "# x\n## Active threads\n- Twice\n## Inactive\n- twice (dismissed by you 2026-07-01)\n"
    )
    memfile.write_text(text, encoding="utf-8")
    with pytest.raises(memory.MemorySyncError) as excinfo:
        memory.sync_memory(migrated_con)
    assert "duplicate topic" in str(excinfo.value)
    assert memfile.read_text(encoding="utf-8") == text


def test_v1_format_section_fails_loudly_with_regenerate_hint(migrated_con, memfile):
    """A leftover v1-format file (e.g. a '## Stale threads' section) must fail
    the v2 parser loudly — never be silently misread (ADR-0006 §6)."""
    text = "# x\n## Active threads\n- A\n## Stale threads\n- B\n"
    memfile.write_text(text, encoding="utf-8")
    with pytest.raises(memory.MemorySyncError) as excinfo:
        memory.sync_memory(migrated_con)
    msg = str(excinfo.value)
    assert "unknown section heading" in msg and "Stale threads" in msg
    assert "delete the file to regenerate" in msg
    assert memfile.read_text(encoding="utf-8") == text


def test_thread_line_before_any_section_is_a_hard_stop(migrated_con, memfile):
    memfile.write_text("# x\n- Orphan Line\n## Active threads\n", encoding="utf-8")
    with pytest.raises(memory.MemorySyncError) as excinfo:
        memory.sync_memory(migrated_con)
    assert "before any section heading" in str(excinfo.value)


def test_empty_topic_line_is_a_hard_stop(migrated_con, memfile):
    # "- *" strips to an empty topic (adjudicated: an em-dash-prefixed body
    # like "- — note" parses as a weird-but-round-trip-stable topic name and
    # is deliberately tolerated; genuinely EMPTY topics must stop the run).
    memfile.write_text("# x\n## Active threads\n- *\n", encoding="utf-8")
    with pytest.raises(memory.MemorySyncError) as excinfo:
        memory.sync_memory(migrated_con)
    assert "empty topic" in str(excinfo.value)


def test_unreadable_file_is_a_loud_stop(migrated_con, memfile):
    if os.geteuid() == 0:
        pytest.skip("running as root — chmod 000 is still readable")
    memfile.write_text(MINIMAL_FILE.format(line="A"), encoding="utf-8")
    memfile.chmod(0)
    try:
        with pytest.raises(memory.MemorySyncError) as excinfo:
            memory.sync_memory(migrated_con)
    finally:
        memfile.chmod(0o600)
    assert "not readable" in str(excinfo.value)


# --- dormancy clock --------------------------------------------------------------------

def test_unreferenced_15_days_goes_dormant_dated(migrated_con):
    add_row(migrated_con, "Idle", created=iso(NOW - timedelta(days=15)))
    went = memory.apply_dormancy(migrated_con, now_utc=NOW)
    assert went == ["Idle"]
    row = migrated_con.execute(
        "SELECT status, status_changed_at FROM memory WHERE topic='Idle'"
    ).fetchone()
    assert row["status"] == "dormant"
    assert row["status_changed_at"] == iso(NOW)


def test_reference_keeps_a_thread_alive(migrated_con):
    b = add_briefing(migrated_con, "2026-07-01", iso(NOW - timedelta(days=3)))
    add_row(migrated_con, "Covered", created=iso(NOW - timedelta(days=30)), ref=b)
    assert memory.apply_dormancy(migrated_con, now_utc=NOW) == []
    assert statuses(migrated_con)["Covered"] == "active"


def test_note_edits_do_not_reset_the_dormancy_clock(migrated_con):
    """ADR-0006 §2: referenced-ness is about briefings, not editing —
    updated_at moves on note edits and must not keep a thread active."""
    add_row(
        migrated_con, "Edited Lots",
        created=iso(NOW - timedelta(days=20)),
        updated=iso(NOW - timedelta(hours=1)),  # fresh note edit
    )
    assert memory.apply_dormancy(migrated_con, now_utc=NOW) == ["Edited Lots"]


def test_thirteen_days_is_still_active(migrated_con):
    add_row(migrated_con, "Recent", created=iso(NOW - timedelta(days=13)))
    assert memory.apply_dormancy(migrated_con, now_utc=NOW) == []


def test_dormancy_is_surfaced_in_sync_summary(migrated_con, memfile):
    add_row(migrated_con, "Fading", created=iso(NOW - timedelta(days=40)))
    result = memory.sync_memory(migrated_con)
    assert result.went_dormant == ["Fading"]
    lines = result.summary_lines()
    assert any("went dormant" in l and "Fading" in l and "auto-revive" in l for l in lines)
    assert "(dormant since" in memfile.read_text(encoding="utf-8")


# --- context + revival surfaces -----------------------------------------------------------

def test_active_context_cap_and_referenced_first_ordering(migrated_con):
    b1 = add_briefing(migrated_con, "2026-07-01", iso(NOW - timedelta(days=3)))
    b2 = add_briefing(migrated_con, "2026-07-02", iso(NOW - timedelta(days=2)))
    add_row(migrated_con, "Ref Newer", ref=b2)
    add_row(migrated_con, "Ref Older", ref=b1)
    for i in range(16):
        add_row(migrated_con, f"Unref {i:02d}",
                created=iso(NOW - timedelta(days=1)),
                updated=iso(NOW - timedelta(minutes=16 - i)))
    add_row(migrated_con, "Dormant One", status="dormant", changed=iso(NOW))
    add_row(migrated_con, "Gone", status="dismissed_user", changed=iso(NOW))
    ctx = memory.active_context(migrated_con)
    assert len(ctx) == memory.CONTEXT_CAP == 15
    assert ctx[0] == "Ref Newer" and ctx[1] == "Ref Older"  # referenced first
    assert "Dormant One" not in ctx and "Gone" not in ctx
    # Never-referenced follow, newest updated first:
    assert ctx[2] == "Unref 15"


def test_dormant_topics_never_includes_dismissed_user(migrated_con):
    add_row(migrated_con, "Dormant A", status="dormant", changed=iso(NOW))
    add_row(migrated_con, "Dismissed B", status="dismissed_user", changed=iso(NOW))
    assert memory.dormant_topics(migrated_con) == ["Dormant A"]


def test_revive_matched_captures_last_covered_before_update(migrated_con):
    b_old = add_briefing(migrated_con, "2026-06-20", iso(NOW - timedelta(days=14)))
    b_new = add_briefing(migrated_con, "2026-07-04", iso(NOW))
    add_row(migrated_con, "Sleeper", status="dormant",
            changed=iso(NOW - timedelta(days=2)), ref=b_old)
    revived = memory.revive_matched(migrated_con, b_new, ["sleeper"])  # case-insensitive
    assert revived == [{"topic": "Sleeper", "last_covered": "2026-06-20"}]
    row = migrated_con.execute(
        "SELECT status, last_referenced_briefing_id FROM memory WHERE topic='Sleeper'"
    ).fetchone()
    assert row["status"] == "active"
    assert row["last_referenced_briefing_id"] == b_new


def test_revive_matched_never_touches_dismissed_user_or_unknowns(migrated_con):
    b = add_briefing(migrated_con, "2026-07-04", iso(NOW))
    add_row(migrated_con, "Dismissed", status="dismissed_user",
            changed=iso(NOW - timedelta(days=2)))
    revived = memory.revive_matched(migrated_con, b, ["Dismissed", "Never Existed"])
    assert revived == []
    assert statuses(migrated_con)["Dismissed"] == "dismissed_user"


def test_update_references_skips_dismissed_user(migrated_con):
    b = add_briefing(migrated_con, "2026-07-04", iso(NOW))
    add_row(migrated_con, "Live One")
    add_row(migrated_con, "Dead One", status="dismissed_user", changed=iso(NOW))
    n = memory.update_references(migrated_con, b, ["live one", "Dead One"])
    assert n == 1
    rows = {
        r["topic"]: r["last_referenced_briefing_id"]
        for r in migrated_con.execute(
            "SELECT topic, last_referenced_briefing_id FROM memory"
        )
    }
    assert rows["Live One"] == b and rows["Dead One"] is None


def test_note_edit_moves_updated_at_only_not_status_changed_at(migrated_con, memfile):
    changed = iso(NOW - timedelta(days=6))
    add_row(migrated_con, "Annotated", changed=changed)
    memfile.write_text(
        MINIMAL_FILE.format(line="Annotated — a brand new note"), encoding="utf-8"
    )
    memory.sync_memory(migrated_con)
    row = migrated_con.execute(
        "SELECT status_changed_at, updated_at FROM memory WHERE topic='Annotated'"
    ).fetchone()
    assert row["status_changed_at"] == changed      # transition date untouched
    assert row["updated_at"] != changed             # edit date moved


# --- M4 gate-fix pins 1-2: revival survives sync; annotation-kept moves ----------------------

def test_gatefix1a_file_move_revival_survives_its_own_sync_and_the_next(
    migrated_con, memfile
):
    """GATE-FIX PIN 1a: a dormant thread is by definition >14d unreferenced,
    so before the fix a file-move revival self-reverted inside the very sync
    that applied it (apply_dormancy saw only old created_at/ref times). The
    basis now includes status_changed_at: the revival transition resets the
    clock. Freezes: ACTIVE at the end of the revival's own sync, no
    went_dormant entry for it, and still active after the NEXT sync."""
    add_row(
        migrated_con, "Sleeper", status="dormant",
        changed=iso(NOW - timedelta(days=30)),
        created=iso(NOW - timedelta(days=60)),
    )
    memfile.write_text(MINIMAL_FILE.format(line="Sleeper"), encoding="utf-8")
    result = memory.sync_memory(migrated_con)
    assert result.status_changed == ["Sleeper: dormant->active"]
    assert result.went_dormant == []  # did NOT self-revert in the same pass
    assert statuses(migrated_con)["Sleeper"] == "active"
    again = memory.sync_memory(migrated_con)  # canonical file, no edits
    assert again.went_dormant == [] and again.status_changed == []
    assert statuses(migrated_con)["Sleeper"] == "active"


def test_gatefix1_boundary_note_edit_still_excluded_from_the_new_basis(migrated_con):
    """GATE-FIX PIN 1 scope boundary: status_changed_at joined the basis but
    updated_at did NOT — a >14d thread whose only recent activity is a note
    edit still goes dormant."""
    add_row(
        migrated_con, "Edited Only",
        created=iso(NOW - timedelta(days=40)),
        changed=iso(NOW - timedelta(days=20)),  # old transition
        updated=iso(NOW),                        # fresh note edit
    )
    assert memory.apply_dormancy(migrated_con, now_utc=NOW) == ["Edited Only"]


def test_gatefix2_annotation_kept_move_to_active_is_clean_revival(
    migrated_con, memfile
):
    """GATE-FIX PIN 2: the principal rearranges lines with their annotations
    attached (the header says to keep them). A dormant line moved under
    Active WITH its "(dormant since …)" annotation must revive the REAL
    thread: annotation stripped, exactly one row, active, zero
    dismissed-by-deletion audit noise, nothing leaked into topic or note."""
    b = add_briefing(migrated_con, "2026-06-01", "2026-06-01T12:00:00.000Z")
    add_row(
        migrated_con, "Sleeper", status="dormant",
        changed=iso(NOW - timedelta(days=30)),
        created=iso(NOW - timedelta(days=60)), ref=b,
    )
    memfile.write_text(
        "# x\n## Active threads\n"
        "- Sleeper (dormant since 2026-06-04, last covered 2026-06-01)\n"
        "## Inactive\n",
        encoding="utf-8",
    )
    result = memory.sync_memory(migrated_con)
    assert result.status_changed == ["Sleeper: dormant->active"]
    assert result.added == []                    # no junk thread minted
    assert result.dismissed_by_deletion == []    # no audit inversion
    rows = migrated_con.execute(
        "SELECT topic, status, principal_note FROM memory"
    ).fetchall()
    assert len(rows) == 1
    assert rows[0]["topic"] == "Sleeper" and rows[0]["status"] == "active"
    assert rows[0]["principal_note"] is None     # annotation never became a note


def test_gatefix2_dismissed_annotation_kept_on_active_move_also_revives(
    migrated_con, memfile
):
    add_row(
        migrated_con, "Comeback", status="dismissed_user",
        changed=iso(NOW - timedelta(days=5)),
    )
    memfile.write_text(
        "# x\n## Active threads\n- Comeback (dismissed by you 2026-06-29)\n"
        "## Inactive\n",
        encoding="utf-8",
    )
    result = memory.sync_memory(migrated_con)
    assert result.status_changed == ["Comeback: dismissed_user->active"]
    rows = migrated_con.execute("SELECT topic FROM memory").fetchall()
    assert [r["topic"] for r in rows] == ["Comeback"]


def test_gatefix2_lastref_annotation_kept_on_demotion_strips_clean(
    migrated_con, memfile
):
    """The mirror case: an active line (rendered with "(last referenced: …)")
    demoted to Inactive with the annotation attached — the annotation strips,
    the real thread is dismissed, no junk topic."""
    b = add_briefing(migrated_con, "2026-07-01", "2026-07-01T12:00:00.000Z")
    add_row(migrated_con, "Demoted", ref=b)
    memfile.write_text(
        "# x\n## Active threads\n## Inactive\n"
        "- Demoted (last referenced: 2026-07-01)\n",
        encoding="utf-8",
    )
    result = memory.sync_memory(migrated_con)
    assert result.status_changed == ["Demoted: active->dismissed_user"]
    rows = migrated_con.execute("SELECT topic, status FROM memory").fetchall()
    assert len(rows) == 1
    assert rows[0]["topic"] == "Demoted"
    assert rows[0]["status"] == "dismissed_user"


# --- prior_briefing_context (M5 seam) --------------------------------------------------------

def test_prior_briefing_context_none_without_prior(migrated_con):
    assert memory.prior_briefing_context(migrated_con, "2026-07-04") is None


def test_prior_briefing_context_is_bounded_and_excludes_own_date(migrated_con):
    import json

    slots = [
        {
            "slot": i,
            "story_title": f"Story {i}",
            "summary": "S" * 400,
            "matched_tags": [{"name": "AI regulation", "level": "topic"}],
            "matched_memory": ["Iran War"] if i == 1 else [],
            "override": False,
        }
        for i in range(1, 6)
    ]
    migrated_con.execute(
        "INSERT INTO briefings (date, story_slots, generated_at)"
        " VALUES ('2026-07-03', ?, '2026-07-03T12:00:00.000Z')",
        (json.dumps(slots),),
    )
    migrated_con.execute(
        "INSERT INTO briefings (date, story_slots, generated_at)"
        " VALUES ('2026-07-04', ?, '2026-07-04T12:00:00.000Z')",
        (json.dumps(slots),),
    )
    migrated_con.commit()
    ctx = memory.prior_briefing_context(migrated_con, "2026-07-04")
    assert ctx["date"] == "2026-07-03"  # own-date row excluded, most recent prior wins
    assert len(ctx["stories"]) == 5
    assert ctx["stories"][0]["matched_memory"] == ["Iran War"]
    assert len(ctx["text_block"]) <= 1500  # bounded by construction, never full history
    assert ctx["text_block"].startswith("Your previous briefing (2026-07-03)")


# --- migration 0006: the table rebuild ---------------------------------------------------------

@pytest.fixture
def v1_world(tmp_path):
    """A DB migrated through 0005 only (the v1 CHECK still in force), with
    v1-status rows, plus a migrations dir we can grow to 0006."""
    mdir = tmp_path / "migs"
    mdir.mkdir()
    real = PROTOTYPE_ROOT / "migrations"
    for name in [
        "0001_initial_schema.sql", "0002_briefings_date_format.sql",
        "0003_ranking_runs.sql", "0004_ranking_runs_append_only.sql",
        "0005_memory_topic_unique.sql",
    ]:
        shutil.copy(real / name, mdir / name)
    db_path = tmp_path / "v1.db"
    db.migrate(db_path=db_path, migrations_dir=mdir)
    con = db.connect(db_path)
    con.execute(
        "INSERT INTO briefings (date, generated_at)"
        " VALUES ('2026-06-20', '2026-06-20T12:00:00.000Z')"
    )
    bid = con.execute("SELECT id FROM briefings").fetchone()["id"]
    con.execute(
        "INSERT INTO memory (topic, status, principal_note,"
        " last_referenced_briefing_id, created_at, updated_at) VALUES"
        " ('Live', 'active', 'note kept', ?, '2026-06-01T00:00:00.000Z',"
        "  '2026-06-25T00:00:00.000Z')",
        (bid,),
    )
    con.execute(
        "INSERT INTO memory (topic, status, created_at, updated_at) VALUES"
        " ('Was Stale', 'stale', '2026-06-01T00:00:00.000Z', '2026-06-10T00:00:00.000Z')"
    )
    con.execute(
        "INSERT INTO memory (topic, status, created_at, updated_at) VALUES"
        " ('Was Dismissed', 'dismissed', '2026-06-01T00:00:00.000Z', '2026-06-15T00:00:00.000Z')"
    )
    con.commit()
    con.close()
    shutil.copy(real / "0006_memory_lifecycle_v2.sql", mdir / "0006_memory_lifecycle_v2.sql")
    return db_path, mdir


def test_0006_rebuild_maps_statuses_and_preserves_data(v1_world):
    db_path, mdir = v1_world
    ran = db.migrate(db_path=db_path, migrations_dir=mdir)
    assert ran == ["0006_memory_lifecycle_v2.sql"]
    con = db.connect(db_path)
    try:
        rows = {
            r["topic"]: r
            for r in con.execute(
                "SELECT topic, status, principal_note, status_changed_at,"
                " last_referenced_briefing_id, created_at FROM memory"
            )
        }
    finally:
        con.close()
    assert rows["Live"]["status"] == "active"
    assert rows["Was Stale"]["status"] == "dormant"
    assert rows["Was Dismissed"]["status"] == "dismissed_user"
    # Data survives the rebuild:
    assert rows["Live"]["principal_note"] == "note kept"
    assert rows["Live"]["last_referenced_briefing_id"] is not None
    assert rows["Live"]["created_at"] == "2026-06-01T00:00:00.000Z"
    # status_changed_at seeded from the old updated_at (annotation dates):
    assert rows["Was Stale"]["status_changed_at"] == "2026-06-10T00:00:00.000Z"


def test_0006_reapply_is_harmless_pass_through(v1_world):
    db_path, mdir = v1_world
    db.migrate(db_path=db_path, migrations_dir=mdir)
    con = db.connect(db_path)
    try:
        con.execute(
            "DELETE FROM schema_migrations WHERE filename = '0006_memory_lifecycle_v2.sql'"
        )
        con.commit()
    finally:
        con.close()
    ran = db.migrate(db_path=db_path, migrations_dir=mdir)  # must not raise
    assert ran == ["0006_memory_lifecycle_v2.sql"]
    con = db.connect(db_path)
    try:
        after = {
            r["topic"]: r["status"] for r in con.execute("SELECT topic, status FROM memory")
        }
    finally:
        con.close()
    assert after == {
        "Live": "active", "Was Stale": "dormant", "Was Dismissed": "dismissed_user",
    }
