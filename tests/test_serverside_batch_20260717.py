"""Server-side batch — the 2026-07-16 stale-server incident + DECISIONS
2026-07-17 "standing orders". Red-first proofs for:

  * item 1 — the code-identity STALENESS GUARD. The server stamps its code
    identity at boot; on divergence with disk the UI shows a banner AND — the
    teeth — the generate trigger REFUSES (reading stale pages is tolerable;
    writing an edition with stale code is the incident). An unresolvable
    identity disables the guard (never block on a broken check).
  * item 2 — free-text topic entry DIES. The Topics combobox becomes
    suggestions-only, like the story-follow combobox; the sweep confirms no
    UI free-entry path to a topic/thread survives (writers stay free — a
    writer is a feed, not a topic/thread).
  * item 3 — _here_for dedupes a tag and a same-named tracked thread
    case-insensitively, order-preserving (the NL-68 "Strait of Hormuz, Strait
    of Hormuz" exhibit).

Offline by construction (conftest autouse sandbox + loopback-only guard).
"""
from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from datetime import datetime
from http.server import ThreadingHTTPServer
from types import SimpleNamespace

import pytest

from newslens import db, server

DATE = datetime.now().strftime("%Y-%m-%d")


# --------------------------------------------------------------------------
# loopback harness (mirrors test_server.py::ui — the real HTTP path so the
# generate refusal is proven end-to-end, not by poking a method)
# --------------------------------------------------------------------------
@pytest.fixture
def ui(tmp_paths, monkeypatch):
    db.migrate()
    monkeypatch.setattr(server, "GEN_JOB", server._GenJob())  # fresh job state
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    box = SimpleNamespace(
        base=f"http://127.0.0.1:{httpd.server_address[1]}", httpd=httpd)
    yield box
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


def _con():
    db.migrate()
    return db.connect()


def _seed_edition(con, date=DATE):
    con.execute(
        "INSERT INTO briefings (date, story_slots) VALUES (?, ?)",
        (date, json.dumps([{"slot": 1, "story_title": "Lead",
                            "matched_tags": [{"name": "AI regulation"}]}])))
    con.commit()


# ==========================================================================
# item 1 — the staleness guard
# ==========================================================================
def test_item1_stale_git_server_shows_banner(tmp_paths, monkeypatch):
    monkeypatch.setattr(server, "_STARTUP_IDENTITY", ("git", "oldsha"))
    monkeypatch.setattr(server, "_git_head", lambda: "newsha")
    con = _con()
    try:
        page, _ = server.build_page(con)
    finally:
        con.close()
    assert 'class="staleness-banner"' in page
    assert "no longer matches" in page
    assert "<code>newslens serve</code>" in page
    assert 'role="alert"' in page


def test_item1_fresh_git_server_shows_no_banner(tmp_paths, monkeypatch):
    monkeypatch.setattr(server, "_STARTUP_IDENTITY", ("git", "samesha"))
    monkeypatch.setattr(server, "_git_head", lambda: "samesha")
    con = _con()
    try:
        page, _ = server.build_page(con)
    finally:
        con.close()
    # the .staleness-banner CSS rule always ships; the ELEMENT must not
    assert 'class="staleness-banner"' not in page
    assert 'role="alert"' not in page


def test_item1_unresolvable_identity_never_blocks(tmp_paths, monkeypatch):
    # never stamped (in-process Handler without serve()) -> guard OFF
    monkeypatch.setattr(server, "_STARTUP_IDENTITY", None)
    assert server._server_is_stale() is False
    # current side unresolvable (git vanished mid-run) -> never block on a
    # broken check, even though the process WAS stamped
    monkeypatch.setattr(server, "_STARTUP_IDENTITY", ("git", "oldsha"))
    monkeypatch.setattr(server, "_git_head", lambda: None)
    assert server._server_is_stale() is False


def test_item1_mtime_is_the_git_unavailable_fallback(tmp_paths, monkeypatch):
    monkeypatch.setattr(server, "_git_head", lambda: None)
    ident = server._code_identity()
    assert ident is not None and ident[0] == "mtime"
    # divergence detected on the mtime axis too
    monkeypatch.setattr(server, "_STARTUP_IDENTITY", ("mtime", "1"))
    monkeypatch.setattr(server, "_src_mtime", lambda: "2")
    assert server._server_is_stale() is True


def test_item1_stale_server_refuses_generate_THE_TEETH(ui, monkeypatch):
    """The enforcement red test: a stale server refuses the generate trigger —
    the pipeline never starts, so no edition is written with stale code."""
    starts = []
    monkeypatch.setattr(server.GEN_JOB, "start",
                        lambda: (starts.append(1), True)[1])
    monkeypatch.setattr(server, "_STARTUP_IDENTITY", ("git", "oldsha"))
    monkeypatch.setattr(server, "_git_head", lambda: "newsha")
    code, obj = post(ui, "/api/generate", {})
    assert code == 409
    assert obj["ok"] is False
    assert "newslens serve" in obj["error"]
    assert starts == []                       # the pipeline was NEVER triggered


def test_item1_fresh_server_allows_generate(ui, monkeypatch):
    starts = []
    monkeypatch.setattr(server.GEN_JOB, "start",
                        lambda: (starts.append(1), True)[1])
    monkeypatch.setattr(server, "_STARTUP_IDENTITY", ("git", "samesha"))
    monkeypatch.setattr(server, "_git_head", lambda: "samesha")
    code, obj = post(ui, "/api/generate", {})
    assert code == 200 and obj["ok"] is True
    assert starts == [1]                      # the fresh path reaches the job


# ==========================================================================
# item 2 — free-text topic entry dies
# ==========================================================================
def test_item2_topic_combobox_is_suggestions_only(tmp_paths):
    con = _con()
    try:
        _seed_edition(con)
        following = server._render_following(con)
    finally:
        con.close()
    topic_attrs = following.split('data-kind="topic"')[1].split(">")[0]
    assert "data-suggest-only" in topic_attrs
    # the placeholder no longer invites free entry
    assert "Search or add a topic" not in following


def test_item2_no_freetext_topic_or_thread_surface_remains(tmp_paths):
    """Sweep: every UI combobox routing to a topic/thread is suggestions-only.
    Writers are intentionally excluded — a writer is a feed, not a topic."""
    con = _con()
    try:
        _seed_edition(con)
        following = server._render_following(con)
    finally:
        con.close()
    for kind in ("topic", "story"):
        attrs = following.split(f'data-kind="{kind}"')[1].split(">")[0]
        assert "data-suggest-only" in attrs, f"{kind} still allows free entry"
    writer_attrs = following.split('data-kind="writer"')[1].split(">")[0]
    assert "data-suggest-only" not in writer_attrs   # a feed link, not a topic


# ==========================================================================
# item 3 — _here_for dedupe (the NL-68 exhibit)
# ==========================================================================
def test_item3_here_for_dedupes_tag_and_same_named_thread():
    slot = {"matched_tags": [{"name": "Strait of Hormuz"}],
            "matched_memory": ["Strait of Hormuz"]}
    assert server._here_for(slot) == "Strait of Hormuz"


def test_item3_here_for_dedup_is_case_insensitive():
    slot = {"matched_tags": [{"name": "Strait of Hormuz"}],
            "matched_memory": ["strait of hormuz"]}
    assert server._here_for(slot) == "Strait of Hormuz"


def test_item3_here_for_tags_first_keeps_distinct_drops_only_dupes():
    slot = {"matched_tags": [{"name": "AI regulation"}],
            "matched_memory": ["Chips", "AI regulation"]}
    assert server._here_for(slot) == "AI regulation, Chips"


def test_item3_here_for_distinct_and_fallbacks_unchanged():
    assert server._here_for(
        {"matched_tags": [{"name": "AI regulation"}], "matched_memory": []}
    ) == "AI regulation"
    assert server._here_for({"override": True}) == \
        "editor's override — see note above"
    assert server._here_for({}) == \
        "world-impact selection (no tag or thread match)"
