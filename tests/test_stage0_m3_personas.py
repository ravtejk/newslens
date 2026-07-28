"""Stage-0 M3 — the three synthetic personas, the review affordance, $0 readiness.

Contract (dispatch 2026-07-27; multi-user brief §2 Stage-0 "3 synthetic
personas (one deliberately off-distribution; near-neighbor probes = the real
shallow-personalization kill-test; the WALL: quality-auditable, never
engagement evidence)"; DECISIONS [2026-07-25] STAGE-0 M1/M2 SHIPPED, THE
$0-RUN LAW, SONAR RULED):

  1. Three org-authored fixtures: a near-neighbour PAIR whose geometry is
     high-overlap at the domain rung and near-disjoint at the topic rung (the
     kill-test), plus one off-distribution reader disjoint from both AND from
     the founder's authored vocabulary.
  2. Per-profile caps + ledgers armed on each persona world (M2 machinery —
     verified here, not rebuilt).
  3. The review affordance: a persona is a DIFFERENT INSTANCE on a DIFFERENT
     PORT. No in-shell identity, no switcher, and never the founder's 8484.
  4. $0 first-briefing readiness: subscription lane, discovery paused, and the
     Sonar key absent to the pipeline — where "absent" means EMPTY, not
     deleted (§ITEM 4 below is the whole reason this is code and not a README
     sentence).
  5. Cold-start honesty: a persona world is born unseeded and the M2 script
     continuity net still bites on it.
  6. Sacred surfaces: provisioning a persona resolves no path of the
     founder's, and never writes one.

BORN-RED DISCLOSURE (ENGINEERING.md, gate ruling 2026-07-18): at HEAD
(60d0fd9) `newslens.personas` does not exist, so EVERY test in this file is
SYMBOL-ABSENCE red, not defect red. The load-bearing enforcement pins are
mutation-proved instead — see the build report for the mutation runs. The one
pin that characterises SHIPPED behaviour independent of this milestone is
`test_deleting_the_sonar_key_re_injects_it_but_emptying_it_does_not`, and it is
labelled a CARRIED-INVARIANT (born-green): it describes python-dotenv +
config.load_env as they already are, and exists so that a later "cleanup" to
`env -u` cannot land quietly.

Sandbox: the tree conftest's autouse fixtures apply; disk-touching tests use
M2's `real_route` harness. $0 by construction — no LLM seam and no metered API
is reached, and every child process is pointed at a fixture `.env`.
"""
from __future__ import annotations

import json
import socket

import pytest

from newslens import config, db, generate, paths, personas, profiles, server

GUARDED_NAMES = ("DATA_DIR", "DB_PATH", "SOURCES_FILE", "ENV_FILE", "MEMORY_FILE")

FOUNDER_SOURCES = (
    "sources:\n"
    "  - name: Founder Outlet\n"
    "    rss_url: https://founder.invalid/feed\n"
    "interests:\n"
    "  broad: [founder-domain]\n"
    "  granular: [founder-topic]\n"
)

# A deliberately fake credential, never a real one. Its only job is to be
# re-injected (or not) by load_dotenv so the ITEM-4 characterisation is
# hermetic instead of depending on whatever is in the principal's .env.
FAKE_KEY = "pplx-FAKE-not-a-credential-0123456789"


@pytest.fixture
def real_route(monkeypatch, tmp_path):
    """M2's routing harness verbatim: no module-dict shadows, no redirection
    vars, PROJECT_ROOT and the _GUARDED table both re-anchored at tmp_path, so
    paths.__getattr__ answers the way it does on the principal's machine.

    PERSONAS_DIR is deliberately NOT re-anchored — a fixture is shipped code,
    the same class as migrations/ and templates/, which this harness also
    leaves real. So these tests provision the REAL personas into a sandboxed
    world.
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
    yield tmp_path
    # Clearing the pin must never be the thing that fails a test: set_profile(
    # None) re-resolves through current_profile(), which RAISES on a malformed
    # NEWSLENS_PROFILE — and the QA-2 tests deliberately export one. A cleanup
    # that cannot run under the conditions its own tests create is not cleanup.
    try:
        paths.set_profile(None)
    except paths.ProfileError:
        paths._PROFILE_OVERRIDE = None


@pytest.fixture
def fixture_env(real_route):
    """A `.env` of ours where the persona layout actually looks for one.

    Deliberately NOT a NEWSLENS_ENV_FILE redirection. An earlier cut of this
    fixture used one, and the redirection short-circuits paths.__getattr__
    before its sanction check — so the suite went green while every real
    invocation died on the guard. The .env is written at `real_route/.env`,
    which IS `profile_layout(<persona>)["ENV_FILE"]` under this harness, so the
    tests exercise the same path resolution a real run does.
    """
    import os
    (real_route / ".env").write_text(
        f"PERPLEXITY_API_KEY={FAKE_KEY}\nBUDGET_CAP_USD_PER_RUN=1.50\n",
        encoding="utf-8")
    env = dict(os.environ)
    env.pop("NEWSLENS_DISCOVERY_ENABLED", None)
    env.pop("NEWSLENS_ENV_FILE", None)
    return env


# ===========================================================================
# ITEM 1 — the fixtures, and the kill-test's geometry
# ===========================================================================

def test_the_three_personas_are_the_shipped_roster():
    """Two near neighbours and exactly one off-distribution reader. The scope
    is 3, and a fourth arriving unremarked would change what the pair means."""
    people = personas.load_all()
    assert [p.slug for p in people] == ["energy-desk", "public-health",
                                        "rates-desk"]
    roles = sorted(p.role for p in people)
    assert roles == ["near-neighbour-a", "near-neighbour-b", "off-distribution"]


def test_the_near_neighbour_pair_has_the_pre_registered_geometry():
    """THE KILL-TEST. High overlap at the domain rung, near-disjoint at the
    topic rung: then the only thing that can separate the two editions is the
    topic signal. README §Probe quotes these exact numbers; a tag edit that
    quietly flattened the pair — making the readers near-identical, or making
    them strangers — has to come past this pin and update the README with it.
    """
    a = personas.load("rates-desk")
    b = personas.load("energy-desk")
    ov = personas.overlap(a, b)
    assert ov["domain"] == (2, 4, pytest.approx(0.50, abs=0.001))
    assert ov["topic"] == (1, 15, pytest.approx(0.0667, abs=0.001))
    assert ov["domain"][2] > 5 * ov["topic"][2], (
        "the pair is no longer a near-neighbour pair: domain overlap must "
        "dominate topic overlap, or the two editions differing proves nothing")


def test_the_off_distribution_persona_is_disjoint_from_both_neighbours():
    c = personas.load("public-health")
    for other in ("rates-desk", "energy-desk"):
        ov = personas.overlap(c, personas.load(other))
        assert ov["domain"][0] == 0 and ov["topic"][0] == 0, (
            f"public-health shares tags with {other} — it is supposed to be "
            "the reader this source pack was NOT built for")


def test_the_off_distribution_persona_shares_no_tag_with_the_founder():
    """The probe is 'far from the founder's world', so the distance is
    measured against HIS authored vocabulary, not asserted in prose.

    DELIBERATE READ OF REAL STATE, read-only and disclosed: this and the pin
    below are the only two tests in the tree that open the principal's own
    sources.yaml. Nothing is written and no guarded attribute is resolved (the
    path is passed explicitly). The dependency is the point — "off-distribution
    relative to the founder" is a claim about his ACTUAL interests.

    QA-3: the message must name WHICH SIDE MOVED. `sources.yaml` is a file he
    edits routinely, so a red here is at least as likely to be his edit as a
    bad fixture, and a pin that blames the fixture points the next reader at
    the wrong file. The dated baseline below is the discriminator.
    """
    cfg = config.load_sources(paths.PROJECT_ROOT / "sources.yaml")
    founder = {t.lower() for t in cfg.interests_broad + cfg.interests_granular}
    assert founder, "the founder's sources.yaml has no interests to compare to"
    mine = {t.lower() for t in personas.load("public-health").all_tags}
    collided = sorted(mine & founder)
    assert not collided, (
        f"public-health is no longer off-distribution: {collided}. None of its "
        "tags was in his authored list on 2026-07-27, so HIS INTERESTS MOVED — "
        "he now follows this vertical. The fixture did not change. Re-pick the "
        "off-distribution vertical, or accept that the probe has expired.")


# Dated baseline, read from the principal's own sources.yaml on 2026-07-27 (the
# M3 build read; 59 authored tags, of which these 19 are every distinct tag the
# near-neighbour pair uses). It exists to answer ONE question when the pin below
# goes red: was the tag ever his, or did the fixture invent it? Without it the
# assertion cannot tell his edit from our error — QA-3.
FOUNDER_VOCAB_BASELINE_2026_07_27 = {
    'agricultural commodities', 'central bank policy', 'credit default risk',
    'direct lending', 'economic policy', 'energy supply shock',
    'federal reserve', 'fertilizer supply', 'global trade', 'household debt',
    'inflation', 'maritime chokepoints', 'oil markets', 'opec+',
    'private credit', 'recession risk', 'shadow banking', 'strait of hormuz',
    'systemic risk',
}


def test_both_near_neighbours_draw_only_on_the_founders_authored_vocabulary():
    """The near-neighbour pair uses catalog vocabulary, not invented strings:
    every tag exists in the org's own authored list. (public-health is the
    deliberate exception — its vertical has no catalog entries yet, which its
    fixture declares as catalog EXTENSIONS.)

    QA-3: splits the two causes of a red instead of blaming the fixture for
    both. A tag that is missing from his file today but WAS in the dated
    baseline means he deleted one of his own tags — the fixture invented
    nothing, and the reader is sent to the right file.
    """
    cfg = config.load_sources(paths.PROJECT_ROOT / "sources.yaml")
    founder = {t.lower() for t in cfg.interests_broad + cfg.interests_granular}
    for slug in ("rates-desk", "energy-desk"):
        missing = sorted(t for t in personas.load(slug).all_tags
                         if t.lower() not in founder)
        invented = [t for t in missing
                    if t.lower() not in FOUNDER_VOCAB_BASELINE_2026_07_27]
        he_removed = [t for t in missing
                      if t.lower() in FOUNDER_VOCAB_BASELINE_2026_07_27]
        assert not invented, (
            f"{slug} carries tag(s) that were NEVER in the founder's authored "
            f"list: {invented} — the FIXTURE invented catalog entries. Fix "
            f"personas/{slug}.yaml.")
        assert not he_removed, (
            f"{slug} is fine — the FIXTURE DID NOT CHANGE. These tags were in "
            f"the founder's sources.yaml on 2026-07-27 and are not there now: "
            f"{he_removed}. He edited his own interests. Either re-point the "
            f"fixture at vocabulary he still uses, or refresh "
            f"FOUNDER_VOCAB_BASELINE_2026_07_27 and this pin together.")


def test_every_fixture_documents_its_design_intent():
    """A fixture whose purpose is undocumented cannot be audited against its
    purpose — which is the only thing these worlds are for."""
    for p in personas.load_all():
        assert len(p.design_intent) > 200, (
            f"{p.slug}: design_intent is too thin to audit against")


# ===========================================================================
# ITEM 1b — the NL-17 altitude vocabulary
# ===========================================================================

def test_fixtures_are_authored_in_catalog_altitudes_never_storage_keys():
    """DECISIONS 2026-07-25: the picker builds on the NL-17 catalog
    (Domain→Topic→Entity), 'never on the dying broad/granular vocabulary'. The
    fixture files say domain/topic; broad/granular exist only as the storage
    keys the mapping produces."""
    for path in personas.fixture_paths():
        body = "\n".join(ln for ln in path.read_text(encoding="utf-8").splitlines()
                         if not ln.lstrip().startswith("#"))
        assert "broad" not in body and "granular" not in body, (
            f"{path.name} writes a storage key where a catalog altitude belongs")
    assert set(personas.ALTITUDE_TO_YAML_KEY) == {"domain", "topic"}
    assert personas.ALTITUDE_TO_YAML_KEY == {"domain": "broad",
                                             "topic": "granular"}


def test_a_fixture_using_the_dead_vocabulary_is_refused(tmp_path, monkeypatch):
    """Enforcement, not convention: `broad:` in a fixture is a hard refusal
    naming the catalog rungs, so the dead vocabulary cannot creep back in
    through a copy-paste."""
    fixtures = tmp_path / "fixtures"   # NOT tmp_path: the conftest
    fixtures.mkdir()                   # sandbox writes a sources.yaml there
    monkeypatch.setattr(personas, "PERSONAS_DIR", fixtures)
    (fixtures / "ghost.yaml").write_text(
        "slug: ghost\ndisplay: Ghost\nport: 8499\nrole: off-distribution\n"
        "design_intent: |\n  " + ("x" * 250) + "\n"
        "interests:\n  broad:\n    - Something\n", encoding="utf-8")
    with pytest.raises(personas.PersonaError) as exc:
        personas.load("ghost")
    assert "altitude" in str(exc.value)
    assert "'domain', 'topic'" in str(exc.value) or "domain" in str(exc.value)


def test_the_entity_rung_is_absent_rather_than_folded_into_topic():
    """NL-17 acceptance criterion (a): one concept = one vocabulary, tag XOR
    entity. The interests file has two rungs; an `entity:` block would file a
    third rung's concepts under the second rung's label AND weight."""
    assert "entity" not in personas.ALTITUDE_TO_YAML_KEY
    with pytest.raises(KeyError):
        personas.ALTITUDE_TO_YAML_KEY["entity"]


# ===========================================================================
# ITEM 3 — the review affordance: different instance, different port
# ===========================================================================

def test_no_persona_can_be_reached_at_the_founders_address():
    assert personas.FOUNDER_PORT == server.DEFAULT_PORT == 8484
    for p in personas.load_all():
        assert p.port != personas.FOUNDER_PORT


def test_persona_ports_are_distinct():
    ports = [p.port for p in personas.load_all()]
    assert len(set(ports)) == len(ports)
    assert sorted(ports) == [8485, 8486, 8487]


def test_a_fixture_claiming_the_founders_port_is_refused(tmp_path, monkeypatch):
    fixtures = tmp_path / "fixtures"   # NOT tmp_path: the conftest
    fixtures.mkdir()                   # sandbox writes a sources.yaml there
    monkeypatch.setattr(personas, "PERSONAS_DIR", fixtures)
    (fixtures / "greedy.yaml").write_text(
        "slug: greedy\ndisplay: Greedy\nport: 8484\nrole: off-distribution\n"
        "design_intent: |\n  " + ("x" * 250) + "\n"
        "interests:\n  topic:\n    - Something\n", encoding="utf-8")
    with pytest.raises(personas.PersonaError, match="founder"):
        personas.load("greedy")


def test_two_fixtures_sharing_a_port_are_refused(tmp_path, monkeypatch):
    """A collision would surface as a bind failure long after the operator
    stopped watching — and the second instance would silently be the first."""
    fixtures = tmp_path / "fixtures"   # NOT tmp_path: the conftest
    fixtures.mkdir()                   # sandbox writes a sources.yaml there
    monkeypatch.setattr(personas, "PERSONAS_DIR", fixtures)
    for name in ("one", "two"):
        (fixtures / f"{name}.yaml").write_text(
            f"slug: {name}\ndisplay: {name}\nport: 8490\n"
            "role: off-distribution\n"
            "design_intent: |\n  " + ("x" * 250) + "\n"
            "interests:\n  topic:\n    - Something\n", encoding="utf-8")
    with pytest.raises(personas.PersonaError, match="port"):
        personas.load_all()


def test_the_founder_is_never_a_persona():
    """No in-shell identity: there is no 'switch to default' — his world is
    reached the way it always was."""
    with pytest.raises(personas.PersonaError, match="founder"):
        personas.require(paths.DEFAULT_PROFILE)


def test_serve_refuses_a_persona_with_no_world_yet(real_route, capsys):
    rc = personas.serve_main(["rates-desk"])
    assert rc == 2
    assert "persona-provision" in capsys.readouterr().err


def test_serve_hands_the_profile_and_port_to_the_shipped_cli(real_route,
                                                             monkeypatch):
    """WIRING PIN: serve does not re-implement an entrypoint — it calls
    cli.main with --profile and --port, so the incident guard, the profile
    boundary and the redirection disclosure are all the shipped code."""
    personas.provision(personas.load("rates-desk"))
    seen = {}

    def fake_main(argv):
        seen["argv"] = argv
        return 0

    from newslens import cli
    monkeypatch.setattr(cli, "main", fake_main)
    assert personas.serve_main(["rates-desk"]) == 0
    assert seen["argv"] == ["--profile", "rates-desk", "serve", "--port", "8485"]


def test_serve_refuses_a_port_that_is_already_serving(real_route, capsys):
    """A second instance on a live port would traceback out of
    ThreadingHTTPServer; the operator gets a sentence instead."""
    personas.provision(personas.load("rates-desk"))
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("127.0.0.1", 8485))
    sock.listen(1)
    try:
        rc = personas.serve_main(["rates-desk"])
    finally:
        sock.close()
    assert rc == 2
    assert "already serving" in capsys.readouterr().err


# ===========================================================================
# ITEM 1c / 6 — provisioning through the shipped lanes, founder untouched
# ===========================================================================

def test_provisioning_mints_a_world_through_profile_create(real_route):
    persona = personas.load("rates-desk")
    res = personas.provision(persona)
    assert res.ok and not res.failures
    root = real_route / "profiles" / "rates-desk"
    assert (root / "data" / "newslens.db").exists()
    assert (root / "memory.md").read_bytes() == b""      # the unseeded start
    assert (root / "sources.yaml").exists()


def test_provisioning_migrates_through_the_current_head_migration(real_route):
    """Not a stale cut: the head migration file on disk (0023 at this commit)
    must be recorded as applied, and nothing may be pending."""
    personas.provision(personas.load("rates-desk"))
    db_path = real_route / "profiles" / "rates-desk" / "data" / "newslens.db"
    head = db.migration_files()[-1].name
    con = db.connect_readonly(db_path)
    try:
        applied = set(db.applied_migrations(con))
    finally:
        con.close()
    assert head in applied, f"head migration {head} was not applied"
    assert db.pending_migrations(db_path) == []


def test_every_fixture_tag_lands_in_the_profiles_own_sources_file(real_route):
    """End-to-end through the SHIPPED topic editor: the tags the fixture
    declares are the tags the ranker will read, at the rung the fixture put
    them at."""
    persona = personas.load("rates-desk")
    personas.provision(persona)
    cfg = config.load_sources(
        real_route / "profiles" / "rates-desk" / "sources.yaml")
    assert cfg.interests_broad == persona.domain_tags
    assert cfg.interests_granular == persona.topic_tags
    assert cfg.has_interests and not cfg.problems


def test_all_three_personas_provision_and_stay_separate(real_route):
    for persona in personas.load_all():
        assert personas.provision(persona).ok
    seen = {}
    for persona in personas.load_all():
        cfg = config.load_sources(
            real_route / "profiles" / persona.slug / "sources.yaml")
        seen[persona.slug] = (tuple(cfg.interests_broad),
                              tuple(cfg.interests_granular))
    assert len(set(seen.values())) == 3, (
        "two persona worlds ended up with the same interests: " + str(seen))


def test_provisioning_never_creates_or_touches_the_founders_world(real_route):
    """SACRED. His sources.yaml is byte-identical and his data/ is never
    created by a persona being minted."""
    founder_sources = real_route / "sources.yaml"
    before = founder_sources.read_bytes()
    for persona in personas.load_all():
        personas.provision(persona)
    assert founder_sources.read_bytes() == before
    assert not (real_route / "data").exists(), (
        "provisioning a persona created the founder's data directory")
    assert not (real_route / "memory.md").exists()


def test_provisioning_leaves_no_profile_pin_behind(real_route):
    """The in-process leak class M1's CLI probe found and the doctor repeated:
    a leaked pin sends the NEXT caller's edit into this persona's world."""
    assert paths.current_profile() == paths.DEFAULT_PROFILE
    personas.provision(personas.load("rates-desk"))
    assert paths.current_profile() == paths.DEFAULT_PROFILE
    assert paths.SOURCES_FILE == real_route / "sources.yaml"


def test_re_provisioning_an_existing_world_is_refused(real_route):
    """A fresh empty DB beside a populated memory.md is the file-and-DB
    disagreement NL-81's guard exists to refuse."""
    persona = personas.load("rates-desk")
    personas.provision(persona)
    with pytest.raises(profiles.ProfileExistsError):
        personas.provision(persona)


def test_provision_main_leaves_an_existing_world_untouched(real_route, capsys):
    persona = personas.load("rates-desk")
    personas.provision(persona)
    stamp = (real_route / "profiles" / "rates-desk" / "sources.yaml").read_bytes()
    assert personas.provision_main(["rates-desk"]) == 0
    assert "already exists" in capsys.readouterr().out
    assert (real_route / "profiles" / "rates-desk"
            / "sources.yaml").read_bytes() == stamp


def test_a_refused_tag_makes_provisioning_fail_loudly(real_route, monkeypatch,
                                                      capsys):
    """ENFORCEMENT: a half-authored reader is worse than none, because its
    editions would look like evidence about a reader who was never described.
    The world stays on disk to be inspected; the command exits nonzero."""
    real_add = server.topic_add
    calls = {"n": 0}

    def flaky(name, level):
        calls["n"] += 1
        if calls["n"] == 3:
            return False, "Didn’t add it — synthetic refusal."
        return real_add(name, level)

    monkeypatch.setattr(server, "topic_add", flaky)
    assert personas.provision_main(["rates-desk"]) == 1
    err = capsys.readouterr().err
    assert "TAG REFUSED" in err and "INCOMPLETE" in err


# ===========================================================================
# ITEM 2 — per-profile caps and ledgers armed on each persona world
# ===========================================================================

def test_each_persona_ledger_lands_in_its_own_data_dir(real_route):
    """M2's machinery, verified on the persona worlds rather than rebuilt."""
    for persona in personas.load_all():
        personas.provision(persona)
    for i, persona in enumerate(personas.load_all()):
        paths.set_profile(persona.slug)
        generate.log_generation({"date": "2026-07-27", "status": "ok",
                                 "total_usd": 0.10 * (i + 1)})
    paths.set_profile(None)
    for i, persona in enumerate(personas.load_all()):
        log = (real_route / "profiles" / persona.slug / "data"
               / "generation_log.jsonl")
        rows = [json.loads(ln) for ln in
                log.read_text(encoding="utf-8").splitlines()]
        assert [r["total_usd"] for r in rows] == [pytest.approx(0.10 * (i + 1))]
    assert not (real_route / "data" / "generation_log.jsonl").exists(), (
        "a persona's spend landed in the founder's ledger")


def test_one_personas_spend_is_invisible_to_another(real_route):
    for persona in personas.load_all():
        personas.provision(persona)
    paths.set_profile("rates-desk")
    generate.log_generation({"date": "2026-07-27", "status": "ok",
                             "total_usd": 0.99})
    assert server._log_entry_for("2026-07-27")["total_usd"] == pytest.approx(0.99)
    paths.set_profile("energy-desk")
    assert server._log_entry_for("2026-07-27") is None
    paths.set_profile(None)
    assert server._log_entry_for("2026-07-27") is None


def test_readiness_reports_the_cap_and_ledger_as_armed(real_route, fixture_env):
    personas.provision(personas.load("rates-desk"))
    checks = {c.name: c for c in
              personas.readiness(personas.load("rates-desk"), fixture_env)}
    assert checks["per-run budget cap armed"].ok
    assert "1.50" in checks["per-run budget cap armed"].detail, (
        "the cap was read from the shell, not from the .env the run loads")
    ledger = checks["spend ledger is this profile's own"]
    assert ledger.ok and "profiles/rates-desk" in ledger.detail


# ===========================================================================
# ITEM 4 — THE $0 CONTRACT
# ===========================================================================

def test_deleting_the_sonar_key_re_injects_it_but_emptying_it_does_not(
        real_route, fixture_env):
    """CARRIED-INVARIANT (born-green) — this characterises SHIPPED behaviour.

    config.load_env() is load_dotenv(override=False), and python-dotenv's
    override check is `if k in os.environ` — PRESENCE, not truthiness. So the
    obvious spelling of "remove the key" is the one that spends money:

      * DELETED  -> load_env re-injects the key from .env -> a metered call.
      * EMPTY    -> load_env leaves it alone -> absent to every consumer.

    Measured through a real process boundary, against a fixture .env holding a
    fake credential — never the principal's.
    """
    env_file = real_route / ".env"
    deleted = dict(fixture_env)
    deleted.pop(personas.SONAR_KEY_ENV, None)
    assert personas.resolved_key_length(deleted, env_file) == len(FAKE_KEY), (
        "the .env key was NOT re-injected — if this ever goes green the "
        "hazard is gone and zero_dollar_env's docstring is stale")

    emptied = personas.zero_dollar_env(fixture_env)
    assert emptied[personas.SONAR_KEY_ENV] == ""
    assert personas.resolved_key_length(emptied, env_file) == 0


def test_zero_dollar_env_sets_the_key_empty_rather_than_removing_it():
    """ENFORCEMENT PIN, and the only shape that works: a future edit to
    `del env[KEY]` reads as equivalent and silently re-arms the paid path."""
    env = personas.zero_dollar_env({"PERPLEXITY_API_KEY": "live-key",
                                    "OTHER": "kept"})
    assert personas.SONAR_KEY_ENV in env, (
        "the key was DELETED — load_env would re-inject it from .env")
    assert env[personas.SONAR_KEY_ENV] == ""
    assert env["OTHER"] == "kept"


def test_the_scrubbed_key_reaches_analysis_as_an_honest_skip(monkeypatch):
    """The disclosure half of the $0 promise: with no key, the Sonar step does
    not silently pass — it returns a status that says it was skipped and why,
    and that status rides the per-story record. Never a fabricated
    verification."""
    from newslens import analysis
    results, cost, status = analysis._sonar_verify("", "A story", ["a claim"])
    assert results == [] and cost == 0.0
    assert "skipped" in status and personas.SONAR_KEY_ENV in status


def test_generate_refuses_when_the_scrub_did_not_hold(real_route, monkeypatch,
                                                      capsys):
    """ENFORCEMENT, born with this milestone: readiness is MEASURED, not
    assumed. If the pipeline still resolves a key, nothing runs — this is the
    pin that only the refusal can flip."""
    personas.provision(personas.load("rates-desk"))
    # Seam is pipeline_probe since the gate's QA-8 fix: one child, two facts
    # (key length + discovery), so the doors stub the probe, not the extractor.
    monkeypatch.setattr(
        personas, "pipeline_probe",
        lambda env, env_file, python=None: {
            "key_len": 42, "discovery_enabled": False,
            "cap": 1.50, "cap_error": None})
    called = {"n": 0}
    from newslens import cli
    monkeypatch.setattr(cli, "main",
                        lambda argv: called.__setitem__("n", called["n"] + 1))
    rc = personas.generate_main(["rates-desk"])
    assert rc == 1
    assert called["n"] == 0, "a metered run started after a failed scrub"
    err = capsys.readouterr().err
    assert "REFUSING" in err and "Nothing was run" in err


def test_generate_scrubs_then_delegates_to_the_shipped_cli(real_route,
                                                           monkeypatch):
    """WIRING PIN: the scrub is applied to THIS process before the verb runs,
    and the verb itself is the shipped one."""
    import os
    personas.provision(personas.load("rates-desk"))
    monkeypatch.setenv(personas.SONAR_KEY_ENV, "live-looking-key")
    monkeypatch.setattr(
        personas, "pipeline_probe",
        lambda env, env_file, python=None: {
            "key_len": 0, "discovery_enabled": False,
            "cap": 1.50, "cap_error": None})
    seen = {}
    from newslens import cli
    monkeypatch.setattr(cli, "main", lambda argv: seen.setdefault("argv", argv))
    personas.generate_main(["rates-desk", "--no-refresh"])
    assert seen["argv"] == ["--profile", "rates-desk", "generate", "--no-refresh"]
    assert os.environ[personas.SONAR_KEY_ENV] == ""


def test_generate_never_edits_the_env_file(real_route, monkeypatch):
    """THE LAW: the scrub is env-scoped at invocation. His .env is never
    written, and the fixture .env here proves the file is untouched."""
    env_file = real_route / ".env"
    env_file.write_text("PERPLEXITY_API_KEY=live\n", encoding="utf-8")
    before = env_file.read_bytes()
    personas.provision(personas.load("rates-desk"))
    monkeypatch.setattr(
        personas, "pipeline_probe",
        lambda env, env_file, python=None: {
            "key_len": 0, "discovery_enabled": False,
            "cap": 1.50, "cap_error": None})
    from newslens import cli
    monkeypatch.setattr(cli, "main", lambda argv: 0)
    personas.generate_main(["rates-desk"])
    assert env_file.read_bytes() == before


def test_readiness_says_discovery_is_paused_and_the_key_is_absent(real_route,
                                                                  fixture_env):
    personas.provision(personas.load("rates-desk"))
    checks = {c.name: c for c in
              personas.readiness(personas.load("rates-desk"), fixture_env)}
    assert checks["tier-2 discovery paused (code level)"].ok
    key_check = next(c for n, c in checks.items() if n.startswith("Sonar key"))
    assert key_check.ok and "skip path" in key_check.detail


def test_readiness_is_green_on_every_provisioned_persona(real_route,
                                                         fixture_env):
    for persona in personas.load_all():
        personas.provision(persona)
    for persona in personas.load_all():
        failed = [c.name for c in personas.readiness(persona, fixture_env)
                  if not c.ok]
        assert not failed, f"{persona.slug}: {failed}"


def test_readiness_refuses_a_world_that_was_never_provisioned(real_route,
                                                              fixture_env):
    checks = personas.readiness(personas.load("rates-desk"), fixture_env)
    assert not checks[0].ok and checks[0].name == "world exists"


def test_readiness_resolves_no_guarded_path_of_the_founders(real_route,
                                                            fixture_env):
    """The readiness tool deliberately does NOT self-sanction. Proven by
    running it with real paths REFUSED: it must complete anyway."""
    personas.provision(personas.load("rates-desk"))
    import os
    monkey_env = dict(fixture_env)
    os.environ.pop("NEWSLENS_REAL_DATA", None)
    paths._REAL_PATHS_ALLOWED = False
    try:
        checks = personas.readiness(personas.load("rates-desk"), monkey_env)
    finally:
        os.environ["NEWSLENS_REAL_DATA"] = "1"
    assert [c for c in checks if c.name == "world exists"][0].ok


# ===========================================================================
# ITEM 5 — cold-start honesty on a persona world
# ===========================================================================

def test_a_persona_world_is_born_with_nothing_to_be_continuous_with(real_route):
    """Day one, for real: no threads, no prior edition, a 0-byte memory.md."""
    personas.provision(personas.load("public-health"))
    st = profiles.status("public-health")
    assert st.memory_bytes == 0
    assert st.threads_active == 0
    assert st.sync_profile_slug == "public-health"


def test_the_script_continuity_net_still_bites_on_a_persona_world(real_route):
    """CARRIED-INVARIANT (born-green): M2's RED-2 net, exercised against a
    persona's own empty world. A day-one episode claiming the show has been
    tracking something is a fabricated relationship with a reader it has never
    met — and it must be named in the findings, not merely suppressed."""
    from test_generate import compliant_script, seed_briefing, slot

    date = "2026-07-27"
    personas.provision(personas.load("public-health"))
    paths.set_profile("public-health")
    con = db.connect()
    try:
        slots = [slot(1, tags=(), mem=())]
        seed_briefing(con, date, slots)
        inputs = generate.load_briefing_inputs(con, date)
        # Day one on this reader's own world: nothing to be continuous WITH.
        assert not generate._has_real_prior_coverage(inputs)
        clean = compliant_script(inputs["slots"])
        assert generate.script_continuity_findings(con, clean, inputs, date) == [], (
            "the net fired on an ordinary day-one episode")
        poisoned = ("We have been tracking hospital capacity all week. "
                    "Regular listeners will remember the staffing story. "
                    + clean)
        findings = generate.script_continuity_findings(con, poisoned, inputs,
                                                       date)
    finally:
        con.close()
        paths.set_profile(None)
    assert findings, (
        "a day-one persona episode claimed continuity and nothing objected")


def test_a_persona_world_has_no_briefing_to_be_continuous_with(real_route):
    personas.provision(personas.load("public-health"))
    paths.set_profile("public-health")
    con = db.connect()
    try:
        assert con.execute("SELECT COUNT(*) c FROM briefings"
                           ).fetchone()["c"] == 0
        assert con.execute("SELECT COUNT(*) c FROM memory").fetchone()["c"] == 0
    finally:
        con.close()
        paths.set_profile(None)


# ===========================================================================
# ITEM 6 — structural: the persona tools cannot reach the founder
# ===========================================================================

def test_no_persona_path_resolves_to_a_founder_path(real_route):
    founder = paths.profile_layout(paths.DEFAULT_PROFILE)
    for persona in personas.load_all():
        layout = paths.profile_layout(persona.slug)
        for key in ("DATA_DIR", "DB_PATH", "MEMORY_FILE", "SOURCES_FILE"):
            assert layout[key] != founder[key], f"{persona.slug} shares {key}"
        # .env IS shared, deliberately: machine credentials, not reader state.
        assert layout["ENV_FILE"] == founder["ENV_FILE"]


def test_the_launchers_are_thin_and_point_at_this_module():
    """WIRING PIN: every scripts/persona-* ON DISK is an executable thin
    launcher over exactly one `*_main` this module exports. The launcher set
    is discovered by glob, never a hand-kept list — QA-9: a hand-kept list is
    how a fifth launcher that bypasses the `*_main` census (and with it every
    scrub pin) ships unseen."""
    import os
    on_disk = sorted((paths.PROJECT_ROOT / "scripts").glob("persona-*"))
    assert [p.name for p in on_disk] == [
        "persona-generate", "persona-provision", "persona-ready",
        "persona-serve"], (
        f"scripts/persona-* on disk is {[p.name for p in on_disk]} — a new "
        "launcher must be a thin wrapper over a *_main (covered by the "
        "exhaustiveness pin) and must join this list in the same change")
    for script in on_disk:
        name = script.name[len("persona-"):]
        assert os.access(script, os.X_OK), f"{script.name} not executable"
        body = script.read_text(encoding="utf-8")
        assert f"{name}_main" in body, (
            f"{script.name} does not hand off to {name}_main — a launcher "
            "that bypasses the *_main census is outside every scrub pin")
        assert len(body.splitlines()) < 25, "a launcher grew logic"
        assert hasattr(personas, f"{name}_main")


def test_no_shipped_code_enters_the_persona_lane_sideways():
    """QA-9: the fix return's closure claim 'no other module imports personas'
    was a one-time grep; this is that grep as a pin, plus the PEP-562 door a
    dir() census cannot see. Nothing in src/ may import the persona lane (so
    no shipped code path reaches a door sideways), no script outside
    persona-* may, and the module exposes no __getattr__ — paths.py uses
    exactly that idiom in this codebase, so a lazily-exposed door is not
    exotic, and one would evade the *_main exhaustiveness pin."""
    needles = ("import personas", "personas import", "newslens.personas")
    src = paths.PROJECT_ROOT / "src" / "newslens"
    offenders = [py.name for py in sorted(src.rglob("*.py"))
                 if py.name != "personas.py"
                 and any(n in py.read_text(encoding="utf-8") for n in needles)]
    assert not offenders, (
        f"shipped module(s) {offenders} reference the persona lane — a door "
        "reached from src/ is outside every launcher and scrub pin")
    for script in sorted((paths.PROJECT_ROOT / "scripts").iterdir()):
        if not script.is_file() or script.name.startswith("persona-"):
            continue
        try:
            body = script.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        assert not any(n in body for n in needles), (
            f"scripts/{script.name} enters the persona lane but is not a "
            "persona-* launcher — it evades the launcher census above")
    assert "__getattr__" not in vars(personas), (
        "personas grew a module __getattr__ — dir()-census pins cannot see "
        "what it exposes; extend the door census before adding one")


# ===========================================================================
# FIX RETURN (QA pass 2026-07-27) — the second door, and the doors after it
#
# QA-1 (HIGH) found the $0 contract enforced on ONE of two doors: persona-
# generate scrubbed, persona-serve did not — and a persona's day-one page
# carries a one-click "Generate today's edition" button that runs the pipeline
# in the SERVE process with the principal's live key injected by load_env.
# ===========================================================================

def test_serve_resolves_a_zero_length_key_at_the_moment_it_delegates(
        real_route, fixture_env, monkeypatch):
    """QA-1's red test, to its contract: at the instant `serve_main` hands off
    to `cli.main`, the pipeline must resolve a ZERO-LENGTH Sonar key.

    Measured the way the serve process itself would resolve it — through
    `config.load_env()` in a real child — not by reading back the variable this
    process just set. The serve door is long-lived: every generation the web UI
    can start inherits whatever this process's environment says at this moment.
    """
    import os
    personas.provision(personas.load("rates-desk"))
    env_file = real_route / ".env"
    # A login shell: the variable is ABSENT, which is exactly the state in
    # which load_dotenv injects the live key.
    monkeypatch.delenv(personas.SONAR_KEY_ENV, raising=False)
    assert personas.resolved_key_length(dict(os.environ), env_file) == len(FAKE_KEY), (
        "fixture precondition: an unscrubbed process must resolve the .env key")

    seen = {}

    def fake_cli_main(argv):
        seen["argv"] = argv
        seen["resolved"] = personas.resolved_key_length(dict(os.environ), env_file)
        return 0

    from newslens import cli
    monkeypatch.setattr(cli, "main", fake_cli_main)
    assert personas.serve_main(["rates-desk"]) == 0
    assert seen["argv"][:2] == ["--profile", "rates-desk"]
    assert seen["resolved"] == 0, (
        "persona-serve handed the pipeline a live Sonar key — the UI's "
        "Generate button would fire metered _sonar_verify calls")


def test_serve_refuses_when_the_scrub_did_not_hold(real_route, monkeypatch,
                                                   capsys):
    """ENFORCEMENT twin of the generate-side refusal: measured, not assumed."""
    personas.provision(personas.load("rates-desk"))
    monkeypatch.setattr(
        personas, "pipeline_probe",
        lambda env, env_file, python=None: {
            "key_len": 7, "discovery_enabled": False,
            "cap": 1.50, "cap_error": None})
    called = {"n": 0}
    from newslens import cli
    monkeypatch.setattr(cli, "main",
                        lambda argv: called.__setitem__("n", called["n"] + 1))
    assert personas.serve_main(["rates-desk"]) == 1
    assert called["n"] == 0, "a server started with a live key resolvable"
    assert "REFUSING" in capsys.readouterr().err


def test_every_persona_lane_entrypoint_scrubs_this_process(real_route,
                                                           monkeypatch):
    """EXHAUSTIVENESS, behavioural: run EVERY `*_main` this module exports with
    a live-looking key exported, and assert each one leaves the process's key
    present-but-empty.

    Discovered by introspection, never a hand-kept list — a future
    `persona-<something>` door that forgets the scrub fails here on the day it
    is written, which is the only way "no third door exists" stays true.
    """
    import os
    personas.provision(personas.load("rates-desk"))
    from newslens import cli
    monkeypatch.setattr(cli, "main", lambda argv: 0)
    monkeypatch.setattr(
        personas, "pipeline_probe",
        lambda env, env_file, python=None: {
            "key_len": 0, "discovery_enabled": False,
            "cap": 1.50, "cap_error": None})

    mains = sorted(n for n in dir(personas)
                   if n.endswith("_main") and callable(getattr(personas, n)))
    assert len(mains) >= 4, f"expected the four persona doors, found {mains}"
    for name in mains:
        os.environ[personas.SONAR_KEY_ENV] = "live-looking-key"
        getattr(personas, name)(["rates-desk"])
        assert personas.SONAR_KEY_ENV in os.environ, (
            f"{name} DELETED the key — load_env would re-inject it from .env")
        assert os.environ[personas.SONAR_KEY_ENV] == "", (
            f"{name} left a live Sonar key in this process's environment")


def test_the_scrub_is_one_implementation_that_sets_rather_than_deletes():
    """The mechanism, not just the outcome: `scrub_this_process` is the ONE
    implementation. A door that hand-rolled `del os.environ[...]` would pass a
    naive 'is it falsy' check and re-arm the paid path through load_dotenv's
    presence test, so every door must route through this function."""
    import inspect
    import os
    os.environ[personas.SONAR_KEY_ENV] = "live-looking-key"
    personas.scrub_this_process()
    assert os.environ[personas.SONAR_KEY_ENV] == ""
    for name in ("provision_main", "ready_main", "serve_main", "generate_main"):
        body = inspect.getsource(getattr(personas, name))
        assert ("scrub_this_process()" in body
                or "enforce_zero_dollar(" in body), (
            f"{name} does not go through the shared scrub")


def test_an_unpaused_discovery_refuses_both_doors(real_route, fixture_env,
                                                  monkeypatch, capsys):
    """QA-8 (gate-landed, BORN RED at the fix-return code): the docstring and
    the generate banner both said the doors refuse an unpaused tier-2
    discovery — the body never checked it. The key scrub means discovery could
    not SPEND today (it gates on the key too), so this is a claim being made
    true, not a live hole: NL-102's testing opt-in is a pre-registered future
    shell state on this machine, and a door that runs under it while printing
    "tier-2 discovery paused" states a fact it never measured.

    Real child, real load_env: the opt-in is read AFTER the dotenv load, the
    same way the run itself reads it — so an opt-in living in the .env file is
    caught exactly like an exported one.
    """
    personas.provision(personas.load("rates-desk"))
    monkeypatch.setenv("NEWSLENS_DISCOVERY_ENABLED", "1")
    refusal = personas.enforce_zero_dollar("rates-desk")
    assert refusal is not None and "discovery" in refusal, (
        "enforce_zero_dollar cleared a persona run with tier-2 discovery "
        "unpaused — the door's 'tier-2 discovery paused' banner would be a "
        "statement of fact it never measured")
    # The door itself: serve refuses before reaching the shipped CLI. (Both
    # doors share enforce_zero_dollar — the one-implementation pin above holds
    # generate to the same arm.)
    called = {"n": 0}
    from newslens import cli
    monkeypatch.setattr(cli, "main",
                        lambda argv: called.__setitem__("n", called["n"] + 1))
    assert personas.serve_main(["rates-desk"]) == 1
    assert called["n"] == 0, "a persona server started with discovery unpaused"
    assert "discovery" in capsys.readouterr().err
    # And the clear path still clears — guards an inverted or always-on check.
    monkeypatch.delenv("NEWSLENS_DISCOVERY_ENABLED")
    assert personas.enforce_zero_dollar("rates-desk") is None


# --- QA-2: provision must not mint before it checks -----------------------

def test_a_malformed_env_profile_mints_nothing(real_route, monkeypatch):
    """QA-2's red test. `current_profile()` raises on a malformed
    NEWSLENS_PROFILE — and it was being called AFTER `profiles.create`, so the
    refusal landed on a world that already existed. The operator's natural
    retry then reported success over a zero-tag, un-rankable reader."""
    monkeypatch.setenv("NEWSLENS_PROFILE", "bad!!name")
    rc = personas.provision_main(["rates-desk"])
    assert rc != 0
    assert not profiles.exists("rates-desk"), (
        "a failed provision left a world on disk — the next run reports "
        "'already exists' and exits 0 over an un-rankable reader")


def test_a_failed_provision_says_whether_a_world_survived(real_route,
                                                          monkeypatch, capsys):
    """Honesty half: when a mint DOES survive a failure, the message must say
    so and carry the same INCOMPLETE guidance the partial-tag path gives."""
    from newslens import server

    def boom(name, level):
        raise RuntimeError("synthetic editor failure")

    monkeypatch.setattr(server, "topic_add", boom)
    assert personas.provision_main(["rates-desk"]) == 1
    err = capsys.readouterr().err
    assert "INCOMPLETE" in err, "a surviving half-made world was not disclosed"
    assert profiles.exists("rates-desk")


# --- QA-4: the re-exec decision is a real, tested predicate ---------------

def test_the_reexec_predicate_fires_for_a_non_venv_interpreter(tmp_path):
    """QA-4: the old guard compared RESOLVED interpreter paths, and a venv's
    python symlinks to the same system binary — so the branch was unreachable
    and the report described behaviour that never occurred. The decision is now
    a pure function over venv ROOTS, tested in both directions."""
    venv = tmp_path / ".venv"
    assert personas._needs_reexec(prefix="/usr", venv_dir=venv, marker="")
    assert not personas._needs_reexec(prefix=str(venv), venv_dir=venv,
                                      marker="")
    assert not personas._needs_reexec(prefix="/usr", venv_dir=venv,
                                      marker="1"), (
        "the loop guard must stop a second re-exec")
