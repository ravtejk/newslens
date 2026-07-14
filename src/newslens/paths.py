"""Canonical filesystem locations for the NewsLens prototype.

Everything is anchored on the repo checkout that contains this file
(src/newslens/paths.py -> prototype/). This assumes the package is used from
the checkout — either editable-installed (`pip install -e .`, the documented
setup) or run via scripts/doctor's sys.path bootstrap. A non-editable install
into site-packages is unsupported for this prototype (migrations/ and
sources.yaml live in the checkout, not in the wheel); the doctor script
verifies the anchor and says so rather than failing cryptically.

Stdlib-only by design (see module docstring in newslens/__init__.py).
"""

import os
from pathlib import Path

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
    two real entrypoints (cli.main, doctor.main)."""
    global _REAL_PATHS_ALLOWED
    _REAL_PATHS_ALLOWED = True


_GUARDED = {
    "DATA_DIR": PROJECT_ROOT / "data",           # gitignored; created on demand
    "DB_PATH": PROJECT_ROOT / "data" / "newslens.db",
}


def __getattr__(name: str):
    if name in _GUARDED:
        if (_REAL_PATHS_ALLOWED
                or "PYTEST_CURRENT_TEST" in os.environ
                or os.environ.get("NEWSLENS_REAL_DATA") == "1"):
            return _GUARDED[name]
        # RuntimeError, not AttributeError — hasattr/getattr(default=) must
        # not swallow the refusal.
        raise RuntimeError(
            f"newslens.paths.{name} refused: unsanctioned process (incident "
            "guard, 2026-07-14 — an ad-hoc render-proof script clobbered the "
            "real generation_log). Run via `newslens ...`/scripts/doctor, "
            "under pytest, or set NEWSLENS_REAL_DATA=1. Ad-hoc probes must "
            "sandbox. LIMIT: hardcoded 'data/...' strings bypass this guard.")
    raise AttributeError(f"module 'newslens.paths' has no attribute {name!r}")


MIGRATIONS_DIR = PROJECT_ROOT / "migrations"
PROMPTS_DIR = PROJECT_ROOT / "prompts"
SOURCES_FILE = PROJECT_ROOT / "sources.yaml"
ENV_FILE = PROJECT_ROOT / ".env"                 # principal-edited; never committed
ENV_EXAMPLE_FILE = PROJECT_ROOT / ".env.example"
MEMORY_FILE = PROJECT_ROOT / "memory.md"         # hand-editable memory surface
                                                  # (gitignored; personal state)


def looks_like_checkout() -> bool:
    """True if PROJECT_ROOT actually is the prototype checkout."""
    return (PROJECT_ROOT / "pyproject.toml").exists() and MIGRATIONS_DIR.is_dir()
