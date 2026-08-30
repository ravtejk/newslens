"""NL-163 Stage-A M2 — THE MAC'S HALF: delivery, instruments, generated assets.

Binding sources: the engineering adjudication 2026-08-29 §Q2 (the tokens/login
palette is GENERATED from webui.TOKENS and drift-pinned here) and §Q4 (push
transport, idempotence, containment, the retry verb); the M1 gate's rider R-A
(the doctor compares artifact presence against the log's dates) and its FIX-2
note that M2's shell INHERITS the bundle's boot script; DECISIONS 2026-08-29
(icon C, the evening plate).

WHAT THIS FILE PINS, and why each pin exists

  * THE PUSH NEVER COSTS AN EDITION. The mount runs after publish, after the
    log, after the mint; a dead host, a wrong token and a raising client each
    cost one warning line and nothing else. Executed against the REAL mount
    block, not a paraphrase of it (the M1 extraction, reused).
  * THE TOKEN DOES NOT LEAK. Not into the state file, not into a warning, not
    into the doctor's output. A secret that appears in a log is a secret that
    appears in a screenshot.
  * REFUSALS ARE NOT RETRIED. Three copies of "bad token" is not resilience.
  * THE PALETTE IS CONSUMED, NEVER COPIED. tokens.css, the manifest colours,
    the icon's three inks and the shell's boot script are all generated from
    webui; drift on disk is a red test, and shell.css declaring a hex of its
    own is a red test.
  * THE ICON IS THE PRINCIPAL'S PICK, MEASURABLY. Icon C's ground is ink, its
    letter is paper, its rule is terra — read out of the PNG — and the
    maskable drawing stays inside the safe circle a round mask leaves.

Hermetic and $0: no socket is opened (the transport is exercised through an
injected `urlopen`), no model is called, and every path is a sandbox.
"""
from __future__ import annotations

import ast
import hashlib
import io
import json
import re
import struct
import urllib.error
import zlib
from pathlib import Path

import pytest

from conftest import PROTOTYPE_ROOT
from newslens import doctor, editionbundle, hostedassets, pushclient, webui
from test_nl163_bundle import DATE, ENTRY, GEN_AT, SLOTS, _NoPush, _mount_block

STREAM_URL = "https://paper.example.test/api/streams/main"
TOKEN = "a-token-that-must-never-be-logged"


@pytest.fixture
def con():
    """The M1 fixture world: one published edition with the memory its
    furniture reads. Reused rather than re-declared — the mount block these
    tests execute is the same block, and it must run against the same world."""
    from newslens import db
    from test_nl163_bundle import _world
    db.migrate()
    c = db.connect()
    _world(c)
    yield c
    c.close()


def _bundle(date=DATE, html="<html><body>edition</body></html>"):
    return {
        "bundle_version": 1, "edition_date": date, "generated_at": GEN_AT,
        "variant": "A",
        "content_sha256": hashlib.sha256(html.encode("utf-8")).hexdigest(),
        "meta": {"story_count": 3}, "audio": None, "html": html,
    }


class _Resp:
    """A urlopen context manager, as urllib hands one back."""

    def __init__(self, status=200, payload=None):
        self.status = status
        self._raw = json.dumps(payload or {"status": "stored"}).encode("utf-8")

    def read(self):
        return self._raw

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _urlopen_returning(*responses, record=None):
    """An injected transport. Each call pops the next scripted answer, so a
    retry test can script `500, 500, 200` and count what actually happened."""
    queue = list(responses)

    def fake(request, timeout=None):
        if record is not None:
            record.append(request)
        # The LAST scripted answer repeats: one URLError means a host that is
        # down, not a host that is down once and then quietly recovers. (A
        # default success here made a containment test pass for the wrong
        # reason — caught, and the harness fixed rather than the assert.)
        answer = queue.pop(0) if len(queue) > 1 else queue[0]
        if isinstance(answer, Exception):
            raise answer
        return answer
    return fake


# ---------------------------------------------------------------------------
# THE ENDPOINT — a URL that is not a stream is refused before anything is sent
# ---------------------------------------------------------------------------

def test_the_endpoint_names_the_stream_and_the_date():
    assert pushclient.endpoint(STREAM_URL, DATE) == (
        f"{STREAM_URL}/editions/{DATE}")
    assert pushclient.endpoint(STREAM_URL + "/", DATE).endswith(
        f"/api/streams/main/editions/{DATE}")


@pytest.mark.parametrize("bad", [
    "https://paper.example.test",                 # no stream at all
    "https://paper.example.test/api/streams",     # the collection, not a stream
    "https://paper.example.test/api/streams/main/editions/2026-08-29",
    "ftp://paper.example.test/api/streams/main",
    "",
])
def test_a_url_that_is_not_a_stream_endpoint_is_refused_loudly(bad):
    """Silence here would mean a PUT into an address that 404s every morning,
    and the operator learning about it from an empty phone."""
    with pytest.raises(pushclient.PushError) as exc:
        pushclient.endpoint(bad, DATE)
    assert "api/streams" in str(exc.value)


def test_a_date_shaped_nothing_never_reaches_the_wire():
    for bad in ("2026-8-2", "../../etc/passwd", "latest", ""):
        with pytest.raises(pushclient.PushError):
            pushclient.endpoint(STREAM_URL, bad)


# ---------------------------------------------------------------------------
# THE REQUEST
# ---------------------------------------------------------------------------

def test_a_push_sends_the_bundle_verbatim_under_a_bearer_token(monkeypatch):
    sent = []
    monkeypatch.setattr(pushclient.urllib.request, "urlopen",
                        _urlopen_returning(_Resp(200, {"status": "stored"}),
                                           record=sent))
    result = pushclient.push_bundle(_bundle(), url=STREAM_URL, token=TOKEN)
    assert result.ok and result.detail == "stored"
    request = sent[0]
    assert request.get_method() == "PUT"
    assert request.full_url == f"{STREAM_URL}/editions/{DATE}"
    assert request.headers["Authorization"] == f"Bearer {TOKEN}"
    # The document that arrives is the document that was frozen: same bytes,
    # same digest, no re-serialisation of the html.
    assert json.loads(request.data.decode("utf-8")) == _bundle()


def test_a_refusal_is_reported_once_and_never_retried(monkeypatch):
    """A 4xx is the host telling us something TRUE about this request. Sending
    it twice more would just be two more copies of the same wrong."""
    calls = []
    err = urllib.error.HTTPError(
        f"{STREAM_URL}/editions/{DATE}", 401, "Unauthorized", {},
        io.BytesIO(json.dumps({"error": "token not recognised"}).encode()))
    monkeypatch.setattr(pushclient.urllib.request, "urlopen",
                        _urlopen_returning(err, record=calls))
    result = pushclient.push_bundle(_bundle(), url=STREAM_URL, token=TOKEN,
                                    sleep=lambda s: None)
    assert not result.ok
    assert result.status == 401
    assert result.detail == "token not recognised"
    assert result.attempts == 1, "a refusal must not be retried"
    assert len(calls) == 1


def test_a_dead_host_is_retried_with_backoff_and_then_gives_up(monkeypatch):
    calls, slept = [], []
    monkeypatch.setattr(
        pushclient.urllib.request, "urlopen",
        _urlopen_returning(urllib.error.URLError("connection refused"),
                           urllib.error.URLError("connection refused"),
                           urllib.error.URLError("connection refused"),
                           record=calls))
    result = pushclient.push_bundle(_bundle(), url=STREAM_URL, token=TOKEN,
                                    sleep=slept.append)
    assert not result.ok and result.status == 0
    assert result.attempts == 3 and len(calls) == 3
    assert slept == list(pushclient.BACKOFF_SECONDS)


def test_a_server_error_is_retried_and_a_later_attempt_can_succeed(monkeypatch):
    err = urllib.error.HTTPError(STREAM_URL, 502, "Bad Gateway", {},
                                 io.BytesIO(b""))
    monkeypatch.setattr(pushclient.urllib.request, "urlopen",
                        _urlopen_returning(err, _Resp(200, {"status": "stored"})))
    result = pushclient.push_bundle(_bundle(), url=STREAM_URL, token=TOKEN,
                                    sleep=lambda s: None)
    assert result.ok and result.attempts == 2


# ---------------------------------------------------------------------------
# THE LOCAL RECORD — what the doctor can stat, and what must never be in it
# ---------------------------------------------------------------------------

def test_the_record_keeps_the_last_success_and_the_last_attempt_apart():
    """A week of failures must not erase the memory of when the paper last
    landed — the two facts answer two different questions."""
    ok = pushclient.PushResult(True, 200, "stored",
                               payload={"pushed_at": "2026-08-29T06:02:11Z"})
    pushclient.record(DATE, ok, STREAM_URL)
    bad = pushclient.PushResult(False, 0, "URLError: connection refused")
    pushclient.record("2026-08-30", bad, STREAM_URL)

    state = pushclient.read_state()
    assert state["last_success"]["date"] == DATE
    assert state["last_success"]["host_pushed_at"] == "2026-08-29T06:02:11Z"
    assert state["last_attempt"]["date"] == "2026-08-30"
    assert state["last_attempt"]["status"] == "failed"


def test_the_token_never_reaches_the_state_file():
    """THE LEAK TOOTH. The state file is written on every push, read by the
    doctor, and printed in whole by anyone debugging at 6am."""
    pushclient.record(DATE, pushclient.PushResult(True, 200, "stored"),
                      f"https://x:{TOKEN}@paper.example.test/api/streams/main")
    raw = pushclient.state_path().read_text(encoding="utf-8")
    assert TOKEN not in raw
    assert "/api/streams" not in raw          # not even the path is kept


# ---------------------------------------------------------------------------
# THE SEAM — push_after_publish is the only thing the generate calls
# ---------------------------------------------------------------------------

def test_an_unconfigured_mac_says_nothing(monkeypatch):
    """No host is a legitimate state, not a warning: the edition is still
    frozen and still readable here. The doctor is where that fact belongs."""
    assert pushclient.push_after_publish(DATE, env={}) is None


def test_a_failed_delivery_warns_in_the_run_and_names_the_retry(monkeypatch):
    monkeypatch.setattr(
        pushclient.urllib.request, "urlopen",
        _urlopen_returning(urllib.error.URLError("connection refused")))
    editionbundle.write_bundle(_bundle())
    note = pushclient.push_after_publish(
        DATE, env={"NEWSLENS_PUSH_URL": STREAM_URL,
                   "NEWSLENS_PUSH_TOKEN": TOKEN}, sleep=lambda s: None)
    assert note is not None
    assert "PUBLISHED and unaffected" in note
    assert f"newslens push --date {DATE}" in note
    assert TOKEN not in note                  # the leak tooth, at the surface


def test_a_missing_artifact_warns_instead_of_raising():
    """`push_after_publish` NEVER raises — the mount's containment is the belt,
    this is the braces, and a generate must survive both."""
    note = pushclient.push_after_publish(
        "2011-01-01", env={"NEWSLENS_PUSH_URL": STREAM_URL,
                           "NEWSLENS_PUSH_TOKEN": TOKEN})
    assert note and "NOT delivered" in note and "PUBLISHED and unaffected" in note


def test_a_successful_delivery_is_quiet(monkeypatch):
    monkeypatch.setattr(pushclient.urllib.request, "urlopen",
                        _urlopen_returning(_Resp(200, {"status": "stored"})))
    editionbundle.write_bundle(_bundle())
    assert pushclient.push_after_publish(
        DATE, env={"NEWSLENS_PUSH_URL": STREAM_URL,
                   "NEWSLENS_PUSH_TOKEN": TOKEN}) is None
    assert pushclient.read_state()["last_success"]["date"] == DATE


# ---------------------------------------------------------------------------
# THE MOUNT — executed, not paraphrased (the M1 extraction, second half)
# ---------------------------------------------------------------------------

def _run_mount(report, con, *, pushclient_stub):
    exec(compile(_mount_block(), "<mount>", "exec"),
         {"report": report, "con": con, "date": DATE, "log_entry": ENTRY,
          "editionbundle": editionbundle, "pushclient": pushclient_stub,
          "__name__": "mount"})


def test_a_sample_never_pushes(con):
    """Samples never mint, so there is nothing to deliver — and a phone that
    could show a comparison variant as the morning's edition would be showing
    something that was never published."""
    from newslens import generate

    calls = []

    class _Counting:
        @staticmethod
        def push_after_publish(date, **kw):
            calls.append(date)
            return None

    _run_mount(generate.GenReport(date=DATE, variant="A", sample=True), con,
               pushclient_stub=_Counting)
    assert calls == [], f"a sample reached the push ({len(calls)} call(s))"

    # The control proves the harness could have pushed: same block, same stub,
    # sample off.
    _run_mount(generate.GenReport(date=DATE, variant="A", sample=False), con,
               pushclient_stub=_Counting)
    assert calls == [DATE], "the control must prove the mount can push"


def test_a_failed_mint_means_no_push_attempt(con, monkeypatch):
    """`if report.bundle_path:` — there is no artifact to deliver, and the
    push must not invent one from a live world."""
    from newslens import generate

    calls = []

    class _Counting:
        @staticmethod
        def push_after_publish(date, **kw):
            calls.append(date)
            return None

    monkeypatch.setattr(editionbundle, "mint",
                        lambda *a, **kw: (_ for _ in ()).throw(OSError("disk full")))
    report = generate.GenReport(date=DATE, variant="A", sample=False)
    _run_mount(report, con, pushclient_stub=_Counting)
    assert report.bundle_path == ""
    assert calls == []
    assert len(report.warnings) == 1 and "phone bundle" in report.warnings[0]


def test_a_raising_push_client_costs_a_warning_and_never_the_edition(con):
    """CONTAINMENT, MEASURED. By the time control reaches here the edition is
    published, persisted, logged and frozen. Anything the delivery does after
    that point is a convenience failing, and crashing would trade a published
    edition for one."""
    from newslens import generate, server

    class _Exploding:
        @staticmethod
        def push_after_publish(date, **kw):
            raise RuntimeError("the network stack fell over")

    report = generate.GenReport(date=DATE, variant="A", sample=False)
    _run_mount(report, con, pushclient_stub=_Exploding)

    assert report.bundle_path.endswith(f"{DATE}.phone.json")
    warn = [w for w in report.warnings if "phone push" in w]
    assert len(warn) == 1
    assert "the network stack fell over" in warn[0]
    assert "PUBLISHED and unaffected" in warn[0]
    assert f"newslens push --date {DATE}" in warn[0]
    assert server._briefing_row(con, DATE) is not None
    assert editionbundle.load(DATE)["edition_date"] == DATE


def test_a_quiet_push_leaves_the_run_output_unchanged(con):
    from newslens import generate
    report = generate.GenReport(date=DATE, variant="A", sample=False)
    _run_mount(report, con, pushclient_stub=_NoPush)
    assert report.warnings == []
    assert report.bundle_path.endswith(f"{DATE}.phone.json")


# ---------------------------------------------------------------------------
# THE DOCTOR — rider R-A (artifact presence vs the log) and push age
# ---------------------------------------------------------------------------

def _log(rows):
    from newslens import generate, paths
    path = generate.log_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    assert path.exists() and paths.DATA_DIR in path.parents


def _texts(results):
    return " || ".join(r.text for r in results)


def test_the_doctor_says_nothing_is_frozen_yet_without_crying_hole():
    _log([{"date": "2026-08-01", "status": "ok"},
          {"date": "2026-08-02", "status": "ok"}])
    results = doctor.check_phone_delivery({})
    assert not any(r.status == doctor.WARN for r in results)
    assert "NOT reconstructed" in _texts(results)


def test_the_doctor_finds_the_edition_that_never_froze():
    """RIDER R-A. A mint failure warns at generate time and the log entry is
    already written when it does — so the record does NOT carry it, and this
    comparison is the only durable detector."""
    _log([{"date": "2026-08-02", "status": "ok"},
          {"date": "2026-08-03", "status": "ok"},
          {"date": "2026-08-04", "status": "ok"}])
    editionbundle.write_bundle(_bundle(date="2026-08-02"))
    editionbundle.write_bundle(_bundle(date="2026-08-04"))

    results = doctor.check_phone_delivery({})
    text = _texts(results)
    assert any(r.status == doctor.WARN for r in results)
    assert "(2026-08-03)" in text, text        # exactly the one that is gone


def test_the_doctor_ignores_samples_and_failures_and_the_pre_bundle_era():
    """Three exclusions, each with a reason: a sample never mints, a failed run
    published nothing, and an edition older than the first artifact belongs to
    the honest pre-NL-163 archive."""
    _log([{"date": "2026-07-01", "status": "ok"},                    # pre-era
          {"date": "2026-08-02", "status": "ok"},
          {"date": "2026-08-03", "status": "ok", "sample": True},    # sample
          {"date": "2026-08-04", "status": "failed"}])               # failed
    editionbundle.write_bundle(_bundle(date="2026-08-02"))
    results = doctor.check_phone_delivery({})
    assert not any(r.status == doctor.WARN for r in results), _texts(results)


def test_the_doctor_reports_an_unconfigured_push_as_information():
    _log([{"date": DATE, "status": "ok"}])
    editionbundle.write_bundle(_bundle())
    results = doctor.check_phone_delivery({})
    push = [r for r in results if "push" in r.text]
    assert push and push[0].status == doctor.INFO
    assert "delivered nowhere" in push[0].text


def test_the_doctor_fails_a_half_configured_push():
    results = doctor.check_phone_delivery({"NEWSLENS_PUSH_URL": STREAM_URL})
    assert any(r.status == doctor.FAIL for r in results)
    assert "NEWSLENS_PUSH_TOKEN" in _texts(results)


def test_the_doctor_refuses_a_url_that_is_not_a_stream_endpoint():
    results = doctor.check_phone_delivery(
        {"NEWSLENS_PUSH_URL": "https://paper.example.test",
         "NEWSLENS_PUSH_TOKEN": TOKEN})
    assert any(r.status == doctor.FAIL for r in results)


def test_the_doctor_names_the_undelivered_edition():
    _log([{"date": DATE, "status": "ok"}])
    editionbundle.write_bundle(_bundle())
    pushclient.record("2026-08-01",
                      pushclient.PushResult(True, 200, "stored",
                                            payload={"pushed_at": "2026-08-01T06:00:00Z"}),
                      STREAM_URL)
    results = doctor.check_phone_delivery(
        {"NEWSLENS_PUSH_URL": STREAM_URL, "NEWSLENS_PUSH_TOKEN": TOKEN})
    text = _texts(results)
    assert any(r.status == doctor.WARN for r in results)
    assert "has NOT been delivered" in text and DATE in text
    assert TOKEN not in text                  # the leak tooth, in the doctor


def test_the_doctor_never_claims_the_host_is_alive():
    """THE HONESTY BOUND. This check makes no request; it reads a local record
    of pushes this machine made. It may say what landed and when — never that
    the host is up, and never that it still holds anything."""
    _log([{"date": DATE, "status": "ok"}])
    editionbundle.write_bundle(_bundle())
    pushclient.record(DATE, pushclient.PushResult(
        True, 200, "stored", payload={"pushed_at": "2026-08-29T06:02:11Z"}),
        STREAM_URL)
    results = doctor.check_phone_delivery(
        {"NEWSLENS_PUSH_URL": STREAM_URL, "NEWSLENS_PUSH_TOKEN": TOKEN})
    text = _texts(results)
    assert "not a probe" in text
    assert "last delivery recorded: " + DATE in text
    assert not any(r.status == doctor.WARN for r in results), text


def test_the_shared_log_reader_survives_a_torn_line():
    """The record is append-only and a crash can interrupt a line mid-write.
    Both readers of it (arc continuity, this census) must count the tear, not
    die of it — the doctor builds every section before printing one."""
    from newslens import generate
    generate.log_file().parent.mkdir(parents=True, exist_ok=True)
    generate.log_file().write_text(
        json.dumps({"date": DATE, "status": "ok"}) + "\n{\"date\": \"2026-08-",
        encoding="utf-8")
    rows, skipped, unreadable, segments = doctor._log_rows()
    assert [r["date"] for r in rows] == [DATE]
    assert skipped == 1 and unreadable is None and segments == 1
    assert any("unparseable" in r.text for r in doctor.check_phone_delivery({}))


# ---------------------------------------------------------------------------
# THE GENERATED ASSETS — consumption, never a copy (§Q2)
# ---------------------------------------------------------------------------

def test_every_generated_asset_on_disk_is_current():
    """THE DRIFT PIN. Re-pin a token in webui.py and this reds until
    `scripts/gen-hosted-assets` has run — which is the whole reason the hosted
    service is allowed to have a stylesheet at all."""
    stale = hostedassets.stale_assets()
    assert stale == [], (
        "hosted/static is stale — run scripts/gen-hosted-assets: " + str(stale))


def test_tokens_css_is_webui_and_says_so():
    css = (PROTOTYPE_ROOT / "hosted/static/tokens.css").read_text(encoding="utf-8")
    assert hostedassets.GENERATED_MARKER in css
    assert webui.TOKENS.strip() in css
    assert "body.dark {" in css
    # Every colour in the generated file exists in webui's own stylesheet.
    for hexval in set(re.findall(r"#[0-9A-Fa-f]{3,8}\b", css)):
        assert hexval in webui.CSS, f"{hexval} is not webui's"


def test_the_hosted_shell_stylesheet_declares_no_colour_of_its_own():
    """The authored half of the hosted CSS may lay things out; it may not
    invent a colour. Two files declaring the same hexes is how they drift, and
    tokens.css is the one that is allowed to hold them."""
    css = (PROTOTYPE_ROOT / "hosted/static/shell.css").read_text(encoding="utf-8")
    assert not re.findall(r"#[0-9A-Fa-f]{3,8}\b", css)
    assert not re.findall(r"\brgba?\(", css)


def test_the_shell_never_paints_with_the_token_that_does_not_invert():
    """A REAL DEFECT, caught in the browser and pinned here.

    `--bg: var(--paper)` is declared once, on `:root`, so it resolves there —
    and `body.dark` redefines `--paper` WITHOUT redefining `--bg`. Anything
    painted with `var(--bg)` therefore keeps the light value in dark mode: the
    ink CTA rendered paper-on-paper, an invisible label on a visible block
    (measured at 390px, dark, states 3 and 404).

    The token itself is the shipped Mac surface's (webui.py:40, used at :460,
    :615, :621, :653) and is deliberately NOT changed by this milestone — that
    is his stylesheet and §10 leaves the designed dark register open. What is
    enforced here is that the hosted shell never reaches for it."""
    css = (PROTOTYPE_ROOT / "hosted/static/shell.css").read_text(encoding="utf-8")
    assert "var(--bg)" not in css, (
        "shell.css paints with --bg, which does not invert under body.dark — "
        "use --paper (see the comment on .cta-quiet)")


def test_the_shell_inherits_the_bundles_own_boot_script():
    """M1 gate FIX-2's landing note: the shell inherits BOOT_JS wholesale — so
    the touch-gated Dynamic Type seed and the three-state dark rule cannot be
    right on one surface and wrong on the other."""
    boot = (PROTOTYPE_ROOT / "hosted/static/boot.js").read_text(encoding="utf-8")
    assert editionbundle.BOOT_JS.strip() in boot
    assert "navigator.maxTouchPoints > 1" in boot
    assert "newslens-theme" in boot


def test_the_manifest_is_the_paper_and_takes_its_colours_from_the_palette():
    manifest = json.loads(
        (PROTOTYPE_ROOT / "hosted/static/manifest.webmanifest").read_text("utf-8"))
    assert manifest["name"] == "NewsLens"
    assert manifest["display"] == "standalone"
    assert manifest["background_color"] == hostedassets.token("--paper")
    assert manifest["theme_color"] == hostedassets.token("--paper")
    purposes = {i["purpose"] for i in manifest["icons"]}
    assert purposes == {"any", "maskable"}
    for icon in manifest["icons"]:
        assert (PROTOTYPE_ROOT / "hosted" / icon["src"].lstrip("/")).exists()


# ---- the icon, measured out of its own pixels -----------------------------

def _read_png(path: Path):
    """(size, pixels) for the 8-bit RGB, filter-0 PNGs this generator writes."""
    raw = path.read_bytes()
    assert raw[:8] == b"\x89PNG\r\n\x1a\n"
    pos, width, height, idat = 8, None, None, b""
    while pos < len(raw):
        length = struct.unpack(">I", raw[pos:pos + 4])[0]
        tag = raw[pos + 4:pos + 8]
        data = raw[pos + 8:pos + 8 + length]
        if tag == b"IHDR":
            width, height, depth, colour = struct.unpack(">IIBB", data[:10])
            assert (depth, colour) == (8, 2)
        elif tag == b"IDAT":
            idat += data
        pos += 12 + length
    assert width == height
    flat = zlib.decompress(idat)
    stride = width * 3 + 1
    pixels = {}
    for y in range(height):
        row = flat[y * stride:(y + 1) * stride]
        assert row[0] == 0, "filter 0 only"
        for x in range(width):
            pixels[(x, y)] = tuple(row[1 + x * 3:4 + x * 3])
    return width, pixels


def _hex(value):
    v = value.lstrip("#")
    return (int(v[0:2], 16), int(v[2:4], 16), int(v[4:6], 16))


@pytest.mark.parametrize("name,size", [
    ("icon-192.png", 192), ("icon-512.png", 512),
    ("icon-maskable-192.png", 192), ("icon-maskable-512.png", 512),
    ("apple-touch-icon.png", 180), ("favicon.png", 48)])
def test_icon_c_is_the_evening_plate_at_every_size(name, size):
    """HIS PICK, MEASURED (DECISIONS 2026-08-29: icon C, the evening plate).
    Ink ground, paper letter, terra rule — read out of the file, not asserted
    about the code that wrote it."""
    width, pixels = _read_png(PROTOTYPE_ROOT / "hosted/static" / name)
    assert width == size
    ink, paper, terra = (_hex(hostedassets.token(t))
                         for t in ("--ink", "--paper", "--terra"))
    assert pixels[(0, 0)] == ink and pixels[(size - 1, size - 1)] == ink
    values = set(pixels.values())
    assert paper in values, "the letter is missing"
    assert terra in values, "the terra rule is missing"
    # The letter sits ABOVE its rule — the plate, not a random arrangement.
    letter_y = [y for (x, y), v in pixels.items() if v == paper]
    rule_y = [y for (x, y), v in pixels.items() if v == terra]
    assert max(letter_y) < min(rule_y)


@pytest.mark.parametrize("name,size", [("icon-maskable-192.png", 192),
                                       ("icon-maskable-512.png", 512)])
def test_the_maskable_icon_survives_a_round_mask(name, size):
    """A maskable icon may be cropped to a circle of 80% diameter. Anything
    outside that circle is a mark that gets beheaded on somebody's home
    screen — so the drawing must fit inside it, with the ground bleeding to
    the edges to fill whatever shape the OS chooses."""
    _, pixels = _read_png(PROTOTYPE_ROOT / "hosted/static" / name)
    ink = _hex(hostedassets.token("--ink"))
    centre = (size - 1) / 2.0
    safe = 0.40 * size                       # radius of the 80%-diameter circle
    worst = max((((x - centre) ** 2 + (y - centre) ** 2) ** 0.5
                 for (x, y), v in pixels.items() if v != ink), default=0)
    assert worst <= safe, (f"{name}: drawing reaches {worst:.1f}px from centre, "
                           f"outside the {safe:.1f}px safe radius")


def test_the_plain_icons_use_more_of_the_tile_than_the_maskable_ones():
    """The maskable drawing is a DIFFERENT drawing, not the same file
    relabelled — if these two ever converge, one of them is wrong."""
    def reach(name):
        size, pixels = _read_png(PROTOTYPE_ROOT / "hosted/static" / name)
        ink, centre = _hex(hostedassets.token("--ink")), (size - 1) / 2.0
        return max((((x - centre) ** 2 + (y - centre) ** 2) ** 0.5
                    for (x, y), v in pixels.items() if v != ink)) / size
    assert reach("icon-512.png") > reach("icon-maskable-512.png") * 1.15


# ---------------------------------------------------------------------------
# THE HOSTED TREE, read without importing it — so this file runs everywhere
# ---------------------------------------------------------------------------

def test_the_hosted_service_still_publishes_every_route_it_promises():
    """A STRUCTURAL PIN, by AST rather than import: it holds on a machine with
    no Flask installed, where the hosted suite skips. It cannot prove a route
    WORKS — test_nl163_hosted.py does that — but it does catch the route that
    quietly stopped existing."""
    tree = ast.parse((PROTOTYPE_ROOT / "hosted/app.py").read_text("utf-8"))
    routes = set()
    for node in ast.walk(tree):
        for dec in getattr(node, "decorator_list", []):
            if (isinstance(dec, ast.Call) and getattr(dec.func, "attr", "") == "route"
                    and dec.args and isinstance(dec.args[0], ast.Constant)):
                routes.add(dec.args[0].value)
    assert routes >= {"/", "/editions/<date>", "/archive", "/login",
                      "/api/streams/<stream>/editions/<date>",
                      "/api/ping", "/healthz", "/sw.js"}


def test_the_hosted_dependency_story_is_one_story():
    """flask + gunicorn are M2's; PyJWT + cryptography are M3's and are listed
    early ON PURPOSE, so the reviewer reads one dependency story instead of
    meeting the second half at the lock."""
    reqs = (PROTOTYPE_ROOT / "hosted/requirements.txt").read_text("utf-8").lower()
    for dep in ("flask", "gunicorn", "pyjwt", "cryptography"):
        assert re.search(rf"^{dep}[><=]", reqs, re.M), f"{dep} unlisted"
    pyproject = (PROTOTYPE_ROOT / "pyproject.toml").read_text("utf-8")
    assert "flask" in pyproject, (
        "flask is a DEV dependency of this repo now — the hosted suite runs "
        "against it here, and an undeclared dev dependency is a suite that "
        "silently skips on the next machine")


def test_the_deployable_carries_its_own_runbook_and_never_a_secret():
    hosted = PROTOTYPE_ROOT / "hosted"
    for name in ("Dockerfile", "fly.toml", "DEPLOY.md", "requirements.txt",
                 "app.py", "hoststore.py"):
        assert (hosted / name).exists(), f"hosted/{name} is missing"
    # Nothing in the deployable may carry a credential: the remote is public.
    for path in hosted.rglob("*"):
        if path.is_file() and path.suffix in (".py", ".toml", ".md", ".txt",
                                              ".html", ".js", ".css"):
            text = path.read_text(encoding="utf-8", errors="replace")
            assert not re.search(r"\b(sk|pplx|gsk)-[A-Za-z0-9_\-]{12,}", text)
            assert "BEGIN PRIVATE KEY" not in text


# ---------------------------------------------------------------------------
# THE SERVICE WORKER'S POLICY — a structural floor over sw.js's own bytes.
#
# QA-authored tooth, adopted by the gate at its proven bytes (NL-163 M2 gate;
# the NL-160 G-1 / NL-166 / M1 FIX-1 instrument precedent — currency minted
# twice by two hands is not re-typed through a third). Proven green on the
# real bytes and red on each law-breaking mutation by QA's run and the gate's
# own re-run before landing.
#
# sw.js is the one law-bearing artifact in this batch the suite cannot
# EXECUTE without a new dependency (node); that dependency decision rides to
# the checkpoint. This pin is the honest FLOOR under that gap: it reds when a
# load-bearing policy line is deleted or inverted on disk. It is STRUCTURAL,
# not behavioural — a determined refactor could keep the anchors and change
# behaviour (the labelled limitation; the behavioural harness lives at
# scripts/sw_harness.mjs, deliberately NOT in the suite). Each mutation case
# asserts its anchor still exists before mutating, so an anchor drift is a
# loud red that forces this pin to move WITH the policy, never a vacuous
# pass over a mutation that no longer applies.
# ---------------------------------------------------------------------------

def _sw_source() -> str:
    return (PROTOTYPE_ROOT / "hosted/static/sw.js").read_text(encoding="utf-8")


def _sw_fn_body(src: str, fn: str) -> str:
    """The source of function `fn` up to its matching close brace."""
    m = re.search(r"function\s+" + re.escape(fn) + r"\s*\([^)]*\)\s*\{", src)
    if not m:
        return ""
    i = m.end()
    depth = 1
    while i < len(src) and depth:
        if src[i] == "{":
            depth += 1
        elif src[i] == "}":
            depth -= 1
        i += 1
    return src[m.end():i]


def sw_policy_violations(src: str):
    """Every service-worker LAW whose load-bearing line is missing from `src`.

    The laws, from the adjudication and the sw.js header: the cache is never
    auth-gated; today-first, never plain cache-first; /api/* always reaches
    the network (the offline probe); same-origin GET only; the edition slot
    keeps the NEWEST-seen date; a dated edition is answered only by its day."""
    v = []
    ref = _sw_fn_body(src, "isRefusal")
    if not re.search(r"\b401\b", ref) or not re.search(r"\b403\b", ref):
        v.append("isRefusal no longer treats 401/403 as refusals -> the cache "
                 "would be auth-gated")
    if not re.search(r">=\s*500|>\s*499", ref):
        v.append("isRefusal no longer treats 5xx as a refusal")
    if "/login" not in ref:
        v.append("isRefusal no longer treats a /login redirect as a refusal")
    fetchblk = (src[src.find("addEventListener('fetch'"):]
                if "addEventListener('fetch'" in src else src)
    if "todayLocal()" not in fetchblk:
        v.append("the fetch handler no longer consults todayLocal() -> "
                 "today-first refinement lost (plain cache-first would serve "
                 "yesterday in a live morning)")
    if not re.search(r"isToday\s*&&\s*isFront", fetchblk):
        v.append("the cache-first branch is no longer gated on "
                 "(isToday && isFront)")
    if not re.search(r"pathname\.indexOf\('/api/'\)\s*===?\s*0", fetchblk):
        v.append("/api/* is no longer excluded from interception -> the "
                 "offline probe cannot reach the network, the stamp would lie")
    if "request.method !== 'GET'" not in fetchblk:
        v.append("non-GET requests are no longer passed through")
    if "url.origin !== self.location.origin" not in fetchblk:
        v.append("cross-origin requests are no longer passed through "
                 "(poison-cache risk)")
    st = _sw_fn_body(src, "storeEdition")
    if not re.search(r"heldDate\s*<=\s*date|heldDate\s*<\s*date", st):
        v.append("the edition slot no longer keeps the NEWEST-seen date (a "
                 "last-write slot loses tomorrow's paper after an archive "
                 "browse)")
    ce = _sw_fn_body(src, "cachedEdition")
    if "allowSlot" not in ce:
        v.append("cachedEdition no longer distinguishes the front page from a "
                 "dated edition -> an archive date could be answered with a "
                 "different day")
    return v


def test_the_service_workers_policy_lines_are_all_present():
    assert sw_policy_violations(_sw_source()) == []


_SW_MUTATIONS = [
    ("auth-gating",
     "if (response.status === 401 || response.status === 403) { return true; }",
     "/* mutated: auth answers no longer refusals */",
     "auth-gated"),
    ("today-first",
     "var isToday = hit && hit.headers.get('X-NewsLens-Edition-Date') === todayLocal();",
     "var isToday = !!hit;",
     "today-first"),
    ("api-probe",
     "if (url.pathname.indexOf('/api/') === 0) { return; }",
     "/* mutated: api now intercepted */",
     "offline probe"),
    ("slot-newest",
     "if (!heldDate || heldDate <= date) { return cache.put(EDITION_KEY, forSlot); }",
     "return cache.put(EDITION_KEY, forSlot);",
     "NEWEST-seen"),
]


@pytest.mark.parametrize("law,old,new,expect",
                         _SW_MUTATIONS, ids=[m[0] for m in _SW_MUTATIONS])
def test_the_policy_pin_bites_when_a_law_line_is_broken(law, old, new, expect):
    """The tooth's own red, kept live: each law-breaking mutation must be
    caught. `old in src` first — if that fails, the anchor drifted and the
    pin must move WITH the policy change, consciously."""
    src = _sw_source()
    assert old in src, (
        f"the {law} anchor drifted — update _SW_MUTATIONS with the policy "
        "change, do not delete the case")
    mutated = src.replace(old, new)
    assert mutated != src
    violations = sw_policy_violations(mutated)
    assert violations and any(expect in x for x in violations), violations
