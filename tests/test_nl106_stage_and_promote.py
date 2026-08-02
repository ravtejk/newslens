"""NL-106 — stage-and-promote: a regenerate no longer destroys the edition.

THE DEFECT (proven live in the NL-103 remediation QA, finding QA-1): a full
regenerate destroyed the readable edition ~30 minutes before its replacement
existed. `ranking.persist()` archived the old row and NULLed the body on the
LIVE row at rank time; `generate.persist_generation()` wrote the new body at the
very end. Anything failing in between — analysis, narrative, editor, script,
audio — left a listed-but-blank edition in the Archive.

THE INVARIANT THESE TESTS HOLD: at every instant the live `briefings` row for a
date is either the coherent pre-run edition or the coherent new one. Never a
mixture. Never bodyless because a run is in flight.

The mechanism under test:
  * rank, on a date that already has a READABLE edition, stages its new
    selection in `briefings_pending` and touches nothing else;
  * the two mid-run readers (generate.load_briefing_inputs with
    prefer_pending=True, and the analysis stage) consume the STAGED selection;
  * persist_generation PROMOTES in one transaction — archive the displaced
    edition, install staged slots + new body, delete the staged row;
  * a dead run writes nothing at all: the staged row is simply left behind.
"""

from __future__ import annotations

import json
import re
import sqlite3
from datetime import datetime
from pathlib import Path

import pytest

from newslens import analysis, db, generate, paths, ranking, server

from test_server import ui, get, seed_briefing            # noqa: F401
from test_generate import (                               # noqa: F401
    ENV as GEN_ENV, A_DAY, compliant_script, fake_model, slot as gen_slot,
    seed_briefing as seed_published_edition, stories_payload, _fake_audio_ok,
)

SRC = Path(__file__).resolve().parents[1] / "src" / "newslens"
DATE = "2026-07-06"

BRIEFING_COLUMNS = ("id", "date", "story_slots", "corroboration_labels",
                    "narrative_text", "script_text", "audio_file_path",
                    "token_cost", "generated_at")


# --- helpers ----------------------------------------------------------------

@pytest.fixture
def con(tmp_paths):
    """A migrated sandbox DB at the sandboxed paths.DB_PATH (so a second,
    genuinely separate connection can read it the way the server does)."""
    db.migrate()
    c = db.connect()
    yield c
    c.close()


def _slot(n: int, title: str, outlets=("Outlet A", "Outlet B"), item_ids=()):
    return ranking.RankedSlot(
        slot=n, story_title=title, summary=f"summary of {title}",
        item_ids=list(item_ids), outlets=list(outlets), matched_tags=[],
        matched_memory=[], followed_analyst=False, personal_score=1.0,
        world_impact=6, combined_score=0.8, override=False,
        override_label=None, corroboration_count=len(outlets),
        corroboration_label=f"Reported by {len(outlets)} named outlets",
        wire_items_excluded=0)


def rank(con, title: str, date: str = DATE, prompt_tokens: int = 100,
         outlets=("Outlet A", "Outlet B"), item_ids=()):
    """Drive the REAL rank-time persistence for a one-slot selection.

    `outlets` also drives the corroboration payload, so a caller can make two
    selections differ in corroboration as well as in slots — without that, a
    'corroboration moved with the slots' assertion is vacuously true.
    """
    report = ranking.RankReport(
        date=date, slots=[_slot(1, title, outlets, item_ids)],
        token_usage={"prompt_tokens": prompt_tokens, "completion_tokens": 20})
    ranking.persist(con, report, {"window": {"days": 1.0}})


def publish(con, narrative: str, script: str = "SCRIPT", steps=None,
            date: str = DATE, audio=None):
    """Drive the REAL end-of-run publication (promote when staged)."""
    generate.persist_generation(con, date, narrative, script,
                                steps if steps is not None else [],
                                audio_path=audio)


def live(con, date: str = DATE):
    row = con.execute("SELECT * FROM briefings WHERE date = ?", (date,)).fetchone()
    return dict(row) if row is not None else None


def staged(con, date: str = DATE):
    row = con.execute("SELECT * FROM briefings_pending WHERE date = ?",
                      (date,)).fetchone()
    return dict(row) if row is not None else None


def history(con, date: str = DATE):
    return [dict(r) for r in con.execute(
        "SELECT * FROM briefings_history WHERE date = ? ORDER BY id", (date,))]


# --- 1. the rank stage: stage, never destroy --------------------------------

def test_a_re_rank_of_a_readable_edition_leaves_the_live_row_byte_identical(con):
    """THE CORE PIN. Every column of the live row survives a re-rank, and the
    archive does not fire — because nothing has been replaced yet."""
    rank(con, "First story")
    publish(con, "EDITION ONE")
    before = live(con)
    assert before["narrative_text"] == "EDITION ONE"

    rank(con, "Second story")

    after = live(con)
    for col in BRIEFING_COLUMNS:
        assert after[col] == before[col], f"the re-rank changed {col}"
    assert history(con) == []          # nothing displaced, nothing archived
    pend = staged(con)
    assert pend is not None
    assert "Second story" in pend["story_slots"]
    assert "First story" not in pend["story_slots"]
    assert pend["created_at"] and pend["token_cost"]


def test_a_bodyless_row_keeps_the_original_archive_and_overwrite_path(con):
    """The first-run-failure arm is UNCHANGED (F2, out of NL-106's scope): a row
    with no body has no readable edition to protect, so rank archives the husk
    and overwrites in place — and stages nothing."""
    rank(con, "First story")           # ranked, never published
    assert live(con)["narrative_text"] is None
    assert staged(con) is None

    rank(con, "Second story")

    row = live(con)
    assert "Second story" in row["story_slots"]
    assert row["narrative_text"] is None
    assert len(history(con)) == 1      # the husk was archived, exactly once
    assert staged(con) is None         # and nothing was staged


def test_the_null_arm_still_scrubs_a_stray_body_field_on_a_bodyless_row(con):
    """CARRIED INVARIANT (born green at f4510fa). The M3 item-11 NULLing
    survives where it is still reachable: a row whose narrative is gone but
    which carries a stray script/audio must not keep them against new slots."""
    rank(con, "First story")
    with con:
        con.execute("UPDATE briefings SET script_text = 'orphan script',"
                    " audio_file_path = '/tmp/orphan.mp3' WHERE date = ?", (DATE,))
    rank(con, "Second story")
    row = live(con)
    assert row["script_text"] is None and row["audio_file_path"] is None


def test_a_second_re_rank_overwrites_the_staged_row_rather_than_duplicating(con):
    """A dead run leaves its staged selection behind; the next re-rank simply
    writes over it. One date, at most one staged selection."""
    rank(con, "First story")
    publish(con, "EDITION ONE")
    rank(con, "Second story")          # run 2 stages, then dies
    rank(con, "Third story")           # run 3 stages over it

    rows = con.execute("SELECT * FROM briefings_pending").fetchall()
    assert len(rows) == 1
    assert "Third story" in rows[0]["story_slots"]
    assert live(con)["narrative_text"] == "EDITION ONE"   # still the reader's


# --- 2. the mid-run readers -------------------------------------------------

def test_mid_run_reads_the_staged_slots_while_the_reader_holds_the_old_edition(con):
    """THE KILLER TEST. Mid-regenerate the stages must write about the NEW
    selection while a concurrent reader — the server, on its own connection —
    still gets the whole OLD edition."""
    rank(con, "First story")
    publish(con, "EDITION ONE")
    rank(con, "Second story")

    inputs = generate.load_briefing_inputs(con, DATE, prefer_pending=True)
    assert inputs["slots"][0]["story_title"] == "Second story"
    assert inputs["row"]["id"] == live(con)["id"]     # same row identity

    reader = db.connect_readonly()                   # cannot write, by construction
    try:
        seen = reader.execute(
            "SELECT * FROM briefings WHERE date = ?", (DATE,)).fetchone()
    finally:
        reader.close()
    assert seen["narrative_text"] == "EDITION ONE"
    assert "First story" in seen["story_slots"]
    assert "Second story" not in seen["story_slots"]


def test_load_briefing_inputs_reads_the_live_row_unless_asked_for_pending(con):
    """prefer_pending defaults OFF. The published edition is what every other
    caller (backfill, the prompt/cost batteries) must see — a caller reading
    staged slots against a published narrative would be the mixture this whole
    change exists to prevent, one table over."""
    rank(con, "First story")
    publish(con, "EDITION ONE")
    rank(con, "Second story")

    inputs = generate.load_briefing_inputs(con, DATE)
    assert inputs["slots"][0]["story_title"] == "First story"


def test_exactly_one_call_site_opts_into_the_staged_read():
    """Wiring proof for the line above: the in-flight run turns it on, it is the
    only thing that does, and (FIX-2) it turns it on only for runs that can
    PROMOTE — the condition is part of the pin, not incidental."""
    opted = []
    for path in sorted(SRC.glob("*.py")):
        for call in re.findall(r"(?<!def )load_briefing_inputs\([^)]*\)",
                               path.read_text(encoding="utf-8")):
            if "prefer_pending" in call:
                opted.append((path.name, call))
    assert opted == [
        ("generate.py",
         "load_briefing_inputs(con, date, prefer_pending=not report.sample)")
    ], opted


def test_the_analysis_stage_consumes_the_staged_selection(con, monkeypatch):
    """The second mid-run reader. The analysis stage runs BEFORE the promote, so
    it must analyse what the run is about to publish, not what is published."""
    rank(con, "First story")
    publish(con, "EDITION ONE")
    rank(con, "Second story")

    seen = []

    def recorder(con_, date_, slot_no, slot, tier, *a, **kw):
        seen.append(slot["story_title"])
        # The REAL return type, not a hand-listed duck-type (NL-130,
        # 2026-08-01). This stub used to be a SimpleNamespace enumerating the
        # fields `run_analysis` happened to read, so every new StoryAnalysis
        # field broke a test about staging — the ordering ruling's two
        # instrumentation fields (est_usd/bound_usd) were the fourth such
        # field and the one that caught it. A dataclass tracks its own
        # contract; this test is about which SLOT the stage analyses.
        return analysis.StoryAnalysis(
            slot=slot_no, tier=tier, outcome="ok", detail="",
            cost_usd=0.0, shadow_usd=0.0, fetch_ok=0,
            fetch_attempted=0, sonar_status="skipped")

    monkeypatch.setattr(analysis, "analyze_story", recorder)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-not-real")
    analysis.run_analysis(date=DATE, con=con, sleep=lambda s: None)
    assert seen == ["Second story"]
    # ...and it read that from the STAGED row, not the live one: the live row
    # is still the reader's untouched first edition. Asserting both halves is
    # what makes this test able to fail — an analysis stage reading the live row
    # would be indistinguishable from this one if rank had overwritten it.
    row = live(con)
    assert "First story" in row["story_slots"]
    assert "Second story" not in row["story_slots"]
    assert row["narrative_text"] == "EDITION ONE"


def test_the_staged_payload_is_what_the_refusals_fire_on(con):
    """The corrupt/empty-slot refusals must judge the payload actually consumed
    — a staged selection with no slots refuses the run exactly as a live row
    with no slots does."""
    rank(con, "First story")
    publish(con, "EDITION ONE")
    rank(con, "Second story")
    with con:
        con.execute("UPDATE briefings_pending SET story_slots = '[]'"
                    " WHERE date = ?", (DATE,))
    with pytest.raises(generate.GenerateError, match="no story slots"):
        generate.load_briefing_inputs(con, DATE, prefer_pending=True)

    with con:
        con.execute("UPDATE briefings_pending SET story_slots = '{not json'"
                    " WHERE date = ?", (DATE,))
    with pytest.raises(generate.GenerateError, match="corrupt"):
        generate.load_briefing_inputs(con, DATE, prefer_pending=True)

    # ...and the live row, which is still perfectly readable, still loads
    assert generate.load_briefing_inputs(con, DATE)["slots"][0][
        "story_title"] == "First story"


# --- 3. the promote ---------------------------------------------------------

def test_a_successful_regenerate_promotes_atomically(con):
    """The swap: new slots and new body arrive together, the displaced edition
    is archived as ONE complete history row (ADR-0001), the staged row is gone."""
    rank(con, "First story")
    publish(con, "EDITION ONE", script="SCRIPT ONE", audio="/tmp/one.mp3")
    old = live(con)

    # a THIRD outlet on the new selection, so the corroboration payload differs
    # from the old edition's and the pin below cannot pass vacuously
    rank(con, "Second story", outlets=("Outlet A", "Outlet B", "Outlet C"))
    staged_payload = staged(con)                       # FIX-5 (QA-4)
    publish(con, "EDITION TWO", script="SCRIPT TWO", audio="/tmp/two.mp3")

    row = live(con)
    assert "Second story" in row["story_slots"]
    # FIX-5: slots and corroboration are one payload and must move together —
    # a promote that installed new slots beside the old corroboration labels
    # would be a mixture inside the row it just made coherent.
    assert row["story_slots"] == staged_payload["story_slots"]
    assert row["corroboration_labels"] == staged_payload["corroboration_labels"]
    assert row["corroboration_labels"] != old["corroboration_labels"]
    assert row["narrative_text"] == "EDITION TWO"
    assert row["script_text"] == "SCRIPT TWO"
    assert row["audio_file_path"] == "/tmp/two.mp3"
    assert row["id"] == old["id"]                      # same row, same identity
    assert staged(con) is None                         # staging area cleared

    hist = history(con)
    assert len(hist) == 1                              # exactly one, not two
    h = hist[0]
    # THE PAIRING: old slots WITH old body, in the same row (this is what the
    # rank-time archive used to produce and what must not regress).
    assert "First story" in h["story_slots"]
    assert h["narrative_text"] == "EDITION ONE"
    assert h["script_text"] == "SCRIPT ONE"
    assert h["audio_file_path"] == "/tmp/one.mp3"
    assert h["corroboration_labels"] == old["corroboration_labels"]
    assert h["token_cost"] == old["token_cost"]
    assert h["generated_at"] == old["generated_at"]
    assert h["briefing_id"] == old["id"]


def test_the_promoted_ledger_is_this_editions_money_and_only_this_editions(con):
    """NL-95: `usd` is real money. The promoted row carries the NEW rank step
    (which rode on the staged row) plus THIS run's generation steps — never the
    displaced edition's spend, which has just been archived with it."""
    rank(con, "First story", prompt_tokens=111)
    publish(con, "EDITION ONE", steps=[{"step": "narrative_v1", "usd": 0.5}])

    rank(con, "Second story", prompt_tokens=222)
    staged_cost = json.loads(staged(con)["token_cost"])
    publish(con, "EDITION TWO", steps=[{"step": "narrative_v2", "usd": 0.25}])

    tc = json.loads(live(con)["token_cost"])
    assert [s["step"] for s in tc["steps"]] == ["rank_select", "narrative_v2"]
    assert tc["steps"][0]["prompt_tokens"] == 222        # the NEW rank step
    assert tc["steps"][0] == staged_cost["steps"][0]     # ...verbatim, not re-priced
    assert "narrative_v1" not in json.dumps(tc)
    assert tc["total_usd"] == round(
        (staged_cost["steps"][0].get("usd") or 0) + 0.25, 6)

    # and the displaced edition's ledger went to history with it
    assert "narrative_v1" in history(con)[0]["token_cost"]


def test_a_narrative_only_re_run_with_nothing_staged_is_unchanged(con):
    """CARRIED INVARIANT (born green at f4510fa — this path is deliberately
    untouched). No staged row -> persist_generation behaves exactly as it always
    has: archive-if-body, then fold this run's steps onto the LIVE row's
    ledger."""
    rank(con, "First story", prompt_tokens=111)
    publish(con, "EDITION ONE", steps=[{"step": "narrative_v1", "usd": 0.5}])
    old = live(con)

    publish(con, "EDITION ONE REDONE", steps=[{"step": "narrative_v2", "usd": 0.25}])

    row = live(con)
    assert row["narrative_text"] == "EDITION ONE REDONE"
    assert "First story" in row["story_slots"]          # slots untouched
    tc = json.loads(row["token_cost"])
    assert [s["step"] for s in tc["steps"]] == [
        "rank_select", "narrative_v1", "narrative_v2"]   # the live-row fold
    hist = history(con)
    assert len(hist) == 1 and hist[0]["narrative_text"] == "EDITION ONE"
    assert hist[0]["token_cost"] == old["token_cost"]


def test_a_narrative_only_re_run_after_a_failed_regenerate_completes_it(con):
    """FLAG F1, implemented as the dispatch's stated default (the gate rules):
    a `--no-refresh` re-run issued after a failed regenerate consumes the stale
    staged slots and promotes them — i.e. it FINISHES the interrupted
    regenerate rather than re-writing the old edition."""
    rank(con, "First story")
    publish(con, "EDITION ONE")
    rank(con, "Second story")          # regenerate stages, then dies

    inputs = generate.load_briefing_inputs(con, DATE, prefer_pending=True)
    assert inputs["slots"][0]["story_title"] == "Second story"
    publish(con, "EDITION TWO")

    row = live(con)
    assert "Second story" in row["story_slots"]
    assert row["narrative_text"] == "EDITION TWO"
    assert staged(con) is None
    assert len(history(con)) == 1 and history(con)[0]["narrative_text"] == "EDITION ONE"


# --- 3b. who may consume a staged selection (FIX-2, FIX-3) ------------------

def _published_edition_with_a_staged_selection(con, monkeypatch, fake_model):
    """A published edition of record + an interrupted regenerate's staged
    selection, ready for a real `run_generate` through the writer."""
    _fake_audio_ok(monkeypatch, [])
    live_slots = [gen_slot(1, title="LIVE STORY OF RECORD")]
    seed_published_edition(con, A_DAY, live_slots, narrative="EDITION ONE")
    rank(con, "STAGED STORY NOT YET PUBLISHED", date=A_DAY, item_ids=[1])
    fake_model.narrative = stories_payload(live_slots)
    fake_model.script = compliant_script(live_slots)
    return live_slots


def test_a_sample_never_consumes_a_staged_selection(con, fake_model, monkeypatch):
    """FIX-2 (from QA-2). A sample cannot promote, forces refresh=False, and
    promises in its own warning that the briefing of record is untouched and
    that it consumed the existing row. With an unconditional opt-in it narrated
    the STAGED selection instead — no data damage, but a sample that misreports
    what it sampled is exactly the small lie this product does not get to tell."""
    _published_edition_with_a_staged_selection(con, monkeypatch, fake_model)

    rep = generate.run_generate(date=A_DAY, con=con, env=GEN_ENV,
                                variant_override="B")
    assert rep.sample is True

    prompt = fake_model.calls[0]["prompt"]
    assert "LIVE STORY OF RECORD" in prompt
    assert "STAGED STORY NOT YET PUBLISHED" not in prompt
    assert any("briefing of record is untouched" in w for w in rep.warnings)
    # the record and the interrupted run's staging both survive the sample
    assert live(con, A_DAY)["narrative_text"] == "EDITION ONE"
    assert staged(con, A_DAY) is not None


def test_completing_an_interrupted_regenerate_says_so(con, fake_model, monkeypatch):
    """FIX-3 (ruling R1). No expiry — silently discarding a staged row would be
    a new silent data decision — but consuming a selection this run did not
    create must be disclosed, with the moment it was ranked."""
    _published_edition_with_a_staged_selection(con, monkeypatch, fake_model)
    stamp = staged(con, A_DAY)["created_at"]

    rep = generate.run_generate(date=A_DAY, con=con, env=GEN_ENV, refresh=False)

    disclosure = [w for w in rep.warnings
                  if "completing an earlier interrupted regenerate" in w]
    assert len(disclosure) == 1, rep.warnings
    assert stamp in disclosure[0]                  # WHICH selection, not just that
    assert "newslens generate" in disclosure[0]    # and the way out
    # it really did complete it — the disclosure describes what happened
    assert "STAGED STORY NOT YET PUBLISHED" in live(con, A_DAY)["story_slots"]
    assert staged(con, A_DAY) is None


def test_a_fresh_regenerate_consuming_its_own_staging_stays_silent(
    con, fake_model, monkeypatch
):
    """CARRIED INVARIANT / negative pin (born green against the pre-fix batch,
    where no disclosure existed at all, so its silence was vacuous). The other
    half of FIX-3: a refresh run's staged row is seconds old and its own.
    Warning on that would train the principal to ignore the warning, which is
    how a disclosure surface dies."""
    from newslens import ingest as ingest_mod

    live_slots = [gen_slot(1, title="LIVE STORY OF RECORD")]
    _fake_audio_ok(monkeypatch, [])
    seed_published_edition(con, A_DAY, live_slots, narrative="EDITION ONE")

    def fake_ingest(con=None, env=None):
        r = type("R", (), {})()
        r.succeeded, r.attempted, r.items_new = ["s"], 1, 0
        r.discovery_status, r.degradation_message = "ok", None
        return r

    def fake_rank(date=None, con=None, env=None, **kw):
        rank(con, "THIS RUN'S OWN SELECTION", date=date, item_ids=[1])
        r = type("R", (), {})()
        r.warnings = []
        return r

    monkeypatch.setattr(ingest_mod, "run_ingest", fake_ingest)
    monkeypatch.setattr(ranking, "run_rank", fake_rank)
    fake_model.narrative = stories_payload(live_slots)
    fake_model.script = compliant_script(live_slots)

    rep = generate.run_generate(date=A_DAY, con=con, env=GEN_ENV, refresh=True)

    assert not [w for w in rep.warnings
                if "completing an earlier interrupted regenerate" in w]
    # ...and it did consume its own staging, so silence is not vacuous
    assert "THIS RUN'S OWN SELECTION" in live(con, A_DAY)["story_slots"]
    assert staged(con, A_DAY) is None


# --- 4. isolation and degradation -------------------------------------------

def test_the_reader_facing_surfaces_never_mention_the_staging_table():
    """STRUCTURAL PIN, vacuously green at f4510fa (the table did not exist
    there) — it exists to bite the day someone wires the reader to the staging
    area. Isolation by construction: staged CONTENT never reaches a reader
    surface. If the server could render staged rows, the invariant would be a
    convention instead of a structure.

    CARVE-OUT, recorded by the NL-107 gate ruling R1 (2026-07-27): the reader
    may consult staging EXISTENCE — one bit, through `analysis.rival_exists`,
    which reads no column of the staged row and fails OPEN on any
    OperationalError. The bit only chooses between two reads of honestly
    persisted `analysis_briefs` rows, so its degraded answer is the pre-NL-107
    newest-valid read and can never show a lie. That is deliberately the
    opposite arm from FIX-1's fail-CLOSED staged read at the promote, which
    gated a WRITE. This test's assertions are unchanged and still bind:
    `server.py`/`webui.py` name the staging table nowhere; the existence check
    lives in `analysis.py`."""
    for name in ("server.py", "webui.py"):
        assert "briefings_pending" not in (SRC / name).read_text(encoding="utf-8")


def test_reads_degrade_to_the_live_row_on_a_database_without_0023(con):
    """A pre-0023 database has nothing staged, by definition. The mid-run reads
    must degrade to the live row, never kill a run."""
    rank(con, "First story")
    publish(con, "EDITION ONE")
    with con:
        con.execute("DROP TABLE briefings_pending")

    assert ranking.pending_selection(con, DATE) is None
    inputs = generate.load_briefing_inputs(con, DATE, prefer_pending=True)
    assert inputs["slots"][0]["story_title"] == "First story"
    publish(con, "EDITION ONE REDONE")             # promote path degrades too
    assert live(con)["narrative_text"] == "EDITION ONE REDONE"


def test_staging_fails_loud_when_the_table_is_missing_and_saves_the_edition(con):
    """The WRITE takes the opposite stance to the reads: a staging write that
    cannot land must never fall back to the destructive path. Fail loud, and
    leave the reader's edition exactly as it was."""
    rank(con, "First story")
    publish(con, "EDITION ONE")
    before = live(con)
    with con:
        con.execute("DROP TABLE briefings_pending")

    with pytest.raises(ranking.RankingError, match="0023"):
        rank(con, "Second story")

    after = live(con)
    for col in BRIEFING_COLUMNS:
        assert after[col] == before[col], f"the refused re-rank changed {col}"
    assert history(con) == []


class _PendingReadsLocked:
    """A connection proxy in which ONLY reads of briefings_pending raise
    `database is locked`. Everything else — the archive INSERT, the live-row
    UPDATE, the transaction itself — behaves normally, which is precisely the
    freak world G-1 describes: one read failing while the surrounding writes
    would have succeeded."""

    def __init__(self, con):
        self._con = con

    def execute(self, sql, *args, **kwargs):
        if "FROM briefings_pending" in sql:
            raise sqlite3.OperationalError("database is locked")
        return self._con.execute(sql, *args, **kwargs)

    def __enter__(self):
        return self._con.__enter__()

    def __exit__(self, *exc):
        return self._con.__exit__(*exc)

    def __getattr__(self, name):
        return getattr(self._con, name)


def test_a_failed_staged_read_propagates_rather_than_choosing_a_write_path(con):
    """FIX-1 (gate finding G-1). `no such table` is positive evidence that
    nothing was staged; `database is locked` is evidence of nothing at all — and
    this read decides WHICH WRITE PATH THE PROMOTE TAKES. Swallowing it would
    silently install a new narrative against the OLD slots while the staged row
    survived: the banned mixture, produced with no error surfaced anywhere.

    The correct behaviour is to refuse. A regenerate that cannot find out
    whether a selection is staged has no business publishing."""
    rank(con, "First story")
    publish(con, "EDITION ONE")
    rank(con, "Second story")
    before, before_staged = live(con), staged(con)

    with pytest.raises(sqlite3.OperationalError, match="database is locked"):
        generate.persist_generation(_PendingReadsLocked(con), DATE,
                                    "EDITION TWO", "SCRIPT TWO", [])

    for col in BRIEFING_COLUMNS:
        assert live(con)[col] == before[col], f"the refused promote changed {col}"
    assert staged(con) == before_staged      # still staged, still completable
    assert history(con) == []                # and nothing was archived
    # the mid-run reader refuses on the same terms
    with pytest.raises(sqlite3.OperationalError, match="database is locked"):
        generate.load_briefing_inputs(_PendingReadsLocked(con), DATE,
                                      prefer_pending=True)


def test_the_staging_table_has_the_shape_the_contract_specifies(con):
    cols = {r["name"]: r for r in con.execute(
        "PRAGMA table_info(briefings_pending)")}
    assert set(cols) == {"date", "story_slots", "corroboration_labels",
                         "token_cost", "created_at"}
    assert cols["date"]["pk"] == 1                 # one staged selection per date
    assert cols["story_slots"]["notnull"] == 1
    assert cols["created_at"]["notnull"] == 1


# --- 5. G6, re-anchored: what the reader and the panel now see ---------------

@pytest.fixture
def errjob(monkeypatch):
    monkeypatch.setattr(server, "GEN_JOB", server._GenJob())
    server.GEN_JOB.state = "error"
    server.GEN_JOB.error = "script stage failed"
    yield


def _panel(page: str) -> str:
    """The failure panel, whichever surface currently owns it.

    STAGE-0 C1 UPDATE (2026-07-28) — assertions UNCHANGED; only the extraction
    moved. The empty-panel pin below is explicitly about a FAILED FIRST RUN, and
    a first run is precisely what the Commissioning's founding page now owns
    (first-run state matrix). Its failure panel states the outcome from the SAME
    predicate the app's does (server._failure_outcome), so "The saved edition is
    empty." is still asserted against the words the reader actually sees."""
    if 'id="view-today"' in page:
        today = page.split('id="view-today"')[1].split('id="view-following"')[0]
        return re.search(r'<div class="state-panel">.*?</div>',
                         today, re.S).group(0)
    m = re.search(r'<div class="panel" id="c3-failed">.*?</div>', page, re.S)
    assert m, "neither the app's failure panel nor the founding page's rendered"
    return m.group(0)


INTACT = "The saved edition is intact."
EMPTY = "The saved edition is empty."
NOTHING = "Nothing was published."


def test_g6_a_failed_regenerate_leaves_the_old_edition_intact_end_to_end(ui, errjob):
    """G6, RE-ANCHORED (NL-103 QA-1 flipped). Same scenario, same construction —
    except the re-rank runs through `ranking.persist` instead of a hand-copy of
    its old destructive shape. The outcome inverts: the reader's edition still
    opens, and the panel's 'intact' claim is now TRUE.

    The f4510fa acceptance pair proved the panel never LIES about this. This
    proves the pipeline no longer creates the state it would have had to lie
    about."""
    today = datetime.now().strftime("%Y-%m-%d")
    con = db.connect()
    seed_briefing(con, date=today)                          # published + readable
    log = Path(paths.DATA_DIR) / "generation_log.jsonl"
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_text(json.dumps({
        "date": today, "status": "ok", "sample": False,
        "stories": [{"tier": "full", "headline": "Chip export controls pass",
                     "lede": "The lede sentence.",
                     "why_it_matters": "Concrete effects.",
                     "watch_for": "The vote."}]}) + "\n", encoding="utf-8")

    code, _, body = get(ui, f"/edition?date={today}")
    assert "Chip export controls pass" in body.decode("utf-8"), \
        "fixture broken: the edition was not readable to begin with"

    rank(con, "A completely different lead", date=today)    # the regenerate's rank

    # generate.run_generate's failed-run log entry: a later stage died
    with log.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps({"date": today, "status": "failed",
                             "error": "script stage failed", "steps": [],
                             "total_usd": 0.0, "warnings": []}) + "\n")

    # THE ASSERTION THE DEFECT FAILS (deliberately before any staging read, so
    # its red is the finding itself and not a missing-table scaffolding error).
    code, _, body = get(ui, f"/edition?date={today}")
    page = body.decode("utf-8")
    assert "Chip export controls pass" in page, \
        "the failed regenerate took the reader's edition with it"
    assert "A completely different lead" not in page, \
        "the staged selection leaked into the reader's edition"

    _, _, body = get(ui, "/")
    panel = _panel(body.decode("utf-8"))
    assert INTACT in panel and EMPTY not in panel and NOTHING not in panel

    pend = staged(con, today)
    con.close()
    assert pend is not None, "the regenerate did not stage its new selection"


def test_the_empty_panel_state_is_still_reachable_via_a_failed_first_run(ui, errjob):
    """CARRIED INVARIANT (born green at f4510fa — first-run behaviour is
    deliberately unchanged, F2). Three-state coverage MOVED, not deleted: 'The
    saved edition is empty.' is no longer reachable by failing a REGENERATE, but
    it is still honest and still reachable the other way — a FIRST run whose
    rank committed a row and whose later stage died. That edition really is
    empty, and the panel must keep saying so. (That this run stages nothing is
    pinned separately, in the bodyless-arm test above.)"""
    today = datetime.now().strftime("%Y-%m-%d")
    con = db.connect()
    rank(con, "First story", date=today)                    # rank only, no body
    assert live(con, today)["narrative_text"] is None
    con.close()

    _, _, body = get(ui, "/")
    panel = _panel(body.decode("utf-8"))
    assert EMPTY in panel and INTACT not in panel and NOTHING not in panel
