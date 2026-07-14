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
    "NEWSLENS_REAL_DATA",  # the paths-guard opt-in must never leak into tests
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
def sandbox_paths(tmp_path, monkeypatch):
    """AUTOUSE (M5 escape postmortem): redirect all *stateful* newslens.paths
    locations into a sandbox for EVERY test, requested or not.

    Why autouse: when `generate` became a real verb at M5, a stale M1 pin
    (`cli.main(["generate"])`, no fixtures) executed the real pipeline —
    config.load_env() read the REAL .env (with a real key) because
    paths.ENV_FILE was only redirected for tests that opted into the fixture.
    Sandboxing must not be opt-in: no future newly-real verb may ever see
    real state from inside this suite.

    MIGRATIONS_DIR, PROMPTS_DIR, PROJECT_ROOT stay real — they are the code
    under test. sources.yaml starts in the synthetic TEMPLATE state (zero
    active sources); tests write their own content over it as needed.
    """
    data_dir = tmp_path / "data"
    monkeypatch.setattr(paths, "DATA_DIR", data_dir, raising=False)
    monkeypatch.setattr(paths, "DB_PATH", data_dir / "newslens.db",
                        raising=False)

    sources = tmp_path / "sources.yaml"
    sources.write_text(SYNTHETIC_TEMPLATE, encoding="utf-8")
    monkeypatch.setattr(paths, "SOURCES_FILE", sources)
    monkeypatch.setattr(paths, "ENV_FILE", tmp_path / ".env")  # does not exist
    # M4: memory.md is live principal state on this machine — the suite must
    # never read or write the real one.
    monkeypatch.setattr(paths, "MEMORY_FILE", tmp_path / "memory.md")
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
