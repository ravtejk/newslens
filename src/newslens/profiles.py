"""Profile provisioning and inventory (Stage-0 M1).

A profile is one reader's whole world: its own SQLite database, its own
corpus/artifacts/spend log under data/, its own memory.md, its own
sources.yaml. `paths.profile_layout` says WHERE those live; this module
creates them and reports on them.

Two laws this module exists to keep:

  1. **The founder never moves.** He is the `default` profile and his paths
     are the checkout's own — data/, memory.md, sources.yaml, in place. Every
     function here refuses to write anything under the default profile; a new
     profile is always a new directory beside his state, never a migration of
     it.

  2. **A new profile inherits nothing.** No threads (first-run seeding was
     killed — see memory.py), no interest tags (the shipped template's
     interests block is empty on purpose), no notes, no ledger, no corpus.
     The 0-byte memory.md is defence in depth: even if some future bootstrap
     path came back, a file that EXISTS forecloses the empty-file arm.

Stdlib-only at import time (db.py's rule): PyYAML is imported lazily, inside
the one status field that needs it, so a pre-install doctor can still list
profiles.
"""

from __future__ import annotations

import os
import shutil
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from . import db, paths


class ProfileExistsError(Exception):
    """`profile create` on a name that already has a directory."""


class ProfileMissingError(Exception):
    """A profile was named that has never been created."""


class ProfileDeleteRefused(Exception):
    """`profile delete` was pointed at something it must never remove — the
    founder's own world, a link out of the profile tree, a directory holding
    real state, or a call that never typed the slug back."""


@dataclass
class ProfileStatus:
    """Honest inventory for one profile — every field is observed, never
    assumed, and anything unreadable is reported as such rather than skipped."""
    slug: str
    is_default: bool
    root: Path
    db_path: Path
    db_exists: bool = False
    pending_migrations: Optional[int] = None   # None = could not be determined
    memory_exists: bool = False
    memory_bytes: Optional[int] = None
    sync_generation: Optional[int] = None
    sync_profile_slug: Optional[str] = None
    threads_active: Optional[int] = None
    sources_exists: bool = False
    has_interests: Optional[bool] = None       # None = unparseable/absent
    problems: List[str] = field(default_factory=list)

    @property
    def commissioned(self) -> bool:
        """Ready to rank: schema current AND this reader has chosen tags."""
        return bool(self.db_exists and self.pending_migrations == 0
                    and self.has_interests)

    def line(self) -> str:
        """One honest status line for `newslens profile list`."""
        bits = [f"{self.slug}" + ("  (founder / default)" if self.is_default else "")]
        if not self.db_exists:
            bits.append("db: MISSING — run `newslens migrate`")
        elif self.pending_migrations is None:
            bits.append("db: present, schema UNREADABLE")
        elif self.pending_migrations:
            bits.append(f"db: {self.pending_migrations} migration(s) PENDING")
        else:
            bits.append("db: up to date")
        if self.threads_active is not None:
            bits.append(f"threads: {self.threads_active} active")
        if self.memory_exists:
            size = "0 bytes (unwritten)" if self.memory_bytes == 0 \
                else f"{self.memory_bytes} bytes"
            gen = "" if self.sync_generation is None \
                else f", sync gen {self.sync_generation}"
            bits.append(f"memory.md: {size}{gen}")
        else:
            bits.append("memory.md: absent")
        if not self.sources_exists:
            bits.append("sources.yaml: MISSING")
        elif self.has_interests is None:
            bits.append("sources.yaml: unreadable")
        elif self.has_interests:
            bits.append("interests: set")
        else:
            bits.append("interests: NONE — not commissioned yet (rank refuses)")
        return " · ".join(bits)


# ---------------------------------------------------------------------------
# Inventory
# ---------------------------------------------------------------------------

def profile_names(anchor: Optional[Path] = None) -> List[str]:
    """Every profile that exists, default first. Read-only: never creates
    profiles/ as a side effect of being asked what is in it."""
    names = [paths.DEFAULT_PROFILE]
    root = paths.profiles_dir(anchor)
    if root.is_dir():
        for child in sorted(root.iterdir()):
            if (child.is_dir() and child.name != paths.DEFAULT_PROFILE
                    and _is_slug(child.name)):
                names.append(child.name)
    return names


def stray_directories(anchor: Optional[Path] = None) -> List[str]:
    """Directory names under profiles/ that no profile in `profile_names()`
    owns — invalid slugs, PLUS the one valid slug that is never a profile root.

    Reported rather than hidden: something put them there, and a listing that
    quietly omits state is the dishonest kind.

    Stage-0 M2 (M1 gate rider): `profiles/default` fell through BOTH listings.
    It is a valid slug, so the invalid-name filter skipped it; and
    profile_names() excludes DEFAULT_PROFILE by construction, because the
    founder's profile root is the CHECKOUT, not a directory under profiles/.
    So a directory holding a whole reader's state could sit there permanently
    invisible — and worse, invisibly ignored: nothing reads it, and its owner
    would have no way to find out why."""
    root = paths.profiles_dir(anchor)
    if not root.is_dir():
        return []
    return sorted(c.name for c in root.iterdir()
                  if c.is_dir() and (not _is_slug(c.name)
                                     or c.name == paths.DEFAULT_PROFILE))


def redirection_warnings(profile: Optional[str] = None) -> List[str]:
    """Disclosure lines for a real hazard the precedence rules create.

    Redirection outranks the profile (see paths.__getattr__): that ordering is
    load-bearing — it is what keeps the QA sandbox hermetic across process
    boundaries — but it means an exported NEWSLENS_MEMORY_FILE (say) silently
    hands profile `tester1` the file that variable names, mixing two readers'
    worlds one path at a time. Observed live during M1's own CLI probe.

    We do not change the precedence; we refuse to let it be silent. NL-81's
    stamp identity would still REFUSE a cross-paired memory.md, so this is a
    disclosure, not a second guard."""
    import os
    slug = paths.normalize_profile(profile) if profile else paths.current_profile()
    if slug == paths.DEFAULT_PROFILE:
        return []                      # redirection IS the founder's sandbox
    hits = [(name, var) for name, var in paths._ENV_OVERRIDE.items()
            if os.environ.get(var)]
    if not hits:
        return []
    return [
        f"profile {slug!r} is active, but {len(hits)} path redirection(s) "
        "OUTRANK it — these locations are NOT this profile's:",
    ] + [f"    {var}={os.environ[var]}  (overrides {name})" for name, var in hits]


def db_path_for(profile: str, anchor: Optional[Path] = None) -> Path:
    """The database to use for `profile`, named EXPLICITLY.

    The DEFAULT profile resolves through paths.DB_PATH so every per-process
    redirection (the sandbox seams, NEWSLENS_DB_PATH) is honoured exactly as
    the rest of the code sees it — `migrate --all-profiles` must migrate the
    same founder database `migrate` does.

    Every other profile resolves through the layout. That is deliberate and
    it is not a shortcut: the guarded-name route can be shadowed per process
    (the suite's conftest shadows the module dict to sandbox itself), and a
    verb that migrated "whatever DB_PATH currently means" would then quietly
    operate on the wrong reader's world. Naming the database is the honest
    form for a verb that takes a profile argument."""
    slug = paths.normalize_profile(profile)
    if slug == paths.DEFAULT_PROFILE and anchor is None:
        return paths.DB_PATH
    return paths.profile_layout(slug, anchor)["DB_PATH"]


def exists(profile: str, anchor: Optional[Path] = None) -> bool:
    slug = paths.normalize_profile(profile)
    if slug == paths.DEFAULT_PROFILE:
        return True                      # the founder's world always exists
    return paths.profile_root(slug, anchor).is_dir()


def require_exists(profile: str, anchor: Optional[Path] = None) -> str:
    """Resolve a profile name or refuse LOUDLY.

    This is the reason a typo cannot silently mint a new world: db.connect()
    creates parent directories, so `--profile alcie` would otherwise provision
    an empty profile on first write and quietly bury a reader's state in it."""
    slug = paths.normalize_profile(profile)
    if not exists(slug, anchor):
        known = ", ".join(profile_names(anchor))
        raise ProfileMissingError(
            f"no profile named {slug!r} — create it with "
            f"`newslens profile create {slug}` (existing: {known})")
    return slug


def resolve_entrypoint_profile(name: Optional[str] = None):
    """The profile boundary EVERY real entrypoint owes, as one implementation.

    Returns `(slug, None)` when the active profile is usable, or
    `(None, message)` when it must be refused — the caller prints the message
    to stderr and exits 2. Both refusal classes land here: a malformed name
    (paths.ProfileError, which current_profile raises rather than degrading to
    the founder's world) and a name nobody created (ProfileMissingError).

    `set_profile(name)` is called even when `name` is None, deliberately: that
    CLEARS a pin an earlier in-process main() left behind, so a second
    entrypoint call in one process re-resolves from the environment instead of
    silently inheriting the previous caller's reader. That leak is a real one
    — it was found in the CLI during M1's own probe and again in the doctor
    (QA fix loop 1, F2).

    Stage-0 M2 (M1 gate rider): battery / moat_battery / follow_altitude
    self-sanction with allow_real_paths() exactly like cli.main and
    doctor.main, but had no profile boundary at all — so an exported typo'd
    NEWSLENS_PROFILE plus a deliberate paid `--run` would write their
    artifacts under a world nobody created, after which require_exists sees a
    directory and every other verb accepts the typo too (F1's cascade,
    through a side door). Dry-run defaults are why that was zero-writes; a
    default is not a guard.

    NOTE: cli.main (cli.py:293-304) and doctor.main (doctor.py:1120-1132)
    still carry their own hand-rolled copies of this logic. They are M1-pinned
    and working, so this milestone did not rewrite them — but three copies
    became five without this function, and the duplicated-validator class is
    exactly how BUG-1 shipped in two places at once. Folding those two onto
    this helper is a clean follow-up one-liner each, flagged not taken."""
    try:
        slug = paths.set_profile(name)
        if slug != paths.DEFAULT_PROFILE:
            require_exists(slug)
    except (paths.ProfileError, ProfileMissingError) as exc:
        return None, f"profile: {exc}"
    return slug, None


def status(profile: str, anchor: Optional[Path] = None) -> ProfileStatus:
    """Observe one profile. Read-only by construction: nothing here creates a
    file, a directory or a database (db.pending_migrations and
    db.connect_readonly are the read-only halves of db.py).

    Reports the ON-DISK layout under the anchor, not this process's
    per-path redirections. `newslens profile list` is a question about the
    world, not about the current shell's env vars — and any redirection that
    IS in force is disclosed separately by redirection_warnings()."""
    slug = paths.normalize_profile(profile)
    layout = paths.profile_layout(slug, anchor)
    st = ProfileStatus(slug=slug, is_default=(slug == paths.DEFAULT_PROFILE),
                       root=paths.profile_root(slug, anchor),
                       db_path=layout["DB_PATH"])

    st.db_exists = layout["DB_PATH"].exists()
    if st.db_exists:
        try:
            st.pending_migrations = len(db.pending_migrations(layout["DB_PATH"]))
        except Exception as exc:                       # unreadable/corrupt
            st.problems.append(f"schema unreadable: {type(exc).__name__}: {exc}")
        try:
            con = db.connect_readonly(layout["DB_PATH"])
            try:
                row = con.execute(
                    "SELECT sync_generation, profile_slug FROM sync_state"
                    " WHERE id = 1").fetchone()
                if row is not None:
                    st.sync_generation = row["sync_generation"]
                    st.sync_profile_slug = row["profile_slug"]
                st.threads_active = con.execute(
                    "SELECT COUNT(*) c FROM memory WHERE status = 'active'"
                ).fetchone()["c"]
            finally:
                con.close()
        except sqlite3.Error as exc:
            # pre-0022 or pre-migration DBs land here; that is information,
            # not breakage.
            st.problems.append(f"db read: {exc}")

    mem = layout["MEMORY_FILE"]
    st.memory_exists = mem.exists()
    if st.memory_exists:
        try:
            st.memory_bytes = mem.stat().st_size
        except OSError as exc:
            st.problems.append(f"memory.md unreadable: {exc}")

    src = layout["SOURCES_FILE"]
    st.sources_exists = src.exists()
    if st.sources_exists:
        try:
            from . import config           # lazy: pulls PyYAML
            cfg = config.load_sources(src)
            st.has_interests = cfg.has_interests
            for problem in cfg.problems:
                st.problems.append(f"sources.yaml: {problem}")
        except Exception as exc:
            st.problems.append(
                f"sources.yaml not parsed ({type(exc).__name__}: {exc})")

    if (st.sync_profile_slug or paths.DEFAULT_PROFILE) != slug \
            and st.sync_generation is not None:
        # Not fatal — it is exactly the mispairing NL-81's stamp identity
        # refuses at sync time — but a list that stayed quiet about it would
        # be the dishonest kind of status.
        st.problems.append(
            f"database says it belongs to profile "
            f"{st.sync_profile_slug or '(unset/default)'!r}, directory says "
            f"{slug!r}")
    return st


def inventory(anchor: Optional[Path] = None) -> List[ProfileStatus]:
    return [status(name, anchor) for name in profile_names(anchor)]


def _is_slug(name: str) -> bool:
    try:
        paths.normalize_profile(name)
        return True
    except paths.ProfileError:
        return False


# ---------------------------------------------------------------------------
# Provisioning
# ---------------------------------------------------------------------------

def create(profile: str, anchor: Optional[Path] = None) -> ProfileStatus:
    """Provision a brand-new profile. Fresh fully-migrated DB, 0-byte
    memory.md, its own dirs, its own copy of the committed source catalog.

    Never seeds. Never reads, copies or moves any other profile's state — in
    particular never the founder's sources.yaml, whose working copy carries
    his own uncommitted tuning (the catalog comes from the committed template
    at paths.PROFILE_SOURCES_TEMPLATE).

    Refuses over an existing profile: re-provisioning in place would put a
    fresh empty DB beside a populated memory.md, which is precisely the
    file-and-DB disagreement NL-81's guard exists to refuse."""
    slug = paths.normalize_profile(profile)
    if slug == paths.DEFAULT_PROFILE:
        raise ProfileExistsError(
            f"{paths.DEFAULT_PROFILE!r} is the founder's own profile — it "
            "already exists and is never provisioned. Pick another name.")
    root = paths.profile_root(slug, anchor)
    if root.exists():
        # NL-132-B: this used to say "delete that directory by hand", which was
        # an `rm -rf` typed beside the founder's own state at review cadence.
        # There is a guarded verb now, and the exit advice has to be it.
        raise ProfileExistsError(
            f"profile {slug!r} already exists at {root} — start it over with "
            f"`newslens profile delete {slug} --confirm {slug}` (dry-runs "
            "first), then create it again")
    if not paths.PROFILE_SOURCES_TEMPLATE.exists():
        raise FileNotFoundError(
            f"source template missing: {paths.PROFILE_SOURCES_TEMPLATE} — run "
            "from the prototype checkout")

    layout = paths.profile_layout(slug, anchor)
    layout["DATA_DIR"].mkdir(parents=True, exist_ok=False)

    # 1. Fresh database, every migration applied (0001..latest).
    db.migrate(db_path=layout["DB_PATH"])

    # 2. Stamp the profile onto its own sync_state row, so NL-81's pairing
    #    identity can NAME this profile when a foreign memory.md shows up.
    con = db.connect(layout["DB_PATH"])
    try:
        with con:
            con.execute("UPDATE sync_state SET profile_slug = ? WHERE id = 1",
                        (slug,))
    finally:
        con.close()

    # 3. A 0-byte memory.md — M0's proven lawful true-zero start: parse_file("")
    #    is [], the gen-0 bootstrap adopts it, nothing is seeded.
    layout["MEMORY_FILE"].write_text("", encoding="utf-8")

    # 4. The committed source catalog, interests empty. copyfile, not copy2:
    #    we want a new file owned by this profile, not a clone of the
    #    template's metadata.
    shutil.copyfile(paths.PROFILE_SOURCES_TEMPLATE, layout["SOURCES_FILE"])

    return status(slug, anchor)


# ---------------------------------------------------------------------------
# Teardown (NL-132-B)
#
# Until this milestone, `create` refused over an existing profile and the only
# teardown was the sentence it printed: "delete that directory by hand". At
# review cadence that is an `rm -rf` typed repeatedly, one mistyped path away
# from the founder's own world, which is the hazard the scoping named (eng-2,
# 2026-08-01, Rook). So the removal gets guards, and the guards are structural:
# a NAME comparison against 'default' is defeated by a symlink called anything
# else, so every check below runs on RESOLVED paths.
#
# HARDENED 2026-08-02 (QA F-1, fix loop 1). The first draft's guards were a
# TABLE of named paths, and a table only protects what somebody remembered to
# name: 9 of QA's 28 attacks resolved to a deletable path — data/briefings,
# data/battery, src/, tests/, profiles/ itself — two of them through nothing
# but the shipped NEWSLENS_DATA_DIR seam plus one symlink, i.e. reachable from
# the shipped CLI. Both root causes are closed below: guard 2 (the link one
# level UP, at profiles/ itself) and guard 6 (containment, which replaces
# "is it on the list" with "where does it actually resolve to").
# ---------------------------------------------------------------------------

@dataclass
class DeletionPlan:
    """What `profile delete` is about to destroy, or just did — observed, not
    assumed. `root` is the path as the layout names it; `real_root` is that
    path with every symlink resolved, and it is the one that gets removed."""
    slug: str
    root: Path
    real_root: Path
    files: int
    dirs: int
    bytes: int
    status: ProfileStatus
    # What was ON DISK again the instant after rmtree returned. Empty on a
    # plan (nothing was removed) and on a clean delete. See `delete`.
    survivors: Tuple[str, ...] = ()


def _within(inner: Path, outer: Path) -> bool:
    """Is `inner` strictly below `outer`? Both must already be resolved."""
    return outer in inner.parents


def _at_or_within(inner: Path, outer: Path) -> bool:
    """Is `inner` the same path as `outer`, or below it? Both resolved."""
    return inner == outer or outer in inner.parents


def _real_profiles_root() -> Path:
    """THIS checkout's own `profiles/` — the only directory inside this
    checkout whose children this verb is ever allowed to remove.

    Read at CALL time, not import time, for the same reason `_protected_paths`
    reads `paths._GUARDED` as a plain dict: the suite re-anchors
    `paths.PROJECT_ROOT` at a tmp mirror (`test_stage0_m1_profiles.py:675`,
    `test_stage0_m3_personas.py:82`, this batch's `real_route` fixture), and a
    guard frozen at import would still be pointed at the developer's real
    checkout from inside those tests — untestable in the one direction that
    matters.
    """
    return (paths.PROJECT_ROOT / paths.PROFILES_DIRNAME).resolve()


def _protected_paths(anchor: Optional[Path] = None) -> List[Tuple[str, Path]]:
    """Everything a delete must never remove — or remove FROM — with a label
    for the refusal sentence.

    TWO independent tables on purpose. `profile_layout(DEFAULT_PROFILE)` is the
    founder's world under whatever anchor is in force (so a sandboxed process
    protects its own sandbox's founder), and `paths._GUARDED` is the CHECKOUT's
    real table read as a plain dict — no PEP 562, no sanction, the conftest
    tripwire's own idiom.

    This table is NOT a containment proof, and the first draft of this
    docstring claimed it was (QA F-1, 2026-08-02: 28 attacks, 9 resolved to a
    deletable path). It matches a path EXACTLY, or catches a named path sitting
    inside the target — so every subtree nobody thought to name walked straight
    through it: `data/briefings`, `data/battery`, `data/follow_altitude`,
    `src/`, `tests/`, and `profiles/` itself. Naming more paths is not the fix,
    because the next unnamed subtree is always one `mkdir` away; the fix is
    guard 6, which asks where the resolved target IS rather than what it is
    called. This table survives as the arm guard 6 cannot cover: a guarded path
    sitting INSIDE an otherwise perfectly legitimate profile root.
    """
    base = anchor if anchor is not None else paths.anchor_dir()
    out: List[Tuple[str, Path]] = [
        ("the profiles directory itself", paths.profiles_dir(anchor)),
        ("this world's anchor", Path(base)),
        ("the checkout root", paths.PROJECT_ROOT),
    ]
    for key, value in paths.profile_layout(paths.DEFAULT_PROFILE, anchor).items():
        out.append((f"the founder's {key}", Path(value)))
    for key, value in paths._GUARDED.items():
        out.append((f"the checkout's real {key}", Path(value)))
    return out


def _resolve_deletable_root(slug: str, anchor: Optional[Path] = None) -> Path:
    """The one gate. Returns the resolved directory that may be removed, or
    raises — and every raise is a refusal somebody could otherwise have talked
    their way past with a plausible-looking slug.

    SEVEN guards, in order, each with its own born-red pin in
    tests/test_nl132b_profile_hardening.py (QA F-2, 2026-08-02: a guard the
    suite cannot notice being removed is not an acceptance contract). Guard 5
    is the one exception — it is provably unpinnable and says so in place.

    THE CLAIM THIS COMPOSITION EARNS, stated as narrowly as it is true: no
    `anchor=` argument and no NEWSLENS_DATA_DIR value can make this verb return
    anything inside THIS checkout except something strictly inside THIS
    checkout's own `profiles/`. The real `data/` — including every dated subtree under it —
    the checkout root, `src/`, `tests/` and `profiles/` itself are all
    unreachable, whether or not anybody remembered to name them.

    WHAT IT DOES NOT CLAIM: anything about a world OUTSIDE this checkout.
    There the anchor is the operator's own declaration of which world they
    mean, which is the whole point of the seam — a sandboxed process must be
    able to tear down its own sandbox. Guard 2 is what keeps that from being
    aimed back at the checkout by a link.
    """
    # --- guard 1: the founder, by name. His root IS the checkout. ----------
    if slug == paths.DEFAULT_PROFILE:
        raise ProfileDeleteRefused(
            f"{paths.DEFAULT_PROFILE!r} is the founder's own profile and its "
            "root IS this checkout — data/, memory.md and sources.yaml are "
            "not a profile directory and are never deleted by this verb.")
    # --- guard 2: `profiles/` ITSELF is a symlink. -------------------------
    # QA F-1 root cause #1: guard 3 lstats profiles/<slug> and never the
    # directory above it, so with `profiles/` as the link, <slug> is a REAL
    # directory in somebody else's world and guard 3 never fires. That is the
    # v7-M1 pinhole shape one level up, and it is how 8 of QA's 9 holes were
    # reached — including `profiles/` -> the real profiles/, aimed at a live
    # reader. A profiles/ that is a link is refused for DELETE only; reading,
    # creating and serving through it are untouched.
    holder_link = paths.profiles_dir(anchor)
    if holder_link.is_symlink():
        raise ProfileDeleteRefused(
            f"{holder_link} is a SYMLINK, not a profiles directory. Refusing "
            "to delete through it — the link decides which world's profiles/ "
            "this is, and a delete verb must not take that from a link. "
            "Run the delete against the real directory, or remove the link.")
    # --- guard 3: profiles/<slug> is a symlink. ----------------------------
    root = paths.profile_root(slug, anchor)
    if root.is_symlink():
        raise ProfileDeleteRefused(
            f"{root} is a SYMLINK, not a profile directory. Refusing to delete "
            "through a link — a link can point anywhere, including at your own "
            "data/. Remove the link by hand if that is what you meant.")
    # --- guard 4: it is not a directory at all. ----------------------------
    if not root.is_dir():
        raise ProfileMissingError(
            f"no profile named {slug!r} at {root} — nothing to delete "
            f"(existing: {', '.join(profile_names(anchor))})")
    real = root.resolve()
    holder = holder_link.resolve()
    # --- guard 5: shape. A profile root is one level below its holder. -----
    # HONEST LABEL (QA F-2): this guard is provably SUBSUMED by guard 3 for
    # every input reachable today — if root is not a symlink and is a
    # directory, then root.resolve().parent == profiles_dir().resolve() by
    # construction, because resolve() expands the same parent components in
    # both. QA's mutation of it was therefore silent, and no behavioural pin
    # can make it bite. It stays as a cheap assertion of the invariant the
    # guards below rely on, and it carries a STRUCTURAL pin instead
    # (test_the_shape_guard_is_present_even_though_it_cannot_bite) so its
    # removal is still visible to the suite. Enumerated, not hidden.
    if real.parent != holder:
        raise ProfileDeleteRefused(
            f"{root} resolves to {real}, which is not a directory directly "
            f"under {holder}. A profile root is always exactly one level below "
            "profiles/; anything else is a link or an escape, and this verb "
            "removes neither.")
    # --- guard 6: CONTAINMENT inside this checkout. ------------------------
    # QA F-1 root cause #2: the table below matches names, so any subtree
    # nobody named passed it (data/briefings, data/battery, src/, tests/).
    # This asks the containment question instead, and it is the guard that
    # makes the claim in the docstring true: if the resolved target is at or
    # inside this checkout, it must be strictly inside this checkout's own
    # profiles/. Outside the checkout it says nothing — that is the sandbox
    # seam, and guard 2 is what stops a link pointing the sandbox back here.
    project = paths.PROJECT_ROOT.resolve()
    if _at_or_within(real, project) and not _within(real, _real_profiles_root()):
        raise ProfileDeleteRefused(
            f"refusing to delete {real}: it is inside this checkout "
            f"({project}) but not inside {_real_profiles_root()}. The only "
            "thing this verb may remove inside the checkout is one reader's "
            "own directory under profiles/ — not data/, not the source tree, "
            "and not profiles/ itself, whatever anchor was in force.")
    # --- guard 7: the protected table (IS or CONTAINS a named path). -------
    for label, protected in _protected_paths(anchor):
        candidate = Path(protected).resolve()
        if candidate == real or _within(candidate, real):
            raise ProfileDeleteRefused(
                f"refusing to delete {real}: it IS or CONTAINS {label} "
                f"({candidate}). Real state outside the named profile is never "
                "in the blast radius.")
    return real


def _measure(root: Path) -> Tuple[int, int, int]:
    """(files, directories, bytes) under `root`. Read-only; lstat, so a link
    is counted as itself rather than as whatever it points at."""
    files = dirs = total = 0
    for dirpath, dirnames, filenames in os.walk(root):
        dirs += len(dirnames)
        for name in filenames:
            files += 1
            try:
                total += os.lstat(os.path.join(dirpath, name)).st_size
            except OSError:
                pass                       # raced or unreadable: counted, unsized
    return files, dirs, total


def deletion_plan(profile: str, anchor: Optional[Path] = None) -> DeletionPlan:
    """What deleting `profile` would destroy. Runs every guard and touches
    nothing — this is what the CLI's dry run prints, and it is also the
    measurement `delete` reports afterwards."""
    slug = paths.normalize_profile(profile)
    real = _resolve_deletable_root(slug, anchor)
    files, dirs, total = _measure(real)
    return DeletionPlan(slug=slug, root=paths.profile_root(slug, anchor),
                        real_root=real, files=files, dirs=dirs, bytes=total,
                        status=status(slug, anchor))


def delete(profile: str, anchor: Optional[Path] = None,
           confirm: Optional[str] = None) -> DeletionPlan:
    """Remove one profile's whole world, permanently. Returns what went.

    `confirm` must be the profile's own slug, typed back exactly. The check
    lives HERE and not only in the argument parser: a script, the reader-serve
    door or a future dev portal must not be able to reach the rmtree by
    skipping the CLI — a guard that only one caller honours is a convention,
    not a guard.

    Everything else is `_resolve_deletable_root`'s job, on resolved paths.

    OBSERVED, NOT ASSUMED (QA F-3, 2026-08-02): the returned plan carries
    `survivors` — whatever was on disk again the instant `rmtree` returned.
    QA sandboxed a delete against a LIVE serve of the same profile and the
    world partially came back: `server.serve` opens the database per request
    rather than holding a handle, so the very next HTTP request re-created
    `profiles/<slug>/data/newslens.db` moments after the CLI had printed
    "(gone)". A verb that reports an outcome it did not check is the thing
    this milestone exists to stop, so it checks.

    This is the CHEAP half of that finding and it is honest about being so:
    a resurrection that lands AFTER this stat is not observable from here.
    Refusing to delete a profile that is being served needs a lockfile or
    pidfile in every profile root — new machinery, a new file in every world,
    a design question — and is tracked as the follow-up, not smuggled in here.
    """
    slug = paths.normalize_profile(profile)
    if confirm != slug:
        raise ProfileDeleteRefused(
            f"refusing: this deletes profile {slug!r} permanently — its "
            "database, corpus, artifacts, spend ledger, memory.md and "
            f"sources.yaml. Type the slug back to confirm (confirm={slug!r}, "
            f"got {confirm!r}). Nothing was changed.")
    plan = deletion_plan(slug, anchor)
    shutil.rmtree(plan.real_root)
    plan.survivors = _survivors(plan.real_root)
    return plan


def _survivors(root: Path, limit: int = 8) -> Tuple[str, ...]:
    """What is on disk under `root` right now — read-only, lstat only, capped.

    Called immediately after `rmtree`. An empty tuple means the removal stuck
    at the moment we looked; anything else is a world that came back (a live
    serve is the known cause) and the caller must not say "(gone)"."""
    if not os.path.lexists(root):
        return ()
    found = [str(root)]
    for dirpath, dirnames, filenames in os.walk(root):
        for name in sorted(dirnames) + sorted(filenames):
            found.append(os.path.join(dirpath, name))
            if len(found) >= limit:
                return tuple(found)
    return tuple(found)


def migrate_all(anchor: Optional[Path] = None) -> Dict[str, List[str]]:
    """Apply pending migrations to EVERY profile's database, default first.

    Returns {slug: [migrations applied]}. A profile whose database does not
    exist yet is created by its own migrate — that is the same behaviour
    `newslens migrate` has always had for the founder, applied per profile."""
    ran: Dict[str, List[str]] = {}
    for name in profile_names(anchor):
        ran[name] = db.migrate(db_path=db_path_for(name, anchor))
    return ran
