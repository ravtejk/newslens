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

import json
import time
import urllib.error

import pytest

from newslens import ranking
from test_ranking_validation import (
    DATE, KNOWN_IDS, MEMORY, TAGS, _resp, cluster, rank_cfg, seed_items,
)

BASE = "BASE-PROMPT"
CORRECTED = BASE + "\n\n" + ranking.RETRY_CORRECTION


@pytest.fixture(autouse=True)
def _pin_rank_api_lane(monkeypatch):
    """B3: rank DEFAULTS to the claude -p subscription lane (usd_charged 0.0).
    These retry tests assert the per-attempt SPEND via _usd (usage_to_usd = the
    shadow price), so they run on the api FALL-OVER lane where usd == usd_charged
    == usd_shadow — the corrected-retry / transport-retry ledger mechanics they
    pin are lane-agnostic. The subscription-lane cost_sink shape (legacy usd ==
    usd_charged == 0.0) is proven in test_b3_subscription_lane.py."""
    monkeypatch.setenv("NEWSLENS_LANE_RANK", "api")


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
    assert "[id=KEY]" in text                         # NL-70: the alphanumeric key format
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


# --- attempt ledger (gate F1-F4, 2026-07-14): every billed attempt on record -----
#
# The property under pin: token_usage shows only the RETURNING attempt, so
# without the ledger a corrected-retry recovery is indistinguishable from a
# clean first draw after the money is spent — and a double failure logs NULL
# over real spend (run 28 did exactly that). One entry per billed attempt,
# recorded BEFORE the truncation check; carried on the error across the raise;
# surfaced at run level as meta.llm_attempts + one warning, only when a retry
# actually fired.

def _resp_u(payload, pt, ct, finish_reason=None):
    """_resp with distinguishable per-attempt token counts."""
    r = _resp(payload, finish_reason)
    r["usage"] = {"prompt_tokens": pt, "completion_tokens": ct}
    return r


def _usd(pt, ct):
    return round(ranking.usage_to_usd(
        {"prompt_tokens": pt, "completion_tokens": ct}
    ), 6)


def test_P1_recovered_retry_lands_both_attempts_on_the_run_record(
    migrated_con, monkeypatch
):
    """Malformed -> corrected recovery at the RUN level: both billed attempts
    in ranking_runs.meta.llm_attempts with tokens+usd, the true-spend warning
    fires, and token_usage still holds the returning attempt only."""
    seed_items(migrated_con)
    script = [
        _resp_u({"clusters": [cluster([9999])]}, 700, 90),   # billed, rejected
        _resp_u({"clusters": [cluster(
            [1, 2], title="Keeper",
            tags=[{"name": "AI regulation", "level": "topic"}],
        )]}, 730, 120),                                      # billed, recovers
    ]
    monkeypatch.setattr(ranking, "_post_chat",
                        lambda key, prompt: script.pop(0))
    monkeypatch.setattr(time, "sleep", lambda s: None)
    report = ranking.run_rank(
        date=DATE, con=migrated_con, cfg=rank_cfg(), env={"OPENAI_API_KEY": "sk-x"}
    )
    run = migrated_con.execute(
        "SELECT meta, token_usage FROM ranking_runs WHERE date = ?", (DATE,)
    ).fetchone()
    meta = json.loads(run["meta"])
    assert meta["status"] == "ok"
    ledger = meta["llm_attempts"]
    assert [e["attempt"] for e in ledger] == [1, 2]
    assert all(e["step"] == "rank_select" for e in ledger)
    assert [(e["prompt_tokens"], e["completion_tokens"]) for e in ledger] == [
        (700, 90), (730, 120)
    ]
    assert [e["usd"] for e in ledger] == [_usd(700, 90), _usd(730, 120)]
    assert all(e["usd"] > 0 for e in ledger)
    # token_usage column semantics unchanged: the returning attempt only.
    assert json.loads(run["token_usage"])["prompt_tokens"] == 730
    # The disclosure names the retry and the TRUE total spend.
    retry_warnings = [w for w in report.warnings if "rank retry" in w]
    assert len(retry_warnings) == 1
    total = round(_usd(700, 90) + _usd(730, 120), 6)
    assert f"${total:.4f}" in retry_warnings[0]
    assert "ranking_runs.meta.llm_attempts" in retry_warnings[0]


def test_P2_double_failure_carries_the_ledger_onto_the_failed_row(
    migrated_con, monkeypatch
):
    """Two billed malformed attempts -> RankingError carries .llm_attempts,
    and the failed ranking_runs row's meta records both entries while
    token_usage stays NULL (run 28's money hole, closed)."""
    seed_items(migrated_con)
    script = [
        _resp_u({"clusters": [cluster([9999])]}, 700, 90),
        _resp_u({"clusters": [cluster([8888])]}, 730, 95),
    ]
    monkeypatch.setattr(ranking, "_post_chat",
                        lambda key, prompt: script.pop(0))
    monkeypatch.setattr(time, "sleep", lambda s: None)
    with pytest.raises(ranking.RankingError) as excinfo:
        ranking.run_rank(
            date=DATE, con=migrated_con, cfg=rank_cfg(),
            env={"OPENAI_API_KEY": "sk-x"},
        )
    assert [e["attempt"] for e in excinfo.value.llm_attempts] == [1, 2]
    run = migrated_con.execute(
        "SELECT meta, token_usage FROM ranking_runs WHERE date = ?", (DATE,)
    ).fetchone()
    meta = json.loads(run["meta"])
    assert meta["status"] == "failed"
    ledger = meta["llm_attempts"]
    assert len(ledger) == 2
    assert all(e["usd"] > 0 for e in ledger)
    assert run["token_usage"] is None


def test_P3_transport_retry_records_exactly_one_billed_attempt(monkeypatch):
    """A timeout never returns usage — nothing to bill, nothing on the ledger.
    The recovery entry carries attempt=2 (the true attempt number, not a
    renumbering): exactly one entry, no phantom cost."""
    sink: list = []
    _wire(monkeypatch, [
        TimeoutError("timed out"),
        _resp_u({"clusters": [cluster([1])]}, 500, 40),
    ])
    ranking.call_llm_validated(
        "sk-x", BASE, KNOWN_IDS, TAGS, MEMORY, cost_sink=sink,
    )
    assert len(sink) == 1
    assert sink[0]["attempt"] == 2
    assert sink[0]["usd"] == _usd(500, 40)


def test_P3b_single_clean_draw_stays_silent_at_run_level(
    migrated_con, monkeypatch
):
    """No retry -> no meta.llm_attempts, no rank-retry warning: the ledger
    surfaces only when there is something to disclose (a one-entry ledger is
    the token_usage column, restated)."""
    seed_items(migrated_con)
    script = [
        _resp_u({"clusters": [cluster(
            [1, 2], title="Keeper",
            tags=[{"name": "AI regulation", "level": "topic"}],
        )]}, 730, 120),
    ]
    monkeypatch.setattr(ranking, "_post_chat",
                        lambda key, prompt: script.pop(0))
    report = ranking.run_rank(
        date=DATE, con=migrated_con, cfg=rank_cfg(), env={"OPENAI_API_KEY": "sk-x"}
    )
    meta = json.loads(migrated_con.execute(
        "SELECT meta FROM ranking_runs WHERE date = ?", (DATE,)
    ).fetchone()["meta"])
    assert "llm_attempts" not in meta
    assert not [w for w in report.warnings if "rank retry" in w]


def test_P4_no_sink_is_the_default_and_the_error_still_has_the_attribute(
    monkeypatch
):
    """cost_sink defaults to None: the recovery path works sink-less, and a
    double failure's error carries llm_attempts == [] (attribute always
    present, so run_rank's getattr never feeds garbage to log_failed_run)."""
    _wire(monkeypatch, [
        _resp({"clusters": [cluster([9999])]}),
        _resp({"clusters": [cluster([1])]}),
    ])
    clusters, _ = _call()
    assert [c["item_ids"] for c in clusters] == [[1]]
    _wire(monkeypatch, [
        _resp({"clusters": [cluster([9999])]}),
        _resp({"clusters": [cluster([8888])]}),
    ])
    with pytest.raises(ranking.RankingError) as excinfo:
        _call()
    assert excinfo.value.llm_attempts == []


def test_P5_truncated_attempt_is_billed_and_on_the_ledger(monkeypatch):
    """The ledger append sits BEFORE the finish_reason check (gate F1's exact
    property, generate.py precedent): a truncated draw billed real tokens and
    must be entry 1, not invisible."""
    sink: list = []
    _wire(monkeypatch, [
        _resp_u({"clusters": [cluster([1])]}, 800, 3000, finish_reason="length"),
        _resp_u({"clusters": [cluster([1])]}, 830, 150),
    ])
    ranking.call_llm_validated(
        "sk-x", BASE, KNOWN_IDS, TAGS, MEMORY, cost_sink=sink,
    )
    assert [e["attempt"] for e in sink] == [1, 2]
    assert sink[0]["completion_tokens"] == 3000  # the truncated spend, recorded
