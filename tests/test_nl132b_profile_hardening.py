"""NL-132-B — the profile-review hardening slice.

Charter of record: the NL-132 engineering-2 scoping (option B, 2026-08-01) +
DECISIONS 2026-08-02 ruling (3), grown by the NL-133 gate's R-E rider.
Three items, one milestone:

  1. `scripts/reader-serve` — serve ANY profile with the $0 scrub STRUCTURAL
     by default (the present-but-empty PERPLEXITY_API_KEY form, personas.py
     :310-357), `--live` the explicit opt-in, `--create` mint-and-serve in one
     command, a PROBED free port (never the founder's 8484, never a bound one)
     and a ledger printout at exit. Rook's law, from the scoping: at review
     cadence the scrub must be structural, not a shell prefix somebody has to
     remember — and the obvious spelling of "remove the key" (`env -u`) is the
     one that spends money.
  2. `newslens profile delete` — teardown that is not `rm -rf` beside the
     founder's own world. Refuses the founder STRUCTURALLY (resolved paths,
     not a name comparison), requires the exact slug typed back, prints what
     it removed.
  3. The tripwire widening (NL-133 gate R-E): `_real_state_snapshot` stat'd
     `profiles/` at the TOP LEVEL only, so a nested profile-DB rewrite — the
     exact in-place class v7-M2 closed for `data/` — was invisible. QA proved
     the gap by execution, both arms (2026-08-02 QA report §7).

Every disk-touching test here provisions under tmp_path: the autouse sandbox
redirects `paths.anchor_dir()`, and the conftest tripwire itself watches the
real `profiles/`, so a test that leaked into the checkout fails by name.
"""
from __future__ import annotations

import builtins
import json
import os
import socket
import stat
import sys
import time
from pathlib import Path

import pytest

from newslens import cli, paths, personas, profiles

FAKE_KEY = "pplx-FAKE-not-a-credential-0123456789"
GUARDED_NAMES = ("DATA_DIR", "DB_PATH", "SOURCES_FILE", "ENV_FILE", "MEMORY_FILE")


# ===========================================================================
# ITEM 3 — the tripwire sees nested profile writes (NL-133 gate R-E rider)
#
# Template: test_v7_m2_qa.py::test_tripwire_snapshot_sees_inplace_db_and_log_
# rewrites. Same manual swap/restore discipline, and for the same reason: the
# monkeypatch fixture is instantiated BEFORE the autouse tripwire, so its undo
# would land after the tripwire's own after-snapshot and the mirror paths would
# leak into it.
# ===========================================================================

def _conftest_module():
    for m in list(sys.modules.values()):
        f = (getattr(m, "__file__", "") or "").replace("\\", "/")
        if f.endswith("tests/conftest.py"):
            return m
    raise AssertionError("tests/conftest.py module not found in sys.modules")


def _profiles_mirror(tmp_path):
    """A profiles/ shaped exactly like the real one: <slug>/data/newslens.db,
    <slug>/data/generation_log.jsonl, <slug>/memory.md, <slug>/sources.yaml."""
    mirror = tmp_path / "fakeprofiles"
    for slug in ("fresh1", "fresh2"):
        data = mirror / slug / "data"
        data.mkdir(parents=True)
        (data / "newslens.db").write_bytes(b"SQLite format 3\x00 original")
        (data / "generation_log.jsonl").write_text('{"real": "line"}\n',
                                                   encoding="utf-8")
        (mirror / slug / "memory.md").write_text("", encoding="utf-8")
        (mirror / slug / "sources.yaml").write_text("interests: {}\n",
                                                    encoding="utf-8")
    return mirror


def test_tripwire_snapshot_sees_nested_profile_db_rewrites(tmp_path):
    """BORN RED (NL-133 gate R-E; QA's arm-1 probe is this shape).

    An in-place rewrite of `profiles/<slug>/data/newslens.db` moves neither
    `profiles/`'s mtime nor its listing, so the pre-widening snapshot — one
    stat + one listdir on the top directory — could not see it. That is the
    identical class v7-M2 closed for `data/`, never extended to `profiles/`,
    and live surface since a real fresh1 world exists on this machine.
    """
    cf = _conftest_module()
    mirror = _profiles_mirror(tmp_path)
    nested_db = mirror / "fresh1" / "data" / "newslens.db"
    nested_log = mirror / "fresh1" / "data" / "generation_log.jsonl"
    saved = cf._REAL_PROFILES_DIR
    try:
        cf._REAL_PROFILES_DIR = mirror
        base = cf._real_state_snapshot()
        assert cf._real_state_snapshot() == base, "unstable at rest"

        # (a) the in-place DB rewrite — QA's arm 1, invisible pre-widening
        nested_db.write_bytes(b"SQLite format 3\x00 rewritten in place, longer")
        after_rewrite = cf._real_state_snapshot()
        assert after_rewrite != base, (
            "a nested profile database was rewritten in place and the tripwire "
            "did not see it")

        # (b) the ledger append — the 2026-07-14 generation_log incident shape,
        #     one directory deeper
        with open(nested_log, "a", encoding="utf-8") as fh:
            fh.write('{"fake": "spend"}\n')
        after_append = cf._real_state_snapshot()
        assert after_append != after_rewrite

        # (c) a file created inside an existing nested directory
        (mirror / "fresh2" / "data" / "briefing.md").write_text("x",
                                                               encoding="utf-8")
        assert cf._real_state_snapshot() != after_append
    finally:
        cf._REAL_PROFILES_DIR = saved


def test_tripwire_snapshot_reaches_every_profile_not_just_the_first(tmp_path):
    """BORN RED. Coverage is per-file over the whole tree: a rewrite in the
    LAST profile is seen exactly like one in the first — otherwise the widening
    would be a spot-check wearing a recursion's name."""
    cf = _conftest_module()
    mirror = _profiles_mirror(tmp_path)
    saved = cf._REAL_PROFILES_DIR
    try:
        cf._REAL_PROFILES_DIR = mirror
        base = cf._real_state_snapshot()
        (mirror / "fresh2" / "memory.md").write_text(
            "- a thread this reader never followed\n", encoding="utf-8")
        assert cf._real_state_snapshot() != base
    finally:
        cf._REAL_PROFILES_DIR = saved


def test_tripwire_snapshot_stats_and_never_reads(tmp_path):
    """BORN RED on the perf bound, not on the behaviour: the fixture is autouse
    and runs TWICE per test across the whole suite, so the widening must stay
    stat-only. Reading bytes (hashing, parsing) would be the expensive mistake,
    so this pin forbids `open` for the duration of one snapshot.

    Restored manually, never via monkeypatch: a broken `open` left in place
    past the test body would blow up the autouse tripwire's own after-snapshot.
    """
    cf = _conftest_module()
    mirror = _profiles_mirror(tmp_path)
    saved = cf._REAL_PROFILES_DIR
    real_open = builtins.open

    def forbidden(*a, **kw):
        raise AssertionError(
            "_real_state_snapshot opened a file — the tripwire runs twice per "
            "test and must stay stat-only")

    try:
        cf._REAL_PROFILES_DIR = mirror
        builtins.open = forbidden
        try:
            snap = cf._real_state_snapshot()
        finally:
            builtins.open = real_open
    finally:
        cf._REAL_PROFILES_DIR = saved
    # and it really did walk the tree it was pointed at
    nested = str(mirror / "fresh1" / "data" / "newslens.db")
    assert nested in snap, f"nested profile file not watched: {nested}"


def test_tripwire_still_watches_the_profiles_directory_itself(tmp_path):
    """CARRIED INVARIANT (born GREEN, per ENGINEERING.md:122) — the Stage-0 M1
    top-level watch is not replaced by the widening: a whole new profile
    appearing under `profiles/` must still be seen."""
    cf = _conftest_module()
    mirror = _profiles_mirror(tmp_path)
    saved = cf._REAL_PROFILES_DIR
    try:
        cf._REAL_PROFILES_DIR = mirror
        base = cf._real_state_snapshot()
        (mirror / "minted-by-a-test").mkdir()
        assert cf._real_state_snapshot() != base
    finally:
        cf._REAL_PROFILES_DIR = saved


def test_tripwire_snapshot_survives_a_missing_profiles_directory(tmp_path):
    """CARRIED INVARIANT (born GREEN): a checkout with no profiles/ yet must
    snapshot cleanly rather than raise out of the autouse fixture."""
    cf = _conftest_module()
    saved = cf._REAL_PROFILES_DIR
    try:
        cf._REAL_PROFILES_DIR = tmp_path / "there-is-no-profiles-dir"
        snap = cf._real_state_snapshot()
        assert cf._real_state_snapshot() == snap
    finally:
        cf._REAL_PROFILES_DIR = saved


# ===========================================================================
# ITEM 2 — guarded `newslens profile delete`
# ===========================================================================

def test_profile_delete_removes_the_world_and_says_what_went(tmp_path, capsys):
    """BORN RED. The verb exists, it takes the exact slug back as its
    confirmation, and the printout is an inventory of what was destroyed."""
    assert cli.main(["profile", "create", "tester"]) == 0
    assert cli.main(["profile", "create", "bystander"]) == 0
    root = tmp_path / "profiles" / "tester"
    assert (root / "data" / "newslens.db").exists()
    capsys.readouterr()

    assert cli.main(["profile", "delete", "tester", "--confirm", "tester"]) == 0
    out = capsys.readouterr().out
    assert not root.exists()
    assert "tester" in out
    assert str(root) in out
    assert "removed" in out.lower()
    # blast radius is one profile wide: the holder and every other world stand
    assert (tmp_path / "profiles").is_dir()
    assert (tmp_path / "profiles" / "bystander" / "data" / "newslens.db").exists()


def test_profile_delete_without_the_typed_slug_is_a_dry_run(tmp_path, capsys):
    """BORN RED. The CLI's own confirmation idiom (`discovery-clean`, cli.py
    :292-307): default prints the plan and changes nothing."""
    assert cli.main(["profile", "create", "tester"]) == 0
    root = tmp_path / "profiles" / "tester"
    capsys.readouterr()

    assert cli.main(["profile", "delete", "tester"]) == 0
    out = capsys.readouterr().out
    assert root.exists(), "a dry run deleted the profile"
    assert (root / "data" / "newslens.db").exists()
    assert "DRY RUN" in out
    assert "--confirm tester" in out


def test_profile_delete_refuses_a_confirmation_that_is_not_the_exact_slug(
        tmp_path, capsys):
    """BORN RED. 'yes' is not the slug; neither is a near-miss. Typing the
    world's own name back is the whole guard."""
    assert cli.main(["profile", "create", "tester"]) == 0
    root = tmp_path / "profiles" / "tester"
    capsys.readouterr()

    for wrong in ("yes", "y", "TESTER", "teste", "tester "):
        assert cli.main(["profile", "delete", "tester", "--confirm", wrong]) == 2
        assert root.exists(), f"confirmation {wrong!r} deleted the profile"
    assert "confirm" in capsys.readouterr().err.lower()


def test_profile_delete_refuses_the_founder_by_name(tmp_path, capsys):
    """BORN RED. HOLDS GUARD 1 (slug == default). QA's mutation M1 proved this
    pin dies when that guard alone is removed.

    `default` is the founder's own world and its root is the checkout
    itself."""
    (tmp_path / "data").mkdir(exist_ok=True)
    (tmp_path / "data" / "newslens.db").write_bytes(b"founder")
    assert cli.main(["profile", "delete", "default", "--confirm", "default"]) == 2
    err = capsys.readouterr().err
    assert "founder" in err.lower()
    assert (tmp_path / "data" / "newslens.db").exists()


def test_profile_delete_refuses_a_symlink_aimed_at_the_founder_data_root(
        tmp_path, capsys):
    """BORN RED. A COMPOSITION pin, not a per-guard pin — relabelled honestly
    after QA F-2 (2026-08-02) mutated guard 3 and found this test still green:
    guard 7 catches this same target, because the link points at a path the
    _GUARDED table names. It pins that the composition refuses the shape; guard
    3's own pin is `..._refuses_a_symlink_to_a_sibling_profile` below, which
    aims at a target no other guard covers.

    A name comparison passes this: the slug is `evil`, not `default`. Only
    resolving the path refuses it."""
    founder_data = tmp_path / "data"
    founder_data.mkdir(exist_ok=True)
    (founder_data / "newslens.db").write_bytes(b"founder database")
    holder = tmp_path / "profiles"
    holder.mkdir(exist_ok=True)
    os.symlink(founder_data, holder / "evil")

    assert cli.main(["profile", "delete", "evil", "--confirm", "evil"]) == 2
    assert (founder_data / "newslens.db").read_bytes() == b"founder database"
    assert "profile delete" in capsys.readouterr().err


def test_profile_delete_refuses_a_root_holding_a_real_guarded_path(
        tmp_path, capsys):
    """BORN RED. HOLDS GUARD 7 (the protected table). QA's mutations M4 and M5
    both proved this pin dies when that guard — or just its `paths._GUARDED`
    half — is removed.

    Whatever the anchor says, a directory that CONTAINS one of the checkout's
    own guarded locations (the real data/, memory.md, sources.yaml, .env, db)
    is never deletable. This is the arm guard 6's containment rule cannot
    cover: the target here IS a legitimate profile root, and the guarded path
    is inside it. Simulated by pointing one guarded entry inside the target,
    which is the only way to exercise the arm without touching real state."""
    assert cli.main(["profile", "create", "trap"]) == 0
    root = tmp_path / "profiles" / "trap"
    saved = dict(paths._GUARDED)
    try:
        paths._GUARDED["DB_PATH"] = root / "data" / "newslens.db"
        assert cli.main(["profile", "delete", "trap", "--confirm", "trap"]) == 2
    finally:
        paths._GUARDED.clear()
        paths._GUARDED.update(saved)
    assert root.exists(), "delete removed a directory holding a guarded path"
    assert "profile delete" in capsys.readouterr().err


def test_profile_delete_refuses_a_profile_that_was_never_created(
        tmp_path, capsys):
    """BORN RED. HOLDS GUARD 4 (`not root.is_dir()`).

    Same law as `--profile`: an unknown name is refused, never invented — and
    certainly never rmtree'd on the chance it exists. Asserting the SENTENCE
    matters for the mutation: with guard 4 gone the guards below all pass on a
    path that is not there, and the failure that eventually arrives is a bare
    FileNotFoundError out of rmtree, not a refusal."""
    assert cli.main(["profile", "delete", "ghost", "--confirm", "ghost"]) == 2
    err = capsys.readouterr().err
    assert "ghost" in err
    assert "nothing to delete" in err


def test_profile_delete_refuses_a_malformed_slug(tmp_path, capsys):
    """BORN RED. `../data` must be unrepresentable, not sanitized."""
    assert cli.main(["profile", "delete", "../data", "--confirm", "../data"]) == 2
    assert "invalid profile name" in capsys.readouterr().err
    # and asking the question created nothing: profiles/ is not minted by a
    # refusal (profiles.profiles_dir's read-only discipline)
    assert not (tmp_path / "profiles").exists()


def test_profiles_delete_library_refuses_without_the_confirmation(tmp_path):
    """BORN RED. The guard lives in the library, not only in the argument
    parser: a second caller (a script, the wrapper, a future portal) cannot
    reach the rmtree by skipping the CLI."""
    profiles.create("tester")
    root = paths.profile_root("tester")
    with pytest.raises(profiles.ProfileDeleteRefused):
        profiles.delete("tester")
    with pytest.raises(profiles.ProfileDeleteRefused):
        profiles.delete("tester", confirm="yes")
    assert root.is_dir()
    report = profiles.delete("tester", confirm="tester")
    assert not root.exists()
    assert report.files >= 3 and report.slug == "tester"


def test_create_now_points_at_the_guarded_verb_not_at_rm_rf(tmp_path, capsys):
    """BORN RED. `create`'s refusal over an existing profile used to end with
    "delete that directory by hand" — an `rm -rf` typed beside the founder's
    own state, repeatedly, at review cadence. That advice is the exact hazard
    this milestone removed, so the sentence has to name the guarded verb."""
    assert cli.main(["profile", "create", "tester"]) == 0
    capsys.readouterr()
    assert cli.main(["profile", "create", "tester"]) == 2
    err = capsys.readouterr().err
    assert "already exists" in err                       # carried
    assert "profile delete tester --confirm tester" in err
    assert "by hand" not in err


def test_profiles_deletion_plan_reads_the_world_without_touching_it(tmp_path):
    """BORN RED. The dry run's numbers are observed, and observing them is
    read-only: the plan can be taken twice and the world is identical after."""
    profiles.create("tester")
    root = paths.profile_root("tester")
    before = sorted(p.name for p in root.rglob("*"))
    plan = profiles.deletion_plan("tester")
    plan2 = profiles.deletion_plan("tester")
    assert plan.files == plan2.files and plan.bytes == plan2.bytes
    assert plan.files >= 3 and plan.bytes > 0
    assert plan.root == root
    assert sorted(p.name for p in root.rglob("*")) == before


# ===========================================================================
# ITEM 2, FIX LOOP 1 — one born-red pin per guard (QA F-2), and the containment
# guards that make the docstring's claim true (QA F-1).
#
# QA removed guards 2 and 4 of the landed draft, together, and the FULL SUITE
# delta was ZERO. A guard the suite cannot notice being removed is not an
# acceptance contract, and this is a delete verb. So every guard that survives
# the F-1 hardening carries a pin aimed at a target NO OTHER GUARD COVERS —
# which is the only kind of pin a mutation can kill.
#
# `_mirror_checkout` re-anchors paths.PROJECT_ROOT at a tmp tree shaped like
# the real checkout. Same idiom as `real_route` below and
# test_stage0_m1_profiles.py:675 — and it is what lets these tests aim the
# attacks at "the checkout" without the real one ever being named, let alone
# resolved to.
# ===========================================================================

def _mirror_checkout(tmp_path, monkeypatch):
    """A checkout-shaped tree, installed as paths.PROJECT_ROOT."""
    checkout = tmp_path / "checkout"
    (checkout / "profiles" / "victim" / "data").mkdir(parents=True)
    (checkout / "profiles" / "victim" / "data" / "newslens.db").write_bytes(
        b"SQLite format 3\x00 a reader's world")
    (checkout / "data" / "briefings" / "2026-08-02").mkdir(parents=True)
    (checkout / "data" / "briefings" / "2026-08-02" / "edition.md").write_text(
        "his edition", encoding="utf-8")
    (checkout / "data" / "battery").mkdir()
    (checkout / "data" / "battery" / "scores.json").write_text("{}", encoding="utf-8")
    (checkout / "src").mkdir()
    (checkout / "src" / "newslens.py").write_text("# source", encoding="utf-8")
    (checkout / "tests").mkdir()
    (checkout / "tests" / "test_x.py").write_text("# test", encoding="utf-8")
    monkeypatch.setattr(paths, "PROJECT_ROOT", checkout)
    return checkout


def _tree(root):
    return sorted(str(p.relative_to(root)) for p in root.rglob("*"))


def test_profile_delete_refuses_when_the_profiles_holder_is_itself_a_symlink(
        tmp_path, monkeypatch):
    """BORN RED. HOLDS GUARD 2 (`profiles/` ITSELF is a symlink).

    QA F-1 root cause #1, and 8 of their 9 holes: the landed draft lstat'd
    `profiles/<slug>` and never the directory above it, so with `profiles/` as
    the link, `<slug>` is a REAL directory in somebody else's world and the
    symlink guard never fires. This is the exact D8 shape, the one that
    resolved to a live reader's own root through a sandbox anchor.

    Aimed at a target NO OTHER GUARD COVERS: the resolved target here is a
    legitimate direct child of the mirror checkout's own profiles/, so guard 6
    is silent by design and guard 7 finds nothing. Remove guard 2 and this
    resolves."""
    checkout = _mirror_checkout(tmp_path, monkeypatch)
    w = tmp_path / "w"
    w.mkdir()
    os.symlink(checkout / "profiles", w / "profiles")

    with pytest.raises(profiles.ProfileDeleteRefused) as exc:
        profiles.deletion_plan("victim", anchor=w)
    assert "SYMLINK" in str(exc.value)
    assert (checkout / "profiles" / "victim" / "data" / "newslens.db").exists()


def test_profile_delete_refuses_a_symlink_to_a_sibling_profile(tmp_path, capsys):
    """HOLDS GUARD 3 (`profiles/<slug>` is a symlink).

    PROOF CLASS, honestly (ENGINEERING.md:122): born RED at HEAD d214359, where
    the verb does not exist — but born GREEN against this batch's own pre-fix
    bytes, because guard 3 itself is not new. What is new is that guard 3 now
    has a pin that dies when guard 3 ALONE is removed, which is the whole of
    QA F-2. Mutation-proven, not claimed.

    QA F-2: guard 3's existing pin (the link aimed at the founder's data root)
    does NOT die when guard 3 is removed, because guard 7 catches that target
    too. This one aims the link at a SIBLING PROFILE, one level below the same
    holder — so the resolved parent is still the holder (guard 5 silent), it is
    still outside the checkout (guard 6 silent), and it names no guarded path
    (guard 7 silent). Deleting `decoy` would destroy `neighbour`: the blast
    radius lands on the wrong reader, which is a name the operator never
    typed."""
    assert cli.main(["profile", "create", "neighbour"]) == 0
    holder = tmp_path / "profiles"
    os.symlink(holder / "neighbour", holder / "decoy")
    capsys.readouterr()

    assert cli.main(["profile", "delete", "decoy", "--confirm", "decoy"]) == 2
    assert "SYMLINK" in capsys.readouterr().err
    assert (holder / "neighbour" / "data" / "newslens.db").exists()


def test_profile_delete_refuses_a_target_inside_the_checkout_but_outside_profiles(
        tmp_path, monkeypatch):
    """BORN RED. HOLDS GUARD 6 (containment inside this checkout).

    QA F-1 root cause #2: guard 7 matches paths by NAME, so every subtree
    nobody named walked through it. Containment asks where the target actually
    resolved to instead.

    Aimed at a target NO OTHER GUARD COVERS: the holder here is a REAL
    directory literally named `profiles` (guard 2 silent), the target is a real
    directory directly under it (guards 3, 4, 5 silent), and nothing in the
    guarded table is at or inside it (guard 7 silent) — it is simply in the
    wrong place inside the checkout. Remove guard 6 and this resolves. This is
    the shape `NEWSLENS_DATA_DIR=<checkout>/src/data` produces, with no symlink
    involved at all."""
    checkout = _mirror_checkout(tmp_path, monkeypatch)
    stray = checkout / "src" / "profiles" / "victim"
    stray.mkdir(parents=True)
    (stray / "keep.txt").write_text("not a reader's world", encoding="utf-8")

    with pytest.raises(profiles.ProfileDeleteRefused) as exc:
        profiles.deletion_plan("victim", anchor=checkout / "src")
    assert "inside this checkout" in str(exc.value)
    assert (stray / "keep.txt").exists()
    # and the legitimate target under the SAME mirror checkout still resolves,
    # so this guard is a containment rule and not a blanket refusal
    assert profiles.deletion_plan("victim", anchor=checkout).real_root == \
        (checkout / "profiles" / "victim").resolve()


def test_the_shape_guard_is_present_even_though_it_cannot_bite():
    """BORN RED. HOLDS GUARD 5 (`real.parent != holder`) — STRUCTURALLY, and
    this pin is labelled that way on purpose.

    Guard 5 is provably SUBSUMED by guard 3: if the root is not a symlink and
    is a directory, then `root.resolve().parent == profiles_dir().resolve()` by
    construction, because `resolve()` expands the same parent components on
    both sides. So no input can make guard 5 fire while guard 3 is present, QA's
    behavioural mutation of it was correctly SILENT, and no behavioural pin is
    possible. Rather than delete a cheap invariant assertion from a delete verb,
    or leave it unpinned after QA showed what unpinned guards are worth, it gets
    a source pin: comment the guard out and this test fails by name. Same idiom
    as `..._goes_through_the_one_zero_dollar_implementation`."""
    import inspect
    src = inspect.getsource(profiles._resolve_deletable_root)
    # the LIVE statement, not the string appearing anywhere in the function:
    # a guard commented out is a guard removed, and this pin has to know the
    # difference (it did not, first try — the mutation ran silent).
    assert any(line.strip() == "if real.parent != holder:"
               for line in src.split("\n")), (
        "guard 5 (the shape assertion) is not a live statement in "
        "_resolve_deletable_root")
    assert "SUBSUMED by guard 3" in src, (
        "guard 5 must keep saying, in place, that it cannot bite — an "
        "unlabelled unpinnable guard is how QA F-2 happened")


def test_no_anchor_manipulation_can_aim_the_verb_outside_the_profiles_tree(
        tmp_path, monkeypatch, capsys):
    """BORN RED. THE CLAIM ITSELF, as a mechanism — QA's F-1 corpus in
    miniature, driven through the SHIPPED CLI and the SHIPPED env seam.

    `_resolve_deletable_root`'s docstring claims that no anchor argument and no
    NEWSLENS_DATA_DIR value can make this verb return anything inside the
    checkout except something strictly inside the checkout's own profiles/.
    The first
    draft made that claim and it was false nine ways (QA F-1). A load-bearing
    absolute in a delete verb has to be a test, so here it is: every shape QA
    found, aimed at a MIRROR checkout, must exit 2 and leave that mirror byte
    for byte identical.

    Every row goes through `cli.main`, because QA's point was that two of these
    need nothing but the shipped seam plus one symlink — cli.py's delete verb
    passes no anchor, so `anchor_dir()` honours NEWSLENS_DATA_DIR."""
    checkout = _mirror_checkout(tmp_path, monkeypatch)
    before = _tree(checkout)

    w_root = tmp_path / "w_root"; w_root.mkdir()
    os.symlink(checkout, w_root / "profiles")              # -> the checkout
    w_data = tmp_path / "w_data"; w_data.mkdir()
    os.symlink(checkout / "data", w_data / "profiles")     # -> its data/
    w_prof = tmp_path / "w_prof"; w_prof.mkdir()
    os.symlink(checkout / "profiles", w_prof / "profiles")  # -> its profiles/

    corpus = [
        (w_root, "data"),              # the guarded data root, by name
        (w_root, "profiles"),          # every reader world at once
        (w_root, "src"),
        (w_root, "tests"),
        (w_data, "briefings"),         # his editions — named by nobody
        (w_data, "battery"),
        (w_prof, "victim"),            # a live reader's own root (QA's D8)
    ]
    for anchor, slug in corpus:
        monkeypatch.setenv("NEWSLENS_DATA_DIR", str(anchor / "data"))
        capsys.readouterr()
        rc = cli.main(["profile", "delete", slug, "--confirm", slug])
        err = capsys.readouterr().err
        assert rc == 2, f"anchor {anchor.name} + slug {slug!r} was not refused"
        assert "profile delete" in err
        assert _tree(checkout) == before, (
            f"anchor {anchor.name} + slug {slug!r} changed the checkout")

    # and the dry run — the read-only half — is refused on the same shapes,
    # so the operator never even gets an inventory of what they cannot touch
    for anchor, slug in corpus:
        monkeypatch.setenv("NEWSLENS_DATA_DIR", str(anchor / "data"))
        capsys.readouterr()
        assert cli.main(["profile", "delete", slug]) == 2
    assert _tree(checkout) == before


# ---------------------------------------------------------------------------
# ITEM 2, FIX LOOP 1 — QA F-3: "(gone)" is an observation, not an assumption
# ---------------------------------------------------------------------------

def _resurrecting_rmtree(real_rmtree, marker=b"SQLite format 3\x00"):
    """`rmtree`, then the world comes back — what QA measured against a live
    serve. `server.serve` opens the profile DB per request instead of holding a
    handle, so the next request after the delete re-created
    `profiles/<slug>/data/newslens.db`. Simulated rather than staged with a
    real serve because a pin may not start a server."""
    def rmtree(path, *args, **kwargs):
        real_rmtree(path, *args, **kwargs)
        data = Path(path) / "data"
        data.mkdir(parents=True)
        (data / "newslens.db").write_bytes(marker)
    return rmtree


def test_profile_delete_says_the_world_came_back_instead_of_saying_gone(
        tmp_path, capsys, monkeypatch):
    """BORN RED (QA F-3, the cheap half). The landed draft printed "(gone)" and
    exited 0 without ever looking. QA deleted a profile out from under a live
    serve of that same profile: the CLI said removed / "(gone)" / "3 file(s)",
    exit 0 — and `profiles/<slug>/data/newslens.db` was back moments later,
    leaving a broken half-world that `profile list` reports as existing.

    So the verb re-stats and reports what it SAW. Not the same as refusing to
    delete a served profile — that needs a lockfile in every profile root and
    is tracked as the follow-up; this is the honesty half, which is the part
    that should not wait."""
    assert cli.main(["profile", "create", "tester"]) == 0
    root = tmp_path / "profiles" / "tester"
    monkeypatch.setattr(profiles.shutil, "rmtree",
                        _resurrecting_rmtree(profiles.shutil.rmtree))
    capsys.readouterr()

    rc = cli.main(["profile", "delete", "tester", "--confirm", "tester"])
    out, err = capsys.readouterr()
    assert rc == 1, "a delete that did not stick must not report success"
    assert "(gone)" not in out, "the verb claimed a directory that is on disk"
    assert "ON DISK AGAIN" in out
    assert str(root / "data" / "newslens.db") in out
    assert "serve" in err, "the message must name the known cause"
    assert (root / "data" / "newslens.db").exists()


def test_a_clean_delete_still_says_what_the_re_stat_cannot_promise(
        tmp_path, capsys):
    """BORN RED, and it exists because the fix above is NOT the whole finding.

    The live reproduction (fix loop 1, mirror checkout, real serve): the delete
    printed "(gone)" and the re-stat found NOTHING, because the resurrection is
    triggered by the next REQUEST to the serve — which arrived after the CLI
    had already exited. So on the clean path the verb states the limit rather
    than implying durability it did not check: an idle serve re-creates the
    world later and nothing at delete time can see it.

    An absolute a delete verb cannot keep is the exact failure this whole fix
    loop was returned for."""
    assert cli.main(["profile", "create", "tester"]) == 0
    capsys.readouterr()
    assert cli.main(["profile", "delete", "tester", "--confirm", "tester"]) == 0
    out = capsys.readouterr().out
    assert "(gone" in out
    assert "NEXT request re-creates" in out, (
        "the clean path implies a durability the re-stat cannot prove")
    assert "profile list" in out, "the operator gets no way to check"


def test_profiles_delete_reports_survivors_on_the_plan_not_only_in_the_cli(
        tmp_path, monkeypatch):
    """BORN RED (QA F-3). Same law this batch already applied to the
    confirmation: a library whose only honest caller is the CLI is a
    convention, not a mechanism. The observation rides on the returned plan, so
    the reader-serve door or a future portal sees it too — and it is EMPTY on a
    delete that stuck, which is the half that must not become noise."""
    profiles.create("tester")
    root = paths.profile_root("tester")

    clean = profiles.delete("tester", confirm="tester")
    assert clean.survivors == ()
    assert not root.exists()

    profiles.create("tester")
    monkeypatch.setattr(profiles.shutil, "rmtree",
                        _resurrecting_rmtree(profiles.shutil.rmtree))
    zombie = profiles.delete("tester", confirm="tester")
    assert zombie.survivors, "the world came back and the plan did not say so"
    assert str(root) in zombie.survivors[0]
    assert any("newslens.db" in s for s in zombie.survivors)


# ===========================================================================
# ITEM 1 — the reader-serve door
#
# Harness note: `real_route` is M2's routing harness (test_stage0_m3_personas
# .py:66) — no module-dict shadows, no redirection vars, PROJECT_ROOT and the
# _GUARDED table both re-anchored at tmp_path, so paths.__getattr__ answers the
# way it does on the principal's machine.
# ===========================================================================

@pytest.fixture
def real_route(monkeypatch, tmp_path):
    for name in GUARDED_NAMES:
        monkeypatch.delitem(vars(paths), name, raising=False)
    for var in ("NEWSLENS_DATA_DIR", "NEWSLENS_DB_PATH", "NEWSLENS_SOURCES_FILE",
                "NEWSLENS_ENV_FILE", "NEWSLENS_MEMORY_FILE"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr(paths, "PROJECT_ROOT", tmp_path)
    for key, value in paths.profile_layout(paths.DEFAULT_PROFILE, tmp_path).items():
        monkeypatch.setitem(paths._GUARDED, key, value)
    monkeypatch.setenv("NEWSLENS_REAL_DATA", "1")
    (tmp_path / "sources.yaml").write_text("interests:\n  broad: []\nsources: []\n",
                                           encoding="utf-8")
    yield tmp_path
    try:
        paths.set_profile(None)
    except paths.ProfileError:
        paths._PROFILE_OVERRIDE = None


@pytest.fixture
def keyed_route(real_route):
    """A `.env` of ours where the profile layout actually looks for one — the
    unscrubbed state a real login shell hands the door."""
    (real_route / ".env").write_text(
        f"PERPLEXITY_API_KEY={FAKE_KEY}\nBUDGET_CAP_USD_PER_RUN=1.50\n",
        encoding="utf-8")
    return real_route


@pytest.fixture
def intercept(monkeypatch):
    """Stand where `cli.main` stands at the handoff and record what the door
    resolved at that exact instant — the persona-door test idiom."""
    seen = {}

    def fake_main(argv):
        seen["argv"] = argv
        seen["key_in_process"] = os.environ.get(personas.SONAR_KEY_ENV, "<absent>")
        return 0

    from newslens import cli as cli_mod
    monkeypatch.setattr(cli_mod, "main", fake_main)
    return seen


def _free_port():
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def test_reader_serve_hands_the_profile_and_a_probed_port_to_the_shipped_cli(
        keyed_route, intercept):
    """BORN RED. WIRING PIN — the wrapper does not re-implement an entrypoint:
    it calls the shipped `cli.main` with `--profile` and `--port`, so the
    incident guard, the profile boundary and the redirection disclosure are all
    the shipped code (the persona door's precedent, personas.py:684-692)."""
    from newslens import readerserve
    profiles.create("qa-fresh1-sandbox")        # not via cli.main — `intercept` owns it
    assert readerserve.main(["qa-fresh1-sandbox"]) == 0
    argv = intercept["argv"]
    assert argv[:3] == ["--profile", "qa-fresh1-sandbox", "serve"]
    assert argv[3] == "--port"
    assert int(argv[4]) != personas.FOUNDER_PORT


def test_reader_serve_resolves_a_zero_length_key_at_the_moment_it_delegates(
        keyed_route, monkeypatch):
    """BORN RED — THE $0 PIN, measured the way the serve process itself would
    resolve it: through `config.load_env()` in a real child, at the instant of
    handoff, not by reading back a variable this process just set.

    The serve door is long-lived and its empty-state page carries a one-click
    "Generate today's edition" button. Without the scrub, load_env injects the
    principal's live key into THIS process and that button spends his money.
    """
    from newslens import readerserve
    assert cli.main(["profile", "create", "qa-fresh1-sandbox"]) == 0
    env_file = keyed_route / ".env"
    monkeypatch.delenv(personas.SONAR_KEY_ENV, raising=False)
    assert personas.resolved_key_length(dict(os.environ), env_file) == len(FAKE_KEY), (
        "fixture precondition: an unscrubbed process resolves the .env key")

    seen = {}

    def fake_main(argv):
        seen["resolved"] = personas.resolved_key_length(dict(os.environ), env_file)
        return 0

    from newslens import cli as cli_mod
    monkeypatch.setattr(cli_mod, "main", fake_main)
    assert readerserve.main(["qa-fresh1-sandbox"]) == 0
    assert seen["resolved"] == 0, (
        "reader-serve handed the pipeline a live Sonar key — the UI's Generate "
        "button would fire metered _sonar_verify calls")


def test_reader_serve_live_is_an_explicit_opt_in_that_keeps_the_real_key(
        keyed_route, monkeypatch, capsys):
    """BORN RED. `--live` is the ONLY way past the scrub, it changes no env
    surface (PERPLEXITY_API_KEY already exists), and it says out loud that this
    session can spend — with the key's LENGTH, never the key."""
    from newslens import readerserve
    assert cli.main(["profile", "create", "qa-fresh1-sandbox"]) == 0
    env_file = keyed_route / ".env"
    monkeypatch.delenv(personas.SONAR_KEY_ENV, raising=False)

    seen = {}

    def fake_main(argv):
        seen["resolved"] = personas.resolved_key_length(dict(os.environ), env_file)
        return 0

    from newslens import cli as cli_mod
    monkeypatch.setattr(cli_mod, "main", fake_main)
    capsys.readouterr()
    assert readerserve.main(["qa-fresh1-sandbox", "--live"]) == 0
    assert seen["resolved"] == len(FAKE_KEY)
    out = capsys.readouterr().out
    assert "LIVE" in out
    assert str(len(FAKE_KEY)) in out
    assert FAKE_KEY not in out, "the door printed the key itself"


def test_reader_serve_auto_port_skips_the_founder_port_and_bound_ports(
        keyed_route, intercept, monkeypatch):
    """BORN RED. 'auto port' means PROBED, not assumed: a port somebody is
    already holding is skipped by live check, and the founder's 8484 is never
    a candidate at all.

    The two decoy ports are DISCOVERED, never hardcoded — this machine really
    does run review serves inside the span (one was live on 8490 while this
    test was written), which is the whole reason the door probes."""
    from newslens import readerserve
    profiles.create("qa-fresh1-sandbox")        # not via cli.main — `intercept` owns it
    decoys, held = [], []
    for port in range(readerserve.PORT_SPAN[0], readerserve.PORT_SPAN[1] + 1):
        if len(decoys) == 2:
            break
        if not readerserve.port_is_free(port):
            continue
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind(("127.0.0.1", port))
            s.listen(1)
        except OSError:                       # raced; try the next one
            s.close()
            continue
        decoys.append(port)
        held.append(s)
    assert len(decoys) == 2, "could not stage two busy ports in the span"
    try:
        assert readerserve.main(["qa-fresh1-sandbox"]) == 0
    finally:
        for s in held:
            s.close()
    chosen = int(intercept["argv"][4])
    assert chosen not in decoys, "the door took a port somebody was holding"
    assert chosen != personas.FOUNDER_PORT
    assert readerserve.PORT_SPAN[0] <= chosen <= readerserve.PORT_SPAN[1]


def test_the_port_probe_sees_a_port_that_is_bound_but_never_listens(tmp_path):
    """BORN RED (QA F-4). `port_is_free` asks two questions and the suite only
    ever staged one: the decoys above `bind()` AND `listen(1)`, so `connect_ex`
    alone catches them and the bind arm — the only reason the second question
    exists — was never exercised. QA proved it by mutation: dropping either arm
    changed nothing.

    A socket that is BOUND and never listens is invisible to `connect_ex` and
    still fatal to the serve's own bind, which is the case the bind arm is for.

    THE SECOND ASSERTION IS THE POINT, and it is why this pin was worth
    writing: staging this arm for the first time found that an untimed
    `connect_ex` against such a port does not come back "refused" — it sits in
    SYN retransmit for ~26 REAL SECONDS. `pick_port` walks a 110-port span, so
    one of these turned the auto-port door into a half-minute hang. The bound
    is 3 s against a measured 0.25 s timeout and a sub-millisecond loopback
    connect: a hundredfold margin, so this is a hang detector, not a benchmark."""
    from newslens import readerserve
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.bind((readerserve.LOOPBACK, 0))          # bound, NEVER listen()
        port = sock.getsockname()[1]
        probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        probe.settimeout(readerserve.PROBE_TIMEOUT)
        try:
            answered = probe.connect_ex((readerserve.LOOPBACK, port))
        finally:
            probe.close()
        assert answered != 0, (
            "premise failed: a bound-not-listening port answered connect_ex, "
            "so this test is no longer staging the bind arm")

        started = time.perf_counter()
        free = readerserve.port_is_free(port)
        elapsed = time.perf_counter() - started
        assert free is False, (
            "the door would hand this port to the server, whose bind then dies")
        assert elapsed < 3.0, (
            f"port_is_free took {elapsed:.1f}s on one bound-not-listening "
            "port; the liveness probe has lost its timeout and pick_port "
            "walks 110 of these")
        # and the two questions stay two questions: taken, but nobody serving
        assert readerserve.port_has_listener(port) is False
    finally:
        sock.close()


def test_the_door_says_which_kind_of_busy_a_busy_port_is(keyed_route, capsys):
    """BORN RED (QA F-4 / F-2). This is the CONNECT arm's pin, and it exists
    because the arm had no observable consequence at all: `port_is_free` is a
    single boolean and the bind arm already produces it (measured — a plain
    bind against a live listener is refused EADDRINUSE even with SO_REUSEADDR
    set on the listener), so QA's mutation that dropped `connect_ex` could not
    fail anything.

    It also fixes a sentence that was simply untrue: every busy port used to be
    reported as "something is serving there", which sends the operator hunting
    for a serve that does not exist when the port is merely bound, in TIME_WAIT,
    or reserved. The sibling pin above covers the live-listener wording."""
    from newslens import readerserve
    assert cli.main(["profile", "create", "qa-fresh1-sandbox"]) == 0
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.bind((readerserve.LOOPBACK, 0))          # bound, NEVER listen()
        port = sock.getsockname()[1]
        capsys.readouterr()
        assert readerserve.main(["qa-fresh1-sandbox", "--port", str(port)]) == 2
        err = capsys.readouterr().err
    finally:
        sock.close()
    assert str(port) in err
    assert "nothing is serving on it" in err
    assert "something is serving" not in err, (
        "the door blamed a serve that does not exist")


def test_reader_serve_refuses_the_founder_port_when_asked_for_it(
        keyed_route, capsys):
    """BORN RED. `--port 8484` is the founder's own instance; the door refuses
    with a sentence rather than colliding with his live serve."""
    from newslens import readerserve
    assert cli.main(["profile", "create", "qa-fresh1-sandbox"]) == 0
    capsys.readouterr()
    assert readerserve.main(["qa-fresh1-sandbox", "--port", str(personas.FOUNDER_PORT)]) == 2
    assert str(personas.FOUNDER_PORT) in capsys.readouterr().err


def test_reader_serve_refuses_a_port_that_is_already_serving(
        keyed_route, capsys):
    """BORN RED. An explicit busy port gets a sentence, not a traceback out of
    ThreadingHTTPServer."""
    from newslens import readerserve
    assert cli.main(["profile", "create", "qa-fresh1-sandbox"]) == 0
    port = _free_port()
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind(("127.0.0.1", port))
    s.listen(1)
    capsys.readouterr()
    try:
        assert readerserve.main(["qa-fresh1-sandbox", "--port", str(port)]) == 2
    finally:
        s.close()
    err = capsys.readouterr().err
    assert str(port) in err
    # fix loop 1: and it says WHICH kind of busy. Something really is serving
    # here, so this is the arm that may say so.
    assert "something is serving" in err


def test_reader_serve_create_mints_and_serves_in_one_command(
        keyed_route, intercept):
    """BORN RED. `--create` = provision + first serve, zero prompts, one
    command — and provisioning goes through the SHIPPED `profile create`, not a
    second copy of it."""
    from newslens import readerserve
    assert not paths.profile_root("fresh9").exists()
    assert readerserve.main(["--create", "fresh9"]) == 0
    assert (paths.profile_root("fresh9") / "data" / "newslens.db").exists()
    assert (paths.profile_root("fresh9") / "memory.md").read_bytes() == b""
    assert intercept["argv"][:3] == ["--profile", "fresh9", "serve"]


def test_reader_serve_create_refuses_over_an_existing_world(
        keyed_route, capsys):
    """BORN RED. `--create` inherits `profile create`'s refusal: re-provisioning
    in place would put a fresh empty DB beside a populated memory.md."""
    from newslens import readerserve
    assert cli.main(["profile", "create", "qa-fresh1-sandbox"]) == 0
    capsys.readouterr()
    assert readerserve.main(["--create", "qa-fresh1-sandbox"]) != 0
    assert "already exists" in capsys.readouterr().err


def test_reader_serve_refuses_the_founders_own_world(keyed_route, capsys):
    """BORN RED. The founder's world is reached the way it always was
    (`newslens serve`, 8484). A second server on his SQLite — one that migrates
    on startup — is not a review sandbox."""
    from newslens import readerserve
    capsys.readouterr()
    assert readerserve.main([paths.DEFAULT_PROFILE]) == 2
    err = capsys.readouterr().err
    assert "founder" in err.lower() and "newslens serve" in err


def test_reader_serve_refuses_a_profile_that_was_never_created(
        keyed_route, capsys):
    """BORN RED. A typo cannot mint a world here either — the create path is
    the explicit flag, never a fallback."""
    from newslens import readerserve
    capsys.readouterr()
    assert readerserve.main(["nosuchreader"]) == 2
    err = capsys.readouterr().err
    assert "nosuchreader" in err and "--create" in err


def test_reader_serve_prints_the_profiles_own_ledger_at_exit(
        keyed_route, monkeypatch, capsys):
    """BORN RED. What this session charged and shadowed, read from THIS
    profile's own generation_log — so a review walk's spend is disclosed per
    walk instead of assumed, and the founder's ledger stays out of it."""
    from newslens import readerserve
    assert cli.main(["profile", "create", "qa-fresh1-sandbox"]) == 0
    ledger = paths.profile_layout("qa-fresh1-sandbox")["DATA_DIR"] / "generation_log.jsonl"
    ledger.write_text(json.dumps({
        "ts": "2026-08-01T09:00:00Z", "status": "ok", "total_usd": 0.5,
        "steps": [{"step": "writer", "usd": 0.5, "usd_shadow": 0.5}]}) + "\n",
        encoding="utf-8")

    def fake_main(argv):
        with ledger.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({
                "ts": "2026-08-02T09:00:00Z", "status": "ok",
                "total_usd": 0.0,
                "steps": [{"step": "writer", "usd": 0.0, "usd_shadow": 1.25}]}) + "\n")
        return 0

    from newslens import cli as cli_mod
    monkeypatch.setattr(cli_mod, "main", fake_main)
    capsys.readouterr()
    assert readerserve.main(["qa-fresh1-sandbox"]) == 0
    out = capsys.readouterr().out
    assert "ledger" in out.lower()
    assert "1.25" in out, "the session's SHADOW spend was not disclosed"
    assert "$0.00" in out, "the session's CHARGED spend was not disclosed"
    assert str(ledger) in out


def test_reader_serve_refuses_when_the_scrub_did_not_hold(
        keyed_route, monkeypatch, capsys):
    """BORN RED. ENFORCEMENT twin of the persona doors' refusal: the $0 claim
    is MEASURED after load_env, in a child, and a door that cannot prove it
    runs nothing."""
    from newslens import readerserve
    assert cli.main(["profile", "create", "qa-fresh1-sandbox"]) == 0
    monkeypatch.setattr(
        personas, "pipeline_probe",
        lambda env, env_file, python=None: {
            "key_len": 7, "discovery_enabled": False,
            "cap": 1.50, "cap_error": None})
    called = {"n": 0}
    from newslens import cli as cli_mod
    monkeypatch.setattr(cli_mod, "main",
                        lambda argv: called.__setitem__("n", called["n"] + 1))
    capsys.readouterr()
    assert readerserve.main(["qa-fresh1-sandbox"]) == 1
    assert called["n"] == 0, "the door served after failing to prove the scrub"
    assert "REFUSING" in capsys.readouterr().err


def test_reader_serve_goes_through_the_one_zero_dollar_implementation():
    """BORN RED. One implementation of the $0 contract, not a second copy: the
    door calls `personas.enforce_zero_dollar`, the same function both persona
    doors call (pinned for them at test_stage0_m3_personas.py:928)."""
    import inspect
    from newslens import readerserve
    body = inspect.getsource(readerserve.main)
    assert "enforce_zero_dollar(" in body, (
        "reader-serve grew its own copy of the $0 contract")


def test_the_reader_serve_launcher_is_thin_and_executable():
    """BORN RED. scripts/reader-serve follows the persona-serve precedent: a
    sys.path bootstrap and a handoff, so a fresh clone can run it and ALL the
    logic stays in src/ where the suite can reach it."""
    script = paths.PROJECT_ROOT / "scripts" / "reader-serve"
    assert script.exists(), "scripts/reader-serve is missing"
    assert os.stat(script).st_mode & stat.S_IXUSR, "not executable"
    body = script.read_text(encoding="utf-8")
    assert "readerserve" in body
    assert "main()" in body
    assert len(body.splitlines()) <= 30, "the launcher grew logic"
