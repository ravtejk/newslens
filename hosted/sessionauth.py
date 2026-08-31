"""THE LOCK — host-side session verification, and no vendor secret anywhere.

NL-163 Stage-A milestone 3, per the engineering adjudication 2026-08-29 §Q3
(written for Clerk, retargeted to Stytch by the principal's ruling 2026-08-31,
"Lets do c") and DIRECTION-phone-addendum §9 (login-as-masthead).

WHAT THIS FILE IS, in one sentence: it turns a signed string the browser hands
us into a user id, using arithmetic and a public key, and it refuses everything
else.

WHY LOCAL VERIFICATION AND NOT AN API CALL. The vendor signs a short-lived JWT
with a project-specific RSA key and publishes the matching PUBLIC key at a
JWKS URL. Verifying a signature against a public key is arithmetic: no secret,
no round trip, no vendor outage on the reading path. That is the whole
architecture the adjudication chose, and it is why THE HOST HOLDS NO VENDOR
SECRET — a secret would let this service mint and refresh sessions, which is
exactly the power a public reader-facing box should not have.

VENDOR-AGNOSTIC ON PURPOSE. Nothing below imports a vendor SDK or knows a
vendor's product names. It knows four configured facts — issuer, audience,
JWKS location, algorithm — and those are the same four facts for Clerk, Auth0,
WorkOS or anyone else with an OIDC-shaped session JWT. The recorded falsifier
(the Stytch kill-check, DECISIONS 2026-08-31) is survivable here by changing
env vars, which is the point.

WHAT IT REFUSES, each with a test that was born red (tests/test_nl163_auth.py):

  * a token signed with a key we do not have (`kid` not in the JWKS)
  * a token signed by a DIFFERENT key that claims a `kid` we do have
  * `alg: none` — the classic unsigned forgery
  * `alg: HS256` where the RSA PUBLIC key is used as the HMAC secret — the
    algorithm-confusion attack; refused twice over, at the header check and at
    the pinned algorithm list
  * an expired token (beyond a small clock-skew leeway)
  * a token issued by another project (`iss`) or for another audience (`aud`)
  * a token with a tampered payload
  * a token with no subject

THE FIVE-MINUTE FACT, stated here because every other file's design depends on
it: Stytch session JWTs have a fixed 5-minute lifetime, deliberately, "to
ensure there is a limit to how long a stale JWT could continue to be locally
validly despite the underlying session being revoked" (their words, docs read
2026-08-31). The LONG session — the ≥90-day rolling one the design law
requires — is the vendor-side session behind the JWT, and refreshing the JWT
from it is an authenticated call this service cannot make and must not be able
to make. So the refresh happens in the ONE place that holds vendor credentials
of its own: the browser, on the login page, with the PUBLIC token. See
hosted/static/login.js and the report's §"the five-minute collision".
"""

from __future__ import annotations

import json
import os
import threading
import time
import urllib.request
from typing import Callable, Dict, List, Optional

# PyJWT + cryptography are declared in hosted/requirements.txt (listed since
# M2 so the dependency story is one story). Import failure is a configuration
# failure, not a runtime surprise: the app refuses to authenticate anyone
# rather than falling back to something weaker.
try:                                            # pragma: no cover - import shape
    import jwt as pyjwt
    from jwt import PyJWKClient  # noqa: F401  (presence check only; see below)
    _JWT_IMPORT_ERROR = None
except Exception as exc:                        # pragma: no cover - import shape
    pyjwt = None
    _JWT_IMPORT_ERROR = exc


# The ONLY algorithm this service will ever accept. Pinned as a constant, not
# read from a token, not configurable: every JWT verification CVE of the last
# decade is some version of "the token told us how to check the token".
ALGORITHM = "RS256"

# Clock skew allowance. Small on purpose: a phone and a rented box are both
# NTP-synced, and a generous leeway is just a longer window in which a revoked
# session still opens the paper.
DEFAULT_LEEWAY_SECONDS = 30

# How long a fetched JWKS is trusted before a refresh. The vendor rotates
# roughly every 6 months and serves both keys for a month, so an hour is three
# orders of margin and still bounded.
DEFAULT_JWKS_TTL_SECONDS = 3600

# A `kid` we have never seen triggers ONE refetch, then nothing for this long.
# Without the floor, a stream of forged tokens carrying random `kid`s is a
# free denial-of-service against the vendor's JWKS endpoint and against our own
# request latency — one fetch per forgery. With it, a real rotation is picked
# up within a minute and a forgery storm costs one fetch a minute.
JWKS_REFETCH_COOLDOWN_SECONDS = 60

JWKS_TIMEOUT_SECONDS = 5.0

# Env var names, all in one place (SETUP.md and .env.example carry the prose).
PROJECT_ID_VAR = "NEWSLENS_STYTCH_PROJECT_ID"
PUBLIC_TOKEN_VAR = "NEWSLENS_STYTCH_PUBLIC_TOKEN"
JWKS_URL_VAR = "NEWSLENS_STYTCH_JWKS_URL"
JWKS_INLINE_VAR = "NEWSLENS_STYTCH_JWKS"
ISSUER_VAR = "NEWSLENS_STYTCH_ISSUER"
AUDIENCE_VAR = "NEWSLENS_STYTCH_AUDIENCE"
ENVIRONMENT_VAR = "NEWSLENS_STYTCH_ENV"          # "test" | "live"
SDK_URL_VAR = "NEWSLENS_STYTCH_SDK_URL"

# The vendor's two API hosts (docs, retrieved 2026-08-31). `test` is the
# sandbox project; `live` is the real one. Nothing here is a secret.
API_HOSTS = {"test": "https://test.stytch.com", "live": "https://api.stytch.com"}

# The vendor's frontend SDK, loaded ONLY by the login page (hosted/app.py adds
# this origin to that ONE route's Content-Security-Policy and to no other).
DEFAULT_SDK_URL = "https://js.stytch.com/stytch.js"
SDK_ORIGIN = "https://js.stytch.com"
VENDOR_API_ORIGIN = "https://*.stytch.com"


class SessionInvalid(Exception):
    """This token does not prove anyone is signed in.

    Carries a short reason for the LOG. It is never shown to a caller: a
    refusal that explains which check failed is a refusal that teaches an
    attacker which check to defeat next. The reader sees one quiet line.
    """


class SessionConfig:
    """The four facts a session verifier needs, plus where to get the keys.

    DEFAULTS ARE THE VENDOR'S DOCUMENTED SHAPES, and each is overridable by
    env, because the shapes are the part most likely to be wrong:

      issuer   default `stytch.com/<project_id>`   (secondary source; UNVERIFIED
                                                    against a live token —
                                                    SETUP.md step 1 settles it)
      audience default `<project_id>`               (same)
      jwks     default `<api host>/v1/sessions/jwks/<project_id>`  (first-party)

    An operator whose token carries different claims changes two env vars
    rather than waiting for a deploy — the same seam that makes a vendor swap
    a configuration change (the standing falsifier, DECISIONS 2026-08-31).
    """

    def __init__(self, env: Optional[Dict[str, str]] = None):
        env = os.environ if env is None else env
        self.project_id = (env.get(PROJECT_ID_VAR) or "").strip()
        self.public_token = (env.get(PUBLIC_TOKEN_VAR) or "").strip()
        self.environment = (env.get(ENVIRONMENT_VAR) or "").strip().lower()
        if self.environment not in API_HOSTS:
            # Derived from the project id's own prefix when it says so
            # (`project-live-...` / `project-test-...`); `live` otherwise,
            # because guessing `test` for a production box would verify
            # against the wrong key set and lock everybody out quietly.
            self.environment = "test" if self.project_id.startswith(
                "project-test-") else "live"
        self.issuer = (env.get(ISSUER_VAR) or "").strip() or (
            f"stytch.com/{self.project_id}" if self.project_id else "")
        self.audience = (env.get(AUDIENCE_VAR) or "").strip() or self.project_id
        self.jwks_url = (env.get(JWKS_URL_VAR) or "").strip() or (
            f"{API_HOSTS[self.environment]}/v1/sessions/jwks/{self.project_id}"
            if self.project_id else "")
        # An OPERATOR-PINNED key set, verbatim JSON. When present nothing here
        # ever opens a socket: the keys were fetched once by the operator's own
        # machine (which holds the vendor secret) and pasted into the host's
        # environment. Costs a re-paste at every ~6-month rotation, buys a
        # reading path with no third-party dependency at all. DEPLOY.md states
        # the trade both ways; the URL is the default.
        self.inline_jwks = (env.get(JWKS_INLINE_VAR) or "").strip()
        self.sdk_url = (env.get(SDK_URL_VAR) or "").strip() or DEFAULT_SDK_URL
        self.leeway = DEFAULT_LEEWAY_SECONDS

    @property
    def configured(self) -> bool:
        """Is there enough here to verify anybody?

        The public token is required too, even though verification does not use
        it: without it the login page cannot run a ceremony, so a box with keys
        and no public token is a door with a lock and no handle — better named
        as unconfigured at boot than discovered by a reader at 6am.
        """
        return bool(self.project_id and self.public_token
                    and self.issuer and self.audience
                    and (self.jwks_url or self.inline_jwks))

    def missing(self) -> List[str]:
        """Which names are missing, for the doctor and the boot log. Names
        only — a value is never printed from here."""
        out = []
        if not self.project_id:
            out.append(PROJECT_ID_VAR)
        if not self.public_token:
            out.append(PUBLIC_TOKEN_VAR)
        if not (self.jwks_url or self.inline_jwks):
            out.append(JWKS_URL_VAR)
        return out


def _http_get_json(url: str) -> Dict:
    """One GET, one timeout, one visible failure path (ENGINEERING.md).

    No credentials are sent. The vendor documents this key set as publicly
    readable ("verification keys available at a project-specific URL that is
    publicly accessible"); their API reference also lists the endpoint under
    the project's basic auth. THE EVIDENCE IS MIXED and we hold no secret to
    settle it, so the inline-pin path above exists precisely so a 401 here is
    an operator's one-line fix instead of a redesign.
    """
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=JWKS_TIMEOUT_SECONDS) as resp:
        return json.loads(resp.read().decode("utf-8"))


class JwksCache:
    """The public keys, held in memory, refreshed when they stop explaining.

    THREE BEHAVIOURS, each one a test:
      * a key set is fetched at most once per TTL;
      * a `kid` that is not in the cache triggers exactly ONE refetch, then is
        rate-limited (a forged-kid storm must not become a fetch storm);
      * a fetch failure never invalidates keys we already hold — an unreachable
        vendor may not sign every reader out.
    """

    def __init__(self, url: str, *, inline: str = "",
                 fetcher: Optional[Callable[[str], Dict]] = None,
                 ttl: int = DEFAULT_JWKS_TTL_SECONDS,
                 clock: Callable[[], float] = time.time):
        self.url = url
        self._fetch = fetcher or _http_get_json
        self._ttl = ttl
        self._clock = clock
        self._lock = threading.Lock()
        self._keys: Dict[str, Dict] = {}
        self._fetched_at = 0.0
        self._last_miss_fetch = 0.0
        self.fetch_count = 0
        self._inline_error: Optional[str] = None
        if inline:
            try:
                self._keys = self._index(json.loads(inline))
                # Pinned keys never expire on a clock: the operator's paste IS
                # the source of truth until they paste again.
                self._fetched_at = float("inf")
            except (ValueError, TypeError) as exc:
                self._inline_error = f"{JWKS_INLINE_VAR} is not a JWKS: {exc}"

    @staticmethod
    def _index(document) -> Dict[str, Dict]:
        keys = (document or {}).get("keys") if isinstance(document, dict) else None
        if not isinstance(keys, list):
            raise ValueError("no 'keys' array")
        out = {}
        for key in keys:
            if isinstance(key, dict) and key.get("kid"):
                out[str(key["kid"])] = key
        if not out:
            raise ValueError("no keys with a kid")
        return out

    def _refresh(self) -> None:
        if not self.url:
            return
        try:
            document = self._fetch(self.url)
            fresh = self._index(document)
        except Exception:
            # DELIBERATELY SWALLOWED, and this is the one place in the file
            # where that is right: a vendor outage must not sign out a reader
            # whose key we already hold. The keys we have keep working; the
            # next request tries again. A JWKS we never had at all surfaces as
            # a plain refusal at verify time.
            return
        self.fetch_count += 1
        self._keys = fresh
        self._fetched_at = self._clock()

    def key_for(self, kid: str) -> Optional[Dict]:
        with self._lock:
            now = self._clock()
            if not self._keys or now - self._fetched_at > self._ttl:
                self._refresh()
                now = self._clock()
            hit = self._keys.get(kid)
            if hit is not None:
                return hit
            # A ROTATION LOOKS EXACTLY LIKE A FORGERY from here — an unknown
            # kid — so the honest response is to look once and then stop.
            if now - self._last_miss_fetch >= JWKS_REFETCH_COOLDOWN_SECONDS:
                self._last_miss_fetch = now
                self._refresh()
                return self._keys.get(kid)
            return None

    @property
    def problem(self) -> Optional[str]:
        return self._inline_error


class SessionVerifier:
    """Signed string in, user id out, or SessionInvalid.

    Constructed once per app (hosted/app.py) so the key cache is shared by
    every worker thread; the cache takes its own lock.
    """

    def __init__(self, config: SessionConfig,
                 fetcher: Optional[Callable[[str], Dict]] = None,
                 clock: Callable[[], float] = time.time):
        self.config = config
        self.clock = clock
        self.jwks = JwksCache(config.jwks_url, inline=config.inline_jwks,
                              fetcher=fetcher, clock=clock)

    def available(self) -> Optional[str]:
        """None when this verifier can work; a reason when it cannot."""
        if pyjwt is None:
            return (f"PyJWT is not installed ({_JWT_IMPORT_ERROR}) — "
                    "hosted/requirements.txt lists it")
        if self.jwks.problem:
            return self.jwks.problem
        if not self.config.configured:
            return ("session verification is not configured: "
                    + ", ".join(self.config.missing()) + " unset")
        return None

    def verify(self, token: str) -> Dict:
        """The claims of a token this service is willing to believe.

        Order matters and is deliberate: cheap structural refusals first, the
        signature check last, so a malformed or hostile token never reaches the
        key cache and cannot be used to probe it.
        """
        problem = self.available()
        if problem:
            raise SessionInvalid(problem)
        if not token or not isinstance(token, str) or token.count(".") != 2:
            raise SessionInvalid("not a JWT")

        try:
            header = pyjwt.get_unverified_header(token)
        except Exception as exc:
            raise SessionInvalid(f"unreadable header: {exc}")

        # THE ALGORITHM IS OURS, NOT THE TOKEN'S. `alg: none` and the
        # RSA-public-key-as-HMAC-secret confusion both die here, before any key
        # is looked up — and again below, where PyJWT is given a one-item
        # algorithm list. Two locks on one door because this is the door.
        alg = header.get("alg")
        if alg != ALGORITHM:
            raise SessionInvalid(f"algorithm {alg!r} is not {ALGORITHM}")
        kid = header.get("kid")
        if not kid:
            raise SessionInvalid("no kid")

        jwk = self.jwks.key_for(str(kid))
        if jwk is None:
            raise SessionInvalid("no public key for this kid")
        try:
            key = pyjwt.PyJWK.from_dict(jwk, algorithm=ALGORITHM).key
        except Exception as exc:
            raise SessionInvalid(f"unusable public key: {exc}")

        try:
            claims = pyjwt.decode(
                token, key, algorithms=[ALGORITHM],
                issuer=self.config.issuer,
                audience=self.config.audience,
                leeway=self.config.leeway,
                options={"require": ["exp", "iat", "sub", "iss", "aud"],
                         "verify_signature": True, "verify_exp": True,
                         "verify_iat": True, "verify_iss": True,
                         "verify_aud": True},
            )
        except Exception as exc:
            raise SessionInvalid(f"{type(exc).__name__}: {exc}")

        subject = claims.get("sub")
        if not isinstance(subject, str) or not subject.strip():
            raise SessionInvalid("no subject")
        return claims

    def user_id(self, token: str) -> str:
        return self.verify(token)["sub"]
