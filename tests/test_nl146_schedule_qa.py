"""NL-146 QA — acceptance contracts (2026-08-13 adversarial pass).

BORN RED ON THE NL-146 BUILD TREE, deliberately: each test below is the
acceptance criterion for a finding in the QA report
(workspace/products/newslens/research/2026-08-13--nl146-qa.md). The fix loop
flips them green; nobody value-swaps them. Reds observed on the build tree at
1f92260+NL-146 (uncommitted) — the observed-red receipts are in the QA report.

THE FINDING BEHIND THE FIRST THREE (F-1, HIGH): `server._log_entry_for` matches
`e.get("date") == date and not e.get("sample")` — and NL-146's fire-decision
lines (schedule.log_fire) carry a `date` key and no `sample` key, so THE FIRE
LINE ITSELF becomes "the run entry for the date" the moment the ladder writes
it. Stage lines never bit here because they land BEFORE the run entry; fire
lines are the first writer that lands AFTER it. Everything `_log_entry_for`
feeds is poisoned from that moment: the quiet failure note (keys on
entry["trigger"]/entry["status"], both absent on a fire line), the structured
stories/tiers of the Today and archive renderers, and `schedule._charged_for`
on any later same-day fire.

FIX CONTRACT (one line + its pin): `_log_entry_for` skips lines carrying
`schedule.SCHEDULE_LINE_KEY`, exactly as `_run_log_entries` already does — one
more key in the same filter idiom. All three tests below must then pass with no
other change.
"""
from __future__ import annotations

from datetime import datetime, timedelta
import json

import pytest

from newslens import analysis, db, generate, paths, schedule, server

from test_server import ui, get, seed_briefing            # noqa: F401

TODAY = datetime.now().strftime("%Y-%m-%d")
YDAY = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")


def _bodyless_row(con, date=TODAY):
    con.execute(
        "INSERT INTO briefings (date, story_slots, corroboration_labels,"
        " token_cost, generated_at) VALUES (?, ?, ?, ?, ?)",
        (date, json.dumps([]), json.dumps([]), json.dumps({}),
         "2026-08-13T04:00:00.000Z"))
    con.commit()


class Sleeper:
    def __init__(self):
        self.slept = []

    def __call__(self, seconds):
        self.slept.append(seconds)


# ---------------------------------------------------------------------------
# F-1 — the fire line must never BE the run entry
# ---------------------------------------------------------------------------

def test_log_entry_for_skips_schedule_fire_lines(tmp_paths):
    """F-1 unit contract. A fire-decision line is a decision about a run, not
    the run; the per-date reader must return the run entry that precedes it."""
    db.migrate()
    generate.log_generation({"date": TODAY, "status": "ok",
                             "stories": [{"headline": "H", "lede": "L"}],
                             "tiers": ["full"], "total_usd": 1.23})
    schedule.log_fire(schedule.FIRED_PUBLISHED, TODAY, attempts=1,
                      charged_usd=1.23)

    entry = server._log_entry_for(TODAY)
    assert entry is not None and entry.get("status") == "ok", (
        "the fire-decision line was returned as the run entry: "
        f"{sorted((entry or {}).keys())}")
    assert schedule._charged_for(TODAY) == pytest.approx(1.23), \
        "a later same-day fire's budget arithmetic reads the fire line's nothing"


def test_the_quiet_note_survives_the_ladders_own_fire_line(ui):
    """F-1 end-to-end, THE REAL EXHAUSTION FLOW. run_scheduled's ladder writes
    FIRED_FAILED *after* the run entry — the build's page test wrote the run
    entry by hand and stopped there, so the note it proved is unreachable in
    the flow it was built for. This test does exactly what the ladder does."""
    con = db.connect()
    seed_briefing(con, YDAY)
    _bodyless_row(con)
    con.close()
    generate.log_generation({"date": TODAY, "status": "failed",
                             "trigger": schedule.TRIGGER_SCHEDULED,
                             "retryable": True, "paused": "fetch"})
    schedule.log_fire(schedule.FIRED_FAILED, TODAY,
                      detail="SystemicFetchFailure: nothing fetched",
                      attempts=3, charged_usd=0.0)

    _, _, raw = get(ui, "/")
    today = raw.decode("utf-8").split('id="view-today"')[1] \
                               .split('id="view-following"')[0]
    assert "scheduled edition didn" in today, (
        "the quiet note vanished the moment the ladder wrote its own record — "
        "the reader who opens at 07:00 gets an absence with no reason")


def test_a_scheduled_success_keeps_the_structured_stories(tmp_paths):
    """F-1 rendering half. A successful SCHEDULED morning must render from the
    same structured record an interactive one does — 'a scheduled run and an
    interactive run of the same date are the same run' (run_generate's own
    contract). With the fire line as entry, _stories_for silently falls back to
    the legacy narrative parse and the tier chips to positional defaults."""
    db.migrate()
    generate.log_generation({
        "date": TODAY, "status": "ok",
        "stories": [{"headline": "STRUCTURED-HEADLINE-SENTINEL",
                     "lede": "structured lede",
                     "why_it_matters": "w", "why_label": "Why it matters"}],
        "tiers": ["full"], "total_usd": 0.0})
    schedule.log_fire(schedule.FIRED_PUBLISHED, TODAY, attempts=1)

    entry = server._log_entry_for(TODAY)
    raw = (entry or {}).get("stories")
    assert isinstance(raw, list) and raw and \
        raw[0]["headline"] == "STRUCTURED-HEADLINE-SENTINEL", (
        "the structured stories are gone from the entry the renderers read — "
        "every scheduled success renders through the legacy parse fallback")


# ---------------------------------------------------------------------------
# F-3 — the session budget must BOUND the session, not only stop the ladder
# ---------------------------------------------------------------------------

def test_an_unattended_session_never_charges_past_one_runs_cap(tmp_paths,
                                                               monkeypatch):
    """F-3 acceptance (routine-derating law, strict form). The projection
    (`charged + last_charged > cap`) stops the LADDER but bounds no ATTEMPT:
    each retry runs under the FULL per-run cap, so with escalating costs the
    session lands past the cap before the projection can see it.

    Premise chain, stated honestly: the only retryable failure is the systemic
    fetch pause, which NL-148 pins at $0 charged — so reaching this shape needs
    that pin already broken. But the guard's own docstring claims the anomalous
    world ("a pause that somehow charged real money stops early"), and NL-80's
    no-output-ceiling class is exactly the anomaly it guards. In that world:
    cap $4.25; attempt 1 charges $2.00 (projection $4.00 <= cap -> retry);
    attempt 2 charges $4.25 (legal under its own per-run cap). Session: $6.25
    charged, 1.47x what an attended run may spend.

    FIX CONTRACT: derate the cap handed to every retry attempt to the session
    remainder (env override of BUDGET_CAP_USD_PER_RUN = cap - charged for
    attempts >= 2), so an attempt structurally cannot spend past the session
    budget; the projection check may stay as the cheap early exit."""
    db.migrate()
    cap = 4.25
    monkeypatch.setattr(schedule.config, "budget_cap_usd_per_run",
                        lambda env=None: cap)
    costs = iter([2.00, 4.25, 0.0])
    monkeypatch.setattr(schedule, "_charged_for",
                        lambda date: next(costs))

    calls = []

    def runner(**kw):
        calls.append(kw)
        raise analysis.SystemicFetchFailure("anomalous charged pause")

    sleeper = Sleeper()
    result = schedule.run_scheduled(date=TODAY, runner=runner, sleeper=sleeper)

    assert result["charged_usd"] <= cap, (
        f"the unattended session charged ${result['charged_usd']:.2f} against "
        f"a ${cap:.2f} cap — an unattended morning outspent an attended run")


# ---------------------------------------------------------------------------
# F-4 — a row is not an edition, in the ARCHIVE either
# ---------------------------------------------------------------------------

def test_a_bodyless_row_is_not_an_edition_in_the_archive_list(ui):
    """F-4 acceptance. The build closed the half-edition hole on Today with the
    renderer's own predicate and the words "A ROW IS NOT AN EDITION" — but the
    archive list still links every briefings row as a readable edition. A dead
    scheduled run leaves a permanent bodyless row for its date, and NL-146
    makes those deaths routine and unwatched: the archive acquires a tile for
    a day that never published, opening onto an empty edition view.

    FIX CONTRACT: the archive list applies the same `_stories_for` readability
    predicate before presenting a date as an edition (skip, or render as an
    honest non-edition line — the skip is the smallest true change)."""
    con = db.connect()
    seed_briefing(con, YDAY)
    _bodyless_row(con)
    con.close()

    _, _, raw = get(ui, "/")
    page = raw.decode("utf-8")
    archive = page.split('id="view-archive"')[1].split("</section>")[0]
    assert YDAY in archive, "premise: the published day is in the archive"
    assert TODAY not in archive, (
        "the archive presents the body-less row as an edition of record — "
        "same lie the Today fix just closed, one view over")
