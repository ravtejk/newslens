"""NL-141 / NL-147 QA contracts — acceptance criteria from the 2026-08-13 pass.

Two findings, two contracts. Receipts for both are in
research/2026-08-13--nl141-nl147-qa.md.

  F-1 (BORN RED here)  — the batch's census was scoped `grep -rni haiku src/`,
      so six present-tense Haiku claims survive in the three SHIPPED artifacts
      the principal actually reads when granting access. FIX CONTRACT: bring
      .env.example / README.md / SETUP.md in line with `llm.SEATS`, then this
      goes green. These three files carry no measurement tables and no dated
      correction narratives — unlike PREFLIGHT.md and adr/*, whose Haiku
      mentions are receipts and are deliberately NOT covered here.

  F-2 (GREEN here, and that is the finding) — the shipped fall-over really does
      keep the seat's model; but the pin that claims to guard it never calls the
      shipped fall-over, so it cannot see a regression in it. FIX CONTRACT: this
      test replaces `test_a_lane_fall_over_never_substitutes_the_model` as the
      fall-over pin, because THIS one goes red when `llm.effective_seat` starts
      substituting a model and that one does not (mutation M4 in the QA report:
      the old pin scored 10 passed under exactly that regression).

GATE R-A RESOLUTION (2026-08-13): fix loop 1 rewrote the original pin IN PLACE
to drive `llm.effective_seat` (keeping its name, adding price + no-fall
assertions) instead of replacing it with this file's test; the gate re-derived
the head-to-head (original-reconstruction PASSED/PASSED, this contract
FAILED/PASSED, the rewrite FAILED/FAILED under model-swap/re-price) and blessed
the rewrite as strictly dominating. This test stands as a second, independent
pin; "replaces" above is QA's recommendation of record, not the landed outcome.
"""
from __future__ import annotations

import os
import re
from pathlib import Path

import pytest

from newslens import doctor, llm

PROTOTYPE_ROOT = Path(__file__).resolve().parents[1]

# The three shipped artifacts whose Haiku mentions are all present-tense claims
# about the CURRENT seat map. PREFLIGHT.md and adr/* are excluded on purpose:
# their mentions are dated history rows and measurement receipts, which the
# no-Haiku law protects rather than purges.
SHIPPED_PROSE = (".env.example", "README.md", "SETUP.md")


def _readme_env_row(var: str):
    """(required_cell, why_cell) for one row of README's credentials table."""
    text = (PROTOTYPE_ROOT / "README.md").read_text(encoding="utf-8")
    for line in text.splitlines():
        if line.startswith(f"| `{var}`"):
            cells = [c.strip() for c in line.strip().strip("|").split("|")]
            assert len(cells) >= 3, f"malformed env-table row for {var}: {cells}"
            return cells[1], cells[2]
    raise AssertionError(f"no README env-table row for {var}")


@pytest.mark.parametrize("var,check", [
    ("OPENAI_API_KEY", "check_openai_key"),
    ("ANTHROPIC_API_KEY", "check_anthropic_key"),
])
def test_readme_never_demands_a_key_the_doctor_calls_unnecessary(var, check):
    """NL-155 (2026-08-14). BORN RED at ce334d1 on OPENAI_API_KEY.

    The README's `Required` column and the doctor's keyless verdict are two
    answers to one question — "must I fill this in for a default install?" —
    and they had drifted apart. README said **Yes** for OPENAI_API_KEY and
    justified it with "the narrative (writer), analyst, and synthesis seats",
    while `check_openai_key({})` on that same tree returned INFO / "not needed"
    because writer and analyst left for the Claude lane at B4/item C and
    `synthesis` is dormant. The prose was two seat batches stale and demanded a
    purchase the product had stopped needing.

    This pin does not hardcode which keys are needed — it DERIVES the answer
    from the doctor (which derives it from `llm.SEATS` + `llm.DORMANT_SEATS`)
    and only requires the table not to contradict it. A future seat flip that
    genuinely re-arms a key turns the doctor's verdict red-or-FAIL first, and
    then a bare "Yes" is allowed again.

    Scope, deliberately narrow: it constrains the Required CELL, not the prose
    cell. The Why column carries dated correction narratives by design, and a
    grep-the-prose rule would fight those."""
    required, why = _readme_env_row(var)
    verdict = getattr(doctor, check)({})
    assert len(verdict) == 1, f"{check}({{}}) is no longer a single line"
    status, text = verdict[0].status, verdict[0].text
    # FIX LOOP 1, QA F-4 (2026-08-14) — THE PREDICATE IS TEXT-DRIVEN, AND IT MAY
    # NOT SHRUG. Keyed on `status == INFO` it went vacuous under every other
    # verdict: QA disarmed this pin entirely by moving the verdict to WARN with
    # the words "not needed" still in it and a bare `**Yes**` restored to the
    # table — green, with the contradiction fully present. That world is not
    # hypothetical, because THIS batch added a WARN branch to the ANTHROPIC row
    # (armed fall-over, no key); a future default that arms it would have
    # switched half the pin off silently. A FAIL is the only verdict that can
    # make a key genuinely required, so anything else is read for its words —
    # and a non-FAIL whose words this pin cannot read FAILS LOUDLY rather than
    # passing, which is the difference between a pin and a decoration.
    _DENIALS = ("not needed", "need no key", "not required", "verdict withheld")
    unnecessary = status != doctor.FAIL and any(d in text for d in _DENIALS)
    if unnecessary:
        # FIX LOOP 1, QA F-5: a LEADING yes, however qualified. `!= "yes"`
        # caught `**Yes**` and waved through `Yes (text generation)` — which is
        # the exact shape the stale claim NL-155 deleted would grow back as.
        assert not re.match(r"yes\b", required.strip().strip("*_ ").lower()), (
            f"README marks {var} Required='{required}', but the doctor's own "
            f"keyless verdict on this tree is:\n    [{status}] {text}\n"
            "One of the two is lying to the principal about what he must buy "
            "before the product will run.")
    else:
        assert status == doctor.FAIL, (
            f"{check}({{}}) returned [{status}] with wording this pin cannot "
            f"read:\n    {text}\nThat is neither a FAIL (the key is genuinely "
            f"required, so a bare 'Yes' is allowed) nor any denial it knows "
            f"{_DENIALS}. Teach it the new wording — a pin that cannot read "
            "the verdict must say so, not pass.")
    assert why, f"{var} row has an empty Why/scope cell"


@pytest.mark.parametrize("relpath", SHIPPED_PROSE)
def test_no_shipped_artifact_claims_a_haiku_seat(relpath):
    """BORN RED (QA F-1, 2026-08-13).

    The batch deleted `_HAIKU_API` precisely because a SENTENCE telling a
    maintainer to flip a row back to Haiku is how the last two slips got in.
    The same sentence class still stands in the files the principal reads to set
    the product up:

      * .env.example  — "unset = the default seat map ... Haiku on the
        SUBSCRIPTION lane for rank/editor/script; GPT-4o (api) for
        state/synthesis". Wrong four ways today: rank is Sonnet 5, editor and
        script and state are Opus 4.8, and state is not a gpt-4o seat.
      * README.md     — "Runs on the `rank` seat — Claude Haiku 4.5"; "rank/
        editor/script/state Claude Haiku 4.5"; "The Claude API lane credential
        (Claude Haiku 4.5)".
      * SETUP.md      — "the ranking, editorial-tighten, TTS-script and
        memory/state seats (Claude Haiku 4.5)"; "an anthropic content seat
        (Haiku rank/editor/script/state ...)".

    This is prose, so it cannot route a call — but .env.example and SETUP.md are
    the access-granting surface, and README is the product's own description of
    what it runs. A law enforced in the seam and contradicted in the setup guide
    is enforced in one place only."""
    path = PROTOTYPE_ROOT / relpath
    assert path.exists(), f"{relpath} missing from the checkout"
    text = path.read_text(encoding="utf-8")
    hits = [f"{relpath}:{n}: {line.strip()[:120]}"
            for n, line in enumerate(text.splitlines(), 1)
            if "haiku" in line.lower()]
    assert hits == [], (
        f"{relpath} still names Haiku as part of the product:\n" + "\n".join(hits)
        + "\n\nThe live roster is llm.SEATS: "
        + ", ".join(f"{n}={c.model}" for n, c in sorted(llm.SEATS.items())))


def test_the_shipped_fall_over_keeps_each_seats_own_model():
    """QA F-2 (2026-08-13) — the fall-over pin with teeth.

    As first written, `test_a_lane_fall_over_never_substitutes_the_model`
    asserted a property of `dataclasses.replace(cfg, lane=...)` — a property of
    the stdlib, not of this product: it re-implemented the line it meant to pin
    and never called `llm.effective_seat`, so a fall-over that DID substitute a
    model would not have disturbed it (measured then: 10 passed with
    `effective_seat` poisoned to return a Haiku model). It was rewritten at fix
    loop 1 to drive `effective_seat` directly — see test_nl147_no_haiku.py:119.

    This drives the shipped function instead. The fall is forced the way the
    product forces it — NEWSLENS_LANE_FALLBACK=api armed, and the subscription
    lane genuinely unavailable because the `claude` binary does not resolve.

    NL-156 (2026-08-14): the `monkeypatch.setenv("NEWSLENS_CLAUDE_BIN", ...)`
    that used to sit here is DELETED. Its docstring conceded that "`check_lane`
    resolves that binary from os.environ, NOT from the env mapping passed to
    `effective_seat`" — so the mapping's NEWSLENS_CLAUDE_BIN entry bought
    nothing and the process env forced the fall. A probe that has to reach
    around the seam it is probing cannot see that seam break. `check_lane` now
    honours the env it is handed, so the mapping alone stages the fall; this is
    BORN RED on the pre-NL-156 tree (conftest's process-env stub EXISTS, so the
    old `check_lane` found it, nothing fell, and `assert fell` failed).

    No spawn, no network, no spend: `check_lane`'s binary check is a filesystem
    stat and the api-lane check is a registry lookup."""
    env = {
        "NEWSLENS_LANE_FALLBACK": "api",
        "ANTHROPIC_API_KEY": "sk-ant-not-a-real-key-never-sent",
        "NEWSLENS_CLAUDE_BIN": "/nonexistent/qa/no-such-claude",
    }

    fell = []
    for seat, cfg in sorted(llm.SEATS.items()):
        if cfg.lane != "subscription":
            continue
        effective, reason = llm.effective_seat(seat, env)
        assert reason == "subscription_unavailable", (
            f"seat {seat} did not take the armed fall — this test is no longer "
            "staging the fall-over it exists for")
        assert effective.lane == "api"
        fell.append(seat)
        assert effective.model == cfg.model, (
            f"the armed fall-over MOVED seat {seat}'s model: "
            f"{cfg.model} -> {effective.model}. A lane fall changes the "
            "transport only; substituting a model makes a lane event into a "
            "seat ruling, which is the principal's call.")
        assert "haiku" not in effective.model.lower(), (
            f"seat {seat} fell onto a Haiku model: {effective.model}")

    assert fell, "no subscription seat fell — the fall-over was never exercised"
