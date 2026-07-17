"""P3.1 — editorial enforcement (principal rulings batch, DECISIONS.md
2026-07-06; implementer-written, QA extends — the test_p3_script.py
pattern).

What this file pins:
  * CALIBRATION (the ruling's own evidence): the script that shipped to the
    principal's ears (tests/fixtures/script/2026-07-06-repetitive.txt, the
    verbatim artifact) is CAUGHT — cold-open cap + cross-section
    repetition; a legitimately edited script of the same edition
    (2026-07-06-legitimate.txt) passes CLEAN.
  * LIVENESS reds per the ENGINEERING.md BUG17 rule (enforcement is born
    with the red only its wiring can flip): the structural gate and the
    lead tier floor both reach run_generate's persisted output, not just a
    helper function.
  * SPEND-PROOF (offline, injected models, $0): each hard-with-retry path
    fires at most ONE retry, and a retry whose estimate would breach the
    remaining cap is SKIPPED with disclosure — never attempted.

Everything runs offline: generate._chat is a sequenced fake; the autouse
sandbox isolates DATA_DIR/DB_PATH; zero consumption events; the openai TTS
default degrades through the loopback-only socket guard into the disclosed
no-audio path (AudioError), exactly like the kokoro-absent path before it.
"""

from __future__ import annotations

import copy
import json
import time
from pathlib import Path

import pytest

from newslens import audio, config, db, generate

from test_generate import compliant_script, slot, stories_payload
from test_m3_qa import _stage_fakes, persist_valid

DATE = "2026-07-07"
ENV = {"OPENAI_API_KEY": "sk-qa-fake"}
FIXTURES = Path(__file__).parent / "fixtures" / "script"


# --- injected model: sequenced fake (retry paths need per-call outputs) ------

@pytest.fixture
def fake_seq(monkeypatch):
    """Position-sequenced stateful fake (local copy per the fixtures-don't-
    import-across-modules convention): the Nth json call serves
    .narratives[N-1] (last entry sticks — the editor echoes the final
    narrative, a no-op edit); the Nth non-json call serves .scripts[N-1]
    (last entry sticks). .calls records every request."""
    state = type("S", (), {})()
    state.calls, state.narratives, state.scripts = [], [], []

    def chat(key, prompt, max_tokens, temperature, json_mode):
        state.calls.append({"json_mode": json_mode, "prompt": prompt})
        if json_mode:
            n = sum(1 for c in state.calls if c["json_mode"])
            content = json.dumps(
                state.narratives[min(n - 1, len(state.narratives) - 1)])
        else:
            n = sum(1 for c in state.calls if not c["json_mode"])
            content = state.scripts[min(n - 1, len(state.scripts) - 1)]
        return {"choices": [{"finish_reason": "stop",
                             "message": {"content": content}}],
                "usage": {"prompt_tokens": 900, "completion_tokens": 200}}

    monkeypatch.setattr(generate, "_chat", chat)
    monkeypatch.setattr(time, "sleep", lambda s: None)
    return state


COLD_OPEN_4_SENTENCES = (
    "The cartel decided to lift output again this cycle. "
    "Prices had been sliding for weeks before the decision. "
    "Ministers met over the weekend to settle on the size. "
    "Some members pressed for a larger step than agreed. "
    "It's Tuesday, July 7. Here's what matters today."
)
REUSED_RUN = ("the ministers agreed to keep the arrangement under review "
              "through the autumn session")


def violating_script(slots):
    """A script that clears every OTHER gate (hard checks, the degenerate
    backstop, furniture) and violates EXACTLY the two structural rules:
    a 4-sentence cold open before the dateline + one repeated 13-word run
    across two 15+-word paragraphs."""
    para_a = ("In the first section, " + REUSED_RUN
              + ", officials said after the talks concluded.")
    para_b = ("Later in the hour, " + REUSED_RUN
              + ", a point the communique repeated in closing.")
    return (COLD_OPEN_4_SENTENCES + "\n\n" + compliant_script(slots)
            + "\n\n" + para_a + "\n\n" + para_b)


def payload_with_lead_words(slots, sentinel, n_repeats=50):
    """stories_payload with story 1's lede padded past LEAD_FLOOR_WORDS
    using numeral-free, hedge-free filler + a unique sentinel sentence.
    n_repeats=50 (~450 filler words) clears the NL-63 M2 floor of 450."""
    payload = copy.deepcopy(stories_payload(slots))
    filler = "The analysis continues with sourced detail and measured context. "
    payload["stories"][0]["lede"] += " " + filler * n_repeats + sentinel
    return payload


# --- calibration: the ruling's own evidence ----------------------------------

def test_calibration_the_2026_07_06_script_is_caught():
    """The exact artifact the principal reviewed (post-tts_safe_pass text,
    verbatim) trips BOTH rules: the 7-sentence story-body cold open he
    called 'way too long' and the open/menu/body retells."""
    text = (FIXTURES / "2026-07-06-repetitive.txt").read_text(encoding="utf-8")
    out = generate.script_structural_check(text)
    cold = [v for v in out if v.startswith("cold open runs")]
    reps = [v for v in out if "retell the same material" in v]
    assert cold and "7 sentences" in cold[0]
    assert len(reps) >= 2  # open-vs-lead-body AND menu-vs-body at minimum


def test_calibration_a_legitimately_edited_script_passes_clean():
    """Same edition, edited to the bar: one-line hook -> dateline, teasing
    menu, full bodies, rephrased outro. Zero structural violations — the
    gate must not tax a good script."""
    text = (FIXTURES / "2026-07-06-legitimate.txt").read_text(encoding="utf-8")
    assert generate.script_structural_check(text) == []


# --- unit boundaries ----------------------------------------------------------

def test_cold_open_cap_boundaries():
    ok = ("Oil got cheaper overnight. The cartel answered anyway. "
          "Here is the shape of it. It's Monday, July 6, 2026. "
          "Here's what matters today.\n\nBody follows in a longer paragraph "
          "with enough words to be a real section of spoken prose here.")
    assert not any(v.startswith("cold open") for v in
                   generate.script_structural_check(ok))
    four = generate.script_structural_check(
        COLD_OPEN_4_SENTENCES + "\n\nBody follows in a longer paragraph "
        "with enough words to be a real section of spoken prose here.")
    assert any(v.startswith("cold open runs 4 sentences") for v in four)
    wordy = ("This single opening sentence keeps rolling through clause "
             "after clause piling phrase upon phrase detail upon detail "
             "aside upon aside observation upon observation digression "
             "upon digression qualification upon qualification until it "
             "has spent well over sixty words of the listener's morning "
             "on pure scene-setting that a tight one line hook would have "
             "carried far better than this meandering marathon ever could "
             "have hoped to manage anyway. It's Monday, July 6, 2026.")
    assert any(v.startswith("cold open") for v in
               generate.script_structural_check(wordy))


def test_no_dateline_no_cold_open_check():
    """Documented limitation carried over from the warn-grade detector: no
    dateline anchor = no cold-open boundary (the dateline itself is the
    script contract's job upstream). Repetition still runs. Since the
    2026-07-09 anchor fix the skip is no longer SILENT at run level —
    see test_LIVENESS_no_dateline_cold_open_cap_is_disclosed."""
    text = ("Good morning and welcome to a briefing without any date line "
            "in it at all today.\n\nGood morning and welcome to a briefing "
            "without any date line in it at all again.")
    out = generate.script_structural_check(text)
    assert not any(v.startswith("cold open") for v in out)


def test_repetition_needs_three_distinct_shared_six_grams():
    # 8 shared words = 3 distinct 6-grams -> fires.
    run8 = "alpha bravo charlie delta echo foxtrot golf hotel"
    a = f"Opening frame words here, {run8}, and then some closing words too."
    b = f"Different frame entirely now, {run8}, with another ending altogether."
    out = generate.script_structural_check(a + "\n\n" + b)
    assert any("retell the same material" in v for v in out)
    # 7 shared words = 2 distinct 6-grams -> below threshold, silent.
    run7 = "alpha bravo charlie delta echo foxtrot golf"
    c = f"Opening frame words here, {run7}, and then some closing words too."
    d = f"Different frame entirely now, {run7}, with another ending altogether."
    assert generate.script_structural_check(c + "\n\n" + d) == []


def test_short_paragraphs_are_exempt_from_repetition():
    """Menu one-liners under 15 words never pair-match — the menu may name
    the same subject the body covers; it may not retell it."""
    line = "alpha bravo charlie delta echo foxtrot golf hotel india."
    assert generate.script_structural_check(line + "\n\n" + line) == []


def test_repetition_reports_cap_at_three():
    run8 = "alpha bravo charlie delta echo foxtrot golf hotel"
    paras = [
        f"Frame number {w} opens here, {run8}, then trails away separately."
        for w in ("one", "two", "three", "four", "five")
    ]
    out = generate.script_structural_check("\n\n".join(paras))
    assert len([v for v in out if "retell" in v]) == 3  # MAX_STRUCTURAL_REPORTS


def test_repetition_bites_between_distant_sections_at_digest_scale():
    """QA (NL-63 M2 fix loop, flips audit): never-repeat at the DIGEST's
    section count. A k=5-shaped script — opener plus five 15+-word sections —
    where ONLY the first and last sections share material draws exactly ONE
    retell report naming that distant pair; the four distinct story sections
    stay silent. Pairwise over all sections: the 5-story digest gets the same
    never-repeat law the 2-section pin established."""
    run8 = "alpha bravo charlie delta echo foxtrot golf hotel"
    opener = (f"The morning menu frames the day here, {run8}, before the "
              "stories begin in order.")
    stories = [
        "The first story walks through an energy decision made overnight and "
        "what it changes for prices tomorrow.",
        "A second segment turns to housing policy where a council vote "
        "shifted the permitting timeline again.",
        "Then a courtroom development in the chip dispute moved the schedule "
        "and sharpened the remedies question.",
        "Next a research group published findings on grid storage that "
        "utilities had been waiting to see.",
    ]
    closer = (f"The final story returns to the same ground, {run8}, closing "
              "the loop on the open.")
    out = generate.script_structural_check(
        "\n\n".join([opener] + stories + [closer]))
    retells = [v for v in out if "retell the same material" in v]
    assert len(retells) == 1
    assert "sections 1 and 6" in retells[0]


# --- liveness: the structural gate reaches the persisted script ---------------

def test_LIVENESS_structural_gate_retries_and_persists_the_clean_retry(
        tmp_paths, fake_seq, monkeypatch):
    """The red only the wiring can flip: a violating first script triggers
    ONE injected-violations retry; the clean retry is what lands in the
    briefings row. Remove the _run_generate_body wiring and this fails."""
    db.migrate()
    con = db.connect()
    try:
        slots = _stage_fakes(monkeypatch)
        fake_seq.narratives = [stories_payload(slots)]
        fake_seq.scripts = [violating_script(slots), compliant_script(slots)]
        rep = generate.run_generate(date=DATE, con=con, env=dict(ENV),
                                    refresh=True)
        script_calls = [c for c in fake_seq.calls if not c["json_mode"]]
        assert len(script_calls) == 2
        assert "STRUCTURAL VIOLATIONS" in script_calls[1]["prompt"]
        assert "cold open runs 4 sentences" in script_calls[1]["prompt"]
        assert any("script structural retry: violations cleared" in w
                   for w in rep.warnings)
        row = con.execute("SELECT script_text FROM briefings WHERE date=?",
                          (DATE,)).fetchone()
        assert "The cartel decided to lift output" not in row["script_text"]
        assert "Good morning. Here is your briefing." in row["script_text"]
    finally:
        con.close()


def test_structural_retry_fires_at_most_once_and_ships_with_disclosure(
        tmp_paths, fake_seq, monkeypatch):
    """Spend-proof: retry output still violating => NO second retry; the
    first attempt ships WITH the disclosure warning."""
    db.migrate()
    con = db.connect()
    try:
        slots = _stage_fakes(monkeypatch)
        fake_seq.narratives = [stories_payload(slots)]
        fake_seq.scripts = [violating_script(slots)]  # sticks: retry = same
        rep = generate.run_generate(date=DATE, con=con, env=dict(ENV),
                                    refresh=True)
        script_calls = [c for c in fake_seq.calls if not c["json_mode"]]
        assert len(script_calls) == 2  # first + exactly one retry, never a third
        assert any("script structural retry did not improve" in w
                   and "shipped with disclosure" in w for w in rep.warnings)
        row = con.execute("SELECT script_text FROM briefings WHERE date=?",
                          (DATE,)).fetchone()
        assert "The cartel decided to lift output" in row["script_text"]
    finally:
        con.close()


def test_structural_retry_skipped_when_it_would_breach_the_cap(
        tmp_paths, fake_seq, monkeypatch):
    """Spend-proof: the retry is estimated BEFORE it is attempted; an
    estimate over the remaining cap means zero retry calls + disclosure."""
    real_est = generate._est_cost

    def starving_est(prompt, max_tokens, step="narrative"):
        # B2: _est_cost is seat-priced per step now — the stub passes the step
        # through so the poisoned estimate stays the ONLY difference.
        if "STRUCTURAL VIOLATIONS" in prompt:
            return 999.0
        return real_est(prompt, max_tokens, step)

    monkeypatch.setattr(generate, "_est_cost", starving_est)
    db.migrate()
    con = db.connect()
    try:
        slots = _stage_fakes(monkeypatch)
        fake_seq.narratives = [stories_payload(slots)]
        fake_seq.scripts = [violating_script(slots)]
        rep = generate.run_generate(date=DATE, con=con, env=dict(ENV),
                                    refresh=True)
        script_calls = [c for c in fake_seq.calls if not c["json_mode"]]
        assert len(script_calls) == 1  # the retry was never attempted
        assert any("retry skipped" in w and "exceed the cap" in w
                   for w in rep.warnings if "STRUCTURAL" in w)
    finally:
        con.close()


# --- liveness: the lead tier floor (item 3) ------------------------------------

def test_LIVENESS_tier_floor_retry_lifts_a_briefed_lead(
        tmp_paths, fake_seq, monkeypatch):
    """A briefed lead under LEAD_FLOOR_WORDS draws ONE narrative retry with
    the deficiency injected; the floor-meeting retry is what persists."""
    db.migrate()
    con = db.connect()
    try:
        slots = _stage_fakes(monkeypatch)
        persist_valid(con)  # slot-1 analysis brief exists -> the floor binds
        sentinel = "The lead now carries its full analytical weight."
        fake_seq.narratives = [stories_payload(slots),
                               payload_with_lead_words(slots, sentinel)]
        fake_seq.scripts = [compliant_script(slots)]
        rep = generate.run_generate(date=DATE, con=con, env=dict(ENV),
                                    refresh=True)
        json_calls = [c for c in fake_seq.calls if c["json_mode"]]
        assert len(json_calls) == 3  # narrative + ONE retry + editor
        assert "TIER-EXPRESSION VIOLATION" in json_calls[1]["prompt"]
        assert "story 1 (the lead) ran" in json_calls[1]["prompt"]
        assert any("lead tier floor: retry brought the lead" in w
                   for w in rep.warnings)
        assert sentinel in rep.narrative_text
    finally:
        con.close()


def test_tier_floor_retry_message_carries_amended_steering_and_bills(
        tmp_paths, fake_seq, monkeypatch):
    """QA (NL-63 M2 fix loop): the REWRITTEN floor-retry message reaches the
    model carrying the amended steering — TARGET ~640, LONGEST-story primacy,
    rewrite-the-lead-ALONE — while keeping both long-pinned substrings (the
    TIER-EXPRESSION header and the 'story 1 (the lead) ran' opener) intact.
    And money honesty holds on a retry-bearing OK run: the attempt ledger
    bills all four API-reaching attempts exactly once each."""
    db.migrate()
    con = db.connect()
    try:
        slots = _stage_fakes(monkeypatch)
        persist_valid(con)  # slot-1 analysis brief exists -> the floor binds
        sentinel = "The lead now carries its full analytical weight."
        fake_seq.narratives = [stories_payload(slots),
                               payload_with_lead_words(slots, sentinel)]
        fake_seq.scripts = [compliant_script(slots)]
        rep = generate.run_generate(date=DATE, con=con, env=dict(ENV),
                                    refresh=True)
        retry_prompt = [c for c in fake_seq.calls if c["json_mode"]][1]["prompt"]
        assert "TIER-EXPRESSION VIOLATION" in retry_prompt   # pinned header kept
        assert "story 1 (the lead) ran" in retry_prompt      # pinned opener kept
        assert "TARGET ~640 words" in retry_prompt           # amended steering
        assert "LONGEST story of the day" in retry_prompt
        assert "Rewrite the lead ALONE" in retry_prompt
        assert "Keep every other story's tier and length" in retry_prompt
        assert [(e["step"], e["attempt"]) for e in rep.attempt_ledger] == [
            ("narrative", 1), ("narrative_retry", 1),
            ("editor", 1), ("script", 1)]
    finally:
        con.close()


def test_tier_floor_is_inert_without_a_lead_brief(
        tmp_paths, fake_seq, monkeypatch):
    """Thin days keep the material excuse: no slot-1 brief => a short lead
    draws NO retry and NO floor warning (the floor binds only when a valid
    lead analysis brief removed the excuse)."""
    db.migrate()
    con = db.connect()
    try:
        slots = _stage_fakes(monkeypatch)  # no persist_valid
        fake_seq.narratives = [stories_payload(slots)]
        fake_seq.scripts = [compliant_script(slots)]
        rep = generate.run_generate(date=DATE, con=con, env=dict(ENV),
                                    refresh=True)
        json_calls = [c for c in fake_seq.calls if c["json_mode"]]
        assert len(json_calls) == 2  # narrative + editor, no retry
        assert not any("TIER-EXPRESSION" in c["prompt"] for c in json_calls)
        assert not any("lead tier floor" in w for w in rep.warnings)
    finally:
        con.close()


def test_tier_floor_retry_skipped_when_it_would_breach_the_cap(
        tmp_paths, fake_seq, monkeypatch):
    """Spend-proof, narrative side: floor deficit + no cap headroom =>
    zero retry calls, shipped with disclosure."""
    real_est = generate._est_cost

    def starving_est(prompt, max_tokens, step="narrative"):
        # B2: step-aware passthrough (seat-priced estimates), same poison.
        if "TIER-EXPRESSION VIOLATION" in prompt:
            return 999.0
        return real_est(prompt, max_tokens, step)

    monkeypatch.setattr(generate, "_est_cost", starving_est)
    db.migrate()
    con = db.connect()
    try:
        slots = _stage_fakes(monkeypatch)
        persist_valid(con)
        fake_seq.narratives = [stories_payload(slots)]
        fake_seq.scripts = [compliant_script(slots)]
        rep = generate.run_generate(date=DATE, con=con, env=dict(ENV),
                                    refresh=True)
        json_calls = [c for c in fake_seq.calls if c["json_mode"]]
        assert len(json_calls) == 2  # narrative + editor; retry never attempted
        assert any("lead tier floor" in w and "retry skipped" in w
                   and "shipped with disclosure" in w for w in rep.warnings)
    finally:
        con.close()


def test_editor_guard_discards_an_edit_that_cuts_the_lead_below_floor(
        tmp_paths, fake_seq, monkeypatch):
    """M6's cut power gains a floor, not a new power: a floor-meeting draft
    edited below the floor is DISCARDED through the existing degrade path,
    disclosed, and the draft persists."""
    db.migrate()
    con = db.connect()
    try:
        slots = _stage_fakes(monkeypatch)
        persist_valid(con)
        sentinel = "The lead now carries its full analytical weight."
        draft = payload_with_lead_words(slots, sentinel)
        edit = copy.deepcopy(draft)
        edit["stories"][0]["lede"] = "Cut to nothing."  # same tier/labels
        fake_seq.narratives = [draft, edit]
        fake_seq.scripts = [compliant_script(slots)]
        rep = generate.run_generate(date=DATE, con=con, env=dict(ENV),
                                    refresh=True)
        assert any("editor cut the lead to" in w
                   and "tier floor" in w for w in rep.warnings)
        assert any("the edit was discarded" in w for w in rep.warnings)
        assert sentinel in rep.narrative_text
    finally:
        con.close()


# --- item 4: the TTS default flip ----------------------------------------------

def test_tts_default_is_openai_per_the_ear_test(tmp_paths):
    """Principal ear-test ruling 2026-07-06: gpt-4o-mini-tts default;
    kokoro remains a valid engine (the $0 fallback), never removed."""
    assert audio.DEFAULT_TTS_ENGINE == "openai"
    assert audio.OPENAI_TTS_MODEL == "gpt-4o-mini-tts"
    assert set(audio.VALID_TTS_ENGINES) == {"kokoro", "openai"}
    # settings absent -> openai; explicit kokoro pin still honored.
    assert config.load_sources().tts_engine == "openai"


# =============================================================================
# QA adversarial extension (2026-07-09) — the implementer's suggested seams
# plus the QA pass's own: exact boundaries, anchor-evasion variants pinned
# as actual, the improved-but-still-violating retry branch, real-spend cap
# exhaustion (vs. the estimate-poisoned variant above), rejected-vs-absent
# lead briefs, and the legitimate calibration fixture through the FULL
# run_generate pipeline. All offline, injected models, $0.
# =============================================================================

from newslens import analysis  # noqa: E402  (QA section import)


def test_cold_open_word_cap_boundary_exactly_60_in_61_out():
    """The ~50-words ruling landed as COLD_OPEN_MAX_WORDS=60 (handoff-line
    slack): exactly 60 words before the dateline is clean, 61 fires —
    pinned so a silent constant change surfaces here. The 3-vs-4 sentence
    edge is pinned in test_cold_open_cap_boundaries above."""
    body = ("\n\nBody follows in a longer paragraph with enough words to "
            "be a real section of spoken prose here today.")
    w60 = " ".join(["word"] * 59) + " end."   # one sentence, exactly 60 words
    w61 = " ".join(["word"] * 60) + " end."   # one sentence, 61 words
    assert generate.script_structural_check(
        w60 + " It's Monday, July 6, 2026." + body) == []
    out = generate.script_structural_check(
        w61 + " It's Monday, July 6, 2026." + body)
    assert any(v.startswith("cold open runs 1 sentences / 61 words")
               for v in out)


def test_cold_open_anchor_evasion_variants_pinned_as_actual():
    """RE-PINNED AS CAUGHT (implementer, P3.1 fix loop 2026-07-09). This
    was QA's pin-as-actual tripwire, built to fail on exactly this fix —
    its original docstring: "Fix contract if the gate ratifies it as a
    bug: normalize ’ -> ' before the anchor search, accept 'it is', and
    WARN when no dateline is found (the script contract mandates one).
    These pins are the tripwire: they FAIL when that fix lands, forcing
    the evasion class to be re-pinned as caught." That fix landed
    (generate._anchor_view + the tightened _DATELINE_RE + the run-level
    disclosure); the evasion class is now pinned as caught:
      (a) "It is Monday, July 6" phrasing ANCHORS — the cap fires;
      (b) curly U+2019 "It’s" is normalized to ASCII and ANCHORS;
      (c) no dateline at all: still no cold-open VIOLATION at the unit
          level (there is no boundary to measure — repetition still
          runs, per test_no_dateline_no_cold_open_check), but the
          exemption is no longer silent: the run-level "cold-open cap
          unenforceable" disclosure is pinned by
          test_LIVENESS_no_dateline_cold_open_cap_is_disclosed;
      (d) the FP quirk is closed: possessive "its Monday, July 6
          meeting" no longer false-anchors (the apostrophe is no longer
          optional), so mid-prose date mentions can't cap-check a cold
          open against the wrong anchor."""
    pre4 = "One. Two here now. Three here now. Four here now. "
    body = ("\n\nBody follows in a longer paragraph with enough words to "
            "be a real section of spoken prose here today.")
    # (a) "It is" phrasing: CAUGHT.
    assert any(v.startswith("cold open runs 4 sentences")
               for v in generate.script_structural_check(
                   pre4 + "It is Monday, July 6, 2026. Here's what matters."
                   + body))
    # (b) curly apostrophe: normalized, CAUGHT.
    assert any(v.startswith("cold open runs 4 sentences")
               for v in generate.script_structural_check(
                   pre4 + "It’s Monday, July 6, 2026. Here's what matters."
                   + body))
    # ASCII control: caught before the fix, caught after it.
    assert any(v.startswith("cold open runs 4 sentences")
               for v in generate.script_structural_check(
                   pre4 + "It's Monday, July 6, 2026. Here's what matters."
                   + body))
    # (c) no dateline, otherwise clean: no violation (nothing to measure);
    # the disclosure is run-level (see docstring), not a violations entry —
    # keep-green pins like test_repetition_needs_three_distinct_shared_six_
    # grams rely on [] here.
    assert generate.script_structural_check(
        "A clean short opening line." + body) == []
    # (d) possessive false-anchor: closed — no cold-open violation, and no
    # true dateline means this script draws the run-level disclosure.
    assert not any(v.startswith("cold open runs")
                   for v in generate.script_structural_check(
                       pre4 + "Because of its Monday, July 6 meeting the "
                       "group moved." + body))


def test_LIVENESS_no_dateline_cold_open_cap_is_disclosed(
        tmp_paths, fake_seq, monkeypatch):
    """The BUG17-rule red for the anchor fix's WARN surface (QA fix
    contract in test_cold_open_anchor_evasion_variants_pinned_as_actual:
    "WARN when no dateline is found (the script contract mandates
    one)"): a SHIPPED script with no detectable dateline draws the
    run-level "cold-open cap unenforceable: no dateline anchor found"
    disclosure — the hard cap is never silently exempted. It is
    disclosure, NOT retry material: exactly one script call.
    compliant_script carries no dateline, so the plain green path
    exercises this seam; remove the run_generate wiring and this fails
    (verified red against the pre-fix tree)."""
    db.migrate()
    con = db.connect()
    try:
        slots = _stage_fakes(monkeypatch)
        fake_seq.narratives = [stories_payload(slots)]
        fake_seq.scripts = [compliant_script(slots)]
        rep = generate.run_generate(date=DATE, con=con, env=dict(ENV),
                                    refresh=True)
        script_calls = [c for c in fake_seq.calls if not c["json_mode"]]
        assert len(script_calls) == 1  # a warn, never a retry
        assert any("cold-open cap unenforceable: no dateline anchor found"
                   in w for w in rep.warnings)
    finally:
        con.close()


def test_structural_retry_improved_but_still_violating_ships_with_disclosure(
        tmp_paths, fake_seq, monkeypatch):
    """The third retry outcome (generate.py :1698 branch — the one the
    implementer's tests left uncovered): the retry fixes the cold open
    but keeps a repetition pair. Fewer violations => the RETRY ships,
    and the REMAINING violation text travels into report.warnings
    (the implementer's 'across the retry boundary' seam) — never a
    second retry."""
    db.migrate()
    con = db.connect()
    try:
        slots = _stage_fakes(monkeypatch)
        para_a = ("In the first section, " + REUSED_RUN
                  + ", officials said after the talks concluded.")
        para_b = ("Later in the hour, " + REUSED_RUN
                  + ", a point the communique repeated in closing.")
        still_repetitive = (compliant_script(slots) + "\n\n" + para_a
                            + "\n\n" + para_b)  # repetition only, no cold open
        fake_seq.narratives = [stories_payload(slots)]
        fake_seq.scripts = [violating_script(slots), still_repetitive]
        rep = generate.run_generate(date=DATE, con=con, env=dict(ENV),
                                    refresh=True)
        script_calls = [c for c in fake_seq.calls if not c["json_mode"]]
        assert len(script_calls) == 2  # improvement never buys a third call
        remain = [w for w in rep.warnings
                  if "violation(s) REMAIN" in w and "shipped with disclosure" in w]
        assert len(remain) == 1
        assert "retell the same material" in remain[0]  # violations carried
        row = con.execute("SELECT script_text FROM briefings WHERE date=?",
                          (DATE,)).fetchone()
        assert "The cartel decided to lift output" not in row["script_text"]
        assert "Good morning. Here is your briefing." in row["script_text"]
    finally:
        con.close()


def test_structural_retry_skipped_when_real_spend_already_ate_the_cap(
        tmp_paths, fake_seq, monkeypatch):
    """GREEN since the BUG21 fix (implementer, P3.1 fix loop 2026-07-09) —
    was KNOWN-RED (BUG21, QA 2026-07-09), kept red as the fix's
    acceptance criterion (BUG18 precedent). The fix is the docstring's
    own contract, landed verbatim: `spent += step_s["usd"]` immediately
    after the script call_llm, before the structural-retry pre-check.
    QA's forensic record of the pre-fix behavior follows, unedited.

    Spend-proof, the OTHER exhaustion path: the estimate-poisoned test
    above proves the pre-check reads the ESTIMATE; this one proves it must
    read accumulated REAL spend at retry time. Honest small estimates
    (0.05 each), heavy actual step costs (0.50 each): analysis 0.021 +
    narrative 0.50 + editor 0.50 + script 0.50 = 1.521 spent under a 1.55
    cap; the structural retry's pre-check should land at ~1.57 > cap and
    SKIP — zero retry calls, violations stand in warnings, first attempt
    persists.

    PRE-FIX it retried: the script step's cost NEVER entered `spent` — every
    other LLM step has a `spent +=` site (generate.py :1358 analysis,
    :1420 narrative, :1457 narrative-retry, :1525 editor, :1691
    script-retry) but step_s is only appended to report.steps at :1725,
    AFTER the structural block, with no accumulation at all. The
    narrative twin counts its own step BEFORE the floor-retry decision
    (:1420 precedes :1427) — the asymmetry marks this an oversight, not a
    design choice. Effect: the retry decision under-counts true spend by
    one script call, so a run can overshoot the cap by up to one retry
    when headroom is thinner than the script step cost. Severity: LOW
    (bounded, cents at real prices) but it breaks the stated invariant
    'retry cost pre-checked vs cap'. Fix contract: `spent += step-cost of
    usage_s` immediately after the script call_llm (before :1665);
    everything this file pins must stay green and THIS test flips."""
    # B2 seam shift, stub follows the live path: every spend-accumulation site
    # now reads _step_ledger (seat-sourced via llm.cost_fields) — _step_cost is
    # no longer on the accumulation path, so patching it would steer nothing
    # (a dead stub silently un-testing the invariant). The heavy 0.50 rides the
    # ledger's usd key; shape kept honest with the shadow/lane fields.
    monkeypatch.setattr(generate, "_est_cost",
                        lambda p, m, step="narrative": 0.05)
    monkeypatch.setattr(
        generate, "_step_ledger",
        lambda step, usage: {"model": "stub-model", "lane": "api", "usd": 0.50,
                             "usd_shadow": 0.50, "usd_charged": 0.50,
                             "cache_read_tokens": 0,
                             "cache_creation_tokens": 0})
    db.migrate()
    con = db.connect()
    try:
        slots = _stage_fakes(monkeypatch)
        fake_seq.narratives = [stories_payload(slots)]
        fake_seq.scripts = [violating_script(slots)]
        env = dict(ENV, BUDGET_CAP_USD_PER_RUN="1.55")
        rep = generate.run_generate(date=DATE, con=con, env=env,
                                    refresh=True)
        script_calls = [c for c in fake_seq.calls if not c["json_mode"]]
        assert len(script_calls) == 1  # retry never attempted
        assert not any(s["step"] == "script_retry" for s in rep.steps)
        assert any("retry skipped" in w and "exceed the cap" in w
                   and "cold open runs 4 sentences" in w
                   for w in rep.warnings)
        row = con.execute("SELECT script_text FROM briefings WHERE date=?",
                          (DATE,)).fetchone()
        assert "The cartel decided to lift output" in row["script_text"]
    finally:
        con.close()


def test_tier_floor_inert_with_a_rejected_slot1_brief(
        tmp_paths, fake_seq, monkeypatch):
    """Rejected is not valid: a slot-1 brief row with status='rejected'
    (and no valid row) leaves the floor INERT exactly like absence —
    latest_valid_brief filters on status, the material excuse stands,
    and the deep view reads absent."""
    db.migrate()
    con = db.connect()
    try:
        slots = _stage_fakes(monkeypatch)
        analysis.persist_brief(con, DATE, 1, "full", "rejected", None, "",
                               0.01, {"manifest": {}})
        fake_seq.narratives = [stories_payload(slots)]  # short lead
        fake_seq.scripts = [compliant_script(slots)]
        rep = generate.run_generate(date=DATE, con=con, env=dict(ENV),
                                    refresh=True)
        json_calls = [c for c in fake_seq.calls if c["json_mode"]]
        assert len(json_calls) == 2  # narrative + editor, no retry
        assert not any("TIER-EXPRESSION" in c["prompt"] for c in json_calls)
        assert not any("lead tier floor" in w for w in rep.warnings)
        assert rep.deep_views["1"] == "absent"
    finally:
        con.close()


def test_tier_floor_still_binds_when_a_newer_rejection_follows_a_valid_brief(
        tmp_paths, fake_seq, monkeypatch):
    """The adversarial flip of the rejected case: a valid brief followed
    by a NEWER rejected row (a failed regeneration) does NOT lift the
    floor — latest_valid_brief serves the latest VALID row, the writer
    still receives that brief, so the thin-material excuse stays removed
    and the short lead draws its retry. (Deliberately different from
    analyst_slot3_tier's newest-row-wins: that derives a VERDICT; this
    asks whether usable brief material exists.)"""
    db.migrate()
    con = db.connect()
    try:
        slots = _stage_fakes(monkeypatch)
        persist_valid(con)                                   # valid, older
        analysis.persist_brief(con, DATE, 1, "full", "rejected", None, "",
                               0.01, {"manifest": {}})       # rejected, newer
        sentinel = "The lead now carries its full analytical weight."
        fake_seq.narratives = [stories_payload(slots),
                               payload_with_lead_words(slots, sentinel)]
        fake_seq.scripts = [compliant_script(slots)]
        rep = generate.run_generate(date=DATE, con=con, env=dict(ENV),
                                    refresh=True)
        json_calls = [c for c in fake_seq.calls if c["json_mode"]]
        assert len(json_calls) == 3  # narrative + floor retry + editor
        assert "TIER-EXPRESSION VIOLATION" in json_calls[1]["prompt"]
        assert sentinel in rep.narrative_text
        assert rep.deep_views["1"] == "available"
    finally:
        con.close()


def test_legitimate_fixture_survives_the_full_pipeline(
        tmp_paths, fake_seq, monkeypatch):
    """Calibration closed at RUN level: the legitimately-edited 2026-07-06
    script goes through run_generate whole — validate_script (hard checks,
    furniture, warn channels) AND the structural gate together. One script
    call (no retry), zero structural warnings, and the fixture text
    persists. The numeral-subset warn (fixture numerals vs the synthetic
    narrative) doubles as proof validate_script really ran on this text
    while the structural gate stayed quiet."""
    db.migrate()
    con = db.connect()
    try:
        slots = _stage_fakes(monkeypatch)
        text = (FIXTURES / "2026-07-06-legitimate.txt").read_text(
            encoding="utf-8")
        fake_seq.narratives = [stories_payload(slots)]
        fake_seq.scripts = [text]
        rep = generate.run_generate(date=DATE, con=con, env=dict(ENV),
                                    refresh=True)
        script_calls = [c for c in fake_seq.calls if not c["json_mode"]]
        assert len(script_calls) == 1  # the gate never fired
        assert not any("STRUCTURAL" in w or "structural retry" in w
                       for w in rep.warnings)
        # Anchor-fix flip side (2026-07-09): a script WITH a dateline must
        # never draw the "cap unenforceable" disclosure — pins that the
        # warn is conditional, not ambient noise.
        assert not any("cold-open cap unenforceable" in w
                       for w in rep.warnings)
        assert any("script numerals absent from narrative" in w
                   for w in rep.warnings)  # validate_script ran on it
        row = con.execute("SELECT script_text FROM briefings WHERE date=?",
                          (DATE,)).fetchone()
        assert "What I'm watching this week" in row["script_text"]
        assert generate.SIGNOFF in row["script_text"]
    finally:
        con.close()
