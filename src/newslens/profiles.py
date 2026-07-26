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

import shutil
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

from . import db, paths


class ProfileExistsError(Exception):
    """`profile create` on a name that already has a directory."""


class ProfileMissingError(Exception):
    """A profile was named that has never been created."""


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
        raise ProfileExistsError(
            f"profile {slug!r} already exists at {root} — delete that "
            "directory by hand if you really mean to start it over")
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


def migrate_all(anchor: Optional[Path] = None) -> Dict[str, List[str]]:
    """Apply pending migrations to EVERY profile's database, default first.

    Returns {slug: [migrations applied]}. A profile whose database does not
    exist yet is created by its own migrate — that is the same behaviour
    `newslens migrate` has always had for the founder, applied per profile."""
    ran: Dict[str, List[str]] = {}
    for name in profile_names(anchor):
        ran[name] = db.migrate(db_path=db_path_for(name, anchor))
    return ran
