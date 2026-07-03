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
import socket
import threading
from pathlib import Path

import pytest

from newslens import db, paths

PROTOTYPE_ROOT = Path(__file__).resolve().parents[1]

# Every env var the milestone-1 code reads, plus proxy vars that could
# redirect urllib away from our local fake server.
SCRUBBED_ENV_VARS = [
    "OPENAI_API_KEY",
    "PERPLEXITY_API_KEY",
    "GNEWS_API_KEY",
    "BUDGET_CAP_USD_PER_RUN",
    "GENERATE_HOUR_LOCAL",
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


@pytest.fixture
def tmp_paths(tmp_path, monkeypatch):
    """Redirect all *stateful* newslens.paths locations into a sandbox.

    MIGRATIONS_DIR, PROMPTS_DIR, PROJECT_ROOT stay real — they are the code
    under test. The sandbox gets a byte-for-byte copy of the *shipped*
    sources.yaml template so template-state tests exercise the real artifact.
    """
    data_dir = tmp_path / "data"
    monkeypatch.setattr(paths, "DATA_DIR", data_dir)
    monkeypatch.setattr(paths, "DB_PATH", data_dir / "newslens.db")

    sources = tmp_path / "sources.yaml"
    sources.write_text(
        (PROTOTYPE_ROOT / "sources.yaml").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    monkeypatch.setattr(paths, "SOURCES_FILE", sources)
    monkeypatch.setattr(paths, "ENV_FILE", tmp_path / ".env")  # does not exist
    return tmp_path


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

    def do_GET(self):
        self.server.recorded.append(
            {
                "method": "GET",
                "path": self.path,
                "user_agent": self.headers.get("User-Agent", ""),
            }
        )
        if self.path == "/v1/models":
            if self._bearer() == self.server.good_key:
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
        else:
            self._send(404, b'{"error": "not found"}')


class FakeAPI:
    def __init__(self):
        self.server = http.server.ThreadingHTTPServer(
            ("127.0.0.1", 0), _FakeAPIHandler
        )
        self.server.recorded = []
        self.server.good_key = "sk-qa-local-fake-good-key-0000"
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

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
