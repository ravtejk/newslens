"""M7 web UI server (ADR-0010): binding discipline, routes, the generation
job guard, sources.yaml line surgery, shared thread verbs, structured/fallback
rendering parity, and structural a11y.

All requests go over loopback against a live ThreadingHTTPServer on port 0 —
inside the suite's autouse sandbox (fresh DB, synthetic sources, sandboxed
memory.md) and the loopback-only network guard. The GEN_JOB never reaches a
paid call: its double-trigger pin uses a gated fake pipeline.
"""

from __future__ import annotations

import json
import threading
import time
import urllib.error
import urllib.request
import wave
from datetime import datetime, timezone
from http.server import ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace

import pytest

from newslens import config, db, memory, paths, ranking, server, webui

from conftest import PROTOTYPE_ROOT

# NL-11: Today defaults to TODAY's edition (or the empty state) — never a
# stale one shown as current. Tests that GET "/" and expect the seeded edition
# to render must seed today's date; DATE is dynamic so they keep working, and
# the symbolic `(DATE, "read")` assertions are unaffected.
DATE = datetime.now().strftime("%Y-%m-%d")


def iso_now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


@pytest.fixture
def ui(tmp_paths, monkeypatch):
    """A live loopback UI server against the sandboxed world."""
    db.migrate()
    monkeypatch.setattr(server, "GEN_JOB", server._GenJob())  # fresh job state
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    box = SimpleNamespace(
        base=f"http://127.0.0.1:{httpd.server_address[1]}", httpd=httpd
    )
    yield box
    httpd.shutdown()
    httpd.server_close()


def get(ui, path, headers=None):
    req = urllib.request.Request(ui.base + path, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.getcode(), dict(resp.headers), resp.read()
    except urllib.error.HTTPError as exc:
        return exc.code, dict(exc.headers), exc.read()


def post(ui, path, payload):
    req = urllib.request.Request(
        ui.base + path, data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.getcode(), json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8")
        try:
            return exc.code, json.loads(body)
        except ValueError:
            return exc.code, {"raw": body}


def seed_briefing(con, date=DATE, narrative=None, audio_path=None):
    slots = [{
        "slot": 1, "story_title": "Chip export controls pass",
        "summary": "S.", "item_ids": [1], "outlets": ["Outlet A", "Outlet B"],
        "matched_tags": [{"name": "AI regulation", "level": "topic"}],
        "matched_memory": [], "matched_dormant": [], "followed_analyst": False,
        "personal_score": 1.0, "world_impact": 6, "world_impact_reason": "R",
        "combined_score": 0.8, "override": False, "override_label": None,
        "corroboration_count": 2,
        "corroboration_label": "Reported by 2 named outlets",
        "wire_items_excluded": 0, "revived_threads": [],
    }]
    if narrative is None:
        from newslens import generate
        stories = [{
            "tier": "full", "headline": "Chip export controls pass",
            "lede": "The lede sentence.",
            "why_it_matters": "Concrete effects.", "watch_for": "The vote.",
            "why_label": "Why it matters", "watch_label": "Watch for",
            "my_read": None,
        }]
        inputs = {"slots": slots, "items_by_slot": {1: []}, "threads": [],
                  "prior_ctx": None, "continuity_status": "none",
                  "window_meta": None, "corroboration": {}}
        narrative = generate.assemble_narrative(date, "A", stories, inputs)
    con.execute(
        "INSERT INTO briefings (date, story_slots, corroboration_labels,"
        " narrative_text, audio_file_path, generated_at)"
        " VALUES (?, ?, ?, ?, ?, ?)",
        (date, json.dumps(slots),
         json.dumps({"standing_caveat": ranking.CORROBORATION_CAVEAT,
                     "per_story": []}),
         narrative, audio_path, iso_now()),
    )
    con.commit()
    return slots


def event_rows(con):
    return con.execute(
        "SELECT date, kind, occurred_at FROM consumption_events ORDER BY id"
    ).fetchall()


# --- binding + route discipline --------------------------------------------------------

def test_server_binds_loopback_only_by_source():
    """A 0.0.0.0 regression is a security finding: generation spend and
    thread editing must never be network-reachable."""
    src = (PROTOTYPE_ROOT / "src" / "newslens" / "server.py").read_text(encoding="utf-8")
    assert '("127.0.0.1", port)' in src
    assert "0.0.0.0" not in src
    assert "ThreadingHTTPServer((\"127.0.0.1\"" in src.replace("'", '"')


def test_unknown_routes_and_methods_are_sane(ui):
    code, _, body = get(ui, "/definitely/not/here")
    assert code == 404
    code, obj = post(ui, "/api/definitely-not", {})
    assert code == 404 and obj == {"ok": False, "error": "no such endpoint"}


def test_empty_state_renders_generate_copy_and_logs_nothing(ui):
    code, _, body = get(ui, "/")
    text = body.decode("utf-8")
    assert code == 200
    assert "No edition has been generated" in text
    con = db.connect()
    try:
        assert event_rows(con) == []  # an empty state is not a read
    finally:
        con.close()


def test_never_existed_date_renders_empty_copy_not_a_read(ui):
    """The implementer-flagged untested case: ?date= on a date that never
    existed renders the generate-empty copy and logs NO consumption."""
    con = db.connect()
    seed_briefing(con)
    con.close()
    code, _, body = get(ui, "/?date=1999-01-01")
    assert code == 200
    assert "No edition has been generated" in body.decode("utf-8")
    code2, _, body2 = get(ui, "/?date=not-a-date")  # malformed -> today (seeded)
    assert code2 == 200 and "Chip export controls pass" in body2.decode("utf-8")
    con = db.connect()
    try:
        rows = event_rows(con)
    finally:
        con.close()
    # only the malformed->today render was a real briefing view
    assert [(r["date"], r["kind"]) for r in rows] == [(DATE, "read")]


def test_today_only_default_hides_stale_edition(ui):
    """NL-11: a past-dated edition is never shown as today's — Today defaults
    to the empty state (with Generate) and logs no read; the stale edition is
    still reachable by explicit ?date= (archive / no-JS deep link)."""
    con = db.connect()
    seed_briefing(con, date="2020-01-02")  # unambiguously not today
    con.close()
    _, _, body = get(ui, "/")
    text = body.decode("utf-8")
    assert "No edition has been generated" in text        # empty copy...
    # ...and the stale edition is not shown AS today's. Scope to the Today view:
    # the v7-M2 archive list DOES surface every edition's lead headline (§8, the
    # list-below is the archive's primary rendering), which is not "today dressed
    # as current" — the NL-11 guarantee is about the Today surface.
    today_view = text.split('id="view-today"')[1].split('id="view-following"')[0]
    assert "Chip export controls pass" not in today_view
    con = db.connect()
    try:
        assert event_rows(con) == []                       # empty state is not a read
    finally:
        con.close()
    _, _, body2 = get(ui, "/?date=2020-01-02")             # but explicit date reaches it
    assert "Chip export controls pass" in body2.decode("utf-8")


def test_api_status_starts_idle(ui):
    code, _, body = get(ui, "/api/status")
    assert code == 200
    assert json.loads(body) == {"state": "idle", "error": ""}


# --- the generation job: never two concurrent paid runs -----------------------------------

def test_generate_endpoint_is_single_flight(ui, monkeypatch):
    from newslens import generate as generate_mod

    release = threading.Event()
    runs = []

    def slow_fake_generate(*a, **kw):
        runs.append(1)
        release.wait(timeout=30)

    monkeypatch.setattr(generate_mod, "run_generate", slow_fake_generate)
    monkeypatch.setattr(config, "load_env", lambda *a, **kw: None)

    code, obj = post(ui, "/api/generate", {})
    assert code == 200 and obj == {"ok": True, "detail": "started"}
    # Double-tap while running: refused, no second run, no second spend.
    for _ in range(3):
        code, obj = post(ui, "/api/generate", {})
        assert obj == {"ok": True, "detail": "already running"}
    _, _, body = get(ui, "/api/status")
    assert json.loads(body)["state"] == "running"
    release.set()
    deadline = time.time() + 10
    while time.time() < deadline:
        _, _, body = get(ui, "/api/status")
        if json.loads(body)["state"] == "done":
            break
        time.sleep(0.05)
    assert json.loads(body)["state"] == "done"
    assert len(runs) == 1  # exactly ONE pipeline run, ever


def test_generate_failure_surfaces_in_status(ui, monkeypatch):
    from newslens import generate as generate_mod

    def failing(*a, **kw):
        raise generate_mod.GenerateError("OPENAI_API_KEY not set — etc")

    monkeypatch.setattr(generate_mod, "run_generate", failing)
    monkeypatch.setattr(config, "load_env", lambda *a, **kw: None)
    post(ui, "/api/generate", {})
    deadline = time.time() + 10
    state = {}
    while time.time() < deadline:
        _, _, body = get(ui, "/api/status")
        state = json.loads(body)
        if state["state"] == "error":
            break
        time.sleep(0.05)
    assert state["state"] == "error"
    assert "OPENAI_API_KEY not set" in state["error"]


# --- consumption events end-to-end ----------------------------------------------------------

def test_reads_log_raw_one_per_view(ui):
    con = db.connect()
    seed_briefing(con)
    con.close()
    for _ in range(3):
        get(ui, "/")
    con = db.connect()
    try:
        rows = event_rows(con)
    finally:
        con.close()
    assert [(r["date"], r["kind"]) for r in rows] == [(DATE, "read")] * 3


def _make_wav(path, n_frames=4000):
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(8000)
        w.writeframes(b"\x00\x01" * n_frames)


def test_listen_dedup_three_plays_one_row_range_bursts_ignored(ui):
    """The implementer's 3-plays demo as e2e: byte-0 opens log at most one
    listen per (briefing-date, calendar-day); Range continuation bursts log
    nothing; a next-day play logs again."""
    wav = paths.DATA_DIR / "briefings" / f"{DATE}.wav"
    _make_wav(wav)
    con = db.connect()
    seed_briefing(con, audio_path=str(wav))
    con.close()

    for _ in range(3):  # three full plays today
        code, headers, body = get(ui, f"/audio/{DATE}.wav")
        assert code == 200 and len(body) == wav.stat().st_size
        # the player's buffering: mid-file Range bursts
        code, headers, _ = get(ui, f"/audio/{DATE}.wav",
                               headers={"Range": "bytes=1000-2000"})
        assert code == 206
        assert headers["Content-Range"] == f"bytes 1000-2000/{wav.stat().st_size}"

    con = db.connect()
    try:
        rows = [r for r in event_rows(con) if r["kind"] == "listen"]
        assert len(rows) == 1  # deduped: one listen row for today
        # Roll that row to yesterday: a fresh play today logs a NEW row.
        con.execute(
            "UPDATE consumption_events SET occurred_at ="
            " datetime(occurred_at, '-1 day') WHERE kind = 'listen'"
        )
        con.commit()
    finally:
        con.close()
    get(ui, f"/audio/{DATE}.wav")
    con = db.connect()
    try:
        listens = [r for r in event_rows(con) if r["kind"] == "listen"]
    finally:
        con.close()
    assert len(listens) == 2  # one per calendar day


def test_range_zero_start_counts_as_a_play_but_still_dedups(ui):
    wav = paths.DATA_DIR / "briefings" / f"{DATE}.wav"
    _make_wav(wav)
    con = db.connect()
    seed_briefing(con, audio_path=str(wav))
    con.close()
    code, headers, _ = get(ui, f"/audio/{DATE}.wav",
                           headers={"Range": "bytes=0-499"})
    assert code == 206
    get(ui, f"/audio/{DATE}.wav", headers={"Range": "bytes=0-499"})
    con = db.connect()
    try:
        listens = [r for r in event_rows(con) if r["kind"] == "listen"]
    finally:
        con.close()
    assert len(listens) == 1


def test_audio_416_and_suffix_ranges(ui):
    wav = paths.DATA_DIR / "briefings" / f"{DATE}.wav"
    _make_wav(wav)
    size = wav.stat().st_size
    con = db.connect()
    seed_briefing(con, audio_path=str(wav))
    con.close()
    code, headers, _ = get(ui, f"/audio/{DATE}.wav",
                           headers={"Range": f"bytes={size + 10}-"})
    assert code == 416 and headers["Content-Range"] == f"bytes */{size}"
    code, headers, body = get(ui, f"/audio/{DATE}.wav",
                              headers={"Range": "bytes=-100"})
    assert code == 206 and len(body) == 100
    con = db.connect()
    try:  # neither the 416 nor the suffix tail was a play
        assert [r for r in event_rows(con) if r["kind"] == "listen"] == []
    finally:
        con.close()


def test_audio_missing_is_404(ui):
    code, _, _ = get(ui, "/audio/2026-01-01.wav")
    assert code == 404


def test_generation_never_writes_consumption_events():
    """The falsifier's premise, statically enforced: the generation pipeline
    has no path into consumption_events."""
    for mod in ("generate.py", "ranking.py", "ingest.py"):
        src = (PROTOTYPE_ROOT / "src" / "newslens" / mod).read_text(encoding="utf-8")
        assert "consumption_events" not in src
        assert "from . import events" not in src and "import events" not in src


# --- sources.yaml line surgery -----------------------------------------------------------

REPLICA = """\
# =============================================================================
# NewsLens sources & interests — this file is yours to edit.
# (fixture replica: structure + comment styles mirror the real file)
# =============================================================================

sources:
  # --- Wire (reference_only per principal) ------------------------------------
  - name: Reuters
    tier: reference_only
    wire_syndication: true
    note: "wire copy reaches us via other feeds"
  - name: The Hill
    rss_url: https://thehill.com/feed/
  - name: Chartbook (Adam Tooze)
    # principal-followed (comment inside the entry survives surgery)
    rss_url: https://adamtooze.substack.com/feed
    followed_analyst: true
    note: "followed analyst"
  - name: Noahpinion Legacy   # inline name comment (BUG-9 fixture)
    rss_url: https://legacy.example/feed
    followed_analyst: true
  - name: Inline Enabled Writer
    rss_url: https://inline.example/feed
    followed_analyst: true
    enabled: true  # keep while testing the beta feed
    # enabled: true was flipped manually once — decoy comment (ride 25)

# --- Interests -----------------------------------------------------------------
interests:
  broad:
    - economy          # inline comment: survives surgery
    - technology
  granular:
    - AI regulation    # keep an eye on the EU timeline
    - semiconductor supply chains

settings:
  threads_steer_selection: false
"""


@pytest.fixture
def replica(tmp_paths):
    paths.SOURCES_FILE.write_text(REPLICA, encoding="utf-8")
    return paths.SOURCES_FILE


def _comment_lines(text):
    return [ln for ln in text.splitlines() if ln.strip().startswith("#")
            or "  #" in ln]


def test_topic_add_and_remove_round_trip_preserving_comments(replica):
    before_comments = _comment_lines(REPLICA)
    ok, msg = server.topic_add("Quantum computing", "specific")
    assert ok, msg
    text = replica.read_text(encoding="utf-8")
    assert "    - Quantum computing" in text
    cfg = config.load_sources()
    assert cfg.problems == [] and "Quantum computing" in cfg.interests_granular
    for c in before_comments:
        assert c in text  # every comment survived, inline ones included
    ok, msg = server.topic_remove("Quantum computing")
    assert ok, msg
    text2 = replica.read_text(encoding="utf-8")
    assert "Quantum computing" not in text2
    for c in before_comments:
        assert c in text2
    assert "AI regulation    # keep an eye on the EU timeline" in text2


def test_topic_remove_handles_inline_comments_on_the_target(replica):
    ok, msg = server.topic_remove("AI regulation")
    assert ok, msg
    text = replica.read_text(encoding="utf-8")
    assert "AI regulation" not in text
    assert "- semiconductor supply chains" in text  # neighbors intact


def test_topic_add_duplicate_and_unicode(replica):
    ok, msg = server.topic_add("economy", "broad")
    assert not ok and "already" in msg
    ok, msg = server.topic_add("Türkiye–EU accession", "specific")
    assert ok, msg
    assert "Türkiye–EU accession" in config.load_sources().interests_granular
    ok, _ = server.topic_remove("Türkiye–EU accession")
    assert ok


def test_exception_branch_reverts_byte_identical(replica, monkeypatch):
    """FLIPPED per the gate fixes: structural names are now intercepted by
    _bad_name BEFORE surgery, so the revert path needs a validation failure
    that reaches it. Branch 1: load_sources RAISES -> byte-identical revert."""
    before = replica.read_text(encoding="utf-8")

    def boom():
        raise config.SourcesParseError("synthetic parse explosion")

    monkeypatch.setattr(config, "load_sources", boom)
    ok, msg = server.topic_add("Perfectly Fine Topic", "broad")
    assert not ok and "reverted" in msg and "synthetic parse explosion" in msg
    assert replica.read_text(encoding="utf-8") == before


def test_problems_state_branch_reverts_byte_identical(replica, monkeypatch):
    """Branch 2 (gate finding 1): load_sources returns problems WITHOUT
    raising — shipping that file would brick later runs, so it reverts too,
    naming the problem."""
    before = replica.read_text(encoding="utf-8")

    def problematic():
        cfg = config.SourcesConfig()
        cfg.problems.append("synthetic problem: `tier` must be one of ...")
        return cfg

    monkeypatch.setattr(config, "load_sources", problematic)
    ok, msg = server.topic_add("Another Fine Topic", "broad")
    assert not ok and "reverted" in msg and "synthetic problem" in msg
    assert replica.read_text(encoding="utf-8") == before


def test_surgery_on_comments_only_file_fails_without_writing(tmp_paths):
    text = "# just comments\n# nothing else\n"
    paths.SOURCES_FILE.write_text(text, encoding="utf-8")
    ok, msg = server.topic_add("anything", "broad")
    assert not ok and "could not locate" in msg
    assert paths.SOURCES_FILE.read_text(encoding="utf-8") == text


def test_writer_add_validates_and_dedups(replica):
    ok, msg = server.writer_add("", "ftp://nope")
    assert not ok and "http(s)" in msg
    ok, msg = server.writer_add("Chartbook (Adam Tooze)", "https://new.example/feed")
    assert not ok and "already in your sources" in msg
    ok, msg = server.writer_add("Noahpinion", "https://www.noahpinion.blog/feed")
    assert ok, msg
    cfg = config.load_sources()
    assert cfg.problems == []
    added = [s for s in cfg.sources if s.name == "Noahpinion"]
    assert added and added[0].followed_analyst is True
    text = replica.read_text(encoding="utf-8")
    assert text.index("Noahpinion") < text.index("interests:")


def test_writer_unfollow_disables_the_whole_entry(replica):
    """Never silent collection: unfollow flips followed_analyst false AND
    disables the source entirely."""
    ok, msg = server.writer_remove("Chartbook (Adam Tooze)")
    assert ok, msg
    cfg = config.load_sources()
    assert cfg.problems == []
    entry = next(s for s in cfg.sources if s.name == "Chartbook (Adam Tooze)")
    assert entry.followed_analyst is False
    assert entry.enabled is False
    assert not entry.fetchable
    text = replica.read_text(encoding="utf-8")
    assert "# principal-followed" in text  # the entry's comment lives


def test_BUG9_unfollow_must_tolerate_inline_name_comments(replica):
    """KNOWN-RED (BUG-9, low) — self-contained acceptance: writer_remove's
    name-line regex requires end-of-line after the name (`\\s*$`), so an
    entry whose `- name:` line carries an inline comment cannot be
    unfollowed — while topic_remove already tolerates `(#.*)?$`. The file is
    the principal's to comment; a comment must never make a source
    un-unfollowable (silent collection risk). Fix: same trailing-comment
    tolerance as topic_remove; this test goes green then."""
    ok, msg = server.writer_remove("Noahpinion Legacy")
    assert ok, msg
    entry = next(s for s in config.load_sources().sources
                 if s.name == "Noahpinion Legacy")
    assert entry.enabled is False and entry.followed_analyst is False


def test_writer_remove_non_followed_is_refused(replica):
    ok, msg = server.writer_remove("The Hill")
    assert not ok and "not a followed writer" in msg


# --- shared thread verbs: UI == CLI ---------------------------------------------------------

def test_follow_stamps_reference_and_second_click_unfollows(ui):
    con = db.connect()
    seed_briefing(con)
    briefing_id = con.execute(
        "SELECT id FROM briefings WHERE date = ?", (DATE,)).fetchone()["id"]
    con.close()

    code, obj = post(ui, "/api/follow",
                     {"topic": "Chip exports", "briefing_date": DATE})
    assert obj["ok"] is True and obj["outcome"] == "added"
    con = db.connect()
    try:
        row = con.execute(
            "SELECT status, last_referenced_briefing_id FROM memory"
            " WHERE topic = 'Chip exports'").fetchone()
    finally:
        con.close()
    assert row["status"] == "active"
    assert row["last_referenced_briefing_id"] == briefing_id
    assert "Chip exports" in paths.MEMORY_FILE.read_text(encoding="utf-8")

    code, obj = post(ui, "/api/unfollow", {"topic": "Chip exports"})
    assert obj == {"ok": True}
    con = db.connect()
    try:
        status = con.execute(
            "SELECT status FROM memory WHERE topic = 'Chip exports'"
        ).fetchone()["status"]
    finally:
        con.close()
    assert status == "dismissed_user"
    assert "(dismissed by you" in paths.MEMORY_FILE.read_text(encoding="utf-8")


def test_follow_duplicate_topic_reports_already_active(ui):
    post(ui, "/api/follow", {"topic": "Chip exports"})
    code, obj = post(ui, "/api/follow", {"topic": "chip exports"})  # case-insens
    assert obj["ok"] is True and obj["outcome"] == "already-active"
    con = db.connect()
    try:
        n = con.execute(
            "SELECT COUNT(*) FROM memory WHERE lower(topic)='chip exports'"
        ).fetchone()[0]
    finally:
        con.close()
    assert n == 1  # no duplicate row


def test_ui_dismiss_equals_cli_dismiss(ui, capsys):
    from newslens import cli

    post(ui, "/api/follow", {"topic": "Via Web"})
    cli.main(["memory", "add", "Via CLI"])
    capsys.readouterr()

    post(ui, "/api/dismiss", {"topic": "Via Web"})
    cli.main(["memory", "dismiss", "Via CLI"])
    capsys.readouterr()

    con = db.connect()
    try:
        rows = {r["topic"]: r for r in con.execute(
            "SELECT topic, status, status_changed_at FROM memory")}
    finally:
        con.close()
    assert rows["Via Web"]["status"] == rows["Via CLI"]["status"] == "dismissed_user"
    text = paths.MEMORY_FILE.read_text(encoding="utf-8")
    dismissed_lines = [ln for ln in text.splitlines()
                       if ln.startswith("- ") and "(dismissed by you" in ln]
    assert len(dismissed_lines) == 2  # same file effect, same shape
    # (the header's explanatory prose also says "dismissed by you" — count
    # thread LINES, a lesson learned)


def test_soft_delete_removes_tracking_never_the_record(ui):
    """ADR-0010 §4: delete removes the memory row and its memory.md line;
    past briefing text is byte-untouched."""
    con = db.connect()
    seed_briefing(con, narrative="# Edition\n\nWe covered Chip exports today.\n")
    con.close()
    post(ui, "/api/follow", {"topic": "Chip exports"})
    post(ui, "/api/dismiss", {"topic": "Chip exports"})
    con = db.connect()
    narrative_before = con.execute(
        "SELECT narrative_text FROM briefings WHERE date = ?", (DATE,)
    ).fetchone()["narrative_text"]
    con.close()

    code, obj = post(ui, "/api/thread/delete", {"topic": "Chip exports"})
    assert obj == {"ok": True}
    con = db.connect()
    try:
        gone = con.execute(
            "SELECT 1 FROM memory WHERE lower(topic)='chip exports'").fetchone()
        narrative_after = con.execute(
            "SELECT narrative_text FROM briefings WHERE date = ?", (DATE,)
        ).fetchone()["narrative_text"]
    finally:
        con.close()
    assert gone is None
    assert "Chip exports" not in paths.MEMORY_FILE.read_text(encoding="utf-8")
    assert narrative_after == narrative_before  # history is immutable


def test_delete_is_guarded_dismissed_only_at_the_shared_verb(ui):
    """FLIPPED per M7 gate ruling 2: delete is the product's only
    irreversible verb, so the dismissed-only rule is enforced in
    memory.delete_thread itself (Tuple[bool, str]) — not by UI courtesy.
    Both branches pinned."""
    post(ui, "/api/follow", {"topic": "Still Active"})
    code, obj = post(ui, "/api/thread/delete", {"topic": "Still Active"})
    assert obj["ok"] is False
    assert "dismiss the thread first" in obj["error"]
    con = db.connect()
    try:
        row = con.execute(
            "SELECT status FROM memory WHERE topic='Still Active'").fetchone()
    finally:
        con.close()
    assert row is not None and row["status"] == "active"  # row survives

    post(ui, "/api/dismiss", {"topic": "Still Active"})
    code, obj = post(ui, "/api/thread/delete", {"topic": "Still Active"})
    assert obj == {"ok": True}
    con = db.connect()
    try:
        gone = con.execute(
            "SELECT 1 FROM memory WHERE topic='Still Active'").fetchone()
    finally:
        con.close()
    assert gone is None


def test_verbs_reject_separator_and_sync_first_is_loud(ui):
    code, obj = post(ui, "/api/follow", {"topic": "bad — topic"})
    assert code == 400 and obj["ok"] is False
    paths.MEMORY_FILE.write_text("# x\n## Active threads\nbroken prose\n",
                                 encoding="utf-8")
    code, obj = post(ui, "/api/dismiss", {"topic": "anything"})
    assert obj["ok"] is False and "memory.md has problems" in obj["error"]


def test_note_verb_sets_and_rejects_separator(ui):
    post(ui, "/api/follow", {"topic": "Notable"})
    code, obj = post(ui, "/api/note", {"topic": "Notable", "note": "watch the vote"})
    assert obj == {"ok": True}
    con = db.connect()
    try:
        note = con.execute(
            "SELECT principal_note FROM memory WHERE topic='Notable'"
        ).fetchone()["principal_note"]
    finally:
        con.close()
    assert note == "watch the vote"
    code, obj = post(ui, "/api/note", {"topic": "Notable", "note": "a — b"})
    assert code == 400


# --- structured stories + fallback parity ------------------------------------------------------

def _log_entry_with_stories():
    entry = {
        "date": DATE, "variant": "A", "sample": False, "status": "ok",
        "stories": [{
            "tier": "full", "headline": "Chip export controls pass",
            "lede": "The lede sentence.",
            "why_it_matters": "Concrete effects.",
            "watch_for": "The vote.",
            "why_label": "Why it matters", "watch_label": "Watch for",
            "my_read": None,
        }],
    }
    log = paths.DATA_DIR / "generation_log.jsonl"
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_text(json.dumps(entry) + "\n", encoding="utf-8")


def test_structured_and_fallback_paths_render_the_same_shape(ui):
    con = db.connect()
    seed_briefing(con)
    con.close()
    _log_entry_with_stories()
    _, _, body = get(ui, "/")
    structured = body.decode("utf-8")

    (paths.DATA_DIR / "generation_log.jsonl").unlink()  # pre-M7 edition
    _, _, body = get(ui, "/")
    fallback = body.decode("utf-8")

    for page, label in ((structured, "structured"), (fallback, "fallback")):
        assert "Chip export controls pass" in page, label
        assert "Why it matters" in page, label
        assert "The lede sentence." in page, label
        # Trust furniture ALWAYS from slots, on both paths:
        assert "Reported by 2 named outlets" in page, label
        assert "Outlet A" in page, label
        assert "Here for" in page, label


# --- a11y: durable markup pins ------------------------------------------------------------------

def test_a11y_structural_markers(ui):
    paths.SOURCES_FILE.write_text(REPLICA, encoding="utf-8")  # removable tokens
    con = db.connect()
    seed_briefing(con)
    con.close()
    post(ui, "/api/follow", {"topic": "Chip exports"})
    _, _, body = get(ui, "/")
    page = body.decode("utf-8")
    # Dialogs are real dialogs:
    assert page.count('role="dialog"') >= 5
    assert 'aria-modal="true"' in page
    assert 'aria-labelledby="popup-delete-title"' in page
    # The dark toggle is a labeled switch:
    assert 'role="switch"' in page and 'aria-label="Dark mode"' in page
    # Follow buttons carry pressed state; removes are labeled:
    assert "aria-pressed" in page
    assert 'aria-label="Remove' in page or "aria-label=\"Stop" in page
    # Escape + focus wiring exists in the shipped JS:
    assert "Escape" in webui.JS
    assert ".focus()" in webui.JS
# --- M7 gate-fix pins ------------------------------------------------------------------------

def test_csrf_guard_rejects_non_json_content_types(ui):
    """Cross-origin form posts can't be application/json without a preflight
    — the 415 gate keeps drive-by POSTs off the mutation endpoints, and the
    handler never runs."""
    for ctype in ("text/plain", "application/x-www-form-urlencoded"):
        req = urllib.request.Request(
            ui.base + "/api/follow",
            data=json.dumps({"topic": "Drive By"}).encode("utf-8"),
            headers={"Content-Type": ctype},
        )
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                code, body = resp.getcode(), resp.read()
        except urllib.error.HTTPError as exc:
            code, body = exc.code, exc.read()
        assert code == 415
        assert json.loads(body) == {"ok": False, "error": "unsupported content type"}
    con = db.connect()
    try:
        assert con.execute(
            "SELECT 1 FROM memory WHERE topic='Drive By'").fetchone() is None
    finally:
        con.close()
    # The correct content type still proceeds (all other POST tests ride it).
    code, obj = post(ui, "/api/follow", {"topic": "Legit"})
    assert obj["ok"] is True


@pytest.mark.parametrize(
    "name, fragment",
    [
        ("bad: colon", "can't contain ':'"),
        ("two\nlines", "line breaks"),
        ("# looks-like-comment", "start with '#'"),
    ],
)
def test_bad_name_precheck_rejects_before_surgery(replica, name, fragment):
    before = replica.read_text(encoding="utf-8")
    ok, msg = server.topic_add(name, "broad")
    assert not ok and fragment in msg
    ok2, msg2 = server.writer_add(name, "https://x.example/feed")
    assert not ok2 and fragment in msg2
    assert replica.read_text(encoding="utf-8") == before  # untouched, both paths


def test_yaml_writes_are_atomic_tmp_plus_replace(replica):
    """Gate ruling 1: no torn writes — the mechanism is tmp + os.replace,
    and no tmp sibling survives a successful edit."""
    src = (PROTOTYPE_ROOT / "src" / "newslens" / "server.py").read_text(encoding="utf-8")
    assert "os.replace(tmp, path)" in src
    ok, _ = server.topic_add("Atomic Topic", "broad")
    assert ok
    leftovers = list(paths.SOURCES_FILE.parent.glob("*.tmp"))
    assert leftovers == []
    assert "Atomic Topic" in config.load_sources().interests_broad


def test_reads_are_honest_during_running_and_error_states(ui, monkeypatch):
    """Gate finding 4: a read event means the briefing BODY was shown — the
    running/error panels are not reads."""
    from newslens import generate as generate_mod

    con = db.connect()
    seed_briefing(con)
    con.close()
    release = threading.Event()
    monkeypatch.setattr(generate_mod, "run_generate",
                        lambda *a, **kw: release.wait(timeout=30))
    monkeypatch.setattr(config, "load_env", lambda *a, **kw: None)
    post(ui, "/api/generate", {})
    code, _, body = get(ui, "/")  # running panel replaces the briefing
    assert "Generating" in body.decode("utf-8")
    con = db.connect()
    try:
        assert event_rows(con) == []  # not a read
    finally:
        con.close()
    release.set()
    deadline = time.time() + 10
    while time.time() < deadline:
        _, _, sbody = get(ui, "/api/status")
        if json.loads(sbody)["state"] == "done":
            break
        time.sleep(0.05)
    get(ui, "/")  # done + briefing shown -> a real read
    con = db.connect()
    try:
        rows = event_rows(con)
    finally:
        con.close()
    assert [(r["date"], r["kind"]) for r in rows] == [(DATE, "read")]


def test_follow_revive_stamps_the_edition_cli_revive_does_not(ui, capsys):
    """Gate finding 8: following a story whose thread is dormant/dismissed
    revives it AND stamps the edition it came from; the CLI revive (no
    edition context) leaves the stamp as it was."""
    from newslens import cli

    con = db.connect()
    seed_briefing(con)
    briefing_id = con.execute(
        "SELECT id FROM briefings WHERE date = ?", (DATE,)).fetchone()["id"]
    con.execute(
        "INSERT INTO memory (topic, status, status_changed_at, created_at,"
        " updated_at) VALUES ('Old Thread', 'dismissed_user', ?, ?, ?)",
        (iso_now(), iso_now(), iso_now()),
    )
    con.commit()
    con.close()

    code, obj = post(ui, "/api/follow",
                     {"topic": "Old Thread", "briefing_date": DATE})
    assert obj["ok"] is True and obj["outcome"] == "revived"
    con = db.connect()
    try:
        row = con.execute(
            "SELECT status, last_referenced_briefing_id FROM memory"
            " WHERE topic='Old Thread'").fetchone()
    finally:
        con.close()
    assert row["status"] == "active"
    assert row["last_referenced_briefing_id"] == briefing_id  # stamped

    post(ui, "/api/dismiss", {"topic": "Old Thread"})
    cli.main(["memory", "add", "Old Thread"])  # CLI revive: no edition context
    capsys.readouterr()
    con = db.connect()
    try:
        row = con.execute(
            "SELECT status, last_referenced_briefing_id FROM memory"
            " WHERE topic='Old Thread'").fetchone()
    finally:
        con.close()
    assert row["status"] == "active"
    assert row["last_referenced_briefing_id"] == briefing_id  # unchanged


def test_settings_engine_display_follows_the_config(ui):
    # P3.1 item 4 pin FLIP (mechanical, intended): the default engine is now
    # openai (ear-test ruling 2026-07-06) — the display still follows the
    # CONFIG (the M7 gate contract), poles swapped.
    con = db.connect()
    seed_briefing(con)
    con.close()
    _, _, body = get(ui, "/")
    assert "OpenAI gpt-4o-mini-tts (~$0.015/min)" in body.decode("utf-8")  # default
    paths.SOURCES_FILE.write_text(
        "sources:\n  - name: A\n    rss_url: https://a.invalid/f\n"
        "settings:\n  tts_engine: kokoro\n",
        encoding="utf-8",
    )
    _, _, body = get(ui, "/")
    page = body.decode("utf-8")
    assert "Kokoro (local, $0/episode)" in page
    assert "OpenAI gpt-4o-mini-tts (~$0.015/min)" not in page
# --- M8 final-pass pins ------------------------------------------------------------------------

def _raw_http(ui, request_bytes):
    """Send a raw request over loopback (lets us omit the Host header)."""
    import socket as socket_mod

    host, port = "127.0.0.1", int(ui.base.rsplit(":", 1)[1])
    s = socket_mod.create_connection((host, port), timeout=10)
    try:
        s.sendall(request_bytes)
        data = b""
        while True:
            chunk = s.recv(65536)
            if not chunk:
                break
            data += chunk
    finally:
        s.close()
    return data


def test_ride22_host_allowlist_on_get_and_post(ui):
    con = db.connect()
    seed_briefing(con)
    con.close()
    # Hostile Host: 403 on GET…
    code, _, body = get(ui, "/", headers={"Host": "evil.example"})
    assert code == 403 and b"Forbidden" in body
    # …and on POST, independently of the CSRF gate (correct content type).
    req = urllib.request.Request(
        ui.base + "/api/follow",
        data=json.dumps({"topic": "Rebound"}).encode("utf-8"),
        headers={"Content-Type": "application/json", "Host": "evil.example"},
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            code, body = resp.getcode(), resp.read()
    except urllib.error.HTTPError as exc:
        code, body = exc.code, exc.read()
    assert code == 403
    assert json.loads(body) == {"ok": False, "error": "forbidden host"}
    con = db.connect()
    try:
        assert con.execute(
            "SELECT 1 FROM memory WHERE topic='Rebound'").fetchone() is None
    finally:
        con.close()
    # Localhost names pass, any port:
    for host in ("localhost:9999", "127.0.0.1:1", "[::1]:8484"):
        code, _, _ = get(ui, "/", headers={"Host": host})
        assert code == 200, host
    # No reads were logged by the hostile GET (it never rendered):
    con = db.connect()
    try:
        n = con.execute("SELECT COUNT(*) FROM consumption_events").fetchone()[0]
    finally:
        con.close()
    assert n == 3  # exactly the three allowed-host renders above


def test_ride22_absent_host_http10_is_allowed(ui):
    con = db.connect()
    seed_briefing(con)
    con.close()
    raw = _raw_http(ui, b"GET / HTTP/1.0\r\n\r\n")
    assert raw.startswith(b"HTTP/1.0 200") or raw.startswith(b"HTTP/1.1 200")


def test_ride22_layered_gates_hold_independently(ui):
    """Belt AND suspenders: good Host + bad content type -> 415; bad Host +
    good content type -> 403. Neither gate substitutes for the other."""
    req = urllib.request.Request(
        ui.base + "/api/follow", data=b"topic=x",
        headers={"Content-Type": "text/plain", "Host": "localhost"},
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            code = resp.getcode()
    except urllib.error.HTTPError as exc:
        code = exc.code
    assert code == 415


@pytest.mark.filterwarnings("ignore::pytest.PytestUnhandledThreadExceptionWarning")
def test_ride24_basexception_never_strands_running(ui, monkeypatch):
    """The finally-guard: a BaseException in the job thread lands state at
    error with the abnormal-exit message — the UI never spins forever."""
    from newslens import generate as generate_mod

    def rude(*a, **kw):
        raise KeyboardInterrupt  # BaseException, skips `except Exception`

    monkeypatch.setattr(generate_mod, "run_generate", rude)
    monkeypatch.setattr(config, "load_env", lambda *a, **kw: None)
    post(ui, "/api/generate", {})
    deadline = time.time() + 10
    state = {}
    while time.time() < deadline:
        _, _, body = get(ui, "/api/status")
        state = json.loads(body)
        if state["state"] != "running":
            break
        time.sleep(0.05)
    assert state["state"] == "error"
    assert "exited abnormally" in state["error"]


def test_ride25_enabled_rewrite_is_key_anchored(replica):
    """Unfollow flips `enabled: true  # comment` preserving the comment;
    a comment line merely MENTIONING enabled: is never touched."""
    ok, msg = server.writer_remove("Inline Enabled Writer")
    assert ok, msg
    text = replica.read_text(encoding="utf-8")
    assert "enabled: false  # keep while testing the beta feed" in text
    assert "# enabled: true was flipped manually once — decoy comment (ride 25)" in text
    cfg = config.load_sources()
    entry = next(s for s in cfg.sources if s.name == "Inline Enabled Writer")
    assert entry.enabled is False and entry.followed_analyst is False


ERROR_PANEL_SENTENCE = (
    "No half-written edition ever goes out: a failure before the save\n"
    "     publishes nothing; one during file export after the save leaves the\n"
    "     saved edition intact."
)


def test_ride23_error_panel_wording_in_both_failure_positions(ui):
    """The recovery sentence must be TRUE and identical whether the failure
    happened with no edition at all or on a day that already has one."""
    server.GEN_JOB.state = "error"
    server.GEN_JOB.error = "synthetic failure for the wording pin"
    # Position 1: no briefing row exists.
    _, _, body = get(ui, "/")
    page1 = body.decode("utf-8")
    assert "Today’s edition failed" in page1
    assert ERROR_PANEL_SENTENCE in page1
    # Position 2: a briefing row exists (the panel replaces it).
    con = db.connect()
    seed_briefing(con)
    con.close()
    _, _, body = get(ui, "/")
    page2 = body.decode("utf-8")
    assert ERROR_PANEL_SENTENCE in page2
    # the panel replaced the Today edition body — scope the check to the Today
    # view (the v7-M2 archive list surfaces every edition's lead headline, §8).
    today_view = page2.split('id="view-today"')[1].split('id="view-following"')[0]
    assert "Chip export controls pass" not in today_view
    # And neither render logged a read (read-honesty holds here too).
    con = db.connect()
    try:
        assert con.execute("SELECT COUNT(*) FROM consumption_events").fetchone()[0] == 0
    finally:
        con.close()


def test_ride26_dead_branch_is_gone():
    src = (PROTOTYPE_ROOT / "src" / "newslens" / "server.py").read_text(encoding="utf-8")
    assert "not reachable" not in src


def test_item27_furniture_contract_through_build_page(ui):
    """ACCEPTED (item 27): the drift-guard for the trust surface. A synthetic
    briefing exercising every code-owned furniture element must render all of
    them from SLOT data, whatever webui evolves into."""
    slots = [
        {
            "slot": 1, "story_title": "Tracked story", "summary": "S.",
            "item_ids": [1], "outlets": ["Outlet A", "Outlet B"],
            "matched_tags": [{"name": "AI regulation", "level": "topic"}],
            "matched_memory": ["Iran War"], "matched_dormant": [],
            "followed_analyst": False, "personal_score": 1.0,
            "world_impact": 6, "world_impact_reason": "R",
            "combined_score": 0.8, "override": False, "override_label": None,
            "corroboration_count": 2,
            "corroboration_label": "Reported by 2 named outlets",
            "wire_items_excluded": 1,
            "revived_threads": [{"topic": "Iran War", "last_covered": "2026-07-01"}],
        },
        {
            "slot": 2, "story_title": "Override story", "summary": "S.",
            "item_ids": [2], "outlets": ["Solo"],
            "matched_tags": [], "matched_memory": [], "matched_dormant": [],
            "followed_analyst": False, "personal_score": 0.0,
            "world_impact": 9, "world_impact_reason": "Global systemic thing",
            "combined_score": 0.4, "override": True,
            "override_label": ranking.OVERRIDE_LABEL_PREFIX + "Global systemic thing.",
            "corroboration_count": 1,
            "corroboration_label": "Reported by 1 named outlet",
            "wire_items_excluded": 0, "revived_threads": [],
        },
    ]
    # The narrative must parse to story cards (same lesson as the parity
    # test): build it with the REAL assembler so the fallback recovers it.
    from newslens import generate
    stories = [
        {"tier": "full", "headline": "Tracked story",
         "lede": "We last covered 2026-07-01 this thread; new movement today.",
         "why_it_matters": "Concrete effects.", "watch_for": "The vote.",
         "why_label": "Why it matters", "watch_label": "Watch for",
         "my_read": None},
        {"tier": "medium", "headline": "Override story",
         "lede": "A global development outside your tags.",
         "why_it_matters": "Systemic consequence.", "watch_for": "The summit.",
         "why_label": "The stakes", "watch_label": "What happens next",
         "my_read": None},
    ]
    inputs = {"slots": slots, "items_by_slot": {1: [], 2: []}, "threads": [],
              "prior_ctx": None, "continuity_status": "none",
              "window_meta": None, "corroboration": {}}
    narrative = generate.assemble_narrative(DATE, "A", stories, inputs)
    con = db.connect()
    con.execute(
        "INSERT INTO briefings (date, story_slots, corroboration_labels,"
        " narrative_text, generated_at) VALUES (?, ?, ?, ?, ?)",
        (DATE, json.dumps(slots),
         json.dumps({"standing_caveat": ranking.CORROBORATION_CAVEAT,
                     "per_story": []}),
         narrative, iso_now()),
    )
    con.execute(
        "INSERT INTO memory (topic, status, created_at, updated_at)"
        " VALUES ('Iran War', 'active', ?, ?)", (iso_now(), iso_now()),
    )
    con.commit()
    con.close()
    _, _, body = get(ui, "/")
    page = body.decode("utf-8")
    # 1. Tracked marker (from matched_memory + active thread):
    assert "Tracked ongoing story" in page
    # 2. Override note (canonical label text, from the slot):
    assert "Outside your interests" in page or "outside your" in page.lower()
    # 3. Meta-footnote: corroboration + outlets + provenance, from slots:
    assert "Reported by 2 named outlets" in page
    assert "Outlet A" in page
    assert "Here for" in page
    # 4. Disclosure trigger: the revival back-reference reaches the surface:
    assert "2026-07-01" in page
    # 5. Follow affordance with pressed state (on the NON-tracked story — the
    # tracked story drops the redundant button per the NL-11 coexistence rule):
    assert "aria-pressed" in page
    # 6. Coexistence (NL-11 rule, NL-58 merged control): the tracked story
    # shows only its marker STATE; the override story (no thread match) keeps a
    # follow toggle. v7/NL-65 flip — WAS: the follow control + "full picture"
    # shared one .story-affordances row under the title; NOW: the follow control
    # sits alone in the under-title .deck (full picture moved to the story
    # bottom). One .deck per story; the marker/button split is unchanged.
    today = page[page.index('id="view-today"'):page.index('id="view-following"')]
    assert today.count('class="tracked-marker"') == 1     # the tracked story
    assert today.count('class="follow-story-btn') == 1    # only the override story
    assert today.count('class="deck"') == 2               # under-title control row per story
    assert 'class="glance"' not in page                   # glance removed (NL-11)


# =====================================================================
# NL-11 — UI/UX v2: suggestion component, view-preserving verbs, archive
# in-place, follow coexistence, empty-state default. New wiring is born
# with the tests only it can turn green (claims-of-wiring proof rule).
# =====================================================================

def test_following_uses_suggestion_component_not_datalist(ui):
    _, _, body = get(ui, "/")
    page = body.decode("utf-8")
    following = page[page.index('id="view-following"'):page.index('id="view-archive"')]
    assert "<datalist" not in page                    # native datalist gone everywhere
    # NL-68 item 10 (DECISIONS 2026-07-16): 'Follow a new story' became a THIRD
    # suggest combobox (suggestions-only) — WAS 2 (topics + writers).
    assert following.count('class="suggest"') == 3    # topics + writers + story-follow
    assert following.count('data-suggest-only="1"') == 1   # only the story follow is constrained
    assert 'role="combobox"' in following
    assert 'class="suggest-data"' in following         # JSON payload embedded
    assert 'role="listbox"' in following and "hidden></ul>" in following  # hidden until JS
    for token in ("function suggestKeydown", "function suggestInput",
                  "function suggestChoose", "ArrowDown", "ArrowUp",
                  "'Enter'", "'Escape'"):
        assert token in webui.JS                       # keyboard flow is wired


def test_topic_suggestions_exclude_already_followed(tmp_paths):
    db.migrate()
    con = db.connect()
    try:
        con.execute(
            "INSERT INTO briefings (date, story_slots) VALUES (?, ?)",
            ("2026-07-01", json.dumps([{"slot": "1", "matched_tags":
                [{"name": "Economy"}, {"name": "Fusion Power"}]}])))
        con.commit()
        cfg = SimpleNamespace(interests_broad=["Economy"], interests_granular=[],
                              sources=[], followed_analyst_sources=[])
        sugg = {o["v"] for o in server._topic_suggestions(con, cfg)}
        assert "Fusion Power" in sugg                  # unfollowed coverage tag -> offered
        assert "Economy" not in sugg                   # already followed -> excluded
    finally:
        con.close()


def test_archive_rows_open_in_place_with_no_js_fallback(ui):
    con = db.connect()
    seed_briefing(con, date="2020-01-02")
    con.close()
    _, _, body = get(ui, "/")
    page = body.decode("utf-8")
    archive = page[page.index('id="view-archive"'):page.index('id="edition-mount"')]
    assert "openEdition('2020-01-02', event)" in archive   # JS opens in-place
    assert 'href="/?date=2020-01-02"' in archive           # no-JS graceful fallback
    assert '<div id="edition-mount">' in page              # the in-place mount
    assert "function openEdition" in webui.JS and "function backToArchive" in webui.JS


def test_edition_fragment_renders_in_place_and_logs_a_read(ui):
    con = db.connect()
    seed_briefing(con, date="2020-01-02")
    con.close()
    code, _, body = get(ui, "/edition?date=2020-01-02")
    assert code == 200
    frag = body.decode("utf-8")
    assert "Back to Archive" in frag and 'id="view-edition"' in frag
    assert "Chip export controls pass" in frag             # the edition body renders
    assert 'id="ed2020-01-02-story-0"' in frag             # date-scoped id (no collision)
    assert "<!DOCTYPE html>" not in frag                   # a fragment, not a full page
    con = db.connect()
    try:
        rows = [(r["date"], r["kind"]) for r in event_rows(con)]
    finally:
        con.close()
    assert rows == [("2020-01-02", "read")]                # serving the body IS a read
    code2, _, _ = get(ui, "/edition?date=nope")
    assert code2 == 400                                    # bad date rejected


def test_edition_fragment_absent_edition_is_graceful_and_no_read(ui):
    code, _, body = get(ui, "/edition?date=1999-01-01")
    assert code == 200 and "unavailable" in body.decode("utf-8")
    con = db.connect()
    try:
        assert event_rows(con) == []                       # nothing served -> no read
    finally:
        con.close()


def test_verbs_preserve_view_through_one_reload_mechanism():
    js = webui.JS
    assert "function reloadPreservingView" in js
    assert "function restoreViewAfterReload" in js
    assert "restoreViewAfterReload();" in js               # run on load
    assert "sessionStorage.setItem('nl-restore'" in js
    # every mutating verb reloads through the preserving path — no raw bounce.
    # NL-68 item 10 (DECISIONS 2026-07-16): addStory (free-text) is GONE, replaced
    # by followStory (suggestions-only), which reloads with the fold-expand flag
    # reloadPreservingView(true) — so the assertion matches the call prefix.
    for fn in ("saveNote", "followStory", "addTopic", "addWriter",
               "deleteThread", "threadAction", "generateAgain", "removeToken"):
        region = js.split("function " + fn, 1)[1].split("\nfunction ", 1)[0]
        assert "location.reload()" not in region, fn
        assert "reloadPreservingView(" in region, fn
    # removeToken re-fetches (count refresh), never the old silent in-place hide:
    remove_region = js.split("function removeToken", 1)[1].split("\nfunction ", 1)[0]
    assert "style.display = 'none'" not in remove_region
