"""Live-contact fix loop #3 — QA extensions (QA-written, 2026-07-14; extends,
never replaces, the implementer's tests/test_script_floor_retry.py).

What this file adds over the implementer's nine:

  HAMMER 1 — built-prompt rendering across coverage k=1/2/5: the bookend's
  conditional sentence stays ATTACHED to its condition at every k, the
  carve-out survives .format, no k renders a false floor claim, and the
  number the prompt promises is byte-derived from the SAME constant the code
  enforces (with the thin-day 369/501 relaxation deliberately UNnamed in the
  prompt — the pending flat-vs-scaled ruling is neither widened nor narrowed).

  HAMMER 2 — the informed retry through the REAL pipeline validators, not
  synthetic ones: the exact ValueError text of _shape_check/_editor_shape/
  _validate_script lands in attempt 2's POST for all three validate-bearing
  steps, uniformly, in one run — including a byte-exact replay of today's
  live paid failure (thin script -> corrected retry -> ships).

  HAMMER 3 — correction-text discipline beyond the implementer's pin: the
  full binding clause is pinned verbatim, the composed block survives error
  text containing braces/quotes (concatenation, never .format), and the
  KeyError-envelope class is pinned AS BUILT (uniform with the rank
  precedent: str(KeyError) is an odd correction but a corrected retry all
  the same).

  HAMMER 4 — interplay with the SEPARATE outer retry mechanisms (narrative
  lead-floor retry ~generate.py:1731, script structural retry ~:2019): when
  both an outer retry and call_llm's informed retry fire in ONE run, each
  correction lands exactly once in its own scope, outer retry bases stay
  pristine (no leaked CORRECTION block), and nothing compounds.

  HAMMER 5 — the fact-subset rule: the bookend's depth remedy points INTO
  the narrative's own material ("already in the narrative below") and the
  paragraph carries no fact-licensing language; the FACT-SUBSET RULE block
  still precedes and binds.

  Plus the third-attempt anchor pin: the 2-attempt loop cannot exhibit a
  third send, so the non-compounding guarantee for a grown loop is pinned at
  the construction (correction anchored to `prompt`, never `next_prompt`) —
  a whitespace-normalized source assertion, the honest limit of what is
  testable here.

Fully offline: generate._chat is monkeypatched at the module seam; autouse
sandbox fixtures redirect DATA_DIR/DB_PATH; the loopback guard would refuse
any real socket. No source files are modified by this file.
"""

from __future__ import annotations

import inspect
import json
import time

import pytest

from newslens import db, generate, paths
from test_generate import (
    A_DAY, ENV, _digest_script, _inputs_for, seed_briefing, slot,
    stories_payload,
)
from test_m3_qa import persist_valid

PREFIX = generate.RETRY_CORRECTION_PREFIX
SUFFIX = generate.RETRY_CORRECTION_SUFFIX


# =========================================================================
# HAMMER 1 — built-prompt rendering across k
# =========================================================================

def _built(n_slots):
    return generate.build_script_prompt(
        A_DAY, "A", "The narrative body.",
        _inputs_for([slot(i) for i in range(1, n_slots + 1)]))


def test_bookend_condition_claim_and_carveout_render_at_every_k():
    """k=1 (single-story), k=2 (two-story), k=5 (7-slot edition, the live
    failure's shape): the NOT-VIABLE claim renders with its 'On a normal
    edition' condition fused to it as ONE sentence — so no k reads an
    unconditional 600 floor — and the thin-edition carve-out is present in
    the SAME rendered prompt. No template residue survives formatting."""
    claim = ("On a normal edition (the lead plus two or more supporting "
             "stories) a script that comes in under ~600 words is NOT VIABLE")
    carve = ("A genuine single- or two-story edition legitimately runs "
             "shorter than that floor, and that is correct — the floor "
             "scales down with coverage.")
    remedy = ("work its three movements and its receipts — the specific "
              "figures, named actors, and mechanism already in the "
              "narrative below")
    for n_slots in (1, 2, 7):
        built = _built(n_slots)
        norm = " ".join(built.split())
        assert claim in norm, f"conditional claim broken at n_slots={n_slots}"
        assert carve in norm, f"carve-out missing at n_slots={n_slots}"
        assert remedy in norm, f"depth remedy missing at n_slots={n_slots}"
        assert "band_low" not in built          # no unrendered placeholder
        assert "{" not in built.split("=== LABEL DATA")[0].replace(
            "{band_low}", "")                   # nothing else unformatted


def test_prompt_promises_only_the_number_the_code_enforces():
    """Decision (1) held, both directions: the prompt's stated floor is
    rendered from SCRIPT_MIN_VIABLE_WORDS (the k>=3 run-time floor, exactly),
    so prompt and enforcement cannot drift apart silently; and the prompt
    never names the thin-day 369/501 relaxations — the pending flat-vs-scaled
    ruling stays open, qualitative carve-out only. The code side of that
    table is pinned in test_generate.test_viability_floor_derivation_table_
    pinned_AS_BUILT; here we pin the prompt side against the same constant."""
    assert generate.SCRIPT_MIN_VIABLE_WORDS == 600
    # k>=3 enforcement is the SAME constant the prompt renders
    for k in (3, 4, 5):
        ceiling = generate._script_budgets(k)[0]
        assert min(generate.SCRIPT_MIN_VIABLE_WORDS,
                   int(ceiling * 0.66)) == generate.SCRIPT_MIN_VIABLE_WORDS
    for n_slots in (1, 2, 3, 7):
        built = _built(n_slots)
        assert f"under ~{generate.SCRIPT_MIN_VIABLE_WORDS} words" in built
        # thin-day relaxed values stay out of the model's contract text
        assert "369" not in built
        assert "501" not in built
        # and the code never enforces harsher than the prompt states
        k = generate._script_coverage(n_slots)
        floor = min(generate.SCRIPT_MIN_VIABLE_WORDS,
                    int(generate._script_budgets(n_slots)[0] * 0.66))
        assert floor <= generate.SCRIPT_MIN_VIABLE_WORDS, (n_slots, k, floor)


# =========================================================================
# HAMMER 5 — fact-subset discipline of the new paragraph
# =========================================================================

def test_bookend_remedy_licenses_no_new_facts():
    """The one remedy is depth INTO the narrative's existing material. The
    paragraph must anchor its receipts to 'already in the narrative below'
    and carry zero fact-licensing language; the FACT-SUBSET RULE block still
    appears BEFORE it and intact, template and built prompt both."""
    raw = (paths.PROMPTS_DIR / generate.PROMPT_SCRIPT).read_text(
        encoding="utf-8")
    start = raw.index("THE LOWER EDGE IS A HARD BOOKEND")
    end = raw.index("STRUCTURE, in order:")
    para = " ".join(raw[start:end].split())
    # positive anchors: depth remedy points INTO the narrative's material
    assert "already in the narrative below" in para
    assert "The remedy is never to pad" in para
    assert "never to stretch the supporting stories" in para
    # licensing language absent from the paragraph (lowercased scan)
    lowered = para.lower()
    for licensing in (
        "new fact", "new information", "additional fact", "add detail",
        "add context", "add color", "add background", "bring in",
        "outside the narrative", "beyond the narrative", "own knowledge",
        "general knowledge", "background knowledge", "research", "look up",
        "recall", "invent", "draw on", "supplement",
    ):
        assert licensing not in lowered, f"licensing phrase: {licensing!r}"
    # the fact-subset rule precedes the bookend, verbatim and binding
    assert raw.index("THE FACT-SUBSET RULE (binding)") < start
    assert ("The script introduces no factual claim\nabsent from the "
            "narrative text" in raw
            or "introduces no factual claim absent from the narrative text"
            in " ".join(raw.split()))
    built = _built(7)
    bnorm = " ".join(built.split())
    assert "introduces no factual claim absent from the narrative text" in bnorm
    assert bnorm.index("THE FACT-SUBSET RULE") < bnorm.index(
        "THE LOWER EDGE IS A HARD BOOKEND")


# =========================================================================
# Pipeline fixture — scripted _chat with prompt recording
# =========================================================================

@pytest.fixture
def rec_chat(monkeypatch):
    """Strictly-ordered scripted fake for generate._chat: each reply is a
    content string (finish stop), a (content, finish_reason) tuple, or an
    exception instance to raise. Records every request's prompt/json_mode.
    Tests assert the queue drains — exact call-count proof."""
    state = type("S", (), {})()
    state.replies = []
    state.calls = []

    def chat(key, prompt, max_tokens, temperature, json_mode):
        state.calls.append({"json_mode": json_mode, "prompt": prompt,
                            "temperature": temperature})
        assert state.replies, "rec_chat: more API calls than scripted replies"
        step = state.replies.pop(0)
        if isinstance(step, BaseException):
            raise step
        content, finish = step if isinstance(step, tuple) else (step, "stop")
        return {"choices": [{"finish_reason": finish,
                             "message": {"content": content}}],
                "usage": {"prompt_tokens": 900, "completion_tokens": 200}}

    monkeypatch.setattr(generate, "_chat", chat)
    monkeypatch.setattr(time, "sleep", lambda s: None)
    return state


def _con():
    db.migrate()
    return db.connect()


def _ledger(rep):
    return [(e["step"], e["attempt"]) for e in rep.attempt_ledger]


K3_FLOOR_ERR = ("script not viable: 550 words — under the 600-word floor "
                "for a 3-story digest (disclosures and truncation checked "
                "and clear; this is empty/degenerate output, not a "
                "legitimately short episode)")


# =========================================================================
# HAMMER 2 — informed retry through the REAL validators, in-pipeline
# =========================================================================

def test_pipeline_replay_of_the_live_paid_failure_now_recovers(rec_chat):
    """The acceptance contract of this fix loop, replayed end-to-end: a
    3-story run whose script attempt 1 lands at 550 words (the 565-word
    live shape) gets a retry whose POSTed prompt is byte-exactly the
    ORIGINAL script prompt + one correction block quoting _validate_script's
    OWN failure text — and the corrected 620-word attempt ships. Both
    billed script attempts are on the money ledger. Before this fix the
    retry bytes were identical to attempt 1 and the run died."""
    con = _con()
    try:
        slots = [slot(1), slot(2), slot(3)]
        seed_briefing(con, A_DAY, slots)
        rec_chat.replies = [
            json.dumps(stories_payload(slots)),      # narrative, clean
            json.dumps(stories_payload(slots)),      # editor echo, clean
            _digest_script(slots, 550),              # script attempt 1: thin
            _digest_script(slots, 620),              # corrected attempt: ships
        ]
        rep = generate.run_generate(date=A_DAY, con=con, env=dict(ENV),
                                    refresh=False)
        assert rep.script_words == 620
        s_calls = [c for c in rec_chat.calls if not c["json_mode"]]
        assert len(s_calls) == 2
        assert "HARD BOOKEND" in s_calls[0]["prompt"]   # part 1 reaches attempt 1
        assert PREFIX not in s_calls[0]["prompt"]
        assert s_calls[1]["prompt"] == (
            s_calls[0]["prompt"] + "\n\n" + PREFIX + K3_FLOOR_ERR + SUFFIX)
        assert s_calls[1]["prompt"].count(PREFIX) == 1
        # no cross-step contamination
        for c in rec_chat.calls:
            if c["json_mode"]:
                assert PREFIX not in c["prompt"]
        assert _ledger(rep) == [("narrative", 1), ("editor", 1),
                                ("script", 1), ("script", 2)]
        assert not rec_chat.replies
    finally:
        con.close()


def test_pipeline_narrative_and_editor_validators_get_the_same_correction(
        rec_chat):
    """Uniformity across the validate-bearing steps, one run: a malformed
    narrative attempt and a malformed editor attempt EACH draw a corrected
    retry quoting their validator's exact text; each step's attempt 1 is
    pristine (the previous step's correction never leaks forward); the
    script step then starts pristine too."""
    con = _con()
    try:
        slots = [slot(1), slot(2), slot(3)]
        seed_briefing(con, A_DAY, slots)
        try:
            json.loads("this is not json")
        except ValueError as e:
            json_err = str(e)
        good = json.dumps(stories_payload(slots))
        rec_chat.replies = [
            "this is not json",                      # narrative 1: malformed
            good,                                    # narrative 2: recovers
            "this is not json",                      # editor 1: malformed
            good,                                    # editor 2: recovers (echo)
            _digest_script(slots, 620),              # script: clean first draw
        ]
        rep = generate.run_generate(date=A_DAY, con=con, env=dict(ENV),
                                    refresh=False)
        j = [c["prompt"] for c in rec_chat.calls if c["json_mode"]]
        s = [c["prompt"] for c in rec_chat.calls if not c["json_mode"]]
        assert len(j) == 4 and len(s) == 1
        assert PREFIX not in j[0]
        assert j[1] == j[0] + "\n\n" + PREFIX + json_err + SUFFIX
        # the editor's base prompt is its own, pristine — not the corrected
        # narrative prompt, not carrying any correction block
        assert j[2] != j[1] and PREFIX not in j[2]
        assert j[3] == j[2] + "\n\n" + PREFIX + json_err + SUFFIX
        assert j[3].count(PREFIX) == 1
        assert PREFIX not in s[0]
        assert _ledger(rep) == [("narrative", 1), ("narrative", 2),
                                ("editor", 1), ("editor", 2), ("script", 1)]
        assert not rec_chat.replies
    finally:
        con.close()


# =========================================================================
# HAMMER 4 — interplay with the SEPARATE outer retry mechanisms
# =========================================================================

def test_lead_floor_retry_and_informed_retry_compose_without_doubling(
        rec_chat):
    """Both mechanisms in one narrative pass: attempt 1 is valid-shaped but
    its briefed lead is under LEAD_FLOOR_WORDS -> the OUTER floor retry
    fires a fresh call_llm whose base is n_prompt + the TIER-EXPRESSION
    block, pristine of any CORRECTION block. Inside THAT call, attempt 1
    returns malformed JSON -> the informed retry appends exactly ONE
    correction to exactly THAT base. One TIER header, one CORRECTION block,
    nothing compounds, and the floor-cleared payload ships."""
    con = _con()
    try:
        slots = [slot(1), slot(2), slot(3)]
        seed_briefing(con, A_DAY, slots)
        persist_valid(con, date=A_DAY)   # slot-1 brief -> the floor binds
        try:
            json.loads("still not json")
        except ValueError as e:
            json_err = str(e)
        import copy
        long_lead = copy.deepcopy(stories_payload(slots))
        filler = ("The analysis continues with sourced detail and measured "
                  "context. ")
        long_lead["stories"][0]["lede"] += " " + filler * 60
        rec_chat.replies = [
            json.dumps(stories_payload(slots)),   # narrative 1: short lead
            "still not json",                     # floor retry, attempt 1: bad
            json.dumps(long_lead),                # floor retry, attempt 2: ok
            json.dumps(long_lead),                # editor echo
            _digest_script(slots, 620),           # script clean
        ]
        rep = generate.run_generate(date=A_DAY, con=con, env=dict(ENV),
                                    refresh=False)
        j = [c["prompt"] for c in rec_chat.calls if c["json_mode"]]
        assert len(j) == 4
        assert PREFIX not in j[0] and "TIER-EXPRESSION" not in j[0]
        # outer retry base: original narrative prompt + TIER block, no leak
        assert j[1].startswith(j[0])
        assert "TIER-EXPRESSION VIOLATION" in j[1]
        assert PREFIX not in j[1]
        # informed retry anchors to THAT base: one correction, one TIER header
        assert j[2] == j[1] + "\n\n" + PREFIX + json_err + SUFFIX
        assert j[2].count(PREFIX) == 1
        assert j[2].count("TIER-EXPRESSION VIOLATION") == 1
        # editor starts pristine of both mechanisms' blocks
        assert PREFIX not in j[3] and "TIER-EXPRESSION" not in j[3]
        assert any("lead tier floor: retry brought the lead" in w
                   for w in rep.warnings)
        assert _ledger(rep) == [
            ("narrative", 1), ("narrative_retry", 1), ("narrative_retry", 2),
            ("editor", 1), ("script", 1)]
        assert not rec_chat.replies
    finally:
        con.close()


COLD4_A_DAY = (
    "The cartel decided to lift output again this cycle. "
    "Prices had been sliding for weeks before the decision. "
    "Ministers met over the weekend to settle on the size. "
    "Some members pressed for a larger step than agreed. "
    "It's Sunday, July 5. Here's what matters today.")


def _viable_but_cold_open_violating(slots, total_words):
    """Clears _validate_script (>=600 words, disclosures clean, stop-finish)
    while tripping EXACTLY the structural cold-open cap (4 sentences before
    the dateline). Single big filler paragraph -> no retell pair."""
    parts = [COLD4_A_DAY]
    for s in slots:
        parts.append(f"Story {s['slot']}. The development moved today in "
                     "ways that matter.")
    parts.append(generate.SIGNOFF)
    need = total_words - generate.wc("\n\n".join(parts))
    assert need > 0
    parts.insert(1, " ".join(["substance"] * need))
    text = "\n\n".join(parts)
    assert generate.wc(text) == total_words
    return text


def test_structural_retry_base_stays_pristine_after_informed_recovery(
        rec_chat):
    """Script-side interplay: attempt 1 fails the viability floor (informed
    retry fires), the corrected attempt 2 is viable but structurally bad
    (4-sentence cold open) -> the OUTER structural retry fires, and its base
    prompt is the PRISTINE script prompt + the STRUCTURAL block — the
    CORRECTION block from the inner recovery must NOT leak into it. The
    structural retry's clean draw ships with the violations-cleared
    disclosure."""
    con = _con()
    try:
        slots = [slot(1), slot(2), slot(3)]
        seed_briefing(con, A_DAY, slots)
        rec_chat.replies = [
            json.dumps(stories_payload(slots)),            # narrative
            json.dumps(stories_payload(slots)),            # editor echo
            _digest_script(slots, 550),                    # script 1: thin
            _viable_but_cold_open_violating(slots, 700),   # script 2: viable, bad open
            _digest_script(slots, 620),                    # structural retry: clean
        ]
        rep = generate.run_generate(date=A_DAY, con=con, env=dict(ENV),
                                    refresh=False)
        s = [c["prompt"] for c in rec_chat.calls if not c["json_mode"]]
        assert len(s) == 3
        assert PREFIX not in s[0]
        assert s[1] == s[0] + "\n\n" + PREFIX + K3_FLOOR_ERR + SUFFIX
        # the structural retry rebuilds from the pristine s_prompt
        assert s[2].startswith(s[0])
        assert "STRUCTURAL VIOLATIONS" in s[2]
        assert "cold open runs" in s[2]
        assert PREFIX not in s[2]                    # no inner-retry leak
        assert rep.script_words == 620
        assert any("script structural retry: violations cleared" in w
                   for w in rep.warnings)
        assert _ledger(rep) == [("narrative", 1), ("editor", 1),
                                ("script", 1), ("script", 2),
                                ("script_retry", 1)]
        assert not rec_chat.replies
    finally:
        con.close()


# =========================================================================
# HAMMER 3 — correction-text discipline, extended
# =========================================================================

def test_correction_full_binding_clause_and_hostile_error_text():
    """Beyond the implementer's phrase pins: the SUFFIX's binding clause is
    pinned in full, and the composed block is CONCATENATION — error text
    containing braces/quotes/percent lands verbatim (a future 'cleanup' to
    str.format or %-interpolation would crash or mangle here)."""
    assert PREFIX == "CORRECTION — your previous draft was rejected: "
    assert "Fix exactly that failure and nothing else" in SUFFIX
    assert "every other contract rule above still binds" in SUFFIX
    assert "Return only the corrected output" in SUFFIX

    hostile = 'draft must be {"valid": "json"} — got 100% "garbage" {x}'

    def reject_once():
        state = {"n": 0}

        def v(content):
            state["n"] += 1
            if state["n"] == 1:
                raise ValueError(hostile)
        return v

    sent = []

    def chat(key, prompt, max_tokens, temperature, json_mode):
        sent.append(prompt)
        return {"choices": [{"finish_reason": "stop",
                             "message": {"content": "ok"}}],
                "usage": {}}

    real_chat, real_sleep = generate._chat, time.sleep
    generate._chat, time.sleep = chat, lambda s: None
    try:
        content, _ = generate.call_llm("sk-x", "BASE", "script", 100, 0.4,
                                       False, validate=reject_once())
    finally:
        generate._chat, time.sleep = real_chat, real_sleep
    assert content == "ok"
    assert sent[1] == "BASE" + "\n\n" + PREFIX + hostile + SUFFIX
    assert hostile in sent[1]                       # verbatim, unmangled


def test_envelope_keyerror_class_gets_informed_retry_as_built(monkeypatch):
    """AS-BUILT PIN (uniform with the rank precedent's malformed class): a
    response envelope missing 'choices' raises KeyError, which the declared
    (ValueError/KeyError/IndexError/TypeError) scope routes through the
    CORRECTED retry — str(KeyError) makes an odd correction ("'choices'")
    but the retry is informed, not blind, and recovery works. If the gate
    ever re-scopes envelope malformation to transport (original bytes),
    flip this pin consciously."""
    sent = []
    replies = [
        {"usage": {"prompt_tokens": 1, "completion_tokens": 1}},  # no choices
        {"choices": [{"finish_reason": "stop",
                      "message": {"content": "ok"}}], "usage": {}},
    ]

    def chat(key, prompt, max_tokens, temperature, json_mode):
        sent.append(prompt)
        return replies.pop(0)

    monkeypatch.setattr(generate, "_chat", chat)
    monkeypatch.setattr(time, "sleep", lambda s: None)
    content, _ = generate.call_llm("sk-x", "BASE", "script", 100, 0.4, False)
    assert content == "ok"
    assert sent[0] == "BASE"
    assert sent[1] == "BASE" + "\n\n" + PREFIX + "'choices'" + SUFFIX


# =========================================================================
# The third-attempt anchor, pinned at the construction
# =========================================================================

def test_correction_anchor_is_the_original_prompt_by_construction():
    """The loop is hard-capped at two attempts, so a third send cannot be
    exhibited behaviorally; what CAN be pinned is the construction that
    makes a grown loop safe: the correction is anchored to `prompt` (the
    call's immutable argument), and `next_prompt` is never itself a
    concatenation base. Whitespace-normalized source assertion — goes red
    if anyone rewrites the anchor to compound."""
    norm = " ".join(inspect.getsource(generate.call_llm).split())
    assert ('next_prompt = ( prompt + "\\n\\n" + RETRY_CORRECTION_PREFIX '
            '+ str(exc) + RETRY_CORRECTION_SUFFIX )') in norm
    assert "next_prompt +" not in norm          # never compounds off itself
    assert norm.count("next_prompt =") == 2     # init + the one reassignment
