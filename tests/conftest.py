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
"""

from __future__ import annotations

import http.server
import json
import os
import socket
import threading
from pathlib import Path

import pytest

from newslens import db, paths

PROTOTYPE_ROOT = Path(__file__).resolve().parents[1]

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


def _real_state_snapshot():
    snap = {}
    for d in (_REAL_DATA_DIR, _REAL_DATA_DIR / "briefings"):
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
    return snap


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
    """
    before = _real_state_snapshot()
    yield
    after = _real_state_snapshot()
    if after != before:
        diff = {
            k: {"before": before.get(k), "after": after.get(k)}
            for k in set(before) | set(after)
            if before.get(k) != after.get(k)
        }
        pytest.fail(
            "REAL state touched during this test (ENGINEERING.md 'no "
            f"real-state writes' — sandbox pinhole): {diff}",
            pytrace=False,
        )

# Every env var the milestone-1 code reads, plus proxy vars that could
# redirect urllib away from our local fake server.
SCRUBBED_ENV_VARS = [
    "NEWSLENS_REAL_DATA",  # the paths-guard opt-in must never leak into tests
    "NEWSLENS_DATA_DIR",   # ambient redirections scrubbed; sandbox_paths sets
    "NEWSLENS_DB_PATH",    # its own per-test values after this scrub
    "NEWSLENS_SOURCES_FILE",
    "NEWSLENS_ENV_FILE",
    "NEWSLENS_MEMORY_FILE",
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
    and refuse EVERYTHING, including loopback."""
    real_getaddrinfo = socket.getaddrinfo
    real_connect = socket.socket.connect

    def guarded_getaddrinfo(host, *args, **kwargs):
        if str(host) in ("127.0.0.1", "localhost", "::1"):
            return real_getaddrinfo(host, *args, **kwargs)
        raise OSError(
            f"QA suite is offline-only: DNS lookup for {host!r} refused "
            "(loopback_only_network structural guard)"
        )

    def guarded_connect(self, address):
        if not isinstance(address, tuple):  # AF_UNIX etc. — local by nature
            return real_connect(self, address)
        host = str(address[0])
        if host.startswith("127.") or host in ("::1", "localhost"):
            return real_connect(self, address)
        raise OSError(
            f"QA suite is offline-only: connect to {address!r} refused "
            "(loopback_only_network structural guard)"
        )

    monkeypatch.setattr(socket, "getaddrinfo", guarded_getaddrinfo)
    monkeypatch.setattr(socket.socket, "connect", guarded_connect)


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
    """
    attempts = []

    def blocked_getaddrinfo(host, *args, **kwargs):
        attempts.append(("getaddrinfo", str(host)))
        raise socket.gaierror("network blocked by QA no_network guard")

    def blocked_connect(self, address):
        attempts.append(("connect", str(address)))
        raise OSError("network blocked by QA no_network guard")

    monkeypatch.setattr(socket, "getaddrinfo", blocked_getaddrinfo)
    monkeypatch.setattr(socket.socket, "connect", blocked_connect)
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
        body = spec.get("body", b"")
        self.send_header("Content-Type", spec.get("content_type", "application/xml"))
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
                self._send(200, anthropic_envelope("ok"))
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
