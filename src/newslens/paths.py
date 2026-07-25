"""Canonical filesystem locations for the NewsLens prototype.

Everything is anchored on the repo checkout that contains this file
(src/newslens/paths.py -> prototype/). This assumes the package is used from
the checkout — either editable-installed (`pip install -e .`, the documented
setup) or run via scripts/doctor's sys.path bootstrap. A non-editable install
into site-packages is unsupported for this prototype (migrations/ and
sources.yaml live in the checkout, not in the wheel); the doctor script
verifies the anchor and says so rather than failing cryptically.

Stage-0 M1 adds the PROFILE dimension (see the PROFILES block below): the
founder is the `default` profile and his paths are unchanged in place; every
other profile owns its own data/, DB, memory.md and sources.yaml under
`profiles/<slug>/`, behind the SAME real-paths guard. Profiles are resolved on
the sanctioned arm of __getattr__ — never by redirecting NEWSLENS_DATA_DIR.

Stdlib-only by design (see module docstring in newslens/__init__.py).
"""

import os
import re
from pathlib import Path
from typing import Dict, Optional

PROJECT_ROOT = Path(__file__).resolve().parents[2]

# DATA_DIR / DB_PATH are NOT module globals — they resolve through the PEP 562
# __getattr__ guard below (M3 gate FIX-5, incident 2026-07-14: an ad-hoc
# render-proof script imported newslens outside pytest and clobbered the real
# generation_log.jsonl through paths.DATA_DIR; second recurrence of the class
# the 2026-07-07 "no real-state writes during probing" rule governs — a
# procedure that fails twice needs a mechanism).

_REAL_PATHS_ALLOWED = False


def allow_real_paths() -> None:
    """Sanction real DATA_DIR/DB_PATH for this process. Called ONLY by the
    real principal-run entrypoints (cli.main, doctor.main, battery.main,
    moat_battery.main, follow_altitude.main)."""
    global _REAL_PATHS_ALLOWED
    _REAL_PATHS_ALLOWED = True


_GUARDED = {
    "DATA_DIR": PROJECT_ROOT / "data",           # gitignored; created on demand
    "DB_PATH": PROJECT_ROOT / "data" / "newslens.db",
    # Principal-owned files behind the same seam (2026-07-16 incident: a
    # sandboxed serve probe rewrote the REAL memory.md — the seam covered
    # only DATA_DIR/DB_PATH; second instance of the v7-M1 pinhole class).
    "SOURCES_FILE": PROJECT_ROOT / "sources.yaml",
    "ENV_FILE": PROJECT_ROOT / ".env",           # principal-edited; never committed
    "MEMORY_FILE": PROJECT_ROOT / "memory.md",   # hand-editable; personal state
}

_ENV_OVERRIDE = {"DATA_DIR": "NEWSLENS_DATA_DIR", "DB_PATH": "NEWSLENS_DB_PATH",
                 "SOURCES_FILE": "NEWSLENS_SOURCES_FILE",
                 "ENV_FILE": "NEWSLENS_ENV_FILE",
                 "MEMORY_FILE": "NEWSLENS_MEMORY_FILE"}

# ---------------------------------------------------------------------------
# PROFILES (Stage-0 M1) — a dimension INSIDE the guarded lane.
#
# Rook's seam-inversion law (multi-user brief 2026-07-16 §1 "The correction"):
# a profile is NOT a NEWSLENS_DATA_DIR redirection. The seam's law is
# "redirection = not real state" — implementing profiles on it would put a
# tester's memory behind the door labelled write-freely, inverting the very
# incident guard this module exists to be. So the profile is resolved on the
# SANCTIONED arm of __getattr__ below: every profile's DB, corpus, artifacts,
# spend log and memory.md are REAL STATE, refused to an unsanctioned process
# exactly like the founder's, and reachable only via allow_real_paths() or a
# conscious NEWSLENS_REAL_DATA=1.
#
# ZERO-MOVE ADOPTION: the founder IS the default profile. profile_layout(
# "default") returns _GUARDED unchanged — data/, memory.md, sources.yaml stay
# exactly where they are, byte for byte. Nothing about his world moves for
# profile #2 to exist.
#
# .env is DELIBERATELY NOT profile-scoped: keys are machine credentials, not
# reader state, and one machine has one set. Every profile reads the same .env
# (per-profile spend is separated by the per-profile generation_log, which
# lives under DATA_DIR).
# ---------------------------------------------------------------------------

DEFAULT_PROFILE = "default"
PROFILES_DIRNAME = "profiles"
PROFILE_ENV = "NEWSLENS_PROFILE"

# Deliberately narrow: lowercase slug, no dots, no separators. The slug becomes
# a directory name under the checkout, so "..", "/", "~" and friends must be
# unrepresentable rather than sanitized away.
_PROFILE_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")

_PROFILE_OVERRIDE: Optional[str] = None


class ProfileError(ValueError):
    """A profile name that cannot be honoured. Loud by design: silently
    falling back to the default profile would route a tester's writes into the
    founder's world — the worst available failure."""


def normalize_profile(name: str) -> str:
    """Validate a profile slug and return its canonical form.

    Raises ProfileError on anything that is not a plain lowercase slug. The
    ONLY repair is trimming surrounding whitespace (`NEWSLENS_PROFILE=$(cat
    file)` picks up a trailing newline); nothing inside the name is ever
    fixed up, because quietly repairing a name is how `--profile ../data`
    becomes a footgun."""
    if not isinstance(name, str):
        raise ProfileError(f"profile name must be a string, got {type(name).__name__}")
    slug = name.strip()
    if not _PROFILE_RE.match(slug):
        raise ProfileError(
            f"invalid profile name {name!r} — use lowercase letters, digits, "
            "'-' and '_' only (start with a letter or digit, max 32 chars). "
            f"The founder's own world is the {DEFAULT_PROFILE!r} profile.")
    return slug


def set_profile(name: Optional[str]) -> str:
    """Pin the active profile for this process (the `--profile` flag).
    `None` CLEARS the pin, falling back to NEWSLENS_PROFILE or the default.
    Returns the resolved slug.

    Deliberately does NOT export NEWSLENS_PROFILE. An earlier draft did, so
    that children would inherit the profile — and M1's own CLI probe caught
    the cost: a second in-process `cli.main()` with no --profile then inherited
    the first call's profile and listed a tester's threads as the founder's.
    Nothing this package spawns resolves newslens paths anyway (git, the
    kokoro TTS runner with a scrubbed env, the `claude` binary), so the export
    bought nothing and leaked a reader across a boundary. A user who wants a
    child to inherit a profile exports the variable themselves — which is
    exactly what the variable is for."""
    global _PROFILE_OVERRIDE
    if name is None:
        _PROFILE_OVERRIDE = None
        return current_profile()
    _PROFILE_OVERRIDE = normalize_profile(name)
    return _PROFILE_OVERRIDE


def current_profile() -> str:
    """The active profile slug: an explicit --profile wins, then
    NEWSLENS_PROFILE, then the founder's default. A malformed env value raises
    rather than degrading to the default."""
    if _PROFILE_OVERRIDE is not None:
        return _PROFILE_OVERRIDE
    env = os.environ.get(PROFILE_ENV, "").strip()
    if env:
        return normalize_profile(env)
    return DEFAULT_PROFILE


def anchor_dir() -> Path:
    """The directory that CONTAINS data/ — the checkout root normally, or the
    sandbox root when NEWSLENS_DATA_DIR redirects.

    This is the one place the profile tree honours the redirection seam: a
    sandboxed process must be able to provision and inspect profiles without
    ever touching the real checkout. It does NOT make profiles a redirection —
    the profile still resolves on the sanctioned arm; the anchor only says
    which world's `profiles/` we are talking about."""
    override = os.environ.get(_ENV_OVERRIDE["DATA_DIR"])
    if override:
        return Path(override).parent
    return PROJECT_ROOT


def profiles_dir(anchor: Optional[Path] = None) -> Path:
    """Where non-default profiles live. Never created as a side effect of
    asking (the db.py read-only discipline)."""
    return (anchor if anchor is not None else anchor_dir()) / PROFILES_DIRNAME


def profile_root(profile: str, anchor: Optional[Path] = None) -> Path:
    """The directory owning one profile's state. The default profile's root is
    the checkout itself — that IS the zero-move adoption."""
    slug = normalize_profile(profile)
    base = anchor if anchor is not None else anchor_dir()
    if slug == DEFAULT_PROFILE:
        return base
    return base / PROFILES_DIRNAME / slug


def profile_layout(profile: str, anchor: Optional[Path] = None) -> Dict[str, Path]:
    """PURE: where one profile's five guarded locations live. Creates nothing,
    sanctions nothing, stats nothing.

    For the default profile under the real anchor this returns _GUARDED
    unchanged — asserted by test_stage0_m1_profiles.py so the zero-move
    promise is a mechanism, not a comment."""
    slug = normalize_profile(profile)
    base = anchor if anchor is not None else anchor_dir()
    if slug == DEFAULT_PROFILE:
        data = base / "data"
        return {"DATA_DIR": data,
                "DB_PATH": data / "newslens.db",
                "SOURCES_FILE": base / "sources.yaml",
                "ENV_FILE": base / ".env",
                "MEMORY_FILE": base / "memory.md"}
    root = base / PROFILES_DIRNAME / slug
    data = root / "data"
    return {"DATA_DIR": data,
            "DB_PATH": data / "newslens.db",
            "SOURCES_FILE": root / "sources.yaml",
            # .env is machine-level, never per-reader: same file as default.
            "ENV_FILE": base / ".env",
            "MEMORY_FILE": root / "memory.md"}


def __getattr__(name: str):
    if name in _GUARDED:
        # Redirection outranks sanction (v7-M1 pinhole fix, 2026-07-14): an
        # explicitly overridden location is not real state, and env vars are
        # the only sandbox that survives a process boundary — the QA suite's
        # children (doctor, CLI) resolve these instead of the live data/.
        # DB_PATH derives from a DATA_DIR-only override so one variable
        # sandboxes both.
        override = os.environ.get(_ENV_OVERRIDE[name])
        if override:
            return Path(override)
        if name == "DB_PATH":
            data_override = os.environ.get(_ENV_OVERRIDE["DATA_DIR"])
            if data_override:
                return Path(data_override) / "newslens.db"
        # No pytest arm: children spawned by tests inherit PYTEST_CURRENT_TEST,
        # so sanctioning on it blessed every such child against real data/
        # (the v7-M1 pinhole). Under pytest the conftest sandbox provides the
        # module-dict shadow and the env overrides above; nothing legitimate
        # resolves real paths from inside the suite.
        if (_REAL_PATHS_ALLOWED
                or os.environ.get("NEWSLENS_REAL_DATA") == "1"):
            # Stage-0 M1: the profile dimension resolves HERE, on the
            # sanctioned arm — every profile's state is real state behind the
            # same door.
            #
            # ZERO-MOVE: the default profile returns _GUARDED[name] — the
            # literal pre-M1 expression, so the founder's resolution is not
            # merely equivalent, it is the same line of code it always was.
            # profile_layout(DEFAULT_PROFILE, PROJECT_ROOT) == _GUARDED is
            # pinned by test so the two can never drift apart.
            profile = current_profile()
            if profile == DEFAULT_PROFILE:
                return _GUARDED[name]
            return profile_layout(profile)[name]
        # RuntimeError, not AttributeError — hasattr/getattr(default=) must
        # not swallow the refusal.
        raise RuntimeError(
            f"newslens.paths.{name} refused: unsanctioned process (incident "
            "guard, 2026-07-14 — an ad-hoc render-proof script clobbered the "
            "real generation_log). Run via `newslens ...`/scripts/doctor, or "
            "set NEWSLENS_REAL_DATA=1 for a conscious one-off. Sandboxed and "
            "child processes set NEWSLENS_DATA_DIR (and optionally "
            "NEWSLENS_DB_PATH / NEWSLENS_SOURCES_FILE / NEWSLENS_ENV_FILE / "
            "NEWSLENS_MEMORY_FILE) to redirect instead. Ad-hoc probes must "
            "sandbox. LIMIT: hardcoded 'data/...' strings bypass this guard.")
    raise AttributeError(f"module 'newslens.paths' has no attribute {name!r}")


MIGRATIONS_DIR = PROJECT_ROOT / "migrations"
PROMPTS_DIR = PROJECT_ROOT / "prompts"
ENV_EXAMPLE_FILE = PROJECT_ROOT / ".env.example"
TEMPLATES_DIR = PROJECT_ROOT / "templates"
# The committed source catalog a new profile is born with (Stage-0 M1). NOT
# guarded and NOT profile-scoped: it is shipped code, read-only, same class as
# migrations/ and prompts/. Deliberately not the principal's sources.yaml —
# provisioning must never read his working file.
PROFILE_SOURCES_TEMPLATE = TEMPLATES_DIR / "profile-sources.yaml"
# SOURCES_FILE / ENV_FILE / MEMORY_FILE are NOT module globals — they resolve
# through the PEP 562 guard above (see _GUARDED).


def looks_like_checkout() -> bool:
    """True if PROJECT_ROOT actually is the prototype checkout."""
    return (PROJECT_ROOT / "pyproject.toml").exists() and MIGRATIONS_DIR.is_dir()
