"""NL-77 QA pass (QA-owned) — adversarial probes against the cold-start
backgrounder contract (Executive Brief 2026-07-17; ADR-0013).

Two kinds of test live here, labeled:

  * RED — acceptance contracts for defects found in this pass. Each docstring
    carries the fix contract; the test is the red only that fix flips.
    (Fix loop 1, 2026-07-16: all four flipped green — D1/D2 via the
    licensing_baseline_cite currency gate, D3 via the dismissed refusal,
    D4 via the attributed-only diction scope. The GATE-POKE section below
    pins the fixed gate's edges from the re-verify pass.)
  * PIN — green characterization pins for surfaces the milestone suite left
    uncovered (server render, load-path liveness, driver economics) and for
    consciously-accepted leniencies, so any later change is a conscious flip.

Offline by construction: conftest autouse sandbox + loopback guard; every
generation call rides the injectable chat seam; $0 spent.
"""

from __future__ import annotations

import json

import pytest

from newslens import db, generate, labels, memory_core as mc, paths, server


# --- helpers -----------------------------------------------------------------

def _thread(con, topic, status="active", note=None):
    con.execute(
        "INSERT INTO memory (topic, status, principal_note) VALUES (?, ?, ?)",
        (topic, status, note))
    return con.execute(
        "SELECT id FROM memory WHERE topic = ?", (topic,)).fetchone()["id"]


def _delta(con, tid, date, what, slot=1, signif="x"):
    con.execute(
        "INSERT INTO thread_deltas (thread_id, edition_date, slot, verdict,"
        " what_happened, significance, cites_json)"
        " VALUES (?, ?, ?, 'advances', ?, ?, '[\"S1\"]')",
        (tid, date, slot, what, signif))


def _story(lede):
    return {"headline": "h", "lede": lede, "why_it_matters": ""}


def _spy_chat(calls, bg="The dispute began in 2014.", seed="It stands unresolved.",
              cost=0.0123):
    def chat(key, prompt):
        calls.append(prompt)
        return ({"backgrounder": bg, "state_seed": seed,
                 "cites": ["established background"]}, cost)
    return chat


# ===========================================================================
# RED — the counterfeit-cite hole (contract: the licensing rule / 0014 law)
# ===========================================================================

def test_red_counterfeit_baseline_cite_does_not_license_without_a_baseline(migrated_con):
    """RED (NL-77 defect D1). The dated baseline cite is a CURRENCY — it licenses
    continuity diction only because an actual ready baseline stands behind it.
    generate.py:1021 currently honors the cite's FORM alone (`mc.has_baseline_cite`
    unconditionally), so a writer/model that fabricates "(baseline, Jul 14)" on a
    thread with NO baseline (or on a story with no matched thread at all)
    silences the repetition-antecedent warning — the exact poison NL-75 built
    that net to surface, now wearing trust clothing.

    Fix contract: the generic net may honor a baseline cite only when some
    matched thread carries a READY baseline as-of the edition
    (mc.ready_baseline(con, tid, before_date=edition_date) for tid in matched
    topics); otherwise the sentence is treated exactly as bare. A cite with no
    issuing baseline is counterfeit and must keep (or gain) a finding.
    """
    con = migrated_con
    _thread(con, "Blockade")                     # followed, NO baseline rows
    slots = [{"slot": 1, "matched_memory": ["Blockade"]}]
    stories = [_story("The blockade was reinstated (baseline, Jul 14).")]
    findings = generate.forward_claim_findings(con, stories, slots, "2026-07-20")
    assert findings, (
        "a dated baseline cite on a thread with NO baseline licensed a "
        "repetition word — counterfeit currency accepted")

    # No matched thread at all: nothing can issue the cite, same contract.
    slots2 = [{"slot": 1, "matched_memory": []}]
    stories2 = [_story("Sanctions were reimposed (baseline, Jul 14).")]
    findings2 = generate.forward_claim_findings(con, stories2, slots2, "2026-07-20")
    assert findings2, (
        "a dated baseline cite with no matched thread licensed a repetition "
        "word — counterfeit currency accepted")


def test_red_baseline_cite_date_must_match_the_issuing_baseline(migrated_con):
    """RED (NL-77 defect D2, D1's tighter half). The currency is the DATED cite
    of the issuing baseline — "(baseline, <as_of>)". A cite whose date matches
    NO ready baseline on the matched threads misattributes the founding floor
    (a reader chasing "(baseline, Jul 2)" finds no Jul 2 baseline) and today
    passes both nets untouched.

    Fix contract: licensing requires the cited date to PARSE-EQUAL the as_of of
    a ready baseline on a matched thread (form-insensitive: 'Jul 14' == 'July
    14' == '2026-07-14'). Equivalent well-formed spellings of the true date must
    STAY licensed — the two green asserts below bind the fix against
    over-tightening.
    """
    con = migrated_con
    tid = _thread(con, "Blockade")
    mc.record_baseline(con, tid, "2026-07-14", "ready", backgrounder="bg 2015")

    # Green today, must stay green: true-date spellings all license.
    for cite in ("(baseline, Jul 14)", "(baseline, July 14)",
                 "(baseline, 2026-07-14)"):
        s = [_story(f"The blockade was reinstated {cite}.")]
        assert generate.forward_claim_findings(
            con, s, [{"slot": 1, "matched_memory": ["Blockade"]}],
            "2026-07-20") == [], f"true-dated cite {cite!r} must license"

    # The counterfeit date: no Jul 2 baseline exists — must be flagged.
    s = [_story("The blockade was reinstated (baseline, Jul 2).")]
    findings = generate.forward_claim_findings(
        con, s, [{"slot": 1, "matched_memory": ["Blockade"]}], "2026-07-20")
    assert findings, (
        "a baseline cite dated Jul 2 licensed a repetition word, but the only "
        "ready baseline is as-of Jul 14 — misattributed currency accepted")


# ===========================================================================
# RED — the retro command spends on a dismissed thread (contract 6 / §F)
# ===========================================================================

def test_red_thread_id_backfill_refuses_a_dismissed_thread(migrated_con):
    """RED (NL-77 defect D3). The entry-zero genre is for FOLLOWED threads
    (run_baseline_backfill's own docstring; §F: the reader said stop). The
    --all lane filters to active/dormant via threads_awaiting_baseline, but the
    --thread-id lane fetches `status` from memory and never checks it — so
    `memory-baseline --thread-id <dismissed>` runs a paid analyst call and
    writes a baseline for a thread the reader explicitly dismissed.

    Fix contract: the thread_id lane refuses status == 'dismissed_user' with an
    honest reason (mirroring the ledger-present refusal), before any cap check
    or model call. This test's spy chat must record ZERO calls and the table
    must stay empty.
    """
    con = migrated_con
    tid = _thread(con, "Old dismissed thing", status="dismissed_user")
    calls = []
    rep = generate.run_baseline_backfill(
        thread_id=tid, con=con, env={}, date="2026-07-20",
        chat=_spy_chat(calls))
    assert rep.refused, "backfill generated for a dismissed (unfollowed) thread"
    assert calls == [], "a model call was made for a dismissed thread (spend!)"
    assert con.execute(
        "SELECT COUNT(*) c FROM thread_baselines").fetchone()["c"] == 0


# ===========================================================================
# RED — double-surfacing on an incidental 'baseline' word (validator's own law)
# ===========================================================================

def test_red_one_finding_per_bare_word_even_when_sentence_says_baseline(migrated_con):
    """RED (NL-77 defect D4, warn-noise grade). baseline_diction_findings
    documents its no-double-surfacing rule: a pure-bare word is the generic
    net's job, "not re-flagged here". The gesture gate is `\\bbaseline\\b`,
    which also matches the word's ordinary English sense — "the 2019 baseline
    level", "baseline emissions" — so a bare continuity word in such a sentence
    is flagged by BOTH nets: two findings for one word, on prose that never
    gestured at OUR baseline.

    Fix contract: forward_claim_findings yields at most ONE finding per
    (sentence, repetition-word): either the diction validator skips sentences
    the generic net already flags (fire only when the generic net is silent),
    or the aggregator dedupes. The false-positive gesture itself (a sentence
    that IS source-attributed and uses 'baseline' incidentally) is pinned
    separately as accepted warn-noise — this red is only about the double.
    """
    con = migrated_con
    tid = _thread(con, "Climate accord")
    mc.record_baseline(con, tid, "2026-07-14", "ready", backgrounder="bg 2015")
    slots = [{"slot": 1, "matched_memory": ["Climate accord"]}]
    stories = [_story("Emissions rose again from the 2019 baseline level.")]
    findings = generate.forward_claim_findings(con, stories, slots, "2026-07-20")
    assert len(findings) == 1, (
        f"expected one finding for one bare word, got {len(findings)}: "
        f"{findings}")


# ===========================================================================
# PIN — accepted validator leniencies/strictness (conscious, documented)
# ===========================================================================

def test_pin_gesture_on_attributed_baseline_named_source_is_accepted_noise(migrated_con):
    """PIN (accepted risk, implementer-named angle). A real source literally
    named 'Baseline' (Baseline Ventures, a 'Baseline Survey') inside a
    source-attributed sentence trips the gesture gate: the generic net stays
    silent (attribution marker 'cited'), the diction net fires one warn-grade
    finding. Accepted: respecting attribution markers here would reopen the
    exact gap the validator exists to close ('per the baseline, reinstated' —
    'per ' IS a marker). Findings are advisory warnings, not draft failures.
    A conscious flip of this pin requires a discrimination mechanism the gate
    signs off on, not a marker bypass."""
    con = migrated_con
    tid = _thread(con, "Chip exports")
    mc.record_baseline(con, tid, "2026-07-14", "ready", backgrounder="bg 2015")
    slots = [{"slot": 1, "matched_memory": ["Chip exports"]}]
    stories = [_story(
        "Regulators again cited estimates from Baseline Ventures.")]
    findings = generate.baseline_diction_findings(
        con, stories, slots, "2026-07-20")
    assert len(findings) == 1          # the accepted false positive, on record


def test_pin_year_shaped_quantity_licenses_backgrounder_continuity(migrated_con):
    """PIN (accepted leniency, implementer-named angle). The backgrounder
    validator treats any plausible-year token as the dated anchor — including a
    year-shaped QUANTITY ('drew 2019 arrests'). Clause-level parsing was
    rejected as more fragile (a comma-separated dated clause would false-
    reject). Failure direction: a rare unanchored continuity word survives
    validation INSIDE the disclosed, baseline-cited backgrounder body — never
    in edition prose. Flip consciously or not at all."""
    raw = {"backgrounder": "The pact was reinstated after protests drew 2019 "
                           "arrests.",
           "state_seed": "It stands.", "cites": []}
    bg, seed, cites = generate._validate_baseline(raw)
    assert bg                                     # accepted (the leniency)


def test_pin_decade_anchor_is_rejected_as_bare(migrated_con):
    """PIN (strict direction, prompt/validator mismatch on record). The prompt
    endorses decade-shaped vagueness ('since the early 2010s') for uncertain
    specifics, but the validator's date detectors (_ISO_RE, _MONTH_DAY_RE,
    year regex) do not count '2010s' as a date — a continuity word anchored
    only by a decade is REJECTED as bare. Failure direction is safe (honest
    failed row, retry; never fabrication). If the org wants decade anchors to
    license, that is a conscious flip: extend the year regex (e.g. r'\\d{4}s?')
    AND note it in the ADR — do not weaken the prompt's law instead."""
    raw = {"backgrounder": "Talks resumed repeatedly since the early 2010s.",
           "state_seed": "It stands.", "cites": []}
    with pytest.raises(generate.BaselineRejected):
        generate._validate_baseline(raw)


def test_pin_cite_grammar_edges():
    """PIN — the currency's grammar, so drift is conscious. Accepted: Mon D /
    full month / dotted abbrev / ISO / loose spacing / any case. Rejected (and
    therefore UNLICENSED, the safe direction): year-suffixed 'Jul 14, 2026',
    day-first '14 July', colon separator, 'Sept' (not a recognized
    abbreviation — 'Sep' is), bare forms."""
    yes = ["(baseline, Jul 14)", "(baseline, July 14)", "(baseline, Jul. 14)",
           "(baseline, 2026-07-14)", "( baseline ,  Jul 14 )",
           "(BASELINE, JUL 14)"]
    no = ["(baseline, Jul 14, 2026)", "(baseline, 14 July)",
          "(baseline: Jul 14)", "(baseline, Sept 14)", "(baseline)",
          "per the baseline", "baseline, Jul 14"]
    for t in yes:
        assert mc.has_baseline_cite(t), f"expected recognized: {t!r}"
    for t in no:
        assert not mc.has_baseline_cite(t), f"expected unrecognized: {t!r}"


# ===========================================================================
# PIN — two-clocks: a future baseline never enters an earlier edition
# ===========================================================================

def test_pin_future_baseline_stays_out_of_earlier_editions(migrated_con):
    con = migrated_con
    tid = _thread(con, "Iran talks")
    mc.record_baseline(con, tid, "2026-07-20", "ready",
                       backgrounder="Began in 2014.")
    # the writer prompt for an EARLIER edition must not see it
    assert mc.writer_baseline_block(con, "Iran talks", before_date="2026-07-14") == ""
    # ... and the diction validator must not treat the thread as baselined then
    slots = [{"slot": 1, "matched_memory": ["Iran talks"]}]
    stories = [_story("Per the baseline, talks resumed.")]
    assert generate.baseline_diction_findings(
        con, stories, slots, "2026-07-14") == []
    # same-day boundary: as_of == edition date IS visible (inclusive bound)
    assert mc.writer_baseline_block(con, "Iran talks", before_date="2026-07-20") != ""


# ===========================================================================
# PIN — load-path liveness: the block reaches the assembled prompt from the DB
# ===========================================================================

def test_pin_load_briefing_inputs_carries_baseline_after_ledger(tmp_paths):
    """End-to-end liveness of writer-flow LAST: a REAL loaded briefing (not
    synthetic inputs) renders the hot thread's MEMORY ledger block and the
    cold thread's BACKGROUNDER block, baseline AFTER ledger, in the assembled
    writer prompt. A pending-only thread contributes nothing."""
    db.migrate()
    con = db.connect()
    date = "2026-07-20"
    hot = _thread(con, "Old War")
    _delta(con, hot, "2026-07-10", "Front line shifted near the river.")
    cold = _thread(con, "Iran talks")
    mc.record_baseline(con, cold, "2026-07-14", "ready",
                       backgrounder="The talks began in 2014.",
                       state_seed="They stand stalled.")
    pending = _thread(con, "Fresh follow")
    mc.write_baseline_intent(con, pending, "2026-07-19")

    slots = [{"slot": "1", "story_title": "S1", "summary": "s", "item_ids": [],
              "outlets": ["X"], "matched_tags": [],
              "matched_memory": ["Old War", "Iran talks", "Fresh follow"],
              "override": False,
              "corroboration_label": "Reported by 1 named outlet"}]
    with con:
        con.execute("INSERT INTO briefings (date, story_slots) VALUES (?, ?)",
                    (date, json.dumps(slots)))

    inputs = generate.load_briefing_inputs(con, date)
    s = inputs["slots"][0]
    assert "BACKGROUNDER" in s["thread_baseline"]
    assert "(baseline, Jul 14)" in s["thread_baseline"]
    assert "Fresh follow" not in s["thread_baseline"]   # pending contributes nothing

    inputs["briefs_by_slot"] = {}
    inputs["analyst_slot3_tier"] = None
    prompt = generate.build_narrative_prompt(date, "A", inputs)
    i_ledger = prompt.index("MEMORY — the record for thread 'Old War'")
    i_base = prompt.index("BACKGROUNDER — how 'Iran talks' got here")
    assert i_base > i_ledger, "baseline block must ride AFTER the ledger block"
    con.close()


# ===========================================================================
# PIN — the reader surface (server render): disclosure, honest states, escaping
# ===========================================================================

def _page(con, tid):
    mrow = con.execute("SELECT * FROM memory WHERE id = ?", (tid,)).fetchone()
    return server._render_thread_page(con, mrow)


def test_pin_thread_page_ready_baseline_disclosed_and_escaped(tmp_paths):
    """Baselined empty-ledger thread page: 'How we got here' present with the
    disclosure + dated cite; the state section falls back to the SEED with the
    same disclosure; the story-so-far stays the honest empty note (the
    anti-obligation kill-test at the reader surface: NO fabricated arc or
    timeline from a baseline); model-authored text is HTML-escaped."""
    db.migrate()
    con = db.connect()
    tid = _thread(con, "Blockade")
    mc.record_baseline(
        con, tid, "2026-07-14", "ready",
        backgrounder="It began in March 2024.\n\nA <script>x</script> deal followed.",
        state_seed="It stands <b>unresolved</b>.")
    html = _page(con, tid)
    assert f'<h2 class="deep-section-label">{labels.HOW_WE_GOT_HERE}</h2>' in html
    assert labels.BASELINE_DISCLOSURE in html
    assert "(baseline, Jul 14)" in html
    # the seed serves "Where this stands", disclosed, until a real state exists
    assert "baseline-seed" in html and "It stands" in html
    assert labels.THREAD_NO_STATE not in html
    # honest empty ledger — the baseline never fabricates story-so-far content
    # (the day-one empty note stays; a baseline is not coverage)
    assert labels.THE_STORY_SO_FAR in html
    assert labels.THREAD_NO_ARC in html
    # model text is escaped at the render boundary
    assert "<script>" not in html and "&lt;script&gt;" in html
    assert "<b>" not in html
    con.close()


def test_pin_thread_page_pending_failed_and_real_state_precedence(tmp_paths):
    db.migrate()
    con = db.connect()
    # pending: the honest preparing note, no disclosure/cite yet
    t1 = _thread(con, "Pending thread")
    mc.write_baseline_intent(con, t1, "2026-07-19")
    h1 = _page(con, t1)
    assert labels.BASELINE_PENDING in h1
    assert labels.BASELINE_DISCLOSURE not in h1
    # failed newest: section absent entirely; seed hidden (newest wins)
    t2 = _thread(con, "Failed thread")
    mc.record_baseline(con, t2, "2026-07-14", "ready",
                       backgrounder="Began in 2014.", state_seed="Stands.")
    mc.record_baseline(con, t2, "2026-07-16", "failed", reason="retry failed")
    h2 = _page(con, t2)
    assert labels.HOW_WE_GOT_HERE not in h2
    assert labels.BASELINE_DISCLOSURE not in h2
    assert labels.THREAD_NO_STATE in h2
    # a real thread_state outranks the seed; the section itself remains
    t3 = _thread(con, "Real state thread")
    mc.record_baseline(con, t3, "2026-07-14", "ready",
                       backgrounder="Began in 2014.", state_seed="Seed text.")
    con.execute("INSERT INTO thread_state (thread_id, as_of_date, state_text)"
                " VALUES (?, '2026-07-18', 'The real record-grade state.')",
                (t3,))
    h3 = _page(con, t3)
    assert "The real record-grade state." in h3
    assert "Seed text." not in h3          # seed retired once a real state exists
    assert labels.HOW_WE_GOT_HERE in h3    # the backgrounder section is permanent
    con.close()


# ===========================================================================
# PIN — driver economics: idempotence, all-skip budget, intent-date propagation
# ===========================================================================

def test_pin_backfill_all_is_idempotent(migrated_con):
    con = migrated_con
    _thread(con, "Alpha")
    _thread(con, "Beta")
    calls = []
    rep1 = generate.run_baseline_backfill(
        all_threads=True, con=con, env={}, date="2026-07-20",
        chat=_spy_chat(calls))
    assert not rep1.refused and len(calls) == 2
    rows = con.execute("SELECT COUNT(*) c FROM thread_baselines").fetchone()["c"]
    rep2 = generate.run_baseline_backfill(
        all_threads=True, con=con, env={}, date="2026-07-21",
        chat=_spy_chat(calls))
    assert rep2.refused                     # nothing awaits; honest no-op
    assert len(calls) == 2                  # no second spend
    assert con.execute(
        "SELECT COUNT(*) c FROM thread_baselines").fetchone()["c"] == rows


def test_pin_backfill_budget_skip_writes_nothing_keeps_intent(migrated_con):
    con = migrated_con
    tid = _thread(con, "Alpha")
    mc.write_baseline_intent(con, tid, "2026-07-19")
    calls = []
    rep = generate.run_baseline_backfill(
        all_threads=True, con=con,
        env={"BUDGET_CAP_USD_PER_RUN": "0.000001"}, date="2026-07-20",
        chat=_spy_chat(calls))
    assert not rep.refused
    assert calls == []                                  # cap pre-check: no call
    assert rep.spent_usd == 0.0
    assert [g["outcome"] for g in rep.generated] == ["skipped-budget"]
    assert rep.warnings
    assert mc.latest_baseline(con, tid)["status"] == "pending"   # intent stands


def test_pin_backfill_pending_intent_date_outranks_run_date(migrated_con):
    con = migrated_con
    tid = _thread(con, "Alpha")
    mc.write_baseline_intent(con, tid, "2026-07-10")
    rep = generate.run_baseline_backfill(
        all_threads=True, con=con, env={}, date="2026-07-20",
        chat=_spy_chat([]))
    assert rep.generated[0]["as_of"] == "2026-07-10"
    b = mc.ready_baseline(con, tid)
    assert b and b["as_of_date"] == "2026-07-10"
    assert mc.baseline_cite(b["as_of_date"]) == "(baseline, Jul 10)"


def test_pin_thread_id_backfill_pending_intent_date_outranks_run_date(migrated_con):
    """Gate FIX-2: the --thread-id lane honors a standing intent's as_of the
    same way --all does — the run date never overwrites the intent date."""
    con = migrated_con
    tid = _thread(con, "Alpha")
    mc.write_baseline_intent(con, tid, "2026-07-10")
    rep = generate.run_baseline_backfill(
        thread_id=tid, con=con, env={}, date="2026-07-20",
        chat=_spy_chat([]))
    assert rep.generated[0]["as_of"] == "2026-07-10"
    b = mc.ready_baseline(con, tid)
    assert b and b["as_of_date"] == "2026-07-10"


# ===========================================================================
# GATE-POKE PINS (re-verify pass, fix loop 1) — licensing_baseline_cite edges
# ===========================================================================

def test_pin_d1_flip_is_the_generic_net_finding(migrated_con):
    """The counterfeit-cite flip fires the RIGHT net: the generic repetition
    finding ('reinstated' class / antecedent message), exactly once."""
    con = migrated_con
    _thread(con, "Blockade")
    f = generate.forward_claim_findings(
        con, [_story("The blockade was reinstated (baseline, Jul 14).")],
        [{"slot": 1, "matched_memory": ["Blockade"]}], "2026-07-20")
    assert len(f) == 1 and "antecedent" in f[0]


def test_pin_licensing_gate_matcher_edges(migrated_con):
    """The as_of matcher's accept/reject set, pinned. Accepted residual on
    record: a sentence carrying one TRUE cite licenses even with an extra
    wrong-dated rider cite beside it (display noise the warn surface does not
    chase; the licensed word IS backed by a real floor)."""
    con = migrated_con
    tid = _thread(con, "Blockade")
    mc.record_baseline(con, tid, "2026-07-04", "ready", backgrounder="bg 2015")

    def lic(s):
        return mc.licensing_baseline_cite(con, ["Blockade"], s, "2026-07-20")

    assert lic("reinstated (baseline, Jul 4)")
    assert lic("reinstated (baseline, Jul 04)")           # zero-padded day
    assert lic("reinstated (baseline, July 4)")           # full month
    assert lic("reinstated (baseline, 2026-07-04)")       # ISO, year-strict OK
    assert not lic("reinstated (baseline, Jul 1)")        # wrong day
    assert not lic("reinstated (baseline, Jul 14)")       # wrong day, 2-digit
    assert not lic("reinstated (baseline, 2025-07-04)")   # ISO wrong year
    assert not lic("reinstated (baseline)")               # bare form
    assert lic("reinstated (baseline, Jul 2) and (baseline, Jul 4)")  # rider


def test_pin_human_cite_year_comes_from_the_founding_floor(migrated_con):
    """'(baseline, Jul 14)' carries no year BY DESIGN — the issuing floor (a
    thread's single ready baseline) supplies it, so a 2025 floor licenses the
    human form in a 2026 edition. The ISO form stays year-strict."""
    con = migrated_con
    tid = _thread(con, "Old floor")
    mc.record_baseline(con, tid, "2025-07-14", "ready", backgrounder="bg 2015")
    assert mc.licensing_baseline_cite(
        con, ["Old floor"], "reinstated (baseline, Jul 14)", "2026-07-20")
    assert not mc.licensing_baseline_cite(
        con, ["Old floor"], "reinstated (baseline, 2026-07-14)", "2026-07-20")


def test_pin_attributed_wrong_date_evasion_caught_on_baselined_thread(migrated_con):
    """The evasion stack — attribution marker (generic-net exemption) + a
    wrong-dated cite (counterfeit currency) — is caught by the diction net on a
    baselined thread: the cite's own 'baseline' word trips the gesture gate."""
    con = migrated_con
    tid = _thread(con, "Blockade")
    mc.record_baseline(con, tid, "2026-07-14", "ready", backgrounder="bg 2015")
    f = generate.forward_claim_findings(
        con, [_story("Per the baseline, the blockade was reinstated "
                     "(baseline, Jul 2).")],
        [{"slot": 1, "matched_memory": ["Blockade"]}], "2026-07-20")
    assert len(f) == 1 and "dated-anchored" in f[0]


def test_pin_phantom_gesture_on_baselineless_thread_is_exempt_today(migrated_con):
    """DOCUMENTED RESIDUAL (pre-existing net semantics, flagged to the gate):
    on a thread with NO baseline, 'per the baseline, reinstated ...' draws zero
    findings — with or without a counterfeit cite — because the generic net's
    attribution exemption predates NL-77 and never verified a named source's
    existence, and the diction net scopes to baselined threads by charter.
    The phantom-SOURCE class (attributing to anything nonexistent) is a
    separate, older exemption breadth; closing it belongs to a cite-verification
    rung (0014's 'delta inheriting baseline diction' milestone is the natural
    home), not to NL-77's currency gate. A conscious flip only."""
    con = migrated_con
    _thread(con, "Blockade")
    slots = [{"slot": 1, "matched_memory": ["Blockade"]}]
    f1 = generate.forward_claim_findings(
        con, [_story("Per the baseline, the blockade was reinstated.")],
        slots, "2026-07-20")
    f2 = generate.forward_claim_findings(
        con, [_story("Per the baseline, the blockade was reinstated "
                     "(baseline, Jul 14).")], slots, "2026-07-20")
    assert (f1, f2) == ([], [])


def test_pin_superseded_floor_does_not_license(migrated_con):
    """Newest-wins reaches the currency gate: a ready floor superseded by a
    later failed row licenses nothing; its once-true cite is flagged."""
    con = migrated_con
    tid = _thread(con, "Blockade")
    mc.record_baseline(con, tid, "2026-07-14", "ready", backgrounder="bg 2015")
    mc.record_baseline(con, tid, "2026-07-16", "failed", reason="retry failed")
    assert not mc.licensing_baseline_cite(
        con, ["Blockade"], "reinstated (baseline, Jul 14)", "2026-07-20")
    f = generate.forward_claim_findings(
        con, [_story("The blockade was reinstated (baseline, Jul 14).")],
        [{"slot": 1, "matched_memory": ["Blockade"]}], "2026-07-20")
    assert len(f) == 1


# ===========================================================================
# PIN — the follow-lane gate at the CLI boundary
# ===========================================================================

def test_pin_memory_add_skips_intent_when_ledger_exists(tmp_paths):
    from newslens import cli
    assert cli.main(["memory", "add", "Hot Topic"]) == 0
    con = db.connect(paths.DB_PATH)
    try:
        tid = con.execute("SELECT id FROM memory WHERE topic='Hot Topic'"
                          ).fetchone()["id"]
        # simulate history arriving, then a dormancy round-trip re-follow
        _delta(con, tid, "2026-07-10", "It moved.")
        with con:
            con.execute("UPDATE memory SET status='dormant' WHERE id=?", (tid,))
        assert cli.main(["memory", "add", "Hot Topic"]) == 0   # revival path
        n = con.execute("SELECT COUNT(*) c FROM thread_baselines"
                        " WHERE thread_id=?", (tid,)).fetchone()["c"]
        # one pending row from the ORIGINAL cold follow; the revival with a
        # ledger on file must not add another (no founding floor needed)
        assert n == 1
    finally:
        con.close()


def test_pin_ready_baseline_refuses_empty_backgrounder(migrated_con):
    """Gate FIX-3: the write boundary itself refuses a contentless 'ready'
    row — ready_baseline feeds licensing_baseline_cite, so an empty ready
    row would be counterfeit licensing currency minted at the DB layer."""
    con = migrated_con
    tid = _thread(con, "Alpha")
    with pytest.raises(ValueError, match="empty"):
        mc.record_baseline(con, tid, "2026-07-10", "ready", backgrounder="")
    with pytest.raises(ValueError, match="empty"):
        mc.record_baseline(con, tid, "2026-07-10", "ready", backgrounder="   ")
    # 'failed' with no backgrounder stays legitimate (the honest refusal row)
    rid = mc.record_baseline(con, tid, "2026-07-10", "failed",
                             reason="validation rejected the draft")
    assert rid
