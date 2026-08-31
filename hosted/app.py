"""THE READER RUNS AND RECEIVES — the hosted paper.

NL-163 Stage-A milestone 2, per the engineering adjudication 2026-08-29 (§Q2
service shape, §Q4 push transport, §Q5 multi-user, §Q6 read ledger) and the
principal's "build that whole thing out and make sure it works".

WHAT THIS SERVICE IS. A door and a shelf. The Mac generates an edition, freezes
it into one self-contained document, and PUSHes it here; a reader signs in and
is served that document. Nothing here renders an edition, because rendering it
here would mean a second renderer for the same paper — the drift class the
bundle exists to end. The one thing this service adds to a frozen document is a
tail: the data island and the shell script that let the service worker say
`Offline · pushed at 06:12`.

MULTI-USER BY CONSTRUCTION (the USERBASE DIRECTIVE). One authenticated user
sees ONE stream: `NEWSLENS_USER_STREAMS` maps user -> stream, and a different
account is a different paper. No picker, no in-shell identity, no founder
strings anywhere in this file — the register is "the paper" and "the paper's
operator".

AUTH IS REAL FROM M3 (the principal's ruling 2026-08-31, "Lets do c" —
Stytch): `current_user()` verifies a session JWT LOCALLY against a cached
JWKS (hosted/sessionauth.py), and THIS SERVICE HOLDS NO VENDOR SECRET. The
development bypass survives unchanged in posture — `NEWSLENS_DEV_NO_AUTH=1`
still refuses to boot on any non-loopback bind and is still refused per
request from any non-loopback caller (see `dev_bypass_guard`).
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import shlex
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from urllib.parse import quote

from flask import (Flask, Response, abort, jsonify, redirect, render_template,
                   request, send_from_directory)

from . import hoststore, sessionauth
from .hoststore import HostStore, StoreError, valid_date, valid_stream
from .sessionauth import SessionConfig, SessionInvalid, SessionVerifier

# 10MB (adjudication Q4). Renegotiated when audio joins the envelope; a real
# edition measures ~150KB today, so this is three orders of headroom and still
# a hard stop on a body that is not an edition.
MAX_BUNDLE_BYTES = 10 * 1024 * 1024

# PROTOTYPE: faked — the ONLY faked seam in this service (team/ENGINEERING.md
# "what's faked" list). It exists so the local loop can drive every state
# without a vendor account; real session verification is live beside it.
DEV_NO_AUTH_VAR = "NEWSLENS_DEV_NO_AUTH"

# THE SESSION COOKIE. HttpOnly (no script on any page can read it — including
# the login page's own, including a vendor's), Secure wherever the connection
# is not a plaintext loopback one, SameSite=Lax (it must survive the top-level
# navigation back from /login, and Lax is the tightest value that does).
#
# THE COOKIE'S LIFETIME IS NOT THE SESSION'S LIFETIME, and the distinction is
# the §9 law's whole mechanism: the cookie holds a vendor JWT that expires in
# five minutes by the vendor's design, while the cookie itself lives ≥90 days
# so the DEVICE stays enrolled. When the JWT inside has expired, the login
# page silently exchanges the long-lived vendor session for a fresh one — the
# reader types nothing. Credentials once per device (addendum §9), by the only
# arrangement that holds it without this box holding a vendor secret.
SESSION_COOKIE = "nl_session"
# 366 days — the vendor's own documented session maximum
# (`session_duration_minutes` max 527,040), so the cookie never dies before the
# session it carries. The ratified law asks for ≥90 rolling; this is 4×.
SESSION_COOKIE_MAX_AGE = 366 * 24 * 60 * 60

# A same-origin custom header is required on the session POST. A cross-site
# form or image cannot set one without a CORS preflight, and this service
# answers no preflight — so a hostile page cannot log a reader into the
# attacker's account (session fixation) or out of their own.
SESSION_REQUEST_HEADER = "X-NewsLens-Session"

# Content-Security-Policy. `unsafe-inline` for scripts is not laxness: a frozen
# edition carries its own inline boot script and its own inline stylesheet by
# construction (that is what "one self-contained document" means), and hashing
# them would put a second copy of the bundle's bytes in this file. What the
# policy DOES pin is the thing M3 must pin — SCRIPT ORIGINS. Only the login
# page may reach the vendor; every other route, the served edition above all,
# admits no third-party script at all.
_CSP_COMMON = (
    "default-src 'self'; "
    # SPELLED OUT rather than left to the fallback chain. A worker's script is
    # governed by `worker-src`, which falls back through `child-src` to
    # `script-src` — and `script-src` on the login route names the vendor. The
    # offline morning is too important to rest on which fallback a browser
    # implements: the service worker is same-origin, always, on every route.
    "worker-src 'self'; "
    "style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data:; "
    "font-src 'self' data:; "
    "form-action 'none'; "
    "frame-ancestors 'none'; "
    "base-uri 'none'; "
    "object-src 'none'")
CSP_READER = ("script-src 'self' 'unsafe-inline'; connect-src 'self'; "
              + _CSP_COMMON)
CSP_LOGIN = (f"script-src 'self' 'unsafe-inline' {sessionauth.SDK_ORIGIN}; "
             f"connect-src 'self' {sessionauth.VENDOR_API_ORIGIN}; "
             f"frame-src 'self' {sessionauth.VENDOR_API_ORIGIN}; "
             + _CSP_COMMON)

_LOOPBACK = {"127.0.0.1", "localhost", "::1", "[::1]", "0177.0.0.1"}
# Signals that say "this process is on a platform, not on somebody's laptop".
# Cheap, and they close the failure Rook named: the bypass ending up on the
# internet one tired evening.
_PLATFORM_VARS = ("FLY_APP_NAME", "FLY_ALLOC_ID", "FLY_MACHINE_ID",
                  "KUBERNETES_SERVICE_HOST", "DYNO", "RENDER",
                  "AWS_EXECUTION_ENV", "GOOGLE_CLOUD_PROJECT")


# ---------------------------------------------------------------------------
# THE BOOT REFUSAL (Rook's condition; violating it is a review BLOCK)
# ---------------------------------------------------------------------------

def _is_loopback(host: str) -> bool:
    h = (host or "").strip().strip("[]").lower()
    if h in _LOOPBACK or h == "127.0.0.1":
        return True
    if h.startswith("127.") and re.match(r"^127(\.\d{1,3}){3}$", h):
        return True
    return False


def _binds_in(tokens: List[str]) -> List[str]:
    """gunicorn's own three spellings — `-b X`, `--bind X`, `--bind=X`. One
    grammar, applied to every token list that can carry it."""
    out: List[str] = []
    for i, arg in enumerate(tokens or []):
        if arg in ("-b", "--bind") and i + 1 < len(tokens):
            out.append(tokens[i + 1])
        elif arg.startswith("--bind="):
            out.append(arg.split("=", 1)[1])
    return out


def declared_binds(env: Dict[str, str], argv: List[str]) -> List[str]:
    """Every address this process can tell it was asked to listen on.

    FOUR SOURCES, because the process is started several ways and only one of
    them is ours: gunicorn's own argv (gunicorn imports this module in the
    worker, so its `-b/--bind` is right there in `sys.argv`), GUNICORN_CMD_ARGS
    (gunicorn reads its flags from there too, so a bare `gunicorn
    hosted.app:app` can be bound to the world by a variable the argv scan
    cannot see — found in review, walked as a real process), the environment a
    Dockerfile or fly.toml sets, and `main()`'s parsed `--host`.

    A bind we cannot see is still not treated as safe: `dev_bypass_guard`'s
    platform check catches the deployed case without needing the address, and
    the request-time guard in `create_app` catches the rest. Enumerating
    channels here is a race this function cannot win alone — a `-c
    gunicorn.conf.py` holding `bind=` is the same class and is not parsed."""
    binds = _binds_in(argv or [])
    raw = env.get("GUNICORN_CMD_ARGS")
    if raw:
        try:
            binds.extend(_binds_in(shlex.split(raw)))
        except ValueError:              # unbalanced quotes; scan it raw
            binds.extend(_binds_in(raw.split()))
    for var in ("NEWSLENS_BIND", "GUNICORN_BIND", "HOST", "NEWSLENS_HOST"):
        if env.get(var):
            binds.append(env[var])
    return binds


def dev_bypass_guard(env: Dict[str, str] = None, argv: List[str] = None,
                     host: str = None) -> None:
    """Refuse to run the auth bypass anywhere a stranger could reach.

    THE RULE, stated once: `NEWSLENS_DEV_NO_AUTH=1` is a LOCAL-LOOP-ONLY seam.
    It exists so the milestone's own verification loop can drive five states
    and a service worker without a vendor account, and for no other reason. If
    the process is bound to anything but loopback, or is running on a platform
    that gives it a public address, this raises SystemExit at import — which
    kills gunicorn before it accepts a connection.

    Deliberately fail-CLOSED on ambiguity: an unrecognised bind string is
    treated as non-loopback, because the cost of a false refusal is one
    puzzled minute and the cost of a false permit is a paper on the internet
    with its door open."""
    env = os.environ if env is None else env
    argv = sys.argv if argv is None else argv
    if str(env.get(DEV_NO_AUTH_VAR, "")).strip().lower() not in ("1", "true", "yes"):
        return

    platform = [v for v in _PLATFORM_VARS if env.get(v)]
    if platform:
        raise SystemExit(
            f"REFUSING TO BOOT: {DEV_NO_AUTH_VAR} is on and this process is "
            f"running on a hosting platform ({', '.join(platform)} is set). "
            "The no-auth bypass is a loopback-only development seam; unset it "
            "and configure real authentication (hosted/DEPLOY.md).")

    candidates = list(declared_binds(env, argv))
    if host:
        candidates.append(host)
    for bind in candidates:
        addr = bind.rsplit(":", 1)[0] if bind.count(":") == 1 else bind
        if bind.startswith("["):                       # [::1]:8080
            addr = bind.split("]")[0] + "]"
        if not _is_loopback(addr):
            raise SystemExit(
                f"REFUSING TO BOOT: {DEV_NO_AUTH_VAR} is on and this process "
                f"is bound to {bind!r}, which is not loopback. The no-auth "
                "bypass may only run on 127.0.0.1/::1. Either drop the "
                "variable or bind to loopback.")


# ---------------------------------------------------------------------------
# Configuration — every knob is an env var, no config file, no secrets in-tree
# ---------------------------------------------------------------------------

class Config:
    def __init__(self, env: Dict[str, str] = None):
        env = os.environ if env is None else env
        self.env = env
        self.data_dir = Path(env.get("NEWSLENS_HOSTED_DATA") or "/data")
        # {stream: sha256(token)} — the DIGESTS live here, never the tokens
        # (Rook item 1: hashed at rest). `newslens push` holds the secret half.
        self.push_tokens = _json_map(env.get("NEWSLENS_PUSH_TOKENS"))
        # {user_id: stream} — one account, one paper (Q5).
        self.user_streams = _json_map(env.get("NEWSLENS_USER_STREAMS"))
        self.dev_no_auth = str(env.get(DEV_NO_AUTH_VAR, "")).strip().lower() in (
            "1", "true", "yes")
        self.dev_user = env.get("NEWSLENS_DEV_USER") or ""
        # The lock's four facts + the public token. No secret is read here,
        # and there is no env var this service accepts that could carry one.
        self.session = SessionConfig(env)
        self.max_bytes = int(env.get("NEWSLENS_MAX_BUNDLE_BYTES")
                             or MAX_BUNDLE_BYTES)
        # The paper's day boundary, in minutes from UTC. Only "is the latest
        # edition today's?" depends on it — the panel a reader sees before the
        # morning push lands. Default UTC; an operator whose press runs in
        # US/Pacific sets -420 so a 5pm open does not claim the morning's
        # edition is yesterday's. Named in DEPLOY.md.
        try:
            self.day_offset = timedelta(
                minutes=int(env.get("NEWSLENS_DAY_OFFSET_MINUTES") or 0))
        except ValueError:
            self.day_offset = timedelta(0)

    def today(self) -> str:
        return (datetime.now(timezone.utc) + self.day_offset).strftime("%Y-%m-%d")


def _json_map(raw: Optional[str]) -> Dict[str, str]:
    """A {str: str} env map, or empty. A malformed map is empty and loud in the
    logs rather than a half-parsed one — the two things it configures (who may
    push, who may read) both fail CLOSED when it is empty."""
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except ValueError:
        print("newslens-hosted: could not parse an env JSON map — treating it "
              "as empty (nobody is authorised)", file=sys.stderr)
        return {}
    if not isinstance(parsed, dict):
        return {}
    return {str(k): str(v) for k, v in parsed.items()}


# ---------------------------------------------------------------------------
# The frozen document's one addition
# ---------------------------------------------------------------------------

_HEAD_LINKS = (
    '<link rel="manifest" href="/static/manifest.webmanifest">'
    '<link rel="apple-touch-icon" href="/static/apple-touch-icon.png">'
    '<link rel="icon" type="image/png" href="/static/favicon.png">'
)


def augment_edition(html: str, island: Dict) -> str:
    """Add the PWA head links and the shell tail to a FROZEN document.

    THE FROZEN DOCUMENT IS NOT REWRITTEN. Two insertions at two anchors, each
    required to occur exactly once — removing the inserted spans returns the
    bundle's html byte for byte, and the suite pins exactly that. The tail is
    what fills M1's empty offline slot: the bundle cannot know whether it
    arrived from the network or from a service-worker cache, and the host
    cannot know either; only the client can, so only the client fills it."""
    tail = (
        '<script id="newslens-edition" type="application/json">'
        + json.dumps(island, ensure_ascii=False).replace("<", "\\u003c")
        + '</script><script src="/static/shell.js" defer></script>')
    for anchor, insert in (("</head>", _HEAD_LINKS), ("</body>", tail)):
        if html.count(anchor) != 1:
            # The bundle's own single-occurrence discipline (M1 FIX-3): an
            # anchor that appears twice would silently insert into the wrong
            # place, and a document that is silently wrong is the failure this
            # whole milestone is built to avoid.
            raise StoreError(
                f"cannot augment this document: {anchor} occurs "
                f"{html.count(anchor)} times, expected exactly 1")
        html = html.replace(anchor, insert + anchor, 1)
    return html


# ---------------------------------------------------------------------------
# The app
# ---------------------------------------------------------------------------

def create_app(env: Dict[str, str] = None, host: str = None,
               jwks_fetcher=None) -> Flask:
    dev_bypass_guard(env, host=host)
    app = Flask(__name__)
    cfg = Config(env)
    store = HostStore(cfg.data_dir)
    # ONE verifier per app: the key cache is shared by every worker thread and
    # takes its own lock, so a busy morning fetches the JWKS once, not once per
    # request. `jwks_fetcher` is the F1 test seam — the suite hands in a
    # fixture key set and NO SOCKET IS EVER OPENED under test.
    verifier = SessionVerifier(cfg.session, fetcher=jwks_fetcher)
    app.config["NEWSLENS"] = cfg
    app.config["NEWSLENS_STORE"] = store
    app.config["NEWSLENS_VERIFIER"] = verifier
    app.config["MAX_CONTENT_LENGTH"] = cfg.max_bytes + 1024

    # -- auth --------------------------------------------------------------

    def current_user() -> Optional[str]:
        """The signed-in reader, or None.

        THE WHOLE OF THE LOCK IS HERE: one cookie, verified locally against a
        cached public key. No vendor round trip on the reading path, no vendor
        secret in this process, and no route below has to know which vendor it
        is — which is what makes the recorded falsifier (if live-mode passkeys
        turn out gated, Clerk re-enters) a configuration change.

        A REFUSAL IS NOT SWALLOWED: it becomes a logged reason and a redirect
        to the login page, which is the visible failure path the caller can
        act on. The reason never reaches the reader — a refusal that names the
        failed check teaches an attacker which check to defeat next."""
        if cfg.dev_no_auth:
            # PROTOTYPE: faked — the marked dev seam, posture unchanged from
            # M2 (loopback-only at boot AND per request).
            if cfg.dev_user:
                return cfg.dev_user
            if len(cfg.user_streams) == 1:
                return next(iter(cfg.user_streams))
            return None
        token = request.cookies.get(SESSION_COOKIE)
        if not token:
            return None
        try:
            return verifier.user_id(token)
        except SessionInvalid as exc:
            # INFO, not warning, and the difference from the exchange below is
            # deliberate: this fires every time the vendor's five-minute proof
            # lapses, which is the ordinary rhythm of a reader who left the app
            # open — routine, not diagnostic. The VISIBLE failure path is the
            # redirect the caller gets, not this line.
            app.logger.info("session refused: %s", exc)
            return None

    def reader() -> Tuple[Optional[str], Optional[str]]:
        """(user, stream). A user with no stream mapped is not a reader —
        an account exists to read one paper, and an unmapped account is an
        operator mistake, not a picker prompt."""
        user = current_user()
        if not user:
            return None, None
        return user, cfg.user_streams.get(user)

    def _no_store(resp: Response) -> Response:
        resp.headers["Cache-Control"] = "no-store"
        return resp

    def _to_login() -> Response:
        """Send an unauthenticated visitor to the door, remembering WHICH page
        they wanted.

        The return path is what makes a five-minute JWT invisible: the login
        page silently refreshes the session and puts the reader back where they
        were. `/` carries no parameter at all, because `/` is where the login
        page goes by default — a bare redirect is the common case and stays
        bare."""
        target = _safe_next(request.full_path if request.query_string
                            else request.path)
        return redirect("/login" if target == "/" else
                        "/login?next=" + quote(target, safe="/?=&-_.~"))

    @app.after_request
    def _security_headers(resp: Response) -> Response:
        """THE VENDOR SCRIPT PIN, enforced by the browser rather than promised
        by a comment (adjudication Q3 / charter item 2: "their JS loaded ONLY
        on the login page, never on the reader; pin that").

        The login route's policy names the vendor's script origin. EVERY other
        route — the served edition first among them — sends a policy with no
        third-party origin in it at all, so a vendor script tag that ever leaks
        into an edition is refused by the browser, not by our good intentions.
        """
        resp.headers.setdefault(
            "Content-Security-Policy",
            CSP_LOGIN if request.path == "/login" else CSP_READER)
        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
        resp.headers.setdefault("Referrer-Policy", "same-origin")
        return resp

    # -- the bypass's request-time backstop --------------------------------

    if cfg.dev_no_auth:
        @app.before_request
        def _bypass_is_loopback_only():
            """THE BYPASS SERVES NOBODY OFF THIS MACHINE, whatever the boot
            check managed to see.

            `dev_bypass_guard` refuses the binds it can see, and enumerating
            channels loses that race by construction: `GUNICORN_CMD_ARGS` was
            one (found in review), a `-c gunicorn.conf.py` holding `bind=` is
            another, and the next one is not written down yet. So the refusal
            also stands at the moment that always exists — the request itself.
            A caller who is not on this machine is answered 403 and served
            nothing: no page, no edition, no push.

            NAMED LIMITATION, not solved here: a same-host reverse proxy
            deliberately forwarding into a bypass instance presents loopback
            remote_addrs and would pass this guard. That is a two-step operator
            act rather than the tired evening this whole arrangement is written
            about, and M3 replaces the stub with real session verification
            entirely.

            A MISSING `remote_addr` IS TREATED AS LOOPBACK: that is the
            in-process test client and any WSGI harness that sets no peer —
            never a socket from somewhere else."""
            addr = request.remote_addr
            if addr and not _is_loopback(addr):
                return _err(403,
                            f"refused: {DEV_NO_AUTH_VAR} is a loopback-only "
                            f"development seam and this request arrived from "
                            f"{addr}. Unset it and configure real "
                            "authentication (hosted/DEPLOY.md).")
            return None

    # -- the reading surface ----------------------------------------------

    def _serve_edition(stream: str, date: str, user: str, kind: str) -> Response:
        bundle = store.read_edition(stream, date)
        if not bundle or not isinstance(bundle.get("html"), str):
            abort(404)
        pushed = store.pushed_at(stream, date) or bundle.get("generated_at") or ""
        html = augment_edition(bundle["html"], {
            "date": date,
            "pushed_at": pushed,
            "day": hoststore.weekday_name(date),
            "bundle_version": bundle.get("bundle_version"),
        })
        # ONE ROW PER AUTHENTICATED EDITION GET (Q6). A service-worker cache
        # serve never reaches this line — that hole is measured, not assumed.
        store.record_read(stream, date, kind, user)
        resp = Response(html, mimetype="text/html")
        resp.headers["X-NewsLens-Edition-Date"] = date
        resp.headers["X-NewsLens-Pushed-At"] = pushed
        # The service worker owns edition caching; the HTTP cache must not
        # serve a stale morning behind its back.
        resp.headers["Cache-Control"] = "no-cache"
        return resp

    def _shell(template: str, **kw) -> Response:
        return Response(render_template(template, **kw), mimetype="text/html")

    @app.context_processor
    def _palette():
        """The status-bar colours, READ from the generated tokens.css.

        Same law as everywhere else in this build: a `meta theme-color`
        attribute cannot hold a var(), so the hex is read out of the generated
        artifact rather than typed beside it. If tokens.css is missing (a
        broken image), the metas are simply omitted — the alternative is a
        hardcoded fallback, which is the second copy this whole arrangement
        exists to prevent."""
        light, dark = _theme_colors(Path(app.static_folder) / "tokens.css")
        return {"theme_light": light, "theme_dark": dark}

    @app.route("/")
    def home():
        user, stream = reader()
        if not user:
            return _to_login()
        if not stream:
            return _shell("nostream.html"), 403
        latest = store.latest(stream)
        today = cfg.today()
        if latest == today:
            return _serve_edition(stream, latest, user, "today")
        # STATE 3 / STATE 5 as drawn: no edition for today yet. Reading the
        # last one is the primary act; the generate secondary renders the §8
        # unreachable grammar (his as-drawn flag 3 — remote generate is not
        # wired in Stage A, so a control that pretended to work would be the
        # dead button the law forbids).
        return _shell("empty.html", latest=latest, today=today,
                      latest_day=(hoststore.weekday_name(latest) if latest else ""),
                      archive_count=len(store.dates(stream)),
                      current="today", **_dateline_ctx(today))

    @app.route("/editions/<date>")
    def edition(date):
        if not valid_date(date):
            abort(404)
        user, stream = reader()
        if not user:
            return _to_login()
        if not stream:
            return _shell("nostream.html"), 403
        kind = "today" if date == store.latest(stream) else "archive"
        return _serve_edition(stream, date, user, kind)

    @app.route("/archive")
    def archive():
        user, stream = reader()
        if not user:
            return _to_login()
        if not stream:
            return _shell("nostream.html"), 403
        dates = list(reversed(store.dates(stream)))
        return _shell("archive.html", current="archive", entries=[
            {"date": d, "label": _archive_label(d)} for d in dates])

    @app.route("/login")
    def login():
        """The masthead page (addendum §9, mockup state 1) — as drawn, now
        ARMED. No self-serve signup, no password-reset theater; recovery runs
        through the paper's operator, said once.

        THE PAGE IS NEVER AUTH-GATED and never no-store: it is the one door,
        and a reader whose session has just expired must be able to reach it
        with no session at all. Everything the ceremony needs is public — a
        publishable token and a script URL; there is nothing here to protect.
        """
        session_cfg = cfg.session
        return _shell(
            "login.html",
            stytch_public_token=session_cfg.public_token,
            stytch_sdk_url=session_cfg.sdk_url,
            # An honest flag for the page: with no keys the ceremony cannot
            # run, and the quiet line says so on the tap rather than the
            # buttons pretending. The copy stays as drawn either way — this
            # is a machine state, not a product state.
            auth_ready=bool(session_cfg.configured),
            login_next=_safe_next(request.args.get("next") or "/"))

    @app.route("/api/session", methods=["POST"])
    def open_session():
        """Exchange a verified vendor JWT for this service's own HttpOnly
        cookie. THE ONLY WRITE PATH ON THE READING SIDE, and it writes exactly
        one cookie — no row, no file, no state (read-pure, Q7, holds).

        WHY AN EXCHANGE AT ALL, rather than reading the vendor SDK's cookie
        directly: the SDK's cookies are readable by any script on the page, by
        design. Ours is not readable by any script at all. The exchange is what
        buys HttpOnly.

        CSRF: a same-origin custom header is required (a cross-site form or
        image cannot set one, and this service answers no CORS preflight), so a
        hostile page cannot plant its own session in this reader's browser.
        """
        if request.headers.get(SESSION_REQUEST_HEADER) != "1":
            return _err(400, "this endpoint takes a same-origin request from "
                             "the sign-in page")
        body = request.get_json(silent=True)
        token = (body or {}).get("session_jwt")
        if not isinstance(token, str) or not token:
            return _err(400, "no session_jwt in the body")
        try:
            claims = verifier.verify(token)
        except SessionInvalid as exc:
            # THE REASON GOES TO THE OPERATOR AND NEVER TO THE CALLER: the
            # reader gets one quiet line, and an attacker gets no oracle
            # telling them which check to defeat next.
            #
            # WARNING-GRADE, and the grade was measured rather than assumed:
            # at `info` this line did not appear at all under `app.run`
            # (Flask's logger sits at WARNING there), which would have made
            # DEPLOY.md's "read the issuer error out of the logs" repair path
            # a false promise. A refused EXCHANGE is rare and diagnostic —
            # somebody presented a token this host would not take, which is
            # either a misconfigured claim or an attempt.
            app.logger.warning("session refused at exchange: %s", exc)
            return _err(401, "that sign-in could not be verified")
        user = claims["sub"]
        stream = cfg.user_streams.get(user)
        resp = _no_store(jsonify({
            "ok": True,
            # Named honestly so the login page can send an unmapped account to
            # the operator page instead of a blank paper.
            "stream": stream,
            "next": _safe_next(request.args.get("next")
                               or (body or {}).get("next") or "/"),
        }))
        resp.set_cookie(
            SESSION_COOKIE, token,
            max_age=SESSION_COOKIE_MAX_AGE,
            httponly=True, secure=_cookie_secure(), samesite="Lax", path="/")
        return resp

    # -- the push endpoint -------------------------------------------------

    @app.route("/api/streams/<stream>/editions/<date>", methods=["PUT"])
    def push_edition(stream, date):
        token = _bearer(request.headers.get("Authorization", ""))
        owner = _stream_for_token(cfg, token)
        if not owner:
            return _err(401, "token not recognised")
        if not valid_stream(stream) or not valid_date(date):
            return _err(400, "stream or date is not a legal name")
        if owner != stream:
            # The stream comes from the TOKEN, never from the URL (Rook item
            # 1). A mismatch is refused rather than silently redirected, so a
            # misconfigured Mac is loud on its first push instead of writing
            # into somebody else's paper.
            return _err(403, f"this token publishes {owner!r}, not {stream!r}")

        raw = request.get_data(cache=False)
        if len(raw) > cfg.max_bytes:
            return _err(413, f"edition is {len(raw)} bytes; the limit is "
                             f"{cfg.max_bytes}")
        try:
            bundle = json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return _err(400, "body is not the JSON of an edition bundle")
        problem = _bundle_problem(bundle, date)
        if problem:
            return _err(400, problem)

        sha = bundle["content_sha256"]
        existing = store.edition_sha(stream, date)
        if existing == sha:
            # IDEMPOTENT (Q4): the same document arriving twice is not an
            # event. Nothing is written to the SHELF; a receipt ROW is minted
            # with outcome="unchanged", because a DR re-push should still be
            # visible to the operator months later; and the offline stamp does
            # not move, because `pushed_at()` counts only rows that are not
            # "unchanged" and that is where the stamp's clock comes from.
            store.record_push(stream, date, sha, len(raw), "unchanged")
            # AND THE POINTER IS REPAIRED. A push that landed the bundle and
            # died before `set_latest` — a crash, a redeploy, a killed worker —
            # leaves today's edition on the shelf with `latest` still naming
            # yesterday, so the front page renders "No edition has been
            # generated for today" about a paper sitting right there. DEPLOY.md
            # offers exactly one recovery verb for a lost pointer
            # (`newslens push --date`); without this line that retry answers
            # 200 "unchanged" and repairs nothing. Safe by construction:
            # `set_latest` only ever moves FORWARD, so re-pushing an older date
            # still cannot make yesterday the paper of record.
            store.set_latest(stream, date)
            return jsonify({"status": "unchanged", "date": date,
                            "stream": stream, "content_sha256": sha,
                            "pushed_at": store.pushed_at(stream, date)})
        outcome = "replaced" if existing else "stored"
        store.write_edition(stream, date, raw)
        stamp = store.record_push(stream, date, sha, len(raw), outcome)
        store.set_latest(stream, date)
        return jsonify({"status": outcome, "date": date, "stream": stream,
                        "content_sha256": sha, "pushed_at": stamp,
                        "bytes": len(raw)})

    # -- plumbing ----------------------------------------------------------

    @app.route("/api/ping")
    def ping():
        """The client's offline probe. Deliberately auth-free and tiny: the
        page asking it is already rendered, and the only fact it returns is
        whether this machine is reachable at all."""
        return _no_store(jsonify({"ok": True}))

    @app.route("/healthz")
    def healthz():
        return _no_store(jsonify({"ok": True, "service": "newslens-hosted"}))

    @app.route("/sw.js")
    def service_worker():
        """Served from the ROOT so its scope is the whole paper — a worker
        served from /static/ could only ever control /static/."""
        resp = send_from_directory(app.static_folder, "sw.js",
                                   mimetype="text/javascript")
        resp.headers["Service-Worker-Allowed"] = "/"
        resp.headers["Cache-Control"] = "no-cache"
        return resp

    @app.errorhandler(404)
    def not_found(_exc):
        if request.path.startswith("/api/"):
            return _err(404, "no such endpoint")
        return _shell("missing.html"), 404

    @app.errorhandler(413)
    def too_large(_exc):
        return _err(413, f"body exceeds the {cfg.max_bytes}-byte limit")

    return app


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
    return None


def _err(status: int, detail: str):
    """An honest refusal: a status and a sentence, never a stack trace and
    never a page. The Mac's push client prints this sentence verbatim."""
    return jsonify({"error": detail}), status


app = create_app()


def main(argv=None) -> int:
    import argparse
    parser = argparse.ArgumentParser(description="NewsLens hosted reader")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=5473)
    args = parser.parse_args(argv)
    # Second call, now that the bind is known for certain: `python -m
    # hosted.app --host 0.0.0.0` with the bypass on dies here.
    dev_bypass_guard(host=args.host)
    create_app(host=args.host).run(host=args.host, port=args.port,
                                   debug=False, threaded=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
