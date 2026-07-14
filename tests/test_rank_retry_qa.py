"""Corrected-retry scope QA (QA-written, 2026-07-14; extends the run-28 fix
tests in test_ranking_validation.py).

The contract under test, from the fix: the ONE retry mutates the prompt for
exactly ONE failure class — malformed/failed-validation output. Every
transport-class retry (5xx, 429, timeout, connection) re-sends the ORIGINAL
prompt byte-for-byte. The correction never appears in attempt 1, is anchored
to the ORIGINAL prompt (it can never compound into a growing prompt), never
survives into a fresh call, and its text only TIGHTENS compliance (verbatim
[id=N] copying, exact tag/thread vocabulary — never loosening language).

Fully offline: _post_chat is monkeypatched at the module seam, same as the
fix's own tests; the autouse loopback guard would refuse any real socket.

CLASSIFICATION PIN (conscious, gate-visible): completion truncation
(finish_reason="length") raises ValueError inside the try block, so it lands
in the MALFORMED-OUTPUT handler — the truncation retry DOES carry the
correction. The 2026-07-14 QA dispatch checklist grouped truncation with the
original-bytes transport classes; the as-built code scopes only
5xx/429/network as transport and codes truncation into the malformed class
(pre-existing routing, from before this fix). The correction's content is
compliance-tightening and plausibly SHORTENS the completion ("leave that
item out", "return only the one JSON object"), which is the truncation
remedy, so QA pins the as-built behavior in
test_truncation_retry_carries_the_correction_as_built. If the gate rules
truncation must re-send original bytes instead, flip that one test's
expectation — a conscious flip, not drift.
"""

from __future__ import annotations

import time
import urllib.error

import pytest

from newslens import ranking
from test_ranking_validation import KNOWN_IDS, MEMORY, TAGS, _resp, cluster

BASE = "BASE-PROMPT"
CORRECTED = BASE + "\n\n" + ranking.RETRY_CORRECTION


def _wire(monkeypatch, script):
    """Monkeypatch _post_chat with a scripted attempt sequence; returns the
    list of prompts actually sent. A script entry is either a parsed-response
    dict (returned) or an exception instance (raised)."""
    sent = []

    def fake_post(key, prompt):
        sent.append(prompt)
        step = script.pop(0)
        if isinstance(step, BaseException):
            raise step
        return step

    monkeypatch.setattr(ranking, "_post_chat", fake_post)
    monkeypatch.setattr(time, "sleep", lambda s: None)
    return sent


def _call():
    return ranking.call_llm_validated("sk-x", BASE, KNOWN_IDS, TAGS, MEMORY)


# --- transport classes: the retry must re-send the ORIGINAL bytes ---------------

def test_429_rate_limit_retry_re_sends_the_original_prompt_unchanged(monkeypatch):
    """Transient 429 is throttling, not the model's doing — its retry gets no
    correction and no other mutation (byte-equal to attempt 1)."""
    sent = _wire(monkeypatch, [
        urllib.error.HTTPError("u", 429, "slow down", {"Retry-After": "0"}, None),
        _resp({"clusters": [cluster([1])]}),
    ])
    clusters, _ = _call()
    assert [c["item_ids"] for c in clusters] == [[1]]
    assert sent == [BASE, BASE]
    assert all(ranking.RETRY_CORRECTION not in p for p in sent)


def test_timeout_retry_re_sends_the_original_prompt_unchanged(monkeypatch):
    sent = _wire(monkeypatch, [
        TimeoutError("timed out"),
        _resp({"clusters": [cluster([2])]}),
    ])
    clusters, _ = _call()
    assert [c["item_ids"] for c in clusters] == [[2]]
    assert sent == [BASE, BASE]
    assert all(ranking.RETRY_CORRECTION not in p for p in sent)


def test_connection_error_retry_re_sends_the_original_prompt_unchanged(monkeypatch):
    sent = _wire(monkeypatch, [
        urllib.error.URLError(ConnectionRefusedError(61, "connection refused")),
        _resp({"clusters": [cluster([3])]}),
    ])
    clusters, _ = _call()
    assert [c["item_ids"] for c in clusters] == [[3]]
    assert sent == [BASE, BASE]
    assert all(ranking.RETRY_CORRECTION not in p for p in sent)


# --- the malformed class: exactly one correction, anchored, never leaking -------

def test_double_malformed_appends_the_correction_exactly_once(monkeypatch):
    """Two malformed attempts -> visible RankingError. Attempt 1 never sees
    the correction; attempt 2's prompt is byte-exactly ORIGINAL + one
    correction block (anchored to `prompt`, not to `next_prompt` — the
    construction that would stack corrections into a growing prompt if the
    attempt count ever grows past two)."""
    sent = _wire(monkeypatch, [
        _resp({"clusters": [cluster([9999])]}),   # invented id -> rejected
        _resp({"clusters": [cluster([8888])]}),   # invented again -> rejected
    ])
    with pytest.raises(ranking.RankingError) as excinfo:
        _call()
    assert "after one retry" in str(excinfo.value)
    assert "malformed" in str(excinfo.value)
    assert len(sent) == 2
    assert ranking.RETRY_CORRECTION not in sent[0]
    assert sent[1] == CORRECTED
    assert sent[1].count(ranking.RETRY_CORRECTION) == 1


def test_correction_never_leaks_into_a_fresh_call(monkeypatch):
    """next_prompt is call-local: after a call whose retry carried the
    correction, a brand-new call's attempt 1 must send the pristine prompt
    (guards against any future module-level caching of the retry state)."""
    sent = _wire(monkeypatch, [
        _resp({"clusters": [cluster([9999])]}),   # call 1, attempt 1: rejected
        _resp({"clusters": [cluster([1])]}),      # call 1, attempt 2: recovers
        _resp({"clusters": [cluster([2])]}),      # call 2, attempt 1: clean
    ])
    _call()
    clusters, _ = _call()
    assert [c["item_ids"] for c in clusters] == [[2]]
    assert len(sent) == 3
    assert sent[1] == CORRECTED
    assert sent[2] == BASE                        # fresh call starts pristine


# --- truncation: the as-built classification, pinned consciously ----------------

def test_truncation_retry_carries_the_correction_as_built(monkeypatch):
    """AS-BUILT PIN — see the module docstring's CLASSIFICATION PIN note.
    Truncation routes through the malformed-output handler, so its retry
    carries the correction (and stays temperature-0, original prompt + one
    appended block, nothing else mutated)."""
    sent = _wire(monkeypatch, [
        _resp({"clusters": [cluster([1])]}, finish_reason="length"),
        _resp({"clusters": [cluster([1])]}),
    ])
    clusters, _ = _call()
    assert [c["item_ids"] for c in clusters] == [[1]]
    assert sent[0] == BASE
    assert sent[1] == CORRECTED


# --- the correction text itself: model-facing prose on the trust path -----------

def test_correction_text_only_tightens_compliance():
    """The correction must DEMAND the closed vocabulary — verbatim [id=N]
    copying, EXACT tag/thread lists, omission (never approximation) as the
    fallback — and must carry nothing that loosens validation compliance.
    Load-bearing phrases are pinned so a wordsmithing pass that drops the
    verbatim demand goes red at the gate instead of drifting through."""
    text = ranking.RETRY_CORRECTION
    # demands present
    assert "copied verbatim" in text
    assert "[id=N]" in text
    assert "Do NOT invent" in text
    assert "EXACTLY" in text
    assert "leave that item out" in text          # honest fallback is omission
    assert "Numbers inside titles are" in text    # the headline-number class
    # loosening language absent (checked lowercase; list avoids words the
    # text uses only inside prohibitions, e.g. "invent", "guess")
    lowered = text.lower()
    for loosening in (
        "be creative", "creativity", "any reasonable", "best guess",
        "approximate", "closest match", "similar id", "plausible",
        "paraphrase", "make up", "your choice", "feel free",
        "temperature", "loosely", "roughly",
    ):
        assert loosening not in lowered, f"loosening phrase present: {loosening!r}"
    # and it must end by demanding the same closed task, not a new one
    assert "SAME INPUT ITEMS" in text
    assert "one JSON object" in text
