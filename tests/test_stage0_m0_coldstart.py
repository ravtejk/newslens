"""Stage-0 M0 — cold-start empty-ledger acceptance contracts (QA, 2026-07-25).

STATUS: ADOPTED by Stage-0 M1 (implementer, 2026-07-25). This file arrived as
the red-pin deliverable of the M0 cold-start QA pass (report:
workspace/products/newslens/research/2026-07-25--m0-coldstart-qa.md in the
product-org tree) and is now part of the suite.

WHAT M1 DID TO IT — nothing but the two lines below; no assertion in this file
was relaxed, reordered or deleted:
  * RED-1 now PASSES. The fix is structural: `memory.seed_if_first_run` and
    `memory.SEED_THREADS` were deleted outright (see the obituary comment in
    memory.py), so there is no seeding path left to gate, dodge or trip. The
    test is unchanged from the form it failed in.
  * RED-2 is PARKED as xfail(strict=True) tagged STAGE0-M2 — the script
    continuity net is M2's charge, not M1's. strict=True is the whole point:
    the day the net lands, this xpasses and the SUITE GOES RED until someone
    removes the marker and re-adopts the pin. A parked contract that could rot
    into a permanent yellow line would be worse than no pin at all.

BORN-RED AT HEAD fa26e45 (2 of 10 — the HEAD-run fail list travels in the M0
report per the born-red law, and was re-measured by the M1 implementer before
any diff: RED-1 `AssertionError: first contact seeded 14 threads into a virgin
profile (seed_if_first_run — the founder taxonomy) / assert 14 == 0`, RED-2
the no-finding record below. Every other test here is a labeled
carried-invariant, born green):

  RED-1  test_stage0_virgin_profile_first_contact_seeds_nothing
         seed_if_first_run() fires on ANY empty-DB + absent-memory.md profile
         and injects the FOUNDER's 14-thread taxonomy (memory.SEED_THREADS —
         "Iran War" ... "Stagflation", five with steering notes) into a brand
         new user's memory. Proven by probe to reach the rank prompt
         vocabulary, the writer's ACTIVE THREADS block, memory.md ("the live
         threads it's tracking for you"), and the Following spine.
         FIX CONTRACT: on a non-founder profile, first contact seeds NOTHING
         — kill the M4 bootstrap or gate it to the founder install
         explicitly; the Commissioning's first follow is the only sanctioned
         first write. Green when a virgin profile's first sync leaves
         memory empty on all three surfaces asserted below.
         DISCHARGED at Stage-0 M1 by the KILL (not a gate): the seeding
         function and its 14-thread constant no longer exist.

  RED-2  test_stage0_day_one_script_continuity_claim_reaches_the_record
         The script lane has NO continuity/repetition net: a day-one episode
         saying "As we covered last week..." sails through validate_script
         with zero hard problems and zero warnings (probe-verified), while
         the script PROMPT unconditionally licenses "thread-arc callbacks"
         with a continuity-flavored exemplar. The narrative-side nets
         (repetition_antecedent_findings et al.) never see script text.
         FIX CONTRACT: an unsupported spoken continuity claim must reach the
         run's record — warn-grade minimum, named per claim (mirror the
         repetition-word vocabulary; attribution exemption may apply). Green
         when the run record names the claim; the narrative-side nets must
         not be weakened to get there.
         PARKED at Stage-0 M1 as xfail(strict=True), tag STAGE0-M2 — the
         script net is M2's scope. It still RUNS every suite pass, so the
         moment the net lands it xpasses and the suite fails until the marker
         comes off.

Sandbox: the tree conftest's autouse fixtures apply (sandboxed paths incl.
child-env seams, loopback-only network, real-state tripwire). $0 by
construction.
"""
from __future__ import annotations

import json

import pytest

from newslens import db, generate, memory, memory_core, paths, server

from test_generate import compliant_script, seed_briefing, slot, stories_payload

DATE = "2026-07-25"
DAY2 = "2026-07-26"
DAY3 = "2026-07-27"
ENV = {"OPENAI_API_KEY": "sk-qa-fake"}


@pytest.fixture
def virgin_con():
    """A fully-migrated (0001..0022) EMPTY DB inside the autouse sandbox —
    the world a Stage-0 profile is born into. memory.md is ABSENT."""
    db.migrate()
    con = db.connect()
    assert con.execute("SELECT COUNT(*) c FROM memory").fetchone()["c"] == 0
    assert con.execute("SELECT sync_generation FROM sync_state"
                       ).fetchone()["sync_generation"] == 0
    yield con
    con.close()


# ---------------------------------------------------------------------------
# RED-1 — the seeding law (fails at HEAD: 14 founder threads appear)
# ---------------------------------------------------------------------------

def test_stage0_virgin_profile_first_contact_seeds_nothing(virgin_con):
    """BORN-RED at fa26e45 — fix contract in the module docstring (RED-1).

    A virgin profile's FIRST embedded sync (the one rank/serve verbs run)
    must leave memory EMPTY on all three surfaces: the DB, the ranking
    vocabulary, and the rendered memory.md."""
    con = virgin_con
    assert not paths.MEMORY_FILE.exists()
    res = memory.sync_memory(con)               # what rank's opening sync does
    assert not res.stale_refusal                # fresh profile is never refused
    n = con.execute("SELECT COUNT(*) c FROM memory").fetchone()["c"]
    assert n == 0, (
        f"first contact seeded {n} threads into a virgin profile "
        f"(seed_if_first_run — the founder taxonomy)")
    assert memory.active_context(con) == []
    rendered = paths.MEMORY_FILE.read_text(encoding="utf-8")
    for founder_topic in ("Iran War", "Strait of Hormuz", "Stagflation"):
        assert founder_topic not in rendered


# ---------------------------------------------------------------------------
# RED-2 — the script lane's continuity net (fails at HEAD: no finding at all)
# ---------------------------------------------------------------------------

@pytest.mark.xfail(
    strict=True,
    reason="STAGE0-M2: the script lane's continuity net is M2's charge "
           "(M0 finding F2). Strict on purpose — when the net lands this "
           "xpasses and the suite goes RED until the marker is removed and "
           "the pin re-adopted as a normal green.")
def test_stage0_day_one_script_continuity_claim_reaches_the_record(
        virgin_con, monkeypatch):
    """BORN-RED at fa26e45 — fix contract in the module docstring (RED-2).

    Edition 1 on a virgin profile; the script model fabricates spoken
    continuity ("As we covered last week", "we've been tracking"). The run
    must surface a finding NAMING the unsupported claim — today it ships
    silently into the episode text."""
    import time as _time
    con = virgin_con
    slots = [slot(1, tags=(), mem=())]
    seed_briefing(con, DATE, slots)

    poisoned = ("Good morning. As we covered last week, the corridor fight "
                "deepened — we have been tracking this story for weeks. "
                + compliant_script(slots))

    calls = {"json": 0}

    def chat(key, prompt, max_tokens, temperature, json_mode):
        if json_mode:
            calls["json"] += 1
            return {"choices": [{"finish_reason": "stop", "message": {
                "content": json.dumps(stories_payload(slots))}}],
                "usage": {"prompt_tokens": 900, "completion_tokens": 200}}
        return {"choices": [{"finish_reason": "stop",
                             "message": {"content": poisoned}}],
                "usage": {"prompt_tokens": 900, "completion_tokens": 200}}

    monkeypatch.setattr(generate, "_chat", chat)
    monkeypatch.setattr(_time, "sleep", lambda s: None)
    rep = generate.run_generate(date=DATE, con=con, env=dict(ENV),
                                refresh=False)
    record = " || ".join(rep.warnings)
    assert ("as we covered" in record.lower()
            or "been tracking" in record.lower()
            or ("continuity" in record.lower()
                and ("record" in record.lower()
                     or "antecedent" in record.lower()))), (
        "a day-one script's fabricated continuity claims reached the episode "
        f"with no finding in the run record. warnings: {record!r}")


# ---------------------------------------------------------------------------
# Carried invariants (BORN GREEN — the empty-ledger laws Stage-0 stacks on;
# each bit against a probe mutation during the M0 pass)
# ---------------------------------------------------------------------------

def test_stage0_fresh_profile_first_sync_is_never_refused(virgin_con):
    """Carried-invariant (born green): the NL-81 guard adopts a legitimately
    fresh profile — absent file -> stamped gen-1 render, no refusal; second
    sync is a change-free round-trip."""
    con = virgin_con
    res = memory.sync_memory(con)
    assert not res.stale_refusal and res.created_file
    stamp = memory.parse_stamp(paths.MEMORY_FILE.read_text(encoding="utf-8"))
    assert stamp and stamp["generation"] == 1
    res2 = memory.sync_memory(con)
    assert not res2.stale_refusal
    assert res2.added == [] and res2.dismissed_by_deletion == []


def test_stage0_zero_byte_memory_file_is_a_lawful_unseeded_start(virgin_con):
    """Carried-invariant (born green): an org-shipped 0-byte memory.md is a
    lawful true-zero start — bootstrap adopts it, and (because the file
    EXISTS) the M4 seeding arm is foreclosed. This is the provisioning shape
    Stage-0 can ship TODAY regardless of the RED-1 fix."""
    con = virgin_con
    paths.MEMORY_FILE.write_text("", encoding="utf-8")
    res = memory.sync_memory(con)
    assert not res.stale_refusal and res.bootstrapped
    assert res.seeded == 0
    assert con.execute("SELECT COUNT(*) c FROM memory").fetchone()["c"] == 0
    stamp = memory.parse_stamp(paths.MEMORY_FILE.read_text(encoding="utf-8"))
    assert stamp and stamp["generation"] == 1


def test_stage0_cross_profile_file_is_refused_on_identity_day_one(
        virgin_con, tmp_path):
    """Carried-invariant (born green): profile A's stamped memory.md into
    profile B's FRESH DB refuses on the pairing identity, naming both sides —
    the Stage-0 cross-pairing law is live from migration time, not first
    render."""
    con_a = virgin_con
    memory.sync_memory(con_a)                       # A: gen-1 stamped file
    db_b = tmp_path / "profile-b.db"
    db.migrate(db_path=db_b)
    con_b = db.connect(db_b)
    try:
        res = memory.sync_memory(con_b)             # B reads A's file
        assert res.stale_refusal
        assert "DIFFERENT NewsLens database" in res.stale_refusal
        assert res.added == [] and res.dismissed_by_deletion == []
    finally:
        con_b.close()


def test_stage0_day_one_thread_keeps_total_silence(virgin_con):
    """Carried-invariant (born green): a followed-but-never-covered thread
    produces NO writer memory block, NO backgrounder, NO Today ordinal stamp
    — the day-one silence the arc line also keeps."""
    con = virgin_con
    paths.MEMORY_FILE.write_text("", encoding="utf-8")   # no seeding
    memory.sync_memory(con)
    memory.add_thread(con, "Grain Corridor")
    tid = memory_core.resolve_thread_id(con, "Grain Corridor")
    assert memory_core.writer_thread_context(con, "Grain Corridor",
                                             before_date=DATE) == ""
    assert memory_core.writer_baseline_block(con, "Grain Corridor",
                                             before_date=DATE) == ""
    assert memory_core.today_memory_stamp(con, tid, DATE) is None


def test_stage0_rung_a_is_strictly_prior_on_a_one_edition_history(virgin_con):
    """Carried-invariant (born green): with EXACTLY ONE ledger entry (the
    first delta, dated day 2), the writer context is empty for day 2 itself
    and appears only from day 3 — same-day rows are never 'prior coverage'."""
    con = virgin_con
    paths.MEMORY_FILE.write_text("", encoding="utf-8")
    memory.sync_memory(con)
    memory.add_thread(con, "Grain Corridor")
    tid = memory_core.resolve_thread_id(con, "Grain Corridor")
    con.execute("INSERT INTO briefings (date, story_slots) VALUES (?, '[]')",
                (DAY2,))
    bid = con.execute("SELECT id FROM briefings WHERE date=?",
                      (DAY2,)).fetchone()["id"]
    con.execute(
        "INSERT INTO thread_deltas (thread_id, briefing_id, edition_date,"
        " slot, verdict, what_happened, significance, cites_json)"
        " VALUES (?, ?, ?, 1, 'advances', 'The first move.',"
        " 'It changed the story.', '[\"S1\"]')", (tid, bid, DAY2))
    con.commit()
    assert memory_core.writer_thread_context(con, "Grain Corridor",
                                             before_date=DAY2) == ""
    blk = memory_core.writer_thread_context(con, "Grain Corridor",
                                            before_date=DAY3)
    assert blk.startswith("MEMORY — the record for thread")
    assert DAY3 not in blk


def test_stage0_repetition_words_bite_on_the_empty_ledger(virgin_con):
    """Carried-invariant (born green): the empty ledger never licenses a
    repetition word — threaded AND threadless stories flag; the attributed
    form stays exempt. A vacuously-passing net would be worse than none."""
    con = virgin_con
    paths.MEMORY_FILE.write_text("", encoding="utf-8")
    memory.sync_memory(con)
    memory.add_thread(con, "Grain Corridor")

    def story(lede):
        return {"tier": "full", "headline": "H", "lede": lede,
                "why_label": "Why it matters", "why_it_matters": "It matters.",
                "watch_label": "Watch for",
                "watch_for": "Watch the vote due Aug 2."}

    threaded = [{"slot": 1, "matched_memory": ["Grain Corridor"]}]
    threadless = [{"slot": 1, "matched_memory": []}]
    assert generate.repetition_antecedent_findings(
        con, [story("Officials reinstated the blockade today.")],
        threaded, DATE)
    assert generate.repetition_antecedent_findings(
        con, [story("Sanctions were reimposed on the exporters today.")],
        threadless, DATE)
    assert generate.repetition_antecedent_findings(
        con, [story('The FT reports the blockade was "reinstated" Friday.')],
        threaded, DATE) == []
    assert memory_core.has_predating_antecedent(
        con, "Grain Corridor", {"blockade"}, DATE) is False


def test_stage0_counterfeit_baseline_cite_never_licenses_on_virgin(virgin_con):
    """Carried-invariant (born green): on a baseline-less profile the dated
    cite FORM may parse (has_baseline_cite / the HSR form predicate) but the
    licensing gate refuses, and the generic net still flags the sentence."""
    con = virgin_con
    paths.MEMORY_FILE.write_text("", encoding="utf-8")
    memory.sync_memory(con)
    memory.add_thread(con, "Grain Corridor")
    sent = "The corridor deal was reinstated (baseline, Jul 14)."
    assert memory_core.has_baseline_cite(sent) is True
    assert memory_core.licensing_baseline_cite(
        con, ["Grain Corridor"], sent, DATE) is False
    assert generate.repetition_antecedent_findings(
        con, [{"tier": "full", "headline": "H", "lede": sent,
               "why_label": "Why it matters", "why_it_matters": "It matters.",
               "watch_label": "Watch for",
               "watch_for": "Watch the vote due Aug 2."}],
        [{"slot": 1, "matched_memory": ["Grain Corridor"]}], DATE)


def test_stage0_virgin_serving_renders_empty_and_logs_no_phantom_read(
        virgin_con):
    """Carried-invariant (born green): the never-had-anything DB serves the
    front page and archive with rendered=falsy, ZERO consumption events, and
    no continuity furniture (the NL-11 empty state, extended from the
    no-briefings-TODAY world to the never-anything world)."""
    con = virgin_con
    page, rendered = server.build_page(con, None)
    assert not rendered
    assert con.execute(
        "SELECT COUNT(*) c FROM consumption_events").fetchone()["c"] == 0
    import re as _re
    body = _re.sub(r"(?s)<style.*?</style>|<script.*?</script>", "", page)
    for marker in ("UPDATED THIS EDITION", "LAST UPDATED", "last covered"):
        assert marker not in body
    arch = server._archive_body(con, None)
    assert not arch or "No editions" in arch or "empty" in arch.lower()
