"""NL-17 M1 — ENTITY IDENTITY: migrations 0024/0025, the mint-or-match door,
the settle-outcome record, and the one-retry bound.

WHAT THIS FILE GUARDS. M1 adds a durable ACTOR table and makes the settle's
answer a matter of record instead of a matter of pixels. Four teeth:

  1. THE SCHEMA IS ADDITIVE AND O(1) — 0024 is a new table plus one nullable FK
     column, 0025 a new append-only log. Neither rewrites a row of memory. This
     matters beyond tidiness: the migrations apply on the principal's next
     server restart against his live 69-thread record, unattended.
  2. THE DOOR REFUSES. mint_or_match mints ONLY on a classifiable actor. A
     storyline, a bare name, an unmapped class: all mint NOTHING and say why.
     No provisional rows, ever (product council 2026-08-08 §5.1) — a placeholder
     entity is a junk identity with a primary key, and junk identity is exactly
     what the principal's amendment (i) is about.
  3. THE SILENCE IS ACCOUNTABLE. Cases (a) and (b) both render nothing, by
     ruling. That is only defensible if the outcome exists SOMEWHERE, so every
     landing appends to 0025 — and the two cases are distinguishable there and
     nowhere else.
  4. THE RETRY IS BOUNDED, AND AUTO-WIDEN IS NOT. A failed settle earns exactly
     one re-settle; a terminal-none thread stays widenable forever. The
     asymmetry IS the ruling, so it is pinned in both directions — a bound that
     also silently blocked auto-widen would be an over-correction that reads as
     a pass.

Offline by construction: autouse sandbox (conftest), no network, no key, $0. The
resolver is never called — every settle here is driven through a stub, because
this file is about what the system DOES with an answer, not about getting one.
"""

from __future__ import annotations

import sqlite3

import pytest

from newslens import db, entities, follow_altitude, memory, paths, server


# ---------------------------------------------------------------------------
# harness
# ---------------------------------------------------------------------------

STORY = "Volkswagen plans significant job cuts"
HEADLINE = "Volkswagen plans significant job cuts at three plants"


class _FollowHandler:
    """The established double (test_nl17_m1c_follow_surface's pattern) — the
    real handler methods, a captured _send_json."""
    _topic_arg = server.Handler._topic_arg
    _raw_topic_arg = server.Handler._raw_topic_arg
    _with_memory = server.Handler._with_memory
    _ref_id_for = server.Handler._ref_id_for
    _commit_altitude = server.Handler._commit_altitude
    _api_follow_seed = server.Handler._api_follow_seed
    _api_follow_settle = server.Handler._api_follow_settle
    _seed_thread = server.Handler._seed_thread
    _settle_onto = server.Handler._settle_onto
    _log_settle = server.Handler._log_settle

    def __init__(self):
        self.sent = []

    def _send_json(self, obj, status=200):
        self.sent.append((obj, status))
        return obj


class _Res:
    """A resolver answer, shaped like follow_altitude.AltitudeResult where the
    settle reads it."""
    def __init__(self, altitude="entity", confidence="high",
                 primary_entity="Volkswagen", disclosure="Volkswagen (company)",
                 alt_label="Volkswagen job cuts"):
        self.altitude = altitude
        self.confidence = confidence
        self.primary_entity = primary_entity
        self.disclosure = disclosure
        self.alt_label = alt_label


@pytest.fixture()
def con():
    db.migrate(db_path=paths.DB_PATH)
    c = db.connect(paths.DB_PATH)
    yield c
    c.close()


@pytest.fixture()
def quiet_memory(monkeypatch):
    """memory.md sync is not what any of this is about."""
    monkeypatch.setattr(memory, "sync_memory", lambda con, **kw: memory.SyncResult())
    monkeypatch.setattr(memory, "write_memory_file", lambda con: 0)


def _settle(monkeypatch, res, *, raises=None):
    """Point the settle at a fixed answer (or a fixed failure). Never a call."""
    def _resolve(*a, **kw):
        if raises is not None:
            raise raises
        return res
    monkeypatch.setattr(follow_altitude, "resolve_altitude", _resolve)
    monkeypatch.setattr(follow_altitude, "resolve_cost_gate",
                        lambda topic, env=None: (True, 0.003, 0.25))


def _events(con, thread_id=None):
    q = "SELECT outcome, attempt, entity_id, detail FROM follow_settle_events"
    args = ()
    if thread_id is not None:
        q += " WHERE thread_id = ?"
        args = (thread_id,)
    return [dict(r) for r in con.execute(q + " ORDER BY id", args)]


def _seed_and_settle(monkeypatch, res=None, raises=None,
                     topic=STORY, headline=HEADLINE):
    h = _FollowHandler()
    h._api_follow_seed({"topic": topic, "origin": headline})
    _settle(monkeypatch, res or _Res(), raises=raises)
    h._api_follow_settle({"topic": topic, "topic_current": topic,
                          "origin": headline})
    return h


# ===========================================================================
# 1 — THE SCHEMA (0024 / 0025)
# ===========================================================================

def test_0024_is_additive_and_o1_over_an_existing_record(tmp_path):
    """BORN RED (the migration does not exist pre-diff).

    THE PROPERTY THAT MATTERS, and it is not "the column appears": SQLite's
    ADD COLUMN with a NULL default is a metadata-only change, so pre-existing
    memory rows are not rewritten and their bytes do not move. Proved by
    migrating to 0023 FIRST, writing rows, then applying the rest — every prior
    row survives with entity_id NULL, which is also its correct first-class
    semantic ("no broader concept"), not a placeholder."""
    db_path = tmp_path / "m.db"
    for name in db.migration_files():
        if name.name > "0023_briefings_pending.sql":
            continue
        con = db.connect(db_path)
        try:
            con.executescript(name.read_text(encoding="utf-8"))
        finally:
            con.close()
    con = db.connect(db_path)
    try:
        for t in ("Strait of Hormuz", "Volkswagen job cuts", "ECB rates"):
            memory.add_thread(con, t)
        before = [dict(r) for r in con.execute(
            "SELECT id, topic, status FROM memory ORDER BY id")]
    finally:
        con.close()

    con = db.connect(db_path)
    try:
        con.executescript(
            (paths.MIGRATIONS_DIR / "0024_entities.sql").read_text(encoding="utf-8"))
    finally:
        con.close()

    con = db.connect(db_path)
    try:
        after = [dict(r) for r in con.execute(
            "SELECT id, topic, status FROM memory ORDER BY id")]
        assert after == before                      # nothing rewritten
        assert all(r["entity_id"] is None for r in con.execute(
            "SELECT entity_id FROM memory"))        # NULL, first-class
        kinds = [r[2] for r in con.execute("PRAGMA table_info(memory)")
                 if r[1] == "entity_id"]
        assert kinds == ["INTEGER"]
        # the FK is declared (0024's REFERENCES), which is what makes a bad
        # entity_id impossible rather than merely unlikely
        fks = [r[2] for r in con.execute("PRAGMA foreign_key_list(memory)")]
        assert "entities" in fks
    finally:
        con.close()


def test_0024_kind_check_is_the_closed_vocabulary(con):
    """BORN RED. Three kinds, enforced by the database — the entity tier's twin
    of ALTITUDES. A fourth kind cannot enter without a migration, which is what
    keeps "one concept = one vocabulary" from drifting into free text."""
    con.execute("INSERT INTO entities (canonical_name, kind) VALUES (?, ?)",
                ("Volkswagen", "org"))
    for bad in ("company", "storyline", "thing", "ORG", ""):
        with pytest.raises(sqlite3.IntegrityError):
            con.execute("INSERT INTO entities (canonical_name, kind)"
                        " VALUES (?, ?)", ("X" + bad, bad))


def test_0024_canonical_name_is_unique_case_insensitively(con):
    """BORN RED. The door checks before it inserts, but a door is a promise and
    an index is a guarantee (0005's precedent for memory.topic)."""
    con.execute("INSERT INTO entities (canonical_name, kind) VALUES (?, ?)",
                ("Volkswagen", "org"))
    with pytest.raises(sqlite3.IntegrityError):
        con.execute("INSERT INTO entities (canonical_name, kind) VALUES (?, ?)",
                    ("VOLKSWAGEN", "org"))


def test_0025_settle_events_are_structurally_append_only(con):
    """BORN RED. The whole defence of cases (a)/(b) rendering NOTHING is that
    the outcome is on the record instead. A record that can be rewritten proves
    nothing, so append-only is enforced by trigger (0004/0009/0020's pair), not
    by everyone remembering."""
    memory.log_settle_outcome(con, 1, "T", "settled_none", detail="low-confidence")
    con.commit()
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        con.execute("UPDATE follow_settle_events SET outcome = 'settled_entity'")
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        con.execute("DELETE FROM follow_settle_events")


def test_0025_outcome_vocabulary_is_closed_in_the_db_and_in_python(con):
    """BORN RED. Closed at BOTH ends on purpose: the CHECK stops a bad row, and
    the Python guard stops a caller with a typo from finding out via an
    IntegrityError halfway through a settle transaction."""
    with pytest.raises(sqlite3.IntegrityError):
        con.execute("INSERT INTO follow_settle_events (thread_id, topic, outcome)"
                    " VALUES (1, 'T', 'settled_maybe')")
    with pytest.raises(ValueError, match="outcome must be one of"):
        memory.log_settle_outcome(con, 1, "T", "settled_maybe")
    assert memory.SETTLE_OUTCOMES == (
        "settled_entity", "settled_none", "settled_low", "settle_failed")


# ===========================================================================
# 2 — THE MINT-OR-MATCH DOOR
# ===========================================================================

def test_the_door_mints_one_row_and_matches_it_thereafter(con):
    """BORN RED. THE SHARED-ENTITY DEDUP, which is the point of the table: two
    threads that settle onto the same actor find the SAME row. Without this,
    "one concept = one vocabulary" has no key to be one of."""
    with con:
        first, detail = entities.mint_or_match(
            con, disclosure="Volkswagen (company)", primary_entity="Volkswagen")
    assert detail == ""
    with con:
        again, _ = entities.mint_or_match(
            con, disclosure="Volkswagen (company)", primary_entity="Volkswagen")
    assert again == first
    assert con.execute("SELECT COUNT(*) FROM entities").fetchone()[0] == 1
    row = entities.get(con, first)
    assert row["canonical_name"] == "Volkswagen" and row["kind"] == "org"


def test_the_door_matches_case_insensitively_and_through_aliases(con):
    """BORN RED. Alias-aware matching is what makes a second story about the
    same actor land on the same identity when the resolver spells it
    differently — and aliases ACCRETE, so a form that once resolved here keeps
    resolving here."""
    with con:
        eid, _ = entities.mint_or_match(
            con, disclosure="Volkswagen (company)", primary_entity="Volkswagen AG")
    assert entities.split_aliases(entities.get(con, eid)["aliases"]) == ["Volkswagen AG"]
    with con:
        by_alias, _ = entities.mint_or_match(
            con, disclosure="Volkswagen AG (company)", primary_entity="VW")
    assert by_alias == eid
    aliases = entities.split_aliases(entities.get(con, eid)["aliases"])
    assert "VW" in aliases and "Volkswagen AG" in aliases
    # canonical_name is NEVER rewritten — an established identity does not move
    # because a later story spelled it differently.
    assert entities.get(con, eid)["canonical_name"] == "Volkswagen"
    with con:
        by_case, _ = entities.mint_or_match(con, disclosure="VOLKSWAGEN (company)")
    assert by_case == eid


@pytest.mark.parametrize("disclosure,primary,reason", [
    ("", "Volkswagen", "no-disclosure"),
    ("Volkswagen job cuts", "Volkswagen", "no-class"),      # descriptive storyline
    ("Redemption Gates (fund-withdrawal story)", "", "unmapped-kind"),
    # "(company)" is NOT in this list, and the omission is deliberate: it lands
    # in the no-class arm, because split_qualifier only splits on " (" and a
    # string with no space before the paren has no class to read. The door's
    # fourth arm ("no-name") is an unreachable BACKSTOP under today's grammar —
    # documented as such at the door rather than pinned by a contrived input, so
    # nothing here claims a red it has never seen.
])
def test_the_door_refuses_rather_than_guessing(con, disclosure, primary, reason):
    """BORN RED, AND THE MOST IMPORTANT TOOTH IN THIS FILE.

    Every one of these could be "made to work" by inventing a kind or accepting
    a bare name as an actor. Doing so would mint exactly the junk identities the
    principal's amendment (i) targets — an entity called "Volkswagen job cuts",
    an actor filed under a class nobody can defend — and they would then pollute
    mint-or-match for every future settle and create XOR exposure against
    sources.yaml. The refusal returns a MACHINE reason so the 0025 event can say
    what happened, and the thread keeps its better name either way."""
    with con:
        eid, detail = entities.mint_or_match(con, disclosure=disclosure,
                                             primary_entity=primary)
    assert eid is None
    assert detail.startswith(reason), detail
    assert con.execute("SELECT COUNT(*) FROM entities").fetchone()[0] == 0


def test_the_kind_table_has_no_fallback_arm(con):
    """BORN RED. Stated as a property, not as a list: an unrecognized class maps
    to None. A heuristic (stemming, "else org") would be un-reviewable and would
    quietly re-file actors; a lookup table's every entry is a one-line diff
    someone can argue with."""
    assert entities.kind_for_class("company") == "org"
    assert entities.kind_for_class("PERSON") == "person"     # case-insensitive
    assert entities.kind_for_class("  place ") == "place"    # trimmed
    for unknown in ("fund-withdrawal story", "saga", "vehicle", "", "orgs"):
        assert entities.kind_for_class(unknown) is None, unknown
    assert set(entities.KIND_BY_CLASS.values()) <= set(entities.KINDS)


# ===========================================================================
# 3 — THE SETTLE'S OUTCOME IS ON THE RECORD
# ===========================================================================

def test_a_confident_entity_settle_mints_and_points_the_thread_at_it(
        monkeypatch, quiet_memory, con):
    """BORN RED. The happy path, end to end through the real route: the thread
    is re-aimed, an entity exists, the thread points at it, and the outcome
    says so — all four, or the milestone did not land."""
    h = _seed_and_settle(monkeypatch)
    out, _ = h.sent[-1]
    assert out["settled"] is True and out["topic"] == "Volkswagen"
    row = con.execute(
        "SELECT id, entity_id FROM memory WHERE lower(topic) = 'volkswagen'"
    ).fetchone()
    assert row["entity_id"] is not None
    ent = entities.get(con, row["entity_id"])
    assert (ent["canonical_name"], ent["kind"]) == ("Volkswagen", "org")
    ev = _events(con, row["id"])
    assert [e["outcome"] for e in ev] == ["settled_entity"]
    assert ev[0]["entity_id"] == row["entity_id"] and ev[0]["attempt"] == 1


def test_a_storyline_settle_renames_the_thread_and_mints_nothing(
        monkeypatch, quiet_memory, con):
    """BORN RED, and it is the case the terminal-none bucket has to be COUNTED
    for (Kass's ratification demand). A storyline settle names a broader STORY —
    a real, useful outcome the reader sees as a better name — but a story is not
    an actor and the kind vocabulary has no seat for one. So: renamed, entity_id
    NULL, outcome settled_none with the reason on the record."""
    h = _seed_and_settle(monkeypatch, _Res(
        altitude="storyline", disclosure="Volkswagen job cuts",
        alt_label="Volkswagen (company)"))
    out, _ = h.sent[-1]
    assert out["settled"] is True and out["topic"] == "Volkswagen job cuts"
    row = con.execute(
        "SELECT id, entity_id, altitude FROM memory"
        " WHERE lower(topic) = 'volkswagen job cuts'").fetchone()
    assert row["altitude"] == "storyline"
    assert row["entity_id"] is None
    assert con.execute("SELECT COUNT(*) FROM entities").fetchone()[0] == 0
    ev = _events(con, row["id"])
    assert [(e["outcome"], e["detail"]) for e in ev] == [("settled_none", "storyline")]


def test_an_unmappable_entity_class_settles_none_and_names_the_class(
        monkeypatch, quiet_memory, con):
    """BORN RED. The resolver called it an entity; we cannot file the class. The
    honest landing is settled_none with the class NAMED in `detail`, so the
    checkpoint can show the principal how often three kinds is too few — rather
    than him discovering it later as a pile of mis-filed rows."""
    _seed_and_settle(monkeypatch, _Res(
        altitude="entity", disclosure="Redemption Gates (fund-withdrawal story)",
        primary_entity="Redemption Gates"))
    assert con.execute("SELECT COUNT(*) FROM entities").fetchone()[0] == 0
    ev = _events(con)
    assert [e["outcome"] for e in ev] == ["settled_none"]
    assert ev[0]["detail"] == "unmapped-kind: fund-withdrawal story"


def test_low_is_settled_low_and_a_dead_resolver_is_settle_failed(
        monkeypatch, quiet_memory, con):
    """BORN RED — THE THREE-WAY DISTINCTION, which exists ONLY here.

    All three render nothing; the rulings say so. What separates them is what
    actually happened:

      settled_low   the settle RAN and came back UNCONFIDENT, holding named
                    candidates (fix loop 1 — this used to be filed as
                    settled_none, which hid it inside the count the principal
                    rules on);
      settled_none  the settle RAN and affirmatively found nothing broader;
      settle_failed it never got an answer at all.

    The first two decide what a management surface may afford; the third decides
    whether a retry is owed. Filing any of them as another is not cosmetic."""
    _seed_and_settle(monkeypatch, _Res(confidence="low"), topic="A", headline="A1")
    _seed_and_settle(monkeypatch, raises=follow_altitude.AltitudeError("dead lane"),
                     topic="B", headline="B1")
    _seed_and_settle(monkeypatch, _Res(altitude="storyline",
                                       disclosure="C storyline", alt_label=""),
                     topic="C", headline="C1")
    by_topic = {e["topic"]: e for e in con.execute(
        "SELECT topic, outcome, detail FROM follow_settle_events")}
    assert by_topic["A"]["outcome"] == "settled_low"
    assert "low-confidence" in by_topic["A"]["detail"]
    assert by_topic["B"]["outcome"] == "settle_failed"
    assert "AltitudeError" in by_topic["B"]["detail"]
    assert by_topic["C storyline"]["outcome"] == "settled_none"
    # THE DENOMINATOR STAYS CLEAN: an unconfident settle must never land in the
    # bucket Kass's falsifier is counted from.
    assert by_topic["A"]["outcome"] != "settled_none"


def test_the_cap_refusal_is_a_failure_not_a_finding(monkeypatch, quiet_memory, con):
    """BORN RED. R-COVERAGE never reaches the model, so the thread was not
    examined — filing it settled_none would tell the backfill this thread was
    looked at and found actorless. It is retry-eligible for the same reason."""
    h = _FollowHandler()
    h._api_follow_seed({"topic": STORY, "origin": HEADLINE})
    monkeypatch.setattr(follow_altitude, "resolve_cost_gate",
                        lambda topic, env=None: (False, 9.99, 0.25))
    h._api_follow_settle({"topic": STORY, "topic_current": STORY,
                          "origin": HEADLINE})
    payload, status = h.sent[-1]
    assert status == 409 and payload["follow_stands"] is True
    ev = _events(con)
    assert [(e["outcome"], e["detail"]) for e in ev] == [("settle_failed", "coverage")]


def test_the_outcome_and_the_reaim_are_one_transaction(monkeypatch, quiet_memory,
                                                       con, tmp_path):
    """BORN RED. Three facts have to become true together — the thread is
    re-aimed, the entity exists, the thread points at it. `_settle_onto` shares
    one connection and therefore one transaction, so a crash between them cannot
    leave a thread claiming an entity nothing minted. Proved by making the
    OUTCOME write fail and checking the entity write went with it."""
    h = _FollowHandler()
    h._api_follow_seed({"topic": STORY, "origin": HEADLINE})
    _settle(monkeypatch, _Res())

    real = memory.log_settle_outcome

    def _boom(con_, *a, **kw):
        raise sqlite3.OperationalError("disk full")
    monkeypatch.setattr(memory, "log_settle_outcome", _boom)
    with pytest.raises(sqlite3.OperationalError):
        h._api_follow_settle({"topic": STORY, "topic_current": STORY,
                              "origin": HEADLINE})
    monkeypatch.setattr(memory, "log_settle_outcome", real)
    assert con.execute("SELECT COUNT(*) FROM entities").fetchone()[0] == 0
    assert con.execute(
        "SELECT COUNT(*) FROM memory WHERE entity_id IS NOT NULL"
    ).fetchone()[0] == 0


# ===========================================================================
# 4 — THE BOUND, AND THE THING IT MUST NOT BOUND
# ===========================================================================

def test_one_failure_earns_one_retry_and_no_more(con):
    """BORN RED — THE BOUND ITSELF, at the predicate the door asks.

    Enforced server-side because a bound the client is trusted to honour is not
    a bound: the browser can be reloaded, scripted, or simply wrong, and every
    unbounded retry is real money on a paid lane."""
    memory.add_thread(con, "T")
    tid = con.execute("SELECT id FROM memory WHERE topic = 'T'").fetchone()["id"]
    assert memory.settle_allowed(con, tid) is True          # never tried
    with con:
        memory.log_settle_outcome(con, tid, "T", "settle_failed", attempt=1)
    assert memory.settle_allowed(con, tid) is True          # the ONE retry
    with con:
        memory.log_settle_outcome(con, tid, "T", "settle_failed", attempt=2)
    assert memory.settle_allowed(con, tid) is False         # and no more
    assert memory.SETTLE_FAILURE_RETRIES == 1


def test_auto_widen_is_not_bounded_and_a_success_clears_the_streak(con):
    """BORN RED, AND THE OVER-CORRECTION GUARD. A terminal-none thread is not
    retrying anything — it settled correctly and found no actor — so a later
    story with new evidence gets a fresh attempt, indefinitely (case (a):
    "auto-widen stays live"). A bound that also quietly blocked THAT would look
    exactly like a passing test while removing the ruling's own affordance."""
    memory.add_thread(con, "T")
    tid = con.execute("SELECT id FROM memory WHERE topic = 'T'").fetchone()["id"]
    for i in range(5):
        with con:
            memory.log_settle_outcome(con, tid, "T", "settled_none",
                                      attempt=i + 1, detail="low-confidence")
        assert memory.settle_allowed(con, tid) is True, i
    # …and a success between failures clears the streak: the thing bounded is
    # repeating a FAILURE for free, not trying again on new evidence.
    with con:
        memory.log_settle_outcome(con, tid, "T", "settle_failed", attempt=6)
        memory.log_settle_outcome(con, tid, "T", "settled_none", attempt=7)
        memory.log_settle_outcome(con, tid, "T", "settle_failed", attempt=8)
    assert memory.settle_allowed(con, tid) is True


def test_the_settle_door_refuses_a_spent_retry_before_it_can_spend(
        monkeypatch, quiet_memory, con):
    """BORN RED — the bound WIRED, not merely available.

    MUTATION-PROVEN framing: the refusal returns ABOVE the cap gate and above
    any transport, so a spent thread cannot reach the resolver at all. The stub
    raises if called, which is what makes this a spend proof rather than a
    payload proof."""
    h = _FollowHandler()
    h._api_follow_seed({"topic": STORY, "origin": HEADLINE})
    tid = con.execute("SELECT id FROM memory").fetchone()["id"]
    with con:
        memory.log_settle_outcome(con, tid, STORY, "settle_failed", attempt=1)
        memory.log_settle_outcome(con, tid, STORY, "settle_failed", attempt=2)

    calls = []

    def _never(*a, **kw):
        calls.append(1)
        raise AssertionError("the resolver must not be reached")
    monkeypatch.setattr(follow_altitude, "resolve_altitude", _never)
    monkeypatch.setattr(follow_altitude, "resolve_cost_gate", _never)

    h._api_follow_settle({"topic": STORY, "topic_current": STORY,
                          "origin": HEADLINE})
    out, status = h.sent[-1]
    assert status == 200 and out["settled"] is False and out["retry_exhausted"]
    assert calls == []
    # and nothing was appended for an attempt that never ran — an outcome row
    # here would corrupt the very streak the bound reads.
    assert len(_events(con, tid)) == 2


def test_the_seed_route_decides_settle_eligibility_not_the_client(
        monkeypatch, quiet_memory, con):
    """BORN RED. It used to be `d.seeded === true` in the browser: a new thread
    settled, an existing one never did. Auto-widen makes that wrong (a later
    story rejoining a terminal-none thread is exactly when the ruling wants
    another attempt), and the bound makes it unsafe (eligibility depends on
    history and on money). So the SERVER answers, on the payload."""
    h = _FollowHandler()
    h._api_follow_seed({"topic": STORY, "origin": HEADLINE})
    assert h.sent[-1][0]["settle"] is True          # new seed

    # a terminal-none thread re-tapped: still eligible (auto-widen)
    tid = con.execute("SELECT id FROM memory").fetchone()["id"]
    with con:
        memory.log_settle_outcome(con, tid, STORY, "settled_none", attempt=1,
                                  detail="low-confidence")
    h2 = _FollowHandler()
    h2._api_follow_seed({"topic": STORY, "origin": HEADLINE})
    assert h2.sent[-1][0]["seeded"] is False
    assert h2.sent[-1][0]["settle"] is True

    # a thread whose retry is spent: not eligible
    with con:
        memory.log_settle_outcome(con, tid, STORY, "settle_failed", attempt=2)
        memory.log_settle_outcome(con, tid, STORY, "settle_failed", attempt=3)
    h3 = _FollowHandler()
    h3._api_follow_seed({"topic": STORY, "origin": HEADLINE})
    assert h3.sent[-1][0]["settle"] is False


def test_a_settled_thread_is_never_re_aimed(monkeypatch, quiet_memory, con):
    """CARRIED-INVARIANT (born green) — the mutation law, unchanged by this
    milestone and stated here because auto-widen is the exact place someone
    would erode it. A thread the reader or an earlier settle NAMED stays named;
    only a still-story-seeded thread is eligible."""
    _seed_and_settle(monkeypatch)
    h = _FollowHandler()
    h._api_follow_seed({"topic": "Volkswagen", "origin": HEADLINE})
    assert h.sent[-1][0]["settle"] is False
    assert h.sent[-1][0]["altitude"] == "entity"
