"""NL-17-M1c — UNIFY EVERY FOLLOW SURFACE: the implementer's wiring proofs.

The milestone: ONE noun (thread), ONE follow-line component, FOUR mounts, and
NO SILENT REFUSALS. Three inconsistent follow treatments shipped before this
diff — a card picker with its own vocabulary, a Following row that could only
disclose (never act), and a thread page carrying the lifecycle verbs — and the
client swallowed four separate refusal payloads without a word.

Binding artifacts: `design/mockup-v11.html` (blessed at his re-glance 2026-07-27),
the v11 content finals (`research/2026-07-27--v11-content-pass.md`, strings
verbatim), his five gate rulings (DECISIONS "MOCKUP-V11 GATE RULINGS"), and the
ratified register (`design/TAXONOMY-PROPOSAL.md`).

WHAT THIS FILE OWNS: the wiring proofs + the two MECHANICAL TEETH the gate
asked for. QA owns the real-browser DoD (keyboard, announcement order, the ~3s
revert as experienced).

Offline by construction: no network, no real key, autouse sandbox (conftest).
Every resolver is stubbed, so a regression shows up as a stub call that should
not have happened — never as real spend.
"""

from __future__ import annotations

import inspect
import json
import re
import sqlite3
import types
from pathlib import Path

import pytest

from newslens import db, follow_altitude as fa, labels, memory, paths, server, webui


# ---------------------------------------------------------------------------
# harness — the established _FollowHandler double
# ---------------------------------------------------------------------------

class _FollowHandler:
    _topic_arg = server.Handler._topic_arg
    _with_memory = server.Handler._with_memory
    _ref_id_for = server.Handler._ref_id_for
    _commit_altitude = server.Handler._commit_altitude
    _api_follow_seed = server.Handler._api_follow_seed
    _api_follow_settle = server.Handler._api_follow_settle
    _seed_thread = server.Handler._seed_thread
    _settle_onto = server.Handler._settle_onto
    _api_follow_at = server.Handler._api_follow_at
    _api_dismiss = server.Handler._api_dismiss

    def __init__(self):
        self.sent = []

    def _send_json(self, obj, status=200):
        self.sent.append((obj, status))
        return obj


_ENTITY = dict(confidence="high", altitude="entity", primary_entity="Volkswagen",
               disclosure="Volkswagen (company)", alt_label="Volkswagen job cuts")
_LOW = dict(_ENTITY, confidence="low")

STORY = "Volkswagen job cuts"
HEADLINE = "Volkswagen plans significant job cuts"
TIGHT_CAP = "0.0005"


def _quiet_memory(monkeypatch):
    monkeypatch.setattr(memory, "sync_memory",
                        lambda con, **kw: memory.SyncResult())
    monkeypatch.setattr(memory, "write_memory_file", lambda con: None)


def _resolver(calls, spec=_ENTITY):
    def _resolve(thread, **kwargs):
        calls.append(getattr(thread, "topic", None))
        return types.SimpleNamespace(**spec)
    return _resolve


def _tap(h, topic=STORY, origin=HEADLINE):
    body = {"topic": topic, "origin": origin}
    h._api_follow_seed(dict(body))
    seeded = h.sent[-1][0]
    if seeded.get("ok") is False or seeded.get("seeded") is not True:
        return
    h._api_follow_settle(dict(body, topic_current=seeded.get("topic")))


def _rows(where="status = 'active'"):
    con = db.connect(paths.DB_PATH)
    try:
        return [dict(r) for r in con.execute(
            f"SELECT * FROM memory WHERE {where} ORDER BY id")]
    finally:
        con.close()


def _fn(name: str) -> str:
    """The source of one JS function from webui.JS (the flat function table)."""
    i = webui.JS.index("function " + name + "(")
    j = webui.JS.find("\nfunction ", i + 1)
    return webui.JS[i:(j if j != -1 else len(webui.JS))]


def _mount_markup(mount: str) -> str:
    """One rendered slot per mount, for the cross-mount identity checks."""
    db.migrate(db_path=paths.DB_PATH)
    con = db.connect(paths.DB_PATH)
    try:
        memory.add_thread_at_altitude(
            con, "Volkswagen", altitude="entity", disclosure="Volkswagen (company)",
            alt_label=STORY, source="auto", origin_story=STORY)
        if mount == "card":
            return server._follow_control(
                {"headline": HEADLINE}, {"story_title": STORY}, [],
                server._active_topics_lower(con), "2026-07-27", slug="s", con=con)
        if mount == "deep":
            return server._deep_follow_line(con, {"story_title": STORY},
                                            HEADLINE, "2026-07-27", "story-0")
        row = dict(server._following_rows(con)["active"][0])
        return server._following_row_follow_line(row)
    finally:
        con.close()


def _follow_block() -> str:
    """The client's follow-line block — flEsc through the last fl* renderer."""
    return webui.JS[webui.JS.index("function flEsc("):
                    webui.JS.index("function openDeepView(")]


def _js_code(block: str) -> str:
    """A JS block with its comments stripped — so a structural pin reads the
    CODE, not the prose explaining it (the comment-satisfiable class: R4 of fix
    loop 2 proved a pin can pass on a doc comment alone)."""
    out = re.sub(r"/\*.*?\*/", " ", block, flags=re.S)
    return re.sub(r"//[^\n]*", " ", out)


def _reader_layer(page: str) -> str:
    """What a reader (or a screen reader) can actually RECEIVE from a page: the
    text nodes plus the announced attributes. The <script>/<style> payloads, the
    data-* / class / on* machine layer and the DOM ids are code — they are never
    rendered and never announced, and a vocabulary sweep that reads them is
    grepping the implementation, not the copy."""
    out = re.sub(r"<script\b.*?</script>", " ", page, flags=re.S | re.I)
    out = re.sub(r"<style\b.*?</style>", " ", out, flags=re.S | re.I)
    out = re.sub(r'\s(?:data-[a-z-]+|class|id|href|src|on[a-z]+)="[^"]*"', " ", out)
    return re.sub(r"<!--.*?-->", " ", out, flags=re.S)


# ===========================================================================
# A — THE THREAD MODEL: one noun, an instant tap, an invisible settle
# ===========================================================================

def test_a1_the_cta_names_the_thread():
    """His ruling (item 2 / NL-103 row 12): the object seat takes THREAD.
    BORN-RED: the shipped CTA is "○ Follow this story"."""
    assert labels.FOLLOW_THREAD_INACTIVE == "○ Follow this thread"
    assert not hasattr(labels, "FOLLOW_STORY_INACTIVE")


def test_a2_the_tap_commits_before_anything_external_is_consulted(monkeypatch):
    """THE TAP COMMITS INSTANTLY, ALWAYS. The seed route makes ZERO external
    calls and reaches no budget gate — proven behaviourally (the tripwire
    resolver is never entered) and structurally (neither call site exists in
    the route's source). BORN-RED: at HEAD the tap's only route resolved first
    and could commit nothing at all."""
    db.migrate(db_path=paths.DB_PATH)
    _quiet_memory(monkeypatch)
    calls = []
    monkeypatch.setattr(fa, "resolve_altitude", _resolver(calls))

    h = _FollowHandler()
    h._api_follow_seed({"topic": STORY, "origin": HEADLINE})

    assert calls == []                                  # nothing consulted
    payload, status = h.sent[-1]
    assert status == 200 and payload["state"] == "committed"
    assert payload["altitude"] == "narrow" and payload["seeded"] is True
    rows = _rows()
    assert len(rows) == 1 and rows[0]["altitude_source"] == "seed"
    src = inspect.getsource(server.Handler._api_follow_seed)
    assert "resolve_altitude" not in src and "resolve_cost_gate" not in src


def test_a3_the_settle_moves_the_seeded_thread_never_creates_a_second(monkeypatch):
    """A confident settle RE-AIMS the thread the tap seeded — one thread, one
    identity. BORN-RED: at HEAD there was no seed to move, so the concept of a
    second row for one story is exactly the QA double-follow this guards."""
    db.migrate(db_path=paths.DB_PATH)
    _quiet_memory(monkeypatch)
    calls = []
    monkeypatch.setattr(fa, "resolve_altitude", _resolver(calls))

    _tap(_FollowHandler())

    rows = _rows()
    assert len(rows) == 1
    assert rows[0]["topic"] == "Volkswagen"             # re-aimed
    assert rows[0]["disclosure"] == "Volkswagen (company)"
    assert rows[0]["origin_story"] == STORY             # birthplace kept


def test_a4_an_unconfident_settle_renders_nothing_and_the_follow_stands(monkeypatch):
    """THE ASK IS DEAD (his ruling ④). An unconfident settle returns
    `settled: False` — the client's one silent branch — and the story-scoped
    follow simply stands. BORN-RED: at HEAD low confidence returned state 'ask'
    with an option list, and committed NOTHING until the reader picked."""
    db.migrate(db_path=paths.DB_PATH)
    _quiet_memory(monkeypatch)
    monkeypatch.setattr(fa, "resolve_altitude", _resolver([], _LOW))

    h = _FollowHandler()
    _tap(h)

    payload, status = h.sent[-1]
    assert status == 200
    assert payload["state"] == "unsettled" and payload["settled"] is False
    assert "options" not in payload and "lead" not in payload
    rows = _rows()
    assert len(rows) == 1 and rows[0]["altitude"] == "narrow"
    # and no state, renderer or label survives to build an ask from
    assert not hasattr(server, "_altitude_options")
    for gone in ("flRenderAsk", "flPick("):
        assert gone not in webui.JS, gone


def test_a5_a_failed_settle_renders_nothing_and_never_apologises(monkeypatch):
    """NL-103 row 3: the apology door is dead. A failed settle leaves an
    ORDINARY story-scoped follow. BORN-RED: at HEAD a resolver failure returned
    state 'degrade' carrying the two apology strings."""
    db.migrate(db_path=paths.DB_PATH)
    _quiet_memory(monkeypatch)

    def _boom(thread, **kw):
        raise fa.AltitudeError("lane down")
    monkeypatch.setattr(fa, "resolve_altitude", _boom)

    h = _FollowHandler()
    _tap(h)

    payload, _ = h.sent[-1]
    assert payload["ok"] is True and payload["settled"] is False
    assert labels.FOLLOW_DEGRADE_UPGRADE not in str(payload)
    assert _rows()[0]["altitude"] == "narrow"


def test_a6_the_settle_never_reaims_a_scope_someone_chose(monkeypatch):
    """MUTATION LAW, extended. A re-follow RESUMES a thread at the scope it
    already had — the settle must not fire, or "picked up where it left off"
    quietly becomes "re-scoped behind your back". BORN-RED (assertion): a naive
    seed calls add_thread_at_altitude, which overwrites the altitude columns
    with narrow/'' on the way in."""
    db.migrate(db_path=paths.DB_PATH)
    _quiet_memory(monkeypatch)
    con = db.connect(paths.DB_PATH)
    try:
        memory.add_thread_at_altitude(
            con, "Volkswagen", altitude="entity", primary_entity="Volkswagen",
            disclosure="Volkswagen (company)", alt_label=STORY,
            confidence="high", source="pick", origin_story=STORY)
        memory.dismiss_thread(con, "Volkswagen")
    finally:
        con.close()
    calls = []
    monkeypatch.setattr(fa, "resolve_altitude", _resolver(calls))

    h = _FollowHandler()
    _tap(h, topic="Volkswagen", origin=HEADLINE)

    payload, _ = h.sent[-1]
    assert payload["seeded"] is False              # no settle licensed
    assert calls == []                             # …and none fired
    assert payload["disclosure"] == "Volkswagen (company)"   # scope resumed
    rows = _rows()
    assert len(rows) == 1 and rows[0]["altitude"] == "entity"


def test_a6b_a_legacy_thread_resumes_as_itself_never_re_seeded(monkeypatch):
    """GATE F4 (QA-3, re-sized) — HIS SHAPE, constructed: a pre-0019 legacy row
    with NO altitude and REAL history (12 ledger entries, the Strait of Hormuz
    shape), unfollowed, then re-followed from a card.

    The old predicate required an altitude, so a legacy row fell through to a
    fresh narrow seed: no resume clause, `seeded:True`, and the settle then
    RENAMED a thread he had named himself. Existence is the predicate now.

    BORN-RED against the pre-fix tree: `resumed` is absent, `seeded` is True,
    the resolver is called, and the topic is renamed out from under him."""
    db.migrate(db_path=paths.DB_PATH)
    _quiet_memory(monkeypatch)
    legacy = "Strait of Hormuz"
    con = db.connect(paths.DB_PATH)
    try:
        tid = con.execute(
            "INSERT INTO memory (topic, status, status_changed_at, created_at,"
            " updated_at) VALUES (?, 'active', ?, ?, ?)",
            (legacy, "2026-07-01T00:00:00.000Z", "2026-07-01T00:00:00.000Z",
             "2026-07-01T00:00:00.000Z")).lastrowid
        for i in range(12):
            con.execute(
                "INSERT INTO thread_deltas (thread_id, edition_date, verdict,"
                " what_happened, significance, cites_json, slot) VALUES"
                " (?, ?, 'advances', 'x', 'y', '[]', NULL)",
                (tid, f"2026-07-{i + 1:02d}"))
        con.commit()
        assert _row_col(con.execute(
            "SELECT altitude FROM memory WHERE id = ?", (tid,)).fetchone(),
            "altitude") == ""                      # a genuine legacy row
        memory.dismiss_thread(con, legacy)         # one tap, from any entry now
    finally:
        con.close()
    calls = []
    monkeypatch.setattr(fa, "resolve_altitude", _resolver(calls))

    h = _FollowHandler()
    _tap(h, topic=legacy, origin=legacy)           # re-follow

    payload, _ = h.sent[-1]
    assert payload["resumed"] is True              # the clause renders
    assert payload["kept"] == 12                   # …naming what was kept
    assert payload["seeded"] is False              # …so the settle is not licensed
    assert calls == []                             # …and provably never fired
    rows = _rows()
    assert len(rows) == 1
    assert rows[0]["topic"] == legacy              # HIS name, unchanged
    assert rows[0]["altitude"] == ""               # bare: never settled, never renamed
    con = db.connect(paths.DB_PATH)
    try:
        tomb = con.execute(
            "SELECT COUNT(*) AS n FROM memory_tombstones").fetchone()["n"]
    finally:
        con.close()
    assert tomb == 0                               # no rename tombstone at all


def _row_col(row, name):
    return (row[name] if name in row.keys() else "") or ""


def test_a7_the_settle_is_not_a_reader_correction(monkeypatch):
    """Axel's instrument must keep measuring what it was built to measure: how
    often a MEDIUM auto-commit gets corrected by a reader within 24h. A
    system-initiated re-aim logged as a 'correct' at the same instant as its own
    'commit' would push the ratio to 1.0 on traffic alone and trip the
    pre-registered flip. BORN-RED (assertion): move_follow_altitude logs the
    correction unconditionally."""
    db.migrate(db_path=paths.DB_PATH)
    _quiet_memory(monkeypatch)
    monkeypatch.setattr(fa, "resolve_altitude",
                        _resolver([], dict(_ENTITY, confidence="medium")))

    _tap(_FollowHandler())

    con = db.connect(paths.DB_PATH)
    try:
        stats = memory.medium_correction_stats(con)
    finally:
        con.close()
    assert stats["medium_auto_commits"] == 1
    assert stats["corrected_within_day"] == 0
    assert stats["flip_would_trigger"] is False


def test_a7b_the_settle_does_not_sign_the_forensic_log_as_the_reader(monkeypatch):
    """GATE F3 — a system act must not wear the reader's name in NL-81's log.

    Every RENAMING settle writes a rename tombstone. That log is append-only
    with NO EXPIRY by design — whatever it records is what the org believes
    about this thread forever — so `actor='principal'` on a re-aim nobody asked
    for is a falsehood that cannot later be corrected, only contradicted.

    BORN-RED against the pre-fix tree: the tombstone reads actor='principal'."""
    db.migrate(db_path=paths.DB_PATH)
    _quiet_memory(monkeypatch)
    monkeypatch.setattr(fa, "resolve_altitude", _resolver([]))

    _tap(_FollowHandler())

    con = db.connect(paths.DB_PATH)
    try:
        tomb = [dict(r) for r in con.execute(
            "SELECT topic, kind, actor, successor_key FROM memory_tombstones"
            " ORDER BY id")]
    finally:
        con.close()
    assert len(tomb) == 1 and tomb[0]["kind"] == "rename"
    assert tomb[0]["topic"] == STORY                  # the seeded name it left
    assert tomb[0]["actor"] == "org"                  # …renamed BY THE SYSTEM
    assert tomb[0]["successor_key"] == "volkswagen"


def test_a7c_a_settle_merge_never_marks_a_row_reader_dismissed(monkeypatch):
    """GATE F3, second write — the gate's own construct: a bare-name dismissed
    holder (the real-world shape: a settled thread the reader later unfollowed)
    plus a new story whose settle lands on that same name. The revive-merge
    fires ON THE SETTLE LANE, and the row it retires is the one the reader
    created two seconds earlier by tapping Follow.

    `dismissed_via` answers exactly one question — "did a person's verb cause
    this?" — and it is the column the lifecycle round will read to decide
    whether to render "you stopped following". On this lane the honest answer
    is NULL. A reader switch is unchanged and still stamps 'principal'.

    BORN-RED against the pre-fix tree: the merged-away row reads
    dismissed_via='principal' for an act no person performed."""
    db.migrate(db_path=paths.DB_PATH)
    _quiet_memory(monkeypatch)
    con = db.connect(paths.DB_PATH)
    try:
        memory.add_thread_at_altitude(
            con, "Volkswagen", altitude="entity", primary_entity="Volkswagen",
            disclosure="Volkswagen (company)", alt_label=STORY,
            confidence="high", source="auto", origin_story="An older story")
        memory.dismiss_thread(con, "Volkswagen")      # the reader unfollowed it
    finally:
        con.close()
    monkeypatch.setattr(fa, "resolve_altitude", _resolver([]))

    _tap(_FollowHandler())                            # a NEW story settles onto it

    con = db.connect(paths.DB_PATH)
    try:
        rows = {r["topic"]: dict(r) for r in con.execute(
            "SELECT topic, status, dismissed_via FROM memory")}
    finally:
        con.close()
    assert rows["Volkswagen"]["status"] == "active"           # revived, history kept
    merged = rows[STORY]
    assert merged["status"] == "dismissed_user"               # retired by the merge
    assert merged["dismissed_via"] is None                    # …by NO person's verb


def test_a7d_the_reader_lanes_still_sign_as_the_reader(monkeypatch):
    """GATE F3's other half, and the one that makes it a fix rather than a
    blanket downgrade: a READER switch is byte-unchanged — tombstone
    actor='principal', merged-away dismissed_via='principal'. CARRIED-INVARIANT
    (born-GREEN): it holds on the pre-fix tree and must keep holding, because a
    fix that made every lane say 'org' would erase the distinction it exists to
    draw."""
    db.migrate(db_path=paths.DB_PATH)
    _quiet_memory(monkeypatch)
    con = db.connect(paths.DB_PATH)
    try:
        memory.add_thread_at_altitude(
            con, "Volkswagen", altitude="entity", disclosure="Volkswagen (company)",
            alt_label=STORY, source="auto")
        memory.dismiss_thread(con, "Volkswagen")
        memory.add_thread_at_altitude(
            con, STORY, altitude="storyline", disclosure=STORY,
            alt_label="Volkswagen (company)", source="pick")
    finally:
        con.close()

    h = _FollowHandler()
    h._api_follow_at({"name": "Volkswagen", "altitude": "entity",
                      "disclosure": "Volkswagen (company)",
                      "from_topic": STORY})            # the reader taps the rung

    con = db.connect(paths.DB_PATH)
    try:
        tomb = [dict(r) for r in con.execute(
            "SELECT actor FROM memory_tombstones ORDER BY id")]
        merged = con.execute(
            "SELECT status, dismissed_via FROM memory WHERE topic = ?",
            (STORY,)).fetchone()
    finally:
        con.close()
    assert tomb and all(t["actor"] == "principal" for t in tomb)
    assert merged["status"] == "dismissed_user"
    assert merged["dismissed_via"] == "principal"


def test_a8_no_bare_directional_verb_survives_anywhere():
    """"Widen"/"Broaden" render nowhere — the affordance class died with his
    07-25 ruling, and every surviving scope act NAMES its target. BORN-GREEN on
    the labels half (the words were already absent) and BORN-RED on the acts
    half (the shipped acts line had no accessible names at all, so a rung's
    name in a button list was a bare "this story")."""
    live = _live_label_strings()
    for word in ("Widen", "widen", "Broaden", "broaden"):
        assert not [n for n, v in live.items() if word in v], word
    assert "Switch to " in webui.JS               # every rung names its target
    assert "Switch to " in inspect.getsource(server._follow_acts_line)


# ===========================================================================
# B — ONE COMPONENT, FOUR MOUNTS
# ===========================================================================

def _story_page(con):
    st = {"headline": HEADLINE, "lede": "Body."}
    slot = {"story_title": STORY, "outlets": [], "matched_memory": []}
    return server._render_story(0, st, slot, "analyst", server._active_topics_lower(con),
                                has_file=True, slug="story-0", date="2026-07-27",
                                con=con)


def test_b1_the_deep_view_mounts_the_follow_line():
    """MOUNT 3 — the deep view is the thread's management home, and it is where
    Unfollow lives now that his item 4 took it off cards. BORN-RED: the deep
    view rendered NO follow control of any kind."""
    db.migrate(db_path=paths.DB_PATH)
    con = db.connect(paths.DB_PATH)
    try:
        memory.add_thread_at_altitude(
            con, "Volkswagen", altitude="entity", primary_entity="Volkswagen",
            disclosure="Volkswagen (company)", alt_label=STORY,
            confidence="high", source="auto", origin_story=STORY)
        html = server._render_deep_view(
            "story-0", HEADLINE, {"brief": {}, "header": {}}, "2026-07-27",
            con=con, slot={"story_title": STORY})
    finally:
        con.close()
    assert html.count('class="follow-slot"') == 1
    assert 'data-mount="deep"' in html
    assert 'data-state="expanded"' in html
    assert labels.FOLLOW_UNFOLLOW in html
    assert labels.FOLLOW_INSTEAD_PREFIX in html
    # the state line, and the disclosure it carries
    assert '<span class="oq">(company)</span>' in html
    # …mounted ABOVE the jumplist, where the blessed artifact puts it (SCREEN C)
    assert html.index('class="follow-slot"') < html.index('deep-jumplist')


def test_b2_the_following_row_mounts_the_acts_only_form():
    """MOUNT 4 — the Following row's acts-only primary (his 07-25 blessing).
    BORN-RED: the Following row could DISCLOSE a follow's scope and never act
    on it — no unfollow, no swap, nothing."""
    db.migrate(db_path=paths.DB_PATH)
    con = db.connect(paths.DB_PATH)
    try:
        memory.add_thread_at_altitude(
            con, "Volkswagen", altitude="entity", primary_entity="Volkswagen",
            disclosure="Volkswagen (company)", alt_label=STORY,
            confidence="high", source="auto")
        row = dict(server._following_rows(con)["active"][0],
                   this_delta={"date": "2026-07-27", "what_happened": "x"})
        html = server._spine_updated_row(row)
    finally:
        con.close()
    assert html.count('class="follow-slot"') == 1
    assert 'data-mount="row"' in html
    assert 'data-object-slot="surface"' in html
    assert labels.FOLLOW_UNFOLLOW in html
    # ACTS ONLY: the row's own title is the object, so no second state line
    assert 'class="fl-sentence"' not in html
    assert labels.FOLLOW_COMMITTED_VERB not in html


def test_b3_the_card_carries_state_and_no_acts():
    """MOUNT 1/2 — his ruling ②: today cards are clean A3. No "Instead:", no
    Unfollow, no acts of any kind. CARRIED-INVARIANT on the absence of Unfollow
    (cards never had it) and BORN-RED on the noun + the door."""
    db.migrate(db_path=paths.DB_PATH)
    con = db.connect(paths.DB_PATH)
    try:
        memory.add_thread_at_altitude(
            con, "Volkswagen", altitude="entity", primary_entity="Volkswagen",
            disclosure="Volkswagen (company)", alt_label=STORY,
            confidence="high", source="auto", origin_story=STORY)
        html = _story_page(con)
    finally:
        con.close()
    assert html.count('class="follow-slot"') == 1
    assert 'data-mount="card"' in html
    assert labels.FOLLOW_INSTEAD_PREFIX not in html
    assert labels.FOLLOW_UNFOLLOW not in html
    assert "fl-unfollow" not in html


def test_b4_the_card_steady_verb_is_a_door_to_the_deep_view():
    """FLAG ③ — with Unfollow gone from cards, re-expanding to an actless line
    would be a click with no answer, so the steady verb opens the deep view.
    A card with NO deep view renders a plain statement instead: a dead door is
    worse than a quiet line. BORN-RED: the shipped verb was a collapse toggle."""
    db.migrate(db_path=paths.DB_PATH)
    con = db.connect(paths.DB_PATH)
    try:
        memory.add_thread_at_altitude(
            con, "Volkswagen", altitude="entity", disclosure="Volkswagen (company)",
            source="auto", origin_story=STORY)
        st, slot = {"headline": HEADLINE}, {"story_title": STORY}
        active = server._active_topics_lower(con)
        with_door = server._follow_control(st, slot, [], active, "2026-07-27",
                                           slug="story-0", con=con,
                                           deep_slug="story-0")
        no_door = server._follow_control(st, slot, [], active, "2026-07-27",
                                         slug="story-0", con=con)
    finally:
        con.close()
    assert "openDeepView('story-0'" in with_door
    assert "followTap(this)" not in with_door         # nothing to toggle
    assert "openDeepView" not in no_door
    assert "<a " not in no_door                       # …and no dead link either


def test_b4b_the_card_verb_renders_the_same_bytes_from_both_renderers():
    """GATE F9 — THE TWIN PARITY PIN. The card's steady verb has two renderers:
    the server's `_committed_verb_inner` on load, and the client's
    `flSteadyVerb` after a tap. They must agree on every row shape, or the line
    changes text on reload — a single-rendering-law breach on the milestone
    whose charge is that law.

    They disagreed on ONE shape: an UNMIGRATED row (no altitude, no
    disclosure). The server rendered `● Following` bare, deliberately
    ("nothing settled, nothing fabricated"); the client conflated that case
    into the narrow arm with `|| !disclosure` and rendered `● Following — this
    thread`, asserting a story scope nobody ever stated. Harmless while the
    seed flow made narrow and empty-disclosure coincide — reachable the moment
    F4 let a legacy row resume at its own empty altitude.

    Source-level per the d5/d7 discipline (comments stripped, so a doc comment
    that merely NAMES an arm cannot satisfy the pin), and BOTH SIDES are pinned
    here so neither can drift back alone.

    BORN-RED against the pre-F9 tree: the conflated test is present and there
    is no bare arm."""
    client = _js_code(_fn("flSteadyVerb"))
    server_src = inspect.getsource(server._committed_verb_inner)

    # 1 — the conflation is gone, and the narrow arm is tested on its own
    assert "|| !disclosure" not in client
    assert "altitude === 'narrow'" in client
    # 2 — the deictic is reachable ONLY through the narrow test
    narrow_arm = client.split("altitude === 'narrow'")[1].split("else if")[0]
    assert "threadSelf" in narrow_arm
    assert client.count("threadSelf") == 1
    # 3 — the bare arm exists, on both sides
    assert "NL_LABELS.committedVerb" in client
    assert "labels.FOLLOW_COMMITTED_VERB" in server_src
    # 4 — and the disclosure arm still qualifies, on both sides
    assert "flQualified(disclosure)" in client
    assert "split_qualifier(disclosure)" in server_src

    # 5 — the acceptance shape, measured on the renderer that owns the bytes:
    #     an unmigrated row is BARE from the server, and the client's arm order
    #     now reaches the same branch for the same inputs.
    assert server._committed_verb_inner({"altitude": "", "disclosure": ""}) == (
        f"{labels.FOLLOW_DOT_ON} {labels.FOLLOW_COMMITTED_VERB}")
    assert labels.FOLLOW_THREAD_SELF not in server._committed_verb_inner(
        {"altitude": "", "disclosure": ""})
    # …while narrow and disclosure rows are byte-identical to before F9
    assert server._committed_verb_inner({"altitude": "narrow"}) == (
        f"{labels.FOLLOW_DOT_ON} {labels.FOLLOW_STEADY_PREFIX} "
        f"{labels.FOLLOW_THREAD_SELF}")


def test_b5_the_memory_stamp_stays_a_separate_node():
    """The single-rendering law governs the follow-STATE node, not the
    continuity stamp (the 07-20 design ruling, carried). CARRIED-INVARIANT
    (born-green) — it holds at HEAD and must survive the unification."""
    src = inspect.getsource(server._render_story)
    assert 'class="memline' in src
    assert "_follow_control" in src
    assert 'class="memline' not in inspect.getsource(server._follow_control)


def test_b6_every_mount_renders_through_the_same_component():
    """THE POINT OF THE MILESTONE. One data-* contract, one set of client
    renderers, three server entries — so a state change on any surface renders
    identically on all of them. BORN-RED: three treatments, three vocabularies,
    and only one of them could act."""
    for fn in (server._follow_control, server._follow_slot_html):
        src = inspect.getsource(fn)
        assert 'class="follow-slot"' in src
        assert "data-mount=" in src
    # every mount value the server can emit is one the client knows about
    for mount in ("card", "deep", "row"):
        assert f'mount="{mount}"' in _mount_markup(mount), mount
    js = _fn("flRenderCommitted")
    assert "flMount(slot)" in js and "'row'" in js and "'card'" in js
    # …and the new mounts inherit the stamp separation too: not one of them
    # renders a continuity stamp inside the follow-STATE node
    assert 'class="memline' not in inspect.getsource(server._follow_slot_html)


# ===========================================================================
# D — THE REFUSAL MACHINERY (this milestone's trust core)
# ===========================================================================

def test_d1_memory_sync_error_names_its_arm_at_the_raise_site():
    """A7's payload-class DISCRIMINATOR. Every raise names its own arm, so the
    branch that renders the reader's reason is the branch that produced it —
    never a substring match on a CLI sentence a copy pass is free to reword.
    BORN-RED: MemorySyncError has no `kind` at HEAD."""
    src = inspect.getsource(memory)
    for arm in memory.MemorySyncError.KINDS:
        assert f'kind="{arm}"' in src, arm
    assert memory.MemorySyncError("x").kind == ""            # honest default
    assert memory.MemorySyncError("x", kind="unreadable").kind == "unreadable"


@pytest.mark.parametrize("arm,reason,remedy", [
    ("unreadable", labels.REFUSAL_MEM_UNREADABLE, labels.REFUSAL_MEM_UNREADABLE_FIX),
    ("unparseable", labels.REFUSAL_MEM_UNPARSEABLE, labels.REFUSAL_MEM_UNPARSEABLE_FIX),
    ("unwritable", labels.REFUSAL_MEM_UNWRITABLE, labels.REFUSAL_MEM_UNWRITABLE_FIX),
])
def test_d2_each_write_refusal_arm_carries_its_own_reason(monkeypatch, arm,
                                                          reason, remedy):
    """R-WRITE: class + arm + UI-lane reason + remedy, one per arm. The CLI
    sentence rides as `error` for diagnostics and NEVER as the rendered reason
    (it is a good CLI string and an unlawful UI one — gate R5). BORN-RED: at
    HEAD the payload is `{"ok": false, "error": str(exc)}` and the client
    reverts to resting without rendering any of it."""
    cli = "memory.md has problems — fix them (or delete the file …) --accept-file"

    def _raise(con, **kw):
        raise memory.MemorySyncError(cli, kind=arm)
    monkeypatch.setattr(memory, "sync_memory", _raise)

    h = _FollowHandler()
    h._api_follow_seed({"topic": STORY, "origin": HEADLINE})

    payload, status = h.sent[-1]
    assert status == 200                       # TRANSPORT-SHAPE-INDEPENDENT
    assert payload["ok"] is False
    assert payload["refusal"] == "write"
    assert payload["arm"] == arm
    assert payload["reason"] == reason
    assert payload["remedy"] == remedy
    assert payload["error"] == cli             # diagnostics only
    assert "--accept-file" not in payload["reason"]
    assert "--accept-file" not in payload["remedy"]


def test_d3_an_unmapped_arm_still_renders_words(monkeypatch):
    """THE REQUIRED FALLBACK. An unclassified raise must never render raw CLI
    prose and must never silently revert. BORN-RED: no fallback exists."""
    def _raise(con, **kw):
        raise memory.MemorySyncError("something nobody mapped")
    monkeypatch.setattr(memory, "sync_memory", _raise)

    h = _FollowHandler()
    h._api_follow_seed({"topic": STORY, "origin": HEADLINE})

    payload, _ = h.sent[-1]
    assert payload["refusal"] == "write" and payload["arm"] == ""
    assert payload["reason"] == labels.REFUSAL_MEM_FALLBACK
    assert payload["remedy"] == labels.REFUSAL_MEM_FALLBACK_FIX


def test_d4_the_two_refusal_classes_ride_different_transports(monkeypatch):
    """CORRECTION 1, made mechanical: a write refusal rides 200/ok:false and the
    coverage refusal rides 409. Routing on the STATUS misses every write
    refusal; routing on ok===false catches both and renders them identically,
    which is worse — they carry OPPOSITE marks. So the class is on the payload,
    and the two classes are provably distinct. BORN-RED: no class field
    exists."""
    monkeypatch.setenv("BUDGET_CAP_USD_PER_RUN", TIGHT_CAP)
    db.migrate(db_path=paths.DB_PATH)
    _quiet_memory(monkeypatch)
    monkeypatch.setattr(fa, "resolve_altitude", _resolver([]))

    h = _FollowHandler()
    _tap(h)
    coverage, cov_status = h.sent[-1]

    def _raise(con, **kw):
        raise memory.MemorySyncError("nope", kind="unwritable")
    monkeypatch.setattr(memory, "sync_memory", _raise)
    h2 = _FollowHandler()
    h2._api_follow_seed({"topic": "Another story", "origin": "Another story"})
    write, write_status = h2.sent[-1]

    assert (cov_status, coverage["refusal"]) == (409, "coverage")
    assert (write_status, write["refusal"]) == (200, "write")
    assert coverage["ok"] is write["ok"] is False   # the status is NOT the signal


def test_d5_the_client_routes_on_the_class_never_on_the_status():
    """The acceptance criterion, made structural: no follow-line branch reads a
    status code, and the class field is what the renderers key on. BORN-RED:
    the client had no refusal routing at all."""
    code = _js_code(_follow_block())          # comments stripped — CODE only
    assert "d.refusal" in code                # the class IS the discriminator
    assert "409" not in code                  # …and the status is not
    assert "d.status" not in code and ".statusCode" not in code
    # the server states the class on BOTH transports, so the client never has to
    assert '"refusal": "write"' in inspect.getsource(server._write_refusal)
    assert '"refusal": "coverage"' in inspect.getsource(
        server.Handler._api_follow_settle)


@pytest.mark.parametrize("fn,verb", [
    ("flFollow", "follow"),        # bucket 863 — was a silent revert to resting
    ("flSettle", None),            # the SYSTEM act — never routes here (gate F1)
    ("flSwitch", "switch"),        # bucket 1024 — surfaced, but stated no reason
    ("flPickNarrow", "switch"),    # bucket 1051 — was a silent `return;`
    ("flUnfollow", "unfollow"),    # bucket 1060 — was a silent `return;`
])
def test_d6_all_four_silent_buckets_render_their_reason(fn, verb):
    """THE CENSUS, closed. Four `ok === false` branches swallowed their payload
    before this diff: a silent revert to resting (863) and three silent `return;`
    no-ops (1024 surfaced a reasonless line; 1051 and 1060 said nothing at all).
    Every READER ACT now routes to a renderer that states the reason.
    BORN-RED: `assert 'return;' not in ...` fails on three of the four.

    CENSUS NOTE AMENDED (gate F1): `flSettle` is in this list as the leg that
    must NOT be here. It was built routing to the class router, which was the
    settle mis-filed under the reader-act law — and on a class-less failure it
    rendered ○ over a committed follow. The settle is the SYSTEM's act; its
    named failure state is his ruling ④'s silence. `verb=None` marks that arm."""
    body = _fn(fn)
    if verb is None:
        # the settle leg: no route to the refusal machinery, for ANY payload.
        assert "flRefused" not in body, fn
        assert "flRenderRefusal" not in body, fn
        assert "flActRefusal" not in body, fn
        assert "if (!d || d.ok !== true || d.settled !== true) return;" in body
        return
    assert "flRefused(slot, d, '" + verb + "')" in body, fn
    # and every one of them reaches the SAME class router, so a new act cannot
    # invent its own swallow
    router = _fn("flRefused")
    assert "d.refusal === 'coverage'" in router      # R-COVERAGE: renders nothing
    assert "flRenderRefusal" in router               # R-WRITE on the follow: ○
    assert "flActRefusal" in router                  # R-WRITE on a standing act


def test_d6b_a_classless_settle_failure_renders_nothing(monkeypatch):
    """GATE F1 — THE SILENCE TOOTH. The reproduced lie: `{ok:false, error:'…'}`
    with NO refusal class (a transport drop, a generic 500 off a locked DB)
    arriving on the settle leg ~2s after the tap, rendering

        ○ Didn't follow — NewsLens couldn't save to your memory file.

    over a follow that IS in the database and WAS on screen saying ● Following.

    Two halves, because the suite has no JS runtime and a source pin alone would
    not prove the payload is real:

      1. STRUCTURAL — the settle's callback cannot reach any refusal renderer,
         and its one render is gated positively on a landed name.
      2. SERVER-SIDE — the class-less payload this tooth is named for is really
         producible: an exception outside the settle's two caught arms escapes
         to the generic handler as ok:false with no `refusal` key, WHILE the
         seeded follow stands in the DB. That is the input; part 1 is the proof
         that the input now renders nothing.

    BORN-RED against the pre-fix tree: part 1's `flRefused not in body` fails —
    the shipped line was `if (!d || d.ok === false) return flRefused(slot, d,
    'follow');`."""
    body = _fn("flSettle")
    # 1 — structural: one positive gate, no path to the refusal machinery
    assert "if (!d || d.ok !== true || d.settled !== true) return;" in body
    for renderer in ("flRefused", "flRenderRefusal", "flActRefusal"):
        assert renderer not in body, renderer
    assert body.count("flRenderCommitted") == 1      # the ONE visible outcome
    # …and the reader-act legs are untouched by this fix — the line between the
    # two laws is a line, not a retreat
    for leg in ("flFollow", "flSwitch", "flPickNarrow", "flUnfollow"):
        assert "flRefused(slot, d, '" in _fn(leg), leg

    # 2 — the class-less payload is really producible on this route, over a
    # follow that stands. The seed commits; the settle then dies outside its
    # caught arms; do_POST's generic handler answers ok:false with no class.
    db.migrate(db_path=paths.DB_PATH)
    _quiet_memory(monkeypatch)
    h = _FollowHandler()
    h._api_follow_seed({"topic": STORY, "origin": HEADLINE})
    assert h.sent[-1][0]["state"] == "committed"
    assert len(_rows()) == 1                          # the follow is real

    def _boom(*a, **kw):
        raise sqlite3.OperationalError("database is locked")
    monkeypatch.setattr(server, "_resolve_guard_row", _boom)
    try:
        h._api_follow_settle({"topic": STORY, "origin": HEADLINE,
                              "topic_current": STORY})
    except sqlite3.OperationalError:
        # escapes _api_follow_settle exactly as the gate reproduced; do_POST's
        # handler is what converts it on the wire
        pass
    else:                                             # pragma: no cover
        raise AssertionError("expected the exception to escape the route")
    generic = {"ok": False, "error": "database is locked"}
    assert "refusal" not in generic                   # class-less, as reproduced
    assert len(_rows()) == 1                          # …and the follow STANDS


def test_d7_the_marks_never_lie():
    """○ = nothing was followed. ● = followed. A refusal that leaves a follow
    STANDING therefore renders NO ○ — "nothing followed" over a live follow is
    the same lie in the other direction (content pass §4.1). BORN-RED: no
    refusal rendered a mark at all, because no refusal rendered."""
    write = _js_code(_fn("flRenderRefusal"))
    assert "NL_LABELS.dotOff" in write               # ○ — nothing was followed
    act = _js_code(_fn("flActRefusal"))              # CODE only — a comment that
    assert "dotOff" not in act and "dotOn" not in act  # merely NAMES innerHTML
    # …and the act-level renderer never touches the state line above it
    assert "innerHTML" not in act                    # must not fail this pin
    assert "appendChild" in act


def test_d8_refusals_announce_and_the_glyphs_do_not():
    """Axel's unresolved-by-rule item: aria-live on refusals is PART of the
    acceptance criterion — dropping it resurrects the silent-refusal bug in a
    new costume. And the mark glyphs are aria-hidden, or a screen reader
    announces "white circle" instead of the sentence. BORN-RED: neither
    exists."""
    write = _fn("flRenderRefusal")
    assert "aria-live" in write and "polite" in write
    act = _fn("flActRefusal")
    assert "'role'" in act and "'status'" in act        # announced, not silent
    for r in ("flRenderRefusal", "flRenderCommitted", "flSteadyVerb"):
        body = _fn(r)
        if "fl-dot" in body:
            assert 'aria-hidden="true"' in body, r
    assert 'aria-hidden="true"' in inspect.getsource(server._follow_slot_html)


def test_d9_refusal_payload_lands_in_the_client_refusal_branch(monkeypatch):
    """THE WIRE-CONTRACT PIN, EXTENDED (successor to
    test_refusal_payload_lands_in_the_client_resting_branch). The pin's original
    note said the reason was "returned but never shown … a real
    no-silent-no-op miss … flagged to the gate". This closes it, and extends
    the contract to the narrow rung and unfollow — the two buckets the original
    rider never enumerated. BORN-RED: the shape exists but no branch renders
    it."""
    db.migrate(db_path=paths.DB_PATH)
    con = db.connect(paths.DB_PATH)
    try:
        memory.add_thread_at_altitude(
            con, "Volkswagen", altitude="entity", disclosure="Volkswagen (company)",
            alt_label=STORY, source="auto")
    finally:
        con.close()

    def _raise(con, **kw):
        raise memory.MemorySyncError("boom", kind="unwritable")
    monkeypatch.setattr(memory, "sync_memory", _raise)

    h = _FollowHandler()
    h._api_follow_at({"name": STORY, "altitude": "narrow",
                      "from_topic": "Volkswagen"})          # the narrow rung
    h._api_dismiss({"topic": "Volkswagen"})                 # unfollow

    for payload, status in h.sent:
        assert status == 200
        assert payload["ok"] is False
        assert payload["refusal"] == "write"
        assert payload["reason"] == labels.REFUSAL_MEM_UNWRITABLE
        assert payload["remedy"] == labels.REFUSAL_MEM_UNWRITABLE_FIX
    assert [p["verb"] for p, _ in h.sent] == ["switch", "unfollow"]


def test_d10_a_verb_that_succeeds_after_a_file_write_failure_is_not_a_refusal(monkeypatch):
    """THE HOLE THIS CLOSES, found while wiring the frame: the render-only
    memory.md refresh runs AFTER the verb. At HEAD an OSError there escaped to
    the generic handler as a 500 with ok:false — which the new refusal frame
    would have rendered as "○ Didn't follow" OVER A RECORDED FOLLOW, the exact
    lie this milestone exists to kill. The verb's success stands; the adjacent
    fact goes to `warnings`, which is NL-110's surface and renders nowhere in
    M1c. BORN-RED (assertion): the pre-diff call site is unguarded."""
    db.migrate(db_path=paths.DB_PATH)
    monkeypatch.setattr(memory, "sync_memory",
                        lambda con, **kw: memory.SyncResult())

    def _boom(con):
        raise OSError("read-only file system")
    monkeypatch.setattr(memory, "write_memory_file", _boom)

    h = _FollowHandler()
    h._api_follow_seed({"topic": STORY, "origin": HEADLINE})

    payload, status = h.sent[-1]
    assert status == 200 and payload["ok"] is True     # the follow IS recorded
    assert "refusal" not in payload
    assert any("memory.md" in w for w in payload["warnings"])
    assert len(_rows()) == 1


def test_d11_the_reason_is_never_str_exc():
    """The UI-lane clauses are LABELS, and the CLI sentence is not one of them.
    A regression here is how 61 words of CLI prose naming `--accept-file` reach
    a card. BORN-RED: no such mapping exists."""
    src = inspect.getsource(server._write_refusal)
    assert '"reason": reason' in src and '"remedy": remedy' in src
    assert 'str(exc)' in src and src.index('"error": str(exc)') > src.index('"reason"')
    for arm, (reason, remedy) in server._WRITE_REFUSAL_ARMS.items():
        assert reason.startswith("your ") and not reason.endswith(".")
        assert remedy.endswith(".")


# ===========================================================================
# E — the acts line: named swaps only, and never a fabricated candidate
# ===========================================================================

def test_d12_the_wire_lane_detail_never_reaches_a_render_path():
    """GATE F7 — `detail` is the machine lane and must stay there.

    The coverage refusal carries a diagnostic `detail` naming
    BUDGET_CAP_USD_PER_RUN with both figures. That is lawful ON THE WIRE — it
    is how a 409 stays debuggable — and unlawful the instant anything renders
    it (§3 global law: reader copy never contains an env-var name). Nothing
    reads it today; this pin is what keeps a render path from growing there
    quietly, the way the meter leak grew on a line written to prove compliance.

    Two halves: the client never names the field, and the field's content is
    provably the thing the register bans."""
    assert "d.detail" not in _follow_block()
    assert "detail" not in _js_code(_fn("flRefusalReason"))
    assert "detail" not in _js_code(_fn("flRenderRefusal"))
    assert "detail" not in _js_code(_fn("flActRefusal"))
    # …and the field really does carry what may never render — so this pin
    # guards something, rather than passing because `detail` is always empty
    src = inspect.getsource(server.Handler._api_follow_settle)
    assert "BUDGET_CAP_USD_PER_RUN" in src and "detail=" in src


def test_e1_the_instead_prefix_never_renders_without_a_candidate():
    """"A prefix with nothing after it is a broken sentence" (§2.4), and a
    fabricated "the company" would name a company nothing ever resolved. A
    story-seeded thread whose settle never landed offers Unfollow alone.
    BORN-RED: the shipped acts line ALWAYS rendered a fallback rung."""
    unsettled = server._follow_acts_line({"altitude": "narrow", "alt_label": ""},
                                         STORY)
    assert labels.FOLLOW_INSTEAD_PREFIX not in unsettled
    assert labels.FOLLOW_UNFOLLOW in unsettled
    assert labels.FOLLOW_RUNG_THIS_STORY not in unsettled   # nothing to narrow to

    named = server._follow_acts_line(
        {"altitude": "entity", "alt_label": "Volkswagen job cuts"}, "Volkswagen")
    assert labels.FOLLOW_INSTEAD_PREFIX in named
    assert labels.FOLLOW_RUNG_THIS_STORY in named           # something to narrow to

    unnamed = server._follow_acts_line({"altitude": "entity", "alt_label": ""},
                                       "Volkswagen")
    assert labels.FOLLOW_ALT_FALLBACK_STORYLINE in unnamed  # "the wider story"


def test_e2_every_act_names_its_object_in_its_accessible_name():
    """§3 aria law — items #49/#50 of the content finals, absent from the
    artifact everywhere. On a today page of 8-12 cards a button list otherwise
    reads "Follow this thread" a dozen times, indistinguishably. BORN-RED: the
    resting CTA and all seven rungs carry no accessible name."""
    acts = server._follow_acts_line(
        {"altitude": "entity", "alt_label": "Volkswagen job cuts"}, "Volkswagen")
    assert 'aria-label="Switch to Volkswagen job cuts — Volkswagen"' in acts
    assert 'aria-label="Switch to this story — Volkswagen"' in acts
    assert 'aria-label="Unfollow Volkswagen"' in acts

    db.migrate(db_path=paths.DB_PATH)
    con = db.connect(paths.DB_PATH)
    try:
        resting = server._follow_control(
            {"headline": HEADLINE}, {"story_title": STORY}, [], set(),
            "2026-07-27", slug="story-0", con=con)
    finally:
        con.close()
    assert f'aria-label="Follow this thread — {STORY}"' in resting

    # #39 — an UNNAMED story-seeded thread on a deep view: the deictic the
    # button sits under, plus the named target. "Unfollow" alone in a button
    # list, against a thread with no settled name, names nothing at all.
    db.migrate(db_path=paths.DB_PATH)
    con = db.connect(paths.DB_PATH)
    try:
        memory.add_thread_at_altitude(con, "Fund gating at Meridian",
                                      altitude="narrow", source="seed",
                                      origin_story="Fund gating at Meridian")
        deep = server._deep_follow_line(
            con, {"story_title": "Fund gating at Meridian"},
            "Fund gating at Meridian", "2026-07-27", "story-1")
    finally:
        con.close()
    assert ('aria-label="Unfollow this thread — Fund gating at Meridian"'
            in deep)
    # …and with nothing settled there is no candidate, so no "Instead:" prefix
    assert labels.FOLLOW_INSTEAD_PREFIX not in deep


def test_e3_the_following_container_is_named_following():
    """#48: "primary" is design vocabulary naming an approved VARIANT, not
    anything a reader has a concept of — and an accessible name is reader-facing
    copy. CARRIED-INVARIANT (born-green): the shipped container was already
    bare; the pin exists so the artifact's "Following (primary)" cannot be
    transcribed into the build."""
    db.migrate(db_path=paths.DB_PATH)
    con = db.connect(paths.DB_PATH)
    try:
        page, _ = server.build_page(con)
    finally:
        con.close()
    assert "Following (primary)" not in page
    assert "(primary)" not in page


# ===========================================================================
# G — THE TWO MECHANICAL TEETH
# ===========================================================================

# The banned set, verbatim from the ratified register (§3 global laws) plus the
# v11 standing law: reader copy never contains internal vocabulary or an env-var
# name. "resolve" rides in because the meter breach ("THIS RESOLVE") is the
# breach that proved a careful reader is not a mechanism.
_BANNED = ("altitude", "resolver", "resolve", "confidence", "scope",
           "BUDGET_CAP_USD_PER_RUN", "NEWSLENS_")


def _live_label_strings():
    """Every LIVE reader-facing constant in labels.py — i.e. every string
    constant NOT carrying the RETIRED-NOT-RENDERED sweep marker. Retired
    constants are record-keeping, not live copy (labels.py's own law), and
    reading them as live is how a dead phrase gets "fixed" into a ruling it no
    longer belongs to."""
    src = Path(labels.__file__).read_text(encoding="utf-8")
    retired = {m.group(1) for m in re.finditer(
        r"^([A-Z0-9_]+)\s*=.*#\s*RETIRED-NOT-RENDERED", src, re.M)}
    return {n: v for n, v in vars(labels).items()
            if n.isupper() and isinstance(v, str) and n not in retired}


def test_g1_banned_vocabulary_never_ships_in_live_reader_copy():
    """MECHANICAL TEETH for the register's banned-vocabulary law.

    Why this exists, in the gate's own words: the meter breach ("CAP $2.50 ·
    THIS RESOLVE ≈ $0.005") survived a RATIFIED register review — on a line
    authored specifically to demonstrate compliance — because everyone reading
    it thinks in the vocabulary it leaked. A law whose only enforcement is a
    careful reader has already failed once. BORN-RED is not claimable for the
    label half (no live constant carries a banned word today, by construction of
    this diff); it is a CARRIED-INVARIANT with teeth — the first sweep that
    would have caught the breach that shipped."""
    live = _live_label_strings()
    for name, value in live.items():
        low = value.lower()
        for word in _BANNED:
            assert word.lower() not in low, (name, word, value)


def _m1c_surface_page():
    """A page that actually RENDERS the M1c follow surface: a today card whose
    thread is followed (the steady verb + the continuity stamp), a continuation
    card, a LOUD Following row (the acts-only mount), and the deep views.

    GATE F2 — this fixture exists because the previous one did not reach the
    surface it was sweeping. Its swept reader layer was 11,949 chars of a
    111,165-char page and contained none of this milestone's copy: no acts line,
    no `Instead:`, no rungs, no CTA. A grep cannot catch what is not in its
    haystack, and a tooth that closes on an empty haystack is the hole it was
    built to close, wearing a green tick."""
    from test_ui_polish import slot, story, seed, TODAY
    db.migrate(db_path=paths.DB_PATH)
    con = db.connect(paths.DB_PATH)
    try:
        # a settled, named thread with prior coverage -> a LOUD Following row
        # (acts line + Instead rungs) and a stamped continuation card
        memory.add_thread_at_altitude(
            con, "Volkswagen", altitude="entity", primary_entity="Volkswagen",
            disclosure="Volkswagen (company)", alt_label=STORY,
            confidence="high", source="auto", origin_story=STORY)
        tid = con.execute(
            "SELECT id FROM memory WHERE topic = 'Volkswagen'").fetchone()["id"]
        for d in ("2026-07-05", TODAY):
            con.execute(
                "INSERT INTO thread_deltas (thread_id, edition_date, verdict,"
                " what_happened, significance, cites_json, slot)"
                " VALUES (?, ?, 'advances', 'Works council briefed.', 'x',"
                " '[\"S1\"]', NULL)", (tid, d))
        con.commit()
        seed(con,
             [slot(1, STORY, mem=("Volkswagen",)), slot(2, "A second story")],
             [story(1, STORY), story(2, "A second story", "medium")])
        page, _ = server.build_page(con)
    finally:
        con.close()
    return page


def test_g1b_banned_vocabulary_never_reaches_a_rendered_page():
    """The same law at the render grain — a composed string can leak what no
    constant does (the server's f-string aria names are reader copy, per content
    finals #48, and no constant carries them).

    GATE F2 — THE REACH PRECONDITIONS ARE PART OF THE TOOTH. They assert the
    fixture still SEES the surface before the sweep runs, so this cannot rot
    back to vacuity silently. That silent rot is exactly how the gap shipped:
    the sweep was honest, the haystack was empty, and nothing said so."""
    page = _m1c_surface_page()
    swept = _reader_layer(page)

    # --- reach preconditions: fail LOUD if the sweep loses sight of M1c ---
    assert labels.FOLLOW_INSTEAD_PREFIX in swept          # the acts line
    assert labels.FOLLOW_UNFOLLOW in swept                # the symmetry verb
    assert labels.FOLLOW_RUNG_THIS_STORY in swept         # a narrow rung
    assert "Switch to " in swept                          # a rung's aria name
    assert labels.FOLLOW_THREAD_INACTIVE in swept         # the resting CTA
    assert labels.FOLLOW_COMMITTED_VERB in swept          # a committed state line
    assert labels.MEMLINE_UPDATED in swept                # the continuity stamp
    assert "entry on this thread" in swept                # …in its full form

    for word in _BANNED:
        assert word.lower() not in swept.lower(), word


def test_g1c_banned_vocabulary_never_ships_in_the_client_label_table():
    """GATE F2 — the third surface neither tooth could see. `_nl_labels_js()`
    is the client's whole reader vocabulary, and it rides inside a <script>
    element, which `_reader_layer` strips by design.

    Swept as DATA — the JSON payload's VALUES, parsed, never the blob's source.
    That is the false-positive discipline and the reason this needs no
    allowlist: the payload's KEYS are machine names (`altFallbackEntity`,
    `dotOff`) and a source-text grep would fire on them forever."""
    blob = server._nl_labels_js()
    payload = json.loads(blob[blob.index("{"):blob.rindex("}") + 1])
    assert payload, "the client label table is empty — the sweep sees nothing"
    for key, value in payload.items():
        if not isinstance(value, str):
            continue
        for word in _BANNED:
            assert word.lower() not in value.lower(), (key, word, value)


def test_g1d_the_composed_aria_prefix_is_one_string_on_both_sides():
    """GATE F2 — the twin pin. The rung's accessible name is COMPOSED, half in
    Python and half in JS, from a literal that lives in neither labels.py nor
    the client table: `Switch to <target> — <thread name>` (§3 aria law,
    content finals #50). Nothing sweeps a hard-coded literal, so the two halves
    could drift into two different accessible names for the same control —
    server-rendered on load, client-rendered after a tap.

    Pinning them EQUAL is what a banned-word sweep of JS source cannot do
    without false-positives on lawful machine literals (`data-altitude`,
    `data-alt-label` are attribute names, not copy)."""
    prefix = "Switch to "
    assert prefix in inspect.getsource(server._follow_acts_line)
    assert prefix in _fn("flActsLine")
    # and the em-dash joint is the same one on both sides (§3 composition joint)
    assert " — " in inspect.getsource(server._follow_acts_line)
    assert "' \\u2014 '" in _fn("flActsLine") or " — " in _fn("flActsLine")


def test_g2_memory_warnings_never_render_on_the_follow_line():
    """NL-110 BUILD TOOTH. `_with_memory` attaches sync.guard_lines() as
    `warnings`; no client handler renders them, and M1c must NOT be the round
    that starts. If the client ever surfaces them generically they will land on
    the follow line by accident and teach the reader that a successful action
    produces a warning — after which they stop reading the line, including on
    the day it matters. Giving the warnings a home is NL-110, filed separately.
    CARRIED-INVARIANT (born-green) with teeth: it holds at HEAD, and this
    milestone is exactly when it would break."""
    block = webui.JS[webui.JS.index("function flEsc("):
                     webui.JS.index("function openDeepView(")]
    assert "warnings" not in block
    assert "d.warnings" not in webui.JS
    # and the server still ATTACHES them (the fact exists; only its home is
    # deferred) — a tooth that passes because the data vanished proves nothing
    assert "guard_lines()" in inspect.getsource(server.Handler._with_memory)


# ===========================================================================
# F — the string finals
# ===========================================================================

def test_f1_live_reader_copy_uses_typographic_apostrophes():
    """Ship form is typographic ('), and the shipped table was SPLIT on the same
    word — labels.py carried both "Couldn't" (FOLLOW_SWITCH_FAILED) and
    "Couldn't" (FOLLOW_CAP_REFUSAL). BORN-RED on the live set: the refusal frame
    did not exist, and the one live straight-apostrophe string did."""
    for name, value in _live_label_strings().items():
        assert "'" not in value, (name, value)


def test_f2_the_refusal_frame_is_verbatim():
    """The content finals are BINDING VERBATIM. String-equality pins: a byte
    drifts, this bites. BORN-RED: none of these constants exist."""
    assert labels.REFUSAL_DIDNT_FOLLOW == "Didn’t follow —"
    assert labels.REFUSAL_DIDNT_SWITCH == "Didn’t switch —"
    assert labels.REFUSAL_DIDNT_UNFOLLOW == "Didn’t unfollow —"
    assert labels.REFUSAL_MEM_UNREADABLE == "your memory file can’t be read"
    assert labels.REFUSAL_MEM_UNREADABLE_FIX == (
        "Fix the permissions on memory.md, then try again.")
    assert labels.REFUSAL_MEM_UNPARSEABLE == (
        "your memory file has lines NewsLens can’t read")
    assert labels.REFUSAL_MEM_UNPARSEABLE_FIX == (
        "Fix memory.md, or delete it and let NewsLens rebuild it, then try again.")
    assert labels.REFUSAL_MEM_UNWRITABLE == "your memory file couldn’t be saved"
    assert labels.REFUSAL_MEM_UNWRITABLE_FIX == (
        "Make sure memory.md is writable, then try again.")
    assert labels.REFUSAL_MEM_FALLBACK == (
        "NewsLens couldn’t save to your memory file")
    assert labels.REFUSAL_MEM_FALLBACK_FIX == (
        "Run newslens memory sync to see what’s wrong.")


def test_f3_the_state_and_receipt_finals_are_verbatim():
    assert labels.FOLLOW_COMMITTED_VERB == "Following"
    assert labels.FOLLOW_STEADY_PREFIX == "Following —"
    assert labels.FOLLOW_THREAD_SELF == "this thread"
    assert labels.FOLLOW_NARROW == "this story"
    assert labels.FOLLOW_RUNG_THIS_STORY == "this story"    # "just" is dead
    assert labels.FOLLOW_ALT_FALLBACK_STORYLINE == "the wider story"
    assert labels.FOLLOW_UNFOLLOWED_RECEIPT == "Unfollowed —"
    assert labels.FOLLOW_UNFOLLOWED_SELF == "Unfollowed — this thread."
    assert labels.FOLLOW_RESUMED_PREFIX == "Picked up where it left off —"
    assert labels.FOLLOW_RESUMED_ENTRIES == "entries kept."
    assert labels.FOLLOW_RESUMED_ENTRY == "entry kept."


def test_f4_the_two_referent_noun_law_holds_seat_by_seat():
    """TAXONOMY §1.1: thread takes the OBJECT seats, story takes the SCOPE
    seats. The state line for a story-seeded thread says "this thread"; the
    management row's qualifier says "— this story". Same deictic shape, two
    seats, and swapping them is the v10 incoherence his 07-25 verdict killed.
    BORN-RED: the shipped committed line rendered "Following — this story"."""
    inner = server._committed_verb_inner({"altitude": "narrow"})
    assert labels.FOLLOW_THREAD_SELF in inner
    assert "this story" not in inner
    qualifier = server._altitude_qualifier_html({"altitude": "narrow"})
    assert "— this story" in qualifier
