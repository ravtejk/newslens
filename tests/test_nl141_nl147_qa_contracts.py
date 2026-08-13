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

from newslens import llm

PROTOTYPE_ROOT = Path(__file__).resolve().parents[1]

# The three shipped artifacts whose Haiku mentions are all present-tense claims
# about the CURRENT seat map. PREFLIGHT.md and adr/* are excluded on purpose:
# their mentions are dated history rows and measurement receipts, which the
# no-Haiku law protects rather than purges.
SHIPPED_PROSE = (".env.example", "README.md", "SETUP.md")


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


def test_the_shipped_fall_over_keeps_each_seats_own_model(monkeypatch):
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
    Note `check_lane` resolves that binary from os.environ, NOT from the env
    mapping passed to `effective_seat`, which is why this monkeypatches the
    process environment.

    No spawn, no network, no spend: `check_lane`'s binary check is a filesystem
    stat and the api-lane check is a registry lookup."""
    monkeypatch.setenv("NEWSLENS_CLAUDE_BIN", "/nonexistent/qa/no-such-claude")
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
