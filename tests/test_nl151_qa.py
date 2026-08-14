"""NL-151 QA pins — the adversarial pass's acceptance contracts (2026-08-13).

WHY THIS FILE EXISTS BESIDE test_nl151_fetch_skip.py: the build's 21 pins hold
the stage and the renderer; QA's mutation legs found the one seam neither
holds — generate's a_rep -> inputs["fetch_skipped"] plumbing — plus three
edges the hunt list named. Each pin below carries its receipt class.

MUTATION RECEIPTS (QA mirror, PYTHONPATH pinned, newslens.__file__ asserted
inside the copy; restores hash-verified to the pre-mutation shas AND verified
by import):
  M1  both gates neutered (`if False and ...`): the build's 21 pins go
      16 failed / 5 passed — the implementer's receipt re-derived exactly.
  M2  renumber mutant (walk passes a compacted index to analyze_story — the
      NL-148(b) breach a bad drop-arm propagation would commit): BOTH
      test_L2_slot_NUMBERING pins go RED at the latest_valid_brief(date, 1)
      line. The "holds trivially when nothing moves" label undersold them;
      they are mutation-proven against their named class.
  M3  generate.py plumbing severed (inputs["fetch_skipped"] never set): the
      build's 21 pins AND all NL-148 pins stay GREEN (39 passed) — the
      disclosure is dead on every real edition and no committed pin sees it.
      test_e2e_* below is the pin that bites (observed red under M3, both
      arms).
  M4  assembler loop replaced by a comment carrying the tokens: the inverted
      clause-3 pin stays GREEN (source-text assertions are comment-
      satisfiable); the behavioural L2 disclosure pins go red. The register
      is held by behaviour, not by that pin.

BORN-RED IN THIS FILE (the open acceptance contract, fix contract in its
docstring): test_gateB_slot_buys_no_ladder_spend — 2 pins (both arms), red at
the current bytes because Gate B sits BELOW Sonar rung 1 and a funded
never-attempted slot buys verification for a brief it then refuses (measured:
3 Sonar calls, $0.06 charged on a 3-slot all-Gate-B day). Everything else
here is born green with the mutation/derivation receipts above.

Offline by construction: autouse sandbox + loopback guard (conftest); $0.
"""
from __future__ import annotations

import json

import pytest

from newslens import analysis, db, generate, paths

from test_analysis_brief_qa import (
    DATE, ENV_OK, fetch_fixture, s_brief, sonar_none,
)
from test_nl148_fetch_failure import _fetch_all_fail
from test_nl151_fetch_skip import (
    ARMS, _excerpt_brief, _fetch_failing_slots, _run, _seed_slots,
)
from test_generate import (  # noqa: F401
    ENV as GEN_ENV, A_DAY, compliant_script, fake_model, slot as gen_slot,
    seed_briefing, stories_payload, _fake_audio_ok,
)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _log_entries():
    p = paths.DATA_DIR / "generation_log.jsonl"
    if not p.exists():
        return []
    return [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines()
            if l.strip()]


def _real_stage_with(monkeypatch, fetch, chat=None):
    """Wrap the REAL run_analysis so run_generate executes the true stage
    bytes with only the network/model faked (run_generate exposes no fetch
    seam of its own)."""
    real = analysis.run_analysis

    def _wrapped(**kw):
        kw.setdefault("fetch", fetch)
        kw.setdefault("chat", chat or (lambda k, p: (s_brief(), 0.0)))
        kw.setdefault("sonar", sonar_none)
        kw.setdefault("sleep", lambda s: None)
        return real(**kw)

    monkeypatch.setattr(analysis, "run_analysis", _wrapped)


def _pipeline_world(con, monkeypatch, fake_model, n=5):
    from newslens import ingest as ingest_mod, ranking as ranking_mod
    slots = [gen_slot(i, title=f"QA Story {i}") for i in range(1, n + 1)]
    seed_briefing(con, A_DAY, slots)

    def _noop_rank(*a, **k):
        r = type("R", (), {})()
        r.warnings = []
        return r

    monkeypatch.setattr(ingest_mod, "run_ingest",
                        lambda *a, **k: ingest_mod.IngestReport())
    monkeypatch.setattr(ranking_mod, "run_rank", _noop_rank)
    fake_model.narrative = stories_payload(slots)
    fake_model.script = compliant_script(slots)
    _fake_audio_ok(monkeypatch, [])
    return slots


def _dead_urls(*dead, date=A_DAY):
    marks = tuple(f"/{date}/{n}" for n in dead)

    def _fetch(url, timeout, cap=0, user_agent=""):
        if url.endswith("/robots.txt"):
            return _fetch_all_fail(url, timeout, cap, user_agent)
        if any(m in url for m in marks):
            raise OSError("connection reset by peer")
        return fetch_fixture(url, timeout, cap, user_agent)
    return _fetch


# ===========================================================================
# F-1 — the plumbing pin: the disclosure survives the WHOLE pipeline
# ===========================================================================

@pytest.mark.parametrize("arm", ARMS)
def test_e2e_disclosure_reaches_the_persisted_edition(tmp_paths, fake_model,
                                                      monkeypatch, arm):
    """THE SEAM PIN (QA F-1). generate.py's a_rep -> inputs["fetch_skipped"]
    -> assembler -> run record chain has no other executable guard: under
    mutation M3 (those two lines severed) every committed pin stays green
    while the disclosure is dead on every real edition — the exact furniture
    failure NL-148 refused, silent. This pin drives run_generate through the
    REAL analysis stage and reads the two artifacts a reader and NL-146
    actually get: the persisted narrative and the run-record entry.

    MUTATION RECEIPT: red under M3 at the persisted-narrative assert, both
    arms (QA pass 2026-08-13)."""
    monkeypatch.setattr(analysis, "FETCH_SKIP_ARM", arm)
    db.migrate()
    con = db.connect()
    try:
        _pipeline_world(con, monkeypatch, fake_model)
        _real_stage_with(monkeypatch, _dead_urls(1))
        rep = generate.run_generate(date=A_DAY, con=con, env=GEN_ENV,
                                    refresh=True)
        row = con.execute("SELECT * FROM briefings WHERE date = ?",
                          (A_DAY,)).fetchone()
        narrative = row["narrative_text"] or ""
        assert "Fetch failed for prioritized story QA Story 1." in narrative, (
            "the stage skipped slot 1 but the persisted edition carries no "
            "disclosure — generate's fetch_skipped plumbing is dead")
        from newslens import ranking
        assert ranking.CORROBORATION_CAVEAT in narrative
        assert narrative.find(ranking.CORROBORATION_CAVEAT) \
            < narrative.find("Fetch failed for prioritized story")
        runs = [e for e in _log_entries() if "stage" not in e
                and "status" in e and e.get("date") == A_DAY]
        assert runs and [s["slot"] for s in runs[-1]["fetch_skipped"]] == [1]
        assert runs[-1]["fetch_skipped"][0]["disclose"] is True
        assert rep.fetch_skipped == runs[-1]["fetch_skipped"]
        # the healthy-day inverse of clause 4: one dead slot, no pause
        a_entries = [e for e in _log_entries() if e.get("stage") == "analysis"]
        assert a_entries[-1]["fetch_systemic_failure"] is False
        assert a_entries[-1]["depth_slots"] == [2, 3, 4]
    finally:
        con.close()


@pytest.mark.parametrize("arm", ARMS)
def test_e2e_systemic_day_pauses_through_the_real_stage(tmp_paths, fake_model,
                                                        monkeypatch, arm):
    """Clause-4 composition at PIPELINE level through the REAL stage bytes.
    The existing wiring pin (test_clause4_the_pipeline_pauses...) stubs
    run_analysis; the composition pin (test_clause4_a_systemic_day...) stops
    at the stage report. This closes the gap between them: real gates, real
    walk, real verdict, and the run still raises — a systemic day cannot
    degrade into disclosed skips anywhere along the chain."""
    monkeypatch.setattr(analysis, "FETCH_SKIP_ARM", arm)
    db.migrate()
    con = db.connect()
    try:
        _pipeline_world(con, monkeypatch, fake_model)
        _real_stage_with(monkeypatch, _fetch_all_fail)
        with pytest.raises(analysis.SystemicFetchFailure):
            generate.run_generate(date=A_DAY, con=con, env=GEN_ENV,
                                  refresh=True)
        row = con.execute("SELECT narrative_text FROM briefings WHERE date = ?",
                          (A_DAY,)).fetchone()
        assert not row["narrative_text"]
        runs = [e for e in _log_entries() if "stage" not in e
                and "status" in e and e.get("date") == A_DAY]
        assert runs[-1]["status"] == "failed"
        assert runs[-1]["paused"] == "fetch"
        assert runs[-1]["retryable"] is True
        a = [e for e in _log_entries() if e.get("stage") == "analysis"][-1]
        assert a["fetch_systemic_failure"] is True
        assert a["depth_slots"] == []
        # the verdict's inputs are the raw fetch counters, untouched by the
        # gates: every walked slot attempted, none extracted
        assert a["per_story"] and all(s["fetch_attempted"]
                                      for s in a["per_story"])
        assert all(not s["fetch_ok"] for s in a["per_story"])
    finally:
        con.close()


# ===========================================================================
# Gate A below the budget floor — the C-5 free-verdict row survives (hunt 5)
# ===========================================================================

@pytest.mark.parametrize("arm", ARMS)
def test_c5_free_verdict_row_survives_gateA_under_exhaustion(tmp_paths,
                                                             monkeypatch,
                                                             arm):
    """Armed, cap exhausted, slot-3 medium with dead fetches: the budget
    floor must still win (outcome skipped-budget, never skipped-fetch-failed)
    and the C-5 free demoted-quick verdict row must still persist. Hoisting
    Gate A above the floor would silently retire that ruled row — this pin
    is the tripwire."""
    monkeypatch.setattr(analysis, "FETCH_SKIP_ARM", arm)
    db.migrate()
    con = db.connect()
    try:
        _seed_slots(con, 3)
        rep = analysis.run_analysis(
            date=DATE, con=con, env=dict(ENV_OK),
            chat=lambda k, p: (s_brief(), 0.0), sonar=sonar_none,
            fetch=_fetch_all_fail, sleep=lambda s: None,
            already_spent=10_000.0,
            tiers_override=["full", "medium", "medium"])
        assert all(s["outcome"] == "skipped-budget"
                   for s in rep["per_story"]), rep["per_story"]
        assert rep["fetch_skipped"] == []
        s3 = [r for r in con.execute(
            "SELECT status, reject_reason FROM analysis_briefs"
            " WHERE date = ? AND slot = 3", (DATE,))]
        assert s3 and s3[0]["status"] == "rejected"
        assert "demoted-quick" in (s3[0]["reject_reason"] or "")
    finally:
        con.close()


# ===========================================================================
# The real-log specimen (2026-08-03, slot 3: 5 attempted / 0 ok)
# ===========================================================================

@pytest.mark.parametrize("arm", ARMS)
def test_the_one_real_specimen_shape_skips_promotes_and_disclosss(
        db_con_qa, monkeypatch, arm):
    """The only attempted-and-failed prioritized slot in the principal's real
    generation_log (24 stages / 72 slots, re-derived read-only by QA
    2026-08-13: 71 full-text, 1 attempted-and-failed, 0 never-attempted) was
    2026-08-03 slot 3, tier medium, 5 attempted / 0 extracted — shipped that
    day as a degraded 'ok'. This pin replays that exact shape through the
    armed machinery: Gate A takes it, the disclosure names it, slot 4
    inherits the medium tier, numbering never moves."""
    monkeypatch.setattr(analysis, "FETCH_SKIP_ARM", arm)
    con = db_con_qa
    slots = []
    with con:
        for s in range(1, 5):
            ids = []
            for i in range(5 if s == 3 else 2):
                cur = con.execute(
                    "INSERT INTO source_items (source_type, outlet, url,"
                    " title, raw_excerpt) VALUES ('rss', ?, ?, ?, ?)",
                    ("The Hill", f"https://thehill.com/s{s}i{i}",
                     f"Specimen {s} item {i}",
                     "The president travels to the summit midweek."))
                ids.append(cur.lastrowid)
            slots.append({"slot": str(s), "story_title": f"Specimen {s}",
                          "summary": f"Summary {s}.", "item_ids": ids,
                          "outlets": ["The Hill"], "matched_tags": [],
                          "matched_memory": [], "override": False,
                          "corroboration_label": "Reported by 1 named outlet"})
        con.execute("INSERT INTO briefings (date, story_slots) VALUES (?, ?)",
                    (DATE, json.dumps(slots)))
    rep = _run(con, _fetch_failing_slots(3))
    by_slot = {s["slot"]: s for s in rep["per_story"]}
    assert by_slot[3]["fetch_attempted"] == 5
    assert by_slot[3]["fetch_ok"] == 0
    assert by_slot[3]["outcome"] == analysis.FETCH_SKIP_OUTCOME
    assert rep["depth_slots"] == [1, 2, 4]
    assert rep["depth_tiers"][3] == "medium"
    sk = rep["fetch_skipped"]
    assert [s["slot"] for s in sk] == [3] and sk[0]["disclose"] is True
    text = generate.assemble_narrative(
        DATE, "A", [], {"slots": [], "window_meta": {}, "fetch_skipped": sk})
    assert "Fetch failed for prioritized story Specimen 3." in text


# ===========================================================================
# Thin days — fewer than three slots survive the walk (hunt 3c)
# ===========================================================================

@pytest.mark.parametrize("arm", ARMS)
@pytest.mark.parametrize("n,dead,want_depth,want_verdict", [
    (2, (1, 2), [], True),   # 2-slot day, both dead: no depth, pause verdict
    (2, (1,), [2], False),   # 2-slot day, one dead: one depth slot, no pause
    (1, (1,), [], True),     # 1-slot day, dead: no depth, pause verdict
    (0, (), [], False),      # empty day: no-depth-stories, verdict False
])
def test_thin_day_walk_edges(db_con_qa, monkeypatch, arm, n, dead,
                             want_depth, want_verdict):
    """The walk under days thinner than the depth tier: it must neither crash
    nor invent depth slots, and the clause-4 verdict must keep its meaning
    (attempted-and-empty days pause; a never-attempting empty day cannot)."""
    monkeypatch.setattr(analysis, "FETCH_SKIP_ARM", arm)
    _seed_slots(db_con_qa, n)
    rep = _run(db_con_qa, _fetch_failing_slots(*dead))
    assert rep["depth_slots"] == want_depth, rep
    assert rep["fetch_systemic_failure"] is want_verdict, rep
    if n == 0:
        assert rep["status"] == "no-depth-stories"
        assert rep["depth_tiers"] == []


# ===========================================================================
# Disclosure-order hardening
# ===========================================================================

@pytest.mark.parametrize("arm", ARMS)
def test_skip_footer_still_carries_the_standing_caveat(db_con_qa, monkeypatch,
                                                       arm):
    """Hardening for a str.find(-1) vacuity: the build's order pin
    (caveat-index < skip-index) passes vacuously if the caveat ever vanished
    from a skip-bearing footer, because find() returns -1. This asserts the
    caveat's PRESENCE on exactly that footer."""
    from newslens import ranking
    monkeypatch.setattr(analysis, "FETCH_SKIP_ARM", arm)
    _seed_slots(db_con_qa, 5)
    rep = _run(db_con_qa, _fetch_failing_slots(1))
    text = generate.assemble_narrative(
        DATE, "A", [], {"slots": [], "window_meta": {},
                        "fetch_skipped": rep["fetch_skipped"]})
    assert ranking.CORROBORATION_CAVEAT in text
    assert "Fetch failed for prioritized story Story 1." in text


# ===========================================================================
# F-3 — BORN RED: Gate B buys Sonar for a slot it then refuses
# ===========================================================================

@pytest.mark.parametrize("arm", ARMS)
def test_gateB_slot_buys_no_ladder_spend(db_con_qa, monkeypatch, arm):
    """BORN RED (QA F-3, acceptance contract — measured 3 Sonar calls /
    $0.06 charged on a 3-slot all-Gate-B day at the current bytes).

    The NL-151 header comment claims "THE GATES ALSO SAVE MONEY, which is why
    they sit above the ladder" — true of Gate A only. Gate B sits BELOW Sonar
    rung 1, so a funded never-attempted slot pays for verification of a brief
    the gate then refuses to mint. Same waste class as the $0.74
    billed-then-rejected briefs QA measured on a clause-4 pause, on a path
    that has never occurred in production (0 of 72 real slots).

    FIX CONTRACT (either resolves the finding; the gate rules which):
      (a) hoist Gate B above rung 1, keyed on `items and not
          sa.fetch_attempted` — cluster-empty slots (no items) keep their
          skipped-thin outcome untouched, and the Sonar-rescue path for
          excerpt-only briefs stays exactly as live as today when OFF; or
      (b) rule the spend acceptable on a never-observed path, INVERT this pin
          to pin the spend, and amend the header comment that claims both
          gates sit above the ladder."""
    monkeypatch.setattr(analysis, "FETCH_SKIP_ARM", arm)
    monkeypatch.setattr(analysis, "tier_allows_fetch", lambda tier: False)
    _seed_slots(db_con_qa, 3)
    calls = []

    def sonar_counting(key, title, claims):
        calls.append(title)
        return [], 0.02, "ok — 0 results"

    rep = analysis.run_analysis(
        date=DATE, con=db_con_qa, env=dict(ENV_OK),
        chat=lambda k, p: (s_brief(), 0.0), sonar=sonar_counting,
        fetch=_fetch_all_fail, sleep=lambda s: None,
        tiers_override=["full", "medium", "medium"])
    assert rep["fetch_skipped"] and all(
        s["outcome"] == analysis.NO_FETCHABLE_OUTCOME
        for s in rep["fetch_skipped"])
    assert calls == [], (
        f"Sonar was paid for {len(calls)} slot(s) the depth tier then "
        f"refused: {calls} — Gate B sits below the ladder rung it claims "
        "to sit above")
    assert rep["total_usd"] == 0.0


# ---------------------------------------------------------------------------
# fixtures (bottom, house style)
# ---------------------------------------------------------------------------

@pytest.fixture
def db_con_qa(tmp_paths):
    db.migrate()
    con = db.connect()
    try:
        yield con
    finally:
        con.close()
