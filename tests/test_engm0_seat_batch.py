"""ENG-M0 — the full seat batch, pinned.

28 of 33 tests here are BORN RED at 872113e (the HEAD this batch was cut from);
5 are labelled carried-invariants (born green — the list is in the build record
§COMPLETION). The born-red run is quoted in
research/2026-08-06--engm0-build.md. Two classes:

  * CONSTANTS pins (the seat map, the walls, the bands, the cap, the rank output
    budget). These are the "derive from SEATS or die" contract made testable —
    a seat row edited without its downstream constants moving is what they catch.
  * BEHAVIOR pins (C-5). The slot-3 demoted-quick verdict row surviving budget
    exhaustion is a real bug closed, and its pin fails at HEAD for the right
    reason: the row is absent, not merely different.
"""
from __future__ import annotations

import sqlite3

import pytest

from newslens import analysis, config, generate, llm, memory_core, ranking


# ---------------------------------------------------------------------------
# The ruled seat map (2026-08-02 rank ruling + 2026-08-06 (b) and amendment)
# ---------------------------------------------------------------------------

RULED_SEATS = {
    "rank":    ("claude-sonnet-5", "subscription", "adaptive", "high",   False),
    "analyst": ("claude-opus-4-8", "subscription", "adaptive", "high",   False),
    "writer":  ("claude-opus-4-8", "subscription", "adaptive", "xhigh",  False),
    "editor":  ("claude-opus-4-8", "subscription", "adaptive", "high",   False),
    "script":  ("claude-opus-4-8", "subscription", "adaptive", "high",   False),
    "state":   ("claude-opus-4-8", "subscription", "adaptive", "medium", False),
}


@pytest.mark.parametrize("seat,expected", sorted(RULED_SEATS.items()))
def test_the_ruled_seat_map_is_what_ships(seat, expected):
    """Model / lane / thinking / effort / sampling, per seat, as ruled."""
    cfg = llm.SEATS[seat]
    assert (cfg.model, cfg.lane, cfg.thinking, cfg.effort, cfg.sampling) == expected


def test_follow_altitude_is_the_untouched_flagged_exception():
    """The ONE seat the ruling put out of bounds: still Haiku, still no thinking,
    still the 8s/20s reader-facing walls. A future seat sweep that 'tidies' this
    row into the Opus family breaks the 8s UI wall it exists to protect."""
    cfg = llm.SEATS["follow_altitude"]
    assert cfg.model == "claude-haiku-4-5"
    assert cfg.thinking is None and cfg.effort is None
    assert cfg.sampling is True
    assert (cfg.timeout_s, cfg.timeout_sub_s) == (8, 20)


def test_synthesis_stays_dormant():
    assert "synthesis" in llm.DORMANT_SEATS
    assert llm.SEATS["synthesis"].model == "gpt-4o"


def test_every_flipped_seat_left_the_thinking_off_allowlist():
    """editor/script/state declared adaptive thinking; leaving them in the
    allowlist would inject MAX_THINKING_TOKENS=0 and silently cancel the flip —
    the seats would run Opus prices with Haiku-era deliberation suppressed."""
    assert llm._THINKING_OFF_SUB_SEATS == frozenset({"follow_altitude"})


def test_the_thinking_seam_actually_stops_injecting_for_flipped_seats():
    """Not a source-text assertion: build the child env the transport would
    build and check the var is absent for a flipped seat and present for the
    exception. This is the pin that bites if the frozenset is edited back."""
    parent = {"HOME": "/tmp/h", "PATH": "/usr/bin"}
    for seat in ("editor", "script", "state"):
        child = llm._subscription_env(parent, llm.SEATS[seat])
        assert llm._MAX_THINKING_TOKENS_VAR not in child, seat
    child = llm._subscription_env(parent, llm.SEATS["follow_altitude"])
    assert child[llm._MAX_THINKING_TOKENS_VAR] == "0"


# ---------------------------------------------------------------------------
# Derive-from-SEATS-or-die: the ladder re-prices with no hand edit
# ---------------------------------------------------------------------------

def test_every_price_constant_derives_from_its_seat_row():
    """The five derivation chains. Each reads model + both rates off SEATS, so a
    seat flip re-prices the ledger, the pre-call estimates and the budget floors
    automatically. A hand-frozen constant shows up here as a mismatch."""
    for module_name, seat, model_attr, in_attr, out_attr in (
        ("ranking", "rank", "RANK_MODEL",
         "RANK_USD_PER_MTOK_IN", "RANK_USD_PER_MTOK_OUT"),
        ("analysis", "analyst", "ANALYSIS_MODEL",
         "ANALYSIS_USD_IN_PER_MTOK", "ANALYSIS_USD_OUT_PER_MTOK"),
        ("generate", "writer", "WRITER_MODEL",
         "WRITER_USD_PER_MTOK_IN", "WRITER_USD_PER_MTOK_OUT"),
        ("memory_core", "state", "STATE_MODEL",
         "STATE_USD_IN_PER_MTOK", "STATE_USD_OUT_PER_MTOK"),
    ):
        mod = {"ranking": ranking, "analysis": analysis,
               "generate": generate, "memory_core": memory_core}[module_name]
        cfg = llm.SEATS[seat]
        assert getattr(mod, model_attr) == cfg.model, module_name
        assert getattr(mod, in_attr) == cfg.usd_per_mtok_in, module_name
        assert getattr(mod, out_attr) == cfg.usd_per_mtok_out, module_name
    # ranking's legacy aliases ride the same row.
    assert ranking.USD_PER_MTOK_IN == llm.SEATS["rank"].usd_per_mtok_in
    assert ranking.USD_PER_MTOK_OUT == llm.SEATS["rank"].usd_per_mtok_out


def test_the_analyst_out_rate_moved_the_brief_bound_by_derivation():
    """The chartered ladder re-price: the analyst's $15 -> $25 out-rate moves the
    brief's OUTPUT term $0.09 -> $0.15, and the slot-atomic floor with it. Derived
    from the shipped constants, never a frozen literal (NL-133 discipline)."""
    from newslens import paths
    template = (paths.PROMPTS_DIR / "analysis_brief.txt").read_text(encoding="utf-8")
    bound = analysis.brief_bound_usd(template)
    out_term = analysis.ANALYSIS_MAX_TOKENS / 1e6 * analysis.ANALYSIS_USD_OUT_PER_MTOK
    assert out_term == pytest.approx(0.15)
    # The bound is the input leg plus that output leg, and is strictly above it.
    assert bound > out_term
    # The floor rose: it must now exceed the pre-flip Sonnet-priced bound.
    sonnet_out_term = analysis.ANALYSIS_MAX_TOKENS / 1e6 * 15.0
    assert out_term > sonnet_out_term


# ---------------------------------------------------------------------------
# Re-measured walls, bands, budgets (item 2 + item 7 + item 4)
# ---------------------------------------------------------------------------

def test_the_remeasured_subscription_walls():
    walls = {s: llm.SEATS[s].timeout_sub_s
             for s in ("rank", "analyst", "writer", "editor", "script", "state")}
    # Every one MEASURED live on the subscription lane (2026-08-06 probe
    # battery); script/state were corrected UP after their owed probes showed
    # the first, derived values were 2.7x/3.0x rather than the family's >=3x.
    assert walls == {"rank": 600, "analyst": 720, "writer": 900,
                     "editor": 240, "script": 240, "state": 120}


def test_every_wall_outlasts_its_own_token_band():
    """The property the 07-26 down-tune established and this batch re-checks: a
    watchdog must never kill the very call its alarm exists to report. Derived
    from the shipped band + the measured throughput floors, not hand-frozen."""
    # The MEASURED floors (Opus 67.1 tok/s across n=5, Sonnet 111.0 across n=6).
    # 67 not 90: the editor is the fast end of the Opus band, and sizing the
    # walls off it was the error the owed probes corrected.
    THROUGHPUT_FLOOR = {"rank": 111.0, "editor": 67.0, "script": 67.0,
                        "state": 67.0}
    for seat, floor in THROUGHPUT_FLOOR.items():
        band = llm._TOKEN_BANDS[seat]
        wall = llm.SEATS[seat].timeout_sub_s
        assert band / floor < wall, (seat, band / floor, wall)


def test_the_remeasured_token_bands():
    assert llm._TOKEN_BANDS == {"editor": 10000, "script": 10000, "state": 4000,
                                "rank": 30000, "follow_altitude": 200}


@pytest.mark.parametrize("seat,measured_out", [
    ("editor", 5867), ("script", 4670), ("state", 2000), ("rank", 17425),
])
def test_a_normal_measured_call_never_trips_its_own_band(seat, measured_out):
    """THE DEFECT THIS PIN EXISTS FOR, caught by the owed probes and not by
    reasoning: the state band shipped at 1,500 against a seat whose real
    thinking-on call emits 2,000, so it would have fired on EVERY normal state
    rewrite. A band that always fires is not armor — it trains the reader to
    ignore the one trip that matters. Each figure below is a MEASURED live
    subscription-lane call on the shipped seat (2026-08-06)."""
    band = llm._TOKEN_BANDS[seat]
    assert measured_out < band, (seat, measured_out, band)
    assert band >= measured_out * 1.5, (
        f"{seat}: band {band} is under 1.5x its measured {measured_out} — "
        "normal variance will fire it")


def test_rank_band_fires_before_the_api_lane_would_truncate():
    """The new rank band is deliberately BELOW the output cap, so a runaway draw
    is WARNED before finish_reason=='length' kills it."""
    assert llm._TOKEN_BANDS["rank"] < ranking.MAX_COMPLETION_TOKENS


def test_the_rank_output_budget_covers_the_observed_ceiling():
    """item 7 — the api fall-over no longer truncates a real day. 22,748 is the
    all-time observed rank completion ceiling (ranking_runs, n=9)."""
    OBSERVED_CEILING = 22748
    assert ranking.MAX_COMPLETION_TOKENS >= OBSERVED_CEILING * 1.5


def test_the_bigger_rank_budget_puts_the_api_lane_on_the_streaming_path():
    """The load-bearing consequence: above llm._STREAM_MIN_MAX_TOKENS the api
    lane streams, which turns cfg.timeout_s from a total wall into an
    inter-event idle bound. At 3,000 rank was on the blocking path and a slow
    draw idle-died (the NL-93 class)."""
    assert ranking.MAX_COMPLETION_TOKENS > llm._STREAM_MIN_MAX_TOKENS
    assert llm._should_stream(llm.SEATS["rank"], ranking.MAX_COMPLETION_TOKENS)


def test_the_run_cap_clears_the_rank_pre_call_abort():
    """ranking's `est > cap` is a HARD ABORT before the call. The raised output
    budget raises that estimate, so the cap has to clear it or rank can never
    run — the two constants are coupled and this pin is the coupling."""
    prompt = "x" * 81000                      # a real 780-item prompt's size
    est = ranking.estimate_cost_usd(prompt)
    assert est < config.DEFAULT_BUDGET_CAP_USD_PER_RUN


def test_the_run_cap_covers_the_measured_post_flip_edition():
    """item 4. $2.832882 is the measured/derived post-flip 7-slot edition (the
    arithmetic is in config.py). The cap carries ~50% margin over it."""
    MEASURED_POST_FLIP_EDITION = 2.832882
    cap = config.DEFAULT_BUDGET_CAP_USD_PER_RUN
    assert cap >= MEASURED_POST_FLIP_EDITION * 1.4
    assert cap <= MEASURED_POST_FLIP_EDITION * 1.7


# ---------------------------------------------------------------------------
# C-5 — the free slot-3 verdict row survives budget exhaustion (ruling (a))
# ---------------------------------------------------------------------------

def _exhausted_slot3(con, date, monkeypatch):
    """Drive analyze_story for slot 3 with remaining_usd BELOW the brief bound,
    with both money sentinels armed: any model or Sonar call is a test failure."""
    def _no_chat(*a, **k):
        raise AssertionError("the model was called on the exhausted path")

    def _no_sonar(*a, **k):
        raise AssertionError("Sonar was called on the exhausted path")

    from newslens import paths
    template = (paths.PROMPTS_DIR / "analysis_brief.txt").read_text(encoding="utf-8")
    bound = analysis.brief_bound_usd(template)
    return analysis.analyze_story(
        con, date, 3, {"story_title": "T", "item_ids": []}, "medium",
        _cfg_stub(), "sk-x", "pplx-x",
        remaining_usd=bound / 2,          # strictly under the floor
        memory_lines=[], prior=[],
        fetch=lambda *a, **k: (_ for _ in ()).throw(
            AssertionError("no fetch expected")),
        chat=_no_chat, sonar=_no_sonar, sleep=lambda s: None)


class _cfg_stub:
    interests_broad: list = []
    interests_granular: list = []
    followed_analyst_sources: list = []
    sources: list = []


def test_c5_the_demoted_quick_verdict_row_survives_the_budget_floor(
        migrated_con, monkeypatch):
    """BORN RED at HEAD: the slot-atomic floor returned 64 lines above the
    demotion, so no row was written and `analyst_slot3_tier` returned None (the
    writer's silent A2 fallback). Ruling (a) 2026-08-06 restores the row."""
    con, date = migrated_con, "2026-08-06"
    sa = _exhausted_slot3(con, date, monkeypatch)

    # The run still discloses the derate — the row must not hide exhaustion.
    assert sa.outcome == "skipped-budget"
    assert any("derating" in w for w in sa.warnings)
    assert sa.slot3_verdict_under_floor is True

    # ...and the reader gets its verdict.
    assert analysis.analyst_slot3_tier(con, date) == "quick"
    row = con.execute(
        "SELECT status, reject_reason, cost_usd FROM analysis_briefs"
        " WHERE date = ? AND slot = 3 ORDER BY id DESC LIMIT 1", (date,)
    ).fetchone()
    assert row is not None, "no verdict row persisted under the floor"
    assert row["status"] == "rejected"
    assert row["reject_reason"].startswith("demoted-quick:")
    # FREE: the row costs nothing. That is the whole reason it may be written
    # on a path whose premise is that there is no money left.
    assert row["cost_usd"] == 0.0


# ---------------------------------------------------------------------------
# The pool raise + fair-fill (item 6)
# ---------------------------------------------------------------------------

class _Row(dict):
    """Stand-in for sqlite3.Row — fair_fill only ever reads by key."""


def _pool(stamps):
    """Rows newest-stamp-first, id DESC within a stamp — gather_items' order."""
    rows, next_id = [], 100000
    for stamp, n in stamps:
        for _ in range(n):
            rows.append(_Row(id=next_id, fetched_at=stamp))
            next_id -= 1
    return rows


def test_the_pool_raise_is_the_measured_number():
    """NL-142 measured 1.42x off 550; 550 * 1.42 = 781 -> 780."""
    assert ranking.MAX_INPUT_ITEMS == 780
    assert ranking.MAX_INPUT_ITEMS == pytest.approx(550 * 1.42, abs=1)


def test_fair_fill_stops_the_cap_evicting_whole_ingest_runs():
    """THE MEASURED PATHOLOGY, reproduced at the real shape: the founder DB's
    14-day window held 7 ingest stamps totalling 4,014 items, and the old
    `LIMIT 550` returned 550 rows of ONE stamp — six in-window runs evicted
    entirely, every day, silently."""
    rows = _pool([("2026-08-06T17:52Z", 574), ("2026-08-03T15:54Z", 583),
                  ("2026-08-02T20:02Z", 563), ("2026-08-01T16:35Z", 583),
                  ("2026-07-26T15:34Z", 561), ("2026-07-25T14:51Z", 585),
                  ("2026-07-24T22:28Z", 565)])
    old = ranking.fair_fill(rows, 550, 0)          # floor 0 == pre-ENG-M0
    assert len({r["fetched_at"] for r in old}) == 1, "the pathology, reproduced"

    new = ranking.fair_fill(rows, ranking.MAX_INPUT_ITEMS,
                            ranking.FAIR_FILL_MIN_PER_STAMP)
    assert len(new) == ranking.MAX_INPUT_ITEMS
    counts = {}
    for r in new:
        counts[r["fetched_at"]] = counts.get(r["fetched_at"], 0) + 1
    assert len(counts) == 7, "every in-window ingest run must be represented"
    # RECENCY IS NOT INVERTED: the newest run is carried WHOLE and every older
    # run gets at least the floor. This is the property that distinguishes the
    # floor from an equal quota (780/7 = 111 each would have inverted it).
    assert counts["2026-08-06T17:52Z"] == 574
    assert min(counts.values()) >= ranking.FAIR_FILL_MIN_PER_STAMP
    assert counts["2026-08-03T15:54Z"] > ranking.FAIR_FILL_MIN_PER_STAMP


def test_fair_fill_preserves_the_callers_row_order():
    """The cap decides membership, never sequence — the same contract
    _cap_cluster_items holds. build_prompt's ascending-id render and
    pool_composition's complement query both depend on it."""
    rows = _pool([("s2", 30), ("s1", 30)])
    out = ranking.fair_fill(rows, 40, 20)
    assert [r["id"] for r in out] == sorted((r["id"] for r in out), reverse=True)


@pytest.mark.parametrize("n_rows,cap", [(10, 550), (600, 550)])
def test_fair_fill_degenerate_cases_are_no_ops(n_rows, cap):
    """A window under the cap keeps everything; a ONE-stamp window reduces to
    the old `LIMIT cap` exactly — fair-fill may not change the common case."""
    rows = _pool([("only-stamp", n_rows)])
    assert ranking.fair_fill(rows, cap, 20) == rows[:min(n_rows, cap)]


def test_fair_fill_floor_zero_restores_pre_engm0_semantics():
    """The escape hatch, pinned: floor 0 is exactly `ORDER BY ... LIMIT cap`."""
    rows = _pool([("s3", 100), ("s2", 100), ("s1", 100)])
    assert ranking.fair_fill(rows, 150, 0) == rows[:150]


def test_fair_fill_returns_slack_from_short_stamps_to_the_recency_fill():
    """A stamp with fewer rows than the floor contributes what it has, and its
    unused slots go back to the newest stamp rather than shrinking the pool."""
    rows = _pool([("s2", 100), ("s1", 5)])
    out = ranking.fair_fill(rows, 50, 20)
    assert len(out) == 50                      # the cap is still filled
    counts = {}
    for r in out:
        counts[r["fetched_at"]] = counts.get(r["fetched_at"], 0) + 1
    assert counts["s1"] == 5                   # all it had
    assert counts["s2"] == 45                  # the slack came back here


def test_c5_does_not_demote_a_funded_slot3_before_sonar_gets_its_chance(
        migrated_con):
    """The regression the naive fix causes, pinned. `sonar_results` is empty
    until rung 1 runs, so a demotion predicate hoisted UNCONDITIONALLY above the
    floor reads `len([]) < 2` as true and demotes every slot-3 medium on the
    FUNDED path too. Here the slot is funded, so the exhausted-path verdict must
    NOT fire."""
    con, date = migrated_con, "2026-08-07"
    from newslens import paths
    template = (paths.PROMPTS_DIR / "analysis_brief.txt").read_text(encoding="utf-8")
    bound = analysis.brief_bound_usd(template)

    calls = {"sonar": 0}

    def _sonar(*a, **k):
        calls["sonar"] += 1
        return [], 0.0, "ok"

    sa = analysis.analyze_story(
        con, date, 3, {"story_title": "T", "item_ids": []}, "medium",
        _cfg_stub(), "sk-x", "pplx-x",
        remaining_usd=bound * 10,        # comfortably funded
        memory_lines=[], prior=[],
        fetch=lambda *a, **k: [],
        chat=lambda *a, **k: (_ for _ in ()).throw(
            AssertionError("should not reach synthesis in this pin")),
        sonar=_sonar, sleep=lambda s: None)

    # The funded path reached rung 1 (Sonar was offered its chance)...
    assert calls["sonar"] == 1
    # ...and the exhausted-path flag never fired.
    assert sa.slot3_verdict_under_floor is False
    assert sa.outcome != "skipped-budget"
