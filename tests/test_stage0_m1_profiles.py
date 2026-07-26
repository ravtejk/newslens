"""Stage-0 M1 — the guarded profile dimension + the seeding kill.

Contract (dispatch 2026-07-25, multi-user brief §1 "The correction (Rook)" +
§2, TRACKER NL-78/NL-81, M0 report finding F1):

  1. RED-1: no seeding path exists. Killed structurally, not gated.
  2. Profiles resolve INSIDE the guarded lane. A profile is REAL STATE behind
     the SAME refusal as the founder's — never NEWSLENS_DATA_DIR redirection
     (the seam's law is "redirection = not real state"; a tester's memory must
     not live behind the door labelled write-freely).
  3. ZERO-MOVE adoption: the founder is the `default` profile and his paths
     are unchanged, in place, byte for byte. No file moves. Ever.
  4. Provisioning inherits nothing: fresh fully-migrated DB, 0-byte memory.md,
     own dirs/ledger, own copy of the COMMITTED catalog with interests empty.
  5. Ops: migrate --all-profiles; a profile-aware doctor.

Every disk-touching test provisions under tmp_path (paths.anchor_dir() follows
the sandbox's NEWSLENS_DATA_DIR). The conftest tripwire watches the real
profiles/ directory, so a test that leaked into the checkout fails by name.
"""
from __future__ import annotations

import hashlib
import sqlite3

import pytest

from newslens import cli, config, db, doctor, memory, paths, profiles, server

GUARDED_NAMES = ("DATA_DIR", "DB_PATH", "SOURCES_FILE", "ENV_FILE", "MEMORY_FILE")


@pytest.fixture
def unshadowed(monkeypatch):
    """Drop the conftest module-dict shadows so paths.__getattr__ — the actual
    guard — is what answers. Env seams are left in place; tests that need them
    gone delete them explicitly."""
    for name in GUARDED_NAMES:
        monkeypatch.delitem(vars(paths), name, raising=False)


@pytest.fixture
def bare(monkeypatch, unshadowed):
    """unshadowed + every redirection env var removed: __getattr__ falls all
    the way through to the sanction check, exactly as it does in a real run."""
    for var in ("NEWSLENS_DATA_DIR", "NEWSLENS_DB_PATH", "NEWSLENS_SOURCES_FILE",
                "NEWSLENS_ENV_FILE", "NEWSLENS_MEMORY_FILE"):
        monkeypatch.delenv(var, raising=False)


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def snapshot_founder(tmp_path):
    """Hash+mtime of every founder-owned file in the sandbox world."""
    out = {}
    for rel in ("sources.yaml", "memory.md", ".env", "data/newslens.db"):
        p = tmp_path / rel
        out[rel] = (sha(p), p.stat().st_mtime_ns) if p.exists() else None
    return out


# ===========================================================================
# 1. ZERO-MOVE: the founder does not move, and cannot be moved
# ===========================================================================

def test_default_profile_layout_is_literally_the_pre_m1_guarded_table():
    """The zero-move promise as a mechanism: the default profile's five
    locations ARE _GUARDED, key for key, path for path. If anyone ever
    "tidies" data/ into profiles/default/, this fails."""
    layout = paths.profile_layout(paths.DEFAULT_PROFILE, paths.PROJECT_ROOT)
    assert layout == paths._GUARDED
    assert layout["DATA_DIR"] == paths.PROJECT_ROOT / "data"
    assert layout["MEMORY_FILE"] == paths.PROJECT_ROOT / "memory.md"
    assert layout["SOURCES_FILE"] == paths.PROJECT_ROOT / "sources.yaml"
    assert paths.profile_root(paths.DEFAULT_PROFILE, paths.PROJECT_ROOT) \
        == paths.PROJECT_ROOT


def test_sanctioned_default_profile_resolves_the_identical_object(bare, monkeypatch):
    """Not merely an equal path — the same object out of the same table, so
    the founder's resolution provably runs the pre-M1 line of code."""
    monkeypatch.setenv("NEWSLENS_REAL_DATA", "1")
    assert paths.current_profile() == paths.DEFAULT_PROFILE
    for name in GUARDED_NAMES:
        assert getattr(paths, name) is paths._GUARDED[name]


def test_default_profile_never_references_the_profiles_directory(bare, monkeypatch):
    monkeypatch.setenv("NEWSLENS_REAL_DATA", "1")
    for name in GUARDED_NAMES:
        assert paths.PROFILES_DIRNAME not in str(getattr(paths, name))


def test_provisioning_never_touches_founder_state(tmp_path):
    """Any code that would relocate his real data = STOP. This is the pin.

    A REAL founder world (migrated DB, populated memory.md, his sources.yaml,
    his .env), hashed before and after two provisionings and a full listing."""
    db.migrate()
    con = db.connect()
    try:
        memory.add_thread(con, "His Own Thread")
    finally:
        con.close()
    (tmp_path / "memory.md").write_text("- His Own Thread\n", encoding="utf-8")
    (tmp_path / ".env").write_text("OPENAI_API_KEY=x\n", encoding="utf-8")
    before = snapshot_founder(tmp_path)

    profiles.create("tester")
    profiles.create("tester2")
    [st.line() for st in profiles.inventory()]

    assert snapshot_founder(tmp_path) == before
    # and nothing of his was copied into the new world
    assert (tmp_path / "profiles" / "tester" / "memory.md").read_bytes() == b""
    assert "Iran War" not in (tmp_path / "profiles" / "tester"
                              / "sources.yaml").read_text(encoding="utf-8")


# ===========================================================================
# 2. THE SEAM: profiles are real state inside the guarded lane
# ===========================================================================

def test_profile_paths_are_refused_to_an_unsanctioned_process(bare, monkeypatch):
    """Rook's law, mechanically: a profile does NOT open a door. The same
    RuntimeError protects a tester's world as the founder's."""
    monkeypatch.delenv("NEWSLENS_REAL_DATA", raising=False)
    paths.set_profile("tester")
    for name in GUARDED_NAMES:
        with pytest.raises(RuntimeError) as exc:
            getattr(paths, name)
        assert "refused" in str(exc.value)


def test_sanctioned_profile_resolves_under_the_profiles_tree(bare, monkeypatch):
    monkeypatch.setenv("NEWSLENS_REAL_DATA", "1")
    paths.set_profile("tester")
    root = paths.PROJECT_ROOT / "profiles" / "tester"
    assert paths.DATA_DIR == root / "data"
    assert paths.DB_PATH == root / "data" / "newslens.db"
    assert paths.MEMORY_FILE == root / "memory.md"
    assert paths.SOURCES_FILE == root / "sources.yaml"
    # .env is machine-level, never per-reader: one key set, one machine.
    assert paths.ENV_FILE == paths._GUARDED["ENV_FILE"]


def test_set_profile_sets_no_redirection_env_var(monkeypatch):
    """THE seam-inversion pin. If profiles were ever re-implemented as a
    NEWSLENS_DATA_DIR redirect, this fails: the whole point is that a
    profile's state is NOT behind the write-freely door."""
    for var in ("NEWSLENS_DATA_DIR", "NEWSLENS_DB_PATH", "NEWSLENS_SOURCES_FILE",
                "NEWSLENS_ENV_FILE", "NEWSLENS_MEMORY_FILE"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.delenv("NEWSLENS_PROFILE", raising=False)
    paths.set_profile("tester")
    import os
    for var in ("NEWSLENS_DATA_DIR", "NEWSLENS_DB_PATH", "NEWSLENS_SOURCES_FILE",
                "NEWSLENS_ENV_FILE", "NEWSLENS_MEMORY_FILE"):
        assert var not in os.environ, f"{var} was set by set_profile — the seam inverted"
    # nor NEWSLENS_PROFILE itself: set_profile is in-process only (see its
    # docstring — the export leaked a reader across two main() calls).
    assert "NEWSLENS_PROFILE" not in os.environ
    assert paths.current_profile() == "tester"


def test_a_profile_pin_does_not_leak_into_the_next_cli_call(tmp_path, capsys):
    """The exact leak M1's own CLI probe caught: `--profile tester1 memory add`
    followed by a bare `memory list` in the SAME process listed the tester's
    threads as the founder's."""
    profiles.create("tester1")
    assert cli.main(["--profile", "tester1", "profile", "list"]) == 0
    capsys.readouterr()
    assert cli.main(["profile", "list"]) == 0
    out = capsys.readouterr().out
    active = [ln for ln in out.splitlines() if ln.startswith("*")]
    assert active and "default" in active[0], out
    assert paths.current_profile() == paths.DEFAULT_PROFILE


def test_active_redirections_are_disclosed_not_silent(tmp_path, capsys):
    """The precedence hazard M1's own CLI probe hit: with a redirection var
    exported, `--profile tester1` still gets the redirected file. We keep the
    precedence (it is what makes the sandbox hermetic) and refuse to let it be
    silent."""
    profiles.create("tester")
    assert cli.main(["--profile", "tester", "profile", "list"]) == 0
    err = capsys.readouterr().err
    assert "'tester' is active, but" in err and "OUTRANK it" in err
    assert "NEWSLENS_MEMORY_FILE=" in err
    # the founder's own world is never warned about: redirection IS his sandbox
    assert profiles.redirection_warnings("default") == []


def test_redirection_still_outranks_the_profile(unshadowed, monkeypatch, tmp_path):
    """The sandbox seam keeps its precedence: an explicitly redirected
    location is not real state, whatever profile is active."""
    monkeypatch.delenv("NEWSLENS_DB_PATH", raising=False)   # exercise the derived arm
    monkeypatch.setenv("NEWSLENS_DATA_DIR", str(tmp_path / "sandbox"))
    monkeypatch.setenv("NEWSLENS_REAL_DATA", "1")
    paths.set_profile("tester")
    assert paths.DATA_DIR == tmp_path / "sandbox"
    assert paths.DB_PATH == tmp_path / "sandbox" / "newslens.db"


# ===========================================================================
# 3. SLUG SAFETY — a name we would have to repair is a name we refuse
# ===========================================================================

@pytest.mark.parametrize("bad", [
    "..", "../data", "a/b", "/etc", "~", "", "   ", "Alice", "alice.bak",
    "-lead", "_lead", "a" * 33, "défaut", "alice bob", "alice\nbob", "..\\x",
])
def test_bad_profile_names_are_refused(bad):
    with pytest.raises(paths.ProfileError):
        paths.normalize_profile(bad)


@pytest.mark.parametrize("good", ["default", "alice", "tester-1", "p_2", "a"])
def test_good_profile_names_pass(good):
    assert paths.normalize_profile(good) == good


def test_surrounding_whitespace_is_trimmed_not_repaired():
    """`NEWSLENS_PROFILE=$(cat file)` picks up a trailing newline; trimming
    that is safe because the slug regex still refuses everything dangerous.
    Nothing INSIDE the name is ever repaired — see the refusal list above."""
    assert paths.normalize_profile("  alice \n") == "alice"


def test_malformed_env_profile_raises_instead_of_falling_back(monkeypatch):
    """Silently degrading to the default profile would route a tester's writes
    into the founder's world — the worst available failure."""
    monkeypatch.setenv("NEWSLENS_PROFILE", "../data")
    with pytest.raises(paths.ProfileError):
        paths.current_profile()


def test_unknown_profile_is_refused_not_created(tmp_path):
    with pytest.raises(profiles.ProfileMissingError) as exc:
        profiles.require_exists("ghost")
    assert "profile create ghost" in str(exc.value)
    assert not (tmp_path / "profiles" / "ghost").exists()


# ===========================================================================
# 4. PROVISIONING — inherits nothing
# ===========================================================================

def test_create_provisions_a_fully_migrated_empty_world(tmp_path):
    st = profiles.create("tester")
    root = tmp_path / "profiles" / "tester"
    assert st.root == root and root.is_dir()
    assert (root / "data").is_dir()
    assert st.db_path == root / "data" / "newslens.db"

    # every migration, through the latest on disk
    assert db.pending_migrations(st.db_path) == []
    applied = db.applied_migrations(db.connect_readonly(st.db_path))
    assert "0022_memory_sync_guard.sql" in applied
    assert len(applied) == len(db.migration_files())

    # 0-byte memory.md (M0's proven lawful true-zero start)
    assert (root / "memory.md").exists()
    assert (root / "memory.md").stat().st_size == 0

    # its own catalog, interests EMPTY
    cfg = config.load_sources(root / "sources.yaml")
    assert cfg.problems == []
    assert cfg.has_active_sources
    assert cfg.has_interests is False


def test_create_seeds_nothing_at_all(tmp_path):
    """RED-1 at the provisioning surface: a brand-new profile's database is
    empty, and its first sync leaves it empty."""
    st = profiles.create("tester")
    con = db.connect(st.db_path)
    try:
        assert con.execute("SELECT COUNT(*) c FROM memory").fetchone()["c"] == 0
        layout = paths.profile_layout("tester")
        with pytest.MonkeyPatch.context() as mp:
            mp.setitem(vars(paths), "MEMORY_FILE", layout["MEMORY_FILE"])
            res = memory.sync_memory(con)
        assert res.seeded == 0
        assert not res.stale_refusal
        assert con.execute("SELECT COUNT(*) c FROM memory").fetchone()["c"] == 0
        rendered = layout["MEMORY_FILE"].read_text(encoding="utf-8")
        for founder_topic in ("Iran War", "Strait of Hormuz", "Stagflation"):
            assert founder_topic not in rendered
    finally:
        con.close()


def test_create_refuses_over_an_existing_profile(tmp_path):
    profiles.create("tester")
    marker = tmp_path / "profiles" / "tester" / "memory.md"
    marker.write_text("- My Own Thread\n", encoding="utf-8")
    with pytest.raises(profiles.ProfileExistsError):
        profiles.create("tester")
    assert marker.read_text(encoding="utf-8") == "- My Own Thread\n"


def test_create_refuses_the_founders_default(tmp_path):
    with pytest.raises(profiles.ProfileExistsError) as exc:
        profiles.create("default")
    assert "founder" in str(exc.value)
    assert not (tmp_path / "profiles").exists()


def test_created_profile_sources_come_from_the_committed_template(tmp_path):
    """Never the principal's working sources.yaml — his local edits stay his."""
    (tmp_path / "sources.yaml").write_text(
        "sources:\n  - name: His Private Feed\n    rss_url: https://x.invalid/f\n"
        "interests:\n  broad: [His Secret Interest]\n", encoding="utf-8")
    st = profiles.create("tester")
    text = (st.root / "sources.yaml").read_text(encoding="utf-8")
    assert "His Private Feed" not in text
    assert "His Secret Interest" not in text
    assert text == paths.PROFILE_SOURCES_TEMPLATE.read_text(encoding="utf-8")


def test_the_shipped_template_parses_clean_and_carries_no_interests():
    """Drift guard on the committed template itself."""
    assert paths.PROFILE_SOURCES_TEMPLATE.exists()
    cfg = config.load_sources(paths.PROFILE_SOURCES_TEMPLATE)
    assert cfg.problems == []
    assert len(cfg.fetchable_sources) > 10
    assert cfg.interests_broad == [] and cfg.interests_granular == []


# --- QA fix loop 1, F3: the Commissioning's own door must open --------------

def test_a_freshly_provisioned_profile_can_add_its_first_interest(
        real_route, monkeypatch):
    """The one act the Commissioning routes a new reader toward. The M1
    template shipped flow-style `broad: []`, and the topic editor appends a
    block-sequence item under it — invalid YAML, honestly reverted, door
    shut. Runs under real_route so SOURCES_FILE is genuinely the PROFILE's."""
    tmp_path = real_route
    profiles.create("alice")
    paths.set_profile("alice")
    ok, msg = server.topic_add("Grain Corridor", "broad")
    assert ok, msg
    ok2, msg2 = server.topic_add("Chokepoints", "specific")
    assert ok2, msg2
    cfg = config.load_sources(paths.SOURCES_FILE)
    assert cfg.problems == []
    assert cfg.interests_broad == ["Grain Corridor"]
    assert cfg.interests_granular == ["Chokepoints"]
    assert cfg.has_active_sources                 # catalog survived surgery
    # and it landed ONLY in this profile's file
    assert (tmp_path / "profiles" / "alice" / "sources.yaml").read_text(
        encoding="utf-8").count("Grain Corridor") == 1
    assert "Grain Corridor" not in \
        paths.PROFILE_SOURCES_TEMPLATE.read_text(encoding="utf-8")


def test_the_editor_tolerates_a_hand_written_empty_flow_list(tmp_path):
    """Defends hand-edited files, not just our template: `broad: []` typed by
    a human is the same shape and hit the same wall."""
    src = paths.SOURCES_FILE
    src.write_text(
        "sources:\n  - name: Example\n    rss_url: https://e.invalid/f\n"
        "interests:\n  broad: []\n  granular: []\n", encoding="utf-8")
    ok, msg = server.topic_add("Shipping", "broad")
    assert ok, msg
    cfg = config.load_sources(src)
    assert cfg.problems == [] and cfg.interests_broad == ["Shipping"]


def test_a_non_empty_flow_list_is_left_exactly_as_it_behaved_before(tmp_path):
    """Scope fence on the tolerance: ONLY the empty flow list is converted.
    A populated flow list is untouched and still fails the validator — the
    pre-existing behaviour, unchanged, and the file is restored intact."""
    src = paths.SOURCES_FILE
    text = ("sources:\n  - name: Example\n    rss_url: https://e.invalid/f\n"
            "interests:\n  broad: [Alpha, Beta]\n  granular: []\n")
    src.write_text(text, encoding="utf-8")
    ok, msg = server.topic_add("Shipping", "broad")
    # NL-103 FIX-2 RE-PIN: the revert refusal is reader copy now — same
    # behaviour (nothing saved, file restored), register wording.
    assert not ok and msg == ("Nothing was saved — that change would have "
                              "broken your sources file.")
    assert src.read_text(encoding="utf-8") == text


# ===========================================================================
# 5. ISOLATION — two profiles never see each other
# ===========================================================================

def test_two_profiles_have_disjoint_state(tmp_path):
    a = profiles.create("alice")
    b = profiles.create("bob")
    assert a.db_path != b.db_path

    con_a = db.connect(a.db_path)
    try:
        memory.add_thread(con_a, "Alice Only Thread")
    finally:
        con_a.close()

    con_b = db.connect_readonly(b.db_path)
    try:
        rows = [r["topic"] for r in con_b.execute("SELECT topic FROM memory")]
    finally:
        con_b.close()
    assert rows == []

    # the founder's sandbox DB never learned about it either
    db.migrate()
    con_d = db.connect_readonly(paths.DB_PATH)
    try:
        assert [r["topic"] for r in con_d.execute("SELECT topic FROM memory")] == []
    finally:
        con_d.close()


def test_each_profile_owns_its_spend_ledger_and_artifacts(tmp_path):
    profiles.create("alice")
    profiles.create("bob")
    la, lb = paths.profile_layout("alice"), paths.profile_layout("bob")
    assert la["DATA_DIR"] != lb["DATA_DIR"]
    # the generation log (the spend ledger) and briefings/tts artifacts all
    # hang off DATA_DIR, so per-profile DATA_DIR separates all of them.
    assert la["DATA_DIR"] / "generation_log.jsonl" \
        != lb["DATA_DIR"] / "generation_log.jsonl"
    assert paths.profile_layout(paths.DEFAULT_PROFILE)["DATA_DIR"] \
        not in (la["DATA_DIR"], lb["DATA_DIR"])


def test_nl81_pairing_identity_is_per_profile(tmp_path):
    """The NL-81 stamp refuses a cross-profile file/DB pair, and now NAMES the
    profiles because provisioning stamps sync_state.profile_slug."""
    a = profiles.create("alice")
    b = profiles.create("bob")
    con_a, con_b = db.connect(a.db_path), db.connect(b.db_path)
    try:
        st_a, st_b = memory.sync_state(con_a), memory.sync_state(con_b)
        assert st_a["identity"] != st_b["identity"]
        assert st_a["profile"] == "alice" and st_b["profile"] == "bob"

        la = paths.profile_layout("alice")
        with pytest.MonkeyPatch.context() as mp:
            mp.setitem(vars(paths), "MEMORY_FILE", la["MEMORY_FILE"])
            memory.sync_memory(con_a)                    # alice renders gen 1
            stamp = memory.parse_stamp(
                la["MEMORY_FILE"].read_text(encoding="utf-8"))
            assert stamp["profile"] == "alice"
            res = memory.sync_memory(con_b)              # bob reads alice's file
        assert res.stale_refusal
        assert "DIFFERENT NewsLens database" in res.stale_refusal
        assert "alice" in res.stale_refusal and "bob" in res.stale_refusal
        assert res.added == [] and res.dismissed_by_deletion == []
    finally:
        con_a.close()
        con_b.close()


# ===========================================================================
# 6. OPS — migrate --all-profiles, list, doctor
# ===========================================================================

def test_migrate_all_profiles_brings_every_database_current(tmp_path):
    profiles.create("alice")
    profiles.create("bob")
    latest = db.migration_files()[-1].name

    # Rebuild bob's database one migration BEHIND, honestly: apply every
    # migration except the newest, from a copy of migrations/ (no surgery on a
    # migrated schema — a hand-dropped table is not a pre-migration world).
    behind = tmp_path / "migrations-behind"
    behind.mkdir()
    for m in db.migration_files()[:-1]:
        (behind / m.name).write_text(m.read_text(encoding="utf-8"), encoding="utf-8")
    bob_db = paths.profile_layout("bob")["DB_PATH"]
    bob_db.unlink()
    db.migrate(db_path=bob_db, migrations_dir=behind)
    assert db.pending_migrations(bob_db) == [latest]

    ran = profiles.migrate_all()
    assert set(ran) == {"default", "alice", "bob"}
    assert ran["bob"] == [latest]
    assert ran["alice"] == []
    for slug in ("default", "alice", "bob"):
        assert db.pending_migrations(profiles.db_path_for(slug)) == []


def test_profile_list_status_is_honest(tmp_path, capsys):
    profiles.create("alice")
    assert cli.main(["profile", "list"]) == 0
    out = capsys.readouterr().out
    assert "default" in out and "founder" in out
    assert "alice" in out
    assert "interests: NONE — not commissioned yet" in out
    assert "0 bytes (unwritten)" in out


def test_profile_list_reports_stray_directories(tmp_path, capsys):
    (tmp_path / "profiles" / "Not A Profile").mkdir(parents=True)
    assert cli.main(["profile", "list"]) == 0
    out = capsys.readouterr().out
    assert "Not A Profile/ — not a valid profile name" in out


def test_doctor_names_the_profile_it_checked(tmp_path, capsys):
    profiles.create("alice")
    paths.set_profile("alice")
    doctor.run_doctor()
    out = capsys.readouterr().out
    assert "profile: alice" in out
    assert "profiles/alice/" in out


def test_doctor_names_the_default_profile_as_the_founders(capsys):
    doctor.run_doctor()
    out = capsys.readouterr().out
    assert "profile: default" in out and "founder" in out


# ===========================================================================
# 7. CLI WIRING — the flag actually moves the world
# ===========================================================================

def test_cli_profile_flag_routes_migrate_to_that_profiles_database(
        tmp_path, capsys):
    profiles.create("alice")
    alice_db = paths.profile_layout("alice")["DB_PATH"]
    alice_db.unlink()
    assert not alice_db.exists()

    assert cli.main(["--profile", "alice", "migrate"]) == 0
    out = capsys.readouterr().out
    assert alice_db.exists()
    assert str(alice_db) in out
    # and the founder's database was NOT created by that call
    assert not (tmp_path / "data" / "newslens.db").exists()


def test_cli_refuses_an_unknown_profile_without_creating_it(tmp_path, capsys):
    assert cli.main(["--profile", "ghost", "migrate"]) == 2
    err = capsys.readouterr().err
    assert "no profile named 'ghost'" in err
    assert not (tmp_path / "profiles" / "ghost").exists()
    assert not (tmp_path / "data" / "newslens.db").exists()


def test_cli_refuses_a_malformed_profile_name(tmp_path, capsys):
    assert cli.main(["--profile", "../data", "migrate"]) == 2
    assert "invalid profile name" in capsys.readouterr().err


def test_cli_profile_create_end_to_end(tmp_path, capsys):
    assert cli.main(["profile", "create", "tester"]) == 0
    out = capsys.readouterr().out
    assert "created profile 'tester'" in out
    assert "0 bytes" in out and "interests EMPTY by design" in out
    assert (tmp_path / "profiles" / "tester" / "data" / "newslens.db").exists()
    # second attempt refuses, exit 2, nothing clobbered
    assert cli.main(["profile", "create", "tester"]) == 2
    assert "already exists" in capsys.readouterr().err


def test_cli_migrate_all_profiles_flag_is_wired(tmp_path, capsys):
    profiles.create("alice")
    paths.profile_layout("alice")["DB_PATH"].unlink()
    assert cli.main(["migrate", "--all-profiles"]) == 0
    out = capsys.readouterr().out
    assert "[alice]" in out and "[default]" in out
    assert paths.profile_layout("alice")["DB_PATH"].exists()


def test_doctor_main_takes_the_profile_flag(tmp_path, capsys):
    profiles.create("alice")
    rc = doctor.main(["--profile", "alice"])
    assert rc in (0, 1)                       # health verdict is not the point
    assert "profile: alice" in capsys.readouterr().out


def test_doctor_main_refuses_an_unknown_profile(tmp_path, capsys):
    assert doctor.main(["--profile", "ghost"]) == 2
    assert "no profile named 'ghost'" in capsys.readouterr().err


# --- QA fix loop 1, F1/F2: the doctor's ENV-profile side door ---------------
#
# doctor.main used to validate only the --profile FLAG. With NEWSLENS_PROFILE
# set — the exact shell/launchd shape README and SETUP document — a typo'd
# name sailed past every check and the writability probe (check_database §2)
# MINTED profiles/<typo>/data/. After that one run `require_exists` sees a
# directory and EVERY verb accepts the ghost: migrate gives it a database,
# `memory add` buries a reader's writes in it. These pins run under
# `real_route` because the plain sandbox's module-dict shadow hides the site.

def test_doctor_refuses_an_env_selected_ghost_and_mints_nothing(
        real_route, monkeypatch, capsys):
    tmp_path = real_route
    monkeypatch.setenv("NEWSLENS_PROFILE", "ghost")
    rc = doctor.main([])
    out, err = capsys.readouterr()
    assert rc == 2, out
    assert "no profile named 'ghost'" in err
    assert "profile create ghost" in err
    assert not (tmp_path / "profiles" / "ghost").exists()
    assert not (tmp_path / "profiles").exists()
    assert "profile: ghost" not in out          # the checks never ran


def test_doctor_refuses_an_env_malformed_profile_without_a_traceback(
        real_route, monkeypatch, capsys):
    tmp_path = real_route
    monkeypatch.setenv("NEWSLENS_PROFILE", "../data")
    rc = doctor.main([])                        # must NOT raise
    err = capsys.readouterr().err
    assert rc == 2
    assert "invalid profile name" in err
    assert not (tmp_path / "profiles").exists()


def test_doctor_profile_pin_does_not_leak_into_the_next_main(tmp_path, capsys):
    """F2 — the doctor sibling of the CLI leak. Two in-process main() calls:
    the second, with no flag and no env, is the founder again."""
    profiles.create("alice")
    assert doctor.main(["--profile", "alice"]) in (0, 1)
    assert "profile: alice" in capsys.readouterr().out
    assert doctor.main([]) in (0, 1)
    out = capsys.readouterr().out
    assert "profile: default" in out and "profile: alice" not in out
    assert paths.current_profile() == paths.DEFAULT_PROFILE


def test_the_writability_probe_refuses_to_mint_a_missing_profile_root(
        real_route, capsys):
    """Defence in depth (QA-flagged for the gate): even if some future
    entrypoint forgets require_exists, the doctor's one deliberate write must
    not CREATE a reader's world. It may still create data/ INSIDE a profile
    that exists — see the twin below."""
    tmp_path = real_route
    paths.set_profile("ghost")                  # bypasses main()'s refusal
    results = doctor.check_database()
    assert not (tmp_path / "profiles" / "ghost").exists()
    assert not (tmp_path / "profiles").exists()
    assert any("ghost" in r.text and "does not exist" in r.text
               for r in results), [r.text for r in results]


def test_the_writability_probe_still_creates_data_for_a_real_profile(
        real_route):
    """The refusal above must not break the probe's actual job."""
    tmp_path = real_route
    profiles.create("alice")
    import shutil
    shutil.rmtree(tmp_path / "profiles" / "alice" / "data")
    paths.set_profile("alice")
    doctor.check_database()
    assert (tmp_path / "profiles" / "alice" / "data").is_dir()


# ===========================================================================
# 7b. THE END-TO-END ROUTING PROOF
#
# Everything above runs inside the conftest sandbox, where the guarded names
# are module-dict SHADOWS — so paths.__getattr__, the thing that actually
# routes a profile in a real run, never answers. This harness removes the
# shadows AND the redirection env vars and re-anchors PROJECT_ROOT at tmp_path
# instead, so the code under test takes the identical route it takes on the
# principal's machine (sanction -> current_profile -> profile_layout) while
# every byte still lands in tmp. Without it, "the --profile flag routes the
# world" would be a claim, not a proof.
# ===========================================================================

@pytest.fixture
def real_route(bare, monkeypatch, tmp_path):
    monkeypatch.setattr(paths, "PROJECT_ROOT", tmp_path)
    monkeypatch.setenv("NEWSLENS_REAL_DATA", "1")
    return tmp_path


def test_guarded_names_route_a_real_verb_into_the_profiles_world(real_route):
    tmp_path = real_route
    profiles.create("alice")
    paths.set_profile("alice")
    root = tmp_path / "profiles" / "alice"

    con = db.connect()                       # NO explicit path: the guard routes
    try:
        assert memory.sync_memory(con).seeded == 0
        memory.add_thread(con, "Alice's Own First Follow")
        memory.write_memory_file(con)        # the verb's trailing render
    finally:
        con.close()

    assert (root / "data" / "newslens.db").exists()
    text = (root / "memory.md").read_text(encoding="utf-8")
    assert "Alice's Own First Follow" in text
    assert memory.parse_stamp(text)["profile"] == "alice"
    # the default profile's world was never created in this anchored tree
    assert not (tmp_path / "data").exists()
    assert not (tmp_path / "memory.md").exists()


def test_cli_profile_flag_routes_a_whole_verb_end_to_end(real_route, capsys):
    tmp_path = real_route
    profiles.create("alice")
    capsys.readouterr()

    assert cli.main(["--profile", "alice", "memory", "add", "Alice Thread"]) == 0
    capsys.readouterr()

    root = tmp_path / "profiles" / "alice"
    assert "Alice Thread" in (root / "memory.md").read_text(encoding="utf-8")
    con = db.connect_readonly(root / "data" / "newslens.db")
    try:
        assert [r["topic"] for r in con.execute("SELECT topic FROM memory")] \
            == ["Alice Thread"]
    finally:
        con.close()
    # the founder's world, in this anchored tree, does not exist at all
    assert not (tmp_path / "data").exists()
    assert not (tmp_path / "memory.md").exists()


def test_env_profile_alone_routes_the_world(real_route, monkeypatch):
    """NEWSLENS_PROFILE with no --profile flag — the `serve`-under-launchd
    shape."""
    tmp_path = real_route
    profiles.create("alice")
    monkeypatch.setenv("NEWSLENS_PROFILE", "alice")
    assert paths.current_profile() == "alice"
    assert paths.DB_PATH == tmp_path / "profiles" / "alice" / "data" / "newslens.db"
    assert paths.MEMORY_FILE == tmp_path / "profiles" / "alice" / "memory.md"


# ===========================================================================
# 8. THE KILL — no seeding surface survives anywhere
# ===========================================================================

def test_no_seeding_symbol_survives_in_the_package():
    assert not hasattr(memory, "SEED_THREADS")
    assert not hasattr(memory, "seed_if_first_run")


def test_a_virgin_database_stays_empty_through_the_embedded_sync_path(tmp_path):
    """The M0 trigger surface (the first EMBEDDED sync, which rank and the
    server POST verbs run) on a virgin world: still zero rows."""
    db.migrate()
    con = db.connect()
    try:
        assert not paths.MEMORY_FILE.exists()
        res = memory.sync_memory(con)
        assert res.seeded == 0
        assert con.execute("SELECT COUNT(*) c FROM memory").fetchone()["c"] == 0
        assert memory.active_context(con) == []
    finally:
        con.close()


def test_sync_result_still_reports_a_truthful_seeded_zero(tmp_path):
    db.migrate()
    con = db.connect()
    try:
        assert memory.sync_memory(con).seeded == 0
    finally:
        con.close()
