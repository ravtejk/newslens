"""THE OPERATOR'S HAND — the one place a vendor secret is ever read.

NL-163 Stage-A milestone 3, per the principal's ruling 2026-08-31 ("Lets do
c") and its recorded secrets shape: THE HOST HOLDS NO VENDOR SECRET (public
token + JWKS only); the account-creation path holds `STYTCH_SECRET` MAC-SIDE,
in his `.env`, read at runtime, never echoed, never logged, never stored.

WHY THIS IS A MAC VERB AND NOT A HOSTED ROUTE. A reader-facing box that can
create accounts is a reader-facing box that can create accounts — which is the
whole of the §9 "no self-serve signup" law, expressed as an absence rather
than a check. So the power lives on the operator's own machine, behind a
command they type, and the deployable has no code path to it at all (pinned:
tests/test_nl163_auth.py asserts `/v1/users`, `/v1/passwords` and
`STYTCH_SECRET` appear nowhere in `hosted/`).

DRY-RUN BY DEFAULT (team/ENGINEERING.md: "anything that acts externally ships
behind a dry-run flag that defaults to on"). Creating an account is an
external act on a third party's system, so the bare verb PRINTS the request it
would make — method, URL, field names, never the secret and never the
password — and `--commit` is the word that makes it real.

WHAT IT DOES NOT DO: touch the host. The user→stream map is host-side
environment (`NEWSLENS_USER_STREAMS`, adjudication Q5), and this command
PRINTS the exact row for the operator to add. One account is one paper; there
is no picker and no in-shell identity, so a mapping is a deployment fact, not
a database row this verb could write.

STDLIB ONLY. The product tree stays three-dependency; this is two HTTPS calls.
"""

from __future__ import annotations

import base64
import json
import os
import secrets
import string
import urllib.error
import urllib.request
from typing import Callable, Dict, Optional, Tuple

PROJECT_ID_VAR = "STYTCH_PROJECT_ID"
SECRET_VAR = "STYTCH_SECRET"

API_HOSTS = {"test": "https://test.stytch.com", "live": "https://api.stytch.com"}

TIMEOUT_SECONDS = 30.0

# Long enough that it is never guessed, and made of characters a phone keyboard
# and a password manager both handle without ceremony. It is shown ONCE, on the
# operator's own terminal, and nothing writes it anywhere.
PASSWORD_ALPHABET = string.ascii_letters + string.digits
PASSWORD_LENGTH = 24


class AccountError(RuntimeError):
    """A refusal with the sentence the CLI prints. Never carries a credential."""


def environment_for(project_id: str) -> str:
    """`test` or `live`, from the project id's own prefix.

    Guessing wrong is not a small mistake — a live account created against the
    test project simply does not exist for the real paper — so the id says it
    or the operator does."""
    if project_id.startswith("project-test-"):
        return "test"
    if project_id.startswith("project-live-"):
        return "live"
    raise AccountError(
        f"{PROJECT_ID_VAR} does not look like a project id "
        "(expected it to start with 'project-test-' or 'project-live-'). "
        "Copy it from the vendor dashboard's API Keys page — SETUP.md step 1.")


def credentials(env: Optional[Dict[str, str]] = None, *,
                need_secret: bool = True) -> Tuple[str, str]:
    """(project_id, secret) or a refusal that names what is missing.

    THE SECRET IS RETURNED AND NEVER RETAINED: it goes straight into one
    Authorization header and out of scope. Nothing in this module stores it on
    an object, writes it to a file, or puts it in an exception message.

    A DRY RUN ASKS FOR LESS — the project id decides which vendor environment
    the call would go to, so the shape cannot be printed without it; the secret
    is not needed to describe a call that is not being made, and demanding it
    would make the safe verb the harder one."""
    env = os.environ if env is None else env
    project_id = (env.get(PROJECT_ID_VAR) or "").strip()
    secret = (env.get(SECRET_VAR) or "").strip()
    wanted = [(PROJECT_ID_VAR, project_id)]
    if need_secret:
        wanted.append((SECRET_VAR, secret))
    missing = [name for name, value in wanted if not value]
    if missing:
        raise AccountError(
            f"{' and '.join(missing)} not set. Put "
            f"{'them' if len(missing) > 1 else 'it'} in .env on THIS machine "
            "(never on the host — the paper's server holds no vendor secret): "
            # Names the SECTION, not the step. M4 renumbered §7's steps and
            # this pointer was already dangling before that ('Creating a
            # reader's account' matched no heading in SETUP.md at all) — a
            # refusal that sends the operator to a section that isn't there
            # is a small lie in the one message they read when stuck.
            "SETUP.md, 'The phone door'.")
    return project_id, secret


def make_password(length: int = PASSWORD_LENGTH) -> str:
    return "".join(secrets.choice(PASSWORD_ALPHABET) for _ in range(length))


def _post(url: str, project_id: str, secret: str, payload: Dict,
          opener: Optional[Callable] = None) -> Dict:
    """One authenticated call, one timeout, one visible failure path.

    Basic auth is the vendor's documented server-API scheme (project id as the
    user, secret as the password). The header is built here and nowhere else."""
    body = json.dumps(payload).encode("utf-8")
    token = base64.b64encode(f"{project_id}:{secret}".encode("utf-8")).decode("ascii")
    request = urllib.request.Request(url, data=body, method="POST", headers={
        "Authorization": f"Basic {token}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    })
    try:
        with (opener or urllib.request.urlopen)(
                request, timeout=TIMEOUT_SECONDS) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            detail = json.loads(exc.read().decode("utf-8")).get("error_message", "")
        except Exception:                                   # noqa: BLE001
            detail = ""
        # The status and the vendor's own sentence — never the request body,
        # which held a password, and never the header, which held the secret.
        raise AccountError(f"the vendor refused ({exc.code}): "
                           f"{detail or exc.reason}")
    except urllib.error.URLError as exc:
        raise AccountError(f"could not reach the vendor: {exc.reason}")


def _get(url: str, project_id: str, secret: str,
         opener: Optional[Callable] = None) -> Dict:
    token = base64.b64encode(f"{project_id}:{secret}".encode("utf-8")).decode("ascii")
    request = urllib.request.Request(url, headers={
        "Authorization": f"Basic {token}", "Accept": "application/json"})
    try:
        with (opener or urllib.request.urlopen)(
                request, timeout=TIMEOUT_SECONDS) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raise AccountError(f"the vendor refused ({exc.code}): {exc.reason}")
    except urllib.error.URLError as exc:
        raise AccountError(f"could not reach the vendor: {exc.reason}")


def create(email: str, stream: str, *, commit: bool = False,
           env: Optional[Dict[str, str]] = None,
           opener: Optional[Callable] = None,
           password: Optional[str] = None) -> Dict:
    """Create one reader's account, or say exactly what would be created.

    Returns a dict the CLI prints. The password is in it exactly once and is
    the caller's to display and then forget; nothing here writes it down.
    """
    if not email or "@" not in email:
        raise AccountError(f"{email!r} is not an email address")
    if not stream or not stream.replace("-", "").isalnum() or not stream.islower():
        raise AccountError(
            f"{stream!r} is not a stream name (lower-case letters, digits and "
            "hyphens — it is the folder the host keeps this paper in)")

    project_id, secret = credentials(env, need_secret=commit)
    where = environment_for(project_id)
    url = f"{API_HOSTS[where]}/v1/passwords"
    password = password or make_password()

    if not commit:
        # DRY RUN. Names, not values: the operator sees the shape of the call
        # and no part of it that would be a secret in a scrollback buffer.
        return {"committed": False, "environment": where, "method": "POST",
                "url": url, "fields": ["email", "password"], "email": email,
                "stream": stream,
                "note": "nothing was sent — add --commit to create the account"}

    result = _post(url, project_id, secret,
                   {"email": email, "password": password}, opener=opener)
    user_id = result.get("user_id")
    if not user_id:
        raise AccountError("the vendor accepted the call but returned no "
                           "user_id — nothing was mapped; check the dashboard "
                           "before retrying so a second account is not made")
    return {"committed": True, "environment": where, "user_id": user_id,
            "email": email, "stream": stream, "password": password,
            # The operator's next action, spelled out: this command does not
            # touch the host, and a mapping the host does not have is an
            # account that signs in and is told it has no paper.
            "env_row": json.dumps({user_id: stream})}


def jwks(env: Optional[Dict[str, str]] = None,
         opener: Optional[Callable] = None) -> Dict:
    """Fetch this project's public key set, for the operator to pin.

    Read-only, no spend, and the ONE reason this needs the secret at all: the
    vendor's API reference lists the endpoint under the project's basic auth,
    while their own writing calls the key URL publicly accessible. We hold the
    secret here, so we use it and stop guessing — and the host gets the answer
    as a pasted value (`NEWSLENS_STYTCH_JWKS`) with no third party on its
    reading path."""
    project_id, secret = credentials(env)
    where = environment_for(project_id)
    url = f"{API_HOSTS[where]}/v1/sessions/jwks/{project_id}"
    document = _get(url, project_id, secret, opener=opener)
    keys = document.get("keys")
    if not isinstance(keys, list) or not keys:
        raise AccountError(f"{url} returned no keys")
    return {"url": url, "environment": where,
            "jwks": json.dumps({"keys": keys}, separators=(",", ":")),
            "kids": [k.get("kid") for k in keys if isinstance(k, dict)]}
