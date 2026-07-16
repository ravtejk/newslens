"""QA extensions — server-side batch (staleness guard + free-text kill +
_here_for), 2026-07-17 pass.

The implementer's file (test_serverside_batch_20260717.py) proves the guard's
happy paths with patched identity functions. This file is the adversarial
honest-states matrix, exercised through the REAL mechanisms wherever the
sandbox allows:

  * mtime-kind lifecycle against a REAL sandboxed package dir (git absent at
    boot -> fallback stamps mtime -> a later source edit trips the comparison);
  * the CONSCIOUS false positive: a touch-without-change under mtime-kind
    reads as divergence (pinned as ruled-acceptable, see docstring);
  * git present at boot but vanishing mid-run — via a REAL failing subprocess
    (non-repo cwd) and a REAL OSError (empty PATH), never a crash;
  * the like-for-like kind rule: a git-stamped server NEVER compares against
    an mtime current (and an mtime-stamped server never adopts git mid-run);
  * rollback adversary: HEAD moved BACKWARD is still divergence — 409 over
    real HTTP with the pipeline never triggered;
  * identity unresolvable BOTH ways at stamp time: exactly one log line, no
    banner, no refusal;
  * reading stays untouched on a stale server (GET / and /api/status serve;
    only the generate trigger refuses);
  * a zero-mock fresh path: real stamp, real re-check, no banner;
  * _here_for edges the implementer's file skips (thread-only names survive,
    empty/malformed entries drop) + the sc-herefor twin rendered through
    _render_sources_context_view carries the deduped line.

Offline by construction (conftest autouse sandbox + loopback-only guard).
"""
from __future__ import annotations

import json
import os
import threading
import urllib.error
import urllib.request
from datetime import datetime
from http.server import ThreadingHTTPServer
from types import SimpleNamespace

import pytest

from newslens import db, paths, server

DATE = datetime.now().strftime("%Y-%m-%d")


# --------------------------------------------------------------------------
# harness (mirrors the implementer's file / test_server.py::ui)
# --------------------------------------------------------------------------
@pytest.fixture
def ui(tmp_paths, monkeypatch):
    db.migrate()
    monkeypatch.setattr(server, "GEN_JOB", server._GenJob())
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


def get(ui, path):
    with urllib.request.urlopen(ui.base + path, timeout=10) as resp:
        return resp.getcode(), resp.read().decode("utf-8")


def _con():
    db.migrate()
    return db.connect()


@pytest.fixture
def sandbox_pkg(tmp_path, monkeypatch):
    """A REAL package dir the mtime identity walks: server.__file__ is
    repointed so _src_mtime globs sandbox files instead of the live package —
    the mtime lifecycle is then exercised with real stats and real edits,
    without ever touching real source files (no-real-state-writes rule)."""
    pkg = tmp_path / "fakepkg"
    pkg.mkdir()
    (pkg / "server.py").write_text("# sandbox stand-in\n", encoding="utf-8")
    (pkg / "labels.py").write_text("X = 1\n", encoding="utf-8")
    monkeypatch.setattr(server, "__file__", str(pkg / "server.py"))
    # register the ORIGINAL (None) for teardown restore; tests below may call
    # _stamp_startup_identity(), which assigns the global directly.
    monkeypatch.setattr(server, "_STARTUP_IDENTITY", None)
    return pkg


def _bump(path, ns_delta=5_000_000_000):
    """Advance a file's mtime deterministically (immune to FS granularity)."""
    st = os.stat(path)
    os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns + ns_delta))


# ==========================================================================
# the honest-states matrix — mtime kind, real lifecycle
# ==========================================================================
def test_mtime_kind_real_lifecycle_git_absent_then_source_edit_trips(
        sandbox_pkg, monkeypatch):
    """Git absent at boot: the stamp falls back to mtime-kind — and a LATER
    source edit (real file write, real stat) must trip the comparison."""
    monkeypatch.setattr(server, "_git_head", lambda: None)  # the no-git world
    server._stamp_startup_identity()
    assert server._STARTUP_IDENTITY is not None
    assert server._STARTUP_IDENTITY[0] == "mtime"
    assert server._server_is_stale() is False        # like-for-like, fresh
    # the milestone lands: a package file changes on disk
    (sandbox_pkg / "labels.py").write_text("X = 2  # edited\n", encoding="utf-8")
    _bump(sandbox_pkg / "labels.py")
    assert server._server_is_stale() is True
    con = _con()
    try:
        page, _ = server.build_page(con)
    finally:
        con.close()
    assert 'class="staleness-banner"' in page


def test_mtime_kind_touch_without_change_trips_CONSCIOUS_false_positive(
        sandbox_pkg, monkeypatch):
    """PINNED AS RULED-ACCEPTABLE (QA 2026-07-17): under the mtime fallback a
    content-neutral touch reads as divergence — banner shows AND generate
    refuses. The identity axis when git is absent IS 'newest package mtime';
    the process cannot distinguish a touch from an edit without hashing, and
    the failure is fail-closed with a one-line remedy (restart re-stamps).
    The normal mode (git-kind) is immune — see the test below. If hashing
    ever replaces mtime here, this pin flips consciously with it."""
    monkeypatch.setattr(server, "_git_head", lambda: None)
    server._stamp_startup_identity()
    assert server._STARTUP_IDENTITY[0] == "mtime"
    _bump(sandbox_pkg / "labels.py")                 # touch: content unchanged
    assert server._server_is_stale() is True         # the accepted false positive


def test_git_kind_immune_to_mtime_noise(monkeypatch):
    """The converse guard on the same judgment: while git resolves, mtime
    noise (touches, editor swap files) NEVER trips the guard — the sha is the
    identity and it ignores timestamps."""
    monkeypatch.setattr(server, "_STARTUP_IDENTITY", ("git", "samesha"))
    monkeypatch.setattr(server, "_git_head", lambda: "samesha")
    monkeypatch.setattr(server, "_src_mtime",
                        lambda: "999999999999")      # screaming mtime churn
    assert server._server_is_stale() is False


# ==========================================================================
# the honest-states matrix — git vanishing mid-run, REAL failure paths
# ==========================================================================
def test_git_vanishing_midrun_real_failing_subprocess_never_crashes(
        tmp_path, ui, monkeypatch):
    """Stamped git at boot; mid-run the cwd stops being a repo. _git_head runs
    the REAL subprocess (read-only rev-parse) against a non-repo dir, gets a
    nonzero exit, and the guard resolves to fresh — stale -> False, reading
    serves, generate is NOT refused (never block on a broken check)."""
    real_head = server._git_head()                   # real, read-only
    assert real_head, "precondition: the frozen tree is a git checkout"
    monkeypatch.setattr(server, "_STARTUP_IDENTITY", ("git", real_head))
    monkeypatch.setattr(paths, "PROJECT_ROOT", tmp_path)   # not a repo
    assert server._git_head() is None                # real subprocess failure
    assert server._server_is_stale() is False
    starts = []
    monkeypatch.setattr(server.GEN_JOB, "start",
                        lambda: (starts.append(1), True)[1])
    code, obj = post(ui, "/api/generate", {})
    assert code == 200 and obj["ok"] is True and starts == [1]


def test_git_binary_missing_real_oserror_resolves_none(monkeypatch):
    """The OSError arm for real: an empty PATH makes subprocess.run raise
    FileNotFoundError — _git_head returns None instead of propagating."""
    monkeypatch.setenv("PATH", "")
    assert server._git_head() is None


# ==========================================================================
# the like-for-like kind rule — no cross-kind comparison, ever
# ==========================================================================
def test_git_stamped_server_never_compares_against_mtime_current(monkeypatch):
    """The cross-kind probe: git resolved at boot, vanished mid-run, while an
    mtime IS available and DIFFERS. A kind-blind implementation would compare
    'oldsha' to the mtime and scream stale; the rule says: unresolved same-kind
    current -> False."""
    monkeypatch.setattr(server, "_STARTUP_IDENTITY", ("git", "bootsha"))
    monkeypatch.setattr(server, "_git_head", lambda: None)
    monkeypatch.setattr(server, "_src_mtime", lambda: "170000000000")
    assert server._server_is_stale() is False


def test_mtime_stamped_server_never_adopts_git_appearing_midrun(monkeypatch):
    """Converse: stamped mtime (git was absent at boot); a repo appears
    mid-run. The guard keeps judging on the mtime axis — git's arrival neither
    trips it (same mtime) nor masks a real mtime divergence."""
    monkeypatch.setattr(server, "_STARTUP_IDENTITY", ("mtime", "111"))
    monkeypatch.setattr(server, "_git_head", lambda: "shiny-new-sha")
    monkeypatch.setattr(server, "_src_mtime", lambda: "111")
    assert server._server_is_stale() is False        # fresh on its own axis
    monkeypatch.setattr(server, "_src_mtime", lambda: "222")
    assert server._server_is_stale() is True         # stale on its own axis


def test_unknown_stamp_kind_resolves_fresh_not_crash(monkeypatch):
    """Future-proofing pin: an unrecognized kind value degrades to fresh
    (identity-of-kind -> None -> False), never an exception."""
    monkeypatch.setattr(server, "_STARTUP_IDENTITY", ("sha256", "whatever"))
    assert server._server_is_stale() is False


# ==========================================================================
# adversaries — rollback; and reading stays untouched
# ==========================================================================
def test_rollback_head_moved_backward_still_409s_pipeline_untouched(
        ui, monkeypatch):
    """Divergence is divergence: the server was booted on NEWER code and disk
    rolled BACK (checkout of an older milestone). Any comparison keyed on
    'disk is newer' misses this; the guard must still refuse."""
    starts = []
    monkeypatch.setattr(server.GEN_JOB, "start",
                        lambda: (starts.append(1), True)[1])
    monkeypatch.setattr(server, "_STARTUP_IDENTITY", ("git", "newer-sha"))
    monkeypatch.setattr(server, "_git_head", lambda: "older-sha")
    code, obj = post(ui, "/api/generate", {})
    assert code == 409 and obj["ok"] is False
    assert starts == []


def test_reading_stays_untouched_on_stale_server(ui, monkeypatch):
    """The incident rule cuts one way: WRITING with stale code is refused,
    reading is explicitly tolerable. On a stale server GET / serves 200 with
    the banner; /api/status serves 200; only the generate trigger 409s."""
    monkeypatch.setattr(server, "_STARTUP_IDENTITY", ("git", "oldsha"))
    monkeypatch.setattr(server, "_git_head", lambda: "newsha")
    code, body = get(ui, "/")
    assert code == 200
    assert 'class="staleness-banner"' in body
    assert 'role="alert"' in body
    with urllib.request.urlopen(ui.base + "/api/status", timeout=10) as resp:
        assert resp.getcode() == 200
    code, obj = post(ui, "/api/generate", {})
    assert code == 409 and obj["ok"] is False


# ==========================================================================
# unresolvable BOTH ways — one log line, guard off, nothing refused
# ==========================================================================
def test_unresolvable_both_ways_one_log_line_no_banner_no_refusal(
        ui, monkeypatch, capsys):
    monkeypatch.setattr(server, "_git_head", lambda: None)
    monkeypatch.setattr(server, "_src_mtime", lambda: None)
    monkeypatch.setattr(server, "_STARTUP_IDENTITY", ("git", "stale-junk"))
    server._stamp_startup_identity()                 # overwrites with None
    out = capsys.readouterr().out
    lines = [l for l in out.splitlines() if l.strip()]
    assert len(lines) == 1                           # exactly ONE log line
    assert "staleness guard disabled" in lines[0]
    assert server._STARTUP_IDENTITY is None
    assert server._server_is_stale() is False
    con = _con()
    try:
        page, _ = server.build_page(con)
    finally:
        con.close()
    assert 'class="staleness-banner"' not in page    # no banner
    starts = []
    monkeypatch.setattr(server.GEN_JOB, "start",
                        lambda: (starts.append(1), True)[1])
    code, obj = post(ui, "/api/generate", {})
    assert code == 200 and obj["ok"] is True         # no refusal


def test_zero_mock_fresh_path_real_stamp_real_recheck(tmp_paths, monkeypatch):
    """No patched identity functions at all: stamp against the real frozen
    checkout (read-only rev-parse), re-check immediately, render. The real
    mechanism composes to 'fresh' end to end."""
    monkeypatch.setattr(server, "_STARTUP_IDENTITY", None)  # teardown restore
    server._stamp_startup_identity()
    assert server._STARTUP_IDENTITY is not None      # this tree resolves
    assert server._server_is_stale() is False
    con = _con()
    try:
        page, _ = server.build_page(con)
    finally:
        con.close()
    assert 'class="staleness-banner"' not in page


# ==========================================================================
# _here_for — the edges the implementer's file skips, and the twin site
# ==========================================================================
def test_here_for_thread_only_names_survive():
    slot = {"matched_tags": [], "matched_memory": ["Hormuz Grain Corridor"]}
    assert server._here_for(slot) == "Hormuz Grain Corridor"


def test_here_for_empty_and_malformed_entries_drop():
    slot = {"matched_tags": [{"name": ""}, {"nope": 1}, "not-a-dict",
                             {"name": "Real Tag"}],
            "matched_memory": ["", "Real Thread"]}
    assert server._here_for(slot) == "Real Tag, Real Thread"


def test_here_for_multiple_dupes_and_order_preserved():
    slot = {"matched_tags": [{"name": "Alpha"}, {"name": "Beta"}],
            "matched_memory": ["beta", "Gamma", "ALPHA", "Gamma"]}
    # tags first in their order; threads add only genuinely new names; the
    # first-seen casing wins
    assert server._here_for(slot) == "Alpha, Beta, Gamma"


def test_writer_add_without_feed_url_400s_the_boundarys_basis(ui):
    """DECISIONS 2026-07-17 boundary pin: the writer surface keeps type-to-add
    ONLY because a writer add is a FEED LINK, not a topic/thread — and that
    fact is server-enforced, not just client copy. A URL-less add must 400
    before anything is created; if this ever loosens, the free-text kill's
    boundary erodes silently and this pin is the tripwire."""
    code, obj = post(ui, "/api/writer/add", {"name": "redistrictinga"})
    assert code == 400
    assert obj["ok"] is False
    assert "feed link required" in obj["error"]


def test_sc_herefor_twin_renders_the_deduped_line(tmp_paths):
    """The sources-&-context view (the sc-herefor twin) goes through the SAME
    helper — the NL-68 dupe shape renders singly there too."""
    con = _con()
    try:
        slot = {"slot": 1, "story_title": "Strait story",
                "summary": "A summary line.", "item_ids": [],
                "matched_tags": [{"name": "Strait of Hormuz"}],
                "matched_memory": ["Strait of Hormuz"]}
        html = server._render_sources_context_view(
            "qa-story-0", "Strait story", {}, slot, con, DATE)
    finally:
        con.close()
    line = html.split('class="sc-herefor"')[1].split("</p>")[0]
    assert line.count("Strait of Hormuz") == 1
    # the labeled tag/thread lines above it stay separate BY DESIGN
    assert 'class="sc-tags"' in html and 'class="sc-threads"' in html
