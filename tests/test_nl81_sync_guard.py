"""NL-81 — the sync resurrection guard.

Build contract: workspace/debates/2026-07-25--newslens--engineering-2.md §5.
Every test here is an acceptance criterion for one of that contract's QA
charges; the section headers name them (QA-1 … QA-8).

THE INCIDENT (2026-07-17, on record): a stale memory.md reconstruction was
fed through the two-way sync. The sync is file-wins with no memory of
deletion and no recency signal of any kind, so it faithfully propagated the
poison: four threads the swept database didn't have came back, the renamed
thread the file couldn't see was dismissed, and that org-caused dismissal
rendered as "(dismissed by you …)". The reconstruction had the NEWEST mtime
on the machine — which is why the guard's recency signal is a generation
stamp and never a clock.

Sandboxed like the rest of the suite: scratch DBs, a redirected memory.md.
The real file is live principal state.
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from http.server import ThreadingHTTPServer
from types import SimpleNamespace

import pytest

from conftest import anthropic_envelope
from newslens import (cli, config, db, llm as llm_mod, memory, paths, ranking,
                      server)

NOW = datetime(2026, 7, 25, 12, 0, 0, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def frozen_clock(monkeypatch):
    monkeypatch.setattr(memory, "_utc_now", lambda: NOW)


@pytest.fixture
def memfile(tmp_path, monkeypatch):
    f = tmp_path / "memory.md"
    monkeypatch.setattr(paths, "MEMORY_FILE", f)
    return f


def iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%S.000Z")


def add_row(con, topic, status="active", note=None, via=None):
    """Seed a memory row. `dismissed_via` is named ONLY when a test actually
    sets it, so a seed with no provenance is writable against the pre-0022
    schema too — which is what lets the born-red measurement of the incident
    replay reach sync_memory at base instead of dying on the column."""
    cols = ("topic, status, principal_note, status_changed_at, created_at,"
            " updated_at")
    vals = [topic, status, note, iso(NOW - timedelta(days=1)),
            iso(NOW - timedelta(days=2)), iso(NOW - timedelta(days=1))]
    if via is not None:
        cols += ", dismissed_via"
        vals.append(via)
    con.execute(f"INSERT INTO memory ({cols})"
                f" VALUES ({', '.join('?' * len(vals))})", vals)
    con.commit()
    return con.execute("SELECT id FROM memory WHERE topic = ?",
                       (topic,)).fetchone()["id"]


def tombstones(con):
    return [dict(r) for r in con.execute(
        "SELECT topic_key, topic, kind, successor_key, actor"
        " FROM memory_tombstones ORDER BY id")]


def statuses(con):
    return {r["topic"]: r["status"]
            for r in con.execute("SELECT topic, status FROM memory")}


def db_snapshot(con):
    """Everything the import path could possibly touch, as a comparable blob.
    Used to prove a refusal is SIDE-EFFECT-FREE rather than merely quiet."""
    return json.dumps([
        dict(r) for r in con.execute(
            "SELECT id, topic, status, principal_note, status_changed_at,"
            " updated_at, dismissed_via FROM memory ORDER BY id")], sort_keys=True)


def import_surface(con):
    """The narrower snapshot for a run that legitimately writes OTHER memory
    columns. A rank run stamps last_referenced_briefing_id/updated_at on the
    threads its edition covered — that is the run's own bookkeeping, not the
    import. Every field the file->DB import can write is here, and none of the
    fields it cannot."""
    return json.dumps([
        dict(r) for r in con.execute(
            "SELECT id, topic, status, principal_note, status_changed_at,"
            " dismissed_via FROM memory ORDER BY id")], sort_keys=True)


def file_body(con, *, active=(), inactive=(), stamp=None):
    """A hand-authored memory.md. `stamp` None = carry the CURRENT lawful
    stamp; "" = no stamp at all; a string = that literal stamp line."""
    head = memory.stamp_line(con) if stamp is None else stamp
    parts = [head, "# NewsLens memory\n\n## Active threads\n"]
    parts += [f"- {a}\n" for a in active]
    parts.append("\n## Inactive\n")
    parts += [f"- {i}\n" for i in inactive]
    return "".join(parts)


def dismissed_lines(text):
    return [ln for ln in text.splitlines() if ln.startswith("- ")]


# ===========================================================================
# QA-1 — THE NAMED ACCEPTANCE TEST: the 2026-07-17 incident, replayed
# ===========================================================================

def _incident_world(con):
    """The database AFTER the 07-17 junk sweep.

    id 22 was renamed by the picker ("Volkswagen plans significant job cuts"
    -> "Volkswagen") and is ACTIVE; the swept threads were deleted through the
    supported lane and so carry deletion records. This is the state the stale
    file is about to argue with."""
    vw = add_row(con, "Volkswagen plans significant job cuts")
    memory.move_follow_altitude(con, vw, new_name="Volkswagen",
                                altitude="entity", primary_entity="Volkswagen")
    for junk in ("redistrictinga", "Nvidia", "TSMC"):
        add_row(con, junk)
        memory.dismiss_thread(con, junk)          # delete is dismissed-only
        ok, _ = memory.delete_thread(con, junk)
        assert ok
    return vw


def _poisoned_reconstruction(con, memfile, *, stamp=""):
    """memory.md rebuilt from PRE-sweep content: the old names are all present,
    the renamed thread's CURRENT name is absent, and there is no valid stamp.
    Written LAST, so its mtime is the newest on the machine — exactly the file
    that caused the incident."""
    text = file_body(
        con,
        active=["Volkswagen plans significant job cuts", "redistrictinga",
                "Nvidia", "TSMC"],
        stamp=stamp)
    memfile.write_text(text, encoding="utf-8")
    os.utime(memfile, (time.time() + 3600, time.time() + 3600))  # newest mtime
    return text


def test_resurrection_replay_2026_07_17(migrated_con, memfile):
    """THE INCIDENT, REPLAYED (QA-1). At base this sync re-creates four dead
    threads and dismisses the renamed one, then calls that dismissal the
    principal's. Under the guard it must refuse, change NOTHING on either
    side, and say exactly what it refused."""
    _incident_world(migrated_con)
    memory.write_memory_file(migrated_con)        # the lawful file, gen bumped
    text = _poisoned_reconstruction(migrated_con, memfile)

    result = memory.sync_memory(migrated_con)

    # 1. THE INCIDENT ITSELF, asserted first and in the plainest terms the
    #    schema allows, so that a base-tree run of this test fails ON THE
    #    RESURRECTION rather than on some helper the guard introduced: the
    #    swept threads stayed dead and the renamed thread stayed ACTIVE under
    #    its new name. At base this reads five rows, four of them risen.
    assert statuses(migrated_con) == {"Volkswagen": "active"}
    # 2. The file is BYTE-IDENTICAL: a refusal rewrites neither side, so the
    #    principal's own copy is exactly as recoverable as it was.
    assert memfile.read_text(encoding="utf-8") == text
    # 3. The import was REFUSED, and it said so.
    assert result.imported is False
    assert result.stale_refusal is not None
    assert result.added == [] and result.status_changed == []
    assert result.dismissed_by_deletion == []
    assert result.generation is None                  # no render, no bump
    # 4. The disclosure names every blocked act, and the way to really undo it.
    msg = result.stale_refusal
    assert "was NOT imported" in msg and "Nothing on either side was changed" in msg
    for name in ("redistrictinga", "Nvidia", "TSMC",
                 "Volkswagen plans significant job cuts"):
        assert name in msg
    assert "4 blocked as deleted/renamed" in msg
    assert "--accept-file" in msg
    assert result.stale_refusal in result.summary_lines()


def test_resurrection_replay_accept_file_still_blocks_and_never_says_by_you(
        migrated_con, memfile):
    """QA-1 second half: the principal's explicit file-wins override. It
    overrides STALENESS ONLY — the deletion records still hold, and the
    dismissal this file infers is attributed to the file, not to them."""
    _incident_world(migrated_con)
    memory.write_memory_file(migrated_con)
    _poisoned_reconstruction(migrated_con, memfile)

    result = memory.sync_memory(migrated_con, accept_file=True)

    assert result.accepted_file is True
    # The four tombstoned threads are STILL not re-created — not even here.
    assert result.added == []
    assert set(statuses(migrated_con)) == {"Volkswagen"}
    assert result.blocked_resurrections and "NOT re-imported" in (
        result.blocked_resurrections[0])
    # The file-absent renamed thread IS dismissed (file wins, by request) —
    # but by the FILE, and the rendered line says so.
    assert result.dismissed_by_deletion == ["Volkswagen"]
    row = migrated_con.execute(
        "SELECT status, dismissed_via FROM memory").fetchone()
    assert row["status"] == "dismissed_user"
    assert row["dismissed_via"] == "file_sync"
    lines = dismissed_lines(memfile.read_text(encoding="utf-8"))
    assert lines == ["- Volkswagen (removed from your memory.md 2026-07-25)"]
    assert not any("dismissed by you" in ln for ln in lines)


# ===========================================================================
# QA-2 — tombstone writes, the append-only triggers, chains and cycles
# ===========================================================================

def test_delete_thread_appends_a_delete_tombstone_that_outlives_the_row(
        migrated_con):
    tid = add_row(migrated_con, "Gone Thread")
    memory.dismiss_thread(migrated_con, "Gone Thread")
    ok, msg = memory.delete_thread(migrated_con, "Gone Thread")
    assert (ok, msg) == (True, "deleted")
    assert migrated_con.execute("SELECT COUNT(*) FROM memory").fetchone()[0] == 0
    assert tombstones(migrated_con) == [
        {"topic_key": "gone thread", "topic": "Gone Thread", "kind": "delete",
         "successor_key": None, "actor": "principal"}]


def test_delete_thread_actor_is_recorded_not_assumed(migrated_con):
    add_row(migrated_con, "Swept")
    memory.dismiss_thread(migrated_con, "Swept")
    memory.delete_thread(migrated_con, "Swept", actor="org")
    assert tombstones(migrated_con)[0]["actor"] == "org"


def test_rename_appends_a_rename_tombstone_carrying_the_successor(migrated_con):
    tid = add_row(migrated_con, "Old Name")
    memory.move_follow_altitude(migrated_con, tid, new_name="New Name",
                                altitude="entity")
    ts = tombstones(migrated_con)
    assert ts == [{"topic_key": "old name", "topic": "Old Name",
                   "kind": "rename", "successor_key": "new name",
                   "actor": "principal"}]


def test_tombstones_are_append_only_structurally(migrated_con):
    """A deletion record a later write can erase is the same hole one layer
    down — so the ban is a trigger, not a convention."""
    add_row(migrated_con, "X")
    memory.dismiss_thread(migrated_con, "X")
    memory.delete_thread(migrated_con, "X")
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        migrated_con.execute("UPDATE memory_tombstones SET kind = 'lift'")
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        migrated_con.execute("DELETE FROM memory_tombstones")


def test_rename_chain_resolves_to_the_current_name(migrated_con):
    """a -> b -> c: asking about `a` names the thread as it lives TODAY."""
    tid = add_row(migrated_con, "A")
    memory.move_follow_altitude(migrated_con, tid, new_name="B", altitude="entity")
    memory.move_follow_altitude(migrated_con, tid, new_name="C", altitude="entity")
    block = memory.tombstone_block(migrated_con, "A")
    assert block["kind"] == "rename" and block["current_name"] == "C"
    assert memory.tombstone_block(migrated_con, "C") is None   # the live name


def test_rename_cycle_terminates(migrated_con):
    """a -> b -> a. The visited-set guard means the walk ends instead of
    hanging the sync."""
    tid = add_row(migrated_con, "A")
    memory.move_follow_altitude(migrated_con, tid, new_name="B", altitude="entity")
    memory.move_follow_altitude(migrated_con, tid, new_name="A", altitude="entity")
    block = memory.tombstone_block(migrated_con, "B")
    assert block is not None and block["kind"] == "rename"
    # `A` is live again (the row wears it), so it is importable, not blocked.
    assert memory.tombstone_block(migrated_con, "A") is None


def test_no_tombstone_section_is_ever_rendered_into_memory_md(migrated_con,
                                                              memfile):
    """Banked design cut: tombstones are DB-resident. The file never grows a
    deleted section — more parser surface, more states to round-trip.

    CARRIED-INVARIANT (BORN-GREEN), labeled explicitly per the 2026-07-18 gate
    ruling. At base there are no tombstones at all, so this passes trivially —
    it is NOT evidence the guard works. Its job is the opposite: to bite if a
    later hand reintroduces the rendered-deleted-section design that was cut
    here. The other 42 tests in this file were measured red at b803254."""
    add_row(migrated_con, "Deleted Thing")
    memory.dismiss_thread(migrated_con, "Deleted Thing")
    memory.delete_thread(migrated_con, "Deleted Thing")
    text = memory.render_file(migrated_con)
    assert "Deleted Thing" not in text
    assert "tombstone" not in text.lower() and "## Deleted" not in text


# ===========================================================================
# QA-3 — THE MTIME TRAP. This test exists to keep mtime out of the mechanism.
# ===========================================================================

def test_freshly_written_stale_content_is_refused(migrated_con, memfile):
    """The 07-17 file was the NEWEST thing on the machine. Any wall-clock
    signal passes it. The stamp does not."""
    add_row(migrated_con, "Kept")
    memory.write_memory_file(migrated_con)
    lawful_mtime = memfile.stat().st_mtime_ns
    stale = file_body(migrated_con, active=["Kept", "Resurrected"],
                      stamp="<!-- newslens-sync: gen=0 identity="
                            + memory.sync_state(migrated_con)["identity"]
                            + " profile=- rendered=2026-07-17T00:38:33.000Z -->\n")
    memfile.write_text(stale, encoding="utf-8")
    os.utime(memfile, (time.time() + 86400, time.time() + 86400))
    assert memfile.stat().st_mtime_ns > lawful_mtime      # newest mtime wins…

    result = memory.sync_memory(migrated_con)

    assert result.imported is False                        # …and loses anyway
    assert "file generation 0" in result.stale_refusal
    assert "Resurrected" not in statuses(migrated_con)
    assert memfile.read_text(encoding="utf-8") == stale


def test_the_guard_never_reads_mtime(migrated_con, memfile):
    """Structural companion to the trap: a file with an ANCIENT mtime and a
    CURRENT stamp is lawful. Recency is content, not clock."""
    add_row(migrated_con, "Kept")
    memory.write_memory_file(migrated_con)
    text = file_body(migrated_con, active=["Kept", "Genuinely Added"])
    memfile.write_text(text, encoding="utf-8")
    os.utime(memfile, (0, 0))                              # 1970
    result = memory.sync_memory(migrated_con)
    assert result.imported is True and result.added == ["Genuinely Added"]


# ===========================================================================
# QA-4 — the attribution matrix: "by you" only when it was you
# ===========================================================================

def test_principal_verb_keeps_by_you(migrated_con, memfile):
    add_row(migrated_con, "Stopped By Me")
    assert memory.dismiss_thread(migrated_con, "Stopped By Me") is True
    assert migrated_con.execute(
        "SELECT dismissed_via FROM memory").fetchone()[0] == "principal"
    assert dismissed_lines(memory.render_file(migrated_con)) == [
        "- Stopped By Me (dismissed by you 2026-07-25)"]


def test_sync_inferred_dismissal_states_the_mechanism_not_an_actor(
        migrated_con, memfile):
    """THE 07-17 MISATTRIBUTION. The file can be written by anyone, including
    the org — so a dismissal inferred from it names the mechanism."""
    add_row(migrated_con, "Kept")
    add_row(migrated_con, "Dropped From The File")
    memory.write_memory_file(migrated_con)
    memfile.write_text(file_body(migrated_con, active=["Kept"]), encoding="utf-8")
    result = memory.sync_memory(migrated_con)
    assert result.dismissed_by_deletion == ["Dropped From The File"]
    assert migrated_con.execute(
        "SELECT dismissed_via FROM memory WHERE topic = 'Dropped From The File'"
    ).fetchone()[0] == "file_sync"
    line = [ln for ln in dismissed_lines(memfile.read_text(encoding="utf-8"))
            if "Dropped" in ln]
    assert line == ["- Dropped From The File (removed from your memory.md "
                    "2026-07-25)"]


def test_bare_line_demotion_is_file_sync_not_by_you(migrated_con, memfile):
    """§5.4's explicit ruling: a bare line under Inactive IS explicit intent,
    but it ARRIVES VIA THE FILE. "By you" stays reserved for verb surfaces."""
    add_row(migrated_con, "Pushed Down")
    memory.write_memory_file(migrated_con)
    memfile.write_text(file_body(migrated_con, inactive=["Pushed Down"]),
                       encoding="utf-8")
    memory.sync_memory(migrated_con)
    assert migrated_con.execute(
        "SELECT dismissed_via FROM memory").fetchone()[0] == "file_sync"


def test_legacy_null_provenance_renders_neutral_never_backfilled(migrated_con):
    """Rows dismissed before 0022 cannot prove who did it. They get neutral
    copy — we never backfill agency we can't prove."""
    add_row(migrated_con, "Ancient", status="dismissed_user", via=None)
    assert dismissed_lines(memory.render_file(migrated_con)) == [
        "- Ancient (dismissed 2026-07-24)"]


@pytest.mark.parametrize("via,annotation", [
    ("principal", "(dismissed by you 2026-07-24)"),
    ("file_sync", "(removed from your memory.md 2026-07-24)"),
    (None, "(dismissed 2026-07-24)"),
])
def test_every_annotation_form_round_trips_with_zero_edits(
        migrated_con, memfile, via, annotation):
    """render -> parse -> sync must be a no-op for all three forms, or a
    round-trip could launder 'file_sync' into 'principal'."""
    add_row(migrated_con, "Stopped", status="dismissed_user", via=via)
    memory.write_memory_file(migrated_con)
    assert annotation in memfile.read_text(encoding="utf-8")
    result = memory.sync_memory(migrated_con)
    assert result.edits_applied == 0
    assert result.status_changed == [] and result.dismissed_by_deletion == []
    assert migrated_con.execute(
        "SELECT dismissed_via FROM memory").fetchone()[0] == via
    assert annotation in memfile.read_text(encoding="utf-8")


@pytest.mark.parametrize("annotation", [
    "(dismissed by you 2026-06-29)",
    "(removed from your memory.md 2026-06-29)",
    "(dismissed 2026-06-29)",
])
def test_any_stopped_annotation_moved_up_to_active_revives_cleanly(
        migrated_con, memfile, annotation):
    """The M4 gate-fix-2 class, extended to the new forms: a line carried up to
    Active WITH its annotation is revival intent — the annotation must strip,
    never leak into the topic or mint a junk thread."""
    add_row(migrated_con, "Comeback", status="dismissed_user", via="file_sync")
    memory.write_memory_file(migrated_con)
    memfile.write_text(
        file_body(migrated_con, active=[f"Comeback {annotation}"]),
        encoding="utf-8")
    result = memory.sync_memory(migrated_con)
    assert result.status_changed == ["Comeback: dismissed_user->active"]
    assert result.added == []
    rows = migrated_con.execute(
        "SELECT topic, principal_note, dismissed_via FROM memory").fetchall()
    assert len(rows) == 1 and rows[0]["topic"] == "Comeback"
    assert rows[0]["principal_note"] is None
    assert rows[0]["dismissed_via"] is None      # not stopped any more


# ===========================================================================
# QA-5 — the embedded degrade: never throw the edition dead, never mutate
# ===========================================================================

def rank_cfg():
    return config.SourcesConfig(
        sources=[config.Source(name="Outlet 1", rss_url="https://o1.example/f")],
        interests_broad=["economy"], interests_granular=["AI regulation"])


@pytest.fixture
def llm(fake_api, monkeypatch):
    monkeypatch.setattr(llm_mod, "ANTHROPIC_MESSAGES_URL",
                        fake_api.base_url + "/v1/messages")
    monkeypatch.setenv("ANTHROPIC_API_KEY", fake_api.good_key)
    monkeypatch.setenv("NEWSLENS_LANE_RANK", "api")
    monkeypatch.setattr(time, "sleep", lambda s: None)
    return fake_api


def test_ranking_degrades_on_a_stale_file_and_touches_neither_side(
        migrated_con, memfile, llm):
    """Onna's ruling, mechanically. A stale FILE must not kill the 3am
    edition — that is strictly worse than the disease. The run completes on
    database state, warns unmissably, and leaves both sides alone."""
    add_row(migrated_con, "Iran War")
    now = iso(datetime.now(timezone.utc))
    for i in (1, 2, 3):
        migrated_con.execute(
            "INSERT INTO source_items (id, source_type, outlet, url, title,"
            " fetched_at) VALUES (?, 'rss', ?, ?, ?, ?)",
            (i, f"Outlet {i}", f"https://o{i}.example/{i}", f"Story {i}", now))
    migrated_con.commit()
    memory.write_memory_file(migrated_con)
    stale = file_body(migrated_con, active=["Iran War", "Resurrected"],
                      stamp="<!-- newslens-sync: gen=0 identity="
                            + memory.sync_state(migrated_con)["identity"]
                            + " profile=- rendered=2026-07-17T00:38:33.000Z -->\n")
    memfile.write_text(stale, encoding="utf-8")
    before_db = import_surface(migrated_con)
    before_gen = memory.sync_state(migrated_con)["generation"]

    payload = {"clusters": [{
        "story_title": "Earned on merits", "summary": "Summary.",
        "item_ids": [1, 2], "matched_tags": [{"name": "AI regulation",
                                              "level": "topic"}],
        "matched_memory": ["Iran War"], "matched_dormant": [],
        "world_impact": 6, "world_impact_reason": "Reason here"}]}
    llm.add_route("/v1/messages", status=200,
                  body=anthropic_envelope(payload, input_tokens=900),
                  content_type="application/json")
    report = ranking.run_rank(date="2026-07-25", con=migrated_con,
                              cfg=rank_cfg(), env={"OPENAI_API_KEY": "sk-x"})

    # The edition RAN.
    assert report.slots
    # The refusal is unmissable in the edition log.
    assert any("was NOT imported" in w for w in report.warnings)
    assert any("post-run refresh skipped too" in w for w in report.warnings)
    # SIDE-EFFECT-FREE SKIP (Rook's condition): neither side moved. The only
    # database write the run made is its own briefing/reference bookkeeping —
    # the memory table and the file are exactly as they were.
    assert memfile.read_text(encoding="utf-8") == stale
    assert memory.sync_state(migrated_con)["generation"] == before_gen
    assert import_surface(migrated_con) == before_db
    assert "Resurrected" not in statuses(migrated_con)


@pytest.fixture
def ui(tmp_paths, monkeypatch):
    db.migrate()
    monkeypatch.setattr(server, "GEN_JOB", server._GenJob())
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    yield SimpleNamespace(base=f"http://127.0.0.1:{httpd.server_address[1]}")
    httpd.shutdown()
    httpd.server_close()


def post(ui, path, payload):
    req = urllib.request.Request(
        ui.base + path, data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.getcode(), json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode("utf-8"))


def test_server_verb_degrades_on_a_stale_file_and_discloses(ui):
    """The reader is mid-session. Their tap still works, the file is left
    alone, and the refusal rides back in the response."""
    con = db.connect()
    try:
        add_row(con, "Existing")
        memory.write_memory_file(con)
        stale = file_body(con, active=["Existing", "Resurrected"],
                          stamp="<!-- newslens-sync: gen=0 identity="
                                + memory.sync_state(con)["identity"]
                                + " profile=- rendered=2026-07-17T00:00:00Z -->\n")
    finally:
        con.close()
    paths.MEMORY_FILE.write_text(stale, encoding="utf-8")

    code, obj = post(ui, "/api/follow", {"topic": "Tapped Now"})

    assert obj["ok"] is True and obj["outcome"] == "added"     # the verb ran
    assert any("was NOT imported" in w for w in obj["warnings"])
    # File untouched; the stale line was never imported.
    assert paths.MEMORY_FILE.read_text(encoding="utf-8") == stale
    con = db.connect()
    try:
        assert "Resurrected" not in statuses(con)
        assert statuses(con)["Tapped Now"] == "active"
    finally:
        con.close()


# ===========================================================================
# QA-6 — profile mispairing (the Stage-0 day-one piece)
# ===========================================================================

def test_a_file_from_another_profile_is_refused_naming_both_sides(
        migrated_con, memfile):
    add_row(migrated_con, "Mine")
    memory.write_memory_file(migrated_con)
    mine = memory.sync_state(migrated_con)["identity"]
    foreign = file_body(
        migrated_con, active=["Theirs"],
        stamp=f"<!-- newslens-sync: gen={memory.sync_state(migrated_con)['generation']}"
              " identity=61c4aaaabbbbccccddddeeeeffff0000 profile=kass"
              " rendered=2026-07-25T09:00:00.000Z -->\n")
    memfile.write_text(foreign, encoding="utf-8")

    result = memory.sync_memory(migrated_con)

    assert result.imported is False
    assert "DIFFERENT NewsLens database" in result.stale_refusal
    assert "61c4aaaa" in result.stale_refusal          # theirs, named
    assert mine[:8] in result.stale_refusal            # ours, named
    assert "kass" in result.stale_refusal
    assert "Theirs" not in statuses(migrated_con)
    assert memfile.read_text(encoding="utf-8") == foreign


def test_identity_mismatch_beats_a_matching_generation(migrated_con, memfile):
    """The dangerous shape: two profiles at the same generation. Identity is
    checked FIRST, so a matching counter cannot wave a foreign file through."""
    add_row(migrated_con, "Mine")
    memory.write_memory_file(migrated_con)
    gen = memory.sync_state(migrated_con)["generation"]
    memfile.write_text(file_body(
        migrated_con, active=["Mine", "Theirs"],
        stamp=f"<!-- newslens-sync: gen={gen} identity=deadbeefdeadbeefdeadbeef"
              "deadbeef profile=kass rendered=2026-07-25T09:00:00.000Z -->\n"),
        encoding="utf-8")
    result = memory.sync_memory(migrated_con)
    assert result.imported is False
    assert "DIFFERENT NewsLens database" in result.stale_refusal


def test_a_surviving_file_recovers_onto_a_rebuilt_database_via_accept_file(
        tmp_path, monkeypatch):
    """The honest cost of the pairing identity, and its exit. If the database
    is lost and rebuilt, the surviving memory.md is — correctly — a file from a
    DIFFERENT database, so it refuses. `--accept-file` recovers it in one
    command, and the rewrite RE-PAIRS the file to the new database, so the very
    next sync is lawful. Recovery is explicit now, not silent."""
    memfile = tmp_path / "memory.md"
    monkeypatch.setattr(paths, "MEMORY_FILE", memfile)
    db.migrate(db_path=tmp_path / "old.db")
    old = db.connect(tmp_path / "old.db")
    add_row(old, "Kept", note="a note")
    memory.write_memory_file(old)
    old.close()

    db.migrate(db_path=tmp_path / "new.db")
    new = db.connect(tmp_path / "new.db")
    try:
        assert memory.sync_memory(new).imported is False
        recovered = memory.sync_memory(new, accept_file=True)
        assert recovered.added == ["Kept"]
        assert memory.parse_stamp(memfile.read_text(encoding="utf-8"))[
            "identity"] == memory.sync_state(new)["identity"]
        again = memory.sync_memory(new)
        assert again.imported is True and again.stale_refusal is None
    finally:
        new.close()


# ===========================================================================
# QA-7 — the one-shot bootstrap (the path that meets the LIVE file once)
# ===========================================================================

def test_bootstrap_adopts_an_unstamped_file_that_agrees(migrated_con, memfile):
    add_row(migrated_con, "Agreed", note="a note")
    memfile.write_text(file_body(migrated_con, active=["Agreed — a note"],
                                 stamp=""), encoding="utf-8")
    assert memory.sync_state(migrated_con)["generation"] == 0

    result = memory.sync_memory(migrated_con)

    assert result.bootstrapped is True and result.imported is True
    assert result.stale_refusal is None
    assert result.edits_applied == 0
    text = memfile.read_text(encoding="utf-8")
    stamp = memory.parse_stamp(text)
    assert stamp is not None
    assert stamp["generation"] == memory.sync_state(migrated_con)["generation"] == 1
    assert stamp["identity"] == memory.sync_state(migrated_con)["identity"]


def test_bootstrap_refuses_an_unstamped_file_that_disagrees(migrated_con,
                                                            memfile):
    """No silent adoption of an unstamped file that disagrees — the one-shot
    adoption path meets real principal state exactly once, and a mistake there
    is a mistake on the live file."""
    add_row(migrated_con, "In The DB")
    text = file_body(migrated_con, active=["In The DB", "Only In The File"],
                     stamp="")
    memfile.write_text(text, encoding="utf-8")
    before = db_snapshot(migrated_con)

    result = memory.sync_memory(migrated_con)

    assert result.imported is False and result.bootstrapped is False
    assert "no NewsLens generation stamp and it disagrees" in result.stale_refusal
    assert "Only In The File" in result.stale_refusal
    assert db_snapshot(migrated_con) == before
    assert memfile.read_text(encoding="utf-8") == text


def test_bootstrap_is_one_shot_not_a_standing_amnesty(migrated_con, memfile):
    """After the first render the counter has moved, so an unstamped file is
    stale on its face — the adoption path cannot be re-entered by deleting the
    stamp line out of a file you want imported."""
    add_row(migrated_con, "Kept")
    memory.write_memory_file(migrated_con)                 # generation 1
    memfile.write_text(file_body(migrated_con, active=["Kept", "Sneaked In"],
                                 stamp=""), encoding="utf-8")
    result = memory.sync_memory(migrated_con)
    assert result.imported is False
    assert "carries no NewsLens generation stamp" in result.stale_refusal
    assert "Sneaked In" not in statuses(migrated_con)


def test_the_stamp_is_invisible_to_the_parser(migrated_con, memfile):
    """Rollback safety: the stamp is an HTML comment, so a pre-NL-81 build
    reads a stamped file with no change in behavior and nothing to unwind."""
    add_row(migrated_con, "Thing", note="note")
    text = memory.render_file(migrated_con)
    assert "newslens-sync:" in text
    assert memory.parse_file(text) == [
        {"topic": "Thing", "note": "note", "status": "active"}]


# ===========================================================================
# QA-8 — the lift lane: the deliberate way back
# ===========================================================================

def test_memory_add_lifts_a_tombstone_and_recreates_the_thread(migrated_con,
                                                               memfile):
    add_row(migrated_con, "Redistricting")
    memory.dismiss_thread(migrated_con, "Redistricting")
    memory.delete_thread(migrated_con, "Redistricting")
    assert memory.tombstone_block(migrated_con, "Redistricting") is not None

    lifted = memory.lift_tombstone(migrated_con, "Redistricting")

    assert lifted["kind"] == "delete"
    assert [t["kind"] for t in tombstones(migrated_con)] == ["delete", "lift"]
    assert memory.tombstone_block(migrated_con, "Redistricting") is None


def test_add_on_a_live_topic_appends_no_tombstone(migrated_con):
    add_row(migrated_con, "Alive")
    memory.dismiss_thread(migrated_con, "Alive")
    assert memory.add_thread(migrated_con, "Alive") == "revived"
    assert tombstones(migrated_con) == []


def test_a_lifted_thread_survives_the_next_sync(migrated_con, memfile):
    """After the lift, the file may name it again — the guard has been told."""
    add_row(migrated_con, "Redistricting")
    memory.dismiss_thread(migrated_con, "Redistricting")
    memory.delete_thread(migrated_con, "Redistricting")
    assert memory.add_thread(migrated_con, "Redistricting") == "added"
    memory.write_memory_file(migrated_con)
    result = memory.sync_memory(migrated_con)
    assert result.blocked_resurrections == []
    assert statuses(migrated_con)["Redistricting"] == "active"


def test_cli_memory_add_is_the_documented_lift_lane(tmp_paths, capsys):
    """The disclosure the refusal copy points at, end to end through the CLI."""
    cli.main(["migrate"])
    con = db.connect()
    try:
        add_row(con, "Redistrictinga")
        memory.dismiss_thread(con, "Redistrictinga")
        memory.delete_thread(con, "Redistrictinga")
    finally:
        con.close()
    capsys.readouterr()

    rc = cli.main(["memory", "add", "Redistrictinga"])
    out = capsys.readouterr().out

    assert rc == 0
    assert "was deleted on 2026-07-25" in out
    assert "on your explicit request (recorded)" in out
    con = db.connect()
    try:
        assert statuses(con)["Redistrictinga"] == "active"
        assert [t["kind"] for t in tombstones(con)] == ["delete", "lift"]
    finally:
        con.close()


# ===========================================================================
# Blocked resurrection on a LAWFUL file + the interactive refusal surface
# ===========================================================================

def test_a_lawful_file_still_cannot_resurrect_a_deleted_thread(migrated_con,
                                                               memfile):
    """The tombstone is not a staleness heuristic — it holds even when the
    file is perfectly current. The line is not force-deleted from the file
    (a refusal mutates nothing); the next lawful render makes it canonical."""
    add_row(migrated_con, "Kept")
    add_row(migrated_con, "Redistrictinga")
    memory.dismiss_thread(migrated_con, "Redistrictinga")
    memory.delete_thread(migrated_con, "Redistrictinga")
    memory.write_memory_file(migrated_con)
    memfile.write_text(file_body(migrated_con,
                                 active=["Kept", "Redistrictinga"]),
                       encoding="utf-8")

    result = memory.sync_memory(migrated_con)

    assert result.imported is True and result.added == []
    assert result.blocked_resurrections == [
        'memory: 1 thread in memory.md was previously deleted and was NOT '
        're-imported ("Redistrictinga", deleted 2026-07-25) — to really bring '
        'it back: newslens memory add "Redistrictinga"']
    assert "Redistrictinga" not in statuses(migrated_con)
    assert "Redistrictinga" not in memfile.read_text(encoding="utf-8")


def test_a_renamed_thread_is_blocked_with_its_current_name_disclosed(
        migrated_con, memfile):
    tid = add_row(migrated_con, "Volkswagen plans significant job cuts")
    memory.move_follow_altitude(migrated_con, tid, new_name="Volkswagen",
                                altitude="entity")
    memory.write_memory_file(migrated_con)
    memfile.write_text(
        file_body(migrated_con,
                  active=["Volkswagen", "Volkswagen plans significant job cuts"]),
        encoding="utf-8")
    result = memory.sync_memory(migrated_con)
    assert result.added == []
    assert ('"Volkswagen plans significant job cuts" was renamed — it lives on '
            'as "Volkswagen"') in result.blocked_resurrections[0]


def test_cli_memory_verbs_refuse_a_stale_file_and_change_nothing(tmp_paths,
                                                                 capsys):
    """The INTERACTIVE half of Onna's split: a writing verb refuses outright,
    nonzero, with the two ways out."""
    cli.main(["migrate"])
    con = db.connect()
    try:
        add_row(con, "Existing")
        memory.write_memory_file(con)
        stale = file_body(con, active=["Existing", "Resurrected"],
                          stamp="<!-- newslens-sync: gen=0 identity="
                                + memory.sync_state(con)["identity"]
                                + " profile=- rendered=2026-07-17T00:00:00Z -->\n")
        before = db_snapshot(con)
    finally:
        con.close()
    paths.MEMORY_FILE.write_text(stale, encoding="utf-8")
    capsys.readouterr()

    rc = cli.main(["memory", "add", "Something New"])
    out = capsys.readouterr().out

    assert rc == 1
    assert "was NOT imported" in out
    assert "newslens memory sync --accept-file" in out
    assert "delete memory.md" in out
    con = db.connect()
    try:
        assert db_snapshot(con) == before      # the verb never ran
    finally:
        con.close()
    assert paths.MEMORY_FILE.read_text(encoding="utf-8") == stale


def test_cli_memory_list_degrades_because_it_writes_nothing(tmp_paths, capsys):
    """`list` is the one read-only verb: denying it would deny the principal
    the inspection that diagnoses the refusal. It shows database state, says
    so, and still writes nothing."""
    cli.main(["migrate"])
    con = db.connect()
    try:
        add_row(con, "Existing")
        memory.write_memory_file(con)
        stale = file_body(con, active=["Existing", "Resurrected"], stamp="")
    finally:
        con.close()
    paths.MEMORY_FILE.write_text(stale, encoding="utf-8")
    capsys.readouterr()

    rc = cli.main(["memory", "list"])
    out = capsys.readouterr().out

    assert rc == 0
    # The listing itself is DATABASE state: the stale file's extra thread is
    # named in the refusal (that is the disclosure) but never in the list.
    listed = [ln for ln in out.splitlines() if ln.strip().startswith("[")]
    assert listed == ["  [active] Existing"]
    assert "showing DATABASE state only" in out
    assert paths.MEMORY_FILE.read_text(encoding="utf-8") == stale


def test_cli_memory_sync_accept_file_is_the_documented_exit(tmp_paths, capsys):
    cli.main(["migrate"])
    con = db.connect()
    try:
        add_row(con, "Existing")
        memory.write_memory_file(con)
        stale = file_body(con, active=["Existing", "Genuinely Wanted"],
                          stamp="")
    finally:
        con.close()
    paths.MEMORY_FILE.write_text(stale, encoding="utf-8")
    capsys.readouterr()

    assert cli.main(["memory", "sync"]) == 1
    capsys.readouterr()
    rc = cli.main(["memory", "sync", "--accept-file"])
    out = capsys.readouterr().out

    assert rc == 0
    assert "accepted over the database on your explicit --accept-file" in out
    con = db.connect()
    try:
        assert statuses(con)["Genuinely Wanted"] == "active"
    finally:
        con.close()


def test_cli_memory_sync_on_a_lawful_file_applies_and_rewrites(tmp_paths,
                                                               capsys):
    """The ordinary path through the new verb: a current file with a hand edit
    lands, and the rewrite re-stamps."""
    cli.main(["migrate"])
    con = db.connect()
    try:
        add_row(con, "Existing")
        memory.write_memory_file(con)
        lawful = file_body(con, active=["Existing", "Hand Added — a note"])
    finally:
        con.close()
    paths.MEMORY_FILE.write_text(lawful, encoding="utf-8")
    capsys.readouterr()

    rc = cli.main(["memory", "sync"])
    out = capsys.readouterr().out

    assert rc == 0
    assert "applied 1 edit(s) from memory.md" in out
    con = db.connect()
    try:
        assert statuses(con)["Hand Added"] == "active"
        stamp = memory.parse_stamp(
            paths.MEMORY_FILE.read_text(encoding="utf-8"))
        assert stamp["generation"] == memory.sync_state(con)["generation"]
    finally:
        con.close()

    capsys.readouterr()
    assert cli.main(["memory", "sync"]) == 0     # idempotent
    assert "already agrees with the database" in capsys.readouterr().out


# ===========================================================================
# The generation counter's own contract
# ===========================================================================

def test_every_render_advances_the_generation_and_stamps_it(migrated_con,
                                                            memfile):
    add_row(migrated_con, "Thing")
    for expected in (1, 2, 3):
        memory.write_memory_file(migrated_con)
        assert memory.sync_state(migrated_con)["generation"] == expected
        assert memory.parse_stamp(
            memfile.read_text(encoding="utf-8"))["generation"] == expected


def test_render_file_alone_never_advances_the_counter(migrated_con):
    """Rendering for inspection must not invalidate the file on disk."""
    add_row(migrated_con, "Thing")
    before = memory.sync_state(migrated_con)["generation"]
    memory.render_file(migrated_con)
    memory.render_file(migrated_con)
    assert memory.sync_state(migrated_con)["generation"] == before


def test_a_hand_edit_that_keeps_the_stamp_is_lawful(migrated_con, memfile):
    """The normal loop: the principal edits the file the app last wrote. They
    don't touch the stamp, so the guard is invisible to them."""
    add_row(migrated_con, "Iran War")
    memory.write_memory_file(migrated_con)
    text = memfile.read_text(encoding="utf-8")
    memfile.write_text(text.replace("- Iran War",
                                    "- Iran War — watch the vote\n- Brand New"),
                       encoding="utf-8")
    result = memory.sync_memory(migrated_con)
    assert result.added == ["Brand New"]
    assert result.notes_updated == ["Iran War"]
    assert result.stale_refusal is None
