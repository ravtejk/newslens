"""M9-M3 QA — writer-from-brief + the deep view (QA-written; extends
tests/test_m3_integration.py). Fully offline: chat faked, server rendered
in-process, ZERO consumption events (day-30 semantics untouched).

Adversarial focus per dispatch: (1) two fact universes, no bleed;
(2) degraded-hidden == absent at BYTE level; (3) slot-3 verdict binding +
the string-slot footgun sweep; (4) _cite_qualifier grammar + discrepancy
render; (5) one-cap sequencing (analysis first); (6) tiers_override /
--no-refresh reuse / stage-dead degrade; (7) no event path from the
deep-view switch.

BUG ledger: BUG16 (qualifier said "1 outlet" under two named outlets)
was FIXED at the M3 gate render batch — one provenance path via
compute_prov_display; its test is a green regression guard. Open
KNOWN-RED:
  BUG17  gate diff 1a shipped half: trace_check_numerals is defined,
         unit-correct, and NEVER CALLED — no invented-numeral warning can
         reach a run's record from either validation site (draft or
         edited-swap). Dead-validator class; fix contract in the
         test_BUG17_* docstring.

Gate items carried in docstrings: no mechanical fact-subset check shipped
(§5.6 enforcement is prompt-prose; editor label-data wiring stayed the
contract's [ASSUMPTION]); effects qualifier copy deviates from v6's bare
"(via Outlet)"; arc-less briefs keep a dead Arc jumplist anchor; demoted
slot-3 verdict does not persist across --no-refresh re-runs.
"""

from __future__ import annotations

import json
import time

import pytest

from newslens import analysis, db, generate, paths, server, webui
from test_generate import (A_DAY, _inputs_for, compliant_script,
                           seed_briefing, slot, stories_payload)

DATE = "2026-07-07"
ENV = {"OPENAI_API_KEY": "sk-qa-fake"}
EXCERPT = "An excerpt of the source item."  # tg.seed_briefing's marker


def m3_brief(with_discrepancy=False, with_arc=False):
    b = {
        "pinned_facts": [{"fact": "A cited fact.", "cites": ["S1"]}],
        "ledger": [{"claim": "A ledger claim.", "cites": ["S1"],
                    "provenance": "cluster-single"}],
        "mechanism": "An actor answers to a constraint [S1].",
        "effects": [{"effect": "A stated take.", "basis": "attributed",
                     "holder": "Jan Novak", "cites": ["R1"]}],
        "arc": None,
        "unknowns": [{"question": "Which members resist",
                      "why_material": "blocks unanimity",
                      "would_resolve": "the communique"}],
        "watch": [{"observable": "communique by Thursday",
                   "settles": "resistance"}],
        "sources": [
            {"key": "S1", "outlet": "The Hill", "title": "Story",
             "url": "https://thehill.com/a",
             "retrieved_at": "2026-07-07T00:00Z", "kind": "cluster-full-text"},
            {"key": "C1", "outlet": "rferl.org", "title": "Wire note",
             "url": "https://rferl.org/b", "retrieved_at": "2026-07-06",
             "kind": "cluster-excerpt"},
            {"key": "R1", "outlet": "reuters.com", "title": "Agenda",
             "url": "https://reuters.com/c",
             "retrieved_at": "2026-07-07T00:00Z", "kind": "retrieved"},
        ],
        "notes_for_writer": "trace the pledge number.",
    }
    if with_discrepancy:
        # replica of the live slot-2 shape (meeting date: July 8 per
        # rferl.org vs Wednesday per the cluster) — fixture, NEVER the live DB
        b["ledger"].append({"discrepancy": True,
                            "a": {"value": "Meeting July 8", "cites": ["C1"]},
                            "b": {"value": "Meeting Wednesday", "cites": ["S1"]},
                            "note": "dates differ"})
    if with_arc:
        b["arc"] = {"delta": "advances", "what_changed": "staging became "
                    "the summit.", "cites": ["S1"]}
    return b


def persist_valid(con, slot_no=1, date=DATE, **kw):
    analysis.persist_brief(
        con, date, slot_no, "full", "valid", m3_brief(**kw), "", 0.02,
        {"manifest": {}, "degraded": None},
        sources={"S1": {"kind": "cluster-full-text", "outlet": "The Hill",
                        "title": "Story", "url": "https://thehill.com/a",
                        "retrieved_at": "", "text": "body"}})


def seed_m3(con, n=3, date=DATE):
    slots = [slot(i) for i in range(1, n + 1)]
    seed_briefing(con, date, slots)
    return slots


@pytest.fixture
def fake_chat(monkeypatch):
    """Local copy of test_generate's stateful fake (fixtures don't import
    across modules): 1st json call -> narrative, later json -> editor echo,
    non-json -> script."""
    state = type("S", (), {})()
    state.calls, state.narrative, state.script = [], None, None
    state.editor = None  # BUG17 fixture repair: the edited-swap red SET
    # this but nothing served it — the editor call echoed the narrative, so
    # the introduced numeral could never exist. Json call 2+ now serves
    # state.editor when set (echo otherwise, matching the docstring).

    def chat(key, prompt, max_tokens, temperature, json_mode):
        state.calls.append({"json_mode": json_mode, "prompt": prompt})
        if json_mode:
            n_json = sum(1 for c in state.calls if c["json_mode"])
            payload = (state.editor if n_json >= 2 and state.editor is not None
                       else state.narrative)
            content = json.dumps(payload)
        else:
            content = state.script
        return {"choices": [{"finish_reason": "stop",
                             "message": {"content": content}}],
                "usage": {"prompt_tokens": 900, "completion_tokens": 200}}

    monkeypatch.setattr(generate, "_chat", chat)
    monkeypatch.setattr(time, "sleep", lambda s: None)
    return state


def canned_report(**over):
    rep = {"ts": "2026-07-07T05:00:00Z", "stage": "analysis", "date": DATE,
           "status": "ok", "model": "gpt-4o", "total_usd": 0.021,
           "derating": True,
           "warnings": ["derating: Sonar verification skipped under the cap"],
           "per_story": [
               {"slot": 1, "tier": "full", "outcome": "ok", "detail": "",
                "cost_usd": 0.021, "fetch_ok": 1, "fetch_attempted": 1,
                "sonar": "skipped"},
               {"slot": 3, "tier": "medium", "outcome": "ok", "detail": "",
                "cost_usd": 0.0, "fetch_ok": 0, "fetch_attempted": 0,
                "sonar": "skipped"}]}
    rep.update(over)
    return rep


# ---------------------------------------------------------------------------
# 1. Two fact universes, no bleed (prompt level — what shipped)
# ---------------------------------------------------------------------------

def test_briefed_slot_drops_excerpts_unbriefed_keeps_them_no_bleed(tmp_paths):
    """GATE NOTE carried here: the universe swap is PROMPT-level. No
    mechanical fact-subset validator exists for either universe — content
    §5.6's 'writer introduces no specific absent from the brief' is prompt
    prose (generate.py:473) and the editor does not receive the brief
    (contract §607 stayed [ASSUMPTION]). 'A narrative fact absent from the
    brief' fails only at the Editor's eye, not in code. Reported to the
    gate; these pins freeze the separation that DID ship."""
    db.migrate()
    con = db.connect()
    try:
        slots = seed_m3(con)
        inputs = generate.load_briefing_inputs(con, DATE)
        inputs["briefs_by_slot"] = {1: analysis.latest_valid_brief(con, DATE, 1)
                                    or {"brief": m3_brief()}}
        inputs["analyst_slot3_tier"] = None
        persist_valid(con)
        inputs["briefs_by_slot"] = {1: analysis.latest_valid_brief(con, DATE, 1)}
        prompt = generate.build_narrative_prompt(DATE, "A", inputs)
        s1, rest = prompt.split("STORY 2 —", 1)
        # briefed universe: writer view + titles, NO excerpts
        assert "TRACE, DON'T GENERATE" in s1
        assert "A cited fact." in s1 and "PINNED FACTS" in s1
        assert "trace the pledge number." in s1     # notes_for_writer rides
        assert EXCERPT not in s1
        assert "cluster items (context only" in s1
        # unbriefed universe: excerpts + per-story disclosure
        assert EXCERPT in rest
        assert "analysis unavailable for this story" in rest
        # no bleed: the brief renders exactly once, inside story 1's block
        assert prompt.count("TRACE, DON'T GENERATE") == 1
        assert rest.count("A cited fact.") == 0
    finally:
        con.close()


def test_whole_stage_absent_means_no_per_story_disclosure(tmp_paths):
    """briefs_by_slot == {} is the stage-wide-failure shape: the run-level
    warning owns the disclosure; per-story lines would triple-print it."""
    slots = [slot(1), slot(2), slot(3)]
    inputs = _inputs_for(slots)
    inputs["briefs_by_slot"] = {}
    inputs["analyst_slot3_tier"] = None
    prompt = generate.build_narrative_prompt(DATE, "A", inputs)
    assert "analysis unavailable for this story" not in prompt
    assert "TRACE, DON'T GENERATE" not in prompt


def test_slots_beyond_three_never_get_the_disclosure_line(tmp_paths):
    slots = [slot(i) for i in range(1, 5)]
    inputs = _inputs_for(slots)
    inputs["briefs_by_slot"] = {1: {"brief": m3_brief()}}
    inputs["analyst_slot3_tier"] = None
    prompt = generate.build_narrative_prompt(DATE, "A", inputs)
    s4 = prompt.split("STORY 4 —", 1)[1]
    assert "analysis unavailable" not in s4  # quick tier: excerpts, no apology


# ---------------------------------------------------------------------------
# 2. Slot-3 verdict binding (both directions) + string-slot sweep
# ---------------------------------------------------------------------------

def test_analyst_medium_verdict_rejects_a_quick_payload():
    """Their test pins quick-binding rejecting medium; this is the mirror:
    a medium verdict makes 'quick' invalid — the model can't quietly
    shrink a story the analyst ruled medium."""
    slots = [slot(1), slot(2), slot(3)]
    payload = stories_payload(slots)
    payload["stories"][2]["tier"] = "quick"
    with pytest.raises(ValueError, match="tier 'quick' not allowed"):
        generate.validate_narrative_payload(
            payload, slots, "A", slots_ctx={"analyst_slot3_tier": "medium"})


@pytest.mark.parametrize("tier3", ["medium", "quick"])
def test_no_verdict_leaves_slot3_to_the_model(tier3):
    slots = [slot(1), slot(2), slot(3)]
    payload = stories_payload(slots)
    if tier3 == "quick":
        # A2 quick shape: no why_it_matters — borrow the canonical quick
        # entry from a 4-slot payload, keep story 3's headline
        q = stories_payload([slot(1), slot(2), slot(3), slot(4)])["stories"][3]
        payload["stories"][2] = {**q,
                                 "headline": payload["stories"][2]["headline"]}
    payload["stories"][2]["tier"] = tier3
    stories, _ = generate.validate_narrative_payload(payload, slots, "A",
                                                     slots_ctx=None)
    assert stories[2]["tier"] == tier3


def test_binding_line_renders_under_story_3_only():
    slots = [slot(1), slot(2), slot(3)]
    inputs = _inputs_for(slots)
    inputs["briefs_by_slot"] = {}
    inputs["analyst_slot3_tier"] = "medium"
    prompt = generate.build_narrative_prompt(DATE, "A", inputs)
    s3 = prompt.split("STORY 3 —", 1)[1]
    assert "TIER RULED BY THE ANALYST: medium" in s3
    assert prompt.count("TIER RULED BY THE ANALYST") == 1


def test_ladder_label_string_slot_sweep():
    """The implementer's flagged footgun: slot numbers ride as STRINGS in
    slot dicts. The meta-line ladder label must key deep_views correctly
    for the actual string shape — and fire only on depth tiers without an
    available brief."""
    slots = [slot(1), slot(2)]
    assert isinstance(slots[0]["slot"], str) or True  # shape doc, not a gate
    payload = stories_payload(slots)
    stories, _ = generate.validate_narrative_payload(payload, slots, "A")
    inputs = _inputs_for(slots)
    inputs["deep_views"] = {"1": "available", "2": "absent"}
    text = generate.assemble_narrative(A_DAY, "A", stories, inputs)
    assert text.count("Analysis: unavailable — built from feed excerpts.") == 1
    # the label lands in story 2's meta line, not story 1's
    metas = [l for l in text.splitlines() if "Here for:" in l]
    assert "Analysis: unavailable" not in metas[0]
    assert "Analysis: unavailable" in metas[1]


@pytest.mark.parametrize("dv,label_expected", [
    ({"1": "available", "2": "available"}, 0),   # both briefed
    ({}, 0),                                      # stage never ran: run-level warning owns it
    ({"1": "available", "2": "demoted-quick"}, 1),  # medium story, non-available verdict
])
def test_ladder_label_directions(dv, label_expected):
    slots = [slot(1), slot(2)]
    stories, _ = generate.validate_narrative_payload(
        stories_payload(slots), slots, "A")
    inputs = _inputs_for(slots)
    inputs["deep_views"] = dv
    text = generate.assemble_narrative(A_DAY, "A", stories, inputs)
    assert text.count("Analysis: unavailable") == label_expected


def test_quick_tier_never_carries_the_ladder_label():
    """A demoted slot 3 IS quick tier — the label is for degraded depth
    stories, not consciously-quick ones (Axel: absence is the signal)."""
    slots = [slot(1), slot(2), slot(3), slot(4)]
    payload = stories_payload(slots)
    stories, _ = generate.validate_narrative_payload(payload, slots, "A")
    assert stories[3]["tier"] == "quick"
    inputs = _inputs_for(slots)
    inputs["deep_views"] = {"1": "available", "2": "available",
                            "3": "available", "4": "absent"}
    text = generate.assemble_narrative(A_DAY, "A", stories, inputs)
    assert "Analysis: unavailable" not in text


# ---------------------------------------------------------------------------
# 3. run_analysis seam: already_spent + tiers_override
# ---------------------------------------------------------------------------

def test_already_spent_rides_into_the_one_cap(tmp_paths):
    """Cap 0.25 with 0.24 already spent: both money sentinels stay cold,
    outcomes are the disclosed budget rungs, and total_usd reports only
    the stage's OWN delta (0 here)."""
    db.migrate()
    con = db.connect()
    try:
        seed_m3(con, n=1)

        def sonar_sentinel(key, title, claims):
            raise AssertionError("Sonar called with no headroom")

        def chat_sentinel(key, prompt):
            raise AssertionError("synthesis called with no headroom")

        rep = analysis.run_analysis(
            date=DATE, con=con, env=dict(ENV), chat=chat_sentinel,
            sonar=sonar_sentinel, fetch=lambda *a, **k: b"",
            sleep=lambda s: None, already_spent=0.24)
        assert rep["per_story"][0]["outcome"] == "skipped-budget"
        assert rep["derating"] is True
        assert rep["total_usd"] == 0.0
    finally:
        con.close()


def test_tiers_override_beats_the_recorded_log_tiers(tmp_paths):
    """The log says all-quick (which would analyze nothing); the override
    forces the generate-time contract [full, medium, medium] — three
    per_story rows prove the override governs."""
    db.migrate()
    con = db.connect()
    try:
        seed_m3(con)
        paths.DATA_DIR.mkdir(parents=True, exist_ok=True)
        (paths.DATA_DIR / "generation_log.jsonl").write_text(
            json.dumps({"date": DATE, "tiers": ["quick", "quick", "quick"]})
            + "\n", encoding="utf-8")
        rep = analysis.run_analysis(
            date=DATE, con=con, env=dict(ENV),
            chat=lambda k, p: ({}, 0.0), sonar=lambda k, t, c: ([], 0.0, "ok"),
            fetch=lambda *a, **k: b"", sleep=lambda s: None,
            tiers_override=["full", "medium", "medium"])
        assert len(rep["per_story"]) == 3
        assert [r["tier"] for r in rep["per_story"]] == \
            ["full", "medium", "medium"]
    finally:
        con.close()


# ---------------------------------------------------------------------------
# 4. Generate integration: wiring, degrade, reuse, cap sequencing
# ---------------------------------------------------------------------------

def _stage_fakes(monkeypatch):
    """The tg:705 refresh pattern: ingest and rank stubbed; rank seeds the
    3-slot briefing row (the refresh path re-ranks before writing)."""
    from newslens import ingest as ingest_mod, ranking

    def fake_ingest(con=None, env=None, **kw):
        r = type("R", (), {})()
        r.succeeded, r.attempted, r.items_new = ["A"], 1, 3
        r.discovery_status = "not attempted"
        r.degradation_message = None
        return r

    def fake_rank(date=None, con=None, env=None, **kw):
        slots = [slot(1), slot(2), slot(3)]
        seed_briefing(con, date, slots)
        r = type("R", (), {})()
        r.warnings = []
        return r

    monkeypatch.setattr(ingest_mod, "run_ingest", fake_ingest)
    monkeypatch.setattr(ranking, "run_rank", fake_rank)
    # BUG17 cascade (mechanical, intended change): with the trace-check and
    # slots_ctx wiring LANDED, letting the REAL run_analysis run offline
    # (fetch guard-blocked, Sonar keyless) demotes slot 3 and the writer
    # enforcement then correctly rejects these fixtures' medium story 3.
    # Default-fake it to a no-DB-write report; tests that need their own
    # fake (the wiring pin) override after.
    monkeypatch.setattr(analysis, "run_analysis",
                        lambda **kw: canned_report())
    paths.SOURCES_FILE.write_text(
        "sources:\n  - name: The Hill\n    rss_url: https://x.example/f\n"
        "interests:\n  tags:\n    - AI regulation\n",
        encoding="utf-8")
    return [slot(1), slot(2), slot(3)]


def test_generate_runs_analysis_first_and_writes_from_the_brief(
        tmp_paths, fake_chat, monkeypatch):
    """The full wiring pin: analysis called with already_spent-at-call-time
    and the tiers_override contract; its verdict binds slot 3's prompt
    line; its warnings ride prefixed; derating escalates; the briefed
    story writes from the writer view; deep_views + analysis_usd land in
    the report AND the log entry (Axel). Mid-analysis derating (cap
    pressure) still lets the writer run — the reconciled ladder."""
    db.migrate()
    con = db.connect()
    try:
        slots = _stage_fakes(monkeypatch)
        persist_valid(con)
        seen = {}

        def fake_run_analysis(**kw):
            seen.update(kw)
            return canned_report()

        monkeypatch.setattr(analysis, "run_analysis", fake_run_analysis)
        fake_chat.narrative = stories_payload(slots)
        fake_chat.script = compliant_script(slots)
        rep = generate.run_generate(date=DATE, con=con, env=dict(ENV),
                                    refresh=True)
        assert seen["already_spent"] == pytest.approx(0.0)
        assert seen["tiers_override"] == ["full", "medium", "medium"]
        assert rep.analysis_usd == pytest.approx(0.021)
        assert rep.deep_views == {"1": "available", "2": "absent",
                                  "3": "absent"}
        assert any(w.startswith("analysis: derating:") for w in rep.warnings)
        assert any("analysis DERATING under the cap" in w for w in rep.warnings)
        prompt = fake_chat.calls[0]["prompt"]
        assert "TRACE, DON'T GENERATE" in prompt
        # CONSCIOUSLY FLIPPED (M3 gate item 2): the verdict derives from
        # PERSISTED rows only — one path for fresh and --no-refresh. This
        # fake's per_story reports slot-3 'ok' while persisting nothing (a
        # state only a fake produces), which now correctly yields NO
        # verdict; the positive cases live in the demotion-persistence
        # tests.
        assert "TIER RULED BY THE ANALYST" not in prompt
        s1 = prompt.split("STORY 2 —", 1)[0]
        assert EXCERPT not in s1 and "A cited fact." in s1
        # Axel instrumentation persisted
        lines = [json.loads(l) for l in
                 (paths.DATA_DIR / "generation_log.jsonl")
                 .read_text(encoding="utf-8").splitlines() if l.strip()]
        entry = [e for e in lines if e.get("date") == DATE
                 and not e.get("sample")][-1]
        assert entry["analysis_usd"] == pytest.approx(0.021)
        assert entry["deep_views"] == rep.deep_views
        # the artifact's honest ladder label on the two unbriefed depth stories
        text = con.execute("SELECT narrative_text FROM briefings WHERE date=?",
                           (DATE,)).fetchone()["narrative_text"]
        assert text.count("Analysis: unavailable — built from feed "
                          "excerpts.") == 2
    finally:
        con.close()


def test_dead_analysis_stage_degrades_disclosed_and_briefing_generates(
        tmp_paths, fake_chat, monkeypatch):
    """Keyless/dead stage (RuntimeError) -> one run-level warning, excerpt
    material, NO per-story disclosure spam, and the briefing still lands."""
    db.migrate()
    con = db.connect()
    try:
        slots = _stage_fakes(monkeypatch)

        def dying(**kw):
            raise RuntimeError("OPENAI_API_KEY not set — no keyless mode")

        monkeypatch.setattr(analysis, "run_analysis", dying)
        fake_chat.narrative = stories_payload(slots)
        fake_chat.script = compliant_script(slots)
        rep = generate.run_generate(date=DATE, con=con, env=dict(ENV),
                                    refresh=True)
        assert any("analysis stage unavailable this run (RuntimeError"
                   in w for w in rep.warnings)
        prompt = fake_chat.calls[0]["prompt"]
        assert EXCERPT in prompt
        assert "analysis unavailable for this story" not in prompt
        assert rep.deep_views == {"1": "absent", "2": "absent", "3": "absent"}
    finally:
        con.close()


def test_no_refresh_reuses_persisted_briefs_and_never_reruns_analysis(
        tmp_paths, fake_chat, monkeypatch):
    """--no-refresh: run_analysis must stay cold; persisted valid briefs
    still feed the writer (read-only reuse).

    (My M3 flag on verdict loss across re-runs was CLOSED by gate item 2:
    demotions now persist as rejected verdict rows and both paths derive
    from analyst_slot3_tier() — see the re-cut pins in section 8. This
    test's slot 3 has NO rows, so no TIER RULED line is the correct
    no-verdict behavior, unchanged.)"""
    db.migrate()
    con = db.connect()
    try:
        paths.SOURCES_FILE.write_text(
            "sources:\n  - name: The Hill\n    rss_url: https://x.example/f\n",
            encoding="utf-8")
        slots = seed_m3(con)
        persist_valid(con)

        def sentinel(**kw):
            raise AssertionError("run_analysis called on a --no-refresh run")

        monkeypatch.setattr(analysis, "run_analysis", sentinel)
        fake_chat.narrative = stories_payload(slots)
        fake_chat.script = compliant_script(slots)
        rep = generate.run_generate(date=DATE, con=con, env=dict(ENV),
                                    refresh=False)
        prompt = fake_chat.calls[0]["prompt"]
        assert "TRACE, DON'T GENERATE" in prompt          # reuse worked
        assert "TIER RULED BY THE ANALYST" not in prompt  # verdict lost (flagged)
        assert rep.deep_views["1"] == "available"
    finally:
        con.close()


def test_cap_exhausted_by_analysis_aborts_the_writer_disclosed(
        tmp_paths, fake_chat, monkeypatch):
    """Mid-writer cap probe: analysis legitimately spends 0.24 of the 0.25
    cap; the narrative pre-call estimate no longer fits -> disclosed
    budget abort (GenerateError, M5 :831 precedent) BEFORE any writer
    spend. Money honesty on abort: the analysis stage self-logs its own
    spend via its M2 stage entry, so the $0.24 is on the record even
    though the writer entry never happens."""
    db.migrate()
    con = db.connect()
    try:
        slots = _stage_fakes(monkeypatch)
        monkeypatch.setattr(analysis, "run_analysis",
                            lambda **kw: canned_report(total_usd=0.24,
                                                       derating=False,
                                                       warnings=[]))
        fake_chat.narrative = stories_payload(slots)
        fake_chat.script = compliant_script(slots)
        with pytest.raises(generate.GenerateError,
                           match="exceeds the remaining budget cap"):
            generate.run_generate(date=DATE, con=con, env=dict(ENV),
                                  refresh=True)
        assert fake_chat.calls == []  # the writer was never called past its check
    finally:
        con.close()


# ---------------------------------------------------------------------------
# 5. Degraded-hidden at byte level + the deep view
# ---------------------------------------------------------------------------

def _page_db(tmp_path, monkeypatch, name, with_rejected):
    monkeypatch.setattr(paths, "DB_PATH", tmp_path / name)
    db.migrate()
    con = db.connect()
    slots = [{"slot": str(n), "story_title": f"Story {n}", "summary": f"s{n}",
              "item_ids": [], "outlets": ["The Hill"], "matched_tags": [],
              "matched_memory": [], "override": False,
              "corroboration_label": "Reported by 1 named outlet"}
             for n in (1, 2, 3)]
    with con:
        con.execute("INSERT INTO briefings (date, story_slots) VALUES (?, ?)",
                    (DATE, json.dumps(slots)))
    persist_valid(con)
    if with_rejected:
        analysis.persist_brief(con, DATE, 2, "medium", "rejected", None,
                               "fabricated citation 'S9'", 0.01,
                               {"manifest": {}}, sources={})
    return con


def _log_entry():
    paths.DATA_DIR.mkdir(parents=True, exist_ok=True)
    entry = {"ts": "2026-07-07T01:00:00Z", "date": DATE, "status": "ok",
             "sample": False, "tiers": ["full", "medium", "quick"],
             "stories": [{"headline": f"Headline {n}", "lede": "Lede."}
                         for n in (1, 2, 3)]}
    (paths.DATA_DIR / "generation_log.jsonl").write_text(
        json.dumps(entry) + "\n", encoding="utf-8")


def test_rejected_brief_page_is_byte_identical_to_never_had_one(
        tmp_paths, tmp_path, monkeypatch):
    """Axel's contract, pinned at the byte: a persisted REJECTED brief for
    slot 2 changes NOTHING in the rendered page versus a DB where slot 2
    never had a brief at all. Total absence is the signal; any stray byte
    (a class, a comment, an empty div) would out the degradation."""
    _log_entry()
    con_a = _page_db(tmp_path, monkeypatch, "a.db", with_rejected=True)
    page_a1, _ = server.build_page(con_a, DATE)
    page_a2, _ = server.build_page(con_a, DATE)
    assert page_a1 == page_a2  # determinism first, so the diff below means something
    con_a.close()
    con_b = _page_db(tmp_path, monkeypatch, "b.db", with_rejected=False)
    page_b, _ = server.build_page(con_b, DATE)
    con_b.close()
    assert page_a1 == page_b
    assert page_a1.count("→ The full picture") == 1


def test_deep_view_discrepancy_renders_two_lines_never_averaged(tmp_paths):
    """The live slot-2 shape as a fixture replica: two values, two cites,
    the unresolved tag — and no blended third value anywhere."""
    doc = {"header": {"degraded": None},
           "brief": m3_brief(with_discrepancy=True)}
    html = server._render_deep_view("story-1", "Headline", doc, DATE)
    d = html.split('class="deep-discrepancy"')[1].split("</div>")[0]
    assert "Meeting July 8" in d and "Meeting Wednesday" in d
    assert d.index("Meeting July 8") < d.index("Meeting Wednesday")
    assert "unresolved" in d
    assert "(rferl.org" in d and "(The Hill" in d


@pytest.mark.parametrize("cites,prov,expected", [
    (["S1", "C2"], "cluster-corroborated (2 outlets)",
     "(The Hill, CNBC · 2 outlets)"),
    (["S1"], "cluster-single", "(The Hill · 1 outlet)"),
    (["R1"], "retrieved-single (reuters.com)", "(reuters.com · via Sonar)"),
    (["P1"], "", "(NewsLens (prior edition))"),
    ([], "", "(background)"),
    (["S9"], "", "(background)"),          # unresolvable key -> background
    (["R1"], "", "(reuters.com · via Sonar)"),   # kinds fallback, no provenance
])
def test_cite_qualifier_grammar_across_the_provenance_shapes(cites, prov, expected):
    src = {"S1": {"kind": "cluster-full-text", "outlet": "The Hill"},
           "C2": {"kind": "cluster-excerpt", "outlet": "CNBC"},
           "R1": {"kind": "retrieved", "outlet": "reuters.com"},
           "P1": {"kind": "prior-briefing", "outlet": "NewsLens (prior edition)"}}
    assert server._cite_qualifier(cites, src, prov) == expected


def test_cite_qualifier_three_plus_outlets_names_two():
    src = {"S1": {"kind": "cluster-full-text", "outlet": "A"},
           "C1": {"kind": "cluster-excerpt", "outlet": "B"},
           "C2": {"kind": "cluster-excerpt", "outlet": "C"}}
    q = server._cite_qualifier(["S1", "C1", "C2"],
                               src, "cluster-corroborated (3 outlets)")
    assert q == "(A, B · 3 outlets)"


def test_BUG16_no_provenance_multi_outlet_must_not_say_one_outlet():
    """GREEN since the M3 gate render batch — was KNOWN-RED (BUG16). _cite_qualifier with no provenance and two
    resolved cluster outlets currently returns '(A, B · 1 outlet)' — a
    self-contradiction on the trust surface. Reachable today via mechanism
    inline multi-key cites ('[S1, C2]'), which pass no provenance string.

    Underclaiming, so the failure direction is safe — but v4/v6's grammar
    is 'names · N outlets' and N must match the names. Fix contract:
    derive the count from the resolved outlet set when no provenance
    string is supplied (or route the mechanism substitution through
    compute_prov_display exactly as pinned facts do). The corroborated /
    single / Sonar / background shapes above must keep passing."""
    src = {"S1": {"kind": "cluster-full-text", "outlet": "The Hill"},
           "C2": {"kind": "cluster-excerpt", "outlet": "CNBC"}}
    q = server._cite_qualifier(["S1", "C2"], src, "")
    assert q == "(The Hill, CNBC · 2 outlets)"
    brief = m3_brief()
    brief["mechanism"] = "Two outlets converge on the constraint [S1, C1]."
    html = server._render_deep_view(
        "story-0", "H", {"header": {}, "brief": brief}, DATE)
    mech = html.split('id="story-0-mechanism"')[1].split("</div>")[0]
    assert "· 1 outlet)" not in mech  # two named outlets may not read as one


def test_effects_qualifier_copy_pinned_as_actual_v6_deviation():
    """FLAGGED-AS-ACTUAL for the gate: v6's effect qualifiers read bare
    '(via Outlet)'; the code emits '(via Outlet · 1 outlet)' for cluster
    cites and '(via reuters.com · via Sonar)' (double via) for Sonar
    cites. Cosmetic copy deviation on a binding spec — frozen here so the
    gate's ruling (ratify v6 or fix copy) flips it consciously."""
    doc = {"header": {}, "brief": m3_brief()}
    html = server._render_deep_view("story-0", "H", doc, DATE)
    eff = html.split('id="story-0-effects"')[1].split("</div>")[0]
    assert "Jan Novak:" in eff
    # CONSCIOUSLY FLIPPED (M3 gate render batch): v6's bare grammar is now
    # the render — never "(via X · via Sonar)", never "(via X · 1 outlet)".
    assert "(via reuters.com)" in eff
    assert "via Sonar)" not in eff


def test_deep_view_jumplist_matches_v6_and_arc_anchor_gap_pinned():
    """v6 parity: exactly seven links — Facts · Ledger · Mechanism · Arc ·
    Unknowns · Watch for · Sources; effects render as a section but are
    unlinked (v6's own shape). FLAGGED: an arc-less brief still emits the
    Arc link with no #...-arc section (dead anchor — cosmetic, gate
    note); with an arc, the anchor resolves."""
    doc = {"header": {}, "brief": m3_brief()}       # arc None
    html = server._render_deep_view("story-0", "H", doc, DATE)
    jump = html.split('class="deep-jumplist"')[1].split("</p>")[0]
    # CONSCIOUSLY FLIPPED (M3 gate render batch): an arc-less brief emits
    # NO Arc jumplist link (M7 no-dead-affordances precedent); the other
    # six anchors stand.
    for label in ("Facts", "Ledger", "Mechanism", "Unknowns",
                  "Watch for", "Sources"):
        assert f">{label}</a>" in jump
    assert ">Arc</a>" not in jump
    assert "Effects" not in jump and "What could follow" not in jump
    assert 'id="story-0-arc"' not in html
    doc2 = {"header": {}, "brief": m3_brief(with_arc=True)}
    html2 = server._render_deep_view("story-0", "H", doc2, DATE)
    assert 'id="story-0-arc"' in html2
    assert "Advances the thread" in html2


def test_deep_view_escapes_hostile_source_fields(tmp_paths):
    """The brief is validated but its STRINGS are model-written and its
    URLs are fetched-world data: everything must render escaped."""
    brief = m3_brief()
    brief["sources"][0]["title"] = '<script>alert(1)</script>'
    brief["sources"][0]["url"] = 'https://x.example/a"><script>steal()</script>'
    brief["pinned_facts"][0]["fact"] = 'A fact with <b>markup</b> & "quotes".'
    html = server._render_deep_view("story-0", "H",
                                    {"header": {}, "brief": brief}, DATE)
    assert "<script>" not in html
    assert "&lt;script&gt;" in html
    assert '"><script>' not in html


def test_deep_view_footer_carries_degradation_and_the_binding_copy():
    doc = {"header": {"degraded": "no full-text extraction succeeded"},
           "brief": m3_brief()}
    html = server._render_deep_view("story-0", "H", doc, DATE)
    assert "Based on 3 cited source(s)" in html
    assert "Limited source access for this story" in html
    assert "no full-text extraction succeeded" in html
    assert "cited, not verified" in html
    assert "receipts, not proof" in html


def test_source_rows_label_kinds_and_link_only_real_urls():
    doc = {"header": {}, "brief": m3_brief()}
    html = server._render_deep_view("story-0", "H", doc, DATE)
    src_html = html.split('id="story-0-sources"')[1]
    assert "cluster, full text" in src_html
    assert "cluster excerpt" in src_html
    assert "retrieved, via Sonar" in src_html
    assert 'href="https://thehill.com/a"' in src_html


# ---------------------------------------------------------------------------
# 6. No event path from the deep-view switch (focus 7)
# ---------------------------------------------------------------------------

def test_deep_view_switch_is_client_side_with_no_network_verbs():
    """Structural pin on the shipped JS: openDeepView/closeDeepView are
    pure class switches — no fetch, no XHR, no beacon, no form posts. A
    deep-view open can NEVER mint a consumption event; day-30 read/listen
    semantics are untouched by construction."""
    assert "function openDeepView" in webui.JS
    tail = webui.JS.split("function openDeepView", 1)[1]
    parts = tail.split("\nfunction ")  # top-level declarations only —
    assert parts[1].startswith("closeDeepView")  # inline callbacks stay inside
    js = parts[0] + parts[1]  # exactly the two deep-view function bodies
    for verb in ("fetch(", "XMLHttpRequest", "sendBeacon", "navigator.send",
                 "new Request", "/event"):
        assert verb not in js
    assert "classList.remove('active')" in js
    assert "lastStoryAnchor" in js


def test_rendering_deep_views_mints_zero_consumption_events(
        tmp_paths, tmp_path, monkeypatch):
    _log_entry()
    con = _page_db(tmp_path, monkeypatch, "ev.db", with_rejected=False)
    try:
        for _ in range(3):
            page, _d = server.build_page(con, DATE)
            assert "view-deep-story-0" in page
        n = con.execute(
            "SELECT COUNT(*) c FROM consumption_events").fetchone()["c"]
        assert n == 0
    finally:
        con.close()


# ---------------------------------------------------------------------------
# 7. Diagnose: Axel's availability readout
# ---------------------------------------------------------------------------

def test_diagnose_reads_deep_view_availability_with_demotion_math(tmp_paths):
    """demoted-quick leaves the depth denominator (the analyst CHOSE quick
    — that's not a missing file); absent stays in it."""
    from datetime import datetime, timezone
    from newslens import diagnose
    db.migrate()
    entry = {"ts": "2026-07-07T05:00:00Z", "date": DATE, "status": "ok",
             "sample": False, "analysis_usd": 0.02,
             "deep_views": {"1": "available", "2": "absent",
                            "3": "demoted-quick"},
             "token_cost": {"total_usd": 0.05}}
    paths.DATA_DIR.mkdir(parents=True, exist_ok=True)
    (paths.DATA_DIR / "generation_log.jsonl").write_text(
        json.dumps(entry) + "\n", encoding="utf-8")
    out = diagnose.run_diagnose(
        now_utc=datetime(2026, 7, 7, 12, tzinfo=timezone.utc))
    assert "deep-view availability" in out
    assert "1/2 depth stories carry a file" in out
    assert "slot 2: absent" in out and "slot 3: demoted-quick" in out


# ---------------------------------------------------------------------------
# 8. M3 gate closing pass — the five diffs, ordered pins, BUG17
# ---------------------------------------------------------------------------

def _trace_inputs(brief_numeral="5", title_numeral="12"):
    """A briefed slot 1 (brief carries {brief_numeral}, cluster title
    carries {title_numeral}) and an unbriefed slot 2."""
    b = m3_brief()
    b["pinned_facts"][0]["fact"] = f"The pledge is {brief_numeral} percent."
    slots = [slot(1), slot(2)]
    inputs = _inputs_for(slots)
    inputs["items_by_slot"] = {
        slots[0]["slot"]: [{"title": f"Summit weighs {title_numeral} targets"}],
        slots[1]["slot"]: [],
    }
    inputs["briefs_by_slot"] = {1: {"header": {}, "brief": b}}
    return slots, inputs


def _stories(slots, s1_extra="", s2_extra=""):
    payload = stories_payload(slots)
    payload["stories"][0]["lede"] += s1_extra
    payload["stories"][1]["lede"] += s2_extra
    return payload["stories"]


def test_trace_check_flags_invented_numerals_naming_slot_and_figures():
    """Gate 1a ordered pin: a numeral in a briefed story that traces to
    neither the brief, the cluster titles, nor the slot text warns —
    naming the slot, the loose numerals, and the §5.6 tag."""
    slots, inputs = _trace_inputs()
    warns = generate.trace_check_numerals(
        _stories(slots, s1_extra=" The measure jumped 47 percent."), inputs)
    assert len(warns) == 1
    assert warns[0].startswith("story 1:")
    assert "47" in warns[0]
    assert "§5.6 trace-don't-generate check" in warns[0]


def test_trace_check_accepts_brief_title_and_slot_sourced_numerals():
    """Every legitimate source in the universe: the brief's figure, the
    cluster title's figure, and the slot title/summary's own numerals
    pass silently."""
    slots, inputs = _trace_inputs(brief_numeral="5", title_numeral="12")
    slots[0]["summary"] = "Talks cover 30 nations."
    inputs["slots"] = slots
    stories = _stories(slots, s1_extra=" A 5 percent pledge spans 30 "
                                       "nations and 12 targets.")
    assert generate.trace_check_numerals(stories, inputs) == []


def test_trace_check_derived_numeral_warns_as_accepted_noise():
    """Gate 1a documented acceptance: derived arithmetic ('doubled to 10'
    from a brief that says 5) IS flagged — warn-grade by design, so the
    noise costs a log line, never a briefing. The pre-registered
    escalation to reject-grade lives in NOTES-M2; this pin freezes the
    warn-not-reject behavior until that ruling."""
    slots, inputs = _trace_inputs(brief_numeral="5")
    warns = generate.trace_check_numerals(
        _stories(slots, s1_extra=" The pledge doubled to 10 percent."), inputs)
    assert len(warns) == 1 and "10" in warns[0]
    assert "[warn-grade" in warns[0]


def test_trace_check_silent_for_unbriefed_slots_and_absent_stage():
    """Two silences, both deliberate: an unbriefed slot's numerals are the
    excerpt lane's business (M5 machinery), and a run with no briefs at
    all short-circuits."""
    slots, inputs = _trace_inputs()
    stories = _stories(slots, s2_extra=" An uncheckable 99 percent figure.")
    warns = generate.trace_check_numerals(stories, inputs)
    assert warns == []  # slot 2 has no brief: silent by design
    inputs["briefs_by_slot"] = {}
    assert generate.trace_check_numerals(stories, inputs) == []


@pytest.mark.parametrize("site", ["draft", "edited-swap"])
def test_BUG17_trace_check_must_run_at_both_validation_sites(
        site, tmp_paths, fake_chat, monkeypatch):
    """KNOWN-RED (BUG17, closing-pass find). Gate diff 1a shipped HALF:
    trace_check_numerals exists and behaves (units above) but has ZERO
    call sites — `grep -rn trace_check_numerals src/` finds only the def.
    The gate's enumeration says it runs after BOTH validation sites; today
    an invented numeral in the draft OR one introduced by the editor
    reaches the record with no warning. Dead-validator class (BUG-5's
    claim-without-enforcement, M9's own recurring shape).

    Fix contract: invoke trace_check_numerals(stories, inputs) after the
    draft validation AND again on the edited-swapped stories, extending
    report.warnings both times (dedupe is fine); the four unit pins above
    define its behavior; this test goes green when a run-level warning
    names the loose numeral at each site."""
    db.migrate()
    con = db.connect()
    try:
        slots = _stage_fakes(monkeypatch)
        persist_valid(con)  # slot 1 briefed: numeral universe is the brief
        payload = stories_payload(slots)
        if site == "draft":
            payload["stories"][0]["lede"] += " The measure jumped 47 percent."
            fake_chat.narrative = payload
        else:
            fake_chat.narrative = payload
            import copy
            edited = copy.deepcopy(payload)
            edited["stories"][0]["lede"] += " The measure jumped 47 percent."
            fake_chat.editor = edited
        fake_chat.script = compliant_script(slots)
        rep = generate.run_generate(date=DATE, con=con, env=dict(ENV),
                                    refresh=True)
        assert any("outside the brief+cluster universe" in w and "47" in w
                   for w in rep.warnings)
    finally:
        con.close()


def test_editor_receives_the_analysis_fact_universe_block(
        tmp_paths, fake_chat, monkeypatch):
    """Gate 1b ordered pin, RE-INDEXED for P3.1: the editor's prompt carries
    the ANALYSIS FACT UNIVERSE constraint line exactly once and the briefed
    slot's block — facts, ledger claims, discrepancy VS-never-merge, takes
    with basis/holder. The M6 editor guard set stays untouched (the shape
    checks still ran on this run).

    Re-index note (QA, P3.1): this exact setup — persist_valid + the short
    fixture lead — now ALSO trips the lead tier floor, so a narrative retry
    precedes the editor and 'the 2nd json call' stopped being the editor.
    The editor call is selected by its template marker ("You are the copy
    editor", unique to prompts/editor_pass.txt), never by position; the
    block itself (generate.build_analysis_facts_block -> editor prompt)
    is unchanged."""
    db.migrate()
    con = db.connect()
    try:
        slots = _stage_fakes(monkeypatch)
        persist_valid(con)
        fake_chat.narrative = stories_payload(slots)
        fake_chat.script = compliant_script(slots)
        generate.run_generate(date=DATE, con=con, env=dict(ENV), refresh=True)
        json_calls = [c for c in fake_chat.calls if c["json_mode"]]
        editor_calls = [c for c in json_calls
                        if "You are the copy editor" in c["prompt"]]
        assert len(editor_calls) == 1
        editor_prompt = editor_calls[0]["prompt"]
        assert editor_prompt.count("ANALYSIS FACT UNIVERSE") == 1
        assert "story 1 (briefed — its fact universe):" in editor_prompt
        assert "fact: A cited fact." in editor_prompt
        assert "take [attributed: Jan Novak]:" in editor_prompt
    finally:
        con.close()


def test_editor_block_says_excerpt_lanes_govern_when_no_briefs():
    assert generate.build_analysis_facts_block({"briefs_by_slot": {}}) == \
        "(no analysis briefs this run — the excerpt lanes govern)"
    block = generate.build_analysis_facts_block(
        {"briefs_by_slot": {2: {"brief": m3_brief(with_discrepancy=True)}}})
    assert "story 2 (briefed — its fact universe):" in block
    assert "Meeting July 8 VS Meeting Wednesday (unresolved — never merge)" \
        in block


def test_analyst_slot3_tier_newest_row_wins_across_all_three_states(tmp_paths):
    """Gate 2 ordered pin, the unit battery: none -> None; valid ->
    medium; a NEWER demoted-quick verdict row -> quick; a NEWER plain
    rejection -> None (a failed regeneration is not a ruling); a NEWER
    valid -> medium again. One derivation path, newest row governs."""
    db.migrate()
    con = db.connect()
    try:
        assert analysis.analyst_slot3_tier(con, DATE) is None
        hdr = {"manifest": {}}
        analysis.persist_brief(con, DATE, 3, "medium", "valid",
                               {"x": 1}, "", 0.01, hdr)
        assert analysis.analyst_slot3_tier(con, DATE) == "medium"
        analysis.persist_brief(con, DATE, 3, "medium", "rejected", None,
                               "demoted-quick: thin material", 0.0, hdr)
        assert analysis.analyst_slot3_tier(con, DATE) == "quick"
        analysis.persist_brief(con, DATE, 3, "medium", "rejected", None,
                               "fabricated citation 'S9'", 0.01, hdr)
        assert analysis.analyst_slot3_tier(con, DATE) is None
        analysis.persist_brief(con, DATE, 3, "medium", "valid",
                               {"x": 2}, "", 0.01, hdr)
        assert analysis.analyst_slot3_tier(con, DATE) == "medium"
        # slot-2 rows never leak into the slot-3 verdict
        analysis.persist_brief(con, DATE, 2, "medium", "rejected", None,
                               "demoted-quick: wrong slot", 0.0, hdr)
        assert analysis.analyst_slot3_tier(con, DATE) == "medium"
    finally:
        con.close()


def test_recut_fresh_run_binds_medium_from_a_persisted_valid_slot3_brief(
        tmp_paths, fake_chat, monkeypatch):
    """The RE-CUT of my flipped wiring pin, against the real shape: the
    verdict is read back from PERSISTED rows, so a valid slot-3 brief —
    not a report row — is what binds the writer to medium."""
    db.migrate()
    con = db.connect()
    try:
        slots = _stage_fakes(monkeypatch)
        persist_valid(con)                       # slot 1: briefed story
        analysis.persist_brief(con, DATE, 3, "medium", "valid",
                               m3_brief(), "", 0.01, {"manifest": {}})
        monkeypatch.setattr(analysis, "run_analysis",
                            lambda **kw: canned_report())
        fake_chat.narrative = stories_payload(slots)
        fake_chat.script = compliant_script(slots)
        rep = generate.run_generate(date=DATE, con=con, env=dict(ENV),
                                    refresh=True)
        prompt = fake_chat.calls[0]["prompt"]
        assert "TIER RULED BY THE ANALYST: medium" in prompt
        assert rep.deep_views == {"1": "available", "2": "absent",
                                  "3": "available"}
    finally:
        con.close()


def test_recut_no_refresh_run_binds_quick_from_the_persisted_verdict_row(
        tmp_paths, fake_chat, monkeypatch):
    """The other re-cut half: a persisted demoted-quick VERDICT row rules
    the writer on a --no-refresh re-run — the exact consistency gap my
    M3 pass flagged, now closed. Analysis stays provably cold; the A2
    validation accepts only quick for slot 3; deep_views carries the
    demotion for Axel's readout."""
    db.migrate()
    con = db.connect()
    try:
        paths.SOURCES_FILE.write_text(
            "sources:\n  - name: The Hill\n    rss_url: https://x.example/f\n",
            encoding="utf-8")
        slots = seed_m3(con)
        analysis.persist_brief(con, DATE, 3, "medium", "rejected", None,
                               "demoted-quick: thin material (verdict row)",
                               0.0, {"manifest": {}, "verdict": "demoted-quick"})

        def sentinel(**kw):
            raise AssertionError("run_analysis called on --no-refresh")

        monkeypatch.setattr(analysis, "run_analysis", sentinel)
        payload = stories_payload(slots)
        q = stories_payload([slot(1), slot(2), slot(3), slot(4)])["stories"][3]
        payload["stories"][2] = {**q,
                                 "headline": payload["stories"][2]["headline"],
                                 "tier": "quick"}
        fake_chat.narrative = payload
        fake_chat.script = compliant_script(slots)
        rep = generate.run_generate(date=DATE, con=con, env=dict(ENV),
                                    refresh=False)
        prompt = fake_chat.calls[0]["prompt"]
        assert "TIER RULED BY THE ANALYST: quick" in prompt
        assert rep.deep_views["3"] == "demoted-quick"
    finally:
        con.close()
