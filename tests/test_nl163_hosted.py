"""NL-163 Stage-A M2 — THE HOSTED PAPER: the door, the shelf, and the refusal.

Binding sources: the engineering adjudication 2026-08-29 §Q2 (service shape,
file storage + one SQLite ledger), §Q4 (token-authed idempotent PUT, sha
verified server-side, honest rejections), §Q5 (one user, one stream, no
picker), §Q6 (a read_events row per authenticated edition GET, no client event
API); DIRECTION-phone-addendum §§5-11; the approved mockup's states 1/3/4/5.

WHAT THIS FILE PINS, and why each pin exists

  * THE DOOR. A token is compared against a stored DIGEST in constant time and
    the STREAM COMES FROM THE TOKEN — a URL cannot talk its way into another
    paper. Bad token, wrong stream, bad date, oversize body, mismatched
    digest, mis-dated envelope: six refusals, each measured, each leaving the
    shelf untouched.
  * IDEMPOTENCE. The same document arriving twice is not an event: nothing is
    rewritten and the offline stamp keeps the time the bytes actually landed.
  * THE FROZEN DOCUMENT STAYS FROZEN. What the host adds is a tail and three
    head links; strip them and the bytes are the bundle's, exactly.
  * THE LEDGER SEES GETS AND NOTHING ELSE. A row per authenticated edition
    read; no row for a shell state, a static asset, or a login page — and,
    structurally, no row for an edition the service worker served from the
    cache. That hole is the honest cost of having no client event API.
  * THE BYPASS CANNOT TRAVEL. `NEWSLENS_DEV_NO_AUTH=1` refuses to boot on any
    non-loopback bind or any hosting platform — proven at import in a real
    child process, not asserted about the source.

Hermetic: Flask's test client is in-process (no socket), every path is a
sandbox, and the module skips whole if Flask is not installed — the suite
stays green for a dev who has not installed the hosted dependency.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys

import pytest

from conftest import PROTOTYPE_ROOT

pytest.importorskip("flask", reason="hosted/ needs Flask (pip install -e '.[dev]')")

from hosted import app as hosted_app          # noqa: E402  (after importorskip)
from hosted.hoststore import HostStore        # noqa: E402

TODAY = "2026-08-29"
YESTERDAY = "2026-08-28"
TOKEN = "push-token-for-the-tests"
DIGEST = hashlib.sha256(TOKEN.encode("utf-8")).hexdigest()
USER = "user_test"

HTML = ("<!DOCTYPE html><html lang=\"en\"><head><meta charset=\"UTF-8\">"
        "<title>NewsLens</title></head><body>"
        "<header class=\"page masthead\"><p class=\"wordmark\">NewsLens</p>"
        "<p class=\"dispatch-strip\">Edition assembled 06:02 UTC</p>"
        "<p class=\"offline-line\" id=\"offline-stamp\" hidden></p>"
        "<nav class=\"section-line\"><div class=\"page\">"
        "<span class=\"section-current\">Today</span></div></nav></header>"
        "<main id=\"main\">the edition</main></body></html>")


def bundle(date=TODAY, html=HTML):
    return {"bundle_version": 1, "edition_date": date,
            "generated_at": f"{date}T06:02:00.000Z", "variant": "A",
            "content_sha256": hashlib.sha256(html.encode("utf-8")).hexdigest(),
            "meta": {"story_count": 3, "arc_present": True},
            "audio": None, "html": html}


def env_for(tmp_path, **over):
    env = {"NEWSLENS_HOSTED_DATA": str(tmp_path / "hostdata"),
           "NEWSLENS_PUSH_TOKENS": json.dumps({"main": DIGEST}),
           "NEWSLENS_USER_STREAMS": json.dumps({USER: "main"}),
           "NEWSLENS_DEV_NO_AUTH": "1"}
    env.update(over)
    return {k: v for k, v in env.items() if v is not None}


@pytest.fixture
def store(tmp_path):
    return HostStore(tmp_path / "hostdata")


@pytest.fixture
def client(tmp_path):
    return hosted_app.create_app(env_for(tmp_path)).test_client()


def text(page: str) -> str:
    """Rendered copy with HTML's own whitespace collapsed — a template that
    wraps a sentence across two indented lines renders the same sentence, and
    a copy pin that could not see that would be pinning the indentation."""
    return re.sub(r"\s+", " ", page)


def put(client, payload, *, date=TODAY, stream="main", token=TOKEN, raw=None):
    body = raw if raw is not None else json.dumps(payload)
    headers = {"Content-Type": "application/json"}
    if token is not None:
        headers["Authorization"] = f"Bearer {token}"
    return client.put(f"/api/streams/{stream}/editions/{date}", data=body,
                      headers=headers)


# ---------------------------------------------------------------------------
# THE PUSH ENDPOINT
# ---------------------------------------------------------------------------

def test_a_pushed_edition_lands_on_the_shelf_with_a_receipt(client, store):
    resp = put(client, bundle())
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["status"] == "stored"
    assert body["content_sha256"] == bundle()["content_sha256"]
    assert body["pushed_at"].endswith("Z")

    assert store.read_edition("main", TODAY)["html"] == HTML
    assert store.latest("main") == TODAY
    assert store.counts()["push_receipts"] == 1


def test_the_same_document_twice_is_not_an_event(client, store):
    """IDEMPOTENT (Q4). And the stamp does not move: `pushed at 06:02` is when
    the bytes LANDED, and nothing landed the second time."""
    first = put(client, bundle()).get_json()
    path = store.edition_path("main", TODAY)
    before = path.stat().st_mtime_ns

    second = put(client, bundle()).get_json()
    assert second["status"] == "unchanged"
    assert second["pushed_at"] == first["pushed_at"]
    assert path.stat().st_mtime_ns == before, "the file was rewritten"


def test_the_retry_after_a_crashed_push_repairs_the_front_page(client, store,
                                                               monkeypatch):
    """THE DOCUMENTED RECOVERY VERB HAS TO RECOVER.

    A push that lands the bundle and dies before the pointer moves (a crash, a
    redeploy, or any of the concurrent-write 500s above) leaves today's edition
    on the shelf with `latest` still naming yesterday — so the front page
    renders the empty panel, whose copy ("No edition has been generated for
    today") is a FALSE sentence about a paper that is sitting right there.
    DEPLOY.md's answer to a lost pointer is `newslens push --date`; before this
    pin, that retry answered 200 "unchanged" and repaired nothing, because the
    idempotent branch skipped `set_latest`. Measured end-to-end over a real
    loopback wire pre-fix, not inferred."""
    monkeypatch.setattr(hosted_app.Config, "today", lambda self: TODAY)
    put(client, bundle(date=YESTERDAY), date=YESTERDAY)

    # The crash: the bytes land, the process dies before the pointer moves.
    store.write_edition("main", TODAY, json.dumps(bundle()).encode())
    assert store.latest("main") == YESTERDAY, "the pre-condition is a stale pointer"
    assert "Nothing for today yet" in client.get("/").get_data(as_text=True)

    # The retry, byte-identical — `newslens push --date <today>`.
    assert put(client, bundle()).get_json()["status"] == "unchanged"

    assert store.latest("main") == TODAY, "the retry did not repair the pointer"
    page = client.get("/").get_data(as_text=True)
    assert "the edition" in page and "Nothing for today yet" not in page


def test_a_changed_edition_replaces_and_the_stamp_moves(client, store):
    first = put(client, bundle()).get_json()
    corrected = bundle(html=HTML.replace("the edition", "the edition, corrected"))
    second = put(client, corrected).get_json()
    assert second["status"] == "replaced"
    assert second["pushed_at"] != first["pushed_at"]
    assert "corrected" in store.read_edition("main", TODAY)["html"]
    assert store.pushed_at("main", TODAY) == second["pushed_at"]


def test_an_unknown_token_is_refused_and_writes_nothing(client, store):
    resp = put(client, bundle(), token="not-the-token")
    assert resp.status_code == 401
    assert resp.get_json()["error"] == "token not recognised"
    assert store.dates("main") == [] and store.counts()["push_receipts"] == 0


def test_no_token_at_all_is_refused(client):
    assert put(client, bundle(), token=None).status_code == 401


def test_the_stream_comes_from_the_token_not_from_the_url(client, store):
    """Rook's enumeration, item 1. A valid token pointed at another paper's
    address is refused rather than redirected — a misconfigured Mac is loud on
    its first push instead of writing into somebody else's paper."""
    resp = put(client, bundle(), stream="someone-else")
    assert resp.status_code == 403
    assert "publishes 'main'" in resp.get_json()["error"]
    assert store.dates("someone-else") == []


def test_a_body_whose_digest_does_not_match_is_refused(client, store):
    """VERIFIED SERVER-SIDE. A truncated upload and a corrupted one look
    identical to a length check; they do not look identical to a digest."""
    lying = bundle()
    lying["html"] = lying["html"].replace("the edition", "something else")
    resp = put(client, lying)
    assert resp.status_code == 400
    assert "content_sha256 does not match" in resp.get_json()["error"]
    assert store.dates("main") == []


def test_a_bundle_dated_elsewhere_cannot_be_filed_under_this_date(client, store):
    resp = put(client, bundle(date=YESTERDAY), date=TODAY)
    assert resp.status_code == 400
    assert "was pushed to" in resp.get_json()["error"]
    assert store.dates("main") == []


@pytest.mark.parametrize("date", ["notadate", "2026-8-2", "20260829"])
def test_a_date_that_is_not_a_date_never_reaches_the_filesystem(client, date):
    resp = put(client, bundle(), date=date)
    assert resp.status_code in (400, 404)


def test_a_traversal_attempt_finds_no_route(client, tmp_path):
    """The date is a path component. It is validated everywhere it is
    accepted, and the route itself does not match a path that contains one."""
    resp = client.put("/api/streams/main/editions/../../etc/passwd",
                      data="{}", headers={"Authorization": f"Bearer {TOKEN}"})
    assert resp.status_code in (404, 405)
    assert not (tmp_path / "hostdata" / "streams" / "etc").exists()


def test_an_oversize_body_is_refused_by_size_not_by_parsing(tmp_path):
    app = hosted_app.create_app(env_for(tmp_path, NEWSLENS_MAX_BUNDLE_BYTES="2048"))
    big = bundle(html=HTML + "x" * 4096)
    resp = put(app.test_client(), big)
    assert resp.status_code == 413
    assert HostStore(tmp_path / "hostdata").dates("main") == []


def test_the_route_enforces_the_limit_itself_and_not_only_the_server(tmp_path):
    """A PIN THAT REACHES THE CODE UNDER TEST. The framework's own
    MAX_CONTENT_LENGTH sits a kilobyte above ours, so a hugely oversize body is
    refused before the route runs — and a test that only sends one would pass
    with the route's own check deleted (measured: it did, M-17). This body
    lands in the gap where the route's check is the only thing standing."""
    limit = 8192
    app = hosted_app.create_app(env_for(tmp_path, NEWSLENS_MAX_BUNDLE_BYTES=str(limit)))
    payload = json.dumps(bundle())
    body = payload + " " * (limit + 200 - len(payload))     # over ours, under Flask's
    assert limit < len(body) < limit + 1024
    resp = put(app.test_client(), None, raw=body)
    assert resp.status_code == 413
    assert f"the limit is {limit}" in resp.get_json()["error"], (
        "this must be the ROUTE's refusal, not the framework's")
    assert HostStore(tmp_path / "hostdata").dates("main") == []


def test_garbage_is_rejected_honestly(client):
    resp = put(client, None, raw="this is not json")
    assert resp.status_code == 400
    assert "not the JSON" in resp.get_json()["error"]
    resp = put(client, {"bundle_version": 1, "edition_date": TODAY})
    assert resp.status_code == 400
    assert "no html" in resp.get_json()["error"]


@pytest.mark.parametrize("html,anchor,count", [
    # no `</head>` at all — the M4 gate's own probe
    ("<!DOCTYPE html><html><body>an edition</body></html>", "</head>", 0),
    # a head that closes, a body that never does
    ("<!DOCTYPE html><html><head></head><body>an edition", "</body>", 0),
    # two closes: the ambiguity `augment_edition` refuses at read time
    ("<html><head></head><head></head><body>x</body></html>", "</head>", 2),
])
def test_a_bundle_the_serve_path_cannot_serve_is_refused_at_the_push(
        client, store, html, anchor, count):
    """THE PUBLISH PATH MAY NOT ACCEPT WHAT THE SERVE PATH CANNOT SERVE (M4
    gate FIX-1, its own find).

    Measured at the pre-fix bytes: a sha-valid, correctly-dated bundle with no
    `</head>` was answered **200 stored**, `latest` moved to it, and every
    subsequent read — the front page first — answered **500** with a StoreError
    traceback until somebody pushed a good one. An operator-self-inflicted
    availability hole, and a direct contradiction of this endpoint's own stated
    philosophy: a misconfigured Mac is loud on its FIRST PUSH."""
    resp = put(client, bundle(html=html))
    assert resp.status_code == 400
    assert resp.get_json()["error"] == (
        f"bundle html cannot be augmented: {anchor} occurs {count} times, "
        "expected exactly 1")
    assert store.dates("main") == []


def test_a_good_bundle_still_lands_and_still_serves_after_the_push(
        client, store, monkeypatch):
    """THE CONTROL THAT WOULD HAVE CAUGHT IT. Every other push pin stops at the
    200; this one reads the paper back afterwards, which is the only assertion
    that can see a bundle the shelf accepted and the reader cannot have."""
    monkeypatch.setattr(hosted_app.Config, "today", lambda self: TODAY)
    assert put(client, bundle()).get_json()["status"] == "stored"
    assert store.dates("main") == [TODAY]
    served = client.get("/")
    assert served.status_code == 200
    assert "the edition" in served.get_data(as_text=True)


def test_a_refusal_is_a_sentence_and_never_a_stack_trace(client):
    for resp in (put(client, bundle(), token="wrong"),
                 put(client, None, raw="{"),
                 client.get("/api/nothing-here")):
        body = resp.get_data(as_text=True)
        assert "Traceback" not in body and "<html" not in body.lower()
        assert resp.get_json() and "error" in resp.get_json()


# ---------------------------------------------------------------------------
# THE READING SURFACE
# ---------------------------------------------------------------------------

def test_the_front_page_serves_todays_edition_and_logs_one_read(client, store,
                                                                monkeypatch):
    monkeypatch.setattr(hosted_app.Config, "today", lambda self: TODAY)
    put(client, bundle())
    resp = client.get("/")
    assert resp.status_code == 200
    assert "the edition" in resp.get_data(as_text=True)
    assert resp.headers["X-NewsLens-Edition-Date"] == TODAY
    assert resp.headers["X-NewsLens-Pushed-At"]
    assert store.counts()["read_events"] == 1


def test_the_document_the_reader_gets_is_the_document_that_was_frozen(client,
                                                                      monkeypatch):
    """The host adds a tail and three head links and touches NOTHING else.
    Strip exactly what it added and the bytes are the bundle's, exactly — the
    freeze is the whole point of the artifact, and a host that rewrote it
    would have quietly become a second renderer."""
    monkeypatch.setattr(hosted_app.Config, "today", lambda self: TODAY)
    put(client, bundle())
    served = client.get("/").get_data(as_text=True)

    assert 'id="newslens-edition"' in served
    assert '/static/shell.js' in served
    assert 'rel="manifest"' in served
    stripped = re.sub(r'<script id="newslens-edition".*?</script>'
                      r'<script src="/static/shell.js" defer></script>', "",
                      served, flags=re.S)
    stripped = stripped.replace(hosted_app._HEAD_LINKS, "")
    assert stripped == HTML


def test_the_island_carries_what_only_the_host_knows(client, monkeypatch):
    """The offline stamp needs three facts the frozen document cannot hold:
    which date it is, what day that was, and when the bytes LANDED here."""
    monkeypatch.setattr(hosted_app.Config, "today", lambda self: TODAY)
    landed = put(client, bundle()).get_json()["pushed_at"]
    served = client.get("/").get_data(as_text=True)
    island = json.loads(re.search(
        r'<script id="newslens-edition" type="application/json">(.*?)</script>',
        served, re.S).group(1))
    assert island == {"date": TODAY, "pushed_at": landed, "day": "Saturday",
                      "bundle_version": 1}


def test_the_offline_slot_survives_the_augmentation(client, monkeypatch):
    monkeypatch.setattr(hosted_app.Config, "today", lambda self: TODAY)
    put(client, bundle())
    assert 'id="offline-stamp"' in client.get("/").get_data(as_text=True)


def test_augmenting_a_document_with_a_doubled_anchor_is_refused():
    """The bundle's own single-occurrence discipline (M1 FIX-3): an anchor that
    appears twice would silently insert into the wrong place, and a document
    that is silently wrong is what this milestone exists to prevent."""
    from hosted.hoststore import StoreError
    with pytest.raises(StoreError):
        hosted_app.augment_edition("<body></body></body>", {"date": TODAY})
    with pytest.raises(StoreError):
        hosted_app.augment_edition("<html><head></head><body>x", {"date": TODAY})


def test_before_the_morning_push_the_reader_gets_the_drawn_empty_state(client,
                                                                       store,
                                                                       monkeypatch):
    """STATE 3 with STATE 5's grammar in the generate slot — his as-drawn
    ruling on open flag 3. Reading is the primary act; the secondary says the
    press cannot be reached rather than offering a control that does nothing."""
    monkeypatch.setattr(hosted_app.Config, "today", lambda self: TODAY)
    put(client, bundle(date=YESTERDAY), date=YESTERDAY)
    page = client.get("/").get_data(as_text=True)

    assert "Nothing for today yet" in page
    assert "Read last generated edition" in page
    assert f'href="/editions/{YESTERDAY}"' in page
    assert "The press can’t be reached from here." in text(page)
    assert "Friday’s edition is still readable above" in text(page)
    assert "Generate today’s edition" not in page      # no dead control
    assert store.counts()["read_events"] == 0, "a shell state is not a read"


def test_day_one_is_honestly_empty(client, monkeypatch):
    """The no-seeding ruling, rendered: nothing is reconstructed, so on day one
    there is no edition to offer and no archive to point at — and neither is
    mentioned, rather than mentioned falsely."""
    monkeypatch.setattr(hosted_app.Config, "today", lambda self: TODAY)
    page = client.get("/").get_data(as_text=True)
    assert "Nothing for today yet" in page
    assert "Read last generated edition" not in page
    assert "Earlier editions are in your" not in page
    assert "Today’s edition will appear when the press next pushes" in text(page)


def test_an_archive_edition_reads_and_is_logged_as_an_archive_read(client, store,
                                                                   monkeypatch):
    monkeypatch.setattr(hosted_app.Config, "today", lambda self: TODAY)
    put(client, bundle(date=YESTERDAY), date=YESTERDAY)
    put(client, bundle())
    assert client.get(f"/editions/{YESTERDAY}").status_code == 200
    assert client.get(f"/editions/{TODAY}").status_code == 200

    con = store.connect()
    kinds = [r["kind"] for r in con.execute(
        "SELECT kind FROM read_events ORDER BY id")]
    con.close()
    assert kinds == ["archive", "today"]


def test_an_edition_that_was_never_pushed_is_a_404_page_not_a_traceback(client):
    resp = client.get("/editions/2011-01-01")
    assert resp.status_code == 404
    assert "Nothing at this address" in resp.get_data(as_text=True)
    assert "Traceback" not in resp.get_data(as_text=True)


def test_the_archive_lists_dates_newest_first(client):
    for date in (YESTERDAY, TODAY):
        put(client, bundle(date=date), date=date)
    page = client.get("/archive").get_data(as_text=True)
    assert page.index(TODAY) < page.index(YESTERDAY)
    assert f'href="/editions/{TODAY}"' in page
    assert "Saturday, August 29" in page


def test_the_empty_archive_says_so(client, store):
    page = client.get("/archive").get_data(as_text=True)
    assert "No editions have been pushed to this paper yet" in text(page)
    assert store.counts()["read_events"] == 0


def test_the_ledger_never_logs_a_read_for_furniture(client, store, monkeypatch):
    """A read event means A PERSON OPENED AN EDITION. Login pages, static
    assets, the service worker and the liveness probe are not readings, and a
    ledger that counted them would answer a question nobody asked."""
    monkeypatch.setattr(hosted_app.Config, "today", lambda self: TODAY)
    put(client, bundle())
    for path in ("/login", "/archive", "/sw.js", "/api/ping", "/healthz",
                 "/static/tokens.css", "/static/manifest.webmanifest",
                 "/nothing-here"):
        client.get(path)
    assert store.counts()["read_events"] == 0


# ---------------------------------------------------------------------------
# THE DOOR — what an unauthenticated visitor gets (M2: the stub's other half)
# ---------------------------------------------------------------------------

def test_without_a_session_every_reading_route_goes_to_the_login_page(tmp_path):
    """M2's contract, EXTENDED BY M3 and disclosed: the redirect now carries
    the page the visitor wanted.

    Why the extension exists rather than a cosmetic tidy: the vendor's session
    JWT expires every five minutes by design, so an ordinary reader opening
    yesterday's edition can arrive at the door mid-journey. Without a return
    path the silent refresh would land them on the front page — a paper that
    quietly changes which page you asked for. `/` still redirects bare,
    because `/` is where the login page goes by default."""
    client = hosted_app.create_app(
        env_for(tmp_path, NEWSLENS_DEV_NO_AUTH=None)).test_client()
    assert client.get("/").headers["Location"] == "/login"
    for path in (f"/editions/{TODAY}", "/archive"):
        resp = client.get(path)
        assert resp.status_code == 302
        assert resp.headers["Location"] == f"/login?next={path}"


def test_the_cache_and_the_shell_are_never_auth_gated(tmp_path):
    """Structural, per the adjudication: the service worker and the assets it
    caches must be reachable with no session at all — a dead session may not
    take the last edition away from a reader who already has it."""
    client = hosted_app.create_app(
        env_for(tmp_path, NEWSLENS_DEV_NO_AUTH=None)).test_client()
    for path in ("/login", "/sw.js", "/static/sw.js", "/static/shell.js",
                 "/static/tokens.css", "/static/manifest.webmanifest",
                 "/api/ping", "/healthz"):
        assert client.get(path).status_code == 200, path


def test_the_service_worker_is_served_at_the_root_with_its_scope(client):
    resp = client.get("/sw.js")
    assert resp.status_code == 200
    assert resp.headers["Service-Worker-Allowed"] == "/"
    assert "text/javascript" in resp.headers["Content-Type"]
    assert "newslens-v1" in resp.get_data(as_text=True)


def test_the_login_page_is_the_approved_masthead_as_drawn(client):
    page = client.get("/login").get_data(as_text=True)
    assert "Sign in to read the edition." in page
    assert "Accounts are created by the paper’s operator — there is no signup." in page
    assert "Continue with passkey" in page
    assert "Use a password instead" in page
    assert "You’ll stay signed in on this phone." in page
    # No self-serve signup, no email-reset theater: recovery runs through the
    # operator, said once.
    assert "Forgot password" not in page
    assert "Sign up" not in page


def test_an_account_with_no_paper_is_told_so_and_reads_nothing(tmp_path):
    """One account reads one paper (Q5). An unmapped account is an operator
    mistake — not a picker prompt, and not a silent empty page."""
    client = hosted_app.create_app(env_for(
        tmp_path, NEWSLENS_USER_STREAMS=json.dumps({"someone": "main"}),
        NEWSLENS_DEV_USER="unmapped")).test_client()
    resp = client.get("/")
    assert resp.status_code == 403
    assert "No paper on this account" in resp.get_data(as_text=True)


def test_two_accounts_are_two_papers(tmp_path):
    """Multi-user BY CONSTRUCTION, not by load: the map decides, and one
    reader can never see the other's edition."""
    env = env_for(tmp_path, NEWSLENS_USER_STREAMS=json.dumps(
        {"reader_a": "main", "reader_b": "second"}),
        NEWSLENS_PUSH_TOKENS=json.dumps(
            {"main": DIGEST,
             "second": hashlib.sha256(b"second-token").hexdigest()}))
    a = hosted_app.create_app(dict(env, NEWSLENS_DEV_USER="reader_a")).test_client()
    b = hosted_app.create_app(dict(env, NEWSLENS_DEV_USER="reader_b")).test_client()

    put(a, bundle())                                        # into `main`
    other = bundle(html=HTML.replace("the edition", "another paper"))
    put(b, other, stream="second", token="second-token")

    assert "another paper" not in a.get(f"/editions/{TODAY}").get_data(as_text=True)
    assert "another paper" in b.get(f"/editions/{TODAY}").get_data(as_text=True)


# ---------------------------------------------------------------------------
# THE BOOT REFUSAL — Rook's condition; violating it is a review BLOCK
# ---------------------------------------------------------------------------

def test_the_guard_is_inert_when_the_bypass_is_off():
    hosted_app.dev_bypass_guard({}, ["gunicorn", "-b", "0.0.0.0:8080"])
    hosted_app.dev_bypass_guard({"FLY_APP_NAME": "paper"}, [], host="0.0.0.0")


@pytest.mark.parametrize("loopback", ["127.0.0.1:8080", "127.0.0.1",
                                      "localhost:5473", "[::1]:8080"])
def test_the_bypass_runs_on_loopback(loopback):
    hosted_app.dev_bypass_guard({"NEWSLENS_DEV_NO_AUTH": "1"},
                                ["gunicorn", "-b", loopback])


@pytest.mark.parametrize("bind", ["0.0.0.0:8080", "0.0.0.0", "::",
                                  "192.168.1.10:8080", "fly-local-6pn:8080",
                                  "not-an-address"])
def test_the_bypass_refuses_any_bind_that_is_not_loopback(bind):
    """FAIL-CLOSED, including on a bind string it cannot parse: one puzzled
    minute is the cost of a false refusal; a paper on the internet with its
    door open is the cost of a false permit."""
    with pytest.raises(SystemExit) as exc:
        hosted_app.dev_bypass_guard({"NEWSLENS_DEV_NO_AUTH": "1"},
                                    ["gunicorn", "--bind", bind])
    assert "REFUSING TO BOOT" in str(exc.value)


def test_the_bypass_refuses_a_bind_from_the_environment():
    for var in ("NEWSLENS_BIND", "HOST", "GUNICORN_BIND"):
        with pytest.raises(SystemExit):
            hosted_app.dev_bypass_guard(
                {"NEWSLENS_DEV_NO_AUTH": "1", var: "0.0.0.0:8080"}, [])


@pytest.mark.parametrize("var", ["FLY_APP_NAME", "FLY_ALLOC_ID",
                                 "KUBERNETES_SERVICE_HOST", "DYNO"])
def test_the_bypass_refuses_a_platform_even_on_a_loopback_bind(var):
    """A bind we cannot see is not treated as safe. If the process is on a
    platform, the bypass is off — whatever the address says."""
    with pytest.raises(SystemExit) as exc:
        hosted_app.dev_bypass_guard({"NEWSLENS_DEV_NO_AUTH": "1", var: "x"},
                                    ["gunicorn", "-b", "127.0.0.1:8080"])
    assert "hosting platform" in str(exc.value)


def test_creating_the_app_on_a_public_host_with_the_bypass_dies(tmp_path):
    with pytest.raises(SystemExit):
        hosted_app.create_app(env_for(tmp_path), host="0.0.0.0")


def test_the_guard_actually_runs_at_import_in_a_real_process():
    """THE WIRING PROOF. Everything above tests a function; this proves the
    function is CALLED where it matters — a module import under gunicorn's own
    argv. Without this, the guard could be correct and never reached, which is
    exactly the shape a reviewer BLOCK is written about."""
    child = (
        "import sys, pathlib\n"
        "sys.argv = ['gunicorn', '--bind', '0.0.0.0:8080', 'hosted.app:app']\n"
        "import hosted.app as a\n"
        # off-tree discipline: the child must have imported THIS tree's copy
        f"assert pathlib.Path(a.__file__).resolve().is_relative_to("
        f"pathlib.Path({str(PROTOTYPE_ROOT)!r}).resolve()) if hasattr("
        "pathlib.Path, 'is_relative_to') else True\n"
        "print('BOOTED', a.__file__)\n")
    proc = subprocess.run(
        [sys.executable, "-c", child], cwd=str(PROTOTYPE_ROOT),
        capture_output=True, text=True, timeout=120,
        env=dict(os.environ, NEWSLENS_DEV_NO_AUTH="1",
                 PYTHONPATH=str(PROTOTYPE_ROOT)))
    assert proc.returncode != 0, (
        "the module imported cleanly with the bypass on a 0.0.0.0 bind — "
        f"stdout: {proc.stdout!r}")
    assert "BOOTED" not in proc.stdout
    assert "REFUSING TO BOOT" in (proc.stderr + proc.stdout)


def test_the_bypass_refuses_a_bind_hidden_in_gunicorn_cmd_args():
    """gunicorn reads its own flags out of `GUNICORN_CMD_ARGS` as well as argv,
    so a bare `gunicorn hosted.app:app` with that variable set is bound wherever
    the variable says — a channel the boot check could not see, walked as a real
    process pre-fix (exit 0, "BOOTED-THROUGH-THE-GAP")."""
    with pytest.raises(SystemExit) as exc:
        hosted_app.dev_bypass_guard(
            {"NEWSLENS_DEV_NO_AUTH": "1",
             "GUNICORN_CMD_ARGS": "--bind 0.0.0.0:8080"}, [])
    assert "REFUSING TO BOOT" in str(exc.value)
    # and the loopback spelling of the same variable still boots
    hosted_app.dev_bypass_guard(
        {"NEWSLENS_DEV_NO_AUTH": "1",
         "GUNICORN_CMD_ARGS": "--workers 2 --bind 127.0.0.1:8080"}, [])


def test_a_stranger_is_refused_even_when_the_boot_check_was_evaded(tmp_path):
    """THE CLASS-CLOSE. Enumerating bind channels loses the race by
    construction — `GUNICORN_CMD_ARGS` above was one, a `-c gunicorn.conf.py`
    holding `bind=` is another, and the next one is not written down yet. So
    the refusal also stands at the only moment that always exists: the request.
    With the bypass on, a non-loopback caller is answered 403 and served
    NOTHING — no page, no edition, no push."""
    app = hosted_app.create_app(env_for(tmp_path))
    client = app.test_client()
    stranger = {"REMOTE_ADDR": "203.0.113.9"}

    front = client.get("/", environ_overrides=stranger)
    assert front.status_code == 403
    assert "loopback-only" in front.get_json()["error"]

    pushed = client.put(f"/api/streams/main/editions/{TODAY}",
                        data=json.dumps(bundle()),
                        headers={"Authorization": f"Bearer {TOKEN}",
                                 "Content-Type": "application/json"},
                        environ_overrides=stranger)
    assert pushed.status_code == 403
    assert HostStore(tmp_path / "hostdata").dates("main") == [], (
        "a stranger's push reached the shelf")


def test_the_request_time_guard_leaves_the_loopback_reader_alone(client, tmp_path,
                                                                 monkeypatch):
    """The control, both directions.

    The guard refuses the ADDRESS, not the bypass: the local development loop
    the seam exists for is untouched — as are the other hosted nodes in this
    file, every one of which runs through it. And it is the BYPASS's backstop,
    not a new access rule: with the bypass off (M3's real auth) it is never
    registered at all, so a reverse proxy's forwarded requests are none of its
    business."""
    monkeypatch.setattr(hosted_app.Config, "today", lambda self: TODAY)
    assert put(client, bundle()).status_code == 200
    for addr in ("127.0.0.1", "127.0.0.53", "::1"):
        page = client.get("/", environ_overrides={"REMOTE_ADDR": addr})
        assert page.status_code == 200, f"{addr} was refused"
        assert "the edition" in page.get_data(as_text=True)

    off = hosted_app.create_app(env_for(tmp_path, NEWSLENS_DEV_NO_AUTH=None))
    resp = off.test_client().get("/", environ_overrides={"REMOTE_ADDR": "203.0.113.9"})
    assert resp.status_code == 302 and "/login" in resp.headers["Location"]


def test_the_same_import_succeeds_on_loopback():
    """The control: the refusal above is the bind's doing, not a broken child."""
    child = ("import sys\n"
             "sys.argv = ['gunicorn', '--bind', '127.0.0.1:8080', 'hosted.app:app']\n"
             "import hosted.app\n"
             "print('BOOTED')\n")
    proc = subprocess.run(
        [sys.executable, "-c", child], cwd=str(PROTOTYPE_ROOT),
        capture_output=True, text=True, timeout=120,
        env=dict(os.environ, NEWSLENS_DEV_NO_AUTH="1",
                 PYTHONPATH=str(PROTOTYPE_ROOT)))
    assert proc.returncode == 0, proc.stderr
    assert "BOOTED" in proc.stdout


# ---------------------------------------------------------------------------
# THE STORE — the rules that hold whether or not a request is in flight
# ---------------------------------------------------------------------------

def test_the_latest_pointer_only_moves_forward(store):
    """A re-push of an older date (the retry path) must not make yesterday the
    paper of record."""
    store.write_edition("main", TODAY, json.dumps(bundle()).encode())
    store.set_latest("main", TODAY)
    store.write_edition("main", YESTERDAY, json.dumps(bundle(YESTERDAY)).encode())
    store.set_latest("main", YESTERDAY)
    assert store.latest("main") == TODAY


def test_a_pointer_to_a_missing_edition_falls_back_to_what_is_there(store):
    """A half-restored volume must not serve a 404 as the front page."""
    store.write_edition("main", YESTERDAY, json.dumps(bundle(YESTERDAY)).encode())
    (store.stream_dir("main") / "latest").write_text(TODAY, encoding="utf-8")
    assert store.latest("main") == YESTERDAY


def test_a_torn_write_leaves_no_litter_beside_the_editions(store, monkeypatch):
    import os as _os
    real_replace = _os.replace

    def boom(src, dst):
        raise OSError("no space left on device")
    monkeypatch.setattr(_os, "replace", boom)
    with pytest.raises(OSError):
        store.write_edition("main", TODAY, b"{}")
    monkeypatch.setattr(_os, "replace", real_replace)
    assert list(store.stream_dir("main").glob("*.tmp")) == []
    assert store.dates("main") == []


@pytest.mark.parametrize("stream", ["../evil", "Main", "a" * 40, "", "main/x"])
def test_a_stream_name_that_is_not_a_slug_never_becomes_a_path(store, stream):
    from hosted.hoststore import StoreError
    with pytest.raises(StoreError):
        store.stream_dir(stream)


def test_the_archive_is_exactly_the_bundles_on_the_shelf(store):
    store.write_edition("main", TODAY, json.dumps(bundle()).encode())
    (store.stream_dir("main") / "notes.txt").write_text("x", encoding="utf-8")
    (store.stream_dir("main") / "2026-13-99.json").write_text("{}", encoding="utf-8")
    # A LIVE tmp from a write in flight beside us: the per-writer scratch name
    # must never read as an edition (it is why the name ends `.tmp`, not
    # `.json`), or a concurrent push would flicker into somebody's archive.
    (store.stream_dir("main") / ".2026-08-30.json.9999-deadbeef.tmp").write_text(
        "half a document", encoding="utf-8")
    assert store.dates("main") == [TODAY]


def test_two_writers_in_the_same_window_both_publish_a_whole_document(store,
                                                                      monkeypatch):
    """THE CONCURRENCY LAW, deterministically.

    Writer B runs to completion from INSIDE writer A's fsync/replace window —
    the interleaving a shared tmp filename turns into a truncated inode: B's
    `open(tmp,"wb")` empties the file A just fsynced, B's replace publishes it
    and B's unlink removes it, and A's own replace then raises
    FileNotFoundError → 500. Measured on a real wire at 16, 17 and 20 server
    errors per 48 concurrent same-date pushes by three separate hands; pinned
    here deterministically, because a thread race that fails one run in five is
    a test that goes green the day the defect comes back.

    The law: both writers publish a COMPLETE document, and the last replace
    wins. Nobody publishes a fragment and nobody dies."""
    import os as _os
    real_fsync = _os.fsync
    a_bytes = json.dumps(bundle()).encode()
    b_bytes = json.dumps(bundle(html=HTML.replace("the edition",
                                                  "the other edition"))).encode()
    state = {"fired": False, "b_error": None}

    def fsync_hook(fd):
        real_fsync(fd)
        if state["fired"]:
            return                      # only writer A's window is hooked
        state["fired"] = True
        try:
            store.write_edition("main", TODAY, b_bytes)
        except Exception as exc:        # noqa: BLE001 — recorded, then asserted
            state["b_error"] = exc

    monkeypatch.setattr(_os, "fsync", fsync_hook)
    store.write_edition("main", TODAY, a_bytes)          # A must not raise
    monkeypatch.setattr(_os, "fsync", real_fsync)

    assert state["fired"], "the hook never ran — the write path changed shape"
    assert state["b_error"] is None, (
        f"writer B died inside writer A's window: {state['b_error']!r}")
    landed = store.read_edition("main", TODAY)
    assert landed is not None, "two writers ran and the shelf is empty"
    assert hashlib.sha256(landed["html"].encode("utf-8")).hexdigest() == (
        landed["content_sha256"]), "a reader could catch a half-written document"
    assert list(store.stream_dir("main").glob("*.tmp")) == [], "tmp litter"


def test_two_pointer_writers_in_the_same_window_both_succeed(store, monkeypatch):
    """The same law at the OTHER shared-tmp site (`set_latest`). Two workers
    landing the same morning's edition both move the pointer; on a shared
    `latest.tmp` the second one to arrive deletes the first one's file out from
    under it and the first raises. The pointer must end up valid either way."""
    import os as _os
    real_replace = _os.replace
    store.write_edition("main", TODAY, json.dumps(bundle()).encode())
    state = {"fired": False, "b_error": None}

    def replace_hook(src, dst):
        if not state["fired"]:
            state["fired"] = True       # only writer A's window is hooked
            try:
                store.set_latest("main", TODAY)
            except Exception as exc:    # noqa: BLE001 — recorded, then asserted
                state["b_error"] = exc
        return real_replace(src, dst)

    monkeypatch.setattr(_os, "replace", replace_hook)
    store.set_latest("main", TODAY)                      # A must not raise
    monkeypatch.setattr(_os, "replace", real_replace)

    assert state["fired"], "the hook never ran — the pointer write changed shape"
    assert state["b_error"] is None, (
        f"pointer writer B died inside writer A's window: {state['b_error']!r}")
    assert store.latest("main") == TODAY
    assert list(store.stream_dir("main").glob("*.tmp")) == [], "tmp litter"
