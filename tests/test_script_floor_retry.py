"""Live-contact fix loop #3 — script length language + informed retry
(Implementer, 2026-07-14; PART 1 consciously FLIPPED same day by DECISIONS
'podcast floor REMOVED' — the floor-removal batch).

Two contracts, red-first:

  PART 1 — length language in prompts/script_adapt.txt. AS FIRST LANDED the
  lower edge was a named HARD bookend with a depth remedy; the principal's
  same-day floor-REMOVED ruling retired the bookend, and the pin flipped with
  it (see test_script_prompt_is_ceiling_only_with_no_floor_language's WAS/NOW):
  the prompt is now CEILING-ONLY — no floor claim of any kind, 'no minimum'
  stated, lead-depth steering surviving as pure quality guidance. Pinned like
  the text-pin precedent in test_rank_retry_qa.py::test_correction_text_only_
  tightens_compliance so a silent wordsmith goes red instead of drifting.

  PART 2 — informed validation retry in generate.call_llm. call_llm's ONE
  retry was BLIND (identical prompt bytes, temp the only variance). Now the
  validation/truncation-class retry carries a correction block quoting the exact
  ValueError text, so attempt 2 is steered at the rule that failed. Scoped
  EXACTLY like the rank-side fix (ranking.RETRY_CORRECTION / commit 3b40d6a):
  correction on the (ValueError/KeyError/IndexError/TypeError) malformed-or-
  validation class only; transport retries (429/5xx/timeout/connection) re-send
  the ORIGINAL prompt byte-for-byte; anchored to the ORIGINAL prompt (never
  compounding); never leaks across calls. call_llm is shared by the
  narrative/editor/script steps, so the correction is dynamic (echoes the
  validator's own message) rather than a fixed constant the way rank's single
  id-fabrication text is — and it applies to all three validate-bearing steps
  uniformly. The narrative-floor retry at its own call site (generate.py ~1690)
  is a SEPARATE mechanism and is not touched here.

  CLASSIFICATION PIN (conscious, gate-visible, mirrors the rank pin): a
  finish_reason="length" truncation raises ValueError inside the try, so it
  lands in the malformed/validation handler and its retry DOES carry the
  correction. If the gate rules truncation must re-send original bytes, flip
  test_truncation_retry_carries_the_correction_as_built — a conscious flip.

Fully offline: generate._chat is monkeypatched at the module seam; the autouse
sandbox fixtures redirect DATA_DIR/DB_PATH so nothing touches real state.
"""

from __future__ import annotations

import time
import urllib.error

import pytest

from newslens import generate, paths
from test_generate import A_DAY, slot, _inputs_for


# =========================================================================
# PART 1 — length-language liveness (ceiling-only since floor REMOVED 07-14)
# =========================================================================

def test_script_prompt_is_ceiling_only_with_no_floor_language():
    """CONSCIOUS FLIP (was test_script_prompt_floor_bookend_and_remedy_language;
    DECISIONS 2026-07-14 'podcast floor REMOVED'). WAS: the lower edge pinned as
    a NAMED HARD bookend with the depth remedy + thin-day carve-out. NOW: the
    prompt is ceiling-only — the bookend paragraph is GONE, no floor claim of
    any kind survives, 'no minimum' is stated, and the lead-depth steering
    (three movements, receipts) lives on as pure quality guidance in the
    center-of-gravity sentence. Compression directives run unopposed."""
    raw = (paths.PROMPTS_DIR / generate.PROMPT_SCRIPT).read_text(encoding="utf-8")
    norm = " ".join(raw.split())

    # the floor is GONE — no bookend, no viability claim, no carve-out
    assert "HARD BOOKEND" not in raw
    assert "not viable" not in norm.lower()
    assert "band_low" not in raw and "minutes_low" not in raw
    assert "scales down with coverage" not in norm
    # the ceiling-only framing is explicit
    assert "up to ~{minutes_high} minutes" in norm
    assert "a ceiling, not a target" in norm
    assert "There is no minimum." in norm
    # lead-depth steering survives as quality guidance (not floor remedy)
    assert "three movements" in raw
    assert "receipts" in raw
    assert "center of gravity" in norm

    # every compression directive is untouched (now unopposed — the intent)
    assert "LENGTH is EMERGENT, never filled" in raw
    assert "CEILINGS and guides, NOT floors" in raw
    assert "a naturally short episode is correct" in norm
    assert "aim for the FULL target" not in raw  # the killed fullness ask stays dead

    # it reaches the built prompt: ceiling rendered, no floor number anywhere
    built = generate.build_script_prompt(
        A_DAY, "A", "The narrative body.", _inputs_for([slot(1), slot(2), slot(3)]))
    bnorm = " ".join(built.split())
    assert "HARD BOOKEND" not in built
    assert "up to ~11 minutes" in bnorm and "(~1650 words)" in bnorm
    assert "under ~600 words" not in built and "600-1650" not in built
    assert "three movements" in built and "receipts" in built


# =========================================================================
# PART 2 — informed validation retry in call_llm (mirror the rank shapes)
# =========================================================================

BASE = "SCRIPT-PROMPT-BODY"
# a representative validator failure text, quoted verbatim into the correction
# (refreshed for the floor-REMOVED contract: the machinery under test is
# failure-class-general — it echoes whatever the validator raises)
ERRTEXT = ("script degenerate: 40 words — below the 120-word brokenness "
           "backstop")


def _resp(content, finish_reason="stop", pt=900, ct=200):
    return {
        "choices": [{"finish_reason": finish_reason,
                     "message": {"content": content}}],
        "usage": {"prompt_tokens": pt, "completion_tokens": ct},
    }


def _wire(monkeypatch, script):
    """Monkeypatch generate._chat with a scripted attempt sequence; returns the
    list of prompts actually sent. An entry is a response dict (returned) or an
    exception instance (raised)."""
    sent = []

    def fake_chat(key, prompt, max_tokens, temperature, json_mode):
        sent.append(prompt)
        step = script.pop(0)
        if isinstance(step, BaseException):
            raise step
        return step

    monkeypatch.setattr(generate, "_chat", fake_chat)
    monkeypatch.setattr(time, "sleep", lambda s: None)
    return sent


def _correction(errtext):
    return (generate.RETRY_CORRECTION_PREFIX + errtext
            + generate.RETRY_CORRECTION_SUFFIX)


def _corrected(errtext):
    return BASE + "\n\n" + _correction(errtext)


def _reject_once(errtext=ERRTEXT):
    """A validate that raises the given ValueError on its FIRST call, passes
    after — so attempt 1 fails validation and attempt 2 (the corrected one) is
    accepted."""
    state = {"n": 0}

    def v(content):
        state["n"] += 1
        if state["n"] == 1:
            raise ValueError(errtext)

    return v


def _call(validate=None):
    return generate.call_llm("sk-x", BASE, "script", 100, 0.4, False,
                             validate=validate)


# --- the validation class: attempt 2 carries the exact failure text -------------

def test_validation_failure_retry_carries_the_exact_error_text(monkeypatch):
    """A validation ValueError on attempt 1 -> attempt 2's prompt is the
    ORIGINAL + a correction block that QUOTES the exact ValueError text. The
    model is no longer retried blind at the same near-miss."""
    sent = _wire(monkeypatch, [_resp("thin draft"), _resp("fixed draft")])
    content, _ = _call(validate=_reject_once())
    assert content == "fixed draft"
    assert sent[0] == BASE                       # attempt 1 pristine
    assert sent[1] == _corrected(ERRTEXT)        # attempt 2 = original + correction
    assert ERRTEXT in sent[1]                    # the exact failure is quoted
    assert generate.RETRY_CORRECTION_PREFIX not in sent[0]


def test_double_validation_failure_appends_correction_exactly_once(monkeypatch):
    """Two validation failures -> visible GenerateError. Attempt 1 never sees
    the correction; attempt 2 is byte-exactly ORIGINAL + ONE correction block
    (anchored to `prompt`, not to `next_prompt` — the construction that would
    stack corrections if the attempt count ever grew past two)."""
    def always_reject(content):
        raise ValueError(ERRTEXT)

    sent = _wire(monkeypatch, [_resp("bad"), _resp("still bad")])
    with pytest.raises(generate.GenerateError) as excinfo:
        _call(validate=always_reject)
    assert "after one retry" in str(excinfo.value)
    assert len(sent) == 2
    assert generate.RETRY_CORRECTION_PREFIX not in sent[0]
    assert sent[1] == _corrected(ERRTEXT)
    assert sent[1].count(generate.RETRY_CORRECTION_PREFIX) == 1


def test_correction_never_leaks_into_a_fresh_call(monkeypatch):
    """next_prompt is call-local: after a call whose retry carried the
    correction, a brand-new call's attempt 1 must send the pristine prompt."""
    sent = _wire(monkeypatch, [
        _resp("bad"), _resp("recovered"),   # call 1: fail then recover
        _resp("clean"),                      # call 2: clean first draw
    ])
    _call(validate=_reject_once())
    content, _ = _call(validate=None)
    assert content == "clean"
    assert len(sent) == 3
    assert sent[1] == _corrected(ERRTEXT)
    assert sent[2] == BASE                   # fresh call starts pristine


# --- transport classes: the retry must re-send the ORIGINAL bytes ---------------

def test_429_rate_limit_retry_re_sends_original_bytes(monkeypatch):
    """A transient 429 is throttling, not the model's doing — its retry gets no
    correction (byte-equal to attempt 1)."""
    sent = _wire(monkeypatch, [
        urllib.error.HTTPError("u", 429, "slow down", {"Retry-After": "0"}, None),
        _resp("ok"),
    ])
    content, _ = _call(validate=None)
    assert content == "ok"
    assert sent == [BASE, BASE]
    assert all(generate.RETRY_CORRECTION_PREFIX not in p for p in sent)


def test_timeout_retry_re_sends_original_bytes(monkeypatch):
    sent = _wire(monkeypatch, [TimeoutError("timed out"), _resp("ok")])
    content, _ = _call(validate=None)
    assert content == "ok"
    assert sent == [BASE, BASE]
    assert all(generate.RETRY_CORRECTION_PREFIX not in p for p in sent)


def test_connection_error_retry_re_sends_original_bytes(monkeypatch):
    sent = _wire(monkeypatch, [
        urllib.error.URLError(ConnectionRefusedError(61, "connection refused")),
        _resp("ok"),
    ])
    content, _ = _call(validate=None)
    assert content == "ok"
    assert sent == [BASE, BASE]
    assert all(generate.RETRY_CORRECTION_PREFIX not in p for p in sent)


# --- truncation: the as-built classification, pinned consciously ----------------

def test_truncation_retry_carries_the_correction_as_built(monkeypatch):
    """AS-BUILT PIN (see the module docstring's CLASSIFICATION PIN). A
    finish_reason='length' truncation raises ValueError inside the try, so it
    routes through the malformed/validation handler and its retry carries the
    correction quoting the truncation message."""
    trunc_msg = "completion truncated at the script token cap (100)"
    sent = _wire(monkeypatch, [
        _resp("cut off", finish_reason="length"),
        _resp("ok"),
    ])
    content, _ = _call(validate=None)
    assert content == "ok"
    assert sent[0] == BASE
    assert sent[1] == _corrected(trunc_msg)
    assert "truncated" in sent[1]


# --- the correction text itself: model-facing prose on the trust path -----------

def test_correction_text_is_class_neutral_and_does_not_loosen():
    """The correction frames a rejection, demands the ONE failure be fixed with
    every other rule still binding, and asks for only the corrected output — and
    carries nothing that loosens contract compliance. Load-bearing phrases are
    pinned so a wordsmith that drops the 'every other rule still binds' clause
    goes red at the gate instead of drifting."""
    prefix = generate.RETRY_CORRECTION_PREFIX
    suffix = generate.RETRY_CORRECTION_SUFFIX
    assert "CORRECTION" in prefix
    assert "rejected" in prefix
    # the whole point: fix THAT failure, nothing else loosens
    assert "nothing else" in suffix
    assert "still binds" in suffix
    assert "corrected output" in suffix
    lowered = (prefix + suffix).lower()
    for loosening in (
        "be creative", "creativity", "any reasonable", "best guess",
        "approximate", "closest", "plausible", "paraphrase", "make up",
        "your choice", "feel free", "temperature", "loosely", "roughly",
        "pad", "fill to", "reach the",
    ):
        assert loosening not in lowered, f"loosening phrase present: {loosening!r}"
