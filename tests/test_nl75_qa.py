"""NL-75 Phase 1 — QA extension pass (trust-machinery milestone, full teeth).

Scope (dispatch 2026-07-16): rung-(a) ledger->writer plumbing, the Forward-Claim
Rules, the expiry register, migrations 0011-0013, the edition_dates sweep.
Binding texts: Content council 2026-07-16 (Forward-Claim Rules i-v), HSR
baseline §5.1(2) (the poisoned-antecedent trap), DECISIONS 2026-07-16
(moat-strategy rulings A-D), team/ENGINEERING.md (wiring-proof law).

Accounting — this file ships 28 GREEN tests plus NINE RED acceptance contracts
(house pattern: a red is the fix's acceptance criterion; the fix contract lives
in its docstring; the implementer flips it, never QA):

  RED-1/RED-2  test_thread_timeline_strikes_superseded_rows /
               test_deep_view_timeline_strikes_superseded_rows      (defect D1)
  RED-3        test_same_thread_on_two_slots_writes_one_conversion  (defect D2)
  RED-4        test_possessive_apostrophe_is_not_source_attribution (defect D3a)
  RED-5        test_spec_lexicon_hyphenated_and_nth_time_forms_fire (defect D3b)
  RED-6        test_reshipped_observable_in_watch_for_is_flagged_not_resolved
                                                                    (defect D5)
  RED-7        test_no_threads_sample_carries_no_rung_a_blocks      (defect D4)
  RED-8        test_live_shape_thread_history_does_not_dilute_the_subject
                                                                    (defect D6)
  RED-9        test_0013_is_cleanly_separable_a_watchless_db_still_generates
                                                                    (defect D7)

Everything here is offline by construction (conftest autouse sandbox +
loopback guard); LLM passes run through a local fake `generate._chat`.
"""

from __future__ import annotations

import json
import shutil
import sqlite3
import time
from datetime import datetime, timezone

import pytest

from newslens import config, db, events, generate, memory_core, paths, server

EDITION = "2026-07-14"          # the live-exhibit date (HSR edition 5)
PRIOR_EDITION = "2026-07-10"    # the edition that raised the Switzerland watch


def iso_now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


# ---------------------------------------------------------------------------
# Seeding helpers (self-contained; shapes mirror tests/test_generate.py)
# ---------------------------------------------------------------------------

def _thread(con, topic="Strait of Hormuz"):
    con.execute("INSERT INTO memory (topic, status) VALUES (?, 'active')", (topic,))
    return con.execute("SELECT id FROM memory WHERE topic = ?",
                       (topic,)).fetchone()["id"]


def _delta(con, tid, date, what, signif="", slot=1):
    con.execute(
        "INSERT INTO thread_deltas (thread_id, edition_date, slot, verdict,"
        " what_happened, significance, cites_json)"
        " VALUES (?, ?, ?, 'advances', ?, ?, '[\"S1\"]')",
        (tid, date, slot, what, signif))
    return con.execute(
        "SELECT id FROM thread_deltas ORDER BY id DESC LIMIT 1").fetchone()["id"]


def _state(con, tid, as_of, text):
    con.execute(
        "INSERT INTO thread_state (thread_id, as_of_date, state_text)"
        " VALUES (?, ?, ?)", (tid, as_of, text))


def _supersede(con, delta_id, by_id, reason=""):
    con.execute(
        "INSERT INTO thread_delta_supersessions (delta_id, superseded_by, reason)"
        " VALUES (?, ?, ?)", (delta_id, by_id, reason))
    con.commit()


def _open_watch(con, tid, raised_on, observable, due):
    con.execute(
        "INSERT INTO watch_items (thread_id, edition_date, kind, observable,"
        " due_date) VALUES (?, ?, 'open', ?, ?)", (tid, raised_on, observable, due))
    con.commit()
    return con.execute(
        "SELECT id FROM watch_items ORDER BY id DESC LIMIT 1").fetchone()["id"]


def _slot(n, mem=(), title=None):
    return {
        "slot": n,
        "story_title": title or f"Story {n}",
        "summary": "What happened, in one line.",
        "item_ids": [n],
        "outlets": ["Outlet A", "Outlet B"],
        "matched_tags": [{"name": "AI regulation", "level": "topic"}],
        "matched_memory": list(mem),
        "matched_dormant": [],
        "followed_analyst": False,
        "personal_score": 1.0,
        "world_impact": 6,
        "world_impact_reason": "Sector-wide effects",
        "combined_score": 0.8,
        "override": False,
        "override_label": None,
        "corroboration_count": 2,
        "corroboration_label": "Reported by 2 named outlets",
        "wire_items_excluded": 0,
        "revived_threads": [],
    }


def _seed_edition(con, date, slots):
    con.execute(
        "INSERT INTO briefings (date, story_slots, corroboration_labels,"
        " token_cost, generated_at) VALUES (?, ?, ?, ?, ?)",
        (date, json.dumps(slots),
         json.dumps({"standing_caveat": "", "per_story": []}),
         json.dumps({"steps": [{"step": "rank_select", "usd": 0.001}],
                     "total_usd": 0.001}),
         iso_now()))
    for s in slots:
        con.execute(
            "INSERT OR IGNORE INTO source_items (id, source_type, outlet, url,"
            " title, fetched_at, raw_excerpt) VALUES (?, 'rss', ?, ?, ?, ?, ?)",
            (s["slot"], s["outlets"][0], f"https://x.example/{date}/{s['slot']}",
             s["story_title"], iso_now(), "An excerpt of the source item."))
    con.commit()


def _tier(i):
    return "full" if i == 1 else ("medium" if i in (2, 3) else "quick")


def _payload(slots, story_overrides=None):
    """A validator-compliant stories payload; `story_overrides` is a dict of
    slot-index (1-based) -> field overrides."""
    overrides = story_overrides or {}
    stories = []
    for i, s in enumerate(slots, start=1):
        story = {
            "tier": _tier(i),
            "headline": f"Rewritten headline {s['slot']}",
            "lede": ("The opening sentence reports the development. "
                     "A second sentence adds context."),
            "why_it_matters": ("It matters because of concrete effects on the "
                               "reader's interests."),
            "watch_for": "Watch the next scheduled decision.",
            "why_label": generate.WHY_FRAMINGS[(i - 1) % len(generate.WHY_FRAMINGS)],
            "watch_label": generate.WATCH_FRAMINGS[(i - 1) % len(generate.WATCH_FRAMINGS)],
        }
        story.update(overrides.get(i, {}))
        stories.append(story)
    return {"stories": stories}


def _script(slots):
    parts = ["Good morning. Here is your briefing."]
    for s in slots:
        parts.append(f"Story {s['slot']}. The development moved today.")
    parts.append(generate.SPOKEN_CAVEAT)
    parts.append(generate.SIGNOFF)
    slot_budget = (generate.SCRIPT_OPEN_WORDS + generate.SCRIPT_OUTRO_WORDS
                   + sum(generate.script_segment(int(s["slot"])) for s in slots))
    body_words = sum(len(p.split()) for p in parts)
    need = int(slot_budget * 0.85) - body_words
    if need > 0:
        parts.insert(1, " ".join(
            ["The detail continues in measured spoken prose."] * (need // 7 + 1)))
    return "\n\n".join(parts)


@pytest.fixture
def fake_model(monkeypatch):
    """Offline chat fake: json_mode -> narrative payload (2nd+ json call = the
    editor pass, echoing unless .editor set); non-json -> script. Records every
    call's prompt so liveness assertions run on the REAL assembled prompts."""
    state = type("S", (), {})()
    state.calls = []
    state.narrative = None
    state.editor = None
    state.script = None

    def fake_chat(key, prompt, max_tokens, temperature, json_mode):
        state.calls.append({"json_mode": json_mode, "prompt": prompt})
        if json_mode:
            n_before = sum(1 for c in state.calls[:-1] if c["json_mode"])
            payload = (state.narrative if n_before == 0
                       else (state.editor if state.editor is not None
                             else state.narrative))
            content = json.dumps(payload)
        else:
            content = state.script
        return {
            "choices": [{"finish_reason": "stop",
                         "message": {"content": content}}],
            "usage": {"prompt_tokens": 900, "completion_tokens": 200},
        }

    monkeypatch.setattr(generate, "_chat", fake_chat)
    monkeypatch.setattr(time, "sleep", lambda s: None)
    return state


ENV = {"OPENAI_API_KEY": "sk-qa-fake"}

_SWISS_OBS = ("The next round of U.S.-Iran talks in Switzerland on July 12 "
              "will indicate whether diplomatic channels remain viable")


def _seed_hormuz(con, with_expired_watch=True):
    """The HSR edition-5 shape: a thread with genuine PRIOR history and (opt.)
    the expired Switzerland watch-for raised by the prior edition."""
    tid = _thread(con)
    _delta(con, tid, "2026-07-05",
           "Iran offered special transit terms amid US fee objections",
           "the contest was over the terms of passage")
    _delta(con, tid, "2026-07-10",
           "Iran closed the strait after both sides traded strikes",
           "a war over passage itself")
    _state(con, tid, "2026-07-10",
           "The strait standoff escalated from a fee dispute (Jul 5) to closure"
           " (Jul 10).")
    if with_expired_watch:
        _open_watch(con, tid, PRIOR_EDITION, _SWISS_OBS, "2026-07-12")
    con.commit()
    return tid


# ===========================================================================
# A. END-TO-END LIVENESS — the whole chain through run_generate, offline
#    (dispatch item 1: the dedicated liveness test the trace-proof lacked)
# ===========================================================================

def test_e2e_offline_run_persists_watch_items_and_surfaces_forward_claim_warnings(
        migrated_con, fake_model):
    """THE liveness test for NL-75 Phase 1, end to end through run_generate:

    * rung (a): the narrative prompt the model actually received carries the
      MEMORY block (dated deltas + standing state) and the EXPIRED WATCH-FOR
      conversion demand;
    * Forward-Claim Rules fire on the shipped stories and SURFACE: rule i
      (stale watch-for date) and rule ii (expired item silently dropped) in
      report.warnings and in generation_log.jsonl (the diagnose feed).
      [Rule iii on the live thread shape is defect D6's RED — see
      test_live_shape_thread_history_does_not_dilute_the_subject.]
    * the expiry register persists this edition's watch-fors as open rows and
      the unconverted debt survives the run.

    The story fixture deliberately shares NO salient unit with the expired
    Switzerland observable (including the bare number '12' — subject matching
    is substring-on-units, so any '12' in prose would 'address' the promise;
    pinned in test_numeric_unit_promiscuity_pinned_as_built).

    Bite-proof (hash-verified comment-out procedure, this QA pass): with the
    forward_claim_findings call or the thread_ledger prompt injection
    commented out in generate.py, this test FAILS — see the QA report."""
    con = migrated_con
    tid = _seed_hormuz(con)
    # The poison: a same-day backfill row echoing edition-day source diction.
    _delta(con, tid, EDITION, "U.S. reinstated a naval blockade of the strait")
    slots = [_slot(1, mem=("Strait of Hormuz",)), _slot(2), _slot(3)]
    _seed_edition(con, EDITION, slots)
    fake_model.narrative = _payload(slots, {
        1: {"lede": ("The United States blockaded the strait Tuesday. "
                     "Oil prices surged on the news."),
            # rule i: a stale date shipped as forward-looking (no unit shared
            # with the Switzerland observable)
            "watch_for": "Watch the coalition vote on July 8."},
        # no slot's prose references the Switzerland observable -> rule ii debt
    })
    fake_model.script = _script(slots)

    rep = generate.run_generate(date=EDITION, con=con, env=ENV, refresh=False)
    assert rep.sample is False   # the edition of record, not a sample

    # -- rung (a): the prompt the writer actually saw --------------------
    n_prompt = next(c for c in fake_model.calls if c["json_mode"])["prompt"]
    assert "MEMORY — the record for thread 'Strait of Hormuz'" in n_prompt
    assert "Jul 5" in n_prompt and "Jul 10" in n_prompt
    assert "escalated from a fee dispute" in n_prompt.lower()
    assert "EXPIRED WATCH-FOR you flagged on 2026-07-10" in n_prompt
    assert "NEVER re-ship it" in n_prompt
    # strict before_date: today's own backfill delta is NOT in the block
    assert "reinstated a naval blockade of the strait" not in n_prompt.split(
        "EXPIRED WATCH-FOR")[0]

    # -- Forward-Claim findings i + ii, surfaced ---------------------------
    joined = " | ".join(rep.warnings)
    assert "2026-07-08" in joined                            # rule i
    assert "not future-relative" in joined
    assert "NOT converted" in joined                         # rule ii

    # -- the expiry register: this edition's promises persisted -----------
    opens = con.execute(
        "SELECT slot, due_date, observable FROM watch_items WHERE kind='open'"
        " AND edition_date = ? ORDER BY slot", (EDITION,)).fetchall()
    assert len(opens) == 3                    # every slot's watch_for
    assert opens[0]["due_date"] == "2026-07-08"   # parsed from the prose
    # the silently-dropped expired item is STILL an unconverted debt
    assert memory_core.expired_unconverted_watch_items(
        con, "Strait of Hormuz", "2026-07-15") != []

    # -- the warnings reached the log (diagnose's feed) --------------------
    log = (paths.DATA_DIR / "generation_log.jsonl").read_text(encoding="utf-8")
    entry = json.loads(log.strip().splitlines()[-1])
    assert entry["status"] == "ok"
    assert any("not future-relative" in w for w in entry["warnings"])
    assert any("NOT converted" in w for w in entry["warnings"])


def test_e2e_conversion_recorded_and_idempotent_across_regenerates(
        migrated_con, fake_model):
    """When the prose CONVERTS the expired item (exemplar C: silence reported),
    the register writes exactly one conversion row closing the open item —
    and a re-generate of the same edition adds NOTHING (open rows dedup on
    (briefing_id, slot, kind); the converted debt never re-enters)."""
    con = migrated_con
    _seed_hormuz(con)
    slots = [_slot(1, mem=("Strait of Hormuz",)), _slot(2), _slot(3)]
    _seed_edition(con, EDITION, slots)
    fake_model.narrative = _payload(slots, {
        1: {"lede": ("The Switzerland talks this briefing flagged on July 10 "
                     "have come and gone without a mention in today's "
                     "reporting. The record still holds the sequence."),
            "watch_for": "Watch whether any outlet reports the talks' fate "
                         "by July 20."}})
    fake_model.script = _script(slots)

    generate.run_generate(date=EDITION, con=con, env=ENV, refresh=False)
    conv = con.execute(
        "SELECT kind, converts FROM watch_items WHERE kind != 'open'").fetchall()
    assert [c["kind"] for c in conv] == ["unanswered"]
    assert memory_core.expired_unconverted_watch_items(
        con, "Strait of Hormuz", EDITION) == []
    n_open_1 = con.execute(
        "SELECT COUNT(*) c FROM watch_items WHERE kind='open'").fetchone()["c"]

    # Re-generate the same edition: nothing duplicates.
    generate.run_generate(date=EDITION, con=con, env=ENV, refresh=False)
    n_open_2 = con.execute(
        "SELECT COUNT(*) c FROM watch_items WHERE kind='open'").fetchone()["c"]
    n_conv_2 = con.execute(
        "SELECT COUNT(*) c FROM watch_items WHERE kind != 'open'").fetchone()["c"]
    assert n_open_2 == n_open_1
    assert n_conv_2 == 1


def test_same_thread_on_two_slots_writes_one_conversion(migrated_con, fake_model):
    """[RED-3 — acceptance contract, defect D2] One expired promise, one
    conversion row. Today: when the SAME thread matches two slots, the expired
    item rides both slots' `expired_watch`, and the post-persist register loop
    writes a conversion row PER SLOT whose prose references the observable —
    duplicate (and potentially contradictory: 'resolved' + 'unanswered')
    conversion records for a single promise, double-counting Data's proposed
    expired-watch-for conversion-rate metric.

    FIX CONTRACT: at most one conversion row may close an open item — dedup at
    record time (record_watch_conversion refuses/skips when a conversion row
    for open_item['id'] already exists, or the run loop tracks converted ids).
    The append-only table needs no schema change for the skip."""
    con = migrated_con
    _seed_hormuz(con)
    slots = [_slot(1, mem=("Strait of Hormuz",)),
             _slot(2, mem=("Strait of Hormuz",)), _slot(3)]
    _seed_edition(con, EDITION, slots)
    convert_lede = ("The Switzerland talks this briefing flagged have come and "
                    "gone without a mention in today's reporting.")
    fake_model.narrative = _payload(slots, {
        1: {"lede": convert_lede}, 2: {"lede": convert_lede}})
    fake_model.script = _script(slots)

    generate.run_generate(date=EDITION, con=con, env=ENV, refresh=False)
    open_id = con.execute(
        "SELECT id FROM watch_items WHERE kind='open' AND edition_date = ?",
        (PRIOR_EDITION,)).fetchone()["id"]
    conv = con.execute(
        "SELECT COUNT(*) c FROM watch_items WHERE converts = ?",
        (open_id,)).fetchone()["c"]
    assert conv == 1, (
        f"one promise, {conv} conversion rows — the register double-counts "
        "when one thread matches two slots (defect D2)")


def test_no_threads_sample_carries_no_rung_a_blocks(migrated_con, fake_model):
    """[RED-7 — acceptance contract, defect D4] The cold-start sample's
    contract (ADR-0007 amendment, pinned by test_generate's no-threads test):
    'every thread/memory trace is stripped'. The strip copies slots with
    matched_memory/revived_threads emptied — but rung (a) attached
    `thread_ledger` and `expired_watch` BEFORE the strip, and the copy carries
    both keys through, so the no-threads sample's prompt now ships the MEMORY
    block and the EXPIRED WATCH-FOR demand (and forward-claim conversion
    warnings fire against a sample that stripped its threads). The existing
    no-threads pin misses this because its fixture thread has no ledger.

    FIX CONTRACT: the no_threads copy also empties the rung-(a) keys
    ({**s, "matched_memory": [], "revived_threads": [], "thread_ledger": "",
    "expired_watch": []}) — the cold-start view is thread-free again."""
    con = migrated_con
    _seed_hormuz(con)
    slots = [_slot(1, mem=("Strait of Hormuz",)), _slot(2), _slot(3)]
    _seed_edition(con, EDITION, slots)
    fake_model.narrative = _payload(slots)
    fake_model.script = _script(slots)

    generate.run_generate(date=EDITION, con=con, env=ENV, refresh=False,
                          no_threads=True)
    n_prompt = next(c for c in fake_model.calls if c["json_mode"])["prompt"]
    assert "MEMORY — the record for thread" not in n_prompt, (
        "no-threads sample leaked the rung-(a) MEMORY block (defect D4)")
    assert "EXPIRED WATCH-FOR" not in n_prompt, (
        "no-threads sample leaked the expired-watch conversion demand (D4)")


# ===========================================================================
# B. THE POISONED-ANTECEDENT COUNTER UNDER FIRE (HSR §5.1(2), BINDING)
# ===========================================================================

def test_the_exact_trap_multiple_same_day_backfill_rows_and_state(migrated_con):
    """The trap AS FOUND: deltas 5-6 (two same-day backfill rows, both echoing
    'reinstated a naval blockade') AND poisoned state rows. Nothing predates
    the edition; nothing may license the word. has_predating_antecedent must
    refuse — states are not antecedents, and same-day rows never predate."""
    con = migrated_con
    tid = _thread(con)
    _delta(con, tid, EDITION, "U.S. reinstated a naval blockade of the strait",
           "economic dispute became direct military confrontation")
    _delta(con, tid, EDITION, "Iran answered the reinstated blockade with "
           "strikes on U.S. assets", slot=2)
    _state(con, tid, EDITION,
           "The U.S. is reinstating a naval blockade of the strait (Jul 14).")
    con.commit()
    assert memory_core.has_predating_antecedent(
        con, "Strait of Hormuz", {"blockade"}, EDITION) is False


def test_strict_predate_boundary_bites_on_the_edition_date_itself(migrated_con):
    """The boundary, all three sides: a row dated edition-1 licenses; a row
    dated THE EDITION DATE does not (strict predate — the same-day backfill
    class); a row dated AFTER the edition (hostile/clock-skew shape) does not."""
    con = migrated_con
    tid = _thread(con)
    day_before = _delta(con, tid, "2026-07-13", "U.S. imposed a naval blockade")
    con.commit()
    assert memory_core.has_predating_antecedent(
        con, "Strait of Hormuz", {"blockade"}, EDITION) is True
    # shift the row onto the edition date: same fact, no longer predating
    con2 = migrated_con
    tid2 = _thread(con2, "Boundary Thread")
    _delta(con2, tid2, EDITION, "U.S. imposed a naval blockade")
    con2.commit()
    assert memory_core.has_predating_antecedent(
        con2, "Boundary Thread", {"blockade"}, EDITION) is False
    tid3 = _thread(con2, "Future Thread")
    _delta(con2, tid3, "2026-07-15", "U.S. imposed a naval blockade")
    con2.commit()
    assert memory_core.has_predating_antecedent(
        con2, "Future Thread", {"blockade"}, EDITION) is False
    assert day_before  # silence the unused warning


def test_unrelated_predating_history_plus_poisoned_same_day_still_refuses(
        migrated_con):
    """Splitting the trap: genuine predating history WITHOUT the subject plus
    a same-day row WITH the subject — neither leg licenses; the check must
    join subject and predate on the SAME row."""
    con = migrated_con
    tid = _thread(con)
    _delta(con, tid, "2026-07-05", "Iran offered special transit terms")
    _delta(con, tid, EDITION, "U.S. reinstated a naval blockade")
    con.commit()
    assert memory_core.has_predating_antecedent(
        con, "Strait of Hormuz", {"blockade"}, EDITION) is False


def test_live_shape_thread_history_does_not_dilute_the_subject(migrated_con):
    """[RED-8 — acceptance contract, defect D6 — the HIGH one] The live call
    site defeats the poisoned-antecedent counter on exactly the HSR shape it
    was built from. repetition_antecedent_findings passes the WHOLE SENTENCE's
    salient units as the antecedent subject; on a thread with any real prior
    history, mundane shared words license the repetition word. The canonical
    07-14 sentence ('The United States reinstated a naval blockade of the
    strait Tuesday.') yields units {'united','states','reinstated','naval',
    'blockade','strait','tuesday'} — and the thread's genuine 07-10 closure
    row contains 'strait', so has_predating_antecedent returns True with ZERO
    prior blockade anywhere: the exact §5.1(2) false hit, alive in the live
    path. The implementer's poisoned-antecedent fixtures only cover day-one
    threads (empty prior set), which is why their tests pass.

    FIX CONTRACT: the subject passed to has_predating_antecedent must
    discriminate the repetition's OBJECT, not echo the sentence — e.g. salient
    units drawn from a bounded window AFTER the repetition match (the thing
    being re-X'd), falling back to the full sentence only when the window has
    no units; thread-topic words alone must not license. Acceptance: this test
    flags the sentence below while
    test_predating_antecedent_licenses_the_word (implementer's, green today)
    stays green."""
    con = migrated_con
    tid = _thread(con)
    # the REAL thread-10 priors — history that shares 'strait' but holds no
    # blockade antecedent
    _delta(con, tid, "2026-07-05",
           "Iran offered special transit terms amid US fee objections",
           "the contest was over the terms of passage")
    _delta(con, tid, "2026-07-10",
           "Iran closed the strait after both sides traded strikes",
           "a war over passage itself")
    # the poison: today's backfill echoing source diction
    _delta(con, tid, EDITION, "U.S. reinstated a naval blockade of the strait")
    con.commit()
    stories = [{"headline": "US blockades the strait",
                "lede": ("The United States reinstated a naval blockade of "
                         "the strait Tuesday."),
                "why_it_matters": "Oil prices surged."}]
    slots = [{"slot": "1", "matched_memory": ["Strait of Hormuz"]}]
    findings = generate.repetition_antecedent_findings(
        con, stories, slots, EDITION)
    assert any("reinstated" in f.lower() for f in findings), (
        "the live-shape thread history ('strait' in a prior row) licensed "
        "'reinstated' with no prior blockade on record — subject dilution "
        "defeats HSR §5.1(2)'s hardening (defect D6)")


def test_numeric_only_match_never_closes_a_debt():
    """CONSCIOUS FLIP per this pin's own docstring pre-authorization ("flips
    or dies consciously with that fix") — gate FIX-4, NL-75 milestone review.
    WAS: bare '12' anywhere in a body classified the Switzerland promise
    'resolved' (pinned as-built). NOW: numeric-only matches leave the debt
    OPEN (None) — the omission warning fires and the demand repeats next
    edition; non-numeric units carry the reference test. Genuine conversion
    prose still classifies (sibling test below)."""
    assert memory_core.classify_conversion(
        _SWISS_OBS, "The index rose 12 percent on the day.") is None


def test_genuine_conversion_prose_still_classifies_after_fix4():
    """FIX-4's guard must not break real conversions: prose that actually
    addresses the observable's non-numeric substance classifies."""
    assert memory_core.classify_conversion(
        _SWISS_OBS,
        "The Switzerland talks came and went without a mention in "
        "today's coverage.") is not None


def test_curly_quote_attribution_is_recognized_as_the_legal_middle_state(
        migrated_con):
    """Typographically-set prose attributes with curly quotes — the legal
    middle state must survive typography (the marker list is not ASCII-only)."""
    con = migrated_con
    tid = _thread(con)
    _delta(con, tid, EDITION, "U.S. reinstated a naval blockade")   # poison only
    con.commit()
    stories = [{"headline": "Blockade",
                "lede": ("The U.S. blockaded the strait — a step today’s "
                         "reports call “reinstated,” though no earlier "
                         "blockade appears in this record."),
                "why_it_matters": "x"}]
    slots = [{"slot": "1", "matched_memory": ["Strait of Hormuz"]}]
    assert generate.repetition_antecedent_findings(
        con, stories, slots, EDITION) == []


# ===========================================================================
# C. ADVERSARIAL CALIBRATION — _REPETITION_RE + attribution (dispatch item 2)
# ===========================================================================

def test_possessive_apostrophe_is_not_source_attribution(migrated_con):
    """[RED-4 — acceptance contract, defect D3a] `_is_source_attributed`
    counts any apostrophe/quote CHARACTER as attribution. 32% of the shipped
    editions' prose sentences contain an apostrophe (measured read-only on the
    real DB this pass) — a possessive alone ("Tehran's") currently launders an
    unattributed repetition word past rule iii. The canonical 07-14 sentence
    happens to be apostrophe-free, so the shipped exhibit is caught — but its
    nearest rephrasing is not.

    FIX CONTRACT: attribution requires an attribution FRAME, not a quote byte:
    keep the verb/frame markers ("said", "reports call it", "according to",
    "per ", ...); a bare quote character counts only when the repetition word
    itself sits INSIDE a quoted span (opening+closing pair around the match).
    A possessive apostrophe alone must not attribute."""
    con = migrated_con
    tid = _thread(con)
    _delta(con, tid, EDITION, "U.S. reinstated a naval blockade")  # same-day only
    con.commit()
    stories = [{"headline": "Blockade",
                "lede": ("Washington reinstated the blockade after Tehran's "
                         "latest threats."),
                "why_it_matters": "x"}]
    slots = [{"slot": "1", "matched_memory": ["Strait of Hormuz"]}]
    findings = generate.repetition_antecedent_findings(con, stories, slots, EDITION)
    assert any("reinstated" in f.lower() for f in findings), (
        "a possessive apostrophe alone counted as source attribution (D3a) — "
        "the unattributed 'reinstated' class sailed through")


def test_spec_lexicon_hyphenated_and_nth_time_forms_fire(migrated_con):
    """[RED-5 — acceptance contract, defect D3b] Content rule iii enumerates
    the lexicon as 'reinstated, again, resumed, renewed, re-imposed, once
    more, for the Nth time'. Two spec-listed members never fire as built:
    the hyphenated re- forms ('re-imposed' — news copy hyphenates freely; HSR
    §5.1(4) found the unhyphenated sibling) and the for-the-Nth-time class
    ('for the third time in a week').

    FIX CONTRACT: _REPETITION_RE gains optional hyphens on the re- stems
    (re-?instat…, re-?impos…, re-?open…) and a bounded Nth-time alternative
    (for the (second|third|fourth|fifth|\\d+(st|nd|rd|th)) time)."""
    con = migrated_con
    _thread(con)
    con.commit()
    slots = [{"slot": "1", "matched_memory": ["Strait of Hormuz"]}]
    hyphenated = [{"headline": "Sanctions",
                   "lede": "The country re-imposed sanctions on the sector.",
                   "why_it_matters": "x"}]
    nth_time = [{"headline": "Strikes",
                 "lede": "Strikes hit the region for the third time in a week.",
                 "why_it_matters": "x"}]
    f1 = generate.repetition_antecedent_findings(con, hyphenated, slots, EDITION)
    f2 = generate.repetition_antecedent_findings(con, nth_time, slots, EDITION)
    assert f1, "hyphenated 're-imposed' (spec-listed) did not fire (D3b)"
    assert f2, "'for the third time' (spec's Nth-time class) did not fire (D3b)"


def test_repetition_regex_boundary_calibration_as_built():
    """Calibration pins (GREEN, as-built): word boundaries hold ('against'
    and 'renewable' never fire); the known FP class is on record — 'pushed
    back on' fires 'back on' (an idiom, not a continuity claim). Warn-grade,
    so over-warning is the safe direction; logged for Content as validator
    noise to weigh, not a code defect."""
    assert generate._REPETITION_RE.search("They argued against the plan.") is None
    assert generate._REPETITION_RE.search("Renewable capacity grew fast.") is None
    m = generate._REPETITION_RE.search("Officials pushed back on the criticism.")
    assert m and m.group(0) == "back on"   # documented FP class, as built


# ===========================================================================
# D. RUNG (a) CONTENT HONESTY (dispatch item 5)
# ===========================================================================

def test_writer_context_truncation_keeps_the_newest_five(migrated_con):
    """N=5 takes the NEWEST five: with 8 prior deltas, the three oldest are
    absent and the five newest all present, oldest-first within the block."""
    con = migrated_con
    tid = _thread(con)
    for d in range(2, 10):   # 07-02 .. 07-09, eight priors
        _delta(con, tid, f"2026-07-{d:02d}", f"turn number {d} of the arc")
    con.commit()
    block = memory_core.writer_thread_context(
        con, "Strait of Hormuz", before_date=EDITION)
    for d in (2, 3, 4):
        assert f"turn number {d} " not in block
    for d in (5, 6, 7, 8, 9):
        assert f"turn number {d} " in block
    # oldest-first among the shown five
    assert block.index("turn number 5 ") < block.index("turn number 9 ")


def test_writer_context_state_without_prior_deltas_is_state_only(migrated_con):
    """A thread whose only deltas are today's own but whose state predates the
    edition: the block carries the standing state and NO 'record so far'
    section — an honest partial, never fabricated ledger lines."""
    con = migrated_con
    tid = _thread(con)
    _delta(con, tid, EDITION, "today's own turn — post-generation backfill")
    _state(con, tid, "2026-07-10", "The standoff holds (Jul 10).")
    con.commit()
    block = memory_core.writer_thread_context(
        con, "Strait of Hormuz", before_date=EDITION)
    assert "standing state (as of Jul 10" in block
    assert "record so far" not in block
    assert "today's own turn" not in block


def test_writer_context_todays_state_is_not_prior_state(migrated_con):
    """Strict on BOTH clocks of the block: a state row as_of the edition date
    itself (a same-day regeneration shape — run 1's memory pass already wrote
    today's state) is NOT prior coverage; with no earlier state the block says
    'none on record yet'."""
    con = migrated_con
    tid = _thread(con)
    _delta(con, tid, "2026-07-10", "a genuine prior turn")
    _state(con, tid, EDITION, "Today's own synthesized state (Jul 14).")
    con.commit()
    block = memory_core.writer_thread_context(
        con, "Strait of Hormuz", before_date=EDITION)
    assert "none on record yet" in block.lower()
    assert "Today's own synthesized state" not in block


def test_strict_before_date_bites_the_day_after(migrated_con):
    """Prove the exclusions are date-driven, not accidental: the same rows
    excluded from the edition's own context DO appear one edition later."""
    con = migrated_con
    tid = _thread(con)
    _delta(con, tid, EDITION, "the edition-day turn")
    _state(con, tid, EDITION, "The edition-day state (Jul 14).")
    con.commit()
    same_day = memory_core.writer_thread_context(
        con, "Strait of Hormuz", before_date=EDITION)
    next_day = memory_core.writer_thread_context(
        con, "Strait of Hormuz", before_date="2026-07-15")
    assert same_day == ""                                   # nothing prior
    assert "the edition-day turn" in next_day               # now it is history
    assert "The edition-day state" in next_day


def test_superseded_delta_never_reaches_the_writer_context(migrated_con):
    """Rook's gate on the WRITER surface: a superseded prior delta is dropped
    from the memory block (the corrected-away fact stops re-entering prose)."""
    con = migrated_con
    tid = _thread(con)
    a = _delta(con, tid, "2026-07-05", "WRONG: the strait never closed at all")
    b = _delta(con, tid, "2026-07-10", "the corrected closure account")
    _supersede(con, a, b, "corrected")
    block = memory_core.writer_thread_context(
        con, "Strait of Hormuz", before_date=EDITION)
    assert "WRONG" not in block
    assert "corrected closure account" in block


def test_reader_history_is_structurally_quarantined_from_the_prompt(
        migrated_con, fake_model):
    """Two-clocks, adversarial: seed consumption_events (the READER's clock)
    with distinctive markers via the new 0011 kinds, then build the record
    run's prompt — no marker may reach it. The writer context reads
    thread_deltas / thread_state / watch_items only; this pins the quarantine
    mechanically rather than by code-reading."""
    con = migrated_con
    _seed_hormuz(con, with_expired_watch=False)
    events.log_thread_view(con, PRIOR_EDITION, "READER-OPENED-MARKER-XYZ", "today")
    events.log_deep_view(con, PRIOR_EDITION, "READER-DEEPVIEW-MARKER-ABC", "archive")
    slots = [_slot(1, mem=("Strait of Hormuz",)), _slot(2), _slot(3)]
    _seed_edition(con, EDITION, slots)
    fake_model.narrative = _payload(slots)
    fake_model.script = _script(slots)
    generate.run_generate(date=EDITION, con=con, env=ENV, refresh=False)
    for call in fake_model.calls:
        assert "READER-OPENED-MARKER-XYZ" not in call["prompt"]
        assert "READER-DEEPVIEW-MARKER-ABC" not in call["prompt"]


def test_variant_b_carries_the_expired_watch_block_and_two_clocks_header(
        tmp_paths):
    """Variant B inherits ALL of rung (a) through the shared story block —
    the implementer proved the dated deltas; this extends to the expired-watch
    conversion demand and the two-clocks steering line."""
    db.migrate()
    con = db.connect()
    try:
        _seed_hormuz(con)
        slots = [_slot(1, mem=("Strait of Hormuz",)), _slot(2), _slot(3)]
        _seed_edition(con, EDITION, slots)
        inputs = generate.load_briefing_inputs(con, EDITION)
        inputs["briefs_by_slot"] = {}
        inputs["analyst_slot3_tier"] = None
        prompt_b = generate.build_narrative_prompt(EDITION, "B", inputs)
        assert "EXPIRED WATCH-FOR you flagged on 2026-07-10" in prompt_b
        assert "edition history only; never the reader's history" \
            in prompt_b.lower()
    finally:
        con.close()


# ===========================================================================
# E. THE EXPIRY REGISTER — conversion outcomes and parse edges (item 6)
# ===========================================================================

def test_dec_to_jan_rollover_pins_the_disclosed_wrong_year(migrated_con):
    """DISCLOSED LIMITATION, pinned as built (the docstring flags it, the gate
    accepted it): a December edition naming a January date resolves into the
    edition's own year — 'January 5' from 2026-12-30 becomes 2026-01-05, a
    PAST date, so the item expires immediately instead of on 2027-01-05. This
    test is the tripwire that fires when the limitation is actually fixed."""
    assert memory_core.parse_due_date(
        "talks resume on January 5", "2026-12-30") == "2026-01-05"


def test_dateless_watch_never_phantom_expires(migrated_con):
    """An observable with no parseable date -> due_date NULL -> it can never
    enter the expired set, even far in the future (tracked, not auto-expiring;
    Content: 'due-date when parseable')."""
    con = migrated_con
    tid = _thread(con)
    _open_watch(con, tid, PRIOR_EDITION, "whether the blockade holds", None)
    assert memory_core.expired_unconverted_watch_items(
        con, "Strait of Hormuz", "2027-12-31") == []


def test_parse_due_date_iso_precedence_and_noncalendar_pin():
    """As-built pins: (1) an ISO date wins over an EARLIER human-form date in
    the same prose (documented ordering quirk, not a defect — one canonical
    choice, deterministic); (2) the parser does no calendar validation — a
    'June 31' typo yields due 2026-06-31, a non-calendar string that still
    orders sanely against real dates (lexicographic). On record for Content."""
    assert memory_core.parse_due_date(
        "talks July 12 conclude by 2026-07-20", PRIOR_EDITION) == "2026-07-20"
    assert memory_core.parse_due_date(
        "a vote on June 31 will settle it", PRIOR_EDITION) == "2026-06-31"


def test_reshipped_observable_in_watch_for_is_flagged_not_resolved():
    """[RED-6 — acceptance contract, defect D5] Content rule ii: an expired
    watch-for is NEVER re-shipped. As built, a re-ship is worse than uncaught —
    it is falsely recorded as paid: the expired observable re-shipped in the
    story's `watch_for` (dateless — Sten's evasion clause, the shipped
    07-14 script's own move) makes the observable 'referenced', so
    expiry_conversion_findings stays silent AND classify_conversion returns
    'resolved', writing a conversion row that closes the debt the edition just
    re-incurred. Rule i cannot catch it (no date to grep).

    FIX CONTRACT (deterministic, field-scoped): the conversion check runs
    against the story BODY (lede/why_it_matters/headline); an expired
    observable whose only reference sits in `watch_for` is the RE-SHIP
    violation — flagged by expiry_conversion_findings, never classified as a
    conversion (the register write path uses the same body-only prose)."""
    expired = {"observable": _SWISS_OBS, "due_date": "2026-07-12",
               "edition_date": PRIOR_EDITION}
    stories = [{"lede": "Oil prices climbed on supply fears.",
                "why_it_matters": "Energy costs feed inflation.",
                "watch_for": ("Watch whether the Switzerland talks produce "
                              "an outcome this week.")}]
    slots = [{"slot": "1", "expired_watch": [expired]}]
    findings = generate.expiry_conversion_findings(stories, slots)
    assert findings, (
        "a dateless re-ship of the expired observable in watch_for was "
        "counted as a conversion — the debt closed on a re-incurred promise "
        "(defect D5)")


def test_classify_conversion_reship_gap_pinned_as_built():
    """Companion pin (GREEN, as-built) for D5: classify_conversion alone
    cannot tell a report-back from a re-promise — the dateless re-ship text
    classifies 'resolved' today. When D5's fix lands this pin flips scope
    (body-only prose) or dies with a conscious note; until then it documents
    the exact misclassification the implementer's handoff disclosed."""
    reship = "Watch whether the Switzerland talks produce an outcome this week."
    assert memory_core.classify_conversion(_SWISS_OBS, reship) == "resolved"


def test_unconverted_debt_persists_across_editions(migrated_con):
    """The register never forgets: an expired item left unconverted by one
    edition is STILL the next edition's debt (and the day after's) — silence
    does not launder it; only a conversion row closes it."""
    con = migrated_con
    tid = _thread(con)
    _open_watch(con, tid, PRIOR_EDITION, _SWISS_OBS, "2026-07-12")
    for later in ("2026-07-14", "2026-07-15", "2026-08-01"):
        debts = memory_core.expired_unconverted_watch_items(
            con, "Strait of Hormuz", later)
        assert [d["due_date"] for d in debts] == ["2026-07-12"]
    item = memory_core.expired_unconverted_watch_items(
        con, "Strait of Hormuz", "2026-08-01")[0]
    memory_core.record_watch_conversion(
        con, item, "2026-08-01", None, "superseded",
        "overtaken by the blockade")
    assert memory_core.expired_unconverted_watch_items(
        con, "Strait of Hormuz", "2026-08-02") == []


def test_non_thread_watch_items_are_outside_the_conversion_loop_as_built():
    """COVERAGE BOUNDARY, pinned as built and flagged in the QA report: a
    watch-for from a slot with NO matched thread persists with thread_id NULL,
    and the expiry read (expired_unconverted_watch_items) is thread-scoped —
    a dated non-thread promise expires with no edition ever asked to convert
    it. The implementer's docstring discloses thread-scoping; the register
    still HOLDS the debt (queryable), so the loop can be widened without a
    migration. On record for the gate: rule ii's 'never silently dropped' is
    currently thread-scoped."""
    from newslens import db as _db
    con = _db.connect(":memory:")
    con.executescript(
        "CREATE TABLE memory (id INTEGER PRIMARY KEY, topic TEXT,"
        " status TEXT);"
        "CREATE TABLE watch_items (id INTEGER PRIMARY KEY, thread_id INTEGER,"
        " briefing_id INTEGER, slot INTEGER, edition_date TEXT, kind TEXT,"
        " observable TEXT, due_date TEXT, converts INTEGER,"
        " created_at TEXT DEFAULT '');")
    con.execute(
        "INSERT INTO watch_items (thread_id, edition_date, kind, observable,"
        " due_date) VALUES (NULL, '2026-07-10', 'open', 'a dated non-thread"
        " promise for July 12', '2026-07-12')")
    # the debt exists in the register ...
    n = con.execute("SELECT COUNT(*) c FROM watch_items WHERE kind='open'"
                    " AND due_date < '2026-07-14'"
                    " AND NOT EXISTS (SELECT 1 FROM watch_items c2 WHERE"
                    " c2.converts = watch_items.id)").fetchone()["c"]
    assert n == 1
    # ... but no thread topic can ever surface it into a conversion demand
    assert memory_core.expired_unconverted_watch_items(
        con, "Any Topic", "2026-07-14") == []
    con.close()


# ===========================================================================
# F. MIGRATIONS UNDER FIRE (dispatch items 3 + 8)
# ===========================================================================

BEFORE_0011 = [f"{i:04d}" for i in range(1, 11)]   # 0001..0010


def _dir_through(tmp_path, prefixes):
    mdir = tmp_path / "migs"
    mdir.mkdir(exist_ok=True)
    for p in paths.MIGRATIONS_DIR.glob("*.sql"):
        if p.name[:4] in prefixes:
            shutil.copy(p, mdir / p.name)
    return mdir


# The REAL DB's consumption shape, attested read-only this pass (2026-07-16):
# 39 rows = 37 read + 2 listen, ids 1..39 contiguous, occurred_at in
# millisecond-Z format, 6 distinct dates. The fixture mirrors it exactly.
_REAL_SHAPE = (
    [("2026-07-06", "read")] * 7 + [("2026-07-06", "listen")]
    + [("2026-07-08", "read")] * 6
    + [("2026-07-10", "read")] * 8 + [("2026-07-10", "listen")]
    + [("2026-07-13", "read")] * 5
    + [("2026-07-14", "read")] * 6
    + [("2026-07-16", "read")] * 5
)


def test_0011_rebuild_preserves_all_39_real_shaped_rows(tmp_path):
    """Dispatch item 3/8: a fixture mirroring the real DB's 39 consumption
    rows (37 read + 2 listen, contiguous ids, ms-Z timestamps, 6 dates)
    survives the 0011 rebuild byte-identical, ids included, with the new
    columns NULL."""
    assert len(_REAL_SHAPE) == 39
    db_path = tmp_path / "real-shaped.db"
    db.migrate(db_path=db_path, migrations_dir=_dir_through(tmp_path, BEFORE_0011))
    con = db.connect(db_path)
    try:
        for i, (date, kind) in enumerate(_REAL_SHAPE, start=1):
            con.execute(
                "INSERT INTO consumption_events (id, date, kind, occurred_at)"
                " VALUES (?, ?, ?, ?)",
                (i, date, kind, f"{date}T02:36:{i:02d}.986Z"))
        con.commit()
        before = [tuple(r) for r in con.execute(
            "SELECT id, date, kind, occurred_at FROM consumption_events"
            " ORDER BY id")]
    finally:
        con.close()

    ran = db.migrate(db_path=db_path,
                     migrations_dir=_dir_through(tmp_path, BEFORE_0011 + ["0011"]))
    assert ran == ["0011_consumption_view_events.sql"]
    con = db.connect(db_path)
    try:
        after = [tuple(r) for r in con.execute(
            "SELECT id, date, kind, occurred_at FROM consumption_events"
            " ORDER BY id")]
        nulls = con.execute(
            "SELECT COUNT(*) c FROM consumption_events WHERE target IS NOT NULL"
            " OR referrer IS NOT NULL").fetchone()["c"]
    finally:
        con.close()
    assert after == before and len(after) == 39
    assert nulls == 0


def test_0011_refuses_hostile_rows_whole_and_recovers(tmp_path):
    """The mid-rebuild interrupt, proven: a drifted pre-0011 table (a legacy
    'view' kind, a NULL kind, a NULL occurred_at — shapes the 0007 CHECK would
    have blocked, simulating manual writes) makes 0011's INSERT..SELECT die
    against the v2 CHECKs MID-SCRIPT. The failure must be loud and WHOLE:
    original table intact with every row, no _v2 residue, 0011 unrecorded —
    and after the hostile rows are repaired, re-apply succeeds with the clean
    rows preserved."""
    db_path = tmp_path / "hostile.db"
    db.migrate(db_path=db_path, migrations_dir=_dir_through(tmp_path, BEFORE_0011))
    con = db.connect(db_path)
    try:
        # replace the checked table with a drifted, check-free shape
        con.executescript(
            "DROP TABLE consumption_events;"
            "CREATE TABLE consumption_events (id INTEGER PRIMARY KEY,"
            " date TEXT, kind TEXT, occurred_at TEXT);")
        rows = [(1, "2026-07-05", "read", "2026-07-05T09:00:00.000Z"),
                (2, "2026-07-06", "view", "2026-07-06T09:00:00.000Z"),   # legacy kind
                (3, "2026-07-07", None, "2026-07-07T09:00:00.000Z"),     # NULL kind
                (4, "2026-07-08", "read", None)]                          # NULL ts
        con.executemany(
            "INSERT INTO consumption_events VALUES (?, ?, ?, ?)", rows)
        con.commit()
    finally:
        con.close()

    with pytest.raises(sqlite3.IntegrityError):
        db.migrate(db_path=db_path,
                   migrations_dir=_dir_through(tmp_path, BEFORE_0011 + ["0011"]))

    con = db.connect(db_path)
    try:
        # whole-script rollback: original rows intact, no residue, unrecorded
        kept = con.execute(
            "SELECT COUNT(*) c FROM consumption_events").fetchone()["c"]
        residue = con.execute(
            "SELECT COUNT(*) c FROM sqlite_master WHERE name ="
            " 'consumption_events_v2'").fetchone()["c"]
        recorded = con.execute(
            "SELECT COUNT(*) c FROM schema_migrations WHERE filename LIKE"
            " '0011%'").fetchone()["c"]
        assert (kept, residue, recorded) == (4, 0, 0)
        # repair the drift, then re-apply
        con.execute("DELETE FROM consumption_events WHERE kind IS NULL"
                    " OR kind = 'view'")
        con.execute("UPDATE consumption_events SET occurred_at ="
                    " '2026-07-08T09:00:00.000Z' WHERE occurred_at IS NULL")
        con.commit()
    finally:
        con.close()
    ran = db.migrate(db_path=db_path,
                     migrations_dir=_dir_through(tmp_path, BEFORE_0011 + ["0011"]))
    assert ran == ["0011_consumption_view_events.sql"]
    con = db.connect(db_path)
    try:
        kept = [tuple(r) for r in con.execute(
            "SELECT id, kind FROM consumption_events ORDER BY id")]
    finally:
        con.close()
    assert kept == [(1, "read"), (4, "read")]


def test_thread_deltas_append_only_triggers_survive_0012_update_and_delete(
        migrated_con):
    """Rook's invariant, BOTH verbs: after 0012, the 0010 ledger triggers
    still abort UPDATE and DELETE on thread_deltas (the side-table promise:
    'leaves 0010's append-only triggers UNTOUCHED'), and both triggers still
    exist by name."""
    con = migrated_con
    tid = _thread(con)
    _delta(con, tid, "2026-07-05", "fee dispute")
    con.commit()
    with pytest.raises(sqlite3.DatabaseError, match="append-only"):
        con.execute("UPDATE thread_deltas SET what_happened = 'rewritten'")
    with pytest.raises(sqlite3.DatabaseError, match="append-only"):
        con.execute("DELETE FROM thread_deltas")
    names = {r["name"] for r in con.execute(
        "SELECT name FROM sqlite_master WHERE type='trigger'"
        " AND tbl_name='thread_deltas'")}
    assert any("update" in n.lower() for n in names)
    assert any("delete" in n.lower() for n in names)


def test_new_tables_enforce_foreign_keys(migrated_con):
    """FK teeth on both new tables (db.connect turns FKs ON): a supersession
    must reference real deltas; a conversion must reference a real open item;
    watch_items.thread_id must reference a real memory row."""
    con = migrated_con
    with pytest.raises(sqlite3.IntegrityError):
        con.execute("INSERT INTO thread_delta_supersessions (delta_id,"
                    " superseded_by) VALUES (901, 902)")
    with pytest.raises(sqlite3.IntegrityError):
        con.execute("INSERT INTO watch_items (thread_id, edition_date, kind,"
                    " observable, converts) VALUES (NULL, '2026-07-14',"
                    " 'resolved', 'x', 909)")
    with pytest.raises(sqlite3.IntegrityError):
        con.execute("INSERT INTO watch_items (thread_id, edition_date, kind,"
                    " observable) VALUES (777, '2026-07-14', 'open', 'x')")


def test_all_three_new_migrations_are_idempotent_together(tmp_path):
    """Idempotency in one pass: migrate a fresh DB (0011-0014 on the same NL
    train), then migrate again — zero pending, zero applied, tables/triggers
    unchanged. (0014, the provenance bound, joined the tail — NL-69; 0015/0016,
    the collect-now closure + explained-once schemas, joined after — ruling C.)"""
    db_path = tmp_path / "idem.db"
    first = db.migrate(db_path=db_path)
    assert [f[:4] for f in first][-11:] == ["0011", "0012", "0013", "0014",
                                            "0015", "0016", "0017", "0018",
                                            "0019", "0020", "0021"]
    assert db.migrate(db_path=db_path) == []
    con = db.connect(db_path)
    try:
        tables = {r["name"] for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
    finally:
        con.close()
    assert {"consumption_events", "thread_delta_supersessions",
            "watch_items"} <= tables


def test_0013_is_cleanly_separable_a_watchless_db_still_generates(
        tmp_path, monkeypatch, fake_model):
    """[RED-9 — acceptance contract, defect D7] CHECKPOINT ATTESTATION
    (migration 0013 is the un-approved third migration; the principal may
    decline it). The implementer's separability claim is FALSE as built: on a
    DB migrated through 0012 only — no watch_items — the run completes, but
    load_briefing_inputs' OperationalError seam wraps the writer-context loop
    TOGETHER with the expired-watch read, so the watch read's failure CLEARS
    the already-built thread_ledger blocks: declining 0013 silently disables
    rung (a) itself — the approved core deliverable — with no warning (the
    silent-no-op class). Proven: on this DB the MEMORY block never reaches the
    writer prompt.

    FIX CONTRACT: decouple the seams — the ledger/state read (0010/0012
    tables, always present) never dies with the watch read; the watch read's
    absence degrades expired to [] WITH a disclosed one-line warning. Then a
    0013-less DB still generates with rung (a) live, the register alone
    degrades, the post-persist write stays contained, and the edition
    persists — which is what 'cleanly separable' has to mean."""
    mdir = _dir_through(tmp_path, [f"{i:04d}" for i in range(1, 13)])  # 0001-0012
    db_path = tmp_path / "watchless.db"
    db.migrate(db_path=db_path, migrations_dir=mdir)
    con = db.connect(db_path)
    try:
        _seed_hormuz(con, with_expired_watch=False)
        slots = [_slot(1, mem=("Strait of Hormuz",)), _slot(2), _slot(3)]
        _seed_edition(con, EDITION, slots)
        fake_model.narrative = _payload(slots)
        fake_model.script = _script(slots)
        rep = generate.run_generate(date=EDITION, con=con, env=ENV,
                                    refresh=False)
        assert rep.sample is False
        n_prompt = next(c for c in fake_model.calls if c["json_mode"])["prompt"]
        assert "MEMORY — the record for thread 'Strait of Hormuz'" in n_prompt
        assert any("watch-items: register update failed after persist" in w
                   for w in rep.warnings)
        row = con.execute("SELECT narrative_text FROM briefings WHERE date=?",
                          (EDITION,)).fetchone()
        assert row["narrative_text"]          # the edition is on the record
    finally:
        con.close()


# ===========================================================================
# G. THE TIMELINE STRIKE — the read-side contract's third leg (defect D1)
# ===========================================================================

def _seed_superseded_pair(con):
    tid = _thread(con)
    a = _delta(con, tid, "2026-07-05", "WRONG-FACT the strait never closed")
    b = _delta(con, tid, "2026-07-10", "RIGHT-FACT Iran closed the strait")
    _supersede(con, a, b, "corrected by the 07-10 entry")
    return tid


def test_thread_timeline_strikes_superseded_rows(migrated_con):
    """[RED-1 — acceptance contract, defect D1] The 0012 read-side contract
    (migration header; Rook's gate; memory_core's own comment 'the server
    strikes it'): a superseded delta is 'rendered struck/annotated in
    timelines'. timeline_rows surfaces `superseded_by` — and the thread-page
    render (server._thread_timeline_html) drops it: the wrong fact renders
    indistinguishable from live history while state regeneration excludes it,
    so the reader-facing archive and the machine state now disagree. Per
    ENGINEERING.md's wiring-proof law the claim is unlanded until this flips.

    FIX CONTRACT: the superseded entry's <li> is visibly distinguished (a
    'superseded'-bearing class and/or strikethrough) AND annotated with its
    correction (the superseding entry's date), never dropped; live entries
    carry no such mark."""
    con = migrated_con
    tid = _seed_superseded_pair(con)
    html = server._thread_timeline_html(con, tid, "t1")
    assert "WRONG-FACT" in html            # never dropped (already true)
    assert "supersed" in html.lower(), (
        "the thread-page timeline renders a superseded delta unmarked (D1)")


def test_deep_view_timeline_strikes_superseded_rows(migrated_con):
    """[RED-2 — acceptance contract, defect D1, second surface] Same contract
    on the deep view's 'story so far' (server._deep_timeline_html): a
    superseded prior delta must render struck/annotated there too. FIX
    CONTRACT: as RED-1 (shared marker), on this render path."""
    con = migrated_con
    _seed_superseded_pair(con)
    slot = {"slot": 1, "matched_memory": ["Strait of Hormuz"]}
    html = server._deep_timeline_html(migrated_con, slot, EDITION, "s1")
    assert "WRONG-FACT" in html
    assert "supersed" in html.lower(), (
        "the deep-view timeline renders a superseded delta unmarked (D1)")


# ===========================================================================
# H. BUDGET HONESTY (dispatch item 11)
# ===========================================================================

def test_cap_gate_sees_the_enriched_prompt_and_aborts_loudly(
        migrated_con, fake_model):
    """The claim under test: the rung-(a) blocks flow through _est_cost and
    the cap arithmetic. Two arms, same edition, cap $0.08: WITHOUT ledger
    enrichment the narrative call proceeds; WITH a bloated thread ledger the
    estimate crosses the cap and the run aborts LOUDLY (GenerateError naming
    the estimate) BEFORE any model call — the enrichment is exactly what
    tripped it, so the gate provably sees it. The failed run still lands in
    generation_log (money-honesty)."""
    # B4 arithmetic (conscious re-pin): the narrative estimate now carries a
    # fixed ~$0.40 output leg (16k Opus ceiling at $25/MTok), so the cap is
    # DERIVED from the control arm's real estimate instead of a hardcoded
    # 0.08: generous cap for the control run, then cap = control_est + $0.02
    # so only the ~$0.057 enrichment delta (5 x 8k chars at Opus $5/MTok-in)
    # crosses it. Same claim proven: the gate sees the enriched prompt.
    env = {"OPENAI_API_KEY": "sk-qa-fake", "BUDGET_CAP_USD_PER_RUN": "9"}
    con = migrated_con
    slots = [_slot(1, mem=("Strait of Hormuz",)), _slot(2), _slot(3)]
    _seed_edition(con, EDITION, slots)
    tid = _thread(con)
    con.commit()
    fake_model.narrative = _payload(slots)
    fake_model.script = _script(slots)

    # control arm: no ledger — the run reaches the narrative call
    generate.run_generate(date=EDITION, con=con, env=env, refresh=False)
    assert any(c["json_mode"] for c in fake_model.calls)
    control_prompt = next(c["prompt"] for c in fake_model.calls
                          if c["json_mode"])
    control_est = generate._est_cost(control_prompt,
                                     generate.NARRATIVE_MAX_TOKENS)
    env["BUDGET_CAP_USD_PER_RUN"] = f"{control_est + 0.02:.4f}"
    fake_model.calls.clear()

    # test arm: five bloated priors (~8k chars each) enter the writer context
    for d in range(5, 10):
        _delta(con, tid, f"2026-07-{d:02d}", "arc turn " + ("x" * 8000))
    con.commit()
    with pytest.raises(generate.GenerateError, match="estimated narrative cost"):
        generate.run_generate(date=EDITION, con=con, env=env, refresh=False)
    assert not any(c["json_mode"] for c in fake_model.calls), (
        "the cap must abort BEFORE the model call")
    log = (paths.DATA_DIR / "generation_log.jsonl").read_text(encoding="utf-8")
    last = json.loads(log.strip().splitlines()[-1])
    assert last["status"] == "failed"
    assert "estimated narrative cost" in last["error"]


def test_worst_case_19_thread_enrichment_stays_inside_the_default_cap():
    """Dispatch item 11's arithmetic, re-pinned at B4 prices (conscious flip):
    19 memory blocks at the REAL DB's maxima (what_happened 192 ch,
    significance 158 ch, state 836 ch) plus a generous 30k-char base prompt
    now cost ~$0.12 input (Opus $5/MTok-in) + $0.40 output ceiling (16k tok
    at $25/MTok) ≈ $0.52 — inside the $1.50 B4 default cap with ~1.7x
    headroom against the 60% line. If block shapes or pricing constants grow
    enough to threaten the cap, this pin fires before the principal's money
    does. The default itself is pinned at 1.50 in test_config_guards; this
    derives so the two never fork."""
    block = ("MEMORY — the record for thread 'X' (edition history only; NEVER"
             " the reader's history):\n"
             + "standing state (as of Jul 10): " + "s" * 836 + "\n"
             + "this thread's record so far (edition dates are load-bearing"
             " — build continuity from them in the sentence):\n"
             + ("  * Jul 05: " + "w" * 192 + " — " + "g" * 158 + "\n") * 5)
    enriched = "b" * 30_000 + block * 19
    est = generate._est_cost(enriched, generate.NARRATIVE_MAX_TOKENS)
    assert est < config.DEFAULT_BUDGET_CAP_USD_PER_RUN * 0.6, (
        f"worst-case 19-thread narrative estimate ${est:.4f} eats >60% of the"
        " default cap — budget headroom assumption broken, escalate")


def test_empty_subject_units_never_license_the_repetition_word(migrated_con):
    """D6-R (QA re-verify, fix loop 1): a repetition idiom contributing no
    salient units of its own ('back on'), on a sentence whose only other
    salient words are the thread-topic's, yields an EMPTY subject set — and
    the empty set must REFUSE, never fall through has_predating_antecedent's
    any-prior branch. Repro from the re-verify: genuine unrelated priors +
    'The strait is back on.' licensed with zero finding. Conservative
    direction: warn-grade surface, false positive costs a warning."""
    con = migrated_con
    tid = _thread(con)
    _delta(con, tid, "2026-07-05",
           "Iran offered special transit terms amid US fee objections")
    _delta(con, tid, "2026-07-10",
           "Iran closed the strait after both sides traded strikes")
    con.commit()
    stories = [{"headline": "Strait update",
                "lede": "The strait is back on.",
                "why_it_matters": ""}]
    slots = [{"slot": "1", "matched_memory": ["Strait of Hormuz"]}]
    findings = generate.repetition_antecedent_findings(
        con, stories, slots, "2026-07-14")
    assert any("back on" in f for f in findings), (
        "empty subject units fell through to any-prior licensing (D6-R)")
