"""Stage-0 C1 QA — RED ACCEPTANCE CONTRACTS for the Commissioning.

Written by the QA seat 2026-07-30 against the implementer's C1 diff (working
tree on 93e64c9, uncommitted). EVERY TEST IN THIS FILE IS RED ON ARRIVAL and
each one is an acceptance criterion: the milestone is done when they are green.
Each docstring carries the FIX CONTRACT — what must become true, not how.

PROOF CLASS, stated honestly (ENGINEERING.md born-red law): these are NOT
born-red enforcement pins for new wiring. They are QA-authored acceptance
tests that FAIL ON THE CURRENT TREE because the behaviour they require is
absent or wrong. The HEAD-run fail list is this file's own first run, recorded
in the QA report.

WHY THE SHIPPED SUITE MISSED THE TOP TWO. Every one of the implementer's 43
commissioning tests exercises the write door by POSTing canonical catalog
names, i.e. the bytes a *test* chooses. None of them ever parses the rendered
HTML the way a browser parses it. The picker's markup is therefore unasserted
at exactly the seam where it is broken.
"""

from __future__ import annotations

import json
import os
import re
import stat
import threading
import urllib.error
import urllib.request
from html.parser import HTMLParser
from http.server import ThreadingHTTPServer

import pytest

from newslens import catalog, commissioning, config, db, labels, paths, server


# ---------------------------------------------------------------------------
# fixtures — the same hermetic sandbox the shipped C1 tests use
# ---------------------------------------------------------------------------

@pytest.fixture
def fresh(tmp_paths):
    db.migrate()
    paths.SOURCES_FILE.write_text(
        paths.PROFILE_SOURCES_TEMPLATE.read_text(encoding="utf-8"),
        encoding="utf-8")
    return tmp_paths


@pytest.fixture
def cat():
    return catalog.load()


@pytest.fixture
def picker_html(fresh, cat):
    return commissioning.render("picker", config.load_sources(),
                                {"state": "idle"}, cat=cat)


class _Inputs(HTMLParser):
    """An HTML5-conformant tokenizer — the same rule a browser applies to an
    unquoted attribute value: it ends at the first whitespace."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.inputs = []
        self.fieldsets = []

    def handle_starttag(self, tag, attrs):
        if tag == "input":
            self.inputs.append(dict(attrs))
        elif tag == "fieldset":
            self.fieldsets.append(dict(attrs))


def _parse(html):
    p = _Inputs()
    p.feed(html.split("</style>", 1)[-1])
    return p


class _AllTags(HTMLParser):
    """Every start tag: its parsed attributes AND its literal source text.

    `get_starttag_text()` is the raw markup the template emitted, which is what
    lets an unquoted attribute be caught DIRECTLY rather than only through the
    boolean attributes its whitespace happens to produce."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.tags = []

    def handle_starttag(self, tag, attrs):
        self.tags.append((tag, attrs, self.get_starttag_text() or ""))

    handle_startendtag = handle_starttag


def _all_tags(html):
    p = _AllTags()
    p.feed(html.split("</style>", 1)[-1])
    return p.tags


# An attribute whose value does not open with a quote. Restricted to start-tag
# source text, so the page's inline script (full of `a = b`) is never scanned.
_UNQUOTED_ATTR = re.compile(r"""[\s]([a-zA-Z][a-zA-Z0-9-]*)=(?!["'])""")


# ===========================================================================
# QA-1 — BLOCKER: the picker's own markup destroys 54 of its 66 values
# ===========================================================================

def test_QA1_every_picker_value_survives_an_html_parse(picker_html, cat):
    """FIX CONTRACT: every checkbox's `value` must still equal its catalog name
    after the page is parsed as HTML.

    THE DEFECT. commissioning._pick_row builds the input with
    `value=%s ... data-name=%s`, and `_attr()` escapes but does NOT add
    surrounding quotes. An unquoted HTML attribute value terminates at the
    first whitespace, so `value=Middle East Conflict` parses as
    value="Middle" plus two invented boolean attributes `east` and `conflict`.

    CONSEQUENCE, end to end: the page's own picked() reads b.value, so a reader
    who ticks "Central Bank Policy" POSTs "Central"; catalog.resolve() cannot
    resolve it, and the found act answers COMMISSION_UNKNOWN_TOPIC — "one of
    those topics isn't in the list" — about a topic the page itself just
    rendered. 54 of 66 entries (82%) are unpickable in a browser. Verified in
    Chrome/WebKit as well as this parser.

    THE FIX IS ONE CHARACTER PER ATTRIBUTE: quote them. `_attr()` already
    escapes `"` to &quot;, so `value="%s"` is safe as-is."""
    boxes = [i for i in _parse(picker_html).inputs
             if i.get("type") == "checkbox"]
    names = cat.names()
    assert len(boxes) == len(names) == cat.entry_count
    broken = [(n, b.get("value")) for n, b in zip(names, boxes)
              if b.get("value") != n]
    assert not broken, (
        "%d of %d picker values do not survive an HTML parse; first five: %s"
        % (len(broken), len(names), broken[:5]))


def test_QA1b_the_page_invents_no_boolean_attributes(fresh, cat):
    """FIX CONTRACT: no rendered state carries an attribute its template never
    wrote — asserted from the other side of QA-1, so a fix that quotes `value`
    but leaves `data-name`/`data-domain` unquoted still goes red.

    AMENDED BY QA 2026-07-30 (fix loop 1 arbitration), for one reason and
    STRENGTHENED, not softened: the original body swept EVERY <input> on the
    page against a picker-shaped allowlist, so it flagged the filter field's
    `autocomplete="off"` and `aria-describedby="c1-filter-status"` — bytes
    written verbatim by the binding mockup at mockup-v12-commissioning.html
    line 413 (verified). Green would have required deleting spec bytes and the
    field's own description relationship, which is a11y regression, not a fix.

    The replacement is a STRICTLY WIDER contract than either the original or
    the implementer's picker-scoped substitute. The fingerprint of an unquoted
    attribute is not "an unexpected name on a checkbox" — it is A BOOLEAN
    ATTRIBUTE: `value=Middle East Conflict` parses as value="Middle" plus two
    valueless attributes `east` and `conflict`. So this sweeps EVERY start tag
    of EVERY rendered state (picker, waiting, failed, failed-with-error, and
    the staleness banner) and requires every attribute to carry a value except
    the two the templates legitimately declare bare. That bites on an unquoted
    attribute anywhere on any state — including the three panels the picker's
    own test never renders — and it cannot be satisfied by deleting mockup
    bytes, because a declared attribute WITH a value always passes."""
    DECLARED_BARE = {"hidden", "checked"}
    states = [
        ("picker", commissioning.render("picker", config.load_sources(),
                                        {"state": "idle"}, cat=cat,
                                        staleness_banner='<p class="b">x</p>')),
        ("waiting", commissioning.render("waiting", config.load_sources(),
                                         {"state": "running",
                                          "stage_label": "Ranking"}, cat=cat)),
        ("failed", commissioning.render("failed", config.load_sources(),
                                        {"state": "error"},
                                        outcome="No edition.", cat=cat)),
        ("failed+err", commissioning.render(
            "failed", config.load_sources(),
            {"state": "error", "error": "ingest failed: 12 of 37 feeds timed out"},
            outcome="No edition.", cat=cat)),
    ]
    naked, bare = [], {}
    for name, html in states:
        for tag, attrs, src in _all_tags(html):
            # ARM 1 — the direct check, on the literal markup. Catches an
            # unquoted attribute even when its value has no whitespace (a
            # number, a slug), which ARM 2 structurally cannot see.
            for hit in _UNQUOTED_ATTR.findall(src):
                naked.append("%s:<%s %s=…>" % (name, tag, hit))
            # ARM 2 — the parsed fingerprint: a multi-word unquoted value
            # becomes several valueless attributes.
            for k, v in attrs:
                if v is None and k not in DECLARED_BARE:
                    bare.setdefault("%s:<%s> %s" % (name, tag, k), 0)
                    bare["%s:<%s> %s" % (name, tag, k)] += 1
    assert not naked, ("%d attribute values are not quoted in the rendered "
                       "markup: %s" % (len(naked), sorted(set(naked))[:12]))
    assert not bare, (
        "%d valueless attributes the templates never wrote — the fingerprint "
        "of an unquoted attribute value: %s" % (len(bare), sorted(bare)[:12]))


def test_QA1c_picker_rows_and_fieldsets_carry_only_declared_attributes(
        picker_html):
    """The other half of QA-1b's original intent, kept at the scope its own
    name always read: the PICKER's rows and fieldsets, by attribute NAME.

    `data-cov` joined the declared set on 2026-08-03 (NL-135 Q1): each row
    carries its own coverage state so the client can recount unserved picks
    without re-deriving a rule. Declaring it HERE is the deliberate act this
    pin exists to force — an attribute name nobody declared is still the
    fingerprint of an unquoted value, and the value is quoted."""
    allowed = {"class", "type", "id", "value", "data-level", "data-name",
               "data-cov", "onchange", "checked"}
    p = _parse(picker_html)
    stray = {}
    for box in p.inputs:
        if box.get("type") != "checkbox":
            continue                      # the filter field is not a pick row
        for k in box:
            if k not in allowed:
                stray.setdefault(k, 0)
                stray[k] += 1
    for fs in p.fieldsets:
        for k in fs:
            if k not in {"class", "data-domain", "hidden"}:
                stray.setdefault("fieldset:" + k, 0)
                stray["fieldset:" + k] += 1
    assert not stray, ("the parser invented %d attribute names the template "
                       "never wrote: %s" % (len(stray), sorted(stray)[:12]))


# ===========================================================================
# QA-2 — BLOCKER: the filter cannot find most of the catalog, and says so
# ===========================================================================

@pytest.mark.parametrize("query,must_find", [
    ("bank", "Central Bank Policy"),
    ("policy", "Economic Policy"),
    ("east", "Middle East Conflict"),
    ("reserve", "Federal Reserve"),
    ("health", "Public Health"),
])
def test_QA2_the_filter_finds_a_topic_by_a_word_inside_its_name(
        picker_html, query, must_find):
    """FIX CONTRACT: typing any word that appears in a catalog name must match
    that name. The page promises "Typing filters this list"; today it filters
    on a `data-name` that was truncated at the first space by the same quoting
    defect as QA-1.

    OBSERVED IN A REAL BROWSER: typing "bank" renders "No topic matches
    “bank”." while *Central Bank Policy* and *Shadow Banking* are both on
    screen. That is the page telling a reader a topic does not exist while
    displaying it — the sharpest form of the failure, because the no-match copy
    was deliberately written to give the reader nothing to argue with.

    This test asserts the DATA the filter reads, not the JS: data-name must
    carry the whole lowercased name."""
    boxes = [i for i in _parse(picker_html).inputs
             if i.get("type") == "checkbox"]
    hits = [b for b in boxes if query in (b.get("data-name") or "")]
    names = [b.get("data-name") for b in hits]
    assert must_find.lower() in names, (
        "filtering on %r cannot reach %r; data-name values that matched: %s"
        % (query, must_find, names))


# ===========================================================================
# QA-3 — HIGH: the partial-write refusal states something untrue
# ===========================================================================

def test_QA3_a_partial_write_never_claims_nothing_was_saved(fresh, cat):
    """FIX CONTRACT: when some picks landed and a later one failed, the reader
    must not be told their topics "couldn't be saved". Either roll the landed
    writes back, or name what landed. The refusal must not be false.

    THE DEFECT (the implementer's self-flag 2, driven here). Pick three topics
    and fail the SECOND write: the first is already on disk, commission()
    returns `written=[]`, and the reader gets COMMISSION_WRITE_REFUSAL —
    "your topics couldn't be saved, so nothing was started." One of the three
    WAS saved. Nothing on the page ever says which, and the picker re-renders
    every box unchecked (see QA-7), so the reader's next act is taken against a
    file they believe is empty.

    THE STAKES ARE NOT COSMETIC: the saved topic personalises the edition that
    eventually generates. A reader who is told a pick did not save, and whose
    morning is then ranked by it, has been told something false about the one
    file this page exists to write."""
    picks = ["Inflation", "Federal Reserve", "ECB"]
    calls = []

    def door(name, level):
        calls.append(name)
        if len(calls) == 2:
            return False, "simulated editor failure"
        return server.topic_add(name, level)

    ok, refusal, written = commissioning.commission(picks, door, cat=cat)
    assert not ok
    after = config.load_sources()
    landed = list(after.interests_broad) + list(after.interests_granular)
    assert landed, "probe misfired — nothing landed, so there is no partial write"
    assert refusal != labels.COMMISSION_WRITE_REFUSAL or not landed, (
        "reader is told %r while %s IS on disk" % (refusal, landed))
    for name in landed:
        assert name in refusal or name in [w[0] for w in written], (
            "%r landed on disk but the reader is never told: refusal=%r "
            "written=%r" % (name, refusal, written))


# ===========================================================================
# QA-4 — HIGH: a name stored at the other level is an unrecoverable deadlock
# ===========================================================================

def test_QA4_a_name_already_stored_at_the_other_level_is_not_a_deadlock(
        fresh, cat):
    """FIX CONTRACT: a reader must always be able to found an edition. A
    catalog name that is already present in sources.yaml at the OPPOSITE level
    from the catalog's ruling must not refuse forever.

    THE DEFECT (QA-found; not in the implementer's self-flags). commission()
    builds `already` as the UNION of interests.broad and interests.granular, so
    a name present at either level is skipped at the write step. The verify
    step then checks the name is present at its CATALOG level specifically —
    and it is not. Result: COMMISSION_VERIFY_REFUSAL, "your topics didn't
    save", forever. Retrying cannot help: the write is skipped every time.

    'Systemic Risk' is exactly this shape. It is the one name the shipped data
    carries at BOTH levels (a domain in personas/rates-desk.yaml, a topic in
    the founder's own sources.yaml); the catalog rules it a DOMAIN. Any profile
    whose sources.yaml already holds it as a topic — anything seeded from a
    founder-shaped file — cannot ever pick it.

    Worse with a companion: picking ['Systemic Risk', 'Inflation'] WRITES
    Inflation and still refuses, so a reader retrying accumulates writes while
    being told nothing saved."""
    ok, msg = server.topic_add("Systemic Risk", "specific")   # the topic level
    assert ok, msg
    assert cat.level_of("Systemic Risk") == catalog.DOMAIN

    outcomes = [commissioning.commission(["Systemic Risk"], server.topic_add,
                                         cat=cat)[0] for _ in range(3)]
    assert any(outcomes), (
        "three consecutive found attempts all refused — this profile can never "
        "found an edition (outcomes=%s)" % outcomes)


def test_QA4b_a_refusal_that_can_leave_the_file_untouched_does(fresh, cat):
    """FIX CONTRACT: every refusal class that CAN leave sources.yaml
    byte-identical DOES — and the one that cannot names what landed.

    AMENDED BY QA 2026-07-30 (fix loop 1 arbitration). The original body opened
    `commission(['Systemic Risk','Inflation']) ... assert not ok`, using the
    one input that was GUARANTEED to refuse pre-fix so it could then check the
    file. That guarantee WAS the QA-4 deadlock: post-fix the same input
    succeeds, which is precisely what QA-4's contract demanded ("a reader must
    always be able to found an edition"). `assert not ok` therefore pinned the
    bug its own sibling forbade, and it is retired — the ONE line that changed.

    Its real contract survives here intact, and is now asserted over every
    refusal class instead of one accidental input. The unreachable half is
    retired honestly too: a partial write is NOT rolled back, because QA-3
    ruled disclosure over rollback (either was allowed; the dispatch chose
    disclosure) and because a blind rollback in a two-tab world could delete a
    name a SECOND caller had just legitimately written — _YAML_LOCK is held per
    edit, not across a commission. So the surviving law is: a refusal never
    leaves an UNDISCLOSED write."""
    classes = [
        ("unknown topic", ["Nope"], server.topic_add),
        ("no picks at all", [], server.topic_add),
        ("first write fails", ["Inflation", "ECB"],
         lambda n, l: (False, "simulated editor failure")),
        ("door lies: ok, writes nothing", ["Inflation"],
         lambda n, l: (True, "lied")),
    ]
    for label, picks, door in classes:
        paths.SOURCES_FILE.write_text(
            paths.PROFILE_SOURCES_TEMPLATE.read_text(encoding="utf-8"),
            encoding="utf-8")
        before = paths.SOURCES_FILE.read_bytes()
        ok, refusal, written = commissioning.commission(picks, door, cat=cat)
        assert not ok, label
        assert written == [], "%s: refused but claims %r written" % (label, written)
        assert paths.SOURCES_FILE.read_bytes() == before, (
            "%s: a REFUSED commission wrote to sources.yaml; refusal=%r"
            % (label, refusal))

    # And the class that CANNOT be byte-identical must disclose, never deny.
    paths.SOURCES_FILE.write_text(
        paths.PROFILE_SOURCES_TEMPLATE.read_text(encoding="utf-8"),
        encoding="utf-8")
    calls = []

    def second_fails(name, level):
        calls.append(name)
        if len(calls) == 2:
            return False, "simulated editor failure"
        return server.topic_add(name, level)

    ok, refusal, written = commissioning.commission(
        ["Inflation", "ECB"], second_fails, cat=cat)
    after = config.load_sources()
    landed = list(after.interests_broad) + list(after.interests_granular)
    assert not ok and landed == ["Inflation"], (ok, landed)
    assert refusal != labels.COMMISSION_WRITE_REFUSAL, (
        "a partial write is denied rather than disclosed: %r" % refusal)
    for name in landed:
        assert name in refusal and name in [w[0] for w in written], (
            "%r landed but is not disclosed: refusal=%r written=%r"
            % (name, refusal, written))


# ===========================================================================
# QA-5 — MEDIUM: concurrent found acts report a write failure that did not happen
# ===========================================================================

def test_QA5_a_second_tab_is_not_told_its_topics_failed_to_save(fresh, cat):
    """FIX CONTRACT: when two loads submit the same picks, the loser must get a
    truthful answer — the topics are saved and a run is going.

    THE DEFECT (implementer self-flag 3, driven here). Both callers read
    `before` and compute `already` from it. The first writes; the second calls
    server.topic_add for a name that is now present, and the editor correctly
    refuses ("Didn't add it — X is already in your specific topics"). commission()
    reads that boolean as a write failure and answers COMMISSION_WRITE_REFUSAL.

    Measured over real HTTP with six racing POSTs: one 200, five 400s saying
    "your topics couldn't be saved, so nothing was started" — while all three
    topics were on disk and a run HAD started. The client's `btn.disabled` does
    not cover this: the page invites a second tab ("Closing this page won't stop
    it"), and two tabs is the reachable case.

    NOTE the write door's refusal is a SUCCESS condition, not a failure: the
    name is present, which is what the caller wanted.

    A SEQUENTIAL re-submit does NOT reproduce this: by then `before` already
    contains the names, so they are skipped and verify passes. The defect needs
    two callers whose `before` reads interleave — hence real threads."""
    picks = ["Inflation", "Federal Reserve", "ECB"]
    results = []
    barrier = threading.Barrier(6)

    def fire():
        barrier.wait()                      # force the `before` reads to race
        results.append(commissioning.commission(picks, server.topic_add,
                                                cat=cat))

    threads = [threading.Thread(target=fire) for _ in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    after = config.load_sources()
    landed = list(after.interests_broad) + list(after.interests_granular)
    assert sorted(landed) == sorted(picks), (
        "concurrency corrupted the write: on disk=%s" % landed)
    liars = [r[1] for r in results
             if not r[0] and r[1] == labels.COMMISSION_WRITE_REFUSAL]
    assert not liars, (
        "%d of %d concurrent found acts were told %r — but every topic IS on "
        "disk (%s)" % (len(liars), len(results),
                       labels.COMMISSION_WRITE_REFUSAL, landed))


# ===========================================================================
# QA-6 — MEDIUM: unfit_for_readers leaks classes the regex never modelled
# ===========================================================================

@pytest.mark.parametrize("label,error", [
    ("bare relative path", "prompt template missing: prompts/narrative.md"),
    ("OSError with a filename", "[Errno 2] No such file or directory: 'memory.md'"),
    ("profile named unquoted", "profile default has no interests configured"),
    ("profile named, double quotes", 'profile "default" has no interests configured'),
    ("the YAML key, backticked", "add them under `interests:`"),
    ("env var name", "NEWSLENS_SOURCES_FILE is not set"),
    ("model id", "claude-opus-4-6 returned an error"),
    ("credential shaped", "AuthenticationError: invalid x-api-key"),
    ("module path", "newslens.ranking.RankingError: no interests"),
    ("spend", "budget cap exceeded: $4.12 spent this run"),
])
def test_QA6_the_failure_panel_shows_no_operator_sentence(fresh, label, error):
    """FIX CONTRACT: none of these reach a stranger's failure panel.

    The implementer named this class themselves ("unfit_for_readers's regex is
    the whole defence and regexes are guesses"). SEAM-2's ORDERING holds — the
    shipped no-interests refusal is genuinely unreachable, and its verbatim text
    IS blocked. This is the belt, and the belt has holes.

    The two that matter most: a MODEL ID and a SPEND FIGURE on a $0 product's
    first screen, and a credential-shaped error. A denylist cannot close this;
    the shape of the fix is an allowlist — render the run's own sentence only
    when it matches a known reader-safe form, and otherwise omit it (which the
    panel already knows how to do)."""
    assert commissioning.unfit_for_readers(error), (
        "%s is not recognised as an operator sentence: %r" % (label, error))


# ===========================================================================
# QA-7 — MEDIUM: the picker hides state the reader already owns
# ===========================================================================

def test_QA7_the_picker_shows_the_topics_that_are_already_saved(fresh, cat):
    """FIX CONTRACT: a topic already in this profile's sources.yaml renders
    checked.

    The founding page is reachable repeatedly before the first edition
    publishes — after a stale-server refusal, after a partial write, after any
    reload. Every one of those re-renders shows 66 empty circles over a file
    that is not empty. The reader cannot see their own state, and the count
    line ("Nothing picked yet.") states it as a fact.

    This is the disclosure half of QA-3: with it, a partial write is legible on
    the very next load without any new copy."""
    ok, _, _ = commissioning.commission(["Inflation"], server.topic_add, cat=cat)
    assert ok
    html = commissioning.render("picker", config.load_sources(),
                                {"state": "idle"}, cat=cat)
    boxes = {b.get("data-name"): b for b in _parse(html).inputs
             if b.get("type") == "checkbox"}
    infl = boxes.get("inflation")
    assert infl is not None, "Inflation row not found in the picker"
    assert "checked" in infl, (
        "Inflation is saved in sources.yaml but its checkbox renders unchecked")


# ===========================================================================
# QA-9 — HIGH: the found act's refusal line is not guarded at all
# ===========================================================================

@pytest.fixture
def ui(fresh, monkeypatch):
    monkeypatch.setattr(server, "GEN_JOB", server._GenJob())
    monkeypatch.setattr(server.GEN_JOB, "start", lambda: True)
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield "http://127.0.0.1:%d" % httpd.server_address[1]
    httpd.shutdown()
    httpd.server_close()


def _post(base, payload):
    req = urllib.request.Request(
        base + "/api/commission", data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return r.getcode(), json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode("utf-8"))


def test_QA9_the_found_act_never_answers_with_an_operator_sentence(ui):
    """FIX CONTRACT: /api/commission answers a blessed reader-world refusal in
    EVERY arm, including the unhandled-exception arm. The operator's grade of
    the same fact goes to the serve terminal, exactly as server.py:113 already
    does for a failed generate.

    THE DEFECT (QA-found this pass; my pass-1 sweep missed it because I drove
    11 hand-crafted PAYLOADS and never injected a FAULT).

    `unfit_for_readers()` has exactly one call site — commissioning.py:719, the
    failure PANEL. The found act's refusal line is a different surface and is
    guarded by nothing: the client writes the server's string straight into
    #c1-refusal (`refusal.textContent = (d && d.error) || NL_C1.refusal`,
    commissioning.py:1060), and do_POST's catch-all answers
    `{"ok": false, "error": str(exc)}` with 500. _api_commission catches only
    catalog.CatalogError.

    IT IS REACHABLE WITH THE SHIPPED CODE AND NO MONKEYPATCH. server._yaml_edit
    wraps only config.load_sources() in try/except; `path.read_text()`,
    `tmp.write_text()` and `os.replace()` are bare, so a real OSError walks out
    of server.topic_add untouched. Driven against a read-only profile
    directory — a setup mistake SETUP.md exists to prevent — the picker renders
    perfectly (HTTP 200, 66 boxes), the reader ticks a topic, presses *Found my
    edition*, and the line under the button reads:

        [Errno 13] Permission denied: '/Users/…/sources.yaml.tmp'

    An absolute filesystem path, an errno and a temp filename, on a stranger's
    first screen, at the one act SEAM 2 was ratified build-blocking to protect.
    unfit_for_readers() returns True on that very string — it is simply never
    asked. The file is left intact, so this is a truthfulness defect, not a
    data one.

    (The founder's own /api/topic/add leaks the same way, so the MECHANISM
    predates C1. C1 is the first route where the reader is a stranger, which is
    what makes it this milestone's to close.)"""
    base = ui
    d = paths.SOURCES_FILE.parent
    mode = os.stat(d).st_mode
    os.chmod(d, stat.S_IRUSR | stat.S_IXUSR)      # readable, not writable
    try:
        code, body = _post(base, {"topics": ["Inflation"]})
    finally:
        os.chmod(d, mode)
    shown = str(body.get("error", ""))
    blessed = {labels.COMMISSION_WRITE_REFUSAL, labels.COMMISSION_VERIFY_REFUSAL,
               labels.COMMISSION_UNKNOWN_TOPIC, labels.COMMISSION_FOUND_REFUSAL,
               labels.STALENESS_REFUSAL}
    assert shown in blessed or not shown, (
        "the found act answered HTTP %s with an operator sentence the reader's "
        "refusal line renders verbatim: %r" % (code, shown))
