"""Stage-0 C1 — THE PICKER'S MARKUP, PARSED THE WAY A BROWSER PARSES IT.

THE TEST CLASS THIS SUITE WAS MISSING, named by the QA pass of 2026-07-30 and
added in fix loop 1. Every one of the 43 shipped commissioning tests drives the
write door by POSTing canonical catalog names — i.e. bytes a TEST chose. Not one
of them ever tokenised the rendered page. So the picker's markup was unasserted
at exactly the seam where it was broken, and a BLOCKER shipped past a green
suite: `value=%s` and `data-name=%s` were rendered UNQUOTED, an unquoted HTML
attribute value terminates at the first whitespace, and 54 of the 66 catalog
entries (82%) were therefore unpickable in a browser. A reader who ticked
*Central Bank Policy* POSTed the string "Central"; the found act answered "one
of those topics isn't in the list" about a topic the page had just rendered.
The same truncation made the filter tell a reader that *Central Bank Policy*
did not exist while it was on screen.

THE RULE THIS FILE ENFORCES: the bytes the PAGE would post must round-trip to
the catalog's own name — render -> tokenise -> resolve -> write -> read back.
A test that chooses its own input can never see this class of defect, so every
assertion below starts from html.parser, whose unquoted-attribute rule is the
HTML5 tokenizer's rule and was confirmed against a live Chrome/WebKit engine by
the QA seat.

The falsifier rides with it: test_the_parse_tooth_bites re-renders through the
PRE-FIX row template and asserts this file goes red on it. A parse test that
cannot detect the bug it exists for looks identical to a clean page.

Sandbox: the tree conftest's autouse fixtures apply. $0 by construction —
GEN_JOB.start is stubbed everywhere it could be reached; no pipeline runs.
"""

from __future__ import annotations

import json
import re
import threading
import urllib.error
import urllib.request
from html.parser import HTMLParser
from http.server import ThreadingHTTPServer
from types import SimpleNamespace

import pytest

from newslens import catalog, commissioning, config, db, labels, paths, server


# ---------------------------------------------------------------------------
# A browser-grade tokenizer, and the world the page renders into
# ---------------------------------------------------------------------------

class _Markup(HTMLParser):
    """html.parser applies the HTML5 rule for an unquoted attribute value: it
    ends at the first whitespace, and the remaining words become boolean
    attributes of their own. That is precisely what a browser does, which is
    why this and not a regex is the instrument."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.inputs = []
        self.fieldsets = []

    def handle_starttag(self, tag, attrs):
        if tag == "input":
            self.inputs.append(dict(attrs))
        elif tag == "fieldset":
            self.fieldsets.append(dict(attrs))


def parse(html: str) -> _Markup:
    p = _Markup()
    p.feed(html.split("</style>", 1)[-1])      # the page minus its stylesheet
    return p


def checkboxes(html: str):
    return [i for i in parse(html).inputs if i.get("type") == "checkbox"]


def count_line(html: str) -> str:
    """The rendered count ELEMENT, not the page. The NL_C1 blob ships every
    count string to the client by design, so a page-wide `in` would pass over a
    line that says the opposite of what it renders."""
    return re.search(r'<p class="count" id="c1-count">(.*?)</p>',
                     html, re.S).group(1)


@pytest.fixture
def fresh(tmp_paths):
    """A freshly provisioned reader's world — the shipped template with an
    EMPTY interests block, which is the state the founding page exists for."""
    db.migrate()
    paths.SOURCES_FILE.write_text(
        paths.PROFILE_SOURCES_TEMPLATE.read_text(encoding="utf-8"),
        encoding="utf-8")
    return tmp_paths


@pytest.fixture
def cat():
    return catalog.load()


@pytest.fixture
def page(fresh, cat):
    return commissioning.render("picker", config.load_sources(),
                                {"state": "idle"}, cat=cat)


@pytest.fixture
def ui(fresh, monkeypatch):
    monkeypatch.setattr(server, "GEN_JOB", server._GenJob())
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield SimpleNamespace(base="http://127.0.0.1:%d" % httpd.server_address[1])
    httpd.shutdown()
    httpd.server_close()


def get(ui, path):
    with urllib.request.urlopen(ui.base + path, timeout=10) as r:
        return r.getcode(), r.read().decode("utf-8")


def post(ui, path, payload):
    req = urllib.request.Request(
        ui.base + path, data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.getcode(), json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode("utf-8"))


# ---------------------------------------------------------------------------
# The round trip: render -> tokenise -> the value the page would POST
# ---------------------------------------------------------------------------

def test_every_rendered_checkbox_value_survives_the_tokenizer(page, cat):
    """All 66, one at a time and by name. The page's own picked() reads
    `b.value`, so this IS the string the reader's browser would send."""
    boxes = checkboxes(page)
    names = cat.names()
    assert len(boxes) == len(names) == cat.entry_count
    broken = [(n, b.get("value")) for n, b in zip(names, boxes)
              if b.get("value") != n]
    assert not broken, ("%d of %d values were destroyed by the parse: %s"
                        % (len(broken), len(names), broken[:5]))


def test_the_multi_word_names_are_the_majority_this_protects(cat):
    """The shape of the risk, stated as a number rather than assumed. 54 of the
    66 entries carry a space; single-token names were the only 12 that ever
    worked, which is why a suite driving canonical names could stay green over
    a page that was 82% broken."""
    multi = [n for n in cat.names() if " " in n]
    assert len(multi) == 54 and cat.entry_count == 66


def test_the_values_the_page_would_post_all_found_an_edition(fresh, cat):
    """THE FULL ROUND TRIP, end to end, through the real write door: take the
    bytes off the rendered page, hand them to commission() exactly as the
    client would, and read the file back. Every catalog name must arrive at its
    own level — nothing truncated, nothing dropped, nothing renamed."""
    values = [b["value"] for b in
              checkboxes(commissioning.render("picker", config.load_sources(),
                                              {"state": "idle"}, cat=cat))]
    ok, refusal, written = commissioning.commission(values, server.topic_add,
                                                    cat=cat)
    assert ok, refusal
    assert len(written) == cat.entry_count
    cfg = config.load_sources()
    assert sorted(cfg.interests_broad) == sorted(d.name for d in cat.domains)
    assert sorted(cfg.interests_granular) == sorted(
        t for d in cat.domains for t in d.topics)
    assert not cfg.problems


def test_a_multi_word_pick_founds_over_real_http_from_the_rendered_bytes(
        ui, monkeypatch, cat):
    """The QA's own end-to-end case, driven over real HTTP: GET the page, take
    the value the browser would post for *Central Bank Policy*, POST it. Before
    the fix this posted "Central" and the reader met COMMISSION_UNKNOWN_TOPIC
    about a topic the page had just rendered."""
    monkeypatch.setattr(server.GEN_JOB, "start", lambda: True)
    _, html = get(ui, "/")
    box = next(b for b in checkboxes(html)
               if b.get("id") == "pk-central-bank-policy")
    assert box["value"] == "Central Bank Policy"
    code, out = post(ui, "/api/commission", {"topics": [box["value"]]})
    assert code == 200 and out["ok"] is True, out
    assert config.load_sources().interests_broad == ["Central Bank Policy"]


def test_a_name_carrying_an_html_special_character_round_trips(page, cat):
    """`Mergers & Acquisitions` — the escaping half. `_attr()` escapes `&` to
    `&amp;` and `"` to `&quot;`; the quotes added by the fix do not change that,
    and the tokenizer must hand back the original name character for character."""
    target = next(n for n in cat.names() if "&" in n)
    box = next(b for b in checkboxes(page) if b.get("value") == target)
    assert box["value"] == "Mergers & Acquisitions" == target
    assert box["data-name"] == target.lower()


# ---------------------------------------------------------------------------
# The filter reads data-name — the same bytes, the other consumer
# ---------------------------------------------------------------------------

def test_every_data_name_is_the_whole_lowercased_name(page, cat):
    """filterCatalog() matches `q` against data-name. Truncated, it made the
    page state that a visible topic did not exist — the sharpest form of the
    failure, because the no-match copy was deliberately written to give the
    reader nothing to argue with."""
    boxes = checkboxes(page)
    wrong = [(n, b.get("data-name")) for n, b in zip(cat.names(), boxes)
             if b.get("data-name") != n.lower()]
    assert not wrong, wrong[:5]


@pytest.mark.parametrize("typed,reaches", [
    ("bank", ["central bank policy", "shadow banking"]),
    ("policy", ["economic policy", "central bank policy", "policy failure"]),
    ("east", ["east asia", "middle east conflict"]),
])
def test_typing_an_inner_word_reaches_every_name_that_contains_it(
        page, typed, reaches):
    """The filter's own predicate (`name.indexOf(q) !== -1`) applied to the
    rendered data, so this asserts what the browser would actually match."""
    hit = {b.get("data-name") for b in checkboxes(page)
           if typed in (b.get("data-name") or "")}
    missing = [n for n in reaches if n not in hit]
    assert not missing, ("typing %r cannot reach %s (it reaches %s)"
                         % (typed, missing, sorted(hit)))


def test_every_fieldset_data_domain_survives_the_tokenizer(page, cat):
    fieldsets = [f for f in parse(page).fieldsets if f.get("class") == "dom"]
    assert len(fieldsets) == cat.domain_count
    got = [f.get("data-domain") for f in fieldsets]
    assert got == [d.name.lower() for d in cat.domains]


def test_the_picker_rows_and_fieldsets_carry_only_declared_attributes(page):
    """QA-1b's contract, scoped to the picker the way its name reads.

    (The QA file's own version sweeps EVERY <input> on the page, which includes
    the filter field — and the filter field's `autocomplete="off"` and
    `aria-describedby="c1-filter-status"` are written verbatim by the binding
    mockup, line 413. That test therefore cannot go green without deleting spec
    bytes; this one asserts the thing the defect was actually about, and it goes
    red on any unquoted attribute in a picker row or a fieldset.)

    `data-cov` joined the declared set on 2026-08-03 (NL-135 Q1) — the row's
    own coverage state, quoted like everything else here."""
    allowed = {"class", "type", "id", "value", "data-level", "data-name",
               "data-cov", "onchange", "checked"}
    stray = sorted({k for b in checkboxes(page) for k in b if k not in allowed})
    assert not stray, stray
    fs_stray = sorted({k for f in parse(page).fieldsets for k in f
                       if k not in {"class", "data-domain", "hidden"}})
    assert not fs_stray, fs_stray


# ---------------------------------------------------------------------------
# The picker shows the state the reader already owns (QA-7)
# ---------------------------------------------------------------------------

def test_a_saved_topic_renders_checked_and_the_count_line_agrees(fresh, cat):
    """The founding page is reachable repeatedly before the first edition
    publishes — after a stale-server refusal, after a partial write, after any
    reload — and every one of those re-renders used to show 66 empty circles
    over a file that was not empty, under a line stating "Nothing picked yet."
    as a fact. Both channels now derive from the file, server-side, so they are
    true before a byte of script runs."""
    ok, _, _ = commissioning.commission(["Inflation", "Central Bank Policy"],
                                        server.topic_add, cat=cat)
    assert ok
    html = commissioning.render("picker", config.load_sources(),
                                {"state": "idle"}, cat=cat)
    marked = {b["data-name"] for b in checkboxes(html) if "checked" in b}
    assert marked == {"inflation", "central bank policy"}
    assert count_line(html) == "2 %s" % labels.COMMISSION_COUNT_MANY


def test_a_fresh_profile_still_renders_nothing_picked_and_no_checked_box(page):
    marked = [b for b in checkboxes(page) if "checked" in b]
    assert not marked
    assert count_line(page) == labels.COMMISSION_COUNT_NONE


def test_an_interest_that_is_not_a_catalog_entry_is_not_counted(fresh, cat):
    """The founder's four local tags (`NYC Subway` and friends) are real
    interests with no catalog row. They render no checkbox, so they must not
    inflate a count line the reader would then read against 66 visible boxes."""
    assert cat.level_of("NYC Subway") is None
    assert server.topic_add("NYC Subway", "specific")[0]
    html = commissioning.render("picker", config.load_sources(),
                                {"state": "idle"}, cat=cat)
    assert not [b for b in checkboxes(html) if "checked" in b]
    assert count_line(html) == labels.COMMISSION_COUNT_NONE


# ---------------------------------------------------------------------------
# THE TOOTH — this file can detect the defect it exists for
# ---------------------------------------------------------------------------

_PRE_FIX_ROW = (
    '<label class="pick">'
    '<input class="pk" type="checkbox" id="pk-%s" value=%s data-level="%s" '
    'data-name=%s onchange="pickChanged()">'
    '<span class="mark" aria-hidden="true"></span>'
    '<span class="pick-name">%s</span></label>'
)


def test_the_parse_tooth_bites(fresh, cat, monkeypatch):
    """Re-render through the row template EXACTLY as it shipped — unquoted —
    and prove every assertion above goes red on it. A green parse test over a
    page nobody tokenised looks identical to a green parse test over a page
    that is fine; this is the falsifier that tells them apart.

    The measured damage is the QA's number, reproduced here: 54 of 66."""
    # `cov` is accepted and DROPPED on purpose: this stub reproduces the row
    # exactly as it shipped pre-fix, and the pre-fix row had no coverage state.
    # The signature has to track _pick_row's or the monkeypatch stops standing
    # in for it (NL-135 Q1 added the parameter, 2026-08-03).
    def unquoted(name, level, idx, checked=False, cov=None):
        return _PRE_FIX_ROW % (idx, commissioning._attr(name),
                               commissioning._e(level),
                               commissioning._attr(name.lower()),
                               commissioning._e(name))

    monkeypatch.setattr(commissioning, "_pick_row", unquoted)
    html = commissioning.render("picker", config.load_sources(),
                                {"state": "idle"}, cat=cat)
    boxes = checkboxes(html)
    truncated = [(n, b.get("value")) for n, b in zip(cat.names(), boxes)
                 if b.get("value") != n]
    assert len(truncated) == 54, len(truncated)
    assert ("Central Bank Policy", "Central") in truncated

    # and the two consumers, both blind in the same way
    assert not [b for b in boxes if "bank" in (b.get("data-name") or "")]
    invented = {k for b in boxes for k in b
                if k not in {"class", "type", "id", "value", "data-level",
                             "data-name", "onchange", "checked"}}
    assert "conflict" in invented and "policy" in invented

    # and the door refuses the bytes such a page would post
    ok, refusal, written = commissioning.commission(
        [b["value"] for b in boxes], server.topic_add, cat=cat)
    assert ok is False and written == []
    assert refusal == labels.COMMISSION_UNKNOWN_TOPIC
