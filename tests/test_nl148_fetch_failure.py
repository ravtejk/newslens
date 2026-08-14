"""NL-148 — the fetch-failure contract (principal's ruling 2026-08-09).

WHAT LANDED AND WHAT DID NOT, because a test file that pins four clauses when
two shipped is itself a false claim:

  clause 1  try the story's OTHER cluster sources, inside the existing
            sourcing boundaries, no open-web hunting.
            CARRIED INVARIANT — born GREEN and labeled as such. The behaviour
            predates this milestone; these pins exist because nothing in the
            suite asserted it as a CONTRACT, and clause 1 is now a promise to
            the principal rather than an implementation detail that happened
            to hold. Each carries a mutation receipt (the enforcement removed,
            the red observed) per the pin-proves-its-route law.
  clause 2  skip + promote. RULED 2026-08-13 and BUILT BY NL-151 — see
            tests/test_nl151_fetch_skip.py. The two DETECTOR pins below stay,
            and both are still GREEN, which is the point: neither measured fact
            changed. Finding (a) (a failed fetch still leaves excerpt material)
            was never an obstacle to the skip — only to hanging the skip off
            SOURCE-MAP EMPTINESS; NL-151 triggers on fetch OUTCOMES instead.
            Finding (b) (no story identity in the table) is avoided rather than
            resolved: NL-151 moves the depth TIER, never the slot NUMBER, so
            the promote that needed a schema field never happens. If either pin
            goes red the premises have moved and BOTH rulings must be re-read.
  clause 3  bottom-of-briefing disclosure. BUILT BY NL-151 with clause 2. The
            pin that guarded against half a disclosure is INVERTED, not
            deleted — it now guards the whole one.
  clause 4  systemic pause + retry. BUILT — born RED (10 of the 11 clause-4
            pins fail at HEAD; the eleventh is the labeled born-green CONTROL
            that proves the degrade handler it must escape is actually live).
            The count was stale at 9-of-10: the QA-F4 fix loop added
            test_clause4_the_pause_can_fire_after_real_spend... after this
            docstring was written. Corrected by NL-151's scout.

PROOF CLASSES ARE LABELED PER PIN and they are not decoration: born-red,
carried-invariant/born-green, and mutation-receipt answer different questions,
and an inflated label is the failure the born-red law exists to catch. Measured
at HEAD (612fc8a) via a read-only `git archive` export with PYTHONPATH pinned
to that copy's src: 11 failed / 31 passed across this file and the vocab_move
file it ships beside.

Offline by construction: autouse sandbox (conftest), no network, no key, $0.
"""

from __future__ import annotations

import json

import pytest

from newslens import analysis, commissioning, db, generate, paths

from test_analysis_brief_qa import (  # the stage's own harness, reused
    DATE, ENV_OK, seed_min, sonar_none, s_brief,
)
from test_generate import (                               # noqa: F401
    ENV as GEN_ENV, A_DAY, compliant_script, fake_model, slot as gen_slot,
    seed_briefing as seed_published_edition, stories_payload, _fake_audio_ok,
)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _fetch_all_fail(url, timeout, cap=0, user_agent=""):
    """Every article fetch fails; robots 404s so the DENY arm never masks it.

    This is the clause-4 world: sockets open, nothing comes back."""
    if url.endswith("/robots.txt"):
        import urllib.error
        raise urllib.error.HTTPError(url, 404, "nf", {}, None)
    raise OSError("connection reset by peer")


def _chat_unreachable(key, prompt):
    """A synthesis seat that is DOWN. Not an assertion about the pipeline.

    CORRECTED (QA F4, 2026-08-12). This used to say "synthesis was called on a
    run that must have paused before it", which described a guarantee the code
    does not make: `analyze_story` calls synthesis for every prioritized slot,
    fetch failures and all, and its per-slot handler catches whatever comes back
    — including this AssertionError — and turns it into a failed slot. The raise
    was therefore never a tripwire; it was a way of making the model unreachable
    so no brief can be produced. That is all it is, and the pins that use it are
    pins about the no-material world, not about call ordering."""
    raise AssertionError(
        "synthesis seat is down for this test (the model cannot be reached); "
        "the per-slot handler is expected to turn this into a failed slot")


def _c_brief():
    """A brief valid against an EXCERPT-ONLY source map (C1/C2 offered).

    The harness's `s_brief` cites S1, which by definition cannot exist on a
    run where every fetch failed — that map holds C-keys only. Re-citing is
    the point of the fixture, not a workaround: it is what "the fetch failed
    and the story still had material" actually looks like."""
    b = s_brief()
    for entry in b["pinned_facts"]:
        entry["cites"] = ["C1"]
    b["ledger"] = [{"claim": "The summit spans two days.", "cites": ["C1"]}]
    b["mechanism"] = ("Attendance is traded for spending commitments; each "
                      "ally answers to its own parliament [C1].")
    return b


# ===========================================================================
# CLAUSE 1 — CARRIED INVARIANT (born GREEN, labeled; mutation receipts below)
# ===========================================================================

def test_clause1_every_cluster_source_is_tried_not_just_the_first():
    """CARRIED INVARIANT, born green. The contract's first move — "try that
    story's OTHER cluster sources" — is `fetch_cluster_articles` walking every
    item it is handed, and the walk does not stop at the first success or the
    first failure.

    MUTATION RECEIPT (pin proves its route): with the loop body's recursion
    into `fetch_article` replaced by a `break` after the first record, this
    fails `assert len(records) == 3`, observed 1 != 3."""
    seen = []

    def fetch(url, timeout, cap=0, user_agent=""):
        if url.endswith("/robots.txt"):
            import urllib.error
            raise urllib.error.HTTPError(url, 404, "nf", {}, None)
        seen.append(url)
        raise OSError("down")

    items = [{"url": f"https://a{i}.com/s", "source_name": f"O{i}",
              "tier": "full"} for i in range(3)]
    records = analysis.fetch_cluster_articles(items, fetch=fetch,
                                              sleep=lambda s: None)
    assert len(records) == 3
    # every one of them was actually attempted over the wire, in order
    assert seen == ["https://a0.com/s", "https://a1.com/s", "https://a2.com/s"]
    assert all(r.attempted for r in records)


def test_clause1_the_retry_never_leaves_the_cluster_or_the_tier_boundaries():
    """CARRIED INVARIANT, born green. The TIER half of "no open-web hunting":
    inside `fetch_cluster_articles`, a source outside the analyst's fetch tiers
    is excluded BEFORE a socket opens, and the exclusion is recorded.

    SCOPE, CORRECTED (QA F4-class overclaim, finding F1, 2026-08-12). This pin
    used to claim it "would catch a future 'fetch failed, try a search engine'
    fix". IT WOULD NOT, and QA proved it: a hunt added one layer up — in
    `analyze_story`, after the cluster walk comes back empty — never passes
    through this helper, and the whole suite stayed green under exactly that
    mutation. This pin proves the boundary INSIDE the helper for the items the
    helper is handed. The open-web claim at the layer a "fix" would land is
    pinned by `test_clause1_the_pipeline_fetches_the_whole_cluster_and_only_it`
    below, which drives `run_analysis`.

    MUTATION RECEIPT: with the `tier_allows_fetch` guard removed from
    `fetch_article`, the headline_only URL is fetched and this fails at
    `assert wire == [...]`, observed the paywalled URL present."""
    wire = []

    def fetch(url, timeout, cap=0, user_agent=""):
        if url.endswith("/robots.txt"):
            import urllib.error
            raise urllib.error.HTTPError(url, 404, "nf", {}, None)
        wire.append(url)
        raise OSError("down")

    items = [
        {"url": "https://ok.com/a", "source_name": "OK", "tier": "full"},
        {"url": "https://paywalled.com/b", "source_name": "PW",
         "tier": "headline_only"},
        {"url": "https://nyt.com/c", "source_name": "NYT",
         "tier": "reference_only"},
    ]
    records = analysis.fetch_cluster_articles(items, fetch=fetch,
                                              sleep=lambda s: None)
    # only the in-tier source ever reached the network
    assert wire == ["https://ok.com/a"]
    excluded = [r for r in records if r.outcome == analysis.TIER_EXCLUDED]
    assert {r.source_name for r in excluded} == {"PW", "NYT"}
    assert all(not r.attempted for r in excluded)
    # and the exclusions are RECORDED, never silent
    assert all(r.detail for r in excluded)


def test_clause1_the_pipeline_fetches_the_whole_cluster_and_only_it():
    """THE PIPELINE-ROUTE PIN (QA finding F1, 2026-08-12 — the hole this fix
    loop closes). CARRIED INVARIANT, born GREEN and labeled as such: it passes
    at HEAD 612fc8a (measured, read-only git-archive export: 10 failed / 7
    passed in this file, and this pin is one of the 7). Calling it born-red
    would inflate the proof class — the behaviour was always right; what was
    missing was any pin that could SEE it break. Its bite is therefore proven
    by mutation, twice, below.

    Clause 1 is a promise about what the PIPELINE does, and until
    now every clause-1 pin drove `fetch_cluster_articles` directly. Two
    mutations proved the gap, both measured by QA against the full suite:

      * truncate the cluster upstream (`_cluster_items_for_slot` -> `ids[:1]`)
        — all 15 NL-148 pins stayed GREEN;
      * hunt the open web one layer up (`analyze_story`, after an
        attempted-and-all-failed walk, fetch a google.com/search URL) — the
        FULL suite stayed green, 27 environment failures and no product catch.

    So this one runs the real route: `run_analysis` -> `analyze_story` ->
    `_cluster_items_for_slot` -> `fetch_cluster_articles`, with a recording
    fetch, and asserts the SET OF URLS THAT REACHED THE WIRE is exactly the
    cluster the slot names — nothing dropped, nothing invented.

    THE EXPECTATION IS READ FROM THE DATABASE, not from
    `_cluster_items_for_slot`. That is the difference between a pin and a
    tautology: sourcing the expected set from the function under mutation would
    move both sides together and stay green under the truncation.

    Every fetch fails on purpose — that is the state in which a "try a search
    engine" fix would be tempting, so it is the state the pin has to hold in.

    MUTATION RECEIPTS (both re-run by this fix loop; transcripts in the report):
      * `ids[:1]`  -> `assert 1 == 3` / left-vs-right URL sets differ;
      * google hunt -> the search URL appears in the wire set."""
    wire = []

    def fetch(url, timeout, cap=0, user_agent=""):
        if url.endswith("/robots.txt"):
            import urllib.error
            raise urllib.error.HTTPError(url, 404, "nf", {}, None)
        wire.append(url)
        raise OSError("down")

    db.migrate()
    con = db.connect()
    try:
        seed_min(con, n_items=3)
        # the CLUSTER, straight from the slot's own item_ids -> source_items.
        row = con.execute(
            "SELECT story_slots FROM briefings WHERE date = ?", (DATE,)
        ).fetchone()
        slot = json.loads(row["story_slots"])[0]
        ids = slot["item_ids"]
        assert len(ids) == 3, ids            # the fixture really seeded three
        cluster_urls = sorted(
            r["url"] for r in con.execute(
                "SELECT url FROM source_items WHERE id IN (?, ?, ?)", ids))

        def chat_rejected(key, prompt):
            # a brief that cannot validate on an excerpt-only map: keeps the
            # run in the no-material world without needing the model to be down.
            return s_brief(), 0.0

        analysis.run_analysis(date=DATE, con=con, env=dict(ENV_OK),
                              chat=chat_rejected, sonar=sonar_none,
                              fetch=fetch, sleep=lambda s: None)

        # THE WHOLE CLUSTER: every one of the slot's items was tried.
        # AND ONLY THE CLUSTER: no URL the cluster did not name reached the wire
        # — no search engine, no discovered link, nothing.
        assert sorted(wire) == cluster_urls, {
            "on the wire": sorted(wire), "the cluster": cluster_urls}
    finally:
        con.close()


# ===========================================================================
# CLAUSE 2 — THE STOP'S TWO PREMISES, PINNED AS DETECTORS
# ===========================================================================

def test_clause2_premise_a_a_failed_fetch_still_leaves_excerpt_material():
    """THE FACT THAT STOPPED CLAUSE 2 (a). There is no "no cluster source
    yielded anything" state to hang a skip on: every unfetched cluster item
    still mints a C# excerpt key, so a story whose full-text fetch failed
    completely still has material and still builds a brief.

    DETECTOR, not a guarantee: if this ever goes red, `build_source_map` has
    changed and the stop's first premise must be re-read against the ruling."""
    items = [{"outlet": "The Hill", "url": f"https://x/{i}", "title": f"T{i}",
              "raw_excerpt": "blurb", "fetched_at": "", "published_at": ""}
             for i in (1, 2)]
    records = [analysis.FetchRecord(url=it["url"], source_name="The Hill",
                                    tier="full", outcome=analysis.ERROR)
               for it in items]
    sources = analysis.build_source_map(records, items, [], [])

    assert sorted(sources) == ["C1", "C2"]
    # ...and therefore the existing total-failure branch does NOT fire, which
    # is precisely why the contract's skip could not be wired to it.
    assert any(k[0] in "SCR" for k in sources)


def test_clause2_premise_b_the_brief_table_has_no_story_identity():
    """THE FACT THAT STOPPED CLAUSE 2 (b). `analysis_briefs` is keyed
    (date, slot) with no story column, so "promote the next story into its
    place" — which renumbers slots — would make slot N's persisted analysis
    describe a different story than slot N now holds.

    DETECTOR: goes red the day a story-identity column lands, which is exactly
    the day the promote becomes buildable."""
    db.migrate()
    con = db.connect()
    try:
        cols = {r["name"] for r in
                con.execute("PRAGMA table_info(analysis_briefs)")}
        assert "slot" in cols and "date" in cols
        assert not cols & {"story_id", "story_title", "cluster_id", "item_ids"}
    finally:
        con.close()


def test_clause3_the_disclosure_landed_whole_with_its_trigger():
    """INVERTED BY NL-151, deliberately, and NOT deleted.

    This pin used to assert the disclosure's ABSENCE — the right guard while
    clause 2 was an open design decision, because a frozen reader-facing
    sentence wired to an unreachable trigger reads as a shipped guarantee. The
    principal ruled clause 2 on 2026-08-13 and clause 3 landed with it, so the
    premise moved and the pin turned over.

    GATE F-2 TRUTH-EDIT (2026-08-13): this pin is the PRESENCE-LEVEL FURNITURE
    TRIPWIRE only — it reads source text, so it is comment-satisfiable by
    construction (gate mutation M4: an assembler loop replaced by a comment
    carrying both tokens leaves it green). It does NOT prove the disclosure
    ships whole. The register is actually held by the behavioral pins:
    test_L2_the_disclosure_is_his_sentence_verbatim_at_the_bottom (red under
    M4) and test_nl151_qa.py::test_e2e_disclosure_reaches_the_persisted_edition
    (red under M3, the severed-plumbing mutant). Keep this pin for the cheap
    early warning; trust those for the property."""
    assert hasattr(generate, "FETCH_SKIP_LINE")
    import inspect
    src = inspect.getsource(generate.assemble_narrative)
    assert "fetch_skipped" in src
    assert "FETCH_SKIP_LINE" in src
    # ...and the trigger is live: the stage reports the key the renderer reads.
    assert "fetch_skipped" in inspect.getsource(analysis.run_analysis)


# ===========================================================================
# CLAUSE 4 — THE SYSTEMIC PAUSE (born RED)
# ===========================================================================

def test_clause4_the_stage_reports_systemic_failure_when_nothing_fetched():
    """BORN RED. Every prioritized story attempted fetches and got nothing:
    the STAGE says so. It reports; it does not raise — `run_analysis` is a
    directly-callable stage API (the CLI and a dozen tests drive it alone),
    and a stage that killed a pipeline it does not own would be deciding the
    fate of runs that never asked it to."""
    db.migrate()
    con = db.connect()
    try:
        seed_min(con)
        report = analysis.run_analysis(
            date=DATE, con=con, env=dict(ENV_OK), chat=_chat_unreachable,
            sonar=sonar_none, fetch=_fetch_all_fail, sleep=lambda s: None)
        assert report["fetch_systemic_failure"] is True
        # the stage still returned a report and still logged one
        assert report["per_story"][0]["fetch_attempted"] > 0
        assert report["per_story"][0]["fetch_ok"] == 0
    finally:
        con.close()


def test_clause4_the_ruled_sentence_is_the_exception_text_verbatim():
    """BORN RED. The pause message is what the reader is SHOWN, so it is
    pinned as text rather than as a type. The principal's sentence, 2026-08-09,
    to the character."""
    exc = analysis.SystemicFetchFailure(analysis.FETCH_PAUSE_MESSAGE)
    assert str(exc) == "Fetch failed, please try again in a few minutes"


def test_clause4_the_verdict_is_on_the_record():
    """BORN RED. A run that ends without an edition must still be visible in
    the generation log — "the one failure the record never saw" is the
    asymmetry the keyless-refusal note in generate.py exists to forbid. The
    stage's own entry carries the verdict."""
    db.migrate()
    con = db.connect()
    try:
        seed_min(con)
        analysis.run_analysis(date=DATE, con=con, env=dict(ENV_OK),
                              chat=_chat_unreachable, sonar=sonar_none,
                              fetch=_fetch_all_fail, sleep=lambda s: None)
        lines = [json.loads(l) for l in
                 (paths.DATA_DIR / "generation_log.jsonl")
                 .read_text(encoding="utf-8").splitlines() if l.strip()]
        stage = [e for e in lines if e.get("stage") == "analysis"]
        assert stage, "the analysis stage left no log entry"
        assert stage[-1]["fetch_systemic_failure"] is True
    finally:
        con.close()


def _completed_spend(con, date):
    """What a retry would re-bill if the ruled default were ever violated: the
    money already sunk into COMPLETED (valid) briefs for the date."""
    return con.execute(
        "SELECT COALESCE(SUM(cost_usd), 0) AS s FROM analysis_briefs"
        " WHERE date = ? AND status = 'valid'", (date,)).fetchone()["s"]


def test_clause4_no_completed_story_exists_at_the_pause():
    """BORN RED — THE RULED DEFAULT, held by construction.

    THE RULING (principal 2026-08-09, untouched): "retry resumes from the
    failed slot; already-completed stories never re-run and never re-bill."
    THE INVARIANT THAT MAKES IT SAFE: the pause's third conjunct is "no valid
    brief exists", so at the moment it fires the set of COMPLETED stories is
    EMPTY — a retry re-runs only incomplete work, and there is no completed
    story it could re-bill.

    RE-SCOPED (QA F4, 2026-08-12) from "the retry is free because nothing
    completed". That title claimed more than the ruling and more than the code:
    QA measured a pause firing after $0.74 of billed-but-rejected synthesis, so
    a retry is NOT always $0. The behaviour is right and unchanged; the claim
    was too wide. What is pinned now is the thing that is actually true in every
    world — completed work is empty, therefore never re-billed — and the
    scenario the old claim missed gets its own pin directly below.

    Note the assertion moved from SUM(cost_usd) over ALL rows to SUM over
    COMPLETED rows. The old form passed only because this test's model is
    unreachable; the new form is the invariant."""
    db.migrate()
    con = db.connect()
    try:
        seed_min(con)
        report = analysis.run_analysis(
            date=DATE, con=con, env=dict(ENV_OK), chat=_chat_unreachable,
            sonar=sonar_none, fetch=_fetch_all_fail, sleep=lambda s: None)
        assert report["fetch_systemic_failure"] is True
        # the set of completed stories is empty...
        assert analysis.any_valid_brief(con, DATE) is False
        assert con.execute(
            "SELECT COUNT(*) AS n FROM analysis_briefs WHERE date = ?"
            " AND status = 'valid'", (DATE,)).fetchone()["n"] == 0
        # ...so there is nothing completed for a retry to re-bill.
        assert _completed_spend(con, DATE) == 0
    finally:
        con.close()


def test_clause4_the_pause_can_fire_after_real_spend_and_the_ruling_still_holds(
        monkeypatch):
    """BORN RED — the honest half of F4, pinned so the overclaim cannot return.

    The world: every fetch fails AND the model is UP. Each slot is synthesized
    and the brief is REJECTED (it cites S1, which cannot exist on an
    excerpt-only map), which bills. So the pause fires on a run that DID spend
    money — "$0 at the pause / free by construction" is a property of the
    model-down scenario, not of the design.

    What survives, and is what the principal actually ruled: nothing COMPLETED
    was paid for, so the retry re-bills no completed story. Both facts are
    asserted together on purpose — a future edit that restores the "$0" claim
    has to delete a passing assertion to do it.

    ARM-PINNED 2026-08-14 (NL-151b), and the reason is a MEASURED WIN rather
    than a test-fitting convenience. Under his ruled arm this billed world is
    STRUCTURALLY UNREACHABLE: clause 4 needs `all(not fetch_ok)`, and Gate A
    returns every attempted-and-failed slot ABOVE the ladder at $0, so a
    systemic day can no longer buy a synthesis it then rejects — exactly the
    $0.74 QA measured on one pause. The world is preserved HERE at the OFF arm
    because the overclaim it guards against is a claim about the DESIGN, which
    still permits a billed pause the moment the gates are off. The armed twin
    is the pin immediately below."""
    monkeypatch.setattr(analysis, "FETCH_SKIP_ARM", analysis.FETCH_SKIP_ARM_OFF)
    db.migrate()
    con = db.connect()
    try:
        seed_min(con)

        def chat_rejected_at_cost(key, prompt):
            return s_brief(), 0.37        # valid shape, uncitable map -> reject

        report = analysis.run_analysis(
            date=DATE, con=con, env=dict(ENV_OK), chat=chat_rejected_at_cost,
            sonar=sonar_none, fetch=_fetch_all_fail, sleep=lambda s: None)

        assert report["fetch_systemic_failure"] is True      # it still pauses
        total = con.execute(
            "SELECT COALESCE(SUM(cost_usd), 0) AS s FROM analysis_briefs"
            " WHERE date = ?", (DATE,)).fetchone()["s"]
        assert total > 0, "this world is supposed to have billed"
        # ...and yet the ruled default is untouched: no completed work exists,
        # so the retry re-bills no completed story.
        assert analysis.any_valid_brief(con, DATE) is False
        assert _completed_spend(con, DATE) == 0
    finally:
        con.close()


def test_clause4_at_his_arm_the_same_world_pauses_at_exactly_zero():
    """NL-151b — the ARMED twin of the pin above, and the measurement that
    makes the arming worth more than L1 alone.

    Same world, model up, every fetch failed, HIS DEFAULT ARM: the pause still
    fires and the stage charges nothing, because Gate A refuses each slot
    before the ladder can buy a brief the depth tier would then reject. The
    two pins together state the whole truth — the design permits a billed
    pause; the shipped arm cannot reach one.

    GATE R-E TRUTH-EDIT (2026-08-14): the chat sentinel is belt-and-braces
    noise, NOT the proof. Under MUT-H (Gate A neutered — the exact condition
    the old claim named) the sentinel's AssertionError is swallowed by
    analyze_story's per-slot `except Exception` into outcome `failed` at
    fake-$0 and THIS PIN STAYS GREEN — measured independently by QA and the
    gate, 2026-08-14. The register is held by name in
    test_nl151b_qa.py::test_the_systemic_pause_is_free_because_of_the_gates_
    not_the_fakes, observed red under the same mutant."""
    db.migrate()
    con = db.connect()
    try:
        seed_min(con)

        def chat_sentinel(key, prompt):
            raise AssertionError(
                "synthesis was bought on a systemic-failure day — Gate A has "
                "fallen below the ladder and the billed pause is back")

        report = analysis.run_analysis(
            date=DATE, con=con, env=dict(ENV_OK), chat=chat_sentinel,
            sonar=sonar_none, fetch=_fetch_all_fail, sleep=lambda s: None)

        assert report["fetch_systemic_failure"] is True
        assert report["total_usd"] == 0.0
        assert con.execute(
            "SELECT COALESCE(SUM(cost_usd), 0) AS s FROM analysis_briefs"
            " WHERE date = ?", (DATE,)).fetchone()["s"] == 0
        assert analysis.any_valid_brief(con, DATE) is False
        assert _completed_spend(con, DATE) == 0
    finally:
        con.close()


def test_clause4_a_story_that_still_produced_a_brief_never_pauses(monkeypatch):
    """BORN RED — the guard that keeps the pause from eating a working
    edition. Fetch fails everywhere, but the analysis still lands a valid
    brief from the material it has; an edition CAN be built, so the run must
    continue rather than pause and throw that work away.

    ARM-PINNED 2026-08-14 (NL-151b), and this one is a RULING-LEVEL note, not
    a fixture detail — it is the ONE behavioural delta the default flip
    carries on a previously-ruled contract. The "working edition" this world
    builds is a valid brief minted from an excerpt-only map AT A DEPTH SLOT,
    which is precisely the degraded depth coverage his L1 abolishes. Under the
    ruled arm that brief never mints, `not any_valid_brief` holds, and the day
    PAUSES where it used to publish.

    The guard itself is not weakened, and that matters more than the delta:
    its lawful successor — `test_clause4_does_not_fire_when_the_walk_finds_a_
    survivor` (both arms, test_nl151_fetch_skip.py) — proves that an edition
    with a HEALTHY story further down the ranking still runs, which is the
    case the guard was written to protect. Kept here at OFF because the
    historical behaviour is worth staying executable."""
    monkeypatch.setattr(analysis, "FETCH_SKIP_ARM", analysis.FETCH_SKIP_ARM_OFF)
    db.migrate()
    con = db.connect()
    try:
        seed_min(con)

        def chat_ok(key, prompt):
            return _c_brief(), 0.0

        report = analysis.run_analysis(
            date=DATE, con=con, env=dict(ENV_OK), chat=chat_ok,
            sonar=sonar_none, fetch=_fetch_all_fail, sleep=lambda s: None)
        # the fetch DID fail for every source...
        assert report["per_story"][0]["fetch_ok"] == 0
        assert report["per_story"][0]["fetch_attempted"] > 0
        # ...and the run still produced an edition-capable stage
        assert report["fetch_systemic_failure"] is False
        assert analysis.any_valid_brief(con, DATE) is True
    finally:
        con.close()


def test_clause4_tier_excluded_sources_are_policy_not_a_fetch_failure(monkeypatch):
    """BORN RED (KeyError: 'fetch_systemic_failure' at HEAD), and it ALSO
    carries a mutation receipt — the two proofs answer different questions.
    Born-red says the pin can see the new field; the mutation says the pin
    bites the specific conjunct it claims to guard rather than passing over a
    path the conjunct never touches.

    WHAT IT GUARDS: a POLICY-ONLY DAY. Every slot's sources sit outside the
    analyst's fetch tiers, so the whole stage ATTEMPTED nothing — that is the
    principal's 2026-07-06 boundary working exactly as ruled, not a failure to
    fetch. A run that never opened a socket has not discovered that fetching is
    broken, and must not claim the systemic pause.

    SCOPE, NARROWED BY FIX-1 (gate Ruling A, 2026-08-12). This pin used to be
    read as "an excluded slot means no pause". It never guarded that and must
    not: under the old `all(attempted and not ok)` shape ONE excluded slot in a
    mixed day vetoed the verdict and hid a whole-network outage. What survives —
    and what this pin actually guards — is the day where NOTHING was attempted
    anywhere. Its mixed-day sibling is
    `test_clause4_one_tier_excluded_slot_cannot_shield_a_network_outage` below,
    and the two together are the `any()`/`all()` split.

    THE ROUTE IS PROVEN. The brief here is deliberately made INVALID (it cites
    S1, which cannot exist on an excerpt-only map), so no valid brief is
    persisted and the last conjunct cannot mask the one under test. The
    `any(fetch_attempted)` conjunct is then the only thing standing between this
    run and a pause.
    MUTATION RECEIPT, RE-TAKEN AGAINST THE NEW PREDICATE (FIX-1 item 4; the
    receipt it replaces quoted `s["fetch_attempted"] and`, which no longer
    exists in the source): with the `any(s["fetch_attempted"] for s in
    report["per_story"]) and` conjunct DELETED from the verdict, this fails at
    `assert report["fetch_systemic_failure"] is False` — observed
    `AssertionError: assert True is False`.
    And one generation further back, the receipt quoted a `SystemicFetchFailure
    ... raised out of run_analysis`, which the shipped code cannot produce:
    after the mid-build layer move the stage only REPORTS the verdict and
    `analysis.py` has zero raise sites for it (`grep -n "raise
    SystemicFetchFailure" src/newslens/analysis.py` is empty; the raise lives in
    generate.py). The pin always bit;
    only the transcribed symptom belonged to the pre-move build, and
    receipts-are-transcribed-never-anticipated says it should have been re-taken
    when the layer moved."""
    import types

    db.migrate()
    con = db.connect()
    try:
        seed_min(con)

        def chat_rejected(key, prompt):
            # cites S1: valid shape, unciteable on this map -> rejected row,
            # so `any_valid_brief` stays False and cannot mask the guard.
            return s_brief(), 0.0

        # every outlet in the slot sits at headline_only: excluded before any
        # socket opens, so fetch_attempted is 0 for the whole stage.
        cfg = types.SimpleNamespace(sources=[
            types.SimpleNamespace(name="The Hill", tier="headline_only")])
        # `config` is imported INSIDE run_analysis, so the attribute has to be
        # patched on the module object the local import will bind.
        monkeypatch.setattr("newslens.config.load_sources",
                            lambda *a, **k: cfg)

        report = analysis.run_analysis(
            date=DATE, con=con, env=dict(ENV_OK), chat=chat_rejected,
            sonar=sonar_none, fetch=_fetch_all_fail, sleep=lambda s: None)

        # the preconditions that make the guard the ONLY thing holding
        assert report["per_story"][0]["fetch_attempted"] == 0
        assert report["per_story"][0]["fetch_ok"] == 0
        assert analysis.any_valid_brief(con, DATE) is False
        # ...and no systemic verdict was claimed
        assert report["fetch_systemic_failure"] is False
    finally:
        con.close()


def test_clause4_one_tier_excluded_slot_cannot_shield_a_network_outage(
        monkeypatch):
    """BORN RED — FIX-1 (gate Ruling A, 2026-08-12). The gate's P-A probe,
    turned into a pin.

    THE DAY: slot 1's outlet is in-tier, so the run really does open sockets —
    and every fetch fails. Slot 2's outlet is wholly tier-excluded, so it
    attempts nothing, as it would on a perfectly healthy day. The model is
    down, so no brief survives. Genuinely NOTHING FETCHED ANYWHERE.

    Under the shipped `all(attempted and not ok)` shape the verdict came back
    FALSE: slot 2's non-attempt vetoed it, and the reader got an unruled
    failure instead of his ruled pause + retry, while NL-146 would have read
    "this run is broken" instead of "retryable — nothing fetched". That made
    outage detection a function of EDITORIAL CONFIGURATION — one excluded
    outlet in the mix and the detector is off.

    BORN-RED RECEIPT, measured against the pre-FIX-1 tree (analysis.py sha
    386fd978bcb2e8499f37fa9a01d7745739d7ffc33e4433b1f38d89a0b06b088c):
        >   assert report["fetch_systemic_failure"] is True
        E   assert False is True

    The sibling above holds the other half: a day where NOTHING was attempted
    anywhere is policy, not outage, and still never pauses. `any()` is what
    separates them."""
    import types

    db.migrate()
    con = db.connect()
    try:
        # slot 2's material first — its own outlet, so the tier map can put the
        # two slots on opposite sides of the fetch boundary.
        with con:
            cur = con.execute(
                "INSERT INTO source_items (source_type, outlet, url, title,"
                " raw_excerpt) VALUES ('rss', ?, ?, ?, ?)",
                ("Excluded Wire", "https://excluded.example/w1",
                 "Wire item", "A second story the analyst may not fetch."))
            excluded_id = cur.lastrowid
        seed_min(con, n_items=2, slots_extra=[{
            "slot": "2", "story_title": "Wire story", "summary": "Wire.",
            "item_ids": [excluded_id], "outlets": ["Excluded Wire"],
            "matched_tags": [], "matched_memory": [], "override": False,
            "corroboration_label": "Reported by 1 named outlet"}])

        cfg = types.SimpleNamespace(sources=[
            types.SimpleNamespace(name="The Hill", tier="full"),
            types.SimpleNamespace(name="Excluded Wire", tier="headline_only")])
        monkeypatch.setattr("newslens.config.load_sources",
                            lambda *a, **k: cfg)

        report = analysis.run_analysis(
            date=DATE, con=con, env=dict(ENV_OK), chat=_chat_unreachable,
            sonar=sonar_none, fetch=_fetch_all_fail, sleep=lambda s: None,
            tiers_override=["full", "medium"])

        by_slot = {s["slot"]: s for s in report["per_story"]}
        assert len(by_slot) == 2, report["per_story"]
        # slot 1 TRIED and got nothing — the fetch layer really is dead
        assert by_slot[1]["fetch_attempted"] > 0
        assert by_slot[1]["fetch_ok"] == 0
        # slot 2 never opened a socket — policy, and it must not get a vote
        assert by_slot[2]["fetch_attempted"] == 0
        assert by_slot[2]["fetch_ok"] == 0
        # nothing survived, so an edition cannot be built from this day
        assert analysis.any_valid_brief(con, DATE) is False
        # ...and the outage is CALLED, one excluded slot notwithstanding
        assert report["fetch_systemic_failure"] is True
    finally:
        con.close()


def _pipeline_harness(con, monkeypatch, fake_model):
    """A `run_generate` that reaches the analysis stage and no further under
    its own power: ingest and rank are no-ops over a pre-seeded row, the model
    and audio are fakes. Modelled on test_nl106's refresh=True harness."""
    from newslens import ingest as ingest_mod, ranking as ranking_mod

    slots = [gen_slot(1, title="ONE")]
    seed_published_edition(con, A_DAY, slots)
    _fake_audio_ok(monkeypatch, [])

    def _noop_rank(*a, **k):
        r = type("R", (), {})()
        r.warnings = []
        return r

    # the REAL report type, empty — a hand-rolled stub drifts from the shape
    # the pipeline reads (it already cost two rounds of AttributeError here).
    monkeypatch.setattr(ingest_mod, "run_ingest",
                        lambda *a, **k: ingest_mod.IngestReport())
    monkeypatch.setattr(ranking_mod, "run_rank", _noop_rank)
    fake_model.narrative = stories_payload(slots)
    fake_model.script = compliant_script(slots)
    return slots


def test_clause4_the_pipeline_pauses_when_the_stage_reports_the_verdict(
        tmp_paths, fake_model, monkeypatch):
    """BORN RED — THE WIRING PROOF, and it is behavioural on purpose.

    An earlier draft of this pin asserted SOURCE TEXT (that the pause arm sat
    above the degrade handler). That is precisely the pin a comment can
    satisfy, which the org's own law says is not a pin. This one runs the
    pipeline: the stage reports the verdict, and the RUN must stop."""
    db.migrate()
    con = db.connect()
    try:
        _pipeline_harness(con, monkeypatch, fake_model)
        monkeypatch.setattr(analysis, "run_analysis", lambda **kw: {
            "per_story": [{"slot": 1, "outcome": "rejected",
                           "fetch_ok": 0, "fetch_attempted": 3}],
            "warnings": [], "total_usd": 0.0, "total_usd_shadow": 0.0,
            "fetch_systemic_failure": True})

        with pytest.raises(analysis.SystemicFetchFailure) as exc:
            generate.run_generate(date=A_DAY, con=con, env=GEN_ENV,
                                  refresh=True)
        assert str(exc.value) == analysis.FETCH_PAUSE_MESSAGE
        # NOTHING WAS PUBLISHED: the pause lands long before the promote, so
        # the reader keeps whatever edition they had and the retry is free.
        assert not con.execute(
            "SELECT narrative_text FROM briefings WHERE date = ?",
            (A_DAY,)).fetchone()["narrative_text"]
    finally:
        con.close()


def test_clause4_the_degrade_handler_is_live_and_still_cannot_swallow_the_pause(
        tmp_paths, fake_model, monkeypatch):
    """CARRIED INVARIANT, born GREEN and labeled as such — it passes at HEAD
    because the degrade handler predates this milestone, and calling it
    born-red would inflate the proof class.

    It is the CONTROL half of the wiring proof above, and the pair is what
    makes either one mean anything: this proves the stage-wide handler is
    LIVE (a generic stage exception still degrades to feed-excerpt material
    and the run completes and publishes), so the test above cannot be passing
    merely because that handler was broken or deleted. Together they say the
    handler works AND the pause still escapes it — which is the whole reason
    the raise sits outside the try rather than in an except arm."""
    db.migrate()
    con = db.connect()
    try:
        _pipeline_harness(con, monkeypatch, fake_model)

        def _boom(**kw):
            raise RuntimeError("analyst seat exploded")

        monkeypatch.setattr(analysis, "run_analysis", _boom)

        rep = generate.run_generate(date=A_DAY, con=con, env=GEN_ENV,
                                    refresh=True)
        assert any("analysis stage unavailable this run" in w
                   for w in rep.warnings), rep.warnings
        # ...and the run really did complete and publish
        assert con.execute(
            "SELECT narrative_text FROM briefings WHERE date = ?",
            (A_DAY,)).fetchone()["narrative_text"]
    finally:
        con.close()


def test_clause4_the_pause_is_marked_retryable_for_nl146_to_inherit(
        tmp_paths, fake_model, monkeypatch):
    """BORN RED, and BEHAVIOURAL — rewritten in fix loop 1 (QA finding F2).

    WHAT THIS REPLACES AND WHY: the first version read
    `inspect.getsource(generate.run_generate)` and asserted the strings
    `"paused"`, `"fetch"`, `"retryable"`, `"status": "failed"` appeared in it.
    QA falsified it by execution — deleting the two `entry[...]` assignments and
    leaving the four tokens in a COMMENT left the pin GREEN with the seam
    functionally gone. A pin a comment can satisfy is not a pin (NL-138/139,
    ratified 2026-08-06), and this was the only guard on the NL-146 fork.

    So it now drives the pause through `run_generate` and reads the artifact
    NL-146 will actually read: the entry in `generation_log.jsonl`.

    `status` deliberately stays 'failed' so no existing consumer moves; the fork
    (`paused`/`retryable`) rides beside it, which is exactly what a scheduled
    run needs to tell "retryable — nothing fetched" from "this run is broken"."""
    db.migrate()
    con = db.connect()
    try:
        _pipeline_harness(con, monkeypatch, fake_model)
        monkeypatch.setattr(analysis, "run_analysis", lambda **kw: {
            "per_story": [{"slot": 1, "outcome": "rejected",
                           "fetch_ok": 0, "fetch_attempted": 3}],
            "warnings": [], "total_usd": 0.0, "total_usd_shadow": 0.0,
            "fetch_systemic_failure": True})

        with pytest.raises(analysis.SystemicFetchFailure):
            generate.run_generate(date=A_DAY, con=con, env=GEN_ENV,
                                  refresh=True)

        lines = [json.loads(l) for l in
                 (paths.DATA_DIR / "generation_log.jsonl")
                 .read_text(encoding="utf-8").splitlines() if l.strip()]
        runs = [e for e in lines if e.get("date") == A_DAY
                and "status" in e and "stage" not in e]
        assert runs, "the paused run left no generation-log entry"
        entry = runs[-1]
        # the word every existing consumer already reads — unmoved
        assert entry["status"] == "failed"
        # ...and the NL-146 fork beside it
        assert entry["paused"] == "fetch"
        assert entry["retryable"] is True
        # the pause message itself is on the record, not just a type name
        assert entry["error"] == analysis.FETCH_PAUSE_MESSAGE
    finally:
        con.close()


def test_the_ruled_sentence_is_omitted_by_the_founding_page_filter():
    """DISCLOSED GAP, pinned so it cannot be forgotten or later mis-described.

    The Today panel renders the run's error verbatim, so the ruled sentence
    reaches the reader there. The FOUNDING page (a stranger's first screen)
    runs every run-sentence through an allowlist whose safe form is
    "<phase> failed: <plain words>"; this sentence is a comma clause and is
    therefore OMITTED there, falling back to the panel's generic text.

    Carried rather than closed: the two available fixes are rewording HIS
    sentence to fit a grammar, or widening a build-blocking C1 safety seam as
    an unrequested mid-milestone rider. Both are checkpoint acts."""
    assert commissioning.unfit_for_readers(analysis.FETCH_PAUSE_MESSAGE) is True
