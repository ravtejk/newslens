"""NL-107 — analysis briefs are read against the edition of record.

THE DEFECT (implementer FINDING 1 + QA-1 + gate R9 on the NL-106 loop):
`analysis_briefs` rows are written MID-RUN, before the promote, and every
reader outside the generating run picked them by "newest row per (date, slot)".
So a regenerate that died after the analysis stage left the intact OLD edition
rendering the NEW run's "full picture" — the reader's story and the pane under
it describing two different selections. NL-106 did not cause this (a hard crash
at f4510fa rendered a three-way mixture); it removed the blindfold, and it made
the backfill's published-gate reachable in that world for the first time.

THE INVARIANT THESE TESTS HOLD: every consumer OUTSIDE the generating run — the
server's deep view, the memory backfill, the prompt batteries — sees only
briefs coherent with the live edition of record. An in-flight or failed run's
briefs are invisible outside that run until its own promote installs the
edition they describe.

THE MECHANISM: read-side selection only. `analysis_briefs` stays append-only
(migration 0009 — no trigger touched, no migration added, nothing deleted). The
predicate is the edition's own publish stamp: the newest valid brief for
(date, slot) written before `briefings.generated_at` (plus that stamp's
one-second flooring error bar — see analysis.publish_bound).

THE LEDGER, measured at a100f15 (24 tests, 20F/4P — every claim in this block
is the recorded HEAD-run result, per the born-red law in team/ENGINEERING.md):

  * 10 fail on BEHAVIOUR — the acceptance contracts. The QA-1 world through the
    real HTTP reader; the same world through the archive fragment; the mid-run
    window; the run/reader split; the backfill writing the dead run's arc; the
    backfill proceeding where it should refuse; the completion path's
    before-state; window_meta pairing (tooth d); and the two structural
    censuses of the call sites.
  * 10 fail on ABSENCE — new-surface pins naming `analysis.coherent_valid_brief`,
    `analysis.publish_bound` or `memory_core._brief_id_for`, which do not exist
    at a100f15. Several are carried invariants in substance (the status filter,
    the unpublished date) and say so in their own docstring.
  *  4 PASS at a100f15 — carried invariants, born green: the successful
    regenerate, the plain first run, window_meta on an ordinary date, and the
    scope tripwire (no migration, no trigger, no delete).

Fully offline: no LLM call, no network, sandboxed paths, $0.
"""

from __future__ import annotations

import ast
import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from newslens import (analysis, db, generate, memory_core, moat_battery, paths,
                      ranking, server)

from test_server import ui, get                              # noqa: F401
from test_nl106_stage_and_promote import (                   # noqa: F401
    _slot, con, live, publish, rank, staged,
)
from test_generate import (                                  # noqa: F401
    ENV as GEN_ENV, A_DAY, compliant_script, fake_model, _fake_audio_ok,
    seed_briefing as seed_published_edition, slot as gen_slot, stories_payload,
)
from test_m3_qa import m3_brief

SRC = Path(__file__).resolve().parents[1] / "src" / "newslens"
MIGRATIONS = Path(__file__).resolve().parents[1] / "migrations"
DATE = "2026-07-06"          # the date test_nl106_stage_and_promote.rank uses

OLD_MARK = "OLD RUN ANALYSIS MARKER"
NEW_MARK = "NEW RUN ANALYSIS MARKER"
RIVAL2_MARK = "SECOND RIVAL ANALYSIS MARKER"

STAMP_FMT = "%Y-%m-%dT%H:%M:%S.%fZ"


# --- helpers ----------------------------------------------------------------

def shift(stamp: str, seconds: float) -> str:
    """A stamp `seconds` away from `stamp`, in the millisecond-ISO shape both
    `analysis_briefs.created_at` and `briefings.generated_at` use."""
    t = datetime.strptime(stamp, STAMP_FMT) + timedelta(seconds=seconds)
    return t.strftime(STAMP_FMT)[:23] + "Z"


def brief_doc(marker: str, arc=None):
    b = m3_brief()
    b["pinned_facts"] = [{"fact": f"{marker} is on the record.", "cites": ["S1"]}]
    b["mechanism"] = f"{marker} explains the constraint [S1]."
    b["arc"] = arc
    return {"header": {"manifest": {}, "degraded": None}, "brief": b}


def insert_brief(con, marker: str, created_at: str, date: str = DATE,
                 slot: int = 1, status: str = "valid", arc=None) -> int:
    """A persisted brief with an EXPLICIT created_at.

    Explicit because the row cannot be corrected afterwards: `analysis_briefs`
    is append-only by trigger (migration 0009), so an UPDATE would be refused —
    which is exactly why this fix had to be read-side."""
    cur = con.execute(
        "INSERT INTO analysis_briefs (date, slot, tier, status, brief_json,"
        " model, cost_usd, created_at) VALUES (?, ?, 'full', ?, ?, 'stub',"
        " 0.0, ?)",
        (date, slot, status, json.dumps(brief_doc(marker, arc)), created_at))
    con.commit()
    return cur.lastrowid


def log_entry(date: str, headline: str) -> None:
    """The generation_log entry the reader surfaces render stories from."""
    entry = {"date": date, "variant": "A", "sample": False, "status": "ok",
             "stories": [{"tier": "full", "headline": headline,
                          "lede": "The lede.", "why_it_matters": "Effects.",
                          "watch_for": "The vote.",
                          "why_label": "Why it matters",
                          "watch_label": "Watch for", "my_read": None}]}
    log = paths.DATA_DIR / "generation_log.jsonl"
    log.parent.mkdir(parents=True, exist_ok=True)
    with log.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")


PUBLISHED_AT = "2026-07-06T08:00:00.000Z"


def published_edition(con, headline: str = "OLD HEADLINE",
                      narrative: str = "EDITION ONE",
                      at: str = PUBLISHED_AT) -> str:
    """A real published edition of record, through the real rank + promote —
    then its publish stamp PINNED to a fixed instant on its own date.

    Pinned because this world is minutes wide and the suite runs it in
    milliseconds: a regenerate ranks, fetches and analyses for many minutes
    before it promotes, and no test can wait for that. Every brief stamp here
    is relative to this instant, and any LATER real promote (wall clock, today)
    is unambiguously after all of them. `briefings.generated_at` is a mutable
    column the pipeline itself rewrites at every promote — nothing append-only
    is touched to do this."""
    rank(con, "First story")
    publish(con, narrative)
    con.execute("UPDATE briefings SET generated_at = ? WHERE date = ?",
                (at, DATE))
    con.commit()
    log_entry(DATE, headline)
    return at


def dead_regenerate(con, published_at: str, gap_s: float = 300.0, arc=None,
                    title: str = "Second story") -> int:
    """A regenerate that reached the analysis stage and died: it STAGED a new
    selection (the real rank path) and wrote a brief `gap_s` after the surviving
    edition's publish stamp — and never promoted. Returns the new brief's id.

    The `rank()` is load-bearing, not scenery (gate F4): under the ruled
    predicate the staged row is exactly what makes this a RIVAL world. A newer
    brief with nothing staged means post-hoc analysis of the live edition — the
    real 2026-07-05 shape — and must be read, not hidden."""
    rank(con, title)
    return insert_brief(con, NEW_MARK, shift(published_at, gap_s), arc=arc)


def page(ui, date: str = DATE) -> str:
    code, _, body = get(ui, f"/?date={date}")
    assert code == 200
    return body.decode("utf-8")


ADVANCING_OLD = {"delta": "advances",
                 "what_happened": "The blockade was reinstated in the strait.",
                 "significance": "A pricing dispute became a shooting war.",
                 "cites": ["S1", "R1"]}
ADVANCING_NEW = {"delta": "advances",
                 "what_happened": "The summit collapsed without a communique.",
                 "significance": "The diplomatic track closed for the season.",
                 "cites": ["S1", "R1"]}


def seed_thread(con, topic="Iran War") -> int:
    now = "2026-07-01T00:00:00.000Z"
    cur = con.execute(
        "INSERT INTO memory (topic, status, status_changed_at, created_at,"
        " updated_at) VALUES (?, 'active', ?, ?, ?)", (topic, now, now, now))
    con.commit()
    return cur.lastrowid


def state_chat(cost=0.02):
    def chat(key, prompt):
        return ({"state": f"The conflict is open war ({DATE})."}, cost)
    return chat


# ===========================================================================
# 1. the reader surfaces — the QA-1 world, end to end (RED at a100f15)
# ===========================================================================

def test_a_failed_regenerate_never_hangs_its_brief_under_the_saved_edition(ui):
    """RED CARRIER. The QA-1 world through the REAL HTTP reader: a published
    edition with its own analysis, then a regenerate that stages, analyses and
    dies. The reader keeps the whole old edition — headline AND the full-picture
    pane that was written about it. The dead run's brief is not reachable from
    any reader surface until an edition it describes is published."""
    con = db.connect()
    published_at = published_edition(con)
    old_id = insert_brief(con, OLD_MARK, shift(published_at, -300))
    new_id = dead_regenerate(con, published_at)
    assert new_id > old_id                       # newest-wins would pick NEW

    html = page(ui)

    assert "OLD HEADLINE" in html                # the edition itself is intact
    assert OLD_MARK in html                      # paired with its OWN analysis
    assert NEW_MARK not in html
    # ...and the run that died is still on the forensic record, untouched.
    assert con.execute(
        "SELECT COUNT(*) c FROM analysis_briefs WHERE date = ?",
        (DATE,)).fetchone()["c"] == 2


def test_the_archive_fragment_obeys_the_same_bound_as_today(con):
    """RED CARRIER. The second deep-view call site: the archive-in-place
    edition fragment (NL-11). Same world, same answer — a date is rendered with
    its own briefs whichever door the reader came through."""
    published_at = published_edition(con)
    insert_brief(con, OLD_MARK, shift(published_at, -300))
    dead_regenerate(con, published_at)

    html, date_read = server.build_edition_fragment(con, DATE)

    assert date_read == DATE
    assert "OLD HEADLINE" in html
    assert OLD_MARK in html
    assert NEW_MARK not in html


def test_the_mid_run_window_shows_the_reader_only_the_published_analysis(ui):
    """RED CARRIER (tooth c). The transient window every SUCCESSFUL regenerate
    passes through: briefs land minutes before the promote. Until the promote,
    the reader's edition is the old one and so is its analysis. Modelled as the
    live world the run leaves behind at that instant — staged selection present,
    new brief written, no promote yet."""
    con = db.connect()
    published_at = published_edition(con)
    insert_brief(con, OLD_MARK, shift(published_at, -300))
    dead_regenerate(con, published_at, gap_s=120.0)
    assert staged(con, DATE) is not None          # the run really is in flight

    html = page(ui)

    assert OLD_MARK in html
    assert NEW_MARK not in html


def test_the_run_reads_its_own_fresh_brief_while_the_reader_reads_the_old_one(ui):
    """RED CARRIER — the SPLIT itself. Run-internal semantics are untouched:
    the in-flight run's own read (analysis.latest_valid_brief, the call the
    narrative pass makes) still returns the brief it just wrote, at the same
    instant the reader is served the published edition's. Both halves are
    asserted in one world, so neither can be satisfied by breaking the other."""
    con = db.connect()
    published_at = published_edition(con)
    insert_brief(con, OLD_MARK, shift(published_at, -300))
    dead_regenerate(con, published_at)

    run_read = analysis.latest_valid_brief(con, DATE, 1)
    assert NEW_MARK in json.dumps(run_read)       # the run sees its own work

    html = page(ui)
    assert OLD_MARK in html and NEW_MARK not in html


# ===========================================================================
# 2. the ledger — the backfill tooth (b) (RED at a100f15)
# ===========================================================================

def test_the_backfill_writes_the_published_editions_arc_not_the_dead_runs(con):
    """RED CARRIER (tooth b). Pre-NL-106 the published-gate refused here — not
    by design but by accident, because a failed regenerate had blanked the
    narrative. Now the gate correctly passes (the old edition genuinely IS
    published), so the coherence has to come from the brief read: the delta
    entering the thread ledger must be the arc of the edition it cites, and the
    brief_id it carries must be that same brief.

    The dead run STAGES (gate F4) — without it this would be the post-hoc
    world, where the newer brief is the edition's own and must be consumed."""
    tid = seed_thread(con)
    published_at = published_edition(con)
    old_id = insert_brief(con, OLD_MARK, shift(published_at, -300),
                          arc=ADVANCING_OLD)
    new_id = dead_regenerate(con, published_at, arc=ADVANCING_NEW)
    # the live slot matches the thread, so the pass has something to write
    con.execute("UPDATE briefings SET story_slots = ? WHERE date = ?",
                (json.dumps([{"slot": 1, "story_title": "First story",
                              "matched_memory": ["Iran War"],
                              "matched_dormant": [], "item_ids": []}]), DATE))
    con.commit()

    rep = generate.run_memory_backfill(DATE, con=con, env={},
                                       state_chat=state_chat())

    assert rep.refused is False and rep.deltas_written == 1
    row = con.execute(
        "SELECT what_happened, brief_id FROM thread_deltas WHERE thread_id = ?"
        " AND edition_date = ?", (tid, DATE)).fetchone()
    assert row["what_happened"] == ADVANCING_OLD["what_happened"]
    assert row["brief_id"] == old_id and row["brief_id"] != new_id


def test_the_backfill_refuses_when_only_a_dead_runs_brief_exists(con):
    """The bound must not be able to FABRICATE coherence: an edition published
    with no analysis of its own, beside a STAGED dead regenerate's brief,
    refuses on the existing string rather than borrowing the rival's. (New-
    surface pin: at a100f15 this world proceeded and wrote the dead run's arc.)

    Contrast with `test_the_backfill_proceeds_on_the_post_hoc_world` below:
    same rows, no staged rival — and there the brief IS the edition's own."""
    seed_thread(con)
    published_at = published_edition(con)
    dead_regenerate(con, published_at, arc=ADVANCING_NEW)
    con.execute("UPDATE briefings SET story_slots = ? WHERE date = ?",
                (json.dumps([{"slot": 1, "story_title": "First story",
                              "matched_memory": ["Iran War"],
                              "matched_dormant": [], "item_ids": []}]), DATE))
    con.commit()

    rep = generate.run_memory_backfill(DATE, con=con, env={},
                                       state_chat=state_chat())

    assert rep.refused is True
    assert "no valid analysis brief persisted" in rep.reason
    assert con.execute(
        "SELECT COUNT(*) c FROM thread_deltas").fetchone()["c"] == 0


def test_the_live_memory_pass_runs_after_the_promote_so_newest_is_coherent(con):
    """The reason the inline pass keeps its unbounded read. Two halves:

    (a) ORDER — CARRIED INVARIANT, green at a100f15. In _run_generate_body the
        promote precedes the memory pass (the M1 gate-F orphan-delta reorder:
        no delta is written until its edition is published). Pinned on the
        source so a future reorder cannot silently make the unbounded read
        wrong.
    (b) CONSEQUENCE — new-surface pin: in the post-promote world the unbounded
        brief id and the bounded one are the same row, and they STAY the same
        row once a rival stages (the ledger cites the edition's own brief)."""
    body = (SRC / "generate.py").read_text(encoding="utf-8")
    body = body.split("def _run_generate_body")[1]
    assert body.index("persist_generation(con, date, narrative") \
        < body.index("run_memory_pass(con, date, key, cap")

    published_at = published_edition(con)
    own = insert_brief(con, OLD_MARK, shift(published_at, -300))
    assert memory_core._brief_id_for(con, DATE, 1, None) == own
    assert memory_core._brief_id_for(con, DATE, 1, published_at) == own

    rival = dead_regenerate(con, published_at)     # a rival stages and dies
    assert memory_core._brief_id_for(con, DATE, 1, published_at) == own
    assert memory_core._brief_id_for(con, DATE, 1, None) == rival  # run-internal


# ===========================================================================
# 3. the normal cases the bound must not break (carried invariants)
# ===========================================================================

def test_a_successful_regenerate_publishes_its_own_analysis_immediately(ui):
    """CARRIED INVARIANT (born green) — the don't-break-the-normal-case pin. A
    regenerate that COMPLETES promotes new slots and a new stamp, and its briefs
    (written before that stamp) are the edition's own from the first render."""
    con = db.connect()
    published_at = published_edition(con)
    insert_brief(con, OLD_MARK, shift(published_at, -300))
    rank(con, "Second story")
    insert_brief(con, NEW_MARK, shift(published_at, 300))
    publish(con, "EDITION TWO")                  # the promote
    log_entry(DATE, "NEW HEADLINE")

    html = page(ui)

    assert "NEW HEADLINE" in html
    assert NEW_MARK in html
    assert OLD_MARK not in html


def test_a_no_refresh_completion_installs_the_interrupted_runs_analysis(ui):
    """RED CARRIER in its first half (the before-state is the QA-1 world), then
    the behaviour that must NOT change — the R1 completion path. `--no-refresh`
    after a failed regenerate finishes the run someone else started: its promote
    stamps generated_at AFTER the earlier run's briefs, so exactly those briefs
    become the edition of record's. The bound is what makes them appear at the
    same instant as the edition they describe, not before it."""
    con = db.connect()
    published_at = published_edition(con)
    insert_brief(con, OLD_MARK, shift(published_at, -300))
    dead_regenerate(con, published_at)

    assert OLD_MARK in page(ui) and NEW_MARK not in page(ui)   # before

    publish(con, "EDITION TWO")                  # the completion's promote
    log_entry(DATE, "NEW HEADLINE")

    html = page(ui)                              # after
    assert "NEW HEADLINE" in html
    assert NEW_MARK in html and OLD_MARK not in html


def test_a_first_run_publishes_its_own_briefs_without_any_staging(ui):
    """CARRIED INVARIANT (born green). The plain first-run path — rank, analyse,
    promote, nothing staged — renders its own brief. The bound is inert here:
    the analysis ran two minutes before the promote, which is the ordinary
    shape of every run."""
    con = db.connect()
    published_at = published_edition(con)
    assert staged(con, DATE) is None
    insert_brief(con, OLD_MARK, shift(published_at, -120))

    html = page(ui)
    assert "OLD HEADLINE" in html and OLD_MARK in html


def test_an_unpublished_first_run_renders_nothing_but_hides_nothing(ui, con):
    """The bodyless world (NL-106 R2, out of scope there): a first run that
    ranked and died leaves a row with NO narrative, whose generated_at is the
    RANK stamp — a context START, not an end. Two halves:

    (a) READER — CARRIED INVARIANT, byte-identical to a100f15: nothing is
        published, so nothing renders.
    (b) THE READ — gate fix F-c/G-2 acceptance: with no rival staged, the direct
        read returns the run's own newest brief. Bounding against a rank stamp
        was backwards — it excluded the current context's post-rank analysis
        while admitting the previous context's leftovers."""
    before = datetime.now(timezone.utc).strftime(STAMP_FMT)[:23] + "Z"
    rank(con, "First story")
    row = live(con, DATE)
    assert row["narrative_text"] is None
    # NL-106 leaves the untouched (bodyless/no-row) arms stamping generated_at
    # at RANK time — verified here, because that is the stamp G-2 is about.
    assert row["generated_at"][:19] >= before[:19]
    own = insert_brief(con, NEW_MARK, shift(row["generated_at"], 300))

    assert NEW_MARK not in page(ui)                                    # (a)
    doc = analysis.coherent_valid_brief(con, DATE, 1, row["generated_at"])
    assert NEW_MARK in json.dumps(doc)                                 # (b)
    assert analysis.coherent_valid_brief_id(
        con, DATE, 1, row["generated_at"]) == own
    # and the dev harnesses that read this date carry it too (F-c)
    inputs = moat_battery.load_inputs(con, DATE)
    assert NEW_MARK in json.dumps(inputs["briefs_by_slot"])


# ===========================================================================
# 4. the predicate itself — the boundary, both sides (new-surface pins)
# ===========================================================================

@pytest.mark.parametrize("offset_s,visible", [
    (-0.001, True),      # written a millisecond before the stamp
    (0.0, True),         # written in the stamp's own instant
    (0.999, True),       # inside the stamp's flooring error bar
    (1.0, False),        # one full second after: outside it
    (300.0, False),      # the real shape of a dead regenerate's brief
])
def test_the_publish_bound_admits_exactly_the_flooring_error_bar(
        con, offset_s, visible):
    """NEW-SURFACE PIN, on the RIVAL arm (gate F4: the rank is what puts the
    bound under test at all). Both writers of generated_at floor it to the
    second, so the promote happened somewhere in [stamp, stamp+1s) and a brief
    written just before it can read up to 999ms later than the stamp that
    published it. The bound is that interval and nothing wider — pinned on both
    sides so a future widening has to break a test to happen."""
    published_at = published_edition(con)
    rank(con, "Second story")                    # a rival stages
    insert_brief(con, NEW_MARK, shift(published_at, offset_s))
    doc = analysis.coherent_valid_brief(con, DATE, 1, published_at)
    assert (doc is not None) is visible
    if visible:
        assert NEW_MARK in json.dumps(doc)


def test_the_bound_is_the_stamp_plus_one_second_in_the_stamps_own_shape():
    """NEW-SURFACE PIN. The bound is a plain string compare against
    created_at's millisecond-ISO shape (23 chars + Z), it rolls over correctly,
    and an unparseable legacy stamp falls back to the STRICTEST reading (the
    raw value) rather than a wider one."""
    assert analysis.publish_bound("2026-07-27T12:00:00.000Z") \
        == "2026-07-27T12:00:01.000Z"
    assert analysis.publish_bound("2026-07-27T23:59:59.999Z") \
        == "2026-07-28T00:00:00.999Z"
    assert analysis.publish_bound("not-a-stamp") == "not-a-stamp"


def test_a_rejected_brief_is_still_never_read_however_recent(con):
    """CARRIED INVARIANT in substance (status='valid' filtering is unchanged by
    either regime), new-surface pin in form: a rejected brief is never the
    reader's — inside the bound on the rival arm, or newest-wins without one."""
    published_at = published_edition(con)
    insert_brief(con, OLD_MARK, shift(published_at, -300))
    insert_brief(con, NEW_MARK, shift(published_at, -60), status="rejected")
    assert OLD_MARK in json.dumps(                       # no rival: newest wins
        analysis.coherent_valid_brief(con, DATE, 1, published_at))
    rank(con, "Second story")                            # rival: bounded
    assert OLD_MARK in json.dumps(
        analysis.coherent_valid_brief(con, DATE, 1, published_at))


def test_no_edition_of_record_means_no_coherent_brief(con):
    """NEW-SURFACE PIN: nothing is coherent with an edition that does not
    exist, so an absent/empty stamp reads as no brief — never as no bound."""
    insert_brief(con, NEW_MARK, "2026-07-06T12:00:00.000Z")
    assert analysis.coherent_valid_brief(con, DATE, 1, None) is None
    assert analysis.coherent_valid_brief(con, DATE, 1, "") is None
    assert analysis.coherent_valid_brief_id(con, DATE, 1, None) is None


# ===========================================================================
# 5. window_meta — tooth (d), F-a (RED at a100f15)
# ===========================================================================

def _rank_run(con, ran_at: str, start_iso: str, date: str = DATE) -> None:
    """One instrumentation row with an EXPLICIT ran_at and window.

    Hand-INSERTed because `ranking_runs` is append-only too (migration 0004):
    the stamp can only be written at insert time, and the real rank path takes
    it from the column default (wall clock). The rows the real ranks in this
    test wrote carry today's wall clock and sit outside the bound; these two
    are the ones the assertions are about."""
    con.execute(
        "INSERT INTO ranking_runs (date, meta, token_usage, ran_at)"
        " VALUES (?, ?, '{}', ?)",
        (date, json.dumps({"status": "ok",
                           "window": {"days": 1.0, "start_iso": start_iso}}),
         ran_at))
    con.commit()


def test_window_meta_follows_the_payload_the_reader_actually_gets(con):
    """RED CARRIER (tooth d, gate INFO folded into T1). The fetch window is a
    property of the rank that chose the slots. A default reader on a
    stale-staged date gets the LIVE slots, so it must get the live edition's
    window — not the dead regenerate's. The in-flight run, reading its own
    STAGED slots, still gets its own rank's window."""
    published_at = published_edition(con)
    _rank_run(con, shift(published_at, -600), "OLD-WINDOW")
    rank(con, "Second story")                    # the dead regenerate: stages
    _rank_run(con, shift(published_at, 600), "NEW-WINDOW")
    assert staged(con, DATE) is not None

    default_reader = generate.load_briefing_inputs(con, DATE)
    assert default_reader["window_meta"]["window"]["start_iso"] == "OLD-WINDOW"

    in_flight = generate.load_briefing_inputs(con, DATE, prefer_pending=True)
    assert in_flight["window_meta"]["window"]["start_iso"] == "NEW-WINDOW"


def test_window_meta_survives_the_ordinary_published_date(con):
    """CARRIED INVARIANT (born green): on a date with no dead regenerate the
    bound changes nothing — the rank that produced the edition is inside it
    (the promote stamp postdates the rank), so the window still renders."""
    rank(con, "First story")
    publish(con, "EDITION ONE")
    inputs = generate.load_briefing_inputs(con, DATE)
    assert inputs["window_meta"] is not None
    assert inputs["window_meta"]["window"]["days"] == 1.0


# ===========================================================================
# 6. the no-rival regime — the gate's fix return (F5, born red against the
#    FIRST version of this batch, which bounded on the stamp alone)
# ===========================================================================

POST_HOC_DATE = "2026-07-05"
POST_HOC_PUBLISHED_AT = "2026-07-05T22:11:45.000Z"
POST_HOC_BRIEF_AT = ("2026-07-06T07:45:18.384Z", "2026-07-06T07:45:35.194Z")
POST_HOC_MARK = "POST-HOC ANALYSIS MARKER"


def post_hoc_edition(con) -> str:
    """The live 2026-07-05 shape, stamps and all.

    Not a hypothetical: this is the principal's own archive. That edition
    published at 22:11:45, and the analysis organ was built and run against it
    the NEXT MORNING (07:04–07:45) — so its only briefs postdate its promote by
    9.5 hours. There is no staged row, no later rank and no later promote for
    that date: these briefs have no rival, they describe exactly these slots,
    and they are the edition's own. Two slots, because the live world lost
    exactly two panes (slots 1 and 2) under a stamp-only bound.

    Deliberately NO generation_log entry — the reader parses the persisted
    narrative, as it does for that date."""
    report = ranking.RankReport(
        date=POST_HOC_DATE,
        slots=[_slot(1, "The lead story"), _slot(2, "The second story")],
        token_usage={"prompt_tokens": 100, "completion_tokens": 20})
    ranking.persist(con, report, {"window": {"days": 1.0}})
    slots = json.loads(live(con, POST_HOC_DATE)["story_slots"])
    stories = [{"tier": "full" if i == 0 else "medium",
                "headline": f"HEADLINE {i + 1}", "lede": "The lede.",
                "why_it_matters": "Effects.", "watch_for": "The vote.",
                "why_label": "Why it matters", "watch_label": "Watch for",
                "my_read": None} for i in range(2)]
    narrative = generate.assemble_narrative(
        POST_HOC_DATE, "A", stories,
        {"slots": slots, "items_by_slot": {1: [], 2: []}, "threads": [],
         "prior_ctx": None, "continuity_status": "none", "window_meta": None,
         "corroboration": {}})
    generate.persist_generation(con, POST_HOC_DATE, narrative, "SCRIPT", [])
    con.execute("UPDATE briefings SET generated_at = ? WHERE date = ?",
                (POST_HOC_PUBLISHED_AT, POST_HOC_DATE))
    con.commit()
    for i, at in enumerate(POST_HOC_BRIEF_AT, start=1):
        insert_brief(con, f"{POST_HOC_MARK} {i}", at, date=POST_HOC_DATE,
                     slot=i, arc=ADVANCING_OLD)
    return POST_HOC_PUBLISHED_AT


def test_a_post_hoc_analysed_edition_renders_its_own_briefs(con):
    """F5(a) — THE QA-1 ACCEPTANCE CONTRACT, born red against this batch's
    first version. An edition of record whose only valid briefs were written
    AFTER its promote stamp, with nothing staged and no newer rank, renders
    those briefs. A stamp-only bound took 2 of the live archive's 34 (date,
    slot) panes down; the rival gate restores them without readmitting a single
    rival brief (the dead-regenerate reds in this file stay green alongside)."""
    post_hoc_edition(con)
    assert staged(con, POST_HOC_DATE) is None       # no rival ever existed

    html, date_read = server.build_edition_fragment(con, POST_HOC_DATE)

    assert date_read == POST_HOC_DATE
    assert f"{POST_HOC_MARK} 1" in html
    assert f"{POST_HOC_MARK} 2" in html
    assert html.count('id="view-deep-') == 2        # both panes, not one


def test_the_backfill_proceeds_on_the_post_hoc_world(con):
    """F5(b) — 07-05's second surface. Under a stamp-only bound that date's
    backfill REFUSED ("no valid analysis brief persisted"), because the only
    briefs it had were the post-hoc ones. With no rival staged they are the
    edition's own: the pass proceeds and the delta cites one of them."""
    tid = seed_thread(con)
    post_hoc_edition(con)
    con.execute("UPDATE briefings SET story_slots = ? WHERE date = ?",
                (json.dumps([{"slot": 1, "story_title": "The lead story",
                              "matched_memory": ["Iran War"],
                              "matched_dormant": [], "item_ids": []}]),
                 POST_HOC_DATE))
    con.commit()

    rep = generate.run_memory_backfill(POST_HOC_DATE, con=con, env={},
                                       state_chat=state_chat())

    assert rep.refused is False and rep.deltas_written == 1
    row = con.execute(
        "SELECT what_happened, brief_id FROM thread_deltas WHERE thread_id = ?"
        " AND edition_date = ?", (tid, POST_HOC_DATE)).fetchone()
    assert row["what_happened"] == ADVANCING_OLD["what_happened"]
    assert row["brief_id"] == con.execute(
        "SELECT MAX(id) m FROM analysis_briefs WHERE date = ? AND slot = 1",
        (POST_HOC_DATE,)).fetchone()["m"]


def test_a_pre_0023_database_hides_nothing(con):
    """F5(c) — THE LIVE-DB-TODAY PIN (gate G-1). `briefings_pending` does not
    exist in the principal's DB until his next migrate, and on a pre-0023
    database no rival CAN exist: a re-rank of a readable edition fails loud
    there (NL-106's staging write). So the existence check fails OPEN and every
    reader behaves exactly as it did before this batch — the batch must not be
    able to remove content from a database it cannot yet protect."""
    post_hoc_edition(con)
    with con:
        con.execute("DROP TABLE briefings_pending")

    assert analysis.rival_exists(con, POST_HOC_DATE) is False   # degrades, no raise

    html, _ = server.build_edition_fragment(con, POST_HOC_DATE)
    assert f"{POST_HOC_MARK} 1" in html and f"{POST_HOC_MARK} 2" in html
    assert analysis.coherent_valid_brief(
        con, POST_HOC_DATE, 1, POST_HOC_PUBLISHED_AT) is not None


def test_two_consecutive_failed_regenerates_still_show_the_published_briefs(ui):
    """F5(d) — the stamp-advance immunity, on the record. A second failed
    regenerate re-stages with INSERT OR REPLACE, moving the staged row's
    created_at FORWARD. A predicate that bounded on the STAGED stamp would
    re-admit the first dead run's brief at that moment; reading only the
    EXISTENCE bit cannot. Both rivals stay invisible, the published edition
    keeps its own analysis."""
    con = db.connect()
    published_at = published_edition(con)
    insert_brief(con, OLD_MARK, shift(published_at, -300))
    dead_regenerate(con, published_at, gap_s=300)          # rival 1
    rank(con, "Third story")                               # rival 2 re-stages
    insert_brief(con, RIVAL2_MARK, shift(published_at, 600))
    assert "Third story" in staged(con, DATE)["story_slots"]   # replaced, not added
    # The advance itself, made explicit: the suite re-stages inside one second,
    # so push the staged stamp past rival 1's brief by hand. This is precisely
    # the state in which a staged-STAMP bound would re-admit that brief.
    con.execute("UPDATE briefings_pending SET created_at = ? WHERE date = ?",
                (shift(published_at, 450), DATE))
    con.commit()

    html = page(ui)

    assert OLD_MARK in html
    assert NEW_MARK not in html and RIVAL2_MARK not in html


def test_a_sample_in_a_stale_staged_world_reads_the_published_brief(
        con, fake_model, monkeypatch):
    """F5(e) — build flag F-b, fixed by gate F3, proven END TO END through a
    real sample run rather than at the seam. A sample is not a generation
    context: it forces refresh=False, so it never ranks, never stages and never
    writes a brief, and FIX-2 already has it consuming the LIVE row's slots.
    Pairing those slots with a rival's brief was the render mixture inside a
    sample artifact — so the sample's prompt carries the published edition's
    analysis and not the dead run's."""
    _fake_audio_ok(monkeypatch, [])
    live_slots = [gen_slot(1, title="LIVE STORY OF RECORD")]
    seed_published_edition(con, A_DAY, live_slots, narrative="EDITION ONE")
    published_at = live(con, A_DAY)["generated_at"]
    insert_brief(con, OLD_MARK, shift(published_at, -300), date=A_DAY)
    rank(con, "STAGED STORY NOT YET PUBLISHED", date=A_DAY, item_ids=[1])
    insert_brief(con, NEW_MARK, shift(published_at, 300), date=A_DAY)
    fake_model.narrative = stories_payload(live_slots)
    fake_model.script = compliant_script(live_slots)

    rep = generate.run_generate(date=A_DAY, con=con, env=GEN_ENV,
                                variant_override="B")

    assert rep.sample is True
    prompt = fake_model.calls[0]["prompt"]
    assert "LIVE STORY OF RECORD" in prompt        # FIX-2's slots, unchanged
    assert OLD_MARK in prompt                      # ...with the matching brief
    assert NEW_MARK not in prompt
    assert live(con, A_DAY)["narrative_text"] == "EDITION ONE"
    assert staged(con, A_DAY) is not None           # the rival's staging survives


# ===========================================================================
# 7. structure — the wiring, and what this fix did NOT touch
# ===========================================================================

def test_the_reader_holds_no_unbounded_brief_read():
    """RED CARRIER — the wiring pin. The server must reach analysis through the
    bounded reader only; an unbounded `latest_valid_brief(` in server.py is the
    defect itself. Kept structural because a call site is what regressed here.
    webui.py must not read briefs at all."""
    src = (SRC / "server.py").read_text(encoding="utf-8")
    assert "coherent_valid_brief(" in src
    assert "latest_valid_brief(" not in src
    assert "latest_valid_brief" not in (SRC / "webui.py").read_text(
        encoding="utf-8")


def test_every_outside_the_run_brief_read_is_bounded():
    """The call-site census, structural: exactly TWO unbounded brief reads
    survive in the package — the run's own document read (generate.py, the
    narrative pass) and the inline memory pass's id read (memory_core.py,
    post-promote by the gate-F ordering). Everything else — server, backfill,
    both batteries — goes through the bounded pair. A new unbounded reader has
    to edit this census to land."""
    calls = {}
    for f in sorted(SRC.glob("*.py")):
        for ln in f.read_text(encoding="utf-8").splitlines():
            if ln.lstrip().startswith("#") or ln.lstrip().startswith("def "):
                continue
            for name in ("latest_valid_brief(", "_latest_valid_brief_id("):
                if name in ln:
                    calls.setdefault(f.name, []).append(name)
    assert calls == {"generate.py": ["latest_valid_brief("],
                     "memory_core.py": ["_latest_valid_brief_id("]}


_SQL_SITE_RE = re.compile(r"\b(?:FROM|INTO|UPDATE|JOIN)\s+analysis_briefs\b")


def _raw_sql_sites():
    """Every raw SQL statement in the package that names `analysis_briefs`, at
    (file, function) granularity, with a count per site."""
    sites = {}
    for f in sorted(SRC.glob("*.py")):
        text = f.read_text(encoding="utf-8")
        hits = [i + 1 for i, ln in enumerate(text.splitlines())
                if _SQL_SITE_RE.search(ln) and not ln.lstrip().startswith("#")]
        if not hits:
            continue
        funcs = [(n.lineno, n.end_lineno, n.name) for n in ast.walk(ast.parse(text))
                 if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
        for ln in hits:
            owner = max([(a, nm) for a, b, nm in funcs if a <= ln <= b],
                        default=(0, "<module>"))[1]
            sites[(f.name, owner)] = sites.get((f.name, owner), 0) + 1
    return sites


def test_the_raw_sql_census_of_analysis_briefs_reads(tmp_path):
    """THE CENSUS THAT ACTUALLY BINDS (gate F6, from QA-3). The helper-name
    pins above are name-scans: a raw `SELECT … FROM analysis_briefs … ORDER BY
    id DESC` dropped into `server.py` sails past both while reinstating exactly
    the defect NL-107 closes. This pins the SQL itself — every statement naming
    the table, by file and function. Adding a reader anywhere in the package
    fails it, and the completeness claim stops living in report prose."""
    assert _raw_sql_sites() == {
        # the writer, and the two run-internal readers
        ("analysis.py", "persist_brief"): 1,
        ("analysis.py", "analyst_slot3_tier"): 1,
        ("analysis.py", "latest_valid_brief"): 1,
        # the outside-the-run pair: one statement per regime
        ("analysis.py", "coherent_valid_brief"): 2,
        ("analysis.py", "coherent_valid_brief_id"): 2,
        # the inline memory pass's cited-brief read
        ("memory_core.py", "_latest_valid_brief_id"): 1,
    }

    # ...and the pin bites on QA-3's exact injection (checked against a COPY —
    # the tree is never written to).
    injected = tmp_path / "server.py"
    injected.write_text(
        (SRC / "server.py").read_text(encoding="utf-8").replace(
            "def _collect_deep_views(",
            "def _injected_reader(con, date):\n"
            "    return con.execute(\n"
            "        \"SELECT brief_json FROM analysis_briefs WHERE date = ?\"\n"
            "        \" AND status = 'valid' ORDER BY id DESC LIMIT 1\",\n"
            "        (date,)).fetchone()\n\n\n"
            "def _collect_deep_views(", 1), encoding="utf-8")
    hits = [ln for ln in injected.read_text(encoding="utf-8").splitlines()
            if _SQL_SITE_RE.search(ln)]
    assert len(hits) == 1, "the injection must be visible to the census scanner"


def test_the_fix_is_read_side_only_and_the_append_only_law_is_untouched():
    """THE SCOPE TRIPWIRE, as a test. This fix adds no migration and no
    trigger: `analysis_briefs` is still the forensic record, and the dead run's
    brief is still on it — hidden from readers, never removed."""
    names = sorted(p.name for p in MIGRATIONS.glob("*.sql"))
    # NL-17 M1 added 0024/0025; this tripwire is about NL-107 adding NONE,
    # so it tracks the roster rather than freezing a number that any later
    # milestone legitimately moves.
    assert names[-1] == "0026_vocabulary_moves.sql"
    assert len(names) == 26
    sql = (MIGRATIONS / "0009_analysis_append_only_and_retrieval.sql").read_text(
        encoding="utf-8")
    assert "RAISE(ABORT" in sql
    for name in ("analysis.py", "server.py", "generate.py", "memory_core.py",
                 "battery.py", "moat_battery.py"):
        src = (SRC / name).read_text(encoding="utf-8")
        assert "DELETE FROM analysis_briefs" not in src
        assert "UPDATE analysis_briefs" not in src
