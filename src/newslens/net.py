"""Shared HTTP plumbing for feed fetching (ingest + doctor).

One opener, one 308 story, one size cap — M2 review carryovers 5-7 wanted the
ingest/doctor fetch behavior to be the SAME behavior, so a feed that works in
the pipeline can't fail in the doctor or vice versa.

Stdlib-only (doctor imports this pre-install).
"""

from __future__ import annotations

import urllib.request
from typing import Optional

USER_AGENT = "NewsLens/0.1 (personal news briefing prototype; RSS reader)"
MAX_FEED_BYTES = 4_000_000  # generous: real-world feeds run 10KB-1MB


class Redirect308Handler(urllib.request.HTTPRedirectHandler):
    """Python 3.9's urllib does not follow HTTP 308 (support landed in 3.11).
    Real outlets in the principal's list 308 (found in the M2 sweep), so
    treat 308 exactly like 301 — everywhere, identically."""

    def http_error_308(self, req, fp, code, msg, headers):  # noqa: N802 (urllib API)
        return self.http_error_301(req, fp, 301, msg, headers)


OPENER = urllib.request.build_opener(Redirect308Handler())


def fetch_bytes(
    url: str,
    timeout: int,
    cap: int = MAX_FEED_BYTES,
    user_agent: str = USER_AGENT,
) -> bytes:
    """GET with the shared opener, explicit timeout, and a hard byte cap.

    Reads cap+1 bytes and refuses oversize bodies loudly (M2 QA observation 3:
    items were capped, bytes were not) — a visible per-source failure, never
    an unbounded read.
    """
    req = urllib.request.Request(url, headers={"User-Agent": user_agent})
    with OPENER.open(req, timeout=timeout) as resp:
        body = resp.read(cap + 1)
    if len(body) > cap:
        raise ValueError(f"response exceeds the {cap}-byte feed size cap")
    return body


def head_bytes(url: str, timeout: int, n: int = 4096, user_agent: str = USER_AGENT) -> "tuple[bytes, int]":
    """First n bytes + HTTP status, for feed-shape sniffing (doctor)."""
    req = urllib.request.Request(url, headers={"User-Agent": user_agent})
    with OPENER.open(req, timeout=timeout) as resp:
        return resp.read(n), resp.getcode()
