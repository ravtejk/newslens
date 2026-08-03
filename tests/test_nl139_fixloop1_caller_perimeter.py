"""NL-139 FIX LOOP 1 — the CALLER perimeter of the topic clamp.

The clamp itself (tests/test_nl139_byte_clamps.py) was sound at all four
INSERT doors. What QA's F-1 found is that a clamp is only half a contract: the
CALLERS have to speak the same key. Three of them did not, and a fourth write
door was never in the inventory at all.

  F-1 (MAJOR, this-batch regression) — `server._seed_thread`'s RESUMED-vs-NEW
      predicate, `_commit_altitude`'s response `topic`, and the raw-keyed
      unfollow/delete/note verbs all keyed on the UNCLAMPED posted name while
      storage was clamped. For a canonical topic over TOPIC_MAX_CHARS (the
      founder's story_slots carry titles to 108 chars today, clamp 80) the
      predicate missed the row the seed had just created, so unfollow→refollow
      took the NEW landing on an EXISTING thread and answered `seeded: True` —
      licensing the settle to re-aim a thread the reader already had. That is
      the property gate F4 / QA-3 protects, and `_seed_thread`'s own docstring
      promises. It did NOT exist at 38141a3 (nothing was clamped there), which
      is what makes it a regression rather than a pre-existing defect.

  DOOR 5 — `memory.move_follow_altitude` is the tree's only
      `UPDATE memory SET topic`. Both my door inventory and QA's counted
      `INSERT INTO memory` and found four; this is a fifth way an unclamped
      name reaches the column, and the only one the MODEL drives (the
      background settle names the thread from `split_qualifier(res.disclosure)`).

  F-2 / F-3 — the CLI door's clamp and `/api/revive`'s fourth-outcome fix were
      live but unpinned: mutating either flipped zero tests, which the
      wiring-proof law (ENGINEERING.md:113) treats as unlanded.

PROOF CLASS — stated per group, because they are NOT the same currency and the
born-red law (ENGINEERING.md:122) is about not inflating one into the other.
The baseline is MY OWN PRE-FIX LAND BYTES (the fix-loop-entry snapshot), NOT
38141a3: F-1 and door 5 are regressions this batch introduced, so HEAD is the
wrong baseline and would score them a false green.

  * BEHAVIOUR-RED vs pre-fix land (7) — the three F-1 symptoms, both door-5
    pins, and the two /api/follow-at pins. These fail on real behaviour: a
    wrong response key, a re-seeded thread, a false unfollow receipt, an
    unclamped rename, an unresolvable successor key, and a switch that created
    a divergent second follow instead of moving the first.
  * MUTATION-PROVEN (6) — the F-2 (CLI door) and F-3 (/api/revive) pins are
    GREEN against pre-fix land ON PURPOSE: both fixes were already live, and
    what was missing was the pin. Their bite is proven by removing the
    enforcement, not by the pre-fix run; receipts in the build record.
  * CARRIED-INVARIANT / premise guards (2) — the headline-shape guard and the
    origin_story hazard pin, both green on both sides and labelled in place.

SECOND CALLER, found while fixing the first: `/api/follow-at` (the reader's
pick / switch lane) reads `name` and `from_topic` straight out of the body, so
`_topic_arg` never saw either. QA's probe did not reach it. Same class, same
fix, pinned below.

The pre-fix fail list is in the build record's §FIX-LOOP-1.
"""
import sqlite3

import pytest

from newslens import cli, db, memory, server


# ---------------------------------------------------------------------------
# harness — the established _FollowHandler double (test_nl17_m1c_follow_surface)
# ---------------------------------------------------------------------------

class _FollowHandler:
    _topic_arg = server.Handler._topic_arg
    # Bound only when it exists, DELIBERATELY: the pre-fix bytes this file is
    # born red against have no `_raw_topic_arg`, and a harness that blew up on
    # the missing name would collect-error the whole module — turning a set of
    # BEHAVIOUR reds into one symbol red and proving nothing about the
    # regression. The pre-fix handler never calls it, so binding it absent is
    # correct rather than merely convenient.
    if hasattr(server.Handler, "_raw_topic_arg"):
        _raw_topic_arg = server.Handler._raw_topic_arg
    _with_memory = server.Handler._with_memory
    _ref_id_for = server.Handler._ref_id_for
    _commit_altitude = server.Handler._commit_altitude
    _api_follow_seed = server.Handler._api_follow_seed
    _seed_thread = server.Handler._seed_thread
    _api_dismiss = server.Handler._api_dismiss
    _api_follow_at = server.Handler._api_follow_at
    _settle_onto = server.Handler._settle_onto
    _api_revive = server.Handler._api_revive

    def __init__(self):
        self.sent = []

    def _send_json(self, obj, status=200):
        self.sent.append((obj, status))
        return obj


# 108 chars — the real shape: the founder DB's longest story_slots title today
# is 108, and the story's canonical topic IS its story_title.
HEADLINE = ("Volkswagen confirms plant closures and sweeping job cuts across "
            "its German operations amid the EV transition")


@pytest.fixture(autouse=True)
def _quiet_memory(monkeypatch):
    """memory.md side effects off — this file is about DB keys, and the real
    sync would rewrite the principal's file on every verb."""
    monkeypatch.setattr(memory, "sync_memory",
                        lambda con, **kw: memory.SyncResult())
    monkeypatch.setattr(memory, "write_memory_file", lambda con: None)


@pytest.fixture
def con(tmp_paths):
    db.migrate()
    c = db.connect()
    yield c
    c.close()


def _stored_topics(con):
    return [r["topic"] for r in
            con.execute("SELECT topic FROM memory ORDER BY id").fetchall()]


def test_the_headline_is_the_real_shape_not_a_convenient_one():
    """Guard on the pin's own premise. If TOPIC_MAX_CHARS ever rises past the
    real-world title length this whole file silently stops testing anything."""
    assert len(HEADLINE) == 108
    assert len(HEADLINE) > memory.TOPIC_MAX_CHARS


# ---------------------------------------------------------------------------
# F-1 — the three symptoms, driven through the REAL handler methods
# ---------------------------------------------------------------------------

def test_the_seed_response_echoes_the_key_the_client_must_use(con):
    """F-1 symptom 1. The client keys its unfollow and settle calls on the
    `topic` this response carries. Handing back the 108-char posted name while
    storing 80 means every follow-up call the client makes speaks a key the
    database does not hold.

    RED against pre-fix land: response 108 chars, stored 80."""
    h = _FollowHandler()
    h._api_follow_seed({"topic": HEADLINE})
    out, status = h.sent[-1]
    assert status == 200 and out["ok"] is True

    stored = _stored_topics(con)
    assert len(stored) == 1
    assert out["topic"] == stored[0], (
        "the seed answered %r but stored %r" % (out["topic"], stored[0]))
    assert len(out["topic"]) <= memory.TOPIC_MAX_CHARS


def test_unfollow_then_refollow_is_RESUMED_not_reseeded(con):
    """F-1 symptom 2 — THE MAJOR, and the gate F4 / QA-3 property.

    A thread that EXISTED must come back as itself: `seeded: False`, its
    altitude columns untouched, so the background settle is NOT licensed to
    re-aim a thread whose identity someone already decided.

    RED against pre-fix land: the predicate keyed on the raw 108-char name,
    missed the clamped row, took the NEW landing on that same row —
    `_set_altitude_columns` overwrote altitude/disclosure/alt_label and the
    handler answered `seeded: True`.

    SCENARIO NOTE (QA's S2, deliberately NOT their S3): the thread keeps the
    story's own name. A thread the reader RENAMED leaves this predicate's
    reach on every tree, HEAD included — that is pre-existing behaviour and
    pinning it here would manufacture a regression that is not this batch's.
    The identity below is given at the SAME key, through the picker's commit
    lane, which is what makes the re-seed's damage visible."""
    h = _FollowHandler()
    h._api_follow_seed({"topic": HEADLINE})
    first, _ = h.sent[-1]
    assert first["seeded"] is True, "the first tap must be a fresh seed"
    # Read the STORED name from the row, not from the response. On pre-fix
    # bytes the response carries the raw key, so using it would make the
    # unfollow below fail silently (that is symptom 3) and leave the row
    # ACTIVE — where the XOR guard short-circuits before this predicate is
    # ever reached, hiding symptom 2 behind symptom 3. Reading storage
    # isolates the predicate, which is what this test is for.
    stored = _stored_topics(con)[0]

    # The reader settles the thread's scope at its own name (the picker's
    # commit lane) — an identity someone chose, which is exactly what the
    # RESUMED contract exists to protect.
    memory.add_thread_at_altitude(
        con, stored, altitude="entity", primary_entity="Volkswagen",
        disclosure="Volkswagen (company)", alt_label="Volkswagen job cuts",
        source="pick")
    # ...then unfollows from the card, which keys on the STORED name.
    assert memory.dismiss_thread(con, stored)

    # Re-tap the same story.
    h2 = _FollowHandler()
    h2._api_follow_seed({"topic": HEADLINE})
    again, _ = h2.sent[-1]

    assert again["seeded"] is False, (
        "an EXISTING thread was re-seeded — the settle is now licensed to "
        "re-aim a thread the reader already scoped: %r" % (again,))
    assert again["altitude"] == "entity", (
        "the resumed thread lost the scope the reader chose: %r" % (again,))
    assert again["alt_label"] == "Volkswagen job cuts"
    assert "kept" in again, "the RESUMED landing's resume clause is missing"
    assert len(_stored_topics(con)) == 1, "a divergent second row was created"
    # The stored columns, not just the response, survived the re-tap.
    row = con.execute("SELECT altitude, alt_label FROM memory").fetchone()
    assert row["altitude"] == "entity" and row["alt_label"] == "Volkswagen job cuts"


def test_a_mid_session_unfollow_of_a_long_named_thread_lands(con):
    """F-1 symptom 3. `_api_dismiss` answers `outcome: 'already'` when there is
    no active row — an honest receipt for "you are not following this". Keyed
    on the raw name it produced that receipt for a thread that WAS active and
    stayed active: a false receipt that heals only on reload.

    RED against pre-fix land: outcome 'already', row still active."""
    h = _FollowHandler()
    h._api_follow_seed({"topic": HEADLINE})

    d = _FollowHandler()
    d._api_dismiss({"topic": HEADLINE})
    out, _ = d.sent[-1]
    assert out["outcome"] == "unfollowed", (
        "the unfollow false-receipted: %r" % (out,))
    status = con.execute("SELECT status FROM memory").fetchone()["status"]
    assert status == "dismissed_user", (
        "the endpoint reported success but the row is still %r" % (status,))


def test_origin_story_keeps_the_raw_story_identity(con):
    """The fix's own hazard, pinned. `origin_story` is NOT a thread name — it is
    a story identity that `_origin_follow_row` / `_resolve_guard_row` match at
    RENDER time against unclamped `story_slots` titles. Clamping it along with
    everything else would have broken origin-card recognition for exactly the
    long-titled stories this fix is about: the same regression, one column
    over. Hence `_raw_topic_arg`.

    PROOF CLASS: CARRIED-INVARIANT (green on both sides of the fix, labelled
    per ENGINEERING.md:122). origin_story was never clamped; this pin exists so
    that the obvious "just clamp everything" simplification of the F-1 fix
    cannot land silently."""
    h = _FollowHandler()
    h._api_follow_seed({"topic": HEADLINE})
    row = con.execute("SELECT topic, origin_story FROM memory").fetchone()
    assert len(row["topic"]) <= memory.TOPIC_MAX_CHARS
    assert row["origin_story"] == HEADLINE, (
        "origin_story was clamped — the origin card can no longer recognize "
        "its own follow from the story's raw title")
    # And the guard that reads it still finds the row from the RAW story key.
    assert server._resolve_guard_row(con, HEADLINE, HEADLINE), (
        "the render-time recognition guard lost the row")


# ---------------------------------------------------------------------------
# DOOR 5 — the rename lane
# ---------------------------------------------------------------------------

def test_the_rename_lane_clamps_too(con):
    """DOOR 5, found in this fix loop. `move_follow_altitude` is the tree's only
    `UPDATE memory SET topic`; an insert-only door inventory (mine and QA's)
    could not see it. It is also the door the MODEL drives — the background
    settle renames a thread from `split_qualifier(res.disclosure)` — so an
    unbounded model name would land straight in the column the clamp exists to
    bound, and NL-139's whole point is that remote-authored strings get clamped.

    RED against pre-fix land: the rename stores all 108 chars."""
    tid = con.execute(
        "INSERT INTO memory (topic, status, status_changed_at, created_at,"
        " updated_at) VALUES ('seed', 'active', '', '', '')").lastrowid
    con.commit()
    survivor = memory.move_follow_altitude(
        con, tid, new_name=HEADLINE, altitude="entity", source="pick")
    assert survivor is not None
    stored = con.execute("SELECT topic FROM memory WHERE id = ?",
                         (survivor,)).fetchone()["topic"]
    assert len(stored) <= memory.TOPIC_MAX_CHARS, (
        "the rename lane stored %d chars" % len(stored))
    assert stored == memory.clamp_topic(HEADLINE)[0]


def test_the_rename_tombstone_records_the_key_that_will_resolve(con):
    """The ordering half of door 5. The rename tombstone blocks a stale
    memory.md from re-INSERTing the OLD name and carries the SUCCESSOR key so
    the disclosure can name the thread's current name. Clamping after the
    tombstone was written would record a successor key no row will ever hold.

    RED against pre-fix land: successor is the 108-char name."""
    tid = con.execute(
        "INSERT INTO memory (topic, status, status_changed_at, created_at,"
        " updated_at) VALUES ('old name', 'active', '', '', '')").lastrowid
    con.commit()
    memory.move_follow_altitude(
        con, tid, new_name=HEADLINE, altitude="entity", source="pick")
    tomb = con.execute(
        "SELECT topic, successor_key FROM memory_tombstones"
        " WHERE kind = 'rename' ORDER BY id DESC LIMIT 1").fetchone()
    assert tomb["topic"] == "old name"
    # successor_key is stored casefolded (append_tombstone) — compare in kind.
    assert tomb["successor_key"] == memory.clamp_topic(HEADLINE)[0].casefold(), (
        "the tombstone's successor key names no row that exists: %r"
        % (tomb["successor_key"],))
    # ...and it resolves: the successor key IS the row's stored key.
    live = con.execute(
        "SELECT topic FROM memory WHERE lower(topic) = ?",
        (tomb["successor_key"],)).fetchone()
    assert live is not None, "the successor key resolves to no row"


# ---------------------------------------------------------------------------
# F-2 — door 4 (the CLI's own INSERT)
# ---------------------------------------------------------------------------

def test_the_cli_door_clamps_and_says_so(tmp_paths, capsys):
    """F-2: the clamp at cli.py's memory handler was live but unpinned —
    removing it flipped zero tests, which ENGINEERING.md:113 treats as
    unlanded. This is that test.

    The CLI runs its OWN `INSERT INTO memory` rather than going through
    `add_thread`, and its clamp sits above its own `lower(topic)` lookup, so
    the pin drives both halves: the row is stored short AND a second add of the
    same long name resolves to it instead of creating a twin.

    PROOF CLASS: MUTATION-PROVEN, not born-red. This pin is GREEN against the
    pre-fix land bytes on purpose — the clamp was already there; the debt was
    the missing pin. Its bite is proven by deleting the two clamp lines at
    cli.py and re-running (receipt in the build record's §FIX-LOOP-1)."""
    db.migrate()
    assert cli.main(["memory", "add", HEADLINE]) == 0
    out = capsys.readouterr().out
    assert "shortened" in out, (
        "the CLI stored a different name than the principal typed and said "
        "nothing: %r" % (out,))
    assert str(memory.TOPIC_MAX_CHARS) in out

    con = db.connect()
    try:
        topics = _stored_topics(con)
    finally:
        con.close()
    assert len(topics) == 1 and len(topics[0]) <= memory.TOPIC_MAX_CHARS

    # Idempotent: the clamp is above the lookup, so the same long name finds
    # its own row rather than inserting a twin.
    assert cli.main(["memory", "add", HEADLINE]) == 0
    con = db.connect()
    try:
        assert len(_stored_topics(con)) == 1, "a twin row was created"
    finally:
        con.close()


# ---------------------------------------------------------------------------
# F-3 — /api/revive's fourth outcome
# ---------------------------------------------------------------------------

def test_api_revive_counts_a_truncated_add_as_success(con, monkeypatch):
    """F-3: `/api/revive` tests `add_thread`'s outcome against a literal tuple.
    NL-139 added a FOURTH outcome ('added-truncated'), so the tuple had to
    grow — the fix was live but unpinned, and reverting it flipped nothing.

    ATTRIBUTION (QA-confirmed): NL-139-INTERNAL, not a pre-existing defect. At
    38141a3 `add_thread` returned exactly the three outcomes the tuple listed;
    the endpoint only becomes wrong once the fourth exists.

    DRIVEN BY STUBBING `add_thread`'s RETURN, and that is the point rather than
    a shortcut. After the F-1 fix `_topic_arg` clamps at the door, so the HTTP
    path hands `add_thread` an already-short name and can no longer ELICIT
    'added-truncated' — the tuple entry is now defence-in-depth for direct
    programmatic callers. A pin that posted a long topic would therefore pass
    with the entry deleted (measured: it did), which is exactly the false
    green the wiring-proof law exists to catch. Stubbing the outcome tests the
    tuple itself, which is the thing that has to be right.

    PROOF CLASS: MUTATION-PROVEN — green against pre-fix land (the fix was
    live), red when the tuple loses the outcome."""
    monkeypatch.setattr(memory, "add_thread",
                        lambda *a, **kw: "added-truncated")
    h = _FollowHandler()
    h._api_revive({"topic": "anything"})
    out, _ = h.sent[-1]
    assert out["ok"] is True, (
        "the endpoint reported failure for an add that happened: %r" % (out,))


@pytest.mark.parametrize("outcome",
                         ["added", "revived", "already-active",
                          "added-truncated"])
def test_every_add_thread_outcome_reads_as_success_at_revive(
        con, monkeypatch, outcome):
    """The regression guard for the NEXT outcome. `add_thread` has no failure
    return — failures RAISE — so every value it can return means the reader is
    now following the thread, and revive's success set must cover all of them.
    A fifth outcome added without touching this tuple would silently answer
    ok:false for an act that succeeded, which is the exact shape of F-3.

    Behavioural, not source-inspecting: the first draft of this pin read
    `inspect.getsource` for each outcome string and passed under mutation
    because it was matching the explanatory COMMENT above the tuple, not the
    tuple. A pin that a comment can satisfy is not a pin.

    PROOF CLASS: MUTATION-PROVEN — red for the dropped outcome."""
    monkeypatch.setattr(memory, "add_thread", lambda *a, **kw: outcome)
    h = _FollowHandler()
    h._api_revive({"topic": "anything"})
    out, _ = h.sent[-1]
    assert out["ok"] is True, (
        "revive answered ok:false for outcome %r — an add that happened reads "
        "as a failure" % (outcome,))


# ---------------------------------------------------------------------------
# F-1, SECOND CALLER — /api/follow-at (the reader's pick / switch lane)
# ---------------------------------------------------------------------------
# QA's F-1 reached the seed lane. This endpoint is the same class one route
# over and was NOT in their probe: it reads `name` / `from_topic` out of the
# body directly, so `_topic_arg` never saw either, and both are memory keys.

def test_a_switch_away_from_a_long_named_thread_moves_it(con):
    """The divergent-second-follow bug, in the switch lane. `from_topic`
    resolves the row to MOVE; unclamped it found nothing for a >80-char thread
    and fell through to the CREATE branch — two active follows for one story,
    the exact double the XOR guard (`_resolve_guard_row`) exists to prevent,
    and the mutation law ("the follow MOVES, never copies") broken.

    RED against pre-fix land: two rows, outcome 'added' instead of 'moved'."""
    h = _FollowHandler()
    h._api_follow_seed({"topic": HEADLINE})
    stored = h.sent[-1][0]["topic"]

    sw = _FollowHandler()
    sw._api_follow_at({"name": "Volkswagen", "altitude": "entity",
                       "from_topic": HEADLINE, "disclosure": "Volkswagen (company)"})
    out, _ = sw.sent[-1]
    assert out["outcome"] == "moved", (
        "the switch created a second follow instead of moving the first: %r"
        % (out,))
    actives = con.execute(
        "SELECT topic FROM memory WHERE status = 'active'").fetchall()
    assert len(actives) == 1, (
        "one story now has %d active follows" % len(actives))
    assert actives[0]["topic"] == "Volkswagen"
    assert stored  # the seed's stored key existed to switch away from


def test_the_pick_lane_echoes_the_stored_key_on_both_branches(con):
    """The response-echo half, for this endpoint's two branches. The client
    keys its next call (unfollow, switch-back) on `topic`, so a raw echo is the
    same client/storage divergence F-1's symptom 1 was — and here it survives
    the door clamp, because this route never passes through `_topic_arg`.

    RED against pre-fix land: both branches echo the raw 108-char name."""
    # CREATE branch (no from_topic).
    h = _FollowHandler()
    h._api_follow_at({"name": HEADLINE, "altitude": "narrow"})
    created, _ = h.sent[-1]
    stored = con.execute(
        "SELECT topic FROM memory WHERE id = ?",
        (created["thread_id"],)).fetchone()["topic"]
    assert created["topic"] == stored, (
        "create branch answered %r, stored %r" % (created["topic"], stored))

    # MOVE branch.
    m = _FollowHandler()
    m._api_follow_at({"name": HEADLINE.replace("Volkswagen", "Porsche"),
                      "altitude": "entity", "from_topic": HEADLINE})
    moved, _ = m.sent[-1]
    assert moved["outcome"] == "moved"
    stored2 = con.execute(
        "SELECT topic FROM memory WHERE id = ?",
        (moved["thread_id"],)).fetchone()["topic"]
    assert moved["topic"] == stored2, (
        "move branch answered %r, stored %r" % (moved["topic"], stored2))
    assert len(moved["topic"]) <= memory.TOPIC_MAX_CHARS


def test_commit_altitude_echoes_storage_for_ANY_caller(con):
    """`_commit_altitude`'s own docstring: "One place, so every commit path
    shares the storage contract." This pins that contract at the function
    rather than at a route — called directly with an UNCLAMPED name, the way a
    future commit path would if it forgot the door.

    WHY IT IS PINNED HERE AND NOT THROUGH A ROUTE (honest note): with
    `_topic_arg` and `/api/follow-at` both clamping, no HTTP path can now reach
    this function with a long name, so the read-back is invisible to a
    route-level mutation — reverting it to `return name` leaves every
    route test green (measured: mutation M4, 32/32 pass). An enforcement no
    test can flip is treated as unlanded by the wiring-proof law
    (ENGINEERING.md:113), so it gets a test at the level where it IS
    observable.

    PROOF CLASS: MUTATION-PROVEN — red when `_commit_altitude` returns `name`
    instead of the stored row's topic."""
    h = _FollowHandler()
    out = h._commit_altitude(con, name=HEADLINE, altitude="narrow",
                             source="seed", origin_story=HEADLINE)
    stored = con.execute("SELECT topic FROM memory WHERE id = ?",
                         (out["thread_id"],)).fetchone()["topic"]
    assert out["topic"] == stored, (
        "a commit path that skipped the door got back a key the database does "
        "not hold: answered %r, stored %r" % (out["topic"], stored))
    assert len(out["topic"]) <= memory.TOPIC_MAX_CHARS


# ---------------------------------------------------------------------------
# FIX LOOP 2 — the two enumerated residues QA left non-blocking
# ---------------------------------------------------------------------------

def test_the_settle_echoes_the_stored_key_for_a_long_model_name(con, monkeypatch):
    """R-1 (QA fix-loop-1 re-verification). `_settle_onto` was the ONE lane
    that missed the read-back rule the rest of this perimeter follows. Its
    `name` is MODEL output — `split_qualifier(res.disclosure)` — so a settle
    name over TOPIC_MAX_CHARS stored 80 chars and announced 139.

    QA measured it as FUNCTIONALLY ABSORBED: every verb the client makes next
    passes a clamped door, so even the raw echo still unfollows. That is why
    it is fixed as a CONTRACT rather than as a bug — the response tells the
    reader what their thread is now called, and naming it something the
    database does not hold is wrong even when nothing downstream trips on it.
    Door 5 already bounds what is STORED; this bounds what is SAID.

    PROOF CLASS: born-red vs the fix-loop-1 land bytes (echo 139 vs stored 80).
    """
    import types

    from newslens import follow_altitude as fa

    long_name = "Volkswagen European manufacturing restructuring programme " \
                "and the wider German industrial transition to electric " \
                "vehicles"
    assert len(long_name) > memory.TOPIC_MAX_CHARS

    h = _FollowHandler()
    h._api_follow_seed({"topic": HEADLINE})
    seeded = h.sent[-1][0]

    res = types.SimpleNamespace(
        confidence="high", altitude="entity", primary_entity="Volkswagen",
        disclosure=long_name, alt_label="", topic=long_name)
    monkeypatch.setattr(fa, "resolve_altitude", lambda thread, **kw: res)

    s = _FollowHandler()
    out = s._settle_onto(con, from_topic=seeded["topic"],
                         name=fa.split_qualifier(long_name)[0], res=res,
                         origin_story=HEADLINE)
    stored = con.execute("SELECT topic FROM memory WHERE id = ?",
                         (out["thread_id"],)).fetchone()["topic"]
    assert out["topic"] == stored, (
        "the settle announced %r (%d chars) but stored %r (%d) — the client is "
        "told a thread name the database does not hold"
        % (out["topic"], len(out["topic"]), stored, len(stored)))
    assert len(out["topic"]) <= memory.TOPIC_MAX_CHARS


def test_the_seed_echo_survives_a_case_variant_re_tap(con):
    """M5, corrected. My fix-loop-1 note claimed `_seed_thread`'s echo of
    `prior["topic"]` had NO independent observable once the door clamps —
    "no test can flip it". QA showed that is too strong: `lower(topic)`
    matching admits CASE VARIANTS, so a re-tap posting different casing hits
    the RESUMED branch with `headline != prior["topic"]`, and the mutated echo
    returns the POSTED casing while storage holds the original.

    Materiality is low (casing only; every server-side resolver
    lower()-normalizes) — but the honest statement is "cheaply pinnable", not
    "unobservable", and the M5 row of the mutation matrix now bites.

    PROOF CLASS: MUTATION-PROVEN — red when `_seed_thread` echoes `headline`
    instead of the stored row's topic."""
    h = _FollowHandler()
    h._api_follow_seed({"topic": HEADLINE})
    stored = _stored_topics(con)[0]
    # The unfollow is REQUIRED to reach the lane under test: while the row is
    # active the XOR guard (`_resolve_guard_row`, status='active') answers
    # first and echoes the stored topic itself, so the re-tap never reaches
    # `_seed_thread` and the mutation cannot be observed. Measured — without
    # this line the pin passes with the echo reverted, which is how the
    # fix-loop-1 "no observable" reading came about in the first place.
    assert memory.dismiss_thread(con, stored)

    again = _FollowHandler()
    again._api_follow_seed({"topic": HEADLINE.upper()})
    out = again.sent[-1][0]

    assert out["seeded"] is False, "the case variant should resume, not re-seed"
    assert out["topic"] == stored, (
        "the resumed response echoed the POSTED casing %r instead of the "
        "stored %r" % (out["topic"], stored))
    assert len(_stored_topics(con)) == 1, "a case variant created a twin row"
