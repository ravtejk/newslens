"""NL-147 — the no-Haiku law, as a test.

THE LAW (principal, 2026-08-09): no Haiku for any part of the product. It was
enforced by hand across three batches — ENG-M0 moved rank/editor/script/state,
NL-17 M3 fix loop 1 moved the follow-altitude resolver (the UX-touching seat),
and NL-147 finished the job on the unarmed rollback targets and the prose. Hand
enforcement is exactly what it should not stay: the record shows Haiku slipping
back into the seam TWICE by way of a sentence nobody re-read.

WHAT THIS FILE DOES NOT DO, stated because a purge test that is too greedy is
worse than none: it does not ban the STRING "haiku" from the tree. Two classes
are deliberately protected —

  * PRICING-LEDGER DATA. `battery._ARM_PRICES["claude-haiku-4-5"]` prices ledger
    rows and battery arms that were ALREADY SPENT. The law governs what may RUN;
    a record of a past charge is not a seat target, and repricing history is how
    a cost ledger starts lying. There is a pin below that this row STAYS.
  * RECEIPTS. Measurement tables, dated rulings, and correction narratives in
    comments and docstrings (llm.py's thinking-tax measurements, ranking.py's
    id-slip record, DECISIONS quotes). Those are records; rewriting them to make
    a grep look tidy would delete the evidence for the law itself.

So the pins below are on BEHAVIOR and on the seat-shaped DATA STRUCTURES: what
the roster resolves to, what a fall-over can do, what the doctor prints.

BORN-RED STATUS, per test (HEAD = edf5ad4; the run is quoted in
research/2026-08-13--nl141-nl147-build.md):
  * BORN RED  — test_no_seat_target_dict_in_the_seam_names_a_haiku_model
                (`_HAIKU_API` and `_HAIKU_SUB` were live module attributes)
  * BORN RED  — test_the_seam_carries_no_haiku_price_constants
  * BORN RED  — test_the_doctor_names_no_haiku_model_on_any_surface
                (three separate surfaces said Haiku)
  * BORN RED  — test_the_doctor_reads_its_seat_map_off_the_table (x2, the
                mutation pins: the prose was hand-typed, so it did not follow)
  * CARRIED INVARIANT (born green) — FIVE, not three (the header undercounted
    until NL-141/147 fix loop 1, QA F-5): the live-roster pin, TWO fall-over
    pins (the armed fall through `effective_seat`, and the env-driven lane
    overrides through `resolve_seat`), and TWO battery-price pins (the ledger
    row STAYS, and an unknown model still falls back to the writer seat). All
    five were already true at HEAD; they are here as the tooth that keeps them
    true, and they are labelled rather than counted as this batch's born-red
    currency.

FIX LOOP 1 (2026-08-13, QA F-2): the armed-fall pin was DECORATION as first
written — it asserted a property of `dataclasses.replace` and never called
`llm.effective_seat`, so it scored 10 passed against a poisoned `effective_seat`
that substituted a Haiku model. It now drives the shipped function. The lesson
is the one this file is about: a pin that re-implements the line it means to
guard is testing the test. Both rewrites are in section 2.
"""
from __future__ import annotations

import dataclasses

import pytest

from conftest import sandbox_bin_env
from newslens import battery, doctor, llm


def _haiku_models(models) -> list:
    return sorted(m for m in models if "haiku" in str(m).lower())


# ===========================================================================
# 1. The roster and the module's seat-shaped data
# ===========================================================================

def test_no_live_seat_names_a_haiku_model():
    """CARRIED INVARIANT (born green — ENG-M0 and M3 did this work).

    The whole roster, not a named subset: a per-seat pin only guards the seats
    somebody thought to name, and `follow_altitude` is the standing proof that
    the dangerous seat is the one nobody listed (it sat on Haiku through ENG-M0
    because that batch enumerated the content seats and this one was not one of
    them)."""
    offenders = {name: cfg.model for name, cfg in llm.SEATS.items()
                 if "haiku" in cfg.model.lower()}
    assert offenders == {}, f"seats on a Haiku model: {offenders}"


def test_no_seat_target_dict_in_the_seam_names_a_haiku_model():
    """BORN RED at edf5ad4 — `_HAIKU_API` and `_HAIKU_SUB`.

    The roster pin above would never have caught them: they were not IN the
    roster. They were module-level seat-shaped dicts kept as the REGISTERED
    ROLLBACK TARGET, and the comment above them told a future maintainer to
    "flip a row back to **_HAIKU_API" — a standing, documented instruction to
    reintroduce a model the principal had outlawed.

    Discovered scan rather than a name list, for the same reason the roster pin
    is whole-roster: a `_HAIKU_SONNET_MIXED_API` added next year is caught by
    shape, and would be missed by any list written today."""
    offenders = {}
    for name, value in vars(llm).items():
        if isinstance(value, dict) and {"provider", "model"} <= set(value):
            if "haiku" in str(value["model"]).lower():
                offenders[name] = value["model"]
    assert offenders == {}, (
        f"seat-shaped dicts naming a Haiku model: {offenders}. Under the "
        "no-Haiku law a Haiku row is not a rollback target — a seat's rollback "
        "is its OWN row with lane='api', and a MODEL rollback is a seat ruling "
        "that goes through the principal.")


def test_the_seam_carries_no_haiku_price_constants():
    """BORN RED at edf5ad4 — `HAIKU_USD_PER_MTOK_IN` / `_OUT` (1.00 / 5.00).

    They priced the two deleted rows and had no other consumer, so leaving them
    would have left a live price for a model no seat may use — the raw material
    for the next row. The price FACT is not lost; see the battery pin below."""
    leftovers = sorted(n for n in vars(llm) if "HAIKU" in n.upper())
    assert leftovers == [], f"Haiku constants still in the seam: {leftovers}"


# ===========================================================================
# 2. The structural guarantee — a lane event cannot change what runs
# ===========================================================================

def test_a_lane_fall_over_never_substitutes_the_model():
    """CARRIED INVARIANT, and the reason the deleted rows were unarmed rather
    than live: an armed fall moves the TRANSPORT and nothing else, so no
    NEWSLENS_LANE_FALLBACK and no per-seat NEWSLENS_LANE_<SEAT>=api could ever
    have routed a call to a model the seat row did not name. This is what makes
    the roster pin SUFFICIENT — without it, a Haiku-free roster would still not
    be a Haiku-free product.

    REWRITTEN in NL-141/147 fix loop 1 (QA F-2, 2026-08-13). The original body
    asserted that `dataclasses.replace(cfg, lane=...)` leaves `.model` alone —
    a property of the STDLIB, not of this product. It re-implemented the line it
    meant to pin and never called `llm.effective_seat`, so it could not see a
    regression in the thing it named: with `effective_seat` poisoned to return a
    Haiku model, the file still scored 10 passed (QA mutation M4). It now drives
    the shipped function, and it pins PRICES as well as the model — a fall that
    kept the model but re-priced it would restate spend in the ledger.

    The fall is forced the way the product forces it: armed via the env mapping,
    with the subscription lane genuinely dead at the gate because the `claude`
    binary does not resolve.

    NL-156 (2026-08-14) — THE WORKAROUND IS GONE, AND THAT IS THE POINT. This
    body used to carry `monkeypatch.setenv("NEWSLENS_CLAUDE_BIN", ...)` beside
    the mapping below, with a docstring conceding "`check_lane` reads that
    binary from os.environ rather than from the mapping handed to
    `effective_seat`, which is why this sets the process env too." The mapping's
    NEWSLENS_CLAUDE_BIN entry was therefore INERT — the process env did all the
    work, and this probe could not have caught `check_lane` ignoring the env it
    was handed, because it was silently relying on exactly that. NL-156 made
    `check_lane` honour its env argument; the monkeypatch is deleted so the
    mapping is load-bearing, and this test is now the pin for that. It is BORN
    RED against the pre-NL-156 tree: conftest points the process env's
    NEWSLENS_CLAUDE_BIN at a stub that EXISTS, so the old `check_lane` resolved
    it, no seat fell, and the final `assert fell` failed.

    No spawn, no network, no spend: the binary check is a filesystem stat and
    the api-lane check is a provider-registry lookup."""
    env = {
        "NEWSLENS_LANE_FALLBACK": "api",
        "NEWSLENS_CLAUDE_BIN": "/nonexistent/nl147/no-such-claude",
    }

    fell = []
    for name, cfg in sorted(llm.SEATS.items()):
        effective, reason = llm.effective_seat(name, env)
        if cfg.lane != "subscription":
            # A seat whose lane is available is handed back untouched, unlabelled.
            assert reason is None, f"seat {name} fell without needing to: {reason}"
            assert effective == cfg
            continue
        assert reason == "subscription_unavailable", (
            f"seat {name} did not take the armed fall — this test is no longer "
            "staging the fall-over it exists for")
        assert effective.lane == "api"
        fell.append(name)
        assert effective.model == cfg.model, (
            f"the armed fall-over MOVED seat {name}'s model: {cfg.model} -> "
            f"{effective.model}. A lane fall changes the transport only; "
            "substituting a model turns a lane event into a seat ruling, which "
            "is the principal's call.")
        assert (effective.usd_per_mtok_in, effective.usd_per_mtok_out) == (
            cfg.usd_per_mtok_in, cfg.usd_per_mtok_out), (
            f"the armed fall-over RE-PRICED seat {name}: "
            f"{(cfg.usd_per_mtok_in, cfg.usd_per_mtok_out)} -> "
            f"{(effective.usd_per_mtok_in, effective.usd_per_mtok_out)}")
        assert "haiku" not in effective.model.lower(), (
            f"seat {name} fell onto a Haiku model: {effective.model}")

    assert fell, "no subscription seat fell — the fall-over was never exercised"


def test_no_lane_override_can_resolve_a_seat_onto_a_haiku_model():
    """CARRIED INVARIANT (born green). The env-driven half of the same claim,
    exercised through the shipped resolver instead of asserted about it.

    NL-141/147 fix loop 1 (QA F-2, smaller sibling): a `NEWSLENS_LANE_FALLBACK`
    entry used to sit in this list and was INERT — `resolve_seat` reads
    NEWSLENS_LANE, NEWSLENS_LANE_<SEAT> and NEWSLENS_MODEL_<SEAT> only, so the
    armed-fallback case was never staged here and the row bought nothing. The
    fix is to the TEST, not the code: the fallback belongs to `effective_seat`,
    and it is pinned there by test_a_lane_fall_over_never_substitutes_the_model.
    Teaching `resolve_seat` to honour the fallback so a test could pass would
    have moved the single-fall decision out of the one function that labels it."""
    envs = [
        {},
        {"NEWSLENS_LANE": "api"},
        {"NEWSLENS_LANE": "subscription"},
    ]
    for seat in llm.SEATS:
        envs.append({f"NEWSLENS_LANE_{seat.upper()}": "api"})
    for env in envs:
        for seat in llm.SEATS:
            model = llm.resolve_seat(seat, env).model
            assert "haiku" not in model.lower(), (env, seat, model)


# ===========================================================================
# 3. The doctor — what the principal actually reads
# ===========================================================================

def _claude_stub(tmp_path):
    """A `claude` that only knows --version. The subscription section spawns
    exactly that and never `-p` (the no-spend law, pinned in the B3 QA file);
    this stub is here so the probe-design WARN can be rendered without one."""
    stub = tmp_path / "claude"
    stub.write_text("#!/bin/sh\necho '2.1.212 (NL-147 stub)'\n", encoding="utf-8")
    stub.chmod(0o755)
    return str(stub)


def _doctor_surfaces(tmp_path):
    """Every surface that names a model, rendered — with the stub's own path
    scrubbed out of the text.

    The scrub is not cosmetic and it caught itself: the subscription section
    echoes the resolved binary path, pytest's `tmp_path` embeds the TEST NAME,
    and this test is named ..._no_haiku — so the first run failed on its own
    filename appearing in the doctor's output. An assertion that can be tripped
    by what you called the test is not measuring the product. (NL-156 sprang the
    same trap a second time from the other side: the sandbox's stand-in for
    CLAUDE_BIN_DEFAULT was briefly a tmp_path child, and the lane-FAIL text
    echoed it. The stand-in is a fixed name-free constant in conftest now.)

    NL-156: the lane surface is rendered against a DECLARED binary. It was `{}`,
    which reached os.environ behind the mapping; left as `{}` it would now
    render seven binary-gate FAILs and this pin would be reading the doctor's
    error path rather than its lane map."""
    bin_path = _claude_stub(tmp_path)
    surfaces = {
        "anthropic-key (keyless)": doctor.check_anthropic_key({}),
        "llm lanes": doctor.check_llm_lanes(sandbox_bin_env()),
        "subscription probe design": doctor.check_subscription_lane(
            {"NEWSLENS_CLAUDE_BIN": bin_path,
             "NEWSLENS_DOCTOR_SUBSCRIPTION_PROBE": "1"}),
    }
    return {label: [r.text.replace(bin_path, "<claude>") for r in results]
            for label, results in surfaces.items()}


def test_the_doctor_names_no_haiku_model_on_any_surface(tmp_path, no_network):
    """BORN RED at edf5ad4, on three surfaces at once:

      * the keyless ANTHROPIC_API_KEY FAIL said "the Claude API lane (Haiku 4.5)"
      * the lane-map INFO said "rank/editor/script/state Haiku, writer Opus,
        analyst Sonnet" — the pre-ENG-M0 map, two seat batches stale
      * the opt-in auth probe RECOMMENDED
        `claude -p --output-format json --model claude-haiku-4-5`, i.e. handed
        the principal a command to run an outlawed model himself

    The third is why this pin reads the doctor rather than the source: a stale
    comment is untidy, but a stale INSTRUCTION is the org telling its principal
    to break its own law. `no_network` proves the surfaces are rendered without
    a single connect."""
    for label, texts in _doctor_surfaces(tmp_path).items():
        for text in texts:
            assert "haiku" not in text.lower(), (
                f"doctor surface {label!r} still names Haiku: {text}")
    assert no_network == []


@pytest.mark.parametrize("model,cheapest", [
    ("claude-fable-5", "claude-fable-5"),
    ("claude-mythos-5", "claude-mythos-5"),
])
def test_the_doctor_reads_its_seat_map_off_the_table(model, cheapest, tmp_path,
                                                     monkeypatch, no_network):
    """BORN RED at edf5ad4 — THE MUTATION PIN, and the one that makes the pin
    above mean something.

    "No Haiku in the doctor's prose" is satisfiable by retyping the sentence with
    today's models in it, which is precisely the fix that failed twice already:
    the prose is only safe if it is DERIVED. So swap the seat table for one
    naming a model that appears nowhere in this repo's source, and require the
    rendered prose to follow. Hand-typed text cannot."""
    fake = {name: dataclasses.replace(cfg, model=model)
            for name, cfg in llm.SEATS.items()}
    monkeypatch.setattr(llm, "SEATS", fake)

    lanes = " ".join(r.text for r in doctor.check_llm_lanes({}))
    assert model in lanes, (
        "the lane-map summary did not follow the seat table — it is hand-typed "
        "prose again, and it will go stale the next time a seat moves")

    probe = " ".join(r.text for r in doctor.check_subscription_lane(
        {"NEWSLENS_CLAUDE_BIN": _claude_stub(tmp_path),
         "NEWSLENS_DOCTOR_SUBSCRIPTION_PROBE": "1"}))
    assert f"--model {cheapest}" in probe, (
        "the auth-probe design named a model the seat table does not carry")
    assert no_network == []


# ===========================================================================
# 4. What the purge must NOT take
# ===========================================================================

def test_the_ledger_keeps_its_haiku_price_row():
    """CARRIED INVARIANT (born green) — and a pin AGAINST an over-eager future
    sweep, which is the failure mode a purge invites.

    `battery._ARM_PRICES` maps a model id to its rate so the battery can report
    each arm at ITS real cost and so already-written ledger rows can be read
    back honestly. Editions generated under the Haiku seats are in the record at
    $1.00/$5.00. Deleting this row would not un-spend that money; it would make
    the ledger fall back to the writer seat's Opus rates and silently restate
    history at 5x. The no-Haiku law governs what may RUN."""
    assert battery._ARM_PRICES["claude-haiku-4-5"] == (1.0, 5.0)
    assert battery._arm_prices("claude-haiku-4-5") == (1.0, 5.0)


def test_an_unknown_model_still_falls_back_to_the_writer_seat():
    """The other half of the row above: the fallback is what a DELETED row would
    have silently handed the ledger, so it is worth seeing next to it."""
    w = llm.SEATS["writer"]
    assert battery._arm_prices("no-such-model-9") == (
        w.usd_per_mtok_in, w.usd_per_mtok_out)
