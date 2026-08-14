"""Shared fixtures for the NewsLens milestone-1 QA suite (QA-owned, per team/ENGINEERING.md).

Design rules for this suite:
  * Hermetic: no test touches the real data/ DB, the real sources.yaml, or a
    real .env. Stateful paths are redirected into tmp sandboxes per test.
  * No real network, ever: API-shaped checks run against a local fake HTTP
    server on 127.0.0.1; "the doctor makes zero network calls when keyless"
    is verified *mechanically* with a socket-level recorder, not by reading
    the code and nodding.
  * The shipped artifacts (template sources.yaml, migrations/, prompts/,
    .env.example) are tested as shipped — copied or referenced read-only.

OFF-TREE COPY CHECKLIST — binding on every born-red / mutation / green leg that
runs this suite from a copy (ENGINEERING.md, "off-tree legs prove the executed
artifact"). This is where the recipe lives; a copy is made like this and no
other way:

    rsync -a --exclude='.env' --exclude='.venv' --exclude='data/' \
             --exclude='profiles/' --exclude='__pycache__' \
             --exclude='.pytest_cache' <tree>/ <copy>/

`.git` is NOT excluded: the repo-hygiene contract shells out to
`git check-ignore` / `git ls-files` against the tree root, so a copy without it
fails on missing history rather than on the behaviour under test.

  1. NEVER copy `.env`. The suite does not read it (see _NEVER_HASHED below —
     it is not even hashed), so a copy carrying it has no purpose but to
     multiply the number of places the principal's real keys exist. NL-108 QA
     F-1: a kept born-red tree held a byte-identical copy of the real `.env`
     for a whole loop, and a leg proved the suite runs green without it.
  2. Never copy live state — the real `data/` (the founder's DB and generation
     log) or `profiles/` (each reader's own DB, memory.md and sources.yaml).
     No test may resolve them: every path a test gets is a sandbox from the
     fixtures below, and the profile tests do path arithmetic on names that do
     not exist on disk. RECEIPT (NL-108 fix loop): a full ordered leg from a
     copy with BOTH excluded ran 3986 passed / 1 skipped / 1 xfailed — the
     same counts as the leg that carried them. Copy either one only as an
     explicit, hash-verified, read-only specimen for a census, never as part
     of a runnable tree.
  3. Set PYTHONPATH to the COPY's `src` and assert in-process that
     `newslens.__file__` resolves inside the copy before the leg runs — the
     venv's editable `.pth` otherwise silently re-imports the real checkout.
  4. Delete the copy when the loop that made it is over, or say in the report
     where it was kept and why.
"""

from __future__ import annotations

import builtins
import errno
import hashlib
import http.server
import json
import os
import socket
import sys
import threading
import traceback
from pathlib import Path

import pytest

from newslens import db, paths

PROTOTYPE_ROOT = Path(__file__).resolve().parents[1]

# Stage-0 M2 (R5 convention, in-tree residency): the seeded shuffle plugin.
# Implementation lives in tools/pytest_shuffle.py — reviewable on its own,
# untangled from these fixtures — and is re-exported HERE because this file is
# an initial conftest (testpaths = ["tests"]), the only place pytest honours
# pytest_addoption. Loading it never reorders an ordered run: the hooks are
# inert unless --shuffle is passed.
if str(PROTOTYPE_ROOT) not in sys.path:
    sys.path.insert(0, str(PROTOTYPE_ROOT))
from tools.pytest_shuffle import (  # noqa: E402
    pytest_addoption,               # noqa: F401
    pytest_collection_modifyitems,  # noqa: F401
    pytest_configure,               # noqa: F401
    pytest_report_header,           # noqa: F401
    pytest_terminal_summary,        # noqa: F401
)

# B3 subscription-lane safety: a DEFAULT stub `claude` shim, created ONCE and
# pointed at by NEWSLENS_CLAUDE_BIN in sandbox_paths. It emits a canned
# `claude -p --output-format json` success envelope and NEVER touches the
# network or the real CLI — so (a) llm.check_lane's binary-resolution gate
# passes for the subscription-default seats (rank/editor/script) exactly as the
# api lane's key check passed in B2, and (b) if a test ever actually reaches the
# subscription transport, it hits THIS shim, never the real `claude` installed
# at ~/.local/bin/claude (which would make a live, billable call). Tests that
# need a specific subprocess behaviour (env-strip proof, is_error, timeout,
# recorded argv) override NEWSLENS_CLAUDE_BIN with their own shim; tests that
# assert api-lane transport pin NEWSLENS_LANE_<SEAT>=api.
_STUB_CLAUDE_SRC = (
    "#!/usr/bin/env python3\n"
    "import sys, json\n"
    "# --version answers IMMEDIATELY, without touching stdin (B3 QA): the\n"
    "# doctor's check_subscription_lane spawns `<bin> --version` with NO\n"
    "# stdin pipe, so a stub that read stdin first would inherit pytest's\n"
    "# fd0 — under a terminal run that read BLOCKS until the doctor's 10s\n"
    "# timeout, flipping the section to FAIL and making suite results depend\n"
    "# on how pytest was invoked. Version string mirrors the ADR-0015 pin.\n"
    "if '--version' in sys.argv[1:]:\n"
    "    print('2.1.212 (NewsLens QA stub, not the real CLI)')\n"
    "    sys.exit(0)\n"
    "sys.stdin.read()\n"
    "print(json.dumps({'type': 'result', 'subtype': 'success', "
    "'is_error': False, 'result': '{}', 'session_id': 'stub-session', "
    "'total_cost_usd': 0.0, 'usage': {'input_tokens': 1, 'output_tokens': 1, "
    "'cache_read_input_tokens': 0}}))\n"
)


def _default_stub_claude() -> Path:
    """Create the canned-success stub `claude` shim once, in a stable temp dir
    (not the repo, not real state), and return its path."""
    import stat as _stat
    import tempfile
    d = Path(tempfile.gettempdir()) / "newslens-qa-stub-claude"
    d.mkdir(exist_ok=True)
    shim = d / "claude"
    if not shim.exists() or shim.read_text() != _STUB_CLAUDE_SRC:
        shim.write_text(_STUB_CLAUDE_SRC)
        shim.chmod(shim.stat().st_mode | _stat.S_IXUSR | _stat.S_IXGRP | _stat.S_IXOTH)
    return shim


_STUB_CLAUDE_BIN = _default_stub_claude()

# NL-156: the dead stand-in for llm.CLAUDE_BIN_DEFAULT under the sandbox (see
# sandbox_paths). Deliberately NOT under tmp_path: pytest's tmp_path embeds the
# TEST NAME, and this path is echoed verbatim in the doctor's lane-FAIL text —
# test_nl147_no_haiku asserts "haiku" appears on no doctor surface and is itself
# named ..._no_haiku, so a tmp_path sentinel made that test fail on its own
# filename. (Same trap `_doctor_surfaces` documents having already sprung once.)
_NO_MACHINE_DEFAULT_CLAUDE = "/nonexistent/newslens-suite/no-machine-default-claude"


def sandbox_bin_env(**extra) -> dict:
    """An env mapping that DECLARES the sandbox's stub `claude` — i.e. "a normal
    install, CLI present and resolvable".

    NL-156 (2026-08-14): `check_lane` resolves the binary from the env it is
    handed, so a test passing a hand-built mapping to `effective_seat` or
    `doctor.check_llm_lanes` must say what binary that world has. Before, such a
    mapping quietly inherited the process env's stub and the test read as though
    `{}` meant "a healthy machine" — it did not, it meant "ask os.environ". Use
    this where the intended world has a working CLI; pass a path that does not
    resolve where the intended world has none."""
    return {"NEWSLENS_CLAUDE_BIN": str(_STUB_CLAUDE_BIN), **extra}


def rank_keys(content):
    """NL-70 re-key: a real rank seat emits bracketed [id=KEY] Crockford codes,
    not raw ints (ranking.decode_keys now REJECTS bare JSON-number ints — that was
    the silent in-vocab channel QA F1 closed). Mocked rank output is authored with
    readable int item_ids for legibility; this renders each into the KEY the model
    would actually emit, so the fixture decodes back through decode_keys to the same
    int. Ints only (bool/negative/non-int pass through untouched, so a test that
    deliberately sends a malformed value still exercises the reject path). Returns a
    COPY — the caller's payload dict is never mutated. Non-rank content (a narrative
    string, a dict without a `clusters` list) passes straight through."""
    from newslens import ranking
    if not isinstance(content, dict) or not isinstance(content.get("clusters"), list):
        return content
    out = dict(content)
    out["clusters"] = [
        {**c, "item_ids": [ranking.encode_rank_key(x) if type(x) is int and x >= 0 else x
                           for x in c["item_ids"]]}
        if isinstance(c, dict) and isinstance(c.get("item_ids"), list) else c
        for c in content["clusters"]
    ]
    return out


def anthropic_envelope(content, input_tokens: int = 1000, output_tokens: int = 200,
                       stop_reason: str = "end_turn", cache_creation: int = 0,
                       cache_read: int = 0) -> bytes:
    """B2 Claude API lane fake: an anthropic /v1/messages response body. The
    twin of each test-file's OpenAI-shaped `envelope()`. `content` is the text
    (a JSON string for json_mode seats, a plain string otherwise; a dict/list is
    json.dumps'd for convenience). stop_reason 'max_tokens' is what the provider
    maps to finish_reason 'length' (the truncation-guard trigger).

    NL-70: a rank-cluster dict is re-keyed (int item_ids -> [id=KEY] codes) so the
    fake body carries keys-only model output, exactly as a live Haiku would."""
    content = rank_keys(content)
    text = json.dumps(content) if isinstance(content, (dict, list)) else str(content)
    return json.dumps({
        "id": "msg_qa", "type": "message", "role": "assistant",
        "model": "claude-haiku-4-5",
        "content": [{"type": "text", "text": text}],
        "stop_reason": stop_reason,
        "usage": {"input_tokens": input_tokens, "output_tokens": output_tokens,
                  "cache_creation_input_tokens": cache_creation,
                  "cache_read_input_tokens": cache_read},
    }).encode("utf-8")


def anthropic_sse_bytes(payload: dict) -> bytes:
    """NL-93: serialise a non-streaming /v1/messages payload dict into the SSE
    event stream the LONG-call seats (writer/analyst) now receive, so a fake
    urlopen response can serve the SAME content EITHER way. The frames rebuild,
    through llm._accumulate_sse, the SAME native dict json.load would produce from
    the non-streaming body: message_start carries input + cache usage (initial
    output_tokens=1, wire-faithful); each content block is streamed whole;
    message_delta carries the FINAL cumulative output_tokens + stop_reason;
    message_stop terminates. A payload that is not anthropic-shaped (e.g. an
    OpenAI-shaped canned dict a ROUTING test uses, where content/usage are not
    asserted) yields a minimal valid stream that still completes (message_stop
    present) so the accumulator never raises its incomplete-stream transport
    error."""
    usage = payload.get("usage") or {}
    out_tokens = usage.get("output_tokens")
    start_usage = {k: v for k, v in usage.items() if k != "output_tokens"}
    start_usage["output_tokens"] = 1
    events = [{
        "type": "message_start",
        "message": {
            "id": payload.get("id", "msg_sse"), "type": "message",
            "role": "assistant", "model": payload.get("model", "claude"),
            "content": [], "stop_reason": None, "usage": start_usage,
        },
    }]
    idx = 0
    for block in (payload.get("content") or []):
        if not isinstance(block, dict):
            continue
        btype = block.get("type", "text")
        key = "thinking" if btype == "thinking" else "text"
        dtype = "thinking_delta" if btype == "thinking" else "text_delta"
        events += [
            {"type": "content_block_start", "index": idx,
             "content_block": {"type": btype, key: ""}},
            {"type": "content_block_delta", "index": idx,
             "delta": {"type": dtype, key: block.get(key, "")}},
            {"type": "content_block_stop", "index": idx},
        ]
        idx += 1
    delta_usage = {} if out_tokens is None else {"output_tokens": out_tokens}
    events += [
        {"type": "message_delta",
         "delta": {"stop_reason": payload.get("stop_reason") or "end_turn",
                   "stop_sequence": None},
         "usage": delta_usage},
        {"type": "message_stop"},
    ]
    frames = "".join(
        f"event: {e['type']}\ndata: {json.dumps(e)}\n\n" for e in events)
    return frames.encode("utf-8")

# The actual on-disk locations, captured through the guard's backing table
# (plain dict read — no sanction check, no PEP 562) before any sandboxing.
_REAL_DATA_DIR = paths._GUARDED["DATA_DIR"]
# v7-M2 QA widening (2026-07-14): the db and the generation log are stat'd
# INDIVIDUALLY — the dir mtime/listing snapshot below only moves on
# create/delete/rename, so an IN-PLACE rewrite (append/clobber, the exact
# 2026-07-14 generation_log incident shape) is invisible to it. Proven by
# probe: an append to a pre-existing watched-dir file passed the pre-widening
# tripwire. test_v7_m2_qa.py::test_tripwire_snapshot_sees_inplace_db_and_log_rewrites
# is the red test only this widening flips.
_REAL_STATE_FILES = (paths._GUARDED["SOURCES_FILE"],
                     paths._GUARDED["MEMORY_FILE"],
                     paths._GUARDED["ENV_FILE"],
                     paths._GUARDED["DB_PATH"],
                     _REAL_DATA_DIR / "generation_log.jsonl")
# Stage-0 M1: profiles/ is real state too — every non-default reader's whole
# world lives there. No suite test may create, provision or migrate a REAL
# profile; watching the directory makes that a mechanism instead of a hope
# (the profile tests provision inside tmp_path, via paths.anchor_dir()).
_REAL_PROFILES_DIR = paths.PROJECT_ROOT / paths.PROFILES_DIRNAME


def _walk_state(root, snap):
    """RECURSIVE per-file stat of one real-state tree, into `snap`.

    NL-132-B (NL-133 gate rider R-E, 2026-08-02). The top-level dir snapshot
    below moves only on create/delete/rename IN THAT DIRECTORY, so every write
    one level deeper — `profiles/<slug>/data/newslens.db` rewritten in place,
    `profiles/<slug>/data/generation_log.jsonl` appended — was invisible to it.
    That is the SAME in-place class v7-M2 closed for data/ by naming the db and
    the log individually; it was never extended to profiles/, and profiles/ has
    been live surface since NL-134. QA proved the gap by execution, both arms
    (2026-08-02 QA report §7: arm 1 nested rewrite -> tripwire GREEN; arm 2
    control append to the watched default db -> tripwire FAILS by name).
    `_real_state_snapshot` cannot enumerate the profile tree by rule the way
    _REAL_STATE_FILES enumerates the founder's five, because profiles are
    minted at will — so the coverage has to be a walk.

    CHEAP BY CONSTRUCTION, because this runs twice per test for the whole
    suite: os.scandir + DirEntry.stat only — no reads, no hashing, no sorting
    of the whole tree (dict comparison is order-independent). Measured on the
    real profiles/ (20 files, 13 dirs, 3.2M): 0.20 ms per snapshot, ~1.3 s
    across a 3152-test run. Directories are recorded as bare keys so a mkdir
    or an rmdir is caught even when it moves no file.
    """
    try:
        entries = os.scandir(root)
    except (FileNotFoundError, NotADirectoryError, PermissionError):
        return
    with entries:
        for entry in entries:
            try:
                if entry.is_dir(follow_symlinks=False):
                    snap[entry.path] = "dir"
                    _walk_state(entry.path, snap)
                else:
                    st = entry.stat(follow_symlinks=False)
                    snap[entry.path] = (st.st_mtime_ns, st.st_size)
            except OSError as exc:                 # raced or unreadable
                snap[entry.path] = f"unstat-able: {type(exc).__name__}"


def _real_state_snapshot():
    snap = {}
    for d in (_REAL_DATA_DIR, _REAL_DATA_DIR / "briefings", _REAL_PROFILES_DIR):
        try:
            st = os.stat(d)
            snap[str(d)] = (st.st_mtime_ns, tuple(sorted(os.listdir(d))))
        except FileNotFoundError:
            snap[str(d)] = None
    for f in _REAL_STATE_FILES:
        try:
            st = os.stat(f)
            snap[str(f)] = (st.st_mtime_ns, st.st_size)
        except FileNotFoundError:
            snap[str(f)] = None
    # Widened per file, one level and every level below (R-E). Additive: the
    # top-level watch above is kept, so nothing the M1 pin proved moves.
    _walk_state(_REAL_PROFILES_DIR, snap)
    return snap


# ===========================================================================
# THE IN-SUITE WRITE LEDGER — tripwire ATTRIBUTION (NL-148 QA §B.3 proposal 1)
# ===========================================================================
#
# WHY THIS EXISTS. The tripwire above brackets the real files per test BY STAT.
# Stat detects CHANGE; it cannot detect AUTHORSHIP. On 2026-08-12 the principal
# unfollowed and re-followed a topic through his own live `newslens serve` while
# a suite run was in flight; the change landed inside one GET-only test's
# bracket and the tripwire reported "REAL state touched during this test
# (sandbox pinhole)". The suite had written nothing — QA proved it twice over
# (exact-seed replay against a byte-identical sentinel: zero writes; a 789-event
# audit: all 525 sources.yaml writes sandboxed) — but the message named the
# suite, and an org spent a day on a pinhole that did not exist.
#
# So the tripwire now consults a LEDGER of writes the suite actually made, and
# says which of the two things happened.
#
# COVERAGE BOUND, stated so a "no in-suite write recorded" line is never
# over-read as proof of innocence:
#   * COVERED — `open(..., 'w'/'a'/'x'/'+')`, `os.replace`, `os.rename`,
#     `os.remove`, `os.unlink`, called through the `builtins`/`os` attributes.
#   * NOT COVERED — sqlite3's writes (they go through the C library, never
#     `builtins.open`), `os.open`/low-level fd writes, a module that bound
#     `from os import replace` before this wrap installed, and any child
#     process (a child's writes are its own; the env seams are what sandbox
#     those). A change to `newslens.db` with an empty ledger is therefore
#     UNATTRIBUTED, not proven external — the message says exactly that.
#
# COST: the hot path is one `isinstance` + one set-disjointness test on the
# mode string for every `open()`; reads leave before any path work. Watched-path
# resolution and the stack capture run only on a hit, and a hit should be a
# once-a-year event.

_WRITE_LEDGER = []          # append-only; entries are dicts, see _record_write

# Every real-state location the tripwire watches, as a prefix tuple. A write is
# "in-suite" for our purposes only if it lands on one of these.
_LEDGER_WATCH_PREFIXES = tuple(sorted({
    str(_REAL_DATA_DIR),
    str(_REAL_PROFILES_DIR),
    *(str(p) for p in _REAL_STATE_FILES),
}))

_TRUE_WRITE_CHARS = frozenset("wax+")


def _watched_write_target(target):
    """The watched real path `target` names, else None. Pure string work — no
    stat, no resolve (a symlink pointing INTO real state is not resolved here;
    the tripwire's own stat still catches the change, it is only attribution
    that would degrade to 'unattributed')."""
    if isinstance(target, int):          # an already-open fd: not a path
        return None
    try:
        p = os.fspath(target)
    except TypeError:
        return None
    if isinstance(p, bytes):
        try:
            p = p.decode()
        except UnicodeDecodeError:
            return None
    if not p.startswith(os.sep):
        p = os.path.join(os.getcwd(), p)
    p = os.path.normpath(p)
    for prefix in _LEDGER_WATCH_PREFIXES:
        if p == prefix or p.startswith(prefix + os.sep):
            return p
    return None


def _record_write(path: str, op: str) -> None:
    """One ledger entry. The STACK is the point: a real future pinhole should
    fail with the writer's own frames in hand, which is strictly more than the
    stat diff this replaces ever gave anyone."""
    _WRITE_LEDGER.append({
        "path": path,
        "op": op,
        "thread": threading.current_thread().name,
        # carries the phase (setup/call/teardown) — a teardown- or gap-phase
        # write is the straggler signature the F7 geometry would produce.
        "test": os.environ.get("PYTEST_CURRENT_TEST", "<outside any test>"),
        "stack": traceback.format_stack(limit=14)[:-1],
    })


_REAL_OPEN = builtins.open
_REAL_OS_REPLACE = os.replace
_REAL_OS_RENAME = os.rename
_REAL_OS_REMOVE = os.remove
_REAL_OS_UNLINK = os.unlink


def _ledger_open(file, mode="r", *args, **kwargs):
    if isinstance(mode, str) and not _TRUE_WRITE_CHARS.isdisjoint(mode):
        hit = _watched_write_target(file)
        if hit is not None:
            _record_write(hit, f"open(mode={mode!r})")
    return _REAL_OPEN(file, mode, *args, **kwargs)


def _ledger_replace(src, dst, *args, **kwargs):
    hit = _watched_write_target(dst)
    if hit is not None:
        _record_write(hit, "os.replace")
    return _REAL_OS_REPLACE(src, dst, *args, **kwargs)


def _ledger_rename(src, dst, *args, **kwargs):
    hit = _watched_write_target(dst)
    if hit is not None:
        _record_write(hit, "os.rename")
    return _REAL_OS_RENAME(src, dst, *args, **kwargs)


def _ledger_remove(path, *args, **kwargs):
    hit = _watched_write_target(path)
    if hit is not None:
        _record_write(hit, "os.remove")
    return _REAL_OS_REMOVE(path, *args, **kwargs)


def _ledger_unlink(path, *args, **kwargs):
    hit = _watched_write_target(path)
    if hit is not None:
        _record_write(hit, "os.unlink")
    return _REAL_OS_UNLINK(path, *args, **kwargs)


builtins.open = _ledger_open
os.replace = _ledger_replace
os.rename = _ledger_rename
os.remove = _ledger_remove
os.unlink = _ledger_unlink


# --- content shas, so a diff report carries WHAT changed, not just THAT ------
#
# Taken ONCE at session start, and deliberately NOT inside _real_state_snapshot:
# that function runs twice per test across the whole suite and is pinned
# stat-only (test_nl132b_profile_hardening: it may not even call `open`).
#
# TWO DELIBERATE OMISSIONS:
#   * `.env` is never hashed — the suite does not read the principal's secret
#     file, not even to digest it. Stat-only, as before.
#   * anything over the size cap (the 16MB newslens.db today) is not hashed
#     either; a per-session 16MB read to improve one failure message is not a
#     trade worth making. Both report their size instead, and say so.
_SHA_SIZE_CAP_BYTES = 4 * 1024 * 1024
_NEVER_HASHED = {str(paths._GUARDED["ENV_FILE"])}


def _content_sha(path) -> str:
    """sha256 of a watched file, or a stated reason it was not hashed."""
    p = str(path)
    if p in _NEVER_HASHED:
        return "not hashed (the principal's .env is never read by this suite)"
    try:
        size = os.stat(p).st_size
    except FileNotFoundError:
        return "absent"
    except OSError as exc:
        return f"unstat-able: {type(exc).__name__}"
    if size > _SHA_SIZE_CAP_BYTES:
        return f"not hashed ({size} bytes, over the {_SHA_SIZE_CAP_BYTES} cap)"
    h = hashlib.sha256()
    try:
        with _REAL_OPEN(p, "rb") as fh:
            for chunk in iter(lambda: fh.read(1 << 20), b""):
                h.update(chunk)
    except OSError as exc:
        return f"unreadable: {type(exc).__name__}"
    return h.hexdigest()


_SESSION_START_SHAS = {str(f): _content_sha(f) for f in _REAL_STATE_FILES}


def _tripwire_message(diff: dict, writes: list) -> str:
    """The tripwire's failure text, ATTRIBUTED. Pure — no I/O beyond hashing the
    files the diff already named — so it can be pinned by test rather than by
    reading it and nodding."""
    if writes:
        verdict = (
            f"CHANGED BY THIS SUITE — {len(writes)} in-suite write(s) recorded "
            "on watched real paths. This is a genuine sandbox pinhole: the "
            "writer's own frames are below."
        )
    else:
        verdict = (
            "CHANGED DURING THIS TEST, NOT BY IT — no in-suite write was "
            "recorded on any watched real path (ledger covers open-for-write, "
            "os.replace, os.rename, os.remove, os.unlink). An EXTERNAL WRITER "
            "is the likely author: a live `newslens serve` sitting on this "
            "machine edits sources.yaml / memory.md / data/ while the suite "
            "runs, and its write lands inside whichever test happens to hold "
            "the bracket (incident #4, 2026-08-12 — re-classified from "
            "'pinhole' to exactly this). LIMIT: sqlite3 writes go through the C "
            "library and are outside the ledger, so a newslens.db change with "
            "an empty ledger is UNATTRIBUTED, not proven external."
        )
    lines = [
        "REAL state touched during this test (ENGINEERING.md 'no real-state "
        "writes').",
        f"ATTRIBUTION: {verdict}",
        "",
        "WHAT MOVED (stat):",
    ]
    for key in sorted(diff):
        lines.append(f"  {key}")
        lines.append(f"    before: {diff[key]['before']}")
        lines.append(f"    after:  {diff[key]['after']}")
        if key in _SESSION_START_SHAS:
            lines.append(f"    sha at session start: {_SESSION_START_SHAS[key]}")
            lines.append(f"    sha now:              {_content_sha(key)}")
    if writes:
        lines.append("")
        lines.append("IN-SUITE WRITES RECORDED DURING THIS TEST:")
        for w in writes:
            lines.append(f"  {w['op']} -> {w['path']}")
            lines.append(f"    thread: {w['thread']}")
            lines.append(f"    test:   {w['test']}")
            for frame in w["stack"][-6:]:
                lines.append("    " + frame.rstrip().replace("\n", "\n    "))
    return "\n".join(lines)


@pytest.fixture(autouse=True)
def real_state_tripwire():
    """AUTOUSE, defined first so it wraps every other fixture's teardown.

    v7-M1 QA observation (2026-07-14): a full-suite run bumped the REAL
    data/ mtime — test_preinstall_doctor's doctor child ran the data-dir
    writability probe against the real checkout, because monkeypatch
    sandboxing cannot cross a process boundary. ENGINEERING.md says the
    committed suite is safe by construction; this fixture makes that a
    mechanism instead of a hope — any test whose run (including its
    children) creates, deletes, or rewrites real state fails BY NAME,
    read-only stat/listdir being the only inspection it performs.
    NL-148 fix loop 1: the failure is now ATTRIBUTED. The snapshot pair still
    only detects CHANGE; the write ledger above says whether this suite is the
    one that made it, so "an external writer edited your sources.yaml mid-run"
    can no longer be reported as a sandbox pinhole (incident #4, 2026-08-12).
    """
    before = _real_state_snapshot()
    ledger_mark = len(_WRITE_LEDGER)
    yield
    after = _real_state_snapshot()
    if after != before:
        diff = {
            k: {"before": before.get(k), "after": after.get(k)}
            for k in set(before) | set(after)
            if before.get(k) != after.get(k)
        }
        pytest.fail(
            _tripwire_message(diff, _WRITE_LEDGER[ledger_mark:]),
            pytrace=False,
        )

# ===========================================================================
# THE SESSION FLOORS (NL-148 QA §B.3 proposal 2 — the straggler geometry, shut)
# ===========================================================================
#
# QA's LEG-2 audit proved the suite never ARMS the straggler geometry today:
# 248/248 `allow_real_paths` sanctions were MainThread and call-phase, so every
# one sat inside the per-test `_REAL_PATHS_ALLOWED = False` save/restore and no
# post-unwind resolution could ever find the flag open. But "no test does this
# in this order" is a property of the current suite, not of the machine — the
# geometry is one new fixture away, and `_REAL_PATHS_ALLOWED` is process-global
# while `ThreadingHTTPServer(daemon_threads=True)` request threads can outlive
# the test that started them (socketserver._Threads never tracks a daemon
# thread, so server_close joins nothing).
#
# Two floors close it structurally, in every phase, sanctioned or not.

# ---- FLOOR 1: the path seams always point SOMEWHERE UNREACHABLE ------------
#
# `paths.__getattr__` resolves the NEWSLENS_* override AHEAD of the sanction
# arm ("redirection outranks sanction", the v7-M1 fix). Per-test sandboxing
# sets those vars and monkeypatch restores them to whatever the process had —
# which used to be UNSET, i.e. back to the arm that can resolve the founder's
# real files. Pointing the process-level value at a path that does not exist
# means the restored state is a loud ENOENT instead: outside a test's window
# (collection, gaps, teardown races, a straggler request thread) the real
# sources.yaml / memory.md / data dir are not merely refused, they are
# UNREACHABLE — nothing can name them.
#
# Set at MODULE IMPORT, not in a pytest_configure hook, on purpose: this file
# re-exports tools.pytest_shuffle's `pytest_configure` (see the import above),
# and defining another one here would SHADOW it and silently disarm the shuffle
# plugin. Module import of the initial conftest is the same session scope.
_SESSION_FLOOR_ROOT = Path(
    os.environ.get("TMPDIR", "/tmp")) / f"newslens-suite-floor-{os.getpid()}"
# Deliberately NEVER created. Every value below is a path under a directory
# that does not exist, so a read raises ENOENT and a write raises ENOENT — no
# silent success, and nothing real is touched either way.
_SESSION_ENV_FLOOR = {
    "NEWSLENS_DATA_DIR": str(_SESSION_FLOOR_ROOT / "data"),
    "NEWSLENS_DB_PATH": str(_SESSION_FLOOR_ROOT / "data" / "newslens.db"),
    "NEWSLENS_SOURCES_FILE": str(_SESSION_FLOOR_ROOT / "sources.yaml"),
    "NEWSLENS_ENV_FILE": str(_SESSION_FLOOR_ROOT / ".env"),
    "NEWSLENS_MEMORY_FILE": str(_SESSION_FLOOR_ROOT / "memory.md"),
}
os.environ.update(_SESSION_ENV_FLOOR)

# ---- FLOOR 2: in-suite HTTP request threads are JOINED, never orphaned -----
#
# `http.server.ThreadingHTTPServer` sets `daemon_threads = True`; socketserver's
# `_Threads.append` drops daemon threads on the floor, so `server_close()` joins
# nothing and a handler can still be running after its test's fixtures have
# unwound. Flipping the class attribute (rather than editing the nine hand-rolled
# `ui` fixtures that copy the same four lines, and rather than adding a tenth
# helper they would have to adopt) makes `server_close()` block until every
# request thread has finished — for the fixtures that exist today AND for the
# next one somebody pastes.
#
# Safe here because every in-suite server is loopback, HTTP/1.0 (the stdlib
# default — no keep-alive, so a handler thread ends with its one response), and
# every fixture already calls `shutdown()` + `server_close()`. `block_on_close`
# is left at its stdlib default of True; with non-daemon threads that is what
# makes `_Threads` track and join them.
#
# DEVIATION, disclosed: QA's proposal says "the shared ui-server helper joins
# request threads". There is no shared helper — the idiom is copy-pasted across
# eight test files plus conftest's FakeAPI. This floor reaches all of them and
# every future copy, which is strictly more coverage than editing the eight.
http.server.ThreadingHTTPServer.daemon_threads = False


# Every env var the milestone-1 code reads, plus proxy vars that could
# redirect urllib away from our local fake server.
SCRUBBED_ENV_VARS = [
    "NEWSLENS_REAL_DATA",  # the paths-guard opt-in must never leak into tests
    "NEWSLENS_DATA_DIR",   # ambient redirections scrubbed; sandbox_paths sets
    "NEWSLENS_DB_PATH",    # its own per-test values after this scrub
    "NEWSLENS_SOURCES_FILE",
    "NEWSLENS_ENV_FILE",
    "NEWSLENS_MEMORY_FILE",
    # Stage-0 M1: the profile selector. The principal runs `serve` from a
    # shell that may legitimately export this, and that ambient value must
    # never decide which world a test resolves — every test starts as the
    # founder. (set_profile() deliberately does NOT export it, so there is no
    # in-test writer; the in-PROCESS half of the leak is handled by the
    # _PROFILE_OVERRIDE reset in sandbox_paths below.)
    "NEWSLENS_PROFILE",
    # The NL-102 discovery opt-in (2026-07-25 pause). Every test starts
    # PAUSED: an ambient `export NEWSLENS_DISCOVERY_ENABLED=1` in the shell the
    # suite runs from must never arm a metered path inside a test that did not
    # ask for it. Tests wanting the unpaused path pass it in an explicit env
    # dict (see tests/test_discovery.py::OPT_IN).
    "NEWSLENS_DISCOVERY_ENABLED",
    "OPENAI_API_KEY",
    "PERPLEXITY_API_KEY",
    "GNEWS_API_KEY",
    "BUDGET_CAP_USD_PER_RUN",
    "GENERATE_HOUR_LOCAL",
    # Provider seam (ADR-0014): llm.resolve_seat / llm.fallback_armed read these
    # at call time, so an ambient shell export must not leak into the suite (a
    # stray NEWSLENS_LANE=subscription would fail-loud real-path tests).
    # ANTHROPIC_API_KEY is now LIVE (B2 — the anthropic provider reads it as its
    # x-api-key), so it must be scrubbed so no test can make a real Claude call.
    # NEWSLENS_LANE_STATE joins the per-seat set (B2 gate ruling R1: the state
    # seat joined the seam).
    "ANTHROPIC_API_KEY",
    "NEWSLENS_LANE",
    "NEWSLENS_LANE_FALLBACK",
    "NEWSLENS_LANE_RANK",
    "NEWSLENS_LANE_ANALYST",
    "NEWSLENS_LANE_WRITER",
    "NEWSLENS_LANE_EDITOR",
    "NEWSLENS_LANE_SCRIPT",
    "NEWSLENS_LANE_SYNTHESIS",
    "NEWSLENS_LANE_STATE",
    # NL-17-M1: the follow-altitude resolver seat joined SEATS (a Haiku
    # subscription-default seat). resolve_seat reads its per-seat lane/model
    # override at call time like every other seat — scrub both so an ambient
    # shell export cannot leak into the suite (the NEWSLENS_MODEL_* class, below).
    "NEWSLENS_LANE_FOLLOW_ALTITUDE",
    # B4 (QA, the D2-hermeticity precedent): llm.resolve_seat now also reads
    # NEWSLENS_MODEL_<SEAT> at call time — the battery harness surface. The
    # principal's shell WILL export NEWSLENS_MODEL_WRITER around the ~07-24
    # battery runs, and an ambient value silently re-models/re-prices every
    # writer-seat request in the suite (proven to bite pre-fix:
    # `NEWSLENS_MODEL_WRITER=claude-fable-5 pytest tests/test_b1_llm_seam*.py`
    # failed 7 tests, QA run 2026-07-17). Scrub the whole seat family.
    "NEWSLENS_MODEL_RANK",
    "NEWSLENS_MODEL_ANALYST",
    "NEWSLENS_MODEL_WRITER",
    "NEWSLENS_MODEL_EDITOR",
    "NEWSLENS_MODEL_SCRIPT",
    "NEWSLENS_MODEL_SYNTHESIS",
    "NEWSLENS_MODEL_STATE",
    "NEWSLENS_MODEL_FOLLOW_ALTITUDE",   # NL-17-M1 resolver seat (see LANE note above)
    # B3: the subscription lane's binary override. Scrubbed here, then pointed
    # by sandbox_paths (below) at the canned-success STUB shim above — so no
    # test can ever resolve, let alone SPAWN, the real `claude` on this machine
    # (~/.local/bin/claude exists here and is the DEFAULT resolution leg). The
    # non-existent-sentinel alternative was rejected (ADR-0015: it reddened the
    # ~680 assertions that only need check_lane to pass); tests that need a
    # specific subprocess behaviour override this with their own shim. NOTE:
    # this env pin is process-inherited, NOT structural — a child spawned with
    # a HAND-BUILT env must pin this var itself or resolution falls through to
    # the real binary (the test_preinstall_doctor pinhole, fixed 2026-07-17).
    "NEWSLENS_CLAUDE_BIN",
    "http_proxy",
    "https_proxy",
    "all_proxy",
    "no_proxy",
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "ALL_PROXY",
    "NO_PROXY",
]


@pytest.fixture(autouse=True)
def scrub_env(monkeypatch):
    """Every test starts keyless and proxy-free unless it opts in explicitly."""
    for var in SCRUBBED_ENV_VARS:
        monkeypatch.delenv(var, raising=False)


# A synthetic zero-active-sources template. Since M2 the SHIPPED sources.yaml
# is seeded with the principal's live outlets — tests must never depend on its
# shape and must NEVER fetch its real feeds. Template-state behavior is a
# contract of its own, pinned against this synthetic file instead.
SYNTHETIC_TEMPLATE = (
    "# QA synthetic sources.yaml — template state: zero active sources.\n"
    "# sources:\n"
    "#   - name: Example Outlet\n"
    "#     rss_url: https://example.invalid/feed.xml\n"
)


@pytest.fixture(autouse=True)
def sandbox_paths(tmp_path, monkeypatch, scrub_env):
    """AUTOUSE (M5 escape postmortem): redirect all *stateful* newslens.paths
    locations into a sandbox for EVERY test, requested or not.

    Why autouse: when `generate` became a real verb at M5, a stale M1 pin
    (`cli.main(["generate"])`, no fixtures) executed the real pipeline —
    config.load_env() read the REAL .env (with a real key) because
    paths.ENV_FILE was only redirected for tests that opted into the fixture.
    Sandboxing must not be opt-in: no future newly-real verb may ever see
    real state from inside this suite.

    v7-M1 pinhole fix (2026-07-14): the module-attribute shadow is process-
    local, but tests legitimately spawn real entrypoints (scripts/doctor,
    the venv CLI) whose main() self-sanctions via allow_real_paths() — the
    doctor child ran its data-dir writability probe against the REAL data/.
    So the sandbox now also exports NEWSLENS_DATA_DIR/NEWSLENS_DB_PATH,
    which paths.__getattr__ resolves ahead of any sanction: every child that
    inherits the test environment lands in the sandbox too. (Depends on
    scrub_env so the ambient-value scrub happens before these are set.)

    MIGRATIONS_DIR, PROMPTS_DIR, PROJECT_ROOT stay real — they are the code
    under test. sources.yaml starts in the synthetic TEMPLATE state (zero
    active sources); tests write their own content over it as needed.
    """
    data_dir = tmp_path / "data"
    db_path = data_dir / "newslens.db"
    monkeypatch.setenv("NEWSLENS_DATA_DIR", str(data_dir))
    monkeypatch.setenv("NEWSLENS_DB_PATH", str(db_path))
    # setitem on the module dict, not setattr: reading the guarded names back
    # through getattr would consult the PEP 562 guard (and record whatever it
    # returned as the value to "restore", materializing a stale global).
    monkeypatch.setitem(vars(paths), "DATA_DIR", data_dir)
    monkeypatch.setitem(vars(paths), "DB_PATH", db_path)
    # In-process cli.main()/doctor.main() calls flip the process-wide
    # sanction and nothing unflips it; reset per test so a gap after a CLI
    # test never inherits the sanction of the test that ran before.
    monkeypatch.setattr(paths, "_REAL_PATHS_ALLOWED", False)
    # Same reasoning for the Stage-0 M1 profile pin: an in-process
    # cli.main(["--profile", "x", ...]) sets a module-level override that
    # nothing unsets. Every test starts in the founder's default profile.
    # (The NEWSLENS_PROFILE env half is handled by scrub_env above.)
    monkeypatch.setattr(paths, "_PROFILE_OVERRIDE", None)

    sources = tmp_path / "sources.yaml"
    sources.write_text(SYNTHETIC_TEMPLATE, encoding="utf-8")
    # setitem + setenv, same reasoning as DATA_DIR/DB_PATH above: the env
    # seams carry the sandbox across process boundaries (the 2026-07-16
    # memory.md incident: a serve child resolved the REAL file because
    # these three had no env seam).
    monkeypatch.setitem(vars(paths), "SOURCES_FILE", sources)
    monkeypatch.setenv("NEWSLENS_SOURCES_FILE", str(sources))
    monkeypatch.setitem(vars(paths), "ENV_FILE", tmp_path / ".env")  # absent
    monkeypatch.setenv("NEWSLENS_ENV_FILE", str(tmp_path / ".env"))
    # M4: memory.md is live principal state on this machine — the suite must
    # never read or write the real one.
    monkeypatch.setitem(vars(paths), "MEMORY_FILE", tmp_path / "memory.md")
    monkeypatch.setenv("NEWSLENS_MEMORY_FILE", str(tmp_path / "memory.md"))
    # B3 subprocess safety: point the subscription lane's binary resolution at
    # the DEFAULT canned-success stub shim (above), so the subscription-default
    # seats resolve their lane at the gate WITHOUT ever reaching the real
    # `claude` on this machine (a live, billable call — the thing the suite must
    # never do). A test that exercises the subprocess overrides this with its
    # own shim; a test asserting api-lane transport pins NEWSLENS_LANE_<SEAT>=api.
    monkeypatch.setenv("NEWSLENS_CLAUDE_BIN", str(_STUB_CLAUDE_BIN))
    # NL-156 (2026-08-14) — THE SAME GUARD, MADE STRUCTURAL. The env pin above
    # only protects resolution that READS os.environ. Now that check_lane
    # honours the env it is handed, any caller passing a HAND-BUILT mapping
    # (doctor.check_llm_lanes({}), effective_seat(seat, {...}) — the suite is
    # full of them) skips the env leg entirely: PATH resolves to "" by the
    # resolve_claude_bin rule, and the last leg, CLAUDE_BIN_DEFAULT, is a FIXED
    # MACHINE PATH that no env can redirect. On this machine
    # ~/.local/bin/claude exists, so that leg would hand product code the real
    # agent binary and make the suite's verdict depend on whether the developer
    # has the CLI installed. Kill the leg for the whole suite; the three tests
    # that exercise it (test_b3_subscription_lane_qa.py) monkeypatch their own
    # synthetic default on top of this, which still wins.
    from newslens import llm as _llm
    monkeypatch.setattr(
        _llm, "CLAUDE_BIN_DEFAULT", _NO_MACHINE_DEFAULT_CLAUDE)
    return tmp_path


@pytest.fixture
def tmp_paths(sandbox_paths):
    """Back-compat alias: the sandbox is autouse now; requesting tmp_paths
    just hands back its tmp_path root."""
    return sandbox_paths


@pytest.fixture(autouse=True)
def loopback_only_network(monkeypatch):
    """AUTOUSE structural guard (M5 escape postmortem, layer 2): the suite is
    offline-only BY CONSTRUCTION. DNS resolution and socket connects are
    allowed to loopback (the fake server) and refused everywhere else —
    so even a future sandboxing mistake cannot reach a real endpoint or
    spend money. The opt-in `no_network` fixture layers on top to record
    and refuse EVERYTHING, including loopback.

    NL-148 QA finding F6: `connect_ex` is a SECOND way out of this process.
    `socket.connect_ex` is not implemented in terms of `connect` — it is its own
    method returning an errno instead of raising — so guarding `connect` alone
    left the offline-by-construction claim with an API seam. Nothing exploits it
    today (`readerserve.port_is_free` is the only caller and it is a deliberate
    loopback skip-if-bound handshake), but the guard's promise is structural, so
    it covers both. A refused `connect_ex` returns ECONNREFUSED rather than
    raising — that IS the method's contract, and returning an error is the
    refusal."""
    real_getaddrinfo = socket.getaddrinfo
    real_connect = socket.socket.connect
    real_connect_ex = socket.socket.connect_ex

    def guarded_getaddrinfo(host, *args, **kwargs):
        if str(host) in ("127.0.0.1", "localhost", "::1"):
            return real_getaddrinfo(host, *args, **kwargs)
        raise OSError(
            f"QA suite is offline-only: DNS lookup for {host!r} refused "
            "(loopback_only_network structural guard)"
        )

    def _is_loopback(address):
        if not isinstance(address, tuple):  # AF_UNIX etc. — local by nature
            return True
        host = str(address[0])
        return host.startswith("127.") or host in ("::1", "localhost")

    def guarded_connect(self, address):
        if _is_loopback(address):
            return real_connect(self, address)
        raise OSError(
            f"QA suite is offline-only: connect to {address!r} refused "
            "(loopback_only_network structural guard)"
        )

    def guarded_connect_ex(self, address):
        if _is_loopback(address):
            return real_connect_ex(self, address)
        return errno.ECONNREFUSED

    monkeypatch.setattr(socket, "getaddrinfo", guarded_getaddrinfo)
    monkeypatch.setattr(socket.socket, "connect", guarded_connect)
    monkeypatch.setattr(socket.socket, "connect_ex", guarded_connect_ex)


def make_rss(items, channel_title="QA feed"):
    """Build a minimal-but-valid RSS 2.0 document for the fake server.

    Each item is a dict with optional keys: title, url, summary, pubdate
    (RFC-822 string). Omit a key to omit the element — lets tests craft
    entries missing url/title.
    """
    parts = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<rss version="2.0"><channel>',
        f"<title>{channel_title}</title>",
        "<link>http://qa.invalid/</link>",
        "<description>QA synthetic feed</description>",
    ]
    for item in items:
        parts.append("<item>")
        if "title" in item:
            parts.append(f"<title>{item['title']}</title>")
        if "url" in item:
            parts.append(f"<link>{item['url']}</link>")
        if "summary" in item:
            parts.append(f"<description><![CDATA[{item['summary']}]]></description>")
        if "pubdate" in item:
            parts.append(f"<pubDate>{item['pubdate']}</pubDate>")
        parts.append("</item>")
    parts.append("</channel></rss>")
    return "\n".join(parts).encode("utf-8")


@pytest.fixture
def no_network(monkeypatch):
    """Mechanical zero-network guard.

    Records every DNS lookup / socket connect attempt and refuses it. Tests
    assert the recording list is EMPTY — which distinguishes "never attempted
    a call" from "attempted one and the doctor swallowed the failure"
    (the latter would still show up here, plus as a 'could not reach' line).

    F6: `connect_ex` is recorded and refused here too — same seam, same reason
    as loopback_only_network above. A probe that slipped out through connect_ex
    would otherwise leave this fixture's list empty and its "never attempted a
    call" assertion would read as proof of something it never checked."""
    attempts = []

    def blocked_getaddrinfo(host, *args, **kwargs):
        attempts.append(("getaddrinfo", str(host)))
        raise socket.gaierror("network blocked by QA no_network guard")

    def blocked_connect(self, address):
        attempts.append(("connect", str(address)))
        raise OSError("network blocked by QA no_network guard")

    def blocked_connect_ex(self, address):
        attempts.append(("connect_ex", str(address)))
        return errno.ECONNREFUSED

    monkeypatch.setattr(socket, "getaddrinfo", blocked_getaddrinfo)
    monkeypatch.setattr(socket.socket, "connect", blocked_connect)
    monkeypatch.setattr(socket.socket, "connect_ex", blocked_connect_ex)
    return attempts


class _FakeAPIHandler(http.server.BaseHTTPRequestHandler):
    """Offline stand-in for api.openai.com / api.perplexity.ai / RSS hosts."""

    def log_message(self, *args):  # keep pytest output clean
        pass

    def _bearer(self) -> str:
        auth = self.headers.get("Authorization", "")
        return auth[len("Bearer "):] if auth.startswith("Bearer ") else ""

    def _send(self, code: int, body: bytes, ctype: str = "application/json") -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    # ENG-M0 (2026-08-06) — THE SSE ACCEPT, served centrally.
    # ranking.MAX_COMPLETION_TOKENS rose 3,000 -> 36,000, which crosses
    # llm._STREAM_MIN_MAX_TOKENS (5,000), so the rank seat now POSTs
    # `"stream": true` on the api lane exactly as the writer and analyst already
    # did. Before this, every rank api-lane test routed a NON-streaming JSON body
    # and the provider's SSE accumulator (correctly) rejected it with
    # "SSE stream ended without a message_stop event".
    #
    # THE FIX BELONGS HERE, NOT IN ~40 TESTS. The request itself says whether it
    # asked to stream, so the fake server can answer the way the WIRE would:
    # same routed payload, serialised either way. That keeps every existing
    # `route(..., body=anthropic_envelope(...))` call site correct and unchanged,
    # and — more importantly — it means a future seat crossing the streaming
    # threshold does not silently break its tests again.
    # Scoped tightly: POST /v1/messages, status 200, request asked to stream.
    # Error envelopes (401/429/400) and every non-anthropic route are untouched,
    # so the transport-failure taxonomy tests keep testing what they tested.
    def _maybe_stream(self, spec, body: bytes) -> "tuple":
        if self.path != "/v1/messages" or spec.get("status") != 200 or not body:
            return body, spec.get("content_type", "application/xml")
        if not (getattr(self, "_req_body", None) or {}).get("stream"):
            return body, spec.get("content_type", "application/xml")
        try:
            payload = json.loads(body.decode("utf-8"))
        except ValueError:
            return body, spec.get("content_type", "application/xml")
        return anthropic_sse_bytes(payload), "text/event-stream"

    def _try_route(self) -> bool:
        """Dynamic per-test routes (FakeAPI.add_route). Returns True if handled."""
        spec = self.server.routes.get(self.path)
        if spec is None:
            return False
        self.send_response(spec["status"])
        if spec.get("location"):
            self.send_header("Location", spec["location"])
        for name, value in (spec.get("headers") or {}).items():
            self.send_header(name, value)
        body, ctype = self._maybe_stream(spec, spec.get("body", b""))
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if body:
            self.wfile.write(body)
        return True

    def do_GET(self):
        self.server.recorded.append(
            {
                "method": "GET",
                "path": self.path,
                "user_agent": self.headers.get("User-Agent", ""),
            }
        )
        if self._try_route():
            return
        if self.path == "/v1/models":
            # Accepts the OpenAI bearer OR the anthropic x-api-key (B2: the doctor
            # validates ANTHROPIC_API_KEY with a read-only GET /v1/models too).
            ok = (self._bearer() == self.server.good_key
                  or self.headers.get("x-api-key", "") == self.server.good_key)
            if ok:
                self._send(
                    200,
                    json.dumps(
                        {"data": [{"id": "gpt-4o-mini"}, {"id": "gpt-4o"}]}
                    ).encode("utf-8"),
                )
            else:
                self._send(401, b'{"error": {"message": "bad key"}}')
        elif self.path == "/feed.xml":
            body = (
                b'<?xml version="1.0" encoding="UTF-8"?>\n'
                b'<rss version="2.0"><channel><title>QA feed</title>'
                b"</channel></rss>"
            )
            self._send(200, body, ctype="application/rss+xml")
        elif self.path == "/page.html":
            self._send(200, b"<html><body>not a feed</body></html>", ctype="text/html")
        elif self.path == "/boom":
            self._send(500, b'{"error": "server exploded"}')
        else:
            self._send(404, b'{"error": "not found"}')

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length)
        try:
            body = json.loads(raw.decode("utf-8"))
        except ValueError:
            body = None
        # ENG-M0: stashed so _maybe_stream can answer the way the wire would —
        # the REQUEST is what says whether this response should be SSE.
        self._req_body = body if isinstance(body, dict) else None
        self.server.recorded.append(
            {
                "method": "POST",
                "path": self.path,
                "user_agent": self.headers.get("User-Agent", ""),
                "body": body,
            }
        )
        if self._try_route():
            return
        if self.path == "/chat/completions":
            if self._bearer() == self.server.good_key:
                self._send(
                    200,
                    json.dumps(
                        {
                            "id": "qa-fake",
                            "model": "sonar",
                            "choices": [
                                {"message": {"role": "assistant", "content": "ok"}}
                            ],
                        }
                    ).encode("utf-8"),
                )
            else:
                self._send(401, b'{"error": {"message": "bad key"}}')
        elif self.path == "/v1/messages":
            # B2 Claude API lane: the anthropic provider authenticates with the
            # x-api-key header (its own credential, read from ANTHROPIC_API_KEY),
            # NOT a bearer token. Default canned response is anthropic-SHAPED; the
            # provider synthesises the OpenAI shape its callers parse. Per-test
            # bodies come via FakeAPI.add_route("/v1/messages", ...) with
            # anthropic_envelope(...).
            if self.headers.get("x-api-key", "") == self.server.good_key:
                # ENG-M0: the default canned reply streams too when asked.
                canned = anthropic_envelope("ok")
                if (self._req_body or {}).get("stream"):
                    self._send(200,
                               anthropic_sse_bytes(json.loads(canned.decode("utf-8"))),
                               ctype="text/event-stream")
                else:
                    self._send(200, canned)
            else:
                self._send(401, b'{"type": "error", '
                                b'"error": {"type": "authentication_error", '
                                b'"message": "bad key"}}')
        else:
            self._send(404, b'{"error": "not found"}')


class FakeAPI:
    def __init__(self):
        self.server = http.server.ThreadingHTTPServer(
            ("127.0.0.1", 0), _FakeAPIHandler
        )
        self.server.recorded = []
        self.server.routes = {}
        self.server.good_key = "sk-qa-local-fake-good-key-0000"
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def add_route(
        self,
        path: str,
        status: int = 200,
        body: bytes = b"",
        content_type: str = "application/xml",
        location: str = None,
        headers: dict = None,
    ) -> str:
        """Register a dynamic response for `path` (GET and POST). Returns the
        absolute URL. `location` adds a Location header (redirect tests);
        `headers` adds arbitrary extras (e.g. Retry-After)."""
        self.server.routes[path] = {
            "status": status,
            "body": body,
            "content_type": content_type,
            "location": location,
            "headers": headers,
        }
        return self.base_url + path

    @property
    def base_url(self) -> str:
        host, port = self.server.server_address
        return f"http://{host}:{port}"

    @property
    def good_key(self) -> str:
        return self.server.good_key

    @property
    def recorded(self):
        return self.server.recorded

    def dead_url(self, path: str = "/") -> str:
        """A 127.0.0.1 URL that refuses connections (nothing listens there)."""
        probe = socket.socket()
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
        probe.close()
        return f"http://127.0.0.1:{port}{path}"

    def stop(self):
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture
def fake_api():
    api = FakeAPI()
    yield api
    api.stop()


@pytest.fixture
def migrated_con(tmp_path):
    """A connection (FKs ON, via db.connect) to a freshly migrated scratch DB."""
    db_path = tmp_path / "schema-under-test.db"
    db.migrate(db_path=db_path)
    con = db.connect(db_path)
    yield con
    con.close()


# ---------------------------------------------------------------------------
# NL-126 — the fixture time-bomb pin (2026-07-31)
# ---------------------------------------------------------------------------
# THE CLASS: a test seeds an ABSOLUTE stamp ('2026-07-17T00:00:00.000Z' into
# source_items.fetched_at) and then calls a NOW-ANCHORED window — ranking's
# candidate_window (now - RECENCY_CAP_DAYS) or memory's apply_dormancy (now -
# DORMANT_AFTER_DAYS). Such a test is a bomb with a fuse: it passes until real
# time walks past the stamp, then fails for a reason that has nothing to do
# with the code under test. NL-126's four fired on the evening of 2026-07-30,
# 14 days after the 07-16/17 stamps, BETWEEN a gate run and its land.
#
# THE PIN: inject a frozen clock through the seams the product already exposes
# (`now_utc=`), so the seeded stamps and the window agree forever. Two laws
# this helper keeps:
#   * it NEVER re-dates fixture data (that hides the class and re-arms the
#     fuse at a later date), and
#   * it NEVER touches product code — `candidate_window(con, date, now_utc=…)`
#     and `apply_dormancy(con, now_utc=…)` are pre-existing seams; the only
#     reason a wrapper is needed at all is that `ranking.run_rank` does not
#     thread `now_utc` down to its window call (ranking.py:1782), so the pin
#     must sit on the callee.
# A caller-supplied now_utc always wins, so a test that already pins its own
# clock (test_ranking_selection.py's `now_utc=NOW` calls) is unaffected.

def pin_product_clock(monkeypatch, frozen):
    """NL-126 pin — freeze every NOW-ANCHORED window the ranking/memory paths
    read, at `frozen` (an aware UTC datetime). Call from an autouse fixture in
    any module whose seeds carry absolute stamps. Signature-preserving: a
    caller that passes its own `now_utc=` still wins, so modules that already
    pin their own clock are unaffected. monkeypatch owns the undo.

    Coupling to note: the pin freezes READS, not writes — rows written from
    the real clock (e.g. briefings.generated_at, ranking.py:1498) land in the
    frozen now's future, and candidate_window's clock-skew clamp
    (ranking.py:230) is what turns that into a 0.0d window rather than a
    negative one.
    """
    from newslens import memory as _memory
    from newslens import ranking as _ranking

    _real_window = _ranking.candidate_window
    _real_history = _ranking.ingested_history_days

    def _pinned_window(con, target_date, now_utc=None):
        return _real_window(con, target_date, now_utc=now_utc or frozen)

    def _pinned_history(con, now_utc=None):
        return _real_history(con, now_utc=now_utc or frozen)

    monkeypatch.setattr(_ranking, "candidate_window", _pinned_window)
    monkeypatch.setattr(_ranking, "ingested_history_days", _pinned_history)
    # memory.sync_memory calls apply_dormancy(con) with no now_utc (memory.py:845),
    # and run_rank calls sync_memory first — so the dormancy cap is on the same
    # fuse as the recency cap whenever a module seeds dated memory rows.
    monkeypatch.setattr(_memory, "_utc_now", lambda: frozen)
