"""THE PUSH — the Mac hands the morning's edition to the paper's host.

NL-163 Stage-A milestone 2, per the engineering adjudication 2026-08-29 §Q4.

WHAT IT IS. One `PUT` of one already-minted artifact. The Mac is the only
generator and the authority on what an edition is; the host stores what it is
handed. There is no negotiation, no partial upload, no server-side render — the
document that arrives is the document that was frozen at publish.

WHAT IT NEVER DOES: break a generate. It mounts at the same post-publish seam
as the bundle mint (generate.py, the :5485 containment precedent), so a dead
host, a wrong token, a laptop on a train — every one of them costs a warning
line and nothing else. The edition is already written to the database, the
briefing file, the run log and the local artifact by the time this runs, and
the artifact is exactly what `newslens push --date` re-sends later.

STDLIB ONLY (`urllib`), like the rest of the product tree. The hosted service
is the deployable with the dependency story; this is one HTTP request.

SECRETS. `NEWSLENS_PUSH_TOKEN` is read from the environment and sent in an
`Authorization: Bearer` header. It is never logged, never written to the push
state file, never echoed in a warning — the host stores only its sha256, so
this string exists in exactly two places: his `.env` and this request.
"""

from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Optional, Tuple
from urllib.parse import urlsplit

from . import paths

URL_VAR = "NEWSLENS_PUSH_URL"
TOKEN_VAR = "NEWSLENS_PUSH_TOKEN"

# The endpoint shape the host publishes (adjudication Q4). The configured URL
# names the STREAM — one paper per token — and this client appends the edition.
STREAM_URL_RE = re.compile(r"^https?://[^/]+(/[^?#]*)?/api/streams/[a-z0-9][a-z0-9-]{0,31}$")

STATE_FILE_NAME = "push-state.json"

ATTEMPTS = 3
BACKOFF_SECONDS = (1.0, 2.0)
TIMEOUT_SECONDS = 30.0


class PushError(RuntimeError):
    """A push could not be attempted or was refused. Carries the sentence the
    caller prints — at the mount it becomes a warning, at the CLI an exit 1."""


class PushResult:
    def __init__(self, ok: bool, status: int, detail: str, *,
                 payload: Optional[Dict] = None, attempts: int = 1):
        self.ok, self.status, self.detail = ok, status, detail
        self.payload = payload or {}
        self.attempts = attempts

    def __repr__(self) -> str:
        return f"<PushResult ok={self.ok} status={self.status} {self.detail!r}>"


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

def config(env: Optional[Dict[str, str]] = None) -> Tuple[str, str]:
    """(url, token) from the environment, or ("", "") when the push is not
    configured. NOT configured is a legitimate state — a Mac with no host yet
    still generates, still mints, still opens the artifact locally."""
    env = os.environ if env is None else env
    return (env.get(URL_VAR) or "").strip(), (env.get(TOKEN_VAR) or "").strip()


def is_configured(env: Optional[Dict[str, str]] = None) -> bool:
    url, token = config(env)
    return bool(url and token)


def endpoint(url: str, date: str) -> str:
    """The PUT target for one edition, refusing a URL that is not a stream.

    The refusal is loud on purpose: a base URL missing its `/api/streams/<x>`
    tail would otherwise PUT into an address that answers 404 every morning,
    and the operator would learn about it from an empty phone."""
    base = (url or "").rstrip("/")
    if not STREAM_URL_RE.match(base):
        raise PushError(
            f"{URL_VAR} must name the stream — it looks like "
            "https://<host>/api/streams/<stream> (the token decides which "
            f"stream may be written; got {base!r})")
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", date or ""):
        raise PushError(f"not an edition date: {date!r}")
    return f"{base}/editions/{date}"


def _host_of(url: str) -> str:
    """Host and port ONLY — never the userinfo.

    `urlsplit().netloc` keeps `user:password@` when a URL carries credentials,
    and this string is written into the push state file and printed by the
    doctor. A token that reaches either one has reached every screenshot of
    them. (Found by its own test before it was ever true in a report.)"""
    try:
        parts = urlsplit(url)
        host = parts.hostname or ""
        return f"{host}:{parts.port}" if parts.port else host
    except ValueError:
        return ""


# ---------------------------------------------------------------------------
# The request
# ---------------------------------------------------------------------------

def push_bundle(bundle: Dict, *, url: str, token: str,
                attempts: int = ATTEMPTS, sleep=time.sleep) -> PushResult:
    """PUT one bundle. Returns a result; raises only on a config error.

    RETRIES ARE FOR THE NETWORK, NOT FOR REFUSALS. A 4xx is the host telling us
    something true about this request (bad token, wrong stream, digest
    mismatch) — repeating it three times would just be three copies of the same
    wrong. Connection errors and 5xx get the backoff."""
    date = bundle.get("edition_date") or ""
    target = endpoint(url, date)
    body = json.dumps(bundle, ensure_ascii=False).encode("utf-8")
    last: Optional[PushResult] = None
    for attempt in range(1, max(1, attempts) + 1):
        request = urllib.request.Request(
            target, data=body, method="PUT",
            headers={"Authorization": f"Bearer {token}",
                     "Content-Type": "application/json",
                     "User-Agent": "newslens-push/1"})
        try:
            with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as resp:
                raw = resp.read().decode("utf-8", "replace")
                payload = _json_or_empty(raw)
                return PushResult(True, resp.status,
                                  payload.get("status") or "accepted",
                                  payload=payload, attempts=attempt)
        except urllib.error.HTTPError as exc:
            raw = ""
            try:
                raw = exc.read().decode("utf-8", "replace")
            except Exception:      # noqa: BLE001 — a body we cannot read is not the story
                pass
            payload = _json_or_empty(raw)
            detail = payload.get("error") or (raw.strip()[:200] or exc.reason or "")
            last = PushResult(False, exc.code, str(detail), payload=payload,
                              attempts=attempt)
            if exc.code < 500:
                return last          # an honest refusal, not a flaky network
        except (urllib.error.URLError, OSError, ValueError) as exc:
            reason = getattr(exc, "reason", None) or exc
            last = PushResult(False, 0, f"{type(exc).__name__}: {reason}",
                              attempts=attempt)
        if attempt <= len(BACKOFF_SECONDS):
            sleep(BACKOFF_SECONDS[attempt - 1])
    return last or PushResult(False, 0, "no attempt was made")


def _json_or_empty(raw: str) -> Dict:
    try:
        parsed = json.loads(raw or "{}")
    except ValueError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


# ---------------------------------------------------------------------------
# The local record — what the doctor can stat
# ---------------------------------------------------------------------------
#
# The doctor is deliberately network-free (its own honesty bound: it reports
# what it can read, never what it infers about another machine). So the push
# writes down what happened, and the doctor compares that record to the
# artifacts on disk. NO TOKEN, NO URL PATH, NO EDITION CONTENT lands here —
# just the host, the date, the outcome and two timestamps.

def state_path() -> Path:
    return paths.DATA_DIR / STATE_FILE_NAME


def read_state() -> Dict:
    try:
        parsed = json.loads(state_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def record(date: str, result: PushResult, url: str) -> None:
    """Append-by-replace: the last attempt and the last SUCCESS, kept apart so
    a week of failures cannot erase the memory of when the paper last landed."""
    state = read_state()
    entry = {
        "date": date,
        "host": _host_of(url),
        "status": result.detail if result.ok else "failed",
        "http_status": result.status,
        "attempts": result.attempts,
        "at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    if result.ok:
        entry["host_pushed_at"] = result.payload.get("pushed_at") or ""
        state["last_success"] = entry
    else:
        entry["detail"] = result.detail[:300]
    state["last_attempt"] = entry
    path = state_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(json.dumps(state, indent=2), encoding="utf-8")
        os.replace(str(tmp), str(path))
    except OSError:
        # The record is an instrument, not the product: failing to write it
        # must never turn a delivered edition into a failed one.
        pass


# ---------------------------------------------------------------------------
# The two callers
# ---------------------------------------------------------------------------

def push_date(date: str, *, env: Optional[Dict[str, str]] = None,
              sleep=time.sleep) -> PushResult:
    """Push the LOCAL ARTIFACT for `date` — the retry path and the CLI verb.

    Reads the frozen document from disk rather than rebuilding it: a rebuild
    would render today's follow state into yesterday's edition and call it
    frozen at publish (the no-seeding ruling, `editionbundle.load`'s refusal)."""
    from . import editionbundle
    url, token = config(env)
    if not url or not token:
        raise PushError(
            f"push is not configured — set {URL_VAR} and {TOKEN_VAR} in .env "
            "(see .env.example; the host holds only the token's sha256)")
    bundle = editionbundle.load(date)          # raises BundleError, honestly
    result = push_bundle(bundle, url=url, token=token, sleep=sleep)
    record(date, result, url)
    return result


def push_after_publish(date: str, *, env: Optional[Dict[str, str]] = None,
                       sleep=time.sleep) -> Optional[str]:
    """The generate seam. Returns a WARNING LINE or None; never raises.

    None means one of two things and the difference belongs in the doctor, not
    in the middle of a generate's output: the push succeeded, or no host is
    configured at all. A configured push that fails says so here, in the run's
    own words, next to the edition it could not deliver."""
    if not is_configured(env):
        return None
    try:
        result = push_date(date, env=env, sleep=sleep)
    except Exception as exc:            # noqa: BLE001 — post-publish containment
        return (f"phone push: NOT delivered for {date} ({exc}) — the edition is "
                "PUBLISHED and unaffected; the frozen artifact is on disk and "
                f"`newslens push --date {date}` re-sends it")
    if result.ok:
        return None
    return (f"phone push: NOT delivered for {date} — the host answered "
            f"{result.status or 'nothing'}: {result.detail}. The edition is "
            "PUBLISHED and unaffected; the frozen artifact is on disk and "
            f"`newslens push --date {date}` re-sends it")
