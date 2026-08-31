"""NL-163 Stage-A M3 — THE LOCK: what this door refuses, and what it never gates.

Binding sources: the engineering adjudication 2026-08-29 §Q3 (host-side
verification, vendor-agnostic seam, the F1/F2/F3 faked-seam list); the
principal's ruling 2026-08-31 ("Lets do c" — Stytch, and the HOST HOLDS NO
VENDOR SECRET); DIRECTION-phone-addendum §8 (the one scoped danger-ink
extension) and §9 (login-as-masthead, ≥90-day rolling, credentials once per
device, no self-serve signup, THE CACHE IS NEVER AUTH-GATED).

THE F1 SEAM, and why it is honest currency rather than a shortcut. This module
MINTS ITS OWN RS256 KEYPAIR and serves its own JWKS document from memory. That
is not a mock of the verification: `hosted/sessionauth.py` runs unmodified —
real PyJWT, real RSA signature arithmetic, real issuer/audience/expiry checks,
real kid lookup. What the fixture replaces is the vendor's *identity*, which is
the only part a $0 build cannot have. Every refusal below is therefore a
measured refusal by the shipped code, and the one thing it cannot prove is that
the vendor's real tokens carry the claim shapes we configured — which is
SETUP.md step 1, his kill-check, and is named as unproven in the M3 report.

  * F1 — self-minted keypair + JWKS fixture (this file). NO SOCKET IS OPENED:
    the JWKS "fetch" is a function handed to the app, and one node asserts
    zero network attempts mechanically with the suite's own recorder.
  * F2 — `NEWSLENS_DEV_NO_AUTH=1`, unchanged in posture from M2 and re-pinned
    here BESIDE the real lock, because the milestone that arms real auth is
    exactly the milestone where a bypass could quietly widen.
  * F3 — the vendor's JS never loads under test, and cannot: no test renders
    it, and the CSP census below proves no route but /login would even be
    allowed to.

WHAT IS DELIBERATELY NOT HERE: any call to a vendor. The principal holds no
keys yet; a live call would be a custody breach and would prove nothing this
fixture does not.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
import shutil
import sqlite3
import subprocess
import tempfile
import threading
from datetime import datetime, timezone
from pathlib import Path

import pytest

from conftest import PROTOTYPE_ROOT

pytest.importorskip("flask", reason="hosted/ needs Flask (pip install -e '.[dev]')")
pyjwt = pytest.importorskip("jwt", reason="the lock needs PyJWT (pip install -e '.[dev]')")
pytest.importorskip("cryptography", reason="PyJWT's RS256 backend")

from cryptography.hazmat.primitives.asymmetric import rsa            # noqa: E402
from cryptography.hazmat.primitives import serialization             # noqa: E402

from hosted import app as hosted_app                                 # noqa: E402
from hosted import sessionauth                                       # noqa: E402
from hosted.hoststore import HostStore                               # noqa: E402

PROJECT = "project-test-nl163-m3"
ISSUER = f"stytch.com/{PROJECT}"
KID = "jwk-test-primary"
OTHER_KID = "jwk-test-rotated"
USER = "user-test-the-reader"
STREAM = "main"
TODAY = "2026-08-31"

PUSH_TOKEN = "push-token-for-the-lock-tests"
PUSH_DIGEST = hashlib.sha256(PUSH_TOKEN.encode("utf-8")).hexdigest()

HTML = ("<!DOCTYPE html><html lang=\"en\"><head><meta charset=\"UTF-8\">"
        "<title>NewsLens</title></head><body>"
        "<header class=\"page masthead\"><p class=\"wordmark\">NewsLens</p>"
        "<p class=\"offline-line\" id=\"offline-stamp\" hidden></p>"
        "<nav class=\"section-line\"><div class=\"page\">"
        "<span class=\"section-current\">Today</span></div></nav></header>"
        "<main id=\"main\">the edition</main></body></html>")


# ---------------------------------------------------------------------------
# F1 — the minted identity: one keypair, one JWKS, one token factory
# ---------------------------------------------------------------------------

def now_seconds() -> int:
    """THE CLOCK THE VERIFIER READS, and deliberately not `time.time()`.

    The suite's third leg runs at a shifted process clock
    (tools/pytest_clockshift), which moves `datetime`'s wall-clock entry points
    and leaves `time.time` alone — that distinction is what makes the plugin a
    detector rather than a noise machine. PyJWT checks `exp` against
    `datetime.now(timezone.utc)`, so a fixture that minted against `time.time`
    would hand the verifier a token a year stale and go red for a reason that
    is entirely the fixture's.

    MEASURED, not theorised: the first shifted-clock leg of this milestone
    turned twelve of these nodes red exactly that way, with the product code
    innocent. This function is the fix, and its docstring is the receipt."""
    return int(datetime.now(timezone.utc).timestamp())


def _b64u(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _int_b64u(value: int) -> str:
    return _b64u(value.to_bytes((value.bit_length() + 7) // 8, "big"))


class Signer:
    """One RSA key with the JWK the verifier will look it up by."""

    def __init__(self, kid: str):
        self.kid = kid
        self.key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        self.pem = self.key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption()).decode("ascii")
        numbers = self.key.public_key().public_numbers()
        self.jwk = {"kty": "RSA", "use": "sig", "alg": "RS256", "kid": kid,
                    "n": _int_b64u(numbers.n), "e": _int_b64u(numbers.e)}
        self.public_pem = self.key.public_key().public_bytes(
            serialization.Encoding.PEM,
            serialization.PublicFormat.SubjectPublicKeyInfo).decode("ascii")

    def mint(self, **over) -> str:
        """A token this project would accept, unless an override spoils it."""
        now = now_seconds()
        claims = {"sub": USER, "iss": ISSUER, "aud": PROJECT,
                  "iat": now, "nbf": now, "exp": now + 300,
                  "session_id": "session-test-1"}
        kid = over.pop("_kid", self.kid)
        for key, value in over.items():
            if value is None:
                claims.pop(key, None)
            else:
                claims[key] = value
        return pyjwt.encode(claims, self.pem, algorithm="RS256",
                            headers={"kid": kid})


PRIMARY = Signer(KID)
FOREIGN = Signer(KID)            # a DIFFERENT key claiming the SAME kid
ROTATED = Signer(OTHER_KID)

JWKS = {"keys": [PRIMARY.jwk]}
JWKS_AFTER_ROTATION = {"keys": [PRIMARY.jwk, ROTATED.jwk]}


class Fetcher:
    """The injected stand-in for one HTTPS GET. Counts calls, because two of
    the cache's three laws are about how OFTEN it fetches."""

    def __init__(self, document=None):
        self.document = JWKS if document is None else document
        self.calls = []
        self.fail = False

    def __call__(self, url):
        self.calls.append(url)
        if self.fail:
            raise OSError("the vendor is unreachable")
        return self.document


def unsigned(claims, alg="none", kid=KID) -> str:
    """A token with a header we choose and no real signature — the alg:none
    forgery, built by hand because no honest library will mint one."""
    header = _b64u(json.dumps({"alg": alg, "typ": "JWT", "kid": kid}).encode())
    body = _b64u(json.dumps(claims).encode())
    return f"{header}.{body}."


def hs256_confusion(claims, secret: str, kid=KID) -> str:
    """THE ALGORITHM-CONFUSION FORGERY, built by hand for the same reason.

    The attack: the public key is public, so an attacker signs an HS256 token
    using the RSA PUBLIC KEY BYTES as the HMAC secret. A verifier that reads
    `alg` out of the token and then "uses the key for this kid" will compute
    the same HMAC and let the forgery in. PyJWT itself refuses to mint this
    (it detects the PEM), which is why it is assembled here."""
    header = _b64u(json.dumps({"alg": "HS256", "typ": "JWT", "kid": kid}).encode())
    body = _b64u(json.dumps(claims).encode())
    signing_input = f"{header}.{body}".encode("ascii")
    mac = hmac.new(secret.encode("utf-8"), signing_input, hashlib.sha256).digest()
    return f"{header}.{body}.{_b64u(mac)}"


def live_claims(**over):
    now = now_seconds()
    claims = {"sub": USER, "iss": ISSUER, "aud": PROJECT,
              "iat": now, "nbf": now, "exp": now + 300}
    claims.update(over)
    return claims


# ---------------------------------------------------------------------------
# Fixtures — a configured host with no vendor and no socket
# ---------------------------------------------------------------------------

def auth_env(tmp_path, **over):
    env = {"NEWSLENS_HOSTED_DATA": str(tmp_path / "hostdata"),
           "NEWSLENS_PUSH_TOKENS": json.dumps({STREAM: PUSH_DIGEST}),
           "NEWSLENS_USER_STREAMS": json.dumps({USER: STREAM}),
           "NEWSLENS_STYTCH_PROJECT_ID": PROJECT,
           "NEWSLENS_STYTCH_PUBLIC_TOKEN": "public-token-test-not-a-secret"}
    env.update(over)
    return {k: v for k, v in env.items() if v is not None}


@pytest.fixture(autouse=True)
def pin_the_papers_day(monkeypatch):
    """THE PAPER'S DAY IS PINNED for every node in this file (the M2 file's own
    pattern, `Config.today`).

    The fixture edition is dated by a literal, so on the suite's shifted-clock
    leg the host would correctly conclude that a 2026 edition is not today's
    and render the no-edition state instead — a red that is about the fixture's
    calendar and not about the lock. Measured on the first shifted leg. The
    TOKENS are not pinned and must not be: their expiry is real arithmetic
    against a real clock, and pinning it would delete the one node that proves
    an expired session is refused."""
    monkeypatch.setattr(hosted_app.Config, "today", lambda self: TODAY)


@pytest.fixture
def fetcher():
    return Fetcher()


@pytest.fixture
def app(tmp_path, fetcher):
    return hosted_app.create_app(auth_env(tmp_path), jwks_fetcher=fetcher)


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def verifier(fetcher):
    return sessionauth.SessionVerifier(
        sessionauth.SessionConfig(auth_env_bare()), fetcher=fetcher)


def auth_env_bare():
    return {"NEWSLENS_STYTCH_PROJECT_ID": PROJECT,
            "NEWSLENS_STYTCH_PUBLIC_TOKEN": "public-token-test-not-a-secret"}


def sign_in(client, token=None):
    """Do what the login page does: hand the host a vendor JWT."""
    return client.post("/api/session", json={"session_jwt": token or PRIMARY.mint()},
                       headers={hosted_app.SESSION_REQUEST_HEADER: "1"})


def put_edition(client, date=TODAY, html=HTML):
    body = {"bundle_version": 1, "edition_date": date,
            "generated_at": f"{date}T06:02:00.000Z", "variant": "A",
            "content_sha256": hashlib.sha256(html.encode("utf-8")).hexdigest(),
            "meta": {"story_count": 3}, "audio": None, "html": html}
    return client.put(f"/api/streams/{STREAM}/editions/{date}",
                      data=json.dumps(body).encode("utf-8"),
                      headers={"Authorization": f"Bearer {PUSH_TOKEN}",
                               "Content-Type": "application/json"})


# ===========================================================================
# THE ATTACK MATRIX — one node per refusal, each born red (see the M3 report)
# ===========================================================================

def test_a_token_this_project_signed_is_accepted(verifier):
    """The control. Without it every refusal below could be a verifier that
    refuses everything, which refuses nothing in particular."""
    claims = verifier.verify(PRIMARY.mint())
    assert claims["sub"] == USER
    assert claims["iss"] == ISSUER


def test_a_token_from_outside_this_projects_key_set_is_refused(verifier):
    """CARRIED-INVARIANT, and labelled so on measurement rather than on faith.

    The refusal here is carried by the SIGNATURE check, not by the `no public
    key for this kid` raise above it: mutation M-5 (make an unknown kid fall
    back to any key in the set) leaves this node GREEN, because the token was
    signed by a key this project does not hold either way. Recorded in the M3
    report as one of two deliberate greens. The kid path's real job is
    ROTATION, and that is pinned by the two cache nodes below."""
    with pytest.raises(sessionauth.SessionInvalid):
        verifier.verify(ROTATED.mint(_kid=OTHER_KID))


def test_a_foreign_key_claiming_a_kid_we_trust_is_refused(verifier):
    """THE ONE THAT MATTERS MOST. `kid` is an unauthenticated hint chosen by
    whoever wrote the token: a verifier that trusts the hint instead of the
    signature accepts anything. Here a different RSA key signs a token whose
    header names OUR kid — the lookup succeeds, the signature does not."""
    with pytest.raises(sessionauth.SessionInvalid):
        verifier.verify(FOREIGN.mint())


def test_alg_none_is_refused(verifier):
    with pytest.raises(sessionauth.SessionInvalid):
        verifier.verify(unsigned(live_claims()))


def test_hs256_confusion_with_the_public_key_as_the_secret_is_refused(verifier):
    """Both spellings of the same forgery: the PEM as the HMAC secret, and the
    raw JWK modulus as the HMAC secret. The second is the one that matters —
    measured 2026-08-31, PyJWT itself refuses the PEM spelling outright
    ("should not be used as an HMAC secret") but ACCEPTS the modulus spelling
    when a verifier takes its algorithm from the token's own header. Mutation
    M-1 builds exactly that naive verifier and this node goes red."""
    for secret in (PRIMARY.public_pem, PRIMARY.jwk["n"]):
        with pytest.raises(sessionauth.SessionInvalid):
            verifier.verify(hs256_confusion(live_claims(), secret))


def test_a_tampered_payload_is_refused(verifier):
    """One byte of the subject changed, signature untouched: the reader
    becomes somebody else, and the arithmetic says no."""
    header, body, sig = PRIMARY.mint().split(".")
    claims = json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))
    claims["sub"] = "user-test-somebody-else"
    forged = f"{header}.{_b64u(json.dumps(claims).encode())}.{sig}"
    with pytest.raises(sessionauth.SessionInvalid):
        verifier.verify(forged)


def test_an_expired_token_is_refused_and_a_slightly_skewed_one_is_not(verifier):
    """The leeway is for CLOCK SKEW, not for grace. A token thirty seconds
    stale on a box whose NTP drifted is the same token; one an hour stale is a
    session somebody wants back."""
    now = now_seconds()
    with pytest.raises(sessionauth.SessionInvalid):
        verifier.verify(PRIMARY.mint(exp=now - 3600, iat=now - 7200, nbf=now - 7200))
    skewed = PRIMARY.mint(exp=now - 5, iat=now - 305, nbf=now - 305)
    assert verifier.verify(skewed)["sub"] == USER
    assert sessionauth.DEFAULT_LEEWAY_SECONDS <= 60, (
        "a generous leeway is just a longer window in which a revoked session "
        "still opens the paper")


def test_another_projects_token_is_refused(verifier):
    with pytest.raises(sessionauth.SessionInvalid):
        verifier.verify(PRIMARY.mint(iss="stytch.com/project-live-somebody-else"))


def test_a_token_for_another_audience_is_refused(verifier):
    with pytest.raises(sessionauth.SessionInvalid):
        verifier.verify(PRIMARY.mint(aud="project-live-somebody-else"))


def test_a_token_with_no_subject_is_refused(verifier):
    with pytest.raises(sessionauth.SessionInvalid):
        verifier.verify(PRIMARY.mint(sub=None))
    with pytest.raises(sessionauth.SessionInvalid):
        verifier.verify(PRIMARY.mint(sub="   "))


def test_a_token_with_no_expiry_is_refused(verifier):
    """A JWT with no `exp` is a permanent credential. The vendor never mints
    one; a forger would like to."""
    with pytest.raises(sessionauth.SessionInvalid):
        verifier.verify(PRIMARY.mint(exp=None))


def test_rubbish_is_refused_without_touching_the_key_cache(verifier, fetcher):
    """Cheap refusals come FIRST: a hostile string must not be usable to make
    this host fetch anything."""
    for junk in ("", "not-a-jwt", "a.b", "a.b.c.d", "..", None, 17):
        with pytest.raises(sessionauth.SessionInvalid):
            verifier.verify(junk)
    assert fetcher.calls == []


# ---------------------------------------------------------------------------
# The key cache — three laws
# ---------------------------------------------------------------------------

def test_the_key_set_is_fetched_once_and_then_reused(verifier, fetcher):
    for _ in range(5):
        verifier.verify(PRIMARY.mint())
    assert len(fetcher.calls) == 1
    assert fetcher.calls[0].endswith(f"/v1/sessions/jwks/{PROJECT}")


def test_an_unknown_kid_refetches_once_and_then_stops(verifier, fetcher):
    """A ROTATION AND A FORGERY LOOK IDENTICAL from here — both are an unknown
    kid — so the honest behaviour is to look once and then rate-limit. Without
    the floor, a stream of forged kids is a free fetch-per-request against the
    vendor and against our own latency."""
    verifier.verify(PRIMARY.mint())                       # fetch 1
    assert len(fetcher.calls) == 1
    fetcher.document = JWKS_AFTER_ROTATION
    verifier.verify(ROTATED.mint(_kid=OTHER_KID))         # fetch 2: the rotation
    assert len(fetcher.calls) == 2
    for _ in range(20):                                   # a forgery storm
        with pytest.raises(sessionauth.SessionInvalid):
            verifier.verify(PRIMARY.mint(_kid="kid-that-never-existed"))
    assert len(fetcher.calls) == 2, "a forged kid bought the attacker a fetch"


def test_an_unreachable_vendor_does_not_sign_anyone_out(verifier, fetcher):
    """The keys we already hold keep working. A vendor outage that logged
    every reader out would be the single worst failure this design can have —
    the paper is supposed to open on a train."""
    verifier.verify(PRIMARY.mint())
    fetcher.fail = True
    assert verifier.verify(PRIMARY.mint())["sub"] == USER


def test_an_operator_pinned_key_set_opens_no_socket_at_all(tmp_path, no_network):
    """The offline pin (DEPLOY.md): the operator's own machine fetched the
    keys once and pasted them in, so the reading path has no third party in
    it. Proven MECHANICALLY, not by reading the code and nodding — the suite's
    own socket recorder must stay empty."""
    env = auth_env(tmp_path, NEWSLENS_STYTCH_JWKS=json.dumps(JWKS))
    app = hosted_app.create_app(env)          # NO fetcher injected: the real one
    with app.test_client() as client:
        assert sign_in(client).status_code == 200
    assert no_network == [], f"the pinned path touched the network: {no_network}"


def test_a_malformed_pinned_key_set_refuses_everyone_loudly(tmp_path):
    """Fail CLOSED and say why in the log: a paste that lost a brace must not
    silently fall back to trusting nobody-knows-what."""
    env = auth_env(tmp_path, NEWSLENS_STYTCH_JWKS="{not json")
    verifier = sessionauth.SessionVerifier(sessionauth.SessionConfig(env))
    assert "NEWSLENS_STYTCH_JWKS" in (verifier.available() or "")
    with pytest.raises(sessionauth.SessionInvalid):
        verifier.verify(PRIMARY.mint())


def test_an_unconfigured_host_verifies_nobody(tmp_path):
    """No keys, no readers. The failure is named, not silent, and it is the
    state a box is in between `fly deploy` and `fly secrets set`."""
    verifier = sessionauth.SessionVerifier(sessionauth.SessionConfig({}))
    problem = verifier.available()
    assert problem and sessionauth.PROJECT_ID_VAR in problem
    with pytest.raises(sessionauth.SessionInvalid):
        verifier.verify(PRIMARY.mint())


def test_the_algorithm_is_pinned_in_the_source_not_taken_from_the_token():
    """A pin a comment cannot satisfy: the decode call names ONE algorithm."""
    src = (PROTOTYPE_ROOT / "hosted/sessionauth.py").read_text("utf-8")
    assert 'ALGORITHM = "RS256"' in src
    assert "algorithms=[ALGORITHM]" in src
    assert not re.search(r"algorithms\s*=\s*\[?\s*header", src)


# ===========================================================================
# THE DOOR — cookie, exchange, and what an unverified caller gets
# ===========================================================================

def test_signing_in_sets_one_httponly_cookie_that_outlives_the_jwt(client):
    """§9's ≥90-day rolling law, and the mechanism behind it.

    THE COOKIE'S LIFETIME IS NOT THE JWT'S. The vendor's JWT expires in five
    minutes by their design; the cookie lives 366 days so the DEVICE stays
    enrolled and the login page can refresh silently. A cookie that expired
    with the token would be a five-minute login."""
    resp = sign_in(client)
    assert resp.status_code == 200 and resp.get_json()["stream"] == STREAM
    cookie = resp.headers["Set-Cookie"]
    assert cookie.startswith(f"{hosted_app.SESSION_COOKIE}=")
    assert "HttpOnly" in cookie
    assert "SameSite=Lax" in cookie
    ninety_days = 90 * 24 * 60 * 60
    max_age = int(re.search(r"Max-Age=(\d+)", cookie).group(1))
    assert max_age >= ninety_days, "the ratified law is ≥90 days rolling"
    assert hosted_app.SESSION_COOKIE_MAX_AGE == 366 * 24 * 60 * 60


def test_the_cookie_is_secure_off_the_loopback_and_only_off_the_loopback(client):
    """Fail-closed in the direction that matters: a cookie is never handed to
    a REMOTE reader over plaintext. The one exemption is `http://127.0.0.1`,
    where a Secure cookie would simply never be stored and the seam would be
    untestable."""
    remote = client.post("/api/session", json={"session_jwt": PRIMARY.mint()},
                         headers={hosted_app.SESSION_REQUEST_HEADER: "1"},
                         environ_overrides={"REMOTE_ADDR": "203.0.113.9"})
    assert "Secure" in remote.headers["Set-Cookie"]

    tls = client.post("/api/session", json={"session_jwt": PRIMARY.mint()},
                      headers={hosted_app.SESSION_REQUEST_HEADER: "1"},
                      environ_overrides={"wsgi.url_scheme": "https"})
    assert "Secure" in tls.headers["Set-Cookie"]

    local = client.post("/api/session", json={"session_jwt": PRIMARY.mint()},
                        headers={hosted_app.SESSION_REQUEST_HEADER: "1"},
                        environ_overrides={"REMOTE_ADDR": "127.0.0.1"})
    assert "Secure" not in local.headers["Set-Cookie"]


def test_a_forged_token_never_becomes_a_cookie(client):
    """The exchange is not a formality: every attack in the matrix above is
    refused AT THE ROUTE too, and nothing is set."""
    now = now_seconds()
    for token in (FOREIGN.mint(),
                  unsigned(live_claims()),
                  hs256_confusion(live_claims(), PRIMARY.public_pem),
                  PRIMARY.mint(exp=now - 3600, iat=now - 7200, nbf=now - 7200),
                  PRIMARY.mint(iss="stytch.com/somebody-else"),
                  PRIMARY.mint(aud="somebody-else"),
                  "not-a-jwt"):
        resp = sign_in(client, token)
        assert resp.status_code == 401, token[:24]
        assert "Set-Cookie" not in resp.headers
        # No oracle: the refusal never names which check failed.
        detail = resp.get_json()["error"]
        for leak in ("kid", "iss", "aud", "exp", "signature", "algorithm"):
            assert leak not in detail.lower()


def test_the_exchange_refuses_a_cross_site_caller(client):
    """CSRF / session fixation. Without the same-origin header a hostile page
    could POST a form and log this reader into the ATTACKER's account —
    quietly handing the attacker everything the reader then does."""
    resp = client.post("/api/session", json={"session_jwt": PRIMARY.mint()})
    assert resp.status_code == 400
    assert "Set-Cookie" not in resp.headers


def test_a_verified_reader_reads_and_the_ledger_records_it(client, app):
    put_edition(client)
    sign_in(client)
    page = client.get("/")
    assert page.status_code == 200
    assert "the edition" in page.get_data(as_text=True)
    store = app.config["NEWSLENS_STORE"]
    assert store.counts()["read_events"] == 1


def test_an_expired_cookie_sends_the_reader_back_to_the_door(client):
    """The five-minute fact, end to end. The reader is not signed OUT — the
    device is still enrolled with the vendor — they are bounced to the page
    that can refresh silently, carrying where they were going."""
    put_edition(client)
    sign_in(client)
    assert client.get("/").status_code == 200
    now = now_seconds()
    client.set_cookie(hosted_app.SESSION_COOKIE,
                      PRIMARY.mint(exp=now - 600, iat=now - 900, nbf=now - 900))
    resp = client.get(f"/editions/{TODAY}")
    assert resp.status_code == 302
    assert resp.headers["Location"] == f"/login?next=/editions/{TODAY}"


def test_the_return_path_can_only_point_at_this_paper(client):
    """An open redirect turns a correct sign-in into a phishing hop. Refused,
    not repaired: a value we had to fix is a value we did not understand."""
    hostile = ["https://evil.example/", "//evil.example/", "/\\evil.example",
               "http:/evil.example", "/edition\r\nSet-Cookie: a=b", "javascript:1",
               "  /archive", "/x" * 400]
    for raw in hostile:
        assert hosted_app._safe_next(raw) == "/", raw
    for good in ("/", "/archive", f"/editions/{TODAY}", "/editions/x?y=1"):
        assert hosted_app._safe_next(good) == good
    resp = sign_in(client)
    assert resp.get_json()["next"] == "/"


def test_a_control_character_in_the_return_path_is_refused(client):
    """M4 gate FIX-4 — the M3 gate's dropped C0 ticket, closed.

    The docstring said "no control characters" and the code named five bytes,
    two of which (backslash, space) are not control characters at all. Measured
    at the M3 gate: `/foo\\x00bar` was KEPT. A NUL or a vertical tab in a return
    path is not a place on this paper by any reading — and a value that only
    LOOKS like one is exactly what this gate refuses rather than repairs."""
    for ch in ("\x00", "\x01", "\x0b", "\x0c", "\x1b", "\x1f", "\x7f"):
        assert hosted_app._safe_next(f"/foo{ch}bar") == "/", repr(ch)
        assert hosted_app._safe_next(f"/archive?q=a{ch}b") == "/", repr(ch)
    # …and the genuine paths are untouched by the widening.
    for good in ("/", "/archive", f"/editions/{TODAY}", "/editions/x?y=1",
                 "/editions/2026-08-29?from=archive&scroll=1"):
        assert hosted_app._safe_next(good) == good


def test_an_account_with_no_paper_still_signs_in_and_is_told_so(tmp_path, fetcher):
    """One account, one paper (Q5). An unmapped account is an operator
    mistake — the honest page, not a picker and not a blank."""
    env = auth_env(tmp_path, NEWSLENS_USER_STREAMS=json.dumps({"someone-else": STREAM}))
    client = hosted_app.create_app(env, jwks_fetcher=fetcher).test_client()
    resp = sign_in(client)
    assert resp.status_code == 200 and resp.get_json()["stream"] is None
    page = client.get("/")
    assert page.status_code == 403
    assert "operator" in page.get_data(as_text=True).lower()


# ===========================================================================
# §9 — THE CACHE IS NEVER AUTH-GATED (structural; do not regress)
# ===========================================================================

def test_the_door_and_the_cache_open_with_no_session_at_all(client):
    """The law, restated as a test at the milestone that could break it: a
    dead session may not take the last edition away from a reader who has it.
    The worker, the assets it caches and the login page are reachable with no
    cookie; only the EDITION routes ask."""
    for path in ("/login", "/sw.js", "/static/sw.js", "/static/shell.js",
                 "/static/login.js", "/static/tokens.css", "/api/ping", "/healthz"):
        assert client.get(path).status_code == 200, path
    for path in ("/", "/archive", f"/editions/{TODAY}"):
        assert client.get(path).status_code == 302, path


def test_the_service_worker_still_reads_a_login_redirect_as_a_refusal():
    """THE STRUCTURAL HALF of the offline law: the host answers an expired
    session with a 302 to /login, and the worker must treat that as 'the
    network cannot give you the paper' and serve the cache. Pinned here as
    well as in the M2 policy census because M3 is the milestone that makes
    that redirect a routine event rather than a stub's edge case."""
    sw = (PROTOTYPE_ROOT / "hosted/static/sw.js").read_text("utf-8")
    body = sw[sw.find("function isRefusal"):]
    body = body[:body.find("\nfunction ")]
    assert "/login" in body and "401" in body and "403" in body


# ===========================================================================
# THE VENDOR PIN — their script on the login page and nowhere else
# ===========================================================================

def test_only_the_login_page_may_reach_the_vendor(client):
    """CHARTER ITEM 2, ENFORCED BY THE BROWSER rather than by a comment. If a
    vendor script tag ever appears in a served edition, the reader's browser
    refuses it — because the edition's own policy names no third-party origin
    at all."""
    put_edition(client)
    sign_in(client)
    login_csp = client.get("/login").headers["Content-Security-Policy"]
    assert sessionauth.SDK_ORIGIN in login_csp

    for path in ("/", "/archive", f"/editions/{TODAY}", "/healthz", "/nothing"):
        csp = client.get(path).headers["Content-Security-Policy"]
        assert "stytch" not in csp, f"{path} may reach the vendor"
        assert "script-src 'self' 'unsafe-inline';" in csp
        assert "frame-ancestors 'none'" in csp


def test_the_login_policy_follows_the_configured_sdk_origin(tmp_path, fetcher):
    """M4 gate FIX-2 — THE RUNBOOK'S REPAIR PATH IS NOT SELF-DEFEATING.

    SETUP 1b/5 tell the operator's engineer to repair a wrong-shaped vendor
    artifact by retargeting one variable. At the pre-fix bytes the policy was
    baked from a module constant at import, so doing exactly that made the
    login page's own CSP refuse the script it had just been pointed at — and
    the page then reported "This paper's door has not been given its keys yet",
    a second misdiagnosis arriving at the worst possible moment.

    The derived origin REPLACES the pinned one: a retarget that left the old
    allowance standing would widen the one surface this milestone narrows."""
    retarget = "https://cdn.example.test/stytch/6.2.0/index.js"
    env = auth_env(tmp_path, NEWSLENS_STYTCH_SDK_URL=retarget)
    client = hosted_app.create_app(env, jwks_fetcher=fetcher).test_client()

    login_csp = client.get("/login").headers["Content-Security-Policy"]
    assert "https://cdn.example.test" in login_csp
    assert sessionauth.SDK_ORIGIN not in login_csp.split("connect-src")[0], (
        "the retarget must REPLACE the pinned script origin, not join it")
    # The page really does load from there, so policy and markup agree.
    assert retarget in client.get("/login").get_data(as_text=True)

    # …and the API origins do NOT move with the loader.
    assert f"connect-src 'self' {sessionauth.VENDOR_API_ORIGIN};" in login_csp
    assert f"frame-src 'self' {sessionauth.VENDOR_API_ORIGIN};" in login_csp

    # Every other route stays third-party-free — the M-13 pin, re-derived
    # against a RETARGETED config, which is where a naive fix would leak.
    put_edition(client)
    sign_in(client)
    for path in ("/", "/archive", f"/editions/{TODAY}", "/healthz",
                 "/api/ping", "/sw.js", "/nothing"):
        csp = client.get(path).headers["Content-Security-Policy"]
        assert "cdn.example.test" not in csp, path
        assert "stytch" not in csp, path
        assert "script-src 'self' 'unsafe-inline';" in csp, path


def test_an_sdk_url_that_is_not_an_https_origin_fails_closed_to_the_pin(
        tmp_path, fetcher, capsys):
    """FAIL CLOSED, NEVER FAIL OPEN. A typo in the environment may narrow the
    policy back to the shipped default; it may never widen it to whatever the
    typo happened to spell. The refusal is said once, on stderr, in the
    boot-refusal register — a silent downgrade would be the failure this whole
    fix exists to end."""
    for bad in ("http://cdn.evil.example/x.js", "not-a-url", "", "javascript:1",
                "ftp://cdn.example.test/x.js"):
        assert hosted_app.sdk_script_origin(bad) is None, bad
        assert hosted_app.login_csp_for(bad) == hosted_app.CSP_LOGIN, bad
    err = capsys.readouterr().err
    assert err.count("REFUSING THE CONFIGURED SDK ORIGIN") == 5
    assert sessionauth.SDK_URL_VAR in err

    env = auth_env(tmp_path, NEWSLENS_STYTCH_SDK_URL="http://cdn.evil.example/x.js")
    client = hosted_app.create_app(env, jwks_fetcher=fetcher).test_client()
    csp = client.get("/login").headers["Content-Security-Policy"]
    assert sessionauth.SDK_ORIGIN in csp and "evil.example" not in csp

    # The local walk is the one plaintext exemption, and only on the loopback.
    assert hosted_app.sdk_script_origin(
        "http://127.0.0.1:8899/stytch.js") == "http://127.0.0.1:8899"


def test_the_vendor_appears_in_exactly_one_template():
    """A census, not a spot check: the only template that may name the vendor
    script is the login page, and the served edition is not a template at all
    (it arrives frozen from the Mac)."""
    named = []
    for path in sorted((PROTOTYPE_ROOT / "hosted/templates").glob("*.html")):
        if "stytch" in path.read_text("utf-8").lower():
            named.append(path.name)
    assert named == ["login.html"], named
    shell = (PROTOTYPE_ROOT / "hosted/static/shell.js").read_text("utf-8")
    assert "stytch" not in shell.lower(), (
        "shell.js is injected into every served edition — no vendor, ever")
    assert "data-login-show" not in shell, (
        "the login ceremony moved to login.js; shell.js rides the edition")


def test_the_ceremony_asks_for_the_rolling_maximum_every_time():
    """§9's ≥90-day ROLLING half. The vendor extends a session on each
    successful authentication by the duration passed, so every ceremony and
    every silent refresh passes the documented maximum — 527,040 minutes,
    366 days. A call that forgot it would quietly shorten the session."""
    js = (PROTOTYPE_ROOT / "hosted/static/login.js").read_text("utf-8")
    calls = re.findall(r"client\.(\w+)\.(\w+)\(\{([^}]*)\}\)", js, re.S)
    assert len(calls) == 3, calls
    for product, method, args in calls:
        assert "session_duration_minutes: minutes" in args, (product, method)
    template = (PROTOTYPE_ROOT / "hosted/templates/login.html").read_text("utf-8")
    assert '"session_minutes": 527040' in template


# ===========================================================================
# THE VERSION FLOOR — M4 gate FIX-3 (R1's ruling (iii))
#
# The vendor's loader URL carries NO VERSION, so the artifact behind it can
# change shape any morning; the CSP origin pin sees where a script came from
# and never what shape it has. These three nodes EXECUTE the shipped login.js
# against a DOM stub and a client of a chosen shape — the only way to see what
# the page actually renders. No vendor, no network, no browser.
# ===========================================================================

VERSION_FACT = "This page loaded an unexpected version of its sign-in service."
VERSION_WHY = ("Signing in may not work or may not last; the paper’s operator "
               "has the fix in the runbook.")
NETWORK_LIE = "Sign-in needs the network"

_LOGIN_HARNESS = r"""
const fs = require('fs');
const src = fs.readFileSync(process.argv[2], 'utf8');
const spec = JSON.parse(process.argv[3]);
const log = [];

function mk(id, attrs) {
  return { id: id, hidden: true, textContent: '', value: '',
           attrs: attrs || {}, _click: [],
           addEventListener: function (k, fn) { if (k === 'click') { this._click.push(fn); } },
           getAttribute: function (k) { return (k in this.attrs) ? this.attrs[k] : null; },
           click: function () { this._click.forEach(function (fn) { fn(); }); } };
}

const els = {};
['login-error', 'login-unreach', 'login-unreach-why', 'login-passkey',
 'login-password', 'li-email', 'li-email2', 'li-pw'].forEach(function (id) {
   els[id] = mk(id);
 });
els['li-email2'].value = 'reader@example.test';
els['li-pw'].value = 'not-a-real-password';
const cfgNode = { textContent: JSON.stringify(spec.cfg) };
const arms = { passkey: mk('b1', { 'data-login-arm': 'passkey' }),
               password: mk('b2', { 'data-login-arm': 'password' }) };

global.document = {
  getElementById: function (id) {
    return id === 'newslens-login' ? cfgNode : (els[id] || null);
  },
  querySelectorAll: function (sel) {
    return sel === '[data-login-arm]' ? [arms.passkey, arms.password] : [];
  }
};
global.navigator = { onLine: true };
global.window = { location: { replace: function (u) { log.push('navigated:' + u); } } };
global.fetch = function (url, opts) {
  log.push('exchange:' + url);
  return Promise.resolve({ ok: true, json: function () {
    return Promise.resolve({ next: JSON.parse(opts.body).next }); } });
};

function build(shape) {
  const c = {};
  if (shape.webauthn) {
    c.webauthn = { authenticate: function () {
      log.push('called:webauthn.authenticate');
      return Promise.resolve({ session_jwt: 'jwt-from-passkey' }); } };
  }
  if (shape.passwords) {
    c.passwords = { authenticate: function () {
      log.push('called:passwords.authenticate');
      return Promise.resolve({ session_jwt: 'jwt-from-password' }); } };
  }
  c.session = {};
  if (shape.sessionAuthenticate) {
    c.session.authenticate = function () {
      log.push('called:session.authenticate');
      return Promise.resolve({ session_jwt: 'jwt-from-refresh' }); };
  }
  if (shape.getTokens) {
    c.session.getTokens = function () {
      log.push('called:session.getTokens');
      return { session_jwt: 'a-live-vendor-session' }; };
  }
  return c;
}
global.window.Stytch = function () { return build(spec.shape); };

eval(src);

if (spec.press) { arms[spec.press].click(); }
setTimeout(function () {
  console.log(JSON.stringify({
    error: els['login-error'].hidden ? null : els['login-error'].textContent,
    unreach: els['login-unreach'].hidden ? null : els['login-unreach'].textContent,
    why: els['login-unreach-why'].hidden ? null : els['login-unreach-why'].textContent,
    log: log
  }));
}, 80);
"""

_SHAPE_WHOLE = {"webauthn": True, "passwords": True,
                "sessionAuthenticate": True, "getTokens": True}


def _drive_login(shape, press=None):
    """Execute the SHIPPED login.js against a stub DOM and a client of `shape`.

    Not a source assertion: a comment cannot satisfy this, because what it
    returns is the text the page put in its own two registers."""
    node = shutil.which("node")
    if not node:
        pytest.skip("node not available — the version floor needs a JS runtime")
    spec = {"cfg": {"ready": True, "public_token": "public-token-test-not-a-secret",
                    "session_minutes": 527040, "next": "/archive"},
            "shape": shape, "press": press}
    with tempfile.TemporaryDirectory() as d:
        harness = Path(d) / "h.js"
        harness.write_text(_LOGIN_HARNESS, encoding="utf-8")
        proc = subprocess.run(
            [node, str(harness),
             str(PROTOTYPE_ROOT / "hosted/static/login.js"), json.dumps(spec)],
            capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout.strip().splitlines()[-1])


def test_a_wrong_shape_sign_in_service_is_named_instead_of_blamed_on_the_network():
    """BORN RED at the pre-fix bytes — THE NETWORK LIE, killed.

    A missing method and a dead network are the same JS error class, so the
    password arm's TypeError was caught, classified as unreachable, and
    answered "The press can't be reached from here. / Sign-in needs the
    network…" — loud, and false about the cause. SETUP step 6's console
    symptom was false for the same reason: the error never reaches the
    console, because attempt() catches it.

    AND THE FLOOR NEVER BLOCKS AN ARM: the same wrong-shaped client still
    signs a reader in through the arm that DOES exist. A false positive here
    can add a line and nothing else."""
    shape = dict(_SHAPE_WHOLE, passwords=False)

    hit = _drive_login(shape, press="password")
    assert hit["unreach"] == VERSION_FACT
    assert hit["why"] == VERSION_WHY
    assert NETWORK_LIE not in (hit["why"] or ""), "the network lie must be dead"
    assert "keys yet" not in (hit["why"] or ""), "nor the unconfigured lie"

    working = _drive_login(shape, press="passkey")
    assert working["unreach"] == VERSION_FACT, "the fact is owed either way"
    assert "called:webauthn.authenticate" in working["log"]
    assert "exchange:/api/session" in working["log"]
    assert "navigated:/archive" in working["log"], (
        "the floor may not block an arm whose methods are all present")


def test_a_right_shape_client_says_nothing_and_the_ceremony_proceeds():
    """THE CONTROL. Silence on first paint (§11) and silence through a whole
    successful ceremony — a floor that spoke on a healthy page would be the
    theater this product refuses, and would train its one reader to ignore it."""
    quiet = _drive_login(_SHAPE_WHOLE)
    assert (quiet["error"], quiet["unreach"], quiet["why"]) == (None, None, None)
    assert "called:session.getTokens" in quiet["log"], "the silent return ran"

    done = _drive_login(_SHAPE_WHOLE, press="passkey")
    assert (done["error"], done["unreach"], done["why"]) == (None, None, None)
    assert "called:webauthn.authenticate" in done["log"]
    assert "navigated:/archive" in done["log"]


def test_a_session_that_cannot_be_refreshed_is_named_at_the_next_ceremony():
    """BORN RED at the pre-fix bytes — THE SILENT HALF.

    `canRefresh()` swallows a missing `getTokens` and returns false, which is
    indistinguishable from an ordinary first morning. The reader signs in
    perfectly well and then, five minutes later, is asked again — for ever,
    with nothing anywhere saying why. §9's whole promise dies invisibly. First
    paint stays silent (that much was right); the ceremony it forces is where
    the fact is owed, and "may not LAST" is the half of the sentence that is
    about exactly this."""
    shape = dict(_SHAPE_WHOLE, getTokens=False)

    first_paint = _drive_login(shape)
    assert (first_paint["error"], first_paint["unreach"],
            first_paint["why"]) == (None, None, None)
    assert "called:session.authenticate" not in first_paint["log"], (
        "no silent return is possible without getTokens")

    forced = _drive_login(shape, press="passkey")
    assert forced["unreach"] == VERSION_FACT
    assert forced["why"] == VERSION_WHY
    assert "called:webauthn.authenticate" in forced["log"]
    assert "navigated:/archive" in forced["log"], "the ceremony still works"


def test_the_login_page_is_still_the_approved_masthead_and_says_nothing_on_arrival(client):
    """The drawn copy is byte-preserved, and BOTH failure lines are hidden on
    first paint — a page that greeted a reader with danger ink would be the
    theater §11 rules out."""
    page = client.get("/login").get_data(as_text=True)
    for ruled in ("Sign in to read the edition.",
                  "Accounts are created by the paper’s operator — there is no signup.",
                  "Continue with passkey", "Use a password instead",
                  "You’ll stay signed in on this phone."):
        assert ruled in page
    assert "Forgot password" not in page and "Sign up" not in page
    for node in ("login-error", "login-unreach", "login-unreach-why"):
        assert re.search(rf'id="{node}"[^>]*\shidden', page), node


def test_there_is_no_signup_surface_anywhere_in_the_service():
    """§9: accounts are created by the paper's operator. The service exposes
    ONE non-GET route besides the push endpoint, and it creates nothing —
    it exchanges a token the vendor already issued.

    SCOPE IS THE WHOLE PACKAGE, NOT ONE FILE (M4). At M3 this audit read
    `hosted/app.py` alone, which was then the whole deployable's Python. The
    M4 split moved 132 lines out into `hosted/webhelpers.py` — and an audit
    that keeps naming one file gets quietly narrower every time the package
    grows a module, so the secretless-host claim would end up true of the file
    we happen to check rather than of the thing we deploy. It globs now, and
    the manifest assert below fails loudly if a module ever stops being found.

    AND IT DESCENDS (M4 gate FIX-5). `glob("*.py")` stopped at the top of the
    package, so the first `hosted/<anything>/module.py` anyone adds would walk
    out of both the census and the secret audit — the same narrowing the M4
    split had just been widened to close, one directory down. `rglob` reaches
    it; the manifest assert is unchanged because it is a floor, not a list.
    """
    import ast
    modules = sorted((PROTOTYPE_ROOT / "hosted").rglob("*.py"))
    assert {p.name for p in modules} >= {"app.py", "webhelpers.py",
                                         "sessionauth.py", "hoststore.py"}, modules
    writes = {}
    for path in modules:
        tree = ast.parse(path.read_text("utf-8"))
        for node in ast.walk(tree):
            for dec in getattr(node, "decorator_list", []):
                if not (isinstance(dec, ast.Call)
                        and getattr(dec.func, "attr", "") == "route"):
                    continue
                methods = []
                for kw in dec.keywords:
                    if kw.arg == "methods":
                        methods = [e.value for e in kw.value.elts]
                if methods and methods != ["GET"]:
                    writes[dec.args[0].value] = methods
    assert writes == {"/api/streams/<stream>/editions/<date>": ["PUT"],
                      "/api/session": ["POST"]}, writes
    for path in modules:
        src = path.read_text("utf-8")
        for forbidden in ("STYTCH_SECRET", "/v1/users", "/v1/passwords",
                          "secret="):
            assert forbidden not in src, (
                f"{forbidden!r} in {path.name} — the host holds NO vendor "
                "secret and creates NO accounts (his ruling 2026-08-31)")


# ===========================================================================
# F2 — the development bypass, unchanged in posture beside the real lock
# ===========================================================================

def test_the_bypass_still_refuses_every_caller_that_is_not_this_machine(tmp_path):
    env = auth_env(tmp_path, NEWSLENS_DEV_NO_AUTH="1", NEWSLENS_DEV_USER=USER)
    client = hosted_app.create_app(env).test_client()
    put_edition(client)
    assert client.get("/").status_code == 200
    for addr in ("203.0.113.9", "10.0.0.4", "::ffff:8.8.8.8"):
        resp = client.get("/", environ_overrides={"REMOTE_ADDR": addr})
        assert resp.status_code == 403, addr
        assert "the edition" not in resp.get_data(as_text=True)


def test_the_real_lock_does_not_register_the_bypass_backstop(client):
    """With auth on, the M2 request guard is not installed at all — so a
    reverse proxy's forwarded requests are none of its business, exactly as
    its own comment promised."""
    resp = client.get("/login", environ_overrides={"REMOTE_ADDR": "203.0.113.9"})
    assert resp.status_code == 200


# ===========================================================================
# R-F — the ledger's first-connection lock race (M2 gate rider, fixed here)
# ===========================================================================

RF_WRITERS = 12
RF_ROUNDS = 120


def test_twelve_writers_meeting_a_brand_new_ledger_all_land_every_round(tmp_path):
    """R-F, the gate's own instrument shape — STRENGTHENED (M4, gate LOW-2).

    Twelve threads make FIRST CONTACT with a ledger that does not exist yet.
    Before the fix, `PRAGMA journal_mode=WAL` on every connection lost the lock
    race and raised `database is locked`; now journal mode is READ first and
    only written when it is not already WAL.

    WHY IT LOOPS, and why the loop is the whole strengthening. M3 shipped this
    as EIGHT threads run ONCE, and both QA and the gate measured the same
    embarrassing fact: it stayed GREEN under a full revert of the fix. A race
    pin that fires on one throw of the dice is not a regression detector, it is
    a smoke test wearing one's clothes.

    THE SHAPE WAS MEASURED, NOT GUESSED (M4, off-tree, against `_prepare`
    reverted to M2's unconditional pragma+CREATE):

        per-ROUND red rate under the revert   ~5-15% (thread count is NOT the
                                                     lever — 8, 12, 16 and 24
                                                     writers all measured the
                                                     same band; ROUNDS are)
        the M3 node (8 writers, 1 round)      5 of 20 runs RED — the control,
                                              and the whole reason LOW-2 was
                                              raised: the pin missed the
                                              regression it was written for in
                                              three runs out of four
        this NODE at 60 rounds                19 of 20 runs RED — REJECTED;
                                              one run in twenty still misses
        this NODE at 120 rounds (SHIPPED)     20 of 20 runs RED
        this NODE against the FIX             0 red in 500 cold rounds
        cost                                  ~3.4s

    Thread count is capped at twelve because thread count was measured NOT to
    be the lever: 8, 16 and 24 writers all sat in the same red band, so past
    twelve the machine is measuring its own scheduler rather than this
    ledger's first contact. Rounds are the lever, so rounds are what moved.
    """
    for attempt in range(RF_ROUNDS):
        root = tmp_path / f"cold-{attempt}"
        errors = []
        barrier = threading.Barrier(RF_WRITERS)

        def push(n):
            try:
                barrier.wait(timeout=20)
                HostStore(root).record_push(
                    STREAM, TODAY, "0" * 64, 10, f"stored-{n}")
            except Exception as exc:                 # noqa: BLE001 - recorded
                errors.append(f"{type(exc).__name__}: {exc}")

        threads = [threading.Thread(target=push, args=(n,))
                   for n in range(RF_WRITERS)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=60)
        assert errors == [], f"round {attempt}: {errors}"
        assert HostStore(root).counts()["push_receipts"] == RF_WRITERS, (
            f"round {attempt}: receipts lost at first contact")


def test_the_ledger_asks_for_the_write_lock_only_when_it_has_to(tmp_path,
                                                                monkeypatch):
    """The fix in one measurement: a warm ledger issues NO journal-mode write
    and NO CREATE TABLE. That is what makes the race unwinnable rather than
    merely unlikely.

    Measured with SQLite's own trace callback — every statement the engine
    actually executes, not every string this file thought it sent."""
    store = HostStore(tmp_path / "warm")
    store.record_push(STREAM, TODAY, "0" * 64, 10, "stored")

    statements = []
    real_connect = sqlite3.connect

    def traced(*args, **kwargs):
        con = real_connect(*args, **kwargs)
        con.set_trace_callback(lambda sql: statements.append(" ".join(sql.split())))
        return con

    monkeypatch.setattr(sqlite3, "connect", traced)
    HostStore(tmp_path / "warm").counts()

    assert statements, "the trace recorded nothing — the instrument is dead"
    assert not any("journal_mode=WAL" in s for s in statements), statements
    assert not any(s.upper().startswith("CREATE TABLE") for s in statements), statements
    assert any("journal_mode" in s for s in statements), "it stopped checking"


# ===========================================================================
# THE OPERATOR'S HAND — Mac-side account creation (§9: there is no signup)
# ===========================================================================

OPERATOR_ENV = {"STYTCH_PROJECT_ID": "project-test-abcdef", "STYTCH_SECRET": "sec"}


class Opener:
    """A stand-in for one HTTPS call. PROTOTYPE: faked — the vendor is never
    reached from this suite, and the principal holds no keys yet, so a live
    call would prove nothing and breach custody."""

    def __init__(self, payload=None, capture=None):
        self.payload = payload if payload is not None else {
            "user_id": "user-test-created", "status_code": 200}
        self.capture = capture if capture is not None else []

    def __call__(self, request, timeout=None):
        self.capture.append(request)
        opener = self

        class _Resp:
            def read(self):
                return json.dumps(opener.payload).encode("utf-8")

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False
        return _Resp()


def test_creating_an_account_is_a_dry_run_until_the_operator_says_otherwise():
    """ENGINEERING.md: anything that acts externally defaults to dry-run.
    Creating an account on a third party's system is an external act."""
    from newslens import phoneaccount
    capture = []
    result = phoneaccount.create("reader@example.com", STREAM,
                                 env=OPERATOR_ENV, opener=Opener(capture=capture))
    assert result["committed"] is False
    assert result["url"].endswith("/v1/passwords")
    assert result["environment"] == "test"
    assert capture == [], "a dry run reached the vendor"
    assert "password" not in result, "a dry run minted a credential"


def test_committing_creates_the_account_and_prints_the_host_mapping_row():
    from newslens import phoneaccount
    capture = []
    result = phoneaccount.create("reader@example.com", STREAM, commit=True,
                                 env=OPERATOR_ENV, opener=Opener(capture=capture))
    assert result["committed"] is True
    assert result["user_id"] == "user-test-created"
    assert json.loads(result["env_row"]) == {"user-test-created": STREAM}
    assert len(result["password"]) >= 20
    (request,) = capture
    assert request.full_url == "https://test.stytch.com/v1/passwords"
    sent = json.loads(request.data.decode("utf-8"))
    assert set(sent) == {"email", "password"}
    # The secret authenticates the call and appears nowhere else.
    assert request.get_header("Authorization").startswith("Basic ")
    assert "sec" not in json.dumps(sent)


def test_the_vendor_environment_is_read_from_the_project_id_never_guessed():
    from newslens import phoneaccount
    assert phoneaccount.environment_for("project-live-x") == "live"
    assert phoneaccount.environment_for("project-test-x") == "test"
    with pytest.raises(phoneaccount.AccountError):
        phoneaccount.environment_for("pk_live_something_from_another_vendor")


def test_the_operator_verb_refuses_a_bad_email_or_stream_before_any_call():
    from newslens import phoneaccount
    capture = []
    for email, stream in (("not-an-email", STREAM),
                          ("reader@example.com", "Main"),
                          ("reader@example.com", "main/../etc"),
                          ("reader@example.com", "")):
        with pytest.raises(phoneaccount.AccountError):
            phoneaccount.create(email, stream, commit=True, env=OPERATOR_ENV,
                                opener=Opener(capture=capture))
    assert capture == []


def test_a_vendor_refusal_never_echoes_the_secret_or_the_password(monkeypatch):
    """The failure path is where credentials leak. It carries the vendor's own
    sentence and the status code, and nothing that was in the request."""
    import urllib.error
    from newslens import phoneaccount

    def angry(request, timeout=None):
        raise urllib.error.HTTPError(
            request.full_url, 401, "Unauthorized", {},
            _FakeBody(json.dumps({"error_message": "wrong project secret"})))

    with pytest.raises(phoneaccount.AccountError) as caught:
        phoneaccount.create("reader@example.com", STREAM, commit=True,
                            env=OPERATOR_ENV, opener=angry,
                            password="the-password-that-must-not-appear")
    message = str(caught.value)
    assert "401" in message and "wrong project secret" in message
    assert "sec" not in message.replace("secret", "")
    assert "the-password-that-must-not-appear" not in message


class _FakeBody:
    """`HTTPError` holds this as its file object and CLOSES it on collection —
    a body without `close()` surfaces as a PytestUnraisableExceptionWarning
    from the garbage collector, in some other test's output."""

    def __init__(self, text):
        self._text = text.encode("utf-8")

    def read(self, *_a):
        return self._text

    def close(self):
        return None


def test_the_operator_verb_lives_only_on_the_mac():
    """A census, because this is the §9 law expressed as an absence: the
    deployable has no import of it and no route that could reach it."""
    for path in sorted((PROTOTYPE_ROOT / "hosted").rglob("*.py")):
        assert "phoneaccount" not in path.read_text("utf-8"), path


def test_a_ledger_whose_file_was_replaced_heals_itself(tmp_path):
    """The reason the schema check is per-connection and not a one-time flag on
    the object: a restored volume can hand the same process a different file."""
    root = tmp_path / "replaced"
    store = HostStore(root)
    store.record_push(STREAM, TODAY, "0" * 64, 10, "stored")
    (root / "ledger.db").unlink()
    store.record_push(STREAM, TODAY, "0" * 64, 10, "stored")
    assert store.counts()["push_receipts"] == 1
