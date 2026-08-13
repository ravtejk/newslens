"""Stage-0 M2 — per-profile plumbing, the shadow-cap fix, the script net.

Contract (dispatch 2026-07-25; multi-user brief §2 Stage-0; DECISIONS
[2026-07-25] STAGE-0 M1 SHIPPED "M2 CONTRACT RIDERS"; engineering-3 NL-95
design note; M0 QA finding F2):

  1. Interests/ingest/rank read the ACTIVE profile's sources.yaml — never the
     founder's. The default profile ADOPTS his file zero-move (the literal
     pre-M1 object). A zero-interest profile still rank-REFUSES, by name.
  2. Caps and spend ledgers bind the acting profile: one profile's spend can
     never decrement another's headroom, and ledger rows land in the acting
     profile's DATA_DIR/DB only.
  3. NL-95: edition-scoped caps bind usd_SHADOW at BOTH remaining cap sites;
     every persisted cost_usd stays usd_CHARGED.
  5. The three side entrypoints refuse a ghost profile instead of minting one.
  6. A freshly provisioned profile follows NOBODY (the founder's three analysts
     do not ride the template) and carries decided, not inherited, settings.
  7. A directory literally named profiles/default is VISIBLE, not swallowed.

Items 4 (the script continuity net) and 8 (the shuffle plugin) live where the
things they guard live: RED-2 in tests/test_stage0_m0_coldstart.py (its
xfail marker comes off in this milestone) and the plugin's own behaviour in
test_shuffle_plugin.py.

Sandbox: the tree conftest's autouse fixtures apply. Disk-touching profile
tests provision under tmp_path via paths.anchor_dir(); the `real_route`
harness (M1's) drops the module-dict shadows and re-anchors PROJECT_ROOT so
the code takes the SAME route it takes on the principal's machine. $0 by
construction — every LLM seam is injected.
"""
from __future__ import annotations

import json

import pytest

from newslens import (analysis, config, db, generate, llm, paths, profiles,
                      ranking)

from test_generate import seed_briefing, slot

GUARDED_NAMES = ("DATA_DIR", "DB_PATH", "SOURCES_FILE", "ENV_FILE", "MEMORY_FILE")

FOUNDER_SOURCES = (
    "sources:\n"
    "  - name: Founder Outlet\n"
    "    rss_url: https://founder.invalid/feed\n"
    "interests:\n"
    "  broad: [founder-domain]\n"
    "  granular: [founder-topic]\n"
)


@pytest.fixture
def real_route(monkeypatch, tmp_path):
    """M1's end-to-end routing harness, hardened for the DEFAULT arm.

    No module-dict shadows, no redirection env vars, PROJECT_ROOT re-anchored
    at tmp_path: paths.__getattr__ — the thing that actually routes a profile
    in a real run — is what answers.

    M2 addition, and it matters: `_GUARDED` is built ONCE at import against the
    REAL PROJECT_ROOT, and the sanctioned DEFAULT arm returns `_GUARDED[name]`
    verbatim (that literal return IS the zero-move promise). So re-anchoring
    PROJECT_ROOT alone leaves every default-profile resolution pointing at the
    principal's real sources.yaml / data/ — M1's tests never noticed because
    they only ever routed NON-default profiles through this harness. Observed
    live while writing these pins: a default-arm assertion read his real
    sources.yaml and his real generation_log.jsonl (read-only; all four sacred
    files verified byte-identical). Re-anchor the table itself, and the
    identity pin below still proves zero-move because it is the SAME object
    either way.
    """
    for name in GUARDED_NAMES:
        monkeypatch.delitem(vars(paths), name, raising=False)
    for var in ("NEWSLENS_DATA_DIR", "NEWSLENS_DB_PATH", "NEWSLENS_SOURCES_FILE",
                "NEWSLENS_ENV_FILE", "NEWSLENS_MEMORY_FILE"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr(paths, "PROJECT_ROOT", tmp_path)
    for key, value in paths.profile_layout(paths.DEFAULT_PROFILE, tmp_path).items():
        monkeypatch.setitem(paths._GUARDED, key, value)
    monkeypatch.setenv("NEWSLENS_REAL_DATA", "1")
    (tmp_path / "sources.yaml").write_text(FOUNDER_SOURCES, encoding="utf-8")
    return tmp_path


def _write_interests(path, broad=(), granular=()):
    path.write_text(
        "sources:\n"
        "  - name: Their Outlet\n"
        "    rss_url: https://theirs.invalid/feed\n"
        "interests:\n"
        f"  broad: [{', '.join(broad)}]\n"
        f"  granular: [{', '.join(granular)}]\n", encoding="utf-8")


# ===========================================================================
# ITEM 1 — interests are the ACTIVE profile's, never the shared list
# ===========================================================================

def test_the_active_profiles_interests_are_the_ones_rank_ranks_on(real_route):
    """The ranking tag vocabulary — the thing personalization IS — comes from
    the active profile's own file. The founder's tags must not appear in a
    tester's prompt, and vice versa."""
    tmp_path = real_route
    profiles.create("alice")
    _write_interests(tmp_path / "profiles" / "alice" / "sources.yaml",
                     broad=("alice-domain",), granular=("alice-topic",))

    paths.set_profile("alice")
    cfg = config.load_sources()
    assert cfg.interests_broad == ["alice-domain"]
    assert cfg.interests_granular == ["alice-topic"]
    # through the REAL prompt builder — the vocabulary the rank seat is sent
    prompt = ranking.build_prompt("2026-07-25", [], cfg, [], "last 24h")
    assert "alice-domain" in prompt and "alice-topic" in prompt
    assert "founder-domain" not in prompt
    assert "founder-topic" not in prompt


def test_a_non_default_profile_never_reads_the_founders_sources_file(real_route):
    """Mechanical, not by inspection: make the founder's sources.yaml
    unreadable-by-tripwire and prove a profile run never opens it.

    The founder's sources.yaml is a SACRED file (a standing local edit). A
    profile that read it would import his outlet list, his followed analysts
    and his tags into a tester's world — the interests half of the same class
    the M1 seeding kill closed on threads."""
    tmp_path = real_route
    profiles.create("alice")
    _write_interests(tmp_path / "profiles" / "alice" / "sources.yaml",
                     broad=("alice-domain",))
    founder_file = tmp_path / "sources.yaml"
    opened = []
    real_read_text = type(founder_file).read_text

    def watched(self, *a, **kw):
        opened.append(str(self))
        return real_read_text(self, *a, **kw)

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(type(founder_file), "read_text", watched)
        paths.set_profile("alice")
        cfg = config.load_sources()
    assert cfg.interests_broad == ["alice-domain"]
    assert str(founder_file) not in opened, (
        f"a non-default profile read the founder's sources.yaml: {opened}")


def test_the_default_profile_adopts_the_founders_interests_zero_move(real_route):
    """Zero-move adoption for interests, as a mechanism: the default arm
    resolves the LITERAL pre-M1 object, so the founder's tags are his current
    tags by identity — nothing is copied, migrated or re-derived."""
    tmp_path = real_route
    profiles.create("alice")
    paths.set_profile(None)
    assert paths.SOURCES_FILE is paths._GUARDED["SOURCES_FILE"]
    cfg = config.load_sources()
    assert cfg.interests_broad == ["founder-domain"]
    assert cfg.interests_granular == ["founder-topic"]


def test_a_zero_interest_profile_still_refuses_to_rank_and_names_itself(
        real_route):
    """The M1 gate-endorsed posture, not weakened: a freshly provisioned
    profile refuses to rank until its reader chooses tags. M2 adds the honesty
    a multi-profile world needs — the refusal NAMES the profile and the file
    the reader has to edit, instead of saying 'sources.yaml' into a world with
    several of them."""
    tmp_path = real_route
    profiles.create("alice")
    paths.set_profile("alice")
    with pytest.raises(ranking.RankingError) as exc:
        ranking.run_rank(date="2026-07-25")
    msg = str(exc.value)
    assert "alice" in msg, f"the refusal did not name the profile: {msg}"
    assert str(tmp_path / "profiles" / "alice" / "sources.yaml") in msg, (
        f"the refusal did not name the file to edit: {msg}")


def test_a_malformed_profile_gets_the_ranking_refusal_not_a_profile_error(
        monkeypatch):
    """BORN-RED (gate F6). The refusal above NAMES the acting profile, and the
    naming step used to call paths.current_profile() bare — which RAISES on a
    malformed NEWSLENS_PROFILE. A library caller (run_rank(cfg=..., env={}) —
    the shape the served UI and any future embedder use) therefore got a
    ProfileError thrown out of the message-BUILDING code instead of the
    RankingError it was owed: the refusal destroyed itself while formatting.

    The contract: the no-interests refusal is reachable with ANY profile state.
    A profile that cannot be resolved degrades to '<unresolved>' in the text —
    honest, and still a RankingError the CLI prints and exits 1 on.
    """
    monkeypatch.setenv("NEWSLENS_PROFILE", "../data")   # slug refusal shape
    cfg = config.SourcesConfig()
    assert not cfg.has_interests and not cfg.problems   # the branch under test

    with pytest.raises(ranking.RankingError) as exc:
        ranking.run_rank(date="2026-07-25", cfg=cfg, env={})

    assert not isinstance(exc.value, paths.ProfileError), (
        "the profile refusal escaped the ranking refusal")
    msg = str(exc.value)
    assert "has no interests configured" in msg, msg
    assert "<unresolved>" in msg, (
        f"the degraded label did not say the profile was unresolvable: {msg}")


def test_discovery_steers_on_the_active_profiles_interests(real_route):
    """The Sonar discovery query is built from interests — the 'interest
    steering bleed' the brief names as the reason fetch must be per-profile.
    The prompt must carry THIS reader's tags only."""
    from newslens import discovery
    tmp_path = real_route
    profiles.create("alice")
    _write_interests(tmp_path / "profiles" / "alice" / "sources.yaml",
                     broad=("alice-domain",), granular=("alice-topic",))
    paths.set_profile("alice")
    prompt = discovery.build_prompt(config.load_sources())
    assert "alice-domain" in prompt and "alice-topic" in prompt
    assert "founder-domain" not in prompt and "founder-topic" not in prompt


def test_ingest_writes_into_the_acting_profiles_corpus_only(real_route):
    """Per-profile ingest end to end through the real route: alice's items
    land in alice's database and the founder's world never learns them."""
    tmp_path = real_route
    profiles.create("alice")
    _write_interests(tmp_path / "profiles" / "alice" / "sources.yaml",
                     broad=("alice-domain",))
    paths.set_profile("alice")
    db.migrate()
    con = db.connect()
    try:
        con.execute(
            "INSERT INTO source_items (outlet, title, url, published_at,"
            " fetched_at, source_type) VALUES"
            " ('Their Outlet', 'Alice item', 'https://theirs.invalid/1',"
            "  '2026-07-25T00:00:00Z', '2026-07-25T00:00:00Z', 'rss')")
        con.commit()
    finally:
        con.close()
    assert (tmp_path / "profiles" / "alice" / "data" / "newslens.db").exists()
    assert not (tmp_path / "data").exists(), (
        "the founder's corpus was created by a profile's ingest")


# ===========================================================================
# ITEM 2 — caps and spend ledgers bind the ACTING profile
# ===========================================================================

def test_a_profiles_spend_ledger_lands_in_its_own_data_dir_only(real_route):
    """The generation log IS the spend ledger. A run under alice appends to
    alice's log; the founder's log is not created, let alone appended to."""
    tmp_path = real_route
    profiles.create("alice")
    paths.set_profile("alice")
    generate.log_generation({"date": "2026-07-25", "status": "ok",
                             "total_usd": 0.42})
    alice_log = tmp_path / "profiles" / "alice" / "data" / "generation_log.jsonl"
    assert alice_log.exists()
    assert json.loads(alice_log.read_text(encoding="utf-8").splitlines()[0]
                      )["total_usd"] == 0.42
    assert not (tmp_path / "data" / "generation_log.jsonl").exists(), (
        "a profile's spend landed in the founder's ledger")


def test_one_profiles_spend_is_invisible_to_every_other_profiles_readers(
        real_route):
    """Through a REAL ledger reader (server._log_entry_for, the one the UI
    renders spend from): alice's run is visible to alice and to nobody else.
    Two readers of the same figure resolving different files is what makes the
    per-run cap a per-profile cap."""
    from newslens import server
    tmp_path = real_route
    profiles.create("alice")
    profiles.create("bob")

    paths.set_profile("alice")
    generate.log_generation({"date": "2026-07-25", "status": "ok",
                             "total_usd": 0.99})
    assert server._log_entry_for("2026-07-25")["total_usd"] == pytest.approx(0.99)

    paths.set_profile("bob")
    assert server._log_entry_for("2026-07-25") is None, (
        "bob can see alice's spend")
    paths.set_profile(None)
    assert server._log_entry_for("2026-07-25") is None, (
        "the founder's ledger reader picked up a tester's run")


def test_the_founders_ledger_is_never_created_by_another_profiles_run(
        real_route):
    """The direction that would burn HIM: two testers logging runs must not
    create, let alone append to, the founder's spend ledger."""
    tmp_path = real_route
    for slug in ("alice", "bob"):
        profiles.create(slug)
        paths.set_profile(slug)
        generate.log_generation({"date": "2026-07-25", "status": "ok",
                                 "total_usd": 1.50})
    assert not (tmp_path / "data").exists()


def test_no_spend_ledger_path_escapes_the_profile_dimension():
    """Structural pin (the recurrence guard, not a behaviour check): every
    composition of the generation log's path in the package hangs off
    paths.DATA_DIR, which is profile-resolved. A future hardcoded
    'data/generation_log.jsonl' would put one reader's money in another's
    record — and paths.__getattr__'s own docstring names hardcoded 'data/...'
    strings as its one acknowledged limit."""
    import re
    from pathlib import Path as _P
    src = _P(paths.__file__).parent
    # A PATH literal has no whitespace in it; the prose that mentions the
    # ledger by name in diagnose's human-readable report does. That is the
    # whole discrimination — deliberately crude, so it stays readable.
    path_literal = re.compile(r"""['"]([^'"\s]*data/[^'"\s]*generation_log[^'"]*)['"]""")
    offenders = []
    for py in sorted(src.glob("*.py")):
        for i, line in enumerate(py.read_text(encoding="utf-8").splitlines(), 1):
            if "generation_log" not in line or path_literal.search(line) is None:
                continue
            if "DATA_DIR" not in line:
                offenders.append(f"{py.name}:{i}: {line.strip()}")
    assert offenders == [], (
        "a spend-ledger path bypasses the profile dimension: " + str(offenders))


def test_ledger_rows_land_in_the_acting_profiles_database_only(real_route):
    """The DB-side ledger (consumption/instrumentation rows) is namespaced by
    the per-profile database — proven by writing through the guarded route,
    not by reading the layout table."""
    tmp_path = real_route
    profiles.create("alice")
    profiles.create("bob")
    paths.set_profile("alice")
    db.migrate()
    con = db.connect()
    try:
        ranking.log_failed_run(con, "2026-07-25", "alice's failure")
        con.commit()
        n_alice = con.execute(
            "SELECT COUNT(*) c FROM ranking_runs").fetchone()["c"]
    finally:
        con.close()
    assert n_alice >= 1

    paths.set_profile("bob")
    db.migrate()
    con_b = db.connect()
    try:
        assert con_b.execute(
            "SELECT COUNT(*) c FROM ranking_runs").fetchone()["c"] == 0
    finally:
        con_b.close()


# ===========================================================================
# ITEM 3 — NL-95: the edition cap binds usd_SHADOW; charged stays charged
# ===========================================================================

def _subscription_analyst(monkeypatch, shadow_per_call: float):
    """Put the analyst seat on the SUBSCRIPTION lane at the cost_fields level:
    charged 0.0, shadow > 0. Monkeypatching HERE (not `chat=`) is deliberate —
    it exercises call_analysis_model's OWN accumulation, which is the site the
    fix changes."""
    real_cost_fields = llm.cost_fields

    def fake(cfg, usage, *, fallback_reason=None):
        out = dict(real_cost_fields(cfg, usage, fallback_reason=fallback_reason))
        out["usd_charged"] = 0.0
        out["usd_shadow"] = shadow_per_call
        return out

    monkeypatch.setattr(llm, "cost_fields", fake)


def test_nl95_analysis_cap_binds_shadow(monkeypatch, tmp_path):
    """BORN-RED. On the subscription lane the analyst's charged spend is $0
    while its SHADOW spend is real — and the edition cap must bind the shadow.
    The teeth: a cap sized so slot 1's shadow exhausts it must make slot 2 land
    `skipped-budget`. Today slot 2 sails through, because the ladder decrements
    by charged (= $0).

    Slot 1's own verdict is deliberately not asserted — spend accrues before
    brief validation, so whether it ships or is rejected does not change what
    the cap must do next."""
    _subscription_analyst(monkeypatch, shadow_per_call=0.40)
    db.migrate()
    con = db.connect()
    try:
        seed_briefing(con, "2026-07-25", [slot(1, title="One"),
                                          slot(2, title="Two")])

        def chat(key, prompt):
            # the seam a real analyst call returns through, post-fix shape
            return ({"headline": "h", "body": "b"}, 0.0, 0.40)

        def sonar(key, title, claims):
            return ([], 0.0, "skipped")

        rep = analysis.run_analysis(
            date="2026-07-25", con=con,
            env={"OPENAI_API_KEY": "sk-qa-fake",
                 "BUDGET_CAP_USD_PER_RUN": "0.40"},   # exactly one slot's shadow
            chat=chat, sonar=sonar, sleep=lambda s: None,
            tiers_override=["full", "medium"])
    finally:
        con.close()

    assert rep["total_usd"] == 0.0, (
        "charged total must stay $0 on the subscription lane")
    assert rep["total_usd_shadow"] > 0, "the shadow total is not reported"
    outcomes = {s["slot"]: s["outcome"] for s in rep["per_story"]}
    assert outcomes.get(2) == "skipped-budget", (
        "slot 2 ran even though slot 1's SHADOW spend had exhausted the cap — "
        f"the cap is still bound to charged dollars. outcomes={outcomes}")


def test_nl95_api_lane_keeps_charged_and_shadow_equal(monkeypatch, tmp_path):
    """The fall-over is correct BY CONSTRUCTION, not by coincidence: on the api
    lane the two totals are the same number (the test_generate.py:1196 class,
    extended to the new keys)."""
    db.migrate()
    con = db.connect()
    try:
        seed_briefing(con, "2026-07-25", [slot(1, title="One")])

        def chat(key, prompt):
            return ({"headline": "h", "body": "b"}, 0.02, 0.02)

        rep = analysis.run_analysis(
            date="2026-07-25", con=con, env={"OPENAI_API_KEY": "sk-qa-fake"},
            chat=chat, sonar=lambda k, t, c: ([], 0.0, "skipped"),
            sleep=lambda s: None, tiers_override=["full"])
    finally:
        con.close()
    assert rep["total_usd"] == rep["total_usd_shadow"]


def test_nl95_per_story_row_carries_charged_and_shadow_side_by_side(
        monkeypatch, tmp_path):
    """BORN-RED (additive key). `cost_usd` keeps meaning REAL money — the
    column it feeds is charged — and the shadow figure rides beside it under a
    new name, so no historical row's meaning moves."""
    _subscription_analyst(monkeypatch, shadow_per_call=0.05)
    db.migrate()
    con = db.connect()
    try:
        seed_briefing(con, "2026-07-25", [slot(1, title="One")])
        rep = analysis.run_analysis(
            date="2026-07-25", con=con, env={"OPENAI_API_KEY": "sk-qa-fake"},
            chat=lambda k, p: ({"headline": "h", "body": "b"}, 0.0, 0.05),
            sonar=lambda k, t, c: ([], 0.0, "skipped"),
            sleep=lambda s: None, tiers_override=["full"])
    finally:
        con.close()
    row = rep["per_story"][0]
    assert row["cost_usd"] == 0.0
    assert row["usd_shadow"] == pytest.approx(0.05)


def test_nl95_call_analysis_model_returns_the_dual_track_triple(monkeypatch):
    """BORN-RED. The root: ONE cost_fields call per attempt feeds BOTH
    accumulators, and the function hands back (parsed, charged, shadow)."""
    _subscription_analyst(monkeypatch, shadow_per_call=0.03)
    monkeypatch.setattr(analysis, "_analysis_chat", lambda key, prompt: {
        "choices": [{"finish_reason": "stop",
                     "message": {"content": '{"ok": true}'}}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 5}})
    monkeypatch.setenv("NEWSLENS_LANE_ANALYST", "api")
    parsed, charged, shadow = analysis.call_analysis_model("k", "p")
    assert parsed == {"ok": True}
    assert charged == 0.0
    assert shadow == pytest.approx(0.03)


def test_nl95_baseline_cap_binds_shadow(monkeypatch, tmp_path):
    """BORN-RED, the second cap site. Two awaiting threads on the subscription
    lane with a cap under 2x the per-baseline shadow: thread 2 must land
    `skipped-budget`. Charged stays $0 on the persisted rows; the report
    reports BOTH figures."""
    from newslens import memory, memory_core
    db.migrate()
    con = db.connect()
    try:
        for topic in ("Thread One", "Thread Two"):
            memory.add_thread(con, topic)
        con.commit()

        calls = {"n": 0}

        def chat(key, prompt):
            # call_analysis_model returns PARSED json — so does this seam.
            calls["n"] += 1
            return ({"backgrounder": "On Jul 1, 2026 the corridor closed. "
                                     "By Jul 5, 2026 traffic had halved.",
                     "state_seed": "The corridor is closed.",
                     "cites": ["https://example.invalid/a"]}, 0.0, 0.50)

        rep = generate.run_baseline_backfill(
            all_threads=True, con=con,
            env={"BUDGET_CAP_USD_PER_RUN": "0.50"},   # exactly one baseline
            date="2026-07-25", chat=chat)

        outcomes = {g["thread"]: g["outcome"] for g in rep.generated}
        rows = con.execute(
            "SELECT cost_usd FROM thread_baselines").fetchall()
    finally:
        con.close()

    assert outcomes.get("Thread Two") == "skipped-budget", (
        "the second baseline ran even though the first's SHADOW spend had "
        f"eaten the cap — outcomes={outcomes}")
    assert all(r["cost_usd"] == 0.0 for r in rows), (
        "thread_baselines.cost_usd must stay CHARGED (=$0 on subscription)")
    assert rep.spent_usd == pytest.approx(0.50), (
        "spent_usd is the CAP figure (shadow) — thread one's full shadow")
    assert rep.charged_usd == 0.0, "charged_usd is the real-money figure"


def test_nl95_edition_cap_decrements_by_the_analysis_stages_shadow(
        monkeypatch, tmp_path):
    """BORN-RED, and the pin the loop's own mutation pass proved was MISSING.

    Enforcement fix #2 lives at the generate side: `spent += a_rep[
    "total_usd_shadow"]`. Reverting that one line to the charged total left the
    entire suite green — an enforcement change that disturbs no test, which
    ENGINEERING.md says to treat as suspicious by default. This is the red test
    only that wiring can flip.

    The teeth: an analysis stage that charged $0 but burned $0.90 of SHADOW
    must leave the writer no headroom under a $0.90 cap.

    NL-148 FIX LOOP 1 — THE TEETH MOVED, AND THEY GOT SHARPER. The principal's
    2026-08-12 ruling demoted the shadow-only kill to a warn (his run died on
    phantom money), so "the narrative step aborts" is no longer how a run
    without headroom behaves on the subscription lane. The enforcement this pin
    exists for — the analysis stage's SHADOW reaching the edition's `spent` —
    is untouched and still load-bearing (it is what the derating ladder and the
    warn both read), so the pin now reads the FIGURE instead of the exception:
    the budget warn must quote $0.9000 of shadow. Bound to charged, `spent`
    would be $0.0000, the ~$0.40 narrative estimate would fit inside the $0.90
    cap, and NO WARN WOULD FIRE AT ALL — a stricter red than the old one, which
    only knew that some GenerateError arrived."""
    from newslens import analysis as analysis_mod
    from newslens import ingest as ingest_mod

    monkeypatch.setattr(analysis_mod, "run_analysis", lambda **kw: {
        "stage": "analysis", "status": "ok", "per_story": [], "warnings": [],
        "derating": False,
        "total_usd": 0.0,            # subscription lane: nothing charged
        "total_usd_shadow": 0.90,    # ...but the cap must feel this
    })

    # refresh=True chains ingest -> rank before the writer; both are stubbed so
    # this test is about ONE thing: whether the analysis stage's shadow spend
    # reached the edition's `spent`.
    def fake_ingest(*a, **kw):
        from newslens.ingest import IngestReport
        return IngestReport(attempted=1, succeeded=["QA Outlet"])

    def fake_rank(date=None, con=None, env=None, **kw):
        seed_briefing(con, date, [slot(1, title="One")])
        r = type("R", (), {})()
        r.warnings = []
        return r

    monkeypatch.setattr(ingest_mod, "run_ingest", fake_ingest)
    monkeypatch.setattr(ranking, "run_rank", fake_rank)
    db.migrate()
    con = db.connect()
    try:
        # the narrative call itself has no fake behind it, so the run still
        # ends in a GenerateError — but from the CALL, not from the cap. What
        # the pin reads is the warn the cap gate left on the record first.
        with pytest.raises(generate.GenerateError):
            generate.run_generate(
                date="2026-07-25", con=con,
                env={"OPENAI_API_KEY": "sk-qa-fake",
                     "BUDGET_CAP_USD_PER_RUN": "0.90"},
                refresh=True)
    finally:
        con.close()
    entry = json.loads(
        (paths.DATA_DIR / "generation_log.jsonl")
        .read_text(encoding="utf-8").strip().splitlines()[-1])
    warns = [w for w in entry["warnings"]
             if w.startswith("budget: narrative continued past")]
    assert warns, (
        "the narrative cap gate saw headroom it should not have had — the "
        f"analysis stage's shadow spend never reached `spent`. Got: {entry['warnings']}")
    assert "$0.9000 shadow" in warns[0], warns[0]
    assert "$0.0000 actually charged" in warns[0], warns[0]


def test_nl95_failed_run_fold_never_fabricates_charged_dollars(monkeypatch,
                                                               tmp_path):
    """BORN-RED. The failed-run fold writes the MONEY record. Its analysis row's
    `usd` must stay charged (real money); the shadow figure rides beside it
    under its own key. A fold that summed shadow into `usd` would invent
    dollars the principal never spent."""
    rep = generate.GenReport(date="2026-07-25", variant="A")
    rep.analysis_usd = 0.0            # subscription lane: nothing charged
    rep.analysis_shadow_usd = 0.44    # but 44 cents of shadow
    ledger = generate.fold_late_steps(rep)
    analysis_rows = [r for r in ledger if r.get("step") == "analysis"]
    assert analysis_rows, "the analysis row vanished from the failed-run fold"
    row = analysis_rows[0]
    assert row["usd"] == 0.0, (
        f"the money record claims ${row['usd']} charged that was never spent")
    assert row["usd_shadow"] == pytest.approx(0.44)


# ===========================================================================
# ITEM 5 — the three side entrypoints refuse a ghost profile
# ===========================================================================

# Each instrument's cheapest legal invocation — all three are dry-run/$0 by
# default; moat-battery's subcommand is required, and t3 is its no-LLM one.
SIDE_ENTRYPOINTS = [("battery", []), ("moat_battery", ["t3"]),
                    ("follow_altitude", [])]


@pytest.mark.parametrize("module_name,argv", SIDE_ENTRYPOINTS)
def test_side_entrypoints_refuse_a_ghost_profile_and_mint_nothing(
        module_name, argv, real_route, monkeypatch, capsys):
    """BORN-RED (gate MINOR, F1's cascade through a side door). An exported
    typo'd NEWSLENS_PROFILE plus a deliberate --run writes this instrument's
    artifacts under a world nobody created — and once that directory exists,
    require_exists accepts the typo everywhere else. Dry-run defaults are why
    this is zero-writes TODAY; that is a default, not a guard.

    The refusal must also come FIRST: rc 2 before the instrument's own
    read-the-record failures, so a ghost is never merely masked by an
    unrelated error."""
    import importlib
    tmp_path = real_route
    mod = importlib.import_module(f"newslens.{module_name}")
    monkeypatch.setenv("NEWSLENS_PROFILE", "alcie")   # the typo

    rc = mod.main(list(argv))
    err = capsys.readouterr().err
    assert rc == 2, f"{module_name}.main accepted a ghost profile (rc={rc})"
    assert "alcie" in err and "profile" in err.lower()
    assert not (tmp_path / "profiles" / "alcie").exists(), (
        f"{module_name}.main MINTED the ghost world")


@pytest.mark.parametrize("module_name,argv", SIDE_ENTRYPOINTS)
def test_side_entrypoints_refuse_a_malformed_profile_without_a_traceback(
        module_name, argv, real_route, monkeypatch, capsys):
    """A malformed NEWSLENS_PROFILE is a boundary refusal, never a stack trace
    out of an instrument (the doctor's copy of the same rule)."""
    import importlib
    mod = importlib.import_module(f"newslens.{module_name}")
    monkeypatch.setenv("NEWSLENS_PROFILE", "../data")
    rc = mod.main(list(argv))
    assert rc == 2
    assert "profile" in capsys.readouterr().err.lower()


@pytest.mark.parametrize("module_name,argv", SIDE_ENTRYPOINTS)
def test_side_entrypoints_do_not_inherit_an_earlier_in_process_profile_pin(
        module_name, argv, real_route, capsys):
    """The doctor's F2 leak, checked on the side doors: an in-process
    cli.main(['--profile', 'alice', ...]) leaves a module-level pin that
    nothing unsets, and a later battery run must NOT silently operate on
    alice's world."""
    import importlib
    tmp_path = real_route
    profiles.create("alice")
    paths.set_profile("alice")
    mod = importlib.import_module(f"newslens.{module_name}")
    mod.main(list(argv))
    capsys.readouterr()
    assert paths.current_profile() == paths.DEFAULT_PROFILE, (
        f"{module_name}.main inherited the previous call's profile pin")


# ===========================================================================
# ITEM 6 — a fresh profile follows NOBODY (EDITORIAL: one-line reversible)
# ===========================================================================

def test_a_fresh_profile_follows_nobody(real_route):
    """BORN-RED. The founder's three followed analysts (Tooze, Smith,
    Yglesias) rode the shipped template into every new profile, carrying a
    personal-impact ranking boost independent of topic match — his taste,
    pre-installed in a stranger's world. The outlets stay in the catalog; the
    FOLLOW does not.

    EDITORIAL, principal-reversible in one line per source (uncomment the
    `followed_analyst: true` the template now ships commented)."""
    tmp_path = real_route
    profiles.create("alice")
    cfg = config.load_sources(tmp_path / "profiles" / "alice" / "sources.yaml")
    assert cfg.followed_analyst_sources == [], (
        "a fresh profile is born following "
        f"{[s.name for s in cfg.followed_analyst_sources]}")
    names = {s.name for s in cfg.sources}
    for outlet in ("Chartbook (Adam Tooze)", "Noahpinion (Noah Smith)",
                   "Slow Boring (Matt Yglesias)"):
        assert outlet in names, (
            f"{outlet} was removed from the catalog — the follow was supposed "
            "to go, not the outlet")


def test_the_shipped_template_settings_are_decided_not_inherited(real_route):
    """The settings block rode the template by copy. It stays — a fresh
    profile needs values — but as DECIDED values: the $0 local voice (a new
    reader must not start spending on TTS by default) and the shipped
    thread-steering default."""
    tmp_path = real_route
    profiles.create("alice")
    cfg = config.load_sources(tmp_path / "profiles" / "alice" / "sources.yaml")
    assert cfg.tts_engine == "kokoro"
    assert cfg.threads_steer_selection is False
    assert cfg.problems == []


def test_the_founders_own_follows_are_untouched(real_route):
    """The other direction of the same editorial call: nothing in this change
    reaches into the founder's file. His follows are his."""
    tmp_path = real_route
    (tmp_path / "sources.yaml").write_text(
        "sources:\n"
        "  - name: Chartbook (Adam Tooze)\n"
        "    rss_url: https://adamtooze.substack.com/feed\n"
        "    followed_analyst: true\n"
        "interests:\n"
        "  broad: [founder-domain]\n", encoding="utf-8")
    profiles.create("alice")
    paths.set_profile(None)
    cfg = config.load_sources()
    assert [s.name for s in cfg.followed_analyst_sources] == \
        ["Chartbook (Adam Tooze)"]


# ===========================================================================
# ITEM 7 — profiles/default is visible, not swallowed
# ===========================================================================

def test_a_profiles_default_directory_is_reported_not_swallowed(real_route):
    """BORN-RED. `profiles/default` is a VALID slug, so stray_directories()
    skipped it; and profile_names() excludes the default name by construction,
    so it never appeared there either. A directory holding a whole reader's
    state was invisible in both listings — the founder's profile is the
    checkout root, so anything at profiles/default is state nobody will find."""
    tmp_path = real_route
    (tmp_path / "profiles" / "default").mkdir(parents=True)
    strays = profiles.stray_directories()
    assert "default" in strays, (
        f"profiles/default is invisible in both listings: names="
        f"{profiles.profile_names()} strays={strays}")


def test_profile_list_prints_the_stray_default_directory(real_route, capsys):
    """The listing a human actually reads must name it."""
    from newslens import cli
    tmp_path = real_route
    (tmp_path / "profiles" / "default").mkdir(parents=True)
    assert cli.main(["profile", "list"]) == 0
    assert "default" in capsys.readouterr().out


def test_a_real_profile_named_by_a_stray_check_is_still_a_profile(real_route):
    """The one-liner must not start calling legitimate profiles strays."""
    tmp_path = real_route
    profiles.create("alice")
    assert "alice" in profiles.profile_names()
    assert "alice" not in profiles.stray_directories()
