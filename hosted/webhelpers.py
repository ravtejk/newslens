"""The pure helpers the hosted app leans on — no routes, no app, no state.

NL-163 Stage-A milestone 4, first work, per the M3 gate ruling §6: `hosted/
app.py` had grown to 850 lines against the adjudication's ~400 bar, and the
gate ordered the split BEFORE M4 added to it, so that M4's own proof stack
runs once against the split bytes rather than twice against two shapes.

WHAT LIVES HERE AND WHAT DOES NOT. Everything in this file is a function of
its arguments (plus, for two of them, the current Flask request). No route is
registered here, no app is constructed here, nothing is written here. The
door itself — the boot refusal, `current_user`, the exchange, the push
endpoint, the CSP — stays in `hosted/app.py`, because a reader auditing the
credential surface should find it in one file.

THE MOVE WAS BYTES, NOT MEANING. Every function below arrived here by
extraction from `hosted/app.py` at sha `4f788c0c…` with its body unchanged to
the byte; `hosted/app.py` re-exports each name, so every call site inside the
app and every existing test import (`hosted_app._safe_next`) resolves exactly
as before. That is deliberate: relocating security-relevant arithmetic —
`_safe_next` is the open-redirect gate, `_stream_for_token` is the
constant-time push-token compare, `_bundle_problem` is the server-side digest
check, `_cookie_secure` is the fail-closed `Secure` decision — is only safe
if "moved" can be proven rather than promised.

TWO FUNCTIONS HAVE BEEN EDITED SINCE THAT EXTRACTION, and saying so is the
point of this paragraph — the byte-identity claim above is about the MOVE, and
it stays true of the move; it is not a claim about the file for ever. Both
edits landed in the M4 gate's fix loop, both are named where they sit, and both
carry a receipt: `_bundle_problem` gained the push-time anchor gate (FIX-1 —
pre-fix, a bundle with no `</head>` was stored 200 and then every read answered
500), and `_safe_next` gained the C0/DEL refusal that makes its own docstring
true (FIX-4 — pre-fix, `/foo\x00bar` was kept). Nothing else in this file has
changed since the move.

`_is_loopback` came along for a reason worth naming: `_cookie_secure` cannot
be a pure helper without it, and copying it would put a second spelling of
"is this address on this machine?" in a codebase whose whole M2/M3 posture
rests on there being exactly one. `hosted/app.py` imports it back for the
boot refusal and the bypass backstop.
"""

from __future__ import annotations

import hashlib
import hmac
import re
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Dict, Optional, Tuple

from flask import jsonify, request

from . import hoststore

if TYPE_CHECKING:                    # pragma: no cover - typing only
    # Never imported at runtime: `hosted.app` imports THIS module, so a real
    # import here would be a cycle. The annotation survives as a string
    # (`from __future__ import annotations`), so the signature below reads the
    # same as it did in app.py without costing an import.
    from .app import Config


_LOOPBACK = {"127.0.0.1", "localhost", "::1", "[::1]", "0177.0.0.1"}


def _is_loopback(host: str) -> bool:
    h = (host or "").strip().strip("[]").lower()
    if h in _LOOPBACK or h == "127.0.0.1":
        return True
    if h.startswith("127.") and re.match(r"^127(\.\d{1,3}){3}$", h):
        return True
    return False


def _theme_colors(tokens_css: Path) -> Tuple[Optional[str], Optional[str]]:
    """(light, dark) `--paper`, read out of the generated palette."""
    try:
        css = tokens_css.read_text(encoding="utf-8")
    except OSError:
        return None, None
    out = []
    for opener in (":root", "body.dark"):
        start = css.find(opener + " {")
        if start < 0:
            start = css.find(opener + "{")
        m = re.search(r"--paper\s*:\s*(#[0-9A-Fa-f]{3,8})\s*;",
                      css[start:] if start >= 0 else "")
        out.append(m.group(1) if m else None)
    return out[0], out[1]


def _dateline_ctx(date: str) -> Dict[str, str]:
    """The masthead dateline's parts, in the shipped grammar
    (server.py::_dateline_html — 'Friday, July [10] [2026]')."""
    try:
        d = datetime.strptime(date, "%Y-%m-%d")
    except ValueError:
        return {"dateline_prefix": date, "dateline_day": "", "dateline_year": ""}
    return {"dateline_prefix": d.strftime("%A, %B"),
            "dateline_day": str(d.day), "dateline_year": str(d.year)}


def _archive_label(date: str) -> str:
    try:
        return hoststore.weekday_name(date) + datetime.strptime(
            date, "%Y-%m-%d").strftime(", %B %-d")
    except ValueError:
        return date


def _safe_next(raw) -> str:
    """A place on THIS paper, or the front page.

    An open redirect is the classic way a sign-in page becomes a phishing
    stepping stone: `/login?next=https://evil.example` sends a reader who did
    everything right to somebody else's imitation of this paper. So the return
    path is not sanitised, it is CHECKED — one leading slash, no scheme, no
    authority, no control characters — and anything that fails goes to `/`.
    Refused rather than repaired: a value we had to fix is a value we did not
    understand."""
    if not isinstance(raw, str) or not raw:
        return "/"
    if len(raw) > 512:
        return "/"
    if not raw.startswith("/") or raw.startswith("//") or raw.startswith("/\\"):
        return "/"
    if any(ch in raw for ch in ("\r", "\n", "\t", "\\", " ")):
        return "/"
    # THE DOCSTRING'S "no control characters", MADE TRUE (M4 gate FIX-4,
    # closing the M3 gate's C0 ticket). The tuple above names the five bytes
    # that actually smuggle, and two of them — backslash and space — are not
    # control characters at all, so the claim above it was wider than the code:
    # `/foo\x00bar` was KEPT (measured at the M3 gate). The whole C0 range plus
    # DEL is refused here by codepoint. Still refused rather than stripped: a
    # value we had to repair is a value we did not understand.
    if any(ord(ch) < 0x20 or ord(ch) == 0x7f for ch in raw):
        return "/"
    if ":" in raw.split("?", 1)[0]:            # `/\x2f`-style scheme smuggling
        return "/"
    return raw


def _cookie_secure() -> bool:
    """`Secure` unless this is a plaintext request from this machine.

    FAIL-CLOSED IN THE DIRECTION THAT MATTERS: anything not on the loopback
    gets the flag, so a cookie can never be handed to a remote reader over
    plaintext. The one exemption is the local development loop and the browser
    walk, where the origin is `http://127.0.0.1` and a `Secure` cookie would
    simply never be stored — the seam would be untestable and nobody would be
    protected by it.

    A MISSING `remote_addr` IS LOOPBACK, exactly as the bypass backstop reads
    it (that is the in-process test client, which speaks no socket at all)."""
    if request.is_secure:
        return True
    addr = request.remote_addr
    return not (not addr or _is_loopback(addr))


def _bearer(header: str) -> str:
    parts = (header or "").split(None, 1)
    return parts[1].strip() if len(parts) == 2 and parts[0].lower() == "bearer" else ""


def _stream_for_token(cfg: Config, token: str) -> Optional[str]:
    """Which stream this token publishes, or None.

    The digest is compared with `hmac.compare_digest` — constant time, so a
    token cannot be discovered a byte at a time by watching how long the
    refusal takes. Every configured stream is compared even after a match, for
    the same reason."""
    if not token:
        return None
    digest = hashlib.sha256(token.encode("utf-8")).hexdigest()
    found = None
    for stream, expected in cfg.push_tokens.items():
        if hmac.compare_digest(digest, str(expected).strip().lower()):
            found = found or stream
    return found


def _bundle_problem(bundle, date: str) -> Optional[str]:
    """Everything that makes a body not an edition of `date`, in one place."""
    if not isinstance(bundle, dict):
        return "body is not an edition bundle (not a JSON object)"
    html = bundle.get("html")
    if not isinstance(html, str) or not html.strip():
        return "bundle has no html"
    sha = bundle.get("content_sha256")
    if not isinstance(sha, str) or not re.fullmatch(r"[0-9a-f]{64}", sha):
        return "bundle has no content_sha256"
    actual = hashlib.sha256(html.encode("utf-8")).hexdigest()
    if not hmac.compare_digest(actual, sha):
        # VERIFIED SERVER-SIDE (Q4). A truncated upload and a corrupted one
        # look identical to a length check; they do not look identical to a
        # digest.
        return f"content_sha256 does not match the html ({actual} computed)"
    if bundle.get("edition_date") != date:
        return (f"bundle is dated {bundle.get('edition_date')!r} but was "
                f"pushed to {date}")
    for anchor in ("</head>", "</body>"):
        if html.count(anchor) != 1:
            # THE PUBLISH PATH REFUSES WHAT THE SERVE PATH CANNOT SERVE (M4
            # gate FIX-1). `augment_edition` requires each anchor exactly once
            # and raises otherwise; without this check a sha-valid bundle
            # missing `</head>` was STORED 200, `latest` moved to it, and every
            # subsequent read — the front page first — answered 500 until a
            # good re-push. The refusal belongs at the push, where M2's own
            # philosophy puts it: a misconfigured Mac is loud on its FIRST
            # push, not silently at a reader's 6am. The read-time raise stays
            # as defence in depth for bundles that reached the shelf by some
            # other road.
            return (f"bundle html cannot be augmented: {anchor} occurs "
                    f"{html.count(anchor)} times, expected exactly 1")
    return None


def _err(status: int, detail: str):
    """An honest refusal: a status and a sentence, never a stack trace and
    never a page. The Mac's push client prints this sentence verbatim."""
    return jsonify({"error": detail}), status
