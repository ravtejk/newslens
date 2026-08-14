"""NL-108 — memory clocks and revivals advance ONLY for editions that publish.

The defect these reds were written against: `memory.update_references` and
`memory.revive_matched` fired inside `ranking.persist`, at RANK time. Analysis,
narrative, script, audio and the budget all come after that, so a run that died
downstream had already advanced thread dormancy clocks and flipped dormant
threads to active for an edition nobody ever read. The NL-146 retry ladder could
do it several times in one morning, each attempt re-advancing the clocks.

It is not hypothetical. On the principal's own log, 2026-08-10 ranked twice: the
FIRST attempt (16:25:51) auto-revived "Drone warfare & European rearmament" and
never published; the SECOND (17:55:12) published at 18:15:34 and recorded no
revival at all, because the thread was already active by then. The published
edition carries no `revived_threads` and its narrative never says "last covered"
— the reader was silently denied the continuity payoff, and the thread's
`status_changed_at` is still stamped with the dead run's timestamp. The 08-12
OPEC+ revival is the same machinery landing correctly, purely because that
morning's fourth attempt happened to be the one that published.

The fix moves both writes to `generate.persist_generation` — the promote — and
runs them inside its transaction, off the slots that edition installs. Same
trigger discipline the moat's delta ledger already runs on (M1 gate F: "a delta
is written ONLY once its edition is published").

All offline; memory.md sandboxed via the imported `memfile` fixture — the real
file is live principal state. Reds are self-contained acceptance criteria.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from conftest import anthropic_envelope
from newslens import generate, memory, paths, ranking

# House pattern (test_generate is imported by a dozen suites): reuse the
# revival world rather than seeding a second, subtly different one.
from test_memory_ranking import (  # noqa: F401 — memfile/llm are fixtures
    DATE, TOPIC, _seed_revival_world, cluster, iso, llm, memfile, rank_cfg,
    seed_items,
)
from test_generate import (  # noqa: F401 — fake_model is a fixture
    compliant_script, fake_model, run, seed_briefing, stories_payload,
)
from test_generate import slot as gen_slot

ENV = {"OPENAI_API_KEY": "sk-x"}


def _memory_snapshot(con):
    """Every column the two deferred writes can touch, for byte-identity."""
    return [
        tuple(r)
        for r in con.execute(
            "SELECT id, topic, status, last_referenced_briefing_id,"
            " status_changed_at, updated_at FROM memory ORDER BY id"
        )
    ]


def _route(llm, *, dormant=("Helium Shortage",), mem=("Iran War",)):
    """One slot that matches BOTH an active thread (update_references) and a
    dormant one (revive_matched), so a single rank exercises both writes."""
    payload = {
        "clusters": [
            cluster([1, 2], title="Earned on merits", tags=TOPIC,
                    mem=list(mem), dormant=list(dormant), impact=6),
        ]
    }
    llm.add_route("/v1/messages", status=200,
                  body=anthropic_envelope(payload, input_tokens=900),
                  content_type="application/json")


def _rank(con):
    return ranking.run_rank(date=DATE, con=con, cfg=rank_cfg(), env=ENV)


def _promote(con, date=DATE, narrative="Body.", script="Script."):
    """The DATABASE half of publication, which is where the memory writes now
    live. Kept separate from the file half so the reds below fail on the
    behaviour under test and not on an incidental helper."""
    return generate.persist_generation(con, date, narrative, script, [])


def _publish(con, **kw):
    """Promote, then re-take the mirror — run_generate's own two-step sequence.

    persist_generation owns the database half (inside its transaction);
    re-rendering memory.md is the orchestrator's, because only it has a report
    to disclose onto. test_run_generate_wires_the_promote_and_the_mirror is
    what proves production actually performs both."""
    applied = _promote(con, **kw)
    memory.refresh_file_after_publish(con, applied)
    return applied


def _thread(con, topic="Helium Shortage"):
    return con.execute(
        "SELECT status, last_referenced_briefing_id AS ref, status_changed_at"
        " FROM memory WHERE topic = ?", (topic,)
    ).fetchone()


# ---------------------------------------------------------------------------
# The defect: rank is not publication
# ---------------------------------------------------------------------------

def test_rank_alone_leaves_memory_byte_identical(migrated_con, memfile, llm):
    """A run that ranks and then dies must not have moved memory at all.

    BORN RED: the old code revived Helium Shortage and re-dated Iran War's
    reference inside ranking.persist, before a single downstream step ran.

    PRECISION (NL-108 QA F-4), so the name is not over-read: byte-identical is
    asserted of the memory TABLE — every column the two deferred writes touch.
    memory.md is THREAD-STATE-identical, not byte-identical: a failed morning
    still re-stamps its `<!-- newslens-sync: gen=N -->` line, which is
    pre-existing NL-81 machinery (the DB claiming authorship of the file it
    last wrote) and is measured to be the whole delta. See
    memory.refresh_file_after_publish's docstring."""
    _seed_revival_world(migrated_con)
    _route(llm)
    before = _memory_snapshot(migrated_con)

    report = _rank(migrated_con)          # rank succeeds; nothing publishes

    assert report.slots, "precondition: the rank produced a selection"
    assert _memory_snapshot(migrated_con) == before
    assert _thread(migrated_con)["status"] == "dormant"


def test_retry_ladder_is_idempotent_on_memory_until_one_publishes(
    migrated_con, memfile, llm
):
    """The NL-146 ladder's live amplifier: three attempts, one morning.

    BORN RED: each attempt used to re-advance the clocks."""
    _seed_revival_world(migrated_con)
    _route(llm)
    before = _memory_snapshot(migrated_con)

    for _ in range(3):
        _rank(migrated_con)
        assert _memory_snapshot(migrated_con) == before

    # ...and the attempt that finally publishes moves memory exactly once.
    revived = _promote(migrated_con)
    assert [r["topic"] for r in revived] == ["Helium Shortage"]
    assert _thread(migrated_con)["status"] == "active"


def test_rank_records_revival_as_pending_not_applied(migrated_con, memfile, llm):
    """ranking_runs is the rank's own record — it may say what this attempt
    WOULD revive, never that it did.

    BORN RED: the key was `revivals`, written as an accomplished fact by an
    attempt that might never publish (the 2026-08-10 row says exactly that)."""
    _seed_revival_world(migrated_con)
    _route(llm)
    report = _rank(migrated_con)

    metas = [json.loads(r["meta"]) for r in migrated_con.execute(
        "SELECT meta FROM ranking_runs WHERE date = ?", (DATE,))]
    ok = [m for m in metas if m["status"] == "ok"]
    assert ok and ok[0]["revivals_pending"] == [
        {"topic": "Helium Shortage", "last_covered": "2026-07-01"}
    ]
    assert "revivals" not in ok[0]

    # The operator line is future-tense, and says where the change will land.
    assert any("will auto-revive when this edition publishes" in w
               and "Helium Shortage (last covered 2026-07-01)" in w
               for w in report.warnings)
    assert not any("auto-revived by slot-earning stories" in w
                   for w in report.warnings)


# ---------------------------------------------------------------------------
# The positive control: a legitimate revival must still fire (the OPEC+ shape)
# ---------------------------------------------------------------------------

def test_publish_applies_revival_and_reference_the_opec_shape(
    migrated_con, memfile, llm
):
    """The 08-12 OPEC+ case end to end: dormant thread earns a slot, the
    edition publishes, the thread revives and the slot carries the dated
    back-reference the narrative reads."""
    _seed_revival_world(migrated_con)
    _route(llm)
    _rank(migrated_con)

    assert _thread(migrated_con)["status"] == "dormant", "not yet published"
    iran_before = _thread(migrated_con, "Iran War")["ref"]

    revived = _promote(migrated_con)

    assert revived == [{"topic": "Helium Shortage", "last_covered": "2026-07-01"}]
    briefing_id = migrated_con.execute(
        "SELECT id FROM briefings WHERE date = ?", (DATE,)).fetchone()["id"]
    helium = _thread(migrated_con)
    assert helium["status"] == "active" and helium["ref"] == briefing_id
    # Continuity's spine moved for the plain active thread too.
    assert _thread(migrated_con, "Iran War")["ref"] == briefing_id != iran_before

    # The reader-facing half was never deferred: the slot JSON still carries
    # the back-reference, computed read-only at rank.
    slots = json.loads(migrated_con.execute(
        "SELECT story_slots FROM briefings WHERE date = ?", (DATE,)
    ).fetchone()["story_slots"])
    assert slots[0]["revived_threads"] == [
        {"topic": "Helium Shortage", "last_covered": "2026-07-01"}
    ]


def test_dismissed_thread_is_not_resurrected_by_the_promote(
    migrated_con, memfile, llm
):
    """revive_matched still filters status='dormant' at the promote, so a
    thread dismissed BETWEEN rank and publish stays dismissed."""
    _seed_revival_world(migrated_con)
    _route(llm)
    _rank(migrated_con)
    migrated_con.execute(
        "UPDATE memory SET status = 'dismissed_user', dismissed_via = 'principal'"
        " WHERE topic = 'Helium Shortage'")
    migrated_con.commit()

    assert _promote(migrated_con) == []
    assert _thread(migrated_con)["status"] == "dismissed_user"


# ---------------------------------------------------------------------------
# Atomicity + the staged (regenerate) path
# ---------------------------------------------------------------------------

def test_memory_writes_roll_back_with_a_failed_publish(
    migrated_con, memfile, llm, monkeypatch
):
    """The writes live in the promote's transaction, so they commit if and
    only if the edition does."""
    _seed_revival_world(migrated_con)
    _route(llm)
    _rank(migrated_con)
    before = _memory_snapshot(migrated_con)

    def boom(*a, **k):
        raise RuntimeError("promote died mid-transaction")

    monkeypatch.setattr(memory, "revive_matched", boom)
    with pytest.raises(RuntimeError):
        _promote(migrated_con)

    assert _memory_snapshot(migrated_con) == before
    row = migrated_con.execute(
        "SELECT narrative_text FROM briefings WHERE date = ?", (DATE,)).fetchone()
    assert not row["narrative_text"], "the edition rolled back too"


def test_promote_uses_the_staged_slots_not_the_live_row(
    migrated_con, memfile, llm
):
    """NL-106 regenerate: the effects follow the selection being INSTALLED.

    'Old Thread' is referenced only by the edition being displaced; it must not
    collect a reference from the edition that replaces it — which is what
    reading the LIVE row's slots instead of the staged ones would do.

    BORN RED on the return contract: the old promote applied nothing and
    returned None. Iran War is the positive half — it IS in the staged
    selection, so its reference must move to this briefing."""
    _seed_revival_world(migrated_con)
    now = iso(datetime.now(timezone.utc))
    migrated_con.execute(
        "INSERT INTO memory (topic, status, created_at, updated_at)"
        " VALUES ('Old Thread', 'active', ?, ?)", (now, now))
    # A readable edition already exists for DATE, whose slots cite Old Thread.
    migrated_con.execute(
        "INSERT INTO briefings (date, story_slots, narrative_text, generated_at)"
        " VALUES (?, ?, 'Old body.', ?)",
        (DATE, json.dumps([{"slot": 1, "matched_memory": ["Old Thread"],
                            "matched_dormant": []}]), now))
    migrated_con.commit()
    old_ref = _thread(migrated_con, "Old Thread")["ref"]

    _route(llm)
    _rank(migrated_con)                     # stages, does not overwrite
    assert ranking.pending_selection(migrated_con, DATE) is not None

    revived = _promote(migrated_con)

    assert revived == [{"topic": "Helium Shortage", "last_covered": "2026-07-01"}]
    briefing_id = migrated_con.execute(
        "SELECT id FROM briefings WHERE date = ?", (DATE,)).fetchone()["id"]
    assert _thread(migrated_con, "Old Thread")["ref"] == old_ref
    assert _thread(migrated_con, "Iran War")["ref"] == briefing_id
    assert _thread(migrated_con)["status"] == "active"


# ---------------------------------------------------------------------------
# memory.md must not out-vote the database on the next sync
# ---------------------------------------------------------------------------

def test_memory_file_is_re_rendered_after_the_promote(
    migrated_con, memfile, llm
):
    """The file is rendered at the END of the rank stage — now BEFORE the
    transition applies. Left alone it would still carry a matching generation
    stamp, so the next sync would read it as lawful and the FILE WINS on status
    (memory.plan_import): the revival this edition earned would be quietly
    reverted to dormant by its own mirror."""
    _seed_revival_world(migrated_con)
    _route(llm)
    _rank(migrated_con)
    assert "Helium Shortage" in memfile.read_text(encoding="utf-8").split(
        "## Inactive")[1], "precondition: rank-stage render still says dormant"

    _publish(migrated_con)

    active_section = memfile.read_text(encoding="utf-8").split("## Inactive")[0]
    assert "Helium Shortage" in active_section
    # Coverage moved here from test_memory_ranking's gate-fix-3 pin: the
    # reference annotation now appears when the edition is on the record.
    assert f"(last referenced: {DATE})" in active_section

    # The proof that matters: a following sync now has nothing to impose.
    plan = memory.plan_import(
        migrated_con, memory.parse_file(memfile.read_text(encoding="utf-8")))
    assert plan.is_empty


def test_run_generate_wires_the_promote_and_the_mirror(
    migrated_con, memfile, fake_model
):
    """The production path, end to end, on the --no-refresh publish shape (rank
    ran in an earlier process — the 2026-07-14 case class).

    This is the wiring proof: nothing here reaches past run_generate."""
    _seed_revival_world(migrated_con)
    s = gen_slot(1, mem=["Iran War"])
    s["matched_dormant"] = ["Helium Shortage"]
    s["revived_threads"] = [
        {"topic": "Helium Shortage", "last_covered": "2026-07-01"}]
    seed_briefing(migrated_con, DATE, [s])       # bodyless: nothing published
    fake_model.narrative = stories_payload([s])
    fake_model.script = compliant_script([s])
    memory.sync_memory(migrated_con)             # a lawful, stamped memory.md
    assert _thread(migrated_con)["status"] == "dormant"

    report = run(migrated_con, date=DATE)

    assert _thread(migrated_con)["status"] == "active"
    assert "Helium Shortage" in memfile.read_text(
        encoding="utf-8").split("## Inactive")[0]
    assert any(
        "auto-revived by slot-earning stories in this published edition" in w
        for w in report.warnings)


def test_refresh_declines_when_the_file_changed_in_flight(
    migrated_con, memfile, llm
):
    """M4 gate: a transparency surface never overwrites edits it has not read.
    The edition still publishes; the file is disclosed, not clobbered."""
    _seed_revival_world(migrated_con)
    _route(llm)
    _rank(migrated_con)
    hand_edited = memfile.read_text(encoding="utf-8") + "\n- Hand Added Thread\n"
    memfile.write_text(hand_edited, encoding="utf-8")

    revived = _promote(migrated_con)
    left_alone = memory.refresh_file_after_publish(migrated_con, revived)

    assert left_alone and "changed while this run was in flight" in left_alone
    assert memfile.read_text(encoding="utf-8") == hand_edited
    # The database still moved — declining to write the file is not declining
    # to publish the edition's memory effects.
    assert _thread(migrated_con)["status"] == "active"


def test_refresh_declines_a_stale_file_and_leaves_its_bytes_alone(
    migrated_con, memfile, llm
):
    """The refresh's OTHER refusal — NL-81 §5.2, the stale-stamp door.

    Derivation receipt: QA probe P3 (NL-108 QA, 2026-08-14), lifted into the
    shipping suite per F-3 — the M4 gate above had a pin, this door did not.

    A file the database did not last write is stale, so nothing may be written
    to it. Declining is safe precisely because staleness is transitive: the
    next sync refuses the same file, so it cannot revert the revival either.
    The DB half is unaffected — a mirror that cannot be re-taken is not a
    reason to withhold an edition's memory effects."""
    _seed_revival_world(migrated_con)
    _route(llm)
    _rank(migrated_con)
    # Staleness the NL-81 way: the DB's generation counter advances past the
    # stamp the file carries, as if another writer had rendered elsewhere.
    memory.bump_generation(migrated_con)
    before_bytes = memfile.read_bytes()

    revived = _promote(migrated_con)
    left_alone = memory.refresh_file_after_publish(migrated_con, revived)

    assert left_alone and "not the file this database last wrote" in left_alone
    assert memfile.read_bytes() == before_bytes
    assert _thread(migrated_con)["status"] == "active"


def test_a_refusing_paths_guard_cannot_crash_a_published_edition(
    migrated_con, memfile, fake_model
):
    """The containment at generate.py's post-publish refresh, pinned.

    Derivation receipt: QA finding F-3 (NL-108 QA, 2026-08-14) — the second
    unpinned refusal path. `paths.MEMORY_FILE` raises RuntimeError, NOT
    OSError, in an unsanctioned process (the 2026-07-14 incident guard), so
    the helper's own OSError handling does not catch it. By the time this
    runs the edition is ALREADY PUBLISHED and its memory effects ALREADY
    COMMITTED, so an escaping exception would turn a successful publication
    into a crash and lose the report.

    The RuntimeError here is the real one: the module-dict shadow and the
    env override are both removed for the duration of the run, which is
    exactly the condition an ad-hoc/unsanctioned process presents, and the
    guard raises from `paths.__getattr__`."""
    _seed_revival_world(migrated_con)
    s = gen_slot(1, mem=["Iran War"])
    s["matched_dormant"] = ["Helium Shortage"]
    s["revived_threads"] = [
        {"topic": "Helium Shortage", "last_covered": "2026-07-01"}]
    seed_briefing(migrated_con, DATE, [s])
    fake_model.narrative = stories_payload([s])
    fake_model.script = compliant_script([s])
    memory.sync_memory(migrated_con)
    before_bytes = memfile.read_bytes()

    with pytest.MonkeyPatch.context() as mp:
        mp.delitem(vars(paths), "MEMORY_FILE")      # the sandbox's shadow
        mp.delenv("NEWSLENS_MEMORY_FILE")           # and its env redirect
        with pytest.raises(RuntimeError):           # precondition, not the pin
            getattr(paths, "MEMORY_FILE")
        report = run(migrated_con, date=DATE)       # must not raise

    # Published, with its memory effects, despite the mirror being unreachable.
    assert migrated_con.execute(
        "SELECT narrative_text FROM briefings WHERE date = ?", (DATE,)
    ).fetchone()["narrative_text"]
    assert _thread(migrated_con)["status"] == "active"
    # Contained AS A DISCLOSURE, not swallowed: the report says the file was
    # left alone and carries the refusal's own words.
    left = [w for w in report.warnings
            if "memory.md was not refreshed after publication" in w]
    assert len(left) == 1, report.warnings
    assert "unsanctioned process" in left[0]
    assert memfile.read_bytes() == before_bytes
