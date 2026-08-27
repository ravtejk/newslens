"""NL-123 + NL-162-B — the ruled copy slate and the two mounts it needs.

THE RULING: workspace/debates/2026-08-27--newslens--content-2.md §5 (the
Content Lead's slate, delivered on the principal's 2026-08-27 blanket, items
2-3). Eight lines, four of them strings that already shipped and four of them
copy that had no surface to render on. This file is the acceptance for all of
it, per surface.

WHAT THE SLATE RULED, AND WHAT EACH LINE COSTS IN CODE

  1-2  the token GROUP HEADERS `Broad (N)`/`Specific (N)` become
       `Areas (N)`/`Topics (N)`. "Area" is not minted here — it is the shipped
       reader word for a catalog DOMAIN (labels.COMMISSION_COV_ONE/MANY,
       "…sources cover this area."), reused.
  3-4  the empty notes `No broad topics yet`/`No specific topics yet` become
       `No areas yet`/`No topics yet`. The NL-103 row-20 worry that put the
       adjective in-string ("No topics yet" under `Broad (0)` reads as "no
       topics at all") DIES WITH THE ADJECTIVE: distinct nouns cannot collide
       that way, so the empty note is now the plain class noun.
  5-6  `topic_add`'s two refusals stop naming a level the reader is never
       shown. The killed prompt (NL-150, 2026-08-24) was the only thing that
       ever asked; copy naming the answer references a choice that no longer
       exists. `broad -> areas`, `specific -> topics`.
  7    OFF-CATALOG interest removal gets the house confirm card. Catalog-name
       removal stays SILENT — it is re-addable forever (offer = catalog ∪
       edition leg, NL-150), and verified-and-correct is silent. Off-catalog is
       the uncertain act, so it is the one that gets the label.
  8    the two MACHINE-facing strings stay: `level must be broad or specific`
       (protocol; no shipped client has sent a level since NL-150) and
       `added {name} as a {level} topic` (discarded on ok). Pinned below so a
       future sweep cannot "finish the job" through them.

  RIDER, no code: `COMMISSION_PARTIAL_REFUSAL`'s name list stays UNCAPPED.
  Carried invariant, pinned here so the ruling has a witness.

NL-162-B — THE REFUSAL THAT HAD NO MOUNT

The principal's four blocked-thread FKs (thread_deltas / thread_state /
watch_items / thread_closures — the four the NL-77r scope tripwire measures,
tests/test_nl77r_delete_cascade.py::test_the_other_four_memory_fks_still_block_delete)
raise `sqlite3.IntegrityError` out of `memory.delete_thread`'s own DELETE. At
HEAD nothing catches it and nothing renders it: `_api_delete` let it walk into
the generic 500 arm, and `deleteThread()` discarded the response entirely
(closePopup + reload, unconditionally). Net reader experience on those threads:
the popup closes, the page reloads, the thread is still there, and no words are
ever spoken.

THE SCOPE IS THE CHARTER'S (DECISIONS 2026-08-24, NL-162 CHARTERED, option B —
refuse-with-reason): NO schema, NO cascade. The four FKs still block. The
change is that the reader now HEARS about it. `memory.delete_thread` still
RAISES — the catch is at the route, deliberately, so the scope tripwire stays
green on untouched bytes.

BORN-RED PROVENANCE: every test in this file except the ones labelled CARRIED
INVARIANT (born green) was run against a read-only `git archive` export of
3efb705 (PYTHONPATH at that copy's src, `newslens.__file__` asserted inside it)
before the diff existed, and FAILED there. The fail list is transcribed in the
build report, workspace/products/newslens/research/2026-08-27--nl123-162b-build.md.

  LABEL CORRECTION — fixloop 2026-08-27 (QA F-2 / gate FIX-6), stated with
  provenance rather than silently edited, because provenance labels ARE the
  proof-class currency this house runs on:
    · This line said "the four labelled CARRIED INVARIANT". The shipped file
      carried SEVEN such labels. The COUNT was wrong, not the measurement —
      three hands (build, QA, gate) each measured the same HEAD-export split,
      24 failed / 8 passed (+ each runner's own artifact probe).
    · Of those seven labels, SIX were true. `test_writer_tokens_are_never_warned`
      was labelled CARRIED INVARIANT and is in fact BORN RED at HEAD: its
      `_remove_args` regex requires the new four-argument `removeToken(...)`
      call shape, so at HEAD it reads `set()` where it wants `{"false"}`. The
      test was STRONGER than its label; the label now says born red.
    · Two genuinely born-green tests carried NO label
      (`test_a_refused_delete_leaves_the_thread_and_writes_no_tombstone`,
      `test_delete_thread_still_raises_out_of_the_memory_layer`), which this
      docstring's own rule read as a born-red claim. Both are labelled now.
  Net for that 32-test population: EIGHT labelled CARRIED INVARIANT (born
  green), matching its eight measured passes exactly, and one test labelled
  BORN RED whose old label understated its tooth. No assertion was touched by
  this correction — only labels, which is the whole point of it.

  THE SAME FIXLOOP ADDED TWO PINS, taking this file to 34 tests. Both are
  RETRO-PINS: the behaviour each guards is already live, so neither could be
  born red for the usual reason, and each carries a MUTATION receipt instead
  (quoted in their docstrings and in the fixloop report). Measured, not
  assumed, on the same read-only 3efb705 export — the file's whole HEAD split
  transcribed from that run is 25 failed / 9 passed:
    · `test_a_baseline_only_thread_deletes_clean_through_the_route` — passes
      at HEAD. Genuinely born green; labelled CARRIED INVARIANT accordingly.
    · `test_a_non_integrity_failure_never_wears_the_refusal_sentence` — FAILS
      at HEAD, and the reason is worth stating rather than banking as proof:
      its three BEHAVIOURAL assertions (500, ok false, error "database is
      locked") all hold at HEAD, and it dies one line later on
      `labels.THREAD_DELETE_BLOCKED`, a constant this batch mints
      (AttributeError). So it is red at HEAD by REFERENCE, not by behaviour.
      Counting that as born-red currency would be exactly the inflation the
      born-red law exists to stop; its real currency is the mutation receipt.
  Nine tests therefore carry the CARRIED INVARIANT (born green) label, and
  nine passed at the export.

Offline by construction: autouse sandbox (conftest), no network, no key, $0.
"""

from __future__ import annotations

import json
import re
import sqlite3
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from types import SimpleNamespace

import pytest

from newslens import (catalog, commissioning, config, db, labels, memory,
                      server, webui)


# --- world builders --------------------------------------------------------

# Names chosen off the shipped catalog (templates/topic-catalog.yaml) so the
# on/off-catalog split under test is a DATA fact, not a mock: "Geopolitics" is
# a domain, "Medicaid" is a topic, "Zebra Futures" is in neither.
CATALOG_DOMAIN = "Geopolitics"
CATALOG_TOPIC = "Medicaid"
OFF_CATALOG = "Zebra Futures"


def _sources(broad=(), granular=(), *, drop=()):
    """Write a sandbox sources.yaml. `drop` omits an interests KEY entirely —
    the state refusal #5 answers (`_find_interest_list` returns start < 0)."""
    out = ["sources:",
           "  - name: Example",
           "    rss_url: https://example.invalid/feed",
           "interests:"]
    if "broad" not in drop:
        out.append("  broad:")
        out.extend(f"    - {n}" for n in broad)
    if "granular" not in drop:
        out.append("  granular:")
        out.extend(f"    - {n}" for n in granular)
    config.paths.SOURCES_FILE.write_text("\n".join(out) + "\n", encoding="utf-8")
    return config.paths.SOURCES_FILE


def _con():
    db.migrate()
    return db.connect()


def _thread(con, topic, status="active"):
    with con:
        con.execute("INSERT INTO memory (topic, status) VALUES (?, ?)",
                    (topic, status))
    return con.execute("SELECT id FROM memory WHERE topic = ?",
                       (topic,)).fetchone()["id"]


def _dismiss(con, tid):
    with con:
        con.execute("UPDATE memory SET status='dismissed_user' WHERE id = ?",
                    (tid,))


# The four FK legs the NL-77r tripwire measures, verbatim from that file so the
# two pins cannot drift apart.
BLOCKING_CHILDREN = {
    "thread_deltas": ("INSERT INTO thread_deltas (thread_id, edition_date,"
                      " verdict, what_happened, significance)"
                      " VALUES (?, '2026-07-16', 'advances', 'x', 'y')"),
    "thread_state": ("INSERT INTO thread_state (thread_id, as_of_date,"
                     " state_text) VALUES (?, '2026-07-16', 's')"),
    "watch_items": ("INSERT INTO watch_items (thread_id, edition_date,"
                    " observable) VALUES (?, '2026-07-16', 'o')"),
    "thread_closures": ("INSERT INTO thread_closures (thread_id,"
                        " edition_date) VALUES (?, '2026-07-16')"),
}


def _blocked_thread(topic, child="thread_deltas"):
    con = _con()
    try:
        tid = _thread(con, topic)
        with con:
            con.execute(BLOCKING_CHILDREN[child], (tid,))
        _dismiss(con, tid)
        return tid
    finally:
        con.close()


def _baselined_thread(topic):
    """A dismissed thread whose ONLY memory(id) child is a `thread_baselines`
    row — the FIFTH FK, and the one that does not block.

    This is the GO path the bound comment at server.py's `_api_delete` names:
    0027 recreated this FK ON DELETE CASCADE, so the baseline row is removed
    with the thread instead of refusing it. Deliberately built with no row from
    BLOCKING_CHILDREN, so the delete under test is unblocked by construction."""
    con = _con()
    try:
        tid = _thread(con, topic)
        with con:
            con.execute("INSERT INTO thread_baselines (thread_id, as_of_date,"
                        " status) VALUES (?, '2026-07-16', 'ready')", (tid,))
        _dismiss(con, tid)
        return tid
    finally:
        con.close()


# --- render readers --------------------------------------------------------

def _group_names(html):
    return re.findall(r'<p class="token-group-name">([^<]*)</p>', html)


def _empty_notes(html):
    return re.findall(r'<p class="empty-note">([^<]*)</p>', html)


def _remove_args(html):
    """{(kind, name): warn-literal} read off the rendered onclick attributes.

    The attribute is `_e(_js_str(v))` — JSON quoting, then HTML escaping — so
    the quotes arrive as &quot; and this regex reads what a browser reads."""
    return {(m.group(1), m.group(2)): m.group(3) for m in re.finditer(
        r'removeToken\(&quot;(\w+)&quot;, &quot;(.*?)&quot;, this, (true|false)\)',
        html)}


def _js_function(name):
    """The body of one JS function out of webui.JS, so a pin cannot be
    satisfied by a coincidence elsewhere in a 2000-line blob."""
    m = re.search(r"^function %s\(.*?\n^}" % re.escape(name),
                  webui.JS, re.M | re.S)
    assert m, f"{name}() not found in webui.JS"
    return m.group(0)


# --- live server -----------------------------------------------------------

@pytest.fixture
def ui(tmp_paths, monkeypatch):
    db.migrate()
    monkeypatch.setattr(server, "GEN_JOB", server._GenJob())
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    box = SimpleNamespace(base=f"http://127.0.0.1:{httpd.server_address[1]}")
    yield box
    httpd.shutdown()
    httpd.server_close()


def post(ui, path, payload):
    req = urllib.request.Request(
        ui.base + path, data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.getcode(), json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8")
        try:
            return exc.code, json.loads(body)
        except ValueError:
            return exc.code, {"raw": body}


# ===========================================================================
# SLATE 1-2 — the group headers
# ===========================================================================

def test_the_group_headers_read_areas_and_topics(tmp_paths):
    """Slate lines 1-2. Asserted as the EXACT ordered list of group names, so a
    rename that also reordered or dropped a group cannot pass."""
    _sources(broad=[CATALOG_DOMAIN], granular=[CATALOG_TOPIC, OFF_CATALOG])
    con = _con()
    try:
        html = server._render_following(con)
    finally:
        con.close()
    assert _group_names(html) == ["Areas (1)", "Topics (2)"]


def test_the_counts_still_come_from_the_groups_they_name(tmp_paths):
    """BORN RED on the noun; the COUNT half is a carried invariant folded into
    the same assertion (measured at HEAD: `['Broad (3)', 'Specific (1)']` — the
    counts were already right). Here because the rename touches the label
    expression the count is interpolated next to, and a hand that re-wrote the
    f-string could cross the two lists without any other test noticing."""
    _sources(broad=["A", "B", "C"], granular=["D"])
    con = _con()
    try:
        html = server._render_following(con)
    finally:
        con.close()
    assert _group_names(html) == ["Areas (3)", "Topics (1)"]


# ===========================================================================
# SLATE 3-4 — the empty notes
# ===========================================================================

def test_the_empty_notes_are_the_plain_class_nouns(tmp_paths):
    """Slate lines 3-4. The page's OTHER two empty notes (threads, writers)
    ride along in the same assertion as carried invariants: neither is in this
    slate, and an over-broad sweep of the word "topics" would move them.
    Measured at HEAD: `['No threads yet', 'No broad topics yet',
    'No specific topics yet', 'No writers yet']`."""
    _sources()
    con = _con()
    try:
        html = server._render_following(con)
    finally:
        con.close()
    assert _empty_notes(html) == ["No threads yet", "No areas yet",
                                  "No topics yet", "No writers yet"]


def test_a_populated_group_still_renders_no_empty_note(tmp_paths):
    """CARRIED INVARIANT (born green) — the empty note is gated on emptiness,
    not composed unconditionally. Neither topic group contributes a note here;
    threads and writers still do."""
    _sources(broad=[CATALOG_DOMAIN], granular=[CATALOG_TOPIC])
    con = _con()
    try:
        html = server._render_following(con)
    finally:
        con.close()
    assert _empty_notes(html) == ["No threads yet", "No writers yet"]


# ===========================================================================
# SLATE 5-6 — the topic_add refusals
# ===========================================================================

def test_the_missing_section_refusal_says_areas(tmp_paths):
    """Slate line 5, broad arm. Explicit level, so the catalog's inference
    cannot pick the arm for us and hide a mis-mapped noun."""
    _sources(drop=("broad",))
    ok, msg = server.topic_add(OFF_CATALOG, "broad")
    assert not ok
    assert msg == "Didn’t add it — your sources file has no areas section."


def test_the_missing_section_refusal_says_topics(tmp_paths):
    """Slate line 5, specific arm."""
    _sources(drop=("granular",))
    ok, msg = server.topic_add(OFF_CATALOG, "specific")
    assert not ok
    assert msg == "Didn’t add it — your sources file has no topics section."


def test_the_duplicate_refusal_says_areas(tmp_paths):
    """Slate line 6, broad arm."""
    _sources(broad=[OFF_CATALOG])
    ok, msg = server.topic_add(OFF_CATALOG, "broad")
    assert not ok
    assert msg == f"Didn’t add it — {OFF_CATALOG} is already in your areas."


def test_the_duplicate_refusal_says_topics(tmp_paths):
    """Slate line 6, specific arm — and the arm a reader actually reaches
    today, since an absent level infers `specific` for anything off-catalog."""
    _sources(granular=[OFF_CATALOG])
    ok, msg = server.topic_add(OFF_CATALOG)
    assert not ok
    assert msg == f"Didn’t add it — {OFF_CATALOG} is already in your topics."


def test_no_topic_add_refusal_speaks_a_dead_rung_word(tmp_paths):
    """The vocabulary law applied to the whole refusal set at once
    (DECISIONS 2026-07-28 §2 — broad/granular/specific are dead as READER
    words). Sweeps every reader-reachable refusal this door can produce."""
    dead = re.compile(r"\b(broad|granular|specific)\b", re.I)
    _sources(drop=("broad",))
    reached = [server.topic_add(OFF_CATALOG, "broad")[1]]
    _sources(drop=("granular",))
    reached.append(server.topic_add(OFF_CATALOG, "specific")[1])
    _sources(broad=[OFF_CATALOG], granular=[CATALOG_TOPIC])
    reached.append(server.topic_add(OFF_CATALOG, "broad")[1])
    reached.append(server.topic_add(CATALOG_TOPIC, "specific")[1])
    assert [m for m in reached if dead.search(m)] == []


# ===========================================================================
# SLATE 8 — the machine-facing strings are NOT swept
# ===========================================================================

def test_the_level_protocol_string_is_untouched(tmp_paths):
    """CARRIED INVARIANT (born green), and the point of pinning it: slate line
    8 rules these two OUT of the sweep. This one is a wire-protocol answer no
    shipped client can reach (nothing has posted a level since NL-150), and a
    future rung sweep that "finishes the job" through it goes red here."""
    _sources()
    ok, msg = server.topic_add(OFF_CATALOG, "sideways")
    assert (ok, msg) == (False, "level must be broad or specific")


def test_the_discarded_ok_detail_is_untouched(tmp_paths):
    """CARRIED INVARIANT (born green). Slate line 8's second string: the
    success detail, discarded by addTopic on ok, diagnostics register."""
    _sources()
    ok, msg = server.topic_add(OFF_CATALOG)
    assert (ok, msg) == (True, f"added {OFF_CATALOG} as a specific topic")


# ===========================================================================
# SLATE 5 (rider) — PARTIAL_REFUSAL stays uncapped
# ===========================================================================

def test_the_partial_refusal_list_is_uncapped(tmp_paths):
    """CARRIED INVARIANT (born green) — RULED, not changed (slate line 5).
    Twelve names all survive into the sentence: no cap, no "and N more", no
    count standing in for the names. The refusal's job is to tell the reader
    which picks landed so they can re-pick the rest; a count forces a diff
    against the Following page instead."""
    names = [f"Topic {i:02d}" for i in range(12)]
    line = labels.COMMISSION_PARTIAL_REFUSAL.format(
        topics=commissioning._name_list(names))
    for n in names:
        assert n in line
    assert not re.search(r"\b\d+\s+more\b", line)
    assert "…" not in line and "..." not in line


# ===========================================================================
# SLATE 7 — the warn-arm confirm card (off-catalog removals only)
# ===========================================================================

def test_an_off_catalog_token_arms_the_confirm_and_a_catalog_one_does_not(
        tmp_paths):
    """Slate line 7, BOTH DIRECTIONS in one render — the asymmetry IS the
    design (catalog names are re-addable forever, so their removal is
    verified-and-correct and stays silent). Both levels carry a catalog name so
    the gating is proven to key on the CATALOG, not on which group a token sat
    in."""
    _sources(broad=[CATALOG_DOMAIN], granular=[CATALOG_TOPIC, OFF_CATALOG])
    con = _con()
    try:
        args = _remove_args(server._render_following(con))
    finally:
        con.close()
    assert args[("topic", OFF_CATALOG)] == "true"
    assert args[("topic", CATALOG_DOMAIN)] == "false"
    assert args[("topic", CATALOG_TOPIC)] == "false"


def test_a_mis_cased_catalog_name_is_still_a_catalog_name(tmp_paths):
    """The gate is `catalog.resolve`, the same case-blind lookup the write door
    uses (`_catalog_write_identity`) — not a string compare against the
    catalog's spelling. A hand-edited `medicaid` in his file is on-catalog and
    must not be warned over."""
    _sources(granular=[CATALOG_TOPIC.lower()])
    con = _con()
    try:
        args = _remove_args(server._render_following(con))
    finally:
        con.close()
    assert args[("topic", CATALOG_TOPIC.lower())] == "false"


def test_writer_tokens_are_never_warned(tmp_paths):
    """BORN RED (measured at the HEAD export; label corrected in the 2026-08-27
    fixloop — it shipped mislabelled as a carried invariant, deliberately
    written in lower case here so that neither a reader nor a grep counts this
    docstring among the labelled ones).

    The SUBJECT is a surface the slate does not reach: `token()` is shared with
    the writers list, and writers have their own removal story, so "a writer is
    never warned" is a carried invariant of the PRODUCT. The TEST is not: it
    reads the warn flag through `_remove_args`, whose regex requires the new
    four-argument `removeToken(kind, name, this, warn)` shape this batch
    introduces. At HEAD the third argument does not exist, nothing matches, and
    the assertion reads `set() == {"false"}` — red. The test is stronger than
    its old label claimed, which is why the label, not the test, moved."""
    config.paths.SOURCES_FILE.write_text(
        "sources:\n"
        "  - name: Example\n"
        "    rss_url: https://example.invalid/feed\n"
        "  - name: Someone\n"
        "    rss_url: https://example.invalid/w\n"
        "    followed_analyst: true\n"
        "interests:\n  broad:\n  granular:\n", encoding="utf-8")
    con = _con()
    try:
        html = server._render_following(con)
    finally:
        con.close()
    # Warn-agnostic first, so "the fixture rendered no writer at all" can never
    # be mistaken for "the writer was not warned".
    assert re.search(r'removeToken\(&quot;writer&quot;', html), \
        "no writer token rendered — fixture did not bite"
    writers = {k: v for k, v in _remove_args(html).items() if k[0] == "writer"}
    assert set(writers.values()) == {"false"}


def test_a_broken_catalog_warns_over_nothing_and_still_renders(tmp_paths,
                                                              monkeypatch):
    """The NL-150 degrade law, extended to this gate: a catalog that will not
    load must not take the Following page down, and must not mint warns it
    cannot justify. Unknown-catalog degrades to today's silent removal."""
    _sources(broad=[CATALOG_DOMAIN], granular=[OFF_CATALOG])

    def boom(*a, **k):
        raise catalog.CatalogError("planted")

    monkeypatch.setattr(catalog, "load", boom)
    con = _con()
    try:
        html = server._render_following(con)
    finally:
        con.close()
    assert set(_remove_args(html).values()) == {"false"}
    assert _group_names(html) == ["Areas (1)", "Topics (1)"]


def test_the_remove_confirm_card_carries_the_ruled_copy():
    """Slate line 7's exact strings, on the house confirm-card idiom (scrim +
    card + title-with-name + one fact + Cancel + act verb) — the same classes
    add-topic / add-writer / schedule-hour / delete-confirm all use. No new
    component and no new CSS."""
    p = webui.POPUPS
    # The house idiom, asserted on the opening tag itself (the scrim class and
    # the id are one string, so a card that skipped the shared class cannot
    # pass), then on the card body.
    opener = '<div class="popup-scrim" id="popup-remove-topic" role="dialog"'
    assert opener in p
    card = p.split(opener)[1].split("</div>\n</div>")[0]
    assert '<div class="popup-card">' in card
    assert 'Remove “<span id="remove-topic-name"></span>”?' in card
    assert ('is suggested again only while the latest edition still mentions '
            'it — after that, it won’t come back on its own.') in card
    assert '<div class="popup-actions">' in card
    assert '>Cancel</button>' in card
    assert '>Remove</button>' in card


def test_the_confirm_card_body_names_the_topic(tmp_paths):
    """The body is `Once removed, {name} is suggested again only while…` — the
    name is interpolated, not a pronoun (NL-103 row 11: a receipt or a warn
    never takes a pronoun object). Proven by the element the client fills."""
    card = webui.POPUPS.split('id="popup-remove-topic"')[1]
    assert 'id="remove-topic-name-body"' in card
    assert "Once removed, " in card
    fn = _js_function("removeToken")
    assert "remove-topic-name-body" in fn


def test_remove_token_branches_on_the_warn_flag():
    """The mount's wiring, function-scoped: the warn arm opens the card and
    returns; the silent arm goes straight to the API. A pin on the whole blob
    could be satisfied by an unrelated line."""
    fn = _js_function("removeToken")
    assert "popup-remove-topic" in fn
    assert "/api/" not in fn, ("removeToken must delegate the request — the "
                              "warn arm has to be able to return before it")
    doit = _js_function("doRemoveToken")
    assert "'/api/' + kind + '/remove'" in doit


def test_every_id_the_remove_flow_writes_to_exists_in_the_popups():
    """CROSS-FILE WIRING (the class of bug a source-text pin exists to catch):
    every getElementById the two remove-flow functions name must be an id the
    shipped markup actually carries. A typo here is silent in the browser —
    the popup opens with an empty name, or nothing opens at all."""
    ids = set()
    for fn in ("removeToken", "confirmRemoveTopic"):
        ids |= set(re.findall(r"getElementById\('([^']+)'\)", _js_function(fn)))
    ids |= set(re.findall(r"[Oo]penPopup\('([^']+)'\)",
                          _js_function("removeToken")))
    assert ids, "no ids read out of the remove flow — extraction broke"
    for i in sorted(ids):
        assert f'id="{i}"' in webui.POPUPS, f"{i} is written by JS but not rendered"


# ===========================================================================
# NL-162-B — the refusal, end to end through the shipped door
# ===========================================================================

def test_a_blocked_delete_answers_the_reader_with_the_ruled_refusal(ui):
    """THE ROUND TRIP. Real server, real FK, real POST — the whole path the
    reader's tap takes.

    HEAD COUNTERFACTUAL (what this measured before the diff): the
    IntegrityError walked out of `_api_delete` into do_POST's generic arm, so
    this answered HTTP 500 with `{"ok": false, "error": "FOREIGN KEY
    constraint failed"}` — and the client discarded even that."""
    _blocked_thread("Blocked Thread")
    code, body = post(ui, "/api/thread/delete", {"topic": "Blocked Thread"})
    assert code == 200
    assert body["ok"] is False
    assert body["error"] == (
        "Didn’t delete it — this thread carries dated facts recorded from "
        "your editions, and the record keeps them. It stays dismissed; "
        "nothing new is tracked.")


@pytest.mark.parametrize("child", sorted(BLOCKING_CHILDREN))
def test_all_four_blocking_fks_reach_the_same_refusal(ui, child):
    """SCOPE, MEASURED FROM THE ROUTE. The NL-77r tripwire proves the four FKs
    still block at `memory.delete_thread`; this proves the reader hears the
    same sentence from each of them. The charter's option (B) is exactly this:
    nothing about the blocking changes, only the silence."""
    _blocked_thread(f"Blocked by {child}", child=child)
    code, body = post(ui, "/api/thread/delete", {"topic": f"Blocked by {child}"})
    assert (code, body["ok"]) == (200, False)
    assert body["error"].startswith("Didn’t delete it —")


def test_a_refused_delete_leaves_the_thread_and_writes_no_tombstone(ui):
    """CARRIED INVARIANT (born green) — label added in the 2026-08-27 fixloop
    (QA F-2 ii): this test shipped unlabelled, which this file's own docstring
    rule read as a born-red claim it never was. HEAD refused the delete too, by
    walking into the generic 500 arm; the thread survived and no tombstone was
    written there either. What this batch changes is the SENTENCE, not the
    rollback — and the rollback is exactly what this pin guards.

    The refusal is a REFUSAL, not a partial act. `delete_thread` appends the
    NL-81 tombstone in the same transaction as the DELETE precisely so a failed
    delete cannot leave a deletion RECORD behind; catching at the route must
    not disturb that rollback."""
    tid = _blocked_thread("Still Here")
    post(ui, "/api/thread/delete", {"topic": "Still Here"})
    con = _con()
    try:
        assert con.execute("SELECT 1 FROM memory WHERE id = ?",
                           (tid,)).fetchone() is not None
        assert con.execute(
            "SELECT count(*) FROM memory_tombstones WHERE thread_id = ?",
            (tid,)).fetchone()[0] == 0
    finally:
        con.close()


def test_an_unblocked_delete_still_succeeds(ui):
    """CARRIED INVARIANT (born green). Successful deletes are untouched — the
    catch is narrow enough that the happy path never enters it."""
    con = _con()
    try:
        tid = _thread(con, "Deletable")
        _dismiss(con, tid)
    finally:
        con.close()
    code, body = post(ui, "/api/thread/delete", {"topic": "Deletable"})
    assert (code, body["ok"]) == (200, True)
    con = _con()
    try:
        assert con.execute("SELECT 1 FROM memory WHERE id = ?",
                           (tid,)).fetchone() is None
    finally:
        con.close()


def test_the_dismissed_first_refusal_is_untouched(ui):
    """CARRIED INVARIANT (born green). `delete_thread`'s OWN refusals still
    ride back unchanged — the new arm catches an exception, it does not
    rewrite the (False, msg) returns above it."""
    con = _con()
    try:
        _thread(con, "Still Active")
    finally:
        con.close()
    code, body = post(ui, "/api/thread/delete", {"topic": "Still Active"})
    assert (code, body["ok"]) == (200, False)
    assert body["error"] == ("dismiss the thread first — delete is only "
                             "offered on stopped follows")


def test_delete_thread_still_raises_out_of_the_memory_layer(tmp_paths):
    """CARRIED INVARIANT (born green) — label added in the 2026-08-27 fixloop
    (QA F-2 ii); it shipped unlabelled. Of course it is born green: it asserts
    the memory layer STILL raises, and HEAD is the state where nothing catches
    that raise at all. The pin's value is forward-facing, not born-red.

    THE SCOPE BOUND, RESTATED AT THIS FILE'S OWN EDGE. The catch lives at
    the ROUTE, not in `memory.delete_thread` — because
    tests/test_nl77r_delete_cascade.py::test_the_other_four_memory_fks_still_block_delete
    measures the raise, and this batch is chartered to leave that untouched.
    If someone ever moves the catch down a layer, both files go red together
    and the record gets dragged along."""
    _blocked_thread("Raises Below")
    con = _con()
    try:
        with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY"):
            memory.delete_thread(con, "Raises Below")
    finally:
        con.close()


def test_a_non_integrity_failure_never_wears_the_refusal_sentence(ui,
                                                                 monkeypatch):
    """THE CATCH'S NARROWNESS, PINNED (QA F-1 / gate FIX-1, 2026-08-27 fixloop).

    MUTATION-PROVEN RETRO-PIN. What it guards — that the catch at `_api_delete`
    is `except sqlite3.IntegrityError` and not one word wider — is already true
    at these bytes and vacuously true at HEAD, where there is no catch at all.
    So its currency is the mutation receipt, not a born-red one: with the catch
    widened to `except Exception:` this goes red at `assert 200 == 500` (fixloop
    leg, off-tree at final bytes: exactly ONE red across 102 tests — this file,
    test_nl77r_delete_cascade.py and ALL of test_server.py — the same net QA's
    Q2 and the gate's G4 plants left entirely green).

    HONEST NOTE ON ITS HEAD BEHAVIOUR, because this file just had to correct a
    label: run against the 3efb705 export it FAILS, and that is NOT born-red
    currency. Its three behavioural assertions below (500, ok false, the error
    text) all hold at HEAD; it dies on the NEXT line, at
    `labels.THREAD_DELETE_BLOCKED`, a constant this batch introduces
    (AttributeError). Red by reference, not by behaviour.

    WHY IT IS WORTH A TEST. Nothing else in this file or in test_server.py
    notices a widened catch — QA's Q2 plant and the gate's G4 plant each left
    100/100 targeted tests GREEN. That is the NL-139 shape: enforcement dead,
    suite green. And the damage a widening does here is specific and worse than
    the silence this batch kills. `delete_thread` can fail for reasons that have
    nothing to do with the four blocking FKs — a locked database raises
    `sqlite3.OperationalError` (measured), a programming error raises whatever
    it raises — and a wide catch would answer every one of them with the ruled
    refusal sentence: "this thread carries dated facts…". That sentence would
    then be a FALSE EXPLANATION of a defect, told confidently to the reader, on
    the exact surface this batch built to stop lying to them. An honest 500 the
    reader can report beats a fluent wrong reason.

    The injected failure is `OperationalError` rather than something exotic
    because it is the real one: it is what a concurrent writer produces, and QA
    measured it arriving here from a genuinely locked database."""
    def _locked(con, topic):
        raise sqlite3.OperationalError("database is locked")
    con = _con()
    try:
        tid = _thread(con, "Locked Out")
        _dismiss(con, tid)
    finally:
        con.close()
    # Deliberately a thread that WOULD delete cleanly: the only reason this
    # call fails is the injected non-Integrity failure, so a green here cannot
    # be explained by the world.
    monkeypatch.setattr(memory, "delete_thread", _locked)
    code, body = post(ui, "/api/thread/delete", {"topic": "Locked Out"})
    assert code == 500, "a non-IntegrityError must NOT be caught by the refusal arm"
    assert body["ok"] is False
    assert body["error"] == "database is locked"
    assert body["error"] != labels.THREAD_DELETE_BLOCKED
    assert not body["error"].startswith("Didn’t delete it"), \
        "the refusal sentence is being used as a costume for an unrelated failure"


def test_a_baseline_only_thread_deletes_clean_through_the_route(ui):
    """THE CASCADE GO PATH, PINNED AT THE ROUTE (QA F-3c / gate FIX-4).

    CARRIED INVARIANT (born green — MEASURED at the 3efb705 export, not
    assumed) + MUTATION-PROVEN. Green at HEAD because 0027 already shipped: a
    baseline-only thread deletes cleanly there too, and 0027's own test file
    pins that at the MEMORY layer. What had no pin was the READER'S DOOR — that
    the same clean delete survives the whole route, including the new catch.

    TWO mutation receipts, both off-tree at final bytes (fixloop report):
      · the gate's specified plant, `delete_thread` → IntegrityError:
        `assert (200, False) == (200, True)`;
      · the truer regression, 0027's `ON DELETE CASCADE` reverted in the
        migration so the baseline FK blocks for real: same red. That second one
        is the case this pin exists for — a lawful delete refused, wearing the
        refusal sentence as though the reader's thread held dated facts.

    WHY THE ROUTE NEEDS ITS OWN: `thread_baselines` is the fifth FK into
    memory(id) and the only non-blocking one. If a future migration reverted
    0027's cascade, or the WHEN guard on its append-only trigger were dropped,
    or the catch above mis-fired, this lawful delete would start raising
    IntegrityError — and the new arm would catch it and tell the reader their
    thread "carries dated facts recorded from your editions". A regression
    would arrive already dressed in a sentence that sounds correct. This pin is
    what makes that loud instead."""
    tid = _baselined_thread("Baselined Only")
    con = _con()
    try:
        assert con.execute(
            "SELECT count(*) FROM thread_baselines WHERE thread_id = ?",
            (tid,)).fetchone()[0] == 1, "fixture did not bite — no baseline row"
    finally:
        con.close()
    code, body = post(ui, "/api/thread/delete", {"topic": "Baselined Only"})
    assert (code, body["ok"]) == (200, True)
    con = _con()
    try:
        assert con.execute("SELECT 1 FROM memory WHERE id = ?",
                           (tid,)).fetchone() is None, "the thread is gone"
        assert con.execute(
            "SELECT count(*) FROM thread_baselines WHERE thread_id = ?",
            (tid,)).fetchone()[0] == 0, "0027's cascade fired"
        assert con.execute(
            "SELECT count(*) FROM memory_tombstones WHERE thread_id = ?",
            (tid,)).fetchone()[0] == 1, "exactly one deletion record"
    finally:
        con.close()


def test_the_delete_confirm_card_has_a_status_line():
    """The mount the string needs to exist at all: `popup-delete-confirm` had
    NO status element at HEAD, which is why the copy could not simply be
    written. House pattern — `popup-status err`, aria-live polite."""
    card = webui.POPUPS.split('id="popup-delete-confirm"')[1]
    assert 'id="delete-thread-status"' in card
    assert 'class="popup-status err"' in card
    assert 'aria-live="polite"' in card
    assert ("This removes it permanently from your list. Past editions that "
            "mentioned it are unaffected.") in card, \
        "the existing confirm body is still true and stays (slate line 7 note)"


def test_delete_thread_stops_discarding_the_response():
    """HEAD read exactly this, and it is the whole defect:

        function deleteThread() {
          api('/api/thread/delete', {topic: deleteTopic},
              function () { closePopup('popup-delete-confirm');
                            reloadPreservingView(); });
        }

    — a callback that takes no argument cannot branch on the answer. The popup
    must now stay OPEN on a refusal, or the sentence flashes and dies with the
    reload."""
    fn = _js_function("deleteThread")
    assert "d.error" in fn
    assert "delete-thread-status" in fn
    ok_arm = fn.split("d.ok")[1]
    assert "closePopup" in ok_arm.split("else")[0]
    refusal_arm = fn.split("else")[1]
    assert "closePopup" not in refusal_arm and "reload" not in refusal_arm


def test_every_id_the_delete_flow_writes_to_exists_in_the_popups():
    """CROSS-FILE WIRING, same class as the remove flow's pin."""
    ids = set()
    for fn in ("deleteThread", "openDeleteConfirm"):
        ids |= set(re.findall(r"getElementById\('([^']+)'\)", _js_function(fn)))
    assert "delete-thread-status" in ids
    for i in sorted(ids):
        assert f'id="{i}"' in webui.POPUPS, f"{i} is written by JS but not rendered"
